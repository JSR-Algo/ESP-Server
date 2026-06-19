from pathlib import Path
import importlib
import sys


class SilentLogger:
    def bind(self, **kwargs):
        return self

    def debug(self, *args, **kwargs):
        return None

    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None

    def error(self, *args, **kwargs):
        return None


def test_enhanced_prompt_renders_child_profile_from_private_config(tmp_path: Path):
    sys.modules.pop("core.utils.prompt_manager", None)
    prompt_manager_module = importlib.import_module("core.utils.prompt_manager")
    PromptManager = prompt_manager_module.PromptManager

    template_path = tmp_path / "prompt.txt"
    template_path.write_text(
        "{{base_prompt}}\n"
        "Child name: {{ child_profile.child_name }}\n"
        "Child age: {{ child_profile.child_age }}\n"
        "Device: {{ child_profile.device_alias }}\n",
        encoding="utf-8",
    )
    config = {
        "prompt_template": str(template_path),
        "selected_module": {},
        "TTS": {},
        "child_profile": {
            "child_name": "Bong",
            "child_age": 6,
            "device_alias": "Robot phong ngu",
        },
    }

    manager = PromptManager(config, SilentLogger())
    manager._get_current_time_info = lambda: ("2026-06-19", "Friday", "")
    manager.cache_manager.clear(manager.CacheType.DEVICE_PROMPT)

    prompt = manager.build_enhanced_prompt(
        "Base role",
        "child-profile-test-device",
        None,
    )

    assert "Child name: Bong" in prompt
    assert "Child age: 6" in prompt
    assert "Device: Robot phong ngu" in prompt
