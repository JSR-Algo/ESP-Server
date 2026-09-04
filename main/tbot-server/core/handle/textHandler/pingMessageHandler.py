import json
import re
import time
from typing import Dict, Any

from core.handle.textMessageHandler import TextMessageHandler
from core.handle.textMessageType import TextMessageType

TAG = __name__
SAFE_EVIDENCE_STEP_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


class PingMessageHandler(TextMessageHandler):
    """Ping message handler for keeping WebSocket connection alive"""

    @property
    def message_type(self) -> TextMessageType:
        return TextMessageType.PING

    async def handle(self, conn, msg_json: Dict[str, Any]) -> None:
        """
        Handle PING message, send PONG response
        Message format: {"type": "ping"}
        Args:
            conn: WebSocket connection object
            msg_json: JSON data of PING message
        """
        # CheckEnabledcompletedWebSocketHeartbeat Feature
        enable_websocket_ping = conn.config.get("enable_websocket_ping", True)
        if not enable_websocket_ping:
            conn.logger.debug(f"WebSocket heartbeat not enabled, ignoring PING message")
            return

        try:
            conn.logger.debug(f"Received PING message, send PONG response")
            conn.last_activity_time = time.time() * 1000
            journey_id = getattr(conn, "google_live_evidence_journey_id", None)
            lesson_runtime = getattr(conn, "lesson_runtime", None)
            step_id = getattr(lesson_runtime, "_step_id", None)
            if (
                isinstance(journey_id, str)
                and journey_id
                and isinstance(step_id, str)
                and SAFE_EVIDENCE_STEP_RE.fullmatch(step_id)
            ):
                marker_key = (journey_id, str(step_id))
                if getattr(conn, "_google_live_evidence_ping_marker", None) != marker_key:
                    conn._google_live_evidence_ping_marker = marker_key
                    conn.logger.info(
                        "Google Live firmware_ping journey_id={} connection_id={} "
                        "live_connection_id={} lesson_step={}",
                        journey_id,
                        str(getattr(conn, "session_id", "unknown")),
                        str(getattr(conn, "google_live_live_connection_id", "none")),
                        str(step_id),
                    )
            # ConstructPONGResponseMessage
            pong_message = {
                "type": "pong",
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            }

            # SendPONGResponse
            await conn.websocket.send(json.dumps(pong_message))

        except Exception as e:
            conn.logger.error(f"Error handling PING message: {e}")
