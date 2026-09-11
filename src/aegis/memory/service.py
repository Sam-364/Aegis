"""Build, store and retrieve incident memories."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from aegis.agent.prompts import MEMORY_SYSTEM
from aegis.agent.schemas import MemorySummary
from aegis.domain.action import ActionPlan
from aegis.domain.clock import Clock, SystemClock
from aegis.domain.enums import ActionPlanStatus
from aegis.domain.errors import LLMError
from aegis.domain.evidence import Evidence
from aegis.domain.hypothesis import Hypothesis
from aegis.domain.incident import Incident
from aegis.domain.memory import IncidentMemory, MemoryMatch, SimilarIncidentQuery
from aegis.logging import get_logger
from aegis.ports.llm import EmbeddingProvider, LLMProvider
from aegis.ports.repositories import MemoryRepository

log = get_logger(__name__)


class IncidentMemoryService:
    def __init__(
        self,
        repo: MemoryRepository,
        *,
        embeddings: EmbeddingProvider | None = None,
        llm: LLMProvider | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.repo = repo
        self.embeddings = embeddings
        self.llm = llm
        self.clock = clock or SystemClock()

    # --- write ------------------------------------------------------------------------------------

    def build(
        self,
        incident: Incident,
        *,
        hypotheses: Sequence[Hypothesis],
        plans: Sequence[ActionPlan],
        evidence: Sequence[Evidence],
        outcome: str,
        summary: MemorySummary | None = None,
    ) -> IncidentMemory:
        leading = max(hypotheses, key=lambda h: h.confidence, default=None)
        executed = [
            p
            for p in plans
            if p.status
            in (
                ActionPlanStatus.EXECUTED,
                ActionPlanStatus.VERIFIED,
                ActionPlanStatus.VERIFICATION_FAILED,
                ActionPlanStatus.ROLLED_BACK,
            )
        ]
        symptoms = [s.description for s in incident.signals[:8]] or [incident.title]
        strong = sorted(evidence, key=lambda e: -e.strength)[:6]
        root_cause = (summary.root_cause if summary else None) or (
            leading.statement if leading else "undetermined"
        )
        if executed:
            resolution = "; ".join(
                f"{p.tool_name}({', '.join(f'{k}={v}' for k, v in p.arguments.items())}) → "
                f"{p.verification_result.status.value if p.verification_result else p.status.value}"
                for p in executed
            )
        elif outcome == "false_positive":
            resolution = "no action required; metrics recovered without intervention"
        else:
            resolution = "escalated to humans without automated remediation"
        if summary and summary.resolution:
            resolution = f"{summary.resolution} [{resolution}]"
        verification = next(
            (p.verification_result.summary for p in reversed(executed) if p.verification_result), ""
        )
        memory = IncidentMemory(
            incident_id=incident.id,
            incident_number=incident.number,
            title=(summary.title if summary else incident.title) or incident.title,
            severity=incident.severity,
            symptoms=(summary.symptoms if summary else None) or symptoms,
            affected_services=list(incident.affected_services),
            root_cause=root_cause,
            root_cause_service=leading.suspected_root_cause_service if leading else None,
            root_cause_category=leading.category.value if leading else "unknown",
            evidence_summary=[e.summary[:200] for e in strong],
            actions=[
                {
                    "tool": p.tool_name,
                    "arguments": p.arguments,
                    "status": p.status.value,
                    "risk": p.risk.value,
                }
                for p in plans
            ],
            resolution=resolution,
            verification_summary=verification,
            duration_seconds=incident.duration_seconds(self.clock.now()),
            outcome=outcome,
            lessons=summary.lessons if summary else [],
            resolved_at=incident.resolved_at,
        )
        memory.embedding_text = memory.to_embedding_text()
        return memory

    async def summarize_with_llm(self, memory: IncidentMemory) -> MemorySummary | None:
        if self.llm is None:
            return None
        prompt = "\n".join(
            [
                f"Incident: {memory.title} ({memory.severity.value})",
                "Symptoms: " + "; ".join(memory.symptoms),
                f"Affected services: {', '.join(memory.affected_services)}",
                f"Leading hypothesis: {memory.root_cause}",
                "Key evidence:",
                *[f"- {e}" for e in memory.evidence_summary],
                "Actions: "
                + "; ".join(
                    f"{a['tool']} {a['arguments']} ({a['status']})" for a in memory.actions
                ),
                f"Resolution: {memory.resolution}",
                f"Verification: {memory.verification_summary or 'n/a'}",
                f"Outcome: {memory.outcome}",
            ]
        )
        try:
            result = await self.llm.complete_structured(
                schema=MemorySummary, system=MEMORY_SYSTEM, user=prompt, tier="fast"
            )
        except LLMError as exc:
            log.warning("memory.summary_unavailable", error=str(exc))
            return None
        return result.value

    async def store(self, memory: IncidentMemory) -> IncidentMemory:
        if self.embeddings is not None:
            try:
                vectors = await self.embeddings.embed([memory.embedding_text])
                memory.embedding = vectors[0]
                memory.embedding_model = self.embeddings.model
            except LLMError as exc:
                log.warning("memory.embedding_unavailable", error=str(exc))
        stored = await self.repo.add(memory)
        log.info(
            "memory.stored",
            incident_id=str(memory.incident_id),
            embedded=memory.embedding is not None,
        )
        return stored

    # --- read -------------------------------------------------------------------------------------

    async def search(self, query: SimilarIncidentQuery) -> list[MemoryMatch]:
        text = "\n".join(
            [
                query.text,
                "Symptoms: " + "; ".join(query.symptoms),
                "Affected services: " + ", ".join(query.affected_services),
            ]
        )
        matches: list[MemoryMatch] = []
        if self.embeddings is not None:
            try:
                vector = (await self.embeddings.embed([text]))[0]
                matches = await self.repo.search(
                    vector, limit=query.limit * 2, exclude_incident_id=query.exclude_incident_id
                )
            except LLMError as exc:
                log.warning("memory.search_embedding_unavailable", error=str(exc))
        if not matches:
            terms = [t for t in text.replace(",", " ").replace(";", " ").split() if len(t) > 3]
            matches = await self.repo.search_lexical(
                terms,
                query.affected_services,
                limit=query.limit * 2,
                exclude_incident_id=query.exclude_incident_id,
            )
        # boost matches sharing affected services; keep the score in [0, 1]
        boosted: list[MemoryMatch] = []
        for m in matches:
            overlap = len(set(query.affected_services) & set(m.memory.affected_services))
            bonus = min(0.15, 0.05 * overlap)
            boosted.append(m.model_copy(update={"similarity": min(1.0, m.similarity + bonus)}))
        boosted.sort(key=lambda m: -m.similarity)
        return boosted[: query.limit]

    def as_search(self) -> Any:
        """Adapter for :class:`aegis.tools.context.MemorySearch`."""
        return self.search
