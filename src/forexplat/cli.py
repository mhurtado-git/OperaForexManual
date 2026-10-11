"""Línea de comandos.

    py -m forexplat check    --profile demo
    py -m forexplat signals  --profile demo
    py -m forexplat backtest --profile demo --strategy ema_rsi_atr
    py -m forexplat backtest --profile demo --csv velas.csv
    py -m forexplat telegram-setup --profile demo
    py -m forexplat telegram-test  --profile demo
    py -m forexplat watch --profile demo
    py -m forexplat review --profile demo
    py -m forexplat ui --profile demo
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

    for name, text in (
        ("telegram-setup", "Muestra el chat_id de quien escribió a tu bot (para TELEGRAM_CHAT_ID)"),
        ("telegram-test", "Envía un mensaje de prueba a tu Telegram"),
    ):
        _add_profile_args(sub.add_parser(name, help=text))

    _add_profile_args(sub.add_parser("review", help="Revisa cómo terminaron las alertas enviadas y tus decisiones"))

    ui = sub.add_parser("ui", help="Abre la interfaz gráfica en el navegador (solo en este PC)")
    _add_profile_args(ui)
    ui.add_argument("--port", type=int, default=8501)

    watch = sub.add_parser(
        "watch", help="Vigila las señales y las avisa por Telegram (Ctrl+C para detener)"
    )
    _add_profile_args(watch)
    watch.add_argument("--strategy", action="append", help="Estrategia (repetible). Por defecto, las del perfil")
    watch.add_argument("--symbol", action="append", help="Símbolo (repetible). Por defecto, los del perfil")
    watch.add_argument("--interval", type=int, default=60, help="Segundos entre revisiones (mínimo 20)")
    watch.add_argument("--once", action="store_true", help="Hace una sola revisión y termina")
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


def _telegram(profile, need_chat: bool):
    from .telegram import TelegramClient

    config = profile.telegram
    if config is None:
        raise ValueError(
            f"Falta TELEGRAM_BOT_TOKEN en .env.{profile.mode.value}. Crea el bot con @BotFather "
            "y agrega la línea TELEGRAM_BOT_TOKEN=... a ese archivo."
        )
    if need_chat and config.chat_id is None:
        raise ValueError(
            f"Falta TELEGRAM_CHAT_ID en .env.{profile.mode.value}. Escribe /start a tu bot, ejecuta "
            "`telegram-setup` y agrega la línea TELEGRAM_CHAT_ID=... a ese archivo."
        )
    return TelegramClient(config.token.get_secret_value(), config.chat_id)


def _cmd_telegram_setup(args: argparse.Namespace, root: Path) -> None:
    _confirm_real(args)
    tg = _telegram(load_profile(args.profile, root), need_chat=False)
    print(f"Bot conectado: @{tg.get_me().get('username', '?')}")
    chats: dict[int, str] = {}
    for update in tg.get_updates():
        message = update.get("message")
        if message and message.get("chat", {}).get("type") == "private":
            chat = message["chat"]
            chats[chat["id"]] = " ".join(
                part for part in (chat.get("first_name"), chat.get("last_name")) if part
            ) or str(chat["id"])
    if not chats:
        print("Nadie ha escrito al bot todavía. Abre tu bot en Telegram, envíale /start y repite este comando.")
        return
    for chat_id, name in chats.items():
        print(f"chat_id {chat_id}  ({name})")
    print(f"\nAgrega a .env.{args.profile} la línea:  TELEGRAM_CHAT_ID=<el número de arriba>")
    if len(chats) > 1:
        print("Aparecen varios chats: usa solo el tuyo.")


def _cmd_telegram_test(args: argparse.Namespace, root: Path) -> None:
    _confirm_real(args)
    profile = load_profile(args.profile, root)
    tg = _telegram(profile, need_chat=True)
    tg.send_message(f"[{profile.mode.value.upper()}] Prueba de alertas: si lees esto, Telegram está bien configurado.")
    print("Mensaje de prueba enviado.")


def _cmd_watch(args: argparse.Namespace, root: Path) -> None:
    import time

    from .strategy import get_strategy, load_strategies
    from .telegram import TelegramError
    from .watch import State, handle_updates, run_cycle

    _confirm_real(args)
    profile = load_profile(args.profile, root)
    tg = _telegram(profile, need_chat=True)
    available = load_strategies(root)
    strategies = [get_strategy(available, name) for name in (args.strategy or profile.strategies)]
    symbols = args.symbol or profile.symbols
    interval = max(args.interval, 20)
    state = State(root / "state" / f"alerts_{profile.mode.value}.json")
    journal = root / "journal" / f"decisiones_{profile.mode.value}.csv"
    Mt5Client = _mt5_client_class()

    names = ", ".join(s.name for s in strategies)
    print(f"Vigilando {', '.join(symbols)} {profile.timeframe} con {names}. Ctrl+C para detener.")
    with Mt5Client(profile) as client:
        if not args.once:
            tg.send_message(
                f"[{profile.mode.value.upper()}] Vigilancia iniciada: {names} | {', '.join(symbols)} "
                f"{profile.timeframe}. Te aviso cuando cierre una vela con señal."
            )
        while True:
            try:
                for event in run_cycle(client, tg, profile, strategies, symbols, state):
                    print(event)
                wait = 0 if args.once else min(interval, 50)
                for event in handle_updates(tg, state, journal, timeout=wait):
                    print(event)
            except (RuntimeError, TelegramError) as error:
                if args.once:
                    raise
                print(f"Aviso: {error}. Reintento en {interval} s.")
                time.sleep(interval)
                continue
            if args.once:
                return
            time.sleep(max(interval - 50, 0))


def _cmd_ui(args: argparse.Namespace, root: Path) -> None:
    import os
    import subprocess

    try:
        import streamlit  # noqa: F401
    except ImportError:
        raise ValueError("Falta Streamlit. Instálalo con:  pip install -e .[ui]") from None
    _confirm_real(args)
    app = Path(__file__).resolve().parent / "ui" / "app.py"
    command = [
        sys.executable, "-m", "streamlit", "run", str(app),
        "--server.address", "127.0.0.1", "--server.port", str(args.port),
        "--browser.gatherUsageStats", "false", "--", "--profile", args.profile,
    ]
    subprocess.run(command, env={**os.environ, "FOREXPLAT_ROOT": str(root)}, check=False)


def _cmd_review(args: argparse.Namespace, root: Path) -> None:
    from .review import bars_needed, evaluate_alert, format_review, load_alerts
    from .risk import SymbolSpec

    _confirm_real(args)
    profile = load_profile(args.profile, root)
    alerts = load_alerts(root, profile.mode.value)
    if not alerts:
        print(format_review([], profile.mode.value))
        return
    Mt5Client = _mt5_client_class()
    rows = []
    with Mt5Client(profile) as client:
        for (symbol, timeframe), group in _group_alerts(alerts).items():
            spec = SymbolSpec.from_summary(client.symbol_summary(symbol))
            candles = client.get_candles(symbol, timeframe, bars_needed(group))
            spread = spec.spread_points * spec.point
            rows += [{**a, **evaluate_alert(a, candles, spread)} for a in group]
    rows.sort(key=lambda row: row["candle_time"])
    print(format_review(rows, profile.mode.value))


def _group_alerts(alerts: list[dict]) -> dict:
    groups: dict[tuple[str, str], list[dict]] = {}
    for alert in alerts:
        groups.setdefault((alert["symbol"], alert["timeframe"]), []).append(alert)
    return groups


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
        elif args.command == "telegram-setup":
            _cmd_telegram_setup(args, root)
        elif args.command == "telegram-test":
            _cmd_telegram_test(args, root)
        elif args.command == "ui":
            _cmd_ui(args, root)
        elif args.command == "review":
            _cmd_review(args, root)
        elif args.command == "watch":
            _cmd_watch(args, root)
    except KeyboardInterrupt:
        print("\nDetenido.")
        return 0
    except (FileNotFoundError, ValueError, AccountModeMismatch) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    except RuntimeError as error:  # incluye Mt5Error
        print(f"Error de MetaTrader 5: {error}", file=sys.stderr)
        return 1
    return 0
