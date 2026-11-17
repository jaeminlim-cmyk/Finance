"""실데이터 공급자 (DATA_MODE=live).

- 시세·재무·실적일·뉴스: yfinance (키 불필요. 야후의 비공식 경로라 형식이 바뀌면 깨질 수 있다)
- 뉴스: FINNHUB_API_KEY 가 있으면 Finnhub 회사 뉴스를 우선 사용 (과거 날짜 조회 가능)
- 커뮤니티: StockTwits 공개 스트림, REDDIT_CLIENT_ID/SECRET 이 있으면 Reddit 검색 추가

과거 시점 조회가 안 되는 항목은 `history_unsupported` 에 올려 두었고, 백테스트에서는 '데이터 없음'으로 처리된다.
외부 호출이 실패하면 예외를 올리지 않고 빈 값을 돌려준다. 에이전트는 그 항목을 data_gaps 로 보고한다.
"""

from __future__ import annotations

import logging
import os
import time as _time
from datetime import date, datetime, timedelta, timezone

from ..clock import CLOSE, ET, OPEN, at, last_completed_session, session_close
from ..models import Bar, CommunityPost, CommunitySnapshot, NewsItem, Profile
from .base import DataProvider

log = logging.getLogger("fund.data.live")

LIVE_UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "AMD", "AVGO", "CRM", "ORCL", "ADBE", "INTC", "QCOM", "MU", "PLTR", "CSCO", "IBM",
    "GOOGL", "META", "NFLX", "DIS", "TMUS", "AMZN", "TSLA", "HD", "NKE", "MCD", "SBUX", "LOW", "BKNG",
    "WMT", "KO", "PEP", "COST", "PG", "JPM", "BAC", "GS", "MS", "V", "MA", "WFC", "C", "COIN", "BRK-B",
    "UNH", "LLY", "JNJ", "PFE", "MRK", "ABBV", "TMO", "XOM", "CVX", "COP", "CAT", "BA", "GE", "HON", "UPS", "UBER",
]
SECTOR_KO = {
    "Technology": "정보기술", "Communication Services": "커뮤니케이션", "Consumer Cyclical": "임의소비재",
    "Consumer Defensive": "필수소비재", "Financial Services": "금융", "Healthcare": "헬스케어", "Energy": "에너지",
    "Industrials": "산업재", "Basic Materials": "소재", "Real Estate": "부동산", "Utilities": "유틸리티",
}
SECTOR_ETFS = {"정보기술": "XLK", "커뮤니케이션": "XLC", "임의소비재": "XLY", "필수소비재": "XLP", "금융": "XLF",
               "헬스케어": "XLV", "에너지": "XLE", "산업재": "XLI"}
UA = {"User-Agent": "ai-hedge-fund-paper/0.1"}


class _Cache:
    def __init__(self):
        self._d: dict = {}

    def get(self, key, ttl: float, fn):
        hit = self._d.get(key)
        if hit and _time.time() - hit[0] < ttl:
            return hit[1]
        try:
            value = fn()
        except Exception as e:  # 외부 데이터 실패는 '데이터 없음'으로 흡수한다
            log.warning("데이터 조회 실패 %s: %s", key, e)
            value = hit[1] if hit else None
        self._d[key] = (_time.time(), value)
        return value


class LiveProvider(DataProvider):
    name = "live"
    history_unsupported = frozenset({"fundamentals", "community", "earnings", "news"})

    def __init__(self) -> None:
        import yfinance  # noqa: F401  (없으면 여기서 바로 알려 준다)

        self._yf = yfinance
        self._cache = _Cache()
        self._finnhub = os.getenv("FINNHUB_API_KEY") or None
        self._reddit = (os.getenv("REDDIT_CLIENT_ID"), os.getenv("REDDIT_CLIENT_SECRET"))
        if self._finnhub:  # Finnhub 은 날짜 범위 조회가 되므로 백테스트에서도 뉴스를 쓸 수 있다
            self.history_unsupported = self.history_unsupported - {"news"}

    def universe(self) -> list[str]:
        return list(LIVE_UNIVERSE)

    # ── 시세 ───────────────────────────────────────────────
    def _frame_to_bars(self, symbol: str, df, daily: bool, minutes: int = 5) -> list[Bar]:
        bars = []
        if df is None or df.empty:
            return bars
        for ts, row in df.iterrows():
            if row["Close"] != row["Close"]:  # NaN
                continue
            dt = ts.to_pydatetime()
            dt = dt.replace(tzinfo=ET) if dt.tzinfo is None else dt.astimezone(ET)
            end = session_close(dt.date()) if daily else dt + timedelta(minutes=minutes)
            bars.append(Bar(symbol, end, float(row["Open"]), float(row["High"]), float(row["Low"]), float(row["Close"]),
                            float(row["Volume"] or 0)))
        return bars

    def _all_daily(self, symbol: str) -> list[Bar]:
        def load():
            df = self._yf.Ticker(symbol).history(period="5y", interval="1d", auto_adjust=True)
            return self._frame_to_bars(symbol, df, daily=True)
        return self._cache.get(("daily", symbol), 600, load) or []

    def daily_bars(self, symbol: str, as_of: datetime, lookback: int) -> list[Bar]:
        # 장중에는 야후가 진행 중인 당일 봉을 주는데, 확정되지 않은 봉은 ts(16:00) > as_of 라서 여기서 걸러진다.
        bars = [b for b in self._all_daily(symbol) if b.ts <= as_of]
        return bars[-lookback:]

    def intraday_bars(self, symbol: str, day: date, as_of: datetime, minutes: int) -> list[Bar]:
        def load():
            df = self._yf.Ticker(symbol).history(period="5d", interval=f"{minutes}m", auto_adjust=True)
            return self._frame_to_bars(symbol, df, daily=False, minutes=minutes)
        if (datetime.now(ET).date() - day).days > 4:
            return []  # 오래된 분봉은 야후에서 제공하지 않는다
        bars = self._cache.get(("intraday", symbol, minutes), 45, load) or []
        return [b for b in bars if b.ts.date() == day and b.ts <= as_of]

    def quote(self, symbol: str, as_of: datetime) -> float | None:
        as_of = as_of.astimezone(ET)
        if abs((datetime.now(ET) - as_of).total_seconds()) < 180:
            def load():
                return float(self._yf.Ticker(symbol).fast_info["last_price"])
            price = self._cache.get(("quote", symbol), 20, load)
            if price:
                return price
        d = as_of.date()
        if OPEN <= as_of.time() < CLOSE:
            intraday = self.intraday_bars(symbol, d, as_of, 5)
            if intraday:
                return intraday[-1].close
            today = [b for b in self._all_daily(symbol) if b.ts.date() == d]
            if today:
                return today[0].open
        done = last_completed_session(as_of)
        bars = [b for b in self._all_daily(symbol) if b.ts.date() <= done]
        return bars[-1].close if bars else None

    # ── 기업 정보 ──────────────────────────────────────────
    def _info(self, symbol: str) -> dict:
        return self._cache.get(("info", symbol), 6 * 3600, lambda: dict(self._yf.Ticker(symbol).info)) or {}

    def profile(self, symbol: str) -> Profile:
        info = self._info(symbol)
        sector = SECTOR_KO.get(info.get("sector", ""), info.get("sector") or ("ETF" if info.get("quoteType") == "ETF" else "기타"))
        return Profile(symbol, info.get("shortName") or info.get("longName") or symbol, sector)

    def fundamentals(self, symbol: str, as_of: datetime) -> dict | None:
        info = self._info(symbol)
        if not info or info.get("quoteType") == "ETF" or not info.get("totalRevenue"):
            return None
        g = info.get
        de = g("debtToEquity")
        ni, cfo, rev, fcf = g("netIncomeToCommon"), g("operatingCashflow"), g("totalRevenue"), g("freeCashflow")
        price, target = g("currentPrice") or g("regularMarketPrice"), g("targetMeanPrice")
        return {
            "fiscal_period": "최근 12개월(TTM)", "reported_at": as_of.date().isoformat(),
            "revenue_ttm_usd_m": round(rev / 1e6) if rev else None,
            "revenue_growth_yoy": g("revenueGrowth"), "gross_margin": g("grossMargins"),
            "operating_margin": g("operatingMargins"), "net_margin": g("profitMargins"),
            "eps_ttm": g("trailingEps"), "eps_growth_yoy": g("earningsGrowth"), "eps_surprise_pct": None,
            "fcf_margin": round(fcf / rev, 4) if fcf and rev else None,
            "cfo_to_net_income": round(cfo / ni, 2) if cfo and ni and ni > 0 else None,
            "debt_to_equity": round(de / 100, 2) if de is not None else None,  # 야후는 퍼센트로 준다
            "current_ratio": g("currentRatio"), "roe": g("returnOnEquity"),
            "pe_ttm": g("trailingPE"), "peg": g("trailingPegRatio") or g("pegRatio"),
            "ps_ttm": g("priceToSalesTrailing12Months"), "sector": SECTOR_KO.get(g("sector", ""), g("sector")),
            "sector_median_pe": None, "price": price, "analyst_target_mean": target,
            "insider_net_buy_3m_usd_m": None, "institutional_ownership_chg_qoq": None,
            "forward_pe": g("forwardPE"), "analyst_recommendation_mean": g("recommendationMean"),
        }

    def next_earnings(self, symbol: str, as_of: datetime) -> date | None:
        def load():
            cal = self._yf.Ticker(symbol).calendar or {}
            dates = cal.get("Earnings Date") or []
            return [d for d in dates if isinstance(d, date)]
        dates = self._cache.get(("earnings", symbol), 6 * 3600, load) or []
        future = [d for d in dates if d >= as_of.date()]
        return min(future) if future else None

    # ── 뉴스 ───────────────────────────────────────────────
    def news(self, symbol: str, as_of: datetime, limit: int = 8) -> list[NewsItem]:
        if self._finnhub:
            items = self._cache.get(("finnhub", symbol, as_of.date()), 600, lambda: self._finnhub_news(symbol, as_of))
        else:
            items = self._cache.get(("yfnews", symbol), 600, lambda: self._yahoo_news(symbol))
        items = [n for n in (items or []) if as_of - timedelta(days=5) <= n.ts <= as_of]
        return sorted(items, key=lambda n: n.ts, reverse=True)[:limit]

    def _finnhub_news(self, symbol: str, as_of: datetime) -> list[NewsItem]:
        import requests

        r = requests.get("https://finnhub.io/api/v1/company-news", timeout=15, params={
            "symbol": symbol, "from": (as_of.date() - timedelta(days=5)).isoformat(), "to": as_of.date().isoformat(),
            "token": self._finnhub})
        r.raise_for_status()
        return [NewsItem(datetime.fromtimestamp(n["datetime"], tz=timezone.utc).astimezone(ET), n.get("headline", ""),
                         (n.get("summary") or "")[:600], n.get("source", "Finnhub"), n.get("url", ""))
                for n in r.json() if n.get("datetime") and n.get("headline")]

    def _yahoo_news(self, symbol: str) -> list[NewsItem]:
        out = []
        for n in self._yf.Ticker(symbol).news or []:
            c = n.get("content") or n  # 야후 뉴스 형식은 버전에 따라 두 가지다
            title = c.get("title")
            if c.get("pubDate"):
                ts = datetime.fromisoformat(c["pubDate"].replace("Z", "+00:00"))
            elif c.get("providerPublishTime"):
                ts = datetime.fromtimestamp(c["providerPublishTime"], tz=timezone.utc)
            else:
                continue
            if not title:
                continue
            provider = c.get("provider") or {}
            url = (c.get("canonicalUrl") or {}).get("url") or c.get("link") or ""
            out.append(NewsItem(ts.astimezone(ET), title, (c.get("summary") or "")[:600],
                                provider.get("displayName") or c.get("publisher") or "Yahoo Finance", url))
        return out

    # ── 커뮤니티 ───────────────────────────────────────────
    def community(self, symbol: str, as_of: datetime) -> CommunitySnapshot | None:
        return self._cache.get(("community", symbol), 900, lambda: self._community(symbol, as_of))

    def _community(self, symbol: str, as_of: datetime) -> CommunitySnapshot | None:
        import requests

        posts: list[CommunityPost] = []
        bull = bear = 0
        try:
            r = requests.get(f"https://api.stocktwits.com/api/2/streams/symbol/{symbol}.json", timeout=15, headers=UA)
            r.raise_for_status()
            for m in r.json().get("messages", []):
                ts = datetime.fromisoformat(m["created_at"].replace("Z", "+00:00")).astimezone(ET)
                tag = ((m.get("entities") or {}).get("sentiment") or {}).get("basic")
                bull += tag == "Bullish"
                bear += tag == "Bearish"
                posts.append(CommunityPost(ts, (m.get("body") or "")[:400], "StockTwits", (m.get("likes") or {}).get("total", 0)))
        except Exception as e:
            log.warning("StockTwits 조회 실패 %s: %s", symbol, e)
        posts += self._reddit_posts(symbol)
        posts = [p for p in posts if as_of - timedelta(hours=24) <= p.ts <= as_of]
        if not posts:
            return None
        posts.sort(key=lambda p: p.ts, reverse=True)
        # 무료 경로로는 언급량의 7일 평균을 알 수 없어 mentions_avg_7d 는 0(미제공)으로 둔다.
        return CommunitySnapshot(min(as_of, posts[0].ts), len(posts), 0.0,
                                 round(bull / (bull + bear), 3) if bull + bear >= 5 else None, posts[:12])

    def _reddit_posts(self, symbol: str) -> list[CommunityPost]:
        cid, secret = self._reddit
        if not cid or not secret:
            return []
        import requests

        try:
            tok = self._cache.get(("reddit_token",), 3000, lambda: requests.post(
                "https://www.reddit.com/api/v1/access_token", auth=(cid, secret), headers=UA, timeout=15,
                data={"grant_type": "client_credentials"}).json()["access_token"])
            r = requests.get("https://oauth.reddit.com/r/wallstreetbets+stocks+investing/search", timeout=15,
                             headers={**UA, "Authorization": f"Bearer {tok}"},
                             params={"q": f'"{symbol}"', "restrict_sr": 1, "sort": "new", "t": "day", "limit": 25})
            r.raise_for_status()
            return [CommunityPost(datetime.fromtimestamp(c["data"]["created_utc"], tz=timezone.utc).astimezone(ET),
                                  (c["data"].get("title", "") + " " + c["data"].get("selftext", ""))[:400],
                                  f"r/{c['data'].get('subreddit')}", c["data"].get("score", 0))
                    for c in r.json().get("data", {}).get("children", [])]
        except Exception as e:
            log.warning("Reddit 조회 실패 %s: %s", symbol, e)
            return []

    # ── 매크로 ─────────────────────────────────────────────
    def macro(self, as_of: datetime) -> dict:
        def closes(sym: str, n: int) -> list[float]:
            return [b.close for b in self.daily_bars(sym, as_of, n)]

        spy, vix, tnx = closes("SPY", 220), closes("^VIX", 30), closes("^TNX", 30)
        if len(spy) < 200:
            return {}
        out = {
            "as_of_session": last_completed_session(as_of).isoformat(),
            "spy_close": spy[-1],
            "spy_return_5d": round(spy[-1] / spy[-6] - 1, 4),
            "spy_return_20d": round(spy[-1] / spy[-21] - 1, 4),
            "spy_vs_sma50": round(spy[-1] / (sum(spy[-50:]) / 50) - 1, 4),
            "spy_vs_sma200": round(spy[-1] / (sum(spy[-200:]) / 200) - 1, 4),
            "vix_proxy": round(vix[-1], 1) if vix else None,  # 실데이터에서는 실제 VIX 지수
            "vix_proxy_change_20d": round(vix[-1] - vix[-21], 1) if len(vix) > 20 else None,
            "ust10y_yield": round(tnx[-1], 2) if tnx else None,
            "ust10y_change_20d": round(tnx[-1] - tnx[-21], 2) if len(tnx) > 20 else None,
            "sector_return_20d": {},
            "economic_calendar": None,  # 경제 일정 소스는 아직 연결하지 않았다
        }
        for sector, etf in SECTOR_ETFS.items():
            c = closes(etf, 25)
            if len(c) > 20:
                out["sector_return_20d"][sector] = round(c[-1] / c[-21] - 1, 4)
        return out
