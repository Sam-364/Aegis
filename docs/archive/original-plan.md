You are the principal architect and staff-level engineer responsible for building a production-grade autonomous incident response platform called:

AEGIS
Autonomous Incident Response Runtime

Do not build this as a toy, tutorial, notebook, demo, or simple chatbot.

Build it as a serious, extensible incident-response infrastructure platform whose architecture could eventually be deployed against real distributed systems.

The system must run locally using Docker Compose and must also have a clean path to Kubernetes/cloud deployment.

============================================================
1. PRODUCT DEFINITION
============================================================

Aegis is a stateful autonomous incident-response runtime.

Its purpose is to:

1. ingest infrastructure/system telemetry
2. detect anomalous behavior
3. create and manage incidents
4. investigate incidents autonomously
5. gather structured evidence
6. construct hypotheses
7. score hypotheses
8. validate hypotheses using diagnostic tools
9. generate remediation plans
10. enforce safety/policy constraints
11. execute approved remediation actions
12. verify whether remediation worked
13. rollback failed remediation
14. close the incident
15. learn from the incident
16. retain structured incident memory
17. expose the entire process through a production-grade web console

The core differentiator is NOT the LLM.

The core differentiator is:

A FLOW-SCOPED AUTONOMOUS EXECUTION RUNTIME.

The LLM should NEVER have unrestricted access to tools.

Every tool invocation must be governed by:

- active incident
- active flow
- active phase
- policy
- risk level
- authorization
- tool capability
- environment
- execution budget
- timeout
- retry policy
- idempotency
- audit requirements

============================================================
2. CORE ARCHITECTURAL PRINCIPLE
============================================================

Separate:

A. DURABLE WORKFLOW EXECUTION
B. AGENT REASONING
C. TOOL EXECUTION
D. POLICY ENFORCEMENT
E. STATE MANAGEMENT
F. OBSERVABILITY
G. USER INTERFACE

Use:

Temporal
    ->
durable incident workflows

LangGraph
    ->
agent reasoning and controlled flow execution

FastAPI
    ->
API/control plane

PostgreSQL
    ->
system of record

Redis
    ->
ephemeral state/cache/event acceleration

OpenTelemetry
    ->
distributed tracing/telemetry

Prometheus
    ->
metrics

Grafana
    ->
metrics dashboards

Loki
    ->
logs

Tempo
    ->
traces

Next.js + TypeScript
    ->
operations console

Docker Compose
    ->
local environment

Kubernetes manifests/Helm
    ->
production deployment path

============================================================
3. IMPORTANT ARCHITECTURAL RULE
============================================================

Do NOT make LangGraph responsible for the entire distributed workflow lifecycle.

Temporal owns:

- incident workflow lifecycle
- durable execution
- retries
- crash recovery
- timers
- scheduled execution
- long-running incident workflows
- human approval waits
- remediation execution orchestration
- workflow history

LangGraph owns:

- investigation reasoning
- hypothesis generation
- evidence analysis
- diagnostic planning
- tool selection within a flow
- hypothesis evaluation
- remediation planning
- reasoning loops

Therefore:

Temporal
    |
    +---- DetectionWorkflow
    |
    +---- IncidentWorkflow
             |
             +---- InvestigationActivity
                       |
                       +---- LangGraph Runtime
                                |
                                +---- Flow Pack
                                |
                                +---- Tool Registry
                                |
                                +---- Policy Engine
                                |
                                +---- Evidence Store

============================================================
4. SYSTEM ARCHITECTURE
============================================================

Build the following logical architecture:

                    ┌──────────────────────┐
                    │      Next.js UI      │
                    │   Aegis Operations   │
                    │       Console        │
                    └──────────┬───────────┘
                               │
                         REST / SSE
                               │
                    ┌──────────▼───────────┐
                    │       FastAPI        │
                    │      API Plane       │
                    └──────────┬───────────┘
                               │
             ┌─────────────────┼──────────────────┐
             │                 │                  │
             ▼                 ▼                  ▼
        PostgreSQL          Temporal            Redis
             │                 │
             │                 ▼
             │        Incident Workflow
             │                 │
             │                 ▼
             │        Agent Runtime
             │                 │
             │          ┌──────▼──────┐
             │          │  LangGraph  │
             │          └──────┬──────┘
             │                 │
             │          Flow Pack Runtime
             │                 │
             │       ┌─────────┼──────────┐
             │       ▼         ▼          ▼
             │    Tools      Policy    Evidence
             │    Registry   Engine     Engine
             │
             └────────────────────────────────────

Telemetry:

Simulator / Services
        |
        +---- Metrics
        +---- Logs
        +---- Traces
        |
        ▼
Prometheus / Loki / Tempo / OTel
        |
        ▼
Aegis Detection Engine
        |
        ▼
Incident Creation

============================================================
5. REPOSITORY STRUCTURE
============================================================

Create a monorepo with approximately:

aegis/
|
├── apps/
│   ├── api/
│   ├── worker/
│   ├── agent-runtime/
│   └── web/
|
├── packages/
│   ├── domain/
│   ├── contracts/
│   ├── flow-runtime/
│   ├── tool-runtime/
│   ├── policy-engine/
│   ├── evidence-engine/
│   ├── incident-memory/
│   ├── telemetry/
│   ├── llm/
│   └── simulator/
|
├── flows/
│   ├── incident-investigation/
│   ├── database-failure/
│   ├── api-latency/
│   ├── redis-saturation/
│   └── deployment-regression/
|
├── infrastructure/
│   ├── docker/
│   ├── postgres/
│   ├── redis/
│   ├── temporal/
│   ├── prometheus/
│   ├── grafana/
│   ├── loki/
│   ├── tempo/
│   └── otel/
|
├── deploy/
│   ├── docker-compose/
│   ├── kubernetes/
│   └── helm/
|
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── contract/
│   ├── workflow/
│   ├── security/
│   ├── chaos/
│   └── e2e/
|
├── docs/
│   ├── architecture/
│   ├── adr/
│   ├── api/
│   ├── flows/
│   ├── operations/
│   └── development/
|
├── scripts/
├── Makefile
├── pyproject.toml
├── package.json
├── docker-compose.yml
├── .env.example
└── README.md

Use clean package boundaries.

Do not create one giant Python application.

============================================================
6. TECHNOLOGY STACK
============================================================

Backend:

Python 3.12+

Use:

- FastAPI
- Pydantic v2
- SQLAlchemy 2.x
- Alembic
- asyncpg
- httpx
- structlog
- pytest
- pytest-asyncio
- ruff
- mypy
- uv

Use strict typing.

Frontend:

- Next.js
- TypeScript
- React
- Tailwind
- shadcn/ui
- TanStack Query
- Zustand only where local state is actually required
- Recharts or equivalent visualization library
- SSE for live incident streaming

Agent:

- LangGraph
- LangChain only where useful
- structured output
- Pydantic models
- provider abstraction

Workflow:

- Temporal Python SDK

Database:

- PostgreSQL

Cache:

- Redis

Observability:

- OpenTelemetry
- Prometheus
- Grafana
- Loki
- Tempo

Security/policy:

- implement Aegis Policy Engine first
- optionally integrate OPA later

Testing:

- pytest
- Playwright
- hypothesis/property-based tests where useful

Containerization:

- Docker
- Docker Compose

Deployment:

- Kubernetes
- Helm

============================================================
7. DOMAIN MODEL
============================================================

Define strong domain models.

Core entities:

Incident
IncidentEvent
IncidentState
IncidentSeverity
IncidentStatus

Service
ServiceDependency

TelemetrySignal
MetricSignal
LogSignal
TraceSignal

Evidence
EvidenceSource
EvidenceRelation

Hypothesis
HypothesisScore
HypothesisTest

Action
ActionPlan
ActionExecution
ActionResult

Policy
PolicyDecision

Flow
FlowPack
FlowPhase

Tool
ToolCapability
ToolExecution

AgentRun
AgentStep

ApprovalRequest

IncidentMemory
IncidentResolution

AuditEvent

Environment
ServiceInstance
Deployment

All models should have:

- UUID identifiers
- timestamps
- correlation IDs
- tenant/environment IDs where appropriate
- version fields
- lifecycle state
- audit metadata

Use database constraints aggressively.

============================================================
8. INCIDENT STATE MACHINE
============================================================

Implement an explicit incident state machine.

States:

DETECTED
TRIAGING
INVESTIGATING
HYPOTHESIS_FORMED
VALIDATING
REMEDIATION_PLANNED
AWAITING_APPROVAL
REMEDIATING
VERIFYING
RESOLVED
ROLLED_BACK
ESCALATED
FAILED
CLOSED

Transitions must be explicit.

Invalid transitions must fail.

Never allow arbitrary state mutation from the LLM.

============================================================
9. FLOW PACK ARCHITECTURE
============================================================

This is the most important component.

Implement a first-class FlowPack abstraction.

Example:

FlowPack(
    id="api-latency-investigation",
    version="1.0.0",
    phases=[
        detect,
        investigate,
        hypothesize,
        validate,
        remediate,
        verify
    ]
)

Each phase defines:

- allowed tools
- required evidence
- entry conditions
- exit conditions
- max iterations
- timeout
- risk level
- policy requirements
- allowed transitions

Example:

DETECT:

allowed_tools:
    - get_metrics
    - get_logs

INVESTIGATE:

allowed_tools:
    - get_metrics
    - get_logs
    - query_traces
    - inspect_dependencies

HYPOTHESIZE:

allowed_tools:
    - compare_baseline
    - search_incident_memory

VALIDATE:

allowed_tools:
    - run_diagnostic
    - reproduce_issue

REMEDIATE:

allowed_tools:
    - restart_service
    - rollback_deployment
    - scale_service

VERIFY:

allowed_tools:
    - health_check
    - get_metrics
    - compare_baseline

============================================================
10. FLOW-SCOPED TOOL ENFORCEMENT
============================================================

A tool must NEVER be callable merely because the LLM knows its name.

Implement:

ToolRegistry

ToolDefinition

ToolContext

ToolAuthorization

ToolExecutionRequest

ToolExecutionResult

ToolPolicy

Every tool invocation passes through:

LLM
  ↓
ToolCallRequest
  ↓
FlowRuntime
  ↓
ToolRegistry
  ↓
PolicyEngine
  ↓
Authorization
  ↓
Execution
  ↓
Audit
  ↓
Evidence
  ↓
State update

Implement checks:

1. Is tool registered?
2. Is tool enabled?
3. Is tool allowed in current flow?
4. Is tool allowed in current phase?
5. Is tool allowed for current incident severity?
6. Is tool allowed in current environment?
7. Does current actor have permission?
8. Does tool exceed risk threshold?
9. Does tool require human approval?
10. Is tool idempotent?
11. Has the same action already been executed?
12. Is execution budget available?
13. Has timeout been exceeded?
14. Is the incident still active?

Reject otherwise.

Use explicit exceptions:

ToolNotRegistered
ToolNotAllowed
FlowViolation
PhaseViolation
PolicyViolation
ApprovalRequired
ExecutionBudgetExceeded
DuplicateExecution

============================================================
11. TOOL CATEGORIES
============================================================

Implement tools against a simulated infrastructure environment.

READ-ONLY:

get_metrics
get_logs
query_traces
inspect_service
inspect_dependencies
inspect_deployment
inspect_database
inspect_redis
get_health
compare_baseline
search_incident_memory

DIAGNOSTIC:

run_diagnostic
run_connectivity_test
run_database_diagnostic
run_cache_diagnostic
run_load_test
reproduce_issue

MUTATING:

restart_service
rollback_deployment
scale_service
clear_cache
rotate_connection_pool

DANGEROUS:

delete_data
drop_database
terminate_instance

Dangerous tools must exist only as policy-engine test cases and must never be automatically executable.

============================================================
12. SIMULATED DISTRIBUTED SYSTEM
============================================================

Do NOT depend on AWS/GCP/Azure for the core demo.

Build an actual local distributed system simulator.

Services:

api-gateway
auth-service
user-service
order-service
payment-service
inventory-service
notification-service
postgres
redis

Each service should expose:

/health
/metrics
/version

Generate:

- normal traffic
- latency
- errors
- CPU pressure
- memory pressure
- DB connection exhaustion
- Redis saturation
- dependency failures
- deployment regressions
- traffic spikes
- cascading failures

Use realistic telemetry.

============================================================
13. FAILURE INJECTION ENGINE
============================================================

Create:

FailureInjector

It must support scenarios:

API latency spike
Database connection leak
Redis saturation
Memory leak
CPU saturation
Service crash
Dependency outage
Bad deployment
Network latency
Network packet loss
Traffic spike
Connection pool exhaustion

Each scenario should be deterministic and reproducible.

Example:

POST /simulation/incidents/api-latency

This should inject a known failure.

The user should then be able to watch Aegis detect and resolve it.

============================================================
14. DETECTION ENGINE
============================================================

Implement anomaly detection.

Start with deterministic statistical methods rather than an LLM.

Implement:

- moving average
- EWMA
- z-score
- threshold detection
- baseline comparison

Example:

latency > baseline + 3σ

Do not use the LLM to detect basic anomalies.

Detection creates:

Incident

with:

severity
signals
affected_services
initial_evidence
correlation_id

============================================================
15. INCIDENT CORRELATION
============================================================

Multiple alerts must not automatically become multiple incidents.

Implement correlation using:

- service
- dependency graph
- timestamp proximity
- metric similarity
- trace relationships
- shared deployment
- common infrastructure dependency

Example:

Redis saturation
+
API latency
+
Order-service timeout

should potentially become ONE incident.

============================================================
16. EVIDENCE GRAPH
============================================================

Do not represent investigation as a flat list of messages.

Create an evidence graph.

Nodes:

Signal
Metric
Log
Trace
Service
Deployment
Dependency
Hypothesis
Action
Observation

Edges:

caused_by
depends_on
correlated_with
supports
contradicts
observed_on
resolved_by

Store evidence as structured data.

The agent should reason over this graph.

============================================================
17. HYPOTHESIS ENGINE
============================================================

Create structured hypotheses.

Example:

Hypothesis:

"Redis connection pool exhaustion is causing API latency."

Fields:

id
description
confidence
supporting_evidence
contradicting_evidence
affected_services
tests
status

Confidence must be calculated from evidence.

Do NOT let the LLM arbitrarily output:

"confidence=0.97"

without supporting evidence.

Implement a scoring mechanism.

Example:

score =
    evidence_strength
    + temporal_alignment
    + dependency_alignment
    + historical_similarity
    - contradiction_penalty

The LLM proposes hypotheses.

The runtime scores and validates them.

============================================================
18. INCIDENT MEMORY
============================================================

Implement structured incident memory.

Example:

{
    incident_id,
    symptoms,
    affected_services,
    root_cause,
    evidence,
    actions,
    resolution,
    verification,
    duration,
    severity
}

Store embeddings for semantic retrieval.

Use pgvector if available.

Do not create a generic vector database unless needed.

PostgreSQL + pgvector should be the default.

The agent can retrieve similar incidents.

But retrieved memory must be treated as evidence, not truth.

============================================================
19. AGENT REASONING LOOP
============================================================

Implement the agent as a controlled loop:

OBSERVE
↓
ASSESS
↓
SELECT NEXT ACTION
↓
EXECUTE TOOL
↓
COLLECT RESULT
↓
UPDATE EVIDENCE
↓
UPDATE HYPOTHESES
↓
DECIDE
↓
CONTINUE / REMEDIATE / ESCALATE

Never allow:

LLM → arbitrary tool → arbitrary action

Instead:

LLM
→ proposed action
→ runtime validation
→ policy validation
→ authorization
→ execution

============================================================
20. LANGGRAPH DESIGN
============================================================

Implement a LangGraph StateGraph.

State should resemble:

AgentState:

incident_id
flow_id
phase
objective
signals
evidence
hypotheses
observations
tool_history
candidate_actions
selected_action
policy_decision
risk_level
iteration
budget
termination_reason

Nodes:

observe
analyze
generate_hypotheses
select_diagnostic
execute_tool
update_evidence
evaluate_hypothesis
plan_remediation
request_approval
execute_remediation
verify
finish

The graph itself must not bypass FlowRuntime.

============================================================
21. LLM ABSTRACTION
============================================================

Do not hardcode a single LLM provider.

Create:

LLMProvider

implement adapters for:

Gemini
OpenAI-compatible models
local models

Configuration:

LLM_PROVIDER
LLM_MODEL
LLM_BASE_URL
LLM_API_KEY

Use structured outputs.

The LLM should never directly mutate database state.

============================================================
22. POLICY ENGINE
============================================================

Create a first-class policy engine.

Policy examples:

READ_ONLY tools:
automatically allowed

LOW_RISK mutation:
allowed in development

MEDIUM_RISK:
requires approval

HIGH_RISK:
never autonomous

PRODUCTION:
restart_service requires approval

ROLLBACK:
requires approval unless explicitly configured otherwise

DELETE_DATA:
always prohibited

Policy decisions:

ALLOW
DENY
REQUIRE_APPROVAL

Every decision must be audited.

============================================================
23. HUMAN APPROVAL
============================================================

Implement approval workflow.

If action requires approval:

Temporal workflow pauses.

Create:

ApprovalRequest

Frontend shows:

Incident
Hypothesis
Evidence
Proposed action
Risk
Expected impact
Rollback plan

Buttons:

Approve
Reject

Approval resumes the workflow.

Do not poll unnecessarily.

Use Temporal durable waiting/signals.

============================================================
24. REMEDIATION
============================================================

Every remediation must have:

ActionPlan

including:

action
reason
expected_effect
risk
rollback_action
timeout
verification_conditions

Example:

restart_service(order-service)

Expected:

latency decreases
error rate decreases
health restored

Then verify.

============================================================
25. AUTOMATIC ROLLBACK
============================================================

If remediation fails:

DO NOT blindly retry forever.

Perform:

verification
failure classification
rollback eligibility check
rollback
verification

Example:

restart_service
    ↓
health check fails
    ↓
rollback unavailable
    ↓
escalate

============================================================
26. VERIFICATION ENGINE
============================================================

Never mark an incident resolved because the LLM says it is resolved.

Resolution requires measurable evidence.

Example:

Before:

p95 latency = 8.2s

After:

p95 latency = 180ms

error rate:

Before = 14%

After = 0.7%

Then:

verification_status = PASSED

Only then:

incident.status = RESOLVED

============================================================
27. TEMPORAL WORKFLOW
============================================================

Implement:

IncidentWorkflow

Lifecycle:

receive incident
↓
triage
↓
investigation
↓
hypothesis
↓
validation
↓
remediation planning
↓
approval if required
↓
remediation
↓
verification
↓
rollback if required
↓
resolution
↓
memory extraction

Temporal activities should be:

idempotent

retryable

timeout-bound

observable

Do not perform non-deterministic operations directly inside workflow code.

============================================================
28. API DESIGN
============================================================

FastAPI endpoints:

GET /health
GET /ready

GET /api/v1/incidents
POST /api/v1/incidents
GET /api/v1/incidents/{id}
POST /api/v1/incidents/{id}/acknowledge
POST /api/v1/incidents/{id}/resolve
POST /api/v1/incidents/{id}/cancel

GET /api/v1/incidents/{id}/timeline
GET /api/v1/incidents/{id}/evidence
GET /api/v1/incidents/{id}/hypotheses
GET /api/v1/incidents/{id}/actions
GET /api/v1/incidents/{id}/audit

POST /api/v1/approvals/{id}/approve
POST /api/v1/approvals/{id}/reject

GET /api/v1/flows
GET /api/v1/flows/{id}

GET /api/v1/tools
GET /api/v1/tools/{id}

POST /api/v1/simulation/failures
GET /api/v1/simulation/services
GET /api/v1/simulation/topology

GET /api/v1/metrics

Use:

API versioning
OpenAPI
request IDs
correlation IDs
pagination
consistent error schemas
rate limiting
authentication abstraction

============================================================
29. REAL-TIME FRONTEND
============================================================

Build a serious operations console.

Pages:

Dashboard

Incidents

Incident Detail

Service Map

Topology

Agent Runs

Flow Packs

Tools

Policies

Approvals

Incident Memory

Simulation

System Health

============================================================
30. INCIDENT DETAIL PAGE
============================================================

This is the most important frontend screen.

Show:

Incident severity

Status

Duration

Affected services

Root-cause hypothesis

Confidence

Timeline

Live agent execution

Evidence graph

Metrics

Logs

Traces

Tool calls

Policy decisions

Actions

Approvals

Remediation

Verification

Audit trail

Example timeline:

10:31:04
Anomaly detected

10:31:07
Incident created

10:31:09
Investigating API latency

10:31:11
Queried Redis metrics

10:31:13
Hypothesis generated

10:31:16
Redis saturation confirmed

10:31:20
Remediation proposed

10:31:22
Approval required

10:31:34
Approved

10:31:36
Restarting service

10:31:48
Verification passed

10:31:49
Incident resolved

============================================================
31. SERVICE TOPOLOGY
============================================================

Create interactive service dependency graph.

Nodes:

services

Edges:

dependencies

Overlay:

health
latency
error rate
traffic

When incident occurs:

highlight affected dependency chain.

============================================================
32. AGENT TRACE VIEWER
============================================================

Build a visual trace.

Example:

AgentRun
|
+-- Flow: api-latency-investigation
|
+-- Phase: investigate
|     |
|     +-- get_metrics
|     +-- query_traces
|     +-- inspect_redis
|
+-- Phase: hypothesize
|     |
|     +-- hypothesis #1
|     +-- hypothesis #2
|
+-- Phase: validate
|
+-- Phase: remediate
|
+-- Phase: verify

Every step should show:

latency
tokens
model
tool
input
output
policy decision
result
timestamp

Do not expose secrets.

============================================================
33. OBSERVABILITY
============================================================

Instrument everything.

Create OpenTelemetry traces for:

HTTP requests
Temporal workflows
LangGraph runs
agent nodes
tool executions
database queries
LLM calls
policy decisions
remediation actions

Propagate:

trace_id
span_id
incident_id
workflow_id
agent_run_id
tool_execution_id

Metrics:

aegis_incidents_total
aegis_incidents_active
aegis_incident_resolution_seconds
aegis_agent_runs_total
aegis_tool_calls_total
aegis_tool_failures_total
aegis_policy_denials_total
aegis_approval_wait_seconds
aegis_remediation_success_total
aegis_remediation_failure_total
aegis_rollback_total
aegis_llm_latency_seconds
aegis_llm_tokens_total

============================================================
34. AUDITABILITY
============================================================

Everything important must be auditable.

Create immutable audit events.

Example:

{
    event_type,
    actor,
    incident_id,
    flow_id,
    tool_id,
    action,
    decision,
    timestamp,
    reason,
    trace_id
}

Never overwrite audit history.

============================================================
35. SECURITY
============================================================

Implement:

- API authentication abstraction
- RBAC
- tool-level permissions
- environment-level permissions
- secrets through environment/secret manager abstraction
- no secrets in logs
- input validation
- SSRF protection for network tools
- command execution sandboxing
- resource limits
- timeout enforcement
- audit logs

Never allow arbitrary shell execution from the LLM.

Never allow the LLM to construct arbitrary SQL.

Use parameterized queries.

============================================================
36. FAILURE MODES
============================================================

Explicitly handle:

LLM timeout
LLM malformed output
LLM unavailable
tool timeout
tool failure
database unavailable
Redis unavailable
Temporal unavailable
duplicate tool execution
stale incident
conflicting hypotheses
failed remediation
failed verification
approval timeout
workflow restart
worker crash
network partition

The system must fail safely.

Default behavior:

READ
→ retry

DIAGNOSTIC
→ retry with limits

MUTATION
→ idempotency check

DANGEROUS
→ deny

UNKNOWN
→ escalate

============================================================
37. IDEMPOTENCY
============================================================

Every mutating tool must support:

idempotency_key

Example:

incident_id + action_id

Before executing:

check previous execution.

If already succeeded:

return previous result.

If running:

do not duplicate.

============================================================
38. BUDGETING
============================================================

Implement agent execution budgets.

Budget fields:

max_iterations
max_tool_calls
max_runtime
max_llm_tokens
max_remediation_attempts

Example:

max_iterations = 20
max_tool_calls = 40
max_remediation_attempts = 2

When budget exceeded:

STOP

then:

ESCALATE

Never continue indefinitely.

============================================================
39. RATE LIMITING
============================================================

Protect:

LLM
tools
simulation
API

Use token bucket / sliding-window style controls where appropriate.

============================================================
40. CONFIGURATION
============================================================

Use environment-driven configuration.

Create:

Settings

with:

environment
database_url
redis_url
temporal_url
temporal_namespace
llm_provider
llm_model
llm_api_key
otel_endpoint
prometheus_endpoint
log_level

Validate settings at startup.

============================================================
41. DATABASE
============================================================

Use PostgreSQL migrations.

Tables:

incidents
incident_events
incident_signals
services
dependencies
telemetry_snapshots
evidence
evidence_relations
hypotheses
hypothesis_tests
actions
action_executions
approvals
policies
audit_events
agent_runs
agent_steps
flow_definitions
tool_definitions
incident_memories

Add:

indexes
foreign keys
constraints
unique constraints
timestamps

Use soft deletion only where semantically appropriate.

============================================================
42. API / DOMAIN SEPARATION
============================================================

Do not allow FastAPI route handlers to directly manipulate SQLAlchemy models everywhere.

Use layers:

API
↓
Application Service
↓
Domain
↓
Repository
↓
Database

Example:

IncidentService
IncidentRepository

ToolService
ToolRepository

PolicyService
PolicyRepository

MemoryService
MemoryRepository

============================================================
43. TESTING STRATEGY
============================================================

This is critical.

Do not consider the project complete without tests.

Unit tests:

flow validation
tool authorization
policy decisions
state transitions
hypothesis scoring
incident correlation
budget enforcement
idempotency

Integration tests:

Postgres
Redis
Temporal
LangGraph
API

Workflow tests:

incident lifecycle
approval pause/resume
worker crash recovery
retry
rollback

Security tests:

unauthorized tools
policy bypass attempts
SQL injection
SSRF
command injection
secret leakage

E2E:

inject failure
detect
investigate
hypothesize
validate
approve
remediate
verify
resolve

The full E2E test should execute automatically.

============================================================
44. CHAOS TESTING
============================================================

Create chaos tests.

Kill:

API process
agent worker
Temporal worker
Redis
Postgres

During active incident.

Verify:

workflow resumes
state remains consistent
duplicate remediation does not happen
incident remains recoverable

============================================================
45. DEMO SCENARIOS
============================================================

Implement at least five polished scenarios.

SCENARIO 1:

Redis connection exhaustion

Symptoms:

API latency
Redis connections
timeouts

Expected root cause:

Redis connection leak

Remediation:

restart affected workers

Verification:

latency returns to baseline

------------------------------------------------------------

SCENARIO 2:

Bad deployment

Symptoms:

error rate spike
new deployment version
specific endpoint failure

Expected root cause:

deployment regression

Remediation:

rollback deployment

Verification:

error rate normalizes

------------------------------------------------------------

SCENARIO 3:

Database saturation

Symptoms:

DB connections
query latency
API latency

Remediation:

scale workers / connection pool adjustment

------------------------------------------------------------

SCENARIO 4:

Cascading dependency failure

Service A
↓
Service B
↓
Service C

C fails.

Aegis should identify C as likely root cause rather than restarting A.

------------------------------------------------------------

SCENARIO 5:

False positive

Metric spike occurs briefly.

Aegis should:

detect
investigate
determine insufficient evidence
avoid remediation
close/escalate appropriately

============================================================
46. FRONTEND UX PRINCIPLES
============================================================

The UI should look like a professional infrastructure operations product.

Think:

Datadog
Grafana
PagerDuty
Linear
GitHub Actions

Do NOT make it look like an AI chat application.

The agent chat should be secondary.

The primary interface should be:

incident
evidence
decision
action
verification

============================================================
47. LLM UX
============================================================

Provide a reasoning panel.

Do not expose hidden chain-of-thought.

Instead show structured reasoning artifacts:

Observation

Evidence

Hypothesis

Confidence

Next diagnostic

Policy decision

Proposed action

Expected outcome

Verification

This is important.

Never display internal private chain-of-thought.

============================================================
48. FLOW PACK CONFIGURATION
============================================================

Flow packs should be declarative.

Prefer YAML definitions:

flows/
    api-latency.yaml

Example:

name: api-latency-investigation
version: 1.0

phases:

  - name: detect
    tools:
      - get_metrics
      - get_logs

  - name: investigate
    tools:
      - get_metrics
      - get_logs
      - query_traces
      - inspect_dependencies

  - name: validate
    tools:
      - run_diagnostic

  - name: remediate
    tools:
      - restart_service
      - rollback_deployment
    approval: required

  - name: verify
    tools:
      - get_metrics
      - health_check

Compile YAML into validated FlowPack objects.

Reject invalid FlowPacks at startup.

============================================================
49. FLOW PACK VERSIONING
============================================================

Flow packs must be versioned.

Example:

api-latency-investigation:v1
api-latency-investigation:v2

Existing incidents must continue using their original version.

Do not silently mutate active flows.

============================================================
50. TOOL VERSIONING
============================================================

Tools should have:

name
version
capabilities
risk
idempotent
timeout
retry_policy

Example:

restart_service:v1

============================================================
51. AGENT POLICY
============================================================

The LLM is an advisor.

The runtime is the authority.

Never:

LLM → direct database mutation
LLM → direct infrastructure mutation
LLM → bypass policy
LLM → change flow
LLM → change permissions
LLM → approve its own action

============================================================
52. INCIDENT SEVERITY
============================================================

Implement:

SEV1
SEV2
SEV3
SEV4

Severity influences:

allowed tools
approval requirements
agent budgets
notification
escalation
remediation autonomy

============================================================
53. MULTI-TENANCY PREPARATION
============================================================

Do not fully implement SaaS multi-tenancy yet.

But design entities with:

tenant_id
environment_id

and make repositories capable of tenant scoping.

Never allow cross-tenant access.

============================================================
54. NOTIFICATION SYSTEM
============================================================

Create NotificationService abstraction.

Implement local:

in-app notifications

Architecture should support:

Slack
email
PagerDuty

later.

============================================================
55. EVENT MODEL
============================================================

Use domain events.

Examples:

IncidentDetected
IncidentCreated
InvestigationStarted
EvidenceCollected
HypothesisCreated
HypothesisValidated
RemediationProposed
ApprovalRequested
RemediationStarted
RemediationCompleted
VerificationPassed
VerificationFailed
IncidentResolved
IncidentEscalated

Persist important events.

============================================================
56. EVENT STREAMING
============================================================

Frontend must receive live updates.

Use SSE initially.

Event:

GET /api/v1/incidents/{id}/stream

Stream:

incident state changes
agent steps
tool executions
policy decisions
approvals
verification

Implement reconnection.

Events must be ordered.

============================================================
57. DOCUMENTATION
============================================================

Generate:

README.md

Architecture document

ADR documents for major decisions:

ADR-001 Temporal + LangGraph
ADR-002 PostgreSQL
ADR-003 Flow Packs
ADR-004 Tool authorization
ADR-005 Evidence graph
ADR-006 Policy engine
ADR-007 Observability

Developer setup

Production deployment

Flow Pack authoring guide

Tool authoring guide

Incident lifecycle documentation

API documentation

Security model

Threat model

============================================================
58. DEVELOPER EXPERIENCE
============================================================

Root commands:

make setup
make dev
make test
make lint
make typecheck
make format
make migrate
make seed
make simulation
make e2e
make chaos
make observability

Also support:

docker compose up

and:

docker compose up --build

A new developer should be able to start the entire system with one command.

============================================================
59. LOCAL ENVIRONMENT
============================================================

Docker Compose must start:

aegis-api
aegis-worker
aegis-agent
aegis-web
postgres
redis
temporal
temporal-ui
prometheus
grafana
loki
tempo
otel-collector
simulated-services

Seed the system automatically.

============================================================
60. PRODUCTION DEPLOYMENT
============================================================

Create Kubernetes deployment manifests.

Services:

api
worker
agent
web

Infrastructure:

postgres
redis
temporal

Observability:

otel-collector

Do not assume stateful infrastructure should necessarily run inside Kubernetes in real production.

Document recommended production architecture where managed:

PostgreSQL
Redis
Temporal

are preferable.

============================================================
61. HEALTH CHECKS
============================================================

Every service needs:

/health
/ready

Readiness must verify dependencies where appropriate.

Example:

API ready only if:

database reachable
required migrations applied
Temporal available

============================================================
62. GRACEFUL SHUTDOWN
============================================================

Implement graceful shutdown.

Agents must not abandon active operations.

Workers should stop accepting new work.

Temporal workflows must remain recoverable.

============================================================
63. DATABASE MIGRATIONS
============================================================

Alembic.

No auto-create tables in production.

Startup should verify migration state.

============================================================
64. LOGGING
============================================================

Use structured JSON logs.

Fields:

timestamp
level
service
environment
trace_id
span_id
incident_id
workflow_id
agent_run_id
tool_execution_id
message

Never log:

API keys
tokens
passwords
credentials
authorization headers

============================================================
65. PERFORMANCE
============================================================

Do not optimize prematurely.

But design for:

async I/O
connection pooling
bounded concurrency
backpressure
pagination
database indexes
streaming
caching

Avoid blocking operations inside async endpoints.

============================================================
66. FAILURE-SAFE DEFAULTS
============================================================

Default:

unknown policy
    -> DENY

unknown tool
    -> DENY

unknown phase
    -> STOP

missing evidence
    -> INVESTIGATE

failed verification
    -> DO NOT RESOLVE

ambiguous root cause
    -> ESCALATE

LLM unavailable
    -> fall back to deterministic diagnostics where possible

============================================================
67. ARCHITECTURE QUALITY
============================================================

Follow:

SOLID

Domain-driven design where useful

Hexagonal architecture

Dependency inversion

Explicit boundaries

Typed interfaces

No circular dependencies

No global mutable state

No singleton abuse

No giant service classes

No god objects

No giant LangGraph node containing the entire application

============================================================
68. IMPLEMENTATION ORDER
============================================================

Do NOT attempt to write everything simultaneously.

Implement in vertical slices.

PHASE 1:

Repository foundation

Python environment

Next.js

Docker Compose

Postgres

Redis

FastAPI

basic CI

------------------------------------------------------------

PHASE 2:

Domain model

database schema

migrations

repositories

incident state machine

------------------------------------------------------------

PHASE 3:

Simulation platform

services

telemetry

failure injection

------------------------------------------------------------

PHASE 4:

Detection engine

anomaly detection

incident creation

------------------------------------------------------------

PHASE 5:

Tool Registry

ToolDefinition

ToolContext

FlowPack

FlowRuntime

PolicyEngine

strict authorization

------------------------------------------------------------

PHASE 6:

LangGraph agent

investigation

hypothesis generation

evidence updates

controlled tool execution

------------------------------------------------------------

PHASE 7:

Temporal

IncidentWorkflow

durable execution

approval waiting

retries

recovery

------------------------------------------------------------

PHASE 8:

Remediation

actions

idempotency

rollback

verification

------------------------------------------------------------

PHASE 9:

Incident Memory

pgvector

retrieval

similar incidents

------------------------------------------------------------

PHASE 10:

Observability

OpenTelemetry

Prometheus

Grafana

Loki

Tempo

------------------------------------------------------------

PHASE 11:

Frontend

dashboard

incident detail

agent timeline

evidence graph

topology

approvals

------------------------------------------------------------

PHASE 12:

Security

RBAC

policy hardening

audit

security tests

------------------------------------------------------------

PHASE 13:

Chaos

failure injection

worker crashes

dependency outages

workflow recovery

------------------------------------------------------------

PHASE 14:

Production deployment

Kubernetes

Helm

deployment documentation

------------------------------------------------------------

PHASE 15:

Polish

UX

documentation

performance

testing

demo scenarios

============================================================
69. CI/CD
============================================================

Create GitHub Actions.

Pipeline:

lint
typecheck
unit tests
integration tests
security tests
build backend
build frontend
build Docker images
E2E

Do not allow merge if critical checks fail.

============================================================
70. CODE QUALITY
============================================================

Use:

ruff
mypy
pytest

Frontend:

eslint
prettier
TypeScript strict mode

No:

TODO placeholders for core functionality.

No:

pass

for unimplemented production logic.

No fake API responses.

No hardcoded incident results.

No fake "AI reasoning" strings.

============================================================
71. ACCEPTANCE TEST
============================================================

The project is NOT complete until this scenario works end-to-end:

1. Start:

docker compose up --build

2. Open Aegis dashboard.

3. Trigger:

Redis connection exhaustion.

4. Simulator begins producing:

high Redis connections
API latency
timeouts

5. Detection engine detects anomaly.

6. Incident is created.

7. Temporal starts IncidentWorkflow.

8. LangGraph begins investigation.

9. Agent accesses only tools allowed by current FlowPack phase.

10. Agent collects:

metrics
logs
dependency information

11. Evidence graph updates.

12. Agent generates hypotheses.

13. Hypothesis engine scores them.

14. Agent validates the leading hypothesis.

15. Agent proposes:

restart affected workers

16. Policy engine evaluates action.

17. Approval is required.

18. UI shows approval request.

19. User approves.

20. Temporal workflow resumes.

21. Tool executes exactly once.

22. System verifies:

latency decreases
errors decrease
Redis connections normalize

23. Incident becomes:

RESOLVED

24. Incident memory is generated.

25. Future similar incident retrieves this memory.

26. Entire workflow is visible in:

Aegis UI

27. Entire workflow is traceable through:

OpenTelemetry

28. Audit trail exists.

29. If the worker crashes during step 20:

Temporal resumes execution safely.

30. If remediation is executed twice accidentally:

idempotency prevents duplicate execution.

============================================================
72. IMPORTANT ENGINEERING BEHAVIOR
============================================================

Before writing code:

1. inspect repository
2. create architecture plan
3. identify dependencies
4. create ADRs
5. define domain contracts
6. implement foundation
7. run tests
8. continue incrementally

After every major phase:

- run tests
- run typecheck
- run lint
- verify Docker
- update documentation

Do not simply generate hundreds of files without validating them.

============================================================
73. CLAUDE CODE OPERATING MODE
============================================================

You are authorized to create and modify the entire repository.

Work autonomously.

Do not repeatedly ask for confirmation for normal engineering decisions.

When a technology choice is ambiguous:

prefer:

production maturity
maintainability
strong typing
observability
failure recovery
clear architecture
local developer experience

over:

novelty
minimum code
quick hacks

When there are multiple reasonable choices, document the decision in an ADR.

============================================================
74. MOST IMPORTANT RULE
============================================================

Aegis must demonstrate this invariant:

THE MODEL PROPOSES.
THE RUNTIME DECIDES.
THE POLICY ENGINE AUTHORIZES.
THE TOOL EXECUTES.
THE OBSERVABILITY LAYER RECORDS.
THE VERIFICATION ENGINE PROVES.

Never invert this architecture.

The LLM must never become the authority over infrastructure.

============================================================
75. FINAL DELIVERABLE
============================================================

At completion provide:

1. working repository
2. Docker Compose environment
3. Kubernetes deployment
4. Helm chart
5. FastAPI API
6. Next.js frontend
7. Temporal workflows
8. LangGraph agent runtime
9. Flow Pack framework
10. Tool registry
11. Policy engine
12. Evidence graph
13. Hypothesis engine
14. Incident memory
15. simulation environment
16. failure injection
17. remediation engine
18. verification engine
19. audit system
20. OpenTelemetry
21. Prometheus/Grafana
22. Loki/Tempo
23. complete test suite
24. CI pipeline
25. documentation
26. ADRs
27. security model
28. five working incident scenarios

The final system should feel like an actual internal infrastructure platform rather than an AI demo.

Do not optimize for number of features.

Optimize for:

CORRECTNESS
DURABILITY
CONTROL
AUDITABILITY
OBSERVABILITY
SAFETY
EXTENSIBILITY

Build Aegis accordingly.