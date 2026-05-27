import importlib
import unittest


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


if __name__ == "__main__":
    unittest.main()
