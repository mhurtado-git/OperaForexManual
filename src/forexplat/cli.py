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

POLL_SECONDS = 15  # espera larga corta: algunas redes cortan las conexiones inactivas de ~50 s


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

    check_time = sub.add_parser(
        "check-time", help="Comprueba la hora del servidor del broker (necesaria para el filtro de sesión)"
    )
    _add_profile_args(check_time)
    check_time.add_argument("--symbol", help="Símbolo de prueba. Por defecto, el primero del perfil")
    check_time.add_argument("--bars", type=int, default=9000, help="Velas H1 a revisar (9000 ~ 1 año)")

    optimize = sub.add_parser(
        "optimize", help="Busca estrategias (M5) con validación fuera de muestra y escribe un informe"
    )
    _add_profile_args(optimize)
    optimize.add_argument("--symbol", action="append", help="Par a evaluar (repetible). Por defecto, 6 pares habituales")
    optimize.add_argument("--bars", type=int, default=100000, help="Velas M5 a descargar por par (máx. lo que dé MT5)")
    optimize.add_argument("--spread-pips", type=float, help="Spread fijo para todos los pares (si no, se mide en MT5)")
    optimize.add_argument("--slack-pips", type=float, default=0.3, help="Holgura que se suma al spread medido")
    optimize.add_argument("--risk-per-trade", type=float, default=0.0025, help="Riesgo por operación sugerido")
    optimize.add_argument("--alpha", type=float, default=0.05, help="Nivel de significancia (se corrige por finalistas)")
    optimize.add_argument("--no-session", action="store_true", help="No probar el filtro de sesión (no exige broker_time)")
    optimize.add_argument("--use-saved", action="store_true", help="Usa las velas de data/ en lugar de descargarlas")
    optimize.add_argument("--out", default="reports", help="Carpeta del informe")
    optimize.add_argument("--write", action="store_true", help="Escribe las estrategias ganadoras en strategies.yaml")

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
                if not strategy.applies_to(symbol):
                    continue
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
        candles, strategy, Context(pip_size, profile.broker_time is not None), profile.risk, spread_pips,
        initial_balance,
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
                wait = 0 if args.once else min(interval, POLL_SECONDS)
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
            time.sleep(max(interval - POLL_SECONDS, 0))


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


def _cmd_check_time(args: argparse.Namespace, root: Path) -> None:
    from .timecheck import check_time_report

    _confirm_real(args)
    profile = load_profile(args.profile, root)
    symbol = args.symbol or profile.symbols[0]
    with _mt5_client_class()(profile) as client:
        for line in check_time_report(client, profile, symbol, args.bars):
            print(line)


DEFAULT_OPT_SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "EURJPY"]


def _cmd_optimize(args: argparse.Namespace, root: Path) -> None:
    from .config import RiskConfig
    from .data import guess_pip_size, load_candles_csv, save_csv
    from .optimize import (
        SESSIONS, Criteria, combos_table, evaluate_symbol, finalize, pick_winners,
        render_report, winner_name,
    )
    from .risk import SymbolSpec

    _confirm_real(args)
    profile = load_profile(args.profile, root, require_credentials=not args.use_saved)
    if profile.broker_time is None and not args.no_session:
        raise ValueError(
            "Para probar el filtro de sesión hace falta `broker_time` en el perfil. Ejecuta "
            "`py -m forexplat check-time` y agrega la línea que sugiere (o usa --no-session)."
        )
    sessions = ["sin_filtro"] if args.no_session else list(SESSIONS)
    symbols = args.symbol or DEFAULT_OPT_SYMBOLS
    risk = RiskConfig(
        risk_per_trade=args.risk_per_trade, max_daily_loss=0.03, min_reward_risk=1.5,
        max_spread_pips=max(profile.risk.max_spread_pips, 2.0),
    )
    criteria = Criteria(alpha=args.alpha)
    reports, notes = [], []
    print(f"Pares: {', '.join(symbols)} | sesiones: {', '.join(sessions)}. Puede tardar varios minutos por par.")

    def run_symbol(symbol, candles, pip_size, spread):
        report = evaluate_symbol(
            symbol, candles, pip_size, spread, risk, criteria, sessions=sessions,
            times_are_utc=profile.broker_time is not None or args.no_session, log=print,
        )
        reports.append(report)

    def candles_path(symbol):
        return root / "data" / f"velas_{re.sub(r'[^A-Za-z0-9_.-]', '_', symbol)}_M5.csv"

    if args.use_saved:
        for symbol in symbols:
            path = candles_path(symbol)
            if not path.exists():
                notes.append(f"{symbol}: no hay {path.name} en data/ (se omite)")
                continue
            spread = (args.spread_pips if args.spread_pips is not None else profile.backtest.spread_pips)
            run_symbol(symbol, load_candles_csv(path), guess_pip_size(symbol), spread)
    else:
        with _mt5_client_class()(profile) as client:
            for symbol in symbols:
                try:
                    spec = SymbolSpec.from_summary(client.symbol_summary(symbol))
                    measured = spec.spread_points * spec.point / spec.pip_size
                    spread = (args.spread_pips if args.spread_pips is not None else measured + args.slack_pips)
                    candles = client.get_candles(symbol, "M5", args.bars)
                except RuntimeError as error:
                    notes.append(f"{symbol}: {error} (se omite)")
                    continue
                print(f"{symbol}: {len(candles)} velas M5, spread usado {spread:.2f} pips")
                save_csv(candles, candles_path(symbol))
                run_symbol(symbol, candles, spec.pip_size, spread)

    if not reports:
        raise ValueError("No se pudo evaluar ningún par. " + "; ".join(notes))
    need = finalize(reports, criteria)
    winners = pick_winners(reports)
    text = render_report(reports, winners, criteria, extra_notes=notes)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / "informe_m5.md"
    report_path.write_text(text, encoding="utf-8")
    save_csv(combos_table(reports), out_dir / "optimize_m5_finalistas.csv")
    print(f"\nInforme: {report_path}  (t exigido en la validación: {need:.2f})")
    if not winners:
        print("RESULTADO: ninguna candidata demostró ventaja fuera de muestra. No se escribió nada.")
        return
    for winner in winners:
        v = winner.combo.valid
        print(f"GANADORA: {winner_name(winner)} ({winner.symbol}) validación: {v['n']} ops, R medio {v['avg_r']:+.2f}, t {v['t']:.2f}")
    if not args.write:
        print("Para guardarlas en config/strategies.yaml agrega --write.")
        return
    from .ui import configio

    for winner in winners:
        backup = configio.save_strategy(root, winner_name(winner), winner.strategy_data)
        print(f"Guardada {winner_name(winner)}" + (f" (copia previa: {backup.name})" if backup else ""))
    names = [winner_name(w) for w in winners]
    print("\nPara usarlas, en config/demo.yaml pon:")
    print(f"  symbols: {sorted({w.symbol for w in winners})}\n  timeframe: M5\n  strategies: {names}")
    print(f"  risk: risk_per_trade: {args.risk_per_trade}, min_reward_risk: 1.5")
    print("(cada estrategia solo se evalúa en su par). Detén y reinicia la vigilancia después de cambiarlo.")


def _cmd_review(args: argparse.Namespace, root: Path) -> None:
    from .review import format_review, load_alerts, review_rows

    _confirm_real(args)
    profile = load_profile(args.profile, root)
    alerts = load_alerts(root, profile.mode.value)
    rows: list[dict] = []
    if alerts:
        with _mt5_client_class()(profile) as client:
            rows = review_rows(client, alerts)
    print(format_review(rows, profile.mode.value))


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
        elif args.command == "optimize":
            _cmd_optimize(args, root)
        elif args.command == "check-time":
            _cmd_check_time(args, root)
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
