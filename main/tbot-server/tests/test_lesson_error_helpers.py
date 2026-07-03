import unittest

from core.lesson.errors import (
    RENDERER_CAPABILITY,
    device_renderer_capabilities,
    lesson_capability_ok,
)


class LessonErrorHelperTest(unittest.TestCase):
    def test_lesson_capability_rejects_non_dict_features(self):
        self.assertFalse(lesson_capability_ok(None))
        self.assertFalse(lesson_capability_ok("lesson"))

    def test_device_renderer_capabilities_default_for_missing_or_bad_renderer(self):
        self.assertEqual(device_renderer_capabilities(None), [RENDERER_CAPABILITY])
        self.assertEqual(device_renderer_capabilities({"renderer": [None, ""]}), [RENDERER_CAPABILITY])


if __name__ == "__main__":
    unittest.main()
