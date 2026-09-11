# ADR-001: Temporal owns the incident; LangGraph owns one agent phase

- Status: Accepted, implemented (2026-09-10)
- Code: `src/aegis/workflows/incident_workflow.py`, `src/aegis/workflows/activities.py`,
  `src/aegis/agent/runtime.py`, `src/aegis/application/bootstrap.py`

## Context

An incident lives for minutes to hours, waits on humans, and must survive worker crashes without
re-running a mutation. An LLM reasoning loop is a tight, chatty cycle of prompts and read-only tool
calls whose payloads should not end up in a workflow history. The original plan ran the whole agent,
including remediation, as one long activity inside LangGraph.

## Decision

Split responsibilities by durability requirement:

- **Temporal** is the durable spine. `IncidentWorkflow` is deterministic (ids from
  `workflow.uuid4()`, waits are `wait_condition` with timeouts, every side effect is an activity).
  It owns the phase loop, status transitions, policy evaluation, the approval wait (signal
  `approval_decided`, timeout `approval_timeout_seconds`), the `cancel` signal, execution,
  verification, rollback, replanning and finalization. Workflow ids are `incident-<incident uuid>`
  with `ALLOW_DUPLICATE_FAILED_ONLY`, so an incident has at most one live workflow. The status query
  returns a six-field `WorkflowStatus`: phase, status, awaiting approval id, remediation attempts,
  phases visited and cancelled.
- **LangGraph** runs *one phase at a time* inside the `run_agent_phase` activity. The graph
  (`observe → propose → {execute_tool, apply_hypotheses, plan_remediation, conclude}`, plus
  `propose → observe` when no proposal was produced, and a conditional edge from each of those four
  action nodes back to `observe` or to `END`) is checkpointed with `thread_id = agent_run_id` and
  `recursion_limit: 400`, and the workflow supplies that id. A retried activity resumes at the last
  completed node instead of repeating tool calls. The activity heartbeats at every node
  (`heartbeat_timeout=90s`), so a hung model call is retried quickly. The Postgres
  checkpointer is only built for `role == "worker"`; every other role falls back to `InMemorySaver`,
  so only the worker's phases are resumable across processes.
- The agent never executes a mutating tool. `plan_remediation` only stores an `ActionPlan`; the
  workflow's `execute_remediation` activity is the only caller of `ToolExecutor` with
  `in_agent_loop=False`.
- Large state (evidence, hypotheses, plans) lives in Postgres and is re-read at every node; the
  LangGraph state is a handful of ids, counters and the last proposal, and the Temporal history holds
  only small typed contracts (`src/aegis/workflows/contracts.py`, pydantic data converter).

## Consequences

- Crash safety at two granularities: Temporal replays the workflow and retries activities; LangGraph
  resumes the phase. Combined with the idempotency ledger, a mutation happens once
  (`tests/chaos/test_worker_crash.py`).
- Human waits cost nothing: an approval can take 15 minutes (default) with no process holding state.
- Two runtimes to operate (Temporal server plus the worker's Postgres checkpointer), and two places
  to look when debugging: the Temporal UI for the spine, `agent_runs`/`agent_steps` for the loop.
- `max_phases=14`, `MAX_PHASE_ENTRIES = 2` (a third entry into the same phase escalates, which
  usually binds sooner than `max_phases`) and per-flow budgets bound the workflow length; a phase
  cannot exceed `agent_phase_timeout_seconds` (600).
- The workflow's phase-to-status mapping is by phase *name* (`PHASE_STATUS`) and is partial: only
  `investigate`, `hypothesize` and `validate` map to a status, so entering `triage` or `remediate`
  leaves the incident status unchanged (`_enter_phase` is a no-op for an unmapped name). The special
  names `verify` and `escalate` are interpreted by the workflow. Custom flow packs must keep those
  names.

## Alternatives considered

- One long-running LangGraph run per incident with Postgres checkpoints only: no durable timers or
  signals, no retry policy per side effect, approvals would need a custom queue, and the
  checkpointer would carry the entire history.
- Temporal only, with the agent loop written as workflow steps: every LLM call and tool call would
  become an activity with its payload in the history; unbounded history growth, and awkward
  branching logic in workflow code.
- A separate Temporal workflow per phase (child workflows): more history, no benefit over a
  checkpointed activity for a loop that lasts seconds to a few minutes.
