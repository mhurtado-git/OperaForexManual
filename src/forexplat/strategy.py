"""Estrategias: un disparador (evento) más reglas de confirmación, con SL y TP configurables."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import yaml
from pydantic import Field, ValidationError, model_validator

from .config import ROOT
from .models import StrictModel
from .risk import LONG, SHORT, StopLossConfig, TakeProfitConfig
from .rules import Context, Rule, RuleOutput

NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class StrategyConfig(StrictModel):
    description: str = ""
    # Si se indica, la estrategia solo se aplica a estos símbolos (por ejemplo, una optimizada para un par).
    symbols: list[str] | None = Field(default=None, min_length=1)
    trigger: str
    rules: list[Rule] = Field(min_length=1)
    min_confirmations: int | None = Field(default=None, ge=0)
    stop_loss: StopLossConfig = Field(default_factory=StopLossConfig)
    take_profit: TakeProfitConfig = Field(default_factory=TakeProfitConfig)

    @model_validator(mode="after")
    def _check(self):
        names = [rule.rule_name for rule in self.rules]
        repeated = sorted({name for name in names if names.count(name) > 1})
        if repeated:
            raise ValueError(
                f"Nombres de regla repetidos: {repeated}. Usa `name:` para distinguirlas."
            )
        if self.trigger not in names:
            raise ValueError(f"El disparador '{self.trigger}' no es ninguna de las reglas: {names}")
        trigger_rule = next(rule for rule in self.rules if rule.rule_name == self.trigger)
        if not trigger_rule.is_event:
            raise ValueError(
                f"El disparador '{self.trigger}' debe ser un evento (por ejemplo ma_cross con "
                "mode: event, o rsi con mode: reversal), no un estado"
            )
        confirmations = sum(1 for rule in self.rules if not getattr(rule, "is_gate", False)) - 1
        if self.min_confirmations is not None and self.min_confirmations > confirmations:
            raise ValueError(
                f"min_confirmations ({self.min_confirmations}) supera las reglas de confirmación "
                f"disponibles ({confirmations})"
            )
        return self


@dataclass
class Signal:
    direction: str
    time: pd.Timestamp
    index: int
    price: float
    reasons: list[str]


@dataclass
class Evaluation:
    long: pd.Series
    short: pd.Series
    outputs: dict[str, RuleOutput]


@dataclass(frozen=True)
class RuleState:
    long: bool
    short: bool
    is_trigger: bool
    values: dict[str, float]


def combine(
    outputs: dict[str, RuleOutput], trigger: str, min_confirmations: int | None, index: pd.Index,
    gates: frozenset[str] | set[str] = frozenset(),
) -> tuple[pd.Series, pd.Series]:
    """Señal = el disparador ocurre, suficientes reglas de confirmación coinciden y todas las
    compuertas (por ejemplo el filtro de sesión) se cumplen. Las compuertas no cuentan como votos."""
    trigger_out = outputs[trigger]
    others = [out for name, out in outputs.items() if name != trigger and name not in gates]
    needed = len(others) if min_confirmations is None else min_confirmations

    def side(attr: str) -> pd.Series:
        votes = sum((getattr(out, attr).astype(int) for out in others), start=pd.Series(0, index=index))
        passed = getattr(trigger_out, attr) & (votes >= needed)
        for name in gates:
            passed = passed & getattr(outputs[name], attr)
        return passed

    long, short = side("long"), side("short")
    both = long & short  # señales contradictorias: se descartan
    return long & ~both, short & ~both


class Strategy:
    def __init__(self, name: str, config: StrategyConfig) -> None:
        self.name = name
        self.config = config

    def applies_to(self, symbol: str) -> bool:
        return self.config.symbols is None or symbol in self.config.symbols

    @property
    def warmup_bars(self) -> int:
        """Velas que hacen falta antes de fiarse de los indicadores (con margen de convergencia)."""
        needs = [rule.min_bars for rule in self.config.rules]
        sl, tp = self.config.stop_loss, self.config.take_profit
        if sl.mode == "atr":
            needs.append(sl.atr_period + 2)
        elif sl.mode == "swing":
            needs.append(max(sl.swing_lookback, sl.atr_period) + 2)
        if tp.mode == "atr":
            needs.append(tp.atr_period + 2)
        return 3 * max(needs)

    def evaluate(self, df: pd.DataFrame, ctx: Context) -> Evaluation:
        outputs = {rule.rule_name: rule.evaluate(df, ctx) for rule in self.config.rules}
        gates = {rule.rule_name for rule in self.config.rules if getattr(rule, "is_gate", False)}
        long, short = combine(outputs, self.config.trigger, self.config.min_confirmations, df.index, gates)
        return Evaluation(long, short, outputs)

    def signal_at(self, evaluation: Evaluation, df: pd.DataFrame, i: int) -> Signal | None:
        if i < 0 or i >= len(df):
            return None
        if bool(evaluation.long.iloc[i]):
            direction = LONG
        elif bool(evaluation.short.iloc[i]):
            direction = SHORT
        else:
            return None
        attr = "label_long" if direction == LONG else "label_short"
        side = "long" if direction == LONG else "short"
        trigger = self.config.trigger
        names = [trigger] + [n for n in evaluation.outputs if n != trigger]
        reasons = [
            getattr(evaluation.outputs[name], attr)
            for name in names
            if bool(getattr(evaluation.outputs[name], side).iloc[i])
        ]
        return Signal(direction, df["time"].iloc[i], i, float(df["close"].iloc[i]), reasons)

    def rule_states(self, evaluation: Evaluation, i: int) -> dict[str, RuleState]:
        states = {}
        for name, out in evaluation.outputs.items():
            states[name] = RuleState(
                long=bool(out.long.iloc[i]),
                short=bool(out.short.iloc[i]),
                is_trigger=name == self.config.trigger,
                values={key: float(series.iloc[i]) for key, series in out.values.items()},
            )
        return states


def load_strategies(root: Path = ROOT) -> dict[str, Strategy]:
    """Carga y valida config/strategies.yaml."""
    path = root / "config" / "strategies.yaml"
    if not path.exists():
        raise FileNotFoundError(f"No existe {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    extra = set(data) - {"strategies"}
    if extra:
        raise ValueError(f"{path.name}: claves desconocidas en el nivel superior: {sorted(extra)}")
    raw = data.get("strategies")
    if not isinstance(raw, dict) or not raw:
        raise ValueError(f"{path.name} debe tener una sección 'strategies' con al menos una estrategia")

    strategies: dict[str, Strategy] = {}
    for name, body in raw.items():
        if not NAME_RE.match(str(name)):
            raise ValueError(f"Nombre de estrategia no válido: {name!r} (usa letras, números, _ o -)")
        try:
            config = StrategyConfig.model_validate(body)
        except ValidationError as error:
            raise ValueError(f"Estrategia '{name}' no válida en {path.name}:\n{error}") from error
        strategies[str(name)] = Strategy(str(name), config)
    return strategies


def get_strategy(strategies: dict[str, Strategy], name: str) -> Strategy:
    if name not in strategies:
        raise ValueError(
            f"La estrategia '{name}' no existe en strategies.yaml. Disponibles: {sorted(strategies)}"
        )
    return strategies[name]
