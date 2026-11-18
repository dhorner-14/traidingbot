from app.backtest import run_compare
from tests.conftest import FakeLLM, FakeModel, make_candles


def test_compare_three_strategies_same_period_and_costs(risk):
    candles = {s: make_candles(n=80, seed=i, drift=0.002) for i, s in enumerate(risk.symbols)}
    models = {s: FakeModel(0.01) for s in risk.symbols}
    res = run_compare(candles, models, risk, FakeLLM(), steps=30, include_llm=True)
    assert set(res) == {"modelo_sin_llm", "agente_con_llm", "comprar_y_mantener"}
    for name, m in res.items():
        assert "error" not in m and m["pasos"] == 30, name
        assert 0 <= m["max_drawdown_pct"] <= 100
    # el LLM de prueba imita la regla del modelo → mismo resultado y mismas operaciones
    assert res["modelo_sin_llm"]["rendimiento_neto_pct"] == res["agente_con_llm"]["rendimiento_neto_pct"]
    assert res["agente_con_llm"]["latencia_media_ms"] is not None and res["modelo_sin_llm"]["latencia_media_ms"] is None
    # comprar y mantener: 1 operación cerrada por símbolo (liquidación final con costo de venta)
    assert res["comprar_y_mantener"]["operaciones_cerradas"] == len(risk.symbols)


def test_compare_win_rate_undefined_without_closed_trades(risk):
    candles = {s: make_candles(n=80, seed=i) for i, s in enumerate(risk.symbols)}
    models = {s: FakeModel(-0.05) for s in risk.symbols}          # nunca compra
    res = run_compare(candles, models, risk, None, steps=20, include_llm=False)
    assert res["modelo_sin_llm"]["win_rate_pct"] is None and res["modelo_sin_llm"]["operaciones_cerradas"] == 0
    assert res["modelo_sin_llm"]["rendimiento_neto_pct"] == 0
