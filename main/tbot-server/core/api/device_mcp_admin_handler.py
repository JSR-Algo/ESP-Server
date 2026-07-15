import asyncio
from typing import Callable, Optional

from aiohttp import web

from core.api.lesson_nudge_handler import LessonNudgeHandler
from core.providers.tools.device_mcp.mcp_handler import call_mcp_tool, send_mcp_message

MOTION_TOOL_ACK_TIMEOUT_SEC = 0.25

_ROBOT_MOTION_TOOL_PREFIXES = (
    "self_robot_head_",
    "self_robot_left_arm_",
    "self_robot_right_arm_",
    "self_robot_both_arms_",
    "self.robot.head_",
    "self.robot.left_arm_",
    "self.robot.right_arm_",
    "self.robot.both_arms_",
)


class MCPUnknownToolError(RuntimeError):
    """Privacy-safe proof that a correlated MCP response rejected the tool."""

    def __init__(self):
        super().__init__("mcp-unknown-tool")


def _is_correlated_unknown_tool_result(raw_result, tool_name: str) -> bool:
    if not isinstance(raw_result, dict) or raw_result.get("isError") is not True:
        return False
    error = raw_result.get("error")
    if isinstance(error, dict):
        code = str(error.get("code") or "").strip().lower().replace("-", "_")
        rejected_tool = error.get("toolName") or error.get("tool") or error.get("name")
        return code in {"unknown_tool", "tool_not_found", "unlisted_tool"} and rejected_tool == tool_name
    if not isinstance(error, str) or tool_name not in error:
        return False
    normalized = error.casefold().replace("-", " ").replace("_", " ")
    return any(
        marker in normalized
        for marker in ("unknown tool", "tool not found", "unlisted tool")
    )


async def _cleanup_registered_call(mcp_client, tool_call_id: int) -> None:
    async def cleanup() -> None:
        await mcp_client.cleanup_call_result(tool_call_id)

    cleanup_task = asyncio.create_task(cleanup())
    while not cleanup_task.done():
        try:
            await asyncio.shield(cleanup_task)
        except asyncio.CancelledError:
            continue
    await asyncio.gather(cleanup_task, return_exceptions=True)

def _mcp_call_timeout(tool_name: str) -> float:
    return MOTION_TOOL_ACK_TIMEOUT_SEC if tool_name.startswith(_ROBOT_MOTION_TOOL_PREFIXES) else 30


def _is_robot_motion_tool(tool_name: str) -> bool:
    return tool_name.startswith(_ROBOT_MOTION_TOOL_PREFIXES)


async def _call_raw_mcp_tool(
    conn,
    mcp_client,
    tool_name: str,
    args: dict,
    *,
    timeout: int = 30,
    on_dispatched: Optional[Callable[[], None]] = None,
):
    if not isinstance(args, dict):
        raise ValueError(f"Parameters must be dictionary type, actual type: {type(args)}")

    tool_call_id = await mcp_client.get_next_id()
    result_future = asyncio.Future()
    await mcp_client.register_call_result_future(tool_call_id, result_future)
    payload = {
        "jsonrpc": "2.0",
        "id": tool_call_id,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": args},
    }

    try:
        if on_dispatched is not None:
            on_dispatched()
        await send_mcp_message(conn, payload)
        raw_result = await asyncio.wait_for(result_future, timeout=timeout)
    except BaseException:
        await _cleanup_registered_call(mcp_client, tool_call_id)
        raise

    if isinstance(raw_result, dict):
        if raw_result.get("isError") is True:
            if _is_correlated_unknown_tool_result(raw_result, tool_name):
                raise MCPUnknownToolError() from None
            raise RuntimeError("mcp-tool-call-failed")
        content = raw_result.get("content")
        if isinstance(content, list) and content and isinstance(content[0], dict) and "text" in content[0]:
            return content[0]["text"]
    return str(raw_result)


class DeviceMCPAdminHandler:
    def __init__(self, config: dict, connections):
        self.config = config
        self.connections = connections
        self._shared = LessonNudgeHandler(config, connections)

    async def handle_post(self, request: web.Request) -> web.Response:
        auth_error = self._shared._authorize(request)
        if auth_error is not None:
            return auth_error

        try:
            body = await request.json()
        except Exception:
            body = {}

        tool_name = str((body or {}).get("toolName") or "").strip()
        if not tool_name:
            return web.json_response(
                {"error": "TOOL_NAME_REQUIRED", "message": "Body field 'toolName' is required"},
                status=400,
            )

        device_id = request.match_info.get("deviceId", "")
        conn = await self._shared._find_connection(device_id)
        if conn is None:
            return web.json_response(
                {"data": {"called": False, "reason": "device-offline"}},
                status=202,
            )

        mcp_client = getattr(conn, "mcp_client", None)
        if mcp_client is None:
            return web.json_response(
                {"error": "MCP_CLIENT_MISSING", "message": "Device MCP client is not available"},
                status=409,
            )

        args = (body or {}).get("args", {})
        timeout = _mcp_call_timeout(tool_name)
        try:
            if bool((body or {}).get("allowUnlisted")):
                result = await _call_raw_mcp_tool(conn, mcp_client, tool_name, args, timeout=timeout)
            else:
                result = await call_mcp_tool(conn, mcp_client, tool_name, args, timeout=timeout)
        except TimeoutError as exc:
            if _is_robot_motion_tool(tool_name):
                return web.json_response(
                    {"data": {"called": True, "result": "sent_unconfirmed"}},
                    status=202,
                )
            return web.json_response(
                {"error": "MCP_CALL_FAILED", "message": str(exc)},
                status=409,
            )
        except Exception as exc:
            return web.json_response(
                {"error": "MCP_CALL_FAILED", "message": str(exc)},
                status=409,
            )

        return web.json_response({"data": {"called": True, "result": result}}, status=202)
