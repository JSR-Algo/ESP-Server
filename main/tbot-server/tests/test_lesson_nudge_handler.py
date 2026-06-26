import os
import json
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

    async def test_backend_uuid_resolution_failure_on_one_connection_does_not_block_matching_connection(self):
        from core.api.lesson_nudge_handler import LessonNudgeHandler
        import core.lesson.runtime as runtime

        os.environ["TBOT_DEVICE_MINT_SECRET"] = "secret"
        bad_conn = SimpleNamespace(
            device_id="00:00:00:00:00:01",
            config={"server": {"api_url": "http://backend.local/v1"}},
            logger=None,
        )
        good_conn = SimpleNamespace(
            device_id="00:00:00:00:00:02",
            config={"server": {"api_url": "http://backend.local/v1"}},
            logger=None,
        )
        pull = AsyncMock(return_value=None)
        saved = runtime.maybe_start_lesson_on_connect
        runtime.maybe_start_lesson_on_connect = pull
        resolve = AsyncMock(side_effect=[RuntimeError("mint service timeout"), ("backend-device-uuid", "token")])
        try:
            handler = LessonNudgeHandler(
                {},
                {
                    "00:00:00:00:00:01": bad_conn,
                    "00:00:00:00:00:02": good_conn,
                },
            )
            with patch("config.device_token_client.resolve_device_identity", resolve):
                response = await handler.handle_post(_FakeRequest(device_id="backend-device-uuid"))
        finally:
            runtime.maybe_start_lesson_on_connect = saved

        self.assertEqual(response.status, 202)
        self.assertEqual(resolve.await_count, 2)
        pull.assert_awaited_once_with(good_conn)

    async def test_backend_uuid_nudge_pulls_assignment_and_emits_lesson_prepare(self):
        from core.api.lesson_nudge_handler import LessonNudgeHandler
        import config.device_token_client as dtc
        import config.manage_api_client as mac
        from tests.test_lesson_runtime import FIX, _RepublishConn, _build_manifest, _manifest_checksum

        events = []

        class _CapturingLogger:
            def bind(self, **kwargs):
                return self

            def info(self, message, *args, **kwargs):
                events.append(("info", str(message)))

            def warning(self, message, *args, **kwargs):
                events.append(("warning", str(message)))

            def error(self, message, *args, **kwargs):
                events.append(("error", str(message)))

        os.environ["TBOT_DEVICE_MINT_SECRET"] = "secret"
        prep = FIX["frames"]["lesson_prepare"]
        conn = _RepublishConn(api_base="https://backend.test/v1")
        conn.device_id = "14:c1:9f:d1:a8:48"
        conn.logger = _CapturingLogger()
        assignment = {
            "assignmentId": prep["assignmentId"],
            "assignmentVersion": prep["body"]["assignmentVersion"],
            "childId": "child-1",
            "lessonId": prep["lessonId"],
            "lessonVersion": prep["lessonVersion"],
            "manifestChecksum": _manifest_checksum(),
            "profile": "espTft",
            "state": "ASSIGNED",
        }

        async def _resolve_device_identity(client, base_url, device_id, *, logger=None):
            return "backend-device-uuid", "device-token"

        async def _get_assignment(client, base_url, device_id, *, token=None):
            self.assertEqual(device_id, "backend-device-uuid")
            self.assertEqual(token, "device-token")
            return assignment

        async def _get_manifest(
            client,
            base_url,
            lesson_id,
            profile,
            *,
            token=None,
            renderer_capabilities=None,
            lesson_version=None,
        ):
            self.assertEqual(lesson_id, prep["lessonId"])
            self.assertEqual(lesson_version, prep["lessonVersion"])
            self.assertEqual(profile, "espTft")
            self.assertEqual(token, "device-token")
            return _build_manifest(), f'"lesson-3-espTft-{_manifest_checksum()}"'

        saved = (dtc.resolve_device_identity, mac.get_current_assignment, mac.get_lesson_manifest)
        dtc.resolve_device_identity = _resolve_device_identity
        mac.get_current_assignment = _get_assignment
        mac.get_lesson_manifest = _get_manifest
        try:
            handler = LessonNudgeHandler({}, {"14:c1:9f:d1:a8:48": conn})
            response = await handler.handle_post(_FakeRequest(device_id="backend-device-uuid"))
        finally:
            dtc.resolve_device_identity, mac.get_current_assignment, mac.get_lesson_manifest = saved

        self.assertEqual(response.status, 202)
        self.assertIsNotNone(conn.lesson_runtime)
        sent = [json.loads(payload) for payload in conn.websocket.sent]
        self.assertEqual([frame["type"] for frame in sent], ["lesson_prepare"])
        self.assertEqual(sent[0]["assignmentId"], prep["assignmentId"])
        messages = [message for _level, message in events]
        assignment_logs = [message for message in messages if "assignment/current" in message]
        self.assertTrue(assignment_logs, messages)
        self.assertIn(f"assignmentId={prep['assignmentId']}", assignment_logs[0])
        self.assertIn("state=ASSIGNED", assignment_logs[0])
        self.assertIn("lessonVersion=3", assignment_logs[0])
        self.assertIn("assignmentVersion=1", assignment_logs[0])
        self.assertIn("manifestChecksum=", assignment_logs[0])
        self.assertIn("deviceId=14:c1:9f:d1:a8:48", assignment_logs[0])
        self.assertIn("childId=child-1", assignment_logs[0])
        manifest_logs = [message for message in messages if "lesson manifest fetched" in message]
        self.assertTrue(manifest_logs, messages)
        self.assertIn(f"lessonId={prep['lessonId']}", manifest_logs[0])
        self.assertIn("profile=espTft", manifest_logs[0])
        self.assertIn("lessonVersion=3", manifest_logs[0])
        self.assertIn("manifestChecksum=", manifest_logs[0])
        self.assertIn("stepCount=", manifest_logs[0])
        self.assertIn("storyBeat", manifest_logs[0])
        self.assertIn("waitForChild", manifest_logs[0])

    async def test_offline_device_is_accepted_for_pull_on_connect(self):
        from core.api.lesson_nudge_handler import LessonNudgeHandler

        os.environ["TBOT_DEVICE_MINT_SECRET"] = "secret"
        handler = LessonNudgeHandler({}, {})

        response = await handler.handle_post(_FakeRequest(device_id="offline-device"))

        self.assertEqual(response.status, 202)


    async def test_rejects_when_mint_secret_is_not_configured(self):
        from core.api.lesson_nudge_handler import LessonNudgeHandler

        os.environ.pop("TBOT_DEVICE_MINT_SECRET", None)
        handler = LessonNudgeHandler({}, {})

        response = await handler.handle_post(_FakeRequest())

        self.assertEqual(response.status, 503)

    async def test_find_connection_handles_empty_maps_missing_metadata_and_import_failures(self):
        from core.api.lesson_nudge_handler import LessonNudgeHandler

        self.assertIsNone(await LessonNudgeHandler({}, None)._find_connection("device-1"))

        no_identity = SimpleNamespace(headers={}, config={})
        self.assertIsNone(await LessonNudgeHandler({}, {"fallback": no_identity})._find_connection("device-1"))

        with patch.dict("sys.modules", {"httpx": None}):
            self.assertIsNone(await LessonNudgeHandler({}, {"device-1": object()})._find_connection("missing"))


if __name__ == "__main__":
    unittest.main()
