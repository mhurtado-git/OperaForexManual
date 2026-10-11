"""Cliente falso de MetaTrader 5 para probar el escáner y los comandos sin conexión."""

SUMMARY = {
    "symbol": "EURUSD", "digits": 5, "point": 0.00001, "tick_value": 1.0, "tick_size": 0.00001,
    "volume_min": 0.01, "volume_step": 0.01, "volume_max": 100.0, "spread_points": 10, "stops_level": 0,
}


class FakeClient:
    def __init__(
        self, candles, tick=None, summary=None, balance=10_000.0, margin_free=9_500.0, margin=500.0,
    ):
        self.candles = candles
        self.tick = tick or {"bid": 1.1000, "ask": 1.1001}
        self.summary = {**SUMMARY, **(summary or {})}
        self.account = {"login": 1, "server": "Fake", "kind": "demo", "balance": balance,
                        "currency": "USD", "leverage": 100, "margin_free": margin_free}
        self.margin = margin
        self.margin_calls = []

    def account_summary(self):
        return dict(self.account)

    def symbol_summary(self, symbol):
        return {**self.summary, "symbol": symbol}

    def get_candles(self, symbol, timeframe, bars):
        return self.candles

    def get_tick(self, symbol):
        return dict(self.tick)

    def margin_required(self, symbol, direction, lots, price):
        self.margin_calls.append((direction, lots, price))
        return self.margin


def client_class(client):
    """Clase que se comporta como Mt5Client(profile) usado en un `with`, pero devuelve `client`."""

    class _Wrapper:
        def __init__(self, profile):
            self.profile = profile

        def __enter__(self):
            return client

        def __exit__(self, *exc):
            return False

    return _Wrapper
