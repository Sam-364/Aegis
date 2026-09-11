"""Builtin tools grouped by category. ``build_default_registry`` wires them all."""

from __future__ import annotations

from aegis.tools.builtin.diagnostic import DIAGNOSTIC_TOOLS
from aegis.tools.builtin.mutating import DANGEROUS_TOOLS, MUTATING_TOOLS
from aegis.tools.builtin.read import READ_TOOLS
from aegis.tools.registry import ToolRegistry


def build_default_registry() -> ToolRegistry:
    return ToolRegistry([*READ_TOOLS, *DIAGNOSTIC_TOOLS, *MUTATING_TOOLS, *DANGEROUS_TOOLS])


__all__ = ["build_default_registry"]
