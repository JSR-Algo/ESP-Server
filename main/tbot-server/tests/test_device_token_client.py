import os
import unittest
from unittest.mock import AsyncMock, patch

import httpx

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

    async def post(self, url, *, json, headers, follow_redirects=False):
        self.calls.append(
            {
                "url": url,
                "json": json,
                "headers": headers,
                "follow_redirects": follow_redirects,
            }
        )
        return self.response


class _RaisingClient:
    def __init__(self):
        self.calls = []

    async def post(self, url, *, json, headers, follow_redirects=False):
        self.calls.append(
            {
                "url": url,
                "json": json,
                "headers": headers,
                "follow_redirects": follow_redirects,
            }
        )
        raise OSError("offline")


class _TransientClient:
    def __init__(self):
        self.calls = 0

    async def post(self, url, *, json, headers, follow_redirects=False):
        self.calls += 1
        if self.calls == 1:
            raise httpx.ReadTimeout("temporary backend stall")
        return _Response()


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
            device_token_client.time, "monotonic", return_value=1001.0
        ):
            cached = await device_token_client.resolve_device_identity(client, "https://backend", "mac")

        with patch.dict(os.environ, {"TBOT_DEVICE_MINT_SECRET": "mint-secret"}), patch.object(
            device_token_client.time, "monotonic", return_value=2000.0
        ):
            refreshed = await device_token_client.resolve_device_identity(client, "https://backend/", "mac")

        self.assertEqual(cached, ("cached-device", "cached-jwt"))
        self.assertEqual(refreshed, ("new-device", "new-jwt"))
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.calls[0]["url"], "https://backend/internal/devices/mint-token")

    async def test_force_refresh_bypasses_still_fresh_cached_token(self):
        client = _Client(_Response({"deviceUuid": "device-1", "token": "new-jwt"}))
        device_token_client._cache.clear()
        device_token_client._cache["mac"] = ("device-1", "cached-jwt", 1000.0)

        with patch.dict(os.environ, {"TBOT_DEVICE_MINT_SECRET": "mint-secret"}), patch.object(
            device_token_client.time, "monotonic", return_value=1001.0
        ):
            refreshed = await device_token_client.resolve_device_identity(
                client,
                "https://backend",
                "mac",
                force_refresh=True,
            )

        self.assertEqual(refreshed, ("device-1", "new-jwt"))
        self.assertEqual(len(client.calls), 1)

    async def test_refreshes_cached_token_after_whole_lesson_validity_margin(self):
        client = _Client(_Response({"deviceUuid": "new-device", "token": "new-jwt"}))
        device_token_client._cache.clear()
        device_token_client._cache["mac"] = ("cached-device", "cached-jwt", 1000.0)

        with patch.dict(os.environ, {"TBOT_DEVICE_MINT_SECRET": "mint-secret"}), patch.object(
            device_token_client.time, "monotonic", side_effect=[1000.0 + (11 * 60), 1000.0 + (11 * 60)]
        ):
            result = await device_token_client.resolve_device_identity(client, "https://backend", "mac")

        self.assertEqual(result, ("new-device", "new-jwt"))
        self.assertEqual(len(client.calls), 1)

    async def test_refreshes_cached_token_when_wall_clock_moves_backward_but_monotonic_age_is_stale(self):
        client = _Client(_Response({"deviceUuid": "new-device", "token": "new-jwt"}))
        device_token_client._cache.clear()
        device_token_client._cache["mac"] = ("cached-device", "cached-jwt", 1000.0)

        with patch.dict(os.environ, {"TBOT_DEVICE_MINT_SECRET": "mint-secret"}), patch.object(
            device_token_client.time, "time", return_value=900.0
        ), patch.object(
            device_token_client.time, "monotonic", side_effect=[1000.0 + (11 * 60), 1000.0 + (11 * 60)]
        ):
            result = await device_token_client.resolve_device_identity(client, "https://backend", "mac")

        self.assertEqual(result, ("new-device", "new-jwt"))
        self.assertEqual(len(client.calls), 1)

    async def test_reuses_cached_token_at_whole_lesson_validity_boundary(self):
        client = _Client(_Response({"deviceUuid": "new-device", "token": "new-jwt"}))
        device_token_client._cache.clear()
        device_token_client._cache["mac"] = ("cached-device", "cached-jwt", 1000.0)

        with patch.dict(os.environ, {"TBOT_DEVICE_MINT_SECRET": "mint-secret"}), patch.object(
            device_token_client.time, "monotonic", return_value=1000.0 + (10 * 60)
        ):
            result = await device_token_client.resolve_device_identity(client, "https://backend", "mac")

        self.assertEqual(result, ("cached-device", "cached-jwt"))
        self.assertEqual(client.calls, [])

    async def test_cached_device_uuid_uses_inclusive_monotonic_cache_boundary(self):
        device_token_client._cache.clear()
        device_token_client._cache["mac"] = ("cached-device", "cached-jwt", 1000.0)

        with patch.object(
            device_token_client.time, "monotonic", return_value=1000.0 + device_token_client._CACHE_TTL_S
        ):
            self.assertEqual(device_token_client.cached_device_uuid("mac"), "cached-device")

        with patch.object(
            device_token_client.time, "monotonic", return_value=1000.0 + device_token_client._CACHE_TTL_S + 0.001
        ):
            self.assertIsNone(device_token_client.cached_device_uuid("mac"))

    async def test_cached_device_uuid_expires_legacy_wall_clock_entries(self):
        device_token_client._cache.clear()
        now = 1_800_000_000.0
        device_token_client._cache["fresh"] = ("fresh-device", "jwt", now - 1)
        device_token_client._cache["stale"] = (
            "stale-device",
            "jwt",
            now - device_token_client._CACHE_TTL_S - 1,
        )

        with patch.object(device_token_client.time, "time", return_value=now), patch.object(
            device_token_client.time, "monotonic", return_value=50_000.0
        ):
            self.assertEqual(device_token_client.cached_device_uuid("fresh"), "fresh-device")
            self.assertIsNone(device_token_client.cached_device_uuid("stale"))

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

    async def test_retries_one_transient_mint_transport_failure(self):
        client = _TransientClient()
        device_token_client._cache.clear()

        with patch.dict(os.environ, {"TBOT_DEVICE_MINT_SECRET": "mint-secret"}), patch(
            "asyncio.sleep", new=AsyncMock()
        ) as sleep:
            result = await device_token_client.resolve_device_identity(
                client,
                "https://backend",
                "14:c1:9f:d1:a8:48",
            )

        self.assertEqual(result, ("device-1", "jwt-1"))
        self.assertEqual(client.calls, 2)
        sleep.assert_awaited_once()

    async def test_redirect_does_not_replay_mint_secret_to_location(self):
        hits = []

        async def handler(request):
            hits.append((str(request.url), request.headers.get("X-Mint-Secret")))
            if request.url.host == "backend.example":
                return httpx.Response(
                    307,
                    headers={"Location": "https://evil.example/capture"},
                    request=request,
                )
            return httpx.Response(200, json={"data": {"deviceUuid": "bad", "token": "bad"}}, request=request)

        client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            follow_redirects=True,
        )
        device_token_client._cache.clear()
        try:
            with patch.dict(os.environ, {"TBOT_DEVICE_MINT_SECRET": "mint-secret"}):
                result = await device_token_client.resolve_device_identity(
                    client,
                    "https://backend.example/v1",
                    "14:c1:9f:d1:a8:48",
                )
        finally:
            await client.aclose()

        self.assertEqual(result, (None, None))
        self.assertEqual(hits, [("https://backend.example/v1/internal/devices/mint-token", "mint-secret")])


if __name__ == "__main__":
    unittest.main()
