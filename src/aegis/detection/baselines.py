"""Exponentially weighted baselines with variance tracking."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class RollingBaseline:
    """EWMA mean/variance for one (component, metric).

    ``alpha`` is deliberately small so slow drifts (memory leaks, connection growth) stand out
    instead of being absorbed. While a rule on this metric is firing the baseline is frozen so the
    anomaly never becomes the new normal.
    """

    alpha: float = 0.05
    warmup: int = 12
    mean: float | None = None
    var: float = 0.0
    count: int = 0
    frozen: bool = False
    last_value: float | None = None
    history: list[float] = field(default_factory=list)

    @property
    def warm(self) -> bool:
        return self.count >= self.warmup and self.mean is not None

    @property
    def std(self) -> float:
        return math.sqrt(max(self.var, 0.0))

    def update(self, value: float) -> None:
        self.last_value = value
        if self.frozen:
            return
        if self.mean is None:
            self.mean = value
            self.var = 0.0
        else:
            diff = value - self.mean
            incr = self.alpha * diff
            self.mean += incr
            self.var = (1 - self.alpha) * (self.var + diff * incr)
        self.count += 1
        self.history.append(value)
        if len(self.history) > 60:
            del self.history[: len(self.history) - 60]

    def zscore(self, value: float, *, min_std_ratio: float = 0.05, epsilon: float = 1e-6) -> float:
        if self.mean is None:
            return 0.0
        floor = max(abs(self.mean) * min_std_ratio, epsilon)
        std = max(self.std, floor)
        return (value - self.mean) / std

    def ratio(self, value: float) -> float:
        if self.mean is None or abs(self.mean) < 1e-9:
            return math.inf if value > 0 else 1.0
        return value / self.mean

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RollingBaseline:
        return cls(**data)
