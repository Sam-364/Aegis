"""Enumerations shared across the runtime.

Values are lowercase snake_case strings so that they serialize cleanly to JSON, Postgres
enums and the frontend without translation tables.
"""

from __future__ import annotations

from enum import StrEnum


class Environment(StrEnum):
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


class Severity(StrEnum):
    """Incident severity. SEV1 is the most severe."""

    SEV1 = "sev1"
    SEV2 = "sev2"
    SEV3 = "sev3"
    SEV4 = "sev4"

    @property
    def rank(self) -> int:
        """Lower rank means more severe (SEV1 → 1)."""
        return int(self.value[-1])

    def is_at_least(self, other: Severity) -> bool:
        """True if ``self`` is as severe as or more severe than ``other``."""
        return self.rank <= other.rank


class IncidentStatus(StrEnum):
    DETECTED = "detected"
    TRIAGING = "triaging"
    INVESTIGATING = "investigating"
    HYPOTHESIS_FORMED = "hypothesis_formed"
    VALIDATING = "validating"
    REMEDIATION_PLANNED = "remediation_planned"
    AWAITING_APPROVAL = "awaiting_approval"
    REMEDIATING = "remediating"
    VERIFYING = "verifying"
    RESOLVED = "resolved"
    ROLLED_BACK = "rolled_back"
    ESCALATED = "escalated"
    FAILED = "failed"
    CLOSED = "closed"

    @property
    def is_terminal(self) -> bool:
        return self is IncidentStatus.CLOSED

    @property
    def is_active(self) -> bool:
        """Active incidents may still have tools executed on their behalf."""
        return self not in _INACTIVE_STATUSES


_INACTIVE_STATUSES = frozenset(
    {IncidentStatus.RESOLVED, IncidentStatus.CLOSED, IncidentStatus.FAILED}
)


class ToolCategory(StrEnum):
    READ_ONLY = "read_only"
    DIAGNOSTIC = "diagnostic"
    MUTATING = "mutating"
    DANGEROUS = "dangerous"

    @property
    def is_mutation(self) -> bool:
        return self in (ToolCategory.MUTATING, ToolCategory.DANGEROUS)


class RiskLevel(StrEnum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return _RISK_RANK[self]

    def exceeds(self, ceiling: RiskLevel) -> bool:
        return self.rank > ceiling.rank


_RISK_RANK = {
    RiskLevel.NONE: 0,
    RiskLevel.LOW: 1,
    RiskLevel.MEDIUM: 2,
    RiskLevel.HIGH: 3,
    RiskLevel.CRITICAL: 4,
}


class PolicyEffect(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class HypothesisStatus(StrEnum):
    PROPOSED = "proposed"
    TESTING = "testing"
    SUPPORTED = "supported"
    CONFIRMED = "confirmed"
    REFUTED = "refuted"
    ABANDONED = "abandoned"


class HypothesisCategory(StrEnum):
    RESOURCE_EXHAUSTION = "resource_exhaustion"
    DEPLOYMENT_REGRESSION = "deployment_regression"
    DEPENDENCY_FAILURE = "dependency_failure"
    CAPACITY = "capacity"
    NETWORK = "network"
    CONFIGURATION = "configuration"
    TRANSIENT = "transient"
    UNKNOWN = "unknown"


class ActionPlanStatus(StrEnum):
    PROPOSED = "proposed"
    POLICY_DENIED = "policy_denied"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTING = "executing"
    EXECUTED = "executed"
    EXECUTION_FAILED = "execution_failed"
    VERIFIED = "verified"
    VERIFICATION_FAILED = "verification_failed"
    ROLLED_BACK = "rolled_back"
    SUPERSEDED = "superseded"


class ExecutionStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    DENIED = "denied"
    SKIPPED_DUPLICATE = "skipped_duplicate"


class EvidenceKind(StrEnum):
    SIGNAL = "signal"
    METRIC = "metric"
    LOG = "log"
    TRACE = "trace"
    TOPOLOGY = "topology"
    DEPLOYMENT = "deployment"
    HEALTH = "health"
    DIAGNOSTIC = "diagnostic"
    MEMORY = "memory"
    OBSERVATION = "observation"
    ACTION_RESULT = "action_result"
    VERIFICATION = "verification"


class RelationKind(StrEnum):
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    CAUSED_BY = "caused_by"
    DEPENDS_ON = "depends_on"
    CORRELATED_WITH = "correlated_with"
    OBSERVED_ON = "observed_on"
    RESOLVED_BY = "resolved_by"


class GraphNodeKind(StrEnum):
    EVIDENCE = "evidence"
    HYPOTHESIS = "hypothesis"
    SERVICE = "service"
    ACTION = "action"


class AgentRunStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    BUDGET_EXHAUSTED = "budget_exhausted"
    CANCELLED = "cancelled"


class TerminationReason(StrEnum):
    PHASE_COMPLETE = "phase_complete"
    ACTION_PLANNED = "action_planned"
    NO_ACTION_REQUIRED = "no_action_required"
    ESCALATE = "escalate"
    BUDGET_EXHAUSTED = "budget_exhausted"
    INSUFFICIENT_SIGNAL = "insufficient_signal"
    LLM_UNAVAILABLE = "llm_unavailable"
    INCIDENT_INACTIVE = "incident_inactive"
    ERROR = "error"


class AgentStepKind(StrEnum):
    OBSERVATION = "observation"
    PROPOSAL = "proposal"
    AUTHORIZATION = "authorization"
    TOOL_EXECUTION = "tool_execution"
    EVIDENCE_UPDATE = "evidence_update"
    HYPOTHESIS_UPDATE = "hypothesis_update"
    DECISION = "decision"
    REMEDIATION_PLAN = "remediation_plan"
    FALLBACK = "fallback"


class SignalKind(StrEnum):
    LATENCY = "latency"
    ERROR_RATE = "error_rate"
    SATURATION = "saturation"
    TRAFFIC = "traffic"
    AVAILABILITY = "availability"
    RESOURCE = "resource"


class ActorKind(StrEnum):
    SYSTEM = "system"
    DETECTOR = "detector"
    AGENT = "agent"
    WORKFLOW = "workflow"
    HUMAN = "human"
    API = "api"


class Role(StrEnum):
    VIEWER = "viewer"
    OPERATOR = "operator"
    ADMIN = "admin"

    @property
    def rank(self) -> int:
        return {Role.VIEWER: 0, Role.OPERATOR: 1, Role.ADMIN: 2}[self]

    def includes(self, other: Role) -> bool:
        return self.rank >= other.rank


class VerificationStatus(StrEnum):
    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"
    INCONCLUSIVE = "inconclusive"


class HealthState(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


class NotificationKind(StrEnum):
    INCIDENT_DETECTED = "incident_detected"
    APPROVAL_REQUESTED = "approval_requested"
    INCIDENT_RESOLVED = "incident_resolved"
    INCIDENT_ESCALATED = "incident_escalated"
    POLICY_DENIAL = "policy_denial"
