"""HTTP admin API: fan-out lesson asset packs to robot SD cards."""

from __future__ import annotations

import hmac
import os

from aiohttp import web

from core.lesson.sd_pack_fanout import fanout_sd_pack_sync, get_pending_store, pending_snapshot


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
        queue_offline = body.get("queueOffline")
        if queue_offline is None:
            queue_offline = body.get("queue_offline", True)

        result = await fanout_sd_pack_sync(
            self.config,
            self.connections,
            lesson_id=str(lesson_id).strip() if lesson_id else None,
            cache_key=str(cache_key).strip() if cache_key else None,
            device_ids=device_ids,
            queue_offline=bool(queue_offline),
            store=self.pending_store,
            online_index=self.online_index,
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
                    "onlineDeviceIds": sorted(
                        str(k) for k in (self.connections or {}) if k
                    ),
                }
            },
            status=200,
        )
