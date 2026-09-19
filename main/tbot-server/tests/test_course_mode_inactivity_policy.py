"""Variant B: a course-mode-aware inactivity policy that answers S17 run09 N2'.

S17 run09 D3 (`06c7b497`) correctly stopped the legacy per-step child-inactivity timer
from arming under a live course session - it was pausing healthy RUNNING lessons and
forwarding `lesson_abandoned(child_inactive)` with no `lesson_stop` and no reward. Its
consequence (run09 N2', PARTIAL case C11) is that nothing then ends a course session
whose child never speaks: the assignment stays RUNNING indefinitely.

The replacement proved here differs from the legacy timer in exactly the three ways that
made the legacy one wrong for course mode:

1. it arms on the COURSE child's turn (the assessment window), never on a step ack;
2. it can never fire while audio or clip playout is in flight - T15's S4 ("nothing else
   may take talking off the screen while that audio is still playing") and S1/S2 (the
   turn begins at the real audio boundary), so an in-flight playout restarts the window;
3. it does not pause - it ends the session through the real terminal path: T18's D4 exit
   dispatch, then the documented CANCELLED `lesson_stop`, exactly once.

Measured: 7 of these 9 cases fail on the Variant A tree (no policy exists) and all 9 pass
here. The two that pass on both are the D3 invariants at the end - D3 is preserved, not
reverted: the legacy per-step timer is still never armed under course mode, and non-course
renderers keep their legacy window and timer untouched.
"""
import asyncio
from types import SimpleNamespace

import pytest

from core.lesson.course_orchestrator import SessionState
from core.lesson.runtime import S_RUNNING
from tests.test_google_live_course_playout import receipt, setup_playout, start
from tests.test_lesson_cinematic_phase_routing import (
    _ack_prepare_and_start,
    _activate_v5,
    _frames,
    _v5_runtime,
)


class _FakeCourseMode(SimpleNamespace):
    """The minimum surface the live-course terminal paths touch."""

    def pending_evidence_batches(self):
        return []


def _attach_course_mode(runtime, *, timeout_sec):
    runtime.course_mode = _FakeCourseMode(
        orchestrator=SimpleNamespace(
            active_activity_id=runtime._step_id,
            session_state=SessionState.WORD_ACTIVE,
        ),
        _completion_stop_dispatched=False,
    )
    runtime.conn.config["lesson"]["course_inactivity_timeout_sec"] = timeout_sec
    return runtime


def _course_runtime(*, timeout_sec=0.02):
    runtime = _v5_runtime()
    runtime.state = S_RUNNING
    _activate_v5(runtime, 0)
    runtime._child_response_window_open = False
    runtime._step_visuals_ready = False
    runtime._child_response_timeout_sec = lambda: 0.01
    runtime._max_child_response_timeouts = lambda: 1
    return _attach_course_mode(runtime, timeout_sec=timeout_sec)


def _events(runtime, type_):
    return [
        event for batch in runtime.forwarder.batches for event in batch.get("events", [])
        if event.get("type") == type_
    ]


def _stop_frames(runtime):
    return [frame for frame in _frames(runtime) if frame.get("type") == "lesson_stop"]


async def _settle(seconds=0.3):
    await asyncio.sleep(seconds)


# ── 1. the policy exists and arms only on the child's turn ────────────────────


@pytest.mark.asyncio
async def test_policy_arms_on_the_course_childs_turn_and_not_on_a_step_ack():
    runtime = _course_runtime()

    # Step entry is D3 territory: still no legacy window, still no legacy timer, and
    # the course policy does not arm here either - a step ack is not the child's turn.
    await runtime._continue_after_step_visuals(runtime._step_id, runtime._step_seq)
    assert runtime._child_response_timeout_task is None
    assert runtime._child_response_window_open is False
    assert runtime._course_inactivity_task is None

    assert await runtime._open_course_assessment_window(runtime._course_assessment_generation)
    assert runtime._course_inactivity_task is not None
    runtime._cancel_course_inactivity_timeout()


@pytest.mark.asyncio
async def test_answering_the_turn_retires_the_policy_without_a_terminal_stop():
    runtime = _course_runtime()
    await runtime._open_course_assessment_window(runtime._course_assessment_generation)
    assert runtime._course_inactivity_task is not None

    # A course answer closes the child's turn (course tools -> response-plan dispatch).
    runtime._course_assessment_generation += 1
    runtime._close_course_assessment_window()

    assert runtime._course_inactivity_task is None
    await _settle()
    assert runtime.state == S_RUNNING
    assert _stop_frames(runtime) == []
    assert _events(runtime, "lesson_abandoned") == []


# ── 2. it can never fire while playout is in flight (T15 S1/S2/S4) ────────────


@pytest.mark.asyncio
async def test_policy_cannot_end_a_session_while_the_device_is_still_playing_audio():
    """S4: the talking clip owns the screen until the real audio boundary.

    The inactivity window here (20 ms) is far shorter than the time the test spends in
    the playout, so a policy that ignored playout would stop the lesson mid-sentence.
    """
    runtime, conn, provider, bridge = setup_playout()
    token = await start(runtime, conn, bridge)
    assert provider.accept_lesson_audio_drain_ack(receipt(conn, token, "start"))
    await asyncio.sleep(0)
    await _ack_prepare_and_start(runtime, 1)
    assert runtime._cinematic_phase["phaseId"] == "teach"
    assert runtime._course_playout_active
    _attach_course_mode(runtime, timeout_sec=0.02)

    assert await runtime._open_course_assessment_window(runtime._course_assessment_generation)
    assert runtime._course_playout_in_flight() is True
    await _settle(0.4)  # ~20 inactivity windows

    # The talking clip is still on the screen and the lesson is still RUNNING.
    assert runtime._cinematic_phase["phaseId"] == "teach"
    assert runtime.state == S_RUNNING
    assert _stop_frames(runtime) == []
    assert runtime._course_inactivity_task is not None
    assert not runtime._course_inactivity_task.done()

    runtime._cancel_course_inactivity_timeout()
    await runtime.close()


@pytest.mark.asyncio
async def test_a_deferred_visual_intent_also_counts_as_playout_in_flight():
    """T15's deferred phase belongs to a response the device has not finished."""
    runtime = _course_runtime(timeout_sec=0.02)
    await runtime._open_course_assessment_window(runtime._course_assessment_generation)

    runtime._course_playout_deferred_phase = ("thinking", runtime._step_id)
    assert runtime._course_playout_in_flight() is True
    await _settle(0.4)
    assert runtime.state == S_RUNNING
    assert _stop_frames(runtime) == []

    runtime._cancel_course_inactivity_timeout()


# ── 3. an abandoned session ends cleanly through the real terminal path ───────


@pytest.mark.asyncio
async def test_an_abandoned_course_session_ends_through_the_terminal_path_exactly_once():
    runtime = _course_runtime(timeout_sec=0.02)

    assert await runtime._open_course_assessment_window(runtime._course_assessment_generation)
    await _settle()

    # Not paused: the D3 defect (S_PAUSED + lesson_abandoned + no stop) must not return.
    assert runtime.state != "PAUSED"
    assert runtime._terminal_requested is True
    stops = _stop_frames(runtime)
    assert len(stops) == 1, stops
    assert stops[0]["body"]["reason"] == "CANCELLED"
    # The child's turn is retired, so a late answer cannot re-open a closing session.
    assert runtime.course_assessment_window_open is False
    assert runtime._course_inactivity_task is None
    assert runtime._course_inactivity_closed is True

    # Exactly one terminal stop: a backend-terminal rejection arriving now adds none.
    assert await runtime.on_backend_assignment_terminal(
        {"assignmentId": runtime.assignment_id}, None
    ) is False
    assert len(_stop_frames(runtime)) == 1


@pytest.mark.asyncio
async def test_the_abandoned_close_plays_the_bound_exit_clip_first():
    """T18 D4's exit dispatch: the scene closes the way a completed lesson closes."""
    runtime, conn, provider, bridge = setup_playout()
    # A whole response plays out on the device and ends at its real audio boundary,
    # which is where the child's turn actually begins (T15 S1/S2).
    token = await start(runtime, conn, bridge)
    assert provider.accept_lesson_audio_drain_ack(receipt(conn, token, "start"))
    await asyncio.sleep(0)
    await _ack_prepare_and_start(runtime, 1)
    assert runtime._cinematic_phase["phaseId"] == "teach"
    await bridge._send_tts_message("stop")
    assert provider.accept_lesson_audio_drain_ack(receipt(conn, token, "stop", 400))
    await asyncio.sleep(0)
    await _ack_prepare_and_start(runtime, 3)
    assert runtime._cinematic_phase["phaseId"] == "listen"
    assert runtime._course_playout_in_flight() is False

    _attach_course_mode(runtime, timeout_sec=0.02)
    played = []
    original = runtime._play_exit_phase_before_stop

    async def _record():
        played.append(runtime._current_cinematic_activity_id())
        return await original()

    runtime._play_exit_phase_before_stop = _record
    assert await runtime._open_course_assessment_window(runtime._course_assessment_generation)
    # The device acknowledges the listening clip the window queued: it is alive and
    # idle, only the child is silent. Until it does, that clip command is playout in
    # flight and the policy deliberately waits for it.
    await _ack_prepare_and_start(runtime, 5)
    await _settle(0.5)

    assert played, "the exit phase must be dispatched before the terminal stop"
    assert len(_stop_frames(runtime)) == 1
    await runtime.close()


@pytest.mark.asyncio
async def test_the_policy_never_fires_after_the_session_is_already_terminal():
    runtime = _course_runtime(timeout_sec=0.05)
    await runtime._open_course_assessment_window(runtime._course_assessment_generation)

    await runtime.stop()
    before = len(_stop_frames(runtime))
    await _settle(0.4)

    assert len(_stop_frames(runtime)) == before
    assert runtime._course_inactivity_closed is False


# ── 4. the D3 invariants still hold (these pass on both variants) ─────────────


@pytest.mark.asyncio
async def test_the_legacy_per_step_timer_is_still_never_armed_under_course_mode():
    runtime = _course_runtime()

    runtime._start_child_response_timeout()

    assert runtime._child_response_timeout_task is None
    await _settle(0.2)
    assert runtime.state == S_RUNNING
    assert _events(runtime, "lesson_abandoned") == []


@pytest.mark.asyncio
async def test_non_course_v5_keeps_the_legacy_window_and_timer():
    runtime = _course_runtime()
    runtime.course_mode = None

    await runtime._continue_after_step_visuals(runtime._step_id, runtime._step_seq)

    assert runtime._child_response_window_open is True
    assert runtime._child_response_timeout_task is not None
    assert getattr(runtime, "_course_inactivity_task", None) is None
    runtime._cancel_child_response_timeout()
