import json
import unittest

from core.handle.abortHandle import handleAbortMessage


class _DummyLogger:
    def bind(self, **kwargs):
        return self

    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None


class _DummyWebSocket:
    def __init__(self):
        self.sent_messages = []

    async def send(self, payload):
        self.sent_messages.append(payload)


class _DummyVoiceProvider:
    def __init__(self):
        self.interrupt_calls = 0

    async def interrupt(self):
        self.interrupt_calls += 1

class _FailingVoiceProvider:
    async def interrupt(self):
        raise RuntimeError("interrupt failed")


class _DummyConn:
    def __init__(self):
        self.logger = _DummyLogger()
        self.websocket = _DummyWebSocket()
        self.voice_provider = _DummyVoiceProvider()
        self.session_id = "session-1"
        self.client_abort = False
        self.close_after_chat = True
        self.clear_queue_calls = 0
        self.clear_speak_calls = 0

    def clear_queues(self):
        self.clear_queue_calls += 1

    def clearSpeakStatus(self):
        self.clear_speak_calls += 1


class AbortVoiceProviderInterruptTest(unittest.IsolatedAsyncioTestCase):
    async def test_abort_delegates_to_voice_provider_interrupt(self):
        conn = _DummyConn()

        await handleAbortMessage(conn)

        self.assertTrue(conn.client_abort)
        self.assertFalse(conn.close_after_chat)
        self.assertEqual(conn.voice_provider.interrupt_calls, 1)
        self.assertEqual(conn.clear_queue_calls, 1)
        self.assertEqual(conn.clear_speak_calls, 1)
        self.assertEqual(
            json.loads(conn.websocket.sent_messages[0]),
            {"type": "tts", "state": "stop", "session_id": "session-1"},
        )

    async def test_abort_continues_when_voice_provider_interrupt_fails(self):
        conn = _DummyConn()
        conn.voice_provider = _FailingVoiceProvider()

        await handleAbortMessage(conn)

        self.assertTrue(conn.client_abort)
        self.assertEqual(conn.clear_queue_calls, 1)
        self.assertEqual(conn.clear_speak_calls, 1)
        self.assertEqual(len(conn.websocket.sent_messages), 1)

    async def test_abort_without_voice_provider_still_stops_client_speaking(self):
        conn = _DummyConn()
        conn.voice_provider = None

        await handleAbortMessage(conn)

        self.assertTrue(conn.client_abort)
        self.assertEqual(conn.clear_queue_calls, 1)
        self.assertEqual(conn.clear_speak_calls, 1)
        self.assertEqual(len(conn.websocket.sent_messages), 1)
