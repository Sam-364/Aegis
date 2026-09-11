# ADR-004: One ordered authorization pipeline; mutating tools only through the workflow

- Status: Accepted, implemented (2026-09-10)
- Code: `src/aegis/tools/authorizer.py`, `executor.py`, `definition.py`, `registry.py`,
  `src/aegis/policy/engine.py`, `src/aegis/workflows/activities.py::execute_remediation`

## Context

The model's output is untrusted input. A tool call it proposes may name a tool that does not exist,
one that is off-limits in this phase, arguments shaped like an injection, a component that is not in
the topology, or a mutation it is trying to sneak past the workflow. The same request shape is also
used by the workflow when it executes an approved plan, and a retried activity may present the same
mutation twice.

## Decision

- Every tool request, from the loop or from the workflow, goes through `ToolAuthorizer.authorize`,
  a fixed sequence of 14 checks (registered, enabled, incident active, flow allows, phase allows,
  category permitted, environment, severity, actor role, risk ceiling, policy effect, arguments
  valid, idempotency, budget). The pipeline never raises; it returns an `AuthorizationDecision` with
  the ordered trace up to and including the failing check, a `denial_code` and a reason. The full
  list with what each check proves is in `docs/architecture/overview.md`.
- Categories are structural: `read_only` and `diagnostic` tools may run in the loop; `mutating`
  tools are refused in the loop by check 6 *and* by the policy invariant
  `no_mutation_inside_agent_loop`; `dangerous` tools are registered (so policy can be tested against
  them) but refused by check 6 and the invariant `dangerous_tools_denied`. An `agent` actor can never
  execute a mutation (`agent_cannot_mutate`), even with `in_agent_loop=False`. Check 9 enforces the
  minimum actor role per category (`REQUIRED_ROLE`: read_only → viewer, diagnostic → operator,
  mutating → operator, dangerous → admin).
- The only path to a mutation is: the agent stores an `ActionPlan` → the workflow evaluates policy →
  approval if required → `execute_remediation` calls `ToolExecutor.execute(in_agent_loop=False,
  approval=...)` with `requested_by=Actor.workflow(...)`. Check 11 requires the approval object to be
  `approved` and bound to the same `action_plan_id`.
- Arguments are validated by the tool's own Pydantic model (`extra="forbid"`, string max 512,
  per-field bounds) after `guard_strings` rejects injection-shaped strings
  (`../`, `; & | \` $`, `$(`, `<script`, `://`, `\xNN`), and any `service`/`component`/`source`/`target`
  must be a known topology component — that last check is conditional on a non-empty
  `known_components`, so an empty frozenset silently disables it.
- Exactly-once is enforced by the ledger, not by hope: mutations are keyed per action plan, the
  record is inserted with status `running` before the handler runs, the key column is unique, and a
  prior record under the same key whose status is exactly `SUCCEEDED` short-circuits to
  `skipped_duplicate` with that result — a prior `FAILED` or `TIMED_OUT` record re-executes. Read and
  diagnostic calls are keyed per request, so exactly-once is a mutation-only property; denied and
  duplicate records are re-keyed `denied:<request id>` / `dup:<request id>` so they never hold the
  real key. Reads are retried up to `spec.retry.max_attempts` times, mutations are attempted once.
- Denials are first-class data: they are persisted as `ToolExecutionRecord` rows with status
  `denied`, audited as `tool.denied` with the check trace, counted in
  `aegis_policy_denials_total{code,tool}`, and fed back to the model as text.

## Consequences

- The security property "the model cannot cause a mutation" does not depend on prompt quality; it is
  enforced in three independent places (check 6, the invariant, the workflow being the only caller).
- Adding a tool means adding a `@tool` definition and listing it in a flow pack; the pipeline covers
  it with no further code (`docs/development/tool-authoring.md`).
- The check order is part of the contract: the console renders it as a gate sequence, and tests
  assert which check fails (`tests/unit/test_authorizer.py`, `tests/security/test_tool_abuse.py`,
  `evals/suites/authorization.py` with a required `false_allows = 0`).
- The trace is truncated at the first failure, so a denied request does not reveal whether later
  checks (for example policy) would have passed.

## Alternatives considered

- Relying on prompt instructions and the flow's tool list: the model would still be able to
  request anything, and nothing would stop a mutation reaching the gateway.
- Per-tool authorization code: 25 tools × 14 concerns, inconsistent and untestable as a corpus.
- Policy-only (rules decide everything): rules can be edited; the invariants and category gates
  cannot, which is the point.
