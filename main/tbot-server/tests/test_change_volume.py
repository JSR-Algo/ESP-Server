import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from plugins_func.functions import change_volume as change_volume_module
from plugins_func.register import Action


class _DummyLogger:
    def bind(self, **kwargs):
        return self

    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None

    def error(self, *args, **kwargs):
        return None


class _FakeMCPClient:
    def __init__(
        self,
        ready=True,
        tools=("self_get_device_status", "self_audio_speaker_set_volume"),
    ):
        self._ready = ready
        self._tools = set(tools)

    async def is_ready(self):
        return self._ready

    def has_tool(self, name):
        return name in self._tools

class _BrokenToolsMCPClient(_FakeMCPClient):
    @property
    def tools(self):
        raise RuntimeError("tools unavailable")


class _FakeConn:
    def __init__(self, mcp_client=None, last_known_volume=None):
        self.mcp_client = mcp_client
        self.logger = _DummyLogger()
        if last_known_volume is not None:
            self._last_known_volume = last_known_volume


class _CapturingCallMcp:
    def __init__(self, status_response='{"audio_speaker": {"volume": 30}}'):
        self.status_response = status_response
        self.calls = []

    async def __call__(self, conn, mcp_client, tool_name, args_str, timeout=30):
        self.calls.append({"tool": tool_name, "args": args_str})
        if tool_name == change_volume_module.GET_STATUS_TOOL:
            return self.status_response
        if tool_name == change_volume_module.SET_VOLUME_TOOL:
            return "ok"
        return ""


def _patched_call_mcp_tool(capturing):
    return patch(
        "core.providers.tools.device_mcp.mcp_handler.call_mcp_tool",
        new=capturing,
    )


class ChangeVolumeTest(unittest.IsolatedAsyncioTestCase):
    async def test_set_clamps_and_sends_mcp_command(self):
        conn = _FakeConn(mcp_client=_FakeMCPClient())
        capturing = _CapturingCallMcp()

        with _patched_call_mcp_tool(capturing):
            result = await change_volume_module.change_volume(
                conn,
                action="set",
                level=150,
                response_success="Đã đặt âm lượng {volume}%",
            )

        self.assertEqual(result.action, Action.RESPONSE)
        self.assertEqual(result.result, "100")
        self.assertEqual(result.response, "Đã đặt âm lượng 100%")
        set_calls = [c for c in capturing.calls if c["tool"] == change_volume_module.SET_VOLUME_TOOL]
        self.assertEqual(len(set_calls), 1)
        self.assertEqual(json.loads(set_calls[0]["args"]), {"volume": 100})
        self.assertEqual(conn._last_known_volume, 100)

    async def test_up_queries_status_then_increases(self):
        conn = _FakeConn(mcp_client=_FakeMCPClient())
        capturing = _CapturingCallMcp(status_response='{"audio_speaker": {"volume": 30}}')

        with _patched_call_mcp_tool(capturing):
            result = await change_volume_module.change_volume(
                conn,
                action="up",
                step=15,
                response_success="Đã to lên còn {volume}%",
            )

        self.assertEqual(result.action, Action.RESPONSE)
        self.assertEqual(result.result, "45")
        self.assertEqual(result.response, "Đã to lên còn 45%")
        # First call queries status, second sets volume
        tools_called = [c["tool"] for c in capturing.calls]
        self.assertEqual(
            tools_called,
            [change_volume_module.GET_STATUS_TOOL, change_volume_module.SET_VOLUME_TOOL],
        )
        self.assertEqual(json.loads(capturing.calls[-1]["args"]), {"volume": 45})

    async def test_down_clamps_at_zero(self):
        conn = _FakeConn(mcp_client=_FakeMCPClient(), last_known_volume=5)
        capturing = _CapturingCallMcp(status_response='{"audio_speaker": {"volume": 5}}')

        with _patched_call_mcp_tool(capturing):
            result = await change_volume_module.change_volume(
                conn,
                action="down",
                step=20,
                response_success="Đã giảm xuống còn {volume}%",
            )

        self.assertEqual(result.action, Action.RESPONSE)
        self.assertEqual(result.result, "0")
        self.assertEqual(json.loads(capturing.calls[-1]["args"]), {"volume": 0})

    async def test_up_uses_last_known_and_default_step_when_status_is_invalid(self):
        conn = _FakeConn(mcp_client=_FakeMCPClient(), last_known_volume=33)
        capturing = _CapturingCallMcp(status_response="not json")

        with _patched_call_mcp_tool(capturing):
            result = await change_volume_module.change_volume(
                conn,
                action="up",
                step="bad",
            )

        self.assertEqual(result.action, Action.RESPONSE)
        self.assertEqual(result.result, "53")
        self.assertEqual(result.response, "Đã chỉnh âm lượng còn 53%")
        self.assertEqual(json.loads(capturing.calls[-1]["args"]), {"volume": 53})

    async def test_query_current_volume_returns_none_when_device_status_unavailable(self):
        not_ready = _FakeConn(mcp_client=_FakeMCPClient(ready=False))
        missing_status = _FakeConn(
            mcp_client=_FakeMCPClient(tools=(change_volume_module.SET_VOLUME_TOOL,))
        )
        non_dict_status = _FakeConn(mcp_client=_FakeMCPClient())
        capturing = _CapturingCallMcp(status_response=["bad"])

        self.assertIsNone(await change_volume_module._query_current_volume(not_ready))
        self.assertIsNone(await change_volume_module._query_current_volume(missing_status))
        with _patched_call_mcp_tool(capturing):
            self.assertIsNone(await change_volume_module._query_current_volume(non_dict_status))

    async def test_query_current_volume_returns_none_when_status_call_raises(self):
        conn = _FakeConn(mcp_client=_FakeMCPClient())

        async def _raise_call(*args, **kwargs):
            raise RuntimeError("status failed")

        with patch("core.providers.tools.device_mcp.mcp_handler.call_mcp_tool", new=_raise_call):
            self.assertIsNone(await change_volume_module._query_current_volume(conn))

    async def test_mute_caches_current_and_sets_zero(self):
        conn = _FakeConn(mcp_client=_FakeMCPClient())
        capturing = _CapturingCallMcp(status_response='{"audio_speaker": {"volume": 70}}')

        with _patched_call_mcp_tool(capturing):
            result = await change_volume_module.change_volume(
                conn,
                action="mute",
                response_success="Đã tắt tiếng",
            )

        self.assertEqual(result.action, Action.RESPONSE)
        # `mute` doesn't update _last_known_volume to 0 — it caches prior volume
        self.assertEqual(conn._last_known_volume, 0)  # final = 0 after set
        self.assertEqual(json.loads(capturing.calls[-1]["args"]), {"volume": 0})

    async def test_unmute_restores_last_known_volume(self):
        conn = _FakeConn(mcp_client=_FakeMCPClient(), last_known_volume=42)
        capturing = _CapturingCallMcp()

        with _patched_call_mcp_tool(capturing):
            result = await change_volume_module.change_volume(
                conn,
                action="unmute",
                response_success="Đã bật lại {volume}%",
            )

        self.assertEqual(result.action, Action.RESPONSE)
        self.assertEqual(result.result, "42")
        self.assertEqual(json.loads(capturing.calls[-1]["args"]), {"volume": 42})

    async def test_unmute_without_cached_volume_uses_default_volume(self):
        conn = _FakeConn(mcp_client=_FakeMCPClient())
        capturing = _CapturingCallMcp()

        with _patched_call_mcp_tool(capturing):
            result = await change_volume_module.change_volume(
                conn,
                action="unmute",
                response_success="Đã bật lại {volume}%",
            )

        self.assertEqual(result.action, Action.RESPONSE)
        self.assertEqual(result.result, str(change_volume_module.DEFAULT_VOLUME_ON_UNMUTE))
        self.assertEqual(
            json.loads(capturing.calls[-1]["args"]),
            {"volume": change_volume_module.DEFAULT_VOLUME_ON_UNMUTE},
        )

    async def test_mute_with_cached_volume_skips_status_lookup(self):
        conn = _FakeConn(mcp_client=_FakeMCPClient(), last_known_volume=25)
        capturing = _CapturingCallMcp()

        with _patched_call_mcp_tool(capturing):
            result = await change_volume_module.change_volume(
                conn,
                action="mute",
                response_success="Đã tắt tiếng",
            )

        self.assertEqual(result.action, Action.RESPONSE)
        self.assertEqual([call["tool"] for call in capturing.calls], [change_volume_module.SET_VOLUME_TOOL])

    async def test_returns_error_when_mcp_client_missing(self):
        conn = _FakeConn(mcp_client=None)

        result = await change_volume_module.change_volume(
            conn, action="up", response_success="x"
        )

        self.assertEqual(result.action, Action.ERROR)

    async def test_returns_error_for_unknown_action(self):
        conn = _FakeConn(mcp_client=_FakeMCPClient())

        result = await change_volume_module.change_volume(
            conn, action="explode", response_success="x"
        )

        self.assertEqual(result.action, Action.ERROR)

    async def test_set_without_level_returns_error(self):
        conn = _FakeConn(mcp_client=_FakeMCPClient())

        result = await change_volume_module.change_volume(
            conn, action="set", response_success="x"
        )

        self.assertEqual(result.action, Action.ERROR)

    async def test_set_with_invalid_level_returns_error(self):
        conn = _FakeConn(mcp_client=_FakeMCPClient())

        result = await change_volume_module.change_volume(
            conn, action="set", level="max", response_success="x"
        )

        self.assertEqual(result.action, Action.ERROR)

    async def test_returns_error_when_device_lacks_set_volume_tool(self):
        """If device MCP doesn't expose set_volume (under sanitized name), fail clearly."""
        conn = _FakeConn(mcp_client=_FakeMCPClient(tools=("self_get_device_status",)))
        capturing = _CapturingCallMcp(status_response='{"audio_speaker": {"volume": 30}}')

        with _patched_call_mcp_tool(capturing):
            result = await change_volume_module.change_volume(
                conn,
                action="up",
                response_success="ok",
            )

        self.assertEqual(result.action, Action.ERROR)

    async def test_set_device_volume_returns_false_when_client_not_ready(self):
        conn = _FakeConn(mcp_client=_FakeMCPClient(ready=False))

        self.assertFalse(await change_volume_module._set_device_volume(conn, 20))

    async def test_set_device_volume_handles_unreadable_tool_list(self):
        conn = _FakeConn(mcp_client=_BrokenToolsMCPClient(tools=()))

        self.assertFalse(await change_volume_module._set_device_volume(conn, 20))

    async def test_set_device_volume_returns_false_when_call_raises(self):
        conn = _FakeConn(mcp_client=_FakeMCPClient())

        async def _raise_call(*args, **kwargs):
            raise RuntimeError("set failed")

        with patch("core.providers.tools.device_mcp.mcp_handler.call_mcp_tool", new=_raise_call):
            self.assertFalse(await change_volume_module._set_device_volume(conn, 20))

    def test_constants_use_sanitized_tool_names(self):
        """Regression: MCP client keys tools by sanitized name (dots → underscores)."""
        self.assertEqual(change_volume_module.GET_STATUS_TOOL, "self_get_device_status")
        self.assertEqual(
            change_volume_module.SET_VOLUME_TOOL,
            "self_audio_speaker_set_volume",
        )


class PluginExecutorAwaitsCoroutineTest(unittest.IsolatedAsyncioTestCase):
    async def test_executor_awaits_coroutine_plugin_result(self):
        # Imports the executor module without pulling MCP server dependencies
        import sys
        if "core.providers.tools.server_plugins.plugin_executor" in sys.modules:
            del sys.modules["core.providers.tools.server_plugins.plugin_executor"]
        try:
            from core.providers.tools.server_plugins.plugin_executor import (
                ServerPluginExecutor,
            )
            from plugins_func.register import (
                FunctionItem,
                ToolType,
                ActionResponse,
                Action,
                all_function_registry,
            )
        except ImportError as exc:  # pragma: no cover - env-specific
            self.skipTest(f"missing dependency: {exc}")
            return

        async def _async_plugin(conn, value):
            return ActionResponse(action=Action.RESPONSE, response=f"got {value}")

        item = FunctionItem(
            name="_test_async_plugin",
            description={
                "type": "function",
                "function": {
                    "name": "_test_async_plugin",
                    "description": "test",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            func=_async_plugin,
            type=ToolType.SYSTEM_CTL,
        )
        all_function_registry[item.name] = item

        try:
            conn = SimpleNamespace(config={"plugins": {}}, logger=_DummyLogger())
            executor = ServerPluginExecutor(conn)
            result = await executor.execute(conn, item.name, {"value": "hi"})
            self.assertEqual(result.action, Action.RESPONSE)
            self.assertEqual(result.response, "got hi")
        finally:
            all_function_registry.pop(item.name, None)


if __name__ == "__main__":
    unittest.main()
