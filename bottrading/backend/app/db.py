"""Persistencia SQLite: velas procesadas, decisiones, órdenes Paper, patrimonio, estado y eventos/errores.

La idempotencia se garantiza con la clave primaria (mode, symbol, open_time) de `processed`:
la decisión, la orden, el patrimonio y el estado del símbolo se guardan en UNA transacción junto con esa marca.
"""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sleeves (
  mode TEXT, symbol TEXT, cash REAL NOT NULL, qty REAL NOT NULL, entry_cost REAL NOT NULL,
  last_equity REAL NOT NULL, last_price REAL NOT NULL, PRIMARY KEY (mode, symbol));
CREATE TABLE IF NOT EXISTS processed (
  mode TEXT, symbol TEXT, open_time INTEGER, ts TEXT NOT NULL, PRIMARY KEY (mode, symbol, open_time));
CREATE TABLE IF NOT EXISTS decisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, mode TEXT, symbol TEXT, candle_time INTEGER, candle_date TEXT,
  price REAL, forecast REAL, delta REAL, signal TEXT, strategy TEXT, llm_action TEXT, llm_reason TEXT,
  final_action TEXT, risk_status TEXT, risk_reason TEXT, latency_ms REAL, exec_price REAL,
  dist_sma20 REAL, vol20 REAL);
CREATE TABLE IF NOT EXISTS orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, mode TEXT, symbol TEXT, candle_time INTEGER, side TEXT,
  status TEXT, reason TEXT, qty REAL, price REAL, notional REAL, fee REAL, pnl REAL, cash_after REAL, qty_after REAL);
CREATE TABLE IF NOT EXISTS equity (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, mode TEXT, symbol TEXT, candle_time INTEGER,
  cash REAL, qty REAL, price REAL, equity REAL);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, level TEXT, component TEXT, kind TEXT, message TEXT, symbol TEXT);
CREATE INDEX IF NOT EXISTS ix_dec ON decisions(mode, symbol, id);
CREATE INDEX IF NOT EXISTS ix_eq ON equity(mode, symbol, id);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        with self.lock, self.conn:
            self.conn.executescript(SCHEMA)

    def close(self):
        with self.lock:
            self.conn.close()

    # ---- estado general ----
    def get_state(self, key, default=None):
        with self.lock:
            row = self.conn.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_state(self, key, value):
        with self.lock, self.conn:
            self.conn.execute("INSERT INTO state(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                              (key, str(value)))

    # ---- sleeves (efectivo y posición por símbolo) ----
    def get_sleeve(self, mode, symbol):
        with self.lock:
            row = self.conn.execute("SELECT * FROM sleeves WHERE mode=? AND symbol=?", (mode, symbol)).fetchone()
        return dict(row) if row else None

    def all_sleeves(self, mode):
        with self.lock:
            rows = self.conn.execute("SELECT * FROM sleeves WHERE mode=?", (mode,)).fetchall()
        return {r["symbol"]: dict(r) for r in rows}

    def save_sleeve(self, mode, symbol, s: dict):
        with self.lock, self.conn:
            self._save_sleeve(mode, symbol, s)

    def _save_sleeve(self, mode, symbol, s):
        self.conn.execute(
            "INSERT INTO sleeves(mode,symbol,cash,qty,entry_cost,last_equity,last_price) VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(mode,symbol) DO UPDATE SET cash=excluded.cash, qty=excluded.qty, entry_cost=excluded.entry_cost, "
            "last_equity=excluded.last_equity, last_price=excluded.last_price",
            (mode, symbol, s["cash"], s["qty"], s["entry_cost"], s["last_equity"], s["last_price"]))

    # ---- idempotencia ----
    def is_processed(self, mode, symbol, open_time) -> bool:
        with self.lock:
            return self.conn.execute("SELECT 1 FROM processed WHERE mode=? AND symbol=? AND open_time=?",
                                     (mode, symbol, open_time)).fetchone() is not None

    def record_step(self, mode, symbol, open_time, decision: dict, order: dict | None, equity_row: dict, sleeve: dict) -> bool:
        """Guarda todo en una transacción. Devuelve False (sin escribir nada) si la vela ya estaba procesada."""
        ts = now_iso()
        with self.lock:
            try:
                with self.conn:
                    cur = self.conn.execute("INSERT OR IGNORE INTO processed(mode,symbol,open_time,ts) VALUES(?,?,?,?)",
                                            (mode, symbol, open_time, ts))
                    if cur.rowcount == 0:
                        return False
                    d = {**decision, "ts": ts, "mode": mode, "symbol": symbol, "candle_time": open_time}
                    cols = list(d)
                    self.conn.execute(f"INSERT INTO decisions({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
                                      [d[c] for c in cols])
                    if order:
                        o = {**order, "ts": ts, "mode": mode, "symbol": symbol, "candle_time": open_time}
                        oc = list(o)
                        self.conn.execute(f"INSERT INTO orders({','.join(oc)}) VALUES({','.join('?' * len(oc))})",
                                          [o[c] for c in oc])
                    e = {**equity_row, "ts": ts, "mode": mode, "symbol": symbol, "candle_time": open_time}
                    ec = list(e)
                    self.conn.execute(f"INSERT INTO equity({','.join(ec)}) VALUES({','.join('?' * len(ec))})",
                                      [e[c] for c in ec])
                    self._save_sleeve(mode, symbol, sleeve)
            except sqlite3.Error:
                raise
        return True

    def record_manual_order(self, mode, symbol, order: dict, equity_row: dict, sleeve: dict, candle_time: int):
        """Orden fuera del ciclo de velas (liquidación manual explícita): orden + patrimonio + estado, una transacción."""
        ts = now_iso()
        with self.lock, self.conn:
            o = {**order, "ts": ts, "mode": mode, "symbol": symbol, "candle_time": candle_time}
            oc = list(o)
            self.conn.execute(f"INSERT INTO orders({','.join(oc)}) VALUES({','.join('?' * len(oc))})", [o[c] for c in oc])
            e = {**equity_row, "ts": ts, "mode": mode, "symbol": symbol, "candle_time": candle_time}
            ec = list(e)
            self.conn.execute(f"INSERT INTO equity({','.join(ec)}) VALUES({','.join('?' * len(ec))})", [e[c] for c in ec])
            self._save_sleeve(mode, symbol, sleeve)

    def reset_mode(self, mode):
        with self.lock, self.conn:
            for t in ("sleeves", "processed", "decisions", "orders", "equity"):
                self.conn.execute(f"DELETE FROM {t} WHERE mode=?", (mode,))

    # ---- eventos / errores ----
    def log_event(self, level, component, kind, message, symbol=None):
        with self.lock, self.conn:
            self.conn.execute("INSERT INTO events(ts,level,component,kind,message,symbol) VALUES(?,?,?,?,?,?)",
                              (now_iso(), level, component, kind, str(message)[:500], symbol))

    # ---- consultas ----
    def _query(self, sql, params=()):
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def decisions(self, mode="live", symbol=None, limit=100):
        q, p = "SELECT * FROM decisions WHERE mode=?", [mode]
        if symbol:
            q += " AND symbol=?"; p.append(symbol)
        return self._query(q + " ORDER BY id DESC LIMIT ?", p + [limit])

    def orders(self, mode="live", symbol=None, limit=100):
        q, p = "SELECT * FROM orders WHERE mode=?", [mode]
        if symbol:
            q += " AND symbol=?"; p.append(symbol)
        return self._query(q + " ORDER BY id DESC LIMIT ?", p + [limit])

    def equity_series(self, mode="live", symbol=None, limit=500):
        q, p = "SELECT * FROM equity WHERE mode=?", [mode]
        if symbol:
            q += " AND symbol=?"; p.append(symbol)
        rows = self._query(q + " ORDER BY id DESC LIMIT ?", p + [limit])
        return list(reversed(rows))

    def events(self, limit=100):
        return self._query("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))
