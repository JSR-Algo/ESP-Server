"""Pure server-authoritative Course Mode session orchestrator."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, cast

from core.lesson.course_mode_contract import CourseModeContract
from core.lesson.embodied_intent import EmbodiedIntent
from core.lesson.interaction_templates import curriculum_outcome_name
from core.lesson.word_mastery import EvidenceLevel, WordMastery


CONTEXT_BRANCH_TYPES = (
    "RELATED_STORY",
    "UNRELATED_CURIOSITY",
    "EMOTIONAL_SHARE",
    "HELP_REQUEST",
    "PLAY_REQUEST",
    "REFUSAL",
    "SAFETY_DISCLOSURE",
)
CONTEXT_BRIDGE_INTENTS = (
    "white_cat_visual",
    "pet_sound_clue",
    "resume_active_word_visual",
    "resume_active_word_choice",
)
CONTEXT_CHILD_DETAIL_CODES = (
    "grandmother_pet",
    "related_pet",
    "child_choice",
    "current_visual",
    "earlier_session_detail",
    "no_personal_detail",
)


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
    activity_id: str | None = None
    visual_state: str = "listen"
    replay_entrance: bool = False
    attempt: int = 0
    visual_focus_region: str | None = None


class CourseOrchestrator:
    def __init__(
        self, contract: CourseModeContract, *, started_at_ms: int, soft_deadline_ms: int,
        lesson_session_id: str | None = None,
    ) -> None:
        self.contract = contract
        self.lesson_session_id = lesson_session_id or contract.lesson_session_id
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
        self.active_activity_id = contract.activities[0].activity_id
        self._activity_attempts: dict[str, int] = {}
        self._evidence_state: dict[str, str] = {}
        self._regulation_state: str | None = None

    @property
    def active_mastery(self) -> WordMastery:
        return self._mastery[self.active_target_id]

    def _decision(
        self, action: str, *, accepted: bool = True, acknowledgment: str = "acknowledge_child",
        teaching: str | None = None, question: str | None = None,
        embodied: EmbodiedIntent = EmbodiedIntent.INVITE_CHILD, may_model: bool = False,
        evidence: dict[str, object] | None = None, branch_id: str | None = None,
        activity_id: str | None = None, visual_state: str = "listen",
        replay_entrance: bool = False, attempt: int = 0,
        visual_focus_region: str | None = None,
    ) -> CourseDecision:
        self._decision_sequence += 1
        return CourseDecision(
            f"course-decision-{self._decision_sequence}", accepted, self.session_state, action,
            acknowledgment, teaching, question, embodied, may_model, evidence, branch_id,
            activity_id, visual_state, replay_entrance, attempt, visual_focus_region,
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
        if observation.safety_class != "normal":
            self.session_state = SessionState.SAFETY_PAUSED
            self._active_branch_id = None
            return self._decision(
                "PAUSE_FOR_SAFETY", acknowledgment="acknowledge_safety",
                embodied=EmbodiedIntent.COMFORT_CALM,
            )
        if self.session_state is SessionState.REGULATION_BREAK:
            if observation.intent == "resume":
                self.session_state = SessionState.WORD_ACTIVE
                return self._decision(
                    "RESUME_AFTER_REGULATION", acknowledgment="honor_resume_choice",
                    question="invite_observation", embodied=EmbodiedIntent.THINK_CURIOUS,
                )
            if observation.intent == "stop":
                self.session_state = SessionState.CLOSING
                return self._decision(
                    "CLOSE_BY_CHILD_CHOICE", acknowledgment="honor_stop_choice",
                    embodied=EmbodiedIntent.GOODBYE_SMALL,
                )
            return self.hold_protected_pause()
        if self.session_state is SessionState.SAFETY_PAUSED:
            if observation.intent == "stop":
                self.session_state = SessionState.CLOSING
                return self._decision(
                    "CLOSE_BY_SAFETY_CHOICE", acknowledgment="honor_safety_stop",
                    embodied=EmbodiedIntent.GOODBYE_SMALL,
                )
            return self.hold_protected_pause()
        if observation.now_ms - self.started_at_ms >= self.soft_deadline_ms:
            self.session_state = SessionState.CLOSING
            return self._decision(
                "CLOSE_AT_DEADLINE" if self.contract.is_curriculum else "CLOSE_WITHOUT_SECOND_WORD",
                embodied=EmbodiedIntent.GOODBYE_SMALL, activity_id=self.active_activity_id,
                visual_state="completion",
            )
        if observation.intent in {"emotional_share", "refusal", "fatigue"}:
            self.session_state = SessionState.REGULATION_BREAK
            self._regulation_state = observation.intent
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
                activity_id=self.active_activity_id,
            )
        try:
            activity = self.contract.activity(observation.activity_id)
        except KeyError:
            return self._decision("INVALID_ACTIVITY_IGNORED", accepted=False, acknowledgment="retain_authoritative_state")
        if activity.target_id != self.active_target_id or activity.context_id != observation.context_id:
            if self.contract.is_curriculum and observation.activity_id == self.active_activity_id and activity.context_id == observation.context_id:
                return self._observe_curriculum(activity, observation)
            return self._decision("INVALID_ACTIVITY_IGNORED", accepted=False, acknowledgment="retain_authoritative_state")
        if self.contract.is_curriculum:
            return self._observe_curriculum(activity, observation)
        assessment_stage = activity.stage in {"RECALL", "TRANSFER", "DELAYED_RECALL"}
        if (
            observation.confidence_band != "high"
            or observation.robot_audio_contaminated
            or (assessment_stage and not observation.assessment_eligible)
        ):
            self.session_state = SessionState.TECHNICAL_RECOVERY
            return self._decision("OWN_ASR_UNCERTAINTY", acknowledgment="robot_ears_unclear", question="invite_retry", embodied=EmbodiedIntent.LISTEN_STILL)
        mastery = self.active_mastery
        mastery.set_target_text_visible(observation.target_text_visible)
        mastery.set_robot_audio_contaminated(observation.robot_audio_contaminated)
        evidence = None
        before_level = mastery.level
        if activity.stage == "UNDERSTAND" and observation.semantic_class == "meaning_vi":
            result = mastery.record_meaning(
                evidence_id=observation.observation_id, activity_id=observation.activity_id, context_id=observation.context_id,
            )
            self.word_state = WordState.IMITATE
        elif activity.stage == "RECALL":
            result = mastery.record_speech(
                evidence_id=observation.observation_id, activity_id=observation.activity_id,
                context_id=observation.context_id, now_ms=observation.now_ms,
                semantic_class=observation.semantic_class, speech_class=observation.speech_class,
                assessment_eligible=observation.assessment_eligible, confidence_band=observation.confidence_band,
            )
            if result.level is not EvidenceLevel.INDEPENDENT_RECALL:
                self.session_state = SessionState.WORD_ACTIVE
                return self._decision(
                    "MODEL_AND_SUPPORT", teaching="model_target_once",
                    question="invite_supported_speech", embodied=EmbodiedIntent.MODEL_WORD,
                    may_model=True,
                    evidence=(
                        {
                            "targetId": self.active_target_id, "evidenceLevel": result.level.value,
                            "activityId": observation.activity_id, "contextId": observation.context_id,
                            "assessmentConfidenceBand": observation.confidence_band,
                            "reviewNeeded": result.review_needed,
                        }
                        if result.accepted and result.level is not before_level else None
                    ),
                )
        elif (
            activity.stage == "TRANSFER"
            and observation.semantic_class == "target_en"
            and observation.speech_class in {"exact", "near"}
            and mastery.answer_leakage.independent_eligible(observation.now_ms)
        ):
            result = mastery.record_transfer(
                evidence_id=observation.observation_id, activity_id=observation.activity_id,
                context_id=observation.context_id,
            )
            self.word_state = WordState.DELAYED_RECALL
        elif activity.stage == "DELAYED_RECALL":
            if (
                self.word_state is not WordState.DELAYED_RECALL
                or mastery.level is not EvidenceLevel.TRANSFERRED
            ):
                self.session_state = SessionState.WORD_ACTIVE
                return self._decision(
                    "INVALID_ACTIVITY_IGNORED", accepted=False,
                    acknowledgment="retain_authoritative_state",
                )
            result = mastery.record_delayed_recall(
                evidence_id=observation.observation_id, activity_id=observation.activity_id,
                context_id=observation.context_id, now_ms=observation.now_ms,
                assessment_eligible=observation.assessment_eligible,
                confidence_band=observation.confidence_band,
                successful=(
                    observation.semantic_class == "target_en"
                    and observation.speech_class in {"exact", "near"}
                ),
            )
            self.word_state = WordState.DONE_FOR_SESSION
        else:
            self.session_state = SessionState.WORD_ACTIVE
            return self._decision("ACKNOWLEDGE_GUIDE_INVITE", question="one_next_question")
        if result.accepted and (result.level is not before_level or result.review_needed):
            evidence = {
                "targetId": self.active_target_id, "evidenceLevel": result.level.value,
                "activityId": observation.activity_id, "contextId": observation.context_id,
                "assessmentConfidenceBand": observation.confidence_band,
                "reviewNeeded": result.review_needed,
            }
        self.session_state = SessionState.WORD_ACTIVE
        return self._decision("ACKNOWLEDGE_GUIDE_INVITE", evidence=evidence, question="one_next_question")

    def _observe_curriculum(self, activity: Any, observation: ChildObservation) -> CourseDecision:
        if observation.activity_id != self.active_activity_id:
            return self._decision("INVALID_ACTIVITY_IGNORED", accepted=False, acknowledgment="retain_authoritative_state", activity_id=self.active_activity_id)
        if (
            observation.confidence_band != "high"
            or observation.robot_audio_contaminated
            or (activity.stage in {"RECALL", "TRANSFER", "DELAYED_RECALL"} and not observation.assessment_eligible)
        ):
            self.session_state = SessionState.TECHNICAL_RECOVERY
            return self._decision("OWN_ASR_UNCERTAINTY", acknowledgment="robot_ears_unclear", question="invite_retry", embodied=EmbodiedIntent.LISTEN_STILL, activity_id=activity.activity_id, visual_state="retry", attempt=self._activity_attempts.get(activity.activity_id, 0), visual_focus_region=activity.visual_focus_region)
        outcome_name = curriculum_outcome_name(semantic_class=observation.semantic_class, speech_class=observation.speech_class, language=observation.language, intent=observation.intent)
        outcome = activity.outcomes.get(outcome_name)
        if outcome is None and outcome_name in {"correct", "near", "vietnamese"}:
            outcome = activity.outcomes.get("continue") or activity.outcomes.get("finished")
        if outcome is None and outcome_name in {"incorrect", "silence"}:
            outcome = activity.outcomes.get("help")
        if outcome is None:
            return self._decision("ACKNOWLEDGE_GUIDE_INVITE", activity_id=activity.activity_id, acknowledgment="acknowledge_child", question="one_next_question")
        action = outcome["action"]
        attempt = self._activity_attempts.get(activity.activity_id, 0)
        acknowledgment = "acknowledge_vietnamese_meaning" if outcome_name == "vietnamese" else "acknowledge_child"
        if action in {"retry", "support"}:
            attempt += 1
            self._activity_attempts[activity.activity_id] = attempt
            self.active_activity_id = cast(str, outcome["activityId"])
            help_requested = outcome_name == "help"
            return self._decision(
                "MODEL_AND_SUPPORT" if help_requested else ("OFFER_CHOICE_OR_RETRY" if outcome_name == "silence" else "SUPPORT_WITH_CLUE"),
                acknowledgment="acknowledge_help" if help_requested else acknowledgment,
                teaching="model_target_once" if help_requested else "authored_clue",
                question="invite_supported_speech" if help_requested else "offer_choice_or_retry",
                embodied=EmbodiedIntent.MODEL_WORD if help_requested else EmbodiedIntent.TRY_DIFFERENT_WAY,
                may_model=help_requested, activity_id=self.active_activity_id,
                visual_state="retry" if action == "retry" else "incorrect", attempt=attempt,
                visual_focus_region=activity.visual_focus_region,
            )
        if action == "pause":
            self.session_state = SessionState.REGULATION_BREAK
            self._regulation_state = outcome_name
            return self._decision("RESPOND_WITHOUT_REDIRECT", acknowledgment=f"acknowledge_{outcome_name}", question="offer_pause_choice", embodied=EmbodiedIntent.PAUSE_CHOICE, activity_id=activity.activity_id, attempt=attempt, visual_focus_region=activity.visual_focus_region)
        if action in {"close", "complete"}:
            self.session_state = SessionState.COMPLETE if action == "complete" else SessionState.CLOSING
            return self._decision("COMPLETE_COURSE" if action == "complete" else "CLOSE_BY_OUTCOME", acknowledgment=acknowledgment, embodied=EmbodiedIntent.GOODBYE_SMALL, activity_id=activity.activity_id, visual_state="completion", attempt=attempt, visual_focus_region=activity.visual_focus_region)
        index = next(i for i, candidate in enumerate(self.contract.activities) if candidate.activity_id == activity.activity_id)
        if index + 1 >= len(self.contract.activities):
            self.session_state = SessionState.COMPLETE
            return self._decision("COMPLETE_COURSE", embodied=EmbodiedIntent.GOODBYE_SMALL, activity_id=activity.activity_id, visual_state="completion")
        for target_id in activity.target_ids:
            self._evidence_state[target_id] = activity.evidence_name
        self.active_activity_id = self.contract.activities[index + 1].activity_id
        self.session_state = SessionState.WORD_ACTIVE
        return self._decision("ADVANCE_ACTIVITY", acknowledgment=acknowledgment, embodied=EmbodiedIntent(activity.embodied_intent), activity_id=self.active_activity_id, visual_state="nearMiss" if outcome_name == "near" else "correct", attempt=attempt, visual_focus_region=activity.visual_focus_region)

    def open_context_branch(self, *, observation_id: str, turn_sequence_id: int, branch_type: str) -> CourseDecision:
        if observation_id in self._consumed_observations:
            return self._decision("DUPLICATE_IGNORED", accepted=False, acknowledgment="retain_authoritative_state")
        if branch_type not in CONTEXT_BRANCH_TYPES:
            return self._decision("INVALID_CONTEXT_BRANCH_IGNORED", accepted=False)
        self._consumed_observations.add(observation_id)
        if branch_type == "SAFETY_DISCLOSURE":
            self.session_state = SessionState.SAFETY_PAUSED
            self._active_branch_id = None
            return self._decision(
                "PAUSE_FOR_SAFETY", acknowledgment="acknowledge_safety",
                embodied=EmbodiedIntent.COMFORT_CALM,
            )
        if branch_type in {"EMOTIONAL_SHARE", "REFUSAL"}:
            self.session_state = SessionState.REGULATION_BREAK
            self._active_branch_id = None
            return self._decision(
                "RESPOND_WITHOUT_REDIRECT",
                acknowledgment=f"acknowledge_{branch_type.casefold()}",
                question="offer_pause_choice",
                embodied=(
                    EmbodiedIntent.COMFORT_CALM
                    if branch_type == "EMOTIONAL_SHARE"
                    else EmbodiedIntent.PAUSE_CHOICE
                ),
            )
        self.session_state = SessionState.CONTEXT_BRANCH
        self._active_branch_id = f"branch-{turn_sequence_id}-{observation_id}"
        return self._decision(
            "OPEN_CONTEXT_BRANCH", acknowledgment=f"acknowledge_{branch_type.casefold()}",
            question="invite_one_story_detail", embodied=EmbodiedIntent.ACKNOWLEDGE_STORY,
            branch_id=self._active_branch_id,
        )

    def close_context_branch(self, *, branch_id: str | None, bridge_intent: str, child_detail_code: str) -> CourseDecision:
        if branch_id != self._active_branch_id:
            return self._decision("STALE_BRANCH_IGNORED", accepted=False)
        if (
            bridge_intent not in CONTEXT_BRIDGE_INTENTS
            or child_detail_code not in CONTEXT_CHILD_DETAIL_CODES
        ):
            return self._decision("INVALID_CONTEXT_BRIDGE_IGNORED", accepted=False)
        self._active_branch_id = None
        self.session_state = SessionState.WORD_ACTIVE
        return self._decision(
            "RETURN_THROUGH_AUTHORED_BRIDGE", acknowledgment=f"acknowledge_{child_detail_code}",
            teaching=f"bridge_{bridge_intent}", question="resume_active_word", embodied=EmbodiedIntent.ACKNOWLEDGE_STORY,
        )

    def continue_word(self, *, now_ms: int) -> CourseDecision:
        if now_ms - self.started_at_ms >= self.soft_deadline_ms:
            self.session_state = SessionState.CLOSING
            return self._decision(
                "CLOSE_WITHOUT_SECOND_WORD", embodied=EmbodiedIntent.GOODBYE_SMALL,
            )
        self.session_state = SessionState.WORD_ACTIVE
        return self._decision(
            "PRESENT_INTERVENING_ACTIVITY", teaching="authored_non_answer_activity",
            question="invite_observation", embodied=EmbodiedIntent.THINK_CURIOUS,
        )

    def hold_protected_pause(self) -> CourseDecision:
        if self.session_state is SessionState.SAFETY_PAUSED:
            return self._decision(
                "HOLD_PROTECTED_PAUSE", acknowledgment="acknowledge_safety",
                question="await_safety_clearance", embodied=EmbodiedIntent.COMFORT_CALM,
            )
        if self.session_state is SessionState.REGULATION_BREAK:
            return self._decision(
                "HOLD_PROTECTED_PAUSE", acknowledgment="honor_pause_choice",
                question="offer_pause_choice", embodied=EmbodiedIntent.PAUSE_CHOICE,
            )
        raise ValueError("protected pause can only be held from a protected state")

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
            return self._decision(
                "START_OPTIONAL_SECONDARY", teaching="secondary_curiosity",
                question="invite_secondary", may_model=True,
            )
        self.session_state = SessionState.CLOSING
        action = (
            "CLOSE_AFTER_OPTIONAL_SECONDARY"
            if self.contract.secondary is not None
            and self.active_target_id == self.contract.secondary.target_id
            else "CLOSE_AFTER_PRIMARY"
        )
        return self._decision(action, embodied=EmbodiedIntent.GOODBYE_SMALL)

    def snapshot(self) -> dict[str, Any]:
        return {
            "lessonSessionId": self.lesson_session_id, "sessionState": self.session_state.value,
            "startedAtMs": self.started_at_ms, "softDeadlineMs": self.soft_deadline_ms,
            "wordState": self.word_state.value, "activeTargetId": self.active_target_id,
            "openingStep": self._opening_step, "decisionSequence": self._decision_sequence,
            "consumedObservationIds": sorted(self._consumed_observations),
            "mastery": {key: value.snapshot() for key, value in self._mastery.items()},
            "activeBranchId": self._active_branch_id,
            "activeActivityId": self.active_activity_id,
            "activityAttempts": dict(self._activity_attempts),
            "evidenceState": dict(self._evidence_state),
            "regulationState": self._regulation_state,
        }

    @classmethod
    def restore(cls, contract: CourseModeContract, snapshot: dict[str, Any]) -> "CourseOrchestrator":
        value = cls(
            contract, started_at_ms=snapshot["startedAtMs"],
            soft_deadline_ms=snapshot["softDeadlineMs"],
            lesson_session_id=snapshot["lessonSessionId"],
        )
        value.session_state = SessionState(snapshot["sessionState"])
        value.word_state = WordState(snapshot["wordState"])
        value.active_target_id = snapshot["activeTargetId"]
        value._opening_step = snapshot["openingStep"]
        value._decision_sequence = snapshot["decisionSequence"]
        value._consumed_observations = set(snapshot["consumedObservationIds"])
        value._mastery = {key: WordMastery.restore(item) for key, item in snapshot["mastery"].items()}
        value._active_branch_id = snapshot["activeBranchId"]
        value.active_activity_id = snapshot.get("activeActivityId", contract.activities[0].activity_id)
        value._activity_attempts = dict(snapshot.get("activityAttempts", {}))
        value._evidence_state = dict(snapshot.get("evidenceState", {}))
        value._regulation_state = snapshot.get("regulationState")
        value.pending_effects = ()
        return value
