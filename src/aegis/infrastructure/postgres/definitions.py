"""Persist the flow packs, policy rules and tool specs a process loaded.

An incident records *which* flow ran (name and version). This table records the text of that
version, so an incident stays auditable against the exact programme and policy that governed it
even after the YAML on disk has moved on. Writes are idempotent: the unique key is
(kind, name, version, checksum), so restarting a process adds nothing new.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Sequence
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from aegis.domain.flow import FlowPack
from aegis.domain.policy import PolicyRule
from aegis.domain.tool import ToolSpec
from aegis.infrastructure.postgres.models import DefinitionRow
from aegis.logging import get_logger

log = get_logger(__name__)


def _canonical(value: Any) -> Any:  # noqa: PLR0911 - one branch per JSON-able shape
    """A form of a definition that is identical in every process.

    ``model_dump`` renders a ``frozenset`` field (a phase's tools, a rule's severities) as a list
    in iteration order, and that order depends on string hash randomisation — so the same YAML
    hashed to a different checksum in each process and every restart wrote a new "version" of
    every tool. Sets are sorted here; genuinely ordered lists are left alone, so reordering
    policy rules still changes the checksum, as it must.
    """
    if isinstance(value, BaseModel):
        return {name: _canonical(getattr(value, name)) for name in type(value).model_fields}
    if isinstance(value, set | frozenset):
        return sorted((_canonical(v) for v in value), key=_sort_key)
    if isinstance(value, list | tuple):
        return [_canonical(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _canonical(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def _sort_key(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def _checksum(payload: Any) -> str:
    canonical = json.dumps(_canonical(payload), sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def _rows(
    flows: Sequence[FlowPack],
    policy_rules: Sequence[PolicyRule],
    tools: Sequence[ToolSpec],
    now: datetime,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for pack in flows:
        document = _canonical(pack)
        rows.append(
            {
                "id": uuid.uuid4(),
                "kind": "flow",
                "name": pack.name,
                "version": pack.version,
                "checksum": pack.checksum or _checksum(pack),
                "loaded_at": now,
                "document": document,
            }
        )
    if policy_rules:
        document = {"rules": [_canonical(r) for r in policy_rules]}
        rows.append(
            {
                "id": uuid.uuid4(),
                "kind": "policy",
                "name": "default",
                "version": str(len(policy_rules)),
                "checksum": _checksum(document),
                "loaded_at": now,
                "document": document,
            }
        )
    for spec in tools:
        document = _canonical(spec)
        rows.append(
            {
                "id": uuid.uuid4(),
                "kind": "tool",
                "name": spec.name,
                "version": spec.version,
                "checksum": _checksum(document),
                "loaded_at": now,
                "document": document,
            }
        )
    return rows


async def snapshot_definitions(
    engine: AsyncEngine,
    *,
    flows: Sequence[FlowPack],
    policy_rules: Sequence[PolicyRule],
    tools: Sequence[ToolSpec],
    now: datetime,
) -> int:
    """Record the definitions this process is running. Returns the number of new rows."""
    rows = _rows(flows, policy_rules, tools, now)
    if not rows:
        return 0
    statement = (
        pg_insert(DefinitionRow)
        .values(rows)
        .on_conflict_do_nothing(index_elements=["kind", "name", "version", "checksum"])
    )
    async with engine.begin() as conn:
        result = await conn.execute(statement)
    written = int(result.rowcount or 0)
    log.info("definitions.snapshot", candidates=len(rows), written=written)
    return written
