import unittest
import sys
import types
from enum import Enum
from types import SimpleNamespace

def _reset_asr_imports():
    for module_name in (
        "core.utils.asr",
        "core.providers.asr.base",
        "core.providers.asr.gemini_asr",
        "core.providers.asr.dto.dto",
    ):
        sys.modules.pop(module_name, None)

    util_module = types.ModuleType("core.utils.util")
    util_module.remove_punctuation_and_length = lambda value: (value, len(value))
    sys.modules["core.utils.util"] = util_module

    report_module = types.ModuleType("core.handle.reportHandle")
    report_module.enqueue_asr_report = lambda *args, **kwargs: None
    sys.modules["core.handle.reportHandle"] = report_module

    receive_module = types.ModuleType("core.handle.receiveAudioHandle")

    async def _noop_async(*args, **kwargs):
        return None

    receive_module.startToChat = _noop_async
    receive_module.handleAudioMessage = _noop_async
    sys.modules["core.handle.receiveAudioHandle"] = receive_module

    dto_module = types.ModuleType("core.providers.asr.dto.dto")

    class _InterfaceType(Enum):
        STREAM = "STREAM"
        NON_STREAM = "NON_STREAM"
        LOCAL = "LOCAL"

    dto_module.InterfaceType = _InterfaceType
    sys.modules["core.providers.asr.dto.dto"] = dto_module


class _Response:
    status_code = 200
    text = "{}"

    def json(self):
        return {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": "Xin chào, tôi nghe rõ tiếng Việt."}
                        ]
                    }
                }
            ]
        }


class GeminiASRProviderTest(unittest.IsolatedAsyncioTestCase):
    async def test_gemini_asr_transcribes_audio_file(self):
        import tempfile
        from pathlib import Path
        _reset_asr_imports()
        from core.utils.asr import create_instance

        with tempfile.TemporaryDirectory() as tmp:
            audio_path = Path(tmp) / "speech.wav"
            audio_path.write_bytes(b"RIFF....WAVEfmt ")

            captured = {}

            def fake_post(url, json, timeout, proxies, headers):
                captured["url"] = url
                captured["json"] = json
                captured["headers"] = headers
                return _Response()

            provider = create_instance(
                "gemini_asr",
                {
                    "api_key": "test-key",
                    "model_name": "gemini-2.5-flash",
                    "language": "vi",
                    "output_dir": tmp,
                },
                True,
            )

            import core.providers.asr.gemini_asr as gemini_asr

            original_post = gemini_asr.requests.post
            gemini_asr.requests.post = fake_post
            try:
                text, file_path = await provider.speech_to_text(
                    [],
                    "session-id",
                    artifacts=SimpleNamespace(file_path=str(audio_path)),
                )
            finally:
                gemini_asr.requests.post = original_post

            self.assertEqual(text, "Xin chào, tôi nghe rõ tiếng Việt.")
            self.assertEqual(file_path, str(audio_path))
            self.assertIn("models/gemini-2.5-flash:generateContent", captured["url"])
            self.assertTrue(captured["json"]["contents"][0]["parts"][0]["inlineData"]["data"])
            self.assertIn("Vietnamese", captured["json"]["contents"][0]["parts"][1]["text"])


if __name__ == "__main__":
    unittest.main()
