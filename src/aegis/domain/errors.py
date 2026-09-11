"""Domain error hierarchy.

Authorization errors carry a stable ``code`` so that API responses, audit events and the UI
can render them without depending on Python class names.
"""

from __future__ import annotations

from typing import Any


class AegisError(Exception):
    """Base class for all Aegis errors."""

    code: str = "aegis_error"
    http_status: int = 500

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}


class NotFoundError(AegisError):
    code = "not_found"
    http_status = 404


class ConflictError(AegisError):
    code = "conflict"
    http_status = 409


class ValidationError(AegisError):
    code = "validation_error"
    http_status = 422


class InvalidTransitionError(AegisError):
    code = "invalid_transition"
    http_status = 409


class ConcurrencyError(ConflictError):
    code = "concurrency_conflict"


class ForbiddenError(AegisError):
    code = "forbidden"
    http_status = 403


class UnauthorizedError(AegisError):
    code = "unauthorized"
    http_status = 401


class RateLimitedError(AegisError):
    code = "rate_limited"
    http_status = 429


# --- Tool authorization ----------------------------------------------------------------------


class ToolAuthorizationError(AegisError):
    """Base for every reason a tool call may be refused."""

    code = "tool_authorization_error"
    http_status = 403


class ToolNotRegistered(ToolAuthorizationError):  # noqa: N818 - domain vocabulary
    code = "tool_not_registered"


class ToolDisabled(ToolAuthorizationError):  # noqa: N818
    code = "tool_disabled"


class ToolNotAllowed(ToolAuthorizationError):  # noqa: N818
    code = "tool_not_allowed"


class FlowViolation(ToolAuthorizationError):  # noqa: N818
    code = "flow_violation"


class PhaseViolation(ToolAuthorizationError):  # noqa: N818
    code = "phase_violation"


class PolicyViolation(ToolAuthorizationError):  # noqa: N818
    code = "policy_violation"


class ApprovalRequired(ToolAuthorizationError):  # noqa: N818
    code = "approval_required"


class ExecutionBudgetExceeded(ToolAuthorizationError):  # noqa: N818
    code = "execution_budget_exceeded"


class DuplicateExecution(ToolAuthorizationError):  # noqa: N818
    code = "duplicate_execution"


class IncidentInactive(ToolAuthorizationError):  # noqa: N818
    code = "incident_inactive"


class InvalidToolArguments(ToolAuthorizationError):  # noqa: N818
    code = "invalid_tool_arguments"
    http_status = 422


class RiskCeilingExceeded(ToolAuthorizationError):  # noqa: N818
    code = "risk_ceiling_exceeded"


# --- Execution ---------------------------------------------------------------------------------


class ToolExecutionError(AegisError):
    code = "tool_execution_error"
    http_status = 502


class ToolTimeout(ToolExecutionError):  # noqa: N818
    code = "tool_timeout"
    http_status = 504


class LLMError(AegisError):
    code = "llm_error"
    http_status = 502


class LLMUnavailable(LLMError):  # noqa: N818
    code = "llm_unavailable"
    http_status = 503


class LLMMalformedOutput(LLMError):  # noqa: N818
    code = "llm_malformed_output"


class FlowDefinitionError(AegisError):
    code = "flow_definition_error"


class PolicyDefinitionError(AegisError):
    code = "policy_definition_error"


class InfrastructureError(AegisError):
    code = "infrastructure_error"
    http_status = 503
