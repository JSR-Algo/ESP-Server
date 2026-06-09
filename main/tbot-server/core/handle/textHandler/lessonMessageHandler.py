"""S6/S8 — inbound ``lesson_ack`` / ``lesson_progress`` / ``lesson_error`` handlers.

The registry maps exactly one ``message_type.value`` -> one handler, so the slice
registers THREE thin handler instances that share one dispatcher reading
``conn.lesson_runtime``. This keeps the registry/processor/ABC contract untouched
and is purely additive — the 7 existing voice/control handlers are not disturbed,
and an unrecognised ``type`` still hits the existing unknown-type no-op.

These frames arrive over the device WS as plain text. They reach this registry via
``handleTextMessage`` ONLY after the voice provider returns falsy for them (it
always does — ``lesson_*`` is none of listen/text/chat/input), so the realtime
voice path is never intercepted.
"""

from __future__ import annotations

from typing import Any, Dict

from core.handle.textMessageHandler import TextMessageHandler
from core.handle.textMessageType import TextMessageType

TAG = __name__


async def _dispatch(conn: Any, msg_json: Dict[str, Any], method: str) -> None:
    runtime = getattr(conn, "lesson_runtime", None)
    if runtime is None:
        # No active lesson session (or lesson runtime disabled) -> safe no-op.
        logger = getattr(conn, "logger", None)
        if logger is not None:
            try:
                logger.bind(tag=TAG).debug(
                    f"Dropped {msg_json.get('type')} with no active lesson runtime"
                )
            except Exception:
                pass
        return
    handler = getattr(runtime, method, None)
    if handler is None:
        return
    await handler(msg_json)


class LessonAckHandler(TextMessageHandler):
    async def handle(self, conn, msg_json: Dict[str, Any]) -> None:
        await _dispatch(conn, msg_json, "on_lesson_ack")

    @property
    def message_type(self) -> TextMessageType:
        return TextMessageType.LESSON_ACK


class LessonProgressHandler(TextMessageHandler):
    async def handle(self, conn, msg_json: Dict[str, Any]) -> None:
        await _dispatch(conn, msg_json, "on_lesson_progress")

    @property
    def message_type(self) -> TextMessageType:
        return TextMessageType.LESSON_PROGRESS


class LessonErrorHandler(TextMessageHandler):
    async def handle(self, conn, msg_json: Dict[str, Any]) -> None:
        await _dispatch(conn, msg_json, "on_lesson_error")

    @property
    def message_type(self) -> TextMessageType:
        return TextMessageType.LESSON_ERROR
