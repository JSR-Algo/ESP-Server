import asyncio
import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest

from core.lesson.retained_pack_materializer import materialize_retained_operation
from core.lesson.retained_pack_store import RetainedPackStore
from core.lesson.sd_pack_materializer import MaterializationError, _shared_store
from core.lesson.sd_pack_gc import shared_protected_cache_keys
from core.lesson.shared_asset_store import PackDeletionRefused


def fixture(tmp_path, monkeypatch):
    monkeypatch.setenv('LESSON_ASSET_ALLOWED_ORIGINS', 'https://cdn.example.com')
    monkeypatch.setenv('LESSON_SD_MAX_FILE_BYTES', '64')
    monkeypatch.setenv('LESSON_SD_MAX_PACK_BYTES', '128')
    config = {'lesson': {'asset_pack_mount_root': str(tmp_path / 'tbot/lesson-assets')}}
    store = _shared_store(config)
    retained = RetainedPackStore(store)
    path = Path(__file__).resolve().parents[1] / 'contracts/retained-assignment-pack.v1.vectors.json'
    op = json.loads(path.read_text())['valid'][0]['operation']
    op['consumerIdentity'] = retained.consumer_identity
    content = b'original bytes'
    pack = json.loads(op['packCanonicalJson'])
    pack['assets'][0].update(size=len(content), sha256=hashlib.sha256(content).hexdigest())
    op['packCanonicalJson'] = json.dumps(pack, sort_keys=True, separators=(',', ':'))
    op['selection']['packDescriptorChecksum'] = hashlib.sha256(op['packCanonicalJson'].encode()).hexdigest()
    return config, store, op, content


class Stream:
    status_code = 200
    headers = {}

    def __init__(self, content, started=None, finish=None):
        self.content, self.started, self.finish = content, started, finish

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def raise_for_status(self):
        pass

    async def aiter_bytes(self, _chunk_size):
        if self.started:
            self.started.set()
            await self.finish.wait()
        yield self.content


class Client:
    def __init__(self, stream):
        self.response = stream

    def stream(self, method, url, **_kwargs):
        assert method == 'GET'
        assert url == 'https://cdn.example.com/farm.png'
        return self.response


async def public_resolver(_host):
    return ['93.184.216.34']


def cancel(op):
    return {**{k: v for k, v in op.items() if k != 'packCanonicalJson'}, 'action': 'release',
            'requestRevision': 2, 'desiredSelectionRevision': 2, 'operationId': str(uuid4())}


@pytest.mark.asyncio
async def test_real_store_materializes_and_replays_exact_bytes(tmp_path, monkeypatch):
    config, store, op, content = fixture(tmp_path, monkeypatch)
    result = await materialize_retained_operation(op, config=config, client=Client(Stream(content)), resolver=public_resolver)
    assert result['state'] == 'materialized'
    assert store.is_pack_ready(op['selection']['cacheKey'])
    assert await materialize_retained_operation(op, config=config) == result
    with pytest.raises(PackDeletionRefused):
        store.delete_pack(op['selection']['cacheKey'])


@pytest.mark.asyncio
async def test_waiter_cancel_keeps_persistent_protection_until_explicit_release(tmp_path, monkeypatch):
    config, store, op, content = fixture(tmp_path, monkeypatch)
    started, finish = asyncio.Event(), asyncio.Event()
    task = asyncio.create_task(materialize_retained_operation(op, config=config,
        client=Client(Stream(content, started, finish)), resolver=public_resolver))
    try:
        await asyncio.wait_for(started.wait(), 2)
    except BaseException:
        if not task.done():
            task.cancel()
        await task
        raise
    assert op['selection']['cacheKey'] in shared_protected_cache_keys(store)
    with pytest.raises(ValueError, match='busy'):
        await materialize_retained_operation(cancel(op), config=config)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert op['selection']['cacheKey'] in shared_protected_cache_keys(store)
    result = await materialize_retained_operation(cancel(op), config=config)
    assert result['state'] == 'released'
    assert op['selection']['cacheKey'] not in shared_protected_cache_keys(store)


@pytest.mark.asyncio
async def test_bad_original_bytes_fail_without_replacement_or_releasing_protection(tmp_path, monkeypatch):
    config, store, op, _content = fixture(tmp_path, monkeypatch)
    with pytest.raises(MaterializationError):
        await materialize_retained_operation(op, config=config, client=Client(Stream(b'wrong')), resolver=public_resolver)
    assert not store.is_pack_ready(op['selection']['cacheKey'])
    assert op['selection']['cacheKey'] in shared_protected_cache_keys(store)


@pytest.mark.asyncio
async def test_bind_refuses_missing_firmware_capability(tmp_path, monkeypatch):
    config, _store, op, content = fixture(tmp_path, monkeypatch)
    await materialize_retained_operation(op, config=config, client=Client(Stream(content)), resolver=public_resolver)
    bind = {**{k: v for k, v in op.items() if k != 'packCanonicalJson'}, 'action': 'bind',
            'requestRevision': 2, 'desiredSelectionRevision': 2, 'operationId': str(uuid4()),
            'assignmentId': str(uuid4()), 'assignmentVersion': 1}
    with pytest.raises(MaterializationError) as failure:
        await materialize_retained_operation(bind, config=config)
    assert failure.value.code == 'RETAINED_DEVICE_CAPABILITY_UNAVAILABLE'


@pytest.mark.asyncio
async def test_nested_pack_validation_precedes_persistent_acquisition(tmp_path, monkeypatch):
    config, store, op, _content = fixture(tmp_path, monkeypatch)
    pack = json.loads(op['packCanonicalJson'])
    pack['assets'][0]['localPath'] = '/sdcard/other-owner/asset.png'
    op['packCanonicalJson'] = json.dumps(pack, sort_keys=True, separators=(',', ':'))
    op['selection']['packDescriptorChecksum'] = hashlib.sha256(op['packCanonicalJson'].encode()).hexdigest()
    with pytest.raises(MaterializationError):
        await materialize_retained_operation(op, config=config)
    assert op['selection']['cacheKey'] not in shared_protected_cache_keys(store)
