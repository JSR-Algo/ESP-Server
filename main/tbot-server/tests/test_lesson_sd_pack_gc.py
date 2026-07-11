import hashlib
import json
import os
from pathlib import Path

from core.lesson.sd_pack_gc import SdPackActivationState, SdPackGarbageCollector
from core.lesson.shared_asset_store import SharedAssetStore


def _ready_pack(store: SharedAssetStore, cache_key: str, content: bytes) -> Path:
    digest = hashlib.sha256(content).hexdigest()
    store.put_bytes(content, digest)
    return store.commit_pack(cache_key, {"asset": digest})


def _disk(total: int, free: int):
    return type("Usage", (), {"total": total, "used": total - free, "free": free})()


def test_gc_protects_exact_runtime_keys_and_deletes_only_one_lru_pack(tmp_path):
    store = SharedAssetStore(tmp_path / "tbot")
    keys = {
        "active": "lesson/v1-active",
        "preloading": "lesson/v2-preloading",
        "current": "lesson/v3-current",
        "previous": "lesson/v4-previous",
        "oldest": "lesson/v5-oldest",
        "newer": "lesson/v6-newer",
    }
    packs = {name: _ready_pack(store, key, name.encode()) for name, key in keys.items()}
    for index, name in enumerate(("oldest", "newer", "active", "preloading", "current", "previous"), 1):
        os.utime(packs[name], (index, index))

    gc = SdPackGarbageCollector(
        store.pack_root,
        shared_store=store,
        quota_bytes=1,
        disk_usage=lambda _path: _disk(100, 50),
    )
    result = gc.collect_one(
        active_cache_key=keys["active"],
        preloading_cache_key=keys["preloading"],
        current_cache_key=keys["current"],
        previous_known_good_cache_key=keys["previous"],
    )

    assert result == {"deleted": keys["oldest"], "reason": "quota"}
    assert not packs["oldest"].exists()
    assert packs["newer"].exists()
    assert all(packs[name].exists() for name in ("active", "preloading", "current", "previous"))


def test_gc_thresholds_and_busy_guards(tmp_path):
    store = SharedAssetStore(tmp_path / "tbot")
    pack = _ready_pack(store, "lesson/v1-old", b"old")
    usage = {"free": 21}
    busy = {"voice": False, "render": False}
    gc = SdPackGarbageCollector(
        store.pack_root,
        shared_store=store,
        quota_bytes=10_000,
        disk_usage=lambda _path: _disk(100, usage["free"]),
        voice_busy=lambda: busy["voice"],
        render_busy=lambda: busy["render"],
    )

    assert gc.collect_one() == {"skipped": "threshold_not_met"}
    usage["free"] = 19
    busy["voice"] = True
    assert gc.collect_one() == {"skipped": "voice_busy"}
    busy["voice"] = False
    busy["render"] = True
    assert gc.collect_one() == {"skipped": "lesson_render_busy"}
    assert pack.exists()
    busy["render"] = False
    assert gc.collect_one() == {"deleted": "lesson/v1-old", "reason": "low_free_space"}


def test_preload_is_refused_below_five_percent_free(tmp_path):
    free = {"value": 5}
    gc = SdPackGarbageCollector(
        tmp_path / "packs",
        disk_usage=lambda _path: _disk(100, free["value"]),
    )

    assert gc.can_preload()
    free["value"] = 4
    assert not gc.can_preload()


def test_boot_cleanup_removes_parts_and_ignores_nonready_packs(tmp_path):
    store = SharedAssetStore(tmp_path / "tbot")
    ready = _ready_pack(store, "lesson/v1-ready", b"ready")
    incomplete = store.pack_root / "lesson/v2-incomplete"
    incomplete.mkdir(parents=True)
    (incomplete / "pack.json").write_text(json.dumps({"cacheKey": "lesson/v2-incomplete", "assets": {}}))
    stale = store.root / "orphan.part"
    stale.write_bytes(b"partial")

    gc = SdPackGarbageCollector(store.pack_root, shared_store=store)
    report = gc.boot_cleanup()

    assert not stale.exists()
    assert report["ready"] == ["lesson/v1-ready"]
    assert report["ignored"] == ["lesson/v2-incomplete"]
    assert ready.exists() and incomplete.exists()


def test_activation_is_two_phase_and_rollback_reattests_exact_old_pack(tmp_path):
    store = SharedAssetStore(tmp_path / "tbot")
    old_key = "lesson/v1-" + "a" * 64
    new_key = "lesson/v2-" + "b" * 64
    _ready_pack(store, old_key, b"old")
    _ready_pack(store, new_key, b"new")
    state = SdPackActivationState(store, current_cache_key=old_key)

    state.begin_candidate(new_key)
    assert state.current_cache_key == old_key
    assert state.candidate_cache_key == new_key
    assert state.activate_candidate()
    assert state.current_cache_key == new_key
    assert state.previous_known_good_cache_key == old_key

    old_manifest = json.loads((store.pack_root / old_key / "pack.json").read_text())
    old_digest = next(iter(old_manifest["assets"].values()))
    store.asset_path(old_digest).write_bytes(b"corrupt")
    assert not state.rollback(old_key)
    assert state.current_cache_key == new_key

    store.put_bytes(b"old", old_digest)
    store.commit_pack(old_key, {"asset": old_digest})
    assert state.rollback(old_key)
    assert state.current_cache_key == old_key
    assert state.previous_known_good_cache_key == new_key


def test_gc_never_deletes_shared_cas_blobs(tmp_path):
    store = SharedAssetStore(tmp_path / "tbot")
    pack = _ready_pack(store, "lesson/v1-old", b"shared")
    manifest = json.loads((pack / "pack.json").read_text())
    digest = next(iter(manifest["assets"].values()))
    shared = store.asset_path(digest)
    gc = SdPackGarbageCollector(
        store.pack_root,
        shared_store=store,
        quota_bytes=1,
        disk_usage=lambda _path: _disk(100, 50),
    )

    assert gc.collect_one()["deleted"] == "lesson/v1-old"
    assert shared.exists()
