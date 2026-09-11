"""Tool registry. Unknown tools do not exist as far as the runtime is concerned."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from aegis.domain.enums import ToolCategory
from aegis.domain.errors import ToolNotRegistered
from aegis.domain.tool import ToolSpec
from aegis.tools.definition import ToolDefinition


class ToolRegistry:
    def __init__(self, tools: Iterable[ToolDefinition[Any]] = ()) -> None:
        self._tools: dict[str, ToolDefinition[Any]] = {}
        self._disabled: set[str] = set()
        for t in tools:
            self.register(t)

    def register(self, definition: ToolDefinition[Any]) -> None:
        if definition.name in self._tools:
            raise ValueError(f"tool '{definition.name}' is already registered")
        self._tools[definition.name] = definition

    def has(self, name: str) -> bool:
        return name in self._tools

    def get(self, name: str) -> ToolDefinition[Any]:
        try:
            return self._tools[name]
        except KeyError:
            raise ToolNotRegistered(
                f"tool '{name}' is not registered", details={"tool": name}
            ) from None

    def is_enabled(self, name: str) -> bool:
        return name in self._tools and name not in self._disabled and self._tools[name].spec.enabled

    def disable(self, name: str) -> None:
        self.get(name)
        self._disabled.add(name)

    def enable(self, name: str) -> None:
        self._disabled.discard(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def specs(self) -> list[ToolSpec]:
        return [self.spec(n) for n in self.names()]

    def spec(self, name: str) -> ToolSpec:
        defn = self.get(name)
        return defn.spec.model_copy(update={"enabled": self.is_enabled(name)})

    def by_category(self, category: ToolCategory) -> list[ToolDefinition[Any]]:
        return [t for t in self._tools.values() if t.spec.category is category]

    def descriptions_for(self, names: Iterable[str]) -> list[dict[str, Any]]:
        """Compact tool descriptions for the LLM prompt (only the tools the phase allows)."""
        out = []
        for name in sorted(set(names)):
            if not self.has(name):
                continue
            spec = self._tools[name].spec
            props = spec.arguments_schema.get("properties", {})
            required = set(spec.arguments_schema.get("required", []))
            args = ", ".join(
                f"{k}{'' if k in required else '?'}: {v.get('type', 'any')}"
                + (f" ({v['description']})" if v.get("description") else "")
                for k, v in props.items()
            )
            out.append(
                {
                    "name": name,
                    "description": spec.description,
                    "arguments": args,
                    "category": spec.category.value,
                    "risk": spec.risk.value,
                }
            )
        return out
