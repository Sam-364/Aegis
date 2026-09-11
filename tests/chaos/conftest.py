"""Chaos tests run against the live docker compose stack (make dev) and kill containers."""

from __future__ import annotations

import os
import subprocess

import httpx
import pytest

API = os.environ.get("AEGIS_E2E_API", "http://localhost:8600")


def compose(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", "compose", *args], check=False, capture_output=True, text=True)


@pytest.fixture(scope="session")
def api() -> httpx.Client:
    client = httpx.Client(base_url=API, timeout=30)
    try:
        if client.get("/ready").status_code != 200:
            pytest.skip("compose stack is not ready")
    except httpx.HTTPError:
        pytest.skip("compose stack is not running")
    return client
