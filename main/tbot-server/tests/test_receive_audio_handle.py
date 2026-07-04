import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from core.handle import receiveAudioHandle
from core.providers.tts.dto.dto import SentenceType


class _Logger:
    def __init__(self):
        self.errors = []
        self.infos = []

    def bind(self, **_kwargs):
        return self

    def error(self, message):
        self.errors.append(message)

    def info(self, message):
        self.infos.append(message)


class _Queue:
    def __init__(self):
        self.items = []

    def put(self, item):
        self.items.append(item)


class _Executor:
    def __init__(self):
        self.calls = []

    def submit(self, fn, *args):
        self.calls.append((fn, args))


def _conn(**overrides):
    data = {
        "logger": _Logger(),
        "vad": SimpleNamespace(is_vad=Mock(return_value=False)),
        "asr": SimpleNamespace(receive_audio=AsyncMock()),
        "last_activity_time": 0.0,
        "config": {"close_connection_no_voice_time": 120, "end_prompt": {}},
        "close_after_chat": False,
        "client_abort": True,
        "closed": False,
        "need_bind": False,
        "bind_code": None,
        "max_output_size": 0,
        "headers": {"device-id": "device-1"},
        "client_is_speaking": False,
        "client_listen_mode": "auto",
        "current_speaker": None,
        "executor": _Executor(),
        "chat": Mock(),
        "tts": SimpleNamespace(tts_audio_queue=_Queue()),
    }

    async def close():
        conn.closed = True

    data.update(overrides)
    conn = SimpleNamespace(**data)
    conn.close = close
    return conn


class ReceiveAudioHandleTest(unittest.IsolatedAsyncioTestCase):
    async def test_handle_audio_message_resumes_vad_after_wakeup_without_forwarding_audio(self):
        conn = _conn(just_woken_up=True)
        created = []

        def create_task(coro):
            coro.close()
            task = SimpleNamespace(done=lambda: False)
            created.append(task)
            return task

        with patch.object(receiveAudioHandle.asyncio, "create_task", side_effect=create_task):
            await receiveAudioHandle.handleAudioMessage(conn, b"audio")
            await receiveAudioHandle.handleAudioMessage(conn, b"audio")

        self.assertEqual(len(created), 1)
        conn.asr.receive_audio.assert_not_awaited()

    async def test_handle_audio_message_updates_idle_state_and_forwards_to_asr(self):
        conn = _conn()
        conn.vad.is_vad.return_value = True

        with patch.object(receiveAudioHandle.time, "time", return_value=10.0):
            await receiveAudioHandle.handleAudioMessage(conn, b"audio")

        self.assertEqual(conn.last_activity_time, 10000.0)
        conn.asr.receive_audio.assert_awaited_once_with(conn, b"audio", True)

    async def test_resume_vad_detection_clears_wakeup_flag_after_delay(self):
        conn = _conn(just_woken_up=True)

        with patch.object(receiveAudioHandle.asyncio, "sleep", new=AsyncMock()) as sleep:
            await receiveAudioHandle.resume_vad_detection(conn)

        sleep.assert_awaited_once_with(2)
        self.assertFalse(conn.just_woken_up)


class StartToChatTest(unittest.IsolatedAsyncioTestCase):
    async def test_google_live_start_to_chat_does_not_enter_classic_pipeline(self):
        conn = _conn(config={"voice_mode": {"type": "google_live"}})
        sent = []

        async def send(_conn, text):
            sent.append(text)

        with patch.object(receiveAudioHandle, "handle_user_intent", new=AsyncMock(return_value=False)) as intent, patch.object(
            receiveAudioHandle, "send_stt_message", new=send
        ):
            await receiveAudioHandle.startToChat(conn, "xin chao")

        intent.assert_not_awaited()
        self.assertEqual(sent, [])
        self.assertEqual(conn.executor.calls, [])

    async def test_start_to_chat_preserves_json_payload_for_speaker_metadata(self):
        conn = _conn()
        sent = []

        async def send(_conn, text):
            sent.append(text)

        with patch.object(receiveAudioHandle, "handle_user_intent", new=AsyncMock(return_value=False)) as intent, patch.object(
            receiveAudioHandle, "send_stt_message", new=send
        ):
            await receiveAudioHandle.startToChat(conn, '{"speaker":"Ada","language":"en","content":"hello"}')

        self.assertEqual(conn.current_speaker, "Ada")
        intent.assert_awaited_once_with(conn, '{"speaker":"Ada","language":"en","content":"hello"}')
        self.assertEqual(sent, ['{"speaker":"Ada","language":"en","content":"hello"}'])
        self.assertFalse(conn.client_abort)
        self.assertEqual(conn.executor.calls, [(conn.chat, ('{"speaker":"Ada","language":"en","content":"hello"}',))])

    async def test_start_to_chat_handles_malformed_or_incomplete_json_as_plain_text(self):
        conn = _conn()

        with patch.object(receiveAudioHandle, "handle_user_intent", new=AsyncMock(return_value=True)) as intent:
            await receiveAudioHandle.startToChat(conn, '{"speaker":"Ada","content":"hello"}')
            await receiveAudioHandle.startToChat(conn, "{bad json}")

        self.assertIsNone(conn.current_speaker)
        self.assertEqual(intent.await_args_list[0].args[1], '{"speaker":"Ada","content":"hello"}')
        self.assertEqual(intent.await_args_list[1].args[1], "{bad json}")
        self.assertEqual(conn.executor.calls, [])

    async def test_start_to_chat_routes_bind_output_limit_abort_and_intent_handled_paths(self):
        bind_conn = _conn(need_bind=True, bind_code="123")
        limit_conn = _conn(max_output_size=5)
        speaking_conn = _conn(client_is_speaking=True, client_listen_mode="auto")
        manual_conn = _conn(client_is_speaking=True, client_listen_mode="manual")
        calls = []

        with patch.object(receiveAudioHandle, "check_bind_device", new=AsyncMock(side_effect=lambda c: calls.append(("bind", c)))), patch.object(
            receiveAudioHandle, "check_device_output_limit", return_value=True
        ), patch.object(receiveAudioHandle, "max_out_size", new=AsyncMock(side_effect=lambda c: calls.append(("limit", c)))), patch.object(
            receiveAudioHandle, "handleAbortMessage", new=AsyncMock(side_effect=lambda c: calls.append(("abort", c)))) , patch.object(
            receiveAudioHandle, "handle_user_intent", new=AsyncMock(return_value=True)
        ):
            await receiveAudioHandle.startToChat(bind_conn, "hi")
            await receiveAudioHandle.startToChat(limit_conn, "hi")
            await receiveAudioHandle.startToChat(speaking_conn, "hi")
            await receiveAudioHandle.startToChat(manual_conn, "hi")

        self.assertEqual(calls, [("bind", bind_conn), ("limit", limit_conn), ("abort", speaking_conn)])
        self.assertEqual(speaking_conn.executor.calls, [])
        self.assertEqual(manual_conn.executor.calls, [])


class IdleAndPromptTest(unittest.IsolatedAsyncioTestCase):
    async def test_no_voice_close_connect_closes_without_prompt_when_disabled(self):
        conn = _conn(
            last_activity_time=1000.0,
            config={"close_connection_no_voice_time": 1, "end_prompt": {"enable": False}},
        )

        with patch.object(receiveAudioHandle.time, "time", return_value=3.0):
            await receiveAudioHandle.no_voice_close_connect(conn, False)

        self.assertTrue(conn.close_after_chat)
        self.assertFalse(conn.client_abort)
        self.assertTrue(conn.closed)

    async def test_no_voice_close_connect_starts_default_or_configured_end_prompt(self):
        default_conn = _conn(last_activity_time=1000.0, config={"close_connection_no_voice_time": 1, "end_prompt": {}})
        malformed_conn = _conn(last_activity_time=1000.0, config={"close_connection_no_voice_time": 1, "end_prompt": "bad"})
        custom_conn = _conn(
            last_activity_time=1000.0,
            config={"close_connection_no_voice_time": 1, "end_prompt": {"prompt": "bye prompt"}},
        )
        prompts = []

        async def start(_conn, prompt):
            prompts.append(prompt)

        with patch.object(receiveAudioHandle.time, "time", return_value=3.0), patch.object(
            receiveAudioHandle, "startToChat", new=start
        ):
            await receiveAudioHandle.no_voice_close_connect(default_conn, False)
            await receiveAudioHandle.no_voice_close_connect(malformed_conn, False)
            await receiveAudioHandle.no_voice_close_connect(custom_conn, False)

        self.assertIn("Time flies", prompts[0])
        self.assertIn("Time flies", prompts[1])
        self.assertEqual(prompts[2], "bye prompt")

    async def test_max_out_size_sends_prompt_audio_and_marks_close_after_chat(self):
        conn = _conn()
        sent = []

        async def send(_conn, text):
            sent.append(text)

        with patch.object(receiveAudioHandle, "send_stt_message", new=send), patch.object(
            receiveAudioHandle, "audio_to_data", new=AsyncMock(return_value=[b"opus"])
        ) as audio_to_data:
            await receiveAudioHandle.max_out_size(conn)

        audio_to_data.assert_awaited_once_with("config/assets/max_output_size.wav")
        self.assertEqual(conn.tts.tts_audio_queue.items[0][0], SentenceType.LAST)
        self.assertEqual(conn.tts.tts_audio_queue.items[0][1], [b"opus"])
        self.assertEqual(sent, ["Sorry, I have something to do now. Let’s chat tomorrow at this time. Deal! See you tomorrow,Bye!"])
        self.assertTrue(conn.close_after_chat)
        self.assertFalse(conn.client_abort)


    async def test_google_live_max_out_size_does_not_queue_classic_prompt_audio(self):
        conn = _conn(config={"voice_mode": {"type": "google_live"}})

        with patch.object(receiveAudioHandle, "send_stt_message", new=AsyncMock()) as send, patch.object(
            receiveAudioHandle, "audio_to_data", new=AsyncMock(return_value=[b"opus"])
        ) as audio_to_data:
            await receiveAudioHandle.max_out_size(conn)

        send.assert_not_awaited()
        audio_to_data.assert_not_awaited()
        self.assertEqual(conn.tts.tts_audio_queue.items, [])
        self.assertFalse(conn.close_after_chat)

class BindDeviceTest(unittest.IsolatedAsyncioTestCase):
    async def test_google_live_check_bind_device_does_not_queue_classic_prompt_audio(self):
        conn = _conn(config={"voice_mode": {"type": "google_live"}}, bind_code="123456")

        with patch.object(receiveAudioHandle, "send_stt_message", new=AsyncMock()) as send, patch.object(
            receiveAudioHandle, "audio_to_data", new=AsyncMock(return_value=[b"opus"])
        ) as audio_to_data:
            await receiveAudioHandle.check_bind_device(conn)

        send.assert_not_awaited()
        audio_to_data.assert_not_awaited()
        self.assertEqual(conn.tts.tts_audio_queue.items, [])

    async def test_check_bind_device_rejects_invalid_code(self):
        conn = _conn(bind_code="123")
        sent = []

        async def send(_conn, text):
            sent.append(text)

        with patch.object(receiveAudioHandle, "send_stt_message", new=send):
            await receiveAudioHandle.check_bind_device(conn)

        self.assertIn("Invalid binding code format: 123", conn.logger.errors[0])
        self.assertEqual(sent, ["Binding code formatErrorPlease check config."])
        self.assertEqual(conn.tts.tts_audio_queue.items, [])

    async def test_check_bind_device_plays_code_digits_and_continues_after_digit_failure(self):
        conn = _conn(bind_code="123456")
        sent = []

        async def send(_conn, text):
            sent.append(text)

        async def audio_to_data(path):
            if path.endswith("3.wav"):
                raise RuntimeError("missing digit")
            return [path.encode()]

        with patch.object(receiveAudioHandle, "send_stt_message", new=send), patch.object(
            receiveAudioHandle, "audio_to_data", new=audio_to_data
        ):
            await receiveAudioHandle.check_bind_device(conn)

        self.assertEqual(sent, ["pleaseLoginControl panel, enter123456,Bind device."])
        self.assertEqual(conn.tts.tts_audio_queue.items[0][0], SentenceType.FIRST)
        self.assertEqual(conn.tts.tts_audio_queue.items[-1], (SentenceType.LAST, [], None))
        self.assertEqual(len([item for item in conn.tts.tts_audio_queue.items if item[0] == SentenceType.MIDDLE]), 5)
        self.assertIn("Play number audio failed: missing digit", conn.logger.errors[0])

    async def test_check_bind_device_without_code_plays_unbound_prompt(self):
        conn = _conn(bind_code=None)
        sent = []

        async def send(_conn, text):
            sent.append(text)

        with patch.object(receiveAudioHandle, "send_stt_message", new=send), patch.object(
            receiveAudioHandle, "audio_to_data", new=AsyncMock(return_value=[b"not-found"])
        ) as audio_to_data:
            await receiveAudioHandle.check_bind_device(conn)

        audio_to_data.assert_awaited_once_with("config/assets/bind_not_found.wav")
        self.assertFalse(conn.client_abort)
        self.assertIn("No version found", sent[0])
        self.assertEqual(conn.tts.tts_audio_queue.items[0][0], SentenceType.LAST)


if __name__ == "__main__":
    unittest.main()
