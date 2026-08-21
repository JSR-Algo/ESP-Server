from __future__ import annotations

import json
from pathlib import Path

from core.lesson.course_mode_contract import CourseModeContract
from core.lesson.course_orchestrator import ChildObservation, CourseOrchestrator
from core.lesson.word_mastery import EvidenceLevel


ROOT = Path(__file__).parent / "fixtures"
JOURNEYS = json.loads((ROOT / "course_mode_journeys.json").read_text(encoding="utf-8"))
CONTRACT = CourseModeContract.from_mapping(json.loads(
    (ROOT / "course-mode" / "course-mode-pilot-cat-ball.json").read_text(encoding="utf-8")
))


def runtime() -> CourseOrchestrator:
    value = CourseOrchestrator(CONTRACT, started_at_ms=0, soft_deadline_ms=540_000)
    value.begin(); value.continue_opening(); value.continue_opening()
    return value


def observe(value: CourseOrchestrator, row: dict, observation_id: str = "o1", now_ms: int = 30_000):
    return value.observe(ChildObservation(
        observation_id=observation_id, turn_sequence_id=1,
        semantic_class=row.get("semantic", "unknown"), speech_class=row.get("speech", "not_applicable"),
        language="vi", intent=row.get("intent", "answer"), engagement="engaged",
        safety_class=row.get("safety", "normal"), assessment_eligible=row.get("eligible", True),
        confidence_band=row.get("confidence", "high"), activity_id="cat-recall-visual-02",
        context_id="cat_primary_visual_recall", now_ms=now_ms,
        robot_audio_contaminated=row.get("contaminated", False), target_text_visible=row.get("visible", False),
    ))


def test_fixture_covers_all_required_deterministic_child_journeys_without_real_child_data() -> None:
    required = {
        "knows word early", "repetition only", "Vietnamese answer", "partial speech", "silence",
        "low confidence", "unrelated story", "emotional share", "refusal", "fatigue", "question",
        "barge-in", "reconnect", "duplicate tool call", "delayed recall success",
        "delayed recall failure", "one-word close", "two-word success", "safety pause",
        "technical recovery",
    }
    assert required <= {row["name"] for row in JOURNEYS}
    assert len(JOURNEYS) >= 20
    serialized = json.dumps(JOURNEYS).casefold()
    assert "transcript" not in serialized and "audio" not in serialized and "family story" not in serialized


def test_all_scripted_journeys_are_deterministic_and_truthful() -> None:
    for row in JOURNEYS:
        first = _run(row)
        second = _run(row)
        assert first == second, row["name"]


def _run(row: dict):
    value = runtime()
    special = row.get("special")
    if special == "restore":
        restored = CourseOrchestrator.restore(CONTRACT, value.snapshot())
        return restored.session_state.value, restored.pending_effects
    if special == "duplicate":
        first = observe(value, row)
        second = observe(value, row)
        return first.accepted, second.accepted, second.action
    if special == "deadline":
        decision = observe(value, row, now_ms=541_000)
        return decision.action, value.active_target_id
    if special == "secondary":
        value.active_mastery.level = EvidenceLevel.MASTERED_TODAY
        decision = value.maybe_advance_target(now_ms=200_000)
        return decision.action, value.active_target_id
    if special == "bridge":
        opened = observe(value, {"semantic": "related", "intent": "story"})
        closed = value.close_context_branch(
            branch_id=opened.branch_id, bridge_intent="white_cat_visual", child_detail_code="grandmother_pet",
        )
        return opened.action, closed.action, closed.teaching_intent
    if special in {"delayed_success", "delayed_failure"}:
        mastery = value.active_mastery
        mastery.record_meaning(evidence_id="m", activity_id="meaning", context_id="choice")
        mastery.record_model(now_ms=1_000); mastery.record_intervening_activity()
        mastery.record_speech(
            evidence_id="r", activity_id="recall", context_id="visual", now_ms=30_000,
            semantic_class="target_en", speech_class="exact", assessment_eligible=True, confidence_band="high",
        )
        mastery.record_transfer(evidence_id="t", activity_id="transfer", context_id="scene")
        result = mastery.record_delayed_recall(
            evidence_id="d", activity_id="delayed", context_id="callback", now_ms=70_000,
            assessment_eligible=special == "delayed_success", confidence_band="high",
        )
        return result.level.value, result.review_needed
    decision = observe(value, row)
    assert decision.action == row["expectedAction"], row["name"]
    if row["name"] in {"repetition only", "visible answer text"}:
        assert value.active_mastery.level is not EvidenceLevel.INDEPENDENT_RECALL
    if row["name"] in {"emotional share", "safety pause"}:
        assert decision.question_intent not in {"elicit_target", "recall_target"}
    return decision.action, decision.next_state.value, value.active_mastery.level.value
