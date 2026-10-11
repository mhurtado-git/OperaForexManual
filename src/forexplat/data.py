"""Lectura y escritura de velas en CSV (para backtests sin conexión a MetaTrader 5)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

REQUIRED_COLUMNS = ("time", "open", "high", "low", "close")
MIN_CANDLES = 60


def guess_pip_size(symbol: str) -> float:
    """Pip habitual del símbolo cuando no hay MT5 para preguntarlo (JPY: 0.01; resto: 0.0001)."""
    return 0.01 if "JPY" in symbol.upper() else 0.0001


def load_candles_csv(path: str | Path) -> pd.DataFrame:
    """Carga velas desde un CSV con columnas time, open, high, low, close (y opcionalmente tick_volume).

    Es el mismo formato que escribe `backtest --save-candles`.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"No existe el archivo {path}")
    frame = pd.read_csv(path)
    frame.columns = [str(column).strip().lower() for column in frame.columns]
    missing = [column for column in REQUIRED_COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError(
            f"Al CSV le faltan las columnas {missing}. Se esperan: {list(REQUIRED_COLUMNS)}"
        )
    try:
        frame["time"] = pd.to_datetime(frame["time"], utc=True)
        for column in ("open", "high", "low", "close"):
            frame[column] = pd.to_numeric(frame[column], errors="raise")
    except (ValueError, TypeError) as error:
        raise ValueError(f"El CSV tiene fechas o precios que no se pueden leer: {error}") from error

    frame = frame.sort_values("time").drop_duplicates("time").reset_index(drop=True)
    if frame[list(REQUIRED_COLUMNS[1:])].isna().any().any():
        raise ValueError("El CSV tiene precios vacíos")
    if (frame["high"] < frame["low"]).any():
        raise ValueError("El CSV tiene velas con máximo menor que el mínimo")
    if len(frame) < MIN_CANDLES:
        raise ValueError(f"Se necesitan al menos {MIN_CANDLES} velas y el CSV tiene {len(frame)}")
    return frame


def save_csv(frame: pd.DataFrame, path: str | Path) -> Path:
    """Guarda velas u operaciones en un CSV (crea la carpeta si no existe)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)
    return path
