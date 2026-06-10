import os
import asyncio
import yaml
from collections.abc import Mapping
from config.manage_api_client import (
    init_service,
    get_server_config,
    get_agent_models,
    get_correct_words,
    DeviceNotFoundException,
    DeviceBindException,
)

GOOGLE_LIVE_DEFAULTS = {
    "enable_audio_input": True,
    "enable_audio_output": True,
    "native_voice": True,
    "language_code": "vi-VN",
    "input_audio_format": "pcm16",
    "input_sample_rate": 16000,
    "output_audio_format": "pcm16",
    "output_sample_rate": 24000,
    "input_flush_delay_sec": 0.8,
    "interrupt_forced_flush_delay_sec": 0.8,
    "interrupt_min_capture_ms": 360,
    "interrupt_speech_tail_ms": 240,
    "interrupt_max_capture_ms": 1200,
    "disable_server_side_interruptions": True,
    "turn_coverage": "TURN_INCLUDES_ALL_INPUT",
    "log_audio_diagnostics": True,
    "interrupt_on_input_while_speaking": False,
    "interrupt_rms_threshold": 5000,
    "interrupt_min_input_duration_sec": 0.42,
    "interrupt_min_output_age_sec": 0.25,
    "interrupt_suppress_audio_sec": 0.25,
    "suppress_robot_output_echo": True,
    "wake_audio_allow_window_sec": 5.0,
    "robot_output_echo_bypass_rms_threshold": 650,
    "robot_output_echo_bypass_min_duration_sec": 0.06,
    "hard_reconnect_on_interrupt": False,
    "drop_input_while_speaking": False,
    "barge_in": False,
    # PR4 P4.5: sync with config.yaml so manager-api configs do not drift
    # back to the old 5000 / 0.42 values (PR2 drift lesson).
    "barge_in_rms_threshold": 4500,
    "barge_in_min_input_duration_sec": 0.30,
    "barge_in_min_output_age_sec": 0.25,
    # When False, RMS-based loud-input bypass of the echo gate cannot fire a
    # mid-sentence interrupt — for hardware where speaker echo crosses the
    # bypass threshold and would otherwise cut the model off.
    "echo_bypass_interrupt_enabled": True,
    "server_side_vad_enabled": False,
    "send_transcript_events": True,
    "send_llm_state_events": False,
    "reconnect": {
        "enabled": True,
        "max_retries": 6,
        "backoff_ms": 250,
        "backoff_multiplier": 2,
    },
}


def get_project_dir():
    """Get project root directory"""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "/"


def read_config(config_path):
    with open(config_path, "r", encoding="utf-8") as file:
        config = yaml.safe_load(file)
    return config


# Env var -> nested config path. Each entry overlays an os.environ value onto
# the merged config so deployment-specific endpoints/secrets can change without
# editing config.yaml or data/.config.yaml. Additive + null-safe: nested dicts
# are created if absent, and an override only applies when the env var is set to
# a non-empty value.
_ENV_CONFIG_OVERRIDES = (
    ("MANAGER_API_URL", ("manager-api", "url")),
    ("MANAGER_API_SECRET", ("manager-api", "secret")),
    # Read at core/lesson/runtime.py:639 as server_cfg.get("api_url").
    ("COURSE_BACKEND_URL", ("server", "api_url")),
    ("PUBLIC_WS_URL", ("server", "websocket")),
    ("PUBLIC_OTA_URL", ("server", "ota")),
)


def apply_env_overrides(config):
    """Overlay os.environ values onto the merged config dict.

    Mutates and returns ``config``. Only non-empty env vars override existing
    values; nested dicts are created on demand. Safe to call when ``config`` is
    not a Mapping (returns it unchanged).
    """
    if not isinstance(config, Mapping):
        return config

    for env_name, path in _ENV_CONFIG_OVERRIDES:
        value = os.environ.get(env_name)
        if value is None or value == "":
            continue
        target = config
        for key in path[:-1]:
            child = target.get(key)
            if not isinstance(child, Mapping):
                child = {}
                target[key] = child
            target = child
        target[path[-1]] = value

    return config


def normalize_voice_config(config):
    """Ensure voice provider config defaults exist."""
    if not isinstance(config, Mapping):
        config = {}

    voice_mode = config.get("voice_mode")
    if not isinstance(voice_mode, Mapping):
        voice_mode = {}
        config["voice_mode"] = voice_mode
    voice_mode.setdefault("type", "classic_pipeline")
    voice_mode.setdefault("fallback_to_classic_on_error", True)

    google_live = config.get("google_live")
    if voice_mode.get("type") == "google_live":
        if not isinstance(google_live, Mapping):
            google_live = {}
        config["google_live"] = merge_configs(GOOGLE_LIVE_DEFAULTS, google_live)
    elif not isinstance(google_live, Mapping):
        config["google_live"] = {}
    return config


def load_config():
    """Load config file"""
    from core.utils.cache.manager import cache_manager, CacheType

    # Check Cache
    cached_config = cache_manager.get(CacheType.CONFIG, "main_config")
    if cached_config is not None:
        return cached_config

    default_config_path = get_project_dir() + "config.yaml"
    custom_config_path = get_project_dir() + "data/.config.yaml"

    # Load default config
    default_config = read_config(default_config_path)
    custom_config = read_config(custom_config_path)
    if not isinstance(default_config, Mapping):
        default_config = {}
    if not isinstance(custom_config, Mapping):
        custom_config = {}

    if custom_config.get("manager-api", {}).get("url"):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            config = asyncio.run(get_config_from_api_async(custom_config))
        else:
            raise RuntimeError(
                "load_config() cannot fetch manager-api config from a running event loop; "
                "use load_config_async() instead"
            )
    else:
        # Merge Config
        config = merge_configs(default_config, custom_config)
    config = normalize_voice_config(config)
    # Overlay env vars onto the merged config before any consumer reads it.
    config = apply_env_overrides(config)
    # Initialize directory
    ensure_directories(config)

    # Cache Config
    cache_manager.set(CacheType.CONFIG, "main_config", config)
    return config


async def load_config_async():
    """Load config file from async context without blocking the running loop."""
    from core.utils.cache.manager import cache_manager, CacheType

    cached_config = cache_manager.get(CacheType.CONFIG, "main_config")
    if cached_config is not None:
        return cached_config

    default_config_path = get_project_dir() + "config.yaml"
    custom_config_path = get_project_dir() + "data/.config.yaml"

    default_config = read_config(default_config_path)
    custom_config = read_config(custom_config_path)
    if not isinstance(default_config, Mapping):
        default_config = {}
    if not isinstance(custom_config, Mapping):
        custom_config = {}

    if custom_config.get("manager-api", {}).get("url"):
        config = await get_config_from_api_async(custom_config)
    else:
        config = merge_configs(default_config, custom_config)
    config = normalize_voice_config(config)
    # Overlay env vars onto the merged config before any consumer reads it.
    config = apply_env_overrides(config)
    ensure_directories(config)
    cache_manager.set(CacheType.CONFIG, "main_config", config)
    return config


async def get_config_from_api_async(config):
    """Get config from Java API (async version)"""
    # InitializeAPIClient
    init_service(config)

    # Get server config
    config_data = await get_server_config()
    if config_data is None:
        raise Exception("Failed to fetch server config from API")

    config_data["read_config_from_api"] = True
    config_data["manager-api"] = {
        "url": config["manager-api"].get("url", ""),
        "secret": config["manager-api"].get("secret", ""),
    }
    auth_enabled = config_data.get("server", {}).get("auth", {}).get("enabled", False)
    # serverconfig uses local as source
    if config.get("server"):
        config_data["server"] = {
            "ip": config["server"].get("ip", ""),
            "port": config["server"].get("port", ""),
            "http_port": config["server"].get("http_port", ""),
            "websocket": config["server"].get("websocket", ""),
            "vision_explain": config["server"].get("vision_explain", ""),
            "auth_key": config["server"].get("auth_key", ""),
        }
    config_data["server"]["auth"] = {"enabled": auth_enabled}
    # If server has noprompt_templateThen read from local config
    if not config_data.get("prompt_template"):
        config_data["prompt_template"] = config.get("prompt_template")
    return normalize_voice_config(config_data)


async def get_private_config_from_api(config, device_id, client_id):
    """Get private config from Java API"""
    results = await asyncio.gather(
        get_agent_models(device_id, client_id, config["selected_module"]),
        get_correct_words(device_id),
        return_exceptions=True,
    )
    agent_result = results[0]
    correct_words = results[1] if not isinstance(results[1], Exception) else None

    # Throw BusinessException
    if isinstance(agent_result, DeviceNotFoundException):
        raise agent_result
    if isinstance(agent_result, DeviceBindException):
        raise agent_result

    private_config = agent_result if not isinstance(agent_result, Exception) else {}
    if correct_words:
        private_config["correct_words"] = correct_words
    return normalize_voice_config(private_config)


def ensure_directories(config):
    """Ensure all config paths exist"""
    dirs_to_create = set()
    project_dir = get_project_dir()  # Get project root directory
    # Log file directory
    log_dir = config.get("log", {}).get("log_dir", "tmp")
    dirs_to_create.add(os.path.join(project_dir, log_dir))

    # ASR/TTS moduleOutput Directory
    for module in ["ASR", "TTS"]:
        if config.get(module) is None:
            continue
        for provider in config.get(module, {}).values():
            output_dir = provider.get("output_dir", "")
            if output_dir:
                dirs_to_create.add(output_dir)

    # Based onselected_moduleCreate model directory
    selected_modules = config.get("selected_module", {})
    for module_type in ["ASR", "LLM", "TTS"]:
        selected_provider = selected_modules.get(module_type)
        if not selected_provider:
            continue
        if config.get(module) is None:
            continue
        if config.get(selected_provider) is None:
            continue
        provider_config = config.get(module_type, {}).get(selected_provider, {})
        output_dir = provider_config.get("output_dir")
        if output_dir:
            full_model_dir = os.path.join(project_dir, output_dir)
            dirs_to_create.add(full_model_dir)

    # Uniformly create directory (keep originaldatadirectory create)
    for dir_path in dirs_to_create:
        try:
            os.makedirs(dir_path, exist_ok=True)
        except PermissionError:
            print(f"Warning: cannot create directory {dir_path}, check write permission")


def merge_configs(default_config, custom_config):
    """
    Recursively merge config, custom_config has higher priority

    Args:
        default_config: default config
        custom_config: user custom config

    Returns:
        merged config
    """
    if not isinstance(default_config, Mapping) or not isinstance(
        custom_config, Mapping
    ):
        return custom_config

    merged = dict(default_config)

    for key, value in custom_config.items():
        if (
            key in merged
            and isinstance(merged[key], Mapping)
            and isinstance(value, Mapping)
        ):
            merged[key] = merge_configs(merged[key], value)
        else:
            merged[key] = value

    return merged
