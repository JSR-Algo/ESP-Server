import importlib
import sys
import types


class _Logger:
    def __init__(self):
        self.records = []

    def bind(self, **kwargs):
        self.records.append(("bind", kwargs))
        return self

    def debug(self, message):
        self.records.append(("debug", message))

    def info(self, message):
        self.records.append(("info", message))

    def warning(self, message):
        self.records.append(("warning", message))

    def error(self, message):
        self.records.append(("error", message))


class _CacheType:
    CONFIG = "config"
    DEVICE_PROMPT = "device_prompt"
    LOCATION = "location"
    WEATHER = "weather"


class _Cache:
    def __init__(self):
        self.values = {}
        self.sets = []

    def get(self, cache_type, key):
        return self.values.get((cache_type, key))

    def set(self, cache_type, key, value):
        self.sets.append((cache_type, key, value))
        self.values[(cache_type, key)] = value


class _ContextProvider:
    def __init__(self):
        self.calls = []

    def fetch_all(self, device_id):
        self.calls.append(device_id)
        return f"context:{device_id}"


def _prompt_module():
    sys.modules.pop("core.utils.prompt_manager", None)
    return importlib.import_module("core.utils.prompt_manager")


def _manager(prompt_module, config=None):
    manager = prompt_module.PromptManager.__new__(prompt_module.PromptManager)
    manager.config = config or {}
    manager.logger = _Logger()
    manager.base_prompt_template = None
    manager.last_update_time = 0
    manager.cache_manager = _Cache()
    manager.CacheType = _CacheType
    manager.context_provider = _ContextProvider()
    manager.context_data = ""
    return manager


def test_load_base_template_cache_file_missing_and_error_paths(tmp_path):
    prompt_module = _prompt_module()
    default_path = _manager(prompt_module, {})
    default_path._load_base_template()
    assert default_path.base_prompt_template is not None or any(
        level == "warning" and "agent-base-prompt.txt" in msg for level, msg in default_path.logger.records
    )

    cached = _manager(prompt_module, {"prompt_template": "cached.txt"})
    cached.cache_manager.values[(_CacheType.CONFIG, "prompt_template:cached.txt")] = "cached prompt"
    cached._load_base_template()
    assert cached.base_prompt_template == "cached prompt"

    template_path = tmp_path / "prompt.txt"
    template_path.write_text("file prompt", encoding="utf-8")
    loaded = _manager(prompt_module, {"prompt_template": str(template_path)})
    loaded._load_base_template()
    assert loaded.base_prompt_template == "file prompt"
    assert loaded.cache_manager.values[(_CacheType.CONFIG, f"prompt_template:{template_path}")] == "file prompt"

    missing = _manager(prompt_module, {"prompt_template": str(tmp_path / "missing.txt")})
    missing._load_base_template()
    assert any(level == "warning" and "file not found" in msg for level, msg in missing.logger.records)

    broken = _manager(prompt_module, {"prompt_template": str(template_path)})
    broken.cache_manager.get = lambda *args: (_ for _ in ()).throw(RuntimeError("cache down"))
    broken._load_base_template()
    assert any(level == "error" and "cache down" in msg for level, msg in broken.logger.records)


def test_quick_prompt_time_location_and_weather_paths(monkeypatch):
    prompt_module = _prompt_module()
    manager = _manager(prompt_module)
    manager.cache_manager.values[(_CacheType.DEVICE_PROMPT, "device_prompt:robot")] = "cached prompt"
    assert manager.get_quick_prompt("incoming", "robot") == "cached prompt"
    assert manager.get_quick_prompt("incoming", None) == "incoming"
    assert manager.get_quick_prompt("fresh", "robot-2") == "fresh"
    assert manager.cache_manager.values[(_CacheType.DEVICE_PROMPT, "device_prompt:robot-2")] == "fresh"

    date_info = manager._get_current_time_info()
    assert len(date_info) == 3
    assert date_info[2].endswith("\n")

    manager.cache_manager.values[(_CacheType.LOCATION, "1.1.1.1")] = "Cached City"
    assert manager._get_location_info("1.1.1.1") == "Cached City"

    util_module = importlib.import_module("core.utils.util")
    monkeypatch.setattr(util_module, "get_ip_info", lambda ip, logger: {"city": "Live City"})
    assert manager._get_location_info("2.2.2.2") == "Live City"
    assert manager.cache_manager.values[(_CacheType.LOCATION, "2.2.2.2")] == "Live City"

    monkeypatch.setattr(util_module, "get_ip_info", lambda ip, logger: (_ for _ in ()).throw(RuntimeError("geo down")))
    assert manager._get_location_info("3.3.3.3") == "Unknown location"

    class ActionResponse:
        def __init__(self, result):
            self.result = result

    weather_module = types.ModuleType("plugins_func.functions.get_weather")
    weather_module.get_weather = lambda conn, location, lang: ActionResponse(f"weather:{location}:{lang}")
    register_module = types.ModuleType("plugins_func.register")
    register_module.ActionResponse = ActionResponse
    monkeypatch.setitem(sys.modules, "plugins_func.functions.get_weather", weather_module)
    monkeypatch.setitem(sys.modules, "plugins_func.register", register_module)
    assert manager._get_weather_info(object(), "Live City") == "weather:Live City:zh_CN"
    assert manager._get_weather_info(object(), "Live City") == "weather:Live City:zh_CN"
    weather_module.get_weather = lambda conn, location, lang: object()
    assert manager._get_weather_info(object(), "Other City") == "Get weather info failed"
    weather_module.get_weather = lambda conn, location, lang: (_ for _ in ()).throw(RuntimeError("weather down"))
    assert manager._get_weather_info(object(), "Broken City") == "Get weather info failed"


def test_update_context_info_caches_location_weather_and_dynamic_context():
    prompt_module = _prompt_module()
    manager = _manager(prompt_module)
    manager.base_prompt_template = "local_address weather_info dynamic_context"
    manager._get_location_info = lambda ip: "Hanoi"
    weather_calls = []
    manager._get_weather_info = lambda conn, location: weather_calls.append((conn, location))
    conn = types.SimpleNamespace(device_id="robot-1")

    manager.update_context_info(conn, "127.0.0.1")
    assert weather_calls == [(conn, "Hanoi")]
    assert manager.context_provider.calls == ["robot-1"]
    assert manager.context_data == "context:robot-1"

    manager.base_prompt_template = "local_address"
    manager.update_context_info(conn, "127.0.0.1")
    assert manager.context_data == ""

    manager._get_location_info = lambda ip: (_ for _ in ()).throw(RuntimeError("context down"))
    manager.update_context_info(conn, "127.0.0.1")
    assert any(level == "error" and "context down" in msg for level, msg in manager.logger.records)


def test_build_enhanced_prompt_success_fallback_and_child_profile_aliases():
    prompt_module = _prompt_module()
    manager = _manager(
        prompt_module,
        {
            "selected_module": {"TTS": "edge"},
            "TTS": {"edge": {"language": "English"}},
            "child_profile": {"childName": "Bong", "childAge": 6, "deviceAlias": "Bedroom"},
        },
    )
    assert manager.build_enhanced_prompt("base", "robot", "1.1.1.1") == "base"

    manager.base_prompt_template = (
        "{{base_prompt}}|{{today_date}}|{{today_weekday}}|{{lunar_date}}|{{local_address}}|"
        "{{weather_info}}|{{device_id}}|{{client_ip}}|{{dynamic_context}}|{{language}}|"
        "{{ child_profile.child_name }}|{{ child_profile.child_age }}|{{ child_profile.device_alias }}"
    )
    manager.context_data = "dynamic"
    manager._get_current_time_info = lambda: ("2026-06-20", "Saturday", "lunar")
    manager.cache_manager.values[(_CacheType.LOCATION, "1.1.1.1")] = "Hanoi"
    manager.cache_manager.values[(_CacheType.WEATHER, "Hanoi")] = "Sunny"

    prompt = manager.build_enhanced_prompt("base", "robot", "1.1.1.1")
    assert prompt == "base|2026-06-20|Saturday|lunar|Hanoi|Sunny|robot|1.1.1.1|dynamic|English|Bong|6|Bedroom"
    assert manager.cache_manager.values[(_CacheType.DEVICE_PROMPT, "device_prompt:robot")] == prompt

    override = manager.build_enhanced_prompt(
        "base",
        "robot-2",
        None,
        child_profile={"child_name": "An", "interests": ["space"], "parentCareer": "engineer"},
    )
    assert "An" in override
    assert manager._normalize_child_profile(None) == {}
    assert manager._normalize_child_profile({"child_name": "", "learningStyle": "visual"}) == {"learning_style": "visual"}

    manager.base_prompt_template = "{{"
    assert manager.build_enhanced_prompt("fallback", "robot", None) == "fallback"
