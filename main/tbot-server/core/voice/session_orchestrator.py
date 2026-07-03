from __future__ import annotations

from enum import Enum


class SessionMode(str, Enum):
    DORMANT = "DORMANT"
    CONVERSATION = "CONVERSATION"
    LESSON = "LESSON"


def normalize_session_mode(value) -> SessionMode:
    if isinstance(value, SessionMode):
        return value
    return SessionMode(str(value))
