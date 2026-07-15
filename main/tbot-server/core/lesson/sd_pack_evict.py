"""Fail-closed policy for evicting one exact firmware lesson cache key."""

from __future__ import annotations

import asyncio
import json
import re
from contextlib import suppress
from typing import Any, Awaitable, Callable, Dict, Optional

CACHE_KEY_RE = re.compile(
    r"(?P<slug>[a-z0-9]+(?:-[a-z0-9]+)*)/"
    r"v(?P<version>[1-9][0-9]*)-(?P<checksum>[0-9a-f]{64})"
)
EVICT_TOOL = "self.lesson_assets.evict_cache_key"
EVICT_TIMEOUT_SEC = 30

_BUSY_STATES = frozenset({"PRELOADING", "RUNNING", "PAUSED"})
_RESULT_FIELDS = frozenset({"cacheKey", "status", "evicted", "notFound", "fileCount", "reason"})
_FIRMWARE_REFUSALS = frozenset(
    {
        "invalid_cache_key",
        "lesson_runtime_active",
        "path_mismatch",
        "nested_directory",
        "symlink_rejected",
        "unexpected_node_type",
        "scan_failed",
        "unlink_failed",
        "rmdir_failed",
    }
)


class CacheEvictionRefused(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def validate_cache_key(value: Any) -> str:
    if not isinstance(value, str):
        raise CacheEvictionRefused("invalid_cache_key")
    match = CACHE_KEY_RE.fullmatch(value)
    if match is None:
        raise CacheEvictionRefused("invalid_cache_key")
    reconstructed = "{}/v{}-{}".format(
        match.group("slug"),
        int(match.group("version")),
        match.group("checksum"),
    )
    if reconstructed != value:
        raise CacheEvictionRefused("invalid_cache_key")
    return reconstructed


def protected_cache_keys(conn: Any) -> Dict[str, str]:
    active = getattr(conn, "lesson_runtime", None)
    candidate = getattr(conn, "lesson_runtime_candidate", None)
    activation = getattr(conn, "lesson_sd_pack_activation", None)
    sources = (
        (_runtime_cache_key(active), "protected-active"),
        (_runtime_cache_key(candidate), "protected-candidate"),
        (getattr(conn, "lesson_preloading_cache_key", None), "protected-preloading"),
        (getattr(conn, "lesson_current_cache_key", None), "protected-current"),
        (
            getattr(conn, "lesson_previous_known_good_cache_key", None),
            "protected-previous-known-good",
        ),
        (
            getattr(activation, "current_cache_key", None),
            "protected-activation-current",
        ),
        (
            getattr(activation, "previous_known_good_cache_key", None),
            "protected-activation-previous-known-good",
        ),
        (
            getattr(activation, "candidate_cache_key", None),
            "protected-activation-candidate",
        ),
    )
    protected: Dict[str, str] = {}
    for cache_key, reason in sources:
        if isinstance(cache_key, str) and cache_key and cache_key not in protected:
            protected[cache_key] = reason
    return protected


def parse_firmware_result(expected_key: str, raw: Any) -> Dict[str, Any]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            raise CacheEvictionRefused("firmware-malformed-result") from None
    if not isinstance(raw, dict) or set(raw) != _RESULT_FIELDS:
        raise CacheEvictionRefused("firmware-malformed-result")

    cache_key = raw.get("cacheKey")
    status = raw.get("status")
    evicted = raw.get("evicted")
    not_found = raw.get("notFound")
    file_count = raw.get("fileCount")
    reason = raw.get("reason")
    if (
        not isinstance(cache_key, str)
        or not isinstance(status, str)
        or type(evicted) is not bool
        or type(not_found) is not bool
        or type(file_count) is not int
        or file_count < 0
        or not isinstance(reason, str)
    ):
        raise CacheEvictionRefused("firmware-malformed-result")
    if cache_key != expected_key:
        raise CacheEvictionRefused("firmware-key-mismatch")
    if status in _FIRMWARE_REFUSALS:
        raise CacheEvictionRefused("firmware-refused")

    coherent_evicted = status == "evicted" and evicted is True and not_found is False and reason == "evicted"
    coherent_not_found = (
        status == "not_found" and evicted is False and not_found is True and file_count == 0 and reason == "not_found"
    )
    if not coherent_evicted and not coherent_not_found:
        raise CacheEvictionRefused("firmware-malformed-result")
    return {
        "cacheKey": cache_key,
        "status": status,
        "evicted": evicted,
        "notFound": not_found,
        "fileCount": file_count,
        "reason": reason,
    }


async def evict_exact_cache_key(
    connections: Any,
    device_id: str,
    requested_cache_key: Any,
    *,
    find_connection: Callable[[str], Awaitable[Any]],
    raw_mcp_call: Optional[Callable[..., Awaitable[Any]]] = None,
) -> Dict[str, Any]:
    del connections  # Resolution stays delegated to the authenticated handler policy.
    cache_key = validate_cache_key(requested_cache_key)
    conn = await find_connection(device_id)
    if conn is None:
        return {
            "cacheKey": cache_key,
            "status": "device-offline",
            "evicted": False,
            "notFound": False,
            "fileCount": 0,
            "reason": "device-offline",
        }

    if _voice_busy(conn):
        _log(conn, "warning", cache_key, "voice-busy")
        raise CacheEvictionRefused("voice-busy")
    if _lesson_render_busy(conn):
        _log(conn, "warning", cache_key, "lesson-render-busy")
        raise CacheEvictionRefused("lesson-render-busy")
    protected_reason = protected_cache_keys(conn).get(cache_key)
    if protected_reason is not None:
        _log(conn, "warning", cache_key, protected_reason)
        raise CacheEvictionRefused(protected_reason)

    mcp_client = getattr(conn, "mcp_client", None)
    if mcp_client is None:
        _log(conn, "warning", cache_key, "firmware-refused")
        raise CacheEvictionRefused("firmware-refused")
    if raw_mcp_call is None:
        from core.api.device_mcp_admin_handler import _call_raw_mcp_tool

        raw_mcp_call = _call_raw_mcp_tool

    try:
        raw = await raw_mcp_call(
            conn,
            mcp_client,
            EVICT_TOOL,
            {"cacheKey": cache_key},
            timeout=EVICT_TIMEOUT_SEC,
        )
        result = parse_firmware_result(cache_key, raw)
    except (asyncio.TimeoutError, TimeoutError):
        _log(conn, "warning", cache_key, "firmware-timeout")
        raise CacheEvictionRefused("firmware-timeout") from None
    except CacheEvictionRefused as exc:
        _log(conn, "warning", cache_key, exc.code)
        raise
    except Exception as exc:
        code = "firmware-unknown-tool" if "unknown tool" in str(exc).lower() else "firmware-refused"
        _log(conn, "warning", cache_key, code)
        raise CacheEvictionRefused(code) from None

    _log(conn, "info", cache_key, result["status"], result["fileCount"])
    return result


def _runtime_cache_key(runtime: Any) -> Any:
    asset_cache = getattr(runtime, "asset_cache", None)
    return getattr(asset_cache, "cache_key", None)


def _voice_busy(conn: Any) -> bool:
    check = getattr(conn, "is_realtime_busy", None)
    if not callable(check):
        return False
    try:
        return bool(check())
    except Exception:
        return True


def _lesson_render_busy(conn: Any) -> bool:
    return any(
        getattr(getattr(conn, name, None), "state", None) in _BUSY_STATES
        for name in ("lesson_runtime", "lesson_runtime_candidate")
    )


def _log(conn: Any, level: str, cache_key: str, code: str, file_count: Optional[int] = None) -> None:
    logger = getattr(conn, "logger", None)
    if logger is None:
        return
    message = f"lesson_cache_evict cache_key={cache_key} code={code}"
    if file_count is not None:
        message += f" file_count={file_count}"
    with suppress(Exception):
        getattr(logger.bind(tag="LessonSdPackEvict"), level)(message)
