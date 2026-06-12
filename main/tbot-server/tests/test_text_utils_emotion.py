import unittest

from core.utils import textUtils


class TextUtilsEmotionTest(unittest.TestCase):
    def test_infer_emotion_uses_emoji_first(self):
        self.assertEqual(textUtils.infer_emotion("I am sorry 😔"), ("😔", "sad"))

    def test_infer_emotion_uses_keywords_without_emoji(self):
        self.assertEqual(textUtils.infer_emotion("Let me think about it"), ("🤔", "thinking"))
        self.assertEqual(textUtils.infer_emotion("Xin lỗi bạn nha"), ("😔", "sad"))


if __name__ == "__main__":
    unittest.main()
