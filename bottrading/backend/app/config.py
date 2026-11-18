"""Configuración: secretos y URLs desde variables de entorno; riesgo desde config/risk.yaml."""
from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from pathlib import Path

import yaml
from dotenv import load_dotenv

from .errors import ConfigError

ROOT = Path(os.environ.get("BOT_ROOT", Path(__file__).resolve().parents[2]))
MODES = ("live", "replay")


@dataclass
class SymbolRule:
    qty_step: float
    min_notional: float
    delta: float


@dataclass
class RiskConfig:
    initial_capital: float
    symbols: list
    fee_rate: float
    slippage_bps: float
    max_position_fraction: float
    max_total_exposure: float
    max_daily_loss: float
    max_data_age_hours: float
    symbol_rules: dict = field(default_factory=dict)

    @property
    def sleeve_capital(self) -> float:
        return self.initial_capital / len(self.symbols)

    def validate(self):
        if self.initial_capital <= 0 or not self.symbols:
            raise ConfigError("risk.yaml: initial_capital debe ser > 0 y symbols no puede estar vacío.")
        for name in ("fee_rate", "slippage_bps"):
            if getattr(self, name) < 0:
                raise ConfigError(f"risk.yaml: {name} no puede ser negativo.")
        for name in ("max_position_fraction", "max_total_exposure", "max_daily_loss"):
            v = getattr(self, name)
            if not (0 < v <= 1):
                raise ConfigError(f"risk.yaml: {name} debe estar en (0, 1]; recibido {v}.")
        if self.max_data_age_hours <= 0:
            raise ConfigError("risk.yaml: max_data_age_hours debe ser > 0.")
        for s in self.symbols:
            if s not in self.symbol_rules:
                raise ConfigError(f"risk.yaml: falta symbol_rules para {s}.")

    def to_dict(self):
        return asdict(self)


def load_risk(path: Path | None = None) -> RiskConfig:
    path = Path(path) if path else ROOT / "config" / "risk.yaml"
    if not path.exists():
        raise ConfigError(f"No existe {path}. Copia config/risk.yaml del repositorio.")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    try:
        rules = {k: SymbolRule(**v) for k, v in raw.pop("symbol_rules", {}).items()}
        cfg = RiskConfig(symbol_rules=rules, **raw)
    except TypeError as exc:
        raise ConfigError(f"risk.yaml inválido: {exc}") from exc
    cfg.validate()
    return cfg


def save_risk(cfg: RiskConfig, path: Path | None = None):
    path = Path(path) if path else ROOT / "config" / "risk.yaml"
    path.write_text(yaml.safe_dump(cfg.to_dict(), sort_keys=False, allow_unicode=True), encoding="utf-8")


@dataclass
class Settings:
    api_key: str
    ollama_url: str
    ollama_model: str
    mexc_base_url: str
    db_path: Path
    poll_seconds: float
    models_dir: Path
    risk_path: Path


def load_settings() -> Settings:
    load_dotenv(ROOT / ".env")
    key = os.environ.get("API_KEY", "").strip()
    if len(key) < 16 or key.lower() in {"change-me", "changeme"}:
        raise ConfigError("API_KEY ausente o demasiado corta (mín. 16 caracteres). Defínela en .env "
                          "(ver .env.example); el backend no arranca sin una clave secreta.")
    db = Path(os.environ.get("DB_PATH", "data/bot.db"))
    return Settings(
        api_key=key,
        ollama_url=os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/"),
        ollama_model=os.environ.get("OLLAMA_MODEL", "llama3.1:8b"),
        mexc_base_url=os.environ.get("MEXC_BASE_URL", "https://api.mexc.com").rstrip("/"),
        db_path=db if db.is_absolute() else ROOT / db,
        poll_seconds=float(os.environ.get("POLL_SECONDS", "300")),
        models_dir=Path(os.environ.get("MODELS_DIR", ROOT / "models")),
        risk_path=Path(os.environ.get("RISK_PATH", ROOT / "config" / "risk.yaml")),
    )
