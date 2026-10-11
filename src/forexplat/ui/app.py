"""Interfaz gráfica local (Streamlit). Se lanza con:  py -m forexplat ui

Solo dibuja pantallas: toda la lógica está en `services.py` y en los módulos del proyecto.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

import streamlit as st

# Importes absolutos: Streamlit ejecuta este archivo como script, no como parte del paquete.
from forexplat.alerts import format_scan
from forexplat.config import ROOT, VALID_TIMEFRAMES, load_profile
from forexplat.strategy import load_strategies
from forexplat.ui import services
from forexplat.ui.watchproc import WatchController


def _state(key: str, default=None):
    if key not in st.session_state:
        st.session_state[key] = default
    return st.session_state[key]


def _show_error(error: Exception, profile_name: str, root: Path) -> None:
    secrets: list[str] = []
    try:
        secrets = services._secrets(load_profile(profile_name, root))
    except Exception:  # noqa: BLE001 - sin perfil no hay secretos que ocultar
        pass
    st.error(services.friendly_error(error, secrets))


# --- Inicio -------------------------------------------------------------------

def _home(profile_name: str, root: Path, controller: WatchController) -> None:
    if profile_name == "real":
        st.error("PERFIL REAL: cuenta con dinero real. Revisa cada paso antes de operar.")
    else:
        st.success("Perfil DEMO: cuenta de práctica.")

    if st.button("Actualizar estado", key="refresh_status"):
        with st.spinner("Consultando MetaTrader 5..."):
            st.session_state["status"] = services.get_status(profile_name, root)
    status = _state("status")
    if status is None:
        st.info("Pulsa «Actualizar estado» para comprobar MetaTrader 5 y Telegram.")
    else:
        if status["mt5_ok"]:
            account = status["account"]
            st.success(f"MetaTrader 5: conectado a la cuenta {account['login']} ({account['kind']})")
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Saldo", f"{account['balance']:.2f} {account['currency']}")
            with col2:
                st.metric("Servidor", str(account["server"]))
            with col3:
                st.metric("Apalancamiento", f"1:{account['leverage']}")
        else:
            st.warning(f"MetaTrader 5: {status['mt5_message']}")
        st.write(f"Telegram: {status['telegram']}")
        if status["symbols"]:
            st.write(
                f"Pares: {', '.join(status['symbols'])} | Temporalidad: {status['timeframe']} | "
                f"Estrategias: {', '.join(status['strategies'])}"
            )
    st.write("Vigilancia: " + ("ACTIVA" if controller.is_running() else "detenida"))


# --- Señales ------------------------------------------------------------------

def _signals(profile_name: str, root: Path) -> None:
    try:
        profile = load_profile(profile_name, root, require_credentials=False)
        names = list(load_strategies(root))
    except Exception as error:  # noqa: BLE001
        _show_error(error, profile_name, root)
        return
    chosen = st.multiselect(
        "Estrategias", names, default=[n for n in profile.strategies if n in names], key="sig_strategies"
    )
    symbols = st.multiselect("Pares", profile.symbols, default=profile.symbols, key="sig_symbols")
    if st.button("Revisar ahora", key="run_signals"):
        if not chosen or not symbols:
            st.warning("Elige al menos una estrategia y un par.")
            return
        try:
            with st.spinner("Revisando la última vela cerrada..."):
                _, results = services.run_signals(profile_name, root, chosen, symbols)
        except Exception as error:  # noqa: BLE001
            _show_error(error, profile_name, root)
            return
        for result in results:
            st.code(format_scan(result, profile.mode.value), language=None)


# --- Backtest -----------------------------------------------------------------

def _backtest(profile_name: str, root: Path) -> None:
    try:
        profile = load_profile(profile_name, root, require_credentials=False)
        names = list(load_strategies(root))
    except Exception as error:  # noqa: BLE001
        _show_error(error, profile_name, root)
        return

    default_strategy = profile.strategies[0] if profile.strategies[0] in names else names[0]
    strategy = st.selectbox("Estrategia", names, index=names.index(default_strategy), key="bt_strategy")
    col1, col2 = st.columns(2)
    with col1:
        symbol = st.selectbox("Par", profile.symbols, key="bt_symbol")
        timeframe = st.selectbox(
            "Temporalidad", list(VALID_TIMEFRAMES), index=list(VALID_TIMEFRAMES).index(profile.timeframe),
            key="bt_timeframe",
        )
        source = st.radio("Origen de las velas", ["MetaTrader 5", "Archivo CSV"], key="bt_source")
    with col2:
        bars = st.number_input(
            "Velas a descargar", min_value=200, max_value=100000, value=profile.backtest.bars, step=500,
            key="bt_bars",
        )
        spread = st.number_input(
            "Spread simulado (pips)", min_value=0.0, value=float(profile.backtest.spread_pips), step=0.1,
            key="bt_spread",
        )
        balance = st.number_input(
            "Saldo inicial", min_value=100.0, value=float(profile.backtest.initial_balance), step=1000.0,
            key="bt_balance",
        )
    uploaded = st.file_uploader("CSV de velas (time, open, high, low, close)", type=["csv"], key="bt_csv")
    split = st.checkbox("Separar periodo de ajuste y de validación", value=True, key="bt_split")
    ratio = 0.7
    if split:
        ratio = st.slider("Parte más antigua para ajustar (%)", 50, 90, 70, key="bt_ratio") / 100

    if st.button("Ejecutar backtest", key="run_backtest"):
        try:
            with st.spinner("Simulando..."):
                csv_path = None
                if source == "Archivo CSV":
                    if uploaded is None:
                        st.warning("Sube primero un archivo CSV.")
                        return
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".csv") as handle:
                        handle.write(uploaded.getvalue())
                        csv_path = handle.name
                candles, pip = services.load_backtest_candles(
                    profile_name, root, symbol, timeframe, int(bars), csv_path=csv_path
                )
                output = services.run_backtest_ui(
                    profile_name, root, candles, pip, strategy, float(spread), float(balance),
                    ratio if split else None,
                )
        except Exception as error:  # noqa: BLE001
            _show_error(error, profile_name, root)
            return
        runs = _state("bt_runs", {})
        key = f"{symbol}|{timeframe}"
        runs[key] = runs.get(key, 0) + 1
        st.session_state["bt_output"] = {**output, "label": f"{strategy} | {symbol} {timeframe}", "runs": runs[key]}

    output = st.session_state.get("bt_output")
    if not output:
        return
    result = output["result"]
    st.subheader(output["label"])
    if output["runs"] >= services.MANY_VARIANTS:
        st.warning(
            f"Llevas {output['runs']} backtests sobre este mismo par y temporalidad en esta sesión. "
            "Cuantas más variantes pruebas sobre los mismos datos, más probable es encontrar un buen "
            "resultado por azar: confirma siempre con datos que no usaste para elegir."
        )
    for warning in output["warnings"]:
        st.warning(warning)

    columns = {"Completo": result.metrics}
    if output["fit"] is not None:
        columns = {"Ajuste": output["fit"], "Validación": output["valid"], "Completo": result.metrics}
        st.caption(f"Corte entre ajuste y validación: {output['cut_time']:%Y-%m-%d}")
    st.dataframe(services.metrics_table(**columns))
    st.write(
        f"Señales: {result.signals} | descartadas por el gestor de riesgo: {sum(result.skipped.values())}"
    )
    if len(output["equity"]):
        st.line_chart(output["equity"].set_index("time")["balance"])
    if len(result.trades):
        st.dataframe(result.trades)
        st.download_button(
            "Descargar operaciones (CSV)", data=result.trades.to_csv(index=False).encode("utf-8"),
            file_name=f"backtest_{strategy}_{symbol}_{timeframe}.csv", mime="text/csv", key="bt_download",
        )
    st.caption(
        "Un buen backtest no garantiza resultados futuros. Valida siempre en demo antes de usar dinero real."
    )


# --- Vigilancia ---------------------------------------------------------------

def _watch(profile_name: str, root: Path, controller: WatchController) -> None:
    running = controller.is_running()
    st.write("Estado: " + ("ACTIVA" if running else "detenida"))
    if profile_name != "demo":
        st.info("En el perfil real la vigilancia se inicia desde la consola, con la confirmación habitual.")
    else:
        col1, col2 = st.columns(2)
        with col1:
            if st.button("Iniciar vigilancia", key="watch_start", disabled=running):
                try:
                    controller.start()
                    st.success("Vigilancia iniciada. Sigue funcionando aunque cierres esta pestaña.")
                except Exception as error:  # noqa: BLE001
                    _show_error(error, profile_name, root)
        with col2:
            if st.button("Detener vigilancia", key="watch_stop", disabled=not running):
                controller.stop()
                st.success("Vigilancia detenida.")
    st.button("Actualizar registro", key="watch_refresh")
    st.caption("Últimas líneas del registro (si MetaTrader 5 está cerrado, aquí verás el motivo):")
    st.code(controller.log_tail(40) or "(sin registro todavía)", language=None)


# --- Entrada ------------------------------------------------------------------

def render(profile_name: str, root: Path) -> None:
    st.set_page_config(page_title="Operación Forex manual", layout="wide")
    st.title("Operación manual en Forex")
    controller = WatchController(root, profile_name)
    tab_home, tab_signals, tab_backtest, tab_watch = st.tabs(["Inicio", "Señales", "Backtest", "Vigilancia"])
    with tab_home:
        _home(profile_name, root, controller)
    with tab_signals:
        _signals(profile_name, root)
    with tab_backtest:
        _backtest(profile_name, root)
    with tab_watch:
        _watch(profile_name, root, controller)


def _main() -> None:  # lo ejecuta `streamlit run`
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default="demo", choices=("demo", "real"))
    args, _ = parser.parse_known_args(sys.argv[1:])
    render(args.profile, Path(os.environ.get("FOREXPLAT_ROOT", ROOT)))


if __name__ == "__main__":
    _main()
