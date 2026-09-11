"""FastAPI application factory."""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from aegis import __version__
from aegis.api.errors import install_error_handlers
from aegis.api.middleware import RateLimitMiddleware, RequestContextMiddleware
from aegis.api.v1 import (
    agent,
    approvals,
    events,
    incidents,
    memory,
    notifications,
    registry,
    simulation,
    system,
)
from aegis.application.container import RuntimeContainer
from aegis.config import Settings
from aegis.infrastructure.memory.messaging import InMemoryRateLimiter
from aegis.ports.messaging import RateLimiter
from aegis.telemetry.tracing import instrument_fastapi

DESCRIPTION = """Aegis control plane.

The model proposes. The runtime decides. The policy engine authorizes. The tool executes.
The observability layer records. The verification engine proves."""


def create_app(
    settings: Settings,
    *,
    container: RuntimeContainer | None = None,
    rate_limiter: RateLimiter | None = None,
    simulator_control: Any = None,
    readiness: Any = None,
) -> FastAPI:
    """Build the app. When ``container`` is None the lifespan wires a real runtime from settings."""

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        resources = None
        if container is None:
            from aegis.application.bootstrap import build_runtime
            from aegis.application.bootstrap import readiness as readiness_check

            built, resources = await build_runtime(settings, role="api")
            app.state.container = built
            app.state.simulator_control = resources.simulator_control
            app.state.readiness = lambda: readiness_check(settings, resources)
            if resources.rate_limiter is not None:
                app.state.rate_limiter = resources.rate_limiter
            # Tracing is configured by build_runtime, so the app can only be instrumented
            # once that has happened — not at construction time.
            instrument_fastapi(app)
        try:
            yield
        finally:
            if resources is not None:
                await resources.aclose()

    app = FastAPI(
        title="Aegis",
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
    )
    if container is not None:
        app.state.container = container
        app.state.simulator_control = simulator_control
        app.state.readiness = readiness
    limiter = rate_limiter or InMemoryRateLimiter()
    app.add_middleware(
        RateLimitMiddleware, limiter=limiter, per_minute=settings.api_rate_limit_per_minute
    )
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.api_cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["x-request-id"],
    )
    install_error_handlers(app)
    app.include_router(system.router)
    for r in (
        incidents.router,
        approvals.router,
        registry.router,
        agent.router,
        memory.router,
        simulation.router,
        notifications.router,
        events.router,
    ):
        app.include_router(r, prefix="/api/v1")
    return app
