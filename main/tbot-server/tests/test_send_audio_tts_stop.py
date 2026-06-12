import json
import unittest

from core.handle import sendAudioHandle


class _WebSocket:
    def __init__(self):
        self.sent = []

    async def send(self, payload):
        self.sent.append(payload)


class _Logger:
    def bind(self, **_kwargs):
        return self

    def debug(self, *_args, **_kwargs):
        return None

    def info(self, *_args, **_kwargs):
        return None


class _Conn:
    def __init__(self):
        self.config = {"enable_stop_tts_notify": False}
        self.tts = type("Tts", (), {"tts_audio_first_sentence": False})()
        self.session_id = "session-1"
        self.sentence_id = "old-sentence"
        self.client_abort = False
        self.client_is_speaking = True
        self.conn_from_mqtt_gateway = False
        self.websocket = _WebSocket()
        self.logger = _Logger()
        self.clear_speak_calls = 0

    def clearSpeakStatus(self):
        self.clear_speak_calls += 1
        self.client_is_speaking = False


class SendTtsStopTest(unittest.IsolatedAsyncioTestCase):
    async def test_stale_stop_still_releases_client_when_no_new_audio_started(self):
        conn = _Conn()

        async def mutate_sentence_id(_conn):
            _conn.sentence_id = "new-sentence"

        original_wait = sendAudioHandle._wait_for_audio_completion
        sendAudioHandle._wait_for_audio_completion = mutate_sentence_id
        try:
            await sendAudioHandle.send_tts_message(conn, "stop")
        finally:
            sendAudioHandle._wait_for_audio_completion = original_wait

        self.assertEqual(conn.clear_speak_calls, 1)
        self.assertFalse(conn.client_is_speaking)
        self.assertEqual(
            [json.loads(payload) for payload in conn.websocket.sent],
            [{"type": "tts", "state": "stop", "session_id": "session-1"}],
        )

    async def test_stt_message_does_not_put_device_into_speaking_before_audio(self):
        conn = _Conn()
        conn.client_is_speaking = False

        await sendAudioHandle.send_stt_message(conn, "Hi ESP")

        self.assertFalse(conn.client_is_speaking)
        self.assertEqual(
            [json.loads(payload) for payload in conn.websocket.sent],
            [{"type": "stt", "text": "Hi ESP", "session_id": "session-1"}],
        )

    async def test_first_audio_message_sends_tts_start_before_audio(self):
        conn = _Conn()
        conn.client_is_speaking = False

        await sendAudioHandle.sendAudioMessage(
            conn,
            sendAudioHandle.SentenceType.FIRST,
            [b"opus-frame"],
            "Hello",
        )

        self.assertTrue(conn.client_is_speaking)
        self.assertEqual(json.loads(conn.websocket.sent[0])["state"], "start")
        self.assertEqual(json.loads(conn.websocket.sent[1])["state"], "sentence_start")
        self.assertEqual(conn.websocket.sent[2], b"opus-frame")


if __name__ == "__main__":
    unittest.main()
