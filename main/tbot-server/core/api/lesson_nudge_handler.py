import hmac
import os

from aiohttp import web


def _conn_mac(conn, fallback_key=""):
    mac = getattr(conn, "device_id", None)
    if mac:
        return mac
    headers = getattr(conn, "headers", None) or {}
    return headers.get("device-id") or fallback_key


def _conn_base_url(conn):
    config = getattr(conn, "config", {}) or {}
    lesson_cfg = config.get("lesson", {}) or {}
    server_cfg = config.get("server", {}) or {}
    return lesson_cfg.get("api_base") or server_cfg.get("api_url")


class LessonNudgeHandler:
    def __init__(self, config: dict, connections):
        self.config = config
        self.connections = connections

    def _authorize(self, request: web.Request):
        expected = os.environ.get("TBOT_DEVICE_MINT_SECRET", "")
        if not expected:
            return web.json_response(
                {"error": "MINT_SECRET_NOT_CONFIGURED", "message": "Device token minting is not configured"},
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

        device_id = request.match_info.get("deviceId", "")
        conn = await self._find_connection(device_id)
        if conn is None:
            return web.json_response(
                {"data": {"nudged": False, "reason": "device-offline"}},
                status=202,
            )

        # When the built-in sample demo is enabled, the nudge opens the SAMPLE lesson on
        # the connected device (ignoring any backend assignment), matching the spoken
        # start_lesson trigger. Otherwise it re-pulls the device's assigned lesson.
        sample_check = getattr(conn, "_sample_lesson_enabled", None)
        if callable(sample_check) and sample_check():
            from core.lesson.sample import start_sample_lesson

            await start_sample_lesson(conn)
            return web.json_response({"data": {"nudged": True, "mode": "sample"}}, status=202)

        from core.lesson.runtime import maybe_start_lesson_on_connect

        await maybe_start_lesson_on_connect(conn)
        return web.json_response({"data": {"nudged": True}}, status=202)

    async def handle_child_response_post(self, request: web.Request) -> web.Response:
        auth_error = self._authorize(request)
        if auth_error is not None:
            return auth_error

        device_id = request.match_info.get("deviceId", "")
        conn = await self._find_connection(device_id)
        if conn is None:
            return web.json_response(
                {"data": {"handled": False, "reason": "device-offline"}},
                status=202,
            )

        try:
            body = await request.json()
        except Exception:
            body = {}
        text = str((body or {}).get("text") or "").strip()
        if not text:
            return web.json_response(
                {"error": "TEXT_REQUIRED", "message": "Body field 'text' is required"},
                status=400,
            )

        runtime = getattr(conn, "lesson_runtime", None)
        responder = getattr(runtime, "on_child_response", None)
        if not callable(responder):
            return web.json_response(
                {"data": {"handled": False, "reason": "no-active-lesson"}},
                status=202,
            )

        handled = bool(await responder(text, source="internal_dev_endpoint"))
        return web.json_response({"data": {"handled": handled}}, status=202)

    async def _find_connection(self, device_id):
        if self.connections is None:
            return None
        conn = self.connections.get(device_id)
        if conn is not None:
            return conn

        # WebSocket identity is the robot MAC; the backend nudge route carries
        # the backend device UUID. Use the same D-RUNTOKEN mint bridge to match
        # active MAC connections without adding lesson payload authority here.
        try:
            import httpx
            from config.device_token_client import resolve_device_identity
        except Exception:
            return None

        for key, candidate in list(self.connections.items()):
            mac = _conn_mac(candidate, key)
            base_url = _conn_base_url(candidate)
            if not mac or not base_url:
                continue
            try:
                async with httpx.AsyncClient() as client:
                    minted_uuid, _ = await resolve_device_identity(
                        client,
                        base_url,
                        mac,
                        logger=getattr(candidate, "logger", None),
                    )
            except Exception as exc:
                logger = getattr(candidate, "logger", None)
                if logger is not None:
                    try:
                        logger.warning("lesson nudge identity resolution failed for %s: %s", mac, exc)
                    except Exception:
                        pass
                continue
            if minted_uuid == device_id:
                return candidate
        return None
