from datetime import date, time

import pytest

from app.broker.paper import PaperBroker
from app.clock import SimClock, at
from app.config import Config
from app.data.base import MarketData
from app.data.mock import MockProvider
from app.portfolio import Portfolio
from app.risk import RiskEngine
from app.store import Store

PROVIDER = MockProvider()  # 시세 생성이 무거우므로 테스트 전체에서 공유


@pytest.fixture
def cfg():
    return Config()


@pytest.fixture
def clock():
    return SimClock(at(date(2024, 3, 12), time(10, 0)))  # 화요일 장중


@pytest.fixture
def store():
    s = Store(":memory:")
    yield s
    s.close()


@pytest.fixture
def data(clock):
    return MarketData(PROVIDER, clock, historical=True)


@pytest.fixture
def env(cfg, clock, store, data):
    class Env:
        pass
    e = Env()
    e.cfg, e.clock, e.store, e.data = cfg, clock, store, data
    e.portfolio = Portfolio(store, data, cfg)
    e.broker = PaperBroker(store, cfg, clock)
    e.risk = RiskEngine(store, cfg, clock)
    return e
