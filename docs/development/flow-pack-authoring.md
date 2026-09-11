# Authoring a flow pack

A flow pack is a YAML document in `AEGIS_FLOWS_DIR` (default `flows/`) compiled at startup by
`aegis.flow.loader.parse_flow` into the immutable `FlowPack` model in `src/aegis/domain/flow.py`.
The three shipped packs are `flows/incident-investigation.yaml`, `flows/api-latency.yaml` and
`flows/error-rate.yaml`; copy one of them.

## Top-level fields

| key | type | notes |
|---|---|---|
| `name` | string | registry key together with `version` (the filename is irrelevant: `flows/api-latency.yaml` declares `api-latency-investigation`); the incident pins both |
| `version` | string (quote it: `"1.1.0"`) | `FlowRegistry.get(name)` returns the highest numeric version; `_version_key` keeps only the digits of each dot-separated piece, so `1.1.0-rc1` sorts as `(1, 1, 1)` |
| `description` | string | shown in the console |
| `applies_to` | list of `SignalKind` (`latency`, `error_rate`, `saturation`, `traffic`, `availability`, `resource`) | selection: most overlap with the incident's signal kinds, then highest `priority`; `[]` means "fallback only" — `FlowRegistry.select` skips packs with an empty `applies_to` and falls back to its hard-coded `default="incident-investigation"`, so an empty `applies_to` is reachable only for a pack with that exact name |
| `priority` | int, default 0 | tie-breaker between packs with equal overlap |
| `initial_phase` | string | must name a phase |
| `remediation_tools` | list of tool names | tools the **workflow** may execute for a plan; they are not loop tools. `plan_remediation` proposals outside this list are rejected |
| `remediation_risk_ceiling` | `RiskLevel`, default `high` | authorization check 10 for workflow executions |
| `budget` | object | `max_iterations` (1–200, default 20), `max_tool_calls` (1–500, 40), `max_llm_calls` (0–500, 30), `max_llm_tokens` (200 000), `max_runtime_seconds` (≥10, 900), `max_remediation_attempts` (0–5, 2). No shipped pack uses these defaults: incident-investigation sets 24/40/30/250 000/900/2, api-latency and error-rate 22/36/26/220 000/900/2. All of them are checked at the top of every agent observation and again at authorization check 14; `max_runtime_seconds` is wall clock charged per observation, with a single gap capped at 120 s so a worker restart is not billed to the agent |
| `severity_budget_factor` | map severity → float | defaults `sev1: 1.5, sev2: 1.25, sev3: 1.0, sev4: 0.75`; scales every budget field except remediation attempts |
| `phases` | list | see below |

The whole incident shares one `BudgetUsage`; the workflow threads it from phase to phase and the
authorizer's check 14 and the agent's `observe` node stop the run when it is exhausted.

## Phase fields

| key | type | notes |
|---|---|---|
| `name` | string | unique within the pack |
| `objective` | string | shown to the model as the phase objective |
| `tools` | list of tool names | becomes `allowed_tools`; the model may *request* only these (check 5). Terminal phases must have none |
| `max_iterations` | int 0–100, default 6 | when reached, the `exhausted` transition fires |
| `timeout_seconds` | int ≥ 5, default 300 | parsed and exposed over the API, **not enforced** by the runtime today; the only enforced `timeout_seconds` is the tool spec's (the phase activity is bounded by `AEGIS_AGENT_PHASE_TIMEOUT_SECONDS` instead) |
| `risk_ceiling` | `RiskLevel`, default `none` | check 10 for loop calls; read-only tools are risk `none`, diagnostics `low`, so a phase that runs diagnostics needs `risk_ceiling: low` |
| `exit_conditions` | list of `{kind, value?, description?}` | all must hold; a phase without conditions is "met" after one iteration |
| `transitions` | list of `{when, to}` | see the `when` gotcha below |
| `terminal` | bool | ends the flow; `verify` and `escalate` are the names the workflow understands |
| `guidance` | string | extra instructions appended to the prompt for this phase |
| `plans_remediation` | bool | adds the remediation-tools section to the prompt; the `remediate` phase |

### Exit condition kinds

Evaluated by `FlowRuntime.evaluate_exit` against `PhaseFacts` computed by the runtime (never
supplied by the model):

| kind | true when |
|---|---|
| `min_evidence` | evidence rows for the incident ≥ `value` |
| `min_hypotheses` | hypotheses ≥ `value` |
| `min_hypothesis_confidence` | top hypothesis confidence ≥ `value` |
| `hypothesis_validated` | some hypothesis is `confirmed`, or `supported` with at least one confirmed test |
| `action_planned` | this phase produced an `ActionPlan` |
| `no_action_required` | the agent's guarded `conclude_no_action` was accepted |
| `min_iterations` | iterations in this phase ≥ `value` |

### Transitions and the `when` key

```yaml
transitions:
  - { when: exit_conditions_met, to: hypothesize }
  - { when: exhausted, to: hypothesize }
  - { when: no_action_required, to: verify }
```

The YAML key is **`when`**. The domain field (and the JSON returned by `GET /api/v1/flows`) is `on`.
The loader reads `t["when"]` and raises `FlowDefinitionError` (`KeyError: 'when'`) if you write
`on:`; the reason is that PyYAML parses a bare `on` as the boolean `True`. Triggers are
`exit_conditions_met`, `exhausted`, `escalate`, `no_action_required`. `FlowRuntime.decide` checks
`no_action_required` first, then `exit_conditions_met`, then on exhaustion uses the `exhausted`
transition or falls back to an `escalate` transition; if none exists the phase ends with
`next_phase = None`, which the workflow treats as a terminate.

## Phase names are part of the contract

Several components key on phase names, so new packs should keep `triage`, `investigate`,
`hypothesize`, `validate`, `remediate`, `verify`, `escalate`:

- `PHASE_ACTIONS` in `src/aegis/agent/prompts.py` decides which model actions are legal per phase;
  an unknown phase name gets only `call_tool`, `phase_complete`, `escalate`.
- `DeterministicPlanner.propose` has a playbook per standard phase and escalates on an unknown one.
- The workflow maps `investigate`/`hypothesize`/`validate` to incident statuses (`PHASE_STATUS`) and
  gives `verify` (run recovery verification) and `escalate` (finish as escalated) special meaning.

## Validation at startup

`build_container` loads every file matching `*.y*ml` in the directory, and a file may hold several
packs because the loader reads all YAML documents in it, then:

1. `load_flow_dir` raises `FlowDefinitionError` if the directory does not exist, and again if it
   yields zero packs.
2. `parse_flow` rejects unknown enum values, wrong types and missing required keys with
   `FlowDefinitionError` naming the source file.
3. `FlowPack._validate_graph` rejects duplicate phase names, an undefined `initial_phase`,
   transitions to unknown phases, non-terminal phases without transitions, terminal phases with
   tools, and packs without a terminal phase.
4. `FlowRegistry.register` rejects a duplicate `name@version`.
5. `FlowRegistry.validate_tools(registry.names(), mutating_tools)` rejects any tool name (phase or
   remediation) the tool registry does not know, and any **mutating** tool listed in a phase's
   `tools:` — the authorizer would refuse it at check 6 anyway, but a pack that asks for it is a
   mistake worth catching at load time rather than mid-incident. Declare it under
   `remediation_tools` instead.

A failure aborts process start; `tests/unit/test_flow.py::test_shipped_flow_packs_reference_only_registered_tools`
guards the shipped packs. Each pack gets a `checksum` (first 16 hex chars of the SHA-256 of the
canonical YAML) that appears in the API.

## Versioning

Bump `version` whenever you change behaviour. Incidents record `flow_name`/`flow_version` at intake
and `triage_incident` loads that exact version, so a deploy with a new version does not change
running incidents. Keep the old version in the directory while such incidents exist, or the worker
raises `NotFoundError` for them.

## Checklist

- Every tool in `tools` and `remediation_tools` exists (`GET /api/v1/tools` or
  `build_default_registry().names()`), and no `tools:` entry is `mutating` or `dangerous`.
- Diagnostic phases have `risk_ceiling: low`; the `remediate` phase has `plans_remediation: true`.
  Its `risk_ceiling` does **not** need to cover the remediation tools' risk: check 10 compares a
  loop call against `phase.risk_ceiling` and a workflow execution against
  `flow.remediation_risk_ceiling`, and the `remediate` phase's own `tools:` are read-only in all
  three shipped packs.
- `exhausted` transitions exist for every non-terminal phase, or an `escalate` one.
- `uv run pytest tests/unit/test_flow.py` and `make lint` pass.
