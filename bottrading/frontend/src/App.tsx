import { useCallback, useEffect, useMemo, useState } from "react";
import { api, ApiError } from "./api";
import EquityChart from "./EquityChart";
import type { Decision, EquityPoint, EventRow, Mode, Status } from "./types";

const usd = (v: number) => v.toLocaleString("es-MX", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const pct = (v: number | null | undefined, d = 2) => (v == null ? "—" : `${(v * 100).toFixed(d)} %`);
const qty = (v: number) => v.toLocaleString("es-MX", { maximumFractionDigits: 6 });

const VERB: Record<Decision["final_action"], string> = { comprar: "Comprar", mantener: "Mantener", vender: "Vender" };
const STATUS_LABEL = { running: "En ejecución", paused: "En pausa", stopped: "Detenido" } as const;
const RISK_LABEL: Record<Decision["risk_status"], string> = {
  ok: "Aprobado", ajustado: "Aprobado con ajuste", bloqueado: "Bloqueado", rechazado: "Rechazado",
};

export default function App() {
  const [keyInput, setKeyInput] = useState("");
  const [key, setKey] = useState("");
  const [mode, setMode] = useState<Mode>("live");
  const [symbol, setSymbol] = useState("");
  const [status, setStatus] = useState<Status | null>(null);
  const [decisions, setDecisions] = useState<Decision[]>([]);
  const [equity, setEquity] = useState<EquityPoint[]>([]);
  const [events, setEvents] = useState<EventRow[]>([]);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    if (!key) return;
    try {
      const st = await api<Status>(`/api/status?mode=${mode}`, key);
      const sym = symbol && st.symbols[symbol] ? symbol : Object.keys(st.symbols)[0];
      const [dec, eq, ev] = await Promise.all([
        api<Decision[]>(`/api/decisions?mode=${mode}&symbol=${sym}&limit=15`, key),
        api<EquityPoint[]>(`/api/equity?mode=${mode}&symbol=${sym}&limit=300`, key),
        api<EventRow[]>(`/api/events?limit=8`, key),
      ]);
      setStatus(st); setSymbol(sym); setDecisions(dec); setEquity(eq); setEvents(ev); setError("");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Error inesperado al consultar el backend.");
      if (e instanceof ApiError && e.status === 401) { setKey(""); setStatus(null); }
    }
  }, [key, mode, symbol]);

  useEffect(() => {
    refresh();
    if (!key) return;
    const id = setInterval(refresh, 5000);
    return () => clearInterval(id);
  }, [refresh, key]);

  const act = async (path: string, ok: string, init: RequestInit = { method: "POST" }) => {
    setBusy(true); setNotice(""); setError("");
    try {
      await api(path, key, init);
      setNotice(ok);
      await refresh();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "No se pudo completar la acción.");
    } finally {
      setBusy(false);
    }
  };

  const connect = (e: React.FormEvent) => {
    e.preventDefault();
    setKey(keyInput.trim());
    setKeyInput("");           // la clave solo permanece en el estado de React, nunca en el navegador
  };

  const cur = status?.symbols[symbol];
  const d = cur?.last_decision ?? null;
  const sleeveCapital = status ? status.initial_capital / Object.keys(status.symbols).length : 0;
  const gain = status ? status.total_equity / status.initial_capital - 1 : 0;
  const latest = decisions[0] ?? null;
  const symbols = useMemo(() => (status ? Object.entries(status.symbols) : []), [status]);

  if (!key || !status) {
    return (
      <main className="gate">
        <h1>Bot de trading · modo Paper</h1>
        <p>Simulación con dinero virtual. Datos públicos de MEXC; no se envía ninguna orden real.</p>
        <form onSubmit={connect}>
          <label htmlFor="k">API key del backend</label>
          <input id="k" type="password" autoComplete="off" value={keyInput} onChange={(e) => setKeyInput(e.target.value)} />
          <button className="primary" disabled={keyInput.trim().length < 8}>Conectar</button>
        </form>
        {error && <p role="alert" className="msg error">{error}</p>}
        <p className="hint">La clave se define en el archivo .env del backend. Aquí solo se guarda en memoria mientras la pestaña esté abierta.</p>
      </main>
    );
  }

  return (
    <div className="app">
      <header className="top">
        <div>
          <h1>Bot de trading · modo Paper</h1>
          <p className="sub">Dinero virtual · datos públicos de MEXC · el LLM propone, el riesgo decide</p>
        </div>
        <div className="controls">
          <span className={`state ${status.status}`} aria-live="polite">{STATUS_LABEL[status.status]}</span>
          <button className="primary" disabled={busy || status.status === "running"} onClick={() => act("/api/start", "Agente iniciado.")}>Iniciar</button>
          <button disabled={busy || status.status !== "running"} onClick={() => act("/api/pause", "Agente en pausa.")}>Pausar</button>
          <button className="danger" disabled={busy || status.status === "stopped"} onClick={() => act("/api/stop", "Agente detenido. Las posiciones siguen abiertas.")}>Detener</button>
        </div>
      </header>

      {status.pause_reason && status.status !== "running" && <p role="status" className="msg warn">{status.pause_reason}</p>}
      {status.halted && <p role="alert" className="msg error">Límite de riesgo alcanzado: las órdenes nuevas están bloqueadas. Revisa la posición y pulsa Iniciar para reanudar.</p>}
      {error && <p role="alert" className="msg error">{error}</p>}
      {notice && <p role="status" className="msg ok">{notice}</p>}

      <div className="toolbar">
        <div className="seg" role="group" aria-label="Fuente de datos">
          <button aria-pressed={mode === "live"} onClick={() => setMode("live")}>En vivo</button>
          <button aria-pressed={mode === "replay"} onClick={() => setMode("replay")}>Repetición histórica</button>
        </div>
        <button disabled={busy || status.status !== "running"} onClick={() => act("/api/cycle/run", "Ciclo ejecutado.")}>Ejecutar ciclo ahora</button>
        <button disabled={busy} onClick={() => act("/api/replay/run", "Repetición terminada.", { method: "POST", body: JSON.stringify({ steps: 20 }) })}>
          Repetir últimos 20 días
        </button>
        <button disabled={busy} onClick={() => { if (confirm(`Se venderán todas las posiciones (${mode === "live" ? "en vivo" : "repetición"}). ¿Continuar?`)) act(`/api/liquidate?mode=${mode}`, "Posiciones liquidadas."); }}>
          Liquidar posiciones
        </button>
      </div>

      <div className="layout">
        <aside className="rail" aria-label="Activos">
          <div className="total">
            <span className="label">Patrimonio total</span>
            <strong>{usd(status.total_equity)} <small>USDT</small></strong>
            <span className={gain >= 0 ? "pos" : "neg"}>{gain >= 0 ? "+" : ""}{pct(gain)} desde {usd(status.initial_capital)}</span>
          </div>
          <ul>
            {symbols.map(([s, v]) => (
              <li key={s}>
                <button className={s === symbol ? "asset on" : "asset"} aria-current={s === symbol} onClick={() => setSymbol(s)}>
                  <span className="sym">{s.replace("USDT", "")}<small>/USDT</small></span>
                  <span className="eq">{usd(v.equity)}</span>
                  <span className={v.in_position ? "tag in" : "tag out"}>{v.in_position ? "En posición" : "En efectivo"}</span>
                </button>
              </li>
            ))}
          </ul>
          <p className="hint">Modelos: {status.models.map((m) => `${m.type} ${m.version}`).filter((x, i, a) => a.indexOf(x) === i).join(", ")}<br />LLM local: {status.llm.model}</p>
        </aside>

        <main className="main">
          {d ? (
            <section className="decision" aria-label="Última decisión">
              <p className="when">{symbol} · vela cerrada del {d.candle_date} · cierre {usd(d.price)} USDT{d.strategy === "model" ? " · sin LLM" : ""}</p>
              <h2 className={`act ${d.final_action}`}>{VERB[d.final_action]} {symbol}</h2>
              <p className="why">
                El modelo estima {pct(d.forecast)} para el siguiente día (umbral {pct(d.delta)}), por eso la señal es{" "}
                {d.signal === "comprar" ? "estar comprado" : "estar en efectivo"}.
                {d.llm_action && <> El LLM propuso <b>{d.llm_action}</b>{d.llm_reason ? `: ${d.llm_reason}` : ""}.</>}
                {!d.llm_action && d.llm_reason && <> {d.llm_reason}.</>}
              </p>
              <p className={`risk ${d.risk_status}`}><b>Riesgo: {RISK_LABEL[d.risk_status]}.</b> {d.risk_reason}</p>
            </section>
          ) : (
            <section className="decision"><h2>Sin decisiones todavía</h2><p className="why">Pulsa Iniciar para operar en vivo (una decisión por vela diaria cerrada) o “Repetir últimos 20 días” para ver el sistema funcionando con datos históricos.</p></section>
          )}

          <section className="tiles" aria-label="Indicadores y posición">
            <div><span className="label">Pronóstico</span><strong>{pct(d?.forecast, 3)}</strong></div>
            <div><span className="label">Distancia a SMA20</span><strong>{pct(d?.dist_sma20)}</strong></div>
            <div><span className="label">Volatilidad 20 días</span><strong>{pct(d?.vol20)}</strong></div>
            <div><span className="label">Posición</span><strong>{cur ? qty(cur.qty) : "—"}</strong></div>
            <div><span className="label">Efectivo</span><strong>{cur ? usd(cur.cash) : "—"}</strong></div>
            <div><span className="label">Patrimonio del símbolo</span><strong>{cur ? usd(cur.equity) : "—"}</strong></div>
          </section>

          <section>
            <h3>Patrimonio de {symbol}</h3>
            <EquityChart points={equity} baseline={sleeveCapital} />
          </section>

          <section>
            <h3>Últimas decisiones de {symbol}</h3>
            {decisions.length === 0 ? <p className="empty">Aún no hay decisiones en este modo.</p> : (
              <div className="scroll">
                <table>
                  <thead><tr><th>Vela</th><th>Pronóstico</th><th>LLM</th><th>Acción final</th><th>Riesgo</th><th>Precio de ejecución</th></tr></thead>
                  <tbody>
                    {decisions.map((x) => (
                      <tr key={x.id}>
                        <td>{x.candle_date}</td><td>{pct(x.forecast, 3)}</td><td>{x.llm_action ?? "—"}</td>
                        <td><span className={`act-s ${x.final_action}`}>{VERB[x.final_action]}</span></td>
                        <td title={x.risk_reason}>{RISK_LABEL[x.risk_status]}</td><td>{usd(x.exec_price)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {latest?.latency_ms != null && <p className="hint">Última consulta al LLM: {Math.round(latest.latency_ms)} ms.</p>}
          </section>

          <section>
            <h3>Eventos y errores recientes</h3>
            {events.length === 0 ? <p className="empty">Sin eventos.</p> : (
              <ul className="events">
                {events.map((e) => (
                  <li key={e.id} className={e.level}><time>{e.ts.replace("T", " ").slice(0, 19)}</time><span>{e.symbol ? `${e.symbol}: ` : ""}{e.message}</span></li>
                ))}
              </ul>
            )}
          </section>
        </main>
      </div>
    </div>
  );
}
