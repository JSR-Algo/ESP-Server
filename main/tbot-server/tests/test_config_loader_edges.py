import asyncio
import importlib.util
import sys
import types
from pathlib import Path

import pytest

config_loader = None


def _load_real_module(module_name, path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_real_config_loader():
    root = Path(__file__).resolve().parents[1]
    previous_manage_api = sys.modules.get("config.manage_api_client")
    sentinel = object()
    if previous_manage_api is None:
        previous_manage_api = sentinel
    real_manage_api = _load_real_module(
        "config.manage_api_client",
        root / "config" / "manage_api_client.py",
    )
    sys.modules["config.manage_api_client"] = real_manage_api
    try:
        return _load_real_module(
            "config._config_loader_edges",
            root / "config" / "config_loader.py",
        )
    finally:
        if previous_manage_api is sentinel:
            sys.modules.pop("config.manage_api_client", None)
        else:
            sys.modules["config.manage_api_client"] = previous_manage_api


def _clear_config_cache():
    from core.utils.cache.config import CacheType
    from core.utils.cache.manager import cache_manager

    cache_manager.clear(CacheType.CONFIG)


@pytest.fixture(autouse=True)
def clean_config_cache():
    global config_loader
    config_loader = _load_real_config_loader()
    _clear_config_cache()
    yield
    _clear_config_cache()


def test_normalize_and_env_helpers_handle_non_mappings_and_bad_values(monkeypatch):
    assert config_loader.normalize_voice_config(None)["voice_mode"]["type"] == "classic_pipeline"
    repaired_live = config_loader.normalize_voice_config(
        {"voice_mode": {"type": "google_live"}, "google_live": "bad"}
    )
    assert repaired_live["google_live"]["language_code"] == "vi-VN"
    assert config_loader._apply_lesson_env_overrides(None) is None
    assert config_loader._apply_connection_env_overrides(None) is None
    assert config_loader._apply_server_endpoint_env_overrides(None) is None
    assert config_loader._clean_env("MISSING_ENV") is None

    monkeypatch.setenv("LESSON_RUNTIME_ENABLED", "yes")
    monkeypatch.setenv("COURSE_BACKEND_URL", "https://course.test/v1/")
    monkeypatch.setenv("LESSON_ASSET_ORIGIN_BASE", "https://assets.origin/")
    monkeypatch.setenv("LESSON_ASSET_PUBLIC_BASE_URL", "https://assets.public/")
    monkeypatch.setenv("LESSON_ASSET_DELIVERY_MODE", "sd_pack")
    monkeypatch.setenv("LESSON_ASSET_PACK_LOCAL_ROOT", "/tmp/pack/")
    monkeypatch.setenv("LESSON_ASSET_PACK_MOUNT_ROOT", "/mnt/sd/")
    monkeypatch.setenv("LESSON_STEP_TIMEOUT_FLOOR_SEC", "bad-float")
    config = {"lesson": "bad", "server": "bad"}

    result = config_loader._apply_lesson_env_overrides(config)

    assert result["lesson"]["runtime_enabled"] is True
    assert result["lesson"]["asset_origin_base"] == "https://assets.origin"
    assert result["lesson"]["asset_public_base_url"] == "https://assets.public"
    assert result["lesson"]["asset_delivery_mode"] == "sd_pack"
    assert result["lesson"]["asset_pack_local_root"] == "/tmp/pack"
    assert result["lesson"]["asset_pack_mount_root"] == "/mnt/sd"
    assert "step_timeout_floor_sec" not in result["lesson"]
    assert result["lesson"]["api_base"] == "https://course.test/v1"
    assert result["server"]["api_url"] == "https://course.test/v1"

    monkeypatch.setenv("LESSON_STEP_TIMEOUT_FLOOR_SEC", "-3")
    config = {"lesson": {}, "server": {}}
    assert config_loader._apply_lesson_env_overrides(config)["lesson"]["step_timeout_floor_sec"] == 0.0

    monkeypatch.setenv("TBOT_CLOSE_CONNECTION_NO_VOICE_TIME", "bad-int")
    config = {}
    assert "close_connection_no_voice_time" not in config_loader._apply_connection_env_overrides(config)
    monkeypatch.setenv("TBOT_CLOSE_CONNECTION_NO_VOICE_TIME", "0")
    assert config_loader._apply_connection_env_overrides(config)["close_connection_no_voice_time"] == 1
    monkeypatch.setenv("TBOT_ALLOW_DEVICE_CONFIG_FALLBACK", "true")
    assert config_loader._apply_connection_env_overrides(config)["allow_device_config_fallback"] is True
    monkeypatch.setenv("TBOT_ALLOW_DEVICE_CONFIG_FALLBACK", "false")
    assert config_loader._apply_connection_env_overrides(config)["allow_device_config_fallback"] is False


def test_voice_env_overrides_force_google_live_and_wake_words(monkeypatch):
    monkeypatch.setenv("TBOT_VOICE_MODE", "google_live")
    monkeypatch.setenv("GOOGLE_API_KEY", "google-key")
    monkeypatch.setenv("TBOT_GOOGLE_LIVE_MODEL", "gemini-live-test")
    monkeypatch.setenv("TBOT_GOOGLE_LIVE_LANGUAGE_CODE", "vi-VN")
    monkeypatch.setenv("TBOT_GOOGLE_LIVE_VOICE_NAME", "Kore")
    monkeypatch.setenv("TBOT_WAKEUP_WORDS", "Hi Tâm,Hi ESP,TBOT")
    config = {
        "voice_mode": {"type": "classic_pipeline"},
        "google_live": "bad",
        "wakeup_words": ["TBOT"],
    }

    result = config_loader._apply_server_endpoint_env_overrides(config)
    result = config_loader.normalize_voice_config(result)

    assert result["voice_mode"]["type"] == "google_live"
    assert result["voice_mode"]["fallback_to_classic_on_error"] is False
    assert result["google_live"]["api_key"] == "google-key"
    assert result["google_live"]["model"] == "gemini-live-test"
    assert result["google_live"]["language_code"] == "vi-VN"
    assert result["google_live"]["voice_name"] == "Kore"
    assert result["wakeup_words"] == ["TBOT", "Hi Tâm", "Hi ESP"]


def test_google_live_enabled_env_forces_voice_mode_without_voice_mode_name(monkeypatch):
    monkeypatch.setenv("TBOT_GOOGLE_LIVE_ENABLED", "true")
    config = {"voice_mode": {"type": "classic_pipeline"}}

    result = config_loader._apply_voice_env_overrides(config)

    assert result["voice_mode"]["type"] == "google_live"


def test_google_live_defaults_pin_single_robot_voice():
    config = {"voice_mode": {"type": "google_live"}, "google_live": {}}

    result = config_loader.normalize_voice_config(config)

    assert result["google_live"]["voice_name"] == "Kore"

def test_google_live_runtime_policy_ignores_voice_env_override(monkeypatch):
    monkeypatch.setenv("TBOT_GOOGLE_LIVE_VOICE_NAME", "Aoede")
    config = {"voice_mode": {"type": "google_live"}, "google_live": {}}

    result = config_loader.normalize_voice_config(config)

    assert result["google_live"]["voice_name"] == "Kore"

@pytest.mark.asyncio
async def test_private_config_honors_session_resumption_env_override(monkeypatch):
    monkeypatch.setenv("TBOT_GOOGLE_LIVE_SESSION_RESUMPTION_ENABLED", "false")

    async def fake_get_agent_models(_device_id, _client_id, _selected_module):
        return {
            "voice_mode": {"type": "google_live"},
            "google_live": {"model": "gemini-live-private"},
        }

    async def fake_get_correct_words(_device_id):
        return None

    monkeypatch.setattr(config_loader, "get_agent_models", fake_get_agent_models)
    monkeypatch.setattr(config_loader, "get_correct_words", fake_get_correct_words)

    result = await config_loader.get_private_config_from_api(
        {"selected_module": {}},
        "device-1",
        "client-1",
    )

    assert result["google_live"]["session_resumption_enabled"] is False


@pytest.mark.asyncio
async def test_private_config_inherits_base_google_live_for_external_and_lesson(monkeypatch):
    async def fake_get_agent_models(_device_id, _client_id, _selected_module):
        return {
            "voice_mode": {"type": "classic_pipeline"},
            "google_live": {"model": "agent-live-model"},
            "lesson": {"runtime_enabled": True},
        }

    async def fake_get_correct_words(_device_id):
        return None

    monkeypatch.setattr(config_loader, "get_agent_models", fake_get_agent_models)
    monkeypatch.setattr(config_loader, "get_correct_words", fake_get_correct_words)

    result = await config_loader.get_private_config_from_api(
        {
            "selected_module": {},
            "voice_mode": {"type": "google_live", "fallback_to_classic_on_error": False},
            "google_live": {"model": "base-live-model", "voice_name": "Kore"},
        },
        "device-1",
        "client-1",
    )

    assert result["voice_mode"]["type"] == "google_live"
    assert result["voice_mode"]["fallback_to_classic_on_error"] is False
    assert result["google_live"]["model"] == "agent-live-model"
    assert result["google_live"]["voice_name"] == "Kore"
    assert result["lesson"]["runtime_enabled"] is True

def test_voice_env_accepts_google_gemini_api_key_alias(monkeypatch):
    monkeypatch.setenv("GOOGLE_GEMINI_API_KEY", "google-gemini-key")
    config = {"voice_mode": {"type": "google_live"}, "google_live": {}}

    result = config_loader._apply_voice_env_overrides(config)

    assert result["google_live"]["api_key"] == "google-gemini-key"


def test_gemini_tts_inherits_google_live_api_key_when_config_key_is_placeholder():
    agent_key = "live-key-from-agent-xxxxxxxx"  # >=20 chars = non-placeholder
    config = {
        "voice_mode": {"type": "google_live"},
        "google_live": {"api_key": agent_key},
        "selected_module": {"TTS": "TTS_Gemini25ProTTS"},
        "TTS": {
            "TTS_Gemini25ProTTS": {
                "type": "gemini",
                "api_key": "test-key",
                "model_name": "gemini-2.5-pro-preview-tts",
            }
        },
    }

    result = config_loader.normalize_voice_config(config)

    assert result["TTS"]["TTS_Gemini25ProTTS"]["api_key"] == agent_key


def test_all_gemini_modules_forced_to_agent_google_live_api_key():
    """Role-tab google_live.api_key wins over per-module ASR/LLM/TTS secrets."""
    agent_key = "AQ." + ("y" * 40)
    stale_asr = "AIza" + ("a" * 35)
    stale_tts = "AIza" + ("t" * 35)
    config = {
        "voice_mode": {"type": "google_live"},
        "google_live": {"api_key": agent_key},
        "selected_module": {
            "ASR": "ASR_GeminiASR",
            "LLM": "LLM_GeminiProLLM",
            "TTS": "TTS_Gemini25ProTTS",
        },
        "ASR": {
            "ASR_GeminiASR": {"type": "gemini_asr", "api_key": stale_asr},
        },
        "LLM": {
            "LLM_GeminiProLLM": {"type": "gemini", "api_key": "??api_key"},
        },
        "TTS": {
            "TTS_Gemini25ProTTS": {
                "type": "gemini",
                "api_key": stale_tts,
                "model_name": "gemini-2.5-pro-preview-tts",
            }
        },
    }

    result = config_loader.normalize_voice_config(config)

    assert result["google_live"]["api_key"] == agent_key
    assert result["ASR"]["ASR_GeminiASR"]["api_key"] == agent_key
    assert result["LLM"]["LLM_GeminiProLLM"]["api_key"] == agent_key
    assert result["TTS"]["TTS_Gemini25ProTTS"]["api_key"] == agent_key


def test_gemini_modules_backfill_live_key_when_agent_live_key_missing():
    module_key = "AIza" + ("m" * 35)
    config = {
        "voice_mode": {"type": "google_live"},
        "google_live": {"api_key": "${GOOGLE_API_KEY}"},
        "ASR": {"ASR_GeminiASR": {"type": "gemini_asr", "api_key": module_key}},
        "TTS": {"GeminiTTS": {"type": "gemini", "api_key": "??api_key"}},
    }

    result = config_loader.normalize_voice_config(config)

    assert result["google_live"]["api_key"] == module_key
    assert result["TTS"]["GeminiTTS"]["api_key"] == module_key


def test_gemini_tts_gets_edge_fallback_by_default(monkeypatch):
    monkeypatch.setenv("TBOT_EDGE_TTS_VOICE", "en-US-JennyNeural")
    config = {
        "voice_mode": {"type": "google_live"},
        "google_live": {"api_key": "live-key-from-agent"},
        "selected_module": {"TTS": "TTS_Gemini25ProTTS"},
        "TTS": {
            "TTS_Gemini25ProTTS": {
                "type": "gemini",
                "api_key": "test-key",
                "model_name": "gemini-2.5-pro-preview-tts",
            }
        },
    }

    result = config_loader.normalize_voice_config(config)

    assert result["selected_module"]["TTS"] == "TTS_Gemini25ProTTS"
    assert result["TTS"]["TTS_Gemini25ProTTS"]["fallback_tts"] == {
        "type": "edge",
        "voice": "en-US-JennyNeural",
        "output_dir": "tmp/",
    }


def test_gemini_tts_edge_fallback_defaults_to_vietnamese_voice(monkeypatch):
    monkeypatch.delenv("TBOT_EDGE_TTS_VOICE", raising=False)
    config = {
        "voice_mode": {"type": "google_live"},
        "google_live": {"api_key": "live-key-from-agent"},
        "selected_module": {"TTS": "GeminiTTS"},
        "TTS": {
            "GeminiTTS": {
                "type": "gemini",
                "api_key": "test-key",
                "model_name": "gemini-3.1-flash-tts",
            }
        },
    }

    result = config_loader.normalize_voice_config(config)

    assert result["TTS"]["GeminiTTS"]["fallback_tts"] == {
        "type": "edge",
        "voice": "vi-VN-HoaiMyNeural",
        "output_dir": "tmp/",
    }

def test_tts_provider_env_can_force_edge_tts(monkeypatch):
    monkeypatch.setenv("TBOT_TTS_PROVIDER", "edge")
    monkeypatch.setenv("TBOT_EDGE_TTS_VOICE", "en-US-JennyNeural")
    config = {
        "selected_module": {"TTS": "TTS_Gemini25ProTTS"},
        "TTS": {"TTS_Gemini25ProTTS": {"type": "gemini", "api_key": "test-key"}},
    }

    result = config_loader.normalize_voice_config(config)

    assert result["selected_module"]["TTS"] == "EdgeTTS"
    assert result["TTS"]["EdgeTTS"] == {
        "type": "edge",
        "voice": "en-US-JennyNeural",
        "output_dir": "tmp/",
    }


def test_lesson_runtime_auto_enables_when_production_prereqs_are_present(monkeypatch):
    monkeypatch.delenv("LESSON_RUNTIME_ENABLED", raising=False)
    monkeypatch.setenv("COURSE_BACKEND_URL", "https://course.test/v1/")
    monkeypatch.setenv("LESSON_ASSET_ORIGIN_BASE", "https://assets.origin/")
    monkeypatch.setenv("TBOT_DEVICE_MINT_SECRET", "mint-secret")
    config = {"lesson": {"runtime_enabled": False}, "server": {}}

    result = config_loader._apply_lesson_env_overrides(config)

    assert result["lesson"]["runtime_enabled"] is True


def test_explicit_lesson_runtime_false_env_wins_over_auto_enable(monkeypatch):
    monkeypatch.setenv("LESSON_RUNTIME_ENABLED", "false")
    monkeypatch.setenv("COURSE_BACKEND_URL", "https://course.test/v1/")
    monkeypatch.setenv("LESSON_ASSET_ORIGIN_BASE", "https://assets.origin/")
    monkeypatch.setenv("TBOT_DEVICE_MINT_SECRET", "mint-secret")
    config = {"lesson": {"runtime_enabled": True}, "server": {}}

    result = config_loader._apply_lesson_env_overrides(config)

    assert result["lesson"]["runtime_enabled"] is False


def test_server_endpoint_overrides_repair_bad_shapes_and_sync_lesson_api(monkeypatch):
    monkeypatch.setenv("TBOT_PUBLIC_WEBSOCKET_URL", "wss://public.test/ws")
    monkeypatch.setenv("TBOT_BACKEND_API_URL", "https://backend.test/v1/")
    monkeypatch.setenv("TBOT_SERVER_AUTH_KEY", "env-auth-key")
    config = {"server": "bad"}

    result = config_loader._apply_server_endpoint_env_overrides(config)

    assert result["server"] == {
        "websocket": "wss://public.test/ws",
        "api_url": "https://backend.test/v1",
        "auth_key": "env-auth-key",
    }

    config = {"server": {"api_url": "https://old.test/v1"}, "lesson": {"api_base": "https://old.test/v1"}}
    result = config_loader._apply_server_endpoint_env_overrides(config)
    assert result["lesson"]["api_base"] == "https://backend.test/v1"

    config = {"server": {"api_url": "https://old.test/v1"}, "lesson": {"api_base": "https://custom.test/v1"}}
    result = config_loader._apply_server_endpoint_env_overrides(config)
    assert result["lesson"]["api_base"] == "https://backend.test/v1"

def test_server_auth_enabled_env_override_wins_without_endpoint_env(monkeypatch):
    monkeypatch.setenv("TBOT_SERVER_AUTH_ENABLED", "true")
    config = {"server": {"auth": {"enabled": False}}}

    result = config_loader._apply_server_endpoint_env_overrides(config)

    assert result["server"]["auth"]["enabled"] is True


def test_boot_safety_edges(monkeypatch):
    assert config_loader._assert_lesson_runtime_boot_safe(None) is None

    monkeypatch.setenv("NODE_ENV", "production")
    with pytest.raises(RuntimeError, match="server.auth.enabled"):
        config_loader._assert_production_boot_safe(None)

    for name, value in {
        "TBOT_REQUIRE_DEVICE_TOKEN": "true",
        "JWT_PUBLIC_KEY": "key",
        "TBOT_DEVICE_MINT_SECRET": "secret",
        "LESSON_ASSET_ORIGIN_BASE": "https://assets.test",
    }.items():
        monkeypatch.setenv(name, value)

    import core.voice.aec.aec_processor as aec_processor

    monkeypatch.setattr(
        aec_processor,
        "AecProcessor",
        lambda **_kwargs: types.SimpleNamespace(bypassed=False, reason=None),
    )
    with pytest.raises(RuntimeError, match="voice_mode.type=google_live"):
        config_loader._assert_production_boot_safe(
            {
                "server": {"auth": {"enabled": True}, "auth_key": "auth-key"},
                "voice_mode": {"type": "classic_pipeline"},
            }
        )
    config_loader._assert_production_boot_safe(
        {
            "server": {"auth": {"enabled": True}, "auth_key": "auth-key"},
            "voice_mode": {"type": "google_live"},
            "google_live": "bad",
        }
    )


def test_load_config_returns_cached_value(monkeypatch):
    from core.utils.cache.config import CacheType
    from core.utils.cache.manager import cache_manager

    cached = {"cached": True}
    cache_manager.set(CacheType.CONFIG, "main_config", cached)
    monkeypatch.setattr(config_loader, "read_config", lambda _path: (_ for _ in ()).throw(AssertionError("no read")))

    assert config_loader.load_config() is cached


def test_load_config_manager_api_sync_path_and_cache(monkeypatch):
    calls = []
    monkeypatch.setattr(config_loader, "read_config", lambda path: {"manager-api": {"url": "http://manager", "secret": "s"}} if path.endswith("data/.config.yaml") else {})
    monkeypatch.setattr(config_loader, "get_project_dir", lambda: "/project/")
    monkeypatch.setattr(config_loader, "ensure_directories", lambda config: calls.append(("ensure", config)))

    async def fake_api_config(custom_config):
        calls.append(("api", custom_config))
        return {"server": {"auth": {"enabled": False}}, "voice_mode": {"type": "classic_pipeline"}}

    monkeypatch.setattr(config_loader, "get_config_from_api_async", fake_api_config)

    result = config_loader.load_config()

    assert result["voice_mode"]["type"] == "classic_pipeline"
    assert calls[0][0] == "api"
    assert calls[1] == ("ensure", result)


@pytest.mark.asyncio
async def test_load_config_async_paths_and_cache(monkeypatch):
    calls = []
    monkeypatch.setattr(config_loader, "get_project_dir", lambda: "/project/")
    monkeypatch.setattr(config_loader, "ensure_directories", lambda config: calls.append(("ensure", config)))
    monkeypatch.setattr(config_loader, "read_config", lambda path: {"server": {"auth": {"enabled": False}}} if path.endswith("config.yaml") else {"voice_mode": {"type": "classic_pipeline"}})

    result = await config_loader.load_config_async()
    assert result["server"]["auth"]["enabled"] is False
    assert result["voice_mode"]["type"] == "classic_pipeline"
    assert calls == [("ensure", result)]

    calls.clear()
    assert await config_loader.load_config_async() is result
    assert calls == []

    _clear_config_cache()
    monkeypatch.setattr(config_loader, "read_config", lambda path: {"manager-api": {"url": "http://manager", "secret": "s"}} if path.endswith("data/.config.yaml") else {})

    async def fake_api_config(custom_config):
        calls.append(("api", custom_config))
        return {"server": {"auth": {"enabled": False}}}

    monkeypatch.setattr(config_loader, "get_config_from_api_async", fake_api_config)
    result = await config_loader.load_config_async()
    assert calls[0][0] == "api"
    assert result["voice_mode"]["type"] == "classic_pipeline"

    _clear_config_cache()
    calls.clear()
    monkeypatch.setattr(config_loader, "read_config", lambda _path: None)
    result = await config_loader.load_config_async()
    assert result["voice_mode"]["type"] == "classic_pipeline"
    assert calls == [("ensure", result)]


@pytest.mark.asyncio
async def test_get_config_from_api_async_edges(monkeypatch):
    calls = []
    monkeypatch.setattr(config_loader, "init_service", lambda config: calls.append(("init", config)))

    async def no_server_config():
        return None

    monkeypatch.setattr(config_loader, "get_server_config", no_server_config)
    with pytest.raises(Exception, match="Failed to fetch server config"):
        await config_loader.get_config_from_api_async({"manager-api": {"url": "http://m", "secret": "s"}})

    async def server_config():
        return {"server": {"auth": {"enabled": True}}, "prompt_template": None}

    monkeypatch.setattr(config_loader, "get_server_config", server_config)
    config = await config_loader.get_config_from_api_async(
        {
            "manager-api": {"url": "http://m", "secret": "s"},
            "server": {
                "ip": "0.0.0.0",
                "port": 8000,
                "http_port": 8003,
                "websocket": "ws://local",
                "api_url": "http://api",
                "vision_explain": "http://vision",
                "firmware_download_scheme": "http",
                "factory_test_claimed_devices": ["aa:bb"],
                "claim_reset_devices": ["14:c1:9f:d1:ac:20"],
                "claim_reset_nonce": "repair-2026-06-22",
                "auth_key": "auth",
            },
            "prompt_template": {"default": "local"},
        }
    )
    assert config["read_config_from_api"] is True
    assert config["manager-api"] == {"url": "http://m", "secret": "s"}
    assert config["server"]["auth"] == {"enabled": True}
    assert config["server"]["websocket"] == "ws://local"
    assert config["server"]["firmware_download_scheme"] == "http"
    assert config["server"]["factory_test_claimed_devices"] == ["aa:bb"]
    assert config["server"]["claim_reset_devices"] == ["14:c1:9f:d1:ac:20"]
    assert config["server"]["claim_reset_nonce"] == "repair-2026-06-22"
    assert config["prompt_template"] == {"default": "local"}


@pytest.mark.asyncio
async def test_manager_config_inherits_base_google_live_policy(monkeypatch):
    monkeypatch.setattr(config_loader, "init_service", lambda config: None)

    async def server_config():
        return {
            "server": {"auth": {"enabled": True}},
            "selected_module": {},
            "voice_mode": {"type": "classic_pipeline"},
            "google_live": {"model": "manager-live-model"},
        }

    monkeypatch.setattr(config_loader, "get_server_config", server_config)

    config = await config_loader.get_config_from_api_async(
        {
            "manager-api": {"url": "http://m", "secret": "s"},
            "server": {"auth_key": "auth"},
            "voice_mode": {"type": "google_live", "fallback_to_classic_on_error": False},
            "google_live": {"model": "base-live-model", "voice_name": "Kore"},
        }
    )

    assert config["voice_mode"]["type"] == "google_live"
    assert config["voice_mode"]["fallback_to_classic_on_error"] is False
    assert config["google_live"]["model"] == "manager-live-model"
    assert config["google_live"]["voice_name"] == "Kore"

@pytest.mark.asyncio
async def test_get_private_config_from_api_exceptions_and_fallbacks(monkeypatch):
    async def missing_device(*_args, **_kwargs):
        raise AssertionError("replaced below")

    async def words(_device_id):
        return {"hello": "xin chao"}

    monkeypatch.setattr(config_loader, "get_correct_words", words)

    async def agent_not_found(*_args):
        return config_loader.DeviceNotFoundException("missing")

    monkeypatch.setattr(config_loader, "get_agent_models", agent_not_found)
    with pytest.raises(config_loader.DeviceNotFoundException):
        await config_loader.get_private_config_from_api({"selected_module": {}}, "dev", "client")

    async def agent_bind(*_args):
        return config_loader.DeviceBindException("123")

    monkeypatch.setattr(config_loader, "get_agent_models", agent_bind)
    with pytest.raises(config_loader.DeviceBindException):
        await config_loader.get_private_config_from_api({"selected_module": {}}, "dev", "client")

    async def agent_error(*_args):
        return RuntimeError("backend")

    monkeypatch.setattr(config_loader, "get_agent_models", agent_error)
    result = await config_loader.get_private_config_from_api({"selected_module": {}}, "dev", "client")
    assert result["correct_words"] == {"hello": "xin chao"}
    assert result["voice_mode"]["type"] == "classic_pipeline"

    async def words_error(_device_id):
        raise RuntimeError("words failed")

    async def agent_ok(*_args):
        return {
            "voice_mode": {"type": "google_live"},
            "google_live": {"barge_in": True, "drop_input_while_speaking": True},
        }

    monkeypatch.setattr(config_loader, "get_correct_words", words_error)
    monkeypatch.setattr(config_loader, "get_agent_models", agent_ok)
    result = await config_loader.get_private_config_from_api({"selected_module": {"LLM": "x"}}, "dev", "client")
    assert "correct_words" not in result
    assert result["google_live"]["barge_in"] is False
    assert result["google_live"]["drop_input_while_speaking"] is False

    assert missing_device is not None


def test_ensure_directories_and_merge_edges(tmp_path, monkeypatch, capsys):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(config_loader, "get_project_dir", lambda: str(project) + "/")
    made = []

    def fake_makedirs(path, exist_ok=False):
        made.append((path, exist_ok))
        if path.endswith("forbidden"):
            raise PermissionError("denied")

    monkeypatch.setattr(config_loader.os, "makedirs", fake_makedirs)
    config_loader.ensure_directories(
        {
            "log": {"log_dir": "logs"},
            "ASR": {"local": {"output_dir": str(tmp_path / "asr-output")}},
            "TTS": {"voice": {"output_dir": str(tmp_path / "forbidden")}},
            "selected_module": {"ASR": "local", "LLM": "llm", "TTS": "voice"},
            "LLM": {"llm": {"output_dir": "models/llm"}},
        }
    )

    paths = {path for path, _exist_ok in made}
    assert str(project / "logs") in paths
    assert str(tmp_path / "asr-output") in paths
    assert str(project / "models/llm") in paths
    assert "Warning: cannot create directory" in capsys.readouterr().out

    assert config_loader.merge_configs({"a": 1}, None) is None
    assert config_loader.merge_configs({"a": {"b": 1}}, {"a": {"c": 2}}) == {"a": {"b": 1, "c": 2}}


def test_ensure_directories_skips_missing_and_bad_provider_shapes(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(config_loader, "get_project_dir", lambda: str(project) + "/")
    made = []
    monkeypatch.setattr(config_loader.os, "makedirs", lambda path, exist_ok=False: made.append(path))

    config_loader.ensure_directories(
        {
            "log": {"log_dir": "logs"},
            "selected_module": {"ASR": "missing", "TTS": "bad"},
            "TTS": {"bad": "not-a-provider-config"},
        }
    )

    assert made == [str(project / "logs")]


def test_read_config_and_project_dir(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("server:\n  port: 9000\n", encoding="utf-8")

    assert config_loader.read_config(config_file) == {"server": {"port": 9000}}
    assert config_loader.get_project_dir().endswith("/")
