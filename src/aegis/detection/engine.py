"""Detection engine: polls telemetry, detects anomalies, correlates and opens incidents."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from aegis.detection.correlation import IncidentCorrelator
from aegis.detection.detector import AnomalyDetector, DetectorConfig
from aegis.domain.clock import Clock, SystemClock
from aegis.domain.incident import AnomalySignal, Incident
from aegis.domain.telemetry import Topology
from aegis.logging import get_logger
from aegis.ports.detection import IncidentSink
from aegis.ports.telemetry import BaselineStore, TelemetryProvider

log = get_logger(__name__)


@dataclass
class CycleResult:
    at: datetime
    samples: int = 0
    signals: list[AnomalySignal] = field(default_factory=list)
    opened: list[Incident] = field(default_factory=list)
    attached: list[Incident] = field(default_factory=list)
    cleared: list[str] = field(default_factory=list)


class DetectionEngine:
    def __init__(
        self,
        telemetry: TelemetryProvider,
        sink: IncidentSink,
        *,
        interval_seconds: float = 5.0,
        detector: AnomalyDetector | None = None,
        correlator: IncidentCorrelator | None = None,
        baseline_store: BaselineStore | None = None,
        clock: Clock | None = None,
        topology_ttl_seconds: float = 60.0,
    ) -> None:
        self.telemetry = telemetry
        self.sink = sink
        self.interval = interval_seconds
        self.detector = detector or AnomalyDetector(DetectorConfig())
        self.correlator = correlator or IncidentCorrelator()
        self.baseline_store = baseline_store
        self.clock = clock or SystemClock()
        self._topology: Topology | None = None
        self._topology_at: datetime | None = None
        self._topology_ttl = timedelta(seconds=topology_ttl_seconds)
        self._last_seen: dict[tuple[str, str], datetime] = {}
        self._cycles = 0

    async def topology(self) -> Topology:
        now = self.clock.now()
        if (
            self._topology is None
            or self._topology_at is None
            or now - self._topology_at > self._topology_ttl
        ):
            self._topology = await self.telemetry.topology()
            self._topology_at = now
            self.detector.component_kinds = {n.name: n.kind for n in self._topology.nodes}
        return self._topology

    async def bootstrap(self, window_seconds: int = 600) -> int:
        """Seed baselines so detection is live immediately after start.

        Persisted baselines are used as a *prior*, never as the final answer: recent history is
        always replayed on top of them. A saved baseline can describe a world that no longer
        exists (the process was down through a deployment, or the observed system was rebuilt),
        and trusting it verbatim turns the first live sample into a false positive.
        """
        restored = 0
        if self.baseline_store is not None:
            saved = await self.baseline_store.load("detector")
            if saved:
                self.detector.load(saved)
                restored = len(self.detector.baselines)
        await self.topology()
        series = await self.telemetry.snapshot(window_seconds=window_seconds)
        fed = 0
        stride = max(1, int(self.interval))
        for s in series:
            for i, sample in enumerate(s.samples):
                if i % stride:
                    continue
                self.detector.observe(s.service, s.metric, sample.value, sample.at, emit=False)
                fed += 1
            if s.samples:
                self._last_seen[(s.service, s.metric)] = s.samples[-1].at
        # Anything that was firing due to history is reset; only live samples raise signals.
        for state in self.detector.states.values():
            state.firing = False
            state.consecutive = 0
            state.normal_streak = 0
        for b in self.detector.baselines.values():
            b.frozen = False
        self.detector.frozen_since.clear()
        log.info(
            "detector.bootstrapped", samples=fed, series=len(series), restored_baselines=restored
        )
        return fed

    async def cycle(self) -> CycleResult:
        now = self.clock.now()
        result = CycleResult(at=now)
        topology = await self.topology()
        series = await self.telemetry.snapshot(window_seconds=max(2, int(self.interval * 2)))
        for s in series:
            if not s.samples:
                continue
            latest = s.samples[-1]
            key = (s.service, s.metric)
            if key in self._last_seen and latest.at <= self._last_seen[key]:
                continue
            self._last_seen[key] = latest.at
            signals, cleared, _ = self.detector.observe(
                s.service, s.metric, latest.value, latest.at
            )
            result.samples += 1
            result.signals.extend(signals)
            result.cleared.extend(f"{s.service}.{c}" for c in cleared)
        if result.signals:
            open_incidents = await self.sink.open_incidents()
            decision = self.correlator.correlate(result.signals, open_incidents, topology, now)
            for att in decision.attached:
                incident = await self.sink.attach_signals(att.incident.id, att.signals)
                result.attached.append(incident)
            for opened in decision.opened:
                incident = await self.sink.open_incident(
                    title=opened.title,
                    summary=opened.summary,
                    severity=opened.severity,
                    signals=opened.signals,
                    affected_services=opened.affected_services,
                    correlation_key=opened.correlation_key,
                )
                result.opened.append(incident)
                log.info(
                    "detector.incident_opened",
                    incident_id=str(incident.id),
                    title=incident.title,
                    severity=incident.severity.value,
                    signals=len(opened.signals),
                )
        self._cycles += 1
        if self.baseline_store is not None and self._cycles % 12 == 0:
            await self.baseline_store.save("detector", self.detector.export(), ttl_seconds=86400)
        return result
