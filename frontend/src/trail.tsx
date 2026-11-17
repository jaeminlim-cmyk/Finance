import type { DebateTurn, Decision, Report } from "./api";
import { AGENT_KO, ActionBadge, REGIME_KO, STATUS_KO, SignalBadge, VERDICT_KO, money, num, pct } from "./ui";

export function ReportCard({ r }: { r: Report }) {
  const p = r.payload;
  return (
    <div className="report">
      <div className="who">
        <span>{AGENT_KO[r.agent] ?? r.agent}</span>
        <SignalBadge signal={r.signal} />
      </div>
      <p>{r.summary}</p>
      {!!p.evidence?.length && (
        <ul>
          {p.evidence.map((e, i) => (
            <li key={i}>{e.point} <span className="src">{e.source}</span></li>
          ))}
        </ul>
      )}
      {!!p.risks?.length && <ul>{p.risks.map((x, i) => <li key={i}>위험: {x}</li>)}</ul>}
      {!!p.data_gaps?.length && <p className="src">데이터 없음: {p.data_gaps.join(", ")}</p>}
      {r.confidence != null && (
        <div className="conf" title={`확신도 ${pct(r.confidence, 0)}`}><i style={{ width: `${r.confidence * 100}%` }} /></div>
      )}
      {r.fwd_return != null && (
        <p className="src">
          5거래일 뒤 실제 수익률 {(r.fwd_return * 100).toFixed(1)}%
          {r.hit != null && (r.hit ? " · 적중" : " · 빗나감")}
        </p>
      )}
    </div>
  );
}

export function MacroNote({ r }: { r: Report | null }) {
  if (!r) return null;
  const p = r.payload;
  return (
    <div className="note">
      <b>매크로 분석가</b> <span className="pill">{REGIME_KO[p.regime ?? ""] ?? "판정 없음"}</span> {r.summary}
      {!!p.favored_sectors?.length && <> · 유리 {p.favored_sectors.join(", ")}</>}
      {!!p.avoided_sectors?.length && <> · 회피 {p.avoided_sectors.join(", ")}</>}
    </div>
  );
}

function Bubble({ t }: { t: DebateTurn }) {
  const p = t.payload;
  return (
    <div className={`bubble ${t.side}`}>
      <div className="who">
        <span>{t.side === "bull" ? "매수 논거 담당" : "매도 논거 담당"} · {t.round}라운드</span>
        <span>확신 {pct(p.conviction, 0)}</span>
      </div>
      <div className="thesis">{p.thesis}</div>
      {!!p.key_points.length && <ul>{p.key_points.map((x, i) => <li key={i}>{x}</li>)}</ul>}
      {!!p.rebuttals.length && (<><h4>반박</h4><ul>{p.rebuttals.map((x, i) => <li key={i}>{x}</li>)}</ul></>)}
      {!!p.concessions.length && (<><h4>인정하는 부분</h4><ul>{p.concessions.map((x, i) => <li key={i}>{x}</li>)}</ul></>)}
    </div>
  );
}

/** 한 종목이 리서치에서 최종 결정까지 거친 전체 과정. */
export function Trail({ reports, debate, decision, consensus }: { reports: Report[]; debate: DebateTurn[]; decision: Decision | null; consensus?: number | null }) {
  const steps: { title: string; body: JSX.Element }[] = [];
  steps.push({
    title: `리서치 본부 보고${consensus != null ? ` — 합의 점수 ${consensus >= 0 ? "+" : ""}${num(consensus)}` : ""}`,
    body: reports.length ? <div className="reports">{reports.map((r) => <ReportCard key={r.id} r={r} />)}</div> : <div className="muted">보고서 없음</div>,
  });
  if (debate.length) steps.push({ title: "매수 대 매도 토론", body: <div className="debate">{debate.map((t) => <Bubble key={t.id} t={t} />)}</div> });
  if (decision?.proposal) {
    const p = decision.proposal;
    steps.push({
      title: "스윙 트레이더 매매안",
      body: (
        <div className="plain">
          <ActionBadge action={p.action} /> <span className="ink2">크기 배수 {num(p.size_multiplier)} · 예상 보유 {p.horizon_days}일
          {p.stop_price != null && ` · 손절 ${money(p.stop_price, true)}`}{p.target_price != null && ` · 목표 ${money(p.target_price, true)}`}</span>
          <p className="ink2" style={{ margin: "6px 0 0" }}>{p.rationale}</p>
        </div>
      ),
    });
  }
  if (decision?.risk_review) {
    const r = decision.risk_review;
    steps.push({
      title: "리스크 관리자 검토",
      body: (
        <div className="plain">
          <span className={`pill strong ${r.verdict === "reject" ? "crit" : r.verdict === "reduce" ? "warn" : "good"}`}>{VERDICT_KO[r.verdict] ?? r.verdict}</span>{" "}
          <span className="ink2">크기 배수 {num(r.size_multiplier)}</span>
          <p className="ink2" style={{ margin: "6px 0 0" }}>{r.rationale}</p>
          {!!r.concerns.length && <ul>{r.concerns.map((x, i) => <li key={i}>{x}</li>)}</ul>}
        </div>
      ),
    });
  }
  if (decision) {
    const blocked = decision.status === "blocked" || decision.status === "error";
    steps.push({
      title: "수석 트레이더 최종 결정",
      body: (
        <div className="verdict">
          <div className="row">
            <ActionBadge action={decision.action} />
            {decision.conviction != null && <span className="ink2">확신 {pct(decision.conviction, 0)}</span>}
            <span className={`pill ${blocked ? "crit" : ""}`}>{STATUS_KO[decision.status] ?? decision.status}</span>
            {decision.qty != null && decision.qty > 0 && <span className="num">{decision.qty}주</span>}
            {decision.stop_price != null && <span className="ink2 num">손절 {money(decision.stop_price, true)}</span>}
            {decision.target_price != null && <span className="ink2 num">목표 {money(decision.target_price, true)}</span>}
          </div>
          {decision.rationale && <p>{decision.rationale}</p>}
          {!!decision.decision?.final?.key_factors?.length && (
            <div className="plain"><ul>{decision.decision.final.key_factors.map((x, i) => <li key={i}>{x}</li>)}</ul></div>
          )}
          {decision.dissent && <p className="ink2"><b>소수 의견</b> — {decision.dissent}</p>}
          {decision.status_detail && <div className={`note ${blocked ? "crit" : ""}`} style={{ marginTop: 10 }}>하드 리스크 규칙·실행 결과: {decision.status_detail}</div>}
        </div>
      ),
    });
  }
  return (
    <div className="timeline">
      {steps.map((s, i) => (
        <div className="step" key={s.title}>
          <div className="rail"><div className="n">{i + 1}</div></div>
          <div><h3>{s.title}</h3>{s.body}</div>
        </div>
      ))}
    </div>
  );
}
