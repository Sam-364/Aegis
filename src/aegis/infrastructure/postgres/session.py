"""Engine, sessions and the Postgres unit of work."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from aegis.infrastructure.postgres.repositories import (
    PostgresActionPlanRepository,
    PostgresAgentRunRepository,
    PostgresApprovalRepository,
    PostgresAuditRepository,
    PostgresEvidenceRepository,
    PostgresHypothesisRepository,
    PostgresIncidentRepository,
    PostgresMemoryRepository,
    PostgresNotificationRepository,
    PostgresToolExecutionRepository,
)


def create_engine(
    url: str, *, pool_size: int = 10, max_overflow: int = 10, echo: bool = False
) -> AsyncEngine:
    return create_async_engine(
        url,
        pool_size=pool_size,
        max_overflow=max_overflow,
        echo=echo,
        pool_pre_ping=True,
        pool_recycle=1800,
    )


class PostgresUnitOfWork:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = session_factory
        self._session: AsyncSession | None = None
        self._committed = False

    async def __aenter__(self) -> PostgresUnitOfWork:
        self._session = self._factory()
        await self._session.begin()
        s = self._session
        self.incidents = PostgresIncidentRepository(s)
        self.evidence = PostgresEvidenceRepository(s)
        self.hypotheses = PostgresHypothesisRepository(s)
        self.action_plans = PostgresActionPlanRepository(s)
        self.approvals = PostgresApprovalRepository(s)
        self.audit = PostgresAuditRepository(s)
        self.agent_runs = PostgresAgentRunRepository(s)
        self.tool_executions = PostgresToolExecutionRepository(s)
        self.memories = PostgresMemoryRepository(s)
        self.notifications = PostgresNotificationRepository(s)
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        assert self._session is not None
        try:
            if not self._committed and self._session.in_transaction():
                await self._session.rollback()
        finally:
            await self._session.close()
            self._session = None

    async def commit(self) -> None:
        assert self._session is not None
        await self._session.commit()
        self._committed = True
        # allow further work in the same UoW (new transaction) — callers usually exit right after
        await self._session.begin()
        self._committed = False

    async def rollback(self) -> None:
        assert self._session is not None
        await self._session.rollback()
        await self._session.begin()


class PostgresUnitOfWorkFactory:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine
        self.session_factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

    def __call__(self) -> PostgresUnitOfWork:
        return PostgresUnitOfWork(self.session_factory)


@asynccontextmanager
async def engine_lifespan(url: str, **kwargs: object) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(url, **kwargs)  # type: ignore[arg-type]
    try:
        yield engine
    finally:
        await engine.dispose()
