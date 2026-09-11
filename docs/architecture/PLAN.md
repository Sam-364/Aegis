# Aegis — Amended Architecture Plan

This document supersedes `docs/archive/original-plan.md`. It keeps the original's intent and
tightens it where the original was vague, contradictory, or under-specified. Changes are marked
**AMENDED**. Decisions with alternatives are recorded as ADRs in `docs/adr/`.

## 1. Identity

Aegis is not an AI agent that happens to operate infrastructure. It is a **durable autonomous
execution runtime** in which an AI agent is one controlled decision-making component.

```
THE MODEL PROPOSES.
THE RUNTIME DECIDES.
THE POLICY ENGINE AUTHORIZES.
THE TOOL EXECUTES.
THE OBSERVABILITY LAYER RECORDS.
THE VERIFICATION ENGINE PROVES.
```

## 2. What changed from the original plan

| # | Original | Amended | Why |
|---|----------|---------|-----|
| 1 | LangGraph executes remediation tools inside the agent loop (`execute_remediation` node) | **Mutating tools are never executed inside the LLM loop.** The agent's REMEDIATE phase only produces a validated `ActionPlan`. Policy → approval → execution → verification → rollback run as Temporal activities. | Makes the "model proposes, runtime decides" invariant structural, not a convention. Every infrastructure mutation is durable, approved, idempotent and auditable by construction. |
| 2 | Agent runs as one long Temporal activity | Agent runs **per phase** as an activity with heartbeats; LangGraph checkpoints to Postgres (`thread_id = agent_run_id`) so a crashed worker resumes at the last node. Tool executions are ledgered by idempotency key before execution. | Crash-safety at node granularity without bloating Temporal history with LLM payloads. |
| 3 | Telemetry ingestion path undefined ("Prometheus → Detection") | A **`TelemetryProvider` port** with two adapters: `SimulatorTelemetryProvider` (HTTP to the simulator) and `PrometheusTelemetryProvider` (PromQL). Detection, tools and verification all read through the port. | The spine is testable without Prometheus; production swaps adapters, not logic. |
| 4 | `packages/*` Python monorepo with ten distributions | **One distribution, `src/aegis/`, with enforced layering via import-linter contracts** (domain → ports → runtime → infrastructure/application → api/workflows/apps). | Ten wheels multiply build, typing and Docker complexity with no runtime benefit. Boundaries are enforced by CI, not by packaging. ADR-008. |
| 5 | Gemini + OpenAI + local adapters | `OpenAIProvider` (Responses API, strict structured outputs) and `OpenAICompatibleProvider` (base URL; covers vLLM, Ollama, Gemini's compatible endpoint). Plus `ScriptedProvider` for tests and a **`DeterministicPlanner` fallback** when the LLM is unavailable. | One well-tested path beats three half-tested ones. |
| 6 | Hypothesis confidence "calculated from evidence" (unspecified) | Explicit, explainable score: weighted sum of evidence strength, temporal alignment, dependency alignment, historical similarity, minus contradiction penalty. Each component is 0..1 with a stored breakdown. The LLM can only reference evidence IDs that exist. | Confidence must be reproducible, auditable and rendered in the UI. |
| 7 | Verification "measurable evidence" (unspecified) | `VerificationSpec` on every `ActionPlan`: conditions over metrics (absolute or baseline-relative) that must hold across a stabilization window. Verification is a Temporal activity, not an LLM judgement. False positives also pass through verification before RESOLVED. | Resolution is proven, never asserted. |
| 8 | No eval strategy | `evals/` with suites per component (detection, correlation, policy/authorization adversarial, hypothesis ranking, agent scenario accuracy, memory recall, LLM schema validity, verification). Runner emits JSON + Markdown reports; agent evals run against the in-process simulator with the real LLM. | "Battle tested" needs measurements, not vibes. |
| 9 | Detection as a Temporal workflow | Detection is a **supervised asyncio loop with a Redis leader lock**, publishing anomaly signals to a correlator that opens or attaches to incidents and starts `IncidentWorkflow`. | Polling telemetry every 5s from a workflow is history-unfriendly. Leader lock keeps multi-replica correctness. |
| 10 | Separate Temporal Postgres | Single `pgvector/pgvector:pg17` Postgres hosting `aegis`, `temporal`, `temporal_visibility`. The default `docker compose up` brings the core spine; an `observability` profile adds Prometheus, Grafana, Tempo, Loki and the OTel collector. | The host is RAM-constrained. Profiles keep the spine light. |
| 11 | Five demo scenarios listed loosely | **Eight deterministic** scenarios with ground truth (root-cause service, correct *and* incorrect remediations, expected outcome) in `src/aegis/simulator/faults.py`, shared by the simulator, the E2E harness and the evals. | One source of truth for "did the agent get it right", including what counts as the wrong fix. |
| 12 | Frontend "like Datadog/Linear" | A distinct visual identity ("flight recorder"): incident ledger, evidence graph, authorization gate sequence, topology with live overlay, approval console. No chat UI. | The user explicitly asked for a non-generic console. |

## 3. Layered architecture

```
 ┌──────────────────────────────────────────────────────────────────────────┐
 │  Delivery       api/ (FastAPI, SSE)   workflows/ (Temporal)   apps/ (CLI)│
 ├──────────────────────────────────────────────────────────────────────────┤
 │  Application    application/ (services, event publisher, unit of work)   │
 ├──────────────────────────────────────────────────────────────────────────┤
 │  Runtime core   flow/ policy/ tools/ evidence/ hypotheses/ detection/    │
 │                 verification/ remediation/ memory/ llm/ agent/           │
 ├──────────────────────────────────────────────────────────────────────────┤
 │  Ports          ports/ (Protocols: repositories, telemetry, infra, llm…) │
 ├──────────────────────────────────────────────────────────────────────────┤
 │  Domain         domain/ (entities, enums, state machine, events, errors) │
 └──────────────────────────────────────────────────────────────────────────┘
   infrastructure/ implements ports (postgres, redis, simulator, prometheus)
   simulator/ is an independent package (its own process)
```

Import direction is downward only. import-linter enforces it in CI.

## 4. Tool authorization pipeline (the centerpiece)

```
LLM proposal ─▶ ToolCallRequest
   1  tool_registered          registry knows name@version
   2  tool_enabled             not disabled by config
   3  incident_active          incident not terminal
   4  flow_allows              flow pack lists the tool
   5  phase_allows             current phase lists the tool
   6  category_permitted        MUTATING/DANGEROUS never inside the agent loop
   7  environment_allows       tool permitted in this environment
   8  severity_allows          tool permitted at this severity
   9  actor_permitted          RBAC for the requesting actor
  10  risk_within_ceiling      tool.risk ≤ phase.risk_ceiling
  11  policy_effect            PolicyEngine → ALLOW / DENY / REQUIRE_APPROVAL
  12  arguments_valid          Pydantic schema, SSRF/injection guards
  13  idempotency              no duplicate in-flight / completed execution
  14  budget_available         iterations, tool calls, runtime, tokens
─▶ AuthorizationDecision(checks=[…], effect, reason) ─▶ audit ─▶ execute | deny | approval
```

Every decision is stored with its ordered check trace and rendered in the UI as a gate sequence.

## 5. Durable spine

```
Detector ─▶ Correlator ─▶ Incident (DETECTED) ─▶ Temporal IncidentWorkflow
   triage(activity)
   loop phase in flow: run_agent_phase(activity, heartbeat, LangGraph checkpointed)
   if action planned: evaluate_policy → [wait approval signal | timeout → ESCALATED]
   execute_action(activity, idempotency key = incident_id + action_plan_id)
   verify(activity, VerificationSpec)  ─▶ RESOLVED | rollback(activity) ─▶ replan | ESCALATED
   extract_memory(activity)  ─▶ incident_memories (pgvector)
```

## 6. Simulator

A discrete-time model of a small distributed system (gateway → auth/user/order/payment/
inventory/notification → postgres/redis) with causal fault propagation. Faults are declarative
scenarios with ground truth. Remediation endpoints have realistic dynamics: a restart only
helps if it addresses the actual cause; a rollback only helps for a deployment regression.

## 7. Scenarios (ground truth shared by simulator, E2E and evals)

| ID | Scenario | Root cause | Correct remediation | Verification |
|----|----------|------------|---------------------|--------------|
| S1 | redis-connection-leak | order-service leaks Redis connections → Redis saturates | `rotate_connection_pool(order-service)` or `restart_service(order-service)` | gateway p95 & 5xx back within baseline, redis connections normal |
| S2 | bad-deployment | payment-service v2.4.0 regression → 5xx on /checkout | `rollback_deployment(payment-service)` | error rate back to baseline |
| S3 | db-pool-exhaustion | user-service leaves sessions idle in transaction | `rotate_connection_pool(user-service, postgres)` or `restart_service(user-service)`; scaling is explicitly wrong (more replicas open more leaking sessions) | db connections, latency normalize |
| S4 | cascading-dependency | inventory-service crash → order-service → gateway | `restart_service(inventory-service)` (not gateway) | inventory healthy, chain recovers |
| S5 | transient-spike | 45s traffic burst, self-recovers | **none** | recovery verified, resolved without action |
| S6 | memory-leak | payment-service heap grows until it OOMs | `restart_service(payment-service)` | memory and latency back to baseline, and not still accumulating |
| S7 | cpu-saturation | a job burst pins notification-service's single replica | `scale_service(notification-service)` (a restart meets the same load) | cpu and latency back to baseline |
| S8 | network-latency | 800ms added on the gateway→payment path | **none in scope** | escalated to humans |

## 8. Build order (vertical slices)

1. Domain + state machine + settings + logging (unit tests)
2. Simulator engine + HTTP (unit tests for dynamics)
3. Telemetry port + detection + correlation (unit tests)
4. Flow packs + registry + authorizer + policy + executor + tools (unit + security tests)
5. LLM provider + agent + hypotheses + evidence (evals with scripted LLM, then real)
6. Temporal workflow + approvals + verification + rollback + memory (workflow tests)
7. Postgres/Redis adapters + API + SSE (integration tests)
8. Compose + Dockerfiles + E2E harness + chaos
9. Frontend
10. Observability, docs, ADRs, Kubernetes/Helm, CI
11. Adversarial review of the finished system, then fix what it finds

Step 11 is not decoration. A commissioned review of the built system found sixteen weaknesses, and
the two that mattered most were invisible to every test that existed at the time: the idempotency
key for a remediation was reserved in the *caller's* transaction (so a worker killed mid-remediation
rolled the reservation back and the Temporal retry repeated the infrastructure change), and an
approval was matched by id alone rather than bound to the exact tool and arguments it approved.
Both are now fixed and regression-tested (`tests/integration/test_exactly_once_claim.py`,
`tests/security/test_hardening.py`). The lesson worth keeping: exactly-once is a property of
transaction boundaries, not of a uniqueness constraint.
