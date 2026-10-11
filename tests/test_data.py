import pandas as pd
import pytest

from forexplat.data import guess_pip_size, load_candles_csv, save_csv
from helpers import random_candles


def write(tmp_path, frame, name="velas.csv"):
    path = tmp_path / name
    frame.to_csv(path, index=False)
    return path


def test_roundtrip_through_csv(tmp_path):
    original = random_candles(120, seed=2)
    path = save_csv(original, tmp_path / "sub" / "velas.csv")  # crea la carpeta
    loaded = load_candles_csv(path)
    assert len(loaded) == 120
    assert loaded["time"].dt.tz is not None
    assert (loaded["close"] - original["close"]).abs().max() < 1e-9


def test_headers_are_case_insensitive_and_data_is_sorted_and_deduplicated(tmp_path):
    frame = random_candles(100, seed=4)
    frame = pd.concat([frame, frame.iloc[[5]]]).sample(frac=1, random_state=1)
    frame.columns = [column.upper() for column in frame.columns]
    loaded = load_candles_csv(write(tmp_path, frame))
    assert len(loaded) == 100
    assert loaded["time"].is_monotonic_increasing


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda f: f.drop(columns=["high"]), "faltan las columnas"),
        (lambda f: f.assign(close=f["close"].astype(object).where(f.index != 3, "abc")), "no se pueden leer"),
        (lambda f: f.assign(time="no es una fecha"), "no se pueden leer"),
        (lambda f: f.assign(high=f["low"] - 0.001), "máximo menor que el mínimo"),
        (lambda f: f.assign(open=f["open"].where(f.index != 7)), "precios vacíos"),
        (lambda f: f.iloc[:30], "al menos 60"),
    ],
)
def test_invalid_csv_is_rejected_with_clear_message(tmp_path, mutate, message):
    frame = mutate(random_candles(100, seed=1))
    with pytest.raises(ValueError, match=message):
        load_candles_csv(write(tmp_path, frame))


def test_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError, match="No existe"):
        load_candles_csv(tmp_path / "nada.csv")


def test_guess_pip_size():
    assert guess_pip_size("EURUSD") == 0.0001
    assert guess_pip_size("usdjpy") == 0.01
    assert guess_pip_size("EURJPY.m") == 0.01
