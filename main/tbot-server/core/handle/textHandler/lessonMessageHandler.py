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
    if method == "on_lesson_ack":
        accept_reset = getattr(conn, "_accept_lesson_preload_reset_ack", None)
        if callable(accept_reset) and accept_reset(msg_json):
            return
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
    # ADDITIVE-ONLY ISOLATION: the lesson layer must NEVER break voice. An exception
    # raised inside a lesson_* handler would propagate up through process_message
    # (which only catches json.JSONDecodeError) into the connection receive loop,
    # tearing down the whole WS connection — and the realtime voice session with it.
    # Swallow + log here so a lesson fault degrades the LESSON ONLY; voice survives.
    try:
        await handler(msg_json)
    except Exception:
        logger = getattr(conn, "logger", None)
        if logger is not None:
            try:
                logger.bind(tag=TAG).warning(
                    f"lesson {method} handler raised; degrading lesson only "
                    f"(type={msg_json.get('type')})",
                    exc_info=True,
                )
            except Exception:
                pass


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
