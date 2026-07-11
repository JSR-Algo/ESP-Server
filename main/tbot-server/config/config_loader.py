import asyncio
import os
from collections.abc import Mapping

import yaml

from config.manage_api_client import (
    DeviceBindException,
    DeviceNotFoundException,
    get_agent_models,
    get_correct_words,
    get_server_config,
    init_service,
)

DEFAULT_GOOGLE_LIVE_VOICE_NAME = "Kore"

GOOGLE_LIVE_DEFAULTS = {
    "model": "gemini-3.1-flash-live-preview",
    "enable_audio_input": True,
    "enable_audio_output": True,
    "native_voice": True,
    "voice_name": DEFAULT_GOOGLE_LIVE_VOICE_NAME,
    "language_code": "vi-VN",
    "input_audio_format": "pcm16",
    "input_sample_rate": 16000,
    "output_audio_format": "pcm16",
    "output_sample_rate": 24000,
    "input_live_chunk_ms": 20,
    "interrupt_policy": "wake_or_transcript",
    "raw_audio_barge_in_enabled": False,
    "input_flush_delay_sec": 0.75,
    # Conversation: slightly patient end so STT gets full phrases (less mishear).
    "conversation_input_flush_delay_sec": 0.36,
    "input_speech_tail_ms": 650,
    "conversation_input_speech_tail_ms": 360,
    "input_min_capture_ms": 280,
    "input_max_capture_ms": 8000,
    "conversation_input_max_capture_ms": 5000,
    # Lesson say-it turns are short (one word); finalize faster than full phrases.
    "lesson_child_input_speech_tail_ms": 280,
    "lesson_child_input_flush_delay_sec": 0.32,
    "lesson_child_input_max_capture_ms": 4000,
    "lesson_child_response_window_sec": 22.0,
    "lesson_child_response_open_delay_sec": 0.1,
    "lesson_child_response_fast_reopen_sec": 0.8,
    "lesson_prompt_playback_tail_sec": 0.5,
    # Soft speech still needs to pass start-noise gate; 900 was dropping quiet asks.
    "input_speech_rms_threshold": 650,
    "lesson_child_input_speech_rms_threshold": 2000,
    "input_gain": 3.5,
    # Live/TTS PCM is often under-normalized; boost before Opus→robot.
    # 2.0 clipped/distorted on ES8311+PA; 1.35 is a safer loudness tradeoff.
    "output_gain": 1.35,
    # Floor enforced in provider; agent private 3.0 was dropping tool replies.
    "waiting_model_timeout_sec": 5.0,
    "waiting_model_retry_prompt_after_sec": 12.0,
    "live_open_timeout_sec": 12.0,
    # Open Live soon after robot websocket connect / on Hi ESP so the first
    # spoken turn does not pay cold Google connect latency (~0.6–1.2s).
    "prewarm_live_on_connect": True,
    "prewarm_live_on_connect_delay_sec": 0.0,
    "prewarm_live_on_wake": True,
    "wake_greeting_enabled": True,
    "wake_greeting_text": "Dạ, mình nghe đây ạ.",
    "wake_greeting_protect_sec": 1.1,
    "post_reply_hold_sec": 0.55,
    # Keep Live + conversation open through long multi-turn chats (pauses OK).
    "idle_timeout_sec": 900,
    # After Hi ESP / listen:start, allow continuous talk without re-wake for 15 min.
    "wake_audio_allow_window_sec": 900,
    "conversation_audio_allow_window_sec": 900,
    "wake_transcript_tail_suppress_sec": 0.15,
    # Passive steps use adaptive caps (~spoken length); 30s hung after Live interrupts.
    "lesson_prompt_output_guard_timeout_sec": 10.0,
    "lesson_prompt_playback_guard_timeout_sec": 6.0,
    # After model audio, advance passive steps once speech has settled.
    "lesson_prompt_inferred_idle_sec": 1.6,
    "interrupt_forced_flush_delay_sec": 0.8,
    "interrupt_min_capture_ms": 360,
    "interrupt_speech_tail_ms": 240,
    "interrupt_max_capture_ms": 1200,
    "interrupt_replay_buffer_ms": 900,
    "reconnect_buffer_ms": 2000,
    # Post-tts residual (device still drains playback after server tts:stop).
    # Too short reopens monologue loops; too long feels deaf after robot speaks.
    "echo_tail_suppression_ms": 500,
    "echo_tail_extend_rms_threshold": 700,
    "echo_tail_extend_ms": 300,
    "echo_tail_max_total_ms": 1200,
    # Latch audible briefly so residual frames stay under the echo gate.
    "echo_tail_audible_ms": 350,
    "music_auto_pause_on_user_speech": True,
    "disable_server_side_interruptions": False,
    "activity_handling": "START_OF_ACTIVITY_INTERRUPTS",
    "turn_coverage": "TURN_INCLUDES_ALL_INPUT",
    "log_audio_diagnostics": True,
    "interrupt_on_input_while_speaking": False,
    "interrupt_rms_threshold": 5000,
    "interrupt_min_input_duration_sec": 0.42,
    "interrupt_min_output_age_sec": 0.25,
    # Ignore Live false barge-in for the first ~0.7s of robot speech.
    "interruption_min_output_age_sec": 0.7,
    "interrupt_suppress_audio_sec": 0.28,
    # First model-audio frames often leak residual energy before AEC converges.
    "mute_input_after_audio_start_sec": 0.4,
    "suppress_robot_output_echo": True,
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
    "barge_in_transcript_min_output_age_sec": 0.6,
    # When False, RMS-based loud-input bypass of the echo gate cannot fire a
    # mid-sentence interrupt — for hardware where speaker echo crosses the
    # bypass threshold and would otherwise cut the model off.
    "echo_bypass_interrupt_enabled": False,
    "server_side_vad_enabled": True,
    "send_transcript_events": True,
    "send_llm_state_events": False,
    "session_resumption_enabled": True,
    "session_resumption_transparent": True,
    "context_window_compression_enabled": True,
    "context_window_trigger_tokens": 24000,
    "context_window_target_tokens": 12000,
    "aec_enabled": True,
    "aec_filter_length_ms": 200,
    "aec_frame_ms": 10,
    "tool_timeout_sec": 10.0,
    "dangerous_tool_names": [],
    "reconnect": {
        "enabled": True,
        "max_retries": 6,
        "backoff_ms": 250,
        "backoff_multiplier": 2,
    },
}
DEFAULT_EDGE_TTS_VOICE = "vi-VN-HoaiMyNeural"


def get_project_dir():
    """Get project root directory"""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "/"


def read_config(config_path):
    with open(config_path, encoding="utf-8") as file:
        config = yaml.safe_load(file)
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
    if voice_mode.get("type") == "google_live":
        voice_mode["fallback_to_classic_on_error"] = False

    google_live = config.get("google_live")
    if voice_mode.get("type") == "google_live":
        if not isinstance(google_live, Mapping):
            google_live = {}
        config["google_live"] = merge_configs(GOOGLE_LIVE_DEFAULTS, google_live)
        _apply_google_live_runtime_safety_policy(config["google_live"])
    elif not isinstance(google_live, Mapping):
        config["google_live"] = {}
    _apply_tts_runtime_overrides(config)
    # Single source of truth: Agent Role tab → google_live.api_key (then env fallback).
    # Push that one key onto every Gemini ASR/LLM/TTS/VLLM module so providers do not
    # diverge (ASR had its own AIza key while Live used agent AQ./AIza key).
    _apply_single_agent_gemini_api_key(config)
    return config

def _apply_base_google_live_policy(config, base_config):
    if not isinstance(config, Mapping):
        config = {}
    if not isinstance(base_config, Mapping):
        return config

    base_voice_mode = base_config.get("voice_mode")
    if not isinstance(base_voice_mode, Mapping) or base_voice_mode.get("type") != "google_live":
        return config

    current_voice_mode = config.get("voice_mode")
    current_voice_mode = current_voice_mode if isinstance(current_voice_mode, Mapping) else {}
    merged_voice_mode = merge_configs(base_voice_mode, current_voice_mode)
    merged_voice_mode["type"] = "google_live"
    config["voice_mode"] = merged_voice_mode

    base_google_live = base_config.get("google_live")
    base_google_live = base_google_live if isinstance(base_google_live, Mapping) else {}
    current_google_live = config.get("google_live")
    current_google_live = current_google_live if isinstance(current_google_live, Mapping) else {}
    config["google_live"] = merge_configs(base_google_live, current_google_live)
    return config

def _apply_tts_runtime_overrides(config):
    provider = (_clean_env("TBOT_TTS_PROVIDER") or "").casefold()
    force_edge = _parse_bool_env("TBOT_FORCE_EDGE_TTS") is True or provider == "edge"
    # Google/Gemini TTS uses the REST generateContent API, which needs a REAL Gemini
    # API key (AIza...) — NOT the Live *ephemeral* token in GOOGLE_API_KEY (that only
    # authenticates the Live WebSocket). Read a dedicated key so the Live token is never
    # mis-inherited for TTS, and map TBOT_TTS_PROVIDER=google|gemini -> GeminiTTS.
    prefer_gemini = provider in ("google", "gemini", "geministts")
    gemini_tts_key = _clean_env("GEMINI_API_KEY") or _clean_env("TBOT_GEMINI_TTS_API_KEY")
    selected_module = config.get("selected_module")
    if not isinstance(selected_module, Mapping):
        selected_module = {}
        config["selected_module"] = selected_module
    tts_configs = config.get("TTS")
    if not isinstance(tts_configs, Mapping):
        tts_configs = {}
        config["TTS"] = tts_configs

    edge_config = tts_configs.get("EdgeTTS")
    if not isinstance(edge_config, Mapping):
        edge_config = {}
    edge_config = dict(edge_config)
    edge_config.setdefault("type", "edge")
    edge_config["voice"] = _clean_env("TBOT_EDGE_TTS_VOICE") or edge_config.get("voice") or DEFAULT_EDGE_TTS_VOICE
    edge_config.setdefault("output_dir", "tmp/")
    tts_configs["EdgeTTS"] = edge_config

    # Dedicated TTS env keys are folded into google_live.api_key later by
    # _apply_single_agent_gemini_api_key — do not keep a second TTS-only secret.
    if gemini_tts_key:
        google_live = config.get("google_live")
        if not isinstance(google_live, Mapping):
            google_live = {}
            config["google_live"] = google_live
        if _looks_like_placeholder_api_key(google_live.get("api_key")):
            google_live["api_key"] = gemini_tts_key

    if force_edge:
        selected_module["TTS"] = "EdgeTTS"
        return

    # provider=google|gemini -> select GeminiTTS as the primary TTS (EdgeTTS stays only
    # as the fallback_tts safety net below).
    if prefer_gemini and isinstance(tts_configs.get("GeminiTTS"), Mapping):
        selected_module["TTS"] = "GeminiTTS"

    selected_tts = selected_module.get("TTS")
    selected_config = tts_configs.get(selected_tts)
    if not isinstance(selected_config, Mapping):
        return
    selected_type = str(selected_config.get("type") or selected_tts or "").casefold()
    if "gemini" not in selected_type:
        return
    selected_config = dict(selected_config)
    selected_config.setdefault("fallback_tts", dict(edge_config))
    tts_configs[selected_tts] = selected_config

def _looks_like_placeholder_api_key(value):
    key = str(value or "").strip()
    if not key:
        return True
    # Unresolved env template or manager console placeholders.
    if key.startswith("${") and key.endswith("}"):
        return True
    if key.startswith("??"):
        return True
    if len(key) < 20:
        return True
    lowered = key.casefold()
    return any(
        marker in lowered
        for marker in ("placeholder", "your_key", "your-key", "test-key", "dummy")
    )

def _first_usable_gemini_module_api_key(config):
    """Migration fallback only: scan agent ASR/LLM/TTS Gemini modules for a real key."""
    if not isinstance(config, Mapping):
        return None
    for section in ("ASR", "LLM", "TTS", "VLLM"):
        modules = config.get(section)
        if not isinstance(modules, Mapping):
            continue
        for name, provider_config in modules.items():
            if not isinstance(provider_config, Mapping):
                continue
            provider_type = str(provider_config.get("type") or name).casefold()
            if "gemini" not in provider_type and "gemini" not in str(name).casefold():
                continue
            key = provider_config.get("api_key")
            if not _looks_like_placeholder_api_key(key):
                return str(key).strip()
    return None


def _env_gemini_api_key():
    return (
        _clean_env("GOOGLE_API_KEY")
        or _clean_env("TBOT_GOOGLE_LIVE_API_KEY")
        or _clean_env("GEMINI_API_KEY")
        or _clean_env("GOOGLE_GEMINI_API_KEY")
        or _clean_env("TBOT_GEMINI_TTS_API_KEY")
    )


def _is_gemini_provider(name, provider_config):
    provider_type = str(provider_config.get("type") or name or "").casefold()
    name_l = str(name or "").casefold()
    return "gemini" in provider_type or "gemini" in name_l


def _resolve_canonical_gemini_api_key(config):
    """One key only: Agent Role tab google_live.api_key, then env, then module migration."""
    if not isinstance(config, Mapping):
        return None
    google_live = config.get("google_live")
    if isinstance(google_live, Mapping):
        live_key = google_live.get("api_key")
        if not _looks_like_placeholder_api_key(live_key):
            return str(live_key).strip()
    env_key = _env_gemini_api_key()
    if env_key and not _looks_like_placeholder_api_key(env_key):
        return env_key
    return _first_usable_gemini_module_api_key(config)


def _apply_single_agent_gemini_api_key(config):
    """Force every Gemini module to use the same key as Agent Role → Google Live API.

    Source of truth (in order):
      1. google_live.api_key from agent private config (manager Role Config tab)
      2. env GOOGLE_API_KEY / TBOT_GOOGLE_LIVE_API_KEY / GEMINI_API_KEY (deploy override)
      3. first non-placeholder key found on ASR/LLM/TTS modules (legacy migration only)

    Then overwrite api_key on all Gemini ASR/LLM/TTS/VLLM providers so ASR can no
    longer run a different AIza key than Live/LLM/TTS.
    """
    if not isinstance(config, Mapping):
        return
    canonical = _resolve_canonical_gemini_api_key(config)
    if not canonical:
        return

    google_live = config.get("google_live")
    if not isinstance(google_live, Mapping):
        google_live = {}
        config["google_live"] = google_live
    google_live["api_key"] = canonical

    for section in ("ASR", "LLM", "TTS", "VLLM"):
        modules = config.get(section)
        if not isinstance(modules, Mapping):
            continue
        for name, provider_config in list(modules.items()):
            if not isinstance(provider_config, Mapping):
                continue
            if not _is_gemini_provider(name, provider_config):
                continue
            # Mutate in place (provider configs are expected to be dicts after merge).
            provider_config["api_key"] = canonical

def _apply_google_live_runtime_safety_policy(google_live):
    """Keep manager/private configs from re-enabling unsafe Live interrupts."""
    google_live["voice_name"] = DEFAULT_GOOGLE_LIVE_VOICE_NAME
    google_live["interrupt_policy"] = "wake_or_transcript"
    google_live["raw_audio_barge_in_enabled"] = False
    google_live["disable_server_side_interruptions"] = False
    google_live["activity_handling"] = "START_OF_ACTIVITY_INTERRUPTS"
    google_live["barge_in"] = False
    google_live["interrupt_on_input_while_speaking"] = False
    google_live["drop_input_while_speaking"] = False
    try:
        min_output_age = float(google_live.get("interruption_min_output_age_sec", 0.7))
    except (TypeError, ValueError):
        min_output_age = 0.7
    # Floor 0.7s: false barge-in cuts clustered under ~0.5s; 1.0s felt laggy.
    google_live["interruption_min_output_age_sec"] = max(0.7, min(min_output_age, 2.0))
    try:
        transcript_min_age = float(
            google_live.get("barge_in_transcript_min_output_age_sec", 0.6)
        )
    except (TypeError, ValueError):
        transcript_min_age = 0.6
    google_live["barge_in_transcript_min_output_age_sec"] = max(
        0.4, min(transcript_min_age, 2.0)
    )
    try:
        mute_after = float(google_live.get("mute_input_after_audio_start_sec", 0.4))
    except (TypeError, ValueError):
        mute_after = 0.4
    google_live["mute_input_after_audio_start_sec"] = max(0.28, min(mute_after, 0.6))
    try:
        silence_ms = float(google_live.get("silence_duration_ms", 600))
    except (TypeError, ValueError):
        silence_ms = 600.0
    google_live["silence_duration_ms"] = max(600.0, min(silence_ms, 800.0))
    try:
        waiting_timeout = float(google_live.get("waiting_model_timeout_sec", 5.0))
    except (TypeError, ValueError):
        waiting_timeout = 5.0
    # Cap at 6.0 so private configs cannot stall forever; prefer ~5s for first audio.
    google_live["waiting_model_timeout_sec"] = min(max(0.0, waiting_timeout), 6.0)
    google_live["echo_bypass_interrupt_enabled"] = False
    google_live["server_side_vad_enabled"] = True
    google_live["aec_enabled"] = True
    google_live.setdefault("session_resumption_enabled", True)
    google_live["context_window_compression_enabled"] = True

def _clean_env(name):
    value = os.environ.get(name, "").strip()
    return value or None

def _parse_bool_env(name):
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return None
    return raw.strip().lower() in ("1", "true", "yes", "on")

def _parse_positive_int_env(name):
    raw = _clean_env(name)
    if raw is None:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None

def _parse_percent_env(name):
    raw = _clean_env(name)
    if raw is None:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if 0 <= value <= 100 else None

def _apply_lesson_env_overrides(config):
    """LESSON_RUNTIME_ENABLED dark-rollout flag + lesson endpoints, so an operator
    can enable/point the lesson runtime via env without editing config volumes.
    RESTORED 2026-06-12 (deep-audit): the unify checkout-subtree-replace dropped
    main's apply_env_overrides, silently disabling these documented knobs.
    Additive + null-safe; never overwrites an author-provided lesson.api_base.
    LESSON_RUNTIME_ENABLED -> lesson.runtime_enabled (bool parsed). If the explicit
    flag is absent, a production-ready lesson env (course backend URL + mint secret +
    asset origin) auto-enables the runtime so a deployed server does not hide the
    start_lesson tool because one boolean was omitted. COURSE_BACKEND_URL (or
    TBOT_BACKEND_API_URL) -> server.api_url AND, as a fallback, lesson.api_base
    (runtime reads lesson.api_base first, else server.api_url). The shipped
    lesson.api_base default is never enough to auto-enable production lessons.
    LESSON_ASSET_ORIGIN_BASE -> lesson.asset_origin_base.
    LESSON_ASSET_PUBLIC_BASE_URL -> lesson.asset_public_base_url.
    LESSON_ASSET_DELIVERY_MODE -> lesson.asset_delivery_mode.
    LESSON_ASSET_PACK_LOCAL_ROOT -> lesson.asset_pack_local_root.
    LESSON_ASSET_PACK_MOUNT_ROOT -> lesson.asset_pack_mount_root.
    LESSON_STEP_TIMEOUT_FLOOR_SEC -> lesson.step_timeout_floor_sec.
    LESSON_VOICE_RT_P95_DISABLE_MS -> lesson.voice_rt_p95_disable_ms.
    LESSON_MAX_ASSET_BYTES -> lesson.max_asset_bytes.
    LESSON_MAX_TOTAL_ASSET_BYTES -> lesson.max_total_asset_bytes.
    LESSON_SD_CACHE_QUOTA_BYTES -> lesson.sd_cache_quota_bytes.
    LESSON_SD_GC_FREE_PERCENT -> lesson.sd_gc_free_percent.
    LESSON_SD_PRELOAD_MIN_FREE_PERCENT -> lesson.sd_preload_min_free_percent."""
    if not isinstance(config, Mapping):
        return config

    flag = _parse_bool_env("LESSON_RUNTIME_ENABLED")
    course_url = _clean_env("COURSE_BACKEND_URL") or _clean_env("TBOT_BACKEND_API_URL")
    asset_origin = _clean_env("LESSON_ASSET_ORIGIN_BASE")
    asset_public_base = _clean_env("LESSON_ASSET_PUBLIC_BASE_URL")
    asset_delivery_mode = _clean_env("LESSON_ASSET_DELIVERY_MODE")
    asset_pack_local_root = _clean_env("LESSON_ASSET_PACK_LOCAL_ROOT")
    asset_pack_mount_root = _clean_env("LESSON_ASSET_PACK_MOUNT_ROOT")
    step_timeout_floor = _clean_env("LESSON_STEP_TIMEOUT_FLOOR_SEC")
    voice_rt_p95_disable_ms = _clean_env("LESSON_VOICE_RT_P95_DISABLE_MS")
    max_asset_bytes = _parse_positive_int_env("LESSON_MAX_ASSET_BYTES")
    max_total_asset_bytes = _parse_positive_int_env("LESSON_MAX_TOTAL_ASSET_BYTES")
    sd_cache_quota_bytes = _parse_positive_int_env("LESSON_SD_CACHE_QUOTA_BYTES")
    sd_gc_free_percent = _parse_percent_env("LESSON_SD_GC_FREE_PERCENT")
    sd_preload_min_free_percent = _parse_percent_env("LESSON_SD_PRELOAD_MIN_FREE_PERCENT")
    existing_lesson = config.get("lesson")
    existing_lesson = existing_lesson if isinstance(existing_lesson, Mapping) else {}
    effective_gc = sd_gc_free_percent if sd_gc_free_percent is not None else existing_lesson.get("sd_gc_free_percent", 20)
    effective_preload = (
        sd_preload_min_free_percent
        if sd_preload_min_free_percent is not None
        else existing_lesson.get("sd_preload_min_free_percent", 5)
    )
    try:
        effective_gc = float(effective_gc)
        effective_preload = float(effective_preload)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("lesson SD free-space percentages must be numeric")
    if not 0 < effective_preload <= effective_gc <= 100:
        raise ValueError("lesson SD percentages require 0 < preload_min <= gc_trigger <= 100")
    # Built-in sample-lesson DEMO flag (independent of runtime_enabled; NEVER coupled to
    # the production auto-enable below). LESSON_SAMPLE_ENABLED -> lesson.sample_lesson;
    # LESSON_SAMPLE_ASSET_BASE -> lesson.sample_asset_base_url (optional image host).
    sample_flag = _parse_bool_env("LESSON_SAMPLE_ENABLED")
    sample_asset_base = _clean_env("LESSON_SAMPLE_ASSET_BASE")
    sample_step_dwell = _clean_env("LESSON_SAMPLE_STEP_DWELL_SEC")
    # LESSON_SAMPLE_MODE -> lesson.sample_mode ('interactive' default | 'passive' fallback).
    sample_mode = _clean_env("LESSON_SAMPLE_MODE")
    if (
        flag is None
        and not course_url
        and not asset_origin
        and not asset_public_base
        and not asset_delivery_mode
        and not asset_pack_local_root
        and not asset_pack_mount_root
        and not step_timeout_floor
        and not voice_rt_p95_disable_ms
        and max_asset_bytes is None
        and max_total_asset_bytes is None
        and sd_cache_quota_bytes is None
        and sd_gc_free_percent is None
        and sd_preload_min_free_percent is None
        and sample_flag is None
        and not sample_asset_base
        and not sample_step_dwell
        and not sample_mode
    ):
        return config

    lesson_cfg = config.get("lesson")
    if not isinstance(lesson_cfg, Mapping):
        lesson_cfg = {}
        config["lesson"] = lesson_cfg
    if flag is not None:
        lesson_cfg["runtime_enabled"] = flag
    if asset_origin:
        lesson_cfg["asset_origin_base"] = asset_origin.rstrip("/")
    if asset_public_base:
        lesson_cfg["asset_public_base_url"] = asset_public_base.rstrip("/")
    if asset_delivery_mode:
        lesson_cfg["asset_delivery_mode"] = asset_delivery_mode
    if asset_pack_local_root:
        lesson_cfg["asset_pack_local_root"] = asset_pack_local_root.rstrip("/")
    if asset_pack_mount_root:
        lesson_cfg["asset_pack_mount_root"] = asset_pack_mount_root.rstrip("/")
    if step_timeout_floor:
        try:
            lesson_cfg["step_timeout_floor_sec"] = max(0.0, float(step_timeout_floor))
        except ValueError:
            pass
    if voice_rt_p95_disable_ms:
        try:
            lesson_cfg["voice_rt_p95_disable_ms"] = max(0.0, float(voice_rt_p95_disable_ms))
        except ValueError:
            pass
    if max_asset_bytes is not None:
        lesson_cfg["max_asset_bytes"] = max_asset_bytes
    if max_total_asset_bytes is not None:
        lesson_cfg["max_total_asset_bytes"] = max_total_asset_bytes
    if sd_cache_quota_bytes is not None:
        lesson_cfg["sd_cache_quota_bytes"] = sd_cache_quota_bytes
    if sd_gc_free_percent is not None:
        lesson_cfg["sd_gc_free_percent"] = sd_gc_free_percent
    if sd_preload_min_free_percent is not None:
        lesson_cfg["sd_preload_min_free_percent"] = sd_preload_min_free_percent
    if sample_flag is not None:
        lesson_cfg["sample_lesson"] = sample_flag
    if sample_asset_base:
        lesson_cfg["sample_asset_base_url"] = sample_asset_base.rstrip("/")
    if sample_step_dwell:
        try:
            lesson_cfg["sample_step_dwell_sec"] = max(0.0, float(sample_step_dwell))
        except ValueError:
            pass
    if sample_mode:
        lesson_cfg["sample_mode"] = sample_mode.strip().lower()

    if course_url:
        normalized = course_url.rstrip("/")
        server_cfg = config.get("server")
        if not isinstance(server_cfg, Mapping):
            server_cfg = {}
            config["server"] = server_cfg
        server_cfg["api_url"] = normalized
        # An EXPLICIT COURSE_BACKEND_URL/TBOT_BACKEND_API_URL is the operator's
        # production backend and MUST win over any shipped/stale committed
        # lesson.api_base default, so the runtime never pulls from a stale endpoint.
        lesson_cfg["api_base"] = normalized
    # Auto-enable ONLY when an EXPLICIT backend URL env (course_url) is present — the
    # shipped lesson.api_base / server.api_url default is deliberately never enough to
    # arm production lessons (dark-by-default; see docstring + boot-guard).
    if flag is None and not lesson_cfg.get("runtime_enabled") and course_url:
        server_cfg = config.get("server")
        api_base = lesson_cfg.get("api_base")
        if not api_base and isinstance(server_cfg, Mapping):
            api_base = server_cfg.get("api_url")
        if _clean_env("TBOT_DEVICE_MINT_SECRET") and lesson_cfg.get("asset_origin_base") and api_base:
            lesson_cfg["runtime_enabled"] = True
    return config

def _apply_connection_env_overrides(config):
    if not isinstance(config, Mapping):
        return config
    timeout = _clean_env("TBOT_CLOSE_CONNECTION_NO_VOICE_TIME")
    if timeout:
        try:
            config["close_connection_no_voice_time"] = max(1, int(timeout))
        except ValueError:
            pass
    allow_device_config_fallback = _parse_bool_env("TBOT_ALLOW_DEVICE_CONFIG_FALLBACK")
    if allow_device_config_fallback is not None:
        config["allow_device_config_fallback"] = allow_device_config_fallback
    return config

def _split_csv_env(name):
    raw = _clean_env(name)
    if raw is None:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]

def _apply_voice_env_overrides(config):
    """Let production env force Google Live when manager-api config is stale."""
    if not isinstance(config, Mapping):
        return config

    voice_mode = _clean_env("TBOT_VOICE_MODE")
    google_live_enabled = _parse_bool_env("TBOT_GOOGLE_LIVE_ENABLED")
    force_google_live = (
        str(voice_mode or "").strip().lower() == "google_live"
        or google_live_enabled is True
    )
    if force_google_live:
        current_voice_mode = config.get("voice_mode")
        if not isinstance(current_voice_mode, Mapping):
            current_voice_mode = {}
        current_voice_mode["type"] = "google_live"
        current_voice_mode["fallback_to_classic_on_error"] = False
        config["voice_mode"] = current_voice_mode

    google_live = config.get("google_live")
    if not isinstance(google_live, Mapping):
        google_live = {}
        config["google_live"] = google_live

    api_key = (
        _clean_env("GOOGLE_API_KEY")
        or _clean_env("TBOT_GOOGLE_LIVE_API_KEY")
        or _clean_env("GEMINI_API_KEY")
        or _clean_env("GOOGLE_GEMINI_API_KEY")
    )
    if api_key:
        google_live["api_key"] = api_key
    model = _clean_env("TBOT_GOOGLE_LIVE_MODEL") or _clean_env("GOOGLE_LIVE_MODEL")
    if model:
        google_live["model"] = model
    language_code = _clean_env("TBOT_GOOGLE_LIVE_LANGUAGE_CODE")
    if language_code:
        google_live["language_code"] = language_code
    voice_name = _clean_env("TBOT_GOOGLE_LIVE_VOICE_NAME") or _clean_env("GOOGLE_LIVE_VOICE_NAME")
    if voice_name:
        google_live["voice_name"] = voice_name
    session_resumption_enabled = _parse_bool_env("TBOT_GOOGLE_LIVE_SESSION_RESUMPTION_ENABLED")
    if session_resumption_enabled is not None:
        google_live["session_resumption_enabled"] = session_resumption_enabled

    extra_wake_words = _split_csv_env("TBOT_WAKEUP_WORDS")
    if extra_wake_words:
        existing = config.get("wakeup_words")
        if not isinstance(existing, list):
            existing = []
        seen = {str(word).casefold() for word in existing}
        for word in extra_wake_words:
            key = word.casefold()
            if key not in seen:
                existing.append(word)
                seen.add(key)
        config["wakeup_words"] = existing
    return config

def _assert_lesson_runtime_boot_safe(config):
    if not isinstance(config, Mapping):
        return

    lesson_cfg = config.get("lesson")
    if not isinstance(lesson_cfg, Mapping) or not lesson_cfg.get("runtime_enabled"):
        return

    missing = [
        name
        for name in ("TBOT_DEVICE_MINT_SECRET", "LESSON_ASSET_ORIGIN_BASE")
        if not _clean_env(name)
    ]
    if not (_clean_env("COURSE_BACKEND_URL") or _clean_env("TBOT_BACKEND_API_URL")):
        missing.append("COURSE_BACKEND_URL or TBOT_BACKEND_API_URL")
    if missing:
        raise RuntimeError(
            "lesson.runtime_enabled=true requires boot env prerequisites; "
            f"missing: {', '.join(missing)}"
        )

    delivery_mode = str(lesson_cfg.get("asset_delivery_mode") or "").strip().lower()
    sd_pack_enabled = delivery_mode == "sd_pack" or lesson_cfg.get("sd_asset_pack_enabled") is True
    if sd_pack_enabled and not lesson_cfg.get("asset_pack_mount_root"):
        raise RuntimeError(
            "lesson.asset_delivery_mode=sd_pack requires LESSON_ASSET_PACK_MOUNT_ROOT "
            "so verified lesson assets can be materialized before firmware reads sd:// paths"
        )

def _assert_production_boot_safe(config):
    if _clean_env("NODE_ENV") != "production":
        return
    if not isinstance(config, Mapping):
        config = {}

    server_cfg = config.get("server")
    auth_cfg = server_cfg.get("auth") if isinstance(server_cfg, Mapping) else None
    auth_enabled = auth_cfg.get("enabled") if isinstance(auth_cfg, Mapping) else None
    if auth_enabled is not True:
        raise RuntimeError("production boot requires server.auth.enabled=true")
    auth_key = server_cfg.get("auth_key") if isinstance(server_cfg, Mapping) else None
    if not isinstance(auth_key, str) or not auth_key.strip():
        raise RuntimeError("production boot requires non-empty server.auth.auth_key")

    missing = [
        name
        for name in (
            "TBOT_REQUIRE_DEVICE_TOKEN",
            "JWT_PUBLIC_KEY",
            "TBOT_DEVICE_MINT_SECRET",
            "LESSON_ASSET_ORIGIN_BASE",
        )
        if not _clean_env(name)
    ]
    if missing:
        raise RuntimeError(
            "production boot requires TBOT_REQUIRE_DEVICE_TOKEN=true and env "
            f"prerequisites; missing: {', '.join(missing)}"
        )
    if _parse_bool_env("TBOT_REQUIRE_DEVICE_TOKEN") is not True:
        raise RuntimeError("production boot requires TBOT_REQUIRE_DEVICE_TOKEN=true")
    if _parse_bool_env("ADMIN_AUTH_DISABLED") is True:
        raise RuntimeError("ADMIN_AUTH_DISABLED must not be true in production")
    if _parse_bool_env("TBOT_BYPASS_VOICE_CONSENT") is True:
        raise RuntimeError("production boot forbids voice consent bypass: TBOT_BYPASS_VOICE_CONSENT")
    if isinstance(server_cfg, Mapping):
        if server_cfg.get("factory_test_claimed_all") is True:
            raise RuntimeError("production boot forbids server.factory_test_claimed_all=true")
        factory_claimed_devices = server_cfg.get("factory_test_claimed_devices") or []
        if isinstance(factory_claimed_devices, str):
            factory_claimed_devices = [factory_claimed_devices]
        if any(str(device).strip() for device in factory_claimed_devices):
            raise RuntimeError("production boot forbids server.factory_test_claimed_devices in production")
        voice_consent_bypass_devices = server_cfg.get("voice_consent_bypass_devices") or []
        if isinstance(voice_consent_bypass_devices, (str, Mapping)):
            voice_consent_bypass_devices = [voice_consent_bypass_devices]
        if any(str(device).strip() for device in voice_consent_bypass_devices):
            raise RuntimeError("production boot forbids server.voice_consent_bypass_devices in production")
    voice_mode = config.get("voice_mode")
    if not isinstance(voice_mode, Mapping) or voice_mode.get("type") != "google_live":
        raise RuntimeError("production boot requires voice_mode.type=google_live")
    google_live = config.get("google_live")
    google_live = google_live if isinstance(google_live, Mapping) else {}
    live_model = str(google_live.get("model") or "").strip()
    production_model = GOOGLE_LIVE_DEFAULTS["model"]
    if live_model and live_model != production_model:
        raise RuntimeError(
            f"production boot requires google_live.model={production_model}"
        )
    for key in ("enable_audio_input", "enable_audio_output", "native_voice"):
        if google_live.get(key, True) is not True:
            raise RuntimeError(f"production boot requires google_live.{key}=true")
    language_code = str(
        google_live.get("language_code") or GOOGLE_LIVE_DEFAULTS["language_code"]
    ).strip()
    if language_code != GOOGLE_LIVE_DEFAULTS["language_code"]:
        raise RuntimeError(
            "production boot requires "
            f"google_live.language_code={GOOGLE_LIVE_DEFAULTS['language_code']}"
        )
    _assert_production_google_live_aec_ready(config)


def _assert_production_google_live_aec_ready(config):
    voice_mode = config.get("voice_mode") if isinstance(config, Mapping) else None
    if not isinstance(voice_mode, Mapping) or voice_mode.get("type") != "google_live":
        return
    google_live = config.get("google_live") if isinstance(config, Mapping) else None
    if not isinstance(google_live, Mapping):
        google_live = {}
    from core.voice.aec.aec_processor import AecProcessor

    processor = AecProcessor(
        sample_rate=int(google_live.get("input_sample_rate", 16000)),
        frame_ms=int(google_live.get("aec_frame_ms", 10)),
        filter_ms=int(google_live.get("aec_filter_length_ms", 200)),
        enabled=bool(google_live.get("aec_enabled", False)),
    )
    if processor.bypassed:
        reason = processor.reason or "unknown"
        raise RuntimeError(f"production boot requires active AEC; bypassed={reason}")

_LOCAL_LESSON_ASSET_PACK_KEYS = (
    "asset_delivery_mode",
    "sd_asset_pack_enabled",
    "asset_public_base_url",
    "asset_public_base",
    "asset_pack_local_root",
    "asset_pack_mount_root",
    "asset_cache_root",
    "asset_origin_base",
    "sd_cache_quota_bytes",
    "sd_gc_free_percent",
    "sd_preload_min_free_percent",
)


def _merge_local_lesson_asset_pack_settings(api_config, local_config):
    """Overlay local lesson asset-pack settings onto manager-api config.

    Manager-api is the voice/agent source of truth, but lesson SD pack delivery is
    an ops/runtime concern often configured only via data/.config.yaml or env.
    Without this merge, fan-out reports ``sd_pack_disabled`` even when local YAML
    has ``asset_delivery_mode: sd_pack``.
    """
    if not isinstance(api_config, Mapping):
        return api_config
    local_lesson = local_config.get("lesson") if isinstance(local_config, Mapping) else None
    if not isinstance(local_lesson, Mapping):
        return api_config

    api_lesson = api_config.get("lesson")
    if not isinstance(api_lesson, Mapping):
        api_lesson = {}
        api_config["lesson"] = api_lesson
    elif not isinstance(api_lesson, dict):
        api_lesson = dict(api_lesson)
        api_config["lesson"] = api_lesson

    for key in _LOCAL_LESSON_ASSET_PACK_KEYS:
        if key not in local_lesson:
            continue
        value = local_lesson.get(key)
        if value is None or value == "":
            continue
        # Local/ops value wins for pack delivery knobs (API rarely sets them).
        api_lesson[key] = value
    return api_config


def _apply_server_endpoint_env_overrides(config):
    """Let Docker/.env repair public OTA endpoints without editing volumes."""
    if not isinstance(config, Mapping):
        return config

    # Restored lesson env knobs (runs even when no OTA-endpoint env is set).
    config = _apply_lesson_env_overrides(config)
    config = _apply_connection_env_overrides(config)
    config = _apply_voice_env_overrides(config)

    websocket_url = _clean_env("TBOT_PUBLIC_WEBSOCKET_URL")
    api_url = _clean_env("TBOT_BACKEND_API_URL")
    auth_key = _clean_env("TBOT_SERVER_AUTH_KEY")
    auth_enabled = _parse_bool_env("TBOT_SERVER_AUTH_ENABLED")
    if not websocket_url and not api_url and not auth_key and auth_enabled is None:
        return config

    server_config = config.get("server")
    if not isinstance(server_config, Mapping):
        server_config = {}
        config["server"] = server_config

    if websocket_url:
        server_config["websocket"] = websocket_url
    if api_url:
        normalized_api_url = api_url.rstrip("/")
        prior_api_url = server_config.get("api_url")
        server_config["api_url"] = normalized_api_url
        # Keep lesson.api_base coherent with the server endpoint: the lesson
        # runtime reads lesson.api_base first, else server.api_url. If api_base
        # was only defaulted (empty, or synced to the previous server.api_url by
        # _apply_lesson_env_overrides via COURSE_BACKEND_URL) then re-point it at
        # TBOT_BACKEND_API_URL so the two halves don't split-brain across two
        # backends. An explicitly author-set api_base (one that differs from the
        # prior server.api_url) is preserved.
        lesson_config = config.get("lesson")
        if isinstance(lesson_config, Mapping):
            lesson_api_base = lesson_config.get("api_base")
            if not lesson_api_base or lesson_api_base == prior_api_url:
                lesson_config["api_base"] = normalized_api_url
    if auth_key:
        server_config["auth_key"] = auth_key
    if auth_enabled is not None:
        auth_config = server_config.get("auth")
        if not isinstance(auth_config, Mapping):
            auth_config = {}
            server_config["auth"] = auth_config
        auth_config["enabled"] = auth_enabled
    return config


def load_config():
    """Load config file"""
    from core.utils.cache.manager import CacheType, cache_manager

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
    config = _apply_server_endpoint_env_overrides(config)
    config = normalize_voice_config(config)
    _assert_production_boot_safe(config)
    _assert_lesson_runtime_boot_safe(config)
    # Initialize directory
    ensure_directories(config)

    # Cache Config
    cache_manager.set(CacheType.CONFIG, "main_config", config)
    return config


async def load_config_async():
    """Load config file from async context without blocking the running loop."""
    from core.utils.cache.manager import CacheType, cache_manager

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
    config = _apply_server_endpoint_env_overrides(config)
    config = normalize_voice_config(config)
    _assert_production_boot_safe(config)
    _assert_lesson_runtime_boot_safe(config)
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
        local_server_config = config["server"]
        config_data["server"] = {
            "ip": local_server_config.get("ip", ""),
            "port": local_server_config.get("port", ""),
            "http_port": local_server_config.get("http_port", ""),
            "websocket": local_server_config.get("websocket", ""),
            "api_url": local_server_config.get("api_url", ""),
            "vision_explain": local_server_config.get("vision_explain", ""),
            "auth_key": local_server_config.get("auth_key", ""),
        }
        for local_only_key in (
            "firmware_download_scheme",
            "factory_test_claimed_devices",
            "factory_test_claimed_all",
            "claim_reset_devices",
            "claim_reset_nonce",
        ):
            if local_only_key in local_server_config:
                config_data["server"][local_only_key] = local_server_config[local_only_key]
    config_data.setdefault("server", {})["auth"] = {"enabled": auth_enabled}
    # If server has noprompt_templateThen read from local config
    if not config_data.get("prompt_template"):
        config_data["prompt_template"] = config.get("prompt_template")
    config_data = _apply_base_google_live_policy(config_data, config)
    # Manager-api payloads often omit lesson SD-pack / public asset knobs. Preserve
    # local data/.config.yaml (and later env overrides) so production lab/prod can
    # enable sd_pack + correct asset_public_base_url without forking the API config.
    config_data = _merge_local_lesson_asset_pack_settings(config_data, config)
    config_data = _apply_server_endpoint_env_overrides(config_data)
    config_data = normalize_voice_config(config_data)
    _assert_production_boot_safe(config_data)
    _assert_lesson_runtime_boot_safe(config_data)
    return config_data


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
    private_config = _apply_base_google_live_policy(private_config, config)
    private_config = _apply_voice_env_overrides(private_config)
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
            if not isinstance(provider, Mapping):
                continue
            output_dir = provider.get("output_dir", "")
            if output_dir:
                dirs_to_create.add(output_dir)

    # Based onselected_moduleCreate model directory
    selected_modules = config.get("selected_module", {})
    for module_type in ["ASR", "LLM", "TTS"]:
        selected_provider = selected_modules.get(module_type)
        if not selected_provider:
            continue
        if config.get(module_type) is None:
            continue
        provider_config = config.get(module_type, {}).get(selected_provider, {})
        if not isinstance(provider_config, Mapping):
            continue
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
