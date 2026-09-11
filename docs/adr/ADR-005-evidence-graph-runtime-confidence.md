# ADR-005: Evidence is a typed graph; hypothesis confidence is computed by the runtime

- Status: Accepted, implemented (2026-09-10)
- Code: `src/aegis/domain/evidence.py`, `src/aegis/domain/hypothesis.py`,
  `src/aegis/evidence/service.py`, `src/aegis/hypotheses/engine.py`

## Context

The original plan said confidence is "calculated from evidence" without saying how. A number the
model asserts about its own hypothesis is not auditable, cannot be compared across incidents, and is
trivially gamed by a model that wants to reach remediation. Operators need to see *why* a hypothesis
is believed, and the remediation guard needs a threshold it can trust.

## Decision

- Every tool result becomes one or more `Evidence` rows (kind, source tool, service, title,
  summary, structured `data`, a deterministic `strength` in [0, 1] from the tool's own heuristics,
  tags). The strengths are computed inline in the tool bodies (`src/aegis/tools/builtin/*.py`);
  `src/aegis/tools/strength.py` holds four shared helpers (`ratio_strength`, `recency_strength`,
  `share_strength`, `within`) that a handful of them use. Relations (`supports`, `contradicts`,
  `caused_by`, `depends_on`, `correlated_with`, `observed_on`, `resolved_by`) form an
  `EvidenceGraph` per incident, exposed at `GET /api/v1/incidents/{id}/evidence`.
- The model sees a compact digest with stable handles (`E1..En`, `H1..Hn`) and may only reference
  those handles. `HypothesisEngine.accept` drops unknown handles, rejects a proposal with no valid
  support, and rejects a root-cause service that is not in the topology.
- `HypothesisEngine.score` computes confidence from five components with fixed weights (evidence
  0.45, temporal 0.15, dependency 0.25, historical 0.15, and a contradiction weight of 0.50 that is
  subtracted, plus the validation term `min(0.3, confirmed × 0.15) − min(0.6, refuted × 0.18)`, so
  tests can add at most +0.3 and subtract at most −0.6) and stores the breakdown and a textual
  explanation on the hypothesis (`HypothesisScore`). `evidence_strength` is not a plain noisy-OR but
  `min(0.95, 0.6 × max(w) + 0.4 × noisy_or(w))` over the supporting strengths, where evidence about a
  service other than the suspected root counts at half weight; a `TRANSIENT` hypothesis also gets a
  `dependency` floor of 0.6. The formula and rules are documented in `docs/architecture/overview.md`.
- Two structural rules encode SRE knowledge that models get wrong: **attribution** (a saturated
  shared resource is a symptom; the client holding the connections is the cause) and **propagation**
  (a service whose own error rate is low and whose errors come from a dependency is a symptom; the
  dependency is the cause). They add support to the right hypothesis and contradictions to the wrong
  one automatically.
- Validation tests are judged twice: the `fast` model reads the diagnostic, then `judge_test`
  downgrades `confirmed` to `inconclusive` unless the evidence actually implicates the suspected
  service with strength ≥ 0.6, and downgrades `refuted` to `inconclusive` when the diagnostic
  produced no evidence at all.
- Hypothesis status (`proposed`/`testing`/`supported`/`confirmed`/`refuted`, plus `abandoned`, which
  `_status_for` treats as sticky and terminal) is derived from the score and tests: `supported` at
  confidence ≥ 0.55, `confirmed` only with ≥ 1 confirmed test *and* confidence ≥ 0.65. A single
  refuted test no longer refutes a hypothesis — refutation needs two refuted tests outnumbering the
  confirmations, or a refuted test with confidence below 0.35. The remediation gate is two
  conditions: status in (`confirmed`, `supported`) *and* confidence ≥ `min_remediation_confidence`
  (default 0.55). `validate_remediation_target` refuses plans that act on a symptomatic caller, and
  `validate_remediation_fit`/`_effective_category` (`src/aegis/remediation/planning.py`) let the
  evidence override a category the model mislabelled: a CPU-bound target with no memory pressure and
  no leaked state is treated as `capacity`, so a restart is refused and `scale_service` required.

## Consequences

- Confidence is reproducible: the same evidence yields the same score, which is what
  `evals/suites/hypotheses.py` measures (MRR of the true root cause among one candidate per
  topology node).
- The deterministic planner can propose hypotheses from strong evidence
  (`deterministic_hypotheses`) and be scored identically, which gives the LLM a baseline to beat.
- Weights are opinionated constants; changing them changes every incident's confidence. They are
  centralized in `ScoreWeights` and covered by unit tests.
- Memory matches enter the graph as `memory` evidence with capped strength (≤ 0.6), so history can
  raise a hypothesis but not confirm it.

## Alternatives considered

- Model-asserted confidence: unauditable, incomparable, gameable.
- A transcript-based agent that reasons over chat history: no structure for the console, no
  handles to validate references against, unbounded prompt growth.
- A probabilistic causal model over the topology: attractive later; today the two structural rules
  cover the failure classes the simulator produces and remain explainable line by line.
