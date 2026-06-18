import unittest

import httpx

from core.lesson.forwarder import LessonEventForwarder


def _http_status_error(status_code=500):
    request = httpx.Request("POST", "http://backend.test/v1/devices/dev1/lesson-events")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError("backend hiccup", request=request, response=response)


class LessonEventForwarderDurabilityTest(unittest.IsolatedAsyncioTestCase):
    async def test_retryable_5xx_retries_then_dead_letters_and_counts_dropped_event(self):
        attempts = 0

        async def _post(_client, _base_url, _device_id, _batch, *, token=None):
            nonlocal attempts
            attempts += 1
            raise _http_status_error(500)

        forwarder = LessonEventForwarder(
            device_id="dev1",
            base_url="http://backend.test/v1",
            post_fn=_post,
            retry_backoff_sec=0,
            max_reenqueue_attempts=2,
        )
        batch = {
            "assignmentId": "a1",
            "sessionId": "s1",
            "events": [{"type": "step_completed", "sequence": 7}],
        }

        forwarder.enqueue(batch)
        await forwarder._queue.join()

        self.assertEqual(attempts, 3)
        self.assertEqual(forwarder.dropped_events_total, 1)
        self.assertEqual(forwarder.dead_letters, [batch])

        await forwarder.aclose()

    async def test_terminal_lifecycle_batch_replays_once_until_backend_acks(self):
        calls = []

        async def _post(_client, _base_url, _device_id, batch, *, token=None):
            calls.append(batch)
            if len(calls) == 1:
                raise _http_status_error(500)
            return {"accepted": 1}

        forwarder = LessonEventForwarder(
            device_id="dev1",
            base_url="http://backend.test/v1",
            post_fn=_post,
            retry_backoff_sec=0,
            max_reenqueue_attempts=0,
        )
        terminal = {
            "assignmentId": "a1",
            "sessionId": "s1",
            "events": [{"type": "lesson_completed", "completedAt": 1_700_000_000_000}],
        }

        forwarder.enqueue(terminal)
        await forwarder._queue.join()
        self.assertEqual(forwarder.dropped_events_total, 1)
        self.assertEqual(forwarder.pending_terminal_batch, terminal)

        replayed = await forwarder.replay_pending_terminal_event()
        self.assertTrue(replayed)
        self.assertIsNone(forwarder.pending_terminal_batch)

        replayed_again = await forwarder.replay_pending_terminal_event()
        self.assertFalse(replayed_again)
        self.assertEqual(calls, [terminal, terminal])

        await forwarder.aclose()


if __name__ == "__main__":
    unittest.main()
