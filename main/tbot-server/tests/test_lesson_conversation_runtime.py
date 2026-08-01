from __future__ import annotations

from copy import deepcopy
from dataclasses import FrozenInstanceError

import pytest

from core.lesson.conversation_contract import (
    ConversationContractError,
    LessonConversationContract,
    LessonToolIdentity,
    lesson_conversation_contract_from_backend,
)
from core.lesson.conversation_runtime import (
    ConversationState,
    LessonConversationRuntime,
)


def _payload(*, phonemes: bool = False) -> dict[str, object]:
    pronunciation: dict[str, object] = {
        "slow_model": "ap-ple",
        "l1_guidance_vi": "Mở miệng nhẹ ở âm đầu.",
    }
    pronunciation["phonemes" if phonemes else "segments"] = ["æ", "p", "əl"] if phonemes else ["ap", "ple"]
    effects: dict[str, str] = {
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
    payload: dict[str, object] = {
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
    return payload


def _contract(**kwargs) -> LessonConversationContract:
    payload = _payload(**kwargs)
    return LessonConversationContract.from_mapping(payload)


def _backend_step(step_key: str, index: int, count: int, *, terminal: bool) -> dict[str, object]:
    target = "barn" if step_key == "barn" else "hay"
    effects = [
        "teach", "listen", "thinking", "correct", "retry-level-1",
        "retry-level-2", "retry-level-3", "celebrate",
    ]
    cues = [{
        "cueId": f"{step_key}-{effect}",
        "effect": effect,
        "playbackMode": "loop" if effect in {"listen", "thinking"} else "once",
    } for effect in effects]
    if not terminal:
        cues.append({
            "cueId": f"{step_key}-to-hay-word-transition",
            "effect": "word-transition",
            "playbackMode": "once",
        })
    return {
        "stepKey": step_key,
        "targetWord": target,
        "vietnameseMeanings": ["nhà kho"] if target == "barn" else ["cỏ khô"],
        "relatedConcepts": ["farm"],
        "questionSeeds": [f"Can you say {target}?"],
        "teachingCopy": {
            "intro": f"This is {target}.",
            "explanation": f"Learn {target}.",
            "prompt": f"Say {target}.",
        },
        "expectedAnswer": target,
        "progress": {"index": index, "count": count},
        "pronunciation": {
            "slowModel": target,
            "approvedSegments": [target],
            "vietnameseL1Guidance": ["Nói chậm và rõ."],
        },
        "contextTurns": ["one", "two"],
        "cues": cues,
    }


def _backend_manifest() -> dict[str, object]:
    steps = [
        _backend_step("barn", 1, 2, terminal=False),
        _backend_step("hay", 2, 2, terminal=True),
    ]
    phases = [
        {"templateId": "flattenedMjpegCinematic", "templateVersion": 2,
         "cueId": "barn-opening", "effect": "opening", "stepKey": "barn", "playbackMode": "once",
         "timing": {}, "asset": {}},
        {"templateId": "flattenedMjpegCinematic", "templateVersion": 2,
         "cueId": "barn-greet", "effect": "greet", "stepKey": "barn", "playbackMode": "loop",
         "timing": {}, "asset": {}},
    ]
    for index, step in enumerate(steps):
        for cue in step["cues"]:
            phases.append({
                "templateId": "flattenedMjpegCinematic", "templateVersion": 2,
                **cue,
                "stepKey": steps[index + 1]["stepKey"] if cue["effect"] == "word-transition" else step["stepKey"],
                "timing": {}, "asset": {},
            })
    return {
        "manifestVersion": "teebot-lesson-renderer.v4",
        "protocolVersion": "teebot-lesson-renderer.v4",
        "lessonId": "farm-english",
        "lessonVersion": 4,
        "conversation": {
            "presetId": "tvideoJourney",
            "presetVersion": 1,
            "maxContextualTurns": 2,
            "steps": steps,
        },
        "cinematicPhases": phases,
    }


def _runtime() -> LessonConversationRuntime:
    ids = iter(("attempt-a", "attempt-b", "attempt-c"))
    return LessonConversationRuntime(_contract(), attempt_id_factory=lambda: next(ids))


def _identity(runtime: LessonConversationRuntime, *, cue_id: str | None = None) -> LessonToolIdentity:
    return runtime.identity(cue_id=cue_id)


def _raw_identity(runtime: LessonConversationRuntime, cue_id: str) -> LessonToolIdentity:
    assert runtime.attempt_id is not None
    return LessonToolIdentity(
        lesson_session_id="session-7a",
        turn_sequence_id=runtime.turn_sequence_id,
        attempt_id=runtime.attempt_id,
        step_key="farm.apple",
        cue_id=cue_id,
    )


def test_contract_is_exact_deeply_immutable_and_supports_either_pronunciation_union() -> None:
    payload = _payload()
    contract = LessonConversationContract.from_mapping(payload)
    meanings = payload["meanings_vi"]
    cues = payload["cues"]
    assert isinstance(meanings, list)
    assert isinstance(cues, dict)
    meanings.append("changed")
    cues["listen"]["cue_id"] = "changed"

    assert contract.meanings_vi == ("quả táo", "trái táo")
    assert contract.cues[1].cue_id == "farm.apple.listen"
    assert LessonConversationContract.from_mapping(_payload(phonemes=True)).pronunciation.phonemes
    with pytest.raises(FrozenInstanceError):
        contract.target_word = "pear"  # type: ignore[misc]


def test_backend_conversation_adapter_preserves_raw_cues_and_terminal_transition_rule() -> None:
    manifest = _backend_manifest()
    barn = lesson_conversation_contract_from_backend(
        manifest, lesson_session_id="session-7a", step_key="barn"
    )
    hay = lesson_conversation_contract_from_backend(
        manifest, lesson_session_id="session-7a", step_key="hay"
    )

    assert barn.cue_map["retry_level_2"].cue_id == "barn-retry-level-2"
    assert barn.cue_map["word_transition"].cue_id == "barn-to-hay-word-transition"
    assert hay.progress_index == hay.progress_count == 2
    assert "word_transition" not in hay.cue_map


@pytest.mark.parametrize(
    "mutate",
    [
        lambda manifest: manifest["conversation"]["steps"][0]["cues"].pop(),
        lambda manifest: manifest["conversation"]["steps"][1]["cues"].append({
            "cueId": "hay-word-transition", "effect": "word-transition", "playbackMode": "once",
        }),
        lambda manifest: manifest["conversation"]["steps"][0]["cues"][0].update(effect="listen"),
        lambda manifest: manifest["conversation"]["steps"][0]["cues"][0].update(extra=True),
        lambda manifest: manifest["cinematicPhases"].pop(),
    ],
)
def test_backend_conversation_adapter_rejects_missing_extra_stale_or_terminal_cues(mutate) -> None:
    manifest = _backend_manifest()
    mutate(manifest)
    with pytest.raises(ConversationContractError):
        lesson_conversation_contract_from_backend(
            manifest, lesson_session_id="session-7a", step_key="barn"
        )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda manifest: manifest["conversation"].update(presetVersion=True),
        lambda manifest: manifest["conversation"].update(presetVersion=1.0),
        lambda manifest: manifest["conversation"].update(maxContextualTurns=2.0),
        lambda manifest: manifest["conversation"]["steps"][0]["progress"].update(index=True),
        lambda manifest: manifest["conversation"]["steps"][0]["progress"].update(count=2.0),
        lambda manifest: manifest["conversation"]["steps"][0]["cues"][0].update(effect=[]),
        lambda manifest: manifest["conversation"]["steps"][0]["cues"][0].update(playbackMode=[]),
        lambda manifest: manifest["cinematicPhases"][0].update(templateVersion=2.0),
        lambda manifest: manifest["cinematicPhases"][0].update(effect=[]),
    ],
)
def test_backend_adapter_rejects_malformed_types_as_contract_errors(mutate) -> None:
    manifest = _backend_manifest()
    mutate(manifest)

    with pytest.raises(ConversationContractError):
        lesson_conversation_contract_from_backend(
            manifest, lesson_session_id="session-7a", step_key="barn"
        )


def test_terminal_step_continue_completes_without_nonexistent_transition_cue() -> None:
    contract = lesson_conversation_contract_from_backend(
        _backend_manifest(), lesson_session_id="session-7a", step_key="hay"
    )
    runtime = LessonConversationRuntime(contract, attempt_id_factory=lambda: "attempt-terminal")
    runtime.open_attempt()
    runtime.child_response(_identity(runtime), "target")
    runtime.pronunciation_outcome(_identity(runtime), "correct")

    continued = runtime.continue_lesson(
        _identity(runtime, cue_id="hay-celebrate"), effect="show_celebration"
    )

    assert continued.accepted
    assert continued.next_intent == "complete_lesson"
    assert continued.cue_id is None


def test_terminal_attempted_review_continue_needs_no_transition_visual() -> None:
    contract = lesson_conversation_contract_from_backend(
        _backend_manifest(), lesson_session_id="session-7a", step_key="hay"
    )
    runtime = LessonConversationRuntime(contract, attempt_id_factory=lambda: "attempt-terminal")
    runtime.open_attempt()
    runtime.child_response(_identity(runtime), "target")
    for _ in range(4):
        runtime.pronunciation_outcome(_identity(runtime), "retry")

    continued = runtime.continue_lesson(_identity(runtime), effect=None)

    assert continued.accepted
    assert continued.next_intent == "complete_lesson"
    assert continued.cue_id is None


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
    assert heard.state is ConversationState.LISTENING
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
    ("setup", "expected_state"),
    [
        (None, ConversationState.ASKING),
        ("meaning_vi", ConversationState.BRIDGING),
        ("silence", ConversationState.COACHING),
        ("correct", ConversationState.REACTING),
    ],
)
def test_interrupt_during_model_speech_or_reaction_retires_identity_and_listens(setup, expected_state) -> None:
    runtime = _runtime()
    runtime.open_attempt()
    if setup == "correct":
        runtime.child_response(_identity(runtime), "target")
        runtime.pronunciation_outcome(_identity(runtime), "correct")
    elif setup is not None:
        runtime.child_response(_identity(runtime), setup)
    assert runtime.state is expected_state
    old = _identity(runtime)
    decision = runtime.interrupt(old)
    assert decision.state is ConversationState.LISTENING
    assert decision.next_intent == "listen_to_child"
    assert runtime.turn_sequence_id == old.turn_sequence_id + 1
    assert runtime.child_response(old, "target").code == "STALE_IDENTITY"


def test_begin_turn_increments_sequence_and_child_can_barge_into_listening() -> None:
    runtime = _runtime()
    runtime.open_attempt()
    identity = _identity(runtime, cue_id="farm.apple.listen")
    begun = runtime.begin_turn(identity, "listen")
    assert begun.accepted
    assert runtime.turn_sequence_id == identity.turn_sequence_id + 1
    assert runtime.begin_turn(identity, "listen").code == "STALE_IDENTITY"
    barged = runtime.child_response(_identity(runtime), "meaning_vi")
    assert barged.state is ConversationState.BRIDGING


def test_semantic_replay_is_duplicate_until_newer_turn_retires_it_as_stale() -> None:
    runtime = _runtime()
    runtime.open_attempt()
    semantic_identity = _identity(runtime)
    runtime.child_response(semantic_identity, "meaning_vi")
    assert runtime.child_response(semantic_identity, "meaning_vi").code == "DUPLICATE_IDENTITY"

    runtime.begin_turn(_identity(runtime, cue_id="farm.apple.teach"), "teach")
    assert runtime.child_response(semantic_identity, "meaning_vi").code == "STALE_IDENTITY"


@pytest.mark.parametrize(
    "transition_to",
    [
        ConversationState.ASKING,
        ConversationState.BRIDGING,
        ConversationState.COACHING,
        ConversationState.REACTING,
        ConversationState.COMPLETE,
    ],
)
def test_begin_turn_rejects_caller_selected_semantic_state_jumps_without_mutation(transition_to) -> None:
    runtime = _runtime()
    runtime.open_attempt()
    snapshot = runtime.snapshot()
    decision = runtime.begin_turn(
        _identity(runtime, cue_id="farm.apple.listen"),
        "listen",
        transition_to=transition_to,
    )
    assert decision.code == "ILLEGAL_STATE_TRANSITION"
    assert runtime.snapshot() == snapshot


@pytest.mark.parametrize(
    ("setup", "expected_state", "cue_role"),
    [
        (None, ConversationState.ASKING, "listen"),
        ("meaning_vi", ConversationState.BRIDGING, "teach"),
        ("silence", ConversationState.COACHING, "retry_level_1"),
        ("correct", ConversationState.REACTING, "celebrate"),
    ],
)
def test_begin_turn_can_retain_each_authoritative_speaking_state(setup, expected_state, cue_role) -> None:
    runtime = _runtime()
    runtime.open_attempt()
    if setup == "correct":
        runtime.child_response(_identity(runtime), "target")
        runtime.pronunciation_outcome(_identity(runtime), "correct")
    elif setup is not None:
        runtime.child_response(_identity(runtime), setup)
    before_sequence = runtime.turn_sequence_id
    cue_id = f"farm.apple.{cue_role}"
    decision = runtime.begin_turn(_identity(runtime, cue_id=cue_id), cue_role)
    assert decision.accepted
    assert decision.state is expected_state
    assert runtime.turn_sequence_id == before_sequence + 1


@pytest.mark.parametrize(
    ("setup", "cue_role"),
    [(None, "listen"), ("meaning_vi", "teach"), ("silence", "retry_level_1"), ("correct", "celebrate")],
)
def test_begin_turn_allows_only_listening_transition_from_speaking_states(setup, cue_role) -> None:
    runtime = _runtime()
    runtime.open_attempt()
    if setup == "correct":
        runtime.child_response(_identity(runtime), "target")
        runtime.pronunciation_outcome(_identity(runtime), "correct")
    elif setup is not None:
        runtime.child_response(_identity(runtime), setup)
    decision = runtime.begin_turn(
        _identity(runtime, cue_id=f"farm.apple.{cue_role}"),
        cue_role,
        transition_to=ConversationState.LISTENING,
    )
    assert decision.accepted
    assert decision.state is ConversationState.LISTENING


def test_begin_turn_rejects_complete_state_without_mutation() -> None:
    runtime = _runtime()
    runtime.open_attempt()
    runtime.child_response(_identity(runtime), "target")
    runtime.pronunciation_outcome(_identity(runtime), "correct")
    runtime.continue_lesson(
        _identity(runtime, cue_id="farm.apple.celebrate"),
        effect="show_celebration",
    )
    snapshot = runtime.snapshot()
    decision = runtime.begin_turn(_identity(runtime, cue_id="farm.apple.word_transition"), "word_transition")
    assert decision.code == "TURN_NOT_AVAILABLE"
    assert runtime.snapshot() == snapshot


def test_begin_turn_rejects_new_turn_from_listening_without_mutation() -> None:
    runtime = _runtime()
    runtime.open_attempt()
    runtime.interrupt(_identity(runtime))
    snapshot = runtime.snapshot()
    decision = runtime.begin_turn(_identity(runtime, cue_id="farm.apple.listen"), "listen")
    assert decision.code == "TURN_NOT_AVAILABLE"
    assert runtime.snapshot() == snapshot


def test_begin_turn_listening_transition_requires_pending_cue_without_mutation() -> None:
    runtime = _runtime()
    runtime.open_attempt()
    snapshot = runtime.snapshot()
    decision = runtime.begin_turn(
        _raw_identity(runtime, "farm.apple.teach"),
        "teach",
        transition_to=ConversationState.LISTENING,
    )
    assert decision.code == "ILLEGAL_CUE"
    assert runtime.snapshot() == snapshot


@pytest.mark.parametrize("setup", [None, "meaning_vi", "silence", "correct"])
def test_pronunciation_outcome_rejects_non_assessment_states_without_mutation(setup) -> None:
    runtime = _runtime()
    runtime.open_attempt()
    if setup == "correct":
        runtime.child_response(_identity(runtime), "target")
        runtime.pronunciation_outcome(_identity(runtime), "correct")
    elif setup is not None:
        runtime.child_response(_identity(runtime), setup)
    snapshot = runtime.snapshot()
    decision = runtime.pronunciation_outcome(_identity(runtime), "retry")
    assert decision.code == "PRONUNCIATION_NOT_AVAILABLE"
    assert runtime.snapshot() == snapshot


def test_mastered_completion_rejects_pronunciation_and_cannot_reopen() -> None:
    runtime = _runtime()
    runtime.open_attempt()
    runtime.child_response(_identity(runtime), "target")
    runtime.pronunciation_outcome(_identity(runtime), "correct")
    runtime.continue_lesson(
        _identity(runtime, cue_id="farm.apple.celebrate"),
        effect="show_celebration",
    )
    snapshot = runtime.snapshot()
    assert runtime.pronunciation_outcome(_identity(runtime), "correct").code == ("PRONUNCIATION_NOT_AVAILABLE")
    assert runtime.open_attempt().code == "ATTEMPT_ALREADY_OPENED"
    assert runtime.snapshot() == snapshot


def test_interrupt_retires_prior_speaking_evidence_before_listening() -> None:
    runtime = _runtime()
    runtime.open_attempt()
    runtime.child_response(_identity(runtime), "target")
    runtime.pronunciation_outcome(_identity(runtime), "correct")
    runtime.interrupt(_identity(runtime))
    snapshot = runtime.snapshot()
    decision = runtime.pronunciation_outcome(_identity(runtime), "retry")
    assert decision.code == "SPEAKING_EVIDENCE_REQUIRED"
    assert runtime.snapshot() == snapshot


def test_attempted_completion_rejects_pronunciation_and_cannot_reopen() -> None:
    runtime = _runtime()
    runtime.open_attempt()
    runtime.child_response(_identity(runtime), "target")
    for _ in range(4):
        runtime.pronunciation_outcome(_identity(runtime), "retry")
    snapshot = runtime.snapshot()
    assert runtime.pronunciation_outcome(_identity(runtime), "correct").code == ("PRONUNCIATION_NOT_AVAILABLE")
    assert runtime.open_attempt().code == "ATTEMPT_ALREADY_OPENED"
    assert runtime.snapshot() == snapshot


def test_visual_reaction_continue_and_skip_are_server_authoritative() -> None:
    runtime = _runtime()
    runtime.open_attempt()
    bad_cue = runtime.visual_reaction(
        _raw_identity(runtime, "farm.apple.correct"),
        "correct",
        effect="show_correct_reaction",
    )
    bad_effect = runtime.visual_reaction(
        _identity(runtime, cue_id="farm.apple.listen"), "listen", effect="show_celebration"
    )
    premature = runtime.continue_lesson(
        _identity(runtime, cue_id="farm.apple.listen"),
        effect="show_listening_scene",
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
        _identity(runtime, cue_id="farm.apple.celebrate"),
        effect="show_celebration",
    )
    assert continued.next_intent == "continue_lesson"
    assert continued.cue_id == "farm.apple.word_transition"


def test_celebration_is_unavailable_before_authoritative_pronunciation_mastery() -> None:
    runtime = _runtime()
    runtime.open_attempt()
    runtime.child_response(_identity(runtime), "target")
    decision = runtime.visual_reaction(
        _raw_identity(runtime, "farm.apple.celebrate"),
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


def test_target_thinking_cue_blocks_future_retry_cues_without_mutation() -> None:
    runtime = _runtime()
    runtime.open_attempt()
    runtime.child_response(_identity(runtime), "target")
    snapshot = runtime.snapshot()

    with pytest.raises(ValueError, match="pending cue"):
        _identity(runtime, cue_id="farm.apple.retry_level_3")
    visual = runtime.visual_reaction(
        _raw_identity(runtime, "farm.apple.retry_level_3"),
        "retry_level_3",
        effect="show_pronunciation_guide",
    )
    begun = runtime.begin_turn(_raw_identity(runtime, "farm.apple.retry_level_2"), "retry_level_2")
    assert [visual.code, begun.code] == ["ILLEGAL_CUE", "ILLEGAL_CUE"]
    assert runtime.snapshot() == snapshot


def test_silence_level_one_blocks_unissued_later_retry_cues() -> None:
    runtime = _runtime()
    runtime.open_attempt()
    runtime.child_response(_identity(runtime), "silence")
    snapshot = runtime.snapshot()
    for role, effect in (
        ("retry_level_2", "show_slow_model"),
        ("retry_level_3", "show_pronunciation_guide"),
    ):
        visual = runtime.visual_reaction(_raw_identity(runtime, f"farm.apple.{role}"), role, effect=effect)
        begun = runtime.begin_turn(_raw_identity(runtime, f"farm.apple.{role}"), role)
        assert [visual.code, begun.code] == ["ILLEGAL_CUE", "ILLEGAL_CUE"]
        assert runtime.snapshot() == snapshot


def test_retry_cues_unlock_only_when_issued_and_old_cues_expire() -> None:
    runtime = _runtime()
    runtime.open_attempt()
    runtime.child_response(_identity(runtime), "target")

    level_one = runtime.pronunciation_outcome(_identity(runtime), "retry")
    assert level_one.cue_id == "farm.apple.retry_level_1"
    assert runtime.visual_reaction(
        _identity(runtime, cue_id=level_one.cue_id),
        "retry_level_1",
        effect="show_effort_reaction",
    ).accepted

    level_two = runtime.pronunciation_outcome(_identity(runtime), "retry")
    assert level_two.cue_id == "farm.apple.retry_level_2"
    stale_level_one = runtime.visual_reaction(
        _raw_identity(runtime, "farm.apple.retry_level_1"),
        "retry_level_1",
        effect="show_effort_reaction",
    )
    assert stale_level_one.code == "ILLEGAL_CUE"
    assert runtime.visual_reaction(
        _identity(runtime, cue_id=level_two.cue_id),
        "retry_level_2",
        effect="show_slow_model",
    ).accepted

    level_three = runtime.pronunciation_outcome(_identity(runtime), "retry")
    assert level_three.cue_id == "farm.apple.retry_level_3"
    assert runtime.begin_turn(_identity(runtime, cue_id=level_three.cue_id), "retry_level_3").accepted


def test_continue_rejects_model_selected_cue_effect_or_step() -> None:
    runtime = _runtime()
    runtime.open_attempt()
    runtime.child_response(_identity(runtime), "target")
    runtime.pronunciation_outcome(_identity(runtime), "correct")
    wrong_cue = runtime.continue_lesson(_raw_identity(runtime, "farm.apple.word_transition"), effect="show_celebration")
    wrong_effect = runtime.continue_lesson(
        _identity(runtime, cue_id="farm.apple.celebrate"), effect="show_word_transition"
    )
    selected_step = runtime.continue_lesson(
        _identity(runtime, cue_id="farm.apple.celebrate"),
        effect="show_celebration",
        next_step_key="farm.pear",
    )
    assert [wrong_cue.code, wrong_effect.code, selected_step.code] == [
        "ILLEGAL_CUE",
        "ILLEGAL_EFFECT",
        "MODEL_STEP_SELECTION_FORBIDDEN",
    ]


def test_mastered_continue_is_applied_only_once_without_mutation_on_replay() -> None:
    runtime = _runtime()
    runtime.open_attempt()
    runtime.child_response(_identity(runtime), "target")
    runtime.pronunciation_outcome(_identity(runtime), "correct")
    first = runtime.continue_lesson(
        _identity(runtime, cue_id="farm.apple.celebrate"),
        effect="show_celebration",
    )
    assert first.accepted
    snapshot = runtime.snapshot()
    replay = runtime.continue_lesson(
        _identity(runtime, cue_id="farm.apple.word_transition"),
        effect="show_word_transition",
    )
    assert replay.code == "CONTINUE_ALREADY_APPLIED"
    assert runtime.snapshot() == snapshot


def test_attempted_review_continue_is_applied_only_once() -> None:
    runtime = _runtime()
    runtime.open_attempt()
    runtime.child_response(_identity(runtime), "target")
    for _ in range(4):
        runtime.pronunciation_outcome(_identity(runtime), "retry")
    first = runtime.continue_lesson(
        _identity(runtime, cue_id="farm.apple.word_transition"),
        effect="show_word_transition",
    )
    assert first.accepted
    snapshot = runtime.snapshot()
    replay = runtime.continue_lesson(
        _identity(runtime, cue_id="farm.apple.word_transition"),
        effect="show_word_transition",
    )
    assert replay.code == "CONTINUE_ALREADY_APPLIED"
    assert runtime.snapshot() == snapshot


def test_word_transition_visual_ack_does_not_renew_continue_authority() -> None:
    runtime = _runtime()
    runtime.open_attempt()
    runtime.child_response(_identity(runtime), "target")
    runtime.pronunciation_outcome(_identity(runtime), "correct")
    runtime.continue_lesson(
        _identity(runtime, cue_id="farm.apple.celebrate"),
        effect="show_celebration",
    )
    acknowledged = runtime.visual_reaction(
        _identity(runtime, cue_id="farm.apple.word_transition"),
        "word_transition",
        effect="show_word_transition",
    )
    assert acknowledged.accepted
    snapshot = runtime.snapshot()
    replay = runtime.continue_lesson(
        _raw_identity(runtime, "farm.apple.word_transition"),
        effect="show_word_transition",
    )
    repeated_visual = runtime.visual_reaction(
        _raw_identity(runtime, "farm.apple.word_transition"),
        "word_transition",
        effect="show_word_transition",
    )
    assert replay.code == "CONTINUE_ALREADY_APPLIED"
    assert repeated_visual.code == "ILLEGAL_CUE"
    assert runtime.snapshot() == snapshot


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
