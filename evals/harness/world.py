"""Shared world builder: simulator + detection + in-memory runtime + optional real LLM."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from aegis.application.container import RuntimeContainer, build_container
from aegis.config import Settings
from aegis.detection.engine import DetectionEngine
from aegis.domain.incident import Incident
from aegis.infrastructure.memory.repositories import InMemoryStore, InMemoryUnitOfWorkFactory
from aegis.infrastructure.simulator.inprocess import (
    InProcessSimulatorGateway,
    InProcessSimulatorTelemetry,
)
from aegis.llm import build_llm_provider
from aegis.ports.llm import LLMProvider
from aegis.simulator.engine import SimulationEngine

ROOT = Path(__file__).resolve().parents[2]


class SimClock:
    def __init__(self, engine: SimulationEngine) -> None:
        self.engine = engine

    def now(self) -> datetime:
        return self.engine.now


@dataclass
class World:
    engine: SimulationEngine
    store: InMemoryStore
    container: RuntimeContainer
    detector: DetectionEngine


def real_llm(settings: Settings | None = None) -> LLMProvider | None:
    settings = settings or Settings(_env_file=None)  # type: ignore[call-arg]
    return build_llm_provider(settings)


def build_world(
    *,
    seed: int,
    llm: LLMProvider | None = None,
    warmup: int = 600,
    reasoner_model: str | None = None,
) -> World:
    engine = SimulationEngine(seed=seed)
    engine.warmup(warmup)
    store = InMemoryStore()
    kwargs = {"llm_provider": "scripted" if llm is None else "openai", "_env_file": None}
    if reasoner_model:
        kwargs["llm_reasoner_model"] = reasoner_model
    settings = Settings(**kwargs)  # type: ignore[arg-type]
    if llm is not None and reasoner_model:
        llm.models["reasoner"] = reasoner_model  # type: ignore[attr-defined]
    container = build_container(
        settings,
        telemetry=InProcessSimulatorTelemetry(engine),
        infrastructure=InProcessSimulatorGateway(engine),
        uow_factory=InMemoryUnitOfWorkFactory(store),
        llm=llm,
        clock=SimClock(engine),
        root=ROOT,
    )
    detector = DetectionEngine(
        container.telemetry, container.intake, interval_seconds=5, clock=SimClock(engine)
    )
    return World(engine=engine, store=store, container=container, detector=detector)


async def detect(
    world: World, scenario: str, *, seconds: int = 100, params: dict | None = None
) -> Incident | None:
    await world.detector.bootstrap()
    world.engine.inject(scenario, params)
    for _ in range(seconds // 5):
        world.engine.advance(5)
        await world.detector.cycle()
    incidents = await world.container.intake.open_incidents()
    return incidents[0] if incidents else None
