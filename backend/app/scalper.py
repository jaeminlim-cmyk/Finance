"""스캘핑 데스크 실행 엔진.

LLM(스캘퍼 에이전트)은 장 시작 전에 후보 종목과 전략만 고른다. 진입·청산은 이 엔진이 5분봉 규칙대로
기계적으로 수행하고, 장 마감 전에 전량 청산한다. 배정 자본 한도는 RiskEngine 이 주문마다 강제한다.
"""

from __future__ import annotations

from datetime import date, time, timedelta
from statistics import pstdev
from typing import TYPE_CHECKING

from .clock import SimClock, session_open
from .data import indicators as ind
from .models import Bar

if TYPE_CHECKING:
    from .fund import Fund

OPENING_RANGE_BARS = 6          # 장 시작 30분
ORB_ENTRY_UNTIL = time(11, 30)
NO_ENTRY_AFTER = time(15, 0)
FLATTEN_AT = time(15, 55)
VWAP_Z_ENTRY = -2.0


class ScalpEngine:
    def __init__(self, fund: "Fund"):
        self.f = fund
        self._seen: dict[str, object] = {}

    def plan(self) -> dict | None:
        plan = self.f.store.get_state("scalp_plan")
        if plan and plan["day"] == self.f.clock.now().date().isoformat():
            return plan
        return None

    # ── 진입 신호 ──────────────────────────────────────────
    @staticmethod
    def orb_signal(bars: list[Bar]) -> tuple[float, float, float] | None:
        """시가 범위 돌파. (진입가, 손절가, 목표가) 또는 None."""
        if len(bars) <= OPENING_RANGE_BARS or bars[-1].ts.time() > ORB_ENTRY_UNTIL:
            return None
        opening = bars[:OPENING_RANGE_BARS]
        hi, lo = max(b.high for b in opening), min(b.low for b in opening)
        last, prev = bars[-1], bars[-2]
        if not (last.close > hi >= prev.close):  # 이번 봉에서 처음 돌파한 경우만
            return None
        entry = last.close
        stop = min((hi + lo) / 2, entry * 0.9985)
        return entry, round(stop, 2), round(entry + 1.5 * (entry - stop), 2)

    @staticmethod
    def vwap_signal(bars: list[Bar]) -> tuple[float, float, float] | None:
        """VWAP 회귀. 종가가 VWAP 아래로 표준편차의 2배 넘게 밀리면 진입, VWAP 에서 청산."""
        if len(bars) < 12:
            return None
        v = ind.vwap(bars)
        dev = pstdev(b.close for b in bars)
        if not v or dev <= 0:
            return None
        entry = bars[-1].close
        if (entry - v) / dev > VWAP_Z_ENTRY or (v - entry) / entry < 0.002:
            return None
        return entry, round(entry - 0.75 * (v - entry), 2), round(v, 2)

    # ── 봉 처리 ────────────────────────────────────────────
    def on_bar(self) -> None:
        """새 5분봉이 확정될 때마다 호출한다. 같은 봉을 두 번 처리하지 않는다."""
        f = self.f
        now = f.clock.now()
        plan = self.plan()
        open_scalps = {p["symbol"]: p for p in f.store.query("SELECT * FROM positions WHERE desk='scalp'")}
        strategies = {c["symbol"]: c["strategy"] for c in (plan["candidates"] if plan else [])}
        for sym in dict.fromkeys([*open_scalps, *strategies]):
            bars = f.data.intraday_bars(sym, now.date(), f.cfg.scalper.bar_minutes)
            if not bars or self._seen.get(sym) == bars[-1].ts:
                continue
            self._seen[sym] = bars[-1].ts
            last = bars[-1]
            pos = open_scalps.get(sym)
            if pos:
                self._manage(pos, last)
            elif plan and sym not in plan["entered"] and last.ts.time() <= NO_ENTRY_AFTER:
                self._try_enter(sym, strategies[sym], bars, plan)

    def _manage(self, pos: dict, bar: Bar) -> None:
        price = reason = None
        if pos["stop_price"] and bar.low <= pos["stop_price"]:
            price, reason = min(bar.open, pos["stop_price"]), "stop"
        elif pos["target_price"] and bar.high >= pos["target_price"]:
            price, reason = max(bar.open, pos["target_price"]), "target"
        elif bar.ts.time() >= FLATTEN_AT:
            price, reason = bar.close, "eod_flatten"
        if reason:
            self.f.broker.sell(pos["symbol"], pos["qty"], price, desk="scalp", reason=reason, bar_volume=bar.volume)

    def _try_enter(self, sym: str, strategy: str, bars: list[Bar], plan: dict) -> None:
        f = self.f
        if f.risk.entries_blocked():
            return
        signal = self.orb_signal(bars) if strategy == "orb" else self.vwap_signal(bars)
        if not signal:
            return
        entry, stop, target = signal
        plan["entered"].append(sym)  # 종목당 하루 한 번만 진입
        f.store.set_state("scalp_plan", plan)
        snap = f.portfolio.snapshot()
        f.risk.update_limits(snap["equity"])
        sector = f.data.profile(sym).sector
        res = f.risk.check_buy(symbol=sym, price=entry, stop_price=stop, desk="scalp", sector=sector, snapshot=snap)
        if not res.allowed:
            f.broker.reject(sym, "buy", 0, entry, desk="scalp", reason=res.reason)
            f.risk.event("order_blocked", f"{sym} 스캘핑 진입 차단: {res.reason}", sym)
            return
        f.broker.buy(sym, res.qty, entry, desk="scalp", stop_price=stop, target_price=target, sector=sector,
                     bar_volume=bars[-1].volume, note=f"{strategy} 신호")

    def flatten(self, reason: str = "eod_flatten") -> None:
        """남아 있는 스캘핑 포지션을 현재가로 모두 청산한다."""
        for p in self.f.store.query("SELECT * FROM positions WHERE desk='scalp'"):
            price = self.f.data.quote(p["symbol"])
            if price:
                self.f.broker.sell(p["symbol"], p["qty"], price, desk="scalp", reason=reason)

    def run_day(self, day: date) -> None:
        """시뮬레이션: 하루치 5분봉을 차례로 재생한다."""
        clock = self.f.clock
        assert isinstance(clock, SimClock)
        if not self.plan() and not self.f.store.scalar("SELECT COUNT(*) FROM positions WHERE desk='scalp'", default=0):
            return
        self._seen.clear()
        step = self.f.cfg.scalper.bar_minutes
        t = session_open(day) + timedelta(minutes=step)
        end = session_open(day) + timedelta(minutes=390)
        while t <= end:
            clock.set(t)
            self.on_bar()
            t += timedelta(minutes=step)
        self.flatten()
