import { useState } from "react";
import { post, refreshAll, useApi, type Overview } from "../api";
import { Delta, money, num, pct } from "../ui";

interface Backtest {
  id: string;
  start: string;
  end: string;
  data_mode: string;
  llm_mode: string;
  seconds: number;
  metrics: {
    total_return?: number;
    benchmark_return?: number;
    sharpe?: number | null;
    max_drawdown?: number;
    trade_count: number;
    win_rate?: number;
    llm_cost_usd: number;
  };
}

/** 백테스트 실행과 결과 목록. 운용 기록과 분리된 별도 DB 에서 돌아간다. */
export default function Backtests() {
  const { data } = useApi<Backtest[]>("/backtests");
  const { data: o } = useApi<Overview>("/overview");
  const [start, setStart] = useState("2024-01-02");
  const [end, setEnd] = useState("2024-03-28");
  const [cap, setCap] = useState("5");
  const [err, setErr] = useState<string | null>(null);
  const real = o != null && o.mode.llm !== "mock";
  const run = () => {
    const warning = "Claude 를 실제로 호출하는 백테스트입니다. 비용 상한 " + cap + "달러까지 API 비용이 발생할 수 있습니다. 진행할까요?";
    if (real && !window.confirm(warning)) return;
    setErr(null);
    post("/backtests", { start, end, max_cost_usd: real ? Number(cap) : null })
      .then(refreshAll)
      .catch((e: Error) => setErr(e.message));
  };
  return (
    <div className="card">
      <h2>
        백테스트 <span className="muted">과거 기간에 같은 회의·주문·리스크 규칙을 재생합니다. 운용 기록과 분리된 별도 DB 를 씁니다</span>
      </h2>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center", marginBottom: 12 }}>
        <label className="small muted">시작 <input type="date" value={start} onChange={(e) => setStart(e.target.value)} /></label>
        <label className="small muted">종료 <input type="date" value={end} onChange={(e) => setEnd(e.target.value)} /></label>
        {real && (
          <label className="small muted">
            LLM 비용 상한(달러) <input type="number" min="1" style={{ width: 64 }} value={cap} onChange={(e) => setCap(e.target.value)} />
          </label>
        )}
        <button className="btn primary" disabled={!!o?.status.job} onClick={run}>
          {o?.status.job?.kind === "backtest" ? "실행 중…" : "백테스트 실행"}
        </button>
        {err && <span className="up small">{err}</span>}
      </div>
      {real && (
        <div className="note warn" style={{ marginBottom: 12 }}>
          모델이 학습 과정에서 과거 사건을 이미 알고 있을 수 있어, 학습 시점 이전 기간의 백테스트 성과는 낙관적으로 나올 수 있습니다.
        </div>
      )}
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>기간</th><th>데이터 · LLM</th><th className="r">수익률</th><th className="r">벤치마크</th><th className="r">샤프</th>
              <th className="r">최대 낙폭</th><th className="r">거래</th><th className="r">승률</th><th className="r">LLM 비용</th>
            </tr>
          </thead>
          <tbody>
            {data?.map((b) => (
              <tr key={b.id}>
                <td className="num">{b.start} ~ {b.end}</td>
                <td className="ink2">{b.data_mode === "mock" ? "샘플" : "실데이터"} · {b.llm_mode === "mock" ? "가짜 응답" : "Claude"}</td>
                <td className="r"><Delta value={b.metrics.total_return} /></td>
                <td className="r"><Delta value={b.metrics.benchmark_return} /></td>
                <td className="r num">{num(b.metrics.sharpe)}</td>
                <td className="r"><Delta value={b.metrics.max_drawdown} /></td>
                <td className="r num">{b.metrics.trade_count}</td>
                <td className="r num">{pct(b.metrics.win_rate, 0)}</td>
                <td className="r num">{money(b.metrics.llm_cost_usd, true)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {data?.length === 0 && <div className="empty">아직 실행한 백테스트가 없습니다</div>}
      </div>
    </div>
  );
}
