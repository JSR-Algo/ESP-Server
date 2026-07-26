import asyncio
import json
import sys
import types

import pytest

import core.websocket_server as websocket_server


class _Logger:
    def __init__(self):
        self.records = []

    def bind(self, **kwargs):
        self.records.append(("bind", kwargs))
        return self

    def debug(self, *args):
        self.records.append(("debug", args))

    def info(self, *args):
        self.records.append(("info", args))

    def warning(self, *args):
        self.records.append(("warning", args))

    def error(self, *args):
        self.records.append(("error", args))


class _WebSocket:
    def __init__(self, headers=None, path="/ws", closed=None, state=None, close_error=None):
        self.request = types.SimpleNamespace(path=path, headers=headers or {})
        self.sent = []
        self.close_calls = []
        self.close_error = close_error
        if closed is not None:
            self.closed = closed
        if state is not None:
            self.state = types.SimpleNamespace(name=state)

    async def send(self, payload):
        self.sent.append(payload)

    async def close(self, code=None, reason=None):
        self.close_calls.append((code, reason))
        if self.close_error:
            raise self.close_error


def _config(**server_overrides):
    server = {"auth_key": "secret", "auth": {"enabled": False}}
    server.update(server_overrides)
    return {"selected_module": {}, "server": server}


def _build_server(monkeypatch, config=None, modules=None):
    monkeypatch.setattr(websocket_server, "setup_logging", lambda: _Logger())
    monkeypatch.setattr(websocket_server, "initialize_modules", lambda *args, **kwargs: modules or {})
    return websocket_server.WebSocketServer(config or _config())


def test_handshake_filter_and_accept_cap_fallbacks(monkeypatch):
    filter_ = websocket_server.SuppressInvalidHandshakeFilter()
    assert filter_.filter(types.SimpleNamespace(getMessage=lambda: "normal log")) is True
    assert filter_.filter(types.SimpleNamespace(getMessage=lambda: "opening handshake failed")) is False

    server = _build_server(monkeypatch, _config(audio_admission={"enabled": False}))
    assert server.accept_cap is None
    assert server._is_over_accept_cap() is False

    server = _build_server(monkeypatch, _config(audio_admission={"enabled": True, "hard_accept_cap": "bad", "measured_cost_per_stream_core_fraction": "bad"}))
    assert server.accept_cap is None
    assert any(level == "warning" for level, _ in server.logger.records)

    monkeypatch.setattr(websocket_server.os, "cpu_count", lambda: 8)
    server = _build_server(monkeypatch, _config(audio_admission={"enabled": True, "measured_cost_per_stream_core_fraction": 0.5, "headroom_fraction": "bad", "cpu_cores": "bad"}))
    assert server.accept_cap == 11

    monkeypatch.setattr(websocket_server, "setup_logging", lambda: _Logger())
    monkeypatch.setattr(websocket_server, "initialize_modules", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boot down")))
    with pytest.raises(RuntimeError, match="boot down"):
        websocket_server.WebSocketServer(_config())


@pytest.mark.asyncio
async def test_start_uses_websocket_serve_and_http_response(monkeypatch):
    server = _build_server(monkeypatch, _config(ip="127.0.0.1", port="9001"))
    calls = []

    class ServeContext:
        async def __aenter__(self):
            return types.SimpleNamespace(name="server")

        async def __aexit__(self, exc_type, exc, tb):
            return False

    def fake_serve(handler, host, port, process_request):
        calls.append((handler, host, port, process_request))
        return ServeContext()

    monkeypatch.setattr(websocket_server.websockets, "serve", fake_serve)
    task = asyncio.create_task(server.start())
    await asyncio.sleep(0)
    server._stop_event.set()
    await task
    assert calls[0][1:3] == ("127.0.0.1", 9001)
    assert server._server.name == "server"

    assert await server._http_response(object(), types.SimpleNamespace(headers={"connection": "upgrade"})) is None
    responder = types.SimpleNamespace(respond=lambda code, body: (code, body))
    assert await server._http_response(responder, types.SimpleNamespace(headers={})) == (200, "Server is running\n")


@pytest.mark.asyncio
async def test_drain_closes_server_sets_stop_event_and_cancels_pending_tasks(monkeypatch):
    server = _build_server(monkeypatch)
    closed = []

    class ServerHandle:
        def close(self):
            closed.append("close")

        async def wait_closed(self):
            closed.append("wait_closed")

    async def never_finishes():
        await asyncio.sleep(60)

    async def raises_when_done():
        raise RuntimeError("done failure")

    pending = asyncio.create_task(never_finishes())
    done_error = asyncio.create_task(raises_when_done())
    await asyncio.sleep(0)
    server._server = ServerHandle()
    server._stop_event = asyncio.Event()
    server._connection_tasks.update({pending, done_error})

    await server.drain(timeout=0.001)
    assert closed == ["close", "wait_closed"]
    assert server._stop_event.is_set()
    assert pending.cancelled()

    class PendingTask:
        def done(self):
            return False

    class DoneTask:
        def result(self):
            raise RuntimeError("done failure")

    async def fake_wait(pending_tasks, timeout):
        return {DoneTask()}, set()

    server = _build_server(monkeypatch)
    server._connection_tasks.add(PendingTask())
    monkeypatch.setattr(websocket_server.asyncio, "wait", fake_wait)
    await server.drain(timeout=0.001)


@pytest.mark.asyncio
async def test_update_config_success_none_and_exception(monkeypatch):
    server = _build_server(monkeypatch)
    new_config = {"selected_module": {"LLM": "llm", "Memory": "memory", "Intent": "intent"}, "server": {"auth_key": "new", "auth": {"enabled": False}}}
    monkeypatch.setattr(websocket_server, "get_config_from_api_async", lambda config: asyncio.sleep(0, result=new_config))
    monkeypatch.setattr(websocket_server, "check_vad_update", lambda old, new: True)
    monkeypatch.setattr(websocket_server, "check_asr_update", lambda old, new: True)

    def fake_initialize(logger, config, init_vad, init_asr, init_llm, init_tts, init_memory, init_intent):
        assert (init_vad, init_asr, init_llm, init_tts, init_memory, init_intent) == (True, True, True, False, True, True)
        return {"vad": "vad", "asr": "asr", "llm": "llm", "intent": "intent", "memory": "memory"}

    monkeypatch.setattr(websocket_server, "initialize_modules", fake_initialize)
    assert await server.update_config() is True
    assert (server._vad, server._asr, server._llm, server._intent, server._memory) == ("vad", "asr", "llm", "intent", "memory")

    monkeypatch.setattr(websocket_server, "get_config_from_api_async", lambda config: asyncio.sleep(0, result=None))
    assert await server.update_config() is False
    monkeypatch.setattr(websocket_server, "get_config_from_api_async", lambda config: (_ for _ in ()).throw(RuntimeError("api down")))
    assert await server.update_config() is False


@pytest.mark.asyncio
async def test_handle_connection_no_device_auth_failure_and_handler_cleanup(monkeypatch):
    server = _build_server(monkeypatch)
    no_path = _WebSocket(headers={}, path="")
    await server._handle_connection(no_path)
    assert no_path.close_calls == [(None, None)]

    probe = _WebSocket(headers={}, path="/probe")
    await server._handle_connection(probe)
    assert probe.sent == ["Port normal. To test connection, start digital-human test."]

    async def auth_fails(websocket):
        raise websocket_server.AuthenticationError("bad")

    monkeypatch.setattr(server, "_handle_auth", auth_fails)
    auth_ws = _WebSocket(headers={"device-id": "robot", "client-id": "client"})
    await server._handle_connection(auth_ws)
    assert auth_ws.sent == ["Authentication failed"]

    handled = []

    class Handler:
        def __init__(self, *args):
            self.device_id = None

        async def handle_connection(self, websocket):
            handled.append(self.device_id)
            raise RuntimeError("handler down")

    connection_module = types.ModuleType("core.connection")
    connection_module.ConnectionHandler = Handler
    monkeypatch.setitem(sys.modules, "core.connection", connection_module)

    async def auth_ok(websocket):
        return None

    monkeypatch.setattr(server, "_handle_auth", auth_ok)
    ws = _WebSocket(headers={"device-id": "robot", "client-id": "client"}, closed=False)
    await server._handle_connection(ws)
    assert handled == ["robot"]
    assert server.lesson_connections == {}
    assert server._active_device_connections == 0
    assert ws.close_calls == [(None, None)]

    ws_state = _WebSocket(headers={"device-id": "robot", "client-id": "client"}, state="OPEN")
    await server._handle_connection(ws_state)
    assert ws_state.close_calls == [(None, None)]

    ws_error = _WebSocket(headers={"device-id": "robot", "client-id": "client"}, close_error=RuntimeError("close down"))
    await server._handle_connection(ws_error)
    assert any(level == "error" and "close down" in args[0] for level, args in server.logger.records if args)


@pytest.mark.asyncio
async def test_handle_connection_invalidates_lesson_sd_online_index_on_disconnect(monkeypatch):
    server = _build_server(monkeypatch)
    invalidated = []
    server.lesson_sd_online_index = types.SimpleNamespace(
        invalidate_connection=lambda conn: invalidated.append(conn)
    )

    class Handler:
        def __init__(self, *args):
            self.device_id = None

        async def handle_connection(self, websocket):
            return None

    connection_module = types.ModuleType("core.connection")
    connection_module.ConnectionHandler = Handler
    monkeypatch.setitem(sys.modules, "core.connection", connection_module)

    async def auth_ok(websocket):
        return None

    monkeypatch.setattr(server, "_handle_auth", auth_ok)

    ws = _WebSocket(headers={"device-id": "robot", "client-id": "client"}, closed=True)
    await server._handle_connection(ws)

    assert len(invalidated) == 1
    assert invalidated[0].device_id == "robot"


@pytest.mark.asyncio
async def test_auth_enabled_missing_invalid_whitelist_and_accept_reject(monkeypatch):
    server = _build_server(monkeypatch, _config(auth={"enabled": True, "allowed_devices": ["safe"]}))
    server.auth_enable = True
    server.allowed_devices = {"safe"}
    await server._handle_auth(_WebSocket(headers={"device-id": "safe"}))

    with pytest.raises(websocket_server.AuthenticationError, match="Missing"):
        await server._handle_auth(_WebSocket(headers={"device-id": "robot", "client-id": "client", "authorization": "bad"}))

    server.auth.verify_token = lambda token, client_id, username: False
    with pytest.raises(websocket_server.AuthenticationError, match="Invalid token"):
        await server._handle_auth(_WebSocket(headers={"device-id": "robot", "client-id": "client", "authorization": "Bearer token"}))

    server.auth.verify_token = lambda token, client_id, username: True
    await server._handle_auth(_WebSocket(headers={"device-id": "robot", "client-id": "client", "authorization": "Bearer token"}))

    server.accept_cap = 2
    server._active_device_connections = 2
    assert server._is_over_accept_cap() is True
    busy = _WebSocket()
    await server._reject_accept_cap(busy)
    assert json.loads(busy.sent[0])["reason"] == "active_stream_cap"
