"""S8 / S9 — LessonRuntime state machine + the single result->outcome rename.

Drives ``core.lesson.runtime.LessonRuntime`` against the FROZEN S2 wire fixture
``fixtures/lesson-protocol.v1.json`` (byte-consistent prepare/start/step/stop) and
exercises the P0 ack-correlation contract (body.acks, never envelope.sequence /
ackFor), the ready gate, the capability + protocol-version gates, STEP_TIMEOUT vs
PROTOCOL_SEQUENCE_ERROR distinctness, and the dedicated progress-forward path.

Async tests use ``unittest.IsolatedAsyncioTestCase`` (this repo does NOT use
pytest-asyncio markers). The REAL ``config.manage_api_client`` is loaded via an
``importlib`` spec because ``tests/conftest.py`` installs a stub for it.
"""

import copy
import importlib.util
import json
import os
import unittest

from core.lesson.errors import LessonError


# ── frozen wire fixture ─────────────────────────────────────────────────────────

FIX = json.load(
    open(
        os.path.join(
            os.path.dirname(__file__),
            "..",
            "..",
            "..",
            "..",
            "docs",
            "stories",
            "US-006-learning-course-runtime",
            "fixtures",
            "lesson-protocol.v1.json",
        )
    )
)


def _load_real_manage_api_client():
    """Load a fresh, isolated copy of the real ``config.manage_api_client`` from
    disk — immune to any monkeypatching other tests apply to the shared module
    instance. (conftest does NOT stub this module; it only filters warnings.)"""
    spec = importlib.util.spec_from_file_location(
        "config._mac_real_for_test",
        os.path.join(os.path.dirname(__file__), "..", "config", "manage_api_client.py"),
    )
    mac = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mac)
    return mac


# ── fakes ───────────────────────────────────────────────────────────────────────


class _DummyLogger:
    def bind(self, **kwargs):
        return self

    def info(self, *a, **k):
        return None

    def debug(self, *a, **k):
        return None

    def warning(self, *a, **k):
        return None

    def error(self, *a, **k):
        return None


class _FakeWebSocket:
    def __init__(self):
        self.sent = []

    async def send(self, payload):
        self.sent.append(payload)


class _FakeConn:
    def __init__(self, features=None, session_id="sess"):
        self.logger = _DummyLogger()
        self.websocket = _FakeWebSocket()
        self.session_id = session_id
        self.features = (
            {"lesson": True, "renderer": "teebot-lesson-renderer.v1"}
            if features is None
            else features
        )
        self.config = {}

    def is_realtime_busy(self):
        return False


class _FakeAssetCache:
    """Injectable preload outcome; never touches the network or disk."""

    def __init__(
        self,
        *,
        ready=True,
        preload_error=None,
        profile_error=None,
    ):
        self.preload_timeout_sec = 90
        self._ready = ready
        self._preload_error = preload_error
        self._profile_error = profile_error
        self.closed = False

    def assert_profile_renderable(self):
        if self._profile_error is not None:
            raise self._profile_error

    async def preload(self):
        if self._preload_error is not None:
            raise self._preload_error
        return self._ready

    def synthesize_preload_status(self, assignment_version):
        return {
            "assignmentVersion": assignment_version,
            "ready": self._ready,
            "criticalTotal": 2,
            "criticalReady": 2 if self._ready else 0,
            "assets": [],
        }

    async def aclose(self):
        self.closed = True


class _FakeForwarder:
    def __init__(self):
        self.batches = []
        self.closed = False

    def enqueue(self, batch):
        self.batches.append(batch)

    async def aclose(self):
        self.closed = True


# ── manifest / assignment derived FROM the fixture (round-trips to the frames) ───


def _build_manifest():
    prep = FIX["frames"]["lesson_prepare"]
    step = FIX["frames"]["lesson_step"]
    assets = [
        {
            "id": a["key"],
            "layer": a["layer"],
            "role": a["role"],
            "mediaType": a["mediaType"],
            "path": a["path"],
            "url": "http://assets.test/" + a["path"],
            "sha256": a["sha256"],
            "critical": a["critical"],
        }
        for a in prep["body"]["criticalAssets"]
    ]
    steps = [
        {
            "id": step["stepId"],
            "type": step["body"]["stepType"],
            "scene": step["body"]["scene"],
            "audio": step["body"]["audio"],
            "timeoutSec": step["body"]["timeoutSec"],
            "prompt": "Say barn",
            "subject": "barn",
            "robotState": "modeling",
            "pose": "teach",
            "expression": "teaching",
            "phase": "model",
            "entrance": "none",
        }
    ]
    return {
        "manifestVersion": "teebot-lesson-renderer.v1",
        "lessonId": prep["lessonId"],
        "lessonVersion": prep["lessonVersion"],
        "profile": "espTft",
        "assets": assets,
        "steps": steps,
    }


def _build_multistep_manifest():
    """P5: a 2-step manifest projecting s4 (frames.lesson_step) THEN s5
    (multiStep.frames.lesson_step_s5) in manifest order. Both step frames are taken
    verbatim from the fixture so the emitted lesson_step bodies round-trip back to
    their frozen wire shapes."""
    base = _build_manifest()
    s4 = FIX["frames"]["lesson_step"]
    s5 = FIX["multiStep"]["frames"]["lesson_step_s5"]
    base["steps"] = [
        {
            "id": s4["stepId"],
            "type": s4["body"]["stepType"],
            "scene": s4["body"]["scene"],
            "audio": s4["body"]["audio"],
            "timeoutSec": s4["body"]["timeoutSec"],
        },
        {
            "id": s5["stepId"],
            "type": s5["body"]["stepType"],
            "scene": s5["body"]["scene"],
            "audio": s5["body"]["audio"],
            "timeoutSec": s5["body"]["timeoutSec"],
        },
    ]
    return base


def _build_two_interactive_step_manifest():
    """A 2-step manifest where BOTH steps are INTERACTIVE (model s4 -> listen s5),
    so each gates on its own ack + step_completed. Used to test latch-reset between
    steps without the passive auto-advance shortcut."""
    base = _build_manifest()
    step = FIX["frames"]["lesson_step"]
    base["steps"] = [
        {
            "id": "s4",
            "type": "model",
            "scene": step["body"]["scene"],
            "audio": step["body"]["audio"],
            "timeoutSec": step["body"]["timeoutSec"],
        },
        {
            "id": "s5",
            "type": "listen",  # INTERACTIVE: still waits for step_completed.
            "scene": step["body"]["scene"],
            "audio": step["body"]["audio"],
            "timeoutSec": step["body"]["timeoutSec"],
        },
    ]
    return base


def _build_steps_manifest(step_specs):
    """Build a manifest with arbitrary (id, type) steps reusing the frozen s4 scene/
    audio so each emitted lesson_step is a valid §5.6 body. ``step_specs`` is a list
    of (stepId, stepType) tuples in authored/manifest order."""
    base = _build_manifest()
    step = FIX["frames"]["lesson_step"]
    base["steps"] = [
        {
            "id": sid,
            "type": stype,
            "scene": step["body"]["scene"],
            "audio": step["body"]["audio"],
            "timeoutSec": step["body"]["timeoutSec"],
        }
        for sid, stype in step_specs
    ]
    return base


def _build_class_steps_manifest(step_specs):
    """L3 P1: a manifest of (id, type, completionClass) steps reusing the frozen s4
    scene/audio. ``completionClass`` is either 'passive'/'interactive' (the explicit
    authoritative classifier) or None (omit the field entirely -> v1 fallback path).
    The step ``type`` can be an AUTHOR-DEFINED kind unknown to PASSIVE_STEP_TYPES."""
    base = _build_manifest()
    step = FIX["frames"]["lesson_step"]
    out = []
    for sid, stype, completion_class in step_specs:
        row = {
            "id": sid,
            "type": stype,
            "scene": step["body"]["scene"],
            "audio": step["body"]["audio"],
            "timeoutSec": step["body"]["timeoutSec"],
        }
        if completion_class is not None:
            row["completionClass"] = completion_class
        out.append(row)
    base["steps"] = out
    return base


def _build_assignment():
    prep = FIX["frames"]["lesson_prepare"]
    return {
        "assignmentId": prep["assignmentId"],
        "assignmentVersion": prep["body"]["assignmentVersion"],
        "lessonId": prep["lessonId"],
        "lessonVersion": prep["lessonVersion"],
        "profile": "espTft",
        "state": "ASSIGNED",
    }


def _manifest_checksum():
    return FIX["frames"]["lesson_prepare"]["body"]["manifestRef"]["manifestChecksum"]


def _ack(acks, env_seq, *, step_id=None, extra=None):
    body = {"acks": acks, "rendered": True, "degraded": False}
    if extra is not None:
        body = dict(extra)
    return {
        "type": "lesson_ack",
        "protocolVersion": "teebot-lesson-renderer.v1",
        "assignmentId": "a",
        "sessionId": "s",
        "lessonId": "w01-d01-barn-say-it",
        "lessonVersion": 3,
        "stepId": step_id,
        "sequence": env_seq,
        "timestamp": 1,
        "body": body,
    }


def _progress(env_seq, body, *, step_id="s4"):
    return {
        "type": "lesson_progress",
        "protocolVersion": "teebot-lesson-renderer.v1",
        "assignmentId": "a",
        "sessionId": "s",
        "lessonId": "w01-d01-barn-say-it",
        "lessonVersion": 3,
        "stepId": step_id,
        "sequence": env_seq,
        "timestamp": 1,
        "body": body,
    }


class _RecordingAlarm:
    """S13 alarm stand-in: records the preload-window bracket calls (plan §11.2)."""

    def __init__(self):
        self.events = []  # ("active", bool) in call order
        self.depth = 0
        self.max_depth = 0

    def set_preload_active(self, active):
        self.events.append(("active", bool(active)))
        self.depth += 1 if active else -1
        self.max_depth = max(self.max_depth, self.depth)


class LessonRuntimeTest(unittest.IsolatedAsyncioTestCase):
    def _runtime(
        self, conn=None, manifest=None, asset_cache=None, forwarder=None, alarm=None
    ):
        from core.lesson.runtime import LessonRuntime

        prep = FIX["frames"]["lesson_prepare"]
        conn = conn or _FakeConn(session_id=prep["sessionId"])
        return LessonRuntime(
            conn,
            assignment=_build_assignment(),
            manifest=manifest or _build_manifest(),
            asset_cache=asset_cache or _FakeAssetCache(ready=True),
            forwarder=forwarder or _FakeForwarder(),
            manifest_checksum=_manifest_checksum(),
            alarm=alarm,
        )

    def _sent_frames(self, conn):
        return [json.loads(p) for p in conn.websocket.sent]

    # 1) happy thread byte-consistent with the fixture ---------------------------

    async def test_happy_thread_frames_byte_consistent_with_fixture(self):
        conn = _FakeConn(session_id=FIX["frames"]["lesson_prepare"]["sessionId"])
        forwarder = _FakeForwarder()
        rt = self._runtime(conn=conn, forwarder=forwarder)

        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))  # prepare-ack
        self.assertIsNotNone(rt._preload_task)
        await rt._preload_task
        await rt.on_lesson_ack(_ack(2, 2))  # start-ack
        await rt.on_lesson_ack(_ack(3, 3, step_id="s4"))  # step-ack
        await rt.on_lesson_progress(
            _progress(
                4,
                {
                    "event": "step_completed",
                    "stepType": "model",
                    "result": "success",
                    "detail": {"tapTargetHit": True, "utterance": "Yes! barn!"},
                },
            )
        )
        await rt.on_lesson_ack(_ack(4, 5))  # stop-ack (env seq 5, body.acks 4)

        sent = self._sent_frames(conn)
        by_type = {f["type"]: f for f in sent}
        self.assertEqual(
            set(by_type),
            {"lesson_prepare", "lesson_start", "lesson_step", "lesson_stop"},
        )

        for ftype in ("lesson_prepare", "lesson_start", "lesson_step", "lesson_stop"):
            got = dict(by_type[ftype])
            got.pop("timestamp")
            want = copy.deepcopy(FIX["frames"][ftype])
            want.pop("timestamp")
            self.assertEqual(got, want, f"{ftype} not byte-consistent with fixture")

        self.assertEqual(rt.state, "COMPLETED")

    # 2) ack correlation uses body.acks, never envelope.sequence / ackFor --------

    async def test_ack_correlation_uses_body_acks_not_envelope_sequence_and_ignores_ackFor(
        self,
    ):
        # 2a: a prepare-ack carrying ONLY {"ackFor":1} must NOT correlate.
        conn = _FakeConn()
        rt = self._runtime(conn=conn)
        await rt.start()
        await rt.on_lesson_ack(_ack(None, 1, extra={"ackFor": 1}))
        self.assertIsNone(rt._preload_task)  # no correlation -> preload not kicked
        # only lesson_prepare on the wire so far.
        self.assertEqual([f["type"] for f in self._sent_frames(conn)], ["lesson_prepare"])

        # body.acks=1 DOES kick it (envelope sequence here is the F->S counter 2).
        await rt.on_lesson_ack(_ack(1, 2))
        self.assertIsNotNone(rt._preload_task)
        await rt._preload_task
        self.assertIn("lesson_start", [f["type"] for f in self._sent_frames(conn)])

        # 2b: the stop-ack with envelope sequence 5 but body.acks 4 correlates to stop.
        await rt.on_lesson_ack(_ack(2, 3))  # start-ack (F->S seq 3)
        await rt.on_lesson_ack(_ack(3, 4, step_id="s4"))  # step-ack (F->S seq 4)
        await rt.on_lesson_progress(
            _progress(
                5,
                {
                    "event": "step_completed",
                    "stepType": "model",
                    "result": "success",
                    "detail": {"tapTargetHit": True},
                },
            )
        )
        await rt.on_lesson_ack(_ack(4, 6))  # stop-ack: env seq 6, body.acks 4
        self.assertEqual(rt.state, "COMPLETED")

    # 3) lesson_start gated until ready ------------------------------------------

    async def test_lesson_start_blocked_until_ready(self):
        # Negative: ready=False -> after prepare-ack + preload, only lesson_prepare.
        conn = _FakeConn()
        rt = self._runtime(conn=conn, asset_cache=_FakeAssetCache(ready=False))
        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))
        await rt._preload_task
        self.assertEqual(
            [f["type"] for f in self._sent_frames(conn)], ["lesson_prepare"]
        )

        # Positive control: ready=True -> lesson_start IS sent.
        conn2 = _FakeConn()
        rt2 = self._runtime(conn=conn2, asset_cache=_FakeAssetCache(ready=True))
        await rt2.start()
        await rt2.on_lesson_ack(_ack(1, 1))
        await rt2._preload_task
        self.assertIn("lesson_start", [f["type"] for f in self._sent_frames(conn2)])

    # 4) capability gate refuses (no frame on the wire) --------------------------

    async def test_capability_gate_refuses(self):
        for features in ({}, {"lesson": True}):
            conn = _FakeConn(features=features)
            rt = self._runtime(conn=conn)
            with self.assertRaises(LessonError) as ctx:
                await rt.start()
            self.assertEqual(ctx.exception.code, "LESSON_VERSION_UNSUPPORTED")
            self.assertEqual(conn.websocket.sent, [])  # nothing emitted

    # 5) protocol-version gate ---------------------------------------------------

    async def test_protocol_version_gate(self):
        conn = _FakeConn()
        manifest = _build_manifest()
        manifest["manifestVersion"] = "teebot-renderer.v1"
        rt = self._runtime(conn=conn, manifest=manifest)
        with self.assertRaises(LessonError) as ctx:
            await rt.start()
        self.assertEqual(ctx.exception.code, "LESSON_VERSION_UNSUPPORTED")
        self.assertEqual(conn.websocket.sent, [])

    # 5b) device-renderer profile gate -------------------------------------------

    async def test_profile_gate_rejects_non_esptft_profile(self):
        # FIX#1: a published lesson with a non-espTft profile (piTft/mobile) renders
        # BLANK on espTft-only firmware. With the default supported_profiles (['espTft'],
        # i.e. conn.config has no lesson.supported_profiles) a runtime whose assignment
        # profile is 'piTft' must be REJECTED from start() with LESSON_VERSION_UNSUPPORTED
        # and put NO frame on the wire (skipped, not rendered blank).
        from core.lesson.runtime import LessonRuntime

        conn = _FakeConn()  # config = {} -> supported_profiles defaults to ['espTft']
        assignment = _build_assignment()
        assignment["profile"] = "piTft"
        rt = LessonRuntime(
            conn,
            assignment=assignment,
            manifest=_build_manifest(),
            asset_cache=_FakeAssetCache(ready=True),
            forwarder=_FakeForwarder(),
            manifest_checksum=_manifest_checksum(),
        )
        with self.assertRaises(LessonError) as ctx:
            await rt.start()
        self.assertEqual(ctx.exception.code, "LESSON_VERSION_UNSUPPORTED")
        self.assertEqual(conn.websocket.sent, [])  # nothing emitted

    async def test_profile_gate_passes_esptft_profile(self):
        # The espTft profile is in the default supported set -> the gate passes and the
        # runtime proceeds to emit lesson_prepare (unchanged behaviour for current FW).
        rt = self._runtime()  # _build_assignment() -> profile 'espTft'
        await rt.start()  # must NOT raise
        self.assertEqual(rt.state, "PRELOADING")
        sent_types = [json.loads(p)["type"] for p in rt.conn.websocket.sent]
        self.assertEqual(sent_types, ["lesson_prepare"])

    # 6) STEP_TIMEOUT distinct from PROTOCOL_SEQUENCE_ERROR ----------------------

    async def test_step_timeout_distinct_from_protocol_sequence_error(self):
        import asyncio

        conn = _FakeConn()
        manifest = _build_manifest()
        manifest["steps"][0]["timeoutSec"] = 0.05
        rt = self._runtime(conn=conn, manifest=manifest)
        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))
        await rt._preload_task
        await rt.on_lesson_ack(_ack(2, 2))  # start-ack -> RUNNING + emits lesson_step
        self.assertEqual(rt.state, "RUNNING")
        # Do NOT send the step-ack; let the timeout fire.
        await asyncio.sleep(0.15)

        self.assertEqual(rt.state, "FAILED")
        self.assertIsNotNone(rt.last_error)
        self.assertEqual(rt.last_error.code, "STEP_TIMEOUT")

        codes = [
            f.get("body", {}).get("code")
            for f in self._sent_frames(conn)
            if f["type"] == "lesson_error"
        ]
        self.assertIn("STEP_TIMEOUT", codes)
        self.assertNotIn("PROTOCOL_SEQUENCE_ERROR", codes)

    # 7) inbound gap -> PROTOCOL_SEQUENCE_ERROR (distinct from STEP_TIMEOUT) -----

    async def test_inbound_gap_raises_protocol_sequence_error(self):
        conn = _FakeConn()
        rt = self._runtime(conn=conn)
        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))
        await rt._preload_task
        await rt.on_lesson_ack(_ack(2, 2))  # start-ack -> RUNNING
        await rt.on_lesson_ack(_ack(3, 3, step_id="s4"))  # step-ack (last inbound = 3)
        self.assertEqual(rt.state, "RUNNING")

        # Jump the inbound envelope sequence forward (last+3 = 6, expected 4).
        await rt.on_lesson_progress(
            _progress(6, {"event": "step_completed", "result": "success"})
        )

        err_frames = [
            f for f in self._sent_frames(conn) if f["type"] == "lesson_error"
        ]
        self.assertTrue(err_frames)
        seq_errs = [
            f for f in err_frames if f["body"]["code"] == "PROTOCOL_SEQUENCE_ERROR"
        ]
        self.assertTrue(seq_errs)
        self.assertTrue(seq_errs[-1]["body"]["retryable"])
        # Distinct from STEP_TIMEOUT: the gap does NOT fail the run.
        self.assertNotEqual(rt.state, "FAILED")

    # 8) progress forwarded on the dedicated path --------------------------------

    async def test_progress_forwarded_on_dedicated_path(self):
        conn = _FakeConn()
        forwarder = _FakeForwarder()
        rt = self._runtime(conn=conn, forwarder=forwarder)
        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))
        await rt._preload_task
        await rt.on_lesson_ack(_ack(2, 2))
        await rt.on_lesson_ack(_ack(3, 3, step_id="s4"))
        await rt.on_lesson_progress(
            _progress(
                4,
                {
                    "event": "step_completed",
                    "stepType": "model",
                    "result": "success",
                    "detail": {"tapTargetHit": True, "utterance": "Yes! barn!"},
                },
            )
        )

        completed = [
            b
            for b in forwarder.batches
            if b["events"] and b["events"][0].get("type") == "step_completed"
        ]
        self.assertTrue(completed, "step_completed not forwarded via forwarder.enqueue")
        ev = completed[0]["events"][0]
        self.assertEqual(ev["type"], "step_completed")
        self.assertEqual(ev["result"], "success")  # rename happens at POST, not here

    # 9) post_lesson_event renames result->outcome and strips utterance ----------

    async def test_post_lesson_event_renames_result_to_outcome_and_strips_utterance(
        self,
    ):
        mac = _load_real_manage_api_client()

        class _FakeResp:
            def __init__(self):
                self.status_code = 202
                self.content = b"{}"

            def raise_for_status(self):
                return None

            def json(self):
                return {"data": {}}

            async def aclose(self):
                return None

        class _FakeClient:
            def __init__(self):
                self.calls = []

            async def request(self, method, url, **kwargs):
                self.calls.append((method, url, kwargs))
                return _FakeResp()

        client = _FakeClient()
        batch = {
            "assignmentId": "a",
            "lessonId": "w01-d01-barn-say-it",
            "lessonVersion": 3,
            "sessionId": "s",
            "events": [
                {
                    "type": "step_completed",
                    "sequence": 4,
                    "stepId": "s4",
                    "result": "success",
                    "detail": {"tapTargetHit": True, "utterance": "Yes! barn!"},
                }
            ],
        }

        await mac.post_lesson_event(client, "http://b/v1", "dev1", batch)

        self.assertEqual(len(client.calls), 1)
        method, url, kwargs = client.calls[0]
        self.assertEqual(method, "POST")
        self.assertTrue(url.endswith("/devices/dev1/lesson-events"), url)
        posted = kwargs["json"]
        ev = posted["events"][0]
        self.assertEqual(ev["outcome"], "success")
        self.assertNotIn("result", ev)
        self.assertEqual(ev["detail"], {"tapTargetHit": True})
        self.assertNotIn("utterance", ev["detail"])

    # 10) S13: runtime brackets the preload window for the voice-latency alarm ----

    async def test_preload_window_bracketed_for_alarm(self):
        alarm = _RecordingAlarm()
        conn = _FakeConn()
        rt = self._runtime(conn=conn, asset_cache=_FakeAssetCache(ready=True), alarm=alarm)
        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))  # prepare-ack -> kicks _run_preload
        await rt._preload_task

        # The window opened then closed exactly once around the download phase.
        self.assertEqual(alarm.events, [("active", True), ("active", False)])
        self.assertEqual(alarm.max_depth, 1)
        self.assertEqual(alarm.depth, 0)

    # 11) S13: the window still closes when preload FAILS (finally) ---------------

    async def test_preload_window_closes_on_preload_failure(self):
        from core.lesson.errors import AssetChecksumMismatch

        alarm = _RecordingAlarm()
        conn = _FakeConn()
        rt = self._runtime(
            conn=conn,
            asset_cache=_FakeAssetCache(preload_error=AssetChecksumMismatch("poster")),
            alarm=alarm,
        )
        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))
        await rt._preload_task

        self.assertEqual(rt.state, "FAILED")
        # Window opened AND closed despite the failure (finally guarantees it).
        self.assertEqual(alarm.events, [("active", True), ("active", False)])
        self.assertEqual(alarm.depth, 0)

    # 12) S13: a None alarm is a safe no-op (dark / unwired) ----------------------

    async def test_no_alarm_is_noop(self):
        conn = _FakeConn()
        rt = self._runtime(conn=conn, asset_cache=_FakeAssetCache(ready=True), alarm=None)
        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))
        await rt._preload_task  # must not raise
        self.assertIn("lesson_start", [f["type"] for f in self._sent_frames(conn)])

    # ── P5 multi-step playback ──────────────────────────────────────────────────

    async def _drive_to_running(self, conn, rt):
        """prepare -> prepare-ack -> preload -> start -> start-ack (=> RUNNING + s4)."""
        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))  # prepare-ack -> preload
        await rt._preload_task
        await rt.on_lesson_ack(_ack(2, 2))  # start-ack -> RUNNING + emits lesson_step s4

    # 13) a 2-step lesson plays ALL steps in order, one lesson_step per step --------

    async def test_multi_step_plays_all_steps_in_order(self):
        conn = _FakeConn()
        forwarder = _FakeForwarder()
        rt = self._runtime(
            conn=conn, manifest=_build_multistep_manifest(), forwarder=forwarder
        )
        await self._drive_to_running(conn, rt)

        # After start-ack, exactly ONE lesson_step (s4) is on the wire; s5 not yet.
        step_frames = [f for f in self._sent_frames(conn) if f["type"] == "lesson_step"]
        self.assertEqual([f["stepId"] for f in step_frames], ["s4"])
        self.assertEqual(rt.state, "RUNNING")

        # Complete the INTERACTIVE s4 ('model'): step-ack (S->F seq 3) + step_completed
        # progress (F->S seq 4) — model waits for BOTH the ack AND step_completed.
        await rt.on_lesson_ack(_ack(3, 3, step_id="s4"))
        await rt.on_lesson_progress(
            _progress(4, {"event": "step_completed", "stepType": "model",
                          "result": "success"}, step_id="s4")
        )

        # s4 completion advances to s5 — the SECOND lesson_step is emitted now, and
        # the run is NOT stopped yet (no lesson_stop while a step remains).
        types = [f["type"] for f in self._sent_frames(conn)]
        step_frames = [f for f in self._sent_frames(conn) if f["type"] == "lesson_step"]
        self.assertEqual([f["stepId"] for f in step_frames], ["s4", "s5"])
        self.assertNotIn("lesson_stop", types)
        self.assertEqual(rt.state, "RUNNING")

        # s5 lesson_step rides the next S->F sequence (4), preserving monotonicity.
        s5 = step_frames[1]
        self.assertEqual(s5["sequence"], 4)
        # s5 is the PASSIVE 'review' step.
        self.assertEqual(s5["body"]["stepType"], "review")

        # Complete the PASSIVE s5 ('review') with its ACK ALONE (body.acks 4, F->S
        # seq 5). A passive narration step gets NO step_completed from the firmware —
        # the ack IS its completion signal (auto-advance). Sending a progress here
        # would be wrong, so we must NOT.
        await rt.on_lesson_ack(_ack(4, 5, step_id="s5"))

        # Last step done on its ack alone -> lesson_stop (S->F seq 5) with the REAL
        # stepsCompleted=2 (interactive s4 + passive s5).
        sent = self._sent_frames(conn)
        stop = [f for f in sent if f["type"] == "lesson_stop"]
        self.assertEqual(len(stop), 1)
        self.assertEqual(stop[0]["sequence"], 5)
        self.assertEqual(rt.state, "RUNNING")  # COMPLETED only after stop-ack

        # stop-ack (body.acks 5, F->S seq 6 — no progress(s5) collapsed the F->S
        # stream by one) -> COMPLETED + lesson_completed forward.
        await rt.on_lesson_ack(_ack(5, 6))
        self.assertEqual(rt.state, "COMPLETED")

        completed = [
            b for b in forwarder.batches
            if b["events"] and b["events"][0].get("type") == "lesson_completed"
        ]
        self.assertTrue(completed, "lesson_completed not forwarded")
        self.assertEqual(completed[0]["events"][0]["summary"]["stepsCompleted"], 2)

    # 14) the full S->F sequence + per-step acks/progress match the additive thread -

    async def test_multi_step_sequence_matches_additive_fixture_thread(self):
        conn = _FakeConn(session_id=FIX["frames"]["lesson_prepare"]["sessionId"])
        rt = self._runtime(conn=conn, manifest=_build_multistep_manifest())
        await self._drive_to_running(conn, rt)
        # Interactive s4: ack + step_completed.
        await rt.on_lesson_ack(_ack(3, 3, step_id="s4"))
        await rt.on_lesson_progress(
            _progress(4, {"event": "step_completed", "stepType": "model",
                          "result": "success"}, step_id="s4")
        )
        # Passive s5 ('review'): ack ALONE auto-advances (no step_completed).
        await rt.on_lesson_ack(_ack(4, 5, step_id="s5"))
        # Stop-ack: F->S seq 6 (no progress(s5) -> stream collapsed by one vs the
        # earlier 'guided' draft that incorrectly emitted a progress for s5).
        await rt.on_lesson_ack(_ack(5, 6))

        # The S->F command stream the sender produced, in order.
        sent = self._sent_frames(conn)
        sf = [(f["type"], f["sequence"], f["stepId"]) for f in sent]
        self.assertEqual(
            sf,
            [
                ("lesson_prepare", 1, None),
                ("lesson_start", 2, None),
                ("lesson_step", 3, "s4"),
                ("lesson_step", 4, "s5"),
                ("lesson_stop", 5, None),
            ],
        )

        # The emitted s5 lesson_step is byte-consistent with the additive fixture.
        s5_sent = next(
            f for f in sent if f["type"] == "lesson_step" and f["stepId"] == "s5"
        )
        got = dict(s5_sent)
        got.pop("timestamp")
        want = copy.deepcopy(FIX["multiStep"]["frames"]["lesson_step_s5"])
        want.pop("timestamp")
        self.assertEqual(got, want, "s5 lesson_step not byte-consistent with fixture")

    # 15) per-step STEP_TIMEOUT on step 1 halts the run (does NOT advance to step 2) -

    async def test_per_step_timeout_on_first_step_halts_before_second(self):
        import asyncio

        conn = _FakeConn()
        manifest = _build_multistep_manifest()
        manifest["steps"][0]["timeoutSec"] = 0.05  # s4 times out
        rt = self._runtime(conn=conn, manifest=manifest)
        await self._drive_to_running(conn, rt)
        self.assertEqual(rt.state, "RUNNING")

        # Never ack s4 -> its per-step STEP_TIMEOUT fires -> FAILED, s5 never emitted.
        await asyncio.sleep(0.15)
        self.assertEqual(rt.state, "FAILED")
        self.assertEqual(rt.last_error.code, "STEP_TIMEOUT")

        step_ids = [
            f["stepId"] for f in self._sent_frames(conn) if f["type"] == "lesson_step"
        ]
        self.assertEqual(step_ids, ["s4"])  # s5 was never reached
        self.assertNotIn(
            "lesson_stop", [f["type"] for f in self._sent_frames(conn)]
        )
        # Distinctness preserved: STEP_TIMEOUT, not PROTOCOL_SEQUENCE_ERROR.
        codes = [
            f.get("body", {}).get("code")
            for f in self._sent_frames(conn)
            if f["type"] == "lesson_error"
        ]
        self.assertIn("STEP_TIMEOUT", codes)
        self.assertNotIn("PROTOCOL_SEQUENCE_ERROR", codes)

    # 16) a step's completion latch never leaks into the next step ------------------

    async def test_step_latches_reset_between_steps(self):
        # Uses an INTERACTIVE second step (listen) so the "ack alone must not stop"
        # invariant holds — a passive second step would (correctly) auto-advance on
        # its ack, which is covered separately by the passive auto-advance tests.
        conn = _FakeConn()
        manifest = _build_two_interactive_step_manifest()  # model (s4) -> listen (s5)
        rt = self._runtime(conn=conn, manifest=manifest)
        await self._drive_to_running(conn, rt)

        # Complete s4 fully.
        await rt.on_lesson_ack(_ack(3, 3, step_id="s4"))
        await rt.on_lesson_progress(
            _progress(4, {"event": "step_completed", "result": "success"}, step_id="s4")
        )
        # s5 is now outstanding with FRESH latches: not acked, not completed.
        self.assertFalse(rt._step_acked)
        self.assertFalse(rt._step_completed)
        self.assertEqual(rt._step_id, "s5")
        # No premature stop: the INTERACTIVE s5's ack alone (without its
        # step_completed) must not stop — it still waits for the progress.
        await rt.on_lesson_ack(_ack(4, 5, step_id="s5"))
        self.assertNotIn(
            "lesson_stop", [f["type"] for f in self._sent_frames(conn)]
        )

    # ── P5 PASSIVE-step auto-advance (playability defect fix) ───────────────────

    # 17) a single PASSIVE step auto-advances on its ack — no hang, no STEP_TIMEOUT --

    async def test_passive_step_auto_advances_on_ack_without_step_completed(self):
        # A lesson whose ONLY step is passive narration (greeting). The firmware
        # acks the lesson_step but — by contract — NEVER sends a step_completed for
        # it. Pre-fix this hung forever in RUNNING (the per-step timeout is cancelled
        # on ack, so it can no longer fire). It must now auto-advance to lesson_stop
        # on the ack ALONE, with stepsCompleted=1 and NO STEP_TIMEOUT.
        import asyncio

        conn = _FakeConn()
        forwarder = _FakeForwarder()
        manifest = _build_steps_manifest([("s4", "greeting")])
        # A short timeoutSec proves the run does NOT depend on / get failed by it.
        manifest["steps"][0]["timeoutSec"] = 0.05
        rt = self._runtime(conn=conn, manifest=manifest, forwarder=forwarder)
        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))  # prepare-ack -> preload
        await rt._preload_task
        await rt.on_lesson_ack(_ack(2, 2))  # start-ack -> RUNNING + emits greeting step
        self.assertEqual(rt.state, "RUNNING")

        # Ack the passive step (S->F seq 3). This ALONE must finish it -> lesson_stop.
        await rt.on_lesson_ack(_ack(3, 3, step_id="s4"))
        types = [f["type"] for f in self._sent_frames(conn)]
        self.assertIn("lesson_stop", types)
        # Crucially: NO step_completed was ever delivered, yet the step completed.
        self.assertEqual(rt._steps_completed, 1)

        # Let any leftover timeout timer elapse: it must NOT fail an acked passive step.
        await asyncio.sleep(0.1)
        self.assertNotEqual(rt.state, "FAILED")
        codes = [
            f.get("body", {}).get("code")
            for f in self._sent_frames(conn)
            if f["type"] == "lesson_error"
        ]
        self.assertNotIn("STEP_TIMEOUT", codes)

        # stop-ack (body.acks 4, F->S seq 4) -> COMPLETED with stepsCompleted=1.
        await rt.on_lesson_ack(_ack(4, 4))
        self.assertEqual(rt.state, "COMPLETED")
        completed = [
            b for b in forwarder.batches
            if b["events"] and b["events"][0].get("type") == "lesson_completed"
        ]
        self.assertTrue(completed)
        self.assertEqual(completed[0]["events"][0]["summary"]["stepsCompleted"], 1)

    # 18) greeting -> model -> celebrate plays to lesson_stop (mixed classes) --------

    async def test_greeting_model_celebrate_plays_to_stop(self):
        # The authored shape that motivated the fix: a passive greeting, an
        # interactive model, then a passive celebrate. Pre-fix the run HUNG on the
        # greeting (passive, no step_completed). It must now play ALL three to
        # lesson_stop with stepsCompleted=3.
        conn = _FakeConn()
        forwarder = _FakeForwarder()
        manifest = _build_steps_manifest(
            [("s1", "greeting"), ("s4", "model"), ("s9", "celebrate")]
        )
        rt = self._runtime(conn=conn, manifest=manifest, forwarder=forwarder)
        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))  # prepare-ack
        await rt._preload_task
        await rt.on_lesson_ack(_ack(2, 2))  # start-ack -> RUNNING + greeting (S->F 3)

        # First lesson_step on the wire is the greeting; nothing else yet.
        step_ids = [
            f["stepId"] for f in self._sent_frames(conn) if f["type"] == "lesson_step"
        ]
        self.assertEqual(step_ids, ["s1"])

        # PASSIVE greeting: ack ALONE auto-advances to the model step (S->F 4).
        await rt.on_lesson_ack(_ack(3, 3, step_id="s1"))
        step_ids = [
            f["stepId"] for f in self._sent_frames(conn) if f["type"] == "lesson_step"
        ]
        self.assertEqual(step_ids, ["s1", "s4"])
        self.assertNotIn("lesson_stop", [f["type"] for f in self._sent_frames(conn)])

        # INTERACTIVE model: requires BOTH ack (S->F 4) AND step_completed (F->S 5).
        await rt.on_lesson_ack(_ack(4, 4, step_id="s4"))
        self.assertNotIn("lesson_stop", [f["type"] for f in self._sent_frames(conn)])
        await rt.on_lesson_progress(
            _progress(5, {"event": "step_completed", "stepType": "model",
                          "result": "success"}, step_id="s4")
        )
        # model done -> celebrate emitted (S->F 5).
        step_ids = [
            f["stepId"] for f in self._sent_frames(conn) if f["type"] == "lesson_step"
        ]
        self.assertEqual(step_ids, ["s1", "s4", "s9"])
        self.assertNotIn("lesson_stop", [f["type"] for f in self._sent_frames(conn)])

        # PASSIVE celebrate: ack ALONE (body.acks 5, F->S seq 6) -> lesson_stop.
        await rt.on_lesson_ack(_ack(5, 6, step_id="s9"))
        stop = [f for f in self._sent_frames(conn) if f["type"] == "lesson_stop"]
        self.assertEqual(len(stop), 1)
        self.assertEqual(rt._steps_completed, 3)

        # stop-ack -> COMPLETED with stepsCompleted=3.
        await rt.on_lesson_ack(_ack(6, 7))
        self.assertEqual(rt.state, "COMPLETED")
        completed = [
            b for b in forwarder.batches
            if b["events"] and b["events"][0].get("type") == "lesson_completed"
        ]
        self.assertTrue(completed)
        self.assertEqual(completed[0]["events"][0]["summary"]["stepsCompleted"], 3)

    # 18b) a STRAY passive step_completed must NOT latch the next (interactive) step -

    async def test_stray_passive_step_completed_does_not_latch_interactive_step(self):
        # Latch-contamination regression. With the REAL firmware a PASSIVE step is
        # auto-advanced by the runtime on its ACK, but the firmware ALSO emits an
        # UNCONDITIONAL step_completed for that SAME passive step (lesson_handler.cc
        # :327 ack then :339 step_completed). WS in-order delivery means the passive
        # ack is processed first (passive auto-advances, the INTERACTIVE step is now
        # in flight and the latches are reset), and only THEN the stray passive
        # step_completed lands. If that stray progress sets _step_completed=True on the
        # now-current interactive step, the interactive step would finish on its ack
        # ALONE — skipping its OWN step_completed and cascading an off-by-one for the
        # rest of the run. The stepId guard on step_completed must drop the stale event.
        #
        # Tests 20/21 do NOT cover this: 20 sends only the passive ack (no stray
        # step_completed), and 21 has no preceding passive step to auto-advance.
        conn = _FakeConn()
        forwarder = _FakeForwarder()
        manifest = _build_steps_manifest([("s1", "greeting"), ("s4", "model")])
        rt = self._runtime(conn=conn, manifest=manifest, forwarder=forwarder)
        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))  # prepare-ack
        await rt._preload_task
        await rt.on_lesson_ack(_ack(2, 2))  # start-ack -> RUNNING + greeting (S->F 3)

        step_ids = [
            f["stepId"] for f in self._sent_frames(conn) if f["type"] == "lesson_step"
        ]
        self.assertEqual(step_ids, ["s1"])

        # PASSIVE greeting: its ACK ALONE auto-advances to the INTERACTIVE model step
        # (S->F 4). The greeting's step_completed has NOT arrived yet (WS in-order).
        await rt.on_lesson_ack(_ack(3, 3, step_id="s1"))
        step_ids = [
            f["stepId"] for f in self._sent_frames(conn) if f["type"] == "lesson_step"
        ]
        self.assertEqual(step_ids, ["s1", "s4"])
        self.assertEqual(rt._step_id, "s4")  # model is now the in-flight step.

        # STRAY passive step_completed for the ALREADY-ADVANCED greeting (F->S env
        # seq 4). The firmware emitted it unconditionally; it lands AFTER the model is
        # in flight and its latch has been reset. It MUST be ignored for completion —
        # its stepId ('s1') is not the current step ('s4'), so the latch stays clear.
        await rt.on_lesson_progress(
            _progress(4, {"event": "step_completed", "stepType": "greeting",
                          "result": "success"}, step_id="s1")
        )
        self.assertFalse(
            rt._step_completed,
            "stray passive step_completed must NOT latch the interactive step",
        )

        # INTERACTIVE model ACK ALONE (body.acks 4, F->S env seq 5). Pre-fix the stray
        # latch above would make this finish the model and emit lesson_stop here; with
        # the guard the model still WAITS for its OWN step_completed -> no stop yet.
        await rt.on_lesson_ack(_ack(4, 5, step_id="s4"))
        self.assertNotIn("lesson_stop", [f["type"] for f in self._sent_frames(conn)])
        self.assertEqual(rt.state, "RUNNING")

        # The model's OWN step_completed (matching stepId 's4', F->S env seq 6) is what
        # finishes it -> lesson_stop with the REAL stepsCompleted=2.
        await rt.on_lesson_progress(
            _progress(6, {"event": "step_completed", "stepType": "model",
                          "result": "success"}, step_id="s4")
        )
        stop = [f for f in self._sent_frames(conn) if f["type"] == "lesson_stop"]
        self.assertEqual(len(stop), 1)
        self.assertEqual(rt._steps_completed, 2)

        # stop-ack (body.acks 5, F->S env seq 7) -> COMPLETED with stepsCompleted=2.
        await rt.on_lesson_ack(_ack(5, 7))
        self.assertEqual(rt.state, "COMPLETED")
        completed = [
            b for b in forwarder.batches
            if b["events"] and b["events"][0].get("type") == "lesson_completed"
        ]
        self.assertTrue(completed)
        self.assertEqual(completed[0]["events"][0]["summary"]["stepsCompleted"], 2)

    # 19) an UN-acked passive step still STEP_TIMEOUTs (render stall, not auto-pass) --

    async def test_passive_step_without_ack_still_times_out(self):
        # Auto-advance is gated on the ACK (render confirmed). If a passive step is
        # never acked, that is a genuine render stall and MUST still STEP_TIMEOUT —
        # the fix must not turn an un-rendered passive step into a silent pass.
        import asyncio

        conn = _FakeConn()
        manifest = _build_steps_manifest([("s4", "greeting")])
        manifest["steps"][0]["timeoutSec"] = 0.05
        rt = self._runtime(conn=conn, manifest=manifest)
        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))
        await rt._preload_task
        await rt.on_lesson_ack(_ack(2, 2))  # RUNNING + emits greeting step
        self.assertEqual(rt.state, "RUNNING")

        # Never ack the step -> its STEP_TIMEOUT fires (un-rendered, not auto-passed).
        await asyncio.sleep(0.15)
        self.assertEqual(rt.state, "FAILED")
        self.assertEqual(rt.last_error.code, "STEP_TIMEOUT")
        self.assertNotIn(
            "lesson_stop", [f["type"] for f in self._sent_frames(conn)]
        )

    # ── L3 P1: explicit completionClass classifier (+ v1 fallback) ──────────────

    # 20) an AUTHOR-DEFINED type with completionClass='passive' auto-advances on ack --

    async def test_author_type_passive_class_auto_advances_on_ack(self):
        # 'songBreak' is UNKNOWN to PASSIVE_STEP_TYPES -> pre-L3 it would be
        # misclassified INTERACTIVE and hang waiting for a step_completed that never
        # comes. With completionClass='passive' it must AUTO-ADVANCE on its ack alone
        # -> lesson_stop, stepsCompleted=1, NO hang and NO STEP_TIMEOUT.
        import asyncio

        conn = _FakeConn()
        forwarder = _FakeForwarder()
        manifest = _build_class_steps_manifest([("s4", "songBreak", "passive")])
        manifest["steps"][0]["timeoutSec"] = 0.05  # proves the run ignores the dwell
        rt = self._runtime(conn=conn, manifest=manifest, forwarder=forwarder)
        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))  # prepare-ack -> preload
        await rt._preload_task
        await rt.on_lesson_ack(_ack(2, 2))  # start-ack -> RUNNING + emits songBreak
        self.assertEqual(rt.state, "RUNNING")

        # FIX#7: the emitted lesson_step.body must FORWARD the author's explicit
        # completionClass (renderer-v1 additive field) so the firmware uses it as the
        # authoritative classifier instead of re-deriving from the (author-defined,
        # PASSIVE_STEP_TYPES-unknown) stepType.
        step_frame = next(
            f for f in self._sent_frames(conn) if f["type"] == "lesson_step"
        )
        self.assertEqual(step_frame["body"]["completionClass"], "passive")
        self.assertEqual(step_frame["body"]["stepType"], "songBreak")

        # Ack ALONE finishes the passive author step -> lesson_stop (no step_completed).
        await rt.on_lesson_ack(_ack(3, 3, step_id="s4"))
        self.assertIn("lesson_stop", [f["type"] for f in self._sent_frames(conn)])
        self.assertEqual(rt._steps_completed, 1)

        # The leftover dwell timer must NOT fail an acked passive step.
        await asyncio.sleep(0.1)
        self.assertNotEqual(rt.state, "FAILED")
        codes = [
            f.get("body", {}).get("code")
            for f in self._sent_frames(conn)
            if f["type"] == "lesson_error"
        ]
        self.assertNotIn("STEP_TIMEOUT", codes)

        await rt.on_lesson_ack(_ack(4, 4))  # stop-ack
        self.assertEqual(rt.state, "COMPLETED")

    # 21) an AUTHOR-DEFINED type with completionClass='interactive' waits for completed -

    async def test_author_type_interactive_class_waits_for_step_completed(self):
        # 'puzzle' is also UNKNOWN to PASSIVE_STEP_TYPES. With
        # completionClass='interactive' it MUST wait for BOTH the ack AND the firmware
        # step_completed — its ack alone must NOT auto-advance / stop the run.
        conn = _FakeConn()
        manifest = _build_class_steps_manifest([("s4", "puzzle", "interactive")])
        rt = self._runtime(conn=conn, manifest=manifest)
        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))
        await rt._preload_task
        await rt.on_lesson_ack(_ack(2, 2))  # RUNNING + emits puzzle step
        self.assertEqual(rt.state, "RUNNING")

        # Ack ALONE must NOT stop: interactive waits for step_completed.
        await rt.on_lesson_ack(_ack(3, 3, step_id="s4"))
        self.assertNotIn("lesson_stop", [f["type"] for f in self._sent_frames(conn)])
        self.assertEqual(rt.state, "RUNNING")

        # The firmware step_completed (F->S 4) is what finishes it -> lesson_stop.
        await rt.on_lesson_progress(
            _progress(4, {"event": "step_completed", "stepType": "puzzle",
                          "result": "success"}, step_id="s4")
        )
        self.assertIn("lesson_stop", [f["type"] for f in self._sent_frames(conn)])
        self.assertEqual(rt._steps_completed, 1)

        await rt.on_lesson_ack(_ack(4, 5))  # stop-ack
        self.assertEqual(rt.state, "COMPLETED")

    # 22) NO completionClass -> v1 PASSIVE_STEP_TYPES fallback (unchanged behavior) ---

    async def test_no_completion_class_falls_back_to_passive_step_types(self):
        # A step with NO completionClass classifies via PASSIVE_STEP_TYPES, exactly
        # like the pre-L3 builtins. 'greeting' (passive, in the set) auto-advances on
        # ack; 'model' (interactive, not in the set) waits for step_completed. This
        # pins that the fallback path is byte-for-behavior identical to v1.
        conn = _FakeConn()
        forwarder = _FakeForwarder()
        manifest = _build_class_steps_manifest(
            [("s1", "greeting", None), ("s4", "model", None)]
        )
        rt = self._runtime(conn=conn, manifest=manifest, forwarder=forwarder)
        await rt.start()
        await rt.on_lesson_ack(_ack(1, 1))
        await rt._preload_task
        await rt.on_lesson_ack(_ack(2, 2))  # RUNNING + greeting (S->F 3)

        # FIX#7: a manifest step WITHOUT completionClass must OMIT the field from the
        # wire body (not emit completionClass=null), keeping the body byte-identical to
        # the frozen v1 fixtures whose firmware then falls back to PASSIVE_STEP_TYPES.
        greeting_frame = next(
            f for f in self._sent_frames(conn) if f["type"] == "lesson_step"
        )
        self.assertNotIn("completionClass", greeting_frame["body"])

        # PASSIVE greeting (fallback, no completionClass): ack ALONE auto-advances.
        await rt.on_lesson_ack(_ack(3, 3, step_id="s1"))
        step_ids = [
            f["stepId"] for f in self._sent_frames(conn) if f["type"] == "lesson_step"
        ]
        self.assertEqual(step_ids, ["s1", "s4"])
        self.assertNotIn("lesson_stop", [f["type"] for f in self._sent_frames(conn)])

        # INTERACTIVE model (fallback): ack alone must NOT stop; waits for completed.
        await rt.on_lesson_ack(_ack(4, 4, step_id="s4"))
        self.assertNotIn("lesson_stop", [f["type"] for f in self._sent_frames(conn)])
        await rt.on_lesson_progress(
            _progress(5, {"event": "step_completed", "stepType": "model",
                          "result": "success"}, step_id="s4")
        )
        self.assertIn("lesson_stop", [f["type"] for f in self._sent_frames(conn)])
        self.assertEqual(rt._steps_completed, 2)

        await rt.on_lesson_ack(_ack(5, 6))  # stop-ack
        self.assertEqual(rt.state, "COMPLETED")

    # ── L3 P3 renderer-capability negotiation ────────────────────────────────────

    async def test_v1_device_default_capability_set(self):
        # A v1 device's advertised renderer ('teebot-lesson-renderer.v1') yields the
        # v1-only capability set; a device that OMITS renderer defaults to the same.
        from core.lesson.errors import device_renderer_capabilities

        rt = self._runtime(conn=_FakeConn())  # default features = v1 renderer string
        self.assertEqual(rt.renderer_capabilities, ["teebot-lesson-renderer.v1"])
        # Default fallback when the field is absent (current firmware that omits it).
        self.assertEqual(
            device_renderer_capabilities({"lesson": True}),
            ["teebot-lesson-renderer.v1"],
        )
        self.assertEqual(
            device_renderer_capabilities(None), ["teebot-lesson-renderer.v1"]
        )

    async def test_negotiated_version_stamps_v1_for_v1_manifest_unchanged(self):
        # A served v1 manifest to a v1 device negotiates v1, so EVERY outbound
        # envelope stamps protocolVersion 'teebot-lesson-renderer.v1' — byte-identical
        # to the hardcoded-constant behaviour (the happy-thread fixture test still
        # passes; this pins the stamp value explicitly post-negotiation).
        conn = _FakeConn(session_id=FIX["frames"]["lesson_prepare"]["sessionId"])
        rt = self._runtime(conn=conn)
        await rt.start()
        self.assertEqual(rt.negotiated_version, "teebot-lesson-renderer.v1")
        await rt.on_lesson_ack(_ack(1, 1))
        await rt._preload_task
        await rt.on_lesson_ack(_ack(2, 2))  # start-ack -> RUNNING + lesson_step
        versions = {
            f["protocolVersion"] for f in self._sent_frames(conn)
        }
        self.assertEqual(versions, {"teebot-lesson-renderer.v1"})

    async def test_served_version_outside_device_capability_set_rejected(self):
        # SIMULATED future-renderer manifest: the backend served a v2 manifestVersion
        # to a v1-only device. The start() gate rejects it (LESSON_VERSION_UNSUPPORTED),
        # NO crash, NO frame on the wire — the structural guard that a v1-only device
        # is never handed a v2 manifest it cannot render.
        conn = _FakeConn()  # v1-only capability set
        manifest = _build_manifest()
        manifest["manifestVersion"] = "teebot-lesson-renderer.v2"
        rt = self._runtime(conn=conn, manifest=manifest)
        with self.assertRaises(LessonError) as ctx:
            await rt.start()
        self.assertEqual(ctx.exception.code, "LESSON_VERSION_UNSUPPORTED")
        self.assertEqual(conn.websocket.sent, [])  # nothing emitted

    async def test_device_advertising_v2_negotiates_and_stamps_v2(self):
        # Forward-looking: a device advertising BOTH v1+v2 (list-form renderer)
        # served a v2 manifest negotiates v2 and stamps it. This proves the
        # negotiation is real (not hardcoded v1) while today's devices stay v1.
        conn = _FakeConn(
            features={
                "lesson": True,
                "renderer": ["teebot-lesson-renderer.v1", "teebot-lesson-renderer.v2"],
            }
        )
        manifest = _build_manifest()
        manifest["manifestVersion"] = "teebot-lesson-renderer.v2"
        rt = self._runtime(conn=conn, manifest=manifest)
        await rt.start()
        self.assertEqual(rt.negotiated_version, "teebot-lesson-renderer.v2")
        prepare = self._sent_frames(conn)[0]
        self.assertEqual(prepare["type"], "lesson_prepare")
        self.assertEqual(prepare["protocolVersion"], "teebot-lesson-renderer.v2")


# ── L3 P3 manifest-fetch capability forwarding ───────────────────────────────────


class _CapRecordingResponse:
    """Minimal httpx-Response stand-in for get_lesson_manifest."""

    def __init__(self, manifest, etag):
        self._manifest = manifest
        self.headers = {"ETag": etag}
        # get_lesson_manifest now routes through _lesson_request_with_retry, which
        # inspects status_code/content for the 204/empty short-circuit. A real
        # httpx.Response carries both, so the stand-in must too.
        self.status_code = 200
        self.content = b'{"data": {"manifest": {}}}'

    def raise_for_status(self):
        return None

    def json(self):
        return {"data": {"manifest": self._manifest}}

    async def aclose(self):
        return None


class _CapRecordingClient:
    """Records the params + headers get_lesson_manifest puts on the wire."""

    def __init__(self, manifest, etag='"lesson-3-espTft-9b1f7c2a"'):
        self._manifest = manifest
        self._etag = etag
        self.calls = []

    async def request(self, method, url, params=None, headers=None):
        self.calls.append(
            {"method": method, "url": url, "params": params or {}, "headers": headers or {}}
        )
        return _CapRecordingResponse(self._manifest, self._etag)


class LessonManifestCapabilityFetchTest(unittest.IsolatedAsyncioTestCase):
    """Exercises the REAL config.manage_api_client.get_lesson_manifest (a fresh
    copy loaded from disk) to pin how the device capability set is forwarded."""

    def setUp(self):
        self.mac = _load_real_manage_api_client()
        self.manifest = _build_manifest()

    async def test_capabilities_forwarded_as_query_param_and_header(self):
        client = _CapRecordingClient(self.manifest)
        caps = ["teebot-lesson-renderer.v1"]
        manifest, etag = await self.mac.get_lesson_manifest(
            client,
            "http://backend.test/v1",
            "w01-d01-barn-say-it",
            "espTft",
            renderer_capabilities=caps,
        )
        self.assertEqual(manifest, self.manifest)
        self.assertTrue(etag)
        call = client.calls[0]
        # Default v1 capability set forwarded BOTH ways (query param + header).
        self.assertEqual(call["params"].get("profile"), "espTft")
        self.assertEqual(
            call["params"].get("rendererCapabilities"), "teebot-lesson-renderer.v1"
        )
        self.assertEqual(
            call["headers"].get("X-Renderer-Capabilities"), "teebot-lesson-renderer.v1"
        )

    async def test_multiple_capabilities_comma_joined(self):
        client = _CapRecordingClient(self.manifest)
        caps = ["teebot-lesson-renderer.v1", "teebot-lesson-renderer.v2"]
        await self.mac.get_lesson_manifest(
            client, "http://backend.test/v1", "L", "espTft", renderer_capabilities=caps
        )
        call = client.calls[0]
        joined = "teebot-lesson-renderer.v1,teebot-lesson-renderer.v2"
        self.assertEqual(call["params"].get("rendererCapabilities"), joined)
        self.assertEqual(call["headers"].get("X-Renderer-Capabilities"), joined)

    async def test_backward_compat_no_capabilities_omits_param_and_header(self):
        # Older call sites / tests that omit renderer_capabilities -> request is
        # byte-identical to today's v1 call: NO rendererCapabilities param, NO header.
        client = _CapRecordingClient(self.manifest)
        await self.mac.get_lesson_manifest(
            client, "http://backend.test/v1", "L", "espTft"
        )
        call = client.calls[0]
        self.assertEqual(call["params"], {"profile": "espTft"})
        self.assertNotIn("X-Renderer-Capabilities", call["headers"])


# ── manifest leg shares the transient-retry path (MED: bare request had none) ────


class _RetryingManifestResponse:
    """httpx-Response stand-in that raises_for_status -> HTTPStatusError once."""

    def __init__(self, manifest, etag, status_code):
        import httpx

        self._manifest = manifest
        self.headers = {"ETag": etag}
        self.status_code = status_code
        self.content = b'{"data": {"manifest": {}}}'
        self._httpx = httpx

    def raise_for_status(self):
        if self.status_code >= 400:
            raise self._httpx.HTTPStatusError(
                "server error",
                request=self._httpx.Request("GET", "http://backend.test/v1/x"),
                response=self._httpx.Response(self.status_code),
            )
        return None

    def json(self):
        return {"data": {"manifest": self._manifest}}

    async def aclose(self):
        return None


class _RetryingManifestClient:
    """Yields a 500 on the first manifest request, then a 200 — proving the leg
    now retries transient failures instead of aborting the whole pull."""

    def __init__(self, manifest, etag='"lesson-3-espTft-9b1f7c2a"'):
        self._manifest = manifest
        self._etag = etag
        self.attempts = 0

    async def request(self, method, url, params=None, headers=None):
        self.attempts += 1
        status = 500 if self.attempts == 1 else 200
        return _RetryingManifestResponse(self._manifest, self._etag, status)


class LessonManifestRetryTest(unittest.IsolatedAsyncioTestCase):
    """MED fix: get_lesson_manifest routes through _lesson_request_with_retry, so a
    single transient 5xx no longer aborts the pull; falsy lesson_id is early-guarded."""

    def setUp(self):
        self.mac = _load_real_manage_api_client()
        self.manifest = _build_manifest()

    async def test_transient_5xx_is_retried_then_succeeds(self):
        import asyncio as _asyncio

        client = _RetryingManifestClient(self.manifest)
        # Patch the real module's asyncio.sleep so the retry backoff is instant.
        orig_sleep = _asyncio.sleep

        async def _no_sleep(_delay):
            return None

        _asyncio.sleep = _no_sleep
        try:
            manifest, etag = await self.mac.get_lesson_manifest(
                client, "http://backend.test/v1", "L", "espTft"
            )
        finally:
            _asyncio.sleep = orig_sleep
        self.assertEqual(client.attempts, 2)  # one 500, one 200
        self.assertEqual(manifest, self.manifest)
        self.assertTrue(etag)

    async def test_falsy_lesson_id_short_circuits_without_request(self):
        class _ExplodingClient:
            async def request(self, *a, **k):
                raise AssertionError("must not hit the wire on falsy lesson_id")

        for bad in ("", None):
            manifest, etag = await self.mac.get_lesson_manifest(
                _ExplodingClient(), "http://backend.test/v1", bad, "espTft"
            )
            self.assertIsNone(manifest)
            self.assertIsNone(etag)


# ── L3 P3 pull-on-connect threads the device capability set to the fetch ──────────


class LessonPullOnConnectCapabilityTest(unittest.IsolatedAsyncioTestCase):
    def _patch_backend(self, assignment, manifest, etag='"lesson-3-espTft-9b1f7c2a"'):
        import config.manage_api_client as mac

        self.manifest_calls = []

        async def _get_assignment(client, base_url, device_id, *, token=None):
            return assignment

        async def _get_manifest(
            client, base_url, lesson_id, profile, *, token=None, renderer_capabilities=None
        ):
            self.manifest_calls.append(renderer_capabilities)
            return manifest, etag

        saved = (mac.get_current_assignment, mac.get_lesson_manifest)
        mac.get_current_assignment = _get_assignment
        mac.get_lesson_manifest = _get_manifest

        def _undo():
            mac.get_current_assignment, mac.get_lesson_manifest = saved

        return _undo

    async def test_v1_device_forwards_default_capability_set_to_fetch(self):
        from core.lesson.runtime import maybe_start_lesson_on_connect

        conn = _RepublishConn()  # default features = v1 renderer
        prep = FIX["frames"]["lesson_prepare"]
        assignment = {
            "assignmentId": prep["assignmentId"],
            "assignmentVersion": prep["body"]["assignmentVersion"],
            "lessonId": prep["lessonId"],
            "lessonVersion": prep["lessonVersion"],
            "profile": "espTft",
            "state": "ASSIGNED",
        }
        undo = self._patch_backend(assignment, _build_manifest())
        try:
            rt = await maybe_start_lesson_on_connect(conn)
        finally:
            undo()

        self.assertIsNotNone(rt)
        # The device's v1-only capability set was threaded to the manifest fetch.
        self.assertEqual(self.manifest_calls, [["teebot-lesson-renderer.v1"]])
        # And the negotiated stamp on the wire is v1 (unchanged behaviour today).
        prepare = json.loads(conn.websocket.sent[0])
        self.assertEqual(prepare["protocolVersion"], "teebot-lesson-renderer.v1")


# ── P5 republish-on-connect (no reconnect) ──────────────────────────────────────


class _RepublishConn:
    """Minimal conn for maybe_start_lesson_on_connect: lesson config + features +
    device_id + a settable is_realtime_busy + a pinnable lesson_runtime."""

    def __init__(self, *, busy=False, api_base="http://backend.test/v1"):
        self.logger = _DummyLogger()
        self.websocket = _FakeWebSocket()
        self.session_id = "sess_republish"
        self.device_id = "dev-republish"
        self.features = {"lesson": True, "renderer": "teebot-lesson-renderer.v1"}
        self.config = {"lesson": {"api_base": api_base, "runtime_enabled": True}}
        self.lesson_runtime = None
        self.lesson_voice_alarm = None
        self._busy = busy

    def is_realtime_busy(self):
        return self._busy

    def _disable_lesson_runtime(self):
        return None


class _PinnedRuntime:
    """Stand-in for an already-running lesson session, so the republish guard can
    read its version identity + verify teardown is invoked."""

    def __init__(self, *, assignment_id, lesson_version, assignment_version):
        self.assignment_id = assignment_id
        self.lesson_version = lesson_version
        self.assignment_version = assignment_version
        self.asset_cache = _EvictableCache()
        self.closed = False

    async def close(self):
        self.closed = True


class _EvictableCache:
    def __init__(self):
        self.evicted = False

    async def evict(self):
        self.evicted = True

    async def aclose(self):
        return None


class RepublishOnConnectTest(unittest.IsolatedAsyncioTestCase):
    def _patch_backend(self, assignment, manifest, etag='"lesson-3-espTft-9b1f7c2a"'):
        """Monkeypatch the REAL config.manage_api_client attributes the runtime
        resolves at call time (conftest does NOT stub this module). Returns an undo callable."""
        import config.manage_api_client as mac

        # Record the renderer_capabilities the runtime forwards to the manifest
        # fetch (L3 P3) so a test can assert the device capability set is threaded
        # through. The fake's signature MUST accept the keyword the production call
        # now passes (and stay tolerant of older positional-only callers).
        self.manifest_calls = []

        async def _get_assignment(client, base_url, device_id, *, token=None):
            return assignment

        async def _get_manifest(
            client, base_url, lesson_id, profile, *, token=None, renderer_capabilities=None
        ):
            self.manifest_calls.append(
                {
                    "lesson_id": lesson_id,
                    "profile": profile,
                    "renderer_capabilities": renderer_capabilities,
                }
            )
            return manifest, etag

        saved = (mac.get_current_assignment, mac.get_lesson_manifest)
        mac.get_current_assignment = _get_assignment
        mac.get_lesson_manifest = _get_manifest

        def _undo():
            mac.get_current_assignment, mac.get_lesson_manifest = saved

        return _undo

    def _assignment(self, *, lesson_version, assignment_version, state="ASSIGNED"):
        prep = FIX["frames"]["lesson_prepare"]
        return {
            "assignmentId": prep["assignmentId"],
            "assignmentVersion": assignment_version,
            "lessonId": prep["lessonId"],
            "lessonVersion": lesson_version,
            "profile": "espTft",
            "state": state,
        }

    async def test_unchanged_version_keeps_existing_session(self):
        from core.lesson.runtime import maybe_start_lesson_on_connect

        conn = _RepublishConn()
        prep = FIX["frames"]["lesson_prepare"]
        pinned = _PinnedRuntime(
            assignment_id=prep["assignmentId"], lesson_version=3, assignment_version=1
        )
        conn.lesson_runtime = pinned

        undo = self._patch_backend(
            self._assignment(lesson_version=3, assignment_version=1), _build_manifest()
        )
        try:
            result = await maybe_start_lesson_on_connect(conn)
        finally:
            undo()

        # Same version -> idempotent no-op: existing kept, NOT torn down, NO new frame.
        self.assertIs(result, pinned)
        self.assertIs(conn.lesson_runtime, pinned)
        self.assertFalse(pinned.closed)
        self.assertFalse(pinned.asset_cache.evicted)
        self.assertEqual(conn.websocket.sent, [])

    async def test_changed_version_evicts_and_repulls_without_reconnect(self):
        from core.lesson.runtime import maybe_start_lesson_on_connect

        conn = _RepublishConn()
        prep = FIX["frames"]["lesson_prepare"]
        pinned = _PinnedRuntime(
            assignment_id=prep["assignmentId"], lesson_version=3, assignment_version=1
        )
        conn.lesson_runtime = pinned

        # Author republished: lessonVersion 3 -> 4.
        undo = self._patch_backend(
            self._assignment(lesson_version=4, assignment_version=2),
            _build_manifest(),
            etag='"lesson-4-espTft-deadbeef"',
        )
        try:
            result = await maybe_start_lesson_on_connect(conn)
        finally:
            undo()

        # Old cache evicted + old runtime closed; a FRESH runtime is pinned and it
        # already emitted lesson_prepare for the new version (no reconnect needed).
        self.assertTrue(pinned.asset_cache.evicted)
        self.assertTrue(pinned.closed)
        self.assertIsNotNone(result)
        self.assertIsNot(result, pinned)
        self.assertIs(conn.lesson_runtime, result)
        self.assertEqual(result.lesson_version, 4)
        sent_types = [json.loads(p)["type"] for p in conn.websocket.sent]
        self.assertEqual(sent_types, ["lesson_prepare"])
        # The new version's cache dir is scoped by the new (version, checksum).
        self.assertIn("v4", result.asset_cache.cache_key)

    async def test_changed_version_deferred_while_voice_busy(self):
        from core.lesson.runtime import maybe_start_lesson_on_connect

        conn = _RepublishConn(busy=True)  # active voice turn
        prep = FIX["frames"]["lesson_prepare"]
        pinned = _PinnedRuntime(
            assignment_id=prep["assignmentId"], lesson_version=3, assignment_version=1
        )
        conn.lesson_runtime = pinned

        undo = self._patch_backend(
            self._assignment(lesson_version=4, assignment_version=2),
            _build_manifest(),
            etag='"lesson-4-espTft-deadbeef"',
        )
        try:
            result = await maybe_start_lesson_on_connect(conn)
        finally:
            undo()

        # Voice wins: the republish is DEFERRED — old session kept, nothing torn down,
        # no new frame put on the wire mid voice turn.
        self.assertIs(result, pinned)
        self.assertIs(conn.lesson_runtime, pinned)
        self.assertFalse(pinned.closed)
        self.assertFalse(pinned.asset_cache.evicted)
        self.assertEqual(conn.websocket.sent, [])


if __name__ == "__main__":
    unittest.main()
