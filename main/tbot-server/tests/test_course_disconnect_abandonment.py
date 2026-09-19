"""D8: a robot that goes away mid-lesson must not leave a RUNNING-forever assignment.

Owner decision D8 (`coordination/owner-decisions-20260919.md`, "D8 — abandonment must
also be handled on the disconnect path"), on evidence from
`coordination/course-inactivity-timer-observability-20260919/`:

> The sweep exposed a hole no timeout value can close: **when the robot goes away, the
> assignment never closes.** ... A robot that disappears mid-lesson must end its
> assignment through the same terminal contract, with no reward, and must not depend on
> an ack that can never arrive.

The four properties asserted here, in the order they matter:

1. **It closes, and through the same contract.** One `lesson_abandoned` batch, same
   envelope every other terminal uses, posted to the backend - not emitted at a device.
2. **It does not depend on an ack.** The post goes out with no device present at all.
   `reason` is NOT `child_inactive`, which the backend treats as a pause and which would
   turn a RUNNING-forever hole into a PAUSED-forever one.
3. **A reconnect resumes rather than dies.** A runtime claiming the same
   `(assignmentId, sessionId)` inside the grace window retires the close.
4. **It loses a genuine-completion race on purpose.** The pre-fire read-back skips an
   assignment that is already terminal, so a disconnect racing a real completion yields
   one terminal record and one reward - the completion's.

The counterpart fix, in `core/connection.py`: a FIRED inactivity timer must not silently
disarm the peer-silence watchdog. Covered at the bottom.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from core.lesson.course_inactivity_policy import (
    COURSE_INACTIVITY_TIMEOUT_DEFAULT_SEC,
    COURSE_INACTIVITY_TIMEOUT_MIN_SEC,
    PEER_SILENCE_TIMEOUT_SHIPPED_DEFAULT_SEC,
)
from core.lesson.disconnect_abandonment import (
    DISCONNECT_ABANDONMENT_GRACE_CONFIG_KEY,
    DISCONNECT_ABANDONMENT_GRACE_DEFAULT_SEC,
    DISCONNECT_ABANDONMENT_GRACE_ENV_VAR,
    DISCONNECT_ABANDONMENT_GRACE_MAX_SEC,
    DISCONNECT_ABANDONMENT_GRACE_MIN_SEC,
    DISCONNECT_ABANDONMENT_REASON,
    DisconnectAbandonmentGraceConfigError,
    DisconnectAbandonmentIntent,
    DisconnectAbandonmentReaper,
    assert_disconnect_abandonment_grace_in_range,
    build_disconnect_abandonment_batch,
    disconnect_abandonment_grace_sec,
    get_disconnect_abandonment_reaper,
    reset_disconnect_abandonment_reaper,
)
from core.lesson.runtime import S_COMPLETED, S_PAUSED, S_RUNNING
from tests.test_course_mode_inactivity_policy import _course_runtime


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _intent(clock, *, grace=90.0, assignment="a-1", session="s-1"):
    return DisconnectAbandonmentIntent(
        assignment_id=assignment,
        session_id=session,
        device_id="14:c1:9f:d1:a8:48",
        base_url="http://backend/v1",
        token="t",
        batch=build_disconnect_abandonment_batch(
            assignment_id=assignment, lesson_id="l-1", lesson_version=5,
            session_id=session, step_id="w01.a03", step_type="model",
            abandoned_at="2026-09-19T12:00:00.000Z",
        ),
        deadline=clock() + grace,
    )


def _reaper(clock, *, assignment=None, post=None):
    posted = []

    async def _post(intent):
        if post is not None:
            await post(intent)
        posted.append(intent.batch)

    async def _assignment(intent):
        return assignment

    reaper = DisconnectAbandonmentReaper(
        clock=clock, post_fn=_post, assignment_fn=_assignment,
    )
    return reaper, posted


@pytest.fixture(autouse=True)
def _isolate_process_reaper():
    reset_disconnect_abandonment_reaper(None)
    yield
    reset_disconnect_abandonment_reaper(None)


# ── 1. it closes, through the same terminal contract, with no ack ─────────────


@pytest.mark.asyncio
async def test_a_gone_robot_is_closed_after_the_grace_window() -> None:
    clock = _Clock()
    reaper, posted = _reaper(clock, assignment={"id": "a-1", "state": "RUNNING"})
    reaper.register(_intent(clock, grace=90.0))

    clock.advance(89.0)
    await reaper.drain()
    assert posted == []  # still inside the grace window

    clock.advance(2.0)
    await reaper.drain()

    assert len(posted) == 1
    assert posted[0]["events"] == [{
        "type": "lesson_abandoned",
        "reason": DISCONNECT_ABANDONMENT_REASON,
        "abandonedAt": "2026-09-19T12:00:00.000Z",
        "stepId": "w01.a03",
        "stepType": "model",
    }]
    assert reaper.counters["closed"] == 1
    assert reaper.pending_keys() == ()


def test_the_stored_reason_is_never_child_inactive() -> None:
    """The backend PAUSES on child_inactive and CANCELS on anything else.

    Storing `child_inactive` here would convert a RUNNING-forever hole into a
    PAUSED-forever one, which is the single mistake this close must not make.
    """
    batch = build_disconnect_abandonment_batch(
        assignment_id="a", lesson_id="l", lesson_version=1, session_id="s",
    )
    assert batch["events"][0]["reason"] != "child_inactive"
    assert batch["events"][0]["reason"] == "disconnected"
    assert batch["events"][0]["type"] == "lesson_abandoned"


def test_the_close_carries_no_device_and_needs_no_ack() -> None:
    """The intent holds only values - never the runtime, connection or forwarder.

    That is what makes it survivable: all three are being torn down when it is armed.
    """
    clock = _Clock()
    intent = _intent(clock)
    for value in vars(intent).values():
        assert not hasattr(value, "websocket")
        assert not hasattr(value, "lesson_runtime")


# ── 2. reconnect resumes rather than being killed ─────────────────────────────


@pytest.mark.asyncio
async def test_a_reconnect_inside_the_grace_window_retires_the_close() -> None:
    clock = _Clock()
    reaper, posted = _reaper(clock, assignment={"id": "a-1", "state": "RUNNING"})
    reaper.register(_intent(clock, grace=90.0))

    clock.advance(10.0)
    assert reaper.claim("a-1", "s-1") is True

    clock.advance(1000.0)
    await reaper.drain()

    assert posted == []
    assert reaper.counters["claimed"] == 1
    assert reaper.counters["closed"] == 0


@pytest.mark.asyncio
async def test_a_reconnect_onto_a_different_assignment_does_not_rescue_the_old_one() -> None:
    """Claiming is keyed on the session, not the device: the old session still closes."""
    clock = _Clock()
    reaper, posted = _reaper(clock, assignment={"id": "a-1", "state": "RUNNING"})
    reaper.register(_intent(clock, grace=30.0))

    assert reaper.claim("a-2", "s-2") is False
    clock.advance(31.0)
    await reaper.drain()

    assert len(posted) == 1


@pytest.mark.asyncio
async def test_the_runtime_claims_its_own_session_when_it_starts() -> None:
    runtime = _course_runtime()
    runtime.assignment_id, runtime.session_id = "a-9", "s-9"
    reaper = get_disconnect_abandonment_reaper()
    clock = _Clock()
    reaper._clock = clock
    reaper.register(_intent(clock, assignment="a-9", session="s-9"))
    assert reaper.pending_keys() == (("a-9", "s-9"),)

    runtime._claim_disconnect_abandonment()

    assert reaper.pending_keys() == ()


# ── 3. idempotence: one terminal record, at most one reward ───────────────────


@pytest.mark.asyncio
async def test_registering_twice_arms_one_close_and_keeps_the_earlier_deadline() -> None:
    clock = _Clock()
    reaper, posted = _reaper(clock, assignment={"id": "a-1", "state": "RUNNING"})
    assert reaper.register(_intent(clock, grace=90.0)) is True
    assert reaper.register(_intent(clock, grace=300.0)) is False

    assert len(reaper.pending_keys()) == 1
    clock.advance(91.0)
    await reaper.drain()
    assert len(posted) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["COMPLETED", "CANCELLED", "FAILED"])
async def test_an_already_terminal_assignment_is_skipped_not_closed_again(state) -> None:
    """The genuine-completion race, lost on purpose.

    If a real terminal landed while the grace window ran, the pre-fire read-back sees
    it and the close is dropped. Nothing is posted, so the backend never has to fence
    a second terminal - and the reward that belongs to the completion is untouched.
    """
    clock = _Clock()
    reaper, posted = _reaper(clock, assignment={"id": "a-1", "state": state})
    reaper.register(_intent(clock, grace=10.0))

    clock.advance(11.0)
    await reaper.drain()

    assert posted == []
    assert reaper.counters["skipped_already_terminal"] == 1
    assert reaper.counters["closed"] == 0
    assert reaper.pending_keys() == ()


@pytest.mark.asyncio
async def test_a_released_assignment_slot_is_also_treated_as_already_closed() -> None:
    clock = _Clock()
    reaper, posted = _reaper(clock, assignment=None)
    reaper.register(_intent(clock, grace=10.0))

    clock.advance(11.0)
    await reaper.drain()

    assert posted == []
    assert reaper.counters["skipped_already_terminal"] == 1


@pytest.mark.asyncio
async def test_a_failing_post_retries_a_bounded_number_of_times_then_gives_up() -> None:
    clock = _Clock()

    async def _boom(intent):
        raise RuntimeError("backend down")

    reaper, posted = _reaper(clock, assignment={"id": "a-1", "state": "RUNNING"}, post=_boom)
    reaper._retry_backoff_sec = 1.0
    reaper.register(_intent(clock, grace=1.0))

    for _ in range(10):
        clock.advance(60.0)
        await reaper.drain()

    assert posted == []
    assert reaper.counters["failed"] == 1
    assert reaper.pending_keys() == ()  # bounded: no unbounded intent set on an outage


# ── 4. what the runtime arms, and what it deliberately does not ───────────────


def _armable(runtime):
    runtime.assignment_id, runtime.session_id = "a-7", "s-7"
    runtime.forwarder.base_url = "http://backend/v1"
    runtime.forwarder.device_id = "14:c1:9f:d1:a8:48"
    runtime.forwarder.token = "t"
    return runtime


@pytest.mark.parametrize(
    "state,armed",
    [(S_RUNNING, True), (S_PAUSED, False), (S_COMPLETED, False)],
)
def test_only_a_running_course_session_arms_a_disconnect_close(state, armed) -> None:
    """SCRAP-only, RUNNING-only, course-only - each narrowing is deliberate.

    PAUSED is a state the backend entered on purpose so a returning child can resume;
    converting it to a cancel on a network blip would be the same category of mistake
    as storing `child_inactive`.
    """
    runtime = _armable(_course_runtime())
    runtime.state = state

    runtime._arm_disconnect_abandonment()

    assert bool(get_disconnect_abandonment_reaper().pending_keys()) is armed


def test_a_non_course_lesson_does_not_arm_the_course_disconnect_close() -> None:
    runtime = _armable(_course_runtime())
    runtime.state = S_RUNNING
    runtime.course_mode = None

    runtime._arm_disconnect_abandonment()

    assert get_disconnect_abandonment_reaper().pending_keys() == ()


def test_an_unusable_grace_refuses_to_arm_rather_than_inventing_the_default() -> None:
    runtime = _armable(_course_runtime())
    runtime.state = S_RUNNING
    runtime.conn.config["lesson"][DISCONNECT_ABANDONMENT_GRACE_CONFIG_KEY] = "not-a-number"

    runtime._arm_disconnect_abandonment()

    assert get_disconnect_abandonment_reaper().pending_keys() == ()


@pytest.mark.asyncio
async def test_closing_a_running_course_runtime_arms_the_close_end_to_end() -> None:
    """The real call site: `_close_runtime_resources`, right after the SCRAP disposition."""
    runtime = _armable(_course_runtime())
    runtime.state = S_RUNNING

    await runtime._close_runtime_resources()

    assert get_disconnect_abandonment_reaper().pending_keys() == (("a-7", "s-7"),)


# ── 5. the grace is a real configuration value, and fails closed ──────────────


def test_the_grace_is_named_defaulted_and_derived() -> None:
    assert DISCONNECT_ABANDONMENT_GRACE_CONFIG_KEY == "disconnect_abandonment_grace_sec"
    assert DISCONNECT_ABANDONMENT_GRACE_ENV_VAR == "LESSON_DISCONNECT_ABANDONMENT_GRACE_SEC"
    assert DISCONNECT_ABANDONMENT_GRACE_DEFAULT_SEC == 90.0
    # A gone robot is a stronger signal than a quiet child, so it is not held open
    # longer than D2's child-inactivity close.
    assert DISCONNECT_ABANDONMENT_GRACE_DEFAULT_SEC < COURSE_INACTIVITY_TIMEOUT_DEFAULT_SEC
    # The floor sits at or above the liveness lease TTL the tree already calls stale.
    assert DISCONNECT_ABANDONMENT_GRACE_MIN_SEC >= 45.0
    assert disconnect_abandonment_grace_sec({}) == DISCONNECT_ABANDONMENT_GRACE_DEFAULT_SEC


@pytest.mark.parametrize("raw,expected", [(120, 120.0), ("120", 120.0), (45.5, 45.5)])
def test_a_configured_grace_is_honoured(raw, expected) -> None:
    assert disconnect_abandonment_grace_sec(
        {DISCONNECT_ABANDONMENT_GRACE_CONFIG_KEY: raw}
    ) == expected


@pytest.mark.parametrize("raw", ["abc", "12s", "nan", "inf", "-30", "0", True, [], {}])
def test_an_unparseable_grace_raises_instead_of_becoming_the_default(raw) -> None:
    with pytest.raises(DisconnectAbandonmentGraceConfigError):
        disconnect_abandonment_grace_sec({DISCONNECT_ABANDONMENT_GRACE_CONFIG_KEY: raw})


@pytest.mark.parametrize(
    "value", [1.0, 44.9, DISCONNECT_ABANDONMENT_GRACE_MAX_SEC + 1, 86400.0]
)
def test_an_out_of_range_grace_raises_instead_of_being_clamped(value) -> None:
    with pytest.raises(DisconnectAbandonmentGraceConfigError):
        assert_disconnect_abandonment_grace_in_range(value)


@pytest.mark.parametrize(
    "value",
    [DISCONNECT_ABANDONMENT_GRACE_MIN_SEC, 90.0, DISCONNECT_ABANDONMENT_GRACE_MAX_SEC],
)
def test_an_in_range_grace_passes_unchanged(value) -> None:
    assert assert_disconnect_abandonment_grace_in_range(value) == value


def test_the_env_override_reaches_the_lesson_config_without_a_rebuild(monkeypatch) -> None:
    import config.config_loader as config_loader

    monkeypatch.setenv(DISCONNECT_ABANDONMENT_GRACE_ENV_VAR, "120")
    applied = config_loader._apply_lesson_env_overrides(
        {"lesson": {"runtime_enabled": True}}
    )

    assert applied["lesson"][DISCONNECT_ABANDONMENT_GRACE_CONFIG_KEY] == 120.0


@pytest.mark.parametrize("raw", ["abc", "0", "-5", "10", "5400", "nan"])
def test_an_unusable_grace_override_refuses_the_boot(raw, monkeypatch) -> None:
    import config.config_loader as config_loader

    monkeypatch.setenv(DISCONNECT_ABANDONMENT_GRACE_ENV_VAR, raw)
    with pytest.raises(ValueError):
        config_loader._apply_lesson_env_overrides({"lesson": {"runtime_enabled": True}})


# ── 6. D2's rider: the configuration MINIMUM, and the watchdog interaction ────


def test_the_configuration_minimum_is_above_the_peer_silence_budget() -> None:
    """D2's first measured rider, and the reason for it.

    A timeout at or below the transport budget fires first, takes the runtime out of
    its armed states, and suppresses the socket close that would otherwise have
    happened - so a short timeout removes a cleanup and replaces it with nothing.
    """
    assert COURSE_INACTIVITY_TIMEOUT_MIN_SEC > PEER_SILENCE_TIMEOUT_SHIPPED_DEFAULT_SEC
    assert COURSE_INACTIVITY_TIMEOUT_MIN_SEC == 90.0
    # The default is untouched: D2 is settled by measurement.
    assert COURSE_INACTIVITY_TIMEOUT_DEFAULT_SEC == 180.0


def _connection_with_runtime(runtime):
    # `core.connection` pulls in `bs4` at import time. That dependency is present in
    # the full ESP runtime (where these four cases run green) but not in the sealed
    # reconciliation harness's standalone interpreter, so these four SKIP there rather
    # than failing on a missing package. Same shape as the pre-existing
    # TBOT-Firmware-checkout skip the reconciliation suite already carries.
    pytest.importorskip("bs4", reason="core.connection imports bs4")
    from core.connection import ConnectionHandler

    conn = SimpleNamespace(lesson_runtime=runtime, sd_pack_sync_task=None)
    conn._lesson_runtime_active = ConnectionHandler._lesson_runtime_active.__get__(conn)
    conn._lesson_terminal_unconfirmed = (
        ConnectionHandler._lesson_terminal_unconfirmed.__get__(conn)
    )
    conn._lesson_peer_silence_watchdog_armed = (
        ConnectionHandler._lesson_peer_silence_watchdog_armed.__get__(conn)
    )
    return conn


def test_a_fired_inactivity_timer_no_longer_disarms_the_peer_silence_watchdog() -> None:
    """The interaction the sweep found, fixed.

    Before: a terminal control moved the runtime out of RUNNING/PRELOADING, so the
    socket-level watchdog disarmed and a half-open socket was never reaped. The window
    between "a terminal was requested" and "the runtime finished closing" is exactly
    when that reaping is most needed.
    """
    runtime = SimpleNamespace(state="FAILED", _terminal_requested=True, _closed=False)
    conn = _connection_with_runtime(runtime)

    assert conn._lesson_runtime_active() is False  # the old arming predicate
    assert conn._lesson_peer_silence_watchdog_armed() is True  # the new one


def test_the_watchdog_still_disarms_once_the_runtime_has_finished_closing() -> None:
    runtime = SimpleNamespace(state="COMPLETED", _terminal_requested=True, _closed=True)
    assert _connection_with_runtime(runtime)._lesson_peer_silence_watchdog_armed() is False


def test_the_watchdog_is_unchanged_for_a_healthy_running_lesson() -> None:
    runtime = SimpleNamespace(state="RUNNING", _terminal_requested=False, _closed=False)
    assert _connection_with_runtime(runtime)._lesson_peer_silence_watchdog_armed() is True


def test_the_watchdog_is_still_disarmed_with_no_lesson_at_all() -> None:
    assert _connection_with_runtime(None)._lesson_peer_silence_watchdog_armed() is False


@pytest.mark.asyncio
async def test_the_reaper_task_fires_on_its_own_without_a_drain() -> None:
    """The loop, not just the test seam: no caller pokes it in production."""
    _record_post = _Recorder()
    reaper = DisconnectAbandonmentReaper(
        post_fn=_record_post, assignment_fn=_none_assignment,
    )
    intent = DisconnectAbandonmentIntent(
        assignment_id="a-live", session_id="s-live", device_id="d",
        base_url="http://backend/v1", token=None,
        batch=build_disconnect_abandonment_batch(
            assignment_id="a-live", lesson_id="l", lesson_version=1, session_id="s-live",
        ),
        deadline=asyncio.get_running_loop().time() - 1,
    )
    reaper._clock = asyncio.get_running_loop().time
    reaper.register(intent)
    for _ in range(50):
        if _record_post.calls:
            break
        await asyncio.sleep(0.01)
    await reaper.aclose()

    assert len(_record_post.calls) == 1


class _Recorder:
    def __init__(self) -> None:
        self.calls = []

    async def __call__(self, intent):
        self.calls.append(intent)


async def _none_assignment(intent):
    return {"id": intent.assignment_id, "state": "RUNNING"}
