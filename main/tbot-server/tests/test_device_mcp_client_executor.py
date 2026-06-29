import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from core.providers.tools.base import ToolType
from core.providers.tools.device_mcp.mcp_client import MCPClient
from core.providers.tools.device_mcp.mcp_executor import DeviceMCPExecutor
from plugins_func.register import Action


class MCPClientTest(unittest.IsolatedAsyncioTestCase):
    async def test_tracks_ready_tools_cache_ids_and_call_results(self):
        client = MCPClient()

        self.assertFalse(await client.is_ready())
        await client.set_ready(True)
        self.assertTrue(await client.is_ready())
        await client.add_tool(
            {
                "name": "self.audio.speaker.set_volume",
                "description": "Set volume using self.audio.speaker.set_volume",
                "inputSchema": {
                    "type": "object",
                    "properties": {"volume": {"type": "integer"}},
                    "required": ["volume"],
                },
            }
        )

        tools = client.get_available_tools()
        self.assertIs(tools, client.get_available_tools())
        self.assertTrue(client.has_tool("self_audio_speaker_set_volume"))
        self.assertEqual(client.name_mapping["self_audio_speaker_set_volume"], "self.audio.speaker.set_volume")
        self.assertEqual(tools[0]["function"]["parameters"]["required"], ["volume"])
        self.assertEqual(await client.get_next_id(), 1)
        self.assertEqual(await client.get_next_id(), 2)

        result_future = asyncio.Future()
        await client.register_call_result_future(7, result_future)
        await client.resolve_call_result(7, "ok")
        self.assertEqual(result_future.result(), "ok")

        error_future = asyncio.Future()
        await client.register_call_result_future(8, error_future)
        await client.reject_call_result(8, RuntimeError("bad"))
        with self.assertRaisesRegex(RuntimeError, "bad"):
            error_future.result()

        leftover = asyncio.Future()
        await client.register_call_result_future(9, leftover)
        await client.cleanup_call_result(9)
        self.assertNotIn(9, client.call_results)


class _FakeMCPClient:
    def __init__(self, ready=True):
        self.ready = ready
        self.tools = {
            "self_tool": {
                "type": "function",
                "function": {"name": "self_tool", "description": "tool"},
            }
        }

    async def is_ready(self):
        return self.ready

    def get_available_tools(self):
        return list(self.tools.values())

    def has_tool(self, name):
        return name in self.tools


class DeviceMCPExecutorTest(unittest.IsolatedAsyncioTestCase):
    async def test_execute_returns_error_when_client_missing_or_not_ready(self):
        no_client = SimpleNamespace()
        not_ready = SimpleNamespace(mcp_client=_FakeMCPClient(ready=False))

        self.assertEqual(
            (await DeviceMCPExecutor(no_client).execute(no_client, "self_tool", {})).action,
            Action.ERROR,
        )
        self.assertEqual(
            (await DeviceMCPExecutor(not_ready).execute(not_ready, "self_tool", {})).action,
            Action.ERROR,
        )

    async def test_execute_converts_results_to_action_or_llm_response(self):
        conn = SimpleNamespace(mcp_client=_FakeMCPClient())
        executor = DeviceMCPExecutor(conn)

        async def json_result(*_args, **_kwargs):
            return '{"action": "RESPONSE", "response": "done"}'

        async def text_result(*_args, **_kwargs):
            return "plain text"

        with patch("core.providers.tools.device_mcp.mcp_executor.call_mcp_tool", new=json_result):
            action_result = await executor.execute(conn, "self_tool", {"x": 1})
        with patch("core.providers.tools.device_mcp.mcp_executor.call_mcp_tool", new=text_result):
            llm_result = await executor.execute(conn, "self_tool", {})

        self.assertEqual(action_result.action, Action.RESPONSE)
        self.assertEqual(action_result.response, "done")
        self.assertEqual(llm_result.action, Action.REQLLM)
        self.assertEqual(llm_result.result, "plain text")

    async def test_execute_maps_call_errors_to_action_responses(self):
        conn = SimpleNamespace(mcp_client=_FakeMCPClient())
        executor = DeviceMCPExecutor(conn)

        async def value_error(*_args, **_kwargs):
            raise ValueError("missing")

        async def runtime_error(*_args, **_kwargs):
            raise RuntimeError("boom")

        with patch("core.providers.tools.device_mcp.mcp_executor.call_mcp_tool", new=value_error):
            missing = await executor.execute(conn, "self_tool", {})
        with patch("core.providers.tools.device_mcp.mcp_executor.call_mcp_tool", new=runtime_error):
            failed = await executor.execute(conn, "self_tool", {})

        self.assertEqual(missing.action, Action.NOTFOUND)
        self.assertEqual(missing.response, "missing")
        self.assertEqual(failed.action, Action.ERROR)
        self.assertEqual(failed.response, "boom")

    def test_get_tools_and_has_tool_reflect_client_state(self):
        no_client = SimpleNamespace()
        conn = SimpleNamespace(mcp_client=_FakeMCPClient())
        executor = DeviceMCPExecutor(conn)

        self.assertEqual(DeviceMCPExecutor(no_client).get_tools(), {})
        self.assertFalse(DeviceMCPExecutor(no_client).has_tool("self_tool"))
        tools = executor.get_tools()

        self.assertIn("self_tool", tools)
        self.assertEqual(tools["self_tool"].tool_type, ToolType.DEVICE_MCP)
        self.assertTrue(executor.has_tool("self_tool"))
        self.assertFalse(executor.has_tool("missing"))


if __name__ == "__main__":
    unittest.main()
