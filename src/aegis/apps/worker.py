"""Temporal worker process: runs IncidentWorkflow and all activities (including the agent)."""

from __future__ import annotations

import asyncio
import contextlib
import signal

from prometheus_client import start_http_server

from aegis.application.bootstrap import build_runtime
from aegis.config import get_settings
from aegis.logging import get_logger
from aegis.telemetry.metrics import REGISTRY
from aegis.workflows.worker import build_worker

log = get_logger("aegis.worker")


async def run() -> None:
    settings = get_settings()
    container, resources = await build_runtime(settings, role="worker", temporal_required=True)
    assert resources.temporal is not None
    metrics_port = settings.metrics_port
    if settings.metrics_enabled:
        with contextlib.suppress(OSError):
            start_http_server(metrics_port, registry=REGISTRY)
    worker = build_worker(resources.temporal, container, task_queue=settings.temporal_task_queue)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    log.info("worker.starting", task_queue=settings.temporal_task_queue, metrics_port=metrics_port)
    async with worker:
        await stop.wait()
        log.info("worker.shutdown_requested")
    await resources.aclose()
    log.info("worker.stopped")


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
