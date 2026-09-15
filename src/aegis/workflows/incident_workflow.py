"""The IncidentWorkflow: durable orchestration of one incident from detection to resolution.

Nothing non-deterministic happens here: every side effect is an activity, every wait is a
Temporal timer or signal, every identifier comes from ``workflow.uuid4()``.
"""

from __future__ import annotations

import asyncio  # noqa: F401 - TimeoutError alias documented
import contextlib
import uuid
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

with workflow.unsafe.imports_passed_through():
    from aegis.domain.flow import BudgetUsage
    from aegis.workflows.contracts import (
        ApprovalSignal,
        ExecutionOutcome,
        FinalizeInput,
        IncidentWorkflowInput,
        IncidentWorkflowResult,
        PhaseResult,
        PolicyOutcome,
        RunPhaseInput,
        TriageResult,
        VerificationOutcome,
        WorkflowStatus,
    )

DB_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=8,
)
AGENT_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=3,
)
# A remediation attempt is retried only for transport-level failures. Anything that happens after
# the tool ran (a stale write, a duplicate event) must not re-run the infrastructure change; the
# durable idempotency claim would refuse it anyway, and refusing is what escalates to a human.
EXEC_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    maximum_attempts=3,
    non_retryable_error_types=[
        "ToolAuthorizationError",
        "PolicyViolation",
        "ConcurrencyError",
        "ConflictError",
        "InvalidTransitionError",
    ],
)

MAX_EARLY_DECISIONS = 8
REOBSERVE_SECONDS = 45
"""How long to let a symptom develop before looking again."""
MAX_REOBSERVATIONS = 3
"""...and how many times, before accepting that there is nothing to diagnose."""
MAX_PHASE_ENTRIES = 2  # a third entry into the same phase means the investigation is cycling

PHASE_STATUS = {
    "investigate": "investigating",
    "hypothesize": "hypothesis_formed",
    "validate": "validating",
}


@workflow.defn(name="IncidentWorkflow")
class IncidentWorkflow:
    def __init__(self) -> None:
        self._approval: ApprovalSignal | None = None
        # A human can decide before the activity that created the request has returned the id to
        # the workflow. Those signals are buffered rather than dropped, or Aegis would escalate an
        # incident a human had already approved.
        self._early_decisions: dict[str, ApprovalSignal] = {}
        self._cancel_reason: str | None = None
        self._reobservations = 0
        # The cycle guard counts phase entries since the last re-observation: after waiting for
        # fresh data, the investigation genuinely starts again and must not inherit the old count.
        self._cycle_base = 0
        self._status = WorkflowStatus()

    # ------------------------------------------------------------------ signals / queries -------

    @workflow.signal
    def approval_decided(self, signal: ApprovalSignal) -> None:
        if self._status.awaiting_approval_id == signal.approval_id:
            self._approval = signal
            return
        self._early_decisions[str(signal.approval_id)] = signal
        while len(self._early_decisions) > MAX_EARLY_DECISIONS:
            # Signals are accepted for any id, so the buffer is bounded: an incident never has
            # more than a couple of approvals outstanding.
            self._early_decisions.pop(next(iter(self._early_decisions)))

    @workflow.signal
    def cancel(self, reason: str) -> None:
        self._cancel_reason = reason or "cancelled"
        self._status.cancelled = True

    @workflow.query
    def status(self) -> WorkflowStatus:
        return self._status

    def _cycling(self, next_phase: str) -> bool:
        """True when re-entering ``next_phase`` would repeat a phase that already ran twice since
        the last re-observation."""
        return self._status.phases[self._cycle_base :].count(next_phase) >= MAX_PHASE_ENTRIES

    def _reobserved_feedback(self) -> str:
        return (
            f"waited {REOBSERVE_SECONDS}s for the symptom to develop (re-observation "
            f"{self._reobservations}/{MAX_REOBSERVATIONS}); re-run the diagnostics on fresh data "
            "before concluding"
        )

    async def _reobserve(self) -> bool:
        """Wait for the symptom to develop, and say whether waiting is still allowed.

        Cycling and "nothing left to try" are the same situation seen from two angles: the
        investigation cannot get further with the data it has. Sometimes that is because there is
        nothing to find; often, early in an incident, it is because the fault is still small. So
        wait on a durable timer before concluding, a bounded number of times.
        """
        if self._reobservations >= MAX_REOBSERVATIONS:
            return False
        self._reobservations += 1
        self._status.reobservations = self._reobservations
        # The timeout is the expected path; an early return means a human cancelled, which the
        # guard at the top of the phase loop handles.
        with contextlib.suppress(TimeoutError):
            await workflow.wait_condition(
                lambda: self._cancel_reason is not None,
                timeout=timedelta(seconds=REOBSERVE_SECONDS),
            )
        self._cycle_base = len(self._status.phases)
        return True

    def _take_approval(self) -> ApprovalSignal | None:
        return self._approval

    def _adopt_pending(self, approval_id: uuid.UUID) -> None:
        """Start awaiting ``approval_id``, adopting a decision that arrived before this workflow
        learned the id from the activity that created the request."""
        self._status.awaiting_approval_id = approval_id
        self._approval = self._early_decisions.pop(str(approval_id), None)

    # ------------------------------------------------------------------ run ---------------------

    @workflow.run
    async def run(self, input: IncidentWorkflowInput) -> IncidentWorkflowResult:
        wf_id = workflow.info().workflow_id
        triage: TriageResult = await workflow.execute_activity(
            "triage_incident",
            input.incident_id,
            result_type=TriageResult,
            start_to_close_timeout=timedelta(seconds=60),
            retry_policy=DB_RETRY,
        )
        if not triage.active:
            return IncidentWorkflowResult(
                incident_id=input.incident_id,
                outcome="closed",
                summary="incident was no longer active at triage",
            )
        phase = triage.initial_phase
        usage = BudgetUsage()
        attempts = 0
        reobserving = False
        refresh = False
        feedback: list[str] = []
        outcome = "escalated"
        summary = ""
        for _ in range(input.max_phases):
            if self._cancel_reason is not None:
                outcome, summary = "closed", self._cancel_reason
                break
            self._status.phase = phase
            if not reobserving:
                # A re-observation is the same visit to the phase, so it must not count towards
                # the cycle guard, and the incident's status has not changed either.
                self._status.phases.append(phase)
                await self._enter_phase(input.incident_id, phase)
            reobserving = False
            try:
                result: PhaseResult = await workflow.execute_activity(
                    "run_agent_phase",
                    RunPhaseInput(
                        incident_id=input.incident_id,
                        flow_name=triage.flow_name,
                        flow_version=triage.flow_version,
                        phase=phase,
                        agent_run_id=workflow.uuid4(),
                        usage=usage,
                        workflow_id=wf_id,
                        feedback=feedback,
                        refresh=refresh,
                    ),
                    result_type=PhaseResult,
                    start_to_close_timeout=timedelta(seconds=input.phase_timeout_seconds),
                    heartbeat_timeout=timedelta(seconds=90),
                    retry_policy=AGENT_RETRY,
                )
            except ActivityError as exc:
                outcome, summary = "failed", f"agent phase '{phase}' failed: {exc.cause or exc}"
                break
            usage = result.usage
            feedback = []
            refresh = False
            if result.decision == "transition" and result.next_phase:
                if result.next_phase == "escalate":
                    outcome, summary = "escalated", result.summary or "flow escalated"
                    break
                if result.next_phase == "verify":
                    # reached verify without an action: symptoms cleared during investigation
                    verified = await self._verify_without_action(input.incident_id)
                    if verified.status == "passed":
                        outcome, summary = "resolved", "symptoms cleared without remediation"
                        break
                    feedback.append(f"verification without action failed: {verified.summary}")
                    if self._cycling("investigate"):
                        if not await self._reobserve():
                            outcome, summary = (
                                "escalated",
                                "investigation is cycling on phase investigate without reaching a "
                                "remediation",
                            )
                            break
                        feedback.append(self._reobserved_feedback())
                        refresh = True
                    phase = "investigate"
                    continue
                if self._cycling(result.next_phase):
                    if not await self._reobserve():
                        outcome, summary = (
                            "escalated",
                            f"investigation is cycling on phase {result.next_phase} without "
                            "reaching a remediation",
                        )
                        break
                    # Fresh data is what the loop was missing, and only `investigate` collects it.
                    feedback.append(self._reobserved_feedback())
                    refresh = True
                    reobserving = phase == "investigate"
                    phase = "investigate"
                    continue
                phase = result.next_phase
                continue
            if result.decision == "no_action":
                verified = await self._verify_without_action(input.incident_id)
                if verified.status == "passed":
                    outcome, summary = (
                        "resolved",
                        "no remediation required; metrics within baseline",
                    )
                    break
                feedback.append(f"metrics did not stay within baseline: {verified.summary}")
                if self._cycling("investigate"):
                    if not await self._reobserve():
                        outcome, summary = (
                            "escalated",
                            "investigation is cycling on phase investigate without reaching a "
                            "remediation",
                        )
                        break
                    feedback.append(self._reobserved_feedback())
                phase = "investigate"
                continue
            if result.decision == "action_planned" and result.action_plan_id is not None:
                attempts += 1
                self._status.remediation_attempts = attempts
                step_outcome, step_summary, step_feedback = await self._remediate(
                    input, result.action_plan_id, attempts, triage.max_remediation_attempts
                )
                if step_outcome == "resolved":
                    outcome, summary = "resolved", step_summary
                    break
                if step_outcome == "replan":
                    feedback.extend(step_feedback)
                    if self._cycling("remediate"):
                        outcome, summary = (
                            "escalated",
                            f"investigation is cycling on phase {'remediate'} without reaching a "
                            "remediation",
                        )
                        break
                    phase = "remediate"
                    continue
                outcome, summary = step_outcome, step_summary
                break
            if result.decision == "escalate":
                outcome, summary = "escalated", result.summary or "agent requested escalation"
                break
            if result.termination == "insufficient_signal":
                if not await self._reobserve():
                    outcome, summary = (
                        "escalated",
                        f"no diagnosable signal after {MAX_REOBSERVATIONS} re-observations "
                        f"over {MAX_REOBSERVATIONS * REOBSERVE_SECONDS}s: {result.summary}",
                    )
                    break
                feedback.append(self._reobserved_feedback())
                refresh = True
                reobserving = phase == "investigate"
                phase = "investigate"
                continue
            # terminate: budget exhausted, incident inactive, error
            if result.termination == "incident_inactive":
                outcome, summary = "closed", "incident no longer active"
            elif result.termination == "budget_exhausted":
                outcome, summary = "escalated", f"execution budget exhausted: {result.summary}"
            else:
                outcome, summary = "failed", result.summary or "agent terminated"
            break
        else:
            outcome, summary = "escalated", "maximum number of phases reached"
        await workflow.execute_activity(
            "finalize_incident",
            FinalizeInput(
                incident_id=input.incident_id, outcome=outcome, summary=summary, usage=usage
            ),
            start_to_close_timeout=timedelta(seconds=120),
            retry_policy=DB_RETRY,
        )
        self._status.status = outcome
        return IncidentWorkflowResult(
            incident_id=input.incident_id,
            outcome=outcome,
            phases=self._status.phases,
            remediation_attempts=attempts,
            summary=summary,
        )

    # ------------------------------------------------------------------ helpers -----------------

    async def _enter_phase(self, incident_id: object, phase: str) -> None:
        status = PHASE_STATUS.get(phase)
        if status is not None:
            await workflow.execute_activity(
                "set_incident_status",
                args=[incident_id, status, f"entering phase '{phase}'"],
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=DB_RETRY,
            )

    async def _verify_without_action(self, incident_id: object) -> VerificationOutcome:
        await workflow.execute_activity(
            "set_incident_status",
            args=[incident_id, "verifying", "verifying recovery without action"],
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=DB_RETRY,
        )
        result: VerificationOutcome = await workflow.execute_activity(
            "verify_recovery",
            incident_id,
            result_type=VerificationOutcome,
            start_to_close_timeout=timedelta(seconds=600),
            heartbeat_timeout=timedelta(seconds=90),
            retry_policy=RetryPolicy(maximum_attempts=2),
        )
        return result

    async def _remediate(
        self,
        input: IncidentWorkflowInput,
        plan_id: object,
        attempt: int,
        max_attempts: int,
    ) -> tuple[str, str, list[str]]:
        incident_id = input.incident_id
        await workflow.execute_activity(
            "set_incident_status",
            args=[incident_id, "remediation_planned", "remediation planned"],
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=DB_RETRY,
        )
        policy: PolicyOutcome = await workflow.execute_activity(
            "evaluate_remediation_policy",
            plan_id,
            result_type=PolicyOutcome,
            start_to_close_timeout=timedelta(seconds=60),
            retry_policy=DB_RETRY,
        )
        if policy.effect == "deny":
            return "escalated", f"policy denied the proposed remediation: {policy.reason}", []
        approval_id = None
        if policy.effect == "require_approval" and policy.approval_id is not None:
            approval_id = policy.approval_id
            self._adopt_pending(approval_id)
            await workflow.execute_activity(
                "set_incident_status",
                args=[incident_id, "awaiting_approval", "awaiting human approval"],
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=DB_RETRY,
            )
            try:
                await workflow.wait_condition(
                    lambda: self._approval is not None or self._cancel_reason is not None,
                    timeout=timedelta(seconds=input.approval_timeout_seconds),
                )
                decided = True
            except TimeoutError:
                decided = False
            self._status.awaiting_approval_id = None
            if self._cancel_reason is not None:
                await workflow.execute_activity(
                    "expire_approval",
                    args=[approval_id, "incident cancelled"],
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=DB_RETRY,
                )
                return "closed", self._cancel_reason, []
            approval = self._take_approval()
            if not decided or approval is None:
                await workflow.execute_activity(
                    "expire_approval",
                    args=[approval_id, "approval timed out"],
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=DB_RETRY,
                )
                return "escalated", "approval request timed out", []
            if not approval.approved:
                reason = approval.reason or "rejected without reason"
                await workflow.execute_activity(
                    "mark_plan",
                    args=[plan_id, "rejected", reason],
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=DB_RETRY,
                )
                if attempt < max_attempts:
                    return (
                        "replan",
                        "",
                        [
                            f"the previous remediation plan was rejected by "
                            f"{approval.decided_by}: {reason}. Propose a different plan."
                        ],
                    )
                return "escalated", f"remediation rejected: {reason}", []
        await workflow.execute_activity(
            "set_incident_status",
            args=[incident_id, "remediating", "executing remediation"],
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=DB_RETRY,
        )
        if self._cancel_reason is not None:
            # An operator closing an incident is asking Aegis to stop; do not start a mutation.
            return "closed", self._cancel_reason, []
        before: dict[str, float] = await workflow.execute_activity(
            "capture_metrics",
            plan_id,
            result_type=dict[str, float],
            start_to_close_timeout=timedelta(seconds=60),
            retry_policy=RetryPolicy(maximum_attempts=2),
        )
        try:
            execution: ExecutionOutcome = await workflow.execute_activity(
                "execute_remediation",
                args=[plan_id, approval_id, False],
                result_type=ExecutionOutcome,
                start_to_close_timeout=timedelta(seconds=180),
                retry_policy=EXEC_RETRY,
            )
        except ActivityError as exc:
            return "failed", f"remediation execution failed: {exc.cause or exc}", []
        if execution.status not in ("succeeded", "skipped_duplicate"):
            return (
                "escalated",
                f"remediation did not execute: {execution.error or execution.status}",
                [],
            )
        await workflow.execute_activity(
            "set_incident_status",
            args=[incident_id, "verifying", "verifying remediation"],
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=DB_RETRY,
        )
        verification: VerificationOutcome = await workflow.execute_activity(
            "verify_remediation",
            args=[plan_id, before],
            result_type=VerificationOutcome,
            start_to_close_timeout=timedelta(seconds=900),
            heartbeat_timeout=timedelta(seconds=90),
            retry_policy=RetryPolicy(maximum_attempts=2),
        )
        if verification.status == "passed":
            return "resolved", f"remediation verified: {verification.summary}", []
        feedback = [f"remediation attempt {attempt} failed verification: {verification.summary}"]
        if verification.rollback_available:
            await workflow.execute_activity(
                "set_incident_status",
                args=[incident_id, "rolled_back", "rolling back"],
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=DB_RETRY,
            )
            try:
                rollback: ExecutionOutcome = await workflow.execute_activity(
                    "execute_remediation",
                    args=[plan_id, approval_id, True],
                    result_type=ExecutionOutcome,
                    start_to_close_timeout=timedelta(seconds=180),
                    retry_policy=EXEC_RETRY,
                )
                feedback.append(f"rollback {rollback.status}")
            except ActivityError as exc:
                feedback.append(f"rollback failed: {exc.cause or exc}")
        if attempt < max_attempts:
            return "replan", "", feedback
        return (
            "escalated",
            f"verification failed after {attempt} attempt(s): {verification.summary}",
            [],
        )


def _unused() -> None:  # keeps ApplicationError referenced for sandbox import analysis
    _ = ApplicationError
