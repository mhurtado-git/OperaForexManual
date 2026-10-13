"""Lógica de la interfaz sin Streamlit: se puede probar y usar desde la consola."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from ..backtest import equity_curve, run_backtest, split_metrics
from ..config import load_profile
from ..data import guess_pip_size, load_candles_csv
from ..risk import SymbolSpec
from ..rules import Context
from ..safety import AccountModeMismatch
from ..scanner import scan
from ..strategy import get_strategy, load_strategies

MIN_RELIABLE_TRADES = 30
MANY_VARIANTS = 5


def redact(text: str, secrets) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return text


def friendly_error(error: Exception, secrets=()) -> str:
    """Texto breve y sin detalles técnicos para mostrar en pantalla."""
    if isinstance(error, FileNotFoundError):
        message = f"Falta un archivo: {error}"
    elif isinstance(error, AccountModeMismatch):
        message = f"La cuenta conectada no coincide con el perfil: {error}"
    elif isinstance(error, ValueError):
        message = f"Configuración o datos no válidos: {error}"
    elif isinstance(error, RuntimeError):
        message = (
            "No se pudo hablar con MetaTrader 5. Comprueba que esté abierto, con la sesión iniciada "
            f"y que el perfil coincida con la cuenta. Detalle: {error}"
        )
    else:
        message = f"Error inesperado ({type(error).__name__}): {error}"
    return redact(message, secrets)


def _secrets(profile) -> list[str]:
    values = []
    if profile.mt5 is not None:
        values.append(profile.mt5.password.get_secret_value())
    if profile.telegram is not None:
        values.append(profile.telegram.token.get_secret_value())
    return values


def safe_secrets(profile_name: str, root: Path) -> list[str]:
    """Secretos del perfil para ocultarlos en mensajes; vacío si el perfil no se puede cargar."""
    try:
        return _secrets(load_profile(profile_name, root))
    except Exception:  # noqa: BLE001
        return []


def _client_class(client_class=None):
    if client_class is not None:
        return client_class
    from ..cli import _mt5_client_class

    return _mt5_client_class()


def get_status(profile_name: str, root: Path, client_class=None) -> dict:
    """Estado de MT5 y Telegram para la pantalla de inicio. Nunca lanza errores."""
    status = {
        "profile": profile_name, "mt5_ok": False, "mt5_message": "", "account": None,
        "telegram": "", "symbols": [], "timeframe": "", "strategies": [],
    }
    try:
        profile = load_profile(profile_name, root)
    except Exception as error:  # noqa: BLE001 - se informa en pantalla
        status["mt5_message"] = friendly_error(error)
        status["telegram"] = "Sin comprobar"
        return status

    status.update(symbols=profile.symbols, timeframe=profile.timeframe, strategies=profile.strategies)
    if profile.telegram is None:
        status["telegram"] = "No configurado (falta TELEGRAM_BOT_TOKEN)"
    elif profile.telegram.chat_id is None:
        status["telegram"] = "Falta TELEGRAM_CHAT_ID"
    else:
        status["telegram"] = "Configurado"

    try:
        with _client_class(client_class)(profile) as client:
            status["account"] = client.account_summary()
        status["mt5_ok"] = True
        status["mt5_message"] = "Conectado"
    except Exception as error:  # noqa: BLE001
        status["mt5_message"] = friendly_error(error, _secrets(profile))
    return status


def run_signals(profile_name: str, root: Path, strategy_names=None, symbols=None, client_class=None):
    profile = load_profile(profile_name, root)
    available = load_strategies(root)
    strategies = [get_strategy(available, name) for name in (strategy_names or profile.strategies)]
    results = []
    with _client_class(client_class)(profile) as client:
        for strategy in strategies:
            for symbol in symbols or profile.symbols:
                if not strategy.applies_to(symbol):
                    continue
                results.append(scan(client, profile, strategy, symbol))
    return profile, results


def load_backtest_candles(
    profile_name: str, root: Path, symbol: str, timeframe: str, bars: int,
    csv_path: str | Path | None = None, pip_size: float | None = None, client_class=None,
):
    """Velas de MT5 o de un CSV. Devuelve (velas, tamaño del pip)."""
    if csv_path:
        return load_candles_csv(csv_path), pip_size or guess_pip_size(symbol)
    profile = load_profile(profile_name, root)
    with _client_class(client_class)(profile) as client:
        pip = SymbolSpec.from_summary(client.symbol_summary(symbol)).pip_size
        return client.get_candles(symbol, timeframe, bars), pip


def run_backtest_ui(
    profile_name: str, root: Path, candles: pd.DataFrame, pip_size: float, strategy_name: str,
    spread_pips: float, initial_balance: float, split_ratio: float | None = None,
) -> dict:
    """Backtest con curva de capital y, si se pide, métricas de ajuste y validación por separado."""
    profile = load_profile(profile_name, root, require_credentials=False)
    strategy = get_strategy(load_strategies(root), strategy_name)
    if len(candles) <= strategy.warmup_bars:
        raise ValueError(
            f"Hay {len(candles)} velas y '{strategy.name}' necesita más de {strategy.warmup_bars}. "
            "Descarga más historia."
        )
    result = run_backtest(candles, strategy, Context(pip_size, profile.broker_time is not None), profile.risk, spread_pips, initial_balance
    )
    out = {
        "result": result,
        "equity": equity_curve(result.trades, profile.risk.risk_per_trade, initial_balance),
        "fit": None, "valid": None, "cut_time": None, "warnings": [],
    }
    if split_ratio is not None:
        cut_time = candles["time"].iloc[int(len(candles) * split_ratio)]
        out["cut_time"] = cut_time
        out["fit"], out["valid"] = split_metrics(
            result.trades, cut_time, profile.risk.risk_per_trade, initial_balance
        ) if len(result.trades) else (result.metrics, result.metrics)
        if out["valid"]["trades"] < MIN_RELIABLE_TRADES:
            out["warnings"].append(
                f"El periodo de validación tiene solo {out['valid']['trades']} operaciones: "
                "es demasiado poco para confiar en el resultado."
            )
    if result.metrics["trades"] < MIN_RELIABLE_TRADES:
        out["warnings"].append(
            f"Solo hay {result.metrics['trades']} operaciones en total: con menos de "
            f"{MIN_RELIABLE_TRADES} el resultado no es estadísticamente fiable."
        )
    return out


METRIC_LABELS = [
    ("trades", "Operaciones", "{:d}"),
    ("win_rate", "Aciertos", "{:.1%}"),
    ("avg_r", "Expectativa (R por operación)", "{:+.2f}"),
    ("profit_factor", "Factor de beneficio", "{:.2f}"),
    ("total_r", "R total", "{:+.1f}"),
    ("return_pct", "Rendimiento (%)", "{:+.1f}"),
    ("max_drawdown_pct", "Drawdown máximo (%)", "{:.1f}"),
    ("max_consecutive_losses", "Mayor racha perdedora", "{:d}"),
]


def metrics_table(**columns: dict | None) -> pd.DataFrame:
    """Tabla de métricas en español: una columna por cada conjunto (completo, ajuste, validación)."""
    rows = []
    for key, label, fmt in METRIC_LABELS:
        row = {"Métrica": label}
        for name, metrics in columns.items():
            if metrics is None:
                continue
            value = metrics[key]
            row[name] = "sin pérdidas" if value is None else fmt.format(value)
        rows.append(row)
    return pd.DataFrame(rows)


# --- diario ---------------------------------------------------------------------

def journal_path(root: Path, profile_name: str) -> Path:
    return root / "journal" / f"decisiones_{profile_name}.csv"


def load_journal_alerts(root: Path, profile_name: str) -> list[dict]:
    """Alertas enviadas y decisiones guardadas (solo archivos locales, sin MetaTrader 5)."""
    from ..review import load_alerts

    return load_alerts(root, profile_name)


def compute_review(profile_name: str, root: Path, client_class=None) -> list[dict]:
    """Alertas con su resultado teórico (TP, SL o abierta), usando velas de MetaTrader 5."""
    from ..review import review_rows

    alerts = load_journal_alerts(root, profile_name)
    if not alerts:
        return []
    profile = load_profile(profile_name, root)
    with _client_class(client_class)(profile) as client:
        return review_rows(client, alerts)


def save_decision(
    root: Path, profile_name: str, alert: dict, decision: str | None, note: str = "",
    real_entry=None, real_lots=None, real_result=None, ticket=None,
) -> dict:
    """Guarda en el diario la decisión, la nota y los datos reales de la orden de una alerta."""
    from datetime import datetime, timezone

    from .. import journal

    def text(value):
        return "" if value in (None, "") else value

    return journal.upsert(
        journal_path(root, profile_name),
        {
            "decided_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "alert_id": alert["alert_id"],
            **{key: alert[key] for key in (
                "strategy", "symbol", "timeframe", "direction", "candle_time", "entry", "sl", "tp", "lots",
            )},
            "decision": decision or "",
            "note": note.strip(),
            "real_entry": text(real_entry), "real_lots": text(real_lots),
            "real_result": text(real_result), "ticket": text(ticket),
        },
    )


def find_real_trade(profile_name: str, root: Path, alert: dict, client_class=None) -> dict | None:
    """Busca en el historial de MetaTrader 5 la operación que abriste tras la alerta."""
    from datetime import datetime, timedelta, timezone

    import pandas as pd

    from .. import journal
    from ..history import match_alert_deals

    taken = {
        int(float(row["ticket"])) for row in journal.load(journal_path(root, profile_name))
        if row["ticket"] != "" and row["alert_id"] != alert["alert_id"]
    }
    signal = pd.Timestamp(alert["candle_time"])
    if signal.tzinfo is None:
        signal = signal.tz_localize("UTC")
    profile = load_profile(profile_name, root)
    with _client_class(client_class)(profile) as client:
        deals = client.get_deals(
            (signal - timedelta(days=1)).to_pydatetime(), datetime.now(timezone.utc) + timedelta(days=1)
        )
    return match_alert_deals(alert, deals, exclude_positions=taken)
