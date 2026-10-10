import pytest
from pydantic import ValidationError

from forexplat.config import Mode, load_profile

YAML = """mode: demo
symbols: [EURUSD]
timeframe: H1
bars: 200
risk:
  risk_per_trade: 0.01
  max_daily_loss: 0.03
  min_reward_risk: 2.0
  max_spread_pips: 2.0
"""
ENV = "MT5_LOGIN=123456\nMT5_PASSWORD=secreto\nMT5_SERVER=Broker-Demo\n"


def make_root(tmp_path, yaml_text=YAML, env_text=ENV, name="demo"):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / f"{name}.yaml").write_text(yaml_text, encoding="utf-8")
    if env_text is not None:
        (tmp_path / f".env.{name}").write_text(env_text, encoding="utf-8")
    return tmp_path


def test_loads_valid_profile(tmp_path):
    profile = load_profile("demo", root=make_root(tmp_path))
    assert profile.mode is Mode.DEMO
    assert profile.symbols == ["EURUSD"]
    assert profile.mt5.login == 123456
    assert profile.risk.risk_per_trade == 0.01


def test_password_is_not_exposed_in_repr(tmp_path):
    profile = load_profile("demo", root=make_root(tmp_path))
    assert "secreto" not in repr(profile)
    assert profile.mt5.password.get_secret_value() == "secreto"


def test_symbol_case_is_preserved(tmp_path):
    root = make_root(tmp_path, yaml_text=YAML.replace("[EURUSD]", "[EURUSD.m]"))
    assert load_profile("demo", root=root).symbols == ["EURUSD.m"]


def test_missing_env_file_raises(tmp_path):
    root = make_root(tmp_path, env_text=None)
    with pytest.raises(FileNotFoundError, match=".env.demo"):
        load_profile("demo", root=root)


def test_mode_must_match_profile_name(tmp_path):
    root = make_root(tmp_path, yaml_text=YAML.replace("mode: demo", "mode: real"))
    with pytest.raises(ValueError, match="mode='real'"):
        load_profile("demo", root=root)


def test_risk_out_of_range_is_rejected(tmp_path):
    root = make_root(tmp_path, yaml_text=YAML.replace("0.01", "0.5", 1))
    with pytest.raises(ValidationError):
        load_profile("demo", root=root)


def test_invalid_timeframe_is_rejected(tmp_path):
    root = make_root(tmp_path, yaml_text=YAML.replace("H1", "H7"))
    with pytest.raises(ValidationError):
        load_profile("demo", root=root)


def test_unknown_profile_name(tmp_path):
    with pytest.raises(ValueError, match="Perfil desconocido"):
        load_profile("staging", root=tmp_path)


def test_missing_credentials_are_rejected(tmp_path):
    root = make_root(tmp_path, env_text="MT5_SERVER=Broker-Demo\n")
    with pytest.raises(ValidationError):
        load_profile("demo", root=root)
