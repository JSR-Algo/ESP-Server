"""Raise the robot left arm through the device MCP UART bridge."""

from typing import TYPE_CHECKING

from config.logger import setup_logging
from plugins_func.register import Action, ActionResponse, ToolType, register_function

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

TAG = __name__
logger = setup_logging()

LEFT_ARM_TOOL = "self_robot_left_arm_raise"

raise_left_arm_function_desc = {
    "type": "function",
    "function": {
        "name": "raise_left_arm",
        "description": (
            "Raise the robot's left arm / left hand. Use this function when the user asks "
            "in Vietnamese or English to raise the left arm, for example: 'nâng tay trái', "
            "'giơ tay trái', 'đưa tay trái lên', 'raise left arm'. Do not only answer with "
            "text for these commands; call this function."
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


@register_function("raise_left_arm", raise_left_arm_function_desc, ToolType.SYSTEM_CTL)
async def raise_left_arm(conn: "ConnectionHandler"):
    logger.bind(tag=TAG).info("raise_left_arm invoked")

    mcp_client = getattr(conn, "mcp_client", None)
    if mcp_client is None:
        logger.bind(tag=TAG).warning("raise_left_arm aborted: conn.mcp_client is None")
        return ActionResponse(
            Action.ERROR,
            response="Thiết bị chưa sẵn sàng để điều khiển tay trái.",
        )

    if not await mcp_client.is_ready():
        logger.bind(tag=TAG).warning("raise_left_arm aborted: mcp_client not ready")
        return ActionResponse(
            Action.ERROR,
            response="Danh sách công cụ thiết bị chưa sẵn sàng.",
        )

    if not mcp_client.has_tool(LEFT_ARM_TOOL):
        logger.bind(tag=TAG).warning(
            f"raise_left_arm aborted: tool {LEFT_ARM_TOOL!r} not advertised. "
            f"Device tools: {_available_tools(mcp_client)}"
        )
        return ActionResponse(
            Action.ERROR,
            response="Thiết bị chưa có công cụ nâng tay trái.",
        )

    from core.providers.tools.device_mcp.mcp_handler import call_mcp_tool

    result = await call_mcp_tool(conn, mcp_client, LEFT_ARM_TOOL, "{}")
    if not _tool_result_is_true(result):
        logger.bind(tag=TAG).warning(f"raise_left_arm failed result={result!r}")
        return ActionResponse(
            Action.ERROR,
            response="Main đã gọi lệnh nhưng servant không xác nhận UART.",
        )

    return ActionResponse(
        Action.RESPONSE,
        result="ok",
        response="Đã nâng tay trái.",
    )
