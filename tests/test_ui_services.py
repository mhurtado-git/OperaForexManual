import pandas as pd
import pytest

from fakes import FakeClient, client_class
from forexplat.backtest import equity_curve, run_backtest, split_metrics, summarize
from forexplat.config import RiskConfig
from forexplat.ui import services
from forexplat.rules import Context
from forexplat.strategy import load_strategies
from helpers import random_candles
from test_cli import ENV, REPO, make_root

RISK = RiskConfig(risk_per_trade=0.01, max_daily_loss=0.03, min_reward_risk=2.0, max_spread_pips=2.0)
TG_ENV = ENV + "TELEGRAM_BOT_TOKEN=111:TOKENSECRETO\nTELEGRAM_CHAT_ID=42\n"


def root_with_env(tmp_path, env=TG_ENV):
    root = make_root(tmp_path)
    (root / ".env.demo").write_text(env, encoding="utf-8")
    return root


class Boom:
    def __init__(self, profile):
        raise RuntimeError("Sin conexión con secreto: secreto")


# --- estado ---------------------------------------------------------------------

def test_status_connected(tmp_path):
    root = root_with_env(tmp_path)
    status = services.get_status("demo", root, client_class(FakeClient(random_candles(100))))
    assert status["mt5_ok"] and status["account"]["login"] == 1
    assert status["telegram"] == "Configurado" and status["timeframe"]


def test_status_never_raises_and_hides_secrets(tmp_path):
    root = root_with_env(tmp_path)
    status = services.get_status("demo", root, Boom)
    assert not status["mt5_ok"] and "secreto" not in status["mt5_message"] and "***" in status["mt5_message"]
    missing = services.get_status("demo", make_root(tmp_path / "b"))  # sin .env.demo
    assert not missing["mt5_ok"] and ".env.demo" in missing["mt5_message"]


def test_status_telegram_variants(tmp_path):
    only_token = root_with_env(tmp_path / "a", ENV + "TELEGRAM_BOT_TOKEN=1:X\n")
    assert "TELEGRAM_CHAT_ID" in services.get_status("demo", only_token, Boom)["telegram"]
    none = root_with_env(tmp_path / "b", ENV)
    assert "TELEGRAM_BOT_TOKEN" in services.get_status("demo", none, Boom)["telegram"]


def test_friendly_errors():
    assert "Falta un archivo" in services.friendly_error(FileNotFoundError("x"))
    assert "MetaTrader 5" in services.friendly_error(RuntimeError("fallo"))
    assert "no válidos" in services.friendly_error(ValueError("mal"))
    assert "abc" not in services.friendly_error(ValueError("abc"), secrets=["abc"])


# --- señales --------------------------------------------------------------------

def test_run_signals_returns_one_result_per_strategy_and_symbol(tmp_path):
    root = root_with_env(tmp_path)
    fake = FakeClient(random_candles(400, seed=3))
    profile, results = services.run_signals(
        "demo", root, ["ema_rsi_atr", "fib_pullback"], ["EURUSD", "GBPUSD"], client_class(fake)
    )
    assert len(results) == 4 and {r.strategy for r in results} == {"ema_rsi_atr", "fib_pullback"}
    assert profile.mode.value == "demo"


# --- backtest -------------------------------------------------------------------

def run_ui_backtest(tmp_path, ratio=0.7, candles=None, strategy="bb_breakout_macd"):
    root = make_root(tmp_path)
    candles = candles if candles is not None else random_candles(3000, seed=7)
    return services.run_backtest_ui("demo", root, candles, 0.0001, strategy, 0.5, 10_000, ratio)


def test_backtest_with_split_partitions_the_trades(tmp_path):
    out = run_ui_backtest(tmp_path, strategy="ema_rsi_atr")
    total = out["result"].metrics["trades"]
    assert total > 0
    assert out["fit"]["trades"] + out["valid"]["trades"] == total
    assert out["cut_time"] == random_candles(3000, seed=7)["time"].iloc[2100]
    assert len(out["equity"]) == total + 1


def test_backtest_without_split_and_warnings(tmp_path):
    out = run_ui_backtest(tmp_path, ratio=None, strategy="fib_pullback")
    assert out["fit"] is None and out["valid"] is None
    if out["result"].metrics["trades"] < services.MIN_RELIABLE_TRADES:
        assert any("estadísticamente fiable" in w for w in out["warnings"])


def test_backtest_rejects_too_few_candles(tmp_path):
    with pytest.raises(ValueError, match="necesita más"):
        run_ui_backtest(tmp_path, candles=random_candles(100))


def test_equity_curve_matches_summary_final_balance():
    root = REPO
    df = random_candles(3000, seed=7)
    strategy = load_strategies(root)["ema_rsi_atr"]
    result = run_backtest(df, strategy, Context(0.0001), RISK, 0.5, 10_000)
    curve = equity_curve(result.trades, RISK.risk_per_trade, 10_000)
    assert curve["balance"].iloc[0] == 10_000
    assert curve["balance"].iloc[-1] == pytest.approx(result.metrics["final_balance"])
    assert equity_curve(result.trades.iloc[0:0], 0.01, 10_000).empty


def test_split_metrics_uses_signal_time():
    trades = pd.DataFrame({
        "signal_time": pd.to_datetime(["2026-01-01", "2026-01-10", "2026-02-01"], utc=True),
        "r": [2.0, -1.0, -1.0], "direction": ["long"] * 3, "bars": [3, 3, 3],
    })
    fit, valid = split_metrics(trades, pd.Timestamp("2026-01-15", tz="UTC"), 0.01, 10_000)
    assert (fit["trades"], valid["trades"]) == (2, 1)
    assert fit["total_r"] == pytest.approx(1.0) and valid["total_r"] == pytest.approx(-1.0)
    assert summarize(trades, 0.01, 10_000)["trades"] == 3


def test_metrics_table_formats_columns():
    metrics = summarize(pd.DataFrame({"r": [2.0, 2.0], "direction": ["long"] * 2, "bars": [1, 1]}), 0.01, 10_000)
    table = services.metrics_table(Completo=metrics, Ajuste=None)
    assert list(table.columns) == ["Métrica", "Completo"]
    assert table.loc[table["Métrica"] == "Factor de beneficio", "Completo"].iloc[0] == "sin pérdidas"
    assert table.loc[table["Métrica"] == "Aciertos", "Completo"].iloc[0] == "100.0%"


def test_load_backtest_candles_from_csv_and_from_mt5(tmp_path):
    from forexplat.data import save_csv

    root = root_with_env(tmp_path / "r")
    csv = save_csv(random_candles(120), tmp_path / "v.csv")
    candles, pip = services.load_backtest_candles("demo", root, "USDJPY", "H4", 100, csv_path=csv)
    assert len(candles) == 120 and pip == 0.01
    fake = FakeClient(random_candles(150))
    candles, pip = services.load_backtest_candles("demo", root, "EURUSD", "H4", 100, client_class=client_class(fake))
    assert len(candles) == 150 and pip == pytest.approx(0.0001)
