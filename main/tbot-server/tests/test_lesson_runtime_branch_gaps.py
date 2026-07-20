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

    # ── 346->exit: close with no asset cache is a clean no-op tail ────────────────
    async def test_close_without_asset_cache_is_clean(self):
        forwarder = T._FakeForwarder()
        rt = _runtime(forwarder=forwarder)
        rt.asset_cache = None

        await rt.close()

        self.assertTrue(forwarder.closed)
        self.assertTrue(rt._closed)


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
