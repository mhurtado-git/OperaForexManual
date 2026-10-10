"""Carga y validación de perfiles (demo / real).

Cada perfil usa su propio YAML (config/<perfil>.yaml) y su propio archivo de
credenciales (.env.<perfil>). Nunca se leen credenciales del entorno global,
para que demo y real no se mezclen por accidente.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path

import yaml
from dotenv import dotenv_values
from pydantic import BaseModel, Field, SecretStr, field_validator

ROOT = Path(__file__).resolve().parents[2]
VALID_TIMEFRAMES = ("M1", "M5", "M15", "M30", "H1", "H4", "D1")


class Mode(str, Enum):
    DEMO = "demo"
    REAL = "real"


class RiskConfig(BaseModel):
    risk_per_trade: float = Field(
        gt=0, le=0.05, description="Fracción del saldo que se acepta perder por operación"
    )
    max_daily_loss: float = Field(
        gt=0, le=0.20, description="Pérdida diaria máxima como fracción del saldo"
    )
    min_reward_risk: float = Field(
        ge=1.0, description="Relación beneficio/riesgo mínima (TP frente a SL)"
    )
    max_spread_pips: float = Field(
        gt=0, description="Spread máximo aceptado para emitir una alerta"
    )


class Mt5Credentials(BaseModel):
    login: int
    password: SecretStr
    server: str = Field(min_length=1)
    path: str | None = None


class Profile(BaseModel):
    mode: Mode
    symbols: list[str] = Field(min_length=1)
    timeframe: str = "H1"
    bars: int = Field(default=200, ge=50, le=5000)
    risk: RiskConfig
    mt5: Mt5Credentials

    @field_validator("symbols")
    @classmethod
    def _clean_symbols(cls, value: list[str]) -> list[str]:
        # No se cambian mayúsculas: algunos brokers usan sufijos como "EURUSD.m".
        cleaned = [symbol.strip() for symbol in value]
        if any(not symbol for symbol in cleaned):
            raise ValueError("Hay un símbolo vacío en la lista")
        return cleaned

    @field_validator("timeframe")
    @classmethod
    def _valid_timeframe(cls, value: str) -> str:
        value = value.strip().upper()
        if value not in VALID_TIMEFRAMES:
            raise ValueError(f"Temporalidad no válida: {value}. Usa una de {VALID_TIMEFRAMES}")
        return value


def load_profile(name: str, root: Path = ROOT) -> Profile:
    """Carga el perfil `name` ("demo" o "real") desde `root`."""
    try:
        expected_mode = Mode(name)
    except ValueError:
        raise ValueError(f"Perfil desconocido: '{name}'. Usa 'demo' o 'real'.") from None

    yaml_path = root / "config" / f"{name}.yaml"
    env_path = root / f".env.{name}"

    if not yaml_path.exists():
        raise FileNotFoundError(f"No existe {yaml_path}")
    if not env_path.exists():
        raise FileNotFoundError(
            f"Falta {env_path.name}. Copia {env_path.name}.example, renómbralo y completa tus datos."
        )

    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    env = dotenv_values(env_path)
    data["mt5"] = {
        "login": env.get("MT5_LOGIN"),
        "password": env.get("MT5_PASSWORD"),
        "server": env.get("MT5_SERVER"),
        "path": env.get("MT5_PATH") or None,
    }

    profile = Profile.model_validate(data)
    if profile.mode is not expected_mode:
        raise ValueError(
            f"{yaml_path.name} dice mode='{profile.mode.value}' pero se cargó como perfil '{name}'."
        )
    return profile
