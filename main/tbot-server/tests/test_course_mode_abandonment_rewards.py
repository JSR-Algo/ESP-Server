"""D3 (owner-decisions-20260919.md): an abandonment close grants ZERO rewards.

The owner's decision: "A `CANCELLED` close grants no reward. The campaign has
repeatedly refused to infer mastery from assignment state; an abandoned lesson is the
clearest case of that. This does not change the one-reward-per-completion invariant
for lessons that actually complete."

A reward is not granted by this runtime - it is granted by the backend, and only on a
`lesson_completed` row. `src/lessons/lesson-event-ingest.service.ts` grants exactly on
the LESSON_COMPLETED projection (`rewardService.grantCompletion`); a `lesson_abandoned`
row whose `reason` is NOT `child_inactive` projects the assignment to CANCELLED and
grants nothing. So "zero rewards" is proved here, on the wire the runtime produces, by
three facts about the abandonment close:

1. it emits exactly one `lesson_stop` and its reason is `CANCELLED`, never `COMPLETED`;
2. once the device acks that stop, the runtime forwards exactly one `lesson_abandoned`
   and ZERO `lesson_completed` - so nothing the backend rewards is ever posted;
3. that `lesson_abandoned` carries `reason: "cancelled"`, NOT `child_inactive`. This is
   load-bearing and easy to get wrong: the backend PAUSES on `child_inactive` (the
   legacy S17-D3 shape, which left the assignment parked and unrewarded but also
   un-closed) and CANCELS on anything else. The abandonment close must take the
   CANCEL branch, which is what closes the RUNNING-forever hole.

The last case pins the invariant D3 explicitly says it does not change: a course
lesson that actually completes still emits exactly one `lesson_completed` - one
reward, never two, never zero.
"""
import asyncio

import pytest

from core.lesson.course_orchestrator import SessionState
from tests.test_course_mode_inactivity_policy import _course_runtime, _settle, _stop_frames
from tests.test_lesson_choreography_lifecycle import _control_ack

# The backend's PAUSE trigger. An abandonment close must never carry it, or the
# assignment parks in PAUSED (the S17-D3 shape) instead of leaving RUNNING.
BACKEND_PAUSE_REASON = "child_inactive"


def _terminal_events(runtime, type_):
    return [
        event
        for batch in runtime.forwarder.batches
        for event in batch.get("events", [])
        if event.get("type") == type_
    ]


async def _abandon(runtime, *, inbound=1):
    """Drive the Variant B abandonment close and ack its stop, as the device would."""
    assert await runtime._open_course_assessment_window(runtime._course_assessment_generation)
    await _settle()
    assert runtime._course_inactivity_closed is True, "the policy never fired"
    stops = _stop_frames(runtime)
    assert len(stops) == 1, stops
    assert stops[0]["body"]["reason"] == "CANCELLED"
    await runtime.on_lesson_ack(_control_ack(runtime, stops[0], inbound))
    return stops[0]


@pytest.mark.asyncio
async def test_an_abandonment_close_forwards_no_completion_and_therefore_no_reward():
    runtime = _course_runtime(timeout_sec=0.02)

    await _abandon(runtime)

    # The only row the backend grants a reward on is lesson_completed. There is none.
    assert _terminal_events(runtime, "lesson_completed") == []
    abandoned = _terminal_events(runtime, "lesson_abandoned")
    assert len(abandoned) == 1, abandoned
    # ... and it takes the backend's CANCEL branch, not its PAUSE branch.
    assert abandoned[0]["reason"] != BACKEND_PAUSE_REASON
    assert abandoned[0]["reason"] == "cancelled"
    assert runtime.state == "COMPLETED"  # runtime-terminal, not lesson-completed


@pytest.mark.asyncio
async def test_progress_made_before_the_child_left_does_not_earn_a_partial_reward():
    """No mastery is inferred from how far the abandoned session got."""
    runtime = _course_runtime(timeout_sec=0.02)
    runtime._steps_completed = 7

    await _abandon(runtime)

    assert _terminal_events(runtime, "lesson_completed") == []
    assert len(_terminal_events(runtime, "lesson_abandoned")) == 1
    assert len(_stop_frames(runtime)) == 1


@pytest.mark.asyncio
async def test_a_replayed_ack_and_a_late_operator_cancel_add_no_reward_path():
    runtime = _course_runtime(timeout_sec=0.02)

    await _abandon(runtime)
    before = len(_terminal_events(runtime, "lesson_abandoned"))

    # A late device replay of the same stop ack.
    await runtime.on_lesson_ack(_control_ack(runtime, _stop_frames(runtime)[0], 2))
    # An operator cancel landing after the policy already closed the session.
    assert await runtime.on_backend_assignment_terminal(
        {"assignmentId": runtime.assignment_id}, None
    ) is False
    await asyncio.sleep(0.05)

    assert len(_stop_frames(runtime)) == 1
    assert len(_terminal_events(runtime, "lesson_abandoned")) == before
    assert _terminal_events(runtime, "lesson_completed") == []


@pytest.mark.asyncio
async def test_one_reward_per_completion_is_unchanged_for_a_lesson_that_completes():
    """D3's explicit carve-out: a lesson that actually completes still earns one."""
    runtime = _course_runtime(timeout_sec=60.0)
    runtime.course_mode.orchestrator.session_state = SessionState.COMPLETE

    assert await runtime._complete_course_mode_close() is True
    stops = _stop_frames(runtime)
    assert len(stops) == 1
    assert stops[0]["body"]["reason"] == "COMPLETED"

    await runtime.on_lesson_ack(_control_ack(runtime, stops[0], 1))
    # Replaying the ack must not produce a second completion (a second reward).
    await runtime.on_lesson_ack(_control_ack(runtime, stops[0], 2))

    assert len(_terminal_events(runtime, "lesson_completed")) == 1
    assert _terminal_events(runtime, "lesson_abandoned") == []
    assert runtime._course_inactivity_closed is False
