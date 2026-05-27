import unittest
from unittest.mock import patch

from plugins_func.functions import raise_left_arm as raise_left_arm_module
from plugins_func.register import Action


class _DummyLogger:
    def bind(self, **kwargs):
        return self

    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None


class _FakeMCPClient:
    def __init__(self, ready=True, tools=(raise_left_arm_module.LEFT_ARM_TOOL,)):
        self._ready = ready
        self.tools = {name: {} for name in tools}

    async def is_ready(self):
        return self._ready

    def has_tool(self, name):
        return name in self.tools


class _FakeConn:
    def __init__(self, mcp_client=None):
        self.mcp_client = mcp_client
        self.logger = _DummyLogger()


class _CapturingCallMcp:
    def __init__(self, result=None):
        self.calls = []
        self.result = result or {
            "content": [{"type": "text", "text": "true"}],
            "isError": False,
        }

    async def __call__(self, conn, mcp_client, tool_name, args_str, timeout=30):
        self.calls.append({"tool": tool_name, "args": args_str})
        return self.result


def _patched_call_mcp_tool(capturing):
    return patch(
        "core.providers.tools.device_mcp.mcp_handler.call_mcp_tool",
        new=capturing,
    )


class RaiseLeftArmTest(unittest.IsolatedAsyncioTestCase):
    async def test_calls_device_mcp_tool(self):
        conn = _FakeConn(mcp_client=_FakeMCPClient())
        capturing = _CapturingCallMcp()

        with _patched_call_mcp_tool(capturing):
            result = await raise_left_arm_module.raise_left_arm(conn)

        self.assertEqual(result.action, Action.RESPONSE)
        self.assertEqual(result.result, "ok")
        self.assertEqual(result.response, "Đã nâng tay trái.")
        self.assertEqual(
            capturing.calls,
            [{"tool": raise_left_arm_module.LEFT_ARM_TOOL, "args": "{}"}],
        )

    async def test_returns_error_when_mcp_client_missing(self):
        result = await raise_left_arm_module.raise_left_arm(_FakeConn())

        self.assertEqual(result.action, Action.ERROR)

    async def test_returns_error_when_mcp_not_ready(self):
        conn = _FakeConn(mcp_client=_FakeMCPClient(ready=False))

        result = await raise_left_arm_module.raise_left_arm(conn)

        self.assertEqual(result.action, Action.ERROR)

    async def test_returns_error_when_tool_missing(self):
        conn = _FakeConn(mcp_client=_FakeMCPClient(tools=()))
        capturing = _CapturingCallMcp()

        with _patched_call_mcp_tool(capturing):
            result = await raise_left_arm_module.raise_left_arm(conn)

        self.assertEqual(result.action, Action.ERROR)
        self.assertEqual(capturing.calls, [])

    async def test_returns_error_when_main_reports_uart_failure(self):
        conn = _FakeConn(mcp_client=_FakeMCPClient())
        capturing = _CapturingCallMcp(
            result={"content": [{"type": "text", "text": "false"}], "isError": False}
        )

        with _patched_call_mcp_tool(capturing):
            result = await raise_left_arm_module.raise_left_arm(conn)

        self.assertEqual(result.action, Action.ERROR)
        self.assertEqual(
            capturing.calls,
            [{"tool": raise_left_arm_module.LEFT_ARM_TOOL, "args": "{}"}],
        )

    def test_tool_name_uses_sanitized_mcp_name(self):
        self.assertEqual(
            raise_left_arm_module.LEFT_ARM_TOOL,
            "self_robot_left_arm_raise",
        )
