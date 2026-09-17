import asyncio
import copy
import hashlib
import json
import multiprocessing
from pathlib import Path
from uuid import uuid4

import pytest

from core.lesson.retained_pack_store import RetainedPackStore
from core.lesson.sd_pack_gc import LivePackReference, shared_protected_cache_keys
from core.lesson.shared_asset_store import PackDeletionRefused, SharedAssetStore


def operation(consumer):
    path = Path(__file__).resolve().parents[1] / 'contracts/retained-assignment-pack.v1.vectors.json'
    op = json.loads(path.read_text())['valid'][0]['operation']
    op['consumerIdentity'] = consumer
    return op


def release(op):
    result = {k: v for k, v in op.items() if k != 'packCanonicalJson'}
    result.update(action='release', operationId=str(uuid4()), requestRevision=2, desiredSelectionRevision=2)
    return result


def ready(store, key):
    content = b'original bytes'
    digest = hashlib.sha256(content).hexdigest()
    store.put_bytes(content, digest)
    store.commit_pack(key, {'asset': digest})


def child_protection(root, queue):
    store = SharedAssetStore(root)
    queue.put(sorted(shared_protected_cache_keys(store)))


def test_durable_reference_is_seen_after_process_restart_and_by_delete(tmp_path):
    store = SharedAssetStore(tmp_path)
    retained = RetainedPackStore(store)
    op = operation(retained.consumer_identity)
    ready(store, op['selection']['cacheKey'])
    with retained.operation(op) as work:
        work.acquire()
    context = multiprocessing.get_context('spawn')
    queue = context.Queue()
    child = context.Process(target=child_protection, args=(str(tmp_path), queue))
    child.start()
    try:
        assert queue.get(timeout=10) == [op['selection']['cacheKey']]
        child.join(10)
        assert child.exitcode == 0
    finally:
        if child.is_alive():
            child.terminate()
            child.join(5)
        queue.close()
    with pytest.raises(PackDeletionRefused):
        store.delete_pack(op['selection']['cacheKey'], sweep=True)


def test_release_tombstone_prevents_late_acquire_and_preserves_independent_owner(tmp_path):
    store = SharedAssetStore(tmp_path)
    retained = RetainedPackStore(store)
    op = operation(retained.consumer_identity)
    key = op['selection']['cacheKey']
    ready(store, key)
    with retained.operation(op) as work:
        work.acquire()
    other = LivePackReference(store, key)
    try:
        with retained.operation(release(op)) as work:
            assert work.release()['state'] == 'released'
        with pytest.raises(ValueError):
            with retained.operation(op):
                pytest.fail('stale acquire admitted')
        with pytest.raises(PackDeletionRefused):
            store.delete_pack(key)
    finally:
        other.close()
    store.delete_pack(key)
    assert not store.is_pack_ready(key)


def test_unknown_acquire_cancel_and_same_operation_body_conflict(tmp_path):
    retained = RetainedPackStore(SharedAssetStore(tmp_path))
    op = operation(retained.consumer_identity)
    with retained.operation(op) as work:
        work.acquire()
    changed = copy.deepcopy(op)
    changed['requestRevision'] = 2
    with pytest.raises(ValueError):
        with retained.operation(changed):
            pytest.fail('operation identity changed')
    cancel = release(op)
    with retained.operation(cancel) as work:
        assert work.release()['state'] == 'released'
    with RetainedPackStore(SharedAssetStore(tmp_path)).operation(cancel) as work:
        assert work.release()['state'] == 'released'


def test_other_store_and_corrupt_reference_refuse_operations_and_deletion(tmp_path):
    store = SharedAssetStore(tmp_path)
    retained = RetainedPackStore(store)
    op = operation(str(uuid4()))
    with pytest.raises(ValueError):
        with retained.operation(op):
            pytest.fail('wrong store admitted')
    op = operation(retained.consumer_identity)
    with retained.operation(op) as work:
        work.acquire()
    state = tmp_path / 'lesson-retained' / 'devices' / (op['deviceId'] + '.json')
    state.write_text('{}')
    with pytest.raises(PackDeletionRefused):
        store.delete_pack(op['selection']['cacheKey'])


def test_released_phase_without_exact_durable_receipt_does_not_license_gc(tmp_path):
    store = SharedAssetStore(tmp_path)
    retained = RetainedPackStore(store)
    op = operation(retained.consumer_identity)
    key = op['selection']['cacheKey']
    ready(store, key)
    with retained.operation(op) as work:
        work.acquire()
    state_path = tmp_path / 'lesson-retained/devices' / (op['deviceId'] + '.json')
    state = json.loads(state_path.read_text())
    state['phase'] = 'RELEASED'
    state_path.write_text(json.dumps(state))
    with pytest.raises(PackDeletionRefused):
        store.delete_pack(key)


def test_releasing_one_device_preserves_same_pack_owned_by_another_device(tmp_path):
    store = SharedAssetStore(tmp_path)
    retained = RetainedPackStore(store)
    first = operation(retained.consumer_identity)
    second = copy.deepcopy(first)
    second.update(deviceId=str(uuid4()), requestId=str(uuid4()), operationId=str(uuid4()))
    key = first['selection']['cacheKey']
    ready(store, key)
    for op in [first, second]:
        with retained.operation(op) as work:
            work.acquire()
    with retained.operation(release(first)) as work:
        assert work.release()['state'] == 'released'
    with pytest.raises(PackDeletionRefused):
        store.delete_pack(key)
    with retained.operation(release(second)) as work:
        assert work.release()['state'] == 'released'
    store.delete_pack(key)
    assert not store.is_pack_ready(key)


def test_unknown_acquisition_reconciliation_persists_its_newer_fence(tmp_path):
    store = SharedAssetStore(tmp_path)
    retained = RetainedPackStore(store)
    op = operation(retained.consumer_identity)
    with retained.operation(op) as work:
        work.acquire()
    reconcile = {**{k: v for k, v in op.items() if k != 'packCanonicalJson'},
                 'action': 'reconcile', 'operationId': str(uuid4()), 'desiredSelectionRevision': 2}
    with retained.operation(reconcile) as work:
        assert work.reconcile()['state'] == 'protected'
    with pytest.raises(ValueError, match='stale'):
        with RetainedPackStore(SharedAssetStore(tmp_path)).operation(op):
            pytest.fail('old acquisition admitted after reconciliation')
    assert op['selection']['cacheKey'] in shared_protected_cache_keys(store)


@pytest.mark.asyncio
async def test_release_cannot_overtake_inflight_acquisition(tmp_path):
    retained = RetainedPackStore(SharedAssetStore(tmp_path))
    op = operation(retained.consumer_identity)
    started = asyncio.Event()
    finish = asyncio.Event()

    async def acquire():
        with retained.operation(op) as work:
            work.acquire()
            started.set()
            await finish.wait()

    task = asyncio.create_task(acquire())
    await started.wait()
    try:
        with pytest.raises(ValueError, match='busy'):
            with retained.operation(release(op)):
                pytest.fail('release overtook acquisition')
    finally:
        finish.set()
        await task
    with retained.operation(release(op)) as work:
        assert work.release()['state'] == 'released'
