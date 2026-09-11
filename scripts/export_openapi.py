"""Export the OpenAPI document without external dependencies (in-memory container)."""

from __future__ import annotations

import json
from pathlib import Path

from aegis.api.app import create_app
from aegis.application.container import build_container
from aegis.config import Settings
from aegis.infrastructure.simulator.inprocess import (
    InProcessSimulatorGateway,
    InProcessSimulatorTelemetry,
)
from aegis.simulator.engine import SimulationEngine

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    engine = SimulationEngine(seed=1)
    settings = Settings(llm_provider="disabled", _env_file=None)  # type: ignore[call-arg]
    container = build_container(
        settings,
        telemetry=InProcessSimulatorTelemetry(engine),
        infrastructure=InProcessSimulatorGateway(engine),
        root=ROOT,
    )
    app = create_app(settings, container=container)
    out = ROOT / "docs" / "api" / "openapi.json"
    out.write_text(json.dumps(app.openapi(), indent=2))
    print(f"wrote {out} ({len(app.openapi()['paths'])} paths)")


if __name__ == "__main__":
    main()
