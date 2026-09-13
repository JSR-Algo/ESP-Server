"""Independent T13 terminal lifecycle review regressions."""

import asyncio
import copy
import json
from unittest.mock import AsyncMock

import pytest

from core.lesson.runtime import LessonRuntime
from core.lesson.errors import LessonError
from tests.test_course_terminal_lifecycle import runtime_with_store, restored, _frames, _control_ack
from tests.test_lesson_cinematic_phase_routing import _v5_ack


@pytest.mark.asyncio
async def test_review_terminal_snapshot_rejects_changed_publication_identity():
    runtime = runtime_with_store()
    restarted = None
    try:
        await runtime.cancel()
        snapshot = await runtime._course_mode_snapshot_store.load(
            'terminal-test-device', runtime.assignment_id,
        )
        assert 'cinematicPlayback' not in snapshot
        with pytest.raises(LessonError, match='COURSE_TERMINAL_IDENTITY_MISMATCH'):
            restarted = LessonRuntime(
                runtime.conn,
                assignment={
                    'assignmentId': runtime.assignment_id,
                    'lessonId': runtime.lesson_id,
                    'lessonVersion': 999,
                    'assignmentVersion': 999,
                },
                manifest=copy.deepcopy(runtime.manifest),
                asset_cache=runtime.asset_cache,
                forwarder=runtime.forwarder,
                manifest_checksum='different-manifest',
                course_mode_snapshot=snapshot,
                course_mode_snapshot_store=runtime._course_mode_snapshot_store,
                course_mode_snapshot_device_id='terminal-test-device',
            )
    finally:
        await runtime.close()
        if restarted is not None:
            await restarted.close()


@pytest.mark.asyncio
async def test_review_prepare_ack_cannot_start_during_stop_interrupt():
    runtime = runtime_with_store()
    entered, release = asyncio.Event(), asyncio.Event()
    original_interrupt = runtime._interrupt_course_embodied_action

    async def held_interrupt(reason):
        entered.set()
        await release.wait()

    stop_task = None
    try:
        assert runtime._queue_course_cinematic_phase('listen')
        await asyncio.sleep(0)
        prepare = _frames(runtime)[-1]
        assert prepare['type'] == 'lesson_prepare'
        runtime._interrupt_course_embodied_action = held_interrupt
        stop_task = asyncio.create_task(runtime.stop())
        await asyncio.wait_for(entered.wait(), 1)
        await runtime.on_lesson_ack(_v5_ack(runtime, prepare, 1))
        assert not any(frame['type'] == 'lesson_start' for frame in _frames(runtime))
    finally:
        release.set()
        if stop_task is not None:
            await stop_task
        runtime._interrupt_course_embodied_action = original_interrupt
        await runtime.close()


@pytest.mark.asyncio
async def test_review_cancel_retries_after_terminal_snapshot_store_recovers():
    runtime = runtime_with_store()
    store = runtime._course_mode_snapshot_store
    original_store = store.store
    try:
        store.store = AsyncMock(side_effect=ConnectionError('owned store outage'))
        with pytest.raises(ConnectionError):
            await runtime.cancel()
        assert not _frames(runtime)
        store.store = original_store
        await runtime.cancel()
        terminal = [frame for frame in _frames(runtime)
                    if frame['body'].get('command') == 'cancel']
        assert len(terminal) == 1
        snapshot = await store.load('terminal-test-device', runtime.assignment_id)
        assert snapshot['terminalLifecycle']['body'] == terminal[0]['body']
        assert runtime._frame_ack_timeout_task is not None
    finally:
        store.store = original_store
        await runtime.close()


@pytest.mark.asyncio
async def test_terminal_timeout_reports_backend_failure_if_error_socket_send_fails():
    runtime = runtime_with_store()
    original_send = runtime._send

    async def broken_error_send(payload):
        if json.loads(payload)['type'] == 'lesson_error':
            raise ConnectionError('owned closed socket')
        await original_send(payload)

    try:
        runtime._send = broken_error_send
        runtime._frame_ack_timeout_sec = lambda: 0.01
        runtime._frame_ack_max_retries = lambda: 0
        await runtime.cancel()
        timeout = runtime._frame_ack_timeout_task
        result = await asyncio.gather(timeout, return_exceptions=True)
        events = [event for batch in runtime.forwarder.batches for event in batch['events']]
        assert sum(event['type'] == 'lesson_failed' for event in events) == 1, result
        assert runtime._terminal_lifecycle['event']['type'] == 'lesson_failed'
        assert not any(event['type'] == 'lesson_completed' for event in events)
    finally:
        runtime._send = original_send
        await runtime.close()


@pytest.mark.asyncio
async def test_stop_retires_old_prepare_timeout_before_embodied_interrupt_await():
    runtime = runtime_with_store()
    entered, release = asyncio.Event(), asyncio.Event()
    original_interrupt = runtime._interrupt_course_embodied_action

    async def held_interrupt(reason):
        entered.set()
        await release.wait()

    stop_task = None
    try:
        runtime._frame_ack_timeout_sec = lambda: 0.01
        runtime._frame_ack_max_retries = lambda: 1
        assert runtime._queue_course_cinematic_phase('listen')
        await asyncio.sleep(0)
        assert _frames(runtime)[-1]['type'] == 'lesson_prepare'
        runtime._interrupt_course_embodied_action = held_interrupt
        stop_task = asyncio.create_task(runtime.stop())
        await asyncio.wait_for(entered.wait(), 1)
        await asyncio.sleep(0.06)
        assert [frame['type'] for frame in _frames(runtime)] == ['lesson_prepare']
        assert runtime.state != 'FAILED'
    finally:
        release.set()
        if stop_task is not None:
            await stop_task
        runtime._interrupt_course_embodied_action = original_interrupt
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('admission_change', ['rollout_disabled', 'capability_missing'])
async def test_recovered_v5_stop_requires_original_control_ack_contract(admission_change):
    runtime = runtime_with_store()
    restarted = None
    original_config = copy.deepcopy(runtime.conn.config)
    original_features = copy.deepcopy(runtime.conn.features)
    try:
        await runtime._emit('lesson_stop', body={
            'reason': 'COMPLETED',
            'cinematicPhase': {'command': 'stop', **runtime._cinematic_identity_payload()},
        })
        snapshot = await runtime._course_mode_snapshot_store.load('terminal-test-device', runtime.assignment_id)
        await runtime.close()
        if admission_change == 'rollout_disabled':
            runtime.conn.config['lesson']['renderer_v5_enabled'] = False
        else:
            runtime.conn.features.pop('lessonRendererV5', None)
        restarted = restored(runtime, snapshot)
        await restarted.start_protocol(preloaded=True)
        frame = _frames(restarted)[-1]
        rejected = _control_ack(restarted, frame, 1)
        rejected['body']['cinematicPhase']['accepted'] = False
        await restarted.on_lesson_ack(rejected)
        events = [event for batch in restarted.forwarder.batches for event in batch['events']]
        assert not any(event['type'] == 'lesson_completed' for event in events)
        assert restarted.state != 'COMPLETED'
        await restarted.on_lesson_ack(_control_ack(restarted, frame, 1))
        events = [event for batch in restarted.forwarder.batches for event in batch['events']]
        assert sum(event['type'] == 'lesson_completed' for event in events) == 1
    finally:
        runtime.conn.config = original_config
        runtime.conn.features = original_features
        await runtime.close()
        if restarted is not None:
            await restarted.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['stop', 'cancel'])
async def test_saved_terminal_intent_survives_crash_during_embodied_interrupt(operation):
    runtime = runtime_with_store()
    runtime._entrance_started = runtime._entrance_completed = True
    await runtime.persist_course_mode_snapshot()
    ordinary = await runtime._course_mode_snapshot_store.load('terminal-test-device', runtime.assignment_id)
    assert ordinary['orchestrator']['sessionState'] == 'WORD_ACTIVE'
    assert 'terminalLifecycle' not in ordinary
    entered = asyncio.Event()
    original_interrupt = runtime._interrupt_course_embodied_action
    restarted = None
    task = None

    async def held_interrupt(reason):
        entered.set()
        await asyncio.Future()

    try:
        runtime._interrupt_course_embodied_action = held_interrupt
        task = asyncio.create_task(getattr(runtime, operation)())
        await asyncio.wait_for(entered.wait(), 1)
        assert runtime._terminal_requested is True
        assert not _frames(runtime)
        crash_snapshot = copy.deepcopy(await runtime._course_mode_snapshot_store.load(
            'terminal-test-device', runtime.assignment_id,
        ))
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        runtime._interrupt_course_embodied_action = original_interrupt
        await runtime.close()
        restarted = restored(runtime, crash_snapshot)
        await restarted.start_protocol(preloaded=True)
        frames = _frames(restarted)
        assert frames and all(frame['type'] not in {'lesson_prepare', 'lesson_start', 'lesson_step'}
                              for frame in frames), {
            'operation': operation,
            'snapshotHasTerminalLifecycle': 'terminalLifecycle' in crash_snapshot,
            'snapshotStillOrdinary': crash_snapshot == ordinary,
            'restartedFrames': frames,
        }
        assert 'terminalLifecycle' in crash_snapshot
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        runtime._interrupt_course_embodied_action = original_interrupt
        await runtime.close()
        if restarted is not None:
            await restarted.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('course', [True, False])
@pytest.mark.parametrize('first,second', [('stop', 'cancel'), ('cancel', 'stop')])
async def test_concurrent_terminal_calls_keep_one_control_while_cleanup_waits(course, first, second):
    runtime = runtime_with_store()
    if not course:
        runtime.course_mode = None
        runtime.course_embodied_dispatcher = None
    entered, release = asyncio.Event(), asyncio.Event()
    original_interrupt = runtime._interrupt_course_embodied_action

    async def held_interrupt(reason):
        entered.set()
        await release.wait()

    first_task = second_task = None
    try:
        runtime._interrupt_course_embodied_action = held_interrupt
        first_task = asyncio.create_task(getattr(runtime, first)())
        await asyncio.wait_for(entered.wait(), 1)
        intent = runtime._terminal_lifecycle.copy()
        second_task = asyncio.create_task(getattr(runtime, second)())
        await asyncio.sleep(0)
        assert not second_task.done()
        assert not _frames(runtime)
        release.set()
        await asyncio.gather(first_task, second_task)
        frames = _frames(runtime)
        assert len(frames) == 1
        assert frames[0]['body'] == intent['body']
        await runtime.on_lesson_ack(_control_ack(runtime, frames[0], 1))
        events = [event for batch in runtime.forwarder.batches for event in batch['events']]
        assert sum(event['type'] == 'lesson_abandoned' for event in events) == 1
    finally:
        release.set()
        for task in (first_task, second_task):
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        runtime._interrupt_course_embodied_action = original_interrupt
        await runtime.close()


@pytest.mark.asyncio
async def test_failed_staged_snapshot_retries_exact_intent_after_storage_recovers():
    runtime = runtime_with_store()
    store = runtime._course_mode_snapshot_store
    original_store = store.store
    try:
        store.store = AsyncMock(side_effect=ConnectionError('owned snapshot unavailable'))
        with pytest.raises(ConnectionError):
            await runtime.stop()
        body = runtime._terminal_lifecycle['body'].copy()
        assert not _frames(runtime)
        store.store = original_store
        await runtime.cancel()
        assert len(_frames(runtime)) == 1
        assert _frames(runtime)[0]['body'] == body
        saved = await store.load('terminal-test-device', runtime.assignment_id)
        assert saved['terminalLifecycle']['body'] == body
    finally:
        store.store = original_store
        await runtime.close()
