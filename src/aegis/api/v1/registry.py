from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from aegis.api.deps import Container, CurrentActor
from aegis.domain.errors import NotFoundError

router = APIRouter(tags=["registry"])


@router.get("/flows")
async def flows(c: Container, _: CurrentActor) -> list[dict[str, Any]]:
    return [p.model_dump(mode="json") for p in c.flows.all()]


@router.get("/flows/{name}")
async def flow(
    name: str, c: Container, _: CurrentActor, version: str | None = None
) -> dict[str, Any]:
    return c.flows.get(name, version).model_dump(mode="json")


@router.get("/tools")
async def tools(c: Container, _: CurrentActor) -> list[dict[str, Any]]:
    return [s.model_dump(mode="json") for s in c.registry.specs()]


@router.get("/tools/{name}")
async def tool(name: str, c: Container, _: CurrentActor) -> dict[str, Any]:
    if not c.registry.has(name):
        raise NotFoundError(f"tool {name} not found")
    spec = c.registry.spec(name)
    used_in = {
        f.ref: [p.name for p in f.phases if name in p.allowed_tools]
        + (["<remediation>"] if name in f.remediation_tools else [])
        for f in c.flows.all()
    }
    return {**spec.model_dump(mode="json"), "used_in": {k: v for k, v in used_in.items() if v}}


@router.get("/policies")
async def policies(c: Container, _: CurrentActor) -> dict[str, Any]:
    return {
        "rules": [r.model_dump(mode="json") for r in c.policy.rules],
        "invariants": [
            {
                "name": "dangerous_tools_denied",
                "description": "Dangerous tools are never executable.",
            },
            {
                "name": "no_mutation_inside_agent_loop",
                "description": (
                    "Mutating tools run only through the workflow after policy and approval."
                ),
            },
            {
                "name": "agent_cannot_mutate",
                "description": "The agent proposes; only the workflow or a human executes.",
            },
        ],
        "default": "deny",
        "environment": c.settings.environment.value,
    }
