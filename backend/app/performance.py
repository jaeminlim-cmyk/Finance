"""성과 측정, 직원별 신호 적중률 채점, 청산 거래 복기."""

from __future__ import annotations

from math import sqrt
from statistics import fmean, pstdev
from typing import TYPE_CHECKING

from .agents.roster import AGENTS, CODE_STAFF
from .clock import add_trading_days, iso, last_completed_session, parse_iso
from .llm.base import BudgetExceeded, LLMCall, LLMError
from .models import SIGNAL_SCORE
from .store import Store

if TYPE_CHECKING:
    from .fund import Fund

SCORE_HORIZON_DAYS = 5  # 보고 후 5거래일 수익률로 신호를 채점한다


def daily_equity(store: Store) -> list[dict]:
    """하루의 마지막 평가액만 추린 시계열."""
    return store.query(
        "SELECT e.* FROM equity e JOIN (SELECT day, MAX(ts) AS ts FROM equity GROUP BY day) m ON e.ts=m.ts ORDER BY e.ts")


def metrics(store: Store, initial_capital: float) -> dict:
    rows = daily_equity(store)
    trades = store.query("SELECT * FROM trades ORDER BY id")
    llm_cost = float(store.scalar("SELECT SUM(cost_usd) FROM llm_usage", default=0.0))
    out: dict = {"days": len(rows), "initial_capital": initial_capital, "llm_cost_usd": round(llm_cost, 2),
                 "trade_count": len(trades)}
    if rows:
        eq = [initial_capital] + [r["equity"] for r in rows]
        rets = [eq[i] / eq[i - 1] - 1 for i in range(1, len(eq))]
        peak, mdd = eq[0], 0.0
        for v in eq:
            peak = max(peak, v)
            mdd = min(mdd, v / peak - 1)
        sd = pstdev(rets) if len(rets) > 1 else 0.0
        bench = [r["benchmark"] for r in rows if r["benchmark"]]
        total = eq[-1] / initial_capital - 1
        # 벤치마크는 첫 기록일 종가부터 잰다. 펀드는 그 전날 종가 기준 초기 자본에서 시작하므로 하루 차이가 있다.
        bench_ret = bench[-1] / bench[0] - 1 if len(bench) > 1 else 0.0
        out.update({
            "equity": round(eq[-1], 2),
            "total_return": round(total, 5),
            "benchmark_return": round(bench_ret, 5),
            "excess_return": round(total - bench_ret, 5),
            "sharpe": round(fmean(rets) / sd * sqrt(252), 2) if sd > 0 else None,
            "volatility": round(sd * sqrt(252), 4) if sd else None,
            "max_drawdown": round(mdd, 5),
            "net_profit_after_llm": round(eq[-1] - initial_capital - llm_cost, 2),
            "return_after_llm": round((eq[-1] - llm_cost) / initial_capital - 1, 5),
        })
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    if trades:
        avg_win = fmean(t["pnl"] for t in wins) if wins else 0.0
        avg_loss = abs(fmean(t["pnl"] for t in losses)) if losses else 0.0
        out.update({
            "win_rate": round(len(wins) / len(trades), 4),
            "avg_win": round(avg_win, 2), "avg_loss": round(avg_loss, 2),
            "payoff_ratio": round(avg_win / avg_loss, 2) if avg_loss else None,
            "realized_pnl": round(sum(t["pnl"] for t in trades), 2),
            "trading_costs": round(sum(t["costs"] for t in trades), 2),
            "by_desk": {desk: {"trades": len(ts), "pnl": round(sum(t["pnl"] for t in ts), 2),
                               "win_rate": round(sum(t["pnl"] > 0 for t in ts) / len(ts), 4)}
                        for desk in ("swing", "scalp") if (ts := [t for t in trades if t["desk"] == desk])},
        })
    return out


def monthly_returns(store: Store, initial_capital: float) -> list[dict]:
    rows = daily_equity(store)
    out, prev_eq, prev_b = [], initial_capital, None
    months: dict[str, dict] = {}
    for r in rows:
        months[r["day"][:7]] = r
    for month, r in months.items():
        b = r["benchmark"]
        out.append({"month": month, "return": round(r["equity"] / prev_eq - 1, 5),
                    "benchmark": round(b / prev_b - 1, 5) if b and prev_b else None})
        prev_eq, prev_b = r["equity"], b or prev_b
    return out


def agent_stats(store: Store) -> list[dict]:
    stats = {r["agent"]: r for r in store.query(
        "SELECT agent, COUNT(*) AS reports, AVG(confidence) AS avg_confidence, SUM(hit IS NOT NULL) AS scored, "
        "AVG(hit) AS hit_rate, "
        "AVG(CASE WHEN signal IN ('buy','strong_buy') THEN fwd_return END) AS avg_fwd_when_bullish, "
        "AVG(CASE WHEN signal IN ('sell','strong_sell') THEN fwd_return END) AS avg_fwd_when_bearish "
        "FROM reports GROUP BY agent")}
    usage = {r["agent"]: r for r in store.query(
        "SELECT agent, COUNT(*) AS calls, SUM(cost_usd) AS cost_usd FROM llm_usage GROUP BY agent")}
    latest = {r["agent"]: r for r in store.query(
        "SELECT r.agent, r.symbol, r.signal, r.confidence, r.summary, r.ts FROM reports r "
        "JOIN (SELECT agent, MAX(id) AS id FROM reports GROUP BY agent) m ON r.id=m.id")}
    for side in ("bull", "bear"):
        row = store.one("SELECT symbol, payload, ts FROM debate WHERE side=? ORDER BY id DESC LIMIT 1", (side,))
        if row:
            latest[side] = {"symbol": row["symbol"], "summary": row["payload"].get("thesis"), "ts": row["ts"],
                            "confidence": row["payload"].get("conviction"), "signal": None}
    for agent, col in (("head", "rationale"), ("trader", "proposal"), ("risk", "risk_review")):
        row = store.one(f"SELECT symbol, rationale, proposal, risk_review, ts FROM decisions "
                        f"WHERE {col} IS NOT NULL ORDER BY id DESC LIMIT 1")
        if row:
            v = row[col]
            ko = {"buy": "매수", "sell": "매도", "hold": "보유·관망", "approve": "승인", "reduce": "축소", "reject": "거부"}
            summary = v if isinstance(v, str) else f"{ko.get(v.get('action') or v.get('verdict'), '')}: {v.get('rationale')}"
            latest[agent] = {"symbol": row["symbol"], "summary": summary, "ts": row["ts"], "signal": None, "confidence": None}
    row = store.one("SELECT symbol, lesson, ts FROM lessons ORDER BY id DESC LIMIT 1")
    if row:
        latest["performance"] = {"symbol": row["symbol"], "summary": row["lesson"], "ts": row["ts"], "signal": None,
                                 "confidence": None}
    out = []
    for a in AGENTS.values():
        s, u = stats.get(a.id, {}), usage.get(a.id, {})
        out.append({"id": a.id, "title": a.title, "division": a.division, "tier": a.tier, "duty": a.duty,
                    "reports": s.get("reports", 0), "scored": s.get("scored") or 0,
                    "hit_rate": round(s["hit_rate"], 4) if s.get("hit_rate") is not None else None,
                    "avg_confidence": round(s["avg_confidence"], 3) if s.get("avg_confidence") is not None else None,
                    "avg_fwd_when_bullish": round(s["avg_fwd_when_bullish"], 5) if s.get("avg_fwd_when_bullish") is not None else None,
                    "avg_fwd_when_bearish": round(s["avg_fwd_when_bearish"], 5) if s.get("avg_fwd_when_bearish") is not None else None,
                    "llm_calls": u.get("calls", 0), "llm_cost_usd": round(u.get("cost_usd") or 0.0, 4),
                    "latest": latest.get(a.id)})
    out.extend({**c, "reports": 0, "scored": 0, "hit_rate": None, "latest": None} for c in CODE_STAFF)
    return out


class Performance:
    def __init__(self, fund: "Fund"):
        self.f = fund

    def score_reports(self) -> int:
        """5거래일이 지난 분석가 보고서를 실제 수익률로 채점한다. hold 의견은 수익률만 기록하고 적중 여부는 매기지 않는다."""
        f, n = self.f, 0
        done = last_completed_session(f.clock.now())
        rows = f.store.query(
            "SELECT id, symbol, signal, ref_price, as_of FROM reports "
            "WHERE fwd_return IS NULL AND signal IS NOT NULL AND ref_price IS NOT NULL AND symbol NOT LIKE '\\_%' ESCAPE '\\'")
        closes: dict[str, dict] = {}
        for r in rows:
            target = add_trading_days(last_completed_session(parse_iso(r["as_of"])), SCORE_HORIZON_DAYS)
            if target > done:
                continue
            if r["symbol"] not in closes:
                closes[r["symbol"]] = {b.ts.date(): b.close for b in f.data.daily_bars(r["symbol"], 400)}
            close = closes[r["symbol"]].get(target)
            if close is None:
                continue
            fwd = close / r["ref_price"] - 1
            s = SIGNAL_SCORE[r["signal"]]
            hit = None if s == 0 else int((s > 0) == (fwd > 0))
            f.store.execute("UPDATE reports SET fwd_return=?, hit=? WHERE id=?", (round(fwd, 5), hit, r["id"]))
            n += 1
        return n

    def review_closed_trades(self) -> int:
        """청산된 스윙 거래를 성과 분석가가 복기하고 교훈을 남긴다. 스캘핑 거래는 건수가 많아 통계로만 본다."""
        f, n = self.f, 0
        f.store.execute("UPDATE trades SET reviewed=1 WHERE reviewed=0 AND desk='scalp'")
        a = AGENTS["performance"]
        for t in f.store.query("SELECT * FROM trades WHERE reviewed=0 AND desk='swing' ORDER BY id"):
            d = f.store.one("SELECT * FROM decisions WHERE id=?", (t["decision_id"],)) if t["decision_id"] else None
            reports = f.store.query(
                "SELECT agent, signal, confidence, summary FROM reports WHERE meeting_id=? AND symbol=?",
                (d["meeting_id"], t["symbol"])) if d else []
            ctx = {
                "trade": {k: t[k] for k in ("symbol", "sector", "qty", "entry_price", "exit_price", "entry_ts", "exit_ts",
                                            "pnl", "pnl_pct", "exit_reason")},
                "consensus_score": d["consensus"] if d else None,
                "decision": {"rationale": d["rationale"], "dissent": d["dissent"], "conviction": d["conviction"],
                             "risk_review": d["risk_review"]} if d else None,
                "reports": reports,
            }
            try:
                pm = f.llm.run(LLMCall(agent=a.id, tier=a.tier, system=a.system,
                                       task="이 청산 거래를 복기하고 교훈을 정리하세요.", context=ctx, schema=a.schema))
            except BudgetExceeded:
                break  # 예산이 돌아오면 다음에 복기한다
            except LLMError:
                continue
            f.store.insert("lessons", ts=iso(f.clock.now()), trade_id=t["id"], symbol=t["symbol"], sector=t["sector"],
                           pnl_pct=t["pnl_pct"], lesson=pm.lesson, payload=pm.model_dump())
            f.store.execute("UPDATE trades SET reviewed=1 WHERE id=?", (t["id"],))
            n += 1
        return n
