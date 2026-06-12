import json
import unittest

from core.handle.helloHandle import handleHelloMessage


class _Logger:
    def bind(self, **_kwargs):
        return self

    def debug(self, *_args, **_kwargs):
        return None

    def info(self, *_args, **_kwargs):
        return None


class _WebSocket:
    def __init__(self):
        self.sent = []

    async def send(self, payload):
        self.sent.append(payload)


class _Conn:
    def __init__(self):
        self.logger = _Logger()
        self.websocket = _WebSocket()
        self.audio_format = "opus"
        self.sample_rate = 24000
        self.welcome_msg = {
            "type": "hello",
            "version": 1,
            "transport": "websocket",
            "session_id": "session-1",
            "audio_params": {
                "format": "opus",
                "sample_rate": 24000,
                "channels": 1,
                "frame_duration": 60,
            },
        }


class HelloAudioParamsTest(unittest.IsolatedAsyncioTestCase):
    async def test_client_audio_params_update_connection_sample_rate(self):
        conn = _Conn()

        await handleHelloMessage(
            conn,
            {
                "type": "hello",
                "audio_params": {
                    "format": "opus",
                    "sample_rate": 16000,
                    "channels": 1,
                    "frame_duration": 60,
                },
            },
        )

        self.assertEqual(conn.sample_rate, 16000)
        server_hello = json.loads(conn.websocket.sent[0])
        self.assertEqual(server_hello["audio_params"]["sample_rate"], 16000)


if __name__ == "__main__":
    unittest.main()
