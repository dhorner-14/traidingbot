"""API del bot. Ejecutar:  uvicorn app.main:create_app --factory --port 8000   (desde backend/)

TODOS los endpoints /api/* (salvo /api/health) exigen la cabecera X-API-Key. No existe ninguna ruta de órdenes reales.
"""
from __future__ import annotations

import secrets
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field

from .agent import Agent
from .config import MODES, Settings, load_risk, load_settings, save_risk
from .db import Database
from .errors import ConfigError, InvalidCandleData, MexcUnavailable, OllamaUnavailable
from .llm import OllamaClient
from .mexc import MexcClient
from .model import load_models


class ReplayBody(BaseModel):
    steps: int = Field(20, ge=1, le=60)
    use_llm: bool | None = None
    reset: bool = True
    symbols: list[str] | None = None


class RiskUpdate(BaseModel):
    max_total_exposure: float | None = Field(None, gt=0, le=1)
    max_position_fraction: float | None = Field(None, gt=0, le=1)
    max_daily_loss: float | None = Field(None, gt=0, le=1)
    max_data_age_hours: float | None = Field(None, gt=0, le=240)
    slippage_bps: float | None = Field(None, ge=0, le=500)


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str = Field(max_length=4000)


class ChatBody(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=10)


def create_app(settings: Settings | None = None, agent: Agent | None = None, db: Database | None = None) -> FastAPI:
    settings = settings or load_settings()
    if agent is None:
        risk = load_risk(settings.risk_path)
        db = db or Database(settings.db_path)
        models = load_models(settings.models_dir, risk.symbols)
        llm = OllamaClient(settings.ollama_url, settings.ollama_model)
        mexc = MexcClient(settings.mexc_base_url)
        agent = Agent(risk, db, mexc, llm, models, poll_seconds=settings.poll_seconds)
    db = agent.db

    @asynccontextmanager
    async def lifespan(app):
        yield
        agent.shutdown()

    app = FastAPI(title="Bot de trading Paper (MEXC datos públicos)", version="1.0", lifespan=lifespan)
    app.state.agent = agent

    def require_key(x_api_key: str | None = Header(default=None)):
        if not x_api_key:
            db.log_event("warn", "auth", "missing_key", "Solicitud sin X-API-Key rechazada")
            raise HTTPException(401, "Falta la cabecera X-API-Key.")
        if not secrets.compare_digest(x_api_key.encode(), settings.api_key.encode()):
            db.log_event("warn", "auth", "bad_key", "Solicitud con API key incorrecta rechazada")
            raise HTTPException(401, "API key incorrecta.")

    auth = [Depends(require_key)]

    def check_mode(mode: str) -> str:
        if mode not in MODES:
            raise HTTPException(422, f"mode debe ser uno de {MODES}.")
        return mode

    @app.get("/api/health")
    def health():
        return {"ok": True, "status": agent.status}

    @app.get("/api/status", dependencies=auth)
    def status(mode: str = "live"):
        snap = agent.snapshot(check_mode(mode))
        snap["risk"] = {k: v for k, v in agent.risk.to_dict().items() if k != "symbol_rules"}
        snap["risk"]["delta"] = {s: r.delta for s, r in agent.risk.symbol_rules.items()}
        snap["llm"] = {"model": settings.ollama_model, "url": settings.ollama_url}
        return snap

    @app.post("/api/start", dependencies=auth)
    def start():
        agent.start()
        return {"status": agent.status}

    @app.post("/api/pause", dependencies=auth)
    def pause():
        agent.pause("pausa manual")
        return {"status": agent.status}

    @app.post("/api/stop", dependencies=auth)
    def stop():
        agent.stop()
        return {"status": agent.status, "nota": "Detener NO liquida posiciones; usa /api/liquidate si quieres cerrarlas."}

    @app.post("/api/liquidate", dependencies=auth)
    def liquidate(mode: str = "live"):
        try:
            return {"liquidadas": agent.liquidate(check_mode(mode))}
        except (MexcUnavailable, InvalidCandleData) as exc:
            raise HTTPException(503, str(exc))

    @app.post("/api/cycle/run", dependencies=auth)
    def run_cycle():
        if agent.status != "running":
            raise HTTPException(409, f"El agente está en '{agent.status}'. Pulsa Iniciar antes de ejecutar un ciclo.")
        return agent.run_live_cycle()

    @app.post("/api/replay/run", dependencies=auth)
    def replay(body: ReplayBody):
        bad = [s for s in (body.symbols or []) if s not in agent.risk.symbols]
        if bad:
            raise HTTPException(422, f"Símbolos no permitidos: {bad}.")
        try:
            return agent.replay(body.steps, body.symbols, body.use_llm, body.reset)
        except (MexcUnavailable, InvalidCandleData) as exc:
            raise HTTPException(503, str(exc))

    @app.get("/api/decisions", dependencies=auth)
    def decisions(mode: str = "live", symbol: str | None = None, limit: int = Query(100, ge=1, le=1000)):
        return db.decisions(check_mode(mode), symbol, limit)

    @app.get("/api/orders", dependencies=auth)
    def orders(mode: str = "live", symbol: str | None = None, limit: int = Query(100, ge=1, le=1000)):
        return db.orders(check_mode(mode), symbol, limit)

    @app.get("/api/equity", dependencies=auth)
    def equity(mode: str = "live", symbol: str | None = None, limit: int = Query(500, ge=1, le=5000)):
        return db.equity_series(check_mode(mode), symbol, limit)

    @app.get("/api/events", dependencies=auth)
    def events(limit: int = Query(100, ge=1, le=1000)):
        return db.events(limit)

    @app.put("/api/config/risk", dependencies=auth)
    def update_risk(body: RiskUpdate):
        changes = body.model_dump(exclude_none=True)
        old = {k: getattr(agent.risk, k) for k in changes}
        for k, v in changes.items():
            setattr(agent.risk, k, v)
        try:
            agent.risk.validate()
        except ConfigError as exc:
            for k, v in old.items():
                setattr(agent.risk, k, v)
            raise HTTPException(422, str(exc))
        save_risk(agent.risk, settings.risk_path)
        db.log_event("info", "audit", "risk_config_changed", f"{old} → {changes}")
        return {"actualizado": changes}

    @app.post("/api/llm/chat", dependencies=auth)
    def llm_chat(body: ChatBody):
        """Proxy autenticado hacia Ollama local (Ollama por sí solo no exige autenticación)."""
        try:
            content = agent.llm.chat_raw([m.model_dump() for m in body.messages])
        except OllamaUnavailable as exc:
            raise HTTPException(503, str(exc))
        return {"model": settings.ollama_model, "content": content}

    return app
