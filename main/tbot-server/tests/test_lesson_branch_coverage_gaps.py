"""Close the final ``--cov-branch`` partial branches on the lesson surface.

An independent ``--cov-branch`` sweep flagged 8 partial branches (one edge never
taken) across ``core.lesson.asset_cache`` and ``core.lesson.runtime``. Six are
reachable and are exercised here with focused tests; the remaining two
(runtime 1585->1603 and 1657->1673) are genuinely unreachable given the
``_assignment_metadata_errors`` upstream gate and are marked ``# pragma: no cover``
in the source with a one-line justification.

Reachable branches closed here:

  asset_cache.py
    * 356->353 -- ``_materialized_total_bytes``: an asset whose final path does
                  NOT exist -> the ``if os.path.exists(path):`` add is skipped and
                  the loop continues to the next asset (false edge of 356).
    * 504->503 -- ``reattest``: the assets-too-large fail-out loop visits a
                  required asset that is NOT in state READY (a missing/PENDING one)
                  -> the ``if asset.state == READY:`` block is skipped and the loop
                  continues (false edge of 504).

  runtime.py
    * 217->210 -- ``_has_invalid_child_response_confidence``: a finite, positive
                  confidence -> the ``if not isfinite or <= 0`` block is False and
                  the loop continues to the next key (false edge of 217).
    * 611->exit -- ``on_lesson_progress``: a ``step_completed`` for a stepId that is
                  neither already-completed nor the current in-flight step
                  (a future/leftover step) -> forwarded for observability but the
                  ``if step_id == self._step_id:`` latch is skipped (false edge).
    * 712->714 -- ``_on_frame_acked`` (lesson_step arm): ``self._step`` is None at
                  ack time -> the ``if self._step is not None:`` speak-prompt is
                  skipped (defensive false edge).
    * 924->926 -- ``_maybe_finish_step``: ``self._step_id`` is not a str ->
                  the ``if isinstance(self._step_id, str):`` completed-id add is
                  skipped and advancement continues (false edge of 924).

Reuses the proven REAL ``LessonRuntime`` / ``AssetCache`` harnesses (no network,
no disk beyond a tmp cache root).
"""

import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))

import test_lesson_runtime as T  # noqa: E402  (sibling test harness)
from core.lesson.asset_cache import (  # noqa: E402
    READY,
    AssetCache,
    AssetState,
)
from core.lesson.runtime import (  # noqa: E402
    LessonRuntime,
    S_RUNNING,
    _compact_json,
    _has_invalid_child_response_confidence,
    _manifest_story_log_summary,
)

BASE = "http://assets.test"


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


class RuntimeHelperCoverageTest(unittest.IsolatedAsyncioTestCase):
    def test_compact_json_returns_empty_object_when_value_is_not_serializable(self):
        self.assertEqual(_compact_json(object()), "{}")

    def test_manifest_story_log_summary_skips_non_dict_and_id_only_steps(self):
        summary = _manifest_story_log_summary(
            {
                "steps": [
                    None,
                    {"id": "id-only"},
                    {"id": "story-text", "storyText": "hello"},
                    {"id": "story-beat", "storyBeat": {"wait": True}},
                    {"id": "classed", "completionClass": "passive"},
                ]
            }
        )

        self.assertEqual(
            summary,
            [
                {"id": "story-text", "storyText": True},
                {"id": "story-beat", "storyBeat": {"wait": True}},
                {"id": "classed", "completionClass": "passive"},
            ],
        )

    async def test_frame_ack_timeout_config_and_no_outstanding_edges(self):
        conn = T._FakeConn()
        conn.config["lesson"] = {"frame_ack_timeout_sec": "bad"}
        rt = _runtime(conn=conn)

        self.assertEqual(rt._frame_ack_timeout_sec(), 12.0)

        conn.config["lesson"] = {"frame_ack_timeout_sec": 0}
        rt._start_frame_ack_timeout("lesson_prepare", 1, None)
        self.assertIsNone(rt._frame_ack_timeout_task)

        async def no_sleep(_delay):
            return None

        conn.config["lesson"] = {"frame_ack_timeout_sec": 0.001}
        rt._sleep = no_sleep
        rt.state = S_RUNNING
        rt._outstanding.clear()
        rt._start_frame_ack_timeout("lesson_prepare", 42, None)
        task = rt._frame_ack_timeout_task
        self.assertIsNotNone(task)
        await task

        self.assertIsNone(rt.last_error)
        self.assertNotIn(42, rt._outstanding)


# ─────────────────────────── asset_cache branch gaps ───────────────────────────


class AssetCacheMaterializedBytesBranchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="lesson-cache-cov-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _cache(self, assets, **kw):
        return AssetCache(
            assets=assets,
            profile="espTft",
            asset_origin_base=BASE,
            cache_root=self.tmp,
            lesson_key="w01-d01-barn-say-it",
            **kw,
        )

    # ── 356->353: a required asset whose final path is absent is skipped ──────────
    def test_materialized_total_bytes_skips_absent_asset(self):
        """``_materialized_total_bytes`` over two required assets where the FIRST
        has bytes on disk and the SECOND was never materialized -> the
        ``if os.path.exists(path):`` add is skipped for the absent one and the loop
        continues (branch 356->353), so the total reflects only the present asset.
        Mutation guard: dropping the existence guard would ``os.path.getsize`` a
        missing path and raise OSError -> caller would see the over-budget sentinel
        instead of the true partial total."""
        cache = self._cache(
            [
                {"key": "teachingObject.barn", "path": "barn.png", "sha256": "ab" * 32, "critical": True},
                {"key": "robotOverlay.pose", "path": "pose.png", "sha256": "cd" * 32, "critical": False},
            ]
        )
        present, absent = cache.assets[0], cache.assets[1]

        # Materialize ONLY the first asset's final path with a known size.
        present_path = cache._final_path(present)
        os.makedirs(os.path.dirname(present_path), exist_ok=True)
        with open(present_path, "wb") as fh:
            fh.write(b"x" * 17)
        # The second asset's final path is intentionally never created.
        assert not os.path.exists(cache._final_path(absent))

        total = cache._materialized_total_bytes(cache.assets)

        # Only the present asset contributed; the absent one was skipped, not summed
        # and not treated as an error (no over-budget sentinel).
        self.assertEqual(total, 17)


class AssetCacheReattestTooLargeBranchTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="lesson-cache-cov-reattest-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _cache(self, assets, **kw):
        return AssetCache(
            assets=assets,
            profile="espTft",
            asset_origin_base=BASE,
            cache_root=self.tmp,
            lesson_key="w01-d01-barn-say-it",
            **kw,
        )

    # ── 504->503: too-large fail-out loop skips a non-READY required asset ─────────
    async def test_reattest_too_large_skips_non_ready_asset(self):
        """``reattest`` over two required assets where one verifies READY (and pushes
        total bytes past a tiny max) and the other is missing on disk (-> PENDING):
        the assets-too-large loop visits both, but the ``if asset.state == READY:``
        block is skipped for the PENDING one (branch 504->503). Proves the too-large
        fail-out only re-flags the READY assets and does not crash on the others."""
        cache = self._cache(
            [
                {"key": "teachingObject.barn", "path": "barn.png", "sha256": None, "critical": True},
                {"key": "robotOverlay.pose", "path": "pose.png", "sha256": None, "critical": False},
            ],
            max_total_asset_bytes=4,  # any real file blows the budget
        )
        ready_asset, missing_asset = cache.assets[0], cache.assets[1]

        # Materialize ONLY the first; give it the sha256 of its own bytes so reattest
        # flips it to READY and counts > 4 bytes.
        body = b"barn-bytes"
        ready_path = cache._final_path(ready_asset)
        os.makedirs(os.path.dirname(ready_path), exist_ok=True)
        with open(ready_path, "wb") as fh:
            fh.write(body)
        ready_asset.sha256 = cache._hash_file(ready_path)
        # second asset's path never created -> reattest sets it PENDING (not READY).
        assert not os.path.exists(cache._final_path(missing_asset))

        ready = await cache.reattest()

        # The READY asset got re-flagged FAILED (too large); the missing asset was
        # skipped by the 504 guard and stayed non-READY (PENDING), never re-flagged
        # with the too-large reason.
        self.assertFalse(ready)
        self.assertEqual(ready_asset.reason, "lesson_assets_too_large")
        self.assertNotEqual(missing_asset.state, READY)
        self.assertNotEqual(missing_asset.reason, "lesson_assets_too_large")


# ─────────────────────────── runtime branch gaps ───────────────────────────────


class ConfidenceLoopContinueBranchTest(unittest.TestCase):
    # ── 217->210: a finite positive confidence continues the key loop ─────────────
    def test_valid_confidence_continues_loop_and_returns_false(self):
        """A single finite, strictly-positive confidence -> the
        ``if not isfinite or <= 0`` guard is False, so the for-loop continues to the
        next key (branch 217->210), exhausts, and the detail is judged VALID.
        Mutation guard: inverting the guard would flag a perfectly good 0.9 as
        invalid and discard a real child response."""
        self.assertFalse(_has_invalid_child_response_confidence({"confidence": 0.9}))
        # And a positive value on a later key alias is likewise valid.
        self.assertFalse(_has_invalid_child_response_confidence({"asrConfidence": 1.0}))


class ProgressStaleStepIdBranchTest(unittest.IsolatedAsyncioTestCase):
    # ── 611->exit: a step_completed for a non-current, non-completed stepId ────────
    async def test_step_completed_for_other_step_is_forwarded_but_not_latched(self):
        """A ``step_completed`` whose stepId is neither already-completed nor the
        current in-flight step (a leftover/future step) passes the earlier guards but
        ``if step_id == self._step_id:`` is False -> it is forwarded for observability
        yet does NOT latch completion (branch 611->exit). This is the
        LATCH-CONTAMINATION guard: a stale frame must never advance the run."""
        conn = T._FakeConn()
        forwarder = T._FakeForwarder()
        rt = _runtime(conn=conn, forwarder=forwarder)
        await rt.start()
        await rt.on_lesson_ack(T._ack(1, 1))
        await rt._preload_task
        await rt.on_lesson_ack(T._ack(2, 2))
        # Now in RUNNING with a known in-flight step.
        self.assertEqual(rt.state, S_RUNNING)
        current_step = rt._step_id
        other_step = (current_step or "s4") + "-leftover"
        self.assertNotEqual(other_step, current_step)
        self.assertNotIn(other_step, rt._completed_step_ids)

        completed_before = rt._steps_completed
        await rt.on_lesson_progress(
            T._progress(
                3,
                {"event": "step_completed", "stepType": "model", "result": "success"},
                step_id=other_step,
            )
        )

        # Forwarded for observability ...
        forwarded = [
            e["stepId"]
            for b in forwarder.batches
            for e in (b.get("events") or [])
            if e.get("type") == "step_completed"
        ]
        self.assertIn(other_step, forwarded)
        # ... but NOT latched: no completion, no advance.
        self.assertFalse(rt._step_completed)
        self.assertEqual(rt._steps_completed, completed_before)


class StepAckNoStepBranchTest(unittest.IsolatedAsyncioTestCase):
    # ── 712->714: lesson_step ack with _step cleared skips the speak-prompt ────────
    async def test_step_ack_without_step_skips_speak_prompt(self):
        """Defensive guard: if ``self._step`` has been cleared by the time the
        ``lesson_step`` ack lands, the ``if self._step is not None:`` speak-prompt is
        skipped (branch 712->714) and the ack still flips ``_step_acked``. Without the
        guard, ``_speak_step_prompt(None)`` would AttributeError on the None step."""
        conn = T._FakeConn()
        rt = _runtime(conn=conn)
        await rt.start()
        await rt.on_lesson_ack(T._ack(1, 1))  # prepare ack -> preload
        await rt._preload_task
        await rt.on_lesson_ack(T._ack(2, 2))  # start ack -> _emit_step emits lesson_step
        # A lesson_step frame is now outstanding awaiting its ack. Clear _step so the
        # ack-time speak-prompt guard takes its false edge.
        sent = [__import__("json").loads(p) for p in conn.websocket.sent]
        step_frame = next(f for f in sent if f["type"] == "lesson_step")
        rt._step = None

        # Ack the lesson_step (env seq 3 acks the step's outstanding sequence).
        await rt.on_lesson_ack(T._ack(3, 3, step_id=step_frame["stepId"]))

        # Speak-prompt skipped (no crash on None step); the ack still registered.
        self.assertTrue(rt._step_acked)


class MaybeFinishNonStrStepIdBranchTest(unittest.IsolatedAsyncioTestCase):
    # ── 924->926: _maybe_finish_step with a non-str _step_id skips the id-add ──────
    async def test_finish_step_with_non_str_step_id_still_advances(self):
        """Defensive guard: a step whose id is not a str leaves ``self._step_id``
        non-str; when ``_maybe_finish_step`` runs it skips
        ``if isinstance(self._step_id, str): self._completed_step_ids.add(...)``
        (branch 924->926) yet still counts the step and advances. Without the
        isinstance guard a non-str id would be added to a set keyed on str ids."""
        conn = T._FakeConn()
        rt = _runtime(conn=conn)
        rt.state = S_RUNNING
        # Single-step manifest -> after finishing, advance falls to lesson_stop.
        rt._steps = T._build_manifest()["steps"]
        rt._step_index = 0
        rt._step = rt._steps[0]
        rt._step_id = None  # non-str id (e.g. a malformed/legacy step) -> 924 false edge
        rt._step_acked = True
        rt._step_completed = True

        completed_before = len(rt._completed_step_ids)
        await rt._maybe_finish_step()

        # Step counted, but no id added (None is not a str) -> set unchanged.
        self.assertEqual(rt._steps_completed, 1)
        self.assertEqual(len(rt._completed_step_ids), completed_before)


if __name__ == "__main__":
    unittest.main()
