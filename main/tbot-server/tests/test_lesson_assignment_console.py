import unittest


class LessonAssignmentConsoleHandlerTest(unittest.IsolatedAsyncioTestCase):
    async def test_console_page_exposes_backend_assignment_and_enrollment_actions(self):
        from core.api.lesson_assignment_console_handler import LessonAssignmentConsoleHandler

        handler = LessonAssignmentConsoleHandler(
            {"server": {"api_url": "https://backend.test/v1"}},
            {"dev-1": object()},
        )

        response = await handler.handle_get(object())
        body = response.text

        self.assertEqual(response.status, 200)
        self.assertIn("TBOT Lesson Assignment", body)
        self.assertIn("https://backend.test/v1", body)
        self.assertIn("/courses", body)
        self.assertIn("/devices/${deviceId}/assignments", body)
        self.assertIn("/courses/${courseId}/enroll", body)
        self.assertIn("dev-1", body)
        self.assertNotIn("localStorage", body)


if __name__ == "__main__":
    unittest.main()
