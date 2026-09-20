"""Queue/outbox ownership through real async workers; HTTP/storage IO is injected."""
import asyncio
import copy
import json

import httpx
import pytest

from core.lesson import forwarder as module


def terminal(kind="lesson_completed"):
    return {"assignmentId": "owned-assignment", "sessionId": "owned-session",
            "events": [{"type": kind, "summary": {"stepsCompleted": 1}}]}


@pytest.fixture(autouse=True)
def isolated_memory(monkeypatch):
    monkeypatch.setattr(module, "_PENDING_TERMINAL_BATCHES", {})


class Store:
    def __init__(self):
        self.value = None
        self.writes = []

    async def store(self, device, batch):
        self.value = copy.deepcopy(batch)
        self.writes.append(copy.deepcopy(batch))

    async def load(self, device, assignment):
        return copy.deepcopy(self.value)

    async def clear(self, device, batch):
        if self.value == batch:
            self.value = None


@pytest.mark.asyncio
async def test_enqueue_captures_terminal_and_started_before_caller_mutates():
    sent = []
    async def post(client, base, device, batch, **kwargs):
        sent.append(copy.deepcopy(batch))
    f = module.LessonEventForwarder(device_id="device", base_url="offline", post_fn=post)
    started = {**terminal(), "events": [{"type": "lesson_started", "startedAt": 123}]}
    completed = terminal()
    try:
        f.enqueue(started)
        f.enqueue(completed)
        started["events"][0]["startedAt"] = 999
        completed["events"][0]["summary"]["stepsCompleted"] = 99
        await f.drain()
        assert sent[0]["events"] == [{"type": "lesson_started", "startedAt": 123}]
        assert sent[-1]["events"] == [{"type": "lesson_started", "startedAt": 123}, *terminal()["events"]]
    finally:
        await f.aclose()


@pytest.mark.asyncio
async def test_memory_store_load_does_not_expose_retained_value():
    store = module.MemoryTerminalReplayStore()
    batch = terminal()
    await store.store("device", batch)
    batch["events"][0]["summary"]["stepsCompleted"] = 7
    loaded = await store.load("device", "owned-assignment")
    assert loaded == terminal()
    loaded["events"].clear()
    assert await store.load("device", "owned-assignment") == terminal()


@pytest.mark.asyncio
@pytest.mark.parametrize("replacement", [terminal("lesson_cancelled"), None])
async def test_retry_does_not_overwrite_newer_terminal_or_recreate_expired_outbox(replacement):
    store = Store()
    sent = []
    async def post(client, base, device, batch, **kwargs):
        sent.append(copy.deepcopy(batch))
        store.value = copy.deepcopy(replacement)
        raise httpx.ReadTimeout("lost response")
    f = module.LessonEventForwarder(device_id="device", base_url="offline", post_fn=post,
        terminal_store=store, retry_backoff_sec=0, terminal_max_reenqueue_attempts=1)
    try:
        f.enqueue(terminal())
        await f.drain()
        assert len(sent) == 1
        assert store.writes == [terminal()]
        assert store.value == replacement
        assert not await f.replay_pending_terminal_event()
        assert len(sent) == 1
        assert store.value == replacement
        assert f.pending_terminal_batch is None
    finally:
        await f.aclose()


@pytest.mark.asyncio
async def test_transport_mutation_cannot_change_retained_or_retried_terminal():
    store = Store()
    sent = []
    async def post(client, base, device, batch, **kwargs):
        sent.append(copy.deepcopy(batch))
        batch["events"][0]["summary"]["stepsCompleted"] = 99
        if len(sent) == 1:
            raise httpx.ReadTimeout("lost response")
    f = module.LessonEventForwarder(device_id="device", base_url="offline", post_fn=post,
        terminal_store=store, retry_backoff_sec=0, terminal_max_reenqueue_attempts=1)
    try:
        f.enqueue(terminal())
        await f.drain()
        assert sent == [terminal(), terminal()]
        assert store.value is None
    finally:
        await f.aclose()


@pytest.mark.asyncio
async def test_reconnect_transport_mutation_cannot_change_clear_identity():
    store = Store()
    await store.store("device", terminal())
    async def post(client, base, device, batch, **kwargs):
        batch["events"][0]["summary"]["stepsCompleted"] = 99
    assert await module.replay_stored_terminal_event(device_id="device", assignment_id="owned-assignment",
        base_url="offline", post_fn=post, terminal_store=store)
    assert store.value is None


@pytest.mark.asyncio
async def test_boolean_payload_is_not_numeric_clear_identity():
    store = module.MemoryTerminalReplayStore()
    expected = terminal()
    replacement = terminal()
    replacement["events"][0]["summary"]["stepsCompleted"] = True
    await store.store("device", replacement)
    await store.clear("device", expected)
    assert await store.load("device", "owned-assignment") == replacement


@pytest.mark.asyncio
async def test_success_callback_cannot_change_the_clear_identity():
    store = Store()
    async def post(*args, **kwargs):
        return {"accepted": 1}
    def acknowledged(batch):
        batch["events"].clear()
    f = module.LessonEventForwarder(device_id="device", base_url="offline", post_fn=post, terminal_store=store)
    try:
        f.enqueue(terminal(), on_success=acknowledged)
        await f.drain()
        assert store.value is None
    finally:
        await f.aclose()


@pytest.mark.asyncio
async def test_equal_retry_delivers_without_rewriting_the_outbox():
    store = Store()
    sent = []
    async def post(client, base, device, batch, **kwargs):
        sent.append(copy.deepcopy(batch))
        if len(sent) == 1:
            raise httpx.ReadTimeout("lost response")
        return {"accepted": 0, "duplicates": 1, "rewardId": "committed-reward"}
    f = module.LessonEventForwarder(device_id="device", base_url="offline", post_fn=post,
        terminal_store=store, retry_backoff_sec=0, terminal_max_reenqueue_attempts=1)
    try:
        f.enqueue(terminal())
        await f.drain()
        assert sent == [terminal(), terminal()]
        assert store.writes == [terminal()]
        assert store.value is None
        assert f.pending_terminal_batch is None
    finally:
        await f.aclose()


@pytest.mark.asyncio
async def test_public_queue_retains_best_effort_delivery_during_store_outage():
    class Outage(Store):
        async def store(self, *args):
            raise ConnectionError("store unavailable")
        async def load(self, *args):
            raise ConnectionError("store unavailable")
    sent = []
    async def post(*args, **kwargs):
        sent.append(copy.deepcopy(args[3]))
        raise httpx.ReadTimeout("backend also unavailable")
    f = module.LessonEventForwarder(device_id="device", base_url="offline", post_fn=post,
        terminal_store=Outage(), retry_backoff_sec=0, terminal_max_reenqueue_attempts=1)
    try:
        f.enqueue(terminal())
        await f.drain()
        assert sent == [terminal(), terminal()]
        assert f.pending_terminal_batch == terminal()
        assert not await f.replay_pending_terminal_event()
        assert sent == [terminal(), terminal(), terminal()]
        assert f.pending_terminal_batch == terminal()
    finally:
        await f.aclose()


@pytest.mark.asyncio
async def test_cancelled_retry_read_propagates_without_mutating_retained_work():
    class Interrupted(Store):
        async def load(self, *args):
            raise asyncio.CancelledError()
    store = Interrupted()
    await store.store("device", terminal())
    f = module.LessonEventForwarder(device_id="device", base_url="offline", terminal_store=store)
    f.pending_terminal_batch = terminal()
    with pytest.raises(asyncio.CancelledError):
        await f._terminal_retry_is_current(terminal())
    assert store.value == terminal()


@pytest.mark.asyncio
async def test_redis_clear_rejects_boolean_numeric_collision_before_lua():
    value = terminal()
    value["events"][0]["summary"]["stepsCompleted"] = True
    class Redis:
        async def get(self, key):
            return json.dumps(value)
        async def eval(self, *args):
            pytest.fail("nonidentical payload must not reach DEL CAS")
    store = module.RedisTerminalReplayStore(url="unused", client=Redis())
    await store.clear("device", terminal())


@pytest.mark.asyncio
@pytest.mark.parametrize("replacement", [terminal("lesson_cancelled"), None])
async def test_reconnect_rechecks_retained_owner_after_retry_budget_exhausted(replacement):
    store = Store()
    sent = []
    async def post(client, base, device, batch, **kwargs):
        sent.append(copy.deepcopy(batch))
        raise httpx.ReadTimeout("lost response")
    f = module.LessonEventForwarder(device_id="device", base_url="offline", post_fn=post,
        terminal_store=store, terminal_max_reenqueue_attempts=0)
    try:
        f.enqueue(terminal())
        await f.drain()
        store.value = copy.deepcopy(replacement)
        assert not await f.replay_pending_terminal_event()
        assert sent == [terminal()]
        assert store.value == replacement
        assert f.pending_terminal_batch is None
    finally:
        await f.aclose()


@pytest.mark.asyncio
async def test_reconnect_does_not_retire_newer_local_pending_during_store_read():
    newer = terminal("lesson_cancelled")
    class Replaced(Store):
        async def load(self, *args):
            f.pending_terminal_batch = newer
            return None
    async def post(*args, **kwargs):
        pytest.fail("stale reconnect must not POST")
    f = module.LessonEventForwarder(device_id="device", base_url="offline", post_fn=post,
        terminal_store=Replaced())
    f.pending_terminal_batch = terminal()
    assert not await f.replay_pending_terminal_event()
    assert f.pending_terminal_batch is newer


@pytest.mark.asyncio
async def test_assignment_terminal_callback_cannot_mutate_retained_or_dead_letter_batch():
    store = Store()
    async def post(*args, **kwargs):
        response = httpx.Response(409, json={"code": "ASSIGNMENT_CONFLICT", "message": "assignment is already terminal"},
            request=httpx.Request("POST", "http://backend/lesson-events"))
        raise httpx.HTTPStatusError("terminal", request=response.request, response=response)
    called = []
    def rejected(batch, exc):
        called.append(True)
        batch["events"].clear()
    f = module.LessonEventForwarder(device_id="device", base_url="offline", post_fn=post,
        terminal_store=store, on_assignment_terminal=rejected)
    try:
        f.enqueue(terminal())
        await f.drain()
        assert called == [True]
        assert f.pending_terminal_batch == terminal()
        assert f.dead_letters == [terminal()]
        assert store.value == terminal()
    finally:
        await f.aclose()
