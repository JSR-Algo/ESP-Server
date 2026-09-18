"""S17 run09 D3: a renderer-v5 course-mode lesson must not be paused by the legacy per-step
child-inactivity timer while the course session (orchestrator + course tools) is live.

Observed on the owned stack (C01 control): after each semantic step entered, the runtime
opened the legacy child-response window and armed the 12s inactivity timer; the course
driver kept answering through course_observe_child / response plans, the timer fired
twice, the runtime went S_PAUSED and forwarded lesson_abandoned(child_inactive) while the
orchestrator ran on to COMPLETE, so no lesson_stop / lesson_completed was ever sent.
"""
import asyncio
from types import SimpleNamespace

import pytest

from core.lesson.runtime import S_RUNNING
from tests.test_lesson_cinematic_phase_routing import _activate_v5, _v5_runtime


def _course_runtime():
    runtime = _v5_runtime()
    runtime.state = S_RUNNING
    _activate_v5(runtime, 0)
    runtime.course_mode = SimpleNamespace(
        orchestrator=SimpleNamespace(active_activity_id=runtime._steps[0]["id"]),
    )
    runtime._child_response_window_open = False
    runtime._step_visuals_ready = False
    runtime._child_response_timeout_sec = lambda: 0.01
    runtime._max_child_response_timeouts = lambda: 1
    return runtime


def _abandoned(runtime):
    return [
        event for batch in runtime.forwarder.batches for event in batch.get("events", [])
        if event.get("type") == "lesson_abandoned"
    ]


@pytest.mark.asyncio
async def test_course_mode_step_entry_does_not_arm_legacy_inactivity_timer():
    runtime = _course_runtime()

    await runtime._continue_after_step_visuals(runtime._step_id, runtime._step_seq)

    assert runtime._child_response_timeout_task is None
    assert runtime._child_response_window_open is False
    await asyncio.sleep(0.05)
    assert runtime.state == S_RUNNING
    assert _abandoned(runtime) == []


@pytest.mark.asyncio
async def test_course_mode_never_arms_legacy_inactivity_timer_from_other_paths():
    runtime = _course_runtime()

    runtime._start_child_response_timeout()

    assert runtime._child_response_timeout_task is None
    await asyncio.sleep(0.05)
    assert runtime.state == S_RUNNING
    assert _abandoned(runtime) == []


@pytest.mark.asyncio
async def test_non_course_v5_step_entry_keeps_legacy_inactivity_pause():
    runtime = _course_runtime()
    runtime.course_mode = None

    await runtime._continue_after_step_visuals(runtime._step_id, runtime._step_seq)

    # Legacy (non-course) v5 keeps its per-step window + inactivity timer; the pause
    # itself is covered by test_lesson_runtime's child-inactivity regressions.
    assert runtime._child_response_window_open is True
    assert runtime._child_response_timeout_task is not None
    runtime._cancel_child_response_timeout()
