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

    @property
    def key(self) -> Tuple[str, str]:
        return (self.assignment_id, self.session_id)


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
        # Observability without a parser per call site, mirroring liveness_lease.
        self.counters: Dict[str, int] = {
            "registered": 0,
            "claimed": 0,
            "closed": 0,
            "skipped_already_terminal": 0,
            "failed": 0,
        }

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
            existing.deadline = min(existing.deadline, intent.deadline)
            self._notify()
            return False
        intent.registered_at = self._clock()
        self._intents[intent.key] = intent
        self.counters["registered"] += 1
        self._log(
            "info",
            "lesson_disconnect_abandonment_armed",
            intent,
            graceSec=round(max(0.0, intent.deadline - intent.registered_at), 3),
        )
        self._ensure_task()
        self._notify()
        return True

    def claim(self, assignment_id: Any, session_id: Any) -> bool:
        """A live runtime owns this session again — retire any armed close.

        Called from `LessonRuntime.start_protocol`, which is the one place a session
        becomes live, on a first start and on a reconnect alike.
        """
        key = (str(assignment_id or ""), str(session_id or ""))
        intent = self._intents.pop(key, None)
        if intent is None:
            return False
        self.counters["claimed"] += 1
        self._log(
            "info",
            "lesson_disconnect_abandonment_claimed",
            intent,
            afterSec=round(max(0.0, self._clock() - intent.registered_at), 3),
        )
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
                return
            intent.deadline = self._clock() + self._retry_backoff_sec * intent.attempts
            self._log(
                "warning",
                "lesson_disconnect_abandonment_retry",
                intent,
                attempts=intent.attempts,
                error=type(exc).__name__,
            )
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
        )
        self._emit_disposition(intent)

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


def _wire_timestamp() -> str:
    from core.lesson.runtime import _wire_timestamp as runtime_wire_timestamp

    return runtime_wire_timestamp()
