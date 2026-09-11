# Adding a tool

Tools are the only way the runtime touches the outside world. A tool is a `ToolDefinition`
(`src/aegis/tools/definition.py`): a `ToolSpec` (metadata the registry, authorizer and console see),
a Pydantic argument model, and an async handler. Built-ins live in `src/aegis/tools/builtin/`
(`read.py`, `diagnostic.py`, `mutating.py`) and are assembled by `build_default_registry()` in
`src/aegis/tools/builtin/__init__.py`.

## Anatomy

```python
from pydantic import Field
from aegis.domain.enums import EvidenceKind, RiskLevel, ToolCategory
from aegis.tools.context import ToolContext
from aegis.tools.definition import EvidenceDraft, ToolArgs, ToolOutput, tool

class QueueDepthArgs(ToolArgs):
    service: str = Field(description="service that owns the queue")
    window_seconds: int = Field(default=300, ge=30, le=1800)

@tool(
    "inspect_queue_depth",
    description="Queue depth and consumer lag for a service over a window.",
    category=ToolCategory.READ_ONLY,
    risk=RiskLevel.NONE,
    args=QueueDepthArgs,
)
async def inspect_queue_depth(ctx: ToolContext, args: QueueDepthArgs) -> ToolOutput:
    start, end = ctx.window(args.window_seconds)
    series = await ctx.telemetry.metrics(args.service, "queue_depth", start=start, end=end)
    latest = series.latest or 0.0
    summary = f"{args.service} queue depth {latest:.0f}"
    return ToolOutput(
        data={"service": args.service, "latest": latest},
        summary=summary,
        evidence=[
            EvidenceDraft(
                kind=EvidenceKind.METRIC, title=f"{args.service} queue depth", summary=summary,
                data={"latest": latest}, strength=0.5 if latest > 1000 else 0.2,
                service=args.service, tags=["queue"],
            )
        ],
    )
```

Then add it to the module's list (`READ_TOOLS`, `DIAGNOSTIC_TOOLS`, `MUTATING_TOOLS`,
`DANGEROUS_TOOLS` — `build_default_registry` wires all four) and to the phases of the flow packs
that may request it (`docs/development/flow-pack-authoring.md`).

The registry currently holds 25 tools: 11 read (all `read_only`, risk `none`), 6 diagnostic (all
`diagnostic`, risk `low`), 5 mutating (`restart_service` and `rollback_deployment` `medium`;
`scale_service`, `clear_cache` and `rotate_connection_pool` `low`) and 3 dangerous (risk
`critical`). The risk of a mutation is not cosmetic: `policies/default.yaml` lets low-risk mutations
run autonomously in `development` (`allow-low-risk-mutations-development`), so a new `low` mutation
is autonomous there by default.

### `@tool` parameters

| parameter | meaning |
|---|---|
| `name` | unique registry key; also the `tools:` entry in flow packs and the label in metrics |
| `description` | shown to the model in the allowed-tools list; say what it reveals and, for mutations, what it does *not* fix |
| `category` | `read_only` / `diagnostic` / `mutating` / `dangerous` (see below) |
| `risk` | `none` / `low` / `medium` / `high` / `critical`; compared with the phase or flow risk ceiling (check 10) and matched by policy rules (`min_risk`/`max_risk`) |
| `args` | a `ToolArgs` subclass |
| `idempotent` | informational today; keep it truthful |
| `timeout_seconds` | `asyncio.wait_for` bound in the executor (default 10; mutations use 30, and `search_incident_memory` overrides it to 20) |
| `retry` | `RetryPolicy`; default 3 attempts for non-mutating tools, 1 for mutations (the executor never retries a mutation) |
| `version` | default `"1"`; appears in `ToolSpec.ref` (`name@version`) and on execution records |
| `allowed_environments` | check 7; defaults to all |
| `min_severity` | check 8; tool usable only when the incident is at least this severe |
| `verification_metrics` | mutating tools only; see below |
| `capabilities` | free-form tags exposed over the API |

### Arguments: `ToolArgs`

`ToolArgs` sets `extra="forbid"`, `str_strip_whitespace=True` and `str_max_length=512`. Before the
model is even validated, `guard_strings` rejects any string argument longer than 512 characters
(`MAX_STRING_ARG`, so the length bound is enforced twice) or containing `../`, `; & | \` $`,
`$(`, `<script`, `://` or `\xNN` escapes, at any nesting depth. After validation, `parse_args`
checks that fields named `service`, `component`, `source` or `target` are known topology components
(`ctx.known_components`, built from `TelemetryProvider.topology()`). Use `Field` bounds for every
numeric argument (`ge`/`le`) and patterns for identifiers (see `RollbackArgs.to_version`,
`ReproduceArgs.endpoint`). The JSON schema of the model becomes `ToolSpec.arguments_schema` and is
rendered into the prompt, so `description=` on fields is model-facing documentation.

### Context: `ToolContext`

Built by the runtime, never by the model (`src/aegis/tools/context.py`): the incident, flow and
phase, the acting `Actor`, environment, `telemetry` (read side), `infrastructure` (write side and
`run_diagnostic`, so not only mutating tools and diagnostics use it — the read-only
`inspect_service`, `inspect_dependencies`, `inspect_database` and `inspect_redis` all call
`ctx.infrastructure.run_diagnostic(...)`), optional `memory_search`, a `Clock`, the optional
`agent_run_id` / `action_plan_id` / `approval_id`, `known_components`, `default_window_seconds`
(300), and helpers `now()`, `window(seconds)` (defaults to `default_window_seconds`, capped at
1800 s) and `incident_window()`.

### Output: `ToolOutput` and `EvidenceDraft`

`data` is stored verbatim on the execution record; `summary` is what the model and the console read;
`evidence` is a list of `EvidenceDraft`s that the executor turns into `Evidence` rows tied to the
execution. Choose `strength` deterministically with the helpers in `src/aegis/tools/strength.py`
(`ratio_strength`, `recency_strength`, `share_strength`) and set `service` to the component the
evidence implicates, not necessarily the one queried (see `_resource_output`, which names the top
client when it holds ≥ 40% of connections). The hypothesis engine gives full weight to evidence whose
`service` matches the suspected root cause, and the attribution and propagation rules read specific
keys (`component`, `saturation`, `connections_by_client`/`clients_by_service`, `leaked_by_service`,
`idle_in_transaction_by_client`, `contributions[].error_contribution`, `own_error_rate`); reuse them if
your data has the same meaning.

## Categories

- `read_only` (risk `none`): observation only; allowed in any phase that lists them; role `viewer`.
- `diagnostic` (risk `low`): active checks that still mutate nothing (probes, synthetic requests,
  projections); role `operator`; phases need `risk_ceiling: low`.
- `mutating` (role `operator`): changes the world. Refused inside the agent loop (check 6 and the
  `no_mutation_inside_agent_loop` invariant); executed only by the workflow after policy and, where
  required, approval; exactly-once by idempotency key; never retried. The handler calls
  `ctx.infrastructure.*`, and `InfrastructureGateway` (`src/aegis/ports/telemetry.py`) needs a new
  method if the action is new.
- `dangerous` (risk `critical`, role `admin`): registered so policy and authorization can be tested
  against them; never executable (`_never` raises). Do not add real handlers.

The roles are check 9's `REQUIRED_ROLE` in `src/aegis/tools/authorizer.py`: `read_only` → `viewer`,
`diagnostic` → `operator`, `mutating` → `operator`, `dangerous` → `admin`.

## Why mutating tools declare `verification_metrics`

`build_verification_spec` (`src/aegis/remediation/planning.py`) derives what a remediation must
prove from the incident's detection signals **plus** `tool_spec.verification_metrics` on the plan's
target service, plus `<target>.up`. The model does not choose the conditions. A tool therefore
declares the metrics it claims to affect: `restart_service` → `error_rate`, `latency_p95_ms`, `up`;
`rollback_deployment` → `error_rate`, `latency_p95_ms`; `scale_service` → `latency_p95_ms`,
`cpu_percent`; `rotate_connection_pool` → `connections`, `latency_p95_ms`; `clear_cache` →
`hit_rate`. Only metrics present in `_METRIC_RULES` produce conditions (`latency_p95_ms`,
`latency_p50_ms`, `error_rate`, `request_rate`, `connections`, `saturation`,
`idle_in_transaction`, `cpu_percent`, `memory_percent`, `db_pool_wait_ms`, `redis_pool_wait_ms`,
`up`); a `verification_metrics` entry with no rule is **silently dropped**, which is the trap to
know about: `hit_rate` is not in `_METRIC_RULES`, so `clear_cache`'s declared metric produces no
condition, ever. Add a rule there if your tool affects a new metric. `verification_metrics` are
measured on the plan's *target* service, and `<target>.up` is always added. If no condition is
produced at all, `build_verification_spec` falls back to `error_rate` and `latency_p95_ms` for
every service in `incident.affected_services`.

Also decide whether the action is reversible: `default_rollback` knows `rollback_deployment` and
`scale_service`, and decides availability from the *execution result* (`from_version` /
`from_replicas`), i.e. after the action has run — before that it returns
`available=False, reason="… unknown until execution"`. `restart_service`,
`rotate_connection_pool` and `clear_cache` have an explicit case returning `available=False`
("not reversible; a failed verification escalates"), and any tool with no case at all gets
`available=False, reason="no rollback defined for this tool"`. If your mutation has a natural
inverse, add a case.

## The guards a mutating tool must pass in `planning.py`

`src/aegis/remediation/planning.py` refuses a plan on two separate grounds, and a new mutating tool
has to be registered with the second one or it slips past it entirely:

- `validate_remediation_target` — the plan must act on the hypothesis' root-cause service, with two
  exceptions: `rotate_connection_pool` on a dependent of the root cause when
  `arguments["target"] == root` (rotating the leaking client's pool), and any remediation on the
  client of a saturated `database`/`cache` root cause. Otherwise `TargetMismatch`.
- `validate_remediation_fit` — the remediation must address the *diagnosed mechanism*, not just the
  right service. `_FIT` maps `restart_service` → {`resource_exhaustion`, `dependency_failure`,
  `configuration`, `unknown`}, `rotate_connection_pool` → {`resource_exhaustion`},
  `rollback_deployment` → {`deployment_regression`}, `scale_service` → {`capacity`}, `clear_cache` →
  {`configuration`, `unknown`, `resource_exhaustion`}. A rollback is additionally refused unless the
  hypothesis category is `deployment_regression` or recent-deployment evidence for the target
  exists. **A tool missing from `_FIT` is not fit-checked at all** (`_FIT.get(...) is None` returns
  early), so add yours.
- `_effective_category`, inside that fit check — the evidence may override a category the model
  mislabelled: a target that
  is CPU-bound (a `cpu_ok` tag, which only lands in the tags when that health check *fails*, or
  `cpu_percent >= 90`) with no memory pressure (a `memory_ok` tag or `memory_percent >= 85`) and no
  leaked state (`leaked_by_service` / `idle_in_transaction_by_client` naming it, or a pool wait
  ≥ 200 ms) is treated as `capacity`, and `restart_service`/`rotate_connection_pool` are then
  refused with "use scale_service instead".

Both raise (`TargetMismatch` / `RemediationMismatch`) with a message the model is expected to act
on, so the wording matters as much as the rule.

## Tests to add

- Unit: a handler test against the in-process simulator (or a fake telemetry provider) asserting
  summary, data and evidence strength for a healthy and an unhealthy state. Extend
  `tests/unit/test_executor.py::test_every_read_and_diagnostic_tool_runs_against_the_simulator` for
  read/diagnostic tools so the tool is exercised through `ToolExecutor`.
- Authorization: a case in `tests/unit/test_authorizer.py` (allowed in the intended phase, denied
  elsewhere) and, for mutations, an entry in `tests/security/test_tool_abuse.py::ATTACKS` proving it
  is refused inside the loop with `phase_violation`/`tool_not_allowed` and leaves the simulator's
  `action_log` untouched.
- Arguments: negative cases for each bound and for injection-shaped strings (they must fail at
  `arguments_valid` with `invalid_tool_arguments`).
- Flow: `tests/unit/test_flow.py::test_shipped_flow_packs_reference_only_registered_tools` will fail
  until the tool exists if you referenced it in a pack first; that is the intended order check.
- Evals: add the tool to `evals/suites/authorization.py::CORPUS` (legitimate and adversarial uses;
  `false_allows` must stay 0). For a mutation, add its scenario mapping to
  `evals/suites/agent.py::ACTION_FOR_TOOL` and the ground truth to
  `src/aegis/simulator/faults.py` so the agent eval can grade it.
- Optional: a policy rule in `policies/default.yaml` if the default category behaviour is wrong for
  it, with a case in `tests/unit/test_policy.py::test_default_policy_matrix`.
