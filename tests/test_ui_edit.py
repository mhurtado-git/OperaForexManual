import csv

import pytest

import fake_streamlit
from fake_telegram import FakeTelegram
from fakes import FakeClient, client_class
from forexplat import journal
from forexplat.review import load_alerts
from forexplat.ui import configio, services
from forexplat.watch import State, handle_updates
from helpers import random_candles
from test_cli import ENV, make_root
from test_scanner import LONG_CANDLES

ALERT = {
    "alert_id": "abc123", "strategy": "ema_rsi_atr", "symbol": "EURUSD", "timeframe": "H4",
    "direction": "long", "candle_time": "2026-01-05 08:00:00+00:00", "entry": 1.1, "sl": 1.09, "tp": 1.12,
    "lots": 0.5,
}


def make(tmp_path):
    root = make_root(tmp_path)
    (root / ".env.demo").write_text(ENV + "TELEGRAM_BOT_TOKEN=1:X\nTELEGRAM_CHAT_ID=42\n", encoding="utf-8")
    return root


def render_tab(tab, root, profile="demo", **kwargs):
    st = fake_streamlit.install(**kwargs)
    try:
        from forexplat.ui import pages_edit

        getattr(pages_edit, tab)(profile, root)
    finally:
        fake_streamlit.uninstall()
    return st


# --- servicios del diario -------------------------------------------------------

def test_save_decision_writes_journal_and_review_reads_it(tmp_path):
    root = make(tmp_path)
    services.save_decision(root, "demo", ALERT, "tomada", " entré tarde ", "1,1", None, "12.5", "77")
    path = services.journal_path(root, "demo")
    row = journal.load(path)[0]
    assert row["decision"] == "tomada" and row["note"] == "entré tarde" and row["ticket"] == "77"
    assert row["symbol"] == "EURUSD" and row["real_lots"] == ""
    alerts = services.load_journal_alerts(root, "demo")
    assert alerts[0]["decision"] == "tomada" and alerts[0]["note"] == "entré tarde"
    services.save_decision(root, "demo", ALERT, None, "")
    assert journal.decision_of(path, "abc123") is None


def test_find_real_trade_uses_deals_and_skips_tickets_of_other_alerts(tmp_path):
    import pandas as pd

    root = make(tmp_path)
    deals = pd.DataFrame([
        {"time": pd.Timestamp("2026-01-05 09:00", tz="UTC"), "position_id": 5, "symbol": "EURUSD", "type": 0,
         "entry": 0, "volume": 0.5, "price": 1.1002, "profit": 0.0, "commission": 0.0, "swap": 0.0},
        {"time": pd.Timestamp("2026-01-05 12:00", tz="UTC"), "position_id": 5, "symbol": "EURUSD", "type": 1,
         "entry": 1, "volume": 0.5, "price": 1.1050, "profit": 24.0, "commission": -1.0, "swap": 0.0},
    ])
    client = FakeClient(random_candles(50))
    client.deals = deals
    found = services.find_real_trade("demo", root, ALERT, client_class(client))
    assert found["ticket"] == 5 and found["real_lots"] == 0.5 and found["real_result"] == 23.0
    other = {**ALERT, "alert_id": "other"}
    services.save_decision(root, "demo", other, "tomada", "", ticket=5)
    assert services.find_real_trade("demo", root, ALERT, client_class(client)) is None


def test_ui_decision_blocks_later_telegram_button(tmp_path):
    from forexplat.watch import alert_id, run_cycle
    from test_scanner import PROFILE, STRATEGY

    mt5, tg, state = FakeClient(LONG_CANDLES), FakeTelegram(), State(tmp_path / "s.json")
    path = tmp_path / "j" / "d.csv"
    run_cycle(mt5, tg, PROFILE, [STRATEGY], ["EURUSD"], state)
    identifier = alert_id("demo", "EURUSD", "H1", LONG_CANDLES["time"].iloc[-1])
    journal.upsert(path, {"alert_id": identifier, "decision": "descartada"})
    tg.press(f"tomada|{identifier}")
    handle_updates(tg, state, path)
    assert journal.decision_of(path, identifier) == "descartada"
    assert "Ya la registraste" in tg.answers[-1][1]
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    assert len(rows) == 1


# --- pestañas ---------------------------------------------------------------------

def test_strategies_tab_saves_edited_strategy(tmp_path):
    root = make(tmp_path)
    st = render_tab("strategies_tab", root, clicked={"0:bb_breakout_macd:save"},
                    values={"st_name": "bb_breakout_macd"})
    assert any("Estrategia guardada" in t for t in st.texts("success"))
    assert list((root / "config" / "backups").glob("strategies_*"))
    configio.validate_strategy(configio.read_strategy_dicts(root)["bb_breakout_macd"])


def test_strategies_tab_real_strategy_needs_confirmation(tmp_path):
    root = make(tmp_path)
    before = (root / "config" / "strategies.yaml").read_text(encoding="utf-8")
    st = render_tab("strategies_tab", root, clicked={"0:ema_rsi_atr:save"}, values={"st_name": "ema_rsi_atr"})
    assert any("REAL" in t for t in st.texts("error")) and not st.texts("success")
    assert (root / "config" / "strategies.yaml").read_text(encoding="utf-8") == before
    st = render_tab("strategies_tab", root, clicked={"0:ema_rsi_atr:save"},
                    values={"st_name": "ema_rsi_atr", "0:ema_rsi_atr:confirm": "REAL"})
    assert any("Estrategia guardada" in t for t in st.texts("success"))


def test_strategies_tab_duplicates(tmp_path):
    root = make(tmp_path)
    render_tab("strategies_tab", root, clicked={"0:bb_breakout_macd:dup"},
               values={"st_name": "bb_breakout_macd", "0:bb_breakout_macd:copyname": "copia_bb"})
    assert "copia_bb" in configio.read_strategy_dicts(root)


def test_profile_tab_saves_and_real_needs_confirmation(tmp_path):
    root = make(tmp_path)
    st = render_tab("profile_tab", root, clicked={"pf:demo:save"},
                    values={"pf:demo:symbols": "EURUSD, GBPUSD", "pf:demo:tf": "H4"})
    assert any("Perfil guardado" in t for t in st.texts("success"))
    assert configio.read_profile_dict(root, "demo")["symbols"] == ["EURUSD", "GBPUSD"]
    before = (root / "config" / "real.yaml").read_text(encoding="utf-8")
    st = render_tab("profile_tab", root, profile="real", clicked={"pf:real:save"})
    assert any("REAL" in t for t in st.texts("error"))
    assert (root / "config" / "real.yaml").read_text(encoding="utf-8") == before
    st = render_tab("profile_tab", root, profile="real", clicked={"pf:real:save"},
                    values={"pf:real:confirm": "REAL"})
    assert any("Perfil guardado" in t for t in st.texts("success"))


def test_journal_tab_empty_and_save(tmp_path):
    root = make(tmp_path)
    st = render_tab("journal_tab", root)
    assert any("Todavía no hay alertas" in t for t in st.texts("info"))
    state = State(root / "state" / "alerts_demo.json")
    state.alerts["abc123"] = {**ALERT, "message_id": 1}
    state.save()
    prefix = "0:abc123"
    st = render_tab("journal_tab", root, clicked={f"{prefix}:save"},
                    values={f"{prefix}:dec": "tomada", f"{prefix}:note": "ok", f"{prefix}:rl": "0,5"})
    assert any("Guardado" in t for t in st.texts("success"))
    row = journal.load(services.journal_path(root, "demo"))[0]
    assert row["decision"] == "tomada" and row["real_lots"] == "0.5" and row["note"] == "ok"
    st = render_tab("journal_tab", root, clicked={f"{prefix}:save"}, values={f"{prefix}:re": "abc"})
    assert any("deben ser números" in t for t in st.texts("error"))


def test_journal_find_with_mt5_closed_is_friendly(tmp_path):
    from forexplat import cli

    root = make(tmp_path)
    state = State(root / "state" / "alerts_demo.json")
    state.alerts["abc123"] = {**ALERT, "message_id": 1}
    state.save()
    original = cli._mt5_client_class

    def broken():
        raise RuntimeError("MetaTrader5 no disponible")

    cli._mt5_client_class = broken
    try:
        st = render_tab("journal_tab", root, clicked={"0:abc123:find"})
    finally:
        cli._mt5_client_class = original
    assert any("MetaTrader 5" in t for t in st.texts("warning") + st.texts("error"))
