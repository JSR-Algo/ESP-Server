import asyncio
import sys
import types

import pytest
from websockets.datastructures import Headers

import core.websocket_server as websocket_server


class Logger:
    def __init__(self):
        self.records = []

    def bind(self, **_kwargs):
        return self

    def info(self, *args, **_kwargs):
        self.records.append(("info", args))

    def warning(self, *args, **_kwargs):
        self.records.append(("warning", args))

    def error(self, *args, **_kwargs):
        self.records.append(("error", args))


class WebSocket:
    def __init__(self, pairs):
        self.request = types.SimpleNamespace(path="/tbot/v1/", headers=Headers(pairs))
        self.sent = []
        self.close_calls = []
        self.closed = False

    async def send(self, payload):
        self.sent.append(payload)

    async def close(self, code=None, reason=None):
        self.close_calls.append((code, reason))
        self.closed = True


def build_server(monkeypatch):
    logger = Logger()
    monkeypatch.setattr(websocket_server, "setup_logging", lambda: logger)
    monkeypatch.setattr(websocket_server, "initialize_modules", lambda *args: {})
    config = {
        "selected_module": {},
        "server": {"auth_key": "secret", "auth": {"enabled": False}},
    }
    return websocket_server.WebSocketServer(config)


@pytest.mark.asyncio
async def test_duplicate_device_identity_is_rejected_inside_managed_lifecycle(monkeypatch):
    server = build_server(monkeypatch)
    websocket = WebSocket(
        [("device-id", "robot-one"), ("device-id", "robot-two"), ("client-id", "client")]
    )

    await server._handle_connection(websocket)

    assert websocket.sent == ["Authentication failed"]
    assert websocket.close_calls == [(None, None)]
    assert server._connection_tasks == set()
    assert server.lesson_connections == {}


@pytest.mark.asyncio
async def test_duplicate_build_identity_headers_reach_connection_boundary_uncollapsed(monkeypatch):
    server = build_server(monkeypatch)
    observed = []

    class Handler:
        def __init__(self, *args):
            self.device_id = None

        async def handle_connection(self, websocket):
            observed.extend(websocket.request.headers.get_all("x-tbot-elf-sha256"))

    module = types.ModuleType("core.connection")
    module.ConnectionHandler = Handler
    monkeypatch.setitem(sys.modules, "core.connection", module)
    websocket = WebSocket(
        [
            ("device-id", "robot"),
            ("client-id", "client"),
            ("x-tbot-elf-sha256", "a" * 64),
            ("x-tbot-elf-sha256", "b" * 64),
        ]
    )

    await server._handle_connection(websocket)

    assert observed == ["a" * 64, "b" * 64]
    assert websocket.close_calls == [(None, None)]
    assert server._connection_tasks == set()


@pytest.mark.asyncio
async def test_connection_registry_reclaims_unique_device_lock_entries():
    from core.connection_registry import ConnectionRegistry

    registry = ConnectionRegistry()
    for index in range(200):
        device_id = f"device-{index}"
        connection = types.SimpleNamespace(session_id=f"session-{index}")
        await registry.replace(device_id, connection)
        assert await registry.remove_if_current(device_id, connection)

    assert registry.lock_entry_count == 0


@pytest.mark.asyncio
async def test_connection_registry_retains_lock_entry_until_waiters_finish():
    from core.connection_registry import ConnectionRegistry

    registry = ConnectionRegistry()
    original = types.SimpleNamespace(session_id="one")
    replacement = types.SimpleNamespace(session_id="two")
    registry["device"] = original

    async with registry.reserve_current("device", original, "one") as current:
        assert current is True
        waiter = asyncio.create_task(registry.replace("device", replacement))
        await asyncio.sleep(0)
        assert waiter.done() is False
        assert registry.lock_entry_count == 1

    await waiter
    assert registry["device"] is replacement
    assert registry.lock_entry_count == 0
