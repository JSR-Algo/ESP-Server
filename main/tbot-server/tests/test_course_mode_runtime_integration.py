from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.lesson.runtime import LessonRuntime, course_mode_runtime_from_manifest


FIXTURE = Path(__file__).parent / "fixtures" / "course-mode" / "course-mode-pilot-cat-ball.json"


def contract():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_v2_requires_explicit_flag_and_exact_contract_without_v1_fallback() -> None:
    assert course_mode_runtime_from_manifest({"courseModeContract": contract()}, enabled=False) is None
    runtime = course_mode_runtime_from_manifest({"courseModeContract": contract()}, enabled=True)
    assert runtime is not None and runtime.course_mode_active is True
    bad = contract(); bad["preset"]["presetVersion"] = 1
    with pytest.raises(ValueError):
        course_mode_runtime_from_manifest({"courseModeContract": bad}, enabled=True)


def test_v1_manifest_is_not_reinterpreted_as_v2() -> None:
    assert course_mode_runtime_from_manifest({"conversation": {"presetId": "tvideoJourney", "presetVersion": 1}}, enabled=True) is None


class _Conn:
    config = {
        "lesson": {
            "runtime_enabled": True,
            "course_mode_v2_enabled": True,
            "rollout_device_allowlist": ["robot-01"],
        }
    }
    device_id = "robot-01"
    logger = None
    features = None
    headers = {}


class _Forwarder:
    def __init__(self) -> None:
        self.batches = []

    def enqueue(self, batch) -> None:
        self.batches.append(batch)


def test_production_lesson_runtime_activates_v2_only_with_strict_flag() -> None:
    manifest = {"courseModeContract": contract()}
    enabled = LessonRuntime(
        _Conn(), assignment={"assignmentId": "a1", "lessonId": "l1"},
        manifest=manifest, asset_cache=object(), forwarder=_Forwarder(),
    )
    assert enabled.course_mode_active is True

    conn = _Conn()
    conn.config = {"lesson": {**conn.config["lesson"], "course_mode_v2_enabled": False}}
    disabled = LessonRuntime(
        conn, assignment={"assignmentId": "a1", "lessonId": "l1"},
        manifest=manifest, asset_cache=object(), forwarder=_Forwarder(),
    )
    assert disabled.course_mode_active is False


@pytest.mark.asyncio
async def test_adapter_rejects_cross_session_and_uses_elapsed_server_clock() -> None:
    ticks = iter((10.0, 45.0))
    runtime = course_mode_runtime_from_manifest(
        {"courseModeContract": contract()}, enabled=True, clock=lambda: next(ticks),
    )
    assert runtime is not None
    wrong = await runtime.course_observe_child({
        "lessonSessionId": "wrong", "turnSequenceId": 99, "observationId": "o-wrong",
        "semanticClass": "target_en", "speechClass": "exact", "language": "en",
        "intent": "answer", "engagement": "engaged", "safetyClass": "normal",
        "assessmentEligible": True, "confidenceBand": "high",
        "activityId": "cat-recall-visual-02", "contextId": "cat_primary_visual_recall",
        "robotAudioContaminated": False, "targetTextVisible": False,
    })
    assert wrong == {"accepted": False, "code": "LESSON_SESSION_MISMATCH"}

    runtime.orchestrator.active_mastery.record_model(now_ms=10_000)
    runtime.orchestrator.active_mastery.record_intervening_activity()
    accepted = await runtime.course_observe_child({
        "lessonSessionId": runtime.contract.lesson_session_id, "turnSequenceId": 2,
        "observationId": "o-recall", "semanticClass": "target_en", "speechClass": "exact",
        "language": "en", "intent": "answer", "engagement": "engaged",
        "safetyClass": "normal", "assessmentEligible": True, "confidenceBand": "high",
        "activityId": "cat-recall-visual-02", "contextId": "cat_primary_visual_recall",
        "robotAudioContaminated": False, "targetTextVisible": False,
    })
    assert accepted["evidenceEvent"]["evidenceLevel"] == "INDEPENDENT_RECALL"


@pytest.mark.asyncio
async def test_adapter_implements_all_advertised_operations_and_forwards_safe_evidence() -> None:
    forwarder = _Forwarder()
    runtime = course_mode_runtime_from_manifest(
        {"courseModeContract": contract()}, enabled=True, clock=lambda: 30.0,
        assignment_id="a1", forwarder=forwarder,
    )
    assert runtime is not None
    identity = {
        "lessonSessionId": runtime.contract.lesson_session_id,
        "turnSequenceId": 1,
        "observationId": "operation-1",
    }
    assert (await runtime.course_continue(identity))["action"] == "GREET_AND_CHECK_IN"
    opened = await runtime.course_open_context({**identity, "observationId": "operation-2", "branchType": "RELATED_STORY"})
    assert opened["accepted"] is True
    closed = await runtime.course_close_context({
        **identity, "observationId": "operation-3", "branchId": opened["branchId"],
        "bridgeIntent": "white_cat_visual", "childDetailCode": "grandmother_pet",
    })
    assert closed["accepted"] is True
    assert (await runtime.course_apply_response_plan({
        **identity, "observationId": "operation-4", "planId": "plan-1",
    }))["accepted"] is True
    duplicate = await runtime.course_continue({**identity, "observationId": "operation-1"})
    assert duplicate == {"accepted": False, "code": "DUPLICATE_OPERATION_IGNORED"}

    runtime.orchestrator.active_mastery.record_model(now_ms=1_000)
    runtime.orchestrator.active_mastery.record_intervening_activity()
    evidence = await runtime.course_observe_child({
        **identity, "observationId": "evidence-1", "semanticClass": "target_en",
        "speechClass": "exact", "language": "en", "intent": "answer",
        "engagement": "engaged", "safetyClass": "normal", "assessmentEligible": True,
        "confidenceBand": "high", "activityId": "cat-recall-visual-02",
        "contextId": "cat_primary_visual_recall", "robotAudioContaminated": False,
        "targetTextVisible": False,
    })
    assert evidence["evidenceEvent"]["evidenceLevel"] == "INDEPENDENT_RECALL"
    assert forwarder.batches[0]["assignmentId"] == "a1"
    serialized = json.dumps(forwarder.batches[0]).casefold()
    assert not any(field in serialized for field in ("transcript", "utterance", "audio", "story"))
