"""Línea de comandos.

    py -m forexplat check    --profile demo
    py -m forexplat signals  --profile demo
    py -m forexplat backtest --profile demo --strategy ema_rsi_atr
    py -m forexplat backtest --profile demo --csv velas.csv
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from .config import ROOT, load_profile
from .safety import AccountModeMismatch


def _add_profile_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--profile", choices=("demo", "real"), default="demo")
    parser.add_argument(
        "--i-understand-real-account",
        action="store_true",
        help="Obligatorio para usar el perfil real (cuenta con dinero real)",
    )


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="forexplat", description="Plataforma de operación manual en Forex (MT5)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser(
        "check", help="Verifica la conexión con MT5 y muestra la cuenta y las últimas velas"
    )
    _add_profile_args(check)
    check.add_argument("--rows", type=int, default=5, help="Velas a mostrar por símbolo")

    signals = sub.add_parser(
        "signals",
        help="Evalúa la última vela cerrada y muestra la alerta (largo/corto con SL, TP y lote)",
    )
    _add_profile_args(signals)
    signals.add_argument(
        "--strategy", action="append", help="Estrategia a usar (repetible). Por defecto, las del perfil"
    )
    signals.add_argument("--symbol", action="append", help="Símbolo (repetible). Por defecto, los del perfil")

    backtest = sub.add_parser("backtest", help="Prueba una estrategia con velas históricas")
    _add_profile_args(backtest)
    backtest.add_argument("--strategy", help="Estrategia a probar. Por defecto, la primera del perfil")
    backtest.add_argument("--symbol", help="Símbolo. Por defecto, el primero del perfil")
    backtest.add_argument("--timeframe", help="Temporalidad. Por defecto, la del perfil")
    backtest.add_argument("--bars", type=int, help="Velas históricas a descargar de MT5")
    backtest.add_argument("--spread-pips", type=float, help="Spread simulado por operación")
    backtest.add_argument("--initial-balance", type=float, help="Saldo inicial simulado")
    backtest.add_argument("--csv", help="Usa velas de un CSV en lugar de MT5 (no necesita conexión)")
    backtest.add_argument("--pip-size", type=float, help="Tamaño del pip para el CSV (si no, se deduce)")
    backtest.add_argument("--out", default="reports", help="Carpeta del informe de operaciones (CSV)")
    backtest.add_argument("--no-report", action="store_true", help="No guardar el CSV de operaciones")
    backtest.add_argument("--save-candles", help="Guarda las velas descargadas de MT5 en este CSV")
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


def _mt5_client_class():
    # Import tardío: MetaTrader5 solo se puede instalar en Windows.
    try:
        from .mt5_client import Mt5Client
    except ImportError as error:
        raise RuntimeError(
            "No se pudo importar el paquete MetaTrader5. Solo funciona en Windows: "
            "ejecuta `pip install -e .[dev]` en tu PC o VPS con Windows."
        ) from error
    return Mt5Client


def _cmd_check(args: argparse.Namespace, root: Path) -> None:
    _confirm_real(args)
    profile = load_profile(args.profile, root)
    label = f"[{profile.mode.value.upper()}]"
    Mt5Client = _mt5_client_class()

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


def _cmd_signals(args: argparse.Namespace, root: Path) -> None:
    from .alerts import format_scan
    from .scanner import scan
    from .strategy import get_strategy, load_strategies

    _confirm_real(args)
    profile = load_profile(args.profile, root)
    available = load_strategies(root)
    strategies = [get_strategy(available, name) for name in (args.strategy or profile.strategies)]
    symbols = args.symbol or profile.symbols
    Mt5Client = _mt5_client_class()

    with Mt5Client(profile) as client:
        for strategy in strategies:
            for symbol in symbols:
                result = scan(client, profile, strategy, symbol)
                print(format_scan(result, profile.mode.value))
                print()


def _cmd_backtest(args: argparse.Namespace, root: Path) -> None:
    from .alerts import format_backtest
    from .backtest import run_backtest
    from .data import guess_pip_size, load_candles_csv, save_csv
    from .risk import SymbolSpec
    from .rules import Context
    from .strategy import get_strategy, load_strategies

    profile = load_profile(args.profile, root, require_credentials=not args.csv)
    strategy = get_strategy(load_strategies(root), args.strategy or profile.strategies[0])
    symbol = args.symbol or profile.symbols[0]
    timeframe = (args.timeframe or profile.timeframe).upper()
    bt = profile.backtest
    spread_pips = bt.spread_pips if args.spread_pips is None else args.spread_pips
    initial_balance = bt.initial_balance if args.initial_balance is None else args.initial_balance

    if args.csv:
        candles = load_candles_csv(args.csv)
        pip_size = args.pip_size or guess_pip_size(symbol)
    else:
        _confirm_real(args)
        Mt5Client = _mt5_client_class()
        bars = args.bars or bt.bars
        with Mt5Client(profile) as client:
            pip_size = SymbolSpec.from_summary(client.symbol_summary(symbol)).pip_size
            candles = client.get_candles(symbol, timeframe, bars)
        if args.save_candles:
            print(f"Velas guardadas en {save_csv(candles, args.save_candles)}")

    if len(candles) <= strategy.warmup_bars:
        raise ValueError(
            f"Hay {len(candles)} velas y la estrategia '{strategy.name}' necesita más de "
            f"{strategy.warmup_bars} para empezar a operar. Descarga más historia."
        )

    result = run_backtest(
        candles, strategy, Context(pip_size), profile.risk, spread_pips, initial_balance
    )
    print(
        format_backtest(
            result, strategy.name, symbol, timeframe, spread_pips,
            profile.risk.risk_per_trade, initial_balance,
        )
    )

    if not args.no_report and len(result.trades):
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", f"{strategy.name}_{symbol}_{timeframe}")
        path = Path(args.out) / f"backtest_{safe}.csv"
        print(f"\nOperaciones guardadas en {save_csv(result.trades, path)}")


def _console_safe() -> None:
    """Evita que un carácter que la consola no sabe imprimir tumbe el programa."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(errors="replace")
            except (ValueError, OSError):
                pass


def main(argv: list[str] | None = None, root: Path = ROOT) -> int:
    _console_safe()
    args = _parse_args(argv)
    try:
        if args.command == "check":
            _cmd_check(args, root)
        elif args.command == "signals":
            _cmd_signals(args, root)
        elif args.command == "backtest":
            _cmd_backtest(args, root)
    except (FileNotFoundError, ValueError, AccountModeMismatch) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    except RuntimeError as error:  # incluye Mt5Error
        print(f"Error de MetaTrader 5: {error}", file=sys.stderr)
        return 1
    return 0
