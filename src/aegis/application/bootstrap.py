"""Build a fully wired runtime for a process role (api, worker, detector) from settings."""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import redis.asyncio as aioredis
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from temporalio.client import Client
from temporalio.contrib.opentelemetry import TracingInterceptor
from temporalio.contrib.pydantic import pydantic_data_converter

from aegis.application.container import RuntimeContainer, build_container
from aegis.config import Settings
from aegis.infrastructure.postgres.definitions import snapshot_definitions
from aegis.infrastructure.postgres.migrations import schema_is_current
from aegis.infrastructure.postgres.session import PostgresUnitOfWorkFactory, create_engine
from aegis.infrastructure.prometheus.provider import PrometheusTelemetryProvider
from aegis.infrastructure.redis.adapters import (
    RedisBaselineStore,
    RedisEventPublisher,
    RedisLeaderLock,
    RedisRateLimiter,
    build_redis,
)
from aegis.infrastructure.simulator.control import SimulatorControlClient
from aegis.infrastructure.simulator.http import HttpSimulatorGateway, HttpSimulatorTelemetry
from aegis.infrastructure.temporal.controller import TemporalWorkflowController
from aegis.llm import build_embedding_provider, build_llm_provider
from aegis.logging import configure_logging, get_logger
from aegis.ports.telemetry import TelemetryProvider
from aegis.telemetry.tracing import configure_tracing, instrument_httpx, instrument_sqlalchemy

log = get_logger(__name__)
Role = Literal["api", "worker", "detector"]


@dataclass
class Resources:
    engine: Any = None
    redis: aioredis.Redis | None = None
    temporal: Client | None = None
    checkpointer_cm: Any = None
    closers: list[Any] = field(default_factory=list)
    lock: RedisLeaderLock | None = None
    rate_limiter: RedisRateLimiter | None = None
    baseline_store: RedisBaselineStore | None = None
    simulator_control: SimulatorControlClient | None = None

    async def aclose(self) -> None:
        for closer in self.closers:
            with contextlib.suppress(Exception):
                await closer()
        if self.checkpointer_cm is not None:
            with contextlib.suppress(Exception):
                await self.checkpointer_cm.__aexit__(None, None, None)
        if self.redis is not None:
            with contextlib.suppress(Exception):
                await self.redis.aclose()
        if self.engine is not None:
            await self.engine.dispose()


async def build_runtime(
    settings: Settings,
    *,
    role: Role,
    root: Path | None = None,
    temporal_required: bool | None = None,
) -> tuple[RuntimeContainer, Resources]:
    configure_logging(settings.log_level, settings.log_format, service=f"aegis-{role}")
    configure_tracing(
        enabled=settings.otel_enabled,
        endpoint=settings.otel_exporter_endpoint,
        service_name=f"aegis-{role}",
        environment=settings.environment.value,
    )
    instrument_httpx()
    res = Resources()

    res.engine = create_engine(
        settings.database_url,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        echo=settings.database_echo,
    )
    instrument_sqlalchemy(res.engine)
    uow_factory = PostgresUnitOfWorkFactory(res.engine)

    res.redis = build_redis(settings.redis_url)
    publisher = RedisEventPublisher(res.redis)
    res.lock = RedisLeaderLock(res.redis)
    res.rate_limiter = RedisRateLimiter(res.redis)
    res.baseline_store = RedisBaselineStore(res.redis)

    sim_telemetry = HttpSimulatorTelemetry(settings.simulator_url)
    res.closers.append(sim_telemetry.aclose)
    telemetry: TelemetryProvider = sim_telemetry
    if settings.telemetry_provider == "prometheus":
        telemetry = PrometheusTelemetryProvider(settings.prometheus_url, fallback=sim_telemetry)
    infrastructure = HttpSimulatorGateway(settings.simulator_url)
    res.closers.append(infrastructure.aclose)
    res.simulator_control = SimulatorControlClient(settings.simulator_url)
    res.closers.append(res.simulator_control.aclose)

    llm = build_llm_provider(settings)
    embeddings = build_embedding_provider(settings)

    workflows = None
    need_temporal = settings.temporal_enabled if temporal_required is None else temporal_required
    if need_temporal:
        interceptors = [TracingInterceptor()] if settings.otel_enabled else []
        res.temporal = await Client.connect(
            settings.temporal_address,
            namespace=settings.temporal_namespace,
            data_converter=pydantic_data_converter,
            interceptors=interceptors,
        )
        workflows = TemporalWorkflowController(
            res.temporal,
            task_queue=settings.temporal_task_queue,
            approval_timeout_seconds=settings.approval_timeout_seconds,
            phase_timeout_seconds=settings.agent_phase_timeout_seconds,
        )

    checkpointer = None
    if role == "worker":
        res.checkpointer_cm = AsyncPostgresSaver.from_conn_string(settings.sync_database_url)
        checkpointer = await res.checkpointer_cm.__aenter__()
        await checkpointer.setup()

    container = build_container(
        settings,
        telemetry=telemetry,
        infrastructure=infrastructure,
        uow_factory=uow_factory,
        llm=llm,
        embeddings=embeddings,
        publisher=publisher,
        workflows=workflows,
        checkpointer=checkpointer,
        root=root,
    )
    # Record the exact programme this process is running, for after-the-fact audit.
    with contextlib.suppress(Exception):
        await snapshot_definitions(
            res.engine,
            flows=container.flows.all(),
            policy_rules=container.policy.rules,
            tools=container.registry.specs(),
            now=container.clock.now(),
        )
    if settings.api_auth_mode == "disabled":
        log.warning(
            "runtime.api_authentication_disabled",
            detail="every caller is treated as a local admin, including approvals; "
            "set AEGIS_API_AUTH_MODE=api_key before exposing this port",
            host=settings.api_host,
        )
    log.info(
        "runtime.built",
        role=role,
        environment=settings.environment.value,
        llm=llm.name if llm else "disabled",
        telemetry=settings.telemetry_provider,
        temporal=bool(workflows),
    )
    return container, res


async def readiness(settings: Settings, res: Resources) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    try:
        ok, current, head = await schema_is_current(res.engine, settings.sync_database_url)
        checks["database"] = {"ok": ok, "revision": current, "head": head}
    except Exception as exc:
        checks["database"] = {"ok": False, "error": str(exc)[:200]}
    try:
        assert res.redis is not None
        checks["redis"] = {"ok": bool(await res.redis.ping())}
    except Exception as exc:
        checks["redis"] = {"ok": False, "error": str(exc)[:200]}
    if settings.temporal_enabled:
        try:
            assert res.temporal is not None
            await res.temporal.service_client.check_health()
            checks["temporal"] = {"ok": True}
        except Exception as exc:
            checks["temporal"] = {"ok": False, "error": str(exc)[:200]}
    try:
        assert res.simulator_control is not None
        checks["simulator"] = {"ok": (await res.simulator_control.health()).get("status") == "ok"}
    except Exception as exc:
        checks["simulator"] = {"ok": False, "error": str(exc)[:200]}
    return checks
