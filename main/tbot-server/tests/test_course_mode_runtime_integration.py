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
    enabled.conn.lesson_runtime = enabled
    enabled.state = "RUNNING"
    assert enabled.conversation_tool_path_active() is True
    assert enabled.conversation_tool_context()["courseMode"] is True

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
    assert (await runtime.course_apply_response_plan({
        **identity, "observationId": "operation-3", "planId": "plan-1",
        "decisionId": opened["decisionId"],
        "acknowledgment": "Robot heard you.", "relation": "We can keep learning.",
        "guidance": "Look at the picture.", "invitation": "What is it?",
        "questionCount": 1, "embodiedIntent": opened["embodiedIntent"],
        "targetFactsUsed": ["animals.cat"], "praiseLevel": "engagement",
        "safetyMode": False, "normalMiss": False,
    }))["accepted"] is True
    closed = await runtime.course_close_context({
        **identity, "observationId": "operation-4", "branchId": opened["branchId"],
        "bridgeIntent": "white_cat_visual", "childDetailCode": "grandmother_pet",
    })
    assert closed["accepted"] is True
    replayed_decision = await runtime.course_apply_response_plan({
        **identity, "observationId": "operation-5", "planId": "plan-2",
        "decisionId": opened["decisionId"],
        "acknowledgment": "Robot heard you.", "relation": "We can keep learning.",
        "guidance": "Look at the picture.", "invitation": "What is it?",
        "questionCount": 1, "embodiedIntent": opened["embodiedIntent"],
        "targetFactsUsed": ["animals.cat"], "praiseLevel": "engagement",
        "safetyMode": False, "normalMiss": False,
    })
    assert replayed_decision == {"accepted": False, "code": "DECISION_ALREADY_APPLIED"}
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
    assert forwarder.batches[0]["sessionId"] == runtime.lesson_session_id
    serialized = json.dumps(forwarder.batches[0]).casefold()
    assert not any(field in serialized for field in ("transcript", "utterance", "audio", "story"))


def test_production_runtime_uses_unique_execution_session_identity() -> None:
    manifest = {"courseModeContract": contract()}
    first = LessonRuntime(
        _Conn(), assignment={"assignmentId": "a1", "lessonId": "l1"},
        manifest=manifest, asset_cache=object(), forwarder=_Forwarder(),
    )
    second = LessonRuntime(
        _Conn(), assignment={"assignmentId": "a1", "lessonId": "l1"},
        manifest=manifest, asset_cache=object(), forwarder=_Forwarder(),
    )

    assert first.course_mode.lesson_session_id == first.session_id
    assert second.course_mode.lesson_session_id == second.session_id
    assert first.course_mode.lesson_session_id != second.course_mode.lesson_session_id
    assert first.conversation_tool_context()["identity"]["lessonSessionId"] == first.session_id


@pytest.mark.asyncio
@pytest.mark.parametrize("safety_class,intent", [("safety", "answer"), ("normal", "fatigue")])
async def test_course_continue_does_not_resume_vocabulary_from_protected_pause(
    safety_class: str, intent: str,
) -> None:
    runtime = course_mode_runtime_from_manifest(
        {"courseModeContract": contract()}, enabled=True, clock=lambda: 0.0,
    )
    assert runtime is not None
    identity = runtime.contract.lesson_session_id
    paused = await runtime.course_observe_child({
        "lessonSessionId": identity, "turnSequenceId": 1, "observationId": "pause",
        "semanticClass": "unknown", "speechClass": "not_applicable", "language": "vi",
        "intent": intent, "engagement": "engaged", "safetyClass": safety_class,
        "assessmentEligible": False, "confidenceBand": "high",
        "activityId": "cat-recall-visual-02", "contextId": "cat_primary_visual_recall",
        "robotAudioContaminated": False, "targetTextVisible": False,
    })
    continued = await runtime.course_continue({
        "lessonSessionId": identity, "turnSequenceId": 2, "observationId": "continue",
    })

    assert continued["nextState"] == paused["nextState"]
    assert continued["action"] == "HOLD_PROTECTED_PAUSE"
    assert continued["teachingIntent"] is None


@pytest.mark.asyncio
async def test_protected_state_blocks_context_mutation_and_stale_normal_plan() -> None:
    runtime = course_mode_runtime_from_manifest(
        {"courseModeContract": contract()}, enabled=True, clock=lambda: 0.0,
    )
    assert runtime is not None
    identity = runtime.lesson_session_id
    normal = await runtime.course_continue({
        "lessonSessionId": identity, "turnSequenceId": 1, "observationId": "normal",
    })
    await runtime.course_observe_child({
        "lessonSessionId": identity, "turnSequenceId": 2, "observationId": "safety",
        "semanticClass": "unknown", "speechClass": "not_applicable", "language": "vi",
        "intent": "answer", "engagement": "engaged", "safetyClass": "safety",
        "assessmentEligible": False, "confidenceBand": "high",
        "activityId": "cat-recall-visual-02", "contextId": "cat_primary_visual_recall",
        "robotAudioContaminated": False, "targetTextVisible": False,
    })

    opened = await runtime.course_open_context({
        "lessonSessionId": identity, "turnSequenceId": 3, "observationId": "branch",
        "branchType": "RELATED_STORY",
    })
    stale_plan = await runtime.course_apply_response_plan({
        "lessonSessionId": identity, "turnSequenceId": 4, "observationId": "late-plan",
        "planId": "p1", "decisionId": normal["decisionId"],
        "acknowledgment": "Robot heard you.", "relation": "We can learn.",
        "guidance": "Look at the picture.", "invitation": "Ready?", "questionCount": 1,
        "embodiedIntent": normal["embodiedIntent"], "targetFactsUsed": ["animals.cat"],
        "praiseLevel": "engagement", "safetyMode": False, "normalMiss": False,
    })

    assert opened == {"accepted": False, "code": "COURSE_OPERATION_NOT_ALLOWED"}
    assert stale_plan == {"accepted": False, "code": "STALE_DECISION"}
    assert "course_open_context" not in runtime.tool_context()["allowedTools"]


@pytest.mark.asyncio
async def test_natural_runtime_operations_can_reach_mastered_today() -> None:
    now = [0.0]
    runtime = course_mode_runtime_from_manifest(
        {"courseModeContract": contract()}, enabled=True, clock=lambda: now[0],
    )
    assert runtime is not None

    def identity(observation_id: str, turn: int) -> dict:
        return {
            "lessonSessionId": runtime.contract.lesson_session_id,
            "turnSequenceId": turn,
            "observationId": observation_id,
        }

    for turn in range(1, 4):
        await runtime.course_continue(identity(f"continue-{turn}", turn))

    async def observe(observation_id, turn, activity_id, context_id, semantic_class, speech_class):
        return await runtime.course_observe_child({
            **identity(observation_id, turn), "semanticClass": semantic_class,
            "speechClass": speech_class, "language": "en", "intent": "answer",
            "engagement": "engaged", "safetyClass": "normal", "assessmentEligible": True,
            "confidenceBand": "high", "activityId": activity_id, "contextId": context_id,
            "robotAudioContaminated": False, "targetTextVisible": False,
        })

    now[0] = 5.0
    await observe("meaning", 4, "cat-meaning-left-right-01", "cat_dog_visual_contrast", "meaning_vi", "not_applicable")
    model = await observe("needs-model", 5, "cat-recall-visual-02", "cat_primary_visual_recall", "unknown", "silence")
    assert model["mayModelTarget"] is True

    async def apply(decision, observation_id, turn):
        return await runtime.course_apply_response_plan({
            **identity(observation_id, turn), "planId": f"plan-{turn}",
            "decisionId": decision["decisionId"], "acknowledgment": "Robot heard you.",
            "relation": "We can try another way.", "guidance": "Look at the picture.",
            "invitation": "Ready?", "questionCount": 1,
            "embodiedIntent": decision["embodiedIntent"],
            "targetFactsUsed": ["animals.cat"], "praiseLevel": "engagement",
            "safetyMode": False, "normalMiss": False,
        })

    await apply(model, "apply-model", 6)
    alternate = await runtime.course_continue(identity("alternate", 7))
    await apply(alternate, "apply-alternate", 8)
    now[0] = 30.0
    await observe("recall", 9, "cat-recall-visual-02", "cat_primary_visual_recall", "target_en", "exact")
    now[0] = 40.0
    await observe("transfer", 10, "cat-transfer-scene-01", "cat_second_visual_scene", "target_en", "exact")
    now[0] = 70.0
    result = await observe("delayed", 11, "cat-delayed-recall-01", "cat_delayed_callback", "target_en", "exact")
    assert result["evidenceEvent"]["evidenceLevel"] == "MASTERED_TODAY"


@pytest.mark.asyncio
async def test_response_plan_must_pass_fail_closed_validator() -> None:
    runtime = course_mode_runtime_from_manifest(
        {"courseModeContract": contract()}, enabled=True, clock=lambda: 0.0,
    )
    assert runtime is not None
    decision = await runtime.course_continue({
        "lessonSessionId": runtime.contract.lesson_session_id, "turnSequenceId": 1,
        "observationId": "decision-for-bad-plan",
    })
    result = await runtime.course_apply_response_plan({
        "lessonSessionId": runtime.contract.lesson_session_id, "turnSequenceId": 2,
        "observationId": "bad-plan", "planId": "invented", "decisionId": decision["decisionId"],
        "acknowledgment": "Wrong.", "relation": "", "guidance": "Try harder.",
        "invitation": "Again?", "questionCount": 1, "embodiedIntent": "INVITE_CHILD",
        "targetFactsUsed": ["invented.fact"], "praiseLevel": "mastery",
        "safetyMode": False, "normalMiss": True,
    })
    assert result == {"accepted": False, "code": "INVALID_RESPONSE_PLAN"}


@pytest.mark.asyncio
async def test_safety_decision_rejects_plan_that_claims_normal_teaching_mode() -> None:
    runtime = course_mode_runtime_from_manifest(
        {"courseModeContract": contract()}, enabled=True, clock=lambda: 0.0,
    )
    assert runtime is not None
    decision = await runtime.course_observe_child({
        "lessonSessionId": runtime.contract.lesson_session_id, "turnSequenceId": 1,
        "observationId": "safety", "semanticClass": "unknown", "speechClass": "not_applicable",
        "language": "vi", "intent": "answer", "engagement": "engaged",
        "safetyClass": "safety", "assessmentEligible": False, "confidenceBand": "high",
        "activityId": "cat-recall-visual-02", "contextId": "cat_primary_visual_recall",
        "robotAudioContaminated": False, "targetTextVisible": False,
    })
    result = await runtime.course_apply_response_plan({
        "lessonSessionId": runtime.contract.lesson_session_id, "turnSequenceId": 2,
        "observationId": "unsafe-plan", "planId": "p1", "decisionId": decision["decisionId"],
        "acknowledgment": "Robot heard you.", "relation": "Let us learn.",
        "guidance": "Look at cat.", "invitation": "What is cat?", "questionCount": 1,
        "embodiedIntent": decision["embodiedIntent"], "targetFactsUsed": ["animals.cat"],
        "praiseLevel": "engagement", "safetyMode": False, "normalMiss": False,
    })
    assert result == {"accepted": False, "code": "INVALID_RESPONSE_PLAN"}


def test_course_tool_context_exposes_only_authoritative_bounded_identifiers() -> None:
    runtime = course_mode_runtime_from_manifest(
        {"courseModeContract": contract()}, enabled=True, clock=lambda: 0.0,
    )
    assert runtime is not None
    context = runtime.tool_context()
    assert context["identity"]["lessonSessionId"] == runtime.contract.lesson_session_id
    assert context["activeTargetId"] == "animals.cat"
    assert {row["activityId"] for row in context["activities"]} == set(runtime.contract.primary.activity_ids)
    serialized = json.dumps(context).casefold()
    assert not any(field in serialized for field in ("transcript", "utterance", "audio", "story"))
