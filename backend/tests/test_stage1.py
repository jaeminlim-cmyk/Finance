"""1단계: 달력, 지표, 시점 강제, 페이퍼 브로커, 하드 리스크 규칙."""
from datetime import date, time, timedelta

import pytest

from app.clock import add_trading_days, at, is_market_open, is_trading_day, last_completed_session, trading_days
from app.config import Config, LiveTradingDisabled, validate
from app.data import indicators as ind
from app.data.base import DataProvider, LookaheadError, MarketData
from app.models import Bar, NewsItem, Profile


# ── 달력 ──────────────────────────────────────────────────
def test_calendar_holidays_and_weekends():
    assert not is_trading_day(date(2024, 3, 29))   # 성금요일
    assert not is_trading_day(date(2024, 7, 4))
    assert not is_trading_day(date(2024, 11, 28))  # 추수감사절
    assert not is_trading_day(date(2024, 6, 19))   # 준틴스
    assert not is_trading_day(date(2023, 1, 2))    # 1/1 일요일 → 월요일 대체 휴장
    assert is_trading_day(date(2021, 12, 31))      # 1/1 토요일이어도 12/31 은 개장
    assert not is_trading_day(date(2024, 3, 9))    # 토요일
    assert len(trading_days(date(2024, 1, 1), date(2024, 12, 31))) == 252
    assert add_trading_days(date(2024, 3, 28), 1) == date(2024, 4, 1)


def test_market_hours():
    d = date(2024, 3, 12)
    assert is_market_open(at(d, time(9, 30)))
    assert not is_market_open(at(d, time(9, 29)))
    assert not is_market_open(at(d, time(16, 0)))
    assert last_completed_session(at(d, time(10, 0))) == date(2024, 3, 11)
    assert last_completed_session(at(d, time(16, 0))) == d


# ── 지표 ──────────────────────────────────────────────────
def test_indicators_known_values():
    up = [float(i) for i in range(1, 40)]
    assert ind.sma(up, 5) == 37.0
    assert ind.rsi(up) == 100.0
    assert ind.rsi(list(reversed(up))) == pytest.approx(0.0)
    flat = [10.0] * 40
    assert ind.macd(flat) == (0.0, 0.0, 0.0)
    upper, mid, lower = ind.bollinger(flat)
    assert upper == mid == lower == 10.0
    assert ind.sma(up, 100) is None
    bars = [Bar("X", at(date(2024, 1, 2), time(16)) + timedelta(days=i), 10, 11, 9, 10, 100) for i in range(20)]
    assert ind.atr(bars) == pytest.approx(2.0)
    assert ind.vwap(bars) == pytest.approx(10.0)


# ── 시점 강제 ─────────────────────────────────────────────
def test_mock_data_never_leaks_future(data, clock):
    for hhmm in (time(8, 0), time(9, 30), time(11, 15), time(16, 0), time(20, 0)):
        clock.set(at(date(2024, 3, 12), hhmm))
        now = clock.now()
        for sym in ("AAPL", "NVDA", "SPY"):
            daily = data.daily_bars(sym, 300)
            assert daily and all(b.ts <= now for b in daily)
            assert all(b.ts <= now for b in data.intraday_bars(sym))
            assert all(n.ts <= now for n in data.news(sym))
            f = data.fundamentals(sym)
            assert f is None or f["reported_at"] <= now.date().isoformat()
    # 장 시작 전에는 전일 종가, 장중에는 당일 봉, 마감 후에는 당일 종가
    clock.set(at(date(2024, 3, 12), time(8, 0)))
    prev_close = data.daily_bars("AAPL", 1)[-1]
    assert prev_close.ts.date() == date(2024, 3, 11) and data.quote("AAPL") == prev_close.close
    clock.set(at(date(2024, 3, 12), time(16, 0)))
    today = data.daily_bars("AAPL", 1)[-1]
    assert today.ts.date() == date(2024, 3, 12) and data.quote("AAPL") == today.close
    clock.set(at(date(2024, 3, 12), time(9, 30)))
    assert data.quote("AAPL") == today.open


def test_mock_is_deterministic_and_intraday_consistent(data, clock):
    from app.data.mock import MockProvider
    other = MockProvider()
    clock.set(at(date(2024, 3, 12), time(16, 0)))
    a = data.daily_bars("TSLA", 50)
    b = other.daily_bars("TSLA", clock.now(), 50)
    assert a == b
    day, intraday = a[-1], data.intraday_bars("TSLA")
    assert len(intraday) == 78
    assert intraday[0].open == day.open and intraday[-1].close == day.close
    assert max(x.high for x in intraday) == day.high and min(x.low for x in intraday) == day.low
    assert all(x.low <= min(x.open, x.close) and x.high >= max(x.open, x.close) for x in [*a, *intraday])


class LeakyProvider(DataProvider):
    """as_of 를 무시하고 미래 데이터를 돌려주는 잘못된 공급자."""
    history_unsupported = frozenset({"community"})

    def daily_bars(self, symbol, as_of, lookback):
        return [Bar(symbol, as_of + timedelta(days=1), 1, 1, 1, 1, 1)]

    def intraday_bars(self, symbol, day, as_of, minutes):
        return []

    def quote(self, symbol, as_of):
        return 1.0

    def profile(self, symbol):
        return Profile(symbol, symbol, "기타")

    def news(self, symbol, as_of, limit=8):
        return [NewsItem(as_of + timedelta(minutes=1), "미래 뉴스", "", "x")]

    def fundamentals(self, symbol, as_of):
        return {"reported_at": (as_of.date() + timedelta(days=3)).isoformat()}

    def community(self, symbol, as_of):
        raise AssertionError("과거 조회 미지원 항목은 호출되면 안 된다")


def test_point_in_time_guard_raises_on_lookahead(clock):
    md = MarketData(LeakyProvider(), clock, historical=True)
    with pytest.raises(LookaheadError):
        md.daily_bars("X")
    with pytest.raises(LookaheadError):
        md.news("X")
    with pytest.raises(LookaheadError):
        md.fundamentals("X")
    assert md.community("X") is None  # 백테스트에서는 '데이터 없음'


# ── 설정 ──────────────────────────────────────────────────
def test_live_trading_mode_is_refused():
    cfg = Config()
    cfg.trading_mode = "live"
    with pytest.raises(LiveTradingDisabled):
        validate(cfg)


# ── 페이퍼 브로커 ─────────────────────────────────────────
def test_broker_buy_sell_roundtrip_accounting(env):
    o = env.broker.buy("AAPL", 100, 200.0, desk="swing", stop_price=190, target_price=220, sector="정보기술")
    assert o["status"] == "filled" and o["fill_price"] == pytest.approx(200.10)  # 5bp 슬리피지
    assert o["commission"] == 1.0                                               # 최소 수수료
    assert env.portfolio.cash == pytest.approx(100_000 - 20_010 - 1.0)
    s = env.broker.sell("AAPL", 100, 210.0, desk="swing", reason="target")
    assert s["fill_price"] == pytest.approx(209.895)
    expected = (209.895 - 200.10) * 100 - 1.0 - 1.0
    assert s["pnl"] == pytest.approx(expected, abs=0.01)
    assert env.portfolio.cash == pytest.approx(100_000 + expected, abs=0.01)
    assert env.portfolio.positions() == []
    trade = env.store.one("SELECT * FROM trades")
    assert trade["pnl"] == pytest.approx(expected, abs=0.01) and trade["exit_reason"] == "target"


def test_broker_partial_fill_and_rejections(env):
    o = env.broker.buy("AAPL", 1000, 50.0, desk="swing", stop_price=48, target_price=55, sector="x", bar_volume=4000)
    assert o["status"] == "partial" and o["filled_qty"] == 200  # 봉 거래량의 5%
    assert env.broker.buy("MSFT", 10_000, 400.0, desk="swing", stop_price=390, target_price=None,
                          sector="x")["status"] == "rejected"   # 현금 부족
    assert env.broker.sell("NVDA", 10, 100.0, desk="swing", reason="x")["status"] == "rejected"  # 공매도 불가
    s = env.broker.sell("AAPL", 999, 51.0, desk="swing", reason="x")
    assert s["filled_qty"] == 200  # 보유 수량까지만
    assert env.store.scalar("SELECT COUNT(*) FROM audit") >= 4


# ── 하드 리스크 규칙 ──────────────────────────────────────
def buy_check(env, **kw):
    args = dict(symbol="AAPL", price=100.0, stop_price=95.0, desk="swing", sector="정보기술",
                snapshot=env.portfolio.snapshot())
    args.update(kw)
    return env.risk.check_buy(**args)


def test_risk_sizes_by_per_trade_risk_and_position_cap(env):
    # 1% 위험 = $1,000, 손절 폭 $5 → 200주 = $20,000 이지만 종목 한도 10% = $10,000 → 100주
    res = buy_check(env)
    assert res.allowed and res.qty == 100 and "종목 비중 한도" in res.reason
    # 손절 폭이 넓으면 거래당 위험 한도가 먼저 걸린다: $1,000 / $20 = 50주
    res = buy_check(env, stop_price=80.0)
    assert res.allowed and res.qty == 50 and not res.adjustments
    assert buy_check(env, stop_price=80.0, size_multiplier=0.5).qty == 25


def test_risk_blocks_missing_stop_penny_stock_earnings_and_hours(env):
    assert not buy_check(env, stop_price=None).allowed
    assert not buy_check(env, stop_price=101.0).allowed
    assert "최소 기준" in buy_check(env, price=3.0, stop_price=2.5).reason
    assert "실적 발표" in buy_check(env, earnings_within_days=1).reason
    assert buy_check(env, earnings_within_days=5).allowed
    assert "유동성" in buy_check(env, avg_dollar_volume=1e6).reason
    env.clock.set(at(date(2024, 3, 12), time(17, 0)))
    assert "정규장" in buy_check(env).reason
    env.clock.set(at(date(2024, 3, 9), time(11, 0)))  # 토요일
    assert "정규장" in buy_check(env).reason


def test_risk_sector_cash_and_scalp_caps(env):
    for sym in ("AAPL", "MSFT", "NVDA"):  # 정보기술 3종목 × 10% = 30%
        env.broker.buy(sym, 100, 100.0, desk="swing", stop_price=95, target_price=None, sector="정보기술")
    res = buy_check(env, symbol="AMD")
    assert not res.allowed and "섹터 비중 한도" in res.reason
    for i, sym in enumerate(("JPM", "XOM", "UNH", "WMT", "CAT", "META")):
        env.broker.buy(sym, 100, 100.0, desk="swing", stop_price=95, target_price=None, sector=f"섹터{i}")
    res = buy_check(env, symbol="KO", sector="필수소비재")  # 현금 약 10% 남음
    assert not res.allowed and "최소 현금 비중" in res.reason


def test_risk_scalp_capital_cap(env):
    res = buy_check(env, desk="scalp", price=100.0, stop_price=99.5, sector="정보기술")
    assert res.allowed and res.qty == 100  # 0.25% 위험이면 500주지만 스캘핑 자본 10%·종목 10% 한도
    env.broker.buy("AAPL", 100, 100.0, desk="scalp", stop_price=99.5, target_price=None, sector="정보기술")
    res = buy_check(env, symbol="TSLA", desk="scalp", price=100.0, stop_price=99.5, sector="임의소비재")
    assert not res.allowed and "스캘핑 자본 한도" in res.reason


def test_daily_loss_limit_drawdown_halt_and_kill_switch(env):
    env.risk.update_limits(100_000)
    env.risk.update_limits(96_900)  # -3.1%
    assert env.risk.daily_stop_active() and "일일 손실" in buy_check(env).reason
    env.clock.set(at(date(2024, 3, 13), time(10, 0)))  # 다음 날에는 풀린다
    env.risk.update_limits(96_900)
    assert buy_check(env).allowed
    env.risk.update_limits(84_000)  # 고점 대비 -16%
    assert env.risk.halted and "낙폭" in buy_check(env).reason
    env.clock.set(at(date(2024, 3, 14), time(10, 0)))
    assert not buy_check(env).allowed  # 날짜가 바뀌어도 사람이 풀기 전까지 유지
    env.risk.resume()
    env.risk.update_limits(84_000)
    assert buy_check(env).allowed
    env.risk.set_kill_switch(True)
    assert "킬 스위치" in buy_check(env).reason
    assert not env.risk.check_sell(held_qty=10, qty=10, protective=False).allowed
    assert env.risk.check_sell(held_qty=10, qty=10, protective=True).allowed  # 손절은 계속 동작
    assert not env.risk.check_sell(held_qty=0, qty=10, protective=True).allowed
    kinds = [e["kind"] for e in env.store.query("SELECT kind FROM risk_events ORDER BY id")]
    # 3/13 의 급락은 그날의 일일 손실 한도도 함께 건드린다
    assert kinds == ["daily_loss_limit", "daily_loss_limit", "max_drawdown", "resume", "kill_switch_on"]


def test_stop_sanitizing(env):
    assert env.risk.sanitize_stop(100, 96, atr=2.0) == 96
    assert env.risk.sanitize_stop(100, None, atr=2.0) == 96.0     # 기본값 2×ATR
    assert env.risk.sanitize_stop(100, 105, atr=2.0) == 96.0     # 진입가 위 손절 → 기본값
    assert env.risk.sanitize_stop(100, 99.9, atr=2.0) == 96.0    # 너무 좁음
    assert env.risk.sanitize_stop(100, 50, atr=2.0) == 96.0      # 너무 넓음
    assert env.risk.default_target(100, 96) == 108.0
