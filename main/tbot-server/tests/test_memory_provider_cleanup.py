import unittest
import sys


def _reset_memory_imports():
    for module_name, required_attr in (
        ("config.manage_api_client", "generate_and_save_chat_summary"),
        ("core.utils.util", "check_model_key"),
    ):
        module = sys.modules.get(module_name)
        if module is not None and not hasattr(module, required_attr):
            sys.modules.pop(module_name, None)
    sys.modules.pop("core.providers.memory.mem_local_short.mem_local_short", None)


class MemoryProviderCleanupTest(unittest.IsolatedAsyncioTestCase):
    async def test_save_memory_without_llm_returns_without_attribute_error(self):
        _reset_memory_imports()
        from core.providers.memory.mem_local_short.mem_local_short import MemoryProvider

        provider = MemoryProvider({}, summary_memory="")

        result = await provider.save_memory(
            [
                type("Message", (), {"role": "user", "content": "xin chao"})(),
                type("Message", (), {"role": "assistant", "content": "chao ban"})(),
            ],
            session_id="session-1",
        )

        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
