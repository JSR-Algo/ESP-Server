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
EXPECTED_OUTCOMES = {
    "knows word early": ("ACKNOWLEDGE_GUIDE_INVITE", "WORD_ACTIVE", "INDEPENDENT_RECALL"),
    "repetition only": ("MODEL_AND_SUPPORT", "WORD_ACTIVE", "SUPPORTED_SPEECH"),
    "Vietnamese answer": ("ACKNOWLEDGE_GUIDE_INVITE", "WORD_ACTIVE", "UNDERSTOOD"),
    "partial speech": ("MODEL_AND_SUPPORT", "WORD_ACTIVE", "NOT_STARTED"),
    "silence": ("MODEL_AND_SUPPORT", "WORD_ACTIVE", "NOT_STARTED"),
    "low confidence": ("OWN_ASR_UNCERTAINTY", "TECHNICAL_RECOVERY", "NOT_STARTED"),
    "unrelated story": ("OPEN_CONTEXT_BRANCH", "CONTEXT_BRANCH", "NOT_STARTED"),
    "emotional share": ("RESPOND_WITHOUT_REDIRECT", "REGULATION_BREAK", "NOT_STARTED"),
    "refusal": ("RESPOND_WITHOUT_REDIRECT", "REGULATION_BREAK", "NOT_STARTED"),
    "fatigue": ("RESPOND_WITHOUT_REDIRECT", "REGULATION_BREAK", "NOT_STARTED"),
    "question": ("OPEN_CONTEXT_BRANCH", "CONTEXT_BRANCH", "NOT_STARTED"),
    "barge-in": ("ACKNOWLEDGE_GUIDE_INVITE", "WORD_ACTIVE", "UNDERSTOOD"),
    "reconnect": ("WORD_ACTIVE", ()),
    "duplicate tool call": (True, False, "DUPLICATE_IGNORED"),
    "delayed recall success": ("MASTERED_TODAY", False),
    "delayed recall failure": ("TRANSFERRED", True),
    "one-word close": ("CLOSE_WITHOUT_SECOND_WORD", "animals.cat"),
    "two-word success": ("START_OPTIONAL_SECONDARY", "toys.ball"),
    "safety pause": ("PAUSE_FOR_SAFETY", "SAFETY_PAUSED", "NOT_STARTED"),
    "technical recovery": ("OWN_ASR_UNCERTAINTY", "TECHNICAL_RECOVERY", "NOT_STARTED"),
    "visible answer text": ("MODEL_AND_SUPPORT", "WORD_ACTIVE", "SUPPORTED_SPEECH"),
    "routine related story bridge": (
        "OPEN_CONTEXT_BRANCH", "RETURN_THROUGH_AUTHORED_BRIDGE", "bridge_white_cat_visual",
    ),
}


def runtime() -> CourseOrchestrator:
    value = CourseOrchestrator(CONTRACT, started_at_ms=0, soft_deadline_ms=540_000)
    value.begin(); value.continue_opening(); value.continue_opening()
    return value


def observe(value: CourseOrchestrator, row: dict, observation_id: str = "o1", now_ms: int = 30_000):
    meaning = row.get("semantic") == "meaning_vi"
    return value.observe(ChildObservation(
        observation_id=observation_id, turn_sequence_id=1,
        semantic_class=row.get("semantic", "unknown"), speech_class=row.get("speech", "not_applicable"),
        language="vi", intent=row.get("intent", "answer"), engagement="engaged",
        safety_class=row.get("safety", "normal"), assessment_eligible=row.get("eligible", True),
        confidence_band=row.get("confidence", "high"),
        activity_id="cat-meaning-left-right-01" if meaning else "cat-recall-visual-02",
        context_id="cat_dog_visual_contrast" if meaning else "cat_primary_visual_recall", now_ms=now_ms,
        robot_audio_contaminated=row.get("contaminated", False), target_text_visible=row.get("visible", False),
    ))


def observe_activity(
    value: CourseOrchestrator, *, observation_id: str, activity_id: str, context_id: str,
    now_ms: int, semantic_class: str = "target_en", speech_class: str = "exact",
):
    return value.observe(ChildObservation(
        observation_id=observation_id, turn_sequence_id=1,
        semantic_class=semantic_class, speech_class=speech_class,
        language="vi", intent="answer", engagement="engaged", safety_class="normal",
        assessment_eligible=True, confidence_band="high", activity_id=activity_id,
        context_id=context_id, now_ms=now_ms, robot_audio_contaminated=False,
        target_text_visible=False,
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


def test_all_scripted_journeys_match_explicit_expected_outcomes() -> None:
    assert set(EXPECTED_OUTCOMES) == {row["name"] for row in JOURNEYS}
    for row in JOURNEYS:
        assert _run(row) == EXPECTED_OUTCOMES[row["name"]], row["name"]


def test_all_soak_journeys_reach_a_closing_session_state() -> None:
    for row in JOURNEYS:
        result = _run_full_session(row)
        assert result["finalState"] == "CLOSING", row["name"]
        assert result["operations"][:3] == (
            "GREET_AND_CHECK_IN",
            "ACKNOWLEDGE_AND_BUILD_CURIOSITY",
            "CLUE_AND_ELICIT",
        ), row["name"]
        assert result["steps"] == len(result["operations"]), row["name"]
        assert result["steps"] >= 4, row["name"]
        assert not any(action.startswith("RUN_SCENARIO:") for action in result["operations"])
        assert result["initialOutcome"] == EXPECTED_OUTCOMES[row["name"]], row["name"]


def test_reconnect_soak_continues_on_the_restored_orchestrator(monkeypatch) -> None:
    restored_instances = []
    advanced_instances = []
    original_restore = CourseOrchestrator.restore
    original_advance = CourseOrchestrator.maybe_advance_target

    def tracking_restore(cls, contract, snapshot):
        restored = original_restore(contract, snapshot)
        restored_instances.append(restored)
        return restored

    def tracking_advance(self, *, now_ms):
        advanced_instances.append(self)
        return original_advance(self, now_ms=now_ms)

    monkeypatch.setattr(CourseOrchestrator, "restore", classmethod(tracking_restore))
    monkeypatch.setattr(CourseOrchestrator, "maybe_advance_target", tracking_advance)

    row = next(row for row in JOURNEYS if row["name"] == "reconnect")
    result = _run_full_session(row)

    assert result["finalState"] == "CLOSING"
    assert len(restored_instances) == 1
    assert advanced_instances
    assert all(value is restored_instances[0] for value in advanced_instances)


def test_delayed_recall_journeys_use_orchestrator_observations(monkeypatch) -> None:
    activity_ids = []
    original_observe = CourseOrchestrator.observe

    def tracking_observe(self, observation):
        activity_ids.append(observation.activity_id)
        return original_observe(self, observation)

    monkeypatch.setattr(CourseOrchestrator, "observe", tracking_observe)

    row = next(row for row in JOURNEYS if row["name"] == "delayed recall success")
    assert _run(row) == EXPECTED_OUTCOMES[row["name"]]
    assert activity_ids == [
        "cat-meaning-left-right-01",
        "cat-recall-visual-02",
        "cat-transfer-scene-01",
        "cat-delayed-recall-01",
    ]


def _run_full_session(row: dict) -> dict:
    value = CourseOrchestrator(CONTRACT, started_at_ms=0, soft_deadline_ms=540_000)
    operations = [
        value.begin().action,
        value.continue_opening().action,
        value.continue_opening().action,
    ]
    initial, value, scenario_operations = _run_scenario(row, value)
    operations.extend(scenario_operations)
    if value.session_state.value == "CONTEXT_BRANCH":
        decision = value.close_context_branch(
            branch_id=value.snapshot()["activeBranchId"],
            bridge_intent="white_cat_visual",
            child_detail_code="no_personal_detail",
        )
        operations.append(decision.action)
    if value.session_state.value in {"REGULATION_BREAK", "SAFETY_PAUSED"}:
        decision = value.observe(ChildObservation(
            observation_id=f"close-{row['name']}", turn_sequence_id=99,
            semantic_class="unknown", speech_class="not_applicable", language="vi",
            intent="stop", engagement="engaged", safety_class="normal",
            assessment_eligible=False, confidence_band="high",
            activity_id="cat-recall-visual-02", context_id="cat_primary_visual_recall",
            now_ms=120_000, robot_audio_contaminated=False, target_text_visible=False,
        ))
        operations.append(decision.action)
    elif value.session_state.value == "TECHNICAL_RECOVERY":
        decision = value.observe(ChildObservation(
            observation_id=f"deadline-{row['name']}", turn_sequence_id=99,
            semantic_class="unknown", speech_class="silence", language="vi",
            intent="answer", engagement="engaged", safety_class="normal",
            assessment_eligible=False, confidence_band="high",
            activity_id="cat-recall-visual-02", context_id="cat_primary_visual_recall",
            now_ms=541_000, robot_audio_contaminated=False, target_text_visible=False,
        ))
        operations.append(decision.action)
    if value.session_state.value == "WORD_ACTIVE":
        decision = value.maybe_advance_target(now_ms=500_000)
        operations.append(decision.action)
        if value.session_state.value == "WORD_ACTIVE":
            decision = value.maybe_advance_target(now_ms=500_001)
            operations.append(decision.action)
    return {
        "initialOutcome": initial,
        "finalState": value.session_state.value,
        "steps": len(operations),
        "operations": tuple(operations),
    }


def _run(row: dict, *, value: CourseOrchestrator | None = None):
    value = value or runtime()
    result, _, _ = _run_scenario(row, value)
    return result


def _run_scenario(row: dict, value: CourseOrchestrator):
    if row["name"] == "repetition only":
        value.active_mastery.record_model(now_ms=25_000)
    special = row.get("special")
    if special == "restore":
        restored = CourseOrchestrator.restore(CONTRACT, value.snapshot())
        return (restored.session_state.value, restored.pending_effects), restored, ("RESTORE_SNAPSHOT",)
    if special == "duplicate":
        first = observe(value, row)
        second = observe(value, row)
        return (first.accepted, second.accepted, second.action), value, (first.action, second.action)
    if special == "deadline":
        decision = observe(value, row, now_ms=541_000)
        return (decision.action, value.active_target_id), value, (decision.action,)
    if special == "secondary":
        value.active_mastery.level = EvidenceLevel.MASTERED_TODAY
        decision = value.maybe_advance_target(now_ms=200_000)
        return (decision.action, value.active_target_id), value, (decision.action,)
    if special == "bridge":
        opened = observe(value, {"semantic": "related", "intent": "story"})
        closed = value.close_context_branch(
            branch_id=opened.branch_id, bridge_intent="white_cat_visual", child_detail_code="grandmother_pet",
        )
        return (opened.action, closed.action, closed.teaching_intent), value, (opened.action, closed.action)
    if special in {"delayed_success", "delayed_failure"}:
        mastery = value.active_mastery
        meaning = observe_activity(
            value, observation_id="m", activity_id="cat-meaning-left-right-01",
            context_id="cat_dog_visual_contrast", now_ms=1_000,
            semantic_class="meaning_vi", speech_class="not_applicable",
        )
        mastery.record_model(now_ms=1_000)
        mastery.record_intervening_activity()
        recall = observe_activity(
            value, observation_id="r", activity_id="cat-recall-visual-02",
            context_id="cat_primary_visual_recall", now_ms=30_000,
        )
        transfer = observe_activity(
            value, observation_id="t", activity_id="cat-transfer-scene-01",
            context_id="cat_second_visual_scene", now_ms=50_000,
        )
        delayed = observe_activity(
            value, observation_id="d", activity_id="cat-delayed-recall-01",
            context_id="cat_delayed_callback", now_ms=70_000,
            semantic_class="target_en" if special == "delayed_success" else "unknown",
            speech_class="exact" if special == "delayed_success" else "silence",
        )
        return (
            (mastery.level.value, bool(delayed.evidence_event and delayed.evidence_event["reviewNeeded"])),
            value,
            (meaning.action, recall.action, transfer.action, delayed.action),
        )
    decision = observe(value, row)
    assert decision.action == row["expectedAction"], row["name"]
    if row["name"] in {"repetition only", "visible answer text"}:
        assert value.active_mastery.level is not EvidenceLevel.INDEPENDENT_RECALL
    if row["name"] in {"emotional share", "safety pause"}:
        assert decision.question_intent not in {"elicit_target", "recall_target"}
    return (
        (decision.action, decision.next_state.value, value.active_mastery.level.value),
        value,
        (decision.action,),
    )
