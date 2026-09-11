"""Tool executor: authorize → claim idempotency key → run with timeout/retries → record.

The executor persists a ``ToolExecutionRecord`` *before* running a mutating tool. In Postgres the
idempotency key is unique, so two workers cannot both claim the same mutation. Retries of a
completed mutation return the prior result instead of running again.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from aegis.domain.approval import ApprovalRequest
from aegis.domain.audit import AuditEvent
from aegis.domain.clock import Clock, SystemClock
from aegis.domain.enums import ExecutionStatus, ToolCategory
from aegis.domain.errors import AegisError, InfrastructureError, ToolExecutionError, ToolTimeout
from aegis.domain.evidence import Evidence
from aegis.domain.flow import BudgetUsage, ExecutionBudget
from aegis.domain.tool import ToolCallRequest, ToolExecutionRecord
from aegis.logging import get_logger
from aegis.ports.repositories import (
    AuditRepository,
    EvidenceRepository,
    ToolExecutionRepository,
    UnitOfWorkFactory,
)
from aegis.telemetry.metrics import (
    POLICY_DENIALS_TOTAL,
    TOOL_CALLS_TOTAL,
    TOOL_FAILURES_TOTAL,
    TOOL_LATENCY_SECONDS,
)
from aegis.telemetry.tracing import tracer
from aegis.tools.authorizer import ToolAuthorizer, idempotency_key_for
from aegis.tools.context import ToolContext
from aegis.tools.definition import ToolOutput
from aegis.tools.registry import ToolRegistry

log = get_logger(__name__)


class ToolExecutor:
    def __init__(
        self,
        registry: ToolRegistry,
        authorizer: ToolAuthorizer,
        *,
        ledger: ToolExecutionRepository,
        evidence: EvidenceRepository,
        audit: AuditRepository,
        clock: Clock | None = None,
        claim_factory: UnitOfWorkFactory | None = None,
    ) -> None:
        self.registry = registry
        self.authorizer = authorizer
        self.ledger = ledger
        self.evidence = evidence
        self.audit = audit
        self.clock = clock or SystemClock()
        # Claiming a mutation must be durable *before* the infrastructure is touched, which means
        # its own committed transaction — see _claim_mutation.
        self.claim_factory = claim_factory

    async def execute(  # noqa: PLR0912, PLR0915 - authorize/claim/run/record is one unit
        self,
        request: ToolCallRequest,
        *,
        ctx: ToolContext,
        budget: ExecutionBudget,
        usage: BudgetUsage,
        in_agent_loop: bool = True,
        approval: ApprovalRequest | None = None,
    ) -> ToolExecutionRecord:
        now = self.clock.now()
        decision = await self.authorizer.authorize(
            request,
            incident=ctx.incident,
            flow=ctx.flow,
            phase=ctx.phase,
            budget=budget,
            usage=usage,
            ledger=self.ledger,
            known_components=ctx.known_components,
            in_agent_loop=in_agent_loop,
            approval=approval,
            now=now,
        )
        category = (
            self.registry.get(request.tool_name).spec.category
            if self.registry.has(request.tool_name)
            else ToolCategory.READ_ONLY
        )
        version = (
            self.registry.get(request.tool_name).spec.version
            if self.registry.has(request.tool_name)
            else "?"
        )
        key = idempotency_key_for(request, category)
        record = ToolExecutionRecord(
            incident_id=request.incident_id,
            request_id=request.id,
            tool_name=request.tool_name,
            tool_version=version,
            category=category,
            arguments=request.arguments,
            idempotency_key=key,
            authorization=decision,
            agent_run_id=request.agent_run_id,
            action_plan_id=request.action_plan_id,
            phase=ctx.phase.name,
        )

        if not decision.allowed:
            record.status = ExecutionStatus.DENIED
            record.error = decision.reason
            record.result_summary = f"denied: {decision.denial_code}"
            record.finished_at = now
            record.idempotency_key = f"denied:{request.id}"
            await self.ledger.add(record)
            TOOL_CALLS_TOTAL.labels(request.tool_name, "denied").inc()
            POLICY_DENIALS_TOTAL.labels(decision.denial_code or "deny", request.tool_name).inc()
            await self._audit(
                "tool.denied",
                request,
                record,
                ctx,
                decision=decision.denial_code or "deny",
                reason=decision.reason,
            )
            log.info(
                "tool.denied",
                tool=request.tool_name,
                code=decision.denial_code,
                incident_id=str(request.incident_id),
            )
            return record

        # Exactly-once for mutations: reuse a prior completed execution.
        if category.is_mutation:
            prior = await self.ledger.find_by_key(key)
            if prior is not None and prior.status is ExecutionStatus.SUCCEEDED:
                record.status = ExecutionStatus.SKIPPED_DUPLICATE
                record.result = prior.result
                record.result_summary = f"duplicate suppressed; reused execution {prior.id}"
                record.finished_at = now
                record.idempotency_key = f"dup:{request.id}"
                await self.ledger.add(record)
                await self._audit(
                    "remediation.skipped_duplicate",
                    request,
                    record,
                    ctx,
                    decision="skipped",
                    reason=record.result_summary,
                )
                return record

        record.status = ExecutionStatus.RUNNING
        record.started_at = now
        try:
            await self._claim(record, durable=category.is_mutation)
        except AegisError as exc:  # unique violation surfaces as ConflictError
            record.status = ExecutionStatus.SKIPPED_DUPLICATE
            record.error = str(exc)
            record.idempotency_key = f"dup:{request.id}"
            record.result_summary = "concurrent duplicate suppressed"
            await self.ledger.add(record)
            return record

        defn = self.registry.get(request.tool_name)
        args = defn.parse_args(request.arguments, known_components=ctx.known_components)
        attempts = defn.spec.retry.max_attempts if not category.is_mutation else 1
        backoff = defn.spec.retry.initial_backoff_seconds
        output: ToolOutput | None = None
        error: Exception | None = None
        span = tracer().start_span(
            "aegis.tool.execute",
            attributes={
                "aegis.tool": defn.name,
                "aegis.category": category.value,
                "aegis.incident_id": str(request.incident_id),
            },
        )
        for attempt in range(1, attempts + 1):
            record.attempt = attempt
            try:
                output = await asyncio.wait_for(
                    defn.run(ctx, args), timeout=defn.spec.timeout_seconds
                )
                error = None
                break
            except TimeoutError as exc:
                error = ToolTimeout(f"{defn.name} timed out after {defn.spec.timeout_seconds}s")
                error.__cause__ = exc
            except InfrastructureError as exc:
                error = exc
            except AegisError as exc:
                error = exc
                break  # domain errors are not retried
            except Exception as exc:
                error = ToolExecutionError(f"{defn.name} failed: {type(exc).__name__}: {exc}")
                break
            if attempt < attempts:
                await asyncio.sleep(min(backoff, defn.spec.retry.max_backoff_seconds))
                backoff *= defn.spec.retry.backoff_multiplier

        finished = self.clock.now()
        record.finished_at = finished
        record.duration_ms = (finished - now).total_seconds() * 1000
        span.set_attribute("aegis.status", "succeeded" if output is not None else "failed")
        span.set_attribute("aegis.attempts", record.attempt)
        span.end()
        TOOL_LATENCY_SECONDS.labels(defn.name).observe(record.duration_ms / 1000)
        if output is not None:
            record.status = ExecutionStatus.SUCCEEDED
            record.result = output.data
            record.result_summary = output.summary
            evidence_items = [
                Evidence(
                    incident_id=request.incident_id,
                    kind=d.kind,
                    source=defn.name,
                    service=d.service,
                    title=d.title,
                    summary=d.summary,
                    data=d.data,
                    strength=d.strength,
                    observed_at=finished,
                    tool_execution_id=record.id,
                    agent_run_id=request.agent_run_id,
                    phase=ctx.phase.name,
                    tags=d.tags,
                )
                for d in output.evidence
            ]
            if evidence_items:
                stored = await self.evidence.add_many(evidence_items)
                record.evidence_ids = [e.id for e in stored]
            await self.ledger.save(record)
            TOOL_CALLS_TOTAL.labels(defn.name, "succeeded").inc()
            await self._audit(
                "tool.executed", request, record, ctx, decision="executed", reason=output.summary
            )
            log.info(
                "tool.executed",
                tool=defn.name,
                duration_ms=round(record.duration_ms, 1),
                evidence=len(record.evidence_ids),
                incident_id=str(request.incident_id),
            )
        else:
            assert error is not None
            record.status = (
                ExecutionStatus.TIMED_OUT
                if isinstance(error, ToolTimeout)
                else ExecutionStatus.FAILED
            )
            record.error = str(error)
            record.result_summary = f"failed: {error}"
            await self.ledger.save(record)
            TOOL_CALLS_TOTAL.labels(defn.name, record.status.value).inc()
            TOOL_FAILURES_TOTAL.labels(defn.name).inc()
            await self._audit(
                "tool.failed", request, record, ctx, decision="failed", reason=str(error)
            )
            log.warning(
                "tool.failed",
                tool=defn.name,
                error=str(error),
                incident_id=str(request.incident_id),
            )
        return record

    async def _audit(
        self,
        event_type: str,
        request: ToolCallRequest,
        record: ToolExecutionRecord,
        ctx: ToolContext,
        *,
        decision: str,
        reason: str,
    ) -> None:
        with contextlib.suppress(AegisError):
            await self.audit.append(
                AuditEvent(
                    event_type=event_type,
                    actor=request.requested_by,
                    incident_id=request.incident_id,
                    flow_name=ctx.flow.name,
                    phase=ctx.phase.name,
                    tool_name=request.tool_name,
                    action=request.tool_name,
                    decision=decision,
                    reason=reason[:500],
                    agent_run_id=request.agent_run_id,
                    tool_execution_id=record.id,
                    data={
                        "arguments": request.arguments,
                        "status": record.status.value,
                        "checks": [c.model_dump() for c in record.authorization.checks],
                    },
                )
            )

    async def _claim(self, record: ToolExecutionRecord, *, durable: bool) -> None:
        """Reserve the idempotency key for this execution.

        For a mutation the reservation is committed in its own transaction before the tool runs.
        Without that, a worker killed mid-remediation would roll the reservation back and the
        Temporal retry would find no trace of the attempt and repeat the infrastructure change.
        The cost of this ordering is that a crash leaves a RUNNING row: the retry is then refused
        as a duplicate and the incident escalates to a human, which is the safe direction for an
        action we cannot prove happened.
        """
        if durable and self.claim_factory is not None:
            async with self.claim_factory() as claim_uow:
                await claim_uow.tool_executions.add(record)
                await claim_uow.commit()
            return
        await self.ledger.add(record)

    @staticmethod
    def summarize(record: ToolExecutionRecord) -> dict[str, Any]:
        return {
            "tool": record.tool_name,
            "status": record.status.value,
            "summary": record.result_summary,
            "evidence": len(record.evidence_ids),
        }
