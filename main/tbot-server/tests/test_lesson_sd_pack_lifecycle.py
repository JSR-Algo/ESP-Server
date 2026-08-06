"""T2.2 — SD pack lifecycle edges (materialize / GC / evict / retry).

Each test here pins one lifecycle edge from the T2.2 deep-dive checklist that
was previously unproven. Edges already covered elsewhere keep their home:

* concurrent double-trigger materialize -> ``test_lesson_sd_pack_materializer``
  (``test_concurrent_identical_materializations_yield_one_accept_and_one_locked_replay``)
* sha256 mismatch blocks READY -> ``test_lesson_sd_pack_materializer`` /
  ``test_shared_asset_store`` (``test_checksum_mismatch_never_commits_asset``)
* restart mid-materialize -> ``test_shared_asset_store``
  (``test_interrupted_pack_materialization_never_creates_ready``)
* orphan pack collection -> ``test_lesson_sd_pack_gc``
* queued fanout on reconnect -> ``test_lesson_sd_pack_fanout``
  (``test_drain_pending_on_reconnect``)
"""

import errno
import hashlib
import os
import time
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from core.lesson import sd_pack_materializer as materializer
from core.lesson.sd_pack_gc import SdPackActivationState, SdPackGarbageCollector
from core.lesson.shared_asset_store import SharedAssetStore

CHECKSUM = "b" * 64
CACHE_KEY = f"lesson-one/v3-{CHECKSUM}"
POSTER = b"poster-bytes-0123456789"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _disk(total: int, free: int):
    return type("Usage", (), {"total": total, "used": total - free, "free": free})()


def _ready_pack(store: SharedAssetStore, cache_key: str, content: bytes) -> Path:
    digest = _sha(content)
    store.put_bytes(content, digest)
    return store.commit_pack(cache_key, {"asset": digest})


# ── GC vs the active assignment ─────────────────────────────────────────────


def test_gc_refuses_a_pack_that_became_protected_after_candidate_selection(tmp_path):
    """The candidate scan is a snapshot; activation can win the race after it."""
    store = SharedAssetStore(tmp_path / "tbot")
    victim, keeper = "lesson/v1-victim", "lesson/v2-keeper"
    packs = {key: _ready_pack(store, key, key.encode()) for key in (victim, keeper)}
    os.utime(packs[victim], (1, 1))
    os.utime(packs[keeper], (9, 9))
    activation = SdPackActivationState(store)

    gc = SdPackGarbageCollector(
        store.pack_root,
        shared_store=store,
        quota_bytes=1,
        disk_usage=lambda _path: _disk(100, 50),
    )
    real_delete = store.delete_pack

    def delete_after_activation_wins(cache_key, **kwargs):
        activation.begin_candidate(
            {"cacheKey": cache_key, "lessonVersion": 1, "manifestChecksum": "victim"}
        )
        return real_delete(cache_key, **kwargs)

    store.delete_pack = delete_after_activation_wins

    assert gc.collect_one() == {"skipped": "pack_became_protected"}
    assert packs[victim].exists()
    assert packs[keeper].exists()


def test_gc_protects_a_pack_another_connection_activated(tmp_path):
    """One activation record serves every robot on this server."""
    store = SharedAssetStore(tmp_path / "tbot")
    other_robot_current = "lesson/v1-other"
    collectable = "lesson/v2-idle"
    packs = {
        key: _ready_pack(store, key, key.encode())
        for key in (other_robot_current, collectable)
    }
    os.utime(packs[other_robot_current], (1, 1))
    os.utime(packs[collectable], (2, 2))
    SdPackActivationState(store, current_cache_key=other_robot_current)

    gc = SdPackGarbageCollector(
        store.pack_root,
        shared_store=store,
        quota_bytes=1,
        disk_usage=lambda _path: _disk(100, 50),
    )

    # This connection knows nothing about the other robot's lesson.
    result = gc.collect_one(preloading_cache_key="lesson/v9-mine")

    assert result["deleted"] == collectable
    assert packs[other_robot_current].exists()


def test_gc_skips_collection_when_protection_cannot_be_read(tmp_path):
    store = SharedAssetStore(tmp_path / "tbot")
    pack = _ready_pack(store, "lesson/v1-old", b"old")

    def broken_provider():
        raise RuntimeError("activation state unreadable")

    gc = SdPackGarbageCollector(
        store.pack_root,
        shared_store=store,
        quota_bytes=1,
        disk_usage=lambda _path: _disk(100, 50),
        protected_keys_provider=broken_provider,
    )

    assert gc.collect_one() == {"skipped": "protection_unavailable"}
    assert pack.exists()


def test_delete_pack_refuses_a_protected_key_instead_of_deleting_it(tmp_path):
    from core.lesson.shared_asset_store import PackDeletionRefused

    store = SharedAssetStore(tmp_path / "tbot")
    pack = _ready_pack(store, "lesson/v1-current", b"current")

    with pytest.raises(PackDeletionRefused):
        store.delete_pack("lesson/v1-current", protected_cache_keys={"lesson/v1-current"})

    assert pack.exists()


# ── Shared-asset refcounting across eviction ────────────────────────────────


def test_shared_asset_survives_one_pack_eviction_and_dies_with_the_last(tmp_path):
    store = SharedAssetStore(tmp_path / "tbot")
    shared = b"shared-bytes"
    digest = _sha(shared)
    store.put_bytes(shared, digest)
    store.commit_pack("lesson/v1-a", {"asset": digest})
    store.commit_pack("lesson/v2-b", {"asset": digest})

    store.delete_pack("lesson/v1-a", sweep=True)
    assert store.asset_path(digest).is_file()

    store.delete_pack("lesson/v2-b", sweep=True)
    assert not store.asset_path(digest).exists()


# ── Disk-full ───────────────────────────────────────────────────────────────


class _FullDiskHandle:
    def __init__(self, handle):
        self._handle = handle

    def __enter__(self):
        self._handle.__enter__()
        return self

    def __exit__(self, *exc):
        return self._handle.__exit__(*exc)

    def write(self, _data):
        raise OSError(errno.ENOSPC, "No space left on device")

    def __getattr__(self, name):
        return getattr(self._handle, name)


def _full_disk_open(monkeypatch, marker: str):
    real_open = Path.open

    def fake_open(self, mode="r", *args, **kwargs):
        handle = real_open(self, mode, *args, **kwargs)
        if "x" in mode and marker in str(self):
            return _FullDiskHandle(handle)
        return handle

    monkeypatch.setattr(Path, "open", fake_open)


def test_disk_full_during_cas_write_reclaims_the_partial_bytes(tmp_path, monkeypatch):
    store = SharedAssetStore(tmp_path / "tbot")
    source = tmp_path / "asset.bin"
    source.write_bytes(POSTER)

    _full_disk_open(monkeypatch, ".part")
    with pytest.raises(OSError):
        store.put_file(source, _sha(POSTER))
    monkeypatch.undo()

    assert not list((tmp_path / "tbot").rglob("*.part"))
    assert not store.attest(_sha(POSTER))


def _manifest() -> dict:
    return {
        "lessonId": "lesson-one",
        "lessonVersion": 3,
        "profile": "espTft",
        "manifestChecksum": CHECKSUM,
        "cacheKey": CACHE_KEY,
        "assets": [
            {
                "key": "poster.jpg",
                "sha256": _sha(POSTER),
                "size": len(POSTER),
                "mediaType": "image/jpeg",
                "critical": True,
                "onlineUrl": "https://assets.example/poster.jpg",
                "sdPath": f"/sdcard/tbot/lesson-assets/{CACHE_KEY}/poster.jpg",
            }
        ],
    }


def _materializer_config(tmp_path) -> dict:
    return {
        "lesson": {
            "asset_pack_mount_root": str(tmp_path / "tbot" / "lesson-assets"),
            "max_file_bytes": 1024 * 1024,
            "max_pack_bytes": 4 * 1024 * 1024,
            "asset_allowed_origins": "https://assets.example",
        }
    }


class _Stream:
    def __init__(self, chunks):
        self.status_code = 200
        self._chunks = chunks

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def raise_for_status(self):
        return None

    async def aiter_bytes(self, _size):
        for chunk in self._chunks:
            yield chunk


class _Client:
    def __init__(self, chunks):
        self._chunks = chunks

    def stream(self, _method, _url):
        return _Stream(self._chunks)

    async def aclose(self):
        return None


async def _public_resolver(_host):
    return ["93.184.216.34"]


@pytest.mark.asyncio
async def test_disk_full_mid_materialize_fails_locally_and_leaves_no_partials(
    tmp_path, monkeypatch
):
    config = _materializer_config(tmp_path)
    Path(config["lesson"]["asset_pack_mount_root"]).mkdir(parents=True)

    _full_disk_open(monkeypatch, materializer.STAGING_PREFIX)
    with pytest.raises(materializer.MaterializationError) as excinfo:
        await materializer.materialize_lesson_sd_pack(
            _manifest(),
            config=config,
            client=_Client([POSTER]),
            resolver=_public_resolver,
        )
    monkeypatch.undo()

    error = excinfo.value
    # A full card is a local storage fault, not an asset-origin fault: calling it
    # DOWNLOAD_FAILED sends the backend retrying against a healthy origin.
    assert error.code == "STORAGE_ERROR"
    assert error.retryable is True
    assert error.details["assetKey"] == "poster.jpg"

    root = tmp_path / "tbot"
    assert list((root / ".materialize").iterdir()) == []
    assert not (root / "lesson-assets" / CACHE_KEY).exists()
    assert not list(root.rglob("*.part"))


@pytest.mark.asyncio
async def test_crashed_materialize_staging_is_reclaimed_by_the_next_run(tmp_path):
    config = _materializer_config(tmp_path)
    Path(config["lesson"]["asset_pack_mount_root"]).mkdir(parents=True)
    store = materializer._shared_store(config)
    staging_root = store.root / ".materialize"
    staging_root.mkdir(parents=True, exist_ok=True)

    crashed = staging_root / f"{materializer.STAGING_PREFIX}crashed"
    crashed.mkdir()
    (crashed / "half.bin").write_bytes(b"x" * 4096)
    stale = time.time() - materializer.DEFAULT_STAGING_STALE_SEC - 60
    os.utime(crashed, (stale, stale))

    fresh = staging_root / f"{materializer.STAGING_PREFIX}inflight"
    fresh.mkdir()

    result = await materializer.materialize_lesson_sd_pack(
        _manifest(),
        config=config,
        client=_Client([POSTER]),
        resolver=_public_resolver,
    )

    assert result["ready"] is True
    assert not crashed.exists()
    # A staging dir that could still belong to a live materialize is left alone.
    assert fresh.exists()


# ── Pending store: corrupt record ───────────────────────────────────────────


class _FakeRedis:
    """Minimal Redis surface without ``eval`` (exercises the non-Lua path)."""

    def __init__(self):
        self.kv = {}
        self.z = {}

    async def get(self, key):
        return self.kv.get(key)

    async def set(self, key, value, ex=None):
        self.kv[key] = value

    async def delete(self, key):
        self.kv.pop(key, None)

    async def zadd(self, key, mapping):
        self.z.setdefault(key, {}).update(mapping)

    async def zrem(self, key, member):
        self.z.get(key, {}).pop(member, None)

    async def expire(self, key, _ttl):
        return True

    async def zrangebyscore(self, key, low, high, start=0, num=100):
        lo = float("-inf") if low == "-inf" else float(low)
        hi = float("inf") if high == "+inf" else float(high)
        items = sorted(self.z.get(key, {}).items(), key=lambda kv: kv[1])
        return [member for member, score in items if lo <= score <= hi][start:start + num]

    async def zscore(self, key, member):
        return self.z.get(key, {}).get(member)


@pytest.mark.asyncio
async def test_truncated_pending_record_is_quarantined_not_reclaimed_forever():
    from core.lesson.sd_pack_pending_store import RedisLessonSdPendingStore

    now = {"value": 1000.0}
    redis = _FakeRedis()
    store = RedisLessonSdPendingStore(
        redis, namespace="t", clock=lambda: now["value"]
    )
    await store.mark("dev-1", ["lesson/v1-a"])
    key = "t:lesson-sd-pending:dev-1"
    redis.kv[key] = '{"cacheKeys": ["lesson/v1-a"], "attemp'  # crash mid-write

    now["value"] = 5000.0
    assert await store.load("dev-1") is None
    # The record is gone from every index, so the worker cannot re-lease it on
    # every tick for the whole 30-day TTL while never making progress.
    assert key not in redis.kv
    assert redis.z.get("t:lesson-sd-pending:due", {}) == {}
    assert redis.z.get("t:lesson-sd-pending:created", {}) == {}
    assert await store.claim_due(limit=10) == []

    # New work for the same device still queues normally.
    await store.mark("dev-1", ["lesson/v2-b"])
    reloaded = await store.load("dev-1")
    assert reloaded is not None and reloaded["cacheKeys"] == ["lesson/v2-b"]


@pytest.mark.asyncio
async def test_retry_worker_drains_a_device_whose_record_was_quarantined():
    from core.lesson.sd_pack_retry_worker import LessonSdOnlineIndex, LessonSdRetryWorker

    class _Store:
        def __init__(self):
            self.claims = 0

        async def claim_due(self, *, limit):
            self.claims += 1
            return ["dev-1"]

        async def load(self, _device_id):
            return None

    store = _Store()
    worker = LessonSdRetryWorker(store, LessonSdOnlineIndex(), interval_sec=0.1)

    assert await worker.run_once() == {"checked": 1, "retried": 0, "skippedOffline": 0}


# ── Evict cancels queued retry work ─────────────────────────────────────────


class _FakeEvictRequest:
    def __init__(self, cache_key, device_id="device-1"):
        self.match_info = {"deviceId": device_id}
        self.headers = {"X-Mint-Secret": "secret"}
        self._body = {"cacheKey": cache_key}

    async def json(self):
        return self._body


def _firmware_evicted(cache_key):
    return {
        "cacheKey": cache_key,
        "status": "evicted",
        "evicted": True,
        "notFound": False,
        "fileCount": 3,
        "reason": "evicted",
    }


@pytest.mark.asyncio
async def test_evict_cancels_queued_retry_for_that_exact_cache_key(monkeypatch):
    from unittest.mock import patch

    from core.api.lesson_sd_evict_handler import LessonSdEvictHandler
    from core.lesson.sd_pack_pending_store import InMemoryLessonSdPendingStore

    monkeypatch.setenv("TBOT_DEVICE_MINT_SECRET", "secret")
    other_key = "lesson-two/v1-" + "c" * 64
    store = InMemoryLessonSdPendingStore()
    await store.mark("device-1", [CACHE_KEY, other_key])

    handler = LessonSdEvictHandler({}, {}, pending_store=store)
    handler._shared._find_connection = AsyncMock(return_value=None)

    with patch(
        "core.api.lesson_sd_evict_handler.evict_exact_cache_key",
        new=AsyncMock(return_value=_firmware_evicted(CACHE_KEY)),
    ):
        response = await handler.handle_post(_FakeEvictRequest(CACHE_KEY))

    assert response.status == 200
    pending = await store.load("device-1")
    # The evicted key must not come back on the next drain; unrelated queued
    # work for the same device is untouched.
    assert pending is not None and pending["cacheKeys"] == [other_key]


@pytest.mark.asyncio
async def test_evict_of_an_absent_pack_keeps_queued_delivery_work(monkeypatch):
    from unittest.mock import patch

    from core.api.lesson_sd_evict_handler import LessonSdEvictHandler
    from core.lesson.sd_pack_pending_store import InMemoryLessonSdPendingStore

    monkeypatch.setenv("TBOT_DEVICE_MINT_SECRET", "secret")
    store = InMemoryLessonSdPendingStore()
    await store.mark("device-1", [CACHE_KEY])

    handler = LessonSdEvictHandler({}, {}, pending_store=store)
    handler._shared._find_connection = AsyncMock(return_value=None)
    not_found = {
        "cacheKey": CACHE_KEY,
        "status": "not_found",
        "evicted": False,
        "notFound": True,
        "fileCount": 0,
        "reason": "not_found",
    }

    with patch(
        "core.api.lesson_sd_evict_handler.evict_exact_cache_key",
        new=AsyncMock(return_value=not_found),
    ):
        response = await handler.handle_post(_FakeEvictRequest(CACHE_KEY))

    assert response.status == 200
    pending = await store.load("device-1")
    assert pending is not None and pending["cacheKeys"] == [CACHE_KEY]
