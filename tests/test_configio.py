import shutil
from pathlib import Path

import pytest

from forexplat.strategy import load_strategies
from forexplat.config import load_profile
from forexplat.ui import configio
from forexplat.ui.configio import ConfigError

REPO = Path(__file__).resolve().parents[1]


def make_root(tmp_path):
    (tmp_path / "config").mkdir(parents=True)
    for name in ("demo.yaml", "real.yaml", "strategies.yaml"):
        shutil.copy(REPO / "config" / name, tmp_path / "config" / name)
    return tmp_path


def backups(root):
    folder = root / "config" / "backups"
    return sorted(folder.iterdir()) if folder.exists() else []


# --- estrategias --------------------------------------------------------------

def test_saving_unchanged_strategies_keeps_them_identical_and_keeps_the_header(tmp_path):
    root = make_root(tmp_path)
    before = {n: s.config.model_dump() for n, s in load_strategies(root).items()}
    original_header = (root / "config" / "strategies.yaml").read_text(encoding="utf-8").split("strategies:")[0]
    for name, data in configio.read_strategy_dicts(root).items():
        configio.save_strategy(root, name, data, confirm_real="REAL")
    after = {n: s.config.model_dump() for n, s in load_strategies(root).items()}
    assert after == before
    text = (root / "config" / "strategies.yaml").read_text(encoding="utf-8")
    assert text.startswith(original_header.rstrip())
    assert len(backups(root)) == 3


def test_edit_a_parameter_persists_and_creates_a_backup(tmp_path):
    root = make_root(tmp_path)
    data = configio.read_strategy_dicts(root)["bb_breakout_macd"]
    data["stop_loss"] = {**data["stop_loss"], "atr_multiplier": 2.5}
    data["rules"][1] = {**data["rules"][1], "fast": 8, "slow": 21}
    backup = configio.save_strategy(root, "bb_breakout_macd", data)
    assert backup is not None and backup.exists()
    saved = load_strategies(root)["bb_breakout_macd"].config
    assert saved.stop_loss.atr_multiplier == 2.5 and (saved.rules[1].fast, saved.rules[1].slow) == (8, 21)
    assert "ema_rsi_atr" in load_strategies(root)  # las demás siguen


def test_invalid_strategy_is_rejected_and_file_is_untouched(tmp_path):
    root = make_root(tmp_path)
    path = root / "config" / "strategies.yaml"
    before = path.read_text(encoding="utf-8")
    data = configio.read_strategy_dicts(root)["ema_rsi_atr"]
    for change, message in (
        ({"trigger": "no_existe"}, "no es ninguna de las reglas"),
        ({"stop_loss": {"mode": "fixed_pips"}}, "pips"),
        ({"rules": []}, "rules"),
    ):
        with pytest.raises(ConfigError, match=message):
            configio.save_strategy(root, "ema_rsi_atr", {**data, **change})
    with pytest.raises(ConfigError, match="Nombre no válido"):
        configio.save_strategy(root, "mi estrategia", data)
    assert path.read_text(encoding="utf-8") == before and backups(root) == []


def test_strategy_used_in_real_requires_typed_confirmation(tmp_path):
    root = make_root(tmp_path)
    real = root / "config" / "real.yaml"
    real.write_text(real.read_text(encoding="utf-8").replace("[ema_rsi_atr]", "[bb_breakout_macd]"), encoding="utf-8")
    assert "real" in configio.profiles_using(root, "bb_breakout_macd")
    data = configio.read_strategy_dicts(root)["bb_breakout_macd"]
    with pytest.raises(ConfigError, match="REAL"):
        configio.save_strategy(root, "bb_breakout_macd", data)
    with pytest.raises(ConfigError, match="REAL"):
        configio.save_strategy(root, "bb_breakout_macd", data, confirm_real="real")
    configio.save_strategy(root, "bb_breakout_macd", data, confirm_real="REAL")


def test_duplicate_strategy(tmp_path):
    root = make_root(tmp_path)
    configio.duplicate_strategy(root, "ema_rsi_atr", "ema_rsi_atr_v2")
    copy = load_strategies(root)["ema_rsi_atr_v2"]
    assert copy.config.description == "Copia de ema_rsi_atr"
    assert copy.config.rules == load_strategies(root)["ema_rsi_atr"].config.rules
    with pytest.raises(ConfigError, match="Ya existe"):
        configio.duplicate_strategy(root, "ema_rsi_atr", "ema_rsi_atr_v2")
    with pytest.raises(ConfigError, match="No existe"):
        configio.duplicate_strategy(root, "x", "y")


def test_failed_verification_restores_the_previous_file(tmp_path):
    path = tmp_path / "a.yaml"
    path.write_text("original", encoding="utf-8")

    def verify():
        raise ValueError("no carga")

    with pytest.raises(ConfigError, match="se restauró"):
        configio._guarded_write(path, "roto", verify)
    assert path.read_text(encoding="utf-8") == "original"
    new = tmp_path / "nuevo.yaml"
    with pytest.raises(ConfigError):
        configio._guarded_write(new, "roto", verify)
    assert not new.exists()


def test_description_with_special_characters_roundtrips(tmp_path):
    root = make_root(tmp_path)
    data = configio.read_strategy_dicts(root)["ema_rsi_atr"]
    data["description"] = "Cruce: EMA 20/50 # prueba 'comillas' y ñ"
    configio.save_strategy(root, "ema_rsi_atr", data, confirm_real="REAL")
    assert load_strategies(root)["ema_rsi_atr"].config.description == data["description"]


# --- perfiles -------------------------------------------------------------------

def test_save_profile_updates_values_and_keeps_it_loadable(tmp_path):
    root = make_root(tmp_path)
    backup = configio.save_profile(root, "demo", {
        "symbols": ["EURUSD", "GBPUSD"], "timeframe": "H4", "strategies": ["bb_breakout_macd"],
        "risk": {"risk_per_trade": 0.005}, "backtest": {"spread_pips": 0.5},
    })
    assert backup is not None
    profile = load_profile("demo", root, require_credentials=False)
    assert profile.symbols == ["EURUSD", "GBPUSD"] and profile.timeframe == "H4"
    assert profile.strategies == ["bb_breakout_macd"] and profile.risk.risk_per_trade == 0.005
    assert profile.risk.max_daily_loss == 0.03 and profile.backtest.spread_pips == 0.5
    text = (root / "config" / "demo.yaml").read_text(encoding="utf-8")
    assert text.startswith("# Perfil DEMO") and "mode: demo" in text and "# fracción del saldo" in text


def test_save_profile_validations(tmp_path):
    root = make_root(tmp_path)
    before = (root / "config" / "demo.yaml").read_text(encoding="utf-8")
    with pytest.raises(ConfigError, match="risk_per_trade"):
        configio.save_profile(root, "demo", {"risk": {"risk_per_trade": 0.5}})
    with pytest.raises(ConfigError, match="timeframe"):
        configio.save_profile(root, "demo", {"timeframe": "H7"})
    with pytest.raises(ConfigError, match="symbols"):
        configio.save_profile(root, "demo", {"symbols": []})
    with pytest.raises(ConfigError, match="no existen"):
        configio.save_profile(root, "demo", {"strategies": ["fantasma"]})
    with pytest.raises(ConfigError, match="descartaría todas"):
        configio.save_profile(root, "demo", {"risk": {"min_reward_risk": 3.0}})  # las estrategias usan 1:2
    with pytest.raises(ConfigError, match="desconocido"):
        configio.save_profile(root, "staging", {})
    assert (root / "config" / "demo.yaml").read_text(encoding="utf-8") == before


def test_real_profile_requires_typed_confirmation(tmp_path):
    root = make_root(tmp_path)
    with pytest.raises(ConfigError, match="REAL"):
        configio.save_profile(root, "real", {"risk": {"risk_per_trade": 0.002}})
    configio.save_profile(root, "real", {"risk": {"risk_per_trade": 0.002}}, confirm_real="REAL")
    assert load_profile("real", root, require_credentials=False).risk.risk_per_trade == 0.002
    assert load_profile("real", root, require_credentials=False).mode.value == "real"


def test_validation_error_formatting():
    from pydantic import ValidationError
    from forexplat.strategy import StrategyConfig

    try:
        StrategyConfig.model_validate({"trigger": "x", "rules": [{"type": "ma_cross", "fats": 1}]})
    except ValidationError as error:
        text = configio.format_validation_error(error)
    assert "fats" in text and "Value error" not in text
