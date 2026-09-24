import asyncio
import hashlib
import json
import os
import sys

import pytest

from scripts.retained_selection_ws_fixture import NativeSelection, dispatch, validate_target
from scripts import retained_selection_ws_fixture as fixture_module


def test_only_explicit_local_software_fixture_is_accepted():
    fixture = {'scope': 'isolated-local-test', 'physicalAcceptance': False,
               'serialNumber': 'M1-SOFTWARE-24-ONLY', 'macAddress': '14:c1:9f:d1:a8:49',
               'deviceId': 'a572f965-f628-4a7b-9077-2775a5d3fb91'}
    assert validate_target('ws://127.0.0.1:8037/tbot/v1/', fixture) == fixture['macAddress']
    for url in ('wss://m0-esp.tjbot.vn/tbot/v1/', 'ws://127.0.0.1.evil:8037/',
                'ws://user:pass@127.0.0.1:8037/', 'ws://localhost:8037/'):
        with pytest.raises(ValueError):
            validate_target(url, fixture)
    for change in ({'physicalAcceptance': True}, {'scope': 'physical'},
                   {'macAddress': '14:c1:9f:d1:ac:20'}, {'serialNumber': 'real-device'}):
        with pytest.raises(ValueError):
            validate_target('ws://127.0.0.1:8037/', {**fixture, **change})


@pytest.mark.asyncio
async def test_receipt_is_exact_native_result_with_rpc_correlation():
    calls = []
    receipt = {'contractVersion': 'retained-assignment-device.v1', 'state': 'bound'}
    async def native(name, arguments):
        calls.append((name, arguments))
        return receipt
    arguments = {'operation': {'action': 'bind'}}
    response = await dispatch({'jsonrpc': '2.0', 'id': 27, 'method': 'tools/call',
        'params': {'name': 'self.lesson_assets.retained_selection', 'arguments': arguments}}, native)
    assert calls == [('self.lesson_assets.retained_selection', arguments)]
    assert response['id'] == 27
    assert json.loads(response['result']['content'][0]['text']) == receipt
    assert 'ready' not in json.dumps(response)


@pytest.mark.asyncio
async def test_unsupported_tool_and_bad_requests_never_invoke_native():
    async def native(*_):
        pytest.fail('unsupported request reached native')
    for request in (
        {'jsonrpc': '2.0', 'id': 9, 'method': 'tools/call', 'params': {'name': 'self.lesson_assets.sync_to_sd'}},
        {'jsonrpc': '2.0', 'id': 10, 'method': 'tools/call', 'params': []},
        {'jsonrpc': '2.0', 'id': 11, 'method': 'other'},
    ):
        response = await dispatch(request, native)
        assert response['id'] == request['id'] and 'error' in response
    assert await dispatch({'jsonrpc': '2.0', 'method': 'notifications/initialized'}, native) is None


@pytest.mark.asyncio
async def test_capability_list_advertises_selection_only_and_failure_is_sanitized():
    async def native(*_):
        raise ValueError('private-secret')
    response = await dispatch({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'}, native)
    assert {t['name'] for t in response['result']['tools']} == {
        'self.lesson_assets.selection_state', 'self.lesson_assets.retained_selection'}
    failed = await dispatch({'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
        'params': {'name': 'self.lesson_assets.selection_state', 'arguments': {}}}, native)
    assert 'error' in failed and 'private-secret' not in json.dumps(failed)


@pytest.mark.asyncio
async def test_cancellation_is_not_converted_to_success_or_error_receipt():
    async def native(*_):
        raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await dispatch({'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
            'params': {'name': 'self.lesson_assets.selection_state', 'arguments': {}}}, native)


def native_fixture(tmp_path, source):
    binary = tmp_path / 'callback'
    binary.write_text('#!' + sys.executable + '\n' + source)
    binary.chmod(0o700)
    return {'path': str(binary), 'sha256': hashlib.sha256(binary.read_bytes()).hexdigest()}


def test_native_binary_identity_must_match(tmp_path):
    descriptor = native_fixture(tmp_path, 'print("{}")\n')
    with pytest.raises(ValueError, match='hash mismatch'):
        NativeSelection({**descriptor, 'sha256': '0' * 64}, tmp_path / 'state')


@pytest.mark.asyncio
async def test_native_subprocess_cancellation_reaps_process(tmp_path):
    marker = tmp_path / 'pid'
    descriptor = native_fixture(tmp_path,
        f'import os,time\nfrom pathlib import Path\nPath({str(marker)!r}).write_text(str(os.getpid()))\ntime.sleep(30)\n')
    native = NativeSelection(descriptor, tmp_path / 'state')
    task = asyncio.create_task(native('self.lesson_assets.selection_state', {}))
    try:
        for _ in range(100):
            if marker.exists():
                break
            await asyncio.sleep(.01)
        assert marker.exists()
        pid = int(marker.read_text())
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize('source', ['print("not json")', 'print("[]")',
    'print(\'{"ready":true}\')', 'raise SystemExit(1)'])
async def test_native_invalid_result_never_becomes_a_receipt(tmp_path, source):
    native = NativeSelection(native_fixture(tmp_path, source), tmp_path / 'state')
    with pytest.raises(ValueError):
        await native('self.lesson_assets.selection_state', {})


@pytest.mark.asyncio
async def test_replacing_descriptor_path_does_not_change_owned_native_code(tmp_path):
    descriptor = native_fixture(tmp_path, 'print(\'{"state":"unowned"}\')')
    native = NativeSelection(descriptor, tmp_path / 'state')
    replacement = tmp_path / 'replacement'
    replacement.write_text('#!' + sys.executable + '\nprint(\'{"state":"forged"}\')')
    replacement.chmod(0o700)
    os.replace(replacement, descriptor['path'])
    assert await native('self.lesson_assets.selection_state', {}) == {'state': 'unowned'}


@pytest.mark.asyncio
async def test_redirect_never_receives_fixture_credentials():
    import websockets
    from websockets.http11 import Response
    from websockets.datastructures import Headers
    hits = []
    async def target(connection):
        hits.append(connection.request)
    async with websockets.serve(target, '127.0.0.1', 0) as destination:
        port = destination.sockets[0].getsockname()[1]
        def redirect(connection, request):
            return Response(302, 'Found', Headers({'Location': f'ws://127.0.0.1:{port}/'}), b'')
        async with websockets.serve(target, '127.0.0.1', 0, process_request=redirect) as origin:
            origin_port = origin.sockets[0].getsockname()[1]
            with pytest.raises(websockets.exceptions.InvalidStatus):
                async with fixture_module.local_connect(f'ws://127.0.0.1:{origin_port}/',
                        additional_headers={'authorization': 'Bearer inert-test-value'}):
                    pytest.fail('redirect was followed')
    assert hits == []
