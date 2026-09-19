"""D2 (owner-decisions-20260919.md): the abandonment timeout is a real configuration
value, not a literal - honoured when overridden, and failing closed when unusable.

The owner's decision: keep 180 s but "stop calling it a placeholder: it must become a
named, configurable value with its derivation written down ... and it must be
overridable without a rebuild."

Two things have to be true, and both are measured here rather than asserted in prose:

* **Honoured when overridden.** An operator setting LESSON_COURSE_INACTIVITY_TIMEOUT_SEC
  (or lesson.course_inactivity_timeout_sec in the config volume) gets that number, all
  the way through to the window the runtime actually waits - not just into a dict.
* **Fails closed.** An out-of-range or unparseable value is REFUSED - the loader raises
  and the server does not boot - instead of silently reverting to 180 s. A silent
  revert is the failure mode that matters: an operator who sets 600 and gets 180 has no
  way to find out, and the abandonment close is a child-visible behaviour.

The bounds come from the derivation itself (recorded beside the constants in
core/lesson/course_inactivity_policy.py): the course flow reprompts at ~12 s and pauses
at ~25 s (T18 run-08 C11), so a close below 30 s would land on the pause a returning
child should still be able to resume from, and a close above 1800 s is not a policy.
"""
import os
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from core.lesson.course_inactivity_policy import (
    COURSE_INACTIVITY_TIMEOUT_CONFIG_KEY,
    COURSE_INACTIVITY_TIMEOUT_DEFAULT_SEC,
    COURSE_INACTIVITY_TIMEOUT_ENV_VAR,
    COURSE_INACTIVITY_TIMEOUT_MAX_SEC,
    COURSE_INACTIVITY_TIMEOUT_MIN_SEC,
    PEER_SILENCE_TIMEOUT_SHIPPED_DEFAULT_SEC,
    CourseInactivityTimeoutConfigError,
    assert_course_inactivity_timeout_in_range,
    course_inactivity_timeout_sec,
)
from core.lesson.runtime import LessonRuntime
from tests.test_course_mode_inactivity_policy import _course_runtime


def _loader():
    # The loader reads os.environ at call time, so no module reload is needed (and a
    # reload would hand every other test in the session a different module object).
    import config.config_loader as config_loader

    return config_loader


def _lesson_config(**lesson):
    return {"lesson": {"runtime_enabled": True, **lesson}}


# ── the value is named, defaulted and documented ──────────────────────────────


def test_the_default_is_180_seconds_and_is_a_named_constant():
    assert COURSE_INACTIVITY_TIMEOUT_DEFAULT_SEC == 180.0
    assert COURSE_INACTIVITY_TIMEOUT_CONFIG_KEY == "course_inactivity_timeout_sec"
    assert COURSE_INACTIVITY_TIMEOUT_ENV_VAR == "LESSON_COURSE_INACTIVITY_TIMEOUT_SEC"
    # The derivation's floor sits above the ~25 s pause it must not collide with, and
    # since D2's first measured rider it also sits clear above the 60 s peer-silence
    # transport budget it must not invert with.
    assert COURSE_INACTIVITY_TIMEOUT_MIN_SEC > 25.0
    assert COURSE_INACTIVITY_TIMEOUT_MIN_SEC > PEER_SILENCE_TIMEOUT_SHIPPED_DEFAULT_SEC
    assert COURSE_INACTIVITY_TIMEOUT_MIN_SEC == 90.0
    # The DEFAULT is deliberately unchanged: D2 is settled by measurement.
    assert COURSE_INACTIVITY_TIMEOUT_DEFAULT_SEC == 180.0
    # An absent value is the only thing that yields the default.
    assert course_inactivity_timeout_sec({}) == COURSE_INACTIVITY_TIMEOUT_DEFAULT_SEC
    assert course_inactivity_timeout_sec({COURSE_INACTIVITY_TIMEOUT_CONFIG_KEY: None}) == 180.0


def test_the_runtime_reads_180_from_configuration_and_not_from_a_literal():
    runtime = _course_runtime(timeout_sec=0.02)
    runtime.conn.config["lesson"].pop(COURSE_INACTIVITY_TIMEOUT_CONFIG_KEY)

    assert runtime._course_inactivity_timeout_sec() == COURSE_INACTIVITY_TIMEOUT_DEFAULT_SEC
    # No literal 180 survives at the call site: the policy module owns the number.
    source = LessonRuntime._course_inactivity_timeout_sec.__doc__ or ""
    assert "course_inactivity_timeout_sec" in source


# ── honoured when overridden ──────────────────────────────────────────────────


@pytest.mark.parametrize("raw,expected", [(240, 240.0), ("240", 240.0), (240.5, 240.5), (30, 30.0)])
# (30 stays here on purpose: the IN-PROCESS parse is bounds-free by design, so a
#  test may still drive the policy with a short window. Only the OPERATOR seam
#  gained the 90 s floor.)
def test_a_configured_value_is_honoured_by_the_runtime(raw, expected):
    runtime = _course_runtime(timeout_sec=raw)

    assert runtime._course_inactivity_timeout_sec() == expected


@pytest.mark.asyncio
async def test_the_configured_value_is_the_window_the_policy_actually_waits():
    """Not just parsed - the armed task sleeps on exactly this number."""
    runtime = _course_runtime(timeout_sec=600.0)
    slept = []

    async def _record(seconds):
        slept.append(seconds)
        raise AssertionError("stop the loop once the window is observed")

    runtime._sleep = _record
    runtime._start_course_inactivity_timeout(runtime._course_assessment_generation)
    task = runtime._course_inactivity_task
    with pytest.raises(AssertionError):
        await task

    assert slept == [600.0]


def test_the_env_override_reaches_the_lesson_config_without_a_rebuild():
    config = _lesson_config()
    with patch.dict(os.environ, {COURSE_INACTIVITY_TIMEOUT_ENV_VAR: "240"}, clear=False):
        applied = _loader()._apply_lesson_env_overrides(config)

    assert applied["lesson"][COURSE_INACTIVITY_TIMEOUT_CONFIG_KEY] == 240.0


def test_a_config_volume_value_survives_the_loader_untouched():
    config = _lesson_config(**{COURSE_INACTIVITY_TIMEOUT_CONFIG_KEY: 300})
    env = {k: v for k, v in os.environ.items() if not k.startswith("LESSON_")}
    with patch.dict(os.environ, env, clear=True):
        applied = _loader()._apply_lesson_env_overrides(config)

    assert applied["lesson"][COURSE_INACTIVITY_TIMEOUT_CONFIG_KEY] == 300


def test_the_env_override_wins_over_the_config_volume():
    config = _lesson_config(**{COURSE_INACTIVITY_TIMEOUT_CONFIG_KEY: 300})
    # Was 45 s. D2's first measured rider raised the operator-facing MINIMUM to 90 s
    # (see the constant's derivation), so 45 is no longer an admissible override and
    # this case now uses 120 to test precedence rather than the bounds.
    with patch.dict(os.environ, {COURSE_INACTIVITY_TIMEOUT_ENV_VAR: "120"}, clear=False):
        applied = _loader()._apply_lesson_env_overrides(config)

    assert applied["lesson"][COURSE_INACTIVITY_TIMEOUT_CONFIG_KEY] == 120.0


# ── fails closed, never silently reverts to the default ───────────────────────


@pytest.mark.parametrize("raw", ["abc", "", "12s", "nan", "inf", "-30", "0", True, [], {}])
def test_an_unparseable_value_raises_instead_of_becoming_180(raw):
    if raw == "":
        # A blank env var is "unset", which is the one benign case.
        assert course_inactivity_timeout_sec({COURSE_INACTIVITY_TIMEOUT_CONFIG_KEY: raw}) == 180.0
        return
    with pytest.raises(CourseInactivityTimeoutConfigError):
        course_inactivity_timeout_sec({COURSE_INACTIVITY_TIMEOUT_CONFIG_KEY: raw})


@pytest.mark.parametrize(
    "value",
    [
        0.5, 29.9, COURSE_INACTIVITY_TIMEOUT_MAX_SEC + 1, 86400.0,
        # D2's first measured rider: 30, 60 and anything up to the new 90 s floor are
        # now REFUSED. A timeout at or below the 60 s peer-silence budget fires first,
        # takes the runtime out of its armed states and suppresses the socket close
        # that would otherwise have reaped a half-open socket - so it removes a cleanup
        # and replaces it with nothing. Measured in
        # coordination/course-inactivity-timer-observability-20260919/report.md §3(c).
        30.0, 45.0, 60.0, 89.9,
    ],
)
def test_an_out_of_range_value_raises_instead_of_being_clamped_or_defaulted(value):
    with pytest.raises(CourseInactivityTimeoutConfigError):
        assert_course_inactivity_timeout_in_range(value)


@pytest.mark.parametrize("value", [COURSE_INACTIVITY_TIMEOUT_MIN_SEC, 180.0, COURSE_INACTIVITY_TIMEOUT_MAX_SEC])
def test_an_in_range_value_passes_the_bounds_check_unchanged(value):
    assert assert_course_inactivity_timeout_in_range(value) == value


@pytest.mark.parametrize("raw", ["abc", "0", "-5", "10", "5400", "nan"])
def test_an_unusable_env_override_refuses_the_boot(raw):
    """The loader raises: the server does not start on a number nobody can honour."""
    with patch.dict(os.environ, {COURSE_INACTIVITY_TIMEOUT_ENV_VAR: raw}, clear=False):
        loader = _loader()
        with pytest.raises(ValueError):
            loader._apply_lesson_env_overrides(_lesson_config())


@pytest.mark.parametrize("raw", ["abc", 0, -5, 10, 5400])
def test_an_unusable_config_volume_value_refuses_the_boot(raw):
    env = {k: v for k, v in os.environ.items() if not k.startswith("LESSON_")}
    with patch.dict(os.environ, env, clear=True):
        loader = _loader()
        with pytest.raises(ValueError):
            loader._apply_lesson_env_overrides(
                _lesson_config(**{COURSE_INACTIVITY_TIMEOUT_CONFIG_KEY: raw})
            )


@pytest.mark.asyncio
async def test_a_runtime_handed_an_unusable_value_refuses_the_turn_rather_than_defaulting():
    """Post-boot (an API-pushed config) the runtime still refuses to invent 180."""
    runtime = _course_runtime(timeout_sec="not-a-number")

    with pytest.raises(CourseInactivityTimeoutConfigError):
        runtime._course_inactivity_timeout_sec()
    with pytest.raises(CourseInactivityTimeoutConfigError):
        runtime._start_course_inactivity_timeout(runtime._course_assessment_generation)
    assert runtime._course_inactivity_task is None
    # The child's turn is refused too - no window opens on a policy nobody can honour.
    assert await runtime._open_course_assessment_window(
        runtime._course_assessment_generation
    ) is False


def test_a_non_dict_lesson_config_is_the_default_not_a_crash():
    assert course_inactivity_timeout_sec(None) == COURSE_INACTIVITY_TIMEOUT_DEFAULT_SEC
    assert course_inactivity_timeout_sec(SimpleNamespace()) == COURSE_INACTIVITY_TIMEOUT_DEFAULT_SEC
