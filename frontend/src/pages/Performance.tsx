import { Link } from "react-router-dom";
import { useApi, type Lesson } from "../api";
import { LineChart } from "../charts";
import { Delta, DESK_KO, Loading, Stat, money, num, pct, signed } from "../ui";
import Backtests from "./Backtests";

interface Metrics {
  days: number;
  trade_count: number;
  llm_cost_usd: number;
  total_return?: number;
  benchmark_return?: number;
  excess_return?: number;
  sharpe?: number | null;
  volatility?: number | null;
  max_drawdown?: number;
  net_profit_after_llm?: number;
  return_after_llm?: number;
  win_rate?: number;
  avg_win?: number;
  avg_loss?: number;
  payoff_ratio?: number | null;
  realized_pnl?: number;
  trading_costs?: number;
  by_desk?: Record<string, { trades: number; pnl: number; win_rate: number }>;
}

interface Resp {
  metrics: Metrics;
  monthly: { month: string; return: number; benchmark: number | null }[];
  drawdown: { day: string; drawdown: number }[];
  lessons: Lesson[];
  llm_daily: { day: string; calls: number; cost: number; cached: number }[];
  llm_by_model: { model: string; calls: number; input_tokens: number; output_tokens: number; cost: number }[];
}

function Monthly({ rows }: { rows: Resp["monthly"] }) {
  const max = Math.max(0.01, ...rows.map((r) => Math.abs(r.return)));
  return (
    <div className="months" role="img" aria-label="월별 수익률 막대 그래프">
      {rows.map((r) => (
        <div className="month" key={r.month} title={`${r.month}: 펀드 ${signed(r.return)}${r.benchmark != null ? ` · 벤치마크 ${signed(r.benchmark)}` : ""}`}>
          <div className="pos">{r.return > 0 && <i style={{ height: `${(r.return / max) * 100}%` }} />}</div>
          <div className="neg">{r.return < 0 && <i style={{ height: `${(-r.return / max) * 100}%` }} />}</div>
          <span>{r.month.slice(2).replace("-", ".")}</span>
        </div>
      ))}
    </div>
  );
}

export default function Performance() {
  const { data, error } = useApi<Resp>("/performance");
  if (!data) return <Loading error={error} />;
  const m = data.metrics;
  if (!m.days) return (<><div className="page-head"><h1>성과 분석</h1></div><div className="card empty">아직 성과 기록이 없습니다</div><Backtests /></>);
  const cum: { time: string; value: number }[] = [];
  let acc = 0;
  for (const d of data.llm_daily) cum.push({ time: d.day, value: (acc += d.cost) });
  return (
    <>
      <div className="page-head">
        <h1>성과 분석</h1>
        <p>{m.days}거래일 · 거래 {m.trade_count}건 · 수익률은 수수료·슬리피지 반영 후</p>
      </div>
      <div className="grid cols-4">
        <Stat label="누적 수익률" value={<Delta value={m.total_return} />} sub={<>벤치마크 <Delta value={m.benchmark_return} /></>} />
        <Stat label="초과수익" value={<Delta value={m.excess_return} />} sub="벤치마크 대비" />
        <Stat label="LLM 비용 차감 후 순수익" value={<Delta value={m.net_profit_after_llm} money />} sub={<>수익률 <Delta value={m.return_after_llm} /> · LLM {money(m.llm_cost_usd, true)}</>} />
        <Stat label="최대 낙폭" value={<Delta value={m.max_drawdown} />} sub={`연 변동성 ${pct(m.volatility, 1)}`} />
        <Stat label="샤프 지수" value={num(m.sharpe)} sub="연율화, 무위험 수익률 0 가정" />
        <Stat label="승률" value={pct(m.win_rate, 1)} sub={`${m.trade_count}건 중`} />
        <Stat label="손익비" value={num(m.payoff_ratio)} sub={`평균 이익 ${money(m.avg_win)} / 평균 손실 ${money(m.avg_loss)}`} />
        <Stat label="거래 비용" value={money(m.trading_costs, true)} sub="수수료 합계 (슬리피지는 체결가에 반영)" />
      </div>
      <div className="grid cols-2">
        <div className="card">
          <h2>월별 수익률</h2>
          <Monthly rows={data.monthly} />
          <div className="table-wrap" style={{ marginTop: 12 }}>
            <table>
              <thead><tr><th>월</th><th className="r">펀드</th><th className="r">벤치마크</th></tr></thead>
              <tbody>
                {data.monthly.map((r) => (
                  <tr key={r.month}><td className="num">{r.month}</td><td className="r"><Delta value={r.return} /></td><td className="r"><Delta value={r.benchmark} /></td></tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
        <div className="card">
          <h2>낙폭 <span className="muted">고점 대비</span></h2>
          <LineChart height={190} series={[{ name: "낙폭", color: "--series-1", area: true, format: (v) => `${(v * 100).toFixed(2)}%`,
                                              data: data.drawdown.map((d) => ({ time: d.day, value: d.drawdown })) }]} />
          <h2 style={{ margin: "18px 0 8px" }}>데스크별</h2>
          <table>
            <thead><tr><th>데스크</th><th className="r">거래</th><th className="r">승률</th><th className="r">실현 손익</th></tr></thead>
            <tbody>
              {Object.entries(m.by_desk ?? {}).map(([k, v]) => (
                <tr key={k}><td>{DESK_KO[k]}</td><td className="r num">{v.trades}</td><td className="r num">{pct(v.win_rate, 0)}</td><td className="r"><Delta value={v.pnl} money /></td></tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      <div className="grid cols-2">
        <div className="card">
          <h2>LLM 누적 비용</h2>
          {m.llm_cost_usd > 0
            ? <LineChart height={190} series={[{ name: "누적 비용", color: "--series-1", data: cum, format: (v) => money(v, true) }]} />
            : <div className="empty">가짜 LLM 응답 모드라 비용이 발생하지 않았습니다</div>}
          <table style={{ marginTop: 10 }}>
            <thead><tr><th>모델</th><th className="r">호출</th><th className="r">입력 토큰</th><th className="r">출력 토큰</th><th className="r">비용</th></tr></thead>
            <tbody>
              {data.llm_by_model.map((r) => (
                <tr key={r.model}>
                  <td>{r.model === "cache" ? "캐시 재사용" : r.model}</td><td className="r num">{r.calls.toLocaleString()}</td>
                  <td className="r num">{(r.input_tokens ?? 0).toLocaleString()}</td><td className="r num">{(r.output_tokens ?? 0).toLocaleString()}</td>
                  <td className="r num">{money(r.cost, true)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="card flush">
          <h2>복기 리포트 <span className="muted">성과 분석가가 청산 거래마다 남긴 교훈. 다음 회의에 반영됩니다</span></h2>
          <div className="table-wrap" style={{ maxHeight: 420, overflowY: "auto" }}>
            <table>
              <thead><tr><th>종목</th><th className="r">결과</th><th>교훈</th></tr></thead>
              <tbody>
                {data.lessons.map((l) => (
                  <tr key={l.id}>
                    <td><Link className="sym" to={`/trades/${l.trade_id}`}>{l.symbol}</Link></td>
                    <td className="r"><Delta value={l.pnl_pct} digits={1} /></td>
                    <td className="wrap ink2">{l.lesson}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {data.lessons.length === 0 && <div className="empty">아직 복기한 거래가 없습니다</div>}
          </div>
        </div>
      </div>
      <Backtests />
    </>
  );
}
