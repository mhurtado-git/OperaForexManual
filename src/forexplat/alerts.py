"""Texto de las alertas y de los resultados (la entrega por Telegram llega en la fase 3).

Solo caracteres ASCII y Latin-1: la consola de Windows en español no imprime flechas ni guiones largos.
"""

from __future__ import annotations

from .risk import LONG
from .scanner import ScanResult

DIRECTION_LABEL = {LONG: "LARGO (compra)", "short": "CORTO (venta)"}


def _yes_no(value: bool) -> str:
    return "sí" if value else "no"


def _values_text(values: dict[str, float]) -> str:
    return ", ".join(f"{name}={value:.5g}" for name, value in values.items())


def format_states(result: ScanResult) -> str:
    lines = ["Estado de las reglas en la última vela cerrada:"]
    for name, state in result.states.items():
        role = " (disparador)" if state.is_trigger else ""
        lines.append(
            f"  - {name}{role}: compra {_yes_no(state.long)} | venta {_yes_no(state.short)}"
            f"   [{_values_text(state.values)}]"
        )
    return "\n".join(lines)


def format_scan(result: ScanResult, mode: str) -> str:
    tag = f"[{mode.upper()}]"
    head = (
        f"{tag} {result.symbol} {result.timeframe} | estrategia {result.strategy} | "
        f"vela cerrada {result.candle_time:%Y-%m-%d %H:%M}"
    )
    lines = [head]

    if result.signal is None:
        lines.append("Sin señal en la última vela cerrada.")
        lines.append(format_states(result))
    else:
        direction = DIRECTION_LABEL[result.signal.direction]
        plan = result.plan
        if plan is None or not plan.ok:
            reason = plan.rejected if plan else "sin plan de operación"
            lines.append(f"SEÑAL {direction} DESCARTADA por el gestor de riesgo: {reason}")
        else:
            percent = plan.risk_money / result.balance * 100 if result.balance else 0.0
            lines += [
                f"ALERTA {direction}",
                f"  Entrada:          {plan.entry:.5g} (precio actual)",
                f"  Stop loss:        {plan.sl:.5g} ({plan.sl_pips:.1f} pips)",
                f"  Take profit:      {plan.tp:.5g} ({plan.tp_pips:.1f} pips)",
                f"  Riesgo/beneficio: 1 : {plan.reward_risk:.2f}",
                f"  Lote sugerido:    {plan.lots:g} (riesgo {plan.risk_money:.2f} {result.currency} = "
                f"{percent:.2f} % del saldo de {result.balance:.2f} {result.currency})",
                f"  Spread actual:    {plan.spread_pips:.1f} pips",
            ]
        lines.append("  Motivos:")
        lines += [f"    - {reason}" for reason in result.signal.reasons]

    for warning in result.warnings:
        lines.append(f"Aviso: {warning}")
    return "\n".join(lines)


def format_backtest(
    result, strategy: str, symbol: str, timeframe: str, spread_pips: float,
    risk_per_trade: float, initial_balance: float,
) -> str:
    m = result.metrics
    period = ""
    if result.first_time is not None:
        period = f" ({result.first_time:%Y-%m-%d} a {result.last_time:%Y-%m-%d})"
    skipped = sum(result.skipped.values())
    lines = [
        f"Backtest | {strategy} | {symbol} {timeframe} | {result.candles} velas{period}",
        f"Spread simulado: {spread_pips:g} pips | Riesgo por operación: {risk_per_trade * 100:.2f} % "
        f"| Saldo inicial: {initial_balance:.2f}",
        f"Señales: {result.signals} | operaciones: {m['trades']} | "
        f"descartadas por el gestor de riesgo: {skipped}",
    ]
    for reason, count in sorted(result.skipped.items(), key=lambda item: -item[1]):
        lines.append(f"    {count} x {reason}")

    if m["trades"] == 0:
        lines.append("Sin operaciones en este periodo: no hay resultados que mostrar.")
    else:
        pf = "sin pérdidas" if m["profit_factor"] is None else f"{m['profit_factor']:.2f}"
        lines += [
            "Resultado:",
            f"  Operaciones:           {m['trades']} (largos {m['longs']} | cortos {m['shorts']})",
            f"  Aciertos:              {m['wins']} ({m['win_rate'] * 100:.1f} %) | pérdidas: {m['losses']}",
            f"  Expectativa:           {m['avg_r']:+.2f} R por operación",
            f"  Factor de beneficio:   {pf}",
            f"  R total:               {m['total_r']:+.1f} R",
            f"  Rendimiento:           {m['return_pct']:+.1f} % (saldo final {m['final_balance']:.2f})",
            f"  Drawdown máximo:       {m['max_drawdown_pct']:.1f} %",
            f"  Mayor racha perdedora: {m['max_consecutive_losses']}",
            f"  Duración media:        {m['avg_bars']:.1f} velas",
        ]
        if m["trades"] < 30:
            lines.append(
                "Aviso: con menos de 30 operaciones el resultado no es estadísticamente fiable."
            )
    lines.append(
        "Recuerda: un buen backtest no garantiza resultados futuros, y ajustar los parámetros hasta "
        "que el histórico se vea bien suele fallar en vivo. Valida siempre en demo."
    )
    return "\n".join(lines)
