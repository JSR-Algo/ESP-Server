import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from tests import test_connection_voice_provider_routing as routing_fixture
from tests.test_remote_unpair_handler import _socket
from core.api.remote_unpair_handler import RemoteUnpairHandler
from core.handle.textMessageHandlerRegistry import TextMessageHandlerRegistry
from core.handle.textMessageProcessor import TextMessageProcessor


@pytest.mark.asyncio
@pytest.mark.parametrize("binding", ["pending", "unbound", "bound"])
@pytest.mark.parametrize("voice_ready", [False, True])
async def test_unpair_ack_routes_as_control_before_voice_or_binding(binding, voice_ready):
    conn = routing_fixture.ConnectionVoiceProviderRoutingTest()._build_handler()
    conn.device_id = "14:c1:9f:d1:ac:20"
    conn.config["voice_mode"] = {"type": "google_live"}
    conn.need_bind = binding != "bound"
    if binding != "pending":
        conn.bind_completed_event.set()
    provider = SimpleNamespace(handle_text_message=AsyncMock(return_value=True))
    conn.voice_provider = provider if voice_ready else None
    conn._wait_for_voice_provider_ready = AsyncMock()
    conn._discard_message_with_bind_prompt = AsyncMock()
    remote = RemoteUnpairHandler({}, {conn.device_id: conn}, ack_timeout=0.05)
    conn.server = SimpleNamespace(remote_unpair_handler=remote)
    processor = TextMessageProcessor(TextMessageHandlerRegistry())

    async def send(raw):
        command = json.loads(raw)
        await conn._route_message(json.dumps({**command, "type": "system_ack"}))

    conn.websocket = _socket(send=send)
    request = SimpleNamespace(
        match_info={"deviceId": "backend-device-uuid"},
        headers={"X-Mint-Secret": "fixture-secret"},
    )
    with (
        patch.dict("os.environ", {"TBOT_DEVICE_MINT_SECRET": "fixture-secret"}),
        patch.object(remote._connection_finder, "_find_connection", AsyncMock(return_value=conn)),
        patch.object(routing_fixture.connection_module, "handleTextMessage", processor.process_message),
    ):
        response = await asyncio.wait_for(remote.handle_post(request), timeout=1.5)
    assert response.status == 202
    assert not remote._pending_acks
    provider.handle_text_message.assert_not_awaited()
    conn._wait_for_voice_provider_ready.assert_not_awaited()
    conn._discard_message_with_bind_prompt.assert_not_awaited()
