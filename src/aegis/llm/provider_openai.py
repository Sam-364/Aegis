"""OpenAI (and OpenAI-compatible) provider using the Responses API with strict structured output."""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Any

import openai
from openai import AsyncOpenAI
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from aegis.domain.errors import LLMError, LLMMalformedOutput, LLMUnavailable
from aegis.llm.breaker import CircuitBreaker
from aegis.logging import get_logger
from aegis.ports.llm import LLMUsage, StructuredResult, Tier
from aegis.telemetry.metrics import LLM_CALLS_TOTAL, LLM_LATENCY_SECONDS, LLM_TOKENS_TOTAL
from aegis.telemetry.tracing import tracer

log = get_logger(__name__)

_REASONING_PREFIXES = ("gpt-5", "o1", "o3", "o4")


class OpenAIProvider:
    def __init__(
        self,
        *,
        api_key: str,
        reasoner_model: str,
        fast_model: str,
        base_url: str | None = None,
        reasoning_effort: str = "low",
        timeout: float = 60.0,
        max_retries: int = 2,
        max_output_tokens: int = 4000,
        breaker_failures: int = 3,
        breaker_reset_seconds: float = 60.0,
        name: str = "openai",
        client: AsyncOpenAI | None = None,
    ) -> None:
        self._name = name
        self.models: dict[str, str] = {"reasoner": reasoner_model, "fast": fast_model}
        self.reasoning_effort = reasoning_effort
        self.max_output_tokens = max_output_tokens
        self.client = client or AsyncOpenAI(
            api_key=api_key, base_url=base_url, timeout=timeout, max_retries=max_retries
        )
        self.breaker = CircuitBreaker(
            failures=breaker_failures, reset_seconds=breaker_reset_seconds
        )

    @property
    def name(self) -> str:
        return self._name

    def model_for(self, tier: Tier) -> str:
        return self.models[tier]

    async def healthy(self) -> bool:
        return not self.breaker.open

    async def complete_structured[T: BaseModel](
        self,
        *,
        schema: type[T],
        system: str,
        user: str,
        tier: Tier = "reasoner",
        max_output_tokens: int | None = None,
        cache_key: str | None = None,
    ) -> StructuredResult[T]:
        if self.breaker.open:
            raise LLMUnavailable("LLM circuit breaker is open", details=self.breaker.snapshot())
        model = self.model_for(tier)
        started = time.perf_counter()
        span = tracer().start_span(
            "aegis.llm.complete", attributes={"aegis.model": model, "aegis.schema": schema.__name__}
        )
        try:
            response = await self._parse(
                model=model,
                schema=schema,
                system=system,
                user=user,
                max_output_tokens=max_output_tokens,
                cache_key=cache_key,
            )
            parsed = response.output_parsed
            repaired = False
            if parsed is None:
                # One repair attempt: hand the model its own output and the schema again.
                raw = getattr(response, "output_text", "") or ""
                repair_user = (
                    f"{user}\n\nYour previous answer was not valid for the required schema. "
                    f"Previous answer:\n{raw[:4000]}\n\nAnswer again with ONLY valid JSON."
                )
                response = await self._parse(
                    model=model,
                    schema=schema,
                    system=system,
                    user=repair_user,
                    max_output_tokens=max_output_tokens,
                    cache_key=cache_key,
                )
                parsed = response.output_parsed
                repaired = True
                if parsed is None:
                    raise LLMMalformedOutput(
                        f"model {model} did not return valid {schema.__name__}",
                        details={
                            "status": getattr(response, "status", None),
                            "incomplete": str(getattr(response, "incomplete_details", "")),
                        },
                    )
        except (
            openai.APITimeoutError,
            openai.APIConnectionError,
            openai.RateLimitError,
            openai.InternalServerError,
        ) as exc:
            self.breaker.record_failure()
            LLM_CALLS_TOTAL.labels(model, schema.__name__, "unavailable").inc()
            span.end()
            raise LLMUnavailable(f"LLM unavailable: {type(exc).__name__}: {exc}") from exc
        except openai.APIStatusError as exc:
            self.breaker.record_failure()
            LLM_CALLS_TOTAL.labels(model, schema.__name__, "error").inc()
            span.end()
            raise LLMError(f"LLM request failed: {exc.status_code} {exc.message}") from exc
        except PydanticValidationError as exc:
            self.breaker.record_failure()
            LLM_CALLS_TOTAL.labels(model, schema.__name__, "malformed").inc()
            span.end()
            raise LLMMalformedOutput(f"schema validation failed: {exc}") from exc
        self.breaker.record_success()
        usage = self._usage(model, response, time.perf_counter() - started)
        LLM_CALLS_TOTAL.labels(model, schema.__name__, "ok").inc()
        LLM_TOKENS_TOTAL.labels(model, "input").inc(usage.input_tokens)
        LLM_TOKENS_TOTAL.labels(model, "output").inc(usage.output_tokens)
        LLM_TOKENS_TOTAL.labels(model, "cached_input").inc(usage.cached_input_tokens)
        LLM_TOKENS_TOTAL.labels(model, "reasoning").inc(usage.reasoning_tokens)
        LLM_LATENCY_SECONDS.labels(model).observe(usage.latency_ms / 1000)
        span.set_attribute("aegis.input_tokens", usage.input_tokens)
        span.set_attribute("aegis.output_tokens", usage.output_tokens)
        span.end()
        log.info(
            "llm.completion",
            model=model,
            tier=tier,
            schema=schema.__name__,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cached=usage.cached_input_tokens,
            reasoning=usage.reasoning_tokens,
            latency_ms=round(usage.latency_ms),
            repaired=repaired,
        )
        return StructuredResult[T](
            value=parsed,
            usage=usage,
            raw_text=getattr(response, "output_text", "") or "",
            repaired=repaired,
        )

    async def _parse(
        self,
        *,
        model: str,
        schema: type[BaseModel],
        system: str,
        user: str,
        max_output_tokens: int | None,
        cache_key: str | None,
    ) -> Any:
        kwargs: dict[str, Any] = {
            "model": model,
            "instructions": system,
            "input": user,
            "text_format": schema,
            "max_output_tokens": max_output_tokens or self.max_output_tokens,
            "store": False,
        }
        if model.startswith(_REASONING_PREFIXES):
            kwargs["reasoning"] = {"effort": self.reasoning_effort}
            kwargs["text"] = {"verbosity": "low"}
        else:
            kwargs["temperature"] = 0.1
        if cache_key:
            kwargs["prompt_cache_key"] = cache_key[:64]
        return await self.client.responses.parse(**kwargs)

    @staticmethod
    def _usage(model: str, response: Any, elapsed: float) -> LLMUsage:
        u = getattr(response, "usage", None)
        cached = reasoning = 0
        if u is not None:
            details_in = getattr(u, "input_tokens_details", None)
            details_out = getattr(u, "output_tokens_details", None)
            cached = int(getattr(details_in, "cached_tokens", 0) or 0)
            reasoning = int(getattr(details_out, "reasoning_tokens", 0) or 0)
        return LLMUsage(
            model=model,
            input_tokens=int(getattr(u, "input_tokens", 0) or 0),
            output_tokens=int(getattr(u, "output_tokens", 0) or 0),
            cached_input_tokens=cached,
            reasoning_tokens=reasoning,
            latency_ms=elapsed * 1000,
        )


class OpenAIEmbeddingProvider:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        dimensions: int,
        base_url: str | None = None,
        timeout: float = 30.0,
        client: AsyncOpenAI | None = None,
    ) -> None:
        self._model = model
        self._dimensions = dimensions
        self.client = client or AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=timeout)

    @property
    def model(self) -> str:
        return self._model

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            response = await self.client.embeddings.create(
                model=self._model, input=list(texts), dimensions=self._dimensions
            )
        except (
            openai.APITimeoutError,
            openai.APIConnectionError,
            openai.RateLimitError,
            openai.InternalServerError,
        ) as exc:
            raise LLMUnavailable(f"embedding provider unavailable: {exc}") from exc
        except openai.APIStatusError as exc:
            raise LLMError(f"embedding request failed: {exc.status_code} {exc.message}") from exc
        return [list(item.embedding) for item in response.data]
