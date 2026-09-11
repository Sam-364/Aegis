"""Shared fixtures."""

from __future__ import annotations

import os

import pytest

# Tests never need a real key; the settings validator requires one for provider=openai.
os.environ.setdefault("AEGIS_LLM_PROVIDER", "scripted")
os.environ.setdefault("AEGIS_TEMPORAL_ENABLED", "false")
os.environ.setdefault("AEGIS_OTEL_ENABLED", "false")


@pytest.fixture(autouse=True)
def _reset_settings() -> None:
    from aegis.config import reset_settings_cache

    reset_settings_cache()
