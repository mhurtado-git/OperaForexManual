import csv
import json

import pandas as pd
import pytest

from forexplat.review import bars_needed, evaluate_alert, format_review, load_alerts, summarize
from forexplat.watch import JOURNAL_COLUMNS
from helpers import make_candles

SIGNAL = "2026-01-05 10:00:00+00:00"  # vela de la señal (índice 10 con make_candles)


def candles(**highs_lows):
    df = make_candles([1.1] * 30, wick=0.0002)
    for index, (high, low) in highs_lows.items():
        df.loc[int(index.lstrip("c")), ["high", "low"]] = [high, low]
    return df


def alert(direction="long", entry=1.1000, sl=1.0980, tp=1.1040, decision="tomada", **extra):
    return {"alert_id": "x", "strategy": "s", "symbol": "EURUSD", "timeframe": "H1", "direction": direction,
            "candle_time": SIGNAL, "entry": entry, "sl": sl, "tp": tp, "lots": 0.5, "decision": decision, **extra}


def test_long_take_profit_and_stop_loss():
    won = evaluate_alert(alert(), candles(c13=(1.1041, 1.0999)))
    assert won["status"] == "TP" and won["r"] == pytest.approx(2.0) and won["bars"] == 3
    lost = evaluate_alert(alert(), candles(c12=(1.1002, 1.0979)))
    assert lost["status"] == "SL" and lost["r"] == pytest.approx(-1.0)


def test_only_candles_after_the_signal_count():
    # El máximo que toca el TP está en la propia vela de la señal (c10): no cuenta.
    result = evaluate_alert(alert(), candles(c10=(1.1050, 1.0999)))
    assert result["status"] == "abierta" and result["r"] == pytest.approx(0.0)


def test_both_levels_in_one_candle_counts_as_stop():
    assert evaluate_alert(alert(), candles(c12=(1.1041, 1.0979)))["status"] == "SL"


def test_short_uses_spread_on_the_ask():
    short = alert(direction="short", entry=1.1000, sl=1.1020, tp=1.0960)
    frame = candles(c12=(1.10195, 1.0999))
    assert evaluate_alert(short, frame, spread=0.0)["status"] == "abierta"
    assert evaluate_alert(short, frame, spread=0.0001)["status"] == "SL"


def test_pending_when_there_are_no_later_candles():
    frame = candles().iloc[:11]
    assert evaluate_alert(alert(), frame)["status"] == "pendiente"
    assert evaluate_alert(alert(entry=1.1, sl=1.1), candles())["status"] == "inválida"


def test_summary_and_report_text():
    rows = [
        {**alert(decision="tomada"), "status": "TP", "r": 2.0, "bars": 3},
        {**alert(decision="tomada"), "status": "SL", "r": -1.0, "bars": 2},
        {**alert(decision="descartada"), "status": "TP", "r": 2.0, "bars": 3},
        {**alert(decision=None), "status": "abierta", "r": 0.4, "bars": 5},
    ]
    summary = summarize(rows[:2])
    assert summary["closed"] == 2 and summary["win_rate"] == 0.5 and summary["total_r"] == pytest.approx(1.0)
    text = format_review(rows, "demo")
    assert "Tomadas: 2 alertas" in text and "+1.00 R en total" in text
    assert "Descartadas: 1 alertas" in text and "Sin decisión: 1 alertas" in text and "abiertas 1" in text
    assert "todavía no se puede juzgar" in text
    text.encode("cp850")


def test_empty_report():
    assert "Todavía no hay alertas" in format_review([], "demo")


def test_load_alerts_merges_state_and_journal(tmp_path):
    (tmp_path / "state").mkdir()
    (tmp_path / "journal").mkdir()
    base = {"strategy": "s", "symbol": "EURUSD", "timeframe": "H1", "direction": "long",
            "candle_time": SIGNAL, "entry": 1.1, "sl": 1.098, "tp": 1.104, "lots": 0.5, "message_id": 1}
    state = {"alerts": {"a1": {**base, "decision": None}, "a2": {**base, "candle_time": "2026-01-06 10:00:00+00:00", "decision": None}}}
    (tmp_path / "state" / "alerts_demo.json").write_text(json.dumps(state), encoding="utf-8")
    with (tmp_path / "journal" / "decisiones_demo.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=JOURNAL_COLUMNS)
        writer.writeheader()
        writer.writerow({"decided_at": "x", "alert_id": "a1", **{k: base[k] for k in (
            "strategy", "symbol", "timeframe", "direction", "candle_time", "entry", "sl", "tp", "lots")},
            "decision": "tomada"})
    alerts = load_alerts(tmp_path, "demo")
    assert [a["alert_id"] for a in alerts] == ["a1", "a2"]
    assert alerts[0]["decision"] == "tomada" and alerts[1]["decision"] is None
    assert load_alerts(tmp_path / "vacio", "demo") == []


def test_bars_needed_is_clamped():
    old = [alert(candle_time="2020-01-01 00:00:00+00:00")]
    assert bars_needed(old) == 5000
    recent = [alert(candle_time=str(pd.Timestamp.now(tz="UTC")))]
    assert bars_needed(recent) == 30
