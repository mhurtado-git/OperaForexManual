"""Revisión de las alertas enviadas: cómo habrían terminado y qué decidiste.

Para cada alerta se recorren las velas posteriores con las mismas reglas del backtest (si una vela toca
SL y TP a la vez, cuenta el SL). Es un resultado teórico con los niveles de la alerta: no lee las órdenes
reales de tu cuenta.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
import pandas as pd

from .backtest import _simulate
from .risk import LONG

TF_MINUTES = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}
GROUPS = (("tomada", "Tomadas"), ("descartada", "Descartadas"), (None, "Sin decisión"))


def load_alerts(root: Path, mode: str) -> list[dict]:
    """Alertas enviadas (estado) con la decisión del diario; el diario manda si hay diferencia."""
    alerts: dict[str, dict] = {}
    state = root / "state" / f"alerts_{mode}.json"
    if state.exists():
        try:
            for key, info in json.loads(state.read_text(encoding="utf-8")).get("alerts", {}).items():
                alerts[key] = {**info, "alert_id": key}
        except ValueError:
            pass
    journal = root / "journal" / f"decisiones_{mode}.csv"
    if journal.exists():
        with journal.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                base = alerts.get(row["alert_id"], {})
                alerts[row["alert_id"]] = {
                    **base,
                    **{key: row[key] for key in ("strategy", "symbol", "timeframe", "direction", "candle_time")},
                    **{key: float(row[key]) for key in ("entry", "sl", "tp", "lots")},
                    "alert_id": row["alert_id"],
                    "decision": row["decision"],
                }
    return sorted(alerts.values(), key=lambda a: a["candle_time"])


def bars_needed(alerts: list[dict]) -> int:
    """Velas que hay que descargar para cubrir desde la alerta más antigua hasta ahora."""
    earliest = min(pd.Timestamp(a["candle_time"]) for a in alerts)
    if earliest.tzinfo is None:
        earliest = earliest.tz_localize("UTC")
    minutes = (pd.Timestamp.now(tz="UTC") - earliest).total_seconds() / 60
    step = TF_MINUTES[alerts[0]["timeframe"]]
    return int(min(max(minutes / step + 10, 30), 5000))


def evaluate_alert(alert: dict, candles: pd.DataFrame, spread: float = 0.0) -> dict:
    """Resultado teórico de la alerta con las velas posteriores a la vela que la generó."""
    signal_time = pd.Timestamp(alert["candle_time"])
    if signal_time.tzinfo is None:
        signal_time = signal_time.tz_localize("UTC")
    later = candles[candles["time"] > signal_time].reset_index(drop=True)
    result = {"status": "pendiente", "r": None, "bars": 0}
    if later.empty:
        return result

    entry, sl, tp = alert["entry"], alert["sl"], alert["tp"]
    risk = abs(entry - sl)
    if risk <= 0:
        return {"status": "inválida", "r": None, "bars": 0}
    o, h, l, c = (later[col].to_numpy(dtype=float) for col in ("open", "high", "low", "close"))
    sign = 1 if alert["direction"] == LONG else -1
    exit_j, exit_price, reason = _simulate(alert["direction"], 0, entry, sl, tp, o, h, l, c, spread)
    r = sign * (exit_price - entry) / risk
    status = "abierta" if reason == "FIN" else reason
    return {"status": status, "r": float(r), "bars": exit_j + 1}


def summarize(rows: list[dict]) -> dict:
    closed = [row["r"] for row in rows if row["status"] in ("TP", "SL")]
    open_r = [row["r"] for row in rows if row["status"] == "abierta"]
    wins = sum(1 for r in closed if r > 0)
    return {
        "alerts": len(rows),
        "closed": len(closed),
        "open": len(open_r),
        "wins": wins,
        "win_rate": wins / len(closed) if closed else 0.0,
        "total_r": float(sum(closed)),
        "avg_r": float(np.mean(closed)) if closed else 0.0,
        "open_r": float(sum(open_r)),
    }


def format_review(rows: list[dict], mode: str) -> str:
    if not rows:
        return (
            f"[{mode.upper()}] Todavía no hay alertas registradas. Deja `watch` corriendo y vuelve "
            "cuando haya recibido alguna."
        )
    lines = [f"[{mode.upper()}] Revisión de {len(rows)} alertas (resultados teóricos con los niveles del aviso)", ""]
    lines.append(f"{'Vela':<17}{'Par':<8}{'Dir.':<7}{'Decisión':<14}{'Resultado':<11}{'R':>6}")
    for row in rows:
        r = "" if row["r"] is None else f"{row['r']:+.2f}"
        decision = row.get("decision") or "sin decisión"
        direction = "largo" if row["direction"] == LONG else "corto"
        lines.append(
            f"{str(row['candle_time'])[:16]:<17}{row['symbol']:<8}{direction:<7}{decision:<14}"
            f"{row['status']:<11}{r:>6}"
        )

    lines += ["", "Resumen:"]
    for decision, label in GROUPS:
        group = [row for row in rows if (row.get("decision") or None) == decision]
        if not group:
            continue
        s = summarize(group)
        text = f"  {label}: {s['alerts']} alertas"
        if s["closed"]:
            text += (
                f" | cerradas {s['closed']} ({s['win_rate'] * 100:.0f} % de aciertos) | "
                f"{s['total_r']:+.2f} R en total ({s['avg_r']:+.2f} R por operación)"
            )
        if s["open"]:
            text += f" | abiertas {s['open']} ({s['open_r']:+.2f} R flotante)"
        lines.append(text)

    closed = sum(1 for row in rows if row["status"] in ("TP", "SL"))
    if closed < 30:
        lines.append(
            f"\nAviso: con {closed} operaciones cerradas todavía no se puede juzgar la estrategia "
            "(hacen falta 40 o 50). Backtest de referencia: +0,1 a +0,2 R por operación."
        )
    return "\n".join(lines)
