"""LLM 추상화 계층: 공급자 교체, 결과 캐싱, 비용 기록, 월 예산 강제."""

from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone

from pydantic import BaseModel

from ..config import Config
from ..store import Store


@dataclass
class LLMCall:
    agent: str
    tier: str  # "research" | "senior"
    system: str
    task: str
    context: dict
    schema: type[BaseModel]
    untrusted: dict | None = None  # 뉴스·커뮤니티 등 외부 수집 텍스트 (지시가 아닌 데이터로만 취급)


@dataclass
class Usage:
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0
    cost_usd: float = 0.0


class LLMError(RuntimeError):
    pass


class LLMRefused(LLMError):
    pass


class BudgetExceeded(LLMError):
    pass


class LLMClient(ABC):
    name = "base"

    @abstractmethod
    def complete(self, call: LLMCall) -> tuple[BaseModel, Usage]: ...


@dataclass
class BudgetStatus:
    spent: float
    budget: float
    level: str  # normal | reduced | minimal | exhausted
    ratio: float = field(init=False)

    def __post_init__(self):
        self.ratio = self.spent / self.budget if self.budget > 0 else 1.0


class LLMGateway:
    """모든 에이전트 호출이 지나는 관문.

    - 같은 입력의 반복 호출은 캐시에서 돌려준다(비용 0).
    - 호출마다 토큰·비용을 기록한다.
    - 이번 달(실제 달력 기준) 누적 비용이 예산에 가까워지면 단계적으로 호출을 줄이고, 넘으면 호출을 거부한다.
    """

    def __init__(self, store: Store, client: LLMClient, cfg: Config, budget_usd: float | None = None):
        self.store, self.client, self.cfg = store, client, cfg
        self.budget_usd = cfg.llm.monthly_budget_usd if budget_usd is None else budget_usd

    @staticmethod
    def _month() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m")

    def spent(self) -> float:
        return float(self.store.scalar("SELECT SUM(cost_usd) FROM llm_usage WHERE month=?", (self._month(),), 0.0))

    def budget(self) -> BudgetStatus:
        spent = self.spent()
        ratio = spent / self.budget_usd if self.budget_usd > 0 else 1.0
        level = "exhausted" if ratio >= 1.0 else "minimal" if ratio >= 0.9 else "reduced" if ratio >= 0.7 else "normal"
        return BudgetStatus(spent=spent, budget=self.budget_usd, level=level)

    def _key(self, call: LLMCall) -> str:
        blob = json.dumps([self.client.name, call.agent, call.tier, call.system, call.task, call.context,
                           call.untrusted, call.schema.__name__], sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()

    def run(self, call: LLMCall) -> BaseModel:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        key = self._key(call)
        hit = self.store.one("SELECT payload FROM llm_cache WHERE key=?", (key,))
        if hit:
            self.store.insert("llm_usage", ts=now, month=self._month(), agent=call.agent, model="cache",
                              input_tokens=0, output_tokens=0, cache_read=0, cache_write=0, cost_usd=0.0, cached=1)
            return call.schema.model_validate(hit["payload"])
        if self.budget().level == "exhausted":
            raise BudgetExceeded(f"이번 달 LLM 예산 ${self.budget_usd:.2f} 를 모두 사용했습니다.")
        try:
            result, usage = self.client.complete(call)
        except LLMError as e:
            failed: Usage | None = getattr(e, "usage", None)
            if failed:  # 실패한 호출도 토큰을 썼다면 비용으로 잡는다
                self.store.insert("llm_usage", ts=now, month=self._month(), agent=call.agent, model=failed.model,
                                  input_tokens=failed.input_tokens, output_tokens=failed.output_tokens,
                                  cache_read=failed.cache_read, cache_write=failed.cache_write,
                                  cost_usd=failed.cost_usd, cached=0)
            raise
        self.store.insert("llm_usage", ts=now, month=self._month(), agent=call.agent, model=usage.model,
                          input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
                          cache_read=usage.cache_read, cache_write=usage.cache_write, cost_usd=usage.cost_usd, cached=0)
        self.store.execute("INSERT OR REPLACE INTO llm_cache(key, ts, payload) VALUES(?,?,?)",
                           (key, now, result.model_dump_json()))
        return result
