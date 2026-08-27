"""Session-bound Course Mode embodied-action dispatcher."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from core.lesson.course_orchestrator import CourseDecision
from core.lesson.embodied_intent import EmbodiedIntent

LISTEN_WINDOW_POLICY = "complete_before_listening"
FOCUS_BY_INTENT = {
    EmbodiedIntent.PRESENT_CENTER: "focus.center.primary",
    EmbodiedIntent.PRESENT_LEFT: "focus.left.choice",
    EmbodiedIntent.PRESENT_RIGHT: "focus.right.choice",
}
DEFAULT_FOCUS_REGION = "focus.center.primary"
ACK_OUTCOMES = frozenset({"applied", "degraded", "rejected"})


class EmbodiedDispatchStatus(str, Enum):
    PENDING = "pending"
    APPLIED = "applied"
    DEGRADED = "degraded"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    TIMED_OUT = "timed_out"
    UNSUPPORTED = "unsupported"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class EmbodiedDispatchResult:
    action_id: str
    generation: int
    status: EmbodiedDispatchStatus
    returned_to_rest: bool
    reduced_motion: bool
    sequence: int | None = None
    reason: str | None = None


class CourseEmbodiedDispatcher:
    """Own one embodied action without owning any semantic lesson state."""

    def __init__(
        self,
        *,
        assignment_id: str,
        session_id: str,
        step_id: Callable[[CourseDecision], str | None],
        features: Any,
        next_sequence: Callable[[], int],
        send_frame: Callable[[dict[str, Any]], Awaitable[None]],
        ack_timeout_sec: float = 2.0,
        settle_before_listen_sec: float = 0.25,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        settle_sleep: Callable[[float], Awaitable[None]] | None = None,
        snapshot: dict[str, Any] | None = None,
        before_send: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self.assignment_id = assignment_id
        self.session_id = session_id
        self._step_id = step_id
        self._next_sequence = next_sequence
        self._send_frame = send_frame
        self.ack_timeout_sec = max(0.0, float(ack_timeout_sec))
        self.settle_before_listen_sec = max(0.0, float(settle_before_listen_sec))
        self._sleep = sleep
        self._settle_sleep = settle_sleep or sleep
        self._before_send = before_send
        self._generation = 0
        self._in_flight: EmbodiedDispatchResult | None = None
        self._waiters: dict[str, asyncio.Future[EmbodiedDispatchResult]] = {}
        self._timeout_tasks: dict[str, asyncio.Task[None]] = {}
        self._results: dict[str, EmbodiedDispatchResult] = {}
        self._action_step_ids: dict[str, str | None] = {}
        self._supported, self.reduced_motion = self._capability(features)
        if snapshot is not None:
            self._restore(snapshot)

    @staticmethod
    def _capability(features: Any) -> tuple[bool, bool]:
        course = features.get("lessonCourseMode") if isinstance(features, dict) else None
        if not isinstance(course, dict):
            return False, False
        supported = course.get("version") == 2 and course.get("embodiedActions") is True
        return supported, bool(supported and course.get("reducedMotion") is True)

    @property
    def in_flight(self) -> EmbodiedDispatchResult | None:
        return self._in_flight

    def result(self, action_id: str) -> EmbodiedDispatchResult | None:
        return self._results.get(action_id)

    def snapshot(self) -> dict[str, Any]:
        results = list(self._results.values())
        if self._in_flight is not None:
            results.append(
                EmbodiedDispatchResult(
                    action_id=self._in_flight.action_id,
                    generation=self._in_flight.generation,
                    status=EmbodiedDispatchStatus.CANCELLED,
                    returned_to_rest=False,
                    reduced_motion=self._in_flight.reduced_motion,
                    sequence=self._in_flight.sequence,
                    reason="transportInterrupted",
                )
            )
        return {
            "snapshotVersion": 1,
            "sessionId": self.session_id,
            "generation": self._generation,
            "results": [
                {
                    "actionId": result.action_id,
                    "generation": result.generation,
                    "status": result.status.value,
                    "returnedToRest": result.returned_to_rest,
                    "reducedMotion": result.reduced_motion,
                    "sequence": result.sequence,
                    "reason": result.reason,
                }
                for result in results
            ],
        }

    def _restore(self, snapshot: dict[str, Any]) -> None:
        if (
            snapshot.get("snapshotVersion") != 1
            or snapshot.get("sessionId") != self.session_id
            or type(snapshot.get("generation")) is not int
            or snapshot["generation"] < 0
            or not isinstance(snapshot.get("results"), list)
        ):
            raise ValueError("invalid embodied dispatcher snapshot")
        self._generation = snapshot["generation"]
        for item in snapshot["results"]:
            result = EmbodiedDispatchResult(
                action_id=item["actionId"],
                generation=item["generation"],
                status=EmbodiedDispatchStatus(item["status"]),
                returned_to_rest=item["returnedToRest"],
                reduced_motion=item["reducedMotion"],
                sequence=item["sequence"],
                reason=item["reason"],
            )
            self._results[result.action_id] = result

    async def dispatch(
        self,
        decision: CourseDecision,
        *,
        retry_transport_interrupted: bool = False,
    ) -> EmbodiedDispatchResult:
        if not isinstance(decision, CourseDecision):
            raise TypeError("dispatcher requires an authoritative CourseDecision")
        if not isinstance(decision.embodied_intent, EmbodiedIntent):
            raise ValueError("decision must use a frozen EmbodiedIntent")
        action_id = f"{self.session_id}:{decision.decision_id}"
        prior = self._results.get(action_id)
        if prior is not None:
            if not (
                retry_transport_interrupted
                and prior.status is EmbodiedDispatchStatus.CANCELLED
                and prior.reason == "transportInterrupted"
            ):
                return prior
            del self._results[action_id]
        if self._in_flight is not None:
            self._complete(
                self._in_flight.action_id,
                EmbodiedDispatchStatus.SUPERSEDED,
                returned_to_rest=False,
                reason="newerDecision",
            )
        self._generation += 1
        if not self._supported:
            result = EmbodiedDispatchResult(
                action_id,
                self._generation,
                EmbodiedDispatchStatus.UNSUPPORTED,
                returned_to_rest=True,
                reduced_motion=False,
                reason="embodiedActionsUnsupported",
            )
            self._results[action_id] = result
            return result
        sequence = self._next_sequence()
        step_id = self._step_id(decision)
        pending = EmbodiedDispatchResult(
            action_id,
            self._generation,
            EmbodiedDispatchStatus.PENDING,
            returned_to_rest=False,
            reduced_motion=self.reduced_motion,
            sequence=sequence,
        )
        waiter = asyncio.get_running_loop().create_future()
        self._waiters[action_id] = waiter
        self._action_step_ids[action_id] = step_id
        self._in_flight = pending
        frame = {
            "type": "lesson_embodied_action",
            "assignmentId": self.assignment_id,
            "sessionId": self.session_id,
            "stepId": step_id,
            "sequence": sequence,
            "body": {
                "actionId": action_id,
                "actionGeneration": self._generation,
                "intent": decision.embodied_intent.value,
                "visualFocusRegion": decision.visual_focus_region
                or FOCUS_BY_INTENT.get(
                    decision.embodied_intent,
                    DEFAULT_FOCUS_REGION,
                ),
                "listenWindowPolicy": LISTEN_WINDOW_POLICY,
            },
        }
        if self._before_send is not None:
            try:
                await self._before_send()
            except Exception:
                return self._complete(
                    action_id,
                    EmbodiedDispatchStatus.REJECTED,
                    returned_to_rest=False,
                    reason="snapshotPersistFailed",
                )
        try:
            await self._send_frame(frame)
        except Exception:
            return self._complete(
                action_id,
                EmbodiedDispatchStatus.REJECTED,
                returned_to_rest=False,
                reason="sendFailed",
            )
        self._timeout_tasks[action_id] = asyncio.create_task(self._timeout(action_id, self._generation))
        return pending

    async def _timeout(self, action_id: str, generation: int) -> None:
        try:
            await self._sleep(self.ack_timeout_sec)
        except asyncio.CancelledError:
            return
        active = self._in_flight
        if active is None or active.action_id != action_id or active.generation != generation:
            return
        self._complete(
            action_id,
            EmbodiedDispatchStatus.TIMED_OUT,
            returned_to_rest=False,
            reason="ackTimeout",
        )

    async def wait(self, action_id: str) -> EmbodiedDispatchResult:
        result = self._results.get(action_id)
        if result is not None:
            return result
        waiter = self._waiters.get(action_id)
        if waiter is None:
            raise KeyError(action_id)
        return await asyncio.shield(waiter)

    async def wait_until_listening_safe(self, action_id: str) -> EmbodiedDispatchResult:
        result = await self.wait(action_id)
        if result.returned_to_rest and self.settle_before_listen_sec:
            await self._settle_sleep(self.settle_before_listen_sec)
        return result

    async def cancel(self, reason: str) -> EmbodiedDispatchResult | None:
        active = self._in_flight
        if active is None:
            return None
        frame = {
            "type": "lesson_embodied_cancel",
            "assignmentId": self.assignment_id,
            "sessionId": self.session_id,
            "stepId": self._action_step_ids.get(active.action_id),
            "sequence": self._next_sequence(),
            "body": {
                "actionId": active.action_id,
                "actionGeneration": active.generation,
            },
        }
        try:
            await self._send_frame(frame)
        except Exception:
            reason = f"{reason}:cancelSendFailed"
        return self._complete(
            active.action_id,
            EmbodiedDispatchStatus.CANCELLED,
            returned_to_rest=False,
            reason=reason,
        )

    @staticmethod
    def is_embodied_ack(message: Any) -> bool:
        body = message.get("body") if isinstance(message, dict) else None
        return isinstance(body, dict) and isinstance(body.get("embodiedAction"), dict)

    async def handle_ack(self, message: dict[str, Any]) -> bool:
        body = message.get("body") if isinstance(message, dict) else None
        embodied = body.get("embodiedAction") if isinstance(body, dict) else None
        if not isinstance(body, dict) or not isinstance(embodied, dict):
            return False
        active = self._in_flight
        if active is None:
            return True
        if (
            set(message)
            != {
                "type",
                "assignmentId",
                "sessionId",
                "stepId",
                "sequence",
                "body",
            }
            or set(body) != {"acks", "embodiedAction"}
            or set(embodied)
            != {
                "actionId",
                "actionGeneration",
                "outcome",
                "returnedToRest",
            }
            or message.get("type") != "lesson_ack"
            or message.get("assignmentId") != self.assignment_id
            or message.get("sessionId") != self.session_id
            or message.get("stepId") != self._action_step_ids.get(active.action_id)
            or type(message.get("sequence")) is not int
            or type(body.get("acks")) is not int
            or body.get("acks") != active.sequence
            or embodied.get("actionId") != active.action_id
            or type(embodied.get("actionGeneration")) is not int
            or embodied.get("actionGeneration") != active.generation
            or embodied.get("outcome") not in ACK_OUTCOMES
            or type(embodied.get("returnedToRest")) is not bool
        ):
            return True
        self._complete(
            active.action_id,
            EmbodiedDispatchStatus(embodied["outcome"]),
            returned_to_rest=embodied["returnedToRest"],
        )
        return True

    def teardown(self, reason: str) -> None:
        if self._in_flight is None:
            return
        self._complete(
            self._in_flight.action_id,
            EmbodiedDispatchStatus.CANCELLED,
            returned_to_rest=False,
            reason=str(reason or "teardown")[:64],
        )

    def _complete(
        self,
        action_id: str,
        status: EmbodiedDispatchStatus,
        *,
        returned_to_rest: bool,
        reason: str | None = None,
    ) -> EmbodiedDispatchResult:
        pending = self._in_flight
        if pending is None or pending.action_id != action_id:
            return self._results[action_id]
        result = EmbodiedDispatchResult(
            action_id=pending.action_id,
            generation=pending.generation,
            status=status,
            returned_to_rest=returned_to_rest,
            reduced_motion=pending.reduced_motion,
            sequence=pending.sequence,
            reason=reason,
        )
        self._in_flight = None
        self._results[action_id] = result
        timeout = self._timeout_tasks.pop(action_id, None)
        if timeout is not None and timeout is not asyncio.current_task():
            timeout.cancel()
        waiter = self._waiters.pop(action_id, None)
        if waiter is not None and not waiter.done():
            waiter.set_result(result)
        return result
