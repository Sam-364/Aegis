"""Stateful anomaly detector: baselines + rule states per (component, metric)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from aegis.detection.baselines import RollingBaseline
from aegis.detection.rules import DEFAULT_RULES, MetricRule
from aegis.domain.incident import AnomalySignal


@dataclass
class RuleState:
    consecutive: int = 0
    normal_streak: int = 0
    firing: bool = False
    fired_at: datetime | None = None
    last_signal_id: str | None = None


@dataclass
class DetectorConfig:
    alpha: float = 0.05
    warmup: int = 12
    zscore_threshold: float = 3.0
    min_consecutive: int | None = None  # override rule defaults when set
    max_freeze_seconds: float = 1200.0


@dataclass
class Observation:
    component: str
    metric: str
    value: float
    at: datetime
    zscore: float
    ratio: float
    baseline: float | None


@dataclass
class AnomalyDetector:
    config: DetectorConfig = field(default_factory=DetectorConfig)
    rules: tuple[MetricRule, ...] = DEFAULT_RULES
    component_kinds: dict[str, str] = field(default_factory=dict)
    baselines: dict[tuple[str, str], RollingBaseline] = field(default_factory=dict)
    states: dict[tuple[str, str, str], RuleState] = field(default_factory=dict)
    frozen_since: dict[tuple[str, str], datetime] = field(default_factory=dict)

    def baseline_for(self, component: str, metric: str) -> RollingBaseline:
        key = (component, metric)
        if key not in self.baselines:
            self.baselines[key] = RollingBaseline(
                alpha=self.config.alpha, warmup=self.config.warmup
            )
        return self.baselines[key]

    def rules_for(self, component: str, metric: str) -> list[MetricRule]:
        kind = self.component_kinds.get(component, "service")
        return [r for r in self.rules if r.metric == metric and kind in r.component_kinds]

    def observe(
        self, component: str, metric: str, value: float, at: datetime, *, emit: bool = True
    ) -> tuple[list[AnomalySignal], list[str], Observation]:
        """Feed one sample. Returns (new signals, cleared rule names, observation).

        ``emit=False`` means this sample is history being replayed to learn a baseline, not a live
        observation: no signal is raised and the baseline is never frozen.
        """
        baseline = self.baseline_for(component, metric)
        rules = self.rules_for(component, metric)
        mean_before = baseline.mean
        z = baseline.zscore(value) if baseline.warm else 0.0
        ratio = baseline.ratio(value) if baseline.warm else 1.0
        signals: list[AnomalySignal] = []
        cleared: list[str] = []
        any_firing = False
        for rule in rules:
            state = self.states.setdefault((component, metric, rule.name), RuleState())
            triggered = rule.absolute_trigger(value) or (
                baseline.warm
                and mean_before is not None
                and rule.statistical_trigger(value, z, ratio, mean_before)
            )
            needed = self.config.min_consecutive or rule.min_consecutive
            if triggered:
                state.consecutive += 1
                state.normal_streak = 0
                if not state.firing and state.consecutive >= needed:
                    state.firing = True
                    state.fired_at = at
                    if emit:
                        signal = AnomalySignal(
                            service=component,
                            metric=metric,
                            kind=rule.kind,
                            observed_value=value,
                            baseline_value=mean_before if mean_before is not None else 0.0,
                            deviation_sigma=z,
                            detector=rule.name,
                            detected_at=at,
                            window_seconds=needed,
                            description=self._describe(
                                rule=rule,
                                component=component,
                                metric=metric,
                                value=value,
                                mean=mean_before,
                                ratio=ratio,
                            ),
                        )
                        state.last_signal_id = str(signal.id)
                        signals.append(signal)
            else:
                state.consecutive = 0
                if state.firing:
                    state.normal_streak += 1
                    if state.normal_streak >= rule.clear_after:
                        state.firing = False
                        state.fired_at = None
                        cleared.append(rule.name)
            # Freeze while firing *or* while a candidate anomaly is being confirmed, so the
            # baseline never absorbs the values it is about to judge.
            any_firing = any_firing or state.firing or state.consecutive > 0
        # Freezing only applies to live detection. Replaying history (emit=False) must always
        # learn, or a stale baseline would freeze itself on the first replayed sample and
        # never catch up with the world it is supposed to describe.
        self._apply_freeze(component, metric, baseline, any_firing and emit, at)
        baseline.update(value)
        obs = Observation(component, metric, value, at, z, ratio, mean_before)
        return signals, cleared, obs

    def _apply_freeze(
        self, component: str, metric: str, baseline: RollingBaseline, firing: bool, at: datetime
    ) -> None:
        key = (component, metric)
        if firing:
            since = self.frozen_since.setdefault(key, at)
            baseline.frozen = (at - since).total_seconds() < self.config.max_freeze_seconds
        else:
            self.frozen_since.pop(key, None)
            baseline.frozen = False

    @staticmethod
    def _describe(
        *,
        rule: MetricRule,
        component: str,
        metric: str,
        value: float,
        mean: float | None,
        ratio: float,
    ) -> str:
        if mean is None or mean == 0:
            return f"{component} {metric}={value:.3g} ({rule.description})"
        pct = (ratio - 1) * 100
        sign = "+" if pct >= 0 else ""
        return (
            f"{component} {metric} {mean:.3g} -> {value:.3g} ({sign}{pct:.0f}%): {rule.description}"
        )

    def firing(self) -> list[tuple[str, str, str]]:
        return [k for k, s in self.states.items() if s.firing]

    def is_firing(self, component: str, metric: str) -> bool:
        return any(
            s.firing for (c, m, _), s in self.states.items() if c == component and m == metric
        )

    def export(self) -> dict[str, Any]:
        return {
            "baselines": {f"{c}|{m}": b.to_dict() for (c, m), b in self.baselines.items()},
            "component_kinds": dict(self.component_kinds),
        }

    def load(self, data: dict[str, Any]) -> None:
        for key, raw in data.get("baselines", {}).items():
            component, metric = key.split("|", 1)
            self.baselines[(component, metric)] = RollingBaseline.from_dict(raw)
        self.component_kinds.update(data.get("component_kinds", {}))
