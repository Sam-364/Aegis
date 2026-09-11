"""OpenTelemetry setup. Everything is a no-op unless AEGIS_OTEL_ENABLED=true."""

from __future__ import annotations

from typing import Any

from opentelemetry import trace
from opentelemetry.trace import Tracer

from aegis.logging import redact_url

_configured = False


def configure_tracing(*, enabled: bool, endpoint: str, service_name: str, environment: str) -> None:
    global _configured  # noqa: PLW0603 - process-wide provider
    if not enabled or _configured:
        return
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (  # noqa: PLC0415
        OTLPSpanExporter,
    )
    from opentelemetry.sdk.resources import Resource  # noqa: PLC0415
    from opentelemetry.sdk.trace import TracerProvider  # noqa: PLC0415
    from opentelemetry.sdk.trace.export import BatchSpanProcessor  # noqa: PLC0415

    resource = Resource.create(
        {
            "service.name": service_name,
            "deployment.environment": environment,
            "service.namespace": "aegis",
        }
    )
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, insecure=True))
    )
    trace.set_tracer_provider(provider)
    _configured = True


def tracer(name: str = "aegis") -> Tracer:
    return trace.get_tracer(name)


_URL_ATTRIBUTES = ("http.url", "url.full", "http.target", "url.query")


def _redact_span_urls(span: Any, _scope: Any) -> None:
    """Stream routes accept the API key as a query parameter because EventSource cannot set
    headers; without this the key would be exported to the trace backend verbatim."""
    attributes = getattr(span, "attributes", None) or {}
    for name in _URL_ATTRIBUTES:
        value = attributes.get(name)
        if isinstance(value, str) and "=" in value:
            redacted = redact_url(value)
            if redacted != value:
                span.set_attribute(name, redacted)


def instrument_fastapi(app: Any) -> None:
    if not _configured:
        return
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor  # noqa: PLC0415

    FastAPIInstrumentor.instrument_app(app, server_request_hook=_redact_span_urls)


def instrument_sqlalchemy(engine: Any) -> None:
    if not _configured:
        return
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor  # noqa: PLC0415

    SQLAlchemyInstrumentor().instrument(engine=engine.sync_engine)


def instrument_httpx() -> None:
    if not _configured:
        return
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor  # noqa: PLC0415

    HTTPXClientInstrumentor().instrument()


def current_trace_id() -> str | None:
    span = trace.get_current_span()
    ctx = span.get_span_context()
    if ctx is None or not ctx.is_valid:
        return None
    return format(ctx.trace_id, "032x")
