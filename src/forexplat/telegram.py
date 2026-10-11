"""Cliente mínimo de la API de bots de Telegram (solo biblioteca estándar).

El token nunca aparece en mensajes de error ni en `repr`.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Callable

API = "https://api.telegram.org/bot{token}/{method}"


class TelegramError(RuntimeError):
    pass


def _post_json(url: str, payload: dict, timeout: float) -> dict:
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        try:
            return json.loads(error.read().decode("utf-8"))  # Telegram explica el motivo en el cuerpo
        except (ValueError, OSError):
            return {"ok": False, "description": f"HTTP {error.code}"}


class TelegramClient:
    def __init__(
        self,
        token: str,
        chat_id: int | None = None,
        http: Callable[[str, dict, float], dict] | None = None,
    ) -> None:
        self._token = token
        self.chat_id = chat_id
        self._http = http or _post_json

    def __repr__(self) -> str:
        return f"TelegramClient(chat_id={self.chat_id})"

    def _call(self, method: str, params: dict | None = None, timeout: float = 15.0):
        url = API.format(token=self._token, method=method)
        try:
            data = self._http(url, params or {}, timeout)
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise TelegramError(
                f"No se pudo contactar con Telegram ({method}): {str(error).replace(self._token, '***')}"
            ) from None
        if not data.get("ok"):
            description = str(data.get("description", "respuesta no válida")).replace(self._token, "***")
            raise TelegramError(f"Telegram rechazó {method}: {description}")
        return data.get("result")

    def get_me(self) -> dict:
        return self._call("getMe")

    def get_updates(self, offset: int | None = None, timeout: int = 0) -> list[dict]:
        params: dict = {"timeout": timeout, "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            params["offset"] = offset
        return self._call("getUpdates", params, timeout=timeout + 15.0) or []

    def send_message(self, text: str, buttons: list[tuple[str, str]] | None = None) -> int:
        """Envía texto plano al chat configurado. `buttons` = [(texto, callback_data)]. Devuelve message_id."""
        if self.chat_id is None:
            raise TelegramError("Falta TELEGRAM_CHAT_ID: ejecuta `telegram-setup` para obtenerlo.")
        params: dict = {"chat_id": self.chat_id, "text": text, "disable_web_page_preview": True}
        if buttons:
            params["reply_markup"] = {
                "inline_keyboard": [[{"text": label, "callback_data": data} for label, data in buttons]]
            }
        return int(self._call("sendMessage", params)["message_id"])

    def answer_callback(self, callback_id: str, text: str = "") -> None:
        self._call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text})

    def clear_buttons(self, message_id: int) -> None:
        self._call(
            "editMessageReplyMarkup",
            {"chat_id": self.chat_id, "message_id": message_id, "reply_markup": {"inline_keyboard": []}},
        )
