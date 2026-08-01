"""Pure server-authoritative state machine for one conversational lesson step."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, Literal

from core.lesson.conversation_contract import (
    CueSpec,
    LessonConversationContract,
    LessonToolIdentity,
    PronunciationGuidance,
)


class ConversationState(str, Enum):
    ASKING = "ASKING"
    LISTENING = "LISTENING"
    BRIDGING = "BRIDGING"
    COACHING = "COACHING"
    REACTING = "REACTING"
    COMPLETE = "COMPLETE"


@dataclass(frozen=True)
class SpeakingEvidence:
    outcome: Literal["mastered", "attempted", "comprehended"]
    attempt_count: int
    final_coaching_level: int
    elapsed_ms: int
    step_key: str
    lesson_version: int

    def __post_init__(self) -> None:
        if self.outcome not in {"mastered", "attempted", "comprehended"}:
            raise ValueError("unsupported evidence outcome")
        for name in (
            "attempt_count",
            "final_coaching_level",
            "elapsed_ms",
            "lesson_version",
        ):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise TypeError(f"{name} must be a non-negative integer")
        if self.attempt_count < 1 or self.lesson_version < 1:
            raise ValueError("attempt_count and lesson_version must be positive")
        if self.final_coaching_level > 3:
            raise ValueError("final_coaching_level must be between 0 and 3")
        if not isinstance(self.step_key, str) or not self.step_key.strip():
            raise TypeError("step_key must be a non-empty string")

    def to_mapping(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "attempt_count": self.attempt_count,
            "final_coaching_level": self.final_coaching_level,
            "elapsed_ms": self.elapsed_ms,
            "step_key": self.step_key,
            "lesson_version": self.lesson_version,
        }


@dataclass(frozen=True)
class ModelGuidanceFacts:
    target_word: str
    meanings_vi: tuple[str, ...]
    related_concepts: tuple[str, ...]
    teaching_copy: str
    expected_answer: str
    pronunciation: PronunciationGuidance

    def to_mapping(self) -> dict[str, Any]:
        return {
            "target_word": self.target_word,
            "meanings_vi": self.meanings_vi,
            "related_concepts": self.related_concepts,
            "teaching_copy": self.teaching_copy,
            "expected_answer": self.expected_answer,
            "pronunciation": {
                "slow_model": self.pronunciation.slow_model,
                "segments": self.pronunciation.segments,
                "phonemes": self.pronunciation.phonemes,
                "l1_guidance_vi": self.pronunciation.l1_guidance_vi,
            },
        }


@dataclass(frozen=True)
class ConversationDecision:
    accepted: bool
    code: str
    state: ConversationState
    next_intent: str
    cue_id: str | None
    effect: str | None
    coaching_level: int
    outcome: str | None
    review_needed: bool
    guidance: ModelGuidanceFacts

    def to_mapping(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "code": self.code,
            "state": self.state.value,
            "next_intent": self.next_intent,
            "cue_id": self.cue_id,
            "effect": self.effect,
            "coaching_level": self.coaching_level,
            "outcome": self.outcome,
            "review_needed": self.review_needed,
            "guidance": self.guidance.to_mapping(),
        }


_SPEAKING_STATES = frozenset(
    {
        ConversationState.ASKING,
        ConversationState.BRIDGING,
        ConversationState.COACHING,
        ConversationState.REACTING,
    }
)
_VISUAL_ROLES = MappingProxyType(
    {
        ConversationState.ASKING: frozenset({"teach", "listen"}),
        ConversationState.LISTENING: frozenset(
            {"listen", "thinking", "retry_level_1", "retry_level_2", "retry_level_3"}
        ),
        ConversationState.BRIDGING: frozenset({"teach", "listen"}),
        ConversationState.COACHING: frozenset({"listen", "retry_level_1", "retry_level_2", "retry_level_3"}),
        ConversationState.REACTING: frozenset({"thinking", "correct", "celebrate"}),
        ConversationState.COMPLETE: frozenset({"word_transition"}),
    }
)

_INACTIVE_GUIDANCE = ModelGuidanceFacts(
    target_word="",
    meanings_vi=(),
    related_concepts=(),
    teaching_copy="",
    expected_answer="",
    pronunciation=PronunciationGuidance(
        slow_model="",
        segments=None,
        phonemes=None,
        l1_guidance_vi="",
    ),
)


def inactive_conversation_decision() -> ConversationDecision:
    return ConversationDecision(
        accepted=False,
        code="CONVERSATION_NOT_ACTIVE",
        state=ConversationState.COMPLETE,
        next_intent="retain_authoritative_state",
        cue_id=None,
        effect=None,
        coaching_level=0,
        outcome=None,
        review_needed=False,
        guidance=_INACTIVE_GUIDANCE,
    )


class LessonConversationRuntime:
    """Own all semantic progress for a single validated lesson step."""

    def __init__(
        self,
        contract: LessonConversationContract,
        *,
        attempt_id_factory: Callable[[], str],
    ) -> None:
        if not isinstance(contract, LessonConversationContract):
            raise TypeError("contract must be a LessonConversationContract")
        if not callable(attempt_id_factory):
            raise TypeError("attempt_id_factory must be callable")
        self._contract = contract
        self._attempt_id_factory = attempt_id_factory
        self._state = ConversationState.COMPLETE
        self._turn_sequence_id = 0
        self._attempt_id: str | None = None
        self._attempt_count = 0
        self._coaching_level = 0
        self._contextual_turn_count = 0
        self._outcome: str | None = None
        self._review_needed = False
        self._mastered = False
        self._speaking_evidence = False
        self._used_identities: set[LessonToolIdentity] = set()
        self._retired_identities: set[LessonToolIdentity] = set()
        self._pending_cue_id: str | None = None
        self._pending_effect: str | None = None
        self._pending_intent: str | None = None
        self._continue_applied = False
        self._guidance = ModelGuidanceFacts(
            target_word=contract.target_word,
            meanings_vi=contract.meanings_vi,
            related_concepts=contract.related_concepts,
            teaching_copy=contract.teaching_copy,
            expected_answer=contract.expected_answer,
            pronunciation=contract.pronunciation,
        )

    @property
    def current_target(self) -> str:
        return self._contract.target_word

    @property
    def state(self) -> ConversationState:
        return self._state

    @property
    def turn_sequence_id(self) -> int:
        return self._turn_sequence_id

    @property
    def attempt_id(self) -> str | None:
        return self._attempt_id

    @property
    def attempt_count(self) -> int:
        return self._attempt_count

    @property
    def contextual_turn_count(self) -> int:
        return self._contextual_turn_count

    @property
    def mastered(self) -> bool:
        return self._mastered

    @property
    def outcome(self) -> str | None:
        return self._outcome

    @property
    def review_needed(self) -> bool:
        return self._review_needed

    @property
    def pending_cue_id(self) -> str | None:
        return self._pending_cue_id

    @property
    def pending_effect(self) -> str | None:
        return self._pending_effect

    @property
    def pending_intent(self) -> str | None:
        return self._pending_intent

    @property
    def continue_applied(self) -> bool:
        return self._continue_applied

    @property
    def coaching_level(self) -> int:
        return self._coaching_level

    @property
    def guidance(self) -> ModelGuidanceFacts:
        return self._guidance

    def identity(self, *, cue_id: str | None = None) -> LessonToolIdentity:
        if self._attempt_id is None or self._turn_sequence_id < 1:
            raise RuntimeError("open_attempt must establish identity first")
        if cue_id is not None and cue_id != self._pending_cue_id:
            raise ValueError("cue_id does not match the pending cue")
        return LessonToolIdentity(
            lesson_session_id=self._contract.lesson_session_id,
            turn_sequence_id=self._turn_sequence_id,
            attempt_id=self._attempt_id,
            step_key=self._contract.step_key,
            cue_id=cue_id,
        )

    def snapshot(self) -> tuple[Any, ...]:
        return (
            self._state,
            self._turn_sequence_id,
            self._attempt_id,
            self._attempt_count,
            self._coaching_level,
            self._contextual_turn_count,
            self._outcome,
            self._review_needed,
            self._mastered,
            self._speaking_evidence,
            frozenset(self._used_identities),
            frozenset(self._retired_identities),
            self._pending_cue_id,
            self._pending_effect,
            self._pending_intent,
            self._continue_applied,
        )

    def restore_authoritative_snapshot(self, snapshot: tuple[Any, ...]) -> None:
        (
            self._state,
            self._turn_sequence_id,
            self._attempt_id,
            self._attempt_count,
            self._coaching_level,
            self._contextual_turn_count,
            self._outcome,
            self._review_needed,
            self._mastered,
            self._speaking_evidence,
            used_identities,
            retired_identities,
            self._pending_cue_id,
            self._pending_effect,
            self._pending_intent,
            self._continue_applied,
        ) = snapshot
        self._used_identities = set(used_identities)
        self._retired_identities = set(retired_identities)

    def reject(self, code: str) -> ConversationDecision:
        return self._reject(code)

    def _cue(self, role: str) -> CueSpec:
        return self._contract.cue_map[role]

    def _allowed_visual_roles(self, state: ConversationState | None = None) -> frozenset[str]:
        selected_state = state or self._state
        if selected_state is ConversationState.REACTING:
            return frozenset({"correct", "celebrate"}) if self._mastered else frozenset({"thinking"})
        return _VISUAL_ROLES[selected_state]

    def _decision(
        self,
        *,
        accepted: bool = True,
        code: str = "ACCEPTED",
        intent: str,
        cue_role: str | None = None,
    ) -> ConversationDecision:
        cue = self._cue(cue_role) if cue_role else None
        decision = ConversationDecision(
            accepted=accepted,
            code=code,
            state=self._state,
            next_intent=intent,
            cue_id=cue.cue_id if cue else None,
            effect=cue.effect if cue else None,
            coaching_level=self._coaching_level,
            outcome=self._outcome,
            review_needed=self._review_needed,
            guidance=self._guidance,
        )
        if accepted:
            self._pending_cue_id = decision.cue_id
            self._pending_effect = decision.effect
            self._pending_intent = decision.next_intent
        return decision

    def _reject(self, code: str) -> ConversationDecision:
        return self._decision(accepted=False, code=code, intent="retain_authoritative_state")

    def _identity_rejection(self, identity: LessonToolIdentity | None) -> ConversationDecision | None:
        if not isinstance(identity, LessonToolIdentity):
            return self._reject("MISSING_IDENTITY")
        if identity.lesson_session_id != self._contract.lesson_session_id:
            return self._reject("CROSS_SESSION")
        if identity.attempt_id != self._attempt_id:
            return self._reject("CROSS_ATTEMPT")
        if identity.step_key != self._contract.step_key:
            return self._reject("CROSS_STEP")
        if identity in self._retired_identities:
            return self._reject("STALE_IDENTITY")
        if identity in self._used_identities:
            return self._reject("DUPLICATE_IDENTITY")
        if identity.turn_sequence_id < self._turn_sequence_id:
            return self._reject("STALE_IDENTITY")
        if identity.turn_sequence_id > self._turn_sequence_id:
            return self._reject("REORDERED_IDENTITY")
        return None

    def _consume(self, identity: LessonToolIdentity, *, retired: bool = False) -> None:
        if retired:
            self._retired_identities.update(self._used_identities)
            self._used_identities.clear()
            self._retired_identities.add(identity)
        else:
            self._used_identities.add(identity)
        self._turn_sequence_id += 1

    def open_attempt(self) -> ConversationDecision:
        if self._attempt_id is not None:
            return self._reject("ATTEMPT_ALREADY_OPENED")
        attempt_id = self._attempt_id_factory()
        LessonToolIdentity(
            lesson_session_id=self._contract.lesson_session_id,
            turn_sequence_id=1,
            attempt_id=attempt_id,
            step_key=self._contract.step_key,
        )
        self._attempt_id = attempt_id
        self._attempt_count += 1
        self._turn_sequence_id += 1
        self._state = ConversationState.ASKING
        self._coaching_level = 0
        self._contextual_turn_count = 0
        self._outcome = None
        self._review_needed = False
        self._mastered = False
        self._speaking_evidence = False
        self._used_identities.clear()
        self._retired_identities.clear()
        return self._decision(intent="scene_question", cue_role="listen")

    def child_response(self, identity: LessonToolIdentity | None, response_class: str) -> ConversationDecision:
        rejected = self._identity_rejection(identity)
        if rejected:
            return rejected
        if self._state is ConversationState.COMPLETE:
            return self._reject("ATTEMPT_COMPLETE")
        if response_class not in {"target", "meaning_vi", "related", "silence", "uncertain"}:
            return self._reject("UNSUPPORTED_RESPONSE_CLASS")
        assert identity is not None
        self._consume(identity)
        if response_class == "target":
            self._state = ConversationState.LISTENING
            self._speaking_evidence = True
            self._outcome = "speaking_evidence"
            return self._decision(intent="assess_pronunciation", cue_role="thinking")
        if response_class == "meaning_vi":
            self._state = ConversationState.BRIDGING
            self._outcome = "comprehension_only"
            return self._decision(intent="bridge_vietnamese", cue_role="teach")
        if response_class == "related":
            self._state = ConversationState.BRIDGING
            self._outcome = "related_understanding"
            return self._decision(intent="bridge_related", cue_role="teach")
        self._state = ConversationState.COACHING
        self._outcome = "supporting"
        intent = "narrow_question" if response_class == "silence" else "contrast_then_model"
        return self._decision(intent=intent, cue_role="retry_level_1")

    def pronunciation_outcome(self, identity: LessonToolIdentity | None, outcome: str) -> ConversationDecision:
        rejected = self._identity_rejection(identity)
        if rejected:
            return rejected
        if self._state is not ConversationState.LISTENING:
            return self._reject("PRONUNCIATION_NOT_AVAILABLE")
        if not self._speaking_evidence:
            return self._reject("SPEAKING_EVIDENCE_REQUIRED")
        if outcome not in {"correct", "retry", "uncertain"}:
            return self._reject("UNSUPPORTED_PRONUNCIATION_OUTCOME")
        assert identity is not None
        self._consume(identity)
        if outcome == "correct":
            self._state = ConversationState.REACTING
            self._mastered = True
            self._outcome = "mastered"
            self._review_needed = False
            return self._decision(intent="acknowledge_correct", cue_role="correct")
        if self._coaching_level < 3:
            self._coaching_level += 1
            self._state = ConversationState.LISTENING
            self._outcome = "trying"
            intents = {
                1: "praise_effort",
                2: "slow_whole_word",
                3: "approved_pronunciation_guidance",
            }
            return self._decision(
                intent=intents[self._coaching_level],
                cue_role=f"retry_level_{self._coaching_level}",
            )
        self._state = ConversationState.COMPLETE
        self._outcome = "attempted"
        self._review_needed = True
        return self._decision(
            intent="praise_effort_complete" if self._contract.is_terminal_step else "praise_effort_continue",
            cue_role=None if self._contract.is_terminal_step else "word_transition",
        )

    def live_fallback(
        self,
        identity: LessonToolIdentity | None,
        *,
        reason: str,
    ) -> ConversationDecision:
        rejected = self._identity_rejection(identity)
        if rejected:
            return rejected
        if reason not in {"timeout", "interrupted", "transport"}:
            return self._reject("UNSUPPORTED_LIVE_FALLBACK_REASON")
        if self._state is ConversationState.COMPLETE or self._mastered:
            return self._reject("LIVE_FALLBACK_NOT_AVAILABLE")
        assert identity is not None
        self._consume(identity, retired=True)
        self._state = ConversationState.LISTENING
        self._speaking_evidence = False
        self._outcome = "supporting"
        return self._decision(intent="curated_fallback_prompt", cue_role="thinking")

    def build_speaking_evidence(
        self,
        *,
        lesson_version: int,
        elapsed_ms: int,
    ) -> SpeakingEvidence | None:
        if self._mastered and self._outcome == "mastered":
            outcome: Literal["mastered", "attempted", "comprehended"] = "mastered"
        elif self._outcome == "attempted" and self._review_needed:
            outcome = "attempted"
        else:
            return None
        return SpeakingEvidence(
            outcome=outcome,
            attempt_count=self._attempt_count,
            final_coaching_level=self._coaching_level,
            elapsed_ms=elapsed_ms,
            step_key=self._contract.step_key,
            lesson_version=lesson_version,
        )

    def context_turn(self, identity: LessonToolIdentity | None) -> ConversationDecision:
        rejected = self._identity_rejection(identity)
        if rejected:
            return rejected
        if self._state is ConversationState.COMPLETE:
            return self._reject("ATTEMPT_COMPLETE")
        assert identity is not None
        self._consume(identity)
        self._state = ConversationState.BRIDGING
        if self._contextual_turn_count < self._contract.max_contextual_turns:
            self._contextual_turn_count += 1
            return self._decision(intent="bounded_context", cue_role="teach")
        return self._decision(intent="forced_back_to_target", cue_role="listen")

    def begin_turn(
        self,
        identity: LessonToolIdentity | None,
        cue_role: str,
        *,
        transition_to: ConversationState | None = None,
    ) -> ConversationDecision:
        rejected = self._identity_rejection(identity)
        if rejected:
            return rejected
        cue = self._contract.cue_map.get(cue_role)
        if cue is None or identity is None or identity.cue_id != cue.cue_id or cue.cue_id != self._pending_cue_id:
            return self._reject("ILLEGAL_CUE")
        if self._state is ConversationState.COMPLETE:
            return self._reject("TURN_NOT_AVAILABLE")
        if transition_to is not None and transition_to is not ConversationState.LISTENING:
            return self._reject("ILLEGAL_STATE_TRANSITION")
        if self._state is ConversationState.LISTENING and cue_role not in {
            "thinking",
            "retry_level_1",
            "retry_level_2",
            "retry_level_3",
        }:
            return self._reject("TURN_NOT_AVAILABLE")
        if self._state not in _SPEAKING_STATES and self._state is not ConversationState.LISTENING:
            return self._reject("TURN_NOT_AVAILABLE")
        if transition_to is ConversationState.LISTENING:
            output_role = "listen"
        elif cue_role not in self._allowed_visual_roles():
            return self._reject("ILLEGAL_STATE_TRANSITION")
        else:
            output_role = cue_role
        self._consume(identity, retired=True)
        if transition_to is ConversationState.LISTENING:
            self._state = ConversationState.LISTENING
            self._speaking_evidence = False
            return self._decision(intent="listen_to_child", cue_role=output_role)
        return self._decision(intent="begin_model_turn", cue_role=output_role)

    def interrupt(self, identity: LessonToolIdentity | None) -> ConversationDecision:
        rejected = self._identity_rejection(identity)
        if rejected:
            return rejected
        if self._state not in _SPEAKING_STATES:
            return self._reject("INTERRUPT_NOT_AVAILABLE")
        assert identity is not None
        self._consume(identity, retired=True)
        self._state = ConversationState.LISTENING
        self._speaking_evidence = False
        return self._decision(intent="listen_to_child", cue_role="listen")

    def visual_reaction(
        self,
        identity: LessonToolIdentity | None,
        cue_role: str,
        *,
        effect: str | None = None,
    ) -> ConversationDecision:
        rejected = self._identity_rejection(identity)
        if rejected:
            return rejected
        cue = self._contract.cue_map.get(cue_role)
        if cue is None or identity is None or identity.cue_id != cue.cue_id or cue.cue_id != self._pending_cue_id:
            return self._reject("ILLEGAL_CUE")
        if cue_role not in self._allowed_visual_roles():
            return self._reject("ILLEGAL_CUE")
        if effect != cue.effect or effect != self._pending_effect:
            return self._reject("ILLEGAL_EFFECT")
        if cue_role == "word_transition" and not self._continue_applied:
            return self._reject("CONTINUE_REQUIRED")
        self._consume(identity)
        if cue_role == "word_transition":
            self._pending_cue_id = None
            self._pending_effect = None
            self._pending_intent = "word_transition_authorized"
            return ConversationDecision(
                accepted=True,
                code="ACCEPTED",
                state=self._state,
                next_intent="word_transition_authorized",
                cue_id=cue.cue_id,
                effect=cue.effect,
                coaching_level=self._coaching_level,
                outcome=self._outcome,
                review_needed=self._review_needed,
                guidance=self._guidance,
            )
        return self._decision(intent="play_approved_reaction", cue_role=cue_role)

    def continue_lesson(
        self,
        identity: LessonToolIdentity | None,
        *,
        effect: str | None = None,
        next_step_key: str | None = None,
    ) -> ConversationDecision:
        rejected = self._identity_rejection(identity)
        if rejected:
            return rejected
        if self._continue_applied:
            return self._reject("CONTINUE_ALREADY_APPLIED")
        if not (self._mastered or (self._outcome == "attempted" and self._review_needed)):
            return self._reject("CONTINUE_NOT_ALLOWED")
        pending_role = next(
            (cue.role for cue in self._contract.cues if cue.cue_id == self._pending_cue_id),
            None,
        )
        if self._mastered and pending_role == "correct":
            if identity is None or identity.cue_id != self._pending_cue_id:
                return self._reject("ILLEGAL_CUE")
            if effect != self._pending_effect:
                return self._reject("ILLEGAL_EFFECT")
            if next_step_key is not None:
                return self._reject("MODEL_STEP_SELECTION_FORBIDDEN")
            self._consume(identity)
            return self._decision(intent="celebrate_mastery", cue_role="celebrate")
        if self._contract.is_terminal_step:
            pending = next(
                (cue for cue in self._contract.cues if cue.cue_id == self._pending_cue_id),
                None,
            )
            if identity is None or (
                pending is not None and identity.cue_id != pending.cue_id
            ) or (
                pending is None and identity.cue_id is not None
            ):
                return self._reject("ILLEGAL_CUE")
            expected_effect = pending.effect if pending is not None else None
            if effect != expected_effect or effect != self._pending_effect:
                return self._reject("ILLEGAL_EFFECT")
            if next_step_key is not None:
                return self._reject("MODEL_STEP_SELECTION_FORBIDDEN")
            self._continue_applied = True
            self._consume(identity)
            self._state = ConversationState.COMPLETE
            return self._decision(intent="complete_lesson")
        pending = next(
            (cue for cue in self._contract.cues if cue.cue_id == self._pending_cue_id),
            None,
        )
        if identity is None or pending is None or identity.cue_id != pending.cue_id:
            return self._reject("ILLEGAL_CUE")
        if effect != pending.effect or effect != self._pending_effect:
            return self._reject("ILLEGAL_EFFECT")
        if next_step_key is not None:
            return self._reject("MODEL_STEP_SELECTION_FORBIDDEN")
        assert identity is not None
        self._continue_applied = True
        self._consume(identity)
        self._state = ConversationState.COMPLETE
        return self._decision(intent="continue_lesson", cue_role="word_transition")

    def mark_mastered(self, identity: LessonToolIdentity | None) -> ConversationDecision:
        rejected = self._identity_rejection(identity)
        if rejected:
            return rejected
        return self._reject("DIRECT_MASTERY_FORBIDDEN")
