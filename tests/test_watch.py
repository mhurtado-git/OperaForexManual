import csv

from fake_telegram import FakeTelegram
from fakes import FakeClient
from forexplat.watch import State, alert_id, handle_updates, run_cycle
from test_scanner import LONG_CANDLES, PROFILE, STRATEGY
from helpers import make_candles


def setup(tmp_path, candles=LONG_CANDLES, **client_kwargs):
    return (
        FakeClient(candles, **client_kwargs), FakeTelegram(), State(tmp_path / "state" / "a.json"),
        tmp_path / "journal" / "d.csv",
    )


def cycle(mt5, tg, state):
    return run_cycle(mt5, tg, PROFILE, [STRATEGY], ["EURUSD"], state)


def test_signal_sends_one_alert_with_two_buttons(tmp_path):
    mt5, tg, state, _ = setup(tmp_path)
    events = cycle(mt5, tg, state)
    assert len(tg.sent) == 1 and "alerta long enviada" in events[0]
    text, buttons = tg.sent[0]
    assert "ALERTA LARGO (compra)" in text and "Ejecútala tú en MetaTrader 5" in text
    assert [data.split("|")[0] for _, data in buttons] == ["tomada", "descartada"]
    assert all(len(data.encode()) <= 64 for _, data in buttons)  # límite de Telegram


def test_same_candle_is_not_alerted_twice_even_after_restart(tmp_path):
    mt5, tg, state, _ = setup(tmp_path)
    cycle(mt5, tg, state)
    cycle(mt5, tg, state)
    assert len(tg.sent) == 1
    again = State(tmp_path / "state" / "a.json")  # reinicio del programa
    cycle(mt5, tg, again)
    assert len(tg.sent) == 1


def test_no_signal_sends_nothing(tmp_path):
    flat = make_candles([1.1 - 0.0001 * i for i in range(61)])
    mt5, tg, state, _ = setup(tmp_path, flat)
    assert cycle(mt5, tg, state) == [] and tg.sent == []


def test_rejected_signal_is_not_sent_but_reported_once(tmp_path):
    mt5, tg, state, _ = setup(tmp_path, summary={"spread_points": 30})
    events = cycle(mt5, tg, state)
    assert tg.sent == [] and "descartada" in events[0] and "Spread" in events[0]
    assert cycle(mt5, tg, state) == []


def test_failed_send_is_retried_next_cycle(tmp_path):
    mt5, tg, state, _ = setup(tmp_path)
    tg.fail_send = True
    try:
        cycle(mt5, tg, state)
    except RuntimeError:
        pass
    assert tg.sent == []
    tg.fail_send = False
    cycle(mt5, tg, state)
    assert len(tg.sent) == 1


def test_button_press_records_decision_in_journal(tmp_path):
    mt5, tg, state, journal = setup(tmp_path)
    cycle(mt5, tg, state)
    identifier = alert_id("demo", "EURUSD", "H1", LONG_CANDLES["time"].iloc[-1])
    tg.press(f"tomada|{identifier}")
    events = handle_updates(tg, state, journal)
    assert events == ["EURUSD demo: tomada"]
    rows = list(csv.DictReader(journal.open(encoding="utf-8")))
    assert len(rows) == 1 and rows[0]["decision"] == "tomada" and rows[0]["symbol"] == "EURUSD"
    assert rows[0]["direction"] == "long" and float(rows[0]["lots"]) == 0.5
    assert tg.answers[-1][1] == "Registrado: tomada" and tg.cleared == [101]
    assert state.offset == 2


def test_decision_cannot_be_changed_or_repeated(tmp_path):
    mt5, tg, state, journal = setup(tmp_path)
    cycle(mt5, tg, state)
    identifier = alert_id("demo", "EURUSD", "H1", LONG_CANDLES["time"].iloc[-1])
    tg.press(f"descartada|{identifier}", update_id=1)
    handle_updates(tg, state, journal)
    tg.press(f"tomada|{identifier}", update_id=2)
    handle_updates(tg, state, journal)
    rows = list(csv.DictReader(journal.open(encoding="utf-8")))
    assert [r["decision"] for r in rows] == ["descartada"]
    assert "Ya la registraste" in tg.answers[-1][1]


def test_presses_from_other_users_and_unknown_alerts_are_ignored(tmp_path):
    mt5, tg, state, journal = setup(tmp_path)
    cycle(mt5, tg, state)
    identifier = alert_id("demo", "EURUSD", "H1", LONG_CANDLES["time"].iloc[-1])
    tg.press(f"tomada|{identifier}", user_id=999, update_id=1)
    tg.press("tomada|noexiste", update_id=2)
    tg.press("basura", update_id=3)
    events = handle_updates(tg, state, journal)
    assert "no viene del chat autorizado" in events[0]
    assert not journal.exists()
    assert state.alerts[identifier]["decision"] is None


def test_state_survives_corrupt_file(tmp_path):
    path = tmp_path / "state" / "a.json"
    path.parent.mkdir()
    path.write_text("{no es json", encoding="utf-8")
    assert State(path).seen == []
