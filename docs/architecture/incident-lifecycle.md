# Incident lifecycle

## States

`IncidentStatus` lives in `src/aegis/domain/enums.py`. `is_active` is true for every status except
`resolved`, `closed` and `failed`; tools may only run for active incidents (authorization check 3).
Only `closed` is terminal.

| status | meaning | set by |
|---|---|---|
| `detected` | incident row created by the detector; workflow not started or not yet triaged | detector (`IncidentIntakeService.open_incident`) |
| `triaging` | `triage_incident` selected the flow pack; `acknowledged_at` stamped | workflow |
| `investigating` | agent is in the `investigate` phase; also the reopen target | workflow; human (reopen) |
| `hypothesis_formed` | agent is in the `hypothesize` phase | workflow |
| `validating` | agent is in the `validate` phase; remains through the `remediate` phase | workflow |
| `remediation_planned` | an `ActionPlan` exists and policy is being evaluated | workflow |
| `awaiting_approval` | policy returned `require_approval`; an `ApprovalRequest` is pending | workflow |
| `remediating` | `execute_remediation` is running | workflow |
| `verifying` | `verify_remediation` or `verify_recovery` is running | workflow |
| `rolled_back` | verification failed and the plan's rollback ran (or was attempted) | workflow |
| `resolved` | verification passed (with or without an action), or a human resolved an escalated incident | workflow; human (from `escalated`) |
| `escalated` | handed to humans: policy deny, approval timeout, rejections or verification attempts exhausted, budget exhausted, agent asked, or `max_phases` reached | workflow |
| `failed` | an agent phase or the remediation activity failed after retries | workflow |
| `closed` | terminal: human close, or the workflow was cancelled | human; workflow (cancel) |

## Transition table

`TRANSITIONS` in `src/aegis/domain/statemachine.py` is a closed table. `transition()` validates the
edge, then the actor, then stamps timestamps.

```
detected            → triaging, closed
triaging            → investigating, verifying, escalated, failed, closed
investigating       → hypothesis_formed, verifying, escalated, failed, closed
hypothesis_formed   → validating, investigating, verifying, escalated, failed, closed
validating          → remediation_planned, hypothesis_formed, investigating, verifying, escalated, failed, closed
remediation_planned → awaiting_approval, remediating, escalated, failed, investigating, closed
awaiting_approval   → remediating, remediation_planned, escalated, failed, closed
remediating         → verifying, rolled_back, escalated, failed, closed
verifying           → resolved, rolled_back, remediation_planned, investigating, escalated, failed, closed
rolled_back         → remediation_planned, investigating, escalated, failed, closed
resolved            → closed, investigating
escalated           → investigating, resolved, closed, remediation_planned
failed              → closed, escalated, investigating
closed              → (none)
```

`closed` is in **every** non-closed frozenset, which is what makes the force-close below universal.

Who may request a transition:

- **The agent: never.** `assert_transition` raises `InvalidTransitionError` for any
  `ActorKind.AGENT` actor on any edge. The agent only returns a `PhaseOutcome`; the workflow
  translates it into status changes (`tests/unit/test_statemachine.py::test_agent_can_never_transition`).
- **Humans** (`Actor.human` via the API, operator role required): only the pairs in
  `HUMAN_TRANSITIONS`, which is *generated*, not hand-listed — `(status, closed)` for all 13
  non-closed statuses, plus `escalated→resolved`, `escalated→investigating` and
  `resolved→investigating`: 16 pairs. So **an operator may force-close from any non-closed state**,
  including `remediating` and `verifying`; there is no state that answers 409 until the runtime moves
  on. The only human edges that are not a close are the three reopen/resolve pairs, so e.g.
  `verifying→resolved` is still refused with HTTP 409 `invalid_transition` ("reserved for the
  runtime"). Endpoints:
  `POST /api/v1/incidents/{id}/close|resolve|reopen` (`src/aegis/api/v1/incidents.py` →
  `IncidentService.change_status`). Closing an active incident also signals `cancel` to the
  workflow. Reopening changes the status only; it does not start a new workflow.
  `POST /api/v1/incidents/{id}/acknowledge` (`IncidentService.acknowledge`) is the one human action
  with no transition at all: it stamps `acknowledged_at` if unset and writes an
  `incident.status_changed` timeline event plus an `incident.acknowledged` audit event, leaving
  `incident.status` untouched.
- **The runtime** (`Actor.workflow`, `Actor.detector`, `Actor.system`): every edge in the table.
  The detector only ever creates incidents in `detected`.

Timestamps: `triaging` sets `acknowledged_at` if unset; `resolved` sets `resolved_at`; `closed` sets
`closed_at`, and additionally back-fills `resolved_at` when the previous status was `resolved` and
`resolved_at` was still unset; a reopen to `investigating` from `resolved`, `escalated` or `failed`
clears `resolved_at` and `closed_at`. `Incident.version` is owned by the repository, not by
`transition()`.

## Phase → status mapping

The workflow (`src/aegis/workflows/incident_workflow.py`) maps agent phases to statuses through the
`set_incident_status` activity:

| workflow step | status |
|---|---|
| `triage_incident` | `detected → triaging` |
| entering phase `investigate` | `investigating` |
| entering phase `hypothesize` | `hypothesis_formed` |
| entering phase `validate` | `validating` |
| entering phase `remediate` | unchanged (`PHASE_STATUS` has no entry; normally still `validating`) |
| agent decision `action_planned` | `remediation_planned` |
| policy `require_approval` | `awaiting_approval` |
| approval granted, or policy `allow` | `remediating` |
| execution `succeeded` or `skipped_duplicate` | `verifying` |
| verification failed and a rollback is available | `rolled_back`, then `remediation_planned` on replan |
| `no_action` decision, or flow transition into `verify` | `verifying` (via `verify_recovery`) |
| `finalize_incident` | `resolved` / `escalated` / `failed` / `closed` |

`set_incident_status` is idempotent (same status → no-op) and swallows `InvalidTransitionError`
with a `workflow.transition_skipped` warning; the workflow keeps going. `finalize_incident` behaves
the same and still writes `resolution_summary`, the timeline event, the audit event, the
notification and (for `resolved`/`escalated`) the incident memory.

## Flows

**Approval.** `evaluate_remediation_policy` builds a `PolicyContext` with `in_agent_loop=False`,
`actor=workflow` and the plan's risk (`risk_for` raises it to at least `medium` on `sev1`). On
`require_approval` it creates an `ApprovalRequest` with `expires_at = now +
AEGIS_APPROVAL_TIMEOUT_SECONDS` (default 900), marks the plan `awaiting_approval`, emits
`approval.requested` and an `approval_requested` notification, and the workflow waits on the
`approval_decided` or `cancel` signal. `POST /api/v1/approvals/{id}/approve|reject`
(`ApprovalService.decide`, `src/aegis/application/approvals.py`): agent actors are refused with
`ForbiddenError`, the operator role is required, a non-pending approval is 409, an expired one is
marked `expired` and 409. The decision is persisted, `approval.decided` timeline and audit events are
written, then the workflow is signalled. The workflow ignores a signal whose `approval_id` is not the
one it is waiting for. At execution time the authorizer re-checks the approval object (status
`approved`, same `action_plan_id`) at check 11.

**Rejection.** `mark_plan(rejected)`. If `attempt < max_remediation_attempts` (2 in the shipped
packs) and the cycle guard allows it (a third entry into `remediate` escalates as cycling instead),
the workflow re-enters phase `remediate` with the feedback "the previous remediation plan was
rejected by <actor>: <reason>. Propose a different plan." A new plan gets a new approval. Otherwise
the outcome is `escalated` ("remediation rejected")
(`tests/workflow/test_incident_workflow.py::test_rejection_leads_to_replan_then_escalation`).

**Approval timeout.** `wait_condition` times out → `expire_approval` marks the approval `expired`
and the plan `rejected` → `escalated` ("approval request timed out").

**Policy deny.** The plan becomes `policy_denied` and the incident `escalated` with the policy
reason. Nothing executes.

**Execution.** `capture_metrics` snapshots the plan's verification metrics (`before`).
`execute_remediation` runs the plan through `ToolExecutor` with `in_agent_loop=False` and the
approval object; `succeeded` and `skipped_duplicate` continue to verification; `denied`, `failed`
and `timed_out` → `escalated`; an `ActivityError` → `failed`. A `succeeded` mutation increments
`incident.remediation_attempts`, and an `ActionExecution` row is written with a unique key.

**Verification failure and rollback.** `verify_remediation` returning `failed` or `inconclusive`
marks the plan `verification_failed`. If `plan.rollback.available` the status becomes `rolled_back`
and `execute_remediation(is_rollback=True)` runs the rollback tool through the same authorization
path. Only `rollback_deployment` and `scale_service` have default rollbacks, and only once the
execution result is known (`default_rollback` needs `from_version`/`from_replicas`); restarts, pool
rotations and cache clears are declared irreversible. If attempts remain the workflow replans in
phase `remediate` with feedback ("remediation attempt N failed verification: ...", "rollback
<status>"); otherwise → `escalated`.

**Verification pass.** The plan becomes `verified`, the hypothesis `confirmed` with confidence
≥ 0.9, `incident.root_cause_summary` is set, and `finalize_incident(resolved)` stamps `resolved_at`,
observes `aegis_incident_resolution_seconds`, writes `incident.resolved` (timeline and audit), sends
an `incident_resolved` notification, and stores an incident memory (LLM `fast`-tier summary and
embedding when available; the memory is still stored without them).

**No action (false positive).** Three things can produce a `no_action` outcome, and only one of them
comes from the model. `AgentRuntime._observe` calls `_signal_status(incident)` once per iteration and
returns `decision="no_action"` *itself* when every detection-signal condition holds on the 45 s
window mean — including the accumulation guard, so a metric still climbing counts as anomalous even
while it is under its threshold — no affected service or gateway went `up < 1` in the last 120 s,
`iteration >= 1`, the phase does not plan remediation and nothing has been remediated — the runtime
owns the recovery call, and the model cannot keep the incident open past it. The same check guards the
model's `conclude_no_action`, rejecting it with the anomalous lines as feedback while metrics are
still out of band. A flow transition into `verify` is the third route. All three run `verify_recovery`
over the symptom metrics (30 s stabilization, 150 s timeout). Pass → `resolved` with memory
`outcome=false_positive` and no action plan; fail → feedback and back to `investigate`, or, if
`investigate` has already been entered twice, escalation as cycling
(`test_transient_spike_resolves_without_action`).

### Re-observation: a symptom that has not developed yet

Detection is deliberately early, which means a phase can run out of things to look at while the
fault is still too small to attribute — a connection leak twenty seconds in reads as 23 % pool
saturation and implicates nobody. Before, the agent kept being told "keep investigating", re-ran the
same diagnostics and escalated on an exhausted budget.

Now the phase gives up quickly and says why. After `MAX_STALLED_ITERATIONS = 2` consecutive
`phase_complete` proposals that add no evidence, the agent returns
`TerminationReason.INSUFFICIENT_SIGNAL` with the unmet exit conditions in its summary. The workflow
— which is where durable waiting belongs — waits `REOBSERVE_SECONDS` (45 s) on a Temporal timer and
re-enters the *same* phase, up to `MAX_REOBSERVATIONS` (3). A re-observation is the same visit: it
does not append to `WorkflowStatus.phases`, so the cycle guard is untouched, and it does not re-emit
the phase's status change. The waiting incident is queryable (`WorkflowStatus.reobservations`) and a
cancel during the wait is honoured by the loop's own guard.

If the symptom still cannot be diagnosed after the last re-observation, the incident escalates with
"no diagnosable signal after 3 re-observations over 135s" — a human takes a fault that is real but
not yet legible. If it develops in the meantime, the next pass sees 100 % saturation and the
investigation proceeds normally
(`tests/workflow/test_incident_workflow.py::test_a_symptom_that_never_develops_is_re_observed_then_escalated`,
`tests/unit/test_agent_runtime.py::test_phase_gives_up_instead_of_spinning_when_the_signal_has_not_developed`).

**Escalation** means the workflow has finished and a human owns the incident. Triggers: the flow
transitions to the `escalate` phase; the agent returns `escalate`; the budget is exhausted; policy
denied the plan; the approval timed out; rejections or failed verifications exhausted
`max_remediation_attempts`; `max_phases` (14) was reached; re-observations were exhausted; or the
investigation is *cycling* —
`MAX_PHASE_ENTRIES = 2` (`src/aegis/workflows/incident_workflow.py`), so a third entry into the same
phase escalates with "investigation is cycling on phase X without reaching a remediation" rather
than burning the remaining phases. The incident stays `is_active`, so read-only tools would still
pass check 3, but no workflow is running. Humans may `resolve`, `close` or `reopen` it.

**Cancel.** A human `close` on an active incident signals `cancel`. The workflow notices at the next
phase boundary or while waiting for approval (`expire_approval` with `cancelled`), finalizes with
outcome `closed`, and skips the transition when the incident is already `closed`
(`test_cancel_signal_closes_incident`).

**Failure.** `run_agent_phase` failing after `AGENT_RETRY` (3 attempts), or `execute_remediation`
raising, gives outcome `failed`. Humans may close or reopen.

## Worked timeline: INC-1042

Recorded facts from the run: first incident on a fresh database (`incident_number_seq` starts at
1042), scenario `redis-connection-leak`, detected 21 s after injection, five agent phases with
`gpt-5-mini` as the reasoner, plan `restart_service(service=order-service)`, one human approval,
24 verification conditions, `resolved` 136 s after detection. The intermediate offsets below are
derived from the code paths and the simulator's fixed timings (10 s restart, 5 s detection interval);
they are not taken from the run log.

| t (s) | actor | status | timeline events | what happened |
|---|---|---|---|---|
| −21 | operator | — | — | `POST /api/v1/simulation/faults {"scenario_id": "redis-connection-leak"}`; order-service starts leaking Redis connections at 2.5/s |
| 0 | detector | `detected` | `incident.detected`, `incident.created`, `flow.selected` | the `connections:saturation` rule fires on `redis` after 3 consecutive 5 s samples and is correlated with the gateway/order-service signals; `FlowRegistry.select` picks the pack whose `applies_to` best matches the signal kinds (`api-latency-investigation@1.2.0` for `latency`/`saturation`); workflow `incident-<uuid>` started |
| ~1 | workflow | `triaging` | `incident.status_changed`, `flow.phase_entered` | `triage_incident` |
| ~1–15 | agent run 1 (`triage`) | `triaging` | `agent.run_started`, `agent.step` ×n, `tool.executed`, `evidence.collected`, `flow.phase_exited`, `agent.run_finished` | `get_health`, `compare_baseline`, `inspect_dependencies`; exit condition `min_evidence >= 3` |
| ~15–50 | agent run 2 (`investigate`) | `investigating` | as above plus `hypothesis.created` | `inspect_redis` shows order-service holding most connections with a `leaked_by_service` entry; `inspect_dependencies`, `query_traces`, `get_logs` ("redis pool exhausted"); hypothesis H1 `resource_exhaustion` on `order-service` |
| ~50–60 | agent run 3 (`hypothesize`) | `hypothesis_formed` | `hypothesis.updated` | rescoring: the attribution rule credits order-service with the redis saturation; confidence ≥ 0.55 → `validate` |
| ~60–70 | agent run 4 (`validate`) | `validating` | `tool.executed`, `hypothesis.validated` | `run_cache_diagnostic(redis)` with `tests_hypothesis=H1`; the judge confirms (the diagnostic implicates order-service with strength 0.9) → H1 `confirmed` |
| ~70–75 | agent run 5 (`remediate`) | `validating` | `remediation.proposed` | `plan_remediation`: `restart_service(order-service)` passes the target and fit checks; the runtime builds 24 verification conditions (signal metrics on the affected services plus `order-service` `error_rate`, `latency_p95_ms`, `up`); rollback unavailable (restart is irreversible) |
| ~75 | workflow | `remediation_planned` → `awaiting_approval` | `policy.decided`, `approval.requested`, notification | rule `approve-medium-risk-mutations` (restart is `medium` risk; development environment; not `sev1`) |
| ~78 | human | `awaiting_approval` | `approval.decided` | `POST /api/v1/approvals/{id}/approve`; the workflow is signalled |
| ~78 | workflow | `remediating` | `remediation.started`, `tool.executed`, `remediation.completed` | `capture_metrics` (before snapshot); `execute_remediation` passes all 14 checks (check 11 sees the approval bound to this plan); the simulator restarts order-service (`eta 10 s`) and its leaked connections drop to zero |
| ~79 | workflow | `verifying` | `verification.started` | 30 s stabilization, then 5 s polls over a 30 s mean window |
| ~79–136 | workflow | `verifying` | — | order-service is `up` again after 10 s; redis connections return within 1.6× baseline and gateway p95 within 1.5×; three consecutive all-green polls are required |
| 136 | workflow | `resolved` | `verification.passed` (with `before`/`after`/`changes`), `incident.resolved`, `memory.stored` | plan `verified`, H1 confidence ≥ 0.9, `resolved_at` set, memory row written with its embedding, `incident_resolved` notification |

To check the same numbers on your own run: `GET /api/v1/incidents/{id}` →
`action_plans[0].verification.conditions` (count) and `verification_result.summary` ("all N
conditions held for 3 consecutive polls"), `agent_runs` (five rows, `model: gpt-5-mini`),
`approvals[0].decided_by`; and `GET /api/v1/incidents/{id}/audit` for the ordered check trace of the
`tool.executed` event.

## Known gaps in the state table

- `conclude_no_action` is an allowed action in **all five** non-terminal phases — `triage`,
  `investigate`, `hypothesize`, `validate` and `remediate` (`PHASE_ACTIONS` in
  `src/aegis/agent/prompts.py`). The old gap here is closed: `TRANSITIONS` now carries
  `triaging→verifying` and `hypothesis_formed→verifying`, so all four investigation phase statuses
  (`triaging`, `investigating`, `hypothesis_formed`, `validating`) reach `verifying`, and the
  no-action path `→ verifying → resolved` is legal from every one of them — including a first pass
  through `remediate`, where the status is still `validating`. What remains is narrower: on a
  *replan* into `remediate` the status is `awaiting_approval` (after a rejection) or `rolled_back`
  (after a failed verification), and neither has a `→ verifying` edge, so a no-action conclusion
  there still finishes with outcome `resolved` behind `transition_skipped` warnings while
  `incident.status` stays put.
- A human `reopen` (`escalated|resolved → investigating`) changes the status only. No workflow is
  started, and `WorkflowIDReusePolicy.ALLOW_DUPLICATE_FAILED_ONLY` would refuse a second workflow
  with the same id unless the first one failed.
