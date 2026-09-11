from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from aegis.domain.errors import InfrastructureError
from aegis.infrastructure.prometheus.provider import PrometheusTelemetryProvider
from aegis.infrastructure.simulator.inprocess import InProcessSimulatorTelemetry
from aegis.simulator.engine import SimulationEngine


@pytest.fixture
def provider() -> PrometheusTelemetryProvider:
    engine = SimulationEngine(seed=1)
    engine.warmup(60)
    client = httpx.AsyncClient(base_url="http://prom:9090")
    return PrometheusTelemetryProvider(
        "http://prom:9090", fallback=InProcessSimulatorTelemetry(engine), client=client
    )


@respx.mock
async def test_metrics_query_uses_service_or_component_selector(
    provider: PrometheusTelemetryProvider,
) -> None:
    now = datetime.now(tz=UTC)
    route = respx.get("http://prom:9090/api/v1/query_range").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "resultType": "matrix",
                    "result": [
                        {
                            "metric": {},
                            "values": [[now.timestamp() - 10, "120.5"], [now.timestamp(), "130.0"]],
                        }
                    ],
                },
            },
        )
    )
    series = await provider.metrics(
        "api-gateway", "latency_p95_ms", start=now - timedelta(minutes=1), end=now
    )
    assert [s.value for s in series.samples] == [120.5, 130.0] and series.unit == "ms"
    assert (
        'sim_service_latency_p95_ms{service="api-gateway"}'
        in route.calls.last.request.url.params["query"]
    )
    await provider.metrics("redis", "connections", start=now - timedelta(minutes=1), end=now)
    assert (
        'sim_infra_connections{component="redis"}' in route.calls.last.request.url.params["query"]
    )


@respx.mock
async def test_prometheus_errors_are_typed_and_baseline_falls_back(
    provider: PrometheusTelemetryProvider,
) -> None:
    respx.get("http://prom:9090/api/v1/query_range").mock(
        return_value=httpx.Response(500, text="boom")
    )
    now = datetime.now(tz=UTC)
    with pytest.raises(InfrastructureError):
        await provider.metrics(
            "api-gateway", "error_rate", start=now - timedelta(minutes=1), end=now
        )
    baseline = await provider.baseline("api-gateway", "latency_p95_ms")
    assert baseline is not None and baseline > 0  # simulator fallback
    assert (await provider.health("api-gateway")).service == "api-gateway"
    assert len(await provider.snapshot(window_seconds=30)) > 50


@respx.mock
async def test_baseline_is_median_of_previous_hour(provider: PrometheusTelemetryProvider) -> None:
    now = datetime.now(tz=UTC).timestamp()
    respx.get("http://prom:9090/api/v1/query_range").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "result": [
                        {
                            "metric": {},
                            "values": [
                                [now - i, str(v)] for i, v in enumerate([10, 12, 11, 100, 9])
                            ],
                        }
                    ]
                },
            },
        )
    )
    assert await provider.baseline("api-gateway", "latency_p95_ms") == 11
