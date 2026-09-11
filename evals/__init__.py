"""Aegis evaluation suites.

Every component has an eval that produces a score and a pass/fail against a threshold. The agent
eval runs the real runtime against the in-process simulator with either the deterministic planner
(baseline, free) or a real LLM (costs money; opt in with --llm).
"""
