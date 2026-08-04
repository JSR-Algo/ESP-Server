"""HTTP admin API: fan-out lesson asset packs to robot SD cards."""

from __future__ import annotations

import hmac
import os

from aiohttp import web

from core.lesson.sd_pack_fanout import fanout_sd_pack_sync, get_pending_store, pending_snapshot


def _generation_enabled(config: dict) -> bool:
    env_value = os.environ.get("LESSON_GENERATION_CMS_URL")
    if isinstance(env_value, str) and env_value.strip():
        return True
    lesson_cfg = config.get("lesson", {}) if isinstance(config, dict) else {}
    return bool(
        isinstance(lesson_cfg, dict)
        and isinstance(lesson_cfg.get("generation_cms_url"), str)
        and lesson_cfg["generation_cms_url"].strip()
    )


def _production_environment() -> bool:
    value = (
        os.environ.get("ENV")
        or os.environ.get("APP_ENV")
        or os.environ.get("PYTHON_ENV")
        or os.environ.get("NODE_ENV")
        or ""
    )
    return value.strip().lower() in {"prod", "production"}


def _error_response(error: str, message: str, *, status: int) -> web.Response:
    return web.json_response({"error": error, "message": message}, status=status)


class LessonSdFanoutHandler:
    def __init__(self, config: dict, connections, *, pending_store=None, online_index=None):
        self.config = config if isinstance(config, dict) else {}
        self.connections = connections if connections is not None else {}
        self.pending_store = pending_store or get_pending_store()
        self.online_index = online_index

    def _authorize(self, request: web.Request):
        expected = os.environ.get("TBOT_DEVICE_MINT_SECRET", "")
        if not expected:
            return web.json_response(
                {
                    "error": "MINT_SECRET_NOT_CONFIGURED",
                    "message": "Device token minting is not configured",
                },
                status=503,
            )
        provided = request.headers.get("X-Mint-Secret", "")
        if not provided or not hmac.compare_digest(provided, expected):
            return web.json_response(
                {"error": "MINT_AUTH_INVALID", "message": "Invalid X-Mint-Secret"},
                status=401,
            )
        return None

    async def handle_post(self, request: web.Request) -> web.Response:
        auth_error = self._authorize(request)
        if auth_error is not None:
            return auth_error

        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}

        lesson_id = body.get("lessonId") or body.get("lesson_id")
        cache_key = body.get("cacheKey") or body.get("cache_key")
        device_ids = body.get("deviceIds") or body.get("device_ids")
        callback_mode = body.get("callbackMode") or body.get("callback_mode") or "required"
        callback_mode = str(callback_mode).strip().lower()
        if callback_mode not in {"required", "none"}:
            return _error_response(
                "CALLBACK_MODE_INVALID",
                "callbackMode must be 'required' or 'none'",
                status=400,
            )

        normalized_cache_key = str(cache_key).strip() if cache_key else None
        no_callback = callback_mode == "none"
        if _generation_enabled(self.config) and not (no_callback and normalized_cache_key):
            return _error_response(
                "LEGACY_FANOUT_DISABLED",
                "Tracked legacy fanout is disabled while global generation mode is active",
                status=409,
            )
        if (no_callback or _production_environment()) and not normalized_cache_key:
            return _error_response(
                "EXACT_CACHE_KEY_REQUIRED",
                "Production lesson SD fanout requires an exact cacheKey",
                status=400,
            )

        queue_offline = body.get("queueOffline")
        if queue_offline is None:
            queue_offline = body.get("queue_offline")
        if no_callback:
            if queue_offline is not None and bool(queue_offline):
                return _error_response(
                    "QUEUE_OFFLINE_NOT_ALLOWED",
                    "callbackMode 'none' cannot queue offline or retry work",
                    status=400,
                )
            queue_offline = False
        elif queue_offline is None:
            queue_offline = True

        fanout_kwargs = {
            "lesson_id": str(lesson_id).strip() if lesson_id else None,
            "cache_key": normalized_cache_key,
            "device_ids": device_ids,
            "queue_offline": bool(queue_offline),
            "store": self.pending_store,
            "online_index": self.online_index,
            "callback_required": not no_callback,
            "persist_retry": not no_callback,
        }
        result = await fanout_sd_pack_sync(
            self.config,
            self.connections,
            **fanout_kwargs,
        )
        status = 202 if result.get("queued") or result.get("failed") else 200
        if result.get("packs", 0) == 0:
            status = 404
        return web.json_response({"data": result}, status=status)

    async def handle_get_pending(self, request: web.Request) -> web.Response:
        auth_error = self._authorize(request)
        if auth_error is not None:
            return auth_error
        return web.json_response(
            {
                "data": {
                    "pending": await pending_snapshot(self.pending_store),
                    "onlineDeviceIds": await self._online_backend_device_ids(),
                }
            },
            status=200,
        )

    async def _online_backend_device_ids(self) -> list[str]:
        if self.online_index is None:
            return []
        resolve = getattr(self.online_index, "resolve_and_upsert", None)
        if not callable(resolve):
            return []
        resolved = []
        for conn in (self.connections or {}).values():
            try:
                backend_id = await resolve(conn)
            except Exception:
                continue
            if backend_id:
                resolved.append(str(backend_id))
        return sorted(set(resolved))
