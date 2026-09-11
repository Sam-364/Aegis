# Aegis architecture overview

Aegis is a durable incident-response runtime. An LLM is one component inside it, and the only thing
it may do is *propose*: a read-only or diagnostic tool call, a hypothesis, a remediation plan, or a
conclusion. Everything that changes state is decided, authorized, executed, recorded and verified by
deterministic code. This document describes the system as implemented under `src/aegis/`. Where the
amended plan (`docs/architecture/PLAN.md`) differs from the code, the code is described and the
difference is called out.

## Processes

Every process is one console script from `pyproject.toml` running from the same image (`Dockerfile`):

| Process | Entrypoint | Role |
|---|---|---|
| `aegis-api` | `src/aegis/apps/api.py` → `aegis.api.app.create_app` | FastAPI control plane on :8600 (REST, SSE, `/health`, `/ready`, `/metrics`) |
| `aegis-worker` | `src/aegis/apps/worker.py` → `aegis.workflows.worker.build_worker` | Temporal worker running `IncidentWorkflow` and every activity, including the agent; metrics on :9464 |
| `aegis-detector` | `src/aegis/apps/detector.py` | Telemetry poller behind a Redis leader lock; opens incidents through `IncidentIntakeService`; metrics on :9465 |
| `aegis-simulator` | `src/aegis/apps/simulator.py` → `aegis.simulator.api` | Simulated infrastructure on :8601 (telemetry, diagnostics, remediation endpoints, fault injection, `/metrics`) |
| `aegis` | `src/aegis/apps/cli.py` | `migrate`, `check`, `scenarios`, `inject`, `incidents` |

Every runtime process except `aegis-simulator` is wired by
`aegis.application.bootstrap.build_runtime(settings, role=...)` (the simulator process builds
`aegis.simulator.api.create_app` directly, with no container), which produces a `RuntimeContainer`
(`src/aegis/application/container.py`): tool registry, flow registry (validated against the registry
at startup), policy engine, authorizer, agent runtime, the
intake/incident/approval/notification services, plus the infrastructure adapters (Postgres unit of
work, Redis publisher/lock/rate-limiter/baseline store, simulator HTTP clients, optional Prometheus
provider, optional Temporal client, and for the worker only the LangGraph `AsyncPostgresSaver`).

## Layers and import contracts

```
 ┌──────────────────────────────────────────────────────────────────────────────┐
 │ Delivery       api/ (FastAPI, SSE)     workflows/ (Temporal)     apps/ (CLI) │
 ├──────────────────────────────────────────────────────────────────────────────┤
 │ Application    application/ (bootstrap, container, intake, incidents,        │
 │                approvals, notifications, memory_search, Emitter)             │
 ├──────────────────────────────────────────────────────────────────────────────┤
 │ Runtime core   flow/ policy/ tools/ evidence/ hypotheses/ detection/         │
 │                verification/ remediation/ memory/ llm/ agent/                │
 ├──────────────────────────────────────────────────────────────────────────────┤
 │ Ports          ports/ (Protocols: repositories, telemetry, llm, messaging,   │
 │                detection sink)                                               │
 ├──────────────────────────────────────────────────────────────────────────────┤
 │ Domain         domain/ (entities, enums, state machine, events, errors)      │
 └──────────────────────────────────────────────────────────────────────────────┘
   infrastructure/  implements the ports: postgres, redis, simulator (http and in-process),
                    prometheus, temporal, memory (in-process doubles for tests and evals)
   simulator/       independent package with its own process; imports nothing from the runtime
   config.py, logging.py, telemetry/   shared leaf modules (settings, structlog, Prometheus, OTel)
```

Import direction is enforced by five import-linter contracts in `pyproject.toml` (`make lint` runs
`lint-imports`):

1. **Domain is pure**: `aegis.domain` imports no other Aegis package.
2. **Ports depend only on domain**.
3. **Core runtime never imports delivery or infrastructure**: `flow, policy, tools, evidence,
   hypotheses, detection, verification, remediation, memory, llm, agent` may not import
   `infrastructure, application, api, workflows, apps`.
4. **Simulator is independent**: `aegis.simulator` imports none of the runtime packages.
5. **Application and infrastructure do not import delivery** (`api`, `apps`).

## The durable spine

```
 simulator | Prometheus ──TelemetryProvider──▶ DetectionEngine (aegis-detector, leader replica only)
                                                  │ EWMA baselines + MetricRules → AnomalySignal
                                                  │ IncidentCorrelator → open new | attach to active
                                                  ▼
                                   IncidentIntakeService (implements IncidentSink)
                                     insert incident (detected) + timeline + audit + notification
                                     start Temporal IncidentWorkflow, id = incident-<uuid>
                                                  ▼
 ┌─ IncidentWorkflow (task queue aegis-incidents) ───────────────────────────────────────────────┐
 │ triage_incident            select flow pack (or keep pinned one), detected → triaging          │
 │ for phase in flow (≤ max_phases = 14; a 3rd entry into the same phase → escalate):            │
 │   set_incident_status(phase)                                                                  │
 │   run_agent_phase ──▶ AgentRuntime.run_phase (LangGraph; checkpoint thread = agent_run_id)   │
 │        returns: transition | no_action | action_planned | escalate | terminate                 │
 │   action_planned:                                                                             │
 │     remediation_planned ─▶ evaluate_remediation_policy ─▶ allow | deny | require_approval    │
 │     require_approval: awaiting_approval; wait_condition(approval_decided signal | cancel,      │
 │                       timeout = approval_timeout_seconds) ─▶ expire_approval on timeout       │
 │     remediating ─▶ capture_metrics ─▶ execute_remediation (ToolExecutor, in_agent_loop=False,│
 │                    idempotency key scoped to the action plan)                                  │
 │     verifying   ─▶ verify_remediation (VerificationEngine, heartbeating)                      │
 │        passed → resolved | failed → rollback if available → replan (≤ max_remediation_attempts)│
 │                                      | escalated                                              │
 │   no_action, or transition into `verify`: verify_recovery ─▶ resolved | back to investigate   │
 │ finalize_incident   resolved | escalated | failed | closed; metrics; notification;             │
 │                     incident memory (pgvector) when resolved or escalated                      │
 └───────────────────────────────────────────────────────────────────────────────────────────────┘
                 API approve/reject ─▶ signal approval_decided          API close ─▶ signal cancel
```

Properties and where they are enforced:

- The workflow body (`src/aegis/workflows/incident_workflow.py`) is deterministic: every side effect
  is an activity in `src/aegis/workflows/activities.py`, ids come from `workflow.uuid4()`, waits are
  `workflow.wait_condition` with timeouts.
- Retry policies: `DB_RETRY` (8 attempts, exponential backoff) for bookkeeping activities;
  `AGENT_RETRY` (3 attempts, `heartbeat_timeout=90s`, `start_to_close=phase_timeout_seconds`,
  default 600) for `run_agent_phase`; `EXEC_RETRY` (3 attempts, `ToolAuthorizationError` and
  `PolicyViolation` non-retryable) for `execute_remediation`.
- Activity timeouts are per activity: `execute_remediation` 180 s, `verify_remediation` 900 s,
  `verify_recovery` 600 s, `run_agent_phase` `phase_timeout_seconds`, bookkeeping 30-120 s.
- The agent activity heartbeats at every graph node (`AgentHooks.on_step` → `activity.heartbeat`),
  so a hung LLM call is detected within 90 s and the retried activity resumes from the checkpoint.
- Phase cycle guard: `_cycling()` counts entries in `_status.phases` and `MAX_PHASE_ENTRIES = 2`
  (`incident_workflow.py:50`), so a third entry into the same phase escalates with "investigation is
  cycling on phase X without reaching a remediation" instead of looping until `max_phases`.
- `@workflow.query def status()` exposes a `WorkflowStatus` (`src/aegis/workflows/contracts.py`:
  `phase`, `phases`, `awaiting_approval_id`, `remediation_attempts`, `status`, `cancelled`), served
  by `GET /api/v1/incidents/{id}/workflow`.
- Mutating tools are executed only by `execute_remediation` with `in_agent_loop=False`. Inside the
  agent loop no shipped flow pack lists a mutation in a phase's `tools:`, so the refusal comes from
  check 5 `phase_allows` (`phase_violation`); check 6 `category_permitted` only fires if a pack ever
  lists a mutation in a phase, and the `no_mutation_inside_agent_loop` invariant at check 11 is a
  third, independent refusal. Both later checks are defence in depth.

## Tool authorization pipeline

`ToolAuthorizer.authorize` (`src/aegis/tools/authorizer.py`) runs the same 14 ordered checks for
every tool request, whether it comes from the LLM inside the loop or from the workflow for a
remediation. It never raises: it returns an `AuthorizationDecision` whose `checks` tuple holds every
passed check followed by the single failing one, so a denial trace is truncated at the failure.
`raise_for()` maps a denial to the typed exception in `DENIAL_EXCEPTIONS` for callers that prefer
exceptions.

| # | check | what it proves | denial code |
|---|---|---|---|
| 1 | `tool_registered` | the name exists in `ToolRegistry`; unknown tools do not exist | `tool_not_registered` |
| 2 | `tool_enabled` | not disabled at runtime (`registry.disable`) and `spec.enabled` | `tool_disabled` |
| 3 | `incident_active` | `incident.status.is_active`: not `resolved`, `closed` or `failed` | `incident_inactive` |
| 4 | `flow_allows` | tool is in `flow.all_tools` (union of every phase's `tools` and the pack's `remediation_tools`) | `flow_violation` |
| 5 | `phase_allows` | in the loop: tool is in the current `phase.allowed_tools`; via workflow: a mutation must be in `flow.remediation_tools` | `phase_violation` |
| 6 | `category_permitted` | `dangerous` is never executable; `mutating` is never executable inside the agent loop | `tool_not_allowed` |
| 7 | `environment_allows` | the runtime environment is in `spec.allowed_environments` | `tool_not_allowed` |
| 8 | `severity_allows` | `spec.min_severity` is unset or the incident is at least that severe | `tool_not_allowed` |
| 9 | `actor_permitted` | the requesting actor holds the role required for the category: read_only → viewer, diagnostic → operator, mutating → operator, dangerous → admin | `tool_not_allowed` |
| 10 | `risk_within_ceiling` | `spec.risk` ≤ `phase.risk_ceiling` (loop) or `flow.remediation_risk_ceiling` (workflow) | `risk_ceiling_exceeded` |
| 11 | `policy_effect` | `PolicyEngine.evaluate(PolicyContext)` returns `allow`, or returns `require_approval` and an `ApprovalRequest` with status `approved` **and** `action_plan_id == request.action_plan_id` was supplied | `policy_violation` (deny) / `approval_required` (decision effect `require_approval`) |
| 12 | `arguments_valid` | `ToolDefinition.parse_args`: injection and size guard on every string, strict Pydantic model (`extra="forbid"`), and any `service`/`component`/`source`/`target` argument names a known topology component | `invalid_tool_arguments` |
| 13 | `idempotency` | no execution with the same key is `running`; a completed one is noted ("result will be reused") | `duplicate_execution` |
| 14 | `budget_available` | `BudgetUsage.exceeded(ExecutionBudget)` is empty (iterations, tool calls, LLM calls, tokens, runtime) | `execution_budget_exceeded` |

Idempotency keys (`idempotency_key_for`): a mutation carrying an `action_plan_id` is keyed
`sha256("plan:<plan_id>|<incident_id>|<tool>|<canonical json args>")[:32]`, so a plan attempt
executes at most once no matter how often the activity retries; reads are keyed per request id
because re-reading is harmless.

The decision is persisted on the `ToolExecutionRecord` (`tool_executions.document.authorization`)
and copied into the `tool.denied` / `tool.executed` audit event, which is what lets the console show
a gate sequence per call.

`ToolExecutor.execute` (`src/aegis/tools/executor.py`) wraps the pipeline:

- denied → record with status `denied` and key `denied:<request id>`, audit `tool.denied`,
  `aegis_policy_denials_total` incremented;
- mutation with a prior `succeeded` record under the same key → `skipped_duplicate` reusing the prior
  result, keyed `dup:<request id>`, audit `remediation.skipped_duplicate`; denied and duplicate
  records are deliberately kept off the real idempotency key so they never claim it;
- otherwise the record is inserted with status `running` *before* the handler runs; the unique
  `idempotency_key` column makes that insert the claim, and a `ConflictError` from a concurrent
  claimant becomes `skipped_duplicate` ("concurrent duplicate suppressed");
- non-mutating tools retry on timeout or `InfrastructureError` up to `spec.retry.max_attempts`
  (backoff 0.5 s × 2); mutations get exactly one attempt. `RetryPolicy.max_attempts` itself defaults
  to **1** (`src/aegis/domain/tool.py`); the 3 attempts come from the `@tool` decorator
  (`src/aegis/tools/definition.py`: 3 for non-mutating, 1 for mutating). Domain `AegisError`s other
  than `InfrastructureError` and the timeout are not retried at all;
- `asyncio.wait_for(spec.timeout_seconds)` bounds every handler (10 s default, 30 s for mutations);
- successful output becomes `Evidence` rows (one per `EvidenceDraft`), linked to the record;
- a handler that produced no output leaves the record `timed_out` (on `ToolTimeout`) or `failed`, with
  a `tool.failed` audit event carrying that status and `aegis_tool_failures_total` incremented.

## Policy engine

`PolicyEngine` (`src/aegis/policy/engine.py`) evaluates three hard-coded invariants first, then the
YAML rules from `policies/*.yaml` sorted by `priority` descending, first match wins, default **deny**:

- `dangerous_tools_denied`: category `dangerous` → deny.
- `no_mutation_inside_agent_loop`: category `mutating` with `in_agent_loop=True` → deny.
- `agent_cannot_mutate`: category `mutating` requested by an `agent` actor → deny, even if a bug
  passed `in_agent_loop=False`.

`policies/default.yaml` ships eleven rules (priority 1000 → 100): deny dangerous; allow read-only;
allow diagnostics up to risk `low`; require approval for medium-risk diagnostics; deny high-risk
mutations in production; require approval for `rollback_deployment`, for every mutation in staging
and production, for every mutation on a `sev1` incident, and for medium-or-higher-risk mutations;
allow low-risk mutations in development; require approval for any remaining mutation. A `PolicyRule`
can also match on `tools`, `phases`, `flows`, `actor_kinds`, `required_role` and `in_agent_loop`.
The `PolicyDecision` is stored on the action plan (`action_plans.document.policy_decision`) as
`{effect, matched_rule, reason, invariant, evaluated_rules}` — the `PolicyContext` itself is not
persisted — and emitted as `policy.decided`. `evaluate_remediation_policy` (`activities.py`) builds
the context with `environment = incident.environment` and `phase="remediate"`, and short-circuits an
idempotent replay twice over: an existing `plan.approval_id` returns `require_approval` without
re-evaluating, and a plan already `approved`/`executing`/`executed` returns `allow`.

## The agent loop

`AgentRuntime` (`src/aegis/agent/runtime.py`) compiles a LangGraph `StateGraph` over a small
`AgentState` (`src/aegis/agent/state.py`: ids, phase, iteration, usage, feedback, last proposal,
decision, invalid streak, model). Evidence and hypotheses are not part of the state; they are re-read
from the database at every node, which keeps checkpoints small and resumable.

```
 START ─▶ observe ──(decision set)──▶ END
             │ no decision
             ▼
          propose ──▶ execute_tool ────┐
             ├──────▶ apply_hypotheses ┼──▶ observe (loop)   or END once a decision is set
             ├──────▶ plan_remediation ┤
             ├──────▶ conclude ────────┘
             └──────▶ observe            (no proposal was produced)
```

- **observe** (deterministic). Terminates the phase if the incident is no longer active
  (`incident_inactive`), if `should_cancel` fires, or if the budget is exceeded (`budget_exhausted`,
  emits `budget.exhausted`). `should_cancel` is never wired in production — `activities.py` sets only
  `AgentHooks(on_step=heartbeat)` — so cancellation is noticed at phase boundaries and in the
  approval wait, not inside the graph. It then calls `_signal_status(incident)` once per iteration
  (`runtime.py` ~line 322): when every detection-signal condition holds on the 45 s window mean
  (it builds a throwaway `VerificationEngine` with `window_seconds=45` and calls `evaluate_once`, so
  the accumulation guard applies), no affected service or gateway went `up < 1` in the last 120 s
  (crash-loop guard), `iteration >= 1`, the phase does not plan remediation and nothing has been
  remediated (`action_plan_id` unset, `incident.active_action_plan_id` unset,
  `remediation_attempts == 0`), `observe` itself returns `decision="no_action"` with an `agent.step`
  event titled "Runtime: all detection signals returned within baseline without remediation". The
  recovery decision belongs to the runtime; the model cannot keep the incident open. Otherwise it
  computes `PhaseFacts` (iterations, evidence count, hypotheses count, top confidence, hypothesis
  validated, action planned, no-action) and lets
  `FlowRuntime.decide` (`src/aegis/flow/runtime.py`) decide: a `no_action_required` transition if the
  flow defines one, then `exit_conditions_met`, then `exhausted` (iterations ≥ `max_iterations`)
  with `escalate` as the fallback transition. A transition ends the phase with decision
  `transition` and emits `flow.phase_exited`.
- **propose**. Builds the prompt (`src/aegis/agent/prompts.py`: incident header, detection signals,
  topology, phase objective and guidance, allowed actions, allowed tools with argument schemas,
  runtime-computed current-vs-baseline lines, a loud signal-status block ("ALL DETECTION SIGNALS ARE
  BACK WITHIN BASELINE" vs "detection signals are still anomalous") carrying the same
  `_signal_status` verdict `observe` computed, similar past incidents as ambient memory, evidence
  digest with `E1..En` handles, hypotheses digest with `H1..Hn`, the last six runtime feedback lines,
  remaining budget) and asks the LLM for exactly one `AgentProposal`
  (`src/aegis/agent/schemas.py`) on the `reasoner` tier with
  `prompt_cache_key=incident_id`. On `LLMError` it emits `llm.fallback` and marks the LLM unavailable
  for the rest of the phase. When the LLM is absent, unhealthy (circuit breaker open) or the
  `invalid_streak` has reached `max_invalid_streak` (2), the `DeterministicPlanner`
  (`src/aegis/agent/planner.py`) produces the step and it is recorded with kind `fallback`. An action
  outside `PHASE_ACTIONS[phase]` is rejected with feedback and counts toward the streak.
- **execute_tool**. Parses `arguments_json`, builds a `ToolCallRequest` with
  `requested_by=Actor.agent(run_id)` and runs `ToolExecutor.execute(in_agent_loop=True)`. Denials
  are fed back verbatim to the next prompt ("was refused (code): reason. Allowed tools now: ..."), so
  the model learns the boundary instead of looping. Produced evidence is linked to services in the
  evidence graph. If the call declared `tests_hypothesis=Hk`, `_judge` (`runtime.py` ~line 1223) asks
  the `reasoner` tier for a `TestJudgement` and `HypothesisEngine.judge_test` applies the
  deterministic attribution guard before rescoring. `_judge` is not only a downgrade: with no LLM it
  renders its own verdict (`confirmed` iff some produced evidence has `service == root`,
  `strength >= 0.75` and none of the `normal`/`ok`/`not_reproduced`/`stable` tags), and it *upgrades*
  an `inconclusive` LLM verdict to `confirmed` under that same condition.
- **apply_hypotheses**. `HypothesisEngine.accept` requires a known root-cause service and at least one
  resolvable evidence handle; duplicates (same service and category) are merged; every hypothesis is
  rescored, linked in the evidence graph, and the leader becomes `incident.leading_hypothesis_id` and
  `root_cause_summary`. The same node also serves `update_hypothesis`, the seventh member of
  `ProposalAction` (`src/aegis/agent/schemas.py`): `_apply_update` (`runtime.py` ~line 1259) adds
  supporting or contradicting evidence by handle, `abandon: true` sets `HypothesisStatus.ABANDONED`,
  and a model-supplied `test_outcome` is pushed through `judge_test` like a real diagnostic.
- **plan_remediation**. Runtime guards, in order: the hypothesis handle exists; it is `confirmed` or
  `supported` with confidence ≥ `min_remediation_confidence` (0.55); the tool is in
  `flow.remediation_tools`, registered and `mutating`; arguments validate against the tool's model
  and the known components; `validate_remediation_target` (act on the root cause, or on the client
  that holds a saturated shared resource); `validate_remediation_fit` (the tool must fit the
  diagnosed category: rollback only for `deployment_regression` or with a recent deployment in
  evidence, scale only for `capacity`, pool rotation only for `resource_exhaustion`, restart for
  `resource_exhaustion`/`dependency_failure`/`configuration`/`unknown`). Inside the fit check,
  `_effective_category` lets the evidence override the model's label: a target that is CPU-bound
  (`cpu_ok` tag or `cpu_percent >= 90`) with no memory pressure (`memory_percent >= 85`) and no
  leaked state counts as `capacity` whatever the hypothesis said, so `restart_service` and
  `rotate_connection_pool` are refused with "use scale_service instead"; `rollback_deployment` is
  refused unless the hypothesis is `deployment_regression` or recent-deployment evidence exists.
  Every refusal text is fed back to the model as feedback. The runtime then computes the
  `VerificationSpec` and the `RollbackPlan` (agent-proposed and validated, else
  `default_rollback`) and stores an `ActionPlan` with status `proposed`; the phase ends with
  `action_planned`. Nothing is executed here.
- **conclude**. `conclude_no_action` is accepted only if `_signal_status` shows every symptom metric
  inside its verification condition on the 45 s mean (accumulation guard included), and no affected
  service restarted in the last 120 s; otherwise the anomalous lines are fed back. `escalate` ends the
  phase. `phase_complete` is advisory: the flow's exit conditions decide on the next `observe`.

Every node except `observe` writes `AgentStep` rows (`agent_steps`, unique on `(agent_run_id, seq)`)
and timeline events; `observe` records a step only on its runtime no-action branch — a transition
emits just a `flow.phase_exited` timeline event, and the inactive, cancelled and budget branches
write no step at all. `_finish` closes the `AgentRun` with a `TerminationReason`, records
`aegis_agent_runs_total{phase,termination}` and `aegis_agent_iterations`, and returns a
`PhaseOutcome` to the workflow.

**Checkpoint resume.** `run_phase` uses `thread_id = agent_run_id`. The workflow generates the run id
with `workflow.uuid4()`, so a retried activity (worker crash, heartbeat timeout) calls `run_phase`
with the same id; `graph.aget_state` finds the checkpoint and `ainvoke(None)` resumes after the last
completed node, so ledgered tool calls are not repeated
(`tests/unit/test_agent_runtime.py::test_crash_mid_phase_resumes_from_checkpoint_without_repeating_tool_calls`).
The worker uses `AsyncPostgresSaver` on the same Postgres database; other roles use `InMemorySaver`.

## Hypothesis scoring

`HypothesisEngine.score` (`src/aegis/hypotheses/engine.py`) is the only source of confidence; the
model never supplies a number and `HypothesisProposal` has no confidence field.

```
total = 0.45 · evidence_strength + 0.15 · temporal_alignment + 0.25 · dependency_alignment
      + 0.15 · historical_similarity − 0.50 · contradiction_penalty + validation_bonus
clamped to [0, 1]                                   (ScoreWeights defaults)
```

- `evidence_strength = min(0.95, 0.6 · max(w) + 0.4 · noisy_or(w))` over weighted supporting items.
  An item's `strength` counts fully if its `service` is the suspected root cause (or unset, or the
  root appears in its tags) and half otherwise. The attribution and propagation rules below add
  items automatically.
- `temporal_alignment`: mean over supporting items of 1.0 when observed within
  [detected_at − 10 min, now + 5 s], else 0.5; deployment evidence scores 1.0 only if deployed
  0–3600 s *before* detection (else 0.3); memory evidence scores 0.7.
- `dependency_alignment`: fraction of the symptomatic services (those carrying detection signals) the
  root can explain (`_explains`): same component; the root is in the symptom's downstream closure;
  the symptom is a database/cache that the root depends on; or both depend on a shared database/cache.
  `transient` hypotheses get at least 0.6.
- `historical_similarity`: similarity of memory evidence that names the same root service (halved
  if only the category matches).
- `contradiction_penalty = noisy_or(strengths of contradicting evidence + automatic contradictions)`.
- `validation_bonus = min(0.3, 0.15 · confirmed_tests) − min(0.6, 0.18 · refuted_tests)`
  (`ScoreWeights.confirmed_bonus = 0.15`, `refuted_penalty = 0.18`).

**Attribution rule** (`attributions`). From diagnostic evidence that carries `component`,
`saturation` and `connections_by_client`/`clients_by_service`: take the top client's share of the
connections; if a `leaked_by_service` or `idle_in_transaction_by_client` map exists, that client
wins with share ≥ 0.8. The attribution counts when share ≥ 0.4 and saturation ≥ 0.5. A hypothesis
blaming the *client* gains `strength · share`; one blaming the *resource* gains an automatic
contradiction ("the resource is a symptom, not the origin").

**Propagation rule** (`propagations`). From `inspect_dependencies` evidence
(`contributions[].error_contribution`; an unavailable dependency counts as ≥ 0.9; threshold 0.2). A
hypothesis blaming the *dependency* gains `strength · contribution`; one blaming a caller whose own
error rate is below 2% gains a contradiction ("errors are inherited from <dependency>").

Status follows the score (`_status_for`), except that `abandoned` short-circuits before every other
branch and stays: `refuted` if two or more tests refuted **and** outnumber the confirmed ones, or if
at least one refuted and confidence < 0.35 — a single refuted test no longer refutes a hypothesis on
its own; `confirmed` if a test confirmed and confidence ≥ 0.65; `supported` at ≥ 0.55; `testing` once
any test exists; otherwise `proposed`. Ranking is by confidence descending, then creation time.
`judge_test` downgrades a model verdict of `confirmed` to `inconclusive` unless the diagnostic
evidence implicates the suspected service with strength ≥ 0.6, and downgrades `refuted` to
`inconclusive` when the test produced no evidence.

## Verification model

Resolution is measured, never asserted. `build_verification_spec`
(`src/aegis/remediation/planning.py`) derives the conditions from the incident's detection signals
plus the remediation tool's declared `verification_metrics` on its target plus `<target>.up`; the
model contributes nothing. Only metrics with a `_METRIC_RULES` entry become conditions — `clear_cache`
declares `verification_metrics=("hit_rate",)` and `hit_rate` has no rule, so it contributes none — and
when nothing maps at all the builder falls back to `error_rate` + `latency_p95_ms` per affected
service. Metrics with a healthy baseline use a ratio (`latency_p95_ms` and
`latency_p50_ms` ≤ 1.5×, `request_rate` ≤ 1.8×, `connections` ≤ 1.6×); the rest use absolutes
(`error_rate` ≤ max(0.03, baseline + 0.02), `saturation` ≤ 0.85, `idle_in_transaction` ≤ 10,
`cpu_percent` and `memory_percent` ≤ 85, `db_pool_wait_ms`/`redis_pool_wait_ms` ≤ 100, `up` ≥ 1).

`VerificationEngine.verify` (`src/aegis/verification/engine.py`) sleeps `stabilization_seconds`
(30), then polls every `AEGIS_VERIFICATION_POLL_SECONDS` (5) taking the 30 s mean per condition. The
`timeout_seconds` deadline is computed *before* the stabilization sleep, so stabilization is spent
inside the budget. It returns `passed` after `consecutive_required = 3` all-green polls, `failed` when
`timeout_seconds` elapses (240 for plans, 150 for recovery without action) if any data was seen,
and `inconclusive` if telemetry was never available. A poll is all-green only when every condition
holds, unless `VerificationSpec.require_all` is false — then one passing condition passes the poll.
`before`/`after` snapshots are stored on the plan and rendered as `changes` in the
`verification.passed|failed` event. A no-action conclusion runs the same engine
(`verify_recovery`) before the incident may become `resolved`.

## Detection

`AnomalyDetector` (`src/aegis/detection/detector.py`) keeps a `RollingBaseline` (EWMA mean and
variance) per (component, metric) and a state per rule. A `MetricRule` (`rules.py`) fires when its
statistical trigger (z-score ≥ 3 plus an optional minimum ratio or delta) or its absolute trigger
holds for `min_consecutive` samples, and stops firing only after `clear_after` consecutive normal
samples (6 by default, applied in `detector.py`); the baseline is frozen while a rule is
firing or confirming (at most 20 min) so an anomaly never becomes the new normal. The eleven default
rules cover `latency_p95_ms`, `error_rate`, `up`, `connections`, `saturation`, `idle_in_transaction`,
`cpu_percent`, `memory_percent`, `request_rate`, `db_pool_wait_ms`, `redis_pool_wait_ms`.
`IncidentCorrelator` (`src/aegis/detection/correlation.py`) groups signals within 180 s that share a
service or are connected in the topology, attaches them to an active incident that touches a related
service and is younger than `max_attach_age_seconds` (3600), and otherwise opens a new one with a
severity ladder (gateway unavailable or gateway errors ≥ 50% → `sev1`; gateway errors
≥ 5%, gateway latency ≥ 3× baseline, database/cache saturation ≥ 0.9, or a tier ≤ 1 component
unavailable → `sev2`; any availability, error ≥ 5%, latency ≥ 2× or saturation signal → `sev3`;
else `sev4`). `DetectionEngine.bootstrap` restores baselines from Redis or feeds 600 s of history at
start; state is saved to Redis every 12 cycles with a 24 h TTL.

## Data model

Postgres is the system of record (`src/aegis/infrastructure/postgres/models.py`, migration
`migrations/versions/0001_initial.py`). Each aggregate row stores the full Pydantic document as JSONB
(`document`) plus projection columns used for indexing and constraints:

| table | projections and constraints |
|---|---|
| `incidents` | `number` from sequence `incident_number_seq` (starts at 1042, so the first incident is `INC-1042`); `tenant_id`, `status`, `severity`, `environment`, `flow_name/version`, `correlation_key`, `workflow_id`, `detected_at`; **`version`** for optimistic locking |
| `incident_events` | `seq` per incident, unique `(incident_id, seq)`; the SSE ordering key |
| `evidence`, `evidence_relations` | relation unique on `(incident_id, from_id, to_id, kind)`, inserted with `ON CONFLICT DO NOTHING` |
| `hypotheses` | `status`, `confidence`, `root_cause_service` |
| `action_plans`, `action_executions` | `action_plans.idempotency_key` is projected but **not** unique; `action_executions.idempotency_key` unique (`<tool key>:action` / `:rollback`) |
| `approvals` | `status`, `requested_at`, `expires_at` |
| `audit_events` | `actor_id`, `event_type`, `decision`, `tool_name`, `incident_id`, `at`; **trigger `audit_events_immutable`** raises `audit_events is append-only` on UPDATE or DELETE |
| `agent_runs`, `agent_steps` | steps unique on `(agent_run_id, seq)` |
| `tool_executions` | **`idempotency_key` unique**: the exactly-once claim for mutations |
| `incident_memories` | unique `incident_id`; `embedding vector(1536)` with an HNSW cosine index; GIN `to_tsvector` index on `embedding_text` |
| `notifications` | `read`, `at` |
| `definitions` | snapshot table for flow/tool/policy definitions; present in the schema but not written by any code path yet |

`PostgresIncidentRepository.save` issues `UPDATE ... WHERE id = :id AND version = :expected` and
raises `ConcurrencyError` (HTTP 409 `concurrency_conflict`) when no row matches. `append_event`
allocates `seq = max + 1` inside a savepoint and retries up to five times on a unique violation.
Every unique-violation path wraps its insert in a SAVEPOINT (`session.begin_nested`) so an
`IntegrityError` does not poison the enclosing transaction, and `incident_memories.document` is
stored with `embedding` nulled out (the vector lives in its own column) and rehydrated only on
demand.
LangGraph's own checkpoint tables live in the same database (`AsyncPostgresSaver.setup()` at
worker start). For local runs Temporal's `temporal` and `temporal_visibility` databases are created
in the same instance by `infrastructure/postgres/init/01-temporal-databases.sh` — at the repository
root, not under `src/aegis/` — which `docker-compose.yml` mounts into
`/docker-entrypoint-initdb.d`.

Redis (`src/aegis/infrastructure/redis/adapters.py`) carries only derived or transient state: event
pub/sub (`aegis:events`, `aegis:events:<incident_id>`), the detector leader lock
(`aegis:lock:detector-leader`, set-if-absent with a per-process token, renew/release by Lua
compare), fixed-window API rate-limit counters, and the detector's baselines
(`aegis:baseline:detector`). Losing Redis loses nothing that cannot be rebuilt.

## Telemetry sources

Detection, tools and verification read through the `TelemetryProvider` port
(`src/aegis/ports/telemetry.py`); mutations go through `InfrastructureGateway`. Adapters:
`HttpSimulatorTelemetry`/`HttpSimulatorGateway` (default), `InProcessSimulator*` (tests and evals),
and `PrometheusTelemetryProvider` (`AEGIS_TELEMETRY_PROVIDER=prometheus`), which answers
`metrics()` and `baseline()` with PromQL `query_range` over the simulator's exported
`sim_service_<metric>{service=...}` / `sim_infra_<metric>{component=...}` series (baseline = median
of the previous hour excluding the last 15 minutes) and delegates logs, traces, health, deployments,
resources, topology and bulk snapshots to the simulator adapter.

## Observability

Structured JSON logs with secret redaction and correlation context (`aegis.logging`: `request_id`,
`incident_id`, `workflow_id`, `agent_run_id`); Prometheus metrics `aegis_*`
(`src/aegis/telemetry/metrics.py`) served at API `/metrics`, worker `:9464`, detector `:9465`;
optional OpenTelemetry traces (`AEGIS_OTEL_ENABLED=true`) with FastAPI, SQLAlchemy, httpx and Temporal
instrumentation plus manual spans `aegis.tool.execute` and `aegis.llm.complete`; and, at product
level, the append-only incident timeline (`incident_events`) streamed over SSE and the immutable
`audit_events`. Details in `docs/adr/ADR-007-observability.md` and `docs/operations/deployment.md`.
