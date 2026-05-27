"""Change speaker volume via the device's MCP audio_speaker.set_volume tool."""

import json
from typing import TYPE_CHECKING, Optional

from plugins_func.register import register_function, ToolType, ActionResponse, Action
from config.logger import setup_logging

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

TAG = __name__
logger = setup_logging()

DEFAULT_STEP = 20
DEFAULT_VOLUME_ON_UNMUTE = 50
# MCP client stores tool names in sanitized form (dots → underscores),
# so we look them up by the sanitized key. The MCP transport restores
# the original dotted name via mcp_client.name_mapping when calling.
GET_STATUS_TOOL = "self_get_device_status"
SET_VOLUME_TOOL = "self_audio_speaker_set_volume"


change_volume_function_desc = {
    "type": "function",
    "function": {
        "name": "change_volume",
        "description": (
            "Adjust the speaker output volume.\n\n"
            "PREFER 'up'/'down' for RELATIVE changes ('louder', 'quieter', "
            "'to lên', 'nhỏ lại', 'tăng âm lượng', 'giảm âm lượng', 'một chút'). "
            "Do NOT use 'set' for relative changes — that jumps the volume "
            "to an arbitrary level and surprises the user.\n\n"
            "USE 'set' ONLY when the user gives an EXPLICIT numeric level "
            "('đặt 70%', 'âm lượng 50', 'volume to 30'), an explicit extreme "
            "('to nhất / hết cỡ / 100%' → level=100; 'nhỏ nhất / mức thấp' → "
            "level=10), or a clear bucket ('âm lượng vừa phải' → level=50).\n\n"
            "Examples (Vietnamese):\n"
            "  'to lên' → action=up (step=20)\n"
            "  'to lên một chút' → action=up, step=10\n"
            "  'to lên nhiều' → action=up, step=40\n"
            "  'nhỏ lại' → action=down (step=20)\n"
            "  'âm lượng 70%' → action=set, level=70\n"
            "  'to hết cỡ' → action=set, level=100\n"
            "  'tắt tiếng' → action=mute\n"
            "  'bật tiếng lại' → action=unmute"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["up", "down", "set", "mute", "unmute"],
                    "description": (
                        "Volume action: 'up'/'down' for relative change, "
                        "'set' only when user names a specific level, "
                        "'mute'/'unmute' for silence toggle."
                    ),
                },
                "level": {
                    "type": "integer",
                    "description": (
                        "Target volume 0-100. REQUIRED when action='set'. "
                        "Must NOT be sent when action is 'up'/'down'/'mute'/'unmute'."
                    ),
                },
                "step": {
                    "type": "integer",
                    "description": (
                        "Change amount for 'up'/'down'. Default 20. "
                        "Use 10 for 'một chút / nhẹ', 40 for 'nhiều / mạnh'."
                    ),
                },
                "response_success": {
                    "type": "string",
                    "description": (
                        "Friendly reply in the user's language confirming the change. "
                        "Use {volume} as placeholder for the actual final volume level."
                    ),
                },
            },
            "required": ["action", "response_success"],
        },
    },
}


def _clamp_volume(value: int) -> int:
    if value < 0:
        return 0
    if value > 100:
        return 100
    return int(value)


async def _query_current_volume(conn) -> Optional[int]:
    mcp_client = getattr(conn, "mcp_client", None)
    if mcp_client is None:
        return None
    try:
        if not await mcp_client.is_ready():
            return None
        if not mcp_client.has_tool(GET_STATUS_TOOL):
            return None
        from core.providers.tools.device_mcp.mcp_handler import call_mcp_tool

        raw = await call_mcp_tool(conn, mcp_client, GET_STATUS_TOOL, "{}")
        data = raw
        if isinstance(raw, str):
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                return None
        if not isinstance(data, dict):
            return None
        speaker = data.get("audio_speaker") or data.get("speaker") or {}
        if isinstance(speaker, dict):
            volume = speaker.get("volume")
            if isinstance(volume, (int, float)):
                return _clamp_volume(int(volume))
    except Exception as exc:
        logger.bind(tag=TAG).warning(f"Failed to query current volume: {exc}")
    return None


async def _set_device_volume(conn, volume: int) -> bool:
    mcp_client = getattr(conn, "mcp_client", None)
    if mcp_client is None:
        logger.bind(tag=TAG).warning("set_volume aborted: conn.mcp_client is None")
        return False
    try:
        ready = await mcp_client.is_ready()
        if not ready:
            logger.bind(tag=TAG).warning("set_volume aborted: mcp_client not ready")
            return False
        if not mcp_client.has_tool(SET_VOLUME_TOOL):
            try:
                available = sorted(getattr(mcp_client, "tools", {}).keys())
            except Exception:
                available = []
            logger.bind(tag=TAG).warning(
                f"set_volume aborted: tool {SET_VOLUME_TOOL!r} not advertised. "
                f"Device tools: {available}"
            )
            return False
        from core.providers.tools.device_mcp.mcp_handler import call_mcp_tool

        await call_mcp_tool(
            conn,
            mcp_client,
            SET_VOLUME_TOOL,
            json.dumps({"volume": volume}),
        )
        return True
    except Exception as exc:
        logger.bind(tag=TAG).warning(f"Failed to set volume to {volume}: {exc}")
        return False


def _format_response(template: str, volume: int) -> str:
    if not template:
        return f"Đã chỉnh âm lượng còn {volume}%"
    return (
        template
        .replace("{volume}", str(volume))
        .replace("{value}", str(volume))
        .replace("{level}", str(volume))
    )


@register_function("change_volume", change_volume_function_desc, ToolType.SYSTEM_CTL)
async def change_volume(
    conn: "ConnectionHandler",
    action: str,
    level: Optional[int] = None,
    step: Optional[int] = None,
    response_success: str = "",
):
    logger.bind(tag=TAG).info(
        f"change_volume invoked action={action!r} level={level} step={step}"
    )
    normalized = (action or "").strip().lower()
    if normalized not in {"up", "down", "set", "mute", "unmute"}:
        logger.bind(tag=TAG).warning(
            f"change_volume rejected unsupported action: {action!r}"
        )
        return ActionResponse(
            action=Action.ERROR,
            response=f"Hành động chỉnh âm lượng không hỗ trợ: {action}",
        )

    step_value = DEFAULT_STEP
    if step is not None:
        try:
            step_value = max(1, min(100, int(step)))
        except (TypeError, ValueError):
            step_value = DEFAULT_STEP

    last_known = getattr(conn, "_last_known_volume", None)

    if normalized == "set":
        if level is None:
            return ActionResponse(
                action=Action.ERROR,
                response="Cần cho biết mức âm lượng muốn đặt (0-100).",
            )
        try:
            target = _clamp_volume(int(level))
        except (TypeError, ValueError):
            return ActionResponse(
                action=Action.ERROR,
                response="Mức âm lượng không hợp lệ.",
            )
    elif normalized == "mute":
        if isinstance(last_known, int) and last_known > 0:
            pass
        else:
            current = await _query_current_volume(conn)
            if isinstance(current, int) and current > 0:
                conn._last_known_volume = current
        target = 0
    elif normalized == "unmute":
        if isinstance(last_known, int) and last_known > 0:
            target = last_known
        else:
            target = DEFAULT_VOLUME_ON_UNMUTE
    else:
        current = await _query_current_volume(conn)
        if current is None:
            current = last_known if isinstance(last_known, int) else DEFAULT_VOLUME_ON_UNMUTE
        delta = step_value if normalized == "up" else -step_value
        target = _clamp_volume(current + delta)

    success = await _set_device_volume(conn, target)
    if not success:
        return ActionResponse(
            action=Action.ERROR,
            response="Không gửi được lệnh chỉnh âm lượng đến thiết bị.",
        )

    conn._last_known_volume = target
    reply = _format_response(response_success, target)
    logger.bind(tag=TAG).info(f"change_volume action={normalized} target={target}")
    return ActionResponse(
        action=Action.RESPONSE,
        result=str(target),
        response=reply,
    )
