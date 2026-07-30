"""Authenticated internal trigger for the aggregate generation poller."""

from __future__ import annotations

import re
from collections.abc import Mapping

from aiohttp import web

from core.api.lesson_nudge_handler import LessonNudgeHandler

_SAFE_ERROR_CODE = re.compile(r"^[a-z0-9_]{1,64}$")


class GenerationRetryHandler:
    def __init__(self, generation_poller):
        self.generation_poller = generation_poller
        self._shared = LessonNudgeHandler({}, {})

    async def handle_post(self, request: web.Request) -> web.Response:
        auth_error = self._shared._authorize(request)
        if auth_error is not None:
            return auth_error

        poller = self.generation_poller
        trigger_retry = getattr(poller, "trigger_retry", None)
        if not callable(trigger_retry):
            return _rejected("generation_poller_unavailable")

        try:
            result = await trigger_retry()
        except Exception:
            return _rejected("generation_retry_failed")

        normalized = _strict_result(result)
        if normalized is None:
            return _rejected("generation_retry_invalid_result")
        return web.json_response(normalized, status=200)


def _strict_result(result):
    if not isinstance(result, Mapping):
        return None
    keys = set(result.keys())
    state = result.get("state")
    if state in {"accepted", "not_modified"} and keys == {"state"}:
        return {"state": state}
    error_code = result.get("errorCode")
    if (
        state == "rejected"
        and keys == {"state", "errorCode"}
        and isinstance(error_code, str)
        and _SAFE_ERROR_CODE.fullmatch(error_code)
    ):
        return {"state": "rejected", "errorCode": error_code}
    return None


def _rejected(error_code: str) -> web.Response:
    return web.json_response(
        {"state": "rejected", "errorCode": error_code},
        status=200,
    )
