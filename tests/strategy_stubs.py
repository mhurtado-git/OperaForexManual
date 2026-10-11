"""Estrategia falsa para probar el backtest y el escáner con señales dictadas a mano."""

from types import SimpleNamespace

import numpy as np
import pandas as pd


class StubStrategy:
    def __init__(self, n, long, short, stop_loss, take_profit, warmup_bars=10, name="stub"):
        self.name = name
        self.warmup_bars = warmup_bars
        self.config = SimpleNamespace(stop_loss=stop_loss, take_profit=take_profit)
        self._long = np.zeros(n, dtype=bool)
        self._short = np.zeros(n, dtype=bool)
        self._long[list(long)] = True
        self._short[list(short)] = True

    def evaluate(self, df, ctx):
        return SimpleNamespace(long=pd.Series(self._long), short=pd.Series(self._short))
