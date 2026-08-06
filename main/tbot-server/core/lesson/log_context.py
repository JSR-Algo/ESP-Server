"""Shared lesson log correlation (T6.2).

Every lesson log line emitted while a lesson session exists must carry
``assignment_id`` and ``session_id`` so one child's run can be reconstructed from
the ESP log alone and joined against the backend's ``progress_events``.

Before T6.2 this suffix was implemented three separate times — ``LessonRuntime.
_with_log_context``, the module-level ``_log`` in ``maybe_start_lesson_on_connect``,
and ``forwarder._with_lesson_log_context`` — each reading correlation ids from a
different shape, and every lesson log line outside those three helpers carried
neither id. This module is the single implementation; the three callers delegate
to it.

Scope note: some lesson log lines have no session by construction — SD-pack
fanout / GC / retry workers are device+cache-key scoped background jobs, and
nudge identity resolution runs before an assignment exists. Those carry
``device_id`` instead; see ``with_lesson_log_context(device_id=...)``.
"""

from __future__ import annotations

from typing import Any, Optional

_ASSIGNMENT_KEYS = ("assignmentId", "assignment_id")
_SESSION_KEYS = ("sessionId", "session_id")
_DEVICE_KEYS = ("deviceId", "device_id")


def _clean(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _from_source(source: Any, keys: tuple) -> Optional[str]:
    """Read the first present id from a dict (wire batch) or an object (conn /
    runtime). Never raises — a log helper must not be able to break a lesson."""
    if source is None:
        return None
    for key in keys:
        try:
            if isinstance(source, dict):
                found = _clean(source.get(key))
            else:
                found = _clean(getattr(source, key, None))
        except Exception:
            found = None
        if found:
            return found
    return None


def lesson_log_context(
    source: Any = None,
    *,
    assignment_id: Any = None,
    session_id: Any = None,
    device_id: Any = None,
) -> str:
    """Render the ``k=v`` correlation suffix (no leading space, may be empty).

    Explicit keyword ids win over anything resolved from ``source``.
    """
    assignment = _clean(assignment_id) or _from_source(source, _ASSIGNMENT_KEYS)
    session = _clean(session_id) or _from_source(source, _SESSION_KEYS)
    device = _clean(device_id) or _from_source(source, _DEVICE_KEYS)

    # A lesson runtime hanging off a connection holds the ids the connection
    # itself does not — follow that one hop rather than making every caller do it.
    if source is not None and not isinstance(source, dict) and not (assignment and session):
        try:
            runtime = getattr(source, "lesson_runtime", None)
        except Exception:
            runtime = None
        if runtime is not None:
            assignment = assignment or _from_source(runtime, _ASSIGNMENT_KEYS)
            session = session or _from_source(runtime, _SESSION_KEYS)

    fields = []
    if assignment:
        fields.append(f"assignment_id={assignment}")
    if session:
        fields.append(f"session_id={session}")
    # device_id is the fallback correlation key for session-less lesson work; it
    # is never a substitute when a session is known.
    if device and not (assignment or session):
        fields.append(f"device_id={device}")
    return " ".join(fields)


def with_lesson_log_context(
    message: str,
    source: Any = None,
    *,
    assignment_id: Any = None,
    session_id: Any = None,
    device_id: Any = None,
) -> str:
    """Append the correlation suffix to ``message``, skipping any id the message
    already spells out itself (several call sites format their own)."""
    suffix = lesson_log_context(
        source,
        assignment_id=assignment_id,
        session_id=session_id,
        device_id=device_id,
    )
    if not suffix:
        return message
    kept = [
        field
        for field in suffix.split(" ")
        if field.split("=", 1)[0] + "=" not in message
    ]
    return f"{message} {' '.join(kept)}" if kept else message
