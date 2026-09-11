"""Kill the worker in the middle of an incident: the workflow must resume and remediation must
execute exactly once."""

from __future__ import annotations

import json
import time

import httpx
import pytest

from tests.chaos.conftest import compose

pytestmark = [pytest.mark.chaos, pytest.mark.timeout(1200)]


def wait_quiet(api: httpx.Client) -> None:
    """Wait until nothing is in flight, closing anything already handed to humans."""
    deadline = time.time() + 420
    while time.time() < deadline:
        page = api.get("/api/v1/incidents", params={"active": "true"}).json()
        for item in page["items"]:
            if item["status"] in ("escalated", "failed"):
                api.post(
                    f"/api/v1/incidents/{item['id']}/close",
                    json={"reason": "chaos test: handed over"},
                )
        page = api.get("/api/v1/incidents", params={"active": "true"}).json()
        faults = api.get("/api/v1/simulation/faults").json()
        if page["total"] == 0 and not faults:
            return
        time.sleep(5)
    pytest.fail("stack did not become quiet")


def find_new_incident(api: httpx.Client, known: set[str], timeout: float = 240) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        for i in api.get("/api/v1/incidents", params={"limit": 200}).json()["items"]:
            if i["id"] not in known:
                return i
        time.sleep(3)
    pytest.fail("incident was not detected")


def test_worker_crash_during_investigation_resumes_and_executes_once(api: httpx.Client) -> None:
    wait_quiet(api)
    known = {i["id"] for i in api.get("/api/v1/incidents", params={"limit": 200}).json()["items"]}
    api.post(
        "/api/v1/simulation/faults", json={"scenario_id": "cascading-dependency"}
    ).raise_for_status()
    incident = find_new_incident(api, known)
    # wait until the agent is actually working
    deadline = time.time() + 180
    while time.time() < deadline:
        runs = api.get(f"/api/v1/incidents/{incident['id']}/agent-runs").json()
        if runs:
            break
        time.sleep(2)
    assert runs, "agent never started"
    steps_before = sum(1 for r in runs)
    # crash the worker hard
    assert compose("kill", "-s", "SIGKILL", "worker").returncode == 0
    time.sleep(5)
    assert compose("up", "-d", "worker").returncode == 0
    # follow the incident; approve when asked
    approvals_done = 0
    deadline = time.time() + 900
    status = None
    while time.time() < deadline:
        current = api.get(f"/api/v1/incidents/{incident['id']}").json()["incident"]
        status = current["status"]
        if status == "awaiting_approval":
            for a in api.get("/api/v1/approvals", params={"status": "pending"}).json():
                if a["incident_id"] == incident["id"]:
                    api.post(
                        f"/api/v1/approvals/{a['id']}/approve", json={"reason": "chaos test"}
                    ).raise_for_status()
                    approvals_done += 1
        if status in ("resolved", "escalated", "failed", "closed"):
            break
        time.sleep(3)
    assert status == "resolved", status
    detail = api.get(f"/api/v1/incidents/{incident['id']}").json()
    executed = [p for p in detail["action_plans"] if p["status"] in ("executed", "verified")]
    assert len(executed) == 1 and executed[0]["tool_name"] == "restart_service"
    assert executed[0]["arguments"]["service"] == "inventory-service"
    # exactly one successful mutating tool execution, even though the worker was killed
    mutations = [
        t
        for t in detail["tool_executions"]
        if t["category"] == "mutating" and t["status"] == "succeeded"
    ]
    assert len(mutations) == 1, json.dumps(
        [(t["tool_name"], t["status"]) for t in detail["tool_executions"]]
    )
    actions = api.get("/api/v1/simulation/actions").json()
    restarts = [
        a
        for a in actions
        if a.get("action") == "restart" and a.get("target") == "inventory-service"
    ]
    assert len(restarts) == 1, restarts
    assert len(detail["agent_runs"]) >= steps_before
    assert api.get("/api/v1/simulation/faults").json() == []


def test_api_restart_does_not_lose_incident_state(api: httpx.Client) -> None:
    before = api.get("/api/v1/incidents", params={"limit": 200}).json()
    assert compose("restart", "api").returncode == 0
    deadline = time.time() + 120
    while time.time() < deadline:
        try:
            if httpx.get(f"{api.base_url}/ready", timeout=5).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(3)
    after = api.get("/api/v1/incidents", params={"limit": 200}).json()
    assert after["total"] == before["total"]
    assert {i["id"] for i in after["items"]} == {i["id"] for i in before["items"]}
