# ADR-002: PostgreSQL with pgvector as the system of record, stored as document + projections

- Status: Accepted, implemented (2026-09-10)
- Code: `src/aegis/infrastructure/postgres/models.py`, `repositories.py`, `session.py`,
  `migrations/versions/0001_initial.py`, `docker-compose.yml`

## Context

Aegis persists many small aggregates with rich nested shapes (incidents with signals, hypotheses with
score breakdowns, plans with verification specs, tool executions with a 14-check trace), needs
exactly-once guarantees for mutations, an immutable audit trail, and similarity search over past
incidents. The host is RAM-constrained, and Temporal also needs a database.

## Decision

- One PostgreSQL 17 instance (`pgvector/pgvector:pg17`) hosts the `aegis` database, Temporal's
  `temporal` and `temporal_visibility` databases (created by
  `infrastructure/postgres/init/01-temporal-databases.sh`), and LangGraph's checkpoint tables.
- Every aggregate table stores the full Pydantic document as JSONB in `document` and duplicates the
  fields needed for querying and constraints into typed projection columns. The set differs per
  table: `status` is widespread, `severity` and `detected_at` are on `incidents` only, `seq` only on
  `incident_events` and `agent_steps`, `idempotency_key` on `tool_executions`, `action_executions`
  and `action_plans`, and `version` means two different things (`incidents.version` is the integer
  optimistic lock, `definitions.version` is a version string). Repositories serialize with
  `model_dump(mode="json")` and rehydrate with `model_validate`, so the domain model is the schema.
- Correctness lives in constraints declared in `models.py`, not conventions:
  `tool_executions.idempotency_key` and `action_executions.idempotency_key` are unique (the
  exactly-once claim; the third `idempotency_key`, on `action_plans`, is not unique),
  `incident_events(incident_id, seq)` and `agent_steps(agent_run_id, seq)` are unique,
  `incidents.version` implements optimistic
  locking (`UPDATE ... WHERE version = :expected`, `ConcurrencyError` on zero rows), and
  `audit_events` has a `BEFORE UPDATE OR DELETE` trigger that raises `audit_events is append-only`.
- `incident_memories.embedding` is a `vector(1536)` column with an HNSW cosine index; the dimension
  is hard-coded as `EMBEDDING_DIMENSIONS` in the table while the provider's dimension is
  settings-driven, and nothing asserts that the two agree. The lexical fallback runs whenever the
  vector search comes back empty — no embedding provider, an `LLMError` while embedding, or all-NULL
  embeddings — but it is `ILIKE '%term%'` over `embedding_text`, which cannot use the GIN
  `to_tsvector` index that also exists: the fallback is a sequential scan capped at `LIMIT 200`, and
  that index is currently unused.
- Human-facing numbering comes from the `incident_number_seq` sequence starting at 1042.
- Schema management is Alembic (`aegis migrate` → `upgrade head`). The initial revision creates the
  tables with `Base.metadata.create_all`, so every table, column and constraint is declared in
  `models.py` and only four things are literal SQL in the migration: `CREATE EXTENSION vector`, the
  audit function and its trigger, the HNSW index and the GIN index. `/ready` compares the database
  revision with the script head — so a process with a stale schema reports not ready — and also gates
  on Redis ping, Temporal health and simulator health.
- Redis holds only derived or transient state (pub/sub fan-out, leader lock, rate limit counters,
  detector baselines) and can be lost without data loss.

## Consequences

- Adding a field to a domain model needs no migration unless it must be indexed or constrained.
- Queries that filter on document-only fields (for example `affected_services`) use JSONB operators
  (`find_open_by_correlation` uses `?|`); heavy analytics belong in projections or a warehouse.
- The in-memory repositories (`src/aegis/infrastructure/memory/repositories.py`) approximate the
  same semantics (unique keys, seq allocation, optimistic locking), which is what lets unit tests and
  evals run without Postgres — but they are not identical: `InMemoryAgentStepRepository.add_step`
  enforces no `(agent_run_id, seq)` uniqueness, event `seq` is `len(events) + 1` rather than
  `max(seq) + 1` with the Postgres repository's five-attempt retry, and incident numbering starts at
  1001 instead of the sequence's 1042.
- One instance for three concerns is right for a laptop and wrong for production; see
  `docs/operations/deployment.md` for the managed-service recommendation.
- The `definitions` snapshot table exists in the schema but is not yet written by any code.

## Alternatives considered

- Fully normalized relational schema: many joins to rebuild one aggregate, migrations for every
  model change, no gain for a system whose reads are "load the aggregate".
- A document database: loses the constraint-based guarantees (unique idempotency keys, triggers,
  transactional unit of work with Temporal-safe activities) that the design depends on.
- Separate Postgres for Temporal: recommended for production, but doubles memory locally for no
  functional benefit.
