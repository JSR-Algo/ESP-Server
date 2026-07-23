from __future__ import annotations

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


class Clock:
    def __init__(self, epoch):
        self.epoch = float(epoch)

    def __call__(self):
        return self.epoch


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
async def test_redis_atomic_clear_does_not_erase_newly_marked_key():
    redis = FakeRedis()
    clock = Clock(1_700_000_000)
    store = RedisLessonSdPendingStore(redis, namespace="ns", clock=clock, random=lambda: 0.0)
    await store.mark("dev-1", {"lesson-a/v1"})

    await store.clear("dev-1", {"lesson-a/v1"}, expected_cache_keys={"lesson-a/v1"})
    await store.mark("dev-1", {"lesson-b/v1"})

    assert (await store.load("dev-1"))["cacheKeys"] == ["lesson-b/v1"]
