"""CR-TRIG-01: assert the start_lesson tool-admission LIST construction directly.

The existing ``test_product_toolset.py`` proves omission/inclusion only through the
heavyweight ``GoogleLiveProvider`` / ``ServerPluginExecutor`` wrappers. Those wrappers
apply their own filtering on top of the list builder, so a regression that drops
``start_lesson`` from ``product_tool_names`` (the real determinant of what the model is
allowed to call) could still be masked there.

This module exercises ``product_tool_names`` directly: the canonical admission list
must OMIT ``start_lesson`` when LESSON_RUNTIME_ENABLED is OFF and INCLUDE it when ON,
for both the connection-method flag source and the config-based fallback.
"""

import unittest

from core.providers.tools.product_toolset import (
    ALWAYS_INCLUDE,
    ALWAYS_INCLUDE_WHEN_LESSON_ENABLED,
    product_tool_names,
)


class _MethodFlagConn:
    """Connection whose lesson-runtime flag is exposed via a ``_lesson_runtime_enabled``
    callable -- the path real Live/classic connections use."""

    def __init__(self, runtime_enabled):
        self._enabled = runtime_enabled
        # No Intent profile configured -> only ALWAYS_INCLUDE (+ lesson when on).
        self.config = {}

    def _lesson_runtime_enabled(self):
        return self._enabled


class _ConfigFlagConn:
    """Connection with no flag method; the flag lives in config['lesson']['runtime_enabled']
    (the fallback branch of lesson_runtime_enabled)."""

    def __init__(self, runtime_enabled):
        self.config = {"lesson": {"runtime_enabled": runtime_enabled}}


class StartLessonAdmissionListTest(unittest.TestCase):
    def test_start_lesson_omitted_from_admission_list_when_runtime_disabled(self):
        names = product_tool_names(_MethodFlagConn(runtime_enabled=False))

        self.assertNotIn("start_lesson", names)
        # The list must still be the real base toolset, not an empty/degenerate list --
        # otherwise "not in" would pass trivially.
        for base_tool in ALWAYS_INCLUDE:
            self.assertIn(base_tool, names)

    def test_start_lesson_included_in_admission_list_when_runtime_enabled(self):
        names = product_tool_names(_MethodFlagConn(runtime_enabled=True))

        self.assertIn("start_lesson", names)
        for base_tool in ALWAYS_INCLUDE:
            self.assertIn(base_tool, names)

    def test_flag_is_the_only_difference_between_off_and_on_admission_lists(self):
        off_names = product_tool_names(_MethodFlagConn(runtime_enabled=False))
        on_names = product_tool_names(_MethodFlagConn(runtime_enabled=True))

        # Toggling LESSON_RUNTIME_ENABLED must add exactly the lesson-gated tool(s) and
        # nothing else -- pins that start_lesson is gated by this flag and only this flag.
        self.assertEqual(
            set(on_names) - set(off_names),
            set(ALWAYS_INCLUDE_WHEN_LESSON_ENABLED),
        )
        self.assertEqual(set(off_names) - set(on_names), set())
        self.assertIn("start_lesson", ALWAYS_INCLUDE_WHEN_LESSON_ENABLED)

    def test_admission_list_has_no_duplicate_start_lesson_when_enabled(self):
        names = product_tool_names(_MethodFlagConn(runtime_enabled=True))

        self.assertEqual(names.count("start_lesson"), 1)

    def test_config_fallback_flag_off_omits_start_lesson(self):
        names = product_tool_names(_ConfigFlagConn(runtime_enabled=False))

        self.assertNotIn("start_lesson", names)
        self.assertIn(ALWAYS_INCLUDE[0], names)

    def test_config_fallback_flag_on_includes_start_lesson(self):
        names = product_tool_names(_ConfigFlagConn(runtime_enabled=True))

        self.assertIn("start_lesson", names)


if __name__ == "__main__":
    unittest.main()
