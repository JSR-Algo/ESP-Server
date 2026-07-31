from __future__ import annotations

from copy import deepcopy
from dataclasses import FrozenInstanceError

import pytest

from core.lesson.conversation_contract import (
    ConversationContractError,
    LessonConversationContract,
    LessonToolIdentity,
)
from core.lesson.conversation_runtime import (
    ConversationState,
    LessonConversationRuntime,
)


def _payload(*, phonemes: bool = False) -> dict:
    pronunciation = {
        "slow_model": "ap-ple",
        "l1_guidance_vi": "Mở miệng nhẹ ở âm đầu.",
    }
    pronunciation["phonemes" if phonemes else "segments"] = ["æ", "p", "əl"] if phonemes else ["ap", "ple"]
    effects = {
        "teach": "show_teaching_scene",
        "listen": "show_listening_scene",
        "thinking": "show_thinking_scene",
        "correct": "show_correct_reaction",
        "retry_level_1": "show_effort_reaction",
        "retry_level_2": "show_slow_model",
        "retry_level_3": "show_pronunciation_guide",
        "celebrate": "show_celebration",
        "word_transition": "show_word_transition",
    }
    return {
        "lesson_session_id": "session-7a",
        "lesson_id": "farm-english",
        "lesson_version": 3,
        "step_key": "farm.apple",
        "target_word": "apple",
        "meanings_vi": ["quả táo", "trái táo"],
        "related_concepts": ["fruit", "red fruit"],
        "question_seeds": ["What is this?", "Can you say apple?"],
        "teaching_copy": "This is an apple.",
        "expected_answer": "Apple",
        "progress_index": 1,
        "progress_count": 4,
        "pronunciation": pronunciation,
        "cues": {role: {"cue_id": f"farm.apple.{role}", "effect": effect} for role, effect in effects.items()},
        "max_contextual_turns": 2,
    }


def _contract(**kwargs) -> LessonConversationContract:
    payload = _payload(**kwargs)
    return LessonConversationContract.from_mapping(payload)


def _runtime() -> LessonConversationRuntime:
    ids = iter(("attempt-a", "attempt-b", "attempt-c"))
    return LessonConversationRuntime(_contract(), attempt_id_factory=lambda: next(ids))


def _identity(runtime: LessonConversationRuntime, *, cue_id: str | None = None) -> LessonToolIdentity:
    return runtime.identity(cue_id=cue_id)


def test_contract_is_exact_deeply_immutable_and_supports_either_pronunciation_union() -> None:
    payload = _payload()
    contract = LessonConversationContract.from_mapping(payload)
    payload["meanings_vi"].append("changed")
    payload["cues"]["listen"]["cue_id"] = "changed"

    assert contract.meanings_vi == ("quả táo", "trái táo")
    assert contract.cues[1].cue_id == "farm.apple.listen"
    assert LessonConversationContract.from_mapping(_payload(phonemes=True)).pronunciation.phonemes
    with pytest.raises(FrozenInstanceError):
        contract.target_word = "pear"  # type: ignore[misc]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.update(extra=True),
        lambda p: p.pop("lesson_id"),
        lambda p: p.update(lesson_session_id="../unsafe"),
        lambda p: p.update(step_key="farm apple"),
        lambda p: p.update(meanings_vi=["Quả táo", "  quả   táo  "]),
        lambda p: p.update(question_seeds=[]),
        lambda p: p.update(expected_answer="pear"),
        lambda p: p["pronunciation"].update(phonemes=["x"]),
        lambda p: p["pronunciation"].pop("segments"),
        lambda p: p.update(max_contextual_turns=3),
        lambda p: p["cues"]["listen"].update(effect="show_celebration"),
        lambda p: p["cues"].update(arbitrary={"cue_id": "x.y", "effect": "show_teaching_scene"}),
    ],
)
def test_contract_rejects_missing_extra_unsafe_duplicate_or_mismatched_content(mutate) -> None:
    payload = _payload()
    mutate(payload)
    with pytest.raises(ConversationContractError):
        LessonConversationContract.from_mapping(payload)


def test_identity_is_exact_and_immutable() -> None:
    identity = LessonToolIdentity.from_mapping(
        {
            "lesson_session_id": "session-7a",
            "turn_sequence_id": 1,
            "attempt_id": "attempt-a",
            "step_key": "farm.apple",
            "cue_id": None,
        }
    )
    with pytest.raises(FrozenInstanceError):
        identity.attempt_id = "attempt-b"  # type: ignore[misc]
    with pytest.raises(ConversationContractError):
        LessonToolIdentity.from_mapping({**identity.to_mapping(), "extra": True})


def test_english_target_first_attempt_is_speaking_evidence_and_mastery() -> None:
    runtime = _runtime()
    opened = runtime.open_attempt()
    assert (opened.state, opened.next_intent, opened.cue_id) == (
        ConversationState.ASKING,
        "scene_question",
        "farm.apple.listen",
    )

    heard = runtime.child_response(_identity(runtime), "target")
    assert heard.outcome == "speaking_evidence"
    assert heard.state is ConversationState.REACTING
    correct = runtime.pronunciation_outcome(_identity(runtime), "correct")
    assert (correct.outcome, correct.next_intent, correct.cue_id) == (
        "mastered",
        "celebrate_mastery",
        "farm.apple.celebrate",
    )


def test_vietnamese_meaning_bridges_comprehension_then_invites_english_without_mastery() -> None:
    runtime = _runtime()
    runtime.open_attempt()
    bridge = runtime.child_response(_identity(runtime), "meaning_vi")
    assert (bridge.state, bridge.next_intent, bridge.outcome) == (
        ConversationState.BRIDGING,
        "bridge_vietnamese",
        "comprehension_only",
    )
    assert bridge.guidance.target_word == "apple"
    assert runtime.mastered is False
    invited = runtime.child_response(_identity(runtime), "target")
    assert invited.outcome == "speaking_evidence"


def test_related_response_bridges_concisely_and_retains_target() -> None:
    runtime = _runtime()
    runtime.open_attempt()
    decision = runtime.child_response(_identity(runtime), "related")
    assert decision.next_intent == "bridge_related"
    assert decision.state is ConversationState.BRIDGING
    assert decision.guidance.target_word == "apple"
    assert runtime.current_target == "apple"


@pytest.mark.parametrize(
    ("response_class", "intent"),
    [("silence", "narrow_question"), ("uncertain", "contrast_then_model")],
)
def test_silence_and_uncertain_narrow_support_without_negative_state(response_class, intent) -> None:
    runtime = _runtime()
    runtime.open_attempt()
    decision = runtime.child_response(_identity(runtime), response_class)
    assert decision.next_intent == intent
    assert decision.state is ConversationState.COACHING
    assert decision.outcome == "supporting"


@pytest.mark.parametrize("starting_level", [0, 1, 2])
def test_correct_pronunciation_at_each_support_level_mastered(starting_level: int) -> None:
    runtime = _runtime()
    runtime.open_attempt()
    runtime.child_response(_identity(runtime), "target")
    for _ in range(starting_level):
        retry = runtime.pronunciation_outcome(_identity(runtime), "retry")
        assert retry.state is ConversationState.LISTENING
    decision = runtime.pronunciation_outcome(_identity(runtime), "correct")
    assert decision.outcome == "mastered"
    assert decision.coaching_level == starting_level


def test_three_unsuccessful_support_levels_end_attempted_review_needed_without_negativity() -> None:
    runtime = _runtime()
    runtime.open_attempt()
    runtime.child_response(_identity(runtime), "target")
    intents = []
    for _ in range(3):
        decision = runtime.pronunciation_outcome(_identity(runtime), "retry")
        intents.append(decision.next_intent)
    assert intents == ["praise_effort", "slow_whole_word", "approved_pronunciation_guidance"]
    final = runtime.pronunciation_outcome(_identity(runtime), "retry")
    assert (final.state, final.next_intent, final.outcome, final.review_needed) == (
        ConversationState.COMPLETE,
        "praise_effort_continue",
        "attempted",
        True,
    )


def test_uncertain_pronunciation_uses_same_gentle_support_path() -> None:
    runtime = _runtime()
    runtime.open_attempt()
    runtime.child_response(_identity(runtime), "target")
    decision = runtime.pronunciation_outcome(_identity(runtime), "uncertain")
    assert decision.next_intent == "praise_effort"
    assert decision.coaching_level == 1


def test_context_is_bounded_to_two_turns_and_never_changes_objective() -> None:
    runtime = _runtime()
    runtime.open_attempt()
    first = runtime.context_turn(_identity(runtime))
    second = runtime.context_turn(_identity(runtime))
    third = runtime.context_turn(_identity(runtime))
    assert [first.next_intent, second.next_intent, third.next_intent] == [
        "bounded_context",
        "bounded_context",
        "forced_back_to_target",
    ]
    assert runtime.contextual_turn_count == 2
    assert runtime.current_target == "apple"
    assert runtime.mastered is False


def test_duplicate_stale_reordered_and_cross_identity_reject_without_mutation() -> None:
    runtime = _runtime()
    runtime.open_attempt()
    original = _identity(runtime)
    accepted = runtime.child_response(original, "meaning_vi")
    snapshot = runtime.snapshot()

    duplicate = runtime.child_response(original, "meaning_vi")
    stale = runtime.child_response(
        LessonToolIdentity(
            original.lesson_session_id,
            original.turn_sequence_id,
            original.attempt_id,
            original.step_key,
            "farm.apple.listen",
        ),
        "target",
    )
    reordered = runtime.child_response(
        LessonToolIdentity(
            original.lesson_session_id,
            runtime.turn_sequence_id + 1,
            original.attempt_id,
            original.step_key,
        ),
        "target",
    )
    cross_session = runtime.child_response(
        LessonToolIdentity(
            "session-other",
            runtime.turn_sequence_id,
            original.attempt_id,
            original.step_key,
        ),
        "target",
    )
    cross_attempt = runtime.child_response(
        LessonToolIdentity(
            original.lesson_session_id,
            runtime.turn_sequence_id,
            "attempt-other",
            original.step_key,
        ),
        "target",
    )
    cross_step = runtime.child_response(
        LessonToolIdentity(
            original.lesson_session_id,
            runtime.turn_sequence_id,
            original.attempt_id,
            "farm.pear",
        ),
        "target",
    )

    assert accepted.accepted is True
    assert [d.code for d in (duplicate, stale, reordered, cross_session, cross_attempt, cross_step)] == [
        "DUPLICATE_IDENTITY",
        "STALE_IDENTITY",
        "REORDERED_IDENTITY",
        "CROSS_SESSION",
        "CROSS_ATTEMPT",
        "CROSS_STEP",
    ]
    assert runtime.snapshot() == snapshot


@pytest.mark.parametrize(
    ("response_class", "expected_state"),
    [
        (None, ConversationState.ASKING),
        ("meaning_vi", ConversationState.BRIDGING),
        ("silence", ConversationState.COACHING),
        ("target", ConversationState.REACTING),
    ],
)
def test_interrupt_during_model_speech_or_reaction_retires_identity_and_listens(response_class, expected_state) -> None:
    runtime = _runtime()
    runtime.open_attempt()
    if response_class is not None:
        runtime.child_response(_identity(runtime), response_class)
    assert runtime.state is expected_state
    old = _identity(runtime)
    decision = runtime.interrupt(old)
    assert decision.state is ConversationState.LISTENING
    assert decision.next_intent == "listen_to_child"
    assert runtime.turn_sequence_id == old.turn_sequence_id + 1
    assert runtime.child_response(old, "target").code == "DUPLICATE_IDENTITY"


def test_begin_turn_increments_sequence_and_child_can_barge_into_listening() -> None:
    runtime = _runtime()
    runtime.open_attempt()
    identity = _identity(runtime, cue_id="farm.apple.teach")
    begun = runtime.begin_turn(identity, "teach", ConversationState.ASKING)
    assert begun.accepted
    assert runtime.turn_sequence_id == identity.turn_sequence_id + 1
    barged = runtime.child_response(_identity(runtime), "meaning_vi")
    assert barged.state is ConversationState.BRIDGING


def test_visual_reaction_continue_and_skip_are_server_authoritative() -> None:
    runtime = _runtime()
    runtime.open_attempt()
    bad_cue = runtime.visual_reaction(_identity(runtime, cue_id="farm.apple.celebrate"), "correct")
    bad_effect = runtime.visual_reaction(
        _identity(runtime, cue_id="farm.apple.listen"), "listen", effect="show_celebration"
    )
    premature = runtime.continue_lesson(
        _identity(runtime, cue_id="farm.apple.word_transition"),
        effect="show_word_transition",
    )
    direct_mastery = runtime.mark_mastered(_identity(runtime))
    assert [bad_cue.code, bad_effect.code, premature.code, direct_mastery.code] == [
        "ILLEGAL_CUE",
        "ILLEGAL_EFFECT",
        "CONTINUE_NOT_ALLOWED",
        "DIRECT_MASTERY_FORBIDDEN",
    ]
    assert runtime.mastered is False

    runtime.child_response(_identity(runtime), "target")
    runtime.pronunciation_outcome(_identity(runtime), "correct")
    continued = runtime.continue_lesson(
        _identity(runtime, cue_id="farm.apple.word_transition"),
        effect="show_word_transition",
    )
    assert continued.next_intent == "continue_lesson"
    assert continued.cue_id == "farm.apple.word_transition"


def test_celebration_is_unavailable_before_authoritative_pronunciation_mastery() -> None:
    runtime = _runtime()
    runtime.open_attempt()
    runtime.child_response(_identity(runtime), "target")
    decision = runtime.visual_reaction(
        _identity(runtime, cue_id="farm.apple.celebrate"),
        "celebrate",
        effect="show_celebration",
    )
    assert decision.code == "ILLEGAL_CUE"
    assert runtime.mastered is False


def test_visual_reaction_requires_the_exact_approved_effect() -> None:
    runtime = _runtime()
    runtime.open_attempt()
    decision = runtime.visual_reaction(_identity(runtime, cue_id="farm.apple.listen"), "listen")
    assert decision.code == "ILLEGAL_EFFECT"


def test_continue_rejects_model_selected_cue_effect_or_step() -> None:
    runtime = _runtime()
    runtime.open_attempt()
    runtime.child_response(_identity(runtime), "target")
    runtime.pronunciation_outcome(_identity(runtime), "correct")
    wrong_cue = runtime.continue_lesson(
        _identity(runtime, cue_id="farm.apple.celebrate"), effect="show_word_transition"
    )
    wrong_effect = runtime.continue_lesson(
        _identity(runtime, cue_id="farm.apple.word_transition"), effect="show_celebration"
    )
    selected_step = runtime.continue_lesson(
        _identity(runtime, cue_id="farm.apple.word_transition"),
        effect="show_word_transition",
        next_step_key="farm.pear",
    )
    assert [wrong_cue.code, wrong_effect.code, selected_step.code] == [
        "ILLEGAL_CUE",
        "ILLEGAL_EFFECT",
        "MODEL_STEP_SELECTION_FORBIDDEN",
    ]


def test_decisions_are_immutable_safe_and_never_contain_negative_wording_or_transcript() -> None:
    runtime = _runtime()
    decisions = [runtime.open_attempt()]
    decisions.append(runtime.child_response(_identity(runtime), "target"))
    for _ in range(4):
        decisions.append(runtime.pronunciation_outcome(_identity(runtime), "retry"))
    rendered = " ".join(str(decision).lower() for decision in decisions)
    assert all(token not in rendered for token in ("wrong", "incorrect", "sai", "red_error"))
    assert "transcript" not in decisions[-1].to_mapping()
    with pytest.raises(FrozenInstanceError):
        decisions[-1].outcome = "mastered"  # type: ignore[misc]


def test_repeated_scenario_is_deterministic() -> None:
    def scenario() -> tuple[dict, ...]:
        runtime = LessonConversationRuntime(_contract(), attempt_id_factory=lambda: "attempt-fixed")
        results = [runtime.open_attempt()]
        results.append(runtime.child_response(_identity(runtime), "meaning_vi"))
        results.append(runtime.context_turn(_identity(runtime)))
        results.append(runtime.child_response(_identity(runtime), "target"))
        results.append(runtime.pronunciation_outcome(_identity(runtime), "correct"))
        return tuple(deepcopy(item.to_mapping()) for item in results)

    assert scenario() == scenario()
