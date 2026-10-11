"""Datos sintéticos y utilidades compartidas por las pruebas."""

import numpy as np
import pandas as pd


def make_candles(closes, wick=0.0002, start="2026-01-05", freq="h", opens=None):
    """Velas con apertura = cierre anterior y mechas simétricas de tamaño `wick`."""
    closes = np.asarray(closes, dtype=float)
    if opens is None:
        opens = np.r_[closes[0], closes[:-1]]
    opens = np.asarray(opens, dtype=float)
    return pd.DataFrame(
        {
            "time": pd.date_range(start, periods=len(closes), freq=freq, tz="UTC"),
            "open": opens,
            "high": np.maximum(opens, closes) + wick,
            "low": np.minimum(opens, closes) - wick,
            "close": closes,
            "tick_volume": 100,
        }
    )


def random_candles(n=1500, seed=7, start_price=1.1):
    """Camino aleatorio con tendencias suaves y mechas realistas (determinista por semilla)."""
    rng = np.random.RandomState(seed)
    drift = 0.00018 * np.sin(np.arange(n) / 70)
    close = start_price + np.cumsum(drift + rng.normal(0, 0.0004, n))
    opens = np.r_[close[0], close[:-1]]
    high = np.maximum(opens, close) + np.abs(rng.normal(0, 0.0002, n))
    low = np.minimum(opens, close) - np.abs(rng.normal(0, 0.0002, n))
    return pd.DataFrame(
        {
            "time": pd.date_range("2025-01-01", periods=n, freq="h", tz="UTC"),
            "open": opens,
            "high": high,
            "low": low,
            "close": close,
            "tick_volume": 100,
        }
    )


def downtrend_then_jump(n_down=60, step=0.0001, jump=0.0050, start=1.1000):
    """Bajada lenta y un salto alcista en la última vela: provoca un cruce alcista de medias."""
    closes = [start - step * i for i in range(n_down)]
    closes.append(closes[-1] + jump)
    return closes
