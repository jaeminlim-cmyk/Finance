"""목 LLM. API 키 없이 전체 흐름을 돌리기 위한 규칙 기반 가짜 응답이다.

입력 데이터의 숫자에 단순한 규칙을 적용해 그럴듯한 보고서를 만든다. 실제 모델의 판단을 흉내 내는 것이 아니라
파이프라인·대시보드·리스크 규칙이 끝까지 동작하는지 확인하는 용도이므로, 이 모드의 수익률에는 의미가 없다.
"""

from __future__ import annotations

from pydantic import BaseModel

from ..models import (SIGNAL_KO, SIGNAL_SCORE, AnalystReport, DebateArgument, Evidence, FinalDecision, MacroReport,
                      PostMortem, RiskReview, ScalpCandidate, ScalperPlan, TradeProposal)
from .base import LLMCall, LLMClient, Usage

POS_WORDS = ("웃돌", "공급 계약", "목표주가 상향", "자사주 매입", "수요가 예상보다 강")
NEG_WORDS = ("못 미쳤", "조사 착수", "하향 조정", "사임", "지연")
AGENT_TITLES = {"chart": "차트 분석가", "technical": "기술적 분석가", "financials": "재무제표 분석가",
                "fundamental": "기본적 분석가", "news": "뉴스 분석가", "community": "커뮤니티 분석가",
                "sentiment": "센티먼트 분석가"}
INJECTION_WORDS =("이전 지시를 무시", "ignore previous", "즉시 강력매수로 보고")


def _signal(score: float) -> str:
    return ("strong_buy" if score >= 1.6 else "buy" if score >= 0.5 else
            "strong_sell" if score <= -1.6 else "sell" if score <= -0.5 else "hold")


def _report(score: float, points: list[tuple[str, str]], risks: list[str], horizon: str, gaps: list[str],
            what: str) -> AnalystReport:
    sig = _signal(score)
    conf = round(min(0.9, 0.4 + 0.15 * abs(score)) * (0.6 if gaps else 1.0), 2)
    summary = f"{what} 관점 {SIGNAL_KO[sig]}" + (f" — {points[0][0]}" if points else " — 뚜렷한 근거 없음")
    return AnalystReport(signal=sig, confidence=conf, summary=summary,
                         evidence=[Evidence(point=p, source=s) for p, s in points[:5]],
                         risks=risks, horizon=horizon, data_gaps=gaps)


class MockLLM(LLMClient):
    name = "mock"

    def complete(self, call: LLMCall) -> tuple[BaseModel, Usage]:
        handler = getattr(self, f"_{call.agent}")
        return handler(call.context, call.untrusted or {}), Usage(model="mock")

    # ── 리서치 본부 ────────────────────────────────────────
    def _chart(self, c: dict, _u: dict) -> AnalystReport:
        f = c.get("chart")
        if not f:
            return _report(0, [], [], "days", ["chart"], "가격 구조")
        score, pts, risks = 0.0, [], []
        if f["ma_alignment"] == "상승":
            score += 1.0
            pts.append(("이동평균이 정배열로 상승 추세", "chart.ma_alignment"))
        elif f["ma_alignment"] == "하락":
            score -= 1.0
            pts.append(("이동평균이 역배열로 하락 추세", "chart.ma_alignment"))
        if f["breakout_20d"]:
            score += 0.9
            pts.append((f"20일 고점 {f['resistance_20d']} 돌파", "chart.breakout_20d"))
        if f["breakdown_20d"]:
            score -= 0.9
            pts.append((f"20일 저점 {f['support_20d']} 이탈", "chart.breakdown_20d"))
        r20 = f.get("return_20d") or 0.0
        if abs(r20) > 0.05:
            score += 0.5 if r20 > 0 else -0.5
            pts.append((f"20일 수익률 {r20:+.1%}", "chart.return_20d"))
        vr = f.get("volume_ratio_vs_20d") or 1.0
        if vr > 1.5 and f.get("return_1d"):
            score += 0.4 if f["return_1d"] > 0 else -0.4
            pts.append((f"거래량이 20일 평균의 {vr:.1f}배로 최근 움직임을 뒷받침", "chart.volume_ratio_vs_20d"))
        if (f.get("pct_from_52w_high") or -1) > -0.03:
            risks.append("52주 고점 부근이라 저항 가능성")
        pts.append((f"지지 {f['support_20d']} / 저항 {f['resistance_20d']}", "chart.support_20d"))
        return _report(score, pts, risks, "days", [], "가격 구조")

    def _technical(self, c: dict, _u: dict) -> AnalystReport:
        f = c.get("indicators")
        if not f:
            return _report(0, [], [], "days", ["indicators"], "지표")
        score, pts, risks, gaps = 0.0, [], [], []
        rsi = f.get("rsi14")
        if rsi is None:
            gaps.append("indicators.rsi14")
        elif rsi < 30:
            score += 0.8
            pts.append((f"RSI {rsi} 로 과매도 구간", "indicators.rsi14"))
        elif rsi > 72:
            score -= 0.5
            risks.append(f"RSI {rsi} 로 과열")
            pts.append((f"RSI {rsi} 로 과매수 구간", "indicators.rsi14"))
        elif rsi > 52:
            score += 0.4
            pts.append((f"RSI {rsi} 로 상승 모멘텀 유지", "indicators.rsi14"))
        hist = f.get("macd_hist")
        if hist is not None:
            score += 0.6 if hist > 0 else -0.6
            pts.append((f"MACD 히스토그램 {hist:+} ({'상방' if hist > 0 else '하방'})", "indicators.macd_hist"))
        for key, w, label in (("price_vs_sma50", 0.5, "50일선"), ("price_vs_sma200", 0.3, "200일선")):
            v = f.get(key)
            if v is None:
                gaps.append(f"indicators.{key}")
            else:
                score += w if v > 0 else -w
                pts.append((f"주가가 {label} 대비 {v:+.1%}", f"indicators.{key}"))
        rs = f.get("relative_strength_60d_vs_benchmark")
        if rs is not None and abs(rs) > 0.03:
            score += 0.4 if rs > 0 else -0.4
            pts.append((f"벤치마크 대비 60일 상대강도 {rs:+.1%}", "indicators.relative_strength_60d_vs_benchmark"))
        if (f.get("atr_pct") or 0) > 0.04:
            risks.append(f"ATR 이 주가의 {f['atr_pct']:.1%} 로 변동성이 큼")
        return _report(score, pts, risks, "days", gaps, "지표")

    def _financials(self, c: dict, _u: dict) -> AnalystReport:
        f = c.get("fundamentals")
        if not f:
            return _report(0, [], [], "months", ["fundamentals"], "재무제표")
        score, pts, risks = 0.0, [], []
        g = f.get("revenue_growth_yoy")
        if g is not None:
            score += 0.7 if g > 0.10 else -0.7 if g < 0 else 0.1
            pts.append((f"매출 성장률 {g:+.1%} (TTM, 전년 대비)", "fundamentals.revenue_growth_yoy"))
        om = f.get("operating_margin")
        if om is not None:
            score += 0.4 if om > 0.20 else -0.3 if om < 0.08 else 0.0
            pts.append((f"영업이익률 {om:.1%}", "fundamentals.operating_margin"))
        s = f.get("eps_surprise_pct")
        if s is not None and abs(s) > 0.03:
            score += 0.5 if s > 0 else -0.5
            pts.append((f"직전 분기 EPS 서프라이즈 {s:+.1%}", "fundamentals.eps_surprise_pct"))
        q = f.get("cfo_to_net_income")
        if q is not None and q < 0.95:
            score -= 0.4
            risks.append(f"영업현금흐름이 순이익의 {q:.2f}배 — 이익의 질 점검 필요")
        de = f.get("debt_to_equity")
        if de is not None and de > 1.5:
            score -= 0.4
            risks.append(f"부채비율 {de:.2f} 로 높음")
        return _report(score, pts, risks, "months", [], "재무제표")

    def _fundamental(self, c: dict, _u: dict) -> AnalystReport:
        f = c.get("valuation")
        if not f:
            return _report(0, [], [], "months", ["valuation"], "가치 평가")
        score, pts, risks, gaps = 0.0, [], [], []
        pe, med = f.get("pe_ttm"), f.get("sector_median_pe")
        if pe is None or med is None:
            gaps.append("valuation.pe_ttm")
        else:
            rel = pe / med
            score += 0.8 if rel < 0.8 else -0.8 if rel > 1.5 else 0.0
            pts.append((f"PER {pe} 배, 섹터 중앙값 {med} 배의 {rel:.2f}배", "valuation.pe_ttm"))
            if rel > 1.5:
                risks.append("섹터 대비 높은 밸류에이션")
        peg = f.get("peg")
        if peg is not None:
            score += 0.4 if peg < 1.5 else -0.3 if peg > 3 else 0.0
            pts.append((f"PEG {peg}", "valuation.peg"))
        tgt, price = f.get("analyst_target_mean"), c.get("price")
        if tgt and price:
            up = tgt / price - 1
            score += 0.4 if up > 0.10 else -0.4 if up < 0 else 0.0
            pts.append((f"애널리스트 평균 목표가 대비 {up:+.1%}", "valuation.analyst_target_mean"))
        ins = f.get("insider_net_buy_3m_usd_m")
        if ins is not None and abs(ins) > 5:
            score += 0.2 if ins > 0 else -0.2
            pts.append((f"최근 3개월 내부자 순매수 {ins:+.1f}백만 달러", "valuation.insider_net_buy_3m_usd_m"))
        ewd = c.get("earnings_within_days")
        if ewd is not None and ewd <= 5:
            risks.append(f"실적 발표까지 {ewd}거래일")
        return _report(score, pts, risks, "months", gaps, "가치 평가")

    def _news(self, c: dict, u: dict) -> AnalystReport:
        items = u.get("news") or []
        if not items:
            return _report(0, [], [], "days", ["news"], "뉴스")
        score, pts = 0.0, []
        for i, n in enumerate(items[:6]):
            text = n.get("headline", "")
            if any(w in text for w in POS_WORDS):
                score += 0.7
                pts.append((f"호재성 보도: {text}", f"news[{i}].headline"))
            elif any(w in text for w in NEG_WORDS):
                score -= 0.7
                pts.append((f"악재성 보도: {text}", f"news[{i}].headline"))
        risks = ["보도 내용이 이미 주가에 반영됐을 수 있음"] if pts else []
        return _report(score, pts, risks, "days", [], "뉴스")

    def _community(self, c: dict, u: dict) -> AnalystReport:
        f = c.get("community")
        if not f:
            return _report(0, [], [], "days", ["community"], "커뮤니티")
        score, pts, risks = 0.0, [], []
        b, ratio = f.get("bullish_ratio"), f.get("mention_ratio") or 1.0
        if b is not None:
            score += 0.7 if b > 0.65 else -0.7 if b < 0.35 else 0.0
            pts.append((f"낙관 비율 {b:.0%}", "community.bullish_ratio"))
        pts.append((f"언급량이 7일 평균의 {ratio:.1f}배", "community.mention_ratio"))
        if ratio > 2 and (b or 0) > 0.7:
            score -= 0.4
            risks.append("언급량 급증과 낙관 쏠림 — 과열 가능성")
        for p in u.get("posts") or []:
            if any(w in p.get("text", "") for w in INJECTION_WORDS):
                risks.append("특정 행동을 지시하는 조작 시도로 보이는 게시물이 있어 무시함")
        return _report(score, pts, risks, "days", [], "커뮤니티")

    def _sentiment(self, c: dict, _u: dict) -> AnalystReport:
        score, pts, risks, gaps = 0.0, [], [], []
        for key, label in (("news_report", "뉴스"), ("community_report", "커뮤니티")):
            rep = c.get(key)
            if not rep or rep.get("signal") is None:
                gaps.append(key)
                continue
            s = SIGNAL_SCORE[rep["signal"]] * rep.get("confidence", 0.5)
            score += s
            if abs(s) > 0.2:
                pts.append((f"{label} 분석가 의견: {SIGNAL_KO[rep['signal']]}", f"{key}.signal"))
        vix = (c.get("market") or {}).get("vix_proxy")
        if vix is not None:
            if vix > 26:
                score -= 0.5
                risks.append(f"변동성 지표 {vix} 로 시장 불안")
            pts.append((f"시장 변동성 지표 {vix}", "market.vix_proxy"))
        return _report(score, pts, risks, "days", gaps, "종합 심리")

    def _macro(self, c: dict, _u: dict) -> MacroReport:
        m = c.get("macro") or {}
        if not m:
            return MacroReport(regime="neutral", confidence=0.2, summary="거시 데이터가 없어 중립으로 둠", evidence=[],
                               favored_sectors=[], avoided_sectors=[], data_gaps=["macro"])
        trend, vix = m.get("spy_vs_sma200", 0.0), m.get("vix_proxy", 20.0)
        regime = "risk_on" if trend > 0 and vix < 22 else "risk_off" if trend < 0 and vix > 22 else "neutral"
        sectors = sorted((m.get("sector_return_20d") or {}).items(), key=lambda kv: kv[1], reverse=True)
        ko = {"risk_on": "위험선호", "neutral": "중립", "risk_off": "위험회피"}[regime]
        return MacroReport(
            regime=regime, confidence=0.6,
            summary=f"{ko} 국면 — 지수가 200일선 대비 {trend:+.1%}, 변동성 지표 {vix}",
            evidence=[Evidence(point=f"S&P 500 이 200일선 대비 {trend:+.1%}", source="macro.spy_vs_sma200"),
                      Evidence(point=f"변동성 지표 {vix}", source="macro.vix_proxy"),
                      Evidence(point=f"미 10년물 금리 {m.get('ust10y_yield')}%, 20일 변화 {m.get('ust10y_change_20d'):+}%p",
                               source="macro.ust10y_yield")],
            favored_sectors=[s for s, _ in sectors[:2]], avoided_sectors=[s for s, _ in sectors[-2:]],
            data_gaps=["macro.economic_calendar"] if m.get("economic_calendar") is None else [])

    # ── 토론 본부 ──────────────────────────────────────────
    def _debate(self, c: dict, side: str) -> DebateArgument:
        want = 1 if side == "bull" else -1
        mine = [r for r in c["reports"] if r.get("signal") and SIGNAL_SCORE[r["signal"]] * want > 0]
        other = [r for r in c["reports"] if r.get("signal") and SIGNAL_SCORE[r["signal"]] * want < 0]
        points = [f"{r['title']}: {r['summary']}" for r in sorted(mine, key=lambda r: -r["confidence"])[:4]]
        if side == "bear":
            points += [f"위험 요인: {risk}" for r in c["reports"] for risk in (r.get("risks") or [])][:3]
        if not points:
            points = ["뚜렷한 매수 근거는 약하지만 하방 신호도 제한적임" if side == "bull" else "뚜렷한 악재는 없으나 살 이유도 부족함"]
        opp = c.get("opponent_latest")
        rebuttals = []
        if opp:
            for p in opp["key_points"][:2]:
                tail = "이미 주가에 반영됐을 가능성이 크고 추세를 바꿀 정도는 아님" if side == "bull" else "지속된다는 근거가 부족하고 반전 시 손실이 큼"
                rebuttals.append(f"상대가 든 「{p}」 — {tail}")
        total = sum(r["confidence"] for r in mine) + sum(r["confidence"] for r in other)
        conviction = round(sum(r["confidence"] for r in mine) / total, 2) if total else 0.3
        verb = "매수" if side == "bull" else "매수 보류 또는 매도"
        return DebateArgument(thesis=f"{c['symbol']} 은(는) 현 시점에서 {verb}가 타당하다 (근거 {len(mine)}건 대 {len(other)}건).",
                              key_points=points, rebuttals=rebuttals,
                              concessions=[f"{other[0]['title']}의 지적은 일리가 있음"] if opp and other else [],
                              conviction=conviction)

    def _bull(self, c: dict, _u: dict) -> DebateArgument:
        return self._debate(c, "bull")

    def _bear(self, c: dict, _u: dict) -> DebateArgument:
        return self._debate(c, "bear")

    # ── 트레이딩 본부 ──────────────────────────────────────
    @staticmethod
    def _conv(debate: list[dict], side: str) -> float:
        turns = [t for t in debate if t["side"] == side]
        return turns[-1]["conviction"] if turns else 0.5

    def _trader(self, c: dict, _u: dict) -> TradeProposal:
        cons, pos = c["consensus_score"], c.get("position")
        bull, bear = self._conv(c.get("debate", []), "bull"), self._conv(c.get("debate", []), "bear")
        if pos:
            if cons <= -0.15 or bear - bull > 0.3:
                return TradeProposal(action="sell", stop_price=None, target_price=None, size_multiplier=1.0, horizon_days=1,
                                     rationale=f"합의 점수 {cons:+.2f} 로 보유 근거가 약해져 청산 제안")
            return TradeProposal(action="hold", stop_price=None, target_price=None, size_multiplier=1.0, horizon_days=10,
                                 rationale=f"합의 점수 {cons:+.2f} 로 보유 근거 유지")
        if cons >= 0.2 and bull >= bear:
            size = round(min(1.0, 0.4 + cons * 1.5), 2)
            return TradeProposal(action="buy", stop_price=None, target_price=None, size_multiplier=size, horizon_days=10,
                                 rationale=f"합의 점수 {cons:+.2f}, 매수 측 확신 {bull:.2f} 대 매도 측 {bear:.2f}. 기본 ATR 손절·목표가 사용")
        return TradeProposal(action="hold", stop_price=None, target_price=None, size_multiplier=0.0, horizon_days=0,
                             rationale=f"합의 점수 {cons:+.2f} 로 진입 근거 부족")

    def _risk(self, c: dict, _u: dict) -> RiskReview:
        prop = c["proposal"]
        if prop["action"] != "buy":
            return RiskReview(verdict="approve", size_multiplier=1.0, concerns=[], rationale="위험을 늘리지 않는 매매안")
        concerns, mult = [], 1.0
        vol = (c.get("volatility") or {}).get("atr_pct") or 0.0
        regime = (c.get("macro") or {}).get("regime", "neutral")
        sector_w = (c["portfolio"].get("sector_pct") or {}).get(c.get("sector"), 0.0)
        ewd = c.get("earnings_within_days")
        if regime == "risk_off":
            concerns.append("거시 국면이 위험회피")
            mult *= 0.5
        if vol > 0.04:
            concerns.append(f"ATR {vol:.1%} 로 변동성이 큼")
            mult *= 0.6
        if sector_w > 0.18:
            concerns.append(f"{c.get('sector')} 섹터 비중이 이미 {sector_w:.0%}")
            mult *= 0.6
        if ewd is not None and ewd <= 4:
            concerns.append(f"실적 발표까지 {ewd}거래일")
            mult *= 0.5
        if (c.get("risk_state") or {}).get("drawdown_pct", 0.0) < -0.08:
            concerns.append("펀드 낙폭이 -8% 를 넘음")
            mult *= 0.5
        if regime == "risk_off" and vol > 0.04:
            return RiskReview(verdict="reject", size_multiplier=0.0, concerns=concerns,
                              rationale="위험회피 국면에서 고변동성 종목 신규 진입은 부적절")
        if mult < 1.0:
            return RiskReview(verdict="reduce", size_multiplier=round(mult, 2), concerns=concerns,
                              rationale="위 사유로 포지션 축소 권고")
        return RiskReview(verdict="approve", size_multiplier=1.0, concerns=[], rationale="특별한 추가 위험 없음")

    def _head(self, c: dict, _u: dict) -> FinalDecision:
        cons, pos, prop, review = c["consensus_score"], c.get("position"), c.get("proposal"), c.get("risk_review")
        regime = {"risk_on": "위험선호", "risk_off": "위험회피"}.get((c.get("macro") or {}).get("regime"), "중립")
        factors = [f"리서치 합의 점수 {cons:+.2f}", f"거시 국면 {regime}"]
        if prop is None:  # 보유 종목 약식 재검토
            if cons <= -0.25 or (pos and pos.get("unrealized_pct", 0) < -0.06 and cons < 0):
                return FinalDecision(action="sell", conviction=0.6, size_multiplier=1.0, stop_price=None, target_price=None,
                                     rationale="보유 근거가 약해져 청산", key_factors=factors, dissent="")
            stop = None
            if pos and pos.get("unrealized_pct", 0) > 0.05:
                stop = round(pos["avg_price"] * 1.005, 2)  # 수익 구간이면 손절가를 본전 위로
                factors.append("수익 구간 진입으로 손절가 상향")
            return FinalDecision(action="hold", conviction=0.55, size_multiplier=1.0, stop_price=stop, target_price=None,
                                 rationale="보유 근거 유지", key_factors=factors, dissent="")
        if review and review["verdict"] == "reject":
            return FinalDecision(action="hold", conviction=0.6, size_multiplier=0.0, stop_price=None, target_price=None,
                                 rationale="리스크 관리자의 거부 의견을 따름", key_factors=factors + review["concerns"],
                                 dissent=f"트레이더는 {prop['action']} 를 제안했으나 리스크 관리자가 거부: {review['rationale']}")
        size = round(prop["size_multiplier"] * (review["size_multiplier"] if review else 1.0), 2)
        bear = [t for t in c.get("debate", []) if t["side"] == "bear"]
        dissent = f"매도 논거 담당의 반대: {bear[-1]['thesis']}" if bear and prop["action"] == "buy" else ""
        if review and review["concerns"]:
            factors += review["concerns"]
        return FinalDecision(action=prop["action"], conviction=round(min(0.9, 0.5 + abs(cons) * 0.6), 2),
                             size_multiplier=size if prop["action"] == "buy" else 1.0,
                             stop_price=prop.get("stop_price"), target_price=prop.get("target_price"),
                             rationale=f"트레이더 매매안 채택. {prop['rationale']}", key_factors=factors, dissent=dissent)

    def _scalper(self, c: dict, _u: dict) -> ScalperPlan:
        if c.get("macro_regime") == "risk_off":
            return ScalperPlan(candidates=[], summary="위험회피 국면이라 오늘은 단타 후보를 내지 않음")
        rows = sorted(c.get("candidates") or [], key=lambda r: r["relative_volume"] + 8 * abs(r["gap_today_pct"]),
                      reverse=True)
        picks = []
        for r in rows[: c.get("max_candidates", 3)]:
            if r["return_5d"] > 0:
                picks.append(ScalpCandidate(symbol=r["symbol"], strategy="orb",
                                            reason=f"5일 수익률 {r['return_5d']:+.1%}, 상대 거래량 {r['relative_volume']}배 — 추세 지속 기대"))
            else:
                picks.append(ScalpCandidate(symbol=r["symbol"], strategy="vwap_reversion",
                                            reason=f"ATR {r['atr_pct']:.1%} 로 변동성은 크나 추세 약함 — 과매도 반등 노림"))
        return ScalperPlan(candidates=picks, summary=f"거래량과 변동성 기준으로 {len(picks)}개 후보 선정")

    # ── 지원 조직 ──────────────────────────────────────────
    def _performance(self, c: dict, _u: dict) -> PostMortem:
        t = c["trade"]
        win = t["pnl"] > 0
        right = [r["agent"] for r in c.get("reports", []) if r.get("signal") and (SIGNAL_SCORE[r["signal"]] > 0) == win
                 and SIGNAL_SCORE[r["signal"]] != 0]
        wrong = [r["agent"] for r in c.get("reports", []) if r.get("signal") and (SIGNAL_SCORE[r["signal"]] > 0) != win
                 and SIGNAL_SCORE[r["signal"]] != 0]
        reason = {"stop": "손절", "target": "목표가 도달", "decision": "회의 결정"}.get(t.get("exit_reason"), t.get("exit_reason"))
        cons = c.get("consensus_score")
        cons_txt = f"합의 점수 {cons:+.2f} 에서 진입" if cons is not None else "진입 근거 기록 없음"
        if win:
            lesson = f"{t['symbol']}({t.get('sector')}): {cons_txt}해 {t['pnl_pct']:+.1%} 로 {reason} 청산. 같은 조건의 신호는 계속 신뢰할 만함."
        else:
            names = ", ".join(AGENT_TITLES.get(a, a) for a in wrong) or "낙관 의견"
            lesson = f"{t['symbol']}({t.get('sector')}): {cons_txt}했으나 {t['pnl_pct']:+.1%} 로 {reason} 청산. {names} 쪽 신호의 비중을 낮춰 볼 것."
        return PostMortem(lesson=lesson, what_worked=[f"{AGENT_TITLES.get(a, a)}의 판단" for a in right][:3],
                          what_failed=[f"{AGENT_TITLES.get(a, a)}의 판단" for a in wrong][:3],
                          agents_right=right, agents_wrong=wrong)
