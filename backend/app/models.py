"""시장 데이터 자료형과 에이전트 출력 스키마."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# ── 시장 데이터 ────────────────────────────────────────────


@dataclass(frozen=True)
class Bar:
    symbol: str
    ts: datetime  # 봉이 확정된 시각 (일봉은 당일 16:00 ET, 분봉은 봉 종료 시각)
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class NewsItem:
    ts: datetime
    headline: str
    summary: str
    source: str
    url: str = ""


@dataclass(frozen=True)
class CommunityPost:
    ts: datetime
    text: str
    source: str
    score: int = 0


@dataclass(frozen=True)
class CommunitySnapshot:
    ts: datetime
    mentions_24h: int
    mentions_avg_7d: float
    bullish_ratio: float | None
    posts: list[CommunityPost] = field(default_factory=list)


@dataclass(frozen=True)
class Profile:
    symbol: str
    name: str
    sector: str


def to_dict(obj) -> dict:
    d = asdict(obj)
    for k, v in d.items():
        if isinstance(v, datetime):
            d[k] = v.isoformat(timespec="minutes")
    return d


# ── 에이전트 출력 스키마 ───────────────────────────────────
# 구조화된 출력(JSON)으로 강제된다. 숫자 범위 검증은 코드에서 따로 한다.

Signal = Literal["strong_buy", "buy", "hold", "sell", "strong_sell"]
SIGNAL_SCORE = {"strong_buy": 2.0, "buy": 1.0, "hold": 0.0, "sell": -1.0, "strong_sell": -2.0}
SIGNAL_KO = {"strong_buy": "강력매수", "buy": "매수", "hold": "중립", "sell": "매도", "strong_sell": "강력매도"}


class Evidence(BaseModel):
    point: str = Field(description="근거 한 문장")
    source: str = Field(description="이 근거가 나온 입력 데이터 항목 이름. 예: indicators.rsi14")


class AnalystReport(BaseModel):
    signal: Signal
    confidence: float = Field(description="0~1 사이 확신도")
    summary: str = Field(description="한 줄 요약")
    evidence: list[Evidence]
    risks: list[str]
    horizon: Literal["intraday", "days", "weeks", "months"]
    data_gaps: list[str] = Field(description="필요했지만 제공되지 않은 데이터. 없으면 빈 배열")


class MacroReport(BaseModel):
    regime: Literal["risk_on", "neutral", "risk_off"]
    confidence: float
    summary: str
    evidence: list[Evidence]
    favored_sectors: list[str]
    avoided_sectors: list[str]
    data_gaps: list[str]


class DebateArgument(BaseModel):
    thesis: str = Field(description="핵심 주장 한두 문장")
    key_points: list[str]
    rebuttals: list[str] = Field(description="상대의 가장 강한 논점에 대한 반박. 1라운드 첫 발언이면 빈 배열")
    concessions: list[str] = Field(description="상대 주장 중 인정하는 부분. 없으면 빈 배열")
    conviction: float = Field(description="0~1 사이 확신도")


class TradeProposal(BaseModel):
    action: Literal["buy", "sell", "hold"]
    stop_price: float | None = Field(description="손절가. 제안하지 않으면 null (코드가 ATR 기준 기본값 사용)")
    target_price: float | None
    size_multiplier: float = Field(description="기본 포지션 크기 대비 배수, 0~1")
    horizon_days: int
    rationale: str


class RiskReview(BaseModel):
    verdict: Literal["approve", "reduce", "reject"]
    size_multiplier: float = Field(description="승인 시 1.0, 축소 시 0~1, 거부 시 0")
    concerns: list[str]
    rationale: str


class FinalDecision(BaseModel):
    action: Literal["buy", "sell", "hold"]
    conviction: float
    size_multiplier: float = Field(description="기본 포지션 크기 대비 배수, 0~1")
    stop_price: float | None
    target_price: float | None
    rationale: str
    key_factors: list[str]
    dissent: str = Field(description="채택하지 않은 소수 의견과 그 이유. 없으면 빈 문자열")


class ScalpCandidate(BaseModel):
    symbol: str
    strategy: Literal["orb", "vwap_reversion"]
    reason: str


class ScalperPlan(BaseModel):
    candidates: list[ScalpCandidate]
    summary: str


class PostMortem(BaseModel):
    lesson: str = Field(description="다음 회의에 반영할 교훈 한두 문장")
    what_worked: list[str]
    what_failed: list[str]
    agents_right: list[str] = Field(description="결과적으로 옳았던 직원의 id")
    agents_wrong: list[str]
