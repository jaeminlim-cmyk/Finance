import { useState } from "react";
import { Link } from "react-router-dom";
import { useApi, type Report, type Staff } from "../api";
import { Loading, SignalBadge, Sym, money, pct, signed, when } from "../ui";

const DIVISIONS = ["리서치 본부", "토론 본부", "트레이딩 본부", "지원 조직"];
const TIER: Record<string, string> = { research: "빠른 모델", senior: "고성능 모델", code: "코드 (LLM 아님)" };

interface AgentDetail {
  id: string;
  title: string;
  division: string;
  duty: string;
  system_prompt: string;
  recent: Report[];
}

function Modal({ id, onClose }: { id: string; onClose: () => void }) {
  const { data } = useApi<AgentDetail>(`/agents/${id}`);
  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal>
        {!data ? <Loading /> : (
          <>
            <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "baseline" }}>
              <h1>{data.title} <span className="muted small">{data.division}</span></h1>
              <button className="btn" onClick={onClose}>닫기</button>
            </div>
            <div>
              <h3 style={{ marginBottom: 6 }}>역할 지시문 (시스템 프롬프트)</h3>
              <pre className="prompt">{data.system_prompt}</pre>
            </div>
            {data.recent.length > 0 && (
              <div>
                <h3 style={{ marginBottom: 6 }}>최근 보고</h3>
                <div className="table-wrap">
                  <table>
                    <thead><tr><th>시각</th><th>종목</th><th>의견</th><th className="r">확신</th><th className="r">5일 뒤</th><th>적중</th><th>요약</th></tr></thead>
                    <tbody>
                      {data.recent.map((r) => (
                        <tr key={r.id}>
                          <td className="num muted"><Link to={`/meetings/${r.meeting_id}`} onClick={onClose}>{when(r.ts)}</Link></td>
                          <td><Sym symbol={r.symbol} /></td>
                          <td><SignalBadge signal={r.signal} /></td>
                          <td className="r num">{pct(r.confidence, 0)}</td>
                          <td className="r num">{signed(r.fwd_return, 1)}</td>
                          <td>{r.hit == null ? <span className="muted">–</span> : r.hit ? "적중" : "빗나감"}</td>
                          <td className="wrap ink2">{r.summary}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

export default function Agents() {
  const { data, error } = useApi<Staff[]>("/agents");
  const [open, setOpen] = useState<string | null>(null);
  if (!data) return <Loading error={error} />;
  const scored = data.filter((a) => a.hit_rate != null && a.scored >= 5).sort((a, b) => b.hit_rate! - a.hit_rate!);
  return (
    <>
      <div className="page-head">
        <h1>직원 현황</h1>
        <p>적중률은 매수·매도 의견을 낸 뒤 5거래일 수익률의 방향이 맞았는지로 채점합니다</p>
      </div>
      {scored.length > 0 && (
        <div className="card">
          <h2>신호 적중률 <span className="muted">세로선은 50% (동전 던지기)</span></h2>
          <div className="bars">
            {scored.map((a) => (
              <div className="bar-row" key={a.id} title={`${a.title}: ${a.scored}건 채점, 적중률 ${pct(a.hit_rate, 1)}`}>
                <span className="name">{a.title}</span>
                <div className="track">
                  <div className="fill" style={{ width: `${a.hit_rate! * 100}%` }} />
                  <div className="limit" style={{ left: "50%" }} />
                </div>
                <span className="val num">{pct(a.hit_rate, 0)}</span>
              </div>
            ))}
          </div>
        </div>
      )}
      {DIVISIONS.map((div) => (
        <div key={div}>
          <h2 style={{ marginBottom: 10 }}>{div}</h2>
          <div className="staff">
            {data.filter((a) => a.division === div).map((a) => {
              const body = (
                <>
                  <div className="t">
                    <span>{a.title}</span>
                    <span className="pill">{TIER[a.tier] ?? a.tier}</span>
                  </div>
                  <div className="d">{a.duty}</div>
                  {a.tier !== "code" && (
                    <div className="m">
                      {a.reports > 0 && <span><b className="num">{a.reports}</b>보고</span>}
                      {a.hit_rate != null && <span><b className="num">{pct(a.hit_rate, 0)}</b>적중률 ({a.scored}건)</span>}
                      <span><b className="num">{a.llm_calls ?? 0}</b>호출</span>
                      <span><b className="num">{money(a.llm_cost_usd ?? 0, true)}</b>비용</span>
                    </div>
                  )}
                  {a.latest && (
                    <div className="last">
                      {a.latest.signal && <SignalBadge signal={a.latest.signal} />} {a.latest.symbol && !a.latest.symbol.startsWith("_") ? `${a.latest.symbol} · ` : ""}
                      {a.latest.summary}
                    </div>
                  )}
                </>
              );
              return a.tier === "code"
                ? <div className="member" key={a.id}>{body}</div>
                : <button className="member" key={a.id} onClick={() => setOpen(a.id)}>{body}</button>;
            })}
          </div>
        </div>
      ))}
      {open && <Modal id={open} onClose={() => setOpen(null)} />}
    </>
  );
}
