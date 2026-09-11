# ADR-008: One Python distribution with import-linter layering instead of many packages

- Status: Accepted, implemented (2026-09-10)
- Code: `pyproject.toml` (`[tool.importlinter]`), `src/aegis/`, `Dockerfile`, `Makefile`

## Context

The original plan called for a `packages/*` monorepo with ten Python distributions. Boundaries
between domain, ports, runtime core, infrastructure and delivery are valuable; ten wheels are not.
They multiply lockfiles, typing configuration, Docker layers and release steps, and the boundaries
they enforce can be enforced by static analysis instead.

## Decision

- Ship one distribution, `aegis` (hatchling, `packages = ["src/aegis"]`), with five console scripts
  (`aegis-api`, `aegis-worker`, `aegis-detector`, `aegis-simulator`, `aegis`) and one multi-stage
  Python image that the api, worker, detector, simulator and migrate services all run from. The
  console is the one other image (`apps/web/Dockerfile`), so the stack builds two.
- Enforce layering with five import-linter contracts that run in `make lint`. All five are
  `type = "forbidden"` deny-lists — there is no layers or independence contract — so each forbids
  exactly the modules it names and nothing more:
  1. "Domain is pure": `aegis.domain` may not import the ports, runtime, delivery or infrastructure
     packages. The list omits `aegis.config`, `aegis.logging` and `aegis.telemetry`, so it does not
     in fact enforce "domain imports no other Aegis package"; and because `aegis.config` already
     imports `aegis.domain.enums`, a `domain → config` import would create a cycle the linter cannot
     see.
  2. "Ports depend only on domain": the same deny-list for `aegis.ports`, with the same hole for the
     three shared leaves.
  3. "Core runtime never imports delivery or infrastructure layers": the runtime core
     (`flow, policy, tools, evidence, hypotheses, detection, verification, remediation, memory, llm,
     agent`) never imports `infrastructure`, `application`, `api`, `workflows`, `apps`.
  4. "Simulator is independent of the Aegis runtime": narrower than "imports none of the runtime" —
     its forbidden list omits `aegis.domain`, `aegis.ports`, `aegis.apps` and the shared leaves,
     though the current code imports none of them.
  5. "Application layer does not import delivery layers": `application` and `infrastructure` never
     import `api` or `apps`.

  No contract constrains `aegis.api`, `aegis.apps` or `aegis.workflows` as a source, so
  `aegis.workflows` may import anything.
- Dependencies are pinned with `uv` (`uv.lock`, `uv sync --frozen` in the image); the toolchain is
  ruff (lint + format), mypy strict with the pydantic plugin, and pytest. The declared markers are
  `integration, workflow, e2e, chaos, llm`; there is no `unit` or `security` marker, and `make test`
  selects those two tiers by path (`tests/unit tests/security`). The `integration` marker's docstring
  in `pyproject.toml` still mentions a `docker compose --profile test` profile that does not exist —
  that infrastructure comes from `make test-infra` (Postgres `:5439`, Redis `:6389`).

## Consequences

- One `uv sync`, one image build, one version. New modules are placed by layer and the linter tells
  you when a dependency points the wrong way.
- The simulator ships in the same wheel and image (`aegis-simulator`) even though it is logically a
  separate system; contract 4 keeps it from importing the runtime modules it lists.
- Shared leaves (`aegis.config`, `aegis.logging`, `aegis.telemetry`) are outside the contracts and
  may be imported from anywhere; keep them free of domain logic.
- A future split into distributions remains possible because the boundaries already exist.

## Alternatives considered

- Ten distributions as planned: build, typing and Docker overhead with no runtime benefit; version
  skew between wheels becomes a new failure class.
- No enforced boundaries: layering erodes within weeks under agentic and human editing alike.
