import json

import pytest
from aiohttp import web

from core.api.retained_pack_handler import RetainedPackHandler


class Request:
    def __init__(self, body=None, secret='test-only-inert-secret', error=None):
        self.headers = {'X-Mint-Secret': secret, 'X-TBOT-Local-Sample-Demo': '1'}
        self.body = body
        self.error = error
        self.read = False

    async def json(self):
        self.read = True
        if self.error:
            raise self.error
        return self.body


@pytest.mark.asyncio
async def test_secret_required_before_any_request_body_or_storage_access(monkeypatch):
    monkeypatch.setenv('TBOT_DEVICE_MINT_SECRET', 'test-only-inert-secret')
    monkeypatch.setenv('TBOT_LOCAL_SAMPLE_DEMO_BYPASS', '1')
    request = Request(secret='wrong')
    assert (await RetainedPackHandler({}).handle_post(request)).status == 401
    assert not request.read
    monkeypatch.delenv('TBOT_DEVICE_MINT_SECRET')
    assert (await RetainedPackHandler({}).handle_post(Request())).status == 503


@pytest.mark.asyncio
async def test_malformed_and_oversized_body_return_sanitized_refusal(monkeypatch):
    monkeypatch.setenv('TBOT_DEVICE_MINT_SECRET', 'test-only-inert-secret')
    for request, status in [(Request(body={'ready': True}), 400),
                            (Request(error=ValueError('private-value')), 400),
                            (Request(error=web.HTTPRequestEntityTooLarge(max_size=1, actual_size=2)), 413)]:
        response = await RetainedPackHandler({}).handle_post(request)
        assert response.status == status
        assert 'private-value' not in response.text
        assert json.loads(response.text)['code']


@pytest.mark.asyncio
async def test_capability_readback_is_authenticated_stable_and_refuses_device_readiness(tmp_path, monkeypatch):
    monkeypatch.setenv('TBOT_DEVICE_MINT_SECRET', 'test-only-inert-secret')
    config = {'lesson': {'asset_pack_mount_root': str(tmp_path / 'tbot/lesson-assets')}}
    handler = RetainedPackHandler(config)
    denied = await handler.handle_get(Request(secret='wrong'))
    assert denied.status == 401
    assert not (tmp_path / 'tbot/lesson-retained').exists()
    first = json.loads((await handler.handle_get(Request())).text)['data']
    second = json.loads((await RetainedPackHandler(config).handle_get(Request())).text)['data']
    assert first == second
    assert first['preparationSupported'] is True
    assert first['deviceSelectionFencing'] is False
    from core.connection_registry import ConnectionRegistry
    wired = json.loads((await RetainedPackHandler(config, ConnectionRegistry()).handle_get(Request())).text)['data']
    assert wired == {**first, 'deviceSelectionFencing': True}
