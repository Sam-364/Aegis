"""Request id, logging context, metrics and rate limiting middleware."""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from aegis.logging import bind_context, clear_context, get_logger
from aegis.ports.messaging import RateLimiter
from aegis.telemetry.metrics import HTTP_LATENCY_SECONDS, HTTP_REQUESTS_TOTAL

log = get_logger("aegis.api")


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        request.state.request_id = request_id
        bind_context(request_id=request_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            elapsed = time.perf_counter() - started
            route = request.scope.get("route")
            path = getattr(route, "path", request.url.path)
            clear_context()
        response.headers["x-request-id"] = request_id
        HTTP_REQUESTS_TOTAL.labels(request.method, path, str(response.status_code)).inc()
        HTTP_LATENCY_SECONDS.labels(request.method, path).observe(elapsed)
        if not path.startswith(("/metrics", "/health", "/ready")):
            log.info(
                "http.request",
                method=request.method,
                path=request.url.path,
                status=response.status_code,
                ms=round(elapsed * 1000, 1),
            )
        return response


# Paths that stream for minutes: counting them against a per-minute budget would close the
# connection. Matched on the resolved route, never on a substring of the URL.
STREAMING_ROUTES = frozenset(
    {
        "/api/v1/incidents/{incident_id}/stream",
        "/api/v1/events/stream",
    }
)
UNMETERED_PREFIXES = ("/metrics", "/health", "/ready")
MAX_TRACKED_CLIENTS = 4096


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Fixed-window limiting, keyed by client address rather than by a client-supplied header.

    Keying on the API key would let an attacker mint a fresh bucket per request simply by varying
    the header, which is exactly the traffic the limit exists to stop.
    """

    def __init__(self, app: object, limiter: RateLimiter, per_minute: int) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self.limiter = limiter
        self.per_minute = per_minute

    def _limiter_for(self, request: Request) -> RateLimiter:
        # The process-wide Redis limiter is only available once the lifespan has built it.
        shared = getattr(request.app.state, "rate_limiter", None)
        return shared if shared is not None else self.limiter

    async def _allow(self, request: Request, key: str) -> tuple[bool, int]:
        """Ask the shared limiter, and fall back to this process's own if it cannot answer.

        A rate limit is a protection, not a correctness requirement: when Redis is down, reads
        must keep working. The local limiter still caps a flood, just per API replica instead of
        across the fleet.
        """
        limiter = self._limiter_for(request)
        try:
            return await limiter.allow(f"api:{key}", self.per_minute, 60)
        except Exception as exc:  # any backend failure degrades to local counting
            if limiter is not self.limiter:
                log.warning("api.rate_limiter_unavailable", error=str(exc))
                return await self.limiter.allow(f"api:{key}", self.per_minute, 60)
            return True, self.per_minute

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        route = request.scope.get("route")
        route_path = getattr(route, "path", None)
        if request.url.path.startswith(UNMETERED_PREFIXES) or route_path in STREAMING_ROUTES:
            return await call_next(request)
        key = request.client.host if request.client else "anonymous"
        allowed, remaining = await self._allow(request, key)
        if not allowed:
            return Response(
                status_code=429,
                content='{"type":"rate_limited","title":"too many requests","status":429}',
                media_type="application/problem+json",
                headers={"retry-after": "60"},
            )
        response = await call_next(request)
        response.headers["x-ratelimit-remaining"] = str(remaining)
        return response
