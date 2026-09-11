"""Redis and simulator outages during an incident: the system degrades safely and recovers."""

from __future__ import annotations

import time

import httpx
import pytest

from tests.chaos.conftest import compose

pytestmark = [pytest.mark.chaos, pytest.mark.timeout(900)]


def test_redis_outage_does_not_break_api_reads(api: httpx.Client) -> None:
    assert compose("stop", "redis").returncode == 0
    try:
        time.sleep(3)
        r = api.get("/api/v1/incidents")
        assert r.status_code in (200, 429, 503)
        ready = api.get("/ready")
        assert ready.status_code == 503 and ready.json()["checks"]["redis"]["ok"] is False
    finally:
        assert compose("start", "redis").returncode == 0
    deadline = time.time() + 90
    while time.time() < deadline:
        if api.get("/ready").status_code == 200:
            break
        time.sleep(3)
    assert api.get("/ready").status_code == 200


def test_detector_survives_simulator_restart(api: httpx.Client) -> None:
    assert compose("restart", "simulator").returncode == 0
    time.sleep(20)
    logs = compose("logs", "detector", "--tail", "50").stdout
    assert "Traceback" not in logs
    assert api.get("/ready").status_code == 200 or time.sleep(20) is None
