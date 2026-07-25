"""Fan out accepted lesson generations to raw websocket sessions."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import re
import uuid
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from core.lesson.sd_pack_sync import sync_cached_lesson_assets_to_sd

GLOBAL_SESSIONS_KEY = "lesson-assets:global-sessions:v1"
GLOBAL_SESSIONS_SEQUENCE_KEY = "lesson-assets:global-sessions:v1:sequence"

_MAC_RE = re.compile(r"^(?:[0-9a-f]{2}:){5}[0-9a-f]{2}$|^(?:[0-9a-f]{2}-){5}[0-9a-f]{2}$")
_RETRY_DELAYS = (5, 10, 20, 40, 80, 160, 300)
_TERMINAL_RESULT_STATES = frozenset({"invalid", "invalid_result", "unsupported"})
_RETRYABLE_SKIP_STATES = frozenset(
    {"client_unavailable", "mcp_client_not_ready", "mcp_not_ready", "no_client", "skipped"}
)

_REGISTER_SCRIPT = """
-- REGISTER_SESSION
local current = redis.call('HGET', KEYS[1], ARGV[1])
if current then
  local decoded = cjson.decode(current)
  if tonumber(decoded.connectionGeneration or 0) >= tonumber(ARGV[2]) then
    return 0
  end
end
redis.call('HSET', KEYS[1], ARGV[1], ARGV[3])
return 1
"""

_UPDATE_SCRIPT = """
-- UPDATE_SESSION
local current = redis.call('HGET', KEYS[1], ARGV[1])
if not current then return 0 end
local decoded = cjson.decode(current)
if tonumber(decoded.connectionGeneration or 0) ~= tonumber(ARGV[2]) then return 0 end
if decoded.sessionId ~= ARGV[3] then return 0 end
redis.call('HSET', KEYS[1], ARGV[1], ARGV[4])
return 1
"""

_DELETE_SCRIPT = """
-- DELETE_SESSION
local current = redis.call('HGET', KEYS[1], ARGV[1])
if not current then return 0 end
local decoded = cjson.decode(current)
if tonumber(decoded.connectionGeneration or 0) ~= tonumber(ARGV[2]) then return 0 end
if decoded.sessionId ~= ARGV[3] then return 0 end
redis.call('HDEL', KEYS[1], ARGV[1])
return 1
"""


@dataclass
class _Session:
    normalized_raw_id: str
    raw_hash: str
    session_id: str
    connection_generation: int
    connection: Any
    retry_attempt: int = 0
    retry_task: asyncio.Task | None = None
    active: bool = True


class GlobalGenerationSessionsError(RuntimeError):
    def __init__(self) -> None:
        self.code = "generation_sessions_store_unavailable"
        super().__init__(self.code)


class GlobalGenerationSessions:
    def __init__(
        self,
        redis: Any,
        *,
        sync: Callable[..., Any] = sync_cached_lesson_assets_to_sd,
        sleep: Callable[[float], Any] = asyncio.sleep,
    ) -> None:
        if redis is None:
            raise ValueError("redis is required")
        self.redis = redis
        self._sync = sync
        self._sleep = sleep
        self._lock = asyncio.Lock()
        self._by_raw: dict[str, _Session] = {}
        self._by_connection: dict[int, _Session] = {}
        self._accepted_generation: int | None = None
        self._accepted_checksum: str | None = None
        self._packs: list[dict[str, str]] | None = None
        self._packs_valid = True

    async def register(self, raw_id: str, connection: Any) -> None:
        normalized = _normalize_raw_id(raw_id)
        raw_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        generation = int(await self.redis.incr(GLOBAL_SESSIONS_SEQUENCE_KEY))
        session = _Session(
            normalized_raw_id=normalized,
            raw_hash=raw_hash,
            session_id=uuid.uuid4().hex,
            connection_generation=generation,
            connection=connection,
        )
        row = _session_row(session, status="retrying")

        old_tasks = []
        async with self._lock:
            previous = self._by_raw.get(normalized)
            previous_for_connection = self._by_connection.get(id(connection))
            for old in (previous, previous_for_connection):
                if old is None or old is session:
                    continue
                old.active = False
                if self._by_raw.get(old.normalized_raw_id) is old:
                    self._by_raw.pop(old.normalized_raw_id, None)
                if old.retry_task is not None and not old.retry_task.done():
                    old.retry_task.cancel()
                    old_tasks.append(old.retry_task)
            self._by_raw[normalized] = session
            self._by_connection[id(connection)] = session

        if old_tasks:
            await asyncio.gather(*set(old_tasks), return_exceptions=True)
        registered = await self._register_shared(session, row)
        if not registered:
            async with self._lock:
                session.active = False
                if self._by_raw.get(normalized) is session:
                    self._by_raw.pop(normalized, None)
            return
        if self._accepted_generation is not None:
            self._start_worker(session, immediate=True)

    async def unregister(self, raw_id: str, connection: Any) -> None:
        normalized = _normalize_raw_id(raw_id)
        task = None
        async with self._lock:
            session = self._by_connection.get(id(connection))
            if (
                session is None
                or session.connection is not connection
                or session.normalized_raw_id != normalized
            ):
                return
            self._by_connection.pop(id(connection), None)
            session.active = False
            task = session.retry_task
            if task is not None and not task.done():
                task.cancel()
            if self._by_raw.get(normalized) is session:
                self._by_raw.pop(normalized, None)
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        await self._delete_shared(session)

    async def sync_on_connect(
        self,
        connection: Any,
        *,
        accepted_generation: int,
        checksum: str,
        packs: list[dict],
    ) -> dict:
        session = await self._session_for(connection)
        if session is None:
            return {"state": "failed", "errorCode": "session_not_current"}
        pack_keys = _pack_keys(packs)
        if not _valid_generation(accepted_generation) or not isinstance(checksum, str):
            await self._set_status(session, status="failed")
            return {"state": "failed", "errorCode": "invalid_generation"}
        if pack_keys is None:
            await self._set_status(session, status="failed")
            return {"state": "failed", "errorCode": "invalid_packs"}
        result = await self._attempt(
            session,
            accepted_generation=accepted_generation,
            checksum=checksum,
            pack_keys=pack_keys,
        )
        if result["state"] == "retrying":
            self._start_worker(session, immediate=False)
        return result

    async def fanout(
        self,
        *,
        generation: int,
        index_checksum: str,
        packs: list[dict],
    ) -> dict:
        pack_keys = _pack_keys(packs)
        stripped = (
            [{"cacheKey": key} for key in pack_keys]
            if pack_keys is not None
            else None
        )
        async with self._lock:
            self._accepted_generation = generation
            self._accepted_checksum = index_checksum
            self._packs = deepcopy(stripped)
            self._packs_valid = pack_keys is not None and _valid_generation(generation)
            sessions = [session for session in self._by_raw.values() if session.active]
            tasks = []
            for session in sessions:
                session.retry_attempt = 0
                if session.retry_task is not None and not session.retry_task.done():
                    session.retry_task.cancel()
                    tasks.append(session.retry_task)
                session.retry_task = None
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        if pack_keys is None or not _valid_generation(generation):
            await asyncio.gather(
                *(self._set_status(session, status="failed") for session in sessions),
                return_exceptions=True,
            )
            counts = await self.aggregate(generation)
            return _fanout_counts(0, counts)

        results = await asyncio.gather(
            *(
                self._attempt(
                    session,
                    accepted_generation=generation,
                    checksum=index_checksum,
                    pack_keys=pack_keys,
                )
                for session in sessions
            ),
            return_exceptions=True,
        )
        for session, result in zip(sessions, results, strict=True):
            if isinstance(result, asyncio.CancelledError):
                raise result
            if isinstance(result, BaseException):
                if not isinstance(result, Exception):
                    raise result
                self._start_worker(session, immediate=False)
                continue
            if not isinstance(result, Mapping):
                self._start_worker(session, immediate=False)
                continue
            if result.get("state") == "retrying":
                self._start_worker(session, immediate=False)
        counts = await self.aggregate(generation)
        return _fanout_counts(len(sessions), counts)

    async def aggregate(self, accepted_generation: int) -> dict[str, int]:
        counts = {"connected": 0, "current": 0, "retrying": 0, "failed": 0}
        store_unavailable = False
        try:
            raw_rows = await self.redis.hgetall(GLOBAL_SESSIONS_KEY)
        except asyncio.CancelledError:
            raise
        except Exception:
            raw_rows = {}
            store_unavailable = True
        if store_unavailable:
            raise GlobalGenerationSessionsError()
        for raw in dict(raw_rows or {}).values():
            row = _decode_row(raw)
            if row is None or not _valid_shared_row(row):
                continue
            counts["connected"] += 1
            if row.get("observedGeneration") == accepted_generation:
                counts["current"] += 1
            elif row.get("status") == "failed":
                counts["failed"] += 1
            else:
                counts["retrying"] += 1
        return counts

    def notify_mcp_ready(self, connection: Any) -> asyncio.Task | None:
        if self._accepted_generation is None:
            return None
        return asyncio.create_task(self._prompt_current(connection))

    async def _prompt_current(self, connection: Any) -> dict:
        session = await self._session_for(connection)
        if session is None:
            return {"state": "failed", "errorCode": "session_not_current"}
        task = session.retry_task
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            session.retry_task = None
        generation = self._accepted_generation
        checksum = self._accepted_checksum
        packs = self._packs
        if generation is None or checksum is None or packs is None or not self._packs_valid:
            return {"state": "failed", "errorCode": "generation_not_ready"}
        return await self.sync_on_connect(
            connection,
            accepted_generation=generation,
            checksum=checksum,
            packs=deepcopy(packs),
        )

    async def _attempt(
        self,
        session: _Session,
        *,
        accepted_generation: int,
        checksum: str,
        pack_keys: tuple[str, ...],
    ) -> dict:
        fence = await self._shared_fence(session)
        if fence is False:
            return {"state": "failed", "errorCode": "session_not_current"}
        if fence is None or not await self._local_current(session, accepted_generation):
            await self._set_status(session, status="retrying")
            return {"state": "retrying", "errorCode": "session_fence_unavailable"}
        try:
            result = self._sync(session.connection, only_cache_keys=set(pack_keys))
            if inspect.isawaitable(result):
                result = await result
        except asyncio.CancelledError:
            raise
        except Exception:
            await self._set_status(session, status="retrying")
            return {"state": "retrying", "errorCode": "sync_failed"}

        fence = await self._shared_fence(session)
        if fence is False:
            return {"state": "failed", "errorCode": "session_not_current"}
        if fence is None or not await self._local_current(session, accepted_generation):
            await self._set_status(session, status="retrying")
            return {"state": "retrying", "errorCode": "session_fence_unavailable"}

        state, error_code = _sync_result_outcome(result, pack_keys)
        if state == "current":
            updated = await self._set_status(
                session,
                status="current",
                observed_generation=accepted_generation,
                observed_checksum=checksum,
            )
            if updated:
                session.retry_attempt = 0
                return {"state": "current"}
            return {"state": "retrying", "errorCode": "session_fence_unavailable"}
        await self._set_status(session, status=state)
        return {"state": state, "errorCode": error_code}

    def _start_worker(self, session: _Session, *, immediate: bool) -> None:
        if not session.active:
            return
        current = session.retry_task
        if current is not None and not current.done():
            return
        task = asyncio.create_task(self._retry_worker(session, immediate=immediate))
        session.retry_task = task
        task.add_done_callback(lambda done: self._worker_done(session, done))

    def _worker_done(self, session: _Session, task: asyncio.Task) -> None:
        if session.retry_task is task:
            session.retry_task = None

    async def _retry_worker(self, session: _Session, *, immediate: bool) -> None:
        try:
            while session.active:
                generation = self._accepted_generation
                checksum = self._accepted_checksum
                packs = self._packs
                if generation is None or checksum is None or packs is None or not self._packs_valid:
                    return
                if not immediate:
                    if not await self._local_current(session, generation):
                        return
                    fence = await self._shared_fence(session)
                    if fence is False:
                        return
                    delay = _RETRY_DELAYS[min(session.retry_attempt, len(_RETRY_DELAYS) - 1)]
                    await self._sleep(delay)
                    if not await self._local_current(session, generation):
                        return
                    fence = await self._shared_fence(session)
                    if fence is False:
                        return
                    if fence is None:
                        session.retry_attempt += 1
                        continue
                immediate = False
                result = await self._attempt(
                    session,
                    accepted_generation=generation,
                    checksum=checksum,
                    pack_keys=tuple(pack["cacheKey"] for pack in packs),
                )
                if result["state"] != "retrying":
                    return
                session.retry_attempt += 1
        except asyncio.CancelledError:
            raise

    async def _session_for(self, connection: Any) -> _Session | None:
        async with self._lock:
            session = self._by_connection.get(id(connection))
            if session is None or session.connection is not connection or not session.active:
                return None
            if self._by_raw.get(session.normalized_raw_id) is not session:
                return None
            return session

    async def _local_current(self, session: _Session, generation: int) -> bool:
        async with self._lock:
            return (
                session.active
                and self._by_raw.get(session.normalized_raw_id) is session
                and self._by_connection.get(id(session.connection)) is session
                and self._accepted_generation in (None, generation)
            )

    async def _shared_fence(self, session: _Session) -> bool | None:
        try:
            raw = await self.redis.hget(GLOBAL_SESSIONS_KEY, session.raw_hash)
        except asyncio.CancelledError:
            raise
        except Exception:
            return None
        row = _decode_row(raw)
        if row is None:
            return False
        return (
            row.get("connectionGeneration") == session.connection_generation
            and row.get("sessionId") == session.session_id
        )

    async def _register_shared(self, session: _Session, row: dict) -> bool:
        try:
            result = await self.redis.eval(
                _REGISTER_SCRIPT,
                1,
                GLOBAL_SESSIONS_KEY,
                session.raw_hash,
                str(session.connection_generation),
                _encode_row(row),
            )
            return int(result) == 1
        except asyncio.CancelledError:
            raise
        except Exception:
            return False

    async def _set_status(
        self,
        session: _Session,
        *,
        status: str,
        observed_generation: int | None = None,
        observed_checksum: str | None = None,
    ) -> bool:
        row = _session_row(
            session,
            status=status,
            observed_generation=observed_generation,
            observed_checksum=observed_checksum,
        )
        async with self._lock:
            if (
                not session.active
                or self._by_raw.get(session.normalized_raw_id) is not session
                or self._by_connection.get(id(session.connection)) is not session
            ):
                return False
            try:
                result = await self.redis.eval(
                    _UPDATE_SCRIPT,
                    1,
                    GLOBAL_SESSIONS_KEY,
                    session.raw_hash,
                    str(session.connection_generation),
                    session.session_id,
                    _encode_row(row),
                )
                return int(result) == 1
            except asyncio.CancelledError:
                raise
            except Exception:
                return False

    async def _delete_shared(self, session: _Session) -> bool:
        try:
            result = await self.redis.eval(
                _DELETE_SCRIPT,
                1,
                GLOBAL_SESSIONS_KEY,
                session.raw_hash,
                str(session.connection_generation),
                session.session_id,
            )
            return int(result) == 1
        except asyncio.CancelledError:
            raise
        except Exception:
            return False


def _normalize_raw_id(raw_id: str) -> str:
    if not isinstance(raw_id, str) or not raw_id.strip():
        raise ValueError("raw_id must be a non-empty string")
    normalized = raw_id.strip().lower()
    if _MAC_RE.fullmatch(normalized):
        return normalized.replace("-", ":")
    return normalized


def _pack_keys(packs: Any) -> tuple[str, ...] | None:
    if not isinstance(packs, list):
        return None
    keys = []
    seen = set()
    for pack in packs:
        if not isinstance(pack, Mapping):
            return None
        key = pack.get("cacheKey")
        if not isinstance(key, str) or not key or key in seen:
            return None
        seen.add(key)
        keys.append(key)
    return tuple(keys)


def _sync_result_outcome(result: Any, expected: tuple[str, ...]) -> tuple[str, str | None]:
    if not isinstance(result, Mapping):
        return "failed", "sync_result_invalid"
    if result.get("unsupported") is True:
        return "failed", "sync_unsupported"
    skipped = str(result.get("skipped", "")).strip().lower()
    if skipped:
        if skipped == "sd_pack_disabled":
            return "failed", "sync_unsupported"
        return "retrying", "sync_skipped"
    state = str(result.get("state", "")).strip().lower()
    if state in _TERMINAL_RESULT_STATES:
        return "failed", "sync_unsupported" if state == "unsupported" else "sync_result_invalid"
    if state in _RETRYABLE_SKIP_STATES:
        return "retrying", "sync_skipped"
    results = result.get("resultsByCacheKey")
    if not isinstance(results, Mapping):
        return "failed", "sync_result_invalid"
    for key in expected:
        item = results.get(key)
        if not isinstance(item, Mapping):
            return "retrying", "sync_result_incomplete"
        if item.get("unsupported") is True:
            return "failed", "sync_unsupported"
        if item.get("skipped"):
            return "retrying", "sync_skipped"
        item_state = str(item.get("state", item.get("status", ""))).strip().lower()
        if item_state in _TERMINAL_RESULT_STATES:
            code = "sync_unsupported" if item_state == "unsupported" else "sync_result_invalid"
            return "failed", code
        if item_state in _RETRYABLE_SKIP_STATES:
            return "retrying", "sync_skipped"
        ready = item.get("ready")
        critical_failed = item.get("criticalFailedCount")
        if not isinstance(ready, bool) or type(critical_failed) is not int:
            return "failed", "sync_result_invalid"
        if ready is not True or critical_failed != 0:
            return "retrying", "sync_result_incomplete"
    return "current", None


def _session_row(
    session: _Session,
    *,
    status: str,
    observed_generation: int | None = None,
    observed_checksum: str | None = None,
) -> dict[str, Any]:
    return {
        "sessionId": session.session_id,
        "connectionGeneration": session.connection_generation,
        "observedGeneration": observed_generation,
        "observedChecksum": observed_checksum,
        "status": status,
        "retryAttempt": session.retry_attempt,
    }


def _encode_row(row: dict[str, Any]) -> str:
    return json.dumps(row, sort_keys=True, separators=(",", ":"))


def _decode_row(raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _valid_shared_row(row: dict[str, Any]) -> bool:
    return (
        isinstance(row.get("sessionId"), str)
        and type(row.get("connectionGeneration")) is int
        and row["connectionGeneration"] > 0
        and row.get("status") in {"current", "retrying", "failed"}
        and (
            row.get("observedGeneration") is None
            or type(row.get("observedGeneration")) is int
        )
    )


def _valid_generation(value: Any) -> bool:
    return type(value) is int and value > 0


def _fanout_counts(attempted: int, counts: Mapping[str, int]) -> dict[str, int]:
    return {
        "attempted": attempted,
        "current": int(counts.get("current", 0)),
        "retrying": int(counts.get("retrying", 0)),
        "failed": int(counts.get("failed", 0)),
    }
