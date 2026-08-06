"""T2.5 — reconnect-storm suite + the stale-socket regression test.

``test_stale_socket_never_receives_newer_sessions_frames`` is the RED→GREEN
regression for the 2026-07-06 stale-WS listening incident
(``robot/docs/qa/ad-hoc/2026-07-06-esp-listening-stale-ws/``). Before the fix,
``ConnectionRegistry.replace`` swapped the map entry and walked away: the
displaced ``ConnectionHandler`` kept its lesson runtime, kept its websocket, and
kept writing lesson frames addressed to a session the device had already
abandoned.

Test IDs referenced by ``robot/docs/failure-path-matrix.md`` are the test
function names in this module.
"""

from __future__ import annotations

import asyncio
import json
import os

import pytest

from core.lesson.liveness_lease import (
    DEFAULT_LEASE_TTL_MS,
    Disposition,
    InMemoryLeaseLedger,
    Lease,
    LeaseVerdict,
    RedisLeaseLedger,
    attach_lease,
    classify_lease,
    emit_disposition,
    read_lease,
    reset_lease_ledger,
)
from tests.reconnect_storm import (
    FAULT_DUPLICATE_CONNECT,
    FAULT_OUT_OF_ORDER_RESUME,
    FAULT_SERVER_RESTART,
    FAULT_WS_DROP,
    LIFECYCLE_STAGES,
    Fault,
    ReconnectStormHarness,
    load_transcript,
)


@pytest.fixture(autouse=True)
def _isolated_lease_ledger():
    """Every test gets a fresh in-memory ledger; none touches the real Redis."""
    reset_lease_ledger(InMemoryLeaseLedger())
    yield
    reset_lease_ledger(None)


# ── the invariant ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stale_socket_never_receives_newer_sessions_frames(monkeypatch):
    """REGRESSION (stale-WS listening): a superseded socket must go silent.

    RED before the fix: the displaced handler's ``_default_send`` still resolved
    ``conn.websocket`` and wrote the frame.
    """
    harness = ReconnectStormHarness(monkeypatch)
    first = await harness.connect()
    second = await harness.connect()

    assert first.handler is not second.handler
    assert first.handler.superseded_by, "duplicate connect did not mark the old handler"
    assert harness.server.lesson_connections.is_current(
        harness.device_id, second.handler
    )

    frames_before = len(first.socket.sent)
    await first.handler.emit_lesson_frame(
        {"type": "lesson_step", "sessionId": first.handler.session_id, "sequence": 7}
    )
    assert len(first.socket.sent) == frames_before, (
        "stale socket received a lesson frame after being superseded"
    )

    await second.handler.emit_lesson_frame(
        {"type": "lesson_step", "sessionId": second.handler.session_id, "sequence": 1}
    )
    assert len(second.socket.sent) == 1, "the live socket must still receive frames"

    await harness.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", LIFECYCLE_STAGES)
@pytest.mark.parametrize(
    "kind",
    [FAULT_WS_DROP, FAULT_DUPLICATE_CONNECT, FAULT_OUT_OF_ORDER_RESUME, FAULT_SERVER_RESTART],
)
async def test_storm_holds_invariant_at_every_stage(monkeypatch, stage, kind):
    """The full failure × lifecycle-stage grid, one assertion each."""
    harness = ReconnectStormHarness(monkeypatch)
    report = await harness.run(load_transcript(), faults=[Fault(stage=stage, kind=kind)])
    ReconnectStormHarness.assert_invariant(report)


@pytest.mark.asyncio
async def test_storm_holds_invariant_under_stacked_faults(monkeypatch):
    """Drops and duplicate connects piled across the whole lifecycle."""
    harness = ReconnectStormHarness(monkeypatch)
    faults = [
        Fault(stage="prepare", kind=FAULT_WS_DROP),
        Fault(stage="preload", kind=FAULT_DUPLICATE_CONNECT),
        Fault(stage="start", kind=FAULT_OUT_OF_ORDER_RESUME),
        Fault(stage="step", kind=FAULT_DUPLICATE_CONNECT),
        Fault(stage="terminal", kind=FAULT_WS_DROP),
    ]
    report = await harness.run(load_transcript(), faults=faults)
    ReconnectStormHarness.assert_invariant(report)
    assert report.suppressed, "a storm this heavy must have suppressed stale writes"


@pytest.mark.asyncio
async def test_happy_path_delivers_every_frame(monkeypatch):
    """No faults: nothing is suppressed and the consumer accepts everything."""
    harness = ReconnectStormHarness(monkeypatch)
    transcript = load_transcript()
    report = await harness.run(transcript)
    ReconnectStormHarness.assert_invariant(report)
    assert len(report.delivered) == len(transcript)
    assert report.suppressed == []
    assert set(report.consumer.verdicts) == {LeaseVerdict.ACCEPT}


# ── supersede bookkeeping ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_superseded_connection_is_closed_not_just_unregistered(monkeypatch):
    harness = ReconnectStormHarness(monkeypatch)
    first = await harness.connect()
    await harness.connect()
    for _ in range(200):
        if first.handler.closed:
            break
        await asyncio.sleep(0)
    assert first.handler.closed, "superseded handler was never torn down"
    assert first.socket.closed
    await harness.shutdown()


@pytest.mark.asyncio
async def test_registry_replace_returns_the_displaced_connection():
    from core.connection_registry import ConnectionRegistry

    registry = ConnectionRegistry()
    first = object()
    second = object()

    assert await registry.replace("dev", first) is None
    assert await registry.replace("dev", second) is first
    assert await registry.replace("dev", second) is None, "self-replace is not a supersede"
    assert registry.is_current("dev", second)
    assert not registry.is_current("dev", first)


@pytest.mark.asyncio
async def test_registry_release_is_identity_guarded():
    from core.connection_registry import ConnectionRegistry

    registry = ConnectionRegistry()
    first, second = object(), object()
    await registry.replace("dev", first)
    await registry.replace("dev", second)
    assert await registry.remove_if_current("dev", first) is False
    assert registry.is_current("dev", second)
    assert await registry.remove_if_current("dev", second) is True


# ── lease semantics ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_epoch_is_monotonic_per_device():
    ledger = InMemoryLeaseLedger()
    first = await ledger.issue("AA:BB")
    second = await ledger.issue("aa:bb")
    other = await ledger.issue("cc:dd")
    assert (first.session_epoch, second.session_epoch) == (1, 2)
    assert other.session_epoch == 1, "epochs are per-device, not global"
    assert await ledger.current("aa:bb") == 2


@pytest.mark.asyncio
async def test_in_memory_ledger_is_declared_non_durable():
    """The load-bearing risk, made checkable rather than assumed."""
    ledger = InMemoryLeaseLedger()
    assert ledger.durable is False
    assert (await ledger.issue("aa:bb")).durable is False
    # A restart re-instantiates the ledger, which is exactly the reset that makes
    # a memory-resident epoch theater.
    assert (await InMemoryLeaseLedger().issue("aa:bb")).session_epoch == 1


@pytest.mark.asyncio
async def test_redis_ledger_survives_a_server_restart():
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
    before = await RedisLeaseLedger(redis, namespace="t25").issue("aa:bb")
    after = await RedisLeaseLedger(redis, namespace="t25").issue("aa:bb")
    assert before.session_epoch == 1 and after.session_epoch == 2
    assert after.durable is True


def test_absent_lease_is_accepted_so_rollout_can_be_incremental():
    assert classify_lease(None, known_epoch=None) is LeaseVerdict.ACCEPT
    assert classify_lease(None, known_epoch=17) is LeaseVerdict.ACCEPT


def test_stale_epoch_routes_to_recover():
    lease = Lease("aa:bb", session_epoch=3, issued_at_ms=1_000).to_wire()
    assert classify_lease(lease, known_epoch=5, now_ms=1_100) is LeaseVerdict.RECOVER


def test_expired_ttl_routes_to_resume():
    lease = Lease("aa:bb", session_epoch=5, ttl_ms=1_000, issued_at_ms=1_000).to_wire()
    assert classify_lease(lease, known_epoch=5, now_ms=2_500) is LeaseVerdict.RESUME
    assert classify_lease(lease, known_epoch=5, now_ms=2_500, grace_ms=1_000) is LeaseVerdict.ACCEPT


def test_malformed_or_implausible_lease_routes_to_abort():
    assert classify_lease("nope", known_epoch=1) is LeaseVerdict.ABORT
    assert classify_lease({"sessionEpoch": 0, "ttlMs": 10, "issuedAtMs": 0}, known_epoch=None) is LeaseVerdict.ABORT
    assert classify_lease({"sessionEpoch": 2, "ttlMs": 0, "issuedAtMs": 0}, known_epoch=None) is LeaseVerdict.ABORT
    far_future = {"sessionEpoch": 500_000, "ttlMs": 1_000, "issuedAtMs": 0}
    assert classify_lease(far_future, known_epoch=3, now_ms=0) is LeaseVerdict.ABORT


def test_lease_is_additive_on_the_wire():
    """Old firmware must see a byte-identical frame when no lease is minted."""
    base = {"type": "lesson_step", "sequence": 3}
    assert attach_lease(dict(base), None) == base
    stamped = attach_lease(dict(base), Lease("aa:bb", session_epoch=4, issued_at_ms=9))
    assert {k: stamped[k] for k in base} == base
    assert read_lease(stamped) == {
        "sessionEpoch": 4,
        "seq": 0,
        "ttlMs": DEFAULT_LEASE_TTL_MS,
        "issuedAtMs": 9,
    }
    assert read_lease(base) is None


def test_lease_seq_advances_within_an_epoch():
    lease = Lease("aa:bb", session_epoch=2, issued_at_ms=10)
    nxt = lease.next_seq(now_ms=50)
    assert (nxt.session_epoch, nxt.seq, nxt.issued_at_ms) == (2, 1, 50)
    assert nxt.expires_at_ms() == 50 + DEFAULT_LEASE_TTL_MS


# ── disposition telemetry ──────────────────────────────────────────────────────


def test_disposition_event_is_structured_and_single_line():
    lines = []

    class _Logger:
        def bind(self, **_kwargs):
            return self

        def info(self, message):
            lines.append(message)

    event = emit_disposition(
        _Logger(),
        disposition=Disposition.SCRAP,
        reason="duplicate_connect_superseded",
        device_id="aa:bb",
        assignment_id="assign-1",
        session_id="sess-1",
        session_epoch=4,
    )
    assert event["disposition"] == "scrap"
    assert event["reason"] == "duplicate_connect_superseded"
    assert len(lines) == 1 and "\n" not in lines[0]
    payload = json.loads(lines[0].split("lesson_disposition ", 1)[1])
    assert payload["event"] == "lesson_disposition"
    assert payload["sessionEpoch"] == 4


def test_disposition_emit_never_raises():
    class _Exploding:
        def bind(self, **_kwargs):
            raise RuntimeError("logger is down")

    event = emit_disposition(
        _Exploding(), disposition=Disposition.RESTOCK, reason="clean_resume"
    )
    assert event["disposition"] == "restock"


@pytest.mark.parametrize(
    "state,expected",
    [
        ("COMPLETED", Disposition.RESTOCK),
        ("FAILED", Disposition.RESTOCK),
        ("IDLE", Disposition.REFURBISH),
        ("PRELOADING", Disposition.REFURBISH),
        ("READY", Disposition.REFURBISH),
        ("RUNNING", Disposition.SCRAP),
        ("PAUSED", Disposition.SCRAP),
    ],
)
def test_runtime_teardown_disposition_by_state(state, expected):
    """Mid-flight teardown is the only case that counts as lost inventory."""
    from core.lesson.runtime import LessonRuntime

    runtime = LessonRuntime.__new__(LessonRuntime)
    runtime.state = state
    disposition, reason = runtime._teardown_disposition()
    assert disposition is expected
    assert state.lower() in reason


def test_runtime_teardown_disposition_emits_once_with_identity():
    from core.lesson.runtime import LessonRuntime

    lines = []

    class _Logger:
        def bind(self, **_kwargs):
            return self

        def info(self, message):
            lines.append(message)

    runtime = LessonRuntime.__new__(LessonRuntime)
    runtime.state = "RUNNING"
    runtime.logger = _Logger()
    runtime.assignment_id = "assign-9"
    runtime.session_id = "sess-9"
    runtime.conn = type(
        "C", (), {"device_id": "aa:bb", "liveness_lease": Lease("aa:bb", session_epoch=6)}
    )()

    runtime._emit_teardown_disposition()
    assert len(lines) == 1
    payload = json.loads(lines[0].split("lesson_disposition ", 1)[1])
    assert payload["disposition"] == "scrap"
    assert payload["assignmentId"] == "assign-9"
    assert payload["sessionEpoch"] == 6
    assert payload["runtimeState"] == "RUNNING"


@pytest.mark.asyncio
async def test_supersede_emits_a_scrap_disposition(monkeypatch):
    events = []
    import core.websocket_server as ws_module

    harness = ReconnectStormHarness(monkeypatch)
    real_emit = ws_module.WebSocketServer._scrap_superseded_connection

    def _capture(self, superseded, winner, device_id):
        events.append(device_id)
        return real_emit(self, superseded, winner, device_id)

    monkeypatch.setattr(
        ws_module.WebSocketServer, "_scrap_superseded_connection", _capture
    )
    await harness.connect()
    await harness.connect()
    assert events == [harness.device_id]
    await harness.shutdown()


# ── transcript parity ──────────────────────────────────────────────────────────


def test_transcript_matches_canonical_happy_thread():
    """Pin the vendored transcript to the cross-repo canonical fixture."""
    canonical = _canonical_fixture()
    if canonical is None:
        pytest.skip("robot fixture unavailable (single-repo checkout)")
    expected = [
        {"type": row["type"], "sequence": row["sequence"], "stepId": row.get("stepId")}
        for row in canonical["happyThread"]
        if row["stream"] in ("S->F", "ESP-synth")
    ]
    assert load_transcript() == expected


def _canonical_fixture():
    relative = os.path.join(
        "docs",
        "stories",
        "US-006-learning-course-runtime",
        "fixtures",
        "lesson-protocol.v1.json",
    )
    worktree_root = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "..")
    )
    candidates = [worktree_root, os.path.dirname(worktree_root)]
    configured = os.environ.get("TBOT_ROBOT_REPO")
    if configured:
        candidates.insert(0, os.path.abspath(configured))
    git_file = os.path.join(worktree_root, ".git")
    if os.path.isfile(git_file):
        with open(git_file) as handle:
            gitdir = handle.read().strip().removeprefix("gitdir:").strip()
        marker = os.sep + "esp32-server" + os.sep + ".git" + os.sep
        if marker in gitdir:
            candidates.append(os.path.dirname(gitdir.split(marker, 1)[0] + os.sep + "esp32-server"))
    for root in candidates:
        path = os.path.join(root, relative)
        if os.path.isfile(path):
            with open(path) as handle:
                return json.load(handle)
    return None
