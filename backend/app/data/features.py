"""에이전트에 넘길 입력 데이터 묶음을 만든다.

에이전트는 여기서 계산된 숫자만 본다. 값이 없으면 None 으로 두어 '데이터 없음'으로 보고하게 한다.
"""

from __future__ import annotations

from statistics import fmean

from ..clock import add_trading_days
from ..models import Bar, to_dict
from . import indicators as ind
from .base import MarketData

r = ind.r


def chart_features(bars: list[Bar]) -> dict | None:
    """가격 구조: 추세, 지지/저항, 거래량, 최근 봉."""
    if len(bars) < 60:
        return None
    closes = [b.close for b in bars]
    last = bars[-1]
    s20, s50, s200 = ind.sma(closes, 20), ind.sma(closes, 50), ind.sma(closes, 200)
    year = bars[-252:]
    weekly = closes[::-5][::-1]
    vol20 = fmean(b.volume for b in bars[-21:-1])
    if s200 is None:
        trend = "상승" if last.close > s20 > s50 else "하락" if last.close < s20 < s50 else "혼조"
    else:
        trend = "상승" if s20 > s50 > s200 else "하락" if s20 < s50 < s200 else "혼조"
    return {
        "last_close": last.close,
        "return_1d": r(ind.pct_change(closes, 1), 4),
        "return_5d": r(ind.pct_change(closes, 5), 4),
        "return_20d": r(ind.pct_change(closes, 20), 4),
        "return_60d": r(ind.pct_change(closes, 60), 4),
        "ma_alignment": trend,
        "weekly_trend_8w": r(weekly[-1] / weekly[-9] - 1, 4) if len(weekly) >= 9 else None,
        "high_52w": max(b.high for b in year),
        "low_52w": min(b.low for b in year),
        "pct_from_52w_high": r(last.close / max(b.high for b in year) - 1, 4),
        "resistance_20d": max(b.high for b in bars[-21:-1]),
        "support_20d": min(b.low for b in bars[-21:-1]),
        "resistance_60d": max(b.high for b in bars[-61:-1]),
        "support_60d": min(b.low for b in bars[-61:-1]),
        "breakout_20d": last.close > max(b.high for b in bars[-21:-1]),
        "breakdown_20d": last.close < min(b.low for b in bars[-21:-1]),
        "volume_ratio_vs_20d": r(last.volume / vol20, 2) if vol20 else None,
        "gap_today_pct": r(last.open / bars[-2].close - 1, 4),
        "recent_bars": [
            {"date": b.ts.date().isoformat(), "open": b.open, "high": b.high, "low": b.low, "close": b.close,
             "volume": b.volume} for b in bars[-5:]
        ],
    }


def technical_features(bars: list[Bar], bench: list[Bar]) -> dict | None:
    """지표 값. 해석은 기술적 분석가가 한다."""
    if len(bars) < 60:
        return None
    closes = [b.close for b in bars]
    last = closes[-1]
    m = ind.macd(closes)
    bb = ind.bollinger(closes)
    a = ind.atr(bars)
    rs = None
    if len(bench) >= 61:
        b_ret = bench[-1].close / bench[-61].close - 1
        rs = (ind.pct_change(closes, 60) or 0) - b_ret
    s20, s50, s200 = ind.sma(closes, 20), ind.sma(closes, 50), ind.sma(closes, 200)
    return {
        "last_close": last,
        "sma20": r(s20), "sma50": r(s50), "sma200": r(s200),
        "price_vs_sma20": r(last / s20 - 1, 4) if s20 else None,
        "price_vs_sma50": r(last / s50 - 1, 4) if s50 else None,
        "price_vs_sma200": r(last / s200 - 1, 4) if s200 else None,
        "rsi14": r(ind.rsi(closes), 1),
        "macd": r(m[0], 3) if m else None,
        "macd_signal": r(m[1], 3) if m else None,
        "macd_hist": r(m[2], 3) if m else None,
        "bollinger_upper": r(bb[0]) if bb else None,
        "bollinger_lower": r(bb[2]) if bb else None,
        "bollinger_pct_b": r((last - bb[2]) / (bb[0] - bb[2]), 2) if bb and bb[0] != bb[2] else None,
        "atr14": r(a),
        "atr_pct": r(a / last, 4) if a else None,
        "realized_vol_20d": r(ind.realized_vol(closes), 3),
        "relative_strength_60d_vs_benchmark": r(rs, 4),
    }


def symbol_pack(data: MarketData, symbol: str, benchmark: str) -> dict:
    """한 종목에 대해 리서치 본부가 쓰는 모든 입력."""
    bars = data.daily_bars(symbol, 260)
    bench = data.daily_bars(benchmark, 80)
    profile = data.profile(symbol)
    news = data.news(symbol)
    comm = data.community(symbol)
    earnings = data.next_earnings(symbol)
    today = data.as_of.date()
    pack = {
        "symbol": symbol,
        "name": profile.name,
        "sector": profile.sector,
        "as_of": data.as_of.isoformat(timespec="minutes"),
        "price": data.quote(symbol),
        "chart": chart_features(bars),
        "indicators": technical_features(bars, bench),
        "fundamentals": data.fundamentals(symbol),
        "news": [to_dict(n) for n in news],
        "community": None,
        "next_earnings_date": earnings.isoformat() if earnings else None,
        "earnings_within_days": None,
    }
    if earnings:
        n, d = 0, today
        while d < earnings and n < 60:
            d = add_trading_days(d, 1)
            n += 1
        pack["earnings_within_days"] = n
    if comm:
        pack["community"] = {
            "mentions_24h": comm.mentions_24h,
            "mentions_avg_7d": comm.mentions_avg_7d,
            "mention_ratio": r(comm.mentions_24h / comm.mentions_avg_7d, 2) if comm.mentions_avg_7d else None,
            "bullish_ratio": comm.bullish_ratio,
            "posts": [to_dict(p) for p in comm.posts],
        }
    return pack


def atr_of(pack: dict) -> float | None:
    return (pack.get("indicators") or {}).get("atr14")
