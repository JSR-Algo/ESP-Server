from __future__ import annotations

import copy
import hashlib
import json
import pytest

from core.lesson.course_mode_contract import CourseModeContract
from core.lesson.course_orchestrator import ChildObservation, CourseOrchestrator, SessionState
from core.lesson.interaction_templates import curriculum_outcome_name


def _checksum(value: dict) -> str:
    payload = {key: child for key, child in value.items() if key != "contractChecksum"}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def curriculum_contract() -> dict:
    targets = [
        {
            "targetId": "w01.hello",
            "targetWord": "hello",
            "role": "primary",
            "vietnameseMeanings": ["xin chao"],
            "activityIds": [f"a{i}" for i in range(1, 8)],
        },
        {
            "targetId": "w01.goodbye",
            "targetWord": "goodbye",
            "role": "primary",
            "vietnameseMeanings": ["tam biet"],
            "activityIds": [f"a{i}" for i in range(1, 8)],
        },
        {
            "targetId": "w01.friend",
            "targetWord": "friend",
            "role": "exposure",
            "vietnameseMeanings": ["ban"],
            "activityIds": ["a1"],
        },
    ]
    stages = ["DISCOVER", "UNDERSTAND", "GUIDED_ACTION", "SUPPORTED_SPEECH", "RECALL", "TRANSFER", "DELAYED_RECALL"]
    activities = []
    for index, stage in enumerate(stages, 1):
        last = index == len(stages)
        activities.append(
            {
                "activityId": f"a{index}",
                "targetIds": ["w01.hello", "w01.goodbye"] + (["w01.friend"] if index == 1 else []),
                "stage": stage,
                "activityType": stage.casefold(),
                "evidenceName": "MASTERED_TODAY" if last else "EXPOSED",
                "contextId": f"context.{index}",
                "embodiedIntent": "PRESENT_CENTER",
                "visualFocusRegion": "focus.center.primary",
                "answerPolicy": {
                    "targetTextVisible": False,
                    "targetAudioBeforeAssessment": False,
                    "spokenTargetInPrompt": False,
                    "multipleChoiceContainsTarget": False,
                    "minElapsedSinceFullModelMs": 20_000 if stage in {"RECALL", "TRANSFER", "DELAYED_RECALL"} else 0,
                    "minInterveningActivityCount": 1 if stage in {"RECALL", "TRANSFER", "DELAYED_RECALL"} else 0,
                },
                "listeningTransition": ["speech_complete", "assessment_window_open"],
                "reducedMotionFallback": "face_and_transient_focus_cue",
                "modalities": ["speech_en", "speech_vi", "choice", "silence", "help"],
                "expectedDurationSec": 60,
                "outcomes": (
                    {
                        "correct": {"action": "advance"},
                        "near": {"action": "advance"},
                        "incorrect": {"action": "support", "activityId": f"a{index + 1}"},
                        "silence": {"action": "retry", "activityId": f"a{index + 1}"},
                        "vietnamese": {"action": "advance"},
                        "help": {"action": "support", "activityId": f"a{index + 1}"},
                        "fatigue": {"action": "pause"},
                        "refusal": {"action": "pause"},
                        "story": {"action": "retry", "activityId": f"a{index + 1}"},
                    }
                    if not last
                    else {
                        "correct": {"action": "complete"},
                        "near": {"action": "complete"},
                        "incorrect": {"action": "close"},
                        "silence": {"action": "close"},
                        "help": {"action": "complete"},
                        "fatigue": {"action": "pause"},
                        "refusal": {"action": "pause"},
                    }
                ),
                "visual": {
                    "strategy": "publishedTeachingObject",
                    "backgroundAssetKey": "scene.playroom",
                    "objectAssetKey": "object.hello",
                    "fallback": "robotActing",
                },
            }
        )
    value = {
        "schemaVersion": 1,
        "contractVersion": "courseCompanion.v2.contract.v1",
        "contractChecksum": "",
        "checksumRules": {"algorithm": "SHA-256"},
        "fixtureId": "curriculum.w01",
        "preset": {"presetId": "courseCompanion", "presetVersion": 2},
        "lesson": {"lessonId": "w01", "lessonVersion": 1, "lessonSessionId": "w01.session"},
        "session": {"softDeadlineSec": 480, "maxAttempts": 3, "listenTimeoutSec": 6},
        "targets": targets,
        "evidenceNames": ["EXPOSED", "MASTERED_TODAY"],
        "embodiedIntentNames": ["PRESENT_CENTER"],
        "visualFocus": {
            "directionSource": "authored_visual_focus_region",
            "regions": ["focus.center.primary"],
            "presentCenterTarget": "single_teaching_object",
        },
        "activities": activities,
        "renderer": {
            "rendererId": "teebot-lesson-renderer.v5",
            "visualLayoutContract": "renderer-v5.layered-cinematic-layout.v1",
        },
    }
    value["contractChecksum"] = _checksum(value)
    return value


def observation(
    activity_id: str, *, semantic: str = "target_en", speech: str = "exact", intent: str = "answer", now_ms: int = 1_000
) -> ChildObservation:
    return ChildObservation(
        observation_id=f"o-{activity_id}-{semantic}-{speech}-{intent}-{now_ms}",
        turn_sequence_id=1,
        semantic_class=semantic,
        speech_class=speech,
        language="vi" if semantic == "meaning_vi" else "en",
        intent=intent,
        engagement="engaged",
        safety_class="normal",
        assessment_eligible=True,
        confidence_band="high",
        activity_id=activity_id,
        context_id=f"context.{activity_id[1:]}",
        now_ms=now_ms,
        robot_audio_contaminated=False,
        target_text_visible=False,
    )


def running() -> CourseOrchestrator:
    value = CourseOrchestrator(
        CourseModeContract.from_mapping(curriculum_contract()), started_at_ms=0, soft_deadline_ms=480_000
    )
    value.session_state = SessionState.WORD_ACTIVE
    return value


def test_curriculum_normalizes_multi_target_activity_metadata_immutably() -> None:
    contract = CourseModeContract.from_mapping(curriculum_contract())
    activity = contract.activities[0]
    assert activity.target_ids == ("w01.hello", "w01.goodbye", "w01.friend")
    assert activity.modalities == ("speech_en", "speech_vi", "choice", "silence", "help")
    assert activity.expected_duration_sec == 60
    assert activity.visual["fallback"] == "robotActing"
    assert contract.targets[2].role == "exposure"
    assert (
        curriculum_outcome_name(
            semantic_class="meaning_vi", speech_class="not_applicable", language="vi", intent="answer"
        )
        == "vietnamese"
    )


def test_curriculum_rejects_invalid_outcome_destination_graph() -> None:
    value = curriculum_contract()
    value["activities"][0]["outcomes"]["help"]["activityId"] = "missing"
    value["contractChecksum"] = _checksum(value)
    with pytest.raises(ValueError, match="INVALID_OUTCOME_GRAPH"):
        CourseModeContract.from_mapping(value)


def test_curriculum_outcomes_cover_advance_near_clue_silence_vi_and_help() -> None:
    runtime = running()
    exact = runtime.observe(observation("a1"))
    assert (exact.action, exact.activity_id) == ("ADVANCE_ACTIVITY", "a2")
    near = runtime.observe(observation("a2", speech="near", now_ms=2_000))
    assert (near.action, near.activity_id) == ("ADVANCE_ACTIVITY", "a3")
    clue = runtime.observe(observation("a3", semantic="other", speech="incorrect", now_ms=3_000))
    assert clue.action == "SUPPORT_WITH_CLUE" and clue.may_model_target is False
    runtime = running()
    runtime.active_activity_id = "a3"
    silence = runtime.observe(observation("a3", semantic="silence", speech="silence", intent="silence", now_ms=4_000))
    assert silence.action == "OFFER_CHOICE_OR_RETRY" and silence.attempt == 1
    runtime = running()
    runtime.active_activity_id = "a3"
    vi = runtime.observe(observation("a3", semantic="meaning_vi", speech="not_applicable", now_ms=5_000))
    assert vi.acknowledgment_intent == "acknowledge_vietnamese_meaning"
    runtime = running()
    runtime.active_activity_id = "a4"
    help_decision = runtime.observe(
        observation("a4", semantic="help", speech="not_applicable", intent="help", now_ms=6_000)
    )
    assert help_decision.action == "MODEL_AND_SUPPORT" and help_decision.may_model_target is True


def test_transition_frame_identity_comes_entirely_from_resulting_activity() -> None:
    value = curriculum_contract()
    value["embodiedIntentNames"].extend(["PRESENT_LEFT", "PRESENT_RIGHT"])
    value["visualFocus"]["regions"].extend(["focus.left.choice", "focus.right.choice"])
    value["activities"][0]["embodiedIntent"] = "PRESENT_LEFT"
    value["activities"][0]["visualFocusRegion"] = "focus.left.choice"
    value["activities"][1]["embodiedIntent"] = "PRESENT_RIGHT"
    value["activities"][1]["visualFocusRegion"] = "focus.right.choice"
    value["contractChecksum"] = _checksum(value)
    runtime = CourseOrchestrator(CourseModeContract.from_mapping(value), started_at_ms=0, soft_deadline_ms=480_000)
    runtime.session_state = SessionState.WORD_ACTIVE
    decision = runtime.observe(observation("a1"))
    assert (decision.activity_id, decision.embodied_intent.value, decision.visual_focus_region) == (
        "a2", "PRESENT_RIGHT", "focus.right.choice",
    )
    runtime = CourseOrchestrator(CourseModeContract.from_mapping(value), started_at_ms=0, soft_deadline_ms=480_000)
    runtime.session_state = SessionState.WORD_ACTIVE
    supported = runtime.observe(observation("a1", semantic="help", speech="not_applicable", intent="help"))
    assert (supported.activity_id, supported.embodied_intent.value, supported.visual_focus_region) == (
        "a2", "PRESENT_RIGHT", "focus.right.choice",
    )


def test_real_compiler_outcome_vocabulary_completes_and_regulates_without_generic_ack() -> None:
    value = curriculum_contract()
    value["activities"][-1]["outcomes"] = {
        "finished": {"action": "complete"},
        "regulationBreak": {"action": "pause"},
    }
    value["contractChecksum"] = _checksum(value)
    contract = CourseModeContract.from_mapping(value)
    runtime = CourseOrchestrator(contract, started_at_ms=0, soft_deadline_ms=480_000)
    runtime.session_state = SessionState.WORD_ACTIVE; runtime.active_activity_id = "a7"
    assert runtime.observe(observation("a7")).action == "COMPLETE_COURSE"
    runtime = CourseOrchestrator(contract, started_at_ms=0, soft_deadline_ms=480_000)
    runtime.session_state = SessionState.WORD_ACTIVE; runtime.active_activity_id = "a7"
    paused = runtime.observe(observation("a7", intent="fatigue"))
    assert paused.action == "RESPOND_WITHOUT_REDIRECT" and paused.next_state is SessionState.REGULATION_BREAK


@pytest.mark.parametrize("semantic,speech,intent,expected", [
    ("target_en", "exact", "answer", "ADVANCE_ACTIVITY"),
    ("target_en", "near", "answer", "ADVANCE_ACTIVITY"),
    ("other", "incorrect", "answer", "SUPPORT_WITH_CLUE"),
    ("silence", "silence", "silence", "OFFER_CHOICE_OR_RETRY"),
    ("help", "not_applicable", "help", "MODEL_AND_SUPPORT"),
    ("other", "not_applicable", "fatigue", "RESPOND_WITHOUT_REDIRECT"),
])
def test_real_compiler_continue_help_regulation_vocabulary(semantic, speech, intent, expected) -> None:
    value = curriculum_contract()
    value["activities"][0]["outcomes"] = {
        "continue": {"action": "advance"},
        "help": {"action": "support", "activityId": "a2"},
        "regulationBreak": {"action": "pause"},
    }
    value["contractChecksum"] = _checksum(value)
    runtime = CourseOrchestrator(CourseModeContract.from_mapping(value), started_at_ms=0, soft_deadline_ms=480_000)
    runtime.session_state = SessionState.WORD_ACTIVE
    result = runtime.observe(observation("a1", semantic=semantic, speech=speech, intent=intent))
    assert result.action == expected


@pytest.mark.parametrize("mutate", [
    lambda value: value["renderer"].update({"rendererId": "teebot-lesson-renderer.v4"}),
    lambda value: value["activities"][0].update({"evidenceName": "NOT_REGISTERED"}),
    lambda value: value["activities"][0].update({"visualFocusRegion": "focus.nowhere"}),
    lambda value: value["activities"][0].update({"stage": "INVENTED"}),
    lambda value: value["activities"][0]["outcomes"]["correct"].update({"extra": True}),
])
def test_curriculum_parser_rejects_noncanonical_registries_and_renderer(mutate) -> None:
    value = curriculum_contract(); mutate(value); value["contractChecksum"] = _checksum(value)
    with pytest.raises(ValueError):
        CourseModeContract.from_mapping(value)


def test_curriculum_uncertainty_regulation_context_deadline_and_completion() -> None:
    runtime = running()
    uncertain = copy.replace(observation("a1"), confidence_band="low") if hasattr(copy, "replace") else None
    if uncertain is None:
        uncertain = ChildObservation(**{**observation("a1").__dict__, "confidence_band": "low"})
    assert runtime.observe(uncertain).action == "OWN_ASR_UNCERTAINTY"
    runtime.session_state = SessionState.WORD_ACTIVE
    assert runtime.observe(observation("a1", intent="fatigue", now_ms=2_000)).action == "RESPOND_WITHOUT_REDIRECT"
    assert runtime.snapshot()["regulationState"] == "fatigue"
    runtime.session_state = SessionState.WORD_ACTIVE
    branch = runtime.observe(observation("a1", semantic="related", intent="story", now_ms=3_000))
    assert branch.action == "OPEN_CONTEXT_BRANCH" and runtime.active_activity_id == "a1"
    runtime.session_state = SessionState.WORD_ACTIVE
    assert runtime.observe(observation("a1", now_ms=480_000)).action == "CLOSE_AT_DEADLINE"

    runtime = running()
    runtime.active_activity_id = "a7"
    completed = runtime.observe(observation("a7", now_ms=7_000))
    assert completed.action == "COMPLETE_COURSE" and completed.next_state is SessionState.COMPLETE


def test_curriculum_snapshot_resume_preserves_activity_attempt_evidence_and_old_defaults() -> None:
    runtime = running()
    runtime.observe(observation("a1", semantic="other", speech="incorrect"))
    snapshot = runtime.snapshot()
    restored = CourseOrchestrator.restore(runtime.contract, json.loads(json.dumps(snapshot)))
    assert restored.active_activity_id == "a2"
    assert restored.snapshot()["activityAttempts"] == {"a1": 1}
    assert restored.snapshot()["evidenceState"] == snapshot["evidenceState"]

    old = {
        key: value
        for key, value in snapshot.items()
        if key not in {"activeActivityId", "activityAttempts", "evidenceState", "regulationState"}
    }
    compatible = CourseOrchestrator.restore(runtime.contract, old)
    assert compatible.active_activity_id == "a1" and compatible.snapshot()["activityAttempts"] == {}
