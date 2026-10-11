from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from forexplat.rules import Context, RuleOutput
from forexplat.strategy import (
    Strategy,
    StrategyConfig,
    combine,
    get_strategy,
    load_strategies,
)
from helpers import downtrend_then_jump, make_candles, random_candles

REPO = Path(__file__).resolve().parents[1]
CTX = Context(pip_size=0.0001)


def rules_config(**overrides):
    base = {
        "trigger": "ema_cross",
        "rules": [
            {"type": "ma_cross", "name": "ema_cross", "fast": 3, "slow": 5},
            {"type": "atr_filter", "name": "atr", "min_pips": 0},
        ],
    }
    base.update(overrides)
    return base


def bool_series(values):
    return pd.Series(values, dtype=bool)


def output(long, short):
    return RuleOutput(bool_series(long), bool_series(short), {}, "compra", "venta")


# --- combinación de reglas ---------------------------------------------------

def test_combine_requires_trigger_and_all_confirmations_by_default():
    outputs = {
        "trig": output([0, 1, 1, 0], [0, 0, 0, 0]),
        "a": output([1, 1, 0, 1], [0, 0, 0, 0]),
        "b": output([1, 1, 1, 1], [0, 0, 0, 0]),
    }
    long, short = combine(outputs, "trig", None, pd.RangeIndex(4))
    assert long.tolist() == [False, True, False, False]  # vela 2 falla la confirmación "a"
    assert not short.any()


def test_combine_min_confirmations_allows_partial_agreement():
    outputs = {
        "trig": output([0, 1, 1, 1], [0, 0, 0, 0]),
        "a": output([0, 1, 0, 0], [0, 0, 0, 0]),
        "b": output([0, 0, 1, 0], [0, 0, 0, 0]),
    }
    long, _ = combine(outputs, "trig", 1, pd.RangeIndex(4))
    assert long.tolist() == [False, True, True, False]
    zero, _ = combine(outputs, "trig", 0, pd.RangeIndex(4))
    assert zero.tolist() == [False, True, True, True]


def test_combine_discards_contradictory_signals_and_works_without_confirmations():
    outputs = {"trig": output([0, 1, 1], [0, 1, 0])}
    long, short = combine(outputs, "trig", None, pd.RangeIndex(3))
    assert long.tolist() == [False, False, True]
    assert not short.any()


# --- validación de la configuración -----------------------------------------

def test_valid_config_loads():
    config = StrategyConfig.model_validate(rules_config())
    assert [rule.rule_name for rule in config.rules] == ["ema_cross", "atr"]


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"trigger": "no_existe"}, "no es ninguna de las reglas"),
        ({"trigger": "atr"}, "debe ser un evento"),
        ({"min_confirmations": 5}, "supera las reglas de confirmación"),
        ({"stop_loss": {"mode": "fixed_pips"}}, "exige el parámetro pips"),
        ({"take_profit": {"mode": "rr", "reward_rsik": 2}}, "reward_rsik"),
        ({"rules": []}, "at least 1"),
        ({"descripcion": "x"}, "descripcion"),
    ],
)
def test_invalid_config_is_rejected(overrides, message):
    with pytest.raises(ValidationError, match=message):
        StrategyConfig.model_validate(rules_config(**overrides))


def test_duplicate_rule_names_are_rejected():
    rules = [
        {"type": "ma_cross", "fast": 3, "slow": 5},
        {"type": "ma_cross", "fast": 10, "slow": 20, "mode": "state"},
    ]
    with pytest.raises(ValidationError, match="repetidos"):
        StrategyConfig.model_validate({"trigger": "ma_cross", "rules": rules})
    rules[1]["name"] = "tendencia"
    StrategyConfig.model_validate({"trigger": "ma_cross", "rules": rules})  # con name, es válida


def test_typo_inside_a_rule_is_not_silently_ignored():
    rules = [{"type": "ma_cross", "name": "x", "fats": 3, "slow": 5}]
    with pytest.raises(ValidationError, match="fats"):
        StrategyConfig.model_validate({"trigger": "x", "rules": rules})


# --- evaluación -------------------------------------------------------------

def test_strategy_signal_on_crafted_cross_and_reasons():
    strategy = Strategy("demo", StrategyConfig.model_validate(rules_config()))
    df = make_candles(downtrend_then_jump())
    evaluation = strategy.evaluate(df, CTX)
    signal = strategy.signal_at(evaluation, df, len(df) - 1)
    assert signal is not None and signal.direction == "long"
    assert signal.index == len(df) - 1
    assert signal.reasons[0] == "EMA3 cruza sobre EMA5"  # el disparador va primero
    assert len(signal.reasons) == 2
    assert strategy.signal_at(evaluation, df, len(df) - 2) is None
    assert strategy.signal_at(evaluation, df, 999) is None


def test_failed_confirmation_blocks_the_signal():
    config = rules_config()
    config["rules"][1] = {"type": "atr_filter", "name": "atr", "min_pips": 500}  # imposible
    strategy = Strategy("demo", StrategyConfig.model_validate(config))
    df = make_candles(downtrend_then_jump())
    evaluation = strategy.evaluate(df, CTX)
    assert not evaluation.long.any()
    states = strategy.rule_states(evaluation, len(df) - 1)
    assert states["ema_cross"].long and states["ema_cross"].is_trigger
    assert not states["atr"].long and not states["atr"].is_trigger


def test_warmup_depends_on_rules_and_stops():
    base = Strategy("a", StrategyConfig.model_validate(rules_config()))
    assert base.warmup_bars == 3 * max(5 + 2, 14 + 1, 14 + 2)
    slow = rules_config()
    slow["rules"][0] = {"type": "ma_cross", "name": "ema_cross", "fast": 50, "slow": 200}
    assert Strategy("b", StrategyConfig.model_validate(slow)).warmup_bars == 3 * 202
    swing = rules_config(stop_loss={"mode": "swing", "swing_lookback": 300})
    assert Strategy("c", StrategyConfig.model_validate(swing)).warmup_bars == 3 * 302


def test_evaluation_is_causal_no_lookahead():
    """Las señales de las primeras k velas no cambian al añadir velas posteriores."""
    strategies = load_strategies(REPO)
    df = random_candles(900, seed=7)
    for name, strategy in strategies.items():
        full = strategy.evaluate(df, CTX)
        for cut in (400, 620, 777):
            part = strategy.evaluate(df.iloc[:cut].reset_index(drop=True), CTX)
            assert full.long.iloc[:cut].to_numpy().tolist() == part.long.to_numpy().tolist(), name
            assert full.short.iloc[:cut].to_numpy().tolist() == part.short.to_numpy().tolist(), name


# --- archivo strategies.yaml del proyecto ------------------------------------

def test_shipped_strategies_load_and_each_uses_three_indicators():
    strategies = load_strategies(REPO)
    assert {"ema_rsi_atr", "fib_pullback", "bb_breakout_macd"} <= set(strategies)
    for name, strategy in strategies.items():
        assert len(strategy.config.rules) == 3, name
    # La estrategia aprobada: cruce EMA 20/50, RSI 14, ATR; SL 1,5 ATR y TP 1:2.
    approved = strategies["ema_rsi_atr"].config
    cross = approved.rules[0]
    assert (cross.fast, cross.slow, cross.ma_type) == (20, 50, "ema")
    assert approved.stop_loss.mode == "atr" and approved.stop_loss.atr_multiplier == 1.5
    assert approved.take_profit.mode == "rr" and approved.take_profit.reward_risk == 2.0


def test_every_profile_points_to_existing_strategies():
    import yaml

    names = set(load_strategies(REPO))
    for profile in ("demo", "real"):
        data = yaml.safe_load((REPO / "config" / f"{profile}.yaml").read_text(encoding="utf-8"))
        assert set(data["strategies"]) <= names


def test_shipped_strategies_are_consistent_with_profile_min_reward_risk():
    import yaml

    for profile in ("demo", "real"):
        data = yaml.safe_load((REPO / "config" / f"{profile}.yaml").read_text(encoding="utf-8"))
        floor = data["risk"]["min_reward_risk"]
        for name, strategy in load_strategies(REPO).items():
            tp = strategy.config.take_profit
            if tp.mode == "rr":
                assert tp.reward_risk >= floor, f"{name} descartaría todas las señales en {profile}"


def write_strategies(tmp_path, text):
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "config" / "strategies.yaml").write_text(text, encoding="utf-8")
    return tmp_path


def test_load_strategies_errors(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_strategies(tmp_path)
    with pytest.raises(ValueError, match="sección 'strategies'"):
        load_strategies(write_strategies(tmp_path, "strategies: {}\n"))
    with pytest.raises(ValueError, match="claves desconocidas"):
        load_strategies(write_strategies(tmp_path, "otra: 1\n"))
    with pytest.raises(ValueError, match="claves desconocidas"):
        load_strategies(write_strategies(tmp_path, "strategies: {}\nextra: 1\n"))
    bad = "strategies:\n  mala:\n    trigger: x\n    rules: [{type: ma_cross, name: x, fast: 50, slow: 20}]\n"
    with pytest.raises(ValueError, match="Estrategia 'mala' no válida"):
        load_strategies(write_strategies(tmp_path, bad))
    bad_name = "strategies:\n  'mi estrategia':\n    trigger: x\n    rules: []\n"
    with pytest.raises(ValueError, match="Nombre de estrategia no válido"):
        load_strategies(write_strategies(tmp_path, bad_name))


def test_get_strategy_lists_available_names():
    strategies = load_strategies(REPO)
    assert get_strategy(strategies, "ema_rsi_atr").name == "ema_rsi_atr"
    with pytest.raises(ValueError, match="Disponibles"):
        get_strategy(strategies, "inventada")
