"""Durable authoritative Course Mode snapshots keyed by device assignment."""

from __future__ import annotations

import copy
import json
import os
from typing import Any


class MemoryCourseModeSnapshotStore:
    def __init__(self) -> None:
        self._snapshots: dict[tuple[str, str], dict[str, Any]] = {}

    async def store(self, device_id: str, assignment_id: str, snapshot: dict[str, Any]) -> None:
        self._snapshots[(device_id, assignment_id)] = copy.deepcopy(snapshot)

    async def load(self, device_id: str, assignment_id: str) -> dict[str, Any] | None:
        value = self._snapshots.get((device_id, assignment_id))
        return copy.deepcopy(value) if value is not None else None

    async def clear(self, device_id: str, assignment_id: str) -> None:
        self._snapshots.pop((device_id, assignment_id), None)


class RedisCourseModeSnapshotStore:
    def __init__(
        self, *, url: str, namespace: str = "prod", ttl_sec: int = 24 * 60 * 60,
        client: Any = None,
    ) -> None:
        self.url = url
        self.namespace = namespace or "prod"
        self.ttl_sec = max(60, int(ttl_sec))
        self._client = client

    @classmethod
    def from_env(cls) -> "RedisCourseModeSnapshotStore | None":
        url = os.getenv("REDIS_URL")
        if not url:
            return None
        try:
            ttl_sec = int(os.getenv("LESSON_COURSE_MODE_SNAPSHOT_TTL_SEC", str(24 * 60 * 60)))
        except ValueError:
            ttl_sec = 24 * 60 * 60
        return cls(
            url=url,
            namespace=os.getenv("TBOT_LIVE_REDIS_NAMESPACE", "prod"),
            ttl_sec=ttl_sec,
        )

    async def store(self, device_id: str, assignment_id: str, snapshot: dict[str, Any]) -> None:
        redis = await self._redis()
        await redis.set(
            self._key(device_id, assignment_id),
            json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")),
            ex=self.ttl_sec,
        )

    async def load(self, device_id: str, assignment_id: str) -> dict[str, Any] | None:
        redis = await self._redis()
        raw = await redis.get(self._key(device_id, assignment_id))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        value = json.loads(raw)
        return value if isinstance(value, dict) else None

    async def clear(self, device_id: str, assignment_id: str) -> None:
        redis = await self._redis()
        await redis.delete(self._key(device_id, assignment_id))

    async def _redis(self) -> Any:
        if self._client is not None:
            return self._client
        from redis import asyncio as redis_asyncio

        self._client = redis_asyncio.from_url(self.url, decode_responses=True)
        return self._client

    def _key(self, device_id: str, assignment_id: str) -> str:
        return f"{self.namespace}:course-mode-snapshot:{device_id}:{assignment_id}"


_DEFAULT_STORE: Any = None


def get_course_mode_snapshot_store() -> Any:
    global _DEFAULT_STORE
    if _DEFAULT_STORE is None:
        _DEFAULT_STORE = RedisCourseModeSnapshotStore.from_env() or MemoryCourseModeSnapshotStore()
    return _DEFAULT_STORE
