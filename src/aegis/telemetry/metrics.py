"""Prometheus metrics for every Aegis process. Names follow the plan (``aegis_*``)."""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

REGISTRY = CollectorRegistry(auto_describe=True)

INCIDENTS_TOTAL = Counter(
    "aegis_incidents_total", "Incidents opened", ["severity", "flow"], registry=REGISTRY
)
INCIDENTS_ACTIVE = Gauge("aegis_incidents_active", "Currently active incidents", registry=REGISTRY)
INCIDENT_RESOLUTION_SECONDS = Histogram(
    "aegis_incident_resolution_seconds",
    "Detection-to-resolution time",
    ["outcome"],
    buckets=(30, 60, 120, 300, 600, 900, 1800, 3600),
    registry=REGISTRY,
)
AGENT_RUNS_TOTAL = Counter(
    "aegis_agent_runs_total", "Agent phase runs", ["phase", "termination"], registry=REGISTRY
)
AGENT_ITERATIONS = Histogram(
    "aegis_agent_iterations",
    "Iterations per agent phase",
    ["phase"],
    buckets=(1, 2, 3, 4, 6, 8, 12, 16, 24),
    registry=REGISTRY,
)
TOOL_CALLS_TOTAL = Counter(
    "aegis_tool_calls_total", "Tool executions", ["tool", "status"], registry=REGISTRY
)
TOOL_FAILURES_TOTAL = Counter(
    "aegis_tool_failures_total", "Tool failures", ["tool"], registry=REGISTRY
)
TOOL_LATENCY_SECONDS = Histogram(
    "aegis_tool_latency_seconds",
    "Tool execution latency",
    ["tool"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
    registry=REGISTRY,
)
POLICY_DENIALS_TOTAL = Counter(
    "aegis_policy_denials_total", "Authorization denials", ["code", "tool"], registry=REGISTRY
)
APPROVAL_WAIT_SECONDS = Histogram(
    "aegis_approval_wait_seconds",
    "Time humans took to decide",
    buckets=(5, 15, 30, 60, 120, 300, 600, 900),
    registry=REGISTRY,
)
APPROVALS_TOTAL = Counter(
    "aegis_approvals_total", "Approval decisions", ["decision"], registry=REGISTRY
)
REMEDIATION_TOTAL = Counter(
    "aegis_remediation_total", "Remediation executions", ["tool", "status"], registry=REGISTRY
)
ROLLBACK_TOTAL = Counter("aegis_rollback_total", "Rollbacks", ["status"], registry=REGISTRY)
VERIFICATION_TOTAL = Counter(
    "aegis_verification_total", "Verification outcomes", ["status"], registry=REGISTRY
)
LLM_CALLS_TOTAL = Counter(
    "aegis_llm_calls_total", "LLM completions", ["model", "schema", "outcome"], registry=REGISTRY
)
LLM_TOKENS_TOTAL = Counter(
    "aegis_llm_tokens_total", "LLM tokens", ["model", "kind"], registry=REGISTRY
)
LLM_LATENCY_SECONDS = Histogram(
    "aegis_llm_latency_seconds",
    "LLM completion latency",
    ["model"],
    buckets=(0.5, 1, 2, 4, 8, 15, 30, 60),
    registry=REGISTRY,
)
DETECTION_SIGNALS_TOTAL = Counter(
    "aegis_detection_signals_total", "Anomaly signals", ["kind"], registry=REGISTRY
)
DETECTION_CYCLE_SECONDS = Histogram(
    "aegis_detection_cycle_seconds",
    "Detection cycle duration",
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5),
    registry=REGISTRY,
)
HTTP_REQUESTS_TOTAL = Counter(
    "aegis_http_requests_total", "API requests", ["method", "route", "status"], registry=REGISTRY
)
HTTP_LATENCY_SECONDS = Histogram(
    "aegis_http_request_seconds",
    "API latency",
    ["method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5),
    registry=REGISTRY,
)
