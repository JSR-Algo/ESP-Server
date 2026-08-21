from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.lesson.course_orchestrator import SessionState, WordState
from core.lesson.course_snapshot_store import MemoryCourseModeSnapshotStore
from core.lesson.runtime import LessonRuntime, course_mode_runtime_from_manifest
from core.lesson.word_mastery import EvidenceLevel


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


class _AcknowledgingForwarder(_Forwarder):
    def enqueue(self, batch, *, on_success=None, on_failure=None):
        self.batches.append(batch)
        self.on_success = on_success
        self.on_failure = on_failure
        return True


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

    runtime.orchestrator.session_state = SessionState.WORD_ACTIVE
    runtime.orchestrator.active_mastery.record_model(now_ms=10_000)
    runtime.orchestrator.active_mastery.record_intervening_activity()
    accepted = await runtime.course_observe_child({
        "lessonSessionId": runtime.contract.lesson_session_id, "turnSequenceId": 1,
        "observationId": "o-recall", "semanticClass": "target_en", "speechClass": "exact",
        "language": "en", "intent": "answer", "engagement": "engaged",
        "safetyClass": "normal", "assessmentEligible": True, "confidenceBand": "high",
        "activityId": "cat-recall-visual-02", "contextId": "cat_primary_visual_recall",
        "robotAudioContaminated": False, "targetTextVisible": False,
    })
    assert accepted["evidenceEvent"]["evidenceLevel"] == "INDEPENDENT_RECALL"


@pytest.mark.asyncio
async def test_server_playback_state_overrides_model_assessment_flags() -> None:
    runtime = course_mode_runtime_from_manifest(
        {"courseModeContract": contract()}, enabled=True, clock=lambda: 30.0,
        assessment_state=lambda: {
            "robotAudioContaminated": True,
            "targetTextVisible": True,
        },
    )
    assert runtime is not None
    runtime.orchestrator.session_state = SessionState.WORD_ACTIVE
    runtime.orchestrator.active_mastery.record_model(now_ms=1_000)
    runtime.orchestrator.active_mastery.record_intervening_activity()

    result = await runtime.course_observe_child({
        "lessonSessionId": runtime.lesson_session_id, "turnSequenceId": 1,
        "observationId": "model-claims-clean-audio", "semanticClass": "target_en",
        "speechClass": "exact", "language": "en", "intent": "answer",
        "engagement": "engaged", "safetyClass": "normal", "assessmentEligible": True,
        "confidenceBand": "high", "activityId": "cat-recall-visual-02",
        "contextId": "cat_primary_visual_recall", "robotAudioContaminated": False,
        "targetTextVisible": False,
    })

    assert result["action"] == "OWN_ASR_UNCERTAINTY"
    assert result["evidenceEvent"] is None
    assert runtime.orchestrator.active_mastery.level is EvidenceLevel.EXPOSED


@pytest.mark.asyncio
async def test_adapter_rejects_stale_turn_before_mutating_authoritative_state() -> None:
    runtime = course_mode_runtime_from_manifest(
        {"courseModeContract": contract()}, enabled=True, clock=lambda: 0.0,
    )
    assert runtime is not None
    identity = {
        "lessonSessionId": runtime.lesson_session_id,
        "turnSequenceId": 1,
    }
    first = await runtime.course_continue({**identity, "observationId": "first"})
    stale = await runtime.course_open_context({
        **identity, "observationId": "stale", "branchType": "RELATED_STORY",
    })

    assert first["nextState"] == "OPENING"
    assert stale == {"accepted": False, "code": "COURSE_OPERATION_NOT_ALLOWED"}
    assert runtime.orchestrator.session_state.name == "OPENING"


@pytest.mark.asyncio
async def test_pending_decision_blocks_progress_until_response_plan_is_applied() -> None:
    runtime = course_mode_runtime_from_manifest(
        {"courseModeContract": contract()}, enabled=True, clock=lambda: 0.0,
    )
    assert runtime is not None

    first = await runtime.course_continue({
        "lessonSessionId": runtime.lesson_session_id,
        "turnSequenceId": 1,
        "observationId": "first",
    })
    skipped = await runtime.course_continue({
        "lessonSessionId": runtime.lesson_session_id,
        "turnSequenceId": 2,
        "observationId": "skip-greeting",
    })

    assert first["action"] == "GREET_AND_CHECK_IN"
    assert skipped == {"accepted": False, "code": "COURSE_OPERATION_NOT_ALLOWED"}
    assert runtime.orchestrator.session_state is SessionState.OPENING


@pytest.mark.asyncio
async def test_response_plan_is_advertised_and_consumed_only_for_a_pending_decision() -> None:
    runtime = course_mode_runtime_from_manifest(
        {"courseModeContract": contract()}, enabled=True, clock=lambda: 0.0,
    )
    assert runtime is not None
    assert "course_apply_response_plan" not in runtime.tool_context()["allowedTools"]

    premature = await runtime.course_apply_response_plan({
        "lessonSessionId": runtime.lesson_session_id, "turnSequenceId": 1,
        "observationId": "premature-plan", "planId": "premature-plan",
        "decisionId": "missing", "acknowledgment": "Hello.", "relation": "",
        "guidance": "", "invitation": "", "questionCount": 0,
        "embodiedIntent": "GREET_SMALL", "targetFactsUsed": [],
        "praiseLevel": "engagement", "safetyMode": False, "normalMiss": False,
    })
    opening = await runtime.course_continue({
        "lessonSessionId": runtime.lesson_session_id, "turnSequenceId": 1,
        "observationId": "opening-after-premature-plan",
    })

    assert premature == {"accepted": False, "code": "COURSE_OPERATION_NOT_ALLOWED"}
    assert opening["action"] == "GREET_AND_CHECK_IN"
    pending_context = runtime.tool_context()
    assert "course_apply_response_plan" in pending_context["allowedTools"]
    assert pending_context["pendingDecision"] == {
        "decisionId": opening["decisionId"],
        "acknowledgmentIntent": opening["acknowledgmentIntent"],
        "teachingIntent": opening["teachingIntent"],
        "questionIntent": opening["questionIntent"],
        "embodiedIntent": opening["embodiedIntent"],
        "mayModelTarget": opening["mayModelTarget"],
        "safetyMode": False,
        "allowedTargetFactCodes": ["animals.cat"],
    }


@pytest.mark.asyncio
async def test_context_snapshot_republishes_opaque_branch_resume_identity() -> None:
    runtime = course_mode_runtime_from_manifest(
        {"courseModeContract": contract()}, enabled=True, clock=lambda: 0.0,
    )
    assert runtime is not None
    runtime.orchestrator.session_state = SessionState.WORD_ACTIVE
    opened = await runtime.course_open_context({
        "lessonSessionId": runtime.lesson_session_id, "turnSequenceId": 1,
        "observationId": "branch-before-reconnect", "branchType": "RELATED_STORY",
    })

    context = runtime.tool_context()
    assert context["allowedTools"] == ["course_apply_response_plan"]
    assert context["activeContext"] == {
        "branchId": opened["branchId"],
        "bridgeIntents": [
            "white_cat_visual", "pet_sound_clue", "resume_active_word_visual",
            "resume_active_word_choice",
        ],
        "childDetailCodes": [
            "grandmother_pet", "related_pet", "child_choice", "current_visual",
            "earlier_session_detail", "no_personal_detail",
        ],
    }
    applied = await runtime.course_apply_response_plan({
        "lessonSessionId": runtime.lesson_session_id, "turnSequenceId": 2,
        "observationId": "branch-plan", "planId": "branch-plan",
        "decisionId": opened["decisionId"], "acknowledgment": "I hear you.",
        "relation": "Let us look together.", "guidance": "Look at the picture.",
        "invitation": "Ready?", "questionCount": 1,
        "embodiedIntent": opened["embodiedIntent"], "targetFactsUsed": [],
        "praiseLevel": "engagement", "safetyMode": False, "normalMiss": False,
    })
    assert applied["accepted"] is True
    resumed_context = runtime.tool_context()
    assert "course_close_context" in resumed_context["allowedTools"]
    assert resumed_context["activeContext"] == context["activeContext"]


@pytest.mark.asyncio
async def test_duplicate_decision_operation_replays_exact_pending_result() -> None:
    runtime = course_mode_runtime_from_manifest(
        {"courseModeContract": contract()}, enabled=True, clock=lambda: 0.0,
    )
    assert runtime is not None
    arguments = {
        "lessonSessionId": runtime.lesson_session_id,
        "turnSequenceId": 1,
        "observationId": "lost-response",
    }

    first = await runtime.course_continue(arguments)
    replay = await runtime.course_continue(arguments)

    assert replay == first
    assert replay["decisionId"] == first["decisionId"]
    assert runtime.tool_context()["identity"]["turnSequenceId"] == 2


@pytest.mark.asyncio
async def test_duplicate_replay_requires_matching_session_operation_and_turn() -> None:
    runtime = course_mode_runtime_from_manifest(
        {"courseModeContract": contract()}, enabled=True, clock=lambda: 0.0,
    )
    assert runtime is not None
    arguments = {
        "lessonSessionId": runtime.lesson_session_id,
        "turnSequenceId": 1,
        "observationId": "replay-identity",
    }
    await runtime.course_continue(arguments)

    wrong_session = await runtime.course_continue({
        **arguments, "lessonSessionId": "other-session",
    })
    wrong_operation = await runtime.course_open_context({
        **arguments, "branchType": "RELATED_STORY",
    })
    wrong_turn = await runtime.course_continue({**arguments, "turnSequenceId": 99})

    assert wrong_session == {"accepted": False, "code": "LESSON_SESSION_MISMATCH"}
    assert wrong_operation == {"accepted": False, "code": "DUPLICATE_OPERATION_IGNORED"}
    assert wrong_turn == {"accepted": False, "code": "STALE_TURN_SEQUENCE"}


@pytest.mark.asyncio
async def test_adapter_snapshot_restore_replays_committed_results_without_effects() -> None:
    runtime = course_mode_runtime_from_manifest(
        {"courseModeContract": contract()}, enabled=True, clock=lambda: 0.0,
    )
    assert runtime is not None
    decision_args = {
        "lessonSessionId": runtime.lesson_session_id, "turnSequenceId": 1,
        "observationId": "opening-before-reconnect",
    }
    decision = await runtime.course_continue(decision_args)
    plan_args = {
        "lessonSessionId": runtime.lesson_session_id, "turnSequenceId": 2,
        "observationId": "plan-before-reconnect", "planId": "plan-before-reconnect",
        "decisionId": decision["decisionId"], "acknowledgment": "Hello.",
        "relation": "", "guidance": "", "invitation": "Ready?", "questionCount": 1,
        "embodiedIntent": decision["embodiedIntent"], "targetFactsUsed": [],
        "praiseLevel": "engagement", "safetyMode": False, "normalMiss": False,
    }
    applied = await runtime.course_apply_response_plan(plan_args)
    assert runtime.commit_course_response_plan(plan_args) is True

    persisted_snapshot = json.loads(json.dumps(runtime.snapshot()))
    restored = course_mode_runtime_from_manifest(
        {"courseModeContract": contract()}, enabled=True, clock=lambda: 0.0,
        authoritative_snapshot=persisted_snapshot,
    )
    assert restored is not None
    assert await restored.course_continue(decision_args) == decision
    assert await restored.course_apply_response_plan(plan_args) == applied
    assert restored.response_plan_requires_delivery(plan_args) is False
    assert restored.tool_context()["identity"]["turnSequenceId"] == 3
    assert restored.orchestrator.snapshot() == runtime.orchestrator.snapshot()

    mismatched = {**persisted_snapshot, "contractChecksum": "0" * 64}
    with pytest.raises(ValueError, match="contract mismatch"):
        course_mode_runtime_from_manifest(
            {"courseModeContract": contract()}, enabled=True,
            authoritative_snapshot=mismatched,
        )


@pytest.mark.asyncio
async def test_preparing_rejects_normal_observation_without_consuming_opening_turn() -> None:
    runtime = course_mode_runtime_from_manifest(
        {"courseModeContract": contract()}, enabled=True, clock=lambda: 0.0,
    )
    assert runtime is not None
    rejected = await runtime.course_observe_child({
        "lessonSessionId": runtime.lesson_session_id, "turnSequenceId": 1,
        "observationId": "premature-meaning", "semanticClass": "meaning_vi",
        "speechClass": "not_applicable", "language": "vi", "intent": "answer",
        "engagement": "engaged", "safetyClass": "normal", "assessmentEligible": True,
        "confidenceBand": "high", "activityId": "cat-meaning-left-right-01",
        "contextId": "cat_dog_visual_contrast", "robotAudioContaminated": False,
        "targetTextVisible": False,
    })
    opening = await runtime.course_continue({
        "lessonSessionId": runtime.lesson_session_id, "turnSequenceId": 1,
        "observationId": "opening",
    })

    assert rejected == {"accepted": False, "code": "COURSE_OPERATION_NOT_ALLOWED"}
    assert opening["action"] == "GREET_AND_CHECK_IN"
    assert runtime.orchestrator.active_mastery.level is EvidenceLevel.NOT_STARTED


@pytest.mark.asyncio
async def test_opening_observation_advances_social_sequence_without_mastery() -> None:
    runtime = course_mode_runtime_from_manifest(
        {"courseModeContract": contract()}, enabled=True, clock=lambda: 0.0,
    )
    assert runtime is not None
    greeting = await runtime.course_continue({
        "lessonSessionId": runtime.lesson_session_id, "turnSequenceId": 1,
        "observationId": "opening",
    })
    runtime._applied_decision_ids.add(greeting["decisionId"])
    response = await runtime.course_observe_child({
        "lessonSessionId": runtime.lesson_session_id, "turnSequenceId": 2,
        "observationId": "check-in-answer", "semanticClass": "meaning_vi",
        "speechClass": "not_applicable", "language": "vi", "intent": "answer",
        "engagement": "engaged", "safetyClass": "normal", "assessmentEligible": True,
        "confidenceBand": "high", "activityId": "cat-meaning-left-right-01",
        "contextId": "cat_dog_visual_contrast", "robotAudioContaminated": False,
        "targetTextVisible": False,
    })

    assert response["action"] == "ACKNOWLEDGE_AND_BUILD_CURIOSITY"
    assert response["nextState"] == "OPENING"
    assert runtime.orchestrator.active_mastery.level is EvidenceLevel.NOT_STARTED


@pytest.mark.asyncio
@pytest.mark.parametrize("reporting_tool", ["observe", "open_context"])
async def test_safety_disclosure_interrupts_pending_context_branch(
    reporting_tool: str,
) -> None:
    runtime = course_mode_runtime_from_manifest(
        {"courseModeContract": contract()}, enabled=True, clock=lambda: 0.0,
    )
    assert runtime is not None
    identity = runtime.lesson_session_id
    runtime.orchestrator.session_state = SessionState.WORD_ACTIVE
    opened = await runtime.course_open_context({
        "lessonSessionId": identity, "turnSequenceId": 1,
        "observationId": "story", "branchType": "RELATED_STORY",
    })
    assert opened["nextState"] == "CONTEXT_BRANCH"

    if reporting_tool == "observe":
        safety = await runtime.course_observe_child({
            "lessonSessionId": identity, "turnSequenceId": 2,
            "observationId": "safety", "semanticClass": "unknown",
            "speechClass": "not_applicable", "language": "vi", "intent": "answer",
            "engagement": "engaged", "safetyClass": "safety",
            "assessmentEligible": False, "confidenceBand": "high",
            "activityId": "cat-recall-visual-02", "contextId": "cat_primary_visual_recall",
            "robotAudioContaminated": False, "targetTextVisible": False,
        })
    else:
        safety = await runtime.course_open_context({
            "lessonSessionId": identity, "turnSequenceId": 2,
            "observationId": "safety", "branchType": "SAFETY_DISCLOSURE",
        })

    assert safety["action"] == "PAUSE_FOR_SAFETY"
    assert safety["nextState"] == "SAFETY_PAUSED"
    assert runtime.tool_context()["allowedTools"] == ["course_apply_response_plan"]


@pytest.mark.asyncio
async def test_failed_delivery_rollback_keeps_exact_response_plan_retryable() -> None:
    runtime = course_mode_runtime_from_manifest(
        {"courseModeContract": contract()}, enabled=True, clock=lambda: 0.0,
    )
    assert runtime is not None
    decision = await runtime.course_continue({
        "lessonSessionId": runtime.lesson_session_id,
        "turnSequenceId": 1,
        "observationId": "decision",
    })
    arguments = {
        "lessonSessionId": runtime.lesson_session_id,
        "turnSequenceId": 2,
        "observationId": "plan-operation",
        "planId": "plan-1",
        "decisionId": decision["decisionId"],
        "acknowledgment": "I hear you.",
        "relation": "Let us look together.",
        "guidance": "Look at the picture.",
        "invitation": "Ready?",
        "questionCount": 1,
        "embodiedIntent": decision["embodiedIntent"],
        "targetFactsUsed": ["animals.cat"],
        "praiseLevel": "engagement",
        "safetyMode": False,
        "normalMiss": False,
    }
    before = runtime.orchestrator.snapshot()

    assert (await runtime.course_apply_response_plan(arguments))["accepted"] is True
    assert runtime.rollback_course_response_plan(arguments) is True
    assert runtime.orchestrator.snapshot() == before
    assert (await runtime.course_apply_response_plan(arguments))["accepted"] is True
    assert runtime.commit_course_response_plan(arguments) is True
    assert runtime.rollback_course_response_plan(arguments) is False

    replay = await runtime.course_apply_response_plan(arguments)
    assert replay["accepted"] is True
    assert replay["responseText"] == "I hear you. Let us look together. Look at the picture. Ready?"
    assert runtime.response_plan_requires_delivery(arguments) is False


@pytest.mark.asyncio
async def test_production_runtime_starts_course_budget_when_protocol_activates() -> None:
    runtime = LessonRuntime(
        _Conn(), assignment={"assignmentId": "a1", "lessonId": "l1"},
        manifest={"courseModeContract": contract()},
        asset_cache=type("AssetCache", (), {"preload_timeout_sec": 90})(),
        forwarder=_Forwarder(),
    )
    assert runtime.course_mode is not None
    runtime.course_mode._clock = lambda: 600.0
    emitted = []

    async def emit(message_type, **kwargs):
        emitted.append((message_type, kwargs))

    runtime._emit = emit

    await runtime.start_protocol(preloaded=True)

    assert runtime.course_mode.orchestrator.started_at_ms == 600_000
    assert emitted[0][0] == "lesson_prepare"


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
    greeted = await runtime.course_continue(identity)
    assert greeted["action"] == "GREET_AND_CHECK_IN"
    assert (await runtime.course_apply_response_plan({
        **identity, "turnSequenceId": 2, "observationId": "operation-2", "planId": "greeting-plan",
        "decisionId": greeted["decisionId"], "acknowledgment": "Hello.", "relation": "",
        "guidance": "", "invitation": "Ready?", "questionCount": 1,
        "embodiedIntent": greeted["embodiedIntent"], "targetFactsUsed": [],
        "praiseLevel": "engagement", "safetyMode": False, "normalMiss": False,
    }))["accepted"] is True
    runtime.orchestrator.session_state = SessionState.WORD_ACTIVE
    opened = await runtime.course_open_context({
        **identity, "turnSequenceId": 3, "observationId": "operation-3",
        "branchType": "RELATED_STORY",
    })
    assert opened["accepted"] is True
    assert (await runtime.course_apply_response_plan({
        **identity, "turnSequenceId": 4, "observationId": "operation-4", "planId": "plan-1",
        "decisionId": opened["decisionId"],
        "acknowledgment": "Robot heard you.", "relation": "We can keep learning.",
        "guidance": "Look at the picture.", "invitation": "What is it?",
        "questionCount": 1, "embodiedIntent": opened["embodiedIntent"],
        "targetFactsUsed": ["animals.cat"], "praiseLevel": "engagement",
        "safetyMode": False, "normalMiss": False,
    }))["accepted"] is True
    closed = await runtime.course_close_context({
        **identity, "turnSequenceId": 5, "observationId": "operation-5", "branchId": opened["branchId"],
        "bridgeIntent": "white_cat_visual", "childDetailCode": "grandmother_pet",
    })
    assert closed["accepted"] is True
    replayed_decision = await runtime.course_apply_response_plan({
        **identity, "turnSequenceId": 6, "observationId": "operation-6", "planId": "plan-2",
        "decisionId": opened["decisionId"],
        "acknowledgment": "Robot heard you.", "relation": "We can keep learning.",
        "guidance": "Look at the picture.", "invitation": "What is it?",
        "questionCount": 1, "embodiedIntent": opened["embodiedIntent"],
        "targetFactsUsed": ["animals.cat"], "praiseLevel": "engagement",
        "safetyMode": False, "normalMiss": False,
    })
    assert replayed_decision == {"accepted": False, "code": "DECISION_ALREADY_APPLIED"}
    assert (await runtime.course_apply_response_plan({
        **identity, "turnSequenceId": 7, "observationId": "operation-7", "planId": "close-plan",
        "decisionId": closed["decisionId"], "acknowledgment": "Thank you.", "relation": "",
        "guidance": "Look at the picture.", "invitation": "Ready?", "questionCount": 1,
        "embodiedIntent": closed["embodiedIntent"], "targetFactsUsed": ["animals.cat"],
        "praiseLevel": "engagement", "safetyMode": False, "normalMiss": False,
    }))["accepted"] is True
    duplicate = await runtime.course_continue({**identity, "turnSequenceId": 8, "observationId": "operation-1"})
    assert duplicate == {"accepted": False, "code": "STALE_TURN_SEQUENCE"}

    runtime.orchestrator.active_mastery.record_model(now_ms=1_000)
    runtime.orchestrator.active_mastery.record_intervening_activity()
    evidence = await runtime.course_observe_child({
        **identity, "turnSequenceId": 8, "observationId": "evidence-1", "semanticClass": "target_en",
        "speechClass": "exact", "language": "en", "intent": "answer",
        "engagement": "engaged", "safetyClass": "normal", "assessmentEligible": True,
        "confidenceBand": "high", "activityId": "cat-recall-visual-02",
        "contextId": "cat_primary_visual_recall", "robotAudioContaminated": False,
        "targetTextVisible": False,
    })
    assert evidence["evidenceEvent"]["evidenceLevel"] == "INDEPENDENT_RECALL"
    assert forwarder.batches[0]["assignmentId"] == "a1"
    assert forwarder.batches[0]["sessionId"] == runtime.lesson_session_id
    assert forwarder.batches[0]["events"][0]["sequence"] < -3_000_000
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
    assert first.course_mode.orchestrator.snapshot()["lessonSessionId"] == first.session_id
    assert second.course_mode.orchestrator.snapshot()["lessonSessionId"] == second.session_id


@pytest.mark.asyncio
async def test_production_runtime_persists_and_restores_course_snapshot() -> None:
    store = MemoryCourseModeSnapshotStore()
    manifest = {"courseModeContract": contract()}
    first = LessonRuntime(
        _Conn(), assignment={"assignmentId": "a1", "lessonId": "l1"},
        manifest=manifest, asset_cache=object(), forwarder=_Forwarder(),
        course_mode_snapshot_store=store, course_mode_snapshot_device_id="device-1",
    )
    decision = await first.course_continue({
        "lessonSessionId": first.session_id, "turnSequenceId": 1,
        "observationId": "opening-before-reconnect",
    })
    plan = {
        "lessonSessionId": first.session_id, "turnSequenceId": 2,
        "observationId": "plan-before-reconnect", "planId": "plan-before-reconnect",
        "decisionId": decision["decisionId"], "acknowledgment": "Hello.",
        "relation": "", "guidance": "", "invitation": "Ready?", "questionCount": 1,
        "embodiedIntent": decision["embodiedIntent"], "targetFactsUsed": [],
        "praiseLevel": "engagement", "safetyMode": False, "normalMiss": False,
    }
    assert (await first.course_apply_response_plan(plan))["accepted"] is True
    assert await first.commit_course_response_plan(plan) is True
    snapshot = await store.load("device-1", "a1")

    restored = LessonRuntime(
        _Conn(), assignment={"assignmentId": "a1", "lessonId": "l1"},
        manifest=manifest, asset_cache=object(), forwarder=_Forwarder(),
        course_mode_snapshot_store=store, course_mode_snapshot_device_id="device-1",
        course_mode_snapshot=snapshot,
    )

    assert restored.session_id == first.session_id
    assert restored.course_mode is not None
    restored_snapshot = restored.course_mode.snapshot()
    first_snapshot = first.course_mode.snapshot()
    restored_snapshot.pop("startedAtMs")
    first_snapshot.pop("startedAtMs")
    restored_snapshot["orchestrator"].pop("startedAtMs")
    first_snapshot["orchestrator"].pop("startedAtMs")
    assert restored_snapshot == first_snapshot
    assert await restored.course_apply_response_plan(plan) == await first.course_apply_response_plan(plan)


@pytest.mark.asyncio
async def test_provisional_response_plan_persists_as_retryable_before_delivery() -> None:
    store = MemoryCourseModeSnapshotStore()
    manifest = {"courseModeContract": contract()}
    runtime = LessonRuntime(
        _Conn(), assignment={"assignmentId": "a1", "lessonId": "l1"},
        manifest=manifest, asset_cache=object(), forwarder=_Forwarder(),
        course_mode_snapshot_store=store, course_mode_snapshot_device_id="device-1",
    )
    decision = await runtime.course_continue({
        "lessonSessionId": runtime.session_id, "turnSequenceId": 1,
        "observationId": "opening-before-delivery",
    })
    durable_before_plan = await store.load("device-1", "a1")
    plan = {
        "lessonSessionId": runtime.session_id, "turnSequenceId": 2,
        "observationId": "plan-before-delivery", "planId": "plan-before-delivery",
        "decisionId": decision["decisionId"], "acknowledgment": "Hello.",
        "relation": "", "guidance": "", "invitation": "Ready?", "questionCount": 1,
        "embodiedIntent": decision["embodiedIntent"], "targetFactsUsed": [],
        "praiseLevel": "engagement", "safetyMode": False, "normalMiss": False,
    }

    assert (await runtime.course_apply_response_plan(plan))["accepted"] is True
    durable_plan = await store.load("device-1", "a1")
    assert durable_plan != durable_before_plan
    assert durable_plan["responsePlanRollback"]["deliveryAttempted"] is False

    restored = LessonRuntime(
        _Conn(), assignment={"assignmentId": "a1", "lessonId": "l1"},
        manifest=manifest, asset_cache=object(), forwarder=_Forwarder(),
        course_mode_snapshot_store=store, course_mode_snapshot_device_id="device-1",
        course_mode_snapshot=durable_plan,
    )
    assert (await restored.course_apply_response_plan(plan))["accepted"] is True
    assert restored.course_mode.response_plan_requires_delivery(plan) is True


@pytest.mark.asyncio
async def test_failed_snapshot_write_after_delivery_does_not_replay_the_plan() -> None:
    class FailingStore(MemoryCourseModeSnapshotStore):
        fail = False

        async def store(self, device_id, assignment_id, snapshot) -> None:
            if self.fail:
                raise RuntimeError("snapshot unavailable")
            await super().store(device_id, assignment_id, snapshot)

    store = FailingStore()
    runtime = LessonRuntime(
        _Conn(), assignment={"assignmentId": "a1", "lessonId": "l1"},
        manifest={"courseModeContract": contract()}, asset_cache=object(),
        forwarder=_Forwarder(), course_mode_snapshot_store=store,
        course_mode_snapshot_device_id="device-1",
    )
    decision = await runtime.course_mode.course_continue({
        "lessonSessionId": runtime.session_id, "turnSequenceId": 1,
        "observationId": "opening-before-failed-commit",
    })
    plan = {
        "lessonSessionId": runtime.session_id, "turnSequenceId": 2,
        "observationId": "failed-commit-plan", "planId": "failed-commit-plan",
        "decisionId": decision["decisionId"], "acknowledgment": "Hello.",
        "relation": "", "guidance": "", "invitation": "Ready?", "questionCount": 1,
        "embodiedIntent": decision["embodiedIntent"], "targetFactsUsed": [],
        "praiseLevel": "engagement", "safetyMode": False, "normalMiss": False,
    }

    assert (await runtime.course_apply_response_plan(plan))["accepted"] is True
    assert await runtime.mark_response_plan_delivery_attempted(plan) is True
    attempted_snapshot = await store.load("device-1", "a1")
    restarted = LessonRuntime(
        _Conn(), assignment={"assignmentId": "a1", "lessonId": "l1"},
        manifest={"courseModeContract": contract()}, asset_cache=object(),
        forwarder=_Forwarder(), course_mode_snapshot_store=store,
        course_mode_snapshot_device_id="device-1",
        course_mode_snapshot=attempted_snapshot,
    )
    assert (await restarted.course_apply_response_plan(plan))["accepted"] is True
    assert restarted.course_mode.response_plan_requires_delivery(plan) is True

    store.fail = True
    assert await runtime.commit_course_response_plan(plan) is False
    store.fail = False
    assert (await runtime.course_apply_response_plan(plan))["accepted"] is True
    assert runtime.course_mode.response_plan_requires_delivery(plan) is False
    assert runtime.course_mode.response_plan_requires_commit(plan) is True


@pytest.mark.asyncio
async def test_evidence_is_not_forwarded_when_snapshot_persistence_fails() -> None:
    class FailingStore(MemoryCourseModeSnapshotStore):
        async def store(self, device_id, assignment_id, snapshot) -> None:
            raise RuntimeError("snapshot unavailable")

    forwarder = _Forwarder()
    runtime = LessonRuntime(
        _Conn(), assignment={"assignmentId": "a1", "lessonId": "l1"},
        manifest={"courseModeContract": contract()}, asset_cache=object(), forwarder=forwarder,
        course_mode_snapshot_store=FailingStore(), course_mode_snapshot_device_id="device-1",
    )
    runtime.course_mode.orchestrator.session_state = SessionState.WORD_ACTIVE
    runtime.course_mode.orchestrator.active_mastery.record_model(now_ms=1_000)
    runtime.course_mode.orchestrator.active_mastery.record_intervening_activity()

    result = await runtime.course_observe_child({
        "lessonSessionId": runtime.session_id, "turnSequenceId": 1,
        "observationId": "evidence-with-failed-snapshot", "semanticClass": "target_en",
        "speechClass": "exact", "language": "en", "intent": "answer",
        "engagement": "engaged", "safetyClass": "normal", "assessmentEligible": True,
        "confidenceBand": "high", "activityId": "cat-recall-visual-02",
        "contextId": "cat_primary_visual_recall", "robotAudioContaminated": False,
        "targetTextVisible": False,
    })

    assert result == {"accepted": False, "code": "COURSE_SNAPSHOT_PERSIST_FAILED"}
    assert forwarder.batches == []


@pytest.mark.asyncio
async def test_durable_snapshot_preserves_only_unforwarded_evidence_for_recovery() -> None:
    forwarder = _Forwarder()
    runtime = course_mode_runtime_from_manifest(
        {"courseModeContract": contract()}, enabled=True, clock=lambda: 30.0,
        assignment_id="a1", forwarder=forwarder, defer_evidence_forwarding=True,
    )
    runtime.orchestrator.session_state = SessionState.WORD_ACTIVE
    runtime.orchestrator.active_mastery.record_model(now_ms=1_000)
    runtime.orchestrator.active_mastery.record_intervening_activity()
    await runtime.course_observe_child({
        "lessonSessionId": runtime.lesson_session_id, "turnSequenceId": 1,
        "observationId": "pending-evidence", "semanticClass": "target_en",
        "speechClass": "exact", "language": "en", "intent": "answer",
        "engagement": "engaged", "safetyClass": "normal", "assessmentEligible": True,
        "confidenceBand": "high", "activityId": "cat-recall-visual-02",
        "contextId": "cat_primary_visual_recall", "robotAudioContaminated": False,
        "targetTextVisible": False,
    })
    snapshot = runtime.durable_snapshot()

    restored_forwarder = _Forwarder()
    restored = course_mode_runtime_from_manifest(
        {"courseModeContract": contract()}, enabled=True, clock=lambda: 35.0,
        assignment_id="a1", forwarder=restored_forwarder,
        authoritative_snapshot=snapshot, defer_evidence_forwarding=True,
    )
    restored.flush_pending_evidence()

    assert len(restored_forwarder.batches) == 1
    assert restored_forwarder.batches[0]["events"][0]["type"] == "word_evidence_recorded"


@pytest.mark.asyncio
async def test_evidence_remains_durable_until_forwarder_acknowledges_backend_success() -> None:
    store = MemoryCourseModeSnapshotStore()
    forwarder = _AcknowledgingForwarder()
    runtime = LessonRuntime(
        _Conn(), assignment={"assignmentId": "a1", "lessonId": "l1"},
        manifest={"courseModeContract": contract()}, asset_cache=object(), forwarder=forwarder,
        course_mode_snapshot_store=store, course_mode_snapshot_device_id="device-1",
    )
    runtime.course_mode.orchestrator.session_state = SessionState.WORD_ACTIVE
    runtime.course_mode.orchestrator.active_mastery.record_model(now_ms=1_000)
    runtime.course_mode.orchestrator.active_mastery.record_intervening_activity()

    result = await runtime.course_observe_child({
        "lessonSessionId": runtime.session_id, "turnSequenceId": 1,
        "observationId": "durable-until-ack", "semanticClass": "target_en",
        "speechClass": "exact", "language": "en", "intent": "answer",
        "engagement": "engaged", "safetyClass": "normal", "assessmentEligible": True,
        "confidenceBand": "high", "activityId": "cat-recall-visual-02",
        "contextId": "cat_primary_visual_recall", "robotAudioContaminated": False,
        "targetTextVisible": False,
    })
    assert result["accepted"] is True
    pending = await store.load("device-1", "a1")
    assert len(pending["pendingEvidenceBatches"]) == 1

    await forwarder.on_success(forwarder.batches[0])
    acknowledged = await store.load("device-1", "a1")
    assert acknowledged["pendingEvidenceBatches"] == []


@pytest.mark.asyncio
async def test_failed_ack_snapshot_write_cannot_erase_durable_evidence_later() -> None:
    class FailingAckStore(MemoryCourseModeSnapshotStore):
        fail_next_store = False

        async def store(self, device_id, assignment_id, snapshot):
            if self.fail_next_store:
                self.fail_next_store = False
                raise RuntimeError("snapshot unavailable")
            await super().store(device_id, assignment_id, snapshot)

    store = FailingAckStore()
    forwarder = _AcknowledgingForwarder()
    runtime = LessonRuntime(
        _Conn(), assignment={"assignmentId": "a1", "lessonId": "l1"},
        manifest={"courseModeContract": contract()}, asset_cache=object(),
        forwarder=forwarder, course_mode_snapshot_store=store,
        course_mode_snapshot_device_id="device-1",
    )
    runtime.course_mode.orchestrator.session_state = SessionState.WORD_ACTIVE
    runtime.course_mode.orchestrator.active_mastery.record_model(now_ms=1_000)
    runtime.course_mode.orchestrator.active_mastery.record_intervening_activity()
    await runtime.course_observe_child({
        "lessonSessionId": runtime.session_id, "turnSequenceId": 1,
        "observationId": "ack-persist-failure", "semanticClass": "target_en",
        "speechClass": "exact", "language": "en", "intent": "answer",
        "engagement": "engaged", "safetyClass": "normal", "assessmentEligible": True,
        "confidenceBand": "high", "activityId": "cat-recall-visual-02",
        "contextId": "cat_primary_visual_recall", "robotAudioContaminated": False,
        "targetTextVisible": False,
    })

    store.fail_next_store = True
    await forwarder.on_success(forwarder.batches[0])
    await runtime.persist_course_mode_snapshot()

    assert len(runtime.course_mode.pending_evidence_batches()) == 1
    durable = await store.load("device-1", "a1")
    assert len(durable["pendingEvidenceBatches"]) == 1


@pytest.mark.asyncio
async def test_rejected_consuming_operation_identity_survives_restart() -> None:
    store = MemoryCourseModeSnapshotStore()
    runtime = LessonRuntime(
        _Conn(), assignment={"assignmentId": "a1", "lessonId": "l1"},
        manifest={"courseModeContract": contract()}, asset_cache=object(),
        forwarder=_Forwarder(), course_mode_snapshot_store=store,
        course_mode_snapshot_device_id="device-1",
    )
    decision = await runtime.course_continue({
        "lessonSessionId": runtime.session_id, "turnSequenceId": 1,
        "observationId": "decision-before-invalid-plan",
    })
    invalid = {
        "lessonSessionId": runtime.session_id, "turnSequenceId": 2,
        "observationId": "invalid-plan-before-restart", "planId": "invalid-plan",
        "decisionId": decision["decisionId"], "acknowledgment": "Wrong.",
        "relation": "", "guidance": "Try harder.", "invitation": "Again?",
        "questionCount": 1, "embodiedIntent": decision["embodiedIntent"],
        "targetFactsUsed": [], "praiseLevel": "engagement",
        "safetyMode": False, "normalMiss": True,
    }
    assert await runtime.course_apply_response_plan(invalid) == {
        "accepted": False, "code": "INVALID_RESPONSE_PLAN",
    }
    snapshot = await store.load("device-1", "a1")
    restarted = LessonRuntime(
        _Conn(), assignment={"assignmentId": "a1", "lessonId": "l1"},
        manifest={"courseModeContract": contract()}, asset_cache=object(),
        forwarder=_Forwarder(), course_mode_snapshot_store=store,
        course_mode_snapshot_device_id="device-1", course_mode_snapshot=snapshot,
    )
    assert await restarted.course_apply_response_plan(invalid) == {
        "accepted": False, "code": "DUPLICATE_OPERATION_IGNORED",
    }


def test_durable_snapshot_rebases_monotonic_timing_across_replicas() -> None:
    runtime = course_mode_runtime_from_manifest(
        {"courseModeContract": contract()}, enabled=True, clock=lambda: 100.0,
        wall_clock=lambda: 1_000.0,
    )
    runtime.start_course_budget()
    runtime.orchestrator.active_mastery.record_model(now_ms=90_000)
    snapshot = runtime.durable_snapshot()

    restored = course_mode_runtime_from_manifest(
        {"courseModeContract": contract()}, enabled=True, clock=lambda: 5.0,
        wall_clock=lambda: 1_030.0, authoritative_snapshot=snapshot,
    )

    assert restored.orchestrator.started_at_ms == -25_000
    assert restored.orchestrator.active_mastery.answer_leakage.last_full_model_at_ms == -35_000


def test_terminal_snapshot_starts_a_fresh_assignment_execution() -> None:
    manifest = {"courseModeContract": contract()}
    finished = LessonRuntime(
        _Conn(), assignment={"assignmentId": "a1", "lessonId": "l1"},
        manifest=manifest, asset_cache=object(), forwarder=_Forwarder(),
    )
    finished.course_mode.orchestrator.session_state = SessionState.CLOSING
    terminal_snapshot = finished.course_mode.snapshot()

    restarted = LessonRuntime(
        _Conn(), assignment={"assignmentId": "a1", "lessonId": "l1"},
        manifest=manifest, asset_cache=object(), forwarder=_Forwarder(),
        course_mode_snapshot=terminal_snapshot,
    )

    assert restarted.session_id != finished.session_id
    assert restarted.course_mode.orchestrator.session_state is SessionState.PREPARING


@pytest.mark.asyncio
async def test_closing_snapshot_with_pending_response_resumes_same_execution() -> None:
    now = [0.0]
    runtime = course_mode_runtime_from_manifest(
        {"courseModeContract": contract()}, enabled=True, clock=lambda: now[0],
    )
    runtime.orchestrator.session_state = SessionState.WORD_ACTIVE
    now[0] = 540.0
    decision = await runtime.course_continue({
        "lessonSessionId": runtime.lesson_session_id, "turnSequenceId": 1,
        "observationId": "pending-close",
    })

    restored = LessonRuntime(
        _Conn(), assignment={"assignmentId": "a1", "lessonId": "l1"},
        manifest={"courseModeContract": contract()}, asset_cache=object(),
        forwarder=_Forwarder(), course_mode_snapshot=runtime.durable_snapshot(),
    )

    assert restored.session_id == runtime.lesson_session_id
    assert restored.course_mode.orchestrator.session_state is SessionState.CLOSING
    assert restored.course_mode.tool_context()["pendingDecision"]["decisionId"] == decision["decisionId"]


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
    runtime.orchestrator.session_state = SessionState.WORD_ACTIVE
    paused = await runtime.course_observe_child({
        "lessonSessionId": identity, "turnSequenceId": 1, "observationId": "pause",
        "semanticClass": "unknown", "speechClass": "not_applicable", "language": "vi",
        "intent": intent, "engagement": "engaged", "safetyClass": safety_class,
        "assessmentEligible": False, "confidenceBand": "high",
        "activityId": "cat-recall-visual-02", "contextId": "cat_primary_visual_recall",
        "robotAudioContaminated": False, "targetTextVisible": False,
    })
    assert (await runtime.course_apply_response_plan({
        "lessonSessionId": identity, "turnSequenceId": 2, "observationId": "pause-plan",
        "planId": "pause-plan", "decisionId": paused["decisionId"],
        "acknowledgment": "Robot is here.", "relation": "", "guidance": "",
        "invitation": "Do you want to stop?", "questionCount": 1,
        "embodiedIntent": paused["embodiedIntent"], "targetFactsUsed": [],
        "praiseLevel": "engagement", "safetyMode": True, "normalMiss": False,
    }))["accepted"] is True
    continued = await runtime.course_continue({
        "lessonSessionId": identity, "turnSequenceId": 3, "observationId": "continue",
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
    await runtime.course_apply_response_plan({
        "lessonSessionId": identity, "turnSequenceId": 2, "observationId": "normal-plan",
        "planId": "normal-plan", "decisionId": normal["decisionId"],
        "acknowledgment": "Hello.", "relation": "", "guidance": "",
        "invitation": "Ready?", "questionCount": 1,
        "embodiedIntent": normal["embodiedIntent"], "targetFactsUsed": [],
        "praiseLevel": "engagement", "safetyMode": False, "normalMiss": False,
    })
    await runtime.course_observe_child({
        "lessonSessionId": identity, "turnSequenceId": 3, "observationId": "safety",
        "semanticClass": "unknown", "speechClass": "not_applicable", "language": "vi",
        "intent": "answer", "engagement": "engaged", "safetyClass": "safety",
        "assessmentEligible": False, "confidenceBand": "high",
        "activityId": "cat-recall-visual-02", "contextId": "cat_primary_visual_recall",
        "robotAudioContaminated": False, "targetTextVisible": False,
    })

    opened = await runtime.course_open_context({
        "lessonSessionId": identity, "turnSequenceId": 4, "observationId": "branch",
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
    assert stale_plan == {"accepted": False, "code": "DECISION_ALREADY_APPLIED"}
    assert "course_open_context" not in runtime.tool_context()["allowedTools"]


@pytest.mark.asyncio
async def test_natural_runtime_operations_can_reach_mastered_today() -> None:
    now = [0.0]
    runtime = course_mode_runtime_from_manifest(
        {"courseModeContract": contract()}, enabled=True, clock=lambda: now[0],
    )
    assert runtime is not None

    turn = 0

    def identity(observation_id: str) -> dict:
        nonlocal turn
        turn += 1
        return {
            "lessonSessionId": runtime.contract.lesson_session_id,
            "turnSequenceId": turn,
            "observationId": observation_id,
        }

    async def apply(decision, observation_id):
        return await runtime.course_apply_response_plan({
            **identity(observation_id), "planId": f"plan-{turn + 1}",
            "decisionId": decision["decisionId"], "acknowledgment": "Robot heard you.",
            "relation": "We can try another way.", "guidance": "Look at the picture.",
            "invitation": "Ready?", "questionCount": 1,
            "embodiedIntent": decision["embodiedIntent"],
            "targetFactsUsed": ["animals.cat"], "praiseLevel": "engagement",
            "safetyMode": False, "normalMiss": False,
        })

    for index in range(1, 4):
        opening = await runtime.course_continue(identity(f"continue-{index}"))
        await apply(opening, f"apply-opening-{index}")

    async def observe(observation_id, activity_id, context_id, semantic_class, speech_class):
        return await runtime.course_observe_child({
            **identity(observation_id), "semanticClass": semantic_class,
            "speechClass": speech_class, "language": "en", "intent": "answer",
            "engagement": "engaged", "safetyClass": "normal", "assessmentEligible": True,
            "confidenceBand": "high", "activityId": activity_id, "contextId": context_id,
            "robotAudioContaminated": False, "targetTextVisible": False,
        })

    now[0] = 5.0
    meaning = await observe("meaning", "cat-meaning-left-right-01", "cat_dog_visual_contrast", "meaning_vi", "not_applicable")
    await apply(meaning, "apply-meaning")
    model = await observe("needs-model", "cat-recall-visual-02", "cat_primary_visual_recall", "unknown", "silence")
    assert model["mayModelTarget"] is True
    await apply(model, "apply-model")
    alternate = await runtime.course_continue(identity("alternate"))
    await apply(alternate, "apply-alternate")
    now[0] = 30.0
    recall = await observe("recall", "cat-recall-visual-02", "cat_primary_visual_recall", "target_en", "exact")
    await apply(recall, "apply-recall")
    now[0] = 40.0
    transfer = await observe("transfer", "cat-transfer-scene-01", "cat_second_visual_scene", "target_en", "exact")
    await apply(transfer, "apply-transfer")
    now[0] = 70.0
    result = await observe("delayed", "cat-delayed-recall-01", "cat_delayed_callback", "target_en", "exact")
    assert result["evidenceEvent"]["evidenceLevel"] == "MASTERED_TODAY"


@pytest.mark.asyncio
async def test_optional_secondary_discovery_closes_after_one_authored_exposure() -> None:
    runtime = course_mode_runtime_from_manifest(
        {"courseModeContract": contract()}, enabled=True, clock=lambda: 300.0,
    )
    assert runtime is not None
    runtime.orchestrator.session_state = SessionState.WORD_ACTIVE
    runtime.orchestrator.active_mastery.level = EvidenceLevel.MASTERED_TODAY

    secondary = await runtime.course_continue({
        "lessonSessionId": runtime.lesson_session_id, "turnSequenceId": 1,
        "observationId": "start-secondary",
    })
    applied = await runtime.course_apply_response_plan({
        "lessonSessionId": runtime.lesson_session_id, "turnSequenceId": 2,
        "observationId": "apply-secondary", "planId": "secondary-plan",
        "decisionId": secondary["decisionId"], "acknowledgment": "I hear you.",
        "relation": "Here is another word.", "guidance": "This is a ball.",
        "invitation": "Ready?", "questionCount": 1,
        "embodiedIntent": secondary["embodiedIntent"], "targetFactsUsed": ["toys.ball"],
        "praiseLevel": "engagement", "safetyMode": False, "normalMiss": False,
    })
    closed = await runtime.course_continue({
        "lessonSessionId": runtime.lesson_session_id, "turnSequenceId": 3,
        "observationId": "close-secondary",
    })

    assert secondary["action"] == "START_OPTIONAL_SECONDARY"
    assert secondary["mayModelTarget"] is True
    assert applied["accepted"] is True
    assert closed["action"] == "CLOSE_AFTER_OPTIONAL_SECONDARY"
    assert closed["nextState"] == "CLOSING"


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
async def test_approved_response_plan_returns_exact_bounded_child_speech() -> None:
    runtime = course_mode_runtime_from_manifest(
        {"courseModeContract": contract()}, enabled=True, clock=lambda: 0.0,
    )
    assert runtime is not None
    decision = await runtime.course_continue({
        "lessonSessionId": runtime.lesson_session_id, "turnSequenceId": 1,
        "observationId": "speech-decision",
    })
    result = await runtime.course_apply_response_plan({
        "lessonSessionId": runtime.lesson_session_id, "turnSequenceId": 2,
        "observationId": "speech-plan", "planId": "speech-plan",
        "decisionId": decision["decisionId"], "acknowledgment": "I hear you.",
        "relation": "Let us look together.", "guidance": "Look at the picture.",
        "invitation": "What do you see?", "questionCount": 1,
        "embodiedIntent": decision["embodiedIntent"], "targetFactsUsed": ["animals.cat"],
        "praiseLevel": "engagement", "safetyMode": False, "normalMiss": False,
    })

    assert result["responseText"] == (
        "I hear you. Let us look together. Look at the picture. What do you see?"
    )


@pytest.mark.asyncio
async def test_response_plan_cannot_leak_target_when_decision_forbids_modeling() -> None:
    runtime = course_mode_runtime_from_manifest(
        {"courseModeContract": contract()}, enabled=True, clock=lambda: 0.0,
    )
    assert runtime is not None
    decision = await runtime.course_continue({
        "lessonSessionId": runtime.lesson_session_id, "turnSequenceId": 1,
        "observationId": "no-model-decision",
    })
    assert decision["mayModelTarget"] is False

    result = await runtime.course_apply_response_plan({
        "lessonSessionId": runtime.lesson_session_id, "turnSequenceId": 2,
        "observationId": "leaking-plan", "planId": "leaking-plan",
        "decisionId": decision["decisionId"], "acknowledgment": "I hear you.",
        "relation": "Let us look together.", "guidance": "The answer is cat.",
        "invitation": "What do you see?", "questionCount": 1,
        "embodiedIntent": decision["embodiedIntent"], "targetFactsUsed": [],
        "praiseLevel": "engagement", "safetyMode": False, "normalMiss": False,
    })

    assert result == {"accepted": False, "code": "INVALID_RESPONSE_PLAN"}
    assert runtime.orchestrator.active_mastery.answer_leakage.last_full_model_at_ms is None


@pytest.mark.asyncio
async def test_response_plan_cannot_leak_authored_target_meaning_during_assessment() -> None:
    runtime = course_mode_runtime_from_manifest(
        {"courseModeContract": contract()}, enabled=True, clock=lambda: 0.0,
    )
    assert runtime is not None
    decision = await runtime.course_continue({
        "lessonSessionId": runtime.lesson_session_id, "turnSequenceId": 1,
        "observationId": "no-meaning-model-decision",
    })

    result = await runtime.course_apply_response_plan({
        "lessonSessionId": runtime.lesson_session_id, "turnSequenceId": 2,
        "observationId": "meaning-leaking-plan", "planId": "meaning-leaking-plan",
        "decisionId": decision["decisionId"], "acknowledgment": "I hear you.",
        "relation": "Robot is here.", "guidance": "The answer is con mèo.",
        "invitation": "Ready?", "questionCount": 1,
        "embodiedIntent": decision["embodiedIntent"], "targetFactsUsed": [],
        "praiseLevel": "engagement", "safetyMode": False, "normalMiss": False,
    })

    assert result == {"accepted": False, "code": "INVALID_RESPONSE_PLAN"}


@pytest.mark.asyncio
async def test_safety_disclosure_and_authored_meaning_remain_in_protected_pause() -> None:
    runtime = course_mode_runtime_from_manifest(
        {"courseModeContract": contract()}, enabled=True, clock=lambda: 0.0,
    )
    assert runtime is not None
    decision = await runtime.course_open_context({
        "lessonSessionId": runtime.lesson_session_id, "turnSequenceId": 1,
        "observationId": "safety-disclosure", "branchType": "SAFETY_DISCLOSURE",
    })
    result = await runtime.course_apply_response_plan({
        "lessonSessionId": runtime.lesson_session_id, "turnSequenceId": 2,
        "observationId": "safety-plan", "planId": "safety-plan",
        "decisionId": decision["decisionId"], "acknowledgment": "Robot nghe con.",
        "relation": "", "guidance": "Mèo là con vật đáng yêu.",
        "invitation": "Con muốn robot ở yên không?", "questionCount": 1,
        "embodiedIntent": decision["embodiedIntent"], "targetFactsUsed": [],
        "praiseLevel": "engagement", "safetyMode": True, "normalMiss": False,
    })

    assert decision["nextState"] == "SAFETY_PAUSED"
    assert decision["branchId"] is None
    assert result == {"accepted": False, "code": "INVALID_RESPONSE_PLAN"}


@pytest.mark.asyncio
async def test_response_plan_rejects_rejected_decision_and_inactive_target_facts() -> None:
    runtime = course_mode_runtime_from_manifest(
        {"courseModeContract": contract()}, enabled=True, clock=lambda: 0.0,
    )
    assert runtime is not None
    runtime.orchestrator.session_state = SessionState.WORD_ACTIVE
    rejected = await runtime.course_observe_child({
        "lessonSessionId": runtime.lesson_session_id, "turnSequenceId": 1,
        "observationId": "invalid-activity", "semanticClass": "target_en",
        "speechClass": "exact", "language": "en", "intent": "answer",
        "engagement": "engaged", "safetyClass": "normal", "assessmentEligible": True,
        "confidenceBand": "high", "activityId": "invented", "contextId": "invented",
        "robotAudioContaminated": False, "targetTextVisible": False,
    })

    common = {
        "lessonSessionId": runtime.lesson_session_id,
        "acknowledgment": "Robot heard you.", "relation": "We can learn.",
        "guidance": "Look at the picture.", "invitation": "Ready?", "questionCount": 1,
        "embodiedIntent": rejected["embodiedIntent"], "praiseLevel": "engagement",
        "safetyMode": False, "normalMiss": False,
    }
    rejected_plan = await runtime.course_apply_response_plan({
        **common, "turnSequenceId": 2, "observationId": "rejected-plan",
        "planId": "rejected-plan", "decisionId": rejected["decisionId"],
        "targetFactsUsed": ["animals.cat"],
    })
    active = await runtime.course_continue({
        "lessonSessionId": runtime.lesson_session_id, "turnSequenceId": 2,
        "observationId": "active-decision",
    })
    inactive_fact_plan = await runtime.course_apply_response_plan({
        **common, "turnSequenceId": 3, "observationId": "inactive-fact-plan",
        "planId": "inactive-fact-plan", "decisionId": active["decisionId"],
        "embodiedIntent": active["embodiedIntent"], "targetFactsUsed": ["toys.ball"],
    })

    assert rejected_plan == {"accepted": False, "code": "COURSE_OPERATION_NOT_ALLOWED"}
    assert inactive_fact_plan == {"accepted": False, "code": "INVALID_RESPONSE_PLAN"}


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


@pytest.mark.asyncio
async def test_course_continue_closes_when_soft_deadline_has_elapsed() -> None:
    now = [0.0]
    runtime = course_mode_runtime_from_manifest(
        {"courseModeContract": contract()}, enabled=True, clock=lambda: now[0],
    )
    assert runtime is not None
    runtime.orchestrator.session_state = SessionState.WORD_ACTIVE
    now[0] = 540.0

    decision = await runtime.course_continue({
        "lessonSessionId": runtime.lesson_session_id, "turnSequenceId": 1,
        "observationId": "deadline-continue",
    })

    assert decision["action"] == "CLOSE_WITHOUT_SECOND_WORD"
    assert decision["nextState"] == "CLOSING"


@pytest.mark.asyncio
async def test_committed_closing_plan_completes_parent_runtime_and_emits_stop() -> None:
    now = [0.0]
    sent = []

    async def send(payload):
        sent.append(json.loads(payload))

    store = MemoryCourseModeSnapshotStore()
    runtime = LessonRuntime(
        _Conn(), assignment={"assignmentId": "a1", "lessonId": "l1"},
        manifest={"courseModeContract": contract()}, asset_cache=object(),
        forwarder=_Forwarder(), send=send, course_mode_snapshot_store=store,
        course_mode_snapshot_device_id="device-1",
    )
    runtime.course_mode._clock = lambda: now[0]
    runtime.course_mode.orchestrator.started_at_ms = 0
    runtime.course_mode.orchestrator.session_state = SessionState.WORD_ACTIVE
    now[0] = 540.0
    decision = await runtime.course_continue({
        "lessonSessionId": runtime.session_id, "turnSequenceId": 1,
        "observationId": "closing-decision",
    })
    plan = {
        "lessonSessionId": runtime.session_id, "turnSequenceId": 2,
        "observationId": "closing-plan", "planId": "closing-plan",
        "decisionId": decision["decisionId"], "acknowledgment": "I hear you.",
        "relation": "We can stop.", "guidance": "", "invitation": "",
        "questionCount": 0, "embodiedIntent": decision["embodiedIntent"],
        "targetFactsUsed": [], "praiseLevel": "engagement",
        "safetyMode": False, "normalMiss": False,
    }
    assert (await runtime.course_apply_response_plan(plan))["accepted"] is True
    assert await runtime.commit_course_response_plan(plan) is True
    snapshot = await store.load("device-1", "a1")

    assert runtime.course_mode.orchestrator.session_state is SessionState.COMPLETE
    assert snapshot["orchestrator"]["sessionState"] == "COMPLETE"
    assert sent[-1]["type"] == "lesson_stop"
    assert sent[-1]["body"]["reason"] == "COMPLETED"


@pytest.mark.asyncio
async def test_course_continue_closes_after_failed_delayed_recall() -> None:
    runtime = course_mode_runtime_from_manifest(
        {"courseModeContract": contract()}, enabled=True, clock=lambda: 70.0,
    )
    assert runtime is not None
    runtime.orchestrator.session_state = SessionState.WORD_ACTIVE
    mastery = runtime.orchestrator.active_mastery
    mastery.record_meaning(evidence_id="meaning", activity_id="meaning", context_id="choice")
    mastery.record_speech(
        evidence_id="recall", activity_id="recall", context_id="visual", now_ms=1_000,
        semantic_class="target_en", speech_class="exact", assessment_eligible=True,
        confidence_band="high",
    )
    mastery.record_transfer(evidence_id="transfer", activity_id="transfer", context_id="scene")
    runtime.orchestrator.word_state = WordState.DELAYED_RECALL
    observed = await runtime.course_observe_child({
        "lessonSessionId": runtime.lesson_session_id, "turnSequenceId": 1,
        "observationId": "delayed-miss", "semanticClass": "unknown",
        "speechClass": "silence", "language": "en", "intent": "answer",
        "engagement": "engaged", "safetyClass": "normal", "assessmentEligible": True,
        "confidenceBand": "high", "activityId": "cat-delayed-recall-01",
        "contextId": "cat_delayed_callback", "robotAudioContaminated": False,
        "targetTextVisible": False,
    })
    applied = await runtime.course_apply_response_plan({
        "lessonSessionId": runtime.lesson_session_id, "turnSequenceId": 2,
        "observationId": "delayed-miss-plan", "planId": "delayed-miss-plan",
        "decisionId": observed["decisionId"], "acknowledgment": "I hear you.",
        "relation": "We can stop.", "guidance": "",
        "invitation": "", "questionCount": 0,
        "embodiedIntent": observed["embodiedIntent"], "targetFactsUsed": [],
        "praiseLevel": "engagement", "safetyMode": False, "normalMiss": False,
    })
    assert applied["accepted"] is True

    decision = await runtime.course_continue({
        "lessonSessionId": runtime.lesson_session_id, "turnSequenceId": 3,
        "observationId": "after-delayed-miss",
    })

    assert decision["action"] == "CLOSE_AFTER_PRIMARY"
    assert decision["nextState"] == "CLOSING"


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
