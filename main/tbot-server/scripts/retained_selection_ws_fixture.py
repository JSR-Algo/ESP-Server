"""Local software selection transport, backed by actual native firmware callbacks.

This fixture cannot render, download, teach, or report READY. Credentials come
from the operator environment; the synthetic identity and loopback are mandatory.
"""
import argparse
import asyncio
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile
import uuid
from urllib.parse import urlsplit

from websockets.asyncio.client import connect

TOOLS = ('self.lesson_assets.retained_selection', 'self.lesson_assets.selection_state')


class local_connect(connect):
    def process_redirect(self, exc):
        return exc


def validate_target(url, fixture):
    parsed = urlsplit(url)
    if (parsed.scheme != 'ws' or parsed.hostname not in ('127.0.0.1', '::1')
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or fixture.get('scope') != 'isolated-local-test'
            or fixture.get('physicalAcceptance') is not False
            or fixture.get('serialNumber') != 'M1-SOFTWARE-24-ONLY'
            or fixture.get('macAddress') != '14:c1:9f:d1:a8:49'):
        raise ValueError('explicit loopback software fixture required')
    uuid.UUID(fixture['deviceId'])
    return fixture['macAddress']


async def dispatch(request, native):
    if not isinstance(request, dict) or 'id' not in request:
        return None
    response = {'jsonrpc': '2.0', 'id': request['id']}
    method = request.get('method')
    if request.get('jsonrpc') != '2.0':
        response['error'] = {'code': -32600, 'message': 'Invalid request'}
    elif method == 'initialize':
        response['result'] = {'protocolVersion': '2024-11-05', 'capabilities': {'tools': {}},
            'serverInfo': {'name': 'native-selection-software-fixture', 'version': '1'}}
    elif method == 'tools/list':
        response['result'] = {'tools': [{'name': name, 'description': 'Native selection only; no READY',
            'inputSchema': {'type': 'object', 'properties': (
                {'operation': {'type': 'object'}} if name == TOOLS[0] else {}),
                'required': ['operation'] if name == TOOLS[0] else []}} for name in TOOLS]}
    elif method == 'tools/call':
        params = request.get('params')
        if not isinstance(params, dict) or params.get('name') not in TOOLS or not isinstance(params.get('arguments', {}), dict):
            response['error'] = {'code': -32602, 'message': 'Unsupported selection request'}
        else:
            try:
                result = await native(params['name'], params.get('arguments', {}))
                response['result'] = {'content': [{'type': 'text', 'text': json.dumps(result)}], 'isError': False}
            except (ValueError, OSError, asyncio.TimeoutError):
                response['error'] = {'code': -32000, 'message': 'Native selection refused'}
    else:
        response['error'] = {'code': -32601, 'message': 'Method not found'}
    return response


class NativeSelection:
    def __init__(self, descriptor, state_path):
        data = Path(descriptor['path']).resolve(strict=True).read_bytes()
        self.digest = descriptor['sha256']
        if hashlib.sha256(data).hexdigest() != self.digest:
            raise ValueError('native binary hash mismatch')
        self.snapshot = tempfile.TemporaryDirectory(prefix='selection-native-')
        self.binary = Path(self.snapshot.name) / 'native'
        self.binary.write_bytes(data)
        self.binary.chmod(0o500)
        self.environment = dict(os.environ, TBOT_RETAINED_TEST_STATE_PATH=str(state_path))

    def close(self):
        self.snapshot.cleanup()

    async def __call__(self, name, arguments):
        if name not in TOOLS:
            raise ValueError('unsupported tool')
        data = json.dumps(arguments).encode()
        if len(data) > 16384:
            raise ValueError('oversized native arguments')
        if hashlib.sha256(self.binary.read_bytes()).hexdigest() != self.digest:
            raise ValueError('native snapshot changed')
        process = await asyncio.create_subprocess_exec(str(self.binary), '--selection-rpc', name,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, env=self.environment)
        try:
            output, _ = await asyncio.wait_for(process.communicate(data), timeout=10)
            if hashlib.sha256(self.binary.read_bytes()).hexdigest() != self.digest:
                raise ValueError('native snapshot changed')
            if process.returncode or len(output) > 65536:
                raise ValueError('native callback refused')
            result = json.loads(output)
            if not isinstance(result, dict) or 'ready' in result:
                raise ValueError('invalid native selection receipt')
            return result
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()


async def run(args):
    from core.auth import AuthManager
    fixture = json.loads(Path(args.fixture).read_text())
    mac = validate_target(args.url, fixture)
    secret = os.environ['SELECTION_FIXTURE_AUTH_KEY']
    if not secret:
        raise ValueError('authentication key required')
    root = Path(args.state_root).resolve(strict=True)
    with (root / 'connection.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        native = NativeSelection(json.loads(Path(args.native_descriptor).read_text()), root / 'selection.record')
        client = fixture['deviceId']
        token = AuthManager(secret).generate_token(client, mac)
        try:
            await serve(args.url, mac, client, token, native)
        finally:
            native.close()


async def serve(url, mac, client, token, native):
    async with local_connect(url, additional_headers={
            'device-id': mac, 'client-id': client, 'authorization': 'Bearer ' + token},
            open_timeout=15, close_timeout=5, max_size=65536) as socket:
        await socket.send(json.dumps({'type': 'hello', 'version': 1, 'transport': 'websocket',
            'features': {'mcp': True}, 'audio_params': {'format': 'opus',
                'sample_rate': 24000, 'channels': 1, 'frame_duration': 60}}))
        print('Software selection fixture connected; no physical/READY capability', flush=True)
        async for raw in socket:
            if not isinstance(raw, str):
                continue
            frame = json.loads(raw)
            if frame.get('type') != 'mcp':
                continue
            response = await dispatch(frame.get('payload'), native)
            if response is not None:
                await socket.send(json.dumps({'type': 'mcp', 'payload': response}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ('url', 'fixture', 'native-descriptor', 'state-root'):
        parser.add_argument('--' + flag, required=True)
    asyncio.run(run(parser.parse_args()))
