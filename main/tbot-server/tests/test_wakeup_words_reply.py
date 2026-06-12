import json
import types
import unittest

from core.handle import helloHandle


class _Logger:
    def __init__(self):
        self.warnings = []

    def bind(self, **_kwargs):
        return self

    def info(self, *_args, **_kwargs):
        return None

    def warning(self, message, *args, **_kwargs):
        self.warnings.append((message, args))


class _WebSocket:
    def __init__(self):
        self.sent = []

    async def send(self, payload):
        self.sent.append(payload)


class _Dialogue:
    def __init__(self):
        self.messages = []

    def put(self, message):
        self.messages.append(message)


class _Conn:
    def __init__(self):
        self.config = {
            "enable_wakeup_words_response_cache": True,
            "enable_stop_tts_notify": False,
            "tts_audio_send_delay": 0,
            "wakeup_words": ["hiesp"],
        }
        self.tts = types.SimpleNamespace(voice="default", tts_audio_first_sentence=True)
        self.websocket = _WebSocket()
        self.session_id = "session-1"
        self.sentence_id = "old-sentence"
        self.client_abort = False
        self.client_is_speaking = True
        self.conn_from_mqtt_gateway = False
        self.last_activity_time = 0
        self.logger = _Logger()
        self.dialogue = _Dialogue()
        self.clear_speak_calls = 0

    def clearSpeakStatus(self):
        self.clear_speak_calls += 1
        self.client_is_speaking = False


class WakeupWordsReplyTest(unittest.IsolatedAsyncioTestCase):
    async def test_wakeup_reply_audio_failure_still_sends_tts_stop(self):
        conn = _Conn()

        async def failing_audio_to_data(*_args, **_kwargs):
            raise RuntimeError("cached wake reply audio unavailable")

        original_audio_to_data = helloHandle.audio_to_data
        original_wakeup_words_config = helloHandle.wakeup_words_config
        helloHandle.audio_to_data = failing_audio_to_data
        helloHandle.wakeup_words_config = types.SimpleNamespace(
            get_wakeup_response=lambda _voice: {
                "file_path": "config/assets/wakeup_words_short.wav",
                "text": "I'm here!",
                "time": 0,
            }
        )
        try:
            handled = await helloHandle.checkWakeupWords(conn, "hiesp")
        finally:
            helloHandle.audio_to_data = original_audio_to_data
            helloHandle.wakeup_words_config = original_wakeup_words_config

        self.assertTrue(handled)
        self.assertEqual(
            [json.loads(payload)["state"] for payload in conn.websocket.sent],
            ["stop"],
        )
        self.assertFalse(conn.client_is_speaking)
        self.assertEqual(conn.clear_speak_calls, 1)
        self.assertTrue(conn.logger.warnings)


if __name__ == "__main__":
    unittest.main()
