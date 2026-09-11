# Aegis console

The operations console for the Aegis autonomous incident-response runtime — a **flight recorder**
for incidents, not a chat interface.

Every screen exists to make one sequence visible and auditable:

```
evidence  →  hypothesis  →  decision  →  action  →  verification
```

The model proposes, the runtime decides, the policy engine authorizes, the tool executes, the
verification engine proves. The console shows all five, including the fourteen-check authorization
gate on every single tool request and the human approval that a mutation cannot skip.

## Running it

```bash
npm install
npm run dev            # http://localhost:3600, expects the API on :8600
```

```bash
npm run lint           # eslint (flat config, next/core-web-vitals + typescript)
npm run typecheck      # tsc --noEmit, strict
npm run build          # standalone production build
npm run start:standalone   # serve the build (see the note below)
npm run generate:api   # regenerate src/lib/api-types.ts from ./openapi.json
```

> **Serving the build.** `next.config.ts` sets `output: "standalone"`, and `next start` refuses to
> work with it — use `npm run start:standalone`, which runs `node .next/standalone/server.js` the way
> the Dockerfile does. The standalone bundle expects `.next/static` and `public` beside the server, so
> outside Docker copy them first:
> `cp -r .next/static .next/standalone/.next/static && cp -r public .next/standalone/public`.
> `PORT` and `HOSTNAME` select the listen address (the image sets `3000` and `0.0.0.0`).

The whole compose stack (API, worker, simulator, Postgres, Redis, Temporal, console) comes up with
`docker compose up --build` from the repository root; the console is published on **3600** there.

### Configuration

All configuration is `NEXT_PUBLIC_*` and therefore **inlined at build time** — changing it requires
rebuilding the image. See [`.env.example`](.env.example).

| Variable | Default | Purpose |
|---|---|---|
| `NEXT_PUBLIC_AEGIS_API_URL` | `http://localhost:8600` | control-plane base URL |
| `NEXT_PUBLIC_AEGIS_API_KEY` | *(empty)* | sent as `X-Aegis-Key`, and as `?key=` on the two SSE routes |
| `NEXT_PUBLIC_TEMPORAL_UI_URL` | `http://localhost:8233` | workflow deep links |
| `NEXT_PUBLIC_TEMPORAL_NAMESPACE` | `default` | workflow deep links |
| `NEXT_PUBLIC_GRAFANA_URL` | `http://localhost:3000` | link on `/system` |
| `NEXT_PUBLIC_PROMETHEUS_URL` | `http://localhost:9090` | link on `/system` |

With `AEGIS_API_AUTH_MODE=disabled` (the dev default) no key is needed and every caller is the local
operator with the admin role.

### Docker

```bash
docker build -t aegis-console \
  --build-arg NEXT_PUBLIC_AEGIS_API_URL=http://api:8600 .
docker run -p 3000:3000 aegis-console
```

Multi-stage (`deps` → `build` → `runner` on `node:20-alpine`), runs as a non-root user, serves the
`output: "standalone"` bundle with `node server.js`.

## Routes

| Route | What it answers |
|---|---|
| `/` | What is happening right now: active incidents by severity, the approval rail, a live global event ticker, readiness, stats, topology mini-map |
| `/incidents` | The register, filtered by status and severity (repeated query params) with pagination |
| `/incidents/[id]` | **The centerpiece.** Three columns: live ledger, root-cause/evidence/remediation/verification, agent + metrics + audit |
| `/approvals` | The human decision queue and its history, with a full-context drawer |
| `/topology` | The simulated service map, tiered left to right, live health overlay, active faults |
| `/agent-runs` → `/agent-runs/[id]` | Every reasoning-loop execution and its steps, budgets and token usage |
| `/flows` | Each flow pack as a phase pipeline: allowed tools, exit conditions, transitions, budgets |
| `/tools` | The registry by category, with argument schemas; dangerous tools marked never executable |
| `/policies` | Rules in priority order, the three code-level invariants, the default-deny banner |
| `/memory` | Extracted incident memories and pgvector similarity search |
| `/simulation` | Scenario injection, fault clearing, clock control, current simulated state |
| `/system` | Readiness checks, system info, principal, links to Temporal / Grafana / Prometheus / API docs |

## The incident page

The layout mirrors the runtime's own order of operations.

**Left — the ledger.** The incident timeline, grouped into phase bands delimited by
`flow.phase_entered` / `flow.phase_exited`, each row `HH:MM:SS` with an actor-kind glyph and an
expandable payload. It loads the replay from `GET /incidents/{id}/timeline`, then follows
`GET /incidents/{id}/stream?after_seq=N` over `EventSource`. On error the hook closes the source and
re-opens it with `after_seq` set to the last seq it saw (exponential backoff, capped at 15s), so no
event is lost or duplicated; the header shows connected / reconnecting and the current seq.

**Centre — the reasoning.** The leading hypothesis with its statement, category, root-cause service
and a confidence meter broken down into the deterministic score components (evidence strength,
temporal and dependency alignment, historical similarity, contradiction penalty, validation bonus)
plus the engine's own `explanation[]` lines — because confidence is *computed by the runtime*, not
asserted by the model. Then the evidence graph (`d3-force` layout, evidence coloured by kind,
hypothesis / service / action nodes, edges from `evidence.relations`), the remediation plans with
their policy decision and verification contract, and the before/after verification results.

When a plan is `awaiting_approval` the approval console takes over that block: the exact tool call
with its arguments, why, the expected effect, the rollback (or its absence), the risk, the
exactly-once key, and two-step Approve / Reject with a reason recorded in the audit trail. That
moment is deliberately given weight — it is where a human takes responsibility.

**Right — the agent, under observation.** One run per phase with status, termination reason and
usage against budget; expanding a run loads its steps and renders each by kind: a *proposal* shows
the observation, rationale, model and tokens and the tool call it **requested**; an *authorization*
step renders the 14 gate lights; *tool_execution* shows what actually ran; *hypothesis_update* shows
the runtime's re-scoring. Beside it, live metric charts per affected component (15-minute window,
polled every 10s, with the healthy baseline band from
`/simulation/components/{c}/baseline/{metric}`) and the append-only audit trail.

## Design

A control room, not a dashboard theme: graphite canvas `#0b0d10`, panels `#12151a`, hairline rules
`#23282f`, and exactly **one** accent — phosphor amber `#f5b31a` for identity, live state and
primary actions. Colour otherwise carries only meaning: SEV1 `#ff4d4f`, SEV2 `#ff8a3d`, SEV3
`#e6c229`, SEV4 `#4c9be8`, success `#3ddc97`.

JetBrains Mono carries all data, ids, timestamps and numbers with `tabular-nums`; IBM Plex Sans
carries prose and the 10–11px uppercase micro-labels. Sections are separated by ruled hairlines
rather than floating cards; corners are 2–4px and only status dots are round. No gradients, no
shadows, no emoji, dark only. Motion is purposeful: an amber pulse on live elements, new ledger rows
sliding in, durations ticking every second.

Charts, the evidence graph, the service map, the gate lights and the phase pipeline are all
hand-rolled SVG — no chart library, no component kit.

## Layout

```
openapi.json               copy of docs/api/openapi.json; source for generate:api
src/app/                   App Router pages (one per route above)
src/components/
  shell/                   left rail, top strip, global SSE provider, inline SVG icons
  ui/                      primitives: SevBadge StatusChip LiveDot Meter Sparkline MetricChart
                           GateSequence Ledger KeyValue Drawer Tabs EmptyState RelativeTime
                           Duration JsonView Problem Section Button Pagination Loading
  incident/                ledger, root cause, evidence graph, remediation, metrics, audit, actions
  agent/                   agent step renderers and budget bars
  approvals/ flows/ tools/ topology/ overview/
src/lib/
  api.ts                   typed fetch client, problem+json → ApiError, repeated array params
  api-types.ts             generated by openapi-typescript (not hand-edited)
  types.ts                 domain models for the routes OpenAPI types as dict
  hooks.ts                 one React Query hook per resource, namespaced query keys
  sse.ts                   EventSource hook with explicit after_seq reconnection
  colors.ts format.ts events.ts config.ts query.tsx use-now.ts use-measure.ts
```

`src/lib/types.ts` is the contract: endpoints that FastAPI types as `dict` have no OpenAPI schema, so
their shapes come from [`docs/api/frontend-contract.md`](../../docs/api/frontend-contract.md) and are
written out by hand there. Where the spec *does* carry a schema the generated type is aliased instead.

## Error handling

`application/problem+json` is surfaced, never swallowed: `ProblemBanner` shows the problem `type`,
status, `title`, `detail`, per-field `errors[{loc,msg}]` for 422, `retry-after` for 429, and the
`request_id` so a console error can be traced to a server log line. A 409 `invalid_transition` from a
human action renders in place next to the button that caused it. 4xx responses are never retried;
network and 5xx errors are retried twice.
