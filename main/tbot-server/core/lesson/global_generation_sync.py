"""Atomically materialize, accept, and fan out a global lesson generation."""

from __future__ import annotations

import asyncio
import inspect
import os
import re
from collections.abc import Awaitable, Callable, Mapping
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any

from core.lesson.sd_pack_materializer import (
    MaterializationError,
    materialize_lesson_sd_pack,
)

_DEFAULT_CONCURRENCY = 3
_MAX_CONCURRENCY = 8
_ERROR_CODE_RE = re.compile(r"[^a-z0-9]+")
_MATERIALIZATION_CODE_RE = re.compile(r"^[A-Za-z0-9 _-]{1,128}$")
_SAFE_FANOUT_COUNT_KEYS = frozenset(
    {
        "deviceCount",
        "failedCount",
        "offlineCount",
        "onlineCount",
        "packCount",
        "queuedCount",
        "skippedCount",
        "syncedCount",
    }
)


class GlobalGenerationSync:
    def __init__(
        self,
        config: Mapping[str, Any],
        store: Any,
        fanout: Callable[[int, str, list[dict[str, Any]]], Awaitable[Any] | Any],
        *,
        materialize: Callable[..., Awaitable[Any] | Any] = materialize_lesson_sd_pack,
        clock: Callable[[], Any] = lambda: datetime.now(timezone.utc),
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    ) -> None:
        self.config = config
        self.store = store
        self.fanout = fanout
        self.materialize = materialize
        self.clock = clock
        self.sleep = sleep
        self._apply_lock = asyncio.Lock()

    async def apply(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        async with self._apply_lock:
            return await self._apply_locked(payload)

    async def _apply_locked(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        generation = payload["generation"]
        index_checksum = payload["indexChecksum"]
        try:
            concurrency = _materialize_concurrency(self.config)
        except ValueError:
            return await self._retry_result(generation, "invalid_materialize_concurrency")

        packs = [_strict_pack(pack) for pack in payload["index"]]
        try:
            await self.store.mark_materializing(generation)
        except asyncio.CancelledError:
            raise
        except Exception:
            return await self._retry_result(generation, "generation_mark_materializing_failed")

        semaphore = asyncio.Semaphore(concurrency)

        async def run(pack: dict[str, Any]) -> Any:
            async with semaphore:
                result = self.materialize(pack, config=self.config)
                if inspect.isawaitable(result):
                    return await result
                return result

        try:
            results = await asyncio.gather(*(run(pack) for pack in packs), return_exceptions=True)
        except asyncio.CancelledError:
            raise

        error_code = _materialization_error_code(results)
        if error_code is not None:
            if any(isinstance(result, asyncio.CancelledError) for result in results):
                raise asyncio.CancelledError
            return await self._retry_result(generation, error_code)

        try:
            await self.store.accept(generation, index_checksum, _utc_timestamp(self.clock()))
        except asyncio.CancelledError:
            raise
        except Exception:
            return await self._retry_result(generation, "generation_accept_failed")

        try:
            fanout_result = self.fanout(generation, index_checksum, packs)
            if inspect.isawaitable(fanout_result):
                fanout_result = await fanout_result
        except asyncio.CancelledError:
            raise
        except Exception:
            return await self._retry_result(generation, "generation_fanout_failed")

        return {
            "state": "ready",
            "generation": generation,
            "indexChecksum": index_checksum,
            "packCount": len(packs),
            "fanout": _safe_fanout_result(fanout_result),
        }

    async def _retry_result(self, generation: int, error_code: str) -> dict[str, Any]:
        stable_code = _normalize_error_code(error_code, fallback="generation_sync_failed")
        attempt = 1
        try:
            snapshot = await self.store.snapshot()
            previous = snapshot.get("retryAttempt") if isinstance(snapshot, Mapping) else None
            if type(previous) is int and previous >= 0:
                attempt = previous + 1
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        delay = min(300, 5 * 2 ** min(attempt - 1, 6))
        try:
            next_retry_at = _utc_timestamp(self.clock(), offset_seconds=delay)
            await self.store.mark_retry(stable_code, attempt, next_retry_at)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        return {
            "state": "retry_wait",
            "errorCode": stable_code,
            "generation": generation,
        }


def _strict_pack(pack: Mapping[str, Any]) -> dict[str, Any]:
    return deepcopy({key: value for key, value in pack.items() if key != "classification"})


def _materialize_concurrency(config: Mapping[str, Any]) -> int:
    raw = os.environ.get("LESSON_GENERATION_MATERIALIZE_CONCURRENCY")
    if raw is None:
        lesson = config.get("lesson", {}) if isinstance(config, Mapping) else {}
        raw = (
            lesson.get("generation_materialize_concurrency", _DEFAULT_CONCURRENCY)
            if isinstance(lesson, Mapping)
            else _DEFAULT_CONCURRENCY
        )
    if type(raw) is bool:
        raise ValueError("invalid materialization concurrency")
    try:
        value = int(str(raw))
    except (TypeError, ValueError):
        raise ValueError("invalid materialization concurrency") from None
    if not 1 <= value <= _MAX_CONCURRENCY:
        raise ValueError("invalid materialization concurrency")
    return value


def _materialization_error_code(results: list[Any]) -> str | None:
    for result in results:
        if isinstance(result, asyncio.CancelledError):
            return "generation_materialization_failed"
        if isinstance(result, MaterializationError):
            return _normalize_materialization_code(result.code)
        if isinstance(result, BaseException):
            return "generation_materialization_failed"
        if not isinstance(result, Mapping):
            return "generation_materialization_invalid_result"
        if (
            result.get("ready") is not True
            or result.get("criticalReady") is not True
            or type(result.get("optionalFailedCount")) is not int
            or result.get("optionalFailedCount") != 0
        ):
            return "generation_materialization_invalid_result"
    return None


def _normalize_error_code(value: Any, *, fallback: str) -> str:
    normalized = _ERROR_CODE_RE.sub("_", str(value).lower()).strip("_")
    if not normalized or not normalized[0].isalnum():
        normalized = fallback
    return normalized[:64].rstrip("_") or fallback


def _normalize_materialization_code(value: Any) -> str:
    if not isinstance(value, str) or _MATERIALIZATION_CODE_RE.fullmatch(value) is None:
        return "generation_materialization_failed"
    return _normalize_error_code(value, fallback="generation_materialization_failed")


def _utc_timestamp(value: Any, *, offset_seconds: int = 0) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock must return a timezone-aware datetime")
    timestamp = value.astimezone(timezone.utc) + timedelta(seconds=offset_seconds)
    return timestamp.isoformat().replace("+00:00", "Z")


def _safe_fanout_result(result: Any) -> dict[str, int]:
    if not isinstance(result, Mapping):
        return {}
    safe: dict[str, int] = {}
    for key in _SAFE_FANOUT_COUNT_KEYS:
        value = result.get(key)
        if type(value) is int and 0 <= value <= 1_000_000:
            safe[key] = value
    packs = result.get("packs")
    if "packCount" not in safe and type(packs) is int and 0 <= packs <= 1_000_000:
        safe["packCount"] = packs
    return safe
