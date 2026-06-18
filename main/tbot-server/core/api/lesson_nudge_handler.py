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

    async def handle_post(self, request: web.Request) -> web.Response:
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

        device_id = request.match_info.get("deviceId", "")
        conn = await self._find_connection(device_id)
        if conn is None:
            return web.json_response(
                {"data": {"nudged": False, "reason": "device-offline"}},
                status=202,
            )

        from core.lesson.runtime import maybe_start_lesson_on_connect

        await maybe_start_lesson_on_connect(conn)
        return web.json_response({"data": {"nudged": True}}, status=202)

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
            async with httpx.AsyncClient() as client:
                minted_uuid, _ = await resolve_device_identity(
                    client,
                    base_url,
                    mac,
                    logger=getattr(candidate, "logger", None),
                )
            if minted_uuid == device_id:
                return candidate
        return None
