# ADR-006: Policy engine with non-overridable invariants and default deny

- Status: Accepted, implemented (2026-09-10)
- Code: `src/aegis/policy/engine.py`, `src/aegis/policy/loader.py`, `src/aegis/domain/policy.py`,
  `policies/default.yaml`

## Context

Which remediations may run autonomously, which need a human, and which are never allowed depends on
environment, severity, risk and tool. Operators must be able to tune that without a deploy, but some
guarantees must not be tunable at all, and an unmatched request must not slip through.

## Decision

- `PolicyEngine.evaluate(PolicyContext)` runs three invariants in code before any rule:
  `dangerous_tools_denied`, `no_mutation_inside_agent_loop`, `agent_cannot_mutate`. YAML cannot
  weaken them (`tests/unit/test_policy.py::test_invariants_cannot_be_overridden`).
- Rules come from `AEGIS_POLICIES_DIR` (default `policies/`), are validated at load
  (`PolicyDefinitionError` on unknown enum values, or on duplicate names *within one file* —
  `load_policy_file` checks its own rules, while `load_policy_dir` concatenates files with no
  cross-file name check), sorted by `priority` descending, and evaluated first-match-wins. A rule
  matches on any combination of `tools`, `categories`, `min_risk`/`max_risk`, `environments`,
  `severities`, `phases`, `flows`, `actor_kinds`, `required_role`, `in_agent_loop`; empty predicates
  match everything.
- No match → `deny` with reason "no policy rule matched; default is deny".
- The decision is a value object (`PolicyDecision`: effect, `matched_rule`, `evaluated_rules`,
  `invariant`, the full context) that is emitted as `policy.decided`, so every autonomous or approved
  action can be traced to the rule that allowed it. What is persisted on the action plan is narrower
  and drops the context: `{effect, matched_rule, reason, invariant, evaluated_rules}`. An invariant
  decision returns before any rule is considered, so `evaluated_rules` is empty and an invariant
  denial audits zero evaluated rules.
- The `PolicyContext` is built by the runtime only (authorizer check 11, or
  `evaluate_remediation_policy` with `in_agent_loop=False` and the workflow actor); the model never
  supplies it.

## Consequences

- The shipped default (`policies/default.yaml`) makes development permissive for low-risk
  mutations and requires a human for everything risky, for every mutation in staging and
  production, for every `sev1` mutation and for every rollback. High-risk mutations are denied in
  production outright. Its `deny-dangerous` rule at priority 1000 is unreachable because the
  `dangerous_tools_denied` invariant fires first, and the catch-all `approve-remaining-mutations` at
  priority 100 means default-deny effectively bites only read-only and diagnostic tools.
- A policy file that forgets to allow read-only tools would deny the whole investigation; the
  default-deny posture is deliberate and the loader refuses an empty policy directory.
- Rules can only make things stricter than the invariants, never looser; there is no rule that
  can execute a `dangerous` tool or let the agent mutate.
- `PolicyContext.remediation_attempt`, `tenant_id`, `arguments` and `incident_id` are carried on the
  context, but `PolicyRule` declares no predicate for any of them and `matches()` never reads them,
  so no rule can match on them at all.

## Alternatives considered

- Everything in code: no operator tuning without a release.
- An external policy engine (OPA/Rego): more expressive, but adds a service, a language and a
  network hop to a decision that must also be evaluated in unit tests and evals in-process.
- Default allow with deny lists: one forgotten tool becomes an autonomous mutation.
