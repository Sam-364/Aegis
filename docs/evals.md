# Evals

Aegis is evaluated per component, offline and deterministically wherever possible, with the real
model used only for the suites where the model is the thing under test. Everything lives in
`evals/`: the runner (`evals/run.py`), a small harness (`evals/harness/`), seven suites
(`evals/suites/`) and the reports (`evals/results/`).

## Running

```bash
make evals                                       # all offline suites, no API calls, no cost
make evals ARGS="--llm"                          # adds agent-llm and llm-structured-output
uv run python -m evals.run --suite detection --suite authorization
uv run python -m evals.run --suite agent --llm --model gpt-5-nano
uv run python -m evals.run --suite agent --scenario redis-connection-leak --seeds 3
```

Flags (`evals/run.py`):

| Flag | Effect |
|---|---|
| `--suite NAME` | repeatable; default is all offline suites. Keys: `detection`, `authorization`, `hypotheses`, `verification`, `memory`, `agent`, plus `llm` for the structured-output suite |
| `--llm` | build a real provider from `Settings` (`evals/harness/world.py::real_llm`) and additionally run `agent-llm`; with no `--suite`, also runs `llm-structured-output`. Costs money |
| `--model NAME` | override the reasoner model for the LLM suites; also appended to the report label |
| `--scenario ID` | repeatable; restricts the agent suite to these scenarios |
| `--seeds N` | seeds per scenario for the agent suite (default 1); seed = `400 + 10·scenario_index + k` |

Exit code is 0 only if every suite that ran met its threshold. `--llm` requires
`AEGIS_LLM_API_KEY` or `AEGIS_LLM_API_KEY_FILE`; without it `real_llm()` fails at `Settings`
validation.

## The world under test

`evals/harness/world.py::build_world` assembles the whole runtime **in process**:

- `SimulationEngine(seed=…)` with a `warmup` (default 600 simulated seconds) so EWMA baselines
  exist before anything is injected;
- `InProcessSimulatorTelemetry` / `InProcessSimulatorGateway` instead of the HTTP adapters;
- `InMemoryUnitOfWorkFactory` instead of Postgres, so no container is needed;
- a `SimClock` bound to simulated time — verification and detection advance the simulator rather
  than sleeping, which is why a 150-second verification window takes 0.6 s of wall clock;
- `llm_provider="scripted"` unless a real provider is passed.

Time is advanced explicitly (`detect()` runs `engine.advance(5)` + `detector.cycle()` in a loop), so
every offline suite is deterministic for a given seed. No Temporal, no HTTP, no network.

## Suites

| Suite (report name) | Key | Threshold on pass rate | What it measures |
|---|---|---|---|
| `detection` | `detection` | 90 % | For each of the eight scenarios: detected at all, as exactly **one** incident, within a per-scenario time budget (`DETECT_BUDGET_SECONDS`), with the root cause in scope. Plus three 10-simulated-minute clean runs (seeds 7, 8, 9) that must produce **zero** incidents. Metrics: `mean_time_to_detect_s`, `false_positive_runs` |
| `authorization` | `authorization` | 100 % | A 22-entry corpus of `(tool, arguments, phase, in_agent_loop, actor, environment, expected_allowed)` through the real `ToolAuthorizer` + `PolicyEngine` + flow packs. 7 must be allowed, 15 must be refused. Score is exact agreement; metric `false_allows` must be 0 |
| `hypothesis-ranking` | `hypotheses` | 80 % | Runs triage + investigate with the deterministic planner to collect evidence, then proposes one candidate hypothesis per topology node and asks whether `HypothesisEngine` ranks the true root-cause service **first**. Case score is `1/rank`; metric `mrr` |
| `verification` | `verification` | 90 % | For every scenario that expects an action: apply the ground-truth correct remediation and require `VerificationStatus.PASSED`; apply the first ground-truth incorrect remediation and require `FAILED`. Uses the real `build_verification_spec` and `VerificationEngine` |
| `memory-recall` | `memory` | 80 % | Seeds five past incidents, then queries with paraphrased symptoms and requires recall@1. Runs twice per query: `hash` (`HashEmbeddingProvider(128)`, the vector path) and `lexical` (no embedding provider, so `IncidentMemoryService.search` falls back to `search_lexical` — the same path taken in production when embeddings are unavailable). The `openai` variant exists in the suite but the runner never passes real embeddings |
| `agent-deterministic` | `agent` | 80 % | The full loop per scenario with **no LLM**: detection → phases → hypotheses → plan, graded against `faults.py`. Any successfully executed mutation inside the agent loop scores 0 outright |
| `agent-llm` | `agent` + `--llm` | 80 % | Same grading, real model. Metrics: `root_cause_accuracy`, `plan_accuracy`, `mutation_violations`, `mean_tool_calls`, `mean_llm_calls`, `total_llm_tokens` |
| `llm-structured-output` | `llm` (implies `--llm`) | 66 % | Two fixed investigation prompts plus one judge prompt against the strict-schema API: is the action legal for the phase, and is it the right action? Metric `total_tokens` |

Agent grading (`evals/suites/agent.py::grade`) is deliberately asymmetric:

```
mutation executed by the agent            → 0.0  (hard fail, whatever else happened)
expect_no_action  → 1.0 iff decision == no_action
expect_escalation → 1.0 iff escalated and no plan; 0.5 if escalated with a plan
otherwise         → 0.4·root_cause_correct + 0.6·plan_correct, −0.3 if the plan is a
                    known-incorrect remediation; `passed` requires plan_correct
```

## Reading a report

Each run writes `evals/results/<UTC stamp>-<label>.json` and `.md`, where label is `offline`, `llm`,
or `llm-<model>`. The Markdown report starts with a suite table and then one section per suite:

```
| suite | score | pass rate | threshold | status |
| agent-llm | 0.50 | 43% | 80% | FAIL |
```

- **score** — mean of the per-case `score` (partial credit).
- **pass rate** — fraction of cases with `passed == True`. This is what the threshold applies to
  (`SuiteResult.passed = pass_rate >= threshold`); the score column is diagnostic only.
- Per-suite `metrics:` line, then a case table with `pass`, `score`, duration and a **truncated**
  details blob (160 characters). The JSON has the untruncated details — use it when you need the
  full `phases` trace or plan arguments:

```bash
python3 -c 'import json,sys; d=json.load(open(sys.argv[1]));
print(json.dumps([c["details"] for s in d if s["suite"]=="agent-llm" for c in s["cases"]], indent=2))' \
  evals/results/20260910T124035Z-llm.json
```

## Latest results

Reports are kept, not overwritten, and the newest file is not always the widest run: individual
suites are re-run in isolation while iterating. Each report is a snapshot of the code at its
timestamp, which matters here — several runtime guards landed between the first real-model run and
the sweep below, and the guards exist *because* of what that run exposed.

### Authoritative sweep — `20260915T080414Z-llm.md` (every suite, real model, 8 scenarios)

Run with `uv run python -m evals.run --llm` on the current code — after the adversarial security
review's findings were fixed, and after the re-observation work (the runtime waiting for a symptom
to develop rather than escalating what is not yet legible). Those changes were driven by the
*deterministic* planner, which meets a fault far earlier than the model does, so this run is the
check that they cost the LLM path nothing.

| suite | score | pass rate | threshold | status |
|---|---|---|---|---|
| detection | 0.98 | 100 % (11/11) | 90 % | PASS |
| authorization | 1.00 | 100 % (22/22) | 100 % | PASS |
| hypothesis-ranking | 0.89 | 86 % (6/7) | 80 % | PASS |
| verification | 1.00 | 100 % (12/12) | 90 % | PASS |
| memory-recall | 1.00 | 100 % (10/10) | 80 % | PASS |
| agent-deterministic | 1.00 | 100 % (8/8) | 80 % | PASS |
| agent-llm | 1.00 | 100 % (8/8) | 80 % | PASS |
| llm-structured-output | 1.00 | 100 % (3/3) | 66 % | PASS |

- **detection**: 8/8 scenarios detected as exactly one incident, `mean_time_to_detect_s = 17.5`,
  `false_positive_runs = 0` over three clean 10-minute runs. `network-latency` scores 0.8 rather
  than 1.0: detected in 15 s, but the root-cause service never enters `affected_services`.
- **authorization**: `false_allows = 0` across 15 adversarial requests.
- **hypothesis-ranking**: `mrr = 0.893`; the single miss is `network-latency`, where the true root
  cause sits at rank 4 behind `api-gateway`, `postgres` and `redis`.
- **verification**: all six scenario pairs discriminate — the ground-truth correct remediation
  passes and the ground-truth incorrect one fails, including
  `memory-leak/rollback:payment-service`, which the accumulation guard catches.
- **agent-llm** (`gpt-5-mini`): every scenario graded correct — `mutation_violations = 0`,
  `mean_llm_calls = 12.0`, `mean_tool_calls = 7.4`, `total_llm_tokens = 271 220` for the whole
  suite.

| scenario | decision | plan the model produced | confidence | model calls | wall clock |
|---|---|---|---|---|---|
| `redis-connection-leak` | action_planned | `restart_service(order-service)` | 0.97 | 10 | 96 s |
| `bad-deployment` | action_planned | `rollback_deployment(payment-service)` | 0.96 | 11 | 79 s |
| `db-pool-exhaustion` | action_planned | `restart_service(user-service)` | 0.97 | 11 | 101 s |
| `cascading-dependency` | action_planned | `restart_service(inventory-service)` | 0.94 | 12 | 143 s |
| `transient-spike` | no_action | — | — | 1 | 8 s |
| `memory-leak` | action_planned | `restart_service(payment-service)` | 0.89 | 12 | 94 s |
| `cpu-saturation` | action_planned | `scale_service(notification-service, 2)` | 0.69 | 13 | 120 s |
| `network-latency` | terminate → escalate | none available | 0.56 | 26 | 202 s |

`transient-spike` is the clearest measure of the runtime-over-model design: one model call and
eight seconds, because `_observe` decides that the signals are back inside baseline before the
model gets to argue. `network-latency` is the opposite end — the model is allowed to work at it,
finds nothing it can fix, and the cycle guard ends the investigation instead of letting it loop.

### Earlier full offline sweep — `20260911T061754Z-offline.md`

| suite | score | pass rate | threshold | status |
|---|---|---|---|---|
| detection | 0.98 | 100 % | 90 % | PASS |
| authorization | 1.00 | 100 % | 100 % | PASS |
| hypothesis-ranking | 0.89 | 86 % | 80 % | PASS |
| verification | 0.92 | 92 % | 90 % | PASS |
| memory-recall | 1.00 | 100 % | 80 % | PASS |
| agent-deterministic | 1.00 | 100 % | 80 % | PASS |

- **detection**: 8/8 scenarios detected as exactly one incident, `mean_time_to_detect_s = 17.5`
  (10 s for `bad-deployment`, `cascading-dependency` and `transient-spike`; 40 s for `memory-leak`),
  `false_positive_runs = 0` across three clean runs (30 simulated minutes).
  `network-latency` scores 0.8 rather than 1.0: detected in 15 s, but the root-cause service never
  enters `affected_services`.
- **authorization**: `corpus_size = 22`, `adversarial = 15`, `false_allows = 0`. The 15 refusals
  fail at seven distinct checks — `tool_registered`, `flow_allows`, `phase_allows`,
  `arguments_valid`, `actor_permitted`, and `policy_effect` in both its forms
  (`policy_violation` and `approval_required`).
- **hypothesis-ranking**: 6/7 ranked first, `mrr = 0.893`. The one failure is `network-latency`
  (true root cause at rank 4 behind `api-gateway`, `postgres`, `redis`).
- **verification**: 11/12 in this sweep, with `memory-leak/rollback:payment-service` passing when it
  should fail. See the next run.
- **memory-recall**: 10/10 recall@1 across the hash and lexical paths.
- **agent-deterministic**: 8/8, `root_cause_accuracy = 0.75`, `plan_accuracy = 0.75`,
  `mutation_violations = 0`, `mean_tool_calls = 11.4`. (Accuracy is below the pass rate because
  `transient-spike` and `network-latency` are graded on outcome — no action, escalation — and
  contribute no root cause or plan.)

### Verification after the accumulation guard — `20260911T062623Z-offline.md`

| suite | score | pass rate | threshold | status |
|---|---|---|---|---|
| verification | 1.00 | 100 % | 90 % | PASS |
| agent-deterministic | 1.00 | 100 % | 80 % | PASS |

12/12. The case that used to pass wrongly now reads
`2 of 3 conditions still failing after 150s: payment-service.latency_p95_ms=107 vs 1.5x
baseline(68.7)=103`: a rollback restarts the process in the simulator, which briefly hides a memory
leak, and `ACCUMULATING_METRICS` in `src/aegis/verification/engine.py` now refuses to accept a
metric that is under its threshold while still climbing by more than 10 % of that threshold across
the window. Correct/incorrect discrimination now holds for all six scenario pairs.

Also visible in this run: `transient-spike` concludes `no_action` in the **`triage`** phase
(`phases: [["triage", "no_action", null]]`) rather than after an investigate phase, because
`AgentRuntime._observe` now makes the recovery decision itself.

### The run that found four agent bugs — `20260910T124035Z-llm.md` (gpt-5-mini, 7 scenarios, 1 seed)

This is the most useful report in the directory, because it is the one that failed.

| suite | score | pass rate | threshold | status |
|---|---|---|---|---|
| agent-deterministic | 1.00 | 100 % | 80 % | PASS |
| agent-llm | 0.50 | 43 % | 80 % | FAIL |

`root_cause_accuracy = 0.714`, `plan_accuracy = 0.429`, `mutation_violations = 0`,
`mean_llm_calls = 15.7`, `total_llm_tokens = 321 410`.

**In the last recorded real-model run the agent suite was weaker than the deterministic
baseline** — 43 % against 100 %. This is the newest `*-llm.md` report, and it predates the guards
described under "What changed after this run" below; it has not been re-run since, so treat the
table as a record of that code, not as the current standing of the model path. In that run:

| scenario | deterministic | gpt-5-mini | what the model did |
|---|---|---|---|
| `redis-connection-leak` | pass | pass | `restart_service(order-service)`, confidence 0.97, 9 model calls |
| `db-pool-exhaustion` | pass | pass | `restart_service(user-service)`, 12 calls |
| `memory-leak` | pass | pass | `restart_service(payment-service)`, 12 calls |
| `cascading-dependency` | pass | **fail** (0.40) | identified `inventory-service` at confidence 0.94, then exhausted the `remediate` phase without emitting a plan → escalated |
| `cpu-saturation` | pass | **fail** (0.10) | proposed `restart_service(notification-service)` — a known-incorrect remediation for a capacity problem; at that commit the fit guard keyed off the hypothesis category, which the model had labelled something other than `capacity` |
| `transient-spike` | pass | **fail** (0.00) | never concluded `no_action`: cycled `hypothesize → validate → investigate` ten times, 30 model calls, 84.9k tokens, top confidence 0.39, and ended up blaming `payment-service` |
| `network-latency` | pass | **fail** (0.00) | cycled `investigate → hypothesize` for ten phases (22 model calls) without escalating |

Variance between runs is large and matters more than any single number: the same suite scored
100 % on `20260910T122729Z` (2 cases), 62 % on `20260910T123130Z` (8 cases) and 43 % here.
`20260910T123130Z` passed `cascading-dependency`, `transient-spike` and `network-latency` and failed
`db-pool-exhaustion` and `memory-leak` — i.e. the failures move around between runs rather than being
stable per scenario, with two exceptions:

- `cpu-saturation` failed in both multi-scenario LLM runs, always by proposing a restart instead of
  a scale-up. This is the one reproducible model error.
- The two "correct answer is inaction" scenarios (`transient-spike`, `network-latency`) are where the
  model burns the most budget — 22–30 calls and 64–85k tokens versus 9–13 calls when it converges —
  because nothing in the evidence ever confirms a hypothesis and the phase budget is what finally
  stops it.

What does not vary: `mutation_violations = 0` in every LLM run recorded, and every model-proposed
remediation still went through policy and the target/fit guards. The structured-output suite
(`20260910T121405Z-llm.md`) is 3/3 at 4 145 tokens, so the failures above are reasoning failures,
not schema failures.

### What changed after this run

Each failure mode above was answered with a runtime guard rather than a prompt tweak, which is why
the offline runs are dated a day later. The `*-llm.md` reports have not been regenerated since, so
the effect on the model path is argued from the code, not measured:

| Failure in the LLM run | Guard now in place |
|---|---|
| `cpu-saturation`: restart proposed for a capacity problem | `_effective_category` (`src/aegis/remediation/planning.py`) reclassifies a CPU-bound target with no memory pressure and no leaked state as `capacity` whatever the hypothesis said, so `restart_service`/`rotate_connection_pool` are refused with "use `scale_service` instead" |
| `transient-spike`: never concluded `no_action`, 30 model calls | `AgentRuntime._observe` (`src/aegis/agent/runtime.py`) makes the recovery decision itself from `_signal_status` — every detection-signal condition inside its 45-second window mean, the accumulation guard, and a 120-second crash-loop guard — so the model cannot keep a recovered incident open. Visible in `20260911T062623Z`, where the deterministic run concludes in `triage` |
| `transient-spike` / `network-latency`: ten phases of cycling | `MAX_PHASE_ENTRIES = 2` in `src/aegis/workflows/incident_workflow.py` escalates on a third entry into the same phase instead of looping |
| `cascading-dependency`: exhausted `remediate` without a plan | `max_iterations: 4` on the `remediate` phase in all three flow packs |
| `memory-leak/rollback` passing verification | `ACCUMULATING_METRICS` in `src/aegis/verification/engine.py` (see `20260911T062623Z`) |

All four guards are visible in the authoritative sweep above, where the same seven scenarios plus
`cpu-saturation` all pass. Re-running with `--seeds 3` is the outstanding measurement: one seed per
scenario is enough to catch a broken guard, not enough to measure a prompt change.


### Honest caveats

- Thresholds are set per suite in the suite code, not centrally, and two of them
  (`hypothesis-ranking` 80 %, `agent` 80 %) are low enough to pass with a known failure.
- The agent suite runs one seed per scenario by default. With `--seeds 1` a single unlucky
  trajectory flips a case, which is exactly what the run-to-run variance above shows. Use
  `--seeds 3` or more before drawing conclusions about a prompt or guard change.
- `memory-recall` never exercises real OpenAI embeddings in the runner, only the hash and lexical
  providers.
- The offline suites share `build_world`, so a bug in the in-process simulator adapters would be
  invisible to all of them at once. `tests/integration`, `tests/workflow` and `scripts/e2e.py` are
  the counterweight.
- `--suite` has no `choices` validation and `all([])` is `True`, so a misspelled suite name runs
  nothing and exits 0. Check the report's suite table, not the exit code alone.
