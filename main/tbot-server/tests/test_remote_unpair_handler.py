import json
import os
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp import ClientSession, web

from core.api.remote_unpair_handler import RemoteUnpairHandler
from core.handle.textHandler.systemAckMessageHandler import SystemAckMessageHandler


def _socket(*, send):
    async def ping():
        pong = asyncio.get_running_loop().create_future()
        pong.set_result(0.001)
        return pong
    return SimpleNamespace(send=send, ping=AsyncMock(side_effect=ping))


class _Request:
    def __init__(self, device_id="backend-device-uuid", secret="mint-secret"):
        self.match_info = {"deviceId": device_id}
        self.headers = {"X-Mint-Secret": secret} if secret is not None else {}


@pytest.mark.asyncio
async def test_uuid_request_accepts_mac_ack_through_actual_message_handler():
    connection = SimpleNamespace(device_id="14:c1:9f:d1:ac:20", websocket=_socket(send=AsyncMock()))
    handler = RemoteUnpairHandler({}, {connection.device_id: connection}, ack_timeout=0.01)
    connection.server = SimpleNamespace(remote_unpair_handler=handler)

    async def send_and_ack(raw):
        payload = json.loads(raw)
        await SystemAckMessageHandler().handle(connection, {**payload, "type": "system_ack"})

    connection.websocket.send.side_effect = send_and_ack
    with (
        patch.dict(os.environ, {"TBOT_DEVICE_MINT_SECRET": "mint-secret"}),
        patch.object(handler._connection_finder, "_find_connection", AsyncMock(return_value=connection)),
    ):
        response = await handler.handle_post(_Request())
    assert response.status == 202
    assert not handler._pending_acks


@pytest.mark.asyncio
async def test_parallel_requests_keep_their_own_ack_and_cleanup():
    sends = asyncio.Queue()
    connection = SimpleNamespace(websocket=_socket(send=AsyncMock(side_effect=sends.put)))
    handler = RemoteUnpairHandler({}, {"socket": connection}, ack_timeout=0.2)
    with (
        patch.dict(os.environ, {"TBOT_DEVICE_MINT_SECRET": "mint-secret"}),
        patch.object(handler._connection_finder, "_find_connection", AsyncMock(return_value=connection)),
    ):
        first = asyncio.create_task(handler.handle_post(_Request()))
        first_id = json.loads(await sends.get())["request_id"]
        second = asyncio.create_task(handler.handle_post(_Request()))
        second_id = json.loads(await sends.get())["request_id"]
        assert first_id != second_id
        assert await handler.acknowledge("backend-device-uuid", connection, first_id)
        assert not await handler.acknowledge("backend-device-uuid", connection, first_id)
        assert (await first).status == 202
        assert not second.done()
        assert await handler.acknowledge("backend-device-uuid", connection, second_id)
        assert (await second).status == 202
    assert not handler._pending_acks


@pytest.mark.asyncio
async def test_cancel_during_send_removes_pending_ack():
    sending = asyncio.Event()
    async def blocked_send(_raw):
        sending.set()
        await asyncio.Future()
    connection = SimpleNamespace(websocket=_socket(send=blocked_send))
    handler = RemoteUnpairHandler({}, {"socket": connection})
    with (
        patch.dict(os.environ, {"TBOT_DEVICE_MINT_SECRET": "mint-secret"}),
        patch.object(handler._connection_finder, "_find_connection", AsyncMock(return_value=connection)),
    ):
        task = asyncio.create_task(handler.handle_post(_Request()))
        await sending.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert not handler._pending_acks


@pytest.mark.asyncio
async def test_cancel_during_ack_wait_removes_pending_ack():
    sent = asyncio.Event()
    async def send(_raw):
        sent.set()
    connection = SimpleNamespace(websocket=_socket(send=send))
    handler = RemoteUnpairHandler({}, {"socket": connection})
    with (
        patch.dict(os.environ, {"TBOT_DEVICE_MINT_SECRET": "mint-secret"}),
        patch.object(handler._connection_finder, "_find_connection", AsyncMock(return_value=connection)),
    ):
        task = asyncio.create_task(handler.handle_post(_Request()))
        await sent.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert not handler._pending_acks


@pytest.mark.asyncio
async def test_ack_requires_current_socket_and_exact_request_id():
    sent = asyncio.Queue()
    connection = SimpleNamespace(device_id="mac", websocket=_socket(send=sent.put))
    replacement = SimpleNamespace(device_id="mac")
    connections = {"mac": connection}
    handler = RemoteUnpairHandler({}, connections, ack_timeout=0.1)
    with (
        patch.dict(os.environ, {"TBOT_DEVICE_MINT_SECRET": "mint-secret"}),
        patch.object(handler._connection_finder, "_find_connection", AsyncMock(return_value=connection)),
    ):
        task = asyncio.create_task(handler.handle_post(_Request()))
        request_id = json.loads(await sent.get())["request_id"]
        assert not await handler.acknowledge("mac", replacement, request_id)
        assert not await handler.acknowledge("mac", connection, "wrong-id")
        connections["mac"] = replacement
        assert not await handler.acknowledge("mac", connection, request_id)
        await handler.cancel_for_connection(connection)
        assert (await task).status == 409
    assert not handler._pending_acks


@pytest.mark.asyncio
async def test_three_http_websocket_unpair_ack_roundtrips():
    connections = {}
    ready = asyncio.Event()
    handler = RemoteUnpairHandler({}, connections)

    async def robot_socket(request):
        socket = web.WebSocketResponse()
        await socket.prepare(request)
        conn = SimpleNamespace(
            device_id="14:c1:9f:d1:ac:20",
            websocket=_socket(send=socket.send_str),
            server=SimpleNamespace(remote_unpair_handler=handler),
        )
        connections[conn.device_id] = conn
        ready.set()
        try:
            async for msg in socket:
                await SystemAckMessageHandler().handle(conn, json.loads(msg.data))
        finally:
            await handler.cancel_for_connection(conn)
            connections.pop(conn.device_id, None)
        return socket

    async def resolve_uuid(device_id):
        assert device_id == "backend-device-uuid"
        return next(iter(connections.values()))

    app = web.Application()
    app.router.add_get("/robot", robot_socket)
    app.router.add_post("/internal/devices/{deviceId}/remote-unpair", handler.handle_post)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    base = f"http://127.0.0.1:{runner.addresses[0][1]}"
    try:
        with (
            patch.dict(os.environ, {"TBOT_DEVICE_MINT_SECRET": "mint-secret"}),
            patch.object(handler._connection_finder, "_find_connection", resolve_uuid),
        ):
            async with ClientSession() as client:
                async with client.ws_connect(base + "/robot") as robot:
                    await ready.wait()
                    for _ in range(3):
                        response_task = asyncio.ensure_future(client.post(
                            base + "/internal/devices/backend-device-uuid/remote-unpair",
                            headers={"X-Mint-Secret": "mint-secret"},
                        ))
                        try:
                            command = await robot.receive_json(timeout=1)
                            assert command["type"] == "system" and command["command"] == "unpair"
                            await robot.send_json({**command, "type": "system_ack"})
                            response = await asyncio.wait_for(response_task, 1)
                            async with response:
                                assert response.status == 202
                                assert (await response.json())["data"]["delivered"]
                        finally:
                            if not response_task.done():
                                response_task.cancel()
                                await asyncio.gather(response_task, return_exceptions=True)
                    assert not handler._pending_acks
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_remote_unpair_requires_internal_secret():
    handler = RemoteUnpairHandler({}, {})
    with patch.dict(os.environ, {"TBOT_DEVICE_MINT_SECRET": "mint-secret"}, clear=False):
        response = await handler.handle_post(_Request(secret="wrong"))

    assert response.status == 401


@pytest.mark.asyncio
async def test_remote_unpair_targets_resolved_live_connection_with_fixed_command():
    websocket = _socket(send=AsyncMock())
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
    websocket = _socket(send=AsyncMock())
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
    websocket = _socket(send=AsyncMock())
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
    websocket = _socket(send=AsyncMock())
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
    websocket = _socket(send=AsyncMock())
    stale = SimpleNamespace(websocket=websocket, session_id="session-1")
    handler = RemoteUnpairHandler({}, {"robot-connection-key": object()})

    with (
        patch.dict(os.environ, {"TBOT_DEVICE_MINT_SECRET": "mint-secret"}, clear=False),
        patch.object(handler._connection_finder, "_find_connection", AsyncMock(return_value=stale)),
    ):
        response = await handler.handle_post(_Request())

    assert response.status == 409
    websocket.send.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["unpair", "wifi_setup"])
async def test_reconnect_during_identity_resolution_sends_only_to_current_socket(command):
    mac = "14:c1:9f:d1:ac:20"
    def connection():
        return SimpleNamespace(
            device_id=mac,
            config={"server": {"api_url": "https://backend.example"}},
            websocket=_socket(send=AsyncMock()),
        )
    stale, current = connection(), connection()
    connections = {mac: stale}
    handler = RemoteUnpairHandler({}, connections, ack_timeout=0.01)
    current.server = SimpleNamespace(remote_unpair_handler=handler)

    async def resolve(*_args, **_kwargs):
        # The old snapshot survives the await while the robot reconnects.
        await asyncio.sleep(0)
        connections[mac] = current
        return "backend-device-uuid", "fixture-token"

    async def acknowledge(raw):
        payload = json.loads(raw)
        if command == "unpair":
            await SystemAckMessageHandler().handle(current, {**payload, "type": "system_ack"})

    current.websocket.send.side_effect = acknowledge
    with (
        patch.dict(os.environ, {"TBOT_DEVICE_MINT_SECRET": "mint-secret"}),
        patch("config.device_token_client.resolve_device_identity", AsyncMock(side_effect=resolve)) as mint,
    ):
        endpoint = handler.handle_post if command == "unpair" else handler.handle_wifi_setup_post
        response = await endpoint(_Request())

    assert response.status == 202
    assert mint.await_count == 2
    stale.websocket.send.assert_not_awaited()
    current.websocket.send.assert_awaited_once()
    assert json.loads(current.websocket.send.await_args.args[0])["command"] == command
    assert not handler._pending_acks


@pytest.mark.asyncio
async def test_repeated_pre_send_replacement_is_bounded_and_never_sends():
    stale = SimpleNamespace(websocket=_socket(send=AsyncMock()))
    handler = RemoteUnpairHandler({}, {"socket": object()})
    finder = AsyncMock(return_value=stale)
    with (
        patch.dict(os.environ, {"TBOT_DEVICE_MINT_SECRET": "mint-secret"}),
        patch.object(handler._connection_finder, "_find_connection", finder),
    ):
        response = await handler.handle_post(_Request())
    assert response.status == 409
    assert finder.await_count == 3
    assert all(call.args == ("backend-device-uuid",) for call in finder.await_args_list)
    stale.websocket.send.assert_not_awaited()
    assert not handler._pending_acks


@pytest.mark.asyncio
async def test_send_failure_after_replacement_is_not_retried():
    current = SimpleNamespace(websocket=_socket(send=AsyncMock()))
    replacement = SimpleNamespace(websocket=_socket(send=AsyncMock()))
    connections = {"socket": current}
    handler = RemoteUnpairHandler({}, connections)
    async def failed_send(_raw):
        connections["socket"] = replacement
        raise OSError("delivery is ambiguous")
    current.websocket.send.side_effect = failed_send
    finder = AsyncMock(return_value=current)
    with (
        patch.dict(os.environ, {"TBOT_DEVICE_MINT_SECRET": "mint-secret"}),
        patch.object(handler._connection_finder, "_find_connection", finder),
    ):
        response = await handler.handle_post(_Request())
    assert response.status == 409
    finder.assert_awaited_once()
    replacement.websocket.send.assert_not_awaited()
    assert not handler._pending_acks


@pytest.mark.asyncio
async def test_half_open_socket_is_probed_then_replacement_receives_unpair_once():
    old = SimpleNamespace(websocket=_socket(send=AsyncMock()))
    new = SimpleNamespace(websocket=_socket(send=AsyncMock()))
    connections = {"socket": old}
    handler = RemoteUnpairHandler({}, connections, ack_timeout=0.02)
    async def old_ping():
        connections["socket"] = new
        raise OSError("old connection disappeared after robot reset")
    old.websocket.ping.side_effect = old_ping
    async def send_and_ack(raw):
        await handler.acknowledge("mac", new, json.loads(raw)["request_id"])
    new.websocket.send.side_effect = send_and_ack
    with (
        patch.dict(os.environ, {"TBOT_DEVICE_MINT_SECRET": "mint-secret"}),
        patch.object(handler._connection_finder, "_find_connection", AsyncMock(side_effect=lambda _: connections["socket"])),
    ):
        response = await handler.handle_post(_Request())
    assert response.status == 202
    old.websocket.ping.assert_awaited_once()
    old.websocket.send.assert_not_awaited()
    new.websocket.ping.assert_awaited_once()
    new.websocket.send.assert_awaited_once()
    assert not handler._pending_acks


@pytest.mark.asyncio
async def test_pong_from_replaced_socket_does_not_authorize_command():
    old = SimpleNamespace(websocket=_socket(send=AsyncMock()))
    new = SimpleNamespace(websocket=_socket(send=AsyncMock()))
    connections = {"socket": old}
    handler = RemoteUnpairHandler({}, connections, ack_timeout=0.02)
    async def old_ping():
        pong = asyncio.get_running_loop().create_future()
        connections["socket"] = new
        pong.set_result(0.001)
        return pong
    old.websocket.ping.side_effect = old_ping
    async def send_and_ack(raw):
        await handler.acknowledge("mac", new, json.loads(raw)["request_id"])
    new.websocket.send.side_effect = send_and_ack
    with (
        patch.dict(os.environ, {"TBOT_DEVICE_MINT_SECRET": "mint-secret"}),
        patch.object(handler._connection_finder, "_find_connection", AsyncMock(side_effect=lambda _: connections["socket"])),
    ):
        response = await handler.handle_post(_Request())
    assert response.status == 202
    old.websocket.send.assert_not_awaited()
    new.websocket.ping.assert_awaited_once()
    new.websocket.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_actual_websockets_ping_pong_precedes_unpair_ack():
    from websockets.asyncio.server import serve
    from websockets.asyncio.client import connect

    connections = {}
    ready = asyncio.Event()
    handler = RemoteUnpairHandler({}, connections, ack_timeout=0.2)
    async def robot_endpoint(socket):
        conn = SimpleNamespace(websocket=socket)
        connections["socket"] = conn
        ready.set()
        async for raw in socket:
            await handler.acknowledge("mac", conn, json.loads(raw)["request_id"])
    with patch.dict(os.environ, {"TBOT_DEVICE_MINT_SECRET": "mint-secret"}):
        async with serve(robot_endpoint, "127.0.0.1", 0) as server:
            async with connect(f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}") as robot:
                await ready.wait()
                conn = connections["socket"]
                with patch.object(handler._connection_finder, "_find_connection", AsyncMock(return_value=conn)):
                    request = asyncio.create_task(handler.handle_post(_Request()))
                    command = json.loads(await asyncio.wait_for(robot.recv(), timeout=1))
                    await robot.send(json.dumps({**command, "type": "system_ack"}))
                    assert (await request).status == 202
                    assert conn.websocket.latency > 0


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked_stage", ["lookup", "ping_send", "pong", "command_send"])
async def test_unpair_deadlines_bound_each_await_and_clean_pending(blocked_stage):
    connection = SimpleNamespace(websocket=_socket(send=AsyncMock()))
    handler = RemoteUnpairHandler({}, {"socket": connection}, readiness_timeout=0.02, delivery_timeout=0.05)
    entered = asyncio.Event()
    cancelled = asyncio.Event()
    async def blocked(*_args):
        entered.set()
        try:
            await asyncio.Future()
        finally:
            cancelled.set()
    finder = AsyncMock(return_value=connection)
    if blocked_stage == "lookup":
        finder.side_effect = blocked
    elif blocked_stage == "ping_send":
        connection.websocket.ping.side_effect = blocked
    elif blocked_stage == "pong":
        async def ping():
            return asyncio.create_task(blocked())
        connection.websocket.ping.side_effect = ping
    else:
        connection.websocket.send.side_effect = blocked
    with (
        patch.dict(os.environ, {"TBOT_DEVICE_MINT_SECRET": "mint-secret"}),
        patch.object(handler._connection_finder, "_find_connection", finder),
    ):
        response = await asyncio.wait_for(handler.handle_post(_Request()), timeout=0.5)
    assert entered.is_set() and cancelled.is_set()
    assert response.status == (504 if blocked_stage == "command_send" else 409)
    assert not handler._pending_acks
    if blocked_stage != "command_send":
        connection.websocket.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_connection_can_reconnect_before_readiness_deadline():
    connection = SimpleNamespace(websocket=_socket(send=AsyncMock()))
    handler = RemoteUnpairHandler({}, {"socket": connection}, readiness_timeout=0.5)
    async def send(raw):
        await handler.acknowledge("mac", connection, json.loads(raw)["request_id"])
    connection.websocket.send.side_effect = send
    finder = AsyncMock(side_effect=[None, connection])
    with (
        patch.dict(os.environ, {"TBOT_DEVICE_MINT_SECRET": "mint-secret"}),
        patch.object(handler._connection_finder, "_find_connection", finder),
    ):
        response = await handler.handle_post(_Request())
    assert response.status == 202
    connection.websocket.send.assert_awaited_once()
