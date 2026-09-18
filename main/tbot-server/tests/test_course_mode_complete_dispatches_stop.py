"""S17 run09 D4: a COMPLETE_COURSE decision (the orchestrator moves straight to COMPLETE on the
last activity / a `complete` outcome) must still dispatch the exit phase and lesson_stop.

Observed on the owned stack (C01c/C01d after D3): the scripted driver applied and committed
the final COMPLETE_COURSE response plan, the orchestrator sat in COMPLETE, but
commit_course_response_plan only closes when the orchestrator is CLOSING and
_dispatch_course_mode_close returned True early for COMPLETE, so no lesson_stop /
lesson_completed was ever emitted and the assignment stayed RUNNING.
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.lesson.course_orchestrator import SessionState
from tests.test_lesson_cinematic_phase_routing import _activate_v5, _frames, _phase_bound_runtime


def _completed_runtime():
    runtime = _phase_bound_runtime()
    _activate_v5(runtime, 1)
    runtime.course_mode = SimpleNamespace(
        _completion_stop_dispatched=False,
        orchestrator=SimpleNamespace(session_state=SessionState.COMPLETE, active_activity_id=runtime._step_id),
    )
    runtime.persist_course_mode_snapshot = AsyncMock()
    runtime._play_exit_phase_before_stop = AsyncMock(return_value=True)
    return runtime


@pytest.mark.asyncio
async def test_complete_state_without_dispatched_stop_still_emits_lesson_stop():
    runtime = _completed_runtime()

    assert await runtime._complete_course_mode_close() is True

    stops = [f for f in _frames(runtime) if f["type"] == "lesson_stop"]
    assert len(stops) == 1
    assert stops[0]["body"]["reason"] == "COMPLETED"
    assert runtime.course_mode._completion_stop_dispatched is True
    assert runtime.course_mode.orchestrator.session_state is SessionState.COMPLETE


@pytest.mark.asyncio
async def test_complete_state_with_dispatched_stop_does_not_emit_a_second_stop():
    runtime = _completed_runtime()
    runtime.course_mode._completion_stop_dispatched = True

    assert await runtime._complete_course_mode_close() is True

    assert [f for f in _frames(runtime) if f["type"] == "lesson_stop"] == []


@pytest.mark.asyncio
async def test_commit_of_the_completing_plan_dispatches_the_close():
    runtime = _completed_runtime()
    arguments = {"decisionId": "d-final", "planId": "p-final", "questionCount": 0}
    runtime.course_mode._decisions = {}
    runtime.course_mode.snapshot = lambda: {}
    runtime.course_mode.commit_course_response_plan = lambda a: a is arguments
    runtime._settle_course_embodied_action = AsyncMock(return_value=None)

    assert await runtime.commit_course_response_plan(arguments) is True

    assert len([f for f in _frames(runtime) if f["type"] == "lesson_stop"]) == 1
