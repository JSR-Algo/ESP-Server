"""T2.1 — ESP lesson runtime state-machine transition table.

One test per row of the transition table documented in
``docs/qa/ad-hoc/2026-08-06-t21-esp-runtime.md``. Covers the deep-dive case
checklist for T2.1: admission (prepare/start gates, activity lease, duplicate
start), the step loop (ack, step timeout, sequence faults), pause/resume/stop,
terminal error projection, and the ``{code,message,retryable}`` payload shape.

Async tests use ``unittest.IsolatedAsyncioTestCase`` (this repo does NOT use
pytest-asyncio markers). Fixtures/fakes are reused from the sibling harness
``test_lesson_runtime`` so this file adds cases, not a second harness.
"""

import asyncio
import json
import os
import sys
import unittest
from unittest.mock import AsyncMock

sys.path.insert(0, os.path.dirname(__file__))

import test_lesson_runtime as T  # noqa: E402  (sibling test harness)
from core.lesson.errors import (  # noqa: E402
    LESSON_VERSION_UNSUPPORTED,
    PROTOCOL_SEQUENCE_ERROR,
    STEP_TIMEOUT,
    LessonError,
)
from core.lesson.runtime import (  # noqa: E402
    S_COMPLETED,
    S_FAILED,
    S_PAUSED,
    S_PRELOADING,
    S_READY,
    S_RUNNING,
)


def _sent(conn):
    return [json.loads(payload) for payload in conn.websocket.sent]


def _frames_of_type(conn, frame_type):
    return [frame for frame in _sent(conn) if frame.get("type") == frame_type]


class LessonRuntimeTransitionTableTest(unittest.IsolatedAsyncioTestCase):
    """Rows 1-12 of the T2.1 transition table."""

    def _runtime(self, **kwargs):
        conn = kwargs.pop("conn", None) or T._FakeConn(
            session_id=T.FIX["frames"]["lesson_prepare"]["sessionId"]
        )
        from unittest.mock import patch

        from core.lesson.runtime import LessonRuntime

        with patch(
            "core.lesson.runtime.uuid.uuid4",
            return_value=T.FIX["frames"]["lesson_prepare"]["sessionId"],
        ):
            return LessonRuntime(
                conn,
                assignment=T._build_assignment(),
                manifest=kwargs.pop("manifest", None) or T._build_manifest(),
                asset_cache=kwargs.pop("asset_cache", None) or T._FakeAssetCache(ready=True),
                forwarder=kwargs.pop("forwarder", None) or T._FakeForwarder(),
                manifest_checksum=T._manifest_checksum(),
                **kwargs,
            )

    # ── admission: IDLE -> PRELOADING -> READY -> RUNNING ────────────────────

    async def test_manifest_version_outside_device_capability_fails_before_any_frame(self):
        """Row: lessonVersion/manifestVersion mismatch -> LESSON_VERSION_UNSUPPORTED,
        no partial render (no frame reaches the wire)."""
        manifest = T._build_manifest()
        manifest["manifestVersion"] = "teebot-lesson-renderer.v9"
        rt = self._runtime(manifest=manifest)

        with self.assertRaises(LessonError) as caught:
            await rt.preload_only()

        self.assertEqual(caught.exception.code, LESSON_VERSION_UNSUPPORTED)
        self.assertFalse(caught.exception.retryable)
        self.assertEqual(rt.conn.websocket.sent, [])
        self.assertEqual(rt.state, "IDLE")

    async def test_device_without_lesson_capability_never_sends_lesson_prepare(self):
        """Row: prepare admission gate — D-CAP-FLAG absence = no support."""
        conn = T._FakeConn(features={"lesson": False})
        rt = self._runtime(conn=conn)

        with self.assertRaises(LessonError) as caught:
            await rt.preload_only()

        self.assertEqual(caught.exception.code, LESSON_VERSION_UNSUPPORTED)
        self.assertEqual(conn.websocket.sent, [])

    async def test_lesson_start_is_not_emitted_before_preload_reports_ready(self):
        """Row: lesson_start before READY -> not emitted; the runtime stays PRELOADING."""
        rt = self._runtime(asset_cache=T._FakeAssetCache(ready=False))
        await rt.start()
        self.assertEqual(len(_frames_of_type(rt.conn, "lesson_prepare")), 1)

        await rt.on_lesson_ack(T._ack(1, 1))
        await rt._preload_task

        self.assertEqual(_frames_of_type(rt.conn, "lesson_start"), [])
        self.assertNotEqual(rt.state, S_RUNNING)

    async def test_duplicate_prepare_ack_does_not_open_a_second_session(self):
        """Row: duplicate lesson_start / re-ack of the same envelope -> idempotent."""
        rt = self._runtime()
        await rt.start()
        ack = T._ack(1, 1)

        await rt.on_lesson_ack(ack)
        await rt._preload_task
        starts_after_first = len(_frames_of_type(rt.conn, "lesson_start"))
        await rt.on_lesson_ack(ack)
        starts_after_duplicate = len(_frames_of_type(rt.conn, "lesson_start"))

        self.assertEqual(starts_after_first, 1)
        self.assertEqual(starts_after_duplicate, 1)
        self.assertEqual(
            {frame["sessionId"] for frame in _sent(rt.conn)}, {rt.session_id}
        )

    # ── step loop: sequencing faults ─────────────────────────────────────────

    async def test_inbound_sequence_gap_emits_retryable_protocol_sequence_error(self):
        """Row: sequence gap -> PROTOCOL_SEQUENCE_ERROR (retryable) and HOLD."""
        rt = self._runtime()
        rt.state = S_RUNNING
        rt._last_inbound_sequence = 4

        self.assertEqual(await rt._accept_inbound(9), "gap")

        errors = _frames_of_type(rt.conn, "lesson_error")
        self.assertEqual(len(errors), 1)
        body = errors[0]["body"]
        self.assertEqual(body["code"], PROTOCOL_SEQUENCE_ERROR)
        self.assertTrue(body["retryable"])
        self.assertEqual(body["context"], {"expected": 5, "got": 9})
        # HOLD: the gap does not advance the inbound cursor.
        self.assertEqual(rt._last_inbound_sequence, 4)

    async def test_duplicate_and_stale_inbound_sequences_are_idempotent_no_ops(self):
        """Row: duplicate (sequence <= last) -> no-op, never a sequence error."""
        rt = self._runtime()
        rt._last_inbound_sequence = 7

        self.assertEqual(await rt._accept_inbound(7), "duplicate")
        self.assertEqual(await rt._accept_inbound(3), "duplicate")

        self.assertEqual(_frames_of_type(rt.conn, "lesson_error"), [])
        self.assertEqual(rt._last_inbound_sequence, 7)

    async def test_ack_for_unknown_outstanding_sequence_is_a_no_op(self):
        """Row: unknown stepId / unknown acks value -> idempotent no-op.

        DELIBERATE: an ack whose ``body.acks`` matches no outstanding frame is
        indistinguishable from a duplicate re-ack, so it is dropped rather than
        raising PROTOCOL_SEQUENCE_ERROR. Ordering faults are detected on the
        ENVELOPE ``sequence`` (see the gap test above), not on ``body.acks``.
        """
        rt = self._runtime()
        rt.state = S_RUNNING
        rt._on_frame_acked = AsyncMock()

        await rt.on_lesson_ack(T._ack(4242, 1, step_id="s4", extra={"acks": 4242}))

        rt._on_frame_acked.assert_not_awaited()
        self.assertEqual(_frames_of_type(rt.conn, "lesson_error"), [])
        self.assertEqual(rt.state, S_RUNNING)

    async def test_ack_from_a_stale_pre_reconnect_session_id_is_ignored(self):
        """Row: ack carrying a superseded sessionId -> ignored, state untouched."""
        rt = self._runtime()
        rt.state = S_RUNNING
        rt._outstanding[9] = {"type": "lesson_step", "stepId": "s4", "body": {}}
        rt._on_frame_acked = AsyncMock()

        stale = T._ack(9, 1, step_id="s4", extra={"acks": 9})
        stale["sessionId"] = "00000000-0000-4000-8000-000000000000"
        await rt.on_lesson_ack(stale)

        rt._on_frame_acked.assert_not_awaited()
        self.assertIn(9, rt._outstanding)
        self.assertEqual(rt.state, S_RUNNING)

    # ── terminal projection ──────────────────────────────────────────────────

    async def test_step_timeout_fails_terminally_with_non_retryable_payload(self):
        """Row: step ack timeout -> STEP_TIMEOUT, RUNNING -> FAILED, one
        lesson_failed, state machine not left mid-step."""
        forwarder = T._FakeForwarder()
        rt = self._runtime(forwarder=forwarder)
        rt.state = S_RUNNING
        rt._step_id = "s4"
        rt._sleep = AsyncMock()

        rt._start_step_timeout(9, "s4", 0.01)
        await rt._step_timeout_task

        self.assertEqual(rt.state, S_FAILED)
        errors = _frames_of_type(rt.conn, "lesson_error")
        self.assertEqual(errors[-1]["body"]["code"], STEP_TIMEOUT)
        self.assertFalse(errors[-1]["body"]["retryable"])
        failed = [
            event
            for batch in forwarder.batches
            for event in batch["events"]
            if event["type"] == "lesson_failed"
        ]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["code"], STEP_TIMEOUT)

    async def test_every_emitted_lesson_error_carries_code_message_retryable(self):
        """Row: error payload shape parity with lesson-error-model.md."""
        rt = self._runtime()
        for err in (
            LessonError("PRELOAD_TIMEOUT", "preload stalled", retryable=True),
            LessonError("ASSET_CHECKSUM_MISMATCH", "bad bytes", retryable=False),
            LessonError(
                PROTOCOL_SEQUENCE_ERROR, "gap", retryable=True, context={"got": 4}
            ),
        ):
            await rt._emit_error(err)

        for frame in _frames_of_type(rt.conn, "lesson_error"):
            body = frame["body"]
            self.assertEqual(
                sorted(k for k in body if k != "context"),
                ["code", "message", "retryable"],
            )
            self.assertIsInstance(body["code"], str)
            self.assertIsInstance(body["message"], str)
            self.assertIsInstance(body["retryable"], bool)

    async def test_terminal_states_absorb_late_acks(self):
        """Row: stop during RUNNING emits its terminal exactly once; a late ack
        after a terminal state cannot resurrect the run."""
        rt = self._runtime()
        rt.state = S_FAILED
        rt._outstanding[9] = {"type": "lesson_step", "stepId": "s4", "body": {}}
        rt._on_frame_acked = AsyncMock()

        await rt.on_lesson_ack(T._ack(9, 1, step_id="s4", extra={"acks": 9}))

        rt._on_frame_acked.assert_not_awaited()
        self.assertEqual(rt.state, S_FAILED)

    # ── pause / resume ───────────────────────────────────────────────────────

    async def test_pause_is_a_no_op_outside_running_and_resume_outside_paused(self):
        """Row: pause/resume are state-guarded (no wire effect from IDLE)."""
        rt = self._runtime()
        rt.state = S_READY

        await rt.pause()
        self.assertEqual(rt.state, S_READY)

        rt.state = S_RUNNING
        result = await rt.resume()
        self.assertFalse(result.accepted)
        self.assertEqual(rt.state, S_RUNNING)

    async def test_step_timeout_does_not_fire_while_paused(self):
        """Row: pause during a step suspends the STEP_TIMEOUT verdict."""
        rt = self._runtime()
        rt.state = S_RUNNING
        rt._sleep = AsyncMock()

        rt._start_step_timeout(9, "s4", 0.01)
        rt.state = S_PAUSED
        await rt._step_timeout_task

        self.assertEqual(rt.state, S_PAUSED)
        self.assertEqual(_frames_of_type(rt.conn, "lesson_error"), [])


class LessonRuntimeStopReasonTest(unittest.IsolatedAsyncioTestCase):
    """T2.1 finding (plan §5): stop() emitted a reason outside the documented enum."""

    def _runtime(self):
        return LessonRuntimeTransitionTableTest._runtime(
            LessonRuntimeTransitionTableTest()
        )

    async def test_graceful_stop_emits_documented_cancelled_reason(self):
        """``STOPPED`` is outside the §4.6 enum and the firmware classifies any
        non-COMPLETED/SUCCEEDED/CANCELLED reason as a FAILURE, showing the child
        the sad-face "Bài học bị gián đoạn." UI for an administrative stop.
        A graceful stop must therefore ride the documented ``CANCELLED``.
        """
        rt = self._runtime()
        rt.state = S_RUNNING

        await rt.stop()

        stops = _frames_of_type(rt.conn, "lesson_stop")
        self.assertEqual(len(stops), 1)
        self.assertEqual(stops[0]["body"]["reason"], "CANCELLED")

    async def test_graceful_stop_ack_projects_lesson_abandoned_not_failed(self):
        """The terminal projection for a graceful stop stays ``lesson_abandoned``."""
        forwarder = T._FakeForwarder()
        rt = LessonRuntimeTransitionTableTest._runtime(
            LessonRuntimeTransitionTableTest(), forwarder=forwarder
        )
        rt.state = S_RUNNING
        await rt.stop()
        stop_seq = _frames_of_type(rt.conn, "lesson_stop")[0]["sequence"]

        await rt.on_lesson_ack(T._ack(stop_seq, 1, extra={"acks": stop_seq}))

        self.assertEqual(rt.state, S_COMPLETED)
        abandoned = [
            event
            for batch in forwarder.batches
            for event in batch["events"]
            if event["type"] == "lesson_abandoned"
        ]
        self.assertEqual(len(abandoned), 1)
        self.assertEqual(abandoned[0]["reason"], "cancelled")
        self.assertEqual(
            [
                event
                for batch in forwarder.batches
                for event in batch["events"]
                if event["type"] == "lesson_failed"
            ],
            [],
        )


class LessonRuntimeRetryableInboundErrorTest(unittest.IsolatedAsyncioTestCase):
    """T2.1 finding (plan §5): ``retryable`` was carried on the wire but never honored."""

    def _runtime(self, forwarder=None):
        return LessonRuntimeTransitionTableTest._runtime(
            LessonRuntimeTransitionTableTest(), forwarder=forwarder
        )

    def _lesson_error_frame(self, code, *, retryable):
        frame = T._ack(1, 1)
        frame["type"] = "lesson_error"
        frame["body"] = {
            "code": code,
            "message": f"{code} from firmware",
            "retryable": retryable,
        }
        return frame

    async def test_retryable_firmware_error_does_not_kill_a_bounded_run(self):
        """A firmware condition flagged ``retryable: true`` (e.g.
        LESSON_ASSET_MUTATION_ACTIVE) is transient. With a frame-ack retry timer
        already in flight the run must stay alive and let the bounded retry
        decide, not fail terminally on the first report."""
        forwarder = T._FakeForwarder()
        rt = self._runtime(forwarder=forwarder)
        rt.state = S_PRELOADING
        rt._outstanding[1] = {"type": "lesson_prepare", "stepId": None, "body": {}}
        rt._sleep = AsyncMock()
        rt._start_frame_ack_timeout("lesson_prepare", 1, None)
        self.addCleanup(rt._cancel_frame_ack_timeout)

        await rt.on_lesson_error(
            self._lesson_error_frame("LESSON_ASSET_MUTATION_ACTIVE", retryable=True)
        )

        self.assertEqual(rt.state, S_PRELOADING)
        self.assertEqual(rt.last_error.code, "LESSON_ASSET_MUTATION_ACTIVE")
        self.assertTrue(rt.last_error.retryable)
        self.assertEqual(
            [
                event
                for batch in forwarder.batches
                for event in batch["events"]
                if event["type"] == "lesson_failed"
            ],
            [],
        )

    async def test_non_retryable_firmware_error_still_fails_terminally(self):
        """Regression guard: honoring ``retryable`` must not weaken the
        non-retryable path."""
        forwarder = T._FakeForwarder()
        rt = self._runtime(forwarder=forwarder)
        rt.state = S_RUNNING
        rt._step_id = "s4"

        await rt.on_lesson_error(
            self._lesson_error_frame("LESSON_IDENTITY_INVALID", retryable=False)
        )

        self.assertEqual(rt.state, S_FAILED)
        failed = [
            event
            for batch in forwarder.batches
            for event in batch["events"]
            if event["type"] == "lesson_failed"
        ]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["code"], "LESSON_IDENTITY_INVALID")

    async def test_retryable_error_with_no_recovery_timer_still_fails_terminally(self):
        """A retryable report with NOTHING in flight to bound it would wedge the
        run forever, so it stays a terminal failure."""
        forwarder = T._FakeForwarder()
        rt = self._runtime(forwarder=forwarder)
        rt.state = S_RUNNING
        rt._step_id = "s4"

        await rt.on_lesson_error(
            self._lesson_error_frame("LESSON_SESSION_CONFLICT", retryable=True)
        )

        self.assertEqual(rt.state, S_FAILED)
        self.assertEqual(
            len(
                [
                    event
                    for batch in forwarder.batches
                    for event in batch["events"]
                    if event["type"] == "lesson_failed"
                ]
            ),
            1,
        )


class LessonRuntimeFaultContainmentTest(unittest.IsolatedAsyncioTestCase):
    """Checklist row: a runtime exception inside a step handler must fail the
    lesson TERMINALLY (with an event) — not wedge it — and the process survives."""

    def _runtime(self, forwarder=None):
        return LessonRuntimeTransitionTableTest._runtime(
            LessonRuntimeTransitionTableTest(), forwarder=forwarder
        )

    async def test_exception_in_step_ack_handler_fails_lesson_terminally(self):
        forwarder = T._FakeForwarder()
        rt = self._runtime(forwarder=forwarder)
        rt.state = S_RUNNING
        rt._step_id = "s4"
        rt._outstanding[9] = {"type": "lesson_step", "stepId": "s4", "body": {}}
        rt._on_frame_acked = AsyncMock(side_effect=RuntimeError("step handler blew up"))

        # The process survives: no exception escapes the state machine.
        await rt.on_lesson_ack(T._ack(9, 1, step_id="s4", extra={"acks": 9}))

        self.assertEqual(rt.state, S_FAILED)
        failed = [
            event
            for batch in forwarder.batches
            for event in batch["events"]
            if event["type"] == "lesson_failed"
        ]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["code"], "LESSON_RUNTIME_FAULT")

    async def test_fault_containment_emits_exactly_one_terminal_event(self):
        forwarder = T._FakeForwarder()
        rt = self._runtime(forwarder=forwarder)
        rt.state = S_RUNNING
        rt._outstanding[9] = {"type": "lesson_step", "stepId": "s4", "body": {}}
        rt._outstanding[10] = {"type": "lesson_step", "stepId": "s5", "body": {}}
        rt._on_frame_acked = AsyncMock(side_effect=RuntimeError("boom"))

        await rt.on_lesson_ack(T._ack(9, 1, step_id="s4", extra={"acks": 9}))
        await rt.on_lesson_ack(T._ack(10, 2, step_id="s5", extra={"acks": 10}))

        failed = [
            event
            for batch in forwarder.batches
            for event in batch["events"]
            if event["type"] == "lesson_failed"
        ]
        self.assertEqual(len(failed), 1)

    async def test_exception_in_progress_handler_is_contained_the_same_way(self):
        forwarder = T._FakeForwarder()
        rt = self._runtime(forwarder=forwarder)
        rt.state = S_RUNNING
        rt._step_id = "s4"
        rt._step_passive = True
        rt._maybe_finish_step = AsyncMock(side_effect=RuntimeError("progress blew up"))

        await rt.on_lesson_progress(
            T._progress(1, {"event": "step_completed"}, step_id="s4")
        )

        self.assertEqual(rt.state, S_FAILED)
        self.assertEqual(
            len(
                [
                    event
                    for batch in forwarder.batches
                    for event in batch["events"]
                    if event["type"] == "lesson_failed"
                ]
            ),
            1,
        )


class LessonRuntimePauseBudgetTest(unittest.IsolatedAsyncioTestCase):
    """Checklist row: resume after a pause that outlived the step-timeout budget."""

    def _runtime(self):
        return LessonRuntimeTransitionTableTest._runtime(
            LessonRuntimeTransitionTableTest()
        )

    async def test_resume_rearms_the_step_timeout_for_a_still_unacked_step(self):
        """A pause that outlives ``timeoutSec`` retires the timer (it returns
        early because the state is no longer RUNNING). Without re-arming, the
        resumed step has NO ack deadline and the run wedges in RUNNING forever.
        """
        rt = self._runtime()
        rt.state = S_RUNNING
        rt._step = {"id": "s4", "type": "listen"}
        rt._step_id = "s4"
        rt._step_seq = 9
        rt._step_acked = False
        rt._step_timeout_sec = 30.0
        rt._current_visual_request = {
            "state": "listen",
            "overlay_key": None,
            "motion_preset": None,
        }
        rt._sleep = AsyncMock()

        rt._start_step_timeout(9, "s4", 30.0)
        await rt.pause()
        self.assertEqual(rt.state, S_PAUSED)
        # The paused timer fires and retires itself without a verdict.
        await rt._step_timeout_task
        rt._step_timeout_task = None

        await rt.resume()

        self.assertEqual(rt.state, S_RUNNING)
        self.assertIsNotNone(rt._step_timeout_task)
        self.addCleanup(rt._cancel_step_timeout)

    async def test_resume_does_not_rearm_a_step_that_was_already_acked(self):
        rt = self._runtime()
        rt.state = S_RUNNING
        rt._step = {"id": "s4", "type": "listen"}
        rt._step_id = "s4"
        rt._step_seq = 9
        rt._step_acked = True
        rt._step_completed = True
        rt._step_timeout_sec = 30.0
        rt._current_visual_request = None

        await rt.pause()
        await rt.resume()

        self.assertIsNone(rt._step_timeout_task)


class LessonRuntimeActivityLeaseAdmissionTest(unittest.IsolatedAsyncioTestCase):
    """Checklist row: lesson admission while another activity holds a lease, and
    while a lesson is already RUNNING."""

    async def test_exclusive_eviction_lease_blocks_lesson_admission(self):
        from core.lesson.runtime import maybe_start_lesson_on_connect

        conn = T._RepublishConn()
        coordinator = T.ActivityLeaseCoordinator(asyncio.get_running_loop())
        conn.activity_leases = coordinator
        lease = coordinator.try_acquire_eviction(
            T.ActivityOperation.LESSON_CACHE_EVICT, busy_probe=lambda: False
        )
        self.assertIsNotNone(lease)

        result = await maybe_start_lesson_on_connect(conn)

        self.assertIsNone(result)
        self.assertEqual(
            (getattr(conn, "lesson_start_status", None) or {}).get("code"),
            "CACHE_EVICTION_RESERVED",
        )
        self.assertEqual(conn.websocket.sent, [])
        lease.complete_exclusive(T.ExclusiveDisposition.DEFINITIVE)

    async def test_running_lesson_survives_a_failed_candidate_admission(self):
        """The ESP has no ROBOT_BUSY start-time refusal (see the findings log);
        admission of a different assignment is a STAGED candidate swap. The
        invariant that must hold is that a candidate which fails to reach READY
        leaves the RUNNING lesson pinned and untouched."""
        from core.lesson.runtime import maybe_start_lesson_on_connect

        conn = T._RepublishConn()
        running = T._PinnedRuntime(
            assignment_id="running-assignment",
            lesson_version=2,
            assignment_version=1,
            state=S_RUNNING,
        )
        conn.lesson_runtime = running

        broken_manifest = T._build_manifest()
        broken_manifest["manifestVersion"] = "teebot-lesson-renderer.v9"
        harness = T.RepublishOnConnectTest()
        harness.setUp()
        self.addCleanup(harness.tearDown)
        undo = harness._patch_backend(
            harness._assignment(
                lesson_version=3, assignment_version=1, assignment_id="new-assignment"
            ),
            broken_manifest,
        )
        try:
            result = await maybe_start_lesson_on_connect(conn)
        finally:
            undo()

        self.assertIs(result, running)
        self.assertIs(conn.lesson_runtime, running)
        self.assertFalse(running.closed)
        self.assertEqual(running.state, S_RUNNING)


class LessonPreloadResetEnvelopeTest(unittest.IsolatedAsyncioTestCase):
    """T2.1 finding (plan §5): the hand-built ``preloadResetOnly`` prepare frame
    put an RFC3339 STRING on a ``timestamp`` field the envelope defines as epoch
    milliseconds."""

    async def test_preload_reset_prepare_timestamp_is_epoch_millis(self):
        import test_connection_edges as C

        handler = C._build_handler()
        handler.websocket = C._SendWebSocket()
        handler.device_id = "AA:BB:CC:DD:EE:FF"

        reset_task = asyncio.create_task(
            handler.request_lesson_preload_reset(
                assignment_id="assignment-1",
                lesson_id="lesson-1",
                profile="espTft",
            )
        )
        while not handler.websocket.sent:
            await asyncio.sleep(0)
        frame = handler.websocket.sent[0]
        handler._accept_lesson_preload_reset_ack(
            {
                "type": "lesson_ack",
                "sessionId": frame["sessionId"],
                "body": {"acks": 1},
            }
        )
        await reset_task

        self.assertEqual(frame["type"], "lesson_prepare")
        self.assertTrue(frame["body"]["preloadResetOnly"])
        self.assertIsInstance(frame["timestamp"], int)
        self.assertGreater(frame["timestamp"], 1_600_000_000_000)


class LessonRuntimeAutoDisableTest(unittest.IsolatedAsyncioTestCase):
    """T2.1 finding (plan §5, routed in by T2.3): the S13 voice-latency breaker
    detached the runtime but left ``session_mode`` pinned to LESSON, and every
    voice output path gates on exactly that — so tripping the breaker mid-lesson
    left the child in permanent silence."""

    async def test_auto_disable_leaves_lesson_mode_so_voice_can_speak_again(self):
        import test_connection_edges as C
        from core.voice.session_orchestrator import SessionMode, normalize_session_mode

        handler = C._build_handler()
        handler.session_mode = SessionMode.LESSON
        handler.audio_channel_owner = SessionMode.LESSON
        handler.lesson_runtime = None

        handler._disable_lesson_runtime()
        # The finish hook is scheduled on the running loop; let it run.
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        self.assertIs(handler.config["lesson"]["runtime_enabled"], False)
        self.assertNotEqual(
            normalize_session_mode(handler.session_mode), SessionMode.LESSON
        )


if __name__ == "__main__":
    unittest.main()
