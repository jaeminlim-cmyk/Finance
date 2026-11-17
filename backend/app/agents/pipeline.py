"""투자 회의 파이프라인.

종목 선별(코드) → 매크로 → 리서치 본부 병렬 분석 → Bull/Bear 토론 → 트레이더 매매안 → 리스크 검토 →
수석 트레이더 최종 결정. 결정은 'pending' 으로 기록되고, 실제 주문은 Executor 가 하드 리스크 규칙을 거쳐 낸다.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

from pydantic import BaseModel

from ..clock import iso
from ..data.features import atr_of, symbol_pack
from ..data.universe import screen
from ..llm.base import BudgetExceeded, LLMCall, LLMError
from ..models import SIGNAL_SCORE, FinalDecision
from .roster import AGENTS, CONSENSUS_WEIGHTS, RESEARCH_WAVE_1

if TYPE_CHECKING:
    from ..fund import Fund

log = logging.getLogger("fund.meeting")

MARKET = "_MARKET"  # 종목이 아닌 시장 전체 보고서(매크로)의 symbol 값
SCALP = "_SCALP"
DEBATE_MIN_CONSENSUS = 0.15   # 신규 후보가 토론에 올라가기 위한 최소 합의 점수
HOLDING_DEBATE_BELOW = -0.15  # 보유 종목의 합의 점수가 이보다 낮으면 약식 재검토 대신 정식 토론

FIN_KEYS = ("fiscal_period", "reported_at", "revenue_ttm_usd_m", "revenue_growth_yoy", "gross_margin",
            "operating_margin", "net_margin", "eps_ttm", "eps_growth_yoy", "eps_surprise_pct", "fcf_margin",
            "cfo_to_net_income", "debt_to_equity", "current_ratio", "roe")
VAL_KEYS = ("pe_ttm", "peg", "ps_ttm", "sector_median_pe", "analyst_target_mean", "insider_net_buy_3m_usd_m",
            "institutional_ownership_chg_qoq", "eps_growth_yoy", "revenue_growth_yoy", "fiscal_period")


def _pick(d: dict | None, keys) -> dict | None:
    return {k: d.get(k) for k in keys} if d else None


class MeetingRunner:
    def __init__(self, fund: "Fund"):
        self.f = fund

    # ── 공통 도우미 ────────────────────────────────────────
    def _call(self, agent_id: str, task: str, context: dict, untrusted: dict | None = None) -> BaseModel:
        a = AGENTS[agent_id]
        return self.f.llm.run(LLMCall(agent=a.id, tier=a.tier, system=a.system, task=task, context=context,
                                      schema=a.schema, untrusted=untrusted))

    def _stage(self, mid: int, stage: str) -> None:
        self.f.store.update("meetings", mid, stage=stage)
        self.f.store.emit("meeting", {"id": mid, "stage": stage})

    def _save_report(self, mid: int, symbol: str, agent: str, result: BaseModel | None, price: float | None,
                     error: str | None = None) -> None:
        now = iso(self.f.clock.now())
        payload = result.model_dump() if result else {"error": error}
        self.f.store.insert("reports", meeting_id=mid, ts=now, as_of=now, symbol=symbol, agent=agent,
                            signal=getattr(result, "signal", None) if result else None,
                            confidence=getattr(result, "confidence", None) if result else None,
                            summary=getattr(result, "summary", None) if result else f"보고 실패: {error}",
                            payload=payload, ref_price=price)
        self.f.store.emit("meeting", {"id": mid, "symbol": symbol, "agent": agent})

    def _weights(self) -> dict[str, float]:
        """직원별 합의 가중치. 평가된 보고가 20건 이상 쌓인 직원은 적중률에 따라 0.5~1.5배로 조정한다."""
        w = dict(CONSENSUS_WEIGHTS)
        for row in self.f.store.query(
                "SELECT agent, AVG(hit) AS rate, COUNT(hit) AS n FROM reports WHERE hit IS NOT NULL GROUP BY agent"):
            if row["agent"] in w and row["n"] >= 20:
                w[row["agent"]] *= 0.5 + row["rate"]
        return w

    @staticmethod
    def consensus(reports: dict[str, BaseModel | None], weights: dict[str, float]) -> float:
        """-1(전원 강력매도) ~ +1(전원 강력매수). 확신도와 직원 가중치를 반영한다."""
        num = den = 0.0
        for agent, rep in reports.items():
            if rep is None or agent not in weights:
                continue
            num += weights[agent] * SIGNAL_SCORE[rep.signal] * max(0.0, min(1.0, rep.confidence))
            den += weights[agent]
        return round(num / (2 * den), 3) if den else 0.0

    @staticmethod
    def _brief(reports: dict[str, BaseModel | None]) -> list[dict]:
        out = []
        for agent, rep in reports.items():
            if rep is None:
                out.append({"agent": agent, "title": AGENTS[agent].title, "signal": None, "summary": "보고 없음"})
            else:
                out.append({"agent": agent, "title": AGENTS[agent].title, "signal": rep.signal,
                            "confidence": rep.confidence, "summary": rep.summary,
                            "evidence": [e.point for e in rep.evidence], "risks": rep.risks,
                            "data_gaps": rep.data_gaps})
        return out

    def _lessons(self, symbol: str, sector: str) -> list[str]:
        rows = self.f.store.query(
            "SELECT lesson FROM lessons ORDER BY (symbol=?) DESC, (sector=?) DESC, id DESC LIMIT 5", (symbol, sector))
        return [r["lesson"] for r in rows]

    # ── 회의 실행 ──────────────────────────────────────────
    def run(self, kind: str = "premarket", holdings_only: bool = False, only: list[str] | None = None) -> int:
        f = self.f
        now = iso(f.clock.now())
        usage_mark = f.store.scalar("SELECT MAX(id) FROM llm_usage", default=0)
        mid = f.store.insert("meetings", ts=now, as_of=now, kind=kind, status="running", stage="종목 선별", symbols=[])
        f.store.audit(now, "meeting", "meeting_started", {"meeting_id": mid, "kind": kind})
        status, error = "done", None
        try:
            self._run(mid, kind, holdings_only, only)
        except BudgetExceeded as e:
            status, error = "budget_stopped", str(e)
            f.risk.event("llm_budget", str(e))
        except Exception as e:  # 회의 실패가 손절·청산 같은 보호 로직까지 멈추면 안 된다
            log.exception("회의 %s 실패", mid)
            status, error = "failed", f"{type(e).__name__}: {e}"
        cost = f.store.scalar("SELECT SUM(cost_usd) FROM llm_usage WHERE id>?", (usage_mark,), 0.0)
        f.store.update("meetings", mid, status=status, stage="종료", error=error, cost_usd=round(cost, 4))
        f.store.audit(iso(f.clock.now()), "meeting", f"meeting_{status}", {"meeting_id": mid, "error": error})
        f.store.emit("meeting", {"id": mid, "status": status})
        return mid

    def _run(self, mid: int, kind: str, holdings_only: bool, only: list[str] | None = None) -> None:
        f, cfg = self.f, self.f.cfg
        budget = f.llm.budget()
        if budget.level == "exhausted":
            raise BudgetExceeded(f"이번 달 LLM 예산 ${budget.budget:.2f} 소진 — 회의를 열지 않습니다.")
        holdings = {p["symbol"]: p for p in f.portfolio.positions() if p["desk"] == "swing"}
        if only is not None:  # 장중 점검: 지정된 보유 종목만 다시 본다
            holdings = {s: p for s, p in holdings.items() if s in only}
            holdings_only = True
        blocked = f.risk.entries_blocked()
        n_research, n_debate, rounds = cfg.universe.max_research, cfg.universe.max_debate, cfg.llm.debate_rounds
        notes = []
        if budget.level == "reduced":
            n_research, n_debate, rounds = max(2, n_research // 2), max(1, n_debate // 2), 1
            notes.append(f"LLM 예산 {budget.ratio:.0%} 사용 — 회의 규모 축소")
        if budget.level == "minimal":
            n_research = n_debate = 0
            notes.append(f"LLM 예산 {budget.ratio:.0%} 사용 — 보유 종목만 검토")
        if blocked:
            n_research = n_debate = 0
            notes.append(f"신규 진입 중단 상태({blocked}) — 보유 종목만 검토")
        snap0 = f.portfolio.snapshot()
        if snap0["cash"] - snap0["equity"] * cfg.risk.min_cash_pct < snap0["equity"] * 0.02 and n_research:
            n_research = n_debate = 0
            notes.append("최소 현금 비중을 빼면 매수 여력이 없어 신규 후보 리서치 생략")
        if holdings_only:
            n_research = n_debate = 0

        screened = screen(f.data, cfg, exclude=set(holdings)) if n_research else []
        candidates = screened[:n_research]

        self._stage(mid, "매크로 분석")
        macro = self._macro(mid)
        macro_brief = {"regime": macro.regime, "summary": macro.summary, "favored_sectors": macro.favored_sectors,
                       "avoided_sectors": macro.avoided_sectors} if macro else {"regime": "neutral", "summary": "매크로 보고 없음"}

        if kind == "premarket" and cfg.scalper.enabled and screened and budget.level in ("normal", "reduced"):
            self._stage(mid, "스캘핑 후보 선정")
            self._scalper_plan(mid, screened, macro_brief["regime"])

        self._stage(mid, "리서치")
        symbols = [*holdings, *[c["symbol"] for c in candidates]]
        packs = {s: symbol_pack(f.data, s, cfg.fund.benchmark) for s in symbols}
        macro_raw = f.data.macro()
        weights = self._weights()
        research: dict[str, dict[str, BaseModel | None]] = {}
        with ThreadPoolExecutor(max_workers=3) as pool:  # 종목 3개 × 분석가 6명까지 동시 호출
            for sym, reports in zip(symbols, pool.map(lambda s: self._research(mid, packs[s], macro_raw), symbols)):
                research[sym] = reports
        scores = {s: self.consensus(research[s], weights) for s in symbols}

        ranked = sorted((c["symbol"] for c in candidates), key=lambda s: scores[s], reverse=True)
        to_debate = [s for s in ranked if scores[s] >= DEBATE_MIN_CONSENSUS][:n_debate]
        board = []
        for s in symbols:
            role = "holding" if s in holdings else "candidate"
            outcome = "보유 재검토" if s in holdings else "토론 진행" if s in to_debate else "선별 탈락"
            board.append({"symbol": s, "name": packs[s]["name"], "role": role, "consensus": scores[s], "outcome": outcome})
        f.store.update("meetings", mid, symbols=board)

        self._stage(mid, "토론과 결정")
        tally = {"buy": 0, "sell": 0, "hold": 0}
        for sym in [*holdings, *to_debate]:
            position = holdings.get(sym)
            full = position is None or scores[sym] <= HOLDING_DEBATE_BELOW
            try:
                decision = self._decide(mid, packs[sym], research[sym], scores[sym], macro_brief, position, rounds, full)
                tally[decision.action] += 1
            except BudgetExceeded:
                raise
            except LLMError as e:
                # 판단을 받지 못한 종목은 아무것도 하지 않는다(보유 종목의 손절가는 그대로 유지된다).
                self._record_decision(mid, packs[sym], scores[sym], None, None, None, position, error=str(e))
        summary = (f"보유 {len(holdings)}종목 재검토, 신규 후보 {len(candidates)}종목 리서치, {len(to_debate)}종목 토론 → "
                   f"매수 {tally['buy']} · 매도 {tally['sell']} · 보유/관망 {tally['hold']}")
        f.store.update("meetings", mid, summary=" / ".join([summary, *notes]))

    # ── 단계별 구현 ────────────────────────────────────────
    def _macro(self, mid: int):
        raw = self.f.data.macro()
        try:
            rep = self._call("macro", "현재 거시 환경을 평가하고 시장 국면을 판정하세요.",
                             {"as_of": self.f.clock.now().isoformat(timespec="minutes"), "macro": raw or None})
        except BudgetExceeded:
            raise
        except LLMError as e:
            self._save_report(mid, MARKET, "macro", None, None, str(e))
            return None
        self._save_report(mid, MARKET, "macro", rep, raw.get("spy_close") if raw else None)
        return rep

    def _scalper_plan(self, mid: int, screened: list[dict], regime: str) -> None:
        f = self.f
        rows = screened[:12]
        allowed = {r["symbol"] for r in rows}
        try:
            plan = self._call("scalper", "오늘 장중 단타 후보와 전략을 고르세요.",
                              {"as_of": f.clock.now().isoformat(timespec="minutes"), "macro_regime": regime,
                               "max_candidates": f.cfg.scalper.max_candidates, "candidates": rows})
        except BudgetExceeded:
            raise
        except LLMError as e:
            self._save_report(mid, SCALP, "scalper", None, None, str(e))
            return
        picks = [c.model_dump() for c in plan.candidates if c.symbol in allowed][: f.cfg.scalper.max_candidates]
        f.store.set_state("scalp_plan", {"day": f.clock.now().date().isoformat(), "candidates": picks, "entered": []})
        self._save_report(mid, SCALP, "scalper", plan, None)

    def _research(self, mid: int, pack: dict, macro_raw: dict) -> dict[str, BaseModel | None]:
        base = {k: pack[k] for k in ("symbol", "name", "sector", "as_of", "price")}
        fund = pack["fundamentals"]
        comm = pack["community"]
        jobs: dict[str, tuple[str, dict, dict | None]] = {
            "chart": ("가격 구조를 분석하세요.", {**base, "chart": pack["chart"]}, None),
            "technical": ("기술적 지표를 해석하세요.", {**base, "indicators": pack["indicators"]}, None),
            "financials": ("재무제표를 분석하세요.", {**base, "fundamentals": _pick(fund, FIN_KEYS)}, None),
            "fundamental": ("현재 주가의 가치 평가를 하세요.",
                            {**base, "valuation": _pick(fund, VAL_KEYS), "next_earnings_date": pack["next_earnings_date"],
                             "earnings_within_days": pack["earnings_within_days"]}, None),
            "news": ("최근 뉴스를 분석하세요.", {**base, "news_count": len(pack["news"])}, {"news": pack["news"]}),
            "community": ("커뮤니티 동향을 분석하세요.",
                          {**base, "community": {k: v for k, v in comm.items() if k != "posts"} if comm else None},
                          {"posts": comm["posts"]} if comm else None),
        }
        out: dict[str, BaseModel | None] = {}

        def one(agent: str) -> BaseModel | None:
            task, ctx, untrusted = jobs[agent]
            try:
                rep = self._call(agent, task, ctx, untrusted)
            except BudgetExceeded:
                raise
            except LLMError as e:
                self._save_report(mid, pack["symbol"], agent, None, pack["price"], str(e))
                return None
            self._save_report(mid, pack["symbol"], agent, rep, pack["price"])
            return rep

        with ThreadPoolExecutor(max_workers=len(RESEARCH_WAVE_1)) as pool:
            for agent, rep in zip(RESEARCH_WAVE_1, pool.map(one, RESEARCH_WAVE_1)):
                out[agent] = rep

        def short(rep):
            return {"signal": rep.signal, "confidence": rep.confidence, "summary": rep.summary} if rep else None

        jobs["sentiment"] = ("이 종목을 둘러싼 심리를 종합하세요.", {
            **base, "news_report": short(out["news"]), "community_report": short(out["community"]),
            "community": {k: v for k, v in comm.items() if k != "posts"} if comm else None,
            "market": _pick(macro_raw, ("vix_proxy", "vix_proxy_change_20d", "spy_return_5d", "spy_return_20d")),
        }, None)
        out["sentiment"] = one("sentiment")
        return out

    def _decide(self, mid: int, pack: dict, reports: dict, score: float, macro: dict, position: dict | None,
                rounds: int, full: bool) -> FinalDecision:
        f, sym = self.f, pack["symbol"]
        price, atr = pack["price"], atr_of(pack)
        base = {"symbol": sym, "name": pack["name"], "sector": pack["sector"], "as_of": pack["as_of"], "price": price,
                "position": _pick(position, ("qty", "avg_price", "stop_price", "target_price", "unrealized_pct", "opened_at")),
                "macro": macro, "consensus_score": score, "reports": self._brief(reports),
                "earnings_within_days": pack["earnings_within_days"]}
        lessons = self._lessons(sym, pack["sector"])
        snap = f.portfolio.snapshot()
        equity = snap["equity"] or 1.0
        portfolio = {
            "equity": snap["equity"], "cash_pct": snap["cash_pct"],
            "positions": [{"symbol": p["symbol"], "sector": p["sector"], "weight": p["weight"],
                           "unrealized_pct": p["unrealized_pct"]} for p in snap["positions"]],
            "sector_pct": {k: round(v / equity, 4) for k, v in snap["sector_value"].items()},
        }
        debate: list[dict] = []
        proposal = review = None
        if full:
            last = {"bull": None, "bear": None}
            for rnd in range(1, rounds + 1):
                for side, other in (("bull", "bear"), ("bear", "bull")):
                    arg = self._call(side, f"{rnd}라운드 발언입니다. 논거를 제시하고, 상대 발언이 있으면 가장 강한 논점에 답하세요.",
                                     {**base, "round": rnd, "total_rounds": rounds,
                                      "your_previous": last[side], "opponent_latest": last[other]})
                    last[side] = arg.model_dump()
                    turn = {"round": rnd, "side": side, **last[side]}
                    debate.append(turn)
                    f.store.insert("debate", meeting_id=mid, ts=iso(f.clock.now()), symbol=sym, round=rnd, side=side,
                                   payload=last[side])
                    f.store.emit("meeting", {"id": mid, "symbol": sym, "agent": side})
            stop = f.risk.default_stop(price, atr)
            defaults = {"entry_reference": price, "atr14": atr, "default_stop": stop,
                        "default_target": f.risk.default_target(price, stop)}
            proposal = self._call("trader", "토론 결과를 바탕으로 매매안을 작성하세요.",
                                  {**base, "debate": debate, "defaults": defaults, "lessons": lessons})
            ind = pack["indicators"] or {}
            exposure = f.risk.exposure(snap)
            review = self._call("risk", "이 매매안을 검토하세요.", {
                **{k: base[k] for k in ("symbol", "name", "sector", "as_of", "price", "position", "macro",
                                        "consensus_score", "earnings_within_days")},
                "proposal": proposal.model_dump(), "defaults": defaults, "portfolio": portfolio,
                "risk_state": {"day_pnl_pct": exposure["day_pnl_pct"], "drawdown_pct": exposure["drawdown_pct"],
                               "open_risk_pct": exposure["open_risk_pct"]},
                "volatility": {"atr_pct": ind.get("atr_pct"), "realized_vol_20d": ind.get("realized_vol_20d")},
                "reports": [{"title": r["title"], "signal": r["signal"], "risks": r.get("risks", [])} for r in base["reports"]],
            })
        task = ("모든 자료를 종합해 최종 결정을 내리세요." if full else
                "보유 중인 종목입니다. 토론 없이 약식으로 재검토합니다. 계속 보유할 근거가 유효한지 판단해 hold 또는 sell 로 "
                "결정하세요. 손절가를 올리는 것이 적절하면 stop_price 에 새 값을 적고, 아니면 null 로 두세요.")
        final = self._call("head", task, {
            **base, "mode": "full_review" if full else "holding_review", "debate": debate,
            "proposal": proposal.model_dump() if proposal else None,
            "risk_review": review.model_dump() if review else None, "portfolio": portfolio, "lessons": lessons})
        # 수석 트레이더도 넘을 수 없는 절차 규칙: 리스크 관리자의 거부·축소는 코드가 다시 한 번 강제한다.
        if review is not None and final.action == "buy":
            if review.verdict == "reject":
                final = final.model_copy(update={"action": "hold", "size_multiplier": 0.0,
                                                 "dissent": (final.dissent + " " if final.dissent else "") +
                                                 "[시스템] 리스크 관리자가 거부한 매수라 보유(관망)로 바꿨습니다."})
            elif review.verdict == "reduce":
                final = final.model_copy(update={"size_multiplier": min(final.size_multiplier, review.size_multiplier)})
        if position is None and final.action == "sell":  # 롱 온리: 없는 종목은 팔 수 없다
            final = final.model_copy(update={"action": "hold"})
        self._record_decision(mid, pack, score, proposal, review, final, position)
        return final

    def _record_decision(self, mid: int, pack: dict, score: float, proposal, review, final: FinalDecision | None,
                         position: dict | None, error: str | None = None) -> None:
        f = self.f
        exec_ctx = {"atr": atr_of(pack), "sector": pack["sector"], "ref_price": pack["price"],
                    "earnings_within_days": pack["earnings_within_days"],
                    "holding": position is not None}
        if final is None:
            action, status, detail = "hold", "error", f"판단 실패: {error}"
        else:
            action = final.action
            status = "pending" if action in ("buy", "sell") or (position and final.stop_price) else "no_action"
            detail = None
        did = f.store.insert(
            "decisions", meeting_id=mid, ts=iso(f.clock.now()), symbol=pack["symbol"], action=action,
            conviction=final.conviction if final else None, rationale=final.rationale if final else None,
            dissent=final.dissent if final else None, consensus=score,
            proposal=proposal.model_dump() if proposal else None, risk_review=review.model_dump() if review else None,
            decision={"final": final.model_dump() if final else None, "exec": exec_ctx},
            status=status, status_detail=detail)
        f.store.audit(iso(f.clock.now()), "head", "decision", {"decision_id": did, "symbol": pack["symbol"], "action": action})
        f.store.emit("meeting", {"id": mid, "symbol": pack["symbol"], "agent": "head"})
