import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from core.handle import intentHandler
from core.providers.tts.dto.dto import ContentType, SentenceType
from plugins_func.register import Action, ActionResponse


class _Logger:
    def __init__(self):
        self.debugs = []
        self.errors = []
        self.infos = []
        self.warnings = []

    def bind(self, **_kwargs):
        return self

    def debug(self, message):
        self.debugs.append(message)

    def error(self, message):
        self.errors.append(message)

    def info(self, message):
        self.infos.append(message)

    def warning(self, message):
        self.warnings.append(message)


class _Dialogue:
    def __init__(self):
        self.dialogue = []

    def put(self, message):
        self.dialogue.append(message)


class _Queue:
    def __init__(self):
        self.items = []

    def put(self, item):
        self.items.append(item)


class _TTS:
    def __init__(self):
        self.stored = []
        self.spoken = []
        self.tts_text_queue = _Queue()

    def store_tts_text(self, sentence_id, text):
        self.stored.append((sentence_id, text))

    def tts_one_sentence(self, conn, content_type, content_detail):
        self.spoken.append((conn, content_type, content_detail))


class _ImmediateExecutor:
    def __init__(self):
        self.calls = []

    def submit(self, fn):
        self.calls.append(fn)
        return fn()


class _FutureResult:
    def __init__(self, result=None, error=None):
        self._result = result
        self._error = error
        self.timeout = None

    def result(self, timeout=None):
        self.timeout = timeout
        if self._error:
            raise self._error
        return self._result


def _run_threadsafe(result=None, error=None):
    def runner(coro, _loop):
        close = getattr(coro, "close", None)
        if close:
            close()
        return _FutureResult(result=result, error=error)

    return runner


def _conn(**overrides):
    data = {
        "logger": _Logger(),
        "cmd_exit": ["exit", "bye"],
        "intent_type": "llm",
        "intent": None,
        "dialogue": _Dialogue(),
        "sentence_id": "sentence-1",
        "current_speaker": None,
        "client_abort": True,
        "executor": _ImmediateExecutor(),
        "config": {"tool_call_timeout": 7},
        "func_handler": SimpleNamespace(handle_llm_function_call=AsyncMock()),
        "loop": SimpleNamespace(),
        "tts": _TTS(),
        "closed": False,
    }

    async def close():
        data_ref.closed = True

    data.update(overrides)
    data_ref = SimpleNamespace(**data)
    data_ref.close = close
    return data_ref


class HandleUserIntentTest(unittest.IsolatedAsyncioTestCase):
    async def test_json_content_sets_current_speaker_before_llm_analysis(self):
        conn = _conn(intent=SimpleNamespace(detect_intent=AsyncMock(return_value=None)))

        with patch.object(intentHandler, "checkWakeupWords", new=AsyncMock(return_value=False)):
            handled = await intentHandler.handle_user_intent(conn, '{"content":"hello!","speaker":"child"}')

        self.assertFalse(handled)
        self.assertEqual(conn.current_speaker, "child")
        conn.intent.detect_intent.assert_awaited_once()
        self.assertEqual(conn.intent.detect_intent.await_args.args[2], "hello!")

    async def test_invalid_json_and_function_call_mode_return_false_without_llm(self):
        conn = _conn(intent_type="function_call", intent=SimpleNamespace(detect_intent=AsyncMock()))

        with patch.object(intentHandler, "checkWakeupWords", new=AsyncMock(return_value=False)):
            handled = await intentHandler.handle_user_intent(conn, "{bad json}")

        self.assertFalse(handled)
        conn.intent.detect_intent.assert_not_called()

    async def test_direct_exit_sends_stt_and_closes_connection(self):
        conn = _conn()
        sent = []

        async def send(conn_arg, text):
            sent.append((conn_arg, text))

        with patch.object(intentHandler, "send_stt_message", new=send):
            handled = await intentHandler.handle_user_intent(conn, "bye!")

        self.assertTrue(handled)
        self.assertEqual(sent, [(conn, "bye")])
        self.assertTrue(conn.closed)

    async def test_wakeup_word_short_circuits_intent_analysis(self):
        conn = _conn(intent=SimpleNamespace(detect_intent=AsyncMock()))

        with patch.object(intentHandler, "checkWakeupWords", new=AsyncMock(return_value=True)):
            handled = await intentHandler.handle_user_intent(conn, "robot")

        self.assertTrue(handled)
        conn.intent.detect_intent.assert_not_called()

    async def test_missing_or_failed_intent_service_returns_none(self):
        no_service = _conn()
        failed = _conn(intent=SimpleNamespace(detect_intent=AsyncMock(side_effect=RuntimeError("down"))))

        self.assertIsNone(await intentHandler.analyze_intent_with_llm(no_service, "hi"))
        self.assertIsNone(await intentHandler.analyze_intent_with_llm(failed, "hi"))
        self.assertIn("Intent recognition service not initialized", no_service.logger.warnings[0])
        self.assertIn("Intent recognition failed: down", failed.logger.errors[0])

    async def test_llm_intent_result_generates_sentence_id_and_processes_result(self):
        conn = _conn(intent=SimpleNamespace(detect_intent=AsyncMock(return_value='{"intent":"x"}')))

        with patch.object(intentHandler, "checkWakeupWords", new=AsyncMock(return_value=False)), patch.object(
            intentHandler, "process_intent_result", new=AsyncMock(return_value=True)
        ) as process_result, patch.object(intentHandler.uuid, "uuid4", return_value=SimpleNamespace(hex="fixed-id")):
            handled = await intentHandler.handle_user_intent(conn, "hello")

        self.assertTrue(handled)
        self.assertEqual(conn.sentence_id, "fixed-id")
        process_result.assert_awaited_once_with(conn, '{"intent":"x"}', "hello")


class ProcessIntentResultTest(unittest.IsolatedAsyncioTestCase):
    async def test_non_function_invalid_json_and_continue_chat_return_false(self):
        conn = _conn()

        self.assertFalse(await intentHandler.process_intent_result(conn, json.dumps({"intent": "chat"}), "hi"))
        self.assertFalse(await intentHandler.process_intent_result(conn, "not-json", "hi"))
        self.assertFalse(
            await intentHandler.process_intent_result(
                conn,
                json.dumps({"function_call": {"name": "continue_chat"}}),
                "hi",
            )
        )
        self.assertIn("Error handling intent result", conn.logger.errors[0])

    async def test_result_for_context_submits_context_prompt_and_speaks_reply(self):
        conn = _conn(intent=SimpleNamespace(replyResult=Mock(return_value="context reply")))
        sent = []

        async def send(_conn, text):
            sent.append(text)

        with patch.object(intentHandler, "send_stt_message", new=send), patch(
            "core.utils.current_time.get_current_time_info",
            return_value=("10:00", "2026-06-20", "Saturday", "lunar"),
        ):
            handled = await intentHandler.process_intent_result(
                conn,
                json.dumps({"function_call": {"name": "result_for_context"}}),
                "what time is it",
            )

        self.assertTrue(handled)
        self.assertFalse(conn.client_abort)
        self.assertEqual(sent, ["what time is it"])
        self.assertIn("Current time:10:00", conn.intent.replyResult.call_args.args[0])
        self.assertEqual(conn.tts.stored, [("sentence-1", "context reply")])
        self.assertEqual(conn.dialogue.dialogue[-1].role, "assistant")

    async def test_function_call_reports_and_speaks_response_action(self):
        result = ActionResponse(action=Action.RESPONSE, result="ignored", response="done")
        conn = _conn()
        reports = []
        sent = []

        async def send(_conn, text):
            sent.append(text)

        with patch.object(intentHandler, "send_stt_message", new=send), patch.object(
            intentHandler, "enqueue_tool_report", side_effect=lambda *args, **kwargs: reports.append((args, kwargs))
        ), patch.object(intentHandler.asyncio, "run_coroutine_threadsafe", new=_run_threadsafe(result)):
            handled = await intentHandler.process_intent_result(
                conn,
                json.dumps({"function_call": {"name": "move_arm", "arguments": {"side": "left"}}}),
                "raise arm",
            )

        self.assertTrue(handled)
        self.assertFalse(conn.client_abort)
        self.assertEqual(sent, ["raise arm"])
        self.assertEqual(reports[0][0][1:3], ("move_arm", {"side": "left"}))
        self.assertEqual(reports[1][0][1:4], ("move_arm", {"side": "left"}, "ignored"))
        self.assertFalse(reports[1][1]["report_tool_call"])
        self.assertEqual(conn.tts.stored[-1], ("sentence-1", "done"))
        self.assertEqual(conn.tts.tts_text_queue.items[0].sentence_type, SentenceType.FIRST)
        self.assertEqual(conn.tts.spoken[0][1], ContentType.TEXT)

    async def test_function_call_reqlmm_uses_tool_result_or_original_when_reply_missing(self):
        conn = _conn(intent=SimpleNamespace(replyResult=Mock(return_value=None)))
        result = ActionResponse(action=Action.REQLLM, result="tool data", response=None)

        with patch.object(intentHandler, "send_stt_message", new=AsyncMock()), patch.object(
            intentHandler, "enqueue_tool_report"
        ), patch.object(intentHandler.asyncio, "run_coroutine_threadsafe", new=_run_threadsafe(result)):
            handled = await intentHandler.process_intent_result(
                conn,
                json.dumps({"function_call": {"name": "lookup", "arguments": "{\"x\":1}"}}),
                "lookup",
            )

        self.assertTrue(handled)
        self.assertEqual(conn.dialogue.dialogue[-2].role, "tool")
        self.assertEqual(conn.tts.stored[-1], ("sentence-1", "tool data"))

    async def test_function_call_notfound_error_and_legacy_actions_speak_fallbacks(self):
        cases = [
            (ActionResponse(action=Action.NOTFOUND, result="missing-result", response="missing-response"), "missing-response"),
            (ActionResponse(action=Action.ERROR, result="error-result", response=None), "error-result"),
            (ActionResponse(action=Action.RECORD, result="record-result", response=None), "record-result"),
        ]
        for result, expected_text in cases:
            conn = _conn()
            with patch.object(intentHandler, "send_stt_message", new=AsyncMock()), patch.object(
                intentHandler, "enqueue_tool_report"
            ), patch.object(intentHandler.asyncio, "run_coroutine_threadsafe", new=_run_threadsafe(result)):
                handled = await intentHandler.process_intent_result(
                    conn,
                    json.dumps({"function_call": {"name": "tool", "arguments": None}}),
                    "run tool",
                )
            self.assertTrue(handled)
            self.assertEqual(conn.tts.stored[-1], ("sentence-1", expected_text))

    async def test_play_music_legacy_action_does_not_speak(self):
        conn = _conn()
        result = ActionResponse(action=Action.RECORD, result="track", response=None)

        with patch.object(intentHandler, "send_stt_message", new=AsyncMock()), patch.object(
            intentHandler, "enqueue_tool_report"
        ), patch.object(intentHandler.asyncio, "run_coroutine_threadsafe", new=_run_threadsafe(result)):
            handled = await intentHandler.process_intent_result(
                conn,
                json.dumps({"function_call": {"name": "play_music", "arguments": {}}}),
                "play music",
            )

        self.assertTrue(handled)
        self.assertEqual(conn.tts.stored, [])

    async def test_function_call_timeout_reports_error_response(self):
        conn = _conn()

        with patch.object(intentHandler, "send_stt_message", new=AsyncMock()), patch.object(
            intentHandler, "enqueue_tool_report"
        ), patch.object(
            intentHandler.asyncio,
            "run_coroutine_threadsafe",
            new=_run_threadsafe(error=TimeoutError("slow")),
        ):
            handled = await intentHandler.process_intent_result(
                conn,
                json.dumps({"function_call": {"name": "slow_tool", "arguments": {}}}),
                "run slow",
            )

        self.assertTrue(handled)
        self.assertIn("Tool call failed: slow", conn.logger.errors[0])
        self.assertEqual(conn.tts.stored[-1], ("sentence-1", "Tool call timed out, please try again later"))


if __name__ == "__main__":
    unittest.main()
