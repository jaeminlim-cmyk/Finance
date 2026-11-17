import { useState } from "react";
import { Link } from "react-router-dom";
import { post, refreshAll, useApi, type EquityPoint, type Order, type Overview, type Position } from "../api";
import { LineChart } from "../charts";
import { Delta, DESK_KO, KIND_KO, Loading, STATUS_KO, Stat, Sym, money, num, pct, when } from "../ui";

function Empty({ o }: { o: Overview }) {
  const [days, setDays] = useState(60);
  const [err, setErr] = useState<string | null>(null);
  const start = () =>
    post("/control/simulate", { days }).then(refreshAll).catch((e: Error) => setErr(e.message));
  return (
    <div className="card">
      <h2>아직 운용 기록이 없습니다</h2>
      <p className="ink2" style={{ marginTop: 0 }}>
        최근 거래일을 빠르게 재생해 기록을 채울 수 있습니다. 실제 운용과 같은 회의·주문·리스크 규칙이 하루씩 그대로 실행됩니다.
        {o.mode.llm !== "mock" && " 지금은 Claude 를 호출하는 모드라 재생하는 동안 실제 API 비용이 발생합니다."}
      </p>
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <div className="seg">
          {[20, 60, 120].map((d) => (
            <button key={d} className={d === days ? "on" : ""} onClick={() => setDays(d)}>{d}거래일</button>
          ))}
        </div>
        <button className="btn primary" disabled={!!o.status.job} onClick={start}>
          {o.status.job ? "재생 중…" : "시뮬레이션 재생"}
        </button>
        {err && <span className="up small">{err}</span>}
      </div>
    </div>
  );
}

export default function OverviewPage({ o }: { o: Overview | undefined }) {
  const { data: eq } = useApi<EquityPoint[]>("/equity");
  const { data: pos } = useApi<{ positions: Position[] }>("/positions");
  const { data: orders } = useApi<Order[]>("/orders?limit=8");
  if (!o) return <Loading />;
  const hasHistory = (eq?.length ?? 0) > 0;
  const excess = o.benchmark_return == null ? null : o.total_return - o.benchmark_return;
  return (
    <>
      <div className="page-head">
        <h1>개요</h1>
        <p>초기 자본 {money(o.initial_capital)} · 현물 롱 온리</p>
      </div>
      {o.mode.data === "mock" && (
        <div className="note">
          지금 보이는 가격·재무·뉴스는 모두 합성한 샘플 데이터이고, 직원들의 보고서도 규칙으로 만든 가짜 응답입니다.
          화면과 흐름을 확인하기 위한 것이므로 이 수익률에는 의미가 없습니다.
        </div>
      )}
      <div className="grid cols-4">
        <Stat hero label="총 자산" value={money(o.equity)} sub={<>누적 <Delta value={o.total_pnl} money /> (<Delta value={o.total_return} />)</>} />
        <Stat label="오늘 손익" value={<Delta value={o.day_pnl} money />} sub={<Delta value={o.day_pnl_pct} />} />
        <Stat label={`${o.benchmark} 대비 초과수익`} value={<Delta value={excess} />} sub={<>{o.benchmark} <Delta value={o.benchmark_return} /></>} />
        <Stat label="현금 비중" value={pct(o.cash_pct, 1)} sub={`${money(o.cash)} · 보유 ${o.positions_count}종목`} />
      </div>
      {!hasHistory && eq && <Empty o={o} />}
      {hasHistory && (
        <div className="card">
          <h2>자산 추이 <span className="muted">{o.benchmark} 는 같은 초기 자본으로 환산</span></h2>
          <LineChart
            series={[
              { name: "펀드", color: "--series-1", data: eq!.map((p) => ({ time: p.day, value: p.equity })) },
              { name: o.benchmark, color: "--series-ref", data: eq!.filter((p) => p.benchmark != null).map((p) => ({ time: p.day, value: p.benchmark! })) },
            ]}
          />
        </div>
      )}
      <div className="grid cols-4">
        <Stat label="샤프 지수" value={num(o.sharpe)} sub="일간 수익률 기준, 연율화" />
        <Stat label="최대 낙폭" value={<Delta value={o.max_drawdown} />} sub="고점 대비" />
        <Stat label="LLM 비용 (이번 달)" value={money(o.llm.spent, true)} sub={`예산 ${money(o.llm.budget)} · 누적 ${money(o.llm.total_cost, true)}`} />
        <Stat
          label="최근 회의"
          value={o.last_meeting ? <Link to={`/meetings/${o.last_meeting.id}`}>{KIND_KO[o.last_meeting.kind] ?? o.last_meeting.kind}</Link> : "–"}
          sub={o.last_meeting ? `${when(o.last_meeting.ts)} · ${o.last_meeting.status === "running" ? o.last_meeting.stage : STATUS_KO[o.last_meeting.status]}` : "아직 없음"}
        />
      </div>
      <div className="grid cols-2">
        <div className="card flush">
          <h2>보유 종목 <Link className="muted" to="/portfolio">전체 보기</Link></h2>
          <div className="table-wrap">
            <table>
              <thead><tr><th>종목</th><th className="r">비중</th><th className="r">평가손익</th></tr></thead>
              <tbody>
                {pos?.positions.slice(0, 8).map((p) => (
                  <tr key={p.id}>
                    <td><Sym symbol={p.symbol} name={p.name} /></td>
                    <td className="r num">{pct(p.weight, 1)}</td>
                    <td className="r"><Delta value={p.unrealized_pct} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
            {pos && pos.positions.length === 0 && <div className="empty">보유 종목이 없습니다</div>}
          </div>
        </div>
        <div className="card flush">
          <h2>최근 주문 <Link className="muted" to="/trades">전체 보기</Link></h2>
          <div className="table-wrap">
            <table>
              <thead><tr><th>시각</th><th>종목</th><th>구분</th><th className="r">수량</th><th>상태</th></tr></thead>
              <tbody>
                {orders?.map((x) => (
                  <tr key={x.id}>
                    <td className="num muted">{when(x.ts)}</td>
                    <td><Sym symbol={x.symbol} /></td>
                    <td>{DESK_KO[x.desk]} {x.side === "buy" ? "매수" : "매도"}</td>
                    <td className="r num">{x.filled_qty || x.qty}</td>
                    <td title={x.reason ?? ""}>{STATUS_KO[x.status] ?? x.status}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {orders && orders.length === 0 && <div className="empty">주문이 없습니다</div>}
          </div>
        </div>
      </div>
    </>
  );
}
