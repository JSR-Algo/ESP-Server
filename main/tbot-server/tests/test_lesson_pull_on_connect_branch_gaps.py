"""Close the remaining *branch* gaps on the pull-on-connect glue in
``core.lesson.runtime`` (``_maybe_start_lesson_on_connect`` + its impl).

``--cov-branch`` flags nine partial branches whose false-edge no existing test takes:

  * 1221->1224 -- ``_maybe_start_lesson_on_connect``: the per-connection pull lock
                ALREADY exists (a prior pull created it) -> the lazy ``asyncio.Lock``
                init is skipped and the existing lock is reused.
  * 1272->1276 -- ``_..._impl``: the hello/features wait loop runs all 50 iterations
                without ``conn.features`` ever appearing -> the loop exits via its
                range, then the capability gate runs on a None features set.
  * 1399->1406 -- republish: ``conn.is_realtime_busy`` is not callable -> the
                busy-defer guard is skipped and tear-down proceeds.
  * 1417->1419 -- republish: the old runtime has no evictable cache (asset_cache is
                None) -> the ``evict`` call is skipped, only ``close`` runs.
  * 1459->1472 -- fresh pull: ``conn.lesson_voice_alarm`` already exists (reused
                across re-pulls) -> a new alarm is NOT constructed.
  * 1500->1502 -- fresh pull: ``runtime.start`` left state FAILED AND a concurrent
                pull replaced ``conn.lesson_runtime`` -> the ``is runtime`` guard is
                False, so the newer pinned runtime is NOT nulled, but this runtime is
                still closed.
  * 1512->1514 -- fresh pull: ``runtime.start`` raised LessonError but
                ``conn.release_lesson_mode`` is not callable -> the release call is
                skipped.
  * 1514->1516 -- fresh pull: same LessonError path, but ``conn.lesson_runtime`` was
                replaced concurrently -> the ``is runtime`` guard is False.

Mirrors ``test_lesson_republish_on_connect.py``: no network; the locally-imported
backend/identity helpers + collaborators are patched at SOURCE, ``LessonRuntime`` is
patched on ``core.lesson.runtime`` with inert fakes, and the function under test runs
end-to-end against real control flow.
"""

import unittest
from unittest import mock

from core.lesson import runtime as rt_mod
from core.lesson.errors import PROTOCOL_VERSION, LessonError

ETAG_V1 = '"lesson-3-espTft-aaaaaaaa"'
ETAG_V2 = '"lesson-4-espTft-bbbbbbbb"'
CHK_V1 = "aaaaaaaa"
CHK_V2 = "bbbbbbbb"
ASSIGNMENT_ID = "asg-1"


def _assignment(lesson_version=4, assignment_version=2, *, assignment_id=ASSIGNMENT_ID):
    a = {
        "assignmentVersion": assignment_version,
        "lessonId": "w01-d01-barn-say-it",
        "lessonVersion": lesson_version,
        "manifestChecksum": CHK_V2 if lesson_version == 4 else CHK_V1,
        "profile": "espTft",
        "state": "ASSIGNED",
    }
    if assignment_id is not None:
        a["assignmentId"] = assignment_id
    return a


def _manifest():
    return {
        "manifestVersion": PROTOCOL_VERSION,
        "lessonId": "w01-d01-barn-say-it",
        "lessonVersion": 4,
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


class _FakeConn:
    def __init__(self, *, busy=False, with_busy_check=True):
        self.logger = _DummyLogger()
        self.websocket = _FakeWS()
        self.device_id = "AA:BB:CC:DD:EE:FF"
        self.features = {"lesson": True, "renderer": PROTOCOL_VERSION}
        self.config = {"server": {"api_url": "http://backend.test"}, "lesson": {}}
        self.lesson_runtime = None
        self.lesson_start_status = None
        self._busy = busy
        if with_busy_check:
            self.is_realtime_busy = self._is_busy

    def _is_busy(self):
        return self._busy


async def _noop_resolve(client, base_url, mac, *, logger=None):
    return "device-uuid-1", "device-jwt-1"


async def _no_replay(*a, **k):
    return False


def _patches(*, assignment, manifest=None, etag=ETAG_V2, runtime_cls):
    async def _get_assignment(client, base_url, device_id, *, token=None):
        return assignment

    async def _get_manifest(client, base_url, lesson_id, profile, **kw):
        return (manifest if manifest is not None else _manifest()), etag

    return [
        mock.patch(
            "config.manage_api_client.get_current_assignment", side_effect=_get_assignment
        ),
        mock.patch(
            "config.manage_api_client.get_lesson_manifest", side_effect=_get_manifest
        ),
        mock.patch(
            "config.device_token_client.resolve_device_identity", side_effect=_noop_resolve
        ),
        mock.patch(
            "core.lesson.forwarder.replay_stored_terminal_event", side_effect=_no_replay
        ),
        mock.patch("core.lesson.asset_cache.AssetCache", new=mock.MagicMock()),
        mock.patch("core.lesson.forwarder.LessonEventForwarder", new=mock.MagicMock()),
        mock.patch(
            "core.lesson.preload_voice_alarm.PreloadVoiceLatencyAlarm", new=mock.MagicMock()
        ),
        mock.patch.object(rt_mod, "LessonRuntime", new=runtime_cls),
    ]


async def _run_impl(conn, patches):
    for p in patches:
        p.start()
    try:
        return await rt_mod._maybe_start_lesson_on_connect_impl(conn)
    finally:
        for p in patches:
            p.stop()


# ── fresh-pull runtime fakes (different start() outcomes per scenario) ────────────


class _HealthyRuntime:
    instances = []

    def __init__(self, conn, **kw):
        self.conn = conn
        self.kwargs = kw
        self.manifest_checksum = kw.get("manifest_checksum")
        self.assignment_id = (kw.get("assignment") or {}).get("assignmentId")
        self.state = "RUNNING"
        self.started = False
        self.closed = False
        type(self).instances.append(self)

    async def start(self):
        self.started = True

    async def close(self):
        self.closed = True


class PullOnConnectBranchTest(unittest.IsolatedAsyncioTestCase):
    # ── 1221->1224: existing pull lock is reused, not re-created ──────────────────
    async def test_existing_pull_lock_is_reused(self):
        import asyncio

        _HealthyRuntime.instances = []
        conn = _FakeConn()
        preexisting_lock = asyncio.Lock()
        conn._lesson_pull_lock = preexisting_lock  # a prior pull already created it

        patches = _patches(assignment=_assignment(), runtime_cls=_HealthyRuntime)
        for p in patches:
            p.start()
        try:
            await rt_mod.maybe_start_lesson_on_connect(conn)
        finally:
            for p in patches:
                p.stop()

        # The SAME lock object survived (lazy-init branch skipped).
        self.assertIs(conn._lesson_pull_lock, preexisting_lock)

    # ── 1272->1276: features-wait loop runs to completion with features=None ──────
    async def test_features_wait_loop_completes_then_capability_gate_runs(self):
        import asyncio

        _HealthyRuntime.instances = []
        conn = _FakeConn()
        conn.features = None  # never arrives -> the loop exhausts its 50 iterations

        slept = []

        async def _fast_sleep(delay):
            slept.append(delay)

        patches = _patches(assignment=_assignment(), runtime_cls=_HealthyRuntime)
        patches.append(mock.patch.object(rt_mod.asyncio, "sleep", _fast_sleep))

        result = await _run_impl(conn, patches)

        # Loop ran all 50 iterations (features stayed None), then the capability gate
        # rejected the None features set -> no runtime built, status flagged.
        self.assertEqual(len(slept), 50)
        self.assertIsNone(result)
        self.assertEqual(conn.lesson_start_status["code"], "LESSON_CAPABILITY_MISSING")

    # NOTE: branch 1328->1346 (the False edge of ``if isinstance(assignment_id, str)
    # and assignment_id:``) is GENUINELY UNREACHABLE: ``_assignment_metadata_errors``
    # at runtime.py:1322 already returns early (ASSIGNMENT_INVALID) whenever
    # assignmentId is not a non-empty str, and line 1327 re-reads the SAME key, so by
    # line 1328 assignment_id is always a valid non-empty str. The False edge is a
    # redundant defensive re-check that no real path can take -> documented as a
    # justified exception rather than fake-covered.

    # ── 1459->1472: an existing per-connection voice alarm is reused ──────────────
    async def test_existing_voice_alarm_is_reused(self):
        _HealthyRuntime.instances = []
        conn = _FakeConn()
        sentinel_alarm = object()
        conn.lesson_voice_alarm = sentinel_alarm  # reused across re-pulls

        patches = _patches(assignment=_assignment(), runtime_cls=_HealthyRuntime)
        result = await _run_impl(conn, patches)

        # The alarm was NOT reconstructed; the same object handed to the runtime.
        self.assertIs(conn.lesson_voice_alarm, sentinel_alarm)
        self.assertIs(result, _HealthyRuntime.instances[0])
        self.assertIs(_HealthyRuntime.instances[0].kwargs.get("alarm"), sentinel_alarm)


class RepublishBranchTest(unittest.IsolatedAsyncioTestCase):
    # ── 1399->1406: republish proceeds when is_realtime_busy is not callable ──────
    async def test_republish_without_busy_check_proceeds(self):
        _HealthyRuntime.instances = []
        # conn WITHOUT an is_realtime_busy attribute at all.
        conn = _FakeConn(with_busy_check=False)

        teardown = []

        class _OldRuntime:
            assignment_id = ASSIGNMENT_ID
            lesson_version = 3
            assignment_version = 1
            manifest_checksum = CHK_V1
            state = "RUNNING"
            replay_pending_terminal_event = None

            def __init__(self):
                self.asset_cache = self._Cache()

            class _Cache:
                async def evict(self_inner):
                    teardown.append("evict")

            async def close(self):
                teardown.append("close")

        conn.lesson_runtime = _OldRuntime()

        # New version (4/2/CHK_V2) differs -> republish path; no busy check -> proceeds.
        patches = _patches(assignment=_assignment(4, 2), runtime_cls=_HealthyRuntime)
        result = await _run_impl(conn, patches)

        self.assertEqual(teardown, ["evict", "close"])
        self.assertEqual(len(_HealthyRuntime.instances), 1)
        self.assertIs(result, _HealthyRuntime.instances[0])

    # ── 1417->1419: republish skips evict when the old cache is None ──────────────
    async def test_republish_without_old_cache_skips_evict(self):
        _HealthyRuntime.instances = []
        conn = _FakeConn(busy=False)

        teardown = []

        class _OldRuntimeNoCache:
            assignment_id = ASSIGNMENT_ID
            lesson_version = 3
            assignment_version = 1
            manifest_checksum = CHK_V1
            state = "RUNNING"
            replay_pending_terminal_event = None
            asset_cache = None  # nothing to evict

            async def close(self):
                teardown.append("close")

        conn.lesson_runtime = _OldRuntimeNoCache()

        patches = _patches(assignment=_assignment(4, 2), runtime_cls=_HealthyRuntime)
        result = await _run_impl(conn, patches)

        # evict skipped (no cache), only close ran.
        self.assertEqual(teardown, ["close"])
        self.assertEqual(len(_HealthyRuntime.instances), 1)
        self.assertIs(result, _HealthyRuntime.instances[0])


class StartFailureBranchTest(unittest.IsolatedAsyncioTestCase):
    # ── 1500->1502: FAILED start + conn.lesson_runtime swapped -> guard False ─────
    async def test_failed_start_with_swapped_pin_does_not_null_newer(self):
        newer_pin = object()

        class _FailingSwapRuntime:
            instances = []

            def __init__(self, conn, **kw):
                self.conn = conn
                self.state = "RUNNING"
                self.closed = False
                type(self).instances.append(self)

            async def start(self):
                # A concurrent pull replaced the pinned runtime, then this one failed.
                self.conn.lesson_runtime = newer_pin
                self.state = "FAILED"

            async def close(self):
                self.closed = True

        _FailingSwapRuntime.instances = []
        conn = _FakeConn()

        patches = _patches(assignment=_assignment(), runtime_cls=_FailingSwapRuntime)
        result = await _run_impl(conn, patches)

        rt = _FailingSwapRuntime.instances[0]
        # FAILED start -> returns None and closes this runtime ...
        self.assertIsNone(result)
        self.assertTrue(rt.closed)
        # ... but the newer concurrently-pinned runtime is preserved (is-guard False).
        self.assertIs(conn.lesson_runtime, newer_pin)

    # ── 1512->1514 + 1514->1516: LessonError, no release callback, swapped pin ────
    async def test_lesson_error_without_release_callback_and_swapped_pin(self):
        newer_pin = object()

        class _ErroringSwapRuntime:
            instances = []

            def __init__(self, conn, **kw):
                self.conn = conn
                self.state = "RUNNING"
                self.closed = False
                type(self).instances.append(self)

            async def start(self):
                self.conn.lesson_runtime = newer_pin  # concurrent swap
                raise LessonError("START_REFUSED", "no display")

            async def close(self):
                self.closed = True

        _ErroringSwapRuntime.instances = []
        # conn has NO release_lesson_mode attribute -> getattr -> None (not callable).
        conn = _FakeConn()
        self.assertFalse(hasattr(conn, "release_lesson_mode"))

        patches = _patches(assignment=_assignment(), runtime_cls=_ErroringSwapRuntime)
        result = await _run_impl(conn, patches)

        rt = _ErroringSwapRuntime.instances[0]
        self.assertIsNone(result)
        self.assertTrue(rt.closed)
        # release callback skipped (1512 false), newer pin preserved (1514 false).
        self.assertIs(conn.lesson_runtime, newer_pin)


if __name__ == "__main__":
    unittest.main()
