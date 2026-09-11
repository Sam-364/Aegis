"""Uniform error responses (problem+json shaped)."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from aegis.domain.errors import AegisError
from aegis.logging import get_logger

log = get_logger(__name__)


def _problem(
    request: Request, *, type_: str, title: str, status: int, detail: dict[str, Any] | None = None
) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={
            "type": type_,
            "title": title,
            "status": status,
            "detail": detail or {},
            "request_id": getattr(request.state, "request_id", None),
        },
        media_type="application/problem+json",
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AegisError)
    async def aegis_error(request: Request, exc: AegisError) -> JSONResponse:
        return _problem(
            request, type_=exc.code, title=exc.message, status=exc.http_status, detail=exc.details
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return _problem(
            request,
            type_="validation_error",
            title="request validation failed",
            status=422,
            detail={"errors": [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]},
        )

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("api.unhandled", path=request.url.path)
        return _problem(request, type_="internal_error", title="internal server error", status=500)
