# ADR-010: A deterministic simulator is the shared ground truth for E2E and evals

- Status: Accepted, implemented (2026-09-10)
- Code: `src/aegis/simulator/{model,state,engine,faults,api}.py`,
  `src/aegis/infrastructure/simulator/{http,inprocess,control,convert}.py`, `scripts/e2e.py`,
  `evals/harness/world.py`, `evals/suites/*.py`

## Context

"Did the agent get it right" needs a definition that does not depend on a human reading a
transcript. Real infrastructure is neither reproducible nor safe to break in CI, and a mocked
telemetry fixture cannot tell the difference between a restart that fixes a leak and one that hides
it for a minute.

## Decision

- `SimulationEngine` is a seeded discrete-time model of a small system: `api-gateway` calls
  `auth-service`, `user-service`, `order-service`, `payment-service`, `inventory-service` and
  `notification-service` and has no infrastructure dependency of its own; `order-service` also calls
  `inventory-service` and `payment-service` (the service-to-service edges the `cascading-dependency`
  scenario is built on); the services depend on `postgres` and/or `redis`, except
  `notification-service`, which depends on `redis` only. Causal propagation: traffic flows
  roots-to-leaves, latency and errors propagate leaves-to-roots with timeouts and sensitivities,
  shared resources saturate from per-client connection demand plus leaks, and resource usage drives
  latency multipliers and OOM crash-restarts. Same seed, same trajectory
  (`tests/unit/test_simulator.py::test_determinism_same_seed_same_trajectory`).
- `faults.py` is the single source of truth: each `ScenarioSpec` declares symptoms, root-cause
  service and category, the remediations that clear the fault and the plausible ones that do not,
  and whether the correct outcome is no action or escalation. `clears_fault` implements "did this
  action actually fix it", and it is the only thing that lets a *remediation* clear a fault;
  `SimulationEngine.clear_fault` (exposed as `DELETE /api/faults/{id}`) and `_expire_faults` (the
  declared duration elapsed, which is how `transient-spike` self-resolves) clear a fault without
  consulting it. Restarting a symptomatic caller, flushing the cache, or scaling a leaking service
  therefore leave the fault active and verification fails on the metrics — though a wrong restart
  still zeroes the simulator's leak counters (an infrastructure restart zeroes them for every
  service), so the symptom disappears briefly while the fault keeps leaking from zero, which makes
  verification failure for those cases timing-dependent.
- The simulator is a separate process (`aegis-simulator`, :8601) that also exports Prometheus
  metrics, so the same world can be read through the simulator adapter or through
  `PrometheusTelemetryProvider` — which covers metrics only and requires a
  `fallback: TelemetryProvider` for logs, traces, health, deployments and resources. In-process
  adapters run the identical engine for unit tests and evals.
- E2E (`scripts/e2e.py`) injects scenarios through the API, approves when asked, and asserts the
  ground truth (correct verified plan on the right target, fault cleared, and no two executed plans
  sharing the same `(tool_name, arguments)` — that is, no remediation executed twice — or no plan /
  escalation). The evals (`evals/suites`) grade detection, ranking, verification and the agent
  against the same specs; four of the seven suites read `aegis.simulator.faults`
  directly (detection, hypotheses, verification, agent), while authorization, memory and llm_schema
  do not.
- Remediation endpoints have realistic dynamics: restarts take `restart_seconds` (10 s for services,
  8 s for infrastructure components) with `up=0` meanwhile, scaling ramps replicas over
  `scale_seconds` (20 s), rollbacks take 15 s and record a deployment, cache flushes drop the hit
  rate and recover over 25 s.

## Consequences

- One definition of correctness shared by simulator, E2E and evals, but adding a scenario touches
  more than `engine.py`: a `ScenarioSpec` and a `clears_fault` case in `faults.py`, cases in up to
  three `fault_type` switches in `engine.py` (`_on_fault_start`, `_apply_fault_effects`, `_clear`), a
  `DETECT_BUDGET_SECONDS` entry in `evals/suites/detection.py`, a `CATEGORY_FOR` entry in
  `evals/suites/hypotheses.py`, and entries in `scripts/e2e.py` for E2E coverage.
- Evals run in seconds without infrastructure (`InProcessSimulatorTelemetry`, `InMemoryStore`).
- The simulator's vocabulary (metric names, component kinds, diagnostic payload keys such as
  `connections_by_client`, `leaked_by_service`, `error_contribution`) is what the tools, the
  attribution/propagation rules and the verification metric table understand. A real telemetry
  backend must be adapted to that vocabulary.
- Eight scenarios ship; the five in `scripts/e2e.py`'s `DEMO` list are the default E2E subset.
  Faults with no in-scope fix (`network-latency`) exist to test that the runtime escalates instead of
  acting, but that branch is not exercised by a default `make e2e` run because `network-latency` is
  not in `DEMO`.

## Alternatives considered

- Mocked telemetry responses per test: cannot express causality or the consequences of a wrong
  action.
- Chaos against real containers: valuable (and used in `tests/chaos` for the Aegis stack itself)
  but neither reproducible nor fast enough to grade an agent on every change.
- Replaying recorded production incidents: useful later for realism; today there is no corpus and no
  way to replay a remediation's effect.
