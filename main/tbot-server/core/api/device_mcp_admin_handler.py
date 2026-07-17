import asyncio
from typing import Callable, Optional

from aiohttp import web

from core.api.lesson_nudge_handler import LessonNudgeHandler, _conn_mac
from core.lesson.sd_pack_evict import CacheEvictionRefused, validate_cache_key
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

_HIL_TOOL_PREFIX = "self.lesson_assets.hil."
_HIL_TOOLS = frozenset(
    {
        "self.lesson_assets.hil.arm_fault",
        "self.lesson_assets.hil.status",
        "self.lesson_assets.hil.stage_fixture",
        "self.lesson_assets.hil.cleanup_fixture",
        "self.lesson_assets.hil.inspect",
    }
)
_HIL_TRIGGER_TOOLS = frozenset(
    {
        "self.lesson_assets.evict_cache_key",
        "self.lesson_assets.sync_to_sd",
    }
)
_HIL_MIN_TIMEOUT_SEC = 5
_HIL_MAX_TIMEOUT_SEC = 75


class MCPUnknownToolError(RuntimeError):
    """Privacy-safe proof that a correlated MCP response rejected the tool."""

    def __init__(self):
        super().__init__("mcp-unknown-tool")


class MCPAmbiguousClientIdentityError(RuntimeError):
    """An internal route matched more than one active firmware client identity."""

    def __init__(self):
        super().__init__("mcp-client-identity-ambiguous")


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


def _normalize_mac(value) -> Optional[str]:
    value = str(value or "").strip().lower()
    parts = value.split(":")
    if len(parts) != 6 or any(
        len(part) != 2 or any(char not in "0123456789abcdef" for char in part)
        for part in parts
    ):
        return None
    return ":".join(parts)


def _conn_client_identity(conn):
    client_id = getattr(conn, "client_id", None)
    if client_id:
        return client_id
    headers = getattr(conn, "headers", None) or {}
    return headers.get("client-id") or headers.get("Client-Id")


def _hil_device_is_allowlisted(config: dict, conn) -> bool:
    lesson = config.get("lesson") if isinstance(config, dict) else None
    allowlist = (
        lesson.get("storage_hil_device_allowlist")
        if isinstance(lesson, dict)
        else None
    )
    if not isinstance(allowlist, list) or len(allowlist) != 1:
        return False
    allowed_mac = _normalize_mac(allowlist[0])
    resolved_mac = _normalize_mac(_conn_mac(conn))
    return bool(allowed_mac and resolved_mac and allowed_mac == resolved_mac)


def _has_canonical_hil_cache_key(tool_name: str, args) -> bool:
    if not isinstance(args, dict):
        return False
    if tool_name == "self.lesson_assets.evict_cache_key":
        candidate = args.get("cacheKey")
    elif tool_name == "self.lesson_assets.sync_to_sd":
        asset_pack = args.get("assetPack")
        candidate = asset_pack.get("cacheKey") if isinstance(asset_pack, dict) else None
    else:
        return False
    try:
        cache_key = validate_cache_key(candidate)
    except CacheEvictionRefused:
        return False
    return cache_key.split("/", 1)[0].startswith("hil-")


def _hil_timeout(body: dict) -> Optional[int]:
    if "timeoutSeconds" not in body:
        return None
    timeout = body.get("timeoutSeconds")
    if type(timeout) is not int or not _HIL_MIN_TIMEOUT_SEC <= timeout <= _HIL_MAX_TIMEOUT_SEC:
        raise ValueError("invalid-hil-timeout")
    return timeout


def _hil_error(code: str, *, status: int) -> web.Response:
    return web.json_response(
        {"error": code, "message": "HIL MCP request rejected"},
        status=status,
    )


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

    async def _find_connection(self, device_id):
        if self.connections is not None:
            conn = self.connections.get(device_id)
            if conn is not None and _normalize_mac(device_id) is not None:
                return conn
            resolved = conn
            for candidate in self.connections.values():
                if _conn_client_identity(candidate) == device_id:
                    if resolved is not None and resolved is not candidate:
                        raise MCPAmbiguousClientIdentityError()
                    resolved = candidate
            if resolved is not None:
                return resolved
        return await self._shared._find_connection(device_id)

    async def handle_post(self, request: web.Request) -> web.Response:
        auth_error = self._shared._authorize(request)
        if auth_error is not None:
            return auth_error

        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}

        tool_name = str(body.get("toolName") or "").strip()
        if not tool_name:
            return web.json_response(
                {"error": "TOOL_NAME_REQUIRED", "message": "Body field 'toolName' is required"},
                status=400,
            )

        is_hil_tool = tool_name in _HIL_TOOLS
        if tool_name.startswith(_HIL_TOOL_PREFIX) and not is_hil_tool:
            return _hil_error("HIL_TOOL_FORBIDDEN", status=403)
        if is_hil_tool and body.get("allowUnlisted") is not True:
            return _hil_error("HIL_TOOL_FORBIDDEN", status=403)

        device_id = request.match_info.get("deviceId", "")
        try:
            conn = await self._find_connection(device_id)
        except MCPAmbiguousClientIdentityError:
            return web.json_response(
                {
                    "error": "MCP_CLIENT_IDENTITY_AMBIGUOUS",
                    "message": "Device MCP connection identity is ambiguous",
                },
                status=409,
            )
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

        args = body.get("args", {})
        timeout = _mcp_call_timeout(tool_name)
        is_hil_timeout_path = False
        if is_hil_tool:
            if not isinstance(args, dict):
                return _hil_error("HIL_TOOL_FORBIDDEN", status=403)
            if not _hil_device_is_allowlisted(self.config, conn):
                return _hil_error("HIL_DEVICE_NOT_ALLOWLISTED", status=403)
            try:
                override = _hil_timeout(body)
            except ValueError:
                return _hil_error("HIL_TOOL_FORBIDDEN", status=403)
            if override is not None:
                timeout = override
            is_hil_timeout_path = True
        elif "timeoutSeconds" in body:
            if tool_name not in _HIL_TRIGGER_TOOLS or not _has_canonical_hil_cache_key(
                tool_name, args
            ):
                return _hil_error("HIL_TOOL_FORBIDDEN", status=403)
            if not _hil_device_is_allowlisted(self.config, conn):
                return _hil_error("HIL_DEVICE_NOT_ALLOWLISTED", status=403)
            try:
                timeout = _hil_timeout(body)
            except ValueError:
                return _hil_error("HIL_TOOL_FORBIDDEN", status=403)
            is_hil_timeout_path = True
        try:
            if is_hil_tool or bool(body.get("allowUnlisted")):
                result = await _call_raw_mcp_tool(conn, mcp_client, tool_name, args, timeout=timeout)
            else:
                result = await call_mcp_tool(conn, mcp_client, tool_name, args, timeout=timeout)
        except (TimeoutError, asyncio.TimeoutError) as exc:
            if is_hil_timeout_path:
                return _hil_error("HIL_MCP_TIMEOUT", status=409)
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
            if is_hil_timeout_path:
                return _hil_error("HIL_MCP_FAILED", status=409)
            return web.json_response(
                {"error": "MCP_CALL_FAILED", "message": str(exc)},
                status=409,
            )

        return web.json_response({"data": {"called": True, "result": result}}, status=202)
