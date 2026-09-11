"""Adapter exposing incident memory search to tools (opens its own unit of work per call)."""

from __future__ import annotations

from aegis.domain.memory import MemoryMatch, SimilarIncidentQuery
from aegis.memory.service import IncidentMemoryService
from aegis.ports.llm import EmbeddingProvider
from aegis.ports.repositories import UnitOfWorkFactory


class MemorySearchAdapter:
    def __init__(
        self, uow_factory: UnitOfWorkFactory, embeddings: EmbeddingProvider | None
    ) -> None:
        self.uow_factory = uow_factory
        self.embeddings = embeddings

    async def __call__(self, query: SimilarIncidentQuery) -> list[MemoryMatch]:
        async with self.uow_factory() as uow:
            service = IncidentMemoryService(uow.memories, embeddings=self.embeddings)
            return await service.search(query)
