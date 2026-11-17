import { useCallback, useEffect, useRef, useState } from "react";

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    ...init,
    headers: init?.body ? { "Content-Type": "application/json" } : undefined,
  });
  if (!res.ok) {
    let detail = `${res.status}`;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* 본문이 JSON 이 아니면 상태 코드만 보여 준다 */
    }
    throw new Error(detail);
  }
  return res.json();
}

export const post = <T,>(path: string, body?: unknown) =>
  api<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

// ── 실시간 갱신: 서버 이벤트(SSE)가 오면 모든 조회를 다시 불러온다 ──
const listeners = new Set<() => void>();
let source: EventSource | null = null;
let timer: number | undefined;

function ping() {
  window.clearTimeout(timer);
  timer = window.setTimeout(() => listeners.forEach((fn) => fn()), 400);
}

function connect() {
  if (source) return;
  source = new EventSource("/api/events");
  source.onmessage = ping;
  window.setInterval(ping, 15000); // 이벤트가 없어도 시세 변화를 반영
}

export function refreshAll() {
  ping();
}

export function useApi<T>(path: string | null): { data: T | undefined; error: string | null; reload: () => void } {
  const [data, setData] = useState<T>();
  const [error, setError] = useState<string | null>(null);
  const seq = useRef(0);
  const load = useCallback(() => {
    if (!path) return;
    const mine = ++seq.current;
    api<T>(path)
      .then((d) => {
        if (mine === seq.current) {
          setData(d);
          setError(null);
        }
      })
      .catch((e: Error) => mine === seq.current && setError(e.message));
  }, [path]);
  useEffect(() => {
    setData(undefined);
    load();
    connect();
    listeners.add(load);
    return () => {
      listeners.delete(load);
    };
  }, [load]);
  return { data, error, reload: load };
}

// ── 응답 타입 (필요한 필드만) ──
export type Signal = "strong_buy" | "buy" | "hold" | "sell" | "strong_sell";

export interface Overview {
  now: string;
  market_open: boolean;
  mode: { trading: string; data: string; llm: string };
  models: { research: string; senior: string };
  equity: number;
  cash: number;
  cash_pct: number;
  positions_value: number;
  positions_count: number;
  initial_capital: number;
  day_pnl: number;
  day_pnl_pct: number;
  total_pnl: number;
  total_return: number;
  benchmark: string;
  benchmark_return: number | null;
  sharpe: number | null;
  max_drawdown: number | null;
  status: {
    kill_switch: boolean;
    halted: boolean;
    halt_reason: string | null;
    daily_stop: boolean;
    entries_blocked: string | null;
    scheduler_paused: boolean;
    scheduler_error: string | null;
    job: { kind: string; detail: string; started: string } | null;
    job_error: string | null;
    last_sim_day: string | null;
  };
  llm: { spent: number; budget: number; level: string; total_cost: number };
  last_meeting: Meeting | null;
}

export interface EquityPoint {
  day: string;
  equity: number;
  cash: number;
  benchmark: number | null;
}

export interface Position {
  id: number;
  symbol: string;
  name: string;
  desk: string;
  qty: number;
  avg_price: number;
  price: number;
  stop_price: number | null;
  target_price: number | null;
  sector: string | null;
  opened_at: string;
  market_value: number;
  unrealized_pnl: number;
  unrealized_pct: number;
  weight: number;
}

export interface Order {
  id: number;
  ts: string;
  symbol: string;
  name?: string;
  side: string;
  desk: string;
  qty: number;
  filled_qty: number;
  ref_price: number | null;
  fill_price: number | null;
  commission: number;
  status: string;
  reason: string | null;
  note: string | null;
  decision_id: number | null;
}

export interface Trade {
  id: number;
  symbol: string;
  name: string;
  desk: string;
  qty: number;
  entry_price: number;
  exit_price: number;
  entry_ts: string;
  exit_ts: string;
  pnl: number;
  pnl_pct: number;
  costs: number;
  exit_reason: string;
  sector: string | null;
  decision_id: number | null;
}

export interface Evidence {
  point: string;
  source: string;
}

export interface Report {
  id: number;
  meeting_id: number;
  ts: string;
  symbol: string;
  name?: string;
  agent: string;
  signal: Signal | null;
  confidence: number | null;
  summary: string | null;
  fwd_return: number | null;
  hit: number | null;
  payload: {
    evidence?: Evidence[];
    risks?: string[];
    data_gaps?: string[];
    horizon?: string;
    regime?: string;
    favored_sectors?: string[];
    avoided_sectors?: string[];
    candidates?: { symbol: string; strategy: string; reason: string }[];
    error?: string;
  };
}

export interface DebateTurn {
  id: number;
  symbol: string;
  round: number;
  side: "bull" | "bear";
  payload: { thesis: string; key_points: string[]; rebuttals: string[]; concessions: string[]; conviction: number };
}

export interface Decision {
  id: number;
  meeting_id: number;
  ts: string;
  symbol: string;
  action: "buy" | "sell" | "hold";
  conviction: number | null;
  rationale: string | null;
  dissent: string | null;
  consensus: number | null;
  proposal: { action: string; stop_price: number | null; target_price: number | null; size_multiplier: number; horizon_days: number; rationale: string } | null;
  risk_review: { verdict: string; size_multiplier: number; concerns: string[]; rationale: string } | null;
  decision: { final: { key_factors: string[]; size_multiplier: number } | null };
  status: string;
  status_detail: string | null;
  qty: number | null;
  stop_price: number | null;
  target_price: number | null;
}

export interface Meeting {
  id: number;
  ts: string;
  kind: string;
  status: string;
  stage: string | null;
  summary: string | null;
  cost_usd: number;
  error: string | null;
  buys?: number;
  sells?: number;
}

export interface MeetingSymbol {
  symbol: string;
  name: string;
  role: string;
  consensus: number | null;
  outcome: string;
  reports: Report[];
  debate: DebateTurn[];
  decision: Decision | null;
}

export interface MeetingDetail {
  meeting: Meeting;
  macro: Report | null;
  scalper: Report | null;
  symbols: MeetingSymbol[];
}

export interface Staff {
  id: string;
  title: string;
  division: string;
  tier: string;
  duty: string;
  reports: number;
  scored: number;
  hit_rate: number | null;
  avg_confidence?: number | null;
  avg_fwd_when_bullish?: number | null;
  avg_fwd_when_bearish?: number | null;
  llm_calls?: number;
  llm_cost_usd?: number;
  latest: { symbol: string; summary: string; ts: string; signal: Signal | null; confidence: number | null } | null;
}

export interface Lesson {
  id: number;
  ts: string;
  trade_id: number;
  symbol: string;
  sector: string;
  pnl_pct: number;
  lesson: string;
  payload: { what_worked: string[]; what_failed: string[] };
}
