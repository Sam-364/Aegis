"""HTTP surface of the simulator: telemetry reads, faults, remediation actions, diagnostics."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from aegis.simulator.engine import SimulationEngine, SimulationError


def _ts(value: float) -> str:
    return datetime.fromtimestamp(value, tz=UTC).isoformat()


def _epoch(value: datetime | None) -> float | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.timestamp()


class InjectRequest(BaseModel):
    scenario_id: str
    params: dict[str, Any] = Field(default_factory=dict)


class RestartRequest(BaseModel):
    target: str
    reason: str = ""


class RollbackRequest(BaseModel):
    service: str
    to_version: str | None = None
    reason: str = ""


class ScaleRequest(BaseModel):
    service: str
    replicas: int = Field(ge=1, le=10)
    reason: str = ""


class ClearCacheRequest(BaseModel):
    component: str
    reason: str = ""


class RotatePoolRequest(BaseModel):
    service: str
    target: str
    reason: str = ""


class DiagnosticRequest(BaseModel):
    kind: str
    target: str
    parameters: dict[str, Any] = Field(default_factory=dict)


class ResetRequest(BaseModel):
    seed: int | None = None
    warmup_seconds: float = 300.0


class AdvanceRequest(BaseModel):
    seconds: float = Field(gt=0, le=3600)


class SimulatorRuntime:
    """Owns the engine, its lock and the optional real-time ticker."""

    def __init__(self, engine: SimulationEngine, *, realtime: bool = True) -> None:
        self.engine = engine
        self.realtime = realtime
        self.paused = False
        self.lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self.realtime and self._task is None:
            self._task = asyncio.create_task(self._run(), name="simulator-ticker")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run(self) -> None:
        interval = self.engine.tick_seconds
        loop = asyncio.get_running_loop()
        next_at = loop.time()
        while True:
            next_at += interval
            if not self.paused:
                async with self.lock:
                    self.engine.tick()
            await asyncio.sleep(max(0.0, next_at - loop.time()))


def create_app(
    engine: SimulationEngine | None = None, *, realtime: bool = True, warmup_seconds: float = 300.0
) -> FastAPI:
    engine = engine or SimulationEngine()
    if warmup_seconds > 0 and engine.tick_count == 0:
        engine.warmup(warmup_seconds)
    runtime = SimulatorRuntime(engine, realtime=realtime)

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await runtime.start()
        try:
            yield
        finally:
            await runtime.stop()

    app = FastAPI(title="Aegis Simulated Infrastructure", version="0.1.0", lifespan=lifespan)
    app.state.runtime = runtime

    @app.exception_handler(SimulationError)
    async def _sim_error(_: Request, exc: SimulationError) -> Any:
        raise HTTPException(status_code=400, detail=str(exc))

    def eng() -> SimulationEngine:
        return runtime.engine

    # ---- health / metrics ---------------------------------------------------------------------
    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "time": eng().now.isoformat(),
            "tick_count": eng().tick_count,
            "paused": runtime.paused,
        }

    @app.get("/metrics", response_class=PlainTextResponse)
    async def prometheus() -> str:
        return eng().prometheus_text()

    # ---- telemetry reads ----------------------------------------------------------------------
    @app.get("/api/topology")
    async def topology() -> dict[str, Any]:
        return eng().topology()

    @app.get("/api/components")
    async def components() -> dict[str, Any]:
        return {"components": eng().component_names()}

    @app.get("/api/metrics/names")
    async def metric_names() -> dict[str, Any]:
        return {"metrics": eng().metric_names()}

    @app.get("/api/components/{name}/metrics")
    async def metrics(
        name: str, metric: str, start: datetime | None = None, end: datetime | None = None
    ) -> dict[str, Any]:
        samples = eng().series(name, metric, start=_epoch(start), end=_epoch(end))
        return {
            "component": name,
            "metric": metric,
            "samples": [{"at": _ts(s.at), "value": s.value} for s in samples],
        }

    @app.get("/api/metrics/snapshot")
    async def snapshot(window_seconds: int = Query(default=600, ge=1, le=1800)) -> dict[str, Any]:
        snap = eng().snapshot(window_seconds)
        return {
            "window_seconds": window_seconds,
            "time": eng().now.isoformat(),
            "components": {
                comp: {
                    metric: [{"at": _ts(s.at), "value": s.value} for s in samples]
                    for metric, samples in metrics.items()
                }
                for comp, metrics in snap.items()
            },
        }

    @app.get("/api/components/{name}/current")
    async def current(name: str) -> dict[str, Any]:
        return {
            "component": name,
            "time": eng().now.isoformat(),
            "metrics": eng().current_metrics(name),
        }

    @app.get("/api/components/{name}/baseline/{metric}")
    async def baseline(name: str, metric: str) -> dict[str, Any]:
        return {"component": name, "metric": metric, "baseline": eng().baseline(name, metric)}

    @app.get("/api/components/{name}/health")
    async def component_health(name: str) -> dict[str, Any]:
        return eng().health(name)

    @app.get("/api/components/{name}/resource")
    async def resource(name: str) -> dict[str, Any]:
        return eng().resource(name)

    @app.get("/api/logs")
    async def logs(
        component: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        level: str | None = None,
        limit: int = Query(default=100, ge=1, le=1000),
    ) -> dict[str, Any]:
        items = eng().logs_for(
            component, start=_epoch(start), end=_epoch(end), level=level, limit=limit
        )
        return {
            "logs": [
                {
                    "at": _ts(log.at),
                    "service": log.service,
                    "level": log.level,
                    "message": log.message,
                    "attributes": log.attributes,
                }
                for log in items
            ]
        }

    @app.get("/api/traces")
    async def traces(
        component: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        errors_only: bool = False,
        limit: int = Query(default=20, ge=1, le=200),
    ) -> dict[str, Any]:
        items = eng().traces_for(
            component, start=_epoch(start), end=_epoch(end), errors_only=errors_only, limit=limit
        )
        return {
            "traces": [
                {
                    "trace_id": tr.trace_id,
                    "at": _ts(tr.at),
                    "root_service": tr.root_service,
                    "duration_ms": round(tr.duration_ms, 2),
                    "error": tr.error,
                    "spans": [
                        {
                            "service": sp.service,
                            "operation": sp.operation,
                            "duration_ms": round(sp.duration_ms, 2),
                            "error": sp.error,
                            "depth": sp.depth,
                        }
                        for sp in tr.spans
                    ],
                }
                for tr in items
            ]
        }

    @app.get("/api/deployments")
    async def deployments(service: str | None = None) -> dict[str, Any]:
        return {
            "deployments": [
                {
                    "service": d.service,
                    "version": d.version,
                    "previous_version": d.previous_version,
                    "deployed_at": _ts(d.deployed_at),
                    "change_summary": d.change_summary,
                    "kind": d.kind,
                }
                for d in eng().deployments_for(service)
            ]
        }

    # ---- faults -------------------------------------------------------------------------------
    @app.get("/api/scenarios")
    async def scenarios() -> dict[str, Any]:
        return {"scenarios": [s.model_dump() for s in eng().scenarios()]}

    @app.get("/api/faults")
    async def faults(active_only: bool = True) -> dict[str, Any]:
        items = eng().active_faults() if active_only else eng().faults
        return {"faults": [f.to_dict() for f in items]}

    @app.post("/api/faults", status_code=201)
    async def inject(body: InjectRequest) -> dict[str, Any]:
        async with runtime.lock:
            return eng().inject(body.scenario_id, body.params).to_dict()

    @app.delete("/api/faults/{fault_id}")
    async def clear(fault_id: str) -> dict[str, Any]:
        async with runtime.lock:
            return eng().clear_fault(fault_id).to_dict()

    # ---- actions ------------------------------------------------------------------------------
    @app.post("/api/actions/restart")
    async def restart(body: RestartRequest) -> dict[str, Any]:
        async with runtime.lock:
            return eng().restart(body.target, reason=body.reason)

    @app.post("/api/actions/rollback")
    async def rollback(body: RollbackRequest) -> dict[str, Any]:
        async with runtime.lock:
            return eng().rollback(body.service, to_version=body.to_version, reason=body.reason)

    @app.post("/api/actions/scale")
    async def scale(body: ScaleRequest) -> dict[str, Any]:
        async with runtime.lock:
            return eng().scale(body.service, replicas=body.replicas, reason=body.reason)

    @app.post("/api/actions/clear-cache")
    async def clear_cache(body: ClearCacheRequest) -> dict[str, Any]:
        async with runtime.lock:
            return eng().clear_cache(body.component, reason=body.reason)

    @app.post("/api/actions/rotate-pool")
    async def rotate_pool(body: RotatePoolRequest) -> dict[str, Any]:
        async with runtime.lock:
            return eng().rotate_pool(body.service, target=body.target, reason=body.reason)

    @app.post("/api/diagnostics")
    async def diagnostics(body: DiagnosticRequest) -> dict[str, Any]:
        async with runtime.lock:
            return eng().diagnostic(body.kind, body.target, body.parameters)

    # ---- control (tests, demos) ---------------------------------------------------------------
    @app.post("/api/control/reset")
    async def reset(body: ResetRequest) -> dict[str, Any]:
        async with runtime.lock:
            eng().reset(seed=body.seed)
            if body.warmup_seconds > 0:
                eng().warmup(body.warmup_seconds)
            return eng().state_summary()

    @app.post("/api/control/advance")
    async def advance(body: AdvanceRequest) -> dict[str, Any]:
        async with runtime.lock:
            eng().advance(body.seconds)
            return eng().state_summary()

    @app.post("/api/control/pause")
    async def pause() -> dict[str, Any]:
        runtime.paused = True
        return {"paused": True}

    @app.post("/api/control/resume")
    async def resume() -> dict[str, Any]:
        runtime.paused = False
        return {"paused": False}

    @app.get("/api/control/state")
    async def state() -> dict[str, Any]:
        return eng().state_summary()

    @app.get("/api/control/actions")
    async def actions() -> dict[str, Any]:
        return {"actions": eng().action_log}

    return app
