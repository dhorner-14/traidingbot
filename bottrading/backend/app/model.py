"""Modelo predictivo por símbolo: carga model + scaler.pkl + features.json y valida el contrato.

Carpeta esperada: models/<SIMBOLO>/
  features.json  contrato: variables, lags, model_version, model_type ('lineal' | 'mlp' | 'lstm'), delta (opcional)
  scaler.pkl     StandardScaler ajustado SOLO con TR (lo genera el notebook)
  model.pkl      modelo sklearn (p. ej. regresión lineal)  -- o --
  model.keras    red entrenada en Colab T4 (requiere keras/tensorflow instalado)
"""
from __future__ import annotations

import json
import pickle
from pathlib import Path

import numpy as np

from .errors import ModelContractError
from .features import compute_features


class ForecastModel:
    def __init__(self, symbol: str, directory: Path):
        self.symbol = symbol
        self.dir = Path(directory)
        feat = self.dir / "features.json"
        if not feat.exists():
            raise ModelContractError(f"{symbol}: falta {feat}. Ejecuta el notebook (scaler/features) y scripts/export_baseline.py.")
        self.contract = json.loads(feat.read_text(encoding="utf-8"))
        self.variables = self.contract.get("variables")
        if not isinstance(self.variables, list) or not self.variables:
            raise ModelContractError(f"{symbol}: features.json sin lista 'variables'.")
        self.version = str(self.contract.get("model_version", "sin-version"))
        self.model_type = self.contract.get("model_type", "lineal")
        try:
            with open(self.dir / "scaler.pkl", "rb") as fh:
                self.scaler = pickle.load(fh)  # archivo local de confianza generado por tu notebook
        except FileNotFoundError as exc:
            raise ModelContractError(f"{symbol}: falta scaler.pkl en {self.dir}.") from exc
        if getattr(self.scaler, "n_features_in_", None) != len(self.variables):
            raise ModelContractError(f"{symbol}: el escalador espera {getattr(self.scaler, 'n_features_in_', '?')} variables "
                                     f"y el contrato define {len(self.variables)}. No reutilices escalas sin reentrenar.")
        self.model = self._load_model()

    def _load_model(self):
        keras_path, pkl_path = self.dir / "model.keras", self.dir / "model.pkl"
        if keras_path.exists():
            try:
                from keras.models import load_model  # import tardío: solo si hay una red
            except ImportError as exc:
                raise ModelContractError("Hay model.keras pero keras/tensorflow no está instalado (ver requirements.txt).") from exc
            return load_model(keras_path)
        if pkl_path.exists():
            with open(pkl_path, "rb") as fh:
                return pickle.load(fh)
        raise ModelContractError(f"{self.symbol}: no hay model.keras ni model.pkl en {self.dir}.")

    @property
    def delta(self):
        return self.contract.get("delta")

    def info(self) -> dict:
        return {"symbol": self.symbol, "version": self.version, "type": self.model_type,
                "variables": len(self.variables), "delta_contrato": self.delta}

    def predict(self, closes) -> float:
        x = compute_features(closes, self.variables)
        xs = self.scaler.transform(x)
        if self.model_type == "lstm":
            xs = xs.reshape(1, xs.shape[1], 1)
        y = np.asarray(self.model.predict(xs, verbose=0) if self.model_type in ("mlp", "lstm") else self.model.predict(xs)).ravel()
        if y.size != 1 or not np.isfinite(y[0]):
            raise ModelContractError(f"{self.symbol}: la predicción no es un valor finito ({y}). Revisa escalas y datos.")
        return float(y[0])


def load_models(models_dir: Path, symbols: list[str]) -> dict:
    return {s: ForecastModel(s, Path(models_dir) / s) for s in symbols}
