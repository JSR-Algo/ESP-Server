"""Durable pending work store for lesson SD-pack fanout."""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from typing import Any, Protocol, TypedDict

LESSON_SD_PENDING_TTL_SEC = 30 * 24 * 60 * 60


class PendingLessonSdWork(TypedDict):
    cacheKeys: list[str]
    attemptCount: int
    createdAt: str
    nextAttemptAt: str


class LessonSdPendingStore(Protocol):
    async def mark(self, device_id: str, cache_keys: Iterable[str] | None = None) -> None: ...
    async def load(self, device_id: str) -> PendingLessonSdWork | None: ...
    async def due(self, *, limit: int) -> list[str]: ...
    async def clear(self, device_id: str, cache_keys: Iterable[str]) -> None: ...
    async def snapshot(self) -> dict[str, PendingLessonSdWork]: ...


def utc_iso(epoch: float) -> str:
    return datetime.fromtimestamp(float(epoch), timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def epoch_from_iso(value: str) -> float:
    if not isinstance(value, str) or not value:
        return 0.0
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def normalize_cache_keys(cache_keys: Iterable[str] | None) -> list[str]:
    if cache_keys is None:
        return []
    return sorted({str(key).strip() for key in cache_keys if str(key).strip()})


def _retry_delay(attempt_count: int, random_fn: Callable[[], float]) -> float:
    attempt = max(1, int(attempt_count or 1))
    base = min(3600.0, float(2 ** (attempt - 1)))
    jitter = max(0.0, min(1.0, float(random_fn())))
    return min(3600.0, base + (base * 0.25 * jitter))


def _pending_key(namespace: str, device_id: str) -> str:
    return f"{namespace}:lesson-sd-pending:{device_id}"


def _due_key(namespace: str) -> str:
    return f"{namespace}:lesson-sd-pending:due"


class InMemoryLessonSdPendingStore:
    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.time,
        random: Callable[[], float] = lambda: 0.0,
    ) -> None:
        self._clock = clock
        self._random = random
        self._items: dict[str, PendingLessonSdWork] = {}
        self._lock = asyncio.Lock()

    async def mark(self, device_id: str, cache_keys: Iterable[str] | None = None) -> None:
        device_id = _normalize_device_id(device_id)
        if not device_id:
            return
        keys = normalize_cache_keys(cache_keys)
        async with self._lock:
            existing = self._items.get(device_id)
            now = self._clock()
            attempt_count = int((existing or {}).get("attemptCount") or 0) + 1
            created_at = (existing or {}).get("createdAt") or utc_iso(now)
            merged = normalize_cache_keys([*((existing or {}).get("cacheKeys") or []), *keys])
            self._items[device_id] = {
                "cacheKeys": merged,
                "attemptCount": attempt_count,
                "createdAt": created_at,
                "nextAttemptAt": utc_iso(now + _retry_delay(attempt_count, self._random)),
            }

    async def load(self, device_id: str) -> PendingLessonSdWork | None:
        device_id = _normalize_device_id(device_id)
        if not device_id:
            return None
        async with self._lock:
            item = self._items.get(device_id)
            return _copy_work(item) if item is not None else None

    async def due(self, *, limit: int) -> list[str]:
        limit = max(0, int(limit or 0))
        if limit <= 0:
            return []
        now = self._clock()
        async with self._lock:
            due = [
                (device_id, epoch_from_iso(work["nextAttemptAt"]))
                for device_id, work in self._items.items()
                if epoch_from_iso(work["nextAttemptAt"]) <= now
            ]
        return [device_id for device_id, _ in sorted(due, key=lambda item: (item[1], item[0]))[:limit]]

    async def clear(self, device_id: str, cache_keys: Iterable[str]) -> None:
        device_id = _normalize_device_id(device_id)
        keys = set(normalize_cache_keys(cache_keys))
        if not device_id or not keys:
            return
        async with self._lock:
            existing = self._items.get(device_id)
            if existing is None:
                return
            remaining = [key for key in existing["cacheKeys"] if key not in keys]
            if remaining:
                existing["cacheKeys"] = remaining
            else:
                self._items.pop(device_id, None)

    async def snapshot(self) -> dict[str, PendingLessonSdWork]:
        async with self._lock:
            return {device_id: _copy_work(work) for device_id, work in sorted(self._items.items())}

    def metrics(self) -> dict[str, int]:
        if not self._items:
            return {"lesson_sd_pending_age_seconds": 0}
        oldest = min(epoch_from_iso(work["createdAt"]) for work in self._items.values())
        return {"lesson_sd_pending_age_seconds": max(0, int(self._clock() - oldest))}


class RedisLessonSdPendingStore:
    def __init__(
        self,
        redis: Any,
        *,
        namespace: str = "prod",
        ttl_sec: int = LESSON_SD_PENDING_TTL_SEC,
        clock: Callable[[], float] = time.time,
        random: Callable[[], float] = lambda: 0.0,
    ) -> None:
        self.redis = redis
        self.namespace = str(namespace or "prod")
        self.ttl_sec = max(60, int(ttl_sec or LESSON_SD_PENDING_TTL_SEC))
        self._clock = clock
        self._random = random

    async def mark(self, device_id: str, cache_keys: Iterable[str] | None = None) -> None:
        device_id = _normalize_device_id(device_id)
        if not device_id:
            return
        key = self._key(device_id)
        existing = await self.load(device_id)
        now = self._clock()
        attempt_count = int((existing or {}).get("attemptCount") or 0) + 1
        created_at = (existing or {}).get("createdAt") or utc_iso(now)
        merged = normalize_cache_keys([*((existing or {}).get("cacheKeys") or []), *normalize_cache_keys(cache_keys)])
        work: PendingLessonSdWork = {
            "cacheKeys": merged,
            "attemptCount": attempt_count,
            "createdAt": created_at,
            "nextAttemptAt": utc_iso(now + _retry_delay(attempt_count, self._random)),
        }
        await self.redis.set(key, json.dumps(work, separators=(",", ":")), ex=self.ttl_sec)
        await self.redis.zadd(self._due_key(), {device_id: epoch_from_iso(work["nextAttemptAt"])})
        await self.redis.expire(self._due_key(), self.ttl_sec)

    async def load(self, device_id: str) -> PendingLessonSdWork | None:
        device_id = _normalize_device_id(device_id)
        if not device_id:
            return None
        raw = await self.redis.get(self._key(device_id))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return None
        if not isinstance(parsed, dict):
            return None
        return _sanitize_work(parsed)

    async def due(self, *, limit: int) -> list[str]:
        limit = max(0, int(limit or 0))
        if limit <= 0:
            return []
        members = await self.redis.zrangebyscore(
            self._due_key(), "-inf", self._clock(), start=0, num=limit
        )
        return [_decode_member(member) for member in members if _decode_member(member)]

    async def clear(self, device_id: str, cache_keys: Iterable[str]) -> None:
        device_id = _normalize_device_id(device_id)
        keys = set(normalize_cache_keys(cache_keys))
        if not device_id or not keys:
            return
        existing = await self.load(device_id)
        if existing is None:
            return
        remaining = [key for key in existing["cacheKeys"] if key not in keys]
        if not remaining:
            await self.redis.delete(self._key(device_id))
            await self.redis.zrem(self._due_key(), device_id)
            return
        existing["cacheKeys"] = remaining
        await self.redis.set(self._key(device_id), json.dumps(existing, separators=(",", ":")), ex=self.ttl_sec)
        await self.redis.zadd(self._due_key(), {device_id: epoch_from_iso(existing["nextAttemptAt"])})
        await self.redis.expire(self._due_key(), self.ttl_sec)

    async def snapshot(self) -> dict[str, PendingLessonSdWork]:
        members = await self.redis.zrangebyscore(
            self._due_key(), "-inf", "+inf", start=0, num=10000
        )
        device_ids = [_decode_member(member) for member in members if _decode_member(member)]
        out: dict[str, PendingLessonSdWork] = {}
        for device_id in sorted(device_ids):
            work = await self.load(device_id)
            if work is not None:
                out[device_id] = work
        return out

    def metrics(self) -> dict[str, int]:
        return {"lesson_sd_pending_age_seconds": 0}

    def _key(self, device_id: str) -> str:
        return _pending_key(self.namespace, device_id)

    def _due_key(self) -> str:
        return _due_key(self.namespace)


def create_lesson_sd_pending_store() -> LessonSdPendingStore:
    redis_url = os.getenv("REDIS_URL")
    allow_memory = os.getenv("LESSON_SD_PENDING_ALLOW_MEMORY", "").strip().lower() == "true"
    env = (
        os.getenv("ENV")
        or os.getenv("APP_ENV")
        or os.getenv("PYTHON_ENV")
        or os.getenv("NODE_ENV")
        or ""
    ).strip().lower()
    if redis_url:
        from redis import asyncio as redis_asyncio

        return RedisLessonSdPendingStore(
            redis_asyncio.from_url(redis_url, decode_responses=True),
            namespace=os.getenv("TBOT_LIVE_REDIS_NAMESPACE", "prod"),
            ttl_sec=_int_env("LESSON_SD_PENDING_TTL_SEC", LESSON_SD_PENDING_TTL_SEC),
        )
    if env in {"prod", "production"} and not allow_memory:
        raise RuntimeError("Redis is required for durable lesson SD pending work in production")
    return InMemoryLessonSdPendingStore()


def _normalize_device_id(device_id: str) -> str:
    return str(device_id or "").strip()


def _copy_work(work: PendingLessonSdWork) -> PendingLessonSdWork:
    return {
        "cacheKeys": list(work["cacheKeys"]),
        "attemptCount": int(work["attemptCount"]),
        "createdAt": str(work["createdAt"]),
        "nextAttemptAt": str(work["nextAttemptAt"]),
    }


def _sanitize_work(value: dict[str, Any]) -> PendingLessonSdWork:
    created_at = str(value.get("createdAt") or utc_iso(time.time()))
    next_attempt_at = str(value.get("nextAttemptAt") or created_at)
    try:
        attempt_count = int(value.get("attemptCount") or 0)
    except (TypeError, ValueError):
        attempt_count = 0
    return {
        "cacheKeys": normalize_cache_keys(value.get("cacheKeys") or []),
        "attemptCount": max(0, attempt_count),
        "createdAt": created_at,
        "nextAttemptAt": next_attempt_at,
    }


def _decode_member(member: Any) -> str:
    if isinstance(member, bytes):
        return member.decode("utf-8")
    return str(member or "").strip()


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default
