"""Process-level lesson runtime counters (T6.2).

Closes the T2.4 finding routed to T6.2: a device connection superseded by a
reconnect, and a socket closed by the lesson peer-silence watchdog, were both
log lines only — so a reconnect storm (the signature failure of a flapping
robot) had no countable signal and nothing to alert on.

These are deliberately tiny: monotonic in-process integers surfaced through the
existing `GET /internal/lesson-runtime/metrics` JSON endpoint, matching how
`forwarder.dropped_events_total` is already reported. The ESP server has no
Prometheus client (see F-T62-01) — this is the metrics surface it does have.

Counters are process-scoped and reset on restart; consumers must treat them as
monotonic-within-a-process, exactly like the existing forwarder counters.
"""

from __future__ import annotations

import threading
from typing import Dict

CONNECTION_SUPERSEDED = "connection.superseded_total"
PEER_SILENCE_CLOSED = "connection.peer_silence_closed_total"

_KNOWN = (CONNECTION_SUPERSEDED, PEER_SILENCE_CLOSED)

_lock = threading.Lock()
_counters: Dict[str, int] = {name: 0 for name in _KNOWN}


def increment(name: str, by: int = 1) -> None:
    """Bump a counter. Never raises — instrumentation must not break a lesson."""
    if by <= 0:
        return
    try:
        with _lock:
            _counters[name] = _counters.get(name, 0) + by
    except Exception:  # pragma: no cover - defensive
        pass


def snapshot() -> Dict[str, int]:
    """All counters, including the ones never incremented.

    Reporting known-but-zero counters is the point: an absent key and a genuinely
    quiet counter must not look the same to whatever scrapes this.
    """
    with _lock:
        merged = {name: 0 for name in _KNOWN}
        merged.update(_counters)
        return merged


def reset() -> None:
    """Test-only."""
    with _lock:
        _counters.clear()
        _counters.update({name: 0 for name in _KNOWN})
