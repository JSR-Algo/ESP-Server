from aiohttp import web

from core.api.lesson_nudge_handler import LessonNudgeHandler
from core.providers.tools.device_mcp.mcp_handler import call_mcp_tool


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
        try:
            result = await call_mcp_tool(conn, mcp_client, tool_name, args, timeout=30)
        except Exception as exc:
            return web.json_response(
                {"error": "MCP_CALL_FAILED", "message": str(exc)},
                status=409,
            )

        return web.json_response({"data": {"called": True, "result": result}}, status=202)
