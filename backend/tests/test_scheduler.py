"""실시간 스케줄러: 하루 동안의 단계가 순서대로 한 번씩만 실행되는지 SimClock 으로 확인한다."""
from datetime import date, time, timedelta

from app.clock import SimClock, at
from app.config import Config
from app.fund import Fund
from app.llm.mock_llm import MockLLM
from app.scheduler import Scheduler
from app.store import Store

from .conftest import PROVIDER

DAY = date(2024, 3, 12)


def test_full_trading_day_and_restart_safety():
    clock = SimClock(at(DAY, time(8, 0)))
    fund = Fund(Config(), Store(":memory:"), PROVIDER, MockLLM(), clock)  # 실시간 모드(historical=False)
    sched = Scheduler(fund)
    sched.tick()
    assert fund.store.scalar("SELECT COUNT(*) FROM meetings") == 0           # 장전 회의 시각 전

    clock.set(at(DAY, time(9, 0)))
    sched.tick()
    assert fund.store.scalar("SELECT COUNT(*) FROM meetings WHERE kind='premarket'") == 1
    pending = fund.store.scalar("SELECT COUNT(*) FROM decisions WHERE status='pending'")
    assert pending > 0 and fund.store.scalar("SELECT COUNT(*) FROM orders") == 0  # 개장 전에는 주문 없음

    t = at(DAY, time(9, 30))
    while t <= at(DAY, time(16, 5)):
        clock.set(t)
        sched.tick()
        if t.time() == time(9, 30):
            assert fund.store.scalar("SELECT COUNT(*) FROM decisions WHERE status='pending'") == 0
            assert fund.store.scalar("SELECT COUNT(*) FROM orders WHERE desk='swing' AND status='filled'") > 0
        if t.time() == time(15, 55):
            assert fund.store.scalar("SELECT COUNT(*) FROM positions WHERE desk='scalp'") == 0  # 스캘핑 전량 청산
        t += timedelta(minutes=5)

    assert fund.store.scalar("SELECT COUNT(*) FROM meetings WHERE kind='premarket'") == 1
    assert fund.store.scalar("SELECT COUNT(*) FROM equity") > 50                 # 5분마다 평가액 기록
    state = fund.store.get_state("scheduler")
    assert state["premarket"] and state["opened"] and state["eod"] and state["flattened"]

    # 서버가 재시작돼도(새 Scheduler) 같은 날의 단계를 반복하지 않는다
    orders = fund.store.scalar("SELECT COUNT(*) FROM orders")
    Scheduler(fund).tick()
    assert fund.store.scalar("SELECT COUNT(*) FROM meetings WHERE kind='premarket'") == 1
    assert fund.store.scalar("SELECT COUNT(*) FROM orders") == orders

    clock.set(at(date(2024, 3, 16), time(10, 0)))  # 토요일
    Scheduler(fund).tick()
    assert fund.store.scalar("SELECT COUNT(*) FROM meetings") == fund.store.scalar("SELECT COUNT(*) FROM meetings WHERE ts < '2024-03-13'")


def test_paused_scheduler_does_nothing():
    clock = SimClock(at(DAY, time(9, 0)))
    fund = Fund(Config(), Store(":memory:"), PROVIDER, MockLLM(), clock)
    sched = Scheduler(fund)
    sched.set_paused(True)
    sched.tick()
    assert fund.store.scalar("SELECT COUNT(*) FROM meetings") == 0
