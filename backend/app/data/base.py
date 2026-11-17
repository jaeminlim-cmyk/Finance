"""데이터 공급자 인터페이스와 시점 강제(point-in-time) 래퍼.

에이전트와 엔진은 항상 `MarketData` 를 통해서만 데이터를 읽는다. `MarketData` 는 현재 시계 시각(as_of)을
공급자에 넘기고, 돌아온 모든 항목의 시각이 as_of 이하인지 다시 검사한다. 하나라도 미래 데이터가 섞이면
`LookaheadError` 로 즉시 중단한다 — 백테스트 결과가 조용히 오염되는 것보다 멈추는 편이 낫다.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date, datetime

from ..clock import Clock
from ..models import Bar, CommunitySnapshot, NewsItem, Profile


class LookaheadError(RuntimeError):
    pass


class DataProvider(ABC):
    name = "base"
    #: 과거 시점 조회를 지원하지 않는 항목. 백테스트에서는 해당 항목을 '데이터 없음'으로 돌려준다.
    history_unsupported: frozenset[str] = frozenset()

    @abstractmethod
    def daily_bars(self, symbol: str, as_of: datetime, lookback: int) -> list[Bar]: ...

    @abstractmethod
    def intraday_bars(self, symbol: str, day: date, as_of: datetime, minutes: int) -> list[Bar]: ...

    @abstractmethod
    def quote(self, symbol: str, as_of: datetime) -> float | None: ...

    @abstractmethod
    def profile(self, symbol: str) -> Profile: ...

    def fundamentals(self, symbol: str, as_of: datetime) -> dict | None:
        return None

    def news(self, symbol: str, as_of: datetime, limit: int = 8) -> list[NewsItem]:
        return []

    def community(self, symbol: str, as_of: datetime) -> CommunitySnapshot | None:
        return None

    def next_earnings(self, symbol: str, as_of: datetime) -> date | None:
        return None

    def macro(self, as_of: datetime) -> dict:
        return {}

    def universe(self) -> list[str]:
        return []


class MarketData:
    """시계에 묶인 데이터 접근 창구."""

    def __init__(self, provider: DataProvider, clock: Clock, *, historical: bool = False):
        self.provider = provider
        self.clock = clock
        self.historical = historical  # True 면 과거 시점 조회 미지원 항목을 차단한다

    @property
    def as_of(self) -> datetime:
        return self.clock.now()

    def _blocked(self, item: str) -> bool:
        return self.historical and item in self.provider.history_unsupported

    def _check(self, items, what: str):
        now = self.as_of
        for it in items:
            if it.ts > now:
                raise LookaheadError(f"{what}: {it.ts.isoformat()} 는 현재 시각 {now.isoformat()} 이후의 데이터입니다.")
        return items

    def daily_bars(self, symbol: str, lookback: int = 260) -> list[Bar]:
        return self._check(self.provider.daily_bars(symbol, self.as_of, lookback), f"{symbol} 일봉")

    def intraday_bars(self, symbol: str, day: date | None = None, minutes: int = 5) -> list[Bar]:
        day = day or self.as_of.date()
        return self._check(self.provider.intraday_bars(symbol, day, self.as_of, minutes), f"{symbol} 분봉")

    def quote(self, symbol: str) -> float | None:
        return self.provider.quote(symbol, self.as_of)

    def profile(self, symbol: str) -> Profile:
        return self.provider.profile(symbol)

    def fundamentals(self, symbol: str) -> dict | None:
        if self._blocked("fundamentals"):
            return None
        f = self.provider.fundamentals(symbol, self.as_of)
        if f and f.get("reported_at") and f["reported_at"] > self.as_of.date().isoformat():
            raise LookaheadError(f"{symbol} 재무: 발표일 {f['reported_at']} 이 현재 시각 이후입니다.")
        return f

    def news(self, symbol: str, limit: int = 8) -> list[NewsItem]:
        if self._blocked("news"):
            return []
        return self._check(self.provider.news(symbol, self.as_of, limit), f"{symbol} 뉴스")

    def community(self, symbol: str) -> CommunitySnapshot | None:
        if self._blocked("community"):
            return None
        snap = self.provider.community(symbol, self.as_of)
        if snap:
            self._check([snap, *snap.posts], f"{symbol} 커뮤니티")
        return snap

    def next_earnings(self, symbol: str) -> date | None:
        if self._blocked("earnings"):
            return None
        return self.provider.next_earnings(symbol, self.as_of)

    def macro(self) -> dict:
        return self.provider.macro(self.as_of)

    def universe(self) -> list[str]:
        return self.provider.universe()
