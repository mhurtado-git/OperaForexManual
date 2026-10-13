"""`check-time`: comprueba (o deduce) la hora del servidor del broker con el cierre semanal.

El mercado de divisas cierra cada viernes a las 17:00 de Nueva York, en verano y en invierno. Si la
conversión a UTC es correcta, la última vela de cada semana termina a esa hora; si el desfase está
mal (por ejemplo, se ignora el cambio de horario), unas semanas fallan. No hace falta fiarse de
suposiciones: se comprueba con los datos del propio broker.
"""

from __future__ import annotations

import pandas as pd

from .timeutil import BrokerTime, verify_weekend_close

H1_BARS = 9000   # ~1 año de velas H1: cubre los dos cambios de horario de EE. UU.
GOOD_SHARE = 0.9  # semanas correctas mínimas (hay festivos con cierres distintos)


def _candidates() -> list[tuple[str, BrokerTime]]:
    out = [(f"ny_offset_hours: {h}", BrokerTime(ny_offset_hours=h)) for h in range(0, 13)]
    out += [(f"utc_offset_hours: {h}", BrokerTime(utc_offset_hours=h)) for h in range(-12, 15)]
    return out


def _spans_both_seasons(utc_times: pd.Series) -> bool:
    ny = pd.to_datetime(utc_times, utc=True).dt.tz_convert("America/New_York")
    sample = ny.iloc[:: max(len(ny) // 200, 1)]
    flags = {bool(t.dst()) for t in sample}
    return len(flags) == 2


def check_time_report(client, profile, symbol: str, bars: int = H1_BARS) -> list[str]:
    lines: list[str] = []
    try:
        offset, raw = client.server_time_offset_hours(symbol)
        lines.append(
            f"Último precio de {symbol}: el servidor va a UTC{offset:+g} aproximadamente "
            f"(diferencia medida {raw / 3600:+.2f} h)."
        )
        lines.append("  Esta medida solo es fiable con el mercado abierto (lunes a viernes).")
    except Exception as error:  # noqa: BLE001
        lines.append(f"No se pudo medir el desfase con el último precio: {error}")

    candles = client.get_candles(symbol, "H1", bars)
    times = candles["time"]
    lines.append(
        f"Velas H1 revisadas: {len(candles)} ({times.iloc[0]:%Y-%m-%d} a {times.iloc[-1]:%Y-%m-%d})."
    )

    def judge(label: str, series: pd.Series) -> tuple[bool, dict]:
        report = verify_weekend_close(series, 60)
        return report["weeks"] > 0 and report["ok"] / report["weeks"] >= GOOD_SHARE, report

    if profile.broker_time is not None:
        ok, report = judge("perfil", times)
        lines.append(f"Perfil: broker_time = {profile.broker_time.describe()}.")
        lines.append(f"Cierres semanales correctos: {report['ok']} de {report['weeks']}.")
        if report["bad"]:
            lines.append("  Semanas que no cuadran: " + ", ".join(f"{d} ({h})" for d, h in report["bad"][:8]))
        if not _spans_both_seasons(times):
            lines.append("  AVISO: estas velas no cubren un cambio de horario; no se prueba el verano/invierno completo.")
        lines.append("RESULTADO: la hora del servidor está bien configurada." if ok else
                     "RESULTADO: la configuración NO cuadra con el cierre del mercado. No uses session_filter hasta corregirla.")
        return lines

    lines.append("El perfil no tiene broker_time. Probando configuraciones con el cierre semanal...")
    passing = []
    for label, broker_time in _candidates():
        ok, report = judge(label, broker_time.to_utc(times))
        if ok:
            passing.append(label)
    if not passing:
        lines.append("Ninguna configuración sencilla cuadra. Prueba con `broker_time: {timezone: <zona>}` "
                     "(por ejemplo Europe/Athens) o revisa que haya velas suficientes.")
        return lines
    ny = [p for p in passing if p.startswith("ny_offset")]
    fixed = [p for p in passing if p.startswith("utc_offset")]
    if ny and not fixed:
        lines.append(f"Configuración que cuadra: broker_time: {{{ny[0]}}}")
        lines.append("Agrégala a config/demo.yaml (y a real.yaml) y vuelve a ejecutar `check-time` para confirmarla.")
    elif ny and fixed:
        lines.append("Cuadran varias, porque estas velas cubren solo parte del año:")
        lines.extend(f"  broker_time: {{{label}}}" for label in ny[:2] + fixed[:2])
        lines.append("Si tu broker cambia su hora en verano (lo habitual), usa la de ny_offset_hours.")
    else:
        lines.append("Cuadran solo desfases fijos (el servidor no sigue el horario de verano de EE. UU.):")
        lines.extend(f"  broker_time: {{{label}}}" for label in fixed[:3])
    return lines
