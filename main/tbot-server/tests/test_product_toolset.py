import ast
import re
import unittest
from pathlib import Path

from core.providers.tools.server_plugins.plugin_executor import ServerPluginExecutor
from core.voice.session_provider.google_live import GoogleLiveProvider
from plugins_func.register import ToolType, all_function_registry

import plugins_func.functions.change_role  # noqa: F401
import plugins_func.functions.change_volume  # noqa: F401
import plugins_func.functions.get_news_from_newsnow  # noqa: F401
import plugins_func.functions.get_weather  # noqa: F401
import plugins_func.functions.handle_exit_intent  # noqa: F401
import plugins_func.functions.play_music_live  # noqa: F401
import plugins_func.functions.raise_left_arm  # noqa: F401
import plugins_func.functions.robot_arm_actions  # noqa: F401
import plugins_func.functions.start_lesson  # noqa: F401
import plugins_func.functions.turn_head  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]


def _master_start_lesson_desc():
    markdown = (ROOT / "docs" / "lesson-master-prompts.md").read_text(encoding="utf-8")
    start = markdown.index("## B. start_lesson FUNCTION DECLARATION")
    block_start = markdown.index("```python\n", start) + len("```python\n")
    block_end = markdown.index("\n```", block_start)
    module = ast.parse(markdown[block_start:block_end])
    assignment = next(node for node in module.body if isinstance(node, ast.Assign))
    value = ast.literal_eval(assignment.value)
    return value["function"]["description"]
import plugins_func.functions.web_search  # noqa: F401


class _DummyLogger:
    def bind(self, **kwargs):
        return self

    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass


class _ToolsetConn:
    def __init__(self, runtime_enabled=True):
        functions = [
            "change_role",
            "start_lesson",
            "change_volume",
            "raise_left_arm",
            "web_search",
            "get_weather",
            "get_news_from_newsnow",
            "play_music",
        ]
        self.config = {
            "selected_module": {"Intent": "function_call"},
            "Intent": {
                "function_call": {
                    "type": "function_call",
                    "functions": list(functions),
                }
            },
            "google_live": {"api_key": "key", "model": "gemini-live"},
            "lesson": {"runtime_enabled": runtime_enabled},
            "plugins": {},
            "voice_mode": {"type": "google_live"},
        }
        self.logger = _DummyLogger()
        self.func_handler = None

    def _lesson_runtime_enabled(self):
        return bool(self.config["lesson"]["runtime_enabled"])


def _live_names(conn):
    tools = GoogleLiveProvider(conn)._resolve_functions_for_live() or []
    return {
        tool["function"]["name"]
        for tool in tools
        if isinstance(tool, dict) and isinstance(tool.get("function"), dict)
    }


def _classic_names(conn):
    return set(ServerPluginExecutor(conn).get_tools().keys())


class ProductToolsetContractTest(unittest.TestCase):
    def test_start_lesson_registration_matches_master_trigger_contract(self):
        item = all_function_registry["start_lesson"]

        self.assertEqual(item.type, ToolType.SYSTEM_CTL)
        self.assertEqual(item.description["function"]["description"], _master_start_lesson_desc())
        self.assertEqual(
            item.description["function"]["parameters"],
            {"type": "object", "properties": {}, "required": []},
        )

    def test_start_lesson_trigger_description_covers_course_and_study_variants(self):
        description = all_function_registry["start_lesson"].description["function"]["description"]

        for phrase in (
            "bắt đầu học bài",
            "mở khóa học của con",
            "vào khóa học của con",
            "tiếp tục khóa học",
            "continue the course",
            "resume course",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, description)
                self.assertIn(phrase, _master_start_lesson_desc())

    def test_live_and_classic_share_child_product_toolset_modulo_music(self):
        conn = _ToolsetConn(runtime_enabled=True)

        classic = _classic_names(conn)
        live = _live_names(conn)
        music_exclusion = set(GoogleLiveProvider._LIVE_INCOMPATIBLE_TOOLS)

        self.assertEqual(classic - music_exclusion, live)
        self.assertIn("start_lesson", classic)
        self.assertIn("start_lesson", live)

    def test_child_product_toolset_excludes_adult_and_danger_tools(self):
        conn = _ToolsetConn(runtime_enabled=True)
        child_names = _classic_names(conn)
        adult_tools = {"web_search", "get_weather", "get_news_from_newsnow"}
        danger_regex = re.compile(
            r"(^|_)(delete|erase|format|factory|reset|reboot|shutdown|ota|firmware|flash|shell|exec|command)($|_)",
            re.IGNORECASE,
        )

        self.assertTrue(adult_tools.isdisjoint(child_names))
        self.assertEqual([], sorted(name for name in child_names if danger_regex.search(name)))

    def test_start_lesson_is_omitted_when_lesson_runtime_disabled(self):
        conn = _ToolsetConn(runtime_enabled=False)

        self.assertNotIn("start_lesson", _classic_names(conn))
        self.assertNotIn("start_lesson", _live_names(conn))
