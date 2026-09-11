"""End-to-end harness against the running docker compose stack.

For each scenario: inject via the API, wait for detection, follow the workflow, approve when asked,
and assert the ground-truth outcome (resolved with the right remediation, or no action / escalation).

    uv run python scripts/e2e.py                       # all five demo scenarios
    uv run python scripts/e2e.py --scenario bad-deployment --timeout 900
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from typing import Any

import httpx

API = "http://localhost:8600"
DEMO = [
    "redis-connection-leak",
    "bad-deployment",
    "db-pool-exhaustion",
    "cascading-dependency",
    "transient-spike",
]

EXPECTED_TOOLS = {
    "redis-connection-leak": ({"restart_service", "rotate_connection_pool"}, "order-service"),
    "bad-deployment": ({"rollback_deployment"}, "payment-service"),
    "db-pool-exhaustion": ({"rotate_connection_pool", "restart_service"}, "user-service"),
    "cascading-dependency": ({"restart_service"}, "inventory-service"),
    "memory-leak": ({"restart_service"}, "payment-service"),
    "cpu-saturation": ({"scale_service"}, "notification-service"),
}


class E2E:
    def __init__(self, api: str, key: str | None, timeout: float) -> None:
        headers = {"X-Aegis-Key": key} if key else {}
        self.client = httpx.AsyncClient(base_url=api, headers=headers, timeout=30)
        self.timeout = timeout

    async def close(self) -> None:
        await self.client.aclose()

    async def get(self, path: str, **params: Any) -> Any:
        r = await self.client.get(path, params=params)
        r.raise_for_status()
        return r.json()

    async def post(self, path: str, body: dict[str, Any] | None = None) -> Any:
        r = await self.client.post(path, json=body or {})
        r.raise_for_status()
        return r.json()

    async def wait_for_memory(self, incident_id: str, wait_seconds: float = 90) -> bool:
        """Wait for the incident's memory row, which finalize writes after the status change."""
        deadline = time.time() + wait_seconds
        while time.time() < deadline:
            page = await self.get("/api/v1/memory", limit=200)
            if any(m["incident_id"] == incident_id for m in page["items"]):
                return True
            await asyncio.sleep(3)
        return False

    async def delete(self, path: str) -> Any:
        r = await self.client.delete(path)
        r.raise_for_status()
        return r.json()

    async def wait_ready(self) -> None:
        deadline = time.time() + 120
        while time.time() < deadline:
            try:
                r = await self.client.get("/ready")
                if r.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(3)
        raise SystemExit("API never became ready")

    async def wait_quiet(self) -> None:
        """Wait until nothing is in flight before the next scenario.

        An escalated incident is one Aegis handed to a human, so the harness plays that human: it
        closes the incident and clears the injected fault, which is what an operator fixing the
        box by hand amounts to. A fault left behind would contaminate the next scenario.
        """
        deadline = time.time() + 420
        while time.time() < deadline:
            page = await self.get("/api/v1/incidents", active="true")
            handed_over = [i for i in page["items"] if i["status"] in ("escalated", "failed")]
            for item in handed_over:
                await self.post(
                    f"/api/v1/incidents/{item['id']}/close",
                    {"reason": "e2e harness: handed over"},
                )
            if handed_over:
                for fault in await self.get("/api/v1/simulation/faults"):
                    await self.delete(f"/api/v1/simulation/faults/{fault['id']}")
                print(
                    f"[harness] closed {len(handed_over)} escalated incident(s) and cleared the "
                    "injected fault, as an operator taking over would",
                    flush=True,
                )
            page = await self.get("/api/v1/incidents", active="true")
            if page["total"] == 0 and not await self.get("/api/v1/simulation/faults"):
                return
            await asyncio.sleep(5)
        raise SystemExit("stack did not become quiet")

    async def run_scenario(self, scenario: str) -> dict[str, Any]:
        started = time.time()
        known = {i["id"] for i in (await self.get("/api/v1/incidents", limit=200))["items"]}
        fault = await self.post("/api/v1/simulation/faults", {"scenario_id": scenario})
        print(f"[{scenario}] injected fault {fault['id']}", flush=True)
        incident = None
        deadline = time.time() + self.timeout
        approvals_done = 0
        while time.time() < deadline:
            page = await self.get("/api/v1/incidents", limit=200)
            fresh = [i for i in page["items"] if i["id"] not in known]
            if fresh and incident is None:
                incident = fresh[0]
                print(
                    f"[{scenario}] detected {incident['display_id']} {incident['severity']} '{incident['title']}' "
                    f"after {time.time() - started:.0f}s",
                    flush=True,
                )
            if incident is None:
                await asyncio.sleep(3)
                continue
            current = (await self.get(f"/api/v1/incidents/{incident['id']}"))["incident"]
            status = current["status"]
            if status == "awaiting_approval":
                for approval in await self.get("/api/v1/approvals", status="pending"):
                    if approval["incident_id"] == incident["id"]:
                        await self.post(
                            f"/api/v1/approvals/{approval['id']}/approve", {"reason": "e2e harness"}
                        )
                        approvals_done += 1
                        print(f"[{scenario}] approved {approval['title']}", flush=True)
            if status in ("resolved", "escalated", "failed", "closed"):
                break
            await asyncio.sleep(3)
        if incident is None:
            return {"scenario": scenario, "ok": False, "reason": "not detected"}
        # The incident's memory is written after the status change (it needs an LLM summary), so
        # reading it straight away reports "no memory" for a run that does store one.
        memory_stored = await self.wait_for_memory(incident["id"])
        detail = await self.get(f"/api/v1/incidents/{incident['id']}")
        inc = detail["incident"]
        plans = detail["action_plans"]
        faults = await self.get("/api/v1/simulation/faults")
        expected_tools, expected_target = EXPECTED_TOOLS.get(scenario, (set(), None))
        executed = [
            p
            for p in plans
            if p["status"] in ("executed", "verified", "verification_failed", "rolled_back")
        ]
        verified = [p for p in plans if p["status"] == "verified"]
        result: dict[str, Any] = {
            "scenario": scenario,
            "incident": f"INC-{inc['number']}",
            "status": inc["status"],
            "duration_s": round(time.time() - started),
            "approvals": approvals_done,
            "plans": [(p["tool_name"], p["arguments"], p["status"]) for p in plans],
            "faults_remaining": [f["scenario_id"] for f in faults],
            "events": len(
                await self.get(f"/api/v1/incidents/{incident['id']}/timeline", limit=2000)
            ),
            "memory": memory_stored,
        }
        if scenario == "transient-spike":
            result["ok"] = inc["status"] == "resolved" and not plans
        elif scenario == "network-latency":
            result["ok"] = inc["status"] == "escalated" and not executed
        else:
            right = (
                bool(verified)
                and verified[-1]["tool_name"] in expected_tools
                and verified[-1]["arguments"].get("service") == expected_target
            )
            result["ok"] = (
                inc["status"] == "resolved"
                and right
                and not faults
                and len(executed)
                == len(
                    {(p["tool_name"], json.dumps(p["arguments"], sort_keys=True)) for p in executed}
                )
            )
        print(
            f"[{scenario}] {'OK' if result['ok'] else 'FAIL'} status={inc['status']} plans={result['plans']}",
            flush=True,
        )
        return result


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default=API)
    parser.add_argument("--key", default=None)
    parser.add_argument("--scenario", action="append")
    parser.add_argument("--timeout", type=float, default=900)
    args = parser.parse_args()
    e2e = E2E(args.api, args.key, args.timeout)
    try:
        await e2e.wait_ready()
        results = []
        for scenario in args.scenario or DEMO:
            await e2e.wait_quiet()
            results.append(await e2e.run_scenario(scenario))
            await asyncio.sleep(5)
    finally:
        await e2e.close()
    print(json.dumps(results, indent=2))
    ok = all(r.get("ok") for r in results)
    print(
        f"\n{'ALL SCENARIOS PASSED' if ok else 'FAILURES'}: {sum(1 for r in results if r.get('ok'))}/{len(results)}"
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
