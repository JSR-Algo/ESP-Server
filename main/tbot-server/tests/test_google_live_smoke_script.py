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


if __name__ == "__main__":
    unittest.main()
