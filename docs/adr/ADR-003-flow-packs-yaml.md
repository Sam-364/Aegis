# ADR-003: Investigation programmes are declarative, versioned flow packs

- Status: Accepted, implemented (2026-09-10)
- Code: `src/aegis/domain/flow.py`, `src/aegis/flow/loader.py`, `registry.py`, `runtime.py`,
  `flows/*.yaml`

## Context

What the agent is allowed to do must be reviewable by people who do not read Python, must be
versioned so an in-flight incident is not changed under it, and must be validated before the process
serves traffic. The phases, the tools each phase may request, the risk ceilings, the exit conditions
and the budgets are policy, not code.

## Decision

- A flow pack is a YAML document compiled by `parse_flow` into an immutable `FlowPack` value object
  (name, version, `applies_to` signal kinds, priority, phases, `remediation_tools`,
  `remediation_risk_ceiling`, `ExecutionBudget`, per-severity budget factors, and a checksum of the
  canonical YAML).
- Packs are loaded from `AEGIS_FLOWS_DIR` (default `flows/` at the repository root; `/app/flows` in
  the image, which is all the Dockerfile contributes) at process startup, where `build_container`
  calls `FlowRegistry(load_flow_dir(...))`. Files are globbed as `*.y*ml` and each file may hold
  several YAML documents, so one file can ship more than one pack; the checksum is the sha256 of the
  canonical YAML truncated to 16 hex characters. Loading fails fast on malformed definitions
  (`FlowDefinitionError`), on graph errors (`FlowPack._validate_graph`: unique phase names, defined
  initial phase, transitions to known phases, non-terminal phases have transitions, terminal phases
  allow no tools, at least one terminal phase), on duplicate `name@version`, and on references to
  tools the registry does not know (`FlowRegistry.validate_tools`).
- Selection is deterministic and by pack *name*, not filename: `FlowRegistry.select` skips packs
  with an empty `applies_to`, scores the remaining latest-version packs by overlap
  between their `applies_to` and the incident's signal kinds, then by `priority`, and falls back to
  its hard-coded `default="incident-investigation"` (which declares `applies_to: []`). Two of the
  three shipped filenames differ from the pack name they contain (`flows/api-latency.yaml` holds
  `api-latency-investigation`, `flows/error-rate.yaml` holds `error-rate-investigation`). The chosen
  `flow_name` and `flow_version` are pinned on the incident; `triage_incident` reuses them, so
  deploying a new pack version never changes a running incident.
- Phases enumerate the tools the agent may *request*; the authorizer decides whether a request
  executes (checks 4, 5, 10). `remediation_tools` are workflow-side only and never appear in the loop
  tool list.
- Exit conditions are declarative predicates over `PhaseFacts` computed by the runtime
  (`min_evidence`, `min_hypotheses`, `min_hypothesis_confidence`, `hypothesis_validated`,
  `action_planned`, `no_action_required`, `min_iterations`); transitions are keyed by trigger
  (`exit_conditions_met`, `exhausted`, `escalate`, `no_action_required`). The LLM cannot end a phase
  by fiat: `phase_complete` is advisory.

## Consequences

- Three packs ship: `incident-investigation@1.1.0` (fallback), `api-latency-investigation@1.2.0`
  (`latency`, `saturation`), `error-rate-investigation@1.1.0` (`error_rate`, `availability`). All
  use the same phase names, which matters because `PHASE_ACTIONS` (allowed model actions), the
  deterministic planner's playbooks, and the workflow's `PHASE_STATUS`/`verify`/`escalate` handling
  are keyed by phase name.
- The YAML transition key is `when`, while the domain field and API output is `on` (PyYAML parses a
  bare `on` as boolean `True`, which is why the loader avoids it). Authoring guidance is in
  `docs/development/flow-pack-authoring.md`.
- `FlowPhase.timeout_seconds` is parsed and exposed but not enforced by the runtime; the effective
  phase bound is the workflow's `agent_phase_timeout_seconds` and the pack's budget.
- Packs are exposed read-only over `GET /api/v1/flows` and `GET /api/v1/flows/{name}` so the console
  can render the programme.

## Alternatives considered

- Hard-coded phase logic in the agent: no review path for non-developers, no versioning, no way for
  different signal kinds to get different programmes.
- A general workflow DSL with conditions and expressions: more power than needed, harder to
  validate, and it would move authorization decisions into a language the authorizer cannot reason
  about.
- Letting the model choose when a phase ends: it cannot be trusted to judge sufficiency; exit
  conditions are facts the runtime computes.
