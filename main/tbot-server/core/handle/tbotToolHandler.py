"""MCP tool security handler — validates, audits, and rate-limits MCP tool calls."""

import time
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

# ── Allowlist: only these MCP tools may be executed ──────────────────────────
# Each entry maps tool_name -> {arg_name: expected_type}
MCP_TOOL_ALLOWLIST: Dict[str, Dict[str, type]] = {
    "self_audio_speaker_set_volume": {"volume": int},
    "self_audio_speaker_get_volume": {},
    "self_audio_speaker_mute": {},
    "self_audio_speaker_unmute": {},
    "self_audio_microphone_mute": {},
    "self_audio_microphone_unmute": {},
    "self_system_get_battery": {},
    "self_system_get_wifi_status": {},
}

# ── Blocklist: these tools are NEVER allowed (destructive / high-risk) ───────
MCP_TOOL_BLOCKLIST = {
    "self_system_factory_reset",
    "self_system_reboot",
    "self_firmware_flash",
    "self_network_disconnect",
}

# ── Sensitive tools: allowed, but trigger audit warnings ─────────────────────
SENSITIVE_TOOLS = {
    "self_audio_speaker_set_volume",
    "self_audio_speaker_mute",
    "self_audio_speaker_unmute",
    "self_audio_microphone_mute",
    "self_audio_microphone_unmute",
}

# ── Rate-limit constants ─────────────────────────────────────────────────────
MCP_RATE_LIMIT_WINDOW_SECONDS = 60
MCP_RATE_LIMIT_MAX_CALLS = 20


def validate_mcp_tool_call(tool_name: str, arguments: Dict[str, Any]) -> tuple[bool, str]:
    """Validate an MCP tool call against allowlist / blocklist.

    Returns:
        (allowed: bool, reason: str)
    """
    if tool_name in MCP_TOOL_BLOCKLIST:
        return False, f"Tool '{tool_name}' is blocked for security reasons"

    if tool_name not in MCP_TOOL_ALLOWLIST:
        return False, f"Tool '{tool_name}' is not in the allowlist"

    expected_args = MCP_TOOL_ALLOWLIST[tool_name]

    # Check required args exist and have correct types
    for arg_name, arg_type in expected_args.items():
        if arg_name not in arguments:
            return False, f"Missing required argument '{arg_name}'"
        if not isinstance(arguments[arg_name], arg_type):
            return False, f"Argument '{arg_name}' must be of type {arg_type.__name__}"

    # Reject unexpected arguments (prevent parameter smuggling)
    for arg_name in arguments:
        if arg_name not in expected_args:
            return False, f"Unexpected argument '{arg_name}'"

    return True, "OK"


def audit_log_tool_call(tool_name: str, arguments: Dict[str, Any], device_id: str = None):
    """Structured audit logging for ALL MCP tool calls."""
    device = device_id or "unknown"
    logger.info(f"MCP_TOOL_CALL: tool={tool_name} args={arguments} device={device}")

    if tool_name in SENSITIVE_TOOLS:
        logger.warning(
            f"Sensitive MCP tool executed: {tool_name} with args {arguments} device={device}"
        )


def check_mcp_rate_limit(call_times: list) -> tuple[bool, list]:
    """Check and update rate-limit state using a connection's _mcp_tool_call_times."""
    """Check and update rate-limit state.

    Args:
        call_times: mutable list of timestamps (floats) for this connection.

    Returns:
        (allowed: bool, updated_call_times: list)
    """
    now = time.time()
    # Prune old entries outside the window
    updated = [t for t in call_times if now - t < MCP_RATE_LIMIT_WINDOW_SECONDS]
    if len(updated) >= MCP_RATE_LIMIT_MAX_CALLS:
        logger.warning(
            f"MCP tool call rate limit exceeded: {len(updated)} calls in "
            f"{MCP_RATE_LIMIT_WINDOW_SECONDS}s (max {MCP_RATE_LIMIT_MAX_CALLS})"
        )
        return False, updated
    updated.append(now)
    return True, updated
