import hmac
import ipaddress
import inspect
import os

from aiohttp import web


def _conn_mac(conn, fallback_key=""):
    mac = getattr(conn, "device_id", None)
    if mac:
        return mac
    headers = getattr(conn, "headers", None) or {}
    return headers.get("device-id") or fallback_key


def _config_block(config, key):
    if not isinstance(config, dict):
        return {}
    block = config.get(key, {}) or {}
    return block if isinstance(block, dict) else {}


def _conn_base_url(conn):
    config = getattr(conn, "config", {}) or {}
    lesson_cfg = _config_block(config, "lesson")
    server_cfg = _config_block(config, "server")
    return lesson_cfg.get("api_base") or server_cfg.get("api_url")


def _is_loopback_endpoint(value):
    value = str(value or "").strip().lower()
    if not value:
        return False
    if value.startswith("["):
        host = value[1:value.find("]")] if "]" in value else value.strip("[]")
    elif value.count(":") == 1:
        host = value.rsplit(":", 1)[0]
    else:
        host = value
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


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

    def _local_sample_demo_bypass(self, request: web.Request):
        if os.environ.get("TBOT_LOCAL_SAMPLE_DEMO_BYPASS") != "1":
            return False, None
        if request.headers.get("X-TBOT-Local-Sample-Demo") != "1":
            return False, None

        host = getattr(request, "host", "") or request.headers.get("Host", "")
        remote = getattr(request, "remote", "")
        if _is_loopback_endpoint(host) and _is_loopback_endpoint(remote):
            return True, None

        return False, web.json_response(
            {
                "error": "LOCAL_SAMPLE_DEMO_FORBIDDEN",
                "message": "Local sample demo bypass is limited to loopback requests",
            },
            status=403,
        )

    async def handle_post(self, request: web.Request) -> web.Response:
        local_sample_demo, auth_error = self._local_sample_demo_bypass(request)
        if auth_error is not None:
            return auth_error
        if not local_sample_demo:
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
        sample_enabled = callable(sample_check) and sample_check()
        if local_sample_demo and not sample_enabled:
            return web.json_response(
                {
                    "error": "LOCAL_SAMPLE_DEMO_ONLY",
                    "message": "Local sample demo bypass can only start the built-in sample lesson",
                },
                status=403,
            )

        if sample_enabled:
            from core.lesson.sample import start_sample_lesson

            runtime = await start_sample_lesson(conn)
            if runtime is None:
                status = getattr(conn, "lesson_start_status", None) or {}
                reason = status.get("code") or "sample-start-refused"
                message = status.get("message")
                data = {"nudged": False, "mode": "sample", "reason": reason}
                if message:
                    data["message"] = message
                return web.json_response({"data": data}, status=202)
            return web.json_response({"data": {"nudged": True, "mode": "sample"}}, status=202)

        from core.lesson.runtime import maybe_start_lesson_on_connect

        transition = getattr(conn, "transition_to_lesson_start", None)
        if callable(transition) and not await transition():
            return web.json_response(
                {
                    "data": {
                        "nudged": False,
                        "reason": "live-transition-timeout",
                    }
                },
                status=202,
            )
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

        await self._stop_live_output_before_internal_response(conn)
        handled = bool(await responder(text, source="internal_dev_endpoint"))
        return web.json_response({"data": {"handled": handled}}, status=202)

    async def _stop_live_output_before_internal_response(self, conn):
        provider = getattr(conn, "voice_provider", None)
        bridge = getattr(provider, "_bridge", None)
        stop_output = getattr(bridge, "stop_output", None)
        if not callable(stop_output):
            return
        try:
            result = stop_output()
            if inspect.isawaitable(result):
                await result
        except Exception:
            return

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
