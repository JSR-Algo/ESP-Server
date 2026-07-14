"""CR-TRIG-01: assert the start_lesson tool-admission LIST construction directly.

The existing ``test_product_toolset.py`` proves omission/inclusion only through the
heavyweight ``GoogleLiveProvider`` / ``ServerPluginExecutor`` wrappers. Those wrappers
apply their own filtering on top of the list builder, so a regression that drops
``start_lesson`` from ``product_tool_names`` (the real determinant of what the model is
allowed to call) could still be masked there.

This module exercises ``product_tool_names`` directly: the canonical admission list
must OMIT ``start_lesson`` when both lesson gates are OFF and INCLUDE it when either
the production runtime or the built-in sample lesson is ON.
"""

import unittest

from core.providers.tools.product_toolset import (
    ALWAYS_INCLUDE,
    ALWAYS_INCLUDE_WHEN_LESSON_ENABLED,
    product_tool_names,
    sample_lesson_config_enabled,
)


class _MethodFlagConn:
    """Connection whose lesson-runtime flag is exposed via a ``_lesson_runtime_enabled``
    callable -- the path real Live/classic connections use."""

    def __init__(self, runtime_enabled, sample_enabled=False):
        self._enabled = runtime_enabled
        self._sample_enabled = sample_enabled
        self.device_id = "robot-01"
        # No Intent profile configured -> only ALWAYS_INCLUDE (+ lesson when on).
        self.config = {
            "lesson": {
                "runtime_enabled": runtime_enabled,
                "sample_lesson": sample_enabled,
                "rollout_device_allowlist": [self.device_id],
            }
        }

    def _lesson_runtime_enabled(self):
        return self._enabled

    def _sample_lesson_enabled(self):
        return self._sample_enabled


class _ConfigFlagConn:
    """Connection with no flag method; flags live in config['lesson']."""

    def __init__(self, runtime_enabled, sample_enabled=False):
        self.device_id = "robot-01"
        self.config = {
            "lesson": {
                "runtime_enabled": runtime_enabled,
                "sample_lesson": sample_enabled,
                "rollout_device_allowlist": ["robot-01"],
            }
        }


class StartLessonAdmissionListTest(unittest.TestCase):
    def test_sample_requires_exactly_one_matching_normalized_device(self):
        conn = _ConfigFlagConn(runtime_enabled=False, sample_enabled=True)
        conn.device_id = " ROBOT-01 "
        conn.config["lesson"]["rollout_device_allowlist"] = [" Robot-01 ", "robot-01"]

        self.assertTrue(sample_lesson_config_enabled(conn))

    def test_sample_rejects_missing_empty_multiple_and_nonmatching_allowlists(self):
        conn = _ConfigFlagConn(runtime_enabled=False, sample_enabled=True)

        for allowlist in (None, [], ["robot-01", "robot-02"], ["robot-02"]):
            if allowlist is None:
                conn.config["lesson"].pop("rollout_device_allowlist", None)
            else:
                conn.config["lesson"]["rollout_device_allowlist"] = allowlist
            self.assertFalse(sample_lesson_config_enabled(conn), allowlist)

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

    def test_runtime_flag_is_the_only_difference_between_off_and_on_admission_lists(self):
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

    def test_start_lesson_included_when_sample_lesson_enabled_without_runtime(self):
        names = product_tool_names(
            _MethodFlagConn(runtime_enabled=False, sample_enabled=True)
        )

        self.assertIn("start_lesson", names)

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

    def test_config_fallback_sample_on_includes_start_lesson_without_runtime(self):
        names = product_tool_names(
            _ConfigFlagConn(runtime_enabled=False, sample_enabled=True)
        )

        self.assertIn("start_lesson", names)


if __name__ == "__main__":
    unittest.main()
