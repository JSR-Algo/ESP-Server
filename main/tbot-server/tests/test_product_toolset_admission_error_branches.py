"""Close the error-path branch gaps in ``core.providers.tools.product_toolset``.

``--cov-branch`` flags two defensive edges never taken by the happy-path admission
tests in ``test_product_toolset_admission.py``:

  * lesson_runtime_enabled (75-76): the ``_lesson_runtime_enabled`` callable RAISES
    -> the ``except Exception: return False`` swallow. A robot whose flag-source
    method throws must fall closed (lesson tool NOT admitted), never propagate.
  * _configured_child_tools (85): ``conn.config`` is not a Mapping (e.g. None) ->
    early ``return []``. A connection without a real config dict must contribute no
    configured child tools, rather than blowing up on ``.get`` against a non-Mapping.

Mirrors test_product_toolset_admission.py: tiny hand-rolled conns, assert directly on
the ``product_tool_names`` / helper output, no heavyweight provider wrappers.
"""

import unittest

from core.providers.tools.product_toolset import (
    ALWAYS_INCLUDE,
    ALWAYS_INCLUDE_WHEN_LESSON_ENABLED,
    _configured_child_tools,
    lesson_runtime_enabled,
    product_tool_names,
)


class _RaisingFlagConn:
    """Connection whose ``_lesson_runtime_enabled`` flag-source method throws."""

    def __init__(self):
        self.config = {}

    def _lesson_runtime_enabled(self):
        raise RuntimeError("flag source unavailable")


class _NoConfigConn:
    """Connection whose ``config`` attribute is None (not a Mapping)."""

    config = None
    # No _lesson_runtime_enabled method -> falls through to the config branch too.


class ProductToolsetErrorBranchTest(unittest.TestCase):
    def test_lesson_runtime_enabled_swallows_checker_exception_and_falls_closed(self):
        """75-76: a raising flag checker must be swallowed and resolve to False,
        so the lesson-gated tool is NOT admitted. Fail-closed, never propagate."""
        conn = _RaisingFlagConn()

        # The helper itself returns False rather than raising...
        self.assertFalse(lesson_runtime_enabled(conn))

        # ...and the admission list it feeds omits the lesson-gated tool entirely,
        # while still being the real base toolset (guards against trivial pass).
        names = product_tool_names(conn)
        for gated in ALWAYS_INCLUDE_WHEN_LESSON_ENABLED:
            self.assertNotIn(gated, names)
        for base_tool in ALWAYS_INCLUDE:
            self.assertIn(base_tool, names)

    def test_configured_child_tools_returns_empty_when_config_not_mapping(self):
        """85: ``conn.config`` is None -> ``_configured_child_tools`` returns []
        without touching ``.get`` on a non-Mapping."""
        conn = _NoConfigConn()

        self.assertEqual(_configured_child_tools(conn), [])

        # And the public builder still produces a valid base list off a None config
        # (lesson flag also falls closed via the config fallback's isinstance guard).
        names = product_tool_names(conn)
        for base_tool in ALWAYS_INCLUDE:
            self.assertIn(base_tool, names)
        for gated in ALWAYS_INCLUDE_WHEN_LESSON_ENABLED:
            self.assertNotIn(gated, names)


if __name__ == "__main__":
    unittest.main()
