import pytest

from forexplat import journal
from forexplat.history import match_alert_deals
import pandas as pd


def test_upsert_creates_then_updates_only_given_columns(tmp_path):
    path = tmp_path / "j" / "d.csv"
    journal.upsert(path, {"alert_id": "a1", "symbol": "EURUSD", "decision": "tomada", "entry": 1.1})
    journal.upsert(path, {"alert_id": "a2", "symbol": "GBPUSD"})
    row = journal.upsert(path, {"alert_id": "a1", "note": "entré tarde", "real_lots": 0.5})
    rows = journal.load(path)
    assert [r["alert_id"] for r in rows] == ["a1", "a2"]
    assert row["decision"] == "tomada" and row["symbol"] == "EURUSD" and row["note"] == "entré tarde"
    assert rows[0]["entry"] == "1.1" and rows[0]["real_lots"] == "0.5" and rows[1]["decision"] == ""
    assert journal.decision_of(path, "a1") == "tomada" and journal.decision_of(path, "a2") is None
    assert journal.decision_of(path, "nope") is None


def test_decision_can_be_cleared_and_invalid_values_are_rejected(tmp_path):
    path = tmp_path / "d.csv"
    journal.upsert(path, {"alert_id": "a1", "decision": "tomada"})
    journal.upsert(path, {"alert_id": "a1", "decision": ""})
    assert journal.decision_of(path, "a1") is None
    with pytest.raises(ValueError, match="no válida"):
        journal.upsert(path, {"alert_id": "a1", "decision": "quizás"})
    with pytest.raises(ValueError, match="alert_id"):
        journal.upsert(path, {"decision": "tomada"})


def test_old_format_file_is_read_and_migrated_on_write(tmp_path):
    path = tmp_path / "d.csv"
    path.write_text(
        "decided_at,alert_id,strategy,symbol,timeframe,direction,candle_time,entry,sl,tp,lots,decision\n"
        "x,a1,s,EURUSD,H4,long,2026-01-01,1.1,1.09,1.12,0.5,tomada\n", encoding="utf-8")
    assert journal.load(path)[0]["note"] == ""
    journal.upsert(path, {"alert_id": "a1", "note": "ok"})
    assert path.read_text(encoding="utf-8").splitlines()[0] == ",".join(journal.COLUMNS)
    assert journal.load(path)[0]["decision"] == "tomada"


def test_missing_file_is_empty(tmp_path):
    assert journal.load(tmp_path / "no.csv") == []


# --- emparejar con el historial de MetaTrader 5 -----------------------------------

def deals(rows):
    frame = pd.DataFrame(rows, columns=["time", "position_id", "symbol", "type", "entry", "volume", "price", "profit", "commission", "swap"])
    frame["time"] = pd.to_datetime(frame["time"], utc=True)
    return frame


ALERT = {"candle_time": "2026-01-05 08:00:00+00:00", "direction": "long", "symbol": "EURUSD"}


def test_matches_entry_and_closed_result():
    frame = deals([
        ["2026-01-05 12:05", 11, "EURUSD", 0, 0, 0.5, 1.1002, 0, -1.5, 0],
        ["2026-01-06 09:00", 11, "EURUSD", 1, 1, 0.5, 1.1042, 200.0, -1.5, -0.3],
    ])
    match = match_alert_deals(ALERT, frame)
    assert match == {"real_entry": 1.1002, "real_lots": 0.5, "real_result": pytest.approx(196.7), "ticket": 11, "closed": True}


def test_open_position_has_no_result_and_partial_close_is_open():
    frame = deals([["2026-01-05 12:05", 11, "EURUSD", 0, 0, 1.0, 1.1, 0, -3, 0]])
    assert match_alert_deals(ALERT, frame)["real_result"] is None
    partial = deals([
        ["2026-01-05 12:05", 11, "EURUSD", 0, 0, 1.0, 1.1, 0, -3, 0],
        ["2026-01-05 15:00", 11, "EURUSD", 1, 1, 0.4, 1.102, 80, -1, 0],
    ])
    assert match_alert_deals(ALERT, partial)["closed"] is False


def test_does_not_match_wrong_symbol_direction_time_or_taken_position():
    base = ["2026-01-05 12:05", 11, "EURUSD", 0, 0, 0.5, 1.1, 0, 0, 0]
    assert match_alert_deals(ALERT, deals([base[:2] + ["GBPUSD"] + base[3:]])) is None
    assert match_alert_deals(ALERT, deals([base[:3] + [1] + base[4:]])) is None            # venta
    assert match_alert_deals(ALERT, deals([["2026-01-05 07:00"] + base[1:]])) is None       # antes de la señal
    assert match_alert_deals(ALERT, deals([["2026-02-05 12:00"] + base[1:]])) is None       # una semana después
    assert match_alert_deals(ALERT, deals([base]), exclude_positions=[11]) is None
    assert match_alert_deals(ALERT, deals([])) is None and match_alert_deals(ALERT, None) is None


def test_scaled_entry_uses_weighted_average_price():
    frame = deals([
        ["2026-01-05 12:05", 11, "EURUSD", 0, 0, 0.3, 1.1000, 0, 0, 0],
        ["2026-01-05 13:00", 11, "EURUSD", 0, 0, 0.7, 1.1010, 0, 0, 0],
    ])
    match = match_alert_deals(ALERT, frame)
    assert match["real_lots"] == pytest.approx(1.0) and match["real_entry"] == pytest.approx(1.1007)
