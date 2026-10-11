"""Telegram falso para probar la vigilancia sin red."""


class FakeTelegram:
    def __init__(self, chat_id=42):
        self.chat_id = chat_id
        self.sent = []          # (texto, botones)
        self.answers = []
        self.cleared = []
        self.pending = []       # updates que devolverá get_updates
        self.fail_send = False

    def send_message(self, text, buttons=None):
        if self.fail_send:
            from forexplat.telegram import TelegramError

            raise TelegramError("sin red")
        self.sent.append((text, buttons))
        return 100 + len(self.sent)

    def get_updates(self, offset=None, timeout=0):
        updates = [u for u in self.pending if offset is None or u["update_id"] >= offset]
        self.pending = []
        return updates

    def answer_callback(self, callback_id, text=""):
        self.answers.append((callback_id, text))

    def clear_buttons(self, message_id):
        self.cleared.append(message_id)

    def press(self, data, user_id=42, update_id=1):
        self.pending.append(
            {"update_id": update_id, "callback_query": {"id": f"cb{update_id}", "from": {"id": user_id}, "data": data}}
        )
