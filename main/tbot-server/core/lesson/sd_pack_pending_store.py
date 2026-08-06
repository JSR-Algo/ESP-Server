"""Durable pending work store for lesson SD-pack fanout."""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from contextlib import suppress
from datetime import datetime, timezone
from typing import Any, Protocol, TypedDict

LESSON_SD_PENDING_TTL_SEC = 30 * 24 * 60 * 60
LESSON_SD_PENDING_LEASE_SEC = 60
logger = logging.getLogger(__name__)

_MARK_LUA = """
local existing = redis.call('GET', KEYS[1])
local work = cjson.decode(ARGV[4])
if existing then
  local current = cjson.decode(existing)
  local seen = {}
  local merged = {}
  for _, key in ipairs(current.cacheKeys or {}) do
    if not seen[key] then seen[key] = true; table.insert(merged, key) end
  end
  for _, key in ipairs(cjson.decode(ARGV[2]) or {}) do
    if not seen[key] then seen[key] = true; table.insert(merged, key) end
  end
  table.sort(merged)
  work.cacheKeys = merged
  work.callbackResults = current.callbackResults
  work.createdAt = current.createdAt or work.createdAt
  work.attemptCount = tonumber(current.attemptCount or 0) + 1
end
local encoded = cjson.encode(work)
redis.call('SET', KEYS[1], encoded, 'EX', tonumber(ARGV[7]))
redis.call('ZADD', KEYS[2], tonumber(ARGV[5]), ARGV[1])
redis.call('ZADD', KEYS[3], tonumber(ARGV[6]), ARGV[1])
redis.call('EXPIRE', KEYS[2], tonumber(ARGV[7]))
redis.call('EXPIRE', KEYS[3], tonumber(ARGV[7]))
return encoded
"""

_CLEAR_LUA = """
local existing = redis.call('GET', KEYS[1])
if not existing then
  redis.call('ZREM', KEYS[2], ARGV[1])
  redis.call('ZREM', KEYS[3], ARGV[1])
  return 0
end
local current = cjson.decode(existing)
local clear = {}
for _, key in ipairs(cjson.decode(ARGV[2]) or {}) do clear[key] = true end
local expected = {}
local expected_count = 0
for _, key in ipairs(cjson.decode(ARGV[3]) or {}) do
  if not expected[key] then expected_count = expected_count + 1 end
  expected[key] = true
end
local current_count = 0
local matches_expected = expected_count == 0
for _, key in ipairs(current.cacheKeys or {}) do
  current_count = current_count + 1
  if expected_count > 0 and not expected[key] then matches_expected = false end
end
if expected_count > 0 and current_count ~= expected_count then matches_expected = false end
local remaining = {}
for _, key in ipairs(current.cacheKeys or {}) do
  if not clear[key] then table.insert(remaining, key) end
end
if #remaining == 0 then
  local has_callbacks = false
  for _, _ in pairs(current.callbackResults or {}) do has_callbacks = true; break end
  if matches_expected and not has_callbacks then
    redis.call('DEL', KEYS[1])
    redis.call('ZREM', KEYS[2], ARGV[1])
    redis.call('ZREM', KEYS[3], ARGV[1])
  elseif matches_expected and has_callbacks then
    current.cacheKeys = remaining
    redis.call('SET', KEYS[1], cjson.encode(current), 'EX', tonumber(ARGV[4]))
    redis.call('EXPIRE', KEYS[2], tonumber(ARGV[4]))
    redis.call('EXPIRE', KEYS[3], tonumber(ARGV[4]))
  end
else
  current.cacheKeys = remaining
  redis.call('SET', KEYS[1], cjson.encode(current), 'EX', tonumber(ARGV[4]))
  redis.call('EXPIRE', KEYS[2], tonumber(ARGV[4]))
  redis.call('EXPIRE', KEYS[3], tonumber(ARGV[4]))
end
return 1
"""

_MARK_CALLBACKS_LUA = """
-- lesson-sd-mark-callbacks
local existing = redis.call('GET', KEYS[1])
local work = cjson.decode(ARGV[3])
local additions = cjson.decode(ARGV[2])
if existing then
  local current = cjson.decode(existing)
  local merged = {}
  for key, result in pairs(current.callbackResults or {}) do merged[key] = result end
  for key, result in pairs(additions or {}) do merged[key] = result end
  work.cacheKeys = current.cacheKeys or {}
  work.callbackResults = merged
  work.createdAt = current.createdAt or work.createdAt
  work.attemptCount = tonumber(current.attemptCount or 0) + 1
end
local encoded = cjson.encode(work)
local due_score = tonumber(ARGV[4])
local current_due_score = redis.call('ZSCORE', KEYS[2], ARGV[1])
if current_due_score and tonumber(current_due_score) > due_score then
  due_score = tonumber(current_due_score)
end
redis.call('SET', KEYS[1], encoded, 'EX', tonumber(ARGV[6]))
redis.call('ZADD', KEYS[2], due_score, ARGV[1])
redis.call('ZADD', KEYS[3], tonumber(ARGV[5]), ARGV[1])
redis.call('EXPIRE', KEYS[2], tonumber(ARGV[6]))
redis.call('EXPIRE', KEYS[3], tonumber(ARGV[6]))
return encoded
"""

_CLEAR_CALLBACKS_LUA = """
-- lesson-sd-clear-callbacks
local existing = redis.call('GET', KEYS[1])
if not existing then
  redis.call('ZREM', KEYS[2], ARGV[1])
  redis.call('ZREM', KEYS[3], ARGV[1])
  return 0
end
local current = cjson.decode(existing)
local clear = {}
for _, key in ipairs(cjson.decode(ARGV[2]) or {}) do clear[key] = true end
local expected = cjson.decode(ARGV[3]) or {}
local function callback_equal(left, right)
  if tostring(left.deviceId or '') ~= tostring(right.deviceId or '') then return false end
  if tostring(left.cacheKey or '') ~= tostring(right.cacheKey or '') then return false end
  if tostring(left.errorCode or '') ~= tostring(right.errorCode or '') then return false end
  if (left.ready == true) ~= (right.ready == true) then return false end
  for _, field in ipairs({'downloadedCount', 'skippedCount', 'reusedCount', 'failedCount', 'criticalFailedCount'}) do
    if tonumber(left[field] or 0) ~= tonumber(right[field] or 0) then return false end
  end
  return true
end
local remaining = {}
local remaining_count = 0
for key, result in pairs(current.callbackResults or {}) do
  local should_clear = clear[key] == true
  if should_clear and expected[key] then should_clear = callback_equal(result, expected[key]) end
  if not should_clear then remaining[key] = result; remaining_count = remaining_count + 1 end
end
if remaining_count == 0 then current.callbackResults = nil else current.callbackResults = remaining end
if #(current.cacheKeys or {}) == 0 and remaining_count == 0 then
  redis.call('DEL', KEYS[1])
  redis.call('ZREM', KEYS[2], ARGV[1])
  redis.call('ZREM', KEYS[3], ARGV[1])
else
  redis.call('SET', KEYS[1], cjson.encode(current), 'EX', tonumber(ARGV[4]))
  redis.call('EXPIRE', KEYS[2], tonumber(ARGV[4]))
  redis.call('EXPIRE', KEYS[3], tonumber(ARGV[4]))
end
return 1
"""

_CLAIM_DUE_LUA = """
local members = redis.call('ZRANGEBYSCORE', KEYS[1], '-inf', ARGV[1], 'LIMIT', 0, tonumber(ARGV[2]))
local claimed = {}
for _, member in ipairs(members) do
  local value_key = ARGV[4] .. ':lesson-sd-pending:' .. member
  if redis.call('GET', value_key) then
    redis.call('ZADD', KEYS[1], tonumber(ARGV[3]), member)
    table.insert(claimed, member)
  else
    redis.call('ZREM', KEYS[1], member)
  end
end
return claimed
"""


class _RequiredPendingLessonSdWork(TypedDict):
    cacheKeys: list[str]
    attemptCount: int
    createdAt: str
    nextAttemptAt: str


class PendingLessonSdWork(_RequiredPendingLessonSdWork, total=False):
    callbackResults: dict[str, dict[str, Any]]


class LessonSdPendingStore(Protocol):
    async def mark(self, device_id: str, cache_keys: Iterable[str] | None = None) -> None: ...
    async def mark_callbacks(
        self,
        device_id: str,
        results: Mapping[str, Any] | Iterable[Mapping[str, Any]],
    ) -> None: ...
    async def load(self, device_id: str) -> PendingLessonSdWork | None: ...
    async def due(self, *, limit: int) -> list[str]: ...
    async def claim_due(self, *, limit: int) -> list[str]: ...
    async def clear(
        self,
        device_id: str,
        cache_keys: Iterable[str],
        *,
        expected_cache_keys: Iterable[str] | None = None,
    ) -> None: ...
    async def clear_callbacks(
        self,
        device_id: str,
        cache_keys: Iterable[str],
        *,
        expected_results: Mapping[str, Any] | Iterable[Mapping[str, Any]] | None = None,
    ) -> None: ...
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


def normalize_callback_results(
    results: Mapping[str, Any] | Iterable[Mapping[str, Any]] | None,
) -> dict[str, dict[str, Any]]:
    if results is None:
        return {}
    candidates: Iterable[tuple[Any, Any]]
    if isinstance(results, Mapping) and "cacheKey" not in results:
        candidates = results.items()
    elif isinstance(results, Mapping):
        candidates = [(results.get("cacheKey"), results)]
    else:
        candidates = [
            (result.get("cacheKey"), result)
            for result in results
            if isinstance(result, Mapping)
        ]
    normalized: dict[str, dict[str, Any]] = {}
    for fallback_key, result in candidates:
        if not isinstance(result, Mapping):
            continue
        cache_key = str(result.get("cacheKey") or fallback_key or "").strip()
        if not cache_key:
            continue
        item = copy.deepcopy(dict(result))
        item["cacheKey"] = cache_key
        normalized[cache_key] = item
    return dict(sorted(normalized.items()))


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
        lease_sec: int = 60,
    ) -> None:
        self._clock = clock
        self._random = random
        self.lease_sec = max(1, int(lease_sec or 60))
        self._items: dict[str, PendingLessonSdWork] = {}
        self._lock = asyncio.Lock()

    async def mark(self, device_id: str, cache_keys: Iterable[str] | None = None) -> None:
        device_id = _normalize_device_id(device_id)
        if not device_id:
            return
        keys = normalize_cache_keys(cache_keys)
        async with self._lock:
            existing = self._items.get(device_id)
            existing_work: Mapping[str, Any] = existing if existing is not None else {}
            now = self._clock()
            attempt_count = int(existing_work.get("attemptCount") or 0) + 1
            created_at = existing_work.get("createdAt") or utc_iso(now)
            merged = normalize_cache_keys([*(existing_work.get("cacheKeys") or []), *keys])
            work: PendingLessonSdWork = {
                "cacheKeys": merged,
                "attemptCount": attempt_count,
                "createdAt": created_at,
                "nextAttemptAt": utc_iso(now + _retry_delay(attempt_count, self._random)),
            }
            callbacks = existing_work.get("callbackResults")
            if callbacks:
                work["callbackResults"] = copy.deepcopy(callbacks)
            self._items[device_id] = work

    async def mark_callbacks(
        self,
        device_id: str,
        results: Mapping[str, Any] | Iterable[Mapping[str, Any]],
    ) -> None:
        device_id = _normalize_device_id(device_id)
        additions = normalize_callback_results(results)
        if not device_id or not additions:
            return
        async with self._lock:
            existing = self._items.get(device_id)
            existing_work: Mapping[str, Any] = existing if existing is not None else {}
            now = self._clock()
            attempt_count = int(existing_work.get("attemptCount") or 0) + 1
            callbacks = normalize_callback_results(existing_work.get("callbackResults") or {})
            callbacks.update(additions)
            next_attempt = max(
                now + _retry_delay(attempt_count, self._random),
                epoch_from_iso(existing_work.get("nextAttemptAt") or ""),
            )
            self._items[device_id] = {
                "cacheKeys": list(existing_work.get("cacheKeys") or []),
                "callbackResults": dict(sorted(callbacks.items())),
                "attemptCount": attempt_count,
                "createdAt": existing_work.get("createdAt") or utc_iso(now),
                "nextAttemptAt": utc_iso(next_attempt),
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

    async def claim_due(self, *, limit: int) -> list[str]:
        limit = max(0, int(limit or 0))
        if limit <= 0:
            return []
        now = self._clock()
        lease_until = utc_iso(now + self.lease_sec)
        async with self._lock:
            due = [
                (device_id, epoch_from_iso(work["nextAttemptAt"]))
                for device_id, work in self._items.items()
                if epoch_from_iso(work["nextAttemptAt"]) <= now
            ]
            claimed = [device_id for device_id, _ in sorted(due, key=lambda item: (item[1], item[0]))[:limit]]
            for device_id in claimed:
                self._items[device_id]["nextAttemptAt"] = lease_until
            return claimed

    async def clear(
        self,
        device_id: str,
        cache_keys: Iterable[str],
        *,
        expected_cache_keys: Iterable[str] | None = None,
    ) -> None:
        device_id = _normalize_device_id(device_id)
        keys = set(normalize_cache_keys(cache_keys))
        if not device_id or not keys:
            return
        async with self._lock:
            existing = self._items.get(device_id)
            if existing is None:
                return
            expected = normalize_cache_keys(expected_cache_keys)
            expected_matches = bool(expected) and existing["cacheKeys"] == expected
            remaining = [key for key in existing["cacheKeys"] if key not in keys]
            if remaining:
                existing["cacheKeys"] = remaining
            elif (not expected or expected_matches) and not existing.get("callbackResults"):
                self._items.pop(device_id, None)
            elif not expected or expected_matches:
                existing["cacheKeys"] = []

    async def clear_callbacks(
        self,
        device_id: str,
        cache_keys: Iterable[str],
        *,
        expected_results: Mapping[str, Any] | Iterable[Mapping[str, Any]] | None = None,
    ) -> None:
        device_id = _normalize_device_id(device_id)
        keys = set(normalize_cache_keys(cache_keys))
        if not device_id or not keys:
            return
        async with self._lock:
            existing = self._items.get(device_id)
            if existing is None:
                return
            callbacks = existing.get("callbackResults") or {}
            expected = normalize_callback_results(expected_results)
            remaining = {
                key: value
                for key, value in callbacks.items()
                if key not in keys or (key in expected and value != expected[key])
            }
            if remaining:
                existing["callbackResults"] = remaining
            else:
                existing.pop("callbackResults", None)
                if not existing["cacheKeys"]:
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
        lease_sec: int = 60,
        clock: Callable[[], float] = time.time,
        random: Callable[[], float] = lambda: 0.0,
    ) -> None:
        self.redis = redis
        self.namespace = str(namespace or "prod")
        self.ttl_sec = max(60, int(ttl_sec or LESSON_SD_PENDING_TTL_SEC))
        self.lease_sec = max(1, int(lease_sec or 60))
        self._clock = clock
        self._random = random
        self._standalone_lua_checked = False

    async def mark(self, device_id: str, cache_keys: Iterable[str] | None = None) -> None:
        device_id = _normalize_device_id(device_id)
        if not device_id:
            return
        existing = await self.load(device_id)
        existing_work: Mapping[str, Any] = existing if existing is not None else {}
        now = self._clock()
        attempt_count = int(existing_work.get("attemptCount") or 0) + 1
        created_at = existing_work.get("createdAt") or utc_iso(now)
        merged = normalize_cache_keys([*(existing_work.get("cacheKeys") or []), *normalize_cache_keys(cache_keys)])
        work: PendingLessonSdWork = {
            "cacheKeys": merged,
            "attemptCount": attempt_count,
            "createdAt": created_at,
            "nextAttemptAt": utc_iso(now + _retry_delay(attempt_count, self._random)),
        }
        callbacks = existing_work.get("callbackResults")
        if callbacks:
            work["callbackResults"] = copy.deepcopy(callbacks)
        if not await self._eval_mark(device_id, normalize_cache_keys(cache_keys), work):
            await self._persist(device_id, work)

    async def mark_callbacks(
        self,
        device_id: str,
        results: Mapping[str, Any] | Iterable[Mapping[str, Any]],
    ) -> None:
        device_id = _normalize_device_id(device_id)
        additions = normalize_callback_results(results)
        if not device_id or not additions:
            return
        existing = await self.load(device_id)
        existing_work: Mapping[str, Any] = existing if existing is not None else {}
        now = self._clock()
        attempt_count = int(existing_work.get("attemptCount") or 0) + 1
        callbacks = normalize_callback_results(existing_work.get("callbackResults") or {})
        callbacks.update(additions)
        next_attempt = max(
            now + _retry_delay(attempt_count, self._random),
            epoch_from_iso(existing_work.get("nextAttemptAt") or ""),
            await self._due_score(device_id),
        )
        work: PendingLessonSdWork = {
            "cacheKeys": list(existing_work.get("cacheKeys") or []),
            "callbackResults": dict(sorted(callbacks.items())),
            "attemptCount": attempt_count,
            "createdAt": existing_work.get("createdAt") or utc_iso(now),
            "nextAttemptAt": utc_iso(next_attempt),
        }
        if not await self._eval_mark_callbacks(device_id, additions, work):
            await self._persist(device_id, work)

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
            await self._quarantine(device_id, "undecodable")
            return None
        if not isinstance(parsed, dict):
            await self._quarantine(device_id, "not_an_object")
            return None
        return _sanitize_work(parsed)

    async def _quarantine(self, device_id: str, reason: str) -> None:
        """Drop a record no reader can interpret.

        Leaving it costs more than losing it: ``claim_due`` only drops members
        whose value key is ABSENT, so a truncated crash-mid-write value is
        re-claimed and re-leased every cycle for the whole 30-day TTL while the
        worker skips it — a pending queue that never drains and never reports.
        A dropped record is re-created by the next fanout or reconnect drain.
        """
        logger.warning(
            "lesson_sd_pending_quarantined device_id=%s reason=%s",
            device_id,
            reason,
        )
        with suppress(Exception):
            await self._delete(device_id)

    async def due(self, *, limit: int) -> list[str]:
        limit = max(0, int(limit or 0))
        if limit <= 0:
            return []
        members = await self.redis.zrangebyscore(
            self._due_key(), "-inf", self._clock(), start=0, num=limit
        )
        return [_decode_member(member) for member in members if _decode_member(member)]

    async def claim_due(self, *, limit: int) -> list[str]:
        limit = max(0, int(limit or 0))
        if limit <= 0:
            return []
        lease_score = self._clock() + self.lease_sec
        claimed = await self._eval_claim_due(limit, lease_score)
        if claimed is not None:
            return claimed
        members = await self.redis.zrangebyscore(
            self._due_key(), "-inf", self._clock(), start=0, num=limit
        )
        claimed = []
        for member in members:
            device_id = _decode_member(member)
            if not device_id:
                continue
            if await self.load(device_id) is None:
                await self.redis.zrem(self._due_key(), device_id)
                await self.redis.zrem(self._created_key(), device_id)
                continue
            await self.redis.zadd(self._due_key(), {device_id: lease_score})
            claimed.append(device_id)
        return claimed

    async def clear(
        self,
        device_id: str,
        cache_keys: Iterable[str],
        *,
        expected_cache_keys: Iterable[str] | None = None,
    ) -> None:
        device_id = _normalize_device_id(device_id)
        keys = set(normalize_cache_keys(cache_keys))
        if not device_id or not keys:
            return
        if await self._eval_clear(device_id, keys, normalize_cache_keys(expected_cache_keys)):
            return
        existing = await self.load(device_id)
        if existing is None:
            return
        expected = normalize_cache_keys(expected_cache_keys)
        expected_matches = bool(expected) and existing["cacheKeys"] == expected
        remaining = [key for key in existing["cacheKeys"] if key not in keys]
        if not remaining:
            if expected and not expected_matches:
                return
            if existing.get("callbackResults"):
                existing["cacheKeys"] = []
                await self._persist(device_id, existing)
                return
            await self._delete(device_id)
            return
        existing["cacheKeys"] = remaining
        await self._persist(device_id, existing)

    async def clear_callbacks(
        self,
        device_id: str,
        cache_keys: Iterable[str],
        *,
        expected_results: Mapping[str, Any] | Iterable[Mapping[str, Any]] | None = None,
    ) -> None:
        device_id = _normalize_device_id(device_id)
        keys = set(normalize_cache_keys(cache_keys))
        if not device_id or not keys:
            return
        expected = normalize_callback_results(expected_results)
        if await self._eval_clear_callbacks(device_id, keys, expected):
            return
        existing = await self.load(device_id)
        if existing is None:
            return
        callbacks = existing.get("callbackResults") or {}
        remaining = {
            key: value
            for key, value in callbacks.items()
            if key not in keys or (key in expected and value != expected[key])
        }
        if remaining:
            existing["callbackResults"] = remaining
        else:
            existing.pop("callbackResults", None)
        if not existing["cacheKeys"] and not remaining:
            await self._delete(device_id)
            return
        await self._persist(device_id, existing)

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
        try:
            result: dict[str, int] = _run_coro_sync(self._metrics_async())
            return result
        except Exception as exc:
            logger.warning("lesson SD pending Redis metrics unavailable: %s", type(exc).__name__)
        return {"lesson_sd_pending_age_seconds": 0}

    async def _metrics_async(self) -> dict[str, int]:
        while True:
            oldest = await self.redis.zrange(self._created_key(), 0, 0, withscores=True)
            if not oldest:
                return {"lesson_sd_pending_age_seconds": 0}
            member, score = oldest[0]
            device_id = _decode_member(member)
            if not device_id:
                return {"lesson_sd_pending_age_seconds": 0}
            work = await self.load(device_id)
            if work is None:
                await self.redis.zrem(self._created_key(), device_id)
                await self.redis.zrem(self._due_key(), device_id)
                continue
            try:
                created = float(score)
            except (TypeError, ValueError):
                created = epoch_from_iso(work["createdAt"])
            return {
                "lesson_sd_pending_age_seconds": max(0, int(self._clock() - created))
            }

    def _key(self, device_id: str) -> str:
        return _pending_key(self.namespace, device_id)

    def _due_key(self) -> str:
        return _due_key(self.namespace)

    def _created_key(self) -> str:
        return f"{self.namespace}:lesson-sd-pending:created"

    async def _persist(self, device_id: str, work: PendingLessonSdWork) -> None:
        await self.redis.set(
            self._key(device_id),
            json.dumps(work, separators=(",", ":")),
            ex=self.ttl_sec,
        )
        await self.redis.zadd(self._due_key(), {device_id: epoch_from_iso(work["nextAttemptAt"])})
        await self.redis.zadd(self._created_key(), {device_id: epoch_from_iso(work["createdAt"])})
        await self.redis.expire(self._due_key(), self.ttl_sec)
        await self.redis.expire(self._created_key(), self.ttl_sec)

    async def _due_score(self, device_id: str) -> float:
        zscore = getattr(self.redis, "zscore", None)
        if not callable(zscore):
            return 0.0
        try:
            value = await zscore(self._due_key(), device_id)
            return float(value or 0.0)
        except (TypeError, ValueError):
            return 0.0

    async def _delete(self, device_id: str) -> None:
        await self.redis.delete(self._key(device_id))
        await self.redis.zrem(self._due_key(), device_id)
        await self.redis.zrem(self._created_key(), device_id)

    async def _eval_mark(
        self,
        device_id: str,
        cache_keys: list[str],
        work: PendingLessonSdWork,
    ) -> bool:
        eval_fn = getattr(self.redis, "eval", None)
        if not callable(eval_fn):
            return False
        await self._ensure_standalone_for_lua()
        await eval_fn(
            _MARK_LUA,
            3,
            self._key(device_id),
            self._due_key(),
            self._created_key(),
            device_id,
            json.dumps(cache_keys, separators=(",", ":")),
            utc_iso(self._clock()),
            json.dumps(work, separators=(",", ":")),
            str(epoch_from_iso(work["nextAttemptAt"])),
            str(epoch_from_iso(work["createdAt"])),
            str(self.ttl_sec),
        )
        return True

    async def _eval_clear(
        self,
        device_id: str,
        cache_keys: set[str],
        expected_cache_keys: list[str],
    ) -> bool:
        eval_fn = getattr(self.redis, "eval", None)
        if not callable(eval_fn):
            return False
        await self._ensure_standalone_for_lua()
        await eval_fn(
            _CLEAR_LUA,
            3,
            self._key(device_id),
            self._due_key(),
            self._created_key(),
            device_id,
            json.dumps(sorted(cache_keys), separators=(",", ":")),
            json.dumps(expected_cache_keys, separators=(",", ":")),
            str(self.ttl_sec),
        )
        return True

    async def _eval_mark_callbacks(
        self,
        device_id: str,
        additions: dict[str, dict[str, Any]],
        work: PendingLessonSdWork,
    ) -> bool:
        eval_fn = getattr(self.redis, "eval", None)
        if not callable(eval_fn):
            return False
        await self._ensure_standalone_for_lua()
        await eval_fn(
            _MARK_CALLBACKS_LUA,
            3,
            self._key(device_id),
            self._due_key(),
            self._created_key(),
            device_id,
            json.dumps(additions, separators=(",", ":")),
            json.dumps(work, separators=(",", ":")),
            str(epoch_from_iso(work["nextAttemptAt"])),
            str(epoch_from_iso(work["createdAt"])),
            str(self.ttl_sec),
        )
        return True

    async def _eval_clear_callbacks(
        self,
        device_id: str,
        cache_keys: set[str],
        expected_results: dict[str, dict[str, Any]],
    ) -> bool:
        eval_fn = getattr(self.redis, "eval", None)
        if not callable(eval_fn):
            return False
        await self._ensure_standalone_for_lua()
        await eval_fn(
            _CLEAR_CALLBACKS_LUA,
            3,
            self._key(device_id),
            self._due_key(),
            self._created_key(),
            device_id,
            json.dumps(sorted(cache_keys), separators=(",", ":")),
            json.dumps(expected_results, separators=(",", ":")),
            str(self.ttl_sec),
        )
        return True

    async def _eval_claim_due(self, limit: int, lease_score: float) -> list[str] | None:
        eval_fn = getattr(self.redis, "eval", None)
        if not callable(eval_fn):
            return None
        await self._ensure_standalone_for_lua()
        members = await eval_fn(
            _CLAIM_DUE_LUA,
            1,
            self._due_key(),
            str(self._clock()),
            str(int(limit)),
            str(lease_score),
            self.namespace,
        )
        return [_decode_member(member) for member in members if _decode_member(member)]

    async def _ensure_standalone_for_lua(self) -> None:
        if self._standalone_lua_checked:
            return
        info_fn = getattr(self.redis, "info", None)
        if not callable(info_fn):
            self._standalone_lua_checked = True
            return
        try:
            try:
                info = await info_fn("cluster")
            except TypeError:
                info = await info_fn()
        except Exception as exc:
            raise RuntimeError(
                "lesson SD pending store requires standalone Redis; Redis capability check failed "
                f"({type(exc).__name__})"
            ) from exc
        enabled = _redis_cluster_enabled(info)
        if enabled:
            raise RuntimeError(
                "lesson SD pending store requires standalone Redis; Redis Cluster is not supported"
            )
        self._standalone_lua_checked = True


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
            lease_sec=_int_env("LESSON_SD_PENDING_LEASE_SEC", LESSON_SD_PENDING_LEASE_SEC),
        )
    if env in {"prod", "production"} and not allow_memory:
        raise RuntimeError("Redis is required for durable lesson SD pending work in production")
    return InMemoryLessonSdPendingStore()


def _normalize_device_id(device_id: str) -> str:
    return str(device_id or "").strip()


def _copy_work(work: PendingLessonSdWork) -> PendingLessonSdWork:
    copied: PendingLessonSdWork = {
        "cacheKeys": list(work["cacheKeys"]),
        "attemptCount": int(work["attemptCount"]),
        "createdAt": str(work["createdAt"]),
        "nextAttemptAt": str(work["nextAttemptAt"]),
    }
    callbacks = work.get("callbackResults")
    if callbacks:
        copied["callbackResults"] = copy.deepcopy(callbacks)
    return copied


def _sanitize_work(value: dict[str, Any]) -> PendingLessonSdWork:
    created_at = str(value.get("createdAt") or utc_iso(time.time()))
    next_attempt_at = str(value.get("nextAttemptAt") or created_at)
    try:
        attempt_count = int(value.get("attemptCount") or 0)
    except (TypeError, ValueError):
        attempt_count = 0
    work: PendingLessonSdWork = {
        "cacheKeys": normalize_cache_keys(value.get("cacheKeys") or []),
        "attemptCount": max(0, attempt_count),
        "createdAt": created_at,
        "nextAttemptAt": next_attempt_at,
    }
    callbacks = normalize_callback_results(value.get("callbackResults") or {})
    if callbacks:
        work["callbackResults"] = callbacks
    return work


def _decode_member(member: Any) -> str:
    if isinstance(member, bytes):
        return member.decode("utf-8")
    return str(member or "").strip()


def _redis_cluster_enabled(info: Any) -> bool:
    if isinstance(info, bytes):
        info = info.decode("utf-8", errors="replace")
    if isinstance(info, str):
        for line in info.splitlines():
            key, sep, value = line.partition(":")
            if sep and key.strip() == "cluster_enabled":
                return str(value).strip() == "1"
        return False
    if not isinstance(info, dict):
        return False
    cluster_enabled = info.get("cluster_enabled")
    cluster = info.get("cluster")
    if cluster_enabled is None and isinstance(cluster, dict):
        cluster_enabled = cluster.get("cluster_enabled")
    return str(cluster_enabled).strip().lower() in {"1", "true", "yes"}


def _run_coro_sync(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result: dict[str, Any] = {}

    def runner() -> None:
        try:
            result["value"] = asyncio.run(coro)
        except Exception as exc:
            result["error"] = exc

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join()
    if "error" in result:
        raise result["error"]
    return result.get("value")


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default
