import asyncio
import importlib
import json
import unittest
import wave
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


class _Clock:
    def __init__(self, values):
        self._values = iter(values)

    def __call__(self):
        return next(self._values)


class _FakeClient:
    def __init__(self, events=(), *, error=None):
        self.events = list(events)
        self.error = error
        self.connect_calls = 0
        self.sent_audio = []
        self.end_calls = 0
        self.close_calls = 0

    async def connect(self):
        self.connect_calls += 1
        if self.error is not None:
            raise self.error

    async def send_audio(self, chunk):
        self.sent_audio.append(chunk)

    async def end_audio_stream(self):
        self.end_calls += 1

    async def receive_events(self):
        for event in self.events:
            yield event

    async def close(self):
        self.close_calls += 1


class _TerminalThenBlockingClient(_FakeClient):
    def __init__(self):
        super().__init__()
        self.receive_closed = False

    async def receive_events(self):
        try:
            yield {"type": "audio_chunk", "audio": b"audio"}
            yield {"type": "audio_end"}
            await asyncio.Event().wait()
        finally:
            self.receive_closed = True


class GoogleLiveSmokeScriptTest(unittest.TestCase):
    def test_build_env_config_uses_secret_placeholder(self):
        smoke = importlib.import_module("scripts.google_live_smoke")

        config = smoke._build_env_config("gemini-live", "Aoede")

        self.assertEqual(config["api_key"], "${GOOGLE_API_KEY}")
        self.assertEqual(config["model"], "gemini-live")
        self.assertEqual(config["voice_name"], "Aoede")
        self.assertTrue(config["native_voice"])
        self.assertTrue(config["enable_audio_input"])
        self.assertTrue(config["enable_audio_output"])

    def test_build_env_config_allows_no_native_voice_name(self):
        smoke = importlib.import_module("scripts.google_live_smoke")

        config = smoke._build_env_config("gemini-live", "")

        self.assertFalse(config["native_voice"])
        self.assertEqual(config["voice_name"], "")

    def test_main_defaults_to_production_voice(self):
        smoke = importlib.import_module("scripts.google_live_smoke")
        captured = {}

        async def fake_run_smoke(config):
            captured.update(config)

        with patch.dict("os.environ", {"GOOGLE_API_KEY": "key"}, clear=True), patch(
            "sys.argv", ["google_live_smoke.py"]
        ), patch.object(smoke, "_run_smoke", fake_run_smoke):
            self.assertEqual(smoke.main(), 0)

        self.assertEqual(captured["voice_name"], "Kore")
        self.assertEqual(captured["language_code"], "vi-VN")
        self.assertTrue(captured["native_voice"])

    def test_has_resolvable_api_key_rejects_missing_placeholder_env(self):
        smoke = importlib.import_module("scripts.google_live_smoke")

        with patch.dict("os.environ", {}, clear=True):
            self.assertFalse(
                smoke._has_resolvable_api_key({"api_key": "${GOOGLE_API_KEY}"})
            )

    def test_has_resolvable_api_key_accepts_placeholder_env(self):
        smoke = importlib.import_module("scripts.google_live_smoke")

        with patch.dict("os.environ", {"GOOGLE_API_KEY": " key "}, clear=True):
            self.assertTrue(
                smoke._has_resolvable_api_key({"api_key": "${GOOGLE_API_KEY}"})
            )

    def test_has_resolvable_api_key_accepts_literal_manager_key(self):
        smoke = importlib.import_module("scripts.google_live_smoke")

        self.assertTrue(smoke._has_resolvable_api_key({"api_key": "literal-key"}))

    def test_manager_config_rejects_malformed_voice_mode_cleanly(self):
        smoke = importlib.import_module("scripts.google_live_smoke")
        closed = []

        async def fake_load_config_async():
            return {}

        async def fake_private_config(_config, _device_id, _client_id):
            return {"google_live": {"api_key": "literal-key"}, "voice_mode": "bad"}

        class FakeManageApiClient:
            def __init__(self, _config):
                pass

            @staticmethod
            def safe_close():
                closed.append(True)

        with patch(
            "config.config_loader.load_config_async", new=fake_load_config_async
        ), patch(
            "config.config_loader.get_private_config_from_api",
            new=fake_private_config,
        ), patch(
            "config.manage_api_client.ManageApiClient",
            new=FakeManageApiClient,
        ), self.assertRaisesRegex(
            RuntimeError, "manager private config voice_mode is not google_live"
        ):
            asyncio.run(smoke._load_manager_google_live_config("device", "client"))

        self.assertEqual(closed, [True])

    def test_main_round_trip_writes_v1_report_and_returns_failure(self):
        smoke = importlib.import_module("scripts.google_live_smoke")

        async def fake_round_trip(_config, _audio_file, _event_timeout_sec):
            return {
                "status": "FAIL",
                "error": {
                    "class": "credential_or_auth",
                    "message": "safe",
                    "apiKey": "must-not-persist",
                },
            }

        with TemporaryDirectory() as directory:
            report_path = Path(directory) / "report.json"
            with patch.dict("os.environ", {"GOOGLE_API_KEY": "key"}, clear=True), patch(
                "sys.argv",
                [
                    "google_live_smoke.py",
                    "--round-trip",
                    "--report",
                    str(report_path),
                ],
            ), patch.object(smoke, "_run_round_trip", fake_round_trip):
                self.assertEqual(smoke.main(), 1)

            report = json.loads(report_path.read_text())

        self.assertEqual(report["schemaVersion"], "google-live-reliability.v1")
        self.assertEqual(report["name"], "real_api")
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["error"]["apiKey"], "<redacted>")

    def test_read_pcm_chunks_requires_nonempty_mono_pcm16_wav(self):
        smoke = importlib.import_module("scripts.google_live_smoke")

        with TemporaryDirectory() as directory:
            fixture = Path(directory) / "speech.wav"
            with wave.open(str(fixture), "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(16000)
                wav_file.writeframes(b"\x01\x00" * 640)

            chunks = smoke._read_pcm_chunks(fixture, chunk_ms=20)

        self.assertEqual([len(chunk) for chunk in chunks], [640, 640])

    def test_build_round_trip_config_preserves_production_voice_configuration(self):
        smoke = importlib.import_module("scripts.google_live_smoke")
        base = smoke._build_env_config(
            "gemini-3.1-flash-live-preview",
            "Kore",
        )

        config = smoke._build_round_trip_config(base, smoke.DEFAULT_AUDIO_FIXTURE)

        self.assertEqual(config["model"], "gemini-3.1-flash-live-preview")
        self.assertTrue(config["native_voice"])
        self.assertEqual(config["voice_name"], "Kore")
        self.assertEqual(config["language_code"], "vi-VN")
        self.assertEqual(config["input_sample_rate"], 24000)

    def test_read_pcm_chunks_rejects_empty_and_wrong_format(self):
        smoke = importlib.import_module("scripts.google_live_smoke")

        with TemporaryDirectory() as directory:
            empty = Path(directory) / "empty.wav"
            with wave.open(str(empty), "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(16000)
                wav_file.writeframes(b"")
            with self.assertRaisesRegex(ValueError, "audio fixture is empty"):
                smoke._read_pcm_chunks(empty, chunk_ms=20)

            stereo = Path(directory) / "stereo.wav"
            with wave.open(str(stereo), "wb") as wav_file:
                wav_file.setnchannels(2)
                wav_file.setsampwidth(2)
                wav_file.setframerate(16000)
                wav_file.writeframes(b"\x00\x00" * 2)
            with self.assertRaisesRegex(ValueError, "mono 16-bit PCM"):
                smoke._read_pcm_chunks(stereo, chunk_ms=20)


class GoogleLiveSmokeRoundTripTest(unittest.IsolatedAsyncioTestCase):
    async def test_run_round_trip_passes_production_identity_to_client(self):
        smoke = importlib.import_module("scripts.google_live_smoke")
        captured = {}

        def fake_client_factory(config, _logger):
            captured.update(config)
            return _FakeClient(
                events=[
                    {"type": "audio_chunk", "audio": b"audio"},
                    {"type": "audio_end"},
                ]
            )

        config = smoke._build_env_config(
            "gemini-3.1-flash-live-preview",
            "Kore",
        )
        with patch.object(smoke, "GoogleLiveClient", fake_client_factory):
            report = await smoke._run_round_trip(
                config,
                smoke.DEFAULT_AUDIO_FIXTURE,
                1,
            )

        self.assertEqual(report["status"], "PASS")
        self.assertEqual(captured["model"], "gemini-3.1-flash-live-preview")
        self.assertTrue(captured["native_voice"])
        self.assertEqual(captured["voice_name"], "Kore")
        self.assertEqual(captured["language_code"], "vi-VN")
        self.assertEqual(captured["input_sample_rate"], 24000)

    async def test_run_audio_round_trip_collects_terminal_audio_and_closes(self):
        smoke = importlib.import_module("scripts.google_live_smoke")
        client = _FakeClient(
            events=[
                {"type": "transcript", "source": "user", "text": "xin chao"},
                {"type": "audio_start"},
                {
                    "type": "audio_chunk",
                    "audio": b"pcm-secret",
                    "mime_type": "audio/pcm;rate=24000",
                },
                {"type": "audio_end"},
            ]
        )

        result = await smoke._run_audio_round_trip(
            client,
            pcm_chunks=[b"\x00\x00" * 320],
            event_timeout_sec=2,
            clock=_Clock([0.0, 0.1, 0.2, 0.3, 0.4, 0.5]),
        )

        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["audioChunks"], 1)
        self.assertEqual(result["connectionMs"], 100.0)
        self.assertEqual(result["firstServerEventMs"], 200.0)
        self.assertEqual(result["firstAudioMs"], 400.0)
        self.assertEqual(client.end_calls, 1)
        self.assertEqual(client.close_calls, 1)
        self.assertNotIn("pcm-secret", json.dumps(result))

    async def test_run_audio_round_trip_fails_closed_without_terminal_audio(self):
        smoke = importlib.import_module("scripts.google_live_smoke")
        client = _FakeClient(events=[{"type": "audio_start"}])

        with self.assertRaisesRegex(RuntimeError, "without terminal audio"):
            await smoke._run_audio_round_trip(
                client,
                pcm_chunks=[b"\x00\x00"],
                event_timeout_sec=2,
            )

        self.assertEqual(client.close_calls, 1)

    async def test_run_audio_round_trip_closes_receive_generator_at_terminal(self):
        smoke = importlib.import_module("scripts.google_live_smoke")
        client = _TerminalThenBlockingClient()

        result = await smoke._run_audio_round_trip(
            client,
            pcm_chunks=[b"\x00\x00"],
            event_timeout_sec=1,
        )

        self.assertEqual(result["status"], "PASS")
        self.assertTrue(client.receive_closed)
        self.assertEqual(client.close_calls, 1)

    async def test_run_audio_round_trip_rejects_empty_input_and_closes(self):
        smoke = importlib.import_module("scripts.google_live_smoke")
        client = _FakeClient()

        with self.assertRaisesRegex(ValueError, "PCM audio is empty"):
            await smoke._run_audio_round_trip(
                client, pcm_chunks=[], event_timeout_sec=2
            )

        self.assertEqual(client.connect_calls, 0)
        self.assertEqual(client.close_calls, 1)

    async def test_failure_reports_are_classified_and_never_echo_exception_text(self):
        smoke = importlib.import_module("scripts.google_live_smoke")
        cases = (
            (RuntimeError("401 UNAUTHENTICATED api_key=AIza-secret"), "credential_or_auth"),
            (RuntimeError("429 RESOURCE_EXHAUSTED token=secret"), "quota_or_rate_limit"),
            (RuntimeError("404 model gemini-secret not found"), "model_or_config"),
            (ConnectionError("Authorization: Bearer secret"), "network_or_transport"),
            (RuntimeError("503 service unavailable x-goog-api-key: secret"), "google_service_unavailable"),
            (TimeoutError("session_resumption_handle=secret"), "acceptance_timeout"),
        )

        for error, expected_class in cases:
            with self.subTest(expected_class=expected_class):
                report = await smoke._run_round_trip_with_retry(
                    lambda error=error: _FakeClient(error=error),
                    pcm_chunks=[b"\x00\x00"],
                    event_timeout_sec=1,
                )
                encoded = json.dumps(report)
                self.assertEqual(report["status"], "FAIL")
                self.assertEqual(report["error"]["class"], expected_class)
                self.assertNotIn("secret", encoded)
                self.assertNotIn("api_key", encoded.lower())
                self.assertEqual(
                    report["attempts"],
                    2
                    if expected_class
                    in {"network_or_transport", "google_service_unavailable"}
                    else 1,
                )

    async def test_retry_succeeds_once_after_transient_failure(self):
        smoke = importlib.import_module("scripts.google_live_smoke")
        clients = iter(
            [
                _FakeClient(error=ConnectionError("network down")),
                _FakeClient(
                    events=[
                        {"type": "audio_chunk", "audio": b"audio"},
                        {"type": "audio_end"},
                    ]
                ),
            ]
        )

        report = await smoke._run_round_trip_with_retry(
            lambda: next(clients),
            pcm_chunks=[b"\x00\x00"],
            event_timeout_sec=1,
        )

        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["attempts"], 2)

    async def test_receive_timeout_is_not_counted_as_a_server_response(self):
        smoke = importlib.import_module("scripts.google_live_smoke")
        client = _FakeClient(events=[{"type": "receive_timeout"}])

        report = await smoke._run_round_trip_with_retry(
            lambda: client,
            pcm_chunks=[b"\x00\x00"],
            event_timeout_sec=1,
        )

        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["attempts"], 1)
        self.assertEqual(report["error"]["class"], "acceptance_timeout")
        self.assertEqual(client.close_calls, 1)


if __name__ == "__main__":
    unittest.main()
