"""Simulador Paper: dinero virtual, solo posición comprada o efectivo, sin apalancamiento ni cortos.

Supuestos documentados:
- Cobra `fee_rate` sobre el monto negociado en cada compra y cada venta (0.0 % por defecto).
- Deslizamiento: la compra se ejecuta a ref*(1+bps) y la venta a ref*(1-bps).
- Cantidad redondeada hacia abajo al `qty_step` del par; se rechaza si el monto queda bajo `min_notional`.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class Fill:
    side: str              # buy | sell
    status: str            # executed | rejected
    reason: str = ""
    qty: float = 0.0
    price: float = 0.0
    notional: float = 0.0
    fee: float = 0.0
    pnl: float | None = None   # solo en ventas: proceeds - costo de entrada (incluye comisiones)


class PaperBroker:
    def __init__(self, cash: float, qty: float = 0.0, entry_cost: float = 0.0, fee_rate: float = 0.0,
                 slippage_bps: float = 0.0, qty_step: float = 1e-6, min_notional: float = 5.0):
        self.cash, self.qty, self.entry_cost = float(cash), float(qty), float(entry_cost)
        self.fee_rate, self.slip = float(fee_rate), float(slippage_bps) / 10_000.0
        self.qty_step, self.min_notional = float(qty_step), float(min_notional)

    def equity(self, price: float) -> float:
        return self.cash + self.qty * price

    def _floor_qty(self, q: float) -> float:
        steps = math.floor(q / self.qty_step + 1e-9)
        decimals = max(0, -int(math.floor(math.log10(self.qty_step))))
        return round(steps * self.qty_step, decimals)

    def buy(self, ref_price: float, max_notional: float) -> Fill:
        if not (math.isfinite(ref_price) and ref_price > 0):
            return Fill("buy", "rejected", "precio de referencia no válido")
        if self.qty > 0:
            return Fill("buy", "rejected", "ya hay posición abierta")
        if self.cash < self.min_notional:
            return Fill("buy", "rejected", f"saldo insuficiente ({self.cash:.2f} < mínimo {self.min_notional:g})")
        budget = min(max_notional, self.cash)
        price = ref_price * (1 + self.slip)
        qty = self._floor_qty(budget / (price * (1 + self.fee_rate)))
        notional = qty * price
        if qty <= 0 or notional < self.min_notional:
            return Fill("buy", "rejected", f"tamaño mínimo no alcanzado (monto {notional:.2f} < {self.min_notional:g})")
        fee = notional * self.fee_rate
        cost = notional + fee
        if cost > self.cash + 1e-9:
            return Fill("buy", "rejected", "saldo insuficiente para cubrir monto y costos")
        self.cash -= cost
        self.qty, self.entry_cost = qty, cost
        return Fill("buy", "executed", "ok", qty, price, notional, fee)

    def sell(self, ref_price: float) -> Fill:
        if not (math.isfinite(ref_price) and ref_price > 0):
            return Fill("sell", "rejected", "precio de referencia no válido")
        if self.qty <= 0:
            return Fill("sell", "rejected", "no hay posición que vender (sin ventas en corto)")
        price = ref_price * (1 - self.slip)
        notional = self.qty * price
        fee = notional * self.fee_rate
        proceeds = notional - fee
        pnl = proceeds - self.entry_cost
        qty = self.qty
        self.cash += proceeds
        self.qty, self.entry_cost = 0.0, 0.0
        return Fill("sell", "executed", "ok", qty, price, notional, fee, pnl)
