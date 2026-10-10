"""Salvaguardas independientes de MetaTrader 5 (fáciles de probar).

MT5 informa el tipo de cuenta en `account_info().trade_mode`:
0 = demo, 1 = concurso, 2 = real (ENUM_ACCOUNT_TRADE_MODE).
"""

from __future__ import annotations

from .config import Mode

_TRADE_MODE_NAMES = {0: "demo", 1: "concurso", 2: "real"}


class AccountModeMismatch(RuntimeError):
    """La cuenta conectada no es del tipo que pide el perfil activo."""


def account_kind(trade_mode: int) -> str:
    return _TRADE_MODE_NAMES.get(trade_mode, f"desconocida ({trade_mode})")


def assert_mode_matches(profile_mode: Mode, trade_mode: int) -> None:
    """Detiene todo si el perfil y la cuenta conectada no coinciden."""
    kind = account_kind(trade_mode)
    if kind != profile_mode.value:
        raise AccountModeMismatch(
            f"El perfil '{profile_mode.value}' no coincide con la cuenta conectada ({kind}). "
            "Se detiene por seguridad."
        )
