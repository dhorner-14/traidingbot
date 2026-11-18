"""Ciclo del agente (por vela diaria cerrada y por símbolo):

  velas cerradas → validar → variables → pronóstico → propuesta (LLM o regla) → riesgo → Paper → guardar → esperar

Estados del agente: stopped | running | paused. `stop` NO liquida: bloquea órdenes nuevas y deja la posición visible.
Liquidar es una acción explícita aparte (`liquidate`).
"""
from __future__ import annotations

import threading
import time
from time import perf_counter

from .config import RiskConfig
from .db import Database
from .errors import (InvalidCandleData, InvalidLLMResponse, MexcUnavailable, ModelContractError,
                     OllamaUnavailable, StaleDataError)
from .features import indicators, returns
from .paper import PaperBroker
from .risk import RiskContext, evaluate

MIN_HISTORY = 40          # cierres de historia que se piden por símbolo
HOUR_MS = 3_600_000


def candle_date(c) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(c.open_time / 1000))


class Agent:
    def __init__(self, risk: RiskConfig, db: Database, mexc, llm, models: dict, poll_seconds=300.0, use_llm=True,
                 clock=time.time):
        self.risk, self.db, self.mexc, self.llm, self.models = risk, db, mexc, llm, models
        self.poll_seconds, self.use_llm, self._clock = poll_seconds, use_llm, clock
        self._thread = None
        self._stop_evt = threading.Event()
        self._cycle_lock = threading.Lock()
        if self.db.get_state("status") == "running":      # reinicio del backend: no reanuda solo
            self.set_status("paused", "backend reiniciado: estado recuperado; pulsa Iniciar para reanudar")

    # ------------------------------------------------------------------ estado
    @property
    def status(self) -> str:
        return self.db.get_state("status", "stopped")

    @property
    def pause_reason(self) -> str:
        return self.db.get_state("pause_reason", "")

    def set_status(self, status: str, reason: str = ""):
        self.db.set_state("status", status)
        self.db.set_state("pause_reason", reason)

    def halted(self, mode="live") -> bool:
        return self.db.get_state(f"halted:{mode}", "0") == "1"

    def start(self):
        self.db.set_state("halted:live", "0")
        self.set_status("running", "")
        self.db.log_event("info", "agent", "start", "Agente iniciado por el usuario")
        if not (self._thread and self._thread.is_alive()):
            self._stop_evt.clear()
            self._thread = threading.Thread(target=self._loop, name="agent-loop", daemon=True)
            self._thread.start()

    def pause(self, reason="pausa manual"):
        self.set_status("paused", reason)
        self.db.log_event("warn", "agent", "pause", reason)

    def stop(self):
        self.set_status("stopped", "detenido por el usuario (la posición NO se liquida)")
        self._stop_evt.set()
        self.db.log_event("info", "agent", "stop", "Agente detenido: órdenes nuevas bloqueadas; posiciones intactas")

    def shutdown(self):
        """Apagado del proceso: detiene el hilo SIN cambiar el estado persistido (al reiniciar queda en pausa)."""
        self._stop_evt.set()

    def _loop(self):
        while not self._stop_evt.is_set() and self.status == "running":
            try:
                self.run_live_cycle()
            except Exception as exc:  # supervisor de último recurso: se registra y se pausa, nunca se silencia
                self.db.log_event("error", "agent", "unexpected", f"{type(exc).__name__}: {exc}")
                self.pause(f"error inesperado ({type(exc).__name__}); revisa los eventos")
            self._stop_evt.wait(self.poll_seconds)

    # ----------------------------------------------------------------- sleeves
    def sleeve(self, mode: str, symbol: str) -> dict:
        s = self.db.get_sleeve(mode, symbol)
        if s is None:
            cap = self.risk.sleeve_capital
            s = dict(cash=cap, qty=0.0, entry_cost=0.0, last_equity=cap, last_price=0.0)
        return s

    def _portfolio(self, mode: str, symbol: str, eq_now: float, exposure_now: float):
        total, expo = eq_now, exposure_now
        for sym in self.risk.symbols:
            if sym == symbol:
                continue
            s = self.sleeve(mode, sym)
            total += s["cash"] + s["qty"] * s["last_price"]
            expo += s["qty"] * s["last_price"]
        return total, expo

    def _broker(self, symbol: str, s: dict) -> PaperBroker:
        r = self.risk.symbol_rules[symbol]
        return PaperBroker(s["cash"], s["qty"], s["entry_cost"], self.risk.fee_rate, self.risk.slippage_bps,
                           r.qty_step, r.min_notional)

    # ------------------------------------------------------------ una vela
    def process_candle(self, mode: str, symbol: str, candles: list, exec_price: float, now_ms: int, strategy="llm"):
        last = candles[-1]
        date = candle_date(last)
        if self.db.is_processed(mode, symbol, last.open_time):
            self.db.log_event("info", "agent", "duplicate", f"Vela {date} de {symbol} ya procesada ({mode}); se ignora", symbol)
            return {"symbol": symbol, "skipped": "duplicado"}

        closes = [c.close for c in candles]
        forecast = self.models[symbol].predict(closes)
        delta = self.risk.symbol_rules[symbol].delta
        signal = "comprar" if forecast > delta else "salir_o_efectivo"
        ind = indicators(closes)
        s = self.sleeve(mode, symbol)
        has_pos = s["qty"] > 0
        rule_action = ("comprar" if not has_pos else "mantener") if signal == "comprar" else ("vender" if has_pos else "mantener")

        llm_action = llm_reason = None
        latency = None
        proposed, forced_reason = rule_action, None
        if strategy == "llm":
            snapshot = {
                "simbolo": symbol, "fecha_vela": date, "cierre": last.close,
                "pronostico_rendimiento": round(forecast, 6), "umbral_delta": delta, "senal_modelo": signal,
                "indicadores": {k: (None if v is None else round(v, 6)) for k, v in ind.items()},
                "rendimientos_recientes": [round(float(x), 5) for x in returns(closes)[-5:]],
                "posicion": {"abierta": has_pos, "cantidad": s["qty"], "efectivo": round(s["cash"], 2)},
                "acciones_permitidas": ["comprar", "mantener", "vender"],
            }
            t0 = perf_counter()
            try:
                prop = self.llm.propose(snapshot, symbol, date, self.risk.symbols)
                llm_action, llm_reason, proposed = prop.accion, prop.justificacion, prop.accion
            except InvalidLLMResponse as exc:          # propuesta rechazada: no se opera ese día
                proposed, forced_reason = "mantener", f"propuesta del LLM rechazada: {exc}"
                llm_reason = forced_reason
                self.db.log_event("warn", "llm", "invalid_json", str(exc), symbol)
            latency = (perf_counter() - t0) * 1000

        eq_mark = s["cash"] + s["qty"] * last.close
        day_loss = max(0.0, (s["last_equity"] - eq_mark) / s["last_equity"]) if s["last_equity"] > 0 else 0.0
        total_eq, expo = self._portfolio(mode, symbol, eq_mark, s["qty"] * last.close)
        ctx = RiskContext(symbol=symbol, action=proposed, running=(mode == "replay" or self.status == "running"),
                          halted=self.halted(mode), data_age_hours=(now_ms - last.close_time) / HOUR_MS,
                          has_position=has_pos, cash=s["cash"], total_equity=total_eq, total_exposure=expo, day_loss=day_loss)
        verdict = evaluate(self.risk, ctx)
        if verdict.trigger_halt:
            self.db.set_state(f"halted:{mode}", "1")
            self.db.log_event("error", "risk", "limit_reached", verdict.reason, symbol)

        broker = self._broker(symbol, s)
        order, final_action, risk_status, risk_reason = None, verdict.action, verdict.status, verdict.reason
        if forced_reason:
            risk_status, risk_reason = "rechazado", forced_reason
        if verdict.action in ("comprar", "vender"):
            fill = broker.buy(exec_price, verdict.max_notional) if verdict.action == "comprar" else broker.sell(exec_price)
            order = dict(side=fill.side, status=fill.status, reason=fill.reason, qty=fill.qty, price=fill.price,
                         notional=fill.notional, fee=fill.fee, pnl=fill.pnl, cash_after=broker.cash, qty_after=broker.qty)
            if fill.status == "rejected":
                final_action, risk_status, risk_reason = "mantener", "rechazado", f"simulador: {fill.reason}"
                self.db.log_event("warn", "paper", "order_rejected", fill.reason, symbol)

        new_sleeve = dict(cash=broker.cash, qty=broker.qty, entry_cost=broker.entry_cost,
                          last_equity=broker.equity(last.close), last_price=last.close)
        decision = dict(candle_date=date, price=last.close, forecast=forecast, delta=delta, signal=signal, strategy=strategy,
                        llm_action=llm_action, llm_reason=llm_reason, final_action=final_action, risk_status=risk_status,
                        risk_reason=risk_reason, latency_ms=latency, exec_price=exec_price,
                        dist_sma20=ind["dist_sma20"], vol20=ind["vol20"])
        eq_row = dict(cash=broker.cash, qty=broker.qty, price=last.close, equity=new_sleeve["last_equity"])
        if not self.db.record_step(mode, symbol, last.open_time, decision, order, eq_row, new_sleeve):
            return {"symbol": symbol, "skipped": "duplicado"}
        return {"symbol": symbol, **decision}

    # -------------------------------------------------------------- ciclo vivo
    def run_live_cycle(self) -> dict:
        with self._cycle_lock:
            if self.status != "running":
                return {"skipped": f"agente en estado {self.status}"}
            out = {}
            strategy = "llm" if self.use_llm else "model"
            for symbol in self.risk.symbols:
                try:
                    candles = self.mexc.closed_candles(symbol, limit=MIN_HISTORY)
                    last = candles[-1]
                    if self.db.is_processed("live", symbol, last.open_time):
                        out[symbol] = {"skipped": "sin vela nueva"}
                        continue
                    now_ms = int(self._clock() * 1000)
                    age_h = (now_ms - last.close_time) / HOUR_MS
                    if age_h > self.risk.max_data_age_hours:
                        raise StaleDataError(f"{symbol}: última vela cerrada con {age_h:.1f} h (máx. {self.risk.max_data_age_hours:g} h). "
                                             "No se opera con datos atrasados; revisa la fuente.")
                    exec_price = self.mexc.price(symbol)
                    out[symbol] = self.process_candle("live", symbol, candles, exec_price, now_ms, strategy)
                except StaleDataError as exc:
                    self.db.log_event("warn", "data", "stale_data", str(exc), symbol)
                    out[symbol] = {"error": str(exc)}
                except InvalidCandleData as exc:
                    self.db.log_event("error", "data", "invalid_candles", str(exc), symbol)
                    out[symbol] = {"error": str(exc)}
                except (MexcUnavailable, OllamaUnavailable, ModelContractError) as exc:
                    self.db.log_event("error", type(exc).__name__, "dependency_failure", str(exc), symbol)
                    self.pause(str(exc))
                    out[symbol] = {"error": str(exc)}
                    break
            return out

    # ----------------------------------------------------------------- replay
    def replay(self, steps=20, symbols=None, use_llm=None, reset=True) -> dict:
        """Repetición histórica (modo 'replay'): NO es la conexión en vivo. Usa los mismos controles y simulador."""
        mode = "replay"
        use_llm = self.use_llm if use_llm is None else use_llm
        syms = symbols or self.risk.symbols
        if reset:
            self.db.reset_mode(mode)
            self.db.set_state(f"halted:{mode}", "0")
        data = {s: self.mexc.closed_candles(s, limit=steps + MIN_HISTORY) for s in syms}
        records, error = [], None
        n = min(len(v) for v in data.values())
        if n < MIN_HISTORY + 1:
            raise InvalidCandleData(f"Replay: historia insuficiente ({n} velas); se necesitan ≥{MIN_HISTORY + 1}.")
        steps = min(steps, n - MIN_HISTORY)
        try:
            for k in range(steps):
                for s in syms:
                    cs = data[s]
                    idx = len(cs) - steps + k
                    exec_price = cs[idx + 1].open if idx + 1 < len(cs) else cs[idx].close   # apertura de t+1
                    now_ms = cs[idx].close_time + 60_000
                    records.append(self.process_candle(mode, s, cs[: idx + 1], exec_price, now_ms,
                                                       "llm" if use_llm else "model"))
        except OllamaUnavailable as exc:
            error = str(exc)
            self.db.log_event("error", "OllamaUnavailable", "replay_aborted", error)
        return {"mode": mode, "steps_done": len(records), "error": error, "records": records}

    # --------------------------------------------------------------- liquidar
    def liquidate(self, mode="live", prices: dict | None = None) -> list:
        """Venta explícita de todas las posiciones Paper. Solo por acción del usuario (o cierre de una comparación)."""
        results = []
        for symbol in self.risk.symbols:
            s = self.sleeve(mode, symbol)
            if s["qty"] <= 0:
                continue
            price = (prices or {}).get(symbol) or self.mexc.price(symbol)
            broker = self._broker(symbol, s)
            fill = broker.sell(price)
            ns = dict(cash=broker.cash, qty=broker.qty, entry_cost=broker.entry_cost,
                      last_equity=broker.equity(price), last_price=price)
            order = dict(side="sell", status=fill.status, reason="liquidación manual", qty=fill.qty, price=fill.price,
                         notional=fill.notional, fee=fill.fee, pnl=fill.pnl, cash_after=broker.cash, qty_after=broker.qty)
            eq = dict(cash=broker.cash, qty=broker.qty, price=price, equity=ns["last_equity"])
            self.db.record_manual_order(mode, symbol, order, eq, ns, int(self._clock() * 1000))
            self.db.log_event("info", "agent", "liquidate", f"{symbol}: posición liquidada a {fill.price:.4f}", symbol)
            results.append({"symbol": symbol, "pnl": fill.pnl})
        return results

    # --------------------------------------------------------------- resumen
    def snapshot(self, mode="live") -> dict:
        symbols = {}
        total_eq = total_cash = 0.0
        for sym in self.risk.symbols:
            s = self.sleeve(mode, sym)
            eq = s["cash"] + s["qty"] * s["last_price"] if s["last_price"] else s["cash"]
            total_eq += eq
            total_cash += s["cash"]
            last = (self.db.decisions(mode, sym, 1) or [None])[0]
            symbols[sym] = {"cash": s["cash"], "qty": s["qty"], "equity": eq, "last_price": s["last_price"],
                            "in_position": s["qty"] > 0, "last_decision": last}
        return {"mode": mode, "status": self.status, "pause_reason": self.pause_reason, "halted": self.halted(mode),
                "use_llm": self.use_llm, "total_equity": total_eq, "total_cash": total_cash,
                "initial_capital": self.risk.initial_capital, "symbols": symbols,
                "models": [m.info() for m in self.models.values()]}
