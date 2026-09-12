"""Shared filesystem ownership across independent server processes."""

import asyncio
import gc as python_gc
import hashlib
import multiprocessing
from types import SimpleNamespace

import pytest

from core.lesson.asset_cache import AssetCache
from core.lesson import sd_pack_sync
from core.lesson.cache_key_contract import compose_asset_sd_path
from core.lesson.runtime import LessonRuntime, _sd_pack_activation_for_connection, _sd_pack_gc_for_connection
from core.lesson.sd_pack_gc import (
    LivePackReference, SdPackActivationState, SdPackGarbageCollector,
    device_activation_state_path, shared_protected_cache_keys,
)
from core.lesson.shared_asset_store import PackDeletionRefused, SharedAssetStore


def _key(number):
    return "lesson/v{}-{}".format(number, str(number) * 64)


def _connection(root, device):
    conn = SimpleNamespace(device_id=device, server=SimpleNamespace(lesson_connections={}))
    gc = _sd_pack_gc_for_connection(conn, {"asset_pack_mount_root": str(root / "lesson-assets")})
    assert gc is not None
    gc._disk_usage = lambda _: SimpleNamespace(total=100, free=10)
    state = _sd_pack_activation_for_connection(conn, gc)
    assert state is not None
    return conn, gc, state


def _activate(state, number):
    state.begin_candidate(_key(number))
    assert state.activate_candidate(_key(number))


def _owner(root, device, pipe):
    conn, _gc, state = _connection(root, device)
    cache = AssetCache(
        assets=[], profile="espTft", asset_pack_mount_root=str(root / "lesson-assets"),
        lesson_key="lesson", lesson_version=1, manifest_checksum="1" * 64,
    )
    conn.lesson_runtime = SimpleNamespace(state="RUNNING", asset_cache=cache)
    _activate(state, 1)
    pipe.send("running")
    try:
        while True:
            command = pipe.recv()
            if command == "close":
                asyncio.run(cache.aclose())
                conn.lesson_runtime = None
                pipe.send("closed")
            elif command == "exit":
                break
            else:
                pipe.send((conn.lesson_runtime.state, (root / "lesson-assets" / _key(1)).is_dir()))
    finally:
        asyncio.run(cache.aclose())
        pipe.close()


def _ready_packs(root):
    store = SharedAssetStore(root)
    for number in (1, 2, 3):
        content = str(number).encode()
        digest = hashlib.sha256(content).hexdigest()
        store.put_bytes(content, digest)
        store.commit_pack(_key(number), {"asset": digest})
    return store


def _receive(pipe):
    assert pipe.poll(20), "owner process did not respond"
    return pipe.recv()


@pytest.mark.parametrize("same_device", [False, True])
@pytest.mark.parametrize("release", ["close", "death"])
def test_gc_preserves_other_process_until_existing_ownership_ends(tmp_path, same_device, release):
    store = _ready_packs(tmp_path)
    ctx = multiprocessing.get_context("spawn")
    parent, child = ctx.Pipe()
    owner = ctx.Process(target=_owner, args=(tmp_path, "device-a", child))
    owner.start()
    child.close()
    try:
        assert _receive(parent) == "running"
        _conn, gc, state = _connection(tmp_path, "device-a" if same_device else "device-b")
        _activate(state, 2)
        _activate(state, 3)
        assert owner.is_alive()
        assert gc.collect_one() == {"skipped": "no_evictable_pack"}
        parent.send("status")
        assert _receive(parent) == ("RUNNING", True)
        assert store.asset_path(hashlib.sha256(b"1").hexdigest()).is_file()

        if release == "close":
            parent.send("close")
            assert _receive(parent) == "closed"
        else:
            owner.kill()
            owner.join(10)
            assert not owner.is_alive()

        # Reconstruct state/GC to exercise server restart, not cached Python objects.
        _restarted, restarted_gc, restored = _connection(tmp_path, "device-a")
        assert restored.current_cache_key == _key(3 if same_device else 1)
        if same_device:
            assert restarted_gc.collect_one()["deleted"] == _key(1)
            assert not store.asset_path(hashlib.sha256(b"1").hexdigest()).exists()
        else:
            assert restarted_gc.collect_one() == {"skipped": "no_evictable_pack"}
            assert store.is_pack_ready(_key(1))
    finally:
        if owner.is_alive():
            parent.send("exit")
            owner.join(10)
        if owner.is_alive():
            owner.kill()
            owner.join(10)
        parent.close()


@pytest.mark.parametrize("device", [None, "", "   ", 123])
def test_activation_refuses_unknown_device_identity(tmp_path, device):
    store = SharedAssetStore(tmp_path)
    conn = SimpleNamespace(device_id=device)
    assert _sd_pack_activation_for_connection(conn, SimpleNamespace(shared_store=store)) is None
    assert not (tmp_path / "lesson-pack-activations").exists()


@pytest.mark.parametrize("record", ["legacy", "device", "live"])
def test_malformed_shared_protection_fails_closed(tmp_path, record):
    store = _ready_packs(tmp_path)
    reference = None
    if record == "legacy":
        path = tmp_path / "lesson-pack-activation.json"
    elif record == "device":
        path = device_activation_state_path(store, "device-a")
    else:
        reference = LivePackReference(store, _key(1))
        path = reference.path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"current":')
    try:
        gc = SdPackGarbageCollector(store.pack_root, shared_store=store, quota_bytes=1)
        assert gc.collect_one() == {"skipped": "protection_unavailable"}
        with pytest.raises(PackDeletionRefused):
            store.delete_pack(_key(1), sweep=True)
        assert store.is_pack_ready(_key(1))
        assert _sd_pack_activation_for_connection(
            SimpleNamespace(device_id="device-b"), gc,
        ) is None
    finally:
        if reference is not None:
            reference.close()


@pytest.mark.parametrize("content", ['{}', '{"current": null}', '{"current": ""}'])
def test_live_reference_cannot_silently_lose_its_exact_identity(tmp_path, content):
    store = _ready_packs(tmp_path)
    reference = LivePackReference(store, _key(1))
    reference.path.write_text(content)
    try:
        gc = SdPackGarbageCollector(store.pack_root, shared_store=store, quota_bytes=1)
        assert gc.collect_one() == {"skipped": "protection_unavailable"}
        with pytest.raises(PackDeletionRefused):
            store.delete_pack(_key(1))
    finally:
        reference.close()


def test_legacy_record_remains_protected_during_device_rotation(tmp_path):
    store = _ready_packs(tmp_path)
    legacy = SdPackActivationState(store)
    _activate(legacy, 1)
    _conn, gc, state = _connection(tmp_path, "device-a")
    _activate(state, 2)
    _activate(state, 3)
    assert gc.collect_one() == {"skipped": "no_evictable_pack"}
    assert SdPackActivationState(store).current_cache_key == _key(1)


def test_cache_close_releases_reference_on_cancellation_and_repeated_close(tmp_path):
    store = _ready_packs(tmp_path)
    cache = AssetCache(assets=[], profile="espTft", shared_asset_store=store,
                       lesson_key="lesson", lesson_version=1, manifest_checksum="1" * 64)

    async def cancelled(**_kwargs):
        raise asyncio.CancelledError()

    cache._maybe_close_client = cancelled
    assert _key(1) in shared_protected_cache_keys(store)
    for _ in range(2):
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(cache.aclose())
        assert _key(1) not in shared_protected_cache_keys(store)


def test_abandoned_cache_finalization_releases_kernel_ownership(tmp_path):
    store = _ready_packs(tmp_path)
    cache = AssetCache(assets=[], profile="espTft", shared_asset_store=store,
                       lesson_key="lesson", lesson_version=1, manifest_checksum="1" * 64)
    assert _key(1) in shared_protected_cache_keys(store)
    del cache
    python_gc.collect()
    assert _key(1) not in shared_protected_cache_keys(store)
    assert not list((tmp_path / "lesson-pack-references").iterdir())


@pytest.mark.parametrize("cancel", [False, True])
def test_runtime_close_releases_cache_when_forwarder_teardown_fails(tmp_path, cancel):
    store = _ready_packs(tmp_path)
    cache = AssetCache(assets=[], profile="espTft", shared_asset_store=store,
                       lesson_key="lesson", lesson_version=1, manifest_checksum="1" * 64)

    async def exercise():
        entered = asyncio.Event()

        async def forwarder_close():
            entered.set()
            if cancel:
                await asyncio.Future()
            raise RuntimeError("forwarder close failed")

        conn = SimpleNamespace(device_id="device-a", config={})
        runtime = LessonRuntime(conn, assignment={"assignmentId": "assignment-a"}, manifest={},
                                asset_cache=cache, forwarder=SimpleNamespace(aclose=forwarder_close))
        conn.lesson_runtime = runtime
        task = asyncio.create_task(runtime.close())
        await asyncio.wait_for(entered.wait(), 5)
        if cancel:
            task.cancel()
        with pytest.raises(asyncio.CancelledError if cancel else RuntimeError):
            await task
        assert runtime._closed
        assert conn.lesson_runtime is runtime
        assert _key(1) not in shared_protected_cache_keys(store)

    try:
        asyncio.run(exercise())
    finally:
        asyncio.run(cache.aclose())


def _sync_fixture(tmp_path):
    store = SharedAssetStore(tmp_path)
    content = b"poster"
    digest = hashlib.sha256(content).hexdigest()
    store.put_bytes(content, digest)
    manifest = {
        "cacheKey": _key(1), "lessonId": "lesson", "lessonVersion": 1,
        "manifestChecksum": "1" * 64, "assignmentVersion": 1, "ready": True,
        "assets": [{"key": "poster", "sha256": digest, "size": len(content),
                    "mediaType": "image/jpeg", "critical": True,
                    "onlineUrl": "https://assets.example/poster.jpg",
                    "sdPath": compose_asset_sd_path(_key(1), "poster")}],
    }
    store.commit_pack(_key(1), {"poster": digest}, manifest=manifest)
    conn = SimpleNamespace(config={"lesson": {"asset_pack_mount_root": str(store.pack_root)}})
    pack = list(sd_pack_sync.cached_asset_packs(conn.config))[0]
    return store, conn, pack


@pytest.mark.parametrize("cancel_waiter", [False, True])
def test_dispatched_background_sync_owns_source_until_transport_completion(tmp_path, monkeypatch, cancel_waiter):
    from core.api import device_mcp_admin_handler

    store, conn, pack = _sync_fixture(tmp_path)

    async def exercise():
        entered, finish = asyncio.Event(), asyncio.Event()

        async def transport(*_args, **_kwargs):
            entered.set()
            await finish.wait()
            return {"ready": True}

        monkeypatch.setattr(device_mcp_admin_handler, "_call_raw_mcp_tool", transport)
        task = asyncio.create_task(sd_pack_sync._call_sd_pack_sync_with_voice_guard(
            conn, object(), pack, busy_check=lambda: False, sleep=asyncio.sleep, poll_interval=0.01,
        ))
        await asyncio.wait_for(entered.wait(), 5)
        if cancel_waiter:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        try:
            ctx = multiprocessing.get_context("spawn")
            parent, child = ctx.Pipe()
            collector = ctx.Process(target=_race_deleter, args=(tmp_path, child, False))
            collector.start()
            child.close()
            try:
                assert _receive(parent) == "attempting"
                assert _receive(parent) == "refused"
                collector.join(10)
                assert collector.exitcode == 0
            finally:
                if collector.is_alive():
                    collector.kill()
                    collector.join(10)
                parent.close()
            assert store.is_pack_ready(_key(1))
        finally:
            finish.set()
            await conn._sd_pack_sync_coordinator._worker
            if not cancel_waiter:
                await task
        store.delete_pack(_key(1), sweep=True)
        assert not store.is_pack_ready(_key(1))

    asyncio.run(exercise())


@pytest.mark.parametrize("busy", [False, True])
def test_unknown_sync_completion_keeps_source_until_confirmed_recovery(tmp_path, monkeypatch, busy):
    from core.api import device_mcp_admin_handler

    store, conn, pack = _sync_fixture(tmp_path)

    async def exercise():
        now = [0.0]
        conn._sd_pack_sync_coordinator = sd_pack_sync.SdPackSyncCoordinator(clock=lambda: now[0])

        async def timeout(*_args, **_kwargs):
            if busy:
                return {"ready": False, "errorCode": "storage_busy"}
            raise TimeoutError("firmware response lost")

        monkeypatch.setattr(device_mcp_admin_handler, "_call_raw_mcp_tool", timeout)
        if busy:
            await sd_pack_sync._call_sd_pack_sync_with_voice_guard(
                conn, object(), pack, busy_check=lambda: False, sleep=asyncio.sleep, poll_interval=0.01,
            )
        else:
            with pytest.raises(TimeoutError):
                await sd_pack_sync._call_sd_pack_sync_with_voice_guard(
                    conn, object(), pack, busy_check=lambda: False, sleep=asyncio.sleep, poll_interval=0.01,
                )
        with pytest.raises(PackDeletionRefused):
            store.delete_pack(_key(1))
        now[0] = 31.0

        async def recovered(*_args, **_kwargs):
            return {"ready": True}

        monkeypatch.setattr(device_mcp_admin_handler, "_call_raw_mcp_tool", recovered)
        await sd_pack_sync._call_sd_pack_sync_with_voice_guard(
            conn, object(), pack, busy_check=lambda: False, sleep=asyncio.sleep, poll_interval=0.01,
        )
        store.delete_pack(_key(1))
        assert not store.is_pack_ready(_key(1))

    asyncio.run(exercise())


def test_background_sync_rechecks_source_after_queued_pack_was_deleted(tmp_path, monkeypatch):
    from core.api import device_mcp_admin_handler

    store, conn, pack = _sync_fixture(tmp_path)
    store.delete_pack(_key(1), sweep=True)

    async def forbidden_transport(*_args, **_kwargs):
        pytest.fail("deleted source must not be sent to firmware")

    monkeypatch.setattr(device_mcp_admin_handler, "_call_raw_mcp_tool", forbidden_transport)
    with pytest.raises(ValueError, match="not READY"):
        asyncio.run(sd_pack_sync._call_sd_pack_sync_with_voice_guard(
            conn, object(), pack, busy_check=lambda: False, sleep=asyncio.sleep, poll_interval=0.01,
        ))
    assert not shared_protected_cache_keys(store)


@pytest.mark.parametrize("same_root", [False, True])
def test_direct_cache_eviction_preserves_other_live_owner(tmp_path, same_root):
    store = _ready_packs(tmp_path)
    caches = [AssetCache(assets=[], profile="espTft", shared_asset_store=store,
                         cache_root=str(store.pack_root if same_root else tmp_path / "cache"),
                         asset_pack_mount_root=str(store.pack_root), lesson_key="lesson",
                         lesson_version=1, manifest_checksum="1" * 64) for _ in range(2)]
    try:
        with pytest.raises(PackDeletionRefused):
            asyncio.run(caches[0].evict())
        assert store.is_pack_ready(_key(1))
        asyncio.run(caches[1].aclose())
        asyncio.run(caches[0].evict())
        assert not store.is_pack_ready(_key(1))
    finally:
        for cache in caches:
            asyncio.run(cache.aclose())


def _race_publisher(root, pipe, pause, activation):
    store = SharedAssetStore(root, cleanup_on_init=False)
    state = None
    if activation:
        state = SdPackActivationState(store, state_path=device_activation_state_path(store, "device-a"))
    original_sync = store._fsync_dir

    def pause_publication(path):
        original_sync(path)
        pipe.send("published")
        assert pipe.recv() == "release"

    if pause:
        store._fsync_dir = pause_publication
    pipe.send("attempting")
    reference = None
    try:
        if state is not None:
            state.begin_candidate(_key(1))
        else:
            reference = LivePackReference(store, _key(1))
        pipe.send(("registered", store.is_pack_ready(_key(1))))
        assert pipe.recv() == "close"
    finally:
        if reference is not None:
            reference.close()
        pipe.close()


def _race_deleter(root, pipe, pause):
    store = SharedAssetStore(root, cleanup_on_init=False)

    def probe():
        pipe.send("locked")
        assert pipe.recv() == "release"
        return set()

    pipe.send("attempting")
    try:
        store.delete_pack(_key(1), sweep=True, protection_probe=probe if pause else None)
    except PackDeletionRefused:
        pipe.send("refused")
    else:
        pipe.send("deleted")
    finally:
        pipe.close()


def _publish_during_cleanup(root, pipe, activation):
    import core.lesson.sd_pack_gc as gc_module

    store = SharedAssetStore(root, cleanup_on_init=False)
    state = SdPackActivationState(store) if activation else None
    original_replace = gc_module.os.replace

    def pause_replace(source, target):
        pipe.send("part_written")
        assert pipe.recv() == "publish"
        original_replace(source, target)

    gc_module.os.replace = pause_replace
    reference = None
    try:
        if activation:
            state.begin_candidate(_key(1))
        else:
            reference = LivePackReference(store, _key(1))
        pipe.send("published")
        assert pipe.recv() == "close"
    finally:
        if reference is not None:
            reference.close()
        pipe.close()


@pytest.mark.parametrize("activation", [False, True])
def test_server_startup_cleanup_cannot_remove_another_process_publication(tmp_path, activation):
    store = _ready_packs(tmp_path)
    ctx = multiprocessing.get_context("spawn")
    parent, child = ctx.Pipe()
    publisher = ctx.Process(target=_publish_during_cleanup, args=(tmp_path, child, activation))
    publisher.start()
    child.close()
    try:
        assert _receive(parent) == "part_written"
        assert list(tmp_path.rglob("*.part"))
        assert store.cleanup_parts() == 0
        parent.send("publish")
        assert _receive(parent) == "published"
        assert _key(1) in shared_protected_cache_keys(store)
        parent.send("close")
        publisher.join(10)
        assert publisher.exitcode == 0
    finally:
        if publisher.is_alive():
            publisher.kill()
            publisher.join(10)
        parent.close()


@pytest.mark.parametrize("winner", ["reference", "activation", "deletion"])
def test_reference_publication_and_deletion_are_serialized_across_processes(tmp_path, winner):
    _ready_packs(tmp_path)
    ctx = multiprocessing.get_context("spawn")
    publish_parent, publish_child = ctx.Pipe()
    delete_parent, delete_child = ctx.Pipe()
    publisher = ctx.Process(target=_race_publisher,
                            args=(tmp_path, publish_child, winner != "deletion", winner == "activation"))
    deleter = ctx.Process(target=_race_deleter, args=(tmp_path, delete_child, winner == "deletion"))
    started = []
    try:
        if winner == "deletion":
            deleter.start()
            started.append(deleter)
            assert _receive(delete_parent) == "attempting"
            assert _receive(delete_parent) == "locked"
            publisher.start()
            started.append(publisher)
            assert _receive(publish_parent) == "attempting"
            assert not publish_parent.poll(0.2)
            delete_parent.send("release")
            assert _receive(delete_parent) == "deleted"
            assert _receive(publish_parent) == ("registered", False)
        else:
            publisher.start()
            started.append(publisher)
            assert _receive(publish_parent) == "attempting"
            assert _receive(publish_parent) == "published"
            deleter.start()
            started.append(deleter)
            assert _receive(delete_parent) == "attempting"
            assert not delete_parent.poll(0.2)
            publish_parent.send("release")
            assert _receive(publish_parent) == ("registered", True)
            assert _receive(delete_parent) == "refused"
        publish_parent.send("close")
        for process in started:
            process.join(10)
            assert process.exitcode == 0
    finally:
        for process in started:
            if process.is_alive():
                process.kill()
                process.join(10)
        for pipe in (publish_parent, publish_child, delete_parent, delete_child):
            pipe.close()
