import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useApi, type DebateTurn, type Decision, type Lesson, type Meeting, type Order, type Report, type Trade } from "../api";
import { MacroNote, Trail } from "../trail";
import { Delta, DESK_KO, EXIT_KO, KIND_KO, Loading, STATUS_KO, Sym, money, when } from "../ui";

export default function Trades() {
  const [tab, setTab] = useState<"trades" | "orders">("trades");
  const [desk, setDesk] = useState<"all" | "swing" | "scalp">("all");
  const { data: trades, error } = useApi<Trade[]>("/trades");
  const { data: orders } = useApi<Order[]>("/orders?limit=400");
  const nav = useNavigate();
  const rows = trades?.filter((t) => desk === "all" || t.desk === desk);
  const orows = orders?.filter((t) => desk === "all" || t.desk === desk);
  return (
    <>
      <div className="page-head">
        <h1>거래 내역</h1>
        <p>청산된 거래를 누르면 그 결정에 이른 전체 과정을 볼 수 있습니다</p>
      </div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <div className="seg">
          <button className={tab === "trades" ? "on" : ""} onClick={() => setTab("trades")}>청산된 거래</button>
          <button className={tab === "orders" ? "on" : ""} onClick={() => setTab("orders")}>모든 주문</button>
        </div>
        <div className="seg">
          {(["all", "swing", "scalp"] as const).map((d) => (
            <button key={d} className={desk === d ? "on" : ""} onClick={() => setDesk(d)}>{d === "all" ? "전체" : DESK_KO[d]}</button>
          ))}
        </div>
      </div>
      <div className="card flush">
        <div className="table-wrap">
          {tab === "trades" ? (
            <table>
              <thead>
                <tr><th>청산 시각</th><th>종목</th><th>데스크</th><th className="r">수량</th><th className="r">진입가</th><th className="r">청산가</th>
                  <th className="r">손익</th><th className="r">수익률</th><th>청산 사유</th><th>진입 시각</th></tr>
              </thead>
              <tbody>
                {rows?.map((t) => (
                  <tr key={t.id} className="link" onClick={() => nav(`/trades/${t.id}`)}>
                    <td className="num muted">{when(t.exit_ts)}</td>
                    <td><Sym symbol={t.symbol} name={t.name} /></td>
                    <td>{DESK_KO[t.desk]}</td>
                    <td className="r num">{t.qty}</td>
                    <td className="r num">{money(t.entry_price, true)}</td>
                    <td className="r num">{money(t.exit_price, true)}</td>
                    <td className="r"><Delta value={t.pnl} money /></td>
                    <td className="r"><Delta value={t.pnl_pct} /></td>
                    <td>{EXIT_KO[t.exit_reason] ?? t.exit_reason}</td>
                    <td className="num muted">{when(t.entry_ts)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <table>
              <thead>
                <tr><th>시각</th><th>종목</th><th>구분</th><th className="r">주문 수량</th><th className="r">체결 수량</th><th className="r">체결가</th>
                  <th className="r">수수료</th><th>상태</th><th>사유·메모</th></tr>
              </thead>
              <tbody>
                {orows?.map((x) => (
                  <tr key={x.id}>
                    <td className="num muted">{when(x.ts)}</td>
                    <td><Sym symbol={x.symbol} name={x.name} /></td>
                    <td>{DESK_KO[x.desk]} {x.side === "buy" ? "매수" : "매도"}</td>
                    <td className="r num">{x.qty || "–"}</td>
                    <td className="r num">{x.filled_qty}</td>
                    <td className="r num">{money(x.fill_price, true)}</td>
                    <td className="r num">{money(x.commission, true)}</td>
                    <td>{STATUS_KO[x.status] ?? x.status}</td>
                    <td className="wrap ink2">{EXIT_KO[x.reason ?? ""] ?? x.reason ?? x.note ?? ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {!trades && <Loading error={error} />}
          {tab === "trades" && rows?.length === 0 && <div className="empty">청산된 거래가 없습니다</div>}
          {tab === "orders" && orows?.length === 0 && <div className="empty">주문이 없습니다</div>}
        </div>
      </div>
    </>
  );
}

interface Detail {
  trade: Trade;
  decision: Decision | null;
  trail: { meeting: Meeting; macro: Report | null; reports: Report[]; debate: DebateTurn[] } | null;
  orders: Order[];
  lesson: Lesson | null;
}

export function TradeDetail() {
  const { id } = useParams();
  const { data, error } = useApi<Detail>(`/trades/${id}`);
  if (!data) return <Loading error={error} />;
  const t = data.trade;
  return (
    <>
      <div className="page-head">
        <h1><Link to="/trades" className="muted">거래 내역</Link> / {t.symbol} <span className="muted">{t.name}</span></h1>
      </div>
      <div className="card">
        <dl className="kv">
          <dt>결과</dt><dd><Delta value={t.pnl} money /> (<Delta value={t.pnl_pct} />) · 비용 {money(t.costs, true)} 차감 후</dd>
          <dt>데스크</dt><dd>{DESK_KO[t.desk]} · {t.qty}주 · {t.sector}</dd>
          <dt>진입</dt><dd className="num">{when(t.entry_ts)} · {money(t.entry_price, true)}</dd>
          <dt>청산</dt><dd className="num">{when(t.exit_ts)} · {money(t.exit_price, true)} · {EXIT_KO[t.exit_reason] ?? t.exit_reason}</dd>
        </dl>
      </div>
      {data.lesson && (
        <div className="card">
          <h2>성과 분석가의 복기</h2>
          <p style={{ margin: 0 }}>{data.lesson.lesson}</p>
        </div>
      )}
      {data.trail ? (
        <div className="card">
          <h2>
            진입 결정 과정
            <Link className="muted" to={`/meetings/${data.trail.meeting.id}`}>
              {when(data.trail.meeting.ts)} {KIND_KO[data.trail.meeting.kind] ?? data.trail.meeting.kind} 보기
            </Link>
          </h2>
          <div style={{ marginBottom: 14 }}><MacroNote r={data.trail.macro} /></div>
          <Trail reports={data.trail.reports} debate={data.trail.debate} decision={data.decision} consensus={data.decision?.consensus} />
        </div>
      ) : (
        <div className="note">
          {t.desk === "scalp"
            ? "스캘핑 거래입니다. 후보 종목과 전략은 장전 회의에서 스캘퍼가 고르고, 진입·청산은 실행 엔진이 5분봉 규칙대로 수행합니다."
            : "이 거래에 연결된 회의 기록이 없습니다."}
        </div>
      )}
    </>
  );
}
