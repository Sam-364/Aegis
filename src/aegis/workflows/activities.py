"""Activities: every side effect of the incident workflow. Idempotent, retryable, observable."""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from datetime import timedelta
from typing import Any

from temporalio import activity

from aegis.agent.runtime import AgentHooks
from aegis.application.container import RuntimeContainer
from aegis.application.events import Emitter
from aegis.domain.action import ActionExecution, VerificationResult, VerificationSpec
from aegis.domain.base import Actor
from aegis.domain.enums import (
    ActionPlanStatus,
    ApprovalStatus,
    ExecutionStatus,
    HypothesisStatus,
    IncidentStatus,
    NotificationKind,
    PolicyEffect,
    VerificationStatus,
)
from aegis.domain.errors import (
    ConcurrencyError,
    ConflictError,
    InvalidTransitionError,
    NotFoundError,
)
from aegis.domain.events import EventType
from aegis.domain.flow import BudgetUsage
from aegis.domain.policy import PolicyContext
from aegis.domain.statemachine import transition
from aegis.domain.tool import ToolCallRequest
from aegis.logging import bind_context, clear_context, get_logger
from aegis.memory.service import IncidentMemoryService
from aegis.remediation.planning import build_verification_spec
from aegis.telemetry.metrics import (
    INCIDENT_RESOLUTION_SECONDS,
    REMEDIATION_TOTAL,
    ROLLBACK_TOTAL,
    VERIFICATION_TOTAL,
)
from aegis.tools.context import ToolContext
from aegis.tools.executor import ToolExecutor
from aegis.verification.engine import describe_change
from aegis.workflows.contracts import (
    ExecutionOutcome,
    FinalizeInput,
    PhaseResult,
    PolicyOutcome,
    RunPhaseInput,
    TriageResult,
    VerificationOutcome,
)

log = get_logger(__name__)

STATUS_FROM_STRING = {s.value: s for s in IncidentStatus}


def _wf_id() -> str:
    return activity.info().workflow_id or "unknown-workflow"


class IncidentActivities:
    def __init__(self, container: RuntimeContainer) -> None:
        self.c = container
        self.emitter = Emitter(container.publisher)

    # ------------------------------------------------------------------ triage ------------------

    @activity.defn(name="triage_incident")
    async def triage_incident(self, incident_id: uuid.UUID) -> TriageResult:
        bind_context(incident_id=str(incident_id), workflow_id=_wf_id())
        actor = Actor.workflow(_wf_id())
        async with self.c.uow_factory() as uow:
            incident = await uow.incidents.get(incident_id)
            if not incident.is_active:
                return TriageResult(
                    flow_name=incident.flow_name or "",
                    flow_version=incident.flow_version or "",
                    initial_phase="",
                    severity=incident.severity.value,
                    active=False,
                )
            flow = (
                self.c.flows.get(incident.flow_name, incident.flow_version)
                if incident.flow_name
                else self.c.flows.select(incident.signals)
            )
            incident.flow_name, incident.flow_version = flow.name, flow.version
            if incident.status is IncidentStatus.DETECTED:
                transition(incident, IncidentStatus.TRIAGING, actor, now=self.c.clock.now())
                await self.emitter.timeline(
                    uow,
                    incident_id=incident.id,
                    type=EventType.INCIDENT_STATUS_CHANGED,
                    actor=actor,
                    title="Triage started",
                    payload={"from": "detected", "to": "triaging"},
                )
            if incident.workflow_id is None:
                incident.workflow_id = _wf_id()
            await uow.incidents.save(incident)
            await self.emitter.timeline(
                uow,
                incident_id=incident.id,
                type=EventType.PHASE_ENTERED,
                actor=actor,
                title=f"Entering phase '{flow.initial_phase}' of {flow.ref}",
                payload={
                    "phase": flow.initial_phase,
                    "flow": flow.ref,
                    "budget": flow.budget_for(incident.severity).model_dump(),
                },
            )
            await uow.commit()
            return TriageResult(
                flow_name=flow.name,
                flow_version=flow.version,
                initial_phase=flow.initial_phase,
                severity=incident.severity.value,
                max_remediation_attempts=flow.budget.max_remediation_attempts,
            )

    @activity.defn(name="set_incident_status")
    async def set_incident_status(self, incident_id: uuid.UUID, status: str, reason: str) -> None:
        target = STATUS_FROM_STRING[status]
        actor = Actor.workflow(_wf_id())
        async with self.c.uow_factory() as uow:
            incident = await uow.incidents.get(incident_id)
            if incident.status is target:
                return
            try:
                previous = transition(incident, target, actor, now=self.c.clock.now())
            except InvalidTransitionError as exc:
                log.warning(
                    "workflow.transition_skipped",
                    incident_id=str(incident_id),
                    from_status=incident.status.value,
                    to=status,
                    error=exc.message,
                )
                return
            await uow.incidents.save(incident)
            await self.emitter.timeline(
                uow,
                incident_id=incident.id,
                type=EventType.INCIDENT_STATUS_CHANGED,
                actor=actor,
                title=f"{previous.value} → {target.value}" + (f": {reason}" if reason else ""),
                payload={"from": previous.value, "to": target.value, "reason": reason},
            )
            await uow.commit()

    # ------------------------------------------------------------------ agent -------------------

    @activity.defn(name="run_agent_phase")
    async def run_agent_phase(self, input: RunPhaseInput) -> PhaseResult:
        bind_context(
            incident_id=str(input.incident_id),
            workflow_id=input.workflow_id,
            agent_run_id=str(input.agent_run_id),
        )

        async def heartbeat(node: str) -> None:
            activity.heartbeat(node)

        self.c.set_agent_hooks(AgentHooks(on_step=heartbeat))
        try:
            outcome = await self.c.agent.run_phase(
                incident_id=input.incident_id,
                flow_name=input.flow_name,
                flow_version=input.flow_version,
                phase=input.phase,
                agent_run_id=input.agent_run_id,
                usage=input.usage,
                workflow_id=input.workflow_id,
                attempt=activity.info().attempt,
                initial_feedback=input.feedback,
                refresh=input.refresh,
            )
        finally:
            clear_context()
        return PhaseResult(
            agent_run_id=outcome.agent_run_id,
            decision=outcome.decision,
            trigger=outcome.trigger,
            next_phase=outcome.next_phase,
            termination=outcome.termination.value if outcome.termination else None,
            action_plan_id=outcome.action_plan_id,
            usage=outcome.usage,
            summary=outcome.summary,
            refresh_pending=outcome.refresh_pending,
            iterations=outcome.iterations,
        )

    # ------------------------------------------------------------------ policy / approval -------

    @activity.defn(name="evaluate_remediation_policy")
    async def evaluate_remediation_policy(self, plan_id: uuid.UUID) -> PolicyOutcome:
        actor = Actor.workflow(_wf_id())
        now = self.c.clock.now()
        async with self.c.uow_factory() as uow:
            plan = await uow.action_plans.get(plan_id)
            incident = await uow.incidents.get(plan.incident_id)
            if plan.approval_id is not None:  # idempotent replay
                approval = await uow.approvals.get(plan.approval_id)
                return PolicyOutcome(
                    effect="require_approval",
                    approval_id=approval.id,
                    reason="approval already requested",
                )
            if plan.status in (
                ActionPlanStatus.APPROVED,
                ActionPlanStatus.EXECUTING,
                ActionPlanStatus.EXECUTED,
            ):
                return PolicyOutcome(effect="allow", reason="already authorized")
            spec = self.c.registry.spec(plan.tool_name)
            ctx = PolicyContext(
                tool_name=plan.tool_name,
                tool_category=spec.category,
                tool_risk=plan.risk,
                environment=incident.environment,
                severity=incident.severity,
                flow_name=incident.flow_name,
                phase="remediate",
                actor=actor,
                incident_id=incident.id,
                in_agent_loop=False,
                arguments=plan.arguments,
                remediation_attempt=incident.remediation_attempts,
                tenant_id=incident.tenant_id,
            )
            decision = self.c.policy.evaluate(ctx)
            plan.policy_decision = {
                "effect": decision.effect.value,
                "matched_rule": decision.matched_rule,
                "reason": decision.reason,
                "invariant": decision.invariant,
                "evaluated_rules": list(decision.evaluated_rules),
            }
            await self.emitter.timeline(
                uow,
                incident_id=incident.id,
                type=EventType.POLICY_DECIDED,
                actor=actor,
                title=f"Policy: {decision.effect.value.replace('_', ' ')} for {plan.tool_name} "
                f"({decision.matched_rule or decision.invariant or 'default'})",
                payload={"action_plan_id": str(plan.id), **plan.policy_decision},
            )
            await self.emitter.audit(
                uow,
                event_type="policy.decided",
                actor=actor,
                incident_id=incident.id,
                action=plan.tool_name,
                decision=decision.effect.value,
                reason=decision.reason,
                tool_name=plan.tool_name,
                data={"action_plan_id": str(plan.id), "rule": decision.matched_rule},
            )
            outcome: PolicyOutcome
            if decision.effect is PolicyEffect.DENY:
                plan.status = ActionPlanStatus.POLICY_DENIED
                outcome = PolicyOutcome(
                    effect="deny", reason=decision.reason, matched_rule=decision.matched_rule
                )
            elif decision.effect is PolicyEffect.REQUIRE_APPROVAL:
                hypothesis = None
                if plan.hypothesis_id is not None:
                    try:
                        hypothesis = await uow.hypotheses.get(plan.hypothesis_id)
                    except NotFoundError:
                        hypothesis = None
                from aegis.domain.approval import ApprovalRequest  # noqa: PLC0415

                approval = ApprovalRequest(
                    incident_id=incident.id,
                    action_plan_id=plan.id,
                    title=(
                        f"{plan.tool_name} on "
                        f"{plan.arguments.get('service') or plan.arguments.get('component')}"
                    ),
                    summary=plan.reason,
                    risk=plan.risk,
                    expected_impact=plan.expected_effect,
                    rollback_summary=(
                        plan.rollback.reason
                        if not plan.rollback.available
                        else f"{plan.rollback.tool_name} {plan.rollback.arguments}"
                    ),
                    hypothesis_id=plan.hypothesis_id,
                    hypothesis_statement=hypothesis.statement if hypothesis else "",
                    hypothesis_confidence=hypothesis.confidence if hypothesis else 0.0,
                    evidence_ids=list(hypothesis.supporting_evidence_ids) if hypothesis else [],
                    requested_by=actor.id,
                    requested_at=now,
                    expires_at=now + timedelta(seconds=self.c.settings.approval_timeout_seconds),
                    context={
                        "tool_name": plan.tool_name,
                        "arguments": plan.arguments,
                        "rollback": (
                            {
                                "tool_name": plan.rollback.tool_name,
                                "arguments": plan.rollback.arguments,
                            }
                            if plan.rollback.available
                            else None
                        ),
                        "policy": plan.policy_decision,
                        "verification": plan.verification.model_dump(mode="json"),
                    },
                )
                await uow.approvals.add(approval)
                plan.status = ActionPlanStatus.AWAITING_APPROVAL
                plan.approval_id = approval.id
                await self.emitter.timeline(
                    uow,
                    incident_id=incident.id,
                    type=EventType.APPROVAL_REQUESTED,
                    actor=actor,
                    title=f"Approval required: {approval.title} (risk {plan.risk.value})",
                    payload={
                        "approval_id": str(approval.id),
                        "action_plan_id": str(plan.id),
                        "expires_at": approval.expires_at.isoformat(),
                        "risk": plan.risk.value,
                        "hypothesis": approval.hypothesis_statement,
                        "confidence": approval.hypothesis_confidence,
                    },
                )
                await self.emitter.notify(
                    uow,
                    kind=NotificationKind.APPROVAL_REQUESTED,
                    incident_id=incident.id,
                    title=f"{incident.display_id}: approve {approval.title}?",
                    body=plan.reason,
                    data={"approval_id": str(approval.id), "risk": plan.risk.value},
                )
                outcome = PolicyOutcome(
                    effect="require_approval",
                    approval_id=approval.id,
                    reason=decision.reason,
                    matched_rule=decision.matched_rule,
                )
            else:
                plan.status = ActionPlanStatus.APPROVED
                outcome = PolicyOutcome(
                    effect="allow", reason=decision.reason, matched_rule=decision.matched_rule
                )
            plan.touch(now)
            await uow.action_plans.save(plan)
            await uow.commit()
            return outcome

    @activity.defn(name="expire_approval")
    async def expire_approval(self, approval_id: uuid.UUID, reason: str) -> None:
        actor = Actor.workflow(_wf_id())
        async with self.c.uow_factory() as uow:
            approval = await uow.approvals.get(approval_id)
            if approval.status is not ApprovalStatus.PENDING:
                return
            approval.status = (
                ApprovalStatus.EXPIRED if "timed out" in reason else ApprovalStatus.CANCELLED
            )
            approval.decided_at = self.c.clock.now()
            approval.decision_reason = reason
            await uow.approvals.save(approval)
            plan = await uow.action_plans.get(approval.action_plan_id)
            plan.status = ActionPlanStatus.REJECTED
            await uow.action_plans.save(plan)
            await self.emitter.timeline(
                uow,
                incident_id=approval.incident_id,
                type=EventType.APPROVAL_EXPIRED,
                actor=actor,
                title=f"Approval {approval.status.value}: {reason}",
                payload={"approval_id": str(approval.id), "reason": reason},
            )
            await uow.commit()

    @activity.defn(name="mark_plan")
    async def mark_plan(self, plan_id: uuid.UUID, status: str, reason: str) -> None:
        async with self.c.uow_factory() as uow:
            plan = await uow.action_plans.get(plan_id)
            plan.status = ActionPlanStatus(status)
            plan.touch(self.c.clock.now())
            await uow.action_plans.save(plan)
            await uow.commit()

    # ------------------------------------------------------------------ execution ---------------

    @activity.defn(name="capture_metrics")
    async def capture_metrics(self, plan_id: uuid.UUID) -> dict[str, float]:
        async with self.c.uow_factory() as uow:
            plan = await uow.action_plans.get(plan_id)
        return await self.c.verification().snapshot(plan.verification)

    @activity.defn(name="execute_remediation")
    async def execute_remediation(
        self, plan_id: uuid.UUID, approval_id: uuid.UUID | None, is_rollback: bool
    ) -> ExecutionOutcome:
        wf_id = _wf_id()
        actor = Actor.workflow(wf_id)
        bind_context(workflow_id=wf_id)
        async with self.c.uow_factory() as uow:
            plan = await uow.action_plans.get(plan_id)
            incident = await uow.incidents.get(plan.incident_id)
            approval = await uow.approvals.get(approval_id) if approval_id else None
            flow = self.c.flows.get(
                incident.flow_name or "incident-investigation", incident.flow_version
            )
            phase = (
                flow.phase("remediate")
                if flow.has_phase("remediate")
                else flow.phase(flow.initial_phase)
            )
            if is_rollback:
                if not plan.rollback.available or plan.rollback.tool_name is None:
                    return ExecutionOutcome(status="failed", error="no rollback available")
                tool_name, arguments = plan.rollback.tool_name, plan.rollback.arguments
            else:
                tool_name, arguments = plan.tool_name, plan.arguments
            topology = await self.c.telemetry.topology()
            ctx = ToolContext(
                incident=incident,
                flow=flow,
                phase=phase,
                actor=actor,
                environment=self.c.settings.environment,
                telemetry=self.c.telemetry,
                infrastructure=self.c.infrastructure,
                clock=self.c.clock,
                action_plan_id=plan.id,
                approval_id=approval_id,
                known_components=frozenset(n.name for n in topology.nodes),
            )
            request = ToolCallRequest(
                incident_id=incident.id,
                tool_name=tool_name,
                arguments=arguments,
                rationale=plan.reason,
                requested_by=actor,
                action_plan_id=plan.id,
                phase=phase.name,
                requested_at=self.c.clock.now(),
            )
            executor = ToolExecutor(
                self.c.registry,
                self.c.authorizer,
                ledger=uow.tool_executions,
                evidence=uow.evidence,
                audit=uow.audit,
                clock=self.c.clock,
                claim_factory=self.c.uow_factory,
            )
            if not is_rollback:
                plan.status = ActionPlanStatus.EXECUTING
                await uow.action_plans.save(plan)
                await self.emitter.timeline(
                    uow,
                    incident_id=incident.id,
                    type=EventType.REMEDIATION_STARTED,
                    actor=actor,
                    title=f"Executing {tool_name} {arguments}",
                    payload={
                        "action_plan_id": str(plan.id),
                        "tool": tool_name,
                        "arguments": arguments,
                    },
                )
            else:
                await self.emitter.timeline(
                    uow,
                    incident_id=incident.id,
                    type=EventType.ROLLBACK_STARTED,
                    actor=actor,
                    title=f"Rolling back with {tool_name} {arguments}",
                    payload={
                        "action_plan_id": str(plan.id),
                        "tool": tool_name,
                        "arguments": arguments,
                    },
                )
            record = await executor.execute(
                request,
                ctx=ctx,
                budget=flow.budget_for(incident.severity),
                usage=BudgetUsage(),
                in_agent_loop=False,
                approval=approval,
            )
            execution = ActionExecution(
                action_plan_id=plan.id,
                incident_id=incident.id,
                tool_execution_id=record.id,
                idempotency_key=(
                    f"{record.idempotency_key}:{'rollback' if is_rollback else 'action'}"
                ),
                attempt=activity.info().attempt,
                status=record.status,
                is_rollback=is_rollback,
                started_at=record.started_at,
                finished_at=record.finished_at,
                result=record.result,
                error=record.error,
            )
            # A retried activity replays a completed attempt; the unique idempotency key
            # rejects the duplicate row and that is the desired outcome.
            with contextlib.suppress(ConflictError):
                await uow.action_plans.add_execution(execution)
            ok = record.status in (ExecutionStatus.SUCCEEDED, ExecutionStatus.SKIPPED_DUPLICATE)
            if is_rollback:
                plan.status = ActionPlanStatus.ROLLED_BACK if ok else plan.status
                event = EventType.ROLLBACK_COMPLETED if ok else EventType.ROLLBACK_FAILED
            else:
                plan.status = ActionPlanStatus.EXECUTED if ok else ActionPlanStatus.EXECUTION_FAILED
                event = EventType.REMEDIATION_COMPLETED if ok else EventType.REMEDIATION_FAILED
                if record.status is ExecutionStatus.SUCCEEDED:
                    # Re-read: the detector may have attached a signal to this incident while the
                    # tool was running. A concurrency conflict here must not fail the activity,
                    # because the retry would re-run the infrastructure change.
                    with contextlib.suppress(ConcurrencyError, NotFoundError):
                        fresh = await uow.incidents.get(plan.incident_id)
                        fresh.remediation_attempts += 1
                        await uow.incidents.save(fresh)
                if record.status is ExecutionStatus.SKIPPED_DUPLICATE:
                    event = EventType.REMEDIATION_SKIPPED_DUPLICATE
            plan.touch(self.c.clock.now())
            await uow.action_plans.save(plan)
            if is_rollback:
                ROLLBACK_TOTAL.labels(record.status.value).inc()
            else:
                REMEDIATION_TOTAL.labels(tool_name, record.status.value).inc()
            await self.emitter.timeline(
                uow,
                incident_id=incident.id,
                type=event,
                actor=actor,
                title=(
                    f"{tool_name}: {record.result_summary or record.error or record.status.value}"
                ),
                payload={
                    "action_plan_id": str(plan.id),
                    "tool_execution_id": str(record.id),
                    "status": record.status.value,
                    "result": record.result,
                    "error": record.error,
                    "denial_code": record.authorization.denial_code,
                },
            )
            await uow.commit()
            clear_context()
            return ExecutionOutcome(
                status=record.status.value,
                tool_execution_id=record.id,
                result=record.result,
                error=record.error,
                denial_code=record.authorization.denial_code,
            )

    # ------------------------------------------------------------------ verification ------------

    async def _verify(
        self, spec: VerificationSpec, before: dict[str, float]
    ) -> tuple[VerificationOutcome, VerificationResult]:
        async def sleeper(seconds: float) -> None:
            remaining = seconds
            while remaining > 0:
                chunk = min(remaining, 5.0)
                if self.c.sleeper is not None:
                    await self.c.sleeper(chunk)
                else:
                    await asyncio.sleep(chunk)
                activity.heartbeat("verifying")
                remaining -= chunk

        async def progress(info: dict[str, Any]) -> None:
            activity.heartbeat(f"poll {info['poll']}")

        engine = self.c.verification(sleep=sleeper)
        result = await engine.verify(spec, before=before, on_progress=progress)
        outcome = VerificationOutcome(
            status=result.status.value,
            summary=result.summary,
            before=result.before,
            after=result.after,
        )
        return outcome, result

    @activity.defn(name="verify_remediation")
    async def verify_remediation(
        self, plan_id: uuid.UUID, before: dict[str, float]
    ) -> VerificationOutcome:
        actor = Actor.workflow(_wf_id())
        async with self.c.uow_factory() as uow:
            plan = await uow.action_plans.get(plan_id)
            incident = await uow.incidents.get(plan.incident_id)
            await self.emitter.timeline(
                uow,
                incident_id=incident.id,
                type=EventType.VERIFICATION_STARTED,
                actor=actor,
                title=f"Verifying {len(plan.verification.conditions)} condition(s) over "
                f"{plan.verification.stabilization_seconds}s stabilization",
                payload={
                    "action_plan_id": str(plan.id),
                    "conditions": [c.model_dump(mode="json") for c in plan.verification.conditions],
                },
            )
            await uow.commit()
        outcome, result = await self._verify(plan.verification, before)
        async with self.c.uow_factory() as uow:
            plan = await uow.action_plans.get(plan_id)
            incident = await uow.incidents.get(plan.incident_id)
            plan.verification_result = result
            passed = result.status is VerificationStatus.PASSED
            VERIFICATION_TOTAL.labels(result.status.value).inc()
            plan.status = (
                ActionPlanStatus.VERIFIED if passed else ActionPlanStatus.VERIFICATION_FAILED
            )
            plan.touch(self.c.clock.now())
            await uow.action_plans.save(plan)
            if passed and plan.hypothesis_id is not None:
                try:
                    hypothesis = await uow.hypotheses.get(plan.hypothesis_id)
                    hypothesis.status = HypothesisStatus.CONFIRMED
                    hypothesis.confidence = max(hypothesis.confidence, 0.9)
                    await uow.hypotheses.save(hypothesis)
                    incident.root_cause_summary = hypothesis.statement
                    await uow.incidents.save(incident)
                except NotFoundError:
                    pass
            await self.emitter.timeline(
                uow,
                incident_id=incident.id,
                type=EventType.VERIFICATION_PASSED if passed else EventType.VERIFICATION_FAILED,
                actor=actor,
                title=("Verification passed: " if passed else "Verification failed: ")
                + result.summary,
                payload={
                    "action_plan_id": str(plan.id),
                    "status": result.status.value,
                    "summary": result.summary,
                    "before": result.before,
                    "after": result.after,
                    "changes": describe_change(result.before, result.after),
                    "conditions": list(result.condition_results),
                },
            )
            await uow.commit()
        outcome.rollback_available = plan.rollback.available
        return outcome

    @activity.defn(name="verify_recovery")
    async def verify_recovery(self, incident_id: uuid.UUID) -> VerificationOutcome:
        actor = Actor.workflow(_wf_id())
        async with self.c.uow_factory() as uow:
            incident = await uow.incidents.get(incident_id)
        baselines = {
            (s.service, s.metric): await self.c.telemetry.baseline(s.service, s.metric)
            for s in incident.signals
        }
        spec = build_verification_spec(
            incident,
            baselines=baselines,
            target_service=None,
            tool_spec=None,
            stabilization_seconds=30,
            timeout_seconds=150,
        )
        before = await self.c.verification().snapshot(spec)
        outcome, result = await self._verify(spec, before)
        async with self.c.uow_factory() as uow:
            passed = result.status is VerificationStatus.PASSED
            await self.emitter.timeline(
                uow,
                incident_id=incident_id,
                type=EventType.VERIFICATION_PASSED if passed else EventType.VERIFICATION_FAILED,
                actor=actor,
                title=(
                    "Recovery verified without action: " if passed else "Recovery not confirmed: "
                )
                + result.summary,
                payload={
                    "status": result.status.value,
                    "summary": result.summary,
                    "before": result.before,
                    "after": result.after,
                    "conditions": list(result.condition_results),
                },
            )
            await uow.commit()
        return outcome

    # ------------------------------------------------------------------ finalize ----------------

    @activity.defn(name="finalize_incident")
    async def finalize_incident(self, input: FinalizeInput) -> None:
        actor = Actor.workflow(_wf_id())
        now = self.c.clock.now()
        target = {
            "resolved": IncidentStatus.RESOLVED,
            "escalated": IncidentStatus.ESCALATED,
            "failed": IncidentStatus.FAILED,
            "closed": IncidentStatus.CLOSED,
        }[input.outcome]
        async with self.c.uow_factory() as uow:
            incident = await uow.incidents.get(input.incident_id)
            if (incident.status is not target and incident.status.is_active) or (
                target is IncidentStatus.CLOSED and incident.status is not IncidentStatus.CLOSED
            ):
                try:
                    previous = transition(incident, target, actor, now=now)
                except InvalidTransitionError:
                    # Route through verification rather than leaving the status contradicting the
                    # outcome the workflow is about to report.
                    previous = incident.status
                    hops = (
                        [IncidentStatus.VERIFYING]
                        if target is IncidentStatus.RESOLVED
                        else [IncidentStatus.ESCALATED]
                    )
                    for hop in hops:
                        with contextlib.suppress(InvalidTransitionError):
                            transition(incident, hop, actor, now=now)
                    try:
                        transition(incident, target, actor, now=now)
                    except InvalidTransitionError as exc:
                        log.error(
                            "finalize.transition_failed",
                            incident_id=str(incident.id),
                            from_status=incident.status.value,
                            to=target.value,
                            error=exc.message,
                        )
                        # Record what actually happened, not the outcome we hoped to report.
                        await self.emitter.audit(
                            uow,
                            event_type="incident.finalize_failed",
                            actor=actor,
                            incident_id=incident.id,
                            action=target.value,
                            decision="not_applied",
                            reason=f"status stayed {incident.status.value}: {exc.message}",
                        )
                        await uow.commit()
                        return
                incident.resolution_summary = input.summary
                await uow.incidents.save(incident)
                INCIDENT_RESOLUTION_SECONDS.labels(target.value).observe(
                    incident.duration_seconds(now)
                )
                event = {
                    IncidentStatus.RESOLVED: EventType.INCIDENT_RESOLVED,
                    IncidentStatus.ESCALATED: EventType.INCIDENT_ESCALATED,
                    IncidentStatus.FAILED: EventType.INCIDENT_FAILED,
                    IncidentStatus.CLOSED: EventType.INCIDENT_CLOSED,
                }[target]
                await self.emitter.timeline(
                    uow,
                    incident_id=incident.id,
                    type=event,
                    actor=actor,
                    title=f"Incident {target.value}: {input.summary}",
                    payload={
                        "from": previous.value,
                        "to": target.value,
                        "summary": input.summary,
                        "usage": input.usage.model_dump(),
                        "duration_seconds": incident.duration_seconds(now),
                    },
                )
                await self.emitter.audit(
                    uow,
                    event_type=f"incident.{target.value}",
                    actor=actor,
                    incident_id=incident.id,
                    action=target.value,
                    decision="ok",
                    reason=input.summary,
                    data={"usage": input.usage.model_dump()},
                )
                kind = (
                    NotificationKind.INCIDENT_RESOLVED
                    if target is IncidentStatus.RESOLVED
                    else NotificationKind.INCIDENT_ESCALATED
                )
                await self.emitter.notify(
                    uow,
                    kind=kind,
                    incident_id=incident.id,
                    title=f"{incident.display_id} {target.value}: {incident.title}",
                    body=input.summary,
                )
            await uow.commit()
        if target in (IncidentStatus.RESOLVED, IncidentStatus.ESCALATED):
            await self._store_memory(input, actor)

    async def _store_memory(self, input: FinalizeInput, actor: Actor) -> None:
        async with self.c.uow_factory() as uow:
            incident = await uow.incidents.get(input.incident_id)
            existing = await uow.memories.get_for_incident(incident.id)
            if existing is not None:
                return
            service = IncidentMemoryService(
                uow.memories, embeddings=self.c.embeddings, llm=self.c.llm, clock=self.c.clock
            )
            hypotheses = await uow.hypotheses.list_for_incident(incident.id)
            plans = await uow.action_plans.list_for_incident(incident.id)
            evidence = await uow.evidence.list_for_incident(incident.id)
            outcome = (
                "false_positive"
                if input.outcome == "resolved"
                and not any(p.status is ActionPlanStatus.VERIFIED for p in plans)
                else input.outcome
            )
            memory = service.build(
                incident, hypotheses=hypotheses, plans=plans, evidence=evidence, outcome=outcome
            )
            summary = await service.summarize_with_llm(memory)
            if summary is not None:
                memory = service.build(
                    incident,
                    hypotheses=hypotheses,
                    plans=plans,
                    evidence=evidence,
                    outcome=outcome,
                    summary=summary,
                )
            await service.store(memory)
            await self.emitter.timeline(
                uow,
                incident_id=incident.id,
                type=EventType.MEMORY_STORED,
                actor=actor,
                title=f"Incident memory stored: {memory.root_cause[:120]}",
                payload={
                    "memory_id": str(memory.id),
                    "root_cause_service": memory.root_cause_service,
                    "outcome": memory.outcome,
                    "lessons": memory.lessons,
                },
            )
            await uow.commit()


def all_activities(container: RuntimeContainer) -> list[Any]:
    acts = IncidentActivities(container)
    return [
        acts.triage_incident,
        acts.set_incident_status,
        acts.run_agent_phase,
        acts.evaluate_remediation_policy,
        acts.expire_approval,
        acts.mark_plan,
        acts.capture_metrics,
        acts.execute_remediation,
        acts.verify_remediation,
        acts.verify_recovery,
        acts.finalize_incident,
    ]
