"""Structured JSON logging with secret redaction and correlation context."""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import Mapping, MutableMapping
from typing import Any

import structlog

REDACTED = "[REDACTED]"
_SECRET_KEYS = re.compile(
    r"(api[_-]?key|authorization|password|passwd|secret|token|credential|cookie|set-cookie)",
    re.IGNORECASE,
)
_SECRET_VALUES = re.compile(r"\b(sk-[A-Za-z0-9_\-]{8,}|Bearer\s+[A-Za-z0-9._\-]+)")
# A secret carried as a query-string or form pair: ...?key=abc&password=hunter2
_SECRET_PAIRS = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|auth|credential|key|passwd|password|secret|token)"
    r"=([^&\s\"'\\]+)"
)
# Credentials embedded in a connection string: postgresql://user:password@host/db
_DSN_CREDENTIALS = re.compile(r"(?P<scheme>[a-zA-Z][\w+.\-]*://)(?P<user>[^:/@\s]*):[^@/\s]+@")


def redact_url(url: str) -> str:
    """Redact secrets in a URL, including the query string, for logs and span attributes."""
    result: str = _SECRET_PAIRS.sub(rf"\g<1>={REDACTED}", url)
    return _DSN_CREDENTIALS.sub(rf"\g<scheme>\g<user>:{REDACTED}@", result)


def redact_value(value: Any) -> Any:
    if isinstance(value, str):
        cleaned = _SECRET_PAIRS.sub(rf"\g<1>={REDACTED}", value)
        cleaned = _SECRET_VALUES.sub(REDACTED, cleaned)
        return _DSN_CREDENTIALS.sub(rf"\g<scheme>\g<user>:{REDACTED}@", cleaned)
    if isinstance(value, Mapping):
        return {
            k: (REDACTED if _SECRET_KEYS.search(str(k)) else redact_value(v))
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return [redact_value(v) for v in value]
    return value


def redact_processor(
    _logger: logging.Logger, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    for key in list(event_dict.keys()):
        if _SECRET_KEYS.search(key):
            event_dict[key] = REDACTED
        else:
            event_dict[key] = redact_value(event_dict[key])
    return event_dict


def configure_logging(level: str = "INFO", fmt: str = "json", service: str = "aegis") -> None:
    shared: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True, key="timestamp"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        redact_processor,
        _add_service(service),
    ]
    renderer: Any = (
        structlog.processors.JSONRenderer()
        if fmt == "json"
        else structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
    )
    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared,
        processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, renderer],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    for noisy in ("uvicorn.access", "httpx", "httpcore", "temporalio", "openai", "asyncio"):
        logging.getLogger(noisy).setLevel(max(logging.WARNING, logging.getLevelName(level)))


def _add_service(service: str) -> Any:
    def processor(
        _logger: logging.Logger, _method: str, event_dict: MutableMapping[str, Any]
    ) -> MutableMapping[str, Any]:
        event_dict.setdefault("service", service)
        return event_dict

    return processor


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger


def bind_context(**values: Any) -> None:
    """Bind correlation identifiers (incident_id, workflow_id…) to the current context."""
    structlog.contextvars.bind_contextvars(**{k: v for k, v in values.items() if v is not None})


def clear_context() -> None:
    structlog.contextvars.clear_contextvars()
