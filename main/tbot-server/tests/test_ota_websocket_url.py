import unittest
import sys
import asyncio
import json
import os

for module_name, required_attr in (
    ("core.auth", "AuthManager"),
    ("core.utils.util", "get_local_ip"),
):
    if module_name in sys.modules and not hasattr(sys.modules[module_name], required_attr):
        del sys.modules[module_name]
from core.api.ota_handler import OTAHandler, is_placeholder_websocket_url


class _FakeOtaRequest:
    method = "POST"

    def __init__(self, device_id="AA:BB:CC:DD:EE:01", client_id="unit-test-client"):
        self.headers = {
            "device-id": device_id,
            "client-id": client_id,
            "device-model": "esp32-s3-touch-lcd-3.5",
            "device-version": "0.0.0",
        }

    async def text(self):
        return json.dumps(
            {
                "board": {"type": "esp32-s3-touch-lcd-3.5"},
                "application": {"version": "0.0.0"},
            }
        )


def _full_handler(server_overrides=None):
    """Build a real OTAHandler with a complete server config for handle_post().

    Defaults keep auth disabled for legacy token-gating unit cases while using
    a concrete (non-placeholder) websocket + /v1 api_url.
    """
    server = {
        "auth_key": "test-secret",
        "auth": {"enabled": False},
        "websocket": "wss://ws.example.com/tbot/v1/",
        "api_url": "https://tbot-backend-8wmh.onrender.com/v1",
        "port": 8000,
        "http_port": 8003,
        "timezone_offset": 7,
    }
    if server_overrides:
        server.update(server_overrides)
    handler = OTAHandler({"server": server, "firmware_cache_ttl": 30})
    # Avoid touching the filesystem firmware cache in unit tests.
    handler._refresh_bin_cache_if_needed = lambda: None
    return handler


def _config(websocket, api_url=None):
    return {
        "server": {
            "auth_key": "test-secret",
            "auth": {"enabled": False},
            "websocket": websocket,
            "api_url": api_url or "",
        }
    }


def _handler(websocket, api_url=None):
    handler = OTAHandler.__new__(OTAHandler)
    handler.config = _config(websocket, api_url)
    return handler


class OTAWebsocketUrlTest(unittest.TestCase):
    def test_chinese_placeholder_falls_back_to_local_websocket_url(self):
        handler = _handler("ws://你的ip或者域名:端口号/tbot/v1/")

        self.assertEqual(
            handler._get_websocket_url("192.168.100.154", 8000),
            "ws://192.168.100.154:8000/tbot/v1/",
        )

    def test_explicit_websocket_url_is_preserved(self):
        handler = _handler("ws://192.168.100.154:8000/tbot/v1/")

        self.assertEqual(
            handler._get_websocket_url("10.0.0.5", 9000),
            "ws://192.168.100.154:8000/tbot/v1/",
        )

    def test_explicit_backend_api_url_is_preserved_for_firmware_config_polling(self):
        handler = _handler(
            "ws://192.168.100.154:8000/tbot/v1/",
            api_url="https://api.example.com",
        )

        self.assertEqual(
            handler._get_api_url("10.0.0.5", 8003),
            "https://api.example.com",
        )

    def test_ota_post_response_includes_configured_api_url(self):
        handler = OTAHandler(
            {
                "server": {
                    "auth_key": "test-secret",
                    "auth": {"enabled": False},
                    "websocket": "wss://ws.example.com/tbot/v1/",
                    "api_url": "https://backend.example.com/v1/",
                    "port": 8000,
                    "http_port": 8003,
                    "timezone_offset": 7,
                },
                "firmware_cache_ttl": 30,
            }
        )
        handler._refresh_bin_cache_if_needed = lambda: None

        response = asyncio.run(handler.handle_post(_FakeOtaRequest()))
        payload = json.loads(response.text)

        self.assertEqual(payload["api_url"], "https://backend.example.com/v1")
        self.assertEqual(payload["websocket"]["url"], "wss://ws.example.com/tbot/v1/")
        self.assertIn("server_time", payload)

    def test_placeholder_detection_covers_chinese_and_english_templates(self):
        self.assertTrue(is_placeholder_websocket_url("ws://你的ip或者域名:端口号/tbot/v1/"))
        self.assertTrue(is_placeholder_websocket_url("ws://Your_IP:8000/tbot/v1/"))
        self.assertTrue(is_placeholder_websocket_url("ws://You_IP:8000/tbot/v1/"))
        self.assertFalse(is_placeholder_websocket_url("ws://192.168.100.154:8000/tbot/v1/"))

    def test_ota_post_api_url_points_at_v1_backend(self):
        """Firmware appends /device/config + /claim/confirm, so the advertised
        api_url must carry the Nest /v1 prefix (P5 ownership / config)."""
        handler = _full_handler()

        response = asyncio.run(handler.handle_post(_FakeOtaRequest()))
        payload = json.loads(response.text)

        self.assertIn("api_url", payload)
        self.assertTrue(
            payload["api_url"].endswith("/v1"),
            f"api_url must end with /v1, got {payload['api_url']!r}",
        )
        self.assertEqual(payload["api_url"], "https://tbot-backend-8wmh.onrender.com/v1")


class OTAClaimResetAllowlistTest(unittest.TestCase):
    DEVICE = "14:c1:9f:d1:ac:20"
    NON_TARGET = "14:c1:9f:d1:ac:21"
    NONCE = "repair-2026-06-22-tbot-14c19fd1ac20"

    def test_claim_reset_is_emitted_only_for_allowlisted_device_with_nonce(self):
        handler = _full_handler(
            {
                "claim_reset_devices": [self.DEVICE],
                "claim_reset_nonce": self.NONCE,
            }
        )

        response = asyncio.run(handler.handle_post(_FakeOtaRequest(self.DEVICE)))
        payload = json.loads(response.text)

        self.assertEqual(
            payload["claim_reset"],
            {"local_claim": 1, "nonce": self.NONCE},
        )
        self.assertNotIn("claim_reset", payload["websocket"])

    def test_claim_reset_accepts_env_allowlist_and_normalizes_mac_case(self):
        old_devices = os.environ.get("TBOT_CLAIM_RESET_DEVICES")
        old_nonce = os.environ.get("TBOT_CLAIM_RESET_NONCE")
        try:
            os.environ["TBOT_CLAIM_RESET_DEVICES"] = self.DEVICE.upper()
            os.environ["TBOT_CLAIM_RESET_NONCE"] = self.NONCE
            handler = _full_handler()

            response = asyncio.run(handler.handle_post(_FakeOtaRequest(self.DEVICE)))
            payload = json.loads(response.text)

            self.assertEqual(payload["claim_reset"]["local_claim"], 1)
            self.assertEqual(payload["claim_reset"]["nonce"], self.NONCE)
        finally:
            if old_devices is None:
                os.environ.pop("TBOT_CLAIM_RESET_DEVICES", None)
            else:
                os.environ["TBOT_CLAIM_RESET_DEVICES"] = old_devices
            if old_nonce is None:
                os.environ.pop("TBOT_CLAIM_RESET_NONCE", None)
            else:
                os.environ["TBOT_CLAIM_RESET_NONCE"] = old_nonce

    def test_claim_reset_is_omitted_for_non_allowlisted_device(self):
        handler = _full_handler(
            {
                "claim_reset_devices": [self.DEVICE],
                "claim_reset_nonce": self.NONCE,
            }
        )

        response = asyncio.run(handler.handle_post(_FakeOtaRequest(self.NON_TARGET)))
        payload = json.loads(response.text)

        self.assertNotIn("claim_reset", payload)

    def test_claim_reset_is_omitted_without_nonce_even_when_device_allowlisted(self):
        handler = _full_handler({"claim_reset_devices": [self.DEVICE]})

        response = asyncio.run(handler.handle_post(_FakeOtaRequest(self.DEVICE)))
        payload = json.loads(response.text)

        self.assertNotIn("claim_reset", payload)


class OTAMqttForkGuardTest(unittest.TestCase):
    """ENDPOINT RULE: the websocket{} block must be present whenever
    mqtt_gateway is unset (the current deployment), and must be REPLACED by
    mqtt{} (no websocket{}) when mqtt_gateway is configured. A future
    mqtt_gateway misconfig must not silently break the websocket endpoint."""

    def test_websocket_block_present_when_mqtt_gateway_unset(self):
        handler = _full_handler()  # no mqtt_gateway key -> unset

        response = asyncio.run(handler.handle_post(_FakeOtaRequest()))
        payload = json.loads(response.text)

        self.assertIn("websocket", payload)
        self.assertNotIn("mqtt", payload)
        self.assertEqual(payload["websocket"]["url"], "wss://ws.example.com/tbot/v1/")
        self.assertIn("token", payload["websocket"])

    def test_websocket_block_present_when_mqtt_gateway_explicit_null(self):
        # config.yaml ships `mqtt_gateway: null`; an explicit null must behave
        # identically to "unset" and still emit the websocket{} block.
        handler = _full_handler({"mqtt_gateway": None})

        response = asyncio.run(handler.handle_post(_FakeOtaRequest()))
        payload = json.loads(response.text)

        self.assertIn("websocket", payload)
        self.assertNotIn("mqtt", payload)

    def test_mqtt_block_replaces_websocket_when_gateway_configured(self):
        handler = _full_handler(
            {"mqtt_gateway": "mqtt.example.com:1883", "mqtt_signature_key": "k"}
        )

        response = asyncio.run(handler.handle_post(_FakeOtaRequest()))
        payload = json.loads(response.text)

        # When MQTT is the transport, websocket{} is intentionally omitted.
        self.assertIn("mqtt", payload)
        self.assertNotIn("websocket", payload)


class OTAWebsocketTokenMintTest(unittest.TestCase):
    """P5 (GATED): when server.auth.enabled is true the OTA handler mints a
    real NON-EMPTY websocket.token (for non-whitelisted devices); when auth is
    explicitly disabled the token is empty. Whitelisted devices bypass the
    token to stay in lockstep with WebSocketServer._handle_auth."""

    DEVICE = "AA:BB:CC:DD:EE:01"

    def test_token_empty_when_auth_disabled(self):
        handler = _full_handler({"auth": {"enabled": False}})

        response = asyncio.run(handler.handle_post(_FakeOtaRequest(self.DEVICE)))
        payload = json.loads(response.text)

        self.assertIn("websocket", payload)
        self.assertEqual(payload["websocket"]["token"], "")

    def test_token_non_empty_when_auth_enabled_for_unlisted_device(self):
        handler = _full_handler({"auth": {"enabled": True}})

        response = asyncio.run(handler.handle_post(_FakeOtaRequest(self.DEVICE)))
        payload = json.loads(response.text)

        self.assertIn("websocket", payload)
        token = payload["websocket"]["token"]
        self.assertTrue(token, "token must be non-empty when auth is enabled")
        # Sanity: the minted token is verifiable by the same AuthManager the WS
        # handshake uses (signature.timestamp shape, round-trips true).
        self.assertTrue(
            handler.auth.verify_token(
                token, client_id="unit-test-client", username=self.DEVICE
            )
        )

    def test_token_empty_when_auth_enabled_but_device_whitelisted(self):
        handler = _full_handler(
            {"auth": {"enabled": True, "allowed_devices": [self.DEVICE]}}
        )

        response = asyncio.run(handler.handle_post(_FakeOtaRequest(self.DEVICE)))
        payload = json.loads(response.text)

        # Whitelisted device bypasses token verification on the WS side, so OTA
        # advertises no token for it.
        self.assertEqual(payload["websocket"]["token"], "")

    def test_token_non_empty_for_unlisted_device_when_whitelist_present(self):
        handler = _full_handler(
            {"auth": {"enabled": True, "allowed_devices": ["FF:FF:FF:FF:FF:FF"]}}
        )

        response = asyncio.run(handler.handle_post(_FakeOtaRequest(self.DEVICE)))
        payload = json.loads(response.text)

        self.assertTrue(payload["websocket"]["token"])

    def test_mint_helper_gating_matrix(self):
        # auth disabled -> empty regardless of whitelist
        h_off = _full_handler({"auth": {"enabled": False}})
        self.assertEqual(h_off._mint_websocket_token("c", self.DEVICE), "")

        # auth enabled, no whitelist -> non-empty
        h_on = _full_handler({"auth": {"enabled": True}})
        self.assertTrue(h_on._mint_websocket_token("c", self.DEVICE))

        # auth enabled, device whitelisted -> empty (bypass)
        h_wl = _full_handler(
            {"auth": {"enabled": True, "allowed_devices": [self.DEVICE]}}
        )
        self.assertEqual(h_wl._mint_websocket_token("c", self.DEVICE), "")
