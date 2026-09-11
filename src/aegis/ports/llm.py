"""LLM and embedding ports."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal, Protocol, TypeVar

from pydantic import BaseModel, model_validator

from aegis.domain.text import sanitize_display_text, sanitize_tree

T = TypeVar("T", bound=BaseModel)
Tier = Literal["reasoner", "fast"]


class LLMUsage(BaseModel):
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    reasoning_tokens: int = 0
    latency_ms: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class StructuredResult[T: BaseModel](BaseModel):
    model_config = {"arbitrary_types_allowed": True}
    value: T
    usage: LLMUsage
    raw_text: str = ""
    repaired: bool = False

    @model_validator(mode="after")
    def _sanitize(self) -> StructuredResult[T]:
        """Every model answer enters the runtime here, so this is where hostile text dies."""
        cleaned = _sanitize_model(self.value)
        if cleaned is not self.value:
            object.__setattr__(self, "value", cleaned)
        return self


def _sanitize_model[M: BaseModel](model: M) -> M:
    updates: dict[str, Any] = {}
    for name in type(model).model_fields:
        current = getattr(model, name, None)
        cleaned = _sanitize_member(current)
        if cleaned is not current:
            updates[name] = cleaned
    return model.model_copy(update=updates) if updates else model


def _sanitize_member(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _sanitize_model(value)
    if isinstance(value, list):
        items = [_sanitize_member(v) for v in value]
        return items if any(a is not b for a, b in zip(items, value, strict=True)) else value
    if isinstance(value, str):
        cleaned = sanitize_display_text(value)
        return value if cleaned == value else cleaned
    if isinstance(value, dict):
        return sanitize_tree(value)
    return value


class LLMProvider(Protocol):
    """Structured-output completion. The runtime never asks for free text it must parse."""

    @property
    def name(self) -> str: ...

    def model_for(self, tier: Tier) -> str: ...

    async def complete_structured(
        self,
        *,
        schema: type[T],
        system: str,
        user: str,
        tier: Tier = "reasoner",
        max_output_tokens: int | None = None,
        cache_key: str | None = None,
    ) -> StructuredResult[T]: ...

    async def healthy(self) -> bool: ...


class EmbeddingProvider(Protocol):
    @property
    def model(self) -> str: ...

    @property
    def dimensions(self) -> int: ...

    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...
