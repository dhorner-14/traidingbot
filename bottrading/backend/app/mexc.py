"""Cliente de DATOS PÚBLICOS de MEXC (sin claves). No existe ninguna ruta de órdenes reales en este proyecto."""
from __future__ import annotations

import math
import time
from dataclasses import dataclass

import requests

from .errors import InvalidCandleData, MexcUnavailable

DAY_MS = 86_400_000


@dataclass(frozen=True)
class Candle:
    open_time: int      # ms UTC
    open: float
    high: float
    low: float
    close: float
    volume: float
    close_time: int     # ms UTC


def parse_klines(rows, symbol: str) -> list[Candle]:
    if not isinstance(rows, list):
        raise InvalidCandleData(f"{symbol}: respuesta inesperada de MEXC ({str(rows)[:120]}). Revisa el símbolo.")
    out = []
    for r in rows:
        if not isinstance(r, (list, tuple)) or len(r) < 6:
            raise InvalidCandleData(f"{symbol}: cada vela debe traer ≥6 campos; recibida {r!r}.")
        try:
            ot = int(r[0])
            ct = int(r[6]) if len(r) > 6 else ot + DAY_MS - 1
            out.append(Candle(ot, float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]), ct))
        except (TypeError, ValueError) as exc:
            raise InvalidCandleData(f"{symbol}: vela con valores no numéricos {r!r}.") from exc
    return out


def validate_candles(candles: list[Candle], symbol: str):
    if not candles:
        raise InvalidCandleData(f"{symbol}: MEXC no devolvió velas cerradas.")
    prev = None
    for c in candles:
        vals = (c.open, c.high, c.low, c.close)
        if not all(math.isfinite(v) and v > 0 for v in vals) or not math.isfinite(c.volume) or c.volume < 0:
            raise InvalidCandleData(f"{symbol}: vela {c.open_time} con precios no finitos/no positivos o volumen negativo.")
        if c.high < max(c.open, c.low, c.close) or c.low > min(c.open, c.high, c.close):
            raise InvalidCandleData(f"{symbol}: vela {c.open_time} con high/low incoherentes.")
        if prev is not None:
            if c.open_time == prev:
                raise InvalidCandleData(f"{symbol}: velas duplicadas en {c.open_time}.")
            if c.open_time < prev:
                raise InvalidCandleData(f"{symbol}: velas desordenadas ({c.open_time} < {prev}).")
            if c.open_time - prev != DAY_MS:
                raise InvalidCandleData(f"{symbol}: hueco en la serie diaria entre {prev} y {c.open_time}; "
                                        "no se rellena: espera datos completos o amplía la consulta.")
        prev = c.open_time


class MexcClient:
    def __init__(self, base_url="https://api.mexc.com", timeout=10.0, retries=3, base_delay=1.0,
                 sleep=time.sleep, session=None, clock=time.time):
        self.base_url = base_url.rstrip("/")
        self.timeout, self.retries, self.base_delay = timeout, retries, base_delay
        self._sleep, self.http, self._clock = sleep, session or requests.Session(), clock

    def _get(self, path: str, params: dict):
        last = "sin detalle"
        rate_limited = False
        for attempt in range(self.retries):
            try:
                r = self.http.get(f"{self.base_url}{path}", params=params, timeout=self.timeout)
                if r.status_code == 429:
                    rate_limited, last = True, "límite de solicitudes (HTTP 429)"
                elif r.status_code >= 500:
                    rate_limited, last = False, f"error del servidor HTTP {r.status_code}"
                elif r.status_code >= 400:
                    raise MexcUnavailable(f"MEXC rechazó la solicitud ({r.status_code}): {r.text[:150]}. "
                                          f"Revisa el símbolo/parámetros {params}.")
                else:
                    return r.json()
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
                rate_limited, last = False, f"{type(exc).__name__}"
            except ValueError as exc:  # JSON inválido
                rate_limited, last = False, "respuesta no es JSON válido"
            if attempt < self.retries - 1:
                self._sleep(self.base_delay * 2 ** attempt)
        raise MexcUnavailable(f"MEXC no respondió tras {self.retries} intentos ({last}). Se pausan las decisiones nuevas; "
                              "revisa conexión o espera y pulsa Iniciar.", rate_limited=rate_limited)

    def closed_candles(self, symbol: str, limit: int = 60) -> list[Candle]:
        """Últimas velas diarias CERRADAS (excluye la vela del día en curso)."""
        rows = self._get("/api/v3/klines", {"symbol": symbol, "interval": "1d", "limit": min(limit + 1, 500)})
        candles = parse_klines(rows, symbol)
        now_ms = int(self._clock() * 1000)
        closed = [c for c in candles if c.open_time + DAY_MS <= now_ms]
        validate_candles(closed, symbol)
        return closed[-limit:]

    def price(self, symbol: str) -> float:
        data = self._get("/api/v3/ticker/price", {"symbol": symbol})
        try:
            p = float(data["price"])
        except (KeyError, TypeError, ValueError) as exc:
            raise InvalidCandleData(f"{symbol}: respuesta de precio inválida {str(data)[:100]}.") from exc
        if not math.isfinite(p) or p <= 0:
            raise InvalidCandleData(f"{symbol}: precio no válido {p}.")
        return p
