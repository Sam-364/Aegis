"""Dependency container. Adapters are injected so tests use in-memory/in-process implementations
and processes wire Postgres/Redis/HTTP in ``aegis.apps.bootstrap``."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver

from aegis.agent.runtime import AgentDependencies, AgentHooks, AgentRuntime
from aegis.application.approvals import ApprovalService
from aegis.application.incidents import IncidentService
from aegis.application.intake import IncidentIntakeService
from aegis.application.memory_search import MemorySearchAdapter
from aegis.application.notifications import NotificationService
from aegis.config import Settings
from aegis.domain.clock import Clock, SystemClock
from aegis.flow.loader import load_flow_dir
from aegis.flow.registry import FlowRegistry
from aegis.infrastructure.memory.repositories import InMemoryUnitOfWorkFactory
from aegis.policy.engine import PolicyEngine
from aegis.policy.loader import load_policy_dir
from aegis.ports.llm import EmbeddingProvider, LLMProvider
from aegis.ports.messaging import EventPublisher, WorkflowController
from aegis.ports.repositories import UnitOfWorkFactory
from aegis.ports.telemetry import InfrastructureGateway, TelemetryProvider
from aegis.tools.authorizer import ToolAuthorizer
from aegis.tools.builtin import build_default_registry
from aegis.tools.registry import ToolRegistry
from aegis.verification.engine import Sleeper, VerificationEngine

Heartbeat = Callable[[str], Awaitable[None]]


@dataclass
class RuntimeContainer:
    settings: Settings
    uow_factory: UnitOfWorkFactory
    registry: ToolRegistry
    flows: FlowRegistry
    policy: PolicyEngine
    authorizer: ToolAuthorizer
    telemetry: TelemetryProvider
    infrastructure: InfrastructureGateway
    llm: LLMProvider | None
    embeddings: EmbeddingProvider | None
    publisher: EventPublisher | None
    workflows: WorkflowController | None
    clock: Clock
    agent: AgentRuntime
    intake: IncidentIntakeService
    incidents: IncidentService
    approvals: ApprovalService
    notifications: NotificationService
    memory_search: MemorySearchAdapter
    sleeper: Sleeper | None = None

    def verification(self, *, sleep: Sleeper | None = None) -> VerificationEngine:
        return VerificationEngine(
            self.telemetry,
            clock=self.clock,
            sleep=sleep or self.sleeper,
            poll_seconds=self.settings.verification_poll_seconds,
        )

    def set_agent_hooks(self, hooks: AgentHooks) -> None:
        self.agent.deps.hooks = hooks


def build_container(
    settings: Settings,
    *,
    telemetry: TelemetryProvider,
    infrastructure: InfrastructureGateway,
    uow_factory: UnitOfWorkFactory | None = None,
    llm: LLMProvider | None = None,
    embeddings: EmbeddingProvider | None = None,
    publisher: EventPublisher | None = None,
    workflows: WorkflowController | None = None,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
    clock: Clock | None = None,
    sleeper: Sleeper | None = None,
    root: Path | None = None,
) -> RuntimeContainer:
    root = root or Path.cwd()
    clock = clock or SystemClock()
    factory: UnitOfWorkFactory = (
        uow_factory if uow_factory is not None else InMemoryUnitOfWorkFactory()
    )
    registry = build_default_registry()
    flows = FlowRegistry(load_flow_dir(_resolve(root, settings.flows_dir)))
    flows.validate_tools(
        set(registry.names()),
        {name for name in registry.names() if registry.spec(name).category.is_mutation},
    )
    policy = PolicyEngine(load_policy_dir(_resolve(root, settings.policies_dir)))
    authorizer = ToolAuthorizer(registry, policy, environment=settings.environment)
    memory_search = MemorySearchAdapter(factory, embeddings)
    agent = AgentRuntime(
        AgentDependencies(
            uow_factory=factory,
            registry=registry,
            authorizer=authorizer,
            flows=flows,
            telemetry=telemetry,
            infrastructure=infrastructure,
            environment=settings.environment,
            llm=llm,
            memory_search=memory_search,
            publisher=publisher,
            clock=clock,
            checkpointer=checkpointer,
        )
    )
    return RuntimeContainer(
        settings=settings,
        uow_factory=factory,
        registry=registry,
        flows=flows,
        policy=policy,
        authorizer=authorizer,
        telemetry=telemetry,
        infrastructure=infrastructure,
        llm=llm,
        embeddings=embeddings,
        publisher=publisher,
        workflows=workflows,
        clock=clock,
        agent=agent,
        intake=IncidentIntakeService(
            factory,
            flows,
            publisher=publisher,
            workflows=workflows,
            environment=settings.environment,
            tenant_id=settings.tenant_id,
            clock=clock,
        ),
        incidents=IncidentService(factory, publisher=publisher, workflows=workflows, clock=clock),
        approvals=ApprovalService(factory, publisher=publisher, workflows=workflows, clock=clock),
        notifications=NotificationService(factory),
        memory_search=memory_search,
        sleeper=sleeper,
    )


def _resolve(root: Path, path: Path) -> Path:
    return path if path.is_absolute() else root / path
