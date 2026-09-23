import asyncio
import copy
import json
from uuid import uuid4

import pytest

from core.lesson.retained_pack_materializer import materialize_retained_operation
from core.lesson.retained_pack_store import RetainedPackStore
from core.lesson.sd_pack_gc import shared_protected_cache_keys
from core.lesson.shared_asset_store import PackDeletionRefused
from tests.test_retained_pack_materializer import Client, Stream, fixture, public_resolver


def binding(acquire):
    return {**{k: v for k, v in acquire.items() if k != 'packCanonicalJson'},
            'action': 'bind', 'operationId': str(uuid4()), 'requestRevision': 2,
            'desiredSelectionRevision': 2, 'assignmentId': str(uuid4()), 'assignmentVersion': 1}


def device_receipt(op):
    return {**{k: op[k] for k in ('operationId', 'requestId', 'requestRevision', 'deviceId',
                                  'consumerIdentity', 'desiredSelectionRevision',
                                  'assignmentId', 'assignmentVersion')},
            'contractVersion': 'retained-assignment-device.v1', 'state': 'bound',
            'cacheKey': op['selection']['cacheKey'],
            'packDescriptorChecksum': op['selection']['packDescriptorChecksum']}


def releasing(bind):
    return {**{k: v for k, v in bind.items() if k not in {'assignmentId', 'assignmentVersion'}},
            'action': 'release', 'operationId': str(uuid4()), 'requestRevision': 3,
            'desiredSelectionRevision': 3}


def release_receipt(op):
    return {**{k: op[k] for k in ('operationId', 'requestId', 'requestRevision', 'deviceId',
                                  'consumerIdentity', 'desiredSelectionRevision')},
            'contractVersion': 'retained-assignment-device.v1', 'state': 'released',
            'cacheKey': op['selection']['cacheKey'],
            'packDescriptorChecksum': op['selection']['packDescriptorChecksum']}


async def prepare(tmp_path, monkeypatch):
    config, store, op, content = fixture(tmp_path, monkeypatch)
    await materialize_retained_operation(op, config=config, client=Client(Stream(content)), resolver=public_resolver)
    return config, store, binding(op)


@pytest.mark.asyncio
async def test_bind_persists_before_dispatch_and_replays_after_restart(tmp_path, monkeypatch):
    config, store, op = await prepare(tmp_path, monkeypatch)
    calls = []

    async def dispatch(envelope):
        calls.append(envelope)
        state = json.loads((store.root / 'lesson-retained/devices' / (op['deviceId'] + '.json')).read_text())
        assert state['phase'] == 'BIND_PENDING'
        assert state['operations'][op['operationId']]['receipt'] is None
        with pytest.raises(PackDeletionRefused):
            store.delete_pack(op['selection']['cacheKey'])
        return device_receipt(envelope)

    receipt = await materialize_retained_operation(op, config=config, bind_device=dispatch)
    assert receipt['state'] == 'bound'
    RetainedPackStore(store)
    assert await materialize_retained_operation(op, config=config, bind_device=dispatch) == receipt
    assert len(calls) == 2  # A durable ESP receipt cannot establish a newly connected device's state.
    result = await materialize_retained_operation(releasing(op), config=config)
    assert result['state'] == 'release_pending'
    assert op['selection']['cacheKey'] in shared_protected_cache_keys(store)


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome', ['cancel', 'timeout', 'wrong-assignment', 'wrong-consumer', 'extra'])
async def test_unknown_or_invalid_device_receipt_never_releases_after_restart(tmp_path, monkeypatch, outcome):
    config, store, op = await prepare(tmp_path, monkeypatch)

    async def dispatch(envelope):
        if outcome == 'cancel':
            raise asyncio.CancelledError()
        if outcome == 'timeout':
            raise TimeoutError()
        result = device_receipt(envelope)
        if outcome == 'wrong-assignment':
            result['assignmentId'] = str(uuid4())
        elif outcome == 'wrong-consumer':
            result['consumerIdentity'] = str(uuid4())
        else:
            result['ready'] = True
        return result

    with pytest.raises((asyncio.CancelledError, TimeoutError, ValueError)):
        await materialize_retained_operation(op, config=config, bind_device=dispatch)
    RetainedPackStore(store)
    result = await materialize_retained_operation(releasing(op), config=config)
    assert result['state'] == 'release_pending'
    with pytest.raises(PackDeletionRefused):
        store.delete_pack(op['selection']['cacheKey'])


@pytest.mark.asyncio
async def test_reconcile_unknown_bind_preserves_protection_and_rejects_changed_assignment(tmp_path, monkeypatch):
    config, store, op = await prepare(tmp_path, monkeypatch)

    async def dispatch(_):
        raise TimeoutError()

    with pytest.raises(TimeoutError):
        await materialize_retained_operation(op, config=config, bind_device=dispatch)
    changed = copy.deepcopy(op)
    changed['assignmentId'] = str(uuid4())
    with pytest.raises(ValueError):
        await materialize_retained_operation(changed, config=config, bind_device=dispatch)
    reconcile = releasing(op)
    reconcile['action'] = 'reconcile'
    assert (await materialize_retained_operation(reconcile, config=config))['state'] == 'protected'
    release = releasing(op)
    release.update(requestRevision=4, desiredSelectionRevision=4)
    assert (await materialize_retained_operation(release, config=config))['state'] == 'release_pending'


@pytest.mark.asyncio
async def test_later_acquire_cannot_downgrade_a_device_owner_to_preparation(tmp_path, monkeypatch):
    config, store, acquire, content = fixture(tmp_path, monkeypatch)
    await materialize_retained_operation(acquire, config=config, client=Client(Stream(content)), resolver=public_resolver)
    op = binding(acquire)

    async def dispatch(envelope):
        return device_receipt(envelope)

    await materialize_retained_operation(op, config=config, bind_device=dispatch)
    acquire.update(operationId=str(uuid4()), requestRevision=3, desiredSelectionRevision=3)
    with pytest.raises(ValueError):
        await materialize_retained_operation(acquire, config=config)
    assert op['selection']['cacheKey'] in shared_protected_cache_keys(store)


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome', ['cancel', 'timeout', 'wrong-device', 'wrong-consumer',
                                   'stale-revision', 'wrong-request', 'wrong-checksum', 'extra', 'bound'])
async def test_uncertain_release_stays_protected_across_restart_until_exact_retry(tmp_path, monkeypatch, outcome):
    config, store, op = await prepare(tmp_path, monkeypatch)

    async def bind(envelope):
        return device_receipt(envelope)

    await materialize_retained_operation(op, config=config, bind_device=bind)
    release = releasing(op)

    async def failed(envelope):
        state = json.loads((store.root / 'lesson-retained/devices' / (op['deviceId'] + '.json')).read_text())
        assert state['phase'] == 'RELEASE_PENDING'
        with pytest.raises(PackDeletionRefused):
            store.delete_pack(op['selection']['cacheKey'])
        if outcome == 'cancel':
            raise asyncio.CancelledError()
        if outcome == 'timeout':
            raise TimeoutError()
        result = release_receipt(envelope)
        edits = {'wrong-device': ('deviceId', str(uuid4())),
                 'wrong-consumer': ('consumerIdentity', str(uuid4())),
                 'stale-revision': ('desiredSelectionRevision', 2),
                 'wrong-request': ('requestId', str(uuid4())),
                 'wrong-checksum': ('packDescriptorChecksum', '0' * 64),
                 'extra': ('ready', True), 'bound': ('state', 'bound')}
        key, value = edits[outcome]
        result[key] = value
        return result

    with pytest.raises((asyncio.CancelledError, TimeoutError, ValueError)):
        await materialize_retained_operation(release, config=config, release_device=failed)
    RetainedPackStore(store)
    assert op['selection']['cacheKey'] in shared_protected_cache_keys(store)
    with pytest.raises(PackDeletionRefused):
        store.delete_pack(op['selection']['cacheKey'])
    calls = []

    async def retry(envelope):
        calls.append(envelope)
        return release_receipt(envelope)

    receipt = await materialize_retained_operation(release, config=config, release_device=retry)
    assert receipt['state'] == 'released'
    assert op['selection']['cacheKey'] not in shared_protected_cache_keys(store)
    RetainedPackStore(store)
    assert await materialize_retained_operation(release, config=config, release_device=retry) == receipt
    assert calls == [release]
    with pytest.raises(ValueError):
        await materialize_retained_operation(op, config=config, bind_device=bind)


@pytest.mark.asyncio
async def test_release_keeps_device_lock_until_receipt_and_rejects_competing_operation(tmp_path, monkeypatch):
    config, store, op = await prepare(tmp_path, monkeypatch)

    async def bind(envelope):
        return device_receipt(envelope)

    await materialize_retained_operation(op, config=config, bind_device=bind)
    release = releasing(op)
    entered, finish = asyncio.Event(), asyncio.Event()

    async def dispatch(envelope):
        entered.set()
        await finish.wait()
        return release_receipt(envelope)

    task = asyncio.create_task(materialize_retained_operation(release, config=config, release_device=dispatch))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        with pytest.raises(ValueError, match='busy'):
            await materialize_retained_operation(release, config=config, release_device=dispatch)
        assert op['selection']['cacheKey'] in shared_protected_cache_keys(store)
    finally:
        finish.set()
        result = await task
    assert result['state'] == 'released'
