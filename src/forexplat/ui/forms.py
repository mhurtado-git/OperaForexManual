"""Descripción de los campos de un modelo pydantic para dibujar su formulario (sin Streamlit)."""

from __future__ import annotations

import types
import typing
from dataclasses import dataclass, field

LABELS = {
    "name": "Nombre de la regla", "description": "Descripción", "fast": "Media rápida (periodos)",
    "slow": "Media lenta (periodos)", "ma_type": "Tipo de media", "mode": "Modo", "period": "Periodo",
    "oversold": "Nivel de sobreventa", "mid": "Nivel medio", "overbought": "Nivel de sobrecompra",
    "signal": "Línea de señal (periodos)", "deviation": "Desviación (x)", "k_period": "Periodo %K",
    "smooth_k": "Suavizado %K", "d_period": "Periodo %D", "lookback": "Velas hacia atrás",
    "zone_start": "Inicio de la zona (Fibonacci)", "zone_end": "Fin de la zona (Fibonacci)",
    "min_range_atr": "Rango mínimo del impulso (x ATR)", "atr_period": "Periodo del ATR",
    "min_pips": "Mínimo (pips)", "max_pips": "Máximo (pips)", "atr_multiplier": "Multiplicador del ATR",
    "pips": "Pips", "swing_lookback": "Velas para el extremo (swing)", "swing_buffer_atr": "Margen extra (x ATR)",
    "reward_risk": "Relación beneficio/riesgo (R)", "risk_per_trade": "Riesgo por operación (fracción)",
    "max_daily_loss": "Pérdida diaria máxima (fracción)", "min_reward_risk": "Beneficio/riesgo mínimo",
    "max_spread_pips": "Spread máximo (pips)", "bars": "Velas", "spread_pips": "Spread simulado (pips)",
    "initial_balance": "Saldo inicial simulado", "timezone": "Zona horaria de la sesión",
    "start": "Inicio de la sesión (HH:MM)", "end": "Fin de la sesión (HH:MM)",
    "days": "Días activos", "no_new_minutes": "Minutos finales sin señales nuevas",
}
WEEKDAYS = {0: "lunes", 1: "martes", 2: "miércoles", 3: "jueves", 4: "viernes", 5: "sábado", 6: "domingo"}


@dataclass
class FieldSpec:
    name: str
    label: str
    kind: str                       # int | float | choice | opt_float | opt_int | text | days
    default: object = None
    options: tuple = ()
    min: float | None = None
    max: float | None = None
    exclusive_min: bool = False
    extra: dict = field(default_factory=dict)


def _bounds(info) -> dict:
    out = {}
    for meta in info.metadata:
        for key in ("ge", "gt", "le", "lt"):
            if hasattr(meta, key):
                out[key] = getattr(meta, key)
    return out


def _spec(name: str, info) -> FieldSpec:
    annotation = info.annotation
    optional = False
    origin = typing.get_origin(annotation)
    if origin in (typing.Union, types.UnionType):
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        optional = len(args) < len(typing.get_args(annotation))
        annotation = args[0]
        origin = typing.get_origin(annotation)
    bounds = _bounds(info)
    low = bounds.get("ge", bounds.get("gt"))
    high = bounds.get("le", bounds.get("lt"))
    default = None if info.is_required() else info.get_default(call_default_factory=True)
    base = dict(name=name, label=LABELS.get(name, name), default=default, min=low, max=high,
                exclusive_min="gt" in bounds)
    if origin is list and typing.get_args(annotation) == (int,):
        return FieldSpec(kind="days", options=tuple(WEEKDAYS), **base)
    if origin is typing.Literal:
        return FieldSpec(kind="choice", options=typing.get_args(annotation), **base)
    if annotation is int:
        return FieldSpec(kind="opt_int" if optional else "int", **base)
    if annotation is float:
        return FieldSpec(kind="opt_float" if optional else "float", **base)
    return FieldSpec(kind="text", **base)


def fields_for(model, skip=("type", "name", "description")) -> list[FieldSpec]:
    """Campos editables de un modelo (sin los que se tratan aparte)."""
    return [_spec(name, info) for name, info in model.model_fields.items() if name not in skip]
