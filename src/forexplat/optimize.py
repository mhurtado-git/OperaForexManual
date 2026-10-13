"""Búsqueda acotada de estrategias (pensada para M5) con validación fuera de muestra.

Cómo se evita engañarse a uno mismo:
- Rejillas pequeñas con valores "redondos"; se cuenta cada combinación probada (también las ventanas
  de sesión, que son otra dimensión de la búsqueda).
- Cada combinación se simula UNA vez sobre todo el historial; ajuste y validación son cortes por la
  fecha de la señal (los indicadores no arrancan en frío en la validación).
- La elección usa solo el periodo de AJUSTE (70 % antiguo) y premia las zonas estables: la nota de una
  combinación es el promedio de su expectativa y la de sus vecinas (un paso en cada parámetro).
- La VALIDACIÓN (30 % reciente) se mira solo para las finalistas (una por par y familia) y no se usa
  para ordenarlas: solo confirma o rechaza.
- Criterios de aceptación fijos antes de ver los datos (ver `Criteria`).
"""

from __future__ import annotations

import itertools
import json
from statistics import NormalDist
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .backtest import run_backtest
from .config import RiskConfig
from .rules import Context
from .strategy import Strategy, StrategyConfig

SESSIONS: dict[str, dict | None] = {
    "sin_filtro": None,
    "ny_08-17": {"start": "08:00", "end": "17:00"},
    "ny_08-12": {"start": "08:00", "end": "12:00"},
    "ny_0930-16": {"start": "09:30", "end": "16:00"},
}
SL_MIN_PIPS = 3.0  # un stop menor que esto en M5 se lo come el spread


@dataclass(frozen=True)
class Criteria:
    min_trades_fit: int = 100
    min_trades_valid: int = 30
    min_avg_r_valid: float = 0.10
    min_profit_factor: float = 1.2
    min_windows_positive: int = 2   # de 3 ventanas consecutivas de la validación
    alpha: float = 0.05             # significancia, corregida por el número de finalistas (Bonferroni)
    max_spread_to_atr: float = 0.15
    spread_stress: float = 1.5      # el resultado debe seguir siendo positivo con spread x1.5
    fit_share: float = 0.7


@dataclass
class Family:
    name: str
    description: str
    grid: dict[str, list]
    build: object  # (params, atr_min_pips) -> (trigger, rules, stop_loss, take_profit)


def _stops(sl_mult: float, rr: float) -> tuple[dict, dict]:
    return (
        {"mode": "atr", "atr_period": 14, "atr_multiplier": sl_mult, "min_pips": SL_MIN_PIPS},
        {"mode": "rr", "reward_risk": rr},
    )


def _breakout(p, atr_min):
    fast, slow, signal = p["macd"]
    rules = [
        {"type": "bollinger", "name": "ruptura", "period": p["bb_period"], "deviation": p["bb_dev"], "mode": "breakout"},
        {"type": "macd", "name": "macd", "fast": fast, "slow": slow, "signal": signal, "mode": "histogram"},
        {"type": "atr_filter", "name": "atr", "period": 14, "min_pips": atr_min},
    ]
    return "ruptura", rules, *_stops(p["sl_mult"], p["rr"])


def _reversion(p, atr_min):
    lo, hi = p["levels"]
    fast, slow = p["trend"]
    rules = [
        {"type": "rsi", "name": "rsi_giro", "period": p["rsi_period"], "mode": "reversal", "oversold": lo, "overbought": hi},
        {"type": "ma_cross", "name": "tendencia", "fast": fast, "slow": slow, "ma_type": "ema", "mode": "state"},
        {"type": "atr_filter", "name": "atr", "period": 14, "min_pips": atr_min},
    ]
    return "rsi_giro", rules, *_stops(p["sl_mult"], p["rr"])


def _cross(p, atr_min):
    rules = [
        {"type": "ma_cross", "name": "cruce", "fast": p["fast"], "slow": p["slow"], "ma_type": "ema", "mode": "event"},
        {"type": "rsi", "name": "rsi", "period": 14, "mode": "momentum"},
        {"type": "atr_filter", "name": "atr", "period": 14, "min_pips": atr_min},
    ]
    return "cruce", rules, *_stops(p["sl_mult"], p["rr"])


FAMILIES = [
    Family(
        "ruptura", "Ruptura de Bollinger + histograma MACD + filtro ATR (momentum)",
        {"bb_period": [20, 40], "bb_dev": [2.0, 2.5], "macd": [(12, 26, 9), (8, 21, 5)],
         "sl_mult": [1.0, 1.5, 2.0], "rr": [1.5, 2.0, 3.0]}, _breakout,
    ),
    Family(
        "reversion", "Giro del RSI a favor de la tendencia (EMA) + filtro ATR (retroceso)",
        {"rsi_period": [7, 14], "levels": [(30, 70), (20, 80)], "trend": [(50, 200), (20, 100)],
         "sl_mult": [1.0, 1.5], "rr": [1.5, 2.0]}, _reversion,
    ),
    Family(
        "cruce", "Cruce de EMAs + RSI de momentum + filtro ATR (tendencia)",
        {"fast": [9, 12], "slow": [21, 34], "sl_mult": [1.0, 1.5, 2.0], "rr": [1.5, 2.0, 3.0]}, _cross,
    ),
]


def strategy_dict(family: Family, params: dict, session: str, atr_min: float, symbol: str | None = None) -> dict:
    trigger, rules, stop_loss, take_profit = family.build(params, atr_min)
    if SESSIONS[session] is not None:
        rules = [*rules, {"type": "session_filter", "name": "sesion_ny", **SESSIONS[session]}]
    data = {
        "description": f"M5 optimizada ({family.description}); sesión: {session}",
        "trigger": trigger, "rules": rules, "stop_loss": stop_loss, "take_profit": take_profit,
    }
    if symbol:
        data = {"description": data["description"], "symbols": [symbol], **{k: v for k, v in data.items() if k != "description"}}
    return data


def _label(params: dict) -> str:
    return json.dumps(params, separators=(",", ":"), default=list)


def r_stats(r) -> dict:
    r = np.asarray(r, dtype=float)
    n = len(r)
    if n == 0:
        return {"n": 0, "win_rate": 0.0, "avg_r": 0.0, "pf": None, "total_r": 0.0, "max_dd_r": 0.0,
                "max_losses": 0, "t": 0.0}
    gain, loss = r[r > 0].sum(), -r[r < 0].sum()
    curve = np.cumsum(r)
    streak = best = 0
    for value in r:
        streak = streak + 1 if value < 0 else 0
        best = max(best, streak)
    spread = float(r.std(ddof=1)) if n > 1 else 0.0
    return {
        "t": float(r.mean() / (spread / np.sqrt(n))) if spread > 0 else 0.0,
        "n": n, "win_rate": float((r > 0).mean()), "avg_r": float(r.mean()),
        "pf": float(gain / loss) if loss > 0 else None, "total_r": float(r.sum()),
        "max_dd_r": float((np.maximum.accumulate(np.r_[0.0, curve]) - np.r_[0.0, curve]).max()),
        "max_losses": int(best),
    }


def _slice(trades: pd.DataFrame, start, end) -> np.ndarray:
    times = pd.to_datetime(trades["signal_time"], utc=True)
    return trades.loc[(times >= start) & (times < end), "r"].to_numpy(dtype=float)


@dataclass
class Combo:
    family: str
    session: str
    index: tuple
    params: dict
    trades: pd.DataFrame
    fit: dict = field(default_factory=dict)
    valid: dict = field(default_factory=dict)
    smooth: float = 0.0


@dataclass
class Finalist:
    symbol: str
    combo: Combo
    checks: dict
    passed: bool
    windows: list[dict]
    stress_valid: dict
    neighbors_valid_positive: tuple[int, int]
    no_filter: dict | None
    trades_per_day: float
    hours_bogota: dict
    strategy_data: dict = field(default_factory=dict)


@dataclass
class SymbolReport:
    symbol: str
    days: float
    candles: int
    spread_pips: float
    atr_pips: float
    cut_time: pd.Timestamp
    skipped: str | None = None
    combos: int = 0
    finalists: list[Finalist] = field(default_factory=list)


def _neighbors(combo: Combo, by_key: dict, grid_sizes: list[int]):
    for dim in range(len(grid_sizes)):
        for delta in (-1, 1):
            idx = list(combo.index)
            idx[dim] += delta
            if 0 <= idx[dim] < grid_sizes[dim]:
                other = by_key.get((combo.family, combo.session, tuple(idx)))
                if other is not None:
                    yield other


def evaluate_symbol(
    symbol: str, candles: pd.DataFrame, pip_size: float, spread_pips: float, risk: RiskConfig,
    criteria: Criteria = Criteria(), sessions: list[str] | None = None, families: list[Family] | None = None,
    times_are_utc: bool = True, log=lambda text: None,
) -> SymbolReport:
    sessions = sessions if sessions is not None else list(SESSIONS)
    families = families or FAMILIES
    ctx = Context(pip_size, times_are_utc=times_are_utc)
    times = candles["time"]
    cut = times.iloc[int(len(candles) * criteria.fit_share)]
    first, last = times.iloc[0], times.iloc[-1] + pd.Timedelta(seconds=1)
    atr = (candles["high"] - candles["low"]).rolling(14).mean() / pip_size
    atr_pips = float(atr.median())
    report = SymbolReport(symbol, (last - first).total_seconds() / 86400, len(candles), spread_pips, atr_pips, cut)
    if spread_pips / atr_pips > criteria.max_spread_to_atr:
        report.skipped = (
            f"spread {spread_pips:.2f} pips = {spread_pips / atr_pips:.0%} del ATR medio ({atr_pips:.1f} pips): "
            f"el coste se come la ventaja (límite {criteria.max_spread_to_atr:.0%})"
        )
        return report
    atr_min = round(max(1.0, 10 * spread_pips), 1)

    combos: dict[tuple, Combo] = {}
    sizes: dict[str, list[int]] = {}
    for family in families:
        names = list(family.grid)
        sizes[family.name] = [len(family.grid[n]) for n in names]
        for session in sessions:
            for idx in itertools.product(*[range(len(family.grid[n])) for n in names]):
                params = {n: family.grid[n][i] for n, i in zip(names, idx)}
                config = StrategyConfig.model_validate(strategy_dict(family, params, session, atr_min))
                strategy = Strategy(f"{family.name}", config)
                if len(candles) <= strategy.warmup_bars:
                    continue
                result = run_backtest(candles, strategy, ctx, risk, spread_pips, 10_000.0)
                combo = Combo(family.name, session, idx, params, result.trades)
                combo.fit = r_stats(_slice(result.trades, first, cut))
                combo.valid = r_stats(_slice(result.trades, cut, last))
                combos[(family.name, session, idx)] = combo
        log(f"  {symbol} {family.name}: {sum(1 for k in combos if k[0] == family.name)} combinaciones")
    report.combos = len(combos)

    for combo in combos.values():
        near = list(_neighbors(combo, combos, sizes[combo.family]))
        values = [combo.fit["avg_r"]] + [n.fit["avg_r"] if n.fit["n"] >= 30 else 0.0 for n in near]
        combo.smooth = float(np.mean(values))

    best: dict[tuple, Combo] = {}
    for combo in combos.values():
        if combo.fit["n"] < criteria.min_trades_fit or combo.fit["avg_r"] <= 0:
            continue
        key = (combo.family,)
        if key not in best or combo.smooth > best[key].smooth:
            best[key] = combo

    for combo in best.values():
        family = next(f for f in families if f.name == combo.family)
        near = list(_neighbors(combo, combos, sizes[combo.family]))
        windows = _windows(combo.trades, cut, last)
        stress_spread = spread_pips * criteria.spread_stress
        config = StrategyConfig.model_validate(strategy_dict(family, combo.params, combo.session, atr_min))
        stressed = run_backtest(candles, Strategy(family.name, config), ctx, risk, stress_spread, 10_000.0)
        stress_valid = r_stats(_slice(stressed.trades, cut, last))
        with_data = [n for n in near if n.valid["n"] >= 10]
        positive = sum(1 for n in with_data if n.valid["avg_r"] > 0)
        sibling = combos.get((combo.family, "sin_filtro", combo.index))
        v = combo.valid
        checks = {
            f"operaciones en ajuste >= {criteria.min_trades_fit}": combo.fit["n"] >= criteria.min_trades_fit,
            f"operaciones en validacion >= {criteria.min_trades_valid}": v["n"] >= criteria.min_trades_valid,
            f"expectativa en validacion > +{criteria.min_avg_r_valid:g}R": v["avg_r"] > criteria.min_avg_r_valid,
            f"factor de beneficio > {criteria.min_profit_factor:g}": (v["pf"] is None and v["n"] > 0 and v["avg_r"] > 0)
            or (v["pf"] is not None and v["pf"] > criteria.min_profit_factor),
            f"positiva en {criteria.min_windows_positive} de 3 ventanas": sum(w["avg_r"] > 0 for w in windows)
            >= criteria.min_windows_positive,
            f"sigue positiva con spread x{criteria.spread_stress:g}": stress_valid["avg_r"] > 0,
            "estable ante cambios de un paso en los parametros": bool(with_data) and positive * 2 >= len(with_data),
        }
        days = max(report.days, 1.0)
        trades = combo.trades
        bogota = pd.to_datetime(trades["signal_time"], utc=True).dt.tz_convert("America/Bogota")
        hours = bogota.dt.hour.value_counts().sort_index().to_dict() if len(trades) else {}
        report.finalists.append(Finalist(
            symbol, combo, checks, all(checks.values()), windows, stress_valid, (positive, len(with_data)),
            None if sibling is None or combo.session == "sin_filtro" else {"fit": sibling.fit, "valid": sibling.valid},
            combo.fit["n"] / (days * criteria.fit_share) if combo.fit["n"] else 0.0,
            {int(h): int(c) for h, c in hours.items()},
            strategy_dict(family, combo.params, combo.session, atr_min, symbol),
        ))
    return report


def required_t(finalists: int, alpha: float) -> float:
    """t mínimo en la validación para que el resultado no sea fruto de haber mirado muchas finalistas.

    Prueba de una cola con corrección de Bonferroni: alpha / número de finalistas. Con 3 finalistas
    (un par) pide t ~ 2.1; con 18 (seis pares), t ~ 2.8.
    """
    return NormalDist().inv_cdf(1 - alpha / max(finalists, 1))


def finalize(reports: list[SymbolReport], criteria: Criteria = Criteria()) -> float:
    """Añade el criterio de significancia a todas las finalistas. Devuelve el t exigido."""
    total = sum(len(r.finalists) for r in reports)
    need = required_t(total, criteria.alpha)
    for r in reports:
        for f in r.finalists:
            f.checks[f"significativa: t >= {need:.2f} (alfa {criteria.alpha:g} corregido por {total} finalistas)"] = (
                f.combo.valid["t"] >= need
            )
            f.passed = all(f.checks.values())
    return need


def _windows(trades: pd.DataFrame, cut, last) -> list[dict]:
    edges = pd.date_range(cut, last, periods=4)
    return [r_stats(_slice(trades, edges[i], edges[i + 1])) for i in range(3)]


def pick_winners(reports: list[SymbolReport], how_many: int = 2) -> list[Finalist]:
    """Entre las finalistas que cumplen TODO, la mejor por par (ordenadas por la nota de ajuste, no por
    la validación) y como máximo `how_many`, cada una en un par distinto."""
    passing = [f for r in reports for f in r.finalists if f.passed]
    passing.sort(key=lambda f: f.combo.smooth, reverse=True)
    winners, used = [], set()
    for finalist in passing:
        if finalist.symbol in used:
            continue
        winners.append(finalist)
        used.add(finalist.symbol)
        if len(winners) == how_many:
            break
    return winners


def winner_name(f: Finalist) -> str:
    return f"m5_{f.combo.family}_{f.symbol.lower().replace('.', '_')}"


def _fmt_pf(value) -> str:
    return "sin perdidas" if value is None else f"{value:.2f}"


def _row(label: str, s: dict) -> str:
    return (f"| {label} | {s['n']} | {s['win_rate']:.0%} | {s['avg_r']:+.2f} | {_fmt_pf(s['pf'])} | "
            f"{s['total_r']:+.1f} | {s['max_dd_r']:.1f} | {s['max_losses']} |")


def render_report(reports: list[SymbolReport], winners: list[Finalist], criteria: Criteria, extra_notes=()) -> str:
    total = sum(r.combos for r in reports)
    validation_looks = sum(len(r.finalists) for r in reports)
    out = ["# Informe M5: busqueda de estrategias", ""]
    out += [
        "Todo lo que sigue sale de datos historicos; no garantiza resultados futuros. "
        "Solo cuenta lo que se sostiene en la validacion (datos que la busqueda no uso para elegir).", "",
        "## Pares evaluados", "",
        "| Par | Dias | Velas M5 | Spread usado (pips) | ATR medio (pips) | Spread/ATR | Estado |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in reports:
        state = f"descartado: {r.skipped}" if r.skipped else f"{r.combos} combinaciones"
        out.append(f"| {r.symbol} | {r.days:.0f} | {r.candles} | {r.spread_pips:.2f} | {r.atr_pips:.1f} | "
                   f"{r.spread_pips / r.atr_pips:.0%} | {state} |")
    out += ["", f"Combinaciones probadas en total: **{total}** (incluye las ventanas de sesion). "
            f"Finalistas que miraron la validacion: **{validation_looks}**.", ""]
    out += ["## Finalistas (una por par y familia)", "",
            "| Par | Familia | Sesion | Parametros | Ajuste: n / R medio | Validacion: n / R medio | Cumple |",
            "|---|---|---|---|---|---|---|"]
    for r in reports:
        for f in r.finalists:
            out.append(f"| {f.symbol} | {f.combo.family} | {f.combo.session} | `{_label(f.combo.params)}` | "
                       f"{f.combo.fit['n']} / {f.combo.fit['avg_r']:+.2f} | {f.combo.valid['n']} / "
                       f"{f.combo.valid['avg_r']:+.2f} | {'SI' if f.passed else 'no'} |")
    if not any(r.finalists for r in reports):
        out.append("| (ninguna combinacion llego a las operaciones minimas con expectativa positiva en el ajuste) | | | | | | |")
    out += ["", "## Resultado", ""]
    if not winners:
        out += ["**Ninguna candidata demostro ventaja.** Ninguna cumplio todos los criterios fuera de muestra. "
                "Eso es un resultado valido: con estos datos no hay una ventaja demostrable en M5 con estas "
                "familias y estos costos. No se escribio ninguna estrategia.", ""]
    for f in winners:
        out += _winner_section(f, criteria)
    if len(winners) == 1:
        out += ["Solo una candidata cumplio los criterios; no se completo la segunda.", ""]
    out += ["## Criterios de aceptacion", ""]
    out += [f"- {text}" for text in (
        f"minimo {criteria.min_trades_fit} operaciones en el ajuste (70 % antiguo) y {criteria.min_trades_valid} en la validacion (30 % reciente)",
        f"expectativa en validacion mayor que +{criteria.min_avg_r_valid:g}R y factor de beneficio mayor que {criteria.min_profit_factor:g}",
        f"positiva en {criteria.min_windows_positive} de 3 ventanas consecutivas de la validacion",
        f"sigue positiva con el spread multiplicado por {criteria.spread_stress:g}",
        "estable: al menos la mitad de las combinaciones vecinas (un paso en un parametro) tambien son positivas en la validacion",
    )]
    out += ["", "## Que no pude demostrar / riesgos", ""]
    out += [f"- {text}" for text in (
        "La ventana de validacion es corta (decenas de dias): una racha de mercado puede favorecer o perjudicar sin que signifique nada.",
        "El spread se midio en un momento puntual; en noticias o al cierre del dia puede ser mucho mayor. No se modelan comisiones ni deslizamiento.",
        "Las ventanas de sesion fueron parte de la busqueda: ya estan contadas en el total de combinaciones.",
        "Con ejecucion manual, una alerta puede llegar con 1 o 2 velas de retraso; el backtest entra en la apertura de la vela siguiente.",
        "Antes de pensar en dinero real hacen falta 40 a 50 operaciones demo evaluadas.",
        *extra_notes,
    )]
    return "\n".join(out) + "\n"


def _winner_section(f: Finalist, criteria: Criteria) -> list[str]:
    c = f.combo
    hours = ", ".join(f"{h:02d}h:{n}" for h, n in f.hours_bogota.items())
    out = [f"### {winner_name(f)} ({f.symbol})", "",
           f"- Familia: {c.family}. Sesion: {c.session}. Parametros: `{_label(c.params)}`",
           f"- Operaciones por dia (aprox., en el ajuste): {f.trades_per_day:.1f}",
           f"- Senales por hora de Bogota (todo el historial): {hours or 'sin datos'}", "",
           "| Tramo | Ops | Aciertos | R medio | Factor de beneficio | R total | Caida max. (R) | Racha perdedora |",
           "|---|---|---|---|---|---|---|---|",
           _row("Ajuste", c.fit), _row("Validacion", c.valid)]
    for i, w in enumerate(f.windows, 1):
        out.append(_row(f"Validacion, ventana {i}/3", w))
    out.append(_row(f"Validacion con spread x{criteria.spread_stress:g}", f.stress_valid))
    out += ["", f"Vecinas positivas en la validacion: {f.neighbors_valid_positive[0]} de {f.neighbors_valid_positive[1]}."]
    if f.no_filter:
        out += ["", "Mismos parametros SIN filtro de sesion:", "",
                "| Tramo | Ops | Aciertos | R medio | Factor de beneficio | R total | Caida max. (R) | Racha perdedora |",
                "|---|---|---|---|---|---|---|---|",
                _row("Ajuste", f.no_filter["fit"]), _row("Validacion", f.no_filter["valid"])]
    out += [""]
    return out


def combos_table(reports: list[SymbolReport]) -> pd.DataFrame:
    """No se guarda el detalle de cada combinacion (ya se resume en las finalistas)."""
    rows = []
    for r in reports:
        for f in r.finalists:
            rows.append({"symbol": f.symbol, "family": f.combo.family, "session": f.combo.session,
                         "params": _label(f.combo.params), "fit_n": f.combo.fit["n"], "fit_avg_r": f.combo.fit["avg_r"],
                         "valid_n": f.combo.valid["n"], "valid_avg_r": f.combo.valid["avg_r"], "passed": f.passed})
    return pd.DataFrame(rows)
