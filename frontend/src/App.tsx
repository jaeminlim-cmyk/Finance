import { useState } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import { post, refreshAll, useApi, type Overview } from "./api";
import Agents from "./pages/Agents";
import Meetings from "./pages/Meetings";
import OverviewPage from "./pages/Overview";
import Performance from "./pages/Performance";
import Portfolio from "./pages/Portfolio";
import Risk from "./pages/Risk";
import SymbolPage from "./pages/SymbolPage";
import Trades, { TradeDetail } from "./pages/Trades";
import { toggleTheme, useTheme } from "./theme";
import { when } from "./ui";

const NAV = [
  ["/", "개요"], ["/portfolio", "포트폴리오"], ["/trades", "거래 내역"], ["/meetings", "회의실"],
  ["/agents", "직원 현황"], ["/risk", "리스크"], ["/performance", "성과 분석"],
] as const;

const JOB_KO: Record<string, string> = { meeting: "회의 진행 중", simulate: "시뮬레이션 진행 중", backtest: "백테스트 진행 중" };

function TopBar({ o }: { o: Overview }) {
  const theme = useTheme();
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setMsg(null);
    try {
      await fn();
    } catch (e) {
      setMsg((e as Error).message);
    }
    setBusy(false);
    refreshAll();
  };
  const s = o.status;
  const kill = () => {
    const on = !s.kill_switch;
    if (on && !window.confirm("킬 스위치를 켭니다.\n신규 매수와 회의 결정에 따른 매도가 모두 중단되고, 스캘핑 포지션은 즉시 청산됩니다.\n손절·목표가 청산은 계속 동작합니다.")) return;
    act(() => post("/control/kill", { on }));
  };
  return (
    <>
      <div className="topbar">
        <span className="pill strong warn" title="실제 돈이 움직이는 주문은 나가지 않습니다">
          <i className="dot" /> 모의투자
        </span>
        <span className="pill">{o.market_open ? "정규장 개장 중" : "정규장 마감"}</span>
        <span className="pill" title="데이터 소스">{o.mode.data === "mock" ? "샘플 데이터" : "실시간 데이터"}</span>
        <span className="pill" title={`리서치 ${o.models.research} · 시니어 ${o.models.senior}`}>
          {o.mode.llm === "mock" ? "가짜 LLM 응답" : "Claude"}
        </span>
        {s.job && <span className="pill good"><i className="dot" /> {JOB_KO[s.job.kind] ?? s.job.kind}</span>}
        <span className="grow" />
        <span className="muted small num">{when(o.now)} ET</span>
        <button className="btn" disabled={busy || !!s.job} onClick={() => act(() => post("/control/meeting"))}>회의 열기</button>
        <button className="btn" onClick={toggleTheme} aria-label="테마 전환">{theme === "dark" ? "라이트" : "다크"}</button>
        <button className={`btn danger ${s.kill_switch ? "on" : ""}`} disabled={busy} onClick={kill}>
          {s.kill_switch ? "킬 스위치 해제" : "킬 스위치"}
        </button>
      </div>
      {s.kill_switch && <div className="banner crit">킬 스위치 작동 중 — 신규 진입이 모두 중단되었습니다. 손절·목표가 청산은 계속 동작합니다.</div>}
      {s.halted && (
        <div className="banner crit">
          낙폭 한도 도달로 전체 매매가 중단되었습니다 ({s.halt_reason}). 상황을 확인한 뒤{" "}
          <button className="btn" onClick={() => window.confirm("낙폭 중단을 해제하고 매매를 재개할까요?") && act(() => post("/control/resume"))}>재개</button>
        </div>
      )}
      {s.daily_stop && !s.halted && <div className="banner warn">일일 손실 한도 도달 — 오늘은 신규 진입이 중단됩니다.</div>}
      {o.llm.level !== "normal" && <div className="banner warn">LLM 예산의 {Math.round((o.llm.spent / o.llm.budget) * 100)}% 사용 — 회의 규모가 자동으로 축소됩니다.</div>}
      {(msg || s.job_error || s.scheduler_error) && <div className="banner crit">{msg || s.job_error || s.scheduler_error}</div>}
    </>
  );
}

export default function App() {
  const { data: o, error } = useApi<Overview>("/overview");
  return (
    <div className="shell">
      <nav className="nav">
        <div className="brand">
          <svg width="22" height="22" viewBox="0 0 32 32" aria-hidden>
            <rect width="32" height="32" rx="7" fill="var(--surface-2)" />
            <path d="M6 22l6-7 5 4 9-11" fill="none" stroke="var(--series-1)" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          AI 헤지펀드
        </div>
        {NAV.map(([to, label]) => (
          <NavLink key={to} to={to} end={to === "/"}>{label}</NavLink>
        ))}
        <div className="spacer" />
        <div className="foot">시각은 미국 동부시간(ET)<br />상승 ▲ 빨강 · 하락 ▼ 파랑</div>
      </nav>
      <div className="main">
        {o ? <TopBar o={o} /> : <div className="topbar"><span className="muted">{error ? `서버에 연결하지 못했습니다: ${error}` : "연결 중…"}</span></div>}
        <div className="content">
          <Routes>
            <Route path="/" element={<OverviewPage o={o} />} />
            <Route path="/portfolio" element={<Portfolio />} />
            <Route path="/trades" element={<Trades />} />
            <Route path="/trades/:id" element={<TradeDetail />} />
            <Route path="/meetings" element={<Meetings />} />
            <Route path="/meetings/:id" element={<Meetings />} />
            <Route path="/agents" element={<Agents />} />
            <Route path="/risk" element={<Risk />} />
            <Route path="/performance" element={<Performance />} />
            <Route path="/symbols/:symbol" element={<SymbolPage />} />
          </Routes>
        </div>
      </div>
    </div>
  );
}
