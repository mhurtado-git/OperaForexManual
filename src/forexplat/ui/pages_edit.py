"""Pestañas de edición: Diario, Estrategias y Perfil (dibujan; la lógica está en configio.py y services.py)."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from forexplat.config import VALID_TIMEFRAMES, BacktestConfig, RiskConfig
from forexplat.review import format_review
from forexplat.risk import StopLossConfig, TakeProfitConfig
from forexplat.ui import configio, services
from forexplat.ui.forms import WEEKDAYS, FieldSpec, fields_for


def _state(key: str, default=None):
    if key not in st.session_state:
        st.session_state[key] = default
    return st.session_state[key]


def _error(error: Exception, profile_name: str, root: Path) -> None:
    if isinstance(error, configio.ConfigError):
        st.error(str(error))
    else:
        st.error(services.friendly_error(error, services.safe_secrets(profile_name, root)))


def render_field(spec: FieldSpec, value, key: str):
    """Dibuja el control de un campo y devuelve el valor elegido."""
    label = spec.label
    if value is None:
        value = spec.default
    if spec.kind == "choice":
        options = list(spec.options)
        return st.selectbox(label, options, index=options.index(value) if value in options else 0, key=key)
    if spec.kind == "days":
        chosen = st.multiselect(
            label, list(spec.options), default=[d for d in (value or []) if d in spec.options],
            format_func=lambda d: WEEKDAYS[d], key=key,
        )
        return sorted(chosen)
    if spec.kind in ("int", "opt_int"):
        if spec.kind == "opt_int" and not st.checkbox(f"{label}: usar", value=value is not None, key=key + ":on"):
            return None
        low = None if spec.min is None else int(spec.min)
        high = None if spec.max is None else int(spec.max)
        start = int(value if value is not None else (low if low is not None else 1))
        return int(st.number_input(label, min_value=low, max_value=high, value=start, step=1, key=key))
    if spec.kind in ("float", "opt_float"):
        if spec.kind == "opt_float" and not st.checkbox(f"{label}: usar", value=value is not None, key=key + ":on"):
            return None
        low = None if spec.min is None else float(spec.min)
        high = None if spec.max is None else float(spec.max)
        fraction = high is not None and high <= 1.0 or spec.name in ("zone_start", "zone_end")
        start = float(value if value is not None else max(low or 0.0, 10.0))
        return float(
            st.number_input(
                label, min_value=low, max_value=high, value=start,
                step=0.005 if fraction else 0.1, format="%.4f" if fraction else "%.2f", key=key,
            )
        )
    return st.text_input(label, value="" if value is None else str(value), key=key)


def _render_fields(model, values: dict, prefix: str, skip=("type", "name", "description")) -> dict:
    return {
        spec.name: render_field(spec, values.get(spec.name), f"{prefix}:{spec.name}")
        for spec in fields_for(model, skip=skip)
    }


# --- Estrategias ----------------------------------------------------------------

def strategies_tab(profile_name: str, root: Path) -> None:
    try:
        stored = configio.read_strategy_dicts(root)
    except Exception as error:  # noqa: BLE001
        _error(error, profile_name, root)
        return
    names = list(stored)
    name = st.selectbox("Estrategia", names, key="st_name")
    used = configio.profiles_using(root, name)
    st.write("Se usa en: " + (", ".join(used) if used else "ningún perfil"))
    if "real" in used:
        st.warning("Esta estrategia está activa en el perfil REAL: los cambios exigen confirmación.")

    version = _state("st_version", 0)
    draft_key = f"st_draft:{name}"
    if draft_key not in st.session_state:
        st.session_state[draft_key] = configio.normalized(configio.validate_strategy(stored[name]))
    draft = st.session_state[draft_key]
    prefix = f"{version}:{name}"

    draft["description"] = st.text_input("Descripción", value=draft.get("description", ""), key=f"{prefix}:desc")

    st.subheader("Reglas (indicadores)")
    rules = draft["rules"]
    for index, rule in enumerate(rules):
        rule_type = rule["type"]
        model = configio.RULE_TYPES[rule_type]
        title = f"{index + 1}. {rule.get('name') or rule_type} ({rule_type})"
        with st.expander(title, expanded=index == 0):
            rule["name"] = st.text_input(
                "Nombre de la regla", value=rule.get("name") or rule_type, key=f"{prefix}:r{index}:name"
            )
            values = _render_fields(model, rule, f"{prefix}:r{index}")
            rules[index] = {"type": rule_type, "name": rule["name"], **values}
            if len(rules) > 1 and st.button("Quitar esta regla", key=f"{prefix}:r{index}:del"):
                rules.pop(index)
                st.session_state["st_version"] = version + 1
                st.rerun()
    col1, col2 = st.columns(2)
    with col1:
        new_type = st.selectbox("Tipo de regla a añadir", list(configio.RULE_TYPES), key=f"{prefix}:newtype")
    with col2:
        if st.button("Añadir regla", key=f"{prefix}:add"):
            taken = {r.get("name") for r in rules}
            unique = new_type if new_type not in taken else f"{new_type}_{len(rules) + 1}"
            rules.append({"type": new_type, "name": unique, **configio.RULE_TYPES[new_type](type=new_type, name=unique).model_dump(exclude_none=True, exclude={"type", "name"})})
            st.session_state["st_version"] = version + 1
            st.rerun()

    st.subheader("Disparador y confirmaciones")
    rule_names = [r["name"] for r in rules]
    trigger = draft.get("trigger")
    draft["trigger"] = st.selectbox(
        "Regla que dispara la señal (debe ser un evento: cruce, ruptura, giro)", rule_names,
        index=rule_names.index(trigger) if trigger in rule_names else 0, key=f"{prefix}:trigger",
    )
    confirmable = max(sum(1 for r in rules if r["type"] != "session_filter") - 1, 0)
    all_needed = st.checkbox(
        "Exigir todas las confirmaciones", value=draft.get("min_confirmations") is None, key=f"{prefix}:all"
    )
    if all_needed:
        draft.pop("min_confirmations", None)
    else:
        draft["min_confirmations"] = int(
            st.number_input(
                "Confirmaciones mínimas", min_value=0, max_value=confirmable,
                value=min(int(draft.get("min_confirmations") or 0), confirmable), step=1,
                key=f"{prefix}:minconf",
            )
        )

    st.subheader("Stop loss")
    draft["stop_loss"] = _render_fields(StopLossConfig, draft["stop_loss"], f"{prefix}:sl")
    st.subheader("Take profit")
    draft["take_profit"] = _render_fields(TakeProfitConfig, draft["take_profit"], f"{prefix}:tp")

    confirm = ""
    if "real" in used:
        confirm = st.text_input("Escribe REAL para confirmar", key=f"{prefix}:confirm")
    st.caption(
        "Al guardar, el encabezado de strategies.yaml se conserva, los comentarios internos se sustituyen por la "
        "descripción y la versión anterior queda en config/backups/."
    )
    if st.button("Guardar estrategia", key=f"{prefix}:save"):
        try:
            backup = configio.save_strategy(root, name, draft, confirm_real=confirm)
            st.success(f"Estrategia guardada. Copia anterior: {backup.name if backup else '-'}")
            st.info("Si la vigilancia está activa, deténla y vuélvela a iniciar para que use los cambios.")
        except Exception as error:  # noqa: BLE001
            _error(error, profile_name, root)

    st.subheader("Duplicar")
    copy_name = st.text_input("Nombre de la copia", key=f"{prefix}:copyname")
    if st.button("Duplicar estrategia", key=f"{prefix}:dup"):
        try:
            configio.duplicate_strategy(root, name, copy_name.strip())
            st.success(f"Creada '{copy_name.strip()}'. Selecciónala arriba para editarla.")
        except Exception as error:  # noqa: BLE001
            _error(error, profile_name, root)


# --- Perfil y riesgo -----------------------------------------------------------

def profile_tab(profile_name: str, root: Path) -> None:
    try:
        current = configio.read_profile_dict(root, profile_name)
        available = list(configio.read_strategy_dicts(root))
    except Exception as error:  # noqa: BLE001
        _error(error, profile_name, root)
        return
    if profile_name == "real":
        st.error("Estás editando el perfil REAL (dinero real).")
    prefix = f"pf:{profile_name}"

    symbols_text = st.text_input(
        "Pares (separados por coma, con el nombre exacto de tu broker)",
        value=", ".join(current.get("symbols", [])), key=f"{prefix}:symbols",
    )
    timeframes = list(VALID_TIMEFRAMES)
    timeframe = st.selectbox(
        "Temporalidad", timeframes,
        index=timeframes.index(current.get("timeframe", "H1")) if current.get("timeframe", "H1") in timeframes else 4,
        key=f"{prefix}:tf",
    )
    bars = int(st.number_input("Velas por ciclo", min_value=50, max_value=5000, value=int(current.get("bars", 200)),
                               step=50, key=f"{prefix}:bars"))
    strategies = st.multiselect(
        "Estrategias activas", available,
        default=[s for s in current.get("strategies", []) if s in available], key=f"{prefix}:strategies",
    )
    st.subheader("Riesgo")
    risk = _render_fields(RiskConfig, current.get("risk", {}), f"{prefix}:risk")
    st.subheader("Backtest")
    backtest = _render_fields(BacktestConfig, current.get("backtest", {}), f"{prefix}:bt")

    confirm = ""
    if profile_name == "real":
        confirm = st.text_input("Escribe REAL para confirmar", key=f"{prefix}:confirm")
    if st.button("Guardar perfil", key=f"{prefix}:save"):
        symbols = [s.strip() for s in symbols_text.split(",") if s.strip()]
        try:
            backup = configio.save_profile(
                root, profile_name,
                {"symbols": symbols, "timeframe": timeframe, "bars": bars, "strategies": strategies,
                 "risk": risk, "backtest": backtest},
                confirm_real=confirm,
            )
            st.success(f"Perfil guardado. Copia anterior: {backup.name if backup else '-'}")
            st.info("Si la vigilancia está activa, deténla y vuélvela a iniciar para que use los cambios.")
        except Exception as error:  # noqa: BLE001
            _error(error, profile_name, root)


# --- Diario ---------------------------------------------------------------------

def _alert_label(alert: dict) -> str:
    direction = "largo" if alert["direction"] == "long" else "corto"
    return f"{str(alert['candle_time'])[:16]} | {alert['symbol']} {direction} | {alert.get('decision') or 'sin decisión'}"


def _table(rows: list[dict]):
    import pandas as pd

    return pd.DataFrame([
        {
            "Vela": str(r["candle_time"])[:16], "Par": r["symbol"],
            "Dirección": "largo" if r["direction"] == "long" else "corto", "Estrategia": r["strategy"],
            "Decisión": r.get("decision") or "sin decisión", "Resultado": r.get("status", ""),
            "R teórico": None if r.get("r") is None else round(r["r"], 2), "Nota": r.get("note", ""),
            "Entrada real": r.get("real_entry", ""), "Lote real": r.get("real_lots", ""),
            "Resultado real": r.get("real_result", ""),
        }
        for r in rows
    ])


def journal_tab(profile_name: str, root: Path) -> None:
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Actualizar diario", key="jn_load"):
            st.session_state["jn_rows"] = services.load_journal_alerts(root, profile_name)
    with col2:
        if st.button("Calcular resultados (usa MetaTrader 5)", key="jn_calc"):
            try:
                with st.spinner("Descargando velas..."):
                    st.session_state["jn_rows"] = services.compute_review(profile_name, root)
            except Exception as error:  # noqa: BLE001
                _error(error, profile_name, root)
    rows = _state("jn_rows")
    if rows is None:
        rows = services.load_journal_alerts(root, profile_name)
        st.session_state["jn_rows"] = rows
    if not rows:
        st.info("Todavía no hay alertas. Deja la vigilancia activa y vuelve cuando haya recibido alguna.")
        return

    st.dataframe(_table(rows))
    if any("status" in r for r in rows):
        st.code(format_review(rows, profile_name), language=None)
        st.caption("Resultados teóricos con los niveles del aviso; no son las órdenes reales de tu cuenta.")

    st.subheader("Registrar decisión, nota y datos reales")
    labels = [_alert_label(r) for r in rows]
    chosen = st.selectbox("Alerta", labels, key="jn_alert")
    alert = rows[labels.index(chosen)]
    version = _state("jn_version", 0)
    found = st.session_state.get("jn_found", {}).get(alert["alert_id"], {})
    prefix = f"{version}:{alert['alert_id']}"

    options = ["sin decisión", "tomada", "descartada"]
    current = alert.get("decision") or "sin decisión"
    decision = st.selectbox("Decisión", options, index=options.index(current), key=f"{prefix}:dec")
    note = st.text_input("Nota (por qué la tomaste o la descartaste)", value=alert.get("note", ""), key=f"{prefix}:note")

    if st.button("Buscar en el historial de MetaTrader 5", key=f"{prefix}:find"):
        try:
            match = services.find_real_trade(profile_name, root, alert)
        except Exception as error:  # noqa: BLE001
            _error(error, profile_name, root)
            match = None
        else:
            if match is None:
                st.warning("No encontré una operación que encaje (mismo par y dirección, después de la alerta).")
            else:
                st.session_state.setdefault("jn_found", {})[alert["alert_id"]] = match
                st.session_state["jn_version"] = version + 1
                st.rerun()

    def initial(key):
        value = found.get(key, alert.get(key, ""))
        return "" if value in (None, "") else value

    if found:
        st.success("Datos tomados del historial de MetaTrader 5. Revísalos y guarda.")
    real_entry = st.text_input("Precio real de entrada", value=str(initial("real_entry")), key=f"{prefix}:re")
    real_lots = st.text_input("Lote real", value=str(initial("real_lots")), key=f"{prefix}:rl")
    real_result = st.text_input(
        "Resultado real (dinero, vacío si sigue abierta)", value=str(initial("real_result")), key=f"{prefix}:rr"
    )
    ticket = st.text_input("Ticket / posición", value=str(initial("ticket")), key=f"{prefix}:tk")

    if st.button("Guardar en el diario", key=f"{prefix}:save"):
        try:
            for label, value in (("precio", real_entry), ("lote", real_lots), ("resultado", real_result)):
                if value.strip():
                    float(value.replace(",", "."))
            services.save_decision(
                root, profile_name, alert, None if decision == "sin decisión" else decision, note,
                real_entry.replace(",", "."), real_lots.replace(",", "."), real_result.replace(",", "."), ticket,
            )
            st.session_state["jn_rows"] = services.load_journal_alerts(root, profile_name)
            st.success("Guardado.")
        except ValueError:
            st.error("El precio, el lote y el resultado deben ser números (usa punto o coma para decimales).")
        except Exception as error:  # noqa: BLE001
            _error(error, profile_name, root)
