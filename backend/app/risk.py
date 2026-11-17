"""하드 리스크 규칙. 주문 직전에 코드가 검사하며 어떤 에이전트의 결정도 이 한도를 넘을 수 없다."""

from __future__ import annotations

from dataclasses import dataclass, field
from math import floor

from .clock import Clock, is_market_open, iso
from .config import Config
from .store import Store


@dataclass
class RiskResult:
    allowed: bool
    qty: int = 0
    blocks: list[str] = field(default_factory=list)  # 차단 사유
    adjustments: list[str] = field(default_factory=list)  # 수량을 줄인 사유

    @property
    def reason(self) -> str:
        return "; ".join(self.blocks or self.adjustments)


class RiskEngine:
    def __init__(self, store: Store, cfg: Config, clock: Clock):
        self.store, self.cfg, self.clock = store, cfg, clock

    # ── 상태 ───────────────────────────────────────────────
    @property
    def kill_switch(self) -> bool:
        return bool(self.store.get_state("kill_switch", False))

    @property
    def halted(self) -> bool:
        return bool(self.store.get_state("halted", False))

    def daily_stop_active(self) -> bool:
        return self.store.get_state("daily_stop_day") == self.clock.now().date().isoformat()

    def entries_blocked(self) -> str | None:
        """신규 진입이 막혀 있으면 그 사유."""
        if self.kill_switch:
            return "킬 스위치 작동 중"
        if self.halted:
            return f"낙폭 한도 도달로 전체 중단 ({self.store.get_state('halt_reason', '')}) — 사람의 확인 필요"
        if self.daily_stop_active():
            return "일일 손실 한도 도달로 당일 신규 진입 중단"
        return None

    def event(self, kind: str, detail: str, symbol: str | None = None) -> None:
        ts = iso(self.clock.now())
        self.store.insert("risk_events", ts=ts, kind=kind, symbol=symbol, detail=detail)
        self.store.audit(ts, "risk_engine", kind, {"symbol": symbol, "detail": detail})
        self.store.emit("risk", {"kind": kind})

    def set_kill_switch(self, on: bool, actor: str = "user") -> None:
        self.store.set_state("kill_switch", on)
        self.event("kill_switch_on" if on else "kill_switch_off", f"{actor} 가 킬 스위치를 {'켰습니다' if on else '껐습니다'}")

    def resume(self, actor: str = "user") -> None:
        """낙폭 한도로 멈춘 뒤 사람이 확인하고 재개. 고점을 현재 자산으로 다시 잡는다."""
        self.store.set_state("halted", False)
        self.store.set_state("peak_equity", None)
        self.event("resume", f"{actor} 가 낙폭 중단 상태를 해제했습니다")

    # ── 계좌 한도 감시 ─────────────────────────────────────
    def update_limits(self, equity: float) -> dict:
        """평가액이 바뀔 때마다 호출. 일일 손실·낙폭 한도를 검사한다."""
        today = self.clock.now().date().isoformat()
        start = self.store.get_state("day_start")
        if not start or start["day"] != today:
            start = {"day": today, "equity": equity}
            self.store.set_state("day_start", start)
        peak = self.store.get_state("peak_equity")
        if peak is None or equity > peak:
            peak = equity
            self.store.set_state("peak_equity", peak)
        day_pnl = equity / start["equity"] - 1 if start["equity"] else 0.0
        drawdown = equity / peak - 1 if peak else 0.0
        r = self.cfg.risk
        if day_pnl <= -r.daily_loss_limit_pct and not self.daily_stop_active():
            self.store.set_state("daily_stop_day", today)
            self.event("daily_loss_limit", f"당일 손익 {day_pnl:.2%} — 한도 -{r.daily_loss_limit_pct:.0%} 도달, 신규 진입 중단")
        if drawdown <= -r.max_drawdown_pct and not self.halted:
            self.store.set_state("halted", True)
            self.store.set_state("halt_reason", f"고점 대비 {drawdown:.2%}")
            self.event("max_drawdown", f"고점 대비 낙폭 {drawdown:.2%} — 한도 -{r.max_drawdown_pct:.0%} 도달, 전체 매매 중단")
        return {"day_pnl_pct": day_pnl, "drawdown_pct": drawdown, "peak_equity": peak, "day_start_equity": start["equity"]}

    # ── 포지션 크기 ────────────────────────────────────────
    def default_stop(self, price: float, atr: float | None) -> float:
        dist = (atr or price * 0.03) * self.cfg.risk.stop_atr_multiple
        return round(price - dist, 2)

    def default_target(self, price: float, stop: float) -> float:
        return round(price + (price - stop) * self.cfg.risk.target_r_multiple, 2)

    def sanitize_stop(self, price: float, stop: float | None, atr: float | None) -> float:
        """에이전트가 제안한 손절가가 비상식적이면 ATR 기준 기본값으로 바꾼다."""
        base = atr or price * 0.03
        if stop is None or stop >= price or stop <= 0:
            return self.default_stop(price, atr)
        dist = price - stop
        if dist < 0.5 * base or dist > 4 * base:
            return self.default_stop(price, atr)
        return round(stop, 2)

    # ── 주문 검사 ──────────────────────────────────────────
    def check_buy(self, *, symbol: str, price: float, stop_price: float | None, desk: str, sector: str | None,
                  snapshot: dict, size_multiplier: float = 1.0, avg_dollar_volume: float | None = None,
                  earnings_within_days: int | None = None, enforce_hours: bool = True) -> RiskResult:
        r, res = self.cfg.risk, RiskResult(allowed=False)
        equity = snapshot["equity"]
        if self.cfg.trading_mode != "paper":
            res.blocks.append("모의투자 모드가 아님")
        blocked = self.entries_blocked()
        if blocked:
            res.blocks.append(blocked)
        if enforce_hours and not is_market_open(self.clock.now()):
            res.blocks.append("정규장 시간이 아님")
        if price is None or price <= 0:
            res.blocks.append("현재가 없음")
            return res
        if price < r.min_price:
            res.blocks.append(f"주가 ${price:.2f} 가 최소 기준 ${r.min_price:.2f} 미만")
        if avg_dollar_volume is not None and avg_dollar_volume < r.min_avg_dollar_volume:
            res.blocks.append("평균 거래대금이 유동성 기준 미만")
        if stop_price is None or stop_price >= price or stop_price <= 0:
            res.blocks.append("유효한 손절가 없음")
        if desk == "swing" and earnings_within_days is not None and earnings_within_days <= r.earnings_blackout_days:
            res.blocks.append(f"실적 발표 {earnings_within_days}거래일 전 — 신규 진입 제한")
        if res.blocks:
            return res

        risk_pct = self.cfg.scalper.risk_per_trade_pct if desk == "scalp" else r.max_risk_per_trade_pct
        mult = min(1.0, max(0.0, size_multiplier))
        caps: list[tuple[str, float]] = [
            ("거래당 손실 위험 한도", equity * risk_pct * mult / (price - stop_price)),
        ]
        held = sum(p["market_value"] for p in snapshot["positions"] if p["symbol"] == symbol)
        caps.append(("종목 비중 한도", (equity * r.max_position_pct - held) / price))
        if sector:
            caps.append(("섹터 비중 한도", (equity * r.max_sector_pct - snapshot["sector_value"].get(sector, 0.0)) / price))
        caps.append(("최소 현금 비중", (snapshot["cash"] - equity * r.min_cash_pct) / price))
        if desk == "scalp":
            caps.append(("스캘핑 자본 한도", (equity * self.cfg.scalper.capital_pct - snapshot["scalp_value"]) / price))
        name, limit = min(caps, key=lambda c: c[1])
        qty = floor(limit)
        if qty < 1:
            res.blocks.append(f"{name}에 걸려 매수 가능 수량 없음")
            return res
        if name != caps[0][0]:
            res.adjustments.append(f"{name}로 수량 {floor(caps[0][1])} → {qty} 축소")
        res.allowed, res.qty = True, qty
        return res

    def check_sell(self, *, held_qty: int, qty: int, protective: bool, enforce_hours: bool = True) -> RiskResult:
        """매도는 위험을 줄이는 방향이므로 중단 상태에서도 손절·청산은 허용한다. 공매도만 막는다."""
        res = RiskResult(allowed=False)
        if held_qty <= 0:
            res.blocks.append("보유 수량 없음 (공매도 불가)")
        if self.kill_switch and not protective:
            res.blocks.append("킬 스위치 작동 중 — 손절·강제 청산 외 매도 중단")
        if enforce_hours and not is_market_open(self.clock.now()):
            res.blocks.append("정규장 시간이 아님")
        if res.blocks:
            return res
        res.allowed, res.qty = True, min(qty, held_qty)
        return res

    # ── 대시보드용 요약 ────────────────────────────────────
    def exposure(self, snapshot: dict) -> dict:
        r, equity = self.cfg.risk, snapshot["equity"] or 1.0
        by_symbol: dict[str, float] = {}
        for p in snapshot["positions"]:
            by_symbol[p["symbol"]] = by_symbol.get(p["symbol"], 0.0) + p["market_value"]
        limits = self.update_limits(snapshot["equity"])
        open_risk = sum(max(0.0, (p["price"] - p["stop_price"]) * p["qty"]) for p in snapshot["positions"] if p["stop_price"])
        return {
            "limits": r.model_dump() | {"scalper_capital_pct": self.cfg.scalper.capital_pct},
            "kill_switch": self.kill_switch, "halted": self.halted, "halt_reason": self.store.get_state("halt_reason"),
            "daily_stop": self.daily_stop_active(), "entries_blocked": self.entries_blocked(),
            "day_pnl_pct": round(limits["day_pnl_pct"], 5), "drawdown_pct": round(limits["drawdown_pct"], 5),
            "cash_pct": snapshot["cash_pct"],
            "largest_position_pct": round(max(by_symbol.values(), default=0.0) / equity, 4),
            "positions_pct": {k: round(v / equity, 4) for k, v in sorted(by_symbol.items(), key=lambda x: -x[1])},
            "sector_pct": {k: round(v / equity, 4) for k, v in sorted(snapshot["sector_value"].items(), key=lambda x: -x[1])},
            "scalp_pct": round(snapshot["scalp_value"] / equity, 4),
            "open_risk_pct": round(open_risk / equity, 4),
        }
