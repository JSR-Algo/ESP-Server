from __future__ import annotations

import pytest

from core.lesson.course_response_plan import CourseResponsePlan, CourseResponsePlanError


def valid(**overrides):
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


def test_normal_plan_is_short_acknowledges_child_and_has_one_question() -> None:
    plan = CourseResponsePlan.from_mapping(valid(), approved_fact_codes={"animals.cat", "pet"})
    assert plan.question_count == 1
    assert plan.acknowledgment.startswith("Một bạn mèo trắng")


@pytest.mark.parametrize(
    "value",
    [
        valid(questionCount=2),
        valid(targetFactsUsed=["unapproved.fact"]),
        valid(guidance="Sai rồi, cố hơn."),
        valid(praiseLevel="mastery"),
        valid(normalMiss=True, embodiedIntent="COMFORT_CALM"),
        valid(safetyMode=True, guidance="Quay lại từ cat nhé", invitation="Cat là gì?"),
        {**valid(), "transcript": "raw child text"},
    ],
)
def test_invalid_or_overclaiming_plans_fail_closed(value) -> None:
    with pytest.raises(CourseResponsePlanError):
        CourseResponsePlan.from_mapping(value, approved_fact_codes={"animals.cat", "pet"})


def test_safety_plan_may_pause_or_comfort_without_target_elicitation() -> None:
    plan = CourseResponsePlan.from_mapping(valid(
        acknowledgment="Robot đang nghe đây.", relation="Mình gọi người lớn ở gần nhé.",
        guidance="Mình tạm dừng.", invitation="Con muốn robot ở yên không?",
        embodiedIntent="COMFORT_CALM", targetFactsUsed=[], safetyMode=True,
    ), approved_fact_codes={"animals.cat"})
    assert plan.safety_mode is True


def test_safety_plan_rejects_authored_vietnamese_target_meaning() -> None:
    with pytest.raises(CourseResponsePlanError, match="SAFETY_REDIRECTION"):
        CourseResponsePlan.from_mapping(valid(
            acknowledgment="Robot đang nghe đây.", relation="",
            guidance="Mèo là con vật đáng yêu.", invitation="Con muốn robot ở yên không?",
            embodiedIntent="COMFORT_CALM", targetFactsUsed=[], safetyMode=True,
        ), approved_fact_codes={"animals.cat"}, safety_forbidden_terms={"cat", "mèo"})


def test_underreported_questions_fail_closed() -> None:
    with pytest.raises(CourseResponsePlanError):
        CourseResponsePlan.from_mapping(
            valid(guidance="What is it? Can you say it?", invitation="Ready?", questionCount=1),
            approved_fact_codes={"animals.cat", "pet"},
        )


@pytest.mark.parametrize(
    "value",
    [
        valid(
            acknowledgment="", relation="", guidance="", invitation="",
            questionCount=0, targetFactsUsed=[],
        ),
        valid(guidance="x" * 161),
        valid(acknowledgment=123),
    ],
)
def test_empty_unbounded_or_non_string_child_text_fails_closed(value) -> None:
    with pytest.raises(CourseResponsePlanError):
        CourseResponsePlan.from_mapping(
            value, approved_fact_codes={"animals.cat", "pet"},
        )


@pytest.mark.parametrize(
    "value",
    [
        valid(
            acknowledgment="Perfect! You mastered cat.", relation="", guidance="",
            invitation="", questionCount=0, praiseLevel="engagement",
        ),
        valid(
            acknowledgment="Can you point to it. Can you say it.", relation="",
            guidance="", invitation="", questionCount=0, targetFactsUsed=[],
        ),
        valid(invitation="Ready. Say it?", questionCount=1),
    ],
)
def test_claims_and_questions_are_validated_from_spoken_text(value) -> None:
    with pytest.raises(CourseResponsePlanError):
        CourseResponsePlan.from_mapping(
            value, approved_fact_codes={"animals.cat", "pet"},
        )


def test_target_fact_wording_must_stay_within_authorized_teaching_forms() -> None:
    with pytest.raises(CourseResponsePlanError, match="UNAPPROVED_FACT_WORDING"):
        CourseResponsePlan.from_mapping(
            valid(
                acknowledgment="Yes.", relation="Cats live on Mars.",
                guidance="They eat moon rocks.", invitation="", questionCount=0,
                targetFactsUsed=["animals.cat"],
            ),
            approved_fact_codes={"animals.cat"},
            approved_fact_terms={"cat", "con mèo", "mèo"},
        )

    plan = CourseResponsePlan.from_mapping(
        valid(
            acknowledgment="Yes.", relation="", guidance="This is a cat.",
            invitation="Can you say cat?", targetFactsUsed=["animals.cat"],
        ),
        approved_fact_codes={"animals.cat"},
        approved_fact_terms={"cat", "con mèo", "mèo"},
    )
    assert plan.target_facts_used == ("animals.cat",)
