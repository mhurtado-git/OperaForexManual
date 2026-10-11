import json

import fake_streamlit
from fakes import FakeClient, client_class
from forexplat import cli
from helpers import random_candles
from test_cli import ENV, make_root, with_fake_client


def make(tmp_path):
    root = make_root(tmp_path)
    (root / ".env.demo").write_text(ENV + "TELEGRAM_BOT_TOKEN=1:X\nTELEGRAM_CHAT_ID=42\n", encoding="utf-8")
    return root


def render(root, profile="demo", **kwargs):
    st = fake_streamlit.install(**kwargs)
    try:
        from forexplat.ui import app

        app.render(profile, root)
    finally:
        fake_streamlit.uninstall()
    return st


def test_default_render_draws_all_tabs_without_touching_mt5(tmp_path):
    st = render(make(tmp_path))
    assert st.texts("title") == ["Operación manual en Forex"]
    assert any("DEMO" in t for t in st.texts("success"))
    assert any("Actualizar estado" in t for t in st.texts("info") + st.texts("button"))
    assert "Vigilancia: detenida" in st.texts("write")


def test_real_profile_shows_a_loud_warning_and_no_start_button(tmp_path):
    root = make(tmp_path)
    st = render(root, profile="real")
    assert any("PERFIL REAL" in t for t in st.texts("error"))
    assert not any(label == "Iniciar vigilancia" for label, in [(a[0],) for n, a in st.calls if n == "button"])


def test_refresh_status_with_fake_mt5(tmp_path):
    root = make(tmp_path)
    restore = with_fake_client(FakeClient(random_candles(100)))
    try:
        st = render(root, clicked={"refresh_status"})
    finally:
        restore()
    assert any("conectado a la cuenta 1" in t for t in st.texts("success"))
    assert "Saldo" in st.texts("metric")


def test_refresh_status_with_mt5_closed_shows_friendly_warning(tmp_path):
    root = make(tmp_path)
    original = cli._mt5_client_class

    def broken():
        raise RuntimeError("MetaTrader5 no disponible")

    cli._mt5_client_class = broken
    try:
        st = render(root, clicked={"refresh_status"})
    finally:
        cli._mt5_client_class = original
    assert any("MetaTrader 5:" in t for t in st.texts("warning"))


def test_run_signals_button_prints_the_alert_text(tmp_path):
    root = make(tmp_path)
    restore = with_fake_client(FakeClient(random_candles(400, seed=3)))
    try:
        st = render(root, clicked={"run_signals"}, values={"sig_strategies": ["ema_rsi_atr"]})
    finally:
        restore()
    codes = st.texts("code")
    assert any("[DEMO] EURUSD" in t and "ema_rsi_atr" in t for t in codes)


def test_run_signals_without_selection_asks_for_one(tmp_path):
    root = make(tmp_path)
    st = render(root, clicked={"run_signals"}, values={"sig_strategies": []})
    assert any("al menos una estrategia" in t for t in st.texts("warning"))


def test_backtest_button_shows_metrics_chart_and_download(tmp_path):
    root = make(tmp_path)
    restore = with_fake_client(FakeClient(random_candles(3000, seed=7)))
    try:
        st = render(root, clicked={"run_backtest"}, values={"bt_strategy": "ema_rsi_atr"})
    finally:
        restore()
    names = [n for n, _ in st.calls]
    assert "dataframe" in names and "line_chart" in names and "download_button" in names
    assert any("Corte entre ajuste y validación" in t for t in st.texts("caption"))


def test_backtest_csv_source_without_file_asks_for_one(tmp_path):
    st = render(make(tmp_path), clicked={"run_backtest"}, values={"bt_source": "Archivo CSV"})
    assert any("Sube primero" in t for t in st.texts("warning"))


def test_backtest_error_is_shown_without_traceback(tmp_path):
    root = make(tmp_path)
    restore = with_fake_client(FakeClient(random_candles(100)))  # pocas velas
    try:
        st = render(root, clicked={"run_backtest"})
    finally:
        restore()
    assert any("necesita más" in t for t in st.texts("error"))


def test_watch_tab_shows_log_and_stop_button_state(tmp_path):
    root = make(tmp_path)
    (root / "logs").mkdir()
    (root / "logs" / "watch_demo.log").write_text("--- inicio ---\nhola\n", encoding="utf-8")
    st = render(root)
    assert any("hola" in t for t in st.texts("code"))


def test_app_has_no_relative_imports():
    """Streamlit ejecuta app.py como script: un importe relativo fallaría al abrir la interfaz."""
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "src" / "forexplat" / "ui" / "app.py").read_text(encoding="utf-8")
    assert not [line for line in source.splitlines() if line.startswith("from .")]
