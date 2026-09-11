# ADR-009: OpenAI Responses API with strict structured outputs, two model tiers, deterministic fallback

- Status: Accepted, implemented (2026-09-10)
- Code: `src/aegis/llm/provider_openai.py`, `provider_scripted.py`, `breaker.py`, `__init__.py`,
  `src/aegis/ports/llm.py`, `src/aegis/agent/schemas.py`, `src/aegis/agent/planner.py`,
  `src/aegis/config.py`

## Context

The runtime must never parse free text. Every model answer is a decision the runtime validates, so
it must arrive as a typed object or not at all. Model calls are the slowest and least reliable part
of the loop, and an incident must still progress when the model is down. Different steps need
different amounts of reasoning.

## Decision

- The `LLMProvider` port has one method that matters: `complete_structured(schema, system, user,
  tier)` returning a Pydantic instance plus usage. `OpenAIProvider` implements it with
  `client.responses.parse(text_format=schema)` (strict JSON schema), `store=False`,
  `prompt_cache_key` (the incident id, truncated to 64 characters) to reuse the stable system
  prompt, `max_output_tokens` (4000). For reasoning models (`gpt-5*`, `o1`, `o3`, `o4`) it sends
  `reasoning.effort`
  (`AEGIS_LLM_REASONING_EFFORT`, default `low`) and `text.verbosity=low`; otherwise
  `temperature=0.1`. `AEGIS_LLM_PROVIDER=openai_compatible` with `AEGIS_LLM_BASE_URL` reuses the
  same class for vLLM/Ollama-style endpoints.
- Two tiers: `reasoner` (`AEGIS_LLM_REASONER_MODEL`, default `gpt-5-mini`) for `AgentProposal`;
  `fast` (`AEGIS_LLM_FAST_MODEL`, default `gpt-5-nano`) for `TestJudgement` and `MemorySummary`.
  Embeddings use `text-embedding-3-small` at 1536 dimensions.
- Schemas are strict-friendly: no free-form dicts, so tool and remediation arguments travel as
  `arguments_json` strings and are parsed and validated by the runtime against the tool's own
  argument model. One repair attempt is made when the parse comes back empty; a second failure is
  `LLMMalformedOutput`.
- A circuit breaker (3 failures, 60 s reset, half-open single probe) turns transport errors into
  `LLMUnavailable` quickly instead of burning the incident's runtime budget on timeouts. The error
  taxonomy is three-way — `LLMUnavailable` (transport), `LLMError` (API status) and
  `LLMMalformedOutput` (schema) — and all three record a breaker failure; the SDK itself retries
  `llm_max_retries` (default 2) times before the breaker sees a failure at all.
- The `DeterministicPlanner` is a first-class fallback, not an error path: it has playbooks for
  exactly the five shipped phase names — `triage`, `investigate`, `hypothesize`, `validate`,
  `remediate` (triage queue, investigation queue along the dependency closure, evidence-derived
  hypotheses, category-specific validation and remediation) — and is used when the provider is
  disabled, unhealthy, fails mid-phase (`llm.fallback` event) or produces two invalid proposals in a
  row. Any other phase name falls through to `escalate` with "deterministic planner has no playbook
  for this phase". It is also the eval baseline (`agent-deterministic`).
- `ScriptedProvider` makes the whole runtime testable offline. `HashEmbeddingProvider` exists for the
  same reason but is never wired by the factory: `build_embedding_provider` returns `None` for
  `scripted` and `disabled`, so under `AEGIS_LLM_PROVIDER=scripted` the runtime has no embedding
  provider at all and memory degrades to lexical search; it is constructed only in tests and evals.

## Consequences

- The key is never in a compose file: `AEGIS_LLM_API_KEY_FILE` (compose mounts
  `./.secrets/openai_api_key` at `/run/secrets/openai_api_key`) or `AEGIS_LLM_API_KEY`; settings
  fail at startup if provider `openai` has no key.
- Token and latency accounting is uniform (`aegis_llm_*` metrics, per-step `tokens_in/out` and
  `latency_ms` on `agent_steps`).
- Reasoning tokens are not charged on top of the budget: usage adds `input_tokens + output_tokens`,
  and reasoning tokens are a sub-component of `output_tokens` tracked separately only for the metric
  (four token kinds are metered: input, output, cached_input, reasoning).
- Provider-specific: the Responses API is OpenAI's; compatible endpoints must support
  `responses.parse`. Adding another vendor means a new `LLMProvider` implementation.

## Alternatives considered

- Chat Completions with function calling: tool calls would be executed by the model's chosen
  schema rather than a single typed proposal; harder to keep "exactly one action per step".
- Multiple vendors from day one (the original plan had Gemini, OpenAI and local): one well-tested
  path beats three half-tested ones; the port keeps the door open.
- No fallback: an incident would stall whenever the model is unavailable, which is exactly when
  incidents happen.
