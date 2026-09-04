import asyncio
import json
import uuid

from aiohttp import web

from core.api.lesson_nudge_handler import LessonNudgeHandler


class RemoteUnpairHandler:
    """Deliver the single fixed reset command to the current robot socket."""

    def __init__(self, config: dict, connections, ack_timeout: float = 3.0):
        self.connections = connections
        self._connection_finder = LessonNudgeHandler(config, connections)
        self._ack_timeout = ack_timeout
        self._pending_acks = {}

    async def handle_post(self, request: web.Request) -> web.Response:
        return await self._deliver_system_command(request, "unpair")

    async def handle_wifi_setup_post(self, request: web.Request) -> web.Response:
        return await self._deliver_system_command(request, "wifi_setup")

    async def _deliver_system_command(
        self, request: web.Request, command: str
    ) -> web.Response:
        auth_error = self._connection_finder._authorize(request)
        if auth_error is not None:
            return auth_error

        device_id = request.match_info.get("deviceId", "")
        connection = await self._connection_finder._find_connection(device_id)
        if connection is None or not self._is_current(connection):
            return self._offline_response()

        websocket = getattr(connection, "websocket", None)
        send = getattr(websocket, "send", None)
        if not callable(send):
            return self._offline_response()

        request_id = uuid.uuid4().hex if command == "unpair" else None
        if request_id:
            self._pending_acks[device_id] = (request_id, connection, asyncio.get_running_loop().create_future())

        payload = {"type": "system", "command": command}
        if request_id:
            payload["request_id"] = request_id

        try:
            await send(
                json.dumps(payload, separators=(",", ":"))
            )
        except Exception:
            self._pending_acks.pop(device_id, None)
            return self._offline_response()

        if request_id:
            pending = self._pending_acks[device_id]
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
            finally:
                if self._pending_acks.get(device_id) is pending:
                    self._pending_acks.pop(device_id, None)
            if not acknowledged:
                return self._offline_response()

        return web.json_response({"data": {"delivered": True}}, status=202)

    async def acknowledge(self, device_id: str, connection, request_id: str) -> bool:
        """Resolve an unpair acknowledgement from the current robot socket."""
        pending = self._pending_acks.get(device_id)
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
