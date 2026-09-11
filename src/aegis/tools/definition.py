"""Tool definitions: typed arguments, a handler, and the spec the registry exposes."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pydantic import ValidationError as PydanticValidationError

from aegis.domain.enums import Environment, EvidenceKind, RiskLevel, Severity, ToolCategory
from aegis.domain.errors import InvalidToolArguments
from aegis.domain.tool import RetryPolicy, ToolSpec
from aegis.tools.context import ToolContext

_SUSPICIOUS = re.compile(r"(\.\./|[;&|`$]|\$\(|<script|://|\\x[0-9a-f]{2})", re.IGNORECASE)
MAX_STRING_ARG = 512
MAX_ARG_DEPTH = 8
MAX_ARG_NODES = 512


class ToolArgs(BaseModel):
    """Base class for tool argument models. Rejects unknown fields and suspicious strings."""

    model_config = ConfigDict(
        extra="forbid", str_strip_whitespace=True, str_max_length=MAX_STRING_ARG
    )


class EvidenceDraft(BaseModel):
    kind: EvidenceKind
    title: str
    summary: str
    data: dict[str, Any] = Field(default_factory=dict)
    strength: float = Field(default=0.5, ge=0.0, le=1.0)
    service: str | None = None
    tags: list[str] = Field(default_factory=list)


class ToolOutput(BaseModel):
    data: dict[str, Any] = Field(default_factory=dict)
    summary: str
    evidence: list[EvidenceDraft] = Field(default_factory=list)


type Handler[ArgsT: BaseModel] = Callable[[ToolContext, ArgsT], Awaitable[ToolOutput]]


def guard_strings(
    value: Any, path: str = "", *, depth: int = 0, budget: list[int] | None = None
) -> None:
    """Defense in depth: our tools never shell out or build URLs, but reject injection-shaped
    strings anyway so a compromised model cannot smuggle payloads into logs or downstream
    systems."""
    if budget is None:
        budget = [MAX_ARG_NODES]
    budget[0] -= 1
    if budget[0] < 0:
        raise InvalidToolArguments("arguments contain too many values")
    if depth > MAX_ARG_DEPTH:
        raise InvalidToolArguments(
            f"argument {path or 'value'} is nested deeper than {MAX_ARG_DEPTH} levels"
        )
    if isinstance(value, str):
        if len(value) > MAX_STRING_ARG:
            raise InvalidToolArguments(f"argument {path or 'value'} is too long")
        if _SUSPICIOUS.search(value):
            raise InvalidToolArguments(
                f"argument {path or 'value'} contains a forbidden pattern",
                details={"argument": path},
            )
    elif isinstance(value, dict):
        for k, v in value.items():
            guard_strings(v, f"{path}.{k}" if path else str(k), depth=depth + 1, budget=budget)
    elif isinstance(value, list | tuple):
        for i, v in enumerate(value):
            guard_strings(v, f"{path}[{i}]", depth=depth + 1, budget=budget)


class ToolDefinition[ArgsT: BaseModel]:
    def __init__(self, spec: ToolSpec, args_model: type[ArgsT], handler: Handler[ArgsT]) -> None:
        self.spec = spec.model_copy(update={"arguments_schema": args_model.model_json_schema()})
        self.args_model = args_model
        self.handler = handler

    @property
    def name(self) -> str:
        return self.spec.name

    def parse_args(
        self, raw: dict[str, Any], *, known_components: frozenset[str] = frozenset()
    ) -> ArgsT:
        guard_strings(raw)
        try:
            args = self.args_model.model_validate(raw)
        except PydanticValidationError as exc:
            raise InvalidToolArguments(
                f"invalid arguments for {self.name}: "
                + "; ".join(
                    f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
                ),
                details={
                    "tool": self.name,
                    "errors": [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()],
                },
            ) from exc
        if known_components:
            for field_name in ("service", "component", "source", "target"):
                value = getattr(args, field_name, None)
                if isinstance(value, str) and value not in known_components:
                    raise InvalidToolArguments(
                        f"unknown component '{value}' for argument {field_name}",
                        details={
                            "tool": self.name,
                            "argument": field_name,
                            "value": value,
                            "known": sorted(known_components),
                        },
                    )
        return args

    async def run(self, ctx: ToolContext, args: ArgsT) -> ToolOutput:
        return await self.handler(ctx, args)


def tool[ArgsT: BaseModel](
    name: str,
    *,
    description: str,
    category: ToolCategory,
    risk: RiskLevel,
    args: type[ArgsT],
    idempotent: bool = True,
    timeout_seconds: float = 10.0,
    retry: RetryPolicy | None = None,
    version: str = "1",
    capabilities: frozenset[str] = frozenset(),
    allowed_environments: frozenset[Environment] | None = None,
    min_severity: Severity | None = None,
    verification_metrics: tuple[str, ...] = (),
) -> Callable[[Handler[ArgsT]], ToolDefinition[ArgsT]]:
    def decorator(handler: Handler[ArgsT]) -> ToolDefinition[ArgsT]:
        spec = ToolSpec(
            name=name,
            version=version,
            description=description,
            category=category,
            risk=risk,
            idempotent=idempotent,
            timeout_seconds=timeout_seconds,
            retry=retry
            or (
                RetryPolicy(max_attempts=3)
                if not category.is_mutation
                else RetryPolicy(max_attempts=1)
            ),
            capabilities=capabilities,
            allowed_environments=allowed_environments or frozenset(Environment),
            min_severity=min_severity,
            verification_metrics=verification_metrics,
        )
        return ToolDefinition(spec, args, handler)

    return decorator
