"""LLM structured-output eval: schema validity, action legality and answer quality on fixed prompts."""

from __future__ import annotations

from aegis.agent.prompts import SYSTEM_PROMPT
from aegis.agent.schemas import AgentProposal, TestJudgement
from aegis.ports.llm import LLMProvider
from evals.harness.core import CaseResult, SuiteResult, run_cases

PROMPTS = [
    (
        "investigate-redis",
        "investigate",
        ("call_tool", "propose_hypotheses"),
        """# Incident INC-1042 — API latency regression
severity sev2, affected services: api-gateway, redis
detection signals:
- redis connections: 12 -> 240 (saturation, 30 sigma)
- api-gateway latency_p95_ms: 200 -> 2400 (latency, 12 sigma)
# Topology
- api-gateway (gateway) -> auth-service, order-service, payment-service
- order-service (service) -> postgres, redis, inventory-service
- auth-service (service) -> redis, postgres
- redis (cache)
# Flow incident-investigation@1.1.0 — phase 'investigate' (iteration 1/8)
allowed actions in this phase: call_tool, propose_hypotheses, phase_complete, conclude_no_action, escalate
# Allowed tools (only these can be called now)
- inspect_redis(component?: string): Cache client connections by service, saturation, blocked clients, hit rate.
- get_logs(service: string, level?: string): Recent log lines for a service.
- inspect_dependencies(service: string): Dependency graph around a service.
# Evidence
[E1 metric api-gateway s=0.80 via get_metrics] api-gateway latency_p95_ms 203 -> 2450 (+1107%)
[E2 health redis s=0.60 via get_health] redis is degraded; failing checks: connections_ok
# Hypotheses
(none yet)
Decide the single best next step now.""",
    ),
    (
        "remediate-wrong-target-guard",
        "remediate",
        ("plan_remediation",),
        """# Incident INC-1043 — Elevated API error rate
severity sev2, affected services: api-gateway, order-service, inventory-service
# Topology
- api-gateway (gateway) -> order-service
- order-service (service) -> inventory-service, postgres
- inventory-service (service) -> postgres
# Flow incident-investigation@1.1.0 — phase 'remediate' (iteration 1/2)
allowed actions in this phase: plan_remediation, call_tool, conclude_no_action, escalate
# Remediation tools available to the workflow (plan_remediation only)
restart_service, rollback_deployment, scale_service, rotate_connection_pool, clear_cache
The remediation must target the root-cause service of a CONFIRMED hypothesis.
# Evidence
[E1 diagnostic inventory-service s=0.85] inventory-service process: DOWN (crashed), restarts 0
[E2 topology inventory-service s=0.80] order-service depends on inventory-service; unhealthy dependencies: inventory-service (down)
# Hypotheses
[H1 confirmed conf=0.91 root=inventory-service cat=dependency_failure tests=confirmed] inventory-service is down and its dependents fail
Decide the single best next step now.""",
    ),
]


async def _case(
    name: str, phase: str, allowed: tuple[str, ...], prompt: str, llm: LLMProvider
) -> CaseResult:
    result = await llm.complete_structured(
        schema=AgentProposal, system=SYSTEM_PROMPT, user=prompt, tier="reasoner"
    )
    p = result.value
    legal = p.action in allowed
    quality = 0.0
    detail: dict[str, object] = {
        "action": p.action,
        "tokens": result.usage.total_tokens,
        "latency_ms": round(result.usage.latency_ms),
    }
    if name == "investigate-redis":
        tool = p.tool_call.tool_name if p.tool_call else None
        detail["tool"] = tool
        quality = (
            1.0
            if tool == "inspect_redis"
            else 0.5
            if tool in ("inspect_dependencies", "get_logs")
            else (
                0.7
                if p.action == "propose_hypotheses"
                and p.hypotheses
                and p.hypotheses[0].suspected_root_cause_service == "order-service"
                else 0.0
            )
        )
    else:
        rem = p.remediation
        detail["remediation"] = (rem.tool_name, rem.arguments_json) if rem else None
        quality = (
            1.0
            if rem
            and rem.tool_name == "restart_service"
            and "inventory-service" in rem.arguments_json
            else 0.0
        )
    passed = legal and quality >= 0.5
    return CaseResult(
        name=name, passed=passed, score=(0.4 if legal else 0.0) + 0.6 * quality, details=detail
    )


async def _judge_case(llm: LLMProvider) -> CaseResult:
    from aegis.agent.prompts import JUDGE_SYSTEM

    result = await llm.complete_structured(
        schema=TestJudgement,
        system=JUDGE_SYSTEM,
        tier="fast",
        user="Hypothesis: order-service leaks Redis connections\nSuspected root cause service: order-service\n"
        "Expectation: order-service is the top redis client\nDiagnostic result:\n- redis diagnostic: saturation 102%, "
        "255 connections; top client order-service (240, 94%); leaked: order-service=228",
    )
    ok = result.value.outcome == "confirmed"
    return CaseResult(
        name="judge-confirms-implicating-diagnostic",
        passed=ok,
        score=1.0 if ok else 0.0,
        details={"outcome": result.value.outcome, "detail": result.value.detail[:120]},
    )


async def run(llm: LLMProvider) -> SuiteResult:
    cases = {
        n: (lambda n=n, ph=ph, al=al, pr=pr: _case(n, ph, al, pr, llm)) for n, ph, al, pr in PROMPTS
    }
    cases["judge"] = lambda: _judge_case(llm)
    result = await run_cases("llm-structured-output", cases, threshold=0.66, concurrency=2)
    result.metrics = {
        "model": llm.model_for("reasoner"),
        "total_tokens": sum(int(c.details.get("tokens", 0)) for c in result.cases),
    }
    return result
