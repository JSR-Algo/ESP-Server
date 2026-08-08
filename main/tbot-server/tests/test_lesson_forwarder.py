import os
import sys
import unittest
from unittest import mock

import httpx

sys.path.insert(0, os.path.dirname(__file__))

import test_lesson_runtime as L

from core.lesson import forwarder as forwarder_module
from core.lesson.forwarder import (
    LessonEventForwarder,
    _PENDING_TERMINAL_BATCHES,
    _clear_pending_terminal_batch,
    _pending_terminal_key,
    _store_pending_terminal_batch,
    replay_stored_terminal_event,
)


def _http_status_error(status_code=500):
    request = httpx.Request("POST", "http://backend.test/v1/devices/dev1/lesson-events")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError("backend hiccup", request=request, response=response)


class _Logger:
    def __init__(self, *, fail_bind=False, fail_warning=False):
        self.fail_bind = fail_bind
        self.fail_warning = fail_warning
        self.messages = []

    def bind(self, **_kwargs):
        if self.fail_bind:
            raise RuntimeError("bind failed")
        return self

    def warning(self, message):
        if self.fail_warning:
            raise RuntimeError("warning failed")
        self.messages.append(("warning", message))


class _Client:
    def __init__(self, *, fail_close=False):
        self.fail_close = fail_close
        self.closed = False

    async def aclose(self):
        self.closed = True
        if self.fail_close:
            raise RuntimeError("close failed")


class _TerminalStore:
    def __init__(self):
        self.batches = {}
        self.calls = []

    async def store(self, device_id, batch):
        self.calls.append("store")
        self.batches[(device_id, batch["assignmentId"])] = batch

    async def load(self, device_id, assignment_id):
        self.calls.append("load")
        return self.batches.get((device_id, assignment_id))

    async def clear(self, device_id, batch):
        self.calls.append("clear")
        self.batches.pop((device_id, batch["assignmentId"]), None)


class LessonEventForwarderDurabilityTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _PENDING_TERMINAL_BATCHES.clear()

    def tearDown(self):
        _PENDING_TERMINAL_BATCHES.clear()

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

    async def test_closed_forwarder_drops_new_batches_and_aclose_is_idempotent(self):
        calls = []

        async def _post(_client, _base_url, _device_id, batch, *, token=None):
            calls.append(batch)

        forwarder = LessonEventForwarder(
            device_id="dev1",
            base_url="http://backend.test/v1",
            post_fn=_post,
        )

        await forwarder.aclose()
        await forwarder.aclose()
        forwarder.enqueue({"events": [{"type": "step_completed"}]})

        self.assertEqual(calls, [])
        self.assertIsNone(forwarder._worker)

    async def test_successful_terminal_worker_post_clears_pending_batch(self):
        calls = []

        async def _post(_client, _base_url, _device_id, batch, *, token=None):
            calls.append(batch)
            return {"accepted": 1}

        terminal = {
            "assignmentId": "a1",
            "sessionId": "s1",
            "events": [{"type": "lesson_completed"}],
        }
        forwarder = LessonEventForwarder(
            device_id="dev1",
            base_url="http://backend.test/v1",
            post_fn=_post,
        )

        forwarder.enqueue(terminal)
        await forwarder._queue.join()

        self.assertEqual(calls, [terminal])
        self.assertIsNone(forwarder.pending_terminal_batch)
        self.assertEqual(_PENDING_TERMINAL_BATCHES, {})

        await forwarder.aclose()

    async def test_retryable_failure_waits_backoff_and_logs_reenqueue(self):
        attempts = 0
        logger = _Logger()
        slept = []

        async def _post(_client, _base_url, _device_id, _batch, *, token=None):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise _http_status_error(500)
            return {"accepted": 1}

        async def _sleep(delay):
            slept.append(delay)

        forwarder = LessonEventForwarder(
            device_id="dev1",
            base_url="http://backend.test/v1",
            post_fn=_post,
            logger=logger,
            retry_backoff_sec=0.25,
            max_reenqueue_attempts=1,
        )

        with mock.patch.object(forwarder_module.asyncio, "sleep", _sleep):
            forwarder.enqueue(
                {
                    "assignmentId": "a1",
                    "sessionId": "s1",
                    "events": [{"type": "step_completed"}],
                }
            )
            await forwarder._queue.join()

        self.assertEqual(attempts, 2)
        self.assertEqual(slept, [0.25])
        self.assertIn("re-enqueued attempt=1/1", logger.messages[0][1])
        self.assertIn("session_id=s1", logger.messages[0][1])
        self.assertIn("assignment_id=a1", logger.messages[0][1])

        await forwarder.aclose()

    async def test_failure_log_names_the_status_and_the_rejected_events(self):
        """A rejection must say WHICH events and WHAT status, not just the exception.

        `HTTPStatusError` alone cannot distinguish a retryable 502 from a permanently
        rejected payload, and it never says which event the backend refused — so a
        completion silently missing from the parent dashboard is indistinguishable
        from a transient blip in the log. Observed on a real T5.3 simulated run.
        """
        logger = _Logger()

        async def _post(_client, _base_url, _device_id, _batch, *, token=None):
            raise _http_status_error(422)

        forwarder = LessonEventForwarder(
            device_id="dev1",
            base_url="http://backend.test/v1",
            post_fn=_post,
            logger=logger,
            retry_backoff_sec=0,
            max_reenqueue_attempts=1,
        )

        forwarder.enqueue(
            {
                "assignmentId": "a1",
                "sessionId": "s1",
                "events": [{"type": "step_completed"}, {"type": "lesson_completed"}],
            }
        )
        await forwarder._queue.join()

        joined = " ".join(message for _level, message in logger.messages)
        self.assertIn("status=422", joined)
        self.assertIn("events=step_completed,lesson_completed", joined)
        self.assertIn("dead-lettered", joined)

        await forwarder.aclose()

    async def test_default_post_function_creates_private_http_client(self):
        calls = []
        created_clients = []

        class _FakeHttpx:
            class Timeout:
                def __init__(self, value):
                    self.value = value

            class Limits:
                def __init__(self, **kwargs):
                    self.kwargs = kwargs

            @staticmethod
            def AsyncClient(**kwargs):
                client = _Client()
                client.kwargs = kwargs
                created_clients.append(client)
                return client

        async def _post(client, base_url, device_id, batch, *, token=None):
            calls.append((client, base_url, device_id, batch, token))

        forwarder = LessonEventForwarder(
            device_id="dev1",
            base_url="http://backend.test/v1",
            token="tok",
        )

        with mock.patch.object(forwarder_module, "httpx", _FakeHttpx), mock.patch.object(
            forwarder_module._backend_api, "post_lesson_event", _post
        ):
            await forwarder._post({"events": []})

        self.assertIs(calls[0][0], created_clients[0])
        self.assertEqual(calls[0][1:], ("http://backend.test/v1", "dev1", {"events": []}, "tok"))
        self.assertTrue(forwarder._owns_client)

        await forwarder.aclose()
        self.assertTrue(created_clients[0].closed)
        self.assertIsNone(forwarder._client)

    async def test_default_post_strips_child_speech_before_http_request(self):
        calls = []

        async def _request(client, method, url, **kwargs):
            calls.append((client, method, url, kwargs))
            return {"data": {"stored": True}}

        client = object()
        forwarder = LessonEventForwarder(
            device_id="dev1",
            base_url="http://backend.test/v1",
            token="tok",
            client=client,
        )
        batch = {
            "assignmentId": "a1",
            "sessionId": "s1",
            "events": [
                {
                    "type": "step_completed",
                    "stepId": "listen-1",
                    "result": "success",
                    "detail": {
                        "recognizedText": "con noi barn",
                        "childResponse": "barn",
                        "transcript": "barn raw",
                        "source": "voice_transcript",
                        "nested": {"recognized_text": "nested raw", "keep": 1},
                        "rawSpeech": "con noi raw",
                        "raw_speech": "snake raw",
                        "speechText": "speech text raw",
                        "asrText": "asr raw",
                        "asrTranscript": "asr transcript raw",
                        "voiceText": "voice text raw",
                        "childUtterance": "child utterance raw",
                        "isCorrect": True,
                        "is_correct": True,
                        "score": 0.94,
                        "evaluation": "pass",
                    },
                    "attempts": [
                        {
                            "ChildResponse": "attempt raw",
                            "speech_text": "nested speech raw",
                            "ASRText": "nested asr raw",
                            "IsCorrect": False,
                            "correctness": "correct",
                            "kept": True,
                        }
                    ],
                }
            ],
        }

        with mock.patch.object(forwarder_module._backend_api, "_lesson_request_with_retry", _request):
            await forwarder._post(batch)

        self.assertEqual(len(calls), 1)
        sent_json = calls[0][3]["json"]
        self.assertEqual(calls[0][0], client)
        self.assertEqual(calls[0][1], "POST")
        self.assertEqual(calls[0][2], "http://backend.test/v1/devices/dev1/lesson-events")
        self.assertEqual(calls[0][3]["headers"]["Authorization"], "Bearer tok")
        self.assertEqual(
            sent_json["events"],
            [
                {
                    "type": "step_completed",
                    "stepId": "listen-1",
                    "detail": {"source": "voice_transcript"},
                    "outcome": "success",
                }
            ],
        )
        self.assertEqual(batch["events"][0]["detail"]["recognizedText"], "con noi barn")

    async def test_replay_pending_terminal_failure_keeps_batch_and_logs(self):
        logger = _Logger()

        async def _post(_client, _base_url, _device_id, _batch, *, token=None):
            raise RuntimeError("offline")

        terminal = {
            "assignmentId": "a1",
            "sessionId": "s1",
            "events": [{"type": "lesson_failed"}],
        }
        forwarder = LessonEventForwarder(
            device_id="dev1",
            base_url="http://backend.test/v1",
            post_fn=_post,
            logger=logger,
        )
        forwarder.pending_terminal_batch = terminal

        self.assertFalse(await forwarder.replay_pending_terminal_event())
        self.assertIs(forwarder.pending_terminal_batch, terminal)
        self.assertIn("lesson terminal replay failed", logger.messages[0][1])
        self.assertIn("session_id=s1", logger.messages[0][1])

    async def test_retry_classifier_errors_are_treated_as_not_retryable(self):
        forwarder = LessonEventForwarder(device_id="dev1", base_url="http://backend.test/v1")

        def _classifier(_exc):
            raise RuntimeError("broken classifier")

        with mock.patch.object(
            forwarder_module._backend_api, "_lesson_is_transient", _classifier, create=True
        ):
            self.assertFalse(forwarder._is_retryable(RuntimeError("boom")))

        with mock.patch.object(
            forwarder_module._backend_api, "_lesson_is_transient", None, create=True
        ):
            self.assertFalse(forwarder._is_retryable(RuntimeError("boom")))

    def test_dead_letter_trims_limit_and_counts_only_list_events(self):
        forwarder = LessonEventForwarder(
            device_id="dev1",
            base_url="http://backend.test/v1",
            dead_letter_limit=1,
        )

        forwarder._dead_letter({"events": [{"type": "a"}, {"type": "b"}]})
        forwarder._dead_letter({"events": "not-a-list"})

        self.assertEqual(forwarder.dropped_events_total, 2)
        self.assertEqual(forwarder.dead_letters, [{"events": "not-a-list"}])

    def test_terminal_and_pending_key_helpers_reject_invalid_shapes(self):
        terminal = {"assignmentId": "a1", "events": [{"type": "lesson_cancelled"}]}

        self.assertFalse(LessonEventForwarder._is_terminal_batch({"events": "bad"}))
        self.assertIsNone(_pending_terminal_key("", terminal))
        self.assertIsNone(_pending_terminal_key("dev1", {"assignmentId": "a1", "events": []}))

        _clear_pending_terminal_batch("dev1", {"events": [{"type": "lesson_completed"}]})
        self.assertEqual(_PENDING_TERMINAL_BATCHES, {})

    def test_child_inactivity_abandonment_is_terminal_and_replayable(self):
        terminal = {
            "assignmentId": "a1",
            "sessionId": "s1",
            "events": [{"type": "lesson_abandoned", "reason": "child_inactive"}],
        }

        self.assertTrue(LessonEventForwarder._is_terminal_batch(terminal))
        self.assertEqual(_pending_terminal_key("dev1", terminal), ("dev1", "a1"))

        _store_pending_terminal_batch("dev1", terminal)
        self.assertEqual(_PENDING_TERMINAL_BATCHES, {("dev1", "a1"): terminal})

        _clear_pending_terminal_batch("dev1", terminal)
        self.assertEqual(_PENDING_TERMINAL_BATCHES, {})

    async def test_ensure_client_noops_when_httpx_unavailable_or_client_exists(self):
        existing = object()
        forwarder = LessonEventForwarder(
            device_id="dev1", base_url="http://backend.test/v1", client=existing
        )

        await forwarder._ensure_client()
        self.assertIs(forwarder._client, existing)

        forwarder._client = None
        with mock.patch.object(forwarder_module, "httpx", None):
            await forwarder._ensure_client()
        self.assertIsNone(forwarder._client)

    async def test_aclose_cancels_worker_on_timeout_and_swallows_client_close_errors(self):
        class _Worker:
            def __init__(self):
                self.cancelled = False

            def cancel(self):
                self.cancelled = True

        worker = _Worker()
        client = _Client(fail_close=True)
        forwarder = LessonEventForwarder(
            device_id="dev1", base_url="http://backend.test/v1", client=client
        )
        forwarder._owns_client = True
        forwarder._worker = worker

        async def _wait_for(_task, timeout):
            raise asyncio.TimeoutError()

        import asyncio

        with mock.patch.object(forwarder_module.asyncio, "wait_for", _wait_for):
            await forwarder.aclose()

        self.assertTrue(worker.cancelled)
        self.assertTrue(client.closed)
        self.assertIsNone(forwarder._client)

    def _phase_events(self, forwarder):
        return [
            event
            for batch in forwarder.batches
            for event in batch.get("events", [])
            if event.get("type") == "runtime_phase_changed"
        ]

    async def test_pause_resume_failure_and_abandonment_forward_parent_safe_phases(self):
        from core.lesson.errors import LessonError
        from core.lesson.runtime import LessonRuntime, S_FAILED, S_RUNNING

        forwarder = L._FakeForwarder()
        runtime = LessonRuntime(
            L._FakeConn(),
            assignment=L._build_assignment(),
            manifest=L._build_manifest(),
            asset_cache=L._FakeAssetCache(ready=True),
            forwarder=forwarder,
            manifest_checksum=L._manifest_checksum(),
        )
        runtime.state = S_RUNNING
        runtime._step = runtime._steps[0]
        runtime._step_id = runtime._step["id"]

        await runtime.pause()
        await runtime.resume()
        runtime.last_error = LessonError("RENDER_FAILED", "private debug detail")
        runtime.state = S_FAILED
        await runtime._notify_lesson_terminal("render_failed")

        phases = self._phase_events(forwarder)
        self.assertEqual([event["phase"] for event in phases], ["paused", "resumed", "failed"])
        allowed_phase_keys = {
            "type", "sequence", "phase", "stepId", "stepType", "occurredAt",
        }
        self.assertTrue(all(set(event) <= allowed_phase_keys for event in phases))
        failed = [
            event
            for batch in forwarder.batches
            for event in batch.get("events", [])
            if event.get("type") == "lesson_failed"
        ]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["code"], "RENDER_FAILED")

        abandoned_forwarder = L._FakeForwarder()
        abandoned_runtime = LessonRuntime(
            L._FakeConn(),
            assignment=L._build_assignment(),
            manifest=L._build_manifest(),
            asset_cache=L._FakeAssetCache(ready=True),
            forwarder=abandoned_forwarder,
            manifest_checksum=L._manifest_checksum(),
        )
        abandoned_runtime.state = S_RUNNING
        abandoned_runtime._step = abandoned_runtime._steps[0]
        abandoned_runtime._step_id = abandoned_runtime._step["id"]
        await abandoned_runtime._on_frame_acked(
            {"type": "lesson_stop", "body": {"reason": "STOPPED"}}, {}
        )
        self.assertEqual(
            [event["phase"] for event in self._phase_events(abandoned_forwarder)],
            ["abandoned"],
        )
        abandoned = [
            event
            for batch in abandoned_forwarder.batches
            for event in batch.get("events", [])
            if event.get("type") == "lesson_abandoned"
        ]
        self.assertEqual(abandoned[0]["reason"], "stopped")

    def test_logger_failures_are_swallowed(self):
        LessonEventForwarder(
            device_id="dev1", base_url="http://backend.test/v1", logger=_Logger(fail_bind=True)
        )._log("warning", "ignored")

        LessonEventForwarder(
            device_id="dev1",
            base_url="http://backend.test/v1",
            logger=_Logger(fail_warning=True),
        )._log("warning", "ignored")

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

    async def test_terminal_batch_is_written_to_injected_store_before_post(self):
        store = _TerminalStore()
        order = []

        async def _post(_client, _base_url, _device_id, _batch, *, token=None):
            order.append("post")
            raise _http_status_error(500)

        async def _store(device_id, batch):
            order.append("store")
            await _TerminalStore.store(store, device_id, batch)

        store.store = _store
        terminal = {
            "assignmentId": "a1",
            "sessionId": "s1",
            "events": [{"type": "lesson_completed"}],
        }
        forwarder = LessonEventForwarder(
            device_id="dev1",
            base_url="http://backend.test/v1",
            post_fn=_post,
            terminal_store=store,
            retry_backoff_sec=0,
            max_reenqueue_attempts=0,
        )

        forwarder.enqueue(terminal)
        await forwarder._queue.join()

        self.assertEqual(order, ["store", "post"])
        self.assertEqual(store.batches[("dev1", "a1")], terminal)

        await forwarder.aclose()

    async def test_terminal_replay_store_includes_prior_lesson_started_for_backend_guard(self):
        store = _TerminalStore()

        async def _post(_client, _base_url, _device_id, _batch, *, token=None):
            raise _http_status_error(500)

        started = {
            "assignmentId": "a1",
            "sessionId": "s1",
            "events": [{"type": "lesson_started", "startedAt": 1_700_000_000_000}],
        }
        terminal = {
            "assignmentId": "a1",
            "sessionId": "s1",
            "events": [{"type": "lesson_completed", "completedAt": 1_700_000_010_000}],
        }
        forwarder = LessonEventForwarder(
            device_id="dev1",
            base_url="http://backend.test/v1",
            post_fn=_post,
            terminal_store=store,
            retry_backoff_sec=0,
            max_reenqueue_attempts=0,
        )

        forwarder.enqueue(started)
        await forwarder._queue.join()
        forwarder.enqueue(terminal)
        await forwarder._queue.join()

        stored = store.batches[("dev1", "a1")]
        self.assertEqual(
            [event["type"] for event in stored["events"]],
            ["lesson_started", "lesson_completed"],
        )
        self.assertEqual(stored["assignmentId"], "a1")
        self.assertEqual(stored["sessionId"], "s1")

        await forwarder.aclose()

    async def test_stored_terminal_replay_uses_injected_store_after_process_dict_is_empty(self):
        store = _TerminalStore()
        terminal = {
            "assignmentId": "a1",
            "sessionId": "s1",
            "events": [{"type": "lesson_completed"}],
        }
        await store.store("dev1", terminal)
        _PENDING_TERMINAL_BATCHES.clear()
        calls = []

        async def _post(_client, base_url, device_id, batch, *, token=None):
            calls.append((base_url, device_id, batch, token))

        self.assertTrue(
            await replay_stored_terminal_event(
                device_id="dev1",
                assignment_id="a1",
                base_url="http://backend.test/v1",
                token="tok",
                post_fn=_post,
                terminal_store=store,
            )
        )

        self.assertEqual(calls, [("http://backend.test/v1", "dev1", terminal, "tok")])
        self.assertEqual(store.batches, {})

    async def test_stored_terminal_replay_failure_logs_and_keeps_pending_batch(self):
        terminal = {
            "assignmentId": "a1",
            "sessionId": "s1",
            "events": [{"type": "lesson_completed"}],
        }
        logger = _Logger()

        async def _post(_client, _base_url, _device_id, _batch, *, token=None):
            raise RuntimeError("backend offline")

        _store_pending_terminal_batch("dev1", terminal)

        self.assertFalse(
            await replay_stored_terminal_event(
                device_id="dev1",
                assignment_id="a1",
                base_url="http://backend.test/v1",
                post_fn=_post,
                logger=logger,
            )
        )
        self.assertIn(("dev1", "a1"), _PENDING_TERMINAL_BATCHES)
        self.assertIn("lesson terminal reconnect replay failed", logger.messages[0][1])
        self.assertIn("session_id=s1", logger.messages[0][1])

        self.assertFalse(
            await replay_stored_terminal_event(
                device_id="dev1",
                assignment_id="a1",
                base_url="http://backend.test/v1",
                post_fn=_post,
                logger=_Logger(fail_warning=True),
            )
        )

    async def test_stored_terminal_replay_noops_without_batch_and_clears_on_success(self):
        terminal = {"assignmentId": "a1", "events": [{"type": "lesson_completed"}]}
        calls = []

        async def _post(_client, base_url, device_id, batch, *, token=None):
            calls.append((base_url, device_id, batch, token))

        self.assertFalse(
            await replay_stored_terminal_event(
                device_id="dev1",
                assignment_id="missing",
                base_url="http://backend.test/v1",
                post_fn=_post,
            )
        )

        _store_pending_terminal_batch("dev1", terminal)
        self.assertTrue(
            await replay_stored_terminal_event(
                device_id="dev1",
                assignment_id="a1",
                base_url="http://backend.test/v1",
                token="tok",
                post_fn=_post,
            )
        )

        self.assertEqual(calls, [("http://backend.test/v1", "dev1", terminal, "tok")])
        self.assertEqual(_PENDING_TERMINAL_BATCHES, {})


if __name__ == "__main__":
    unittest.main()
