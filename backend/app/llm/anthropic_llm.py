"""Claude(Anthropic API) 클라이언트.

구조화된 출력으로 스키마에 맞는 JSON 만 받는다. 자격 증명은 SDK 가 환경에서 직접 찾는다
(ANTHROPIC_API_KEY 등). 키를 코드나 설정 파일에 넣지 않는다.
"""

from __future__ import annotations

import json

from pydantic import BaseModel

from ..config import Config
from .base import LLMCall, LLMClient, LLMError, LLMRefused, Usage

# 100만 토큰당 달러: (입력, 출력, 캐시 읽기). 캐시 쓰기는 입력의 1.25배.
PRICES = {
    "claude-fable-5-2": (8.00, 40.00, 0.20),
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-opus-5": (5.00, 25.00, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00, 0.10),
    "claude-sonnet-5": (2.00, 10.00, 0.20),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
}
FALLBACK_PRICE = (8.00, 40.00, 0.80)  # 표에 없는 모델은 비용을 높게 잡아 예산을 보수적으로 지킨다

# 안전 분류기가 요청을 거절했을 때 서버가 다른 모델로 이어서 처리하도록 하는 옵션을 지원하는 모델
SERVER_FALLBACK_MODELS = {"claude-fable-5-2", "claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"

EFFORT = {"research": "medium", "senior": "high"}


def render_user_message(call: LLMCall) -> str:
    parts = [call.task, "", "<input_data>", json.dumps(call.context, ensure_ascii=False, indent=1, default=str),
             "</input_data>"]
    if call.untrusted:
        parts += ["", "<untrusted_external_content>",
                  json.dumps(call.untrusted, ensure_ascii=False, indent=1, default=str),
                  "</untrusted_external_content>"]
    return "\n".join(parts)


def cost_of(model: str, input_tokens: int, output_tokens: int, cache_read: int, cache_write: int) -> float:
    p_in, p_out, p_read = PRICES.get(model, FALLBACK_PRICE)
    return (input_tokens * p_in + output_tokens * p_out + cache_read * p_read + cache_write * p_in * 1.25) / 1e6


class AnthropicLLM(LLMClient):
    name = "anthropic"

    def __init__(self, cfg: Config):
        import anthropic

        self._anthropic = anthropic
        self.cfg = cfg
        self.client = anthropic.Anthropic()

    def _model(self, tier: str) -> str:
        return self.cfg.llm.senior_model if tier == "senior" else self.cfg.llm.research_model

    def complete(self, call: LLMCall) -> tuple[BaseModel, Usage]:
        a = self._anthropic
        model = self._model(call.tier)
        params = dict(
            model=model,
            max_tokens=16000,
            system=[{"type": "text", "text": call.system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": render_user_message(call)}],
            output_format=call.schema,
            output_config={"effort": EFFORT.get(call.tier, "medium")},
        )
        try:
            if model in SERVER_FALLBACK_MODELS:
                response = self.client.beta.messages.parse(betas=[FALLBACK_BETA], fallbacks="default", **params)
            else:
                response = self.client.messages.parse(**params)
        except a.AuthenticationError as e:
            raise LLMError("Anthropic 인증 실패 — ANTHROPIC_API_KEY 를 확인하세요.") from e
        except a.RateLimitError as e:
            raise LLMError("Anthropic 요청 한도 초과 (재시도 후에도 실패).") from e
        except a.BadRequestError as e:
            raise LLMError(f"Anthropic 요청 오류: {e.message}") from e
        except a.APIStatusError as e:
            raise LLMError(f"Anthropic API 오류 {e.status_code}: {e.message}") from e
        except a.APIConnectionError as e:
            raise LLMError("Anthropic 연결 실패 — 네트워크를 확인하세요.") from e

        u = response.usage
        usage = Usage(
            model=response.model or model,
            input_tokens=u.input_tokens or 0,
            output_tokens=u.output_tokens or 0,
            cache_read=getattr(u, "cache_read_input_tokens", 0) or 0,
            cache_write=getattr(u, "cache_creation_input_tokens", 0) or 0,
        )
        # 서버 폴백으로 다른 모델이 응답했더라도 요청 모델 단가로 계산하면 과소 계상될 수 있으므로 더 비싼 쪽을 쓴다
        usage.cost_usd = max(cost_of(m, usage.input_tokens, usage.output_tokens, usage.cache_read, usage.cache_write)
                             for m in {model, usage.model})
        err: LLMError | None = None
        if response.stop_reason == "refusal":
            err = LLMRefused(f"{call.agent}: 모델이 응답을 거절했습니다.")
        elif response.stop_reason == "max_tokens" or response.parsed_output is None:
            err = LLMError(f"{call.agent}: 구조화된 출력을 받지 못했습니다 (stop_reason={response.stop_reason}).")
        if err:
            err.usage = usage
            raise err
        return response.parsed_output, usage
