import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import type { Signal } from "./api";

// ── 숫자·날짜 포맷 ──
const usd0 = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
const usd2 = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2 });
const et = new Intl.DateTimeFormat("ko-KR", { timeZone: "America/New_York", month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false });
const etDay = new Intl.DateTimeFormat("ko-KR", { timeZone: "America/New_York", year: "numeric", month: "numeric", day: "numeric" });

export const money = (v: number | null | undefined, cents = false) => (v == null ? "–" : (cents ? usd2 : usd0).format(v));
export const pct = (v: number | null | undefined, digits = 2) => (v == null ? "–" : `${(v * 100).toFixed(digits)}%`);
export const signed = (v: number | null | undefined, digits = 2) => (v == null ? "–" : `${v >= 0 ? "+" : ""}${(v * 100).toFixed(digits)}%`);
export const num = (v: number | null | undefined, digits = 2) => (v == null ? "–" : v.toFixed(digits));
/** 미국 동부시간(ET) 기준 표시 */
export const when = (iso: string | null | undefined) => (iso ? et.format(new Date(iso)) : "–");
export const day = (iso: string | null | undefined) => (iso ? etDay.format(new Date(iso)) : "–");

/** 등락 표시: 색만으로 구분하지 않도록 ▲▼ 기호를 함께 쓴다 (상승 빨강, 하락 파랑). */
export function Delta({ value, money: asMoney, digits = 2 }: { value: number | null | undefined; money?: boolean; digits?: number }) {
  if (value == null) return <span className="muted">–</span>;
  const cls = value > 0 ? "up" : value < 0 ? "down" : "ink2";
  const mark = value > 0 ? "▲" : value < 0 ? "▼" : "";
  const body = asMoney ? usd0.format(Math.abs(value)) : `${Math.abs(value * 100).toFixed(digits)}%`;
  return (
    <span className={`num ${cls}`}>
      {mark} {body}
    </span>
  );
}

export function Stat({ label, value, sub, hero }: { label: string; value: ReactNode; sub?: ReactNode; hero?: boolean }) {
  return (
    <div className={`card stat ${hero ? "hero" : ""}`}>
      <div className="label">{label}</div>
      <div className="value num">{value}</div>
      {sub != null && <div className="sub">{sub}</div>}
    </div>
  );
}

const SIGNAL_KO: Record<Signal, string> = { strong_buy: "강력매수", buy: "매수", hold: "중립", sell: "매도", strong_sell: "강력매도" };

export function SignalBadge({ signal }: { signal: Signal | null | undefined }) {
  if (!signal) return <span className="sig hold">보고 없음</span>;
  const cls = signal.includes("buy") ? "buy" : signal.includes("sell") ? "sell" : "hold";
  return <span className={`sig ${cls}`}>{SIGNAL_KO[signal]}</span>;
}

const ACTION_KO: Record<string, string> = { buy: "매수", sell: "매도", hold: "보유·관망" };
export function ActionBadge({ action }: { action: string }) {
  const cls = action === "buy" ? "buy" : action === "sell" ? "sell" : "hold";
  return <span className={`sig ${cls}`}>{ACTION_KO[action] ?? action}</span>;
}

export const STATUS_KO: Record<string, string> = {
  executed: "실행됨", blocked: "차단됨", pending: "실행 대기", no_action: "조치 없음", expired: "폐기", error: "판단 실패",
  filled: "체결", partial: "부분 체결", rejected: "거부", done: "완료", running: "진행 중", failed: "실패",
  budget_stopped: "예산 초과로 중단",
};
export const KIND_KO: Record<string, string> = { premarket: "장전 회의", intraday: "장중 점검", manual: "수동 회의" };
export const EXIT_KO: Record<string, string> = {
  stop: "손절", target: "목표가", decision: "회의 결정", eod_flatten: "장 마감 청산", kill_switch: "킬 스위치 청산",
};
export const DESK_KO: Record<string, string> = { swing: "스윙", scalp: "스캘핑" };
export const AGENT_KO: Record<string, string> = {
  chart: "차트 분석가", technical: "기술적 분석가", financials: "재무제표 분석가", fundamental: "기본적 분석가",
  news: "뉴스 분석가", community: "커뮤니티 분석가", sentiment: "센티먼트 분석가", macro: "매크로 분석가",
  bull: "매수 논거 담당", bear: "매도 논거 담당", trader: "스윙 트레이더", scalper: "스캘퍼", risk: "리스크 관리자",
  head: "수석 트레이더", performance: "성과 분석가",
};
export const VERDICT_KO: Record<string, string> = { approve: "승인", reduce: "축소", reject: "거부" };
export const REGIME_KO: Record<string, string> = { risk_on: "위험선호", neutral: "중립", risk_off: "위험회피" };

export function Sym({ symbol, name }: { symbol: string; name?: string }) {
  return (
    <>
      <Link className="sym" to={`/symbols/${symbol}`} onClick={(e) => e.stopPropagation()}>
        {symbol}
      </Link>
      {name && name !== symbol && <span className="muted">{name}</span>}
    </>
  );
}

export function Loading({ error }: { error?: string | null }) {
  return <div className="empty">{error ? `불러오지 못했습니다: ${error}` : "불러오는 중…"}</div>;
}

/** 한도 대비 사용량 막대. limit 은 0~1 비율, max 는 트랙의 오른쪽 끝 값. */
export function LimitBar({ name, value, limit, max, title }: { name: string; value: number; limit?: number; max: number; title?: string }) {
  const over = limit != null && value > limit + 1e-9;
  return (
    <div className="bar-row" title={title ?? `${name}: ${pct(value, 1)}${limit != null ? ` (한도 ${pct(limit, 0)})` : ""}`}>
      <span className="name">{name}</span>
      <div className="track">
        <div className={`fill ${over ? "over" : ""}`} style={{ width: `${Math.min(100, (value / max) * 100)}%` }} />
        {limit != null && <div className="limit" style={{ left: `${Math.min(100, (limit / max) * 100)}%` }} />}
      </div>
      <span className="val num">{pct(value, 1)}</span>
    </div>
  );
}
