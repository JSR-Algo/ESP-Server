import unittest

from core.voice import child_safety
from plugins_func import register as plugin_register
from plugins_func.functions.change_role import change_role


class _Conn:
    def __init__(self):
        self.prompts = []

    def change_system_prompt(self, prompt):
        self.prompts.append(prompt)


class PluginRegistryTest(unittest.TestCase):
    def setUp(self):
        self._original_registry = dict(plugin_register.all_function_registry)

    def tearDown(self):
        plugin_register.all_function_registry.clear()
        plugin_register.all_function_registry.update(self._original_registry)

    def test_device_type_registry_generates_stable_type_ids_and_keeps_first_registration(self):
        registry = plugin_register.DeviceTypeRegistry()
        descriptor = {
            "name": "servo",
            "properties": {"z": {}, "a": {}},
            "methods": {"turn": {}, "center": {}},
        }

        type_id = registry.generate_device_type_id(descriptor)
        registry.register_device_type(type_id, {"turn": object()})
        registry.register_device_type(type_id, {"center": object()})

        self.assertEqual(type_id, "servo:a,z:center,turn")
        self.assertEqual(list(registry.get_device_functions(type_id)), ["turn"])
        self.assertEqual(registry.get_device_functions("missing"), {})

    def test_function_decorators_and_registry_lifecycle(self):
        desc = {"type": "function", "function": {"name": "demo"}}

        @plugin_register.register_function("demo", desc, plugin_register.ToolType.WAIT)
        def demo():
            return "ok"

        @plugin_register.register_device_function("device_demo", desc, plugin_register.ToolType.IOT_CTL)
        def device_demo():
            return "device"

        direct_item = plugin_register.FunctionItem(
            "direct", desc, lambda: "direct", plugin_register.ToolType.SYSTEM_CTL
        )
        registry = plugin_register.FunctionRegistry()

        self.assertIs(plugin_register.all_function_registry["demo"].func, demo)
        self.assertIs(plugin_register.all_function_registry["demo"].description, desc)
        self.assertEqual(plugin_register.all_function_registry["demo"].type, plugin_register.ToolType.WAIT)
        self.assertEqual(device_demo(), "device")
        self.assertIs(registry.register_function("direct", direct_item), direct_item)
        self.assertIs(registry.register_function("demo"), plugin_register.all_function_registry["demo"])
        self.assertIsNone(registry.register_function("missing"))
        self.assertIs(registry.get_function("demo"), plugin_register.all_function_registry["demo"])
        self.assertIn(desc, registry.get_all_function_desc())
        self.assertIs(registry.get_all_functions(), registry.function_registry)
        self.assertFalse(registry.unregister_function("missing"))
        self.assertTrue(registry.unregister_function("demo"))
        self.assertIsNone(registry.get_function("demo"))


class ChildSafetyAndChangeRoleTest(unittest.TestCase):
    def test_child_safety_block_handles_empty_existing_and_unsafe_model_output(self):
        existing = f"{child_safety.CHILD_SAFETY_BLOCK}\n\nHello"

        self.assertEqual(
            child_safety.ensure_child_safety_block(None),
            child_safety.CHILD_SAFETY_BLOCK,
        )
        self.assertEqual(
            child_safety.ensure_child_safety_block(existing).count("<child_safety>"),
            1,
        )
        self.assertEqual(
            child_safety.screen_model_output("What is your phone number?"),
            {"blocked": True, "reason": "pii_phone"},
        )
        self.assertEqual(
            child_safety.screen_model_output(None),
            {"blocked": False, "reason": None},
        )

    def test_child_safety_block_contains_vietnamese_rules_for_child_lesson_context(self):
        block = child_safety.ensure_child_safety_block("You are TBot.")

        self.assertIn("Người dùng: trẻ em Việt Nam", block)
        self.assertIn("Chống dụ dỗ", block)
        self.assertIn("câu luyện tiếng Anh an toàn", block)

    def test_change_role_rejects_unsupported_role_without_mutating_prompt(self):
        conn = _Conn()

        response = change_role(conn, "Unsupported", "Bot")

        self.assertEqual(response.result, "Switch role failed")
        self.assertEqual(response.response, "Unsupported role")
        self.assertEqual(conn.prompts, [])


if __name__ == "__main__":
    unittest.main()
