"""Eval runner.

uv run python -m evals.run                    # all offline suites (free)
uv run python -m evals.run --llm              # adds LLM suites (real OpenAI calls)
uv run python -m evals.run --suite agent --llm --model gpt-5-nano
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from evals.harness.core import SuiteResult, write_report
from evals.harness.world import real_llm
from evals.suites import agent, authorization, detection, hypotheses, memory, verification

OFFLINE = {
    "detection": detection.run,
    "authorization": authorization.run,
    "hypotheses": hypotheses.run,
    "verification": verification.run,
    "memory": memory.run,
    "agent": agent.run,
}


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="evals")
    parser.add_argument("--suite", action="append", help="suite name (repeatable); default: all")
    parser.add_argument(
        "--llm", action="store_true", help="run LLM-backed suites with the configured provider"
    )
    parser.add_argument("--model", default=None, help="override the reasoner model for LLM suites")
    parser.add_argument(
        "--scenario", action="append", help="restrict agent eval to these scenarios"
    )
    parser.add_argument(
        "--seeds", type=int, default=1, help="seeds per scenario for the agent eval"
    )
    args = parser.parse_args(argv)
    selected = set(args.suite or OFFLINE.keys())
    results: list[SuiteResult] = []
    llm = real_llm() if args.llm else None
    for name in ["detection", "authorization", "hypotheses", "verification", "memory"]:
        if name in selected:
            print(f"→ {name}", file=sys.stderr)
            results.append(await OFFLINE[name]())
    if "agent" in selected:
        print("→ agent (deterministic baseline)", file=sys.stderr)
        results.append(await agent.run(llm=None, scenarios=args.scenario, seeds=args.seeds))
        if llm is not None:
            print(f"→ agent (llm {args.model or llm.model_for('reasoner')})", file=sys.stderr)
            results.append(
                await agent.run(
                    llm=llm, reasoner_model=args.model, scenarios=args.scenario, seeds=args.seeds
                )
            )
    if llm is not None and ("llm" in selected or not args.suite):
        from evals.suites import llm_schema

        if args.model:
            llm.models["reasoner"] = args.model  # type: ignore[attr-defined]
        print("→ llm-structured-output", file=sys.stderr)
        results.append(await llm_schema.run(llm))
    label = "llm" if llm is not None else "offline"
    if args.model:
        label += f"-{args.model}"
    json_path, md_path = write_report(results, label=label)
    print(md_path.read_text())
    print(f"reports: {json_path} {md_path}", file=sys.stderr)
    return 0 if all(r.passed for r in results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
