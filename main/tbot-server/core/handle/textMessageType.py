from enum import Enum


class TextMessageType(Enum):
    """Message type enum"""
    HELLO = "hello"
    ABORT = "abort"
    LISTEN = "listen"
    IOT = "iot"
    MCP = "mcp"
    SERVER = "server"
    PING = "ping"
    # US-006 lesson runtime inbound frames (F->S). Additive; the firmware sends
    # these after it advertised lesson capability. Outbound lesson_* frames
    # (prepare/start/step/stop) are built by LessonRuntime, not dispatched here.
    LESSON_ACK = "lesson_ack"
    LESSON_PROGRESS = "lesson_progress"
    LESSON_ERROR = "lesson_error"
    TTS_ACK = "tts_ack"
