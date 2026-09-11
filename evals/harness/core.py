"""Eval primitives: cases, results, runner, report."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = ROOT / "evals" / "results"


@dataclass
class CaseResult:
    name: str
    passed: bool
    score: float
    details: dict[str, Any] = field(default_factory=dict)
    duration_s: float = 0.0
    error: str | None = None


@dataclass
class SuiteResult:
    suite: str
    cases: list[CaseResult] = field(default_factory=list)
    threshold: float = 1.0
    metrics: dict[str, Any] = field(default_factory=dict)

    @property
    def score(self) -> float:
        return sum(c.score for c in self.cases) / len(self.cases) if self.cases else 0.0

    @property
    def pass_rate(self) -> float:
        return sum(1 for c in self.cases if c.passed) / len(self.cases) if self.cases else 0.0

    @property
    def passed(self) -> bool:
        return self.pass_rate >= self.threshold

    def to_dict(self) -> dict[str, Any]:
        return {
            "suite": self.suite,
            "score": round(self.score, 4),
            "pass_rate": round(self.pass_rate, 4),
            "threshold": self.threshold,
            "passed": self.passed,
            "metrics": self.metrics,
            "cases": [c.__dict__ for c in self.cases],
        }


Case = Callable[[], Awaitable[CaseResult]]


async def run_cases(
    suite: str, cases: dict[str, Case], *, threshold: float = 1.0, concurrency: int = 1
) -> SuiteResult:
    result = SuiteResult(suite=suite, threshold=threshold)
    sem = asyncio.Semaphore(concurrency)

    async def run_one(name: str, fn: Case) -> CaseResult:
        async with sem:
            started = time.perf_counter()
            try:
                out = await fn()
            except Exception as exc:
                out = CaseResult(
                    name=name, passed=False, score=0.0, error=f"{type(exc).__name__}: {exc}"
                )
            out.name = out.name or name
            out.duration_s = round(time.perf_counter() - started, 2)
            return out

    result.cases = list(await asyncio.gather(*(run_one(n, f) for n, f in cases.items())))
    return result


def write_report(results: list[SuiteResult], *, label: str) -> tuple[Path, Path]:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    json_path = RESULTS_DIR / f"{stamp}-{label}.json"
    md_path = RESULTS_DIR / f"{stamp}-{label}.md"
    json_path.write_text(json.dumps([r.to_dict() for r in results], indent=2, default=str))
    lines = [
        f"# Aegis evals — {label} — {stamp}",
        "",
        "| suite | score | pass rate | threshold | status |",
        "|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r.suite} | {r.score:.2f} | {r.pass_rate:.0%} | {r.threshold:.0%} | "
            f"{'PASS' if r.passed else 'FAIL'} |"
        )
    for r in results:
        lines += ["", f"## {r.suite}", ""]
        if r.metrics:
            lines.append("metrics: " + ", ".join(f"{k}={v}" for k, v in r.metrics.items()))
            lines.append("")
        lines.append("| case | pass | score | duration | details |")
        lines.append("|---|---|---|---|---|")
        for c in r.cases:
            detail = c.error or json.dumps(c.details, default=str)
            lines.append(
                f"| {c.name} | {'✓' if c.passed else '✗'} | {c.score:.2f} | {c.duration_s}s | "
                f"{detail[:160].replace('|', '/')} |"
            )
    md_path.write_text("\n".join(lines) + "\n")
    return json_path, md_path
