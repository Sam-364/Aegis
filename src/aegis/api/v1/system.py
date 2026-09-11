from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from aegis import __version__
from aegis.api.deps import Container, CurrentActor
from aegis.api.schemas import Principal, ReadyResponse
from aegis.telemetry.metrics import INCIDENTS_ACTIVE, REGISTRY

router = APIRouter(tags=["system"])


@router.get("/health", include_in_schema=False)
async def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@router.get("/ready", response_model=ReadyResponse, include_in_schema=False)
async def ready(request: Request) -> Response:
    checker = getattr(request.app.state, "readiness", None)
    checks: dict[str, Any] = (
        await checker()
        if checker
        else {"container": {"ok": hasattr(request.app.state, "container")}}
    )
    ok = all(bool(v.get("ok")) for v in checks.values())
    body = ReadyResponse(status="ready" if ok else "not_ready", checks=checks)
    return Response(
        content=body.model_dump_json(),
        status_code=200 if ok else 503,
        media_type="application/json",
    )


@router.get("/metrics", include_in_schema=False)
async def metrics(request: Request) -> Response:
    container = getattr(request.app.state, "container", None)
    if container is not None:
        try:
            stats = await container.incidents.stats()
            INCIDENTS_ACTIVE.set(stats["active"])
        except Exception:  # noqa: S110 - metrics must never fail the scrape
            pass
    return Response(content=generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)


@router.get("/api/v1/me", response_model=Principal)
async def me(c: Container, actor: CurrentActor) -> Principal:
    return Principal(
        id=actor.id,
        display_name=actor.display_name,
        roles=sorted(r.value for r in actor.roles),
        auth_mode=c.settings.api_auth_mode,
    )


@router.get("/api/v1/system/info")
async def info(c: Container, _: CurrentActor) -> dict[str, Any]:
    return {
        "version": __version__,
        "environment": c.settings.environment.value,
        "llm": {
            "provider": c.llm.name if c.llm else "disabled",
            "reasoner": c.llm.model_for("reasoner") if c.llm else None,
            "fast": c.llm.model_for("fast") if c.llm else None,
            "healthy": await c.llm.healthy() if c.llm else False,
        },
        "telemetry_provider": c.settings.telemetry_provider,
        "temporal": c.workflows is not None,
        "flows": [f.ref for f in c.flows.all()],
        "tools": len(c.registry.names()),
        "policy_rules": len(c.policy.rules),
    }
