"""Hardening tests added after the adversarial review of US-006 LANE-ESP:

* restart re-attest is wired into preload() (cached verified bytes are reused, not
  re-downloaded), not just exposed as a standalone method;
* the realtime guard gates request INITIATION (no connection opened mid voice turn);
* a terminal runtime state (FAILED via STEP_TIMEOUT) absorbs late inbound frames so
  a trailing gap cannot supersede STEP_TIMEOUT with PROTOCOL_SEQUENCE_ERROR.
"""

import asyncio
import hashlib
import json
import os
import shutil
import tempfile
import unittest

from core.lesson.asset_cache import AssetCache, DOWNLOADING, READY
from core.lesson.runtime import LessonRuntime
from core.lesson.errors import PROTOCOL_VERSION


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


POSTER = b"poster-bytes-xyz"
BARN = b"barn-bytes-abc"
BASE = "http://assets.test"


class _FakeStreamResponse:
    def __init__(self, chunks):
        self._chunks = chunks

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def raise_for_status(self):
        return None

    async def aiter_bytes(self, chunk_size):
        for c in self._chunks:
            yield c


class _RecordingClient:
    def __init__(self):
        self.requested = []
        self._by_url = {BASE + "/barn-round-field-poster.jpg": [POSTER], BASE + "/barn.png": [BARN]}

    def stream(self, method, url):
        self.requested.append(url)
        return _FakeStreamResponse(self._by_url.get(url, []))

    async def aclose(self):
        return None


def _critical_assets():
    return [
        {"key": "backgroundScene.poster", "path": "barn-round-field-poster.jpg", "sha256": _sha(POSTER),
         "critical": True, "layer": "backgroundScene", "role": "poster", "mediaType": "image/jpeg"},
        {"key": "teachingObject.barn", "path": "barn.png", "sha256": _sha(BARN),
         "critical": True, "layer": "teachingObject", "role": "primarySubject", "mediaType": "image/png"},
    ]


class AssetCacheHardeningTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="lesson-harden-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _cache(self, **kw):
        return AssetCache(
            assets=_critical_assets(), profile="espTft", asset_origin_base=BASE,
            cache_root=self.tmp, lesson_key="L", preload_timeout_sec=kw.pop("preload_timeout_sec", 5.0), **kw,
        )

    async def test_preload_reattests_cached_bytes_and_skips_redownload(self):
        client = _RecordingClient()
        cache = self._cache(client=client)
        # Surviving data/ cache from before a restart (valid bytes).
        os.makedirs(cache.cache_dir, exist_ok=True)
        for a in cache.assets:
            content = POSTER if a.key == "backgroundScene.poster" else BARN
            with open(cache._final_path(a), "wb") as fh:
                fh.write(content)

        ready = await cache.preload()

        self.assertTrue(ready)
        # Re-attest verified the cached bytes -> nothing was re-downloaded.
        self.assertEqual(client.requested, [])

    async def test_preload_downloads_only_the_missing_critical_after_partial_cache(self):
        client = _RecordingClient()
        cache = self._cache(client=client)
        os.makedirs(cache.cache_dir, exist_ok=True)
        # Only the poster survives; the barn must be (re)fetched.
        poster = cache._by_key["backgroundScene.poster"]
        with open(cache._final_path(poster), "wb") as fh:
            fh.write(POSTER)

        ready = await cache.preload()

        self.assertTrue(ready)
        self.assertEqual(client.requested, [BASE + "/barn.png"])

    async def test_request_initiation_is_gated_while_realtime_busy(self):
        busy = {"v": True}
        client = _RecordingClient()
        cache = self._cache(client=client, busy_check=lambda: busy["v"], poll_interval=0.01)

        task = asyncio.create_task(cache.preload())
        for _ in range(5):
            await asyncio.sleep(0.01)
        # No TCP/TLS connection opened during the voice turn.
        self.assertEqual(client.requested, [])
        self.assertFalse(task.done())
        self.assertTrue(all(a.state == DOWNLOADING for a in cache.assets))

        busy["v"] = False
        ready = await asyncio.wait_for(task, timeout=2.0)
        self.assertTrue(ready)
        self.assertEqual(len(client.requested), 2)


# ── runtime terminal-state absorption ────────────────────────────────────────

class _DummyLogger:
    def bind(self, **k):
        return self

    def info(self, *a, **k):
        return None

    debug = warning = error = info


class _FakeWS:
    def __init__(self):
        self.sent = []

    async def send(self, payload):
        self.sent.append(payload)


class _FakeConn:
    def __init__(self):
        self.logger = _DummyLogger()
        self.websocket = _FakeWS()
        self.session_id = "sess_x"
        self.features = {"lesson": True, "renderer": PROTOCOL_VERSION}
        self.config = {}

    def is_realtime_busy(self):
        return False


class _FakeAssetCache:
    preload_timeout_sec = 90

    def assert_profile_renderable(self):
        return None

    async def preload(self):
        return True

    def synthesize_preload_status(self, av):
        return {"assignmentVersion": av, "ready": True, "criticalTotal": 2, "criticalReady": 2, "assets": []}

    async def aclose(self):
        return None


class _FakeForwarder:
    def __init__(self):
        self.batches = []

    def enqueue(self, b):
        self.batches.append(b)

    async def aclose(self):
        return None


def _manifest(step_timeout=0.05):
    return {
        "manifestVersion": PROTOCOL_VERSION,
        "lessonId": "w01-d01-barn-say-it",
        "lessonVersion": 3,
        "profile": "espTft",
        "assets": [
            {"id": "backgroundScene.poster", "layer": "backgroundScene", "role": "poster",
             "mediaType": "image/jpeg", "path": "p.jpg", "url": "u", "sha256": "a", "critical": True},
        ],
        "steps": [
            {"id": "s4", "type": "model", "scene": {"backgroundScene": {}, "teachingObject": {}, "robotOverlay": {}},
             "audio": {"via": "tts"}, "timeoutSec": step_timeout},
        ],
    }


def _ack(seq, acks, step_id=None):
    return {"type": "lesson_ack", "sequence": seq, "stepId": step_id, "body": {"acks": acks, "rendered": True}}


def _progress(seq, event="step_completed"):
    return {"type": "lesson_progress", "sequence": seq, "stepId": "s4",
            "body": {"event": event, "stepType": "model", "result": "success"}}


def _sent_codes(conn):
    codes = []
    for raw in conn.websocket.sent:
        f = json.loads(raw)
        if f.get("type") == "lesson_error":
            codes.append(f["body"].get("code"))
    return codes


class RuntimeTerminalGuardTest(unittest.IsolatedAsyncioTestCase):
    async def _running_runtime(self, step_timeout=0.05):
        conn = _FakeConn()
        rt = LessonRuntime(
            conn,
            assignment={"assignmentId": "a", "assignmentVersion": 1, "lessonId": "w01-d01-barn-say-it",
                        "lessonVersion": 3, "profile": "espTft", "state": "ASSIGNED"},
            manifest=_manifest(step_timeout),
            asset_cache=_FakeAssetCache(),
            forwarder=_FakeForwarder(),
        )
        await rt.start()                      # prepare seq1
        await rt.on_lesson_ack(_ack(1, 1))    # prepare-ack -> preload
        await rt._preload_task                # -> lesson_start seq2
        await rt.on_lesson_ack(_ack(2, 2))    # start-ack -> RUNNING + lesson_step seq3
        return conn, rt

    async def test_step_timeout_absorbs_late_gap_no_protocol_sequence_error(self):
        conn, rt = await self._running_runtime(step_timeout=0.05)
        # No step-ack -> STEP_TIMEOUT fires.
        await asyncio.sleep(0.15)
        self.assertEqual(rt.state, "FAILED")
        self.assertEqual(rt.last_error.code, "STEP_TIMEOUT")
        codes_before = _sent_codes(conn)
        self.assertIn("STEP_TIMEOUT", codes_before)

        # A late, gapped progress must be absorbed by the terminal state.
        await rt.on_lesson_progress(_progress(99))
        codes_after = _sent_codes(conn)
        self.assertNotIn("PROTOCOL_SEQUENCE_ERROR", codes_after)
        self.assertEqual(rt.state, "FAILED")
        self.assertEqual(rt.last_error.code, "STEP_TIMEOUT")

    async def test_inbound_lesson_error_is_terminal_and_absorbs_following_frames(self):
        conn, rt = await self._running_runtime(step_timeout=5.0)  # long timeout: not the trigger
        await rt.on_lesson_error({"type": "lesson_error", "sequence": 3,
                                  "body": {"code": "RENDER_FAILED", "message": "x", "retryable": False}})
        self.assertEqual(rt.state, "FAILED")
        # Following frames are absorbed; no spurious PROTOCOL_SEQUENCE_ERROR emitted.
        await rt.on_lesson_progress(_progress(50))
        self.assertNotIn("PROTOCOL_SEQUENCE_ERROR", _sent_codes(conn))
        rt._cancel_step_timeout()


if __name__ == "__main__":
    unittest.main()
