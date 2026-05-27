import importlib
import unittest


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


if __name__ == "__main__":
    unittest.main()
