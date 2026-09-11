"""Shared Pydantic base classes for domain objects."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from aegis.domain.clock import utcnow
from aegis.domain.enums import ActorKind, Role


class DomainModel(BaseModel):
    """Mutable entity base. Unknown fields are rejected to catch contract drift early."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True, ser_json_bytes="base64")


class ValueObject(BaseModel):
    """Immutable value object base."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class Actor(ValueObject):
    """Who did something. Serialized into audit events and policy context."""

    kind: ActorKind
    id: str
    display_name: str | None = None
    roles: frozenset[Role] = Field(default_factory=frozenset)

    @classmethod
    def system(cls, component: str = "system") -> Actor:
        return cls(
            kind=ActorKind.SYSTEM,
            id=component,
            display_name=component,
            roles=frozenset({Role.ADMIN}),
        )

    @classmethod
    def detector(cls) -> Actor:
        return cls(
            kind=ActorKind.DETECTOR,
            id="detector",
            display_name="Detection engine",
            roles=frozenset({Role.OPERATOR}),
        )

    @classmethod
    def agent(cls, run_id: str) -> Actor:
        return cls(
            kind=ActorKind.AGENT,
            id=f"agent:{run_id}",
            display_name="Aegis agent",
            roles=frozenset({Role.OPERATOR}),
        )

    @classmethod
    def workflow(cls, workflow_id: str) -> Actor:
        return cls(
            kind=ActorKind.WORKFLOW,
            id=f"workflow:{workflow_id}",
            display_name="Incident workflow",
            roles=frozenset({Role.OPERATOR}),
        )

    @classmethod
    def human(cls, subject: str, roles: frozenset[Role], display_name: str | None = None) -> Actor:
        return cls(
            kind=ActorKind.HUMAN,
            id=f"user:{subject}",
            display_name=display_name or subject,
            roles=roles,
        )

    def has_role(self, role: Role) -> bool:
        return any(r.includes(role) for r in self.roles)


class Timestamped(DomainModel):
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    def touch(self, now: datetime | None = None) -> None:
        self.updated_at = now or utcnow()


def json_safe(value: Any) -> Any:
    """Convert a domain object (or plain value) into JSON-serializable data."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [json_safe(v) for v in value]
    return value
