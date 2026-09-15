"""Explainable hypothesis scoring.

    score = w_e·evidence_strength + w_t·temporal_alignment + w_d·dependency_alignment
          + w_h·historical_similarity - w_c·contradiction_penalty + validation_bonus

Every component is in [0, 1] and is stored with a textual explanation so the UI can show *why*
a hypothesis has the confidence it has. The LLM never sets confidence.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field

from aegis.domain.enums import EvidenceKind, HypothesisCategory, HypothesisStatus
from aegis.domain.evidence import Evidence
from aegis.domain.hypothesis import Hypothesis, HypothesisScore, HypothesisTest, SuggestedTest
from aegis.domain.incident import Incident
from aegis.domain.telemetry import Topology

TestOutcome = Literal["confirmed", "refuted", "inconclusive"]


@dataclass(frozen=True)
class ScoreWeights:
    evidence: float = 0.45
    temporal: float = 0.15
    dependency: float = 0.25
    historical: float = 0.15
    contradiction: float = 0.5
    confirmed_bonus: float = 0.15
    refuted_penalty: float = 0.18


class SuggestedTestProposal(BaseModel):
    """Strict-schema friendly form of :class:`SuggestedTest` (arguments as a JSON string)."""

    tool_name: str
    arguments_json: str = Field(default="{}", description="JSON object with the tool arguments")
    expectation: str = Field(max_length=300)
    would_confirm: bool = True

    def to_domain(self) -> SuggestedTest:
        try:
            args = json.loads(self.arguments_json or "{}")
        except json.JSONDecodeError:
            args = {}
        return SuggestedTest(
            tool_name=self.tool_name,
            arguments=args if isinstance(args, dict) else {},
            expectation=self.expectation,
            would_confirm=self.would_confirm,
        )


class HypothesisProposal(BaseModel):
    """Shape the LLM must produce. Evidence is referenced by handle (E1, E2…), resolved by the
    runtime; unknown handles are dropped, and a proposal with no valid support is rejected."""

    statement: str = Field(min_length=10, max_length=400)
    category: HypothesisCategory
    suspected_root_cause_service: str
    mechanism: str = Field(max_length=600)
    supporting_evidence: list[str] = Field(default_factory=list)
    contradicting_evidence: list[str] = Field(default_factory=list)
    suggested_tests: list[SuggestedTestProposal] = Field(default_factory=list)


class ProposalRejected(Exception):  # noqa: N818 - domain vocabulary
    pass


@dataclass(frozen=True)
class Attribution:
    """'Resource R is saturated mostly because of client C' derived from diagnostic data."""

    resource: str
    client: str
    share: float
    strength: float
    evidence_id: uuid.UUID


def attributions(evidence: Sequence[Evidence]) -> list[Attribution]:
    out: list[Attribution] = []
    for e in evidence:
        clients = e.data.get("connections_by_client") or e.data.get("clients_by_service")
        resource = e.data.get("component")
        if not isinstance(clients, dict) or not isinstance(resource, str) or not clients:
            continue
        numeric = {str(k): float(v) for k, v in clients.items() if isinstance(v, int | float)}
        total = sum(numeric.values())
        if total <= 0:
            continue
        client, count = max(numeric.items(), key=lambda kv: kv[1])
        share = count / total
        leaked = (
            e.data.get("leaked_by_service") or e.data.get("idle_in_transaction_by_client") or {}
        )
        if isinstance(leaked, dict) and leaked:
            leak_client = str(max(leaked.items(), key=lambda kv: float(kv[1]))[0])
            if leak_client in numeric:
                client, share = leak_client, max(share, 0.8)
        saturation = float(e.data.get("saturation", 0.0) or 0.0)
        if share >= 0.4 and saturation >= 0.5:
            out.append(
                Attribution(
                    resource=resource,
                    client=client,
                    share=share,
                    strength=e.strength,
                    evidence_id=e.id,
                )
            )
    return out


@dataclass(frozen=True)
class Propagation:
    """'Service S fails because dependency D fails' derived from dependency diagnostics."""

    service: str
    dependency: str
    contribution: float
    own_error_rate: float
    strength: float
    evidence_id: uuid.UUID


def propagations(evidence: Sequence[Evidence]) -> list[Propagation]:
    out: list[Propagation] = []
    for e in evidence:
        contributions = e.data.get("contributions")
        service = e.data.get("service")
        if not isinstance(contributions, list) or not isinstance(service, str):
            continue
        own = float(e.data.get("own_error_rate") or 0.0)
        for c in contributions:
            if not isinstance(c, dict):
                continue
            contribution = float(c.get("error_contribution") or 0.0)
            if not bool(c.get("available", True)):
                contribution = max(contribution, 0.9)
            if contribution >= 0.2 and isinstance(c.get("target"), str):
                out.append(
                    Propagation(
                        service=service,
                        dependency=str(c["target"]),
                        contribution=contribution,
                        own_error_rate=own,
                        strength=e.strength,
                        evidence_id=e.id,
                    )
                )
    return out


def noisy_or(values: list[float]) -> float:
    acc = 1.0
    for v in values:
        acc *= 1 - max(0.0, min(1.0, v))
    return 1 - acc


class HypothesisEngine:
    def __init__(self, weights: ScoreWeights | None = None) -> None:
        self.w = weights or ScoreWeights()

    # --- proposals ------------------------------------------------------------------------------

    def accept(
        self,
        proposal: HypothesisProposal,
        *,
        incident: Incident,
        resolve: dict[str, Evidence],
        known_services: frozenset[str],
        agent_run_id: uuid.UUID | None,
        proposed_by: str = "llm",
    ) -> Hypothesis:
        if proposal.suspected_root_cause_service not in known_services:
            raise ProposalRejected(
                f"unknown root cause service '{proposal.suspected_root_cause_service}'"
            )
        support = [resolve[h].id for h in proposal.supporting_evidence if h in resolve]
        contra = [resolve[h].id for h in proposal.contradicting_evidence if h in resolve]
        if not support:
            raise ProposalRejected("a hypothesis must cite at least one existing evidence item")
        return Hypothesis(
            incident_id=incident.id,
            statement=proposal.statement,
            category=proposal.category,
            suspected_root_cause_service=proposal.suspected_root_cause_service,
            mechanism=proposal.mechanism,
            affected_services=list(incident.affected_services),
            supporting_evidence_ids=list(dict.fromkeys(support)),
            contradicting_evidence_ids=list(dict.fromkeys(contra)),
            suggested_tests=[t.to_domain() for t in proposal.suggested_tests[:4]],
            proposed_by=proposed_by,
            agent_run_id=agent_run_id,
        )

    # --- scoring --------------------------------------------------------------------------------

    def score(  # noqa: PLR0912, PLR0915 - each component is documented inline
        self,
        hypothesis: Hypothesis,
        *,
        evidence: dict[uuid.UUID, Evidence],
        incident: Incident,
        topology: Topology,
        now: datetime,
    ) -> HypothesisScore:
        support = [evidence[i] for i in hypothesis.supporting_evidence_ids if i in evidence]
        contra = [evidence[i] for i in hypothesis.contradicting_evidence_ids if i in evidence]
        root = hypothesis.suspected_root_cause_service
        explanation: list[str] = []

        # evidence strength: noisy-OR, but only evidence that actually implicates the root cause
        # service (or is service-agnostic) counts fully; evidence about other services counts half.
        weighted: list[float] = []
        for e in support:
            factor = 1.0 if (e.service in (None, root) or root in e.tags) else 0.5
            weighted.append(e.strength * factor)
        attrs = attributions(list(evidence.values()))
        auto_contra: list[float] = []
        for a in attrs:
            if a.client == root and a.evidence_id not in hypothesis.supporting_evidence_ids:
                weighted.append(a.strength * a.share)
                explanation.append(
                    f"{a.resource} saturation is attributable to {root} "
                    f"({a.share:.0%} of connections)"
                )
            if a.resource == root and a.client != root:
                auto_contra.append(a.strength * a.share)
                explanation.append(
                    f"{root} saturation is attributable to client {a.client} ({a.share:.0%}); "
                    "the resource is a symptom, not the origin"
                )
        for pr in propagations(list(evidence.values())):
            if pr.service == root and pr.own_error_rate < 0.02 and root is not None:
                auto_contra.append(pr.strength * pr.contribution)
                explanation.append(
                    f"{root}'s errors are inherited from {pr.dependency} "
                    f"({pr.contribution:.0%} contribution, own error rate "
                    f"{pr.own_error_rate:.1%}); "
                    "it is a symptom, not the origin"
                )
            if pr.dependency == root and pr.evidence_id not in hypothesis.supporting_evidence_ids:
                weighted.append(pr.strength * pr.contribution)
                explanation.append(
                    f"{pr.service} fails because of {root} ({pr.contribution:.0%} contribution)"
                )
        if weighted:
            evidence_strength = min(0.95, 0.6 * max(weighted) + 0.4 * noisy_or(weighted))
        else:
            evidence_strength = 0.0
        explanation.append(
            f"{len(support)} supporting evidence items (strength {evidence_strength:.2f})"
        )

        # temporal alignment: evidence observed inside the incident window; deployments must
        # precede detection to be causal.
        window_start = incident.detected_at - timedelta(minutes=10)
        aligned: list[float] = []
        for e in support:
            if e.kind is EvidenceKind.DEPLOYMENT:
                deployed = e.data.get("deployed_at")
                try:
                    dt = datetime.fromisoformat(str(deployed)) if deployed else e.observed_at
                except ValueError:
                    dt = e.observed_at
                delta = (incident.detected_at - dt).total_seconds()
                aligned.append(1.0 if 0 <= delta <= 3600 else 0.3)
            elif e.kind is EvidenceKind.MEMORY:
                aligned.append(0.7)
            else:
                aligned.append(
                    1.0 if window_start <= e.observed_at <= now + timedelta(seconds=5) else 0.5
                )
        temporal = sum(aligned) / len(aligned) if aligned else 0.0
        explanation.append(f"temporal alignment {temporal:.2f}")

        # dependency alignment: the root cause must be able to explain the symptomatic services.
        symptomatic = list(dict.fromkeys(s.service for s in incident.signals)) or list(
            incident.affected_services
        )
        kinds = {n.name: n.kind for n in topology.nodes}
        explained = 0
        for svc in symptomatic:
            if root is not None and self._explains(root, svc, topology, kinds):
                explained += 1
        dependency = explained / len(symptomatic) if symptomatic else 0.5
        if symptomatic and explained == 0:
            explanation.append(
                f"{root} cannot explain any symptomatic service ({', '.join(symptomatic[:4])})"
            )
        else:
            explanation.append(
                f"{root} explains {explained}/{len(symptomatic)} symptomatic services"
            )
        if hypothesis.category is HypothesisCategory.TRANSIENT:
            dependency = max(dependency, 0.6)

        # historical similarity from memory evidence pointing at the same root cause or category
        historical = 0.0
        for e in support:
            if e.kind is EvidenceKind.MEMORY:
                same_root = e.data.get("root_cause_service") == root
                same_cat = hypothesis.category.value in e.tags
                sim = float(e.data.get("similarity", 0.0))
                historical = max(historical, sim if same_root else sim * 0.5 if same_cat else 0.0)
        if historical:
            explanation.append(f"similar past incident (similarity {historical:.2f})")

        contradiction = (
            noisy_or([e.strength for e in contra] + auto_contra) if (contra or auto_contra) else 0.0
        )
        if contra:
            explanation.append(f"{len(contra)} contradicting items (penalty {contradiction:.2f})")

        confirmed = hypothesis.confirmed_tests()
        refuted = hypothesis.refuted_tests()
        validation = min(0.3, confirmed * self.w.confirmed_bonus) - min(
            0.6, refuted * self.w.refuted_penalty
        )
        if confirmed or refuted:
            explanation.append(f"tests: {confirmed} confirmed, {refuted} refuted")

        total = (
            self.w.evidence * evidence_strength
            + self.w.temporal * temporal
            + self.w.dependency * dependency
            + self.w.historical * historical
            - self.w.contradiction * contradiction
            + validation
        )
        total = max(0.0, min(1.0, total))
        return HypothesisScore(
            evidence_strength=round(evidence_strength, 4),
            temporal_alignment=round(temporal, 4),
            dependency_alignment=round(dependency, 4),
            historical_similarity=round(historical, 4),
            contradiction_penalty=round(contradiction, 4),
            validation_bonus=round(validation, 4),
            total=round(total, 4),
            explanation=explanation,
        )

    def rescore(self, hypothesis: Hypothesis, **kwargs: object) -> Hypothesis:
        score = self.score(hypothesis, **kwargs)  # type: ignore[arg-type]
        hypothesis.score = score
        hypothesis.confidence = score.total
        hypothesis.status = self._status_for(hypothesis)
        hypothesis.version += 1
        hypothesis.touch()
        return hypothesis

    @staticmethod
    def _explains(root: str, symptom: str, topology: Topology, kinds: dict[str, str]) -> bool:
        """A root cause explains a symptomatic component when it *is* that component, when the
        component depends on it (failures propagate to callers), or when the component is a shared
        resource (database/cache) and the root cause is one of its clients (clients saturate
        shared resources)."""
        if root == symptom:
            return True
        if not topology.has_node(symptom) or not topology.has_node(root):
            return False
        if root in topology.downstream_closure(symptom):
            return True
        shared = ("database", "cache")
        if kinds.get(symptom) in shared and symptom in topology.downstream_closure(root):
            return True
        # sibling effect: symptom and root share a resource the root can saturate
        # (order-service leaks redis connections -> auth-service waits on redis)
        root_res = {d for d in topology.dependencies_of(root) if kinds.get(d) in shared}
        sym_res = {d for d in topology.downstream_closure(symptom) if kinds.get(d) in shared}
        return bool(root_res & sym_res)

    @staticmethod
    def _status_for(h: Hypothesis) -> HypothesisStatus:
        if h.status is HypothesisStatus.ABANDONED:
            return h.status
        refuted, confirmed = h.refuted_tests(), h.confirmed_tests()
        if (refuted >= 2 and refuted > confirmed) or (refuted >= 1 and h.confidence < 0.35):
            return HypothesisStatus.REFUTED
        if h.confirmed_tests() >= 1 and h.confidence >= 0.65:
            return HypothesisStatus.CONFIRMED
        if h.confidence >= 0.55:
            return HypothesisStatus.SUPPORTED
        if h.tests:
            return HypothesisStatus.TESTING
        return HypothesisStatus.PROPOSED

    @staticmethod
    def rank(hypotheses: list[Hypothesis]) -> list[Hypothesis]:
        return sorted(hypotheses, key=lambda h: (-h.confidence, h.created_at))

    # --- tests ----------------------------------------------------------------------------------

    def judge_test(
        self,
        hypothesis: Hypothesis,
        produced: list[Evidence],
        llm_outcome: TestOutcome,
        *,
        tool_name: str,
        expectation: str,
        tool_execution_id: uuid.UUID | None,
        now: datetime,
        detail: str = "",
    ) -> HypothesisTest:
        """Combine the model's reading of a diagnostic with a deterministic attribution guard: a
        test can only *confirm* a hypothesis if the diagnostic evidence actually implicates the
        suspected root-cause service."""
        root = hypothesis.suspected_root_cause_service
        implicates = root is not None and any(
            e.service == root or root in e.tags or root in str(e.data.get("top_client", ""))
            for e in produced
        )
        strong = any(e.strength >= 0.6 for e in produced)
        outcome: TestOutcome = llm_outcome
        guard = ""
        if llm_outcome == "confirmed" and not (implicates and strong):
            outcome = "inconclusive"
            guard = " (downgraded: diagnostic evidence does not implicate the suspected service)"
        if llm_outcome == "refuted" and not produced:
            outcome = "inconclusive"
            guard = " (downgraded: no diagnostic evidence produced)"
        test = HypothesisTest(
            hypothesis_id=hypothesis.id,
            tool_execution_id=tool_execution_id,
            tool_name=tool_name,
            expectation=expectation,
            outcome=outcome,
            detail=(detail or "")[:400] + guard,
            at=now,
        )
        hypothesis.tests.append(test)
        return test


def deterministic_hypotheses(  # noqa: PLR0912 - keyword table
    incident: Incident, evidence: list[Evidence], topology: Topology
) -> list[HypothesisProposal]:
    """Fallback proposals when the LLM is unavailable: derive candidates from strong evidence."""
    proposals: list[HypothesisProposal] = []
    handles = {e.id: f"E{i + 1}" for i, e in enumerate(evidence)}
    by_service: dict[str, list[Evidence]] = {}
    for e in evidence:
        if e.service and e.strength >= 0.6:
            by_service.setdefault(e.service, []).append(e)
    node_kinds = {n.name: n.kind for n in topology.nodes}
    attributed = {a.resource: a for a in attributions(evidence)}
    ranked = sorted(by_service.items(), key=lambda kv: -max(e.strength for e in kv[1]))
    for service, items in ranked:
        if not topology.has_node(service):
            continue
        if service in attributed and attributed[service].client != service:
            continue  # a saturated shared resource with a dominant client is a symptom
        text = " ".join(e.summary for e in items)
        down = any(
            k in text
            for k in (
                "(down)",
                "UNAVAILABLE",
                "UNREACHABLE",
                "DOWN",
                "ECONNREFUSED",
            )
        ) or any("process_up" in e.tags for e in items)
        is_service = node_kinds.get(service) in ("service", "gateway")
        resource_words = ("saturation", "connections", "pool", "leaked", "idle-in-transaction")
        # A rollback is not a regression: it returns a service to a version it already ran, and
        # Aegis performs rollbacks itself, so treating one as a cause makes the runtime propose
        # undoing its own remediation — redeploying the version that broke.
        release = [
            e
            for e in items
            if e.kind is EvidenceKind.DEPLOYMENT
            and not e.data.get("is_rollback")
            and "rollback" not in e.tags
        ]
        if any(e.strength >= 0.8 for e in release):
            category = HypothesisCategory.DEPLOYMENT_REGRESSION
            statement = (
                f"A recent deployment of {service} introduced a regression causing the symptoms."
            )
        elif is_service and any(k in text for k in resource_words):
            category = HypothesisCategory.RESOURCE_EXHAUSTION
            statement = (
                f"{service} is exhausting a shared resource (connections), degrading callers."
            )
        elif down:
            category = HypothesisCategory.DEPENDENCY_FAILURE
            statement = f"{service} is unavailable and its dependents fail as a result."
        elif any("memory_ok" in e.tags for e in items) or any(
            k in text for k in ("GC pause", "heap", "memory pressure", "OOM")
        ):
            category = HypothesisCategory.RESOURCE_EXHAUSTION
            statement = f"{service} is under memory pressure (leak or undersized limit)."
        elif any("cpu_ok" in e.tags for e in items) or any(
            k in text for k in ("event loop lag", "CPU-bound", "cpu saturation")
        ):
            category = HypothesisCategory.CAPACITY
            statement = f"{service} is CPU-bound and needs more capacity."
        elif any(k in text for k in resource_words):
            category = HypothesisCategory.RESOURCE_EXHAUSTION
            statement = f"{service} is saturated, degrading its callers."
        else:
            category = HypothesisCategory.UNKNOWN
            statement = f"{service} is the most implicated component in the collected evidence."
        proposals.append(
            HypothesisProposal(
                statement=statement,
                category=category,
                suspected_root_cause_service=service,
                mechanism=(
                    "derived deterministically from evidence strength and service attribution"
                ),
                supporting_evidence=[handles[e.id] for e in items],
            )
        )
        if len(proposals) >= 3:
            break
    return proposals
