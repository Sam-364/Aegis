# Working on Aegis

Aegis is a durable autonomous incident-response runtime. The invariant that must never be
inverted: **the model proposes, the runtime decides, the policy engine authorizes, the tool
executes, verification proves.** Mutating tools run only through the Temporal workflow after policy
evaluation and (usually) human approval, keyed for exactly-once execution. The agent never changes
incident status, never approves, never mutates.

## Commands

```bash
uv sync --extra dev                 # Python 3.12 environment
make lint typecheck test            # ruff + import-linter, mypy --strict, unit + security tests
make test-infra test-integration    # throwaway pgvector Postgres :5439 + Redis :6389, adapter tests
make test-workflow                  # Temporal time-skipping server (downloads on first run)
make evals                          # offline eval suites → evals/results/
make dev                            # docker compose up --build (core stack)
make e2e                            # five scenarios against the running stack (needs the LLM key)
```

The OpenAI key is read from `AEGIS_LLM_API_KEY_FILE` (locally `.secrets/openai_api_key`, gitignored).
Tests set `AEGIS_LLM_PROVIDER=scripted`; never make real LLM calls in unit tests.

## Layering (enforced by import-linter, see pyproject.toml)

`domain` → `ports` → runtime core (`flow`, `policy`, `tools`, `hypotheses`, `evidence`, `detection`,
`agent`, `verification`, `remediation`, `memory`, `llm`) → `infrastructure` / `application` →
`api`, `workflows`, `apps`. `simulator` is independent of the runtime. Run `uv run lint-imports`.

## Where things live

- Scenario ground truth: `src/aegis/simulator/faults.py` (shared by simulator, E2E and evals).
- Authorization pipeline: `src/aegis/tools/authorizer.py` (14 ordered checks; never raise, return a trace).
- Policy invariants: `src/aegis/policy/engine.py`; rules: `policies/default.yaml`.
- Hypothesis scoring: `src/aegis/hypotheses/engine.py` (attribution/propagation rules matter).
- Agent graph: `src/aegis/agent/runtime.py`; prompts: `agent/prompts.py`; fallback: `agent/planner.py`.
- Workflow: `src/aegis/workflows/incident_workflow.py` (deterministic; all side effects in `activities.py`).
- Postgres storage pattern: JSONB `document` + projected columns; repositories own `version`.

## Conventions

- Strict typing (`mypy --strict`), ruff line length 100, no `Any` in public signatures where avoidable.
- Pydantic models for anything persisted or sent over the wire; strict-schema-safe models for LLM output
  (no free-form dicts; arguments travel as JSON strings and are validated by the tool's model).
- Every new tool needs: `@tool` definition with `ToolArgs`, evidence drafts with computed strength,
  a unit test in `tests/unit/test_executor.py` style, and, if mutating, `verification_metrics`.
- Never delete or rewrite audit events; the database trigger will refuse anyway.
