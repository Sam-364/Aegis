# Aegis

**A durable autonomous incident-response runtime in which an AI agent is one controlled
decision-making component.**

```
THE MODEL PROPOSES.        THE RUNTIME DECIDES.        THE POLICY ENGINE AUTHORIZES.
THE TOOL EXECUTES.         THE OBSERVABILITY LAYER RECORDS.        THE VERIFICATION ENGINE PROVES.
```

Aegis ingests telemetry, detects anomalies statistically, opens incidents, and drives each one
through a Temporal workflow: an LLM-backed agent investigates inside a flow-scoped, policy-gated
tool runtime; a deterministic engine scores its hypotheses; a human approves the remediation; the
workflow executes it exactly once; a verification engine proves the metrics recovered before the
incident is marked resolved; and a structured memory of the incident is kept for the next one.

Aegis is not a chatbot that can run commands. Every tool call the model asks for passes a
fourteen-check authorization gate, mutating tools cannot run inside the reasoning loop at all, and
hypothesis confidence is computed by the runtime from evidence rather than asserted by the model.

---

## What it looks like when something breaks

```
INC-1042                                                                 SEV-3
redis connections saturation                                   RESOLVED in 2m16s
────────────────────────────────────────────────────────────────────────────────
  +00:21  detector      Anomaly detected: redis connections 12 → 240 (+1900%)
  +00:22  workflow      Flow selected: api-latency-investigation@1.1.0
  +00:25  agent         [triage] inspect_dependencies(api-gateway)
  +00:41  agent         [investigate] inspect_redis → order-service holds 94% of connections
  +01:07  agent         Hypothesis: order-service has a client-side connection leak to Redis
                        confidence 0.82 = evidence 0.91 · dependency 1.00 · temporal 1.00
  +01:31  agent         [validate] run_cache_diagnostic → confirmed
  +01:50  agent         Remediation proposed: restart_service(order-service)   risk MEDIUM
  +01:50  policy        require approval (approve-medium-risk-mutations)
  +01:53  human         Approved by Local operator
  +01:53  workflow      restart_service executed (idempotency key 3f9a…)
  +02:43  workflow      Verification passed: 24 conditions held for 3 consecutive polls
                        api-gateway.latency_p95_ms 2450 → 212 (−91%)   redis.connections 255 → 12
  +02:43  workflow      Incident resolved · memory stored
```

That transcript is from a real run against the local stack with `gpt-5-mini` (14 model calls,
~34k tokens, about two cents).

## Architecture

```
 ┌──────────────────────────────────────────────────────────────────────────────┐
 │  Next.js console  ── REST + SSE ──▶  FastAPI control plane                   │
 ├──────────────────────────────────────────────────────────────────────────────┤
 │  Detector ──▶ Correlator ──▶ Intake ──▶ Temporal IncidentWorkflow            │
 │                                          │ triage                            │
 │                                          │ run_agent_phase ×N  ◀── LangGraph │
 │                                          │   observe → propose → authorize   │
 │                                          │   → execute (read-only tools)     │
 │                                          │   → score hypotheses → decide     │
 │                                          │ policy → approval (signal, timer) │
 │                                          │ execute_remediation (exactly once)│
 │                                          │ verify → resolve | rollback       │
 │                                          │ extract memory (pgvector)         │
 ├──────────────────────────────────────────────────────────────────────────────┤
 │  Postgres (system of record, JSONB documents + projections, immutable audit) │
 │  Redis (events fan-out, leader lock, rate limits, detector baselines)        │
 │  Simulated distributed system (gateway → 6 services → postgres, redis)       │
 └──────────────────────────────────────────────────────────────────────────────┘
```

The Python code is a single distribution with enforced layering (`import-linter`):
`domain → ports → runtime core (flow, policy, tools, hypotheses, detection, agent, verification,
memory, llm) → infrastructure / application → api, workflows, apps`. The simulator is a separate
package that cannot import the runtime, so the agent cannot cheat by reading ground truth.

Design records: [`docs/architecture/PLAN.md`](docs/architecture/PLAN.md) (amended plan),
[`docs/architecture/`](docs/architecture/), [`docs/adr/`](docs/adr/).

### The authorization gate

Every tool request, whether from the model inside the loop or from the workflow for a remediation,
passes these checks in order and the full trace is stored and shown in the console:

```
tool_registered → tool_enabled → incident_active → flow_allows → phase_allows →
category_permitted → environment_allows → severity_allows → actor_permitted →
risk_within_ceiling → policy_effect → arguments_valid → idempotency → budget_available
```

Three invariants cannot be overridden by policy YAML: dangerous tools are never executable,
mutating tools never run inside the agent loop, and the agent can neither execute a mutation nor
approve one.

The built system was then reviewed adversarially and the findings fixed — the two that mattered
were an idempotency key reserved in the caller's transaction (a worker killed mid-remediation would
have repeated the infrastructure change on retry) and an approval matched by id rather than bound
to the exact tool and arguments it approved. Both are regression-tested; the review's full ground
is in [`docs/security/threat-model.md`](docs/security/threat-model.md).

## Quick start

Prerequisites: Docker with Compose v2, an OpenAI API key.

```bash
mkdir -p .secrets && cp /path/to/openai_api_key .secrets/openai_api_key   # or set OPENAI_API_KEY_FILE
docker compose up --build                       # core stack
docker compose --profile observability up --build   # + Prometheus, Grafana, Tempo, Loki
```

| Surface | URL |
|---|---|
| Console | http://localhost:3600 |
| API + OpenAPI docs | http://localhost:8600/api/docs |
| Simulated infrastructure | http://localhost:8601/api/topology |
| Temporal UI | http://localhost:8233 |
| Grafana (observability profile) | http://localhost:3000 (admin / aegis) |

Trigger an incident and watch it travel through the whole loop:

```bash
uv run aegis scenarios                       # list the eight scenarios
uv run aegis inject redis-connection-leak    # or use the Simulation page in the console
```

Approve the proposed remediation in the console (or `POST /api/v1/approvals/{id}/approve`).

## Scenarios

Ground truth lives in one place, `src/aegis/simulator/faults.py`, and drives the simulator, the
end-to-end harness and the evals.

| Scenario | Root cause | Correct remediation | Expected outcome |
|---|---|---|---|
| `redis-connection-leak` | order-service leaks Redis connections | restart or rotate order-service pool | resolved |
| `bad-deployment` | payment-service 2.4.0 regression | rollback payment-service | resolved |
| `db-pool-exhaustion` | user-service idle-in-transaction sessions | rotate pool / restart user-service | resolved |
| `cascading-dependency` | inventory-service crash cascades upward | restart inventory-service (not the gateway) | resolved |
| `transient-spike` | 45 s traffic burst | none | resolved without action |
| `memory-leak` | payment-service heap growth | restart payment-service | resolved |
| `cpu-saturation` | notification-service pinned | scale notification-service | resolved |
| `network-latency` | added latency on one network path | none available | escalated to humans |

Wrong remediations are modelled too: restarting the gateway during the cascade, or rolling back a
service that was never deployed, leaves the fault in place and fails verification.

## Testing and evaluation

```bash
make test               # unit + security (adversarial tool abuse), no infrastructure
make test-infra         # throwaway pgvector Postgres :5439 and Redis :6389
make test-integration   # Postgres/Redis adapters, bootstrap wiring
make test-workflow      # full incident lifecycle on Temporal's time-skipping test server
make evals              # component + agent evals (offline, deterministic planner)
make evals ARGS="--llm" # adds real-model suites
make e2e                # five scenarios against the running compose stack
make chaos              # kill the worker mid-incident, restart the API, stop Redis
```

Latest full sweep — `evals/results/20260911T082507Z-llm.md`, every suite, real model:

| Suite | Result |
|---|---|
| detection | 8/8 scenarios detected as one incident each, mean time to detect 17.5 s, 0 false positives over 30 clean minutes |
| authorization | 22/22 (15 adversarial requests refused, 0 false allows) |
| hypothesis ranking | true root cause ranked first in 6/7, MRR 0.89 |
| verification | 12/12 — correct remediation passes, incorrect one fails, for every scenario pair |
| memory recall | 10/10 recall@1 |
| agent (deterministic planner, no LLM) | 8/8 correct plans, 0 mutation violations |
| agent (`gpt-5-mini`) | 8/8 correct plans, 0 mutation violations, 11 model calls per incident |
| llm structured output (`gpt-5-mini`) | 3/3 |

Against the running stack: **5/5 end-to-end scenarios** resolved with the correct remediation
(`make e2e`) and **4/4 chaos tests** — worker SIGKILLed mid-remediation, API restarted, Redis
stopped, simulator restarted (`make chaos`). Offline: 174 unit and security tests, 14
Postgres/Redis integration tests, 4 Temporal workflow tests.

The agent evals are the part worth reading: four real agent-behaviour bugs and one inconsistency in
the scenario ground truth were found only by running the real model against the simulator — the
deterministic planner passed all eight scenarios throughout. `docs/evals.md` keeps the failing
report and what each failure changed in the runtime.

## Configuration

Everything is environment-driven (`AEGIS_*`, see [`.env.example`](.env.example)). The LLM key is
read from `AEGIS_LLM_API_KEY_FILE` so it never has to be written into a compose file. Model tiers:
`AEGIS_LLM_REASONER_MODEL` (default `gpt-5-mini`) for investigation steps and
`AEGIS_LLM_FAST_MODEL` (default `gpt-5-nano`) for judgements and summaries. Any OpenAI-compatible
endpoint works via `AEGIS_LLM_PROVIDER=openai_compatible` and `AEGIS_LLM_BASE_URL`. With the model
unavailable the runtime continues with a deterministic planner.

## Repository layout

```
src/aegis/          runtime (see docs/architecture/overview.md for the layer map)
flows/              declarative, versioned flow packs (YAML)
policies/           policy rules (YAML); invariants live in code
migrations/         Alembic
tests/              unit · security · integration · workflow · chaos
evals/              eval suites and reports
scripts/            e2e harness, OpenAPI export, dashboard generator
apps/web/           Next.js operations console
deploy/             Kubernetes (kustomize) and Helm
infrastructure/     Prometheus, Grafana, Tempo, Loki, OTel collector configs
docs/               architecture, ADRs, operations, security, development guides
```

## Documentation

- [Architecture overview](docs/architecture/overview.md) · [Incident lifecycle](docs/architecture/incident-lifecycle.md)
- [ADRs](docs/adr/) · [Security model](docs/security/model.md) · [Threat model](docs/security/threat-model.md)
- [Development setup](docs/development/setup.md) · [Flow pack authoring](docs/development/flow-pack-authoring.md) · [Tool authoring](docs/development/tool-authoring.md)
- [Deployment](docs/operations/deployment.md) · [Runbook](docs/operations/runbook.md) · [Evals](docs/evals.md)
- [API contract](docs/api/frontend-contract.md) · [OpenAPI](docs/api/openapi.json)

## License

Apache-2.0
