import math

import numpy as np
import pandas as pd
import pytest

from forexplat import indicators as ind
from helpers import random_candles


def test_sma_known_values():
    out = ind.sma(pd.Series([1.0, 2, 3, 4, 5]), 3)
    assert math.isnan(out.iloc[0]) and math.isnan(out.iloc[1])
    assert out.iloc[2:].tolist() == [2.0, 3.0, 4.0]


def test_ema_matches_manual_recursion():
    # alpha = 2 / (span + 1) = 0.5; la semilla es el primer dato.
    out = ind.ema(pd.Series([1.0, 2, 3, 4, 5]), 3)
    assert math.isnan(out.iloc[0]) and math.isnan(out.iloc[1])
    assert out.iloc[2:].tolist() == pytest.approx([2.25, 3.125, 4.0625])


def test_moving_average_dispatch_and_error():
    s = pd.Series(np.arange(1.0, 30.0))
    assert ind.moving_average(s, 5, "ema").equals(ind.ema(s, 5))
    assert ind.moving_average(s, 5, "sma").equals(ind.sma(s, 5))
    with pytest.raises(ValueError, match="no soportado"):
        ind.moving_average(s, 5, "wma")


def test_rsi_extremes_and_flat_price():
    up = pd.Series(np.arange(1.0, 60.0))
    assert ind.rsi(up, 14).iloc[-1] == 100.0
    assert ind.rsi(-up, 14).iloc[-1] == 0.0
    assert ind.rsi(pd.Series([5.0] * 40), 14).iloc[-1] == 50.0


def test_rsi_matches_wilder_reference_loop():
    close = random_candles(300, seed=3)["close"]
    period = 14
    values = close.to_numpy()
    alpha = 1.0 / period
    avg_gain = avg_loss = None
    expected = [math.nan] * len(values)
    for i in range(1, len(values)):
        change = values[i] - values[i - 1]
        gain, loss = max(change, 0.0), max(-change, 0.0)
        if avg_gain is None:
            avg_gain, avg_loss = gain, loss
        else:
            avg_gain = (1 - alpha) * avg_gain + alpha * gain
            avg_loss = (1 - alpha) * avg_loss + alpha * loss
        if i >= period:
            expected[i] = 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    got = ind.rsi(close, period).to_numpy()
    assert np.allclose(got[period:], np.array(expected)[period:], atol=1e-9)
    assert np.isnan(got[: period - 1]).all()
    assert ((got[period:] >= 0) & (got[period:] <= 100)).all()


def test_atr_constant_range():
    n = 40
    high = pd.Series([1.1002] * n)
    low = pd.Series([1.1000] * n)
    close = pd.Series([1.1001] * n)
    assert ind.atr(high, low, close, 14).iloc[-1] == pytest.approx(0.0002)


def test_true_range_uses_previous_close_on_gaps():
    high = pd.Series([1.1010, 1.1030])
    low = pd.Series([1.1000, 1.1020])
    close = pd.Series([1.1005, 1.1025])
    tr = ind.true_range(high, low, close)
    assert tr.iloc[0] == pytest.approx(0.0010)  # primera vela: máximo - mínimo
    assert tr.iloc[1] == pytest.approx(0.0025)  # |máximo - cierre anterior| = 0.0025


def test_macd_relations():
    close = random_candles(200, seed=5)["close"]
    line, signal, hist = ind.macd(close, 12, 26, 9)
    assert line.equals(ind.ema(close, 12) - ind.ema(close, 26))
    valid = signal.notna()
    assert valid.sum() > 100
    assert np.allclose((line - signal)[valid], hist[valid])
    flat = ind.macd(pd.Series([2.0] * 80))[2]
    assert flat.dropna().abs().max() < 1e-12


def test_bollinger_known_values():
    upper, mid, lower = ind.bollinger(pd.Series([1.0, 2, 3, 4, 5]), period=5, deviation=2.0)
    std = math.sqrt(2.0)  # desviación poblacional de 1..5
    assert mid.iloc[-1] == 3.0
    assert upper.iloc[-1] == pytest.approx(3.0 + 2 * std)
    assert lower.iloc[-1] == pytest.approx(3.0 - 2 * std)
    assert math.isnan(upper.iloc[3])


def test_stochastic_extremes():
    n = 40
    rising = pd.Series(np.arange(1.0, n + 1))
    k, d = ind.stochastic(rising + 0.5, rising - 0.5, rising, 14, 3, 3)
    assert 90 < k.iloc[-1] <= 100  # cierra cerca del máximo del rango
    falling = pd.Series(np.arange(n, 0.0, -1))
    k2, _ = ind.stochastic(falling + 0.5, falling - 0.5, falling, 14, 3, 3)
    assert 0 <= k2.iloc[-1] < 10
    assert math.isnan(k.iloc[10])


def test_fib_swing_up_down_and_flat():
    n = 30
    up = pd.Series(np.r_[np.linspace(100, 110, 20), np.linspace(110, 106, 10)])
    hi, lo, direction = ind.fib_swing(up + 0.1, up - 0.1, lookback=30)
    assert hi.iloc[-1] == pytest.approx(110.1)
    assert lo.iloc[-1] == pytest.approx(99.9)
    assert direction.iloc[-1] == 1
    assert hi.iloc[:-1].isna().all() and (direction.iloc[:-1] == 0).all()

    down = pd.Series(np.r_[np.linspace(110, 100, 20), np.linspace(100, 104, 10)])
    _, _, direction_down = ind.fib_swing(down + 0.1, down - 0.1, lookback=30)
    assert direction_down.iloc[-1] == -1

    flat = pd.Series([100.0] * n)
    _, _, direction_flat = ind.fib_swing(flat, flat, lookback=10)
    assert (direction_flat == 0).all()


def test_indicators_are_causal():
    """El valor de una vela no cambia al añadir velas posteriores (no hay look-ahead)."""
    df = random_candles(400, seed=11)
    cut = 250
    for name, func in (
        ("rsi", lambda d: ind.rsi(d["close"], 14)),
        ("atr", lambda d: ind.atr(d["high"], d["low"], d["close"], 14)),
        ("ema", lambda d: ind.ema(d["close"], 20)),
        ("macd", lambda d: ind.macd(d["close"])[2]),
        ("bb", lambda d: ind.bollinger(d["close"])[0]),
        ("stoch", lambda d: ind.stochastic(d["high"], d["low"], d["close"])[0]),
        ("fib", lambda d: ind.fib_swing(d["high"], d["low"], 60)[0]),
    ):
        full = func(df)
        part = func(df.iloc[:cut])
        assert np.allclose(full.iloc[:cut].to_numpy(), part.to_numpy(), equal_nan=True), name
