"""CR-RUN-12 — republish-on-connect evict + re-pull (tear-down ordering).

Exercises the version/checksum-diff branch of
``core.lesson.runtime._maybe_start_lesson_on_connect_impl`` (runtime.py ~1362-1423):

* a runtime is already pinned for THIS assignment;
* the backend serves a NEW manifest version (different lessonVersion/checksum);
* the stale version's bytes are EVICTED **before** the old runtime is closed, then a
  fresh runtime is re-pulled in place (``conn.lesson_runtime`` is replaced); and
* the whole tear-down + re-pull is DEFERRED (no evict, no close, old runtime kept)
  while ``conn.is_realtime_busy()`` is True, so it never interrupts a voice turn.

Mirrors test_lesson_hardening.py: no network, unittest + unittest.mock fakes. The four
backend/identity helpers are imported *inside* the function under test, so they are
patched at their SOURCE modules (config.manage_api_client / config.device_token_client
/ core.lesson.forwarder). ``AssetCache`` / ``LessonEventForwarder`` /
``PreloadVoiceLatencyAlarm`` are likewise imported locally and patched at source;
``LessonRuntime`` is module-level so it is patched on core.lesson.runtime.
"""

import unittest
from unittest import mock

from core.lesson import runtime as rt_mod
from core.lesson.errors import PROTOCOL_VERSION


# ── manifest etag shape: backend etagFor == "lesson-<version>-<profile>-<checksum>".
# parse_manifest_checksum() pulls the final dash-field, so profile names containing
# hyphens cannot corrupt the republish/cache checksum identity.
ETAG_V1 = '"lesson-3-espTft-aaaaaaaa"'
ETAG_V2 = '"lesson-4-espTft-bbbbbbbb"'
CHK_V1 = "aaaaaaaa"
CHK_V2 = "bbbbbbbb"

ASSIGNMENT_ID = "asg-1"


def _assignment(lesson_version: int, assignment_version: int):
    return {
        "assignmentId": ASSIGNMENT_ID,
        "assignmentVersion": assignment_version,
        "lessonId": "w01-d01-barn-say-it",
        "lessonVersion": lesson_version,
        "manifestChecksum": CHK_V2 if lesson_version == 4 else CHK_V1,
        "profile": "espTft",
        "state": "ASSIGNED",
    }


def _manifest():
    return {
        "manifestVersion": PROTOCOL_VERSION,
        "lessonId": "w01-d01-barn-say-it",
        "lessonVersion": 4,
        "profile": "espTft",
        "assets": [],
        "steps": [],
    }

def _republished_story_manifest():
    manifest = _manifest()
    manifest["lessonVersion"] = 3
    manifest["assets"] = [
        {
            "id": "backgroundScene.sunny",
            "layer": "backgroundScene",
            "role": "poster",
            "mediaType": "image",
            "path": "sunny.jpg",
            "sha256": "a" * 64,
            "critical": True,
        },
        {
            "id": "teachingObject.duck",
            "layer": "teachingObject",
            "role": "object",
            "mediaType": "image",
            "path": "duck.png",
            "sha256": "b" * 64,
            "critical": True,
        },
        {
            "id": "robotOverlay.ask",
            "layer": "robotOverlay",
            "role": "overlay",
            "mediaType": "image",
            "path": "ask.png",
            "sha256": "c" * 64,
            "critical": True,
        },
    ]
    manifest["steps"] = [
        {
            "id": "s-new-duck",
            "type": "repeat",
            "completionClass": "interactive",
            "prompt": "Con nói: con vịt.",
            "storyText": "TeeBot đổi sang bài con vịt trong sân nắng.",
            "storyBeat": {"ask": "Con thấy con gì?", "waitForChild": True},
            "scene": {
                "backgroundScene": {
                    "poster": {"key": "backgroundScene.sunny", "src": "sunny.jpg"},
                },
                "teachingObject": {
                    "asset": {"key": "teachingObject.duck", "src": "duck.png"},
                },
                "robotOverlay": {
                    "pose": "ask",
                    "asset": {"key": "robotOverlay.ask", "src": "ask.png"},
                },
            },
            "audio": {"tts": "Con thấy con gì?"},
            "timeoutSec": 12,
        }
    ]
    return manifest


class _DummyLogger:
    def bind(self, **k):
        return self

    def info(self, *a, **k):
        return None

    debug = warning = error = info


class _FakeWS:
    async def send(self, payload):
        return None


class _FakeExistingCache:
    """Stand-in for the OLD version's asset_cache. ``evict`` is the tear-down hook the
    republish branch must call (and call BEFORE the runtime is closed)."""

    def __init__(self, calls):
        self._calls = calls

    async def evict(self):
        self._calls.append("evict")


class _FakeExistingRuntime:
    """The runtime already pinned on conn.lesson_runtime for the SAME assignment."""

    state = "RUNNING"
    replay_pending_terminal_event = None

    def __init__(self, calls, *, lesson_version, assignment_version, checksum):
        self._calls = calls
        self.assignment_id = ASSIGNMENT_ID
        self.lesson_version = lesson_version
        self.assignment_version = assignment_version
        self.manifest_checksum = checksum
        self.asset_cache = _FakeExistingCache(calls)
        self.closed = False

    async def close(self):
        self.closed = True
        self._calls.append("close")


class _FakeNewRuntime:
    """The runtime built by the fresh re-pull (patched in for LessonRuntime)."""

    instances = []

    def __init__(self, conn, **kw):
        self.conn = conn
        self.kwargs = kw
        self.manifest_checksum = kw.get("manifest_checksum")
        self.state = "RUNNING"
        self.started = False
        self.closed = False
        type(self).instances.append(self)

    async def start(self):
        self.started = True

    async def close(self):
        self.closed = True


class _FakeConn:
    def __init__(self, *, busy=False):
        self.logger = _DummyLogger()
        self.websocket = _FakeWS()
        self.device_id = "AA:BB:CC:DD:EE:FF"
        self.features = {"lesson": True, "renderer": PROTOCOL_VERSION}
        self.config = {"server": {"api_url": "http://backend.test"}, "lesson": {}}
        self.lesson_runtime = None
        self.lesson_start_status = None
        self._busy = busy

    def is_realtime_busy(self):
        return self._busy


async def _noop_resolve(client, base_url, mac, *, logger=None):
    # Identity bridge: a non-empty (uuid, token) so the pull proceeds.
    return "device-uuid-1", "device-jwt-1"


async def _no_replay(*a, **k):
    return False


class RepublishOnConnectTest(unittest.IsolatedAsyncioTestCase):
    def _patches(self, *, assignment, manifest, etag):
        """Patch the backend/identity helpers + the locally-imported collaborators so
        the function under test exercises ONLY the republish branch with no network."""
        async def _get_assignment(client, base_url, device_id, *, token=None):
            return assignment

        async def _get_manifest(client, base_url, lesson_id, profile, **kw):
            return manifest, etag

        return [
            mock.patch(
                "config.manage_api_client.get_current_assignment",
                side_effect=_get_assignment,
            ),
            mock.patch(
                "config.manage_api_client.get_lesson_manifest",
                side_effect=_get_manifest,
            ),
            mock.patch(
                "config.device_token_client.resolve_device_identity",
                side_effect=_noop_resolve,
            ),
            mock.patch(
                "core.lesson.forwarder.replay_stored_terminal_event",
                side_effect=_no_replay,
            ),
            # The fresh re-pull constructs these locally; replace with inert fakes so
            # the assertion is about ordering/identity, not real cache/forwarder I/O.
            mock.patch("core.lesson.asset_cache.AssetCache", new=mock.MagicMock()),
            mock.patch(
                "core.lesson.forwarder.LessonEventForwarder", new=mock.MagicMock()
            ),
            mock.patch(
                "core.lesson.preload_voice_alarm.PreloadVoiceLatencyAlarm",
                new=mock.MagicMock(),
            ),
            mock.patch.object(rt_mod, "LessonRuntime", new=_FakeNewRuntime),
        ]

    async def test_version_change_evicts_then_re_pulls_in_order(self):
        _FakeNewRuntime.instances = []
        calls = []
        conn = _FakeConn(busy=False)
        existing = _FakeExistingRuntime(
            calls, lesson_version=3, assignment_version=1, checksum=CHK_V1
        )
        conn.lesson_runtime = existing

        patches = self._patches(
            assignment=_assignment(lesson_version=4, assignment_version=2),
            manifest=_manifest(),
            etag=ETAG_V2,
        )
        for p in patches:
            p.start()
        try:
            result = await rt_mod._maybe_start_lesson_on_connect_impl(conn)
        finally:
            for p in patches:
                p.stop()

        # The OLD cache was evicted, and that eviction happened BEFORE the old runtime
        # was closed (tear-down ordering — evict the disjoint version-scoped bytes,
        # then aclose the client). This is the load-bearing ordering assertion.
        self.assertEqual(calls, ["evict", "close"])
        self.assertTrue(existing.closed)

        # A FRESH runtime was re-pulled in place for the republished version, carrying
        # the NEW manifest checksum, started, and pinned on the connection.
        self.assertEqual(len(_FakeNewRuntime.instances), 1)
        new_rt = _FakeNewRuntime.instances[0]
        self.assertIs(result, new_rt)
        self.assertIs(conn.lesson_runtime, new_rt)
        self.assertIsNot(result, existing)
        self.assertEqual(new_rt.manifest_checksum, CHK_V2)
        self.assertTrue(new_rt.started)

    async def test_checksum_change_at_same_versions_evicts_then_re_pulls(self):
        """Author republish can keep lessonVersion/assignmentVersion stable while
        changing the manifest checksum. The ESP must treat that checksum as the
        cache identity, evict old bytes, and build a fresh runtime instead of
        reusing stale SD/cache content."""
        _FakeNewRuntime.instances = []
        calls = []
        conn = _FakeConn(busy=False)
        existing = _FakeExistingRuntime(
            calls, lesson_version=3, assignment_version=1, checksum=CHK_V1
        )
        conn.lesson_runtime = existing

        assignment = _assignment(lesson_version=3, assignment_version=1)
        assignment["manifestChecksum"] = CHK_V2
        manifest = _manifest()
        manifest["lessonVersion"] = 3

        patches = self._patches(
            assignment=assignment,
            manifest=manifest,
            etag='"lesson-3-espTft-bbbbbbbb"',
        )
        for p in patches:
            p.start()
        try:
            result = await rt_mod._maybe_start_lesson_on_connect_impl(conn)
        finally:
            for p in patches:
                p.stop()

        self.assertEqual(calls, ["evict", "close"])
        self.assertTrue(existing.closed)
        self.assertEqual(len(_FakeNewRuntime.instances), 1)
        new_rt = _FakeNewRuntime.instances[0]
        self.assertIs(result, new_rt)
        self.assertIs(conn.lesson_runtime, new_rt)
        self.assertIsNot(result, existing)
        self.assertEqual(new_rt.kwargs["assignment"]["lessonVersion"], 3)
        self.assertEqual(new_rt.kwargs["assignment"]["assignmentVersion"], 1)
        self.assertEqual(new_rt.manifest_checksum, CHK_V2)
        self.assertTrue(new_rt.started)

    async def test_checksum_republish_carries_updated_layers_and_story_into_new_runtime(self):
        """When an author edits a lesson without bumping versions, the checksum is
        the cache/runtime identity. The fresh runtime must receive the NEW manifest
        body, including changed layer assets and guided story question, rather than
        replaying the stale in-memory manifest."""
        _FakeNewRuntime.instances = []
        calls = []
        conn = _FakeConn(busy=False)
        existing = _FakeExistingRuntime(
            calls, lesson_version=3, assignment_version=1, checksum=CHK_V1
        )
        conn.lesson_runtime = existing

        assignment = _assignment(lesson_version=3, assignment_version=1)
        assignment["manifestChecksum"] = CHK_V2
        manifest = _republished_story_manifest()

        patches = self._patches(
            assignment=assignment,
            manifest=manifest,
            etag='"lesson-3-espTft-bbbbbbbb"',
        )
        for p in patches:
            p.start()
        try:
            result = await rt_mod._maybe_start_lesson_on_connect_impl(conn)
        finally:
            for p in patches:
                p.stop()

        self.assertEqual(calls, ["evict", "close"])
        self.assertIs(result, conn.lesson_runtime)
        self.assertEqual(len(_FakeNewRuntime.instances), 1)
        new_manifest = _FakeNewRuntime.instances[0].kwargs["manifest"]
        self.assertIs(new_manifest, manifest)
        step = new_manifest["steps"][0]
        self.assertEqual(
            step["storyBeat"],
            {"ask": "Con thấy con gì?", "waitForChild": True},
        )
        self.assertEqual(set(step["scene"]), {"backgroundScene", "teachingObject", "robotOverlay"})
        self.assertEqual(step["scene"]["backgroundScene"]["poster"]["key"], "backgroundScene.sunny")
        self.assertEqual(step["scene"]["teachingObject"]["asset"]["key"], "teachingObject.duck")
        self.assertEqual(step["scene"]["robotOverlay"]["asset"]["key"], "robotOverlay.ask")
        self.assertEqual(_FakeNewRuntime.instances[0].manifest_checksum, CHK_V2)

    async def test_version_change_is_deferred_while_realtime_busy(self):
        _FakeNewRuntime.instances = []
        calls = []
        conn = _FakeConn(busy=True)  # voice turn in progress
        existing = _FakeExistingRuntime(
            calls, lesson_version=3, assignment_version=1, checksum=CHK_V1
        )
        conn.lesson_runtime = existing

        patches = self._patches(
            assignment=_assignment(lesson_version=4, assignment_version=2),
            manifest=_manifest(),
            etag=ETAG_V2,
        )
        for p in patches:
            p.start()
        try:
            result = await rt_mod._maybe_start_lesson_on_connect_impl(conn)
        finally:
            for p in patches:
                p.stop()

        # Deferred: NOTHING was torn down (no evict, no close) and NO fresh runtime was
        # built. The existing session is returned and stays pinned, untouched.
        self.assertEqual(calls, [])
        self.assertFalse(existing.closed)
        self.assertEqual(_FakeNewRuntime.instances, [])
        self.assertIs(result, existing)
        self.assertIs(conn.lesson_runtime, existing)

    async def test_unchanged_version_keeps_session_without_eviction(self):
        # Control: identical version/checksum is the idempotent no-op (NOT a republish)
        # — proves the evict/re-pull is gated on a real version diff, not fired always.
        _FakeNewRuntime.instances = []
        calls = []
        conn = _FakeConn(busy=False)
        existing = _FakeExistingRuntime(
            calls, lesson_version=4, assignment_version=2, checksum=CHK_V2
        )
        conn.lesson_runtime = existing

        patches = self._patches(
            assignment=_assignment(lesson_version=4, assignment_version=2),
            manifest=_manifest(),
            etag=ETAG_V2,
        )
        for p in patches:
            p.start()
        try:
            result = await rt_mod._maybe_start_lesson_on_connect_impl(conn)
        finally:
            for p in patches:
                p.stop()

        self.assertEqual(calls, [])
        self.assertEqual(_FakeNewRuntime.instances, [])
        self.assertIs(result, existing)
        self.assertIs(conn.lesson_runtime, existing)


if __name__ == "__main__":
    unittest.main()
