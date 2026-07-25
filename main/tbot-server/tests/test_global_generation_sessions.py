from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
from types import SimpleNamespace

import pytest

from core.connection import ConnectionHandler
from core.lesson import global_generation_sessions, sd_pack_fanout, sd_pack_sync
from core.lesson.global_generation_sessions import (
    GLOBAL_SESSIONS_KEY,
    GlobalGenerationSessions,
)

CHECKSUM = "a" * 64


def _pack(cache_key: str) -> dict:
    return {"cacheKey": cache_key, "private": {"url": "https://secret.example"}}


def _ready(*cache_keys: str) -> dict:
    return {
        "resultsByCacheKey": {
            key: {"ready": True, "criticalFailedCount": 0} for key in cache_keys
        }
    }


class FakeRedis:
    def __init__(self):
        self.hashes = {}
        self.values = {}
        self.lock = asyncio.Lock()

    async def incr(self, key):
        async with self.lock:
            self.values[key] = int(self.values.get(key, 0)) + 1
            return self.values[key]

    async def hget(self, key, field):
        return self.hashes.get(key, {}).get(field)

    async def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    async def eval(self, script, numkeys, *args):
        assert numkeys == 1
        key = args[0]
        field = args[1]
        async with self.lock:
            current_raw = self.hashes.get(key, {}).get(field)
            current = json.loads(current_raw) if current_raw else None
            if "REGISTER_SESSION" in script:
                generation, row = int(args[2]), args[3]
                if current and int(current.get("connectionGeneration", 0)) >= generation:
                    return 0
                self.hashes.setdefault(key, {})[field] = row
                return 1
            generation, session_id = int(args[2]), args[3]
            if not current:
                return 0
            if (
                current.get("connectionGeneration") != generation
                or current.get("sessionId") != session_id
            ):
                return 0
            if "DELETE_SESSION" in script:
                self.hashes[key].pop(field, None)
            else:
                self.hashes.setdefault(key, {})[field] = args[4]
            return 1


async def _no_sleep(_delay):
    await asyncio.Future()


@pytest.mark.asyncio
async def test_fanout_attempts_each_current_connection_once_and_waits_for_failures():
    redis = FakeRedis()
    calls = []
    completed = []

    async def sync(connection, *, only_cache_keys):
        calls.append((connection.name, only_cache_keys))
        if connection.name == "one":
            await asyncio.sleep(0)
            raise RuntimeError("private failure")
        await asyncio.sleep(0.01)
        completed.append(connection.name)
        return _ready(*only_cache_keys)

    sessions = GlobalGenerationSessions(redis, sync=sync, sleep=_no_sleep)
    one = SimpleNamespace(name="one")
    two = SimpleNamespace(name="two")
    await sessions.register("raw-session-1", one)
    await sessions.register("raw-session-2", two)

    result = await sessions.fanout(
        generation=7,
        index_checksum=CHECKSUM,
        packs=[_pack("lesson-a/v1"), _pack("lesson-b/v1")],
    )

    assert sorted(calls) == [
        ("one", {"lesson-a/v1", "lesson-b/v1"}),
        ("two", {"lesson-a/v1", "lesson-b/v1"}),
    ]
    assert completed == ["two"]
    assert result == {"attempted": 2, "current": 1, "retrying": 1, "failed": 0}
    await sessions.unregister("raw-session-1", one)
    await sessions.unregister("raw-session-2", two)


@pytest.mark.asyncio
async def test_unclaimed_raw_session_syncs_without_identity_or_callback_contract():
    calls = []

    async def sync(connection, *, only_cache_keys):
        calls.append((connection, only_cache_keys))
        return _ready(*only_cache_keys)

    sessions = GlobalGenerationSessions(FakeRedis(), sync=sync, sleep=_no_sleep)
    connection = SimpleNamespace(need_bind=True)
    await sessions.register("  RAW-SESSION-2  ", connection)
    await sessions.fanout(
        generation=1, index_checksum=CHECKSUM, packs=[_pack("lesson/v1")]
    )

    assert calls == [(connection, {"lesson/v1"})]
    source = inspect.getsource(inspect.getmodule(GlobalGenerationSessions)).lower()
    for forbidden in (
        "resolve_device_identity",
        "post_lesson_sd_sync_result",
        "lesson_sd_online_index",
        "claim_token",
        "backend_uuid",
    ):
        assert forbidden not in source
    await sessions.unregister("raw-session-2", connection)


@pytest.mark.asyncio
async def test_mac_ids_canonicalize_but_non_mac_ids_only_trim_and_lowercase():
    redis = FakeRedis()
    sessions = GlobalGenerationSessions(redis, sync=lambda *_args, **_kwargs: None)
    mac = object()
    raw = object()

    await sessions.register(" AA-BB-CC-DD-EE-FF ", mac)
    await sessions.register(" Raw-Session-2 ", raw)

    fields = set(redis.hashes[GLOBAL_SESSIONS_KEY])
    assert fields == {
        hashlib.sha256(b"aa:bb:cc:dd:ee:ff").hexdigest(),
        hashlib.sha256(b"raw-session-2").hexdigest(),
    }
    assert set(sessions._by_raw) == {"aa:bb:cc:dd:ee:ff", "raw-session-2"}
    await sessions.unregister("aa:bb:cc:dd:ee:ff", mac)
    await sessions.unregister("raw-session-2", raw)


@pytest.mark.asyncio
async def test_reconnect_replacement_fences_stale_unregister_and_sync_result():
    redis = FakeRedis()
    release_old = asyncio.Event()

    async def sync(connection, *, only_cache_keys):
        if connection.name == "old":
            await release_old.wait()
        return _ready(*only_cache_keys)

    sessions = GlobalGenerationSessions(redis, sync=sync, sleep=_no_sleep)
    old = SimpleNamespace(name="old")
    new = SimpleNamespace(name="new")
    await sessions.register("AA:BB:CC:DD:EE:FF", old)
    old_sync = asyncio.create_task(
        sessions.sync_on_connect(
            old, accepted_generation=4, checksum=CHECKSUM, packs=[_pack("lesson/v1")]
        )
    )
    await asyncio.sleep(0)
    await sessions.register("aa-bb-cc-dd-ee-ff", new)
    await sessions.unregister("aa:bb:cc:dd:ee:ff", old)
    release_old.set()
    await old_sync

    field = hashlib.sha256(b"aa:bb:cc:dd:ee:ff").hexdigest()
    row = json.loads(redis.hashes[GLOBAL_SESSIONS_KEY][field])
    assert row["connectionGeneration"] == 2
    assert row["observedGeneration"] is None
    await sessions.unregister("aa:bb:cc:dd:ee:ff", new)


@pytest.mark.asyncio
async def test_connection_generations_are_redis_monotonic_and_aggregate_is_shared():
    redis = FakeRedis()
    first = GlobalGenerationSessions(redis, sync=lambda *_args, **_kwargs: _ready("a"))
    second = GlobalGenerationSessions(redis, sync=lambda *_args, **_kwargs: _ready("a"))
    one = object()
    two = object()
    await first.register("one", one)
    await second.register("two", two)
    await first.sync_on_connect(
        one, accepted_generation=3, checksum=CHECKSUM, packs=[_pack("a")]
    )

    rows = [json.loads(value) for value in redis.hashes[GLOBAL_SESSIONS_KEY].values()]
    assert sorted(row["connectionGeneration"] for row in rows) == [1, 2]
    assert await second.aggregate(3) == {
        "connected": 2,
        "current": 1,
        "retrying": 1,
        "failed": 0,
    }
    redis.hashes[GLOBAL_SESSIONS_KEY]["malformed"] = "not-json"
    assert (await first.aggregate(3))["connected"] == 2
    await first.unregister("one", one)
    await second.unregister("two", two)


@pytest.mark.asyncio
async def test_older_register_cannot_overwrite_newer_replica_session():
    class DelayedRedis(FakeRedis):
        def __init__(self):
            super().__init__()
            self.first_eval_started = asyncio.Event()
            self.release_first_eval = asyncio.Event()

        async def eval(self, script, numkeys, *args):
            if "REGISTER_SESSION" in script and args[2] == "1":
                self.first_eval_started.set()
                await self.release_first_eval.wait()
            return await super().eval(script, numkeys, *args)

    redis = DelayedRedis()
    first = GlobalGenerationSessions(redis)
    second = GlobalGenerationSessions(redis)
    old = object()
    new = object()

    old_register = asyncio.create_task(first.register("same", old))
    await redis.first_eval_started.wait()
    await second.register("same", new)
    redis.release_first_eval.set()
    await old_register

    field = hashlib.sha256(b"same").hexdigest()
    row = json.loads(redis.hashes[GLOBAL_SESSIONS_KEY][field])
    assert row["connectionGeneration"] == 2
    assert await first.sync_on_connect(
        old, accepted_generation=1, checksum=CHECKSUM, packs=[_pack("a")]
    ) == {"state": "failed", "errorCode": "session_not_current"}
    await first.unregister("same", old)
    assert field in redis.hashes[GLOBAL_SESSIONS_KEY]
    await second.unregister("same", new)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("result", "expected_status"),
    [
        ({"resultsByCacheKey": {"a": {"ready": True, "criticalFailedCount": 0}}}, "retrying"),
        ({"resultsByCacheKey": {"a": {"ready": False, "criticalFailedCount": 0}, "b": {"ready": True, "criticalFailedCount": 0}}}, "retrying"),
        ({"state": "skipped", "resultsByCacheKey": {}}, "retrying"),
        ({"state": "unsupported", "resultsByCacheKey": {}}, "failed"),
        ({"resultsByCacheKey": []}, "failed"),
    ],
)
async def test_all_expected_critical_results_are_required(result, expected_status):
    async def sync(_connection, *, only_cache_keys):
        return result

    sessions = GlobalGenerationSessions(FakeRedis(), sync=sync, sleep=_no_sleep)
    connection = object()
    await sessions.register("one", connection)
    sync_result = await sessions.sync_on_connect(
        connection,
        accepted_generation=5,
        checksum=CHECKSUM,
        packs=[_pack("a"), _pack("b")],
    )

    assert sync_result["state"] == expected_status
    assert (await sessions.aggregate(5))["current"] == 0
    await sessions.unregister("one", connection)


@pytest.mark.asyncio
async def test_skipped_item_cannot_mark_generation_observed_despite_ready_fields():
    async def sync(_connection, *, only_cache_keys):
        return {
            "resultsByCacheKey": {
                "a": {
                    "state": "skipped",
                    "ready": True,
                    "criticalFailedCount": 0,
                }
            }
        }

    redis = FakeRedis()
    sessions = GlobalGenerationSessions(redis, sync=sync, sleep=_no_sleep)
    connection = object()
    await sessions.register("one", connection)

    result = await sessions.sync_on_connect(
        connection,
        accepted_generation=5,
        checksum=CHECKSUM,
        packs=[_pack("a")],
    )

    row = json.loads(next(iter(redis.hashes[GLOBAL_SESSIONS_KEY].values())))
    assert result == {"state": "retrying", "errorCode": "sync_skipped"}
    assert row["observedGeneration"] is None
    assert await sessions.aggregate(5) == {
        "connected": 1,
        "current": 0,
        "retrying": 1,
        "failed": 0,
    }
    await sessions.unregister("one", connection)


@pytest.mark.asyncio
async def test_aggregate_store_failure_raises_only_sanitized_domain_error():
    class BrokenRedis(FakeRedis):
        async def hgetall(self, key):
            raise RuntimeError("redis://user:secret@private-host/session-value")

    sessions = GlobalGenerationSessions(BrokenRedis())

    with pytest.raises(global_generation_sessions.GlobalGenerationSessionsError) as caught:
        await sessions.aggregate(3)

    assert caught.value.code == "generation_sessions_store_unavailable"
    assert str(caught.value) == "generation_sessions_store_unavailable"
    assert "secret" not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


@pytest.mark.asyncio
async def test_fanout_propagates_sanitized_aggregate_store_failure():
    class BrokenRedis(FakeRedis):
        async def hgetall(self, key):
            raise RuntimeError("private redis value")

    redis = BrokenRedis()
    sessions = GlobalGenerationSessions(
        redis,
        sync=lambda _connection, *, only_cache_keys: _ready(*only_cache_keys),
    )
    connection = object()
    await sessions.register("one", connection)

    with pytest.raises(global_generation_sessions.GlobalGenerationSessionsError) as caught:
        await sessions.fanout(
            generation=4,
            index_checksum=CHECKSUM,
            packs=[_pack("a")],
        )

    assert caught.value.code == "generation_sessions_store_unavailable"
    assert "private" not in str(caught.value)
    await sessions.unregister("one", connection)


@pytest.mark.asyncio
async def test_fanout_reraises_non_exception_base_exception_from_gather(monkeypatch):
    sessions = GlobalGenerationSessions(FakeRedis())
    connection = object()
    await sessions.register("one", connection)

    async def base_exception_result(*aws, **_kwargs):
        for awaitable in aws:
            if inspect.iscoroutine(awaitable):
                awaitable.close()
        return [SystemExit()]

    monkeypatch.setattr(global_generation_sessions.asyncio, "gather", base_exception_result)

    with pytest.raises(SystemExit):
        await sessions.fanout(
            generation=1,
            index_checksum=CHECKSUM,
            packs=[_pack("a")],
        )


@pytest.mark.asyncio
async def test_fanout_type_guards_non_mapping_normal_result(monkeypatch):
    redis = FakeRedis()
    sessions = GlobalGenerationSessions(redis, sleep=_no_sleep)
    connection = object()
    await sessions.register("one", connection)
    real_gather = asyncio.gather

    async def invalid_normal_result(*aws, **_kwargs):
        for awaitable in aws:
            if inspect.iscoroutine(awaitable):
                awaitable.close()
        return [object()]

    monkeypatch.setattr(global_generation_sessions.asyncio, "gather", invalid_normal_result)
    result = await sessions.fanout(
        generation=1,
        index_checksum=CHECKSUM,
        packs=[_pack("a")],
    )

    assert result == {"attempted": 1, "current": 0, "retrying": 1, "failed": 0}
    monkeypatch.setattr(global_generation_sessions.asyncio, "gather", real_gather)
    await sessions.unregister("one", connection)


@pytest.mark.asyncio
async def test_invalid_or_duplicate_pack_keys_fail_without_calling_sync():
    called = False

    async def sync(*_args, **_kwargs):
        nonlocal called
        called = True

    sessions = GlobalGenerationSessions(FakeRedis(), sync=sync, sleep=_no_sleep)
    connection = object()
    await sessions.register("one", connection)

    result = await sessions.sync_on_connect(
        connection,
        accepted_generation=2,
        checksum=CHECKSUM,
        packs=[_pack("a"), _pack("a")],
    )

    assert result == {"state": "failed", "errorCode": "invalid_packs"}
    assert called is False
    await sessions.unregister("one", connection)


@pytest.mark.asyncio
async def test_register_before_ready_retries_and_mcp_ready_prompts_current_sync():
    redis = FakeRedis()
    calls = 0
    sleeping = asyncio.Event()

    async def sleep(_delay):
        sleeping.set()
        await asyncio.Future()

    async def sync(_connection, *, only_cache_keys):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("not ready")
        return _ready(*only_cache_keys)

    sessions = GlobalGenerationSessions(redis, sync=sync, sleep=sleep)
    seed = object()
    await sessions.register("seed", seed)
    await sessions.fanout(
        generation=9, index_checksum=CHECKSUM, packs=[_pack("lesson/v1")]
    )
    await sleeping.wait()
    await sessions.unregister("seed", seed)

    connection = object()
    await sessions.register("new", connection)
    await asyncio.sleep(0)
    task = sessions.notify_mcp_ready(connection)
    assert task is not None
    await task

    assert calls >= 3
    assert (await sessions.aggregate(9))["current"] == 1
    await sessions.unregister("new", connection)


@pytest.mark.asyncio
async def test_disconnect_and_new_generation_cancel_session_retry_task():
    redis = FakeRedis()
    cancelled = 0

    async def sleep(_delay):
        nonlocal cancelled
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancelled += 1
            raise

    async def sync(_connection, *, only_cache_keys):
        raise RuntimeError("retry")

    sessions = GlobalGenerationSessions(redis, sync=sync, sleep=sleep)
    connection = object()
    await sessions.register("one", connection)
    await sessions.fanout(generation=1, index_checksum=CHECKSUM, packs=[_pack("a")])
    await asyncio.sleep(0)
    await sessions.fanout(generation=2, index_checksum=CHECKSUM, packs=[_pack("b")])
    await asyncio.sleep(0)
    await sessions.unregister("one", connection)

    assert cancelled == 2


def test_source_does_not_store_raw_ids_in_shared_rows():
    source = inspect.getsource(inspect.getmodule(GlobalGenerationSessions))
    assert "sha256" in source
    assert '"rawId"' not in source


class _Logger:
    def bind(self, **_kwargs):
        return self

    def warning(self, *_args, **_kwargs):
        return None


def _connection_for_background_sync(global_sessions=None):
    connection = ConnectionHandler.__new__(ConnectionHandler)
    connection.server = SimpleNamespace(global_generation_sessions=global_sessions)
    connection.logger = _Logger()
    return connection


@pytest.mark.asyncio
async def test_connection_does_not_drain_legacy_pending_jobs_by_default(monkeypatch):
    calls = []

    async def drain(_connection):
        calls.append("drain")

    async def sync(_connection):
        calls.append("sync")
        return {"state": "done"}

    monkeypatch.delenv("TBOT_ENABLE_BACKGROUND_WORKERS", raising=False)
    monkeypatch.delenv("LESSON_SD_LEGACY_DEVICE_WORKER_ENABLED", raising=False)
    monkeypatch.setattr(sd_pack_fanout, "drain_pending_for_connection", drain)
    monkeypatch.setattr(sd_pack_sync, "sync_cached_lesson_assets_to_sd", sync)

    result = await _connection_for_background_sync()._sync_cached_lesson_assets_to_sd()

    assert calls == ["sync"]
    assert result == {"pending": None, "full": {"state": "done"}}


@pytest.mark.asyncio
async def test_connection_drains_legacy_only_when_both_exact_flags_are_true(monkeypatch):
    calls = []

    async def drain(_connection):
        calls.append("drain")
        return {"state": "drained"}

    async def sync(_connection):
        calls.append("sync")
        return {"state": "done"}

    monkeypatch.setenv("TBOT_ENABLE_BACKGROUND_WORKERS", " TrUe ")
    monkeypatch.setenv("LESSON_SD_LEGACY_DEVICE_WORKER_ENABLED", "TRUE")
    monkeypatch.setattr(sd_pack_fanout, "drain_pending_for_connection", drain)
    monkeypatch.setattr(sd_pack_sync, "sync_cached_lesson_assets_to_sd", sync)

    result = await _connection_for_background_sync()._sync_cached_lesson_assets_to_sd()

    assert calls == ["drain", "sync"]
    assert result == {
        "pending": {"state": "drained"},
        "full": {"state": "done"},
    }


@pytest.mark.asyncio
async def test_connection_global_path_prompts_session_without_full_sync(monkeypatch):
    class Sessions:
        def notify_mcp_ready(self, connection):
            async def done():
                return {"state": "current", "same": connection is handler}

            return asyncio.create_task(done())

    handler = _connection_for_background_sync(Sessions())

    async def forbidden(*_args, **_kwargs):
        pytest.fail("global path must not run unrestricted full-cache sync")

    monkeypatch.setattr(sd_pack_sync, "sync_cached_lesson_assets_to_sd", forbidden)
    monkeypatch.setattr(sd_pack_fanout, "drain_pending_for_connection", forbidden)

    assert await handler._sync_cached_lesson_assets_to_sd() == {
        "global": {"state": "current", "same": True}
    }
