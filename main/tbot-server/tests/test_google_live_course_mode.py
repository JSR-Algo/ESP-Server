from __future__ import annotations

import copy

import pytest

from plugins_func.functions.lesson_conversation import (
    COURSE_MODE_TOOL_SPECS,
    LESSON_CONVERSATION_TOOL_SPECS,
    _google_live_lesson_tool_admission,
    course_observe_child,
)


class Provider:
    def __init__(self, generation: int = 4) -> None:
        self.generation = generation

    def current_response_id(self) -> int:
        return self.generation


class Runtime:
    course_mode_active = True

    async def course_observe_child(self, arguments):
        return {"accepted": True, "decisionId": "d1", "nextState": "WORD_ACTIVE", "arguments": arguments}


class Conn:
    def __init__(self) -> None:
        self.voice_provider = Provider()
        self.lesson_runtime = Runtime()


def test_v2_tool_schemas_are_separate_closed_and_cannot_submit_mastery_or_transcript() -> None:
    assert set(COURSE_MODE_TOOL_SPECS) == {
        "course_observe_child", "course_open_context", "course_close_context",
        "course_apply_response_plan", "course_continue",
    }
    forbidden = {"transcript", "utterance", "mastery", "evidenceLevel", "pronunciationScore", "servo"}
    for spec in COURSE_MODE_TOOL_SPECS.values():
        parameters = spec["function"]["parameters"]
        assert parameters["additionalProperties"] is False
        assert not forbidden.intersection(parameters["properties"])


def test_v1_specs_remain_byte_equal_when_v2_specs_are_used() -> None:
    before = copy.deepcopy(LESSON_CONVERSATION_TOOL_SPECS)
    _ = COURSE_MODE_TOOL_SPECS
    assert LESSON_CONVERSATION_TOOL_SPECS == before


@pytest.mark.asyncio
async def test_admitted_current_generation_routes_exact_observation_without_raw_content() -> None:
    conn = Conn()
    arguments = {
        "lessonSessionId": "s1", "turnSequenceId": 3, "observationId": "o1",
        "semanticClass": "target_en", "speechClass": "exact", "language": "en",
        "intent": "answer", "engagement": "engaged", "safetyClass": "normal",
        "assessmentEligible": True, "confidenceBand": "high",
        "activityId": "cat-recall-visual-02", "contextId": "cat_primary_visual_recall",
        "robotAudioContaminated": False, "targetTextVisible": False,
    }
    with _google_live_lesson_tool_admission(conn.voice_provider, 4):
        response = await course_observe_child(conn, **arguments)
    assert response.result["accepted"] is True
    assert response.result["arguments"] == arguments


@pytest.mark.asyncio
async def test_stale_generation_extra_args_and_v1_runtime_fail_closed() -> None:
    conn = Conn()
    required = COURSE_MODE_TOOL_SPECS["course_observe_child"]["function"]["parameters"]["required"]
    args = {key: ({"turnSequenceId": 1, "assessmentEligible": True, "robotAudioContaminated": False, "targetTextVisible": False}.get(key, "x")) for key in required}
    with _google_live_lesson_tool_admission(conn.voice_provider, 3):
        stale = await course_observe_child(conn, **args)
    assert stale.result["code"] == "STALE_MODEL_RESPONSE"
    args["transcript"] = "private"
    with _google_live_lesson_tool_admission(conn.voice_provider, 4):
        invalid = await course_observe_child(conn, **args)
    assert invalid.result["code"] == "INVALID_TOOL_ARGS"
    conn.lesson_runtime.course_mode_active = False
    args.pop("transcript")
    with _google_live_lesson_tool_admission(conn.voice_provider, 4):
        inactive = await course_observe_child(conn, **args)
    assert inactive.result["code"] == "COURSE_MODE_NOT_ACTIVE"

