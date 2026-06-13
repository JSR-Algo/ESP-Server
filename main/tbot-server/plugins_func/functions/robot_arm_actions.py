"""Control robot arm movements through the device MCP UART bridge."""

import json
from typing import TYPE_CHECKING

from config.logger import setup_logging
from plugins_func.register import Action, ActionResponse, ToolType, register_function

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

TAG = __name__
logger = setup_logging()

ARM_TOOLS = {
    "raise_right_arm": "self_robot_right_arm_raise",
    "lower_left_arm": "self_robot_left_arm_lower",
    "lower_right_arm": "self_robot_right_arm_lower",
    "raise_both_arms": "self_robot_both_arms_raise",
    "lower_both_arms": "self_robot_both_arms_lower",
}

ARM_PERCENT_TOOLS = {
    "set_left_arm_percent": "self_robot_left_arm_set_percent",
    "set_right_arm_percent": "self_robot_right_arm_set_percent",
    "set_both_arms_percent": "self_robot_both_arms_set_percent",
}

raise_right_arm_function_desc = {
    "type": "function",
    "function": {
        "name": "raise_right_arm",
        "description": (
            "Raise the robot's right arm / right hand. Use this function when the user "
            "asks in Vietnamese or English to raise the right arm, for example: "
            "'nâng tay phải', 'giơ tay phải', 'dơ tay phải', 'đưa tay phải lên', "
            "'raise right arm'. Do not only answer with text for these commands; call "
            "this function."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
}

lower_left_arm_function_desc = {
    "type": "function",
    "function": {
        "name": "lower_left_arm",
        "description": (
            "Lower the robot's left arm / left hand. Use this function when the user "
            "asks in Vietnamese or English to lower the left arm, for example: "
            "'hạ tay trái', 'bỏ tay trái xuống', 'đưa tay trái xuống', "
            "'lower left arm'. Do not only answer with text for these commands; call "
            "this function."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
}

lower_right_arm_function_desc = {
    "type": "function",
    "function": {
        "name": "lower_right_arm",
        "description": (
            "Lower the robot's right arm / right hand. Use this function when the user "
            "asks in Vietnamese or English to lower the right arm, for example: "
            "'hạ tay phải', 'bỏ tay phải xuống', 'đưa tay phải xuống', "
            "'lower right arm'. Do not only answer with text for these commands; call "
            "this function."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
}

raise_both_arms_function_desc = {
    "type": "function",
    "function": {
        "name": "raise_both_arms",
        "description": (
            "Raise both robot arms / hands. Use this function when the user asks in "
            "Vietnamese or English to raise both arms, for example: 'nâng hai tay', "
            "'giơ cả hai tay', 'dơ cả hai tay', 'đưa hai tay lên', "
            "'raise both arms'. Do not only answer with text for these commands; call "
            "this function."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
}

lower_both_arms_function_desc = {
    "type": "function",
    "function": {
        "name": "lower_both_arms",
        "description": (
            "Lower both robot arms / hands. Use this function when the user asks in "
            "Vietnamese or English to lower both arms, for example: 'hạ hai tay', "
            "'hạ cả hai tay', 'bỏ hai tay xuống', 'đưa hai tay xuống', "
            "'lower both arms'. Do not only answer with text for these commands; call "
            "this function."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
}

set_left_arm_percent_function_desc = {
    "type": "function",
    "function": {
        "name": "set_left_arm_percent",
        "description": (
            "Set the robot's left arm / left hand to a percentage. Use this when "
            "the user asks in Vietnamese or English for commands like 'nâng tay "
            "trái 50%', 'hạ tay trái 30%', 'đưa tay trái lên 75%', or 'set left "
            "arm to 40 percent'. 0% is fully lowered and 100% is fully raised. Do "
            "not only answer with text for these commands; call this function."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "percent": {
                    "type": "integer",
                    "description": "Left arm position percent. 0 is lowered, 100 is raised.",
                    "minimum": 0,
                    "maximum": 100,
                    "default": 100,
                }
            },
            "required": ["percent"],
        },
    },
}

set_right_arm_percent_function_desc = {
    "type": "function",
    "function": {
        "name": "set_right_arm_percent",
        "description": (
            "Set the robot's right arm / right hand to a percentage. Use this when "
            "the user asks in Vietnamese or English for commands like 'nâng tay "
            "phải 50%', 'hạ tay phải 30%', 'đưa tay phải lên 75%', or 'set right "
            "arm to 40 percent'. 0% is fully lowered and 100% is fully raised. Do "
            "not only answer with text for these commands; call this function."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "percent": {
                    "type": "integer",
                    "description": "Right arm position percent. 0 is lowered, 100 is raised.",
                    "minimum": 0,
                    "maximum": 100,
                    "default": 100,
                }
            },
            "required": ["percent"],
        },
    },
}

set_both_arms_percent_function_desc = {
    "type": "function",
    "function": {
        "name": "set_both_arms_percent",
        "description": (
            "Set both robot arms / hands to a percentage. Use this when the user "
            "asks in Vietnamese or English for commands like 'nâng hai tay 50%', "
            "'hạ cả hai tay 30%', 'đưa hai tay lên 75%', or 'set both arms to "
            "40 percent'. 0% is fully lowered and 100% is fully raised. Do not "
            "only answer with text for these commands; call this function."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "percent": {
                    "type": "integer",
                    "description": "Both arms position percent. 0 is lowered, 100 is raised.",
                    "minimum": 0,
                    "maximum": 100,
                    "default": 100,
                }
            },
            "required": ["percent"],
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

def _clamp_percent(percent) -> int:
    try:
        target = int(percent)
    except (TypeError, ValueError):
        target = 100
    return max(0, min(100, target))

def _percent_args(percent: int) -> str:
    return json.dumps({"percent": percent}, separators=(",", ":"))


async def _call_arm_tool(
    conn: "ConnectionHandler", function_name: str, success_response: str, args_str: str = "{}"
):
    tool_name = ARM_TOOLS.get(function_name) or ARM_PERCENT_TOOLS[function_name]
    logger.bind(tag=TAG).info(f"arm tool invoked: {function_name} -> {tool_name}")

    mcp_client = getattr(conn, "mcp_client", None)
    if mcp_client is None:
        logger.bind(tag=TAG).warning("arm tool aborted: conn.mcp_client is None")
        return ActionResponse(
            Action.ERROR,
            response="Thiết bị chưa sẵn sàng để điều khiển tay.",
        )

    if not await mcp_client.is_ready():
        logger.bind(tag=TAG).warning("arm tool aborted: mcp_client not ready")
        return ActionResponse(
            Action.ERROR,
            response="Danh sách công cụ thiết bị chưa sẵn sàng.",
        )

    if not mcp_client.has_tool(tool_name):
        logger.bind(tag=TAG).warning(
            f"arm tool aborted: tool {tool_name!r} not advertised. "
            f"Device tools: {_available_tools(mcp_client)}"
        )
        return ActionResponse(
            Action.ERROR,
            response="Thiết bị chưa có công cụ điều khiển tay.",
        )

    from core.providers.tools.device_mcp.mcp_handler import call_mcp_tool

    result = await call_mcp_tool(conn, mcp_client, tool_name, args_str)
    if not _tool_result_is_true(result):
        logger.bind(tag=TAG).warning(f"arm tool failed result={result!r}")
        return ActionResponse(
            Action.ERROR,
            response="Main đã gọi lệnh nhưng servant không xác nhận UART.",
        )

    return ActionResponse(
        Action.RESPONSE,
        result="ok",
        response=success_response,
    )


@register_function("raise_right_arm", raise_right_arm_function_desc, ToolType.SYSTEM_CTL)
async def raise_right_arm(conn: "ConnectionHandler"):
    return await _call_arm_tool(conn, "raise_right_arm", "Đã nâng tay phải.")


@register_function("lower_left_arm", lower_left_arm_function_desc, ToolType.SYSTEM_CTL)
async def lower_left_arm(conn: "ConnectionHandler"):
    return await _call_arm_tool(conn, "lower_left_arm", "Đã hạ tay trái.")


@register_function("lower_right_arm", lower_right_arm_function_desc, ToolType.SYSTEM_CTL)
async def lower_right_arm(conn: "ConnectionHandler"):
    return await _call_arm_tool(conn, "lower_right_arm", "Đã hạ tay phải.")


@register_function("raise_both_arms", raise_both_arms_function_desc, ToolType.SYSTEM_CTL)
async def raise_both_arms(conn: "ConnectionHandler"):
    return await _call_arm_tool(conn, "raise_both_arms", "Đã nâng hai tay.")


@register_function("lower_both_arms", lower_both_arms_function_desc, ToolType.SYSTEM_CTL)
async def lower_both_arms(conn: "ConnectionHandler"):
    return await _call_arm_tool(conn, "lower_both_arms", "Đã hạ hai tay.")

@register_function("set_left_arm_percent", set_left_arm_percent_function_desc, ToolType.SYSTEM_CTL)
async def set_left_arm_percent(conn: "ConnectionHandler", percent: int = 100):
    target = _clamp_percent(percent)
    return await _call_arm_tool(
        conn,
        "set_left_arm_percent",
        f"Đã chỉnh tay trái đến {target}%.",
        _percent_args(target),
    )

@register_function("set_right_arm_percent", set_right_arm_percent_function_desc, ToolType.SYSTEM_CTL)
async def set_right_arm_percent(conn: "ConnectionHandler", percent: int = 100):
    target = _clamp_percent(percent)
    return await _call_arm_tool(
        conn,
        "set_right_arm_percent",
        f"Đã chỉnh tay phải đến {target}%.",
        _percent_args(target),
    )

@register_function("set_both_arms_percent", set_both_arms_percent_function_desc, ToolType.SYSTEM_CTL)
async def set_both_arms_percent(conn: "ConnectionHandler", percent: int = 100):
    target = _clamp_percent(percent)
    return await _call_arm_tool(
        conn,
        "set_both_arms_percent",
        f"Đã chỉnh hai tay đến {target}%.",
        _percent_args(target),
    )
