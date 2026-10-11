"""Backtest: prueba una estrategia sobre velas históricas.

Reglas del simulador (conservadoras, para no inflar resultados):
- La señal se evalúa al cierre de una vela y la operación entra en la apertura de la siguiente.
- Las velas de MT5 son de precio de venta (bid). Una compra entra en el ask (apertura + spread);
  una venta entra en el bid y sus stops se tocan en el ask (precio + spread).
- Si una misma vela toca el stop loss y el take profit, se asume que tocó primero el stop loss.
- Si una vela abre más allá del stop loss o del take profit (hueco), se sale a la apertura.
- Una sola operación abierta a la vez.
- El resultado de cada operación se mide en R (1 R = distancia al stop loss). El saldo se simula
  arriesgando una fracción fija del saldo actual en cada operación.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .config import RiskConfig
from .risk import LONG, StopCalculator
from .rules import Context

TRADE_COLUMNS = [
    "direction", "signal_time", "entry_time", "entry", "sl", "tp",
    "exit_time", "exit", "exit_reason", "bars", "r",
]


@dataclass
class BacktestResult:
    trades: pd.DataFrame
    metrics: dict
    signals: int
    skipped: dict[str, int] = field(default_factory=dict)
    first_time: pd.Timestamp | None = None
    last_time: pd.Timestamp | None = None
    candles: int = 0


def _simulate(
    direction: str, k: int, fill: float, sl: float, tp: float,
    o: np.ndarray, h: np.ndarray, l: np.ndarray, c: np.ndarray, spread: float,
) -> tuple[int, float, str]:
    """Recorre las velas desde la de entrada `k`; devuelve (vela de salida, precio, motivo)."""
    n = len(o)
    for j in range(k, n):
        if direction == LONG:
            if j > k:
                if o[j] <= sl:
                    return j, o[j], "SL"
                if o[j] >= tp:
                    return j, o[j], "TP"
            if l[j] <= sl:
                return j, sl, "SL"
            if h[j] >= tp:
                return j, tp, "TP"
        else:
            ask_open, ask_high, ask_low = o[j] + spread, h[j] + spread, l[j] + spread
            if j > k:
                if ask_open >= sl:
                    return j, ask_open, "SL"
                if ask_open <= tp:
                    return j, ask_open, "TP"
            if ask_high >= sl:
                return j, sl, "SL"
            if ask_low <= tp:
                return j, tp, "TP"
    last = n - 1
    return last, (c[last] if direction == LONG else c[last] + spread), "FIN"


def run_backtest(
    df: pd.DataFrame,
    strategy,
    ctx: Context,
    risk: RiskConfig,
    spread_pips: float = 1.0,
    initial_balance: float = 10_000.0,
) -> BacktestResult:
    evaluation = strategy.evaluate(df, ctx)
    config = strategy.config
    calc = StopCalculator(df, config.stop_loss, config.take_profit, ctx.pip_size)

    o, h, l, c = (df[col].to_numpy(dtype=float) for col in ("open", "high", "low", "close"))
    times = df["time"]
    long_signal = evaluation.long.to_numpy()
    short_signal = evaluation.short.to_numpy()
    spread = spread_pips * ctx.pip_size
    n = len(df)

    trades: list[dict] = []
    skipped: Counter = Counter()
    signals = 0
    i = strategy.warmup_bars
    while i < n - 1:
        if long_signal[i]:
            direction = LONG
        elif short_signal[i]:
            direction = "short"
        else:
            i += 1
            continue
        signals += 1

        k = i + 1  # la entrada es en la apertura de la vela siguiente
        fill = o[k] + spread if direction == LONG else o[k]
        distance, reason = calc.stop_distance(i, direction, fill)
        if distance is None:
            skipped[reason] += 1
            i += 1
            continue
        target = calc.target_distance(i, distance)
        if target is None:
            skipped["ATR no disponible para el take profit"] += 1
            i += 1
            continue
        if target / distance + 1e-9 < risk.min_reward_risk:
            skipped[f"Relación beneficio/riesgo menor que {risk.min_reward_risk:g}"] += 1
            i += 1
            continue

        sign = 1 if direction == LONG else -1
        sl, tp = fill - sign * distance, fill + sign * target
        exit_j, exit_price, exit_reason = _simulate(direction, k, fill, sl, tp, o, h, l, c, spread)
        result_r = sign * (exit_price - fill) / distance
        trades.append(
            {
                "direction": direction,
                "signal_time": times.iloc[i],
                "entry_time": times.iloc[k],
                "entry": fill,
                "sl": sl,
                "tp": tp,
                "exit_time": times.iloc[exit_j],
                "exit": exit_price,
                "exit_reason": exit_reason,
                "bars": exit_j - k + 1,
                "r": result_r,
            }
        )
        i = max(exit_j, i + 1)

    frame = pd.DataFrame(trades, columns=TRADE_COLUMNS)
    return BacktestResult(
        trades=frame,
        metrics=summarize(frame, risk.risk_per_trade, initial_balance),
        signals=signals,
        skipped=dict(skipped),
        first_time=times.iloc[0] if n else None,
        last_time=times.iloc[-1] if n else None,
        candles=n,
    )


def summarize(trades: pd.DataFrame, risk_per_trade: float, initial_balance: float) -> dict:
    """Métricas del backtest. Con 0 operaciones devuelve ceros."""
    count = len(trades)
    metrics = {
        "trades": count, "wins": 0, "losses": 0, "win_rate": 0.0, "avg_r": 0.0, "total_r": 0.0,
        "profit_factor": None, "avg_win_r": 0.0, "avg_loss_r": 0.0, "max_consecutive_losses": 0,
        "final_balance": initial_balance, "return_pct": 0.0, "max_drawdown_pct": 0.0,
        "longs": 0, "shorts": 0, "avg_bars": 0.0,
    }
    if count == 0:
        return metrics

    r = trades["r"].to_numpy(dtype=float)
    wins, losses = r > 0, r < 0
    gross_win, gross_loss = r[wins].sum(), -r[losses].sum()

    balance = initial_balance
    curve = [balance]
    for value in r:
        balance = max(balance * (1.0 + risk_per_trade * value), 0.0)
        curve.append(balance)
    curve_arr = np.array(curve)
    peak = np.maximum.accumulate(curve_arr)
    drawdown = np.where(peak > 0, (peak - curve_arr) / np.where(peak > 0, peak, 1.0), 0.0)

    streak = longest = 0
    for is_loss in losses:
        streak = streak + 1 if is_loss else 0
        longest = max(longest, streak)

    metrics.update(
        wins=int(wins.sum()),
        losses=int(losses.sum()),
        win_rate=float(wins.mean()),
        avg_r=float(r.mean()),
        total_r=float(r.sum()),
        profit_factor=(float(gross_win / gross_loss) if gross_loss > 0 else None),
        avg_win_r=float(r[wins].mean()) if wins.any() else 0.0,
        avg_loss_r=float(r[losses].mean()) if losses.any() else 0.0,
        max_consecutive_losses=int(longest),
        final_balance=float(balance),
        return_pct=float((balance / initial_balance - 1.0) * 100.0),
        max_drawdown_pct=float(drawdown.max() * 100.0),
        longs=int((trades["direction"] == LONG).sum()),
        shorts=int((trades["direction"] != LONG).sum()),
        avg_bars=float(trades["bars"].mean()),
    )
    return metrics


def equity_curve(trades: pd.DataFrame, risk_per_trade: float, initial_balance: float) -> pd.DataFrame:
    """Saldo simulado tras cada operación (misma regla que `summarize`): columnas time y balance."""
    if trades.empty:
        return pd.DataFrame({"time": [], "balance": []})
    balance = initial_balance
    times = [pd.Timestamp(trades["entry_time"].iloc[0])]
    values = [balance]
    for exit_time, r in zip(trades["exit_time"], trades["r"].to_numpy(dtype=float)):
        balance = max(balance * (1.0 + risk_per_trade * r), 0.0)
        times.append(pd.Timestamp(exit_time))
        values.append(balance)
    return pd.DataFrame({"time": times, "balance": values})


def split_metrics(
    trades: pd.DataFrame, cut_time, risk_per_trade: float, initial_balance: float
) -> tuple[dict, dict]:
    """Métricas del periodo de ajuste (señales antes de `cut_time`) y del de validación (desde `cut_time`).

    Se simula toda la historia una sola vez y se reparten las operaciones por la fecha de su señal,
    así los indicadores del periodo de validación no arrancan "en frío".
    """
    signal_time = pd.to_datetime(trades["signal_time"], utc=True)
    cut = pd.Timestamp(cut_time)
    fit = trades[signal_time < cut]
    valid = trades[signal_time >= cut]
    return (
        summarize(fit, risk_per_trade, initial_balance),
        summarize(valid, risk_per_trade, initial_balance),
    )
