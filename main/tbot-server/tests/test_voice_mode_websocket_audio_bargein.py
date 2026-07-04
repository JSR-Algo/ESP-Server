import asyncio
import importlib
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch


class VoiceModeWebsocketAudioBargeinTest(unittest.TestCase):
    def test_opus_packets_from_audio_file_uses_converter(self):
        audio_bargein = importlib.import_module("scripts.voice_mode_websocket_audio_bargein")
        captured = {}

        class _Encoder:
            def __init__(self, sample_rate, channels, frame_duration_ms):
                captured["encoder"] = (sample_rate, channels, frame_duration_ms)

            def close(self):
                captured["closed"] = True

        def _convert(path, is_opus, callback, sample_rate, opus_encoder):
            captured["convert"] = (path, is_opus, sample_rate, isinstance(opus_encoder, _Encoder))
            callback(b"opus-from-file")

        with patch.object(audio_bargein, "OpusEncoderUtils", _Encoder), patch.object(
            audio_bargein, "audio_to_data_stream", _convert, create=True
        ):
            packets = audio_bargein._opus_packets_from_audio_file("stop.wav", 24000, 60)

        self.assertEqual(packets, [b"opus-from-file"])
        self.assertEqual(captured["encoder"], (24000, 1, 60))
        self.assertEqual(captured["convert"], ("stop.wav", True, 24000, True))
        self.assertTrue(captured["closed"])

    def test_run_smoke_uses_production_auth_headers(self):
        audio_bargein = importlib.import_module("scripts.voice_mode_websocket_audio_bargein")
        captured = {}

        class _WebSocket:
            def __init__(self):
                self.messages = [
                    json.dumps({"type": "hello"}),
                    json.dumps({"type": "tts", "state": "start"}),
                    json.dumps({"type": "tts", "state": "stop"}),
                ]

            async def send(self, _payload):
                return None

            async def recv(self):
                return self.messages.pop(0)

            async def close(self):
                return None

        class _Connect:
            def __init__(self, **kwargs):
                captured.update(kwargs)
                self.websocket = _WebSocket()

            async def __aenter__(self):
                return self.websocket

            async def __aexit__(self, *_exc):
                return False

        def _connect(_url, **kwargs):
            return _Connect(**kwargs)

        async def _sleep(_seconds):
            return None

        args = SimpleNamespace(
            websocket_url="wss://esp.example/tbot/v1/",
            device_id="robot-1",
            client_id="client-1",
            authorization_token="tok-1",
            ota_url="",
            sample_rate=24000,
            frame_duration_ms=60,
            audio_duration_sec=0.6,
            rms=9000,
            text="xin chao",
            interrupt_delay_sec=0.3,
            open_timeout_sec=5,
            event_timeout_sec=20,
            interrupt_timeout_sec=5,
        )

        with patch.object(audio_bargein.websockets, "connect", _connect), patch.object(
            audio_bargein, "_opus_packets", return_value=[b"opus"]
        ), patch.object(audio_bargein.asyncio, "sleep", _sleep):
            summary = asyncio.run(audio_bargein.run_smoke(args))

        self.assertEqual(summary["tts_starts"], 1)
        self.assertEqual(captured["additional_headers"]["device-id"], "robot-1")
        self.assertEqual(captured["additional_headers"]["client-id"], "client-1")
        self.assertEqual(captured["additional_headers"]["authorization"], "Bearer tok-1")
        self.assertEqual(captured["additional_headers"]["x-tbot-affinity-key"], "robot-1")


if __name__ == "__main__":
    unittest.main()
