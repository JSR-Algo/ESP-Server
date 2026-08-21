from __future__ import annotations

import json
from pathlib import Path

from core.lesson.course_mode_contract import CourseModeContract
from core.lesson.course_orchestrator import ChildObservation, CourseOrchestrator, SessionState, WordState
from core.lesson.embodied_intent import EmbodiedIntent


FIXTURE = Path(__file__).parent / "fixtures" / "course-mode" / "course-mode-pilot-cat-ball.json"


def course(*, now_ms: int = 0) -> CourseOrchestrator:
    contract = CourseModeContract.from_mapping(json.loads(FIXTURE.read_text(encoding="utf-8")))
    return CourseOrchestrator(contract, started_at_ms=now_ms, soft_deadline_ms=540_000)


def observation(**overrides) -> ChildObservation:
    values = {
        "observation_id": "o1", "turn_sequence_id": 1, "semantic_class": "unknown",
        "speech_class": "not_applicable", "language": "vi", "intent": "answer",
        "engagement": "engaged", "safety_class": "normal", "assessment_eligible": True,
        "confidence_band": "high", "activity_id": "cat-discover-center-01", "context_id": "cat_primary_visual",
        "now_ms": 1_000, "robot_audio_contaminated": False, "target_text_visible": False,
    }
    values.update(overrides)
    return ChildObservation(**values)


def test_opening_follows_social_before_teaching_sequence() -> None:
    runtime = course()
    assert [runtime.begin().action, runtime.continue_opening().action, runtime.continue_opening().action] == [
        "GREET_AND_CHECK_IN", "ACKNOWLEDGE_AND_BUILD_CURIOSITY", "CLUE_AND_ELICIT",
    ]
    assert runtime.session_state is SessionState.WORD_ACTIVE
    assert runtime.word_state is WordState.DISCOVER


def test_related_story_is_meaningfully_acknowledged_then_authored_bridge_returns() -> None:
    runtime = course()
    runtime.begin(); runtime.continue_opening(); runtime.continue_opening()
    before = runtime.active_mastery.level
    opened = runtime.observe(observation(intent="story", semantic_class="related"))
    assert opened.next_state is SessionState.CONTEXT_BRANCH
    assert opened.acknowledgment_intent == "acknowledge_related_story"
    returned = runtime.close_context_branch(
        branch_id=opened.branch_id, bridge_intent="white_cat_visual", child_detail_code="grandmother_pet",
    )
    assert returned.next_state is SessionState.WORD_ACTIVE
    assert returned.teaching_intent == "bridge_white_cat_visual"
    assert runtime.active_mastery.level is before


def test_context_branch_rejects_unbounded_model_invented_identifiers() -> None:
    runtime = course()
    runtime.begin(); runtime.continue_opening(); runtime.continue_opening()

    invalid_open = runtime.open_context_branch(
        observation_id="invented-open", turn_sequence_id=4,
        branch_type="INVENTED_BRANCH",
    )
    assert invalid_open.accepted is False
    assert invalid_open.action == "INVALID_CONTEXT_BRANCH_IGNORED"
    assert runtime.session_state is SessionState.WORD_ACTIVE

    opened = runtime.open_context_branch(
        observation_id="valid-open", turn_sequence_id=5,
        branch_type="RELATED_STORY",
    )
    invalid_close = runtime.close_context_branch(
        branch_id=opened.branch_id,
        bridge_intent="invented_curriculum",
        child_detail_code="raw_free_form_story",
    )
    assert invalid_close.accepted is False
    assert invalid_close.action == "INVALID_CONTEXT_BRIDGE_IGNORED"
    assert runtime.session_state is SessionState.CONTEXT_BRANCH


def test_safety_disclosure_context_enters_protected_pause_without_story_bridge() -> None:
    runtime = course()

    decision = runtime.open_context_branch(
        observation_id="safety-disclosure", turn_sequence_id=1,
        branch_type="SAFETY_DISCLOSURE",
    )

    assert decision.action == "PAUSE_FOR_SAFETY"
    assert decision.next_state is SessionState.SAFETY_PAUSED
    assert decision.embodied_intent is EmbodiedIntent.COMFORT_CALM
    assert decision.branch_id is None


def test_emotional_safety_refusal_and_fatigue_never_force_vocabulary() -> None:
    for intent, safety, expected_state in (
        ("emotional_share", "normal", SessionState.REGULATION_BREAK),
        ("answer", "safety", SessionState.SAFETY_PAUSED),
        ("refusal", "normal", SessionState.REGULATION_BREAK),
        ("fatigue", "normal", SessionState.REGULATION_BREAK),
    ):
        runtime = course(); runtime.begin(); runtime.continue_opening(); runtime.continue_opening()
        decision = runtime.observe(observation(intent=intent, safety_class=safety))
        assert decision.next_state is expected_state
        assert decision.question_intent not in {"elicit_target", "recall_target"}
        assert decision.embodied_intent in {EmbodiedIntent.COMFORT_CALM, EmbodiedIntent.PAUSE_CHOICE}


def test_safety_disclosure_preempts_regulation_resume_intent() -> None:
    runtime = course()
    runtime.observe(observation(observation_id="fatigue", intent="fatigue"))

    decision = runtime.observe(observation(
        observation_id="safety", turn_sequence_id=2,
        intent="resume", safety_class="safety",
    ))

    assert decision.action == "PAUSE_FOR_SAFETY"
    assert decision.next_state is SessionState.SAFETY_PAUSED
    assert decision.embodied_intent is EmbodiedIntent.COMFORT_CALM


def test_low_confidence_and_contamination_do_not_mutate_mastery() -> None:
    runtime = course(); runtime.begin(); runtime.continue_opening(); runtime.continue_opening()
    before = runtime.active_mastery.level
    decision = runtime.observe(observation(
        semantic_class="target_en", speech_class="exact", confidence_band="low",
        robot_audio_contaminated=True, assessment_eligible=False,
    ))
    assert runtime.active_mastery.level is before
    assert decision.evidence_event is None
    assert decision.action == "OWN_ASR_UNCERTAINTY"


def test_non_assessment_understanding_turn_can_record_meaning() -> None:
    runtime = course(); runtime.begin(); runtime.continue_opening(); runtime.continue_opening()

    decision = runtime.observe(observation(
        semantic_class="meaning_vi", assessment_eligible=False,
        activity_id="cat-meaning-left-right-01", context_id="cat_dog_visual_contrast",
    ))

    assert decision.accepted is True
    assert runtime.active_mastery.level.value == "UNDERSTOOD"
    assert runtime.session_state is SessionState.WORD_ACTIVE


def test_duplicate_observation_and_restored_pending_effects_are_not_replayed() -> None:
    runtime = course(); runtime.begin(); runtime.continue_opening(); runtime.continue_opening()
    first = runtime.observe(observation(
        semantic_class="meaning_vi", activity_id="cat-meaning-left-right-01",
        context_id="cat_dog_visual_contrast",
    ))
    duplicate = runtime.observe(observation(
        semantic_class="meaning_vi", activity_id="cat-meaning-left-right-01",
        context_id="cat_dog_visual_contrast",
    ))
    assert first.accepted is True and duplicate.accepted is False
    snapshot = runtime.snapshot()
    restored = CourseOrchestrator.restore(runtime.contract, snapshot)
    replay = restored.observe(observation(
        semantic_class="meaning_vi", activity_id="cat-meaning-left-right-01",
        context_id="cat_dog_visual_contrast",
    ))
    assert replay.accepted is False
    assert restored.pending_effects == ()


def test_snapshot_restore_preserves_session_deadline_basis() -> None:
    runtime = course(now_ms=1_000_000)
    runtime.begin(); runtime.continue_opening(); runtime.continue_opening()
    restored = CourseOrchestrator.restore(runtime.contract, runtime.snapshot())

    decision = restored.observe(observation(observation_id="after-reconnect", now_ms=1_020_000))

    assert decision.action != "CLOSE_WITHOUT_SECOND_WORD"


def test_failed_delayed_recall_records_review_without_losing_transfer() -> None:
    runtime = course()
    runtime.begin(); runtime.continue_opening(); runtime.continue_opening()
    mastery = runtime.active_mastery
    mastery.record_meaning(evidence_id="meaning", activity_id="meaning", context_id="choice")
    mastery.record_model(now_ms=1_000)
    mastery.record_intervening_activity()
    mastery.record_speech(
        evidence_id="recall", activity_id="recall", context_id="visual", now_ms=30_000,
        semantic_class="target_en", speech_class="exact", assessment_eligible=True,
        confidence_band="high",
    )
    mastery.record_transfer(evidence_id="transfer", activity_id="transfer", context_id="scene")

    decision = runtime.observe(observation(
        observation_id="delayed-miss", semantic_class="unknown", speech_class="silence",
        activity_id="cat-delayed-recall-01", context_id="cat_delayed_callback", now_ms=70_000,
    ))

    assert mastery.level.name == "TRANSFERRED"
    assert mastery.snapshot()["missesAfterRecall"] == 1
    assert decision.evidence_event is not None
    assert decision.evidence_event["reviewNeeded"] is True


def test_protected_pause_rejects_normal_learning_observations() -> None:
    runtime = course()
    runtime.begin(); runtime.continue_opening(); runtime.continue_opening()
    runtime.observe(observation(
        observation_id="safety", safety_class="safety", assessment_eligible=False,
    ))
    before = runtime.active_mastery.level

    decision = runtime.observe(observation(
        observation_id="normal-after-safety", semantic_class="meaning_vi",
        activity_id="cat-meaning-left-right-01", context_id="cat_dog_visual_contrast",
    ))

    assert decision.action == "HOLD_PROTECTED_PAUSE"
    assert decision.next_state is SessionState.SAFETY_PAUSED
    assert decision.evidence_event is None
    assert runtime.active_mastery.level is before


def test_regulation_break_requires_explicit_resume_or_stop_choice() -> None:
    runtime = course()
    runtime.begin(); runtime.continue_opening(); runtime.continue_opening()
    runtime.observe(observation(observation_id="fatigue", intent="fatigue"))

    held = runtime.observe(observation(observation_id="ordinary", intent="answer"))
    resumed = runtime.observe(observation(observation_id="resume", intent="resume"))

    assert held.action == "HOLD_PROTECTED_PAUSE"
    assert resumed.action == "RESUME_AFTER_REGULATION"
    assert resumed.next_state is SessionState.WORD_ACTIVE

    runtime.observe(observation(observation_id="refusal", intent="refusal"))
    stopped = runtime.observe(observation(observation_id="stop", intent="stop"))
    assert stopped.action == "CLOSE_BY_CHILD_CHOICE"
    assert stopped.next_state is SessionState.CLOSING


def test_safety_pause_allows_only_explicit_stop_not_resume() -> None:
    runtime = course()
    runtime.begin(); runtime.continue_opening(); runtime.continue_opening()
    runtime.observe(observation(
        observation_id="safety", safety_class="safety", assessment_eligible=False,
    ))

    held = runtime.observe(observation(observation_id="resume", intent="resume"))
    stopped = runtime.observe(observation(observation_id="stop", intent="stop"))

    assert held.action == "HOLD_PROTECTED_PAUSE"
    assert held.next_state is SessionState.SAFETY_PAUSED
    assert stopped.action == "CLOSE_BY_SAFETY_CHOICE"
    assert stopped.next_state is SessionState.CLOSING


def test_time_budget_prefers_one_deep_word_and_does_not_rush_secondary() -> None:
    runtime = course(); runtime.begin(); runtime.continue_opening(); runtime.continue_opening()
    decision = runtime.observe(observation(intent="answer", now_ms=541_000))
    assert decision.next_state is SessionState.CLOSING
    assert runtime.active_target_id == "animals.cat"
    assert decision.action == "CLOSE_WITHOUT_SECOND_WORD"


def test_safety_disclosure_at_deadline_still_enters_protected_pause() -> None:
    runtime = course()
    runtime.begin(); runtime.continue_opening(); runtime.continue_opening()

    decision = runtime.observe(observation(
        observation_id="deadline-safety", safety_class="safety",
        assessment_eligible=False, now_ms=540_000,
    ))

    assert decision.action == "PAUSE_FOR_SAFETY"
    assert decision.next_state is SessionState.SAFETY_PAUSED
    assert decision.embodied_intent is EmbodiedIntent.COMFORT_CALM


def test_secondary_starts_only_after_primary_mastery_and_time_remaining() -> None:
    runtime = course(); runtime.begin(); runtime.continue_opening(); runtime.continue_opening()
    runtime.active_mastery.level = runtime.active_mastery.level.MASTERED_TODAY
    decision = runtime.maybe_advance_target(now_ms=200_000)
    assert runtime.active_target_id == "toys.ball"
    assert decision.action == "START_OPTIONAL_SECONDARY"


def test_only_authored_active_target_activity_and_stage_can_advance_evidence() -> None:
    runtime = course(); runtime.begin(); runtime.continue_opening(); runtime.continue_opening()
    runtime.active_mastery.record_model(now_ms=1_000)
    runtime.active_mastery.record_intervening_activity()

    discover = runtime.observe(observation(
        observation_id="discover", semantic_class="target_en", speech_class="exact",
        activity_id="cat-discover-center-01", context_id="cat_primary_visual", now_ms=30_000,
    ))
    assert discover.evidence_event is None
    assert runtime.active_mastery.level.name == "EXPOSED"

    wrong_target = runtime.observe(observation(
        observation_id="wrong-target", semantic_class="target_en", speech_class="exact",
        activity_id="ball-discover-center-01", context_id="ball_primary_visual", now_ms=31_000,
    ))
    assert wrong_target.accepted is False
    assert wrong_target.action == "INVALID_ACTIVITY_IGNORED"

    wrong_context = runtime.observe(observation(
        observation_id="wrong-context", semantic_class="target_en", speech_class="exact",
        activity_id="cat-recall-visual-02", context_id="invented", now_ms=32_000,
    ))
    assert wrong_context.accepted is False
    assert runtime.active_mastery.level.name == "EXPOSED"
