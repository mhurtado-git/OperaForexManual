from fakes import FakeClient
from forexplat.alerts import format_backtest, format_scan
from forexplat.backtest import run_backtest
from forexplat.config import Profile, RiskConfig
from forexplat.risk import StopLossConfig, TakeProfitConfig
from forexplat.rules import Context
from forexplat.scanner import scan
from helpers import downtrend_then_jump, make_candles
from strategy_stubs import StubStrategy
from test_scanner import LONG_CANDLES, PROFILE, SHORT_CANDLES, STRATEGY

RISK = RiskConfig(risk_per_trade=0.01, max_daily_loss=0.03, min_reward_risk=2.0, max_spread_pips=2.0)


def console_safe(text):
    text.encode("cp850")  # la consola de Windows en español
    text.encode("latin-1")


def test_long_alert_shows_everything_the_user_needs():
    text = format_scan(scan(FakeClient(LONG_CANDLES), PROFILE, STRATEGY, "EURUSD"), "demo")
    for expected in (
        "[DEMO] EURUSD H1", "estrategia demo", "ALERTA LARGO (compra)", "Entrada:", "Stop loss:",
        "Take profit:", "1 : 2.00", "Lote sugerido:    0.5", "1.00 % del saldo", "Motivos:",
        "EMA3 cruza sobre EMA5",
    ):
        assert expected in text, expected
    console_safe(text)


def test_short_alert_label():
    text = format_scan(scan(FakeClient(SHORT_CANDLES), PROFILE, STRATEGY, "EURUSD"), "demo")
    assert "ALERTA CORTO (venta)" in text
    console_safe(text)


def test_no_signal_text_lists_rule_states():
    flat = make_candles([1.1 - 0.0001 * i for i in range(61)])
    text = format_scan(scan(FakeClient(flat), PROFILE, STRATEGY, "EURUSD"), "real")
    assert text.startswith("[REAL]")
    assert "Sin señal" in text and "cruce (disparador)" in text and "atr:" in text
    console_safe(text)


def test_rejected_signal_explains_why():
    result = scan(FakeClient(LONG_CANDLES, summary={"spread_points": 30}), PROFILE, STRATEGY, "EURUSD")
    text = format_scan(result, "demo")
    assert "DESCARTADA por el gestor de riesgo" in text and "Spread" in text
    assert "ALERTA" not in text.replace("DESCARTADA", "")
    console_safe(text)


def test_warnings_are_appended():
    text = format_scan(scan(FakeClient(LONG_CANDLES, margin=None), PROFILE, STRATEGY, "EURUSD"), "demo")
    assert "Aviso:" in text and "margen" in text


def backtest_result(long):
    df = make_candles(downtrend_then_jump(200), wick=0.0002)
    strategy = StubStrategy(
        len(df), long, [], StopLossConfig(mode="fixed_pips", pips=20),
        TakeProfitConfig(mode="rr", reward_risk=2.0), 10,
    )
    return df, run_backtest(df, strategy, Context(0.0001), RISK)


def test_backtest_report_with_trades_has_warning_for_small_samples():
    df, result = backtest_result([20, 60])
    text = format_backtest(result, "demo", "EURUSD", "H1", 1.0, 0.01, 10_000)
    assert "Backtest | demo | EURUSD H1" in text and "Expectativa:" in text
    assert "menos de 30 operaciones" in text and "no garantiza" in text
    console_safe(text)


def test_backtest_report_without_trades():
    df, result = backtest_result([])
    text = format_backtest(result, "demo", "EURUSD", "H1", 1.0, 0.01, 10_000)
    assert "Sin operaciones" in text and "Expectativa" not in text
    console_safe(text)
