"""S7 / CP-5 — ESP asset preload cache: sha256 gate, binary READY rule, profile
reject, the exhaustive realtime guard, and restart re-attest.

These exercise ``core.lesson.asset_cache`` in isolation with an injected fake
``httpx`` client + a controllable ``busy_check`` (no real network, no wall clock).
"""

import asyncio
import base64
import hashlib
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from core.lesson import asset_cache as asset_cache_module
from core.lesson.asset_cache import AssetCache, AssetState, DOWNLOADING, EVICTED, FAILED, READY
from core.lesson.errors import AssetChecksumMismatch, AssetProfileUnavailable, PreloadTimeout
from core.lesson.shared_asset_store import SharedAssetStore


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


class _Logger:
    def __init__(self, *, fail_bind=False, fail_level=False):
        self.fail_bind = fail_bind
        self.fail_level = fail_level
        self.messages = []

    def bind(self, **_kwargs):
        if self.fail_bind:
            raise RuntimeError("bind failed")
        return self

    def warning(self, message):
        if self.fail_level:
            raise RuntimeError("warning failed")
        self.messages.append(("warning", message))

    def error(self, message):
        if self.fail_level:
            raise RuntimeError("error failed")
        self.messages.append(("error", message))

    def info(self, message):
        if self.fail_level:
            raise RuntimeError("info failed")
        self.messages.append(("info", message))


class _CloseClient:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.closed = False

    async def aclose(self):
        self.closed = True
        if self.fail:
            raise RuntimeError("close failed")


POSTER = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAYEBQYFBAYGBQYHBwYIChAKCgkJChQODwwQ"
    "FxQYGBcUFhYaHSUfGhsjHBYWICwgIyYnKSopGR8tMC0oMCUoKSj/2wBDAQcHBwoIChMK"
    "ChMoGhYaKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoK"
    "CgoKCj/wAARCAACAAIDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBg"
    "cICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0"
    "KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZn"
    "aGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8"
    "jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAA"
    "AAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcR"
    "MiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RV"
    "VldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tb"
    "a3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAx"
    "EAPwDoKKKK+XPFP//Z"
)
POSTER_REPUBLISHED = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAYEBQYFBAYGBQYHBwYIChAKCgkJChQODwwQ"
    "FxQYGBcUFhYaHSUfGhsjHBYWICwgIyYnKSopGR8tMC0oMCUoKSj/2wBDAQcHBwoIChMK"
    "ChMoGhYaKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoK"
    "CgoKCj/wAARCAACAAIDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBg"
    "cICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0"
    "KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZn"
    "aGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8"
    "jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAA"
    "AAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcR"
    "MiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RV"
    "VldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tb"
    "a3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAx"
    "EAPwDMooor50/Rz//Z"
)
BARN = b"barn-bytes-abc"
BASE = "http://assets.test"
BACKEND_CANONICAL_MANIFEST_PATH = os.path.realpath(
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "..",
        "..",
        "..",
        "tbot-backend",
        "scripts",
        "seed",
        "076_canonical-manifest.espTft.json",
    )
)
FIRMWARE_LESSON_ROOT = os.path.realpath(
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "..",
        "..",
        "TBOT-Firmware",
        "lesson",
    )
)
ASSET_CACHE_SOURCE = Path(__file__).resolve().parents[1] / "core" / "lesson" / "asset_cache.py"


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

def _canonical_backend_assets_for_test():
    if not os.path.exists(BACKEND_CANONICAL_MANIFEST_PATH):
        raise unittest.SkipTest("backend canonical espTft manifest lives in sibling tbot-backend checkout")
    with open(BACKEND_CANONICAL_MANIFEST_PATH) as fh:
        manifest = json.load(fh)
    assets = []
    content_by_url = {}
    for asset in manifest["assets"]:
        asset_path = os.path.join(FIRMWARE_LESSON_ROOT, asset["path"])
        if not os.path.exists(asset_path):
            raise unittest.SkipTest(f"firmware lesson asset missing: {asset_path}")
        with open(asset_path, "rb") as fh:
            content = fh.read()
        if _sha(content) != asset["sha256"]:
            raise AssertionError(f"canonical backend checksum drifted for {asset['id']}")
        url = f"{BASE}/{asset['path']}"
        content_by_url[url] = [content]
        assets.append(
            {
                "key": asset["id"],
                "path": asset["path"],
                "sha256": asset["sha256"],
                "critical": asset["critical"],
                "layer": asset["layer"],
                "role": asset["role"],
                "mediaType": asset["mediaType"],
            }
        )
    return manifest, assets, content_by_url


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

    def test_asset_cache_contract_documents_verifier_gate_not_sole_downloader(self):
        source = ASSET_CACHE_SOURCE.read_text(encoding="utf-8")

        forbidden = (
            "SOLE downloader",
            "Firmware never downloads",
            "firmware never downloads",
            "firmware does not download",
        )
        for phrase in forbidden:
            self.assertNotIn(phrase, source)

        self.assertIn("authoritative sha256 verifier", source)
        self.assertIn("READY gate", source)

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

    async def test_esptft_background_poster_writes_render_safe_derivative(self):
        original = b"\xff\xd8\xfforiginal-camera-jpeg"
        derivative = b"\xff\xd8\xffbaseline-320x240-jpeg"
        assets = [
            {
                "key": "backgroundScene.poster",
                "path": "poster.jpg",
                "sha256": _sha(original),
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
        normalized = []

        def normalizer(content):
            normalized.append(content)
            return derivative

        cache = self._cache(
            assets,
            client=_FakeClient({f"{BASE}/poster.jpg": [original], f"{BASE}/barn.png": [BARN]}),
            image_normalizer=normalizer,
            public_base_url="https://ota.test",
        )

        self.assertTrue(await cache.preload())

        poster = cache._by_key["backgroundScene.poster"]
        self.assertEqual(normalized, [original])
        with open(cache._final_path(poster), "rb") as fh:
            self.assertEqual(fh.read(), original)
        with open(cache._render_safe_path(poster), "rb") as fh:
            self.assertEqual(fh.read(), derivative)
        self.assertIsNotNone(cache.public_url_for_source("poster.jpg"))

    async def test_esptft_sd_pack_uses_render_safe_background_poster(self):
        original = b"\xff\xd8\xfforiginal-camera-jpeg"
        derivative = b"\xff\xd8\xffbaseline-320x240-jpeg"
        assets = [
            {
                "key": "backgroundScene.poster",
                "path": "poster.jpg",
                "sha256": _sha(original),
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
        sd_root = os.path.join(self.tmp, "sd")
        cache = self._cache(
            assets,
            client=_FakeClient({f"{BASE}/poster.jpg": [original], f"{BASE}/barn.png": [BARN]}),
            image_normalizer=lambda _content: derivative,
            asset_pack_mount_root=sd_root,
        )

        self.assertTrue(await cache.preload())

        poster = cache._by_key["backgroundScene.poster"]
        barn = cache._by_key["teachingObject.barn"]
        with open(cache._asset_pack_path(poster), "rb") as fh:
            self.assertEqual(fh.read(), derivative)
        with open(cache._asset_pack_path(barn), "rb") as fh:
            self.assertEqual(fh.read(), BARN)

        pack = cache.asset_pack_manifest(
            assignment_version=1,
            lesson_id="lesson-1",
            lesson_version=1,
            manifest_checksum="checksum-1",
        )
        poster_record = next(a for a in pack["assets"] if a["key"] == "backgroundScene.poster")
        self.assertEqual(poster_record["sha256"], _sha(derivative))
        self.assertEqual(poster_record["sourceSha256"], _sha(original))
        self.assertEqual(poster_record["size"], len(derivative))

    async def test_reattest_recreates_missing_background_poster_derivative(self):
        original = b"\xff\xd8\xfforiginal-camera-jpeg"
        first_derivative = b"\xff\xd8\xfffirst"
        second_derivative = b"\xff\xd8\xffsecond"
        assets = [
            {
                "key": "backgroundScene.poster",
                "path": "poster.jpg",
                "sha256": _sha(original),
                "critical": True,
                "layer": "backgroundScene",
                "role": "poster",
                "mediaType": "image/jpeg",
            }
        ]
        writer = self._cache(
            assets,
            client=_FakeClient({f"{BASE}/poster.jpg": [original]}),
            image_normalizer=lambda _content: first_derivative,
        )
        self.assertTrue(await writer.preload())
        poster = writer._by_key["backgroundScene.poster"]
        os.remove(writer._render_safe_path(poster))

        reattest = self._cache(
            assets,
            client=_FakeClient({}),
            image_normalizer=lambda _content: second_derivative,
        )

        self.assertTrue(await reattest.reattest())

        poster = reattest._by_key["backgroundScene.poster"]
        with open(reattest._render_safe_path(poster), "rb") as fh:
            self.assertEqual(fh.read(), second_derivative)

    async def test_preload_blocks_critical_background_poster_when_derivative_empty(self):
        original = b"\xff\xd8\xfforiginal-camera-jpeg"
        assets = [
            {
                "key": "backgroundScene.poster",
                "path": "poster.jpg",
                "sha256": _sha(original),
                "critical": True,
                "layer": "backgroundScene",
                "role": "poster",
                "mediaType": "image/jpeg",
            }
        ]
        cache = self._cache(
            assets,
            client=_FakeClient({f"{BASE}/poster.jpg": [original]}),
            image_normalizer=lambda _content: b"",
        )

        with self.assertRaises(AssetProfileUnavailable):
            await cache.preload()

        poster = cache._by_key["backgroundScene.poster"]
        self.assertEqual(poster.state, FAILED)
        self.assertEqual(poster.reason, "render_derivative_failed")
        self.assertFalse(os.path.exists(cache._render_safe_path(poster)))

    async def test_preload_marks_noncritical_background_poster_failed_when_derivative_empty(self):
        original = b"\xff\xd8\xffoptional-camera-jpeg"
        assets = [
            {
                "key": "backgroundScene.poster",
                "path": "poster.jpg",
                "sha256": _sha(original),
                "critical": False,
                "layer": "backgroundScene",
                "role": "poster",
                "mediaType": "image/jpeg",
            }
        ]
        cache = self._cache(
            assets,
            client=_FakeClient({f"{BASE}/poster.jpg": [original]}),
            image_normalizer=lambda _content: b"",
        )

        ready = await cache.preload()

        poster = cache._by_key["backgroundScene.poster"]
        self.assertFalse(ready)
        self.assertEqual(poster.state, FAILED)
        self.assertEqual(poster.reason, "render_derivative_failed")

    async def test_reattest_marks_cached_background_poster_failed_when_derivative_crashes(self):
        original = b"\xff\xd8\xfforiginal-camera-jpeg"
        assets = [
            {
                "key": "backgroundScene.poster",
                "path": "poster.jpg",
                "sha256": _sha(original),
                "critical": True,
                "layer": "backgroundScene",
                "role": "poster",
                "mediaType": "image/jpeg",
            }
        ]
        cache = self._cache(
            assets,
            client=_FakeClient({}),
            image_normalizer=lambda _content: (_ for _ in ()).throw(RuntimeError("bad image")),
        )
        os.makedirs(cache.cache_dir, exist_ok=True)
        poster = cache._by_key["backgroundScene.poster"]
        with open(cache._final_path(poster), "wb") as fh:
            fh.write(original)
        with open(cache._render_safe_path(poster), "wb") as fh:
            fh.write(b"stale")

        self.assertFalse(await cache.reattest())

        self.assertEqual(poster.state, FAILED)
        self.assertFalse(poster.checksum_ok)
        self.assertEqual(poster.reason, "render_derivative_failed")
        self.assertFalse(os.path.exists(cache._render_safe_path(poster)))

    def test_asset_state_requires_key_and_keyless_assets_are_logged_and_skipped(self):
        with self.assertRaises(ValueError):
            AssetState({"path": "missing-key.png"})

        logger = _Logger()
        cache = self._cache(
            [
                {"path": "missing-key.png", "critical": True},
                {"layer": "backgroundScene", "critical": False},
                {"role": "pose", "critical": False},
                {"critical": False},
            ],
            logger=logger,
        )

        self.assertEqual(cache.assets, [])
        self.assertFalse(cache.is_ready())
        self.assertEqual(
            [message for _level, message in logger.messages],
            [
                "skipping manifest asset with empty key (missing id/assetId): missing-key.png",
                "skipping manifest asset with empty key (missing id/assetId): backgroundScene",
                "skipping manifest asset with empty key (missing id/assetId): pose",
                "skipping manifest asset with empty key (missing id/assetId): ?",
            ],
        )

    def test_ready_gate_rejects_dropped_empty_and_unreadable_assets(self):
        keyless = self._cache([{"path": "missing-key.png", "critical": True}])
        self.assertFalse(keyless.is_ready())

        empty = self._cache([])
        self.assertFalse(empty.is_ready())

        cache = self._cache(_critical_assets())
        asset = cache.assets[0]
        asset.state = READY
        asset.checksum_ok = True
        asset.key = ""
        self.assertFalse(cache._asset_cache_materialized(asset))

        sd_root = tempfile.mkdtemp(prefix="lesson-sd-")
        self.addCleanup(shutil.rmtree, sd_root, True)
        sd_cache = self._cache(_critical_assets(), asset_pack_mount_root=sd_root)
        sd_asset = sd_cache.assets[0]
        sd_asset.state = READY
        sd_asset.checksum_ok = True
        sd_asset.key = ""
        self.assertFalse(sd_cache._asset_pack_materialized(sd_asset))

    async def test_preload_does_not_report_ready_when_manifest_contains_keyless_asset(self):
        assets = _critical_assets() + [
            {
                "path": "robot-overlay.png",
                "sha256": _sha(BARN),
                "critical": False,
                "layer": "robotOverlay",
                "role": "pose",
                "mediaType": "image/png",
            }
        ]
        cache = self._cache(
            assets,
            client=_FakeClient(
                {
                    f"{BASE}/barn-round-field-poster.jpg": [POSTER],
                    f"{BASE}/barn.png": [BARN],
                }
            ),
        )

        ready = await cache.preload()

        self.assertFalse(ready)
        self.assertFalse(cache.is_ready())
        self.assertEqual(cache.synthesize_preload_status(assignment_version=1)["ready"], False)
        self.assertTrue(all(asset.state == READY and asset.checksum_ok for asset in cache.assets))

    def test_source_lookup_handles_empty_unready_clean_and_suffix_sources(self):
        assets = _critical_assets() + [
            {
                "key": "absolute.asset",
                "path": "nested/absolute.png",
                "url": "https://cdn.test/absolute.png",
                "sha256": _sha(BARN),
                "critical": False,
            }
        ]
        cache = self._cache(assets, public_base_url="https://ota.test")

        self.assertIsNone(cache.public_url_for_source(""))
        self.assertIsNone(cache.local_pack_url_for_source(""))
        self.assertIsNone(cache.public_url_for_source("barn.png?cache=1"))

        barn = cache._by_key["teachingObject.barn"]
        barn.state = READY
        barn.checksum_ok = True
        os.makedirs(cache.cache_dir, exist_ok=True)
        with open(cache._final_path(barn), "wb") as fh:
            fh.write(BARN)
        absolute = cache._by_key["absolute.asset"]

        self.assertIs(cache._asset_for_source("https://cdn.test/absolute.png"), absolute)
        self.assertIs(cache._asset_for_source("https://host/path/barn.png?x=1#frag"), barn)
        self.assertIsNone(cache._asset_for_source("missing.png"))
        self.assertIsNotNone(cache.public_url_for_source("https://host/path/barn.png?x=1#frag"))

    def test_profile_renderable_noops_for_non_esptft_profiles(self):
        cache = AssetCache(
            assets=[
                {
                    "key": "video",
                    "path": "video.mp4",
                    "sha256": "ab" * 32,
                    "critical": True,
                    "layer": "backgroundScene",
                    "role": "video",
                    "mediaType": "video/mp4",
                }
            ],
            profile="browser",
            cache_root=self.tmp,
        )

        cache.assert_profile_renderable()

    async def test_cancel_pending_cancels_running_tasks_and_removes_part_files(self):
        assets = _critical_assets()
        cache = self._cache(assets)
        os.makedirs(cache.cache_dir, exist_ok=True)
        asset = cache.assets[0]
        task = asyncio.create_task(asyncio.sleep(10))
        tmp_path = cache._tmp_path(asset)
        with open(tmp_path, "wb") as fh:
            fh.write(b"partial")

        await cache._cancel_pending([task], [asset])

        self.assertTrue(task.cancelled())
        self.assertFalse(os.path.exists(tmp_path))

    async def test_download_network_failure_marks_asset_failed_without_raising(self):
        assets = _critical_assets()
        logger = _Logger()
        cache = self._cache(assets, client=_client_for(assets, status=500), logger=logger)
        os.makedirs(cache.cache_dir, exist_ok=True)
        asset = cache.assets[0]

        await cache._download_one(asset)

        self.assertEqual(asset.state, FAILED)
        self.assertFalse(asset.checksum_ok)
        self.assertEqual(asset.reason, "network_error")
        self.assertIn("download failed", logger.messages[0][1])

    async def test_download_timeout_during_stream_marks_failed_and_removes_part(self):
        assets = _critical_assets()
        busy = {"value": False}

        async def _pre_chunk():
            busy["value"] = True
            cache._deadline = cache._now() - 1

        cache = self._cache(
            assets,
            client=_client_for(assets, pre_chunk=_pre_chunk),
            busy_check=lambda: busy["value"],
        )
        os.makedirs(cache.cache_dir, exist_ok=True)
        asset = cache.assets[0]
        tmp_path = cache._tmp_path(asset)

        with self.assertRaises(PreloadTimeout):
            await cache._download_one(asset)

        self.assertEqual(asset.state, FAILED)
        self.assertEqual(asset.reason, "preload_timeout")
        self.assertFalse(os.path.exists(tmp_path))

    async def test_download_over_asset_size_limit_fails_early_and_removes_part(self):
        # Large authored courses must not fill RAM/disk while waiting for a checksum
        # mismatch. The ESP cache streams chunks, but also needs a hard per-asset cap.
        oversized = b"x" * 33
        assets = [
            {
                "key": "backgroundScene.poster",
                "path": "too-large.jpg",
                "sha256": _sha(oversized),
                "critical": True,
                "layer": "backgroundScene",
                "role": "poster",
                "mediaType": "image/jpeg",
            }
        ]
        cache = self._cache(
            assets,
            client=_FakeClient({f"{BASE}/too-large.jpg": [b"x" * 16, b"x" * 17]}),
            max_asset_bytes=32,
        )
        os.makedirs(cache.cache_dir, exist_ok=True)
        asset = cache.assets[0]

        await cache._download_one(asset)

        self.assertEqual(asset.state, FAILED)
        self.assertFalse(asset.checksum_ok)
        self.assertEqual(asset.reason, "asset_too_large")
        self.assertFalse(os.path.exists(cache._tmp_path(asset)))
        self.assertFalse(os.path.exists(cache._final_path(asset)))
        self.assertFalse(cache.is_ready())

    async def test_preload_over_total_lesson_asset_limit_blocks_ready_and_removes_part(self):
        # A course can overflow storage using many individually-valid images. Cap the
        # whole lesson pack, not only each asset.
        first = POSTER
        second = b"b" * 20
        assets = [
            {
                "key": "backgroundScene.poster",
                "path": "first.jpg",
                "sha256": _sha(first),
                "critical": True,
                "layer": "backgroundScene",
                "role": "poster",
                "mediaType": "image/jpeg",
            },
            {
                "key": "robotOverlay.teach",
                "path": "second.png",
                "sha256": _sha(second),
                "critical": False,
                "layer": "robotOverlay",
                "role": "pose",
                "mediaType": "image/png",
            },
        ]
        cache = self._cache(
            assets,
            client=_FakeClient({f"{BASE}/first.jpg": [first], f"{BASE}/second.png": [second]}),
            concurrency=1,
            max_total_asset_bytes=len(first) + 1,
        )

        ready = await cache.preload()

        self.assertFalse(ready)
        self.assertFalse(cache.is_ready())
        self.assertEqual(cache._by_key["backgroundScene.poster"].state, READY)
        too_much = cache._by_key["robotOverlay.teach"]
        self.assertEqual(too_much.state, FAILED)
        self.assertEqual(too_much.reason, "lesson_assets_too_large")
        self.assertFalse(os.path.exists(cache._tmp_path(too_much)))
        self.assertFalse(os.path.exists(cache._final_path(too_much)))

    async def test_reattest_over_total_lesson_asset_limit_does_not_report_ready(self):
        # After an operator lowers the SD/cache budget, restart re-attest must not
        # trust an oversized existing cache pack as READY.
        first = POSTER
        second = b"b" * 20
        assets = [
            {"key": "backgroundScene.poster", "path": "first.jpg", "sha256": _sha(first), "critical": True},
            {"key": "robotOverlay.teach", "path": "second.png", "sha256": _sha(second), "critical": False},
        ]
        writer = self._cache(
            assets,
            client=_FakeClient({f"{BASE}/first.jpg": [first], f"{BASE}/second.png": [second]}),
            max_total_asset_bytes=len(first) + len(second) + 1,
        )
        self.assertTrue(await writer.preload())

        reattest = self._cache(
            assets,
            client=_FakeClient({}),
            max_total_asset_bytes=len(first) + 1,
        )

        self.assertFalse(await reattest.reattest())
        self.assertFalse(reattest.is_ready())

    async def test_wait_while_busy_times_out_after_idle_boundary_when_deadline_expired(self):
        cache = self._cache(_critical_assets(), busy_check=lambda: False)
        cache._deadline = cache._now() - 1

        with self.assertRaises(PreloadTimeout):
            await cache._wait_while_busy()

    def test_resolve_url_falls_back_to_absolute_url_and_path(self):
        cache = self._cache([])
        no_origin = AssetCache(assets=[], profile="espTft", cache_root=self.tmp)
        with_url = AssetState({"key": "url", "url": "https://cdn.test/a.png"})
        path_only = AssetState({"key": "path", "path": "local.png"})

        self.assertEqual(cache._resolve_url(with_url), "https://cdn.test/a.png")
        self.assertEqual(no_origin._resolve_url(path_only), "local.png")

    def test_path_guards_reject_empty_keys_and_mount_escape(self):
        cache = self._cache(_critical_assets(), asset_pack_mount_root=self.tmp)
        asset = cache.assets[0]
        asset.key = ""

        with self.assertRaises(ValueError):
            cache._final_path(asset)
        with self.assertRaises(ValueError):
            cache._asset_pack_path(asset)

        cache.cache_key = "../escape"
        with self.assertRaises(ValueError):
            cache._asset_pack_dir()

    def test_asset_pack_path_rejects_dotdot_asset_key_escape(self):
        cache = self._cache(
            [
                {
                    "key": "..",
                    "path": "escape.png",
                    "sha256": _sha(BARN),
                    "critical": True,
                }
            ],
            asset_pack_mount_root=self.tmp,
        )

        with self.assertRaises(ValueError):
            cache._asset_pack_path(cache.assets[0])

    def test_materialize_skips_copy_when_cache_and_pack_paths_match(self):
        assets = _critical_assets()
        cache = self._cache(assets, asset_pack_mount_root=self.tmp)
        asset = cache.assets[0]
        os.makedirs(cache.cache_dir, exist_ok=True)
        with open(cache._final_path(asset), "wb") as fh:
            fh.write(POSTER)

        cache._materialize_asset_pack_file(asset)

        self.assertFalse(os.path.exists(f"{cache._final_path(asset)}.part"))

    async def test_ensure_client_creates_dedicated_client_and_close_swallows_errors(self):
        created = []

        class _FakeHttpx:
            class Timeout:
                def __init__(self, value):
                    self.value = value

            class Limits:
                def __init__(self, **kwargs):
                    self.kwargs = kwargs

            @staticmethod
            def AsyncClient(**kwargs):
                client = _CloseClient(fail=True)
                client.kwargs = kwargs
                created.append(client)
                return client

        cache = self._cache([])

        with mock.patch.object(asset_cache_module, "httpx", _FakeHttpx):
            await cache._ensure_client()
            await cache._maybe_close_client(force=True)

        self.assertEqual(created[0].kwargs["headers"]["User-Agent"].split()[0], "TbotLessonPreload/1.0")
        self.assertTrue(created[0].closed)
        self.assertIsNone(cache._client)
        self.assertFalse(cache._owns_client)

    def test_safe_remove_and_log_swallow_failures(self):
        cache = self._cache([], logger=_Logger())
        target = os.path.join(self.tmp, "remove-me")
        with open(target, "wb") as fh:
            fh.write(b"x")

        with mock.patch.object(asset_cache_module.os, "remove", side_effect=OSError("busy")):
            cache._safe_remove(target)

        cache._log("info", "ready")
        self.assertEqual(cache._logger.messages, [("info", "ready")])

        AssetCache(assets=[], profile="espTft", cache_root=self.tmp, logger=_Logger(fail_bind=True))._log(
            "warning", "ignored"
        )
        AssetCache(assets=[], profile="espTft", cache_root=self.tmp, logger=_Logger(fail_level=True))._log(
            "warning", "ignored"
        )

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

    async def test_asset_pack_manifest_targets_versioned_sd_paths(self):
        assets = _critical_assets()
        cache = self._cache(
            assets,
            client=_client_for(assets),
            lesson_version=3,
            manifest_checksum="abcdef1234567890",
            asset_pack_local_root="sd://sdcard/tbot/lesson-assets",
        )

        await cache.preload()
        pack = cache.asset_pack_manifest(
            assignment_version=7,
            lesson_id="w01-d01-barn-say-it",
            lesson_version=3,
            manifest_checksum="abcdef1234567890",
        )

        self.assertTrue(pack["ready"])
        self.assertEqual(pack["assignmentVersion"], 7)
        self.assertEqual(pack["lessonVersion"], 3)
        self.assertEqual(pack["cacheKey"], "w01-d01-barn-say-it/v3-abcdef1234567890")
        self.assertEqual(
            pack["localRoot"],
            "sd://sdcard/tbot/lesson-assets/w01-d01-barn-say-it/v3-abcdef1234567890",
        )
        by_key = {asset["key"]: asset for asset in pack["assets"]}
        poster = by_key["backgroundScene.poster"]
        poster_asset = cache._by_key["backgroundScene.poster"]
        with open(cache._render_safe_path(poster_asset), "rb") as fh:
            poster_pack_bytes = fh.read()
        self.assertEqual(poster["size"], len(poster_pack_bytes))
        self.assertEqual(poster["sha256"], _sha(poster_pack_bytes))
        self.assertEqual(poster["sourceSha256"], _sha(POSTER))
        self.assertEqual(poster["state"], READY)
        self.assertTrue(poster["checksumOk"])
        self.assertEqual(
            poster["localPath"],
            "sd://sdcard/tbot/lesson-assets/w01-d01-barn-say-it/v3-abcdef1234567890/backgroundScene.poster",
        )
        self.assertEqual(
            cache.local_pack_url_for_source("barn-round-field-poster.jpg"),
            poster["localPath"],
        )

    async def test_same_lesson_version_republish_checksum_downloads_to_new_sd_cache_key(self):
        old_poster = POSTER
        new_poster = POSTER_REPUBLISHED
        old_assets = [
            {
                "key": "backgroundScene.poster",
                "path": "barn-round-field-poster.jpg",
                "sha256": _sha(old_poster),
                "critical": True,
                "layer": "backgroundScene",
                "role": "poster",
                "mediaType": "image/jpeg",
            }
        ]
        new_assets = [{**old_assets[0], "sha256": _sha(new_poster)}]
        sd_mount = tempfile.mkdtemp(prefix="lesson-sd-")
        self.addAsyncCleanup(asyncio.to_thread, shutil.rmtree, sd_mount, True)

        old_cache = self._cache(
            old_assets,
            client=_FakeClient({f"{BASE}/barn-round-field-poster.jpg": [old_poster]}),
            lesson_version=3,
            manifest_checksum="oldchecksum",
            asset_pack_local_root="sd://sdcard/tbot/lesson-assets",
            asset_pack_mount_root=sd_mount,
        )
        new_cache = self._cache(
            new_assets,
            client=_FakeClient({f"{BASE}/barn-round-field-poster.jpg": [new_poster]}),
            lesson_version=3,
            manifest_checksum="newchecksum",
            asset_pack_local_root="sd://sdcard/tbot/lesson-assets",
            asset_pack_mount_root=sd_mount,
        )

        await old_cache.preload()
        await new_cache.preload()

        self.assertEqual(old_cache.cache_key, "w01-d01-barn-say-it/v3-oldchecksum")
        self.assertEqual(new_cache.cache_key, "w01-d01-barn-say-it/v3-newchecksum")
        self.assertNotEqual(old_cache.cache_key, new_cache.cache_key)
        old_pack_path = os.path.join(sd_mount, old_cache.cache_key, "backgroundScene.poster")
        new_pack_path = os.path.join(sd_mount, new_cache.cache_key, "backgroundScene.poster")
        self.assertTrue(os.path.exists(old_pack_path), old_pack_path)
        self.assertTrue(os.path.exists(new_pack_path), new_pack_path)
        with open(old_pack_path, "rb") as fh:
            old_pack_bytes = fh.read()
        with open(new_pack_path, "rb") as fh:
            new_pack_bytes = fh.read()
        with open(old_cache._render_safe_path(old_cache._by_key["backgroundScene.poster"]), "rb") as fh:
            self.assertEqual(old_pack_bytes, fh.read())
        with open(new_cache._render_safe_path(new_cache._by_key["backgroundScene.poster"]), "rb") as fh:
            self.assertEqual(new_pack_bytes, fh.read())
        self.assertEqual(
            new_cache.local_pack_url_for_source("barn-round-field-poster.jpg"),
            "sd://sdcard/tbot/lesson-assets/w01-d01-barn-say-it/v3-newchecksum/backgroundScene.poster",
        )

    async def test_republish_evicts_prior_version_sd_pack_reclaims_disk_version_isolated(self):
        # G11 (large/multi-version disk safety): two lesson versions materialized to the
        # SAME SD mount accumulate two DISJOINT version dirs. republish-on-connect
        # (runtime.py P5) evict()s the stale version, reclaiming its bytes from BOTH the
        # primary cache and the SD pack, while the live version's disjoint dir is never
        # touched. This keeps cumulative disk bounded across republishes instead of
        # growing unboundedly, and proves eviction is version-isolated.
        old_poster = POSTER
        new_poster = POSTER_REPUBLISHED
        old_assets = [
            {
                "key": "backgroundScene.poster",
                "path": "barn-round-field-poster.jpg",
                "sha256": _sha(old_poster),
                "critical": True,
                "layer": "backgroundScene",
                "role": "poster",
                "mediaType": "image/jpeg",
            }
        ]
        new_assets = [{**old_assets[0], "sha256": _sha(new_poster)}]
        sd_mount = tempfile.mkdtemp(prefix="lesson-sd-")
        self.addAsyncCleanup(asyncio.to_thread, shutil.rmtree, sd_mount, True)

        def _mk(assets, poster_bytes, checksum):
            return self._cache(
                assets,
                client=_FakeClient({f"{BASE}/barn-round-field-poster.jpg": [poster_bytes]}),
                lesson_version=3,
                manifest_checksum=checksum,
                asset_pack_local_root="sd://sdcard/tbot/lesson-assets",
                asset_pack_mount_root=sd_mount,
            )

        old_cache = _mk(old_assets, old_poster, "oldchecksum")
        new_cache = _mk(new_assets, new_poster, "newchecksum")
        await old_cache.preload()
        await new_cache.preload()

        old_pack_dir = os.path.join(sd_mount, old_cache.cache_key)
        new_pack_dir = os.path.join(sd_mount, new_cache.cache_key)
        # Both versions coexist on disk before eviction -> cumulative pressure.
        self.assertNotEqual(old_cache.cache_key, new_cache.cache_key)
        self.assertTrue(os.path.isdir(old_pack_dir), old_pack_dir)
        self.assertTrue(os.path.isdir(new_pack_dir), new_pack_dir)
        self.assertTrue(os.path.isdir(old_cache.cache_dir), old_cache.cache_dir)

        # republish-on-connect tears down the stale version only.
        await old_cache.evict()

        # Stale version's bytes reclaimed from BOTH the SD pack and the primary cache.
        self.assertFalse(os.path.exists(old_pack_dir), old_pack_dir)
        self.assertFalse(os.path.exists(old_cache.cache_dir), old_cache.cache_dir)
        self.assertEqual(old_cache.assets[0].state, EVICTED)
        self.assertFalse(old_cache.assets[0].checksum_ok)
        # The live (disjoint) version is untouched: eviction is version-isolated.
        self.assertTrue(os.path.isdir(new_pack_dir), new_pack_dir)
        self.assertTrue(os.path.isdir(new_cache.cache_dir), new_cache.cache_dir)
        self.assertEqual(new_cache.assets[0].state, READY)
        self.assertTrue(new_cache.is_ready())

    async def test_canonical_assetPack_checksum_parity_backend_esp_firmware(self):
        # Cross-repo checksum parity in ONE test (the prior gap: no single assertion tied
        # the manifest end-to-end across the three repos):
        #   1. backend canonical manifest per-asset sha256 == the firmware's actual asset
        #      file bytes (_canonical_backend_assets_for_test reads both and raises on
        #      drift; SkipTest if a sibling checkout is absent).
        #   2. the ESP SD cache key EMBEDS the full manifest checksum, so the firmware's
        #      documented `strstr(cache_key, manifest_checksum)` prepare-ack guard
        #      (TBOT-Firmware lesson_handler) is satisfiable against an ESP-produced key.
        manifest, assets, content_by_url = _canonical_backend_assets_for_test()

        self.assertEqual(len(assets), len(manifest["assets"]))
        for asset in assets:
            self.assertEqual(len(asset["sha256"]), 64, asset["key"])

        manifest_checksum = (
            "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6"
        )
        cache = self._cache(
            assets,
            client=_FakeClient(content_by_url),
            lesson_version=int(manifest["lessonVersion"]),
            manifest_checksum=manifest_checksum,
            asset_pack_local_root="sd://sdcard/tbot/lesson-assets",
        )

        # Firmware admission contract: the full checksum is a substring of the cache key.
        self.assertIn(manifest_checksum, cache.cache_key)
        await cache.preload()
        pack = cache.asset_pack_manifest(
            assignment_version=1,
            lesson_id=manifest["lessonId"],
            lesson_version=int(manifest["lessonVersion"]),
            manifest_checksum=manifest_checksum,
        )
        self.assertIn(manifest_checksum, pack["cacheKey"])
        self.assertTrue(pack["ready"])

    async def test_preload_materializes_verified_asset_pack_to_shared_sd_mount(self):
        assets = _critical_assets()
        sd_mount = tempfile.mkdtemp(prefix="lesson-sd-")
        self.addAsyncCleanup(asyncio.to_thread, shutil.rmtree, sd_mount, True)
        cache = self._cache(
            assets,
            client=_client_for(assets),
            lesson_version=3,
            manifest_checksum="abcdef1234567890",
            asset_pack_local_root="sd://sdcard/tbot/lesson-assets",
            asset_pack_mount_root=sd_mount,
        )

        await cache.preload()
        pack = cache.asset_pack_manifest(
            assignment_version=7,
            lesson_id="w01-d01-barn-say-it",
            lesson_version=3,
            manifest_checksum="abcdef1234567890",
        )

        by_key = {asset["key"]: asset for asset in pack["assets"]}
        for asset in cache.assets:
            materialized = os.path.join(sd_mount, cache.cache_key, asset.key)
            self.assertTrue(os.path.exists(materialized), materialized)
            expected_path = cache._asset_pack_source_path(asset)
            with open(materialized, "rb") as fh:
                materialized_sha = _sha(fh.read())
            with open(expected_path, "rb") as fh:
                expected_sha = _sha(fh.read())
            self.assertEqual(materialized_sha, expected_sha)
            self.assertEqual(
                by_key[asset.key]["localPath"],
                f"sd://sdcard/tbot/lesson-assets/{cache.cache_key}/{asset.key}",
            )

    async def test_preload_hydrates_exact_ready_rich_pack_without_origin_request(self):
        source_contents = {
            "backgroundScene.poster": b"source-jpeg",
            "teachingObject.barn": b"barn-png",
            "robotOverlay.thinking": b"thinking-png",
        }
        pack_contents = {
            **source_contents,
            "backgroundScene.poster": b"render-safe-jpeg",
        }
        assets = [
            {
                "key": key,
                "path": f"{key}.{'jpg' if key == 'backgroundScene.poster' else 'png'}",
                "sha256": _sha(content),
                "critical": key != "robotOverlay.thinking",
                "layer": key.split(".", 1)[0],
                "role": (
                    "poster"
                    if key == "backgroundScene.poster"
                    else "pose" if key == "robotOverlay.thinking" else "image"
                ),
                "mediaType": (
                    "image/jpeg" if key == "backgroundScene.poster" else "image/png"
                ),
            }
            for key, content in source_contents.items()
        ]
        checksum = "0123456789abcdef" * 4
        sd_mount = tempfile.mkdtemp(prefix="lesson-sd-")
        self.addAsyncCleanup(asyncio.to_thread, shutil.rmtree, sd_mount, True)
        store = SharedAssetStore(os.path.dirname(sd_mount), pack_root=sd_mount)
        cache_key = f"w01-d01-barn-say-it/v3-{checksum}"
        for key, content in pack_contents.items():
            store.put_bytes(content, _sha(content))
        store.commit_pack(
            cache_key,
            {key: _sha(content) for key, content in pack_contents.items()},
            manifest={
                "lessonId": "w01-d01-barn-say-it",
                "lessonVersion": 3,
                "profile": "espTft",
                "manifestChecksum": checksum,
                "cacheKey": cache_key,
                "ready": True,
                "assets": [
                    {
                        "key": key,
                        "sha256": _sha(content),
                        "size": len(content),
                        "mediaType": (
                            "image/jpeg" if key == "backgroundScene.poster" else "image/png"
                        ),
                        "critical": key != "robotOverlay.thinking",
                        "onlineUrl": f"{BASE}/{key}.png",
                        "sdPath": f"/sdcard/tbot/lesson-assets/{cache_key}/{key}",
                        **(
                            {"sourceSha256": _sha(source_contents[key])}
                            if key == "backgroundScene.poster"
                            else {}
                        ),
                    }
                    for key, content in pack_contents.items()
                ],
            },
        )
        failing_origin = _FakeClient({}, status=500)

        def fail_normalizer(_content):
            raise AssertionError("pack-only derivative must not be normalized as source")

        cache = self._cache(
            assets,
            client=failing_origin,
            public_base_url="https://ota.test",
            lesson_version=3,
            manifest_checksum=checksum,
            asset_pack_mount_root=sd_mount,
            shared_asset_store=store,
            image_normalizer=fail_normalizer,
        )

        ready = await cache.preload()

        self.assertTrue(ready)
        self.assertEqual(failing_origin.requested, [])
        self.assertEqual(
            {asset.key for asset in cache.assets if asset.state == READY},
            set(source_contents),
        )
        poster = cache._by_key["backgroundScene.poster"]
        self.assertFalse(os.path.exists(cache._final_path(poster)))
        self.assertIsNone(cache.public_url_for_source("backgroundScene.poster.jpg"))
        self.assertEqual(
            cache.local_pack_url_for_source("backgroundScene.poster.jpg"),
            f"sd://tbot/lesson-assets/{cache.cache_key}/backgroundScene.poster",
        )
        advertised_pack = cache.asset_pack_manifest(
            assignment_version=7,
            lesson_id="w01-d01-barn-say-it",
            lesson_version=3,
            manifest_checksum=checksum,
        )
        advertised_poster = next(
            asset
            for asset in advertised_pack["assets"]
            if asset["key"] == "backgroundScene.poster"
        )
        self.assertTrue(advertised_pack["ready"])
        self.assertEqual(advertised_poster["sha256"], _sha(pack_contents[poster.key]))
        self.assertEqual(
            advertised_poster["sourceSha256"],
            _sha(source_contents[poster.key]),
        )
        for asset in cache.assets:
            if asset is poster:
                continue
            with open(cache._final_path(asset), "rb") as fh:
                self.assertEqual(fh.read(), source_contents[asset.key])
        poster_pack_path = cache._asset_pack_path(poster)
        with open(poster_pack_path, "rb") as fh:
            self.assertEqual(fh.read(), b"render-safe-jpeg")
        with open(poster_pack_path, "wb") as fh:
            fh.write(b"corrupt-derivative")
        self.assertFalse(cache.is_ready())
        self.assertIsNone(cache.local_pack_url_for_source("backgroundScene.poster.jpg"))

    async def test_preload_rejects_ready_rich_pack_with_mismatched_checksum(self):
        content = b"thinking-png"
        checksum = "0123456789abcdef" * 4
        cache_key = f"w01-d01-barn-say-it/v3-{checksum}"
        assets = [
            {
                "key": "robotOverlay.thinking",
                "path": "thinking.png",
                "sha256": _sha(content),
                "critical": False,
                "layer": "robotOverlay",
                "role": "pose",
                "mediaType": "image/png",
            }
        ]
        sd_mount = tempfile.mkdtemp(prefix="lesson-sd-")
        self.addAsyncCleanup(asyncio.to_thread, shutil.rmtree, sd_mount, True)
        store = SharedAssetStore(os.path.dirname(sd_mount), pack_root=sd_mount)
        digest = _sha(content)
        store.put_bytes(content, digest)
        store.commit_pack(
            cache_key,
            {"robotOverlay.thinking": digest},
            manifest={
                "lessonId": "w01-d01-barn-say-it",
                "lessonVersion": 3,
                "profile": "espTft",
                "manifestChecksum": "f" * 64,
                "cacheKey": cache_key,
                "ready": True,
                "assets": [
                    {
                        "key": "robotOverlay.thinking",
                        "sha256": digest,
                        "size": len(content),
                        "mediaType": "image/png",
                        "critical": False,
                        "onlineUrl": f"{BASE}/thinking.png",
                        "sdPath": f"/sdcard/tbot/lesson-assets/{cache_key}/robotOverlay.thinking",
                    }
                ],
            },
        )
        self.assertTrue(store.is_pack_ready(cache_key))
        origin = _FakeClient({f"{BASE}/thinking.png": [content]})
        cache = self._cache(
            assets,
            client=origin,
            lesson_version=3,
            manifest_checksum=checksum,
            asset_pack_mount_root=sd_mount,
            shared_asset_store=store,
        )

        ready = await cache.preload()

        self.assertTrue(ready)
        self.assertEqual(origin.requested, [f"{BASE}/thinking.png"])

    async def test_missing_materialized_sd_file_is_not_advertised_as_ready(self):
        assets = _critical_assets()
        sd_mount = tempfile.mkdtemp(prefix="lesson-sd-")
        self.addAsyncCleanup(asyncio.to_thread, shutil.rmtree, sd_mount, True)
        cache = self._cache(
            assets,
            client=_client_for(assets),
            lesson_version=3,
            manifest_checksum="abcdef1234567890",
            asset_pack_local_root="sd://sdcard/tbot/lesson-assets",
            asset_pack_mount_root=sd_mount,
        )

        await cache.preload()
        missing = os.path.join(sd_mount, cache.cache_key, "teachingObject.barn")
        os.remove(missing)
        pack = cache.asset_pack_manifest(
            assignment_version=7,
            lesson_id="w01-d01-barn-say-it",
            lesson_version=3,
            manifest_checksum="abcdef1234567890",
        )

        self.assertFalse(cache.is_ready())
        self.assertFalse(pack["ready"])
        self.assertIsNone(cache.local_pack_url_for_source("barn.png"))
        self.assertNotIn("teachingObject.barn", {asset["key"] for asset in pack["assets"]})

    async def test_corrupt_materialized_sd_file_is_not_advertised_as_ready(self):
        assets = _critical_assets()
        sd_mount = tempfile.mkdtemp(prefix="lesson-sd-")
        self.addAsyncCleanup(asyncio.to_thread, shutil.rmtree, sd_mount, True)
        cache = self._cache(
            assets,
            client=_client_for(assets),
            lesson_version=3,
            manifest_checksum="abcdef1234567890",
            asset_pack_local_root="sd://sdcard/tbot/lesson-assets",
            asset_pack_mount_root=sd_mount,
        )

        await cache.preload()
        corrupt = os.path.join(sd_mount, cache.cache_key, "teachingObject.barn")
        with open(corrupt, "wb") as fh:
            fh.write(b"CORRUPT-SD-BYTES")
        pack = cache.asset_pack_manifest(
            assignment_version=7,
            lesson_id="w01-d01-barn-say-it",
            lesson_version=3,
            manifest_checksum="abcdef1234567890",
        )

        self.assertFalse(cache.is_ready())
        self.assertFalse(pack["ready"])
        self.assertIsNone(cache.local_pack_url_for_source("barn.png"))
        self.assertNotIn("teachingObject.barn", {asset["key"] for asset in pack["assets"]})

    async def test_missing_primary_cache_file_is_not_advertised_even_if_sd_copy_exists(self):
        assets = _critical_assets()
        sd_mount = tempfile.mkdtemp(prefix="lesson-sd-")
        self.addAsyncCleanup(asyncio.to_thread, shutil.rmtree, sd_mount, True)
        cache = self._cache(
            assets,
            client=_client_for(assets),
            public_base_url="https://ota.test",
            lesson_version=3,
            manifest_checksum="abcdef1234567890",
            asset_pack_local_root="sd://sdcard/tbot/lesson-assets",
            asset_pack_mount_root=sd_mount,
        )

        await cache.preload()
        barn = cache._by_key["teachingObject.barn"]
        self.assertTrue(os.path.exists(cache._asset_pack_path(barn)))
        os.remove(cache._final_path(barn))
        pack = cache.asset_pack_manifest(
            assignment_version=7,
            lesson_id="w01-d01-barn-say-it",
            lesson_version=3,
            manifest_checksum="abcdef1234567890",
        )

        self.assertFalse(cache.is_ready())
        self.assertFalse(pack["ready"])
        self.assertIsNone(cache.public_url_for_source("barn.png"))
        self.assertIsNone(cache.local_pack_url_for_source("barn.png"))
        self.assertNotIn("teachingObject.barn", {asset["key"] for asset in pack["assets"]})

    async def test_verified_optional_robot_overlay_is_materialized_and_advertised_in_sd_pack(self):
        assets = _critical_assets() + [
            {
                "key": "robotOverlay.teach",
                "path": "bright-teach.png",
                "sha256": _sha(BARN),
                "critical": False,
                "layer": "robotOverlay",
                "role": "pose",
                "mediaType": "image/png",
            }
        ]
        sd_mount = tempfile.mkdtemp(prefix="lesson-sd-")
        self.addAsyncCleanup(asyncio.to_thread, shutil.rmtree, sd_mount, True)
        cache = self._cache(
            assets,
            client=_client_for(assets),
            lesson_version=3,
            manifest_checksum="abcdef1234567890",
            asset_pack_local_root="sd://sdcard/tbot/lesson-assets",
            asset_pack_mount_root=sd_mount,
        )

        ready = await cache.preload()
        pack = cache.asset_pack_manifest(
            assignment_version=7,
            lesson_id="w01-d01-barn-say-it",
            lesson_version=3,
            manifest_checksum="abcdef1234567890",
        )

        self.assertTrue(ready)
        by_key = {asset["key"]: asset for asset in pack["assets"]}
        self.assertEqual(set(by_key), {"backgroundScene.poster", "teachingObject.barn", "robotOverlay.teach"})
        overlay = by_key["robotOverlay.teach"]
        self.assertFalse(overlay["critical"])
        self.assertEqual(overlay["layer"], "robotOverlay")
        self.assertEqual(overlay["localPath"], f"sd://sdcard/tbot/lesson-assets/{cache.cache_key}/robotOverlay.teach")
        self.assertEqual(cache.local_pack_url_for_source("bright-teach.png"), overlay["localPath"])
        materialized = os.path.join(sd_mount, cache.cache_key, "robotOverlay.teach")
        self.assertTrue(os.path.exists(materialized), materialized)

    async def test_canonical_backend_manifest_sd_pack_downloads_and_advertises_every_image_asset(self):
        manifest, assets, content_by_url = _canonical_backend_assets_for_test()
        sd_mount = tempfile.mkdtemp(prefix="lesson-sd-")
        self.addAsyncCleanup(asyncio.to_thread, shutil.rmtree, sd_mount, True)
        manifest_checksum = "canonical-test-checksum"
        cache = self._cache(
            assets,
            client=_FakeClient(content_by_url),
            lesson_version=manifest["lessonVersion"],
            manifest_checksum=manifest_checksum,
            asset_pack_local_root="sd://sdcard/tbot/lesson-assets",
            asset_pack_mount_root=sd_mount,
        )

        ready = await cache.preload()
        pack = cache.asset_pack_manifest(
            assignment_version=7,
            lesson_id=manifest["lessonId"],
            lesson_version=manifest["lessonVersion"],
            manifest_checksum=manifest_checksum,
        )

        self.assertTrue(ready)
        self.assertTrue(pack["ready"])
        self.assertEqual(pack["cacheKey"], f"{manifest['lessonId']}/v{manifest['lessonVersion']}-{manifest_checksum}")
        expected_keys = [asset["id"] for asset in manifest["assets"]]
        by_key = {asset["key"]: asset for asset in pack["assets"]}
        self.assertEqual(list(by_key), expected_keys)
        self.assertEqual(
            set(by_key),
            {
                "backgroundScene.poster",
                "teachingObject.barn",
                "teachingObject.farm",
                "teachingObject.hay",
                "robotOverlay.celebrate",
                "robotOverlay.listening",
                "robotOverlay.teach",
                "robotOverlay.thinking",
            },
        )
        self.assertEqual(
            {asset["key"] for asset in pack["assets"] if asset["critical"]},
            {"backgroundScene.poster", "teachingObject.barn"},
        )
        for expected in manifest["assets"]:
            advertised = by_key[expected["id"]]
            materialized = os.path.join(sd_mount, cache.cache_key, expected["id"])
            self.assertTrue(os.path.exists(materialized), materialized)
            asset = cache._by_key[expected["id"]]
            expected_path = cache._asset_pack_source_path(asset)
            with open(materialized, "rb") as fh:
                materialized_sha = _sha(fh.read())
            with open(expected_path, "rb") as fh:
                expected_sha = _sha(fh.read())
            self.assertEqual(materialized_sha, expected_sha)
            self.assertEqual(advertised["path"], expected["path"])
            self.assertEqual(advertised["sha256"], expected_sha)
            if expected_sha != expected["sha256"]:
                self.assertEqual(advertised["sourceSha256"], expected["sha256"])
            self.assertEqual(advertised["mediaType"], expected["mediaType"])
            self.assertEqual(advertised["layer"], expected["layer"])
            self.assertEqual(advertised["role"], expected["role"])
            self.assertEqual(
                advertised["localPath"],
                f"sd://sdcard/tbot/lesson-assets/{cache.cache_key}/{expected['id']}",
            )
            self.assertEqual(cache.local_pack_url_for_source(os.path.basename(expected["path"])), advertised["localPath"])

    async def test_evict_removes_only_current_materialized_asset_pack(self):
        assets = _critical_assets()
        sd_mount = tempfile.mkdtemp(prefix="lesson-sd-")
        self.addAsyncCleanup(asyncio.to_thread, shutil.rmtree, sd_mount, True)
        v3 = self._cache(
            assets,
            client=_client_for(assets),
            lesson_version=3,
            manifest_checksum="3333333300000000",
            asset_pack_mount_root=sd_mount,
        )
        v4 = self._cache(
            assets,
            client=_client_for(assets),
            lesson_version=4,
            manifest_checksum="4444444400000000",
            asset_pack_mount_root=sd_mount,
        )

        await v3.preload()
        await v4.preload()
        v3_pack_dir = os.path.join(sd_mount, v3.cache_key)
        v4_pack_dir = os.path.join(sd_mount, v4.cache_key)
        self.assertTrue(os.path.isdir(v3_pack_dir))
        self.assertTrue(os.path.isdir(v4_pack_dir))

        await v3.evict()

        self.assertFalse(os.path.isdir(v3_pack_dir))
        self.assertTrue(os.path.isdir(v4_pack_dir))

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
            self.assertEqual(
                set(a.keys()),
                {"key", "assetId", "state", "checksumOk", "critical"},
            )
            self.assertEqual(a["assetId"], a["key"])
            self.assertIs(a["critical"], True)

    async def test_noncritical_pose_assets_still_gate_overall_ready(self):
        # criticalTotal remains 2 for telemetry, but all image assets must verify
        # before the ESP can claim the SD/cache pack is ready for a three-layer step.
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
        self.assertFalse(ready)
        self.assertFalse(cache.is_ready())
        self.assertEqual(len(cache.critical_assets), 2)

    async def test_noncritical_pose_image_mismatch_blocks_sd_ready_for_all_layer_images(self):
        assets = _critical_assets() + [
            {
                "key": "robotOverlay.teach",
                "path": "bright-teach.png",
                "sha256": "00" * 32,
                "critical": False,
                "layer": "robotOverlay",
                "role": "pose",
                "mediaType": "image/png",
            }
        ]
        cache = self._cache(
            assets,
            client=_client_for(assets, corrupt=("robotOverlay.teach", b"bad-overlay")),
            lesson_version=3,
            manifest_checksum="abcdef1234567890",
            asset_pack_local_root="sd://sdcard/tbot/lesson-assets",
        )

        ready = await cache.preload()
        pack = cache.asset_pack_manifest(
            assignment_version=7,
            lesson_id="w01-d01-barn-say-it",
            lesson_version=3,
            manifest_checksum="abcdef1234567890",
        )

        self.assertFalse(ready)
        self.assertFalse(cache.is_ready())
        self.assertFalse(pack["ready"])
        self.assertIsNone(cache.local_pack_url_for_source("bright-teach.png"))

    async def test_failed_noncritical_assets_are_not_advertised_as_sd_pack_ready(self):
        assets = _critical_assets() + [
            {
                "key": "robotOverlay.teach",
                "path": "bright-teach.png",
                "sha256": "00" * 32,
                "critical": False,
                "layer": "robotOverlay",
                "role": "pose",
                "mediaType": "image/png",
            }
        ]
        sd_mount = tempfile.mkdtemp(prefix="lesson-sd-")
        self.addAsyncCleanup(asyncio.to_thread, shutil.rmtree, sd_mount, True)
        cache = self._cache(
            assets,
            client=_client_for(assets, corrupt=("robotOverlay.teach", b"bad-optional")),
            lesson_version=3,
            manifest_checksum="abcdef1234567890",
            asset_pack_local_root="sd://sdcard/tbot/lesson-assets",
            asset_pack_mount_root=sd_mount,
        )

        ready = await cache.preload()
        pack = cache.asset_pack_manifest(
            assignment_version=7,
            lesson_id="w01-d01-barn-say-it",
            lesson_version=3,
            manifest_checksum="abcdef1234567890",
        )

        self.assertFalse(ready)
        self.assertFalse(pack["ready"])
        self.assertEqual(
            {asset["key"] for asset in pack["assets"]},
            {"backgroundScene.poster", "teachingObject.barn"},
        )
        self.assertIsNone(cache.local_pack_url_for_source("bright-teach.png"))

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

    async def test_stuck_user_streaming_allows_bounded_progress_only_after_speech_stops(self):
        class StuckUserStreamingConnection:
            def __init__(self):
                self.client_is_speaking = False
                self.client_have_voice = True
                self._lesson_asset_audio_inflight = 0
                self._lesson_asset_last_audio_at = 0.0

            def is_realtime_busy(self):
                # Google Live can remain USER_STREAMING after meaningful speech ends.
                return True

            def _realtime_interaction_state(self):
                return "USER_STREAMING"

        assets = [_critical_assets()[1]]
        conn = StuckUserStreamingConnection()
        client = _client_for(assets)
        cache = self._cache(
            assets,
            client=client,
            busy_check=conn.is_realtime_busy,
            poll_interval=0.01,
        )

        task = asyncio.create_task(cache.preload())
        await asyncio.sleep(0.05)
        self.assertEqual(client.requested, [])
        self.assertFalse(task.done())

        # The persistent interaction state is still busy, but meaningful speech is
        # gone. Foreground lesson preload must make progress within a bounded grace.
        conn.client_have_voice = False
        ready = await asyncio.wait_for(task, timeout=0.5)

        self.assertTrue(ready)
        self.assertEqual(len(client.requested), len(assets))

    async def test_short_audio_pulses_restart_user_streaming_quiet_grace(self):
        class PulsedUserStreamingConnection:
            def __init__(self):
                self.client_is_speaking = False
                self.client_have_voice = False
                self._lesson_asset_audio_inflight = 0
                self._lesson_asset_last_audio_at = 0.0

            def is_realtime_busy(self):
                return True

            def _realtime_interaction_state(self):
                return "USER_STREAMING"

        assets = [_critical_assets()[1]]
        conn = PulsedUserStreamingConnection()
        client = _client_for(assets)
        now = {"value": 0.0}
        last_pulse = {"value": 0.0}

        async def sleep(delay):
            now["value"] += delay
            if now["value"] <= 0.3:
                # The inflight pulse begins and ends between AssetCache polls.
                conn._lesson_asset_audio_inflight = 1
                conn._lesson_asset_last_audio_at = now["value"]
                last_pulse["value"] = now["value"]
                conn._lesson_asset_audio_inflight = 0
            await asyncio.sleep(0)

        cache = self._cache(
            assets,
            client=client,
            busy_check=conn.is_realtime_busy,
            clock=lambda: now["value"],
            sleep=sleep,
            poll_interval=0.05,
        )

        self.assertTrue(await cache.preload())
        self.assertGreaterEqual(
            now["value"] - last_pulse["value"],
            asset_cache_module._SOFT_BUSY_PROGRESS_GRACE_SEC,
        )

    async def test_soft_busy_grace_never_overrides_expired_preload_deadline(self):
        class QuietUserStreamingConnection:
            client_is_speaking = False
            client_have_voice = False
            _lesson_asset_audio_inflight = 0
            _lesson_asset_last_audio_at = 0.0

            def is_realtime_busy(self):
                return True

            def _realtime_interaction_state(self):
                return "USER_STREAMING"

        conn = QuietUserStreamingConnection()
        cache = self._cache(
            [_critical_assets()[1]],
            client=_client_for([_critical_assets()[1]]),
            busy_check=conn.is_realtime_busy,
            clock=lambda: 0.2,
        )
        cache._soft_busy_since = 0.0
        cache._deadline = 0.1

        with self.assertRaises(PreloadTimeout):
            await cache._wait_while_busy()

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

    async def test_preload_wall_clock_timeout_cancels_pending_downloads(self):
        assets = _critical_assets()
        cache = self._cache(assets, client=_client_for(assets))

        async def _wait_for(_awaitable, timeout):
            raise asyncio.TimeoutError()

        with mock.patch.object(asset_cache_module.asyncio, "wait_for", _wait_for):
            with self.assertRaises(PreloadTimeout):
                await cache.preload()

        self.assertFalse(cache.is_ready())

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

    async def test_same_version_checksum_prefix_collision_gets_new_dir(self):
        # Full SHA identity matters. Two course edits can share the same short
        # prefix; the SD/cache key still must not collide and reuse stale bytes.
        assets = _critical_assets()
        old = self._cache(
            assets,
            client=_client_for(assets),
            lesson_version=2,
            manifest_checksum="abcdef1200000000000000000000000000000000000000000000000000000000",
        )
        new = self._cache(
            assets,
            client=_client_for(assets),
            lesson_version=2,
            manifest_checksum="abcdef12ffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
        )

        self.assertNotEqual(old.cache_key, new.cache_key)
        self.assertNotEqual(old.cache_dir, new.cache_dir)

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
