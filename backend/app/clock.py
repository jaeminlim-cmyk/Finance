"""시계와 NYSE 거래일 달력.

모든 시각은 미국 동부시간(ET) aware datetime 으로 다루고, 저장할 때만 UTC ISO 문자열로 바꾼다.
조기 폐장일(추수감사절 다음 날 등)은 반영하지 않는다.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
OPEN = time(9, 30)
CLOSE = time(16, 0)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    d = date(year, month, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def _last_weekday(year: int, month: int, weekday: int) -> date:
    d = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _easter(year: int) -> date:
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = (h + l - 7 * m + 114) % 31 + 1
    return date(year, month, day)


def _observed(d: date) -> date:
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


@lru_cache(maxsize=None)
def nyse_holidays(year: int) -> frozenset[date]:
    days = {
        _observed(date(year, 1, 1)),
        _nth_weekday(year, 1, 0, 3),  # 마틴 루터 킹
        _nth_weekday(year, 2, 0, 3),  # 대통령의 날
        _easter(year) - timedelta(days=2),  # 성금요일
        _last_weekday(year, 5, 0),  # 메모리얼 데이
        _observed(date(year, 7, 4)),
        _nth_weekday(year, 9, 0, 1),  # 노동절
        _nth_weekday(year, 11, 3, 4),  # 추수감사절
        _observed(date(year, 12, 25)),
    }
    if year >= 2022:
        days.add(_observed(date(year, 6, 19)))  # 준틴스
    # 1월 1일이 토요일이면 전년도 12월 31일에 쉬지 않는다(NYSE 규칙).
    if date(year, 1, 1).weekday() == 5:
        days.discard(date(year - 1, 12, 31))
        days.discard(_observed(date(year, 1, 1)))
    return frozenset(days)


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in nyse_holidays(d.year)


def next_trading_day(d: date) -> date:
    d += timedelta(days=1)
    while not is_trading_day(d):
        d += timedelta(days=1)
    return d


def prev_trading_day(d: date) -> date:
    d -= timedelta(days=1)
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d


def trading_days(start: date, end: date) -> list[date]:
    out, d = [], start
    while d <= end:
        if is_trading_day(d):
            out.append(d)
        d += timedelta(days=1)
    return out


def add_trading_days(d: date, n: int) -> date:
    step = next_trading_day if n >= 0 else prev_trading_day
    for _ in range(abs(n)):
        d = step(d)
    return d


def at(d: date, t: time) -> datetime:
    return datetime.combine(d, t, tzinfo=ET)


def session_open(d: date) -> datetime:
    return at(d, OPEN)


def session_close(d: date) -> datetime:
    return at(d, CLOSE)


def is_market_open(dt: datetime) -> bool:
    dt = dt.astimezone(ET)
    return is_trading_day(dt.date()) and OPEN <= dt.time() < CLOSE


def last_completed_session(dt: datetime) -> date:
    """dt 시점에 일봉이 확정된 가장 최근 거래일."""
    dt = dt.astimezone(ET)
    d = dt.date()
    if is_trading_day(d) and dt.time() >= CLOSE:
        return d
    return prev_trading_day(d)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s).astimezone(ET)


class Clock:
    def now(self) -> datetime:
        raise NotImplementedError


class RealClock(Clock):
    def now(self) -> datetime:
        return datetime.now(ET)


class SimClock(Clock):
    """백테스트·시뮬레이션용 시계. 엔진이 직접 시각을 옮긴다."""

    def __init__(self, start: datetime):
        self._now = start.astimezone(ET)

    def now(self) -> datetime:
        return self._now

    def set(self, dt: datetime) -> None:
        self._now = dt.astimezone(ET)
