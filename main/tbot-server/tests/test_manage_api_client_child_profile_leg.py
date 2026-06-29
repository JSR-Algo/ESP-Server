"""Contract test pinning the device-leg child-name read
(``config.manage_api_client.get_device_child_name``).

Mirrors tests/test_manage_api_client_lesson_legs.py: NO network, NO real backend —
a ``_RecordingClient`` returns a canned ``_FakeResponse`` so the REAL function body
runs end to end. Pins the exact GET verb + URL, the device-scoped bearer + Accept
headers, the ``data.childName`` unwrap/trim, and the blank/missing -> None contract.

This leg is the conversation-personalization counterpart to the lesson legs: the
mobile app writes the child name to the backend ``child_profiles.display_name``,
which the esp manager-api ``ai_device.child_name`` keyspace cannot see (migration
086), so the robot reads it over this device-token leg.
"""

import importlib.util
import os
import unittest


def _load_real_manage_api_client():
    spec = importlib.util.spec_from_file_location(
        "config._mac_real_for_child_profile_leg_test",
        os.path.join(os.path.dirname(__file__), "..", "config", "manage_api_client.py"),
    )
    mac = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mac)
    return mac


MAC = _load_real_manage_api_client()


class _FakeResponse:
    def __init__(self, *, status_code=200, json_body=None, headers=None, content=b"x"):
        self.status_code = status_code
        self._json_body = json_body
        self.headers = headers or {}
        self.content = b"" if json_body is None else (content or b"x")
        self.closed = False

    def raise_for_status(self):
        return None

    def json(self):
        return self._json_body

    async def aclose(self):
        self.closed = True


class _RecordingClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    async def request(self, method, url, **kwargs):
        self.calls.append(
            {
                "method": method,
                "url": url,
                "params": kwargs.get("params"),
                "headers": kwargs.get("headers"),
                "json": kwargs.get("json"),
            }
        )
        if not self._responses:
            raise AssertionError("client.request called more times than queued responses")
        return self._responses.pop(0)

    @property
    def last(self):
        return self.calls[-1]


BASE = "http://backend.test/v1"
TOKEN = "dev-jwt-123"


class GetDeviceChildNameContractTest(unittest.IsolatedAsyncioTestCase):
    async def test_pins_get_url_bearer_accept_and_unwraps_data_child_name(self):
        client = _RecordingClient([_FakeResponse(json_body={"data": {"childName": "Milo"}})])

        result = await MAC.get_device_child_name(client, BASE, "device-uuid-9", token=TOKEN)

        self.assertEqual(result, "Milo")
        self.assertEqual(len(client.calls), 1)
        call = client.last
        self.assertEqual(call["method"], "GET")
        self.assertEqual(call["url"], "http://backend.test/v1/devices/device-uuid-9/child-profile")
        self.assertIsNone(call["params"])
        self.assertEqual(call["headers"]["Authorization"], "Bearer dev-jwt-123")
        self.assertEqual(call["headers"]["Accept"], "application/json")

    async def test_trims_surrounding_whitespace(self):
        client = _RecordingClient([_FakeResponse(json_body={"data": {"childName": "  Mai  "}})])
        result = await MAC.get_device_child_name(client, BASE, "dev1", token=TOKEN)
        self.assertEqual(result, "Mai")

    async def test_null_child_name_returns_none(self):
        client = _RecordingClient([_FakeResponse(json_body={"data": {"childName": None}})])
        result = await MAC.get_device_child_name(client, BASE, "dev1", token=TOKEN)
        self.assertIsNone(result)

    async def test_blank_child_name_returns_none(self):
        client = _RecordingClient([_FakeResponse(json_body={"data": {"childName": "   "}})])
        result = await MAC.get_device_child_name(client, BASE, "dev1", token=TOKEN)
        self.assertIsNone(result)

    async def test_missing_data_returns_none(self):
        client = _RecordingClient([_FakeResponse(json_body={})])
        result = await MAC.get_device_child_name(client, BASE, "dev1", token=TOKEN)
        self.assertIsNone(result)

    async def test_no_token_omits_authorization_header(self):
        client = _RecordingClient([_FakeResponse(json_body={"data": {"childName": "Milo"}})])
        result = await MAC.get_device_child_name(client, BASE, "dev1")
        self.assertEqual(result, "Milo")
        self.assertNotIn("Authorization", client.last["headers"])
        self.assertEqual(client.last["headers"]["Accept"], "application/json")

    async def test_base_url_trailing_slash_does_not_double_slash(self):
        client = _RecordingClient([_FakeResponse(json_body={"data": {"childName": "Milo"}})])
        await MAC.get_device_child_name(client, BASE + "/", "dev1", token=TOKEN)
        self.assertEqual(client.last["url"], "http://backend.test/v1/devices/dev1/child-profile")
