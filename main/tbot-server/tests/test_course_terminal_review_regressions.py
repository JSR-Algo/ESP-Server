"""Independent T13 terminal lifecycle review regressions."""

import asyncio
import copy
import json
from unittest.mock import AsyncMock

import pytest

from core.lesson.course_snapshot_store import MemoryCourseModeSnapshotStore
from core.lesson.runtime import LessonRuntime
from core.lesson.errors import LessonError
from tests.test_course_terminal_lifecycle import runtime_with_store, restored, _frames, _control_ack
from tests.test_lesson_cinematic_phase_routing import _v5_ack
from tests.test_lesson_choreography_review import closing_runtime


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


@pytest.mark.asyncio
async def test_cancel_supersedes_unsent_completion_with_real_snapshot_serialization():
    runtime = closing_runtime()
    entered, release = asyncio.Event(), asyncio.Event()

    class HeldFirstStore(MemoryCourseModeSnapshotStore):
        async def store(self, *args):
            if not entered.is_set():
                entered.set()
                await release.wait()
            await super().store(*args)

    runtime._course_mode_snapshot_store = HeldFirstStore()
    runtime._course_mode_snapshot_device_id = 'review-serialized-device'
    runtime.persist_course_mode_snapshot = LessonRuntime.persist_course_mode_snapshot.__get__(runtime)
    completing = cancelling = None
    try:
        completing = asyncio.create_task(runtime._complete_course_mode_close())
        await asyncio.wait_for(entered.wait(), 1)
        cancelling = asyncio.create_task(runtime.cancel())
        await asyncio.sleep(0)
        assert not cancelling.done()
        assert not _frames(runtime)
        release.set()
        complete_result, _ = await asyncio.wait_for(asyncio.gather(completing, cancelling), 2)
        assert complete_result is False
        assert not any(frame['type'] == 'lesson_stop' for frame in _frames(runtime))
        cancel = _frames(runtime)[-1]
        assert cancel['body']['command'] == 'cancel'
        snapshot = await runtime._course_mode_snapshot_store.load('review-serialized-device', runtime.assignment_id)
        assert snapshot['terminalLifecycle']['body'] == cancel['body']
        await runtime.on_lesson_ack(_control_ack(runtime, cancel, 1))
        assert runtime.state == 'COMPLETED'
    finally:
        release.set()
        for task in (completing, cancelling):
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['stop', 'cancel'])
async def test_explicit_terminal_supersedes_completion_after_snapshot_failure(operation):
    runtime = closing_runtime()

    class FailFirstStore(MemoryCourseModeSnapshotStore):
        first = True

        async def store(self, *args):
            if self.first:
                self.first = False
                raise OSError('owned completion snapshot failure')
            await super().store(*args)

    runtime._course_mode_snapshot_store = FailFirstStore()
    runtime._course_mode_snapshot_device_id = 'review-failed-store-device'
    runtime.persist_course_mode_snapshot = LessonRuntime.persist_course_mode_snapshot.__get__(runtime)
    try:
        assert await runtime._complete_course_mode_close() is False
        assert not _frames(runtime)
        await getattr(runtime, operation)()
        frames = _frames(runtime)
        assert len(frames) == 1
        assert frames[0]['body'].get('reason') != 'COMPLETED'
        snapshot = await runtime._course_mode_snapshot_store.load(
            'review-failed-store-device', runtime.assignment_id,
        )
        assert snapshot['terminalLifecycle']['body'] == frames[0]['body']
        await runtime.on_lesson_ack(_control_ack(runtime, frames[0], 1))
        events = [event for batch in runtime.forwarder.batches for event in batch['events']]
        assert sum(event['type'] == 'lesson_abandoned' for event in events) == 1
        assert not any(event['type'] == 'lesson_completed' for event in events)
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('retired_kind', ['prepare', 'pause'])
@pytest.mark.parametrize('invalid_kind', ['accepted', 'phase', 'command_sequence', 'session', 'step', 'acks_type'])
async def test_retired_ack_validation_preserves_highwater_and_terminal_sequence(retired_kind, invalid_kind):
    runtime = runtime_with_store()
    step_acked_before = runtime._step_acked
    try:
        if retired_kind == 'prepare':
            assert runtime._queue_course_cinematic_phase('listen')
            await asyncio.sleep(0)
        else:
            await runtime.pause()
        old = _frames(runtime)[-1]
        valid = _v5_ack(runtime, old, 1) if retired_kind == 'prepare' else _control_ack(runtime, old, 1)
        await runtime.cancel()
        terminal = _frames(runtime)[-1]
        invalid = copy.deepcopy(valid)
        if invalid_kind == 'accepted':
            invalid['body']['cinematicPhase']['accepted'] = False
        elif invalid_kind == 'phase':
            invalid['body']['cinematicPhase']['phaseId'] = 'exit'
        elif invalid_kind == 'command_sequence':
            invalid['body']['cinematicPhase']['commandSequenceId'] += 100
        elif invalid_kind == 'session':
            invalid['sessionId'] = 'other-session'
        elif invalid_kind == 'step':
            invalid['stepId'] = 'other-step'
        else:
            invalid['body']['acks'] = str(invalid['body']['acks'])
        await runtime.on_lesson_ack(invalid)
        assert runtime._last_inbound_sequence == 0
        assert old['sequence'] in runtime._retired_conversation_ack_sequences
        count = len(_frames(runtime))
        await runtime.on_lesson_ack(valid)
        assert runtime._last_inbound_sequence == 1
        assert len(_frames(runtime)) == count
        assert runtime._step_acked is step_acked_before
        assert runtime._cinematic_deferred_step_ack is None
        await runtime.on_lesson_ack(_control_ack(runtime, terminal, 2))
        assert runtime.state == 'COMPLETED'
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('restart_origin', ['same_runtime_attempted', 'restart_attempted', 'restart_durable_before_send'])
async def test_attempted_or_restored_completion_cannot_be_cancelled_during_retry_persist(restart_origin):
    runtime = runtime_with_store()
    current = runtime
    original_send = runtime._send
    store = runtime._course_mode_snapshot_store
    original_store = store.store
    retry_entered, release_retry = asyncio.Event(), asyncio.Event()
    retry = cancel = None
    attempts = []

    async def attempted_send(payload):
        attempts.append(payload)
        raise ConnectionError('owned ambiguous transport result')

    async def durable_then_process_loss(*args):
        await original_store(*args)
        raise asyncio.CancelledError('owned process loss before transport attempt')

    async def hold_retry_store(*args):
        if not retry_entered.is_set():
            retry_entered.set()
            await release_retry.wait()
        await original_store(*args)

    try:
        body = {'reason': 'COMPLETED', 'cinematicPhase': {
            'command': 'stop', **runtime._cinematic_identity_payload(),
        }}
        if restart_origin == 'restart_durable_before_send':
            store.store = durable_then_process_loss
            with pytest.raises(asyncio.CancelledError):
                await runtime._emit('lesson_stop', body=body)
            assert not attempts and not _frames(runtime)
        else:
            runtime._send = attempted_send
            with pytest.raises(ConnectionError):
                await runtime._emit('lesson_stop', body=body)
            assert len(attempts) == 1
            assert runtime._can_supersede_unsent_completion() is False
        saved = copy.deepcopy(await store.load('terminal-test-device', runtime.assignment_id))
        assert saved['terminalLifecycle']['body']['reason'] == 'COMPLETED'
        expected_body = copy.deepcopy(saved['terminalLifecycle']['body'])
        store.store = original_store
        runtime._send = original_send
        if restart_origin.startswith('restart_'):
            await runtime.close()
            current = restored(runtime, saved)
            assert current._can_supersede_unsent_completion() is False
        store.store = hold_retry_store
        retry = asyncio.create_task(current.start_protocol(preloaded=True))
        await asyncio.wait_for(retry_entered.wait(), 1)
        assert current._can_supersede_unsent_completion() is False
        cancel = asyncio.create_task(current.cancel())
        await asyncio.sleep(0)
        assert not cancel.done()
        assert not _frames(current)
        release_retry.set()
        await asyncio.wait_for(asyncio.gather(retry, cancel), 2)
        frames = _frames(current)
        assert len(frames) == 1
        assert frames[0]['type'] == 'lesson_stop'
        assert frames[0]['body'] == expected_body
        assert current._can_supersede_unsent_completion() is False
        events = [event for batch in current.forwarder.batches for event in batch['events']]
        assert not any(event['type'] in {'lesson_completed', 'lesson_abandoned'} for event in events)
        await current.on_lesson_ack(_control_ack(current, frames[0], 1))
        events = [event for batch in current.forwarder.batches for event in batch['events']]
        assert sum(event['type'] == 'lesson_completed' for event in events) == 1
        assert not any(event['type'] == 'lesson_abandoned' for event in events)
    finally:
        release_retry.set()
        for task in (retry, cancel):
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        store.store = original_store
        runtime._send = original_send
        await runtime.close()
        if current is not runtime:
            await current.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['stop', 'cancel'])
async def test_never_sent_completion_retry_transfers_proof_before_second_store(operation):
    runtime = runtime_with_store()
    store = runtime._course_mode_snapshot_store
    original_store = store.store
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0
    retry = superseding = None

    async def fail_then_hold(*args):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConnectionError('owned first snapshot failure')
        if calls == 2:
            entered.set()
            await release.wait()
        await original_store(*args)

    try:
        store.store = fail_then_hold
        with pytest.raises(ConnectionError):
            await runtime._emit('lesson_stop', body={
                'reason': 'COMPLETED', 'cinematicPhase': {
                    'command': 'stop', **runtime._cinematic_identity_payload(),
                },
            })
        old_intent = runtime._terminal_lifecycle
        assert runtime._can_supersede_unsent_completion()
        retry = asyncio.create_task(runtime.start_protocol(preloaded=True))
        await asyncio.wait_for(entered.wait(), 1)
        assert runtime._terminal_lifecycle is not old_intent
        assert runtime._can_supersede_unsent_completion()
        superseding = asyncio.create_task(getattr(runtime, operation)())
        await asyncio.sleep(0)
        release.set()
        outcomes = await asyncio.wait_for(asyncio.gather(retry, superseding, return_exceptions=True), 2)
        assert not any(frame['body'].get('reason') == 'COMPLETED' for frame in _frames(runtime)), outcomes
        frames = _frames(runtime)
        assert len(frames) == 1
        assert frames[0]['body'].get('reason') in {'CANCELLED', 'cancelled'}
        saved = await store.load('terminal-test-device', runtime.assignment_id)
        assert saved['terminalLifecycle']['body'] == frames[0]['body']
    finally:
        release.set()
        for task in (retry, superseding):
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        store.store = original_store
        await runtime.close()
