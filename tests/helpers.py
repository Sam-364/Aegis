"""Shared builders for runtime tests: simulator + registry + policy + executor + in-memory repos."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from aegis.domain.base import Actor
from aegis.domain.enums import Environment, Role, Severity, SignalKind
from aegis.domain.flow import BudgetUsage, ExecutionBudget, FlowPack, FlowPhase
from aegis.domain.incident import AnomalySignal, Incident
from aegis.domain.tool import ToolCallRequest
from aegis.flow.loader import load_flow_dir
from aegis.flow.registry import FlowRegistry
from aegis.infrastructure.memory.repositories import InMemoryStore, InMemoryUnitOfWork
from aegis.infrastructure.simulator.inprocess import (
    InProcessSimulatorGateway,
    InProcessSimulatorTelemetry,
)
from aegis.policy.engine import PolicyEngine
from aegis.policy.loader import load_policy_dir
from aegis.simulator.engine import SimulationEngine
from aegis.tools.authorizer import ToolAuthorizer
from aegis.tools.builtin import build_default_registry
from aegis.tools.context import ToolContext
from aegis.tools.executor import ToolExecutor
from aegis.tools.registry import ToolRegistry

ROOT = Path(__file__).resolve().parents[1]


class SimClock:
    def __init__(self, engine: SimulationEngine) -> None:
        self.engine = engine

    def now(self) -> datetime:
        return self.engine.now


@dataclass
class Runtime:
    engine: SimulationEngine
    registry: ToolRegistry
    flows: FlowRegistry
    policy: PolicyEngine
    authorizer: ToolAuthorizer
    executor: ToolExecutor
    uow: InMemoryUnitOfWork
    store: InMemoryStore
    telemetry: InProcessSimulatorTelemetry
    gateway: InProcessSimulatorGateway
    environment: Environment

    def incident(
        self,
        *,
        severity: Severity = Severity.SEV2,
        services: list[str] | None = None,
        kinds: list[SignalKind] | None = None,
    ) -> Incident:
        now = self.engine.now
        services = services or ["api-gateway", "redis"]
        kinds = kinds or [SignalKind.LATENCY]
        signals = [
            AnomalySignal(
                service=s,
                metric="latency_p95_ms",
                kind=k,
                observed_value=900,
                baseline_value=200,
                deviation_sigma=9,
                detector="t",
                detected_at=now,
                window_seconds=15,
                description=f"{s} anomaly",
            )
            for s, k in zip(services, kinds * len(services), strict=False)
        ]
        inc = Incident(
            title="API latency regression",
            severity=severity,
            signals=signals,
            affected_services=list(services),
            detected_at=now,
            number=1042,
            environment=self.environment,
        )
        return inc

    def ctx(
        self,
        incident: Incident,
        *,
        flow: FlowPack | None = None,
        phase: str = "investigate",
        actor: Actor | None = None,
        action_plan_id: uuid.UUID | None = None,
        approval_id: uuid.UUID | None = None,
        agent_run_id: uuid.UUID | None = None,
    ) -> ToolContext:
        flow = flow or self.flows.get("incident-investigation")
        return ToolContext(
            incident=incident,
            flow=flow,
            phase=flow.phase(phase),
            actor=actor or Actor.agent(str(agent_run_id or "run")),
            environment=self.environment,
            telemetry=self.telemetry,
            infrastructure=self.gateway,
            clock=SimClock(self.engine),
            agent_run_id=agent_run_id,
            action_plan_id=action_plan_id,
            approval_id=approval_id,
            known_components=frozenset(self.engine.component_names()),
        )

    def request(
        self,
        incident: Incident,
        tool: str,
        args: dict[str, Any] | None = None,
        *,
        actor: Actor | None = None,
        action_plan_id: uuid.UUID | None = None,
        agent_run_id: uuid.UUID | None = None,
        phase: str | None = None,
    ) -> ToolCallRequest:
        return ToolCallRequest(
            incident_id=incident.id,
            tool_name=tool,
            arguments=args or {},
            requested_by=actor or Actor.agent(str(agent_run_id or "run")),
            agent_run_id=agent_run_id,
            action_plan_id=action_plan_id,
            phase=phase,
            requested_at=self.engine.now,
        )


def phase_of(flow: FlowPack, name: str) -> FlowPhase:
    return flow.phase(name)


def build_runtime(
    *, environment: Environment = Environment.DEVELOPMENT, seed: int = 3, warmup: int = 300
) -> Runtime:
    engine = SimulationEngine(seed=seed)
    engine.warmup(warmup)
    registry = build_default_registry()
    flows = FlowRegistry(load_flow_dir(ROOT / "flows"))
    flows.validate_tools(set(registry.names()))
    policy = PolicyEngine(load_policy_dir(ROOT / "policies"))
    authorizer = ToolAuthorizer(registry, policy, environment=environment)
    store = InMemoryStore()
    uow = InMemoryUnitOfWork(store)
    executor = ToolExecutor(
        registry,
        authorizer,
        ledger=uow.tool_executions,
        evidence=uow.evidence,
        audit=uow.audit,
        clock=SimClock(engine),
    )
    return Runtime(
        engine=engine,
        registry=registry,
        flows=flows,
        policy=policy,
        authorizer=authorizer,
        executor=executor,
        uow=uow,
        store=store,
        telemetry=InProcessSimulatorTelemetry(engine),
        gateway=InProcessSimulatorGateway(engine),
        environment=environment,
    )


def budget() -> tuple[ExecutionBudget, BudgetUsage]:
    return ExecutionBudget(), BudgetUsage()


def human(role: Role = Role.OPERATOR, name: str = "ops") -> Actor:
    return Actor.human(name, frozenset({role}))
