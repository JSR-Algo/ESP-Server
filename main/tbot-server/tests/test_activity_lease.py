import asyncio
import concurrent.futures
import threading

import pytest

from core.activity_lease import (
    ActivityLeaseCoordinator,
    ActivityLeaseInvariantError,
    ActivityOperation,
    ExclusiveDisposition,
    LeaseKind,
)


VOICE_OP = "classic.start_to_chat"
ALT_VOICE_OP = "google.open"
EVICT_OP = "lesson_cache.evict"


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
    second = coordinator.try_acquire_voice("google.send_text")

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
        asyncio.create_task(worker(VOICE_OP)),
        asyncio.create_task(worker(ALT_VOICE_OP)),
    ]
    await asyncio.wait_for(acquired.wait(), timeout=1)

    assert coordinator.has_voice_leases()
    assert owners[0] is not owners[1]
    assert coordinator.try_acquire_eviction(EVICT_OP, busy_probe=lambda: False) is None

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
    assert coordinator.try_acquire_eviction(EVICT_OP, busy_probe=lambda: False) is None

    exclusive.complete_exclusive(ExclusiveDisposition.DEFINITIVE)
    assert not coordinator.has_exclusive_lease()


@pytest.mark.asyncio
async def test_voice_owner_cannot_acquire_exclusive():
    coordinator = _coordinator()
    voice = coordinator.try_acquire_voice(VOICE_OP)

    assert voice is not None
    assert coordinator.try_acquire_eviction(EVICT_OP, busy_probe=lambda: False) is None
    voice.release()


@pytest.mark.asyncio
async def test_busy_probe_true_or_exception_refuses_without_state_change():
    coordinator = _coordinator()

    assert coordinator.try_acquire_eviction(EVICT_OP, busy_probe=lambda: True) is None

    def broken_probe():
        raise RuntimeError("private voice state")

    assert coordinator.try_acquire_eviction(EVICT_OP, busy_probe=broken_probe) is None
    assert not coordinator.has_exclusive_lease()
    assert not coordinator.has_voice_leases()


@pytest.mark.asyncio
@pytest.mark.parametrize("probe_kind", ["voice", "exclusive"])
async def test_reentrant_busy_probe_cannot_install_voice_and_exclusive_together(probe_kind):
    coordinator = _coordinator()
    acquired = []

    def reentrant_probe():
        if probe_kind == "voice":
            acquired.append(coordinator.try_acquire_voice(VOICE_OP))
        else:
            acquired.append(
                coordinator.try_acquire_eviction(
                    EVICT_OP,
                    busy_probe=lambda: False,
                )
            )
        return False

    outer = coordinator.try_acquire_eviction(EVICT_OP, busy_probe=reentrant_probe)

    assert outer is None
    assert acquired[0] is not None
    assert not (coordinator.has_voice_leases() and coordinator.has_exclusive_lease())
    if probe_kind == "voice":
        acquired[0].release()
    else:
        acquired[0].complete_exclusive(ExclusiveDisposition.DEFINITIVE)


@pytest.mark.asyncio
async def test_busy_probe_closing_coordinator_cannot_install_exclusive():
    coordinator = _coordinator()

    def closing_probe():
        coordinator.close()
        return False

    assert coordinator.try_acquire_eviction(EVICT_OP, busy_probe=closing_probe) is None
    assert not coordinator.has_exclusive_lease()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation",
    [
        "",
        "UPPER",
        " leading",
        "trailing ",
        "child transcript",
        "secret",
        "childsecret",
        "parent@example.com",
        "voice/start",
        "voice\nstart",
        "a" * 65,
        None,
    ],
)
async def test_invalid_operation_tokens_fail_closed_without_snapshot_leak(operation):
    coordinator = _coordinator()
    probe_calls = []

    assert coordinator.try_acquire_voice(operation) is None
    assert (
        coordinator.try_acquire_eviction(
            operation,
            busy_probe=lambda: probe_calls.append(True) or False,
        )
        is None
    )
    snapshot = coordinator.diagnostic_snapshot()
    assert snapshot["voiceOwners"] == []
    assert snapshot["exclusive"] is None
    assert probe_calls == []
    if isinstance(operation, str) and operation:
        assert operation not in str(snapshot)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", [item.value for item in ActivityOperation])
async def test_operation_allowlist_accepts_every_planned_identifier(operation):
    coordinator = _coordinator()

    voice = coordinator.try_acquire_voice(operation)

    assert voice is not None
    assert coordinator.diagnostic_snapshot()["voiceOwners"][0]["operation"] == operation
    voice.release()


@pytest.mark.asyncio
async def test_nested_voice_uses_one_authoritative_owner_record_with_current_depth():
    coordinator = _coordinator()
    first = coordinator.try_acquire_voice(VOICE_OP)
    middle = coordinator.try_acquire_voice("google.open")
    last = coordinator.try_acquire_voice("google.send_text")
    assert first is not None and middle is not None and last is not None

    snapshot = coordinator.diagnostic_snapshot()
    assert snapshot["voiceLeaseCount"] == 3
    assert len(snapshot["voiceOwners"]) == 1
    assert snapshot["voiceOwners"][0]["depth"] == 3
    assert snapshot["voiceOwners"][0]["operation"] == VOICE_OP

    middle.release()
    snapshot = coordinator.diagnostic_snapshot()
    assert snapshot["voiceLeaseCount"] == 2
    assert len(snapshot["voiceOwners"]) == 1
    assert snapshot["voiceOwners"][0]["depth"] == 2

    last.release()
    assert coordinator.diagnostic_snapshot()["voiceOwners"][0]["depth"] == 1
    first.release()
    assert coordinator.diagnostic_snapshot()["voiceOwners"] == []


@pytest.mark.asyncio
async def test_wrong_task_release_raises_without_mutating_state():
    coordinator = _coordinator()
    lease = coordinator.try_acquire_voice(VOICE_OP)
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
    voice = coordinator.try_acquire_voice(VOICE_OP)
    assert voice is not None
    with pytest.raises(ActivityLeaseInvariantError, match="wrong-kind"):
        voice.complete_exclusive(ExclusiveDisposition.DEFINITIVE)
    assert coordinator.has_voice_leases()
    voice.release()
    with pytest.raises(ActivityLeaseInvariantError, match="duplicate"):
        voice.release()

    exclusive = coordinator.try_acquire_eviction(EVICT_OP, busy_probe=lambda: False)
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
    lease = coordinator.try_acquire_voice(VOICE_OP)
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
    lease = coordinator.try_acquire_voice(VOICE_OP)
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
        lease = coordinator.try_acquire_voice(VOICE_OP)
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
    lease = coordinator.try_acquire_voice(VOICE_OP)
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
    lease = coordinator.try_acquire_voice(VOICE_OP)
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
    voice = coordinator.try_acquire_voice(VOICE_OP)
    first = asyncio.get_running_loop().create_future()
    second = asyncio.get_running_loop().create_future()
    assert voice is not None
    voice.release_when_done(first)
    with pytest.raises(ActivityLeaseInvariantError, match="delegated"):
        voice.release_when_done(second)
    first.cancel()
    await asyncio.sleep(0)

    exclusive = coordinator.try_acquire_eviction(EVICT_OP, busy_probe=lambda: False)
    assert exclusive is not None
    with pytest.raises(ActivityLeaseInvariantError, match="wrong-kind"):
        exclusive.release_when_done(second)
    exclusive.complete_exclusive(ExclusiveDisposition.DEFINITIVE)


@pytest.mark.asyncio
async def test_exclusive_ambiguous_is_sticky_until_close():
    coordinator = _coordinator()
    exclusive = coordinator.try_acquire_eviction(EVICT_OP, busy_probe=lambda: False)
    assert exclusive is not None

    exclusive.complete_exclusive(ExclusiveDisposition.AMBIGUOUS)

    assert coordinator.has_exclusive_lease()
    snapshot = coordinator.diagnostic_snapshot()
    assert snapshot["exclusive"] == {
        "kind": "eviction-exclusive",
        "operation": EVICT_OP,
        "sticky": True,
        "leaseIdSuffix": format(exclusive.lease_id, "x")[-6:],
        "ownerTaskIdSuffix": format(id(exclusive.owner_task), "x")[-6:],
    }
    assert exclusive.lease_id in coordinator._handles
    assert coordinator.try_acquire_voice(VOICE_OP) is None
    assert coordinator.try_acquire_eviction(EVICT_OP, busy_probe=lambda: False) is None
    with pytest.raises(ActivityLeaseInvariantError, match="duplicate"):
        exclusive.complete_exclusive(ExclusiveDisposition.DEFINITIVE)

    coordinator.close()
    assert not coordinator.has_exclusive_lease()
    assert coordinator.diagnostic_snapshot() == {
        "closed": True,
        "voiceLeaseCount": 0,
        "voiceOwners": [],
        "exclusive": None,
    }


@pytest.mark.asyncio
async def test_close_request_retains_nested_voice_and_refuses_new_acquisition():
    coordinator = _coordinator()
    first = coordinator.try_acquire_voice(VOICE_OP)
    second = coordinator.try_acquire_voice(ALT_VOICE_OP)
    assert first is not None and second is not None

    coordinator.close()

    assert coordinator.has_voice_leases()
    assert not coordinator.has_exclusive_lease()
    assert coordinator.diagnostic_snapshot()["closed"] is False
    assert coordinator.diagnostic_snapshot()["voiceLeaseCount"] == 2
    assert coordinator.try_acquire_voice(VOICE_OP) is None
    assert coordinator.try_acquire_eviction(EVICT_OP, busy_probe=lambda: False) is None

    second.release()
    assert coordinator.diagnostic_snapshot()["closed"] is False
    first.release()
    assert coordinator.diagnostic_snapshot() == {
        "closed": True,
        "voiceLeaseCount": 0,
        "voiceOwners": [],
        "exclusive": None,
    }


@pytest.mark.asyncio
async def test_close_request_finalizes_only_after_out_of_order_voice_releases_reach_zero():
    coordinator = _coordinator()
    first = coordinator.try_acquire_voice(VOICE_OP)
    middle = coordinator.try_acquire_voice(ALT_VOICE_OP)
    last = coordinator.try_acquire_voice("google.send_text")
    assert first is not None and middle is not None and last is not None

    coordinator.close()
    middle.release()
    first.release()

    assert coordinator.has_voice_leases()
    assert coordinator.diagnostic_snapshot()["closed"] is False

    last.release()

    assert not coordinator.has_voice_leases()
    assert coordinator.diagnostic_snapshot()["closed"] is True


@pytest.mark.asyncio
async def test_future_from_another_loop_is_rejected_without_delegating():
    coordinator = _coordinator()
    voice = coordinator.try_acquire_voice(VOICE_OP)
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
async def test_callback_registration_invariant_drops_raw_exception_cause():
    coordinator = _coordinator()
    voice = coordinator.try_acquire_voice(VOICE_OP)
    assert voice is not None

    class BrokenFuture(asyncio.Future):
        def add_done_callback(self, _callback, *, context=None):
            raise RuntimeError("private callback payload")

    with pytest.raises(ActivityLeaseInvariantError, match="future-callback") as captured:
        voice.release_when_done(BrokenFuture())

    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert "private callback payload" not in str(captured.value)
    assert coordinator.has_voice_leases()
    voice.release()


@pytest.mark.asyncio
async def test_asyncio_future_completion_after_close_request_finalizes_coordinator():
    coordinator = _coordinator()
    voice = coordinator.try_acquire_voice(VOICE_OP)
    delegated = asyncio.get_running_loop().create_future()
    assert voice is not None
    voice.release_when_done(delegated)

    coordinator.close()
    assert coordinator.has_voice_leases()
    assert coordinator.diagnostic_snapshot()["closed"] is False

    delegated.set_result(None)
    await asyncio.sleep(0)

    assert not coordinator.has_voice_leases()
    assert coordinator.diagnostic_snapshot()["closed"] is True


@pytest.mark.asyncio
async def test_thread_future_completion_after_close_request_finalizes_on_owner_loop():
    coordinator = _coordinator()
    voice = coordinator.try_acquire_voice(VOICE_OP)
    delegated = concurrent.futures.Future()
    assert voice is not None
    voice.release_when_done(delegated)
    coordinator.close()

    worker = threading.Thread(target=lambda: delegated.set_result(None))
    worker.start()
    worker.join(timeout=1)
    for _ in range(3):
        await asyncio.sleep(0)

    assert not coordinator.has_voice_leases()
    assert coordinator.diagnostic_snapshot()["closed"] is True


@pytest.mark.asyncio
async def test_thread_future_loop_shutdown_race_is_safely_ignored(monkeypatch, caplog):
    coordinator = _coordinator()
    voice = coordinator.try_acquire_voice(VOICE_OP)
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
