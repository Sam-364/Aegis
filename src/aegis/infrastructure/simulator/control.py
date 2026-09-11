"""Control-plane client for the simulator (scenarios, faults, time control) used by the API."""

from __future__ import annotations

from typing import Any

import httpx

from aegis.domain.errors import InfrastructureError


class SimulatorControlClient:
    def __init__(
        self, base_url: str, client: httpx.AsyncClient | None = None, timeout: float = 15.0
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(base_url=self.base_url, timeout=timeout)

    async def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            r = await self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise InfrastructureError(f"simulator unreachable: {exc}") from exc
        if r.status_code >= 400:
            detail = (
                r.json().get("detail", r.text)
                if "json" in r.headers.get("content-type", "")
                else r.text
            )
            raise InfrastructureError(
                f"simulator error {r.status_code}: {detail}", details={"status": r.status_code}
            )
        return r.json()

    async def health(self) -> dict[str, Any]:
        return dict(await self._call("GET", "/health"))

    async def scenarios(self) -> list[dict[str, Any]]:
        return list((await self._call("GET", "/api/scenarios"))["scenarios"])

    async def faults(self, active_only: bool = True) -> list[dict[str, Any]]:
        return list(
            (await self._call("GET", "/api/faults", params={"active_only": active_only}))["faults"]
        )

    async def inject(
        self, scenario_id: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return dict(
            await self._call(
                "POST", "/api/faults", json={"scenario_id": scenario_id, "params": params or {}}
            )
        )

    async def clear(self, fault_id: str) -> dict[str, Any]:
        return dict(await self._call("DELETE", f"/api/faults/{fault_id}"))

    async def topology(self) -> dict[str, Any]:
        return dict(await self._call("GET", "/api/topology"))

    async def state(self) -> dict[str, Any]:
        return dict(await self._call("GET", "/api/control/state"))

    async def actions(self) -> list[dict[str, Any]]:
        return list((await self._call("GET", "/api/control/actions"))["actions"])

    async def advance(self, seconds: float) -> dict[str, Any]:
        return dict(await self._call("POST", "/api/control/advance", json={"seconds": seconds}))

    async def reset(self, seed: int | None = None) -> dict[str, Any]:
        return dict(
            await self._call(
                "POST", "/api/control/reset", json={"seed": seed, "warmup_seconds": 300}
            )
        )

    async def metrics(
        self, component: str, metric: str, start: str | None = None, end: str | None = None
    ) -> dict[str, Any]:
        params = {
            "metric": metric,
            **({"start": start} if start else {}),
            **({"end": end} if end else {}),
        }
        return dict(await self._call("GET", f"/api/components/{component}/metrics", params=params))

    async def current(self, component: str) -> dict[str, Any]:
        return dict(await self._call("GET", f"/api/components/{component}/current"))

    async def baseline(self, component: str, metric: str) -> dict[str, Any]:
        return dict(await self._call("GET", f"/api/components/{component}/baseline/{metric}"))

    async def health_of(self, component: str) -> dict[str, Any]:
        return dict(await self._call("GET", f"/api/components/{component}/health"))

    async def aclose(self) -> None:
        await self._client.aclose()
