"""Authenticated internal endpoint for verified ESP lesson pack materialization."""

from __future__ import annotations

import json

from aiohttp import web

from core.api.lesson_nudge_handler import LessonNudgeHandler
from core.lesson.sd_pack_materializer import (
    MaterializationError,
    materialize_lesson_sd_pack,
)


class LessonSdMaterializeHandler:
    def __init__(self, config: dict, connections):
        self.config = config if isinstance(config, dict) else {}
        self.connections = connections if connections is not None else {}
        self._shared = LessonNudgeHandler(self.config, self.connections)

    async def handle_post(self, request: web.Request) -> web.Response:
        auth_error = self._shared._authorize(request)
        if auth_error is not None:
            return auth_error

        try:
            body = await request.json()
        except web.HTTPRequestEntityTooLarge:
            return web.json_response(
                {
                    "error": "REQUEST_ENTITY_TOO_LARGE",
                    "message": "Request body is too large",
                    "retryable": False,
                    "details": {},
                },
                status=413,
            )
        except (json.JSONDecodeError, ValueError, TypeError):
            return web.json_response(
                {
                    "error": "INVALID_REQUEST",
                    "message": "Body must be a canonical lesson asset manifest",
                    "retryable": False,
                    "details": {},
                },
                status=400,
            )
        try:
            result = await materialize_lesson_sd_pack(body, config=self.config)
        except MaterializationError as exc:
            return web.json_response(exc.to_response(), status=exc.status)
        status = 200 if result["downloadedCount"] == 0 else 201
        return web.json_response({"data": result}, status=status)
