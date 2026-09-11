"""Compile YAML flow definitions into validated ``FlowPack`` objects."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError as PydanticValidationError

from aegis.domain.enums import RiskLevel, Severity, SignalKind
from aegis.domain.errors import FlowDefinitionError
from aegis.domain.flow import (
    ExecutionBudget,
    ExitCondition,
    FlowPack,
    FlowPhase,
    PhaseTransition,
)


def parse_flow(raw: dict[str, Any], *, source: str = "<memory>") -> FlowPack:
    try:
        phases = tuple(
            FlowPhase(
                name=p["name"],
                objective=p.get("objective", ""),
                allowed_tools=frozenset(p.get("tools", [])),
                max_iterations=int(p.get("max_iterations", 6)),
                timeout_seconds=int(p.get("timeout_seconds", 300)),
                risk_ceiling=RiskLevel(p.get("risk_ceiling", "none")),
                exit_conditions=tuple(
                    ExitCondition(
                        kind=c["kind"], value=c.get("value"), description=c.get("description", "")
                    )
                    for c in p.get("exit_conditions", [])
                ),
                transitions=tuple(
                    PhaseTransition(on=t["when"], to=t["to"]) for t in p.get("transitions", [])
                ),
                terminal=bool(p.get("terminal", False)),
                guidance=p.get("guidance", ""),
                plans_remediation=bool(p.get("plans_remediation", False)),
            )
            for p in raw.get("phases", [])
        )
        budget_raw = raw.get("budget", {})
        factors = {
            Severity(k): float(v) for k, v in raw.get("severity_budget_factor", {}).items()
        } or None
        pack = FlowPack(
            name=raw["name"],
            version=str(raw["version"]),
            description=raw.get("description", ""),
            applies_to=frozenset(SignalKind(k) for k in raw.get("applies_to", [])),
            priority=int(raw.get("priority", 0)),
            initial_phase=raw["initial_phase"],
            phases=phases,
            remediation_tools=frozenset(raw.get("remediation_tools", [])),
            remediation_risk_ceiling=RiskLevel(raw.get("remediation_risk_ceiling", "high")),
            budget=ExecutionBudget(**budget_raw) if budget_raw else ExecutionBudget(),
            **({"severity_budget_factor": factors} if factors else {}),
        )
    except (KeyError, ValueError, TypeError, PydanticValidationError) as exc:
        raise FlowDefinitionError(
            f"invalid flow definition in {source}: {exc}", details={"source": source}
        ) from exc
    canonical = yaml.safe_dump(raw, sort_keys=True).encode()
    return pack.model_copy(update={"checksum": hashlib.sha256(canonical).hexdigest()[:16]})


def load_flow_file(path: Path) -> list[FlowPack]:
    try:
        documents = list(yaml.safe_load_all(path.read_text(encoding="utf-8")))
    except yaml.YAMLError as exc:
        raise FlowDefinitionError(f"cannot parse {path}: {exc}") from exc
    return [parse_flow(doc, source=str(path)) for doc in documents if doc]


def load_flow_dir(directory: Path) -> list[FlowPack]:
    if not directory.exists():
        raise FlowDefinitionError(f"flows directory {directory} does not exist")
    packs: list[FlowPack] = []
    for path in sorted(directory.glob("*.y*ml")):
        packs.extend(load_flow_file(path))
    if not packs:
        raise FlowDefinitionError(f"no flow packs found in {directory}")
    return packs
