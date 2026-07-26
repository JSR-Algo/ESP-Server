"""Durable global lesson asset generation state."""

from __future__ import annotations

import os
import re
from typing import Any

GLOBAL_GENERATION_KEY = "lesson-assets:global-generation:v1"

_SNAPSHOT_KEYS = [
    "desiredGeneration",
    "desiredIndexChecksum",
    "etag",
    "acceptedGeneration",
    "acceptedIndexChecksum",
    "materializationState",
    "retryAttempt",
    "nextRetryAt",
    "lastPollAt",
    "lastMaterializedAt",
    "lastErrorCode",
]
_NUMERIC_FIELDS = {"desiredGeneration", "acceptedGeneration", "retryAttempt"}
_CHECKSUM_RE = re.compile(r"^[0-9a-f]{64}$")
_ERROR_CODE_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,63}$")


class GlobalGenerationStore:
    def __init__(self, redis: Any, *, key: str = GLOBAL_GENERATION_KEY) -> None:
        self.redis = redis
        self.key = key

    async def snapshot(self) -> dict[str, int | str | None]:
        raw = await self.redis.hgetall(self.key)
        decoded = {_decode(field): _decode(value) for field, value in dict(raw or {}).items()}
        return {field: _snapshot_value(field, decoded.get(field)) for field in _SNAPSHOT_KEYS}

    async def set_desired(self, generation: int, index_checksum: str, etag: str) -> None:
        _validate_generation(generation)
        _validate_checksum(index_checksum)
        _validate_nonempty_string(etag, "etag")
        await self.redis.hset(
            self.key,
            mapping={
                "desiredGeneration": str(generation),
                "desiredIndexChecksum": index_checksum,
                "etag": etag,
            },
        )

    async def mark_materializing(self, generation: int) -> None:
        _validate_generation(generation)
        await self.redis.hset(
            self.key,
            mapping={
                "desiredGeneration": str(generation),
                "materializationState": "materializing",
            },
        )

    async def accept(self, generation: int, index_checksum: str, accepted_at: str) -> None:
        _validate_generation(generation)
        _validate_checksum(index_checksum)
        _validate_nonempty_string(accepted_at, "accepted_at")
        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.hset(
                self.key,
                mapping={
                    "acceptedGeneration": str(generation),
                    "acceptedIndexChecksum": index_checksum,
                    "materializationState": "ready",
                    "lastMaterializedAt": accepted_at,
                    "retryAttempt": "0",
                },
            )
            pipe.hdel(self.key, "nextRetryAt", "lastErrorCode")
            await pipe.execute()

    async def mark_retry(self, error_code: str, attempt: int, next_retry_at: str) -> None:
        _validate_error_code(error_code)
        _validate_retry_attempt(attempt)
        _validate_nonempty_string(next_retry_at, "next_retry_at")
        await self.redis.hset(
            self.key,
            mapping={
                "materializationState": "retry_wait",
                "lastErrorCode": error_code,
                "retryAttempt": str(attempt),
                "nextRetryAt": next_retry_at,
            },
        )

    async def mark_polled(self, polled_at: str) -> None:
        _validate_nonempty_string(polled_at, "polled_at")
        await self.redis.hset(self.key, mapping={"lastPollAt": polled_at})


def create_global_generation_store() -> GlobalGenerationStore:
    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        raise RuntimeError("REDIS_URL is required for global lesson asset generation state")
    from redis import asyncio as redis_asyncio

    return GlobalGenerationStore(redis_asyncio.from_url(redis_url, decode_responses=True))


def _snapshot_value(field: str, value: str | None) -> int | str | None:
    if value is None:
        return None
    if field not in _NUMERIC_FIELDS:
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _decode(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _validate_generation(value: int) -> None:
    if type(value) is not int or value <= 0:
        raise ValueError("generation must be a positive integer")


def _validate_retry_attempt(value: int) -> None:
    if type(value) is not int or value < 0:
        raise ValueError("retry attempt must be a non-negative integer")


def _validate_checksum(value: str) -> None:
    if not isinstance(value, str) or not _CHECKSUM_RE.fullmatch(value):
        raise ValueError("index checksum must be lower-case 64-character hex")


def _validate_error_code(value: str) -> None:
    if not isinstance(value, str) or not _ERROR_CODE_RE.fullmatch(value):
        raise ValueError("error code must be stable lower-case snake-case")


def _validate_nonempty_string(value: str, name: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
