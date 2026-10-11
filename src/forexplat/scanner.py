"""Escáner de señales: evalúa la última vela cerrada de un símbolo con una estrategia.

El cliente (MT5 o uno falso en las pruebas) debe ofrecer: account_summary, symbol_summary,
get_candles, get_tick y margin_required.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .config import Profile
from .risk import LONG, StopCalculator, SymbolSpec, TradePlan, build_plan
from .rules import Context
from .strategy import RuleState, Signal, Strategy


@dataclass
class ScanResult:
    symbol: str
    timeframe: str
    strategy: str
    candle_time: pd.Timestamp
    signal: Signal | None
    plan: TradePlan | None
    states: dict[str, RuleState]
    currency: str = ""
    balance: float = 0.0
    warnings: list[str] = field(default_factory=list)


def scan(client, profile: Profile, strategy: Strategy, symbol: str) -> ScanResult:
    spec = SymbolSpec.from_summary(client.symbol_summary(symbol))
    ctx = Context(spec.pip_size)

    bars = max(profile.bars, strategy.warmup_bars + 5)
    candles = client.get_candles(symbol, profile.timeframe, bars)
    warnings: list[str] = []
    if len(candles) < strategy.warmup_bars:
        warnings.append(
            f"Solo hay {len(candles)} velas y la estrategia necesita al menos {strategy.warmup_bars}: "
            "los indicadores aún no son fiables."
        )

    evaluation = strategy.evaluate(candles, ctx)
    last = len(candles) - 1
    signal = strategy.signal_at(evaluation, candles, last)
    account = client.account_summary()
    result = ScanResult(
        symbol=symbol,
        timeframe=profile.timeframe,
        strategy=strategy.name,
        candle_time=candles["time"].iloc[last],
        signal=signal,
        plan=None,
        states=strategy.rule_states(evaluation, last),
        currency=account.get("currency", ""),
        balance=float(account["balance"]),
        warnings=warnings,
    )
    if signal is None:
        return result

    tick = client.get_tick(symbol)
    entry = float(tick["ask"] if signal.direction == LONG else tick["bid"])
    calc = StopCalculator(
        candles, strategy.config.stop_loss, strategy.config.take_profit, spec.pip_size
    )
    plan = build_plan(signal.direction, entry, last, calc, spec, result.balance, profile.risk)

    if plan.ok:
        required = client.margin_required(symbol, signal.direction, plan.lots, entry)
        free = account.get("margin_free")
        if required is None or free is None:
            warnings.append("No se pudo verificar el margen disponible: compruébalo antes de operar.")
        elif required > free:
            plan.rejected = (
                f"Margen libre insuficiente ({free:.2f} disponible, {required:.2f} necesario)"
            )
    result.plan = plan
    return result
