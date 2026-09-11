"""Simulation control for demos and E2E: proxied so the console talks to one API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from aegis.api.deps import Container, CurrentActor, Operator
from aegis.api.schemas import InjectFault, SimulationControl
from aegis.domain.errors import InfrastructureError
from aegis.infrastructure.simulator.control import SimulatorControlClient

router = APIRouter(prefix="/simulation", tags=["simulation"])


def _control(request: Request) -> SimulatorControlClient:
    client: SimulatorControlClient | None = getattr(request.app.state, "simulator_control", None)
    if client is None:
        raise InfrastructureError("simulator control is not configured")
    return client


@router.get("/scenarios")
async def scenarios(request: Request, _: CurrentActor) -> list[dict[str, Any]]:
    return await _control(request).scenarios()


@router.get("/faults")
async def faults(
    request: Request, _: CurrentActor, active_only: bool = True
) -> list[dict[str, Any]]:
    return await _control(request).faults(active_only)


@router.post("/faults", status_code=201)
async def inject(body: InjectFault, request: Request, _: Operator) -> dict[str, Any]:
    return await _control(request).inject(body.scenario_id, body.params)


@router.delete("/faults/{fault_id}")
async def clear(fault_id: str, request: Request, _: Operator) -> dict[str, Any]:
    return await _control(request).clear(fault_id)


@router.get("/topology")
async def topology(request: Request, c: Container, _: CurrentActor) -> dict[str, Any]:
    topo = await _control(request).topology()
    state = await _control(request).state()
    for node in topo["nodes"]:
        svc = state["services"].get(node["name"]) or state["infra"].get(node["name"]) or {}
        node["state"] = svc
    topo["time"] = state["time"]
    topo["active_faults"] = state["active_faults"]
    return topo


@router.get("/state")
async def state(request: Request, _: CurrentActor) -> dict[str, Any]:
    return await _control(request).state()


@router.get("/actions")
async def actions(request: Request, _: CurrentActor) -> list[dict[str, Any]]:
    return await _control(request).actions()


@router.get("/metrics/{component}")
async def metrics(
    component: str,
    request: Request,
    _: CurrentActor,
    metric: str = "latency_p95_ms",
    start: str | None = None,
    end: str | None = None,
) -> dict[str, Any]:
    return await _control(request).metrics(component, metric, start, end)


@router.get("/components/{component}/baseline/{metric}")
async def baseline(
    component: str, metric: str, request: Request, _: CurrentActor
) -> dict[str, Any]:
    """Healthy-period baseline the simulator maintains for a metric (null when unknown)."""
    return await _control(request).baseline(component, metric)


@router.get("/components/{component}/current")
async def current(component: str, request: Request, _: CurrentActor) -> dict[str, Any]:
    return await _control(request).current(component)


@router.post("/control/advance")
async def advance(body: SimulationControl, request: Request, _: Operator) -> dict[str, Any]:
    return await _control(request).advance(body.seconds or 30)


@router.post("/control/reset")
async def reset(body: SimulationControl, request: Request, _: Operator) -> dict[str, Any]:
    return await _control(request).reset(body.seed)
