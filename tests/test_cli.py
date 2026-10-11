import contextlib
import io
import shutil
from pathlib import Path

from fakes import FakeClient, client_class
from forexplat import cli
from forexplat.data import save_csv
from helpers import random_candles

REPO = Path(__file__).resolve().parents[1]
ENV = "MT5_LOGIN=123456\nMT5_PASSWORD=secreto\nMT5_SERVER=Broker-Demo\n"


def make_root(tmp_path, credentials=False):
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    for name in ("demo.yaml", "real.yaml", "strategies.yaml"):
        shutil.copy(REPO / "config" / name, tmp_path / "config" / name)
    if credentials:
        (tmp_path / ".env.demo").write_text(ENV, encoding="utf-8")
    return tmp_path


def run(argv, root):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(argv, root=root)
    return code, out.getvalue(), err.getvalue()


def with_fake_client(client):
    """Sustituye la conexión con MT5 por el cliente falso; devuelve una función para deshacerlo."""
    original = cli._mt5_client_class
    cli._mt5_client_class = lambda: client_class(client)
    return lambda: setattr(cli, "_mt5_client_class", original)


def candles_csv(tmp_path, n=1500):
    return save_csv(random_candles(n, seed=7), tmp_path / "velas.csv")


# --- backtest con CSV (no necesita MT5 ni credenciales) ------------------------

def test_backtest_from_csv_prints_report_and_saves_trades(tmp_path):
    root = make_root(tmp_path / "root")
    csv = candles_csv(tmp_path)
    out_dir = tmp_path / "informes"
    code, out, err = run(["backtest", "--profile", "demo", "--csv", str(csv), "--out", str(out_dir)], root)
    assert code == 0, err
    assert "Backtest | ema_rsi_atr | EURUSD H1" in out and "1500 velas" in out
    saved = out_dir / "backtest_ema_rsi_atr_EURUSD_H1.csv"
    assert saved.exists() == ("Operaciones guardadas" in out)


def test_backtest_options_override_the_profile(tmp_path):
    root = make_root(tmp_path / "root")
    csv = candles_csv(tmp_path)
    code, out, err = run(
        ["backtest", "--csv", str(csv), "--strategy", "bb_breakout_macd", "--symbol", "USDJPY",
         "--timeframe", "m15", "--spread-pips", "0.5", "--initial-balance", "5000", "--no-report"],
        root,
    )
    assert code == 0, err
    assert "bb_breakout_macd | USDJPY M15" in out
    assert "Spread simulado: 0.5 pips" in out and "Saldo inicial: 5000.00" in out
    assert not (Path.cwd() / "reports" / "backtest_bb_breakout_macd_USDJPY_M15.csv").exists()


def test_every_shipped_strategy_runs_a_backtest(tmp_path):
    root = make_root(tmp_path / "root")
    csv = candles_csv(tmp_path)
    for name in ("ema_rsi_atr", "fib_pullback", "bb_breakout_macd"):
        code, out, err = run(["backtest", "--csv", str(csv), "--strategy", name, "--no-report"], root)
        assert code == 0 and f"Backtest | {name}" in out, (name, err)


def test_backtest_errors_are_reported_without_traceback(tmp_path):
    root = make_root(tmp_path / "root")
    code, _, err = run(["backtest", "--csv", str(tmp_path / "no_existe.csv")], root)
    assert code == 1 and err.startswith("Error:") and "No existe" in err

    csv = candles_csv(tmp_path)
    code, _, err = run(["backtest", "--csv", str(csv), "--strategy", "inventada"], root)
    assert code == 1 and "Disponibles" in err

    short = save_csv(random_candles(80, seed=1), tmp_path / "corto.csv")
    code, _, err = run(["backtest", "--csv", str(short), "--no-report"], root)
    assert code == 1 and "necesita más" in err


# --- signals y check con un cliente falso -------------------------------------

def test_signals_scans_every_strategy_and_symbol(tmp_path):
    root = make_root(tmp_path, credentials=True)
    restore = with_fake_client(FakeClient(random_candles(400, seed=3)))
    try:
        code, out, err = run(
            ["signals", "--profile", "demo", "--strategy", "ema_rsi_atr", "--strategy", "fib_pullback",
             "--symbol", "EURUSD"],
            root,
        )
    finally:
        restore()
    assert code == 0, err
    assert "[DEMO] EURUSD H1 | estrategia ema_rsi_atr" in out
    assert "[DEMO] EURUSD H1 | estrategia fib_pullback" in out


def test_signals_without_credentials_file_fails_cleanly(tmp_path):
    root = make_root(tmp_path)
    code, _, err = run(["signals", "--profile", "demo"], root)
    assert code == 1 and ".env.demo" in err


def test_check_prints_account_and_candles(tmp_path):
    root = make_root(tmp_path, credentials=True)
    restore = with_fake_client(FakeClient(random_candles(100, seed=3)))
    try:
        code, out, err = run(["check", "--profile", "demo", "--rows", "2"], root)
    finally:
        restore()
    assert code == 0, err
    assert "[DEMO] Conectado: cuenta 1 en Fake" in out and "EURUSD H1" in out


def test_real_profile_requires_the_explicit_flag(tmp_path):
    root = make_root(tmp_path)
    for command in ("check", "signals"):
        try:
            run([command, "--profile", "real"], root)
        except SystemExit as exit_:
            assert "--i-understand-real-account" in str(exit_)
        else:
            raise AssertionError(f"{command} no exigió la bandera del perfil real")


# --- Telegram y vigilancia ------------------------------------------------------

TG_ENV = ENV + "TELEGRAM_BOT_TOKEN=111:TOKENSECRETO\n"


def telegram_root(tmp_path, chat_id=True):
    root = make_root(tmp_path)
    (root / ".env.demo").write_text(TG_ENV + ("TELEGRAM_CHAT_ID=42\n" if chat_id else ""), encoding="utf-8")
    return root


def with_fake_http(handler):
    import forexplat.telegram as module

    original = module.TelegramClient
    module.TelegramClient = lambda token, chat_id=None: original(token, chat_id, http=handler)

    def restore():
        module.TelegramClient = original

    return restore


def test_telegram_commands_need_token_and_chat_id(tmp_path):
    root = make_root(tmp_path, credentials=True)  # sin TELEGRAM_BOT_TOKEN
    for command in ("telegram-setup", "telegram-test", "watch"):
        code, _, err = run([command, "--profile", "demo"], root)
        assert code == 1 and "TELEGRAM_BOT_TOKEN" in err, command
    root = telegram_root(tmp_path / "otro", chat_id=False)
    for command in ("telegram-test", "watch"):
        code, _, err = run([command, "--profile", "demo"], root)
        assert code == 1 and "TELEGRAM_CHAT_ID" in err and "TOKENSECRETO" not in err, command


def test_telegram_setup_lists_private_chats(tmp_path):
    root = telegram_root(tmp_path, chat_id=False)

    def http(url, payload, timeout):
        if url.endswith("/getMe"):
            return {"ok": True, "result": {"username": "mi_bot"}}
        return {"ok": True, "result": [
            {"update_id": 1, "message": {"chat": {"id": 987, "type": "private", "first_name": "Marco"}}},
            {"update_id": 2, "message": {"chat": {"id": -5, "type": "group"}}},
        ]}

    restore = with_fake_http(http)
    try:
        code, out, err = run(["telegram-setup", "--profile", "demo"], root)
    finally:
        restore()
    assert code == 0, err
    assert "@mi_bot" in out and "chat_id 987  (Marco)" in out and "-5" not in out
    assert "TELEGRAM_CHAT_ID=" in out and "TOKENSECRETO" not in out


def test_telegram_setup_without_messages_explains_what_to_do(tmp_path):
    root = telegram_root(tmp_path, chat_id=False)
    restore = with_fake_http(lambda url, payload, timeout: {"ok": True, "result": {"username": "b"} if "getMe" in url else []})
    try:
        code, out, _ = run(["telegram-setup", "--profile", "demo"], root)
    finally:
        restore()
    assert code == 0 and "/start" in out


def test_telegram_test_sends_a_message(tmp_path):
    root = telegram_root(tmp_path)
    sent = []

    def http(url, payload, timeout):
        sent.append(payload)
        return {"ok": True, "result": {"message_id": 1}}

    restore = with_fake_http(http)
    try:
        code, out, _ = run(["telegram-test", "--profile", "demo"], root)
    finally:
        restore()
    assert code == 0 and "enviado" in out
    assert sent[0]["chat_id"] == 42 and sent[0]["text"].startswith("[DEMO] Prueba")


def test_watch_once_sends_alert_and_creates_state(tmp_path):
    from fake_telegram import FakeTelegram
    from test_scanner import LONG_CANDLES

    root = telegram_root(tmp_path)
    (root / "config" / "strategies.yaml").write_text(
        "strategies:\n  demo:\n    trigger: cruce\n    rules:\n"
        "      - {type: ma_cross, name: cruce, fast: 3, slow: 5}\n      - {type: atr_filter, name: atr}\n"
        "    stop_loss: {mode: fixed_pips, pips: 20}\n    take_profit: {mode: rr, reward_risk: 2.0}\n",
        encoding="utf-8",
    )
    fake_tg = FakeTelegram(chat_id=42)
    original_tg = cli._telegram
    cli._telegram = lambda profile, need_chat: fake_tg
    restore = with_fake_client(FakeClient(LONG_CANDLES))
    try:
        code, out, err = run(["watch", "--profile", "demo", "--once", "--strategy", "demo", "--symbol", "EURUSD"], root)
    finally:
        restore()
        cli._telegram = original_tg
    assert code == 0, err
    assert len(fake_tg.sent) == 1 and "alerta long enviada" in out
    assert (root / "state" / "alerts_demo.json").exists()


def test_review_without_alerts_and_with_alerts(tmp_path):
    root = telegram_root(tmp_path)
    code, out, err = run(["review", "--profile", "demo"], root)
    assert code == 0, err
    assert "Todavía no hay alertas" in out

    from helpers import make_candles

    alert = {"strategy": "demo", "symbol": "EURUSD", "timeframe": "H4", "direction": "long",
             "candle_time": "2026-01-05 10:00:00+00:00", "entry": 1.1, "sl": 1.098, "tp": 1.104,
             "lots": 0.5, "message_id": 1, "decision": None}
    (root / "state").mkdir()
    import json

    (root / "state" / "alerts_demo.json").write_text(json.dumps({"alerts": {"a1": alert}}), encoding="utf-8")
    frame = make_candles([1.1] * 30)
    frame.loc[13, "high"] = 1.1041
    restore = with_fake_client(FakeClient(frame))
    try:
        code, out, err = run(["review", "--profile", "demo"], root)
    finally:
        restore()
    assert code == 0, err
    assert "Revisión de 1 alertas" in out and "TP" in out and "+2.00" in out and "Sin decisión: 1" in out


def test_ui_command_explains_how_to_install_streamlit_when_missing(tmp_path):
    import sys

    root = make_root(tmp_path)
    saved = sys.modules.get("streamlit")
    sys.modules["streamlit"] = None  # hace que `import streamlit` falle
    try:
        code, _, err = run(["ui", "--profile", "demo"], root)
    finally:
        if saved is None:
            sys.modules.pop("streamlit", None)
        else:
            sys.modules["streamlit"] = saved
    assert code == 1 and "pip install -e .[ui]" in err
