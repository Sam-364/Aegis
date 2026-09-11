"""LLM providers. The runtime only ever asks for strict structured output."""

from __future__ import annotations

from aegis.config import Settings
from aegis.llm.provider_openai import OpenAIEmbeddingProvider, OpenAIProvider
from aegis.llm.provider_scripted import ScriptedProvider
from aegis.ports.llm import EmbeddingProvider, LLMProvider


def build_llm_provider(settings: Settings) -> LLMProvider | None:
    if settings.llm_provider == "disabled":
        return None
    if settings.llm_provider == "scripted":
        return ScriptedProvider()
    key = settings.llm_api_key.get_secret_value() if settings.llm_api_key else "unused"
    return OpenAIProvider(
        api_key=key,
        base_url=settings.llm_base_url,
        reasoner_model=settings.llm_reasoner_model,
        fast_model=settings.llm_fast_model,
        reasoning_effort=settings.llm_reasoning_effort,
        timeout=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
        max_output_tokens=settings.llm_max_output_tokens,
        breaker_failures=settings.llm_circuit_breaker_failures,
        breaker_reset_seconds=settings.llm_circuit_breaker_reset_seconds,
        name="openai" if settings.llm_provider == "openai" else "openai_compatible",
    )


def build_embedding_provider(settings: Settings) -> EmbeddingProvider | None:
    if settings.llm_provider in ("disabled", "scripted") or settings.llm_api_key is None:
        return None
    return OpenAIEmbeddingProvider(
        api_key=settings.llm_api_key.get_secret_value(),
        base_url=settings.llm_base_url,
        model=settings.llm_embedding_model,
        dimensions=settings.llm_embedding_dimensions,
        timeout=settings.llm_timeout_seconds,
    )


__all__ = ["OpenAIProvider", "ScriptedProvider", "build_embedding_provider", "build_llm_provider"]
