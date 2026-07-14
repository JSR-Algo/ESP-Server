import asyncio
import unittest

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
    def __init__(self, *, loop=None, enabled=True, pull=None):
        self.loop = loop
        self.logger = _Logger()
        self.lesson_pull_task = None
        self.pull_calls = 0
        self._enabled = enabled
        self._pull = pull
        self.device_id = "robot-01"
        self.config = {
            "lesson": {
                "runtime_enabled": enabled,
                "rollout_device_allowlist": [self.device_id],
            }
        }

    def _lesson_runtime_enabled(self):
        return self._enabled

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


# Terminal-failure status codes set by core/lesson/runtime.py right before it
# returns None. Each MUST trigger the pre-authored audible Vietnamese failure
# feedback so a spoken first-trigger never leaves the child in silence after
# "Okay, let's start the lesson."
_TERMINAL_FAILURE_CASES = [
    ("ASSIGNMENT_INVALID", "Máy chủ gửi bài học thiếu thông tin phiên bản."),
    ("MANIFEST_IDENTITY_MISMATCH", "Bài học tải về không khớp với bài đã giao."),
    ("MANIFEST_CHECKSUM_MISSING", "Robot chưa kiểm tra được nội dung bài học."),
    ("MANIFEST_CHECKSUM_MISMATCH", "Nội dung bài học bị lỗi khi tải về."),
]


class StartLessonFailureCodesTest(unittest.IsolatedAsyncioTestCase):
    async def _run_case(self, code: str, message: str):
        async def _pull_with_failure():
            conn.lesson_start_status = {"code": code, "message": message}
            return None

        conn = _Conn(
            loop=asyncio.get_running_loop(),
            enabled=True,
            pull=_pull_with_failure,
        )
        conn.voice_provider = _VoiceProvider()

        response = start_lesson_module.start_lesson(conn)
        for _ in range(10):
            if conn.voice_provider.acks:
                break
            await asyncio.sleep(0)

        self.assertEqual(response.action, Action.RECORD)
        self.assertEqual(
            len(conn.voice_provider.acks),
            1,
            f"code {code} did not trigger audible failure feedback",
        )
        ack = conn.voice_provider.acks[0]
        self.assertEqual(ack.action, Action.RESPONSE)
        self.assertEqual(ack.result, "lesson_start_failed")
        self.assertEqual(ack.response, message)

    async def test_assignment_invalid_sends_audible_failure_feedback(self):
        await self._run_case(*_TERMINAL_FAILURE_CASES[0])

    async def test_manifest_identity_mismatch_sends_audible_failure_feedback(self):
        await self._run_case(*_TERMINAL_FAILURE_CASES[1])

    async def test_manifest_checksum_missing_sends_audible_failure_feedback(self):
        await self._run_case(*_TERMINAL_FAILURE_CASES[2])

    async def test_manifest_checksum_mismatch_sends_audible_failure_feedback(self):
        await self._run_case(*_TERMINAL_FAILURE_CASES[3])

    async def test_all_four_terminal_codes_are_in_failure_set(self):
        for code, _ in _TERMINAL_FAILURE_CASES:
            self.assertIn(
                code,
                start_lesson_module._FAILURE_STATUS_CODES,
                f"{code} is a terminal-failure code but is missing from "
                "_FAILURE_STATUS_CODES, so the child hears silence",
            )


if __name__ == "__main__":
    unittest.main()
