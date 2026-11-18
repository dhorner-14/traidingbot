"""Compara en el mismo periodo y con los mismos costos: modelo sin LLM, agente con LLM y comprar y mantener.

    cd backend
    python ../scripts/compare_strategies.py --steps 30            # incluye LLM (necesita Ollama corriendo)
    python ../scripts/compare_strategies.py --steps 30 --no-llm   # solo modelo y comprar y mantener

Descarga velas cerradas de MEXC (datos públicos). Es una REPETICIÓN HISTÓRICA (replay), no una conexión en vivo.
Con LLM hace una consulta por símbolo y por día: con 4 símbolos y 30 pasos son ~120 llamadas.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.agent import MIN_HISTORY                      # noqa: E402
from app.backtest import run_compare                   # noqa: E402
from app.config import load_risk, load_settings        # noqa: E402
from app.llm import OllamaClient                       # noqa: E402
from app.mexc import MexcClient                        # noqa: E402
from app.model import load_models                      # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--steps", type=int, default=30)
    ap.add_argument("--no-llm", action="store_true")
    a = ap.parse_args()
    st = load_settings()
    risk = load_risk(st.risk_path)
    models = load_models(st.models_dir, risk.symbols)
    mexc = MexcClient(st.mexc_base_url)
    candles = {s: mexc.closed_candles(s, limit=a.steps + MIN_HISTORY) for s in risk.symbols}
    llm = None if a.no_llm else OllamaClient(st.ollama_url, st.ollama_model)
    res = run_compare(candles, models, risk, llm, a.steps, include_llm=not a.no_llm)
    out = ROOT / "data" / "comparacion.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")
    cols = ["rendimiento_neto_pct", "max_drawdown_pct", "win_rate_pct", "operaciones_cerradas",
            "costos_totales", "latencia_media_ms", "acciones_rechazadas"]
    print(f"{'estrategia':22}" + "".join(f"{c[:18]:>20}" for c in cols))
    for name, m in res.items():
        if "error" in m:
            print(f"{name:22} ERROR: {m['error']}")
            continue
        print(f"{name:22}" + "".join(f"{str(m.get(c)):>20}" for c in cols))
    print(f"\nGuardado en {out}. Los costos y el deslizamiento son supuestos académicos (config/risk.yaml).")


if __name__ == "__main__":
    main()
