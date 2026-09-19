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
_SAFETY_LANGUAGE_RE = re.compile(
    r"^(?:"
    r"(?:robot|i|mình)\s+(?:is here|hear(?:d)? you|am listening|đang nghe(?: đây)?|nghe con)"
    r"|(?:we|mình|chúng mình)\s+(?:can\s+)?(?:pause|stop|stay here|tạm dừng|dừng|ở yên)"
    r"|(?:do you want (?:robot )?to |con muốn robot )(?:pause|stop|stay still|tạm dừng|dừng|ở yên)(?: không)?"
    r"|(?:let's|we can|mình|chúng mình)\s+(?:call|gọi)\s+.*(?:adult|grown-up|parent|bố|mẹ|người lớn).*"
    r"|(?:it sounds like|có vẻ)\s+.*(?:sad|upset|worried|buồn|lo|khó chịu).*"
    r")$",
    re.IGNORECASE,
)
_QUESTION_LEAD_RE = re.compile(
    r"(?:^|[.!]\s+)(?:can|could|would|will|do|did|are|is|what|where|who|why|how|which|when)\b"
    r"|(?:^|[.!]\s+)con\s+(?:có|muốn|thấy|nghĩ)\b",
    re.IGNORECASE,
)
_FACT_CLAUSE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_FACT_GUIDANCE_LEAD_RE = re.compile(
    r"^(?:look|find|point|say|repeat|listen|show|nhìn|chỉ|nói|lặp|nghe)\b"
    r"|^(?:mình|we)\s+(?:can\s+)?(?:nhìn|thử|học|tạm dừng|look|try|learn|pause)\b",
    re.IGNORECASE,
)
_FACT_RELATION_LEAD_RE = re.compile(
    r"^(?:(?:robot|i|mình)\s+(?:hear|heard|am listening|nghe)\b.*"
    r"|robot is here"
    r"|(?:let us|let's|we can|we will|mình|chúng mình)\s+"
    r"(?:look(?: together)?|learn|keep learning|try another way|pause|stop|continue)"
    r"|here is another word|okay)$",
    re.IGNORECASE,
)
_FACT_INVITATION_LEAD_RE = re.compile(
    r"^(?:can|could|would)\s+you\s+(?:say|repeat|point|find|show)\b"
    r"|^(?:what|which)\b"
    r"|^(?:trong\s+tiếng\s+anh|.+\s+là\s+gì)\b",
    re.IGNORECASE,
)
_FACT_FREE_INVITATION_RE = re.compile(
    r"^(?:ready|again|what is it|what do you see|which one"
    r"|do you want to (?:pause|stop|continue|try again)"
    r"|(?:can|could|would) you (?:say|repeat|point|find|show) "
    r"(?:it|this|that|again)"
    r"|con muốn robot (?:ở yên|tạm dừng|dừng)(?: không)?)$",
    re.IGNORECASE,
)
_TARGET_FACT_VERB_RE = re.compile(
    r"\b(?:is|are|was|were|can|could|will|would|has|have|"
    r"live|lives|eat|eats|fly|flies|sleep|sleeps|like|likes|"
    r"là|sống|bay|ăn|ngủ|có|thích)\b",
    re.IGNORECASE,
)


class CourseResponsePlanError(ValueError):
    pass


# ── D9: which cause fired, without leaking the whitelist ───────────────────────
#
# Owner decision D9 (coordination/owner-decisions-20260919.md): a caller refused
# with INVALID_RESPONSE_PLAN must be able to identify the cause, "without leaking
# child-safety whitelist contents into a response a client could mine".
#
# THE TENSION, AND HOW IT IS RESOLVED. The refusal channel is already an oracle: a
# caller learns accepted/refused for any wording it sends, one query per candidate
# string. What a sub-code adds is WHICH RULE refused — never which term, clause,
# pattern or field value triggered it. That distinction is the whole design:
#
#   * the sub-code names a RULE. The rule set below is a compile-time constant,
#     identical for every child, every session and every contract version, and
#     carries no session state whatsoever. Knowing "the fact-wording rule refused
#     this" does not narrow the approved-term set by one term.
#   * it does not reduce the NUMBER of queries needed to enumerate the whitelist.
#     Probing was one boolean query per candidate wording before, and is exactly one
#     boolean query per candidate wording after. The cost of PROBING is unchanged;
#     only the cost of DEBUGGING falls, which is what D9 asked for.
#   * the projection below is an ALLOW-LIST, not a pass-through. A reason that is not
#     in this frozen set becomes `UNSPECIFIED`. That is the load-bearing safety
#     property: a future validator that raises `CourseResponsePlanError(f"...{term}")`
#     cannot leak that term through this channel, because the channel can only ever
#     emit one of these constants.
#
# What must therefore NEVER be added to a refusal payload, and is asserted against in
# tests/test_course_response_plan_refusal_detail.py: the offending clause, any plan
# field's text, any approved fact term or target word, any regex fragment, and any
# count or position that would let a caller bisect the whitelist faster than one
# query per wording.
COURSE_RESPONSE_PLAN_REFUSAL_CODES = frozenset({
    # raised by CourseResponsePlan.from_mapping
    "INVALID_FIELDS",
    "TOO_MANY_QUESTIONS",
    "UNAPPROVED_FACT",
    "INVALID_RESPONSE_TEXT",
    "EMPTY_RESPONSE_TEXT",
    "RESPONSE_TEXT_TOO_LONG",
    "QUESTION_COUNT_MISMATCH",
    "PROHIBITED_WORDING",
    "MASTERY_PRAISE_NOT_AUTHORIZED",
    "UNAPPROVED_FACT_WORDING",
    "UNSUPPORTED_INTENT",
    "DISAPPOINTED_MISS_FEEDBACK",
    "UNSAFE_SAFETY_INTENT",
    "SAFETY_REDIRECTION",
    # raised by LessonRuntime.course_apply_response_plan AFTER from_mapping accepts,
    # where the plan is structurally legal but disagrees with the live decision
    "INTENT_DOES_NOT_MATCH_DECISION",
    "TARGET_MODELLING_NOT_AUTHORIZED",
    "SAFETY_MODE_DOES_NOT_MATCH_DECISION",
})

#: What an unrecognised reason projects to. Fail-closed: an unknown reason is
#: reported as unknown rather than echoed.
COURSE_RESPONSE_PLAN_REFUSAL_UNSPECIFIED = "UNSPECIFIED"


def course_response_plan_refusal_code(reason: Any) -> str:
    """Project a refusal reason onto the closed, leak-free sub-code set.

    Deliberately total and deliberately narrow: anything not in
    :data:`COURSE_RESPONSE_PLAN_REFUSAL_CODES` becomes
    :data:`COURSE_RESPONSE_PLAN_REFUSAL_UNSPECIFIED`, including a
    `CourseResponsePlanError` whose message a future edit made informative.
    """
    if isinstance(reason, CourseResponsePlanError):
        args = reason.args
        reason = args[0] if args else None
    if isinstance(reason, str) and reason in COURSE_RESPONSE_PLAN_REFUSAL_CODES:
        return reason
    return COURSE_RESPONSE_PLAN_REFUSAL_UNSPECIFIED


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
        suffix = "s?" if target_word.isascii() and target_word.isalpha() else ""
        pattern = rf"(?<!\w){re.escape(target_word.casefold())}{suffix}(?!\w)"
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
        cls._validate_fact_wording(text_fields, approved_fact_terms, bool(facts))
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
            if any(
                not _SAFETY_LANGUAGE_RE.fullmatch(clause.rstrip(".!?").strip())
                for field in text_fields
                for clause in _FACT_CLAUSE_SPLIT_RE.split(field.strip())
                if clause.strip()
            ):
                raise CourseResponsePlanError("SAFETY_REDIRECTION")
        return cls(
            *text_fields,
            question_count=value["questionCount"], embodied_intent=embodied,
            target_facts_used=tuple(facts), praise_level=str(value["praiseLevel"]),
            safety_mode=bool(value["safetyMode"]), normal_miss=bool(value["normalMiss"]),
        )

    @staticmethod
    def _validate_fact_wording(
        text_fields: tuple[Any, ...], approved_fact_terms: Set[str], has_fact_codes: bool,
    ) -> None:
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
        unsupported_declaration_re = re.compile(
            rf"(?:^|[,;:]\s*)(?:(?:this|that|it|they|he|she)|"
            rf"(?:(?:a|an|the|một|con|quả)\s+)?(?:{target_re.pattern}))\s+"
            rf"(?:is|are|was|were|can|could|will|would|has|have|"
            rf"live|lives|eat|eats|fly|flies|like|likes|là|sống|bay|ăn|có|thích)\b"
            rf"|^(?:can|could|do|does|is|are)\s+"
            rf"(?:(?:a|an|the)\s+)?(?:{target_re.pattern})\s+\w+",
            re.IGNORECASE,
        )
        for field_index, field in enumerate(text_fields):
            for raw_clause in _FACT_CLAUSE_SPLIT_RE.split(field.strip()):
                clause = raw_clause.strip()
                if not clause:
                    continue
                normalized = clause.rstrip(".!?").strip()
                if identity_re.fullmatch(normalized):
                    continue
                if unsupported_declaration_re.search(normalized):
                    raise CourseResponsePlanError("UNAPPROVED_FACT_WORDING")
                contains_target = target_re.search(normalized) is not None
                if field_index == 0:
                    if contains_target and (
                        not has_fact_codes or _TARGET_FACT_VERB_RE.search(normalized)
                    ):
                        raise CourseResponsePlanError("UNAPPROVED_FACT_WORDING")
                    continue
                if field_index == 3:
                    if (
                        contains_target
                        and not _FACT_INVITATION_LEAD_RE.match(normalized)
                    ) or (
                        not contains_target
                        and not _FACT_FREE_INVITATION_RE.fullmatch(normalized)
                    ):
                        raise CourseResponsePlanError("UNAPPROVED_FACT_WORDING")
                    continue
                if (
                    (field_index == 1 and _FACT_RELATION_LEAD_RE.match(normalized))
                    or (field_index == 2 and (
                        _FACT_GUIDANCE_LEAD_RE.match(normalized)
                        or _FACT_RELATION_LEAD_RE.match(normalized)
                    ))
                ):
                    continue
                raise CourseResponsePlanError("UNAPPROVED_FACT_WORDING")

    def response_text(self) -> str:
        return " ".join(filter(None, (
            self.acknowledgment.strip(), self.relation.strip(),
            self.guidance.strip(), self.invitation.strip(),
        )))
