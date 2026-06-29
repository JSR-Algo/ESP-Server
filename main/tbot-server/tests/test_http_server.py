import asyncio

import pytest

from core import http_server as http_module
from core.http_server import SimpleHttpServer


class _Logger:
    def __init__(self):
        self.messages = []

    def bind(self, **_kwargs):
        return self

    def error(self, message):
        self.messages.append(("error", message))


def _config(**server_overrides):
    server = {
        "auth_key": "test-key",
        "websocket": "ws://Your_IP:port/tbot/v1/",
        "ip": "127.0.0.1",
        "http_port": 8003,
        "port": 8000,
    }
    server.update(server_overrides)
    return {"server": server}


def test_http_server_websocket_url_preserves_explicit_and_falls_back_for_placeholder():
    server = SimpleHttpServer(_config())

    assert server._get_websocket_url("10.0.0.2", 9000) == "ws://10.0.0.2:9000/tbot/v1/"

    server = SimpleHttpServer(_config(websocket="wss://public.example.com/tbot/v1/"))
    assert server._get_websocket_url("10.0.0.2", 9000) == "wss://public.example.com/tbot/v1/"


@pytest.mark.asyncio
async def test_http_server_start_registers_routes_and_starts_site(monkeypatch):
    started = []
    runner_apps = []

    class _Runner:
        def __init__(self, app):
            self.app = app
            runner_apps.append(app)

        async def setup(self):
            return None

    class _Site:
        def __init__(self, runner, host, port):
            self.runner = runner
            self.host = host
            self.port = port

        async def start(self):
            started.append((self.host, self.port))

    async def _sleep(_seconds):
        raise asyncio.CancelledError()

    monkeypatch.setattr(http_module.web, "AppRunner", _Runner)
    monkeypatch.setattr(http_module.web, "TCPSite", _Site)
    monkeypatch.setattr(http_module.asyncio, "sleep", _sleep)
    server = SimpleHttpServer(_config())

    with pytest.raises(asyncio.CancelledError):
        await server.start()

    route_paths = {route.resource.canonical for route in runner_apps[0].router.routes()}
    assert started == [("127.0.0.1", 8003)]
    assert "/tbot/ota/" in route_paths
    assert "/tbot/ota/download/{filename}" in route_paths
    assert "/mcp/vision/explain" in route_paths
    assert "/internal/devices/{deviceId}/lesson-nudge" in route_paths
    assert "/internal/devices/{deviceId}/lesson-child-response" in route_paths
    assert "/internal/devices/{deviceId}/mcp-call" in route_paths
    assert "/tbot/lesson-assets/{cacheToken}/{assetKey}" in route_paths
    assert "/tbot/assign/" in route_paths


@pytest.mark.asyncio
async def test_http_server_start_noops_when_http_port_is_zero():
    server = SimpleHttpServer(_config(http_port=0))

    assert await server.start() is None


@pytest.mark.asyncio
async def test_http_server_start_logs_and_reraises_start_failure(monkeypatch):
    class _Runner:
        def __init__(self, _app):
            return None

        async def setup(self):
            raise RuntimeError("bind failed")

    monkeypatch.setattr(http_module.web, "AppRunner", _Runner)
    server = SimpleHttpServer(_config())
    server.logger = _Logger()

    with pytest.raises(RuntimeError, match="bind failed"):
        await server.start()

    assert any("HTTP server start failed" in message for _level, message in server.logger.messages)
    assert any("Error stack:" in message for _level, message in server.logger.messages)
