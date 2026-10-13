"""Edición segura de strategies.yaml y de los perfiles desde la interfaz.

Cada guardado: valida con los mismos modelos que usa el programa, hace copia en config/backups/,
escribe de forma atómica, vuelve a cargar para comprobar y, si algo falla, restaura la copia.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import yaml
from pydantic import ValidationError

from ..config import Profile, load_profile
from ..risk import StopLossConfig, TakeProfitConfig
from ..rules import (
    AtrFilterRule, BollingerRule, FibonacciRule, MacdRule, MaCrossRule, RsiRule, SessionFilterRule,
    StochasticRule,
)
from ..safeio import atomic_write_text
from ..strategy import NAME_RE, StrategyConfig, load_strategies

RULE_TYPES = {
    "ma_cross": MaCrossRule, "rsi": RsiRule, "macd": MacdRule, "bollinger": BollingerRule,
    "stochastic": StochasticRule, "fibonacci": FibonacciRule, "atr_filter": AtrFilterRule,
    "session_filter": SessionFilterRule,
}
PROFILES = ("demo", "real")
RISK_KEYS = ("risk_per_trade", "max_daily_loss", "min_reward_risk", "max_spread_pips")
BACKTEST_KEYS = ("bars", "spread_pips", "initial_balance")
COMMENTS = {
    "bars": "velas cerradas que se leen en cada ciclo", "symbols": "usa el nombre exacto de tu broker",
    "timeframe": "M1, M5, M15, M30, H1, H4, D1", "strategies": "nombres de config/strategies.yaml",
    "risk_per_trade": "fracción del saldo que se acepta perder por operación (0.01 = 1 %)",
    "max_daily_loss": "pérdida diaria máxima como fracción del saldo",
    "min_reward_risk": "relación mínima beneficio/riesgo (TP frente a SL)",
    "max_spread_pips": "si el spread es mayor, no se emite la alerta",
    "spread_pips": "spread simulado en cada entrada", "initial_balance": "saldo inicial simulado",
}


class ConfigError(ValueError):
    """Error de configuración con un mensaje listo para mostrar."""


def format_validation_error(error: ValidationError) -> str:
    parts = []
    for item in error.errors():
        where = ".".join(str(p) for p in item["loc"] if p != "__root__")
        message = item["msg"].removeprefix("Value error, ")
        parts.append(f"{where}: {message}" if where else message)
    return "; ".join(parts)


def backup_file(path: Path) -> Path | None:
    if not path.exists():
        return None
    target = path.parent / "backups" / f"{path.stem}_{datetime.now():%Y%m%d_%H%M%S_%f}{path.suffix}"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    return target


def _scalar(value) -> str:
    text = yaml.safe_dump(value, allow_unicode=True, width=10_000, default_flow_style=True)
    return text.strip().removesuffix("...").strip()


def _flow(mapping: dict) -> str:
    return yaml.safe_dump(mapping, allow_unicode=True, width=10_000, sort_keys=False, default_flow_style=True).strip()


def _guarded_write(path: Path, text: str, verify) -> Path | None:
    """Copia, escribe y verifica; si la verificación falla, restaura el archivo anterior."""
    previous = path.read_text(encoding="utf-8") if path.exists() else None
    backup = backup_file(path)
    atomic_write_text(path, text)
    try:
        verify()
    except Exception as error:
        if previous is None:
            path.unlink(missing_ok=True)
        else:
            atomic_write_text(path, previous)
        raise ConfigError(f"El archivo guardado no se pudo volver a cargar; se restauró la versión anterior. {error}") from error
    return backup


# --- estrategias ---------------------------------------------------------------

def _read_strategies_file(root: Path) -> tuple[str, dict[str, dict]]:
    path = root / "config" / "strategies.yaml"
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    index = next((i for i, line in enumerate(lines) if line.startswith("strategies:")), len(lines))
    header = "\n".join(lines[:index]).rstrip() + "\n"
    data = yaml.safe_load(text) or {}
    return header, dict(data.get("strategies") or {})


def read_strategy_dicts(root: Path) -> dict[str, dict]:
    return _read_strategies_file(root)[1]


def normalized(config: StrategyConfig) -> dict:
    """Estrategia validada como diccionario completo (todos los parámetros explícitos), con `type` primero."""
    data = config.model_dump(exclude_none=True, mode="json")
    data["rules"] = [{"type": rule.pop("type"), **rule} for rule in data["rules"]]
    return data


def _strategies_text(header: str, strategies: dict[str, dict]) -> str:
    out = [header.rstrip() + "\n", "strategies:\n"]
    for name, cfg in strategies.items():
        out.append(f"\n  {name}:\n")
        if cfg.get("description"):
            out.append(f"    description: {_scalar(cfg['description'])}\n")
        if cfg.get("symbols"):
            out.append(f"    symbols: {_scalar(cfg['symbols'])}\n")
        out.append(f"    trigger: {_scalar(cfg['trigger'])}\n")
        out.append("    rules:\n")
        for rule in cfg["rules"]:
            out.append(f"      - {_flow(rule)}\n")
        if cfg.get("min_confirmations") is not None:
            out.append(f"    min_confirmations: {cfg['min_confirmations']}\n")
        out.append(f"    stop_loss: {_flow(cfg['stop_loss'])}\n")
        out.append(f"    take_profit: {_flow(cfg['take_profit'])}\n")
    return "".join(out)


def profiles_using(root: Path, strategy: str) -> list[str]:
    used = []
    for name in PROFILES:
        path = root / "config" / f"{name}.yaml"
        if path.exists():
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if strategy in (data.get("strategies") or []):
                used.append(name)
    return used


def _require_real_confirmation(confirm_real: str | None) -> None:
    if (confirm_real or "").strip() != "REAL":
        raise ConfigError("Esta estrategia se usa en el perfil REAL: escribe REAL para confirmar el cambio.")


def validate_strategy(data: dict) -> StrategyConfig:
    try:
        return StrategyConfig.model_validate(data)
    except ValidationError as error:
        raise ConfigError(format_validation_error(error)) from None


def save_strategy(root: Path, name: str, data: dict, confirm_real: str | None = None) -> Path | None:
    """Guarda (crea o reemplaza) la estrategia `name`. Devuelve la ruta de la copia de seguridad."""
    if not NAME_RE.match(name):
        raise ConfigError("Nombre no válido: usa letras, números, guion y guion bajo, sin espacios.")
    config = validate_strategy(data)
    if "real" in profiles_using(root, name):
        _require_real_confirmation(confirm_real)
    header, strategies = _read_strategies_file(root)
    strategies = {key: normalized(validate_strategy(value)) for key, value in strategies.items()}
    strategies[name] = normalized(config)
    return _guarded_write(
        root / "config" / "strategies.yaml", _strategies_text(header, strategies),
        lambda: load_strategies(root),
    )


def duplicate_strategy(root: Path, source: str, new_name: str) -> Path | None:
    strategies = read_strategy_dicts(root)
    if source not in strategies:
        raise ConfigError(f"No existe la estrategia '{source}'.")
    if new_name in strategies:
        raise ConfigError(f"Ya existe una estrategia llamada '{new_name}'.")
    data = {**strategies[source], "description": f"Copia de {source}"}
    return save_strategy(root, new_name, data)


# --- perfiles ------------------------------------------------------------------

def read_profile_dict(root: Path, name: str) -> dict:
    return yaml.safe_load((root / "config" / f"{name}.yaml").read_text(encoding="utf-8")) or {}


def _profile_text(name: str, data: dict) -> str:
    def block(key: str, keys) -> str:
        lines = [f"{key}:\n"]
        for item in keys:
            comment = f"    # {COMMENTS[item]}" if item in COMMENTS else ""
            lines.append(f"  {item}: {_scalar(data[key][item])}{comment}\n")
        return "".join(lines)

    known = {"mode", "symbols", "timeframe", "bars", "strategies", "backtest", "risk"}
    out = [
        f"# Perfil {name.upper()}. Las credenciales van en .env.{name} (no aquí).\n",
        "# Guardado desde la interfaz; la versión anterior queda en config/backups/.\n",
        f"mode: {name}\n\n",
        f"symbols: {_scalar(data['symbols'])}    # {COMMENTS['symbols']}\n",
        f"timeframe: {_scalar(data['timeframe'])}    # {COMMENTS['timeframe']}\n",
        f"bars: {data['bars']}    # {COMMENTS['bars']}\n\n",
        f"strategies: {_scalar(data['strategies'])}    # {COMMENTS['strategies']}\n\n",
        block("backtest", BACKTEST_KEYS), "\n", block("risk", RISK_KEYS),
    ]
    for key, value in data.items():  # claves que no conocemos se conservan
        if key not in known:
            out.append(f"\n{key}: {_scalar(value)}\n")
    return "".join(out)


def save_profile(root: Path, name: str, updates: dict, confirm_real: str | None = None) -> Path | None:
    """Actualiza symbols, timeframe, bars, strategies, risk y backtest del perfil `name`."""
    if name not in PROFILES:
        raise ConfigError(f"Perfil desconocido: {name}")
    if name == "real":
        _require_real_confirmation(confirm_real)
    data = read_profile_dict(root, name)
    for key in ("symbols", "timeframe", "bars", "strategies"):
        if key in updates:
            data[key] = updates[key]
    for section in ("risk", "backtest"):
        if section in updates:
            data[section] = {**data.get(section, {}), **updates[section]}
    data["mode"] = name
    try:
        profile = Profile.model_validate(data)
    except ValidationError as error:
        raise ConfigError(format_validation_error(error)) from None

    data.update(
        symbols=profile.symbols, timeframe=profile.timeframe, bars=profile.bars,
        strategies=profile.strategies, risk=profile.risk.model_dump(), backtest=profile.backtest.model_dump(),
    )
    available = load_strategies(root)
    unknown = [s for s in profile.strategies if s not in available]
    if unknown:
        raise ConfigError(f"Estrategias que no existen en strategies.yaml: {unknown}")
    for strategy in profile.strategies:
        tp = available[strategy].config.take_profit
        if tp.mode == "rr" and tp.reward_risk + 1e-9 < profile.risk.min_reward_risk:
            raise ConfigError(
                f"La estrategia '{strategy}' usa un take profit de 1:{tp.reward_risk:g}, por debajo del mínimo "
                f"de {profile.risk.min_reward_risk:g} del perfil: descartaría todas sus señales."
            )
    path = root / "config" / f"{name}.yaml"
    return _guarded_write(path, _profile_text(name, data), lambda: load_profile(name, root, require_credentials=False))
