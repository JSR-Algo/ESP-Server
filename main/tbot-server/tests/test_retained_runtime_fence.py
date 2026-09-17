import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from core.connection_registry import ConnectionRegistry
from core.lesson.runtime import LessonRuntime
from core.providers.tools.device_mcp.mcp_handler import MCPClient
from tests import test_lesson_runtime as legacy
from tests.test_retained_pack_binding import device_receipt


def runtime(tmp_path, *, receipt=True, mode='sd_pack'):
    assignment = legacy._build_assignment()
    assignment['assignmentId'] = str(uuid4())
    cache = legacy._FirmwareSyncAssetCache(ready=True)
    conn = legacy._FakeConn()
    conn.features['mcp'] = True
    conn.features['retainedSelection'] = 'retained-assignment-device.v1'
    conn.config = {'lesson': {'asset_delivery_mode': mode,
        'asset_pack_mount_root': str(tmp_path / 'tbot/lesson-assets')}}
    selection = {'lessonRowId': str(uuid4()), 'lessonKey': assignment['lessonId'],
        'lessonVersion': assignment['lessonVersion'], 'profile': 'espTft',
        'manifestVersion': 'teebot-lesson-renderer.v1', 'manifestChecksum': legacy._manifest_checksum(),
        'packDescriptorChecksum': 'b' * 64, 'cacheKey': cache.cache_key}
    op = {'contractVersion': 'retained-assignment-pack.v1', 'action': 'bind', 'operationId': str(uuid4()),
        'requestId': str(uuid4()), 'requestRevision': 2, 'deviceId': str(uuid4()),
        'consumerIdentity': str(uuid4()), 'desiredSelectionRevision': 2, 'selection': selection,
        'assignmentId': assignment['assignmentId'], 'assignmentVersion': assignment['assignmentVersion']}
    assignment['retainedSelection'] = op
    reports = []
    async def report(value):
        reports.append(value)
    rt = LessonRuntime(conn, assignment=assignment, manifest=legacy._build_manifest(),
        asset_cache=cache, forwarder=legacy._FakeForwarder(), manifest_checksum=legacy._manifest_checksum(),
        preload_status_reporter=report)
    conn.lesson_runtime = rt
    return rt, op, reports


@pytest.mark.asyncio
async def test_retained_runtime_rejects_bare_ready_ack_and_does_not_report_ready(tmp_path):
    rt, op, reports = runtime(tmp_path)
    pack = rt.asset_cache.asset_pack_manifest(assignment_version=rt.assignment_version,
        lesson_id=rt.lesson_id, lesson_version=rt.lesson_version, manifest_checksum=rt.manifest_checksum)
    result = {'ready': True, 'activated': True, 'cacheKey': pack['cacheKey'],
        'manifestChecksum': rt.manifest_checksum, 'downloadedCount': len(pack['assets']),
        'skippedCount': 0, 'failedCount': 0}
    try:
        assert rt._sd_asset_sync_attestation(result, pack) is None
        rt._start_preload_status_reports({'assets': pack['assets']})
        await asyncio.sleep(0)
        assert all(report['state'] != 'READY' for report in reports)
        result['retainedSelection'] = device_receipt(op)
        assert rt._sd_asset_sync_attestation(result, pack) is not None
        rt._start_preload_status_reports({'assets': pack['assets']})
        await asyncio.sleep(0)
        assert any(report.get('retainedDeviceReceipt') == device_receipt(op) for report in reports)
        assert rt._prepare_body()['retainedSelection'] == op
        assert rt._prepare_body()['selectionRevision'] == 2
        assert rt._sd_asset_sync_attestation({'ready': False}, pack) is None
        assert rt._retained_device_receipt is None
        rt._retained_device_receipt = device_receipt(op)
        rt.conn.mcp_client = None
        assert await rt._sync_sd_asset_pack_to_robot() is False
        assert rt._retained_device_receipt is None
    finally:
        await rt.close()


@pytest.mark.asyncio
async def test_retained_assignment_cannot_fall_back_to_online_delivery(tmp_path):
    rt, _, _ = runtime(tmp_path, mode='online')
    try:
        with pytest.raises(Exception, match='retained'):
            await rt.start()
        assert not rt.conn.websocket.sent
    finally:
        await rt.close()


@pytest.mark.asyncio
async def test_ordinary_online_prepare_observes_released_watermark(tmp_path):
    rt, op, _ = runtime(tmp_path, mode='online')
    rt._retained_selection = None
    rt._selection_revision = 0
    client = MCPClient()
    rt.conn.mcp_client = client
    sent = []
    async def send(raw):
        frame = json.loads(raw)
        sent.append(frame)
        if frame.get('type') == 'mcp':
            receipt = device_receipt(op)
            receipt.pop('assignmentId')
            receipt.pop('assignmentVersion')
            receipt.update(state='released', desiredSelectionRevision=3)
            await client.resolve_call_result(frame['payload']['id'], {'content': [{'text': json.dumps(receipt)}]})
    rt.conn.websocket.send = send
    try:
        await rt.start_protocol(preloaded=True)
        prepare = next(frame for frame in sent if frame.get('type') == 'lesson_prepare')
        assert prepare['body']['selectionRevision'] == 3
    finally:
        await rt.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('bound', [False, True])
async def test_ordinary_sync_observes_device_watermark_and_refuses_bound_owner(tmp_path, bound):
    from core.lesson.sd_pack_sync import call_sd_pack_sync_tool
    rt, op, _ = runtime(tmp_path)
    client = MCPClient()
    calls = []
    sent_packs = []
    async def send(raw):
        payload = json.loads(raw)['payload']
        name = payload['params']['name']
        calls.append(name)
        if name == 'self.lesson_assets.selection_state':
            response = device_receipt(op)
            if not bound:
                response.pop('assignmentId')
                response.pop('assignmentVersion')
                response.update(state='released', desiredSelectionRevision=3)
        else:
            sent_packs.append(payload['params']['arguments']['assetPack'])
            response = {'test': 'sync-dispatched'}
        await client.resolve_call_result(payload['id'], {'content': [{'text': json.dumps(response)}]})
    rt.conn.websocket.send = send
    pack = rt.asset_cache.asset_pack_manifest(assignment_version=rt.assignment_version,
        lesson_id=rt.lesson_id, lesson_version=rt.lesson_version, manifest_checksum=rt.manifest_checksum)
    try:
        if bound:
            with pytest.raises(ValueError):
                await call_sd_pack_sync_tool(rt.conn, client, pack)
            assert calls == ['self.lesson_assets.selection_state']
        else:
            await call_sd_pack_sync_tool(rt.conn, client, pack)
            assert calls == ['self.lesson_assets.selection_state', 'self.lesson_assets.sync_to_sd']
            assert sent_packs[0]['selectionRevision'] == 3
    finally:
        await rt.close()


@pytest.mark.asyncio
async def test_foreground_runtime_persists_bind_then_sends_exact_selection_to_actual_mcp(tmp_path):
    import hashlib
    from core.lesson.retained_pack_store import RetainedPackStore
    from core.lesson.sd_pack_materializer import _shared_store

    rt, op, _ = runtime(tmp_path)
    store = _shared_store(rt.conn.config)
    retained = RetainedPackStore(store)
    op['consumerIdentity'] = retained.consumer_identity
    rt._retained_selection['consumerIdentity'] = retained.consumer_identity
    pack = rt.asset_cache.asset_pack_manifest(assignment_version=rt.assignment_version,
        lesson_id=rt.lesson_id, lesson_version=rt.lesson_version, manifest_checksum=rt.manifest_checksum)
    canonical = {key: pack[key] for key in ['lessonId', 'lessonVersion', 'manifestChecksum', 'cacheKey', 'assets']}
    canonical['profile'] = 'espTft'
    raw = json.dumps(canonical, sort_keys=True, separators=(',', ':'))
    op['selection']['packDescriptorChecksum'] = hashlib.sha256(raw.encode()).hexdigest()
    rt._retained_selection['selection'] = dict(op['selection'])
    acquire = {**{k: v for k, v in op.items() if k not in {'assignmentId', 'assignmentVersion'}},
        'action': 'acquire', 'operationId': str(uuid4()), 'requestRevision': 1,
        'desiredSelectionRevision': 1, 'packCanonicalJson': raw}
    data = b'owned transport boundary fixture; not renderer media'
    digest = hashlib.sha256(data).hexdigest()
    store.put_bytes(data, digest)
    store.commit_pack(op['selection']['cacheKey'], {'fixture': digest})
    with retained.operation(acquire) as work:
        work.acquire()
        work.materialized()
    client = MCPClient()
    await client.set_ready(True)
    rt.conn.mcp_client = client
    rt.conn.client_id = op['deviceId']
    rt.conn.session_id = 'owned-runtime-session'
    registry = ConnectionRegistry()
    registry[op['deviceId']] = rt.conn
    rt.conn.server = SimpleNamespace(lesson_connections=registry)
    calls = []
    async def send(raw_message):
        payload = json.loads(raw_message)['payload']
        name = payload['params']['name']
        calls.append(payload)
        if name == 'self.lesson_assets.retained_selection':
            result = device_receipt(payload['params']['arguments']['operation'])
        else:
            requested = payload['params']['arguments']['assetPack']
            result = {'ready': True, 'activated': True, 'cacheKey': requested['cacheKey'],
                'manifestChecksum': requested['manifestChecksum'], 'downloadedCount': len(requested['assets']),
                'skippedCount': 0, 'failedCount': 0, 'retainedSelection': device_receipt(op)}
        await client.resolve_call_result(payload['id'], {'content': [{'text': json.dumps(result)}]})
    rt.conn.websocket.send = send
    try:
        assert await asyncio.wait_for(rt._sync_sd_asset_pack_to_robot(), 3)
        assert [call['params']['name'] for call in calls] == [
            'self.lesson_assets.retained_selection', 'self.lesson_assets.sync_to_sd']
        sent = calls[-1]['params']['arguments']['assetPack']
        assert sent['retainedSelection'] == op
        assert sent['selectionRevision'] == 2
        assert rt._retained_device_receipt == device_receipt(op)
        assert not client.call_results
    finally:
        await rt.close()
