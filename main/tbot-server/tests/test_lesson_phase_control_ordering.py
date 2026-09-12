"""Queued course phases must preserve in-flight lifecycle command authority."""
import asyncio

import pytest

from core.lesson.runtime import S_PAUSED, S_RUNNING
from tests.test_lesson_choreography_review import paused_playout
from tests.test_lesson_choreography_lifecycle import _control_ack
from tests.test_lesson_cinematic_phase_routing import _frames, _ack_prepare_and_start


async def settle_queue():
    # Exceed the native phase worker's polling interval to exercise deferred work.
    await asyncio.sleep(0.08)


@pytest.mark.asyncio
@pytest.mark.parametrize('finish_while_paused', [False, True])
async def test_owned_finish_survives_resume_then_pause_before_listen_runs(finish_while_paused):
    runtime, token = await paused_playout()
    try:
        if finish_while_paused:
            assert runtime.on_course_playout_finished(**token)
        await runtime.resume()
        await runtime.on_lesson_ack(_control_ack(runtime, _frames(runtime)[-1], 4))
        if not finish_while_paused:
            assert runtime.on_course_playout_finished(**token)
        queued_listen = runtime._course_phase_task
        await runtime.pause()
        pause = _frames(runtime)[-1]
        pending = dict(runtime._cinematic_pending_command)
        count = len(_frames(runtime))
        await settle_queue()
        assert runtime._cinematic_pending_command == pending
        assert len(_frames(runtime)) == count
        await runtime.on_lesson_ack(_control_ack(runtime, pause, 5))
        assert runtime.state == S_PAUSED
        await settle_queue()
        assert len(_frames(runtime)) == count
        assert runtime._course_phase_task is queued_listen
        assert not queued_listen.done()
        assert not runtime.on_course_playout_finished(**token)
        await runtime.resume()
        resume = _frames(runtime)[-1]
        await settle_queue()
        assert _frames(runtime)[-1] == resume
        await runtime.on_lesson_ack(_control_ack(runtime, resume, 6))
        await settle_queue()
        await _ack_prepare_and_start(runtime, 7)
        await settle_queue()
        assert runtime.state == S_RUNNING
        assert runtime._cinematic_phase['phaseId'] == 'listen'
        assert [f['body']['cinematicPhase']['phaseId'] for f in _frames(runtime)
                if f['type'] == 'lesson_prepare'] == ['teach', 'listen']
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['teach', 'listen', 'thinking', 'celebrate'])
async def test_shared_phase_queue_waits_for_pause_and_resume_ack(phase):
    runtime, _ = await paused_playout()
    try:
        await runtime.resume()
        await runtime.on_lesson_ack(_control_ack(runtime, _frames(runtime)[-1], 4))
        # Force a queued transition even for teach, the phase already on screen.
        runtime._cinematic_phase_started_at = None
        assert runtime._queue_course_cinematic_phase(phase)
        await runtime.pause()
        pause = _frames(runtime)[-1]
        await settle_queue()
        assert _frames(runtime)[-1] == pause
        assert runtime._cinematic_pending_command['command'] == 'pause'
        await runtime.on_lesson_ack(_control_ack(runtime, pause, 5))
        assert runtime.state == S_PAUSED
        await runtime.resume()
        resume = _frames(runtime)[-1]
        await settle_queue()
        assert _frames(runtime)[-1] == resume
        await runtime.on_lesson_ack(_control_ack(runtime, resume, 6))
        await settle_queue()
        await _ack_prepare_and_start(runtime, 7)
        assert runtime._cinematic_phase['phaseId'] == phase
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('replacement', ['cancel', 'close', 'session', 'assignment', 'step', 'activity'])
async def test_waiting_owned_finish_cannot_outlive_control_or_ownership(replacement):
    runtime, token = await paused_playout()
    try:
        assert runtime.on_course_playout_finished(**token)
        await runtime.resume()
        await runtime.on_lesson_ack(_control_ack(runtime, _frames(runtime)[-1], 4))
        await runtime.pause()
        pause = _frames(runtime)[-1]
        await settle_queue()
        assert _frames(runtime)[-1] == pause
        await runtime.on_lesson_ack(_control_ack(runtime, pause, 5))
        if replacement == 'cancel':
            await runtime.cancel()
        elif replacement == 'close':
            await runtime.close()
        else:
            await runtime.resume()
            await runtime.on_lesson_ack(_control_ack(runtime, _frames(runtime)[-1], 6))
            if replacement == 'session':
                runtime.session_id = 'replacement'
            elif replacement == 'assignment':
                runtime.assignment_id = 'replacement'
            elif replacement == 'step':
                runtime._step_seq += 1
            else:
                runtime._step_id = 'replacement'
        count = len(_frames(runtime))
        await settle_queue()
        assert len(_frames(runtime)) == count
        assert not runtime.on_course_playout_finished(**token)
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_new_playout_supersedes_listen_waiting_for_resume():
    runtime, token = await paused_playout()
    try:
        assert runtime.on_course_playout_finished(**token)
        await runtime.resume()
        await runtime.on_lesson_ack(_control_ack(runtime, _frames(runtime)[-1], 4))
        await runtime.pause()
        pause = _frames(runtime)[-1]
        await settle_queue()
        assert _frames(runtime)[-1] == pause
        await runtime.on_lesson_ack(_control_ack(runtime, pause, 5))
        await runtime.resume()
        await runtime.on_lesson_ack(_control_ack(runtime, _frames(runtime)[-1], 6))
        assert runtime.on_course_playout_started(**{**token, 'playout_id': 2})
        await settle_queue()
        await _ack_prepare_and_start(runtime, 7)
        assert runtime._cinematic_phase['phaseId'] == 'teach'
        assert runtime._course_playout_active
        assert not any(f['type'] == 'lesson_prepare'
                       and f['body']['cinematicPhase']['phaseId'] == 'listen' for f in _frames(runtime))
    finally:
        await runtime.close()
