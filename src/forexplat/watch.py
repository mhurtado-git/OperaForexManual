"""Vigilancia: revisa cada símbolo, avisa por Telegram de las señales nuevas y registra tu decisión.

Los botones del aviso solo registran si tomaste o descartaste la operación (diario en CSV).
No se envía ninguna orden a MetaTrader 5.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from . import journal as journal_store
from .alerts import format_scan
from .config import Profile
from .safeio import atomic_write_text
from .scanner import scan
from .telegram import TelegramError

MAX_SEEN = 1000
JOURNAL_COLUMNS = journal_store.COLUMNS
DECISIONS = {"tomada": "tomada", "descartada": "descartada"}


def alert_id(strategy: str, symbol: str, timeframe: str, candle_time) -> str:
    raw = f"{strategy}|{symbol}|{timeframe}|{candle_time}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]


class State:
    """Qué señales ya se avisaron y qué se decidió, guardado en un JSON (sobrevive a reinicios)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.offset: int | None = None
        self.seen: list[str] = []
        self.alerts: dict[str, dict] = {}
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                data = {}
            self.offset = data.get("offset")
            self.seen = list(data.get("seen", []))
            self.alerts = dict(data.get("alerts", {}))

    def mark_seen(self, key: str) -> None:
        if key not in self.seen:
            self.seen.append(key)
            del self.seen[:-MAX_SEEN]

    def save(self) -> None:
        self.alerts = dict(list(self.alerts.items())[-MAX_SEEN:])
        payload = {"offset": self.offset, "seen": self.seen, "alerts": self.alerts}
        atomic_write_text(self.path, json.dumps(payload, ensure_ascii=False, indent=1))


def _buttons(identifier: str) -> list[tuple[str, str]]:
    return [("✅ La tomé", f"tomada|{identifier}"), ("❌ Descartar", f"descartada|{identifier}")]


def run_cycle(mt5, tg, profile: Profile, strategies, symbols, state: State) -> list[str]:
    """Una pasada: evalúa cada estrategia y símbolo y avisa de las señales nuevas. Devuelve eventos."""
    events: list[str] = []
    for strategy in strategies:
        for symbol in symbols:
            if not strategy.applies_to(symbol):
                continue
            result = scan(mt5, profile, strategy, symbol)
            if result.signal is None:
                continue
            identifier = alert_id(strategy.name, symbol, result.timeframe, result.candle_time)
            if identifier in state.seen:
                continue
            plan = result.plan
            if plan is None or not plan.ok:
                state.mark_seen(identifier)
                reason = plan.rejected if plan else "sin plan"
                events.append(f"{symbol} {strategy.name}: señal descartada ({reason})")
                continue

            text = format_scan(result, profile.mode.value)
            text += "\n\nEjecútala tú en MetaTrader 5 y marca abajo qué hiciste."
            message_id = tg.send_message(text, _buttons(identifier))
            state.mark_seen(identifier)
            state.alerts[identifier] = {
                "strategy": strategy.name, "symbol": symbol, "timeframe": result.timeframe,
                "direction": result.signal.direction, "candle_time": str(result.candle_time),
                "entry": plan.entry, "sl": plan.sl, "tp": plan.tp, "lots": plan.lots,
                "message_id": message_id, "decision": None,
            }
            events.append(f"{symbol} {strategy.name}: alerta {result.signal.direction} enviada")
            state.save()
    state.save()
    return events


def _append_journal(path: Path, row: dict) -> None:
    journal_store.upsert(path, row)


def handle_updates(tg, state: State, journal: Path, timeout: int = 0) -> list[str]:
    """Lee los botones pulsados y registra la decisión. Solo se atiende al chat configurado."""
    events: list[str] = []
    updates = tg.get_updates(offset=state.offset, timeout=timeout)
    for update in updates:
        state.offset = update["update_id"] + 1
        query = update.get("callback_query")
        if not query:
            continue
        sender = query.get("from", {}).get("id")
        if sender != tg.chat_id:
            events.append("Pulsación ignorada: no viene del chat autorizado")
            continue
        action, _, identifier = str(query.get("data", "")).partition("|")
        info = state.alerts.get(identifier)
        if action not in DECISIONS or info is None:
            tg.answer_callback(query["id"], "Alerta desconocida")
            continue
        already = info.get("decision") or journal_store.decision_of(journal, identifier)
        if already:
            info["decision"] = already  # puede haberse decidido en la interfaz
            tg.answer_callback(query["id"], f"Ya la registraste como {already}")
            continue

        info["decision"] = DECISIONS[action]
        _append_journal(
            journal,
            {
                "decided_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "alert_id": identifier,
                **{key: info[key] for key in (
                    "strategy", "symbol", "timeframe", "direction", "candle_time",
                    "entry", "sl", "tp", "lots",
                )},
                "decision": info["decision"],
            },
        )
        tg.answer_callback(query["id"], f"Registrado: {info['decision']}")
        try:
            tg.clear_buttons(info["message_id"])
        except TelegramError:
            pass  # los botones se quedan; la decisión ya está guardada
        events.append(f"{info['symbol']} {info['strategy']}: {info['decision']}")
    state.save()
    return events
