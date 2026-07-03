"""Robot arm actions through the device MCP UART bridge."""

from typing import TYPE_CHECKING

from config.logger import setup_logging
from plugins_func.register import Action, ActionResponse, ToolType, register_function

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

TAG = __name__
logger = setup_logging()

LEFT_ARM_TOOL = "self_robot_left_arm_raise"
RIGHT_ARM_TOOL = "self_robot_right_arm_raise"
LEFT_ARM_LOWER_TOOL = "self_robot_left_arm_lower"
RIGHT_ARM_LOWER_TOOL = "self_robot_right_arm_lower"
BOTH_ARMS_RAISE_TOOL = "self_robot_both_arms_raise"
BOTH_ARMS_LOWER_TOOL = "self_robot_both_arms_lower"
MOTION_TOOL_ACK_TIMEOUT_SEC = 0.25

ARM_TOOL_BY_FUNCTION = {
    "raise_left_arm": LEFT_ARM_TOOL,
    "raise_right_arm": RIGHT_ARM_TOOL,
    "lower_left_arm": LEFT_ARM_LOWER_TOOL,
    "lower_right_arm": RIGHT_ARM_LOWER_TOOL,
    "raise_both_arms": BOTH_ARMS_RAISE_TOOL,
    "lower_both_arms": BOTH_ARMS_LOWER_TOOL,
}

_ARM_ACTION_TEXT = {
    "raise_left_arm": {
        "description": (
            "Raise the robot's left arm / left hand. Use this function when the user asks "
            "in Vietnamese or English to raise the left arm, for example: 'nâng tay trái', "
            "'giơ tay trái', 'dơ tay trái', 'đưa tay trái lên', 'raise left arm'. Do not only answer with text."
        ),
        "missing_tool": "Thiết bị chưa có công cụ nâng tay trái.",
        "success": "Đã nâng tay trái.",
    },
    "raise_right_arm": {
        "description": (
            "Raise the robot's right arm / right hand. Use this function for: 'nâng tay phải', "
            "'giơ tay phải', 'dơ tay phải', 'đưa tay phải lên', 'raise right arm'. Do not only answer with text."
        ),
        "missing_tool": "Thiết bị chưa có công cụ nâng tay phải.",
        "success": "Đã nâng tay phải.",
    },
    "lower_left_arm": {
        "description": (
            "Lower the robot's left arm / left hand. Use this function for: 'hạ tay trái', "
            "'bỏ tay trái xuống', 'lower left arm'. Do not only answer with text."
        ),
        "missing_tool": "Thiết bị chưa có công cụ hạ tay trái.",
        "success": "Đã hạ tay trái.",
    },
    "lower_right_arm": {
        "description": (
            "Lower the robot's right arm / right hand. Use this function for: 'hạ tay phải', "
            "'bỏ tay phải xuống', 'lower right arm'. Do not only answer with text."
        ),
        "missing_tool": "Thiết bị chưa có công cụ hạ tay phải.",
        "success": "Đã hạ tay phải.",
    },
    "raise_both_arms": {
        "description": (
            "Raise both robot arms / hands. Use this function for: 'nâng hai tay', "
            "'giơ cả hai tay', 'dơ cả hai tay', 'dơ hai tay', 'raise both arms'. Do not only answer with text."
        ),
        "missing_tool": "Thiết bị chưa có công cụ nâng cả hai tay.",
        "success": "Đã nâng cả hai tay.",
    },
    "lower_both_arms": {
        "description": (
            "Lower both robot arms / hands. Use this function for: 'hạ hai tay', "
            "'hạ cả hai tay', 'lower both arms'. Do not only answer with text."
        ),
        "missing_tool": "Thiết bị chưa có công cụ hạ cả hai tay.",
        "success": "Đã hạ cả hai tay.",
    },
}

def _function_desc(function_name: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": function_name,
            "description": _ARM_ACTION_TEXT[function_name]["description"],
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    }

raise_left_arm_function_desc = _function_desc("raise_left_arm")


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


async def _run_arm_action(conn: "ConnectionHandler", function_name: str):
    logger.bind(tag=TAG).info(f"{function_name} invoked")

    mcp_client = getattr(conn, "mcp_client", None)
    if mcp_client is None:
        logger.bind(tag=TAG).warning(f"{function_name} aborted: conn.mcp_client is None")
        return ActionResponse(
            Action.ERROR,
            response="Thiết bị chưa sẵn sàng để điều khiển tay robot.",
        )

    if not await mcp_client.is_ready():
        logger.bind(tag=TAG).warning(f"{function_name} aborted: mcp_client not ready")
        return ActionResponse(
            Action.ERROR,
            response="Danh sách công cụ thiết bị chưa sẵn sàng.",
        )

    tool_name = ARM_TOOL_BY_FUNCTION[function_name]
    if not mcp_client.has_tool(tool_name):
        logger.bind(tag=TAG).warning(
            f"{function_name} aborted: tool {tool_name!r} not advertised. "
            f"Device tools: {_available_tools(mcp_client)}"
        )
        return ActionResponse(
            Action.ERROR,
            response=_ARM_ACTION_TEXT[function_name]["missing_tool"],
        )

    from core.providers.tools.device_mcp.mcp_handler import call_mcp_tool

    try:
        result = await call_mcp_tool(
            conn,
            mcp_client,
            tool_name,
            "{}",
            timeout=MOTION_TOOL_ACK_TIMEOUT_SEC,
        )
    except TimeoutError:
        logger.bind(tag=TAG).warning(f"{function_name} ack timed out; treating command as sent")
        result = False
    if not _tool_result_is_true(result):
        logger.bind(tag=TAG).warning(f"{function_name} unconfirmed result={result!r}")

    return _motion_response(result, _ARM_ACTION_TEXT[function_name]["success"])

@register_function("raise_left_arm", raise_left_arm_function_desc, ToolType.SYSTEM_CTL)
async def raise_left_arm(conn: "ConnectionHandler"):
    return await _run_arm_action(conn, "raise_left_arm")

@register_function("raise_right_arm", _function_desc("raise_right_arm"), ToolType.SYSTEM_CTL)
async def raise_right_arm(conn: "ConnectionHandler"):
    return await _run_arm_action(conn, "raise_right_arm")

@register_function("lower_left_arm", _function_desc("lower_left_arm"), ToolType.SYSTEM_CTL)
async def lower_left_arm(conn: "ConnectionHandler"):
    return await _run_arm_action(conn, "lower_left_arm")

@register_function("lower_right_arm", _function_desc("lower_right_arm"), ToolType.SYSTEM_CTL)
async def lower_right_arm(conn: "ConnectionHandler"):
    return await _run_arm_action(conn, "lower_right_arm")

@register_function("raise_both_arms", _function_desc("raise_both_arms"), ToolType.SYSTEM_CTL)
async def raise_both_arms(conn: "ConnectionHandler"):
    return await _run_arm_action(conn, "raise_both_arms")

@register_function("lower_both_arms", _function_desc("lower_both_arms"), ToolType.SYSTEM_CTL)
async def lower_both_arms(conn: "ConnectionHandler"):
    return await _run_arm_action(conn, "lower_both_arms")
