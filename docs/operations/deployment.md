# Deployment

## Topology

Five Aegis processes, one image (`Dockerfile`, `python:3.12-slim-bookworm`, non-root user `aegis` uid 1000,
`uv sync --frozen`), plus the console:

```
                 ┌──────────┐   REST/SSE    ┌───────────┐
   browser ─────▶│  web     │──────────────▶│  api  ×N  │──▶ Postgres (aegis)
                 │ :3600    │               │  :8600    │──▶ Redis (pub/sub, rate limit)
                 └──────────┘               └─────┬─────┘──▶ Temporal (signals, describe)
                                                  │ ──▶ simulator :8601 (control plane proxy)
   ┌───────────────┐   telemetry    ┌──────────┐  │
   │ detector ×N   │◀──────────────▶│simulator │  │
   │ (1 leader)    │──intake──▶ Postgres        │  │
   │ :9465 metrics │──start wf─▶ Temporal ◀─────┼──┼── worker ×N  (IncidentWorkflow + activities,
   └───────────────┘                            │  │   LangGraph checkpoints in Postgres, :9464)
                                                └──┴──▶ OpenAI (worker only; api only for memory search)
```

- **api**: stateless. `/health` is liveness, `/ready` is readiness (below). Serves SSE by replaying
  from Postgres and following Redis pub/sub, so any replica can serve any stream.
- **worker**: runs everything durable, including the agent and all LLM calls. Needs Postgres (data
  and checkpoints), Redis (event publish), Temporal, the simulator/telemetry source and the LLM key.
- **detector**: N replicas allowed; exactly one holds the Redis lock `detector-leader` (TTL
  `max(15, 4 × AEGIS_DETECTION_INTERVAL_SECONDS)`), renewed each cycle. Non-leaders retry the lock
  every interval and take over when the lease lapses. `AEGIS_LLM_PROVIDER=disabled` is fine here.
- **simulator**: a single stateful process for demos, staging and evals. Not part of a production
  control plane; production reads a real telemetry backend through `TelemetryProvider`
  (`AEGIS_TELEMETRY_PROVIDER=prometheus` today; logs/traces/health still come from the fallback
  adapter).
- **migrate**: one-shot `aegis migrate` (Alembic `upgrade head`) that must complete before api,
  worker and detector start.
- **web**: Next.js console (`apps/web`, `node:20-alpine`, uid 1001), container port 3000, mapped to
  3600 locally. `src/app/` has thirteen `page.tsx` routes — overview, `incidents`, `incidents/[id]`,
  `approvals`, `agent-runs`, `agent-runs/[id]`, `flows`, `memory`, `policies`, `simulation`,
  `system`, `tools`, `topology` — plus `layout.tsx`, `error.tsx` and `not-found.tsx`, on top of the
  API client, SSE, query and formatting libraries in `src/lib/`.

## Docker Compose

`docker-compose.yml` has two layers:

- default (no profile): `postgres` (`pgvector/pgvector:pg17`, `max_connections=200`), `redis`
  (`redis:7-alpine`, AOF), `temporal` (`temporalio/auto-setup:1.25.2` using the same Postgres for
  `temporal` and `temporal_visibility`), `temporal-ui` (:8233), `simulator`, `migrate`, `api`,
  `worker`, `detector`, `web`. Start with `make dev` (`docker compose up --build`).
- `--profile observability`: `prometheus` (5 s scrape of api, worker, detector, simulator,
  collector; 2 d retention), `grafana` (:3000, provisioned datasources Prometheus/Tempo/Loki and the
  two dashboards under `infrastructure/grafana/provisioning/dashboards/`), `tempo` (:3200), `loki`
  (:3100) with `promtail` reading container logs from the Docker socket, `otel-collector`
  (:4317/:4318 → Tempo and a Prometheus exporter on :8889). Start with `make observability`. Set
  `AEGIS_OTEL_ENABLED=true` to send traces.

The plan mentions `core` and `test` profiles; they do not exist. The core services are the unprofiled
defaults, and integration-test databases are started by `make test-infra` with plain `docker run`.

Configuration is shared through the `x-aegis-env` anchor; overridable variables:
`AEGIS_ENVIRONMENT`, `AEGIS_LOG_LEVEL`, `AEGIS_TELEMETRY_PROVIDER`, `AEGIS_LLM_PROVIDER`,
`AEGIS_LLM_API_KEY`, `AEGIS_LLM_REASONER_MODEL`, `AEGIS_LLM_FAST_MODEL`, `AEGIS_LLM_REASONING_EFFORT`,
`AEGIS_OTEL_ENABLED`, `AEGIS_API_AUTH_MODE`, `AEGIS_API_KEYS`, `AEGIS_APPROVAL_TIMEOUT_SECONDS`,
`AEGIS_DETECTION_INTERVAL_SECONDS`, `AEGIS_SIMULATOR_SEED`, `OPENAI_API_KEY_FILE`,
`NEXT_PUBLIC_AEGIS_API_URL`. The OpenAI key is a compose secret mounted at
`/run/secrets/openai_api_key` in `api` and `worker` only.

Health checks: postgres `pg_isready`, redis `redis-cli ping`, temporal `tctl cluster health`
(`start_period: 20s`), simulator `GET /health`, api `GET /ready` (20 s), worker and detector `GET /`
on their metrics ports (30 s each). `temporal-ui` and `web` have no healthcheck. `worker` has
`stop_grace_period: 45s`; `migrate` has `restart: "no"` and everything downstream of it waits on
`condition: service_completed_successfully`.

## Kubernetes and Helm

Both trees are written and validated: `deploy/kubernetes/` (28 files — a Kustomize base, `dev` and
`production` overlays, and an optional `monitoring` kustomization) and `deploy/helm/aegis/` (30
files — chart `0.1.0`, `kubeVersion >= 1.27`). They deploy the same topology; pick one and do not
mix them in a namespace. `deploy/kubernetes/README.md` is the operator walkthrough (kind bootstrap,
production steps, validation commands, cheatsheet) and the chart prints the equivalent from
`templates/NOTES.txt`.

### The two trees side by side

| | Kustomize | Helm |
|---|---|---|
| apply | `kubectl apply -k deploy/kubernetes/overlays/{dev,production}` (the base is never applied directly) | `helm upgrade --install aegis deploy/helm/aegis -f values-{dev,production}.yaml` |
| non-secret config | `configMapGenerator` for `aegis-config` and `aegis-web-config`; the generated names carry a content hash, so a changed value rolls every Deployment. Overlays override keys with `behavior: merge` | `templates/configmap.yaml` rendered from typed `config.*` values (plus `config.extraEnv`, written verbatim); Deployments carry a `checksum/config` annotation |
| secret | `aegis-secrets` is created out of band — `base/secret.example.yaml` is a template deliberately *not* listed in `kustomization.yaml`; the dev overlay generates a throwaway one | `secrets.existingSecret`, or rendered from the `secrets.*` literals when any of them is set |
| namespace | `base/namespace.yaml` creates `aegis` with Pod Security Admission `enforce: baseline`, `warn`/`audit: restricted`; `overlays/production/patches/namespace-restricted.yaml` raises enforcement to `restricted` | no `Namespace` object at all, and therefore **no PSA labels** — label the namespace yourself |
| migrations | a plain `Job aegis-migrate` applied alongside everything else (`backoffLimit: 3`, `activeDeadlineSeconds: 900`, `ttlSecondsAfterFinished: 600`) | the same Job as a `pre-install,pre-upgrade` hook (weight `0`, delete policy `before-hook-creation,hook-succeeded`), plus a hook-weighted (`-5`) Secret carrying `AEGIS_DATABASE_URL`, because a pre-install hook runs before the release's own Secret exists |
| in-cluster infrastructure | `overlays/dev/` only | `devInfra.enabled` / `simulator.enabled`, both off by default |
| only here | the `monitoring/` kustomization, the `Namespace`, `secret.example.yaml` | a web HPA, `NOTES.txt`, and the simulator plus dev infrastructure as chart templates |

### Workloads, replicas and probes

Identical in both trees; `(prod)` means the production overlay / `values-production.yaml`:

| process | replicas | HPA | PDB | Service | notes |
|---|---|---|---|---|---|
| api | 2 (prod 3) | 2–6 (prod 3–12), CPU 70 %, `scaleDown.stabilizationWindowSeconds: 300` | `minAvailable: 1` (prod 2) | ClusterIP 8600 | startup + readiness (`/ready`) + liveness (`/health`), grace 30 s |
| worker | 2 (prod 3) | none | `minAvailable: 1` (prod 2) | none | `terminationGracePeriodSeconds: 60` (the compose value is 45) |
| detector | **1 everywhere** | none | none | none | `base/detector.yaml` argues the case: the Redis leader lock makes extra replicas warm standbys, so one is the right number. Started with `AEGIS_LLM_PROVIDER=disabled` |
| web | 2 | Helm only (`web.autoscaling`, off by default, 2–6 in production) | `minAvailable: 1` | ClusterIP 3000 | uid 1001, extra `emptyDir` at `/app/.next/cache` |
| simulator | 1, `strategy: Recreate` | – | – | ClusterIP 8601 | dev overlay / `simulator.enabled` only |

Neither worker nor detector has a **readiness** probe: they receive no Service traffic, so a
`startupProbe` plus `livenessProbe` on `GET /` of the metrics port (9464 / 9465) is all there is —
the manifests say so in a comment. Compose does healthcheck them on `/`, which is the same signal
with a different name. The production overlay adds zone `topologySpreadConstraints` to api and
worker and raises requests/limits; `patches/hpa.yaml` only widens the api HPA's bounds.

Every first-party pod runs non-root (uid 1000, web 1001) with `readOnlyRootFilesystem: true`, all
capabilities dropped, `RuntimeDefault` seccomp, `automountServiceAccountToken: false` and a
size-limited `emptyDir` at `/tmp`. The `aegis` ServiceAccount exists only to be named; Aegis never
calls the Kubernetes API.

### Secrets and configuration

`aegis-secrets` holds four keys: `AEGIS_DATABASE_URL` and `AEGIS_REDIS_URL` (required), plus
`AEGIS_LLM_API_KEY` and `AEGIS_API_KEYS`, both injected with `secretKeyRef … optional: true` so
pods still start when the provider is disabled or auth is off. **The LLM key is a plain environment
variable in Kubernetes, not a file**: `AEGIS_LLM_API_KEY_FILE` appears nowhere under `deploy/` — the
file convention is the compose/local path, where the key arrives as a mounted compose secret. If you
want the file behaviour in-cluster you have to mount the Secret as a volume and set
`AEGIS_LLM_API_KEY_FILE` yourself. `overlays/production/externalsecret.example.yaml` shows the
External Secrets Operator path (a `ClusterSecretStore` named `aegis-store`, `refreshInterval: 1h`);
it ships commented out of `kustomization.yaml`.

`AEGIS_ENVIRONMENT=production` requires `AEGIS_API_AUTH_MODE=api_key` (settings validation refuses
`disabled`) and switches the policy to approval-for-every-mutation with deny for high-risk
mutations; both production configurations set it and expect `AEGIS_API_KEYS` to be populated. The
detector, simulator and the migrate Job force `AEGIS_LLM_PROVIDER=disabled` — they never call the
model, so the key never reaches those pods.

`flows/` and `policies/` are baked into the image at `/app/flows` and `/app/policies`
(`AEGIS_FLOWS_DIR`, `AEGIS_POLICIES_DIR` are set in the `Dockerfile`). Neither tree mounts a
`ConfigMap` over them; if you want to change policy without rebuilding, add that mount yourself and
bump flow pack versions when you do.

### Network and monitoring

`base/networkpolicy.yaml` (and `templates/networkpolicy.yaml`, gated on `networkPolicy.enabled`)
ships `default-deny-ingress` for the namespace and then three explicit allows: `allow-api` (8600,
from the `ingress-nginx` namespace, the web pods and the `monitoring` namespace), `allow-web` (3000,
from `ingress-nginx`) and `allow-metrics-scrape` (9464/9465 for worker and detector, from
`monitoring`). The dev overlay adds two more — `allow-dev-infra` (postgres/redis/temporal/simulator
reachable from any pod in the namespace) and `allow-temporal-ui`. **Egress is deliberately left
open** — the LLM endpoint and the managed data services are outside the cluster — and the file says
so. The namespace selectors assume `kubernetes.io/metadata.name` labels of `ingress-nginx` and
`monitoring`; the chart makes both configurable.

One Ingress fronts both hosts (`api.aegis.example.com` → api, `aegis.example.com` → web) with
`proxy-buffering: "off"` and 3600 s read/send timeouts because the API streams SSE. Production adds
`cert-manager.io/cluster-issuer`, `force-ssl-redirect` and `limit-rps: "50"`; dev drops TLS and uses
`*.localtest.me`, adding a third rule for the Temporal UI.

Scraping needs the Prometheus Operator CRDs and is **off by default**. There is a `ServiceMonitor`
for the **API only** (port `http`, path `/metrics`); worker and detector are scraped by
**`PodMonitor`s** on their `metrics` port, since neither has a Service. Kustomize keeps the three
objects in `deploy/kubernetes/monitoring/` (`kubectl apply -k`, with a `release:
kube-prometheus-stack` label that must match your `serviceMonitorSelector`/`podMonitorSelector`);
Helm renders the ServiceMonitor from one template and both PodMonitors from another that ranges over
`worker` and `detector`, behind `monitoring.serviceMonitor.enabled` / `monitoring.podMonitor.enabled`.
Traces go out with `AEGIS_OTEL_ENABLED=true` and `AEGIS_OTEL_EXPORTER_ENDPOINT` (on in production).

### Production: managed dependencies

Neither tree deploys Postgres, Redis or Temporal in production. Point the runtime at managed
services: `AEGIS_DATABASE_URL` (a pgvector-enabled Postgres — the migration runs `CREATE EXTENSION
vector`) and `AEGIS_REDIS_URL` through the Secret, `AEGIS_TEMPORAL_ADDRESS` through the ConfigMap
(Temporal Cloud `<namespace>.<account>.tmprl.cloud:7233`, or a dedicated cluster), and
`AEGIS_SIMULATOR_URL` at the real infrastructure gateway — `/ready` checks it, so it must be
reachable. Do not reproduce the compose single-Postgres arrangement: Temporal's persistence and
Aegis' data have different backup and scaling needs. Images are pinned to the immutable tags CI
pushes (`sha-<full commit sha>`); the overlays and `values-production.yaml` ship `sha-CHANGEME` /
an empty tag on purpose so an unedited apply fails loudly rather than pulling `latest`.

### Dev: kind or minikube

The dev overlay is a complete single-node stack: it patches api/worker/web to one replica, deletes
the api HPA and all three PDBs, and adds single-replica in-cluster infrastructure — a pgvector
Postgres `StatefulSet` with a 2 Gi PVC (its init ConfigMap mirrors
`infrastructure/postgres/init/01-temporal-databases.sh`), Redis, Temporal `auto-setup` plus
`temporalio/ui`, and the simulator. It sets `AEGIS_LLM_PROVIDER=disabled` (deterministic planner, no
key needed), generates a throwaway `aegis-secrets`, and exposes everything on `*.aegis.localtest.me`
over plain HTTP. `values-dev.yaml` is the same arrangement via `devInfra.enabled: true`.

### Validation

Neither tree needs a cluster to check, and CI does it on every push and pull request — the
`manifests` job of `.github/workflows/ci.yml` runs `kubectl kustomize` over `overlays/dev`,
`overlays/production` and `monitoring`, then `helm lint` and `helm template` for the chart's default,
dev and production value sets. The same commands work locally, and
`deploy/kubernetes/README.md` adds the cluster-side ones (`kubectl apply --dry-run=server`,
`kubeconform -strict`, `kubectl diff -k`).

The rest of `ci.yml` is the gate the manifests sit beside: `lint` (`uv lock --check`, ruff check and
format, `lint-imports`), `typecheck` (`mypy src/aegis`), `unit` (`tests/unit tests/security`),
`integration` (against service containers on the same ports `make test-infra` uses — pgvector on
5439, Redis on 6389), `workflow` (`tests/workflow` with Temporal's time-skipping server), `web`
(`npm ci`, lint, typecheck, build), `evals-offline` (`python -m evals.run`, results uploaded as an
artifact) and `docker`, which builds both images and pushes them to
`ghcr.io/<repo>/{aegis,web}` — tagged `sha-<full sha>` plus `latest` — only on `main`; pull requests
just prove the images build. `.github/workflows/e2e.yml` is separate and never gates a merge: it is
nightly (03:00 UTC) or `workflow_dispatch`, brings the compose stack up with
`AEGIS_LLM_PROVIDER=disabled`, waits up to 300 s for `/ready`, runs `scripts/e2e.py` over the
scenarios you name (default `redis-connection-leak bad-deployment`) and uploads the logs.

### Two dead environment variables

Both trees (and `docker-compose.yml`) set `AEGIS_API_URL` for the console — it is read by nothing.
The console reads only `NEXT_PUBLIC_*` (`apps/web/src/lib/config.ts`). Conversely
`NEXT_PUBLIC_AEGIS_API_KEY` *is* read there, and used both as the `X-Aegis-Key` header and as the
`?key=` query parameter for SSE — but it is set nowhere outside `apps/web/Dockerfile`'s empty-default
build arg and `.env.example`. In the production overlay, which mandates
`AEGIS_API_AUTH_MODE=api_key`, the console therefore cannot authenticate. Worse, `NEXT_PUBLIC_*`
values are inlined into the browser bundle at **image build time**, so the ConfigMap entries the
trees do set are inert unless the image was built with matching `--build-arg`s (the manifests say
this; CI passes only `NEXT_PUBLIC_AEGIS_API_URL` and `NEXT_PUBLIC_TEMPORAL_UI_URL`). Build the web
image with the key and the URLs you intend to serve.

## Readiness semantics

`GET /ready` (`src/aegis/api/v1/system.py`, `aegis.application.bootstrap.readiness`) returns
`{"status": "ready"|"not_ready", "checks": {...}}` with HTTP 200 or 503. Checks:

| check | ok when |
|---|---|
| `database` | a connection succeeds and the Alembic revision equals the script head (`revision`, `head` are reported) |
| `redis` | `PING` succeeds |
| `temporal` | present only when `AEGIS_TEMPORAL_ENABLED`; the service client's health check succeeds |
| `simulator` | `GET {AEGIS_SIMULATOR_URL}/health` returns `status: ok` |

Any false check makes the whole endpoint 503, so a Redis outage takes the API out of the load
balancer even though reads still work (`tests/chaos/test_dependency_outage.py`). `GET /health`
always returns 200 with the version. `uv run aegis check` runs the same checks plus LLM health from
the CLI. Worker and detector readiness is their metrics endpoint responding; their functional health
is visible in `aegis_detection_cycle_seconds` (detector) and `aegis_agent_runs_total` /
`aegis_remediation_total` (worker).

## Graceful shutdown

- **api**: uvicorn `timeout_graceful_shutdown=20`; the lifespan closes the Temporal client, Redis,
  simulator clients and the engine. Open SSE connections are dropped; clients reconnect with
  `Last-Event-ID` and receive the events they missed.
- **worker**: SIGINT/SIGTERM set a stop event; leaving `async with worker` asks Temporal to stop
  polling and waits for running activities. An activity that does not finish in time is retried on
  another worker; the agent resumes from its LangGraph checkpoint and mutations are idempotent, so
  a hard kill is safe (`tests/chaos/test_worker_crash.py`). Give it at least 45 s.
- **detector**: stops after the current cycle and releases the leader lock so a standby takes over
  immediately instead of after the TTL.
- **simulator**: stopping it loses the simulated world's history; `/ready` on the API goes 503 and
  the detector logs `detector.cycle_failed` until it returns, then re-bootstraps baselines.

## Scaling

| process | replicas | coordination |
|---|---|---|
| detector | N | one leader via Redis `SET NX PX`; others idle-poll the lock every interval |
| worker | N | Temporal task queue `aegis-incidents`; `max_concurrent_activities=8` per worker; exactly-once mutations via the ledger's unique key |
| api | N | stateless; rate limiting and SSE fan-out in Redis; sequence numbers and optimistic locking in Postgres |
| simulator | 1 | stateful world; not horizontally scalable |

Budget the LLM: each incident phase is one `AgentProposal` call per iteration on the reasoner (up to
the pack's `max_iterations` and `max_llm_calls`), plus fast-tier calls for test judgements and one
memory summary. The circuit breaker (3 failures, 60 s) degrades to the deterministic planner rather
than stalling.

## Metrics and dashboards

All `aegis_*` series are listed in `docs/adr/ADR-007-observability.md`. The provisioned Grafana
dashboards (`infrastructure/grafana/provisioning/dashboards/`, generated by
`scripts/gen_dashboards.py`):

- **Aegis — Runtime Overview**: active incidents, incidents opened (24 h), approval wait p50,
  resolution time p50/p95, policy denials (1 h), tool calls by tool, tool outcomes, LLM tokens by
  kind, LLM latency p95, remediations, verification outcomes, agent runs by termination, API latency
  p95 by route, detection cycle duration p95.
- **Aegis — Simulated Infrastructure**: service p95 latency and error rate, Redis and Postgres
  connections vs max, pool waits, CPU, memory, availability, active faults, gateway request rate
  (from the simulator's `sim_*` series).

Alerting suggestions (not shipped): `aegis_incidents_active` high for long, `rate(aegis_llm_calls_total{outcome!="ok"}[5m])`
rising, `aegis_verification_total{status="failed"}` increasing, no `aegis_detection_cycle_seconds`
samples for 60 s (no leader), `aegis_approval_wait_seconds` p95 approaching the timeout.

## Backups and data

- **Postgres** is the only durable store: incidents, timeline, evidence, hypotheses, plans,
  approvals, immutable audit events, agent runs and steps, tool execution ledger, memories with
  embeddings, LangGraph checkpoints, and (locally) Temporal's databases. Back it up with your
  provider's PITR or `pg_dump`; the compose volume is `postgres-data`. Restoring an older snapshot
  while Temporal workflows are in flight will desynchronize workflow state from incident rows;
  restore both or terminate the affected workflows.
- **Redis** is derived: pub/sub, leader lock, rate counters, detector baselines (rebuilt from 600 s
  of telemetry history on the next bootstrap). No backup needed.
- **Prometheus/Tempo/Loki** volumes are observability history with short retention.
- **Audit events** cannot be updated or deleted through SQL (`audit_events_immutable` trigger);
  retention must be handled by partition/archival at the database level, not by `DELETE`.
- **Schema**: `aegis migrate` is idempotent; `/ready` reports `revision` vs `head`. There is exactly
  one revision, `migrations/versions/0001_initial.py`: it runs `CREATE EXTENSION IF NOT EXISTS
  vector`, creates the tables from the SQLAlchemy metadata, the HNSW and GIN indexes on
  `incident_memories`, and the `audit_events_immutable` trigger. Temporal's `temporal` and
  `temporal_visibility` databases are not Alembic's business: locally they come from
  `infrastructure/postgres/init/01-temporal-databases.sh`, which compose mounts into the Postgres
  container's `/docker-entrypoint-initdb.d` (the dev overlay mirrors it in a ConfigMap), and in
  production Temporal owns its own database.
