# Fault scenarios

Ground truth for every scenario lives in exactly one place, `src/aegis/simulator/faults.py`, as a
`ScenarioSpec`. The same catalogue drives three consumers, so "did the runtime get it right" has a
single definition:

| Consumer | How it uses the catalogue |
|---|---|
| Simulator (`src/aegis/simulator/engine.py`) | `inject()` creates an `ActiveFault`; `_apply_fault_effects()` advances its dynamics each tick; `clears_fault()` decides whether a remediation actually removes it |
| E2E harness (`scripts/e2e.py`) | injects a scenario against the running compose stack and asserts the incident reaches the expected terminal state |
| Evals (`evals/suites/*.py`) | detection budgets, hypothesis ranking targets, correct/incorrect remediation pairs for verification, and agent grading |

`ScenarioSpec` fields that matter when reading this document:

- `correct_remediations` / `incorrect_remediations` — `(action, target[, extra])` triples. `action`
  is the simulator verb (`restart`, `rollback`, `scale`, `rotate_pool`, `clear_cache`, `none`), not
  the tool name; `evals/suites/agent.py::ACTION_FOR_TOOL` maps tool names onto verbs.
- `expect_no_action` — the graded-correct outcome is that the runtime concludes no remediation is
  required.
- `expect_escalation` — the graded-correct outcome is escalation to humans.
- `self_resolving_seconds` — the fault expires on its own after this long.

The simulated world (`src/aegis/simulator/model.py::default_world`) is a gateway, six services and
two infrastructure components:

```
                       ┌──────────────┐
                       │ api-gateway  │ tier 0, 3 replicas, v3.2.1, 120 rps entry
                       └──┬───┬───┬───┘
      ┌───────────┬───────┘   │   └───────┬───────────────┬──────────────────┐
      ▼           ▼           ▼           ▼               ▼                  ▼
 auth-service  user-service  order-service  payment-service  inventory-service  notification-service
  (2 rep)       (2 rep)       (3 rep)        (2 rep, 768MB)   (2 rep)            (1 rep, non-critical)
      │           │         ┌──┴────┬─────────────┐              │                  │
      ▼           ▼         ▼       ▼             ▼              ▼                  ▼
   redis      postgres   postgres  redis   inventory/payment  postgres/redis      redis
```

`postgres` allows 100 connections, `redis` 250. `order-service` is the heaviest Redis client
(1.5 calls per request). `notification-service` is called with `critical=False, sensitivity=0.05`,
so its failures barely reach the gateway — which is why "the gateway looks fine" is itself a signal.

---

## 1. `redis-connection-leak`

- **Fault type** `redis_connection_leak`, params `{service: order-service, rate_per_second: 2.5}`.
- **Mechanism** each tick adds `rate_per_second × dt × (replicas_ready / base_replicas)` to
  `order-service.leaked_redis`, capped at `1.3 ×` Redis `max_connections`. Redis saturates, every
  Redis client waits on its pool, the gateway inherits latency and 5xx.
- **Symptoms** (`expected_symptoms`) `api-gateway latency_p95_ms`, `redis connections`,
  `order-service redis_pool_wait_ms`, `api-gateway error_rate`. Detected in the reference eval run
  as *"redis connections saturation"*, SEV3, 20 s after injection (budget 60 s).
- **Root cause** `order-service` / `resource_exhaustion`.
- **Correct** `restart(order-service)`, or `rotate_pool(order-service, target=redis)` —
  `rotate_connection_pool(service=order-service, target=redis)` as a tool call.
- **Incorrect** `restart(api-gateway)`, `restart(redis)`, `clear_cache(redis)`,
  `scale(order-service)`. Restarting Redis *does* zero every client's leaked counter in the
  simulator, but `clears_fault` returns `False`, so the leak immediately resumes and verification
  fails; scaling adds replicas that leak proportionally faster.
- **Expected outcome** resolved.
- **What it tests** the shared-resource attribution rule. `attributions()` in
  `src/aegis/hypotheses/engine.py` reads `connections_by_client` / `leaked_by_service` from the
  `inspect_redis` diagnostic; a hypothesis blaming `redis` itself gets an automatic contradiction
  ("the resource is a symptom, not the origin") while a hypothesis blaming `order-service` gets the
  attribution added to its evidence. It also exercises the MEDIUM-risk approval path
  (`restart_service` is `RiskLevel.MEDIUM`, matched by `approve-medium-risk-mutations`) and produces
  the largest verification spec of any scenario (25 conditions in the reference run) because every
  Redis client shows up in the detection signals.

## 2. `bad-deployment`

- **Fault type** `bad_deployment`, params `{service: payment-service, version: 2.4.0,
  error_rate: 0.35, added_latency_ms: 350}`.
- **Mechanism** `_on_fault_start` appends a `DeploymentRecord` (2.3.7 → 2.4.0, change summary
  "checkout handler refactor; new retry path") and sets the service version. Effects apply only
  while `svc.version == params["version"]`, so a rollback removes them and a restart does not.
- **Symptoms** `payment-service error_rate`, `api-gateway error_rate`, plus the deployment record
  itself. Detected as *"Elevated API error rate"*, SEV2, 10 s after injection (budget 30 s).
- **Root cause** `payment-service` / `deployment_regression`.
- **Correct** `rollback(payment-service)` → `rollback_deployment(service=payment-service)`; the tool
  resolves the target version from deployment history when `to_version` is omitted.
- **Incorrect** `restart(payment-service)` (the new version comes back up), `restart(order-service)`,
  `scale(payment-service)`.
- **Expected outcome** resolved.
- **What it tests** deployment-aware temporal scoring and the remediation fit guard.
  `HypothesisEngine.score` gives a `DEPLOYMENT` evidence item full temporal alignment only when the
  deploy precedes detection by 0–3600 s. `validate_remediation_fit`
  (`src/aegis/remediation/planning.py`) refuses `rollback_deployment` unless the hypothesis category
  is `deployment_regression` or recent-deployment evidence exists, and `default_rollback` can build
  a real rollback plan here (re-deploy `from_version`) — the only mutating tool other than
  `scale_service` with a reversible plan.

## 3. `db-pool-exhaustion`

- **Fault type** `db_pool_exhaustion`, params `{service: user-service, rate_per_second: 1.2}`.
- **Mechanism** grows `user-service.leaked_db` up to `1.1 ×` Postgres `max_connections`. Sessions
  show up as `idle_in_transaction`; every Postgres-backed service waits on its pool.
- **Symptoms** `postgres connections`, `postgres idle_in_transaction`, `user-service db_pool_wait_ms`,
  `api-gateway latency_p95_ms`. Detected as *"postgres idle in transaction saturation"*, SEV3, 20 s
  after injection (budget 60 s).
- **Root cause** `user-service` / `resource_exhaustion`.
- **Correct** `rotate_pool(user-service, target=postgres)` or `restart(user-service)`.
- **Incorrect** `scale(user-service)` (more replicas, more pools, faster exhaustion),
  `restart(postgres)`, `restart(order-service)`.
- **Expected outcome** resolved.
- **What it tests** the same attribution rule through a different diagnostic key
  (`idle_in_transaction_by_client` from `inspect_database`), and the pool-rotation exception in
  `validate_remediation_target`: when the hypothesis blames the *resource*, acting on a dependent
  client is still allowed provided `arguments.target` is the blamed resource. It is also the
  scenario where the wrong-but-plausible action (`scale_service`) is LOW risk and therefore
  autonomous in development under `allow-low-risk-mutations-development` — the target and fit guards,
  not the approval gate, are what stop it.

## 4. `cascading-dependency`

- **Fault type** `service_crash`, params `{service: inventory-service}`.
- **Mechanism** `_on_fault_start` sets `crashed = True` and logs `exit code 137 (OOMKilled)`.
  `order-service` calls it critically (1.0 calls/request), so order errors spike and propagate to
  the gateway.
- **Symptoms** `inventory-service up`, `order-service error_rate`, `api-gateway error_rate`.
  Detected as *"Elevated API error rate"*, **SEV1**, 10 s after injection (budget 20 s).
- **Root cause** `inventory-service` / `dependency_failure`.
- **Correct** `restart(inventory-service)`.
- **Incorrect** `restart(api-gateway)`, `restart(order-service)`, `scale(order-service)` — the three
  moves a symptom-driven operator makes first.
- **Expected outcome** resolved.
- **What it tests** the propagation rule and the target guard. `propagations()` reads
  `contributions` from `inspect_dependencies`; a down dependency is forced to `contribution ≥ 0.9`,
  and any hypothesis blaming a service whose own error rate is below 2 % earns the automatic
  contradiction "errors are inherited from *D*; it is a symptom, not the origin". Because detection
  assigns SEV1, `approve-sev1-mutations` (priority 640) requires human approval even for a LOW-risk
  tool, and `risk_for()` lifts any sub-MEDIUM risk to MEDIUM on a SEV1 incident.

## 5. `transient-spike`

- **Fault type** `traffic_spike`, params `{multiplier: 3.0, duration_seconds: 45}`;
  `self_resolving_seconds = 45`, so the fault expires by itself (`_expire_faults` clears it and the
  `clears_fault` table returns `False` for every action).
- **Symptoms** `api-gateway request_rate`, `api-gateway latency_p95_ms`. Detected as *"Traffic surge
  on api-gateway"*, SEV4, 10 s after injection (budget 20 s).
- **Root cause** nominally `api-gateway` / `transient`.
- **Correct** `none`; `expect_no_action = True`.
- **Incorrect** `restart(api-gateway)`, `scale(api-gateway)`.
- **Expected outcome** resolved without action. The workflow takes the `_verify_without_action`
  path: it builds a verification spec from the detection signals with no target service, and only
  marks the incident resolved when the conditions hold — a false positive still has to be *proved*
  recovered rather than assumed.
- **What it tests** that "do nothing" is a first-class outcome. The agent must emit
  `conclude_no_action` (`TerminationReason.NO_ACTION_REQUIRED`); the grading function in
  `evals/suites/agent.py` scores 0 for any plan at all. The eval case advances the simulator an
  extra 60 s (`extra_seconds=60`) so metrics have actually returned to baseline before the agent
  decides, which makes the system prompt's "if metrics have already returned to baseline, conclude
  no remediation is required" rule the decisive one. This is the scenario the real model most often
  gets wrong (see `docs/evals.md`).

## 6. `memory-leak`

- **Fault type** `memory_leak`, params `{service: payment-service, rate_mb_per_second: 4.0}`
  against a 768 MB limit. GC pressure inflates latency; the process eventually OOM-restarts by
  itself inside the simulator (`_crash_restart`).
- **Symptoms** `payment-service memory_percent`, `payment-service latency_p95_ms`. Detected as
  *"payment-service MEMORY pressure"*, SEV4, 40 s after injection (budget 120 s) — the slowest
  detection in the catalogue, because the EWMA baseline has to be outrun by a linear ramp.
- **Root cause** `payment-service` / `resource_exhaustion`.
- **Correct** `restart(payment-service)` (the restart zeroes `memory_leak_mb`).
- **Incorrect** `rollback(payment-service)`, `restart(order-service)`.
- **Expected outcome** resolved.
- **What it tests** resource-pressure detection rules rather than latency/error rules, and the
  memory-pressure branch of the restart fit guard: `validate_remediation_fit` allows
  `restart_service` when `memory_percent ≥ 85` or a `memory_ok` health tag is present, even though
  a restart is refused for a purely CPU-bound service. It was also the known weak spot of the
  verification suite: in the reference run `memory-leak/rollback:payment-service` was the one case in
  twelve that passed verification when it should have failed, because a rollback restarts the
  process in the simulator and the three conditions recovered before the leak rebuilt. That is what
  the accumulation guard in `src/aegis/verification/engine.py` now targets — for
  `ACCUMULATING_METRICS` (`memory_percent`, `connections`, `idle_in_transaction`, `saturation`,
  `leaked`) a condition that is currently under its threshold but has gained more than
  `MAX_ACCUMULATION_SHARE` (10 %) of that threshold across the window counts as failing, with the
  detail "still accumulating". The eval report predates the guard.

## 7. `cpu-saturation`

- **Fault type** `cpu_saturation`, params `{service: notification-service, pressure: 75.0}`,
  scaled by `base_replicas / replicas_ready` — so adding replicas genuinely reduces the pressure.
- **Symptoms** `notification-service cpu_percent`, `notification-service latency_p95_ms`. Detected
  as *"Latency regression on notification-service"*, SEV3, 15 s after injection (budget 30 s). The
  gateway is barely affected because the dependency is non-critical with sensitivity 0.05.
- **Root cause** `notification-service` / `capacity`.
- **Correct** `scale(notification-service, replicas ≥ 3)` — `clears_fault` requires
  `int(extra["replicas"]) >= 3`, and the eval matcher requires the planned `replicas` to be at least
  the ground-truth value.
- **Incorrect** `restart(notification-service)`, `restart(api-gateway)`.
- **Expected outcome** resolved.
- **What it tests** that the runtime distinguishes *capacity* from *leaked state*, and that it does
  so from evidence rather than from the model's label. Restarting returns the same single replica to
  the same load, so `capacity` maps only to `scale_service` in `_FIT`, and `category_default_action`
  computes `replicas = current + 2` (capped at 10) for the deterministic planner. The decisive guard
  is `_effective_category` (`src/aegis/remediation/planning.py`): a target that is CPU-bound
  (`cpu_percent ≥ 90` or a failing `cpu_ok` health check in evidence) with no memory pressure and no
  leaked state is treated as `capacity` **whatever category the hypothesis claimed**, so
  `restart_service` and `rotate_connection_pool` are refused with "use `scale_service` instead" and
  the refusal is fed back to the model. At the default `pressure: 75` the simulated
  `notification-service` sits at `cpu_percent = 100` with `memory_percent ≈ 37` and a failing
  `cpu_ok` check, so the guard fires. Residual limit: a service genuinely saturated *below* 90 %
  CPU, with no leak and no memory pressure in evidence, is not reclassified.

## 8. `network-latency`

- **Fault type** `network_latency`, params `{source: api-gateway, target: payment-service,
  added_ms: 800}`. The latency is attached to the *edge* (`svc.edge_latency_ms`), not to either
  endpoint, so no component looks unhealthy on its own.
- **Symptoms** `api-gateway latency_p95_ms` only. Detected as *"API latency regression"*, SEV3,
  15 s after injection (budget 30 s); the detection eval scores it 0.8 rather than 1.0 because the
  root-cause service never enters `affected_services`.
- **Root cause** recorded as `payment-service` / `network`, but no in-scope tool can fix a network
  path: `clears_fault` returns `False` for every action.
- **Correct** `none` with `expect_escalation = True`.
- **Incorrect** `restart(payment-service)`, `rollback(payment-service)`.
- **Expected outcome** escalated to humans (`IncidentStatus.ESCALATED`).
- **What it tests** the honest-failure path. Nothing in the catalogue rewards the runtime for acting;
  the graded-correct behaviour is to exhaust the investigation phases and escalate with no action
  plan (`grade()` requires `escalated and not plan`). It is also the hardest case for hypothesis
  ranking: the true root cause ranks 4th (`top3: api-gateway, postgres, redis`), which is the single
  failure in the 7-case hypothesis suite and the reason its MRR is 0.89 rather than 1.0 — the
  dependency-alignment component cannot distinguish "the gateway is slow because of the edge to
  payment-service" from "the gateway is slow".

---

## Injecting a scenario

```bash
uv run aegis scenarios                        # id + title for all eight
uv run aegis inject redis-connection-leak     # POST /api/v1/simulation/faults
uv run aegis inject cpu-saturation --params '{"pressure": 90}'
```

The same thing over HTTP (operator role required, see `src/aegis/api/v1/simulation.py`):

```bash
curl -XPOST localhost:8600/api/v1/simulation/faults \
  -H 'content-type: application/json' \
  -d '{"scenario_id":"bad-deployment"}'
curl localhost:8601/api/faults                 # simulator's own view
curl -XDELETE localhost:8600/api/v1/simulation/faults/fault-1   # clear without remediating
```

## Adding a scenario

1. Add a `ScenarioSpec` to `SCENARIOS` in `src/aegis/simulator/faults.py` with a new `fault_type`.
2. Implement the dynamics: a branch in `SimulationEngine._apply_fault_effects` (per-tick effects)
   and, if the fault has an onset event such as a deployment or a crash, in `_on_fault_start`.
3. Add the ground-truth clause to `clears_fault` — this is what makes a remediation "correct".
4. Give the detection suite a budget in `evals/suites/detection.py::DETECT_BUDGET_SECONDS`.
5. Run `make evals`; the detection, hypothesis, verification and agent suites pick the new scenario
   up automatically from `SCENARIOS`. `scripts/e2e.py` keeps its own shorter list.
