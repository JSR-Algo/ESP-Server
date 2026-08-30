import asyncio
import importlib
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch


class VoiceModeWebsocketAudioBargeinTest(unittest.TestCase):
    @staticmethod
    def _args(**overrides):
        values = {
            "websocket_url": "wss://esp.example/tbot/v1/",
            "device_id": "robot-1",
            "client_id": "client-1",
            "authorization_token": "tok-1",
            "ota_url": "",
            "sample_rate": 24000,
            "frame_duration_ms": 60,
            "audio_duration_sec": 0.6,
            "audio_file": "",
            "rms": 9000,
            "text": "xin chao",
            "interrupt_delay_sec": 0.3,
            "open_timeout_sec": 5,
            "event_timeout_sec": 20,
            "interrupt_timeout_sec": 5,
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    @staticmethod
    def _connect_for(messages, captured=None):
        class _WebSocket:
            def __init__(self):
                self.messages = list(messages)

            async def send(self, _payload):
                return None

            async def recv(self):
                if not self.messages:
                    raise asyncio.TimeoutError
                return self.messages.pop(0)

            async def close(self):
                return None

        class _Connect:
            def __init__(self, **kwargs):
                if captured is not None:
                    captured.update(kwargs)
                self.websocket = _WebSocket()

            async def __aenter__(self):
                return self.websocket

            async def __aexit__(self, *_exc):
                return False

        return lambda _url, **kwargs: _Connect(**kwargs)

    def test_run_smoke_collects_terminal_replacement_audio_journey(self):
        audio_bargein = importlib.import_module("scripts.voice_mode_websocket_audio_bargein")

        class _Clock:
            def __init__(self):
                self.value = 0.0

            def __call__(self):
                self.value += 0.03
                return self.value

        messages = [
            json.dumps({"type": "hello"}),
            b"old-response-audio",
            json.dumps({"type": "tts", "state": "start"}),
            json.dumps({"type": "tts", "state": "stop", "reason": "interrupt"}),
            json.dumps({"type": "tts", "state": "start"}),
            b"replacement-audio-1",
            b"replacement-audio-2",
            json.dumps({"type": "tts", "state": "stop"}),
        ]

        async def _sleep(_seconds):
            return None

        with patch.object(
            audio_bargein.websockets,
            "connect",
            self._connect_for(messages),
        ), patch.object(
            audio_bargein, "_opus_packets", return_value=[b"interrupt-opus"]
        ), patch.object(audio_bargein.asyncio, "sleep", _sleep):
            record = asyncio.run(audio_bargein.run_smoke(self._args(), clock=_Clock()))

        self.assertEqual(record["status"], "PASS")
        self.assertTrue(record["oldResponseStopped"])
        self.assertTrue(record["replacementResponseStarted"])
        self.assertEqual(record["replacementBinaryChunks"], 2)
        self.assertEqual(record["maxServerOutputGapMs"], 60.0)
        self.assertLessEqual(record["bargeinStopMs"], 500.0)
        self.assertEqual(record["correlationSource"], "server_log")

    def test_run_smoke_fails_closed_when_replacement_response_is_incomplete(self):
        audio_bargein = importlib.import_module("scripts.voice_mode_websocket_audio_bargein")
        messages = [
            json.dumps({"type": "hello"}),
            json.dumps({"type": "tts", "state": "start"}),
            json.dumps({"type": "tts", "state": "stop", "reason": "interrupt"}),
            json.dumps({"type": "tts", "state": "start"}),
        ]

        async def _sleep(_seconds):
            return None

        with patch.object(
            audio_bargein.websockets,
            "connect",
            self._connect_for(messages),
        ), patch.object(
            audio_bargein, "_opus_packets", return_value=[b"interrupt-opus"]
        ), patch.object(audio_bargein.asyncio, "sleep", _sleep):
            record = asyncio.run(audio_bargein.run_smoke(self._args()))

        self.assertEqual(record["status"], "FAIL")
        self.assertEqual(record["failureCode"], "REPLACEMENT_RESPONSE_INCOMPLETE")
        self.assertTrue(record["replacementResponseStarted"])
        self.assertEqual(record["replacementBinaryChunks"], 0)
        self.assertNotIn("tok-1", json.dumps(record))

    def test_run_smoke_fails_closed_when_replacement_audio_gap_exceeds_budget(self):
        audio_bargein = importlib.import_module("scripts.voice_mode_websocket_audio_bargein")

        class _Clock:
            def __init__(self):
                self.values = iter(
                    [
                        0.0,
                        0.01,
                        0.02,
                        0.03,
                        0.04,
                        0.05,
                        0.06,
                        0.07,
                        0.40,
                        0.41,
                        0.42,
                    ]
                )

            def __call__(self):
                return next(self.values)

        messages = [
            json.dumps({"type": "hello"}),
            json.dumps({"type": "tts", "state": "start"}),
            json.dumps({"type": "tts", "state": "stop", "reason": "interrupt"}),
            json.dumps({"type": "tts", "state": "start"}),
            b"replacement-audio-1",
            b"replacement-audio-2",
            json.dumps({"type": "tts", "state": "stop"}),
        ]

        async def _sleep(_seconds):
            return None

        with patch.object(
            audio_bargein.websockets,
            "connect",
            self._connect_for(messages),
        ), patch.object(
            audio_bargein, "_opus_packets", return_value=[b"interrupt-opus"]
        ), patch.object(audio_bargein.asyncio, "sleep", _sleep):
            record = asyncio.run(audio_bargein.run_smoke(self._args(), clock=_Clock()))

        self.assertEqual(record["status"], "FAIL")
        self.assertEqual(record["failureCode"], "SERVER_OUTPUT_GAP_EXCEEDED")
        self.assertGreater(record["maxServerOutputGapMs"], 250.0)

    def test_run_smoke_fails_closed_when_bargein_stop_exceeds_budget(self):
        audio_bargein = importlib.import_module("scripts.voice_mode_websocket_audio_bargein")

        class _Clock:
            def __init__(self):
                self.values = iter([0.0, 0.6, 0.7, 0.71, 0.72, 0.73, 0.74, 0.75, 0.76])

            def __call__(self):
                return next(self.values)

        messages = [
            json.dumps({"type": "hello"}),
            json.dumps({"type": "tts", "state": "start"}),
            json.dumps({"type": "tts", "state": "stop", "reason": "interrupt"}),
            json.dumps({"type": "tts", "state": "start"}),
            b"replacement-audio",
            json.dumps({"type": "tts", "state": "stop"}),
        ]

        async def _sleep(_seconds):
            return None

        with patch.object(
            audio_bargein.websockets,
            "connect",
            self._connect_for(messages),
        ), patch.object(
            audio_bargein, "_opus_packets", return_value=[b"interrupt-opus"]
        ), patch.object(audio_bargein.asyncio, "sleep", _sleep):
            record = asyncio.run(audio_bargein.run_smoke(self._args(), clock=_Clock()))

        self.assertEqual(record["status"], "FAIL")
        self.assertEqual(record["failureCode"], "BARGEIN_STOP_LATENCY_EXCEEDED")
        self.assertEqual(record["bargeinStopMs"], 600.0)

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
                    json.dumps({"type": "tts", "state": "start"}),
                    b"replacement-audio",
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

        args = self._args()

        with patch.object(audio_bargein.websockets, "connect", _connect), patch.object(
            audio_bargein, "_opus_packets", return_value=[b"opus"]
        ), patch.object(audio_bargein.asyncio, "sleep", _sleep):
            summary = asyncio.run(audio_bargein.run_smoke(args))

        self.assertEqual(summary["tts_starts"], 2)
        self.assertEqual(summary["tts_stops"], 2)
        self.assertEqual(summary["status"], "PASS")
        self.assertEqual(captured["additional_headers"]["device-id"], "robot-1")
        self.assertEqual(captured["additional_headers"]["client-id"], "client-1")
        self.assertEqual(captured["additional_headers"]["authorization"], "Bearer tok-1")
        self.assertEqual(captured["additional_headers"]["x-tbot-affinity-key"], "robot-1")

    def test_run_smoke_uses_audio_file_when_provided(self):
        audio_bargein = importlib.import_module("scripts.voice_mode_websocket_audio_bargein")
        sent_binary = []

        class _WebSocket:
            def __init__(self):
                self.messages = [
                    json.dumps({"type": "hello"}),
                    json.dumps({"type": "tts", "state": "start"}),
                    json.dumps({"type": "tts", "state": "stop"}),
                    json.dumps({"type": "tts", "state": "start"}),
                    b"replacement-audio",
                    json.dumps({"type": "tts", "state": "stop"}),
                ]

            async def send(self, payload):
                if isinstance(payload, bytes):
                    sent_binary.append(payload)

            async def recv(self):
                return self.messages.pop(0)

            async def close(self):
                return None

        class _Connect:
            def __init__(self, **_kwargs):
                self.websocket = _WebSocket()

            async def __aenter__(self):
                return self.websocket

            async def __aexit__(self, *_exc):
                return False

        def _connect(_url, **kwargs):
            return _Connect(**kwargs)

        async def _sleep(_seconds):
            return None

        args = self._args(audio_file="stop.wav")

        with patch.object(audio_bargein.websockets, "connect", _connect), patch.object(
            audio_bargein, "_opus_packets", side_effect=AssertionError("synthetic tone used")
        ), patch.object(
            audio_bargein, "_opus_packets_from_audio_file", return_value=[b"file-opus"], create=True
        ), patch.object(audio_bargein.asyncio, "sleep", _sleep):
            summary = asyncio.run(audio_bargein.run_smoke(args))

        self.assertEqual(summary["opus_packets"], 1)
        self.assertEqual(sent_binary, [b"file-opus"])


if __name__ == "__main__":
    unittest.main()
