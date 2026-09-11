"""Mutating tools (executed only by the workflow after authorization) and dangerous tools
(registered so policy can be tested against them; never executable)."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from aegis.domain.enums import EvidenceKind, RiskLevel, ToolCategory
from aegis.domain.errors import ToolExecutionError
from aegis.tools.context import ToolContext
from aegis.tools.definition import EvidenceDraft, ToolArgs, ToolDefinition, ToolOutput, tool


class RestartArgs(ToolArgs):
    service: str = Field(description="service (or infrastructure component) to restart")
    reason: str = Field(default="", max_length=300)


@tool(
    "restart_service",
    description="Rolling restart of a service. Clears leaked connections and "
    "memory; does not fix code regressions.",
    category=ToolCategory.MUTATING,
    risk=RiskLevel.MEDIUM,
    args=RestartArgs,
    idempotent=True,
    timeout_seconds=30.0,
    verification_metrics=("error_rate", "latency_p95_ms", "up"),
)
async def restart_service(ctx: ToolContext, args: RestartArgs) -> ToolOutput:
    r = await ctx.infrastructure.restart_service(
        args.service, reason=args.reason or "aegis remediation"
    )
    summary = (
        f"restart of {args.service} {r.get('status', 'requested')} "
        f"(eta {float(r.get('eta_seconds', 0)):.0f}s, version {r.get('version', '?')})"
    )
    return ToolOutput(
        data=r,
        summary=summary,
        evidence=[
            EvidenceDraft(
                kind=EvidenceKind.ACTION_RESULT,
                title=f"restart {args.service}",
                summary=summary,
                data=r,
                strength=0.5,
                service=args.service,
                tags=["remediation", "restart"],
            )
        ],
    )


class RollbackArgs(ToolArgs):
    service: str
    to_version: str | None = Field(default=None, pattern=r"^[A-Za-z0-9._\-]{1,40}$")
    reason: str = Field(default="", max_length=300)


@tool(
    "rollback_deployment",
    description="Roll a service back to its previous (or a named) version.",
    category=ToolCategory.MUTATING,
    risk=RiskLevel.MEDIUM,
    args=RollbackArgs,
    idempotent=True,
    timeout_seconds=30.0,
    verification_metrics=("error_rate", "latency_p95_ms"),
)
async def rollback_deployment(ctx: ToolContext, args: RollbackArgs) -> ToolOutput:
    r = await ctx.infrastructure.rollback_deployment(
        args.service, to_version=args.to_version, reason=args.reason or "aegis remediation"
    )
    summary = (
        f"rollback of {args.service}: {r.get('from_version', '?')} → {r.get('to_version', '?')} "
        f"({r.get('status', 'requested')})"
    )
    return ToolOutput(
        data=r,
        summary=summary,
        evidence=[
            EvidenceDraft(
                kind=EvidenceKind.ACTION_RESULT,
                title=f"rollback {args.service}",
                summary=summary,
                data=r,
                strength=0.5,
                service=args.service,
                tags=["remediation", "rollback"],
            )
        ],
    )


class ScaleArgs(ToolArgs):
    service: str
    replicas: int = Field(ge=1, le=10)
    reason: str = Field(default="", max_length=300)


@tool(
    "scale_service",
    description="Change the replica count of a service.",
    category=ToolCategory.MUTATING,
    risk=RiskLevel.LOW,
    args=ScaleArgs,
    idempotent=True,
    timeout_seconds=30.0,
    verification_metrics=("latency_p95_ms", "cpu_percent"),
)
async def scale_service(ctx: ToolContext, args: ScaleArgs) -> ToolOutput:
    r = await ctx.infrastructure.scale_service(
        args.service, replicas=args.replicas, reason=args.reason or "aegis remediation"
    )
    summary = (
        f"scaled {args.service} {r.get('from_replicas', '?')} → {r.get('to_replicas', '?')} "
        f"replicas ({r.get('status', 'requested')})"
    )
    return ToolOutput(
        data=r,
        summary=summary,
        evidence=[
            EvidenceDraft(
                kind=EvidenceKind.ACTION_RESULT,
                title=f"scale {args.service}",
                summary=summary,
                data=r,
                strength=0.5,
                service=args.service,
                tags=["remediation", "scale"],
            )
        ],
    )


class ClearCacheArgs(ToolArgs):
    component: str = Field(default="redis")
    reason: str = Field(default="", max_length=300)


@tool(
    "clear_cache",
    description="Flush a cache. Briefly increases load on backing stores.",
    category=ToolCategory.MUTATING,
    risk=RiskLevel.LOW,
    args=ClearCacheArgs,
    idempotent=True,
    timeout_seconds=30.0,
    verification_metrics=("hit_rate",),
)
async def clear_cache(ctx: ToolContext, args: ClearCacheArgs) -> ToolOutput:
    r = await ctx.infrastructure.clear_cache(
        args.component, reason=args.reason or "aegis remediation"
    )
    summary = f"cache {args.component} flushed ({r.get('status', 'requested')})"
    return ToolOutput(
        data=r,
        summary=summary,
        evidence=[
            EvidenceDraft(
                kind=EvidenceKind.ACTION_RESULT,
                title=f"clear cache {args.component}",
                summary=summary,
                data=r,
                strength=0.5,
                service=args.component,
                tags=["remediation", "cache"],
            )
        ],
    )


class RotatePoolArgs(ToolArgs):
    service: str
    target: str = Field(description="infrastructure component the pool connects to")
    reason: str = Field(default="", max_length=300)


@tool(
    "rotate_connection_pool",
    description="Recycle a service's connection pool towards one "
    "infrastructure component, releasing held connections without a full restart.",
    category=ToolCategory.MUTATING,
    risk=RiskLevel.LOW,
    args=RotatePoolArgs,
    idempotent=True,
    timeout_seconds=30.0,
    verification_metrics=("connections", "latency_p95_ms"),
)
async def rotate_connection_pool(ctx: ToolContext, args: RotatePoolArgs) -> ToolOutput:
    r = await ctx.infrastructure.rotate_connection_pool(
        args.service, target=args.target, reason=args.reason or "aegis remediation"
    )
    summary = (
        f"rotated {args.service} pool to {args.target}: released "
        f"{r.get('released_connections', '?')} connections ({r.get('status', 'requested')})"
    )
    return ToolOutput(
        data=r,
        summary=summary,
        evidence=[
            EvidenceDraft(
                kind=EvidenceKind.ACTION_RESULT,
                title=f"rotate pool {args.service}→{args.target}",
                summary=summary,
                data=r,
                strength=0.5,
                service=args.service,
                tags=["remediation", "pool"],
            )
        ],
    )


MUTATING_TOOLS: list[ToolDefinition[Any]] = [
    restart_service,
    rollback_deployment,
    scale_service,
    clear_cache,
    rotate_connection_pool,
]


# --- dangerous: registered for policy tests only ------------------------------------------------


class TargetArgs(ToolArgs):
    target: str


async def _never(ctx: ToolContext, args: TargetArgs) -> ToolOutput:
    raise ToolExecutionError("dangerous tools are never executable")


delete_data = tool(
    "delete_data",
    description="Delete data. Never executable.",
    category=ToolCategory.DANGEROUS,
    risk=RiskLevel.CRITICAL,
    args=TargetArgs,
    idempotent=False,
)(_never)
drop_database = tool(
    "drop_database",
    description="Drop a database. Never executable.",
    category=ToolCategory.DANGEROUS,
    risk=RiskLevel.CRITICAL,
    args=TargetArgs,
    idempotent=False,
)(_never)
terminate_instance = tool(
    "terminate_instance",
    description="Terminate an instance. Never executable.",
    category=ToolCategory.DANGEROUS,
    risk=RiskLevel.CRITICAL,
    args=TargetArgs,
    idempotent=False,
)(_never)

DANGEROUS_TOOLS: list[ToolDefinition[Any]] = [delete_data, drop_database, terminate_instance]
