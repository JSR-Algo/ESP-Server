from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.lesson.course_orchestrator import CourseDecision, SessionState
from core.lesson.course_snapshot_store import MemoryCourseModeSnapshotStore
from core.lesson.embodied_dispatcher import (
    ACK_OUTCOMES,
    CourseEmbodiedDispatcher,
    EmbodiedDispatchStatus,
)
from core.lesson.embodied_intent import EmbodiedIntent
from core.lesson.runtime import LessonRuntime
from core.voice.session_provider.google_live import GoogleLiveProvider
from plugins_func.functions.lesson_conversation import COURSE_MODE_TOOL_SPECS

FIXTURES = Path(__file__).parent / "fixtures" / "course-mode"
WIRE_CONTRACT = FIXTURES / "lesson-embodied-action-wire-contract.json"


def decision(
    decision_id: str = "course-decision-1",
    intent: EmbodiedIntent = EmbodiedIntent.PRESENT_LEFT,
) -> CourseDecision:
    return CourseDecision(
        decision_id=decision_id,
        accepted=True,
        next_state=SessionState.WORD_ACTIVE,
        action="TEST",
        acknowledgment_intent="acknowledge_child",
        teaching_intent=None,
        question_intent=None,
        embodied_intent=intent,
        may_model_target=False,
        evidence_event=None,
    )


def capability(*, embodied: bool = True, reduced: bool = False) -> dict:
    return {
        "lessonCourseMode": {
            "version": 2,
            "embodiedActions": embodied,
            "reducedMotion": reduced,
            "faces": ["neutral", "happy", "thinking", "relaxed"],
        }
    }


def test_task03_wire_contract_artifact_freezes_exact_accept_and_reject_examples() -> None:
    artifact = json.loads(WIRE_CONTRACT.read_text(encoding="utf-8"))

    assert artifact["contractVersion"] == "lesson-embodied-action.v1"
    accepted = artifact["acceptedFrame"]
    assert list(accepted) == [
        "type",
        "assignmentId",
        "sessionId",
        "stepId",
        "sequence",
        "body",
    ]
    assert list(accepted["body"]) == [
        "actionId",
        "actionGeneration",
        "intent",
        "visualFocusRegion",
        "listenWindowPolicy",
    ]
    cancel = artifact["acceptedCancelFrame"]
    assert list(cancel) == [
        "type",
        "assignmentId",
        "sessionId",
        "stepId",
        "sequence",
        "body",
    ]
    assert cancel["type"] == "lesson_embodied_cancel"
    assert list(cancel["body"]) == ["actionId", "actionGeneration"]
    assert artifact["acceptedAck"]["body"]["embodiedAction"]["outcome"] == "applied"
    assert set(artifact["ackOutcomes"]) == ACK_OUTCOMES
    assert set(artifact["localTerminalOutcomes"]) == {"superseded", "timed_out"}
    assert {row["reason"] for row in artifact["rejectedFrames"]} >= {
        "unknownIntent",
        "rawServoField",
        "staleSession",
        "staleGeneration",
        "assessmentAlreadyOpen",
    }
    serialized = json.dumps(artifact).casefold()
    assert '"angle"' not in json.dumps(artifact["acceptedFrame"]).casefold()
    assert '"percent"' not in json.dumps(artifact["acceptedFrame"]).casefold()
    assert '"angle"' not in json.dumps(cancel).casefold()
    assert '"percent"' not in json.dumps(cancel).casefold()
    assert "complete_before_listening" in serialized


def test_model_authored_course_tools_expose_no_raw_motion_fields() -> None:
    schemas = json.dumps(COURSE_MODE_TOOL_SPECS, sort_keys=True).casefold()
    for forbidden in ("servo", "joint", "angle", "percent", "speed"):
        assert forbidden not in schemas


class Harness:
    def __init__(self, *, features: dict | None = None, timeout: float = 0.01) -> None:
        self.frames: list[dict] = []
        self.sequence = 16
        self.dispatcher = CourseEmbodiedDispatcher(
            assignment_id="assignment-1",
            session_id="session-1",
            step_id=lambda _decision: "cat-meaning-left-right-01",
            features=features if features is not None else capability(),
            next_sequence=self.next_sequence,
            send_frame=self.send_frame,
            ack_timeout_sec=timeout,
            settle_before_listen_sec=0,
        )

    def next_sequence(self) -> int:
        self.sequence += 1
        return self.sequence

    async def send_frame(self, frame: dict) -> None:
        self.frames.append(frame)

    def ack(
        self,
        *,
        outcome: str = "applied",
        returned_to_rest: bool = True,
        action_id: str | None = None,
        generation: int | None = None,
        session_id: str = "session-1",
        acks: int | None = None,
    ) -> dict:
        sent = self.frames[-1]
        body = sent["body"]
        return {
            "type": "lesson_ack",
            "assignmentId": "assignment-1",
            "sessionId": session_id,
            "stepId": sent["stepId"],
            "sequence": 91,
            "body": {
                "acks": sent["sequence"] if acks is None else acks,
                "embodiedAction": {
                    "actionId": action_id or body["actionId"],
                    "actionGeneration": (body["actionGeneration"] if generation is None else generation),
                    "outcome": outcome,
                    "returnedToRest": returned_to_rest,
                },
            },
        }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("intent", "focus"),
    [
        (EmbodiedIntent.PRESENT_CENTER, "focus.center.primary"),
        (EmbodiedIntent.PRESENT_LEFT, "focus.left.choice"),
        (EmbodiedIntent.PRESENT_RIGHT, "focus.right.choice"),
    ],
)
async def test_wire_frame_uses_exact_authored_focus_and_contains_no_raw_servo_values(
    intent: EmbodiedIntent,
    focus: str,
) -> None:
    visual = json.loads((FIXTURES / "renderer-v4-visual-layout.json").read_text(encoding="utf-8"))
    assert focus in visual["focusAnchors"]
    harness = Harness()

    receipt = await harness.dispatcher.dispatch(decision(intent=intent))

    assert receipt.status is EmbodiedDispatchStatus.PENDING
    assert harness.frames == [
        {
            "type": "lesson_embodied_action",
            "assignmentId": "assignment-1",
            "sessionId": "session-1",
            "stepId": "cat-meaning-left-right-01",
            "sequence": 17,
            "body": {
                "actionId": "session-1:course-decision-1",
                "actionGeneration": 1,
                "intent": intent.value,
                "visualFocusRegion": focus,
                "listenWindowPolicy": "complete_before_listening",
            },
        }
    ]
    wire = json.dumps(harness.frames[0], sort_keys=True).casefold()
    for forbidden in ("servo", "joint", "angle", "percent", "speed"):
        assert forbidden not in wire


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["applied", "degraded", "rejected"])
async def test_ack_outcomes_complete_the_one_inflight_action(outcome: str) -> None:
    harness = Harness()
    receipt = await harness.dispatcher.dispatch(decision())

    assert await harness.dispatcher.handle_ack(harness.ack(outcome=outcome)) is True
    result = await harness.dispatcher.wait(receipt.action_id)

    assert result.status.value == outcome
    assert result.returned_to_rest is True
    assert harness.dispatcher.in_flight is None


@pytest.mark.asyncio
async def test_timeout_is_terminal_and_duplicate_decision_never_replays_motion() -> None:
    harness = Harness(timeout=0)
    first = await harness.dispatcher.dispatch(decision())

    timed_out = await harness.dispatcher.wait(first.action_id)
    duplicate = await harness.dispatcher.dispatch(decision())

    assert timed_out.status is EmbodiedDispatchStatus.TIMED_OUT
    assert duplicate == timed_out
    assert len(harness.frames) == 1


@pytest.mark.asyncio
async def test_stale_session_generation_and_sequence_acks_are_ignored() -> None:
    harness = Harness()
    receipt = await harness.dispatcher.dispatch(decision())

    assert await harness.dispatcher.handle_ack(harness.ack(session_id="old-session")) is True
    assert await harness.dispatcher.handle_ack(harness.ack(generation=0)) is True
    assert await harness.dispatcher.handle_ack(harness.ack(acks=999)) is True
    assert harness.dispatcher.in_flight is not None
    assert await harness.dispatcher.handle_ack(harness.ack()) is True
    assert (await harness.dispatcher.wait(receipt.action_id)).status is EmbodiedDispatchStatus.APPLIED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [("acks", True), ("actionGeneration", True)],
)
async def test_boolean_ack_integers_are_rejected(field: str, value: bool) -> None:
    harness = Harness()
    receipt = await harness.dispatcher.dispatch(decision())
    ack = harness.ack()
    target = ack["body"] if field == "acks" else ack["body"]["embodiedAction"]
    target[field] = value

    assert await harness.dispatcher.handle_ack(ack) is True
    assert harness.dispatcher.in_flight is not None
    assert await harness.dispatcher.handle_ack(harness.ack()) is True
    assert (await harness.dispatcher.wait(receipt.action_id)).status is EmbodiedDispatchStatus.APPLIED


@pytest.mark.asyncio
async def test_new_decision_supersedes_old_action_with_monotonic_generation() -> None:
    harness = Harness()
    old = await harness.dispatcher.dispatch(decision("course-decision-1"))
    new = await harness.dispatcher.dispatch(decision("course-decision-2", EmbodiedIntent.PRESENT_RIGHT))

    assert (await harness.dispatcher.wait(old.action_id)).status is EmbodiedDispatchStatus.SUPERSEDED
    assert new.generation == 2
    assert [frame["body"]["actionGeneration"] for frame in harness.frames] == [1, 2]
    assert await harness.dispatcher.handle_ack(harness.ack()) is True
    assert (await harness.dispatcher.wait(new.action_id)).status is EmbodiedDispatchStatus.APPLIED


@pytest.mark.asyncio
async def test_capability_negotiation_is_explicit_and_reduced_motion_stays_named() -> None:
    unsupported = Harness(features=capability(embodied=False))
    unsupported_result = await unsupported.dispatcher.dispatch(decision())

    assert unsupported_result.status is EmbodiedDispatchStatus.UNSUPPORTED
    assert unsupported.frames == []

    reduced = Harness(features=capability(reduced=True))
    pending = await reduced.dispatcher.dispatch(decision())
    assert pending.reduced_motion is True
    assert reduced.frames[0]["body"]["intent"] == "PRESENT_LEFT"
    assert not any(key in reduced.frames[0]["body"] for key in ("angle", "percent", "speed"))


@pytest.mark.asyncio
async def test_send_failure_degrades_without_blocking_the_semantic_turn() -> None:
    async def fail(_frame: dict) -> None:
        raise OSError("socket closed")

    dispatcher = CourseEmbodiedDispatcher(
        assignment_id="assignment-1",
        session_id="session-1",
        step_id=lambda _decision: "cat",
        features=capability(),
        next_sequence=lambda: 1,
        send_frame=fail,
    )

    result = await dispatcher.dispatch(decision())

    assert result.status is EmbodiedDispatchStatus.REJECTED
    assert result.reason == "sendFailed"
    assert result.returned_to_rest is False


@pytest.mark.asyncio
async def test_disconnect_teardown_cancels_without_sending_or_replaying() -> None:
    harness = Harness()
    receipt = await harness.dispatcher.dispatch(decision())

    harness.dispatcher.teardown("disconnect")
    result = await harness.dispatcher.wait(receipt.action_id)

    assert result.status is EmbodiedDispatchStatus.CANCELLED
    assert len(harness.frames) == 1
    assert await harness.dispatcher.dispatch(decision()) == result


@pytest.mark.asyncio
async def test_snapshot_restore_preserves_generation_and_consumed_action_identity() -> None:
    first = Harness()
    receipt = await first.dispatcher.dispatch(decision())
    assert await first.dispatcher.handle_ack(first.ack()) is True
    applied = await first.dispatcher.wait(receipt.action_id)

    restored = Harness()
    restored.dispatcher = CourseEmbodiedDispatcher(
        assignment_id="assignment-1",
        session_id="session-1",
        step_id=lambda _decision: "cat-meaning-left-right-01",
        features=capability(),
        next_sequence=restored.next_sequence,
        send_frame=restored.send_frame,
        snapshot=first.dispatcher.snapshot(),
    )

    assert await restored.dispatcher.dispatch(decision()) == applied
    next_result = await restored.dispatcher.dispatch(decision("course-decision-2", EmbodiedIntent.PRESENT_RIGHT))
    assert next_result.generation == 2
    assert len(restored.frames) == 1


@pytest.mark.asyncio
async def test_reconnect_snapshot_never_replays_an_inflight_action() -> None:
    first = Harness()
    pending = await first.dispatcher.dispatch(decision())

    restored = Harness()
    restored.dispatcher = CourseEmbodiedDispatcher(
        assignment_id="assignment-1",
        session_id="session-1",
        step_id=lambda _decision: "cat-meaning-left-right-01",
        features=capability(),
        next_sequence=restored.next_sequence,
        send_frame=restored.send_frame,
        snapshot=first.dispatcher.snapshot(),
    )
    replay = await restored.dispatcher.dispatch(decision())

    assert replay.action_id == pending.action_id
    assert replay.status is EmbodiedDispatchStatus.CANCELLED
    assert replay.reason == "transportInterrupted"
    assert restored.frames == []


@pytest.mark.asyncio
async def test_explicit_plan_retry_rearms_a_transport_interrupted_action() -> None:
    first = Harness()
    pending = await first.dispatcher.dispatch(decision())

    restored = Harness()
    restored.dispatcher = CourseEmbodiedDispatcher(
        assignment_id="assignment-1",
        session_id="session-1",
        step_id=lambda _decision: "cat-meaning-left-right-01",
        features=capability(),
        next_sequence=restored.next_sequence,
        send_frame=restored.send_frame,
        snapshot=first.dispatcher.snapshot(),
    )

    retried = await restored.dispatcher.dispatch(
        decision(),
        retry_transport_interrupted=True,
    )

    assert retried.action_id == pending.action_id
    assert retried.status is EmbodiedDispatchStatus.PENDING
    assert retried.generation == 2
    assert len(restored.frames) == 1


@pytest.mark.asyncio
async def test_dispatcher_accepts_only_authoritative_decisions_and_frozen_intents() -> None:
    harness = Harness()
    with pytest.raises(TypeError):
        await harness.dispatcher.dispatch({"decisionId": "model-authored", "angle": 90})

    forged = replace(decision(), embodied_intent="PRESENT_LEFT")
    with pytest.raises(ValueError, match="frozen EmbodiedIntent"):
        await harness.dispatcher.dispatch(forged)


def semantic_contract() -> dict:
    return json.loads((FIXTURES / "course-mode-pilot-cat-ball.json").read_text(encoding="utf-8"))


class RuntimeVoice:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def open_lesson_child_response_window(self) -> bool:
        self.events.append("assessment-open")
        return True

    def close_lesson_child_response_window(self) -> None:
        self.events.append("assessment-close")


class RuntimeConn:
    config = {
        "lesson": {
            "runtime_enabled": True,
            "course_mode_v2_enabled": True,
            "rollout_device_allowlist": ["robot-01"],
        }
    }
    device_id = "robot-01"
    logger = None
    headers = {}

    def __init__(self, events: list[str], *, features: dict | None = None) -> None:
        self.features = features if features is not None else capability()
        self.voice_provider = RuntimeVoice(events)


class RuntimeForwarder:
    def enqueue(self, _batch, **_kwargs) -> bool:
        return True

    async def aclose(self) -> None:
        return None


class RuntimeAssetCache:
    async def aclose(self) -> None:
        return None


def response_plan(runtime: LessonRuntime, decision_payload: dict) -> dict:
    return {
        "lessonSessionId": runtime.session_id,
        "turnSequenceId": 2,
        "observationId": "opening-plan",
        "planId": "opening-plan",
        "decisionId": decision_payload["decisionId"],
        "acknowledgment": "Hello.",
        "relation": "",
        "guidance": "",
        "invitation": "Ready?",
        "questionCount": 1,
        "embodiedIntent": decision_payload["embodiedIntent"],
        "targetFactsUsed": [],
        "praiseLevel": "engagement",
        "safetyMode": False,
        "normalMiss": False,
    }


async def opening_decision(runtime: LessonRuntime) -> dict:
    return await runtime.course_continue(
        {
            "lessonSessionId": runtime.session_id,
            "turnSequenceId": 1,
            "observationId": "opening-decision",
        }
    )


def runtime_ack(frame: dict, *, outcome: str = "applied") -> dict:
    return {
        "type": "lesson_ack",
        "assignmentId": frame["assignmentId"],
        "sessionId": frame["sessionId"],
        "stepId": frame["stepId"],
        "sequence": 1,
        "body": {
            "acks": frame["sequence"],
            "embodiedAction": {
                "actionId": frame["body"]["actionId"],
                "actionGeneration": frame["body"]["actionGeneration"],
                "outcome": outcome,
                "returnedToRest": True,
            },
        },
    }


@pytest.mark.asyncio
async def test_runtime_emits_embodied_frame_before_delivery_and_opens_assessment_only_after_ack_and_settle() -> None:
    events: list[str] = []
    sent: list[dict] = []

    async def send(payload: str) -> None:
        frame = json.loads(payload)
        sent.append(frame)
        events.append(f"send:{frame['type']}")

    runtime = LessonRuntime(
        RuntimeConn(events),
        assignment={"assignmentId": "assignment-1", "lessonId": "lesson-1"},
        manifest={"courseModeContract": semantic_contract()},
        asset_cache=RuntimeAssetCache(),
        forwarder=RuntimeForwarder(),
        send=send,
    )
    runtime.state = "RUNNING"
    runtime._step_id = "cat-discover-center-01"
    runtime.course_embodied_dispatcher.ack_timeout_sec = 10

    async def settle(seconds: float) -> None:
        events.append(f"settle:{seconds}")

    runtime.course_embodied_dispatcher._settle_sleep = settle
    decision_payload = await opening_decision(runtime)
    plan = response_plan(runtime, decision_payload)

    applied = await runtime.course_apply_response_plan(plan)
    commit = asyncio.create_task(runtime.commit_course_response_plan(plan))
    await asyncio.sleep(0)

    assert applied["accepted"] is True
    assert [frame["type"] for frame in sent] == ["lesson_embodied_action"]
    assert events == ["assessment-close", "send:lesson_embodied_action"]
    assert commit.done() is False

    semantic_before_ack = runtime.course_mode.orchestrator.snapshot()
    await runtime.on_lesson_ack(runtime_ack(sent[0]))
    assert await commit is True

    assert runtime.course_mode.orchestrator.snapshot() == semantic_before_ack
    assert runtime._last_inbound_sequence == 1
    assert events == [
        "assessment-close",
        "send:lesson_embodied_action",
        "settle:0.25",
        "assessment-open",
    ]
    assert runtime.course_assessment_window_open is True
    assert GoogleLiveProvider._lesson_child_response_window_active(
        SimpleNamespace(conn=SimpleNamespace(lesson_runtime=runtime)),
        require_audio_window=False,
        require_explicit_runtime_window=True,
    ) is True


@pytest.mark.asyncio
async def test_provisional_plan_retries_interrupted_embodied_action_after_reconnect() -> None:
    store = MemoryCourseModeSnapshotStore()
    first_sent: list[dict] = []

    async def first_send(payload: str) -> None:
        first_sent.append(json.loads(payload))

    first = LessonRuntime(
        RuntimeConn([]),
        assignment={"assignmentId": "assignment-1", "lessonId": "lesson-1"},
        manifest={"courseModeContract": semantic_contract()},
        asset_cache=RuntimeAssetCache(),
        forwarder=RuntimeForwarder(),
        send=first_send,
        course_mode_snapshot_store=store,
        course_mode_snapshot_device_id="device-1",
    )
    first.state = "RUNNING"
    first._step_id = "cat-discover-center-01"
    first.course_embodied_dispatcher.ack_timeout_sec = 10
    plan = response_plan(first, await opening_decision(first))

    assert (await first.course_apply_response_plan(plan))["accepted"] is True
    snapshot = await store.load("device-1", "assignment-1")
    assert len(first_sent) == 1
    assert snapshot["embodiedDispatcher"]["results"][0]["reason"] == "transportInterrupted"

    restored_events: list[str] = []
    restored_sent: list[dict] = []

    async def restored_send(payload: str) -> None:
        restored_sent.append(json.loads(payload))

    restored = LessonRuntime(
        RuntimeConn(restored_events),
        assignment={"assignmentId": "assignment-1", "lessonId": "lesson-1"},
        manifest={"courseModeContract": semantic_contract()},
        asset_cache=RuntimeAssetCache(),
        forwarder=RuntimeForwarder(),
        send=restored_send,
        course_mode_snapshot_store=store,
        course_mode_snapshot_device_id="device-1",
        course_mode_snapshot=snapshot,
    )
    restored.state = "RUNNING"
    restored._step_id = "cat-discover-center-01"
    restored.course_embodied_dispatcher.ack_timeout_sec = 10

    assert (await restored.course_apply_response_plan(plan))["accepted"] is True
    assert len(restored_sent) == 1
    assert restored_sent[0]["body"]["actionGeneration"] == 2

    commit = asyncio.create_task(restored.commit_course_response_plan(plan))
    await asyncio.sleep(0)
    await restored.on_lesson_ack(runtime_ack(restored_sent[0]))
    assert await commit is True
    assert restored.course_assessment_window_open is True
    first.course_embodied_dispatcher.teardown("test-complete")


@pytest.mark.asyncio
async def test_timeout_does_not_block_voice_fallback_or_replay_on_commit_retry() -> None:
    events: list[str] = []
    sent: list[dict] = []

    async def send(payload: str) -> None:
        sent.append(json.loads(payload))

    runtime = LessonRuntime(
        RuntimeConn(events),
        assignment={"assignmentId": "assignment-1", "lessonId": "lesson-1"},
        manifest={"courseModeContract": semantic_contract()},
        asset_cache=RuntimeAssetCache(),
        forwarder=RuntimeForwarder(),
        send=send,
    )
    runtime.state = "RUNNING"
    runtime._step_id = "cat-discover-center-01"
    runtime.course_embodied_dispatcher.ack_timeout_sec = 0
    runtime.course_embodied_dispatcher.settle_before_listen_sec = 0
    decision_payload = await opening_decision(runtime)
    plan = response_plan(runtime, decision_payload)

    assert (await runtime.course_apply_response_plan(plan))["accepted"] is True
    assert await runtime.commit_course_response_plan(plan) is True
    assert await runtime.commit_course_response_plan(plan) is False

    assert len(sent) == 1
    assert runtime.course_assessment_window_open is False
    assert runtime.last_embodied_result.status is EmbodiedDispatchStatus.TIMED_OUT


@pytest.mark.asyncio
async def test_assessment_does_not_open_when_firmware_has_not_returned_to_rest() -> None:
    events: list[str] = []
    sent: list[dict] = []

    async def send(payload: str) -> None:
        sent.append(json.loads(payload))

    runtime = LessonRuntime(
        RuntimeConn(events),
        assignment={"assignmentId": "assignment-1", "lessonId": "lesson-1"},
        manifest={"courseModeContract": semantic_contract()},
        asset_cache=RuntimeAssetCache(),
        forwarder=RuntimeForwarder(),
        send=send,
    )
    runtime.state = "RUNNING"
    runtime._step_id = "cat-discover-center-01"
    runtime.course_embodied_dispatcher.ack_timeout_sec = 10
    decision_payload = await opening_decision(runtime)
    plan = response_plan(runtime, decision_payload)
    assert (await runtime.course_apply_response_plan(plan))["accepted"] is True

    commit = asyncio.create_task(runtime.commit_course_response_plan(plan))
    await asyncio.sleep(0)
    await runtime.on_lesson_ack(
        {
            **runtime_ack(sent[0]),
            "body": {
                **runtime_ack(sent[0])["body"],
                "embodiedAction": {
                    **runtime_ack(sent[0])["body"]["embodiedAction"],
                    "returnedToRest": False,
                },
            },
        }
    )

    assert await commit is True
    assert runtime.course_assessment_window_open is False
    assert "assessment-open" not in events


@pytest.mark.asyncio
async def test_disconnect_during_settle_invalidates_pending_assessment_open() -> None:
    events: list[str] = []
    sent: list[dict] = []
    settle = asyncio.Event()

    async def send(payload: str) -> None:
        sent.append(json.loads(payload))

    runtime = LessonRuntime(
        RuntimeConn(events),
        assignment={"assignmentId": "assignment-1", "lessonId": "lesson-1"},
        manifest={"courseModeContract": semantic_contract()},
        asset_cache=RuntimeAssetCache(),
        forwarder=RuntimeForwarder(),
        send=send,
    )
    runtime.state = "RUNNING"
    runtime._step_id = "cat-discover-center-01"
    runtime.course_embodied_dispatcher.ack_timeout_sec = 10
    runtime.course_embodied_dispatcher._settle_sleep = lambda _seconds: settle.wait()
    decision_payload = await opening_decision(runtime)
    plan = response_plan(runtime, decision_payload)
    assert (await runtime.course_apply_response_plan(plan))["accepted"] is True

    commit = asyncio.create_task(runtime.commit_course_response_plan(plan))
    await asyncio.sleep(0)
    await runtime.on_lesson_ack(runtime_ack(sent[0]))
    await asyncio.sleep(0)
    await runtime.on_disconnect()
    settle.set()

    assert await commit is True
    assert runtime.course_assessment_window_open is False
    assert runtime._child_response_window_open is False
    assert "assessment-open" not in events


@pytest.mark.asyncio
async def test_commit_persistence_failure_never_opens_assessment_window() -> None:
    class ToggleStore(MemoryCourseModeSnapshotStore):
        fail = False

        async def store(self, *args, **kwargs) -> None:
            if self.fail:
                raise OSError("snapshot unavailable")
            await super().store(*args, **kwargs)

    events: list[str] = []
    sent: list[dict] = []
    settle = asyncio.Event()
    store = ToggleStore()

    async def send(payload: str) -> None:
        sent.append(json.loads(payload))

    runtime = LessonRuntime(
        RuntimeConn(events),
        assignment={"assignmentId": "assignment-1", "lessonId": "lesson-1"},
        manifest={"courseModeContract": semantic_contract()},
        asset_cache=RuntimeAssetCache(),
        forwarder=RuntimeForwarder(),
        send=send,
        course_mode_snapshot_store=store,
        course_mode_snapshot_device_id="robot-01",
    )
    runtime.state = "RUNNING"
    runtime._step_id = "cat-discover-center-01"
    runtime.course_embodied_dispatcher.ack_timeout_sec = 10
    runtime.course_embodied_dispatcher._settle_sleep = lambda _seconds: settle.wait()
    decision_payload = await opening_decision(runtime)
    plan = response_plan(runtime, decision_payload)
    assert (await runtime.course_apply_response_plan(plan))["accepted"] is True

    commit = asyncio.create_task(runtime.commit_course_response_plan(plan))
    await asyncio.sleep(0)
    await runtime.on_lesson_ack(runtime_ack(sent[0]))
    store.fail = True
    settle.set()

    assert await commit is False
    assert runtime.course_mode.response_plan_requires_commit(plan) is True
    assert runtime.course_assessment_window_open is False
    assert runtime._child_response_window_open is False
    assert "assessment-open" not in events


@pytest.mark.asyncio
async def test_older_commit_cannot_skip_waiting_for_a_newer_safety_action() -> None:
    sent: list[dict] = []

    async def send(payload: str) -> None:
        sent.append(json.loads(payload))

    runtime = LessonRuntime(
        RuntimeConn([]),
        assignment={"assignmentId": "assignment-1", "lessonId": "lesson-1"},
        manifest={"courseModeContract": semantic_contract()},
        asset_cache=RuntimeAssetCache(),
        forwarder=RuntimeForwarder(),
        send=send,
    )
    runtime.state = "RUNNING"
    runtime._step_id = "cat-discover-center-01"
    runtime.course_embodied_dispatcher.ack_timeout_sec = 10

    first_decision = await opening_decision(runtime)
    first_plan = response_plan(runtime, first_decision)
    assert (await runtime.course_apply_response_plan(first_plan))["accepted"] is True
    first_action_id = sent[0]["body"]["actionId"]

    old_wait_completed = asyncio.Event()
    release_old_commit = asyncio.Event()
    original_wait = runtime.course_embodied_dispatcher.wait_until_listening_safe

    async def hold_old_commit(action_id: str):
        result = await original_wait(action_id)
        if action_id == first_action_id:
            old_wait_completed.set()
            await release_old_commit.wait()
        return result

    runtime.course_embodied_dispatcher.wait_until_listening_safe = hold_old_commit
    first_commit = asyncio.create_task(runtime.commit_course_response_plan(first_plan))
    await asyncio.sleep(0)

    safety_decision = await runtime.course_observe_child(
        {
            "lessonSessionId": runtime.session_id,
            "turnSequenceId": 3,
            "observationId": "safety-observation",
            "semanticClass": "unknown",
            "speechClass": "not_applicable",
            "language": "vi",
            "intent": "answer",
            "engagement": "engaged",
            "safetyClass": "safety",
            "assessmentEligible": False,
            "confidenceBand": "high",
            "activityId": "cat-recall-visual-02",
            "contextId": "cat_primary_visual_recall",
            "robotAudioContaminated": False,
            "targetTextVisible": False,
        }
    )
    await old_wait_completed.wait()
    safety_plan = {
        **response_plan(runtime, safety_decision),
        "turnSequenceId": 4,
        "observationId": "safety-plan",
        "planId": "safety-plan",
        "acknowledgment": "Robot is here.",
        "invitation": "Do you want to stop?",
        "questionCount": 1,
        "targetFactsUsed": [],
        "safetyMode": True,
    }
    assert (await runtime.course_apply_response_plan(safety_plan))["accepted"] is True
    second_frame = sent[-1]
    assert second_frame["type"] == "lesson_embodied_action"

    release_old_commit.set()
    assert await first_commit is False

    second_commit = asyncio.create_task(runtime.commit_course_response_plan(safety_plan))
    await asyncio.sleep(0)
    assert second_commit.done() is False

    second_ack = runtime_ack(second_frame)
    second_ack["sequence"] = 2
    await runtime.on_lesson_ack(second_ack)
    assert await second_commit is True


@pytest.mark.asyncio
async def test_validated_safety_question_opens_a_rest_gated_response_window() -> None:
    events: list[str] = []
    sent: list[dict] = []

    async def send(payload: str) -> None:
        sent.append(json.loads(payload))

    runtime = LessonRuntime(
        RuntimeConn(events),
        assignment={"assignmentId": "assignment-1", "lessonId": "lesson-1"},
        manifest={"courseModeContract": semantic_contract()},
        asset_cache=RuntimeAssetCache(),
        forwarder=RuntimeForwarder(),
        send=send,
    )
    runtime.state = "RUNNING"
    runtime._step_id = "cat-discover-center-01"
    runtime.course_embodied_dispatcher.ack_timeout_sec = 10
    runtime.course_embodied_dispatcher.settle_before_listen_sec = 0

    safety_decision = await runtime.course_observe_child(
        {
            "lessonSessionId": runtime.session_id,
            "turnSequenceId": 1,
            "observationId": "safety-observation",
            "semanticClass": "unknown",
            "speechClass": "not_applicable",
            "language": "vi",
            "intent": "answer",
            "engagement": "engaged",
            "safetyClass": "safety",
            "assessmentEligible": False,
            "confidenceBand": "high",
            "activityId": "cat-recall-visual-02",
            "contextId": "cat_primary_visual_recall",
            "robotAudioContaminated": False,
            "targetTextVisible": False,
        }
    )
    assert safety_decision["questionIntent"] is None
    safety_plan = {
        **response_plan(runtime, safety_decision),
        "acknowledgment": "Robot is here.",
        "invitation": "Do you want to stop?",
        "targetFactsUsed": [],
        "safetyMode": True,
    }
    assert (await runtime.course_apply_response_plan(safety_plan))["accepted"] is True

    commit = asyncio.create_task(runtime.commit_course_response_plan(safety_plan))
    await asyncio.sleep(0)
    await runtime.on_lesson_ack(runtime_ack(sent[0]))

    assert await commit is True
    assert runtime.course_assessment_window_open is True
    assert "assessment-open" in events


@pytest.mark.asyncio
async def test_replayed_safety_observation_does_not_cancel_the_newer_action() -> None:
    sent: list[dict] = []

    async def send(payload: str) -> None:
        sent.append(json.loads(payload))

    runtime = LessonRuntime(
        RuntimeConn([]),
        assignment={"assignmentId": "assignment-1", "lessonId": "lesson-1"},
        manifest={"courseModeContract": semantic_contract()},
        asset_cache=RuntimeAssetCache(),
        forwarder=RuntimeForwarder(),
        send=send,
    )
    runtime.state = "RUNNING"
    runtime._step_id = "cat-discover-center-01"
    runtime.course_embodied_dispatcher.ack_timeout_sec = 10

    first_decision = await opening_decision(runtime)
    first_plan = response_plan(runtime, first_decision)
    assert (await runtime.course_apply_response_plan(first_plan))["accepted"] is True

    safety_observation = {
        "lessonSessionId": runtime.session_id,
        "turnSequenceId": 3,
        "observationId": "safety-observation",
        "semanticClass": "unknown",
        "speechClass": "not_applicable",
        "language": "vi",
        "intent": "answer",
        "engagement": "engaged",
        "safetyClass": "safety",
        "assessmentEligible": False,
        "confidenceBand": "high",
        "activityId": "cat-recall-visual-02",
        "contextId": "cat_primary_visual_recall",
        "robotAudioContaminated": False,
        "targetTextVisible": False,
    }
    safety_decision = await runtime.course_observe_child(safety_observation)
    safety_plan = {
        **response_plan(runtime, safety_decision),
        "turnSequenceId": 4,
        "observationId": "safety-plan",
        "planId": "safety-plan",
        "acknowledgment": "Robot is here.",
        "invitation": "Do you want to stop?",
        "targetFactsUsed": [],
        "safetyMode": True,
    }
    assert (await runtime.course_apply_response_plan(safety_plan))["accepted"] is True
    active = runtime.course_embodied_dispatcher.in_flight
    assert active is not None

    assert await runtime.course_observe_child(safety_observation) == safety_decision
    assert runtime.course_embodied_dispatcher.in_flight == active


@pytest.mark.asyncio
async def test_child_audio_inside_open_response_window_is_not_a_barge_in() -> None:
    events: list[str] = []
    sent: list[dict] = []

    async def send(payload: str) -> None:
        sent.append(json.loads(payload))

    runtime = LessonRuntime(
        RuntimeConn(events),
        assignment={"assignmentId": "assignment-1", "lessonId": "lesson-1"},
        manifest={"courseModeContract": semantic_contract()},
        asset_cache=RuntimeAssetCache(),
        forwarder=RuntimeForwarder(),
        send=send,
    )
    runtime.state = "RUNNING"
    runtime._step_id = "cat-discover-center-01"
    runtime.course_embodied_dispatcher.ack_timeout_sec = 10
    runtime.course_embodied_dispatcher.settle_before_listen_sec = 0
    decision_payload = await opening_decision(runtime)
    plan = response_plan(runtime, decision_payload)
    assert (await runtime.course_apply_response_plan(plan))["accepted"] is True
    commit = asyncio.create_task(runtime.commit_course_response_plan(plan))
    await asyncio.sleep(0)
    await runtime.on_lesson_ack(runtime_ack(sent[0]))
    assert await commit is True
    assert runtime.course_assessment_window_open is True

    await runtime.conversation_interrupt_current()

    assert runtime.course_assessment_window_open is True
    assert runtime._child_response_window_open is True
    assert [frame["type"] for frame in sent] == ["lesson_embodied_action"]


@pytest.mark.asyncio
async def test_safety_interrupt_sends_firmware_cancel_before_local_completion() -> None:
    sent: list[dict] = []

    async def send(payload: str) -> None:
        sent.append(json.loads(payload))

    runtime = LessonRuntime(
        RuntimeConn([]),
        assignment={"assignmentId": "assignment-1", "lessonId": "lesson-1"},
        manifest={"courseModeContract": semantic_contract()},
        asset_cache=RuntimeAssetCache(),
        forwarder=RuntimeForwarder(),
        send=send,
    )
    runtime.state = "RUNNING"
    runtime._step_id = "cat-discover-center-01"
    runtime.course_embodied_dispatcher.ack_timeout_sec = 10
    decision_payload = await opening_decision(runtime)
    plan = response_plan(runtime, decision_payload)
    assert (await runtime.course_apply_response_plan(plan))["accepted"] is True
    action = sent[0]

    await runtime.course_observe_child({
        "lessonSessionId": runtime.session_id,
        "turnSequenceId": 3,
        "observationId": "safety-interrupt",
        "semanticClass": "unknown",
        "speechClass": "not_applicable",
        "language": "vi",
        "intent": "answer",
        "engagement": "engaged",
        "safetyClass": "urgent",
        "assessmentEligible": False,
        "confidenceBand": "high",
        "activityId": "cat-recall-visual-02",
        "contextId": "cat_primary_visual_recall",
        "robotAudioContaminated": False,
        "targetTextVisible": False,
    })

    assert sent[1] == {
        "type": "lesson_embodied_cancel",
        "assignmentId": action["assignmentId"],
        "sessionId": action["sessionId"],
        "stepId": action["stepId"],
        "sequence": action["sequence"] + 1,
        "body": {
            "actionId": action["body"]["actionId"],
            "actionGeneration": action["body"]["actionGeneration"],
        },
    }
    assert runtime.course_embodied_dispatcher.in_flight is None
    assert runtime.last_embodied_result.status is EmbodiedDispatchStatus.CANCELLED


@pytest.mark.asyncio
async def test_runtime_consumes_stale_generation_ack_before_next_firmware_sequence() -> None:
    sent: list[dict] = []

    async def send(payload: str) -> None:
        sent.append(json.loads(payload))

    runtime = LessonRuntime(
        RuntimeConn([]),
        assignment={"assignmentId": "assignment-1", "lessonId": "lesson-1"},
        manifest={"courseModeContract": semantic_contract()},
        asset_cache=RuntimeAssetCache(),
        forwarder=RuntimeForwarder(),
        send=send,
    )
    runtime.state = "RUNNING"
    runtime._step_id = "cat-discover-center-01"
    runtime.course_embodied_dispatcher.ack_timeout_sec = 10
    decision_payload = await opening_decision(runtime)
    plan = response_plan(runtime, decision_payload)
    assert (await runtime.course_apply_response_plan(plan))["accepted"] is True

    stale = runtime_ack(sent[0])
    stale["body"]["embodiedAction"]["actionGeneration"] = 0
    await runtime.on_lesson_ack(stale)
    assert runtime._last_inbound_sequence == 1
    assert runtime.course_embodied_dispatcher.in_flight is not None

    current = runtime_ack(sent[0])
    current["sequence"] = 2
    await runtime.on_lesson_ack(current)
    assert runtime._last_inbound_sequence == 2
    assert runtime.course_embodied_dispatcher.in_flight is None


@pytest.mark.asyncio
async def test_unsupported_hardware_uses_explicit_fallback_without_partial_frame() -> None:
    sent: list[str] = []
    events: list[str] = []

    async def send(payload: str) -> None:
        sent.append(payload)

    runtime = LessonRuntime(
        RuntimeConn(events, features=capability(embodied=False)),
        assignment={"assignmentId": "assignment-1", "lessonId": "lesson-1"},
        manifest={"courseModeContract": semantic_contract()},
        asset_cache=RuntimeAssetCache(),
        forwarder=RuntimeForwarder(),
        send=send,
    )
    decision_payload = await opening_decision(runtime)
    plan = response_plan(runtime, decision_payload)

    assert (await runtime.course_apply_response_plan(plan))["accepted"] is True
    assert await runtime.commit_course_response_plan(plan) is True

    assert sent == []
    assert runtime.last_embodied_result.status is EmbodiedDispatchStatus.UNSUPPORTED
    assert runtime.course_assessment_window_open is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "lifecycle",
    [
        "rollback",
        "disconnect",
        "replaced",
        "stop",
        "close",
        "barge",
        "emotional",
        "safety",
        "safety-context",
    ],
)
async def test_runtime_lifecycle_cancels_inflight_embodied_action(lifecycle: str) -> None:
    sent: list[dict] = []

    async def send(payload: str) -> None:
        sent.append(json.loads(payload))

    runtime = LessonRuntime(
        RuntimeConn([]),
        assignment={"assignmentId": "assignment-1", "lessonId": "lesson-1"},
        manifest={"courseModeContract": semantic_contract()},
        asset_cache=RuntimeAssetCache(),
        forwarder=RuntimeForwarder(),
        send=send,
    )
    runtime.state = "RUNNING"
    runtime._step_id = "cat-discover-center-01"
    runtime.course_embodied_dispatcher.ack_timeout_sec = 10
    decision_payload = await opening_decision(runtime)
    plan = response_plan(runtime, decision_payload)
    assert (await runtime.course_apply_response_plan(plan))["accepted"] is True

    if lifecycle == "rollback":
        assert await runtime.rollback_course_response_plan(plan) is True
    elif lifecycle == "disconnect":
        await runtime.on_disconnect()
    elif lifecycle == "replaced":
        await runtime.on_replaced()
    elif lifecycle == "barge":
        await runtime.conversation_interrupt_current()
    elif lifecycle == "close":
        await runtime.close()
    elif lifecycle == "emotional":
        await runtime.course_observe_child({
            "lessonSessionId": runtime.session_id,
            "turnSequenceId": 3,
            "observationId": "emotional-interrupt",
            "semanticClass": "unknown",
            "speechClass": "not_applicable",
            "language": "vi",
            "intent": "emotional_share",
            "engagement": "engaged",
            "safetyClass": "normal",
            "assessmentEligible": False,
            "confidenceBand": "high",
            "activityId": "cat-recall-visual-02",
            "contextId": "cat_primary_visual_recall",
            "robotAudioContaminated": False,
            "targetTextVisible": False,
        })
    elif lifecycle == "safety":
        await runtime.course_observe_child({
            "lessonSessionId": runtime.session_id,
            "turnSequenceId": 3,
            "observationId": "safety-interrupt",
            "semanticClass": "unknown",
            "speechClass": "not_applicable",
            "language": "vi",
            "intent": "answer",
            "engagement": "engaged",
            "safetyClass": "urgent",
            "assessmentEligible": False,
            "confidenceBand": "high",
            "activityId": "cat-recall-visual-02",
            "contextId": "cat_primary_visual_recall",
            "robotAudioContaminated": False,
            "targetTextVisible": False,
        })
    elif lifecycle == "safety-context":
        await runtime.course_open_context({
            "lessonSessionId": runtime.session_id,
            "turnSequenceId": 3,
            "observationId": "safety-context-interrupt",
            "branchType": "SAFETY_DISCLOSURE",
        })
    else:
        await runtime.stop()

    assert runtime.course_embodied_dispatcher.in_flight is None
    assert runtime.last_embodied_result.status is EmbodiedDispatchStatus.CANCELLED
