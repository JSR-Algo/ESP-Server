import json

from aiohttp import web

from core.api.lesson_nudge_handler import LessonNudgeHandler


class RemoteUnpairHandler:
    """Deliver the single fixed reset command to the current robot socket."""

    def __init__(self, config: dict, connections):
        self.connections = connections
        self._connection_finder = LessonNudgeHandler(config, connections)

    async def handle_post(self, request: web.Request) -> web.Response:
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

        try:
            await send(
                json.dumps(
                    {"type": "system", "command": "unpair"},
                    separators=(",", ":"),
                )
            )
        except Exception:
            return self._offline_response()

        return web.json_response({"data": {"delivered": True}}, status=202)

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
