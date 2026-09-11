"""Temporal worker construction shared by the worker process and the workflow tests."""

from __future__ import annotations

from temporalio.client import Client
from temporalio.worker import Worker
from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner, SandboxRestrictions

from aegis.application.container import RuntimeContainer
from aegis.workflows.activities import all_activities
from aegis.workflows.contracts import TASK_QUEUE
from aegis.workflows.incident_workflow import IncidentWorkflow


def build_worker(
    client: Client,
    container: RuntimeContainer,
    *,
    task_queue: str = TASK_QUEUE,
    max_concurrent_activities: int = 8,
) -> Worker:
    return Worker(
        client,
        task_queue=task_queue,
        workflows=[IncidentWorkflow],
        activities=all_activities(container),
        workflow_runner=SandboxedWorkflowRunner(
            restrictions=SandboxRestrictions.default.with_passthrough_modules("aegis")
        ),
        max_concurrent_activities=max_concurrent_activities,
    )
