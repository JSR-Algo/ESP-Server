from __future__ import annotations

import pytest

from core.lesson.global_generation_store import (
    GLOBAL_GENERATION_KEY,
    GlobalGenerationStore,
    create_global_generation_store,
)

HEX = "a" * 64
HEX2 = "b" * 64


class FakePipeline:
    def __init__(self, redis):
        self.redis = redis
        self.commands = []
        self.executed = False

    async def __aenter__(self):
        self.redis.open_pipelines.append(self)
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def hset(self, key, mapping):
        self.commands.append(("hset", key, mapping))
        return self

    def hdel(self, key, *fields):
        self.commands.append(("hdel", key, fields))
        return self

    async def execute(self):
        self.executed = True
        self.redis.executed_batches.append(list(self.commands))
        for command in self.commands:
            if command[0] == "hset":
                _, key, mapping = command
                self.redis.hashes.setdefault(key, {}).update(mapping)
            elif command[0] == "hdel":
                _, key, fields = command
                current = self.redis.hashes.setdefault(key, {})
                for field in fields:
                    current.pop(field, None)


class FakeRedis:
    def __init__(self):
        self.hashes = {}
        self.hset_calls = []
        self.hdel_calls = []
        self.open_pipelines = []
        self.executed_batches = []

    async def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    async def hset(self, key, mapping):
        self.hset_calls.append((key, mapping))
        self.hashes.setdefault(key, {}).update(mapping)

    async def hdel(self, key, *fields):
        self.hdel_calls.append((key, fields))
        current = self.hashes.setdefault(key, {})
        for field in fields:
            current.pop(field, None)

    def pipeline(self, transaction=True):
        assert transaction is True
        return FakePipeline(self)


@pytest.mark.asyncio
async def test_snapshot_decodes_strings_bytes_missing_and_invalid_optional_ints():
    redis = FakeRedis()
    redis.hashes[GLOBAL_GENERATION_KEY] = {
        b"desiredGeneration": b"not-an-int",
        b"desiredIndexChecksum": HEX.encode(),
        b"etag": b"etag-1",
        b"acceptedGeneration": b"7",
        b"acceptedIndexChecksum": HEX2.encode(),
        b"materializationState": b"ready",
        b"retryAttempt": b"bad",
        b"nextRetryAt": b"2026-07-24T00:00:00Z",
        b"lastPollAt": b"2026-07-24T00:01:00Z",
        b"lastMaterializedAt": b"2026-07-24T00:02:00Z",
        b"lastErrorCode": b"network_timeout",
    }

    assert await GlobalGenerationStore(redis).snapshot() == {
        "desiredGeneration": None,
        "desiredIndexChecksum": HEX,
        "etag": "etag-1",
        "acceptedGeneration": 7,
        "acceptedIndexChecksum": HEX2,
        "materializationState": "ready",
        "retryAttempt": None,
        "nextRetryAt": "2026-07-24T00:00:00Z",
        "lastPollAt": "2026-07-24T00:01:00Z",
        "lastMaterializedAt": "2026-07-24T00:02:00Z",
        "lastErrorCode": "network_timeout",
    }

    empty = await GlobalGenerationStore(FakeRedis()).snapshot()
    assert list(empty) == [
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
    assert empty == dict.fromkeys(empty)


@pytest.mark.asyncio
async def test_mutations_persist_across_store_recreation():
    redis = FakeRedis()
    store = GlobalGenerationStore(redis)

    await store.set_desired(3, HEX, "etag-3")
    await store.mark_materializing(3)
    await store.mark_retry("network_timeout", 2, "2026-07-24T00:05:00Z")
    await store.mark_polled("2026-07-24T00:06:00Z")
    await store.accept(3, HEX, "2026-07-24T00:07:00Z")

    assert await GlobalGenerationStore(redis).snapshot() == {
        "desiredGeneration": 3,
        "desiredIndexChecksum": HEX,
        "etag": "etag-3",
        "acceptedGeneration": 3,
        "acceptedIndexChecksum": HEX,
        "materializationState": "ready",
        "retryAttempt": 0,
        "nextRetryAt": None,
        "lastPollAt": "2026-07-24T00:06:00Z",
        "lastMaterializedAt": "2026-07-24T00:07:00Z",
        "lastErrorCode": None,
    }


@pytest.mark.asyncio
async def test_accept_uses_one_transactional_pipeline_and_batch_is_visible_only_after_execute():
    redis = FakeRedis()
    await GlobalGenerationStore(redis).mark_retry("network_timeout", 1, "2026-07-24T00:05:00Z")
    before = dict(redis.hashes[GLOBAL_GENERATION_KEY])

    await GlobalGenerationStore(redis).accept(9, HEX, "2026-07-24T00:07:00Z")

    assert len(redis.open_pipelines) == 1
    assert len(redis.executed_batches) == 1
    assert redis.hset_calls == [
        (
            GLOBAL_GENERATION_KEY,
            {
                "materializationState": "retry_wait",
                "lastErrorCode": "network_timeout",
                "retryAttempt": "1",
                "nextRetryAt": "2026-07-24T00:05:00Z",
            },
        )
    ]
    assert redis.hdel_calls == []
    assert before == {
        "materializationState": "retry_wait",
        "lastErrorCode": "network_timeout",
        "retryAttempt": "1",
        "nextRetryAt": "2026-07-24T00:05:00Z",
    }
    assert redis.executed_batches[0] == [
        (
            "hset",
            GLOBAL_GENERATION_KEY,
            {
                "acceptedGeneration": "9",
                "acceptedIndexChecksum": HEX,
                "materializationState": "ready",
                "lastMaterializedAt": "2026-07-24T00:07:00Z",
                "retryAttempt": "0",
            },
        ),
        ("hdel", GLOBAL_GENERATION_KEY, ("nextRetryAt", "lastErrorCode")),
    ]


@pytest.mark.asyncio
async def test_validation_rejects_caller_bugs():
    store = GlobalGenerationStore(FakeRedis())

    invalid_calls = [
        store.set_desired(True, HEX, "etag"),
        store.set_desired(0, HEX, "etag"),
        store.set_desired(1, "A" * 64, "etag"),
        store.set_desired(1, HEX, ""),
        store.mark_materializing(False),
        store.accept(1, "abc", "now"),
        store.accept(1, HEX, ""),
        store.mark_retry("Network", 0, "later"),
        store.mark_retry("network-timeout", 0, "later"),
        store.mark_retry("network_timeout", True, "later"),
        store.mark_retry("network_timeout", -1, "later"),
        store.mark_retry("network_timeout", 0, ""),
        store.mark_polled(""),
    ]

    for call in invalid_calls:
        with pytest.raises(ValueError):
            await call


def test_factory_requires_redis_url_without_leaking_env(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)

    with pytest.raises(RuntimeError) as exc_info:
        create_global_generation_store()

    message = str(exc_info.value)
    assert "REDIS_URL" in message
    assert "redis://" not in message
    assert "password" not in message


def test_factory_uses_async_redis_from_url_with_decode_responses(monkeypatch):
    created = {}

    class FakeRedisAsyncio:
        @staticmethod
        def from_url(url, *, decode_responses):
            created["url"] = url
            created["decode_responses"] = decode_responses
            return "redis-client"

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "redis" and fromlist == ("asyncio",):
            return type("RedisModule", (), {"asyncio": FakeRedisAsyncio})
        return original_import(name, globals, locals, fromlist, level)

    original_import = __import__
    monkeypatch.setenv("REDIS_URL", "redis://:secret@example.test/0")
    monkeypatch.setattr("builtins.__import__", fake_import)

    store = create_global_generation_store()

    assert isinstance(store, GlobalGenerationStore)
    assert store.redis == "redis-client"
    assert created == {
        "url": "redis://:secret@example.test/0",
        "decode_responses": True,
    }
