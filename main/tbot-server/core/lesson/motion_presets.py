"""Fixed lesson motion presets; manifests never supply raw servo parameters."""

from typing import Dict, Tuple

from core.providers.tools.device_mcp.mcp_handler import call_mcp_tool


ALLOWED_MOTION_PRESETS = {
    "rest", "teach", "presentLeft", "presentRight", "listen", "thinking",
    "encourage", "tryAgain", "celebrate", "goodbye",
}

_PRESET_TOOLS: Dict[str, Tuple[str, ...]] = {
    "rest": ("self_robot_both_arms_lower", "self_robot_head_center"),
    "teach": ("self_robot_right_arm_raise", "self_robot_head_center"),
    "presentLeft": ("self_robot_head_turn_left",),
    "presentRight": ("self_robot_head_turn_right",),
    "listen": ("self_robot_head_center",),
    "thinking": ("self_robot_head_turn_left",),
    "encourage": ("self_robot_right_arm_raise",),
    "tryAgain": ("self_robot_head_center",),
    "celebrate": ("self_robot_both_arms_raise",),
    "goodbye": ("self_robot_right_arm_raise",),
}


def motion_preset_tools(preset: str) -> Tuple[str, ...]:
    """Return the fixed server-side MCP tool sequence for an authored preset."""
    return _PRESET_TOOLS.get(preset, ())


async def dispatch_motion_preset(conn, preset: str) -> bool:
    """Dispatch a named preset best-effort; unavailable motion is a normal degrade."""
    if preset not in ALLOWED_MOTION_PRESETS:
        return False
    client = getattr(conn, "mcp_client", None)
    if client is None:
        return False
    ready = getattr(client, "is_ready", None)
    try:
        if callable(ready) and not await ready():
            return False
        sent = False
        for tool in motion_preset_tools(preset):
            has_tool = getattr(client, "has_tool", None)
            if callable(has_tool) and not has_tool(tool):
                continue
            await call_mcp_tool(conn, client, tool, {}, timeout=0.25)
            sent = True
        return sent
    except Exception:
        return False
