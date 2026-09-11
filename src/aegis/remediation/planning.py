"""Deterministic construction of what a remediation must prove, and guards on what it may target."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from aegis.domain.action import RollbackPlan, VerificationCondition, VerificationSpec
from aegis.domain.enums import EvidenceKind, HypothesisCategory, RiskLevel, SignalKind
from aegis.domain.evidence import Evidence
from aegis.domain.hypothesis import Hypothesis
from aegis.domain.incident import Incident
from aegis.domain.telemetry import Topology
from aegis.domain.tool import ToolSpec

# metric → (comparator, absolute target, ratio to baseline). Ratio wins when a baseline exists.
_METRIC_RULES: dict[str, tuple[str, float | None, float | None]] = {
    "latency_p95_ms": ("lte", None, 1.5),
    "latency_p50_ms": ("lte", None, 1.5),
    "error_rate": ("lte", 0.03, None),
    "request_rate": ("lte", None, 1.8),
    "connections": ("lte", None, 1.6),
    "saturation": ("lte", 0.85, None),
    "idle_in_transaction": ("lte", 10.0, None),
    "cpu_percent": ("lte", 85.0, None),
    "memory_percent": ("lte", 85.0, None),
    "db_pool_wait_ms": ("lte", 100.0, None),
    "redis_pool_wait_ms": ("lte", 100.0, None),
    "hit_rate": ("gte", 0.8, None),
    "up": ("gte", 1.0, None),
}


def build_verification_spec(
    incident: Incident,
    *,
    baselines: dict[tuple[str, str], float | None],
    target_service: str | None,
    tool_spec: ToolSpec | None,
    stabilization_seconds: int = 30,
    timeout_seconds: int = 240,
) -> VerificationSpec:
    """Verification conditions come from the *symptoms* (detection signals), plus the metrics the
    remediation tool declares it affects on its target. Never from the model."""
    conditions: dict[tuple[str, str], VerificationCondition] = {}

    def add(service: str, metric: str) -> None:
        rule = _METRIC_RULES.get(metric)
        if rule is None or (service, metric) in conditions:
            return
        comparator, absolute, ratio = rule
        baseline = baselines.get((service, metric))
        if ratio is not None and baseline is not None and baseline > 0:
            conditions[(service, metric)] = VerificationCondition(
                metric=metric,
                service=service,
                comparator="lte",
                max_ratio_to_baseline=ratio,
                description=f"{service} {metric} within {ratio:g}x of baseline",
            )
        elif absolute is not None:
            if metric == "error_rate" and baseline is not None:
                absolute = max(absolute, baseline + 0.02)
            conditions[(service, metric)] = VerificationCondition(
                metric=metric,
                service=service,
                comparator=comparator,
                target=absolute,
                description=f"{service} {metric} {comparator} {absolute:g}",
            )

    for signal in incident.signals:
        add(signal.service, signal.metric)
    if target_service and tool_spec is not None:
        for metric in tool_spec.verification_metrics:
            add(target_service, metric)
    if target_service:
        add(target_service, "up")
    if not conditions:
        for service in incident.affected_services:
            add(service, "error_rate")
            add(service, "latency_p95_ms")
    return VerificationSpec(
        conditions=tuple(conditions.values()),
        stabilization_seconds=stabilization_seconds,
        timeout_seconds=timeout_seconds,
    )


def default_rollback(
    tool_name: str, arguments: dict[str, Any], *, previous: dict[str, Any] | None = None
) -> RollbackPlan:
    previous = previous or {}
    match tool_name:
        case "rollback_deployment":
            if previous.get("from_version"):
                return RollbackPlan(
                    available=True,
                    tool_name="rollback_deployment",
                    arguments={
                        "service": arguments.get("service"),
                        "to_version": previous["from_version"],
                    },
                    reason="re-deploy the version that was rolled back",
                )
            return RollbackPlan(available=False, reason="original version unknown until execution")
        case "scale_service":
            if previous.get("from_replicas"):
                return RollbackPlan(
                    available=True,
                    tool_name="scale_service",
                    arguments={
                        "service": arguments.get("service"),
                        "replicas": previous["from_replicas"],
                    },
                    reason="restore the previous replica count",
                )
            return RollbackPlan(
                available=False, reason="previous replica count unknown until execution"
            )
        case "restart_service" | "rotate_connection_pool" | "clear_cache":
            return RollbackPlan(
                available=False,
                reason=f"{tool_name} is not reversible; a failed verification escalates",
            )
    return RollbackPlan(available=False, reason="no rollback defined for this tool")


class TargetMismatch(Exception):  # noqa: N818 - domain vocabulary
    pass


def validate_remediation_target(
    tool_name: str, arguments: dict[str, Any], hypothesis: Hypothesis, topology: Topology
) -> str:
    """The remediation must act on the root-cause service (or, for pool rotation, on the client that
    holds the resource). Restarting a symptomatic caller is the classic wrong move; refuse it."""
    root = hypothesis.suspected_root_cause_service
    if root is None:
        raise TargetMismatch("hypothesis has no root-cause service")
    target = arguments.get("service") or arguments.get("component")
    if not isinstance(target, str):
        raise TargetMismatch("remediation arguments name no service or component")
    if target == root:
        return target
    if (
        tool_name == "rotate_connection_pool"
        and arguments.get("target") == root
        and target in (topology.dependents_of(root))
    ):
        # e.g. hypothesis blames redis saturation; rotating the leaking client's pool is legitimate.
        return target
    kinds = {n.name: n.kind for n in topology.nodes}
    if kinds.get(root) in ("database", "cache") and root in topology.dependencies_of(target):
        # remediating the heaviest client of a saturated shared resource
        return target
    raise TargetMismatch(
        f"remediation targets '{target}' but the confirmed root cause is '{root}'; act on the "
        f"root cause, not on a symptomatic caller"
    )


class RemediationMismatch(Exception):  # noqa: N818 - domain vocabulary
    pass


# Which diagnosed mechanisms each remediation can plausibly address.
_FIT: dict[str, frozenset[HypothesisCategory]] = {
    "restart_service": frozenset(
        {
            HypothesisCategory.RESOURCE_EXHAUSTION,
            HypothesisCategory.DEPENDENCY_FAILURE,
            HypothesisCategory.CONFIGURATION,
            HypothesisCategory.UNKNOWN,
        }
    ),
    "rotate_connection_pool": frozenset({HypothesisCategory.RESOURCE_EXHAUSTION}),
    "rollback_deployment": frozenset({HypothesisCategory.DEPLOYMENT_REGRESSION}),
    "scale_service": frozenset({HypothesisCategory.CAPACITY}),
    "clear_cache": frozenset(
        {
            HypothesisCategory.CONFIGURATION,
            HypothesisCategory.UNKNOWN,
            HypothesisCategory.RESOURCE_EXHAUSTION,
        }
    ),
}


def _metric_of(evidence: Evidence, metric: str) -> float | None:
    """Read a numeric metric from evidence data, top-level or under ``metrics``.

    Booleans count: a process diagnostic reports ``up`` as a bool. ``None`` means the evidence
    says nothing about this metric, which is different from saying it is zero.
    """
    data = evidence.data
    nested = data.get("metrics")
    for source in (data, nested if isinstance(nested, dict) else {}):
        value = source.get(metric)
        if isinstance(value, bool):
            return 1.0 if value else 0.0
        if isinstance(value, int | float):
            return float(value)
    return None


def _at_least(evidence: Evidence, metric: str, threshold: float) -> bool:
    value = _metric_of(evidence, metric)
    return value is not None and value >= threshold


def _effective_category(
    hypothesis: Hypothesis, evidence: Sequence[Evidence], target: str
) -> HypothesisCategory:
    """Prefer what the evidence shows over the label the model chose.

    Two overrides, both of which change which remediation is admissible:

    * a component that is down or crashed is a dependency failure, whatever it was called, so it
      is restarted rather than rolled back or scaled;
    * a component pinned at the CPU limit with no leaked connections and no memory pressure is a
      capacity problem, so it is scaled rather than restarted.
    """
    mine = [e for e in evidence if e.service == target]
    if any(
        "process_up" in e.tags or e.data.get("crashed") is True or _metric_of(e, "up") == 0.0
        for e in mine
    ):
        return HypothesisCategory.DEPENDENCY_FAILURE
    cpu_bound = any("cpu_ok" in e.tags or _at_least(e, "cpu_percent", 90.0) for e in mine)
    memory_pressure = any(
        "memory_ok" in e.tags or _at_least(e, "memory_percent", 85.0) for e in mine
    )
    leaked = any(
        target
        in (e.data.get("leaked_by_service") or e.data.get("idle_in_transaction_by_client") or {})
        or (
            e.service == target
            and (
                _at_least(e, "redis_pool_wait_ms", 200.0) or _at_least(e, "db_pool_wait_ms", 200.0)
            )
        )
        for e in evidence
    )
    if cpu_bound and not memory_pressure and not leaked:
        return HypothesisCategory.CAPACITY
    return hypothesis.category


def validate_remediation_fit(
    tool_name: str, hypothesis: Hypothesis, evidence: Sequence[Evidence], target: str
) -> None:
    """A remediation must address the diagnosed mechanism, not merely the right service.

    The mechanism is read from the evidence, not from the label the model attached to its
    hypothesis, because the label is the part a model gets wrong. Rolling back a service that was
    never deployed, restarting one that is simply out of capacity, or scaling one that has crashed
    are the classic wrong moves; each is refused with an explanation the model can act on.
    """
    allowed = _FIT.get(tool_name)
    if allowed is None:
        return
    if tool_name == "rollback_deployment":
        recent_deploy = any(
            e.kind is EvidenceKind.DEPLOYMENT and e.service == target and bool(e.data.get("recent"))
            for e in evidence
        )
        if not recent_deploy:
            raise RemediationMismatch(
                f"rollback_deployment needs evidence that {target} was deployed recently. Call "
                "inspect_deployment on it first; if the running version is not new, the fault is "
                "not a regression and a rollback cannot fix it."
            )
        return
    effective = _effective_category(hypothesis, evidence, target)
    if effective is HypothesisCategory.CAPACITY and tool_name in (
        "restart_service",
        "rotate_connection_pool",
    ):
        raise RemediationMismatch(
            f"{tool_name} does not fit: the evidence shows {target} is CPU-saturated with no "
            "leaked state and no memory pressure, which is a capacity problem. Restarting returns "
            "the same replicas to the same load; use scale_service instead."
        )
    if effective not in allowed:
        diagnosed = (
            effective.value
            if effective is hypothesis.category
            else (
                f"{effective.value} (the evidence overrides the stated {hypothesis.category.value})"
            )
        )
        raise RemediationMismatch(
            f"{tool_name} does not fit a {diagnosed} diagnosis; suitable tools: "
            + ", ".join(sorted(t for t, cats in _FIT.items() if effective in cats))
        )


def risk_for(tool_spec: ToolSpec, hypothesis: Hypothesis, incident: Incident) -> RiskLevel:
    risk = tool_spec.risk
    if incident.severity.value == "sev1" and risk.rank < RiskLevel.MEDIUM.rank:
        return RiskLevel.MEDIUM
    return risk


def category_default_action(  # noqa: PLR0911 - one return per category
    hypothesis: Hypothesis, topology: Topology, remediation_tools: frozenset[str]
) -> tuple[str, dict[str, Any]] | None:
    """Deterministic remediation choice used when the LLM is unavailable."""
    root = hypothesis.suspected_root_cause_service
    if root is None:
        return None
    kinds = {n.name: n.kind for n in topology.nodes}
    match hypothesis.category:
        case HypothesisCategory.DEPLOYMENT_REGRESSION:
            if "rollback_deployment" in remediation_tools:
                return "rollback_deployment", {"service": root}
        case HypothesisCategory.DEPENDENCY_FAILURE:
            if "restart_service" in remediation_tools:
                return "restart_service", {"service": root}
        case HypothesisCategory.RESOURCE_EXHAUSTION:
            if kinds.get(root) in ("database", "cache"):
                return None  # need the client; the deterministic planner resolves it separately
            if "restart_service" in remediation_tools:
                return "restart_service", {"service": root}
        case HypothesisCategory.CAPACITY:
            if "scale_service" in remediation_tools:
                replicas = next((n.replicas for n in topology.nodes if n.name == root), 1) + 2
                return "scale_service", {"service": root, "replicas": min(10, replicas)}
        case _:
            return None
    return None


def signal_kinds(incident: Incident) -> set[SignalKind]:
    return {s.kind for s in incident.signals}
