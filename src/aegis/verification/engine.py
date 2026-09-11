"""Post-remediation verification.

The engine samples the metrics named by the action plan's ``VerificationSpec`` after a stabilization
period and requires every condition to hold on several consecutive polls. It reports before/after
values so the incident record shows *what* recovered, not just that something did.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

from aegis.domain.action import VerificationCondition, VerificationResult, VerificationSpec
from aegis.domain.clock import Clock, SystemClock
from aegis.domain.enums import VerificationStatus
from aegis.domain.errors import AegisError
from aegis.domain.telemetry import MetricSeries
from aegis.logging import get_logger
from aegis.ports.telemetry import TelemetryProvider

log = get_logger(__name__)

# Metrics that accumulate rather than fluctuate: a leak keeps climbing even while it is still
# below its threshold, so "currently under the limit" is not proof that the cause is gone.
ACCUMULATING_METRICS = frozenset(
    {"memory_percent", "connections", "idle_in_transaction", "saturation", "leaked"}
)
# Share of the condition's own threshold a metric may gain across the window before verification
# treats it as still accumulating.
MAX_ACCUMULATION_SHARE = 0.10


def accumulation_share(
    cond: VerificationCondition, series: MetricSeries, baseline: float | None
) -> float | None:
    """How much of its threshold an accumulating metric gained across the window.

    ``None`` when the rule does not apply (not an accumulating metric, or too few samples).
    A restart that drops a metric and lets it settle scores at or below zero; a leak that keeps
    growing scores positive and proportional to how fast it is refilling.
    """
    if cond.metric not in ACCUMULATING_METRICS or len(series.samples) < 4:
        return None
    threshold = cond.target
    if threshold is None and cond.max_ratio_to_baseline is not None and baseline:
        threshold = baseline * cond.max_ratio_to_baseline
    if not threshold:
        return None
    head = series.values[: max(1, len(series.values) // 4)]
    tail = series.values[-max(1, len(series.values) // 4) :]
    gained = (sum(tail) / len(tail)) - (sum(head) / len(head))
    return gained / abs(threshold)


Sleeper = Callable[[float], Awaitable[None]]
Progress = Callable[[dict[str, Any]], Awaitable[None]]


class VerificationEngine:
    def __init__(
        self,
        telemetry: TelemetryProvider,
        *,
        clock: Clock | None = None,
        sleep: Sleeper | None = None,
        poll_seconds: float = 5.0,
        window_seconds: int = 30,
        consecutive_required: int = 3,
    ) -> None:
        self.telemetry = telemetry
        self.clock = clock or SystemClock()
        self.sleep = sleep or asyncio.sleep
        self.poll_seconds = poll_seconds
        self.window_seconds = window_seconds
        self.consecutive_required = consecutive_required

    async def baselines(self, spec: VerificationSpec) -> dict[tuple[str, str], float | None]:
        out: dict[tuple[str, str], float | None] = {}
        for cond in spec.conditions:
            key = (cond.service, cond.metric)
            if key not in out:
                out[key] = await self.telemetry.baseline(cond.service, cond.metric)
        return out

    async def snapshot(self, spec: VerificationSpec) -> dict[str, float]:
        """Current values (mean over the last window) for every condition metric."""
        end = self.clock.now()
        start = end - timedelta(seconds=self.window_seconds)
        out: dict[str, float] = {}
        for cond in spec.conditions:
            try:
                series = await self.telemetry.metrics(
                    cond.service, cond.metric, start=start, end=end
                )
            except AegisError:
                continue
            value = series.mean()
            if value is not None:
                out[f"{cond.service}.{cond.metric}"] = value
        return out

    async def evaluate_once(
        self, spec: VerificationSpec, baselines: dict[tuple[str, str], float | None]
    ) -> tuple[bool, list[dict[str, Any]], dict[str, float], bool]:
        """Returns (all_ok, per-condition results, current values, data_available)."""
        end = self.clock.now()
        start = end - timedelta(seconds=self.window_seconds)
        results: list[dict[str, Any]] = []
        current: dict[str, float] = {}
        all_ok = True
        available = False
        for cond in spec.conditions:
            try:
                series = await self.telemetry.metrics(
                    cond.service, cond.metric, start=start, end=end
                )
            except AegisError as exc:
                results.append(
                    {
                        "service": cond.service,
                        "metric": cond.metric,
                        "ok": False,
                        "detail": f"telemetry unavailable: {exc}",
                    }
                )
                all_ok = False
                continue
            value = series.mean()
            if value is None:
                results.append(
                    {
                        "service": cond.service,
                        "metric": cond.metric,
                        "ok": False,
                        "detail": "no samples in window",
                    }
                )
                all_ok = False
                continue
            available = True
            baseline = baselines.get((cond.service, cond.metric))
            ok, detail = cond.evaluate(value, baseline)
            share = accumulation_share(cond, series, baseline)
            if ok and share is not None and share > MAX_ACCUMULATION_SHARE:
                ok = False
                detail = (
                    f"{detail}, but still accumulating (+{share * 100:.0f}% of the threshold "
                    "across the window)"
                )
            current[f"{cond.service}.{cond.metric}"] = value
            results.append(
                {
                    "service": cond.service,
                    "metric": cond.metric,
                    "ok": ok,
                    "detail": detail,
                    "value": value,
                    "baseline": baseline,
                    "description": cond.description,
                    "accumulation_share": share,
                }
            )
            all_ok = all_ok and ok
        if not spec.require_all and results:
            all_ok = any(bool(r.get("ok")) for r in results)
        return all_ok, results, current, available

    async def verify(
        self,
        spec: VerificationSpec,
        *,
        before: dict[str, float] | None = None,
        baselines: dict[tuple[str, str], float | None] | None = None,
        on_progress: Progress | None = None,
    ) -> VerificationResult:
        if not spec.conditions:
            # Nothing measurable was specified, so recovery cannot be proven. Saying "passed"
            # here would resolve an incident on the absence of evidence.
            return self._result(
                VerificationStatus.INCONCLUSIVE,
                [],
                before,
                {},
                "no verification conditions were specified, so recovery cannot be proven",
            )
        baselines = baselines if baselines is not None else await self.baselines(spec)
        started = self.clock.now()
        deadline = started + timedelta(seconds=spec.timeout_seconds)
        if spec.stabilization_seconds > 0:
            await self.sleep(spec.stabilization_seconds)
        consecutive = 0
        last_results: list[dict[str, Any]] = []
        last_current: dict[str, float] = {}
        polls = 0
        saw_data = False
        while True:
            all_ok, last_results, last_current, available = await self.evaluate_once(
                spec, baselines
            )
            polls += 1
            saw_data = saw_data or available
            consecutive = consecutive + 1 if all_ok else 0
            if on_progress is not None:
                await on_progress(
                    {
                        "poll": polls,
                        "ok": all_ok,
                        "consecutive": consecutive,
                        "results": last_results,
                    }
                )
            if consecutive >= self.consecutive_required and saw_data:
                return self._result(
                    VerificationStatus.PASSED,
                    last_results,
                    before,
                    last_current,
                    f"all {len(spec.conditions)} conditions held for "
                    f"{consecutive} consecutive polls",
                )
            if self.clock.now() >= deadline:
                failing = [r for r in last_results if not r.get("ok")]
                status = VerificationStatus.FAILED if saw_data else VerificationStatus.INCONCLUSIVE
                summary = (
                    f"{len(failing)} of {len(spec.conditions)} conditions still failing after "
                    f"{spec.timeout_seconds}s: " + "; ".join(str(r["detail"]) for r in failing[:4])
                    if saw_data
                    else "no telemetry available during the verification window"
                )
                return self._result(status, last_results, before, last_current, summary)
            await self.sleep(self.poll_seconds)

    def _result(
        self,
        status: VerificationStatus,
        results: list[dict[str, Any]],
        before: dict[str, float] | None,
        after: dict[str, float],
        summary: str,
    ) -> VerificationResult:
        log.info("verification.result", status=status.value, summary=summary)
        return VerificationResult(
            status=status,
            checked_at=self.clock.now(),
            condition_results=tuple(results),
            summary=summary,
            before=before or {},
            after=after,
        )


def describe_change(before: dict[str, float], after: dict[str, float]) -> list[str]:
    lines = []
    for key, b in before.items():
        a = after.get(key)
        if a is None:
            continue
        if b:
            lines.append(f"{key}: {b:.3g} → {a:.3g} ({(a - b) / abs(b) * 100:+.0f}%)")
        else:
            lines.append(f"{key}: {b:.3g} → {a:.3g}")
    return lines


def _unused(_: datetime) -> None:  # keep datetime import explicit for type checkers
    return None
