"""Starts and signals incident workflows through the Temporal client."""

from __future__ import annotations

import contextlib
import uuid
from typing import Any

from temporalio.client import Client, WorkflowHandle
from temporalio.common import WorkflowIDReusePolicy
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError, RPCStatusCode

from aegis.domain.errors import ConflictError
from aegis.logging import get_logger
from aegis.workflows.contracts import (
    TASK_QUEUE,
    ApprovalSignal,
    IncidentWorkflowInput,
    IncidentWorkflowResult,
)
from aegis.workflows.incident_workflow import IncidentWorkflow

log = get_logger(__name__)


def _workflow_gone(exc: RPCError) -> bool:
    """True when the workflow this signal targets no longer exists or has already completed."""
    message = (exc.message or "").lower()
    return exc.status is RPCStatusCode.NOT_FOUND or "already completed" in message


def workflow_id_for(incident_id: uuid.UUID) -> str:
    return f"incident-{incident_id}"


class TemporalWorkflowController:
    def __init__(
        self,
        client: Client,
        *,
        task_queue: str = TASK_QUEUE,
        approval_timeout_seconds: int = 900,
        phase_timeout_seconds: int = 600,
    ) -> None:
        self.client = client
        self.task_queue = task_queue
        self.approval_timeout = approval_timeout_seconds
        self.phase_timeout = phase_timeout_seconds

    @classmethod
    async def connect(
        cls, address: str, namespace: str, **kwargs: Any
    ) -> TemporalWorkflowController:
        client = await Client.connect(
            address, namespace=namespace, data_converter=pydantic_data_converter
        )
        return cls(client, **kwargs)

    async def start_incident_workflow(self, incident_id: uuid.UUID) -> str:
        wf_id = workflow_id_for(incident_id)
        with contextlib.suppress(WorkflowAlreadyStartedError):
            await self.client.start_workflow(
                IncidentWorkflow.run,
                IncidentWorkflowInput(
                    incident_id=incident_id,
                    approval_timeout_seconds=self.approval_timeout,
                    phase_timeout_seconds=self.phase_timeout,
                ),
                id=wf_id,
                task_queue=self.task_queue,
                id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE_FAILED_ONLY,
            )
        return wf_id

    def handle(self, workflow_id: str) -> WorkflowHandle[Any, IncidentWorkflowResult]:
        return self.client.get_workflow_handle(workflow_id, result_type=IncidentWorkflowResult)

    async def signal_approval(
        self, workflow_id: str, approval_id: uuid.UUID, approved: bool, decided_by: str, reason: str
    ) -> None:
        try:
            await self.handle(workflow_id).signal(
                IncidentWorkflow.approval_decided,
                ApprovalSignal(
                    approval_id=approval_id, approved=approved, decided_by=decided_by, reason=reason
                ),
            )
        except RPCError as exc:
            if not _workflow_gone(exc):
                raise
            # The decision arrived after the workflow gave up waiting. Say so plainly rather than
            # letting a transport error surface as a 500: nothing will act on this approval.
            raise ConflictError(
                "the incident workflow has already finished; this decision cannot be acted on",
                details={"workflow_id": workflow_id, "approval_id": str(approval_id)},
            ) from exc

    async def signal_cancel(self, workflow_id: str, reason: str) -> None:
        """Cancelling is advisory and idempotent: the incident status is the authoritative record,
        so a workflow that has already finished (or was archived) is not an error."""
        try:
            await self.handle(workflow_id).signal(IncidentWorkflow.cancel, reason)
        except RPCError as exc:
            if not _workflow_gone(exc):
                raise
            log.info("workflow.cancel_noop", workflow_id=workflow_id, detail=str(exc.message))

    async def describe(self, workflow_id: str) -> dict[str, Any]:
        desc = await self.handle(workflow_id).describe()
        status = (
            await self.handle(workflow_id).query(IncidentWorkflow.status)
            if desc.status is not None and desc.status.name == "RUNNING"
            else None
        )
        return {
            "workflow_id": workflow_id,
            "run_id": desc.run_id,
            "status": desc.status.name if desc.status else None,
            "start_time": desc.start_time.isoformat() if desc.start_time else None,
            "close_time": desc.close_time.isoformat() if desc.close_time else None,
            "task_queue": desc.task_queue,
            "query": status.model_dump(mode="json") if status else None,
        }
