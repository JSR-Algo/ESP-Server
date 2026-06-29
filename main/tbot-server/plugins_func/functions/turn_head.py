"""Turn the robot head through the device MCP UART bridge."""

import json
from typing import TYPE_CHECKING

from config.logger import setup_logging
from plugins_func.register import Action, ActionResponse, ToolType, register_function

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

TAG = __name__
logger = setup_logging()

HEAD_TURN_LEFT_TOOL = "self_robot_head_turn_left"
HEAD_TURN_RIGHT_TOOL = "self_robot_head_turn_right"
HEAD_CENTER_TOOL = "self_robot_head_center"
HEAD_SET_ANGLE_TOOL = "self_robot_head_set_angle"
HEAD_SET_PERCENT_TOOL = "self_robot_head_set_percent"

turn_head_left_function_desc = {
    "type": "function",
    "function": {
        "name": "turn_head_left",
        "description": (
            "Turn the robot's head left. Use this function when the user asks in "
            "Vietnamese or English to turn or look left, for example: 'quay đầu trái', "
            "'nhìn sang trái', 'quay mặt sang trái', 'turn head left'. Do not only "
            "answer with text for these commands; call this function."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
}

turn_head_right_function_desc = {
    "type": "function",
    "function": {
        "name": "turn_head_right",
        "description": (
            "Turn the robot's head right. Use this function when the user asks in "
            "Vietnamese or English to turn or look right, for example: 'quay đầu phải', "
            "'nhìn sang phải', 'quay mặt sang phải', 'turn head right'. Do not only "
            "answer with text for these commands; call this function."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
}

center_head_function_desc = {
    "type": "function",
    "function": {
        "name": "center_head",
        "description": (
            "Center the robot's head. Use this function when the user asks in Vietnamese "
            "or English to face forward or return the head to center, for example: "
            "'đưa đầu về giữa', 'quay đầu về giữa', 'nhìn thẳng', 'center head'. Do "
            "not only answer with text for these commands; call this function."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
}

set_head_angle_function_desc = {
    "type": "function",
    "function": {
        "name": "set_head_angle",
        "description": (
            "Set the robot's head to a specific servo angle from 0 to 180 degrees. "
            "Use this function when the user asks to chỉnh góc quay đầu, quay đầu "
            "120 độ, xoay đầu 45 độ, xoay đầu sang trái 30 độ, or xoay đầu sang "
            "phải 150 độ. 90 degrees is center, lower values turn left, higher "
            "values turn right. Do not only answer with text for these commands; "
            "call this function."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "angle": {
                    "type": "integer",
                    "description": "Head servo angle in degrees. 0 is max left, 90 is center, 180 is max right.",
                    "minimum": 0,
                    "maximum": 180,
                    "default": 90,
                }
            },
            "required": ["angle"],
        },
    },
}

set_head_percent_function_desc = {
    "type": "function",
    "function": {
        "name": "set_head_percent",
        "description": (
            "Set the robot's head turn by percentage. Use this when the user asks "
            "to quay đầu 50%, xoay đầu sang trái 50%, xoay đầu sang phải 75%, "
            "quay mặt 30%, or chỉnh đầu theo phần trăm. If direction is absolute, "
            "0% is fully left, 50% is center, and 100% is fully right. If direction "
            "is left or right, percent means how far to move from center toward that "
            "side. Do not only answer with text for these commands; call this function."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "percent": {
                    "type": "integer",
                    "description": "Requested head movement percentage from 0 to 100.",
                    "minimum": 0,
                    "maximum": 100,
                    "default": 50,
                },
                "direction": {
                    "type": "string",
                    "description": "Use 'left', 'right', 'center', or 'absolute'.",
                    "default": "absolute",
                },
            },
            "required": ["percent"],
        },
    },
}

turn_head_left_then_right_max_function_desc = {
    "type": "function",
    "function": {
        "name": "turn_head_left_then_right_max",
        "description": (
            "Turn the robot's head fully left and then fully right. Use this when "
            "the user asks in Vietnamese for 'quay đầu sang trái rồi sang phải tối "
            "đa', 'xoay đầu trái rồi phải hết cỡ', or 'trái rồi sang phải tối đa'. "
            "Do not only answer with text for these commands; call this function."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
}


def _available_tools(mcp_client) -> list[str]:
    tools = getattr(mcp_client, "tools", {})
    if isinstance(tools, dict):
        return sorted(tools.keys())
    return []


def _tool_result_is_true(result) -> bool:
    if result is True:
        return True
    if isinstance(result, str):
        return result.strip().lower() == "true"
    if isinstance(result, dict):
        content = result.get("content")
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    if str(item.get("text", "")).strip().lower() == "true":
                        return True
        if "result" in result:
            return _tool_result_is_true(result["result"])
    return False

def _motion_response(result, response: str) -> ActionResponse:
    if _tool_result_is_true(result):
        return ActionResponse(Action.RESPONSE, result="ok", response=response)

    return ActionResponse(
        Action.RESPONSE,
        result="sent_unconfirmed",
        response=response,
    )


def _clamp_head_angle(angle) -> int:
    try:
        target = int(angle)
    except (TypeError, ValueError):
        target = 90
    return max(0, min(180, target))


def _clamp_percent(percent) -> int:
    try:
        target = int(percent)
    except (TypeError, ValueError):
        target = 50
    return max(0, min(100, target))


def _head_axis_percent(percent, direction: str = "absolute") -> int:
    target = _clamp_percent(percent)
    normalized = str(direction or "absolute").strip().lower()
    if normalized in {"left", "trai", "trái", "sang trái"}:
        return 50 - ((target * 50 + 50) // 100)
    if normalized in {"right", "phai", "phải", "sang phải"}:
        return 50 + ((target * 50 + 50) // 100)
    if normalized in {"center", "centre", "middle", "giua", "giữa"}:
        return 50
    return target


def _angle_args(angle: int) -> str:
    return json.dumps({"angle": angle}, separators=(",", ":"))


def _percent_args(percent: int) -> str:
    return json.dumps({"percent": percent}, separators=(",", ":"))


async def _call_head_tool(
    conn: "ConnectionHandler",
    tool_name: str,
    success_response: str,
    args_str: str = "{}",
):
    logger.bind(tag=TAG).info(f"head tool invoked: {tool_name}")

    mcp_client = getattr(conn, "mcp_client", None)
    if mcp_client is None:
        logger.bind(tag=TAG).warning("head tool aborted: conn.mcp_client is None")
        return ActionResponse(
            Action.ERROR,
            response="Thiết bị chưa sẵn sàng để điều khiển đầu.",
        )

    if not await mcp_client.is_ready():
        logger.bind(tag=TAG).warning("head tool aborted: mcp_client not ready")
        return ActionResponse(
            Action.ERROR,
            response="Danh sách công cụ thiết bị chưa sẵn sàng.",
        )

    if not mcp_client.has_tool(tool_name):
        logger.bind(tag=TAG).warning(
            f"head tool aborted: tool {tool_name!r} not advertised. "
            f"Device tools: {_available_tools(mcp_client)}"
        )
        return ActionResponse(
            Action.ERROR,
            response="Thiết bị chưa có công cụ điều khiển đầu.",
        )

    from core.providers.tools.device_mcp.mcp_handler import call_mcp_tool

    result = await call_mcp_tool(conn, mcp_client, tool_name, args_str)
    if not _tool_result_is_true(result):
        logger.bind(tag=TAG).warning(f"head tool unconfirmed result={result!r}")

    return _motion_response(result, success_response)


@register_function("turn_head_left", turn_head_left_function_desc, ToolType.SYSTEM_CTL)
async def turn_head_left(conn: "ConnectionHandler"):
    return await _call_head_tool(conn, HEAD_TURN_LEFT_TOOL, "Đã quay đầu sang trái.")


@register_function("turn_head_right", turn_head_right_function_desc, ToolType.SYSTEM_CTL)
async def turn_head_right(conn: "ConnectionHandler"):
    return await _call_head_tool(conn, HEAD_TURN_RIGHT_TOOL, "Đã quay đầu sang phải.")


@register_function("center_head", center_head_function_desc, ToolType.SYSTEM_CTL)
async def center_head(conn: "ConnectionHandler"):
    return await _call_head_tool(conn, HEAD_CENTER_TOOL, "Đã đưa đầu về giữa.")


@register_function("set_head_angle", set_head_angle_function_desc, ToolType.SYSTEM_CTL)
async def set_head_angle(conn: "ConnectionHandler", angle: int = 90):
    target = _clamp_head_angle(angle)
    return await _call_head_tool(
        conn,
        HEAD_SET_ANGLE_TOOL,
        f"Đã chỉnh đầu đến góc {target} độ.",
        _angle_args(target),
    )


@register_function("set_head_percent", set_head_percent_function_desc, ToolType.SYSTEM_CTL)
async def set_head_percent(
    conn: "ConnectionHandler",
    percent: int = 50,
    direction: str = "absolute",
):
    target = _head_axis_percent(percent, direction)
    return await _call_head_tool(
        conn,
        HEAD_SET_PERCENT_TOOL,
        f"Đã chỉnh đầu đến {target}%.",
        _percent_args(target),
    )


@register_function(
    "turn_head_left_then_right_max",
    turn_head_left_then_right_max_function_desc,
    ToolType.SYSTEM_CTL,
)
async def turn_head_left_then_right_max(conn: "ConnectionHandler"):
    left_result = await _call_head_tool(
        conn,
        HEAD_SET_PERCENT_TOOL,
        "Đã quay đầu sang trái tối đa.",
        _percent_args(0),
    )
    if left_result.action != Action.RESPONSE:
        return left_result

    right_result = await _call_head_tool(
        conn,
        HEAD_SET_PERCENT_TOOL,
        "Đã quay đầu sang phải tối đa.",
        _percent_args(100),
    )
    if right_result.action != Action.RESPONSE:
        return right_result

    result_status = "sent_unconfirmed" if (
        left_result.result == "sent_unconfirmed" or
        right_result.result == "sent_unconfirmed"
    ) else "ok"

    return ActionResponse(
        Action.RESPONSE,
        result=result_status,
        response="Đã quay đầu sang trái rồi sang phải tối đa.",
    )
