"""S7 / CP-5 — ESP asset preload cache: sha256 gate, binary READY rule, profile
reject, the exhaustive realtime guard, and restart re-attest.

These exercise ``core.lesson.asset_cache`` in isolation with an injected fake
``httpx`` client + a controllable ``busy_check`` (no real network, no wall clock).
"""

import asyncio
import hashlib
import os
import shutil
import tempfile
import unittest

from core.lesson.asset_cache import AssetCache, DOWNLOADING, EVICTED, FAILED, READY
from core.lesson.errors import AssetChecksumMismatch, AssetProfileUnavailable


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class _FakeStreamResponse:
    def __init__(self, chunks, status=200, pre_chunk=None):
        self._chunks = chunks
        self.status_code = status
        self._pre_chunk = pre_chunk

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    async def aiter_bytes(self, chunk_size):
        for chunk in self._chunks:
            if self._pre_chunk is not None:
                await self._pre_chunk()
            yield chunk


class _FakeClient:
    """Maps absolute url -> list[bytes]. ``pre_chunk`` lets a test observe pausing."""

    def __init__(self, content_by_url, pre_chunk=None, status=200):
        self.content_by_url = content_by_url
        self.pre_chunk = pre_chunk
        self.status = status
        self.requested = []

    def stream(self, method, url):
        self.requested.append(url)
        return _FakeStreamResponse(
            self.content_by_url.get(url, []), status=self.status, pre_chunk=self.pre_chunk
        )

    async def aclose(self):
        return None


POSTER = b"poster-bytes-xyz"
BARN = b"barn-bytes-abc"
BASE = "http://assets.test"


def _critical_assets():
    return [
        {
            "key": "backgroundScene.poster",
            "path": "barn-round-field-poster.jpg",
            "sha256": _sha(POSTER),
            "critical": True,
            "layer": "backgroundScene",
            "role": "poster",
            "mediaType": "image/jpeg",
        },
        {
            "key": "teachingObject.barn",
            "path": "barn.png",
            "sha256": _sha(BARN),
            "critical": True,
            "layer": "teachingObject",
            "role": "primarySubject",
            "mediaType": "image/png",
        },
    ]


def _client_for(assets, *, corrupt=None, **kw):
    content = {f"{BASE}/{a['path']}": (corrupt if corrupt and a["key"] == corrupt[0] else None) for a in assets}
    mapping = {}
    for a in assets:
        url = f"{BASE}/{a['path']}"
        if corrupt and a["key"] == corrupt[0]:
            mapping[url] = [corrupt[1]]
        elif a["key"] == "backgroundScene.poster":
            mapping[url] = [POSTER]
        else:
            mapping[url] = [BARN]
    return _FakeClient(mapping, **kw)


class AssetCachePreloadTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="lesson-cache-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _cache(self, assets, **kw):
        return AssetCache(
            assets=assets,
            profile="espTft",
            asset_origin_base=BASE,
            cache_root=self.tmp,
            lesson_key="w01-d01-barn-say-it",
            preload_timeout_sec=kw.pop("preload_timeout_sec", 5.0),
            **kw,
        )

    async def test_preload_lifecycle_and_sha256_gate(self):
        assets = _critical_assets()
        cache = self._cache(assets, client=_client_for(assets))

        # Pre-state: every critical asset PENDING, not ready.
        self.assertFalse(cache.is_ready())

        ready = await cache.preload()

        self.assertTrue(ready)
        self.assertTrue(cache.is_ready())
        for a in cache.assets:
            self.assertEqual(a.state, READY)
            self.assertTrue(a.checksum_ok)
        # Bytes actually landed on disk and match the sha256.
        for a in cache.assets:
            path = cache._final_path(a)
            self.assertTrue(os.path.exists(path))
            with open(path, "rb") as fh:
                self.assertEqual(_sha(fh.read()), a.sha256)

    async def test_public_url_for_source_only_returns_verified_cached_asset(self):
        assets = _critical_assets()
        cache = self._cache(
            assets,
            client=_client_for(assets),
            public_base_url="https://ota.test",
        )

        self.assertIsNone(cache.public_url_for_source("barn-round-field-poster.jpg"))

        await cache.preload()

        url = cache.public_url_for_source("barn-round-field-poster.jpg")
        self.assertIsNotNone(url)
        self.assertTrue(url.startswith("https://ota.test/tbot/lesson-assets/"))
        self.assertTrue(url.endswith("/backgroundScene.poster"))
        self.assertEqual(
            cache.public_url_for_source("http://assets.test/barn-round-field-poster.jpg"),
            url,
        )

    async def test_critical_checksum_mismatch_blocks_ready_and_raises(self):
        assets = _critical_assets()
        client = _client_for(assets, corrupt=("teachingObject.barn", b"WRONG-BYTES"))
        cache = self._cache(assets, client=client)

        with self.assertRaises(AssetChecksumMismatch) as ctx:
            await cache.preload()

        self.assertEqual(ctx.exception.code, "ASSET_CHECKSUM_MISMATCH")
        self.assertEqual(ctx.exception.context.get("assetKey"), "teachingObject.barn")
        self.assertFalse(ctx.exception.retryable)
        # Binary rule: a single critical mismatch keeps READY false (no best-effort).
        self.assertFalse(cache.is_ready())
        bad = cache._by_key["teachingObject.barn"]
        self.assertEqual(bad.state, FAILED)
        self.assertFalse(bad.checksum_ok)
        # The unverified bytes are discarded — never served.
        self.assertFalse(os.path.exists(cache._final_path(bad)))

    async def test_ready_rule_is_binary_unverified_bytes_are_failed_not_ready(self):
        assets = _critical_assets()
        # The poster URL has no content registered -> download yields nothing ->
        # its sha256 will not match -> FAILED. One critical not-READY => not ready.
        client = _client_for(assets, corrupt=("backgroundScene.poster", b""))
        cache = self._cache(assets, client=client)
        with self.assertRaises(AssetChecksumMismatch):
            await cache.preload()
        self.assertFalse(cache.is_ready())

    async def test_synthesized_preload_status_matches_fixture_shape(self):
        assets = _critical_assets()
        cache = self._cache(assets, client=_client_for(assets))
        await cache.preload()
        status = cache.synthesize_preload_status(assignment_version=1)
        self.assertEqual(status["assignmentVersion"], 1)
        self.assertTrue(status["ready"])
        self.assertEqual(status["criticalTotal"], 2)
        self.assertEqual(status["criticalReady"], 2)
        keys = {a["key"] for a in status["assets"]}
        self.assertEqual(keys, {"backgroundScene.poster", "teachingObject.barn"})
        for a in status["assets"]:
            self.assertEqual(set(a.keys()), {"key", "state", "checksumOk"})

    async def test_optional_pose_assets_do_not_gate_ready(self):
        # criticalTotal=2 (0013 §M): the 4 pose PNGs are class:optional.
        assets = _critical_assets() + [
            {
                "key": "robotOverlay.teach",
                "path": "bright-teach.png",
                "sha256": "00" * 32,  # wrong on purpose
                "critical": False,
                "layer": "robotOverlay",
                "role": "pose",
                "mediaType": "image/png",
            }
        ]
        client = _client_for(assets, corrupt=("robotOverlay.teach", b"whatever"))
        cache = self._cache(assets, client=client)
        ready = await cache.preload()
        # Optional asset failed its checksum, but the 2 criticals are verified.
        self.assertTrue(ready)
        self.assertTrue(cache.is_ready())
        self.assertEqual(len(cache.critical_assets), 2)

    async def test_esptft_forced_video_background_is_rejected_before_download(self):
        assets = [
            {
                "key": "backgroundScene.video",
                "path": "barn.mp4",
                "sha256": "ab" * 32,
                "critical": True,
                "layer": "backgroundScene",
                "role": "video",
                "mediaType": "video/mp4",
            }
        ]
        client = _client_for(_critical_assets())  # would serve if reached
        cache = self._cache(assets, client=client)

        with self.assertRaises(AssetProfileUnavailable) as ctx:
            await cache.preload()
        self.assertEqual(ctx.exception.code, "ASSET_PROFILE_UNAVAILABLE")
        # NEVER entered PRELOADING / never fetched unplayable bytes.
        self.assertEqual(client.requested, [])

    async def test_download_pauses_while_realtime_busy_and_resumes(self):
        assets = _critical_assets()
        busy = {"value": True}
        client = _client_for(assets)
        cache = self._cache(
            assets,
            client=client,
            busy_check=lambda: busy["value"],
            poll_interval=0.01,
        )

        task = asyncio.create_task(cache.preload())
        # Let the loop reach the realtime-guard pause point.
        for _ in range(5):
            await asyncio.sleep(0.01)
        self.assertFalse(task.done())
        self.assertFalse(cache.is_ready())
        # While busy, no critical asset has reached READY.
        self.assertTrue(all(a.state in (DOWNLOADING,) for a in cache.assets))

        # Boundary: realtime turn ends -> downloads resume.
        busy["value"] = False
        ready = await asyncio.wait_for(task, timeout=2.0)
        self.assertTrue(ready)
        self.assertTrue(cache.is_ready())

    async def test_preload_timeout_when_busy_never_releases(self):
        assets = _critical_assets()
        cache = self._cache(
            assets,
            client=_client_for(assets),
            busy_check=lambda: True,  # voice never yields
            preload_timeout_sec=0.05,
            poll_interval=0.01,
        )
        from core.lesson.errors import PreloadTimeout

        with self.assertRaises(PreloadTimeout) as ctx:
            await cache.preload()
        self.assertTrue(ctx.exception.retryable)

    async def test_restart_reattest_rechecksums_cached_bytes(self):
        assets = _critical_assets()
        cache = self._cache(assets, client=_client_for(assets))
        # Simulate a surviving data/ cache from before the restart.
        os.makedirs(cache.cache_dir, exist_ok=True)
        for a in cache.assets:
            content = POSTER if a.key == "backgroundScene.poster" else BARN
            with open(cache._final_path(a), "wb") as fh:
                fh.write(content)

        ready = await cache.reattest()
        self.assertTrue(ready)
        self.assertTrue(all(a.state == READY and a.checksum_ok for a in cache.assets))

    async def test_restart_reattest_discards_corrupt_cached_bytes(self):
        assets = _critical_assets()
        cache = self._cache(assets, client=_client_for(assets))
        os.makedirs(cache.cache_dir, exist_ok=True)
        good = cache._by_key["backgroundScene.poster"]
        bad = cache._by_key["teachingObject.barn"]
        with open(cache._final_path(good), "wb") as fh:
            fh.write(POSTER)
        with open(cache._final_path(bad), "wb") as fh:
            fh.write(b"CORRUPT")

        ready = await cache.reattest()
        self.assertFalse(ready)  # presence is never trusted; bad bytes => not ready
        self.assertEqual(bad.state, FAILED)
        self.assertFalse(os.path.exists(cache._final_path(bad)))

    # ── P5 version-aware cache key + eviction ───────────────────────────────────

    async def test_distinct_versions_get_disjoint_cache_dirs(self):
        # Two authored versions of the SAME lesson must never collide on disk.
        assets = _critical_assets()
        v1 = self._cache(assets, client=_client_for(assets), lesson_version=1,
                         manifest_checksum="aaaaaaaa11112222")
        v2 = self._cache(assets, client=_client_for(assets), lesson_version=2,
                         manifest_checksum="bbbbbbbb33334444")
        self.assertNotEqual(v1.cache_dir, v2.cache_dir)
        self.assertNotEqual(v1.cache_key, v2.cache_key)

        await v1.preload()
        await v2.preload()
        # Each version's bytes land under its OWN directory; no cross-contamination.
        for a in v1.assets:
            self.assertTrue(os.path.exists(v1._final_path(a)))
            self.assertFalse(v1._final_path(a).startswith(v2.cache_dir))

    async def test_same_version_new_checksum_gets_new_dir(self):
        # A republish that keeps the version but changes bytes (new manifestChecksum)
        # must re-pull into a fresh dir, never re-attest the old checksum's bytes.
        assets = _critical_assets()
        a = self._cache(assets, client=_client_for(assets), lesson_version=2,
                        manifest_checksum="0000000000000000")
        b = self._cache(assets, client=_client_for(assets), lesson_version=2,
                        manifest_checksum="ffffffffffffffff")
        self.assertNotEqual(a.cache_dir, b.cache_dir)

    def test_legacy_no_version_keeps_flat_lesson_dir(self):
        # Back-compat: a slice caller passing neither version nor checksum keeps the
        # old flat <cache_root>/<lesson_key> directory (no migration).
        assets = _critical_assets()
        cache = self._cache(assets, client=_client_for(assets))
        self.assertEqual(
            cache.cache_dir, os.path.join(self.tmp, "w01-d01-barn-say-it")
        )

    async def test_evict_removes_version_dir_and_marks_assets_evicted(self):
        assets = _critical_assets()
        cache = self._cache(assets, client=_client_for(assets), lesson_version=3,
                            manifest_checksum="9b1f7c2a5d3e8f04")
        await cache.preload()
        self.assertTrue(os.path.isdir(cache.cache_dir))
        self.assertTrue(cache.is_ready())

        await cache.evict()

        # The version-scoped dir is gone and every asset is flipped to EVICTED.
        self.assertFalse(os.path.isdir(cache.cache_dir))
        self.assertTrue(all(a.state == EVICTED for a in cache.assets))
        self.assertFalse(cache.is_ready())

    async def test_evict_one_version_does_not_touch_another(self):
        # Disjoint dirs => evicting v1 leaves v2's verified bytes intact.
        assets = _critical_assets()
        v1 = self._cache(assets, client=_client_for(assets), lesson_version=1,
                         manifest_checksum="1111111111111111")
        v2 = self._cache(assets, client=_client_for(assets), lesson_version=2,
                         manifest_checksum="2222222222222222")
        await v1.preload()
        await v2.preload()

        await v1.evict()

        self.assertFalse(os.path.isdir(v1.cache_dir))
        self.assertTrue(os.path.isdir(v2.cache_dir))
        for a in v2.assets:
            self.assertTrue(os.path.exists(v2._final_path(a)))


if __name__ == "__main__":
    unittest.main()
