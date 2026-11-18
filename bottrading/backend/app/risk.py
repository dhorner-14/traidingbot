"""Controles de riesgo DETERMINISTAS. La propuesta del LLM nunca los sustituye ni los modifica."""
from __future__ import annotations

from dataclasses import dataclass

from .config import RiskConfig


@dataclass
class RiskContext:
    symbol: str
    action: str               # comprar | mantener | vender (propuesta ya validada)
    running: bool             # el agente está en 'running'
    halted: bool              # bloqueo previo por límite de riesgo
    data_age_hours: float
    has_position: bool
    cash: float
    total_equity: float       # patrimonio de todos los símbolos
    total_exposure: float     # valor invertido en todos los símbolos
    day_loss: float           # pérdida de la vela diaria en este símbolo (fracción, >= 0)


@dataclass
class RiskVerdict:
    action: str               # acción final: comprar | mantener | vender
    status: str               # ok | bloqueado | ajustado
    reason: str
    max_notional: float = 0.0
    trigger_halt: bool = False


def evaluate(cfg: RiskConfig, ctx: RiskContext) -> RiskVerdict:
    rules = cfg.symbol_rules[ctx.symbol]
    if not ctx.running:
        return RiskVerdict("mantener", "bloqueado", "agente detenido o en pausa: no se envían órdenes")
    if ctx.halted:
        return RiskVerdict("mantener", "bloqueado", "bloqueo de riesgo activo: reanuda manualmente tras revisar la posición")
    if ctx.day_loss >= cfg.max_daily_loss:
        return RiskVerdict("mantener", "bloqueado",
                           f"pérdida diaria {ctx.day_loss:.2%} ≥ límite {cfg.max_daily_loss:.2%}: órdenes bloqueadas",
                           trigger_halt=True)
    if ctx.data_age_hours > cfg.max_data_age_hours:
        return RiskVerdict("mantener", "bloqueado",
                           f"dato con {ctx.data_age_hours:.1f} h de antigüedad (máx. {cfg.max_data_age_hours:g} h)")
    if ctx.action == "mantener":
        return RiskVerdict("mantener", "ok", "sin orden")
    if ctx.action == "vender":
        if not ctx.has_position:
            return RiskVerdict("mantener", "bloqueado", "no hay posición que vender (sin ventas en corto)")
        return RiskVerdict("vender", "ok", "venta de la posición existente", )
    # comprar
    if ctx.has_position:
        return RiskVerdict("mantener", "ajustado", "ya hay posición abierta: no se recompra")
    exposure_room = cfg.max_total_exposure * ctx.total_equity - ctx.total_exposure
    max_notional = min(ctx.cash * cfg.max_position_fraction, exposure_room)
    if max_notional < rules.min_notional:
        why = "efectivo insuficiente" if ctx.cash < rules.min_notional else "límite de exposición total alcanzado"
        return RiskVerdict("mantener", "bloqueado", f"{why} (disponible {max(max_notional, 0):.2f} < mínimo {rules.min_notional:g})")
    status = "ajustado" if max_notional < ctx.cash - 1e-9 else "ok"
    return RiskVerdict("comprar", status, "compra dentro de los límites", max_notional=max_notional)
