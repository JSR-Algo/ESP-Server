"""Close the remaining *branch* gaps in ``core.lesson.forwarder``.

``--cov-branch`` flags five partial branches whose false-edge is never taken:

  * 79->81   -- ``enqueue``: the worker task ALREADY exists (a second enqueue), so
                the lazy ``asyncio.create_task`` is skipped and the batch is queued
                directly.
  * 140->142 -- ``replay_pending_terminal_event``: the post succeeds but
                ``self.pending_terminal_batch`` was swapped out mid-replay (a newer
                terminal batch arrived), so the ``is batch`` guard is False and the
                newer pending batch is preserved while the store key is still cleared.
  * 222->exit -- ``_store_pending_terminal_batch``: a non-terminal batch yields a
                None key, so nothing is stored.
  * 231->exit -- ``_clear_pending_terminal_batch``: the stored batch under the key is
                a DIFFERENT object/value than the one passed in, so the entry is NOT
                popped (don't clobber a newer batch's pending entry).
  * 259->266 -- ``replay_stored_terminal_event``: the post raises but ``logger`` is
                None, so the log block is skipped and the function returns False.

Mirrors ``test_lesson_forwarder.py``: real ``LessonEventForwarder`` + module-level
store helpers, real async worker, real terminal-batch bookkeeping.
"""

import unittest

from core.lesson import forwarder as forwarder_module
from core.lesson.forwarder import (
    _PENDING_TERMINAL_BATCHES,
    LessonEventForwarder,
    _clear_pending_terminal_batch,
    _store_pending_terminal_batch,
    replay_stored_terminal_event,
)


class ForwarderBranchGapTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _PENDING_TERMINAL_BATCHES.clear()

    def tearDown(self):
        _PENDING_TERMINAL_BATCHES.clear()

    # ── 79->81: second enqueue reuses the already-started worker ──────────────────
    async def test_enqueue_reuses_existing_worker(self):
        """A second enqueue while the worker is already running -> the
        ``if self._worker is None:`` lazy-start (line 79) is skipped and the batch is
        queued directly (branch 79->81). Mutation guard: if the guard were dropped, a
        second create_task would orphan the first worker; here we assert the SAME
        worker object handled both batches."""
        posted = []

        async def _post(_client, _base_url, _device_id, batch, *, token=None):
            posted.append(batch)

        forwarder = LessonEventForwarder(
            device_id="dev1",
            base_url="http://backend.test/v1",
            post_fn=_post,
        )

        forwarder.enqueue({"events": [{"type": "step_completed", "sequence": 1}]})
        worker_after_first = forwarder._worker
        forwarder.enqueue({"events": [{"type": "step_completed", "sequence": 2}]})
        worker_after_second = forwarder._worker
        await forwarder._queue.join()

        self.assertIs(worker_after_first, worker_after_second)
        self.assertEqual(len(posted), 2)

        await forwarder.aclose()

    # ── 140->142: pending batch swapped mid-replay; is-guard False, store cleared ──
    async def test_replay_success_with_swapped_pending_batch_keeps_newer(self):
        """The replay post succeeds, but a NEWER terminal batch replaced
        ``pending_terminal_batch`` during the await -> the ``is batch`` guard (line 140)
        is False, so the newer pending batch is preserved while the store key clears
        (branch 140->142). Mutation guard: removing the ``is batch`` check would null
        out the newer pending batch and this ``assertIs(newer)`` would fail."""
        original = {"assignmentId": "a1", "events": [{"type": "lesson_completed"}]}
        newer = {"assignmentId": "a1", "events": [{"type": "lesson_completed"}]}

        async def _post(_batch):
            # A concurrent newer terminal event landed while the replay was in flight.
            forwarder.pending_terminal_batch = newer
            return {"accepted": 1}

        forwarder = LessonEventForwarder(
            device_id="dev1",
            base_url="http://backend.test/v1",
            post_fn=lambda *a, **k: None,
        )
        forwarder._post = _post  # type: ignore[assignment]
        forwarder.pending_terminal_batch = original
        _store_pending_terminal_batch("dev1", original)

        self.assertTrue(await forwarder.replay_pending_terminal_event())

        # Newer pending batch untouched; the store entry for the original cleared.
        self.assertIs(forwarder.pending_terminal_batch, newer)
        self.assertEqual(_PENDING_TERMINAL_BATCHES, {})

    # ── 222->exit: storing a non-terminal batch (None key) is a no-op ─────────────
    def test_store_non_terminal_batch_is_noop(self):
        """A non-terminal batch -> _pending_terminal_key returns None -> the
        ``if key is not None:`` block (line 222) is skipped and nothing is stored
        (branch 222->exit)."""
        non_terminal = {"assignmentId": "a1", "events": [{"type": "step_completed"}]}

        _store_pending_terminal_batch("dev1", non_terminal)

        self.assertEqual(_PENDING_TERMINAL_BATCHES, {})

    # ── 231->exit: clearing when the stored batch differs leaves the entry ────────
    def test_clear_pending_leaves_entry_when_stored_batch_differs(self):
        """The store holds a DIFFERENT terminal batch under the key than the one being
        cleared -> ``stored is batch or stored == batch`` (line 231) is False, so the
        entry is NOT popped (branch 231->exit). Protects a newer pending batch from a
        stale clear. Mutation guard: weakening the equality check to always-pop would
        drop the live entry and this assertion would see an empty store."""
        stored = {"assignmentId": "a1", "events": [{"type": "lesson_completed"}]}
        _store_pending_terminal_batch("dev1", stored)
        self.assertEqual(_PENDING_TERMINAL_BATCHES, {("dev1", "a1"): stored})

        # Same key (assignmentId a1) but a different terminal payload.
        different = {"assignmentId": "a1", "events": [{"type": "lesson_failed"}]}
        _clear_pending_terminal_batch("dev1", different)

        # The live entry survives because the stored batch != the one being cleared.
        self.assertEqual(_PENDING_TERMINAL_BATCHES, {("dev1", "a1"): stored})

    # ── 259->266: replay post raises with logger=None -> skip log, return False ────
    async def test_replay_stored_terminal_event_failure_without_logger(self):
        """The stored-terminal replay post raises but ``logger`` is None -> the
        ``if logger is not None:`` block (line 259) is skipped and the function returns
        False (branch 259->266). Proves replay is safe without a logger attached."""
        batch = {"assignmentId": "a1", "events": [{"type": "lesson_completed"}]}
        _PENDING_TERMINAL_BATCHES[("dev1", "a1")] = batch

        async def _post(_client, _base_url, _device_id, _batch, *, token=None):
            raise RuntimeError("offline")

        result = await replay_stored_terminal_event(
            device_id="dev1",
            assignment_id="a1",
            base_url="http://backend.test/v1",
            post_fn=_post,
            logger=None,
        )

        self.assertFalse(result)
        # Batch left in the store for the next reconnect (not cleared on failure).
        self.assertEqual(_PENDING_TERMINAL_BATCHES, {("dev1", "a1"): batch})


if __name__ == "__main__":
    unittest.main()
