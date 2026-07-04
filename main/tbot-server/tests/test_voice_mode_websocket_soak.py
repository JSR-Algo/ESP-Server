import importlib
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch


class VoiceModeWebsocketSoakTest(unittest.TestCase):
    def test_hello_message_uses_firmware_audio_params(self):
        soak = importlib.import_module("scripts.voice_mode_websocket_soak")

        hello = soak._hello_message()

        self.assertEqual(hello["type"], "hello")
        self.assertEqual(hello["audio_params"]["format"], "opus")
        self.assertEqual(hello["audio_params"]["sample_rate"], 24000)
        self.assertEqual(hello["audio_params"]["channels"], 1)
        self.assertEqual(hello["audio_params"]["frame_duration"], 60)

    def test_detect_message_carries_text_without_audio(self):
        soak = importlib.import_module("scripts.voice_mode_websocket_soak")

        message = soak._detect_message("xin chao")

        self.assertEqual(message, {"type": "listen", "state": "detect", "text": "xin chao"})

    def test_tts_state_predicate_matches_existing_surface(self):
        soak = importlib.import_module("scripts.voice_mode_websocket_soak")

        self.assertTrue(soak._is_tts_state({"type": "tts", "state": "stop"}, "stop"))
        self.assertFalse(soak._is_tts_state({"type": "tts", "state": "start"}, "stop"))
        self.assertFalse(soak._is_tts_state({"type": "llm", "state": "stop"}, "stop"))

    def test_build_headers_adds_explicit_authorization_token(self):
        soak = importlib.import_module("scripts.voice_mode_websocket_soak")
        args = SimpleNamespace(
            device_id="robot-1",
            client_id="client-1",
            authorization_token="tok-1",
            ota_url="",
            open_timeout_sec=5,
        )

        headers = soak._build_headers(args)

        self.assertEqual(headers["device-id"], "robot-1")
        self.assertEqual(headers["client-id"], "client-1")
        self.assertEqual(headers["authorization"], "Bearer tok-1")
        self.assertEqual(headers["x-tbot-affinity-key"], "robot-1")

    def test_mint_websocket_token_reads_ota_payload(self):
        soak = importlib.import_module("scripts.voice_mode_websocket_soak")
        captured = {}

        class _Response:
            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                return False

            def read(self):
                return json.dumps({"websocket": {"token": "ota-token"}}).encode()

        def _urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["timeout"] = timeout
            captured["headers"] = dict(request.header_items())
            captured["body"] = json.loads(request.data.decode())
            return _Response()

        with patch.object(soak.request, "urlopen", _urlopen):
            token = soak._mint_websocket_token(
                "https://esp.example/tbot/ota/",
                "robot-1",
                "client-1",
                7,
            )

        self.assertEqual(token, "ota-token")
        self.assertEqual(captured["url"], "https://esp.example/tbot/ota/")
        self.assertEqual(captured["timeout"], 7)
        self.assertEqual(captured["headers"]["Device-id"], "robot-1")
        self.assertEqual(captured["headers"]["Client-id"], "client-1")
        self.assertEqual(captured["body"]["mac_address"], "robot-1")
        self.assertEqual(captured["body"]["uuid"], "client-1")


if __name__ == "__main__":
    unittest.main()
