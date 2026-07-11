import hashlib
import json
import multiprocessing
import os
import errno
from pathlib import Path

import pytest

from core.lesson.shared_asset_store import SharedAssetStore


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hold_atomic_write(root, started, release):
    def hold(stage, _path):
        if stage == "after_temp_create":
            started.set()
            release.wait(10)

    data = b"cross-process-active"
    SharedAssetStore(root, failure_hook=hold).put_bytes(data, _sha(data))


def _commit_generation(root, cache_key, key, content, start):
    store = SharedAssetStore(root)
    digest = _sha(content)
    store.put_bytes(content, digest)
    start.wait(10)
    store.commit_pack(cache_key, {key: digest})


def _crash_after_backup(root, cache_key, digest):
    def crash(stage, _path):
        if stage == "after_backup":
            os._exit(71)

    SharedAssetStore(root, failure_hook=crash).commit_pack(cache_key, {"asset": digest})


def _swap_pack_while_paused(root, cache_key, digest, swapped, release):
    def pause(stage, _path):
        if stage == "after_backup":
            swapped.set()
            release.wait(10)

    SharedAssetStore(root, failure_hook=pause).commit_pack(cache_key, {"asset": digest})


def _read_pack_ready(root, cache_key, result):
    result.put(SharedAssetStore(root).is_pack_ready(cache_key))


def test_equal_sha_is_stored_once_and_reused_across_lesson_packs(tmp_path):
    store = SharedAssetStore(tmp_path / "tbot")
    content = b"shared lesson asset"
    digest = _sha(content)

    first = store.put_bytes(content, digest)
    second = store.put_bytes(content, digest)
    pack_a = store.commit_pack("lesson-a/v1-one", {"poster": digest})
    pack_b = store.commit_pack("lesson-b/v2-two", {"poster": digest})

    assert first == second == tmp_path / "tbot/shared-assets/sha256" / digest[:2] / digest
    assert first.read_bytes() == content
    assert os.stat(pack_a / "poster").st_ino == os.stat(first).st_ino
    assert os.stat(pack_b / "poster").st_ino == os.stat(first).st_ino
    assert store.is_pack_ready("lesson-a/v1-one")
    assert store.is_pack_ready("lesson-b/v2-two")


def test_interrupted_atomic_write_leaves_no_visible_asset_and_cleanup_is_safe(tmp_path):
    def fail_before_replace(stage, _path):
        if stage == "before_replace":
            raise RuntimeError("power loss")

    store = SharedAssetStore(tmp_path / "tbot", failure_hook=fail_before_replace)
    content = b"partial"
    digest = _sha(content)

    with pytest.raises(RuntimeError, match="power loss"):
        store.put_bytes(content, digest)

    assert not store.asset_path(digest).exists()
    assert list((tmp_path / "tbot").rglob("*.part"))
    assert store.cleanup_parts() == 1
    assert store.cleanup_parts() == 0


def test_restart_automatically_cleans_interrupted_parts(tmp_path):
    root = tmp_path / "tbot"

    def interrupt(stage, _path):
        if stage == "before_replace":
            raise RuntimeError("power loss")

    store = SharedAssetStore(root, failure_hook=interrupt)
    data = b"partial"
    with pytest.raises(RuntimeError):
        store.put_bytes(data, _sha(data))
    assert list(root.rglob("*.part"))

    SharedAssetStore(root)

    assert not list(root.rglob("*.part"))


def test_second_store_cleanup_does_not_delete_an_active_atomic_temp(tmp_path):
    root = tmp_path / "tbot"
    observed = []

    def inspect_during_commit(stage, _path):
        if stage != "after_temp_create":
            return
        active = list(root.rglob("*.part"))
        assert len(active) == 1
        SharedAssetStore(root)
        observed.append(active[0].exists())

    store = SharedAssetStore(root, failure_hook=inspect_during_commit)
    data = b"active write"
    target = store.put_bytes(data, _sha(data))

    assert observed == [True]
    assert target.read_bytes() == data
    assert not list(root.rglob("*.part"))


def test_cross_process_cleanup_preserves_active_temp_then_cleans_orphan(tmp_path):
    root = tmp_path / "tbot"
    context = multiprocessing.get_context("spawn")
    started = context.Event()
    release = context.Event()
    writer = context.Process(target=_hold_atomic_write, args=(str(root), started, release))
    writer.start()
    assert started.wait(10)
    active = list(root.rglob("*.part"))
    assert len(active) == 1

    SharedAssetStore(root)
    assert active[0].exists()

    writer.terminate()
    writer.join(10)
    assert writer.exitcode is not None
    SharedAssetStore(root)
    assert not list(root.rglob("*.part"))


def test_checksum_mismatch_never_commits_asset(tmp_path):
    store = SharedAssetStore(tmp_path / "tbot")
    expected = _sha(b"expected")

    with pytest.raises(ValueError, match="checksum mismatch"):
        store.put_bytes(b"corrupt", expected)

    assert not store.asset_path(expected).exists()
    assert not list((tmp_path / "tbot").rglob("*.part"))


def test_missing_or_corrupt_ready_marker_makes_pack_nonready(tmp_path):
    store = SharedAssetStore(tmp_path / "tbot")
    content = b"asset"
    digest = _sha(content)
    store.put_bytes(content, digest)
    pack = store.commit_pack("lesson/v1-sum", {"asset": digest})

    (pack / "READY").unlink()
    assert not store.is_pack_ready("lesson/v1-sum")
    (pack / "READY").write_text("not-json", encoding="utf-8")
    assert not store.is_pack_ready("lesson/v1-sum")


def test_restart_reattests_cas_and_rejects_corrupt_bytes(tmp_path):
    root = tmp_path / "tbot"
    content = b"asset"
    digest = _sha(content)
    store = SharedAssetStore(root)
    asset = store.put_bytes(content, digest)
    store.commit_pack("lesson/v1-sum", {"asset": digest})
    assert store.is_pack_ready("lesson/v1-sum")

    asset.write_bytes(b"tampered")
    restarted = SharedAssetStore(root)

    assert not restarted.is_pack_ready("lesson/v1-sum")
    assert not restarted.attest(digest)


def test_pack_json_then_ready_are_committed_last_atomically(tmp_path):
    events = []

    def record(stage, path):
        events.append((stage, Path(path).name))

    store = SharedAssetStore(tmp_path / "tbot", failure_hook=record)
    data = b"asset"
    digest = _sha(data)
    store.put_bytes(data, digest)
    pack = store.commit_pack("lesson/v1-sum", {"asset": digest})

    replaced = [name for stage, name in events if stage == "after_replace"]
    assert replaced[-2:] == ["pack.json", "READY"]
    manifest = json.loads((pack / "pack.json").read_text(encoding="utf-8"))
    assert manifest["assets"] == {"asset": digest}


def test_interrupted_pack_materialization_never_creates_ready(tmp_path):
    root = tmp_path / "tbot"
    data = b"asset"
    digest = _sha(data)
    armed = {"value": False}

    def interrupt(stage, path):
        if armed["value"] and stage == "before_replace" and path.name == "asset":
            raise RuntimeError("power loss")

    store = SharedAssetStore(root, failure_hook=interrupt)
    store.put_bytes(data, digest)
    armed["value"] = True

    with pytest.raises(RuntimeError):
        store.commit_pack("lesson/v1-sum", {"asset": digest})

    assert not store.is_pack_ready("lesson/v1-sum")
    assert not (store.pack_root / "lesson/v1-sum/READY").exists()
    restarted = SharedAssetStore(root)
    assert not list(root.rglob("*.part"))
    assert restarted.is_pack_ready("lesson/v1-sum") is False


def test_interruption_between_pack_json_and_ready_recovers_on_restart(tmp_path):
    root = tmp_path / "tbot"
    data = b"asset"
    digest = _sha(data)

    def interrupt(stage, path):
        if stage == "before_replace" and path.name == "READY":
            raise RuntimeError("power loss")

    store = SharedAssetStore(root, failure_hook=interrupt)
    store.put_bytes(data, digest)
    with pytest.raises(RuntimeError):
        store.commit_pack("lesson/v1-sum", {"asset": digest})

    pack = store.pack_root / "lesson/v1-sum"
    assert not (pack / "pack.json").exists()
    assert not (pack / "READY").exists()
    restarted = SharedAssetStore(root)
    assert not list(root.rglob("*.part"))
    assert not restarted.is_pack_ready("lesson/v1-sum")
    restarted.commit_pack("lesson/v1-sum", {"asset": digest})
    assert restarted.is_pack_ready("lesson/v1-sum")


def test_hardlink_unsupported_falls_back_to_atomic_copy(monkeypatch, tmp_path):
    store = SharedAssetStore(tmp_path / "tbot")
    data = b"copy fallback"
    digest = _sha(data)
    shared = store.put_bytes(data, digest)

    def no_hardlinks(_source, _target):
        raise OSError(errno.EXDEV, "cross-device link")

    monkeypatch.setattr(os, "link", no_hardlinks)
    pack = store.commit_pack("lesson/v1-copy", {"asset": digest})

    assert store.is_pack_ready("lesson/v1-copy")
    assert (pack / "asset").read_bytes() == data
    assert os.stat(pack / "asset").st_ino != os.stat(shared).st_ino


def test_failed_pack_refresh_preserves_previous_valid_ready(tmp_path):
    root = tmp_path / "tbot"
    old = b"old"
    new = b"new"
    old_digest = _sha(old)
    new_digest = _sha(new)
    store = SharedAssetStore(root)
    store.put_bytes(old, old_digest)
    store.commit_pack("lesson/v1-refresh", {"asset": old_digest})

    def fail_staging(stage, path):
        if stage == "before_replace" and path.name == "asset":
            raise RuntimeError("staging failed")

    refreshing = SharedAssetStore(root, failure_hook=fail_staging)
    refreshing.put_bytes(new, new_digest)
    with pytest.raises(RuntimeError, match="staging failed"):
        refreshing.commit_pack("lesson/v1-refresh", {"asset": new_digest})

    restarted = SharedAssetStore(root)
    assert restarted.is_pack_ready("lesson/v1-refresh")
    assert (restarted.pack_root / "lesson/v1-refresh/asset").read_bytes() == old


def test_concurrent_cross_process_pack_commits_publish_one_complete_generation(tmp_path):
    root = tmp_path / "tbot"
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    args = str(root), "lesson/v1-race"
    first = context.Process(target=_commit_generation, args=(*args, "first", b"one", start))
    second = context.Process(target=_commit_generation, args=(*args, "second", b"two", start))
    first.start()
    second.start()
    start.set()
    first.join(10)
    second.join(10)

    assert first.exitcode == 0
    assert second.exitcode == 0
    store = SharedAssetStore(root)
    assert store.is_pack_ready("lesson/v1-race")
    manifest = json.loads(
        (store.pack_root / "lesson/v1-race/pack.json").read_text(encoding="utf-8")
    )
    assert set(manifest["assets"]) in ({"first"}, {"second"})


def test_restart_restores_valid_backup_after_crash_between_pack_renames(tmp_path):
    root = tmp_path / "tbot"
    cache_key = "lesson/v1-crash"
    store = SharedAssetStore(root)
    old = b"old-ready"
    new = b"new-generation"
    old_digest = _sha(old)
    new_digest = _sha(new)
    store.put_bytes(old, old_digest)
    store.put_bytes(new, new_digest)
    store.commit_pack(cache_key, {"asset": old_digest})

    context = multiprocessing.get_context("spawn")
    writer = context.Process(
        target=_crash_after_backup, args=(str(root), cache_key, new_digest)
    )
    writer.start()
    writer.join(10)
    assert writer.exitcode == 71
    assert not (store.pack_root / cache_key).exists()

    restarted = SharedAssetStore(root)

    assert restarted.is_pack_ready(cache_key)
    assert (restarted.pack_root / cache_key / "asset").read_bytes() == old


def test_reader_blocks_during_live_swap_and_never_observes_nonready(tmp_path):
    root = tmp_path / "tbot"
    cache_key = "lesson/v1-reader"
    store = SharedAssetStore(root)
    old_digest = _sha(b"old")
    new_digest = _sha(b"new")
    store.put_bytes(b"old", old_digest)
    store.put_bytes(b"new", new_digest)
    store.commit_pack(cache_key, {"asset": old_digest})
    context = multiprocessing.get_context("spawn")
    swapped = context.Event()
    release = context.Event()
    result = context.Queue()
    writer = context.Process(
        target=_swap_pack_while_paused,
        args=(str(root), cache_key, new_digest, swapped, release),
    )
    writer.start()
    assert swapped.wait(10)
    reader = context.Process(target=_read_pack_ready, args=(str(root), cache_key, result))
    reader.start()
    reader.join(0.2)
    assert reader.is_alive()

    release.set()
    writer.join(10)
    reader.join(10)

    assert writer.exitcode == 0
    assert reader.exitcode == 0
    assert result.get(timeout=1) is True
