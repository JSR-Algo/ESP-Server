import asyncio
import importlib
import unittest
from unittest.mock import patch


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
        ):
            with self.assertRaisesRegex(
                RuntimeError, "manager private config voice_mode is not google_live"
            ):
                asyncio.run(
                    smoke._load_manager_google_live_config("device", "client")
                )

        self.assertEqual(closed, [True])


if __name__ == "__main__":
    unittest.main()
