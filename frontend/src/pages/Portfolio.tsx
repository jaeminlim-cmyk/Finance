import { useApi, type Position } from "../api";
import { Delta, DESK_KO, LimitBar, Loading, Sym, day, money, pct } from "../ui";

interface Resp {
  equity: number;
  cash: number;
  cash_pct: number;
  positions: Position[];
  sectors: { sector: string; value: number; weight: number }[];
  limits: { max_sector_pct: number; min_cash_pct: number };
}

export default function Portfolio() {
  const { data, error } = useApi<Resp>("/positions");
  if (!data) return <Loading error={error} />;
  const invested = 1 - data.cash_pct;
  return (
    <>
      <div className="page-head">
        <h1>포트폴리오</h1>
        <p>총 자산 {money(data.equity)} · 현금 {money(data.cash)} ({pct(data.cash_pct, 1)})</p>
      </div>
      <div className="card flush">
        <h2>보유 종목 <span className="muted">{data.positions.length}개 · 투자 비중 {pct(invested, 1)}</span></h2>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>종목</th><th>데스크</th><th>섹터</th><th className="r">수량</th><th className="r">평단가</th><th className="r">현재가</th>
                <th className="r">평가액</th><th className="r">비중</th><th className="r">평가손익</th><th className="r">수익률</th>
                <th className="r">손절가</th><th className="r">목표가</th><th>진입일</th>
              </tr>
            </thead>
            <tbody>
              {data.positions.map((p) => (
                <tr key={p.id}>
                  <td><Sym symbol={p.symbol} name={p.name} /></td>
                  <td>{DESK_KO[p.desk]}</td>
                  <td className="ink2">{p.sector}</td>
                  <td className="r num">{p.qty}</td>
                  <td className="r num">{money(p.avg_price, true)}</td>
                  <td className="r num">{money(p.price, true)}</td>
                  <td className="r num">{money(p.market_value)}</td>
                  <td className="r num">{pct(p.weight, 1)}</td>
                  <td className="r"><Delta value={p.unrealized_pnl} money /></td>
                  <td className="r"><Delta value={p.unrealized_pct} /></td>
                  <td className="r num">{money(p.stop_price, true)}</td>
                  <td className="r num">{money(p.target_price, true)}</td>
                  <td className="num muted">{day(p.opened_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {data.positions.length === 0 && <div className="empty">보유 종목이 없습니다</div>}
        </div>
      </div>
      <div className="card">
        <h2>섹터 분포 <span className="muted">세로선은 섹터당 한도 {pct(data.limits.max_sector_pct, 0)}</span></h2>
        <div className="bars">
          {data.sectors.map((s) => <LimitBar key={s.sector} name={s.sector} value={s.weight} limit={data.limits.max_sector_pct} max={data.limits.max_sector_pct * 1.5} />)}
          <LimitBar name="현금" value={data.cash_pct} max={1} title={`현금 ${pct(data.cash_pct, 1)} (최소 ${pct(data.limits.min_cash_pct, 0)} 유지)`} />
        </div>
        {data.sectors.length === 0 && <div className="empty">투자된 섹터가 없습니다</div>}
      </div>
    </>
  );
}
