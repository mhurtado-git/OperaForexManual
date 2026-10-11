"""Reglas (indicadores con parámetros) que combinan las estrategias.

Cada regla devuelve, para cada vela, si favorece una compra (`long`) o una venta (`short`).
Hay dos clases:

- Evento: es verdadera solo en la vela donde ocurre algo (un cruce, una salida de sobreventa).
  Solo estas pueden ser la regla disparadora de una estrategia.
- Estado: es verdadera mientras se cumpla una condición (tendencia alcista, RSI entre 50 y 70).
  Sirven de confirmación.

Todos los parámetros se validan al cargar la configuración; una clave desconocida es un error.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Literal, Union

import pandas as pd
from pydantic import Field, model_validator

from . import indicators as ind
from .models import StrictModel


@dataclass(frozen=True)
class Context:
    """Datos del símbolo que necesitan algunas reglas."""

    pip_size: float


@dataclass
class RuleOutput:
    long: pd.Series
    short: pd.Series
    values: dict[str, pd.Series]
    label_long: str
    label_short: str


def cross_up(a: pd.Series, b: pd.Series) -> pd.Series:
    return (a.shift(1) <= b.shift(1)) & (a > b)


def cross_down(a: pd.Series, b: pd.Series) -> pd.Series:
    return (a.shift(1) >= b.shift(1)) & (a < b)


def cross_up_level(s: pd.Series, level: float) -> pd.Series:
    return (s.shift(1) <= level) & (s > level)


def cross_down_level(s: pd.Series, level: float) -> pd.Series:
    return (s.shift(1) >= level) & (s < level)


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


class _Rule(StrictModel):
    name: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]+$")

    @property
    def rule_name(self) -> str:
        return self.name or self.type  # type: ignore[attr-defined]


class MaCrossRule(_Rule):
    """Media rápida frente a media lenta. `event`: el cruce. `state`: la tendencia."""

    type: Literal["ma_cross"]
    fast: int = Field(default=20, ge=2)
    slow: int = Field(default=50, ge=3)
    ma_type: Literal["ema", "sma"] = "ema"
    mode: Literal["event", "state"] = "event"

    @model_validator(mode="after")
    def _check(self):
        if self.fast >= self.slow:
            raise ValueError("fast debe ser menor que slow")
        return self

    @property
    def is_event(self) -> bool:
        return self.mode == "event"

    @property
    def min_bars(self) -> int:
        return self.slow + 2

    def evaluate(self, df: pd.DataFrame, ctx: Context) -> RuleOutput:
        fast = ind.moving_average(df["close"], self.fast, self.ma_type)
        slow = ind.moving_average(df["close"], self.slow, self.ma_type)
        tag = self.ma_type.upper()
        f_name, s_name = f"{tag}{self.fast}", f"{tag}{self.slow}"
        if self.mode == "event":
            long, short = cross_up(fast, slow), cross_down(fast, slow)
            labels = (f"{f_name} cruza sobre {s_name}", f"{f_name} cruza bajo {s_name}")
        else:
            long, short = fast > slow, fast < slow
            labels = (
                f"tendencia alcista ({f_name} sobre {s_name})",
                f"tendencia bajista ({f_name} bajo {s_name})",
            )
        return RuleOutput(long, short, {f_name: fast, s_name: slow}, *labels)


class RsiRule(_Rule):
    """RSI. `momentum` (estado): sobre/bajo la línea media sin llegar al extremo.
    `reversal` (evento): sale de sobreventa (compra) o de sobrecompra (venta)."""

    type: Literal["rsi"]
    period: int = Field(default=14, ge=2)
    mode: Literal["momentum", "reversal"] = "momentum"
    oversold: float = Field(default=30.0, gt=0, lt=100)
    mid: float = Field(default=50.0, gt=0, lt=100)
    overbought: float = Field(default=70.0, gt=0, lt=100)

    @model_validator(mode="after")
    def _check(self):
        if not self.oversold < self.mid < self.overbought:
            raise ValueError("Se exige oversold < mid < overbought")
        return self

    @property
    def is_event(self) -> bool:
        return self.mode == "reversal"

    @property
    def min_bars(self) -> int:
        return self.period + 2

    def evaluate(self, df: pd.DataFrame, ctx: Context) -> RuleOutput:
        value = ind.rsi(df["close"], self.period)
        tag = f"RSI{self.period}"
        if self.mode == "momentum":
            long = (value > self.mid) & (value < self.overbought)
            short = (value < self.mid) & (value > self.oversold)
            labels = (
                f"{tag} alcista (entre {self.mid:g} y {self.overbought:g})",
                f"{tag} bajista (entre {self.oversold:g} y {self.mid:g})",
            )
        else:
            long = cross_up_level(value, self.oversold)
            short = cross_down_level(value, self.overbought)
            labels = (
                f"{tag} sale de sobreventa (cruza {self.oversold:g} al alza)",
                f"{tag} sale de sobrecompra (cruza {self.overbought:g} a la baja)",
            )
        return RuleOutput(long, short, {tag: value}, *labels)


class MacdRule(_Rule):
    """MACD. `cross` (evento): la línea cruza su señal. `histogram` (estado): signo del histograma."""

    type: Literal["macd"]
    fast: int = Field(default=12, ge=2)
    slow: int = Field(default=26, ge=3)
    signal: int = Field(default=9, ge=2)
    mode: Literal["cross", "histogram"] = "cross"

    @model_validator(mode="after")
    def _check(self):
        if self.fast >= self.slow:
            raise ValueError("fast debe ser menor que slow")
        return self

    @property
    def is_event(self) -> bool:
        return self.mode == "cross"

    @property
    def min_bars(self) -> int:
        return self.slow + self.signal + 2

    def evaluate(self, df: pd.DataFrame, ctx: Context) -> RuleOutput:
        line, signal, hist = ind.macd(df["close"], self.fast, self.slow, self.signal)
        values = {"MACD": line, "Senal": signal, "Histograma": hist}
        if self.mode == "cross":
            return RuleOutput(
                cross_up(line, signal),
                cross_down(line, signal),
                values,
                "MACD cruza sobre su señal",
                "MACD cruza bajo su señal",
            )
        return RuleOutput(
            hist > 0, hist < 0, values, "histograma MACD positivo", "histograma MACD negativo"
        )


class BollingerRule(_Rule):
    """Bandas de Bollinger (eventos). `reversion`: el precio vuelve a entrar en las bandas.
    `breakout`: el precio rompe una banda."""

    type: Literal["bollinger"]
    period: int = Field(default=20, ge=2)
    deviation: float = Field(default=2.0, gt=0)
    mode: Literal["reversion", "breakout"] = "reversion"

    @property
    def is_event(self) -> bool:
        return True

    @property
    def min_bars(self) -> int:
        return self.period + 2

    def evaluate(self, df: pd.DataFrame, ctx: Context) -> RuleOutput:
        close = df["close"]
        upper, mid, lower = ind.bollinger(close, self.period, self.deviation)
        prev = close.shift(1)
        values = {"BB sup": upper, "BB media": mid, "BB inf": lower}
        if self.mode == "reversion":
            long = (prev < lower.shift(1)) & (close >= lower)
            short = (prev > upper.shift(1)) & (close <= upper)
            labels = ("precio vuelve sobre la banda inferior", "precio vuelve bajo la banda superior")
        else:
            long = (prev <= upper.shift(1)) & (close > upper)
            short = (prev >= lower.shift(1)) & (close < lower)
            labels = ("ruptura de la banda superior", "ruptura de la banda inferior")
        return RuleOutput(long, short, values, *labels)


class StochasticRule(_Rule):
    """Estocástico. `cross` (evento): %K cruza %D saliendo de la zona extrema.
    `state`: %K sobre/bajo %D sin estar en la zona extrema contraria."""

    type: Literal["stochastic"]
    k_period: int = Field(default=14, ge=2)
    smooth_k: int = Field(default=3, ge=1)
    d_period: int = Field(default=3, ge=1)
    oversold: float = Field(default=20.0, gt=0, lt=100)
    overbought: float = Field(default=80.0, gt=0, lt=100)
    mode: Literal["cross", "state"] = "cross"

    @model_validator(mode="after")
    def _check(self):
        if self.oversold >= self.overbought:
            raise ValueError("Se exige oversold < overbought")
        return self

    @property
    def is_event(self) -> bool:
        return self.mode == "cross"

    @property
    def min_bars(self) -> int:
        return self.k_period + self.smooth_k + self.d_period + 2

    def evaluate(self, df: pd.DataFrame, ctx: Context) -> RuleOutput:
        k, d = ind.stochastic(df["high"], df["low"], df["close"], self.k_period, self.smooth_k, self.d_period)
        values = {"%K": k, "%D": d}
        if self.mode == "cross":
            long = cross_up(k, d) & (k.shift(1) <= self.oversold)
            short = cross_down(k, d) & (k.shift(1) >= self.overbought)
            labels = ("%K cruza sobre %D desde sobreventa", "%K cruza bajo %D desde sobrecompra")
        else:
            long = (k > d) & (k < self.overbought)
            short = (k < d) & (k > self.oversold)
            labels = ("%K sobre %D", "%K bajo %D")
        return RuleOutput(long, short, values, *labels)


class FibonacciRule(_Rule):
    """Zona de retroceso de Fibonacci del último impulso (estado).

    Compra: el último impulso fue alcista y el precio retrocedió hasta la zona definida.
    Venta: el último impulso fue bajista y el precio rebotó hasta la zona definida.
    `zone_start` y `zone_end` son proporciones del impulso (0.382 y 0.618 por defecto).
    """

    type: Literal["fibonacci"]
    lookback: int = Field(default=100, ge=10)
    zone_start: float = Field(default=0.382, gt=0, le=1)
    zone_end: float = Field(default=0.618, gt=0, le=1)
    min_range_atr: float = Field(default=2.0, ge=0)
    atr_period: int = Field(default=14, ge=2)

    @model_validator(mode="after")
    def _check(self):
        if self.zone_start >= self.zone_end:
            raise ValueError("Se exige zone_start < zone_end")
        return self

    @property
    def is_event(self) -> bool:
        return False

    @property
    def min_bars(self) -> int:
        return self.lookback + self.atr_period

    def evaluate(self, df: pd.DataFrame, ctx: Context) -> RuleOutput:
        close = df["close"]
        high, low, direction = ind.fib_swing(df["high"], df["low"], self.lookback)
        span = high - low
        atr = ind.atr(df["high"], df["low"], close, self.atr_period)
        big_enough = span >= self.min_range_atr * atr

        long = (
            (direction == 1)
            & big_enough
            & (close <= high - self.zone_start * span)
            & (close >= high - self.zone_end * span)
        )
        short = (
            (direction == -1)
            & big_enough
            & (close >= low + self.zone_start * span)
            & (close <= low + self.zone_end * span)
        )
        zone = f"{_pct(self.zone_start)}-{_pct(self.zone_end)}"
        return RuleOutput(
            long,
            short,
            {"Fib max": high, "Fib min": low},
            f"precio en retroceso Fibonacci {zone} de un impulso alcista",
            f"precio en rebote Fibonacci {zone} de un impulso bajista",
        )


class AtrFilterRule(_Rule):
    """Filtro de volatilidad (estado): el ATR, en pips, debe estar dentro del rango indicado."""

    type: Literal["atr_filter"]
    period: int = Field(default=14, ge=2)
    min_pips: float = Field(default=0.0, ge=0)
    max_pips: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _check(self):
        if self.max_pips is not None and self.max_pips <= self.min_pips:
            raise ValueError("Se exige max_pips mayor que min_pips")
        return self

    @property
    def is_event(self) -> bool:
        return False

    @property
    def min_bars(self) -> int:
        return self.period + 1

    def evaluate(self, df: pd.DataFrame, ctx: Context) -> RuleOutput:
        atr_pips = ind.atr(df["high"], df["low"], df["close"], self.period) / ctx.pip_size
        ok = atr_pips >= self.min_pips
        if self.max_pips is not None:
            ok = ok & (atr_pips <= self.max_pips)
        tag = f"ATR{self.period}"
        label = f"volatilidad {tag} dentro del rango"
        return RuleOutput(ok, ok, {f"{tag} (pips)": atr_pips}, label, label)


Rule = Annotated[
    Union[
        MaCrossRule,
        RsiRule,
        MacdRule,
        BollingerRule,
        StochasticRule,
        FibonacciRule,
        AtrFilterRule,
    ],
    Field(discriminator="type"),
]
