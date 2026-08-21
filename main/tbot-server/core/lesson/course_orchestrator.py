"""Pure server-authoritative Course Mode session orchestrator."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from core.lesson.course_mode_contract import CourseModeContract
from core.lesson.embodied_intent import EmbodiedIntent
from core.lesson.word_mastery import EvidenceLevel, WordMastery


class SessionState(str, Enum):
    PREPARING = "PREPARING"
    OPENING = "OPENING"
    WORD_ACTIVE = "WORD_ACTIVE"
    CONTEXT_BRANCH = "CONTEXT_BRANCH"
    REGULATION_BREAK = "REGULATION_BREAK"
    DELAYED_RECALL = "DELAYED_RECALL"
    SAFETY_PAUSED = "SAFETY_PAUSED"
    TECHNICAL_RECOVERY = "TECHNICAL_RECOVERY"
    CLOSING = "CLOSING"
    COMPLETE = "COMPLETE"


class WordState(str, Enum):
    DISCOVER = "DISCOVER"
    UNDERSTAND = "UNDERSTAND"
    IMITATE = "IMITATE"
    RECALL = "RECALL"
    USE = "USE"
    DELAYED_RECALL = "DELAYED_RECALL"
    DONE_FOR_SESSION = "DONE_FOR_SESSION"


@dataclass(frozen=True)
class ChildObservation:
    observation_id: str
    turn_sequence_id: int
    semantic_class: str
    speech_class: str
    language: str
    intent: str
    engagement: str
    safety_class: str
    assessment_eligible: bool
    confidence_band: str
    activity_id: str
    context_id: str
    now_ms: int
    robot_audio_contaminated: bool
    target_text_visible: bool


@dataclass(frozen=True)
class CourseDecision:
    decision_id: str
    accepted: bool
    next_state: SessionState
    action: str
    acknowledgment_intent: str
    teaching_intent: str | None
    question_intent: str | None
    embodied_intent: EmbodiedIntent
    may_model_target: bool
    evidence_event: dict[str, object] | None
    branch_id: str | None = None


class CourseOrchestrator:
    def __init__(self, contract: CourseModeContract, *, started_at_ms: int, soft_deadline_ms: int) -> None:
        self.contract = contract
        self.started_at_ms = started_at_ms
        self.soft_deadline_ms = soft_deadline_ms
        self.session_state = SessionState.PREPARING
        self.word_state = WordState.DISCOVER
        self.active_target_id = contract.primary.target_id
        self._mastery = {target.target_id: WordMastery(target.target_id) for target in (contract.primary, contract.secondary) if target}
        self._opening_step = 0
        self._decision_sequence = 0
        self._consumed_observations: set[str] = set()
        self._active_branch_id: str | None = None
        self.pending_effects: tuple[str, ...] = ()

    @property
    def active_mastery(self) -> WordMastery:
        return self._mastery[self.active_target_id]

    def _decision(
        self, action: str, *, accepted: bool = True, acknowledgment: str = "acknowledge_child",
        teaching: str | None = None, question: str | None = None,
        embodied: EmbodiedIntent = EmbodiedIntent.INVITE_CHILD, may_model: bool = False,
        evidence: dict[str, object] | None = None, branch_id: str | None = None,
    ) -> CourseDecision:
        self._decision_sequence += 1
        return CourseDecision(
            f"course-decision-{self._decision_sequence}", accepted, self.session_state, action,
            acknowledgment, teaching, question, embodied, may_model, evidence, branch_id,
        )

    def begin(self) -> CourseDecision:
        self.session_state = SessionState.OPENING
        self._opening_step = 1
        return self._decision("GREET_AND_CHECK_IN", embodied=EmbodiedIntent.GREET_SMALL, question="check_in")

    def continue_opening(self) -> CourseDecision:
        if self._opening_step == 1:
            self._opening_step = 2
            return self._decision("ACKNOWLEDGE_AND_BUILD_CURIOSITY", teaching="curiosity_hook", question="invite_curiosity")
        self._opening_step = 3
        self.session_state = SessionState.WORD_ACTIVE
        return self._decision("CLUE_AND_ELICIT", teaching="authored_clue", question="elicit_from_clue", embodied=EmbodiedIntent.PRESENT_CENTER)

    def observe(self, observation: ChildObservation) -> CourseDecision:
        if observation.observation_id in self._consumed_observations:
            return self._decision("DUPLICATE_IGNORED", accepted=False, acknowledgment="retain_authoritative_state")
        self._consumed_observations.add(observation.observation_id)
        if observation.now_ms - self.started_at_ms >= self.soft_deadline_ms:
            self.session_state = SessionState.CLOSING
            return self._decision("CLOSE_WITHOUT_SECOND_WORD", embodied=EmbodiedIntent.GOODBYE_SMALL)
        if observation.safety_class != "normal":
            self.session_state = SessionState.SAFETY_PAUSED
            return self._decision("PAUSE_FOR_SAFETY", acknowledgment="acknowledge_safety", embodied=EmbodiedIntent.COMFORT_CALM)
        if observation.intent in {"emotional_share", "refusal", "fatigue"}:
            self.session_state = SessionState.REGULATION_BREAK
            embodied = EmbodiedIntent.COMFORT_CALM if observation.intent == "emotional_share" else EmbodiedIntent.PAUSE_CHOICE
            return self._decision("RESPOND_WITHOUT_REDIRECT", acknowledgment=f"acknowledge_{observation.intent}", question="offer_pause_choice", embodied=embodied)
        if observation.intent == "story" or observation.semantic_class in {"related", "unrelated"}:
            self.session_state = SessionState.CONTEXT_BRANCH
            self._active_branch_id = f"branch-{observation.turn_sequence_id}-{observation.observation_id}"
            related = observation.semantic_class == "related"
            return self._decision(
                "OPEN_CONTEXT_BRANCH", acknowledgment="acknowledge_related_story" if related else "answer_unrelated_curiosity",
                question="invite_one_story_detail" if related else "offer_resume_choice",
                embodied=EmbodiedIntent.ACKNOWLEDGE_STORY, branch_id=self._active_branch_id,
            )
        if observation.confidence_band != "high" or not observation.assessment_eligible or observation.robot_audio_contaminated:
            self.session_state = SessionState.TECHNICAL_RECOVERY
            return self._decision("OWN_ASR_UNCERTAINTY", acknowledgment="robot_ears_unclear", question="invite_retry", embodied=EmbodiedIntent.LISTEN_STILL)
        mastery = self.active_mastery
        mastery.set_target_text_visible(observation.target_text_visible)
        mastery.set_robot_audio_contaminated(observation.robot_audio_contaminated)
        evidence = None
        if observation.semantic_class == "meaning_vi":
            result = mastery.record_meaning(
                evidence_id=observation.observation_id, activity_id=observation.activity_id, context_id=observation.context_id,
            )
            self.word_state = WordState.IMITATE
        else:
            result = mastery.record_speech(
                evidence_id=observation.observation_id, activity_id=observation.activity_id,
                context_id=observation.context_id, now_ms=observation.now_ms,
                semantic_class=observation.semantic_class, speech_class=observation.speech_class,
                assessment_eligible=observation.assessment_eligible, confidence_band=observation.confidence_band,
            )
        if result.accepted and result.level is not EvidenceLevel.NOT_STARTED:
            evidence = {
                "targetId": self.active_target_id, "evidenceLevel": result.level.value,
                "activityId": observation.activity_id, "contextId": observation.context_id,
            }
        self.session_state = SessionState.WORD_ACTIVE
        return self._decision("ACKNOWLEDGE_GUIDE_INVITE", evidence=evidence, question="one_next_question")

    def close_context_branch(self, *, branch_id: str | None, bridge_intent: str, child_detail_code: str) -> CourseDecision:
        if branch_id != self._active_branch_id:
            return self._decision("STALE_BRANCH_IGNORED", accepted=False)
        self._active_branch_id = None
        self.session_state = SessionState.WORD_ACTIVE
        return self._decision(
            "RETURN_THROUGH_AUTHORED_BRIDGE", acknowledgment=f"acknowledge_{child_detail_code}",
            teaching=f"bridge_{bridge_intent}", question="resume_active_word", embodied=EmbodiedIntent.ACKNOWLEDGE_STORY,
        )

    def maybe_advance_target(self, *, now_ms: int) -> CourseDecision:
        if (
            self.contract.secondary is not None
            and self.active_target_id == self.contract.primary.target_id
            and self.active_mastery.level is EvidenceLevel.MASTERED_TODAY
            and now_ms - self.started_at_ms < self.soft_deadline_ms - 120_000
        ):
            self.active_target_id = self.contract.secondary.target_id
            self.word_state = WordState.DISCOVER
            self.session_state = SessionState.WORD_ACTIVE
            return self._decision("START_OPTIONAL_SECONDARY", teaching="secondary_curiosity", question="invite_secondary")
        self.session_state = SessionState.CLOSING
        return self._decision("CLOSE_AFTER_PRIMARY", embodied=EmbodiedIntent.GOODBYE_SMALL)

    def snapshot(self) -> dict[str, Any]:
        return {
            "lessonSessionId": self.contract.lesson_session_id, "sessionState": self.session_state.value,
            "wordState": self.word_state.value, "activeTargetId": self.active_target_id,
            "openingStep": self._opening_step, "decisionSequence": self._decision_sequence,
            "consumedObservationIds": sorted(self._consumed_observations),
            "mastery": {key: value.snapshot() for key, value in self._mastery.items()},
            "activeBranchId": self._active_branch_id,
        }

    @classmethod
    def restore(cls, contract: CourseModeContract, snapshot: dict[str, Any]) -> "CourseOrchestrator":
        value = cls(contract, started_at_ms=0, soft_deadline_ms=540_000)
        value.session_state = SessionState(snapshot["sessionState"])
        value.word_state = WordState(snapshot["wordState"])
        value.active_target_id = snapshot["activeTargetId"]
        value._opening_step = snapshot["openingStep"]
        value._decision_sequence = snapshot["decisionSequence"]
        value._consumed_observations = set(snapshot["consumedObservationIds"])
        value._mastery = {key: WordMastery.restore(item) for key, item in snapshot["mastery"].items()}
        value._active_branch_id = snapshot["activeBranchId"]
        value.pending_effects = ()
        return value
