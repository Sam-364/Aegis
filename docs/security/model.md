# Security model

This document states what Aegis defends, who may do what, and where each control is implemented.
The companion document `docs/security/threat-model.md` walks specific attacks through these
controls. Everything here describes code under `src/aegis/`; residual gaps are called out as such
rather than omitted.

## Trust boundaries

```
 ┌── untrusted ─────────────────────────────────────────────────────────────────────┐
 │ LLM output        proposals, hypotheses, arguments, remediation plans            │
 │ Telemetry text    log lines, trace attributes, deployment change summaries       │
 │ API clients       anything holding an API key, including the console             │
 └──────────────────────────────────────────────────────────────────────────────────┘
                                     │ parsed, validated, bounded
                                     ▼
 ┌── trusted (loaded and validated at process startup, versioned, checksummed) ─────┐
 │ Tool registry     src/aegis/tools/builtin/*        (code, not configuration)     │
 │ Flow packs        flows/*.yaml                     (validated against registry)  │
 │ Policy rules      policies/*.yaml                  (invariants stay in code)     │
 │ Runtime code      authorizer, policy engine, state machine, verification         │
 └──────────────────────────────────────────────────────────────────────────────────┘
```

The model is treated as a hostile-capable input source at all times: it can *name* a tool and
*suggest* arguments, and nothing else. It cannot register a tool, widen a flow, edit a policy,
change an incident's status, approve an action, or execute a mutation.

## Actors and roles

`Actor` (`src/aegis/domain/base.py`) is a frozen value object carried into every policy decision and
every audit event. Two orthogonal fields matter:

`ActorKind` (`src/aegis/domain/enums.py`) — *what* is acting, with the default roles the factories
assign:

| Kind | Factory | Default roles | Used by |
|---|---|---|---|
| `system` | `Actor.system(component)` | `admin` | bootstrap, migrations, background maintenance |
| `detector` | `Actor.detector()` | `operator` | detection engine opening incidents |
| `agent` | `Actor.agent(run_id)` | `operator` | the LLM loop; id is `agent:<run_id>` |
| `workflow` | `Actor.workflow(workflow_id)` | `operator` | Temporal activities; id is `workflow:<id>` |
| `human` | `Actor.human(subject, roles)` | as supplied | API principals; id is `user:<subject>` |
| `api` | — | — | reserved; no factory today |

`Role` — *how much* it may do. Ordered, and `has_role` is satisfied by any higher role
(`Role.includes`): `viewer (0) < operator (1) < admin (2)`.

`ActorKind` is not implied by `Role` and never substitutes for it: an agent holds the `operator`
role (so it may run diagnostics) yet is denied every mutation by a kind-based invariant, and a human
`admin` cannot execute a `dangerous` tool because that denial is category-based, not role-based.

### What each role may do

| Capability | viewer | operator | admin |
|---|---|---|---|
| Read incidents, evidence, hypotheses, ledger, audit, agent runs, timeline, SSE streams | yes | yes | yes |
| Run `read_only` tools (`REQUIRED_ROLE` in `src/aegis/tools/authorizer.py`) | yes | yes | yes |
| Run `diagnostic` tools | no | yes | yes |
| Approve / reject a remediation (`POST /approvals/{id}/approve`) | no | yes | yes |
| Acknowledge, resolve, reopen, force-close an incident | no | yes | yes |
| Inject or clear a simulator fault, advance/reset the simulation | no | yes | yes |
| Have a `mutating` tool executed on their behalf | no | yes | yes |
| `dangerous` tools | no | no | **no** — denied by invariant before the role check matters |

Endpoint gating lives in the FastAPI dependencies (`src/aegis/api/deps.py`): `CurrentActor` means
"authenticated, any role", `Operator` and `Admin` are `require_role(...)` wrappers. Every endpoint
that can change an incident, decide an approval or drive the simulator takes `Operator`; the one
write that does not is `POST /api/v1/notifications/{id}/read`, which any authenticated principal may
call. No endpoint requires `Admin` today — the `Admin` dependency is defined but unused, so an admin
key differs from an operator key only in what the tool authorizer's `REQUIRED_ROLE` table would
permit.

## API authentication

Two modes, selected by `AEGIS_API_AUTH_MODE` (`src/aegis/config.py`):

- **`disabled`** (development only): every request resolves to
  `LOCAL_ACTOR = Actor.human("local-operator", {Role.ADMIN})` — which means anybody who can reach
  the port can approve a remediation. `Settings` refuses to start with auth disabled in
  **`production` or `staging`** (proved by
  `tests/unit/test_config_logging.py::test_production_requires_auth` and
  `tests/security/test_hardening.py::test_authentication_cannot_be_disabled_outside_development`),
  and `build_runtime` logs `runtime.api_authentication_disabled` at WARNING on every start in that
  mode so it cannot be overlooked in a development deployment that became a shared one.
- **`api_key`**: static keys from `AEGIS_API_KEYS`, formatted `key:role[:name]` and comma-separated,
  parsed into `ApiKeyPrincipal` objects whose `__repr__` never renders the key. A matching key
  produces `Actor.human(name, {role})`.

Key transport, in order of preference (`current_actor`):

1. `X-Aegis-Key: <key>`
2. `Authorization: Bearer <key>`
3. `?key=<key>` — **only** on paths ending in `/stream`, because browser `EventSource` cannot set
   headers. Any other route ignores the query parameter.

Missing key → `401 unauthorized`; unknown key → `401`; insufficient role → `403 forbidden`, both as
`application/problem+json` (`src/aegis/api/errors.py`). Keys are compared with
`hmac.compare_digest`, so a wrong key takes the same time to reject as a nearly-right one. There is
still no hashing at rest, no rotation mechanism and no per-key rate accounting beyond the limiter
below. For anything beyond a small trusted operator set, put an authenticating proxy in
front and keep `api_key` mode as defence in depth.

Other request-level controls:

- `RateLimitMiddleware` (`src/aegis/api/middleware.py`): `AEGIS_API_RATE_LIMIT_PER_MINUTE`
  (default 600) per **client host**, because a bucket keyed on a caller-supplied header is a bucket
  the caller can change. `/metrics`, `/health`, `/ready` and the two SSE routes are exempt, and the
  exemption is matched against the **resolved route** (`STREAMING_ROUTES`), not a substring of the
  path, so `/api/v1/incidents?q=stream` is still limited. Over the limit → `429` with
  `retry-after: 60`. The in-memory fallback limiter bounds its own key table (`max_keys=4096`,
  expired windows evicted first) so an attacker cycling source addresses cannot grow it without
  bound; with Redis configured the window is shared across API replicas, and if Redis stops
  answering the middleware falls back to that local counter rather than failing the request — a
  rate limit is a protection, not a dependency that may take reads down with it
  (`tests/unit/test_api.py::test_rate_limiting_degrades_to_local_counting_when_its_backend_is_down`,
  and `tests/chaos/test_dependency_outage.py::test_redis_outage_does_not_break_api_reads` proves it
  on a live stack).
- `RequestContextMiddleware`: assigns/propagates `x-request-id` and binds it to the log context.
- CORS is restricted to `AEGIS_API_CORS_ORIGINS` (default `http://localhost:3600`) with
  `allow_credentials=True`, so this list must never contain `*` in a deployment that relies on
  cookies.

## The three invariants

These are properties of the code, not of configuration. `policies/default.yaml` cannot relax them,
and a policy file that tries is simply never consulted for those cases.

| Invariant | Where | Effect |
|---|---|---|
| `dangerous_tools_denied` | `PolicyEngine._invariants`, plus check 6 in the authorizer | A `DANGEROUS` tool is denied for every actor, environment and severity. `delete_data`, `drop_database` and `terminate_instance` exist only so this is testable |
| `no_mutation_inside_agent_loop` | same two places | `MUTATING`/`DANGEROUS` tools are refused whenever `in_agent_loop=True`. The agent's only path to a mutation is a validated `ActionPlan` handed to the workflow |
| `agent_cannot_mutate` | `PolicyEngine._invariants` | An actor of kind `agent` is denied a `MUTATING` tool *even on the workflow path* — a bug that passed `in_agent_loop=False` with the agent's actor still fails |

Two more structural rules complete the picture:

- **The agent cannot change incident status.** `assert_transition`
  (`src/aegis/domain/statemachine.py`) validates the transition against the closed `TRANSITIONS`
  table, then restricts `ActorKind.HUMAN` to the `HUMAN_TRANSITIONS` set, then raises
  `InvalidTransitionError` for *any* `ActorKind.AGENT` request — even one that would otherwise be
  legal. Everything outside `HUMAN_TRANSITIONS` is runtime-only.
- **The agent cannot approve.** `ApprovalService.decide`
  (`src/aegis/application/approvals.py`) rejects `ActorKind.AGENT` with `ForbiddenError("the agent
  may never approve its own actions")` and then requires `Role.OPERATOR`.

Invariants are enforced twice on purpose: the authorizer's category check (step 6) runs *before*
policy evaluation, so a misconfigured or empty policy file still cannot produce an in-loop mutation,
and the policy engine repeats the judgement for callers that consult it directly.

## Tool argument guards

Arguments arrive as an untrusted JSON object (the model emits `arguments_json`, parsed and re-checked
by the runtime). `ToolDefinition.parse_args` (`src/aegis/tools/definition.py`) applies four layers
before a handler ever sees them; a failure at any layer becomes denial code
`invalid_tool_arguments` at check 12 of the pipeline.

1. **Injection-shaped string rejection** — `guard_strings` walks strings, dicts, lists and tuples
   recursively and rejects anything matching

   ```
   (\.\./|[;&|`$]|\$\(|<script|://|\\x[0-9a-f]{2})
   ```

   That covers path traversal, shell metacharacters and command substitution, inline script tags,
   any URL scheme (which is what stops `http://169.254.169.254/…` SSRF-shaped arguments), and hex
   escapes. No Aegis tool shells out or builds a URL from arguments — this is defence in depth so a
   hostile proposal cannot smuggle a payload into logs, evidence or a downstream system.
2. **Bounded sizes** — `MAX_STRING_ARG = 512` is enforced both by `guard_strings` and by
   `ToolArgs.model_config` (`str_max_length=512`, `str_strip_whitespace=True`). Numeric arguments are
   bounded per field: `window_seconds` 30–1800, `limit` 1–200 (traces 1–50, memory 1–10),
   `replicas` 1–10, `reason` ≤ 300 characters, `to_version` must match
   `^[A-Za-z0-9._\-]{1,40}$`.
3. **`extra="forbid"`** — an unknown field is an error, not ignored. `{"__proto__": {}}` or a
   misspelled argument fails closed rather than silently doing something else.
4. **Unknown component rejection** — when the caller supplies `known_components` (the agent runtime
   and the workflow both pass `frozenset(n.name for n in topology.nodes)`), the fields `service`,
   `component`, `source` and `target` must name a component that actually exists in the topology.
   The denial lists the known names, which is also what lets the agent correct itself.

The same `parse_args` call runs twice for a real execution: once inside the authorizer (so a denial
is recorded with its check trace and no handler runs) and once in `ToolExecutor.execute` after the
idempotency key is claimed.

`DomainModel`/`ValueObject` in `src/aegis/domain/base.py` also set `extra="forbid"`, so contract
drift in anything persisted or transported fails at the boundary rather than later.

## Secret handling

- `Settings.llm_api_key` is a `SecretStr`; `repr`/`str` of settings never renders it.
- The key is normally *not* an environment variable: `AEGIS_LLM_API_KEY_FILE` points at a file
  (locally `.secrets/openai_api_key`, gitignored) which the validator reads at startup. An empty
  `AEGIS_LLM_API_KEY` is treated as "not provided". `AEGIS_LLM_PROVIDER=openai` without either
  fails startup.
- `ApiKeyPrincipal.__repr__` prints only name and role.
- `redact_processor` is installed in every structlog pipeline (`src/aegis/logging.py`). It redacts
  by **key name** (`api_key`, `authorization`, `password`, `secret`, `token`, `credential`, `cookie`,
  `set-cookie`, case-insensitive, matched anywhere in the key) and by **value shape**
  (`sk-…` keys, `Bearer …` headers) recursively through mappings and sequences. The value pattern
  requires at least 8 characters after the prefix, so a truncated stub like `sk-abc` is left alone.
  Proved by `tests/security/test_tool_abuse.py::test_secrets_never_reach_logs` and
  `tests/unit/test_config_logging.py::test_redaction` — note both call `redact_value` directly
  rather than asserting on an emitted log line, so the processor's installation is covered only by
  `test_configure_logging_does_not_raise`.
- Secrets carried as a **pair inside a string** are redacted too (`_SECRET_PAIRS`):
  `?key=…`, `password=…`, `token=…`, `secret=…` and friends collapse to `[REDACTED]` wherever they
  appear in a logged value, while innocent pairs like `service=order-service` are untouched
  (`tests/security/test_hardening.py::test_a_key_in_a_query_string_is_redacted`). The same
  `redact_url` runs as an OpenTelemetry `server_request_hook`, so the API key that `EventSource`
  has to put in the query string of `/stream` is not exported to the trace backend in `http.url`.
- A DSN password (`postgresql://user:pw@host/db`) is rewritten to
  `postgresql://user:[REDACTED]@host/db` by `_DSN_CREDENTIALS`, so a logged connection string does
  not leak its password. Still prefer `AEGIS_DATABASE_ECHO=false` (the default) and treat
  `database_url` as sensitive in whatever ships logs — redaction is a backstop, not a licence.
- Residual gap, stated plainly: redaction is shape-based. A secret with no recognisable key name,
  no `sk-`/`Bearer` prefix and no `name=value` framing — a bare token logged as a positional
  string — passes through.

## Audit immutability

Audit events are append-only at three levels:

1. `AuditEvent` is a frozen `ValueObject` — no in-place mutation in Python.
2. `AuditRepository` exposes `append` and read methods only; there is no update or delete
   (`src/aegis/ports/repositories.py`).
3. The database refuses it anyway. Migration `migrations/versions/0001_initial.py` installs

   ```sql
   CREATE TRIGGER audit_events_immutable
   BEFORE UPDATE OR DELETE ON audit_events
   FOR EACH ROW EXECUTE FUNCTION aegis_audit_immutable();   -- RAISE EXCEPTION 'audit_events is append-only'
   ```

   A row-level trigger never fires for `TRUNCATE`, so migration `0002_audit_truncate_guard.py` adds
   the statement-level trigger that closes that hole:

   ```sql
   CREATE TRIGGER audit_events_no_truncate
   BEFORE TRUNCATE ON audit_events
   FOR EACH STATEMENT EXECUTE FUNCTION aegis_audit_immutable();
   ```

   `tests/integration/test_postgres_repositories.py::test_audit_events_are_immutable_at_the_database`
   asserts `UPDATE`, `DELETE` and `TRUNCATE` all fail.

Every authorization decision is audited with its full ordered check trace: `ToolExecutor._audit`
writes `tool.denied` / `tool.executed` / `tool.failed` with `data.checks` containing each
`AuthorizationCheck` name, pass flag and detail, plus the arguments as submitted. Approvals write
`approval.decided`. Timeline events (`incident_events`) are separately sequenced per incident with a
unique `(incident_id, seq)` constraint.

## Model-authored text and model-authored claims

The model is an untrusted author in two distinct ways, and each needs its own control.

- **What it writes is shown to a human who is about to approve an infrastructure change.**
  Every structured answer enters the runtime through `StructuredResult`
  (`src/aegis/ports/llm.py`), whose validator walks the parsed model and passes every string
  through `sanitize_display_text` (`src/aegis/domain/text.py`): C0/C1 control characters, bidi
  embedding/override/isolate marks, zero-width characters and the BOM are removed. Without it a
  prompt-injected model could write `restart order-service` followed by a right-to-left override
  that renders as something else entirely in the approval drawer or a terminal, and the operator
  would approve text they never actually read. Sanitising at the single ingress point means the
  stored record, the console, the CLI and the audit export all agree.
- **What it claims about its own hypothesis is not evidence.** `update_hypothesis` is a
  bookkeeping tool: it may attach evidence handles and abandon a hypothesis, but a `confirmed`
  test outcome coming from it is downgraded to `inconclusive` unless the attached evidence was
  produced by a real tool execution (`tool_execution_id is not None`), implicates the suspected
  root-cause service, scores ≥ 0.75 and is not tagged `normal`/`ok`/`not_reproduced`/`stable`
  (`AgentRuntime._diagnostic_backing`, the same filter the LLM judge is held to). The model is told
  why in its feedback channel. Confidence itself is always computed by
  `HypothesisEngine.rescore` from the evidence graph — the model never writes a number that the
  remediation gate then reads.

## Tenancy

- `Incident.tenant_id` (`TenantId`, default `"default"`) is set by `IncidentIntakeService` from
  `AEGIS_TENANT_ID`, projected to the `incidents.tenant_id` column and indexed together with status
  (`ix_incidents_tenant_status`).
- `PolicyContext.tenant_id` is populated from the incident, so a policy rule can be scoped per
  tenant.
- **Honest limitation:** the repository queries do not filter by tenant today — `list_incidents` and
  friends select across all rows, and API principals carry no tenant claim. The docstring in
  `src/aegis/ports/repositories.py` ("tenant-scoped by construction") describes the intent, not the
  current implementation. Treat a deployment as single-tenant: one database and one API surface per
  tenant. Multi-tenancy needs (a) a tenant on the principal, (b) a tenant predicate in every
  repository method, and (c) a tenant label on the metrics.

## What leaves the process

- **To the LLM provider**: the system prompt, plus a per-step digest — incident metadata, detection
  signals with values and sigma, the topology, the phase contract and allowed tool list, evidence
  *summaries* (each truncated to 320 characters, at most 40 items), hypothesis statements and score
  explanations, current-vs-baseline metrics, similar past incidents, and runtime feedback on refused
  steps. Raw tool payloads (`evidence.data`, including sampled log lines) are stored and shown in
  the console but are **not** sent verbatim to the model; the log evidence *summary* does contain
  digit-normalised log patterns, which is the prompt-injection channel discussed in the threat
  model. No credentials, no environment variables and no database contents beyond the incident
  record are included. `AEGIS_LLM_PROVIDER=disabled` or `scripted` removes this egress entirely and
  the deterministic planner takes over.
- **To the infrastructure adapter**: only the validated arguments of an authorized tool, over the
  simulator HTTP API or a future real adapter.
- **To Prometheus/OTel**: metric names, label values (tool name, denial code, status, phase) and span
  attributes (incident id, tool, category). No arguments, no evidence text. Request URLs on server
  spans pass through `redact_url` first.

## Supply chain and runtime hardening

- Dependencies are pinned by `uv.lock`; `uv sync --extra dev` is reproducible.
- The image (`Dockerfile`) runs as uid/gid 1000 (`USER aegis`) with the code owned by that user.
  The Kubernetes manifests add `runAsNonRoot`, `seccompProfile: RuntimeDefault`,
  `allowPrivilegeEscalation: false`, `readOnlyRootFilesystem: true` and `capabilities: drop [ALL]`
  (`deploy/kubernetes/base/api.yaml` and its siblings).
- `deploy/kubernetes/base/networkpolicy.yaml` is **ingress-only**: a namespace-wide
  `default-deny-ingress` plus per-component allow rules (ingress controller and the console to the
  API on 8600, Prometheus's namespace for `/metrics`). Egress is deliberately left open because the
  LLM endpoint and managed Postgres/Redis/Temporal live outside the namespace — if you need egress
  restrictions, add them per cluster. See `docs/operations/deployment.md`.
- `mypy --strict` and `ruff` run in CI; the import-linter contracts (`pyproject.toml`) are a security
  control as much as an architectural one — they are what guarantees the simulator cannot import the
  runtime (no ground-truth leakage into agent decisions) and that the runtime core cannot reach for
  an infrastructure adapter that bypasses the ports.
