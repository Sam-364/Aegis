"""Integration tests need the throwaway containers started by `make test-infra`:

    docker run -d --name aegis-test-postgres -e POSTGRES_USER=aegis -e POSTGRES_PASSWORD=aegis \
        -e POSTGRES_DB=aegis_test -p 5439:5432 pgvector/pgvector:pg17
    docker run -d --name aegis-test-redis -p 6389:6379 redis:7-alpine
"""

from __future__ import annotations

import os

import pytest
import pytest_asyncio
from sqlalchemy import text

from aegis.infrastructure.postgres.migrations import upgrade_head_async
from aegis.infrastructure.postgres.session import PostgresUnitOfWorkFactory, create_engine

TEST_DB_URL = os.environ.get(
    "AEGIS_TEST_DATABASE_URL", "postgresql+asyncpg://aegis:aegis@localhost:5439/aegis_test"
)
TEST_REDIS_URL = os.environ.get("AEGIS_TEST_REDIS_URL", "redis://localhost:6389/0")

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def pg_engine():  # type: ignore[no-untyped-def]
    sync_url = TEST_DB_URL.replace("postgresql+asyncpg://", "postgresql://")
    try:
        await upgrade_head_async(sync_url)
    except Exception as exc:
        pytest.skip(f"postgres not available: {exc}")
    engine = create_engine(TEST_DB_URL, pool_size=5, max_overflow=5)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture(loop_scope="module")
async def uow_factory(pg_engine):  # type: ignore[no-untyped-def]
    async with pg_engine.begin() as conn:
        for table in [
            "agent_steps",
            "agent_runs",
            "tool_executions",
            "action_executions",
            "action_plans",
            "approvals",
            "hypotheses",
            "evidence_relations",
            "evidence",
            "incident_events",
            "incident_memories",
            "notifications",
            "incidents",
        ]:
            await conn.execute(text(f"TRUNCATE TABLE {table} CASCADE"))
        await conn.execute(
            text("DELETE FROM audit_events") if False else text("SELECT 1")
        )  # append-only
    return PostgresUnitOfWorkFactory(pg_engine)
