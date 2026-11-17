"""펀드 본체: 구성 요소 조립, 주문 실행기, 하루 운용 사이클."""

from __future__ import annotations

import logging
from datetime import date, time
from statistics import fmean

from .agents.pipeline import MeetingRunner
from .broker.paper import PaperBroker
from .clock import ET, Clock, RealClock, SimClock, at, is_market_open, iso, parse_iso, session_close, session_open
from .config import Config, validate
from .data.base import DataProvider, MarketData
from .llm.base import LLMClient, LLMGateway
from .performance import Performance
from .portfolio import Portfolio
from .risk import RiskEngine
from .scalper import ScalpEngine
from .store import Store

log = logging.getLogger("fund")


def make_provider(cfg: Config) -> DataProvider:
    if cfg.data_mode == "live":
        from .data.live import LiveProvider
        return LiveProvider()
    from .data.mock import MockProvider
    return MockProvider()


def make_llm(cfg: Config) -> LLMClient:
    if cfg.llm_mode == "anthropic":
        from .llm.anthropic_llm import AnthropicLLM
        return AnthropicLLM(cfg)
    from .llm.mock_llm import MockLLM
    return MockLLM()


class Fund:
    def __init__(self, cfg: Config, store: Store, provider: DataProvider, llm_client: LLMClient,
                 clock: Clock | None = None, *, historical: bool = False, llm_budget_usd: float | None = None):
        validate(cfg)
        self.cfg, self.store = cfg, store
        self.clock = clock or RealClock()
        self.historical = historical
        self.data = MarketData(provider, self.clock, historical=historical)
        self.llm = LLMGateway(store, llm_client, cfg, budget_usd=llm_budget_usd)
        self.portfolio = Portfolio(store, self.data, cfg)
        self.risk = RiskEngine(store, cfg, self.clock)
        self.broker = PaperBroker(store, cfg, self.clock)
        self.meetings = MeetingRunner(self)
        self.scalper = ScalpEngine(self)
        self.performance = Performance(self)

    # ── 주문 실행 ──────────────────────────────────────────
    def execute_pending(self) -> int:
        """수석 트레이더의 결정을 하드 리스크 규칙에 통과시켜 주문으로 바꾼다."""
        n = 0
        today = self.clock.now().date()
        for d in self.store.query("SELECT * FROM decisions WHERE status='pending' ORDER BY id"):
            if parse_iso(d["ts"]).date() != today:
                self.store.update("decisions", d["id"], status="expired", status_detail="결정 당일에 실행되지 않아 폐기")
                continue
            self._execute(d)
            n += 1
        return n

    def _execute(self, d: dict) -> None:
        sym, final, ctx = d["symbol"], d["decision"]["final"], d["decision"]["exec"]
        price = self.data.quote(sym)
        pos = self.portfolio.position(sym, "swing")
        bars = self.data.daily_bars(sym, 20)
        avg_volume = fmean(b.volume for b in bars) if bars else None
        adv = fmean(b.volume * b.close for b in bars) if bars else None

        if d["action"] == "buy":
            if pos:
                self.store.update("decisions", d["id"], status="no_action", status_detail="이미 보유 중 — 추가 매수하지 않음")
                return
            stop = self.risk.sanitize_stop(price, final.get("stop_price"), ctx.get("atr")) if price else None
            target = final.get("target_price")
            if price and (not target or target <= price * 1.01):
                target = self.risk.default_target(price, stop)
            snap = self.portfolio.snapshot()
            self.risk.update_limits(snap["equity"])
            res = self.risk.check_buy(symbol=sym, price=price, stop_price=stop, desk="swing", sector=ctx.get("sector"),
                                      snapshot=snap, size_multiplier=final.get("size_multiplier", 1.0),
                                      avg_dollar_volume=adv, earnings_within_days=ctx.get("earnings_within_days"))
            if not res.allowed:
                self.broker.reject(sym, "buy", 0, price or 0.0, desk="swing", reason=res.reason, decision_id=d["id"])
                self.risk.event("order_blocked", f"{sym} 매수 차단: {res.reason}", sym)
                self.store.update("decisions", d["id"], status="blocked", status_detail=res.reason)
                return
            order = self.broker.buy(sym, res.qty, price, desk="swing", stop_price=stop, target_price=target,
                                    sector=ctx.get("sector"), decision_id=d["id"], bar_volume=avg_volume,
                                    note=res.reason)
            ok = order["status"] in ("filled", "partial")
            self.store.update("decisions", d["id"], status="executed" if ok else "blocked",
                              status_detail=res.reason or order.get("reason"), qty=order.get("filled_qty"),
                              stop_price=stop, target_price=target)
        elif d["action"] == "sell":
            res = self.risk.check_sell(held_qty=pos["qty"] if pos else 0, qty=pos["qty"] if pos else 0, protective=False)
            if not res.allowed:
                self.risk.event("order_blocked", f"{sym} 매도 차단: {res.reason}", sym)
                self.store.update("decisions", d["id"], status="blocked", status_detail=res.reason)
                return
            order = self.broker.sell(sym, res.qty, price, desk="swing", reason="decision", bar_volume=avg_volume)
            self.store.update("decisions", d["id"], status="executed", qty=order.get("filled_qty"))
        else:  # 보유 유지 + 손절가 조정. 손절가는 올리기만 한다.
            new_stop = final.get("stop_price")
            if pos and new_stop and price and (pos["stop_price"] or 0) < new_stop < price:
                self.store.update("positions", pos["id"], stop_price=round(new_stop, 2))
                self.store.update("decisions", d["id"], status="executed", stop_price=round(new_stop, 2),
                                  status_detail=f"손절가 {pos['stop_price']} → {round(new_stop, 2)} 상향")
            else:
                self.store.update("decisions", d["id"], status="no_action")

    # ── 손절·목표가 관리 ───────────────────────────────────
    def check_exits_live(self) -> int:
        """실시간: 현재가가 손절가·목표가에 닿은 스윙 포지션을 청산한다."""
        n = 0
        for p in self.store.query("SELECT * FROM positions WHERE desk='swing'"):
            price = self.data.quote(p["symbol"])
            if price is None:
                continue
            reason = "stop" if p["stop_price"] and price <= p["stop_price"] else \
                "target" if p["target_price"] and price >= p["target_price"] else None
            if reason and self.risk.check_sell(held_qty=p["qty"], qty=p["qty"], protective=True).allowed:
                self.broker.sell(p["symbol"], p["qty"], price, desk="swing", reason=reason)
                n += 1
        return n

    def check_exits_daily_bar(self) -> int:
        """일봉 시뮬레이션: 장 마감 뒤 당일 봉의 저가·고가로 손절·목표가 체결을 재현한다.

        한 봉 안에서 둘 다 닿았으면 손절이 먼저였다고 가정한다(보수적). 갭으로 손절가를 건너뛰면 시가에 체결한다.
        """
        n, today = 0, self.clock.now().date()
        for p in self.store.query("SELECT * FROM positions WHERE desk='swing'"):
            bars = self.data.daily_bars(p["symbol"], 1)
            if not bars or bars[-1].ts.date() != today:
                continue
            bar, price, reason = bars[-1], None, None
            if p["stop_price"] and bar.low <= p["stop_price"]:
                price, reason = min(bar.open, p["stop_price"]), "stop"
            elif p["target_price"] and bar.high >= p["target_price"]:
                price, reason = max(bar.open, p["target_price"]), "target"
            if reason:
                self.broker.sell(p["symbol"], p["qty"], price, desk="swing", reason=reason, bar_volume=bar.volume,
                                 note="일봉 기준 체결 재현")
                n += 1
        return n

    # ── 운용 단계 ──────────────────────────────────────────
    def mark(self) -> dict:
        """평가액을 기록하고 계좌 한도(일일 손실·낙폭)를 검사한다."""
        snap = self.portfolio.record_equity()
        self.risk.update_limits(snap["equity"])
        return snap

    def run_meeting(self, kind: str = "premarket", holdings_only: bool = False, only: list[str] | None = None) -> int:
        self.risk.update_limits(self.portfolio.snapshot()["equity"])
        mid = self.meetings.run(kind, holdings_only=holdings_only, only=only)
        if is_market_open(self.clock.now()):
            self.execute_pending()
        return mid

    def end_of_day(self) -> None:
        self.performance.score_reports()
        self.performance.review_closed_trades()

    def simulate_day(self, day: date) -> None:
        """하루를 일봉+5분봉 기준으로 빠르게 재현한다(백테스트·데모 시뮬레이션용). SimClock 이 필요하다."""
        clock = self.clock
        assert isinstance(clock, SimClock), "simulate_day 는 SimClock 에서만 쓸 수 있습니다"
        clock.set(at(day, time(9, 0)))
        self.risk.update_limits(self.portfolio.snapshot()["equity"])  # 전일 종가 기준으로 하루 시작
        self.meetings.run("premarket")
        clock.set(session_open(day))
        self.execute_pending()
        if self.cfg.scalper.enabled:
            self.scalper.run_day(day)
        clock.set(session_close(day))
        self.check_exits_daily_bar()
        self.mark()
        self.end_of_day()
        self.store.set_state("last_sim_day", day.isoformat())


def build_fund(cfg: Config, *, store: Store | None = None, clock: Clock | None = None, historical: bool = False,
               llm_budget_usd: float | None = None) -> Fund:
    return Fund(cfg, store or Store(cfg.db_path), make_provider(cfg), make_llm(cfg), clock,
                historical=historical, llm_budget_usd=llm_budget_usd)


__all__ = ["Fund", "build_fund", "make_provider", "make_llm", "ET", "iso"]
