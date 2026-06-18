import os
import unittest
from unittest.mock import patch

from config import device_token_client


class _Response:
    def raise_for_status(self):
        return None

    def json(self):
        return {"data": {"deviceUuid": "device-1", "token": "jwt-1"}}


class _Client:
    def __init__(self):
        self.calls = []

    async def post(self, url, *, json, headers):
        self.calls.append({"url": url, "json": json, "headers": headers})
        return _Response()


class DeviceTokenClientTest(unittest.IsolatedAsyncioTestCase):
    async def test_mint_request_sends_authorization_bearer_and_legacy_header(self):
        client = _Client()
        device_token_client._cache.clear()

        with patch.dict(os.environ, {"TBOT_DEVICE_MINT_SECRET": "mint-secret"}):
            device_uuid, token = await device_token_client.resolve_device_identity(
                client,
                "https://backend.example/v1",
                "14:c1:9f:d1:a8:48",
            )

        self.assertEqual((device_uuid, token), ("device-1", "jwt-1"))
        self.assertEqual(client.calls[0]["headers"]["X-Mint-Secret"], "mint-secret")
        self.assertEqual(client.calls[0]["headers"]["Authorization"], "Bearer mint-secret")


if __name__ == "__main__":
    unittest.main()
