"""Scripted provider for tests and evals: deterministic, offline, inspectable."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from typing import Any

from pydantic import BaseModel

from aegis.domain.errors import LLMUnavailable
from aegis.ports.llm import LLMUsage, StructuredResult, Tier

Script = Callable[[type[BaseModel], str, str, str], BaseModel | dict[str, Any] | None]


class ScriptedProvider:
    """Answers from a script function or a queue. With no script it raises ``LLMUnavailable`` so
    callers exercise their deterministic fallback path."""

    def __init__(
        self,
        script: Script | None = None,
        *,
        queue: Sequence[BaseModel] | None = None,
        model: str = "scripted",
    ) -> None:
        self.script = script
        self.queue = list(queue or [])
        self.model = model
        self.calls: list[dict[str, Any]] = []
        self.fail_next: int = 0

    @property
    def name(self) -> str:
        return "scripted"

    def model_for(self, tier: Tier) -> str:
        return f"{self.model}-{tier}"

    async def healthy(self) -> bool:
        return self.script is not None or bool(self.queue)

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
        self.calls.append({"schema": schema.__name__, "system": system, "user": user, "tier": tier})
        if self.fail_next > 0:
            self.fail_next -= 1
            raise LLMUnavailable("scripted failure")
        answer: BaseModel | dict[str, Any] | None
        if self.queue:
            answer = self.queue.pop(0)
        elif self.script is not None:
            answer = self.script(schema, system, user, tier)
        else:
            raise LLMUnavailable("no script configured")
        if answer is None:
            raise LLMUnavailable("script returned no answer")
        value = (
            answer
            if isinstance(answer, schema)
            else schema.model_validate(answer if isinstance(answer, dict) else answer.model_dump())
        )
        tokens = len(user) // 4
        return StructuredResult[T](
            value=value,
            usage=LLMUsage(
                model=self.model_for(tier), input_tokens=tokens, output_tokens=64, latency_ms=1.0
            ),
        )


class HashEmbeddingProvider:
    """Deterministic pseudo-embeddings for tests: bag-of-words hashed into a fixed vector."""

    def __init__(self, dimensions: int = 64) -> None:
        self._dimensions = dimensions

    @property
    def model(self) -> str:
        return "hash-embedding"

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        out = []
        for text in texts:
            vec = [0.0] * self._dimensions
            for token in text.lower().replace("\n", " ").split():
                h = int(hashlib.md5(token.encode()).hexdigest(), 16)  # noqa: S324 - not security
                vec[h % self._dimensions] += 1.0
            norm = sum(v * v for v in vec) ** 0.5 or 1.0
            out.append([v / norm for v in vec])
        return out
