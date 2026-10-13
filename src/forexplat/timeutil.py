"""Horas: huso del servidor del broker y zonas horarias de las sesiones.

MetaTrader 5 entrega las velas en la hora del SERVIDOR del broker (no en UTC). Para filtrar por
sesión (por ejemplo Nueva York) hay que convertirlas a UTC primero. El perfil lo declara con
`broker_time`, en una de estas tres formas (exactamente una):

    broker_time: {utc_offset_hours: 3}        # servidor fijo en UTC+3 (sin cambio de horario)
    broker_time: {timezone: Europe/Athens}    # servidor que sigue una zona con su horario de verano
    broker_time: {ny_offset_hours: 7}         # servidor = hora de Nueva York + 7 h (muy habitual:
                                              # GMT+2 en invierno y GMT+3 en verano de EE. UU.)
"""

from __future__ import annotations

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import numpy as np
import pandas as pd
from pydantic import model_validator

from .models import StrictModel

NY = "America/New_York"


def get_zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError) as error:
        raise ValueError(
            f"Zona horaria desconocida: '{name}'. Usa nombres como America/New_York. "
            "En Windows ejecuta una vez: py -m pip install tzdata"
        ) from error


class BrokerTime(StrictModel):
    utc_offset_hours: float | None = None
    timezone: str | None = None
    ny_offset_hours: float | None = None

    @model_validator(mode="after")
    def _check(self):
        given = [
            name for name in ("utc_offset_hours", "timezone", "ny_offset_hours")
            if getattr(self, name) is not None
        ]
        if len(given) != 1:
            raise ValueError(
                "broker_time debe tener exactamente una de: utc_offset_hours, timezone, ny_offset_hours"
            )
        for name in ("utc_offset_hours", "ny_offset_hours"):
            value = getattr(self, name)
            if value is not None and not -14 <= value <= 14:
                raise ValueError(f"{name} fuera de rango (-14 a 14)")
        if self.timezone is not None:
            get_zone(self.timezone)
        return self

    def describe(self) -> str:
        if self.utc_offset_hours is not None:
            return f"servidor fijo en UTC{self.utc_offset_hours:+g}"
        if self.timezone is not None:
            return f"servidor en la zona {self.timezone}"
        return f"servidor = hora de Nueva York {self.ny_offset_hours:+g} h"

    def to_utc(self, times: pd.Series) -> pd.Series:
        """Convierte la hora del servidor (marcada como UTC por MT5) a UTC real."""
        naive = pd.to_datetime(times, utc=True).dt.tz_localize(None)
        if self.utc_offset_hours is not None:
            return (naive - pd.Timedelta(hours=self.utc_offset_hours)).dt.tz_localize("UTC")
        # En los cambios de horario hay una hora ambigua/inexistente: ocurre con el mercado
        # cerrado (domingo), así que basta con una elección razonable.
        if self.timezone is not None:
            zone, wall = self.timezone, naive
        else:
            zone, wall = NY, naive - pd.Timedelta(hours=self.ny_offset_hours)
        local = wall.dt.tz_localize(
            zone, ambiguous=np.ones(len(wall), dtype=bool), nonexistent="shift_forward"
        )
        return local.dt.tz_convert("UTC")


def verify_weekend_close(utc_times: pd.Series, bar_minutes: int) -> dict:
    """Comprueba la conversión con el cierre semanal: el mercado cierra el viernes a las 17:00 de
    Nueva York todo el año. Para cada semana, la última vela debe terminar a esa hora.

    Devuelve {"weeks": n, "ok": n, "bad": [(fecha, hora NY de fin de la última vela), ...]}.
    """
    times = pd.to_datetime(utc_times, utc=True).sort_values().reset_index(drop=True)
    gaps = times.diff().shift(-1)
    last_of_week = times[gaps > pd.Timedelta(hours=24)]
    ok, bad = 0, []
    for t in last_of_week:
        end_ny = (t + pd.Timedelta(minutes=bar_minutes)).tz_convert(NY)
        early = 17 * 60 - (end_ny.hour * 60 + end_ny.minute)  # minutos que faltan para las 17:00
        if end_ny.dayofweek == 4 and 0 <= early <= 10:  # algunos brokers cierran hasta 10 min antes
            ok += 1
        else:
            bad.append((end_ny.strftime("%Y-%m-%d"), end_ny.strftime("%a %H:%M")))
    return {"weeks": len(last_of_week), "ok": ok, "bad": bad}
