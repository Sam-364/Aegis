"""Redis-backed implementations of the messaging and baseline ports."""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import redis.asyncio as redis

from aegis.domain.incident import IncidentEvent
from aegis.logging import get_logger

log = get_logger(__name__)

CHANNEL_ALL = "aegis:events"


def channel_for(incident_id: uuid.UUID) -> str:
    return f"aegis:events:{incident_id}"


class RedisEventPublisher:
    """Publishes incident events to a global channel and a per-incident channel."""

    def __init__(self, client: redis.Redis) -> None:
        self.client = client

    async def publish(self, event: IncidentEvent) -> None:
        payload = event.model_dump_json()
        await self.client.publish(CHANNEL_ALL, payload)
        await self.client.publish(channel_for(event.incident_id), payload)

    async def subscribe(self, incident_id: uuid.UUID | None = None) -> AsyncIterator[IncidentEvent]:
        pubsub = self.client.pubsub()
        channel = channel_for(incident_id) if incident_id else CHANNEL_ALL
        await pubsub.subscribe(channel)
        try:
            while True:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if message is None:
                    await asyncio.sleep(0)
                    continue
                if message.get("type") != "message":
                    continue
                try:
                    yield IncidentEvent.model_validate_json(message["data"])
                except ValueError as exc:  # pragma: no cover - defensive
                    log.warning("events.bad_payload", error=str(exc))
        finally:
            with contextlib.suppress(Exception):
                await pubsub.unsubscribe(channel)
                await pubsub.aclose()  # type: ignore[no-untyped-call]


class RedisLeaderLock:
    def __init__(self, client: redis.Redis, *, prefix: str = "aegis:lock:") -> None:
        self.client = client
        self.prefix = prefix
        self.token = uuid.uuid4().hex

    async def acquire(self, name: str, ttl: timedelta) -> bool:
        ok = await self.client.set(
            self.prefix + name, self.token, nx=True, px=int(ttl.total_seconds() * 1000)
        )
        return bool(ok)

    async def renew(self, name: str, ttl: timedelta) -> bool:
        script = (
            "if redis.call('get', KEYS[1]) == ARGV[1] then "
            "return redis.call('pexpire', KEYS[1], ARGV[2]) else return 0 end"
        )
        result = await self.client.eval(
            script,
            1,
            self.prefix + name,
            self.token,
            int(ttl.total_seconds() * 1000),
        )
        return bool(result)

    async def release(self, name: str) -> None:
        script = (
            "if redis.call('get', KEYS[1]) == ARGV[1] then "
            "return redis.call('del', KEYS[1]) else return 0 end"
        )
        await self.client.eval(script, 1, self.prefix + name, self.token)


class RedisRateLimiter:
    """Fixed-window counter per key; good enough for API protection and cheap."""

    def __init__(self, client: redis.Redis, *, prefix: str = "aegis:rate:") -> None:
        self.client = client
        self.prefix = prefix

    async def allow(self, key: str, limit: int, window_seconds: int) -> tuple[bool, int]:
        redis_key = f"{self.prefix}{key}:{window_seconds}"
        count = await self.client.incr(redis_key)
        if count == 1:
            await self.client.expire(redis_key, window_seconds)
        remaining = max(0, limit - int(count))
        return int(count) <= limit, remaining


class RedisBaselineStore:
    def __init__(self, client: redis.Redis, *, prefix: str = "aegis:baseline:") -> None:
        self.client = client
        self.prefix = prefix

    async def load(self, key: str) -> dict[str, Any] | None:
        raw = await self.client.get(self.prefix + key)
        if raw is None:
            return None
        data: dict[str, Any] = json.loads(raw)
        return data

    async def save(self, key: str, state: dict[str, Any], ttl_seconds: int) -> None:
        await self.client.set(self.prefix + key, json.dumps(state), ex=ttl_seconds)

    async def keys(self, prefix: str) -> list[str]:
        out: list[str] = []
        async for k in self.client.scan_iter(match=f"{self.prefix}{prefix}*"):
            out.append(k.decode() if isinstance(k, bytes) else str(k))
        return out


def build_redis(url: str) -> redis.Redis:
    return redis.from_url(
        url,
        decode_responses=False,
        socket_connect_timeout=5,
        socket_timeout=5,
        health_check_interval=30,
    )
