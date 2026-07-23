from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.lesson.sd_pack_pending_store import InMemoryLessonSdPendingStore
from core.lesson.sd_pack_retry_worker import LessonSdOnlineIndex, LessonSdRetryWorker


class Clock:
    def __init__(self, epoch=1_700_000_000):
        self.epoch = float(epoch)

    def __call__(self):
        return self.epoch


@pytest.mark.asyncio
async def test_worker_retries_online_due_without_reconnect_and_respects_batch(monkeypatch):
    clock = Clock()
    store = InMemoryLessonSdPendingStore(clock=clock, random=lambda: 0.0)
    await store.mark("uuid-1", {"lesson-a/v1"})
    await store.mark("uuid-2", {"lesson-b/v1"})
    await store.mark("uuid-3", {"lesson-c/v1"})
    clock.epoch += 3
    calls = []

    async def drain(conn, *, store=None, pending=None, backend_device_id=None):
        calls.append((conn.name, backend_device_id, pending["cacheKeys"]))
        await store.clear(backend_device_id, pending["cacheKeys"])
        return {"synced": 1}

    monkeypatch.setattr("core.lesson.sd_pack_retry_worker.drain_pending_for_connection", drain)

    index = LessonSdOnlineIndex()
    index.upsert("uuid-1", SimpleNamespace(name="one"))
    index.upsert("uuid-2", SimpleNamespace(name="two"))
    index.upsert("uuid-3", SimpleNamespace(name="three"))
    worker = LessonSdRetryWorker(store, index, batch_size=2)

    result = await worker.run_once()

    assert result == {"checked": 2, "retried": 2, "skippedOffline": 0}
    assert len(calls) == 2
    assert [call[1] for call in calls] == ["uuid-1", "uuid-2"]
    assert await store.load("uuid-3") is not None


@pytest.mark.asyncio
async def test_worker_retains_pending_on_sync_or_callback_failure(monkeypatch):
    clock = Clock()
    store = InMemoryLessonSdPendingStore(clock=clock, random=lambda: 0.0)
    await store.mark("uuid-1", {"lesson-a/v1"})
    clock.epoch += 3

    async def drain(*_args, **_kwargs):
        raise RuntimeError("callback offline")

    monkeypatch.setattr("core.lesson.sd_pack_retry_worker.drain_pending_for_connection", drain)

    index = LessonSdOnlineIndex()
    index.upsert("uuid-1", SimpleNamespace(name="one"))
    result = await LessonSdRetryWorker(store, index, batch_size=10).run_once()

    assert result == {"checked": 1, "retried": 0, "skippedOffline": 0}
    assert (await store.load("uuid-1"))["cacheKeys"] == ["lesson-a/v1"]


@pytest.mark.asyncio
async def test_worker_intersects_online_index_with_current_connections(monkeypatch):
    clock = Clock()
    store = InMemoryLessonSdPendingStore(clock=clock, random=lambda: 0.0)
    await store.mark("uuid-1", {"lesson-a/v1"})
    clock.epoch += 3

    async def drain(*_args, **_kwargs):
        raise AssertionError("stale connection must not retry")

    monkeypatch.setattr("core.lesson.sd_pack_retry_worker.drain_pending_for_connection", drain)

    stale = SimpleNamespace(name="stale")
    index = LessonSdOnlineIndex()
    index.upsert("uuid-1", stale)
    worker = LessonSdRetryWorker(store, index, connections={})

    assert await worker.run_once() == {"checked": 1, "retried": 0, "skippedOffline": 1}


@pytest.mark.asyncio
async def test_online_index_resolves_mac_once_per_connection_and_invalidates(monkeypatch):
    calls = []

    async def resolver(_client, base_url, mac, *, logger=None):
        calls.append((base_url, mac))
        return "uuid-1", "token-1"

    monkeypatch.setattr("core.lesson.sd_pack_retry_worker.resolve_device_identity", resolver)

    index = LessonSdOnlineIndex(api_base="http://backend.test/v1")
    conn = SimpleNamespace(device_id="AA:BB", logger=None)

    assert await index.resolve_and_upsert(conn) == "uuid-1"
    assert await index.resolve_and_upsert(conn) == "uuid-1"
    assert calls == [("http://backend.test/v1", "AA:BB")]
    assert index.get("uuid-1") is conn

    index.invalidate_connection(conn)
    assert index.get("uuid-1") is None
    assert not hasattr(conn, "_lesson_sd_backend_device_id")


@pytest.mark.asyncio
async def test_worker_start_stop_has_single_lifecycle_task():
    store = InMemoryLessonSdPendingStore()
    index = LessonSdOnlineIndex()
    worker = LessonSdRetryWorker(store, index, interval_sec=60)

    first = worker.start()
    second = worker.start()
    assert first is second
    assert first is not None and not first.done()

    await worker.stop()
    assert first.done()
