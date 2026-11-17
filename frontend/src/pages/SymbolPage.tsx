import { Link, useParams } from "react-router-dom";
import { useApi, type Decision, type Order, type Position, type Report, type Trade } from "../api";
import { CandleChart, type Candle, type Mark } from "../charts";
import { ActionBadge, AGENT_KO, Delta, DESK_KO, EXIT_KO, Loading, STATUS_KO, SignalBadge, money, num, pct, when } from "../ui";

interface Resp {
  symbol: string;
  name: string;
  sector: string;
  price: number;
  bars: Candle[];
  positions: Position[];
  orders: Order[];
  trades: Trade[];
  decisions: Decision[];
  reports: Report[];
}

const etDate = new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York" });

export default function SymbolPage() {
  const { symbol } = useParams();
  const { data, error } = useApi<Resp>(`/symbols/${symbol}?days=260`);
  if (!data) return <Loading error={error} />;
  // 같은 날 같은 방향의 체결은 하나의 표시로 합친다
  const merged = new Map<string, Mark>();
  for (const o of data.orders) {
    const time = etDate.format(new Date(o.ts));
    const k = `${time}:${o.side}`;
    const prev = merged.get(k);
    merged.set(k, { time, side: o.side as "buy" | "sell", text: `${o.side === "buy" ? "매수" : "매도"}${prev ? " ×2+" : ""}` });
  }
  const pos = data.positions.find((p) => p.desk === "swing") ?? data.positions[0];
  const lines = pos ? [
    ...(pos.stop_price ? [{ price: pos.stop_price, title: "손절" }] : []),
    ...(pos.target_price ? [{ price: pos.target_price, title: "목표" }] : []),
    { price: pos.avg_price, title: "평단" },
  ] : [];
  const last = data.bars[data.bars.length - 1], prev = data.bars[data.bars.length - 2];
  return (
    <>
      <div className="page-head">
        <h1>{data.symbol} <span className="muted">{data.name}</span></h1>
        <p>{data.sector} · 현재가 <b className="num">{money(data.price, true)}</b> {prev && <Delta value={last.close / prev.close - 1} />}</p>
      </div>
      <div className="card">
        <h2>일봉 <span className="muted">화살표는 이 펀드의 매수·매도 체결 · 드래그로 이동, 휠로 확대</span></h2>
        <CandleChart bars={data.bars} marks={[...merged.values()]} lines={lines} />
      </div>
      {data.positions.map((p) => (
        <div className="card" key={p.id}>
          <h2>보유 중 <span className="muted">{DESK_KO[p.desk]}</span></h2>
          <dl className="kv">
            <dt>수량·평단</dt><dd className="num">{p.qty}주 · {money(p.avg_price, true)}</dd>
            <dt>평가손익</dt><dd><Delta value={p.unrealized_pnl} money /> (<Delta value={p.unrealized_pct} />)</dd>
            <dt>손절·목표</dt><dd className="num">{money(p.stop_price, true)} · {money(p.target_price, true)}</dd>
          </dl>
        </div>
      ))}
      <div className="grid cols-2">
        <div className="card flush">
          <h2>결정 이력</h2>
          <div className="table-wrap" style={{ maxHeight: 420, overflowY: "auto" }}>
            <table>
              <thead><tr><th>회의</th><th>결정</th><th className="r">합의</th><th>상태</th><th>근거</th></tr></thead>
              <tbody>
                {data.decisions.map((d) => (
                  <tr key={d.id}>
                    <td className="num"><Link to={`/meetings/${d.meeting_id}`}>{when(d.ts)}</Link></td>
                    <td><ActionBadge action={d.action} /></td>
                    <td className="r num">{d.consensus == null ? "–" : `${d.consensus >= 0 ? "+" : ""}${num(d.consensus)}`}</td>
                    <td title={d.status_detail ?? ""}>{STATUS_KO[d.status] ?? d.status}</td>
                    <td className="wrap ink2">{d.rationale}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {data.decisions.length === 0 && <div className="empty">이 종목에 대한 결정이 없습니다</div>}
          </div>
        </div>
        <div className="card flush">
          <h2>분석 이력</h2>
          <div className="table-wrap" style={{ maxHeight: 420, overflowY: "auto" }}>
            <table>
              <thead><tr><th>시각</th><th>직원</th><th>의견</th><th className="r">확신</th><th>요약</th></tr></thead>
              <tbody>
                {data.reports.map((r) => (
                  <tr key={r.id}>
                    <td className="num muted"><Link to={`/meetings/${r.meeting_id}`}>{when(r.ts)}</Link></td>
                    <td>{AGENT_KO[r.agent] ?? r.agent}</td>
                    <td><SignalBadge signal={r.signal} /></td>
                    <td className="r num">{pct(r.confidence, 0)}</td>
                    <td className="wrap ink2">{r.summary}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {data.reports.length === 0 && <div className="empty">이 종목에 대한 분석이 없습니다</div>}
          </div>
        </div>
      </div>
      {data.trades.length > 0 && (
        <div className="card flush">
          <h2>청산된 거래</h2>
          <div className="table-wrap">
            <table>
              <thead><tr><th>청산</th><th>데스크</th><th className="r">수량</th><th className="r">진입가</th><th className="r">청산가</th><th className="r">손익</th><th>사유</th></tr></thead>
              <tbody>
                {data.trades.map((t) => (
                  <tr key={t.id}>
                    <td className="num"><Link to={`/trades/${t.id}`}>{when(t.exit_ts)}</Link></td>
                    <td>{DESK_KO[t.desk]}</td>
                    <td className="r num">{t.qty}</td>
                    <td className="r num">{money(t.entry_price, true)}</td>
                    <td className="r num">{money(t.exit_price, true)}</td>
                    <td className="r"><Delta value={t.pnl} money /> (<Delta value={t.pnl_pct} />)</td>
                    <td>{EXIT_KO[t.exit_reason] ?? t.exit_reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </>
  );
}
