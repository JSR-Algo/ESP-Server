from typing import Any, Dict

from core.handle.textMessageHandler import TextMessageHandler
from core.handle.textMessageType import TextMessageType


class SystemAckMessageHandler(TextMessageHandler):
    """Routes robot acknowledgements to a server-side pending command."""

    @property
    def message_type(self) -> TextMessageType:
        return TextMessageType.SYSTEM_ACK

    async def handle(self, conn, msg_json: Dict[str, Any]) -> None:
        if msg_json.get("command") != "unpair":
            return
        request_id = msg_json.get("request_id")
        if not isinstance(request_id, str) or not request_id.strip():
            return
        server = getattr(conn, "server", None)
        remote_unpair = getattr(server, "remote_unpair_handler", None)
        acknowledge = getattr(remote_unpair, "acknowledge", None)
        if not callable(acknowledge):
            return
        await acknowledge(conn.device_id, conn, request_id.strip())
