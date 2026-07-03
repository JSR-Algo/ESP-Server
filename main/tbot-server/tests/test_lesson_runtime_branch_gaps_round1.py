"""Round-1 close-out of the remaining LINE gaps in ``core.lesson.runtime``.

``--cov`` (line, not just branch) still flags a handful of arms on the runtime that
no existing test drives. Each test below targets one exact region:

  * 176        -- ``_strip_immediate_scoring_detail``: the *list* recursion arm. A
                  ``detail`` whose value is a LIST of dicts (each carrying immediate
                  scoring keys) must recurse element-wise and strip per-element.
  * 519        -- ``_matches_runtime_identity``: the ``continue`` arm when the expected
                  identity field (e.g. ``session_id``) is None -> that field is skipped
                  rather than compared, so a frame carrying the key still matches.
  * 574-578    -- ``on_lesson_progress``: a DUPLICATE interactive ``step_completed`` for
                  the current, latched-but-not-yet-finished step is ignored (the step is
                  un-acked so the first one latched without finishing; the second hits
                  the duplicate-interactive guard).
  * 663        -- ``on_lesson_error``: an inbound ``lesson_error`` that arrives while the
                  runtime is ALREADY terminal (COMPLETED) is absorbed by the early
                  return -- last_error is NOT overwritten.
  * 1681-1682  -- ``_maybe_start_lesson_on_connect_impl``: republish-on-connect with an
                  unchanged version whose existing runtime is TERMINAL (COMPLETED) and
                  exposes a callable ``replay_pending_terminal_event`` -> the pending
                  terminal event is replayed before the session is kept.

Reuses the proven harness from ``test_lesson_runtime.py`` and the republish fakes from
``test_lesson_republish_on_connect.py``; every assertion runs against the REAL runtime
with no network or disk.
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))

import test_lesson_runtime as T  # noqa: E402  (sibling test harness)
import test_lesson_republish_on_connect as R  # noqa: E402  (republish fakes)
from core.lesson import runtime as rt_mod  # noqa: E402
from core.lesson.runtime import (  # noqa: E402
    LessonRuntime,
    S_COMPLETED,
    S_RUNNING,
    _strip_immediate_scoring_detail,
)


def _runtime(conn=None, *, asset_cache=None, forwarder=None, manifest=None):
    conn = conn or T._FakeConn(session_id="sess")
    return LessonRuntime(
        conn,
        assignment=T._build_assignment(),
        manifest=manifest or T._build_manifest(),
        asset_cache=asset_cache if asset_cache is not None else T._FakeAssetCache(ready=True),
        forwarder=forwarder if forwarder is not None else T._FakeForwarder(),
        manifest_checksum=T._manifest_checksum(),
    )


class StripScoringDetailListArmTest(unittest.TestCase):
    # ── 176: the list recursion arm strips immediate scoring keys per element ──────
    def test_strip_recurses_into_list_of_dicts(self):
        value = {
            "attempts": [
                {"recognizedText": "barn", "score": 91, "pronunciation": {"accuracy": 0.9}},
                {"recognizedText": "farm", "score": 12, "correction": "try again"},
            ],
            "source": "firmware_asr",
        }

        stripped = _strip_immediate_scoring_detail(value)

        # The list arm (176) recursed element-wise: each dict kept its non-scoring keys
        # and dropped the immediate scoring ones.
        self.assertEqual(len(stripped["attempts"]), 2)
        self.assertEqual(stripped["attempts"][0]["recognizedText"], "barn")
        self.assertEqual(stripped["attempts"][1]["recognizedText"], "farm")
        for elem in stripped["attempts"]:
            self.assertNotIn("score", elem)
            self.assertNotIn("pronunciation", elem)
            self.assertNotIn("correction", elem)
        self.assertEqual(stripped["source"], "firmware_asr")

    def test_strip_passes_scalars_through_unchanged(self):
        # Sanity on the same helper: scalars fall to the terminal return (not list/dict).
        self.assertEqual(_strip_immediate_scoring_detail("barn"), "barn")
        self.assertEqual(_strip_immediate_scoring_detail(7), 7)


class MatchesRuntimeIdentityNoneArmTest(unittest.TestCase):
    # ── 519: an expected identity field of None is SKIPPED (continue), not compared ─
    def test_none_session_id_is_skipped_so_frame_still_matches(self):
        conn = T._FakeConn()
        rt = _runtime(conn=conn)
        rt.session_id = None  # expected identity for sessionId is now None

        # assignmentId matches; sessionId expected is None -> the loop `continue`s past
        # it (519) without comparing, so a frame carrying a sessionId still matches.
        matched = rt._matches_runtime_identity(
            {"assignmentId": rt.assignment_id, "sessionId": "whatever-the-frame-says"}
        )
        self.assertTrue(matched)

    def test_non_none_mismatch_still_rejects(self):
        # Control: with a concrete session_id, a mismatching frame is rejected (the
        # other edge of the same branch), proving 519 is the None-skip, not a blanket pass.
        conn = T._FakeConn()
        rt = _runtime(conn=conn)
        rt.session_id = "real-sess"
        self.assertFalse(
            rt._matches_runtime_identity(
                {"assignmentId": rt.assignment_id, "sessionId": "other-sess"}
            )
        )


class DuplicateInteractiveStepCompletedTest(unittest.IsolatedAsyncioTestCase):
    # ── 574-578: a duplicate step_completed for the latched-but-unfinished step ────
    async def test_duplicate_interactive_step_completed_is_ignored(self):
        conn = T._FakeConn()
        forwarder = T._FakeForwarder()
        rt = _runtime(conn=conn, forwarder=forwarder)
        await rt.start()
        await rt.on_lesson_ack(T._ack(1, 1))   # lesson_prepare ack
        await rt._preload_task
        await rt.on_lesson_ack(T._ack(2, 2))   # lesson_start ack -> RUNNING, step emitted

        # Deliberately DO NOT ack the step frame (s4): the step stays un-acked, so the
        # first step_completed latches `_step_completed=True` but `_maybe_finish_step`
        # no-ops (needs `_step_acked`). The step is the current, non-passive (interactive)
        # 'model' step. Envelope sequence None bypasses the F->S gap detector.
        self.assertFalse(rt._step_passive)
        self.assertFalse(rt._step_acked)

        # `detail.recognizedText` is observable child-response evidence, so the first
        # frame clears the `_interactive_progress_has_response` gate, reaches the latch,
        # and sets `_step_completed=True` (but does not finish: the step is un-acked).
        completed_body = {
            "event": "step_completed",
            "stepType": "model",
            "result": "success",
            "detail": {"recognizedText": "barn", "source": "firmware_asr"},
        }
        await rt.on_lesson_progress(T._progress(None, dict(completed_body), step_id="s4"))
        self.assertTrue(rt._step_completed)
        self.assertEqual(rt.state, S_RUNNING)  # not finished (un-acked)

        forwarded_before = len(
            [e for b in forwarder.batches for e in b.get("events", []) if e.get("type") == "step_completed"]
        )

        # SECOND identical step_completed: current step, non-passive, already
        # `_step_completed` -> hits the duplicate-interactive guard (574-578) and returns
        # WITHOUT re-forwarding.
        await rt.on_lesson_progress(T._progress(None, dict(completed_body), step_id="s4"))

        forwarded_after = len(
            [e for b in forwarder.batches for e in b.get("events", []) if e.get("type") == "step_completed"]
        )
        self.assertEqual(forwarded_after, forwarded_before)  # the duplicate was dropped


class LessonErrorWhileTerminalTest(unittest.IsolatedAsyncioTestCase):
    # ── 663: a lesson_error arriving while already terminal is absorbed (early return) ─
    async def test_lesson_error_in_terminal_state_is_absorbed(self):
        conn = T._FakeConn()
        rt = _runtime(conn=conn)
        rt.state = S_COMPLETED  # terminal -- absorbing
        rt.last_error = None
        prior_seq = rt._last_inbound_sequence

        await rt.on_lesson_error(
            {
                "assignmentId": rt.assignment_id,
                "sessionId": rt.session_id,
                "sequence": prior_seq + 5,
                "body": {"code": "LATE_FAULT", "message": "should be ignored", "retryable": True},
            }
        )

        # The sequence is still counted (symmetric accounting, lines 660-661) ...
        self.assertEqual(rt._last_inbound_sequence, prior_seq + 5)
        # ... but the terminal early-return (663) means last_error is NOT overwritten and
        # the state is untouched.
        self.assertIsNone(rt.last_error)
        self.assertEqual(rt.state, S_COMPLETED)


class _ReplayingTerminalRuntime(R._FakeExistingRuntime):
    """A pinned existing runtime that is TERMINAL (COMPLETED) and exposes a callable
    replay_pending_terminal_event -> drives 1680-1682 on the unchanged-version branch."""

    def __init__(self, calls, **kw):
        super().__init__(calls, **kw)
        self.state = S_COMPLETED
        self.replayed = 0

    async def replay_pending_terminal_event(self):
        self.replayed += 1
        self._calls.append("replay")
        return True


class RepublishUnchangedTerminalReplayTest(unittest.IsolatedAsyncioTestCase):
    # ── 1681-1682: unchanged version + terminal existing + callable replay -> replay ─
    async def test_unchanged_version_terminal_runtime_replays_pending_event(self):
        R._FakeNewRuntime.instances = []
        calls = []
        conn = R._FakeConn(busy=False)
        existing = _ReplayingTerminalRuntime(
            calls, lesson_version=4, assignment_version=2, checksum=R.CHK_V2
        )
        conn.lesson_runtime = existing

        patches = R.RepublishOnConnectTest()._patches(
            assignment=R._assignment(lesson_version=4, assignment_version=2),
            manifest=R._manifest(),
            etag=R.ETAG_V2,
        )
        for p in patches:
            p.start()
        try:
            result = await rt_mod._maybe_start_lesson_on_connect_impl(conn)
        finally:
            for p in patches:
                p.stop()

        # Unchanged version is the no-op keep-session path, but because the existing
        # runtime is terminal AND exposes a callable replay, the pending terminal event
        # is replayed (1681-1682) before the session is kept.
        self.assertEqual(existing.replayed, 1)
        self.assertIn("replay", calls)
        # No eviction / no fresh re-pull: the session is kept.
        self.assertNotIn("evict", calls)
        self.assertNotIn("close", calls)
        self.assertEqual(R._FakeNewRuntime.instances, [])
        self.assertIs(result, existing)
        self.assertIs(conn.lesson_runtime, existing)


if __name__ == "__main__":
    unittest.main()
