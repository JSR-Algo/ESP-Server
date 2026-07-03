import os
import unittest
from unittest.mock import patch

from config import device_token_client


class _Response:
    def __init__(self, data=None):
        self._data = data or {"deviceUuid": "device-1", "token": "jwt-1"}

    def raise_for_status(self):
        return None

    def json(self):
        return {"data": self._data}


class _FailingResponse:
    def raise_for_status(self):
        raise RuntimeError("bad status")


class _Client:
    def __init__(self, response=None):
        self.calls = []
        self.response = response or _Response()

    async def post(self, url, *, json, headers):
        self.calls.append({"url": url, "json": json, "headers": headers})
        return self.response


class _RaisingClient:
    def __init__(self):
        self.calls = []

    async def post(self, url, *, json, headers):
        self.calls.append({"url": url, "json": json, "headers": headers})
        raise OSError("offline")


class _Logger:
    def __init__(self, fail_bind=False):
        self.messages = []
        self.fail_bind = fail_bind

    def bind(self, **kwargs):
        if self.fail_bind:
            raise RuntimeError("logger closed")
        self.messages.append(("bind", kwargs))
        return self

    def info(self, message):
        self.messages.append(("info", message))

    def warning(self, message):
        self.messages.append(("warning", message))


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

    async def test_returns_none_without_required_inputs_or_secret(self):
        client = _Client()
        logger = _Logger()
        device_token_client._cache.clear()

        self.assertEqual(await device_token_client.resolve_device_identity(client, "url", None), (None, None))
        self.assertEqual(await device_token_client.resolve_device_identity(client, None, "mac"), (None, None))

        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                await device_token_client.resolve_device_identity(client, "https://backend", "mac", logger=logger),
                (None, None),
            )

        device_token_client._log(None, "info", "ignored")
        device_token_client._log(_Logger(fail_bind=True), "info", "ignored")
        self.assertEqual(client.calls, [])
        self.assertIn(("info", "mint skipped: TBOT_DEVICE_MINT_SECRET not set"), logger.messages)

    async def test_uses_fresh_cache_and_refreshes_stale_cache(self):
        client = _Client(_Response({"deviceUuid": "new-device", "token": "new-jwt"}))
        device_token_client._cache.clear()
        device_token_client._cache["mac"] = ("cached-device", "cached-jwt", 1000.0)

        with patch.dict(os.environ, {"TBOT_DEVICE_MINT_SECRET": "mint-secret"}), patch.object(
            device_token_client.time, "time", return_value=1001.0
        ):
            cached = await device_token_client.resolve_device_identity(client, "https://backend", "mac")

        with patch.dict(os.environ, {"TBOT_DEVICE_MINT_SECRET": "mint-secret"}), patch.object(
            device_token_client.time, "time", return_value=2000.0
        ):
            refreshed = await device_token_client.resolve_device_identity(client, "https://backend/", "mac")

        self.assertEqual(cached, ("cached-device", "cached-jwt"))
        self.assertEqual(refreshed, ("new-device", "new-jwt"))
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.calls[0]["url"], "https://backend/internal/devices/mint-token")

    async def test_missing_fields_and_post_failures_return_none_and_log_warning(self):
        logger = _Logger()
        device_token_client._cache.clear()

        with patch.dict(os.environ, {"TBOT_DEVICE_MINT_SECRET": "mint-secret"}):
            missing = await device_token_client.resolve_device_identity(
                _Client(_Response({"deviceUuid": "device-only"})),
                "https://backend",
                "mac-missing",
                logger=logger,
            )
            failed_status = await device_token_client.resolve_device_identity(
                _Client(_FailingResponse()),
                "https://backend",
                "mac-status",
                logger=logger,
            )
            failed_post = await device_token_client.resolve_device_identity(
                _RaisingClient(),
                "https://backend",
                "mac-offline",
                logger=logger,
            )

        self.assertEqual(missing, (None, None))
        self.assertEqual(failed_status, (None, None))
        self.assertEqual(failed_post, (None, None))
        warning_messages = [message for level, message in logger.messages if level == "warning"]
        self.assertTrue(any("missing fields" in message for message in warning_messages))
        self.assertTrue(any("RuntimeError" in message for message in warning_messages))
        self.assertTrue(any("OSError" in message for message in warning_messages))


if __name__ == "__main__":
    unittest.main()
