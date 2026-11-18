import pytest
from fastapi.testclient import TestClient

from app.config import ROOT, Settings
from app.errors import OllamaUnavailable
from app.main import create_app
from tests.conftest import FakeLLM

KEY = "clave-de-prueba-0123456789"


@pytest.fixture
def api(make_agent, db, tmp_path):
    import shutil
    risk_copy = tmp_path / "risk.yaml"
    shutil.copy(ROOT / "config" / "risk.yaml", risk_copy)
    agent = make_agent(model_value=0.05)
    st = Settings(KEY, "http://127.0.0.1:11434", "m", "https://api.mexc.com", tmp_path / "x.db", 0.01, tmp_path, risk_copy)
    app = create_app(st, agent)
    with TestClient(app) as c:
        yield c, agent, db


H = {"X-API-Key": KEY}


# 5) Clave incorrecta ---------------------------------------------------------------------------------------------
def test_05_missing_and_wrong_key_are_rejected_and_logged(api):
    c, agent, db = api
    for path, method in [("/api/status", "get"), ("/api/decisions", "get"), ("/api/start", "post"), ("/api/stop", "post"),
                         ("/api/pause", "post"), ("/api/liquidate", "post"), ("/api/cycle/run", "post"),
                         ("/api/replay/run", "post"), ("/api/config/risk", "put"), ("/api/llm/chat", "post"),
                         ("/api/events", "get"), ("/api/orders", "get"), ("/api/equity", "get")]:
        assert getattr(c, method)(path).status_code == 401, path
        assert getattr(c, method)(path, headers={"X-API-Key": "incorrecta"}).status_code == 401, path
    assert agent.status == "stopped"                               # ninguna petición sin clave cambió nada
    kinds = {e["kind"] for e in db.events(500)}
    assert {"missing_key", "bad_key"} <= kinds
    assert KEY not in str([e["message"] for e in db.events(500)])  # la clave nunca se escribe en el registro


def test_health_is_public_and_leaks_nothing(api):
    c, _, _ = api
    r = c.get("/api/health")
    assert r.status_code == 200 and set(r.json()) == {"ok", "status"}


def test_start_pause_stop_flow(api):
    c, agent, _ = api
    assert c.post("/api/cycle/run", headers=H).status_code == 409           # detenido: no corre ciclos
    assert c.post("/api/start", headers=H).json()["status"] == "running"
    agent._stop_evt.set()
    assert c.post("/api/pause", headers=H).json()["status"] == "paused"
    r = c.post("/api/stop", headers=H)
    assert r.json()["status"] == "stopped" and "NO liquida" in r.json()["nota"]


def test_status_has_ui_fields_and_no_secrets(api):
    c, agent, _ = api
    c.post("/api/start", headers=H); agent._stop_evt.set()
    c.post("/api/cycle/run", headers=H)
    s = c.get("/api/status", headers=H).json()
    assert {"status", "total_equity", "symbols", "risk", "llm", "models"} <= set(s)
    btc = s["symbols"]["BTCUSDT"]["last_decision"]
    assert {"forecast", "final_action", "risk_status", "dist_sma20", "vol20", "candle_date"} <= set(btc)
    assert KEY not in c.get("/api/status", headers=H).text


def test_replay_endpoint_validates_input(api):
    c, _, _ = api
    assert c.post("/api/replay/run", json={"steps": 999}, headers=H).status_code == 422
    assert c.post("/api/replay/run", json={"steps": 5, "symbols": ["DOGEUSDT"]}, headers=H).status_code == 422
    r = c.post("/api/replay/run", json={"steps": 5, "use_llm": False}, headers=H)
    assert r.status_code == 200 and r.json()["mode"] == "replay"


def test_risk_config_change_is_validated_audited_and_persisted(api, tmp_path):
    c, agent, db = api
    assert c.put("/api/config/risk", json={"max_daily_loss": 5}, headers=H).status_code == 422
    assert c.put("/api/config/risk", json={"max_daily_loss": 0.03}, headers=H).status_code == 200
    assert agent.risk.max_daily_loss == 0.03 and "0.03" in (tmp_path / "risk.yaml").read_text()
    assert "risk_config_changed" in {e["kind"] for e in db.events(50)}


def test_llm_proxy_requires_key_and_reports_ollama_down(api):
    c, agent, _ = api
    body = {"messages": [{"role": "user", "content": "hola"}]}
    assert c.post("/api/llm/chat", json=body).status_code == 401
    assert c.post("/api/llm/chat", json=body, headers=H).status_code == 200
    agent.llm = FakeLLM(error=OllamaUnavailable("Ollama detenido"))
    r = c.post("/api/llm/chat", json=body, headers=H)
    assert r.status_code == 503 and "Ollama" in r.json()["detail"]
    assert c.post("/api/llm/chat", json={"messages": [{"role": "tool", "content": "x"}]}, headers=H).status_code == 422


def test_no_real_order_routes_exist(api):
    c, _, _ = api
    posts = [(p, m) for p, v in c.app.openapi()["paths"].items() for m in v if m != "get"]
    assert not [p for p, m in posts if any(w in p.lower() for w in ("order", "buy", "sell", "withdraw", "trade"))]
