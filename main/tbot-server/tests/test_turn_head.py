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


def _load_turn_head_module():
    spec = importlib.util.find_spec("plugins_func.functions.turn_head")
    assert spec is not None, "plugins_func.functions.turn_head module missing"
    return importlib.import_module("plugins_func.functions.turn_head")


class _FakeMCPClient:
    def __init__(self, ready=True, tools=None):
        turn_head = _load_turn_head_module()
        default_tools = (
            turn_head.HEAD_TURN_LEFT_TOOL,
            turn_head.HEAD_TURN_RIGHT_TOOL,
            turn_head.HEAD_CENTER_TOOL,
            turn_head.HEAD_SET_ANGLE_TOOL,
            turn_head.HEAD_SET_PERCENT_TOOL,
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


class TurnHeadTest(unittest.IsolatedAsyncioTestCase):
    async def test_turn_head_left_calls_device_mcp_tool(self):
        turn_head = _load_turn_head_module()
        conn = _FakeConn(mcp_client=_FakeMCPClient())
        capturing = _CapturingCallMcp()

        with _patched_call_mcp_tool(capturing):
            result = await turn_head.turn_head_left(conn)

        self.assertEqual(result.action, Action.RESPONSE)
        self.assertEqual(result.result, "ok")
        self.assertEqual(result.response, "Đã quay đầu sang trái.")
        self.assertEqual(
            capturing.calls,
            [{"tool": turn_head.HEAD_TURN_LEFT_TOOL, "args": "{}"}],
        )

    async def test_turn_head_right_calls_device_mcp_tool(self):
        turn_head = _load_turn_head_module()
        conn = _FakeConn(mcp_client=_FakeMCPClient())
        capturing = _CapturingCallMcp()

        with _patched_call_mcp_tool(capturing):
            result = await turn_head.turn_head_right(conn)

        self.assertEqual(result.action, Action.RESPONSE)
        self.assertEqual(result.result, "ok")
        self.assertEqual(result.response, "Đã quay đầu sang phải.")
        self.assertEqual(
            capturing.calls,
            [{"tool": turn_head.HEAD_TURN_RIGHT_TOOL, "args": "{}"}],
        )

    async def test_center_head_calls_device_mcp_tool(self):
        turn_head = _load_turn_head_module()
        conn = _FakeConn(mcp_client=_FakeMCPClient())
        capturing = _CapturingCallMcp()

        with _patched_call_mcp_tool(capturing):
            result = await turn_head.center_head(conn)

        self.assertEqual(result.action, Action.RESPONSE)
        self.assertEqual(result.result, "ok")
        self.assertEqual(result.response, "Đã đưa đầu về giữa.")
        self.assertEqual(
            capturing.calls,
            [{"tool": turn_head.HEAD_CENTER_TOOL, "args": "{}"}],
        )

    async def test_set_head_angle_calls_device_mcp_tool_with_angle(self):
        turn_head = _load_turn_head_module()
        conn = _FakeConn(mcp_client=_FakeMCPClient())
        capturing = _CapturingCallMcp()

        with _patched_call_mcp_tool(capturing):
            result = await turn_head.set_head_angle(conn, angle=120)

        self.assertEqual(result.action, Action.RESPONSE)
        self.assertEqual(result.result, "ok")
        self.assertEqual(result.response, "Đã chỉnh đầu đến góc 120 độ.")
        self.assertEqual(capturing.calls[0]["tool"], turn_head.HEAD_SET_ANGLE_TOOL)
        self.assertEqual(json.loads(capturing.calls[0]["args"]), {"angle": 120})

    async def test_set_head_angle_clamps_to_servo_range(self):
        turn_head = _load_turn_head_module()
        conn = _FakeConn(mcp_client=_FakeMCPClient())
        capturing = _CapturingCallMcp()

        with _patched_call_mcp_tool(capturing):
            result = await turn_head.set_head_angle(conn, angle=999)

        self.assertEqual(result.action, Action.RESPONSE)
        self.assertEqual(result.response, "Đã chỉnh đầu đến góc 180 độ.")
        self.assertEqual(json.loads(capturing.calls[0]["args"]), {"angle": 180})

    async def test_turn_head_left_then_right_max_calls_two_angles(self):
        turn_head = _load_turn_head_module()
        conn = _FakeConn(mcp_client=_FakeMCPClient())
        capturing = _CapturingCallMcp()

        with _patched_call_mcp_tool(capturing):
            result = await turn_head.turn_head_left_then_right_max(conn)

        self.assertEqual(result.action, Action.RESPONSE)
        self.assertEqual(result.result, "ok")
        self.assertEqual(result.response, "Đã quay đầu sang trái rồi sang phải tối đa.")
        self.assertEqual(
            [(call["tool"], json.loads(call["args"])) for call in capturing.calls],
            [
                (turn_head.HEAD_SET_PERCENT_TOOL, {"percent": 0}),
                (turn_head.HEAD_SET_PERCENT_TOOL, {"percent": 100}),
            ],
        )

    async def test_set_head_percent_calls_device_mcp_tool_with_absolute_percent(self):
        turn_head = _load_turn_head_module()
        conn = _FakeConn(mcp_client=_FakeMCPClient())
        capturing = _CapturingCallMcp()

        with _patched_call_mcp_tool(capturing):
            result = await turn_head.set_head_percent(conn, percent=75)

        self.assertEqual(result.action, Action.RESPONSE)
        self.assertEqual(result.response, "Đã chỉnh đầu đến 75%.")
        self.assertEqual(capturing.calls[0]["tool"], turn_head.HEAD_SET_PERCENT_TOOL)
        self.assertEqual(json.loads(capturing.calls[0]["args"]), {"percent": 75})

    async def test_set_head_percent_maps_directional_percent_from_center(self):
        turn_head = _load_turn_head_module()
        conn = _FakeConn(mcp_client=_FakeMCPClient())
        capturing = _CapturingCallMcp()

        with _patched_call_mcp_tool(capturing):
            left_result = await turn_head.set_head_percent(conn, percent=50, direction="left")
            right_result = await turn_head.set_head_percent(conn, percent=40, direction="right")

        self.assertEqual(left_result.action, Action.RESPONSE)
        self.assertEqual(right_result.action, Action.RESPONSE)
        self.assertEqual(
            [json.loads(call["args"]) for call in capturing.calls],
            [{"percent": 25}, {"percent": 70}],
        )

    async def test_returns_error_when_mcp_client_missing(self):
        turn_head = _load_turn_head_module()

        result = await turn_head.turn_head_left(_FakeConn())

        self.assertEqual(result.action, Action.ERROR)

    async def test_returns_error_when_mcp_not_ready(self):
        turn_head = _load_turn_head_module()
        conn = _FakeConn(mcp_client=_FakeMCPClient(ready=False))

        result = await turn_head.turn_head_left(conn)

        self.assertEqual(result.action, Action.ERROR)

    async def test_returns_error_when_tool_missing(self):
        turn_head = _load_turn_head_module()
        conn = _FakeConn(mcp_client=_FakeMCPClient(tools=()))
        capturing = _CapturingCallMcp()

        with _patched_call_mcp_tool(capturing):
            result = await turn_head.turn_head_left(conn)

        self.assertEqual(result.action, Action.ERROR)
        self.assertEqual(capturing.calls, [])

    async def test_returns_error_when_main_reports_uart_failure(self):
        turn_head = _load_turn_head_module()
        conn = _FakeConn(mcp_client=_FakeMCPClient())
        capturing = _CapturingCallMcp(
            result={"content": [{"type": "text", "text": "false"}], "isError": False}
        )

        with _patched_call_mcp_tool(capturing):
            result = await turn_head.turn_head_left(conn)

        self.assertEqual(result.action, Action.ERROR)
        self.assertEqual(
            capturing.calls,
            [{"tool": turn_head.HEAD_TURN_LEFT_TOOL, "args": "{}"}],
        )

    def test_tool_names_use_sanitized_mcp_names(self):
        turn_head = _load_turn_head_module()

        self.assertEqual(turn_head.HEAD_TURN_LEFT_TOOL, "self_robot_head_turn_left")
        self.assertEqual(turn_head.HEAD_TURN_RIGHT_TOOL, "self_robot_head_turn_right")
        self.assertEqual(turn_head.HEAD_CENTER_TOOL, "self_robot_head_center")
        self.assertEqual(turn_head.HEAD_SET_ANGLE_TOOL, "self_robot_head_set_angle")
        self.assertEqual(turn_head.HEAD_SET_PERCENT_TOOL, "self_robot_head_set_percent")

    def test_function_descriptions_cover_vietnamese_head_commands(self):
        turn_head = _load_turn_head_module()

        left_desc = turn_head.turn_head_left_function_desc["function"]["description"]
        right_desc = turn_head.turn_head_right_function_desc["function"]["description"]
        center_desc = turn_head.center_head_function_desc["function"]["description"]
        set_angle_desc = turn_head.set_head_angle_function_desc["function"]["description"]
        set_percent_desc = turn_head.set_head_percent_function_desc["function"]["description"]
        max_desc = turn_head.turn_head_left_then_right_max_function_desc["function"]["description"]
        self.assertIn("quay đầu trái", left_desc)
        self.assertIn("nhìn sang trái", left_desc)
        self.assertIn("quay đầu phải", right_desc)
        self.assertIn("nhìn sang phải", right_desc)
        self.assertIn("đưa đầu về giữa", center_desc)
        self.assertIn("quay đầu 120 độ", set_angle_desc)
        self.assertIn("chỉnh góc quay đầu", set_angle_desc)
        self.assertIn("quay đầu 50%", set_percent_desc)
        self.assertIn("xoay đầu sang trái 50%", set_percent_desc)
        self.assertIn("trái rồi sang phải tối đa", max_desc)

    def test_head_functions_are_available_to_server_plugin_loader(self):
        config_yaml = (ROOT / "config.yaml").read_text(encoding="utf-8")
        executor = (
            ROOT / "core/providers/tools/server_plugins/plugin_executor.py"
        ).read_text(encoding="utf-8")

        for function_name in [
            "turn_head_left",
            "turn_head_right",
            "center_head",
            "set_head_angle",
            "set_head_percent",
            "turn_head_left_then_right_max",
        ]:
            self.assertIn(f"- {function_name}", config_yaml)
            self.assertIn(f'"{function_name}"', executor)
