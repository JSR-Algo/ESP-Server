from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class InteractionState(str, Enum):
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    USER_STREAMING = "USER_STREAMING"
    WAITING_MODEL = "WAITING_MODEL"
    MODEL_SPEAKING = "MODEL_SPEAKING"
    MUSIC_PLAYING = "MUSIC_PLAYING"
    INTERRUPTING = "INTERRUPTING"
    RECONNECTING = "RECONNECTING"
    FALLBACK = "FALLBACK"
    MUTED = "MUTED"


@dataclass
class InteractionIdentity:
    device_id: str | None
    client_id: str | None
    server_session_id: str | None
    live_connection_id: str | None
    turn_id: int
    response_id: int
    audio_seq: int
    state: str
    lesson_session_id: str | None = None
    lesson_turn_sequence_id: int = 0
    lesson_attempt_id: str | None = None
    lesson_step_key: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "client_id": self.client_id,
            "server_session_id": self.server_session_id,
            "live_connection_id": self.live_connection_id,
            "turn_id": self.turn_id,
            "response_id": self.response_id,
            "audio_seq": self.audio_seq,
            "state": self.state,
            "lesson_session_id": self.lesson_session_id,
            "lesson_turn_sequence_id": self.lesson_turn_sequence_id,
            "lesson_attempt_id": self.lesson_attempt_id,
            "lesson_step_key": self.lesson_step_key,
        }


class GoogleLiveInteractionController:
    """Server-owned turn/state identity for Google Live sessions."""

    def __init__(self, conn, live_connection_id: str | None = None):
        self.conn = conn
        self.state = InteractionState.IDLE
        self.turn_id = 0
        self.response_id = 0
        self.audio_seq = 0
        self.live_connection_id = live_connection_id
        self.cancelled_response_ids: set[int] = set()
        self.lesson_session_id = None
        self.lesson_turn_sequence_id = 0
        self.lesson_attempt_id = None
        self.lesson_step_key = None

    def start_live_connection(self, live_connection_id: str | int | None):
        self.live_connection_id = None if live_connection_id is None else str(live_connection_id)
        self.transition(InteractionState.LISTENING)

    def transition(self, state: InteractionState | str):
        self.state = state if isinstance(state, InteractionState) else InteractionState(str(state))
        return self.state

    def begin_turn(self, reason: str | None = None):
        self.turn_id += 1
        self.response_id += 1
        self.transition(InteractionState.LISTENING)
        return self.snapshot(reason=reason)

    def begin_interrupt(self, reason: str | None = None, turn_id: int | None = None, response_id: int | None = None):
        previous_response_id = self.response_id
        if response_id is None:
            self.response_id += 1
        else:
            self.response_id = int(response_id)
        if turn_id is None:
            self.turn_id = max(self.turn_id + 1, self.response_id)
        else:
            self.turn_id = int(turn_id)
        self.cancelled_response_ids.add(previous_response_id)
        if self.lesson_session_id is not None:
            self.lesson_turn_sequence_id += 1
        self.transition(InteractionState.INTERRUPTING)
        return self.snapshot(reason=reason)

    def bind_lesson_attempt(self, *, lesson_session_id, attempt_id, step_key, turn_sequence_id=1):
        self.lesson_session_id = str(lesson_session_id)
        self.lesson_attempt_id = str(attempt_id)
        self.lesson_step_key = str(step_key)
        self.lesson_turn_sequence_id = int(turn_sequence_id)
        return self.snapshot(reason="lesson_attempt")

    def next_audio_identity(self, state: InteractionState | str | None = None):
        if state is not None:
            self.transition(state)
        self.audio_seq += 1
        return self.identity().as_dict()

    def snapshot(self, reason: str | None = None):
        data = self.identity().as_dict()
        if reason is not None:
            data["reason"] = reason
        return data

    def is_stale_response(self, response_id: int | None):
        return response_id is not None and (
            response_id != self.response_id or response_id in self.cancelled_response_ids
        )

    def identity(self):
        return InteractionIdentity(
            device_id=getattr(self.conn, "device_id", None),
            client_id=getattr(self.conn, "client_id", None),
            server_session_id=getattr(self.conn, "session_id", None),
            live_connection_id=self.live_connection_id,
            turn_id=self.turn_id,
            response_id=self.response_id,
            audio_seq=self.audio_seq,
            state=self.state.value,
            lesson_session_id=self.lesson_session_id,
            lesson_turn_sequence_id=self.lesson_turn_sequence_id,
            lesson_attempt_id=self.lesson_attempt_id,
            lesson_step_key=self.lesson_step_key,
        )
