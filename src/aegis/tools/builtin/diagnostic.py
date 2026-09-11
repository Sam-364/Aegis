"""Diagnostic tools: active checks that confirm or refute a hypothesis. Still non-mutating."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from aegis.domain.enums import EvidenceKind, RiskLevel, ToolCategory
from aegis.tools.context import ToolContext
from aegis.tools.definition import EvidenceDraft, ToolArgs, ToolDefinition, ToolOutput, tool


class ConnectivityArgs(ToolArgs):
    source: str = Field(description="calling service")
    target: str = Field(description="dependency being called")


@tool(
    "run_connectivity_test",
    description="Probe one dependency edge: reachability, latency, "
    "timeouts, injected network latency.",
    category=ToolCategory.DIAGNOSTIC,
    risk=RiskLevel.LOW,
    args=ConnectivityArgs,
)
async def run_connectivity_test(ctx: ToolContext, args: ConnectivityArgs) -> ToolOutput:
    d = await ctx.infrastructure.run_diagnostic(
        "connectivity", target=args.target, parameters={"source": args.source}
    )
    if not d.get("dependency", True):
        summary = f"{args.source} does not call {args.target}"
        return ToolOutput(
            data=d,
            summary=summary,
            evidence=[
                EvidenceDraft(
                    kind=EvidenceKind.DIAGNOSTIC,
                    title="connectivity",
                    summary=summary,
                    data=d,
                    strength=0.2,
                )
            ],
        )
    reachable = bool(d.get("reachable", False))
    lat = float(d.get("latency_ms", 0))
    net = float(d.get("added_network_latency_ms", 0))
    summary = (
        f"{args.source} → {args.target}: {'reachable' if reachable else 'UNREACHABLE'}, "
        f"latency {lat:.0f}ms (timeout {float(d.get('timeout_ms', 0)):.0f}ms"
        f"{', timing out' if d.get('timing_out') else ''}), error contribution "
        f"{float(d.get('error_contribution', 0)) * 100:.0f}%"
    )
    if net > 0:
        summary += f"; {net:.0f}ms of network latency on this path"
    strength = 0.9 if not reachable else 0.8 if (d.get("timing_out") or net > 100) else 0.35
    return ToolOutput(
        data=d,
        summary=summary,
        evidence=[
            EvidenceDraft(
                kind=EvidenceKind.DIAGNOSTIC,
                title=f"connectivity {args.source}→{args.target}",
                summary=summary,
                data=d,
                strength=strength,
                service=args.target,
                tags=["unreachable" if not reachable else "network" if net > 100 else "ok"],
            )
        ],
    )


class ComponentArgs(ToolArgs):
    component: str = Field(default="postgres")
    expectation: str = Field(default="", description="what the hypothesis predicts this shows")


@tool(
    "run_database_diagnostic",
    description="Active database check: connection slots by client, "
    "idle-in-transaction sessions, lock waits.",
    category=ToolCategory.DIAGNOSTIC,
    risk=RiskLevel.LOW,
    args=ComponentArgs,
)
async def run_database_diagnostic(ctx: ToolContext, args: ComponentArgs) -> ToolOutput:
    d = await ctx.infrastructure.run_diagnostic("database", target=args.component, parameters={})
    return _infra_diag(args.component, d, "database")


class CacheArgs(ToolArgs):
    component: str = Field(default="redis")
    expectation: str = ""


@tool(
    "run_cache_diagnostic",
    description="Active cache check: clients by service, leaked connections, blocked clients.",
    category=ToolCategory.DIAGNOSTIC,
    risk=RiskLevel.LOW,
    args=CacheArgs,
)
async def run_cache_diagnostic(ctx: ToolContext, args: CacheArgs) -> ToolOutput:
    d = await ctx.infrastructure.run_diagnostic("cache", target=args.component, parameters={})
    return _infra_diag(args.component, d, "cache")


def _infra_diag(component: str, d: dict[str, Any], kind: str) -> ToolOutput:
    clients: dict[str, float] = d.get("connections_by_client") or d.get("clients_by_service") or {}
    top = sorted(clients.items(), key=lambda kv: -kv[1])
    total = float(d.get("connections") or d.get("connected_clients") or 0)
    sat = float(d.get("saturation", 0))
    leaked = d.get("leaked_by_service") or d.get("idle_in_transaction_by_client") or {}
    parts = [f"{component} diagnostic: saturation {sat * 100:.0f}%, {total:.0f} connections"]
    if top:
        share = top[0][1] / total if total else 0
        parts.append(f"top client {top[0][0]} ({top[0][1]:.0f}, {share * 100:.0f}%)")
    if leaked:
        parts.append(
            ("leaked" if kind == "cache" else "idle-in-transaction")
            + ": "
            + ", ".join(f"{k}={v}" for k, v in leaked.items())
        )
    if kind == "database":
        parts.append(f"lock waits {float(d.get('lock_waits', 0)):.0f}")
    if not d.get("available", True):
        parts.append("COMPONENT UNAVAILABLE")
    summary = "; ".join(parts)
    culprit = next(iter(leaked), None) or (top[0][0] if top and sat > 0.5 else component)
    strength = 0.9 if leaked or sat >= 0.9 else 0.5 if sat >= 0.6 else 0.3
    return ToolOutput(
        data=d,
        summary=summary,
        evidence=[
            EvidenceDraft(
                kind=EvidenceKind.DIAGNOSTIC,
                title=f"{component} diagnostic",
                summary=summary,
                data=d,
                strength=strength,
                service=culprit,
                tags=[kind, *list(leaked)[:2]],
            )
        ],
    )


class ServiceArgs(ToolArgs):
    service: str
    expectation: str = ""


@tool(
    "run_process_diagnostic",
    description="Process-level check: uptime, restarts, replicas "
    "ready, memory/cpu, open connections held by the process.",
    category=ToolCategory.DIAGNOSTIC,
    risk=RiskLevel.LOW,
    args=ServiceArgs,
)
async def run_process_diagnostic(ctx: ToolContext, args: ServiceArgs) -> ToolOutput:
    d = await ctx.infrastructure.run_diagnostic("process", target=args.service, parameters={})
    summary = (
        f"{args.service} process: {'up' if d.get('up') else 'DOWN'}"
        f"{' (crashed)' if d.get('crashed') else ''}"
        f"{' (restarting)' if d.get('restarting') else ''}, "
        f"version {d.get('version')}, uptime {d.get('uptime_seconds')}s, replicas "
        f"{d.get('replicas_ready')}/{d.get('replicas')}, restarts {d.get('restart_count')}, "
        f"mem {d.get('memory_percent')}%, cpu {d.get('cpu_percent')}%, open redis conns "
        f"{d.get('open_redis_connections')}, open db conns {d.get('open_db_connections')}"
    )
    abnormal = (
        (not d.get("up", True))
        or float(d.get("memory_percent", 0)) > 85
        or float(d.get("cpu_percent", 0)) > 90
        or float(d.get("open_redis_connections", 0)) > 60
        or float(d.get("open_db_connections", 0)) > 40
    )
    return ToolOutput(
        data=d,
        summary=summary,
        evidence=[
            EvidenceDraft(
                kind=EvidenceKind.DIAGNOSTIC,
                title=f"{args.service} process diagnostic",
                summary=summary,
                data=d,
                strength=0.8 if abnormal else 0.3,
                service=args.service,
                tags=["abnormal" if abnormal else "normal"],
            )
        ],
    )


class ReproduceArgs(ToolArgs):
    service: str
    endpoint: str = Field(default="/", pattern=r"^/[A-Za-z0-9_\-/]*$")
    samples: int = Field(default=8, ge=1, le=20, description="number of synthetic requests")
    expectation: str = ""


@tool(
    "reproduce_issue",
    description="Issue several synthetic requests against a service and report the failure rate, "
    "latency and the failing upstream if any.",
    category=ToolCategory.DIAGNOSTIC,
    risk=RiskLevel.LOW,
    args=ReproduceArgs,
)
async def reproduce_issue(ctx: ToolContext, args: ReproduceArgs) -> ToolOutput:
    results = [
        await ctx.infrastructure.run_diagnostic(
            "reproduce", target=args.service, parameters={"endpoint": args.endpoint}
        )
        for _ in range(args.samples)
    ]
    failures = [r for r in results if int(r.get("status_code", 200)) >= 500]
    rate = len(failures) / len(results)
    latency = sum(float(r.get("latency_ms", 0)) for r in results) / len(results)
    errors: dict[str, int] = {}
    for r in failures:
        if r.get("error"):
            errors[str(r["error"])] = errors.get(str(r["error"]), 0) + 1
    version = results[0].get("version")
    summary = (
        f"{args.service}{args.endpoint}: {len(failures)}/{len(results)} requests failed "
        f"({rate:.0%}), mean latency {latency:.0f}ms, version {version}"
    )
    if errors:
        top = max(errors.items(), key=lambda kv: kv[1])
        summary += f"; most common error: {top[0]} (x{top[1]})"
    data: dict[str, Any] = {
        "service": args.service,
        "endpoint": args.endpoint,
        "samples": len(results),
        "failures": len(failures),
        "failure_rate": rate,
        "mean_latency_ms": latency,
        "errors": errors,
        "version": version,
    }
    strength = 0.9 if rate >= 0.5 else 0.75 if rate >= 0.2 else 0.5 if rate > 0 else 0.3
    return ToolOutput(
        data=data,
        summary=summary,
        evidence=[
            EvidenceDraft(
                kind=EvidenceKind.DIAGNOSTIC,
                title=f"reproduce {args.service}{args.endpoint}",
                summary=summary,
                data=data,
                strength=strength,
                service=args.service,
                tags=["reproduced" if failures else "not_reproduced"],
            )
        ],
    )


class LoadProjectionArgs(ToolArgs):
    service: str
    factor: float = Field(default=1.5, ge=1.0, le=5.0, description="traffic multiplier to project")
    expectation: str = ""


@tool(
    "run_load_projection",
    description="Project latency and replica headroom under a traffic "
    "multiplier (computation only, no load is generated).",
    category=ToolCategory.DIAGNOSTIC,
    risk=RiskLevel.LOW,
    args=LoadProjectionArgs,
)
async def run_load_projection(ctx: ToolContext, args: LoadProjectionArgs) -> ToolOutput:
    d = await ctx.infrastructure.run_diagnostic(
        "load_projection", target=args.service, parameters={"factor": args.factor}
    )
    summary = (
        f"{args.service} at {args.factor:g}x traffic: projected load {d.get('projected_load')}, "
        f"p95 {float(d.get('projected_latency_p95_ms', 0)):.0f}ms, replicas needed "
        f"{d.get('headroom_replicas')}"
    )
    return ToolOutput(
        data=d,
        summary=summary,
        evidence=[
            EvidenceDraft(
                kind=EvidenceKind.DIAGNOSTIC,
                title=f"{args.service} load projection",
                summary=summary,
                data=d,
                strength=0.4,
                service=args.service,
                tags=["capacity"],
            )
        ],
    )


DIAGNOSTIC_TOOLS: list[ToolDefinition[Any]] = [
    run_connectivity_test,
    run_database_diagnostic,
    run_cache_diagnostic,
    run_process_diagnostic,
    reproduce_issue,
    run_load_projection,
]
