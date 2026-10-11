"""Gestor de riesgo: stop loss, take profit y tamaño de la posición.

Todo se calcula con reglas fijas y parámetros del YAML. Claude nunca decide estas cifras.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

import pandas as pd
from pydantic import Field, model_validator

from . import indicators as ind
from .config import RiskConfig
from .models import StrictModel

LONG = "long"
SHORT = "short"


class StopLossConfig(StrictModel):
    """Cómo se calcula el stop loss.

    atr:        atr_multiplier veces el ATR de atr_period velas.
    fixed_pips: una distancia fija en pips (`pips`).
    swing:      detrás del mínimo (compra) o máximo (venta) de las últimas swing_lookback velas,
                más un margen de swing_buffer_atr veces el ATR.
    `min_pips` y `max_pips` descartan la señal si el stop queda demasiado corto o largo.
    """

    mode: Literal["atr", "fixed_pips", "swing"] = "atr"
    atr_period: int = Field(default=14, ge=2)
    atr_multiplier: float = Field(default=1.5, gt=0)
    pips: float | None = Field(default=None, gt=0)
    swing_lookback: int = Field(default=10, ge=2)
    swing_buffer_atr: float = Field(default=0.25, ge=0)
    min_pips: float = Field(default=5.0, ge=0)
    max_pips: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _check(self):
        if self.mode == "fixed_pips" and self.pips is None:
            raise ValueError("El modo fixed_pips exige el parámetro pips")
        if self.max_pips is not None and self.max_pips <= self.min_pips:
            raise ValueError("Se exige max_pips mayor que min_pips")
        return self


class TakeProfitConfig(StrictModel):
    """Cómo se calcula el take profit.

    rr:         reward_risk veces la distancia del stop loss (2.0 = relación 1:2).
    fixed_pips: una distancia fija en pips (`pips`).
    atr:        atr_multiplier veces el ATR de atr_period velas.
    """

    mode: Literal["rr", "fixed_pips", "atr"] = "rr"
    reward_risk: float = Field(default=2.0, gt=0)
    pips: float | None = Field(default=None, gt=0)
    atr_period: int = Field(default=14, ge=2)
    atr_multiplier: float = Field(default=3.0, gt=0)

    @model_validator(mode="after")
    def _check(self):
        if self.mode == "fixed_pips" and self.pips is None:
            raise ValueError("El modo fixed_pips exige el parámetro pips")
        return self


@dataclass(frozen=True)
class SymbolSpec:
    """Datos del símbolo que usa el gestor de riesgo (salen de MT5 o de la configuración)."""

    symbol: str
    digits: int
    point: float
    tick_value: float
    tick_size: float
    volume_min: float
    volume_step: float
    volume_max: float
    spread_points: float = 0.0
    stops_level_points: int = 0

    @property
    def pip_size(self) -> float:
        # Brokers de 5 dígitos (EURUSD) y de 3 (USDJPY): 1 pip = 10 puntos.
        return self.point * 10 if self.digits in (3, 5) else self.point

    @classmethod
    def from_summary(cls, data: dict) -> "SymbolSpec":
        return cls(
            symbol=data["symbol"],
            digits=int(data["digits"]),
            point=float(data["point"]),
            tick_value=float(data["tick_value"]),
            tick_size=float(data["tick_size"]),
            volume_min=float(data["volume_min"]),
            volume_step=float(data["volume_step"]),
            volume_max=float(data["volume_max"]),
            spread_points=float(data.get("spread_points", 0.0)),
            stops_level_points=int(data.get("stops_level", 0)),
        )


class StopCalculator:
    """Precalcula ATR y extremos recientes para obtener SL y TP vela a vela."""

    def __init__(
        self, df: pd.DataFrame, stop_loss: StopLossConfig, take_profit: TakeProfitConfig, pip_size: float
    ) -> None:
        self.sl = stop_loss
        self.tp = take_profit
        self.pip = pip_size
        high, low, close = df["high"], df["low"], df["close"]
        self._atr_sl = ind.atr(high, low, close, stop_loss.atr_period).to_numpy()
        self._atr_tp = ind.atr(high, low, close, take_profit.atr_period).to_numpy()
        lookback = stop_loss.swing_lookback
        self._low_n = low.rolling(lookback, min_periods=lookback).min().to_numpy()
        self._high_n = high.rolling(lookback, min_periods=lookback).max().to_numpy()

    def stop_distance(self, i: int, direction: str, entry: float) -> tuple[float | None, str | None]:
        """Distancia del stop loss en precio para la señal de la vela `i`, o el motivo del rechazo."""
        sl = self.sl
        atr_value = self._atr_sl[i]
        if sl.mode == "atr":
            if math.isnan(atr_value):
                return None, "ATR no disponible (faltan velas)"
            distance = sl.atr_multiplier * atr_value
        elif sl.mode == "fixed_pips":
            distance = sl.pips * self.pip
        else:
            reference = self._low_n[i] if direction == LONG else self._high_n[i]
            if math.isnan(reference) or math.isnan(atr_value):
                return None, "Faltan velas para calcular el swing"
            gap = entry - reference if direction == LONG else reference - entry
            distance = gap + sl.swing_buffer_atr * atr_value
            if distance <= 0:
                return None, "El extremo reciente queda al otro lado de la entrada"

        pips = distance / self.pip
        if pips < sl.min_pips:
            return None, f"SL menor que el mínimo ({sl.min_pips:g} pips)"
        if sl.max_pips is not None and pips > sl.max_pips:
            return None, f"SL mayor que el máximo ({sl.max_pips:g} pips)"
        return distance, None

    def target_distance(self, i: int, stop_distance: float) -> float | None:
        """Distancia del take profit en precio, o None si no se puede calcular."""
        tp = self.tp
        if tp.mode == "rr":
            return tp.reward_risk * stop_distance
        if tp.mode == "fixed_pips":
            return tp.pips * self.pip
        atr_value = self._atr_tp[i]
        if math.isnan(atr_value):
            return None
        return tp.atr_multiplier * atr_value


@dataclass
class TradePlan:
    direction: str
    entry: float
    sl: float = 0.0
    tp: float = 0.0
    sl_pips: float = 0.0
    tp_pips: float = 0.0
    reward_risk: float = 0.0
    lots: float = 0.0
    risk_money: float = 0.0
    spread_pips: float = 0.0
    rejected: str | None = None

    @property
    def ok(self) -> bool:
        return self.rejected is None


def position_size(
    spec: SymbolSpec, balance: float, risk_fraction: float, stop_distance: float
) -> tuple[float, float, str | None]:
    """Lote que arriesga como máximo `risk_fraction` del saldo si el precio toca el stop loss.

    Devuelve (lote, riesgo real en dinero, motivo de rechazo). El lote se redondea hacia abajo
    al paso del símbolo. Si ni el lote mínimo cabe en el riesgo permitido, se rechaza: nunca se
    sube el lote por encima de lo que permite el riesgo.
    """
    if spec.tick_size <= 0 or spec.tick_value <= 0 or stop_distance <= 0:
        return 0.0, 0.0, "Datos del símbolo no válidos para calcular el lote"
    loss_per_lot = stop_distance / spec.tick_size * spec.tick_value
    allowed = balance * risk_fraction
    raw = allowed / loss_per_lot
    steps = math.floor(raw / spec.volume_step + 1e-9)
    lots = round(steps * spec.volume_step, 8)
    lots = min(lots, spec.volume_max)
    if lots < spec.volume_min - 1e-12:
        return 0.0, 0.0, (
            f"El lote mínimo ({spec.volume_min:g}) arriesgaría más del {risk_fraction * 100:.2f} % "
            "permitido con este stop loss"
        )
    return lots, lots * loss_per_lot, None


def build_plan(
    direction: str,
    entry: float,
    i: int,
    calc: StopCalculator,
    spec: SymbolSpec,
    balance: float,
    risk: RiskConfig,
) -> TradePlan:
    """Convierte una señal en un plan de operación con SL, TP y lote, o lo rechaza con un motivo."""
    plan = TradePlan(direction=direction, entry=entry)
    pip = spec.pip_size
    plan.spread_pips = spec.spread_points * spec.point / pip

    if plan.spread_pips > risk.max_spread_pips:
        plan.rejected = (
            f"Spread de {plan.spread_pips:.1f} pips por encima del máximo ({risk.max_spread_pips:g})"
        )
        return plan

    distance, reason = calc.stop_distance(i, direction, entry)
    if distance is None:
        plan.rejected = reason
        return plan
    if spec.stops_level_points * spec.point > distance:
        plan.rejected = "El stop loss queda dentro de la distancia mínima que exige el broker"
        return plan

    target = calc.target_distance(i, distance)
    if target is None:
        plan.rejected = "ATR no disponible para el take profit"
        return plan
    reward_risk = target / distance
    if reward_risk + 1e-9 < risk.min_reward_risk:
        plan.rejected = (
            f"Relación beneficio/riesgo de {reward_risk:.2f} por debajo del mínimo "
            f"({risk.min_reward_risk:g})"
        )
        return plan

    sign = 1 if direction == LONG else -1
    plan.sl = round(entry - sign * distance, spec.digits)
    plan.tp = round(entry + sign * target, spec.digits)
    plan.sl_pips = abs(entry - plan.sl) / pip
    plan.tp_pips = abs(plan.tp - entry) / pip
    plan.reward_risk = reward_risk

    lots, risk_money, reason = position_size(spec, balance, risk.risk_per_trade, abs(entry - plan.sl))
    if reason:
        plan.rejected = reason
        return plan
    plan.lots = lots
    plan.risk_money = risk_money
    return plan
