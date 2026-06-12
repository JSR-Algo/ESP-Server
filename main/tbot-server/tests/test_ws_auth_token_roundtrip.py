"""P5 (GATED) — WS auth token mint/verify round-trip.

server.auth.enabled ships FALSE; this suite proves that WHEN it is turned on:
  - the OTA handler mints a real, non-empty websocket.token, and
  - WebSocketServer._handle_auth ACCEPTS that token and REJECTS missing/invalid
    Authorization, for a non-whitelisted device.

Enabling auth on the live public tunnel remains a separate activation step
(lockout risk) — these tests only verify the code path is correct when enabled,
they do NOT change the shipped default.
"""

import asyncio
import sys
import unittest
from types import SimpleNamespace

# Mirror the defensive re-import guard used by test_ota_websocket_url.py so a
# previously-stubbed core.auth module does not leak in under a full-suite run.
for module_name, required_attr in (
    ("core.auth", "AuthManager"),
    ("core.utils.util", "get_local_ip"),
):
    if module_name in sys.modules and not hasattr(
        sys.modules[module_name], required_attr
    ):
        del sys.modules[module_name]

import core.websocket_server as websocket_server
from core.api.ota_handler import OTAHandler
from core.auth import AuthenticationError

DEVICE = "AA:BB:CC:DD:EE:01"
CLIENT = "unit-test-client"
SECRET = "test-secret"


class _DummyLogger:
    def bind(self, **kwargs):
        return self

    def warning(self, *args, **kwargs):
        return None

    def error(self, *args, **kwargs):
        return None

    def info(self, *args, **kwargs):
        return None


def _build_ws_server(auth_config):
    """Construct a WebSocketServer with module init + logging stubbed out."""
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    original_initialize_modules = websocket_server.initialize_modules
    original_setup_logging = websocket_server.setup_logging
    try:
        websocket_server.initialize_modules = lambda *a, **k: {}
        websocket_server.setup_logging = lambda: _DummyLogger()
        config = {
            "read_config_from_api": True,
            "selected_module": {},
            "server": {"auth_key": SECRET, "auth": auth_config},
        }
        return websocket_server.WebSocketServer(config)
    finally:
        websocket_server.initialize_modules = original_initialize_modules
        websocket_server.setup_logging = original_setup_logging


def _mint_ota_token(auth_config):
    handler = OTAHandler(
        {
            "server": {
                "auth_key": SECRET,
                "auth": auth_config,
                "websocket": "wss://ws.example.com/tbot/v1/",
                "port": 8000,
                "http_port": 8003,
            },
            "firmware_cache_ttl": 30,
        }
    )
    return handler._mint_websocket_token(CLIENT, DEVICE)


def _fake_ws(headers):
    return SimpleNamespace(request=SimpleNamespace(path="/tbot/v1/", headers=headers))


class WsAuthTokenRoundTripTest(unittest.TestCase):
    def test_enabled_auth_accepts_ota_minted_token(self):
        auth_config = {"enabled": True}
        token = _mint_ota_token(auth_config)
        self.assertTrue(token, "OTA must mint a non-empty token when auth enabled")

        server = _build_ws_server(auth_config)
        ws = _fake_ws(
            {
                "device-id": DEVICE,
                "client-id": CLIENT,
                "authorization": f"Bearer {token}",
            }
        )
        # Must not raise.
        asyncio.run(server._handle_auth(ws))

    def test_enabled_auth_rejects_missing_authorization(self):
        server = _build_ws_server({"enabled": True})
        ws = _fake_ws({"device-id": DEVICE, "client-id": CLIENT})
        with self.assertRaises(AuthenticationError):
            asyncio.run(server._handle_auth(ws))

    def test_enabled_auth_rejects_invalid_token(self):
        server = _build_ws_server({"enabled": True})
        ws = _fake_ws(
            {
                "device-id": DEVICE,
                "client-id": CLIENT,
                "authorization": "Bearer not-a-valid-token.123",
            }
        )
        with self.assertRaises(AuthenticationError):
            asyncio.run(server._handle_auth(ws))

    def test_enabled_auth_rejects_token_for_wrong_device(self):
        """A token minted for DEVICE must not authenticate a different device."""
        token = _mint_ota_token({"enabled": True})
        server = _build_ws_server({"enabled": True})
        ws = _fake_ws(
            {
                "device-id": "11:22:33:44:55:66",
                "client-id": CLIENT,
                "authorization": f"Bearer {token}",
            }
        )
        with self.assertRaises(AuthenticationError):
            asyncio.run(server._handle_auth(ws))

    def test_disabled_auth_bypasses_verification(self):
        """Shipped default: auth disabled -> _handle_auth is a no-op even with
        no Authorization header (matches the empty token OTA advertises)."""
        server = _build_ws_server({"enabled": False})
        ws = _fake_ws({"device-id": DEVICE, "client-id": CLIENT})
        # Must not raise despite missing Authorization.
        asyncio.run(server._handle_auth(ws))

    def test_whitelisted_device_bypasses_token_when_auth_enabled(self):
        server = _build_ws_server({"enabled": True, "allowed_devices": [DEVICE]})
        ws = _fake_ws({"device-id": DEVICE, "client-id": CLIENT})
        # Whitelisted device: accepted without any token.
        asyncio.run(server._handle_auth(ws))


if __name__ == "__main__":
    unittest.main()
