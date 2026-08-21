"""Fail-closed validation for model-phrased, server-approved child responses."""

from __future__ import annotations

import re
from collections.abc import Mapping, Set
from dataclasses import dataclass
from typing import Any

from core.lesson.embodied_intent import EmbodiedIntent


_FIELDS = {
    "acknowledgment", "relation", "guidance", "invitation", "questionCount",
    "embodiedIntent", "targetFactsUsed", "praiseLevel", "safetyMode", "normalMiss",
}
_PROHIBITED = ("wrong", "incorrect", "easy", "try harder", "sai rồi", "dễ mà", "cố hơn")


class CourseResponsePlanError(ValueError):
    pass


@dataclass(frozen=True)
class CourseResponsePlan:
    acknowledgment: str
    relation: str
    guidance: str
    invitation: str
    question_count: int
    embodied_intent: EmbodiedIntent
    target_facts_used: tuple[str, ...]
    praise_level: str
    safety_mode: bool
    normal_miss: bool

    def contains_target_word(self, target_word: str) -> bool:
        child_facing_text = " ".join((
            self.acknowledgment, self.relation, self.guidance, self.invitation,
        )).casefold()
        pattern = rf"(?<!\w){re.escape(target_word.casefold())}(?!\w)"
        return re.search(pattern, child_facing_text) is not None

    @classmethod
    def from_mapping(cls, value: Any, *, approved_fact_codes: Set[str]) -> "CourseResponsePlan":
        if not isinstance(value, Mapping) or set(value) != _FIELDS:
            raise CourseResponsePlanError("INVALID_FIELDS")
        if type(value["questionCount"]) is not int or not 0 <= value["questionCount"] <= 1:
            raise CourseResponsePlanError("TOO_MANY_QUESTIONS")
        facts = value["targetFactsUsed"]
        if not isinstance(facts, list) or any(fact not in approved_fact_codes for fact in facts):
            raise CourseResponsePlanError("UNAPPROVED_FACT")
        text = " ".join(str(value[key]) for key in ("acknowledgment", "relation", "guidance", "invitation")).casefold()
        if any(token in text for token in _PROHIBITED):
            raise CourseResponsePlanError("PROHIBITED_WORDING")
        try:
            embodied = EmbodiedIntent(value["embodiedIntent"])
        except (TypeError, ValueError) as exc:
            raise CourseResponsePlanError("UNSUPPORTED_INTENT") from exc
        if value["praiseLevel"] == "mastery":
            raise CourseResponsePlanError("MASTERY_PRAISE_NOT_AUTHORIZED")
        if value["normalMiss"] and embodied in {EmbodiedIntent.COMFORT_CALM}:
            raise CourseResponsePlanError("DISAPPOINTED_MISS_FEEDBACK")
        if value["safetyMode"]:
            if embodied not in {EmbodiedIntent.COMFORT_CALM, EmbodiedIntent.PAUSE_CHOICE}:
                raise CourseResponsePlanError("UNSAFE_SAFETY_INTENT")
            if facts or any(token in text for token in ("cat", "ball", "tiếng anh")):
                raise CourseResponsePlanError("SAFETY_REDIRECTION")
        return cls(
            *(str(value[key]) for key in ("acknowledgment", "relation", "guidance", "invitation")),
            question_count=value["questionCount"], embodied_intent=embodied,
            target_facts_used=tuple(facts), praise_level=str(value["praiseLevel"]),
            safety_mode=bool(value["safetyMode"]), normal_miss=bool(value["normalMiss"]),
        )
