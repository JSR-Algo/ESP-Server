import asyncio
import hashlib
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


class _HeartbeatThenBlockingClient(_FakeClient):
    def __init__(self, heartbeat_count=1):
        super().__init__()
        self.heartbeat_count = heartbeat_count
        self.receive_closed = False

    async def receive_events(self):
        try:
            for _ in range(self.heartbeat_count):
                yield {"type": "receive_timeout"}
            await asyncio.Event().wait()
        finally:
            self.receive_closed = True


class _CleanupClient(_FakeClient):
    def __init__(self, *, events=(), receive_close_error=None, close_error=None, hang=False):
        super().__init__(events=events)
        self.receive_close_error = receive_close_error
        self.close_error = close_error
        self.hang = hang
        self.receive_cleanup_cancelled = False
        self.client_cleanup_cancelled = False

    async def receive_events(self):
        try:
            for event in self.events:
                yield event
        finally:
            try:
                if self.hang:
                    await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.receive_cleanup_cancelled = True
                raise
            if self.receive_close_error is not None:
                raise self.receive_close_error

    async def close(self):
        self.close_calls += 1
        try:
            if self.hang:
                await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.client_cleanup_cancelled = True
            raise
        if self.close_error is not None:
            raise self.close_error


def _identity_args():
    return [
        "--candidate-git-sha",
        "candidate-sha",
        "--candidate-image-digest",
        f"sha256:{'1' * 64}",
        "--firmware-identity",
        "firmware-1",
        "--config-fingerprint",
        "sha256:84d14c7fa49d55c1327d80fcd4259b324b1fabe8f6a360fa3f0fc793cbcd5217",
        "--fixture-sha256",
        "dbd55231b25b5de9d7cbe0e54c8b237944b25aedede780afe745802e4d1696c4",
    ]


def _connect_identity_args():
    return [
        "--candidate-git-sha",
        "candidate-sha",
        "--candidate-image-digest",
        f"sha256:{'1' * 64}",
        "--firmware-identity",
        "firmware-1",
        "--config-fingerprint",
        "sha256:4f64b552410469f6442ab03ff90cc4428762fbd347042432055778e1417db9ff",
        "--fixture-sha256",
        "3" * 64,
    ]


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

        async def fake_round_trip(_config, _pcm_chunks, _event_timeout_sec):
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
                    *_identity_args(),
                ],
            ), patch.object(smoke, "_run_prepared_round_trip", fake_round_trip):
                self.assertEqual(smoke.main(), 1)

            report = json.loads(report_path.read_text())

        self.assertEqual(report["schemaVersion"], "google-live-reliability.v1")
        self.assertEqual(report["name"], "real_api")
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["error"]["apiKey"], "<redacted>")

    def test_connect_only_report_is_blocking_and_never_executes_client(self):
        smoke = importlib.import_module("scripts.google_live_smoke")
        reliability = importlib.import_module("scripts.google_live_reliability")
        executed = []

        async def fake_run_smoke(_config):
            executed.append(True)

        with TemporaryDirectory() as directory:
            report_path = Path(directory) / "report.json"
            missing_audio = Path(directory) / "not-used.wav"
            with patch.dict("os.environ", {"GOOGLE_API_KEY": "key"}, clear=True), patch(
                "sys.argv",
                [
                    "google_live_smoke.py",
                    "--report",
                    str(report_path),
                    "--audio-file",
                    str(missing_audio),
                    *_connect_identity_args(),
                ],
            ), patch.object(smoke, "_run_smoke", fake_run_smoke):
                self.assertEqual(smoke.main(), 1)
            report = json.loads(report_path.read_text())

        verdict = reliability.reliability_verdict(
            report["candidateIdentity"],
            [report],
        )
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["error"]["class"], "model_or_config")
        self.assertEqual(verdict["status"], "FAIL")
        self.assertEqual(executed, [])

    def test_invalid_event_timeout_writes_failure_before_preflight(self):
        smoke = importlib.import_module("scripts.google_live_smoke")

        for invalid in ("nan", "inf", "-inf", "0", "-0.1"):
            with self.subTest(invalid=invalid), TemporaryDirectory() as directory:
                report_path = Path(directory) / "report.json"
                with patch.dict(
                    "os.environ", {"GOOGLE_API_KEY": "key"}, clear=True
                ), patch(
                    "sys.argv",
                    [
                        "google_live_smoke.py",
                        "--round-trip",
                        "--report",
                        str(report_path),
                        f"--event-timeout-sec={invalid}",
                        *_identity_args(),
                    ],
                ), patch.object(
                    smoke,
                    "_load_cli_config",
                    side_effect=AssertionError("preflight must not run"),
                ):
                    self.assertEqual(smoke.main(), 1)
                report = json.loads(report_path.read_text())

            self.assertEqual(report["status"], "FAIL")
            self.assertEqual(report["error"]["class"], "model_or_config")

    def test_invalid_event_timeout_without_report_uses_safe_stderr(self):
        smoke = importlib.import_module("scripts.google_live_smoke")

        with patch.dict("os.environ", {"GOOGLE_API_KEY": "key"}, clear=True), patch(
            "sys.argv",
            [
                "google_live_smoke.py",
                "--round-trip",
                "--event-timeout-sec",
                "nan",
            ],
        ), patch.object(
            smoke,
            "_load_cli_config",
            side_effect=AssertionError("preflight must not run"),
        ), patch("sys.stderr") as stderr:
            self.assertEqual(smoke.main(), 1)

        self.assertTrue(stderr.write.called)

    def test_missing_credentials_writes_blocking_skipped_report(self):
        smoke = importlib.import_module("scripts.google_live_smoke")

        with TemporaryDirectory() as directory:
            report_path = Path(directory) / "report.json"
            with patch.dict("os.environ", {}, clear=True), patch(
                "sys.argv",
                [
                    "google_live_smoke.py",
                    "--round-trip",
                    "--report",
                    str(report_path),
                    *_identity_args(),
                ],
            ):
                self.assertEqual(smoke.main(), 1)
            report = json.loads(report_path.read_text())

        self.assertEqual(report["status"], "SKIPPED")
        self.assertEqual(report["error"]["class"], "credential_or_auth")
        self.assertIn("candidateIdentity", report)
        reliability = importlib.import_module("scripts.google_live_reliability")
        verdict = reliability.reliability_verdict(
            report["candidateIdentity"],
            [report],
        )
        self.assertEqual(verdict["status"], "FAIL")
        self.assertEqual(verdict["failures"][0]["code"], "LAYER_SKIPPED")

    def test_missing_candidate_identity_still_writes_blocking_report(self):
        smoke = importlib.import_module("scripts.google_live_smoke")

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
            ):
                self.assertEqual(smoke.main(), 1)
            report = json.loads(report_path.read_text())

        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["error"]["class"], "model_or_config")
        self.assertEqual(report["candidateIdentity"], {})

    def test_manager_preflight_failure_writes_safe_report(self):
        smoke = importlib.import_module("scripts.google_live_smoke")

        async def fail_manager(*_args):
            raise RuntimeError("manager token=do-not-leak")

        with TemporaryDirectory() as directory:
            report_path = Path(directory) / "report.json"
            with patch.dict("os.environ", {}, clear=True), patch(
                "sys.argv",
                [
                    "google_live_smoke.py",
                    "--round-trip",
                    "--report",
                    str(report_path),
                    "--manager-device-id",
                    "device",
                    "--manager-client-id",
                    "client",
                    *_identity_args(),
                ],
            ), patch.object(smoke, "_load_manager_google_live_config", fail_manager):
                self.assertEqual(smoke.main(), 1)
            encoded = report_path.read_text()
            report = json.loads(encoded)

        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["error"]["class"], "model_or_config")
        self.assertNotIn("do-not-leak", encoded)

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
    async def test_connect_smoke_closes_after_success(self):
        smoke = importlib.import_module("scripts.google_live_smoke")
        client = _FakeClient()

        await smoke._run_smoke_client(client, cleanup_timeout_sec=0.1)

        self.assertEqual(client.connect_calls, 1)
        self.assertEqual(client.close_calls, 1)

    async def test_connect_smoke_closes_after_partial_connect_failure(self):
        smoke = importlib.import_module("scripts.google_live_smoke")
        client = _FakeClient(error=RuntimeError("Google Live connect timed out"))

        with self.assertRaisesRegex(RuntimeError, "connect timed out"):
            await smoke._run_smoke_client(client, cleanup_timeout_sec=0.1)

        self.assertEqual(client.close_calls, 1)

    async def test_connect_smoke_preserves_primary_when_cleanup_fails(self):
        smoke = importlib.import_module("scripts.google_live_smoke")
        client = _CleanupClient(
            close_error=RuntimeError("cleanup secret"),
        )
        client.error = RuntimeError("Google Live connect timed out")

        with self.assertRaisesRegex(RuntimeError, "connect timed out"):
            await smoke._run_smoke_client(client, cleanup_timeout_sec=0.1)

        self.assertEqual(client.close_calls, 1)

    async def test_connect_smoke_hanging_close_is_bounded_and_cancelled(self):
        smoke = importlib.import_module("scripts.google_live_smoke")
        client = _CleanupClient(hang=True)

        with self.assertRaises(smoke._CleanupError):
            await asyncio.wait_for(
                smoke._run_smoke_client(client, cleanup_timeout_sec=0.01),
                timeout=0.2,
            )

        self.assertTrue(client.client_cleanup_cancelled)

    async def test_connect_smoke_preserves_primary_when_close_hangs(self):
        smoke = importlib.import_module("scripts.google_live_smoke")
        client = _CleanupClient(hang=True)
        client.error = RuntimeError("Google Live connect timed out")

        with self.assertRaisesRegex(RuntimeError, "connect timed out"):
            await asyncio.wait_for(
                smoke._run_smoke_client(client, cleanup_timeout_sec=0.01),
                timeout=0.2,
            )

        self.assertTrue(client.client_cleanup_cancelled)

    def test_connect_only_cli_reports_safe_cleanup_failure(self):
        smoke = importlib.import_module("scripts.google_live_smoke")

        async def fail_cleanup(_config):
            raise smoke._CleanupError("cleanup token=do-not-leak")

        with patch.dict("os.environ", {"GOOGLE_API_KEY": "key"}, clear=True), patch(
            "sys.argv", ["google_live_smoke.py"]
        ), patch.object(smoke, "_run_smoke", fail_cleanup), patch(
            "sys.stderr"
        ) as stderr:
            self.assertEqual(smoke.main(), 1)

        rendered = "".join(call.args[0] for call in stderr.write.call_args_list)
        self.assertIn("server_state_or_cleanup", rendered)
        self.assertNotIn("do-not-leak", rendered)

    async def test_reported_identity_fingerprints_exact_client_config(self):
        smoke = importlib.import_module("scripts.google_live_smoke")
        reliability = importlib.import_module("scripts.google_live_reliability")
        captured = {}
        captured_objects = []

        def fake_client_factory(config, _logger):
            captured.update(config)
            captured_objects.append(config)
            return _FakeClient(
                events=[
                    {"type": "audio_chunk", "audio": b"audio"},
                    {"type": "audio_end"},
                ]
            )

        with TemporaryDirectory() as directory:
            fixture = Path(directory) / "speech.wav"
            with wave.open(str(fixture), "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(8000)
                wav_file.writeframes(b"\x01\x00" * 160)
            fixture_sha = hashlib.sha256(fixture.read_bytes()).hexdigest()
            base = smoke._build_env_config("gemini-live", "Kore")
            effective = smoke._build_round_trip_config(base, fixture)
            expected = reliability.build_candidate_identity(
                "candidate",
                f"sha256:{'a' * 64}",
                "firmware",
                effective,
                fixture_sha,
            )
            args = unittest.mock.Mock(
                candidate_git_sha="candidate",
                candidate_image_digest=f"sha256:{'a' * 64}",
                firmware_identity="firmware",
                config_fingerprint=expected["configFingerprint"],
                fixture_sha256=fixture_sha,
                audio_file=fixture,
            )

            identity, effective_config, chunks = smoke._prepare_round_trip(
                args,
                base,
                smoke._declared_candidate_identity(args),
            )
            with patch.object(smoke, "GoogleLiveClient", fake_client_factory):
                result = await smoke._run_prepared_round_trip(
                    effective_config,
                    chunks,
                    1,
                )

        self.assertEqual(result["status"], "PASS")
        self.assertEqual(captured, effective_config)
        self.assertIs(captured_objects[0], effective_config)
        self.assertEqual(identity, expected)
        self.assertEqual(captured["input_sample_rate"], 8000)

    def test_candidate_fingerprint_changes_with_wav_sample_rate(self):
        smoke = importlib.import_module("scripts.google_live_smoke")
        reliability = importlib.import_module("scripts.google_live_reliability")
        base = smoke._build_env_config("gemini-live", "Kore")

        with TemporaryDirectory() as directory:
            fingerprints = []
            for sample_rate in (8000, 24000):
                fixture = Path(directory) / f"speech-{sample_rate}.wav"
                with wave.open(str(fixture), "wb") as wav_file:
                    wav_file.setnchannels(1)
                    wav_file.setsampwidth(2)
                    wav_file.setframerate(sample_rate)
                    wav_file.writeframes(b"\x01\x00" * 160)
                identity = reliability.build_candidate_identity(
                    "candidate",
                    f"sha256:{'a' * 64}",
                    "firmware",
                    smoke._build_round_trip_config(base, fixture),
                    hashlib.sha256(fixture.read_bytes()).hexdigest(),
                )
                fingerprints.append(identity["configFingerprint"])

        self.assertNotEqual(*fingerprints)

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
        client = _HeartbeatThenBlockingClient(heartbeat_count=2)

        report = await smoke._run_round_trip_with_retry(
            lambda: client,
            pcm_chunks=[b"\x00\x00"],
            event_timeout_sec=0.01,
        )

        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["attempts"], 1)
        self.assertEqual(report["error"]["class"], "acceptance_timeout")
        self.assertTrue(client.receive_closed)
        self.assertEqual(client.close_calls, 1)

    async def test_receive_timeout_heartbeats_allow_late_valid_response(self):
        smoke = importlib.import_module("scripts.google_live_smoke")
        client = _FakeClient(
            events=[
                {"type": "receive_timeout"},
                {"type": "receive_timeout"},
                {"type": "audio_chunk", "audio": b"audio"},
                {"type": "audio_end"},
            ]
        )

        report = await smoke._run_round_trip_with_retry(
            lambda: client,
            pcm_chunks=[b"\x00\x00"],
            event_timeout_sec=1,
        )

        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["attempts"], 1)

    async def test_connect_timeout_is_retried_once_as_transport(self):
        smoke = importlib.import_module("scripts.google_live_smoke")
        clients = []

        def factory():
            client = _FakeClient(error=RuntimeError("Google Live connect timed out"))
            clients.append(client)
            return client

        report = await smoke._run_round_trip_with_retry(
            factory,
            pcm_chunks=[b"\x00\x00"],
            event_timeout_sec=1,
        )

        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["attempts"], 2)
        self.assertEqual(report["error"]["class"], "network_or_transport")
        self.assertEqual([client.close_calls for client in clients], [1, 1])

    async def test_primary_failure_survives_throwing_cleanup(self):
        smoke = importlib.import_module("scripts.google_live_smoke")
        client = _CleanupClient(
            close_error=RuntimeError("cleanup client secret"),
        )

        async def fail_send(_chunk):
            raise smoke._RoundTripEvidenceError("primary protocol failure")

        client.send_audio = fail_send

        report = await smoke._run_round_trip_with_retry(
            lambda: client,
            pcm_chunks=[b"\x00\x00"],
            event_timeout_sec=1,
        )

        self.assertEqual(report["error"]["class"], "protocol_or_event_order")
        self.assertNotIn("cleanup", json.dumps(report))
        self.assertEqual(client.close_calls, 1)

    async def test_receive_generator_cleanup_failure_is_classified(self):
        smoke = importlib.import_module("scripts.google_live_smoke")
        client = _CleanupClient(
            events=[
                {"type": "audio_chunk", "audio": b"audio"},
                {"type": "audio_end"},
            ],
            receive_close_error=RuntimeError("cleanup receive secret"),
        )

        report = await smoke._run_round_trip_with_retry(
            lambda: client,
            pcm_chunks=[b"\x00\x00"],
            event_timeout_sec=1,
        )

        self.assertEqual(report["error"]["class"], "server_state_or_cleanup")
        self.assertNotIn("secret", json.dumps(report))

    async def test_hanging_cleanup_does_not_replace_primary_failure(self):
        smoke = importlib.import_module("scripts.google_live_smoke")
        client = _CleanupClient(hang=True)

        async def fail_send(_chunk):
            raise smoke._RoundTripEvidenceError("primary protocol failure")

        client.send_audio = fail_send
        report = await asyncio.wait_for(
            smoke._run_round_trip_with_retry(
                lambda: client,
                pcm_chunks=[b"\x00\x00"],
                event_timeout_sec=1,
                cleanup_timeout_sec=0.01,
            ),
            timeout=0.2,
        )

        self.assertEqual(report["error"]["class"], "protocol_or_event_order")
        self.assertTrue(client.client_cleanup_cancelled)
        self.assertNotIn("secret", json.dumps(report))

    async def test_cleanup_only_failure_has_separate_classification(self):
        smoke = importlib.import_module("scripts.google_live_smoke")
        client = _CleanupClient(
            events=[
                {"type": "audio_chunk", "audio": b"audio"},
                {"type": "audio_end"},
            ],
            close_error=RuntimeError("cleanup client secret"),
        )

        report = await smoke._run_round_trip_with_retry(
            lambda: client,
            pcm_chunks=[b"\x00\x00"],
            event_timeout_sec=1,
        )

        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["error"]["class"], "server_state_or_cleanup")
        self.assertNotIn("secret", json.dumps(report))

    async def test_hanging_cleanup_is_bounded_and_classified(self):
        smoke = importlib.import_module("scripts.google_live_smoke")
        client = _CleanupClient(
            events=[
                {"type": "audio_chunk", "audio": b"audio"},
                {"type": "audio_end"},
            ],
            hang=True,
        )

        report = await asyncio.wait_for(
            smoke._run_round_trip_with_retry(
                lambda: client,
                pcm_chunks=[b"\x00\x00"],
                event_timeout_sec=1,
                cleanup_timeout_sec=0.01,
            ),
            timeout=0.2,
        )

        self.assertEqual(report["error"]["class"], "server_state_or_cleanup")
        self.assertTrue(client.receive_cleanup_cancelled)
        self.assertTrue(client.client_cleanup_cancelled)

    def test_real_api_report_is_task1_verdict_compatible(self):
        smoke = importlib.import_module("scripts.google_live_smoke")
        reliability = importlib.import_module("scripts.google_live_reliability")
        identity = {
            "gitSha": "candidate-sha",
            "imageDigest": f"sha256:{'1' * 64}",
            "firmwareIdentity": "firmware-1",
            "configFingerprint": "sha256:84d14c7fa49d55c1327d80fcd4259b324b1fabe8f6a360fa3f0fc793cbcd5217",
            "fixtureSha256": "dbd55231b25b5de9d7cbe0e54c8b237944b25aedede780afe745802e4d1696c4",
        }
        report = smoke._build_report({"status": "PASS"}, identity)

        verdict = reliability.reliability_verdict(identity, [report])

        self.assertEqual(verdict["status"], "PASS")


if __name__ == "__main__":
    unittest.main()
