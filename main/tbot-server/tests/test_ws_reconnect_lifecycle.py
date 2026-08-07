"""T2.4 — websocket lifecycle & reconnection regressions.

Covers the two defects the reconnect matrix in
``robot/docs/qa/ad-hoc/2026-08-06-t24-esp-websocket.md`` exposed:

1. **Stale-socket invariant.** A device that reconnects (firmware passive-lesson
   backoff, Wi-Fi flap) opens a second socket while the first is still
   registered and, server-side, still open. Before the fix the old
   ``ConnectionHandler`` stayed alive with its own lesson runtime and event
   forwarder: the abandoned socket kept receiving lesson frames and the backend
   kept receiving progress for a session the child had left. This is the
   2026-07-06 "stale-WS listening state" bug class
   (``robot/docs/qa/ad-hoc/2026-07-06-esp-listening-stale-ws/``).
2. **Silent peer death.** ``timeout_seconds`` is floored at 61 minutes for
   60-minute Live sessions, so a half-open socket left a running lesson parked
   in LISTENING for the rest of the hour. During a lesson the firmware pings
   every 2 s and refuses the silent SD-sync window
   (``passive_websocket_liveness.h`` / ``BeginLessonAssetSyncQuiet``), so silence
   is decidable — hence the lesson-scoped peer-silence budget.

Plus the bounded-forwarder and tolerant-broadcast checks from the same matrix.
"""

import asyncio
import contextlib
import importlib.util
import json
import os
import sys
import types
import unittest
from pathlib import Path

import pytest
import websockets
from aiohttp.test_utils import make_mocked_request

import core.connection as conn_mod
import core.http_server as http_mod
import core.websocket_server as ws_mod
from core.connection_registry import ConnectionRegistry
from core.lesson.forwarder import LessonEventForwarder

def _sibling(module_name, alias):
    spec = importlib.util.spec_from_file_location(
        alias, Path(__file__).with_name(module_name)
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


edges_tests = _sibling("test_connection_edges.py", "test_connection_edges_for_ws_reconnect")
pull_tests = _sibling(
    "test_lesson_pull_on_connect_branch_gaps.py", "test_pull_on_connect_for_ws_reconnect"
)

_build_handler = edges_tests._build_handler

DEVICE_ID = "AA:BB:CC:DD:EE:24"

_ORIGINAL_SLEEP = asyncio.sleep
_ORIGINAL_CREATE_TASK = asyncio.create_task


class _Logger:
    def __init__(self):
        self.records = []

    def bind(self, **kwargs):
        return self

    def info(self, *args, **kwargs):
        self.records.append(("info", args))

    def debug(self, *args, **kwargs):
        self.records.append(("debug", args))

    def warning(self, *args, **kwargs):
        self.records.append(("warning", args))

    def error(self, *args, **kwargs):
        self.records.append(("error", args))


class _FakeWebSocket:
    def __init__(self, headers=None, path="/ws"):
        self.request = types.SimpleNamespace(path=path, headers=dict(headers or {}))
        self.sent = []
        self.close_calls = []
        self.state = types.SimpleNamespace(name="OPEN")

    async def send(self, payload):
        self.sent.append(payload)

    async def close(self, code=None, reason=None):
        self.close_calls.append((code, reason))
        self.state = types.SimpleNamespace(name="CLOSED")


# ── registry ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_registry_replace_returns_the_connection_it_displaced():
    registry = ConnectionRegistry()
    first = object()
    second = object()

    assert await registry.replace(DEVICE_ID, first) is None
    assert await registry.replace(DEVICE_ID, second) is first
    # Re-binding the same connection displaces nothing: supersession must not
    # fire on a no-op rebind.
    assert await registry.replace(DEVICE_ID, second) is None
    assert registry[DEVICE_ID] is second


@pytest.mark.asyncio
async def test_superseded_handler_teardown_never_evicts_its_replacement():
    """The old handler's ``finally`` runs after the new one registered."""
    registry = ConnectionRegistry()
    old = object()
    new = object()

    await registry.replace(DEVICE_ID, old)
    await registry.replace(DEVICE_ID, new)

    assert await registry.remove_if_current(DEVICE_ID, old) is False
    assert registry[DEVICE_ID] is new
    assert await registry.remove_if_current(DEVICE_ID, new) is True
    assert DEVICE_ID not in registry


# ── supersession protocol on WebSocketServer ────────────────────────────────


def _config():
    return {
        "selected_module": {},
        "server": {"auth_key": "secret", "auth": {"enabled": False}},
    }


def _build_server(monkeypatch):
    monkeypatch.setattr(ws_mod, "setup_logging", lambda: _Logger())
    monkeypatch.setattr(ws_mod, "initialize_modules", lambda *a, **k: {})
    return ws_mod.WebSocketServer(_config())


class _RecordingHandler:
    """Minimal stand-in with the supersession surface the server relies on."""

    def __init__(self, *args):
        self.device_id = None
        self.session_id = f"session-{id(self)}"
        self.websocket = None
        self.superseded = False
        self.superseded_by = None
        self.liveness_lease = None
        self._release = asyncio.Event()

    def mark_superseded(self, *, reason="duplicate_device_connection"):
        self.superseded = True
        self.reason = reason

    async def handle_connection(self, websocket):
        await self._release.wait()


@pytest.mark.asyncio
async def test_duplicate_device_connection_marks_and_closes_the_previous_socket(monkeypatch):
    server = _build_server(monkeypatch)
    handlers = []

    class Handler(_RecordingHandler):
        def __init__(self, *args):
            super().__init__(*args)
            handlers.append(self)

    connection_module = types.ModuleType("core.connection")
    connection_module.ConnectionHandler = Handler
    monkeypatch.setitem(sys.modules, "core.connection", connection_module)

    async def auth_ok(websocket):
        return None

    monkeypatch.setattr(server, "_handle_auth", auth_ok)

    headers = {"device-id": DEVICE_ID, "client-id": "client"}
    old_socket = _FakeWebSocket(headers)
    new_socket = _FakeWebSocket(headers)

    first = asyncio.create_task(server._handle_connection(old_socket))
    while not handlers:
        await asyncio.sleep(0)
    old = handlers[0]
    assert server.lesson_connections[DEVICE_ID] is old
    assert old.websocket is old_socket
    assert old.superseded is False

    second = asyncio.create_task(server._handle_connection(new_socket))
    while len(handlers) < 2:
        await asyncio.sleep(0)
    new = handlers[1]

    # Marked synchronously with registration — nothing can be dispatched to the
    # old handler after the new one owns the device.
    assert old.superseded is True
    assert old.superseded_by == new.session_id
    assert new.superseded is False
    assert server.lesson_connections[DEVICE_ID] is new

    # The socket close runs off the accept path; drain the scheduled task.
    for _ in range(10):
        if old_socket.close_calls:
            break
        await asyncio.sleep(0)
    assert old_socket.close_calls == [(1001, "superseded by newer connection")]
    assert new_socket.close_calls == []

    old._release.set()
    new._release.set()
    await asyncio.gather(first, second)
    # The old handler's teardown must not evict its replacement's registration.
    assert DEVICE_ID not in server.lesson_connections


@pytest.mark.asyncio
async def test_supersede_tolerates_a_handler_without_a_socket(monkeypatch):
    server = _build_server(monkeypatch)
    displaced = types.SimpleNamespace(websocket=None, superseded_by=None)
    winner = types.SimpleNamespace(session_id="new", liveness_lease=None)
    server._scrap_superseded_connection(displaced, winner, DEVICE_ID)
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert displaced.superseded_by == "new"
    assert not [record for record in server.logger.records if record[0] == "error"]


@pytest.mark.asyncio
async def test_supersede_logs_and_survives_a_close_failure(monkeypatch):
    server = _build_server(monkeypatch)

    class _Boom:
        async def close(self, code=None, reason=None):
            raise RuntimeError("half-open peer")

    displaced = types.SimpleNamespace(websocket=_Boom(), superseded_by=None)
    winner = types.SimpleNamespace(session_id="new", liveness_lease=None)
    server._scrap_superseded_connection(displaced, winner, DEVICE_ID)
    for _ in range(10):
        if any(level == "warning" for level, _ in server.logger.records):
            break
        await asyncio.sleep(0)
    assert any(level == "warning" for level, _ in server.logger.records)


# ── the handler side of the invariant ───────────────────────────────────────


class SupersededConnectionTest(unittest.IsolatedAsyncioTestCase):
    async def test_marked_handler_refuses_every_further_frame(self):
        handler = _build_handler()
        real_socket = edges_tests._SendWebSocket()
        handler.websocket = real_socket

        handler.mark_superseded()

        self.assertTrue(handler.is_superseded)
        self.assertTrue(handler.stop_event.is_set())
        with self.assertRaises(conn_mod.SupersededConnectionError):
            await handler.websocket.send(json.dumps({"type": "lesson_step"}))
        # Nothing reached the socket the device abandoned.
        self.assertEqual(real_socket.sent, [])
        # Teardown of a superseded handler stays a no-op on the stand-in.
        self.assertIsNone(await handler.websocket.close())

    async def test_mark_superseded_is_idempotent(self):
        handler = _build_handler()
        handler.websocket = edges_tests._SendWebSocket()
        handler.mark_superseded()
        sentinel = handler.websocket
        handler.mark_superseded()
        self.assertIs(handler.websocket, sentinel)

    async def test_a_handler_superseded_before_its_first_read_never_starts(self):
        """Registration precedes the read loop; a supersede can land in between."""
        handler = _build_handler()
        real_socket = edges_tests._SendWebSocket()
        handler.websocket = real_socket
        handler.mark_superseded()

        await handler.handle_connection(real_socket)

        # The stand-in was not overwritten and no read loop was entered.
        self.assertIsInstance(handler.websocket, conn_mod._SupersededWebSocket)
        self.assertIsNone(handler.timeout_task)


# ── lesson-scoped peer-silence watchdog ─────────────────────────────────────


class _Runtime:
    def __init__(self, state="RUNNING"):
        self.state = state


class LessonPeerSilenceTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        asyncio.sleep = _ORIGINAL_SLEEP
        asyncio.create_task = _ORIGINAL_CREATE_TASK

    def _handler(self, *, budget=60.0, runtime_state="RUNNING", idle_sec=120.0):
        handler = _build_handler()
        handler.config["lesson"] = {"peer_silence_timeout_sec": budget}
        handler.lesson_runtime = _Runtime(runtime_state) if runtime_state else None
        handler.first_activity_time = 1.0
        handler.last_activity_time = (edges_tests.__dict__.get("time") or __import__("time")).time() * 1000 - idle_sec * 1000
        handler.websocket = edges_tests._WebSocket()
        return handler

    async def _watch(self, handler, *, window=1.0):
        """Let the real timeout loop run; report whether it closed the socket.

        The loop polls on a 10 s cadence, so a run that has not closed within
        ``window`` has decided not to close at all on this pass.
        """
        closed = []

        async def _close(ws=None):
            closed.append(ws)
            handler.stop_event.set()

        handler.close = _close
        try:
            await asyncio.wait_for(handler._check_timeout(), timeout=window)
        except asyncio.TimeoutError:
            handler.stop_event.set()
        return closed

    async def test_running_lesson_with_a_silent_peer_closes_inside_the_budget(self):
        handler = self._handler(budget=60.0, idle_sec=120.0)
        closed = await self._watch(handler)
        self.assertEqual(len(closed), 1)

    async def test_silence_inside_the_budget_is_left_alone(self):
        handler = self._handler(budget=60.0, idle_sec=5.0)
        self.assertEqual(await self._watch(handler), [])

    async def test_no_lesson_runtime_falls_back_to_the_long_idle_timeout(self):
        handler = self._handler(runtime_state=None, idle_sec=120.0)
        self.assertEqual(await self._watch(handler), [])
        self.assertFalse(handler._lesson_peer_silence_watchdog_armed())

    async def test_sd_pack_sync_in_flight_suspends_the_watchdog(self):
        """The robot stops answering on purpose while it hashes an SD pack."""
        handler = self._handler(idle_sec=600.0)

        async def _never():
            await asyncio.Event().wait()

        task = asyncio.get_running_loop().create_task(_never())
        handler.sd_pack_sync_task = task
        try:
            self.assertFalse(handler._lesson_peer_silence_watchdog_armed())
            self.assertEqual(await self._watch(handler), [])
        finally:
            task.cancel()

    async def test_the_long_idle_timeout_still_fires_without_a_lesson(self):
        handler = self._handler(runtime_state=None, idle_sec=4000.0)
        self.assertEqual(len(await self._watch(handler)), 1)

    async def test_budget_is_configurable_and_disablable(self):
        handler = self._handler(budget=15.0)
        self.assertEqual(handler._lesson_peer_silence_timeout_sec(), 15.0)
        handler.config["lesson"] = {"peer_silence_timeout_sec": 0}
        self.assertIsNone(handler._lesson_peer_silence_timeout_sec())
        handler.config["lesson"] = {"peer_silence_timeout_sec": "not-a-number"}
        self.assertIsNone(handler._lesson_peer_silence_timeout_sec())
        handler.config["lesson"] = {}
        self.assertEqual(handler._lesson_peer_silence_timeout_sec(), 60.0)

    async def test_preloading_lesson_counts_as_an_active_runtime(self):
        handler = self._handler(runtime_state="PRELOADING")
        self.assertTrue(handler._lesson_peer_silence_watchdog_armed())


# ── reconnect matrix: teardown column × resume column ───────────────────────

#: Lesson lifecycle stages a disconnect can land on, named as
#: ``core.lesson.runtime`` reports them (``S_*``). "prepare" is IDLE: the
#: ``lesson_prepare`` frame is emitted before the runtime leaves that state.
LIFECYCLE_STAGES = ("IDLE", "PRELOADING", "READY", "RUNNING", "PAUSED")

#: Assignment states the backend can report when the device comes back.
RESUMABLE_ASSIGNMENT_STATES = ("ASSIGNED", "PRELOADING", "READY", "PAUSED")
TERMINAL_ASSIGNMENT_STATES = ("COMPLETED", "CANCELLED", "FAILED")


class _StubRuntime:
    def __init__(self, state):
        self.state = state
        self.closed = False

    async def close(self):
        self.closed = True


class _StubForwarder:
    def __init__(self):
        self.closed = False

    async def aclose(self):
        self.closed = True


class ReconnectMatrixTeardownTest(unittest.IsolatedAsyncioTestCase):
    """Disconnect column: every lifecycle stage must release the same resources.

    A stage that skipped runtime/forwarder teardown would leave the exact
    duplicate-runtime state the stale-socket bug produced.
    """

    def setUp(self):
        asyncio.sleep = _ORIGINAL_SLEEP
        asyncio.create_task = _ORIGINAL_CREATE_TASK

    async def test_every_lifecycle_stage_closes_runtime_forwarder_and_pull(self):
        for stage in LIFECYCLE_STAGES:
            with self.subTest(stage=stage):
                handler = _build_handler()
                handler.websocket = edges_tests._WebSocket()
                runtime = _StubRuntime(stage)
                forwarder = _StubForwarder()
                handler.lesson_runtime = runtime
                handler.safety_event_forwarder = forwarder

                async def _never():
                    await asyncio.Event().wait()

                pull = asyncio.get_running_loop().create_task(_never())
                handler.lesson_pull_task = pull

                await handler.close(handler.websocket)

                self.assertTrue(runtime.closed, f"{stage}: runtime not closed")
                self.assertTrue(forwarder.closed, f"{stage}: forwarder not closed")
                self.assertTrue(pull.cancelled() or pull.done())
                self.assertIsNone(handler.lesson_runtime)
                self.assertIsNone(handler.lesson_pull_task)
                self.assertTrue(handler.stop_event.is_set())


class ReconnectMatrixResumeTest(unittest.IsolatedAsyncioTestCase):
    """Resume column: what pull-on-connect does with the assignment it re-reads.

    Reconnect is a *restart* of the current assignment, not a mid-step resume:
    the server re-pulls ``assignment/current`` and drives prepare -> preload ->
    start again. Terminal assignments must not restart.
    """

    async def _pull(self, state):
        pull_tests._HealthyRuntime.instances = []
        conn = pull_tests._FakeConn()
        assignment = pull_tests._assignment()
        assignment["state"] = state
        patches = pull_tests._patches(
            assignment=assignment, runtime_cls=pull_tests._HealthyRuntime
        )
        result = await pull_tests._run_impl(conn, patches)
        return conn, result

    async def test_a_live_assignment_restarts_from_prepare_on_reconnect(self):
        for state in RESUMABLE_ASSIGNMENT_STATES:
            with self.subTest(state=state):
                conn, result = await self._pull(state)
                self.assertIsNotNone(result, f"{state}: no runtime rebuilt")
                self.assertTrue(result.started)
                self.assertIs(conn.lesson_runtime, result)

    async def test_a_terminal_assignment_never_restarts_on_reconnect(self):
        for state in TERMINAL_ASSIGNMENT_STATES:
            with self.subTest(state=state):
                conn, result = await self._pull(state)
                self.assertIsNone(result, f"{state}: restarted a finished lesson")
                self.assertEqual(pull_tests._HealthyRuntime.instances, [])
                self.assertEqual(
                    conn.lesson_start_status["code"], "ASSIGNMENT_TERMINAL"
                )


# ── forwarder backpressure ──────────────────────────────────────────────────


def _progress(seq):
    return {
        "assignmentId": "assign-1",
        "sessionId": "sess-1",
        "events": [{"type": "lesson_step_completed", "sequence": seq}],
    }


def _terminal():
    return {
        "assignmentId": "assign-1",
        "sessionId": "sess-1",
        "events": [{"type": "lesson_completed"}],
    }


class ForwarderBackpressureTest(unittest.IsolatedAsyncioTestCase):
    async def test_progress_is_shed_at_the_cap_but_terminal_events_are_not(self):
        posted = []

        async def _post(client, base_url, device_id, batch, token=None):
            posted.append(batch)

        forwarder = LessonEventForwarder(
            device_id="dev-1",
            base_url="http://backend.test/v1",
            post_fn=_post,
            max_queue_size=3,
            terminal_store=types.SimpleNamespace(
                store=_noop_store, load=_noop_load, clear=_noop_store
            ),
        )
        # Fill past the cap without letting the worker drain.
        for index in range(10):
            forwarder._queue.put_nowait((_progress(index), 0))

        forwarder.enqueue(_progress(99))
        self.assertEqual(forwarder.dropped_events_total, 1)
        self.assertEqual(len(forwarder.dead_letters), 1)

        forwarder.enqueue(_terminal())
        self.assertEqual(forwarder.dropped_events_total, 1)
        self.assertIsNotNone(forwarder.pending_terminal_batch)
        await forwarder.aclose()

    async def test_default_cap_leaves_ordinary_batches_untouched(self):
        posted = []

        async def _post(client, base_url, device_id, batch, token=None):
            posted.append(batch)

        forwarder = LessonEventForwarder(
            device_id="dev-1",
            base_url="http://backend.test/v1",
            post_fn=_post,
            terminal_store=types.SimpleNamespace(
                store=_noop_store, load=_noop_load, clear=_noop_store
            ),
        )
        for index in range(20):
            forwarder.enqueue(_progress(index))
        await forwarder._queue.join()
        self.assertEqual(len(posted), 20)
        self.assertEqual(forwarder.dropped_events_total, 0)
        await forwarder.aclose()


async def _noop_store(device_id, batch):
    return None


async def _noop_load(device_id, assignment_id):
    return None


# ── broadcast paths tolerate a connection that is falling over ──────────────


class _ExplodingAlarm:
    def snapshot(self):
        raise RuntimeError("connection is being torn down")

    def reset(self):
        raise RuntimeError("connection is being torn down")


class _HealthyAlarm:
    def snapshot(self):
        return {"tripped": False}

    def reset(self):
        return None


class BroadcastResilienceTest(unittest.IsolatedAsyncioTestCase):
    # These exercise the sweep's resilience to a broken connection, not auth —
    # but /internal/lesson-runtime/* now shares the X-Mint-Secret gate every other
    # /internal/ route uses (T6.4), so they have to authenticate to reach it.
    MINT_SECRET = "t64-ws-lifecycle-secret"

    def setUp(self):
        self._saved_secret = os.environ.get("TBOT_DEVICE_MINT_SECRET")
        os.environ["TBOT_DEVICE_MINT_SECRET"] = self.MINT_SECRET

    def tearDown(self):
        if self._saved_secret is None:
            os.environ.pop("TBOT_DEVICE_MINT_SECRET", None)
        else:
            os.environ["TBOT_DEVICE_MINT_SECRET"] = self._saved_secret

    def _request(self, method="GET", path="/internal/lesson-runtime/metrics"):
        return make_mocked_request(
            method, path, headers={"X-Mint-Secret": self.MINT_SECRET}
        )

    def _server(self):
        return http_mod.SimpleHttpServer(
            {
                "server": {
                    "auth_key": "test-key",
                    "websocket": "ws://Your_IP:port/tbot/v1/",
                    "ip": "127.0.0.1",
                    "http_port": 8003,
                    "port": 8000,
                }
            },
            {
                "bad": types.SimpleNamespace(lesson_voice_alarm=_ExplodingAlarm()),
                "good": types.SimpleNamespace(lesson_voice_alarm=_HealthyAlarm()),
            },
        )

    async def test_alarm_snapshot_sweep_skips_the_broken_connection(self):
        server = self._server()
        response = await server.handle_preload_voice_alarm_snapshot(
            self._request(path="/internal/lesson-runtime/preload-voice-alarm")
        )
        payload = json.loads(response.text)
        self.assertEqual(payload["alarms"], 1)
        self.assertEqual([d["deviceId"] for d in payload["devices"]], ["good"])

    async def test_alarm_reset_sweep_skips_the_broken_connection(self):
        server = self._server()
        response = await server.handle_preload_voice_alarm_reset(
            self._request("POST", "/internal/lesson-runtime/preload-voice-alarm/reset")
        )
        payload = json.loads(response.text)
        self.assertEqual(payload["reset"], 1)

    async def test_runtime_metrics_sweep_skips_the_broken_connection(self):
        server = self._server()
        response = await server.handle_lesson_runtime_metrics(self._request())
        payload = json.loads(response.text)
        self.assertEqual(payload["alarms"], 1)


# ── the invariant over a real socket ────────────────────────────────────────


class _StubProvider:
    def __init__(self, conn):
        self.conn = conn

    async def start_session(self):
        return None

    async def handle_text_message(self, message):
        return False

    async def handle_audio_bytes(self, message):
        return False

    async def close(self):
        return None


class RealSocketSupersessionTest(unittest.IsolatedAsyncioTestCase):
    """Two clients, one device-id, over a genuine ``websockets.serve`` acceptor."""

    def setUp(self):
        asyncio.sleep = _ORIGINAL_SLEEP
        asyncio.create_task = _ORIGINAL_CREATE_TASK
        self._saved = {
            "initialize_modules": ws_mod.initialize_modules,
            "setup_logging": ws_mod.setup_logging,
            "provider": conn_mod.create_voice_session_provider,
            "title": conn_mod.generate_and_save_chat_title,
        }
        ws_mod.initialize_modules = lambda *a, **k: {}
        ws_mod.setup_logging = lambda: _Logger()
        conn_mod.create_voice_session_provider = _StubProvider

        async def _skip_title(_session_id):
            return None

        conn_mod.generate_and_save_chat_title = _skip_title

    def tearDown(self):
        ws_mod.initialize_modules = self._saved["initialize_modules"]
        ws_mod.setup_logging = self._saved["setup_logging"]
        conn_mod.create_voice_session_provider = self._saved["provider"]
        conn_mod.generate_and_save_chat_title = self._saved["title"]

    @staticmethod
    def _config():
        return {
            "read_config_from_api": False,
            "selected_module": {},
            "exit_commands": [],
            "close_connection_no_voice_time": 120,
            "server": {
                "ip": "127.0.0.1",
                "port": 0,
                "auth_key": "test-secret",
                "auth": {"enabled": False},
            },
            "lesson": {"runtime_enabled": False},
            "tbot": {
                "type": "hello",
                "version": 1,
                "transport": "websocket",
                "audio_params": {
                    "format": "opus",
                    "sample_rate": 24000,
                    "channels": 1,
                    "frame_duration": 60,
                },
            },
        }

    async def _await_registration(self, server, previous=None):
        for _ in range(200):
            current = server.lesson_connections.get(DEVICE_ID)
            if current is not None and current is not previous:
                return current
            await asyncio.sleep(0.01)
        raise AssertionError("device never registered on the server")

    async def test_reconnect_closes_the_old_socket_and_silences_its_sends(self):
        server = ws_mod.WebSocketServer(self._config())
        headers = {"device-id": DEVICE_ID, "client-id": "client"}

        async with websockets.serve(
            server._handle_connection,
            "127.0.0.1",
            0,
            process_request=server._http_response,
        ) as srv:
            uri = f"ws://127.0.0.1:{srv.sockets[0].getsockname()[1]}/tbot/v1/"

            first = await websockets.connect(uri, additional_headers=headers)
            old_handler = await self._await_registration(server)
            old_socket = old_handler.websocket

            second = await websockets.connect(uri, additional_headers=headers)
            new_handler = await self._await_registration(server, previous=old_handler)
            self.assertIsNot(new_handler, old_handler)

            # 1) the device-observable invariant: the abandoned socket is closed,
            #    so no lesson frame can ever reach it again (RED before the fix —
            #    the old socket stayed open for the full 61-minute idle timeout).
            with self.assertRaises(websockets.exceptions.ConnectionClosed) as caught:
                await asyncio.wait_for(first.recv(), timeout=5)
            self.assertEqual(caught.exception.rcvd.code, 1001)
            with self.assertRaises(websockets.exceptions.ConnectionClosed):
                await old_socket.send(json.dumps({"type": "lesson_step"}))

            # 2) and the handler behind it was taken off the air synchronously,
            #    before that close completed
            self.assertTrue(old_handler.is_superseded)
            with self.assertRaises(conn_mod.SupersededConnectionError):
                await old_handler.websocket.send(json.dumps({"type": "lesson_step"}))

            # 3) the surviving socket is untouched and still carries frames
            self.assertFalse(new_handler.is_superseded)
            await new_handler.websocket.send(json.dumps({"type": "lesson_step"}))
            frame = json.loads(await asyncio.wait_for(second.recv(), timeout=5))
            self.assertEqual(frame["type"], "lesson_step")

            await second.close()
            await first.close()

        await server.drain(timeout=5)

    async def test_a_late_hello_on_the_old_socket_is_never_processed(self):
        """Out-of-order resume: the device moved on, its old frames must not land.

        `conn.features` is the oracle — it is None until a hello is processed
        (`core/handle/textHandler/helloMessageHandler.py`), so a stale hello that
        reached the superseded handler would show up there. There is no inbound
        epoch check to lean on: T2.5's lease ledger issues `session_epoch` on
        accept but nothing calls `classify_lease` on the receive path yet, so
        closing the socket is what actually enforces this.
        """
        server = ws_mod.WebSocketServer(self._config())
        headers = {"device-id": DEVICE_ID, "client-id": "client"}
        hello = json.dumps(
            {
                "type": "hello",
                "version": 1,
                "transport": "websocket",
                "audio_params": {
                    "format": "opus",
                    "sample_rate": 24000,
                    "channels": 1,
                    "frame_duration": 60,
                },
                "features": {"lesson": True, "mcp": False},
            }
        )

        async with websockets.serve(
            server._handle_connection,
            "127.0.0.1",
            0,
            process_request=server._http_response,
        ) as srv:
            uri = f"ws://127.0.0.1:{srv.sockets[0].getsockname()[1]}/tbot/v1/"

            first = await websockets.connect(uri, additional_headers=headers)
            old_handler = await self._await_registration(server)
            self.assertIsNone(old_handler.features)

            second = await websockets.connect(uri, additional_headers=headers)
            new_handler = await self._await_registration(server, previous=old_handler)

            # The device (or a retransmit) speaks on the socket it has abandoned.
            with contextlib.suppress(websockets.exceptions.ConnectionClosed):
                await first.send(hello)
            await asyncio.sleep(0.2)

            # Nothing was processed by the superseded handler, and it did not
            # claw the device registration back from its replacement.
            self.assertIsNone(old_handler.features)
            self.assertTrue(old_handler.is_superseded)
            self.assertIs(server.lesson_connections.get(DEVICE_ID), new_handler)

            # The live socket still handshakes normally.
            await second.send(hello)
            ack = json.loads(await asyncio.wait_for(second.recv(), timeout=5))
            self.assertEqual(ack["type"], "hello")
            self.assertIsNotNone(new_handler.features)

            await second.close()
            await first.close()

        await server.drain(timeout=5)


if __name__ == "__main__":  # pragma: no cover - manual runner
    unittest.main()
