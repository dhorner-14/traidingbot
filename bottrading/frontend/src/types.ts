export type Mode = "live" | "replay";
export type AgentStatus = "stopped" | "running" | "paused";

export interface Decision {
  id: number;
  mode: Mode;
  symbol: string;
  candle_date: string;
  price: number;
  forecast: number;
  delta: number;
  signal: string;
  strategy: "llm" | "model";
  llm_action: string | null;
  llm_reason: string | null;
  final_action: "comprar" | "mantener" | "vender";
  risk_status: "ok" | "ajustado" | "bloqueado" | "rechazado";
  risk_reason: string;
  latency_ms: number | null;
  exec_price: number;
  dist_sma20: number | null;
  vol20: number | null;
}

export interface SymbolState {
  cash: number;
  qty: number;
  equity: number;
  last_price: number;
  in_position: boolean;
  last_decision: Decision | null;
}

export interface Status {
  mode: Mode;
  status: AgentStatus;
  pause_reason: string;
  halted: boolean;
  use_llm: boolean;
  total_equity: number;
  total_cash: number;
  initial_capital: number;
  symbols: Record<string, SymbolState>;
  models: { symbol: string; version: string; type: string }[];
  risk: Record<string, number | string[]> & { delta: Record<string, number> };
  llm: { model: string; url: string };
}

export interface EquityPoint { candle_time: number; equity: number }
export interface EventRow { id: number; ts: string; level: "info" | "warn" | "error"; component: string; kind: string; message: string; symbol: string | null }
