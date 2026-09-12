"""S05 review combinations using native protocol fixtures, not physical audio proof."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.lesson.course_orchestrator import SessionState
from core.lesson.runtime import S_PAUSED, S_RUNNING, course_mode_runtime_from_manifest
from tests.test_course_mode_runtime_integration import contract
from tests.test_course_orchestrator import observation
from tests.test_lesson_choreography_lifecycle import _control_ack
from tests.test_lesson_cinematic_phase_routing import (
    _phase_bound_runtime, _activate_v5, _frames, _ack_prepare_and_start,
)


def closing_runtime(trigger='deadline'):
    runtime = _phase_bound_runtime()
    _activate_v5(runtime, 0)
    adapter = course_mode_runtime_from_manifest({'courseModeContract': contract()}, enabled=True,
        clock=lambda: 0.0, runtime_session_id=runtime.session_id)
    original = runtime._step_id
    activity = adapter.orchestrator.active_activity_id
    bound = runtime._layered_cinematic_activity_phases.pop(original)
    runtime._layered_cinematic_activity_phases[activity] = bound
    for phase in bound.values():
        phase['activityIds'] = [activity]
    runtime._step_id = activity
    runtime._step.update(id=activity, activityId=activity)
    runtime.course_mode = adapter
    runtime._entrance_completed = True
    runtime._cinematic_phase = bound['listen']
    runtime.persist_course_mode_snapshot = AsyncMock()
    orchestrator = adapter.orchestrator
    orchestrator.session_state = SessionState.WORD_ACTIVE
    if trigger is None:
        return runtime
    if trigger == 'deadline':
        decision = orchestrator.observe(observation(now_ms=540_000))
    else:
        initial = observation(safety_class='unsafe' if trigger == 'safety' else 'normal', intent='fatigue')
        paused = orchestrator.observe(initial)
        assert paused.next_state in {SessionState.REGULATION_BREAK, SessionState.SAFETY_PAUSED}
        decision = orchestrator.observe(observation(observation_id='stop-choice', intent='stop'))
    assert decision.next_state is SessionState.CLOSING
    assert orchestrator.active_activity_id == activity
    return runtime


@pytest.mark.asyncio
@pytest.mark.parametrize('trigger', ['deadline', 'child', 'safety'])
@pytest.mark.parametrize('paused', [False, True])
async def test_early_close_from_orchestrator_stops_current_phase_once(trigger, paused):
    runtime = closing_runtime(trigger)
    current = runtime._cinematic_phase
    assert runtime._course_cinematic_cue('exit') is None
    if paused:
        await runtime.pause()
        await runtime.on_lesson_ack(_control_ack(runtime, _frames(runtime)[-1], 1))
        assert runtime.state == S_PAUSED
    results = await asyncio.gather(runtime._complete_course_mode_close(), runtime._complete_course_mode_close())
    assert any(results)
    assert runtime.course_mode.orchestrator.session_state is SessionState.COMPLETE
    stops = [frame for frame in _frames(runtime) if frame['type'] == 'lesson_stop']
    assert len(stops) == 1
    assert stops[0]['body']['cinematicPhase']['command'] == 'stop'
    assert stops[0]['body']['cinematicPhase']['phaseId'] == 'listen'
    assert runtime._cinematic_phase is current
    assert not any(frame['type'] == 'lesson_prepare' for frame in _frames(runtime))
    await runtime.close()


@pytest.mark.asyncio
async def test_cancelled_early_close_does_not_emit_completed_stop():
    runtime = closing_runtime()
    await runtime.cancel()
    assert await runtime._complete_course_mode_close() is False
    assert not any(frame['type'] == 'lesson_stop' for frame in _frames(runtime))
    await runtime.close()


@pytest.mark.asyncio
async def test_early_close_retires_queued_phase_before_current_phase_stop():
    runtime = closing_runtime()
    assert runtime._queue_course_cinematic_phase('teach')
    assert await runtime._complete_course_mode_close()
    await asyncio.sleep(0.02)
    assert [frame['type'] for frame in _frames(runtime)] == ['lesson_stop']
    await runtime.close()


@pytest.mark.asyncio
async def test_deadline_response_plan_commit_closes_phase_bound_first_activity():
    runtime = closing_runtime(None)
    runtime.course_mode._clock = lambda: 540.0
    decision = await runtime.course_continue({
        'lessonSessionId': runtime.session_id, 'turnSequenceId': 1, 'observationId': 'deadline',
    })
    assert decision['nextState'] == 'CLOSING'
    plan = {
        'lessonSessionId': runtime.session_id, 'turnSequenceId': 2, 'observationId': 'close-plan',
        'planId': 'close-plan', 'decisionId': decision['decisionId'], 'acknowledgment': 'I hear you.',
        'relation': 'We can stop.', 'guidance': '', 'invitation': '', 'questionCount': 0,
        'embodiedIntent': decision['embodiedIntent'], 'targetFactsUsed': [], 'praiseLevel': 'engagement',
        'safetyMode': False, 'normalMiss': False,
    }
    assert (await runtime.course_apply_response_plan(plan))['accepted']
    assert await runtime.commit_course_response_plan(plan)
    assert runtime.course_mode.orchestrator.session_state == SessionState.COMPLETE
    assert _frames(runtime)[-1]['body']['cinematicPhase']['command'] == 'stop'
    assert _frames(runtime)[-1]['body']['cinematicPhase']['phaseId'] == 'listen'
    await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('paused', [False, True])
async def test_final_course_close_still_requires_successful_bound_exit(paused):
    runtime = _phase_bound_runtime()
    _activate_v5(runtime, 1)
    runtime.course_mode = SimpleNamespace(_completion_stop_dispatched=False,
        orchestrator=SimpleNamespace(session_state=SessionState.CLOSING, active_activity_id=runtime._step_id))
    runtime.persist_course_mode_snapshot = AsyncMock()
    runtime._play_exit_phase_before_stop = AsyncMock(return_value=False)
    if paused:
        runtime.state = S_PAUSED
    assert await runtime._complete_course_mode_close() is False
    assert runtime.course_mode.orchestrator.session_state == SessionState.CLOSING
    assert not _frames(runtime)
    if paused:
        runtime._play_exit_phase_before_stop.assert_not_awaited()
    await runtime.close()


async def paused_playout():
    runtime = _phase_bound_runtime()
    _activate_v5(runtime, 0)
    runtime._entrance_completed = True
    token = dict(assignment_id=runtime.assignment_id, session_id=runtime.session_id,
        activity_id=runtime._step_id, step_sequence=runtime._step_seq, playout_id=1)
    assert runtime.on_course_playout_started(**token)
    await asyncio.sleep(0)
    await _ack_prepare_and_start(runtime, 1)
    await runtime.pause()
    await runtime.on_lesson_ack(_control_ack(runtime, _frames(runtime)[-1], 3))
    assert runtime.state == S_PAUSED
    return runtime, token


@pytest.mark.asyncio
async def test_owned_finish_during_pause_reconciles_listen_once_after_resume():
    runtime, token = await paused_playout()
    before = len(_frames(runtime))
    assert runtime.on_course_playout_finished(**token)
    assert not runtime.on_course_playout_finished(**token)
    assert not runtime._course_playout_active
    await asyncio.sleep(0.02)
    assert len(_frames(runtime)) == before
    await runtime.resume()
    await runtime.on_lesson_ack(_control_ack(runtime, _frames(runtime)[-1], 4))
    await asyncio.sleep(0.02)
    await _ack_prepare_and_start(runtime, 5)
    assert runtime._cinematic_phase['phaseId'] == 'listen'
    assert len(_frames(runtime)) == before + 3
    assert not runtime.on_course_playout_started(**token)
    assert runtime.on_course_playout_started(**{**token, 'playout_id': 2})
    await runtime.close()


@pytest.mark.asyncio
async def test_finish_while_pause_ack_is_pending_waits_for_resume():
    runtime, token = await paused_playout()
    await runtime.resume()
    await runtime.on_lesson_ack(_control_ack(runtime, _frames(runtime)[-1], 4))
    await runtime.pause()
    pause = _frames(runtime)[-1]
    assert runtime.state == S_RUNNING
    assert runtime.on_course_playout_finished(**token)
    count = len(_frames(runtime))
    await asyncio.sleep(0.02)
    assert len(_frames(runtime)) == count
    await runtime.on_lesson_ack(_control_ack(runtime, pause, 5))
    await runtime.resume()
    await runtime.on_lesson_ack(_control_ack(runtime, _frames(runtime)[-1], 6))
    await asyncio.sleep(0.02)
    await _ack_prepare_and_start(runtime, 7)
    assert runtime._cinematic_phase['phaseId'] == 'listen'
    await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('field,value', [
    ('assignment_id', 'old'), ('session_id', 'old'), ('activity_id', 'old'),
    ('step_sequence', 99), ('playout_id', 0), ('playout_id', True),
])
async def test_stale_paused_finish_does_not_consume_owned_playout(field, value):
    runtime, token = await paused_playout()
    assert not runtime.on_course_playout_finished(**{**token, field: value})
    assert runtime._course_playout_active
    assert runtime.on_course_playout_finished(**token)
    await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('replacement', ['activity', 'step', 'session', 'cancel', 'closed'])
async def test_deferred_finish_cannot_cross_ownership_replacement(replacement):
    runtime, token = await paused_playout()
    assert runtime.on_course_playout_finished(**token)
    if replacement == 'activity':
        _activate_v5(runtime, 1)
        runtime.state = S_PAUSED
    elif replacement == 'step':
        runtime._step_seq += 1
    elif replacement == 'session':
        runtime.session_id = 'replacement'
    elif replacement == 'cancel':
        await runtime.cancel()
        await runtime.on_lesson_ack(_control_ack(runtime, _frames(runtime)[-1], 4))
    else:
        await runtime.close()
    before = len(_frames(runtime))
    if replacement not in {'cancel', 'closed'}:
        await runtime.resume()
        await runtime.on_lesson_ack(_control_ack(runtime, _frames(runtime)[-1], 4))
        before += 1
    await asyncio.sleep(0.02)
    assert len(_frames(runtime)) == before
    assert not runtime.on_course_playout_finished(**token)
    await runtime.close()


@pytest.mark.asyncio
async def test_cancel_during_early_close_persistence_cannot_emit_completed_stop():
    runtime = closing_runtime()
    entered, release = asyncio.Event(), asyncio.Event()
    async def persist():
        entered.set()
        await release.wait()
    runtime.persist_course_mode_snapshot = persist
    task = asyncio.create_task(runtime._complete_course_mode_close())
    await entered.wait()
    await runtime.cancel()
    release.set()
    assert not await task
    assert not any(frame['type'] == 'lesson_stop' for frame in _frames(runtime))
    await runtime.close()
