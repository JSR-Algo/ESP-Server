import time
import unittest


class LessonAssignmentConsoleDeviceIdentityTest(unittest.IsolatedAsyncioTestCase):
    """The console posts to /devices/{uuid}/assignments, which rejects a MAC.

    The websocket registry is keyed by MAC, so the device picker must publish the
    resolved backend device UUID — never the MAC — and must not prefill anything it
    could not resolve.
    """

    def setUp(self):
        from config import device_token_client

        self.token_client = device_token_client
        self._saved_cache = dict(device_token_client._cache)
        device_token_client._cache.clear()

    def tearDown(self):
        self.token_client._cache.clear()
        self.token_client._cache.update(self._saved_cache)

    async def _body(self, connections):
        from core.api.lesson_assignment_console_handler import LessonAssignmentConsoleHandler

        handler = LessonAssignmentConsoleHandler(
            {"server": {"api_url": "https://backend.test/v1"}},
            connections,
        )
        return (await handler.handle_get(object())).text

    async def test_connected_mac_is_published_as_its_backend_device_uuid(self):
        mac = "14:c1:9f:d1:ac:20"
        device_uuid = "22222222-2222-4222-8222-222222222222"
        self.token_client._cache[mac] = (device_uuid, "jwt", time.time())

        body = await self._body({mac: object()})

        self.assertIn(f'"deviceId": "{device_uuid}"', body)
        self.assertIn(f'"mac": "{mac}"', body)
        self.assertIn("assignableDevices[0].deviceId", body)

    async def test_unresolved_mac_is_not_offered_as_an_assignable_device(self):
        body = await self._body({"14:c1:9f:d1:ac:20": object()})

        self.assertIn('"deviceId": ""', body)
        self.assertIn("unresolvedDevices", body)

    async def test_expired_mint_cache_entry_is_treated_as_unresolved(self):
        mac = "14:c1:9f:d1:ac:20"
        stale = time.time() - (self.token_client._CACHE_TTL_S + 1)
        self.token_client._cache[mac] = ("22222222-2222-4222-8222-222222222222", "jwt", stale)

        body = await self._body({mac: object()})

        self.assertIn('"deviceId": ""', body)
        self.assertNotIn('"deviceId": "22222222-2222-4222-8222-222222222222"', body)


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


    async def test_console_uses_default_backend_when_config_has_no_api_url(self):
        from core.api.lesson_assignment_console_handler import LessonAssignmentConsoleHandler

        response = await LessonAssignmentConsoleHandler({}, {}).handle_get(object())

        self.assertIn("https://tbot-backend-8wmh.onrender.com/v1", response.text)

    async def test_console_defaults_malformed_server_config(self):
        from core.api.lesson_assignment_console_handler import LessonAssignmentConsoleHandler

        response = await LessonAssignmentConsoleHandler({"server": "bad"}, {}).handle_get(object())

        self.assertIn("https://tbot-backend-8wmh.onrender.com/v1", response.text)


if __name__ == "__main__":
    unittest.main()
