"""Indicadores técnicos con pandas y numpy (sin librerías externas de análisis técnico).

Todos son causales: el valor de una vela solo usa esa vela y las anteriores, nunca las
posteriores. Eso es lo que permite probar una estrategia en histórico sin hacer trampa.

Nota: RSI, ATR y las medias exponenciales arrancan con el primer dato disponible. Puede haber
pequeñas diferencias con MetaTrader 5 en las primeras velas; desaparecen tras unas decenas de
velas. Para validar, compara los últimos valores con los del indicador de MT5.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period, min_periods=period).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def moving_average(series: pd.Series, period: int, kind: str = "ema") -> pd.Series:
    if kind == "ema":
        return ema(series, period)
    if kind == "sma":
        return sma(series, period)
    raise ValueError(f"Tipo de media no soportado: {kind!r} (usa 'ema' o 'sma')")


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """RSI de Wilder (suavizado con alpha = 1/periodo)."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = avg_gain / avg_loss
        out = 100.0 - 100.0 / (1.0 + rs)
    # Sin pérdidas: 100. Sin ganancias ni pérdidas (precio plano): 50.
    out = out.mask((avg_loss == 0) & (avg_gain > 0), 100.0)
    out = out.mask((avg_loss == 0) & (avg_gain == 0), 50.0)
    return out


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    ranges = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    )
    return ranges.max(axis=1, skipna=True)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """ATR de Wilder. Se expresa en unidades de precio (divídelo por el pip para tener pips)."""
    tr = true_range(high, low, close)
    return tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def macd(
    close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Devuelve (línea MACD, línea de señal, histograma)."""
    line = ema(close, fast) - ema(close, slow)
    signal_line = line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return line, signal_line, line - signal_line


def bollinger(
    close: pd.Series, period: int = 20, deviation: float = 2.0
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Devuelve (banda superior, media, banda inferior). Desviación poblacional, como MT5."""
    mid = sma(close, period)
    std = close.rolling(period, min_periods=period).std(ddof=0)
    return mid + deviation * std, mid, mid - deviation * std


def stochastic(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    k_period: int = 14,
    smooth_k: int = 3,
    d_period: int = 3,
) -> tuple[pd.Series, pd.Series]:
    """Estocástico lento. Devuelve (%K suavizado, %D)."""
    lowest = low.rolling(k_period, min_periods=k_period).min()
    highest = high.rolling(k_period, min_periods=k_period).max()
    span = (highest - lowest).replace(0.0, np.nan)
    raw = 100.0 * (close - lowest) / span
    k = raw.rolling(smooth_k, min_periods=smooth_k).mean()
    d = k.rolling(d_period, min_periods=d_period).mean()
    return k, d


def fib_swing(
    high: pd.Series, low: pd.Series, lookback: int
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Último impulso dentro de las `lookback` velas que terminan en cada vela.

    Devuelve (máximo, mínimo, dirección). Dirección +1 si el mínimo ocurrió antes que el máximo
    (impulso alcista), -1 si el máximo ocurrió antes (impulso bajista) y 0 si coinciden o no hay
    datos suficientes. Sobre ese impulso se miden los retrocesos de Fibonacci.
    """
    n = len(high)
    highs = high.to_numpy(dtype=float)
    lows = low.to_numpy(dtype=float)
    swing_high = np.full(n, np.nan)
    swing_low = np.full(n, np.nan)
    direction = np.zeros(n, dtype=int)
    if n >= lookback:
        window_high = sliding_window_view(highs, lookback)
        window_low = sliding_window_view(lows, lookback)
        idx_high = window_high.argmax(axis=1)
        idx_low = window_low.argmin(axis=1)
        end = np.arange(lookback - 1, n)
        swing_high[end] = window_high.max(axis=1)
        swing_low[end] = window_low.min(axis=1)
        direction[end] = np.where(idx_low < idx_high, 1, np.where(idx_high < idx_low, -1, 0))
    index = high.index
    return (
        pd.Series(swing_high, index=index),
        pd.Series(swing_low, index=index),
        pd.Series(direction, index=index),
    )
