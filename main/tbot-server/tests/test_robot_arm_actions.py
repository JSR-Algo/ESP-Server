import importlib
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from plugins_func.register import Action

ROOT = Path(__file__).resolve().parents[1]


class _DummyLogger:
    def bind(self, **kwargs):
        return self

    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None


def _load_robot_arm_module():
    spec = importlib.util.find_spec("plugins_func.functions.robot_arm_actions")
    assert spec is not None, "plugins_func.functions.robot_arm_actions module missing"
    return importlib.import_module("plugins_func.functions.robot_arm_actions")


class _FakeMCPClient:
    def __init__(self, ready=True, tools=None):
        robot_arm = _load_robot_arm_module()
        default_tools = tuple(robot_arm.ARM_TOOLS.values()) + tuple(
            robot_arm.ARM_PERCENT_TOOLS.values()
        )
        self._ready = ready
        self.tools = {name: {} for name in (default_tools if tools is None else tools)}

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


class RobotArmActionsTest(unittest.IsolatedAsyncioTestCase):
    async def test_arm_voice_functions_call_device_mcp_tools(self):
        robot_arm = _load_robot_arm_module()
        cases = [
            ("raise_right_arm", "self_robot_right_arm_raise", "Đã nâng tay phải."),
            ("lower_left_arm", "self_robot_left_arm_lower", "Đã hạ tay trái."),
            ("lower_right_arm", "self_robot_right_arm_lower", "Đã hạ tay phải."),
            ("raise_both_arms", "self_robot_both_arms_raise", "Đã nâng hai tay."),
            ("lower_both_arms", "self_robot_both_arms_lower", "Đã hạ hai tay."),
        ]

        for function_name, tool_name, response in cases:
            with self.subTest(function_name=function_name):
                conn = _FakeConn(mcp_client=_FakeMCPClient())
                capturing = _CapturingCallMcp()

                with _patched_call_mcp_tool(capturing):
                    result = await getattr(robot_arm, function_name)(conn)

                self.assertEqual(result.action, Action.RESPONSE)
                self.assertEqual(result.result, "ok")
                self.assertEqual(result.response, response)
                self.assertEqual(capturing.calls, [{"tool": tool_name, "args": "{}"}])

    async def test_arm_percent_functions_call_device_mcp_tools_with_percent(self):
        robot_arm = _load_robot_arm_module()
        cases = [
            ("set_left_arm_percent", "self_robot_left_arm_set_percent", "Đã chỉnh tay trái đến 40%."),
            ("set_right_arm_percent", "self_robot_right_arm_set_percent", "Đã chỉnh tay phải đến 40%."),
            ("set_both_arms_percent", "self_robot_both_arms_set_percent", "Đã chỉnh hai tay đến 40%."),
        ]

        for function_name, tool_name, response in cases:
            with self.subTest(function_name=function_name):
                conn = _FakeConn(mcp_client=_FakeMCPClient())
                capturing = _CapturingCallMcp()

                with _patched_call_mcp_tool(capturing):
                    result = await getattr(robot_arm, function_name)(conn, percent=40)

                self.assertEqual(result.action, Action.RESPONSE)
                self.assertEqual(result.result, "ok")
                self.assertEqual(result.response, response)
                self.assertEqual(capturing.calls[0]["tool"], tool_name)
                self.assertEqual(json.loads(capturing.calls[0]["args"]), {"percent": 40})

    async def test_arm_percent_functions_clamp_percent(self):
        robot_arm = _load_robot_arm_module()
        conn = _FakeConn(mcp_client=_FakeMCPClient())
        capturing = _CapturingCallMcp()

        with _patched_call_mcp_tool(capturing):
            result = await robot_arm.set_left_arm_percent(conn, percent=999)

        self.assertEqual(result.action, Action.RESPONSE)
        self.assertEqual(result.response, "Đã chỉnh tay trái đến 100%.")
        self.assertEqual(json.loads(capturing.calls[0]["args"]), {"percent": 100})

    async def test_returns_error_when_mcp_client_missing(self):
        robot_arm = _load_robot_arm_module()

        result = await robot_arm.raise_right_arm(_FakeConn())

        self.assertEqual(result.action, Action.ERROR)

    async def test_returns_error_when_mcp_not_ready(self):
        robot_arm = _load_robot_arm_module()
        conn = _FakeConn(mcp_client=_FakeMCPClient(ready=False))

        result = await robot_arm.raise_right_arm(conn)

        self.assertEqual(result.action, Action.ERROR)

    async def test_returns_error_when_tool_missing(self):
        robot_arm = _load_robot_arm_module()
        conn = _FakeConn(mcp_client=_FakeMCPClient(tools=()))
        capturing = _CapturingCallMcp()

        with _patched_call_mcp_tool(capturing):
            result = await robot_arm.raise_right_arm(conn)

        self.assertEqual(result.action, Action.ERROR)
        self.assertEqual(capturing.calls, [])

    async def test_returns_error_when_main_reports_uart_failure(self):
        robot_arm = _load_robot_arm_module()
        conn = _FakeConn(mcp_client=_FakeMCPClient())
        capturing = _CapturingCallMcp(
            result={"content": [{"type": "text", "text": "false"}], "isError": False}
        )

        with _patched_call_mcp_tool(capturing):
            result = await robot_arm.raise_right_arm(conn)

        self.assertEqual(result.action, Action.ERROR)
        self.assertEqual(
            capturing.calls,
            [{"tool": robot_arm.ARM_TOOLS["raise_right_arm"], "args": "{}"}],
        )

    def test_tool_names_use_sanitized_mcp_names(self):
        robot_arm = _load_robot_arm_module()

        self.assertEqual(
            robot_arm.ARM_TOOLS,
            {
                "raise_right_arm": "self_robot_right_arm_raise",
                "lower_left_arm": "self_robot_left_arm_lower",
                "lower_right_arm": "self_robot_right_arm_lower",
                "raise_both_arms": "self_robot_both_arms_raise",
                "lower_both_arms": "self_robot_both_arms_lower",
            },
        )
        self.assertEqual(
            robot_arm.ARM_PERCENT_TOOLS,
            {
                "set_left_arm_percent": "self_robot_left_arm_set_percent",
                "set_right_arm_percent": "self_robot_right_arm_set_percent",
                "set_both_arms_percent": "self_robot_both_arms_set_percent",
            },
        )

    def test_function_descriptions_cover_vietnamese_arm_voice_commands(self):
        robot_arm = _load_robot_arm_module()

        descriptions = {
            name: getattr(robot_arm, f"{name}_function_desc")["function"]["description"]
            for name in robot_arm.ARM_TOOLS
        }
        self.assertIn("nâng tay phải", descriptions["raise_right_arm"])
        self.assertIn("giơ tay phải", descriptions["raise_right_arm"])
        self.assertIn("hạ tay trái", descriptions["lower_left_arm"])
        self.assertIn("hạ tay phải", descriptions["lower_right_arm"])
        self.assertIn("nâng hai tay", descriptions["raise_both_arms"])
        self.assertIn("hạ hai tay", descriptions["lower_both_arms"])
        percent_descriptions = {
            name: getattr(robot_arm, f"{name}_function_desc")["function"]["description"]
            for name in robot_arm.ARM_PERCENT_TOOLS
        }
        self.assertIn("nâng tay trái 50%", percent_descriptions["set_left_arm_percent"])
        self.assertIn("nâng tay phải 50%", percent_descriptions["set_right_arm_percent"])
        self.assertIn("nâng hai tay 50%", percent_descriptions["set_both_arms_percent"])

    def test_arm_functions_are_available_to_server_plugin_loader(self):
        config_yaml = (ROOT / "config.yaml").read_text(encoding="utf-8")
        executor = (
            ROOT / "core/providers/tools/server_plugins/plugin_executor.py"
        ).read_text(encoding="utf-8")

        for function_name in _load_robot_arm_module().ARM_TOOLS:
            self.assertIn(f"- {function_name}", config_yaml)
            self.assertIn(f'"{function_name}"', executor)
        for function_name in _load_robot_arm_module().ARM_PERCENT_TOOLS:
            self.assertIn(f"- {function_name}", config_yaml)
            self.assertIn(f'"{function_name}"', executor)
