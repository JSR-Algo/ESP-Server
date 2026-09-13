"""Terminal intent survives transport loss without replaying lesson content."""
import copy
import asyncio
from unittest.mock import AsyncMock

import pytest

from core.lesson.course_snapshot_store import MemoryCourseModeSnapshotStore
from core.lesson.runtime import LessonRuntime, SessionState, course_mode_runtime_from_manifest, S_COMPLETED
from tests.test_course_mode_runtime_integration import contract, _Forwarder
from tests.test_lesson_cinematic_phase_routing import _phase_bound_runtime, _activate_v5, _frames
from tests.test_lesson_choreography_lifecycle import _control_ack


class Forwarder(_Forwarder):
    async def aclose(self):
        pass


def runtime_with_store():
    runtime = _phase_bound_runtime()
    _activate_v5(runtime, 0)
    runtime.conn.config = copy.deepcopy(runtime.conn.config)
    runtime.conn.config['lesson']['course_mode_v2_enabled'] = True
    runtime.manifest['courseModeContract'] = contract()
    runtime.course_mode = course_mode_runtime_from_manifest(
        runtime.manifest, enabled=True, assignment_id=runtime.assignment_id,
        runtime_session_id=runtime.session_id, defer_evidence_forwarding=True,
    )
    runtime.course_mode.orchestrator.session_state = SessionState.WORD_ACTIVE
    runtime._course_mode_snapshot_store = MemoryCourseModeSnapshotStore()
    runtime._course_mode_snapshot_device_id = 'terminal-test-device'
    runtime.forwarder = Forwarder()
    runtime.conn.finish_lesson_mode = AsyncMock()
    return runtime


def restored(runtime, snapshot):
    conn = runtime.conn
    conn.websocket.sent.clear()
    result = LessonRuntime(
        conn, assignment={
            'assignmentId': runtime.assignment_id, 'assignmentVersion': runtime.assignment_version,
            'lessonId': runtime.lesson_id, 'lessonVersion': runtime.lesson_version,
        }, manifest_checksum=runtime.manifest_checksum,
        manifest=runtime.manifest, asset_cache=runtime.asset_cache, forwarder=Forwarder(),
        course_mode_snapshot=snapshot,
        course_mode_snapshot_store=runtime._course_mode_snapshot_store,
        course_mode_snapshot_device_id='terminal-test-device',
    )
    conn.lesson_runtime = result
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['stop', 'cancel'])
async def test_terminal_request_supersedes_pending_pause_and_late_ack(operation):
    runtime = runtime_with_store()
    try:
        await runtime.pause()
        pause = _frames(runtime)[-1]
        await getattr(runtime, operation)()
        terminal = _frames(runtime)[-1]
        assert terminal != pause
        await runtime.on_lesson_ack(_control_ack(runtime, pause, 1))
        await runtime.on_lesson_ack(_control_ack(runtime, terminal, 2))
        await runtime.on_lesson_ack(_control_ack(runtime, terminal, 2))
        assert runtime.state == S_COMPLETED
        events = [e for b in runtime.forwarder.batches for e in b['events']]
        assert sum(e['type'] == 'lesson_abandoned' for e in events) == 1
        runtime.conn.finish_lesson_mode.assert_awaited_once()
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['stop', 'cancel', 'complete'])
async def test_restart_replays_only_unacknowledged_terminal_command(operation):
    runtime = runtime_with_store()
    restarted = None
    try:
        if operation == 'complete':
            await runtime._emit('lesson_stop', body={'reason': 'COMPLETED', 'cinematicPhase': {
                'command': 'stop', **runtime._cinematic_identity_payload()}})
        else:
            await getattr(runtime, operation)()
        original = _frames(runtime)[-1]
        snapshot = await runtime._course_mode_snapshot_store.load('terminal-test-device', runtime.assignment_id)
        assert snapshot is not None
        await runtime.close()
        restarted = restored(runtime, snapshot)
        await restarted.start_protocol(preloaded=True)
        replay = _frames(restarted)
        assert len(replay) == 1
        assert replay[0]['type'] == original['type']
        assert replay[0]['body'] == original['body']
        assert replay[0]['sessionId'] == original['sessionId']
        assert replay[0]['sequence'] > original['sequence']
        assert not restarted.forwarder.batches, 'send alone is not terminal ACK proof'
        await restarted.on_lesson_ack(_control_ack(restarted, replay[0], 1))
        assert restarted.state == S_COMPLETED
        events = [e for b in restarted.forwarder.batches for e in b['events']]
        expected = 'lesson_completed' if operation == 'complete' else 'lesson_abandoned'
        assert sum(e['type'] == expected for e in events) == 1
    finally:
        await runtime.close()
        if restarted:
            await restarted.close()


@pytest.mark.asyncio
async def test_acknowledged_cancel_snapshot_never_restarts_content():
    runtime = runtime_with_store()
    restarted = None
    try:
        await runtime.cancel()
        await runtime.on_lesson_ack(_control_ack(runtime, _frames(runtime)[-1], 1))
        snapshot = await runtime._course_mode_snapshot_store.load('terminal-test-device', runtime.assignment_id)
        assert snapshot is not None
        restarted = restored(runtime, snapshot)
        await restarted.start_protocol(preloaded=True)
        assert _frames(restarted) == []
        assert restarted.session_id == runtime.session_id
        assert restarted.state == S_COMPLETED
    finally:
        await runtime.close()
        if restarted:
            await restarted.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['stop', 'cancel'])
async def test_pending_terminal_request_rejects_new_semantic_work(operation):
    runtime = runtime_with_store()
    try:
        before = runtime.course_mode.snapshot()
        await getattr(runtime, operation)()
        assert await runtime.course_continue({
            'lessonSessionId': runtime.session_id, 'turnSequenceId': 1,
            'observationId': 'after-terminal',
        }) == {'accepted': False, 'code': 'COURSE_SESSION_TERMINAL'}
        assert runtime.course_mode.snapshot() == before
        assert await runtime.on_child_response('barn') is False
        assert runtime._queue_course_cinematic_phase('teach') is False
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['stop', 'cancel'])
async def test_paused_terminal_lost_ack_uses_bounded_retry(operation):
    runtime = runtime_with_store()
    try:
        await runtime.pause()
        await runtime.on_lesson_ack(_control_ack(runtime, _frames(runtime)[-1], 1))
        runtime._frame_ack_timeout_sec = lambda: 0.01
        runtime._frame_ack_max_retries = lambda: 1
        await getattr(runtime, operation)()
        original = _frames(runtime)[-1]
        await asyncio.sleep(0.1)
        terminal_frames = [f for f in _frames(runtime) if f['type'] == original['type']
                           and f['body'].get('command') == original['body'].get('command')]
        assert len(terminal_frames) == 2
        assert terminal_frames[1]['body'] == original['body']
        assert runtime.state == 'FAILED'
        events = [e for b in runtime.forwarder.batches for e in b['events']]
        assert sum(e['type'] == 'lesson_failed' for e in events) == 1
        assert not any(e['type'] in {'lesson_completed', 'word_evidence_recorded'} for e in events)
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['stop', 'cancel'])
async def test_crash_at_terminal_send_retains_exact_control_for_restart(operation):
    runtime = runtime_with_store()
    restarted = None
    captured = []

    async def interrupted_send(payload):
        captured.append(payload)
        raise ConnectionError('owned disconnected transport')

    try:
        runtime._send = interrupted_send
        with pytest.raises(ConnectionError):
            await getattr(runtime, operation)()
        snapshot = await runtime._course_mode_snapshot_store.load('terminal-test-device', runtime.assignment_id)
        assert snapshot['terminalLifecycle']['body']['command'] in {'stop', 'cancel'}
        restarted = restored(runtime, snapshot)
        await restarted.start_protocol(preloaded=True)
        assert len(_frames(restarted)) == 1
        assert _frames(restarted)[0]['body'] == snapshot['terminalLifecycle']['body']
    finally:
        await runtime.close()
        if restarted:
            await restarted.close()
