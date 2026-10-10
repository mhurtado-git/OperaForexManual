"""Conector con MetaTrader 5 (solo Windows).

Fase 1: conexión, verificación del tipo de cuenta, lectura de cuenta, símbolos y velas.
El envío de órdenes llega en la fase 3 y siempre después de la confirmación del usuario.
"""

from __future__ import annotations

import MetaTrader5 as mt5
import pandas as pd

from .config import Profile
from .safety import account_kind, assert_mode_matches

_TIMEFRAMES = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1,
}


class Mt5Error(RuntimeError):
    """Fallo al hablar con el terminal MetaTrader 5."""


class Mt5Client:
    """Uso: `with Mt5Client(profile) as client: ...`"""

    def __init__(self, profile: Profile) -> None:
        self._profile = profile
        self._account = None

    # --- ciclo de vida -------------------------------------------------

    def __enter__(self) -> "Mt5Client":
        self.connect()
        return self

    def __exit__(self, *exc_info) -> None:
        self.shutdown()

    def connect(self) -> None:
        creds = self._profile.mt5
        kwargs = {
            "login": creds.login,
            "password": creds.password.get_secret_value(),
            "server": creds.server,
        }
        if creds.path:
            kwargs["path"] = creds.path

        if not mt5.initialize(**kwargs):
            raise Mt5Error(f"No se pudo iniciar MT5: {mt5.last_error()}")

        info = mt5.account_info()
        if info is None:
            error = mt5.last_error()
            mt5.shutdown()
            raise Mt5Error(f"MT5 no devolvió datos de la cuenta: {error}")

        try:
            # Salvaguarda: el perfil activo debe coincidir con la cuenta realmente conectada.
            assert_mode_matches(self._profile.mode, info.trade_mode)
        except Exception:
            mt5.shutdown()
            raise

        self._account = info

    def shutdown(self) -> None:
        mt5.shutdown()
        self._account = None

    # --- lectura -------------------------------------------------------

    def account_summary(self) -> dict:
        """Datos básicos de la cuenta conectada (sin nombre ni contraseña)."""
        info = self._require_account()
        return {
            "login": info.login,
            "server": info.server,
            "kind": account_kind(info.trade_mode),
            "currency": info.currency,
            "balance": info.balance,
            "equity": info.equity,
            "leverage": info.leverage,
        }

    def symbol_summary(self, symbol: str) -> dict:
        """Datos del símbolo que usará más adelante el gestor de riesgo."""
        self._require_account()
        self._select_symbol(symbol)
        info = mt5.symbol_info(symbol)
        if info is None:
            raise Mt5Error(f"Sin información para {symbol}: {mt5.last_error()}")
        return {
            "symbol": symbol,
            "digits": info.digits,
            "point": info.point,
            "spread_points": info.spread,
            "tick_value": info.trade_tick_value,
            "tick_size": info.trade_tick_size,
            "volume_min": info.volume_min,
            "volume_step": info.volume_step,
            "volume_max": info.volume_max,
        }

    def get_candles(
        self, symbol: str, timeframe: str, bars: int, closed_only: bool = True
    ) -> pd.DataFrame:
        """Últimas `bars` velas. Por defecto solo velas cerradas."""
        self._require_account()
        if timeframe not in _TIMEFRAMES:
            raise Mt5Error(f"Temporalidad no soportada: {timeframe}")
        self._select_symbol(symbol)

        # Se pide una vela extra porque la última aún se está formando.
        count = bars + 1 if closed_only else bars
        rates = mt5.copy_rates_from_pos(symbol, _TIMEFRAMES[timeframe], 0, count)
        if rates is None or len(rates) == 0:
            raise Mt5Error(f"Sin velas para {symbol} {timeframe}: {mt5.last_error()}")

        frame = pd.DataFrame(rates)
        # MT5 entrega la hora del servidor del broker; se marca como UTC solo para ordenar.
        frame["time"] = pd.to_datetime(frame["time"], unit="s", utc=True)
        if closed_only:
            frame = frame.iloc[:-1]
        return frame.reset_index(drop=True)

    # --- internos ------------------------------------------------------

    def _require_account(self):
        if self._account is None:
            raise Mt5Error("No hay conexión con MT5. Usa el cliente dentro de un bloque 'with'.")
        return self._account

    @staticmethod
    def _select_symbol(symbol: str) -> None:
        if not mt5.symbol_select(symbol, True):
            raise Mt5Error(
                f"El símbolo {symbol} no está disponible en este broker: {mt5.last_error()}"
            )
