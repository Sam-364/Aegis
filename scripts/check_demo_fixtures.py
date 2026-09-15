"""Check that the demo console can answer every call it makes.

The fixtures are captured from a live stack, so they go stale the moment an endpoint is added.
This resolves the calls the console actually makes against the recorded index — the same
exact-then-bare-path lookup `src/lib/demo.ts` performs — and fails if any of them would 404 in
the browser.

    uv run python scripts/check_demo_fixtures.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "apps" / "web" / "public" / "demo"


def key_for(path: str, query: dict[str, object] | None = None) -> str:
    items = sorted((k, v) for k, v in (query or {}).items() if v not in (None, ""))
    return path + "?" + "&".join(f"{k}={v}" for k, v in items) if items else path


def main() -> int:
    index = json.loads((DEMO / "index.json").read_text())
    routes: dict[str, str] = index["routes"]
    featured = index["featured"]
    if not featured:
        print("no featured incidents in the recording", file=sys.stderr)
        return 1
    incident_id = featured[0]["id"]

    # Exactly what the console asks for on first load and on an incident page.
    calls: list[tuple[str, dict[str, object]]] = [
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
        ("/api/v1/simulation/topology", {}),
        ("/api/v1/simulation/state", {}),
        ("/api/v1/simulation/actions", {}),
        ("/api/v1/simulation/faults", {"active_only": False}),
        (f"/api/v1/incidents/{incident_id}", {}),
        (f"/api/v1/incidents/{incident_id}/timeline", {"after_seq": 0, "limit": 500}),
        (f"/api/v1/incidents/{incident_id}/evidence", {}),
        (f"/api/v1/incidents/{incident_id}/audit", {"limit": 200, "offset": 0}),
        (f"/api/v1/incidents/{incident_id}/approvals", {}),
        (f"/api/v1/incidents/{incident_id}/agent-runs", {}),
        (f"/api/v1/incidents/{incident_id}/workflow", {}),
    ]

    missing: list[str] = []
    unreadable: list[str] = []
    for path, query in calls:
        name = routes.get(key_for(path, query)) or routes.get(path)
        if name is None:
            missing.append(path)
            continue
        file = DEMO / name
        try:
            json.loads(file.read_text())
        except Exception as exc:
            unreadable.append(f"{path} -> {name}: {exc}")

    for name in set(routes.values()):
        if not (DEMO / name).exists():
            unreadable.append(f"index points at missing file {name}")

    if missing or unreadable:
        for m in missing:
            print(f"  not recorded: {m}", file=sys.stderr)
        for u in unreadable:
            print(f"  broken: {u}", file=sys.stderr)
        return 1

    size = sum(f.stat().st_size for f in DEMO.glob("*.json")) / 1024
    print(
        f"demo fixtures OK: {len(calls)} console calls resolve, "
        f"{len(set(routes.values()))} files, {size:.0f} KB"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
