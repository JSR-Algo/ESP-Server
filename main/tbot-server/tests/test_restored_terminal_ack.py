"""Memory snapshot reconstruction; ACKs are protocol fixtures, not device output."""
import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
import traceback

import pytest

from core.lesson.runtime import LessonRuntime, RENDERER_V5
from core.lesson.course_snapshot_store import MemoryCourseModeSnapshotStore
from tests.test_course_terminal_lifecycle import runtime_with_store, restored, _frames, _control_ack
from tests.test_malformed_ack_correlation import MALFORMED


PARTITIONS = ['true', 'false', 'null', 'list', 'dict', 'nonnumeric', 'empty',
              'fractional', 'integral-float', 'missing']


def json_state(value):
    if isinstance(value, asyncio.Future):
        return dict(kind=type(value).__name__, identity=id(value), done=value.done(),
                    cancelled=value.cancelled())
    if isinstance(value, dict):
        return {str(k): json_state(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_state(v) for v in value]
    if isinstance(value, set):
        return sorted(json_state(v) for v in value)
    if value is None or type(value) in (bool, int, float, str):
        return value
    raise TypeError(f'unrecorded state type: {type(value).__name__}')


def state(runtime):
    names = ['_seq', '_last_inbound_sequence', '_outstanding',
        '_retired_conversation_ack_sequences', '_retired_visual_ack_sequences',
        '_visual_ack_waiters', '_visual_generation', '_cinematic_phase',
        '_cinematic_pending_command', '_authored_cinematic_pending',
        '_cinematic_deferred_step_ack', '_course_phase_task', '_visual_transition_task',
        '_frame_ack_timeout_task', '_frame_ack_retry_task', '_terminal_lifecycle',
        '_terminal_requested', '_closed', '_terminal_notified', '_steps_completed',
        '_step_id', '_step_seq', '_step_acked', '_step_completed', '_step_visuals_ready',
        '_entrance_started', '_entrance_completed', '_course_playout_active',
        '_cinematic_cancel_sent', '_cinematic_stop_sent', '_failure_forwarded']
    result = {name: json_state(getattr(runtime, name, None)) for name in names}
    finish = runtime.conn.finish_lesson_mode
    result.update(runtimeId=id(runtime), state=runtime.state,
        negotiatedVersion=runtime.negotiated_version,
        assignmentId=runtime.assignment_id, sessionId=runtime.session_id,
        frames=deepcopy(_frames(runtime)), rawFrames=list(runtime.conn.websocket.sent),
        forwardedBatches=deepcopy(runtime.forwarder.batches),
        finishCalls=[dict(args=json_state(c.args), kwargs=json_state(c.kwargs)) for c in finish.await_args_list],
        timeoutSeconds=runtime._frame_ack_timeout_sec(), maxRetries=runtime._frame_ack_max_retries())
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['stop', 'cancel'])
@pytest.mark.parametrize('admission_change', ['rollout_disabled', 'capability_missing'])
@pytest.mark.parametrize('value', MALFORMED, ids=PARTITIONS)
async def test_restored_terminal_rejects_malformed_then_completes_once(operation, admission_change, value, request):
    baseline_tasks = set(asyncio.all_tasks())
    runtime = runtime_with_store()
    restarted = None
    record = dict(nodeid=request.node.nodeid, operation=operation, admission=admission_change,
                  snapshotStore='production MemoryCourseModeSnapshotStore', status='STARTED')
    try:
        assert type(runtime._course_mode_snapshot_store) is MemoryCourseModeSnapshotStore
        assert runtime.persist_course_mode_snapshot.__func__ is LessonRuntime.persist_course_mode_snapshot
        record['fixtureTimeoutSeconds'] = runtime._frame_ack_timeout_sec()
        # Remove the repository fixture's long timeout override to exercise production defaults.
        runtime.conn.config['lesson'].pop('frame_ack_timeout_sec')
        assert runtime._frame_ack_timeout_sec() == 12 and runtime._frame_ack_max_retries() == 1
        record['initial'] = state(runtime)
        await getattr(runtime, operation)()
        originals = deepcopy(_frames(runtime))
        assert len(originals) == 1
        original = originals[0]
        expected_type = 'lesson_stop' if operation == 'stop' else 'lesson_cinematic_control'
        assert original['type'] == expected_type
        await runtime.persist_course_mode_snapshot()
        snapshot = await runtime._course_mode_snapshot_store.load(
            runtime._course_mode_snapshot_device_id, runtime.assignment_id)
        assert isinstance(snapshot, dict) and 'terminalLifecycle' in snapshot
        assert snapshot['terminalLifecycle']['frameType'] == expected_type
        assert snapshot['terminalLifecycle']['body'] == original['body']
        assert 'event' not in snapshot['terminalLifecycle']
        record.update(originalFrames=originals, savedSnapshot=deepcopy(snapshot), beforeClose=state(runtime))
        await runtime.close()
        assert runtime._closed
        record['oldClosed'] = state(runtime)
        if admission_change == 'rollout_disabled':
            runtime.conn.config['lesson']['renderer_v5_enabled'] = False
        else:
            runtime.conn.features.pop('lessonRendererV5', None)
        record['restoredConfig'] = deepcopy(runtime.conn.config)
        record['restoredFeatures'] = deepcopy(runtime.conn.features)
        restarted = restored(runtime, snapshot)
        assert restarted is not runtime and type(restarted) is LessonRuntime
        assert restarted.start_protocol.__func__ is LessonRuntime.start_protocol
        assert restarted.negotiated_version == restarted.manifest['manifestVersion'] == RENDERER_V5
        assert restarted._terminal_cinematic_version() == RENDERER_V5
        assert not restarted._renderer_v5_enabled()
        assert restarted.conn.lesson_runtime is restarted
        record['constructed'] = state(restarted)
        await restarted.start_protocol(preloaded=True)
        replay = deepcopy(_frames(restarted))
        assert len(replay) == 1 and replay[0]['type'] == expected_type
        replay = replay[0]
        assert replay['body'] == original['body']
        for key in ('protocolVersion', 'assignmentId', 'sessionId', 'lessonId', 'lessonVersion', 'stepId'):
            assert replay[key] == original[key]
        assert replay['sequence'] > original['sequence']
        assert restarted._frame_ack_timeout_sec() == 12 and restarted._frame_ack_max_retries() == 1
        assert not restarted.forwarder.batches
        restarted.conn.finish_lesson_mode.assert_not_awaited()
        valid = _control_ack(restarted, replay, 1)
        malformed = deepcopy(valid)
        if value == 'missing':
            malformed['body'].pop('acks')
        else:
            malformed['body']['acks'] = value
        # Round-trip JSON partitions through the same dict-facing runtime boundary.
        malformed = json.loads(json.dumps(malformed))
        before = state(restarted)
        record.update(replayedFrame=replay, validFixtureAck=valid,
            malformedFixtureAck=malformed, beforeMalformed=before)
        await restarted.on_lesson_ack(malformed)
        after = state(restarted)
        record['afterMalformed'] = after
        record['malformedStateUnchanged'] = before == after
        assert before == after, 'malformed correlation changed restored terminal state'
        await restarted.on_lesson_ack(valid)
        record['afterValid'] = state(restarted)
        assert restarted.state == 'COMPLETED'
        assert restarted._last_inbound_sequence == 1 and not restarted._outstanding
        events = [event for batch in restarted.forwarder.batches for event in batch['events']]
        expected_events = ['runtime_phase_changed', 'lesson_abandoned'] if operation == 'stop' else ['lesson_abandoned']
        assert [event['type'] for event in events] == expected_events
        if operation == 'stop':
            assert events[0]['phase'] == 'abandoned'
        assert events[-1]['reason'] == 'cancelled'
        restarted.conn.finish_lesson_mode.assert_awaited_once_with(reason='lesson_abandoned')
        assert _frames(restarted) == [replay], 'terminal ACK emitted obsolete content'
        before_duplicate = state(restarted)
        await restarted.on_lesson_ack(valid)
        after_duplicate = state(restarted)
        record.update(beforeDuplicate=before_duplicate, afterDuplicate=after_duplicate)
        assert before_duplicate == after_duplicate
        stored_final = await restarted._course_mode_snapshot_store.load(
            restarted._course_mode_snapshot_device_id, restarted.assignment_id)
        record['finalSavedSnapshot'] = stored_final
        assert stored_final['terminalLifecycle']['event']['type'] == 'lesson_abandoned'
        record['status'] = 'PASS'
    except BaseException:
        record['status'] = 'FAIL'
        record['failure'] = traceback.format_exc()
        raise
    finally:
        await runtime.close()
        if restarted is not None:
            await restarted.close()
        await asyncio.sleep(0)
        pending = [t for t in asyncio.all_tasks() if t not in baseline_tasks and not t.done()]
        record['cleanup'] = dict(oldClosed=runtime._closed,
            restoredClosed=restarted._closed if restarted else None, remainingTasks=len(pending))
        destination = Path(os.environ['TBOT_RUN22_CASES'])
        name = request.node.callspec.id
        (destination/(name+'.json')).write_text(json.dumps(record, indent=2, sort_keys=True)+'\n')
        assert not pending, pending
