"""Entrypoint for the simulated-infrastructure process."""

from __future__ import annotations

import uvicorn

from aegis.config import get_settings
from aegis.logging import configure_logging
from aegis.simulator.api import create_app
from aegis.simulator.engine import SimulationEngine


def build() -> object:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format, service="aegis-simulator")
    engine = SimulationEngine(
        seed=settings.simulator_seed, tick_seconds=settings.simulator_tick_seconds
    )
    return create_app(engine, realtime=True, warmup_seconds=300.0)


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "aegis.apps.simulator:build",
        factory=True,
        host="0.0.0.0",  # noqa: S104
        port=settings.simulator_port,
        log_level=settings.log_level.lower(),
        access_log=False,
    )


if __name__ == "__main__":
    main()
