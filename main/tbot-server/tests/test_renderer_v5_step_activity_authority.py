"""Renderer-v5 phase-bound clips must follow the served step's activity, not a lagging orchestrator.

S18 run12 (real cpr-s03-final52 wire, w01-greetings-politeness v5, simulated device answering through the internal
child-response route): the Course Mode orchestrator's ``active_activity_id`` stayed on the first activity while the
runtime's steps advanced, ``_current_cinematic_activity_id`` preferred the orchestrator, so every step entry bound the
FIRST activity's clips and, at completion, the ``exit`` clip bound to the LAST activity was not found:
``_play_exit_phase_before_stop`` returned False, ``_maybe_finish_step`` returned silently, no ``lesson_stop`` was ever
sent and the assignment stayed RUNNING until the peer-silence watchdog scrapped it.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from core.lesson.runtime import S_RUNNING
from tests.test_lesson_cinematic_phase_routing import (
    _ack_prepare_and_start,
    _activate_v5,
    _frames,
    _phase_bound_runtime,
)


def _lagging_orchestrator_runtime():
    runtime = _phase_bound_runtime()
    first, last = runtime._steps[0]["id"], runtime._steps[1]["id"]
    runtime._entrance_completed = True
    # Course Mode present, its orchestrator stuck on the first activity (steps advanced through the
    # passive dwell / internal child-response path, which never observes the orchestrator).
    runtime.course_mode = SimpleNamespace(
        orchestrator=SimpleNamespace(active_activity_id=first),
        tool_context=lambda: None,
        pending_evidence_batches=lambda: [],
        restore_pending_evidence_batches=lambda batches: None,
    )
    _activate_v5(runtime, 0)
    runtime._step_seq = 20
    runtime._semantic_step_sequence = 20
    runtime._cinematic_phase = runtime._layered_cinematic_activity_phases[first]["listen"]
    runtime._cinematic_phase_started_at = asyncio.get_running_loop().time()
    return runtime, first, last


@pytest.mark.asyncio
async def test_v5_step_entry_binds_the_entered_steps_activity_clips() -> None:
    runtime, first, last = _lagging_orchestrator_runtime()
    runtime._step_completed = True

    await runtime._emit_step()          # enter the second step through the real path
    await asyncio.sleep(0.05)           # the step-entry clip sequence runs as a task
    prepare, start = await _ack_prepare_and_start(runtime, 1)

    assert runtime._step_id == last
    assert runtime._current_cinematic_activity_id() == last
    assert prepare["body"]["cinematicPhase"]["phaseId"] == "listen"
    assert prepare["body"]["cinematicPhase"]["layers"][1]["sdPath"].endswith(f"{last}.robot.listen%40v1")


@pytest.mark.asyncio
async def test_v5_completion_after_the_last_step_plays_that_steps_exit_and_stops() -> None:
    runtime, first, last = _lagging_orchestrator_runtime()
    runtime._step_completed = True
    await runtime._emit_step()
    await asyncio.sleep(0.05)
    await _ack_prepare_and_start(runtime, 1)
    await asyncio.sleep(0.05)
    assert runtime.state == S_RUNNING and runtime._step_id == last

    runtime._step_completed = True      # the last step's step_completed arrived (passive dwell / accepted response)
    finishing = asyncio.create_task(runtime._maybe_finish_step())
    await asyncio.sleep(0)
    prepare, start = await _ack_prepare_and_start(runtime, 3)
    assert prepare["body"]["cinematicPhase"]["phaseId"] == "exit"
    assert prepare["body"]["cinematicPhase"]["layers"][1]["sdPath"].endswith(f"{last}.robot.exit%40v1")
    await asyncio.wait_for(finishing, timeout=5.0)
    stop = _frames(runtime)[-1]
    assert stop["type"] == "lesson_stop"
    assert stop["body"]["reason"] == "COMPLETED"
    assert stop["body"]["cinematicPhase"]["phaseId"] == "exit"
