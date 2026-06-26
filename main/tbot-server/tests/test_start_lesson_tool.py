import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from plugins_func.functions import start_lesson as start_lesson_module
from plugins_func.register import Action


class _Logger:
    def bind(self, **kwargs):
        return self

    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None

    def error(self, *args, **kwargs):
        return None


class _Conn:
    def __init__(self, *, loop=None, enabled=True, pull=None, sample=False):
        self.loop = loop
        self.logger = _Logger()
        self.lesson_pull_task = None
        self.lesson_runtime = None
        self.pull_calls = 0
        self._enabled = enabled
        self._sample = sample
        self._pull = pull

    def _lesson_runtime_enabled(self):
        return self._enabled

    def _sample_lesson_enabled(self):
        return self._sample

    async def _lesson_pull_on_connect(self):
        self.pull_calls += 1
        if self._pull is not None:
            return await self._pull()
        return object()


class _VoiceProvider:
    def __init__(self):
        self.acks = []

    async def _send_lesson_start_ack(self, action_response):
        self.acks.append(action_response)


class StartLessonToolTest(unittest.IsolatedAsyncioTestCase):
    async def test_returns_disabled_response_without_scheduling_pull(self):
        conn = _Conn(loop=asyncio.get_running_loop(), enabled=False)

        response = start_lesson_module.start_lesson(conn)

        self.assertEqual(response.action, Action.RESPONSE)
        self.assertEqual(response.result, "Lesson runtime disabled")
        self.assertEqual(response.response, "Robot chưa sẵn sàng vào bài học lúc này.")
        self.assertIsNone(conn.lesson_pull_task)
        self.assertEqual(conn.pull_calls, 0)

    async def test_returns_busy_response_when_event_loop_is_missing(self):
        conn = _Conn(loop=None, enabled=True)

        response = start_lesson_module.start_lesson(conn)

        self.assertEqual(response.action, Action.RESPONSE)
        self.assertEqual(response.result, "System busy")
        self.assertIsNone(conn.lesson_pull_task)

    async def test_sample_flag_schedules_sample_lesson_instead_of_assignment_pull(self):
        # Sample demo flag ON, runtime (assignment) OFF -> load the built-in sample,
        # ignoring any backend assignment, and never call the assignment pull.
        conn = _Conn(loop=asyncio.get_running_loop(), enabled=False, sample=True)
        sample_calls = []

        async def _fake_sample(c):
            sample_calls.append(c)
            return object()

        with patch("core.lesson.sample.start_sample_lesson", _fake_sample):
            response = start_lesson_module.start_lesson(conn)
            self.assertEqual(response.action, Action.RECORD)
            self.assertIsNotNone(conn.lesson_pull_task)
            await conn.lesson_pull_task

        self.assertEqual(len(sample_calls), 1)
        self.assertIs(sample_calls[0], conn)
        self.assertEqual(conn.pull_calls, 0)  # assignment pull never invoked

    async def test_sample_flag_alone_satisfies_the_enabled_gate(self):
        # Even with runtime_enabled OFF, the sample flag alone admits the lesson layer.
        conn = _Conn(loop=asyncio.get_running_loop(), enabled=False, sample=True)

        async def _fake_sample(c):
            return object()

        with patch("core.lesson.sample.start_sample_lesson", _fake_sample):
            response = start_lesson_module.start_lesson(conn)
            self.assertEqual(response.action, Action.RECORD)
            self.assertNotEqual(response.result, "Lesson runtime disabled")
            if conn.lesson_pull_task is not None:
                await conn.lesson_pull_task

    async def test_cancels_prior_pull_task_when_user_explicitly_starts_lesson(self):
        loop = asyncio.get_running_loop()
        prior = loop.create_task(asyncio.sleep(60))
        conn = _Conn(loop=loop, enabled=True)
        conn.lesson_pull_task = prior

        response = start_lesson_module.start_lesson(conn)
        await conn.lesson_pull_task

        self.assertEqual(response.action, Action.RECORD)
        self.assertTrue(prior.cancelled())
        self.assertEqual(conn.pull_calls, 1)

    async def test_cancelled_lesson_pull_task_is_not_reported_as_unhandled_loop_error(self):
        loop = asyncio.get_running_loop()
        loop_errors = []
        previous_handler = loop.get_exception_handler()
        loop.set_exception_handler(lambda _loop, context: loop_errors.append(context))

        async def _slow_pull():
            await asyncio.sleep(60)
            return object()

        conn = _Conn(loop=loop, enabled=True, pull=_slow_pull)

        try:
            response = start_lesson_module.start_lesson(conn)
            conn.lesson_pull_task.cancel()
            await asyncio.sleep(0)
            await asyncio.sleep(0)
        finally:
            loop.set_exception_handler(previous_handler)

        self.assertEqual(response.action, Action.RECORD)
        self.assertTrue(conn.lesson_pull_task.cancelled())
        self.assertEqual(loop_errors, [])

    async def test_falls_back_to_runtime_entry_point_when_connection_wrapper_is_missing(self):
        calls = []

        async def _fake_maybe_start(conn):
            calls.append(conn)
            return object()

        conn = SimpleNamespace(
            loop=asyncio.get_running_loop(),
            logger=_Logger(),
            lesson_pull_task=None,
            _lesson_runtime_enabled=lambda: True,
        )

        with patch("core.lesson.runtime.maybe_start_lesson_on_connect", new=_fake_maybe_start):
            response = start_lesson_module.start_lesson(conn)
            await conn.lesson_pull_task

        self.assertEqual(response.action, Action.RECORD)
        self.assertEqual(calls, [conn])

    async def test_feedback_scheduler_noops_when_message_or_sender_is_missing(self):
        conn = _Conn(loop=asyncio.get_running_loop(), enabled=True)

        start_lesson_module._schedule_lesson_start_feedback(conn, "")
        start_lesson_module._schedule_lesson_start_feedback(conn, "Cannot start")

        await asyncio.sleep(0)
        self.assertEqual(getattr(conn, "sent_feedback", []), [])

    async def test_failed_async_pull_sends_audible_failure_feedback(self):
        async def _pull_without_assignment():
            conn.lesson_start_status = {
                "code": "NO_CURRENT_ASSIGNMENT",
                "message": "Robot chưa có bài học nào được giao.",
            }
            return None

        conn = _Conn(loop=asyncio.get_running_loop(), enabled=True, pull=_pull_without_assignment)
        conn.voice_provider = _VoiceProvider()

        response = start_lesson_module.start_lesson(conn)
        for _ in range(10):
            if conn.voice_provider.acks:
                break
            await asyncio.sleep(0)

        self.assertEqual(response.action, Action.RECORD)
        self.assertEqual(len(conn.voice_provider.acks), 1)
        ack = conn.voice_provider.acks[0]
        self.assertEqual(ack.action, Action.RESPONSE)
        self.assertEqual(ack.result, "lesson_start_failed")
        self.assertEqual(ack.response, "Robot chưa có bài học nào được giao.")

    async def test_outer_guard_returns_friendly_error_when_enabled_check_raises(self):
        class _ExplodingConn(_Conn):
            def _lesson_runtime_enabled(self):
                raise RuntimeError("flag exploded")

        response = start_lesson_module.start_lesson(_ExplodingConn(loop=asyncio.get_running_loop()))

        self.assertEqual(response.action, Action.RESPONSE)
        self.assertEqual(response.result, "flag exploded")
        self.assertEqual(response.response, "Sorry, I couldn't start the lesson.")


if __name__ == "__main__":
    unittest.main()
