"""Google Live semantic tools for the authoritative lesson runtime."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from core.lesson.conversation_contract import ConversationContractError, LessonToolIdentity
from plugins_func.register import Action, ActionResponse, ToolType, register_function


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


def _runtime(conn: Any, admission_generation: int | None):
    provider = getattr(conn, "voice_provider", None)
    if admission_generation is not None:
        current = getattr(provider, "current_response_id", None)
        if not callable(current) or current() != admission_generation:
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


async def _execute(conn: Any, operation: str, arguments: Mapping[str, Any], admission_generation: int | None):
    tool_name = {
        "child_response": "lesson_child_response",
        "pronunciation_outcome": "lesson_pronunciation_outcome",
        "context_turn": "lesson_context_turn",
        "visual_reaction": "lesson_visual_reaction",
        "continue": "lesson_continue",
    }[operation]
    if not _validate_arguments(tool_name, arguments):
        return _rejected("INVALID_TOOL_ARGS")
    runtime, rejected = _runtime(conn, admission_generation)
    if rejected is not None:
        return rejected
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
    return ActionResponse(
        action=Action.REQLLM,
        result=_decision_payload(decision, context),
    )


@register_function(
    "lesson_child_response", LESSON_CONVERSATION_TOOL_SPECS["lesson_child_response"], ToolType.SYSTEM_CTL
)
async def lesson_child_response(conn, _provider_admission_generation=None, **arguments):
    return await _execute(conn, "child_response", arguments, _provider_admission_generation)


@register_function(
    "lesson_pronunciation_outcome", LESSON_CONVERSATION_TOOL_SPECS["lesson_pronunciation_outcome"], ToolType.SYSTEM_CTL
)
async def lesson_pronunciation_outcome(conn, _provider_admission_generation=None, **arguments):
    return await _execute(conn, "pronunciation_outcome", arguments, _provider_admission_generation)


@register_function("lesson_context_turn", LESSON_CONVERSATION_TOOL_SPECS["lesson_context_turn"], ToolType.SYSTEM_CTL)
async def lesson_context_turn(conn, _provider_admission_generation=None, **arguments):
    return await _execute(conn, "context_turn", arguments, _provider_admission_generation)


@register_function(
    "lesson_visual_reaction", LESSON_CONVERSATION_TOOL_SPECS["lesson_visual_reaction"], ToolType.SYSTEM_CTL
)
async def lesson_visual_reaction(conn, _provider_admission_generation=None, **arguments):
    return await _execute(conn, "visual_reaction", arguments, _provider_admission_generation)


@register_function("lesson_continue", LESSON_CONVERSATION_TOOL_SPECS["lesson_continue"], ToolType.SYSTEM_CTL)
async def lesson_continue(conn, _provider_admission_generation=None, **arguments):
    return await _execute(conn, "continue", arguments, _provider_admission_generation)
