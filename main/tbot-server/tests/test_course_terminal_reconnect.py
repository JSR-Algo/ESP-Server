"""Pull-on-connect recovers terminal intent before content admission gates."""
import copy
from unittest.mock import AsyncMock

import pytest

from core.lesson.course_snapshot_store import MemoryCourseModeSnapshotStore
from core.lesson.runtime import LessonRuntime, maybe_start_lesson_on_connect
from tests.test_course_mode_runtime_integration import contract
from tests.test_course_terminal_lifecycle import Forwarder
from tests import test_lesson_runtime as legacy


@pytest.mark.asyncio
async def test_reconnect_pending_stop_does_not_require_new_sd_capacity_or_mcp(monkeypatch):
    import core.lesson.runtime as module
    import core.lesson.forwarder as forwarder_module

    conn = legacy._RepublishConn()
    conn.config = copy.deepcopy(conn.config)
    conn.config['lesson']['course_mode_v2_enabled'] = True
    helper = legacy.RepublishOnConnectTest()
    assignment = helper._assignment(lesson_version=3, assignment_version=1, state='RUNNING')
    manifest = legacy._build_manifest()
    manifest['courseModeContract'] = contract()
    store = MemoryCourseModeSnapshotStore()
    initial = LessonRuntime(
        conn, assignment=assignment, manifest=manifest, asset_cache=None,
        manifest_checksum=assignment['manifestChecksum'], forwarder=Forwarder(),
        course_mode_snapshot_store=store, course_mode_snapshot_device_id=conn.device_id,
    )
    initial.state = 'RUNNING'
    conn.lesson_runtime = initial
    await initial.stop()
    original = copy.deepcopy(initial._terminal_lifecycle)
    await initial.close()
    conn.lesson_runtime = None
    conn.websocket.sent.clear()
    restored = None
    undo = helper._patch_backend(assignment, manifest)
    try:
        monkeypatch.setattr(module, 'get_course_mode_snapshot_store', lambda: store)
        monkeypatch.setattr(forwarder_module, 'get_terminal_replay_store', lambda: type('Empty', (), {
            'load': AsyncMock(return_value=None),
        })())
        monkeypatch.setattr(forwarder_module, 'LessonEventForwarder', lambda **kw: Forwarder())
        monkeypatch.setattr(module, '_sd_pack_gc_for_connection', lambda *a: pytest.fail('terminal retry reached SD admission'))
        monkeypatch.setattr(module, '_wait_for_mcp_reconnect_ready', AsyncMock(side_effect=AssertionError('terminal retry reached MCP admission')))
        monkeypatch.setattr(LessonRuntime, 'preload_only', AsyncMock(side_effect=AssertionError('terminal retry preloaded content')))
        restored = await maybe_start_lesson_on_connect(conn)
        assert restored is not None
        assert restored.session_id == initial.session_id
        assert len(conn.websocket.sent) == 1
        import json
        frame = json.loads(conn.websocket.sent[0])
        assert frame['type'] == 'lesson_stop'
        assert frame['body'] == original['body']
        assert conn.lesson_start_status['code'] == 'TERMINAL_REPLAY_PENDING'
    finally:
        undo()
        if restored is not None:
            await restored.close()


@pytest.mark.asyncio
async def test_terminal_identity_refusal_closes_startup_resources_and_sets_status(monkeypatch):
    import core.lesson.runtime as module
    import core.lesson.forwarder as forwarder_module

    conn = legacy._RepublishConn()
    conn.config = copy.deepcopy(conn.config)
    conn.config['lesson']['course_mode_v2_enabled'] = True
    helper = legacy.RepublishOnConnectTest()
    assignment = helper._assignment(lesson_version=3, assignment_version=1, state='RUNNING')
    manifest = legacy._build_manifest()
    manifest['courseModeContract'] = contract()
    store = MemoryCourseModeSnapshotStore()
    initial = LessonRuntime(
        conn, assignment=assignment, manifest=manifest, asset_cache=None,
        manifest_checksum=assignment['manifestChecksum'], forwarder=Forwarder(),
        course_mode_snapshot_store=store, course_mode_snapshot_device_id=conn.device_id,
    )
    initial.state = 'RUNNING'
    conn.lesson_runtime = initial
    await initial.stop()
    await initial.close()
    conn.lesson_runtime = None
    conn.websocket.sent.clear()
    assignment = {**assignment, 'assignmentVersion': 2}
    undo = helper._patch_backend(assignment, manifest)
    allocated = Forwarder()
    allocated.aclose = AsyncMock()
    try:
        monkeypatch.setattr(module, 'get_course_mode_snapshot_store', lambda: store)
        monkeypatch.setattr(forwarder_module, 'get_terminal_replay_store', lambda: type('Empty', (), {
            'load': AsyncMock(return_value=None),
        })())
        monkeypatch.setattr(forwarder_module, 'LessonEventForwarder', lambda **kw: allocated)
        result = await maybe_start_lesson_on_connect(conn)
        assert result is None
        assert conn.lesson_runtime is None
        assert conn.lesson_start_status['code'] in {
            'COURSE_TERMINAL_IDENTITY_MISMATCH', 'START_REFUSED',
        }
        assert not conn.websocket.sent
        allocated.aclose.assert_awaited_once()
    finally:
        undo()
        await allocated.aclose()


@pytest.mark.asyncio
async def test_terminal_replay_send_failure_closes_startup_resources_and_sets_status(monkeypatch):
    import core.lesson.runtime as module
    import core.lesson.forwarder as forwarder_module

    conn = legacy._RepublishConn()
    conn.config = copy.deepcopy(conn.config)
    conn.config['lesson']['course_mode_v2_enabled'] = True
    helper = legacy.RepublishOnConnectTest()
    assignment = helper._assignment(lesson_version=3, assignment_version=1, state='RUNNING')
    manifest = legacy._build_manifest()
    manifest['courseModeContract'] = contract()
    store = MemoryCourseModeSnapshotStore()
    initial = LessonRuntime(
        conn, assignment=assignment, manifest=manifest, asset_cache=None,
        manifest_checksum=assignment['manifestChecksum'], forwarder=Forwarder(),
        course_mode_snapshot_store=store, course_mode_snapshot_device_id=conn.device_id,
    )
    initial.state = 'RUNNING'
    conn.lesson_runtime = initial
    await initial.stop()
    await initial.close()
    conn.lesson_runtime = None
    conn.websocket.sent.clear()
    conn.websocket.send = AsyncMock(side_effect=ConnectionError('owned disconnected socket'))
    undo = helper._patch_backend(assignment, manifest)
    allocated = Forwarder()
    allocated.aclose = AsyncMock()
    try:
        monkeypatch.setattr(module, 'get_course_mode_snapshot_store', lambda: store)
        monkeypatch.setattr(forwarder_module, 'get_terminal_replay_store', lambda: type('Empty', (), {
            'load': AsyncMock(return_value=None),
        })())
        monkeypatch.setattr(forwarder_module, 'LessonEventForwarder', lambda **kw: allocated)
        result = await maybe_start_lesson_on_connect(conn)
        assert result is None
        assert conn.lesson_runtime is None
        assert conn.lesson_start_status['code'] in {
            'TERMINAL_REPLAY_PENDING',
        }
        assert not conn.websocket.sent
        snapshot = await store.load(conn.device_id, assignment['assignmentId'])
        assert snapshot['terminalLifecycle']['body']['reason'] == 'CANCELLED'
        allocated.aclose.assert_awaited_once()
    finally:
        undo()
        await allocated.aclose()
