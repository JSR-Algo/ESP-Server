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
        self.google_live_evidence_journey_id = None
        self.google_live_evidence_scope = None
        self.google_live_live_connection_id = None
        self.session_id = "server-connection-1"
        self.device_id = "device-secret-1"
        self.client_id = "client-secret-1"
        self.voice_provider = None
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

    async def test_google_live_hello_activates_only_safe_evidence_journey_label(self):
        conn = _Conn()
        conn.config["voice_mode"] = {"type": "google_live"}
        conn.voice_provider = SimpleNamespace(
            prepare_evidence_scope=AsyncMock(return_value="live-7")
        )

        with patch.object(
            helloHandle,
            "_utc_now_iso",
            return_value="2026-08-31T03:00:00+00:00",
        ):
            await handleHelloMessage(
                conn, {"evidence_journey_id": "bargein.run-1:test"}
            )

        self.assertEqual(conn.google_live_evidence_journey_id, "bargein.run-1:test")
        ack = json.loads(conn.websocket.sent[0])
        scope = ack["evidenceScope"]
        self.assertEqual(scope["journeyId"], "bargein.run-1:test")
        self.assertEqual(scope["connectionId"], "server-connection-1")
        self.assertEqual(scope["liveConnectionId"], "live-7")
        self.assertEqual(scope["serverStartUtc"], "2026-08-31T03:00:00+00:00")
        self.assertRegex(scope["peerIdentityHash"], r"^sha256:[0-9a-f]{64}$")
        encoded = json.dumps(ack)
        self.assertNotIn("device-secret-1", encoded)
        self.assertNotIn("client-secret-1", encoded)
        self.assertEqual(conn.google_live_evidence_scope, scope)

    async def test_google_live_evidence_scope_fails_closed_without_live_identity(self):
        conn = _Conn()
        conn.config["voice_mode"] = {"type": "google_live"}
        conn.voice_provider = SimpleNamespace(
            prepare_evidence_scope=AsyncMock(return_value=None)
        )

        await handleHelloMessage(conn, {"evidence_journey_id": "bargein-1"})

        ack = json.loads(conn.websocket.sent[0])
        self.assertEqual(ack["evidenceScope"]["status"], "FAIL")
        self.assertEqual(ack["evidenceScope"]["failureCode"], "LIVE_SCOPE_UNAVAILABLE")
        self.assertIsNone(conn.google_live_evidence_scope)

    async def test_google_live_hello_rejects_unsafe_evidence_journey_without_logging_value(self):
        conn = _Conn()
        conn.config["voice_mode"] = {"type": "google_live"}
        unsafe = "secret journey value"

        await handleHelloMessage(conn, {"evidence_journey_id": unsafe})

        self.assertIsNone(conn.google_live_evidence_journey_id)
        self.assertNotIn(unsafe, " ".join(conn.logger.debugs + conn.logger.infos))

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
