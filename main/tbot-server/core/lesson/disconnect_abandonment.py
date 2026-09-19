"""D8 — close an abandoned lesson when the ROBOT goes away (owner decision D8, 2026-09-19).

The hole this closes, as measured rather than argued
----------------------------------------------------
`coordination/course-inactivity-timer-observability-20260919/` swept the course
abandonment timeout at 30 / 60 / 90 / 180 s against two device behaviours. With the
robot alive and the *child* silent, the Variant B timer closes the assignment
correctly at every value. With the *robot* gone, **no timeout value closes it**:

* at 180 s the peer-silence watchdog closes the socket first, at 60 s idle, and
  `LessonRuntime._close_runtime_resources` cancels the timer while
  `_emit_teardown_disposition` emits telemetry only — no `lesson_stop`, no
  `lesson_abandoned`, assignment left **RUNNING**;
* at 30 s the timer fires first and emits its `lesson_stop` into a socket nobody is
  reading. The backend projects `lesson_abandoned` **only from the device's ack**
  (`runtime._handle_lesson_ack`), so no ack means no close — assignment left
  **RUNNING** again.

Why the close belongs HERE, from the source's own ownership model
-----------------------------------------------------------------
Three seams were possible: backend-side on connection loss, ESP-side before the
socket dies, or a reconciliation sweep. The source decides it:

1. **The backend cannot see a robot disconnect.** It has device-level liveness
   (`devices.last_seen_at`, the OFFLINE sweep in `device-heartbeat.service.ts`) but
   `lesson_sessions` carries no liveness column at all, and the one lesson-side
   watchdog (`lesson-progress-watchdog.service.ts`) deliberately only warns — it
   performs no UPDATE and transitions nothing. A backend close would mean inventing
   lesson liveness in the component that does not hold the socket.
2. **The esp32-server already owns exactly this classification.**
   `LessonRuntime._teardown_disposition()` returns `SCRAP` for "the run died
   mid-flight: this connection's session is gone while the backend assignment is
   still non-terminal", and its own docstring says this "is the case that becomes
   stale state in production". The condition was already named and already counted;
   D8 is the decision to act on it rather than only count it.
3. **The esp32-server is the sole producer of lesson lifecycle events**
   (`LessonEventForwarder` -> `POST /v1/devices/:id/lesson-events`). Closing here
   reuses the one terminal contract instead of adding a second writer. A
   reconciliation sweep would be a *third* writer of terminal lesson state, in a
   process with no knowledge of device presence, duplicating the epoch/lease logic
   that already lives beside this module.

**It is not ack-dependent.** The close is POSTed to the backend over HTTP by this
module. The device is not involved and need never come back.

Why it is DEFERRED rather than immediate
----------------------------------------
`LessonRuntime._resume_terminal_lifecycle` exists precisely so a robot that
reconnects picks its session back up. Closing on the socket event itself would kill
every lesson that survives a Wi-Fi blip. So teardown registers an *intent* with a
deadline, and a reconnecting runtime for the same `(assignmentId, sessionId)`
**claims** the session and cancels it. Only a session nobody came back for is closed.

Idempotence, end to end
-----------------------
A disconnect, a reconnect and a later real terminal event must together produce
exactly one terminal record and at most one reward. Four independent layers give
that, and none of them relies on the others:

1. **One intent per `(assignmentId, sessionId)`.** `register` is keyed; a repeat
   keeps the earliest deadline rather than stacking a second close.
2. **Claim on resume.** `LessonRuntime.start_protocol` claims its own key, so a
   reconnect retires the intent before it can fire.
3. **Pre-fire read-back.** Immediately before posting, the reaper re-reads
   `assignment/current`; an assignment that is absent or already terminal is
   *skipped*, not closed. This is what makes the close lose a genuine-completion
   race rather than racing it.
4. **The backend's own fences.** `progress_events` carries a unique lifecycle
   dedup index on `(assignment_id, event_type) WHERE sequence IS NULL`, so a
   replayed `lesson_abandoned` inserts nothing and projects nothing; and the ingest
   rejects any batch for an assignment already in `COMPLETED | FAILED | CANCELLED`
   with 409 `ASSIGNMENT_CONFLICT`. The reward is granted only where a
   `lesson_completed` actually flips the assignment out of a non-terminal state, so
   a cancelled assignment can never take one — which is D3's zero-reward rule
   holding by construction rather than by a second check.

D10 — the intent must survive an esp32-server restart
-----------------------------------------------------
Owner decision D10 (2026-09-20). The four layers above are all correct and all live
in `self._intents`, which is process memory: an esp32-server restart inside the grace
window lost every armed close and reopened the hole D8 exists to close. D10 makes the
intent durable **following `liveness_lease.py`'s precedent**, which solved the
identical problem for the session-epoch counter and says so in its own docstring.

What is reused, deliberately: the two-ledger shape (`InMemoryIntentLedger` /
`RedisIntentLedger` with a `durable` class attribute), the `get_*` / `reset_*`
process-wide resolution, the `REDIS_URL` + `TBOT_LIVE_REDIS_NAMESPACE` wiring, the
`{namespace}:{subsystem}:{id}` key convention, the fall-back-to-memory-when-the-client
-cannot-be-built behaviour, and the wall-clock `_now_ms()` stamp that makes a value
meaningful in another process.

What deviates, and why, each stated beside its constant: these keys carry a TTL (an
intent is an event, not a counter, and a record that outlives every use is garbage);
there is an index set (recovery must enumerate, a lease lookup never does); and there
is a per-assignment binding guard (a session-keyed claim cannot tell a recovered close
that a NEW session took the assignment over). The record also omits the device token
on purpose — see `DisconnectAbandonmentIntent.to_record`.

Two failure decisions are made explicitly rather than left to emerge, because "an
armed close that silently vanishes because Redis blinked is the same defect in a new
place":

* **Redis unavailable at ARM time** — the close is armed in memory anyway and the
  telemetry says `durable: false` (`persist`). Refusing to arm would leave the
  assignment RUNNING forever, which is the D8 hole itself; "fail closed" here would
  mean failing *into* the defect. The store is then retried on the reaper's own loop
  while Redis stays configured, so a blink costs seconds of exposure, not the life of
  the intent.
* **Redis unavailable at RECOVERY time** — "read it and it was empty" and "could not
  read it" are different events (`recover`). A failed read is retried on a bounded
  schedule at ERROR, and the final line states that armed closes may have been lost.

And two recovery guarantees: a recovered close goes through every fence an in-process
one does (it is re-armed, never re-fired blind), and it additionally refuses an
assignment a different session has since bound to — so a restart can neither
double-close nor steal a live lesson.

The stored reason
-----------------
`"disconnected"`, deliberately, and deliberately **not** `"child_inactive"`. The
backend pauses the assignment on `lesson_abandoned` with `reason == 'child_inactive'`
and *cancels* it on every other reason (`lesson-event-ingest.service.ts`, the two
mutually exclusive branches). Storing `child_inactive` would turn a RUNNING-forever
hole into a PAUSED-forever one. The finalisation lane's proven shape is `"cancelled"`,
which takes the same branch; `"disconnected"` is chosen over it because the branch is
a genuine catch-all (`reason` is a bounded 128-char allowlisted string, not an enum,
so both values project identically to `CANCELLED`) and because the stored payload is
then the only place an operator can tell "the child went silent" from "the robot
vanished" apart. That distinction not being visible anywhere is a large part of why
this hole went unfound.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

TAG = "LessonDisconnectAbandonment"

#: The `lesson_abandoned` reason stored for a robot that went away. See the module
#: docstring: anything other than `child_inactive` takes the backend's cancel branch.
DISCONNECT_ABANDONMENT_REASON = "disconnected"

# ── the grace window (configuration-driven, same shape as D2's timeout) ─────────
#
#   env  LESSON_DISCONNECT_ABANDONMENT_GRACE_SEC
#   file lesson.disconnect_abandonment_grace_sec
#
# DERIVATION. The window has to be long enough that a robot which reboots or loses
# Wi-Fi briefly is back before it expires, and short enough that a robot which is
# really gone does not hold a RUNNING assignment open for longer than a silent child
# would. Both ends are anchored to numbers this tree already commits to:
#
#   floor   the firmware's passive-lesson channel fails its own pong probe at 10 s
#           and the liveness lease TTL is 45 s (DEFAULT_LEASE_TTL_MS, documented as
#           "short enough that an abandoned session is reaped inside one lesson
#           step"). A grace under the lease TTL would close sessions the lease layer
#           still considers live, so the floor sits above it.
#   default 90 s — two lease TTLs. A reconnect that has not happened in twice the
#           interval the tree already calls stale is not a blip.
#   ceiling the D2 abandonment timeout's own maximum, 1800 s, so neither policy can
#           be configured to outlive the other by an order of magnitude.
#
# The default is deliberately BELOW D2's 180 s child-inactivity close: a robot that
# is gone is a stronger signal than a child who is quiet, and should not be held open
# longer. Like D2's value this FAILS CLOSED — an unparseable or out-of-range operator
# value refuses the boot rather than silently reverting to 90.
DISCONNECT_ABANDONMENT_GRACE_CONFIG_KEY = "disconnect_abandonment_grace_sec"
DISCONNECT_ABANDONMENT_GRACE_ENV_VAR = "LESSON_DISCONNECT_ABANDONMENT_GRACE_SEC"
DISCONNECT_ABANDONMENT_GRACE_DEFAULT_SEC = 90.0
DISCONNECT_ABANDONMENT_GRACE_MIN_SEC = 45.0
DISCONNECT_ABANDONMENT_GRACE_MAX_SEC = 1800.0

#: How long the reaper keeps retrying one intent against a backend that is refusing
#: or unreachable before it gives up and says so. Bounded on purpose: an unbounded
#: retry would turn a backend outage into an unbounded in-memory intent set.
DISCONNECT_ABANDONMENT_MAX_ATTEMPTS = 4
DISCONNECT_ABANDONMENT_RETRY_BACKOFF_SEC = 5.0

# ── D10: the durable ledger's constants ───────────────────────────────────────
#
# Owner decision D10 (owner-decisions-20260919.md): "make the armed intent durable,
# following that precedent rather than inventing a second mechanism". The precedent
# is `liveness_lease.py`, whose module docstring states the identical problem in the
# identical words: "If the counter lived in this process's memory, a server restart
# mid-lesson — one of the exact failures the lease is supposed to guard — would reset
# it". An armed close that lives only in `self._intents` has exactly that defect.
#
# KEY CONVENTION, taken from RedisLeaseLedger._key
# (`f"{namespace}:lesson-liveness-epoch:{device_id}"`): `{namespace}:{prefix}:{kind}:{id}`,
# namespace from TBOT_LIVE_REDIS_NAMESPACE, default "prod".
DISCONNECT_ABANDONMENT_LEDGER_PREFIX = "lesson-disconnect-abandonment"

#: Record schema version. Written into every record so a future shape change can be
#: recognised and skipped rather than half-read into a close.
DISCONNECT_ABANDONMENT_RECORD_SCHEMA = 1

# DELIBERATE DEVIATION FROM THE PRECEDENT, #1 — these keys DO expire.
# `RedisLeaseLedger` says "The key is deliberately **not** given a TTL: expiring it
# would silently restart the counter". That reasoning is correct for a monotonic
# counter and wrong for an intent. An intent is an event with a deadline: once the
# close has fired, been claimed or been skipped, its record is garbage, and garbage
# that never expires accumulates without bound in a Redis this process shares with
# other subsystems. Records are therefore deleted explicitly on every terminal
# outcome AND carry a TTL as a backstop for the one case an explicit delete cannot
# cover — a process that dies between firing and deleting.
#
# The TTL can never be the thing that loses a close, because it is set to the whole
# grace window PLUS the entire retry budget PLUS this margin. A record is expired only
# long after the last moment it could still have done any work.
DISCONNECT_ABANDONMENT_RECORD_TTL_MARGIN_SEC = 3600.0

# DELIBERATE DEVIATION #2 — an index key. The lease ledger only ever answers
# "what is the epoch for THIS device", so a point lookup suffices. Recovery must
# enumerate every armed intent with no idea what they are keyed on, so the ledger
# keeps a SET of live record keys beside them. `load` prunes index members whose
# record has expired, so the index cannot outgrow the records it points at.
#
# DELIBERATE DEVIATION #3 — a per-assignment binding guard, with its own TTL. D8's
# claim is by `(assignmentId, sessionId)` and D10 must preserve that, so a claim
# cannot by itself tell a recovered close that a DIFFERENT session has since taken
# over the same assignment. The guard records the session a runtime last bound to an
# assignment, and a recovered close refuses to fire when that is somebody else. Its
# TTL is generous but finite: it is a safety interlock, not a record of truth.
DISCONNECT_ABANDONMENT_BINDING_TTL_SEC = 7200.0

#: Recovery retry schedule when the ledger cannot be read at boot. Bounded and
#: explicit: a recovery that quietly reports "0 armed closes" because Redis was down
#: is the D10 defect wearing a different hat, so each failure is logged at error and
#: the last one says, in one line, that armed closes may have been lost.
DISCONNECT_ABANDONMENT_RECOVERY_BACKOFF_SEC = (5.0, 15.0, 45.0, 120.0)

#: How often the reaper retries persisting an intent that is armed but not yet
#: durable (Redis was down at arm time and is configured, so it may come back).
DISCONNECT_ABANDONMENT_REPERSIST_INTERVAL_SEC = 15.0


class DisconnectAbandonmentGraceConfigError(ValueError):
    """An unusable lesson.disconnect_abandonment_grace_sec. Raised, never defaulted."""


def disconnect_abandonment_grace_sec(lesson_cfg: Any) -> float:
    """The effective grace window, or raise.

    Absent/blank -> DISCONNECT_ABANDONMENT_GRACE_DEFAULT_SEC. Anything present but
    unusable raises: like D2, a silent revert to the default would hide an operator's
    mistake behind child-visible behaviour.
    """
    if not isinstance(lesson_cfg, dict):
        return DISCONNECT_ABANDONMENT_GRACE_DEFAULT_SEC
    raw = lesson_cfg.get(DISCONNECT_ABANDONMENT_GRACE_CONFIG_KEY)
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return DISCONNECT_ABANDONMENT_GRACE_DEFAULT_SEC
    if isinstance(raw, bool):
        raise DisconnectAbandonmentGraceConfigError(
            f"lesson.{DISCONNECT_ABANDONMENT_GRACE_CONFIG_KEY} must be a number, got a boolean"
        )
    try:
        parsed = float(raw)
    except (TypeError, ValueError) as exc:
        raise DisconnectAbandonmentGraceConfigError(
            f"lesson.{DISCONNECT_ABANDONMENT_GRACE_CONFIG_KEY} must be a number, got {raw!r}"
        ) from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise DisconnectAbandonmentGraceConfigError(
            f"lesson.{DISCONNECT_ABANDONMENT_GRACE_CONFIG_KEY} must be a positive finite "
            f"number, got {raw!r}"
        )
    return parsed


def assert_disconnect_abandonment_grace_in_range(value: float) -> float:
    """Operator-supplied bounds check. Raises rather than clamping or defaulting.

    Applied where an OPERATOR supplies the value (env / config volume). The
    in-process parse above stays bounds-free so tests can drive the reaper with a
    short window without weakening the operator-facing contract — the same split D2
    uses.
    """
    if not (
        DISCONNECT_ABANDONMENT_GRACE_MIN_SEC
        <= value
        <= DISCONNECT_ABANDONMENT_GRACE_MAX_SEC
    ):
        raise DisconnectAbandonmentGraceConfigError(
            f"lesson.{DISCONNECT_ABANDONMENT_GRACE_CONFIG_KEY} must be between "
            f"{DISCONNECT_ABANDONMENT_GRACE_MIN_SEC:g}s and "
            f"{DISCONNECT_ABANDONMENT_GRACE_MAX_SEC:g}s (below the floor it would close "
            f"sessions the liveness lease still treats as live; above the ceiling the "
            f"close stops being a policy), got {value!r}"
        )
    return value


# ── the intent ────────────────────────────────────────────────────────────────


@dataclass
class DisconnectAbandonmentIntent:
    """One mid-flight session whose robot went away, and how to close it.

    Everything needed to post the close is captured by value at registration time:
    the connection, its forwarder and its runtime are all being torn down, so the
    intent must not hold a reference to any of them.
    """

    assignment_id: str
    session_id: str
    device_id: str
    base_url: str
    token: Optional[str]
    batch: Dict[str, Any]
    deadline: float
    attempts: int = 0
    registered_at: float = 0.0
    trace: Dict[str, Any] = field(default_factory=dict)

    # ── D10 ───────────────────────────────────────────────────────────────────
    #: The robot's MAC. Persisted so a RECOVERED intent can mint its own device
    #: token instead of carrying one across a restart. See `to_record`.
    device_mac: str = ""
    #: The deadline in WALL-CLOCK epoch ms, beside the monotonic `deadline`.
    #: `time.monotonic()` is undefined across processes — a restart would turn a
    #: 90 s grace into an arbitrary one — so the durable copy uses the same
    #: `_now_ms()` wall clock `liveness_lease.Lease.issued_at_ms` already uses for
    #: exactly the cross-process case.
    deadline_ms: int = 0
    #: True when this intent came back from the ledger rather than from a teardown
    #: in this process. Recovered intents take one extra fence (the binding guard)
    #: and must mint a token, so the distinction is carried rather than inferred.
    recovered: bool = False
    #: True once the ledger has actually stored this intent. False means the close
    #: is armed in memory ONLY and will not survive a restart — which is reported,
    #: never silent. See `DisconnectAbandonmentReaper.persist`.
    durable: bool = False
    #: Whether the non-durable warning has already been emitted for this intent.
    #: The store is retried on a timer, so without this one Redis outage would
    #: produce one identical warning line per retry for every armed session — which
    #: is how a real signal gets filtered out. Counted once, logged once, retried
    #: silently; `persist_failed` still counts every attempt.
    non_durable_reported: bool = False

    @property
    def key(self) -> Tuple[str, str]:
        return (self.assignment_id, self.session_id)

    def to_record(self) -> Dict[str, Any]:
        """The durable form. JSON-serialisable, and deliberately WITHOUT the token.

        The device JWT is omitted on purpose, for two independent reasons:

        1. **It would not work.** Backend device tokens are valid for 15 minutes
           (`device_token_client._BACKEND_TOKEN_TTL_S`). A 90 s grace plus a restart
           plus a retry budget can outlive that, so a persisted token is a token that
           may already be dead when the close finally posts.
        2. **A ledger is not a place for a bearer.** The MAC is a hardware identifier
           the config already carries in plain text (`LESSON_ROLLOUT_DEVICE_ALLOWLIST`);
           a JWT is a credential. A recovered intent mints a fresh one the way every
           other recovery path in this tree does, through
           `device_token_client.resolve_device_identity`.
        """
        return {
            "schema": DISCONNECT_ABANDONMENT_RECORD_SCHEMA,
            "assignmentId": self.assignment_id,
            "sessionId": self.session_id,
            "deviceId": self.device_id,
            "deviceMac": self.device_mac,
            "baseUrl": self.base_url,
            "batch": self.batch,
            "deadlineMs": int(self.deadline_ms),
            "attempts": int(self.attempts),
            "trace": self.trace or {},
        }

    @classmethod
    def from_record(
        cls,
        record: Any,
        *,
        now_ms: Optional[int] = None,
        now_monotonic: Optional[float] = None,
    ) -> Optional["DisconnectAbandonmentIntent"]:
        """Rebuild an intent from a ledger record, or ``None`` when it is unusable.

        Returns ``None`` rather than raising for anything malformed or of an unknown
        schema: one corrupt record must not stop recovery of the rest, and a record
        this build cannot read is not a record it may act on.

        The wall-clock deadline is translated back into this process's monotonic
        clock. A deadline already in the past (the usual case after a restart that
        outlasted the grace) becomes *now*, so the intent is due immediately and goes
        straight through the ordinary pre-fire fences — it is never fired blind.
        """
        if not isinstance(record, dict):
            return None
        if int(record.get("schema") or 0) != DISCONNECT_ABANDONMENT_RECORD_SCHEMA:
            return None
        assignment_id = str(record.get("assignmentId") or "")
        session_id = str(record.get("sessionId") or "")
        batch = record.get("batch")
        if not assignment_id or not session_id or not isinstance(batch, dict):
            return None
        deadline_ms = _coerce_int(record.get("deadlineMs")) or 0
        now_ms_value = _now_ms() if now_ms is None else int(now_ms)
        now_monotonic_value = (
            time.monotonic() if now_monotonic is None else float(now_monotonic)
        )
        remaining_sec = max(0.0, (deadline_ms - now_ms_value) / 1000.0)
        trace = record.get("trace")
        return cls(
            assignment_id=assignment_id,
            session_id=session_id,
            device_id=str(record.get("deviceId") or ""),
            base_url=str(record.get("baseUrl") or ""),
            token=None,
            batch=batch,
            deadline=now_monotonic_value + remaining_sec,
            attempts=_coerce_int(record.get("attempts")) or 0,
            registered_at=now_monotonic_value,
            trace=trace if isinstance(trace, dict) else {},
            device_mac=str(record.get("deviceMac") or ""),
            deadline_ms=deadline_ms,
            recovered=True,
            durable=True,
        )


def build_disconnect_abandonment_batch(
    *,
    assignment_id: Any,
    lesson_id: Any,
    lesson_version: Any,
    session_id: Any,
    step_id: Any = None,
    step_type: Any = None,
    abandoned_at: Optional[str] = None,
    trace_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """The exact wire shape `_forward_terminal` would have sent, built by value.

    Same envelope, same event type, same fields — the close goes through the one
    terminal contract every other close uses. The only difference is `reason`.
    """
    event: Dict[str, Any] = {
        "type": "lesson_abandoned",
        "reason": DISCONNECT_ABANDONMENT_REASON,
        "abandonedAt": abandoned_at or _wire_timestamp(),
    }
    if step_id is not None:
        event["stepId"] = step_id
    if step_type is not None:
        event["stepType"] = step_type
    batch: Dict[str, Any] = {
        "assignmentId": assignment_id,
        "lessonId": lesson_id,
        "lessonVersion": lesson_version,
        "sessionId": session_id,
        "events": [event],
    }
    if isinstance(trace_context, dict):
        batch.update(trace_context)
    return {key: value for key, value in batch.items() if value is not None}


# ── D10: the ledgers ──────────────────────────────────────────────────────────
#
# Shaped on `liveness_lease.py`'s pair, because D10 says to follow that precedent
# rather than invent a second mechanism. Same two classes, same `durable` class
# attribute, same `get_*` / `reset_*` process-wide resolution, same REDIS_URL +
# TBOT_LIVE_REDIS_NAMESPACE wiring, same "fall back to memory when the client cannot
# be built" behaviour. The differences are the three deviations documented beside the
# constants above (TTL, index, binding guard) plus the verb set: a counter needs
# `issue`/`current`; an intent set needs put/drop/load.


class InMemoryIntentLedger:
    """Process-memory intent store. NOT restart-safe — which is the D10 defect.

    Kept, exactly as `InMemoryLeaseLedger` is kept, as the dev/test fallback for a
    deployment with no `REDIS_URL`. `durable` is ``False`` so the reaper can say so in
    the armed telemetry instead of quietly offering theatre.
    """

    durable = False

    def __init__(self) -> None:
        self._records: Dict[str, Dict[str, Any]] = {}
        self._bindings: Dict[str, str] = {}

    @staticmethod
    def _key(assignment_id: str, session_id: str) -> str:
        return f"{assignment_id}\x1f{session_id}"

    async def put(self, intent: "DisconnectAbandonmentIntent", *, ttl_sec: float) -> None:
        self._records[self._key(intent.assignment_id, intent.session_id)] = intent.to_record()

    async def drop(self, assignment_id: str, session_id: str) -> None:
        self._records.pop(self._key(assignment_id, session_id), None)

    async def load(self) -> Tuple[Dict[str, Any], ...]:
        return tuple(self._records.values())

    async def note_binding(self, assignment_id: str, session_id: str) -> None:
        self._bindings[str(assignment_id)] = str(session_id)

    async def binding(self, assignment_id: str) -> Optional[str]:
        return self._bindings.get(str(assignment_id))


class RedisIntentLedger:
    """Redis-backed intent store — the durable arm of D10.

    The same Redis `RedisLeaseLedger` already depends on: `--appendonly yes` over a
    host-mounted volume, in a container separate from the server it guards, so the
    ledger outlives both an esp32-server process restart and a full stack redeploy.

    Three keys per namespace:

    * ``{ns}:lesson-disconnect-abandonment:intent:{assignmentId}:{sessionId}``
      — one JSON record, TTL'd far beyond its own usefulness (see the constants).
    * ``{ns}:lesson-disconnect-abandonment:index`` — a SET of live record keys, so
      recovery can enumerate without a `KEYS` scan over a shared database.
    * ``{ns}:lesson-disconnect-abandonment:binding:{assignmentId}`` — the session a
      runtime most recently bound to that assignment.

    Every method is allowed to raise. The reaper, not the ledger, decides what a
    Redis failure means at each call site, and those decisions are the explicit
    behaviour D10 asks for rather than a swallowed exception.
    """

    durable = True

    def __init__(self, redis: Any, *, namespace: str = "prod") -> None:
        self.redis = redis
        self.namespace = str(namespace or "prod")

    def _record_key(self, assignment_id: str, session_id: str) -> str:
        return (
            f"{self.namespace}:{DISCONNECT_ABANDONMENT_LEDGER_PREFIX}:intent:"
            f"{assignment_id}:{session_id}"
        )

    def _index_key(self) -> str:
        return f"{self.namespace}:{DISCONNECT_ABANDONMENT_LEDGER_PREFIX}:index"

    def _binding_key(self, assignment_id: str) -> str:
        return (
            f"{self.namespace}:{DISCONNECT_ABANDONMENT_LEDGER_PREFIX}:binding:{assignment_id}"
        )

    async def put(self, intent: "DisconnectAbandonmentIntent", *, ttl_sec: float) -> None:
        key = self._record_key(intent.assignment_id, intent.session_id)
        payload = json.dumps(
            intent.to_record(), ensure_ascii=False, separators=(",", ":"), default=str
        )
        # Index first. An index member whose record is missing is pruned harmlessly by
        # `load`; a record no index member points at would be invisible to recovery,
        # which is the failure that matters.
        await self.redis.sadd(self._index_key(), key)
        await self.redis.set(key, payload, ex=max(1, int(ttl_sec)))

    async def drop(self, assignment_id: str, session_id: str) -> None:
        key = self._record_key(assignment_id, session_id)
        await self.redis.delete(key)
        await self.redis.srem(self._index_key(), key)

    async def load(self) -> Tuple[Dict[str, Any], ...]:
        members = await self.redis.smembers(self._index_key())
        keys = sorted(_as_text(member) for member in (members or []))
        records = []
        stale = []
        for key in keys:
            raw = await self.redis.get(key)
            if raw is None:
                stale.append(key)
                continue
            try:
                parsed = json.loads(_as_text(raw))
            except (TypeError, ValueError):
                stale.append(key)
                continue
            if isinstance(parsed, dict):
                records.append(parsed)
            else:
                stale.append(key)
        if stale:
            await self.redis.srem(self._index_key(), *stale)
        return tuple(records)

    async def note_binding(self, assignment_id: str, session_id: str) -> None:
        await self.redis.set(
            self._binding_key(str(assignment_id)),
            str(session_id),
            ex=max(1, int(DISCONNECT_ABANDONMENT_BINDING_TTL_SEC)),
        )

    async def binding(self, assignment_id: str) -> Optional[str]:
        raw = await self.redis.get(self._binding_key(str(assignment_id)))
        return None if raw is None else _as_text(raw)


_DEFAULT_LEDGER: Any = None


def get_intent_ledger() -> Any:
    """Resolve the process-wide ledger: Redis when configured, memory otherwise.

    Byte-for-byte the same resolution `liveness_lease.get_lease_ledger` performs,
    including the same environment variables, so a deployment that made the lease
    durable has already made this durable and no second operational step exists.
    """
    global _DEFAULT_LEDGER
    if _DEFAULT_LEDGER is not None:
        return _DEFAULT_LEDGER
    _DEFAULT_LEDGER = _build_intent_ledger()
    return _DEFAULT_LEDGER


def reset_intent_ledger(ledger: Any = None) -> None:
    """Test seam — swap or clear the process-wide ledger."""
    global _DEFAULT_LEDGER
    _DEFAULT_LEDGER = ledger


def _build_intent_ledger() -> Any:
    url = os.getenv("REDIS_URL")
    if not url:
        return InMemoryIntentLedger()
    try:
        from redis import asyncio as redis_asyncio

        client = redis_asyncio.from_url(url, decode_responses=True)
    except Exception:
        return InMemoryIntentLedger()
    namespace = os.getenv("TBOT_LIVE_REDIS_NAMESPACE", "prod")
    return RedisIntentLedger(client, namespace=namespace)


# ── the reaper ────────────────────────────────────────────────────────────────


class DisconnectAbandonmentReaper:
    """Process-wide deferred closer for sessions whose device never came back.

    One task, one dict. It is deliberately *not* per-connection: the connection is
    the thing that died.
    """

    def __init__(
        self,
        *,
        logger: Any = None,
        post_fn: Any = None,
        assignment_fn: Any = None,
        clock: Any = None,
        sleep: Any = None,
        max_attempts: int = DISCONNECT_ABANDONMENT_MAX_ATTEMPTS,
        retry_backoff_sec: float = DISCONNECT_ABANDONMENT_RETRY_BACKOFF_SEC,
        ledger: Any = None,
        token_fn: Any = None,
        now_ms_fn: Any = None,
    ) -> None:
        self._logger = logger
        self._post_fn = post_fn
        self._assignment_fn = assignment_fn
        self._clock = clock or time.monotonic
        self._sleep = sleep or asyncio.sleep
        self._max_attempts = max(1, int(max_attempts))
        self._retry_backoff_sec = max(0.0, float(retry_backoff_sec))
        self._intents: Dict[Tuple[str, str], DisconnectAbandonmentIntent] = {}
        self._task: Optional[asyncio.Task] = None
        self._wake: Optional[asyncio.Event] = None
        self._closed = False
        # D10 — the durable arm. Resolved lazily so importing this module never
        # builds a Redis client, and injectable so every test drives a real ledger
        # object rather than a patched global.
        self._ledger = ledger
        self._token_fn = token_fn
        self._now_ms = now_ms_fn or _now_ms
        self._side_tasks: set = set()
        self._recovered = False
        # Seeded from the clock, not from zero: otherwise the loop's very first
        # iteration always retries a store that `register` has only just attempted,
        # which doubles every persist failure in the counters for no benefit.
        self._last_repersist = self._clock()
        # Observability without a parser per call site, mirroring liveness_lease.
        self.counters: Dict[str, int] = {
            "registered": 0,
            "claimed": 0,
            "closed": 0,
            "skipped_already_terminal": 0,
            "failed": 0,
            # D10
            "armed_non_durable": 0,
            "persisted": 0,
            "persist_failed": 0,
            "recovered": 0,
            "recovery_failed": 0,
            "skipped_superseded_session": 0,
        }

    # -- D10: the durable arm ---------------------------------------------------

    @property
    def ledger(self) -> Any:
        if self._ledger is None:
            self._ledger = get_intent_ledger()
        return self._ledger

    def _spawn(self, coro: Any) -> None:
        """Run a ledger coroutine beside the caller, or drop it with a reason.

        `register` and `claim` are synchronous — they are called from a teardown and
        from `LessonRuntime.__init__` respectively — so the ledger write cannot be
        awaited inline. Without a running loop (a synchronous teardown in a test)
        there is nothing to schedule onto, and that is reported as a non-durable arm
        rather than swallowed.
        """
        try:
            task = asyncio.ensure_future(coro)
        except RuntimeError:
            coro.close()
            return
        self._side_tasks.add(task)
        task.add_done_callback(self._side_tasks.discard)

    def _record_ttl_sec(self, intent: DisconnectAbandonmentIntent) -> float:
        """Grace remaining + the whole retry budget + the margin. See the constants."""
        remaining = max(0.0, intent.deadline - self._clock())
        retry_budget = self._retry_backoff_sec * self._max_attempts * self._max_attempts
        return remaining + retry_budget + DISCONNECT_ABANDONMENT_RECORD_TTL_MARGIN_SEC

    async def persist(self, intent: DisconnectAbandonmentIntent) -> bool:
        """Store one armed intent. Never raises; reports what happened.

        **THE REDIS-UNAVAILABLE DECISION, AT ARM TIME.** This is deliberate, and it is
        the opposite of the fail-closed choice D2 and D8 make for their configuration
        values, for a reason that is worth stating rather than leaving to inference.

        Refusing to arm when the ledger is unavailable would mean the assignment stays
        RUNNING forever — which is precisely the hole D8 exists to close. "Fail closed"
        there would be failing *into* the defect. So the close is always armed in
        memory, and the ledger is best-effort **but never silent**:

        * a successful store sets `intent.durable = True`;
        * a failure, or a ledger that is in-memory to begin with, leaves it False,
          increments `armed_non_durable`, and logs
          `lesson_disconnect_abandonment_armed_non_durable` at WARNING naming the
          session and the reason;
        * while the ledger is durable-capable, the reaper keeps retrying the store on
          its own loop (`_repersist_pending`), so an arm that lands during a Redis
          blink becomes durable as soon as Redis returns instead of staying degraded
          for the life of the intent.

        The armed close therefore never vanishes because of Redis: at worst it loses
        restart survival, and an operator can see exactly which sessions are exposed.
        """
        ledger = self.ledger
        if not getattr(ledger, "durable", False):
            if intent.durable:
                return True
            self._report_non_durable(intent, reason="ledger_not_durable",
                                     ledger=type(ledger).__name__)
            return False
        try:
            await ledger.put(intent, ttl_sec=self._record_ttl_sec(intent))
        except Exception as exc:
            intent.durable = False
            self.counters["persist_failed"] += 1
            self._report_non_durable(
                intent, reason="ledger_unavailable", error=type(exc).__name__
            )
            return False
        if not intent.durable:
            intent.durable = True
            intent.non_durable_reported = False
            self.counters["persisted"] += 1
            self._log("info", "lesson_disconnect_abandonment_persisted", intent)
        return True

    def _report_non_durable(self, intent: DisconnectAbandonmentIntent, **fields: Any) -> None:
        """Say once, per intent, that this close will not survive a restart."""
        intent.durable = False
        if intent.non_durable_reported:
            return
        intent.non_durable_reported = True
        self.counters["armed_non_durable"] += 1
        self._log(
            "warning", "lesson_disconnect_abandonment_armed_non_durable", intent, **fields
        )

    async def forget(self, intent: DisconnectAbandonmentIntent) -> None:
        """Delete a record whose intent reached a terminal outcome. Never raises.

        A delete that fails is harmless: the record's TTL removes it eventually, and a
        record that outlives its intent is re-read by a later recovery and then
        *skipped* by the pre-fire read-back, which is the same fence a duplicate
        would have hit anyway.
        """
        try:
            await self.ledger.drop(intent.assignment_id, intent.session_id)
        except Exception as exc:
            self._log(
                "warning",
                "lesson_disconnect_abandonment_forget_failed",
                intent,
                error=type(exc).__name__,
            )

    async def note_binding(self, assignment_id: Any, session_id: Any) -> None:
        """Record that a runtime is bound to `(assignmentId, sessionId)`. Never raises.

        This is the assignment-level interlock a session-keyed claim cannot provide.
        D8's claim is by `(assignmentId, sessionId)` and D10 preserves that exactly; a
        recovered close for an OLD session must additionally not fire when a NEW
        session has taken the assignment over, and only an assignment-keyed marker can
        say so.
        """
        if not assignment_id or not session_id:
            return
        try:
            await self.ledger.note_binding(str(assignment_id), str(session_id))
        except Exception:
            # Best-effort. Losing the guard costs a recovered close one extra fence,
            # never a wrong close: the pre-fire read-back still runs.
            pass

    async def recover(self) -> Dict[str, Any]:
        """Re-arm every intent the ledger still holds. Idempotent; safe to call twice.

        **THE REDIS-UNAVAILABLE DECISION, AT RECOVERY TIME.** A recovery that reports
        "0 armed closes" because it could not read the ledger is the same defect D10
        exists to remove, so "read it and it was empty" and "could not read it" are
        different outcomes here and are logged as different events. A failed read is
        retried on `DISCONNECT_ABANDONMENT_RECOVERY_BACKOFF_SEC`, at ERROR each time,
        and if every attempt fails the final line says armed closes may have been lost.

        Recovered intents are re-armed, never re-fired blind: each one passes through
        the ordinary `_fire` path with the pre-fire read-back, plus the binding guard,
        so a restart cannot close an assignment that completed, was cancelled, or was
        taken over by a new runtime while the server was down.
        """
        ledger = self.ledger
        summary: Dict[str, Any] = {
            "durable": bool(getattr(ledger, "durable", False)),
            "recovered": 0,
            "skipped": 0,
            "available": True,
        }
        if not summary["durable"]:
            # Not a failure: a deployment with no REDIS_URL chose this. Said once, at
            # info, so "no armed closes survived the restart" is never a surprise.
            self._log_line(
                "info",
                "lesson_disconnect_abandonment_recovery_skipped "
                + json.dumps(
                    {
                        "event": "lesson_disconnect_abandonment_recovery_skipped",
                        "reason": "ledger_not_durable",
                        "ledger": type(ledger).__name__,
                    },
                    separators=(",", ":"),
                ),
            )
            self._recovered = True
            return summary
        try:
            records = await ledger.load()
        except Exception as exc:
            self.counters["recovery_failed"] += 1
            summary["available"] = False
            summary["error"] = type(exc).__name__
            self._log_line(
                "error",
                "lesson_disconnect_abandonment_recovery_failed "
                + json.dumps(
                    {
                        "event": "lesson_disconnect_abandonment_recovery_failed",
                        "error": type(exc).__name__,
                    },
                    separators=(",", ":"),
                ),
            )
            return summary
        now_ms = self._now_ms()
        now_monotonic = self._clock()
        for record in records or ():
            intent = DisconnectAbandonmentIntent.from_record(
                record, now_ms=now_ms, now_monotonic=now_monotonic
            )
            if intent is None:
                summary["skipped"] += 1
                continue
            if intent.key in self._intents:
                # A live teardown in THIS process already armed it. The in-memory one
                # wins: it has a token and a fresher deadline.
                summary["skipped"] += 1
                continue
            self._intents[intent.key] = intent
            self.counters["recovered"] += 1
            summary["recovered"] += 1
            self._log(
                "info",
                "lesson_disconnect_abandonment_recovered",
                intent,
                dueInSec=round(max(0.0, intent.deadline - now_monotonic), 3),
                attempts=intent.attempts,
            )
        self._recovered = True
        if self._intents:
            self._ensure_task()
            self._notify()
        self._log_line(
            "info",
            "lesson_disconnect_abandonment_recovery_complete "
            + json.dumps(
                {
                    "event": "lesson_disconnect_abandonment_recovery_complete",
                    **{k: v for k, v in summary.items() if k != "error"},
                },
                separators=(",", ":"),
            ),
        )
        return summary

    async def recover_with_retry(
        self, *, backoff: Any = DISCONNECT_ABANDONMENT_RECOVERY_BACKOFF_SEC
    ) -> Dict[str, Any]:
        """`recover`, retried on a bounded schedule while the ledger is unreadable."""
        summary = await self.recover()
        if summary.get("available"):
            return summary
        for delay in backoff:
            await self._sleep(delay)
            if self._closed:
                return summary
            summary = await self.recover()
            if summary.get("available"):
                return summary
        self._log_line(
            "error",
            "lesson_disconnect_abandonment_recovery_unavailable "
            + json.dumps(
                {
                    "event": "lesson_disconnect_abandonment_recovery_unavailable",
                    "attempts": 1 + len(tuple(backoff)),
                    "consequence": "armed disconnect closes from before this restart "
                    "may not have been recovered",
                },
                separators=(",", ":"),
            ),
        )
        return summary

    async def _repersist_pending(self) -> None:
        """Retry the ledger store for intents armed while Redis was unreachable."""
        if not getattr(self.ledger, "durable", False):
            return
        now = self._clock()
        if now - self._last_repersist < DISCONNECT_ABANDONMENT_REPERSIST_INTERVAL_SEC:
            return
        self._last_repersist = now
        for intent in list(self._intents.values()):
            if not intent.durable:
                await self.persist(intent)

    # -- registration / claim ---------------------------------------------------

    def register(self, intent: DisconnectAbandonmentIntent) -> bool:
        """Arm a deferred close. Returns False when one is already armed for the key.

        A repeat keeps the EARLIER deadline. Two teardowns of the same session must
        not push the close further out, and must never produce two closes.
        """
        if self._closed:
            return False
        if not intent.assignment_id or not intent.session_id:
            return False
        existing = self._intents.get(intent.key)
        if existing is not None:
            if intent.deadline < existing.deadline:
                existing.deadline = intent.deadline
                existing.deadline_ms = intent.deadline_ms
                # The durable copy carries the deadline, so a deadline that moves
                # must be re-stored or a restart would recover the old one.
                existing.durable = False
                self._spawn(self.persist(existing))
            # A repeat of an EXISTING key never re-arms and never double-closes. It
            # also re-asserts the record, so a first arm that failed to persist gets
            # another chance from a second teardown of the same session.
            elif not existing.durable:
                self._spawn(self.persist(existing))
            self._notify()
            return False
        intent.registered_at = self._clock()
        if not intent.deadline_ms:
            intent.deadline_ms = self._now_ms() + int(
                max(0.0, intent.deadline - intent.registered_at) * 1000
            )
        self._intents[intent.key] = intent
        self.counters["registered"] += 1
        self._log(
            "info",
            "lesson_disconnect_abandonment_armed",
            intent,
            graceSec=round(max(0.0, intent.deadline - intent.registered_at), 3),
            durable=bool(getattr(self.ledger, "durable", False)),
        )
        # D10: make it survive this process. Scheduled rather than awaited because
        # `register` is called from a synchronous teardown; `persist` reports the
        # outcome on the log either way, so an arm is never silently non-durable.
        self._spawn(self.persist(intent))
        self._ensure_task()
        self._notify()
        return True

    def claim(self, assignment_id: Any, session_id: Any) -> bool:
        """A live runtime owns this session again — retire any armed close.

        Called when a `LessonRuntime` is BOUND to a session — a first start and a
        reconnect alike — with `start_protocol` claiming again as a backstop.

        D10 adds two durable effects, and neither changes what a claim IS: the claim
        is still by `(assignmentId, sessionId)` exactly as D8 decided.

        1. The ledger record is deleted, so the claim survives this process too.
        2. The assignment-level binding guard is written, so a close recovered from
           the ledger for an OLDER session of the same assignment can tell that a new
           runtime owns it and refuse to fire.

        The guard is written even when nothing was armed. A robot that reconnects
        with a NEW session after a restart never had an intent to pop here, and the
        recovered intent for its previous session is exactly the close that must not
        steal it.
        """
        key = (str(assignment_id or ""), str(session_id or ""))
        self._spawn(self.note_binding(key[0], key[1]))
        intent = self._intents.pop(key, None)
        if intent is None:
            return False
        self.counters["claimed"] += 1
        self._log(
            "info",
            "lesson_disconnect_abandonment_claimed",
            intent,
            afterSec=round(max(0.0, self._clock() - intent.registered_at), 3),
            recovered=intent.recovered,
        )
        self._spawn(self.forget(intent))
        return True

    def pending_keys(self) -> Tuple[Tuple[str, str], ...]:
        return tuple(self._intents)

    # -- the loop ---------------------------------------------------------------

    def _ensure_task(self) -> None:
        if self._task is not None and not self._task.done():
            return
        try:
            self._task = asyncio.create_task(self._run())
        except RuntimeError:  # pragma: no cover - no running loop (sync teardown)
            self._task = None

    def _notify(self) -> None:
        event = self._wake
        if event is not None:
            event.set()

    async def _run(self) -> None:
        self._wake = asyncio.Event()
        try:
            while not self._closed and self._intents:
                # D10: an arm that could not reach Redis retries here, so a blink
                # costs restart-survival for seconds rather than for the intent's
                # whole life.
                await self._repersist_pending()
                now = self._clock()
                due = [i for i in self._intents.values() if i.deadline <= now]
                for intent in due:
                    # Re-check: a claim may have landed while we were awaiting.
                    if self._intents.get(intent.key) is not intent:
                        continue
                    await self._fire(intent)
                if not self._intents:
                    break
                horizon = min(i.deadline for i in self._intents.values()) - self._clock()
                if any(not i.durable for i in self._intents.values()):
                    # D10: while anything is armed-but-not-durable, wake often enough
                    # to retry the store. Otherwise a 90 s grace would sleep straight
                    # through a Redis outage that ended in the first second of it.
                    horizon = min(horizon, DISCONNECT_ABANDONMENT_REPERSIST_INTERVAL_SEC)
                self._wake.clear()
                if horizon > 0:
                    try:
                        await asyncio.wait_for(self._wake.wait(), timeout=horizon)
                    except asyncio.TimeoutError:
                        pass
                    except asyncio.CancelledError:
                        raise
                else:
                    await self._sleep(0)
        except asyncio.CancelledError:  # pragma: no cover - teardown
            raise
        except Exception as exc:  # pragma: no cover - the reaper must not die silently
            self._log_line("error", f"disconnect abandonment reaper crashed: {type(exc).__name__}")
        finally:
            self._wake = None

    async def drain(self) -> None:
        """Test seam: fire every intent that is due now, synchronously."""
        for intent in list(self._intents.values()):
            if intent.deadline <= self._clock() and self._intents.get(intent.key) is intent:
                await self._fire(intent)

    async def aclose(self) -> None:
        self._closed = True
        self._intents.clear()
        task = self._task
        self._task = None
        self._notify()
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    # -- firing -----------------------------------------------------------------

    async def _fire(self, intent: DisconnectAbandonmentIntent) -> None:
        intent.attempts += 1
        # D10 fence — the binding guard, for RECOVERED intents only.
        #
        # An in-process intent cannot need it: a runtime that bound to this session
        # popped it synchronously at `claim`. A recovered one can, and this is the
        # "must not steal a live lesson" case D10 names: the server restarted, the
        # robot came back and started a NEW session on the SAME assignment, and the
        # close armed for the OLD session is still holding a deadline. The assignment
        # read-back cannot see that — the assignment is legitimately RUNNING — so only
        # an assignment-keyed marker can refuse it.
        if intent.recovered and await self._superseded_by_new_session(intent):
            self._intents.pop(intent.key, None)
            self.counters["skipped_superseded_session"] += 1
            self._log(
                "info",
                "lesson_disconnect_abandonment_skipped",
                intent,
                observedState="SUPERSEDED_SESSION",
            )
            self._spawn(self.forget(intent))
            return
        # D10 — a recovered intent deliberately carries no token (see `to_record`).
        # Mint one the way every other recovery path in this tree does. A mint that
        # fails leaves `token` None, the post then 401s, and the ordinary retry
        # budget applies: a close is never abandoned because one mint failed.
        if intent.token is None and intent.recovered:
            await self._resolve_token(intent)
        try:
            terminal_state = await self._already_terminal(intent)
        except Exception as exc:  # a failed read-back must not block the close
            self._log(
                "warning",
                "lesson_disconnect_abandonment_readback_failed",
                intent,
                error=type(exc).__name__,
            )
            terminal_state = None
        if terminal_state is not None:
            self._intents.pop(intent.key, None)
            self.counters["skipped_already_terminal"] += 1
            self._log(
                "info",
                "lesson_disconnect_abandonment_skipped",
                intent,
                observedState=terminal_state,
            )
            self._spawn(self.forget(intent))
            return
        try:
            await self._post(intent)
        except Exception as exc:
            if intent.attempts >= self._max_attempts:
                self._intents.pop(intent.key, None)
                self.counters["failed"] += 1
                self._log(
                    "error",
                    "lesson_disconnect_abandonment_failed",
                    intent,
                    attempts=intent.attempts,
                    error=type(exc).__name__,
                )
                # D10: the record goes too. Keeping it would make every later restart
                # re-attempt a close this process already gave up on, forever.
                self._spawn(self.forget(intent))
                return
            intent.deadline = self._clock() + self._retry_backoff_sec * intent.attempts
            intent.deadline_ms = self._now_ms() + int(
                self._retry_backoff_sec * intent.attempts * 1000
            )
            self._log(
                "warning",
                "lesson_disconnect_abandonment_retry",
                intent,
                attempts=intent.attempts,
                error=type(exc).__name__,
            )
            # D10: persist the new deadline and the attempt count, so a restart mid
            # retry resumes the retry rather than restarting the whole budget.
            intent.durable = False
            self._spawn(self.persist(intent))
            self._notify()
            return
        self._intents.pop(intent.key, None)
        self.counters["closed"] += 1
        self._log(
            "info",
            "lesson_disconnect_abandonment_closed",
            intent,
            reason=DISCONNECT_ABANDONMENT_REASON,
            attempts=intent.attempts,
            recovered=intent.recovered,
        )
        self._spawn(self.forget(intent))
        self._emit_disposition(intent)

    async def _superseded_by_new_session(self, intent: DisconnectAbandonmentIntent) -> bool:
        """Has a DIFFERENT session bound to this assignment since the intent was armed?

        Never raises: a guard that cannot be read is treated as absent, which costs
        the close one fence rather than making it wrong — the pre-fire read-back and
        the backend's own 409 both still apply.
        """
        try:
            bound = await self.ledger.binding(intent.assignment_id)
        except Exception:
            return False
        return bool(bound) and str(bound) != str(intent.session_id)

    async def _resolve_token(self, intent: DisconnectAbandonmentIntent) -> None:
        """Mint a device token for a recovered intent. Never raises."""
        token_fn = self._token_fn
        try:
            if token_fn is not None:
                intent.token = await token_fn(intent)
                return
            if not intent.device_mac or not intent.base_url:
                return
            import httpx

            from config.device_token_client import resolve_device_identity

            async with httpx.AsyncClient(timeout=10.0) as client:
                _device_uuid, token = await resolve_device_identity(
                    client, intent.base_url, intent.device_mac
                )
            if token:
                intent.token = token
        except Exception as exc:
            self._log(
                "warning",
                "lesson_disconnect_abandonment_token_unavailable",
                intent,
                error=type(exc).__name__,
            )

    async def _already_terminal(self, intent: DisconnectAbandonmentIntent) -> Optional[str]:
        """Read the assignment back. Returns its state when the close must be skipped.

        `assignment/current` reports an ACTIVE assignment; with `include_terminal` it
        also reports a terminal one. Either "no active assignment" or a terminal state
        means somebody else already closed this — the genuine-completion race, lost on
        purpose.
        """
        assignment_fn = self._assignment_fn
        if assignment_fn is None:
            from config import manage_api_client as backend_api

            async def assignment_fn(intent: DisconnectAbandonmentIntent):  # type: ignore[misc]
                import httpx

                async with httpx.AsyncClient(timeout=5.0) as client:
                    return await backend_api.get_current_assignment(
                        client,
                        intent.base_url,
                        intent.device_id,
                        token=intent.token,
                        include_terminal=True,
                    )

        assignment = await assignment_fn(intent)
        if not isinstance(assignment, dict):
            # No active assignment: the slot is released, which is what a recorded
            # terminal looks like from here.
            return "ABSENT"
        if str(assignment.get("id") or "") not in ("", str(intent.assignment_id)):
            # A different assignment already owns the slot; ours is finished with.
            return "SUPERSEDED"
        state = str(assignment.get("state", "") or "").upper()
        if state in {"COMPLETED", "FAILED", "CANCELLED"}:
            return state
        return None

    async def _post(self, intent: DisconnectAbandonmentIntent) -> None:
        post_fn = self._post_fn
        if post_fn is not None:
            await post_fn(intent)
            return
        from config import manage_api_client as backend_api
        import httpx

        async with httpx.AsyncClient(timeout=10.0) as client:
            await backend_api.post_lesson_event(
                client,
                intent.base_url,
                intent.device_id,
                intent.batch,
                token=intent.token,
            )

    def _emit_disposition(self, intent: DisconnectAbandonmentIntent) -> None:
        from core.lesson.liveness_lease import Disposition, emit_disposition

        try:
            emit_disposition(
                self._logger,
                disposition=Disposition.SCRAP,
                reason="disconnect_abandonment_closed",
                device_id=intent.device_id,
                assignment_id=intent.assignment_id,
                session_id=intent.session_id,
                session_epoch=intent.trace.get("sessionEpoch"),
                extra={"abandonReason": DISCONNECT_ABANDONMENT_REASON},
            )
        except Exception:  # pragma: no cover - telemetry must never break the close
            pass

    # -- logging ----------------------------------------------------------------

    def _log(self, level: str, event: str, intent: DisconnectAbandonmentIntent, **fields: Any) -> None:
        payload = {
            "event": event,
            "deviceId": intent.device_id,
            "assignmentId": intent.assignment_id,
            "sessionId": intent.session_id,
            **fields,
        }
        self._log_line(
            level,
            f"{event} "
            + json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str),
        )

    def _log_line(self, level: str, message: str) -> None:
        logger = self._logger
        if logger is None:
            return
        try:
            bound = logger.bind(tag=TAG) if hasattr(logger, "bind") else logger
            getattr(bound, level, bound.info)(message)
        except Exception:  # pragma: no cover
            pass


_DEFAULT_REAPER: Optional[DisconnectAbandonmentReaper] = None


def get_disconnect_abandonment_reaper(logger: Any = None) -> DisconnectAbandonmentReaper:
    global _DEFAULT_REAPER
    if _DEFAULT_REAPER is None:
        _DEFAULT_REAPER = DisconnectAbandonmentReaper(logger=logger)
    elif logger is not None and _DEFAULT_REAPER._logger is None:
        _DEFAULT_REAPER._logger = logger
    return _DEFAULT_REAPER


def reset_disconnect_abandonment_reaper(
    reaper: Optional[DisconnectAbandonmentReaper] = None,
) -> None:
    """Test seam — swap or clear the process-wide reaper."""
    global _DEFAULT_REAPER
    _DEFAULT_REAPER = reaper


async def recover_disconnect_abandonment_intents(logger: Any = None) -> Dict[str, Any]:
    """D10 boot hook — re-arm every close the ledger survived a restart with.

    Called once from `app.main()`, after the servers are built and before the process
    settles into serving. It is safe to call on a stack with no Redis (it says so and
    returns), safe to call twice, and it never raises: a recovery that cannot run must
    not stop the server that would otherwise serve lessons.
    """
    reaper = get_disconnect_abandonment_reaper(logger)
    try:
        return await reaper.recover_with_retry()
    except Exception as exc:  # pragma: no cover - boot must not die on recovery
        try:
            reaper._log_line(
                "error",
                "lesson_disconnect_abandonment_recovery_crashed "
                + json.dumps({"error": type(exc).__name__}, separators=(",", ":")),
            )
        except Exception:
            pass
        return {"durable": False, "recovered": 0, "skipped": 0, "available": False}


# ── helpers ────────────────────────────────────────────────────────────────────
#
# Local copies rather than imports of `liveness_lease`'s privates: the same values,
# the same semantics, and no new import edge between two modules that are deliberately
# independent of each other.


def _now_ms() -> int:
    return int(time.time() * 1000)


def _coerce_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _as_text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


def _wire_timestamp() -> str:
    from core.lesson.runtime import _wire_timestamp as runtime_wire_timestamp

    return runtime_wire_timestamp()
