import hashlib
import json
import multiprocessing
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.lesson import sd_pack_materializer as materializer
from core.lesson.cache_key_contract import compose_asset_sd_path
from core.lesson.sd_pack_gc import SdPackActivationState, SdPackGarbageCollector
from core.lesson.shared_asset_store import SharedAssetStore


def _write_candidate_state(root, state_path, identity, start):
    store = SharedAssetStore(root, cleanup_on_init=False)
    state = SdPackActivationState(store, state_path=state_path)
    start.wait(10)
    state.begin_candidate(identity)


def _ready_pack(store: SharedAssetStore, cache_key: str, content: bytes) -> Path:
    digest = hashlib.sha256(content).hexdigest()
    store.put_bytes(content, digest)
    return store.commit_pack(cache_key, {"asset": digest})


def _disk(total: int, free: int):
    return type("Usage", (), {"total": total, "used": total - free, "free": free})()


class _AssetResponse:
    def __init__(self, content: bytes):
        self.content = content
        self.status_code = 200
        self.headers = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc_info):
        return False

    def raise_for_status(self):
        return None

    async def aiter_bytes(self, _chunk_size):
        yield self.content


class _AssetClient:
    def __init__(self, url: str, content: bytes):
        self.url = url
        self.content = content
        self.requests = []

    def stream(self, method: str, url: str, **_kwargs):
        self.requests.append((method, url))
        assert url == self.url
        return _AssetResponse(self.content)


async def _public_resolver(_host):
    return ["93.184.216.34"]


def _materialize_manifest(cache_key: str, version: int, checksum: str, content: bytes):
    url = "https://assets.example/candidate.bin"
    return url, {
        "lessonId": "lesson",
        "lessonVersion": version,
        "profile": "espTft",
        "manifestChecksum": checksum,
        "cacheKey": cache_key,
        "assets": [{
            "key": "asset",
            "sha256": hashlib.sha256(content).hexdigest(),
            "size": len(content),
            "mediaType": "application/octet-stream",
            "critical": True,
            "onlineUrl": url,
            "sdPath": compose_asset_sd_path(cache_key, "asset"),
        }],
    }


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

    assert result["deleted"] == keys["oldest"]
    assert result["reason"] == "quota"
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
    result = gc.collect_one()
    assert result["deleted"] == "lesson/v1-old"
    assert result["reason"] == "low_free_space"


def test_preload_is_refused_below_five_percent_free(tmp_path):
    free = {"value": 5}
    gc = SdPackGarbageCollector(
        tmp_path / "packs",
        disk_usage=lambda _path: _disk(100, free["value"]),
    )

    assert gc.can_preload()
    free["value"] = 4
    assert not gc.can_preload()


@pytest.mark.asyncio
async def test_materializer_blocks_candidate_below_disk_floor_without_evicting_rollback_pack(
    tmp_path, monkeypatch
):
    pack_root = tmp_path / "tbot" / "lesson-assets"
    store = SharedAssetStore(pack_root.parent, pack_root=pack_root)
    previous = {
        "cacheKey": "lesson/v1-" + "a" * 64,
        "lessonVersion": 1,
        "manifestChecksum": "a" * 64,
    }
    current = {
        "cacheKey": "lesson/v2-" + "b" * 64,
        "lessonVersion": 2,
        "manifestChecksum": "b" * 64,
    }
    candidate = {
        "cacheKey": "lesson/v3-" + "c" * 64,
        "lessonVersion": 3,
        "manifestChecksum": "c" * 64,
    }
    previous_pack = _ready_pack(store, previous["cacheKey"], b"previous")
    current_pack = _ready_pack(store, current["cacheKey"], b"current")
    activation = SdPackActivationState(store, current=previous)
    activation.begin_candidate(current)
    assert activation.activate_candidate(current)
    activation.begin_candidate(candidate)
    url, manifest = _materialize_manifest(
        candidate["cacheKey"], candidate["lessonVersion"], candidate["manifestChecksum"], b"candidate"
    )
    client = _AssetClient(url, b"candidate")
    monkeypatch.setattr(materializer.shutil, "disk_usage", lambda _path: _disk(100, 4))

    with pytest.raises(materializer.MaterializationError) as exc_info:
        await materializer.materialize_lesson_sd_pack(
            manifest,
            config={
                "lesson": {
                    "asset_pack_mount_root": str(pack_root),
                    "asset_allowed_origins": "https://assets.example",
                    "max_file_bytes": 1024,
                    "max_pack_bytes": 1024,
                    "sd_preload_min_free_percent": 5,
                }
            },
            client=client,
            resolver=_public_resolver,
        )

    assert exc_info.value.code == "SD_PRELOAD_SPACE_LOW"
    assert client.requests == []
    assert activation.current == current
    assert activation.previous_known_good == previous
    assert activation.candidate == candidate
    assert current_pack.joinpath("READY").is_file()
    assert previous_pack.joinpath("READY").is_file()
    assert not store.is_pack_ready(candidate["cacheKey"])
    assert activation.rollback(previous)
    assert activation.current == previous


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
    assert not shared.exists()


def test_gc_counts_physical_bytes_and_sweeps_only_unreferenced_cas(tmp_path):
    store = SharedAssetStore(tmp_path / "tbot")
    shared_content = b"shared-physical"
    shared_digest = hashlib.sha256(shared_content).hexdigest()
    old_content = b"old-only-physical"
    old_digest = hashlib.sha256(old_content).hexdigest()
    store.put_bytes(shared_content, shared_digest)
    store.put_bytes(old_content, old_digest)
    old_pack = store.commit_pack(
        "lesson/v1-old", {"shared": shared_digest, "old": old_digest}
    )
    new_pack = store.commit_pack("lesson/v2-current", {"shared": shared_digest})
    os.utime(old_pack, (1, 1))
    os.utime(new_pack, (2, 2))
    gc = SdPackGarbageCollector(
        store.pack_root,
        shared_store=store,
        quota_bytes=1,
        disk_usage=lambda _path: _disk(100, 50),
    )
    before = gc.physical_usage_bytes()

    result = gc.collect_one(current_cache_key="lesson/v2-current")

    after = gc.physical_usage_bytes()
    assert result["deleted"] == "lesson/v1-old"
    assert result["deletedCas"] == [old_digest]
    assert after < before
    assert not store.asset_path(old_digest).exists()
    assert store.asset_path(shared_digest).exists()
    assert store.is_pack_ready("lesson/v2-current")


def test_activation_state_persists_exact_identity_and_reloads_for_rollback(tmp_path):
    store = SharedAssetStore(tmp_path / "tbot")
    old_key = "lesson/v3-aaaaaaaa"
    new_key = "lesson/v4-bbbbbbbb"
    _ready_pack(store, old_key, b"old")
    _ready_pack(store, new_key, b"new")
    state_file = tmp_path / "activation.json"
    state = SdPackActivationState(
        store,
        state_path=state_file,
        current={"cacheKey": old_key, "lessonVersion": 3, "manifestChecksum": "aaaaaaaa"},
    )
    candidate = {"cacheKey": new_key, "lessonVersion": 4, "manifestChecksum": "bbbbbbbb"}

    state.begin_candidate(
        {"cacheKey": new_key, "lessonVersion": 3, "manifestChecksum": "bbbbbbbb"}
    )
    assert not state.verify_for_activation()
    state.begin_candidate(candidate)
    assert state.verify_for_activation(candidate)
    assert state.activate_candidate(candidate)
    restarted = SdPackActivationState(store, state_path=state_file)

    assert restarted.current == candidate
    assert restarted.previous_known_good == {
        "cacheKey": old_key,
        "lessonVersion": 3,
        "manifestChecksum": "aaaaaaaa",
    }
    assert not restarted.rollback(
        {"cacheKey": old_key, "lessonVersion": 3, "manifestChecksum": "wrong"}
    )
    assert restarted.rollback(restarted.previous_known_good)
    assert restarted.current["cacheKey"] == old_key


def test_runtime_gc_boot_cleanup_runs_once_per_pack_root(monkeypatch, tmp_path):
    from core.lesson import runtime

    calls = []
    original = SdPackGarbageCollector.boot_cleanup

    def record(self):
        calls.append(self.pack_root)
        return original(self)

    monkeypatch.setattr(SdPackGarbageCollector, "boot_cleanup", record)
    runtime._SD_PACK_BOOT_CLEANED_ROOTS.clear()
    config = {"asset_pack_mount_root": str(tmp_path / "tbot/lesson-assets")}
    conn = type("Conn", (), {"lesson_runtime": None, "is_realtime_busy": lambda self: False})()

    runtime._sd_pack_gc_for_connection(conn, config)
    runtime._sd_pack_gc_for_connection(conn, config)

    assert calls == [(tmp_path / "tbot/lesson-assets").resolve()]


def test_runtime_gc_protects_all_connections_after_three_activations(tmp_path):
    from core.lesson import runtime

    store = SharedAssetStore(tmp_path / "tbot")
    connections = {}
    for number in (1, 2, 3):
        checksum = str(number) * 64
        key = f"lesson-{number}/v1-{checksum}"
        _ready_pack(store, key, str(number).encode())
        activation = SdPackActivationState(store)
        activation.begin_candidate({"cacheKey": key, "lessonVersion": 1, "manifestChecksum": checksum})
        assert activation.activate_candidate()
        connections[str(number)] = SimpleNamespace(
            lesson_runtime=SimpleNamespace(asset_cache=SimpleNamespace(cache_key=key)),
            lesson_sd_pack_activation=activation,
        )
    conn = SimpleNamespace(server=SimpleNamespace(lesson_connections=connections))
    gc = runtime._sd_pack_gc_for_connection(conn, {
        "asset_pack_mount_root": str(store.pack_root), "sd_cache_quota_bytes": 1,
    })

    assert gc.collect_one() == {"skipped": "no_evictable_pack"}
    assert all(store.is_pack_ready(item.lesson_runtime.asset_cache.cache_key) for item in connections.values())


def test_runtime_gc_rereads_connection_protection_at_deletion(tmp_path):
    from core.lesson import runtime

    store = SharedAssetStore(tmp_path / "tbot")
    key = "lesson/v1-" + "a" * 64
    pack = _ready_pack(store, key, b"active-late")
    connections = {}
    conn = SimpleNamespace(server=SimpleNamespace(lesson_connections=connections))
    gc = runtime._sd_pack_gc_for_connection(conn, {
        "asset_pack_mount_root": str(store.pack_root), "sd_cache_quota_bytes": 1,
    })
    delete_pack = gc.shared_store.delete_pack

    def protect_before_delete(cache_key, **kwargs):
        connections["other"] = SimpleNamespace(lesson_previous_known_good_cache_key=key)
        return delete_pack(cache_key, **kwargs)

    gc.shared_store.delete_pack = protect_before_delete
    assert gc.collect_one() == {"skipped": "pack_became_protected"}
    assert pack.exists()


def test_backend_rollback_requires_exact_old_version_and_checksum(tmp_path):
    from core.lesson import runtime

    store = SharedAssetStore(tmp_path / "tbot")
    old_key = "lesson-a/v3-aaaaaaaa"
    new_key = "lesson-a/v4-bbbbbbbb"
    _ready_pack(store, old_key, b"old")
    _ready_pack(store, new_key, b"new")
    state = SdPackActivationState(
        store,
        current={"cacheKey": old_key, "lessonVersion": 3, "manifestChecksum": "aaaaaaaa"},
    )
    state.begin_candidate(
        {"cacheKey": new_key, "lessonVersion": 4, "manifestChecksum": "bbbbbbbb"}
    )
    assert state.activate_candidate()
    conn = type("Conn", (), {"lesson_sd_pack_activation": state})()

    assert not runtime.rollback_sd_pack_assignment(
        conn,
        {"lessonId": "lesson-a", "lessonVersion": 3, "manifestChecksum": "wrong"},
    )
    assert runtime.rollback_sd_pack_assignment(
        conn,
        {"lessonId": "lesson-a", "lessonVersion": 3, "manifestChecksum": "aaaaaaaa"},
    )
    assert conn.lesson_current_cache_key == old_key
    assert conn.lesson_previous_known_good_cache_key == new_key


def test_activation_state_concurrent_writers_leave_one_valid_atomic_record(tmp_path):
    root = tmp_path / "tbot"
    SharedAssetStore(root)
    state_path = root / "activation.json"
    identities = [
        {"cacheKey": "lesson/v1-aaaaaaaa", "lessonVersion": 1, "manifestChecksum": "aaaaaaaa"},
        {"cacheKey": "lesson/v2-bbbbbbbb", "lessonVersion": 2, "manifestChecksum": "bbbbbbbb"},
    ]
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    writers = [
        context.Process(
            target=_write_candidate_state,
            args=(str(root), str(state_path), identity, start),
        )
        for identity in identities
    ]
    for writer in writers:
        writer.start()
    start.set()
    for writer in writers:
        writer.join(10)

    assert [writer.exitcode for writer in writers] == [0, 0]
    restarted = SdPackActivationState(SharedAssetStore(root), state_path=state_path)
    assert restarted.candidate in identities
    assert not list(root.glob("activation.json.*.part"))


def test_activation_state_restart_ignores_crash_temp_and_keeps_last_commit(tmp_path):
    root = tmp_path / "tbot"
    store = SharedAssetStore(root)
    state_path = tmp_path / "activation.json"
    current = {
        "cacheKey": "lesson/v1-aaaaaaaa",
        "lessonVersion": 1,
        "manifestChecksum": "aaaaaaaa",
    }
    SdPackActivationState(store, state_path=state_path, current=current)
    crash_part = root / "activation.json.999.crash.part"
    crash_part.write_text("truncated", encoding="utf-8")

    restarted = SdPackActivationState(SharedAssetStore(root), state_path=state_path)

    assert restarted.current == current
    assert not crash_part.exists()
