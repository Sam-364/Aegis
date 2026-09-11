# Development setup

## Prerequisites

- Python 3.12 (`requires-python = ">=3.12,<3.13"`; `.python-version` pins `3.12`).
- [`uv`](https://docs.astral.sh/uv/) for dependency management (`uv.lock` is authoritative).
- Docker with the `docker compose` plugin for the local stack, integration containers and chaos tests.
- Node 20 and npm for the console in `apps/web` (only if you work on the frontend; the API and
  runtime do not need it).
- An OpenAI API key if you want the real model. Everything else (tests, evals baseline, the stack
  with `AEGIS_LLM_PROVIDER=disabled`) works without one.

## Install

```bash
uv sync --extra dev          # runtime + dev tools (pytest, ruff, mypy, import-linter, hypothesis…)
make setup                   # same, plus `npm install` in apps/web
cp .env.example .env         # optional; Settings read .env with prefix AEGIS_
```

Settings are defined in `src/aegis/config.py` (`pydantic-settings`, prefix `AEGIS_`). Validation
happens at startup: `AEGIS_LLM_PROVIDER=openai` requires `AEGIS_LLM_API_KEY` or
`AEGIS_LLM_API_KEY_FILE`, and `AEGIS_ENVIRONMENT=production` refuses `AEGIS_API_AUTH_MODE=disabled`.

### API key file convention

Put the key in a file and point `AEGIS_LLM_API_KEY_FILE` at it; the key never appears in a compose
file, shell history or the repository. `docker-compose.yml` declares a compose secret
`openai_api_key` from `${OPENAI_API_KEY_FILE:-./.secrets/openai_api_key}` and mounts it at
`/run/secrets/openai_api_key`, which is what `AEGIS_LLM_API_KEY_FILE` is set to inside the api and
worker containers. `.secrets/` is git-ignored. `.env.example` currently points the file variable at a
machine-specific path (`/home/skylark/Downloads/openai_api_key`); change it to your own path or to
`./.secrets/openai_api_key`. An empty `AEGIS_LLM_API_KEY` is treated as "not provided".

## Environment variables

From `.env.example` and `src/aegis/config.py`, with defaults from `Settings`
(`AEGIS_TEMPORAL_ENABLED`, `AEGIS_PROMETHEUS_URL` and `AEGIS_LLM_EMBEDDING_DIMENSIONS` are
`Settings` fields with no line in `.env.example`):

| variable | default | notes |
|---|---|---|
| `AEGIS_ENVIRONMENT` | `development` | `development` / `staging` / `production`; drives policy rules and the auth requirement |
| `AEGIS_LOG_LEVEL`, `AEGIS_LOG_FORMAT` | `INFO`, `json` (`console` in `.env.example`) | |
| `AEGIS_DATABASE_URL` | `postgresql+asyncpg://aegis:aegis@localhost:5432/aegis` | Alembic and the LangGraph checkpointer use the psycopg form derived from it |
| `AEGIS_REDIS_URL` | `redis://localhost:6379/0` | |
| `AEGIS_TENANT_ID` | `default` | stamped on every incident at intake and matched by policy rules |
| `AEGIS_TEMPORAL_ADDRESS`, `AEGIS_TEMPORAL_NAMESPACE` | `localhost:7233`, `default` | `AEGIS_TEMPORAL_ENABLED=false` runs API/detector without Temporal (incidents are recorded, no workflow) |
| `AEGIS_TEMPORAL_TASK_QUEUE` | `aegis-incidents` | worker and workflow starter must agree |
| `AEGIS_SIMULATOR_URL` | `http://localhost:8601` | |
| `AEGIS_TELEMETRY_PROVIDER` | `simulator` | `prometheus` reads metrics via PromQL from `AEGIS_PROMETHEUS_URL` (default `http://localhost:9090`) with the simulator as fallback |
| `AEGIS_LLM_PROVIDER` | `openai` | `openai` / `openai_compatible` (+ `AEGIS_LLM_BASE_URL`) / `scripted` / `disabled` |
| `AEGIS_LLM_API_KEY_FILE` / `AEGIS_LLM_API_KEY` | — | see above |
| `AEGIS_LLM_REASONER_MODEL`, `AEGIS_LLM_FAST_MODEL` | `gpt-5-mini`, `gpt-5-nano` | reasoner: agent proposals; fast: test judgements, memory summaries |
| `AEGIS_LLM_EMBEDDING_MODEL` | `text-embedding-3-small` | 1536 dimensions (`AEGIS_LLM_EMBEDDING_DIMENSIONS`) |
| `AEGIS_LLM_REASONING_EFFORT` | `low` | `minimal` / `low` / `medium` / `high` |
| `AEGIS_OTEL_ENABLED`, `AEGIS_OTEL_EXPORTER_ENDPOINT` | `false`, `http://localhost:4317` | |
| `AEGIS_API_PORT` | `8600` | |
| `AEGIS_API_AUTH_MODE` | `disabled` | `api_key` with `AEGIS_API_KEYS=key:role[:name],...` (roles `viewer`, `operator`, `admin`) |
| `AEGIS_API_CORS_ORIGINS` | `["http://localhost:3600"]` | JSON list |
| `AEGIS_DETECTION_INTERVAL_SECONDS` | `5` | detector poll interval; leader-lock TTL is `max(15, 4 × interval)` |
| `AEGIS_APPROVAL_TIMEOUT_SECONDS` | `900` | approval expiry; on timeout the incident escalates |
| `AEGIS_METRICS_PORT` | `9464` (worker) / `9465` (detector) | **not** a `Settings` field: read via `os.environ` in `src/aegis/apps/worker.py` and `src/aegis/apps/detector.py`, each with its own default, so one shared value makes the two processes collide (compose sets it per service) |
| `AEGIS_E2E_API` | `http://localhost:8600` | **not** a `Settings` field: read by `tests/chaos/conftest.py` to find the running stack |

Other settings with effect: `AEGIS_DETECTION_WINDOW_SECONDS` (600, bootstrap history),
`AEGIS_DETECTION_WARMUP_SAMPLES` (12), `AEGIS_DETECTION_ZSCORE_THRESHOLD` (3.0),
`AEGIS_DETECTION_EWMA_ALPHA` (0.2 for the detector process; the in-process default used by tests and
evals is 0.05), `AEGIS_CORRELATION_WINDOW_SECONDS` (180), `AEGIS_VERIFICATION_POLL_SECONDS` (5),
`AEGIS_AGENT_PHASE_TIMEOUT_SECONDS` (600), `AEGIS_FLOWS_DIR` (`flows`), `AEGIS_POLICIES_DIR`
(`policies`), `AEGIS_API_RATE_LIMIT_PER_MINUTE` (600), `AEGIS_SIMULATOR_SEED` (42),
`AEGIS_SIMULATOR_TICK_SECONDS` (1.0), and the LLM knobs `AEGIS_LLM_MAX_OUTPUT_TOKENS` (4000),
`AEGIS_LLM_TIMEOUT_SECONDS` (60), `AEGIS_LLM_MAX_RETRIES` (2),
`AEGIS_LLM_CIRCUIT_BREAKER_FAILURES` (3), `AEGIS_LLM_CIRCUIT_BREAKER_RESET_SECONDS` (60).
Declared but not read by any code path today: `detection_enabled`, `incident_cooldown_seconds`,
`agent_heartbeat_seconds`, `metrics_enabled`, `service_name`, `api_public_url`. Also
`detection_min_consecutive`: `DetectorConfig.min_consecutive` exists and is honoured, but
`src/aegis/apps/detector.py` never passes the setting through, so the per-rule defaults always win.

## Ports

| port | what |
|---|---|
| 8600 | Aegis API (`/api/v1`, `/api/docs`, `/api/redoc`, `/api/openapi.json`, `/health`, `/ready`, `/metrics`) |
| 8601 | simulator (`/api/...`, `/health`, `/metrics`) |
| 3600 | web console (`next dev -p 3600`; the container listens on 3000 and compose maps 3600→3000) |
| 7233 | Temporal frontend |
| 8233 | Temporal UI (compose maps 8233→8080) |
| 5432 | Postgres (`aegis`, `temporal`, `temporal_visibility`) |
| 6379 | Redis |
| 9464 / 9465 | worker / detector Prometheus endpoints |
| 9090 | Prometheus (observability profile) |
| 3000 | Grafana (observability profile; admin / `aegis`, anonymous viewer enabled) |
| 3200, 3100, 4317/4318 | Tempo, Loki, OTel collector (observability profile) |
| 5439 / 6389 | throwaway Postgres / Redis for integration tests (`make test-infra`) |

## Make targets

```
make help             list the targets (default goal)
make setup            uv sync --extra dev + npm install in apps/web
make dev              docker compose up --build            (postgres, redis, temporal, temporal-ui,
                                                            simulator, migrate, api, worker, detector, web)
make observability    adds prometheus, grafana, tempo, loki, promtail, otel-collector
make down / reset     docker compose --profile observability down [-v]: stop (keep volumes) /
                      stop and delete volumes — both also tear down the observability services
make lint             ruff check + ruff format --check + lint-imports
make typecheck        uv run mypy src/aegis (strict comes from `strict = true` in pyproject.toml)
make format           ruff format + ruff check --fix
make test             pytest tests/unit tests/security      (no infrastructure)
make test-infra       start aegis-test-postgres (:5439) and aegis-test-redis (:6389) via docker run
make test-integration pytest tests/integration              (needs test-infra)
make test-workflow    pytest tests/workflow                 (Temporal time-skipping test server)
make test-all         lint typecheck test test-integration test-workflow
make migrate          uv run aegis migrate
make e2e              uv run python scripts/e2e.py         (needs the compose stack)
make chaos            pytest tests/chaos                    (needs the compose stack; kills and
                                                            restarts compose services)
make evals ARGS=...   uv run python -m evals.run $(ARGS)
make simulation SCENARIO=redis-connection-leak
make openapi          regenerate docs/api/openapi.json
make web-dev          npm run dev in apps/web
```

## Running the stack

```bash
mkdir -p .secrets && printf '%s' "$OPENAI_API_KEY" > .secrets/openai_api_key
make dev                                    # first start builds the image and applies migrations
curl -s localhost:8600/ready | jq            # database/redis/temporal/simulator checks
uv run aegis scenarios                       # list the eight scenarios
uv run aegis inject redis-connection-leak    # or: make simulation SCENARIO=...
uv run aegis incidents --active
```

Without a key: `AEGIS_LLM_PROVIDER=disabled make dev` runs the deterministic planner for every
phase. The `migrate` service runs `aegis migrate` once; api, worker and detector wait for it.

## Tests

`tests/conftest.py` sets `AEGIS_LLM_PROVIDER=scripted`, `AEGIS_TEMPORAL_ENABLED=false` and
`AEGIS_OTEL_ENABLED=false` with `os.environ.setdefault` — a value already present in the environment
wins — and resets the settings cache per test. Markers (`pyproject.toml`): `integration`,
`workflow`, `e2e`, `chaos`, `llm`; `e2e` is declared but unused (`tests/e2e/` holds no tests).
Default timeout 120 s per test.

- **Unit and security** (`make test`, ~150 tests): in-process simulator, in-memory repositories,
  `ScriptedProvider`. `tests/helpers.py::build_runtime` wires registry, flows, policy, authorizer and
  executor. `tests/security/test_tool_abuse.py` is the adversarial corpus.
- **Integration** (`make test-infra && make test-integration`, 11 tests): Postgres repositories
  (numbering, events, optimistic locking, exactly-once ledger, audit trigger, vector and lexical
  search) and Redis adapters. The Postgres fixture calls `upgrade_head_async`
  (`src/aegis/infrastructure/postgres/migrations.py`), which runs `command.upgrade(cfg, "head")` in
  a worker thread, against `AEGIS_TEST_DATABASE_URL` (default
  `postgresql+asyncpg://aegis:aegis@localhost:5439/aegis_test`); the Redis fixture pings
  `AEGIS_TEST_REDIS_URL` (default `redis://localhost:6389/0`). Both fixtures skip when their
  container is absent, but `test_bootstrap_wires_real_adapters` asserts the readiness checks
  directly, so it fails rather than skips when Redis is down.
- **Workflow** (`make test-workflow`, 4 tests): `temporalio.testing.WorkflowEnvironment.start_time_skipping`
  downloads the Temporal test server on first use (needs network once). Runs the real
  `IncidentWorkflow` and worker against the in-process simulator: full lifecycle with approval,
  rejection → replan → escalation, transient spike without action, cancel.
- **Chaos** (`make chaos`, 4 tests): against the running compose stack, driving
  `docker compose kill/up/restart/stop` on the service names `worker`, `api`, `redis` and
  `simulator` (not `docker kill` on container names): kills the worker with SIGKILL mid-incident and
  asserts exactly one remediation, restarts the API, stops Redis, restarts the simulator. Skips when
  `/ready` is not 200. The API base URL comes from `AEGIS_E2E_API`.
- **E2E** (`make e2e`): `scripts/e2e.py [--scenario X ...] [--timeout 900] [--api URL] [--key KEY]`
  injects each demo scenario through the API, approves pending approvals, and asserts the ground
  truth from `src/aegis/simulator/faults.py`. It closes escalated or failed incidents between
  scenarios. The script reads no environment variables: the API key comes only from `--key`.
  After a scenario reaches a terminal status it waits (up to 90 s) for the incident's memory row,
  because `finalize` writes that after the status change — reading immediately reports "no memory"
  for a run that does store one.

## Evals

```bash
uv run python -m evals.run                         # offline suites: detection, authorization,
                                                   # hypotheses, verification, memory, agent (deterministic)
uv run python -m evals.run --llm                   # adds agent-llm and llm-structured-output (real calls)
uv run python -m evals.run --suite agent --llm --model gpt-5-nano --scenario bad-deployment --seeds 2
```

Reports are written to `evals/results/<UTC stamp>-<offline|llm[-model]>.{json,md}`; the exit code is
1 if any suite is below its threshold. See `docs/evals.md`.

`--suite` takes the keys of `OFFLINE` in `evals/run.py` (`detection`, `authorization`,
`hypotheses`, `verification`, `memory`, `agent`, plus `llm` with `--llm`) but declares no `choices`,
and `all([])` is True, so a typo such as `--suite bogus` silently runs nothing and exits 0. The
keys also differ from the suite names in the report: `hypotheses` → `hypothesis-ranking`, `memory`
→ `memory-recall`, `agent` → `agent-deterministic` (and `agent-llm` with `--llm`).

## Lint, types, imports

`make lint` runs ruff (rule set in `pyproject.toml`, line length 100), ruff format check, and
`lint-imports` for the five layering contracts (see `docs/adr/ADR-008-single-distribution-import-linter.md`).
`make typecheck` runs mypy in strict mode with the pydantic plugin over `src/aegis`.

## CLI

`uv run aegis migrate | check | scenarios | inject <scenario> [--params '{"service": "..."}'] |
incidents [--active] [--limit N]`. `check` builds a runtime and prints the readiness checks plus LLM
health, exit code 1 on failure.
