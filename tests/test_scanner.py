import pytest

from fakes import FakeClient
from forexplat.config import Profile, RiskConfig
from forexplat.scanner import scan
from forexplat.strategy import Strategy, StrategyConfig
from helpers import downtrend_then_jump, make_candles

RISK = RiskConfig(risk_per_trade=0.01, max_daily_loss=0.03, min_reward_risk=2.0, max_spread_pips=2.0)
PROFILE = Profile(mode="demo", symbols=["EURUSD"], timeframe="H1", bars=60, risk=RISK)

STRATEGY = Strategy(
    "demo",
    StrategyConfig.model_validate(
        {
            "trigger": "cruce",
            "rules": [
                {"type": "ma_cross", "name": "cruce", "fast": 3, "slow": 5},
                {"type": "atr_filter", "name": "atr", "min_pips": 0},
            ],
            "stop_loss": {"mode": "fixed_pips", "pips": 20},
            "take_profit": {"mode": "rr", "reward_risk": 2.0},
        }
    ),
)

LONG_CANDLES = make_candles(downtrend_then_jump())
SHORT_CANDLES = make_candles([1.1 + 0.0001 * i for i in range(60)] + [1.1 + 0.0059 - 0.0050])


def test_long_signal_builds_plan_from_the_ask_price():
    client = FakeClient(LONG_CANDLES, tick={"bid": 1.1000, "ask": 1.1001})
    result = scan(client, PROFILE, STRATEGY, "EURUSD")
    assert result.signal is not None and result.signal.direction == "long"
    plan = result.plan
    assert plan.ok and plan.entry == pytest.approx(1.1001)
    assert plan.sl == pytest.approx(1.0981) and plan.tp == pytest.approx(1.1041)
    assert plan.lots == pytest.approx(0.5)
    assert result.balance == 10_000 and result.currency == "USD"
    assert result.candle_time == LONG_CANDLES["time"].iloc[-1]
    assert client.margin_calls == [("long", plan.lots, 1.1001)]
    assert result.warnings == []


def test_short_signal_uses_the_bid_price():
    client = FakeClient(SHORT_CANDLES, tick={"bid": 1.1000, "ask": 1.1001})
    result = scan(client, PROFILE, STRATEGY, "EURUSD")
    assert result.signal is not None and result.signal.direction == "short"
    assert result.plan.ok and result.plan.entry == pytest.approx(1.1000)
    assert result.plan.sl == pytest.approx(1.1020) and result.plan.tp == pytest.approx(1.0960)


def test_no_signal_returns_states_and_no_plan():
    flat = make_candles([1.1 - 0.0001 * i for i in range(61)])
    client = FakeClient(flat)
    result = scan(client, PROFILE, STRATEGY, "EURUSD")
    assert result.signal is None and result.plan is None
    assert set(result.states) == {"cruce", "atr"}
    assert client.margin_calls == []


def test_wide_spread_rejects_the_signal():
    client = FakeClient(LONG_CANDLES, summary={"spread_points": 30})
    result = scan(client, PROFILE, STRATEGY, "EURUSD")
    assert result.signal is not None and not result.plan.ok
    assert "Spread" in result.plan.rejected
    assert client.margin_calls == []


def test_insufficient_free_margin_rejects_the_signal():
    client = FakeClient(LONG_CANDLES, margin=20_000.0, margin_free=9_500.0)
    plan = scan(client, PROFILE, STRATEGY, "EURUSD").plan
    assert not plan.ok and "Margen libre insuficiente" in plan.rejected


def test_unverifiable_margin_only_warns():
    client = FakeClient(LONG_CANDLES, margin=None)
    result = scan(client, PROFILE, STRATEGY, "EURUSD")
    assert result.plan.ok
    assert any("margen" in warning for warning in result.warnings)


def test_too_few_candles_adds_a_warning():
    client = FakeClient(LONG_CANDLES.iloc[-20:].reset_index(drop=True))
    result = scan(client, PROFILE, STRATEGY, "EURUSD")
    assert any("necesita al menos" in warning for warning in result.warnings)
