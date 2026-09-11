"""Discrete-time simulation engine.

Each tick: expire faults → progress lifecycle (restarts, scaling, rollouts) → apply fault effects
→ compute traffic → compute infrastructure load → propagate latency/errors leaves-to-roots →
compute resources → record metrics/logs/traces. Everything is seeded and reproducible.
"""

from __future__ import annotations

import math
import random
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from aegis.simulator.faults import SCENARIOS, ActiveFault, ScenarioSpec, clears_fault, new_fault
from aegis.simulator.model import (
    INFRA_METRICS,
    SERVICE_METRICS,
    ComponentKind,
    ServiceSpec,
    WorldSpec,
    default_world,
)
from aegis.simulator.state import (
    DeploymentRecord,
    InfraState,
    LogRecord,
    ServiceState,
    SpanRecord,
    TraceRecord,
)

HISTORY_SECONDS = 1800


class SimulationError(Exception):
    pass


@dataclass
class Sample:
    at: float
    value: float


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


class SimulationEngine:
    def __init__(
        self,
        world: WorldSpec | None = None,
        *,
        seed: int = 42,
        tick_seconds: float = 1.0,
        start_time: float | None = None,
    ) -> None:
        self.world = world or default_world()
        self.seed = seed
        self.tick_seconds = tick_seconds
        self.rng = random.Random(seed)
        self.time = start_time if start_time is not None else datetime.now(tz=UTC).timestamp()
        self.tick_count = 0
        self.services: dict[str, ServiceState] = {}
        self.infra: dict[str, InfraState] = {}
        self.faults: list[ActiveFault] = []
        self.deployments: list[DeploymentRecord] = []
        self.logs: deque[LogRecord] = deque(maxlen=8000)
        self.traces: deque[TraceRecord] = deque(maxlen=800)
        self.history: dict[tuple[str, str], deque[Sample]] = {}
        self.clean_baseline: dict[tuple[str, str], float] = {}
        self.action_log: list[dict[str, Any]] = []
        self._spike_multiplier = 1.0
        self._order_leaves_first = self.world.topological_order()
        self._base_replicas = {s.name: s.replicas for s in self.world.services}
        self._reset_state()

    # ------------------------------------------------------------------ lifecycle -------------

    def _reset_state(self) -> None:
        self.services = {
            s.name: ServiceState(
                name=s.name,
                version=s.version,
                replicas=s.replicas,
                replicas_ready=float(s.replicas),
                started_at=self.time,
            )
            for s in self.world.services
        }
        self.infra = {
            i.name: InfraState(
                name=i.name, kind=i.kind.value, version=i.version, max_connections=i.max_connections
            )
            for i in self.world.infra
        }
        for s in self.world.services:
            self.deployments.append(
                DeploymentRecord(
                    service=s.name,
                    version=s.version,
                    previous_version=s.previous_version,
                    deployed_at=self.time - 3600 * 24,
                    change_summary="baseline release",
                )
            )
        maxlen = int(HISTORY_SECONDS / self.tick_seconds)
        for name in self.services:
            for m in SERVICE_METRICS:
                self.history[(name, m)] = deque(maxlen=maxlen)
        for name in self.infra:
            for m in INFRA_METRICS:
                self.history[(name, m)] = deque(maxlen=maxlen)

    def reset(self, *, seed: int | None = None) -> None:
        if seed is not None:
            self.seed = seed
        self.rng = random.Random(self.seed)
        self.faults.clear()
        self.deployments.clear()
        self.logs.clear()
        self.traces.clear()
        self.clean_baseline.clear()
        self.action_log.clear()
        self._spike_multiplier = 1.0
        self.tick_count = 0
        self._reset_state()

    def warmup(self, seconds: float) -> None:
        """Fill history so baselines exist before anything is observed."""
        ticks = int(seconds / self.tick_seconds)
        self.time -= ticks * self.tick_seconds
        for _ in range(ticks):
            self.tick()

    @property
    def now(self) -> datetime:
        return datetime.fromtimestamp(self.time, tz=UTC)

    # ------------------------------------------------------------------ tick -------------------

    def tick(self) -> None:
        dt = self.tick_seconds
        self.time += dt
        self.tick_count += 1
        t = self.time
        self._expire_faults(t)
        self._progress_lifecycle(t)
        self._apply_fault_effects(dt)
        self._compute_traffic(t)
        self._compute_infra_load()
        self._propagate()
        self._compute_resources(t)
        self._record(t)
        self._emit_logs(t)
        self._emit_traces(t)

    def advance(self, seconds: float) -> None:
        for _ in range(int(seconds / self.tick_seconds)):
            self.tick()

    # ------------------------------------------------------------------ faults -----------------

    def scenarios(self) -> list[ScenarioSpec]:
        return list(SCENARIOS.values())

    def inject(self, scenario_id: str, params: dict[str, Any] | None = None) -> ActiveFault:
        spec = SCENARIOS.get(scenario_id)
        if spec is None:
            raise SimulationError(f"unknown scenario '{scenario_id}'")
        fault = new_fault(spec, params or {}, self.time)
        self._on_fault_start(fault)
        self.faults.append(fault)
        self.action_log.append(
            {
                "at": self.time,
                "kind": "inject",
                "scenario": scenario_id,
                "fault_id": fault.id,
                "params": fault.params,
            }
        )
        return fault

    def clear_fault(self, fault_id: str, *, by: str = "operator") -> ActiveFault:
        for f in self.faults:
            if f.id == fault_id and f.active:
                self._clear(f, by)
                return f
        raise SimulationError(f"no active fault '{fault_id}'")

    def active_faults(self) -> list[ActiveFault]:
        return [f for f in self.faults if f.active]

    def _clear(self, fault: ActiveFault, by: str) -> None:
        fault.cleared_at = self.time
        fault.cleared_by = by
        p = fault.params
        if fault.fault_type == "service_crash":
            self.services[p["service"]].crashed = False
        if fault.fault_type == "bad_deployment":
            svc = self.services[p["service"]]
            svc.intrinsic_error = 0.0
            svc.intrinsic_latency_ms = 0.0
        if fault.fault_type == "traffic_spike":
            self._spike_multiplier = 1.0

    def _expire_faults(self, t: float) -> None:
        for f in self.faults:
            if f.active and f.expired(t):
                self._clear(f, "expired")

    def _on_fault_start(self, fault: ActiveFault) -> None:
        p = fault.params
        match fault.fault_type:
            case "bad_deployment":
                svc = self.services[p["service"]]
                spec = self.world.service(p["service"])
                fault.state["previous_version"] = svc.version
                self.deployments.append(
                    DeploymentRecord(
                        service=svc.name,
                        version=p["version"],
                        previous_version=svc.version,
                        deployed_at=self.time,
                        change_summary=p.get(
                            "change_summary", "checkout handler refactor; new retry path"
                        ),
                    )
                )
                svc.version = p["version"]
                svc.rolling_until = self.time + 5.0
                self._log(
                    svc.name,
                    "INFO",
                    f"deployment {p['version']} rolled out "
                    f"(previous {fault.state['previous_version']})",
                    {"version": p["version"]},
                )
                _ = spec
            case "service_crash":
                svc = self.services[p["service"]]
                svc.crashed = True
                self._log(
                    svc.name,
                    "ERROR",
                    "process exited with code 137 (OOMKilled)",
                    {"exit_code": "137"},
                )
            case "traffic_spike":
                self._spike_multiplier = float(p.get("multiplier", 3.0))
            case _:
                pass

    def _apply_fault_effects(self, dt: float) -> None:
        for svc in self.services.values():
            svc.intrinsic_error = 0.0
            svc.intrinsic_latency_ms = 0.0
            svc.cpu_pressure = 0.0
            svc.edge_latency_ms = {}
        spike = 1.0
        for f in self.active_faults():
            p = f.params
            match f.fault_type:
                case "redis_connection_leak":
                    svc = self.services[p["service"]]
                    if svc.available:
                        growth = (
                            float(p["rate_per_second"])
                            * dt
                            * (svc.replicas_ready / self._base_replicas[svc.name])
                        )
                        cap = self.infra["redis"].max_connections * 1.3
                        svc.leaked_redis = min(cap, svc.leaked_redis + growth)
                case "db_pool_exhaustion":
                    svc = self.services[p["service"]]
                    if svc.available:
                        growth = (
                            float(p["rate_per_second"])
                            * dt
                            * (svc.replicas_ready / self._base_replicas[svc.name])
                        )
                        cap = self.infra["postgres"].max_connections * 1.1
                        svc.leaked_db = min(cap, svc.leaked_db + growth)
                case "bad_deployment":
                    svc = self.services[p["service"]]
                    if svc.version == p["version"]:
                        svc.intrinsic_error += float(p["error_rate"])
                        svc.intrinsic_latency_ms += float(p["added_latency_ms"])
                case "memory_leak":
                    svc = self.services[p["service"]]
                    if svc.available:
                        svc.memory_leak_mb += float(p["rate_mb_per_second"]) * dt
                case "cpu_saturation":
                    svc = self.services[p["service"]]
                    ratio = self._base_replicas[svc.name] / max(svc.replicas_ready, 0.5)
                    svc.cpu_pressure += float(p["pressure"]) * ratio
                case "traffic_spike":
                    spike = float(p.get("multiplier", 3.0))
                case "network_latency":
                    src = self.services[p["source"]]
                    src.edge_latency_ms[p["target"]] = float(p["added_ms"])
                case _:
                    pass
        self._spike_multiplier = spike

    # ------------------------------------------------------------------ lifecycle progress ----

    def _progress_lifecycle(self, t: float) -> None:
        for spec in self.world.services:
            svc = self.services[spec.name]
            if svc.restarting_until is not None and t >= svc.restarting_until:
                svc.restarting_until = None
                svc.up = True
                svc.replicas_ready = float(svc.replicas)
                svc.started_at = t
                self._log(
                    svc.name,
                    "INFO",
                    f"started {svc.name} version {svc.version}; readiness probe passed",
                    {"version": svc.version},
                )
            if svc.scaling_until is not None:
                if t >= svc.scaling_until:
                    svc.scaling_until = None
                    svc.replicas_ready = float(svc.replicas)
                    self._log(svc.name, "INFO", f"scaled to {svc.replicas} replicas (all ready)")
                else:
                    frac = _clamp(1 - (svc.scaling_until - t) / spec.scale_seconds, 0.0, 1.0)
                    svc.replicas_ready = svc.scale_from + (svc.replicas - svc.scale_from) * frac
            if svc.rolling_until is not None and t >= svc.rolling_until:
                svc.rolling_until = None
        for ispec in self.world.infra:
            comp = self.infra[ispec.name]
            if comp.restarting_until is not None and t >= comp.restarting_until:
                comp.restarting_until = None
                comp.up = True
                self._log(comp.name, "INFO", f"{comp.name} accepting connections")
            if comp.cache_flushed_until is not None:
                if t >= comp.cache_flushed_until:
                    comp.cache_flushed_until = None
                    comp.hit_rate = 0.97
                else:
                    comp.hit_rate = min(0.97, comp.hit_rate + 0.03)

    # ------------------------------------------------------------------ traffic ----------------

    def _compute_traffic(self, t: float) -> None:
        for svc in self.services.values():
            svc.request_rate = 0.0
        gateway = next(s for s in self.world.services if s.kind is ComponentKind.GATEWAY)
        diurnal = 1 + 0.04 * math.sin(2 * math.pi * t / 600.0)
        noise = 1 + self.rng.gauss(0, 0.02)
        self.services[gateway.name].request_rate = (
            self.world.entry_rps * diurnal * self._spike_multiplier * noise
        )
        for spec in reversed(self._order_leaves_first):  # roots first
            src = self.services[spec.name]
            if not src.available:
                continue
            for d in spec.deps:
                if d.target in self.services:
                    self.services[d.target].request_rate += src.request_rate * d.calls_per_request

    # ------------------------------------------------------------------ infra ------------------

    def _compute_infra_load(self) -> None:
        for ispec in self.world.infra:
            comp = self.infra[ispec.name]
            ops = 0.0
            by_client: dict[str, float] = {}
            idle_in_txn = 0.0
            for spec in self.world.services:
                d = spec.dep(ispec.name)
                if d is None:
                    continue
                svc = self.services[spec.name]
                pool_size = (
                    spec.db_pool_size
                    if ispec.kind is ComponentKind.DATABASE
                    else spec.redis_pool_size
                )
                if not svc.available:
                    by_client[spec.name] = 0.0
                    continue
                calls = svc.request_rate * d.calls_per_request
                ops += calls
                idle_floor = max(1.0, svc.replicas_ready)
                demand = calls * (max(comp.latency_p95_ms, ispec.base_latency_ms) / 1000.0) * 1.5
                pool_cap = pool_size * max(svc.replicas_ready, 0.5)
                in_use = min(pool_cap, max(idle_floor, demand))
                leaked = svc.leaked_db if ispec.kind is ComponentKind.DATABASE else svc.leaked_redis
                by_client[spec.name] = in_use + leaked
                if ispec.kind is ComponentKind.DATABASE:
                    idle_in_txn += leaked
                    svc.db_pool_in_use = in_use
                else:
                    svc.redis_pool_in_use = in_use
            comp.connections_by_client = by_client
            comp.connections = min(sum(by_client.values()), comp.max_connections * 1.02)
            comp.saturation = comp.connections / comp.max_connections
            comp.ops_per_sec = ops
            comp.idle_in_transaction = idle_in_txn
            if not comp.available:
                comp.latency_p95_ms = 0.0
                comp.blocked_clients = 0.0
                continue
            sat = comp.saturation
            comp.latency_p95_ms = ispec.base_latency_ms * (
                1 + 3 * sat**3 + (20 * (sat - 0.9) if sat > 0.9 else 0.0)
            )
            comp.blocked_clients = (
                max(0.0, (sat - 0.95) * comp.max_connections) if sat > 0.95 else 0
            )
            comp.lock_waits = max(0.0, idle_in_txn * 0.15)
            comp.memory_percent = _clamp(30 + 40 * sat + self.rng.gauss(0, 0.5), 0, 100)

    def _pool_wait(self, spec: ServiceSpec, svc: ServiceState, target: str) -> tuple[float, float]:
        """Return (wait_ms, error_fraction) for svc's pool towards an infra component."""
        ispec = self.world.infra_component(target)
        comp = self.infra[target]
        d = spec.dep(target)
        assert d is not None
        if not comp.available:
            return 5.0, 1.0
        pool_size = (
            spec.db_pool_size if ispec.kind is ComponentKind.DATABASE else spec.redis_pool_size
        )
        pool_cap = pool_size * max(svc.replicas_ready, 0.5)
        leaked = svc.leaked_db if ispec.kind is ComponentKind.DATABASE else svc.leaked_redis
        calls = svc.request_rate * d.calls_per_request
        demand = calls * (comp.latency_p95_ms / 1000.0) * 1.5 + max(1.0, svc.replicas_ready)
        _ = leaked  # leaked connections are opened outside the pool; they hurt via saturation
        pressure = demand / max(pool_cap, 1.0)
        wait = 0.0
        if pressure > 0.8:
            wait = ((pressure - 0.8) / 0.2) ** 2 * d.timeout_ms
        if comp.saturation > 0.85:
            wait += ((comp.saturation - 0.85) / 0.15) ** 2 * d.timeout_ms * 0.5
        if comp.saturation >= 0.98:
            wait += ((comp.saturation - 0.98) / 0.04) * d.timeout_ms
        wait = min(wait, d.timeout_ms)
        err = _clamp((wait - 0.5 * d.timeout_ms) / (0.5 * d.timeout_ms), 0.0, 1.0)
        return wait, err

    # ------------------------------------------------------------------ propagation -----------

    def _propagate(self) -> None:
        for spec in self._order_leaves_first:
            svc = self.services[spec.name]
            svc.dep_error_detail = {}
            if not svc.available:
                svc.error_rate = 1.0
                svc.own_error_rate = 1.0
                svc.latency_p50_ms = 0.0
                svc.latency_p95_ms = 0.0
                svc.own_latency_ms = 0.0
                svc.db_pool_wait_ms = 0.0
                svc.redis_pool_wait_ms = 0.0
                continue
            capacity = spec.capacity_rps_per_replica * max(svc.replicas_ready, 0.5)
            load = svc.request_rate / capacity
            load_mult = 1 + 0.5 * load**2 if load < 1 else 1.5 + (load - 1) * 4
            mem_mult = 1 + ((svc.memory_percent - 85) / 15) * 3 if svc.memory_percent > 85 else 1.0
            cpu_total = 12 + 55 * min(load, 2) + svc.cpu_pressure
            cpu_mult = 1 + ((cpu_total - 85) / 15) * 4 if cpu_total > 85 else 1.0
            rolling = 1.3 if svc.rolling_until is not None else 1.0
            own = (
                spec.base_latency_ms * load_mult * mem_mult * cpu_mult * rolling
                + svc.intrinsic_latency_ms
            )
            own_err = svc.intrinsic_error
            if load > 1.2:
                own_err += (load - 1.2) * 0.3
            if svc.memory_percent > 97:
                own_err += 0.3
            own_err = _clamp(own_err, 0.0, 1.0)
            p50 = own
            p95 = own * 1.7
            survive = 1 - own_err
            svc.db_pool_wait_ms = 0.0
            svc.redis_pool_wait_ms = 0.0
            for d in spec.deps:
                if self.world.is_infra(d.target):
                    wait, pool_err = self._pool_wait(spec, svc, d.target)
                    comp = self.infra[d.target]
                    ispec = self.world.infra_component(d.target)
                    if ispec.kind is ComponentKind.DATABASE:
                        svc.db_pool_wait_ms = wait
                    else:
                        svc.redis_pool_wait_ms = wait
                    lat50 = (comp.latency_p95_ms * 0.6 if comp.available else 5.0) + wait
                    lat95 = (comp.latency_p95_ms if comp.available else 5.0) + wait
                    dep_err = pool_err
                else:
                    dep = self.services[d.target]
                    edge = svc.edge_latency_ms.get(d.target, 0.0)
                    if dep.available:
                        lat50 = dep.latency_p50_ms + edge
                        lat95 = dep.latency_p95_ms + edge
                        dep_err = dep.error_rate * d.sensitivity
                    else:
                        lat50 = lat95 = 5.0
                        dep_err = 1.0 * d.sensitivity
                timeout_err = _clamp((lat50 - 0.7 * d.timeout_ms) / (0.6 * d.timeout_ms), 0.0, 1.0)
                if not d.critical:
                    timeout_err *= d.sensitivity
                share = min(1.0, d.calls_per_request)
                contrib = _clamp((dep_err + timeout_err), 0.0, 1.0) * share
                svc.dep_error_detail[d.target] = contrib
                survive *= 1 - contrib
                p50 += d.calls_per_request * min(lat50, d.timeout_ms)
                if d.calls_per_request >= 0.05:
                    weight = (
                        max(1.0, d.calls_per_request)
                        if d.calls_per_request >= 1
                        else d.calls_per_request**0.5
                    )
                    p95 += weight * min(lat95, d.timeout_ms)
            svc.own_latency_ms = own
            svc.own_error_rate = own_err
            svc.latency_p50_ms = p50 * (1 + self.rng.gauss(0, 0.03))
            svc.latency_p95_ms = max(svc.latency_p50_ms, p95 * (1 + self.rng.gauss(0, 0.04)))
            svc.error_rate = _clamp(1 - survive + abs(self.rng.gauss(0, 0.0015)), 0.0, 1.0)

    def _compute_resources(self, t: float) -> None:
        for spec in self.world.services:
            svc = self.services[spec.name]
            if not svc.available:
                svc.cpu_percent = 0.0
                svc.memory_percent = 0.0
                continue
            capacity = spec.capacity_rps_per_replica * max(svc.replicas_ready, 0.5)
            load = svc.request_rate / capacity
            svc.cpu_percent = _clamp(
                12 + 55 * min(load, 2) + svc.cpu_pressure + self.rng.gauss(0, 1.5), 0, 100
            )
            svc.memory_percent = _clamp(
                35
                + 10 * min(load, 1.5)
                + svc.memory_leak_mb / spec.memory_limit_mb * 100
                + self.rng.gauss(0, 0.5),
                0,
                100,
            )
            if svc.memory_percent >= 99.0:
                self._crash_restart(spec, svc, t, "OOMKilled: heap limit exceeded")

    def _crash_restart(self, spec: ServiceSpec, svc: ServiceState, t: float, why: str) -> None:
        svc.up = False
        svc.restarting_until = t + spec.restart_seconds
        svc.memory_leak_mb = 0.0
        svc.leaked_db = 0.0
        svc.leaked_redis = 0.0
        svc.restart_count += 1
        svc.last_restart_at = t
        self._log(svc.name, "ERROR", f"process exited with code 137 ({why})", {"exit_code": "137"})

    # ------------------------------------------------------------------ recording -------------

    def _is_clean(self) -> bool:
        if self.active_faults():
            return False
        return all(
            s.restarting_until is None
            and s.scaling_until is None
            and s.rolling_until is None
            and not s.crashed
            for s in self.services.values()
        ) and all(c.available and c.cache_flushed_until is None for c in self.infra.values())

    def _record(self, t: float) -> None:
        clean = self._is_clean()
        for name, svc in self.services.items():
            for metric, value in svc.metrics().items():
                self.history[(name, metric)].append(Sample(t, value))
                if clean:
                    self._update_baseline(name, metric, value)
        for name, comp in self.infra.items():
            for metric, value in comp.metrics().items():
                self.history[(name, metric)].append(Sample(t, value))
                if clean:
                    self._update_baseline(name, metric, value)

    def _update_baseline(self, name: str, metric: str, value: float) -> None:
        key = (name, metric)
        prev = self.clean_baseline.get(key)
        self.clean_baseline[key] = value if prev is None else prev + 0.05 * (value - prev)

    def _log(
        self, service: str, level: str, message: str, attributes: dict[str, str] | None = None
    ) -> None:
        self.logs.append(
            LogRecord(
                at=self.time,
                service=service,
                level=level,
                message=message,
                attributes=attributes or {},
            )
        )

    def _emit_logs(self, t: float) -> None:
        r = self.rng
        for spec in self.world.services:
            svc = self.services[spec.name]
            if svc.restarting_until is not None:
                if r.random() < 0.3:
                    self._log(svc.name, "INFO", f"starting {svc.name} version {svc.version}")
                continue
            if svc.crashed:
                continue
            if r.random() < 0.25:
                ms = max(1, int(svc.latency_p50_ms * r.uniform(0.6, 1.6)))
                path = r.choice(
                    [
                        "/orders",
                        "/users/me",
                        "/checkout",
                        "/inventory/sku",
                        "/auth/token",
                        "/health",
                    ]
                )
                status = 500 if r.random() < svc.error_rate else 200
                self._log(
                    svc.name,
                    "INFO" if status == 200 else "ERROR",
                    f"GET {path} {status} in {ms}ms",
                    {"path": path, "status": str(status)},
                )
            if svc.redis_pool_wait_ms > 150 and r.random() < 0.8:
                self._log(
                    svc.name,
                    "WARN" if svc.redis_pool_wait_ms < 400 else "ERROR",
                    f"redis pool exhausted: waited {int(svc.redis_pool_wait_ms)}ms for a "
                    f"connection (in_use={svc.redis_pool_in_use:.0f}, "
                    f"pool_size={spec.redis_pool_size * svc.replicas:.0f})",
                    {"dependency": "redis"},
                )
            if svc.db_pool_wait_ms > 200 and r.random() < 0.8:
                self._log(
                    svc.name,
                    "WARN" if svc.db_pool_wait_ms < 800 else "ERROR",
                    f"could not obtain a database connection within "
                    f"{int(svc.db_pool_wait_ms)}ms (pool in_use={svc.db_pool_in_use:.0f})",
                    {"dependency": "postgres"},
                )
            if svc.leaked_redis > 20 and r.random() < 0.2:
                self._log(
                    svc.name,
                    "WARN",
                    f"redis client count for this process is unusually "
                    f"high ({svc.leaked_redis + svc.redis_pool_in_use:.0f})",
                    {"dependency": "redis"},
                )
            if svc.leaked_db > 10 and r.random() < 0.2:
                self._log(
                    svc.name,
                    "WARN",
                    "transaction left open for more than 30s (idle in transaction)",
                    {"dependency": "postgres"},
                )
            if svc.intrinsic_error > 0 and r.random() < 0.7:
                self._log(
                    svc.name,
                    "ERROR",
                    f"NullPointerException in CheckoutHandler.process (version {svc.version})",
                    {"version": svc.version, "handler": "CheckoutHandler"},
                )
            if svc.memory_percent > 85 and r.random() < 0.5:
                pause = int((svc.memory_percent - 80) * 12 * r.uniform(0.7, 1.3))
                self._log(svc.name, "WARN", f"GC pause {pause}ms (heap {svc.memory_percent:.0f}%)")
            if svc.cpu_percent > 88 and r.random() < 0.5:
                self._log(
                    svc.name,
                    "WARN",
                    f"event loop lag {int((svc.cpu_percent - 80) * 25)}ms "
                    f"(cpu {svc.cpu_percent:.0f}%)",
                )
            for target, contrib in svc.dep_error_detail.items():
                if contrib > 0.05 and r.random() < 0.6:
                    dep_available = (
                        self.services[target].available
                        if target in self.services
                        else self.infra[target].available
                    )
                    if not dep_available:
                        self._log(
                            svc.name,
                            "ERROR",
                            f"connect ECONNREFUSED {target}:8080",
                            {"dependency": target},
                        )
                    else:
                        self._log(
                            svc.name,
                            "ERROR",
                            f"upstream {target} failed or timed out "
                            f"({contrib * 100:.0f}% of calls)",
                            {"dependency": target},
                        )
        for ispec in self.world.infra:
            comp = self.infra[ispec.name]
            if not comp.available:
                continue
            if comp.saturation >= 0.98 and r.random() < 0.8:
                msg = (
                    "ERR max number of clients reached"
                    if ispec.kind is ComponentKind.CACHE
                    else "FATAL: remaining connection slots are reserved for non-replication "
                    "superuser connections"
                )
                self._log(comp.name, "ERROR", msg, {"connections": f"{comp.connections:.0f}"})
            elif comp.saturation >= 0.85 and r.random() < 0.3:
                self._log(
                    comp.name,
                    "WARN",
                    f"client connections at {comp.saturation * 100:.0f}% "
                    f"of max ({comp.connections:.0f}/{comp.max_connections})",
                )

    def _emit_traces(self, t: float) -> None:
        gateway = next(s for s in self.world.services if s.kind is ComponentKind.GATEWAY)
        for _ in range(2):
            spans: list[SpanRecord] = []
            root_dur, root_err = self._build_span(gateway, spans, depth=0)
            self.traces.append(
                TraceRecord(
                    trace_id=uuid.UUID(int=self.rng.getrandbits(128)).hex,
                    at=t,
                    root_service=gateway.name,
                    duration_ms=root_dur,
                    error=root_err,
                    spans=spans,
                )
            )

    def _build_span(
        self, spec: ServiceSpec, spans: list[SpanRecord], depth: int
    ) -> tuple[float, bool]:
        svc = self.services[spec.name]
        r = self.rng
        if not svc.available:
            spans.append(SpanRecord(spec.name, "request", 5.0, True, depth))
            return 5.0, True
        duration = svc.own_latency_ms * r.lognormvariate(0, 0.25)
        error = r.random() < svc.own_error_rate
        for d in spec.deps:
            if r.random() >= min(1.0, d.calls_per_request):
                continue
            if self.world.is_infra(d.target):
                comp = self.infra[d.target]
                wait = svc.db_pool_wait_ms if comp.kind == "database" else svc.redis_pool_wait_ms
                dep_dur = min(d.timeout_ms, comp.latency_p95_ms * r.uniform(0.4, 1.1) + wait)
                dep_err = (not comp.available) or wait >= d.timeout_ms * 0.5
                spans.append(
                    SpanRecord(
                        d.target,
                        "query" if comp.kind == "database" else "cmd",
                        dep_dur,
                        dep_err,
                        depth + 1,
                    )
                )
            else:
                dep_dur, dep_err = self._build_span(self.world.service(d.target), spans, depth + 1)
                dep_dur = min(d.timeout_ms, dep_dur + svc.edge_latency_ms.get(d.target, 0.0))
            duration += dep_dur
            if dep_err and d.critical:
                error = True
        spans.insert(
            0, SpanRecord(spec.name, "request" if depth == 0 else "call", duration, error, depth)
        )
        return duration, error

    # ------------------------------------------------------------------ remediation ----------

    def _record_action(
        self, action: str, target: str, extra: dict[str, Any], reason: str, result: dict[str, Any]
    ) -> dict[str, Any]:
        cleared = [f.id for f in self.active_faults() if clears_fault(f, action, target, extra)]
        for f in self.active_faults():
            if f.id in cleared:
                self._clear(f, f"{action}:{target}")
        entry = {
            "at": self.time,
            "kind": "action",
            "action": action,
            "target": target,
            "extra": extra,
            "reason": reason,
            "cleared_faults": cleared,
            **result,
        }
        self.action_log.append(entry)
        return {**result, "at": self.now.isoformat(), "action": action, "target": target}

    def restart(self, target: str, *, reason: str = "") -> dict[str, Any]:
        if target in self.services:
            spec = self.world.service(target)
            svc = self.services[target]
            svc.up = False
            svc.crashed = False
            svc.restarting_until = self.time + spec.restart_seconds
            svc.replicas_ready = 0.0
            svc.leaked_db = 0.0
            svc.leaked_redis = 0.0
            svc.memory_leak_mb = 0.0
            svc.restart_count += 1
            svc.last_restart_at = self.time
            self._log(target, "INFO", f"restart requested: {reason or 'operator'}")
            return self._record_action(
                "restart",
                target,
                {},
                reason,
                {
                    "status": "restarting",
                    "eta_seconds": spec.restart_seconds,
                    "version": svc.version,
                },
            )
        if target in self.infra:
            ispec = self.world.infra_component(target)
            comp = self.infra[target]
            comp.up = False
            comp.restarting_until = self.time + ispec.restart_seconds
            comp.restart_count += 1
            for svc in self.services.values():
                if ispec.kind is ComponentKind.CACHE:
                    svc.leaked_redis = 0.0
                else:
                    svc.leaked_db = 0.0
            self._log(target, "WARN", f"restart requested: {reason or 'operator'}")
            return self._record_action(
                "restart",
                target,
                {},
                reason,
                {"status": "restarting", "eta_seconds": ispec.restart_seconds},
            )
        raise SimulationError(f"unknown component '{target}'")

    def rollback(
        self, service: str, *, to_version: str | None = None, reason: str = ""
    ) -> dict[str, Any]:
        if service not in self.services:
            raise SimulationError(f"unknown service '{service}'")
        svc = self.services[service]
        history = [d for d in self.deployments if d.service == service]
        current = history[-1] if history else None
        target_version = to_version or (current.previous_version if current else None)
        if target_version is None:
            raise SimulationError(f"no previous version available for '{service}'")
        if target_version == svc.version:
            return self._record_action(
                "rollback",
                service,
                {"to_version": target_version},
                reason,
                {"status": "noop", "version": svc.version},
            )
        previous = svc.version
        svc.version = target_version
        svc.rolling_until = self.time + 15.0
        self.deployments.append(
            DeploymentRecord(
                service=service,
                version=target_version,
                previous_version=previous,
                deployed_at=self.time,
                change_summary=f"rollback from {previous}",
                kind="rollback",
            )
        )
        self._log(
            service,
            "INFO",
            f"rolling back {previous} -> {target_version}: {reason or 'operator'}",
            {"version": target_version},
        )
        return self._record_action(
            "rollback",
            service,
            {"to_version": target_version},
            reason,
            {
                "status": "rolling_back",
                "from_version": previous,
                "to_version": target_version,
                "eta_seconds": 15.0,
            },
        )

    def scale(self, service: str, *, replicas: int, reason: str = "") -> dict[str, Any]:
        if service not in self.services:
            raise SimulationError(f"unknown service '{service}'")
        if not 1 <= replicas <= 10:
            raise SimulationError("replicas must be between 1 and 10")
        spec = self.world.service(service)
        svc = self.services[service]
        previous = svc.replicas
        svc.replicas = replicas
        if replicas > previous:
            svc.scale_from = svc.replicas_ready
            svc.scaling_until = self.time + spec.scale_seconds
        else:
            svc.replicas_ready = float(replicas)
        self._log(
            service, "INFO", f"scaling {previous} -> {replicas} replicas: {reason or 'operator'}"
        )
        return self._record_action(
            "scale",
            service,
            {"replicas": replicas},
            reason,
            {
                "status": "scaling",
                "from_replicas": previous,
                "to_replicas": replicas,
                "eta_seconds": spec.scale_seconds if replicas > previous else 0.0,
            },
        )

    def clear_cache(self, component: str, *, reason: str = "") -> dict[str, Any]:
        if component not in self.infra or self.infra[component].kind != "cache":
            raise SimulationError(f"'{component}' is not a cache")
        comp = self.infra[component]
        comp.hit_rate = 0.3
        comp.cache_flushed_until = self.time + 25.0
        self._log(component, "WARN", f"FLUSHALL executed: {reason or 'operator'}")
        return self._record_action(
            "clear_cache", component, {}, reason, {"status": "flushed", "hit_rate": comp.hit_rate}
        )

    def rotate_pool(self, service: str, *, target: str, reason: str = "") -> dict[str, Any]:
        if service not in self.services:
            raise SimulationError(f"unknown service '{service}'")
        if target not in self.infra:
            raise SimulationError(f"unknown infrastructure component '{target}'")
        svc = self.services[service]
        spec = self.world.service(service)
        if spec.dep(target) is None:
            raise SimulationError(f"'{service}' does not use '{target}'")
        released = svc.leaked_db if self.infra[target].kind == "database" else svc.leaked_redis
        if self.infra[target].kind == "database":
            svc.leaked_db = 0.0
        else:
            svc.leaked_redis = 0.0
        svc.rolling_until = self.time + 3.0
        self._log(
            service,
            "INFO",
            f"connection pool to {target} rotated; released "
            f"{released:.0f} connections: {reason or 'operator'}",
        )
        return self._record_action(
            "rotate_pool",
            service,
            {"target": target},
            reason,
            {"status": "rotated", "released_connections": round(released)},
        )

    # ------------------------------------------------------------------ diagnostics ----------

    def _require_service(self, name: str) -> ServiceState:
        svc = self.services.get(name)
        if svc is None:
            raise SimulationError(f"unknown service '{name}'")
        return svc

    def diagnostic(
        self, kind: str, target: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        params = params or {}
        at = self.now.isoformat()
        match kind:
            case "connectivity":
                source = params.get("source")
                if source not in self.services:
                    raise SimulationError("connectivity requires a valid 'source' service")
                spec = self.world.service(source)
                d = spec.dep(target)
                if d is None:
                    return {
                        "at": at,
                        "source": source,
                        "target": target,
                        "dependency": False,
                        "reachable": target in self.services or target in self.infra,
                    }
                available = (
                    self.services[target].available
                    if target in self.services
                    else self.infra[target].available
                )
                svc = self.services[source]
                if target in self.services:
                    lat = self.services[target].latency_p50_ms + svc.edge_latency_ms.get(target, 0)
                else:
                    lat = self.infra[target].latency_p95_ms + (
                        svc.db_pool_wait_ms
                        if self.infra[target].kind == "database"
                        else svc.redis_pool_wait_ms
                    )
                return {
                    "at": at,
                    "source": source,
                    "target": target,
                    "dependency": True,
                    "reachable": available,
                    "latency_ms": round(lat, 1),
                    "timeout_ms": d.timeout_ms,
                    "timing_out": lat >= d.timeout_ms * 0.7,
                    "error_contribution": round(svc.dep_error_detail.get(target, 0.0), 3),
                    "added_network_latency_ms": svc.edge_latency_ms.get(target, 0.0),
                }
            case "database":
                comp = self.infra.get(target)
                if comp is None or comp.kind != "database":
                    raise SimulationError(f"'{target}' is not a database")
                clients = sorted(comp.connections_by_client.items(), key=lambda kv: -kv[1])
                return {
                    "at": at,
                    "component": target,
                    "available": comp.available,
                    "connections": round(comp.connections),
                    "max_connections": comp.max_connections,
                    "saturation": round(comp.saturation, 3),
                    "idle_in_transaction": round(comp.idle_in_transaction),
                    "lock_waits": round(comp.lock_waits, 1),
                    "latency_p95_ms": round(comp.latency_p95_ms, 2),
                    "connections_by_client": {k: round(v) for k, v in clients},
                    "idle_in_transaction_by_client": {
                        s.name: round(s.leaked_db)
                        for s in self.services.values()
                        if s.leaked_db > 0
                    },
                    "top_client": clients[0][0] if clients else None,
                }
            case "cache":
                comp = self.infra.get(target)
                if comp is None or comp.kind != "cache":
                    raise SimulationError(f"'{target}' is not a cache")
                clients = sorted(comp.connections_by_client.items(), key=lambda kv: -kv[1])
                return {
                    "at": at,
                    "component": target,
                    "available": comp.available,
                    "connected_clients": round(comp.connections),
                    "max_clients": comp.max_connections,
                    "saturation": round(comp.saturation, 3),
                    "blocked_clients": round(comp.blocked_clients),
                    "hit_rate": round(comp.hit_rate, 3),
                    "ops_per_sec": round(comp.ops_per_sec),
                    "latency_p95_ms": round(comp.latency_p95_ms, 2),
                    "clients_by_service": {k: round(v) for k, v in clients},
                    "leaked_by_service": {
                        s.name: round(s.leaked_redis)
                        for s in self.services.values()
                        if s.leaked_redis > 0
                    },
                    "top_client": clients[0][0] if clients else None,
                }
            case "process":
                svc = self._require_service(target)
                return {
                    "at": at,
                    "service": target,
                    "up": svc.available,
                    "crashed": svc.crashed,
                    "restarting": svc.restarting_until is not None,
                    "uptime_seconds": round(max(0.0, self.time - svc.started_at)),
                    "version": svc.version,
                    "replicas": svc.replicas,
                    "replicas_ready": round(svc.replicas_ready, 1),
                    "memory_percent": round(svc.memory_percent, 1),
                    "cpu_percent": round(svc.cpu_percent, 1),
                    "restart_count": svc.restart_count,
                    "open_redis_connections": round(svc.redis_pool_in_use + svc.leaked_redis),
                    "open_db_connections": round(svc.db_pool_in_use + svc.leaked_db),
                }
            case "dependencies":
                svc = self._require_service(target)
                spec = self.world.service(target)
                deps: list[dict[str, Any]] = []
                for d in spec.deps:
                    available = (
                        self.services[d.target].available
                        if d.target in self.services
                        else self.infra[d.target].available
                    )
                    deps.append(
                        {
                            "target": d.target,
                            "critical": d.critical,
                            "available": available,
                            "error_contribution": round(svc.dep_error_detail.get(d.target, 0.0), 3),
                            "calls_per_request": d.calls_per_request,
                            "timeout_ms": d.timeout_ms,
                        }
                    )
                deps.sort(key=lambda x: -float(str(x["error_contribution"])))
                return {
                    "at": at,
                    "service": target,
                    "own_error_rate": round(svc.own_error_rate, 4),
                    "own_latency_ms": round(svc.own_latency_ms, 1),
                    "dependencies": deps,
                }
            case "reproduce":
                svc = self._require_service(target)
                failed = self.rng.random() < svc.error_rate
                worst = max(svc.dep_error_detail.items(), key=lambda kv: kv[1], default=(None, 0.0))
                return {
                    "at": at,
                    "service": target,
                    "endpoint": params.get("endpoint", "/"),
                    "status_code": 500 if failed else 200,
                    "latency_ms": round(svc.latency_p95_ms if failed else svc.latency_p50_ms, 1),
                    "error": (
                        f"upstream {worst[0]} failed"
                        if failed and worst[0] and worst[1] > 0.05
                        else ("internal error" if failed else None)
                    ),
                    "version": svc.version,
                }
            case "load_projection":
                svc = self._require_service(target)
                spec = self.world.service(target)
                factor = float(params.get("factor", 1.5))
                capacity = spec.capacity_rps_per_replica * max(svc.replicas_ready, 0.5)
                load = svc.request_rate * factor / capacity
                mult = 1 + 0.5 * load**2 if load < 1 else 1.5 + (load - 1) * 4
                return {
                    "at": at,
                    "service": target,
                    "factor": factor,
                    "projected_load": round(load, 2),
                    "projected_latency_p95_ms": round(
                        svc.latency_p95_ms
                        * mult
                        / max(1e-6, (1 + 0.5 * (svc.request_rate / capacity) ** 2)),
                        1,
                    ),
                    "headroom_replicas": math.ceil(
                        svc.request_rate * factor / spec.capacity_rps_per_replica
                    ),
                }
            case _:
                raise SimulationError(f"unknown diagnostic '{kind}'")

    # ------------------------------------------------------------------ reads ------------------

    def topology(self) -> dict[str, Any]:
        nodes = [
            {
                "name": s.name,
                "kind": s.kind.value,
                "tier": s.tier,
                "owner": s.owner,
                "replicas": self.services[s.name].replicas,
                "version": self.services[s.name].version,
            }
            for s in self.world.services
        ]
        nodes += [
            {
                "name": i.name,
                "kind": i.kind.value,
                "tier": 3,
                "owner": "platform",
                "replicas": 1,
                "version": i.version,
            }
            for i in self.world.infra
        ]
        edges = [
            {
                "source": s.name,
                "target": d.target,
                "protocol": ("tcp" if self.world.is_infra(d.target) else "http"),
                "critical": d.critical,
            }
            for s in self.world.services
            for d in s.deps
        ]
        return {"nodes": nodes, "edges": edges}

    def component_names(self) -> list[str]:
        return self.world.names()

    def metric_names(self) -> list[str]:
        return sorted(set(SERVICE_METRICS) | set(INFRA_METRICS))

    def series(
        self, component: str, metric: str, *, start: float | None = None, end: float | None = None
    ) -> list[Sample]:
        key = (component, metric)
        if key not in self.history:
            raise SimulationError(f"unknown metric {component}/{metric}")
        lo = start if start is not None else -math.inf
        hi = end if end is not None else math.inf
        return [s for s in self.history[key] if lo <= s.at <= hi]

    def snapshot(self, window_seconds: int) -> dict[str, dict[str, list[Sample]]]:
        start = self.time - window_seconds
        out: dict[str, dict[str, list[Sample]]] = {}
        for (component, metric), samples in self.history.items():
            out.setdefault(component, {})[metric] = [s for s in samples if s.at >= start]
        return out

    def current_metrics(self, component: str) -> dict[str, float]:
        if component in self.services:
            return self.services[component].metrics()
        if component in self.infra:
            return self.infra[component].metrics()
        raise SimulationError(f"unknown component '{component}'")

    def baseline(self, component: str, metric: str) -> float | None:
        return self.clean_baseline.get((component, metric))

    def health(self, component: str) -> dict[str, Any]:
        if component in self.services:
            svc = self.services[component]
            base_p95 = self.clean_baseline.get((component, "latency_p95_ms")) or 1.0
            checks = {
                "process_up": svc.available,
                "error_rate_ok": svc.error_rate < 0.05,
                "latency_ok": svc.latency_p95_ms < 3 * base_p95,
                "dependencies_ok": all(v < 0.05 for v in svc.dep_error_detail.values()),
                "memory_ok": svc.memory_percent < 90,
                "cpu_ok": svc.cpu_percent < 90,
                "pools_ok": svc.db_pool_wait_ms < 200 and svc.redis_pool_wait_ms < 200,
            }
            if not svc.available or svc.error_rate > 0.5:
                state = "unhealthy"
            elif not all(checks.values()):
                state = "degraded"
            else:
                state = "healthy"
            failing = [k for k, v in checks.items() if not v]
            return {
                "service": component,
                "state": state,
                "checks": checks,
                "message": (
                    "all checks passing" if not failing else "failing: " + ", ".join(failing)
                ),
                "at": self.now.isoformat(),
            }
        if component in self.infra:
            comp = self.infra[component]
            checks = {
                "process_up": comp.available,
                "connections_ok": comp.saturation < 0.85,
                "latency_ok": comp.latency_p95_ms
                < 5 * self.world.infra_component(component).base_latency_ms,
            }
            state = (
                "unhealthy"
                if not comp.available
                else "degraded"
                if not all(checks.values())
                else "healthy"
            )
            failing = [k for k, v in checks.items() if not v]
            return {
                "service": component,
                "state": state,
                "checks": checks,
                "message": (
                    "all checks passing" if not failing else "failing: " + ", ".join(failing)
                ),
                "at": self.now.isoformat(),
            }
        raise SimulationError(f"unknown component '{component}'")

    def logs_for(
        self,
        component: str | None,
        *,
        start: float | None,
        end: float | None,
        level: str | None,
        limit: int,
    ) -> list[LogRecord]:
        lo = start if start is not None else -math.inf
        hi = end if end is not None else math.inf
        levels = {"ERROR": 3, "WARN": 2, "INFO": 1, "DEBUG": 0}
        min_level = levels.get((level or "DEBUG").upper(), 0)
        out = [
            log
            for log in reversed(self.logs)
            if (component is None or log.service == component)
            and lo <= log.at <= hi
            and levels.get(log.level, 1) >= min_level
        ]
        return out[:limit]

    def traces_for(
        self,
        component: str | None,
        *,
        start: float | None,
        end: float | None,
        errors_only: bool,
        limit: int,
    ) -> list[TraceRecord]:
        lo = start if start is not None else -math.inf
        hi = end if end is not None else math.inf
        out = []
        for tr in reversed(self.traces):
            if not lo <= tr.at <= hi:
                continue
            if errors_only and not tr.error:
                continue
            if component is not None and not any(s.service == component for s in tr.spans):
                continue
            out.append(tr)
            if len(out) >= limit:
                break
        return out

    def deployments_for(self, service: str | None) -> list[DeploymentRecord]:
        return [d for d in self.deployments if service is None or d.service == service]

    def resource(self, component: str) -> dict[str, Any]:
        if component in self.infra:
            comp = self.infra[component]
            return {
                "component": component,
                "kind": comp.kind,
                "at": self.now.isoformat(),
                "metrics": comp.metrics(),
                "attributes": {
                    "version": comp.version,
                    "max_connections": str(comp.max_connections),
                    "up": str(comp.available).lower(),
                },
                "top_clients": dict(
                    sorted(comp.connections_by_client.items(), key=lambda kv: -kv[1])
                ),
            }
        if component in self.services:
            svc = self.services[component]
            return {
                "component": component,
                "kind": "service",
                "at": self.now.isoformat(),
                "metrics": svc.metrics(),
                "attributes": {
                    "version": svc.version,
                    "replicas": str(svc.replicas),
                    "up": str(svc.available).lower(),
                    "restart_count": str(svc.restart_count),
                },
                "top_clients": {},
            }
        raise SimulationError(f"unknown component '{component}'")

    def prometheus_text(self) -> str:
        lines: list[str] = []
        for name, svc in self.services.items():
            for metric, value in svc.metrics().items():
                lines.append(f'sim_service_{metric}{{service="{name}"}} {value:.6f}')
        for name, comp in self.infra.items():
            for metric, value in comp.metrics().items():
                lines.append(f'sim_infra_{metric}{{component="{name}"}} {value:.6f}')
        lines.append(f"sim_active_faults {len(self.active_faults())}")
        lines.append(f"sim_tick_count {self.tick_count}")
        return "\n".join(lines) + "\n"

    def state_summary(self) -> dict[str, Any]:
        return {
            "time": self.now.isoformat(),
            "tick_count": self.tick_count,
            "seed": self.seed,
            "active_faults": [f.to_dict() for f in self.active_faults()],
            "services": {
                n: {
                    "version": s.version,
                    "replicas": s.replicas,
                    "up": s.available,
                    "error_rate": round(s.error_rate, 4),
                    "latency_p95_ms": round(s.latency_p95_ms, 1),
                    "health": self.health(n)["state"],
                }
                for n, s in self.services.items()
            },
            "infra": {
                n: {
                    "connections": round(c.connections),
                    "saturation": round(c.saturation, 3),
                    "up": c.available,
                }
                for n, c in self.infra.items()
            },
        }
