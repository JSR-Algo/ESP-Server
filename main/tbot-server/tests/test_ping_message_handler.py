import json

import pytest

from core.handle.textHandler.pingMessageHandler import PingMessageHandler


class _WebSocket:
    def __init__(self):
        self.sent = []

    async def send(self, payload):
        self.sent.append(payload)


class _Logger:
    def debug(self, *_args, **_kwargs):
        return None

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
