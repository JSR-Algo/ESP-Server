import asyncio
import json
import uuid

from aiohttp import web

from config.logger import setup_logging
from core.api.lesson_nudge_handler import LessonNudgeHandler

logger = setup_logging()


class RemoteUnpairHandler:
    """Deliver the single fixed reset command to the current robot socket."""

    def __init__(self, config: dict, connections, ack_timeout: float = 3.0,
                 readiness_timeout: float = 15.0, delivery_timeout: float = 20.0):
        self.connections = connections
        self._connection_finder = LessonNudgeHandler(config, connections)
        self._ack_timeout = ack_timeout
        self._pending_acks = {}
        self._readiness_timeout = readiness_timeout
        self._delivery_timeout = delivery_timeout

    async def handle_post(self, request: web.Request) -> web.Response:
        # Keep lookup, liveness, send and ACK inside the backend's 22-second deadline.
        try:
            return await asyncio.wait_for(
                self._deliver_system_command(request, "unpair"), self._delivery_timeout
            )
        except asyncio.TimeoutError:
            return web.json_response({"error": "DEVICE_UNPAIR_ACK_TIMEOUT",
                                      "message": "Robot did not confirm the unpair request"}, status=504)

    async def handle_wifi_setup_post(self, request: web.Request) -> web.Response:
        return await self._deliver_system_command(request, "wifi_setup")

    async def _deliver_system_command(
        self, request: web.Request, command: str
    ) -> web.Response:
        auth_error = self._connection_finder._authorize(request)
        if auth_error is not None:
            return auth_error

        device_id = request.match_info.get("deviceId", "")
        if command == "unpair":
            connection = await self._find_responsive_connection(device_id)
        else:
            # Wi-Fi setup retains its existing short producer timeout.
            for attempt in range(3):
                connection = await self._connection_finder._find_connection(device_id)
                if connection is None or self._is_current(connection):
                    break
        if connection is None or not self._is_current(connection):
            logger.bind(tag="RemoteUnpair").info("remote_system command={} stage=no_current_socket", command)
            return self._offline_response()

        websocket = getattr(connection, "websocket", None)
        send = getattr(websocket, "send", None)
        if not callable(send):
            return self._offline_response()

        request_id = uuid.uuid4().hex if command == "unpair" else None
        pending = None
        if request_id:
            # HTTP addresses a backend UUID; WebSocket ACKs identify a MAC.
            # Correlate the opaque request with the exact socket, not either ID.
            pending = (request_id, connection, asyncio.get_running_loop().create_future())
            self._pending_acks[request_id] = pending

        payload = {"type": "system", "command": command}
        if request_id:
            payload["request_id"] = request_id

        try:
            try:
                logger.bind(tag="RemoteUnpair").info("remote_system command={} stage=send_started", command)
                await send(json.dumps(payload, separators=(",", ":")))
            except Exception:
                logger.bind(tag="RemoteUnpair").info("remote_system command={} stage=send_failed", command)
                return self._offline_response()

            if pending is not None:
                try:
                    acknowledged = await asyncio.wait_for(
                        asyncio.shield(pending[2]), timeout=self._ack_timeout
                    )
                except asyncio.TimeoutError:
                    return web.json_response(
                        {
                            "error": "DEVICE_UNPAIR_ACK_TIMEOUT",
                            "message": "Robot did not confirm the unpair request",
                        },
                        status=504,
                    )
                if not acknowledged:
                    logger.bind(tag="RemoteUnpair").info("remote_system command={} stage=socket_lost_after_send", command)
                    return self._offline_response()

            logger.bind(tag="RemoteUnpair").info("remote_system command={} stage=delivered", command)
            return web.json_response({"data": {"delivered": True}}, status=202)
        finally:
            if pending is not None:
                if self._pending_acks.get(request_id) is pending:
                    self._pending_acks.pop(request_id, None)
                if not pending[2].done():
                    pending[2].cancel()

    async def _find_responsive_connection(self, device_id):
        deadline = asyncio.get_running_loop().time() + self._readiness_timeout
        stale_attempts = 0
        while (remaining := deadline - asyncio.get_running_loop().time()) > 0:
            try:
                connection, websocket = await asyncio.wait_for(
                    self._probe_connection(device_id), remaining
                )
                if connection is not None:
                    if self._is_current(connection) and connection.websocket is websocket:
                        return connection
                    stale_attempts += 1
                    logger.bind(tag="RemoteUnpair").info("remote_system command=unpair stage=stale_before_send")
                    if stale_attempts >= 3:
                        return None
                    continue
            except (asyncio.TimeoutError, OSError):
                pass
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining > 0:
                await asyncio.sleep(min(0.1, remaining))
        return None

    async def _probe_connection(self, device_id):
        connection = await self._connection_finder._find_connection(device_id)
        if connection is None:
            return None, None
        websocket = getattr(connection, "websocket", None)
        if not self._is_current(connection):
            return connection, websocket
        ping = getattr(websocket, "ping", None)
        if not callable(ping):
            return None, None
        async def roundtrip():
            pong = await ping()
            await pong
        try:
            await asyncio.wait_for(roundtrip(), timeout=2.0)
        except Exception:
            logger.bind(tag="RemoteUnpair").info("remote_system command=unpair stage=probe_failed")
            return None, None
        return connection, websocket

    async def acknowledge(self, device_id: str, connection, request_id: str) -> bool:
        """Resolve an unpair acknowledgement from the current robot socket."""
        pending = self._pending_acks.get(request_id)
        if pending is None or pending[0] != request_id or pending[1] is not connection:
            return False
        if not self._is_current(connection):
            return False
        future = pending[2]
        if future.done():
            return False
        future.set_result(True)
        return True

    async def cancel_for_connection(self, connection) -> None:
        """Wake requests waiting on a socket that was replaced or disconnected."""
        for pending in tuple(self._pending_acks.values()):
            if pending[1] is connection and not pending[2].done():
                pending[2].set_result(False)

    def _is_current(self, connection) -> bool:
        if self.connections is None:
            return False
        return any(candidate is connection for candidate in self.connections.values())

    @staticmethod
    def _offline_response() -> web.Response:
        return web.json_response(
            {
                "error": "DEVICE_NOT_ONLINE",
                "message": "Robot does not have an active connection",
            },
            status=409,
        )
