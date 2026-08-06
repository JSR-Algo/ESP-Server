"""T2.5 — reconnect-storm harness for the esp32-server websocket endpoint.

Wraps the real :meth:`core.websocket_server.WebSocketServer._handle_connection`
with a fake-ESP32 websocket client and a fake-mobile consumer, replays a recorded
lesson transcript through it, and lets a fault scheduler inject drops, duplicate
connects and out-of-order resumes at each lifecycle stage.

What is real and what is faked
------------------------------
Real (this is the code under test):

* ``WebSocketServer._handle_connection`` — accept, auth, registry install,
  supersede, teardown.
* ``ConnectionRegistry`` — the ``replace`` / ``remove_if_current`` identity rules.
* ``core.lesson.liveness_lease`` — epoch issuance and the consumer-side
  ``classify_lease`` gate.
* ``LessonRuntime._default_send`` — the single choke point every outbound lesson
  frame passes through, bound to a harness connection.

Faked: the voice stack. A full ``ConnectionHandler`` boots VAD/ASR/LLM/TTS and a
Google-Live provider; none of that participates in teardown ownership, and
booting it would make a 24-permutation storm untestable. :class:`HarnessHandler`
implements exactly the handler contract ``_handle_connection`` touches.

The invariant
-------------
:meth:`ReconnectStormHarness.assert_invariant` asserts one thing:

    *No consumer holds listening/session state without a live lease* — and,
    pre-lease, *no stale socket ever receives lesson messages meant for a newer
    one.*

Both halves are checked on the same recorded traffic, so the pre-lease half keeps
working as a regression net while the lease rolls out incrementally.
"""

from __future__ import annotations

import asyncio
import json
import os
import types
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

import core.websocket_server as websocket_server
from core.lesson.liveness_lease import (
    LeaseVerdict,
    attach_lease,
    classify_lease,
    read_lease,
)
from core.lesson.runtime import LessonRuntime

# ── lifecycle stages (matrix columns) ──────────────────────────────────────────

STAGE_CONNECT = "connect"
STAGE_PREPARE = "prepare"
STAGE_PRELOAD = "preload"
STAGE_START = "start"
STAGE_STEP = "step"
STAGE_TERMINAL = "terminal"

LIFECYCLE_STAGES: Sequence[str] = (
    STAGE_CONNECT,
    STAGE_PREPARE,
    STAGE_PRELOAD,
    STAGE_START,
    STAGE_STEP,
    STAGE_TERMINAL,
)

#: Which lifecycle stage each recorded server→firmware frame belongs to.
_FRAME_STAGE = {
    "lesson_prepare": STAGE_PREPARE,
    "lesson_preload_status": STAGE_PRELOAD,
    "lesson_start": STAGE_START,
    "lesson_step": STAGE_STEP,
    "lesson_stop": STAGE_TERMINAL,
    "lesson_error": STAGE_TERMINAL,
}

# ── fault vocabulary (matrix rows) ─────────────────────────────────────────────

FAULT_WS_DROP = "ws_drop"
FAULT_DUPLICATE_CONNECT = "duplicate_connect"
FAULT_OUT_OF_ORDER_RESUME = "out_of_order_resume"
FAULT_SERVER_RESTART = "server_restart"

FAULT_KINDS: Sequence[str] = (
    FAULT_WS_DROP,
    FAULT_DUPLICATE_CONNECT,
    FAULT_OUT_OF_ORDER_RESUME,
    FAULT_SERVER_RESTART,
)


@dataclass(frozen=True)
class Fault:
    """Inject ``kind`` immediately before the frame belonging to ``stage``."""

    stage: str
    kind: str


# ── transcript ─────────────────────────────────────────────────────────────────

_TRANSCRIPT_PATH = os.path.join(
    os.path.dirname(__file__), "fixtures", "lesson_transcript_happy.json"
)


def load_transcript() -> List[Dict[str, Any]]:
    """The recorded happy-path lesson thread, server→firmware frames only.

    Vendored into this repo so the storm runs in a single-repo checkout. The
    parity test in ``test_reconnect_storm.py`` pins it to the canonical
    ``lesson-protocol.v1.json`` ``happyThread`` whenever the robot repo is
    resolvable.
    """
    with open(_TRANSCRIPT_PATH) as handle:
        return json.load(handle)["frames"]


# ── fakes ──────────────────────────────────────────────────────────────────────


class FakeSocket:
    """Minimal websockets-server-connection stand-in."""

    def __init__(self, device_id: str, *, path: str = "/ws"):
        self.device_id = device_id
        self.request = types.SimpleNamespace(
            path=path, headers={"device-id": device_id, "client-id": device_id}
        )
        self.sent: List[str] = []
        self.state = types.SimpleNamespace(name="OPEN")
        self.close_calls: List[Any] = []
        #: Set when the physical link is gone but the server has not noticed —
        #: the half-open TCP case that produces stale listening state.
        self.half_open = False

    async def send(self, payload: str) -> None:
        if self.state.name == "CLOSED":
            raise ConnectionError("socket closed")
        if self.half_open:
            # A half-open socket swallows writes without erroring. That silence
            # is precisely why the server keeps believing the session is alive.
            self.sent.append(payload)
            return
        self.sent.append(payload)

    async def close(self, code: Optional[int] = None, reason: Optional[str] = None) -> None:
        self.close_calls.append((code, reason))
        self.state = types.SimpleNamespace(name="CLOSED")

    @property
    def closed(self) -> bool:
        return self.state.name == "CLOSED"

    def frames(self) -> List[Dict[str, Any]]:
        decoded = []
        for payload in self.sent:
            try:
                decoded.append(json.loads(payload))
            except (TypeError, ValueError):
                continue
        return decoded


class FakeEsp32Client:
    """One robot-side websocket attempt against the harness."""

    def __init__(self, device_id: str):
        self.device_id = device_id
        self.socket = FakeSocket(device_id)
        self.handler: Optional["HarnessHandler"] = None

    def drop(self, *, half_open: bool = False) -> None:
        """Lose the link. ``half_open`` models the server not noticing."""
        if half_open:
            self.socket.half_open = True
            return
        self.socket.state = types.SimpleNamespace(name="CLOSED")


class FakeMobileConsumer:
    """A downstream consumer of session state, gated by the lease contract.

    Stands in for the mobile parent view and the backend projection: both hold
    "child is in a lesson right now" state derived from frames they did not
    originate, and both must refuse a stale epoch.
    """

    def __init__(self, device_id: str):
        self.device_id = device_id
        self.known_epoch: Optional[int] = None
        #: Session state this consumer currently believes is live.
        self.listening_session_id: Optional[str] = None
        self.verdicts: List[LeaseVerdict] = []
        self.accepted: List[Dict[str, Any]] = []
        self.refused: List[Dict[str, Any]] = []

    def observe(self, frame: Dict[str, Any], *, now_ms: Optional[int] = None) -> LeaseVerdict:
        verdict = classify_lease(
            read_lease(frame), known_epoch=self.known_epoch, now_ms=now_ms
        )
        self.verdicts.append(verdict)
        if verdict is LeaseVerdict.ACCEPT:
            lease = read_lease(frame)
            if lease is not None:
                self.known_epoch = max(self.known_epoch or 0, int(lease["sessionEpoch"]))
            self.accepted.append(frame)
            if frame.get("type") in ("lesson_start", "lesson_step"):
                self.listening_session_id = frame.get("sessionId")
            if frame.get("type") in ("lesson_stop", "lesson_error"):
                self.listening_session_id = None
        else:
            self.refused.append(frame)
            if verdict in (LeaseVerdict.RECOVER, LeaseVerdict.ABORT):
                # Stale or unusable: drop the state instead of keeping a ghost.
                self.listening_session_id = None
        return verdict


class HarnessHandler:
    """Stand-in for ``ConnectionHandler``, limited to the teardown contract."""

    #: Filled in by the harness so each instance can register itself.
    harness: Optional["ReconnectStormHarness"] = None

    def __init__(self, config, vad, asr, llm, memory, intent, server):
        self.config = config
        self.server = server
        self.logger = _NullLogger()
        self.session_id = str(uuid.uuid4())
        self.device_id: Optional[str] = None
        self.websocket: Optional[FakeSocket] = None
        self.liveness_lease = None
        self.superseded_by = None
        self.closed = False
        self.close_calls = 0
        self._released = asyncio.Event()
        self.runtime = LessonRuntime.__new__(LessonRuntime)
        self.runtime.conn = self
        self.runtime._log = lambda level, message: None
        if HarnessHandler.harness is not None:
            HarnessHandler.harness.handlers.append(self)

    async def handle_connection(self, ws) -> None:
        self.websocket = ws
        # Stay resident until the harness releases the connection, mirroring a
        # real handler that lives for the socket's lifetime.
        await self._released.wait()

    def release(self) -> None:
        self._released.set()

    async def close(self, ws=None) -> None:
        self.close_calls += 1
        self.closed = True
        target = ws if ws is not None else self.websocket
        if target is not None:
            await target.close()
        self._released.set()

    async def emit_lesson_frame(self, frame: Dict[str, Any]) -> None:
        """Send one lesson frame the way the real runtime does."""
        attach_lease(frame, self.liveness_lease)
        await self.runtime._default_send(json.dumps(frame, ensure_ascii=False))


class _NullLogger:
    def bind(self, **_kwargs):
        return self

    def debug(self, *_a, **_k):
        pass

    def info(self, *_a, **_k):
        pass

    def warning(self, *_a, **_k):
        pass

    def error(self, *_a, **_k):
        pass


# ── report ─────────────────────────────────────────────────────────────────────


@dataclass
class DeliveredFrame:
    frame: Dict[str, Any]
    socket: FakeSocket
    handler: HarnessHandler
    stage: str
    #: Whether the sending handler had *already* been superseded when this frame
    #: went out. Frames sent before a supersede are legitimate history; only
    #: writes after it are the stale-socket defect.
    was_stale: bool = False


@dataclass
class StormReport:
    device_id: str
    faults: List[Fault] = field(default_factory=list)
    delivered: List[DeliveredFrame] = field(default_factory=list)
    suppressed: List[Dict[str, Any]] = field(default_factory=list)
    consumer: Optional[FakeMobileConsumer] = None

    def frames_on_stale_sockets(self) -> List[DeliveredFrame]:
        """Deliveries that landed on a socket a newer connection had replaced."""
        return [item for item in self.delivered if item.was_stale]


# ── harness ────────────────────────────────────────────────────────────────────


class ReconnectStormHarness:
    """Drives the real WS endpoint through a transcript under injected faults."""

    def __init__(self, monkeypatch, *, device_id: str = "aa:bb:cc:dd:ee:ff"):
        self.device_id = device_id
        self.handlers: List[HarnessHandler] = []
        self.consumer = FakeMobileConsumer(device_id)
        self._monkeypatch = monkeypatch
        self._tasks: List[asyncio.Task] = []
        self.server = self._build_server(monkeypatch)

    def _build_server(self, monkeypatch):
        monkeypatch.setattr(websocket_server, "setup_logging", lambda: _NullLogger())
        monkeypatch.setattr(
            websocket_server, "initialize_modules", lambda *a, **k: {}
        )
        HarnessHandler.harness = self
        fake_connection_module = types.SimpleNamespace(ConnectionHandler=HarnessHandler)
        monkeypatch.setitem(
            __import__("sys").modules, "core.connection", fake_connection_module
        )
        config = {
            "selected_module": {},
            "server": {"auth_key": "harness", "auth": {"enabled": False}},
        }
        return websocket_server.WebSocketServer(config)

    # -- connection lifecycle --------------------------------------------------

    async def connect(self) -> FakeEsp32Client:
        """Open one fake-ESP32 connection and wait until it owns the device."""
        client = FakeEsp32Client(self.device_id)
        before = len(self.handlers)
        task = asyncio.create_task(self.server._handle_connection(client.socket))
        self._tasks.append(task)
        for _ in range(200):
            if len(self.handlers) > before and self.handlers[-1].websocket is client.socket:
                break
            await asyncio.sleep(0)
        else:  # pragma: no cover - harness wiring failure
            raise AssertionError("fake-ESP32 client never reached the handler")
        client.handler = self.handlers[-1]
        return client

    async def restart_server(self) -> None:
        """Model an esp32-server process restart mid-lesson.

        Every in-process connection dies and a brand-new ``WebSocketServer``
        (fresh registry, fresh accept counters) takes over. The epoch ledger is
        deliberately *not* reset here: whether it survives is exactly the
        property under test, and the ledger's own durability decides that.
        """
        for handler in list(self.handlers):
            handler.release()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self.server = self._build_server(self._monkeypatch)

    async def shutdown(self) -> None:
        for handler in list(self.handlers):
            handler.release()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    # -- storm -----------------------------------------------------------------

    async def run(
        self,
        transcript: Sequence[Dict[str, Any]],
        faults: Sequence[Fault] = (),
    ) -> StormReport:
        report = StormReport(device_id=self.device_id, faults=list(faults), consumer=self.consumer)
        by_stage: Dict[str, List[Fault]] = {}
        for fault in faults:
            by_stage.setdefault(fault.stage, []).append(fault)

        client = await self.connect()
        for fault in by_stage.get(STAGE_CONNECT, ()):
            client = await self._inject(fault, client, report)

        for entry in transcript:
            frame_type = entry.get("type")
            stage = _FRAME_STAGE.get(frame_type, STAGE_STEP)
            for fault in by_stage.get(stage, ()):
                client = await self._inject(fault, client, report)

            emitter = client.handler
            assert emitter is not None
            frame = _materialize(entry, emitter)
            was_stale = bool(emitter.superseded_by)
            before = len(emitter.websocket.sent) if emitter.websocket else 0
            await emitter.emit_lesson_frame(frame)
            after = len(emitter.websocket.sent) if emitter.websocket else 0
            if after > before:
                delivered = json.loads(emitter.websocket.sent[-1])
                report.delivered.append(
                    DeliveredFrame(
                        frame=delivered,
                        socket=emitter.websocket,
                        handler=emitter,
                        stage=stage,
                        was_stale=was_stale,
                    )
                )
                self.consumer.observe(delivered)
            else:
                report.suppressed.append(frame)

        await self.shutdown()
        return report

    async def _inject(
        self, fault: Fault, client: FakeEsp32Client, report: StormReport
    ) -> FakeEsp32Client:
        if fault.kind == FAULT_WS_DROP:
            # Half-open: the link is gone but the server has not noticed. This is
            # the shape of the 2026-07-06 stale-listening incident.
            client.drop(half_open=True)
            return client
        if fault.kind == FAULT_DUPLICATE_CONNECT:
            return await self.connect()
        if fault.kind == FAULT_OUT_OF_ORDER_RESUME:
            # The device reconnects, then the ORIGINAL socket comes back and
            # tries to keep driving the lesson.
            newer = await self.connect()
            stale = client
            stale_frame = _materialize(
                {"type": "lesson_step", "sequence": 99, "stepId": "stale"}, stale.handler
            )
            was_stale = bool(stale.handler.superseded_by)
            before = len(stale.socket.sent)
            await stale.handler.emit_lesson_frame(stale_frame)
            if len(stale.socket.sent) > before:
                report.delivered.append(
                    DeliveredFrame(
                        frame=json.loads(stale.socket.sent[-1]),
                        socket=stale.socket,
                        handler=stale.handler,
                        stage=STAGE_STEP,
                        was_stale=was_stale,
                    )
                )
                self.consumer.observe(json.loads(stale.socket.sent[-1]))
            else:
                report.suppressed.append(stale_frame)
            return newer
        if fault.kind == FAULT_SERVER_RESTART:
            await self.restart_server()
            return await self.connect()
        raise AssertionError(f"unknown fault kind: {fault.kind}")  # pragma: no cover

    # -- invariant -------------------------------------------------------------

    @staticmethod
    def assert_invariant(report: StormReport) -> None:
        """The single asserted invariant. See the module docstring."""
        stale = report.frames_on_stale_sockets()
        assert not stale, (
            "stale socket received lesson frames meant for a newer session: "
            + ", ".join(
                f"{item.frame.get('type')}@{item.stage}" for item in stale
            )
        )

        consumer = report.consumer
        assert consumer is not None
        if consumer.listening_session_id is not None:
            live = [
                item
                for item in report.delivered
                if item.frame.get("sessionId") == consumer.listening_session_id
                and not item.handler.superseded_by
            ]
            assert live, (
                "consumer holds listening state for session "
                f"{consumer.listening_session_id} with no live lease behind it"
            )


def _materialize(entry: Dict[str, Any], handler: HarnessHandler) -> Dict[str, Any]:
    """Turn a recorded transcript row into a wire frame for ``handler``'s session."""
    return {
        "type": entry.get("type"),
        "protocolVersion": "teebot-lesson-renderer.v1",
        "assignmentId": "11111111-1111-4111-8111-111111111111",
        "sessionId": handler.session_id,
        "lessonId": "22222222-2222-4222-8222-222222222222",
        "lessonVersion": 1,
        "stepId": entry.get("stepId"),
        "sequence": entry.get("sequence", 1),
        "timestamp": 1_700_000_000_000,
        "body": {},
    }
