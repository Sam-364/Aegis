"""Memory eval: recall@1 of the right past incident for a fresh similar incident."""

from __future__ import annotations

import uuid

from aegis.domain.enums import Severity
from aegis.domain.memory import IncidentMemory, SimilarIncidentQuery
from aegis.infrastructure.memory.repositories import InMemoryStore, InMemoryUnitOfWork
from aegis.llm.provider_scripted import HashEmbeddingProvider
from aegis.memory.service import IncidentMemoryService
from aegis.ports.llm import EmbeddingProvider
from evals.harness.core import CaseResult, SuiteResult, run_cases

LIBRARY = [
    (
        "redis-leak",
        "Redis connection leak in order-service",
        ["redis connections +300%", "api-gateway latency up"],
        ["api-gateway", "redis", "order-service"],
        "order-service leaked Redis connections",
        "order-service",
        "restart order-service",
    ),
    (
        "bad-deploy",
        "payment-service regression after 2.4.0",
        ["payment-service error_rate 35%", "api-gateway 5xx"],
        ["payment-service", "api-gateway"],
        "deployment 2.4.0 introduced a checkout bug",
        "payment-service",
        "rollback payment-service to 2.3.7",
    ),
    (
        "db-pool",
        "Postgres connection exhaustion",
        ["postgres connections 100%", "idle in transaction"],
        ["postgres", "user-service", "api-gateway"],
        "user-service leaves sessions idle in transaction",
        "user-service",
        "rotate user-service connection pool",
    ),
    (
        "inventory-crash",
        "inventory-service OOM crash cascade",
        ["inventory-service down", "order-service errors"],
        ["inventory-service", "order-service", "api-gateway"],
        "inventory-service OOM killed",
        "inventory-service",
        "restart inventory-service",
    ),
    (
        "cpu",
        "notification-service CPU saturation",
        ["notification cpu 100%"],
        ["notification-service"],
        "single replica pinned by a burst of jobs",
        "notification-service",
        "scale notification-service to 3",
    ),
]

QUERIES = [
    (
        "redis-leak",
        "redis saturation, order-service waiting on redis pool, gateway latency",
        ["api-gateway", "redis"],
    ),
    ("bad-deploy", "payment errors rising after a new version was deployed", ["payment-service"]),
    (
        "db-pool",
        "database connections exhausted, services waiting for postgres",
        ["postgres", "user-service"],
    ),
    (
        "inventory-crash",
        "inventory unavailable, order service failing, ECONNREFUSED",
        ["inventory-service"],
    ),
    ("cpu", "notification service latency and cpu pressure", ["notification-service"]),
]


async def _seed(service: IncidentMemoryService) -> dict[str, uuid.UUID]:
    ids = {}
    for key, title, symptoms, services, root, root_svc, resolution in LIBRARY:
        m = IncidentMemory(
            incident_id=uuid.uuid4(),
            title=title,
            severity=Severity.SEV2,
            symptoms=symptoms,
            affected_services=services,
            root_cause=root,
            root_cause_service=root_svc,
            resolution=resolution,
        )
        m.embedding_text = m.to_embedding_text()
        await service.store(m)
        ids[key] = m.incident_id
    return ids


async def _case(
    key: str, text: str, services: list[str], embeddings: EmbeddingProvider | None, label: str
) -> CaseResult:
    service = IncidentMemoryService(
        InMemoryUnitOfWork(InMemoryStore()).memories, embeddings=embeddings
    )
    ids = await _seed(service)
    matches = await service.search(
        SimilarIncidentQuery(text=text, affected_services=services, limit=3)
    )
    order = [m.memory.incident_id for m in matches]
    rank = order.index(ids[key]) + 1 if ids[key] in order else 99
    return CaseResult(
        name=f"{label}/{key}",
        passed=rank == 1,
        score=1.0 / rank if rank < 99 else 0.0,
        details={
            "rank": rank,
            "top": matches[0].memory.title if matches else None,
            "similarity": round(matches[0].similarity, 3) if matches else None,
        },
    )


async def run(*, embeddings: EmbeddingProvider | None = None) -> SuiteResult:
    cases = {}
    for key, text, services in QUERIES:
        cases[f"hash/{key}"] = lambda k=key, t=text, s=services: _case(
            k, t, s, HashEmbeddingProvider(128), "hash"
        )
        cases[f"lexical/{key}"] = lambda k=key, t=text, s=services: _case(k, t, s, None, "lexical")
        if embeddings is not None:
            cases[f"openai/{key}"] = lambda k=key, t=text, s=services, e=embeddings: _case(
                k, t, s, e, "openai"
            )
    result = await run_cases("memory-recall", cases, threshold=0.8, concurrency=4)
    return result
