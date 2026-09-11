"""Evidence strength heuristics. Deterministic, documented, testable."""

from __future__ import annotations

import math
from datetime import datetime, timedelta


def ratio_strength(ratio: float) -> float:  # noqa: PLR0911 - threshold table
    """Strength of a 'metric vs baseline' observation. 1.0x → 0.1, 2x → 0.6, 5x+ → 0.9."""
    if math.isinf(ratio):
        return 0.9
    r = max(ratio, 1 / ratio) if ratio > 0 else 1.0
    if r < 1.2:
        return 0.1
    if r < 1.5:
        return 0.3
    if r < 2.0:
        return 0.5
    if r < 3.0:
        return 0.65
    if r < 5.0:
        return 0.8
    return 0.9


def recency_strength(when: datetime, now: datetime, *, hot_minutes: float = 30) -> float:
    """Recent deployments/changes are strong evidence; old ones are weak."""
    age = (now - when).total_seconds() / 60
    if age <= hot_minutes:
        return 0.85
    if age <= hot_minutes * 4:
        return 0.45
    return 0.15


def share_strength(share: float) -> float:
    """How dominant a client is in a shared resource (0.5 share → 0.6, 0.8+ → 0.9)."""
    if share >= 0.8:
        return 0.9
    if share >= 0.5:
        return 0.7
    if share >= 0.3:
        return 0.45
    return 0.2


def within(when: datetime, start: datetime, end: datetime, slack: timedelta = timedelta(0)) -> bool:
    return start - slack <= when <= end + slack
