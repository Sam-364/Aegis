"""Operational CLI: migrations, readiness, scenario injection, incident inspection."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from aegis.config import get_settings
from aegis.logging import configure_logging


def _print(data: Any) -> None:
    print(json.dumps(data, indent=2, default=str))


async def cmd_migrate(_: argparse.Namespace) -> int:
    from aegis.infrastructure.postgres.migrations import upgrade_head_async

    settings = get_settings()
    await upgrade_head_async(settings.sync_database_url)
    print("migrations applied")
    return 0


async def cmd_check(_: argparse.Namespace) -> int:
    from aegis.application.bootstrap import build_runtime, readiness

    settings = get_settings()
    container, res = await build_runtime(settings, role="api")
    try:
        checks = await readiness(settings, res)
        checks["llm"] = {"ok": await container.llm.healthy() if container.llm else None}
        _print(checks)
        return 0 if all(v.get("ok") in (True, None) for v in checks.values()) else 1
    finally:
        await res.aclose()


async def cmd_inject(args: argparse.Namespace) -> int:
    from aegis.infrastructure.simulator.control import SimulatorControlClient

    client = SimulatorControlClient(get_settings().simulator_url)
    try:
        _print(await client.inject(args.scenario, json.loads(args.params) if args.params else None))
        return 0
    finally:
        await client.aclose()


async def cmd_scenarios(_: argparse.Namespace) -> int:
    from aegis.infrastructure.simulator.control import SimulatorControlClient

    client = SimulatorControlClient(get_settings().simulator_url)
    try:
        for s in await client.scenarios():
            print(f"{s['id']:24} {s['title']}")
        return 0
    finally:
        await client.aclose()


async def cmd_incidents(args: argparse.Namespace) -> int:
    from aegis.application.bootstrap import build_runtime

    settings = get_settings()
    container, res = await build_runtime(settings, role="api", temporal_required=False)
    try:
        items, total = await container.incidents.list_incidents(
            active_only=args.active, limit=args.limit
        )
        for i in items:
            print(f"{i.display_id:10} {i.severity.value:5} {i.status.value:20} {i.title}")
        print(f"{total} total")
        return 0
    finally:
        await res.aclose()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="aegis", description="Aegis operational CLI")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate", help="apply database migrations").set_defaults(fn=cmd_migrate)
    sub.add_parser("check", help="check dependencies and readiness").set_defaults(fn=cmd_check)
    sub.add_parser("scenarios", help="list simulator scenarios").set_defaults(fn=cmd_scenarios)
    p = sub.add_parser("inject", help="inject a simulator scenario")
    p.add_argument("scenario")
    p.add_argument("--params", default=None, help="JSON object of parameters")
    p.set_defaults(fn=cmd_inject)
    p = sub.add_parser("incidents", help="list incidents")
    p.add_argument("--active", action="store_true")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(fn=cmd_incidents)
    args = parser.parse_args(argv)
    configure_logging("WARNING", "console", service="aegis-cli")
    sys.exit(asyncio.run(args.fn(args)))


if __name__ == "__main__":
    main()
