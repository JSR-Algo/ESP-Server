import json
import unittest
from unittest.mock import patch

from plugins_func.functions import robot_arm_actions, turn_head
from plugins_func.register import Action
from core.lesson.motion_presets import dispatch_motion_preset, motion_preset_tools


class _FakeMCPClient:
    def __init__(self, *, ready=True, tools=()):
        self._ready = ready
        self.tools = {name: {} for name in tools}

    async def is_ready(self):
        return self._ready

    def has_tool(self, name):
        return name in self.tools


class _FakeConn:
    def __init__(self, mcp_client=None):
        self.mcp_client = mcp_client


class LessonMotionPresetToolTest(unittest.IsolatedAsyncioTestCase):
    async def test_named_preset_uses_only_server_mcp_tools_in_authored_order(self):
        tools = motion_preset_tools("teach")
        self.assertEqual(
            tools,
            ("self_robot_right_arm_raise", "self_robot_head_center"),
        )
        conn = _FakeConn(_FakeMCPClient(tools=tools))
        recorder = _CallRecorder()
        with patch("core.lesson.motion_presets.call_mcp_tool", new=recorder):
            self.assertTrue(await dispatch_motion_preset(conn, "teach"))
        self.assertEqual(
            [call["tool"] for call in recorder.calls],
            list(tools),
        )

    def test_unknown_preset_exposes_no_firmware_or_raw_servo_command(self):
        self.assertEqual(motion_preset_tools("body.motion.present"), ())
        self.assertEqual(motion_preset_tools("rawServo"), ())


class _CallRecorder:
    def __init__(self, result=None):
        self.calls = []
        self.timeouts = []
        self.result = result if result is not None else {"content": [{"type": "text", "text": "true"}]}

    async def __call__(self, conn, mcp_client, tool_name, args_str, timeout=30):
        self.calls.append({"tool": tool_name, "args": args_str})
        self.timeouts.append(timeout)
        return self.result

class _TimeoutRecorder:
    def __init__(self):
        self.calls = []
        self.timeouts = []

    async def __call__(self, conn, mcp_client, tool_name, args_str, timeout=30):
        self.calls.append({"tool": tool_name, "args": args_str})
        self.timeouts.append(timeout)
        raise TimeoutError("motion ack timed out")


def _patch_mcp_call(recorder):
    return patch("core.providers.tools.device_mcp.mcp_handler.call_mcp_tool", new=recorder)


class RobotArmActionToolTest(unittest.IsolatedAsyncioTestCase):
    def _conn(self, *, ready=True, tools=None):
        if tools is None:
            tools = list(robot_arm_actions.ARM_TOOLS.values()) + list(robot_arm_actions.ARM_PERCENT_TOOLS.values())
        return _FakeConn(_FakeMCPClient(ready=ready, tools=tools))

    async def test_discrete_arm_actions_dispatch_to_sanitized_mcp_tools(self):
        for function_name, tool_name in robot_arm_actions.ARM_TOOLS.items():
            recorder = _CallRecorder()
            with _patch_mcp_call(recorder):
                result = await getattr(robot_arm_actions, function_name)(self._conn())

            self.assertEqual(result.action, Action.RESPONSE, function_name)
            self.assertEqual(result.result, "ok", function_name)
            self.assertEqual(recorder.calls, [{"tool": tool_name, "args": "{}"}])

    async def test_arm_percent_actions_clamp_and_send_compact_json(self):
        cases = [
            (robot_arm_actions.set_left_arm_percent, -10, "self_robot_left_arm_set_percent", 0),
            (robot_arm_actions.set_right_arm_percent, "bad", "self_robot_right_arm_set_percent", 100),
            (robot_arm_actions.set_both_arms_percent, 150, "self_robot_both_arms_set_percent", 100),
        ]

        for func, percent, tool_name, expected in cases:
            recorder = _CallRecorder()
            with _patch_mcp_call(recorder):
                result = await func(self._conn(), percent=percent)

            self.assertEqual(result.action, Action.RESPONSE)
            self.assertEqual(result.response, f"Đã chỉnh {self._arm_label(tool_name)} đến {expected}%.")
            self.assertEqual(recorder.calls, [{"tool": tool_name, "args": json.dumps({"percent": expected}, separators=(",", ":"))}])

    async def test_arm_actions_return_error_without_dispatch_when_mcp_unavailable(self):
        for conn in [
            _FakeConn(None),
            self._conn(ready=False),
            self._conn(tools=[]),
        ]:
            recorder = _CallRecorder()
            with _patch_mcp_call(recorder):
                result = await robot_arm_actions.raise_right_arm(conn)

            self.assertEqual(result.action, Action.ERROR)
            self.assertEqual(recorder.calls, [])

    async def test_arm_actions_return_success_when_motion_dispatch_is_unconfirmed(self):
        recorder = _CallRecorder(result={"result": {"content": [{"type": "text", "text": "false"}]}})
        with _patch_mcp_call(recorder):
            result = await robot_arm_actions.lower_left_arm(self._conn())

        self.assertEqual(result.action, Action.RESPONSE)
        self.assertEqual(result.result, "sent_unconfirmed")
        self.assertEqual(result.response, "Đã hạ tay trái.")
        self.assertEqual(recorder.calls, [{"tool": "self_robot_left_arm_lower", "args": "{}"}])

    async def test_arm_motion_uses_fast_ack_timeout(self):
        recorder = _CallRecorder()
        with _patch_mcp_call(recorder):
            await robot_arm_actions.raise_right_arm(self._conn())

        self.assertLess(recorder.timeouts[0], 1)

    async def test_arm_motion_timeout_returns_sent_unconfirmed(self):
        recorder = _TimeoutRecorder()
        with _patch_mcp_call(recorder):
            result = await robot_arm_actions.raise_right_arm(self._conn())

        self.assertEqual(result.action, Action.RESPONSE)
        self.assertEqual(result.result, "sent_unconfirmed")
        self.assertEqual(result.response, "Đã nâng tay phải.")
        self.assertLess(recorder.timeouts[0], 1)

    def test_arm_result_parser_accepts_nested_truthy_mcp_shapes(self):
        self.assertTrue(robot_arm_actions._tool_result_is_true(True))
        self.assertTrue(robot_arm_actions._tool_result_is_true(" TRUE "))
        self.assertTrue(robot_arm_actions._tool_result_is_true({"result": {"content": [{"type": "text", "text": "true"}]}}))
        self.assertFalse(robot_arm_actions._tool_result_is_true({"content": [{"type": "text", "text": "false"}]}))

    def test_arm_available_tools_ignores_non_dict_tool_advertisements(self):
        self.assertEqual(robot_arm_actions._available_tools(type("Client", (), {"tools": ["not", "a", "dict"]})()), [])

    @staticmethod
    def _arm_label(tool_name):
        if "both" in tool_name:
            return "hai tay"
        if "right" in tool_name:
            return "tay phải"
        return "tay trái"


class TurnHeadToolTest(unittest.IsolatedAsyncioTestCase):
    def _conn(self, *, ready=True, tools=None):
        if tools is None:
            tools = [
                turn_head.HEAD_TURN_LEFT_TOOL,
                turn_head.HEAD_TURN_RIGHT_TOOL,
                turn_head.HEAD_CENTER_TOOL,
                turn_head.HEAD_SET_ANGLE_TOOL,
                turn_head.HEAD_SET_PERCENT_TOOL,
            ]
        return _FakeConn(_FakeMCPClient(ready=ready, tools=tools))

    async def test_basic_head_actions_dispatch_to_expected_mcp_tools(self):
        cases = [
            (turn_head.turn_head_left, turn_head.HEAD_TURN_LEFT_TOOL),
            (turn_head.turn_head_right, turn_head.HEAD_TURN_RIGHT_TOOL),
            (turn_head.center_head, turn_head.HEAD_CENTER_TOOL),
        ]

        for func, tool_name in cases:
            recorder = _CallRecorder()
            with _patch_mcp_call(recorder):
                result = await func(self._conn())

            self.assertEqual(result.action, Action.RESPONSE)
            self.assertEqual(recorder.calls, [{"tool": tool_name, "args": "{}"}])

    async def test_basic_head_action_returns_success_when_motion_dispatch_is_unconfirmed(self):
        recorder = _CallRecorder(result={"content": [{"type": "text", "text": "false"}]})
        with _patch_mcp_call(recorder):
            result = await turn_head.turn_head_left(self._conn())

        self.assertEqual(result.action, Action.RESPONSE)
        self.assertEqual(result.result, "sent_unconfirmed")
        self.assertEqual(result.response, "Đã quay đầu sang trái.")
        self.assertEqual(recorder.calls, [{"tool": turn_head.HEAD_TURN_LEFT_TOOL, "args": "{}"}])

    async def test_head_motion_uses_fast_ack_timeout(self):
        recorder = _CallRecorder()
        with _patch_mcp_call(recorder):
            await turn_head.turn_head_left(self._conn())

        self.assertLess(recorder.timeouts[0], 1)

    async def test_head_motion_timeout_returns_sent_unconfirmed(self):
        recorder = _TimeoutRecorder()
        with _patch_mcp_call(recorder):
            result = await turn_head.turn_head_left(self._conn())

        self.assertEqual(result.action, Action.RESPONSE)
        self.assertEqual(result.result, "sent_unconfirmed")
        self.assertEqual(result.response, "Đã quay đầu sang trái.")
        self.assertLess(recorder.timeouts[0], 1)

    async def test_head_angle_clamps_to_servo_range(self):
        for angle, expected in [(-15, 0), (999, 180), ("bad", 90)]:
            recorder = _CallRecorder()
            with _patch_mcp_call(recorder):
                result = await turn_head.set_head_angle(self._conn(), angle=angle)

            self.assertEqual(result.action, Action.RESPONSE)
            self.assertEqual(result.response, f"Đã chỉnh đầu đến góc {expected} độ.")
            self.assertEqual(recorder.calls, [{"tool": turn_head.HEAD_SET_ANGLE_TOOL, "args": json.dumps({"angle": expected}, separators=(",", ":"))}])

    async def test_head_percent_maps_absolute_and_directional_requests(self):
        cases = [
            (30, "absolute", 30),
            (100, "left", 0),
            (75, "right", 88),
            (50, "trái", 25),
            (10, "giữa", 50),
            ("bad", "absolute", 50),
        ]

        for percent, direction, expected in cases:
            recorder = _CallRecorder()
            with _patch_mcp_call(recorder):
                result = await turn_head.set_head_percent(self._conn(), percent=percent, direction=direction)

            self.assertEqual(result.action, Action.RESPONSE)
            self.assertEqual(result.response, f"Đã chỉnh đầu đến {expected}%.")
            self.assertEqual(recorder.calls, [{"tool": turn_head.HEAD_SET_PERCENT_TOOL, "args": json.dumps({"percent": expected}, separators=(",", ":"))}])

    async def test_left_then_right_max_sends_two_percent_commands_in_order(self):
        recorder = _CallRecorder()
        with _patch_mcp_call(recorder):
            result = await turn_head.turn_head_left_then_right_max(self._conn())

        self.assertEqual(result.action, Action.RESPONSE)
        self.assertEqual(
            recorder.calls,
            [
                {"tool": turn_head.HEAD_SET_PERCENT_TOOL, "args": '{"percent":0}'},
                {"tool": turn_head.HEAD_SET_PERCENT_TOOL, "args": '{"percent":100}'},
            ],
        )

    async def test_left_then_right_max_continues_when_left_turn_is_unconfirmed(self):
        recorder = _CallRecorder(result={"content": [{"type": "text", "text": "false"}]})
        with _patch_mcp_call(recorder):
            result = await turn_head.turn_head_left_then_right_max(self._conn())

        self.assertEqual(result.action, Action.RESPONSE)
        self.assertEqual(result.result, "sent_unconfirmed")
        self.assertEqual(
            recorder.calls,
            [
                {"tool": turn_head.HEAD_SET_PERCENT_TOOL, "args": '{"percent":0}'},
                {"tool": turn_head.HEAD_SET_PERCENT_TOOL, "args": '{"percent":100}'},
            ],
        )

    async def test_left_then_right_max_returns_success_when_second_turn_is_unconfirmed(self):
        class _SecondFailureRecorder:
            def __init__(self):
                self.calls = []

            async def __call__(self, conn, mcp_client, tool_name, args_str, timeout=30):
                self.calls.append({"tool": tool_name, "args": args_str})
                return {"content": [{"type": "text", "text": "true" if len(self.calls) == 1 else "false"}]}

        recorder = _SecondFailureRecorder()
        with _patch_mcp_call(recorder):
            result = await turn_head.turn_head_left_then_right_max(self._conn())

        self.assertEqual(result.action, Action.RESPONSE)
        self.assertEqual(result.result, "sent_unconfirmed")
        self.assertEqual(
            recorder.calls,
            [
                {"tool": turn_head.HEAD_SET_PERCENT_TOOL, "args": '{"percent":0}'},
                {"tool": turn_head.HEAD_SET_PERCENT_TOOL, "args": '{"percent":100}'},
            ],
        )

    async def test_head_actions_return_error_without_dispatch_when_mcp_unavailable(self):
        for conn in [
            _FakeConn(None),
            self._conn(ready=False),
            self._conn(tools=[]),
        ]:
            recorder = _CallRecorder()
            with _patch_mcp_call(recorder):
                result = await turn_head.turn_head_left(conn)

            self.assertEqual(result.action, Action.ERROR)
            self.assertEqual(recorder.calls, [])

    def test_head_helpers_cover_non_dict_tools_and_result_shapes(self):
        self.assertEqual(turn_head._available_tools(type("Client", (), {"tools": ["not", "a", "dict"]})()), [])
        self.assertTrue(turn_head._tool_result_is_true(True))
        self.assertTrue(turn_head._tool_result_is_true(" true "))
        self.assertTrue(turn_head._tool_result_is_true({"result": {"content": [{"type": "text", "text": "true"}]}}))
        self.assertFalse(turn_head._tool_result_is_true({"content": [{"type": "text", "text": "false"}]}))


if __name__ == "__main__":
    unittest.main()
