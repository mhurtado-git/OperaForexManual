"""Empareja una alerta con la operación real que abriste en MetaTrader 5 (historial de transacciones)."""

from __future__ import annotations

import pandas as pd

DEAL_ENTRY_IN = 0
DEAL_ENTRY_OUT = (1, 3)  # salida normal o salida por posición contraria
DEAL_TYPE = {"long": 0, "short": 1}  # compra / venta
MAX_DELAY = pd.Timedelta(days=7)


def match_alert_deals(alert: dict, deals: pd.DataFrame, exclude_positions=()) -> dict | None:
    """Datos reales de la operación abierta tras la alerta, o None si no hay ninguna que encaje.

    `deals` trae una fila por transacción con: time, symbol, type, entry, volume, price, profit,
    commission, swap y position_id. Se toma la primera entrada del mismo par y dirección posterior
    a la señal (hasta 7 días), que no pertenezca a una posición ya asignada a otra alerta.
    """
    if deals is None or deals.empty:
        return None
    signal_time = pd.Timestamp(alert["candle_time"])
    if signal_time.tzinfo is None:
        signal_time = signal_time.tz_localize("UTC")
    wanted = DEAL_TYPE[alert["direction"]]
    entries = deals[
        (deals["symbol"] == alert["symbol"])
        & (deals["entry"] == DEAL_ENTRY_IN)
        & (deals["type"] == wanted)
        & (deals["time"] >= signal_time)
        & (deals["time"] <= signal_time + MAX_DELAY)
        & (~deals["position_id"].isin(list(exclude_positions)))
    ].sort_values("time")
    if entries.empty:
        return None

    position = entries.iloc[0]["position_id"]
    same = deals[deals["position_id"] == position]
    ins = same[same["entry"] == DEAL_ENTRY_IN]
    outs = same[same["entry"].isin(DEAL_ENTRY_OUT)]
    volume = float(ins["volume"].sum())
    average = float((ins["price"] * ins["volume"]).sum() / volume)
    closed = bool(len(outs)) and abs(float(outs["volume"].sum()) - volume) < 1e-9
    result = float((same["profit"] + same["commission"] + same["swap"]).sum()) if closed else None
    return {
        "real_entry": round(average, 6), "real_lots": volume, "real_result": result,
        "ticket": int(position), "closed": closed,
    }
