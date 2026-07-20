import asyncio
import types
import unittest
from unittest.mock import patch

from core.handle.textHandler.mcpMessageHandler import McpTextMessageHandler


class McpTextMessageHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_payload_work_is_registered_with_connection_lifecycle(self):
        scheduled = []
        handled = asyncio.Event()

        async def fake_handle(conn, client, payload):
            handled.set()

        def schedule(coro):
            scheduled.append(coro)
            return asyncio.create_task(coro)

        conn = types.SimpleNamespace(
            mcp_client=object(),
            schedule_mcp_background_task=schedule,
        )

        with patch(
            "core.handle.textHandler.mcpMessageHandler.handle_mcp_message",
            side_effect=fake_handle,
        ):
            await McpTextMessageHandler().handle(conn, {"payload": {"id": 2}})
            await asyncio.wait_for(handled.wait(), timeout=0.5)

        self.assertEqual(len(scheduled), 1)


if __name__ == "__main__":
    unittest.main()
