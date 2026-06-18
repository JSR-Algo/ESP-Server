import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


class _FakeRequest:
    def __init__(self, *, device_id="device-1", secret="secret"):
        self.match_info = {"deviceId": device_id}
        self.headers = {"X-Mint-Secret": secret}

    async def json(self):
        return {"assignmentId": "assignment-1"}


class LessonNudgeHandlerTest(unittest.IsolatedAsyncioTestCase):
    async def test_http_server_preserves_initially_empty_shared_connection_map(self):
        from core.http_server import SimpleHttpServer

        connections = {}
        server = SimpleHttpServer({"server": {"auth_key": "test-key"}}, connections)

        self.assertIs(server.lesson_nudge_handler.connections, connections)

    async def test_rejects_missing_or_wrong_mint_secret(self):
        from core.api.lesson_nudge_handler import LessonNudgeHandler

        os.environ["TBOT_DEVICE_MINT_SECRET"] = "secret"
        handler = LessonNudgeHandler({}, {})

        response = await handler.handle_post(_FakeRequest(secret="wrong"))

        self.assertEqual(response.status, 401)

    async def test_triggers_existing_pull_path_for_live_handler(self):
        from core.api.lesson_nudge_handler import LessonNudgeHandler
        import core.lesson.runtime as runtime

        os.environ["TBOT_DEVICE_MINT_SECRET"] = "secret"
        conn = object()
        pull = AsyncMock(return_value=None)
        saved = runtime.maybe_start_lesson_on_connect
        runtime.maybe_start_lesson_on_connect = pull
        try:
            handler = LessonNudgeHandler({}, {"device-1": conn})
            response = await handler.handle_post(_FakeRequest())
        finally:
            runtime.maybe_start_lesson_on_connect = saved

        self.assertEqual(response.status, 202)
        pull.assert_awaited_once_with(conn)

    async def test_resolves_backend_uuid_nudge_to_live_mac_connection(self):
        from core.api.lesson_nudge_handler import LessonNudgeHandler
        import core.lesson.runtime as runtime

        os.environ["TBOT_DEVICE_MINT_SECRET"] = "secret"
        conn = SimpleNamespace(
            device_id="aa:bb:cc:dd:ee:ff",
            config={"server": {"api_url": "http://backend.local/v1"}},
            logger=None,
        )
        pull = AsyncMock(return_value=None)
        saved = runtime.maybe_start_lesson_on_connect
        runtime.maybe_start_lesson_on_connect = pull
        try:
            handler = LessonNudgeHandler({}, {"aa:bb:cc:dd:ee:ff": conn})
            with patch(
                "config.device_token_client.resolve_device_identity",
                AsyncMock(return_value=("backend-device-uuid", "token")),
            ) as resolve:
                response = await handler.handle_post(_FakeRequest(device_id="backend-device-uuid"))
        finally:
            runtime.maybe_start_lesson_on_connect = saved

        self.assertEqual(response.status, 202)
        resolve.assert_awaited_once()
        pull.assert_awaited_once_with(conn)

    async def test_offline_device_is_accepted_for_pull_on_connect(self):
        from core.api.lesson_nudge_handler import LessonNudgeHandler

        os.environ["TBOT_DEVICE_MINT_SECRET"] = "secret"
        handler = LessonNudgeHandler({}, {})

        response = await handler.handle_post(_FakeRequest(device_id="offline-device"))

        self.assertEqual(response.status, 202)


if __name__ == "__main__":
    unittest.main()
