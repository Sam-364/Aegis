"""Evidence graph: relations between evidence, hypotheses, services and actions, plus the compact
digest the LLM reads. Handles (E1, E2…) are stable within an agent run and map back to UUIDs."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field

from aegis.domain.enums import GraphNodeKind, RelationKind
from aegis.domain.evidence import Evidence, EvidenceGraph, EvidenceRelation
from aegis.domain.hypothesis import Hypothesis
from aegis.domain.ids import IncidentId
from aegis.ports.repositories import EvidenceRepository


@dataclass
class EvidenceDigest:
    lines: list[str]
    handles: dict[str, Evidence] = field(default_factory=dict)

    def text(self) -> str:
        return "\n".join(self.lines) if self.lines else "(no evidence collected yet)"


def handles_for(evidence: Sequence[Evidence]) -> dict[str, Evidence]:
    ordered = sorted(evidence, key=lambda e: (e.created_at, str(e.id)))
    return {f"E{i + 1}": e for i, e in enumerate(ordered)}


def digest(
    evidence: Sequence[Evidence], *, limit: int = 40, max_chars: int = 320
) -> EvidenceDigest:
    handles = handles_for(evidence)
    lines: list[str] = []
    items = list(handles.items())
    # Keep the strongest and the most recent when over the limit.
    if len(items) > limit:
        keep = {k for k, _ in sorted(items, key=lambda kv: -kv[1].strength)[: limit // 2]}
        for handle, _ in reversed(items):  # newest first until the limit is filled
            if len(keep) >= limit:
                break
            keep.add(handle)
        items = [kv for kv in items if kv[0] in keep]
    for handle, e in items:
        svc = f" {e.service}" if e.service else ""
        summary = e.summary if len(e.summary) <= max_chars else e.summary[: max_chars - 1] + "…"
        lines.append(f"[{handle} {e.kind.value}{svc} s={e.strength:.2f} via {e.source}] {summary}")
    return EvidenceDigest(lines=lines, handles=handles)


class EvidenceService:
    def __init__(self, repo: EvidenceRepository) -> None:
        self.repo = repo

    async def graph(self, incident_id: IncidentId) -> EvidenceGraph:
        items = await self.repo.list_for_incident(incident_id)
        rels = await self.repo.relations_for_incident(incident_id)
        return EvidenceGraph(incident_id=incident_id, evidence=tuple(items), relations=tuple(rels))

    async def link_hypothesis(self, hypothesis: Hypothesis, *, created_by: str = "runtime") -> None:
        """Record supports/contradicts edges and the observed_on edges to services."""
        existing = {
            (r.from_id, r.to_id, r.kind)
            for r in await self.repo.relations_for_incident(hypothesis.incident_id)
        }
        for eid in hypothesis.supporting_evidence_ids:
            await self._add(
                existing,
                hypothesis.incident_id,
                str(eid),
                GraphNodeKind.EVIDENCE,
                str(hypothesis.id),
                GraphNodeKind.HYPOTHESIS,
                RelationKind.SUPPORTS,
                created_by,
            )
        for eid in hypothesis.contradicting_evidence_ids:
            await self._add(
                existing,
                hypothesis.incident_id,
                str(eid),
                GraphNodeKind.EVIDENCE,
                str(hypothesis.id),
                GraphNodeKind.HYPOTHESIS,
                RelationKind.CONTRADICTS,
                created_by,
            )
        if hypothesis.suspected_root_cause_service:
            await self._add(
                existing,
                hypothesis.incident_id,
                str(hypothesis.id),
                GraphNodeKind.HYPOTHESIS,
                hypothesis.suspected_root_cause_service,
                GraphNodeKind.SERVICE,
                RelationKind.CAUSED_BY,
                created_by,
            )

    async def link_evidence_to_services(self, evidence: Sequence[Evidence]) -> None:
        if not evidence:
            return
        incident_id = evidence[0].incident_id
        existing = {
            (r.from_id, r.to_id, r.kind)
            for r in await self.repo.relations_for_incident(incident_id)
        }
        for e in evidence:
            if e.service:
                await self._add(
                    existing,
                    incident_id,
                    str(e.id),
                    GraphNodeKind.EVIDENCE,
                    e.service,
                    GraphNodeKind.SERVICE,
                    RelationKind.OBSERVED_ON,
                    e.source,
                )

    async def link_action(
        self,
        incident_id: IncidentId,
        action_plan_id: uuid.UUID,
        hypothesis_id: uuid.UUID | None,
        *,
        created_by: str = "workflow",
    ) -> None:
        existing = {
            (r.from_id, r.to_id, r.kind)
            for r in await self.repo.relations_for_incident(incident_id)
        }
        if hypothesis_id is not None:
            await self._add(
                existing,
                incident_id,
                str(hypothesis_id),
                GraphNodeKind.HYPOTHESIS,
                str(action_plan_id),
                GraphNodeKind.ACTION,
                RelationKind.RESOLVED_BY,
                created_by,
            )

    async def _add(  # noqa: PLR0917 - edge description
        self,
        existing: set[tuple[str, str, RelationKind]],
        incident_id: IncidentId,
        from_id: str,
        from_kind: GraphNodeKind,
        to_id: str,
        to_kind: GraphNodeKind,
        kind: RelationKind,
        created_by: str,
    ) -> None:
        if (from_id, to_id, kind) in existing:
            return
        await self.repo.add_relation(
            EvidenceRelation(
                incident_id=incident_id,
                from_id=from_id,
                from_kind=from_kind,
                to_id=to_id,
                to_kind=to_kind,
                kind=kind,
                created_by=created_by,
            )
        )
        existing.add((from_id, to_id, kind))
