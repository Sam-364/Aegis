"""Load policy rules from YAML."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError as PydanticValidationError

from aegis.domain.enums import Environment, PolicyEffect, RiskLevel, Role, Severity, ToolCategory
from aegis.domain.errors import PolicyDefinitionError
from aegis.domain.policy import PolicyRule


def parse_rule(raw: dict[str, Any], *, source: str = "<memory>") -> PolicyRule:
    try:
        return PolicyRule(
            name=raw["name"],
            description=raw.get("description", ""),
            priority=int(raw.get("priority", 0)),
            effect=PolicyEffect(raw["effect"]),
            tools=frozenset(raw.get("tools", [])),
            categories=frozenset(ToolCategory(c) for c in raw.get("categories", [])),
            min_risk=RiskLevel(raw["min_risk"]) if "min_risk" in raw else None,
            max_risk=RiskLevel(raw["max_risk"]) if "max_risk" in raw else None,
            environments=frozenset(Environment(e) for e in raw.get("environments", [])),
            severities=frozenset(Severity(s) for s in raw.get("severities", [])),
            phases=frozenset(raw.get("phases", [])),
            flows=frozenset(raw.get("flows", [])),
            actor_kinds=frozenset(raw.get("actor_kinds", [])),
            required_role=Role(raw["required_role"]) if "required_role" in raw else None,
            in_agent_loop=raw.get("in_agent_loop"),
            reason=raw.get("reason", ""),
        )
    except (KeyError, ValueError, TypeError, PydanticValidationError) as exc:
        raise PolicyDefinitionError(f"invalid policy rule in {source}: {exc}") from exc


def load_policy_file(path: Path) -> list[PolicyRule]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise PolicyDefinitionError(f"cannot parse {path}: {exc}") from exc
    rules = [parse_rule(r, source=str(path)) for r in data.get("rules", [])]
    names = [r.name for r in rules]
    if len(names) != len(set(names)):
        raise PolicyDefinitionError(f"duplicate rule names in {path}")
    return rules


def load_policy_dir(directory: Path) -> list[PolicyRule]:
    if not directory.exists():
        raise PolicyDefinitionError(f"policies directory {directory} does not exist")
    rules: list[PolicyRule] = []
    for path in sorted(directory.glob("*.y*ml")):
        rules.extend(load_policy_file(path))
    if not rules:
        raise PolicyDefinitionError(f"no policy rules found in {directory}")
    return rules
