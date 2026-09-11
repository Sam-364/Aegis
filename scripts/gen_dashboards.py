"""Generate the provisioned Grafana dashboards (kept as a script so panels stay consistent)."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "infrastructure" / "grafana" / "provisioning" / "dashboards"


def target(expr: str, legend: str = "") -> dict:
    return {
        "refId": "A",
        "datasource": {"type": "prometheus", "uid": "prometheus"},
        "expr": expr,
        "legendFormat": legend or "__auto",
    }


def panel(
    pid: int,
    title: str,
    kind: str,
    targets: list[dict],
    x: int,
    y: int,
    w: int,
    h: int,
    unit: str | None = None,
    extra: dict | None = None,
) -> dict:
    p = {
        "id": pid,
        "title": title,
        "type": kind,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "datasource": {"type": "prometheus", "uid": "prometheus"},
        "targets": targets,
        "fieldConfig": {"defaults": {"color": {"mode": "palette-classic"}}, "overrides": []},
        "options": {},
    }
    if unit:
        p["fieldConfig"]["defaults"]["unit"] = unit
    if extra:
        p.update(extra)
    return p


def dashboard(uid: str, title: str, panels: list[dict], tags: list[str]) -> dict:
    return {
        "uid": uid,
        "title": title,
        "tags": tags,
        "timezone": "browser",
        "schemaVersion": 39,
        "version": 1,
        "editable": True,
        "refresh": "10s",
        "time": {"from": "now-30m", "to": "now"},
        "templating": {"list": []},
        "panels": panels,
    }


aegis = dashboard(
    "aegis-overview",
    "Aegis — Runtime Overview",
    [
        panel(1, "Active incidents", "stat", [target("aegis_incidents_active")], 0, 0, 4, 4),
        panel(
            2,
            "Incidents opened (24h)",
            "stat",
            [target("sum(increase(aegis_incidents_total[24h]))")],
            4,
            0,
            4,
            4,
        ),
        panel(
            3,
            "Pending approvals wait p50",
            "stat",
            [
                target(
                    "histogram_quantile(0.5, sum(rate(aegis_approval_wait_seconds_bucket[1h])) by (le))"
                )
            ],
            8,
            0,
            4,
            4,
            unit="s",
        ),
        panel(
            4,
            "Resolution time p50 / p95",
            "stat",
            [
                target(
                    "histogram_quantile(0.5, sum(rate(aegis_incident_resolution_seconds_bucket[24h])) by (le))",
                    "p50",
                ),
                target(
                    "histogram_quantile(0.95, sum(rate(aegis_incident_resolution_seconds_bucket[24h])) by (le))",
                    "p95",
                ),
            ],
            12,
            0,
            6,
            4,
            unit="s",
        ),
        panel(
            5,
            "Policy denials (1h)",
            "stat",
            [target("sum(increase(aegis_policy_denials_total[1h])) by (code)", "{{code}}")],
            18,
            0,
            6,
            4,
        ),
        panel(
            6,
            "Tool calls by tool",
            "timeseries",
            [target("sum(rate(aegis_tool_calls_total[5m])) by (tool)", "{{tool}}")],
            0,
            4,
            12,
            8,
            unit="ops",
        ),
        panel(
            7,
            "Tool outcomes",
            "timeseries",
            [target("sum(rate(aegis_tool_calls_total[5m])) by (status)", "{{status}}")],
            12,
            4,
            12,
            8,
            unit="ops",
        ),
        panel(
            8,
            "LLM tokens by kind",
            "timeseries",
            [
                target(
                    "sum(rate(aegis_llm_tokens_total[5m])) by (kind, model)", "{{model}} {{kind}}"
                )
            ],
            0,
            12,
            12,
            8,
        ),
        panel(
            9,
            "LLM latency p95",
            "timeseries",
            [
                target(
                    "histogram_quantile(0.95, sum(rate(aegis_llm_latency_seconds_bucket[5m])) by (le, model))",
                    "{{model}}",
                )
            ],
            12,
            12,
            12,
            8,
            unit="s",
        ),
        panel(
            10,
            "Remediations",
            "timeseries",
            [
                target(
                    "sum(increase(aegis_remediation_total[15m])) by (tool, status)",
                    "{{tool}} {{status}}",
                )
            ],
            0,
            20,
            8,
            8,
        ),
        panel(
            11,
            "Verification outcomes",
            "timeseries",
            [target("sum(increase(aegis_verification_total[15m])) by (status)", "{{status}}")],
            8,
            20,
            8,
            8,
        ),
        panel(
            12,
            "Agent runs by termination",
            "timeseries",
            [
                target(
                    "sum(increase(aegis_agent_runs_total[15m])) by (phase, termination)",
                    "{{phase}} {{termination}}",
                )
            ],
            16,
            20,
            8,
            8,
        ),
        panel(
            13,
            "API latency p95 by route",
            "timeseries",
            [
                target(
                    "histogram_quantile(0.95, sum(rate(aegis_http_request_seconds_bucket[5m])) by (le, route))",
                    "{{route}}",
                )
            ],
            0,
            28,
            12,
            8,
            unit="s",
        ),
        panel(
            14,
            "Detection cycle duration p95",
            "timeseries",
            [
                target(
                    "histogram_quantile(0.95, sum(rate(aegis_detection_cycle_seconds_bucket[5m])) by (le))",
                    "p95",
                )
            ],
            12,
            28,
            12,
            8,
            unit="s",
        ),
    ],
    ["aegis"],
)

sim = dashboard(
    "aegis-simulated-infra",
    "Aegis — Simulated Infrastructure",
    [
        panel(
            1,
            "Service p95 latency",
            "timeseries",
            [target("sim_service_latency_p95_ms", "{{service}}")],
            0,
            0,
            12,
            8,
            unit="ms",
        ),
        panel(
            2,
            "Service error rate",
            "timeseries",
            [target("sim_service_error_rate", "{{service}}")],
            12,
            0,
            12,
            8,
            unit="percentunit",
        ),
        panel(
            3,
            "Redis connections vs max",
            "timeseries",
            [
                target('sim_infra_connections{component="redis"}', "connections"),
                target('sim_infra_max_connections{component="redis"}', "max"),
            ],
            0,
            8,
            8,
            8,
        ),
        panel(
            4,
            "Postgres connections vs max",
            "timeseries",
            [
                target('sim_infra_connections{component="postgres"}', "connections"),
                target('sim_infra_max_connections{component="postgres"}', "max"),
                target('sim_infra_idle_in_transaction{component="postgres"}', "idle in txn"),
            ],
            8,
            8,
            8,
            8,
        ),
        panel(
            5,
            "Pool waits",
            "timeseries",
            [
                target("sim_service_redis_pool_wait_ms", "{{service}} redis"),
                target("sim_service_db_pool_wait_ms", "{{service}} db"),
            ],
            16,
            8,
            8,
            8,
            unit="ms",
        ),
        panel(
            6,
            "CPU %",
            "timeseries",
            [target("sim_service_cpu_percent", "{{service}}")],
            0,
            16,
            8,
            8,
            unit="percent",
        ),
        panel(
            7,
            "Memory %",
            "timeseries",
            [target("sim_service_memory_percent", "{{service}}")],
            8,
            16,
            8,
            8,
            unit="percent",
        ),
        panel(
            8,
            "Availability (up)",
            "state-timeline",
            [target("sim_service_up", "{{service}}")],
            16,
            16,
            8,
            8,
        ),
        panel(9, "Active faults", "stat", [target("sim_active_faults")], 0, 24, 4, 4),
        panel(
            10,
            "Gateway request rate",
            "timeseries",
            [target('sim_service_request_rate{service="api-gateway"}', "rps")],
            4,
            24,
            20,
            6,
            unit="reqps",
        ),
    ],
    ["aegis", "simulator"],
)

for name, dash in (("aegis-overview.json", aegis), ("aegis-simulated-infra.json", sim)):
    (OUT / name).write_text(json.dumps(dash, indent=2))
    print("wrote", OUT / name)
