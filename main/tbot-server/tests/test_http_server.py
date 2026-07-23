import asyncio
import json
import types

import pytest

from core import http_server as http_module
from core.api.lesson_sd_fanout_handler import LessonSdFanoutHandler
from core.http_server import SimpleHttpServer


class _Logger:
    def __init__(self):
        self.messages = []

    def bind(self, **_kwargs):
        return self

    def error(self, message):
        self.messages.append(("error", message))


class _HeaderMapping:
    def __init__(self, value):
        self.value = value

    def get(self, name, default=None):
        return self.value if name == "Client-Id" else default

class _Request:
    def __init__(self, headers=None):
        self.headers = headers or {}


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
async def test_preload_voice_alarm_snapshot_aggregates_connection_alarms():
    alarm = types.SimpleNamespace(
        snapshot=lambda: {
            "tripped": True,
            "thresholdMs": 1200.0,
            "p95DuringPreloadMs": 1800.0,
            "samplesTotal": 7,
            "samplesDuringPreload": 5,
            "preloadActive": False,
            "lastDisabledAt": 123.0,
        }
    )
    server = SimpleHttpServer(
        _config(),
        lesson_connections={
            "device-1": types.SimpleNamespace(lesson_voice_alarm=alarm),
            "device-2": types.SimpleNamespace(lesson_voice_alarm=None),
        },
    )

    response = await server.handle_preload_voice_alarm_snapshot(None)

    assert response.status == 200
    body = response.text
    assert '"connections": 2' in body
    assert '"alarms": 1' in body
    assert '"deviceId": "device-1"' in body
    assert '"tripped": true' in body

@pytest.mark.asyncio
async def test_preload_voice_alarm_reset_resets_each_connection_alarm():
    resets = []
    alarm = types.SimpleNamespace(reset=lambda: resets.append("reset"))
    server = SimpleHttpServer(
        _config(),
        lesson_connections={
            "device-1": types.SimpleNamespace(lesson_voice_alarm=alarm),
            "device-2": types.SimpleNamespace(lesson_voice_alarm=None),
        },
    )

    response = await server.handle_preload_voice_alarm_reset(None)

    assert response.status == 200
    assert resets == ["reset"]
    assert '"reset": 1' in response.text


@pytest.mark.asyncio
async def test_lesson_runtime_metrics_exposes_forwarder_drops_and_alarm_snapshot():
    alarm = types.SimpleNamespace(
        snapshot=lambda: {
            "tripped": True,
            "thresholdMs": 1200.0,
            "p95DuringPreloadMs": 1800.0,
        }
    )
    server = SimpleHttpServer(
        _config(),
        lesson_connections={
            "device-1": types.SimpleNamespace(
                lesson_runtime=types.SimpleNamespace(
                    forwarder=types.SimpleNamespace(dropped_events_total=3),
                ),
                safety_event_forwarder=types.SimpleNamespace(dropped_events_total=2),
                lesson_voice_alarm=alarm,
                client_id="client-1",
            ),
            "device-2": types.SimpleNamespace(
                lesson_runtime=types.SimpleNamespace(
                    forwarder=types.SimpleNamespace(dropped_events_total=5),
                ),
                headers={"client-id": "client-2"},
                lesson_voice_alarm=None,
            ),
            "device-3": types.SimpleNamespace(
                headers=_HeaderMapping("client-3"),
                lesson_voice_alarm=None,
            ),
        },
    )

    response = await server.handle_lesson_runtime_metrics(None)

    assert response.status == 200
    body = json.loads(response.text)
    assert body["connections"] == 3
    assert body["counters"]["forwarder.dropped_events_total"] == 8
    assert body["counters"]["safety_forwarder.dropped_events_total"] == 2
    assert body["alarms"] == 1
    assert body["devices"][0]["deviceId"] == "device-1"
    assert body["devices"][0]["clientId"] == "client-1"
    assert body["devices"][0]["forwarderDroppedEventsTotal"] == 3
    assert body["devices"][0]["safetyForwarderDroppedEventsTotal"] == 2
    assert body["devices"][0]["alarm"]["tripped"] is True
    assert body["devices"][1]["clientId"] == "client-2"
    assert body["devices"][2]["clientId"] == "client-3"

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
    assert "/internal/lesson-runtime/preload-voice-alarm" in route_paths
    assert "/internal/lesson-runtime/preload-voice-alarm/reset" in route_paths
    assert "/internal/lesson-runtime/metrics" in route_paths
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

@pytest.mark.asyncio
async def test_lesson_sd_pending_status_reports_resolved_backend_ids_only(monkeypatch):
    monkeypatch.setenv("TBOT_DEVICE_MINT_SECRET", "mint")
    config = {"server": {"api_url": "http://backend.test/v1"}}
    resolved = {
        "AA:BB:CC:DD:EE:01": "uuid-1",
        "AA:BB:CC:DD:EE:02": None,
    }

    class Index:
        async def resolve_and_upsert(self, conn):
            return resolved.get(conn.device_id)

    connections = {
        "AA:BB:CC:DD:EE:01": types.SimpleNamespace(device_id="AA:BB:CC:DD:EE:01"),
        "AA:BB:CC:DD:EE:02": types.SimpleNamespace(device_id="AA:BB:CC:DD:EE:02"),
    }
    handler = LessonSdFanoutHandler(config, connections, online_index=Index())

    response = await handler.handle_get_pending(_Request(headers={"X-Mint-Secret": "mint"}))
    body = json.loads(response.text)

    assert body["data"]["onlineDeviceIds"] == ["uuid-1"]
    assert "AA:BB:CC:DD:EE:01" not in response.text
    assert "AA:BB:CC:DD:EE:02" not in response.text
