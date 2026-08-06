"""Authenticated internal endpoint for one exact lesson cache eviction."""

from __future__ import annotations

from contextlib import suppress
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
    def __init__(self, config: dict, connections: Any, *, pending_store: Any = None):
        self.config = config if isinstance(config, dict) else {}
        self.connections = connections if connections is not None else {}
        self._shared = LessonNudgeHandler(self.config, self.connections)
        self._pending_store = pending_store

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
            if not _is_strict_offline_result(cache_key, normalized):
                return _refusal("firmware-refused", status=409)
            return web.json_response({"data": normalized}, status=202)
        try:
            normalized = parse_firmware_result(cache_key, normalized)
        except CacheEvictionRefused:
            return _refusal("firmware-refused", status=409)
        if normalized["status"] == "evicted":
            await self._cancel_pending_retry(device_id, cache_key)
        if normalized["status"] == "partial_evict_recovery_required":
            return web.json_response(
                {
                    "error": "LESSON_CACHE_MAINTENANCE_REQUIRED",
                    "message": (
                        "Retry or repair the exact lesson cache key before creating "
                        "a fresh assignment."
                    ),
                    "data": normalized,
                },
                status=503,
            )
        return web.json_response({"data": normalized}, status=200)


    async def _cancel_pending_retry(self, device_id: str, cache_key: str) -> None:
        """Drop queued fanout work for a key the robot no longer holds.

        Without this the retry worker re-pushes the pack an operator just
        evicted: eviction is the only way to reclaim that SD space, and a
        pending entry silently undoes it on the next drain.
        """
        from core.lesson.sd_pack_fanout import (
            _resolve_backend_device_id_for_conn,
            get_pending_store,
        )

        store = self._pending_store or get_pending_store()
        device_ids = {str(device_id or "").strip()}
        try:
            conn = await self._shared._find_connection(device_id)
        except Exception:
            conn = None
        if conn is not None:
            with suppress(Exception):
                resolved = await _resolve_backend_device_id_for_conn(conn, self.config)
                if resolved:
                    device_ids.add(str(resolved))
        for resolved_id in sorted(key for key in device_ids if key):
            with suppress(Exception):
                await store.clear(resolved_id, [cache_key])
            with suppress(Exception):
                await store.clear_callbacks(resolved_id, [cache_key])


def _invalid_request() -> web.Response:
    return web.json_response(
        {"error": "INVALID_REQUEST", "message": "Body must contain a canonical cacheKey"},
        status=400,
    )


def _is_strict_offline_result(cache_key: str, result: Dict[str, Any]) -> bool:
    return (
        result.get("cacheKey") == cache_key
        and result.get("status") == "device-offline"
        and type(result.get("evicted")) is bool
        and result.get("evicted") is False
        and type(result.get("notFound")) is bool
        and result.get("notFound") is False
        and type(result.get("fileCount")) is int
        and result.get("fileCount") == 0
        and result.get("reason") == "device-offline"
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
