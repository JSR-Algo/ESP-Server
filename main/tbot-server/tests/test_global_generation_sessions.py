from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import math
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
        self.touch_calls = 0

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
            try:
                current = json.loads(current_raw) if current_raw else None
            except (TypeError, ValueError):
                current = None
                malformed = True
            else:
                malformed = False
            if "REGISTER_SESSION" in script:
                generation, row = int(args[2]), args[3]
                if not malformed and current and int(current.get("connectionGeneration", 0)) >= generation:
                    return 0
                self.hashes.setdefault(key, {})[field] = row
                return 1
            if malformed:
                return 0
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
            elif "UPDATE_SESSION" in script:
                expires_at = current.get("expiresAt")
                now = float(args[4])
                next_expires_at = float(args[5])
                if (
                    isinstance(expires_at, bool)
                    or not isinstance(expires_at, (int, float))
                    or not math.isfinite(float(expires_at))
                    or float(expires_at) <= now
                    or not math.isfinite(next_expires_at)
                    or next_expires_at <= now
                ):
                    return 0
                self.hashes.setdefault(key, {})[field] = args[6]
            elif "TOUCH_SESSION" in script:
                expires_at = current.get("expiresAt")
                now = float(args[4])
                if (
                    isinstance(expires_at, bool)
                    or not isinstance(expires_at, (int, float))
                    or not math.isfinite(float(expires_at))
                    or float(expires_at) <= now
                ):
                    return 0
                current["expiresAt"] = float(args[5])
                self.hashes.setdefault(key, {})[field] = json.dumps(current)
                self.touch_calls += 1
            else:
                self.hashes.setdefault(key, {})[field] = args[4]
            return 1


class Clock:
    def __init__(self, value: float):
        self.value = value

    def __call__(self):
        return self.value


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
    module = inspect.getmodule(GlobalGenerationSessions)
    assert module is not None
    source = inspect.getsource(module).lower()
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
    old_heartbeat = sessions._by_connection[id(old)].heartbeat_task
    assert old_heartbeat is not None
    old_sync = asyncio.create_task(
        sessions.sync_on_connect(
            old, accepted_generation=4, checksum=CHECKSUM, packs=[_pack("lesson/v1")]
        )
    )
    await asyncio.sleep(0)
    await sessions.register("aa-bb-cc-dd-ee-ff", new)
    assert old_heartbeat.done()
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
    with pytest.raises(global_generation_sessions.GlobalGenerationSessionsError) as caught:
        await old_register

    field = hashlib.sha256(b"same").hexdigest()
    row = json.loads(redis.hashes[GLOBAL_SESSIONS_KEY][field])
    assert caught.value.code == "generation_session_register_failed"
    assert row["connectionGeneration"] == 2
    assert first._by_raw == {}
    assert first._by_connection == {}
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
@pytest.mark.parametrize(
    "sync_result",
    [
        {
            "resultsByCacheKey": {
                "a": {
                    "ready": False,
                    "criticalFailedCount": 0,
                    "errorCode": "storage_busy",
                }
            }
        },
        {
            "resultsByCacheKey": {
                "a": {
                    "ready": False,
                    "criticalFailedCount": 0,
                    "errorCode": "sd_sync_recovery_pending",
                }
            }
        },
    ],
)
async def test_unknown_or_storage_busy_sync_stays_retrying_without_false_generation(sync_result):
    async def sync(_connection, *, only_cache_keys):
        assert only_cache_keys == {"a"}
        return sync_result

    redis = FakeRedis()
    sessions = GlobalGenerationSessions(redis, sync=sync, sleep=_no_sleep)
    connection = object()
    await sessions.register("one", connection)

    result = await sessions.sync_on_connect(
        connection,
        accepted_generation=8,
        checksum=CHECKSUM,
        packs=[_pack("a")],
    )

    row = json.loads(next(iter(redis.hashes[GLOBAL_SESSIONS_KEY].values())))
    assert result == {"state": "retrying", "errorCode": "sync_result_incomplete"}
    assert row["status"] == "retrying"
    assert row["observedGeneration"] is None
    assert await sessions.aggregate(8) == {
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
    real_gather = asyncio.gather

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
    monkeypatch.setattr(global_generation_sessions.asyncio, "gather", real_gather)
    await sessions.unregister("one", connection)


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
async def test_fresh_reconnect_uses_stable_server_config_for_generation_sync():
    calls = []

    async def sync(connection, *, only_cache_keys):
        lesson = connection.config.get("lesson", {})
        calls.append(lesson.get("asset_pack_mount_root"))
        if lesson.get("asset_pack_mount_root") != "/stable/server/packs":
            return {"packs": 0, "synced": 0, "failed": 0, "resultsByCacheKey": {}}
        return _ready(*only_cache_keys)

    redis = FakeRedis()
    sessions = GlobalGenerationSessions(redis, sync=sync, sleep=_no_sleep)
    connection = SimpleNamespace(
        config={"lesson": {"asset_delivery_mode": "sd_pack"}},
        server=SimpleNamespace(
            config={
                "lesson": {
                    "asset_delivery_mode": "sd_pack",
                    "asset_pack_mount_root": "/stable/server/packs",
                }
            }
        ),
    )
    await sessions.register("one", connection)

    result = await sessions.fanout(
        generation=41,
        index_checksum=CHECKSUM,
        packs=[_pack("lesson/v41")],
    )

    row = json.loads(next(iter(redis.hashes[GLOBAL_SESSIONS_KEY].values())))
    assert calls == ["/stable/server/packs"]
    assert result == {"attempted": 1, "current": 1, "retrying": 0, "failed": 0}
    assert row["status"] == "current"
    assert row["observedGeneration"] == 41
    assert row["observedChecksum"] == CHECKSUM
    await sessions.unregister("one", connection)


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


@pytest.mark.asyncio
async def test_retry_worker_only_resends_incomplete_pack_keys():
    redis = FakeRedis()
    calls = []
    release_retry = asyncio.Event()
    retry_started = asyncio.Event()

    async def sleep(_delay):
        retry_started.set()
        await release_retry.wait()

    async def sync(_connection, *, only_cache_keys):
        calls.append(set(only_cache_keys))
        if len(calls) == 1:
            return {
                "resultsByCacheKey": {
                    "ready": {"ready": True, "criticalFailedCount": 0},
                    "retry": {"ready": False, "criticalFailedCount": 1},
                }
            }
        return _ready(*only_cache_keys)

    sessions = GlobalGenerationSessions(redis, sync=sync, sleep=sleep)
    connection = object()
    await sessions.register("one", connection)

    result = await sessions.fanout(
        generation=5,
        index_checksum=CHECKSUM,
        packs=[_pack("ready"), _pack("retry")],
    )
    assert result["retrying"] == 1
    await retry_started.wait()
    release_retry.set()
    for _ in range(20):
        if len(calls) == 2:
            break
        await asyncio.sleep(0)

    assert calls == [{"ready", "retry"}, {"retry"}]
    assert (await sessions.aggregate(5))["current"] == 1
    await sessions.unregister("one", connection)


@pytest.mark.asyncio
async def test_explicit_invalid_request_is_terminal_and_does_not_start_retry_worker():
    sleeps = 0

    async def sleep(_delay):
        nonlocal sleeps
        sleeps += 1

    async def sync(_connection, *, only_cache_keys):
        key = next(iter(only_cache_keys))
        return {
            "resultsByCacheKey": {
                key: {
                    "ready": False,
                    "criticalFailedCount": 1,
                    "errorCode": "invalid_request",
                }
            }
        }

    sessions = GlobalGenerationSessions(FakeRedis(), sync=sync, sleep=sleep)
    connection = object()
    await sessions.register("one", connection)

    result = await sessions.fanout(
        generation=5,
        index_checksum=CHECKSUM,
        packs=[_pack("invalid")],
    )
    await asyncio.sleep(0)

    assert result == {"attempted": 1, "current": 0, "retrying": 0, "failed": 1}
    assert sleeps == 0
    assert (await sessions.aggregate(5))["failed"] == 1
    await sessions.unregister("one", connection)


def test_source_does_not_store_raw_ids_in_shared_rows():
    module = inspect.getmodule(GlobalGenerationSessions)
    assert module is not None
    source = inspect.getsource(module)
    assert "sha256" in source
    assert '"rawId"' not in source


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["incr", "eval", "non_success"])
async def test_failed_register_raises_safely_and_rolls_back_all_local_state(failure):
    class BrokenRedis(FakeRedis):
        async def incr(self, key):
            if failure == "incr":
                raise RuntimeError("redis://secret@private/register")
            return await super().incr(key)

        async def eval(self, script, numkeys, *args):
            if "REGISTER_SESSION" in script:
                if failure == "eval":
                    raise RuntimeError("private redis row")
                if failure == "non_success":
                    return 0
            return await super().eval(script, numkeys, *args)

    sessions = GlobalGenerationSessions(BrokenRedis())
    started_tasks = []
    sessions._start_heartbeat = lambda session: started_tasks.append(("heartbeat", session))
    sessions._start_worker = lambda session, *, immediate: started_tasks.append(
        ("retry", session, immediate)
    )
    await sessions.fanout(
        generation=8,
        index_checksum=CHECKSUM,
        packs=[_pack("a")],
    )
    connection = object()

    with pytest.raises(global_generation_sessions.GlobalGenerationSessionsError) as caught:
        await sessions.register("raw-session", connection)

    assert caught.value.code == "generation_session_register_failed"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert sessions._by_raw == {}
    assert sessions._by_connection == {}
    assert started_tasks == []
    prompt = sessions.notify_mcp_ready(connection)
    assert prompt is not None
    assert await prompt == {"state": "failed", "errorCode": "session_not_current"}


@pytest.mark.asyncio
async def test_expired_hard_dead_row_is_ignored_and_fenced_deleted():
    clock = Clock(100.0)
    redis = FakeRedis()
    field = hashlib.sha256(b"dead").hexdigest()
    redis.hashes[GLOBAL_SESSIONS_KEY] = {
        field: json.dumps(
            {
                "sessionId": "opaque-dead-session",
                "connectionGeneration": 7,
                "observedGeneration": 3,
                "observedChecksum": CHECKSUM,
                "status": "current",
                "retryAttempt": 0,
                "expiresAt": 99.0,
            }
        )
    }
    sessions = GlobalGenerationSessions(redis, clock=clock)

    assert await sessions.aggregate(3) == {
        "connected": 0,
        "current": 0,
        "retrying": 0,
        "failed": 0,
    }
    assert field not in redis.hashes[GLOBAL_SESSIONS_KEY]


@pytest.mark.asyncio
async def test_active_heartbeat_refreshes_expiry_without_resetting_current_state():
    clock = Clock(1_000.0)
    blocker = asyncio.Event()

    async def heartbeat_sleep(delay):
        if clock.value == 1_000.0:
            clock.value += delay
            return
        await blocker.wait()

    redis = FakeRedis()
    sessions = GlobalGenerationSessions(
        redis,
        sync=lambda _connection, *, only_cache_keys: _ready(*only_cache_keys),
        clock=clock,
        heartbeat_sleep=heartbeat_sleep,
        heartbeat_interval=30.0,
        session_ttl=90.0,
    )
    connection = object()
    await sessions.register("active", connection)
    await sessions.sync_on_connect(
        connection,
        accepted_generation=4,
        checksum=CHECKSUM,
        packs=[_pack("a")],
    )
    for _ in range(20):
        if redis.touch_calls:
            break
        await asyncio.sleep(0)

    row = json.loads(next(iter(redis.hashes[GLOBAL_SESSIONS_KEY].values())))
    assert redis.touch_calls == 1
    assert row["expiresAt"] == 1_120.0
    assert row["observedGeneration"] == 4
    assert row["status"] == "current"
    assert await sessions.aggregate(4) == {
        "connected": 1,
        "current": 1,
        "retrying": 0,
        "failed": 0,
    }
    await sessions.unregister("active", connection)


@pytest.mark.asyncio
async def test_expired_lease_cannot_be_revived_by_recovered_heartbeat():
    clock = Clock(100.0)
    stalled = asyncio.Event()

    async def heartbeat_sleep(delay):
        if not stalled.is_set():
            clock.value += 91.0
            stalled.set()
            return
        await asyncio.Future()

    redis = FakeRedis()
    sessions = GlobalGenerationSessions(
        redis,
        clock=clock,
        heartbeat_sleep=heartbeat_sleep,
        heartbeat_interval=30.0,
        session_ttl=90.0,
    )
    connection = object()
    await sessions.register("stalled", connection)
    session = sessions._by_connection[id(connection)]
    heartbeat_task = session.heartbeat_task
    assert heartbeat_task is not None
    await stalled.wait()
    for _ in range(20):
        if heartbeat_task.done():
            break
        await asyncio.sleep(0)

    assert await sessions._shared_fence(session) is False
    assert heartbeat_task.done()
    assert redis.touch_calls == 0
    assert await sessions.aggregate(1) == {
        "connected": 0,
        "current": 0,
        "retrying": 0,
        "failed": 0,
    }
    await sessions.unregister("stalled", connection)


@pytest.mark.asyncio
async def test_touch_rejects_expiry_boundary_and_nonnumeric_expiry():
    clock = Clock(100.0)
    redis = FakeRedis()
    sessions = GlobalGenerationSessions(redis, clock=clock)
    connection = object()
    await sessions.register("boundary", connection)
    session = sessions._by_connection[id(connection)]
    field = session.raw_hash

    clock.value = 190.0
    assert await sessions._touch_shared(session) is False
    assert redis.touch_calls == 0

    row = json.loads(redis.hashes[GLOBAL_SESSIONS_KEY][field])
    row["expiresAt"] = "not-a-number"
    redis.hashes[GLOBAL_SESSIONS_KEY][field] = json.dumps(row)
    clock.value = 150.0
    assert await sessions._touch_shared(session) is False
    assert redis.touch_calls == 0
    await sessions.unregister("boundary", connection)


@pytest.mark.asyncio
async def test_expired_direct_status_update_cannot_refresh_lease():
    clock = Clock(100.0)
    redis = FakeRedis()
    sessions = GlobalGenerationSessions(redis, clock=clock)
    connection = object()
    await sessions.register("expired-status", connection)
    session = sessions._by_connection[id(connection)]
    original = json.loads(redis.hashes[GLOBAL_SESSIONS_KEY][session.raw_hash])

    clock.value = 190.0
    assert await sessions._set_status(session, status="failed") is False
    unchanged = json.loads(redis.hashes[GLOBAL_SESSIONS_KEY][session.raw_hash])
    assert unchanged == original
    assert (await sessions.aggregate(1))["connected"] == 0
    await sessions.unregister("expired-status", connection)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("accepted_generation", "packs", "expected"),
    [
        (0, [_pack("a")], {"state": "failed", "errorCode": "invalid_generation"}),
        (1, [_pack("a"), _pack("a")], {"state": "failed", "errorCode": "invalid_packs"}),
    ],
)
async def test_expired_public_validation_failure_cannot_revive_lease(
    accepted_generation, packs, expected
):
    clock = Clock(100.0)
    redis = FakeRedis()
    sessions = GlobalGenerationSessions(redis, clock=clock)
    connection = object()
    await sessions.register("expired-public", connection)
    session = sessions._by_connection[id(connection)]
    original_expiry = json.loads(redis.hashes[GLOBAL_SESSIONS_KEY][session.raw_hash])["expiresAt"]

    clock.value = 191.0
    result = await sessions.sync_on_connect(
        connection,
        accepted_generation=accepted_generation,
        checksum=CHECKSUM,
        packs=packs,
    )

    row = json.loads(redis.hashes[GLOBAL_SESSIONS_KEY][session.raw_hash])
    assert result == expected
    assert row["expiresAt"] == original_expiry
    assert (await sessions.aggregate(1))["connected"] == 0
    await sessions.unregister("expired-public", connection)


@pytest.mark.asyncio
async def test_active_status_update_refreshes_expiry_and_preserves_fence():
    clock = Clock(100.0)
    redis = FakeRedis()
    sessions = GlobalGenerationSessions(redis, clock=clock)
    connection = object()
    await sessions.register("active-status", connection)
    session = sessions._by_connection[id(connection)]

    clock.value = 150.0
    assert await sessions._set_status(session, status="failed") is True

    row = json.loads(redis.hashes[GLOBAL_SESSIONS_KEY][session.raw_hash])
    assert row["expiresAt"] == 240.0
    assert row["status"] == "failed"
    assert await sessions._shared_fence(session) is True
    await sessions.unregister("active-status", connection)


@pytest.mark.asyncio
async def test_expired_cleanup_cannot_delete_newer_replacement():
    clock = Clock(100.0)

    class ReplacingRedis(FakeRedis):
        async def eval(self, script, numkeys, *args):
            if "DELETE_SESSION" in script:
                key, field = args[:2]
                self.hashes.setdefault(key, {})[field] = json.dumps(
                    {
                        "sessionId": "new-session",
                        "connectionGeneration": 8,
                        "observedGeneration": None,
                        "observedChecksum": None,
                        "status": "retrying",
                        "retryAttempt": 0,
                        "expiresAt": 190.0,
                    }
                )
            return await super().eval(script, numkeys, *args)

    redis = ReplacingRedis()
    field = hashlib.sha256(b"same").hexdigest()
    redis.hashes[GLOBAL_SESSIONS_KEY] = {
        field: json.dumps(
            {
                "sessionId": "old-session",
                "connectionGeneration": 7,
                "observedGeneration": 3,
                "observedChecksum": CHECKSUM,
                "status": "current",
                "retryAttempt": 0,
                "expiresAt": 99.0,
            }
        )
    }
    sessions = GlobalGenerationSessions(redis, clock=clock)

    assert (await sessions.aggregate(3))["connected"] == 0
    replacement = json.loads(redis.hashes[GLOBAL_SESSIONS_KEY][field])
    assert replacement["connectionGeneration"] == 8
    assert (await sessions.aggregate(3))["connected"] == 1


@pytest.mark.asyncio
async def test_heartbeat_store_outage_does_not_advance_or_leak_errors():
    clock = Clock(100.0)
    second_sleep = asyncio.Event()

    class OutageRedis(FakeRedis):
        fail_reads = False

        async def hget(self, key, field):
            if self.fail_reads:
                raise RuntimeError("redis://secret@private/heartbeat")
            return await super().hget(key, field)

    redis = OutageRedis()

    async def heartbeat_sleep(delay):
        if not redis.fail_reads:
            redis.fail_reads = True
            clock.value += delay
            return
        second_sleep.set()
        await asyncio.Future()

    sessions = GlobalGenerationSessions(
        redis,
        clock=clock,
        heartbeat_sleep=heartbeat_sleep,
        heartbeat_interval=30.0,
        session_ttl=90.0,
    )
    connection = object()
    await sessions.register("outage", connection)
    session = sessions._by_connection[id(connection)]
    await second_sleep.wait()

    row = json.loads(next(iter(redis.hashes[GLOBAL_SESSIONS_KEY].values())))
    assert row["observedGeneration"] is None
    assert row["expiresAt"] == 190.0
    assert session.heartbeat_task is not None
    assert not session.heartbeat_task.done()
    heartbeat_task = session.heartbeat_task
    await sessions.unregister("outage", connection)
    assert heartbeat_task.done()


@pytest.mark.asyncio
async def test_malformed_shared_row_is_repaired_on_register_and_rejected_by_other_scripts():
    redis = FakeRedis()
    field = hashlib.sha256(b"malformed").hexdigest()
    redis.hashes[GLOBAL_SESSIONS_KEY] = {field: "{not-json"}
    sessions = GlobalGenerationSessions(redis)
    connection = object()

    await sessions.register("malformed", connection)

    repaired = json.loads(redis.hashes[GLOBAL_SESSIONS_KEY][field])
    assert repaired["connectionGeneration"] == 1
    session = sessions._by_connection[id(connection)]
    redis.hashes[GLOBAL_SESSIONS_KEY][field] = "{still-not-json"
    assert await sessions._set_status(session, status="failed") is False
    assert await sessions._touch_shared(session) is False
    assert await sessions._delete_shared(session) is False
    assert redis.hashes[GLOBAL_SESSIONS_KEY][field] == "{still-not-json"
    await sessions.unregister("malformed", connection)


def test_lua_scripts_use_protected_json_decode():
    module = inspect.getmodule(GlobalGenerationSessions)
    assert module is not None
    source = inspect.getsource(module)
    assert source.count("pcall(cjson.decode") >= 4


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
async def test_connection_does_not_run_unconditional_full_cache_sync_by_default(monkeypatch):
    calls = []

    async def drain(_connection):
        calls.append("drain")

    async def sync(_connection):
        calls.append("sync")
        return {"state": "done"}

    monkeypatch.delenv("TBOT_ENABLE_BACKGROUND_WORKERS", raising=False)
    monkeypatch.delenv("LESSON_SD_LEGACY_DEVICE_WORKER_ENABLED", raising=False)
    monkeypatch.delenv("LESSON_SD_SYNC_ON_CONNECT_ENABLED", raising=False)
    monkeypatch.setattr(sd_pack_fanout, "drain_pending_for_connection", drain)
    monkeypatch.setattr(sd_pack_sync, "sync_cached_lesson_assets_to_sd", sync)

    result = await _connection_for_background_sync()._sync_cached_lesson_assets_to_sd()

    assert calls == []
    assert result == {"pending": None, "full": {"skipped": "on_connect_disabled"}}


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
    monkeypatch.setenv("LESSON_SD_SYNC_ON_CONNECT_ENABLED", "true")
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
