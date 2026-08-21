"""Google Live semantic tools for the authoritative lesson runtime."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from core.lesson.conversation_contract import ConversationContractError, LessonToolIdentity
from core.lesson.course_orchestrator import (
    CONTEXT_BRANCH_TYPES,
    CONTEXT_BRIDGE_INTENTS,
    CONTEXT_CHILD_DETAIL_CODES,
)
from plugins_func.register import Action, ActionResponse, ToolType, register_function

_GOOGLE_LIVE_LESSON_ADMISSION = ContextVar("google_live_lesson_admission", default=None)


@contextmanager
def _google_live_lesson_tool_admission(
    provider: Any,
    generation: int,
    validation_receipt: dict[str, Any] | None = None,
):
    reset_token = _GOOGLE_LIVE_LESSON_ADMISSION.set(
        (provider, generation, validation_receipt)
    )
    try:
        yield
    finally:
        _GOOGLE_LIVE_LESSON_ADMISSION.reset(reset_token)


def _identity_properties() -> dict[str, Any]:
    return {
        "lessonSessionId": {"type": "string"},
        "turnSequenceId": {"type": "integer", "minimum": 1},
        "attemptId": {"type": "string"},
        "stepKey": {"type": "string"},
    }


def _spec(name: str, description: str, extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
    properties = _identity_properties()
    if extra:
        properties.update(extra)
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": properties,
                "required": list(properties),
            },
        },
    }


LESSON_CONVERSATION_TOOL_SPECS = {
    "lesson_child_response": _spec(
        "lesson_child_response",
        "Classify the child's response without storing or forwarding a transcript.",
        {
            "responseClass": {
                "type": "string",
                "enum": ["target", "meaning_vi", "related", "silence", "uncertain"],
            }
        },
    ),
    "lesson_pronunciation_outcome": _spec(
        "lesson_pronunciation_outcome",
        "Submit only the pronunciation outcome supplied by the lesson speech evaluator.",
        {"outcome": {"type": "string", "enum": ["correct", "retry", "uncertain"]}},
    ),
    "lesson_context_turn": _spec(
        "lesson_context_turn",
        "Request one bounded contextual teaching turn before returning to the target word.",
    ),
    "lesson_visual_reaction": _spec(
        "lesson_visual_reaction",
        "Play exactly the pending server-authorized visual reaction.",
        {
            "cueId": {"type": "string"},
            "cueRole": {
                "type": "string",
                "enum": [
                    "teach",
                    "listen",
                    "thinking",
                    "correct",
                    "retry_level_1",
                    "retry_level_2",
                    "retry_level_3",
                    "celebrate",
                    "word_transition",
                ],
            },
            "effect": {
                "type": "string",
                "enum": [
                    "show_teaching_scene",
                    "show_listening_scene",
                    "show_thinking_scene",
                    "show_correct_reaction",
                    "show_effort_reaction",
                    "show_slow_model",
                    "show_pronunciation_guide",
                    "show_celebration",
                    "show_word_transition",
                ],
            },
        },
    ),
    "lesson_continue": _spec(
        "lesson_continue",
        "Continue only when the authoritative lesson runtime allows progress.",
    ),
}


def _course_spec(name: str, properties: Mapping[str, Any]) -> dict[str, Any]:
    return {"type": "function", "function": {"name": name, "description": "Server-authoritative Course Mode operation.", "parameters": {"type": "object", "additionalProperties": False, "properties": dict(properties), "required": list(properties)}}}


_COURSE_IDENTITY = {"lessonSessionId": {"type": "string"}, "turnSequenceId": {"type": "integer", "minimum": 1}, "observationId": {"type": "string"}}
COURSE_MODE_TOOL_SPECS = {
    "course_observe_child": _course_spec("course_observe_child", {
        **_COURSE_IDENTITY,
        "semanticClass": {"type": "string", "enum": ["target_en", "meaning_vi", "related", "unrelated", "unknown"]},
        "speechClass": {"type": "string", "enum": ["exact", "near", "partial", "silence", "uncertain", "not_applicable"]},
        "language": {"type": "string", "enum": ["vi", "en", "mixed", "unknown"]},
        "intent": {"type": "string"}, "engagement": {"type": "string"},
        "safetyClass": {"type": "string", "enum": ["normal", "safety"]},
        "assessmentEligible": {"type": "boolean"}, "confidenceBand": {"type": "string", "enum": ["low", "medium", "high"]},
        "activityId": {"type": "string"}, "contextId": {"type": "string"},
        "robotAudioContaminated": {"type": "boolean"}, "targetTextVisible": {"type": "boolean"},
    }),
    "course_open_context": _course_spec("course_open_context", {
        **_COURSE_IDENTITY, "branchType": {"type": "string", "enum": list(CONTEXT_BRANCH_TYPES)},
    }),
    "course_close_context": _course_spec("course_close_context", {
        **_COURSE_IDENTITY, "branchId": {"type": "string"},
        "bridgeIntent": {"type": "string", "enum": list(CONTEXT_BRIDGE_INTENTS)},
        "childDetailCode": {"type": "string", "enum": list(CONTEXT_CHILD_DETAIL_CODES)},
    }),
    "course_apply_response_plan": _course_spec("course_apply_response_plan", {
        **_COURSE_IDENTITY, "planId": {"type": "string"}, "decisionId": {"type": "string"},
        "acknowledgment": {"type": "string", "maxLength": 160},
        "relation": {"type": "string", "maxLength": 160},
        "guidance": {"type": "string", "maxLength": 160},
        "invitation": {"type": "string", "maxLength": 160},
        "questionCount": {"type": "integer", "minimum": 0, "maximum": 1},
        "embodiedIntent": {"type": "string"},
        "targetFactsUsed": {"type": "array", "items": {"type": "string"}},
        "praiseLevel": {"type": "string"}, "safetyMode": {"type": "boolean"},
        "normalMiss": {"type": "boolean"},
    }),
    "course_continue": _course_spec("course_continue", _COURSE_IDENTITY),
}


def _course_value_matches_schema(value: Any, schema: Mapping[str, Any]) -> bool:
    value_type = schema.get("type")
    if value_type == "string":
        valid = isinstance(value, str)
    elif value_type == "integer":
        valid = type(value) is int
    elif value_type == "boolean":
        valid = type(value) is bool
    elif value_type == "array":
        valid = isinstance(value, list) and all(
            _course_value_matches_schema(item, schema.get("items", {}))
            for item in value
        )
    else:
        valid = False
    if not valid:
        return False
    if "enum" in schema and value not in schema["enum"]:
        return False
    if isinstance(value, str) and "maxLength" in schema and len(value) > schema["maxLength"]:
        return False
    if type(value) is int:
        if "minimum" in schema and value < schema["minimum"]:
            return False
        if "maximum" in schema and value > schema["maximum"]:
            return False
    return True


def _course_arguments_valid(tool_name: str, arguments: Mapping[str, Any]) -> bool:
    parameters = COURSE_MODE_TOOL_SPECS[tool_name]["function"]["parameters"]
    properties = parameters["properties"]
    return set(arguments) == set(parameters["required"]) and all(
        _course_value_matches_schema(arguments[key], properties[key])
        for key in parameters["required"]
    )


def _course_rejected(runtime: Any, code: str) -> ActionResponse:
    response = dict(_rejected(code).result)
    snapshot = getattr(runtime, "conversation_tool_context", None)
    context = snapshot() if callable(snapshot) else None
    if isinstance(context, Mapping):
        response["context"] = dict(context)
    return ActionResponse(action=Action.REQLLM, result=response)


async def _execute_course(conn: Any, tool_name: str, arguments: Mapping[str, Any]):
    runtime, rejected = _runtime(conn)
    if rejected is not None:
        return rejected
    if getattr(runtime, "course_mode_active", False) is not True:
        return _rejected("COURSE_MODE_NOT_ACTIVE")
    if not _course_arguments_valid(tool_name, arguments):
        return _course_rejected(runtime, "INVALID_TOOL_ARGS")
    operation = getattr(runtime, tool_name, None)
    if not callable(operation):
        return _rejected("COURSE_OPERATION_NOT_AVAILABLE")
    try:
        result = await operation(dict(arguments))
    except (TypeError, ValueError):
        return _course_rejected(runtime, "INVALID_TOOL_ARGS")
    snapshot = getattr(runtime, "conversation_tool_context", None)
    context = snapshot() if callable(snapshot) else None
    if isinstance(result, Mapping) and isinstance(context, Mapping):
        result = {**result, "context": dict(context)}
    admission = _GOOGLE_LIVE_LESSON_ADMISSION.get()
    provider = admission[0] if isinstance(admission, tuple) and len(admission) == 3 else None
    receipt = admission[2] if isinstance(admission, tuple) and len(admission) == 3 else None
    refreshed_identity = context.get("identity") if isinstance(context, Mapping) else None
    if (
        tool_name == "course_apply_response_plan"
        and isinstance(result, Mapping)
        and result.get("accepted") is True
        and isinstance(result.get("responseText"), str)
    ):
        delivery_check = getattr(runtime, "response_plan_requires_delivery", None)
        requires_delivery = not callable(delivery_check) or delivery_check(dict(arguments))
        if requires_delivery:
            deliver = getattr(provider, "deliver_course_response_plan", None)
            delivered = False
            if callable(deliver):
                try:
                    delivered = bool(await deliver(result["responseText"]))
                except Exception:
                    delivered = False
            if not delivered:
                rollback = getattr(runtime, "rollback_course_response_plan", None)
                if callable(rollback):
                    rollback(dict(arguments))
                refreshed = snapshot() if callable(snapshot) else None
                result = {
                    "accepted": False,
                    "code": "RESPONSE_DELIVERY_FAILED",
                    "retryable": True,
                }
                if isinstance(refreshed, Mapping):
                    result["context"] = dict(refreshed)
                return ActionResponse(action=Action.REQLLM, result=result)
            commit = getattr(runtime, "commit_course_response_plan", None)
            if callable(commit):
                commit(dict(arguments))
    if (
        isinstance(result, Mapping)
        and result.get("accepted") is True
        and isinstance(receipt, dict)
        and isinstance(refreshed_identity, Mapping)
    ):
        receipt.update({
            "canonicalToolName": tool_name,
            "refreshedIdentity": dict(refreshed_identity),
        })
    return ActionResponse(action=Action.REQLLM, result=result)


@register_function("course_observe_child", COURSE_MODE_TOOL_SPECS["course_observe_child"], ToolType.SYSTEM_CTL)
async def course_observe_child(conn, **arguments):
    return await _execute_course(conn, "course_observe_child", arguments)


@register_function("course_open_context", COURSE_MODE_TOOL_SPECS["course_open_context"], ToolType.SYSTEM_CTL)
async def course_open_context(conn, **arguments):
    return await _execute_course(conn, "course_open_context", arguments)


@register_function("course_close_context", COURSE_MODE_TOOL_SPECS["course_close_context"], ToolType.SYSTEM_CTL)
async def course_close_context(conn, **arguments):
    return await _execute_course(conn, "course_close_context", arguments)


@register_function("course_apply_response_plan", COURSE_MODE_TOOL_SPECS["course_apply_response_plan"], ToolType.SYSTEM_CTL)
async def course_apply_response_plan(conn, **arguments):
    return await _execute_course(conn, "course_apply_response_plan", arguments)


@register_function("course_continue", COURSE_MODE_TOOL_SPECS["course_continue"], ToolType.SYSTEM_CTL)
async def course_continue(conn, **arguments):
    return await _execute_course(conn, "course_continue", arguments)


def _validate_arguments(tool_name: str, arguments: Mapping[str, Any]) -> bool:
    parameters = LESSON_CONVERSATION_TOOL_SPECS[tool_name]["function"]["parameters"]
    return set(arguments) == set(parameters["required"])


def _decision_payload(decision: Any, context: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = decision.to_mapping()
    return {
        "accepted": raw["accepted"],
        "code": raw["code"],
        "state": raw["state"],
        "nextIntent": raw["next_intent"],
        "cueId": raw["cue_id"],
        "effect": raw["effect"],
        "coachingLevel": raw["coaching_level"],
        "outcome": raw["outcome"],
        "reviewNeeded": raw["review_needed"],
        "guidance": raw["guidance"],
        "context": dict(context) if isinstance(context, Mapping) else None,
    }


def _rejected(code: str) -> ActionResponse:
    return ActionResponse(
        action=Action.REQLLM,
        result={
            "accepted": False,
            "code": code,
            "nextIntent": "retain_authoritative_state",
            "cueId": None,
            "effect": None,
        },
    )


def _runtime(conn: Any):
    provider = getattr(conn, "voice_provider", None)
    admission = _GOOGLE_LIVE_LESSON_ADMISSION.get()
    if (
        not isinstance(admission, tuple)
        or len(admission) not in (2, 3)
        or admission[0] is not provider
    ):
        return None, _rejected("MODEL_RESPONSE_NOT_ADMITTED")
    admission_generation = admission[1]
    current = getattr(provider, "current_response_id", None)
    if not isinstance(admission_generation, int) or not callable(current):
        return None, _rejected("MODEL_RESPONSE_NOT_ADMITTED")
    if current() != admission_generation:
        return None, _rejected("STALE_MODEL_RESPONSE")
    runtime = getattr(conn, "lesson_runtime", None)
    if runtime is None:
        return None, _rejected("CONVERSATION_NOT_ACTIVE")
    return runtime, None


def _identity(arguments: Mapping[str, Any], *, cue_id: str | None = None) -> LessonToolIdentity:
    return LessonToolIdentity.from_mapping(
        {
            "lesson_session_id": arguments.get("lessonSessionId"),
            "turn_sequence_id": arguments.get("turnSequenceId"),
            "attempt_id": arguments.get("attemptId"),
            "step_key": arguments.get("stepKey"),
            "cue_id": cue_id,
        }
    )


async def _execute(
    conn: Any,
    operation: str,
    arguments: Mapping[str, Any],
):
    tool_name = {
        "child_response": "lesson_child_response",
        "pronunciation_outcome": "lesson_pronunciation_outcome",
        "context_turn": "lesson_context_turn",
        "visual_reaction": "lesson_visual_reaction",
        "continue": "lesson_continue",
    }[operation]
    runtime, rejected = _runtime(conn)
    if rejected is not None:
        return rejected
    if not _validate_arguments(tool_name, arguments):
        return _rejected("INVALID_TOOL_ARGS")
    try:
        if operation == "child_response":
            decision = await runtime.conversation_child_response(_identity(arguments), arguments.get("responseClass"))
        elif operation == "pronunciation_outcome":
            decision = await runtime.conversation_pronunciation_outcome(_identity(arguments), arguments.get("outcome"))
        elif operation == "context_turn":
            decision = await runtime.conversation_context_turn(_identity(arguments))
        elif operation == "visual_reaction":
            decision = await runtime.conversation_visual_reaction(
                _identity(arguments, cue_id=arguments.get("cueId")),
                arguments.get("cueRole"),
                effect=arguments.get("effect"),
            )
        else:
            decision = await runtime.conversation_continue_from_tool(_identity(arguments))
    except ConversationContractError as exc:
        return _rejected(exc.code)
    except (TypeError, ValueError):
        return _rejected("INVALID_TOOL_ARGS")
    snapshot = getattr(runtime, "conversation_tool_context", None)
    context = snapshot() if callable(snapshot) else None
    decision_payload = _decision_payload(decision, context)
    admission = _GOOGLE_LIVE_LESSON_ADMISSION.get()
    receipt = admission[2] if isinstance(admission, tuple) and len(admission) == 3 else None
    refreshed_identity = context.get("identity") if isinstance(context, Mapping) else None
    if (
        decision_payload.get("accepted") is True
        and isinstance(receipt, dict)
        and isinstance(refreshed_identity, Mapping)
    ):
        receipt.update(
            {
                "canonicalToolName": tool_name,
                "refreshedIdentity": dict(refreshed_identity),
            }
        )
    return ActionResponse(
        action=Action.REQLLM,
        result=decision_payload,
    )


@register_function(
    "lesson_child_response", LESSON_CONVERSATION_TOOL_SPECS["lesson_child_response"], ToolType.SYSTEM_CTL
)
async def lesson_child_response(
    conn,
    **arguments,
):
    return await _execute(conn, "child_response", arguments)


@register_function(
    "lesson_pronunciation_outcome", LESSON_CONVERSATION_TOOL_SPECS["lesson_pronunciation_outcome"], ToolType.SYSTEM_CTL
)
async def lesson_pronunciation_outcome(
    conn,
    **arguments,
):
    return await _execute(conn, "pronunciation_outcome", arguments)


@register_function("lesson_context_turn", LESSON_CONVERSATION_TOOL_SPECS["lesson_context_turn"], ToolType.SYSTEM_CTL)
async def lesson_context_turn(
    conn,
    **arguments,
):
    return await _execute(conn, "context_turn", arguments)


@register_function(
    "lesson_visual_reaction", LESSON_CONVERSATION_TOOL_SPECS["lesson_visual_reaction"], ToolType.SYSTEM_CTL
)
async def lesson_visual_reaction(
    conn,
    **arguments,
):
    return await _execute(conn, "visual_reaction", arguments)


@register_function("lesson_continue", LESSON_CONVERSATION_TOOL_SPECS["lesson_continue"], ToolType.SYSTEM_CTL)
async def lesson_continue(
    conn,
    **arguments,
):
    return await _execute(conn, "continue", arguments)
