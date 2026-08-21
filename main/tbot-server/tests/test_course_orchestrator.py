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


def test_duplicate_observation_and_restored_pending_effects_are_not_replayed() -> None:
    runtime = course(); runtime.begin(); runtime.continue_opening(); runtime.continue_opening()
    first = runtime.observe(observation(semantic_class="meaning_vi", activity_id="cat-meaning-left-right-01"))
    duplicate = runtime.observe(observation(semantic_class="meaning_vi", activity_id="cat-meaning-left-right-01"))
    assert first.accepted is True and duplicate.accepted is False
    snapshot = runtime.snapshot()
    restored = CourseOrchestrator.restore(runtime.contract, snapshot)
    replay = restored.observe(observation(semantic_class="meaning_vi", activity_id="cat-meaning-left-right-01"))
    assert replay.accepted is False
    assert restored.pending_effects == ()


def test_time_budget_prefers_one_deep_word_and_does_not_rush_secondary() -> None:
    runtime = course(); runtime.begin(); runtime.continue_opening(); runtime.continue_opening()
    decision = runtime.observe(observation(intent="answer", now_ms=541_000))
    assert decision.next_state is SessionState.CLOSING
    assert runtime.active_target_id == "animals.cat"
    assert decision.action == "CLOSE_WITHOUT_SECOND_WORD"


def test_secondary_starts_only_after_primary_mastery_and_time_remaining() -> None:
    runtime = course(); runtime.begin(); runtime.continue_opening(); runtime.continue_opening()
    runtime.active_mastery.level = runtime.active_mastery.level.MASTERED_TODAY
    decision = runtime.maybe_advance_target(now_ms=200_000)
    assert runtime.active_target_id == "toys.ball"
    assert decision.action == "START_OPTIONAL_SECONDARY"

