"""Entrena la REGRESIÓN LINEAL de referencia con TR (salidas del notebook) y la guarda para el backend.

Uso (desde la raíz del repo, después de correr notebooks/avance1_mexc.ipynb y copiar a este repo
data/processed/*_ventanas_20.csv y models/<SIMBOLO>/{scaler.pkl,features.json}):

    python scripts/export_baseline.py            # los 4 símbolos
    python scripts/export_baseline.py BTCUSDT    # uno solo

Solo usa TR para ajustar y reporta VA. NO toca TE. Cuando tengas tu MLP/LSTM de Colab (Avance 2),
guarda model.keras en la misma carpeta y pon "model_type": "mlp" o "lstm" en features.json.
"""
from __future__ import annotations

import json
import pickle
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression

ROOT = Path(__file__).resolve().parents[1]
SIMBOLOS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "ADAUSDT"]


def exportar(simbolo: str):
    csv = ROOT / "data" / "processed" / f"{simbolo}_ventanas_20.csv"
    carpeta = ROOT / "models" / simbolo
    if not csv.exists():
        sys.exit(f"Falta {csv}. Ejecuta el notebook y copia sus salidas (data/processed).")
    for f in ("scaler.pkl", "features.json"):
        if not (carpeta / f).exists():
            sys.exit(f"Falta {carpeta / f}. Ejecuta el notebook y copia models/{simbolo}/.")
    contrato = json.loads((carpeta / "features.json").read_text(encoding="utf-8"))
    with open(carpeta / "scaler.pkl", "rb") as fh:
        scaler = pickle.load(fh)
    df = pd.read_csv(csv)
    cols = contrato["variables"]
    tr, va = df[df.conjunto == "TR"], df[df.conjunto == "VA"]
    lr = LinearRegression().fit(scaler.transform(tr[cols].to_numpy()), tr["y"].to_numpy())
    pred = lr.predict(scaler.transform(va[cols].to_numpy()))
    rmse_lr = float(np.sqrt(np.mean((pred - va["y"]) ** 2)))
    rmse_0 = float(np.sqrt(np.mean(va["y"] ** 2)))
    with open(carpeta / "model.pkl", "wb") as fh:
        pickle.dump(lr, fh)
    contrato.update({"model_type": "lineal", "model_version": f"lineal-{date.today().isoformat()}",
                     "ajustado_con": "TR", "rmse_va_lineal": rmse_lr, "rmse_va_cero": rmse_0})
    (carpeta / "features.json").write_text(json.dumps(contrato, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"{simbolo}: model.pkl guardado · RMSE VA lineal={rmse_lr:.6f} vs cero={rmse_0:.6f}")


if __name__ == "__main__":
    for s in (sys.argv[1:] or SIMBOLOS):
        exportar(s)
