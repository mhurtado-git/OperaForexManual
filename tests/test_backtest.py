from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from forexplat.backtest import TRADE_COLUMNS, run_backtest, summarize
from forexplat.config import RiskConfig
from forexplat.risk import StopLossConfig, TakeProfitConfig
from forexplat.rules import Context
from strategy_stubs import StubStrategy

RISK = RiskConfig(risk_per_trade=0.01, max_daily_loss=0.03, min_reward_risk=2.0, max_spread_pips=2.0)
CTX = Context(pip_size=0.0001)
BASE = (1.1000, 1.1002, 1.0998, 1.1000)  # apertura, máximo, mínimo, cierre


def candles(n=40, **changes):
    """Velas planas; `changes` = {posición: (apertura, máximo, mínimo, cierre)}."""
    rows = [changes.get(i, BASE) for i in range(n)]
    o, h, l, c = (np.array(col, dtype=float) for col in zip(*rows))
    return pd.DataFrame(
        {"time": pd.date_range("2026-01-05", periods=n, freq="h", tz="UTC"),
         "open": o, "high": h, "low": l, "close": c, "tick_volume": 100}
    )


def run(df, long=(), short=(), sl=None, tp=None, spread=0.0, risk=RISK, warmup=10):
    strategy = StubStrategy(
        len(df), long, short, sl or StopLossConfig(mode="fixed_pips", pips=20),
        tp or TakeProfitConfig(mode="rr", reward_risk=2.0), warmup,
    )
    return run_backtest(df, strategy, CTX, risk, spread_pips=spread)


# Señal en la vela 10 -> entrada en la apertura de la 11. SL a 20 pips (1,0980) y TP a 40 (1,1040).

def test_long_hits_take_profit_after_three_candles():
    df = candles()
    df.loc[13, ["high", "close"]] = [1.1041, 1.1040]
    result = run(df, long=[10])
    trade = result.trades.iloc[0]
    assert len(result.trades) == 1
    assert trade["exit_reason"] == "TP" and trade["bars"] == 3
    assert trade["r"] == pytest.approx(2.0)
    assert trade["entry_time"] == df["time"].iloc[11] and trade["exit_time"] == df["time"].iloc[13]
    assert list(result.trades.columns) == TRADE_COLUMNS


def test_long_hits_stop_loss():
    df = candles()
    df.loc[12, "low"] = 1.0979
    trade = run(df, long=[10]).trades.iloc[0]
    assert trade["exit_reason"] == "SL" and trade["r"] == pytest.approx(-1.0)


def test_same_candle_stop_and_target_counts_as_stop():
    df = candles()
    df.loc[12, ["high", "low"]] = [1.1041, 1.0979]
    trade = run(df, long=[10]).trades.iloc[0]
    assert trade["exit_reason"] == "SL" and trade["r"] == pytest.approx(-1.0)


def test_gap_through_stop_exits_at_the_open():
    df = candles()
    df.loc[13, ["open", "low", "close"]] = [1.0950, 1.0940, 1.0950]
    trade = run(df, long=[10]).trades.iloc[0]
    assert trade["exit_reason"] == "SL"
    assert trade["exit"] == pytest.approx(1.0950)
    assert trade["r"] == pytest.approx(-2.5)  # peor que -1 R: el hueco se respeta


def test_gap_through_target_exits_at_the_open():
    df = candles()
    df.loc[13, ["open", "high", "close"]] = [1.1060, 1.1070, 1.1060]
    trade = run(df, long=[10]).trades.iloc[0]
    assert trade["exit_reason"] == "TP" and trade["r"] == pytest.approx(3.0)


def test_open_trade_is_closed_at_the_end_of_data():
    df = candles(20)
    df.loc[19, "close"] = 1.1010
    trade = run(df, long=[10]).trades.iloc[0]
    assert trade["exit_reason"] == "FIN"
    assert trade["r"] == pytest.approx(0.5)  # 10 pips de 20


def test_spread_is_paid_on_long_entries():
    df = candles()
    df.loc[13, ["high", "close"]] = [1.1041, 1.1040]
    free = run(df, long=[10], spread=0.0).trades.iloc[0]
    paid = run(df, long=[10], spread=1.0).trades.iloc[0]
    assert paid["entry"] == pytest.approx(free["entry"] + 0.0001)
    assert paid["r"] == pytest.approx(2.0)  # el TP se desplaza igual; el máximo bid también lo alcanza


def test_short_stops_are_tested_against_the_ask():
    df = candles()
    df.loc[12, "high"] = 1.10195  # bid 1,10195 no toca el SL (1,1020), pero el ask con 1 pip sí
    no_spread = run(df, short=[10], spread=0.0).trades.iloc[0]
    with_spread = run(df, short=[10], spread=1.0).trades.iloc[0]
    assert no_spread["exit_reason"] == "FIN"
    assert with_spread["exit_reason"] == "SL" and with_spread["r"] == pytest.approx(-1.0)


def test_short_take_profit():
    df = candles()
    df.loc[14, ["low", "close"]] = [1.0959, 1.0960]
    trade = run(df, short=[10]).trades.iloc[0]
    assert trade["direction"] == "short" and trade["exit_reason"] == "TP"
    assert trade["sl"] == pytest.approx(1.1020) and trade["tp"] == pytest.approx(1.0960)
    assert trade["r"] == pytest.approx(2.0)


def test_trades_never_overlap_and_signal_on_exit_candle_is_usable():
    df = candles(60)
    for j in (13, 25, 37):
        df.loc[j, ["high", "close"]] = [1.1041, 1.1040]
    result = run(df, long=range(10, 59))
    trades = result.trades
    assert len(trades) >= 3
    assert (trades["entry_time"].iloc[1:].to_numpy() > trades["exit_time"].iloc[:-1].to_numpy()).all()
    assert result.signals >= len(trades)


def test_signal_on_last_candle_cannot_enter():
    result = run(candles(20), long=[19])
    assert len(result.trades) == 0 and result.signals == 0


def test_signals_before_warmup_are_ignored():
    assert run(candles(), long=[5], warmup=10).signals == 0


def test_invalid_stops_are_skipped_with_reasons():
    atr = StopLossConfig(mode="atr", min_pips=0)
    result = run(candles(), long=[3], sl=atr, warmup=0)  # ATR aún sin velas suficientes
    assert len(result.trades) == 0 and result.signals == 1
    assert any("ATR no disponible" in reason for reason in result.skipped)

    weak = TakeProfitConfig(mode="fixed_pips", pips=20)  # 1:1 < mínimo 2
    result = run(candles(), long=[10], tp=weak)
    assert len(result.trades) == 0
    assert any("beneficio/riesgo" in reason for reason in result.skipped)

    tiny = StopLossConfig(mode="fixed_pips", pips=3, min_pips=5)
    result = run(candles(), long=[10], sl=tiny)
    assert any("mínimo" in reason for reason in result.skipped)


def test_result_describes_the_data():
    df = candles()
    result = run(df)
    assert result.candles == 40 and result.signals == 0
    assert result.first_time == df["time"].iloc[0] and result.last_time == df["time"].iloc[-1]
    assert result.metrics["trades"] == 0


# --- métricas ----------------------------------------------------------------

def frame(rs, directions=None):
    directions = directions or ["long"] * len(rs)
    return pd.DataFrame({"r": rs, "direction": directions, "bars": [2] * len(rs)})


def test_summarize_values():
    m = summarize(frame([2, -1, -1, 2, -1], ["long", "short", "long", "long", "short"]), 0.01, 10_000)
    assert (m["trades"], m["wins"], m["losses"]) == (5, 2, 3)
    assert m["win_rate"] == pytest.approx(0.4)
    assert m["avg_r"] == pytest.approx(0.2) and m["total_r"] == pytest.approx(1.0)
    assert m["profit_factor"] == pytest.approx(4 / 3)
    assert m["avg_win_r"] == pytest.approx(2.0) and m["avg_loss_r"] == pytest.approx(-1.0)
    assert m["max_consecutive_losses"] == 2
    assert (m["longs"], m["shorts"]) == (3, 2)
    expected = 10_000 * 1.02 * 0.99 * 0.99 * 1.02 * 0.99
    assert m["final_balance"] == pytest.approx(expected)
    assert m["return_pct"] == pytest.approx((expected / 10_000 - 1) * 100)
    assert m["max_drawdown_pct"] == pytest.approx((1 - 0.99 * 0.99) * 100)  # desde el pico de 10.200


def test_summarize_edge_cases():
    empty = summarize(frame([]), 0.01, 5_000)
    assert empty["trades"] == 0 and empty["final_balance"] == 5_000 and empty["profit_factor"] is None
    only_wins = summarize(frame([1.0, 2.0]), 0.01, 10_000)
    assert only_wins["profit_factor"] is None and only_wins["max_drawdown_pct"] == 0.0
    ruin = summarize(frame([-1.0] * 5), 0.05, 100)  # el saldo nunca baja de cero
    assert ruin["final_balance"] >= 0
