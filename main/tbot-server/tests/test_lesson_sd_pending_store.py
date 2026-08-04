from __future__ import annotations

import json

import pytest

from core.lesson.sd_pack_pending_store import (
    InMemoryLessonSdPendingStore,
    RedisLessonSdPendingStore,
    create_lesson_sd_pending_store,
)


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.zsets = {}
        self.ttls = {}

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, ex=None):
        self.values[key] = value
        if ex is not None:
            self.ttls[key] = ex

    async def delete(self, *keys):
        for key in keys:
            self.values.pop(key, None)
            self.zsets.pop(key, None)

    async def expire(self, key, ttl):
        self.ttls[key] = ttl

    async def zadd(self, key, mapping):
        self.zsets.setdefault(key, {}).update(mapping)

    async def zrem(self, key, *members):
        current = self.zsets.setdefault(key, {})
        for member in members:
            current.pop(member, None)

    async def zrange(self, key, start=0, end=-1, withscores=False):
        members = sorted(self.zsets.get(key, {}).items(), key=lambda item: (item[1], item[0]))
        stop = None if end == -1 else end + 1
        selected = members[start:stop]
        if withscores:
            return selected
        return [member for member, _score in selected]

    async def zrangebyscore(self, key, min_score, max_score, start=0, num=None):
        current = self.zsets.get(key, {})
        min_score = float("-inf") if min_score == "-inf" else float(min_score)
        max_score = float("inf") if max_score == "+inf" else float(max_score)
        members = [
            member
            for member, score in sorted(current.items(), key=lambda item: (item[1], item[0]))
            if min_score <= float(score) <= max_score
        ]
        if num is None:
            return members[start:]
        return members[start : start + num]

    async def zcard(self, key):
        return len(self.zsets.get(key, {}))

    async def zscore(self, key, member):
        return self.zsets.get(key, {}).get(member)

class RedisApiOnlyFake:
    def __init__(self):
        self.values = {}
        self._zsets = {}

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, ex=None):
        self.values[key] = value

    async def delete(self, *keys):
        for key in keys:
            self.values.pop(key, None)
            self._zsets.pop(key, None)

    async def expire(self, key, ttl):
        return None

    async def zadd(self, key, mapping):
        self._zsets.setdefault(key, {}).update(mapping)

    async def zrem(self, key, *members):
        current = self._zsets.setdefault(key, {})
        for member in members:
            current.pop(member, None)

    async def zrange(self, key, start=0, end=-1, withscores=False):
        members = sorted(self._zsets.get(key, {}).items(), key=lambda item: (item[1], item[0]))
        stop = None if end == -1 else end + 1
        selected = members[start:stop]
        if withscores:
            return selected
        return [member for member, _score in selected]

    async def zrangebyscore(self, key, min_score, max_score, start=0, num=None):
        current = self._zsets.get(key, {})
        min_score = float("-inf") if min_score == "-inf" else float(min_score)
        max_score = float("inf") if max_score == "+inf" else float(max_score)
        members = [
            member
            for member, score in sorted(current.items(), key=lambda item: (item[1], item[0]))
            if min_score <= float(score) <= max_score
        ]
        if num is None:
            return members[start:]
        return members[start : start + num]

    async def zscore(self, key, member):
        return self._zsets.get(key, {}).get(member)

class EvalRedisFake(RedisApiOnlyFake):
    def __init__(self, *, cluster_enabled=0):
        super().__init__()
        self.cluster_enabled = cluster_enabled
        self.eval_calls = []
        self.info_calls = []

    async def info(self, section=None):
        self.info_calls.append(section)
        return {"cluster_enabled": self.cluster_enabled}

    async def eval(self, script, numkeys, *args):
        keys = args[:numkeys]
        argv = args[numkeys:]
        self.eval_calls.append((numkeys, keys, argv))
        if "lesson-sd-mark-callbacks" in script:
            value_key, due_key, created_key = keys
            device_id = argv[0]
            additions = json.loads(argv[1])
            work = json.loads(argv[2])
            existing = await self.get(value_key)
            if existing:
                current = json.loads(existing)
                work["cacheKeys"] = current.get("cacheKeys", [])
                work["callbackResults"] = {
                    **current.get("callbackResults", {}),
                    **additions,
                }
                work["createdAt"] = current.get("createdAt") or work["createdAt"]
                work["attemptCount"] = int(current.get("attemptCount") or 0) + 1
            encoded = json.dumps(work, separators=(",", ":"))
            await self.set(value_key, encoded, ex=int(argv[5]))
            current_due = float((await self.zscore(due_key, device_id)) or 0.0)
            await self.zadd(due_key, {device_id: max(current_due, float(argv[3]))})
            await self.zadd(created_key, {device_id: float(argv[4])})
            await self.expire(due_key, int(argv[5]))
            await self.expire(created_key, int(argv[5]))
            return encoded
        if "lesson-sd-clear-callbacks" in script:
            value_key, due_key, created_key = keys
            device_id = argv[0]
            clear = set(json.loads(argv[1]))
            expected = json.loads(argv[2])
            existing = await self.get(value_key)
            if not existing:
                await self.zrem(due_key, device_id)
                await self.zrem(created_key, device_id)
                return 0
            current = json.loads(existing)
            remaining = {
                key: result
                for key, result in current.get("callbackResults", {}).items()
                if key not in clear or (key in expected and result != expected[key])
            }
            if remaining:
                current["callbackResults"] = remaining
            else:
                current.pop("callbackResults", None)
            if not current.get("cacheKeys") and not remaining:
                await self.delete(value_key)
                await self.zrem(due_key, device_id)
                await self.zrem(created_key, device_id)
            else:
                await self.set(value_key, json.dumps(current, separators=(",", ":")), ex=int(argv[3]))
                await self.expire(due_key, int(argv[3]))
                await self.expire(created_key, int(argv[3]))
            return 1
        if "return encoded" in script:
            value_key, due_key, created_key = keys
            device_id = argv[0]
            cache_keys = json.loads(argv[1])
            work = json.loads(argv[3])
            existing = await self.get(value_key)
            if existing:
                current = json.loads(existing)
                work["cacheKeys"] = sorted({*current.get("cacheKeys", []), *cache_keys})
                if current.get("callbackResults"):
                    work["callbackResults"] = current["callbackResults"]
                work["createdAt"] = current.get("createdAt") or work["createdAt"]
                work["attemptCount"] = int(current.get("attemptCount") or 0) + 1
            encoded = json.dumps(work, separators=(",", ":"))
            await self.set(value_key, encoded, ex=int(argv[6]))
            await self.zadd(due_key, {device_id: float(argv[4])})
            await self.zadd(created_key, {device_id: float(argv[5])})
            await self.expire(due_key, int(argv[6]))
            await self.expire(created_key, int(argv[6]))
            return encoded
        if "ZRANGEBYSCORE" in script:
            (due_key,) = keys
            now = float(argv[0])
            limit = int(argv[1])
            lease_score = float(argv[2])
            namespace = argv[3]
            members = await self.zrangebyscore(due_key, "-inf", now, start=0, num=limit)
            claimed = []
            for member in members:
                value_key = f"{namespace}:lesson-sd-pending:{member}"
                if await self.get(value_key):
                    await self.zadd(due_key, {member: lease_score})
                    claimed.append(member)
                else:
                    await self.zrem(due_key, member)
            return claimed
        value_key, due_key, created_key = keys
        device_id = argv[0]
        clear = set(json.loads(argv[1]))
        expected = sorted(json.loads(argv[2]))
        existing = await self.get(value_key)
        if not existing:
            await self.zrem(due_key, device_id)
            await self.zrem(created_key, device_id)
            return 0
        current = json.loads(existing)
        current_keys = sorted(current.get("cacheKeys", []))
        remaining = [key for key in current_keys if key not in clear]
        if not remaining:
            if not expected or current_keys == expected:
                if current.get("callbackResults"):
                    current["cacheKeys"] = []
                    await self.set(value_key, json.dumps(current, separators=(",", ":")), ex=int(argv[3]))
                    await self.expire(due_key, int(argv[3]))
                    await self.expire(created_key, int(argv[3]))
                else:
                    await self.delete(value_key)
                    await self.zrem(due_key, device_id)
                    await self.zrem(created_key, device_id)
        else:
            current["cacheKeys"] = remaining
            await self.set(value_key, json.dumps(current, separators=(",", ":")), ex=int(argv[3]))
            await self.expire(due_key, int(argv[3]))
            await self.expire(created_key, int(argv[3]))
        return 1


class Clock:
    def __init__(self, epoch):
        self.epoch = float(epoch)

    def __call__(self):
        return self.epoch


CALLBACK_RESULT_A = {
    "cacheKey": "lesson-a/v1",
    "deviceId": "dev-1",
    "ready": True,
    "fileCount": 12,
}

CALLBACK_RESULT_B = {
    "cacheKey": "lesson-b/v1",
    "deviceId": "dev-1",
    "ready": False,
    "errorCode": "ASSET_MISSING",
}


@pytest.mark.asyncio
async def test_memory_callback_results_are_keyed_and_removed_independently_from_robot_work():
    store = InMemoryLessonSdPendingStore(
        clock=Clock(1_700_000_000),
        random=lambda: 0.0,
    )
    await store.mark("dev-1", {"lesson-a/v1"})

    await store.mark_callbacks("dev-1", [CALLBACK_RESULT_A, CALLBACK_RESULT_B])
    await store.clear("dev-1", {"lesson-a/v1"})

    pending = await store.load("dev-1")
    assert pending["cacheKeys"] == []
    assert pending["callbackResults"] == {
        "lesson-a/v1": CALLBACK_RESULT_A,
        "lesson-b/v1": CALLBACK_RESULT_B,
    }

    await store.clear_callbacks("dev-1", {"lesson-a/v1"})
    assert (await store.load("dev-1"))["callbackResults"] == {
        "lesson-b/v1": CALLBACK_RESULT_B,
    }
    await store.clear_callbacks("dev-1", {"lesson-b/v1"})
    assert await store.load("dev-1") is None


@pytest.mark.asyncio
async def test_clear_callbacks_cas_preserves_newer_result_for_same_cache_key():
    store = InMemoryLessonSdPendingStore(random=lambda: 0.0)
    await store.mark_callbacks("dev-1", [CALLBACK_RESULT_A])
    newer = {**CALLBACK_RESULT_A, "downloadedCount": 9}
    await store.mark_callbacks("dev-1", [newer])

    await store.clear_callbacks(
        "dev-1",
        {"lesson-a/v1"},
        expected_results={"lesson-a/v1": CALLBACK_RESULT_A},
    )

    assert (await store.load("dev-1"))["callbackResults"] == {
        "lesson-a/v1": newer
    }


@pytest.mark.asyncio
async def test_mark_callbacks_does_not_shorten_active_claim_lease():
    clock = Clock(1_700_000_000)
    store = InMemoryLessonSdPendingStore(
        clock=clock,
        random=lambda: 0.0,
        lease_sec=60,
    )
    await store.mark_callbacks("dev-1", [CALLBACK_RESULT_A])
    clock.epoch += 2
    assert await store.claim_due(limit=1) == ["dev-1"]
    leased_until = (await store.load("dev-1"))["nextAttemptAt"]

    await store.mark_callbacks("dev-1", [CALLBACK_RESULT_A])

    assert (await store.load("dev-1"))["nextAttemptAt"] == leased_until
    assert await store.claim_due(limit=1) == []


@pytest.mark.asyncio
async def test_redis_loads_legacy_work_without_adding_empty_callback_results():
    redis = FakeRedis()
    redis.values["ns:lesson-sd-pending:dev-1"] = json.dumps(
        {
            "cacheKeys": ["lesson-a/v1"],
            "attemptCount": 2,
            "createdAt": "2023-11-14T22:13:20Z",
            "nextAttemptAt": "2023-11-14T22:13:22Z",
        }
    )
    store = RedisLessonSdPendingStore(redis, namespace="ns")

    assert await store.load("dev-1") == {
        "cacheKeys": ["lesson-a/v1"],
        "attemptCount": 2,
        "createdAt": "2023-11-14T22:13:20Z",
        "nextAttemptAt": "2023-11-14T22:13:22Z",
    }


@pytest.mark.asyncio
async def test_redis_robot_mark_and_clear_preserve_callback_results_without_lua():
    redis = FakeRedis()
    clock = Clock(1_700_000_000)
    store = RedisLessonSdPendingStore(
        redis,
        namespace="ns",
        ttl_sec=123,
        clock=clock,
        random=lambda: 0.0,
    )
    await store.mark_callbacks("dev-1", [CALLBACK_RESULT_A])
    await store.mark("dev-1", {"lesson-b/v1"})
    await store.clear("dev-1", {"lesson-b/v1"})

    assert await store.load("dev-1") == {
        "cacheKeys": [],
        "callbackResults": {"lesson-a/v1": CALLBACK_RESULT_A},
        "attemptCount": 2,
        "createdAt": "2023-11-14T22:13:20Z",
        "nextAttemptAt": "2023-11-14T22:13:22Z",
    }

    await store.clear_callbacks("dev-1", {"lesson-a/v1"})
    assert await store.load("dev-1") is None


@pytest.mark.asyncio
async def test_redis_mark_load_due_clear_snapshot_persists_across_instances():
    redis = FakeRedis()
    clock = Clock(1_700_000_000)
    store = RedisLessonSdPendingStore(
        redis,
        namespace="ns",
        ttl_sec=123,
        clock=clock,
        random=lambda: 0.0,
    )

    await store.mark("dev-1", {"lesson-b/v2", "lesson-a/v1"})
    clock.epoch += 10
    await store.mark("dev-1", {"lesson-c/v3", "lesson-a/v1"})

    reloaded = RedisLessonSdPendingStore(
        redis,
        namespace="ns",
        ttl_sec=123,
        clock=clock,
        random=lambda: 0.0,
    )
    work = await reloaded.load("dev-1")

    assert work == {
        "cacheKeys": ["lesson-a/v1", "lesson-b/v2", "lesson-c/v3"],
        "attemptCount": 2,
        "createdAt": "2023-11-14T22:13:20Z",
        "nextAttemptAt": "2023-11-14T22:13:32Z",
    }
    assert redis.ttls["ns:lesson-sd-pending:dev-1"] == 123
    assert redis.ttls["ns:lesson-sd-pending:due"] == 123

    clock.epoch = 1_700_000_032
    assert await reloaded.due(limit=10) == ["dev-1"]
    assert await reloaded.snapshot() == {"dev-1": work}

    await reloaded.clear("dev-1", {"lesson-b/v2"})
    assert (await reloaded.load("dev-1"))["cacheKeys"] == ["lesson-a/v1", "lesson-c/v3"]

    await reloaded.clear("dev-1", {"lesson-a/v1", "lesson-c/v3"})
    assert await reloaded.load("dev-1") is None
    assert await reloaded.due(limit=10) == []


@pytest.mark.asyncio
async def test_memory_store_rejects_production_without_explicit_allow(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("LESSON_SD_PENDING_ALLOW_MEMORY", raising=False)
    monkeypatch.setenv("ENV", "production")

    with pytest.raises(RuntimeError, match="Redis"):
        create_lesson_sd_pending_store()

    monkeypatch.setenv("LESSON_SD_PENDING_ALLOW_MEMORY", "true")
    assert isinstance(create_lesson_sd_pending_store(), InMemoryLessonSdPendingStore)


@pytest.mark.asyncio
async def test_pending_age_metric_reports_oldest_due_age_seconds():
    clock = Clock(1_700_000_100)
    store = InMemoryLessonSdPendingStore(clock=clock, random=lambda: 0.0)
    await store.mark("dev-1", {"lesson-a/v1"})
    clock.epoch += 90

    assert store.metrics()["lesson_sd_pending_age_seconds"] == 90


@pytest.mark.asyncio
async def test_redis_claim_due_is_exclusive_and_leases_before_transfer():
    redis = FakeRedis()
    clock = Clock(1_700_000_000)
    store = RedisLessonSdPendingStore(
        redis,
        namespace="ns",
        ttl_sec=123,
        lease_sec=30,
        clock=clock,
        random=lambda: 0.0,
    )
    await store.mark("dev-1", {"lesson-a/v1"})
    clock.epoch += 2

    assert await store.claim_due(limit=10) == ["dev-1"]
    assert await store.claim_due(limit=10) == []
    assert redis.zsets["ns:lesson-sd-pending:due"]["dev-1"] == 1_700_000_032


@pytest.mark.asyncio
async def test_redis_metrics_reports_oldest_pending_age_after_restart():
    redis = FakeRedis()
    clock = Clock(1_700_000_000)
    store = RedisLessonSdPendingStore(redis, namespace="ns", clock=clock, random=lambda: 0.0)
    await store.mark("newer", {"lesson-b/v1"})
    clock.epoch += 40
    await store.mark("older", {"lesson-a/v1"})
    clock.epoch += 50

    reloaded = RedisLessonSdPendingStore(redis, namespace="ns", clock=clock, random=lambda: 0.0)

    assert reloaded.metrics()["lesson_sd_pending_age_seconds"] == 90

@pytest.mark.asyncio
async def test_redis_metrics_uses_redis_api_and_skips_stale_created_members():
    redis = RedisApiOnlyFake()
    clock = Clock(1_700_000_000)
    store = RedisLessonSdPendingStore(redis, namespace="ns", clock=clock, random=lambda: 0.0)
    await store.mark("stale", {"lesson-stale/v1"})
    await redis.delete("ns:lesson-sd-pending:stale")
    clock.epoch += 40
    await store.mark("valid", {"lesson-a/v1"})
    clock.epoch += 50

    assert store.metrics()["lesson_sd_pending_age_seconds"] == 50


@pytest.mark.asyncio
async def test_redis_atomic_clear_does_not_erase_newly_marked_key():
    redis = FakeRedis()
    clock = Clock(1_700_000_000)
    store = RedisLessonSdPendingStore(redis, namespace="ns", clock=clock, random=lambda: 0.0)
    await store.mark("dev-1", {"lesson-a/v1"})

    await store.clear("dev-1", {"lesson-a/v1"}, expected_cache_keys={"lesson-a/v1"})
    await store.mark("dev-1", {"lesson-b/v1"})

    assert (await store.load("dev-1"))["cacheKeys"] == ["lesson-b/v1"]

@pytest.mark.asyncio
async def test_redis_lua_path_requires_standalone_redis_and_sanitizes_cluster_error():
    redis = EvalRedisFake(cluster_enabled=1)
    store = RedisLessonSdPendingStore(redis, namespace="ns", random=lambda: 0.0)

    with pytest.raises(RuntimeError) as exc_info:
        await store.mark("dev-1", {"lesson-a/v1"})

    message = str(exc_info.value)
    assert "standalone Redis" in message
    assert "redis://" not in message
    assert "password" not in message
    assert redis.eval_calls == []
    assert redis.info_calls == ["cluster"]

@pytest.mark.asyncio
async def test_redis_lua_scripts_cover_mark_claim_partial_and_final_clear():
    redis = EvalRedisFake(cluster_enabled=0)
    clock = Clock(1_700_000_000)
    store = RedisLessonSdPendingStore(redis, namespace="ns", clock=clock, random=lambda: 0.0)

    await store.mark("dev-1", {"lesson-a/v1", "lesson-b/v1"})
    clock.epoch += 2
    assert await store.claim_due(limit=5) == ["dev-1"]
    await store.clear(
        "dev-1",
        {"lesson-a/v1"},
        expected_cache_keys={"lesson-a/v1", "lesson-b/v1"},
    )
    assert (await store.load("dev-1"))["cacheKeys"] == ["lesson-b/v1"]
    await store.clear("dev-1", {"lesson-b/v1"}, expected_cache_keys={"lesson-b/v1"})
    assert await store.load("dev-1") is None

    assert [call[0] for call in redis.eval_calls] == [3, 1, 3, 3]
    assert redis.eval_calls[0][1] == (
        "ns:lesson-sd-pending:dev-1",
        "ns:lesson-sd-pending:due",
        "ns:lesson-sd-pending:created",
    )
    assert redis.eval_calls[1][1] == ("ns:lesson-sd-pending:due",)
    assert redis.info_calls == ["cluster"]


@pytest.mark.asyncio
async def test_redis_lua_robot_operations_preserve_callback_results():
    redis = EvalRedisFake(cluster_enabled=0)
    clock = Clock(1_700_000_000)
    store = RedisLessonSdPendingStore(
        redis,
        namespace="ns",
        ttl_sec=123,
        clock=clock,
        random=lambda: 0.0,
    )

    await store.mark_callbacks("dev-1", [CALLBACK_RESULT_A])
    clock.epoch += 2
    await store.mark("dev-1", {"lesson-b/v1"})
    await store.clear("dev-1", {"lesson-b/v1"})

    pending = await store.load("dev-1")
    assert pending["cacheKeys"] == []
    assert pending["callbackResults"] == {"lesson-a/v1": CALLBACK_RESULT_A}

    await store.clear_callbacks("dev-1", {"lesson-a/v1"})
    assert await store.load("dev-1") is None
    assert [call[0] for call in redis.eval_calls] == [3, 3, 3, 3]


@pytest.mark.asyncio
async def test_redis_lua_callback_cas_and_requeue_preserve_active_lease():
    redis = EvalRedisFake(cluster_enabled=0)
    clock = Clock(1_700_000_000)
    store = RedisLessonSdPendingStore(
        redis,
        namespace="ns",
        lease_sec=60,
        clock=clock,
        random=lambda: 0.0,
    )
    await store.mark_callbacks("dev-1", [CALLBACK_RESULT_A])
    clock.epoch += 2
    assert await store.claim_due(limit=1) == ["dev-1"]
    lease_score = redis._zsets["ns:lesson-sd-pending:due"]["dev-1"]

    newer = {**CALLBACK_RESULT_A, "downloadedCount": 9}
    await store.mark_callbacks("dev-1", [newer])
    await store.clear_callbacks(
        "dev-1",
        {"lesson-a/v1"},
        expected_results={"lesson-a/v1": CALLBACK_RESULT_A},
    )

    assert redis._zsets["ns:lesson-sd-pending:due"]["dev-1"] == lease_score
    assert (await store.load("dev-1"))["callbackResults"] == {
        "lesson-a/v1": newer
    }
