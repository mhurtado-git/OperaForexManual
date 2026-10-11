"""Cada regla se compara contra una definición independiente escrita con bucles simples."""

import math

import numpy as np
import pytest
from pydantic import ValidationError

from forexplat import indicators as ind
from forexplat.rules import (
    AtrFilterRule,
    BollingerRule,
    Context,
    FibonacciRule,
    MaCrossRule,
    MacdRule,
    RsiRule,
    StochasticRule,
)
from helpers import downtrend_then_jump, make_candles, random_candles

CTX = Context(pip_size=0.0001)
SEEDS = (3, 7, 11)


def loop_cross(a, b, up):
    a, b = np.asarray(a, float), np.asarray(b, float)
    out = np.zeros(len(a), dtype=bool)
    for i in range(1, len(a)):
        values = (a[i - 1], b[i - 1], a[i], b[i])
        if any(math.isnan(v) for v in values):
            continue
        p1, p2, c1, c2 = values
        out[i] = (p1 <= p2 and c1 > c2) if up else (p1 >= p2 and c1 < c2)
    return out


def loop_cross_level(s, level, up):
    s = np.asarray(s, float)
    out = np.zeros(len(s), dtype=bool)
    for i in range(1, len(s)):
        if math.isnan(s[i]) or math.isnan(s[i - 1]):
            continue
        out[i] = (s[i - 1] <= level < s[i]) if up else (s[i - 1] >= level > s[i])
    return out


def assert_same(rule_series, expected, label):
    got = rule_series.to_numpy()
    assert got.dtype == bool, label
    assert (got == expected).all(), f"{label}: {int((got != expected).sum())} velas distintas"
    assert expected.sum() > 0, f"{label}: la prueba no ejerce ningún evento"


# --- ma_cross ---------------------------------------------------------------

def test_ma_cross_event_matches_loop_for_ema_and_sma():
    for seed in SEEDS:
        df = random_candles(1500, seed)
        for ma_type in ("ema", "sma"):
            rule = MaCrossRule(type="ma_cross", fast=10, slow=30, ma_type=ma_type, mode="event")
            out = rule.evaluate(df, CTX)
            fast = ind.moving_average(df["close"], 10, ma_type).to_numpy()
            slow = ind.moving_average(df["close"], 30, ma_type).to_numpy()
            assert_same(out.long, loop_cross(fast, slow, True), f"{ma_type} largo seed {seed}")
            assert_same(out.short, loop_cross(fast, slow, False), f"{ma_type} corto seed {seed}")


def test_ma_cross_state_is_trend():
    df = random_candles(800, seed=3)
    rule = MaCrossRule(type="ma_cross", fast=20, slow=50, mode="state")
    out = rule.evaluate(df, CTX)
    fast = ind.ema(df["close"], 20).to_numpy()
    slow = ind.ema(df["close"], 50).to_numpy()
    valid = ~np.isnan(slow)
    assert (out.long.to_numpy()[valid] == (fast > slow)[valid]).all()
    assert (out.short.to_numpy()[valid] == (fast < slow)[valid]).all()
    assert not out.long.to_numpy()[~valid].any()


def test_ma_cross_crafted_cross_happens_once_on_the_jump_candle():
    df = make_candles(downtrend_then_jump())
    event = MaCrossRule(type="ma_cross", fast=3, slow=5, mode="event").evaluate(df, CTX)
    assert event.long.to_numpy().nonzero()[0].tolist() == [len(df) - 1]
    assert not event.short.any()
    state = MaCrossRule(type="ma_cross", fast=3, slow=5, mode="state").evaluate(df, CTX)
    assert state.long.iloc[-1] and not state.long.iloc[-2]
    assert "EMA3 cruza sobre EMA5" in event.label_long


# --- rsi --------------------------------------------------------------------

def test_rsi_momentum_state_in_zone():
    closes = [100.0]
    for i in range(120):
        closes.append(closes[-1] + (-1.0 if i % 3 == 2 else 1.0))  # +1, +1, -1, ...
    df = make_candles(closes, wick=0.1)
    rule = RsiRule(type="rsi", period=14, mode="momentum", overbought=80, oversold=20)
    out = rule.evaluate(df, CTX)
    assert out.long.iloc[40:].all()  # RSI ronda 64-69: entre 50 y 80
    assert not out.short.any()
    # En una subida sin pausas el RSI llega a 100: sobrecompra, no hay compra de momentum.
    ramp = make_candles(np.arange(100.0, 160.0), wick=0.1)
    assert not RsiRule(type="rsi", mode="momentum").evaluate(ramp, CTX).long.iloc[-1]


def test_rsi_reversal_event_matches_loop():
    for seed in SEEDS:
        df = random_candles(1500, seed)
        rule = RsiRule(type="rsi", period=14, mode="reversal", oversold=40, overbought=60)
        out = rule.evaluate(df, CTX)
        value = ind.rsi(df["close"], 14).to_numpy()
        assert_same(out.long, loop_cross_level(value, 40, True), f"rsi largo seed {seed}")
        assert_same(out.short, loop_cross_level(value, 60, False), f"rsi corto seed {seed}")


def test_rsi_reversal_crafted_single_event():
    closes = [200.0 - i for i in range(50)] + [150.0 + i for i in range(40)]
    df = make_candles(closes, wick=0.1)
    out = RsiRule(type="rsi", period=14, mode="reversal").evaluate(df, CTX)
    events = out.long.to_numpy().nonzero()[0]
    assert len(events) == 1
    value = ind.rsi(df["close"], 14)
    idx = events[0]
    assert value.iloc[idx - 1] <= 30 < value.iloc[idx]


# --- macd -------------------------------------------------------------------

def test_macd_cross_matches_loop_and_histogram_is_state():
    for seed in SEEDS:
        df = random_candles(1500, seed)
        line, signal, hist = ind.macd(df["close"], 12, 26, 9)
        cross = MacdRule(type="macd", mode="cross").evaluate(df, CTX)
        assert_same(cross.long, loop_cross(line, signal, True), f"macd largo seed {seed}")
        assert_same(cross.short, loop_cross(line, signal, False), f"macd corto seed {seed}")
        state = MacdRule(type="macd", mode="histogram").evaluate(df, CTX)
        valid = hist.notna().to_numpy()
        assert (state.long.to_numpy()[valid] == (hist.to_numpy() > 0)[valid]).all()
        assert (state.short.to_numpy()[valid] == (hist.to_numpy() < 0)[valid]).all()
        assert not state.long.to_numpy()[~valid].any()


# --- bollinger --------------------------------------------------------------

def loop_bollinger(df, period, deviation, mode):
    upper, _, lower = ind.bollinger(df["close"], period, deviation)
    c, u, l = df["close"].to_numpy(), upper.to_numpy(), lower.to_numpy()
    long = np.zeros(len(c), dtype=bool)
    short = np.zeros(len(c), dtype=bool)
    for i in range(1, len(c)):
        if any(math.isnan(v) for v in (u[i], u[i - 1], l[i], l[i - 1])):
            continue
        if mode == "reversion":
            long[i] = c[i - 1] < l[i - 1] and c[i] >= l[i]
            short[i] = c[i - 1] > u[i - 1] and c[i] <= u[i]
        else:
            long[i] = c[i - 1] <= u[i - 1] and c[i] > u[i]
            short[i] = c[i - 1] >= l[i - 1] and c[i] < l[i]
    return long, short


def test_bollinger_modes_match_loop():
    for seed in SEEDS:
        df = random_candles(2500, seed)
        for mode in ("reversion", "breakout"):
            out = BollingerRule(type="bollinger", period=20, deviation=1.5, mode=mode).evaluate(df, CTX)
            long, short = loop_bollinger(df, 20, 1.5, mode)
            assert_same(out.long, long, f"bollinger {mode} largo seed {seed}")
            assert_same(out.short, short, f"bollinger {mode} corto seed {seed}")


# --- stochastic -------------------------------------------------------------

def test_stochastic_cross_matches_loop_and_state():
    for seed in SEEDS:
        df = random_candles(3000, seed)
        k, d = ind.stochastic(df["high"], df["low"], df["close"], 14, 3, 3)
        kv, dv = k.to_numpy(), d.to_numpy()
        out = StochasticRule(type="stochastic", mode="cross", oversold=35, overbought=65).evaluate(df, CTX)
        prev_k = np.r_[np.nan, kv[:-1]]
        expected_long = loop_cross(kv, dv, True) & (prev_k <= 35)
        expected_short = loop_cross(kv, dv, False) & (prev_k >= 65)
        assert_same(out.long, expected_long, f"estocástico largo seed {seed}")
        assert_same(out.short, expected_short, f"estocástico corto seed {seed}")

        state = StochasticRule(type="stochastic", mode="state").evaluate(df, CTX)
        valid = ~np.isnan(dv)
        assert (state.long.to_numpy()[valid] == ((kv > dv) & (kv < 80))[valid]).all()
        assert (state.short.to_numpy()[valid] == ((kv < dv) & (kv > 20))[valid]).all()


# --- fibonacci --------------------------------------------------------------

def loop_fibonacci(df, rule):
    high, low, close = (df[c].to_numpy() for c in ("high", "low", "close"))
    atr = ind.atr(df["high"], df["low"], df["close"], rule.atr_period).to_numpy()
    n = len(df)
    long = np.zeros(n, dtype=bool)
    short = np.zeros(n, dtype=bool)
    for i in range(rule.lookback - 1, n):
        window_high = high[i - rule.lookback + 1 : i + 1]
        window_low = low[i - rule.lookback + 1 : i + 1]
        idx_high, idx_low = int(np.argmax(window_high)), int(np.argmin(window_low))
        top, bottom = window_high.max(), window_low.min()
        span = top - bottom
        if math.isnan(atr[i]) or span < rule.min_range_atr * atr[i]:
            continue
        if idx_low < idx_high:
            long[i] = top - rule.zone_end * span <= close[i] <= top - rule.zone_start * span
        elif idx_high < idx_low:
            short[i] = bottom + rule.zone_start * span <= close[i] <= bottom + rule.zone_end * span
    return long, short


def test_fibonacci_matches_loop_on_random_data():
    for seed in SEEDS:
        df = random_candles(600, seed)
        rule = FibonacciRule(type="fibonacci", lookback=50, min_range_atr=2.0)
        out = rule.evaluate(df, CTX)
        long, short = loop_fibonacci(df, rule)
        assert_same(out.long, long, f"fib largo seed {seed}")
        assert_same(out.short, short, f"fib corto seed {seed}")


def test_fibonacci_crafted_zone_boundaries():
    def last_bar_long(target):
        closes = np.r_[np.linspace(100, 110, 40), np.linspace(110, target, 20)]
        df = make_candles(closes, wick=0.1)
        rule = FibonacciRule(type="fibonacci", lookback=60)
        return bool(rule.evaluate(df, CTX).long.iloc[-1])

    # Impulso 99.9 -> 110.1 (10.2): zona 38.2%-61.8% = 103.7964 .. 106.2036
    assert last_bar_long(105.0)
    assert not last_bar_long(103.0)  # retrocedió demasiado
    assert not last_bar_long(107.5)  # retroceso todavía pequeño


def test_fibonacci_zone_is_parametrizable():
    closes = np.r_[np.linspace(100, 110, 40), np.linspace(110, 107.5, 20)]
    df = make_candles(closes, wick=0.1)
    shallow = FibonacciRule(type="fibonacci", lookback=60, zone_start=0.1, zone_end=0.382)
    assert shallow.evaluate(df, CTX).long.iloc[-1]  # 107.5 cae en 10%-38.2%


# --- atr_filter -------------------------------------------------------------

def test_atr_filter_pips_range():
    df = make_candles([1.1000] * 60, wick=0.0010)  # rango de 20 pips
    assert AtrFilterRule(type="atr_filter", min_pips=10).evaluate(df, CTX).long.iloc[-1]
    assert not AtrFilterRule(type="atr_filter", min_pips=30).evaluate(df, CTX).long.iloc[-1]
    assert not AtrFilterRule(type="atr_filter", min_pips=5, max_pips=15).evaluate(df, CTX).long.iloc[-1]
    out = AtrFilterRule(type="atr_filter", min_pips=10, max_pips=40).evaluate(df, CTX)
    assert out.long.iloc[-1] and out.short.iloc[-1]
    assert out.values["ATR14 (pips)"].iloc[-1] == pytest.approx(20.0)
    # El pip cambia la medida: con pip 0.01 (JPY) el mismo ATR son 0.2 pips.
    jpy = AtrFilterRule(type="atr_filter", min_pips=10).evaluate(df, Context(pip_size=0.01))
    assert not jpy.long.iloc[-1]


# --- validación -------------------------------------------------------------

@pytest.mark.parametrize(
    "rule_cls, params",
    [
        (MaCrossRule, {"fast": 50, "slow": 20}),
        (MaCrossRule, {"fast": 1}),
        (MaCrossRule, {"ma_type": "wma"}),
        (MaCrossRule, {"periodo": 14}),  # clave desconocida
        (RsiRule, {"oversold": 60, "mid": 50}),
        (RsiRule, {"overbought": 100}),
        (MacdRule, {"fast": 26, "slow": 12}),
        (BollingerRule, {"deviation": 0}),
        (StochasticRule, {"oversold": 80, "overbought": 20}),
        (FibonacciRule, {"zone_start": 0.7, "zone_end": 0.5}),
        (FibonacciRule, {"lookback": 5}),
        (AtrFilterRule, {"min_pips": 10, "max_pips": 5}),
        (MaCrossRule, {"name": "con espacios"}),
    ],
)
def test_invalid_rule_parameters_are_rejected(rule_cls, params):
    type_name = rule_cls.model_fields["type"].annotation.__args__[0]
    with pytest.raises(ValidationError):
        rule_cls.model_validate({"type": type_name, **params})


def test_rule_name_defaults_to_type_and_accepts_custom_name():
    assert RsiRule(type="rsi").rule_name == "rsi"
    assert RsiRule(type="rsi", name="rsi_lento").rule_name == "rsi_lento"


def test_event_flags():
    assert MaCrossRule(type="ma_cross", mode="event").is_event
    assert not MaCrossRule(type="ma_cross", mode="state").is_event
    assert RsiRule(type="rsi", mode="reversal").is_event
    assert not RsiRule(type="rsi", mode="momentum").is_event
    assert MacdRule(type="macd", mode="cross").is_event
    assert not MacdRule(type="macd", mode="histogram").is_event
    assert BollingerRule(type="bollinger").is_event
    assert StochasticRule(type="stochastic", mode="cross").is_event
    assert not FibonacciRule(type="fibonacci").is_event
    assert not AtrFilterRule(type="atr_filter").is_event
