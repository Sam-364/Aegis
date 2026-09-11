# Aegis API contract for the console

Verified against the running backend (2026-09-10). Machine-readable spec: `docs/api/openapi.json`
(regenerate with `make openapi`). Endpoints typed as `dict`/`list[dict]` have no OpenAPI schema; their
exact shapes are the Pydantic domain models documented here.

## Conventions

- All routers mount under `/api/v1` except `/health`, `/ready`, `/metrics` (root).
- Every field of a domain model is always present; absent values are `null`. No extra keys.
- Datetimes: domain models serialize as `2026-09-10T08:56:33.156177Z` (microseconds, `Z`). A few
  fields built with `.isoformat()` (`IncidentSummary.detected_at/resolved_at`, simulator timestamps,
  some event payloads) use `+00:00`. Parse both.
- UUIDs are lowercase dashed strings. Sets (`roles`, `allowed_tools`, `capabilities`, …) are JSON
  arrays in non-deterministic order — sort client-side.
- Errors are `application/problem+json`: `{type, title, status, detail, request_id}`. `type` values:
  `not_found`(404) `conflict`/`invalid_transition`/`concurrency_conflict`(409) `validation_error`(422,
  `detail.errors[{loc,msg}]`) `forbidden`(403) `unauthorized`(401) `rate_limited`(429, hand-rolled body
  without detail, header `retry-after: 60`) `infrastructure_error`/`llm_unavailable`(503)
  `internal_error`(500). Every response carries `x-request-id`.
- Auth: header `X-Aegis-Key: <key>` or `Authorization: Bearer <key>`. `AEGIS_API_AUTH_MODE=disabled`
  (default in dev) makes every caller `{id:"user:local-operator", display_name:"Local operator",
  roles:["admin"]}`. Roles: viewer < operator < admin. Mutating routes (acknowledge/close/resolve/
  reopen, approve/reject, simulation faults/control) require operator. On the two SSE stream routes
  only, the key may also be passed as `?key=<key>` because `EventSource` cannot set headers.
- Rate limit: 600 req/min per key or IP; `/health`, `/ready`, `/metrics` and `*stream*` exempt.

## Enums (string literals)

```
Environment: development | staging | production
Severity: sev1 | sev2 | sev3 | sev4            (sev1 most severe)
IncidentStatus: detected | triaging | investigating | hypothesis_formed | validating |
  remediation_planned | awaiting_approval | remediating | verifying | resolved | rolled_back |
  escalated | failed | closed
ToolCategory: read_only | diagnostic | mutating | dangerous
RiskLevel: none | low | medium | high | critical
PolicyEffect: allow | deny | require_approval
ApprovalStatus: pending | approved | rejected | expired | cancelled
HypothesisStatus: proposed | testing | supported | confirmed | refuted | abandoned
HypothesisCategory: resource_exhaustion | deployment_regression | dependency_failure | capacity |
  network | configuration | transient | unknown
ActionPlanStatus: proposed | policy_denied | awaiting_approval | approved | rejected | executing |
  executed | execution_failed | verified | verification_failed | rolled_back | superseded
ExecutionStatus: pending | running | succeeded | failed | timed_out | denied | skipped_duplicate
EvidenceKind: signal | metric | log | trace | topology | deployment | health | diagnostic | memory |
  observation | action_result | verification
RelationKind: supports | contradicts | caused_by | depends_on | correlated_with | observed_on | resolved_by
GraphNodeKind: evidence | hypothesis | service | action
AgentRunStatus: running | completed | failed | budget_exhausted | cancelled
TerminationReason: phase_complete | action_planned | no_action_required | escalate |
  budget_exhausted | insufficient_signal | llm_unavailable | incident_inactive | error
AgentStepKind: proposal | fallback | authorization | tool_execution | hypothesis_update |
  remediation_plan | decision   (observation / evidence_update exist but are never emitted)
SignalKind: latency | error_rate | saturation | traffic | availability | resource
ActorKind: system | detector | agent | workflow | human | api
Role: viewer | operator | admin
VerificationStatus: pending | passed | failed | inconclusive
HealthState: healthy | degraded | unhealthy | unknown
NotificationKind: incident_detected | approval_requested | incident_resolved | incident_escalated | policy_denial
Phases (all flow packs): triage, investigate, hypothesize, validate, remediate, verify (terminal), escalate (terminal)
Flow refs: incident-investigation@1.1.0, api-latency-investigation@1.2.0, error-rate-investigation@1.1.0
```

Event types (`IncidentEvent.type`, SSE `event:` name): incident.detected, incident.created,
incident.signal_attached, incident.status_changed, incident.resolved, incident.escalated,
incident.closed, incident.failed, flow.selected, flow.phase_entered, flow.phase_exited,
agent.run_started, agent.step, agent.run_finished, tool.requested, tool.authorized, tool.denied,
tool.executed, tool.failed, evidence.collected, hypothesis.created, hypothesis.updated,
hypothesis.validated, hypothesis.refuted, remediation.proposed, policy.decided, approval.requested,
approval.decided, approval.expired, remediation.started, remediation.completed, remediation.failed,
remediation.skipped_duplicate, verification.started, verification.passed, verification.failed,
rollback.started, rollback.completed, rollback.failed, memory.stored, budget.exhausted, llm.fallback.

## Shared types

```ts
interface Actor { kind: ActorKind; id: string; display_name: string | null; roles: Role[] }
// id examples: "detector", "agent:<run_id>", "workflow:<workflow_id>", "user:<name>", "system"

interface IncidentEvent {
  id: string; incident_id: string; seq: number;   // seq: 1-based per incident; SSE id
  type: string; at: string; actor: Actor; title: string;
  payload: Record<string, unknown>; trace_id: string | null;
}

interface ExecutionBudget { max_iterations: number; max_tool_calls: number; max_llm_calls: number;
  max_llm_tokens: number; max_runtime_seconds: number; max_remediation_attempts: number }
interface BudgetUsage { iterations: number; tool_calls: number; llm_calls: number;
  llm_tokens: number; runtime_seconds: number; remediation_attempts: number }
```

## Incidents

`GET /api/v1/incidents?status=a&status=b&severity=sev1&active=false&limit=50&offset=0`
(repeat params for multi-value; comma-separated → 422) → `Page<IncidentSummary>`:

```ts
interface Page<T> { items: T[]; total: number; limit: number; offset: number }
interface IncidentSummary { id: string; number: number; display_id: string; title: string;
  severity: Severity; status: IncidentStatus; affected_services: string[]; flow_name: string | null;
  detected_at: string; resolved_at: string | null; duration_seconds: number;
  leading_hypothesis_id: string | null; active_action_plan_id: string | null;
  root_cause_summary: string; signal_count: number; workflow_id: string | null }
```

`GET /api/v1/incidents/stats` → `{ by_status: Record<IncidentStatus, number>; total; active;
resolved; mttr_seconds: number | null; pending_approvals: number; memories: number }`

`GET /api/v1/incidents/{id}` → detail with exactly these keys:

```ts
interface IncidentDetail {
  incident: Incident; hypotheses: Hypothesis[] /* confidence desc */; action_plans: ActionPlan[];
  approvals: Approval[] /* requested_at desc */; agent_runs: AgentRun[] /* started_at asc */;
  evidence: { incident_id: string; evidence: Evidence[]; relations: EvidenceRelation[] } | null;
  tool_executions: ToolExecution[]; recent_events: IncidentEvent[] /* last 50, seq asc */;
}

interface Incident {  // NOTE: no display_id here — compute `INC-${number}`; no flow_ref — `${flow_name}@${flow_version}`
  created_at; updated_at; id; tenant_id; environment: Environment; number: number; title; summary;
  severity: Severity; status: IncidentStatus; flow_name: string | null; flow_version: string | null;
  correlation_key: string; affected_services: string[]; signals: AnomalySignal[];
  leading_hypothesis_id: string | null; active_action_plan_id: string | null;
  workflow_id: string | null; detected_at: string; acknowledged_at: string | null;
  resolved_at: string | null; closed_at: string | null; resolution_summary: string;
  root_cause_summary: string; remediation_attempts: number; version: number;
}
interface AnomalySignal { id; service: string; metric: string; kind: SignalKind; observed_value: number;
  baseline_value: number; deviation_sigma: number; detector: string; detected_at: string;
  window_seconds: number; description: string }   // baseline_value is the detector's baseline → chart band

interface Hypothesis { created_at; updated_at; id; incident_id; statement: string;
  category: HypothesisCategory; suspected_root_cause_service: string | null; mechanism: string;
  affected_services: string[]; status: HypothesisStatus; confidence: number /* 0..1 = score.total */;
  score: HypothesisScore; supporting_evidence_ids: string[]; contradicting_evidence_ids: string[];
  suggested_tests: {tool_name; arguments: object; expectation; would_confirm: boolean}[];
  tests: {id; hypothesis_id; tool_execution_id: string | null; tool_name; expectation;
          outcome: "confirmed"|"refuted"|"inconclusive"; detail; at}[];
  proposed_by: "llm" | "deterministic" | "memory"; agent_run_id: string | null; version: number }
interface HypothesisScore { evidence_strength; temporal_alignment; dependency_alignment;
  historical_similarity; contradiction_penalty; validation_bonus /* may be negative */; total;
  explanation: string[] }   // all numbers 0..1 except validation_bonus

interface ActionPlan { created_at; updated_at; id; incident_id; hypothesis_id: string | null;
  tool_name: string; tool_version: string; arguments: Record<string, unknown> /* service | component | target | replicas … */;
  reason: string; expected_effect: string; risk: RiskLevel; rollback: RollbackPlan;
  verification: VerificationSpec; timeout_seconds: number; status: ActionPlanStatus;
  idempotency_key: string; proposed_by: "llm" | "deterministic"; agent_run_id: string | null;
  approval_id: string | null; policy_decision: PolicyDecision | null; attempt: number;
  verification_result: VerificationResult | null }
interface RollbackPlan { available: boolean; tool_name: string | null; arguments: object; reason: string }
interface VerificationSpec { conditions: VerificationCondition[]; stabilization_seconds: number;
  timeout_seconds: number; require_all: boolean }
interface VerificationCondition { metric: string; service: string; comparator: "lt"|"lte"|"gt"|"gte";
  target: number | null; max_ratio_to_baseline: number | null; description: string }
interface PolicyDecision { effect: PolicyEffect; matched_rule: string | null; reason: string;
  invariant: string | null; evaluated_rules: string[] }
interface VerificationResult { status: VerificationStatus; checked_at: string;
  condition_results: {service; metric; ok: boolean; detail: string; value?: number; baseline?: number | null; description?: string}[];
  summary: string; before: Record<string, number>; after: Record<string, number> }  // keys `${service}.${metric}`

interface Approval { created_at; updated_at; id; incident_id; action_plan_id; status: ApprovalStatus;
  title: string /* "restart_service on order-service" */; summary: string; risk: RiskLevel;
  expected_impact: string; rollback_summary: string; hypothesis_id: string | null;
  hypothesis_statement: string; hypothesis_confidence: number; evidence_ids: string[];
  requested_by: string; requested_at: string; expires_at: string; decided_at: string | null;
  decided_by: string | null; decision_reason: string; context: Record<string, unknown> }

interface AgentRun { created_at; updated_at; id; incident_id; flow_name; flow_version; phase: string;
  status: AgentRunStatus; budget: ExecutionBudget; usage: BudgetUsage; model: string;
  started_at: string; finished_at: string | null; termination_reason: TerminationReason | null;
  summary: string; error: string | null; steps_count: number; workflow_id: string | null; attempt: number }

interface Evidence { created_at; updated_at; id; incident_id; kind: EvidenceKind; source: string;
  service: string | null; title: string; summary: string; data: Record<string, unknown>;
  strength: number /* 0..1 */; observed_at: string; tool_execution_id: string | null;
  agent_run_id: string | null; phase: string | null; tags: string[] }
interface EvidenceRelation { id; incident_id; from_id: string /* uuid or service NAME when from_kind==service */;
  from_kind: GraphNodeKind; to_id: string; to_kind: GraphNodeKind; kind: RelationKind; weight: number;
  created_at: string; created_by: string }
// emitted edges: evidence→hypothesis (supports|contradicts), hypothesis→service (caused_by),
// evidence→service (observed_on), hypothesis→action (resolved_by)

interface ToolExecution { created_at; updated_at; id; incident_id; request_id; tool_name; tool_version;
  category: ToolCategory; arguments: object; idempotency_key: string; status: ExecutionStatus;
  authorization: AuthorizationDecision; agent_run_id: string | null; action_plan_id: string | null;
  phase: string | null; attempt: number; started_at: string | null; finished_at: string | null;
  duration_ms: number | null; result: object; result_summary: string; error: string | null;
  evidence_ids: string[]; trace_id: string | null }
interface AuthorizationDecision { request_id; tool_name; effect: PolicyEffect; allowed: boolean;
  checks: {name: string; passed: boolean; detail: string}[]  /* ordered; truncated at the failing check */;
  denial_code: string | null; reason: string; matched_policy: string | null; decided_at: string }
// the 14 check names, in order: tool_registered, tool_enabled, incident_active, flow_allows,
// phase_allows, category_permitted, environment_allows, severity_allows, actor_permitted,
// risk_within_ceiling, policy_effect, arguments_valid, idempotency, budget_available
// denial codes: tool_not_registered, tool_disabled, incident_inactive, flow_violation, phase_violation,
// tool_not_allowed, risk_ceiling_exceeded, policy_violation, approval_required (effect require_approval),
// invalid_tool_arguments, duplicate_execution, execution_budget_exceeded
```

Other incident routes: `GET …/timeline?after_seq=0&limit=500` → `IncidentEvent[]`; `GET …/evidence`
→ evidence graph; `GET …/hypotheses`; `GET …/actions`; `GET …/approvals?status=`; `GET …/audit?limit=&offset=`
→ `AuditEvent[]` `{id; at; event_type; actor; incident_id; flow_name; phase; tool_name; action;
decision; reason; trace_id; agent_run_id; tool_execution_id; data}`; `GET …/agent-runs` →
`AgentRun[]`; `GET …/tool-executions`; `GET …/workflow` → `{workflow_id, status: null}` or
`{workflow_id, run_id, status: "RUNNING"|"COMPLETED"|…, start_time, close_time, task_queue,
query: {phase, status, awaiting_approval_id, remediation_attempts, phases[], cancelled} | null}`.
Human actions (operator): `POST …/acknowledge` (no body), `POST …/close|resolve|reopen` body
`{reason: string}` → `IncidentSummary`; illegal transitions → 409 `invalid_transition`.

### SSE

`GET /api/v1/incidents/{id}/stream?after_seq=N` — replays events with `seq > N` then follows.
Frame: `id: <seq>` / `event: <type>` / `data: <IncidentEvent JSON>`, lines separated by `\r\n`,
comment heartbeats `: ping - …` every 15s. `Last-Event-ID` is honoured, so `EventSource` reconnects
resume correctly. `GET /api/v1/events/stream` — global firehose, no replay, `id: <incident_id>:<seq>`.
Useful payloads: `agent.step {step_id, kind, action, observation, rationale, model, tokens}`;
`tool.denied {tool, arguments, denial_code, reason, checks[], tool_execution_id}`; `tool.executed
{tool, arguments, status, summary, tool_execution_id, evidence_ids, duration_ms}`;
`evidence.collected {evidence:[{id,kind,title,service,strength}]}`; `hypothesis.* {hypothesis_id,
statement, category, root_cause_service, status, confidence, score, supporting, contradicting}`
(+ `test` on validated/refuted); `remediation.proposed {action_plan_id, tool, arguments, risk, reason,
expected_effect, hypothesis_id, hypothesis, confidence, rollback, verification}`; `policy.decided
{action_plan_id, effect, matched_rule, reason, invariant, evaluated_rules}`; `approval.requested
{approval_id, action_plan_id, expires_at, risk, hypothesis, confidence}`; `approval.decided
{approval_id, action_plan_id, approved, reason}`; `verification.passed|failed {action_plan_id, status,
summary, before, after, changes: string[], conditions[]}`; `flow.phase_entered {phase, flow, budget}`;
`flow.phase_exited {phase, trigger, next_phase, facts}`; `incident.resolved|escalated|closed|failed
{from, to, summary, usage, duration_seconds}`; `budget.exhausted {reasons[]}`; `llm.fallback {error}`;
`memory.stored {memory_id, root_cause_service, outcome, lessons}`.

## Agent runs

`GET /api/v1/agent-runs?limit=50` → `AgentRun[]` (started_at desc; no incident display id — join
with the incidents list). `GET /api/v1/agent-runs/{id}` → `{run: AgentRun, steps: AgentStep[]}`;
`GET /api/v1/agent-runs/{id}/steps` → `AgentStep[]`.

```ts
interface AgentStep { id; agent_run_id; incident_id; seq: number; phase: string;
  node: "observe"|"propose"|"execute_tool"|"apply_hypotheses"|"plan_remediation"|"conclude";
  kind: AgentStepKind; at: string; title: string; input: object; output: object;
  model: string | null; tokens_in: number; tokens_out: number; latency_ms: number;
  tool_execution_id: string | null; trace_id: string | null }
```
Per kind: `proposal`/`fallback`: input `{phase, iteration, evidence, hypotheses}`, output
`{proposal: AgentProposal, recovered: boolean}` where AgentProposal = `{observation, action,
tool_call: {tool_name, arguments_json, purpose, tests_hypothesis, expectation} | null, hypotheses[],
hypothesis_update | null, remediation: {tool_name, arguments_json, target_hypothesis, reason,
expected_effect, rollback_tool_name, rollback_arguments_json} | null, rationale}`;
`authorization`: input `{tool, arguments}`, output `{decision: AuthorizationDecision}`;
`tool_execution`: input `{tool, arguments}`, output `{status, summary, evidence_ids, duration_ms}`;
`hypothesis_update`: output `{hypotheses: HypothesisPayload[]}`; `remediation_plan`: output
`{action_plan: ActionPlan}`; `decision`: output `{metrics: string[]} | {observation} |
{exit_met, unsatisfied[]}`.

## Approvals

`GET /api/v1/approvals?status=pending&limit=100` → `Approval[]` (bare array; `status` defaults to
pending; to list all statuses of an incident use `/incidents/{id}/approvals`).
`GET /api/v1/approvals/{id}` → `{approval: Approval, action_plan: ActionPlan, incident: {id,
display_id, title, severity, status}, hypothesis: Hypothesis | null, evidence: Evidence[] (≤12)}`.
`POST /api/v1/approvals/{id}/approve|reject` body `{reason}` → updated `Approval`; 409 when already
decided or expired; 403 without operator.

## Registry

`GET /api/v1/flows` → `FlowPack[]`; `GET /api/v1/flows/{name}?version=`:
```ts
interface FlowPack { name; version; description; applies_to: SignalKind[]; priority: number;
  initial_phase: string; phases: FlowPhase[]; remediation_tools: string[];
  remediation_risk_ceiling: RiskLevel; budget: ExecutionBudget;
  severity_budget_factor: Record<Severity, number>; checksum: string }   // no id/ref field
interface FlowPhase { name; objective: string; allowed_tools: string[]; max_iterations: number;
  timeout_seconds: number; risk_ceiling: RiskLevel;
  exit_conditions: {kind: "min_evidence"|"min_hypotheses"|"min_hypothesis_confidence"|"hypothesis_validated"|"action_planned"|"no_action_required"|"min_iterations"; value: number | null; description: string}[];
  transitions: {on: "exit_conditions_met"|"exhausted"|"escalate"|"no_action_required"; to: string}[];
  terminal: boolean; guidance: string; plans_remediation: boolean }
```
`GET /api/v1/tools` → `ToolSpec[]` sorted by name (25 tools: 11 read_only/risk none, 6 diagnostic/
low, 5 mutating, 3 dangerous/critical whose descriptions end "Never executable."):
```ts
interface ToolSpec { name; version; description; category: ToolCategory; risk: RiskLevel;
  idempotent: boolean; timeout_seconds: number; retry: {max_attempts; initial_backoff_seconds;
  backoff_multiplier; max_backoff_seconds}; capabilities: string[]; allowed_environments: Environment[];
  min_severity: Severity | null; enabled: boolean; arguments_schema: JSONSchema;
  produces_evidence: boolean; verification_metrics: string[] }
```
`GET /api/v1/tools/{name}` → ToolSpec + `used_in: Record<flowRef, string[]>` (phase names, plus the
literal `"<remediation>"`). `GET /api/v1/policies` → `{rules: PolicyRule[] (priority desc),
invariants: {name, description}[] (3), default: "deny", environment}`; PolicyRule = `{name; description;
priority; effect; tools[]; categories[]; min_risk|null; max_risk|null; environments[]; severities[];
phases[]; flows[]; actor_kinds[]; required_role|null; in_agent_loop: boolean|null; reason}`.

## Memory

`GET /api/v1/memory?limit=&offset=` → `{items: IncidentMemory[], total, limit, offset}`;
`GET /api/v1/memory/search?q=&services=a,b&limit=` → `{similarity: number, matched_on: string,
memory: IncidentMemory}[]`. IncidentMemory = `{created_at; updated_at; id; incident_id;
incident_number; title; severity; symptoms[]; affected_services[]; root_cause; root_cause_service|null;
root_cause_category; evidence_summary[]; actions: object[]; resolution; verification_summary;
duration_seconds; outcome: "resolved"|"escalated"|"false_positive"; lessons[]; embedding_text;
embedding_model|null; resolved_at|null}` (embedding never returned).

## Simulation (proxied to the simulator)

- `GET /api/v1/simulation/scenarios` → `{id; title; description; fault_type; default_params;
  root_cause_service; root_cause_category; expected_symptoms[]; correct_remediations: {action, target,
  extra}[]; incorrect_remediations[]; expect_no_action; expect_escalation; self_resolving_seconds|null;
  detection_hint_metrics[]}[]`. Ids: redis-connection-leak, bad-deployment, db-pool-exhaustion,
  cascading-dependency, transient-spike, memory-leak, cpu-saturation, network-latency.
- `GET /api/v1/simulation/faults?active_only=true` → `{id: "fault-N"; scenario_id; fault_type; params;
  started_at: number (epoch seconds); duration_seconds|null; cleared_at|null; cleared_by|null;
  active}[]`; `POST /api/v1/simulation/faults {scenario_id, params?}` → 201 fault;
  `DELETE /api/v1/simulation/faults/{id}`.
- `GET /api/v1/simulation/topology` → `{nodes: {name, kind: "gateway"|"service"|"database"|"cache",
  tier, owner, replicas, version, state}[], edges: {source, target, protocol, critical}[], time,
  active_faults[]}`; `state` for services = `{version, replicas, up, error_rate, latency_p95_ms,
  health}`, for infra = `{connections, saturation, up}`. Components: api-gateway, auth-service,
  user-service, order-service, payment-service, inventory-service, notification-service, postgres, redis.
- `GET /api/v1/simulation/state` → `{time, tick_count, seed, active_faults[], services: Record<name,
  state>, infra: Record<name, state>}`. `GET /api/v1/simulation/actions` → action log.
- `GET /api/v1/simulation/metrics/{component}?metric=latency_p95_ms&start=<iso>&end=<iso>` →
  `{component, metric, samples: {at, value}[]}`. Service metrics: request_rate, error_rate,
  latency_p50_ms, latency_p95_ms, cpu_percent, memory_percent, db_pool_in_use, db_pool_wait_ms,
  redis_pool_in_use, redis_pool_wait_ms, replicas_ready, up. Infra metrics: connections,
  max_connections, saturation, ops_per_sec, latency_p95_ms, memory_percent, blocked_clients,
  idle_in_transaction, lock_waits, hit_rate, up.
- `GET /api/v1/simulation/components/{component}/current` → `{component, time, metrics}`;
  `GET /api/v1/simulation/components/{component}/baseline/{metric}` → `{component, metric,
  baseline: number | null}` (healthy-period baseline for chart bands).
- `POST /api/v1/simulation/control/advance {seconds}` and `POST …/control/reset {seed}` → state.

## System

`GET /health` → `{status:"ok", version}`; `GET /ready` → `{status:"ready"|"not_ready", checks:
Record<"database"|"redis"|"temporal"|"simulator", {ok: boolean, …}>}` (HTTP 503 when not ready);
`GET /api/v1/me` → `{id, display_name, roles: Role[], auth_mode}`; `GET /api/v1/system/info` →
`{version, environment, llm: {provider, reasoner, fast, healthy}, telemetry_provider, temporal:
boolean, flows: string[], tools: number, policy_rules: number}`; `GET /api/v1/notifications?unread_only=&limit=`
→ `{id; kind: NotificationKind; incident_id|null; title; body; at; read; data}[]`;
`POST /api/v1/notifications/{id}/read` → 204.
