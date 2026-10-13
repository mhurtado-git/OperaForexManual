import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from fakes import FakeClient
from forexplat.config import Profile, RiskConfig
from forexplat.rules import Context, SessionFilterRule
from forexplat.scanner import scan
from forexplat.strategy import Strategy, StrategyConfig, combine
from forexplat.timeutil import BrokerTime, verify_weekend_close
from helpers import downtrend_then_jump, make_candles

UTC_CTX = Context(0.0001, times_are_utc=True)
RISK = RiskConfig(risk_per_trade=0.01, max_daily_loss=0.03, min_reward_risk=2.0, max_spread_pips=2.0)


def rule(**kwargs):
    return SessionFilterRule(type="session_filter", **kwargs)


def allowed(opens, **kwargs):
    """Para cada hora de APERTURA de vela (UTC, M5) dice si la señal se permite."""
    times = pd.to_datetime(opens, utc=True)
    # Se añaden dos velas M5 antes para que el paso (5 min) se deduzca de los datos.
    frame = pd.DataFrame({"time": times})
    out = rule(**kwargs).evaluate(frame, UTC_CTX).long
    return list(out)


def m5_day(date):
    return pd.date_range(f"{date} 00:00", periods=288, freq="5min", tz="UTC")


def window(date, **kwargs):
    """(primera, última) hora de apertura UTC permitida ese día, o None si no hay ninguna."""
    times = m5_day(date)
    ok = rule(**kwargs).evaluate(pd.DataFrame({"time": times}), UTC_CTX).long.to_numpy()
    if not ok.any():
        return None
    return times[ok][0].strftime("%H:%M"), times[ok][-1].strftime("%H:%M")


# --- validación -----------------------------------------------------------------

def test_rule_defaults_and_validation():
    r = rule()
    assert (r.timezone, r.start, r.end, r.days, r.no_new_minutes) == ("America/New_York", "08:00", "17:00", [0, 1, 2, 3, 4], 30)
    assert r.is_gate and not r.is_event
    for bad, message in (
        ({"timezone": "Mars/Base"}, "Zona horaria"),
        ({"start": "17:00", "end": "08:00"}, "anterior"),
        ({"start": "8:00"}, "pattern|string"),
        ({"days": [7]}, "0 .lunes"),
        ({"no_new_minutes": 600}, "sin tiempo"),
        ({"horas": 3}, "Extra|extra"),
    ):
        with pytest.raises(ValidationError, match=message):
            rule(**bad)
    assert rule(days=[4, 0, 0]).days == [0, 4]


def test_rule_refuses_to_run_without_utc_times():
    frame = pd.DataFrame({"time": m5_day("2026-01-05")})
    with pytest.raises(ValueError, match="broker_time"):
        rule().evaluate(frame, Context(0.0001))


# --- horario de verano ----------------------------------------------------------

def test_winter_window_in_utc():
    # Invierno (EST, UTC-5): entrada válida de 08:00 a 16:25 de Nueva York = 13:00 a 21:25 UTC.
    # La señal es la vela cuya entrada (cierre) cae ahí: abre 12:55 UTC (entrada 13:00) ... 21:20 (entrada 21:25).
    assert window("2026-01-05") == ("12:55", "21:20")


def test_summer_window_in_utc():
    # Verano (EDT, UTC-4): 12:00 a 20:25 UTC de entrada.
    assert window("2026-07-06") == ("11:55", "20:20")


@pytest.mark.parametrize("monday,expected_start", [
    ("2026-03-09", "11:55"),   # lunes después del cambio de marzo: ya es verano
    ("2026-03-02", "12:55"),   # lunes antes: invierno
    ("2026-11-02", "12:55"),   # lunes después del cambio de noviembre: ya es invierno
    ("2026-10-26", "11:55"),   # lunes antes: verano
])
def test_both_dst_changes(monday, expected_start):
    assert window(monday)[0] == expected_start


def test_exact_edges_and_final_minutes():
    # Invierno: la vela que abre 12:50 entra a las 12:55 UTC = 07:55 NY: fuera. 12:55 entra a 13:00: dentro.
    times = ["2026-01-05 12:50", "2026-01-05 12:55", "2026-01-05 21:20", "2026-01-05 21:25", "2026-01-05 21:55"]
    assert allowed(times + ["2026-01-05 22:00"])[:5] == [False, True, True, False, False]
    # Sin minutos finales vedados, la última vela que abre 21:55 UTC entra a 22:00 = 17:00 NY (fuera) y 21:50 sí.
    assert allowed(["2026-01-05 21:50", "2026-01-05 21:55", "2026-01-05 22:00"], no_new_minutes=0)[:2] == [True, False]


def test_weekends_and_days():
    assert window("2026-01-03") is None and window("2026-01-04") is None  # sábado y domingo
    assert window("2026-01-09") is not None                              # viernes
    assert window("2026-01-05", days=[1, 2]) is None                     # lunes excluido
    assert window("2026-01-04", days=[6]) is not None                    # domingo si se pide


def test_custom_zone_and_window():
    # Londres 08:00-12:00 en invierno = UTC.
    assert window("2026-01-05", timezone="Europe/London", start="08:00", end="12:00", no_new_minutes=0) == ("07:55", "11:50")


# --- compuerta en la estrategia -------------------------------------------------

def test_gate_is_mandatory_even_with_min_confirmations_zero():
    from forexplat.rules import RuleOutput

    idx = pd.RangeIndex(3)
    true = pd.Series([True, True, True], index=idx)
    gate = pd.Series([True, False, True], index=idx)
    outputs = {
        "trig": RuleOutput(true, ~true, {}, "t", "t"),
        "sesion": RuleOutput(gate, gate, {}, "s", "s"),
    }
    long, short = combine(outputs, "trig", 0, idx, gates={"sesion"})
    assert list(long) == [True, False, True] and not short.any()
    # Sin ser compuerta contaría como un voto opcional y no bloquearía con min_confirmations=0.
    assert list(combine(outputs, "trig", 0, idx)[0]) == [True, True, True]


def strategy(**extra):
    return Strategy("s", StrategyConfig.model_validate({
        "trigger": "cruce",
        "rules": [
            {"type": "ma_cross", "name": "cruce", "fast": 3, "slow": 5},
            {"type": "atr_filter", "name": "atr", "min_pips": 0},
            {"type": "session_filter", "name": "sesion"},
        ],
        "stop_loss": {"mode": "fixed_pips", "pips": 20},
        "take_profit": {"mode": "rr", "reward_risk": 2.0},
        **extra,
    }))


def test_min_confirmations_does_not_count_the_gate():
    with pytest.raises(ValidationError, match="supera"):
        strategy(min_confirmations=2)
    assert strategy(min_confirmations=1).config.min_confirmations == 1


def candles_ending_at(last_open):
    n = len(downtrend_then_jump())
    start = pd.Timestamp(last_open, tz="UTC") - pd.Timedelta(hours=n - 1)
    return make_candles(downtrend_then_jump(), start=start)


def test_scan_requires_broker_time_and_blocks_outside_session():
    profile = Profile(mode="demo", symbols=["EURUSD"], timeframe="H1", bars=60, risk=RISK)
    inside = candles_ending_at("2026-01-07 14:00")   # entra a las 15:00 UTC = 10:00 NY
    with pytest.raises(ValueError, match="broker_time"):
        scan(FakeClient(inside), profile, strategy(), "EURUSD")
    profile = profile.model_copy(update={"broker_time": BrokerTime(utc_offset_hours=0)})
    result = scan(FakeClient(inside), profile, strategy(), "EURUSD")
    assert result.signal is not None and result.states["sesion"].long
    outside = candles_ending_at("2026-01-07 03:00")  # 22:00 NY del día anterior
    result = scan(FakeClient(outside), profile, strategy(), "EURUSD")
    assert result.signal is None and not result.states["sesion"].long


# --- hora del broker ------------------------------------------------------------

def test_broker_time_needs_exactly_one_form():
    for bad in ({}, {"utc_offset_hours": 2, "ny_offset_hours": 7}, {"utc_offset_hours": 20}, {"timezone": "Nada/Nada"}):
        with pytest.raises(ValidationError):
            BrokerTime(**bad)


def convert(broker_time, text):
    return str(broker_time.to_utc(pd.Series(pd.to_datetime([text], utc=True))).iloc[0])


def test_conversions_to_utc():
    assert convert(BrokerTime(utc_offset_hours=3), "2026-01-05 15:00") == "2026-01-05 12:00:00+00:00"
    ny7 = BrokerTime(ny_offset_hours=7)   # GMT+2 en invierno, GMT+3 en verano de EE. UU.
    assert convert(ny7, "2026-01-05 15:00") == "2026-01-05 13:00:00+00:00"
    assert convert(ny7, "2026-07-06 15:00") == "2026-07-06 12:00:00+00:00"
    athens = BrokerTime(timezone="Europe/Athens")
    assert convert(athens, "2026-01-15 15:00") == "2026-01-15 13:00:00+00:00"
    assert convert(athens, "2026-07-15 15:00") == "2026-07-15 12:00:00+00:00"


def fx_week_candles(shift_summer_hours=0.0, weeks=60):
    """Velas H1 de un año con cierre real del mercado (viernes 17:00 NY), vistas desde un servidor
    NY+7. `shift_summer_hours` simula un desfase mal configurado solo en verano."""
    utc = pd.date_range("2025-10-01", periods=weeks * 7 * 24, freq="h", tz="UTC")
    ny = utc.tz_convert("America/New_York")
    minutes = ny.dayofweek * 1440 + ny.hour * 60 + ny.minute
    open_ = (minutes >= 6 * 1440 + 17 * 60) | (minutes < 4 * 1440 + 17 * 60)   # dom 17:00 .. vie 17:00
    server = (ny[open_] + pd.Timedelta(hours=7)).tz_localize(None)
    server = pd.Series(server)
    summer = pd.Series(ny[open_].map(lambda t: bool(t.dst())))
    server = server + pd.to_timedelta(np.where(summer, shift_summer_hours, 0.0), unit="h")
    return server.dt.tz_localize("UTC")


def test_verify_weekend_close_detects_a_wrong_summer_offset():
    good = BrokerTime(ny_offset_hours=7).to_utc(fx_week_candles())
    report = verify_weekend_close(good, 60)
    assert report["weeks"] > 50 and report["ok"] == report["weeks"] and report["bad"] == []
    wrong = BrokerTime(ny_offset_hours=7).to_utc(fx_week_candles(shift_summer_hours=1))
    report = verify_weekend_close(wrong, 60)
    assert 0 < len(report["bad"]) < report["weeks"]
    # El mismo servidor tratado como UTC fijo +2 acierta en invierno y falla en verano.
    fixed = BrokerTime(utc_offset_hours=2).to_utc(fx_week_candles())
    bad_weeks = verify_weekend_close(fixed, 60)["bad"]
    assert bad_weeks


def test_profile_accepts_broker_time_from_yaml(tmp_path):
    from test_cli import make_root
    from forexplat.config import load_profile

    root = make_root(tmp_path)
    path = root / "config" / "demo.yaml"
    path.write_text(path.read_text(encoding="utf-8") + "\nbroker_time: {ny_offset_hours: 7}\n", encoding="utf-8")
    assert load_profile("demo", root, require_credentials=False).broker_time.ny_offset_hours == 7


# --- check-time -----------------------------------------------------------------

class TimeClient:
    def __init__(self, server_times, offset=2.0):
        self.server_times = server_times
        self.offset = offset

    def server_time_offset_hours(self, symbol):
        return self.offset, self.offset * 3600 + 12

    def get_candles(self, symbol, timeframe, bars):
        return pd.DataFrame({"time": self.server_times})


def test_check_time_finds_the_ny_offset_and_validates_the_profile():
    from forexplat.timecheck import check_time_report

    server = fx_week_candles(weeks=60)
    profile = Profile(mode="demo", symbols=["EURUSD"], risk=RISK)
    text = "\n".join(check_time_report(TimeClient(server), profile, "EURUSD"))
    assert "ny_offset_hours: 7" in text and "utc_offset_hours" not in text and "UTC+2" in text
    configured = profile.model_copy(update={"broker_time": BrokerTime(ny_offset_hours=7)})
    converted = BrokerTime(ny_offset_hours=7).to_utc(server)
    text = "\n".join(check_time_report(TimeClient(converted), configured, "EURUSD"))
    assert "bien configurada" in text
    wrong = profile.model_copy(update={"broker_time": BrokerTime(utc_offset_hours=2)})
    text = "\n".join(check_time_report(TimeClient(BrokerTime(utc_offset_hours=2).to_utc(server)), wrong, "EURUSD"))
    assert "NO cuadra" in text and "Semanas que no cuadran" in text


def test_cli_parses_check_time():
    from forexplat import cli

    args = cli._parse_args(["check-time", "--bars", "5000"])
    assert args.command == "check-time" and args.bars == 5000 and args.profile == "demo"


# --- estrategia limitada a ciertos símbolos -----------------------------------------

def test_strategy_symbols_filter_applies_in_watch_and_survives_saving(tmp_path):
    from forexplat.watch import State, run_cycle
    from fake_telegram import FakeTelegram
    from test_scanner import LONG_CANDLES, PROFILE

    only_gbp = Strategy("s", StrategyConfig.model_validate({
        "symbols": ["GBPUSD"], "trigger": "cruce",
        "rules": [{"type": "ma_cross", "name": "cruce", "fast": 3, "slow": 5}],
        "stop_loss": {"mode": "fixed_pips", "pips": 20}, "take_profit": {"mode": "rr", "reward_risk": 2.0},
    }))
    assert only_gbp.applies_to("GBPUSD") and not only_gbp.applies_to("EURUSD")
    tg = FakeTelegram()
    assert run_cycle(FakeClient(LONG_CANDLES), tg, PROFILE, [only_gbp], ["EURUSD"], State(tmp_path / "s.json")) == []
    assert tg.sent == []
    assert len(run_cycle(FakeClient(LONG_CANDLES), tg, PROFILE, [only_gbp], ["GBPUSD"], State(tmp_path / "s2.json"))) == 1

    from forexplat.ui import configio
    from test_cli import make_root
    root = make_root(tmp_path / "r")
    data = {**configio.read_strategy_dicts(root)["bb_breakout_macd"], "symbols": ["EURUSD"]}
    configio.save_strategy(root, "bb_breakout_macd", data)
    assert configio.read_strategy_dicts(root)["bb_breakout_macd"]["symbols"] == ["EURUSD"]
