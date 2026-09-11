# syntax=docker/dockerfile:1.7
# Multi-stage image for every Aegis Python process (api, worker, detector, simulator).
FROM python:3.12-slim-bookworm AS base
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv PATH="/opt/venv/bin:$PATH"
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.9.12 /uv /uvx /bin/

FROM base AS builder
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-install-project --no-dev
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev

FROM base AS runtime
RUN groupadd --gid 1000 aegis && useradd --uid 1000 --gid aegis --home /app --shell /usr/sbin/nologin aegis
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY --chown=aegis:aegis src ./src
COPY --chown=aegis:aegis flows ./flows
COPY --chown=aegis:aegis policies ./policies
COPY --chown=aegis:aegis migrations ./migrations
COPY --chown=aegis:aegis alembic.ini pyproject.toml ./
USER aegis
ENV AEGIS_FLOWS_DIR=/app/flows AEGIS_POLICIES_DIR=/app/policies
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
    CMD curl -fsS "http://localhost:${AEGIS_API_PORT:-8600}/health" || exit 1
CMD ["aegis-api"]
