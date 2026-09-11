"""Versioned flow registry and deterministic flow selection."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from aegis.domain.errors import FlowDefinitionError, NotFoundError
from aegis.domain.flow import FlowPack
from aegis.domain.incident import AnomalySignal


class FlowRegistry:
    def __init__(self, packs: Iterable[FlowPack] = ()) -> None:
        self._packs: dict[tuple[str, str], FlowPack] = {}
        for pack in packs:
            self.register(pack)

    def register(self, pack: FlowPack) -> None:
        key = (pack.name, pack.version)
        if key in self._packs:
            raise FlowDefinitionError(f"duplicate flow pack {pack.ref}")
        self._packs[key] = pack

    def get(self, name: str, version: str | None = None) -> FlowPack:
        if version is not None:
            pack = self._packs.get((name, version))
            if pack is None:
                raise NotFoundError(f"flow pack {name}@{version} not found")
            return pack
        candidates = [p for (n, _), p in self._packs.items() if n == name]
        if not candidates:
            raise NotFoundError(f"flow pack {name} not found")
        return max(candidates, key=lambda p: _version_key(p.version))

    def all(self) -> list[FlowPack]:
        return sorted(self._packs.values(), key=lambda p: (p.name, _version_key(p.version)))

    def names(self) -> list[str]:
        return sorted({n for n, _ in self._packs})

    def validate_tools(self, known_tools: set[str], mutating_tools: set[str] | None = None) -> None:
        for pack in self._packs.values():
            unknown = pack.all_tools - known_tools
            if unknown:
                raise FlowDefinitionError(
                    f"flow pack {pack.ref} references unknown tools: {sorted(unknown)}"
                )
            if mutating_tools is None:
                continue
            # A mutating tool in a phase's `tools` would be refused at run time by the
            # `category_permitted` check, but a flow pack that asks for it is a mistake we should
            # surface at load time rather than in the middle of an incident.
            for phase in pack.phases:
                offending = phase.allowed_tools & mutating_tools
                if offending:
                    raise FlowDefinitionError(
                        f"flow pack {pack.ref} phase '{phase.name}' allows mutating tools "
                        f"{sorted(offending)} inside the agent loop; declare them under "
                        "remediation_tools instead"
                    )

    def select(
        self, signals: Sequence[AnomalySignal], *, default: str = "incident-investigation"
    ) -> FlowPack:
        """Pick the most specific latest-version pack whose ``applies_to`` matches the signals."""
        kinds = {s.kind for s in signals}
        best: tuple[int, int, FlowPack] | None = None
        for name in self.names():
            pack = self.get(name)
            if not pack.applies_to:
                continue
            overlap = len(pack.applies_to & kinds)
            if overlap == 0:
                continue
            score = (overlap, pack.priority)
            if best is None or score > (best[0], best[1]):
                best = (overlap, pack.priority, pack)
        if best is not None:
            return best[2]
        return self.get(default)


def _version_key(version: str) -> tuple[int, ...]:
    parts: list[int] = []
    for piece in version.split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)
