import urllib.error

import pytest

from forexplat.telegram import TelegramClient, TelegramError

TOKEN = "123456:SECRETO"


def fake_http(responses, calls):
    def http(url, payload, timeout):
        calls.append((url, payload, timeout))
        reply = responses.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    return http


def test_send_message_builds_request_with_buttons():
    calls = []
    tg = TelegramClient(TOKEN, 42, http=fake_http([{"ok": True, "result": {"message_id": 7}}], calls))
    assert tg.send_message("hola", [("Sí", "a|1"), ("No", "b|1")]) == 7
    url, payload, _ = calls[0]
    assert url == f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    assert payload["chat_id"] == 42 and payload["text"] == "hola"
    keyboard = payload["reply_markup"]["inline_keyboard"][0]
    assert keyboard == [{"text": "Sí", "callback_data": "a|1"}, {"text": "No", "callback_data": "b|1"}]


def test_send_message_requires_chat_id():
    tg = TelegramClient(TOKEN, None, http=fake_http([], []))
    with pytest.raises(TelegramError, match="TELEGRAM_CHAT_ID"):
        tg.send_message("hola")


def test_get_updates_passes_offset_and_returns_list():
    calls = []
    tg = TelegramClient(TOKEN, 1, http=fake_http([{"ok": True, "result": [{"update_id": 5}]}], calls))
    assert tg.get_updates(offset=5, timeout=10) == [{"update_id": 5}]
    assert calls[0][1]["offset"] == 5 and calls[0][2] > 10  # el tiempo de espera de red supera el del sondeo


def test_api_error_is_reported_without_the_token():
    tg = TelegramClient(TOKEN, 1, http=fake_http([{"ok": False, "description": f"mal {TOKEN}"}], []))
    with pytest.raises(TelegramError) as info:
        tg.get_me()
    assert TOKEN not in str(info.value) and "***" in str(info.value)


def test_network_error_is_wrapped_and_redacted():
    error = urllib.error.URLError(f"fallo en {TOKEN}")
    tg = TelegramClient(TOKEN, 1, http=fake_http([error], []))
    with pytest.raises(TelegramError, match="No se pudo contactar") as info:
        tg.get_me()
    assert TOKEN not in str(info.value)


def test_token_is_not_in_repr():
    assert TOKEN not in repr(TelegramClient(TOKEN, 1))
