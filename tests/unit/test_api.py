"""API tests with an in-memory container (no Postgres/Redis/Temporal)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from aegis.api.app import create_app
from aegis.application.container import RuntimeContainer, build_container
from aegis.config import Settings
from aegis.detection.engine import DetectionEngine
from aegis.domain.enums import IncidentStatus
from aegis.infrastructure.memory.messaging import InMemoryEventPublisher
from aegis.infrastructure.memory.repositories import InMemoryUnitOfWorkFactory
from aegis.infrastructure.simulator.inprocess import (
    InProcessSimulatorGateway,
    InProcessSimulatorTelemetry,
)
from aegis.simulator.engine import SimulationEngine
from tests.helpers import SimClock

ROOT = Path(__file__).resolve().parents[2]


class FakeSimControl:
    def __init__(self, engine: SimulationEngine) -> None:
        self.engine = engine

    async def scenarios(self):  # type: ignore[no-untyped-def]
        return [s.model_dump() for s in self.engine.scenarios()]

    async def inject(self, scenario_id, params=None):  # type: ignore[no-untyped-def]
        return self.engine.inject(scenario_id, params).to_dict()

    async def faults(self, active_only=True):  # type: ignore[no-untyped-def]
        return [f.to_dict() for f in self.engine.active_faults()]

    async def topology(self):  # type: ignore[no-untyped-def]
        return self.engine.topology()

    async def state(self):  # type: ignore[no-untyped-def]
        return self.engine.state_summary()

    async def health(self):  # type: ignore[no-untyped-def]
        return {"status": "ok"}


@pytest.fixture
def world():  # type: ignore[no-untyped-def]
    engine = SimulationEngine(seed=31)
    engine.warmup(600)
    settings = Settings(
        llm_provider="disabled",
        api_auth_mode="api_key",
        api_keys="op-key:operator:ops,view-key:viewer:viewer,adm-key:admin:root",
        _env_file=None,
    )  # type: ignore[call-arg]
    publisher = InMemoryEventPublisher()
    container = build_container(
        settings,
        telemetry=InProcessSimulatorTelemetry(engine),
        infrastructure=InProcessSimulatorGateway(engine),
        uow_factory=InMemoryUnitOfWorkFactory(),
        publisher=publisher,
        clock=SimClock(engine),
        root=ROOT,
    )
    app = create_app(settings, container=container, simulator_control=FakeSimControl(engine))
    return engine, container, app


async def seed(
    engine: SimulationEngine, container: RuntimeContainer, scenario: str = "redis-connection-leak"
):  # type: ignore[no-untyped-def]
    det = DetectionEngine(
        container.telemetry, container.intake, interval_seconds=5, clock=SimClock(engine)
    )
    await det.bootstrap()
    engine.inject(scenario)
    for _ in range(24):
        engine.advance(5)
        await det.cycle()
    incidents = await container.intake.open_incidents()
    assert incidents
    return incidents[0]


async def client_for(app, key: str | None = "op-key") -> httpx.AsyncClient:  # type: ignore[no-untyped-def]
    headers = {"X-Aegis-Key": key} if key else {}
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test", headers=headers
    )


async def test_auth_and_roles(world) -> None:  # type: ignore[no-untyped-def]
    _engine, _container, app = world
    async with await client_for(app, None) as anon:
        r = await anon.get("/api/v1/incidents")
        assert r.status_code == 401 and r.json()["type"] == "unauthorized"
        assert (await anon.get("/health")).status_code == 200
    async with await client_for(app, "bad") as bad:
        assert (await bad.get("/api/v1/incidents")).status_code == 401
    async with await client_for(app, "view-key") as viewer:
        me = (await viewer.get("/api/v1/me")).json()
        assert me["roles"] == ["viewer"] and me["auth_mode"] == "api_key"
        r = await viewer.post(
            "/api/v1/simulation/faults", json={"scenario_id": "redis-connection-leak"}
        )
        assert r.status_code == 403 and r.json()["type"] == "forbidden"
        assert (await viewer.get("/api/v1/flows")).status_code == 200


async def test_incident_endpoints_and_sse(world) -> None:  # type: ignore[no-untyped-def]
    engine, container, app = world
    inc = await seed(engine, container)
    async with await client_for(app) as client:
        page = (await client.get("/api/v1/incidents", params={"active": "true"})).json()
        assert page["total"] == 1 and page["items"][0]["display_id"].startswith("INC-")
        detail = (await client.get(f"/api/v1/incidents/{inc.id}")).json()
        assert detail["incident"]["title"] == inc.title and "evidence" in detail
        timeline = (await client.get(f"/api/v1/incidents/{inc.id}/timeline")).json()
        assert [e["seq"] for e in timeline] == list(range(1, len(timeline) + 1))
        assert timeline[0]["type"] == "incident.detected"
        stats = (await client.get("/api/v1/incidents/stats")).json()
        assert stats["active"] == 1 and stats["pending_approvals"] == 0
        r = await client.get("/api/v1/incidents/00000000-0000-0000-0000-000000000000")
        assert (
            r.status_code == 404 and r.json()["type"] == "not_found" and r.headers["x-request-id"]
        )


async def test_sse_stream_replays_then_follows(world) -> None:  # type: ignore[no-untyped-def]
    """SSE needs a real server: the ASGI test transport buffers the (endless) response."""
    import socket

    import uvicorn

    engine, container, app = world
    inc = await seed(engine, container)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    try:
        for _ in range(50):
            if server.started:
                break
            await asyncio.sleep(0.05)
        timeline = await container.incidents.timeline(inc.id)
        collected: list[dict] = []
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}", headers={"X-Aegis-Key": "op-key"}, timeout=10
        ) as client:

            async def consume() -> None:
                async with client.stream(
                    "GET", f"/api/v1/incidents/{inc.id}/stream", params={"after_seq": 1}
                ) as resp:
                    assert resp.status_code == 200
                    buffer = ""
                    async for chunk in resp.aiter_text():
                        buffer += chunk.replace("\r\n", "\n")
                        while "\n\n" in buffer:
                            frame, buffer = buffer.split("\n\n", 1)
                            data = [
                                ln[5:].strip()
                                for ln in frame.splitlines()
                                if ln.startswith("data:")
                            ]
                            if data:
                                collected.append(json.loads(data[0]))
                            if len(collected) >= len(timeline):
                                return

            consumer = asyncio.create_task(consume())
            await asyncio.sleep(0.3)
            ack = await client.post(f"/api/v1/incidents/{inc.id}/acknowledge")
            assert ack.status_code == 200
            await asyncio.wait_for(consumer, timeout=10)
        seqs = [e["seq"] for e in collected]
        assert seqs == sorted(seqs) and seqs[0] == 2
        assert any(e["type"] == "incident.status_changed" for e in collected)
    finally:
        server.should_exit = True
        await task


async def test_human_actions_and_validation(world) -> None:  # type: ignore[no-untyped-def]
    engine, container, app = world
    inc = await seed(engine, container)
    async with await client_for(app) as client:
        r = await client.post(f"/api/v1/incidents/{inc.id}/resolve", json={"reason": "x"})
        assert (
            r.status_code == 409 and r.json()["type"] == "invalid_transition"
        )  # detected → resolved not allowed
        r = await client.post(
            f"/api/v1/incidents/{inc.id}/close", json={"reason": "handled manually"}
        )
        assert r.status_code == 200 and r.json()["status"] == IncidentStatus.CLOSED.value
        audit = (await client.get(f"/api/v1/incidents/{inc.id}/audit")).json()
        assert any(
            a["event_type"] == "incident.status_changed" and a["actor"]["id"] == "user:ops"
            for a in audit
        )
        r = await client.post(f"/api/v1/incidents/{inc.id}/close", json={"reason": 42})
        assert r.status_code == 422 and r.json()["type"] == "validation_error"


async def test_registry_simulation_memory_and_notifications(world) -> None:  # type: ignore[no-untyped-def]
    _engine, _container, app = world
    async with await client_for(app, "adm-key") as client:
        flows = (await client.get("/api/v1/flows")).json()
        assert {f["name"] for f in flows} >= {"incident-investigation", "api-latency-investigation"}
        tool = (await client.get("/api/v1/tools/restart_service")).json()
        assert (
            tool["category"] == "mutating"
            and "<remediation>" in tool["used_in"]["incident-investigation@1.1.0"]
        )
        assert (await client.get("/api/v1/tools/nope")).status_code == 404
        pol = (await client.get("/api/v1/policies")).json()
        assert pol["default"] == "deny" and len(pol["invariants"]) == 3
        scen = (await client.get("/api/v1/simulation/scenarios")).json()
        assert any(s["id"] == "bad-deployment" for s in scen)
        r = await client.post("/api/v1/simulation/faults", json={"scenario_id": "bad-deployment"})
        assert r.status_code == 201 and r.json()["active"]
        topo = (await client.get("/api/v1/simulation/topology")).json()
        assert topo["active_faults"] and any(n["name"] == "payment-service" for n in topo["nodes"])
        mem = (await client.get("/api/v1/memory")).json()
        assert mem["total"] == 0
        notes = (await client.get("/api/v1/notifications")).json()
        assert isinstance(notes, list)
        info = (await client.get("/api/v1/system/info")).json()
        assert info["llm"]["provider"] == "disabled" and info["tools"] > 20
        metrics = await client.get("/metrics")
        assert metrics.status_code == 200 and b"aegis_http_requests_total" in metrics.content
        openapi = (await client.get("/api/openapi.json")).json()
        assert "/api/v1/incidents/{incident_id}/stream" in openapi["paths"]


async def test_rate_limit(world) -> None:  # type: ignore[no-untyped-def]
    _engine, container, app = world
    app.user_middleware.clear()  # rebuild with a tiny limit
    settings = Settings(llm_provider="disabled", api_rate_limit_per_minute=3, _env_file=None)  # type: ignore[call-arg]
    small = create_app(settings, container=container)
    async with await client_for(small, "op-key") as client:
        codes = [(await client.get("/api/v1/flows")).status_code for _ in range(5)]
        assert codes == [200, 200, 200, 429, 429]


async def test_rate_limiting_degrades_to_local_counting_when_its_backend_is_down(world) -> None:  # type: ignore[no-untyped-def]
    """A rate limit protects the API; it must not become a dependency that takes reads down with
    it. Redis being unavailable is what the chaos suite does to a running stack."""
    _engine, container, app = world
    app.user_middleware.clear()
    settings = Settings(llm_provider="disabled", api_rate_limit_per_minute=3, _env_file=None)  # type: ignore[call-arg]
    small = create_app(settings, container=container)

    class BrokenLimiter:
        async def allow(self, key: str, limit: int, window_seconds: int) -> tuple[bool, int]:
            raise ConnectionError("redis is down")

    small.state.rate_limiter = BrokenLimiter()
    async with await client_for(small, "op-key") as client:
        codes = [(await client.get("/api/v1/flows")).status_code for _ in range(5)]
    # served, then limited by the in-process counter — never a 500
    assert codes[:3] == [200, 200, 200]
    assert set(codes[3:]) == {429}
