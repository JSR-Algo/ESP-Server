from __future__ import annotations

import copy

import pytest

from plugins_func.functions.lesson_conversation import (
    COURSE_MODE_TOOL_SPECS,
    LESSON_CONVERSATION_TOOL_SPECS,
    _google_live_lesson_tool_admission,
    course_apply_response_plan,
    course_close_context,
    course_continue,
    course_observe_child,
    course_open_context,
)
from core.providers.tools.product_toolset import LESSON_SEMANTIC_TOOLS
from core.voice.google_live.audio_bridge import GoogleLiveAudioBridge
from core.voice.session_orchestrator import SessionMode


class Provider:
    def __init__(self, generation: int = 4) -> None:
        self.generation = generation
        self.lesson_text = []
        self.send_result = True

    def current_response_id(self) -> int:
        return self.generation

    async def _send_live_text_ack(self, text, *, log_label, allow_lesson_output):
        self.lesson_text.append((text, log_label, allow_lesson_output))
        return self.send_result


class Runtime:
    course_mode_active = True

    def conversation_tool_context(self):
        return {
            "identity": {"lessonSessionId": "s1", "turnSequenceId": 2},
            "activeTargetId": "toys.ball",
            "activities": [{"activityId": "ball-discover-center-01"}],
        }

    async def course_observe_child(self, arguments):
        return {"accepted": True, "decisionId": "d1", "nextState": "WORD_ACTIVE", "arguments": arguments}

    async def course_open_context(self, arguments):
        return {"accepted": True, "operation": "open", "arguments": arguments}

    async def course_close_context(self, arguments):
        return {"accepted": True, "operation": "close", "arguments": arguments}

    async def course_apply_response_plan(self, arguments):
        return {"accepted": True, "operation": "plan", "arguments": arguments}

    def rollback_course_response_plan(self, arguments):
        self.rolled_back_plan = dict(arguments)

    def commit_course_response_plan(self, arguments):
        self.committed_plan = dict(arguments)

    async def course_continue(self, arguments):
        return {"accepted": True, "operation": "continue", "arguments": arguments}


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
    assert set(COURSE_MODE_TOOL_SPECS) <= set(LESSON_SEMANTIC_TOOLS)
    plan_properties = COURSE_MODE_TOOL_SPECS["course_apply_response_plan"]["function"]["parameters"]["properties"]
    for field in ("acknowledgment", "relation", "guidance", "invitation"):
        assert plan_properties[field]["maxLength"] == 160


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
async def test_accepted_course_tool_populates_validation_audit_receipt() -> None:
    conn = Conn()
    receipt = {}
    arguments = {"lessonSessionId": "s1", "turnSequenceId": 1, "observationId": "o1"}

    with _google_live_lesson_tool_admission(conn.voice_provider, 4, receipt):
        response = await course_continue(conn, **arguments)

    assert response.result["accepted"] is True
    assert receipt == {
        "canonicalToolName": "course_continue",
        "refreshedIdentity": {"lessonSessionId": "s1", "turnSequenceId": 2},
    }


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


@pytest.mark.asyncio
async def test_malformed_course_argument_values_fail_closed_with_refreshed_context() -> None:
    conn = Conn()
    required = COURSE_MODE_TOOL_SPECS["course_observe_child"]["function"]["parameters"]["required"]
    args = {
        key: ({
            "turnSequenceId": 1,
            "assessmentEligible": True,
            "robotAudioContaminated": False,
            "targetTextVisible": False,
        }.get(key, "x"))
        for key in required
    }
    args["observationId"] = []

    with _google_live_lesson_tool_admission(conn.voice_provider, 4):
        response = await course_observe_child(conn, **args)

    assert response.result["code"] == "INVALID_TOOL_ARGS"
    assert response.result["context"] == conn.lesson_runtime.conversation_tool_context()


@pytest.mark.asyncio
async def test_all_advertised_operations_route_to_active_runtime() -> None:
    conn = Conn()
    identity = {"lessonSessionId": "s1", "turnSequenceId": 1, "observationId": "o1"}
    calls = (
        (course_open_context, {**identity, "branchType": "RELATED_STORY"}, "open"),
        (course_close_context, {
            **identity, "branchId": "b1", "bridgeIntent": "white_cat_visual",
            "childDetailCode": "grandmother_pet",
        }, "close"),
        (course_apply_response_plan, {
            **identity, "planId": "p1", "decisionId": "d1",
            "acknowledgment": "Heard.", "relation": "Okay.",
            "guidance": "Look.", "invitation": "Ready?", "questionCount": 1,
            "embodiedIntent": "INVITE_CHILD", "targetFactsUsed": [],
            "praiseLevel": "engagement", "safetyMode": False, "normalMiss": False,
        }, "plan"),
        (course_continue, identity, "continue"),
    )
    with _google_live_lesson_tool_admission(conn.voice_provider, 4):
        for operation, arguments, expected in calls:
            response = await operation(conn, **arguments)
            assert response.result["operation"] == expected
            assert response.result["context"]["activeTargetId"] == "toys.ball"


@pytest.mark.asyncio
async def test_approved_course_plan_uses_bounded_lesson_output_path() -> None:
    conn = Conn()
    arguments = {
        "lessonSessionId": "s1", "turnSequenceId": 1, "observationId": "o1",
        "planId": "p1", "decisionId": "d1", "acknowledgment": "Heard.",
        "relation": "Okay.", "guidance": "Look.", "invitation": "Ready?",
        "questionCount": 1, "embodiedIntent": "INVITE_CHILD", "targetFactsUsed": [],
        "praiseLevel": "engagement", "safetyMode": False, "normalMiss": False,
    }
    conn.lesson_runtime.course_apply_response_plan = lambda _arguments: None

    async def apply(_arguments):
        return {
            "accepted": True, "code": "RESPONSE_PLAN_APPLIED",
            "responseText": "Heard. Okay. Look. Ready?",
        }

    conn.lesson_runtime.course_apply_response_plan = apply
    with _google_live_lesson_tool_admission(conn.voice_provider, 4):
        response = await course_apply_response_plan(conn, **arguments)

    assert response.result["accepted"] is True
    assert conn.voice_provider.lesson_text == [
        ("Heard. Okay. Look. Ready?", "course_response_plan", True),
    ]
    assert conn.lesson_runtime.committed_plan == arguments


@pytest.mark.asyncio
async def test_failed_course_plan_delivery_rolls_back_and_reports_retryable_failure() -> None:
    conn = Conn()
    conn.voice_provider.send_result = False
    arguments = {
        "lessonSessionId": "s1", "turnSequenceId": 1, "observationId": "o1",
        "planId": "p1", "decisionId": "d1", "acknowledgment": "Heard.",
        "relation": "Okay.", "guidance": "Look.", "invitation": "Ready?",
        "questionCount": 1, "embodiedIntent": "INVITE_CHILD", "targetFactsUsed": [],
        "praiseLevel": "engagement", "safetyMode": False, "normalMiss": False,
    }

    async def apply(_arguments):
        return {
            "accepted": True, "code": "RESPONSE_PLAN_APPLIED",
            "responseText": "Heard. Okay. Look. Ready?",
        }

    conn.lesson_runtime.course_apply_response_plan = apply
    with _google_live_lesson_tool_admission(conn.voice_provider, 4):
        response = await course_apply_response_plan(conn, **arguments)

    assert response.result == {
        "accepted": False,
        "code": "RESPONSE_DELIVERY_FAILED",
        "retryable": True,
        "context": conn.lesson_runtime.conversation_tool_context(),
    }
    assert conn.lesson_runtime.rolled_back_plan == arguments


def test_audio_bridge_admits_course_tool_calls_during_lesson_mode() -> None:
    conn = Conn()
    conn.session_mode = SessionMode.LESSON
    conn.lesson_runtime.state = "RUNNING"
    conn.google_live_lesson_prompt_output_allowed = False
    bridge = GoogleLiveAudioBridge.__new__(GoogleLiveAudioBridge)
    bridge.conn = conn
    bridge._active_response_id = 1
    bridge._response_id_getter = lambda: 1

    assert bridge._should_drop_lesson_model_output(
        "tool_call", {"calls": [{"name": "course_continue"}]},
    ) is False
