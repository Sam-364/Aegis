"""A miniature distributed system with causal fault propagation.

The simulator is deliberately independent of the Aegis runtime (enforced by import-linter). It
exposes telemetry and remediation endpoints the same way a real platform would, so the runtime
cannot cheat by peeking at ground truth.
"""
