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
_UNSUPPORTED_MASTERY_CLAIM_RE = re.compile(
    r"(?<!\w)(mastered|mastery|đã thuộc|thuộc bài)(?!\w)", re.IGNORECASE,
)
_QUESTION_LEAD_RE = re.compile(
    r"(?:^|[.!]\s+)(?:can|could|would|will|do|did|are|is|what|where|who|why|how|which|when)\b"
    r"|(?:^|[.!]\s+)con\s+(?:có|muốn|thấy|nghĩ)\b",
    re.IGNORECASE,
)
_FACT_CLAUSE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_FACT_GUIDANCE_LEAD_RE = re.compile(
    r"^(?:look|find|point|say|repeat|listen|show|nhìn|chỉ|nói|lặp|nghe)\b",
    re.IGNORECASE,
)


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
    def from_mapping(
        cls, value: Any, *, approved_fact_codes: Set[str],
        safety_forbidden_terms: Set[str] = frozenset(),
        approved_fact_terms: Set[str] = frozenset(),
    ) -> "CourseResponsePlan":
        if not isinstance(value, Mapping) or set(value) != _FIELDS:
            raise CourseResponsePlanError("INVALID_FIELDS")
        if type(value["questionCount"]) is not int or not 0 <= value["questionCount"] <= 1:
            raise CourseResponsePlanError("TOO_MANY_QUESTIONS")
        facts = value["targetFactsUsed"]
        if not isinstance(facts, list) or any(fact not in approved_fact_codes for fact in facts):
            raise CourseResponsePlanError("UNAPPROVED_FACT")
        text_fields = tuple(
            value[key] for key in ("acknowledgment", "relation", "guidance", "invitation")
        )
        if any(not isinstance(field, str) or len(field) > 160 for field in text_fields):
            raise CourseResponsePlanError("INVALID_RESPONSE_TEXT")
        if not any(field.strip() for field in text_fields):
            raise CourseResponsePlanError("EMPTY_RESPONSE_TEXT")
        text = " ".join(text_fields).casefold()
        if len(text) > 320:
            raise CourseResponsePlanError("RESPONSE_TEXT_TOO_LONG")
        invitation = value["invitation"].strip()
        non_invitation = " ".join(text_fields[:3]).strip()
        if value["questionCount"] == 0 and invitation:
            raise CourseResponsePlanError("QUESTION_COUNT_MISMATCH")
        if value["questionCount"] == 1 and (
            not invitation.endswith("?")
            or invitation.count("?") != 1
            or any(mark in invitation[:-1] for mark in ".!")
        ):
            raise CourseResponsePlanError("QUESTION_COUNT_MISMATCH")
        if "?" in non_invitation or _QUESTION_LEAD_RE.search(non_invitation):
            raise CourseResponsePlanError("QUESTION_COUNT_MISMATCH")
        if any(token in text for token in _PROHIBITED):
            raise CourseResponsePlanError("PROHIBITED_WORDING")
        if _UNSUPPORTED_MASTERY_CLAIM_RE.search(text):
            raise CourseResponsePlanError("MASTERY_PRAISE_NOT_AUTHORIZED")
        cls._validate_fact_wording(text_fields, approved_fact_terms)
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
            forbidden_terms = {"cat", "ball", "tiếng anh", *safety_forbidden_terms}
            if facts or any(
                re.search(rf"(?<!\w){re.escape(term.casefold())}(?!\w)", text)
                for term in forbidden_terms
                if isinstance(term, str) and term.strip()
            ):
                raise CourseResponsePlanError("SAFETY_REDIRECTION")
        return cls(
            *text_fields,
            question_count=value["questionCount"], embodied_intent=embodied,
            target_facts_used=tuple(facts), praise_level=str(value["praiseLevel"]),
            safety_mode=bool(value["safetyMode"]), normal_miss=bool(value["normalMiss"]),
        )

    @staticmethod
    def _validate_fact_wording(text_fields: tuple[Any, ...], approved_fact_terms: Set[str]) -> None:
        terms = tuple(
            term.casefold().strip() for term in approved_fact_terms
            if isinstance(term, str) and term.strip()
        )
        if not terms:
            return
        term_patterns = []
        for term in terms:
            suffix = "s?" if term.isascii() and term.isalpha() else ""
            term_patterns.append(rf"(?<!\w){re.escape(term)}{suffix}(?!\w)")
        target_re = re.compile("|".join(term_patterns), re.IGNORECASE)
        identity_re = re.compile(
            rf"^(?:(?:this|that|it|the answer|here|đây|đó)\s+"
            rf"(?:is|means|là)\s+(?:(?:a|an|the|một|con|quả)\s+)?(?:{target_re.pattern})"
            rf"|(?:{target_re.pattern})\s+(?:means|là)\s+(?:{target_re.pattern}))$",
            re.IGNORECASE,
        )
        for field in text_fields:
            for raw_clause in _FACT_CLAUSE_SPLIT_RE.split(field.strip()):
                clause = raw_clause.strip()
                if not clause or target_re.search(clause) is None:
                    continue
                normalized = clause.rstrip(".!?").strip()
                if (
                    clause.endswith("?")
                    or _FACT_GUIDANCE_LEAD_RE.match(normalized)
                    or identity_re.fullmatch(normalized)
                ):
                    continue
                raise CourseResponsePlanError("UNAPPROVED_FACT_WORDING")

    def response_text(self) -> str:
        return " ".join(filter(None, (
            self.acknowledgment.strip(), self.relation.strip(),
            self.guidance.strip(), self.invitation.strip(),
        )))
