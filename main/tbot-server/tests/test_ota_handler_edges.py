import asyncio
import json
import os
import time
from pathlib import Path

import pytest
from aiohttp import web

from core.api import ota_handler as ota_module
from core.api.ota_handler import (
    OTAHandler,
    _is_higher_version,
    _parse_version,
    _safe_basename,
    is_placeholder_websocket_url,
)


class _Logger:
    def __init__(self):
        self.messages = []

    def bind(self, **_kwargs):
        return self

    def debug(self, message):
        self.messages.append(("debug", message))

    def info(self, message):
        self.messages.append(("info", message))

    def warning(self, message):
        self.messages.append(("warning", message))

    def error(self, message):
        self.messages.append(("error", message))


class _Request:
    method = "POST"

    def __init__(self, *, headers=None, body="", filename=None):
        self.headers = headers or {}
        self._body = body
        self.match_info = {"filename": filename} if filename is not None else {}

    async def text(self):
        return self._body


class _Headers(dict):
    pass


def _config(**server_overrides):
    server = {
        "auth_key": "test-secret",
        "auth": {"enabled": False},
        "websocket": "wss://ws.example.com/tbot/v1/",
        "api_url": "",
        "port": 8000,
        "http_port": 8003,
        "timezone_offset": 7,
    }
    server.update(server_overrides)
    return {"server": server, "firmware_cache_ttl": 0}


def _handler(tmp_path, **server_overrides):
    handler = OTAHandler(_config(**server_overrides))
    handler.logger = _Logger()
    handler.bin_dir = str(tmp_path)
    handler._bin_cache["ttl"] = 0
    return handler


def _headers(**overrides):
    headers = {
        "device-id": "AA:BB",
        "client-id": "client-1",
        "device-model": "esp32",
        "device-version": "1.0.0",
    }
    headers.update(overrides)
    return headers


def _payload(response):
    return json.loads(response.text)


def test_ota_helper_edges(tmp_path):
    handler = _handler(tmp_path, websocket="ws://Your_IP:port/tbot/v1/", api_url="ws://your-domain:port")

    assert _safe_basename("../../firmware.bin") == "firmware.bin"
    assert _parse_version("no-version") == (0,)
    assert _is_higher_version("1.2.1", "1.2.0") is True
    assert _is_higher_version("1.2.0", "1.2.1") is False
    assert _is_higher_version("1.2", "1.2.0") is False
    assert is_placeholder_websocket_url("") is True
    assert handler._get_api_url("127.0.0.1", 8003) == ""
    assert handler._get_websocket_url("127.0.0.1", 8000) == "ws://127.0.0.1:8000/tbot/v1/"


def test_refresh_bin_cache_discovers_sorts_skips_and_handles_errors(tmp_path, monkeypatch):
    handler = _handler(tmp_path)
    (tmp_path / "esp32_1.0.0.bin").write_bytes(b"old")
    (tmp_path / "esp32_2.0.0.bin").write_bytes(b"new")
    (tmp_path / "bad-name.bin").write_bytes(b"skip")

    handler._refresh_bin_cache_if_needed()

    assert handler._bin_cache["files_by_model"] == {
        "esp32": [("2.0.0", "esp32_2.0.0.bin"), ("1.0.0", "esp32_1.0.0.bin")]
    }
    updated_at = handler._bin_cache["updated_at"]
    handler._bin_cache["ttl"] = 60
    monkeypatch.setattr(ota_module.time, "time", lambda: updated_at + 1)
    handler._refresh_bin_cache_if_needed()
    assert handler._bin_cache["updated_at"] == updated_at

    monkeypatch.setattr(ota_module.os.path, "isdir", lambda _path: (_ for _ in ()).throw(RuntimeError("disk")))
    handler._bin_cache["ttl"] = 0
    handler._refresh_bin_cache_if_needed()
    assert any("Refresh firmware cache failed" in message for level, message in handler.logger.messages if level == "error")


def test_refresh_bin_cache_creates_missing_directory(tmp_path):
    bin_dir = tmp_path / "missing-bin"
    handler = _handler(bin_dir)

    handler._refresh_bin_cache_if_needed()

    assert bin_dir.is_dir()
    assert handler._bin_cache["files_by_model"] == {}


def test_signature_generation_failure_returns_empty(tmp_path):
    handler = _handler(tmp_path)

    assert handler.generate_password_signature("content", None) == ""


def test_firmware_download_url_prefers_public_vision_origin(tmp_path, monkeypatch):
    handler = _handler(tmp_path)
    monkeypatch.setattr(ota_module, "get_vision_url", lambda _config: "https://public.test/mcp/vision/explain")

    assert handler._get_firmware_download_url("esp32_2.0.0.bin", "127.0.0.1", 8003) == "https://public.test/tbot/ota/download/esp32_2.0.0.bin"

    monkeypatch.setattr(ota_module, "get_vision_url", lambda _config: "")
    assert handler._get_firmware_download_url("esp32_2.0.0.bin", "127.0.0.1", 8003) == "http://127.0.0.1:8003/tbot/ota/download/esp32_2.0.0.bin"

def test_firmware_download_url_can_force_plain_http_for_legacy_ota(tmp_path, monkeypatch):
    handler = _handler(tmp_path, firmware_download_scheme="http")
    monkeypatch.setattr(ota_module, "get_vision_url", lambda _config: "https://public.test/mcp/vision/explain")

    assert handler._get_firmware_download_url("esp32_2.0.0.bin", "127.0.0.1", 8003) == "http://public.test/tbot/ota/download/esp32_2.0.0.bin"


def test_mint_websocket_token_raises_when_signer_returns_empty(tmp_path):
    handler = _handler(tmp_path, auth={"enabled": True})
    handler.auth.generate_token = lambda *_args, **_kwargs: ""

    with pytest.raises(RuntimeError):
        handler._mint_websocket_token("client", "device")


@pytest.mark.asyncio
async def test_ota_marks_only_allowlisted_devices_factory_test_claimed(tmp_path, monkeypatch):
    handler = _handler(
        tmp_path,
        auth={"enabled": True},
        factory_test_claimed_devices=["aa:bb"],
    )
    monkeypatch.setattr(ota_module, "get_local_ip", lambda: "127.0.0.1")

    claimed = await handler.handle_post(_Request(headers=_headers(**{"device-id": "AA:BB", "device-version": "2.2.31"})))
    unclaimed = await handler.handle_post(_Request(headers=_headers(**{"device-id": "CC:DD"})))

    claimed_payload = _payload(claimed)
    unclaimed_payload = _payload(unclaimed)
    assert claimed_payload["websocket"]["factory_test_claimed"] == 1
    assert "factory_test_claimed" not in unclaimed_payload["websocket"]

@pytest.mark.asyncio
async def test_ota_withholds_factory_test_claimed_until_firmware_supports_short_nvs_key(tmp_path, monkeypatch):
    handler = _handler(
        tmp_path,
        auth={"enabled": True},
        factory_test_claimed_devices=["aa:bb"],
    )
    monkeypatch.setattr(ota_module, "get_local_ip", lambda: "127.0.0.1")

    response = await handler.handle_post(_Request(headers=_headers(**{"device-id": "AA:BB", "device-version": "2.2.30"})))

    payload = _payload(response)
    assert payload["websocket"]["token"]
    assert "factory_test_claimed" not in payload["websocket"]


@pytest.mark.asyncio
async def test_ota_factory_test_claimed_requires_non_empty_ws_token(tmp_path, monkeypatch):
    handler = _handler(
        tmp_path,
        auth={"enabled": False},
        factory_test_claimed_devices=["aa:bb"],
    )
    monkeypatch.setattr(ota_module, "get_local_ip", lambda: "127.0.0.1")

    response = await handler.handle_post(_Request(headers=_headers(**{"device-id": "AA:BB"})))

    payload = _payload(response)
    assert payload["websocket"]["token"] == ""
    assert "factory_test_claimed" not in payload["websocket"]


@pytest.mark.asyncio
async def test_ota_global_factory_test_claims_every_device(tmp_path, monkeypatch):
    # factory_test_claimed_all marks a device that is in NO allowlist.
    handler = _handler(
        tmp_path,
        auth={"enabled": True},
        factory_test_claimed_all=True,
    )
    monkeypatch.setattr(ota_module, "get_local_ip", lambda: "127.0.0.1")

    response = await handler.handle_post(
        _Request(headers=_headers(**{"device-id": "CC:DD", "device-version": "2.2.31"}))
    )

    payload = _payload(response)
    assert payload["websocket"]["token"]
    assert payload["websocket"]["factory_test_claimed"] == 1


@pytest.mark.asyncio
async def test_ota_global_factory_test_still_requires_non_empty_token(tmp_path, monkeypatch):
    # Auth off -> empty token -> firmware factory path cannot trigger, so the
    # global flag must NOT advertise factory_test_claimed (would lock the device).
    handler = _handler(
        tmp_path,
        auth={"enabled": False},
        factory_test_claimed_all=True,
    )
    monkeypatch.setattr(ota_module, "get_local_ip", lambda: "127.0.0.1")

    response = await handler.handle_post(
        _Request(headers=_headers(**{"device-id": "CC:DD", "device-version": "2.2.31"}))
    )

    payload = _payload(response)
    assert payload["websocket"]["token"] == ""
    assert "factory_test_claimed" not in payload["websocket"]


@pytest.mark.asyncio
async def test_ota_post_uses_body_fallbacks_and_advertises_new_firmware(tmp_path, monkeypatch):
    handler = _handler(tmp_path)
    (tmp_path / "body-model_2.0.0.bin").write_bytes(b"firmware")
    monkeypatch.setattr(ota_module, "get_local_ip", lambda: "127.0.0.1")
    monkeypatch.setattr(ota_module, "get_vision_url", lambda _config: "")
    body = json.dumps({"board": {"type": "body-model"}, "application": {"version": "1.0.0"}})

    response = await handler.handle_post(_Request(headers=_headers(**{"device-model": "", "device-version": ""}), body=body))

    payload = _payload(response)
    assert payload["firmware"] == {
        "version": "2.0.0",
        "url": "http://127.0.0.1:8003/tbot/ota/download/body-model_2.0.0.bin",
    }


@pytest.mark.asyncio
async def test_ota_post_handles_invalid_json_defaults_and_firmware_check_errors(tmp_path, monkeypatch):
    handler = _handler(tmp_path)
    handler._refresh_bin_cache_if_needed = lambda: (_ for _ in ()).throw(RuntimeError("cache failed"))
    monkeypatch.setattr(ota_module, "get_local_ip", lambda: "127.0.0.1")

    response = await handler.handle_post(_Request(headers=_headers(**{"device-model": "", "device-version": ""}), body="{"))

    payload = _payload(response)
    assert payload["firmware"]["version"] == "0.0.0"
    assert any("Error checking firmware version" in message for level, message in handler.logger.messages if level == "error")


@pytest.mark.asyncio
async def test_ota_post_uses_body_model_field_and_handles_bad_application_shape(tmp_path, monkeypatch):
    handler = _handler(tmp_path)
    handler._refresh_bin_cache_if_needed = lambda: None
    monkeypatch.setattr(ota_module, "get_local_ip", lambda: "127.0.0.1")
    body = json.dumps({"model": "body-model", "application": "bad-shape"})

    response = await handler.handle_post(
        _Request(headers=_headers(**{"device-model": "", "device-version": ""}), body=body)
    )

    payload = _payload(response)
    assert payload["firmware"]["version"] == "0.0.0"


@pytest.mark.asyncio
async def test_ota_post_body_model_fallback_swallows_malformed_payload_object(tmp_path, monkeypatch):
    class _BadPayload:
        def __contains__(self, _key):
            raise RuntimeError("bad contains")

        def get(self, _key, default=None):
            return default

    handler = _handler(tmp_path)
    handler._refresh_bin_cache_if_needed = lambda: None
    original_loads = json.loads
    monkeypatch.setattr(ota_module, "get_local_ip", lambda: "127.0.0.1")
    monkeypatch.setattr(ota_module.json, "loads", lambda _data: _BadPayload())

    response = await handler.handle_post(
        _Request(headers=_headers(**{"device-model": "", "device-version": ""}), body="{}")
    )

    payload = original_loads(response.text)
    assert payload["firmware"]["version"] == "0.0.0"


@pytest.mark.asyncio
@pytest.mark.parametrize("headers", [{"client-id": "client"}, {"device-id": "device"}])
async def test_ota_post_missing_required_headers_returns_request_error(tmp_path, headers):
    handler = _handler(tmp_path)

    response = await handler.handle_post(_Request(headers=headers, body="{}"))

    assert _payload(response) == {"success": False, "message": "request error."}
    assert response.headers["Access-Control-Allow-Origin"] == "*"


@pytest.mark.asyncio
async def test_ota_post_mqtt_edges_missing_signature_and_empty_password(tmp_path, monkeypatch):
    handler = _handler(tmp_path, mqtt_gateway="mqtt.example.com:1883")
    monkeypatch.setattr(ota_module, "get_local_ip", lambda: "127.0.0.1")

    response = await handler.handle_post(_Request(headers=_headers(), body="{}"))
    payload = _payload(response)
    assert payload["mqtt"]["password"] == ""
    assert any("MissingMQTTSignatureKey" in message for level, message in handler.logger.messages if level == "warning")

    handler = _handler(tmp_path, mqtt_gateway="mqtt.example.com:1883", mqtt_signature_key="k")
    handler.generate_password_signature = lambda *_args, **_kwargs: ""
    response = await handler.handle_post(_Request(headers=_headers(), body="{}"))
    assert _payload(response)["mqtt"]["password"] == ""


@pytest.mark.asyncio
async def test_ota_post_mqtt_group_id_and_username_error_fallbacks(tmp_path, monkeypatch):
    class _BadModel:
        def strip(self):
            return self

        def __str__(self):
            return "bad model"

        def replace(self, *_args, **_kwargs):
            raise RuntimeError("bad model")

    headers = _Headers(_headers(**{"device-model": _BadModel()}))
    handler = _handler(tmp_path, mqtt_gateway="mqtt.example.com:1883")
    monkeypatch.setattr(ota_module, "get_local_ip", lambda: "127.0.0.1")
    monkeypatch.setattr(ota_module.base64, "b64encode", lambda _data: (_ for _ in ()).throw(RuntimeError("base64")))

    response = await handler.handle_post(_Request(headers=headers, body="{}"))

    payload = _payload(response)
    assert payload["mqtt"]["client_id"].startswith("GID_bad_model@@@")
    assert payload["mqtt"]["username"] == ""


@pytest.mark.asyncio
async def test_ota_get_success_and_error(tmp_path, monkeypatch):
    handler = _handler(tmp_path, websocket="ws://Your_IP:port/tbot/v1/")
    monkeypatch.setattr(ota_module, "get_local_ip", lambda: "127.0.0.1")

    response = await handler.handle_get(_Request())
    assert response.text == "OTAInterface running normally, sent to devicewebsocketAddress is:ws://127.0.0.1:8000/tbot/v1/"
    assert response.headers["Access-Control-Allow-Origin"] == "*"

    monkeypatch.setattr(ota_module, "get_local_ip", lambda: (_ for _ in ()).throw(RuntimeError("net")))
    response = await handler.handle_get(_Request())
    assert response.text == "OTAInterfaceException"


@pytest.mark.asyncio
async def test_ota_download_rejections_and_success(tmp_path):
    handler = _handler(tmp_path)
    (tmp_path / "esp32_1.0.0.bin").write_bytes(b"firmware")

    missing_name = await handler.handle_download(_Request(headers=_headers(), filename=""))
    assert missing_name.status == 400

    invalid_name = await handler.handle_download(_Request(headers=_headers(), filename="not-safe.txt"))
    assert invalid_name.status == 400

    missing_file = await handler.handle_download(_Request(headers=_headers(), filename="missing.bin"))
    assert missing_file.status == 404

    success = await handler.handle_download(_Request(headers={**_headers(), "user-agent": "fw"}, filename="esp32_1.0.0.bin"))
    assert isinstance(success, web.FileResponse)
    assert success.headers["Access-Control-Allow-Origin"] == "*"


@pytest.mark.asyncio
async def test_ota_download_forbidden_and_unexpected_error_paths(tmp_path, monkeypatch):
    handler = _handler(tmp_path)

    real_realpath = ota_module.os.path.realpath

    def fake_realpath(path):
        if path.endswith("escape.bin"):
            return str(Path(tmp_path).parent / "escape.bin")
        return real_realpath(path)

    monkeypatch.setattr(ota_module.os.path, "realpath", fake_realpath)
    forbidden = await handler.handle_download(_Request(headers=_headers(), filename="escape.bin"))
    assert forbidden.status == 403

    monkeypatch.setattr(ota_module.os.path, "realpath", lambda _path: (_ for _ in ()).throw(RuntimeError("fs")))
    error = await handler.handle_download(_Request(headers=_headers(), filename="esp32.bin"))
    assert error.status == 500
    assert error.text == "download error"


@pytest.mark.asyncio
async def test_ota_download_ignores_cors_header_failures(tmp_path, monkeypatch):
    handler = _handler(tmp_path)
    monkeypatch.setattr(handler, "_add_cors_headers", lambda _resp: (_ for _ in ()).throw(RuntimeError("cors")))

    response = await handler.handle_download(_Request(headers=_headers(), filename="missing.bin"))

    assert response.status == 404
