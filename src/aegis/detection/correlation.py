"""Group anomaly signals into incidents using time proximity and the dependency graph."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from aegis.domain.enums import Severity, SignalKind
from aegis.domain.incident import AnomalySignal, Incident
from aegis.domain.telemetry import Topology


@dataclass
class OpenIncidentDecision:
    title: str
    summary: str
    severity: Severity
    signals: list[AnomalySignal]
    affected_services: list[str]
    correlation_key: str


@dataclass
class AttachDecision:
    incident: Incident
    signals: list[AnomalySignal]


@dataclass
class CorrelationResult:
    opened: list[OpenIncidentDecision] = field(default_factory=list)
    attached: list[AttachDecision] = field(default_factory=list)


class IncidentCorrelator:
    def __init__(self, *, window_seconds: int = 180, max_attach_age_seconds: int = 3600) -> None:
        self.window = timedelta(seconds=window_seconds)
        self.max_attach_age = timedelta(seconds=max_attach_age_seconds)

    def correlate(
        self,
        signals: list[AnomalySignal],
        open_incidents: list[Incident],
        topology: Topology,
        now: datetime,
    ) -> CorrelationResult:
        result = CorrelationResult()
        if not signals:
            return result
        groups = self._group(signals, topology)
        for group in groups:
            services = sorted({s.service for s in group})
            target = self._find_incident(services, open_incidents, topology, now)
            if target is not None:
                result.attached.append(AttachDecision(incident=target, signals=group))
                continue
            severity = self.severity_for(group, topology)
            title = self.title_for(group, topology)
            summary = "; ".join(
                s.description for s in sorted(group, key=lambda s: -abs(s.deviation_sigma))[:6]
            )
            result.opened.append(
                OpenIncidentDecision(
                    title=title,
                    summary=summary,
                    severity=severity,
                    signals=group,
                    affected_services=services,
                    correlation_key="|".join(services),
                )
            )
        return result

    # --- grouping --------------------------------------------------------------------------------

    def _group(self, signals: list[AnomalySignal], topology: Topology) -> list[list[AnomalySignal]]:
        groups: list[list[AnomalySignal]] = []
        for signal in sorted(signals, key=lambda s: s.detected_at):
            placed = False
            for group in groups:
                if self._related(signal, group, topology):
                    group.append(signal)
                    placed = True
                    break
            if not placed:
                groups.append([signal])
        return groups

    def _related(
        self, signal: AnomalySignal, group: list[AnomalySignal], topology: Topology
    ) -> bool:
        if abs(signal.detected_at - group[0].detected_at) > self.window:
            return False
        services = {s.service for s in group}
        if signal.service in services:
            return True
        return any(self._connected(signal.service, other, topology) for other in services)

    @staticmethod
    def _connected(a: str, b: str, topology: Topology) -> bool:
        if not topology.has_node(a) or not topology.has_node(b):
            return True  # unknown components: be conservative and merge
        return b in topology.downstream_closure(a) or b in topology.upstream_closure(a)

    def _find_incident(
        self, services: list[str], open_incidents: list[Incident], topology: Topology, now: datetime
    ) -> Incident | None:
        candidates = [
            i for i in open_incidents if i.is_active and now - i.detected_at <= self.max_attach_age
        ]
        for incident in sorted(candidates, key=lambda i: i.detected_at, reverse=True):
            related = set(incident.affected_services)
            for s in incident.affected_services:
                if topology.has_node(s):
                    related.update(topology.downstream_closure(s))
                    related.update(topology.upstream_closure(s))
            if any(s in related for s in services):
                return incident
        return None

    # --- severity / titles -----------------------------------------------------------------------

    @staticmethod
    def _gateway(topology: Topology) -> str | None:
        return next((n.name for n in topology.nodes if n.kind == "gateway"), None)

    def severity_for(  # noqa: PLR0911 - the ladder reads best as explicit returns
        self, group: list[AnomalySignal], topology: Topology
    ) -> Severity:
        gateway = self._gateway(topology)
        tiers = {n.name: n.tier for n in topology.nodes}
        kinds = {n.name: n.kind for n in topology.nodes}
        for s in group:
            if s.service == gateway and (
                (s.kind is SignalKind.AVAILABILITY)
                or (s.kind is SignalKind.ERROR_RATE and s.observed_value >= 0.5)
            ):
                return Severity.SEV1
        for s in group:
            if (
                s.service == gateway
                and s.kind is SignalKind.ERROR_RATE
                and s.observed_value >= 0.05
            ):
                return Severity.SEV2
            if s.service == gateway and s.kind is SignalKind.LATENCY and s.magnitude >= 2.0:
                return Severity.SEV2
            if (
                kinds.get(s.service) in ("database", "cache")
                and s.kind is SignalKind.SATURATION
                and (s.metric == "saturation" and s.observed_value >= 0.9)
            ):
                return Severity.SEV2
            if s.kind is SignalKind.AVAILABILITY and tiers.get(s.service, 2) <= 1:
                return Severity.SEV2
        for s in group:
            if s.kind is SignalKind.AVAILABILITY:
                return Severity.SEV3
            if s.kind is SignalKind.ERROR_RATE and s.observed_value >= 0.05:
                return Severity.SEV3
            if s.kind is SignalKind.LATENCY and s.magnitude >= 1.0:
                return Severity.SEV3
            if s.kind is SignalKind.SATURATION:
                return Severity.SEV3
        return Severity.SEV4

    def title_for(self, group: list[AnomalySignal], topology: Topology) -> str:
        gateway = self._gateway(topology)
        by_kind: dict[SignalKind, list[AnomalySignal]] = {}
        for s in group:
            by_kind.setdefault(s.kind, []).append(s)
        gw = [s for s in group if s.service == gateway]
        if gw:
            if any(s.kind is SignalKind.AVAILABILITY for s in gw):
                return "API gateway unavailable"
            if any(s.kind is SignalKind.ERROR_RATE for s in gw):
                return "Elevated API error rate"
            if any(s.kind is SignalKind.LATENCY for s in gw):
                return "API latency regression"
            if any(s.kind is SignalKind.TRAFFIC for s in gw):
                return "Traffic surge on api-gateway"
        ranked = sorted(group, key=lambda s: -abs(s.deviation_sigma))
        lead = ranked[0]
        labels = {
            SignalKind.AVAILABILITY: f"{lead.service} unavailable",
            SignalKind.ERROR_RATE: f"Elevated error rate on {lead.service}",
            SignalKind.LATENCY: f"Latency regression on {lead.service}",
            SignalKind.SATURATION: f"{lead.service} {lead.metric.replace('_', ' ')} saturation",
            SignalKind.RESOURCE: (
                f"{lead.service} {lead.metric.replace('_percent', '').upper()} pressure"
            ),
            SignalKind.TRAFFIC: f"Traffic surge on {lead.service}",
        }
        return labels[lead.kind]
