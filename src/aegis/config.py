"""Environment-driven configuration, validated at startup.

Secrets are ``SecretStr`` and are never rendered by ``repr``/logs. The LLM key may be supplied
directly (``AEGIS_LLM_API_KEY``) or via a file path (``AEGIS_LLM_API_KEY_FILE``) so that the key
never needs to be written into a compose file or the repository.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from aegis.domain.enums import Environment, Role


class ApiKeyPrincipal:
    """A static API key mapped to a role. Parsed from ``AEGIS_API_KEYS``."""

    __slots__ = ("key", "name", "role")

    def __init__(self, key: str, role: Role, name: str) -> None:
        self.key = key
        self.role = role
        self.name = name

    def __repr__(self) -> str:  # never expose the key
        return f"ApiKeyPrincipal(name={self.name!r}, role={self.role.value!r})"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AEGIS_", env_file=(".env",), env_file_encoding="utf-8", extra="ignore"
    )

    # --- identity -----------------------------------------------------------------------------
    environment: Environment = Environment.DEVELOPMENT
    service_name: str = "aegis"
    tenant_id: str = "default"
    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "json"

    # --- persistence --------------------------------------------------------------------------
    database_url: str = "postgresql+asyncpg://aegis:aegis@localhost:5432/aegis"
    database_pool_size: int = Field(default=10, ge=1, le=100)
    database_max_overflow: int = Field(default=10, ge=0, le=100)
    database_echo: bool = False
    redis_url: str = "redis://localhost:6379/0"

    # --- temporal -----------------------------------------------------------------------------
    temporal_address: str = "localhost:7233"
    temporal_namespace: str = "default"
    temporal_task_queue: str = "aegis-incidents"
    temporal_enabled: bool = True

    # --- telemetry sources --------------------------------------------------------------------
    simulator_url: str = "http://localhost:8601"
    telemetry_provider: Literal["simulator", "prometheus"] = "simulator"
    prometheus_url: str = "http://localhost:9090"

    # --- llm ----------------------------------------------------------------------------------
    llm_provider: Literal["openai", "openai_compatible", "scripted", "disabled"] = "openai"
    llm_api_key: SecretStr | None = None
    llm_api_key_file: Path | None = None
    llm_base_url: str | None = None
    llm_reasoner_model: str = "gpt-5-mini"
    llm_fast_model: str = "gpt-5-nano"
    llm_embedding_model: str = "text-embedding-3-small"
    llm_embedding_dimensions: int = 1536
    llm_reasoning_effort: Literal["minimal", "low", "medium", "high"] = "low"
    llm_timeout_seconds: float = Field(default=60.0, gt=0)
    llm_max_retries: int = Field(default=2, ge=0, le=5)
    llm_max_output_tokens: int = Field(default=4000, ge=256)
    llm_circuit_breaker_failures: int = Field(default=3, ge=1)
    llm_circuit_breaker_reset_seconds: float = Field(default=60.0, gt=0)

    # --- observability ------------------------------------------------------------------------
    otel_enabled: bool = False
    otel_exporter_endpoint: str = "http://localhost:4317"
    metrics_enabled: bool = True
    metrics_port: int = Field(default=9464, ge=1024, le=65535)

    # --- api ----------------------------------------------------------------------------------
    api_host: str = "0.0.0.0"  # noqa: S104 - container binding
    api_port: int = 8600
    api_cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3600"])
    api_auth_mode: Literal["disabled", "api_key"] = "disabled"
    api_keys: str = ""  # "key:role:name,key2:role2:name2"
    api_rate_limit_per_minute: int = Field(default=600, ge=1)
    api_public_url: str = "http://localhost:8600"

    # --- detection ----------------------------------------------------------------------------
    detection_enabled: bool = True
    detection_interval_seconds: float = Field(default=5.0, ge=0.5)
    detection_window_seconds: int = Field(default=600, ge=30)
    detection_warmup_samples: int = Field(default=12, ge=3)
    detection_zscore_threshold: float = Field(default=3.0, gt=0)
    detection_ewma_alpha: float = Field(default=0.2, gt=0, lt=1)
    correlation_window_seconds: int = Field(default=180, ge=10)

    # --- runtime ------------------------------------------------------------------------------
    approval_timeout_seconds: int = Field(default=900, ge=30)
    verification_poll_seconds: float = Field(default=5.0, ge=0.5)
    flows_dir: Path = Path("flows")
    policies_dir: Path = Path("policies")
    agent_heartbeat_seconds: float = Field(default=10.0, gt=0)
    agent_phase_timeout_seconds: int = Field(default=600, ge=30)

    # --- simulator process --------------------------------------------------------------------
    simulator_port: int = 8601
    simulator_seed: int = 42
    simulator_tick_seconds: float = Field(default=1.0, gt=0)

    @field_validator("log_level")
    @classmethod
    def _upper(cls, value: str) -> str:
        value = value.upper()
        if value not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError(f"invalid log level {value}")
        return value

    @model_validator(mode="after")
    def _load_key_file(self) -> Settings:
        if self.llm_api_key is not None and not self.llm_api_key.get_secret_value().strip():
            self.llm_api_key = None  # empty env var means "not provided"
        if self.llm_api_key is None and self.llm_api_key_file is not None:
            path = self.llm_api_key_file.expanduser()
            if path.exists():
                self.llm_api_key = SecretStr(path.read_text(encoding="utf-8").strip())
        if self.llm_provider == "openai" and self.llm_api_key is None:
            raise ValueError(
                "AEGIS_LLM_PROVIDER=openai requires AEGIS_LLM_API_KEY or AEGIS_LLM_API_KEY_FILE"
            )
        if (
            self.environment in (Environment.PRODUCTION, Environment.STAGING)
            and self.api_auth_mode == "disabled"
        ):
            # With auth disabled every caller is an admin, which means anyone who can reach the
            # port can approve a remediation. That is only tolerable on a development machine.
            raise ValueError(
                f"API authentication cannot be disabled in {self.environment.value}; "
                "set AEGIS_API_AUTH_MODE=api_key and AEGIS_API_KEYS"
            )
        return self

    @property
    def sync_database_url(self) -> str:
        """psycopg URL for Alembic and the LangGraph checkpointer."""
        return self.database_url.replace("postgresql+asyncpg://", "postgresql://")

    @property
    def api_key_principals(self) -> list[ApiKeyPrincipal]:
        principals: list[ApiKeyPrincipal] = []
        for raw in filter(None, (p.strip() for p in self.api_keys.split(","))):
            parts = raw.split(":")
            if len(parts) < 2:
                raise ValueError("AEGIS_API_KEYS entries must be key:role[:name]")
            key, role = parts[0], Role(parts[1])
            name = parts[2] if len(parts) > 2 else role.value
            principals.append(ApiKeyPrincipal(key, role, name))
        return principals

    @property
    def is_production(self) -> bool:
        return self.environment is Environment.PRODUCTION


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
