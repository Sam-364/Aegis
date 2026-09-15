"""Record a live Aegis stack into the console's demo fixtures.

The console is a pure client of the API, so with no API to talk to it renders an empty shell.
Rather than hand-write mock data that drifts from reality, this walks a running stack and saves
the real responses; the console replays them when `NEXT_PUBLIC_AEGIS_DEMO=1`.

    uv run python scripts/capture_demo_fixtures.py --incident 1117 --incident 1125

Every file lands in `apps/web/public/demo/`, served as a static asset so none of it enters the
JavaScript bundle. `index.json` maps request paths to files; the runtime looks up the exact
path first, then the path without its query.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "apps" / "web" / "public" / "demo"

# Endpoints that do not depend on a particular incident.
GLOBAL: list[tuple[str, dict[str, Any]]] = [
    ("/health", {}),
    ("/ready", {}),
    ("/api/v1/me", {}),
    ("/api/v1/system/info", {}),
    ("/api/v1/notifications", {"unread_only": False, "limit": 50}),
    ("/api/v1/incidents", {"limit": 50}),
    ("/api/v1/incidents/stats", {}),
    ("/api/v1/approvals", {"status": "pending", "limit": 100}),
    ("/api/v1/agent-runs", {"limit": 50}),
    ("/api/v1/flows", {}),
    ("/api/v1/tools", {}),
    ("/api/v1/policies", {}),
    ("/api/v1/memory", {"limit": 50, "offset": 0}),
    ("/api/v1/simulation/scenarios", {}),
    ("/api/v1/simulation/faults", {"active_only": False}),
    ("/api/v1/simulation/topology", {}),
    ("/api/v1/simulation/state", {}),
    ("/api/v1/simulation/actions", {}),
]

PER_INCIDENT = [
    ("", {}),
    ("/timeline", {"after_seq": 0, "limit": 500}),
    ("/evidence", {}),
    ("/audit", {"limit": 200, "offset": 0}),
    ("/approvals", {}),
    ("/agent-runs", {}),
    ("/workflow", {}),
]


def key_for(path: str, query: dict[str, Any]) -> str:
    """The lookup key: the path, plus a sorted query string when there is one."""
    items = sorted((k, v) for k, v in query.items() if v is not None and v != "")
    if not items:
        return path
    return path + "?" + "&".join(f"{k}={v}" for k, v in items)


def filename_for(key: str) -> str:
    """A filename safe to serve as a static asset.

    The key carries the query string, so anything outside `[A-Za-z0-9_.-]` — `?` above all, which
    would truncate the URL — becomes an underscore. The hash keeps distinct keys distinct.
    """
    stem = "".join(c if c.isalnum() or c in "_.-" else "_" for c in key.strip("/")) or "root"
    return f"{stem[:60]}_{hashlib.sha1(key.encode()).hexdigest()[:8]}.json"  # noqa: S324


class Capture:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client
        self.index: dict[str, str] = {}
        self.written = 0

    async def grab(self, path: str, query: dict[str, Any] | None = None) -> Any:
        query = query or {}
        r = await self.client.get(path, params=query)
        if r.status_code >= 400:
            print(f"  ! {path} -> {r.status_code}", file=sys.stderr)
            return None
        body = r.json()
        name = filename_for(key_for(path, query))
        (OUT / name).write_text(json.dumps(body, indent=1), encoding="utf-8")
        # Index both the exact call and the bare path, so a different limit still resolves.
        self.index[key_for(path, query)] = name
        self.index.setdefault(path, name)
        self.written += 1
        return body


async def main() -> int:
    ap = argparse.ArgumentParser(prog="capture-demo-fixtures")
    ap.add_argument("--api", default="http://localhost:8600")
    ap.add_argument("--key", default=None, help="X-Aegis-Key, if the stack requires one")
    ap.add_argument(
        "--incident",
        type=int,
        action="append",
        required=True,
        help="incident number to record (repeatable); the first is the one the console opens",
    )
    args = ap.parse_args()

    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)

    headers = {"X-Aegis-Key": args.key} if args.key else {}
    async with httpx.AsyncClient(base_url=args.api, headers=headers, timeout=30) as client:
        cap = Capture(client)
        for path, query in GLOBAL:
            await cap.grab(path, query)

        listing = await cap.grab("/api/v1/incidents", {"limit": 200})
        by_number = {i["number"]: i["id"] for i in (listing or {}).get("items", [])}
        featured: list[dict[str, Any]] = []
        for number in args.incident:
            incident_id = by_number.get(number)
            if incident_id is None:
                print(f"  ! INC-{number} not found", file=sys.stderr)
                continue
            for suffix, query in PER_INCIDENT:
                await cap.grab(f"/api/v1/incidents/{incident_id}{suffix}", query)
            runs = await cap.grab(f"/api/v1/incidents/{incident_id}/agent-runs") or []
            for run in runs:
                await cap.grab(f"/api/v1/agent-runs/{run['id']}")
                await cap.grab(f"/api/v1/agent-runs/{run['id']}/steps")
            approvals = await cap.grab(f"/api/v1/incidents/{incident_id}/approvals") or []
            for approval in approvals:
                await cap.grab(f"/api/v1/approvals/{approval['id']}")
            detail = await cap.grab(f"/api/v1/incidents/{incident_id}")
            if detail:
                featured.append({"number": number, "id": incident_id})

        # tool and flow detail pages
        for tool in await cap.grab("/api/v1/tools") or []:
            await cap.grab(f"/api/v1/tools/{tool['name']}")
        for flow in await cap.grab("/api/v1/flows") or []:
            await cap.grab(f"/api/v1/flows/{flow['name']}", {"version": flow["version"]})

        # topology component detail, for the topology page overlay
        topology = await cap.grab("/api/v1/simulation/topology") or {}
        for node in topology.get("nodes", [])[:12]:
            await cap.grab(f"/api/v1/simulation/components/{node['name']}/current")

        (OUT / "index.json").write_text(
            json.dumps(
                {
                    "captured_at": (await cap.grab("/api/v1/system/info") or {}).get("time", ""),
                    "featured": featured,
                    "routes": cap.index,
                },
                indent=1,
            ),
            encoding="utf-8",
        )
    size = sum(f.stat().st_size for f in OUT.glob("*.json"))
    print(f"wrote {cap.written} responses ({size / 1024:.0f} KB) to {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
