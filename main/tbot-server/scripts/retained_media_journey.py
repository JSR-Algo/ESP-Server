"""Canonical backend operations through actual ESP materialization with owned media.

The fixture transport reads generated local bytes. It does not prove public
HTTP/CDN reachability; the production PRIVATE_ADDRESS rule is unchanged.
"""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
import subprocess

run = Path(os.environ['RETAINED_TEST_RUNTIME_ROOT']).resolve(strict=True)
esp = Path(os.environ['COURSE_MODE_ADMIN_ESP_ROOT']).resolve(strict=True)
sys.path.insert(0, str(esp / 'main/tbot-server'))
from core.lesson.retained_pack_materializer import materialize_retained_operation
from core.lesson.retained_pack_store import RetainedPackStore
from core.lesson.sd_pack_materializer import _shared_store
from core.lesson.sd_pack_gc import shared_protected_cache_keys

root = Path(sys.argv[2]).resolve()
assert root.is_relative_to(Path(os.environ.get('RETAINED_TEST_SCRATCH_ROOT', str(run / 'tmp'))).resolve())
config = {'lesson': {'asset_pack_mount_root': str(root / 'device/tbot/lesson-assets'),
    'max_file_bytes': 1024 * 1024, 'max_pack_bytes': 4 * 1024 * 1024}}
store = _shared_store(config)
retained = RetainedPackStore(store)
if sys.argv[1] == 'identity':
    print(retained.consumer_identity)
    sys.exit(0)

operation = json.loads((root / 'operation.json').read_text())
index = json.loads((root / 'media-index.json').read_text())
pack = json.loads(operation['packCanonicalJson']) if operation['action'] != 'release' else None
os.environ['LESSON_ASSET_ALLOWED_ORIGINS'] = 'https://cdn.example.com'

class Stream:
    status_code = 200
    headers = {}
    def __init__(self, data): self.data = data
    async def __aenter__(self): return self
    async def __aexit__(self, *_): return False
    def raise_for_status(self): pass
    async def aiter_bytes(self, chunk_size):
        for offset in range(0, len(self.data), chunk_size):
            yield self.data[offset:offset + chunk_size]

class FixtureTransport:
    def stream(self, method, url, **_):
        assert method == 'GET' and url in index, url
        path = Path(index[url]).resolve()
        assert path.is_relative_to(root)
        return Stream(path.read_bytes())

async def fixture_resolver(_): return ['93.184.216.34']

async def main():
    if sys.argv[1] == 'release':
        result = await materialize_retained_operation(operation, config=config)
        assert result['state'] == 'released'
        (root / 'materializer-receipt.json').write_text(json.dumps(result, indent=2))
        print(json.dumps(result))
        return
    if sys.argv[1] == 'bind':
        from core.lesson.sd_pack_mcp_payload import build_firmware_sync_pack
        from core.lesson.runtime import _manifest_asset_cache_inputs
        from core.lesson.asset_cache import AssetCache
        binding = json.loads((root / 'binding.json').read_text())
        native = json.loads((run / 'runtime/retained-mcp-binary.json').read_text())
        binary = Path(native['path']).resolve()
        assert binary.is_relative_to(run / 'tmp') and hashlib.sha256(binary.read_bytes()).hexdigest() == native['sha256']
        manifest = json.loads((root / 'manifest.json').read_text())
        cache = AssetCache(assets=_manifest_asset_cache_inputs(manifest, manifest_checksum=pack['manifestChecksum']),
            profile='espTft', lesson_key=pack['lessonId'], lesson_version=pack['lessonVersion'],
            manifest_checksum=pack['manifestChecksum'], cache_root=str(root / 'runtime-cache'),
            asset_pack_mount_root=config['lesson']['asset_pack_mount_root'], shared_asset_store=store,
            client=FixtureTransport())
        assert await cache.preload()
        runtime_pack = cache.asset_pack_manifest(assignment_version=binding['assignmentVersion'],
            lesson_id=pack['lessonId'], lesson_version=pack['lessonVersion'], manifest_checksum=pack['manifestChecksum'])
        assert runtime_pack['ready'] and len(runtime_pack['assets']) == len(pack['assets'])
        assert {(a['key'], a['sha256'], a['size']) for a in runtime_pack['assets']} == {
            (a['key'], a['sha256'], a['size']) for a in pack['assets']}
        payload = build_firmware_sync_pack(runtime_pack)
        payload.update(selectionRevision=binding['desiredSelectionRevision'], retainedSelection=binding)
        (root / 'firmware-sync.json').write_text(json.dumps(payload))
        async def bind_device(_):
            subprocess.run([str(binary), str(root / 'binding.json'), str(root / 'firmware-sync.json'),
                str(root / 'media-index.json'), str(root / 'device-receipt.json'), str(root / 'sync-receipt.json')],
                env={**os.environ, 'TBOT_RETAINED_TEST_STATE_PATH': str(root / 'native-selection.record')}, check=True)
            return json.loads((root / 'device-receipt.json').read_text())
        receipt = await materialize_retained_operation(binding, config=config, bind_device=bind_device)
        assert receipt['state'] == 'bound'
        from types import SimpleNamespace
        from core.lesson.runtime import LessonRuntime
        from core.lesson.layered_cinematic_contract import project_layered_cinematic_phase
        runtime_state = SimpleNamespace(manifest_checksum=pack['manifestChecksum'], _retained_selection=binding)
        sync_receipt = json.loads((root / 'sync-receipt.json').read_text())
        attestation = LessonRuntime._sd_asset_sync_attestation(runtime_state, sync_receipt, runtime_pack)
        assert attestation and attestation['assetCount'] == len(pack['assets'])
        assert runtime_state._retained_device_receipt == json.loads((root / 'device-receipt.json').read_text())
        from core.lesson.runtime import _course_mode_v5_activity_authority
        activities, fallback_activities = _course_mode_v5_activity_authority(manifest)
        for phase in manifest['cinematicPhases']:
            project_layered_cinematic_phase(phase, runtime_pack,
                course_mode_activity_ids=activities, fallback_activity_ids=fallback_activities)
        (root / 'esp-attestation.json').write_text(json.dumps(attestation))
        (root / 'bind-receipt.json').write_text(json.dumps(receipt))
        assert pack['cacheKey'] in shared_protected_cache_keys(store)
        print('actual ESP durable BIND_PENDING/BOUND and native firmware receipt; owned file transport')
        return
    result = await materialize_retained_operation(operation, config=config,
        client=FixtureTransport(), resolver=fixture_resolver)
    assert result['state'] == 'materialized'
    assert store.is_pack_ready(pack['cacheKey'])
    assert pack['cacheKey'] in shared_protected_cache_keys(store)
    assert await materialize_retained_operation(operation, config=config) == result
    for asset in pack['assets']:
        source = Path(index[asset['url']]).read_bytes()
        assert len(source) == asset['size'] and hashlib.sha256(source).hexdigest() == asset['sha256']
    (root / 'materializer-receipt.json').write_text(json.dumps(result, indent=2))
    print(json.dumps({'assetsVerified': len(pack['assets']), 'receipt': result,
        'scope': 'actual ESP materializer/shared store; injected owned-file transport; valid procedural renderer media'}))

asyncio.run(main())
