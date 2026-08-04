import asyncio
import json
from collections.abc import Callable
from contextlib import suppress
from typing import Optional  # noqa: UP035 - Python 3.9 runtime compatibility

from aiohttp import web

from core.api.lesson_nudge_handler import LessonNudgeHandler, _conn_mac
from core.lesson.cache_key_contract import CacheEvictionRefused, validate_cache_key
from core.lesson.esp_build_identity import (
    approved_identities_from_config,
    evaluate_esp_build_identity,
)
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
_HIL_TRANSPORT_ABORT_TIMEOUT_SEC = 1.0
_MCP_RESULT_CLEANUP_TIMEOUT_SEC = 0.05


class MCPUnknownToolError(RuntimeError):
    """Privacy-safe proof that a correlated MCP response rejected the tool."""

    def __init__(self):
        super().__init__("mcp-unknown-tool")


class MCPLessonAssetSyncInvalidRequestError(RuntimeError):
    """Privacy-safe proof that firmware rejected an invalid SD sync pack."""

    def __init__(self):
        super().__init__("lesson-asset-sync-invalid-request")


class MCPLessonAssetSyncStorageBusyError(RuntimeError):
    """Privacy-safe proof that firmware storage ownership remains unavailable."""

    def __init__(self):
        super().__init__("lesson-asset-sync-storage-busy")


class MCPAmbiguousClientIdentityError(RuntimeError):
    """An internal route matched more than one active firmware client identity."""

    def __init__(self):
        super().__init__("mcp-client-identity-ambiguous")


class MCPResultCleanupError(RuntimeError):
    """A registered MCP response future could not be proven gone."""

    def __init__(self):
        super().__init__("mcp-result-cleanup-unproven")


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


def _is_lesson_asset_sync_invalid_request(result_or_error, tool_name: str) -> bool:
    if tool_name != "self.lesson_assets.sync_to_sd":
        return False
    message: object
    if isinstance(result_or_error, BaseException):
        message = str(result_or_error).removeprefix("MCP error:").strip()
    elif isinstance(result_or_error, dict) and result_or_error.get("isError") is True:
        raw_error = result_or_error.get("error")
        message = raw_error.get("message") if isinstance(raw_error, dict) else raw_error
    else:
        return False
    return str(message or "").strip().casefold() == "lesson asset sync request invalid"


def _is_lesson_asset_sync_storage_busy(result_or_error, tool_name: str) -> bool:
    if tool_name != "self.lesson_assets.sync_to_sd":
        return False
    message: object
    if isinstance(result_or_error, BaseException):
        message = str(result_or_error).removeprefix("MCP error:").strip()
    elif isinstance(result_or_error, dict) and result_or_error.get("isError") is True:
        raw_error = result_or_error.get("error")
        message = raw_error.get("message") if isinstance(raw_error, dict) else raw_error
    else:
        return False
    return str(message or "").strip().casefold() == "lesson asset storage busy"


def _local_cleanup_registered_call(
    mcp_client, tool_call_id: int, result_future
) -> bool:
    call_results = getattr(mcp_client, "call_results", None)
    if not isinstance(call_results, dict):
        return False
    current = call_results.get(tool_call_id)
    if current is not None and current is not result_future:
        return False
    try:
        call_results.pop(tool_call_id, None)
    except Exception:
        return False
    if not result_future.done():
        result_future.cancel()
    return tool_call_id not in call_results and result_future.done()


async def _cleanup_registered_call(
    mcp_client, tool_call_id: int, result_future
) -> None:
    async def cleanup() -> None:
        await mcp_client.cleanup_call_result(tool_call_id)

    cleanup_task = asyncio.create_task(cleanup())
    settled = False
    try:
        done, _pending = await asyncio.wait(
            {cleanup_task}, timeout=_MCP_RESULT_CLEANUP_TIMEOUT_SEC
        )
        settled = cleanup_task in done
    except asyncio.CancelledError:
        settled = False

    if not settled:
        cleanup_task.cancel()
        try:
            done, _pending = await asyncio.wait(
                {cleanup_task}, timeout=_MCP_RESULT_CLEANUP_TIMEOUT_SEC
            )
            settled = cleanup_task in done
        except asyncio.CancelledError:
            settled = False

    cleanup_succeeded = False
    if settled:
        cleanup_result = (
            await asyncio.gather(cleanup_task, return_exceptions=True)
        )[0]
        cleanup_succeeded = (
            not cleanup_task.cancelled()
            and not isinstance(cleanup_result, BaseException)
        )

    if cleanup_succeeded:
        if not result_future.done():
            result_future.cancel()
        call_results = getattr(mcp_client, "call_results", None)
        if not isinstance(call_results, dict) or tool_call_id not in call_results:
            return

    local_cleanup_proven = _local_cleanup_registered_call(
        mcp_client, tool_call_id, result_future
    )
    if settled and local_cleanup_proven:
        return
    raise MCPResultCleanupError()


async def _abort_connection_transport(conn) -> None:
    websocket = getattr(conn, "websocket", None)
    if websocket is None:
        return
    transport = getattr(websocket, "transport", None)
    abort = getattr(transport, "abort", None)
    if callable(abort):
        abort()

    async def close_and_wait() -> None:
        close = getattr(websocket, "close", None)
        if callable(close):
            try:
                await close(code=1011, reason="MCP dispatch cancelled")
            except TypeError:
                await close()
        wait_closed = getattr(websocket, "wait_closed", None)
        if callable(wait_closed):
            await wait_closed()

    async def bounded_close() -> None:
        with suppress(Exception):
            await asyncio.wait_for(
                close_and_wait(), timeout=_HIL_TRANSPORT_ABORT_TIMEOUT_SEC
            )

    abort_task = asyncio.create_task(bounded_close())
    while not abort_task.done():
        try:
            await asyncio.shield(abort_task)
        except asyncio.CancelledError:
            continue
    await asyncio.gather(abort_task, return_exceptions=True)

def _mcp_call_timeout(tool_name: str) -> float:
    return MOTION_TOOL_ACK_TIMEOUT_SEC if tool_name.startswith(_ROBOT_MOTION_TOOL_PREFIXES) else 30


def _is_robot_motion_tool(tool_name: str) -> bool:
    return tool_name.startswith(_ROBOT_MOTION_TOOL_PREFIXES)


_IDENTITY_ATTESTED_HIL_TOOLS = frozenset(
    {"self.lesson_assets.hil.status", "self.lesson_assets.hil.inspect"}
)
_IDENTITY_BOUND_MUTATING_HIL_TOOLS = frozenset(
    {
        "self.lesson_assets.hil.arm_fault",
        "self.lesson_assets.hil.stage_fixture",
        "self.lesson_assets.hil.cleanup_fixture",
        "self.lesson_assets.evict_cache_key",
        "self.lesson_assets.sync_to_sd",
    }
)


def _attest_hil_result(
    tool_name: str, result, headers, approved_identities, connection_binding_id: str
):
    if tool_name not in _IDENTITY_ATTESTED_HIL_TOOLS:
        return result
    evaluated = evaluate_esp_build_identity(headers, approved_identities)
    if evaluated.status != "approved":
        raise ValueError(f"ESP build identity {evaluated.status}")
    if not isinstance(connection_binding_id, str) or not connection_binding_id:
        raise ValueError("ESP connection binding unavailable")
    encoded = isinstance(result, str)
    try:
        value = json.loads(result) if encoded else result
    except (TypeError, ValueError) as exc:
        raise ValueError("HIL result is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("HIL result must be an object")
    if any(
        name in value
        for name in (
            "identitySchemaVersion",
            "buildIdentity",
            "buildIdentityId",
            "connectionBindingId",
        )
    ):
        raise ValueError("HIL result identity fields already exist")
    attested = dict(value)
    attested["identitySchemaVersion"] = 1
    attested["buildIdentity"] = evaluated.identity
    attested["buildIdentityId"] = evaluated.identity_id
    attested["connectionBindingId"] = connection_binding_id
    return json.dumps(attested, sort_keys=True, separators=(",", ":")) if encoded else attested


def _prepare_hil_mutation_args(
    tool_name: str, args: dict, headers, approved_identities, connection_binding_id: str
) -> dict:
    if tool_name not in _IDENTITY_BOUND_MUTATING_HIL_TOOLS:
        return args
    evaluated = evaluate_esp_build_identity(headers, approved_identities)
    if evaluated.status != "approved":
        raise ValueError(f"ESP build identity {evaluated.status}")
    forwarded = dict(args)
    expected_identity = forwarded.pop("expectedBuildIdentityId", None)
    expected_connection = forwarded.pop("expectedConnectionBindingId", None)
    if expected_identity != evaluated.identity_id:
        raise ValueError("ESP build identity changed before mutation")
    if expected_connection != connection_binding_id:
        raise ValueError("ESP connection changed before mutation")
    return forwarded


def _normalize_mac(value) -> Optional[str]:  # noqa: UP045 - Python 3.9 runtime
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


def _hil_timeout(body: dict) -> Optional[int]:  # noqa: UP045 - Python 3.9 runtime
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
    send_reservation=None,
    on_dispatched: Optional[Callable[[], None]] = None,  # noqa: UP045
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

    async def dispatch():
        if on_dispatched is not None:
            on_dispatched()
        if send_reservation is None:
            await send_mcp_message(conn, payload)
            return await result_future
        else:
            async with send_reservation as current:
                if not current:
                    raise ValueError("ESP connection changed before dispatch")
                try:
                    await send_mcp_message(conn, payload)
                    return await result_future
                except asyncio.CancelledError:
                    await _abort_connection_transport(conn)
                    raise

    try:
        raw_result = await asyncio.wait_for(dispatch(), timeout=timeout)
    except BaseException as exc:
        await _cleanup_registered_call(mcp_client, tool_call_id, result_future)
        if _is_lesson_asset_sync_invalid_request(exc, tool_name):
            raise MCPLessonAssetSyncInvalidRequestError() from None
        if _is_lesson_asset_sync_storage_busy(exc, tool_name):
            raise MCPLessonAssetSyncStorageBusyError() from None
        raise

    if isinstance(raw_result, dict):
        if raw_result.get("isError") is True:
            if _is_correlated_unknown_tool_result(raw_result, tool_name):
                raise MCPUnknownToolError() from None
            if _is_lesson_asset_sync_invalid_request(raw_result, tool_name):
                raise MCPLessonAssetSyncInvalidRequestError() from None
            if _is_lesson_asset_sync_storage_busy(raw_result, tool_name):
                raise MCPLessonAssetSyncStorageBusyError() from None
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

    def _connection_registry_key(self, device_id, selected):
        if self.connections is None:
            return None
        if self.connections.get(device_id) is selected:
            return device_id
        for key, candidate in self.connections.items():
            if candidate is selected and _conn_client_identity(candidate) == device_id:
                return key
        return None

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

        args = body.get("args", {})
        timeout = _mcp_call_timeout(tool_name)
        is_hil_timeout_path = False
        if is_hil_tool:
            if not isinstance(args, dict):
                return _hil_error("HIL_TOOL_FORBIDDEN", status=403)
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
            try:
                timeout = _hil_timeout(body)
            except ValueError:
                return _hil_error("HIL_TOOL_FORBIDDEN", status=403)
            is_hil_timeout_path = True

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
            if is_hil_timeout_path:
                return _hil_error("HIL_DEVICE_NOT_ALLOWLISTED", status=403)
            return web.json_response(
                {"data": {"called": False, "reason": "device-offline"}},
                status=202,
            )

        if is_hil_timeout_path and not _hil_device_is_allowlisted(self.config, conn):
            return _hil_error("HIL_DEVICE_NOT_ALLOWLISTED", status=403)

        mcp_client = getattr(conn, "mcp_client", None)
        if mcp_client is None:
            if is_hil_timeout_path:
                return _hil_error("HIL_MCP_FAILED", status=409)
            return web.json_response(
                {"error": "MCP_CLIENT_MISSING", "message": "Device MCP client is not available"},
                status=409,
            )
        try:
            approved_identities = approved_identities_from_config(self.config)
            connection_binding_id = str(getattr(conn, "session_id", "") or "")
            args = _prepare_hil_mutation_args(
                tool_name,
                args,
                getattr(conn, "headers", None),
                approved_identities,
                connection_binding_id,
            )
            if (
                tool_name in _IDENTITY_BOUND_MUTATING_HIL_TOOLS
                or is_hil_tool
                or bool(body.get("allowUnlisted"))
            ):
                send_reservation = None
                if tool_name in _IDENTITY_BOUND_MUTATING_HIL_TOOLS:
                    registry_key = self._connection_registry_key(device_id, conn)
                    reserve_current = getattr(
                        self.connections, "reserve_current", None
                    )
                    if registry_key is None or not callable(reserve_current):
                        raise ValueError("ESP connection reservation unavailable")
                    send_reservation = reserve_current(
                        registry_key, conn, connection_binding_id
                    )
                result = await _call_raw_mcp_tool(
                    conn,
                    mcp_client,
                    tool_name,
                    args,
                    timeout=timeout,
                    send_reservation=send_reservation,
                )
            else:
                result = await call_mcp_tool(conn, mcp_client, tool_name, args, timeout=timeout)
            result = _attest_hil_result(
                tool_name,
                result,
                getattr(conn, "headers", None),
                approved_identities,
                connection_binding_id,
            )
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
