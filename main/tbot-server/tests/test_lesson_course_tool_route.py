import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


class _FakeRequest:
    def __init__(self, *, device_id="device-1", secret="secret", body=None):
        self.match_info = {"deviceId": device_id}
        self.headers = {"X-Mint-Secret": secret} if secret is not None else {}
        self.host = "localhost"
        self.remote = "127.0.0.1"
        self._body = body if body is not None else {}

    async def json(self):
        return self._body


class _Orchestrator:
    session_state = SimpleNamespace(value="WORD_ACTIVE")


class _CourseMode:
    orchestrator = _Orchestrator()

    def tool_context(self):
        return {"courseMode": True, "identity": {"lessonSessionId": "s-1", "turnSequenceId": 4}}


def _runtime():
    runtime = SimpleNamespace(state="RUNNING", course_mode=_CourseMode())
    runtime.course_continue = AsyncMock(return_value={"accepted": True, "action": "CLUE_AND_ELICIT"})
    runtime.commit_course_response_plan = AsyncMock(return_value=True)
    return runtime


class LessonCourseToolRouteTest(unittest.IsolatedAsyncioTestCase):
    """S17 run09: internal dev route that lets a simulated (voice-less) driver call the
    course-mode tool operations on the live runtime, mirroring lesson-child-response."""

    def setUp(self):
        self._env = patch.dict(os.environ, {
            "TBOT_DEVICE_MINT_SECRET": "secret", "TBOT_INTERNAL_COURSE_TOOL_ROUTE": "1",
        })
        self._env.start()

    def tearDown(self):
        self._env.stop()

    async def test_route_is_registered_on_the_http_server(self):
        from core.http_server import SimpleHttpServer
        server = SimpleHttpServer({"server": {"auth_key": "test-key"}}, {})
        self.assertTrue(callable(getattr(server.lesson_nudge_handler, "handle_course_tool_post", None)))

    async def test_disabled_by_default_and_requires_mint_secret(self):
        from core.api.lesson_nudge_handler import LessonNudgeHandler
        handler = LessonNudgeHandler({}, {"device-1": SimpleNamespace(lesson_runtime=_runtime())})
        with patch.dict(os.environ, {"TBOT_INTERNAL_COURSE_TOOL_ROUTE": ""}):
            response = await handler.handle_course_tool_post(_FakeRequest(body={"operation": "context"}))
            self.assertEqual(response.status, 404)
        response = await handler.handle_course_tool_post(_FakeRequest(secret="wrong", body={"operation": "context"}))
        self.assertEqual(response.status, 401)

    async def test_dispatches_operation_to_live_runtime_and_returns_tool_context(self):
        import json
        from core.api.lesson_nudge_handler import LessonNudgeHandler
        runtime = _runtime()
        handler = LessonNudgeHandler({}, {"device-1": SimpleNamespace(lesson_runtime=runtime)})
        arguments = {"lessonSessionId": "s-1", "turnSequenceId": 4, "observationId": "o-1"}
        response = await handler.handle_course_tool_post(
            _FakeRequest(body={"operation": "course_continue", "arguments": arguments}))
        self.assertEqual(response.status, 202)
        data = json.loads(response.text)["data"]
        runtime.course_continue.assert_awaited_once_with(arguments)
        self.assertEqual(data["result"]["action"], "CLUE_AND_ELICIT")
        self.assertEqual(data["toolContext"]["identity"]["turnSequenceId"], 4)
        self.assertEqual(data["sessionState"], "WORD_ACTIVE")
        self.assertEqual(data["state"], "RUNNING")

    async def test_rejects_unknown_operation_and_reports_no_course_lesson(self):
        import json
        from core.api.lesson_nudge_handler import LessonNudgeHandler
        handler = LessonNudgeHandler({}, {"device-1": SimpleNamespace(lesson_runtime=_runtime())})
        response = await handler.handle_course_tool_post(_FakeRequest(body={"operation": "stop"}))
        self.assertEqual(response.status, 400)
        idle = LessonNudgeHandler({}, {"device-1": SimpleNamespace(lesson_runtime=None)})
        response = await idle.handle_course_tool_post(_FakeRequest(body={"operation": "context"}))
        self.assertEqual(response.status, 202)
        self.assertEqual(json.loads(response.text)["data"]["reason"], "no-active-course-lesson")
        offline = LessonNudgeHandler({}, {})
        response = await offline.handle_course_tool_post(_FakeRequest(body={"operation": "context"}))
        self.assertEqual(response.status, 202)
        self.assertEqual(json.loads(response.text)["data"]["reason"], "device-offline")


if __name__ == "__main__":
    unittest.main()
