import json
import os
import time
import unittest

MINT_SECRET = "t64-console-inventory-secret"


class _StubRequest:
    """Minimal request stand-in: only ``headers`` is read by the console handler."""

    def __init__(self, headers=None):
        self.headers = headers or {}


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
        # The connected-robot inventory is served only to a caller holding the
        # internal mint secret (T6.4); these identity cases assert the resolved
        # inventory, so they authenticate.
        self._saved_secret = os.environ.get("TBOT_DEVICE_MINT_SECRET")
        os.environ["TBOT_DEVICE_MINT_SECRET"] = MINT_SECRET

    def tearDown(self):
        self.token_client._cache.clear()
        self.token_client._cache.update(self._saved_cache)
        if self._saved_secret is None:
            os.environ.pop("TBOT_DEVICE_MINT_SECRET", None)
        else:
            os.environ["TBOT_DEVICE_MINT_SECRET"] = self._saved_secret

    async def _body(self, connections, request=None):
        from core.api.lesson_assignment_console_handler import LessonAssignmentConsoleHandler

        handler = LessonAssignmentConsoleHandler(
            {"server": {"api_url": "https://backend.test/v1"}},
            connections,
        )
        if request is None:
            request = _StubRequest({"X-Mint-Secret": MINT_SECRET})
        return (await handler.handle_get(request)).text

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
    def setUp(self):
        # This case asserts the device inventory renders, so it authenticates (T6.4).
        self._saved_secret = os.environ.get("TBOT_DEVICE_MINT_SECRET")
        os.environ["TBOT_DEVICE_MINT_SECRET"] = MINT_SECRET

    def tearDown(self):
        if self._saved_secret is None:
            os.environ.pop("TBOT_DEVICE_MINT_SECRET", None)
        else:
            os.environ["TBOT_DEVICE_MINT_SECRET"] = self._saved_secret

    async def test_console_page_exposes_backend_assignment_and_enrollment_actions(self):
        from core.api.lesson_assignment_console_handler import LessonAssignmentConsoleHandler

        handler = LessonAssignmentConsoleHandler(
            {"server": {"api_url": "https://backend.test/v1"}},
            {"dev-1": object()},
        )

        response = await handler.handle_get(_StubRequest({"X-Mint-Secret": MINT_SECRET}))
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


class LessonAssignmentConsoleInventoryAuthTest(unittest.IsolatedAsyncioTestCase):
    """T6.4 — nginx proxies /tbot/ with no auth, so the console page is public.

    The page itself has to stay reachable (an operator loads it in a browser to
    paste a parent JWT, and a browser cannot send X-Mint-Secret), but the
    connected-robot inventory pairs each live robot's MAC with its backend device
    UUID. Publishing that to anonymous callers hands out the fleet.
    """

    def setUp(self):
        from config import device_token_client

        self.token_client = device_token_client
        self._saved_cache = dict(device_token_client._cache)
        device_token_client._cache.clear()
        self.mac = "14:c1:9f:d1:ac:20"
        self.device_uuid = "22222222-2222-4222-8222-222222222222"
        device_token_client._cache[self.mac] = (self.device_uuid, "jwt", time.time())
        self._saved_secret = os.environ.get("TBOT_DEVICE_MINT_SECRET")
        os.environ["TBOT_DEVICE_MINT_SECRET"] = MINT_SECRET

    def tearDown(self):
        self.token_client._cache.clear()
        self.token_client._cache.update(self._saved_cache)
        if self._saved_secret is None:
            os.environ.pop("TBOT_DEVICE_MINT_SECRET", None)
        else:
            os.environ["TBOT_DEVICE_MINT_SECRET"] = self._saved_secret

    async def _body(self, request):
        from core.api.lesson_assignment_console_handler import LessonAssignmentConsoleHandler

        handler = LessonAssignmentConsoleHandler(
            {"server": {"api_url": "https://backend.test/v1"}},
            {self.mac: object()},
        )
        return (await handler.handle_get(request)).text

    async def test_anonymous_request_gets_the_page_but_no_fleet_inventory(self):
        body = await self._body(_StubRequest())

        self.assertIn("TBOT Lesson Assignment", body)  # page still served
        self.assertNotIn(self.mac, body)
        self.assertNotIn(self.device_uuid, body)

    async def test_wrong_mint_secret_gets_no_fleet_inventory(self):
        body = await self._body(_StubRequest({"X-Mint-Secret": "wrong-secret"}))

        self.assertNotIn(self.mac, body)
        self.assertNotIn(self.device_uuid, body)

    async def test_correct_mint_secret_still_gets_the_inventory(self):
        body = await self._body(_StubRequest({"X-Mint-Secret": MINT_SECRET}))

        self.assertIn(self.mac, body)
        self.assertIn(self.device_uuid, body)

    async def test_unconfigured_mint_secret_fails_closed(self):
        os.environ.pop("TBOT_DEVICE_MINT_SECRET", None)

        body = await self._body(_StubRequest({"X-Mint-Secret": MINT_SECRET}))

        self.assertNotIn(self.mac, body)
        self.assertNotIn(self.device_uuid, body)

    async def test_request_without_headers_does_not_crash(self):
        # Callers in-tree pass bare stubs; the gate must not raise on them.
        body = await self._body(object())

        self.assertNotIn(self.device_uuid, body)


class LessonAssignmentConsoleScriptInjectionTest(unittest.IsolatedAsyncioTestCase):
    """The device inventory is interpolated into a <script> block.

    Registry keys are the device-supplied ``device-id`` websocket header, so a
    robot can choose its own key. json.dumps leaves '<' and '/' intact, so an id
    containing ``</script>`` would close the block and inject markup into the
    operator console.
    """

    def setUp(self):
        self._saved_secret = os.environ.get("TBOT_DEVICE_MINT_SECRET")
        os.environ["TBOT_DEVICE_MINT_SECRET"] = MINT_SECRET

    def tearDown(self):
        if self._saved_secret is None:
            os.environ.pop("TBOT_DEVICE_MINT_SECRET", None)
        else:
            os.environ["TBOT_DEVICE_MINT_SECRET"] = self._saved_secret

    async def test_malicious_device_id_cannot_close_the_script_block(self):
        from core.api.lesson_assignment_console_handler import LessonAssignmentConsoleHandler

        hostile = '</script><script>window.__pwned=1</script>'
        handler = LessonAssignmentConsoleHandler(
            {"server": {"api_url": "https://backend.test/v1"}},
            {hostile: object()},
        )

        body = (
            await handler.handle_get(_StubRequest({"X-Mint-Secret": MINT_SECRET}))
        ).text

        # The payload may still appear as inert JSON text — that is data, not
        # markup. What must NOT happen is the script block being closed early.
        self.assertNotIn("</script><script>", body)
        self.assertIn("\\u003c/script\\u003e\\u003cscript\\u003e", body)
        # exactly one opening and one closing script tag remain
        self.assertEqual(body.count("<script>"), 1)
        self.assertEqual(body.count("</script>"), 1)
        # and the escaped form still decodes back to the original id
        script = body.split("const connectedDevices = ", 1)[1].split(";\n", 1)[0]
        self.assertEqual(json.loads(script)[0]["mac"], hostile)


if __name__ == "__main__":
    unittest.main()
