"""Comparación sobre el MISMO periodo y con los MISMOS costos:
modelo sin LLM · agente con LLM · comprar y mantener.

Reutiliza el mismo Agent (riesgo + simulador) sobre una base en memoria, así que no hay lógica duplicada.
Al final se liquidan las posiciones al último cierre y se cobra el costo de venta, en las tres estrategias.
"""
from __future__ import annotations

from .agent import MIN_HISTORY, Agent
from .config import RiskConfig
from .db import Database
from .paper import PaperBroker


class StaticMexc:
    """Entrega siempre las mismas velas (ya descargadas) con la interfaz de MexcClient."""

    def __init__(self, candles_by_symbol: dict):
        self.data = candles_by_symbol

    def closed_candles(self, symbol, limit=60):
        return self.data[symbol][-limit:]

    def price(self, symbol):
        return self.data[symbol][-1].close


def _metrics(initial: float, series: list, final: float, orders: list, decisions: list | None = None) -> dict:
    peak, mdd = initial, 0.0
    for v in [initial] + series + [final]:
        peak = max(peak, v)
        mdd = max(mdd, (peak - v) / peak)
    sells = [o for o in orders if o["side"] == "sell" and o["status"] == "executed"]
    wins = [o for o in sells if (o["pnl"] or 0) > 0]
    decisions = decisions or []
    lat = [d["latency_ms"] for d in decisions if d.get("latency_ms") is not None]
    return {
        "patrimonio_final": round(final, 2),
        "rendimiento_neto_pct": round((final / initial - 1) * 100, 3),
        "max_drawdown_pct": round(mdd * 100, 3),
        "operaciones_cerradas": len(sells),
        "win_rate_pct": round(len(wins) / len(sells) * 100, 2) if sells else None,   # None = no definido
        "costos_totales": round(sum(o["fee"] or 0 for o in orders if o["status"] == "executed"), 4),
        "acciones_rechazadas": sum(1 for d in decisions if d["risk_status"] in ("bloqueado", "rechazado")),
        "latencia_media_ms": round(sum(lat) / len(lat), 1) if lat else None,
    }


def run_agent_variant(candles: dict, models: dict, risk: RiskConfig, llm, steps: int, use_llm: bool) -> dict:
    db = Database(":memory:")
    agent = Agent(risk, db, StaticMexc(candles), llm, models, use_llm=use_llm)
    res = agent.replay(steps=steps, use_llm=use_llm)
    if res["error"]:
        return {"error": res["error"]}
    agent.liquidate("replay", prices={s: candles[s][-1].close for s in candles})
    decisions = db.decisions("replay", None, 100000)
    times = sorted({d["candle_time"] for d in decisions})
    by_t = {}
    for e in db.equity_series("replay", None, 100000):
        if e["candle_time"] in times:
            by_t[e["candle_time"]] = by_t.get(e["candle_time"], 0.0) + e["equity"]
    series = [by_t[t] for t in times if t in by_t]
    final = sum(s["cash"] for s in db.all_sleeves("replay").values())
    out = _metrics(risk.initial_capital, series, final, db.orders("replay", None, 100000), decisions)
    out["pasos"] = len(times)
    db.close()
    return out


def run_compare(candles: dict, models: dict, risk: RiskConfig, llm, steps: int, include_llm=True) -> dict:
    results = {"modelo_sin_llm": run_agent_variant(candles, models, risk, llm, steps, use_llm=False)}
    if include_llm:
        results["agente_con_llm"] = run_agent_variant(candles, models, risk, llm, steps, use_llm=True)
    results["comprar_y_mantener"] = _buy_hold_metrics(candles, risk, steps)
    return results


def _buy_hold_metrics(candles: dict, risk: RiskConfig, steps: int) -> dict:
    n = min(len(v) for v in candles.values())
    steps = min(steps, n - MIN_HISTORY)
    series, orders, final = [0.0] * steps, [], 0.0
    for sym, cs in candles.items():
        r = risk.symbol_rules[sym]
        b = PaperBroker(risk.sleeve_capital, fee_rate=risk.fee_rate, slippage_bps=risk.slippage_bps,
                        qty_step=r.qty_step, min_notional=r.min_notional)
        start = len(cs) - steps
        fill = b.buy(cs[start + 1].open if start + 1 < len(cs) else cs[start].close, risk.sleeve_capital)
        orders.append({"side": "buy", "status": fill.status, "fee": fill.fee, "pnl": None})
        for k in range(steps):
            series[k] += b.equity(cs[start + k].close)
        if b.qty > 0:
            sell = b.sell(cs[-1].close)
            orders.append({"side": "sell", "status": sell.status, "fee": sell.fee, "pnl": sell.pnl})
        final += b.cash
    out = _metrics(risk.initial_capital, series, final, orders)
    out.update({"pasos": steps, "mae_rmse": "no aplica", "latencia_media_ms": None, "acciones_rechazadas": 0})
    return out
