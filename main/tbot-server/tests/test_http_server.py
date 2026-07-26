import asyncio
import json
import re
import types
from pathlib import Path

import pytest
from aiohttp.test_utils import make_mocked_request

from core import http_server as http_module
from core.api.lesson_sd_fanout_handler import LessonSdFanoutHandler
from core.http_server import SimpleHttpServer
from core.lesson.global_generation_status import (
    GlobalGenerationStatus,
    GlobalGenerationStatusError,
)


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
    cleanups = []
    runner_apps = []

    class _Runner:
        def __init__(self, app):
            self.app = app
            runner_apps.append(app)

        async def setup(self):
            return None

        async def cleanup(self):
            cleanups.append(1)

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
    assert cleanups == [1]
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
async def test_generation_status_handler_returns_exact_cached_public_payload():
    payload = {
        "acceptedGeneration": 7,
        "indexChecksum": "a" * 64,
        "materializationState": "ready",
        "connections": {"connected": 1, "current": 1, "retrying": 0, "failed": 0},
        "lastPollAt": "2026-07-25T01:00:00Z",
        "lastMaterializedAt": "2026-07-25T00:59:00Z",
        "lastErrorCode": None,
    }

    class Status:
        async def snapshot(self):
            return payload

    response = await SimpleHttpServer(
        _config(), generation_status=Status()
    ).handle_generation_status(None)

    assert response.status == 200
    assert json.loads(response.text) == payload
    assert response.headers["Cache-Control"] == "public, max-age=5"


@pytest.mark.asyncio
async def test_generation_status_handler_returns_sanitized_unavailable_response():
    class Status:
        async def snapshot(self):
            raise GlobalGenerationStatusError()

    response = await SimpleHttpServer(
        _config(), generation_status=Status()
    ).handle_generation_status(None)

    assert response.status == 503
    assert json.loads(response.text) == {"error": "generation_status_unavailable"}
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.asyncio
async def test_generation_status_handler_fails_closed_for_corrupt_connection_partition():
    class Store:
        async def snapshot(self):
            return {}

    class Sessions:
        async def aggregate(self, _generation):
            return {"connected": 1, "current": 1, "retrying": 1, "failed": 1}

    response = await SimpleHttpServer(
        _config(), generation_status=GlobalGenerationStatus(Store(), Sessions())
    ).handle_generation_status(None)

    assert response.status == 503
    assert json.loads(response.text) == {"error": "generation_status_unavailable"}
    assert response.headers["Cache-Control"] == "no-store"
    assert "current" not in response.text


@pytest.mark.asyncio
async def test_generation_status_route_is_exact_get_and_other_methods_are_405(monkeypatch):
    runner_apps = []

    class Status:
        async def snapshot(self):
            return {}

    class _Runner:
        def __init__(self, app):
            self.app = app
            runner_apps.append(app)

        async def setup(self):
            return None

        async def cleanup(self):
            return None

    class _Site:
        def __init__(self, *_args):
            pass

        async def start(self):
            pass

    async def _sleep(_seconds):
        raise asyncio.CancelledError()

    monkeypatch.setattr(http_module.web, "AppRunner", _Runner)
    monkeypatch.setattr(http_module.web, "TCPSite", _Site)
    monkeypatch.setattr(http_module.asyncio, "sleep", _sleep)

    with pytest.raises(asyncio.CancelledError):
        await SimpleHttpServer(_config(), generation_status=Status()).start()

    app = runner_apps[0]
    routes = [
        route for route in app.router.routes()
        if route.resource.canonical == "/public/lesson-assets/generation"
    ]
    assert {route.method for route in routes} == {"GET", "HEAD"}
    match = await app.router.resolve(
        make_mocked_request("POST", "/public/lesson-assets/generation")
    )
    assert match.http_exception.status == 405


@pytest.mark.asyncio
async def test_background_service_lifecycle_is_idempotent_and_never_calls_run_once(monkeypatch):
    calls = []

    class Poller:
        def start(self):
            calls.append("poller.start")

        async def stop(self):
            calls.append("poller.stop")

        async def run_once(self):
            calls.append("poller.run_once")

    server = SimpleHttpServer(_config(), generation_poller=Poller())
    monkeypatch.delenv("TBOT_ENABLE_BACKGROUND_WORKERS", raising=False)
    monkeypatch.delenv("LESSON_SD_LEGACY_DEVICE_WORKER_ENABLED", raising=False)

    server.start_background_services()
    server.start_background_services()
    await server.stop_background_services()
    await server.stop_background_services()

    assert calls == ["poller.start", "poller.stop"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("workers", "legacy", "expected"),
    [("true", "true", 1), ("true", "false", 0), ("false", "true", 0), (None, None, 0)],
)
async def test_legacy_worker_requires_both_flags(monkeypatch, workers, legacy, expected):
    server = SimpleHttpServer(_config())
    starts = []
    stops = []
    server.lesson_sd_retry_worker = types.SimpleNamespace(
        start=lambda: starts.append(1),
        stop=lambda: _async_append(stops, 1),
    )
    for name, value in (
        ("TBOT_ENABLE_BACKGROUND_WORKERS", workers),
        ("LESSON_SD_LEGACY_DEVICE_WORKER_ENABLED", legacy),
    ):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)

    server.start_background_services()
    await server.stop_background_services()

    assert len(starts) == expected
    assert len(stops) == expected


async def _async_append(target, value):
    target.append(value)


@pytest.mark.asyncio
async def test_stop_closes_only_owned_generation_redis_after_services():
    calls = []

    class Poller:
        def start(self):
            calls.append("start")

        async def stop(self):
            calls.append("stop")

    class Redis:
        async def aclose(self):
            calls.append("close")

    server = SimpleHttpServer(
        _config(), generation_poller=Poller(), generation_redis=Redis(), owns_generation_redis=True
    )
    server.start_background_services()
    await server.stop_background_services()

    assert calls == ["start", "stop", "close"]


@pytest.mark.asyncio
async def test_stop_still_closes_owned_redis_when_poller_stop_fails():
    calls = []
    attempts = 0

    class Poller:
        def start(self):
            calls.append("start")

        async def stop(self):
            nonlocal attempts
            calls.append("stop")
            attempts += 1
            if attempts == 1:
                raise RuntimeError("redis://token@secret")

    class Redis:
        async def aclose(self):
            calls.append("close")

    server = SimpleHttpServer(
        _config(), generation_poller=Poller(), generation_redis=Redis(), owns_generation_redis=True
    )
    server.start_background_services()

    with pytest.raises(RuntimeError, match="generation_background_stop_failed") as raised:
        await server.stop_background_services()

    assert raised.value.__cause__ is None
    assert calls == ["start", "stop", "close"]

    await server.stop_background_services()

    assert calls == ["start", "stop", "close", "stop"]


@pytest.mark.asyncio
async def test_stop_retries_only_redis_after_transient_close_failure():
    calls = []
    close_attempts = 0

    class Poller:
        def start(self):
            calls.append("poller.start")

        async def stop(self):
            calls.append("poller.stop")

    class Redis:
        async def aclose(self):
            nonlocal close_attempts
            close_attempts += 1
            calls.append("redis.close")
            if close_attempts == 1:
                raise RuntimeError("transient secret")

    server = SimpleHttpServer(
        _config(), generation_poller=Poller(), generation_redis=Redis(), owns_generation_redis=True
    )
    server.start_background_services()

    with pytest.raises(RuntimeError, match="generation_background_stop_failed"):
        await server.stop_background_services()
    await server.stop_background_services()

    assert calls == ["poller.start", "poller.stop", "redis.close", "redis.close"]


@pytest.mark.asyncio
async def test_stop_retries_cancelled_redis_cleanup_without_repeating_poller():
    calls = []
    close_attempts = 0

    class Poller:
        def start(self):
            pass

        async def stop(self):
            calls.append("poller.stop")

    class Redis:
        async def aclose(self):
            nonlocal close_attempts
            close_attempts += 1
            calls.append("redis.close")
            if close_attempts == 1:
                raise asyncio.CancelledError()

    server = SimpleHttpServer(
        _config(), generation_poller=Poller(), generation_redis=Redis(), owns_generation_redis=True
    )
    server.start_background_services()

    with pytest.raises(asyncio.CancelledError):
        await server.stop_background_services()
    await server.stop_background_services()

    assert calls == ["poller.stop", "redis.close", "redis.close"]


@pytest.mark.asyncio
async def test_concurrent_stops_clean_each_resource_once():
    calls = []
    release = asyncio.Event()

    class Poller:
        def start(self):
            pass

        async def stop(self):
            calls.append("poller.stop")
            await release.wait()

    class Redis:
        async def aclose(self):
            calls.append("redis.close")

    server = SimpleHttpServer(
        _config(), generation_poller=Poller(), generation_redis=Redis(), owns_generation_redis=True
    )
    server.start_background_services()
    first = asyncio.create_task(server.stop_background_services())
    await asyncio.sleep(0)
    second = asyncio.create_task(server.stop_background_services())
    release.set()

    await asyncio.gather(first, second)

    assert calls == ["poller.stop", "redis.close"]


@pytest.mark.asyncio
async def test_stop_retries_legacy_worker_without_repeating_finished_resources(monkeypatch):
    calls = []
    attempts = 0

    class Poller:
        def start(self):
            pass

        async def stop(self):
            calls.append("poller.stop")

    class Legacy:
        def start(self):
            calls.append("legacy.start")

        async def stop(self):
            nonlocal attempts
            attempts += 1
            calls.append("legacy.stop")
            if attempts == 1:
                raise RuntimeError("transient secret")

    monkeypatch.setenv("TBOT_ENABLE_BACKGROUND_WORKERS", "true")
    monkeypatch.setenv("LESSON_SD_LEGACY_DEVICE_WORKER_ENABLED", "true")
    server = SimpleHttpServer(_config(), generation_poller=Poller())
    server.lesson_sd_retry_worker = Legacy()
    server.start_background_services()

    with pytest.raises(RuntimeError, match="generation_background_stop_failed"):
        await server.stop_background_services()
    await server.stop_background_services()

    assert calls == [
        "legacy.start",
        "poller.stop",
        "legacy.stop",
        "legacy.stop",
    ]


@pytest.mark.asyncio
async def test_start_cancellation_during_setup_cleans_runner_and_owned_redis(monkeypatch):
    calls = []

    class Runner:
        def __init__(self, _app):
            pass

        async def setup(self):
            raise asyncio.CancelledError()

        async def cleanup(self):
            calls.append("runner.cleanup")

    class Redis:
        async def aclose(self):
            calls.append("redis.close")

    monkeypatch.setattr(http_module.web, "AppRunner", Runner)
    server = SimpleHttpServer(
        _config(), generation_redis=Redis(), owns_generation_redis=True
    )

    with pytest.raises(asyncio.CancelledError):
        await server.start()

    assert calls == ["redis.close", "runner.cleanup"]


def test_nginx_public_generation_locations_are_read_only_redacted_proxies():
    nginx = (Path(__file__).parents[3] / "deploy/nginx/tjbot.vn.conf").read_text()

    assert "location = /public/lesson-assets/generation" in nginx
    assert "proxy_pass http://127.0.0.1:8003" in nginx
    assert "location = /v1/public/lesson-assets/latest" in nginx
    assert "proxy_pass http://127.0.0.1:3300" in nginx
    assert "127.0.0.1:3000" not in nginx
    assert nginx.count('if ($request_method !~ ^(GET|HEAD)$) { return 405; }') >= 2
    for header in ("Authorization", "Cookie", "X-Admin-Key", "Cf-Access-Jwt-Assertion", "Cf-Access-Client-Id", "Cf-Access-Client-Secret"):
        assert nginx.count(f'proxy_set_header {header} "";') >= 2
    assert "proxy_hide_header ETag" not in nginx
    assert "proxy_hide_header Cache-Control" not in nginx


def test_nginx_public_generation_reads_do_not_share_cloudflared_origin_rate_limit():
    nginx = (Path(__file__).parents[3] / "deploy/nginx/tjbot.vn.conf").read_text()

    assert "zone=lesson_public_read" not in nginx
    locations = []
    for path in (
        "/public/lesson-assets/generation",
        "/v1/public/lesson-assets/latest",
    ):
        marker = f"location = {path}"
        cursor = 0
        while (location_start := nginx.find(marker, cursor)) != -1:
            block_start = nginx.index("{", location_start)
            depth = 0
            for index in range(block_start, len(nginx)):
                if nginx[index] == "{":
                    depth += 1
                elif nginx[index] == "}":
                    depth -= 1
                    if depth == 0:
                        locations.append(nginx[location_start : index + 1])
                        cursor = index + 1
                        break
            else:
                raise AssertionError(f"unterminated nginx location for {path}")

    assert len(locations) == 4
    for location in locations:
        assert "limit_req " not in location
        assert 'if ($request_method !~ ^(GET|HEAD)$) { return 405; }' in location
        for header in (
            "Authorization",
            "Cookie",
            "X-Admin-Key",
            "X-Admin-Proxy-Key",
            "X-Api-Key",
            "Cf-Access-Jwt-Assertion",
            "Cf-Access-Client-Id",
            "Cf-Access-Client-Secret",
        ):
            assert f'proxy_set_header {header} "";' in location


def _nginx_server_block(nginx: str, server_name: str) -> str:
    marker = f"server_name {server_name};"
    marker_index = nginx.index(marker)
    block_start = nginx.rfind("server {", 0, marker_index)
    depth = 0
    for index in range(block_start, len(nginx)):
        if nginx[index] == "{":
            depth += 1
        elif nginx[index] == "}":
            depth -= 1
            if depth == 0:
                return nginx[block_start : index + 1]
    raise AssertionError(f"unterminated nginx server block for {server_name}")


def test_nginx_sample_assets_are_exposed_only_on_esp_hostname():
    nginx = (Path(__file__).parents[3] / "deploy/nginx/tjbot.vn.conf").read_text()
    admin_server = _nginx_server_block(nginx, "admin.tjbot.vn")
    esp_server = _nginx_server_block(nginx, "esp.tjbot.vn")

    assert "/lesson-sample-assets" not in admin_server
    assert "location ~ ^/lesson-sample-assets/" in esp_server


def test_nginx_sample_asset_location_flattens_safe_authored_paths_to_fixed_directory():
    nginx = (Path(__file__).parents[3] / "deploy/nginx/tjbot.vn.conf").read_text()
    esp_server = _nginx_server_block(nginx, "esp.tjbot.vn")

    assert "location = /lesson-sample-assets" in esp_server
    location_pattern = (
        r"^/lesson-sample-assets/(?:[A-Za-z0-9][A-Za-z0-9._-]*/)*"
        r"(?<sample_asset_basename>[A-Za-z0-9][A-Za-z0-9._-]*)$"
    )
    assert f"location ~ {location_pattern}" in esp_server
    assert "alias /var/www/tbot-sample-assets/$sample_asset_basename;" in esp_server
    assert "autoindex off;" in esp_server
    assert "location /lesson-sample-assets/" not in esp_server

    python_pattern = re.compile(location_pattern.replace("(?<", "(?P<"))
    expected_paths = {
        "/lesson-sample-assets/assets/background/barn-round-field-poster.jpg": "barn-round-field-poster.jpg",
        "/lesson-sample-assets/assets/robot/poses/bright-teach.png": "bright-teach.png",
        "/lesson-sample-assets/barn.png": "barn.png",
    }
    for path, basename in expected_paths.items():
        match = python_pattern.fullmatch(path)
        assert match is not None
        assert match.group("sample_asset_basename") == basename
    for unsafe_path in (
        "/lesson-sample-assets/",
        "/lesson-sample-assets/assets//barn.png",
        "/lesson-sample-assets/../barn.png",
        "/lesson-sample-assets/assets/%2Fetc%2Fpasswd",
    ):
        assert python_pattern.fullmatch(unsafe_path) is None


def test_nginx_sample_asset_location_is_read_only_and_immutable():
    nginx = (Path(__file__).parents[3] / "deploy/nginx/tjbot.vn.conf").read_text()
    esp_server = _nginx_server_block(nginx, "esp.tjbot.vn")
    location_start = esp_server.index("location ~ ^/lesson-sample-assets/")
    location = esp_server[location_start : esp_server.index("\n    }", location_start)]

    assert 'if ($request_method !~ ^(GET|HEAD)$) { return 405; }' in location
    assert r'if ($request_uri ~* "^/lesson-sample-assets/[^?]*(?:%2f|%5c|%2e|\\\\)") { return 404; }' in location
    assert r'if ($request_uri ~ "^/lesson-sample-assets/(?:[^/?]+/)*\.\.(?:/|\?|$)") { return 404; }' in location
    assert "disable_symlinks on from=/var/www/tbot-sample-assets;" in location
    assert 'add_header Cache-Control "public, max-age=31536000, immutable";' in location


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
    assert not any("bind failed" in message for _level, message in server.logger.messages)
    assert not any("Error stack:" in message for _level, message in server.logger.messages)


def test_build_servers_disabled_preserves_legacy_constructor_shape(monkeypatch):
    import app

    monkeypatch.delenv("LESSON_GENERATION_CMS_URL", raising=False)
    captures = {}

    class WS:
        def __init__(self, config, *, lesson_sd_online_index=None):
            self.lesson_connections = {}
            captures["ws_index"] = lesson_sd_online_index

    class HTTP:
        def __init__(self, config, connections, *, lesson_sd_online_index=None):
            captures["http_index"] = lesson_sd_online_index
            captures["connections"] = connections

    ws, http = app._build_servers(
        {"server": {"api_url": "http://backend.test"}},
        websocket_server_factory=WS,
        http_server_factory=HTTP,
    )

    assert ws is not None and http is not None
    assert captures["ws_index"] is captures["http_index"]
    assert captures["connections"] is ws.lesson_connections


@pytest.mark.asyncio
async def test_build_servers_enabled_shares_global_stack_and_adapts_positional_fanout(monkeypatch):
    import app

    captures = {}

    class Redis:
        pass

    redis = Redis()

    class Store:
        def __init__(self, dependency):
            assert dependency is redis
            self.redis = dependency
            captures["store"] = self

    class Sessions:
        def __init__(self, dependency):
            assert dependency is redis
            captures["sessions"] = self

        async def fanout(self, *, generation, index_checksum, packs):
            captures["fanout"] = (generation, index_checksum, packs)
            return {"state": "ready"}

    class Sync:
        def __init__(self, config, store, fanout):
            assert store is captures["store"]
            self.fanout = fanout
            captures["sync"] = self

        async def apply(self, _payload):
            return await self.fanout(9, "a" * 64, [{"cacheKey": "lesson/v1-checksum"}])

    class Poller:
        def __init__(self, config, store, callback):
            assert store is captures["store"]
            self.callback = callback
            captures["poller"] = self

        async def run_once(self):
            return await self.callback({"generation": 9})

    class Status:
        def __init__(self, store, sessions):
            assert store is captures["store"]
            assert sessions is captures["sessions"]
            captures["status"] = self

    class WS:
        def __init__(self, config, *, lesson_sd_online_index=None, global_generation_sessions=None):
            assert global_generation_sessions is captures["sessions"]
            self.lesson_connections = {}

    class HTTP:
        def __init__(self, config, connections, **kwargs):
            assert kwargs["generation_poller"] is captures["poller"]
            assert kwargs["generation_status"] is captures["status"]
            assert kwargs["generation_redis"] is redis
            assert kwargs["owns_generation_redis"] is True

    config = {
        "server": {"api_url": "http://backend.test"},
        "lesson": {
            "generation_cms_url": "https://cms.example/v1/public/lesson-assets/latest",
            "asset_allowed_origins": "https://cdn.example",
        },
    }
    monkeypatch.setenv("REDIS_URL", "redis://redis.example/0")
    monkeypatch.delenv("LESSON_GENERATION_CMS_URL", raising=False)

    await app._build_servers_async(
        config,
        websocket_server_factory=WS,
        http_server_factory=HTTP,
        redis_factory=lambda _url, **_kwargs: redis,
        store_factory=Store,
        sessions_factory=Sessions,
        sync_factory=Sync,
        poller_factory=Poller,
        status_factory=Status,
    )
    result = await captures["poller"].run_once()

    assert result == {"state": "ready"}
    assert captures["fanout"] == (
        9,
        "a" * 64,
        [{"cacheKey": "lesson/v1-checksum"}],
    )


@pytest.mark.asyncio
async def test_build_servers_enabled_rejects_missing_redis(monkeypatch):
    import app

    monkeypatch.setenv(
        "LESSON_GENERATION_CMS_URL",
        "https://cms.example/v1/public/lesson-assets/latest",
    )
    monkeypatch.delenv("REDIS_URL", raising=False)

    with pytest.raises(RuntimeError, match="REDIS_URL"):
        await app._build_servers_async(
            {
                "server": {},
                "lesson": {"asset_allowed_origins": "https://cdn.example"},
            }
        )


@pytest.mark.asyncio
async def test_enabled_build_closes_redis_when_poller_validation_fails(monkeypatch):
    import app

    calls = []

    class Redis:
        async def aclose(self):
            calls.append("redis.close")

    monkeypatch.setenv("REDIS_URL", "redis://redis.example/0")

    with pytest.raises(ValueError, match="LESSON_ASSET_ALLOWED_ORIGINS"):
        await app._build_servers_async(
            {
                "server": {},
                "lesson": {
                    "generation_cms_url": "https://cms.example/v1/public/lesson-assets/latest"
                },
            },
            redis_factory=lambda *_args, **_kwargs: Redis(),
        )

    assert calls == ["redis.close"]


@pytest.mark.asyncio
async def test_enabled_build_cancellation_cleans_constructed_poller_and_redis(monkeypatch):
    import app

    calls = []

    class Redis:
        async def aclose(self):
            calls.append("redis.close")

    class Store:
        def __init__(self, redis):
            self.redis = redis

    class Sessions:
        def __init__(self, _redis):
            pass

        async def fanout(self, **_kwargs):
            return {}

    class Sync:
        def __init__(self, _config, _store, _fanout):
            pass

        async def apply(self, _payload):
            return {}

    class Poller:
        def __init__(self, _config, _store, _callback):
            pass

        async def stop(self):
            calls.append("poller.stop")

    class Status:
        def __init__(self, _store, _sessions):
            raise asyncio.CancelledError()

    monkeypatch.setenv("REDIS_URL", "redis://redis.example/0")

    with pytest.raises(asyncio.CancelledError):
        await app._build_servers_async(
            {
                "server": {},
                "lesson": {
                    "generation_cms_url": "https://cms.example/v1/public/lesson-assets/latest",
                    "asset_allowed_origins": "https://cdn.example",
                },
            },
            redis_factory=lambda *_args, **_kwargs: Redis(),
            store_factory=Store,
            sessions_factory=Sessions,
            sync_factory=Sync,
            poller_factory=Poller,
            status_factory=Status,
        )

    assert calls == ["poller.stop", "redis.close"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_stage", ["store", "sessions", "sync", "poller", "status", "ws", "http"])
async def test_enabled_build_cleans_partial_resources_once(monkeypatch, failure_stage):
    import app

    calls = []

    class Redis:
        async def aclose(self):
            calls.append("redis.close")

    redis = Redis()

    class Store:
        def __init__(self, dependency):
            if failure_stage == "store":
                raise RuntimeError("store failed")
            self.redis = dependency

    class Sessions:
        def __init__(self, _dependency):
            if failure_stage == "sessions":
                raise RuntimeError("sessions failed")

        async def fanout(self, **_kwargs):
            return {}

    class Sync:
        def __init__(self, _config, _store, _fanout):
            if failure_stage == "sync":
                raise RuntimeError("sync failed")

        async def apply(self, _payload):
            return {}

    class Poller:
        def __init__(self, _config, _store, _callback):
            if failure_stage == "poller":
                raise RuntimeError("poller failed")

        async def stop(self):
            calls.append("poller.stop")

    class Status:
        def __init__(self, _store, _sessions):
            if failure_stage == "status":
                raise RuntimeError("status failed")

    class WS:
        def __init__(self, *_args, **_kwargs):
            if failure_stage == "ws":
                raise RuntimeError("ws failed")
            self.lesson_connections = {}

    class HTTP:
        def __init__(self, *_args, **_kwargs):
            if failure_stage == "http":
                raise RuntimeError("http failed")

    monkeypatch.setenv("REDIS_URL", "redis://redis.example/0")

    with pytest.raises(RuntimeError, match=f"{failure_stage} failed"):
        await app._build_servers_async(
            {
                "server": {},
                "lesson": {
                    "generation_cms_url": "https://cms.example/v1/public/lesson-assets/latest",
                    "asset_allowed_origins": "https://cdn.example",
                },
            },
            websocket_server_factory=WS,
            http_server_factory=HTTP,
            redis_factory=lambda *_args, **_kwargs: redis,
            store_factory=Store,
            sessions_factory=Sessions,
            sync_factory=Sync,
            poller_factory=Poller,
            status_factory=Status,
        )

    assert calls.count("redis.close") == 1
    assert calls.count("poller.stop") == (1 if failure_stage in {"status", "ws", "http"} else 0)

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
