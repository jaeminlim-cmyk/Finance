import { useEffect, useState } from "react";
import { NavLink, useNavigate, useParams } from "react-router-dom";
import { useApi, type Meeting, type MeetingDetail } from "../api";
import { MacroNote, Trail } from "../trail";
import { ActionBadge, KIND_KO, Loading, STATUS_KO, money, num, when } from "../ui";

function Detail({ id }: { id: string }) {
  const { data, error } = useApi<MeetingDetail>(`/meetings/${id}`);
  const [sel, setSel] = useState<string | null>(null);
  useEffect(() => setSel(null), [id]);
  if (!data) return <div className="card"><Loading error={error} /></div>;
  const m = data.meeting;
  const current = data.symbols.find((s) => s.symbol === sel) ?? data.symbols.find((s) => s.decision) ?? data.symbols[0];
  const plan = data.scalper?.payload.candidates ?? [];
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14, minWidth: 0 }}>
      <div className="card">
        <h2>
          {KIND_KO[m.kind] ?? m.kind} <span className="muted num">{when(m.ts)} ET</span>
          <span className={`pill ${m.status === "running" ? "good" : m.status === "done" ? "" : "crit"}`}>
            {m.status === "running" ? `진행 중 — ${m.stage}` : STATUS_KO[m.status] ?? m.status}
          </span>
          {m.cost_usd > 0 && <span className="muted">LLM 비용 {money(m.cost_usd, true)}</span>}
        </h2>
        {m.summary && <p style={{ margin: "0 0 10px" }}>{m.summary}</p>}
        {m.error && <div className="note crit" style={{ marginBottom: 10 }}>{m.error}</div>}
        <MacroNote r={data.macro} />
        {data.scalper && (
          <div className="note" style={{ marginTop: 8 }}>
            <b>스캘퍼</b> · {data.scalper.summary ?? "후보 없음"}
            {plan.map((c) => <div key={c.symbol} className="small">{c.symbol} · {c.strategy === "orb" ? "시가 범위 돌파" : "VWAP 회귀"} — {c.reason}</div>)}
          </div>
        )}
      </div>
      {data.symbols.length > 0 && (
        <div className="card">
          <h2>종목별 논의 <span className="muted">합의 점수는 −1(전원 강력매도) ~ +1(전원 강력매수)</span></h2>
          <div className="chips" style={{ marginBottom: 16 }}>
            {data.symbols.map((s) => (
              <button key={s.symbol} className={`chip ${current?.symbol === s.symbol ? "on" : ""}`} onClick={() => setSel(s.symbol)} title={`${s.name} · ${s.outcome}`}>
                <b>{s.symbol}</b>
                {s.consensus != null && <span className="muted num">{s.consensus >= 0 ? "+" : ""}{num(s.consensus)}</span>}
                {s.decision ? <ActionBadge action={s.decision.action} /> : <span className="muted small">{s.outcome}</span>}
              </button>
            ))}
          </div>
          {current && (
            <>
              <h3 style={{ marginBottom: 12 }}>
                {current.symbol} {current.name} · {current.role === "holding" ? "보유 종목" : "신규 후보"} · {current.outcome}
              </h3>
              <Trail reports={current.reports} debate={current.debate} decision={current.decision} consensus={current.consensus} />
            </>
          )}
        </div>
      )}
    </div>
  );
}

export default function Meetings() {
  const { id } = useParams();
  const nav = useNavigate();
  const { data, error } = useApi<Meeting[]>("/meetings?limit=120");
  useEffect(() => {
    if (!id && data?.length) nav(`/meetings/${data[0].id}`, { replace: true });
  }, [id, data, nav]);
  return (
    <>
      <div className="page-head">
        <h1>회의실</h1>
        <p>직원들의 보고서, 토론, 리스크 검토, 최종 결정을 회의별로 봅니다</p>
      </div>
      {!data ? <Loading error={error} /> : data.length === 0 ? (
        <div className="card empty">아직 열린 회의가 없습니다. 위의 「회의 열기」를 누르거나 개요에서 시뮬레이션을 재생하세요.</div>
      ) : (
        <div className="split">
          <div className="card flush">
            <div className="list">
              {data.map((m) => (
                <NavLink key={m.id} to={`/meetings/${m.id}`}>
                  <div className="t">
                    <span>{KIND_KO[m.kind] ?? m.kind}</span>
                    <span className="muted num small">{when(m.ts)}</span>
                  </div>
                  <div className="s">
                    {m.status === "running" ? `진행 중 — ${m.stage}` : m.status !== "done" ? STATUS_KO[m.status] : `매수 ${m.buys ?? 0} · 매도 ${m.sells ?? 0}`}
                  </div>
                </NavLink>
              ))}
            </div>
          </div>
          {id && <Detail id={id} />}
        </div>
      )}
    </>
  );
}
