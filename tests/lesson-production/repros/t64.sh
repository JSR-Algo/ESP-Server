#!/usr/bin/env bash
# repo: $TBOT_REPRO_REPO_ROOT
#
# T6.4 — the ESP lesson HTTP surface had four routes that skipped the
# X-Mint-Secret gate every other /internal/ route already enforced, plus a
# stored-XSS sink in the operator console.
#
#   1. GET  /internal/lesson-runtime/metrics              answered 200 anonymously
#   2. GET  /internal/lesson-runtime/preload-voice-alarm  answered 200 anonymously
#   3. POST /internal/lesson-runtime/preload-voice-alarm/reset answered 200
#      anonymously AND actually cleared the latch — the preload voice alarm is
#      the guard that auto-disables LESSON_RUNTIME_ENABLED when voice p95 during
#      preload regresses, so this is anonymous mutation of a safety control.
#      deploy/docker-compose.prod.yml publishes 8003 on the host with no
#      loopback bind, so these are reachable off-box.
#   4. GET  /tbot/assign/ embedded the connected-robot inventory (each live
#      robot's MAC paired with its backend device UUID) into the page for
#      anonymous callers; nginx proxies /tbot/ with no auth. The same inventory
#      is interpolated into a <script> block with json.dumps, which does not
#      escape '<' or '/', and the registry is keyed by the device-supplied
#      device-id websocket header — so a robot could choose an id containing
#      </script> and inject markup into the operator console.
#
# This repro drives only the shipping handler surface (no helper the fix
# introduced), so it tests the BUG, not the patch.
set -euo pipefail

cd "$(pwd)/main/tbot-server"

cat > tests/__t64_repro.py <<'PY'
"""T6.4 repro — the ESP lesson HTTP surface authenticates and encodes output."""

import json
import os
import types

import pytest
from aiohttp.test_utils import make_mocked_request

from core.api.lesson_assignment_console_handler import LessonAssignmentConsoleHandler
from core.http_server import SimpleHttpServer

SECRET = "t64-repro-mint-secret"
CONFIG = {
    "server": {
        "auth_key": "test-key",
        "websocket": "ws://127.0.0.1:8000/tbot/v1/",
        "ip": "127.0.0.1",
        "http_port": 8003,
        "port": 8000,
        "api_url": "https://backend.test/v1",
    }
}


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setenv("TBOT_DEVICE_MINT_SECRET", SECRET)


def _server(alarm=None):
    connection = types.SimpleNamespace(
        lesson_voice_alarm=alarm,
        lesson_runtime=types.SimpleNamespace(
            forwarder=types.SimpleNamespace(dropped_events_total=1)
        ),
        client_id="client-1",
    )
    return SimpleHttpServer(CONFIG, lesson_connections={"device-1": connection})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "handler,method,path",
    [
        ("handle_lesson_runtime_metrics", "GET", "/internal/lesson-runtime/metrics"),
        ("handle_preload_voice_alarm_snapshot", "GET", "/internal/lesson-runtime/preload-voice-alarm"),
        ("handle_preload_voice_alarm_reset", "POST", "/internal/lesson-runtime/preload-voice-alarm/reset"),
    ],
)
async def test_internal_lesson_runtime_routes_reject_anonymous(handler, method, path):
    alarm = types.SimpleNamespace(reset=lambda: None, snapshot=lambda: {"tripped": True})
    response = await getattr(_server(alarm), handler)(make_mocked_request(method, path))
    assert response.status == 401


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "handler,method,path",
    [
        ("handle_lesson_runtime_metrics", "GET", "/internal/lesson-runtime/metrics"),
        ("handle_preload_voice_alarm_snapshot", "GET", "/internal/lesson-runtime/preload-voice-alarm"),
        ("handle_preload_voice_alarm_reset", "POST", "/internal/lesson-runtime/preload-voice-alarm/reset"),
    ],
)
async def test_internal_lesson_runtime_routes_reject_a_wrong_secret(handler, method, path):
    alarm = types.SimpleNamespace(reset=lambda: None, snapshot=lambda: {"tripped": True})
    request = make_mocked_request(method, path, headers={"X-Mint-Secret": "wrong"})
    response = await getattr(_server(alarm), handler)(request)
    assert response.status == 401


@pytest.mark.asyncio
async def test_anonymous_reset_does_not_clear_the_voice_alarm_latch():
    resets = []
    alarm = types.SimpleNamespace(reset=lambda: resets.append("reset"))
    response = await _server(alarm).handle_preload_voice_alarm_reset(
        make_mocked_request("POST", "/internal/lesson-runtime/preload-voice-alarm/reset")
    )
    assert response.status == 401
    assert resets == []


@pytest.mark.asyncio
async def test_authenticated_caller_still_reaches_the_runtime_routes():
    alarm = types.SimpleNamespace(reset=lambda: None, snapshot=lambda: {"tripped": True})
    request = make_mocked_request(
        "GET", "/internal/lesson-runtime/metrics", headers={"X-Mint-Secret": SECRET}
    )
    response = await _server(alarm).handle_lesson_runtime_metrics(request)
    assert response.status == 200


@pytest.mark.asyncio
async def test_console_withholds_the_fleet_inventory_from_anonymous_callers():
    from config import device_token_client

    mac = "14:c1:9f:d1:ac:20"
    uuid = "22222222-2222-4222-8222-222222222222"
    saved = dict(device_token_client._cache)
    device_token_client._cache.clear()
    try:
        import time

        device_token_client._cache[mac] = (uuid, "jwt", time.time())
        handler = LessonAssignmentConsoleHandler(CONFIG, {mac: object()})
        body = (
            await handler.handle_get(make_mocked_request("GET", "/tbot/assign/"))
        ).text
        assert "TBOT Lesson Assignment" in body  # page itself still served
        assert mac not in body
        assert uuid not in body
    finally:
        device_token_client._cache.clear()
        device_token_client._cache.update(saved)


@pytest.mark.asyncio
async def test_console_cannot_be_xss_ed_through_a_hostile_device_id():
    hostile = "</script><script>window.__t64=1</script>"
    handler = LessonAssignmentConsoleHandler(CONFIG, {hostile: object()})
    request = make_mocked_request(
        "GET", "/tbot/assign/", headers={"X-Mint-Secret": SECRET}
    )
    body = (await handler.handle_get(request)).text

    assert body.count("<script>") == 1
    assert body.count("</script>") == 1
    assert "</script><script>" not in body
    # still valid JSON that round-trips to the original id
    script = body.split("const connectedDevices = ", 1)[1].split(";\n", 1)[0]
    assert json.loads(script)[0]["mac"] == hostile
PY

trap 'rm -f tests/__t64_repro.py' EXIT
python3 -m pytest tests/__t64_repro.py -q
