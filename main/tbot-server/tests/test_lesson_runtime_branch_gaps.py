"""Close the *object-level* branch gaps in ``core.lesson.runtime.LessonRuntime``.

``--cov-branch`` flags several partial branches on the runtime state machine whose
false-edge no existing test takes:

  * 344->346 -- ``close``: forwarder is None (already torn down) -> skip aclose, fall
                to the asset_cache teardown.
  * 346->exit -- ``close``: asset_cache is None -> nothing left to tear down.
  * 408->exit -- ``on_lesson_progress``: a forwarded progress event whose ``event`` is
                NOT ``step_completed`` (e.g. ``step_started``) -> the completion-latch
                block is skipped and the handler returns.
  * 460->462 -- ``on_lesson_error``: ``sequence`` is not an int (or not greater than the
                last) -> the inbound-sequence bump is skipped but the handler still
                processes the error.
  * 471->exit -- ``on_lesson_error``: the run is in a non-running, non-terminal state
                (READY) -> the error is recorded but does NOT fail the run.
  * 518->exit -- ``_on_frame_acked``: an unrecognized frame type -> the elif chain
                falls through with no state transition.
  * 912->914 -- ``_emit``: a frame_type that is neither ``lesson_step`` nor one of
                prepare/start/stop (``lesson_error``) -> neither log branch runs.
  * 1018->1025 -- ``_prepare_body``: SD asset pack enabled but the cache exposes no
                callable ``asset_pack_manifest`` -> the assetPack block is skipped.
  * 1123->1120 -- ``_rewrite_required_sd_pack_layer_sources``: a required asset node
                that is None (layer absent) -> the per-node rewrite is skipped and the
                loop continues.

Reuses the proven harness from ``test_lesson_runtime.py`` (FIX-derived assignment /
manifest / fakes) by importing its helpers, so every assertion runs against the REAL
``LessonRuntime`` with no network or disk.
"""

import os
import sys
import unittest
import asyncio
import json
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))

import test_lesson_runtime as T  # noqa: E402  (sibling test harness)
from core.lesson.runtime import (  # noqa: E402
    LessonRuntime,
    S_FAILED,
    S_PRELOADING,
    S_READY,
    S_RUNNING,
)


def _runtime(conn=None, *, asset_cache=None, forwarder=None, manifest=None):
    conn = conn or T._FakeConn(session_id="sess")
    with mock.patch("core.lesson.runtime.uuid.uuid4", return_value=conn.session_id):
        return LessonRuntime(
            conn,
            assignment=T._build_assignment(),
            manifest=manifest or T._build_manifest(),
            asset_cache=asset_cache if asset_cache is not None else T._FakeAssetCache(ready=True),
            forwarder=forwarder if forwarder is not None else T._FakeForwarder(),
            manifest_checksum=T._manifest_checksum(),
        )


class RuntimeCloseBranchTest(unittest.IsolatedAsyncioTestCase):
    # ── 344->346: close with no forwarder still tears down the asset cache ────────
    async def test_close_without_forwarder_still_closes_asset_cache(self):
        asset_cache = T._FakeAssetCache(ready=True)
        rt = _runtime(asset_cache=asset_cache)
        rt.forwarder = None  # already torn down / never attached

        await rt.close()

        # forwarder branch skipped (no crash), asset cache still closed.
        self.assertTrue(asset_cache.closed)
        self.assertTrue(rt._closed)

    async def test_close_rejects_all_visual_waiters_and_increments_generation(self):
        rt = _runtime()
        rt.negotiated_version = "teebot-lesson-renderer.v2"
        rt.renderer_capabilities = ["teebot-lesson-renderer.v2"]
        rt.conn.device_id = "robot-01"
        rt.conn.config = {
            "lesson": {
                "renderer_v2_enabled": True,
                "rollout_device_allowlist": ["robot-01"],
                "frame_ack_timeout_sec": 60,
            }
        }
        task = asyncio.create_task(rt.send_visual_state("thinking", overlay_key="thinking"))
        await asyncio.sleep(0)
        generation = rt._visual_generation
        self.assertEqual(len(rt._visual_ack_waiters), 1)

        await rt.close()
        result = await task

        self.assertFalse(result.accepted)
        self.assertEqual(rt._visual_generation, generation + 1)
        self.assertEqual(rt._visual_ack_waiters, {})
        self.assertEqual(rt._visual_ack_timeout_tasks, {})

    # ── 346->exit: close with no asset cache is a clean no-op tail ────────────────
    async def test_close_without_asset_cache_is_clean(self):
        forwarder = T._FakeForwarder()
        rt = _runtime(forwarder=forwarder)
        rt.asset_cache = None

        await rt.close()

        self.assertTrue(forwarder.closed)
        self.assertTrue(rt._closed)


class VisualAckWaiterTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.rt = _runtime()
        self.rt.negotiated_version = "teebot-lesson-renderer.v2"
        self.rt.renderer_capabilities = ["teebot-lesson-renderer.v2"]
        self.rt.conn.device_id = "robot-01"
        self.rt.conn.config = {
            "lesson": {
                "renderer_v2_enabled": True,
                "rollout_device_allowlist": ["robot-01"],
                "frame_ack_timeout_sec": 60,
            }
        }
        self.rt.state = S_RUNNING
        self.rt._step_id = "s4"

    def _ack(self, server_sequence, inbound_sequence, *, generation, **body):
        return {
            "type": "lesson_ack",
            "protocolVersion": "teebot-lesson-renderer.v2",
            "assignmentId": self.rt.assignment_id,
            "sessionId": self.rt.session_id,
            "stepId": "s4",
            "sequence": inbound_sequence,
            "body": {
                "acks": server_sequence,
                "accepted": True,
                "degraded": False,
                "degradedReason": None,
                "visualGeneration": generation,
                **body,
            },
        }

    async def test_independent_sequences_resolve_only_the_correlated_waiter(self):
        first = asyncio.create_task(self.rt.send_visual_state("thinking"))
        second = asyncio.create_task(self.rt.send_visual_state("listen"))
        await asyncio.sleep(0)
        frames = [json.loads(item) for item in self.rt.conn.websocket.sent]
        visual = [frame for frame in frames if frame["type"] == "lesson_visual_state"]
        first_seq, second_seq = visual[-2]["sequence"], visual[-1]["sequence"]
        generation = visual[-1]["body"]["visualGeneration"]

        await self.rt.on_lesson_ack(self._ack(second_seq, 1, generation=generation))
        await asyncio.sleep(0)
        self.assertTrue(second.done())
        self.assertFalse(first.done())
        await self.rt.on_lesson_ack(self._ack(first_seq, 2, generation=generation))

        self.assertTrue((await first).accepted)
        self.assertTrue((await second).accepted)

    async def test_wrong_generation_and_negative_ack_are_no_motion_noops(self):
        task = asyncio.create_task(
            self.rt.send_visual_state("incorrect", motion_preset="tryAgain")
        )
        await asyncio.sleep(0)
        frame = json.loads(self.rt.conn.websocket.sent[-1])
        seq = frame["sequence"]
        generation = frame["body"]["visualGeneration"]

        await self.rt.on_lesson_ack(self._ack(seq, 1, generation=generation + 1))
        self.assertFalse(task.done())
        await self.rt.on_lesson_ack(
            self._ack(
                seq,
                1,
                generation=generation,
                accepted=False,
                degraded=False,
                degradedReason="unsupportedContract",
            )
        )
        result = await task
        self.assertFalse(result.accepted)
        self.assertIsNone(self.rt._motion_task)

    async def test_visual_ack_rejects_extra_or_wrong_typed_frozen_fields(self):
        task = asyncio.create_task(self.rt.send_visual_state("thinking"))
        await asyncio.sleep(0)
        frame = json.loads(self.rt.conn.websocket.sent[-1])
        seq = frame["sequence"]
        generation = frame["body"]["visualGeneration"]

        wrong_body = self._ack(seq, 1, generation=generation)
        wrong_body["body"] = []
        await self.rt.on_lesson_ack(wrong_body)
        await asyncio.sleep(0)
        self.assertFalse(task.done())

        extra = self._ack(seq, 1, generation=generation)
        extra["body"]["unexpected"] = True
        await self.rt.on_lesson_ack(extra)
        await asyncio.sleep(0)
        self.assertFalse(task.done())

        wrong_sequence = self._ack(seq, True, generation=generation)
        await self.rt.on_lesson_ack(wrong_sequence)
        await asyncio.sleep(0)
        self.assertFalse(task.done())

        await self.rt.on_lesson_ack(self._ack(seq, 1, generation=generation))
        self.assertTrue((await task).accepted)

    async def test_timeout_retries_once_with_new_sequence_same_generation(self):
        sleeps = []

        async def immediate_sleep(delay):
            sleeps.append(delay)

        self.rt._sleep = immediate_sleep
        self.rt.conn.config["lesson"]["frame_ack_timeout_sec"] = 0.01
        result = await self.rt.send_visual_state("thinking")
        frames = [
            json.loads(item)
            for item in self.rt.conn.websocket.sent
            if json.loads(item)["type"] == "lesson_visual_state"
        ]
        self.assertEqual(len(frames), 2)
        self.assertNotEqual(frames[0]["sequence"], frames[1]["sequence"])
        self.assertEqual(
            frames[0]["body"]["visualGeneration"],
            frames[1]["body"]["visualGeneration"],
        )
        self.assertFalse(result.accepted)
        self.assertEqual(len(sleeps), 2)

    async def test_late_ack_for_timed_out_attempt_advances_inbound_before_retry_ack(self):
        second_timeout = asyncio.Event()
        sleep_calls = 0

        async def controlled_sleep(_delay):
            nonlocal sleep_calls
            sleep_calls += 1
            if sleep_calls == 1:
                return
            await second_timeout.wait()

        self.rt._sleep = controlled_sleep
        task = asyncio.create_task(self.rt.send_visual_state("thinking"))
        try:
            for _ in range(10):
                await asyncio.sleep(0)
                frames = [
                    json.loads(item)
                    for item in self.rt.conn.websocket.sent
                    if json.loads(item)["type"] == "lesson_visual_state"
                ]
                if len(frames) == 2:
                    break
            self.assertEqual(len(frames), 2)
            first, retry = frames
            generation = retry["body"]["visualGeneration"]

            await self.rt.on_lesson_ack(
                self._ack(first["sequence"], 1, generation=generation)
            )
            await self.rt.on_lesson_ack(
                self._ack(retry["sequence"], 2, generation=generation)
            )
            await asyncio.sleep(0)

            self.assertTrue(task.done())
            self.assertTrue((await task).accepted)
            sent_types = [json.loads(item)["type"] for item in self.rt.conn.websocket.sent]
            self.assertNotIn("lesson_error", sent_types)
        finally:
            second_timeout.set()
            if not task.done():
                await self.rt.close()
                await task

    def test_retired_visual_sequences_are_bounded_and_pruned_on_generation_change(self):
        for sequence in range(140):
            self.rt._retire_visual_ack_sequence(sequence, self.rt._visual_generation, "s4")

        self.assertEqual(len(self.rt._retired_visual_ack_sequences), 128)
        self.assertNotIn(0, self.rt._retired_visual_ack_sequences)

        self.rt._cancel_visual_waiters(increment_generation=True, reason="replaced")
        self.assertEqual(self.rt._retired_visual_ack_sequences, {})

    async def test_pause_cancels_waiter_without_completing_step_and_resume_resends(self):
        self.rt._step_completed = False
        task = asyncio.create_task(self.rt.send_visual_state("thinking"))
        await asyncio.sleep(0)
        old_generation = self.rt._visual_generation

        await self.rt.pause()
        paused = await task
        self.assertFalse(paused.accepted)
        self.assertFalse(self.rt._step_completed)
        self.assertEqual(self.rt._visual_generation, old_generation)

        resume = asyncio.create_task(self.rt.resume())
        await asyncio.sleep(0)
        frame = json.loads(self.rt.conn.websocket.sent[-1])
        self.assertEqual(frame["type"], "lesson_visual_state")
        self.assertEqual(frame["body"]["visualGeneration"], old_generation + 1)
        await self.rt.on_lesson_ack(
            self._ack(frame["sequence"], 1, generation=old_generation + 1)
        )
        self.assertTrue((await resume).accepted)

    async def test_pause_after_ack_resolution_prevents_stale_motion_dispatch(self):
        self.rt.conn.config["lesson"]["motion_presets_enabled"] = True
        task = asyncio.create_task(
            self.rt.send_visual_state("correct", motion_preset="celebrate")
        )
        await asyncio.sleep(0)
        frame = json.loads(self.rt.conn.websocket.sent[-1])

        await self.rt.on_lesson_ack(
            self._ack(
                frame["sequence"],
                1,
                generation=frame["body"]["visualGeneration"],
            )
        )
        await self.rt.pause()
        result = await task

        self.assertTrue(result.accepted)
        self.assertIsNone(self.rt._motion_task)

    async def test_orchestrator_dispatches_authored_motion_once_after_matching_ack(self):
        self.rt.conn.config["lesson"]["motion_presets_enabled"] = True
        self.rt._step = {
            "motion": {"correct": "celebrate"},
            "scene": {
                "robotOverlay": {"asset": {"key": "robotOverlay.celebrate"}}
            },
        }
        events = []

        async def motion(_conn, preset):
            events.append(("motion", preset))
            return True

        with mock.patch("core.lesson.runtime.dispatch_motion_preset", side_effect=motion):
            task = asyncio.create_task(
                self.rt._apply_authored_visual_then_motion("correct", "correct")
            )
            await asyncio.sleep(0)
            frame = json.loads(self.rt.conn.websocket.sent[-1])
            events.append(("visual", frame["body"]["state"]))
            self.assertEqual(events, [("visual", "correct")])

            await self.rt.on_lesson_ack(
                self._ack(
                    frame["sequence"],
                    1,
                    generation=frame["body"]["visualGeneration"],
                    degraded=True,
                    degradedReason="reducedMotion",
                )
            )
            self.assertTrue(await task)
            self.assertEqual(events, [("visual", "correct"), ("motion", "celebrate")])
            self.assertFalse(
                await self.rt._dispatch_motion_once(
                    "celebrate", frame["body"]["visualGeneration"], self.rt._step_id
                )
            )

            await self.rt.on_lesson_ack(
                self._ack(
                    frame["sequence"],
                    1,
                    generation=frame["body"]["visualGeneration"],
                    degraded=True,
                    degradedReason="reducedMotion",
                )
            )
            await asyncio.sleep(0)
            self.assertEqual(events, [("visual", "correct"), ("motion", "celebrate")])

    async def test_orchestrator_assigns_new_generation_and_replacement_blocks_stale_motion(self):
        self.rt.conn.config["lesson"]["motion_presets_enabled"] = True
        dispatched = []

        async def motion(_conn, preset):
            dispatched.append(preset)
            return True

        with mock.patch("core.lesson.runtime.dispatch_motion_preset", side_effect=motion):
            first = asyncio.create_task(
                self.rt._apply_visual_then_motion(
                    "incorrect", "robotOverlay.thinking", "tryAgain"
                )
            )
            await asyncio.sleep(0)
            first_frame = json.loads(self.rt.conn.websocket.sent[-1])
            second = asyncio.create_task(
                self.rt._apply_visual_then_motion(
                    "retry", "robotOverlay.thinking", "tryAgain"
                )
            )
            await asyncio.sleep(0)
            second_frame = json.loads(self.rt.conn.websocket.sent[-1])

            self.assertGreater(
                second_frame["body"]["visualGeneration"],
                first_frame["body"]["visualGeneration"],
            )
            self.assertFalse(await first)
            await self.rt.on_lesson_ack(
                self._ack(
                    second_frame["sequence"],
                    1,
                    generation=second_frame["body"]["visualGeneration"],
                )
            )
            self.assertTrue(await second)
            self.assertEqual(dispatched, ["tryAgain"])

    async def test_rejected_timeout_pause_stop_disconnect_and_replacement_dispatch_no_motion(self):
        self.rt.conn.config["lesson"]["motion_presets_enabled"] = True

        async def assert_cancelled(action):
            task = asyncio.create_task(
                self.rt._apply_visual_then_motion(
                    "thinking", "robotOverlay.thinking", "thinking"
                )
            )
            await asyncio.sleep(0)
            await action()
            self.assertFalse(await task)
            self.assertIsNone(self.rt._motion_task)

        await assert_cancelled(self.rt.pause)
        self.rt.state = S_RUNNING
        await assert_cancelled(self.rt.stop)
        self.rt.state = S_RUNNING
        await assert_cancelled(self.rt.on_disconnect)
        self.rt.state = S_RUNNING
        await assert_cancelled(self.rt.on_replaced)

        self.rt.state = S_RUNNING
        rejected = asyncio.create_task(
            self.rt._apply_visual_then_motion(
                "incorrect", "robotOverlay.thinking", "tryAgain"
            )
        )
        await asyncio.sleep(0)
        frame = json.loads(self.rt.conn.websocket.sent[-1])
        await self.rt.on_lesson_ack(
            self._ack(
                frame["sequence"],
                1,
                generation=frame["body"]["visualGeneration"],
                accepted=False,
                degraded=False,
                degradedReason="unsupportedContract",
            )
        )
        self.assertFalse(await rejected)
        self.assertIsNone(self.rt._motion_task)

        async def immediate_sleep(_delay):
            return None

        self.rt._sleep = immediate_sleep
        self.rt.conn.config["lesson"]["frame_ack_timeout_sec"] = 0.01
        timed_out = await self.rt._apply_visual_then_motion(
            "thinking", "robotOverlay.thinking", "thinking"
        )
        self.assertFalse(timed_out)
        self.assertIsNone(self.rt._motion_task)

        self.rt._sleep = asyncio.sleep
        self.rt.conn.config["lesson"]["frame_ack_timeout_sec"] = 60
        cancelled = asyncio.create_task(
            self.rt._apply_visual_then_motion(
                "thinking", "robotOverlay.thinking", "thinking"
            )
        )
        await asyncio.sleep(0)
        cancelled.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await cancelled
        self.assertIsNone(self.rt._motion_task)

class ProgressNonCompletionBranchTest(unittest.IsolatedAsyncioTestCase):
    # ── 408->exit: a non-step_completed progress event is forwarded but not latched ─
    async def test_non_completion_progress_event_is_forwarded_without_latch(self):
        conn = T._FakeConn()
        forwarder = T._FakeForwarder()
        rt = _runtime(conn=conn, forwarder=forwarder)
        await rt.start()
        await rt.on_lesson_ack(T._ack(1, 1))
        await rt._preload_task
        await rt.on_lesson_ack(T._ack(2, 2))
        await rt.on_lesson_ack(T._ack(3, 3, step_id="s4"))

        completed_before = rt._steps_completed

        # event != step_completed -> forwarded for observability, NOT latched.
        await rt.on_lesson_progress(
            T._progress(4, {"event": "step_started", "stepType": "model"}, step_id="s4")
        )

        forwarded = [
            b["events"][0]["type"]
            for b in forwarder.batches
            if b.get("events")
        ]
        self.assertIn("step_started", forwarded)
        # No completion latched by a non-completion event.
        self.assertEqual(rt._steps_completed, completed_before)
        self.assertFalse(rt._step_completed)


class LessonErrorBranchTest(unittest.IsolatedAsyncioTestCase):
    # ── 460->462: non-int sequence skips the inbound bump but still processes ──────
    async def test_lesson_error_with_non_int_sequence_still_records(self):
        conn = T._FakeConn()
        rt = _runtime(conn=conn)
        rt.state = S_RUNNING
        prior_seq = rt._last_inbound_sequence

        await rt.on_lesson_error(
            {
                "assignmentId": rt.assignment_id,
                "sessionId": rt.session_id,
                "sequence": None,
                "body": {"code": "DISPLAY_FAULT", "message": "x"},
            }
        )

        # sequence bump skipped (None is not an int) ...
        self.assertEqual(rt._last_inbound_sequence, prior_seq)
        # ... but the error still recorded + the running run failed.
        self.assertEqual(rt.last_error.code, "DISPLAY_FAULT")
        self.assertEqual(rt.state, S_FAILED)

    # ── 471->exit: error in a non-running, non-terminal state does NOT fail run ────
    async def test_lesson_error_in_ready_state_records_without_failing(self):
        conn = T._FakeConn()
        rt = _runtime(conn=conn)
        rt.state = S_READY  # not RUNNING/PRELOADING, not terminal

        await rt.on_lesson_error(
            {
                "assignmentId": rt.assignment_id,
                "sessionId": rt.session_id,
                "sequence": 9,
                "body": {"code": "TRANSIENT", "message": "blip"},
            }
        )

        # Recorded for accounting, but the run is NOT failed (471 false edge).
        self.assertEqual(rt.last_error.code, "TRANSIENT")
        self.assertEqual(rt.state, S_READY)
        self.assertEqual(rt._last_inbound_sequence, 9)


class RuntimeLogContextTest(unittest.TestCase):
    def test_runtime_warning_log_includes_assignment_and_session_id(self):
        messages = []

        class _CapturingLogger(T._DummyLogger):
            def warning(self, message, *args, **kwargs):
                messages.append(str(message))

        conn = T._FakeConn(session_id="sess-ctx")
        conn.logger = _CapturingLogger()
        rt = _runtime(conn=conn)

        rt._log("warning", "runtime degraded")

        self.assertEqual(len(messages), 1)
        self.assertIn("runtime degraded", messages[0])
        self.assertIn(f"assignment_id={rt.assignment_id}", messages[0])
        self.assertIn(f"session_id={rt.session_id}", messages[0])


class FrameAckUnknownTypeBranchTest(unittest.IsolatedAsyncioTestCase):
    # ── 518->exit: an unrecognized acked frame type is a no-op state-wise ─────────
    async def test_on_frame_acked_ignores_unknown_frame_type(self):
        conn = T._FakeConn()
        rt = _runtime(conn=conn)
        rt.state = S_RUNNING

        # No matching elif arm -> falls through, state untouched, no crash.
        await rt._on_frame_acked({"type": "totally_unknown_frame"}, {})

        self.assertEqual(rt.state, S_RUNNING)


class EmitNonStandardFrameTypeBranchTest(unittest.IsolatedAsyncioTestCase):
    # ── 912->914: emit a frame type outside step + prepare/start/stop ─────────────
    async def test_emit_non_standard_frame_type_skips_both_log_branches(self):
        conn = T._FakeConn()
        rt = _runtime(conn=conn)

        seq = await rt._emit("lesson_error", body={"code": "X"})

        # The frame was still sent on the wire (neither log branch is required for it).
        sent = [__import__("json").loads(p) for p in conn.websocket.sent]
        emitted = [f for f in sent if f["type"] == "lesson_error" and f["sequence"] == seq]
        self.assertEqual(len(emitted), 1)


class PrepareBodySdPackBranchTest(unittest.IsolatedAsyncioTestCase):
    # ── 1018->1025: SD pack enabled but cache has no callable asset_pack_manifest ──
    def test_prepare_body_sd_pack_without_manifest_helper_omits_asset_pack(self):
        # Enable SD pack mode via conn.config so _sd_asset_pack_enabled() is True.
        conn = T._FakeConn()
        conn.config = {"lesson": {"asset_delivery_mode": "sd_pack"}}

        class _NoPackManifestCache(T._FakeAssetCache):
            # asset_pack_manifest deliberately NOT callable (None) -> getattr -> None.
            asset_pack_manifest = None

        rt = _runtime(conn=conn, asset_cache=_NoPackManifestCache(ready=True))

        body = rt._prepare_body()

        # SD pack path taken, but no callable manifest helper -> no assetPack key.
        self.assertTrue(rt._sd_asset_pack_enabled())
        self.assertNotIn("assetPack", body)


class RewriteRequiredLayerSourcesBranchTest(unittest.IsolatedAsyncioTestCase):
    # ── 1123->1120: a None required-asset node is skipped, loop continues ─────────
    def test_rewrite_skips_none_required_nodes(self):
        conn = T._FakeConn()
        rt = _runtime(conn=conn)

        # backgroundScene present (a dict node), teachingObject/robotOverlay absent ->
        # _required_lesson_step_asset_nodes yields None for the two missing layers, so
        # the ``if isinstance(node, dict)`` rewrite is skipped for them (branch 1123->1120).
        scene = {
            "backgroundScene": {"poster": {"src": "barn.png"}},
            # teachingObject + robotOverlay intentionally missing -> None nodes.
        }

        # local_pack_url_for_source on _FakeAssetCache is callable, so the loop runs.
        rt._rewrite_required_sd_pack_layer_sources(scene)

        # The present node was rewritten; the absent layers stayed absent (no crash on
        # the None nodes -> the branch was exercised).
        self.assertIsInstance(scene["backgroundScene"]["poster"]["src"], str)
        self.assertNotIn("teachingObject", scene)
        self.assertNotIn("robotOverlay", scene)


if __name__ == "__main__":
    unittest.main()
