import pytest
from pydantic import ValidationError

from forexplat.config import RiskConfig
from forexplat.risk import (
    LONG,
    SHORT,
    StopCalculator,
    StopLossConfig,
    SymbolSpec,
    TakeProfitConfig,
    build_plan,
    position_size,
)
from helpers import make_candles

RISK = RiskConfig(risk_per_trade=0.01, max_daily_loss=0.03, min_reward_risk=2.0, max_spread_pips=2.0)
PIP = 0.0001


def eurusd(**overrides):
    base = dict(
        symbol="EURUSD", digits=5, point=0.00001, tick_value=1.0, tick_size=0.00001,
        volume_min=0.01, volume_step=0.01, volume_max=100.0, spread_points=10, stops_level_points=0,
    )
    base.update(overrides)
    return SymbolSpec(**base)


def flat_candles(n=80):
    # Velas planas: máximo 1,1002, mínimo 1,0998 y ATR constante de 0,0004 (4 pips).
    return make_candles([1.1] * n)


def calc(df=None, sl=None, tp=None):
    return StopCalculator(
        df if df is not None else flat_candles(),
        sl or StopLossConfig(mode="fixed_pips", pips=20),
        tp or TakeProfitConfig(mode="rr", reward_risk=2.0),
        PIP,
    )


# --- configuración -----------------------------------------------------------

def test_stop_and_target_config_validation():
    with pytest.raises(ValidationError, match="exige el parámetro pips"):
        StopLossConfig(mode="fixed_pips")
    with pytest.raises(ValidationError, match="max_pips mayor"):
        StopLossConfig(min_pips=10, max_pips=8)
    with pytest.raises(ValidationError, match="exige el parámetro pips"):
        TakeProfitConfig(mode="fixed_pips")
    with pytest.raises(ValidationError, match="atr_multipler"):
        StopLossConfig(atr_multipler=2)


# --- símbolo -----------------------------------------------------------------

def test_pip_size_for_five_three_and_four_digit_symbols():
    assert eurusd().pip_size == pytest.approx(0.0001)
    assert eurusd(digits=3, point=0.001).pip_size == pytest.approx(0.01)
    assert eurusd(digits=4, point=0.0001).pip_size == pytest.approx(0.0001)


def test_from_summary_maps_fields():
    spec = SymbolSpec.from_summary(
        {
            "symbol": "EURUSD", "digits": 5, "point": 0.00001, "tick_value": 1.0, "tick_size": 0.00001,
            "volume_min": 0.01, "volume_step": 0.01, "volume_max": 100.0, "spread_points": 12,
            "stops_level": 30,
        }
    )
    assert spec.stops_level_points == 30 and spec.spread_points == 12


# --- tamaño de la posición ---------------------------------------------------

def test_position_size_basic():
    lots, money, reason = position_size(eurusd(), 10_000, 0.01, 0.0020)  # 20 pips
    assert reason is None
    assert lots == pytest.approx(0.5)
    assert money == pytest.approx(100.0)


def test_position_size_rounds_down_never_up():
    # 100 de riesgo con 23 pips: 100 / 230 = 0,4347 lotes -> 0,43
    lots, money, _ = position_size(eurusd(), 10_000, 0.01, 0.0023)
    assert lots == pytest.approx(0.43)
    assert money <= 100.0


def test_position_size_rejects_when_min_lot_exceeds_risk():
    lots, money, reason = position_size(eurusd(), 100, 0.01, 0.0020)  # permitido 1; el mínimo arriesga 2
    assert lots == 0 and money == 0
    assert "lote mínimo" in reason


def test_position_size_caps_at_volume_max_and_validates_data():
    lots, _, _ = position_size(eurusd(volume_max=0.2), 10_000, 0.01, 0.0020)
    assert lots == pytest.approx(0.2)
    _, _, reason = position_size(eurusd(tick_value=0.0), 10_000, 0.01, 0.0020)
    assert "no válidos" in reason
    _, _, reason = position_size(eurusd(), 10_000, 0.01, 0.0)
    assert "no válidos" in reason


# --- stop loss y take profit -------------------------------------------------

def test_stop_distance_modes():
    df = flat_candles()
    atr_calc = calc(df, StopLossConfig(mode="atr", atr_multiplier=1.5, min_pips=0))
    distance, reason = atr_calc.stop_distance(50, LONG, 1.1)
    assert reason is None and distance == pytest.approx(0.0006)

    fixed, _ = calc().stop_distance(50, LONG, 1.1)
    assert fixed == pytest.approx(0.0020)

    swing = calc(df, StopLossConfig(mode="swing", swing_lookback=10, swing_buffer_atr=0.25, min_pips=0))
    long_distance, _ = swing.stop_distance(50, LONG, 1.1002)  # (1,1002 - 1,0998) + 0,25 * 0,0004
    short_distance, _ = swing.stop_distance(50, SHORT, 1.0998)
    assert long_distance == pytest.approx(0.0005)
    assert short_distance == pytest.approx(0.0005)


def test_stop_distance_rejections():
    df = flat_candles()
    distance, reason = calc(df, StopLossConfig(mode="atr", min_pips=5)).stop_distance(2, LONG, 1.1)
    assert distance is None and "ATR no disponible" in reason

    atr_calc = calc(df, StopLossConfig(mode="atr", atr_multiplier=1.5, min_pips=10))
    assert "menor que el mínimo" in atr_calc.stop_distance(50, LONG, 1.1)[1]

    wide = calc(df, StopLossConfig(mode="fixed_pips", pips=60, min_pips=5, max_pips=50))
    assert "mayor que el máximo" in wide.stop_distance(50, LONG, 1.1)[1]

    swing = calc(df, StopLossConfig(mode="swing", swing_lookback=10, min_pips=0))
    assert "otro lado" in swing.stop_distance(50, LONG, 1.0990)[1]  # entrada bajo el mínimo reciente
    assert "swing" in swing.stop_distance(3, LONG, 1.1)[1]


def test_target_distance_modes():
    assert calc().target_distance(50, 0.0020) == pytest.approx(0.0040)
    fixed = calc(tp=TakeProfitConfig(mode="fixed_pips", pips=30))
    assert fixed.target_distance(50, 0.0020) == pytest.approx(0.0030)
    by_atr = calc(tp=TakeProfitConfig(mode="atr", atr_multiplier=3.0))
    assert by_atr.target_distance(50, 0.0020) == pytest.approx(0.0012)
    assert by_atr.target_distance(2, 0.0020) is None


# --- plan completo -----------------------------------------------------------

def test_build_plan_long_and_short():
    long_plan = build_plan(LONG, 1.1000, 50, calc(), eurusd(), 10_000, RISK)
    assert long_plan.ok
    assert long_plan.sl == pytest.approx(1.0980) and long_plan.tp == pytest.approx(1.1040)
    assert long_plan.sl_pips == pytest.approx(20) and long_plan.tp_pips == pytest.approx(40)
    assert long_plan.reward_risk == pytest.approx(2.0)
    assert long_plan.lots == pytest.approx(0.5) and long_plan.risk_money == pytest.approx(100.0)
    assert long_plan.spread_pips == pytest.approx(1.0)

    short_plan = build_plan(SHORT, 1.1000, 50, calc(), eurusd(), 10_000, RISK)
    assert short_plan.sl == pytest.approx(1.1020) and short_plan.tp == pytest.approx(1.0960)


def test_build_plan_rejections():
    plan = build_plan(LONG, 1.1, 50, calc(), eurusd(spread_points=30), 10_000, RISK)
    assert not plan.ok and "Spread" in plan.rejected

    plan = build_plan(LONG, 1.1, 50, calc(), eurusd(stops_level_points=300), 10_000, RISK)
    assert not plan.ok and "distancia mínima" in plan.rejected

    weak_tp = calc(tp=TakeProfitConfig(mode="fixed_pips", pips=20))  # relación 1:1 < 2
    plan = build_plan(LONG, 1.1, 50, weak_tp, eurusd(), 10_000, RISK)
    assert not plan.ok and "beneficio/riesgo" in plan.rejected

    plan = build_plan(LONG, 1.1, 50, calc(), eurusd(), 100, RISK)  # saldo pequeño
    assert not plan.ok and "lote mínimo" in plan.rejected

    short_sl = calc(sl=StopLossConfig(mode="fixed_pips", pips=3, min_pips=5))
    plan = build_plan(LONG, 1.1, 50, short_sl, eurusd(), 10_000, RISK)
    assert not plan.ok and "mínimo" in plan.rejected

    no_atr = calc(tp=TakeProfitConfig(mode="atr"))
    plan = build_plan(LONG, 1.1, 2, no_atr, eurusd(), 10_000, RISK)
    assert not plan.ok and "ATR" in plan.rejected


def test_risk_never_exceeds_configured_fraction():
    for balance in (500, 1_234.56, 10_000, 250_000):
        for pips in (7, 13, 25, 61):
            spec = eurusd()
            lots, money, reason = position_size(spec, balance, 0.01, pips * PIP)
            if reason is None:
                assert money <= balance * 0.01 + 1e-9
