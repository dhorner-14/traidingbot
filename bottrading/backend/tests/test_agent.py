"""Los nueve escenarios de error exigidos + ciclo, replay y riesgo.
(La prueba 5, clave incorrecta, está en test_api.py.)"""
from app.agent import Agent
from app.db import Database
from app.errors import InvalidLLMResponse, MexcUnavailable, OllamaUnavailable
from tests.conftest import NOW_MS, FakeLLM, FakeMexc, FakeModel, make_candles


def kinds(db):
    return [e["kind"] for e in db.events(200)]


# 1) API sin respuesta o con límite de solicitudes ---------------------------------------------------------
def test_01_mexc_down_pauses_and_no_orders(make_agent, db):
    mexc = FakeMexc(["BTCUSDT", "ETHUSDT", "SOLUSDT", "ADAUSDT"])
    mexc.fail = MexcUnavailable("MEXC no respondió (límite de solicitudes HTTP 429)", rate_limited=True)
    a = make_agent(mexc=mexc)
    a.start(); a._stop_evt.set()
    out = a.run_live_cycle()
    assert a.status == "paused" and "429" in a.pause_reason
    assert db.orders("live") == [] and "dependency_failure" in kinds(db)
    assert list(out)[0] == "BTCUSDT" and len(out) == 1          # se detiene al primer fallo


# 2) Dato atrasado ---------------------------------------------------------------------------------------------
def test_02_stale_data_blocks_decisions(make_agent, db, risk):
    mexc = FakeMexc(risk.symbols, last_open=(NOW_MS // 86_400_000 - 6) * 86_400_000)   # última vela de hace ~6 días
    a = make_agent(mexc=mexc)
    a.start(); a._stop_evt.set()
    out = a.run_live_cycle()
    assert db.decisions("live") == [] and db.orders("live") == []
    assert "stale_data" in kinds(db)
    assert "atrasados" in out["BTCUSDT"]["error"] and "máx." in out["BTCUSDT"]["error"]


# 3) Ollama detenido ---------------------------------------------------------------------------------------------
def test_03_ollama_down_pauses(make_agent, db):
    a = make_agent(llm=FakeLLM(error=OllamaUnavailable("Ollama no respondió; ejecuta `ollama serve`")), use_llm=True)
    a.start(); a._stop_evt.set()
    a.run_live_cycle()
    assert a.status == "paused" and "ollama" in a.pause_reason.lower()
    assert db.orders("live") == []


# 4) JSON inválido del LLM ----------------------------------------------------------------------------------------
def test_04_invalid_llm_json_is_rejected_without_trading(make_agent, db):
    a = make_agent(llm=FakeLLM(error=InvalidLLMResponse("El LLM no devolvió JSON válido")), use_llm=True)
    a.start(); a._stop_evt.set()
    out = a.run_live_cycle()
    d = out["BTCUSDT"]
    assert d["final_action"] == "mantener" and d["risk_status"] == "rechazado"
    assert db.orders("live") == [] and "invalid_json" in kinds(db) and a.status == "running"


# 6) Saldo insuficiente --------------------------------------------------------------------------------------------
def test_06_insufficient_balance_is_rejected_and_logged(make_agent, db, risk):
    a = make_agent(model_value=0.05)
    a.start(); a._stop_evt.set()
    db.save_sleeve("live", "BTCUSDT", dict(cash=2.0, qty=0.0, entry_cost=0.0, last_equity=2.0, last_price=100.0))
    a.run_live_cycle()
    d = db.decisions("live", "BTCUSDT", 1)[0]
    assert d["final_action"] == "mantener" and d["risk_status"] == "bloqueado" and "insuficiente" in d["risk_reason"]
    assert db.orders("live", "BTCUSDT") == []


def test_06b_simulator_rejection_is_stored_as_rejected_order(make_agent, db, risk):
    a = make_agent(model_value=0.05)
    a.start(); a._stop_evt.set()
    r = risk.symbol_rules["BTCUSDT"]
    risk.symbol_rules["BTCUSDT"] = type(r)(qty_step=1000.0, min_notional=r.min_notional, delta=r.delta)   # paso enorme → qty 0
    a.run_live_cycle()
    o = db.orders("live", "BTCUSDT")
    assert o and o[0]["status"] == "rejected" and "tamaño mínimo" in o[0]["reason"]
    risk.symbol_rules["BTCUSDT"] = r


# 7) Evento duplicado --------------------------------------------------------------------------------------------
def test_07_duplicate_candle_processed_once(make_agent, db):
    a = make_agent(model_value=0.05)
    a.start(); a._stop_evt.set()
    a.run_live_cycle()
    n_orders, n_dec = len(db.orders("live")), len(db.decisions("live"))
    out = a.run_live_cycle()                                   # la misma vela llega otra vez
    assert all(v.get("skipped") for v in out.values())
    assert len(db.orders("live")) == n_orders and len(db.decisions("live")) == n_dec
    cs = a.mexc.closed_candles("BTCUSDT", 40)
    assert a.process_candle("live", "BTCUSDT", cs, cs[-1].close, NOW_MS)["skipped"] == "duplicado"
    assert "duplicate" in kinds(db)


# 8) Reinicio del backend ------------------------------------------------------------------------------------------
def test_08_restart_recovers_state_without_duplicates(make_agent, tmp_path, risk):
    path = tmp_path / "persist.db"
    db1 = Database(path)
    a1 = make_agent(model_value=0.05, database=db1)
    a1.start(); a1._stop_evt.set()
    a1.run_live_cycle()
    before = db1.all_sleeves("live")
    n_orders = len(db1.orders("live"))
    assert n_orders > 0
    db1.close()

    db2 = Database(path)                                        # "reinicio": nuevo proceso, misma base
    a2 = make_agent(model_value=0.05, database=db2)
    assert a2.status == "paused" and "reiniciado" in a2.pause_reason          # no reanuda solo
    assert db2.all_sleeves("live") == before                                   # efectivo y posición recuperados
    a2.start(); a2._stop_evt.set()
    out = a2.run_live_cycle()
    assert all(v.get("skipped") for v in out.values()) and len(db2.orders("live")) == n_orders
    db2.close()


# 9) Botón de detención --------------------------------------------------------------------------------------------
def test_09_stop_blocks_orders_and_keeps_position(make_agent, db):
    a = make_agent(model_value=0.05)
    a.start(); a._stop_evt.set()
    a.run_live_cycle()
    held = {s: v["qty"] for s, v in db.all_sleeves("live").items()}
    assert any(q > 0 for q in held.values())
    a.stop()
    out = a.run_live_cycle()
    assert "detenido" in out["skipped"] or "stopped" in out["skipped"]
    assert {s: v["qty"] for s, v in db.all_sleeves("live").items()} == held       # detener NO liquida
    snap = a.snapshot("live")
    assert snap["status"] == "stopped" and any(s["in_position"] for s in snap["symbols"].values())
    a.liquidate("live", prices={s: 100.0 for s in held})                           # liquidar es explícito
    assert all(v["qty"] == 0 for v in db.all_sleeves("live").values())


# ---------------------------------------------------------------------------- ciclo y reglas
def test_full_cycle_with_llm_records_everything(make_agent, db):
    llm = FakeLLM()
    a = make_agent(model_value=0.05, llm=llm, use_llm=True)
    a.start(); a._stop_evt.set()
    out = a.run_live_cycle()
    assert llm.calls == 4 and all("final_action" in v for v in out.values())
    d = db.decisions("live", "BTCUSDT", 1)[0]
    assert d["strategy"] == "llm" and d["llm_action"] == "comprar" and d["latency_ms"] is not None
    assert d["final_action"] == "comprar" and db.equity_series("live", "BTCUSDT")


def test_exposure_limit_holds_across_symbols(make_agent, db, risk):
    a = make_agent(model_value=0.05)
    a.start(); a._stop_evt.set()
    a.run_live_cycle()
    snap = a.snapshot("live")
    invested = sum(s["qty"] * s["last_price"] for s in snap["symbols"].values())
    assert invested <= risk.max_total_exposure * snap["total_equity"] + 1e-6


def test_daily_loss_halts_new_orders(make_agent, db):
    a = make_agent(model_value=0.05)
    a.start(); a._stop_evt.set()
    last = a.mexc.closed_candles("BTCUSDT", 40)[-1].close
    db.save_sleeve("live", "BTCUSDT", dict(cash=0.0, qty=100.0, entry_cost=1.0, last_equity=last * 100 * 2, last_price=last))
    a.run_live_cycle()                                         # pérdida del 50 % en la vela → límite 5 %
    assert a.halted("live") and "limit_reached" in kinds(db)
    d = db.decisions("live", "BTCUSDT", 1)[0]
    assert d["risk_status"] == "bloqueado" and db.orders("live", "BTCUSDT") == []


def test_replay_runs_20_cycles_per_symbol_in_separate_mode(make_agent, db, risk):
    a = make_agent(model_value=0.002)
    res = a.replay(steps=20, use_llm=False)
    assert res["steps_done"] == 20 * len(risk.symbols) and res["error"] is None
    assert len(db.decisions("replay", "BTCUSDT", 100)) == 20
    assert db.decisions("live") == []                          # no se mezcla con la conexión en vivo
    first = db.decisions("replay", "BTCUSDT", 100)[-1]
    cs = a.mexc.data["BTCUSDT"]
    nxt = [c for c in cs if c.open_time > next(c2.open_time for c2 in cs if c2.close == first["price"])][0]
    assert first["exec_price"] == nxt.open                     # señal en el cierre de t → ejecución en la apertura de t+1


def test_models_cannot_see_future(make_agent):
    """El pronóstico del paso k solo recibe cierres hasta la vela k."""
    seen = []

    class Spy(FakeModel):
        def predict(self, closes):
            seen.append(len(closes))
            return 0.0

    a = make_agent()
    a.models = {s: Spy() for s in a.risk.symbols}
    a.replay(steps=5, use_llm=False)
    per_symbol = seen[::len(a.risk.symbols)]
    assert per_symbol == sorted(per_symbol) and per_symbol[1] - per_symbol[0] == 1
