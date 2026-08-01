"""Reviewed child-product tool exposure for classic and Live pipelines."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Iterable, List


ALWAYS_INCLUDE = (
    "handle_exit_intent",
    "change_role",
    "change_volume",
    "raise_left_arm",
    "raise_right_arm",
    "lower_left_arm",
    "lower_right_arm",
    "raise_both_arms",
    "lower_both_arms",
    "set_left_arm_percent",
    "set_right_arm_percent",
    "set_both_arms_percent",
    "turn_head_left",
    "turn_head_right",
    "center_head",
    "set_head_angle",
    "set_head_percent",
    "turn_head_left_then_right_max",
    "get_weather",
    "web_search",
    "get_news_from_newsnow",
)

ALWAYS_INCLUDE_WHEN_LESSON_ENABLED = ("start_lesson",)
LESSON_CONVERSATION_TOOLS = (
    "lesson_child_response",
    "lesson_pronunciation_outcome",
    "lesson_context_turn",
    "lesson_visual_reaction",
    "lesson_continue",
)

# Music remains a classic-pipeline allowance for existing product behavior. Live
# removes it with its documented incompatibility filter before sending tools to
# Gemini Live.
CONFIGURABLE_CHILD_TOOLS = frozenset(
    {
        "play_music",
        "play_music_live",
        "pause_music",
        "resume_music",
        "stop_music",
    }
)

ADULT_CHILD_DENYLIST = frozenset(
    {
        "get_news_from_chinanews",
    }
)

DANGER_TOOL_NAME_RE = re.compile(
    r"(^|_)(delete|erase|format|factory|reset|reboot|shutdown|ota|firmware|flash|shell|exec|command)($|_)",
    re.IGNORECASE,
)


def product_tool_names(conn: Any) -> List[str]:
    """Return the canonical child-product tool names for this connection."""
    names: list[str] = list(ALWAYS_INCLUDE)
    names.extend(_configured_child_tools(conn))
    if lesson_start_enabled(conn):
        names.extend(ALWAYS_INCLUDE_WHEN_LESSON_ENABLED)
    if lesson_runtime_enabled(conn):
        names.extend(LESSON_CONVERSATION_TOOLS)
    return _dedupe(name for name in names if _is_child_allowed(name))


def lesson_start_enabled(conn: Any) -> bool:
    return lesson_runtime_enabled(conn) or sample_lesson_enabled(conn)


def lesson_runtime_enabled(conn: Any) -> bool:
    if not lesson_runtime_config_enabled(conn):
        return False
    checker = getattr(conn, "_lesson_runtime_enabled", None)
    if callable(checker):
        try:
            return bool(checker())
        except Exception:
            return False
    return True


def lesson_runtime_config_enabled(conn: Any) -> bool:
    """Fail-closed runtime admission for the single-device rollout."""
    config = getattr(conn, "config", None)
    lesson_cfg = config.get("lesson", {}) if isinstance(config, Mapping) else {}
    if not isinstance(lesson_cfg, Mapping):
        return False
    if lesson_cfg.get("runtime_enabled") is not True:
        return False
    allowlist = lesson_cfg.get("rollout_device_allowlist")
    if not isinstance(allowlist, list):
        return False
    normalized = {
        item.strip().lower()
        for item in allowlist
        if isinstance(item, str) and item.strip()
    }
    if len(normalized) != 1:
        return False
    device_id = str(getattr(conn, "device_id", "") or "").strip().lower()
    return bool(device_id) and device_id in normalized


def runtime_rollout_allows_device(conn: Any) -> bool:
    """Require explicit single-device production rollout admission."""
    return lesson_runtime_config_enabled(conn)


def sample_lesson_enabled(conn: Any) -> bool:
    checker = getattr(conn, "_sample_lesson_enabled", None)
    if callable(checker):
        try:
            configured = bool(checker())
        except Exception:
            return False
        return configured and sample_rollout_allows_device(conn)
    return sample_lesson_config_enabled(conn)


def sample_lesson_config_enabled(conn: Any) -> bool:
    config = getattr(conn, "config", None)
    lesson_cfg = config.get("lesson", {}) if isinstance(config, Mapping) else {}
    if not isinstance(lesson_cfg, Mapping):
        return False
    return lesson_cfg.get("sample_lesson") is True and sample_rollout_allows_device(conn)


def sample_rollout_allows_device(conn: Any) -> bool:
    config = getattr(conn, "config", None)
    lesson_cfg = config.get("lesson", {}) if isinstance(config, Mapping) else {}
    if not isinstance(lesson_cfg, Mapping):
        return False
    allowlist = lesson_cfg.get("rollout_device_allowlist")
    if not isinstance(allowlist, list):
        return False
    normalized = {
        item.strip().lower()
        for item in allowlist
        if isinstance(item, str) and item.strip()
    }
    if len(normalized) != 1:
        return False
    device_id = str(getattr(conn, "device_id", "") or "").strip().lower()
    return bool(device_id) and device_id in normalized


def _configured_child_tools(conn: Any) -> list[str]:
    config = getattr(conn, "config", None)
    if not isinstance(config, Mapping):
        return []
    configured = _configured_function_names(config)
    return [name for name in configured if name in CONFIGURABLE_CHILD_TOOLS]


def _configured_function_names(config: Mapping[str, Any]) -> list[str]:
    intent_root = config.get("Intent")
    if not isinstance(intent_root, Mapping):
        return []

    selected = None
    selected_module = config.get("selected_module")
    if isinstance(selected_module, Mapping):
        selected = selected_module.get("Intent")

    profile = intent_root.get(selected) if selected else None
    if not isinstance(profile, Mapping):
        profile = intent_root.get("function_call")
    if not isinstance(profile, Mapping):
        return []

    raw = profile.get("functions", [])
    if not isinstance(raw, Iterable) or isinstance(raw, (str, bytes)):
        return []
    return [str(name) for name in raw if name]


def _is_child_allowed(name: str) -> bool:
    if name in ADULT_CHILD_DENYLIST:
        return False
    return DANGER_TOOL_NAME_RE.search(name) is None


def _dedupe(names: Iterable[str]) -> list[str]:
    seen = set()
    result = []
    for name in names:
        if name in seen:
            continue
        seen.add(name)
        result.append(name)
    return result
