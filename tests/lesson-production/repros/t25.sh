#!/usr/bin/env bash
# repo: robot/esp32-server
# T2.5 repro — the stale-socket invariant.
#
# When a second websocket takes a device, the displaced ConnectionHandler must go
# silent: its LessonRuntime must not write lesson frames to the socket the device
# already abandoned (type specimen:
# robot/docs/qa/ad-hoc/2026-07-06-esp-listening-stale-ws/).
#
# The test is embedded here rather than run from the repo tree so the SAME test
# executes on the pre-patch base and on the fix branch — a real RED->GREEN, not
# "the new file does not exist yet". Part 1 uses only pre-existing API surface;
# on the base it fails on behaviour. Part 2 pins the epoch-persistence decision.
set -euo pipefail

cd main/tbot-server

TEST_FILE="_t25_stale_socket_repro_test.py"
trap 'rm -f "$TEST_FILE"' EXIT

cat >"$TEST_FILE" <<'PYTEST'
import asyncio
import json
import types
import uuid

import pytest

import core.websocket_server as websocket_server
from core.lesson.runtime import LessonRuntime


class _NullLogger:
    def bind(self, **_kwargs):
        return self

    def debug(self, *_a, **_k):
        pass

    info = warning = error = debug


class _Socket:
    def __init__(self, device_id):
        self.request = types.SimpleNamespace(
            path="/ws", headers={"device-id": device_id, "client-id": device_id}
        )
        self.sent = []
        self.state = types.SimpleNamespace(name="OPEN")

    async def send(self, payload):
        self.sent.append(payload)

    async def close(self, code=None, reason=None):
        self.state = types.SimpleNamespace(name="CLOSED")

    @property
    def closed(self):
        return self.state.name == "CLOSED"


_HANDLERS = []


class _Handler:
    def __init__(self, config, vad, asr, llm, memory, intent, server):
        self.config = config
        self.server = server
        self.logger = _NullLogger()
        self.session_id = str(uuid.uuid4())
        self.device_id = None
        self.websocket = None
        self.liveness_lease = None
        self.superseded_by = None
        self._released = asyncio.Event()
        self.runtime = LessonRuntime.__new__(LessonRuntime)
        self.runtime.conn = self
        self.runtime._log = lambda level, message: None
        _HANDLERS.append(self)

    async def handle_connection(self, ws):
        self.websocket = ws
        await self._released.wait()

    async def close(self, ws=None):
        target = ws if ws is not None else self.websocket
        if target is not None:
            await target.close()
        self._released.set()


@pytest.fixture
def server(monkeypatch):
    _HANDLERS.clear()
    monkeypatch.setattr(websocket_server, "setup_logging", lambda: _NullLogger())
    monkeypatch.setattr(websocket_server, "initialize_modules", lambda *a, **k: {})
    monkeypatch.setitem(
        __import__("sys").modules,
        "core.connection",
        types.SimpleNamespace(ConnectionHandler=_Handler),
    )
    return websocket_server.WebSocketServer(
        {"selected_module": {}, "server": {"auth_key": "repro", "auth": {"enabled": False}}}
    )


async def _connect(server, device_id, tasks):
    socket = _Socket(device_id)
    before = len(_HANDLERS)
    tasks.append(asyncio.create_task(server._handle_connection(socket)))
    for _ in range(200):
        if len(_HANDLERS) > before and _HANDLERS[-1].websocket is socket:
            break
        await asyncio.sleep(0)
    else:
        raise AssertionError("handler never bound the socket")
    return _HANDLERS[-1], socket


@pytest.mark.asyncio
async def test_superseded_socket_receives_no_lesson_frames(server):
    """PART 1 — behavioural. Fails on the pre-patch base."""
    tasks = []
    try:
        device_id = "aa:bb:cc:dd:ee:ff"
        first, first_socket = await _connect(server, device_id, tasks)
        second, second_socket = await _connect(server, device_id, tasks)
        assert first is not second

        frame = json.dumps({"type": "lesson_step", "sessionId": first.session_id})
        await first.runtime._default_send(frame)
        assert first_socket.sent == [], (
            "STALE SOCKET RECEIVED A LESSON FRAME after being superseded: "
            f"{first_socket.sent}"
        )

        live = json.dumps({"type": "lesson_step", "sessionId": second.session_id})
        await second.runtime._default_send(live)
        assert second_socket.sent == [live], "the live socket must still receive frames"
    finally:
        for handler in _HANDLERS:
            handler._released.set()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_epoch_ledger_survives_the_process_it_guards():
    """PART 2 — the load-bearing epoch-persistence decision."""
    from core.lesson.liveness_lease import (
        InMemoryLeaseLedger,
        LeaseVerdict,
        Lease,
        RedisLeaseLedger,
        classify_lease,
    )

    class _FakeRedis:
        def __init__(self):
            self.store = {}

        async def incr(self, key):
            self.store[key] = self.store.get(key, 0) + 1
            return self.store[key]

        async def get(self, key):
            value = self.store.get(key)
            return None if value is None else str(value)

    redis = _FakeRedis()  # outlives the ledger, like the sidecar container does
    assert (await RedisLeaseLedger(redis, namespace="t25").issue("aa:bb")).session_epoch == 1
    assert (await RedisLeaseLedger(redis, namespace="t25").issue("aa:bb")).session_epoch == 2

    # The memory ledger must declare itself non-durable rather than pretend.
    assert InMemoryLeaseLedger().durable is False
    assert (await InMemoryLeaseLedger().issue("aa:bb")).session_epoch == 1

    stale = Lease("aa:bb", session_epoch=1, issued_at_ms=1000).to_wire()
    assert classify_lease(stale, known_epoch=2, now_ms=1100) is LeaseVerdict.RECOVER
    assert classify_lease(None, known_epoch=2) is LeaseVerdict.ACCEPT
PYTEST

python3 -m pytest "$TEST_FILE" -q

echo "REPRO PASS: T2.5 stale-socket invariant holds and the epoch ledger is restart-durable."
