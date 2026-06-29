import unittest
from types import SimpleNamespace
from unittest.mock import patch

from core.providers.memory.base import MemoryProviderBase
from core.providers.tools.server_plugins.plugin_executor import ServerPluginExecutor
from core.providers.vad.base import VADProviderBase
from core.voice.session_orchestrator import SessionMode, normalize_session_mode
from plugins_func.functions.handle_exit_intent import handle_exit_intent
from plugins_func.register import Action, ActionResponse, FunctionItem, ToolType


class _Conn(SimpleNamespace):
    def __init__(self):
        super().__init__(config={"plugins": {}}, close_after_chat=False)


class _BadExitConn:
    @property
    def close_after_chat(self):
        raise RuntimeError("bad close flag")


class ServerPluginExecutorBranchTest(unittest.IsolatedAsyncioTestCase):
    async def test_execute_maps_missing_function_and_all_tool_type_call_shapes(self):
        conn = _Conn()
        calls = []

        def system_tool(conn_arg, value):
            calls.append(("system", conn_arg is conn, value))
            return ActionResponse(Action.RESPONSE, response="system")

        def wait_tool(value):
            calls.append(("wait", value))
            return ActionResponse(Action.RESPONSE, response="wait")

        def prompt_tool(conn_arg, value):
            calls.append(("prompt", conn_arg is conn, value))
            return ActionResponse(Action.RESPONSE, response="prompt")

        def none_tool(value):
            calls.append(("none", value))
            return ActionResponse(Action.RESPONSE, response="none")

        def default_tool(value):
            calls.append(("default", value))
            return ActionResponse(Action.RESPONSE, response="default")

        async def async_tool(value):
            calls.append(("async", value))
            return ActionResponse(Action.RESPONSE, response="async")

        def broken_tool():
            raise RuntimeError("broken")

        registry_items = {
            "system_tool": FunctionItem("system_tool", {}, system_tool, ToolType.SYSTEM_CTL),
            "wait_tool": FunctionItem("wait_tool", {}, wait_tool, ToolType.WAIT),
            "prompt_tool": FunctionItem("prompt_tool", {}, prompt_tool, ToolType.CHANGE_SYS_PROMPT),
            "none_tool": FunctionItem("none_tool", {}, none_tool, ToolType.NONE),
            "default_tool": SimpleNamespace(name="default_tool", description={}, func=default_tool),
            "async_tool": FunctionItem("async_tool", {}, async_tool, ToolType.WAIT),
            "broken_tool": FunctionItem("broken_tool", {}, broken_tool, ToolType.WAIT),
        }
        with patch.dict(ServerPluginExecutor.execute.__globals__, {"all_function_registry": registry_items}):
            executor = ServerPluginExecutor(conn)

            missing = await executor.execute(conn, "missing", {})
            system = await executor.execute(conn, "system_tool", {"value": 1})
            wait = await executor.execute(conn, "wait_tool", {"value": 2})
            prompt = await executor.execute(conn, "prompt_tool", {"value": 3})
            none = await executor.execute(conn, "none_tool", {"value": 4})
            default = await executor.execute(conn, "default_tool", {"value": 5})
            async_result = await executor.execute(conn, "async_tool", {"value": 6})
            broken = await executor.execute(conn, "broken_tool", {})

        self.assertEqual(missing.action, Action.NOTFOUND)
        self.assertEqual(
            [system.response, wait.response, prompt.response, none.response, default.response, async_result.response],
            ["system", "wait", "prompt", "none", "default", "async"],
        )
        self.assertEqual(broken.action, Action.ERROR)
        self.assertEqual(broken.response, "broken")
        self.assertEqual(
            calls,
            [
                ("system", True, 1),
                ("wait", 2),
                ("prompt", True, 3),
                ("none", 4),
                ("default", 5),
                ("async", 6),
            ],
        )

    def test_get_tools_applies_config_description_and_news_source_description(self):
        conn = _Conn()
        conn.config = {
            "plugins": {
                "custom_tool": {"description": "Configured description"},
                "get_news_from_newsnow": {"news_sources": "A;B"},
            }
        }
        desc = {
            "function": {
                "name": "custom_tool",
                "description": "Original",
                "parameters": {"properties": {}},
            }
        }
        news_desc = {
            "function": {
                "name": "get_news_from_newsnow",
                "description": "News",
                "parameters": {"properties": {"source": {"description": "old"}}},
            }
        }
        registry_items = {
            "custom_tool": FunctionItem("custom_tool", desc, lambda: None, ToolType.WAIT),
            "get_news_from_newsnow": FunctionItem(
                "get_news_from_newsnow", news_desc, lambda: None, ToolType.WAIT
            ),
        }
        with patch.dict(
            ServerPluginExecutor.get_tools.__globals__,
            {
                "all_function_registry": registry_items,
                "product_tool_names": lambda _conn: ["custom_tool", "get_news_from_newsnow", "missing"],
            },
        ):
            executor = ServerPluginExecutor(conn)
            tools = executor.get_tools()

            self.assertTrue(executor.has_tool("custom_tool"))
            self.assertFalse(executor.has_tool("missing"))

        self.assertEqual(tools["custom_tool"].description["function"]["description"], "Configured description")
        self.assertIn("A、B", news_desc["function"]["parameters"]["properties"]["source"]["description"])

    def test_news_source_description_ignores_malformed_descriptions(self):
        conn = _Conn()
        executor = ServerPluginExecutor(conn)
        malformed = SimpleNamespace(description={"function": {"parameters": None}})

        executor._init_news_source_description(malformed, "missing_config")

        self.assertEqual(malformed.description, {"function": {"parameters": None}})


class ExitIntentAndProviderBaseTest(unittest.IsolatedAsyncioTestCase):
    async def test_exit_intent_defaults_goodbye_sets_close_flag_and_handles_errors(self):
        conn = _Conn()

        default = handle_exit_intent(conn)
        custom = handle_exit_intent(conn, "Bye")
        failed = handle_exit_intent(_BadExitConn(), "Bye")

        self.assertTrue(conn.close_after_chat)
        self.assertEqual(default.response, "Goodbye, wish you happy life!")
        self.assertEqual(custom.response, "Bye")
        self.assertEqual(failed.action, Action.NONE)
        self.assertEqual(failed.result, "Exit intent handling failed")

    async def test_provider_base_methods_are_noops_when_called_by_concrete_subclasses(self):
        class Memory(MemoryProviderBase):
            async def save_memory(self, msgs, session_id=None):
                return await super().save_memory(msgs, session_id=session_id)

            async def query_memory(self, query):
                return await super().query_memory(query)

        class Vad(VADProviderBase):
            def is_vad(self, conn, data):
                return super().is_vad(conn, data)

        memory = Memory({"enabled": True})
        memory.set_llm("llm-1")
        memory.init_memory("role-1", "llm-2")

        self.assertEqual(memory.config, {"enabled": True})
        self.assertEqual(memory.role_id, "role-1")
        self.assertEqual(memory.llm, "llm-2")
        self.assertIsNone(await memory.save_memory(["hello"], session_id="s-1"))
        self.assertEqual(await memory.query_memory("hello"), "please implement query method")
        self.assertIsNone(Vad().is_vad(None, b"audio"))

    async def test_session_mode_normalization_accepts_enum_and_string_values(self):
        self.assertIs(normalize_session_mode(SessionMode.LESSON), SessionMode.LESSON)
        self.assertIs(normalize_session_mode("DORMANT"), SessionMode.DORMANT)


if __name__ == "__main__":
    unittest.main()
