import errno
import hashlib
import json
import multiprocessing
import os
import threading
from copy import deepcopy
from pathlib import Path

import pytest

from core.lesson.shared_asset_store import (
    PACK_COMMIT_REPLAYED,
    PackReplayMismatchError,
    SharedAssetStore,
)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _rich_manifest(cache_key: str, digest: str, size: int) -> dict:
    return {
        "cacheKey": cache_key,
        "lessonId": "lesson-a",
        "lessonVersion": 1,
        "profile": "espTft",
        "manifestChecksum": "a" * 64,
        "assets": [
            {
                "key": "poster",
                "sha256": digest,
                "size": size,
                "mediaType": "image/jpeg",
                "critical": True,
                "onlineUrl": "https://assets.example/poster.jpg?sig=first",
                "sdPath": f"/sdcard/tbot/lesson-assets/{cache_key}/poster",
            }
        ],
    }


def _rich_mp4_manifest(cache_key: str, digest: str, size: int) -> dict:
    manifest = _rich_manifest(cache_key, digest, size)
    manifest["assets"][0].update({
        "key": "scene.opening@v3", "mediaType": "video/mp4",
        "onlineUrl": "https://assets.example/scene.mp4?variant=1#opening",
        "sdPath": f"/sdcard/tbot/lesson-assets/{cache_key}/scene.opening%40v3",
        "sharedAssetKey": "scene.opening", "sharedAssetVersion": 3,
        "compatibilityMetadata": {
            "codec": "mjpeg", "fps": 10, "durationMs": 1000, "frameCount": 10,
            "hasAudio": False, "rect": {"x": 0, "y": 0, "width": 480, "height": 320}, "chromaKey": None,
        },
        "visualRefs": [{"stepKey": "s1", "phase": "opening", "slot": "backgroundScene.opening"}],
    })
    return manifest


def _rich_flattened_manifest(cache_key: str, digest: str, size: int) -> dict:
    manifest = _rich_manifest(cache_key, digest, size)
    manifest["assets"][0].update({
        "key": "flattenedCinematic.opening", "mediaType": "video/mp4",
        "onlineUrl": "https://assets.example/lessons/derivatives/" + "d" * 64 + "/opening.mp4",
        "sdPath": f"/sdcard/tbot/lesson-assets/{cache_key}/flattenedCinematic.opening",
        "derivativeId": "d" * 64, "phaseId": "opening",
        "compatibilityMetadata": {
            "codec": "mjpeg", "width": 480, "height": 320, "fps": 10,
            "durationMs": 9000, "frameCount": 90, "hasAudio": False,
        },
    })
    return manifest


def _rich_trgb_manifest(cache_key: str, digest: str, size: int) -> dict:
    manifest = _rich_manifest(cache_key, digest, size)
    derivative_id = "d" * 64
    cue_id = "barn-listen"
    manifest["assets"][0].update({
        "key": f"flattenedCinematic.{cue_id}",
        "mediaType": "application/vnd.tbot.rgb565-indexed",
        "onlineUrl": (
            "https://admin.tjbot.vn/lesson-derivatives/lessons/derivatives/"
            f"{derivative_id}/{cue_id}.trgb"
        ),
        "path": f"lessons/derivatives/{derivative_id}/{cue_id}.trgb",
        "sdPath": f"/sdcard/tbot/lesson-assets/{cache_key}/flattenedCinematic.{cue_id}",
        "derivativeId": derivative_id,
        "cueId": cue_id,
        "effect": "listen",
        "stepKey": "barn",
        "playbackMode": "loop",
        "compatibilityMetadata": {
            "codec": "rgb565le", "containerVersion": 1,
            "width": 480, "height": 320, "storedWidth": 320, "storedHeight": 480,
            "orientation": "panelNativeClockwise", "fps": 10,
            "durationMs": 1300, "frameCount": 13, "frameBytes": 307200,
            "hasAudio": False,
        },
    })
    return manifest


def test_ready_rich_pack_replay_accepts_rotated_online_url(tmp_path):
    store = SharedAssetStore(tmp_path / "tbot")
    content = b"poster"
    digest = _sha(content)
    cache_key = f"lesson-a/v1-{'a' * 64}"
    store.put_bytes(content, digest)
    manifest = _rich_manifest(cache_key, digest, len(content))
    store.commit_pack(cache_key, {"poster": digest}, manifest=manifest)
    rotated = deepcopy(manifest)
    rotated["assets"][0]["onlineUrl"] = "https://assets.example/poster.jpg?sig=rotated"

    _pack, status = store.commit_pack(
        cache_key,
        {"poster": digest},
        manifest=rotated,
        return_status=True,
    )

    assert status == PACK_COMMIT_REPLAYED


def test_legacy_pack_commit_replays_existing_ready_rich_manifest(tmp_path):
    store = SharedAssetStore(tmp_path / "tbot")
    content = b"poster"
    digest = _sha(content)
    cache_key = f"lesson-a/v1-{'a' * 64}"
    store.put_bytes(content, digest)
    manifest = _rich_manifest(cache_key, digest, len(content))
    pack = store.commit_pack(cache_key, {"poster": digest}, manifest=manifest)

    _pack, status = store.commit_pack(
        cache_key,
        {"poster": digest},
        return_status=True,
    )

    assert status == PACK_COMMIT_REPLAYED
    preserved = json.loads((pack / "pack.json").read_text(encoding="utf-8"))
    assert isinstance(preserved["assets"], list)
    assert preserved["manifestChecksum"] == manifest["manifestChecksum"]
    assert preserved["assets"][0]["sdPath"] == manifest["assets"][0]["sdPath"]


def test_legacy_subset_commit_preserves_existing_ready_rich_manifest(tmp_path):
    store = SharedAssetStore(tmp_path / "tbot")
    poster = b"poster"
    cinematic = b"cinematic"
    poster_digest = _sha(poster)
    cinematic_digest = _sha(cinematic)
    cache_key = f"lesson-a/v1-{'a' * 64}"
    store.put_bytes(poster, poster_digest)
    store.put_bytes(cinematic, cinematic_digest)
    manifest = _rich_manifest(cache_key, poster_digest, len(poster))
    manifest["assets"].append({
        "key": "flattenedCinematic.opening",
        "sha256": cinematic_digest,
        "size": len(cinematic),
        "mediaType": "application/vnd.tbot.rgb565-indexed",
        "critical": True,
        "onlineUrl": "https://assets.example/opening.trgb",
        "sdPath": f"/sdcard/tbot/lesson-assets/{cache_key}/flattenedCinematic.opening",
    })
    pack = store.commit_pack(
        cache_key,
        {"poster": poster_digest, "flattenedCinematic.opening": cinematic_digest},
        manifest=manifest,
    )

    _pack, status = store.commit_pack(
        cache_key,
        {"poster": poster_digest},
        return_status=True,
    )

    assert status == PACK_COMMIT_REPLAYED
    preserved = json.loads((pack / "pack.json").read_text(encoding="utf-8"))
    assert isinstance(preserved["assets"], list)
    assert [asset["key"] for asset in preserved["assets"]] == [
        "poster",
        "flattenedCinematic.opening",
    ]


@pytest.mark.parametrize("changed_field", ["sha256", "size", "sdPath"])
def test_ready_rich_pack_replay_rejects_asset_identity_changes(tmp_path, changed_field):
    store = SharedAssetStore(tmp_path / "tbot")
    content = b"poster"
    digest = _sha(content)
    cache_key = f"lesson-a/v1-{'a' * 64}"
    store.put_bytes(content, digest)
    manifest = _rich_manifest(cache_key, digest, len(content))
    store.commit_pack(cache_key, {"poster": digest}, manifest=manifest)
    changed = deepcopy(manifest)
    staged_assets = {"poster": digest}
    if changed_field == "sha256":
        changed_content = b"change"
        changed_digest = _sha(changed_content)
        store.put_bytes(changed_content, changed_digest)
        changed["assets"][0]["sha256"] = changed_digest
        staged_assets["poster"] = changed_digest
    elif changed_field == "size":
        changed_content = b"poster-long"
        changed_digest = _sha(changed_content)
        store.put_bytes(changed_content, changed_digest)
        changed["assets"][0]["sha256"] = changed_digest
        changed["assets"][0]["size"] = len(changed_content)
        staged_assets["poster"] = changed_digest
    else:
        changed["assets"][0]["sdPath"] += "-changed"

    with pytest.raises(PackReplayMismatchError):
        store.commit_pack(cache_key, staged_assets, manifest=changed)


@pytest.mark.parametrize("mutate", [
    lambda asset: asset.update(onlineUrl="https://assets.example/scene.mp4?variant=2#opening"),
    lambda asset: asset["compatibilityMetadata"].update(durationMs=2000, frameCount=20),
    lambda asset: asset.update(visualRefs=[{"stepKey": "s1", "phase": "greet", "slot": "backgroundScene.greet"}]),
])
def test_ready_renderer_v3_pack_rejects_changed_rich_identity(tmp_path, mutate):
    store = SharedAssetStore(tmp_path / "tbot")
    content = b"mp4"
    digest = _sha(content)
    cache_key = f"lesson-a/v1-{'a' * 64}"
    store.put_bytes(content, digest)
    manifest = _rich_mp4_manifest(cache_key, digest, len(content))
    store.commit_pack(cache_key, {"scene.opening@v3": digest}, manifest=manifest)
    changed = deepcopy(manifest)
    mutate(changed["assets"][0])

    with pytest.raises(PackReplayMismatchError):
        store.commit_pack(cache_key, {"scene.opening@v3": digest}, manifest=changed)


def test_ready_renderer_v4_pack_reuses_exact_flattened_identity(tmp_path):
    store = SharedAssetStore(tmp_path / "tbot")
    content = b"flattened-mp4"
    digest = _sha(content)
    cache_key = f"lesson-a/v4-{'a' * 64}"
    store.put_bytes(content, digest)
    manifest = _rich_flattened_manifest(cache_key, digest, len(content))
    store.commit_pack(cache_key, {"flattenedCinematic.opening": digest}, manifest=manifest)

    _pack, status = store.commit_pack(
        cache_key,
        {"flattenedCinematic.opening": digest},
        manifest=deepcopy(manifest),
        return_status=True,
    )

    assert status == PACK_COMMIT_REPLAYED


@pytest.mark.parametrize("field,value", [
    ("derivativeId", "e" * 64),
    ("phaseId", "greet"),
])
def test_ready_renderer_v4_pack_rejects_changed_flattened_identity(tmp_path, field, value):
    store = SharedAssetStore(tmp_path / "tbot")
    content = b"flattened-mp4"
    digest = _sha(content)
    cache_key = f"lesson-a/v4-{'a' * 64}"
    store.put_bytes(content, digest)
    manifest = _rich_flattened_manifest(cache_key, digest, len(content))
    store.commit_pack(cache_key, {"flattenedCinematic.opening": digest}, manifest=manifest)
    changed = deepcopy(manifest)
    changed["assets"][0][field] = value

    with pytest.raises(PackReplayMismatchError):
        store.commit_pack(
            cache_key,
            {"flattenedCinematic.opening": digest},
            manifest=changed,
        )


@pytest.mark.parametrize("mutate", [
    lambda asset: asset.update(onlineUrl=asset["onlineUrl"].replace("admin.tjbot.vn", "evil.example")),
    lambda asset: asset.update(derivativeId="e" * 64),
    lambda asset: asset.update(path=asset["path"].replace("barn-listen.trgb", "wrong.trgb")),
    lambda asset: asset.update(cueId="barn-thinking"),
    lambda asset: asset.update(effect="thinking"),
    lambda asset: asset.update(stepKey="hay"),
    lambda asset: asset.update(playbackMode="once"),
    lambda asset: asset["compatibilityMetadata"].update(frameCount=12),
])
def test_ready_trgb_pack_replay_rejects_changed_rich_identity(tmp_path, mutate):
    store = SharedAssetStore(tmp_path / "tbot")
    content = b"trgb"
    digest = _sha(content)
    cache_key = f"lesson-a/v7-{'a' * 64}"
    key = "flattenedCinematic.barn-listen"
    store.put_bytes(content, digest)
    manifest = _rich_trgb_manifest(cache_key, digest, len(content))
    store.commit_pack(cache_key, {key: digest}, manifest=manifest)
    changed = deepcopy(manifest)
    mutate(changed["assets"][0])

    with pytest.raises(PackReplayMismatchError):
        store.commit_pack(cache_key, {key: digest}, manifest=changed)


def test_ready_trgb_pack_replays_only_exact_rich_identity(tmp_path):
    store = SharedAssetStore(tmp_path / "tbot")
    content = b"trgb"
    digest = _sha(content)
    cache_key = f"lesson-a/v7-{'a' * 64}"
    key = "flattenedCinematic.barn-listen"
    store.put_bytes(content, digest)
    manifest = _rich_trgb_manifest(cache_key, digest, len(content))
    store.commit_pack(cache_key, {key: digest}, manifest=manifest)

    _pack, status = store.commit_pack(
        cache_key,
        {key: digest},
        manifest=deepcopy(manifest),
        return_status=True,
    )

    assert status == PACK_COMMIT_REPLAYED


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


def _commit_with_gc_pause(root, cache_key, digest, started, release):
    def pause(stage, path):
        if stage == "after_replace" and path.name == "pack.json":
            started.set()
            release.wait(10)

    SharedAssetStore(root, failure_hook=pause).commit_pack(cache_key, {"asset": digest})


def _gc_during_commit(root, cache_key, started, result):
    from core.lesson.sd_pack_gc import SdPackGarbageCollector

    started.wait(10)
    store = SharedAssetStore(root, cleanup_on_init=False)
    usage = lambda _path: type("Usage", (), {"total": 100, "free": 50})()
    result.put(
        SdPackGarbageCollector(
            store.pack_root, shared_store=store, quota_bytes=1, disk_usage=usage
        ).collect_one(preloading_cache_key=cache_key)
    )


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
    # A fault this process observes reclaims its own temp immediately: on a full
    # SD card the stranded bytes are exactly the space that just ran out.
    assert not list((tmp_path / "tbot").rglob("*.part"))

    # A killed process never runs that cleanup, so cleanup_parts still has to
    # collect a genuinely orphaned temp.
    orphan = store.shared_root / "ab" / f"abandoned.{os.getpid() + 1}.deadbeef.part"
    orphan.parent.mkdir(parents=True, exist_ok=True)
    orphan.write_bytes(b"partial")
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
    assert not list(root.rglob("*.part"))

    # Simulate the case no in-process cleanup can cover: the writer was killed
    # between creating the temp and publishing it.
    orphan = store.shared_root / "cd" / f"killed.{os.getpid() + 1}.cafebabe.part"
    orphan.parent.mkdir(parents=True, exist_ok=True)
    orphan.write_bytes(data)

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


def test_commit_reader_and_gc_are_cross_process_serialized(tmp_path):
    root = tmp_path / "tbot"
    store = SharedAssetStore(root)
    old_digest = _sha(b"old-gc")
    new_digest = _sha(b"new-gc")
    store.put_bytes(b"old-gc", old_digest)
    store.put_bytes(b"new-gc", new_digest)
    store.commit_pack("lesson/v1-old", {"asset": old_digest})
    context = multiprocessing.get_context("spawn")
    started = context.Event()
    release = context.Event()
    read_result = context.Queue()
    gc_result = context.Queue()
    cache_key = "lesson/v2-new"
    writer = context.Process(
        target=_commit_with_gc_pause,
        args=(str(root), cache_key, new_digest, started, release),
    )
    writer.start()
    assert started.wait(10)
    reader = context.Process(target=_read_pack_ready, args=(str(root), cache_key, read_result))
    collector = context.Process(
        target=_gc_during_commit, args=(str(root), cache_key, started, gc_result)
    )
    reader.start()
    collector.start()
    release.set()
    writer.join(10)
    reader.join(10)
    collector.join(10)

    assert writer.exitcode == reader.exitcode == collector.exitcode == 0
    assert read_result.get(timeout=1) is True
    assert gc_result.get(timeout=1)["deleted"] == "lesson/v1-old"
    assert SharedAssetStore(root).is_pack_ready(cache_key)


def test_sweep_blocks_between_put_and_materialize_until_hardlink_exists(tmp_path):
    root = tmp_path / "tbot"
    source = tmp_path / "asset.bin"
    source.write_bytes(b"leased-cas")
    digest = _sha(source.read_bytes())
    put_finished = threading.Event()
    release = threading.Event()
    sweep_finished = threading.Event()

    def pause(stage, path):
        if stage == "after_replace" and path.name == digest:
            put_finished.set()
            release.wait(10)

    store = SharedAssetStore(root, failure_hook=pause)

    writer = threading.Thread(
        target=store.put_file_and_materialize,
        args=(source, digest, "lesson/v1-race", "asset"),
    )
    sweeper = threading.Thread(
        target=lambda: (
            store.sweep_unreferenced_cas(),
            sweep_finished.set(),
        )
    )
    writer.start()
    assert put_finished.wait(10)
    sweeper.start()
    assert not sweep_finished.wait(0.2)
    release.set()
    writer.join(10)
    sweeper.join(10)

    assert sweep_finished.is_set()
    assert store.asset_path(digest).exists()
    assert (store.pack_root / "lesson/v1-race/asset").read_bytes() == b"leased-cas"
