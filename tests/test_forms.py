from forexplat.config import RiskConfig
from forexplat.risk import StopLossConfig, TakeProfitConfig
from forexplat.ui.configio import RULE_TYPES
from forexplat.ui.forms import fields_for


def by_name(model):
    return {spec.name: spec for spec in fields_for(model)}


def test_kinds_defaults_and_bounds_come_from_the_models():
    ma = by_name(RULE_TYPES["ma_cross"])
    assert (ma["fast"].kind, ma["fast"].default, ma["fast"].min) == ("int", 20, 2)
    assert ma["ma_type"].kind == "choice" and ma["ma_type"].options == ("ema", "sma")
    assert "type" not in ma and "name" not in ma
    rsi = by_name(RULE_TYPES["rsi"])
    assert rsi["oversold"].kind == "float" and (rsi["oversold"].min, rsi["oversold"].max) == (0, 100)
    sl = by_name(StopLossConfig)
    assert sl["pips"].kind == "opt_float" and sl["pips"].default is None and sl["pips"].exclusive_min
    assert sl["mode"].options == ("atr", "fixed_pips", "swing")
    risk = by_name(RiskConfig)
    assert risk["risk_per_trade"].max == 0.05 and risk["risk_per_trade"].default is None


def test_every_rule_and_config_model_yields_known_kinds():
    for model in (*RULE_TYPES.values(), StopLossConfig, TakeProfitConfig, RiskConfig):
        for spec in fields_for(model):
            assert spec.kind in {"int", "float", "choice", "opt_float", "opt_int", "text", "days"}, (model, spec.name)
            assert spec.label
