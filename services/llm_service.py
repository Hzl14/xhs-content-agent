from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI, RateLimitError

from core.config import settings
from services.trace_service import begin_span


@dataclass
class LLMResult:
    content: str
    input_tokens: int = 0
    output_tokens: int = 0


class LLMService:
    """
    Unified LLM client based on OpenAI-compatible protocol.
    Works with DashScope, DeepSeek, and other OpenAI-compatible endpoints.
    """

    def __init__(self) -> None:
        self.enabled = bool(settings.llm_api_key)
        self._client = (
            AsyncOpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url or None)
            if self.enabled
            else None
        )

    async def chat(
        self,
        system: str,
        user: str,
        temperature: float | None = None,
        model: str | None = None,
    ) -> LLMResult:
        if not self.enabled or not self._client:
            return LLMResult(content="")

        resolved_model = model or settings.llm_model
        span = begin_span(
            "llm_call",
            "chat",
            input_summary={
                "model": resolved_model,
                "temperature": settings.llm_temperature if temperature is None else temperature,
                "system_chars": len(system),
                "user_chars": len(user),
            },
        )
        deadline_at = self._deadline_at()
        try:
            result = await self._with_transport_retry(
                lambda: self._chat_once(
                    system=system,
                    user=user,
                    temperature=temperature,
                    model=resolved_model,
                ),
                deadline_at=deadline_at,
            )
            span.end(
                output_summary={
                    "output_chars": len(result.content),
                    "input_tokens": result.input_tokens,
                    "output_tokens": result.output_tokens,
                }
            )
            return result
        except Exception as exc:
            span.end(status="failed", error=str(exc))
            raise

    async def _chat_once(
        self,
        system: str,
        user: str,
        temperature: float | None,
        model: str,
    ) -> LLMResult:
        response = await self._client.chat.completions.create(
            model=model,
            temperature=settings.llm_temperature if temperature is None else temperature,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        content = response.choices[0].message.content or ""
        input_tokens = response.usage.prompt_tokens if response.usage else 0
        output_tokens = response.usage.completion_tokens if response.usage else 0
        return LLMResult(content=content, input_tokens=input_tokens, output_tokens=output_tokens)

    async def chat_vision(
        self,
        system: str,
        user_text: str,
        image_urls: list[str],
        temperature: float | None = None,
        model: str | None = None,
    ) -> LLMResult:
        if not self.enabled or not self._client:
            return LLMResult(content="")

        resolved_model = model or settings.llm_vision_model
        span = begin_span(
            "llm_call",
            "chat_vision",
            input_summary={
                "model": resolved_model,
                "system_chars": len(system),
                "user_chars": len(user_text),
                "image_count": len(image_urls),
            },
        )
        deadline_at = self._deadline_at()
        try:
            result = await self._with_transport_retry(
                lambda: self._chat_vision_once(
                    system=system,
                    user_text=user_text,
                    image_urls=image_urls,
                    temperature=temperature,
                    model=resolved_model,
                ),
                deadline_at=deadline_at,
            )
            span.end(
                output_summary={
                    "output_chars": len(result.content),
                    "input_tokens": result.input_tokens,
                    "output_tokens": result.output_tokens,
                }
            )
            return result
        except Exception as exc:
            span.end(status="failed", error=str(exc))
            raise

    async def _chat_vision_once(
        self,
        system: str,
        user_text: str,
        image_urls: list[str],
        temperature: float | None,
        model: str,
    ) -> LLMResult:
        user_content: list[dict] = [{"type": "text", "text": user_text}]
        for url in image_urls:
            user_content.append({"type": "image_url", "image_url": {"url": url}})

        response = await self._client.chat.completions.create(
            model=model,
            temperature=settings.llm_temperature if temperature is None else temperature,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ],
        )
        content = response.choices[0].message.content or ""
        input_tokens = response.usage.prompt_tokens if response.usage else 0
        output_tokens = response.usage.completion_tokens if response.usage else 0
        return LLMResult(content=content, input_tokens=input_tokens, output_tokens=output_tokens)

    async def _with_transport_retry(
        self,
        call: Callable[[], Awaitable[LLMResult]],
        deadline_at: float | None = None,
    ) -> LLMResult:
        retries = max(settings.llm_max_retries, 1)
        request_timeout = max(settings.llm_request_timeout_seconds, 0.1)
        retry_deadline = max(settings.llm_retry_deadline_seconds, request_timeout)
        deadline_at = deadline_at or self._deadline_at()
        last_exc: Exception | None = None

        for attempt in range(retries):
            try:
                remaining = deadline_at - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"LLM retry deadline exceeded after {retry_deadline:.1f}s.")
                timeout = min(request_timeout, remaining)
                return await asyncio.wait_for(call(), timeout=timeout)
            except TimeoutError as exc:
                last_exc = exc
            except asyncio.TimeoutError as exc:
                last_exc = TimeoutError(f"LLM request timed out after {request_timeout:.1f}s.")
            except (APIConnectionError, APITimeoutError, RateLimitError) as exc:
                last_exc = exc
            except APIStatusError as exc:
                last_exc = exc
                if exc.status_code < 500 and exc.status_code not in {408, 409, 429}:
                    raise

            if attempt >= retries - 1:
                break
            remaining = deadline_at - time.monotonic()
            if remaining <= 0:
                break
            await asyncio.sleep(min(0.5 * (2**attempt), 4.0, remaining))

        raise last_exc or RuntimeError("LLM request failed.")

    async def chat_json(self, system: str, user: str, max_retries: int | None = None) -> LLMResult:
        retries = max_retries or settings.llm_max_retries
        deadline_at = self._deadline_at()
        prompt = user
        last = LLMResult(content="")
        span = begin_span(
            "llm_call",
            "chat_json",
            input_summary={
                "model": settings.llm_model,
                "max_retries": retries,
                "system_chars": len(system),
                "user_chars": len(user),
                "json_mode": True,
            },
        )
        attempts = 0
        try:
            for i in range(retries):
                attempts = i + 1
                last = await self._with_transport_retry(
                    lambda: self._chat_once(
                        system=system,
                        user=prompt,
                        temperature=None,
                        model=settings.llm_model,
                    ),
                    deadline_at=deadline_at,
                )
                parsed = self.extract_json(last.content)
                if parsed is not None:
                    result = LLMResult(
                        content=json.dumps(parsed, ensure_ascii=False),
                        input_tokens=last.input_tokens,
                        output_tokens=last.output_tokens,
                    )
                    span.end(
                        output_summary={
                            "parsed_json": True,
                            "attempts": attempts,
                            "output_chars": len(result.content),
                            "input_tokens": result.input_tokens,
                            "output_tokens": result.output_tokens,
                        }
                    )
                    return result
                prompt = (
                    f"{user}\n\n"
                    "上一轮输出无法解析为 JSON。"
                    "请只输出 JSON 对象，不要包含 ```json 代码块或额外解释。"
                )
                if i == retries - 1:
                    span.end(
                        status="failed",
                        output_summary={
                            "parsed_json": False,
                            "attempts": attempts,
                            "raw_output_preview": last.content[:300],
                            "input_tokens": last.input_tokens,
                            "output_tokens": last.output_tokens,
                        },
                        error="json_parse_failed",
                    )
                    return last
            return last
        except Exception as exc:
            span.end(
                status="failed",
                output_summary={"attempts": attempts},
                error=str(exc),
            )
            raise

    @staticmethod
    def _deadline_at() -> float:
        request_timeout = max(settings.llm_request_timeout_seconds, 0.1)
        retry_deadline = max(settings.llm_retry_deadline_seconds, request_timeout)
        return time.monotonic() + retry_deadline

    @staticmethod
    def extract_json(text: str) -> dict | None:
        cleaned = text.strip().strip("`")
        try:
            data = json.loads(cleaned)
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
            if not match:
                return None
            try:
                data = json.loads(match.group())
                return data if isinstance(data, dict) else None
            except json.JSONDecodeError:
                return None
