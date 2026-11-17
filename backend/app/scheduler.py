"""실시간 운용 스케줄러 (미국 동부시간 기준).

    장 시작 전   : 전체 회의 → 결정은 대기 상태로 기록
    09:30        : 대기 중인 결정을 하드 리스크 규칙에 통과시켜 주문
    장중 매 틱   : 손절·목표가 감시, 스캘핑 엔진 5분봉 처리, 5분마다 평가액 기록
    장중 N분마다 : 보유 종목 점검 — 크게 움직였거나 새 뉴스가 나온 종목만 LLM 재검토 (비용 절감)
    15:55        : 스캘핑 포지션 전량 청산
    16:00 이후   : 평가액 기록, 적중률 채점, 청산 거래 복기

진행 상황은 DB 에 저장하므로 서버를 재시작해도 같은 날의 단계를 반복하지 않는다.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, time, timedelta

from .clock import CLOSE, OPEN, is_trading_day, parse_iso
from .fund import Fund
from .scalper import FLATTEN_AT

log = logging.getLogger("fund.scheduler")

INTRADAY_MOVE_TRIGGER = 0.03  # 전일 종가 대비 이만큼 움직인 보유 종목은 장중 재검토 대상


class Scheduler:
    def __init__(self, fund: Fund, tick_seconds: float = 20.0):
        self.f = fund
        self.tick_seconds = tick_seconds
        self.busy = threading.Lock()  # 회의·시뮬레이션과 겹치지 않게 한다
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_error: str | None = None
        self.last_tick: datetime | None = None

    # ── 상태 ───────────────────────────────────────────────
    def _state(self, now: datetime) -> dict:
        s = self.f.store.get_state("scheduler") or {}
        if s.get("day") != now.date().isoformat():
            s = {"day": now.date().isoformat()}
        return s

    def _save(self, s: dict) -> None:
        self.f.store.set_state("scheduler", s)

    @property
    def paused(self) -> bool:
        return bool(self.f.store.get_state("scheduler_paused", False))

    def set_paused(self, on: bool) -> None:
        self.f.store.set_state("scheduler_paused", on)

    # ── 한 틱 ──────────────────────────────────────────────
    def tick(self) -> None:
        f, now = self.f, self.f.clock.now()
        self.last_tick = now
        if self.paused or not is_trading_day(now.date()):
            return
        s, t = self._state(now), now.time()
        h, m = map(int, f.cfg.schedule.premarket.split(":"))
        if time(h, m) <= t < CLOSE and not s.get("premarket"):
            s["premarket"] = True
            self._save(s)
            f.run_meeting("premarket")
            return
        if OPEN <= t < CLOSE:
            if not s.get("opened"):
                s["opened"] = True
                self._save(s)
                f.risk.update_limits(f.portfolio.snapshot()["equity"])
                f.execute_pending()
            f.check_exits_live()
            if f.cfg.scalper.enabled:
                f.scalper.on_bar()
                if t >= FLATTEN_AT and not s.get("flattened"):
                    s["flattened"] = True
                    self._save(s)
                    f.scalper.flatten()
            last_mark = parse_iso(s["last_mark"]) if s.get("last_mark") else None
            if not last_mark or now - last_mark >= timedelta(minutes=5):
                s["last_mark"] = now.isoformat()
                self._save(s)
                f.mark()
            every = timedelta(minutes=f.cfg.schedule.intraday_review_minutes)
            last_review = parse_iso(s["last_review"]) if s.get("last_review") else now.replace(hour=9, minute=30, second=0)
            if now - last_review >= every:
                s["last_review"] = now.isoformat()
                self._save(s)
                self._intraday_review(last_review)
        elif t >= CLOSE and s.get("opened") and not s.get("eod"):
            s["eod"] = True
            self._save(s)
            f.mark()
            f.end_of_day()

    def _intraday_review(self, since: datetime) -> None:
        f, triggered = self.f, []
        for p in f.store.query("SELECT symbol FROM positions WHERE desk='swing'"):
            sym = p["symbol"]
            bars, price = f.data.daily_bars(sym, 1), f.data.quote(sym)
            moved = bool(bars and price and abs(price / bars[-1].close - 1) >= INTRADAY_MOVE_TRIGGER)
            fresh_news = any(n.ts > since for n in f.data.news(sym, 5))
            if moved or fresh_news:
                triggered.append(sym)
        if triggered:
            f.run_meeting("intraday", only=triggered)

    # ── 스레드 ─────────────────────────────────────────────
    def _loop(self) -> None:
        while not self._stop.is_set():
            if self.busy.acquire(blocking=False):
                try:
                    self.tick()
                    self.last_error = None
                except Exception as e:  # 한 틱의 실패로 감시가 멈추면 안 된다
                    log.exception("스케줄러 틱 실패")
                    self.last_error = f"{type(e).__name__}: {e}"
                finally:
                    self.busy.release()
            self._stop.wait(self.tick_seconds)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
