import unittest
import json
from types import SimpleNamespace

from core.connection import ConnectionHandler
from core.handle.textHandler.lessonMessageHandler import TtsAckHandler
from core.handle.textMessageHandlerRegistry import TextMessageHandlerRegistry
from core.handle.textMessageType import TextMessageType


class LessonAudioDrainAckTest(unittest.IsolatedAsyncioTestCase):
    async def test_registry_routes_tts_ack_to_voice_provider(self):
        calls = []
        conn = SimpleNamespace(
            voice_provider=SimpleNamespace(
                accept_lesson_audio_drain_ack=lambda message: calls.append(message) or True
            )
        )
        message = {"type": "tts_ack", "state": "stop", "drainId": "drain-1"}

        await TtsAckHandler().handle(conn, message)

        self.assertEqual(calls, [message])
        self.assertEqual(TtsAckHandler().message_type, TextMessageType.TTS_ACK)
        self.assertIsInstance(
            TextMessageHandlerRegistry().get_handler("tts_ack"), TtsAckHandler
        )

    async def test_handler_safely_ignores_missing_provider(self):
        await TtsAckHandler().handle(SimpleNamespace(), {"type": "tts_ack"})

    def test_connection_routes_tts_ack_as_control_message(self):
        self.assertTrue(
            ConnectionHandler._is_lesson_control_message(
                None,
                json.dumps(
                    {"type": "tts_ack", "state": "stop", "drainId": "drain-1"}
                ),
            )
        )
