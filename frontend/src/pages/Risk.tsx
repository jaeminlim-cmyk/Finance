import { useApi, type Order } from "../api";
import { Delta, DESK_KO, LimitBar, Loading, Sym, pct, when } from "../ui";

interface Exposure {
  limits: Record<string, number>;
  kill_switch: boolean;
  halted: boolean;
  daily_stop: boolean;
  entries_blocked: string | null;
  day_pnl_pct: number;
  drawdown_pct: number;
  cash_pct: number;
  largest_position_pct: number;
  positions_pct: Record<string, number>;
  sector_pct: Record<string, number>;
  scalp_pct: number;
  open_risk_pct: number;
}

interface Resp {
  exposure: Exposure;
  events: { id: number; ts: string; kind: string; symbol: string | null; detail: string }[];
  blocked: Order[];
}

const EVENT_KO: Record<string, string> = {
  order_blocked: "주문 차단", daily_loss_limit: "일일 손실 한도", max_drawdown: "낙폭 한도", kill_switch_on: "킬 스위치 켬",
  kill_switch_off: "킬 스위치 끔", resume: "재개", llm_budget: "LLM 예산",
};

export default function Risk() {
  const { data, error } = useApi<Resp>("/risk");
  if (!data) return <Loading error={error} />;
  const e = data.exposure, L = e.limits;
  return (
    <>
      <div className="page-head">
        <h1>리스크</h1>
        <p>아래 한도는 주문 직전에 코드가 검사합니다. 어떤 직원의 결정도 이 한도를 넘을 수 없습니다</p>
      </div>
      <div className={`note ${e.entries_blocked ? "crit" : ""}`}>
        {e.entries_blocked ? `신규 진입 중단 — ${e.entries_blocked}` : "정상 — 신규 진입 가능"}
      </div>
      <div className="grid cols-2">
        <div className="card">
          <h2>계좌 한도 <span className="muted">세로선이 한도</span></h2>
          <div className="bars">
            <LimitBar name="오늘 손실" value={Math.max(0, -e.day_pnl_pct)} limit={L.daily_loss_limit_pct} max={L.daily_loss_limit_pct * 1.5}
                      title={`오늘 손익 ${pct(e.day_pnl_pct)} · 한도 -${pct(L.daily_loss_limit_pct, 0)} 에 닿으면 당일 신규 진입 중단`} />
            <LimitBar name="고점 대비 낙폭" value={Math.max(0, -e.drawdown_pct)} limit={L.max_drawdown_pct} max={L.max_drawdown_pct * 1.5}
                      title={`낙폭 ${pct(e.drawdown_pct)} · 한도 -${pct(L.max_drawdown_pct, 0)} 에 닿으면 전체 중단`} />
            <LimitBar name="가장 큰 종목" value={e.largest_position_pct} limit={L.max_position_pct} max={L.max_position_pct * 1.5} />
            <LimitBar name="스캘핑 자본" value={e.scalp_pct} limit={L.scalper_capital_pct} max={L.scalper_capital_pct * 1.5} />
            <LimitBar name="열린 손절 위험" value={e.open_risk_pct} max={0.15}
                      title={`모든 포지션이 동시에 손절될 때의 손실 ${pct(e.open_risk_pct)} · 거래당 한도 ${pct(L.max_risk_per_trade_pct, 1)}`} />
          </div>
          <p className="muted small" style={{ margin: "12px 0 0" }}>
            오늘 손익 <Delta value={e.day_pnl_pct} /> · 현금 비중 {pct(e.cash_pct, 1)} (최소 {pct(L.min_cash_pct, 0)})
          </p>
        </div>
        <div className="card">
          <h2>섹터 비중 <span className="muted">한도 {pct(L.max_sector_pct, 0)}</span></h2>
          <div className="bars">
            {Object.entries(e.sector_pct).map(([k, v]) => <LimitBar key={k} name={k} value={v} limit={L.max_sector_pct} max={L.max_sector_pct * 1.5} />)}
          </div>
          {Object.keys(e.sector_pct).length === 0 && <div className="empty">보유 종목이 없습니다</div>}
        </div>
      </div>
      <div className="card">
        <h2>종목 비중 <span className="muted">한도 {pct(L.max_position_pct, 0)} · 진입 후 주가가 오르면 한도를 조금 넘을 수 있습니다</span></h2>
        <div className="bars">
          {Object.entries(e.positions_pct).map(([k, v]) => <LimitBar key={k} name={k} value={v} limit={L.max_position_pct} max={L.max_position_pct * 1.5} />)}
        </div>
        {Object.keys(e.positions_pct).length === 0 && <div className="empty">보유 종목이 없습니다</div>}
      </div>
      <div className="grid cols-2">
        <div className="card flush">
          <h2>차단된 주문 <span className="muted">하드 리스크 규칙에 걸려 나가지 못한 주문</span></h2>
          <div className="table-wrap" style={{ maxHeight: 460, overflowY: "auto" }}>
            <table>
              <thead><tr><th>시각</th><th>종목</th><th>구분</th><th>차단 사유</th></tr></thead>
              <tbody>
                {data.blocked.map((x) => (
                  <tr key={x.id}>
                    <td className="num muted">{when(x.ts)}</td>
                    <td><Sym symbol={x.symbol} /></td>
                    <td>{DESK_KO[x.desk]} {x.side === "buy" ? "매수" : "매도"}</td>
                    <td className="wrap ink2">{x.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {data.blocked.length === 0 && <div className="empty">차단된 주문이 없습니다</div>}
          </div>
        </div>
        <div className="card flush">
          <h2>리스크 이벤트 <span className="muted">한도 도달, 킬 스위치, 예산</span></h2>
          <div className="table-wrap" style={{ maxHeight: 460, overflowY: "auto" }}>
            <table>
              <thead><tr><th>시각</th><th>종류</th><th>내용</th></tr></thead>
              <tbody>
                {data.events.map((x) => (
                  <tr key={x.id}>
                    <td className="num muted">{when(x.ts)}</td>
                    <td>{EVENT_KO[x.kind] ?? x.kind}</td>
                    <td className="wrap ink2">{x.detail}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {data.events.length === 0 && <div className="empty">이벤트가 없습니다</div>}
          </div>
        </div>
      </div>
    </>
  );
}
