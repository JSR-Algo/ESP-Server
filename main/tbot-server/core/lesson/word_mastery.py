"""Privacy-safe evidence aggregate for one Course Mode target word."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class EvidenceLevel(str, Enum):
    NOT_STARTED = "NOT_STARTED"
    EXPOSED = "EXPOSED"
    UNDERSTOOD = "UNDERSTOOD"
    SUPPORTED_SPEECH = "SUPPORTED_SPEECH"
    INDEPENDENT_RECALL = "INDEPENDENT_RECALL"
    TRANSFERRED = "TRANSFERRED"
    MASTERED_TODAY = "MASTERED_TODAY"
    REVIEW_NEEDED = "REVIEW_NEEDED"


@dataclass(frozen=True)
class AnswerLeakage:
    last_full_model_at_ms: int | None = None
    target_text_visible: bool = False
    intervening_activity_count: int = 0
    robot_audio_contaminated: bool = False

    def independent_eligible(self, now_ms: int) -> bool:
        return (
            (
                self.last_full_model_at_ms is None
                or (
                    now_ms - self.last_full_model_at_ms >= 20_000
                    and self.intervening_activity_count >= 1
                )
            )
            and not self.target_text_visible
            and not self.robot_audio_contaminated
        )


@dataclass(frozen=True)
class EvidenceResult:
    accepted: bool
    level: EvidenceLevel
    review_needed: bool = False


class WordMastery:
    def __init__(self, target_id: str) -> None:
        self.target_id = target_id
        self.level = EvidenceLevel.NOT_STARTED
        self.answer_leakage = AnswerLeakage()
        self._consumed: set[str] = set()
        self._meaning = False
        self._independent = False
        self._transfer = False
        self._delayed = False
        self._misses_after_recall = 0

    def _consume(self, evidence_id: str) -> bool:
        if evidence_id in self._consumed:
            return False
        self._consumed.add(evidence_id)
        return True

    def _result(self, accepted: bool = True) -> EvidenceResult:
        return EvidenceResult(accepted, self.level, self._misses_after_recall > 0)

    def record_model(self, *, now_ms: int) -> None:
        self.answer_leakage = AnswerLeakage(now_ms, self.answer_leakage.target_text_visible, 0, False)
        if self.level is EvidenceLevel.NOT_STARTED:
            self.level = EvidenceLevel.EXPOSED

    def record_intervening_activity(self) -> None:
        leak = self.answer_leakage
        self.answer_leakage = AnswerLeakage(
            leak.last_full_model_at_ms, leak.target_text_visible, leak.intervening_activity_count + 1,
            leak.robot_audio_contaminated,
        )

    def set_target_text_visible(self, visible: bool) -> None:
        leak = self.answer_leakage
        self.answer_leakage = AnswerLeakage(leak.last_full_model_at_ms, visible, leak.intervening_activity_count, leak.robot_audio_contaminated)

    def set_robot_audio_contaminated(self, contaminated: bool) -> None:
        leak = self.answer_leakage
        self.answer_leakage = AnswerLeakage(leak.last_full_model_at_ms, leak.target_text_visible, leak.intervening_activity_count, contaminated)

    def record_meaning(self, *, evidence_id: str, activity_id: str, context_id: str) -> EvidenceResult:
        if not self._consume(evidence_id):
            return self._result(False)
        self._meaning = True
        if self.level in {EvidenceLevel.NOT_STARTED, EvidenceLevel.EXPOSED}:
            self.level = EvidenceLevel.UNDERSTOOD
        return self._result()

    def record_speech(
        self, *, evidence_id: str, activity_id: str, context_id: str, now_ms: int,
        semantic_class: str, speech_class: str, assessment_eligible: bool, confidence_band: str,
    ) -> EvidenceResult:
        if not self._consume(evidence_id):
            return self._result(False)
        correct = semantic_class == "target_en" and speech_class in {"exact", "near"}
        independent = (
            correct and assessment_eligible and confidence_band == "high"
            and self.answer_leakage.independent_eligible(now_ms)
        )
        if independent:
            self._independent = True
            self.level = EvidenceLevel.INDEPENDENT_RECALL
        elif correct and self.level.value not in {EvidenceLevel.INDEPENDENT_RECALL.value, EvidenceLevel.TRANSFERRED.value, EvidenceLevel.MASTERED_TODAY.value}:
            self.level = EvidenceLevel.SUPPORTED_SPEECH
        elif not correct and self._independent:
            self._misses_after_recall += 1
        return self._result()

    def record_transfer(self, *, evidence_id: str, activity_id: str, context_id: str) -> EvidenceResult:
        if not self._consume(evidence_id):
            return self._result(False)
        if self._independent:
            self._transfer = True
            if self.level is not EvidenceLevel.MASTERED_TODAY:
                self.level = EvidenceLevel.TRANSFERRED
        return self._result()

    def record_delayed_recall(
        self, *, evidence_id: str, activity_id: str, context_id: str, now_ms: int,
        assessment_eligible: bool, confidence_band: str, successful: bool = True,
    ) -> EvidenceResult:
        if not self._consume(evidence_id):
            return self._result(False)
        eligible = (
            successful and assessment_eligible and confidence_band == "high"
            and self.answer_leakage.independent_eligible(now_ms)
        )
        if self._meaning and self._independent and self._transfer and eligible:
            self._delayed = True
            self.level = EvidenceLevel.MASTERED_TODAY
        else:
            self._misses_after_recall += 1
        return self._result()

    def recommend_review(self) -> EvidenceResult:
        return EvidenceResult(True, self.level, True)

    def snapshot(self) -> dict[str, Any]:
        return {
            "targetId": self.target_id, "level": self.level.value,
            "answerLeakage": {
                "lastFullModelAtMs": self.answer_leakage.last_full_model_at_ms,
                "targetTextVisible": self.answer_leakage.target_text_visible,
                "interveningActivityCount": self.answer_leakage.intervening_activity_count,
                "robotAudioContaminated": self.answer_leakage.robot_audio_contaminated,
            },
            "consumedEvidenceIds": sorted(self._consumed), "meaning": self._meaning,
            "independent": self._independent, "transfer": self._transfer, "delayed": self._delayed,
            "missesAfterRecall": self._misses_after_recall,
        }

    @classmethod
    def restore(cls, snapshot: dict[str, Any]) -> "WordMastery":
        value = cls(snapshot["targetId"])
        value.level = EvidenceLevel(snapshot["level"])
        leak = snapshot["answerLeakage"]
        value.answer_leakage = AnswerLeakage(
            leak["lastFullModelAtMs"], leak["targetTextVisible"],
            leak["interveningActivityCount"], leak["robotAudioContaminated"],
        )
        value._consumed = set(snapshot["consumedEvidenceIds"])
        value._meaning = snapshot["meaning"]
        value._independent = snapshot["independent"]
        value._transfer = snapshot["transfer"]
        value._delayed = snapshot["delayed"]
        value._misses_after_recall = snapshot["missesAfterRecall"]
        return value
