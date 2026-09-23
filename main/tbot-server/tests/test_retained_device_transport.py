import asyncio
import json
from types import SimpleNamespace

import pytest

from core.api.retained_pack_handler import RetainedPackHandler
from core.connection_registry import ConnectionRegistry
from core.providers.tools.device_mcp.mcp_handler import MCPClient
from tests.test_retained_pack_binding import device_receipt, prepare, releasing
from core.lesson.sd_pack_gc import shared_protected_cache_keys
from tests.test_retained_pack_handler import Request


class DeviceSocket:
    def __init__(self, client, reply=None):
        self.client = client
        self.sent = []
        self.reply = reply

    async def send(self, raw):
        payload = json.loads(raw)['payload']
        self.sent.append(payload)
        assert payload['method'] == 'tools/call'
        assert payload['params']['name'] == 'self.lesson_assets.retained_selection'
        op = payload['params']['arguments']['operation']
        result = self.reply if self.reply is not None else device_receipt(op)
        await self.client.resolve_call_result(payload['id'], {'content': [{'type': 'text', 'text': json.dumps(result)}]})


async def configured(tmp_path, monkeypatch, reply=None):
    monkeypatch.setenv('TBOT_DEVICE_MINT_SECRET', 'test-only-inert-secret')
    config, store, op = await prepare(tmp_path, monkeypatch)
    client = MCPClient()
    socket = DeviceSocket(client, reply)
    conn = SimpleNamespace(client_id=op['deviceId'], session_id='owned-connection',
                           mcp_client=client, websocket=socket, features={'mcp': True})
    registry = ConnectionRegistry()
    registry['02:00:00:00:00:01'] = conn
    return config, store, op, conn, registry


@pytest.mark.asyncio
async def test_authenticated_handler_keeps_release_protected_when_device_disconnects(tmp_path, monkeypatch):
    config, _store, op, conn, registry = await configured(tmp_path, monkeypatch)
    handler = RetainedPackHandler(config, registry)
    response = await handler.handle_post(Request(body=op))
    assert response.status == 200, response.text
    assert json.loads(response.text)['data']['state'] == 'bound'
    assert len(conn.websocket.sent) == 1
    assert conn.mcp_client.call_results == {}
    registry.clear()
    result = await handler.handle_post(Request(body=releasing(op)))
    assert result.status == 409
    assert op['selection']['cacheKey'] in shared_protected_cache_keys(_store)
    assert len(conn.websocket.sent) == 1


@pytest.mark.asyncio
async def test_authenticated_release_requires_exact_correlated_device_receipt(tmp_path, monkeypatch):
    config, store, op, conn, registry = await configured(tmp_path, monkeypatch)
    handler = RetainedPackHandler(config, registry)
    assert (await handler.handle_post(Request(body=op))).status == 200
    release = releasing(op)
    conn.websocket.reply = {
        **{key: release[key] for key in ('operationId', 'requestId', 'requestRevision',
            'deviceId', 'consumerIdentity', 'desiredSelectionRevision')},
        'contractVersion': 'retained-assignment-device.v1', 'state': 'released',
        'cacheKey': release['selection']['cacheKey'],
        'packDescriptorChecksum': release['selection']['packDescriptorChecksum'],
    }
    result = await handler.handle_post(Request(body=release))
    assert result.status == 200, result.text
    assert json.loads(result.text)['data']['state'] == 'released'
    assert len(conn.websocket.sent) == 2
    assert conn.websocket.sent[-1]['params']['arguments']['operation'] == release
    assert not conn.mcp_client.call_results
    assert op['selection']['cacheKey'] not in shared_protected_cache_keys(store)


@pytest.mark.asyncio
@pytest.mark.parametrize('action', ['bind', 'release'])
async def test_replaced_connection_is_refused_before_actual_send(tmp_path, monkeypatch, action):
    config, _store, op, conn, registry = await configured(tmp_path, monkeypatch)
    handler = RetainedPackHandler(config, registry)
    if action == 'release':
        assert (await handler.handle_post(Request(body=op))).status == 200
        conn.websocket.sent.clear()
        op = releasing(op)
    async with registry.reserve_current('02:00:00:00:00:01', conn, conn.session_id):
        pending = asyncio.create_task(handler.handle_post(Request(body=op)))
        await asyncio.sleep(0)
        registry['02:00:00:00:00:01'] = SimpleNamespace(session_id='replacement')
    result = await asyncio.wait_for(pending, 2)
    assert result.status == 409
    assert not conn.websocket.sent
    assert not conn.mcp_client.call_results


@pytest.mark.asyncio
async def test_release_still_requires_secret_before_dispatch(tmp_path, monkeypatch):
    config, store, op, conn, registry = await configured(tmp_path, monkeypatch)
    handler = RetainedPackHandler(config, registry)
    assert (await handler.handle_post(Request(body=op))).status == 200
    request = Request(body=releasing(op), secret='wrong')
    response = await handler.handle_post(request)
    assert response.status == 401
    assert not request.read
    assert len(conn.websocket.sent) == 1
    assert op['selection']['cacheKey'] in shared_protected_cache_keys(store)


@pytest.mark.asyncio
async def test_wrong_device_reply_is_refused_without_any_ready_claim(tmp_path, monkeypatch):
    config, _store, op, conn, registry = await configured(tmp_path, monkeypatch, {'state': 'bound'})
    response = await RetainedPackHandler(config, registry).handle_post(Request(body=op))
    assert response.status == 409
    assert len(conn.websocket.sent) == 1
    assert not conn.mcp_client.call_results
