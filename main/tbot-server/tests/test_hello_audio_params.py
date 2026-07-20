import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.handle import helloHandle
from core.handle.helloHandle import handleHelloMessage
from core.voice.google_live.audio_bridge import GoogleLiveAudioBridge


class _Logger:
    def __init__(self):
        self.debugs = []
        self.infos = []

    def bind(self, **_kwargs):
        return self

    def debug(self, message, *_args, **_kwargs):
        self.debugs.append(message)

    def info(self, message, *_args, **_kwargs):
        self.infos.append(message)


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
        self.input_sample_rate = None
        self.sample_rate = 24000
        self.features = None
        self.mcp_client = None
        self.mcp_scheduled = []
        self.mcp_sent_counts_at_schedule = []
        self.config = {
            "voice_mode": {"type": "classic_pipeline"},
            "google_live": {"output_sample_rate": 24000},
        }
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

    def schedule_mcp_background_task(self, coro):
        self.mcp_scheduled.append(coro)
        self.mcp_sent_counts_at_schedule.append(len(self.websocket.sent))
        coro.close()
        return SimpleNamespace(done=lambda: True)


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
        self.assertEqual(conn.input_sample_rate, 16000)
        server_hello = json.loads(conn.websocket.sent[0])
        self.assertEqual(server_hello["audio_params"]["sample_rate"], 16000)

    async def test_google_live_uses_client_sample_rate_for_input_and_configured_output_rate(self):
        conn = _Conn()
        conn.config["voice_mode"] = {"type": "google_live"}
        conn.config["google_live"] = {"output_sample_rate": 24000}

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

        self.assertEqual(conn.input_sample_rate, 16000)
        self.assertEqual(conn.sample_rate, 24000)
        server_hello = json.loads(conn.websocket.sent[0])
        self.assertEqual(server_hello["audio_params"]["sample_rate"], 24000)

    async def test_features_with_mcp_initializes_client_and_schedules_initialize_message(self):
        conn = _Conn()

        with patch.object(helloHandle, "MCPClient", return_value="mcp-client"), patch.object(
            helloHandle, "send_mcp_initialize_message", new=AsyncMock()
        ):
            await handleHelloMessage(conn, {"features": {"mcp": True, "vision": True}})

        self.assertEqual(conn.features, {"mcp": True, "vision": True})
        self.assertEqual(conn.mcp_client, "mcp-client")
        self.assertEqual(len(conn.mcp_scheduled), 1)
        self.assertEqual(conn.mcp_sent_counts_at_schedule, [1])
        self.assertEqual(json.loads(conn.websocket.sent[0])["type"], "hello")

    async def test_empty_hello_still_sends_server_welcome_without_overwriting_state(self):
        conn = _Conn()

        await handleHelloMessage(conn, {})

        self.assertEqual(conn.audio_format, "opus")
        self.assertEqual(conn.sample_rate, 24000)
        self.assertIsNone(conn.features)
        self.assertEqual(json.loads(conn.websocket.sent[0])["session_id"], "session-1")

class GoogleLiveAudioBridgeSampleRateTest(unittest.TestCase):
    def test_input_frame_size_uses_client_input_sample_rate_not_output_rate(self):
        conn = _Conn()
        conn.input_sample_rate = 16000
        conn.sample_rate = 24000
        client = SimpleNamespace(config={"input_sample_rate": 16000})

        bridge = GoogleLiveAudioBridge(conn, client, conn.logger)

        self.assertEqual(bridge._get_input_frame_size(), 960)


if __name__ == "__main__":
    unittest.main()
