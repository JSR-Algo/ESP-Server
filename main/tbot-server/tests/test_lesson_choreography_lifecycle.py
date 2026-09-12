"""T10 routing/lifetime regressions. Clip fixtures are not real-media acceptance."""
import asyncio
from types import SimpleNamespace
from copy import deepcopy
from unittest.mock import AsyncMock, Mock

import pytest

from core.lesson.layered_cinematic_contract import LayeredCinematicContractError, project_layered_cinematic_phase
from core.lesson.runtime import S_PAUSED, S_RUNNING, _index_layered_cinematic_phases
from tests.test_layered_cinematic_contract import _pack, _phase
from tests.test_lesson_cinematic_phase_routing import (
    _activate_v5, _bound_phase, _phase_bound_runtime, _frames, _v5_ack,
    _ack_prepare_and_start,
)
from core.lesson.course_snapshot_store import MemoryCourseModeSnapshotStore
from core.lesson.runtime import LessonRuntime
from core.lesson.course_orchestrator import CourseDecision, SessionState
from core.lesson.embodied_intent import EmbodiedIntent
from tests.test_course_mode_runtime_integration import _Conn, _Forwarder, contract


def _control_ack(runtime, frame, sequence):
    ack = _v5_ack(runtime, frame, sequence)
    body = ack['body']['cinematicPhase']
    body.pop('phaseReady', None)
    body['event'] = 'commandApplied'
    return ack


def test_duplicate_sd_asset_authority_is_rejected():
    pack = _pack()
    pack['assets'].append(deepcopy(pack['assets'][0]))
    with pytest.raises(LayeredCinematicContractError, match='duplicat'):
        project_layered_cinematic_phase(_phase(), pack)


@pytest.mark.parametrize('missing', ['teach', 'listen', 'thinking', 'celebrate'])
def test_partial_phase_bound_activity_is_rejected(missing):
    phases = [_bound_phase('a1', p, 100) for p in ('teach', 'listen', 'thinking', 'celebrate') if p != missing]
    with pytest.raises(LayeredCinematicContractError, match='incomplete'):
        _index_layered_cinematic_phases(phases)


@pytest.mark.asyncio
async def test_pause_time_does_not_finish_walking_or_release_prompt():
    runtime = _phase_bound_runtime(walk_ms=100)
    _activate_v5(runtime, 0)
    runtime._cinematic_phase = runtime._course_cinematic_cue('walk')
    runtime._note_cinematic_phase_started()
    await runtime.pause()
    await runtime.on_lesson_ack(_control_ack(runtime, _frames(runtime)[-1], 1))
    assert runtime.state == S_PAUSED
    waiter = asyncio.create_task(runtime._await_running_entrance_phase())
    try:
        await asyncio.sleep(0.25)
        assert not waiter.done(), 'paused wall time must not complete the clip'
        await runtime.resume()
        await runtime.on_lesson_ack(_control_ack(runtime, _frames(runtime)[-1], 2))
        assert runtime.state == S_RUNNING
        assert runtime._running_once_phase_remaining_sec() > 0.05
        await asyncio.wait_for(waiter, 1)
    finally:
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)


@pytest.mark.asyncio
async def test_closed_runtime_rejects_queued_phase_without_emitting():
    runtime = _phase_bound_runtime()
    _activate_v5(runtime, 0)
    runtime._closed = True
    assert runtime._queue_course_cinematic_phase('listen') is False
    await asyncio.sleep(0)
    assert _frames(runtime) == []


@pytest.mark.asyncio
async def test_cancelled_prepare_does_not_make_same_phase_look_started():
    runtime = _phase_bound_runtime()
    _activate_v5(runtime, 0)
    assert runtime._queue_course_cinematic_phase('listen')
    await asyncio.sleep(0)
    task = runtime._course_phase_task
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    count = len(_frames(runtime))
    assert runtime._queue_course_cinematic_phase('listen')
    await asyncio.sleep(0.02)
    assert len(_frames(runtime)) == count + 1
    await _ack_prepare_and_start(runtime, 1)


@pytest.mark.asyncio
async def test_duplicate_completion_waits_for_one_exit_and_one_stop():
    runtime = _phase_bound_runtime()
    _activate_v5(runtime, 1)
    runtime._step_completed = True
    runtime._cinematic_phase = runtime._course_cinematic_cue('listen')
    first = asyncio.create_task(runtime._maybe_finish_step())
    second = None
    try:
        await asyncio.sleep(0)
        await _ack_prepare_and_start(runtime, 1)
        second = asyncio.create_task(runtime._maybe_finish_step())
        await asyncio.sleep(0.3)
        stops = [f for f in _frames(runtime) if f['type'] == 'lesson_stop']
        prepares = [f for f in _frames(runtime) if f['type'] == 'lesson_prepare']
        assert len(stops) == 1
        assert len(prepares) == 1
    finally:
        for task in (first, second):
            if task is not None:
                task.cancel()
        await asyncio.gather(*(t for t in (first, second) if t is not None), return_exceptions=True)


@pytest.mark.asyncio
async def test_cancel_during_entrance_cannot_start_next_clip():
    runtime = _phase_bound_runtime(fly_ms=100)
    _activate_v5(runtime, 0)
    runtime._note_cinematic_phase_started()
    runtime._continue_after_step_visuals = AsyncMock()
    runtime._queue_authored_cinematic_sequence(['walk', 'teach'])
    task = runtime._visual_transition_task
    await runtime.cancel()
    await runtime.on_lesson_ack(_control_ack(runtime, _frames(runtime)[-1], 1))
    await asyncio.sleep(0.25)
    assert [f for f in _frames(runtime) if f['type'] == 'lesson_prepare'] == []
    assert task.done()
    runtime._continue_after_step_visuals.assert_not_awaited()


@pytest.mark.asyncio
async def test_decision_dispatch_does_not_claim_audio_playout():
    runtime = _phase_bound_runtime()
    _activate_v5(runtime, 0)
    decision = CourseDecision('d1', True, SessionState.WORD_ACTIVE, 'ADVANCE_ACTIVITY',
        'acknowledge_child', None, None, EmbodiedIntent.PRESENT_CENTER, False, None,
        activity_id=runtime._step_id, visual_state='teach', replay_entrance=False)
    runtime._queue_course_cinematic_phase = Mock()
    await runtime._dispatch_course_embodied_decision(decision)
    assert all(call.args[0] != 'teach' for call in runtime._queue_course_cinematic_phase.call_args_list)


@pytest.mark.asyncio
async def test_phase_entry_holds_walk_until_playout_and_uses_attentive_pose_on_next_activity():
    runtime = _phase_bound_runtime()
    _activate_v5(runtime, 0)
    assert runtime._step_entry_cinematic_effects(runtime._course_cinematic_cue('teach')) == ['walk']
    runtime._entrance_completed = True
    _activate_v5(runtime, 1)
    assert runtime._step_entry_cinematic_effects(runtime._course_cinematic_cue('teach')) == ['listen']


@pytest.mark.asyncio
async def test_verified_playout_hook_rejects_stale_and_duplicate_events():
    runtime = _phase_bound_runtime()
    _activate_v5(runtime, 0)
    runtime._entrance_completed = True
    token = dict(assignment_id=runtime.assignment_id, session_id=runtime.session_id,
                 activity_id=runtime._step_id, step_sequence=runtime._step_seq, playout_id=1)
    assert runtime.on_course_playout_started(**{**token, 'session_id': 'old'}) is False
    assert runtime.on_course_playout_started(**token) is True
    await asyncio.sleep(0)
    await _ack_prepare_and_start(runtime, 1)
    assert runtime._cinematic_phase['phaseId'] == 'teach'
    count = len(_frames(runtime))
    assert runtime.on_course_playout_started(**token) is False
    assert runtime.on_course_playout_finished(**{**token, 'playout_id': 0}) is False
    assert runtime.on_course_playout_finished(**token) is True
    await asyncio.sleep(0)
    await _ack_prepare_and_start(runtime, 3)
    assert runtime._cinematic_phase['phaseId'] == 'listen'
    assert len(_frames(runtime)) == count + 2
    assert runtime.on_course_playout_started(**token) is False


@pytest.mark.asyncio
async def test_entrance_snapshot_survives_runtime_replacement_with_manifest_fence():
    store = MemoryCourseModeSnapshotStore()
    args = dict(assignment={'assignmentId': 'a1'}, manifest={'courseModeContract': contract()},
                asset_cache=None, forwarder=_Forwarder(), manifest_checksum='a'*64,
                course_mode_snapshot_store=store, course_mode_snapshot_device_id='device-1')
    runtime = LessonRuntime(_Conn(), **args)
    runtime._entrance_started = runtime._entrance_completed = True
    await runtime.persist_course_mode_snapshot()
    snapshot = await store.load('device-1', 'a1')
    restored = LessonRuntime(_Conn(), **args, course_mode_snapshot=snapshot)
    assert restored._entrance_completed is True
    assert restored._entrance_started is True
    assert restored.session_id == runtime.session_id
    with pytest.raises(ValueError, match='cinematic.*manifest'):
        LessonRuntime(_Conn(), **{**args, 'manifest_checksum': 'b'*64}, course_mode_snapshot=snapshot)


@pytest.mark.asyncio
async def test_processing_routes_to_orchestrator_activity_and_fences_replaced_activity():
    runtime = _phase_bound_runtime()
    _activate_v5(runtime, 0)
    runtime._entrance_completed = True
    first, last = [s['id'] for s in runtime._steps]
    runtime.course_mode = SimpleNamespace(orchestrator=SimpleNamespace(active_activity_id=last))
    assert runtime._current_cinematic_activity_id() == last
    assert runtime._queue_course_cinematic_phase('thinking')
    runtime.course_mode.orchestrator.active_activity_id = first
    await asyncio.sleep(0.02)
    assert _frames(runtime) == []


@pytest.mark.asyncio
async def test_cancel_preempts_prepare_and_rejects_its_late_ack():
    runtime = _phase_bound_runtime()
    _activate_v5(runtime, 0)
    task = asyncio.create_task(runtime._apply_authored_cinematic_effect('listen'))
    await asyncio.sleep(0)
    prepare = _frames(runtime)[-1]
    await runtime.cancel()
    cancel = _frames(runtime)[-1]
    assert cancel['body']['command'] == 'cancel'
    await runtime.on_lesson_ack(_v5_ack(runtime, prepare, 1))
    assert not any(f['type'] == 'lesson_start' for f in _frames(runtime))
    await runtime.on_lesson_ack(_control_ack(runtime, cancel, 2))
    assert await asyncio.wait_for(task, 1) is False


@pytest.mark.asyncio
async def test_cancelled_walk_does_not_checkpoint_completed_entrance():
    runtime = _phase_bound_runtime(walk_ms=500)
    _activate_v5(runtime, 0)
    runtime._continue_after_step_visuals = AsyncMock()
    runtime._queue_authored_cinematic_sequence(['walk'])
    await asyncio.sleep(0)
    await _ack_prepare_and_start(runtime, 1)
    await asyncio.sleep(0)
    task = runtime._visual_transition_task
    await runtime.cancel()
    await asyncio.wait_for(task, 1)
    assert not runtime._entrance_completed
    runtime._continue_after_step_visuals.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_ack_can_finish_a_paused_runtime():
    runtime = _phase_bound_runtime()
    _activate_v5(runtime, 0)
    await runtime.pause()
    await runtime.on_lesson_ack(_control_ack(runtime, _frames(runtime)[-1], 1))
    assert runtime.state == S_PAUSED
    await runtime.cancel()
    await runtime.on_lesson_ack(_control_ack(runtime, _frames(runtime)[-1], 2))
    assert runtime.state == 'COMPLETED'


@pytest.mark.asyncio
async def test_failed_required_exit_cannot_emit_completion_stop():
    runtime = _phase_bound_runtime()
    _activate_v5(runtime, 1)
    runtime._step_completed = True
    runtime._play_exit_phase_before_stop = AsyncMock(return_value=False)
    await runtime._maybe_finish_step()
    assert not any(f['type'] == 'lesson_stop' for f in _frames(runtime))
    assert not runtime._completion_stop_sent


@pytest.mark.asyncio
async def test_concurrent_course_completion_does_not_finish_before_exit():
    runtime = _phase_bound_runtime()
    _activate_v5(runtime, 1)
    runtime.course_mode = SimpleNamespace(_completion_stop_dispatched=False,
        orchestrator=SimpleNamespace(session_state=SessionState.CLOSING, active_activity_id=runtime._step_id))
    runtime.persist_course_mode_snapshot = AsyncMock()
    entered, release = asyncio.Event(), asyncio.Event()
    async def exit_phase():
        entered.set()
        await release.wait()
        return False
    runtime._play_exit_phase_before_stop = exit_phase
    first = asyncio.create_task(runtime._complete_course_mode_close())
    await entered.wait()
    assert await runtime._complete_course_mode_close() is False
    assert runtime.course_mode.orchestrator.session_state == SessionState.CLOSING
    release.set()
    assert await first is False
    assert not any(f['type'] == 'lesson_stop' for f in _frames(runtime))


@pytest.mark.asyncio
@pytest.mark.parametrize('completed', [False, True])
async def test_preload_restores_completed_entrance_or_rejects_interrupted_position(completed):
    runtime = _phase_bound_runtime()
    first, last = [step['id'] for step in runtime._steps]
    phases = []
    for activity, names in ((first, ('flyIn', 'walk', 'teach', 'listen', 'thinking', 'celebrate')),
                            (last, ('teach', 'listen', 'thinking', 'celebrate', 'exit'))):
        for name in names:
            phase = _phase()
            phase.update(phaseId=name, activityIds=[activity])
            phases.append(phase)
    runtime.manifest['cinematicPhases'] = phases
    runtime.manifest['courseModeContract'] = {
        'renderer': {'rendererId': runtime.negotiated_version},
        'activities': [{'activityId': activity} for activity in (first, last)],
    }
    runtime.asset_cache.asset_pack_manifest = Mock(return_value=_pack())
    runtime._preload_sd_asset_pack_before_prepare = AsyncMock(return_value=True)
    runtime._entrance_started = True
    runtime._entrance_completed = completed
    runtime._cinematic_restore_activity = last
    if completed:
        assert await runtime.preload_only()
        assert runtime._cinematic_phase['phaseId'] == 'listen'
        assert runtime._cinematic_phase['activityIds'] == [last]
        assert runtime._step_index == 0  # _emit_step advances to restored activity 1.
    else:
        with pytest.raises(Exception, match='interrupted entrance'):
            await runtime.preload_only()
    assert _frames(runtime) == []
