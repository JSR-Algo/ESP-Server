import asyncio
import concurrent.futures
import threading

import pytest

from core.activity_lease import (
    ActivityLeaseCoordinator,
    ActivityLeaseInvariantError,
    ExclusiveDisposition,
    LeaseKind,
)


def _coordinator():
    return ActivityLeaseCoordinator(asyncio.get_running_loop())


@pytest.mark.asyncio
async def test_acquire_voice_requires_current_task_on_owner_loop():
    coordinator = _coordinator()
    lease = coordinator.try_acquire_voice("classic.start_to_chat")

    assert lease is not None
    assert lease.owner_task is asyncio.current_task()
    assert lease.kind is LeaseKind.VOICE
    lease.release()


@pytest.mark.asyncio
async def test_nested_voice_acquire_returns_distinct_handles_and_releases_by_depth():
    coordinator = _coordinator()
    first = coordinator.try_acquire_voice("google.open")
    second = coordinator.try_acquire_voice("google.send")

    assert first is not None and second is not None
    assert first is not second
    assert first.owner_task is second.owner_task
    assert coordinator.has_voice_leases()

    second.release()
    assert coordinator.has_voice_leases()
    first.release()
    assert not coordinator.has_voice_leases()


@pytest.mark.asyncio
async def test_voice_leases_are_shared_across_tasks():
    coordinator = _coordinator()
    acquired = asyncio.Event()
    release = asyncio.Event()
    owners = []

    async def worker(operation):
        lease = coordinator.try_acquire_voice(operation)
        assert lease is not None
        owners.append(lease.owner_task)
        if len(owners) == 2:
            acquired.set()
        await release.wait()
        lease.release()

    tasks = [
        asyncio.create_task(worker("voice.one")),
        asyncio.create_task(worker("voice.two")),
    ]
    await asyncio.wait_for(acquired.wait(), timeout=1)

    assert coordinator.has_voice_leases()
    assert owners[0] is not owners[1]
    assert coordinator.try_acquire_eviction("evict", busy_probe=lambda: False) is None

    release.set()
    await asyncio.gather(*tasks)
    assert not coordinator.has_voice_leases()


@pytest.mark.asyncio
async def test_exclusive_blocks_voice_and_is_non_reentrant():
    coordinator = _coordinator()
    exclusive = coordinator.try_acquire_eviction("lesson_cache.evict", busy_probe=lambda: False)

    assert exclusive is not None
    assert exclusive.kind is LeaseKind.EVICTION_EXCLUSIVE
    assert coordinator.has_exclusive_lease()
    assert coordinator.try_acquire_voice("google.open") is None
    assert coordinator.try_acquire_eviction("nested", busy_probe=lambda: False) is None

    exclusive.complete_exclusive(ExclusiveDisposition.DEFINITIVE)
    assert not coordinator.has_exclusive_lease()


@pytest.mark.asyncio
async def test_voice_owner_cannot_acquire_exclusive():
    coordinator = _coordinator()
    voice = coordinator.try_acquire_voice("classic.intent")

    assert voice is not None
    assert coordinator.try_acquire_eviction("evict", busy_probe=lambda: False) is None
    voice.release()


@pytest.mark.asyncio
async def test_busy_probe_true_or_exception_refuses_without_state_change():
    coordinator = _coordinator()

    assert coordinator.try_acquire_eviction("busy", busy_probe=lambda: True) is None

    def broken_probe():
        raise RuntimeError("private voice state")

    assert coordinator.try_acquire_eviction("broken", busy_probe=broken_probe) is None
    assert not coordinator.has_exclusive_lease()
    assert not coordinator.has_voice_leases()


@pytest.mark.asyncio
async def test_wrong_task_release_raises_without_mutating_state():
    coordinator = _coordinator()
    lease = coordinator.try_acquire_voice("voice.owner")
    assert lease is not None

    async def wrong_owner():
        with pytest.raises(ActivityLeaseInvariantError, match="wrong-task"):
            lease.release()

    await asyncio.create_task(wrong_owner())
    assert coordinator.has_voice_leases()
    lease.release()
    assert not coordinator.has_voice_leases()


@pytest.mark.asyncio
async def test_duplicate_wrong_kind_and_closed_release_raise_without_mutation():
    coordinator = _coordinator()
    voice = coordinator.try_acquire_voice("voice")
    assert voice is not None
    with pytest.raises(ActivityLeaseInvariantError, match="wrong-kind"):
        voice.complete_exclusive(ExclusiveDisposition.DEFINITIVE)
    assert coordinator.has_voice_leases()
    voice.release()
    with pytest.raises(ActivityLeaseInvariantError, match="duplicate"):
        voice.release()

    exclusive = coordinator.try_acquire_eviction("evict", busy_probe=lambda: False)
    assert exclusive is not None
    with pytest.raises(ActivityLeaseInvariantError, match="wrong-kind"):
        exclusive.release()
    assert coordinator.has_exclusive_lease()
    coordinator.close()
    with pytest.raises(ActivityLeaseInvariantError, match="closed"):
        exclusive.complete_exclusive(ExclusiveDisposition.DEFINITIVE)


@pytest.mark.asyncio
async def test_asyncio_future_owns_voice_release_until_exact_completion():
    coordinator = _coordinator()
    lease = coordinator.try_acquire_voice("classic.executor")
    delegated = asyncio.get_running_loop().create_future()
    assert lease is not None

    lease.release_when_done(delegated)
    with pytest.raises(ActivityLeaseInvariantError, match="delegated"):
        lease.release()
    assert coordinator.has_voice_leases()

    delegated.set_result("done")
    await asyncio.sleep(0)
    assert not coordinator.has_voice_leases()


@pytest.mark.asyncio
async def test_delegated_asyncio_future_cancellation_releases_voice():
    coordinator = _coordinator()
    lease = coordinator.try_acquire_voice("classic.executor")
    delegated = asyncio.get_running_loop().create_future()
    assert lease is not None
    lease.release_when_done(delegated)

    delegated.cancel()
    await asyncio.sleep(0)

    assert not coordinator.has_voice_leases()


@pytest.mark.asyncio
async def test_scheduling_task_double_cancel_does_not_release_delegated_future():
    coordinator = _coordinator()
    delegated = asyncio.get_running_loop().create_future()
    ready = asyncio.Event()

    async def scheduler():
        lease = coordinator.try_acquire_voice("classic.executor")
        assert lease is not None
        lease.release_when_done(delegated)
        ready.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(scheduler())
    await asyncio.wait_for(ready.wait(), timeout=1)
    task.cancel()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert coordinator.has_voice_leases()
    delegated.set_result(None)
    await asyncio.sleep(0)
    assert not coordinator.has_voice_leases()


@pytest.mark.asyncio
async def test_thread_future_callback_marshals_release_to_owner_loop():
    coordinator = _coordinator()
    lease = coordinator.try_acquire_voice("classic.thread_executor")
    delegated = concurrent.futures.Future()
    callback_thread = []
    worker_done = threading.Event()
    assert lease is not None
    lease.release_when_done(delegated)

    def complete_in_thread():
        callback_thread.append(threading.get_ident())
        delegated.set_result("done")
        worker_done.set()

    worker = threading.Thread(target=complete_in_thread)
    worker.start()
    worker.join(timeout=1)
    assert worker_done.is_set()
    assert callback_thread == [worker.ident]
    assert coordinator.has_voice_leases()

    await asyncio.sleep(0)
    assert not coordinator.has_voice_leases()


@pytest.mark.asyncio
async def test_delegated_cleanup_rejects_wrong_future_identity_without_mutation():
    coordinator = _coordinator()
    lease = coordinator.try_acquire_voice("classic.executor")
    delegated = asyncio.get_running_loop().create_future()
    wrong = asyncio.get_running_loop().create_future()
    assert lease is not None
    lease.release_when_done(delegated)

    with pytest.raises(ActivityLeaseInvariantError, match="future-mismatch"):
        coordinator._complete_delegated(lease.lease_id, wrong)
    assert coordinator.has_voice_leases()

    delegated.set_result(None)
    await asyncio.sleep(0)
    assert not coordinator.has_voice_leases()


@pytest.mark.asyncio
async def test_release_when_done_is_one_shot_and_voice_only():
    coordinator = _coordinator()
    voice = coordinator.try_acquire_voice("voice")
    first = asyncio.get_running_loop().create_future()
    second = asyncio.get_running_loop().create_future()
    assert voice is not None
    voice.release_when_done(first)
    with pytest.raises(ActivityLeaseInvariantError, match="delegated"):
        voice.release_when_done(second)
    first.cancel()
    await asyncio.sleep(0)

    exclusive = coordinator.try_acquire_eviction("evict", busy_probe=lambda: False)
    assert exclusive is not None
    with pytest.raises(ActivityLeaseInvariantError, match="wrong-kind"):
        exclusive.release_when_done(second)
    exclusive.complete_exclusive(ExclusiveDisposition.DEFINITIVE)


@pytest.mark.asyncio
async def test_exclusive_ambiguous_is_sticky_until_close():
    coordinator = _coordinator()
    exclusive = coordinator.try_acquire_eviction("evict", busy_probe=lambda: False)
    assert exclusive is not None

    exclusive.complete_exclusive(ExclusiveDisposition.AMBIGUOUS)

    assert coordinator.has_exclusive_lease()
    snapshot = coordinator.diagnostic_snapshot()
    assert snapshot["exclusive"] == {
        "kind": "eviction-exclusive",
        "operation": "evict",
        "sticky": True,
        "leaseIdSuffix": format(exclusive.lease_id, "x")[-6:],
        "ownerTaskIdSuffix": format(id(exclusive.owner_task), "x")[-6:],
    }
    assert exclusive.lease_id in coordinator._handles
    assert coordinator.try_acquire_voice("voice") is None
    assert coordinator.try_acquire_eviction("retry", busy_probe=lambda: False) is None
    with pytest.raises(ActivityLeaseInvariantError, match="duplicate"):
        exclusive.complete_exclusive(ExclusiveDisposition.DEFINITIVE)

    coordinator.close()
    assert not coordinator.has_exclusive_lease()
    assert coordinator.diagnostic_snapshot() == {
        "closed": True,
        "voiceLeaseCount": 0,
        "exclusive": None,
    }


@pytest.mark.asyncio
async def test_close_clears_all_records_and_refuses_new_acquisition():
    coordinator = _coordinator()
    voice = coordinator.try_acquire_voice("voice")
    assert voice is not None
    coordinator.close()

    assert not coordinator.has_voice_leases()
    assert not coordinator.has_exclusive_lease()
    assert coordinator.try_acquire_voice("after-close") is None
    assert coordinator.try_acquire_eviction("after-close", busy_probe=lambda: False) is None
    with pytest.raises(ActivityLeaseInvariantError, match="closed"):
        voice.release()


@pytest.mark.asyncio
async def test_future_from_another_loop_is_rejected_without_delegating():
    coordinator = _coordinator()
    voice = coordinator.try_acquire_voice("voice")
    other_loop = asyncio.new_event_loop()
    foreign = other_loop.create_future()
    assert voice is not None
    try:
        with pytest.raises(ActivityLeaseInvariantError, match="future-loop"):
            voice.release_when_done(foreign)
        assert coordinator.has_voice_leases()
        voice.release()
    finally:
        other_loop.close()


@pytest.mark.asyncio
async def test_thread_future_completion_after_close_does_not_schedule_on_loop(monkeypatch):
    coordinator = _coordinator()
    voice = coordinator.try_acquire_voice("classic.thread_executor")
    delegated = concurrent.futures.Future()
    assert voice is not None
    voice.release_when_done(delegated)
    coordinator.close()

    with monkeypatch.context() as scoped:
        scoped.setattr(
            asyncio.get_running_loop(),
            "call_soon_threadsafe",
            lambda *_args: pytest.fail("closed coordinator must not schedule cleanup"),
        )
        delegated.set_result(None)

    assert not coordinator.has_voice_leases()


@pytest.mark.asyncio
async def test_thread_future_loop_shutdown_race_is_safely_ignored(monkeypatch, caplog):
    coordinator = _coordinator()
    voice = coordinator.try_acquire_voice("classic.thread_executor")
    delegated = concurrent.futures.Future()
    calls = []
    assert voice is not None
    voice.release_when_done(delegated)

    def loop_closed(*_args):
        calls.append("schedule")
        raise RuntimeError("Event loop is closed")

    with monkeypatch.context() as scoped:
        scoped.setattr(asyncio.get_running_loop(), "call_soon_threadsafe", loop_closed)
        delegated.set_result(None)

    assert calls == ["schedule"]
    assert coordinator.has_voice_leases()
    assert not [record for record in caplog.records if "exception calling callback" in record.message]
    coordinator.close()
