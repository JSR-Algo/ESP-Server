import json
import os
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from core.api.remote_unpair_handler import RemoteUnpairHandler


class _Request:
    def __init__(self, device_id="backend-device-uuid", secret="mint-secret"):
        self.match_info = {"deviceId": device_id}
        self.headers = {"X-Mint-Secret": secret} if secret is not None else {}


@pytest.mark.asyncio
async def test_remote_unpair_requires_internal_secret():
    handler = RemoteUnpairHandler({}, {})
    with patch.dict(os.environ, {"TBOT_DEVICE_MINT_SECRET": "mint-secret"}, clear=False):
        response = await handler.handle_post(_Request(secret="wrong"))

    assert response.status == 401


@pytest.mark.asyncio
async def test_remote_unpair_targets_resolved_live_connection_with_fixed_command():
    websocket = SimpleNamespace(send=AsyncMock())
    connection = SimpleNamespace(websocket=websocket, session_id="session-1")
    connections = {"robot-connection-key": connection}
    handler = RemoteUnpairHandler({}, connections)

    async def send_and_ack(raw):
        payload = json.loads(raw)
        asyncio.get_running_loop().call_soon(
            lambda: asyncio.create_task(
                handler.acknowledge(
                    "backend-device-uuid", connection, payload["request_id"]
                )
            )
        )

    websocket.send.side_effect = send_and_ack

    with (
        patch.dict(os.environ, {"TBOT_DEVICE_MINT_SECRET": "mint-secret"}, clear=False),
        patch.object(handler._connection_finder, "_find_connection", AsyncMock(return_value=connection)),
    ):
        response = await handler.handle_post(_Request())

    assert response.status == 202
    sent = json.loads(websocket.send.await_args.args[0])
    assert sent["type"] == "system"
    assert sent["command"] == "unpair"
    assert sent["request_id"]


@pytest.mark.asyncio
async def test_remote_unpair_times_out_without_ack():
    websocket = SimpleNamespace(send=AsyncMock())
    connection = SimpleNamespace(websocket=websocket, session_id="session-1")
    handler = RemoteUnpairHandler({}, {"robot-connection-key": connection}, ack_timeout=0.01)

    with (
        patch.dict(os.environ, {"TBOT_DEVICE_MINT_SECRET": "mint-secret"}, clear=False),
        patch.object(handler._connection_finder, "_find_connection", AsyncMock(return_value=connection)),
    ):
        response = await handler.handle_post(_Request())

    assert response.status == 504
    assert json.loads(response.text)["error"] == "DEVICE_UNPAIR_ACK_TIMEOUT"


@pytest.mark.asyncio
async def test_remote_unpair_fails_immediately_when_pending_connection_is_lost():
    websocket = SimpleNamespace(send=AsyncMock())
    connection = SimpleNamespace(websocket=websocket, session_id="session-1")
    connections = {"robot-connection-key": connection}
    handler = RemoteUnpairHandler({}, connections, ack_timeout=10)

    with (
        patch.dict(os.environ, {"TBOT_DEVICE_MINT_SECRET": "mint-secret"}, clear=False),
        patch.object(handler._connection_finder, "_find_connection", AsyncMock(return_value=connection)),
    ):
        request_task = asyncio.create_task(handler.handle_post(_Request()))
        while not handler._pending_acks:
            await asyncio.sleep(0)
        await handler.cancel_for_connection(connection)
        response = await asyncio.wait_for(request_task, timeout=0.1)

    assert response.status == 409
    assert json.loads(response.text)["error"] == "DEVICE_NOT_ONLINE"


@pytest.mark.asyncio
async def test_wifi_setup_targets_resolved_live_connection_without_unpairing():
    websocket = SimpleNamespace(send=AsyncMock())
    connection = SimpleNamespace(websocket=websocket, session_id="session-1")
    handler = RemoteUnpairHandler({}, {"robot-connection-key": connection})

    with (
        patch.dict(os.environ, {"TBOT_DEVICE_MINT_SECRET": "mint-secret"}, clear=False),
        patch.object(handler._connection_finder, "_find_connection", AsyncMock(return_value=connection)),
    ):
        response = await handler.handle_wifi_setup_post(_Request())

    assert response.status == 202
    websocket.send.assert_awaited_once_with(
        json.dumps({"type": "system", "command": "wifi_setup"}, separators=(",", ":"))
    )


@pytest.mark.asyncio
async def test_remote_unpair_rejects_offline_without_sending():
    handler = RemoteUnpairHandler({}, {})
    with (
        patch.dict(os.environ, {"TBOT_DEVICE_MINT_SECRET": "mint-secret"}, clear=False),
        patch.object(handler._connection_finder, "_find_connection", AsyncMock(return_value=None)),
    ):
        response = await handler.handle_post(_Request())

    assert response.status == 409
    assert json.loads(response.text)["error"] == "DEVICE_NOT_ONLINE"


@pytest.mark.asyncio
async def test_remote_unpair_rejects_connection_replaced_before_send():
    websocket = SimpleNamespace(send=AsyncMock())
    stale = SimpleNamespace(websocket=websocket, session_id="session-1")
    handler = RemoteUnpairHandler({}, {"robot-connection-key": object()})

    with (
        patch.dict(os.environ, {"TBOT_DEVICE_MINT_SECRET": "mint-secret"}, clear=False),
        patch.object(handler._connection_finder, "_find_connection", AsyncMock(return_value=stale)),
    ):
        response = await handler.handle_post(_Request())

    assert response.status == 409
    websocket.send.assert_not_awaited()
