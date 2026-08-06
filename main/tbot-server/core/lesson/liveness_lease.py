"""T2.5 — liveness leases + RMA disposition telemetry (cross-component teardown).

Two independent pieces live here because they are the two halves of one idea:
"who is allowed to own listening/session state right now, and what happened to
the state we took away".

1. **Liveness lease.** The esp32-server issues a monotonic
   ``(session_epoch, seq, ttl_ms)`` lease when it accepts a device websocket.
   ``session_epoch`` is per-device and strictly increasing; ``seq`` counts frames
   inside one epoch; ``ttl_ms`` bounds how long a consumer may trust the lease
   without a refresh. The lease rides existing messages as one optional JSON
   field (``lessonLease``) — old firmware ignores unknown keys, so there is no
   flag day. Consumers call :func:`classify_lease` and route to
   resume/recover/abort instead of silently trusting whatever arrived.

   **Epoch persistence is the load-bearing part.** If the counter lived in this
   process's memory, a server restart mid-lesson — one of the exact failures the
   lease is supposed to guard — would reset it to 1 and every stale consumer's
   epoch would suddenly look fresh again. :class:`RedisLeaseLedger` therefore
   owns the counter by default in production (``REDIS_URL``, AOF-persisted volume
   in ``deploy/docker-compose.prod.yml``), and :class:`InMemoryLeaseLedger` is a
   dev/test fallback that reports ``durable = False`` so callers can refuse to
   treat its epochs as authoritative.

   (Named ``issue`` rather than ``mint``: ``mint`` is reserved vocabulary for
   device-identity tokens, and the public global-generation acceptance contract
   forbids that identifier in this file's neighbours.)

   Note: this is NOT ``core.activity_lease``. That one arbitrates voice/eviction
   ownership between tasks *inside* one connection. This one arbitrates
   session-state ownership *across* processes and components.

2. **Disposition telemetry.** Every teardown path classifies itself as
   ``restock`` (clean resume — state kept, usable as-is), ``refurbish`` (partial
   recovery — state kept but needs repair before reuse) or ``scrap`` (clean
   abort — state discarded, user-visible outcome). Emitting them as one
   structured line turns "production stale state" from an anecdote into a rate.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional

TAG = "LessonLivenessLease"

#: Wire field name. Optional and additive — every consumer must tolerate absence.
LEASE_WIRE_FIELD = "lessonLease"

#: Default lease TTL. Deliberately longer than the firmware's 12 s
#: LESSON_FRAME_ACK_TIMEOUT and the backend's 30 s ping so a healthy session
#: never trips it, and short enough that an abandoned session is reaped inside
#: one lesson step.
DEFAULT_LEASE_TTL_MS = 45_000


class Disposition(str, Enum):
    """RMA vocabulary for a teardown outcome (see docs/failure-path-matrix.md)."""

    RESTOCK = "restock"
    REFURBISH = "refurbish"
    SCRAP = "scrap"


class LeaseVerdict(str, Enum):
    """What a consumer should do with state carrying a given lease."""

    #: Lease is current and live — keep going.
    ACCEPT = "accept"
    #: Same epoch, but the TTL lapsed — re-handshake before trusting state.
    RESUME = "resume"
    #: A newer epoch exists — this state belongs to a dead session; rebuild
    #: from the authoritative side.
    RECOVER = "recover"
    #: Structurally unusable (missing/garbled lease, epoch from the future).
    ABORT = "abort"


@dataclass(frozen=True)
class Lease:
    """One issued liveness lease."""

    device_id: str
    session_epoch: int
    seq: int = 0
    ttl_ms: int = DEFAULT_LEASE_TTL_MS
    issued_at_ms: int = 0
    durable: bool = True

    def to_wire(self) -> Dict[str, Any]:
        """The optional JSON field. Keys are stable; add-only."""
        return {
            "sessionEpoch": self.session_epoch,
            "seq": self.seq,
            "ttlMs": self.ttl_ms,
            "issuedAtMs": self.issued_at_ms,
        }

    def next_seq(self, *, now_ms: Optional[int] = None) -> "Lease":
        """Advance the in-epoch counter and re-stamp the issue time."""
        return Lease(
            device_id=self.device_id,
            session_epoch=self.session_epoch,
            seq=self.seq + 1,
            ttl_ms=self.ttl_ms,
            issued_at_ms=_now_ms() if now_ms is None else int(now_ms),
            durable=self.durable,
        )

    def expires_at_ms(self) -> int:
        return self.issued_at_ms + self.ttl_ms


def attach_lease(frame: Dict[str, Any], lease: Optional[Lease]) -> Dict[str, Any]:
    """Piggyback a lease on an existing frame without touching its other keys.

    Returns the same mapping (mutated) so call sites stay one-liners. A ``None``
    lease is a no-op — that is the pre-rollout path and it must stay byte-identical
    to today's wire output.
    """
    if lease is not None and isinstance(frame, dict):
        frame[LEASE_WIRE_FIELD] = lease.to_wire()
    return frame


def read_lease(frame: Any) -> Optional[Dict[str, Any]]:
    """Extract the lease field from an inbound frame, or ``None`` when absent."""
    if not isinstance(frame, dict):
        return None
    raw = frame.get(LEASE_WIRE_FIELD)
    return raw if isinstance(raw, dict) else None


def classify_lease(
    raw: Any,
    *,
    known_epoch: Optional[int],
    now_ms: Optional[int] = None,
    grace_ms: int = 0,
) -> LeaseVerdict:
    """Consumer-side gate: may this state be trusted, and if not, what next?

    ``known_epoch`` is the highest epoch this consumer has ever seen for the
    device (``None`` before the first lease). ``grace_ms`` absorbs clock skew
    between the issuing server and the consumer.

    A missing lease is **not** an error — it is pre-rollout traffic and returns
    :attr:`LeaseVerdict.ACCEPT`, which is what makes the rollout incremental.
    A *present but malformed* lease is :attr:`LeaseVerdict.ABORT`, because a
    consumer that understands leases must not fall back to blind trust once the
    field is on the wire.
    """
    if raw is None:
        return LeaseVerdict.ACCEPT
    if not isinstance(raw, dict):
        return LeaseVerdict.ABORT

    epoch = _coerce_int(raw.get("sessionEpoch"))
    ttl_ms = _coerce_int(raw.get("ttlMs"))
    issued_at_ms = _coerce_int(raw.get("issuedAtMs"))
    if epoch is None or epoch < 1 or ttl_ms is None or ttl_ms <= 0 or issued_at_ms is None:
        return LeaseVerdict.ABORT

    if known_epoch is not None:
        if epoch < known_epoch:
            # Somebody newer already owns this device. Whatever this state says
            # about listening/session-active is a ghost.
            return LeaseVerdict.RECOVER
        if epoch > known_epoch + _MAX_EPOCH_JUMP:
            # Epochs advance one connection at a time. A wild jump means the
            # ledger was reset or the value was forged.
            return LeaseVerdict.ABORT

    now = _now_ms() if now_ms is None else int(now_ms)
    if now > issued_at_ms + ttl_ms + max(0, int(grace_ms)):
        return LeaseVerdict.RESUME
    return LeaseVerdict.ACCEPT


#: An epoch more than this far ahead of what a consumer knows is treated as
#: forged/reset rather than as a very busy device.
_MAX_EPOCH_JUMP = 10_000


# ── ledgers ────────────────────────────────────────────────────────────────────


class InMemoryLeaseLedger:
    """Process-memory epoch counter. NOT restart-safe — see module docstring.

    ``durable`` is ``False`` so a caller can refuse to treat these epochs as an
    authority across a restart instead of quietly getting theater.
    """

    durable = False

    def __init__(self, *, ttl_ms: int = DEFAULT_LEASE_TTL_MS) -> None:
        self._epochs: Dict[str, int] = {}
        self.ttl_ms = max(1, int(ttl_ms))

    async def issue(self, device_id: str) -> Lease:
        key = _normalize_device_id(device_id)
        epoch = self._epochs.get(key, 0) + 1
        self._epochs[key] = epoch
        return Lease(
            device_id=key,
            session_epoch=epoch,
            seq=0,
            ttl_ms=self.ttl_ms,
            issued_at_ms=_now_ms(),
            durable=self.durable,
        )

    async def current(self, device_id: str) -> Optional[int]:
        return self._epochs.get(_normalize_device_id(device_id))


class RedisLeaseLedger:
    """Redis-backed monotonic epoch counter.

    ``INCR`` on a per-device key. The prod Redis runs ``--appendonly yes`` over a
    host-mounted volume (``deploy/docker-compose.prod.yml``) in a container
    separate from the server it guards, so the ledger outlives both an
    esp32-server process restart and a full stack redeploy.

    The key is deliberately **not** given a TTL: expiring it would silently
    restart the counter and reintroduce the exact failure this guards.
    """

    durable = True

    def __init__(
        self,
        redis: Any,
        *,
        namespace: str = "prod",
        ttl_ms: int = DEFAULT_LEASE_TTL_MS,
    ) -> None:
        self.redis = redis
        self.namespace = str(namespace or "prod")
        self.ttl_ms = max(1, int(ttl_ms))

    def _key(self, device_id: str) -> str:
        return f"{self.namespace}:lesson-liveness-epoch:{device_id}"

    async def issue(self, device_id: str) -> Lease:
        key = _normalize_device_id(device_id)
        epoch = int(await self.redis.incr(self._key(key)))
        return Lease(
            device_id=key,
            session_epoch=epoch,
            seq=0,
            ttl_ms=self.ttl_ms,
            issued_at_ms=_now_ms(),
            durable=self.durable,
        )

    async def current(self, device_id: str) -> Optional[int]:
        raw = await self.redis.get(self._key(_normalize_device_id(device_id)))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return _coerce_int(raw)


_DEFAULT_LEDGER: Any = None


def get_lease_ledger() -> Any:
    """Resolve the process-wide ledger: Redis when configured, memory otherwise."""
    global _DEFAULT_LEDGER
    if _DEFAULT_LEDGER is not None:
        return _DEFAULT_LEDGER
    _DEFAULT_LEDGER = _build_lease_ledger()
    return _DEFAULT_LEDGER


def reset_lease_ledger(ledger: Any = None) -> None:
    """Test seam — swap or clear the process-wide ledger."""
    global _DEFAULT_LEDGER
    _DEFAULT_LEDGER = ledger


def _build_lease_ledger() -> Any:
    ttl_ms = _env_int("LESSON_LIVENESS_LEASE_TTL_MS", DEFAULT_LEASE_TTL_MS)
    url = os.getenv("REDIS_URL")
    if not url:
        return InMemoryLeaseLedger(ttl_ms=ttl_ms)
    try:
        from redis import asyncio as redis_asyncio

        client = redis_asyncio.from_url(url, decode_responses=True)
    except Exception:
        return InMemoryLeaseLedger(ttl_ms=ttl_ms)
    namespace = os.getenv("TBOT_LIVE_REDIS_NAMESPACE", "prod")
    return RedisLeaseLedger(client, namespace=namespace, ttl_ms=ttl_ms)


# ── disposition telemetry ──────────────────────────────────────────────────────


def emit_disposition(
    logger: Any,
    *,
    disposition: Disposition,
    reason: str,
    device_id: str = "",
    assignment_id: str = "",
    session_id: str = "",
    session_epoch: Optional[int] = None,
    component: str = "esp32-server",
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Emit one structured teardown-disposition event and return its payload.

    Single line, single prefix (``lesson_disposition``), so a log-based counter
    can turn stale-state incidents into a rate without a parser per call site.
    Never raises: telemetry must not be able to break a teardown path.
    """
    event: Dict[str, Any] = {
        "event": "lesson_disposition",
        "disposition": Disposition(disposition).value,
        "reason": str(reason),
        "component": component,
        "deviceId": str(device_id or ""),
        "assignmentId": str(assignment_id or ""),
        "sessionId": str(session_id or ""),
        "sessionEpoch": session_epoch,
        "atMs": _now_ms(),
    }
    if extra:
        for key, value in extra.items():
            event.setdefault(str(key), value)
    if logger is not None:
        try:
            bound = logger.bind(tag=TAG) if hasattr(logger, "bind") else logger
            bound.info(
                "lesson_disposition "
                + json.dumps(event, ensure_ascii=False, separators=(",", ":"), default=str)
            )
        except Exception:
            pass
    return event


# ── helpers ────────────────────────────────────────────────────────────────────


def _now_ms() -> int:
    return int(time.time() * 1000)


def _normalize_device_id(device_id: Any) -> str:
    return str(device_id or "").strip().lower()


def _coerce_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _env_int(name: str, fallback: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return fallback
    parsed = _coerce_int(raw)
    return parsed if parsed is not None and parsed > 0 else fallback
