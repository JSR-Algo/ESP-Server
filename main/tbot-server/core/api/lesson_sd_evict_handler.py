"""Authenticated internal endpoint for one exact lesson cache eviction."""

from __future__ import annotations

from typing import Any, Dict

from aiohttp import web

from core.api.lesson_nudge_handler import LessonNudgeHandler
from core.lesson.sd_pack_evict import (
    CacheEvictionRefused,
    evict_exact_cache_key,
    parse_firmware_result,
    validate_cache_key,
)

_RESULT_FIELDS = ("cacheKey", "status", "evicted", "notFound", "fileCount", "reason")
_PUBLIC_REFUSAL_CODES = frozenset(
    {
        "voice-busy",
        "lesson-render-busy",
        "protected-active",
        "protected-candidate",
        "protected-preloading",
        "protected-current",
        "protected-previous-known-good",
        "protected-activation-current",
        "protected-activation-previous-known-good",
        "protected-activation-candidate",
        "firmware-timeout",
        "firmware-unknown-tool",
        "firmware-malformed-result",
        "firmware-key-mismatch",
        "firmware-refused",
    }
)


class LessonSdEvictHandler:
    def __init__(self, config: dict, connections: Any):
        self.config = config if isinstance(config, dict) else {}
        self.connections = connections if connections is not None else {}
        self._shared = LessonNudgeHandler(self.config, self.connections)

    async def handle_post(self, request: web.Request) -> web.Response:
        auth_error = self._shared._authorize(request)
        if auth_error is not None:
            return auth_error

        try:
            body = await request.json()
        except Exception:
            return _invalid_request()
        if not isinstance(body, dict) or body.get("cacheKey") is None:
            return _invalid_request()

        try:
            cache_key = validate_cache_key(body.get("cacheKey"))
        except CacheEvictionRefused:
            return _refusal("invalid_cache_key", status=400)

        device_id = request.match_info.get("deviceId", "")
        try:
            result = await evict_exact_cache_key(
                self.connections,
                device_id,
                cache_key,
                find_connection=self._shared._find_connection,
            )
        except CacheEvictionRefused as exc:
            reason = exc.code if exc.code in _PUBLIC_REFUSAL_CODES else "firmware-refused"
            return _refusal(reason, status=409)
        except Exception:
            return _refusal("firmware-refused", status=409)

        if not isinstance(result, dict):
            return _refusal("firmware-refused", status=409)
        normalized: Dict[str, Any] = {name: result.get(name) for name in _RESULT_FIELDS}
        if normalized["status"] == "device-offline":
            if normalized != {
                "cacheKey": cache_key,
                "status": "device-offline",
                "evicted": False,
                "notFound": False,
                "fileCount": 0,
                "reason": "device-offline",
            }:
                return _refusal("firmware-refused", status=409)
            return web.json_response({"data": normalized}, status=202)
        try:
            normalized = parse_firmware_result(cache_key, normalized)
        except CacheEvictionRefused:
            return _refusal("firmware-refused", status=409)
        return web.json_response({"data": normalized}, status=200)


def _invalid_request() -> web.Response:
    return web.json_response(
        {"error": "INVALID_REQUEST", "message": "Body must contain a canonical cacheKey"},
        status=400,
    )


def _refusal(reason: str, *, status: int) -> web.Response:
    return web.json_response(
        {
            "data": {
                "evicted": False,
                "notFound": False,
                "fileCount": 0,
                "reason": reason,
            }
        },
        status=status,
    )
