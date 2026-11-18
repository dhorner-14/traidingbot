import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import ROOT, load_risk                      # noqa: E402
from app.db import Database                                 # noqa: E402
from app.errors import MexcUnavailable, OllamaUnavailable   # noqa: E402
from app.llm import Proposal                                # noqa: E402
from app.mexc import DAY_MS, Candle                         # noqa: E402

NOW_MS = 1_794_916_800_000   # 2026-11-17 12:00 UTC (reloj fijo de las pruebas)
LAST_OPEN = (NOW_MS // DAY_MS - 1) * DAY_MS   # vela de ayer: ya cerrada, con 12 h de antigüedad


def make_candles(n=90, start=100.0, seed=0, last_open=LAST_OPEN, drift=0.0):
    rng = np.random.default_rng(seed)
    closes = start * np.exp(np.cumsum(rng.normal(drift, 0.02, n)))
    out = []
    for i, c in enumerate(closes):
        o = c * (1 + rng.normal(0, 0.003))
        ot = last_open - (n - 1 - i) * DAY_MS
        out.append(Candle(ot, o, max(o, c) * 1.01, min(o, c) * 0.99, float(c), 10.0, ot + DAY_MS - 1))
    return out


class FakeModel:
    def __init__(self, value=0.01):
        self.value = value

    def predict(self, closes):
        return self.value

    def info(self):
        return {"symbol": "X", "version": "fake", "type": "fake", "variables": 20, "delta_contrato": None}


class FakeMexc:
    def __init__(self, symbols, **kw):
        self.data = {s: make_candles(seed=i, **kw) for i, s in enumerate(symbols)}
        self.fail = None
        self.calls = 0

    def closed_candles(self, symbol, limit=60):
        self.calls += 1
        if self.fail:
            raise self.fail
        return self.data[symbol][-limit:]

    def price(self, symbol):
        if self.fail:
            raise self.fail
        return self.data[symbol][-1].close


class FakeLLM:
    def __init__(self, action=None, error=None):
        self.action, self.error, self.calls = action, error, 0

    def propose(self, snapshot, symbol, candle_date, allowed):
        self.calls += 1
        if self.error:
            raise self.error
        accion = self.action or ("comprar" if snapshot["senal_modelo"] == "comprar" else "mantener")
        return Proposal(accion=accion, simbolo=symbol, fecha_vela=candle_date, justificacion="prueba")

    def chat_raw(self, messages):
        if self.error:
            raise self.error
        return "{}"


@pytest.fixture
def risk():
    return load_risk(ROOT / "config" / "risk.yaml")


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "t.db")
    yield d
    d.close()


@pytest.fixture
def make_agent(risk, db):
    from app.agent import Agent

    def _make(model_value=0.01, llm=None, mexc=None, use_llm=False, database=None):
        mexc = mexc or FakeMexc(risk.symbols)
        models = {s: FakeModel(model_value) for s in risk.symbols}
        return Agent(risk, database or db, mexc, llm or FakeLLM(), models, poll_seconds=0.01, use_llm=use_llm,
                     clock=lambda: NOW_MS / 1000)
    return _make
