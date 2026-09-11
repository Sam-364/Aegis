from __future__ import annotations

import asyncio
import os
from datetime import timedelta
from pathlib import Path

import pytest
import pytest_asyncio

from aegis.domain.base import Actor
from aegis.domain.ids import IncidentId
from aegis.domain.incident import IncidentEvent
from aegis.infrastructure.redis.adapters import (
    RedisBaselineStore,
    RedisEventPublisher,
    RedisLeaderLock,
    RedisRateLimiter,
    build_redis,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]
REDIS_URL = os.environ.get("AEGIS_TEST_REDIS_URL", "redis://localhost:6389/0")
ROOT = Path(__file__).resolve().parents[2]


@pytest_asyncio.fixture(loop_scope="module")
async def redis():  # type: ignore[no-untyped-def]
    client = build_redis(REDIS_URL)
    try:
        await client.ping()
    except Exception as exc:
        pytest.skip(f"redis not available: {exc}")
    await client.flushdb()
    try:
        yield client
    finally:
        await client.aclose()


async def test_pubsub_roundtrip_per_incident_and_global(redis) -> None:  # type: ignore[no-untyped-def]
    pub = RedisEventPublisher(redis)
    import uuid

    incident_id = IncidentId(uuid.uuid4())
    other_id = IncidentId(uuid.uuid4())
    received: list[str] = []

    async def consume() -> None:
        async for ev in pub.subscribe(incident_id):
            received.append(ev.title)
            if len(received) == 2:
                return

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.3)  # let the subscription register
    await pub.publish(
        IncidentEvent(incident_id=other_id, type="x", actor=Actor.system(), title="other", seq=1)
    )
    await pub.publish(
        IncidentEvent(incident_id=incident_id, type="x", actor=Actor.system(), title="one", seq=1)
    )
    await pub.publish(
        IncidentEvent(incident_id=incident_id, type="x", actor=Actor.system(), title="two", seq=2)
    )
    await asyncio.wait_for(task, timeout=5)
    assert received == ["one", "two"]


async def test_leader_lock_is_exclusive_and_renewable(redis) -> None:  # type: ignore[no-untyped-def]
    a, b = RedisLeaderLock(redis), RedisLeaderLock(redis)
    assert await a.acquire("det", timedelta(seconds=5))
    assert not await b.acquire("det", timedelta(seconds=5))
    assert await a.renew("det", timedelta(seconds=5))
    assert not await b.renew("det", timedelta(seconds=5))
    await b.release("det")  # not the holder: no effect
    assert not await b.acquire("det", timedelta(seconds=5))
    await a.release("det")
    assert await b.acquire("det", timedelta(seconds=5))


async def test_rate_limiter_window(redis) -> None:  # type: ignore[no-untyped-def]
    rl = RedisRateLimiter(redis)
    results = [await rl.allow("k", 3, 60) for _ in range(5)]
    assert [ok for ok, _ in results] == [True, True, True, False, False]
    assert [rem for _, rem in results] == [2, 1, 0, 0, 0]


async def test_baseline_store_roundtrip(redis) -> None:  # type: ignore[no-untyped-def]
    store = RedisBaselineStore(redis)
    await store.save("detector", {"baselines": {"a|b": {"mean": 1.0}}}, ttl_seconds=60)
    assert (await store.load("detector"))["baselines"]["a|b"]["mean"] == 1.0
    assert await store.load("missing") is None
    assert any(k.endswith("detector") for k in await store.keys("det"))


async def test_bootstrap_wires_real_adapters(pg_engine) -> None:  # type: ignore[no-untyped-def]
    """Build the real runtime against the test Postgres/Redis (no Temporal), then use it."""
    from aegis.application.bootstrap import build_runtime, readiness
    from aegis.config import Settings

    settings = Settings(
        llm_provider="disabled",
        temporal_enabled=False,
        database_url=os.environ.get(
            "AEGIS_TEST_DATABASE_URL", "postgresql+asyncpg://aegis:aegis@localhost:5439/aegis_test"
        ),
        redis_url=REDIS_URL,
        simulator_url="http://localhost:1",
        _env_file=None,
    )  # type: ignore[call-arg]
    container, res = await build_runtime(settings, role="api", root=ROOT)
    try:
        checks = await readiness(settings, res)
        assert checks["database"]["ok"] is True and checks["redis"]["ok"] is True
        assert checks["simulator"]["ok"] is False  # nothing listens on port 1
        assert container.workflows is None
        items, _ = await container.incidents.list_incidents(limit=1)
        assert isinstance(items, list)
        stats = await container.incidents.stats()
        assert "active" in stats
    finally:
        await res.aclose()


async def test_definitions_snapshot_is_written_once_per_version(pg_engine) -> None:  # type: ignore[no-untyped-def]
    """An incident records which flow ran; this table records what that version contained, so the
    incident stays auditable after the YAML moves on. Restarting a process must add nothing."""
    from sqlalchemy import text

    from aegis.domain.clock import utcnow
    from aegis.flow.loader import load_flow_dir
    from aegis.flow.registry import FlowRegistry
    from aegis.infrastructure.postgres.definitions import snapshot_definitions
    from aegis.policy.loader import load_policy_dir
    from aegis.tools.builtin import build_default_registry

    async with pg_engine.begin() as conn:
        await conn.execute(text("TRUNCATE TABLE definitions"))
    flows = FlowRegistry(load_flow_dir(ROOT / "flows")).all()
    rules = load_policy_dir(ROOT / "policies")
    tools = build_default_registry().specs()
    first = await snapshot_definitions(
        pg_engine, flows=flows, policy_rules=rules, tools=tools, now=utcnow()
    )
    again = await snapshot_definitions(
        pg_engine, flows=flows, policy_rules=rules, tools=tools, now=utcnow()
    )
    assert first == len(flows) + 1 + len(tools)
    assert again == 0  # same checksums: a restart records nothing new
    async with pg_engine.begin() as conn:
        by_kind = dict(
            (await conn.execute(text("SELECT kind, count(*) FROM definitions GROUP BY kind"))).all()
        )
        stored = (
            await conn.execute(
                text("SELECT document FROM definitions WHERE kind='flow' AND name=:n"),
                {"n": flows[0].name},
            )
        ).scalar_one()
    assert by_kind == {"flow": len(flows), "policy": 1, "tool": len(tools)}
    assert stored["phases"] and stored["version"] == flows[0].version

    # an edited pack is a new row, so both texts remain auditable
    edited = flows[0].model_copy(update={"checksum": "deadbeefdeadbeef"})
    assert (
        await snapshot_definitions(
            pg_engine, flows=[edited], policy_rules=[], tools=[], now=utcnow()
        )
        == 1
    )
