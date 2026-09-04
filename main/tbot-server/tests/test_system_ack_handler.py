from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.handle.textHandler.systemAckMessageHandler import SystemAckMessageHandler


@pytest.mark.asyncio
async def test_system_ack_forwards_unpair_request_id_to_remote_unpair_handler():
    acknowledge = AsyncMock(return_value=True)
    connection = SimpleNamespace(
        device_id="device-1",
        server=SimpleNamespace(
            remote_unpair_handler=SimpleNamespace(acknowledge=acknowledge)
        ),
    )

    await SystemAckMessageHandler().handle(
        connection,
        {"type": "system_ack", "command": "unpair", "request_id": "request-1"},
    )

    acknowledge.assert_awaited_once_with("device-1", connection, "request-1")


@pytest.mark.asyncio
async def test_system_ack_ignores_malformed_or_non_unpair_ack():
    acknowledge = AsyncMock()
    connection = SimpleNamespace(
        device_id="device-1",
        server=SimpleNamespace(
            remote_unpair_handler=SimpleNamespace(acknowledge=acknowledge)
        ),
    )

    await SystemAckMessageHandler().handle(
        connection, {"type": "system_ack", "command": "wifi_setup"}
    )
    await SystemAckMessageHandler().handle(
        connection,
        {"type": "system_ack", "command": "unpair", "request_id": ""},
    )

    acknowledge.assert_not_awaited()
