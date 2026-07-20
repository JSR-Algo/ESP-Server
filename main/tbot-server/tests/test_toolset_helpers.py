import unittest
from types import SimpleNamespace

from core.providers.tools.base.tool_executor import ToolExecutor
from core.providers.tools.product_toolset import (
    _configured_child_tools,
    _configured_function_names,
    _dedupe,
    _is_child_allowed,
    lesson_runtime_enabled,
)


class _ExplodingLessonConn:
    def _lesson_runtime_enabled(self):
        raise RuntimeError("boom")


class _ConcreteToolExecutor(ToolExecutor):
    async def execute(self, conn, tool_name, arguments):
        return await super().execute(conn, tool_name, arguments)

    def get_tools(self):
        return super().get_tools()

    def has_tool(self, tool_name):
        return super().has_tool(tool_name)


class ProductToolsetHelperTest(unittest.TestCase):
    def test_lesson_runtime_enabled_handles_callable_errors_and_config_fallbacks(self):
        self.assertFalse(lesson_runtime_enabled(_ExplodingLessonConn()))
        self.assertFalse(lesson_runtime_enabled(SimpleNamespace(config=None)))
        self.assertFalse(
            lesson_runtime_enabled(
                SimpleNamespace(
                    device_id="robot-01",
                    config={"lesson": {"runtime_enabled": True}},
                )
            )
        )

    def test_lesson_runtime_enabled_requires_config_admission_before_checker(self):
        checker_only = SimpleNamespace(_lesson_runtime_enabled=lambda: True)
        admitted = SimpleNamespace(
            device_id=" ROBOT-01 ",
            config={
                "lesson": {
                    "runtime_enabled": True,
                    "rollout_device_allowlist": ["robot-01"],
                }
            },
            _lesson_runtime_enabled=lambda: True,
        )
        checker_restricted = SimpleNamespace(
            device_id="robot-01",
            config=admitted.config,
            _lesson_runtime_enabled=lambda: False,
        )

        self.assertFalse(lesson_runtime_enabled(checker_only))
        self.assertTrue(lesson_runtime_enabled(admitted))
        self.assertFalse(lesson_runtime_enabled(checker_restricted))

    def test_configured_child_tools_rejects_bad_config_shapes(self):
        self.assertEqual(_configured_child_tools(SimpleNamespace(config=None)), [])
        self.assertEqual(_configured_function_names({}), [])
        self.assertEqual(_configured_function_names({"Intent": {"other": {}}}), [])
        self.assertEqual(
            _configured_function_names(
                {"Intent": {"function_call": {"functions": "play_music"}}}
            ),
            [],
        )

    def test_configured_child_tools_filters_to_child_music_controls(self):
        conn = SimpleNamespace(
            config={
                "selected_module": {"Intent": "custom"},
                "Intent": {
                    "custom": {
                        "functions": [
                            "play_music",
                            "web_search",
                            "pause_music",
                            None,
                        ]
                    }
                },
            }
        )

        self.assertEqual(_configured_child_tools(conn), ["play_music", "pause_music"])

    def test_child_allowed_and_dedupe_helpers_cover_deny_and_duplicate_paths(self):
        self.assertTrue(_is_child_allowed("get_weather"))
        self.assertFalse(_is_child_allowed("factory_reset"))
        self.assertTrue(_is_child_allowed("change_volume"))
        self.assertEqual(_dedupe(["a", "b", "a", "c"]), ["a", "b", "c"])


class ToolExecutorBaseTest(unittest.IsolatedAsyncioTestCase):
    async def test_base_abstract_methods_are_noop_when_called_by_concrete_subclass(self):
        executor = _ConcreteToolExecutor()

        self.assertIsNone(await executor.execute(None, "tool", {}))
        self.assertIsNone(executor.get_tools())
        self.assertIsNone(executor.has_tool("tool"))


if __name__ == "__main__":
    unittest.main()
