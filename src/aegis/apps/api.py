"""API process entrypoint."""

from __future__ import annotations

import uvicorn

from aegis.api.app import create_app
from aegis.config import get_settings


def build() -> object:
    return create_app(get_settings())


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "aegis.apps.api:build",
        factory=True,
        host=settings.api_host,
        port=settings.api_port,
        log_level=settings.log_level.lower(),
        access_log=False,
        proxy_headers=True,
        timeout_graceful_shutdown=20,
    )


if __name__ == "__main__":
    main()
