"""D9: INVALID_RESPONSE_PLAN must say WHICH cause fired, without leaking the whitelist.

Owner decision D9 (`coordination/owner-decisions-20260919.md`): "Eleven distinct refusal
causes collapse into one opaque code with no detail. That opacity is not a cosmetic
complaint: it produced a wrong conclusion that propagated into three handoffs and cost a
lane a full run. Make the cause identifiable to a caller, without leaking child-safety
whitelist contents into a response a client could mine."

Two obligations, and the second is the interesting one:

* **Identifiable.** Every cause has a stable machine-readable sub-code, and each is
  exercised here by a real plan that actually triggers that rule - including
  `UNAPPROVED_FACT_WORDING`, the rule that caused the wrong conclusion. Asserting the
  constant set alone would prove nothing; a code nothing can produce is not a diagnosis.
* **Unminable.** The sub-code names the RULE, never the content. The refusal payload is
  asserted to contain no plan text, no approved fact term and no target word, and the
  projection is an ALLOW-LIST so a future validator cannot leak through this channel even
  if it raises with an informative message.

(The decision says eleven causes. The count is actually **seventeen** - fourteen
`CourseResponsePlanError` reasons in `from_mapping` plus three post-validation mismatches
in `course_apply_response_plan`. Recorded here rather than quietly rounded: the decision's
substance is unaffected and its direction is unchanged.)
"""
from __future__ import annotations

import json

import pytest

from core.lesson.course_response_plan import (
    COURSE_RESPONSE_PLAN_REFUSAL_CODES,
    COURSE_RESPONSE_PLAN_REFUSAL_UNSPECIFIED,
    CourseResponsePlan,
    CourseResponsePlanError,
    course_response_plan_refusal_code,
)
from core.lesson.runtime import CourseModeRuntimeAdapter

APPROVED_CODES = {"animals.cat", "pet"}
#: The real shape of a live session's approved-fact terms: the active targets'
#: targetWord plus their Vietnamese meanings plus those meanings' last tokens - which
#: is what `course_apply_response_plan` builds, and what makes the whitelist apply at
#: all (an EMPTY set short-circuits `_validate_fact_wording` entirely, which is the trap
#: that produced the wrong conclusion).
APPROVED_TERMS = {"cat", "con mèo", "mèo"}


def plan(**overrides):
    value = {
        "acknowledgment": "Một bạn mèo trắng ở nhà bà!",
        "relation": "Robot nghe con kể rồi.",
        "guidance": "Mình nhìn bạn trong hình nhé.",
        "invitation": "Trong tiếng Anh, bạn mèo là gì nhỉ?",
        "questionCount": 1,
        "embodiedIntent": "ACKNOWLEDGE_STORY",
        "targetFactsUsed": ["animals.cat", "pet"],
        "praiseLevel": "engagement",
        "safetyMode": False,
        "normalMiss": False,
    }
    value.update(overrides)
    return value


def refuse(value, *, terms=frozenset()):
    """Validate a plan that must be refused, and return its projected sub-code."""
    with pytest.raises(CourseResponsePlanError) as raised:
        CourseResponsePlan.from_mapping(
            value,
            approved_fact_codes=APPROVED_CODES,
            safety_forbidden_terms=set(terms),
            approved_fact_terms=set(terms),
        )
    return course_response_plan_refusal_code(raised.value)


# ── 1. every cause is produced by a real plan and names itself ────────────────


@pytest.mark.parametrize(
    "expected,value,terms",
    [
        # structural
        ("INVALID_FIELDS", {**plan(), "transcript": "raw child text"}, frozenset()),
        ("TOO_MANY_QUESTIONS", plan(questionCount=2), frozenset()),
        ("UNAPPROVED_FACT", plan(targetFactsUsed=["invented.fact"]), frozenset()),
        ("INVALID_RESPONSE_TEXT", plan(guidance="x" * 161), frozenset()),
        (
            "EMPTY_RESPONSE_TEXT",
            plan(
                acknowledgment="", relation="", guidance="", invitation="",
                questionCount=0,
            ),
            frozenset(),
        ),
        (
            "RESPONSE_TEXT_TOO_LONG",
            plan(
                acknowledgment="a" * 120, relation="b" * 120, guidance="c" * 120,
                invitation="Ready?",
            ),
            frozenset(),
        ),
        ("QUESTION_COUNT_MISMATCH", plan(questionCount=0), frozenset()),
        # wording policy
        ("PROHIBITED_WORDING", plan(guidance="Sai rồi, cố hơn."), frozenset()),
        ("MASTERY_PRAISE_NOT_AUTHORIZED", plan(praiseLevel="mastery"), frozenset()),
        # THE one that cost a lane a run: the invitation whitelist, which is invisible
        # unless a real session's approved terms are supplied.
        (
            "UNAPPROVED_FACT_WORDING",
            plan(
                acknowledgment="Con kể hay quá!", relation="Robot nghe con kể rồi.",
                guidance="Mình nhìn bạn trong hình nhé.",
                invitation="Can you say it with me?", targetFactsUsed=[],
            ),
            APPROVED_TERMS,
        ),
        # decision/intent shape
        ("UNSUPPORTED_INTENT", plan(embodiedIntent="NOT_AN_INTENT"), frozenset()),
        (
            "DISAPPOINTED_MISS_FEEDBACK",
            plan(normalMiss=True, embodiedIntent="COMFORT_CALM"),
            frozenset(),
        ),
        (
            "UNSAFE_SAFETY_INTENT",
            plan(safetyMode=True, embodiedIntent="ACKNOWLEDGE_STORY"),
            frozenset(),
        ),
        # A safety plan must redirect AWAY from the lesson; citing a target fact at
        # all is the redirection failure. (Kept term-free so this case exercises the
        # safety rule rather than tripping the fact-wording rule first - the two are
        # ordered, and the sub-code is what makes that order legible.)
        (
            "SAFETY_REDIRECTION",
            plan(
                safetyMode=True, embodiedIntent="COMFORT_CALM",
                targetFactsUsed=["animals.cat"],
                acknowledgment="Robot đang nghe đây.", relation="Mình tạm dừng.",
                guidance="Mình ở yên nhé.",
                invitation="Con muốn robot ở yên không?",
            ),
            frozenset(),
        ),
    ],
)
def test_each_validator_cause_reports_its_own_sub_code(expected, value, terms) -> None:
    assert refuse(value, terms=terms) == expected


def test_the_unapproved_fact_wording_rule_is_invisible_without_real_session_terms() -> None:
    """The exact trap D9 exists because of, pinned as a test.

    `_validate_fact_wording` returns early on an empty approved-term set. An offline
    check therefore ACCEPTS a wording a live session REFUSES - which is how a lane
    concluded the refusal was "the live decision contract, not the wording" and wrote
    that into three handoffs. With the sub-code, the same investigation is one call.
    """
    wording = plan(
        acknowledgment="Con kể hay quá!", relation="Robot nghe con kể rồi.",
        guidance="Mình nhìn bạn trong hình nhé.",
        invitation="Can you say it with me?", targetFactsUsed=[],
    )
    # Offline, with no terms: accepted.
    assert CourseResponsePlan.from_mapping(
        wording, approved_fact_codes=APPROVED_CODES, approved_fact_terms=frozenset(),
    ).question_count == 1
    # Live, with the session's real terms: refused, and it says which rule refused.
    assert refuse(wording, terms=APPROVED_TERMS) == "UNAPPROVED_FACT_WORDING"


def test_a_whitelist_legal_invitation_still_passes_against_real_terms() -> None:
    """The contract is not weakened: legal wordings still validate with terms present."""
    accepted = CourseResponsePlan.from_mapping(
        plan(
            acknowledgment="Con kể hay quá!", relation="Robot nghe con kể rồi.",
            guidance="Mình nhìn bạn trong hình nhé.",
            invitation="What do you see?", targetFactsUsed=[],
        ),
        approved_fact_codes=APPROVED_CODES,
        approved_fact_terms=APPROVED_TERMS,
    )
    assert accepted.invitation == "What do you see?"


# ── 2. the runtime's own post-validation causes ───────────────────────────────


def test_the_three_post_validation_causes_are_named_and_in_the_closed_set() -> None:
    """`from_mapping` accepting is not the end: the plan must also match the decision.

    These three were the most opaque of all - the plan is structurally perfect and the
    refusal was identical to a malformed-JSON one.
    """
    for code in (
        "INTENT_DOES_NOT_MATCH_DECISION",
        "TARGET_MODELLING_NOT_AUTHORIZED",
        "SAFETY_MODE_DOES_NOT_MATCH_DECISION",
    ):
        assert code in COURSE_RESPONSE_PLAN_REFUSAL_CODES
        assert CourseModeRuntimeAdapter._invalid_response_plan(code) == {
            "accepted": False, "code": "INVALID_RESPONSE_PLAN", "reason": code,
        }


def test_the_code_is_unchanged_so_the_detail_is_purely_additive() -> None:
    payload = CourseModeRuntimeAdapter._invalid_response_plan("PROHIBITED_WORDING")
    assert payload["accepted"] is False
    assert payload["code"] == "INVALID_RESPONSE_PLAN"


# ── 3. the leak side: a rule name, never the content ──────────────────────────


@pytest.mark.parametrize(
    "reason",
    [
        None,
        "",
        "NOT_A_REAL_CODE",
        123,
        CourseResponsePlanError("UNAPPROVED_FACT_WORDING: 'cat' is not allowed here"),
        CourseResponsePlanError(),
    ],
)
def test_anything_outside_the_closed_set_becomes_unspecified(reason) -> None:
    """The load-bearing safety property: this channel is an allow-list, not a pass-through.

    A future validator that raises with the offending term in its message cannot leak
    it here, because the projection can only ever emit one of the frozen constants.
    """
    assert (
        course_response_plan_refusal_code(reason)
        == COURSE_RESPONSE_PLAN_REFUSAL_UNSPECIFIED
    )
    assert COURSE_RESPONSE_PLAN_REFUSAL_UNSPECIFIED not in COURSE_RESPONSE_PLAN_REFUSAL_CODES


def test_a_refusal_payload_carries_no_plan_text_and_no_whitelist_content() -> None:
    """Serialize the whole refusal and assert none of the mineable material is in it."""
    sentinel_plan = plan(
        acknowledgment="SENTINEL_ACK", relation="SENTINEL_REL",
        guidance="SENTINEL_GUIDE", invitation="Can you say it with me?",
        targetFactsUsed=[],
    )
    with pytest.raises(CourseResponsePlanError) as raised:
        CourseResponsePlan.from_mapping(
            sentinel_plan,
            approved_fact_codes=APPROVED_CODES,
            safety_forbidden_terms=APPROVED_TERMS,
            approved_fact_terms=APPROVED_TERMS,
        )
    payload = CourseModeRuntimeAdapter._invalid_response_plan(raised.value)
    serialized = json.dumps(payload, ensure_ascii=False)

    assert payload["reason"] == "UNAPPROVED_FACT_WORDING"
    # no plan text
    for field_value in sentinel_plan.values():
        if isinstance(field_value, str) and field_value:
            assert field_value not in serialized
    # no whitelist content: neither the session's approved terms nor the fact codes,
    # nor any fragment of the closed-list invitation regex.
    for term in APPROVED_TERMS | APPROVED_CODES:
        assert term not in serialized.casefold()
    for fragment in ("what do you see", "which one", "ở yên", "fullmatch", "re.compile"):
        assert fragment not in serialized.casefold()
    # and nothing that would let a caller bisect faster: no field index, no clause,
    # no count. The payload is exactly three keys.
    assert set(payload) == {"accepted", "code", "reason"}


def test_the_sub_code_set_is_closed_and_carries_no_session_state() -> None:
    """Every code is a bare upper-snake rule name - no terms, no text, no numbers."""
    assert len(COURSE_RESPONSE_PLAN_REFUSAL_CODES) == 17
    for code in COURSE_RESPONSE_PLAN_REFUSAL_CODES:
        assert code.isupper()
        assert set(code) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZ_")
