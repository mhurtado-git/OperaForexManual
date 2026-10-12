"""Diario de decisiones: una fila por alerta, compartido por `watch`, `review` y la interfaz.

El archivo se reescribe completo de forma atómica y bajo bloqueo, así que dos procesos nunca lo dañan.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

from .safeio import atomic_write_text, file_lock

COLUMNS = [
    "decided_at", "alert_id", "strategy", "symbol", "timeframe", "direction",
    "candle_time", "entry", "sl", "tp", "lots", "decision",
    "note", "real_entry", "real_lots", "real_result", "ticket",
]
DECISIONS = ("tomada", "descartada")


def load(path: Path) -> list[dict]:
    """Filas del diario con todas las columnas (las que falten en archivos viejos quedan vacías)."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    rows = []
    for raw in csv.DictReader(io.StringIO(text)):
        rows.append({column: (raw.get(column) or "") for column in COLUMNS})
    return rows


def _write(path: Path, rows: list[dict]) -> None:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    atomic_write_text(path, buffer.getvalue())


def upsert(path: Path, row: dict) -> dict:
    """Crea o actualiza la fila de `row['alert_id']`; solo se cambian las columnas recibidas."""
    if not row.get("alert_id"):
        raise ValueError("Falta alert_id")
    decision = row.get("decision")
    if decision not in (None, "", *DECISIONS):
        raise ValueError(f"Decisión no válida: {decision!r}")
    with file_lock(path):
        rows = load(path)
        current = next((r for r in rows if r["alert_id"] == row["alert_id"]), None)
        if current is None:
            current = {column: "" for column in COLUMNS}
            rows.append(current)
        for key, value in row.items():
            if key in COLUMNS:
                current[key] = "" if value is None else str(value)
        _write(path, rows)
    return current


def decision_of(path: Path, alert_id: str) -> str | None:
    for row in load(path):
        if row["alert_id"] == alert_id:
            return row["decision"] or None
    return None
