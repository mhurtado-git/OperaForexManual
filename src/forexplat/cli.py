"""Línea de comandos: python -m forexplat check --profile demo|real"""

from __future__ import annotations

import argparse
import sys

from .config import load_profile
from .safety import AccountModeMismatch


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="forexplat", description="Plataforma de operación manual en Forex (MT5)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser(
        "check", help="Verifica la conexión con MT5 y muestra la cuenta y las últimas velas"
    )
    check.add_argument("--profile", choices=("demo", "real"), default="demo")
    check.add_argument(
        "--i-understand-real-account",
        action="store_true",
        help="Obligatorio para usar el perfil real (cuenta con dinero real)",
    )
    check.add_argument("--rows", type=int, default=5, help="Velas a mostrar por símbolo")
    return parser.parse_args(argv)


def _confirm_real(args: argparse.Namespace) -> None:
    """El perfil real exige una bandera explícita y una confirmación escrita."""
    if args.profile != "real":
        return
    if not args.i_understand_real_account:
        raise SystemExit("El perfil real exige la bandera --i-understand-real-account.")
    answer = input("Vas a conectar una cuenta REAL. Escribe REAL para continuar: ")
    if answer.strip() != "REAL":
        raise SystemExit("Cancelado.")


def _cmd_check(args: argparse.Namespace) -> None:
    _confirm_real(args)
    profile = load_profile(args.profile)
    label = f"[{profile.mode.value.upper()}]"

    # Import tardío: MetaTrader5 solo se puede instalar en Windows.
    try:
        from .mt5_client import Mt5Client
    except ImportError as error:
        raise RuntimeError(
            "No se pudo importar el paquete MetaTrader5. Solo funciona en Windows: "
            "ejecuta `pip install -e .[dev]` en tu PC o VPS con Windows."
        ) from error

    with Mt5Client(profile) as client:
        account = client.account_summary()
        print(
            f"{label} Conectado: cuenta {account['login']} en {account['server']} "
            f"({account['kind']}), saldo {account['balance']:.2f} {account['currency']}, "
            f"apalancamiento 1:{account['leverage']}"
        )
        for symbol in profile.symbols:
            info = client.symbol_summary(symbol)
            print(
                f"\n{label} {symbol} {profile.timeframe} | dígitos {info['digits']}, "
                f"spread {info['spread_points']} puntos, lote mín. {info['volume_min']}"
            )
            candles = client.get_candles(symbol, profile.timeframe, profile.bars)
            columns = ["time", "open", "high", "low", "close", "tick_volume"]
            print(candles[columns].tail(args.rows).to_string(index=False))


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        if args.command == "check":
            _cmd_check(args)
    except (FileNotFoundError, ValueError, AccountModeMismatch) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    except RuntimeError as error:  # incluye Mt5Error
        print(f"Error de MetaTrader 5: {error}", file=sys.stderr)
        return 1
    return 0
