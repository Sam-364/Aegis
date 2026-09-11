from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from aegis.domain.enums import SignalKind
from aegis.domain.errors import FlowDefinitionError, NotFoundError
from aegis.domain.flow import FlowPack
from aegis.flow.loader import load_flow_dir, parse_flow
from aegis.flow.registry import FlowRegistry
from aegis.flow.runtime import FlowRuntime, PhaseFacts
from aegis.tools.builtin import build_default_registry
from tests.helpers import build_runtime

ROOT = Path(__file__).resolve().parents[2]


def test_shipped_flow_packs_reference_only_registered_tools() -> None:
    reg = FlowRegistry(load_flow_dir(ROOT / "flows"))
    reg.validate_tools(set(build_default_registry().names()))
    for pack in reg.all():
        assert pack.checksum
        assert pack.phase("verify").terminal and pack.phase("escalate").terminal
        remediate = pack.phase("remediate")
        assert remediate.plans_remediation
        assert not any(
            t.startswith(("restart", "rollback", "scale")) for t in remediate.allowed_tools
        ), "mutating tools must never be in-loop phase tools"
        assert pack.remediation_tools


def test_registry_versions_and_selection() -> None:
    base = load_flow_dir(ROOT / "flows")
    v2 = base[0].model_copy(update={"version": "2.0.0", "checksum": "x"})
    reg = FlowRegistry([*base, v2])
    assert reg.get(base[0].name).version == "2.0.0"
    assert reg.get(base[0].name, base[0].version).version == base[0].version
    with pytest.raises(NotFoundError):
        reg.get("nope")
    with pytest.raises(FlowDefinitionError):
        reg.register(v2)

    def sig(kind: SignalKind):  # type: ignore[no-untyped-def]
        from datetime import UTC, datetime

        from aegis.domain.incident import AnomalySignal

        return AnomalySignal(
            service="api-gateway",
            metric="m",
            kind=kind,
            observed_value=1,
            baseline_value=1,
            deviation_sigma=5,
            detector="t",
            detected_at=datetime.now(tz=UTC),
            window_seconds=1,
        )

    assert reg.select([sig(SignalKind.LATENCY)]).name == "api-latency-investigation"
    assert (
        reg.select([sig(SignalKind.ERROR_RATE), sig(SignalKind.AVAILABILITY)]).name
        == "error-rate-investigation"
    )
    assert reg.select([sig(SignalKind.RESOURCE)]).name == "incident-investigation"
    assert reg.select([]).name == "incident-investigation"


def test_parse_flow_rejects_bad_definitions() -> None:
    with pytest.raises(FlowDefinitionError):
        parse_flow({"name": "x", "version": "1", "initial_phase": "nope", "phases": []})
    with pytest.raises(FlowDefinitionError):
        parse_flow(
            {
                "name": "x",
                "version": "1",
                "initial_phase": "a",
                "phases": [
                    {
                        "name": "a",
                        "tools": ["t"],
                        "transitions": [{"when": "exhausted", "to": "zzz"}],
                    },
                    {"name": "done", "terminal": True},
                ],
            }
        )
    with pytest.raises(FlowDefinitionError):
        load_flow_dir(Path("/nonexistent"))


def test_flow_runtime_decisions() -> None:
    rt = build_runtime(warmup=30)
    pack: FlowPack = rt.flows.get("incident-investigation")
    runtime = FlowRuntime(pack)
    triage = runtime.initial_phase()
    assert triage.name == "triage"
    assert runtime.decide(triage, PhaseFacts(iterations=1, evidence_count=1)) == (
        "exit_conditions_met",
        None,
    )
    assert runtime.decide(triage, PhaseFacts(iterations=1, evidence_count=3)) == (
        "exit_conditions_met",
        "investigate",
    )
    assert runtime.decide(triage, PhaseFacts(iterations=4, evidence_count=1)) == (
        "exhausted",
        "investigate",
    )
    investigate = runtime.phase("investigate")
    assert runtime.decide(investigate, PhaseFacts(iterations=2, no_action_required=True)) == (
        "no_action_required",
        "verify",
    )
    hypo = runtime.phase("hypothesize")
    ev = runtime.evaluate_exit(hypo, PhaseFacts(top_confidence=0.4))
    assert not ev.met and ev.unsatisfied == ["min_hypothesis_confidence>=0.55"]
    assert runtime.decide(hypo, PhaseFacts(iterations=3, top_confidence=0.4)) == (
        "exhausted",
        "investigate",
    )
    validate = runtime.phase("validate")
    assert runtime.decide(validate, PhaseFacts(iterations=4)) == ("exhausted", "hypothesize")
    assert runtime.decide(validate, PhaseFacts(iterations=1, hypothesis_validated=True)) == (
        "exit_conditions_met",
        "remediate",
    )
    remediate = runtime.phase("remediate")
    assert runtime.decide(remediate, PhaseFacts(iterations=1, action_planned=True)) == (
        "exit_conditions_met",
        "verify",
    )


def test_definition_checksums_are_stable_across_processes() -> None:
    """The definitions table keys rows on (kind, name, version, checksum), so a checksum that
    varies between processes silently writes a new "version" of every tool on every restart.
    `model_dump` renders set fields in iteration order, which depends on hash randomisation — so
    this has to be checked in fresh interpreters, not in this one."""
    code = (
        "from pathlib import Path;"
        "from aegis.flow.loader import load_flow_dir;"
        "from aegis.policy.loader import load_policy_dir;"
        "from aegis.tools.builtin import build_default_registry;"
        "from aegis.infrastructure.postgres.definitions import _checksum;"
        "r = build_default_registry();"
        "print(''.join(_checksum(r.spec(n)) for n in r.names()),"
        " _checksum({'rules': load_policy_dir(Path('policies'))}),"
        " ''.join(_checksum(p) for p in load_flow_dir(Path('flows'))))"
    )
    digests = set()
    for seed in ("1", "7919"):
        out = subprocess.run(
            [sys.executable, "-c", code],
            env={**os.environ, "PYTHONHASHSEED": seed},
            capture_output=True,
            text=True,
            check=True,
            cwd=ROOT,
        )
        digests.add(out.stdout.strip())
    assert len(digests) == 1, "definition checksums depend on the interpreter's hash seed"
