"""The Course Mode abandonment timeout (owner decision D2, 2026-09-19).

One place for the named, configuration-driven value behind the course-mode
inactivity policy, so that both the runtime that arms the policy and the config
loader that admits an operator's override agree on the name, the default, the
bounds and what an unusable value does.
"""
from __future__ import annotations

import math
from typing import Any


# ── course-mode abandonment timeout (owner decision D2, 2026-09-19) ─────────────
#
# The wait before an abandoned Course Mode session is closed. It is a NAMED,
# CONFIGURATION-DRIVEN value, never a literal at the call site, and it is
# overridable without a rebuild through the same mechanism the rest of the course
# runtime uses:
#
#   env  LESSON_COURSE_INACTIVITY_TIMEOUT_SEC  (config_loader._apply_lesson_env_overrides)
#   file lesson.course_inactivity_timeout_sec  (data/.config.yaml over config.yaml)
#
# DERIVATION (coordination/owner-decisions-20260919.md, D2). The course flow
# already REPROMPTS a silent child at ~12 s and PAUSES at ~25 s — both measured on
# a real stack in T18 run-08, case C11. An abandonment CLOSE is a much heavier
# verdict than a pause: a child who wandered off for a moment and comes back should
# still find the lesson there to resume. So the close must sit well beyond that
# pause, not near it. 180 s leaves roughly two and a half minutes after the ~25 s
# pause before the session is ended. This is a starting value chosen to be SAFE,
# not a researched one — nobody has measured how long a five-year-old actually
# takes — which is exactly why it is configurable rather than compiled in.
#
# BOUNDS. Below MIN the close would land on or near the ~25 s pause and start
# ending lessons a returning child could have resumed; above MAX the "close"
# stops being a policy at all (an hour of silence is the RUNNING-forever hole this
# decision exists to shut). A value outside the bounds, or one that cannot be
# parsed, FAILS CLOSED — the loader refuses it and the server refuses to boot —
# rather than silently reverting to the default, because a silent revert means an
# operator who set 600 gets 180 and is never told.
COURSE_INACTIVITY_TIMEOUT_CONFIG_KEY = "course_inactivity_timeout_sec"
COURSE_INACTIVITY_TIMEOUT_ENV_VAR = "LESSON_COURSE_INACTIVITY_TIMEOUT_SEC"
COURSE_INACTIVITY_TIMEOUT_DEFAULT_SEC = 180.0
COURSE_INACTIVITY_TIMEOUT_MIN_SEC = 30.0
COURSE_INACTIVITY_TIMEOUT_MAX_SEC = 1800.0


class CourseInactivityTimeoutConfigError(ValueError):
    """An unusable lesson.course_inactivity_timeout_sec. Raised, never defaulted."""


def course_inactivity_timeout_sec(lesson_cfg: Any) -> float:
    """The effective abandonment timeout, or raise.

    Absent/blank -> COURSE_INACTIVITY_TIMEOUT_DEFAULT_SEC. Anything present but
    unusable raises: D2 forbids silently reverting to the default.
    """
    if not isinstance(lesson_cfg, dict):
        return COURSE_INACTIVITY_TIMEOUT_DEFAULT_SEC
    raw = lesson_cfg.get(COURSE_INACTIVITY_TIMEOUT_CONFIG_KEY)
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return COURSE_INACTIVITY_TIMEOUT_DEFAULT_SEC
    if isinstance(raw, bool):
        raise CourseInactivityTimeoutConfigError(
            f"lesson.{COURSE_INACTIVITY_TIMEOUT_CONFIG_KEY} must be a number, got a boolean"
        )
    try:
        parsed = float(raw)
    except (TypeError, ValueError) as exc:
        raise CourseInactivityTimeoutConfigError(
            f"lesson.{COURSE_INACTIVITY_TIMEOUT_CONFIG_KEY} must be a number, got {raw!r}"
        ) from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise CourseInactivityTimeoutConfigError(
            f"lesson.{COURSE_INACTIVITY_TIMEOUT_CONFIG_KEY} must be a positive finite number, got {raw!r}"
        )
    return parsed


def assert_course_inactivity_timeout_in_range(value: float) -> float:
    """Operator-supplied bounds check. Raises rather than clamping or defaulting.

    Applied where an OPERATOR supplies the value (env / config volume), which is
    where an out-of-range number is a mistake worth refusing a boot over. The
    in-process parse above stays bounds-free so tests can drive the policy with a
    short window without weakening the operator-facing contract.
    """
    if not (COURSE_INACTIVITY_TIMEOUT_MIN_SEC <= value <= COURSE_INACTIVITY_TIMEOUT_MAX_SEC):
        raise CourseInactivityTimeoutConfigError(
            f"lesson.{COURSE_INACTIVITY_TIMEOUT_CONFIG_KEY} must be between "
            f"{COURSE_INACTIVITY_TIMEOUT_MIN_SEC:g}s and {COURSE_INACTIVITY_TIMEOUT_MAX_SEC:g}s "
            f"(the course flow reprompts at ~12s and pauses at ~25s; an abandonment close "
            f"must sit well beyond that pause), got {value!r}"
        )
    return value
