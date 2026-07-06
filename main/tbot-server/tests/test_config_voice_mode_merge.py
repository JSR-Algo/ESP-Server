import os
import pathlib
import unittest
from unittest import mock

import yaml

from config.config_loader import (
    DEFAULT_GOOGLE_LIVE_VOICE_NAME,
    GOOGLE_LIVE_DEFAULTS,
    _apply_server_endpoint_env_overrides,
    merge_configs,
    normalize_voice_config,
)


def _tts_base_cfg():
    return {
        "selected_module": {"TTS": "EdgeTTS"},
        "TTS": {
            "EdgeTTS": {"type": "edge"},
            "GeminiTTS": {
                "type": "gemini",
                "model_name": "gemini-3.1-flash-tts-preview",
                "voice": "Kore",
                "api_key": "${PLACEHOLDER}",
            },
        },
        "voice_mode": {"type": "google_live"},
    }


class TtsProviderSelectionTest(unittest.TestCase):
    @mock.patch.dict(os.environ, {"TBOT_TTS_PROVIDER": "google", "GEMINI_API_KEY": "AIzaTESTKEY1234567890abcd"}, clear=False)
    def test_google_provider_selects_gemini_tts_with_real_key(self):
        out = normalize_voice_config(_tts_base_cfg())
        self.assertEqual(out["selected_module"]["TTS"], "GeminiTTS")
        gemini = out["TTS"]["GeminiTTS"]
        # Real Gemini key injected (NOT the Live ephemeral token / placeholder).
        self.assertEqual(gemini["api_key"], "AIzaTESTKEY1234567890abcd")
        # EdgeTTS kept only as the fallback safety net.
        self.assertEqual((gemini.get("fallback_tts") or {}).get("type"), "edge")

    @mock.patch.dict(os.environ, {"TBOT_FORCE_EDGE_TTS": "true", "TBOT_TTS_PROVIDER": "google"}, clear=False)
    def test_force_edge_still_wins_over_google(self):
        out = normalize_voice_config(_tts_base_cfg())
        self.assertEqual(out["selected_module"]["TTS"], "EdgeTTS")

    @mock.patch.dict(os.environ, {"TBOT_TTS_PROVIDER": "", "GEMINI_API_KEY": ""}, clear=False)
    def test_no_provider_leaves_selection_untouched(self):
        out = normalize_voice_config(_tts_base_cfg())
        self.assertEqual(out["selected_module"]["TTS"], "EdgeTTS")


class ConfigVoiceModeMergeTest(unittest.TestCase):
    def test_default_voice_mode_is_classic(self):
        merged = normalize_voice_config({})

        self.assertEqual(merged["voice_mode"]["type"], "classic_pipeline")
        self.assertTrue(merged["voice_mode"]["fallback_to_classic_on_error"])
        self.assertEqual(merged["google_live"], {})

    def test_custom_voice_mode_survives_merge(self):
        merged = merge_configs(
            {"voice_mode": {"type": "classic_pipeline"}},
            {
                "voice_mode": {
                    "type": "google_live",
                    "fallback_to_classic_on_error": False,
                },
                "google_live": {"model": "custom-live-model"},
            },
        )
        merged = normalize_voice_config(merged)

        self.assertEqual(merged["voice_mode"]["type"], "google_live")
        self.assertFalse(merged["voice_mode"]["fallback_to_classic_on_error"])
        self.assertEqual(
            merged["google_live"]["model"],
            "custom-live-model",
        )

    def test_google_live_missing_fields_get_production_defaults(self):
        merged = normalize_voice_config(
            {
                "voice_mode": {"type": "google_live"},
                "google_live": {"model": "gemini-live"},
            }
        )

        self.assertEqual(merged["google_live"]["model"], "gemini-live")
        self.assertFalse(merged["voice_mode"]["fallback_to_classic_on_error"])
        self.assertTrue(merged["google_live"]["enable_audio_input"])
        self.assertEqual(merged["google_live"]["language_code"], "vi-VN")
        self.assertEqual(merged["google_live"]["interrupt_policy"], "wake_or_transcript")
        self.assertFalse(merged["google_live"]["raw_audio_barge_in_enabled"])
        self.assertEqual(merged["google_live"]["input_flush_delay_sec"], 1.4)
        self.assertEqual(merged["google_live"]["conversation_input_flush_delay_sec"], 0.45)
        self.assertEqual(merged["google_live"]["input_speech_tail_ms"], 1300)
        self.assertEqual(merged["google_live"]["conversation_input_speech_tail_ms"], 420)
        self.assertEqual(merged["google_live"]["input_min_capture_ms"], 400)
        self.assertEqual(merged["google_live"]["input_max_capture_ms"], 8000)
        self.assertEqual(merged["google_live"]["conversation_input_max_capture_ms"], 2500)
        self.assertEqual(merged["google_live"]["input_speech_rms_threshold"], 500)
        self.assertEqual(merged["google_live"]["lesson_child_input_speech_rms_threshold"], 2000)
        self.assertEqual(merged["google_live"]["input_gain"], 6.0)
        self.assertEqual(merged["google_live"]["waiting_model_timeout_sec"], 4.0)
        self.assertEqual(merged["google_live"]["waiting_model_retry_prompt_after_sec"], 12.0)
        self.assertEqual(merged["google_live"]["lesson_prompt_output_guard_timeout_sec"], 30.0)
        self.assertEqual(merged["google_live"]["lesson_prompt_playback_guard_timeout_sec"], 12.0)
        self.assertEqual(merged["google_live"]["interrupt_rms_threshold"], 5000)
        self.assertEqual(
            merged["google_live"]["interrupt_min_input_duration_sec"],
            0.42,
        )
        self.assertEqual(merged["google_live"]["interrupt_min_output_age_sec"], 0.25)
        self.assertEqual(merged["google_live"]["interruption_min_output_age_sec"], 0.0)
        self.assertFalse(merged["google_live"]["interrupt_on_input_while_speaking"])
        self.assertFalse(merged["google_live"]["drop_input_while_speaking"])
        self.assertFalse(merged["google_live"]["barge_in"])
        self.assertTrue(merged["google_live"]["music_auto_pause_on_user_speech"])
        self.assertEqual(merged["google_live"]["echo_tail_suppression_ms"], 400)
        self.assertEqual(merged["google_live"]["interrupt_replay_buffer_ms"], 900)
        self.assertEqual(merged["google_live"]["reconnect_buffer_ms"], 2000)
        # PR4 P4.5: tuned defaults per baseline data (max echo RMS 8310).
        self.assertEqual(merged["google_live"]["barge_in_rms_threshold"], 4500)
        self.assertEqual(
            merged["google_live"]["barge_in_min_input_duration_sec"],
            0.30,
        )
        self.assertEqual(merged["google_live"]["barge_in_min_output_age_sec"], 0.25)
        self.assertEqual(
            merged["google_live"]["barge_in_transcript_min_output_age_sec"],
            0.0,
        )
        self.assertFalse(merged["google_live"]["disable_server_side_interruptions"])
        self.assertEqual(
            merged["google_live"]["activity_handling"],
            "START_OF_ACTIVITY_INTERRUPTS",
        )
        self.assertTrue(merged["google_live"]["server_side_vad_enabled"])
        # Reconnect defaults track config.yaml (PR2 P2.6: 6 retries, 250ms base).
        self.assertEqual(
            merged["google_live"]["reconnect"],
            {
                "enabled": True,
                "max_retries": 6,
                "backoff_ms": 250,
                "backoff_multiplier": 2,
            },
        )

    def test_config_yaml_uses_production_live_audio_policy(self):
        config_path = pathlib.Path(__file__).resolve().parents[1] / "config.yaml"
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        google_live = config["google_live"]

        self.assertEqual(google_live["interrupt_policy"], "wake_or_transcript")
        self.assertFalse(google_live["raw_audio_barge_in_enabled"])
        self.assertFalse(google_live["disable_server_side_interruptions"])
        self.assertEqual(google_live["activity_handling"], "START_OF_ACTIVITY_INTERRUPTS")
        self.assertFalse(google_live["barge_in"])
        self.assertFalse(google_live["interrupt_on_input_while_speaking"])
        self.assertFalse(google_live["echo_bypass_interrupt_enabled"])
        self.assertTrue(google_live["suppress_robot_output_echo"])
        self.assertTrue(google_live["music_auto_pause_on_user_speech"])
        self.assertEqual(google_live["echo_tail_suppression_ms"], 400)
        # Tuned up from the 1.0 code default (child-speech capture fix); must stay
        # >= input_speech_tail_ms so the idle safety-net doesn't re-cut a paused child.
        self.assertEqual(google_live["input_flush_delay_sec"], 1.4)
        self.assertEqual(google_live["conversation_input_flush_delay_sec"], 0.45)
        self.assertEqual(google_live["waiting_model_timeout_sec"], 4.0)
        self.assertEqual(google_live["interruption_min_output_age_sec"], 0.0)
        self.assertEqual(google_live["barge_in_transcript_min_output_age_sec"], 0.0)
        self.assertEqual(GOOGLE_LIVE_DEFAULTS["input_flush_delay_sec"], 1.4)
        self.assertEqual(GOOGLE_LIVE_DEFAULTS["conversation_input_flush_delay_sec"], 0.45)
        self.assertEqual(GOOGLE_LIVE_DEFAULTS["input_speech_tail_ms"], 1300)
        self.assertEqual(GOOGLE_LIVE_DEFAULTS["conversation_input_speech_tail_ms"], 420)
        self.assertEqual(GOOGLE_LIVE_DEFAULTS["input_min_capture_ms"], 400)
        self.assertEqual(GOOGLE_LIVE_DEFAULTS["input_max_capture_ms"], 8000)
        self.assertEqual(GOOGLE_LIVE_DEFAULTS["conversation_input_max_capture_ms"], 2500)
        self.assertEqual(GOOGLE_LIVE_DEFAULTS["input_speech_rms_threshold"], 500)
        self.assertEqual(GOOGLE_LIVE_DEFAULTS["lesson_child_input_speech_rms_threshold"], 2000)
        self.assertEqual(GOOGLE_LIVE_DEFAULTS["input_gain"], 6.0)
        self.assertEqual(GOOGLE_LIVE_DEFAULTS["waiting_model_timeout_sec"], 4.0)
        self.assertEqual(GOOGLE_LIVE_DEFAULTS["interruption_min_output_age_sec"], 0.0)
        self.assertEqual(
            GOOGLE_LIVE_DEFAULTS["barge_in_transcript_min_output_age_sec"],
            0.0,
        )
        self.assertFalse(GOOGLE_LIVE_DEFAULTS["raw_audio_barge_in_enabled"])
        self.assertFalse(GOOGLE_LIVE_DEFAULTS["disable_server_side_interruptions"])
        self.assertEqual(
            GOOGLE_LIVE_DEFAULTS["activity_handling"],
            "START_OF_ACTIVITY_INTERRUPTS",
        )

    def test_config_yaml_keeps_lesson_runtime_dark_by_default(self):
        config_path = pathlib.Path(__file__).resolve().parents[1] / "config.yaml"
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))

        self.assertIs(config["lesson"]["runtime_enabled"], False)

    def test_google_live_missing_section_gets_production_defaults(self):
        merged = normalize_voice_config({"voice_mode": {"type": "google_live"}})

        self.assertEqual(merged["google_live"]["language_code"], "vi-VN")
        self.assertTrue(merged["google_live"]["aec_enabled"])
        self.assertEqual(merged["google_live"]["interrupt_rms_threshold"], 5000)
        # PR4 P4.5: tuned defaults.
        self.assertEqual(merged["google_live"]["barge_in_min_input_duration_sec"], 0.30)
        self.assertEqual(merged["google_live"]["barge_in_rms_threshold"], 4500)

    def test_google_live_runtime_policy_sanitizes_unsafe_api_overrides(self):
        merged = normalize_voice_config(
            {
                "voice_mode": {"type": "google_live"},
                "google_live": {
                    "barge_in": True,
                    "raw_audio_barge_in_enabled": True,
                    "interrupt_on_input_while_speaking": True,
                    "disable_server_side_interruptions": True,
                    "activity_handling": "NO_INTERRUPTION",
                    "echo_bypass_interrupt_enabled": True,
                    "server_side_vad_enabled": True,
                    "interruption_min_output_age_sec": 2.0,
                    "barge_in_transcript_min_output_age_sec": 2.0,
                    "waiting_model_timeout_sec": 12.0,
                    "voice_name": "Puck",
                    "aec_enabled": False,
                },
            }
        )

        self.assertFalse(merged["google_live"]["barge_in"])
        self.assertFalse(merged["google_live"]["raw_audio_barge_in_enabled"])
        self.assertFalse(merged["google_live"]["interrupt_on_input_while_speaking"])
        self.assertFalse(merged["google_live"]["echo_bypass_interrupt_enabled"])
        self.assertFalse(merged["google_live"]["disable_server_side_interruptions"])
        self.assertEqual(
            merged["google_live"]["activity_handling"],
            "START_OF_ACTIVITY_INTERRUPTS",
        )
        self.assertTrue(merged["google_live"]["server_side_vad_enabled"])
        self.assertEqual(merged["google_live"]["interruption_min_output_age_sec"], 0.0)
        self.assertEqual(
            merged["google_live"]["barge_in_transcript_min_output_age_sec"],
            0.0,
        )
        self.assertEqual(merged["google_live"]["waiting_model_timeout_sec"], 4.0)
        self.assertEqual(
            merged["google_live"]["voice_name"],
            DEFAULT_GOOGLE_LIVE_VOICE_NAME,
        )
        self.assertTrue(merged["google_live"]["aec_enabled"])

    @mock.patch.dict(
        os.environ,
        {
            "TBOT_GOOGLE_LIVE_ENABLED": "true",
            "TBOT_GOOGLE_LIVE_SESSION_RESUMPTION_ENABLED": "false",
        },
        clear=False,
    )
    def test_env_can_disable_google_live_session_resumption(self):
        config = _apply_server_endpoint_env_overrides({})
        merged = normalize_voice_config(config)

        self.assertFalse(merged["google_live"]["session_resumption_enabled"])
