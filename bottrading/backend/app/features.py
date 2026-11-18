"""Variables del modelo. La definición es la MISMA que en el notebook (solo historia hasta t).

Nombres soportados en el contrato features.json:
  r_t-k      rendimiento simple de la vela t-k:  C_{t-k}/C_{t-k-1} - 1   (k = 0 es el último cierre)
  dist_sma20 cierre / SMA de 20 cierres - 1
  vol20      desviación estándar muestral de los últimos 20 rendimientos diarios (sin anualizar)
"""
from __future__ import annotations

import re

import numpy as np

from .errors import ModelContractError

_LAG = re.compile(r"^r_t-(\d+)$")


def returns(closes) -> np.ndarray:
    c = np.asarray(closes, dtype=float)
    return c[1:] / c[:-1] - 1.0


def indicators(closes) -> dict:
    """Indicadores para mostrar y para el LLM. Valores None si no hay historia suficiente."""
    c = np.asarray(closes, dtype=float)
    out = {"dist_sma20": None, "vol20": None}
    if len(c) >= 20:
        out["dist_sma20"] = float(c[-1] / c[-20:].mean() - 1.0)
    if len(c) >= 21:
        out["vol20"] = float(returns(c)[-20:].std(ddof=1))
    return out


def compute_features(closes, variables: list[str]) -> np.ndarray:
    c = np.asarray(closes, dtype=float)
    if not np.isfinite(c).all() or (c <= 0).any():
        raise ModelContractError("Cierres no finitos o no positivos: no se pueden calcular variables.")
    r = returns(c)
    values = []
    for name in variables:
        m = _LAG.match(name)
        if m:
            k = int(m.group(1))
            if len(r) < k + 1:
                raise ModelContractError(f"Historia insuficiente para {name}: se necesitan {k + 2} cierres, hay {len(c)}.")
            values.append(r[-1 - k])
        elif name == "dist_sma20":
            ind = indicators(c)["dist_sma20"]
            if ind is None:
                raise ModelContractError("Historia insuficiente para dist_sma20 (20 cierres).")
            values.append(ind)
        elif name == "vol20":
            ind = indicators(c)["vol20"]
            if ind is None:
                raise ModelContractError("Historia insuficiente para vol20 (21 cierres).")
            values.append(ind)
        else:
            raise ModelContractError(f"Variable desconocida en el contrato: {name!r}. "
                                     "Soportadas: r_t-k, dist_sma20, vol20.")
    x = np.asarray(values, dtype=float).reshape(1, -1)
    if not np.isfinite(x).all():
        raise ModelContractError("Las variables calculadas no son finitas.")
    return x
