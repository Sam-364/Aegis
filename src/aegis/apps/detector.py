"""Detection process: polls telemetry, opens incidents, starts workflows. Single active leader."""

from __future__ import annotations

import asyncio
import contextlib
import signal
import time
from datetime import timedelta

from prometheus_client import start_http_server

from aegis.application.bootstrap import build_runtime
from aegis.config import get_settings
from aegis.detection.correlation import IncidentCorrelator
from aegis.detection.detector import AnomalyDetector, DetectorConfig
from aegis.detection.engine import DetectionEngine
from aegis.logging import get_logger
from aegis.telemetry.metrics import (
    DETECTION_CYCLE_SECONDS,
    DETECTION_SIGNALS_TOTAL,
    REGISTRY,
)

log = get_logger("aegis.detector")
LOCK_NAME = "detector-leader"


async def run() -> None:
    settings = get_settings()
    container, resources = await build_runtime(settings, role="detector")
    if not settings.detection_enabled:
        log.warning("detector.disabled", reason="AEGIS_DETECTION_ENABLED=false")
        await resources.aclose()
        return
    metrics_port = settings.metrics_port
    if settings.metrics_enabled:
        with contextlib.suppress(OSError):
            start_http_server(metrics_port, registry=REGISTRY)
    engine = DetectionEngine(
        container.telemetry,
        container.intake,
        interval_seconds=settings.detection_interval_seconds,
        detector=AnomalyDetector(
            DetectorConfig(
                alpha=settings.detection_ewma_alpha,
                warmup=settings.detection_warmup_samples,
                zscore_threshold=settings.detection_zscore_threshold,
            )
        ),
        correlator=IncidentCorrelator(window_seconds=settings.correlation_window_seconds),
        baseline_store=resources.baseline_store,
        clock=container.clock,
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    lock = resources.lock
    ttl = timedelta(seconds=max(15, settings.detection_interval_seconds * 4))
    leader = False
    bootstrapped = False
    log.info(
        "detector.starting", interval=settings.detection_interval_seconds, metrics_port=metrics_port
    )
    while not stop.is_set():
        started = time.perf_counter()
        try:
            if lock is not None:
                leader = await (
                    lock.renew(LOCK_NAME, ttl) if leader else lock.acquire(LOCK_NAME, ttl)
                )
            else:
                leader = True
            if leader:
                if not bootstrapped:
                    await engine.bootstrap(window_seconds=settings.detection_window_seconds)
                    bootstrapped = True
                result = await engine.cycle()
                for s in result.signals:
                    DETECTION_SIGNALS_TOTAL.labels(s.kind.value).inc()
            DETECTION_CYCLE_SECONDS.observe(time.perf_counter() - started)
        except Exception as exc:
            log.error("detector.cycle_failed", error=str(exc))
            bootstrapped = bootstrapped and "unreachable" not in str(exc)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=settings.detection_interval_seconds)
    if lock is not None and leader:
        await lock.release(LOCK_NAME)
    await resources.aclose()
    log.info("detector.stopped")


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
