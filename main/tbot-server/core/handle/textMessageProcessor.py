import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler
    from core.handle.textMessageHandlerRegistry import TextMessageHandlerRegistry

TAG = __name__
MAX_LOG_VALUE_LEN = 256
SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "access_token",
    "authorization",
    "id_token",
    "password",
    "refresh_token",
    "secret",
    "token",
}


def _redact_for_log(value, depth=0):
    if depth > 4:
        return "<truncated>"
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            if str(key).lower() in SENSITIVE_KEYS:
                redacted[key] = "<redacted>"
            else:
                redacted[key] = _redact_for_log(item, depth + 1)
        return redacted
    if isinstance(value, list):
        if len(value) > 8:
            return [_redact_for_log(item, depth + 1) for item in value[:8]] + [
                f"<{len(value) - 8} more items>"
            ]
        return [_redact_for_log(item, depth + 1) for item in value]
    if isinstance(value, str) and len(value) > MAX_LOG_VALUE_LEN:
        return f"{value[:MAX_LOG_VALUE_LEN]}...<truncated {len(value) - MAX_LOG_VALUE_LEN} chars>"
    return value


def _message_log_summary(msg_json):
    if not isinstance(msg_json, dict):
        return str(msg_json)
    summary = {
        "type": msg_json.get("type"),
    }
    payload = msg_json.get("payload")
    if isinstance(payload, dict):
        if any(key in payload for key in ("jsonrpc", "id", "method", "result", "error")):
            summary["payload"] = _redact_for_log(
                {
                    "jsonrpc": payload.get("jsonrpc"),
                    "id": payload.get("id"),
                    "method": payload.get("method"),
                    "result_keys": sorted(payload.get("result", {}).keys())
                    if isinstance(payload.get("result"), dict)
                    else None,
                    "error": payload.get("error"),
                }
            )
        else:
            summary["payload"] = _redact_for_log(payload)
    elif payload is not None:
        summary["payload"] = _redact_for_log(payload)
    return json.dumps(summary, ensure_ascii=False, separators=(",", ":"))


class TextMessageProcessor:
    """Message handler main class"""

    def __init__(self, registry: "TextMessageHandlerRegistry"):
        self.registry = registry

    async def process_message(self, conn: "ConnectionHandler", message: str) -> None:
        """Main entry for handling messages"""
        try:
            # ParseJSONMessage
            msg_json = json.loads(message)

            # ProcessJSONMessage
            if isinstance(msg_json, dict):
                message_type = msg_json.get("type")

                # Record Log
                conn.logger.bind(tag=TAG).info(
                    f"Received {message_type} message: {_message_log_summary(msg_json)}"
                )

                # Get and execute handler
                handler = self.registry.get_handler(message_type)
                if handler:
                    await handler.handle(conn, msg_json)
                else:
                    conn.logger.bind(tag=TAG).error(
                        f"Received unknown message type: {_message_log_summary(msg_json)}"
                    )
            # Process pure digitsMessage
            elif isinstance(msg_json, int):
                conn.logger.bind(tag=TAG).info(f"Received numeric message: {message}")
                await conn.websocket.send(message)

        except json.JSONDecodeError:
            # nonJSONMessageForward Directly
            conn.logger.bind(tag=TAG).error(f"Parsed invalid message: {message}")
            await conn.websocket.send(message)
