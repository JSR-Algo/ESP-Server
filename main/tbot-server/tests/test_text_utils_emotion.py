import json
import unittest
from types import SimpleNamespace

from core.utils import textUtils


class TextUtilsEmotionTest(unittest.IsolatedAsyncioTestCase):
    def test_infer_emotion_uses_emoji_first(self):
        self.assertEqual(textUtils.infer_emotion("I am sorry 😔"), ("😔", "sad"))

    def test_infer_emotion_uses_keywords_without_emoji(self):
        self.assertEqual(textUtils.infer_emotion("Let me think about it"), ("🤔", "thinking"))
        self.assertEqual(textUtils.infer_emotion("Xin lỗi bạn nha"), ("😔", "sad"))


    def test_text_cleanup_emoji_detection_and_default_emotion(self):
        self.assertEqual(textUtils.get_string_no_punctuation_or_emoji("  !🙂Hello 😴!!"), "Hello")
        self.assertTrue(textUtils.is_punctuation_or_emoji("🙂"))
        self.assertFalse(textUtils.is_punctuation_or_emoji("A"))
        self.assertEqual(textUtils.infer_emotion("plain", default_emotion="unknown"), ("🙂", "unknown"))
        self.assertEqual(textUtils.check_emoji("Hi🙂\nthere🚀"), "Hithere")

    async def test_get_emotion_sends_payload_and_logs_send_failures(self):
        sent = []

        class _Socket:
            async def send(self, payload):
                sent.append(json.loads(payload))

        class _FailSocket:
            async def send(self, payload):
                raise RuntimeError("closed")

        class _Logger:
            def __init__(self):
                self.messages = []

            def bind(self, **kwargs):
                self.messages.append(("bind", kwargs))
                return self

            def warning(self, message):
                self.messages.append(("warning", message))

        conn = SimpleNamespace(websocket=_Socket(), session_id="session-1")
        await textUtils.get_emotion(conn, "wow")
        self.assertEqual(sent[0]["type"], "llm")
        self.assertEqual(sent[0]["emotion"], "surprised")
        self.assertEqual(sent[0]["session_id"], "session-1")

        logger = _Logger()
        await textUtils.get_emotion(SimpleNamespace(websocket=_FailSocket(), session_id="s", logger=logger), "hello")
        self.assertTrue(any(level == "warning" for level, _ in logger.messages))


if __name__ == "__main__":
    unittest.main()
