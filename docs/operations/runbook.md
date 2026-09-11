# Operating Aegis

The console at :3600 and the API at :8600 show the same data; this runbook uses the API so it works
from a terminal. With `AEGIS_API_AUTH_MODE=api_key`, add `-H 'X-Aegis-Key: <key>'` (or
`Authorization: Bearer <key>`) to every call; write operations need the `operator` role. A third
credential path exists for streams only: `?key=<key>` is accepted on paths ending in `/stream`
(`src/aegis/api/deps.py`), because the browser's `EventSource` cannot set headers.

## Following an incident

```bash
curl -s 'localhost:8600/api/v1/incidents?active=true' | jq '.items[] | {display_id,status,severity,title}'
curl -s localhost:8600/api/v1/incidents/<id> | jq '{incident, hypotheses, action_plans, approvals, agent_runs}'
curl -N 'localhost:8600/api/v1/incidents/<id>/stream?key=<key>'   # SSE: replay then live
curl -s localhost:8600/api/v1/incidents/<id>/timeline | jq '.[] | "\(.seq) \(.type) \(.title)"'
```

Shapes differ: `/incidents` is a page (`items`, `total`, `limit`, `offset`; limit 50 by default, 200
at most), while `/timeline` (500, max 2000) and `/audit` (200, max 1000) return bare lists. The
incident detail payload carries more than the five keys above — also `evidence`, `tool_executions`
and `recent_events`. Both streaming endpoints (`/incidents/{id}/stream` and `/events/stream`) resume
from the `Last-Event-ID` header *or* an explicit `?after_seq=<seq>`; when both are present the
higher wins.

Statuses and what they mean are in `docs/architecture/incident-lifecycle.md`. The states that need
you: `awaiting_approval` (decide), `escalated` (the runtime has stopped; you own it), `failed`
(inspect the workflow).

## Approving and rejecting

```bash
curl -s localhost:8600/api/v1/approvals | jq            # pending approvals
curl -s localhost:8600/api/v1/approvals/<approval_id> | jq   # plan, hypothesis, evidence, incident
curl -s -X POST localhost:8600/api/v1/approvals/<approval_id>/approve -H 'content-type: application/json' \
     -d '{"reason": "root cause confirmed, restart is safe"}'
curl -s -X POST localhost:8600/api/v1/approvals/<approval_id>/reject  -H 'content-type: application/json' \
     -d '{"reason": "not during the payment batch window"}'
```

What to read before approving: `action_plan.tool_name` and `arguments` (target service), `risk`,
`hypothesis.confidence` and `score.explanation` (why the runtime believes it), `evidence` (up to 12
supporting items), `action_plan.rollback` (`available: false` for restarts and pool rotations),
`action_plan.verification.conditions` (what will be measured), `approval.expires_at`.

Rules: the agent cannot approve (403 even if it tried); a decided or expired approval returns 409;
a rejection with attempts left (`max_remediation_attempts`, 2) sends the agent back to plan a
*different* remediation with your reason in its feedback, a second rejection escalates; no decision
before `AEGIS_APPROVAL_TIMEOUT_SECONDS` (900) expires the approval and escalates. Approvals are
bound to one action plan; a replanned incident gets a new approval.

## What escalation means

`escalated` = the workflow has completed with outcome `escalated` and no automation will touch the
incident again. The incident keeps everything collected (evidence graph, hypotheses with scores,
plans with policy decisions, verification results). Reasons appear in `incident.resolution_summary`
and the last `incident.escalated` event: policy denied the plan, approval timed out or was rejected
twice, verification failed after the allowed attempts (with rollback if one existed), the budget was
exhausted, the flow reached its `escalate` phase, the investigation was cycling, or the agent asked.
After fixing things yourself:

```bash
curl -s -X POST localhost:8600/api/v1/incidents/<id>/resolve -d '{"reason": "rolled back manually"}' -H 'content-type: application/json'
curl -s -X POST localhost:8600/api/v1/incidents/<id>/close   -d '{"reason": "duplicate of INC-1050"}' -H 'content-type: application/json'
```

`reopen` (`POST .../reopen`) moves an `escalated`/`resolved` incident to `investigating` for
record-keeping only; it does not start a new workflow. An operator may force-close from **any**
non-closed state: `HUMAN_TRANSITIONS` (`src/aegis/domain/statemachine.py`) is generated as
`(status, closed)` for every status, so `hypothesis_formed`, `validating`, `remediation_planned`,
`remediating`, `verifying` and `rolled_back` close too — you never have to wait for the runtime to
move on. The only other transitions a human may request are `escalated` → `resolved`, `escalated` →
`investigating` and `resolved` → `investigating`; anything else is refused with 409
`invalid_transition` ("reserved for the runtime"). Closing an active incident cancels its workflow.

## Reading the ledger

- `GET /api/v1/incidents/{id}/tool-executions`: every tool request, executed or refused. `status`:
  `running`, `succeeded`, `failed`, `timed_out`, `denied`, `skipped_duplicate` (the enum also has
  `pending`, the model default, which the executor never writes). `running` is inserted *before* the
  gateway call and is visible in the ledger while a tool is in flight; it is the row authorization
  check 13 refuses a concurrent duplicate on. `authorization.checks` is the ordered gate trace
  (14 names, truncated at the failing one), `authorization.denial_code` the reason class,
  `authorization.matched_policy` the rule. `category: mutating` rows are the remediations;
  `agent_run_id` is null on them because the workflow, not the agent, requested them. Keys prefixed
  `denied:` or `dup:` were never claims.
- `GET /api/v1/incidents/{id}/audit`: immutable `audit_events` (`incident.created`, `tool.denied`,
  `tool.executed`, `policy.decided`, `approval.decided`, `incident.resolved`, ...) with the actor and,
  for tool events, `data.checks`. The database refuses `UPDATE`/`DELETE` on this table.
- `GET /api/v1/incidents/{id}/actions`: action plans with `status`, `policy_decision`,
  `idempotency_key`, `verification_result.before/after/condition_results`.
- `GET /api/v1/agent-runs/{run_id}`: the agent's steps for one phase. The nine `kind` values are
  `observation`, `proposal` or `fallback` (= who decided), `authorization`, `tool_execution`,
  `evidence_update`, `hypothesis_update`, `remediation_plan` and `decision`, with model, tokens and
  latency per step. A step with no model attached is the runtime deciding rather than the agent —
  see *What the runtime decides without the model* below.
- Logs are JSON (`AEGIS_LOG_FORMAT=json`); filter by `incident_id`, `workflow_id`, `agent_run_id`
  or `request_id` (returned in every API response as `x-request-id`).

## What the runtime decides without the model

Two decisions that look like the model's when you read an incident back are actually the runtime's.

**"The symptoms are gone."** `AgentRuntime._observe` (`src/aegis/agent/runtime.py`) calls
`_signal_status` once per iteration, before the model is consulted. It rebuilds the same
verification spec the workflow will apply later and requires *every* detection-signal condition to
hold on the 45-second window mean; an accumulating metric still climbing across that window (more
than 10 % of its threshold between the first and last quarter) counts as anomalous even when the
mean looks acceptable, and any affected service that went `up < 1` in the last 120 s is treated as
unstable — a crash loop looks healthy for a minute after every restart. When all of that holds and
nothing has been remediated, the phase concludes `no_action` on its own and emits an `agent.step`
event titled "Runtime: all detection signals returned within baseline without remediation", with no
model call behind it. The converse also holds: a model proposing `conclude_no_action` while metrics
are still anomalous is rejected ("cannot conclude 'no action': metrics are still anomalous: ...")
and the refusal goes back into its feedback.

**"That remediation does not fit."** `validate_remediation_fit` (`src/aegis/remediation/planning.py`)
refuses a plan that does not address the diagnosed mechanism, not merely the right service: rolling
back something that was not recently deployed, restarting something that is simply out of capacity,
scaling something that is leaking connections. `_effective_category` lets the evidence override a
mislabelled hypothesis category — a target pinned at the CPU limit with no memory pressure and no
leaked state is a `capacity` problem however the hypothesis described it, so `restart_service` and
`rotate_connection_pool` are refused ("Restarting returns the same replicas to the same load; use
scale_service instead") and `scale_service` is what the agent has to propose. The refusal text
becomes agent feedback and stays in the ledger, so an incident where the model kept reaching for a
restart reads as a run of refusals rather than as a bad remediation.

## Inspecting a workflow in Temporal

Every incident has one workflow, id `incident-<incident uuid>` (`incident.workflow_id`), namespace
`default`, task queue `aegis-incidents`.

- Quick view: `GET /api/v1/incidents/{id}/workflow` returns Temporal's describe (`status`
  `RUNNING`/`COMPLETED`/..., `run_id`, `start_time`, `close_time`) plus the workflow's own query
  while running: `{phase, status, awaiting_approval_id, remediation_attempts, phases[], cancelled}`.
- Temporal UI at http://localhost:8233 → Workflows → search the id. The history shows the activities
  in order (`triage_incident`, `set_incident_status`, `run_agent_phase`,
  `evaluate_remediation_policy`, `expire_approval`, `mark_plan`, `capture_metrics`,
  `execute_remediation`, `verify_remediation`, `verify_recovery`, `finalize_incident`), the
  `approval_decided` and `cancel` signals, activity retries and heartbeat details. A pending
  `run_agent_phase` with recent heartbeats (node names) is the agent working; heartbeats stopping for
  90 s means the activity will be retried and the phase resumed from its checkpoint.
- With the Temporal CLI: `temporal workflow describe -w incident-<uuid>`, `temporal workflow show
  -w incident-<uuid>`. Signal names are `approval_decided` (payload `ApprovalSignal`:
  `approval_id`, `approved`, `decided_by`, `reason`) and `cancel` (string reason).

## Common failure modes

**LLM unavailable or misbehaving.** The OpenAI provider opens its circuit breaker after 3
transport failures (60 s, then a single probe). `GET /api/v1/system/info` shows `llm.healthy`. A
phase that starts while the breaker is open, or that hits an `LLMError` mid-way, continues with the
`DeterministicPlanner`: the timeline shows `llm.fallback`, agent steps have `kind: fallback` and
`model: deterministic-planner`, and `aegis_llm_calls_total{outcome!="ok"}` rises. Investigations
still reach a plan for the standard scenarios (that is the `agent-deterministic` eval), but with less
discrimination. A model that keeps proposing illegal actions is also handed to the planner after two
invalid proposals in a row; its refusals are visible as `tool.denied` events. Nothing needs restarting;
fix the key/endpoint and the breaker closes on the next successful probe.

**Temporal down.** Detection keeps working: the detector still opens incidents in `detected`, writes
the timeline and notifications, and logs `intake.workflow_start_failed`; `incident.workflow_id`
stays null and no investigation starts. `/ready` reports `temporal: ok=false`. When Temporal is back,
new incidents proceed normally, but incidents created during the outage are **not** picked up
automatically; close them or start their workflow manually with the Temporal CLI
(`IncidentWorkflow`, input `{"incident_id": "<uuid>"}`, id `incident-<uuid>`, task queue
`aegis-incidents`). An approval decided during the outage is stored as approved but the signal to the
workflow fails (the API returns 500 after committing); the workflow will time out and escalate unless
you send the `approval_decided` signal by hand with the same `approval_id`.

**Simulator (telemetry source) down.** `/ready` → 503. The detector logs `detector.cycle_failed`
each interval, marks itself un-bootstrapped and re-seeds baselines when telemetry returns. Running
agent phases get `InfrastructureError`s: read tools retry up to 3 times then record `failed` and the
model sees the failure; verification returns `inconclusive`, which is treated as a failed
verification (rollback if available, replan or escalate). Restarting the simulator resets its world,
so active faults vanish and metrics recover, which the runtime will read as recovery.

**Redis down.** `/ready` → 503 (takes API replicas out of rotation). No detector can hold the leader
lock, so detection pauses; workers keep executing (event publish failures do not fail activities for
`AegisError`/`OSError` classes; watch the logs). API reads work — rate limiting falls back to each
replica's in-process counter and logs `api.rate_limiter_unavailable`; SSE live updates stop while
replay still works (`tests/chaos/test_dependency_outage.py::test_redis_outage_does_not_break_api_reads`).

**Postgres unavailable.** Everything stops; activities fail and Temporal retries them with backoff
(`DB_RETRY`, 8 attempts). Once Postgres is back the workflow continues from where it was; nothing
needs manual replay.

**Worker crash mid-incident.** Safe by design: the next worker picks up the activity, the agent
resumes from its LangGraph checkpoint, and a remediation that already ran is recognised by its
idempotency key (`skipped_duplicate`). `tests/chaos/test_worker_crash.py` kills the worker with
SIGKILL during an investigation and asserts exactly one restart on the target.

**Schema drift.** `/ready` → `database: ok=false` with `revision` ≠ `head`. Run `uv run aegis
migrate` (or the compose `migrate` service) and restart.

**The investigation goes round in circles.** The workflow has a cycle guard: `MAX_PHASE_ENTRIES = 2`
(`src/aegis/workflows/incident_workflow.py`), so a third entry into the same phase does not happen.
Instead the workflow escalates with `investigation is cycling on phase <phase> without reaching a
remediation`, which you will see in `incident.resolution_summary`. It is a distinct escalation
cause from a budget exhaustion or a denied plan: the runtime kept getting usable answers but they
never converged on an action. The `phases[]` list in the workflow query shows the path it took.

## How idempotency protects against duplicate remediation

1. The agent produces an `ActionPlan` with `attempt = remediation_attempts + 1`; the plan id is fixed
   from then on. The plan derives a key of its own with scope `attempt:<n>`
   (`src/aegis/domain/action.py`), but the key that actually gates execution is the authorizer's,
   below.
2. `execute_remediation` builds the request with `action_plan_id`, so the authorizer derives the key
   `sha256("plan:<plan_id>|<incident>|<tool>|<canonical args>")[:32]` (check 13 refuses if an
   execution with that key is still `running`).
3. `ToolExecutor` inserts the `tool_executions` row with status `running` *before* calling the
   gateway. The column is unique, so two workers racing on the same plan cannot both insert; the
   loser records `skipped_duplicate`.
4. If the activity is retried after a success (worker died between the gateway call and the
   Temporal completion), the executor finds the prior `succeeded` row and returns
   `skipped_duplicate` with the stored result. The workflow treats `skipped_duplicate` like success
   and proceeds to verification.
5. `action_executions` is a second ledger: `execute_remediation` records each attempt with the tool
   execution's key plus an `:action` or `:rollback` suffix (`src/aegis/workflows/activities.py`).
   That column is unique too, so a replayed activity attempt is rejected and the duplicate row
   deliberately dropped. Rows whose key the executor rewrote carry those prefixes here as well
   (`denied:<request id>`, `dup:<request id>`).
6. A *new* attempt (after rejection or failed verification) is a new plan with a new key, so it is
   allowed to execute; `max_remediation_attempts` bounds how many.

The simulator's action log (`GET /api/v1/simulation/actions`) is the external witness in tests: one
`restart` entry per plan.

## Simulation controls (demo and staging)

```bash
uv run aegis scenarios                                   # or GET /api/v1/simulation/scenarios
uv run aegis inject bad-deployment --params '{"error_rate": 0.5}'
curl -s localhost:8600/api/v1/simulation/faults | jq      # active faults
curl -s -X DELETE localhost:8600/api/v1/simulation/faults/fault-3
curl -s -X POST localhost:8600/api/v1/simulation/control/advance -d '{"seconds": 60}' -H 'content-type: application/json'
curl -s -X POST localhost:8600/api/v1/simulation/control/reset   -d '{"seed": 7}' -H 'content-type: application/json'
```

Reset re-warms 300 s of clean history; run it between demos so baselines are healthy.
