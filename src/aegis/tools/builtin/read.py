"""Read-only tools. Safe to run at any time; they only observe."""

from __future__ import annotations

from collections import Counter
from datetime import timedelta
from typing import Any

from pydantic import Field

from aegis.domain.enums import EvidenceKind, RiskLevel, ToolCategory
from aegis.domain.memory import SimilarIncidentQuery
from aegis.tools.context import ToolContext
from aegis.tools.definition import EvidenceDraft, ToolArgs, ToolDefinition, ToolOutput, tool
from aegis.tools.strength import ratio_strength, recency_strength, share_strength


def _fmt(value: float, unit: str = "") -> str:
    if unit == "ratio":
        return f"{value * 100:.1f}%"
    if abs(value) >= 100:
        return f"{value:.0f}{unit and ' ' + unit}"
    return f"{value:.3g}{unit and ' ' + unit}"


class ServiceMetricArgs(ToolArgs):
    service: str = Field(description="service or infrastructure component name")
    metric: str = Field(description="metric name, e.g. latency_p95_ms, error_rate, connections")
    window_seconds: int = Field(default=300, ge=30, le=1800, description="lookback window")


@tool(
    "get_metrics",
    description="Time series for one metric of one component over a window, "
    "with baseline comparison.",
    category=ToolCategory.READ_ONLY,
    risk=RiskLevel.NONE,
    args=ServiceMetricArgs,
)
async def get_metrics(ctx: ToolContext, args: ServiceMetricArgs) -> ToolOutput:
    start, end = ctx.window(args.window_seconds)
    series = await ctx.telemetry.metrics(args.service, args.metric, start=start, end=end)
    baseline = await ctx.telemetry.baseline(args.service, args.metric)
    latest = series.latest
    mean = series.mean()
    peak = max(series.values) if series.samples else None
    ratio = (latest / baseline) if (latest is not None and baseline) else None
    trend = "flat"
    if len(series.samples) >= 6:
        head = sum(series.values[: len(series.values) // 3]) / max(1, len(series.values) // 3)
        tail = sum(series.values[-(len(series.values) // 3) :]) / max(1, len(series.values) // 3)
        if head and tail / head > 1.25:
            trend = "rising"
        elif head and tail / head < 0.8:
            trend = "falling"
    summary = (
        f"{args.service} {args.metric}: latest {_fmt(latest or 0, series.unit)}, "
        f"mean {_fmt(mean or 0, series.unit)}, peak {_fmt(peak or 0, series.unit)} over "
        f"{args.window_seconds}s ({trend})"
    )
    if baseline is not None and ratio is not None:
        summary += f"; baseline {_fmt(baseline, series.unit)} ({(ratio - 1) * 100:+.0f}%)"
    downsampled = [
        {"at": s.at.isoformat(), "value": round(s.value, 4)}
        for s in series.samples[:: max(1, len(series.samples) // 40)]
    ]
    data: dict[str, Any] = {
        "service": args.service,
        "metric": args.metric,
        "unit": series.unit,
        "latest": latest,
        "mean": mean,
        "peak": peak,
        "baseline": baseline,
        "ratio_to_baseline": ratio,
        "trend": trend,
        "samples": downsampled,
    }
    strength = ratio_strength(ratio) if ratio else 0.2
    return ToolOutput(
        data=data,
        summary=summary,
        evidence=[
            EvidenceDraft(
                kind=EvidenceKind.METRIC,
                title=f"{args.service} {args.metric}",
                summary=summary,
                data=data,
                strength=strength,
                service=args.service,
                tags=[args.metric, trend],
            )
        ],
    )


class CompareBaselineArgs(ToolArgs):
    service: str
    metric: str


@tool(
    "compare_baseline",
    description="Compare the current value of a metric with its healthy baseline.",
    category=ToolCategory.READ_ONLY,
    risk=RiskLevel.NONE,
    args=CompareBaselineArgs,
)
async def compare_baseline(ctx: ToolContext, args: CompareBaselineArgs) -> ToolOutput:
    start, end = ctx.window(60)
    series = await ctx.telemetry.metrics(args.service, args.metric, start=start, end=end)
    baseline = await ctx.telemetry.baseline(args.service, args.metric)
    current = series.mean() if series.samples else None
    if current is None:
        summary = f"{args.service} {args.metric}: no recent samples"
        return ToolOutput(data={"service": args.service, "metric": args.metric}, summary=summary)
    if baseline is None or baseline == 0:
        ratio = None
        verdict = "no baseline"
    else:
        ratio = current / baseline
        verdict = (
            "within baseline" if 0.7 <= ratio <= 1.3 else f"{(ratio - 1) * 100:+.0f}% vs baseline"
        )
    summary = (
        f"{args.service} {args.metric}: current {_fmt(current, series.unit)} vs baseline "
        f"{_fmt(baseline or 0, series.unit)} → {verdict}"
    )
    data = {
        "service": args.service,
        "metric": args.metric,
        "current": current,
        "baseline": baseline,
        "ratio": ratio,
        "verdict": verdict,
        "unit": series.unit,
    }
    return ToolOutput(
        data=data,
        summary=summary,
        evidence=[
            EvidenceDraft(
                kind=EvidenceKind.METRIC,
                title=f"{args.service} {args.metric} vs baseline",
                summary=summary,
                data=data,
                strength=ratio_strength(ratio) if ratio else 0.2,
                service=args.service,
                tags=[args.metric, "baseline"],
            )
        ],
    )


class LogsArgs(ToolArgs):
    service: str
    level: str = Field(default="WARN", description="minimum level: INFO, WARN or ERROR")
    window_seconds: int = Field(default=300, ge=30, le=1800)
    limit: int = Field(default=40, ge=1, le=200)


@tool(
    "get_logs",
    description="Recent log lines for a service, grouped by message pattern.",
    category=ToolCategory.READ_ONLY,
    risk=RiskLevel.NONE,
    args=LogsArgs,
)
async def get_logs(ctx: ToolContext, args: LogsArgs) -> ToolOutput:
    start, end = ctx.window(args.window_seconds)
    logs = await ctx.telemetry.logs(
        args.service, start=start, end=end, level=args.level, limit=args.limit
    )
    patterns: Counter[str] = Counter()
    levels: Counter[str] = Counter()
    deps: Counter[str] = Counter()
    for entry in logs:
        patterns[_pattern(entry.message)] += 1
        levels[entry.level] += 1
        if "dependency" in entry.attributes:
            deps[entry.attributes["dependency"]] += 1
    top = patterns.most_common(6)
    summary = (
        f"{args.service}: {len(logs)} {args.level}+ log lines in {args.window_seconds}s "
        f"({', '.join(f'{k} {v}' for k, v in levels.items()) or 'none'})"
    )
    if top:
        summary += "; top: " + " | ".join(f"{p} (x{c})" for p, c in top[:3])
    if deps:
        summary += "; dependencies mentioned: " + ", ".join(
            f"{d} x{c}" for d, c in deps.most_common(3)
        )
    data = {
        "service": args.service,
        "count": len(logs),
        "levels": dict(levels),
        "patterns": [{"pattern": p, "count": c} for p, c in top],
        "dependencies_mentioned": dict(deps),
        "samples": [
            {"at": e.at.isoformat(), "level": e.level, "message": e.message} for e in logs[:8]
        ],
    }
    errors = levels.get("ERROR", 0)
    strength = 0.15 if not logs else 0.4 if errors == 0 else 0.6 if errors < 10 else 0.75
    return ToolOutput(
        data=data,
        summary=summary,
        evidence=[
            EvidenceDraft(
                kind=EvidenceKind.LOG,
                title=f"{args.service} logs ({args.level}+)",
                summary=summary,
                data=data,
                strength=strength,
                service=args.service,
                tags=list(deps)[:3],
            )
        ],
    )


def _pattern(message: str) -> str:
    import re  # noqa: PLC0415 - local to keep the module header light

    out = re.sub(r"\d+(\.\d+)?", "N", message)
    return out[:120]


class TracesArgs(ToolArgs):
    service: str | None = Field(default=None, description="only traces touching this service")
    errors_only: bool = True
    window_seconds: int = Field(default=300, ge=30, le=1800)
    limit: int = Field(default=12, ge=1, le=50)


@tool(
    "query_traces",
    description="Sampled distributed traces: which span fails first and where time is spent.",
    category=ToolCategory.READ_ONLY,
    risk=RiskLevel.NONE,
    args=TracesArgs,
)
async def query_traces(ctx: ToolContext, args: TracesArgs) -> ToolOutput:
    start, end = ctx.window(args.window_seconds)
    traces = await ctx.telemetry.traces(
        service=args.service, start=start, end=end, errors_only=args.errors_only, limit=args.limit
    )
    if not traces:
        summary = f"no {'error ' if args.errors_only else ''}traces found" + (
            f" for {args.service}" if args.service else ""
        )
        return ToolOutput(
            data={"count": 0},
            summary=summary,
            evidence=[
                EvidenceDraft(
                    kind=EvidenceKind.TRACE,
                    title="trace query",
                    summary=summary,
                    strength=0.2,
                    service=args.service,
                )
            ],
        )
    failing: Counter[str] = Counter()
    slow_total: Counter[str] = Counter()
    for tr in traces:
        error_spans = [s for s in tr.spans if s.error]
        if error_spans:
            # the deepest failing span is the origin; its ancestors fail because of it
            origin = max(error_spans, key=lambda s: s.depth)
            failing[origin.service] += 1
        slowest = max(tr.spans[1:], key=lambda s: s.duration_ms, default=None)
        if slowest:
            slow_total[slowest.service] += 1
    errored = sum(1 for t in traces if t.error)
    avg = sum(t.duration_ms for t in traces) / len(traces)
    summary = f"{len(traces)} traces sampled, {errored} errored, avg {avg:.0f}ms"
    if failing:
        summary += "; failure origin (deepest failing span): " + ", ".join(
            f"{s} x{c}" for s, c in failing.most_common(3)
        )
    if slow_total:
        summary += "; slowest span most often in: " + ", ".join(
            f"{s} x{c}" for s, c in slow_total.most_common(2)
        )
    data = {
        "count": len(traces),
        "errored": errored,
        "avg_duration_ms": avg,
        "failure_origin": dict(failing),
        "slowest_span": dict(slow_total),
        "examples": [
            {
                "trace_id": t.trace_id,
                "duration_ms": round(t.duration_ms),
                "error": t.error,
                "spans": [
                    {"service": s.service, "ms": round(s.duration_ms), "error": s.error}
                    for s in t.spans
                ],
            }
            for t in traces[:3]
        ],
    }
    culprit = (
        failing.most_common(1)[0][0]
        if failing
        else (slow_total.most_common(1)[0][0] if slow_total else None)
    )
    strength = share_strength(
        (failing.most_common(1)[0][1] / len(traces))
        if failing
        else (slow_total.most_common(1)[0][1] / len(traces))
        if slow_total
        else 0
    )
    return ToolOutput(
        data=data,
        summary=summary,
        evidence=[
            EvidenceDraft(
                kind=EvidenceKind.TRACE,
                title="trace analysis",
                summary=summary,
                data=data,
                strength=strength,
                service=culprit,
                tags=[c for c, _ in failing.most_common(2)],
            )
        ],
    )


class ServiceArgs(ToolArgs):
    service: str


@tool(
    "get_health",
    description="Health checks of a service or infrastructure component.",
    category=ToolCategory.READ_ONLY,
    risk=RiskLevel.NONE,
    args=ServiceArgs,
)
async def get_health(ctx: ToolContext, args: ServiceArgs) -> ToolOutput:
    health = await ctx.telemetry.health(args.service)
    failing = [k for k, v in health.checks.items() if not v]
    summary = f"{args.service} is {health.state.value}" + (
        f"; failing checks: {', '.join(failing)}" if failing else "; all checks passing"
    )
    data = {
        "service": args.service,
        "state": health.state.value,
        "checks": health.checks,
        "message": health.message,
    }
    strength = {"healthy": 0.3, "degraded": 0.6, "unhealthy": 0.85, "unknown": 0.1}[
        health.state.value
    ]
    return ToolOutput(
        data=data,
        summary=summary,
        evidence=[
            EvidenceDraft(
                kind=EvidenceKind.HEALTH,
                title=f"{args.service} health",
                summary=summary,
                data=data,
                strength=strength,
                service=args.service,
                tags=[health.state.value, *failing[:3]],
            )
        ],
    )


@tool(
    "inspect_service",
    description="Health, resource usage, process state and open connections of one service.",
    category=ToolCategory.READ_ONLY,
    risk=RiskLevel.NONE,
    args=ServiceArgs,
)
async def inspect_service(ctx: ToolContext, args: ServiceArgs) -> ToolOutput:
    health = await ctx.telemetry.health(args.service)
    resource = await ctx.telemetry.resource(args.service)
    process = (
        await ctx.infrastructure.run_diagnostic("process", target=args.service, parameters={})
        if resource.kind == "service"
        else {}
    )
    m = resource.metrics
    failing = [k for k, v in health.checks.items() if not v]
    parts = [f"{args.service} ({resource.kind}) is {health.state.value}"]
    if failing:
        parts.append("failing: " + ", ".join(failing))
    if resource.kind == "service":
        parts.append(
            f"error_rate {m.get('error_rate', 0) * 100:.1f}%, "
            f"p95 {m.get('latency_p95_ms', 0):.0f}ms, "
            f"cpu {m.get('cpu_percent', 0):.0f}%, mem {m.get('memory_percent', 0):.0f}%"
        )
        if m.get("redis_pool_wait_ms", 0) > 50 or m.get("db_pool_wait_ms", 0) > 50:
            parts.append(
                f"pool waits: redis {m.get('redis_pool_wait_ms', 0):.0f}ms, "
                f"db {m.get('db_pool_wait_ms', 0):.0f}ms"
            )
        if process:
            parts.append(
                f"version {process.get('version')}, uptime {process.get('uptime_seconds')}s, "
                f"replicas {process.get('replicas_ready')}/{process.get('replicas')}, "
                f"restarts {process.get('restart_count')}, open redis conns "
                f"{process.get('open_redis_connections')}, open db conns "
                f"{process.get('open_db_connections')}"
            )
    else:
        parts.append(
            f"connections {m.get('connections', 0):.0f}/{m.get('max_connections', 0):.0f} "
            f"(saturation {m.get('saturation', 0) * 100:.0f}%), "
            f"p95 {m.get('latency_p95_ms', 0):.1f}ms"
        )
        if resource.top_clients:
            top = list(resource.top_clients.items())[:3]
            parts.append("top clients: " + ", ".join(f"{k} {v:.0f}" for k, v in top))
    summary = "; ".join(parts)
    data = {
        "service": args.service,
        "kind": resource.kind,
        "health": health.state.value,
        "failing_checks": failing,
        "metrics": m,
        "attributes": resource.attributes,
        "process": process,
        "top_clients": resource.top_clients,
    }
    strength = {"healthy": 0.3, "degraded": 0.65, "unhealthy": 0.85, "unknown": 0.1}[
        health.state.value
    ]
    return ToolOutput(
        data=data,
        summary=summary,
        evidence=[
            EvidenceDraft(
                kind=EvidenceKind.HEALTH,
                title=f"{args.service} inspection",
                summary=summary,
                data=data,
                strength=strength,
                service=args.service,
                tags=[health.state.value],
            )
        ],
    )


@tool(
    "inspect_dependencies",
    description="Dependency graph around a service and which "
    "dependencies contribute errors or latency right now.",
    category=ToolCategory.READ_ONLY,
    risk=RiskLevel.NONE,
    args=ServiceArgs,
)
async def inspect_dependencies(ctx: ToolContext, args: ServiceArgs) -> ToolOutput:
    topology = await ctx.telemetry.topology()
    deps = topology.dependencies_of(args.service)
    dependents = topology.dependents_of(args.service)
    detail: dict[str, Any] = {}
    node = next((n for n in topology.nodes if n.name == args.service), None)
    if node is not None and node.kind in ("service", "gateway"):
        detail = await ctx.infrastructure.run_diagnostic(
            "dependencies", target=args.service, parameters={}
        )
    unhealthy: list[str] = []
    contributions: list[dict[str, Any]] = detail.get("dependencies", [])
    for c in contributions:
        if not c.get("available", True) or float(c.get("error_contribution", 0)) > 0.05:
            state = (
                "down"
                if not c.get("available", True)
                else f"{round(float(c['error_contribution']) * 100)}% errors"
            )
            unhealthy.append(f"{c['target']} ({state})")
    summary = (
        f"{args.service} depends on {', '.join(deps) or 'nothing'}; depended on by "
        f"{', '.join(dependents) or 'nothing'}"
    )
    if unhealthy:
        summary += "; unhealthy dependencies: " + ", ".join(unhealthy)
    elif contributions:
        summary += "; all dependencies healthy"
    if detail:
        summary += (
            f"; own error rate {float(detail.get('own_error_rate', 0)) * 100:.1f}%, "
            f"own latency {float(detail.get('own_latency_ms', 0)):.0f}ms"
        )
    data = {
        "service": args.service,
        "dependencies": deps,
        "dependents": dependents,
        "contributions": contributions,
        "own_error_rate": detail.get("own_error_rate"),
        "own_latency_ms": detail.get("own_latency_ms"),
        "downstream_closure": topology.downstream_closure(args.service),
    }
    strength = 0.8 if unhealthy else 0.35
    culprit = contributions[0]["target"] if unhealthy and contributions else args.service
    return ToolOutput(
        data=data,
        summary=summary,
        evidence=[
            EvidenceDraft(
                kind=EvidenceKind.TOPOLOGY,
                title=f"{args.service} dependencies",
                summary=summary,
                data=data,
                strength=strength,
                service=culprit,
                tags=[u.split(" ")[0] for u in unhealthy[:3]],
            )
        ],
    )


@tool(
    "inspect_deployment",
    description="Deployment history of a service: current version, when "
    "it changed, whether rollback is available.",
    category=ToolCategory.READ_ONLY,
    risk=RiskLevel.NONE,
    args=ServiceArgs,
)
async def inspect_deployment(ctx: ToolContext, args: ServiceArgs) -> ToolOutput:
    deployments = await ctx.telemetry.deployments(args.service)
    now = ctx.now()
    if not deployments:
        summary = f"no deployment records for {args.service}"
        return ToolOutput(data={"service": args.service, "deployments": []}, summary=summary)
    latest = deployments[-1]
    age_min = (now - latest.deployed_at).total_seconds() / 60
    recent = age_min <= 60
    if age_min < 60:
        age_text = f"{age_min:.0f} min ago"
    elif age_min < 60 * 48:
        age_text = f"{age_min / 60:.0f} h ago"
    else:
        age_text = f"{age_min / 1440:.0f} days ago"
    summary = (
        f"{args.service} runs {latest.version} deployed {age_text}"
        f"{' (previous ' + latest.previous_version + ')' if latest.previous_version else ''}"
        f"; change: {latest.change_summary or 'n/a'}; rollback "
        f"{'available' if latest.rollback_available else 'unavailable'}"
    )
    if not recent:
        summary += " — NO deployment in the last 60 min; version is stable"
    if recent and latest.deployed_at >= ctx.incident.detected_at - timedelta(minutes=30):
        summary += " — deployment precedes the incident window"
    data = {
        "service": args.service,
        "current_version": latest.version,
        "previous_version": latest.previous_version,
        "deployed_at": latest.deployed_at.isoformat(),
        "age_minutes": age_min,
        "recent": recent,
        "rollback_available": latest.rollback_available,
        "history": [
            {
                "version": d.version,
                "deployed_at": d.deployed_at.isoformat(),
                "summary": d.change_summary,
            }
            for d in deployments[-5:]
        ],
    }
    return ToolOutput(
        data=data,
        summary=summary,
        evidence=[
            EvidenceDraft(
                kind=EvidenceKind.DEPLOYMENT,
                title=f"{args.service} deployment {latest.version}",
                summary=summary,
                data=data,
                strength=recency_strength(latest.deployed_at, now),
                service=args.service,
                tags=["recent_deploy" if recent else "stable"],
            )
        ],
    )


class ComponentArgs(ToolArgs):
    component: str = Field(default="postgres")


@tool(
    "inspect_database",
    description="Database connection usage by client, idle-in-transaction "
    "sessions, saturation and latency.",
    category=ToolCategory.READ_ONLY,
    risk=RiskLevel.NONE,
    args=ComponentArgs,
)
async def inspect_database(ctx: ToolContext, args: ComponentArgs) -> ToolOutput:
    d = await ctx.infrastructure.run_diagnostic("database", target=args.component, parameters={})
    return _resource_output(args.component, d, kind="database")


class CacheArgs(ToolArgs):
    component: str = Field(default="redis")


@tool(
    "inspect_redis",
    description="Cache client connections by service, saturation, blocked clients, hit rate.",
    category=ToolCategory.READ_ONLY,
    risk=RiskLevel.NONE,
    args=CacheArgs,
)
async def inspect_redis(ctx: ToolContext, args: CacheArgs) -> ToolOutput:
    d = await ctx.infrastructure.run_diagnostic("cache", target=args.component, parameters={})
    return _resource_output(args.component, d, kind="cache")


def _resource_output(component: str, d: dict[str, Any], *, kind: str) -> ToolOutput:
    clients: dict[str, float] = d.get("connections_by_client") or d.get("clients_by_service") or {}
    total = float(d.get("connections") or d.get("connected_clients") or 0)
    maximum = float(d.get("max_connections") or d.get("max_clients") or 0)
    sat = float(d.get("saturation", 0))
    top = sorted(clients.items(), key=lambda kv: -kv[1])
    top_client, top_count = top[0] if top else (None, 0.0)
    share = (top_count / total) if total else 0.0
    parts = [
        f"{component}: {total:.0f}/{maximum:.0f} connections ({sat * 100:.0f}% saturation), "
        f"p95 {float(d.get('latency_p95_ms', 0)):.1f}ms"
    ]
    if top:
        parts.append("by client: " + ", ".join(f"{k} {v:.0f}" for k, v in top[:4]))
    if top_client and share >= 0.4:
        parts.append(f"{top_client} holds {share * 100:.0f}% of connections")
    leaked = d.get("leaked_by_service") or d.get("idle_in_transaction_by_client") or {}
    if leaked:
        parts.append(
            ("leaked" if kind == "cache" else "idle-in-transaction")
            + " by service: "
            + ", ".join(f"{k} {v}" for k, v in leaked.items())
        )
    if kind == "database" and float(d.get("idle_in_transaction", 0)) > 0:
        parts.append(f"idle_in_transaction {float(d['idle_in_transaction']):.0f}")
    if kind == "cache":
        parts.append(
            f"hit rate {float(d.get('hit_rate', 0)) * 100:.0f}%, blocked clients "
            f"{float(d.get('blocked_clients', 0)):.0f}"
        )
    if not d.get("available", True):
        parts.append("COMPONENT UNAVAILABLE")
    summary = "; ".join(parts)
    strength = max(
        share_strength(share) if sat > 0.5 else 0.2,
        0.9 if sat >= 0.95 else 0.0,
        0.95 if not d.get("available", True) else 0.0,
    )
    return ToolOutput(
        data=d,
        summary=summary,
        evidence=[
            EvidenceDraft(
                kind=EvidenceKind.DIAGNOSTIC,
                title=f"{component} connection analysis",
                summary=summary,
                data=d,
                strength=strength,
                service=top_client if share >= 0.4 else component,
                tags=[kind, "saturated" if sat >= 0.85 else "ok"],
            )
        ],
    )


class MemorySearchArgs(ToolArgs):
    query: str = Field(description="free-text description of the symptoms")
    limit: int = Field(default=3, ge=1, le=10)


@tool(
    "search_incident_memory",
    description="Find similar past incidents (root cause, "
    "remediation, outcome). Memory is evidence, not truth.",
    category=ToolCategory.READ_ONLY,
    risk=RiskLevel.NONE,
    args=MemorySearchArgs,
    timeout_seconds=20.0,
)
async def search_incident_memory(ctx: ToolContext, args: MemorySearchArgs) -> ToolOutput:
    if ctx.memory_search is None:
        return ToolOutput(data={"matches": []}, summary="incident memory is not available")
    matches = await ctx.memory_search(
        SimilarIncidentQuery(
            text=args.query,
            affected_services=list(ctx.incident.affected_services),
            symptoms=[s.description for s in ctx.incident.signals],
            limit=args.limit,
            exclude_incident_id=ctx.incident.id,
        )
    )
    if not matches:
        return ToolOutput(
            data={"matches": []},
            summary="no similar past incidents found",
            evidence=[
                EvidenceDraft(
                    kind=EvidenceKind.MEMORY,
                    title="memory search",
                    summary="no similar past incidents",
                    strength=0.1,
                )
            ],
        )
    evidence = []
    lines = []
    for m in matches:
        mem = m.memory
        line = (
            f"INC-{mem.incident_number} ({m.similarity * 100:.0f}% similar): {mem.title}; root "
            f"cause: {mem.root_cause}; resolution: {mem.resolution}; outcome {mem.outcome}"
        )
        lines.append(line)
        evidence.append(
            EvidenceDraft(
                kind=EvidenceKind.MEMORY,
                title=f"similar incident INC-{mem.incident_number}",
                summary=line,
                data={
                    "memory_id": str(mem.id),
                    "incident_id": str(mem.incident_id),
                    "similarity": m.similarity,
                    "root_cause": mem.root_cause,
                    "root_cause_service": mem.root_cause_service,
                    "actions": mem.actions,
                    "outcome": mem.outcome,
                },
                strength=min(0.6, m.similarity * 0.7),
                service=mem.root_cause_service,
                tags=["memory", mem.root_cause_category],
            )
        )
    return ToolOutput(
        data={"matches": [e.data for e in evidence]},
        summary=f"{len(matches)} similar incidents: " + " || ".join(lines),
        evidence=evidence,
    )


READ_TOOLS: list[ToolDefinition[Any]] = [
    get_metrics,
    compare_baseline,
    get_logs,
    query_traces,
    get_health,
    inspect_service,
    inspect_dependencies,
    inspect_deployment,
    inspect_database,
    inspect_redis,
    search_incident_memory,
]
