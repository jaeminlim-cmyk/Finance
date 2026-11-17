"""유니버스 스크리닝. 오늘 리서치할 종목을 코드로 고른다(LLM 호출 없음)."""

from __future__ import annotations

from statistics import fmean

from ..config import Config
from . import indicators as ind
from .base import MarketData


def screen(data: MarketData, cfg: Config, exclude: set[str] = frozenset()) -> list[dict]:
    """유동성 필터를 통과한 종목을 '오늘 볼 만한 정도' 점수 순으로 돌려준다.

    점수는 방향을 가리지 않는다. 움직임·거래량·뉴스가 있는 종목을 회의에 올리는 것이 목적이고,
    사야 할지 말지는 리서치와 토론에서 정한다.
    """
    symbols = list(dict.fromkeys([*cfg.universe.watchlist, *(cfg.universe.symbols or data.universe())]))
    watch = set(cfg.universe.watchlist)
    rows = []
    for sym in symbols:
        if sym in exclude:
            continue
        bars = data.daily_bars(sym, 80)
        if len(bars) < 60:
            continue
        closes = [b.close for b in bars]
        last = bars[-1]
        dollar_vol = fmean(b.close * b.volume for b in bars[-20:])
        if last.close < cfg.risk.min_price or dollar_vol < cfg.risk.min_avg_dollar_volume:
            continue
        vol20 = fmean(b.volume for b in bars[-21:-1]) or 1.0
        a = ind.atr(bars) or 0.0
        mom5 = ind.pct_change(closes, 5) or 0.0
        mom20 = ind.pct_change(closes, 20) or 0.0
        rel_vol = last.volume / vol20
        atr_pct = a / last.close if last.close else 0.0
        news_n = len(data.news(sym, 5))
        # ATR 로 정규화한 움직임 + 거래량 급증 + 뉴스 건수
        score = (abs(mom5) + 0.5 * abs(mom20)) / (atr_pct or 0.02) * 0.25 + max(0.0, rel_vol - 1) + 0.3 * news_n
        if sym in watch:
            score += 100.0  # 관심종목은 항상 포함
        rows.append({
            "symbol": sym, "score": round(score, 3), "price": last.close,
            "return_5d": round(mom5, 4), "return_20d": round(mom20, 4),
            "relative_volume": round(rel_vol, 2), "atr_pct": round(atr_pct, 4),
            "avg_dollar_volume": round(dollar_vol), "news_count": news_n,
            "gap_today_pct": round(last.open / bars[-2].close - 1, 4),
        })
    rows.sort(key=lambda x: x["score"], reverse=True)
    return rows
