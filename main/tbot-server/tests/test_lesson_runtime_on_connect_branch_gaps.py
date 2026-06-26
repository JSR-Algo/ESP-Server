"""Close two on-connect teardown branches in ``_maybe_start_lesson_on_connect_impl``.

``--cov-branch`` flags two edges the existing republish/pull tests never take:

  * runtime.py 1685-1691 -- a runtime is already pinned for THIS assignment at the
    SAME version/checksum (the idempotent no-op path), BUT its state is ``S_PAUSED``.
    A restart request from PAUSED must NOT keep the dormant session: it logs, closes
    the paused runtime, nulls ``conn.lesson_runtime``, and falls through to build a
    fresh runtime. (Contrast: a RUNNING same-version runtime is kept untouched.)

  * runtime.py 1813,1815 -- the freshly-built runtime's ``start()`` raises
    ``LessonError`` (START_REFUSED). Recovery must (a) call ``conn.release_lesson_mode``
    so the connection leaves lesson mode it was put into for this attempt, and (b) null
    ``conn.lesson_runtime`` so no half-started runtime stays pinned, then return None.

Mirrors test_lesson_republish_on_connect.py: no network, unittest + unittest.mock
fakes; the backend/identity helpers are patched at their SOURCE modules, the locally
imported collaborators are replaced with inert MagicMocks, and module-level
``LessonRuntime`` is patched on core.lesson.runtime.
"""

import unittest
from unittest import mock

from core.lesson import runtime as rt_mod
from core.lesson.errors import LessonError, PROTOCOL_VERSION


ETAG_V1 = '"lesson-3-espTft-aaaaaaaa"'
CHK_V1 = "aaaaaaaa"
ASSIGNMENT_ID = "asg-1"


def _assignment():
    return {
        "assignmentId": ASSIGNMENT_ID,
        "assignmentVersion": 1,
        "lessonId": "w01-d01-barn-say-it",
        "lessonVersion": 3,
        "manifestChecksum": CHK_V1,
        "profile": "espTft",
        "state": "ASSIGNED",
    }


def _manifest():
    return {
        "manifestVersion": PROTOCOL_VERSION,
        "lessonId": "w01-d01-barn-say-it",
        "lessonVersion": 3,
        "profile": "espTft",
        "assets": [],
        "steps": [],
    }


class _DummyLogger:
    def bind(self, **k):
        return self

    def info(self, *a, **k):
        return None

    debug = warning = error = info


class _FakeWS:
    async def send(self, payload):
        return None


class _FakeExistingRuntime:
    """Runtime already pinned for the SAME assignment/version/checksum."""

    replay_pending_terminal_event = None

    def __init__(self, calls, *, state):
        self._calls = calls
        self.assignment_id = ASSIGNMENT_ID
        self.lesson_version = 3
        self.assignment_version = 1
        self.manifest_checksum = CHK_V1
        self.state = state
        self.closed = False

    async def close(self):
        self.closed = True
        self._calls.append("close")


class _FakeNewRuntime:
    """Runtime built by the fresh (re-)pull. ``start`` behaviour is class-configurable
    so the START_REFUSED branch can force a LessonError."""

    instances = []
    start_raises = None  # set to a LessonError to drive the refused branch

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
        if type(self).start_raises is not None:
            raise type(self).start_raises

    async def close(self):
        self.closed = True


class _FakeConn:
    def __init__(self):
        self.logger = _DummyLogger()
        self.websocket = _FakeWS()
        self.device_id = "AA:BB:CC:DD:EE:FF"
        self.features = {"lesson": True, "renderer": PROTOCOL_VERSION}
        self.config = {"server": {"api_url": "http://backend.test"}, "lesson": {}}
        self.lesson_runtime = None
        self.lesson_start_status = None
        self.mode_calls = []

    def is_realtime_busy(self):
        return False

    async def enter_lesson_mode(self, *, reason=None):
        self.mode_calls.append(("enter", reason))

    async def release_lesson_mode(self, *, reason=None):
        self.mode_calls.append(("release", reason))


async def _noop_resolve(client, base_url, mac, *, logger=None):
    return "device-uuid-1", "device-jwt-1"


async def _no_replay(*a, **k):
    return False


class RuntimeOnConnectBranchGapTest(unittest.IsolatedAsyncioTestCase):
    def _patches(self):
        async def _get_assignment(client, base_url, device_id, *, token=None):
            return _assignment()

        async def _get_manifest(client, base_url, lesson_id, profile, **kw):
            return _manifest(), ETAG_V1

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

    async def _run(self, conn):
        patches = self._patches()
        for p in patches:
            p.start()
        try:
            return await rt_mod._maybe_start_lesson_on_connect_impl(conn)
        finally:
            for p in patches:
                p.stop()

    # ── 1685-1691: same-version restart from PAUSED rebuilds the runtime ──────────
    async def test_paused_same_version_runtime_is_closed_and_rebuilt(self):
        _FakeNewRuntime.instances = []
        _FakeNewRuntime.start_raises = None
        calls = []
        conn = _FakeConn()
        paused = _FakeExistingRuntime(calls, state=rt_mod.S_PAUSED)
        conn.lesson_runtime = paused

        result = await self._run(conn)

        # The paused runtime was torn down (close called)...
        self.assertTrue(paused.closed)
        self.assertIn("close", calls)
        # ...and a FRESH runtime was built in its place, started, and pinned --
        # the dormant PAUSED session is NOT kept.
        self.assertEqual(len(_FakeNewRuntime.instances), 1)
        new_rt = _FakeNewRuntime.instances[0]
        self.assertIs(result, new_rt)
        self.assertIs(conn.lesson_runtime, new_rt)
        self.assertIsNot(result, paused)
        self.assertTrue(new_rt.started)

    async def test_running_same_version_runtime_is_kept_not_rebuilt(self):
        # Control: the SAME idempotent no-op path, but state RUNNING -> keep the
        # session, build nothing. Proves the rebuild is gated on PAUSED specifically.
        _FakeNewRuntime.instances = []
        _FakeNewRuntime.start_raises = None
        calls = []
        conn = _FakeConn()
        running = _FakeExistingRuntime(calls, state=rt_mod.S_RUNNING)
        conn.lesson_runtime = running

        result = await self._run(conn)

        self.assertFalse(running.closed)
        self.assertEqual(calls, [])
        self.assertEqual(_FakeNewRuntime.instances, [])
        self.assertIs(result, running)
        self.assertIs(conn.lesson_runtime, running)

    # ── 1813,1815: start() raising LessonError releases mode + nulls runtime ──────
    async def test_start_refused_releases_lesson_mode_and_nulls_runtime(self):
        _FakeNewRuntime.instances = []
        _FakeNewRuntime.start_raises = LessonError(
            "START_REFUSED", "renderer rejected start"
        )
        try:
            conn = _FakeConn()  # no prior runtime
            result = await self._run(conn)
        finally:
            _FakeNewRuntime.start_raises = None

        # A runtime was built and start() was attempted...
        self.assertEqual(len(_FakeNewRuntime.instances), 1)
        new_rt = _FakeNewRuntime.instances[0]
        self.assertTrue(new_rt.started)
        # ...start refused -> the half-started runtime is nulled (not left pinned),
        self.assertIsNone(conn.lesson_runtime)
        # the connection was released back out of lesson mode for this attempt,
        self.assertIn(("release", "lesson_start_refused"), conn.mode_calls)
        # the runtime was closed, and None is returned to the caller.
        self.assertTrue(new_rt.closed)
        self.assertIsNone(result)
        # Status surfaced as START_REFUSED for the parent/console.
        self.assertEqual(getattr(conn, "lesson_start_status", {}).get("code"), "START_REFUSED")


class RuntimeOnConnectChildNameEnrichmentTest(unittest.IsolatedAsyncioTestCase):
    """conn.config child name is enriched from the backend child profile (the name
    the parent set in the mobile app) on pull-on-connect — but only when the esp
    manager-api config left it blank, so an explicit manager-side name still wins.
    """

    def _patches(self, child_name_mock):
        async def _get_assignment(client, base_url, device_id, *, token=None):
            return _assignment()

        async def _get_manifest(client, base_url, lesson_id, profile, **kw):
            return _manifest(), ETAG_V1

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
                "config.manage_api_client.get_device_child_name",
                new=child_name_mock,
            ),
            mock.patch(
                "config.device_token_client.resolve_device_identity",
                side_effect=_noop_resolve,
            ),
            mock.patch(
                "core.lesson.forwarder.replay_stored_terminal_event",
                side_effect=_no_replay,
            ),
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

    async def _run(self, conn, child_name_mock):
        _FakeNewRuntime.instances = []
        _FakeNewRuntime.start_raises = None
        patches = self._patches(child_name_mock)
        for p in patches:
            p.start()
        try:
            return await rt_mod._maybe_start_lesson_on_connect_impl(conn)
        finally:
            for p in patches:
                p.stop()

    async def test_blank_name_is_filled_from_backend_child_profile(self):
        conn = _FakeConn()  # esp config carries NO child_profile (mobile-only household)
        child_name = mock.AsyncMock(return_value="Milo")

        await self._run(conn, child_name)

        self.assertEqual(conn.config.get("child_profile", {}).get("child_name"), "Milo")
        child_name.assert_awaited_once()

    async def test_whitespace_only_name_is_treated_as_blank_and_filled(self):
        conn = _FakeConn()
        conn.config["child_profile"] = {"child_name": "   "}
        child_name = mock.AsyncMock(return_value="Mai")

        await self._run(conn, child_name)

        self.assertEqual(conn.config["child_profile"]["child_name"], "Mai")
        child_name.assert_awaited_once()

    async def test_existing_manager_side_name_is_not_overridden(self):
        conn = _FakeConn()
        conn.config["child_profile"] = {"child_name": "EspName"}
        child_name = mock.AsyncMock(return_value="Milo")

        await self._run(conn, child_name)

        # Manager-side name wins; the backend leg is not even consulted.
        self.assertEqual(conn.config["child_profile"]["child_name"], "EspName")
        child_name.assert_not_awaited()

    async def test_backend_returning_none_leaves_blank_name_blank(self):
        conn = _FakeConn()
        child_name = mock.AsyncMock(return_value=None)

        await self._run(conn, child_name)

        # No child bound -> no key forced into an otherwise-absent child_profile.
        self.assertIsNone(conn.config.get("child_profile", {}).get("child_name"))
        child_name.assert_awaited_once()

    async def test_enrichment_failure_is_swallowed_and_pull_continues(self):
        conn = _FakeConn()
        child_name = mock.AsyncMock(side_effect=RuntimeError("backend down"))

        # The glue must still complete (build the runtime) despite the enrichment error.
        result = await self._run(conn, child_name)

        self.assertIsNotNone(result)
        self.assertIsNone(conn.config.get("child_profile", {}).get("child_name"))


if __name__ == "__main__":
    unittest.main()
