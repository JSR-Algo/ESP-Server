import json

import pytest

from core.handle.textHandler.pingMessageHandler import PingMessageHandler


class _WebSocket:
    def __init__(self):
        self.sent = []

    async def send(self, payload):
        self.sent.append(payload)


class _Logger:
    def __init__(self):
        self.infos = []

    def debug(self, *_args, **_kwargs):
        return None

    def info(self, *args, **_kwargs):
        self.infos.append(args)

    def error(self, *_args, **_kwargs):
        return None


class _Connection:
    def __init__(self, config):
        self.config = config
        self.logger = _Logger()
        self.websocket = _WebSocket()
        self.last_activity_time = 0


@pytest.mark.asyncio
async def test_ping_defaults_to_enabled_when_manager_config_omits_flag():
    conn = _Connection({})

    await PingMessageHandler().handle(conn, {"type": "ping"})

    assert json.loads(conn.websocket.sent[-1])["type"] == "pong"


@pytest.mark.asyncio
async def test_ping_respects_explicit_disabled_flag():
    conn = _Connection({"enable_websocket_ping": False})

    await PingMessageHandler().handle(conn, {"type": "ping"})

    assert conn.websocket.sent == []


@pytest.mark.asyncio
async def test_evidence_firmware_ping_is_logged_only_once_per_lesson_step():
    conn = _Connection({})
    conn.google_live_evidence_journey_id = "lesson-journey-1"
    conn.session_id = "conn-1"
    conn.google_live_live_connection_id = "live-1"
    conn.lesson_runtime = type("Runtime", (), {"_step_id": "step-1"})()

    await PingMessageHandler().handle(conn, {"type": "ping"})
    await PingMessageHandler().handle(conn, {"type": "ping"})
    conn.lesson_runtime._step_id = "step-2"
    await PingMessageHandler().handle(conn, {"type": "ping"})

    markers = [
        args
        for args in conn.logger.infos
        if args and "Google Live firmware_ping journey_id=" in str(args[0])
    ]
    assert len(markers) == 2
    assert markers[0][1:] == ("lesson-journey-1", "conn-1", "live-1", "step-1")
    assert markers[1][1:] == ("lesson-journey-1", "conn-1", "live-1", "step-2")


@pytest.mark.asyncio
async def test_evidence_firmware_ping_rejects_unsafe_step_id():
    conn = _Connection({})
    conn.google_live_evidence_journey_id = "lesson-journey-1"
    conn.lesson_runtime = type("Runtime", (), {"_step_id": "unsafe step token"})()

    await PingMessageHandler().handle(conn, {"type": "ping"})

    assert not any(
        args and "Google Live firmware_ping journey_id=" in str(args[0])
        for args in conn.logger.infos
    )
