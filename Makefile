.DEFAULT_GOAL := help
SHELL := /bin/bash
UV ?= uv
COMPOSE ?= docker compose

help: ## Show targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

setup: ## Install Python and web dependencies
	$(UV) sync --extra dev
	cd apps/web && npm install

dev: ## Start the full local stack
	$(COMPOSE) up --build

observability: ## Start the stack with Prometheus, Grafana, Tempo, Loki
	$(COMPOSE) --profile observability up --build

down: ## Stop the stack (keeps volumes)
	$(COMPOSE) --profile observability down

reset: ## Stop the stack and delete volumes
	$(COMPOSE) --profile observability down -v

lint: ## Ruff + import-linter
	$(UV) run ruff check src tests evals
	$(UV) run ruff format --check src tests evals
	$(UV) run lint-imports

typecheck: ## mypy strict
	$(UV) run mypy src/aegis

format: ## Format code
	$(UV) run ruff format src tests evals
	$(UV) run ruff check --fix src tests evals

test: ## Unit + security tests (no infrastructure)
	$(UV) run pytest tests/unit tests/security -q

test-infra: ## Start throwaway Postgres/Redis for integration tests
	docker start aegis-test-postgres 2>/dev/null || docker run -d --name aegis-test-postgres -e POSTGRES_USER=aegis -e POSTGRES_PASSWORD=aegis -e POSTGRES_DB=aegis_test -p 5439:5432 pgvector/pgvector:pg17
	docker start aegis-test-redis 2>/dev/null || docker run -d --name aegis-test-redis -p 6389:6379 redis:7-alpine

test-integration: ## Postgres/Redis integration tests (needs test-infra)
	$(UV) run pytest tests/integration -q

test-workflow: ## Temporal workflow tests (time-skipping test server)
	$(UV) run pytest tests/workflow -q

test-all: lint typecheck test test-integration test-workflow ## Everything that runs without the compose stack

migrate: ## Apply migrations to the configured database
	$(UV) run aegis migrate

e2e: ## End-to-end scenarios against the running compose stack
	$(UV) run python scripts/e2e.py

chaos: ## Chaos tests against the running compose stack
	$(UV) run pytest tests/chaos -q

evals: ## Run component and agent evals (LLM evals cost money)
	$(UV) run python -m evals.run $(ARGS)

simulation: ## Inject a scenario (SCENARIO=redis-connection-leak)
	$(UV) run aegis inject $(SCENARIO)

openapi: ## Export the OpenAPI document
	$(UV) run python scripts/export_openapi.py

web-dev: ## Run the console in dev mode
	cd apps/web && npm run dev
