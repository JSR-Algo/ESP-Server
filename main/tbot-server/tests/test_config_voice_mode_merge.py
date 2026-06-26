import os
import pathlib
import unittest
from unittest import mock

import yaml

from config.config_loader import GOOGLE_LIVE_DEFAULTS, merge_configs, normalize_voice_config


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
        self.assertTrue(merged["google_live"]["enable_audio_input"])
        self.assertEqual(merged["google_live"]["language_code"], "vi-VN")
        self.assertEqual(merged["google_live"]["interrupt_policy"], "wake_or_transcript")
        self.assertFalse(merged["google_live"]["raw_audio_barge_in_enabled"])
        self.assertEqual(merged["google_live"]["input_flush_delay_sec"], 1.0)
        self.assertEqual(merged["google_live"]["interrupt_rms_threshold"], 5000)
        self.assertEqual(
            merged["google_live"]["interrupt_min_input_duration_sec"],
            0.42,
        )
        self.assertEqual(merged["google_live"]["interrupt_min_output_age_sec"], 0.25)
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
        self.assertTrue(merged["google_live"]["disable_server_side_interruptions"])
        self.assertEqual(
            merged["google_live"]["activity_handling"],
            "NO_INTERRUPTION",
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

    def test_config_yaml_uses_safe_live_audio_policy(self):
        config_path = pathlib.Path(__file__).resolve().parents[1] / "config.yaml"
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        google_live = config["google_live"]

        self.assertEqual(google_live["interrupt_policy"], "wake_or_transcript")
        self.assertFalse(google_live["raw_audio_barge_in_enabled"])
        self.assertTrue(google_live["disable_server_side_interruptions"])
        self.assertEqual(google_live["activity_handling"], "NO_INTERRUPTION")
        self.assertFalse(google_live["barge_in"])
        self.assertFalse(google_live["interrupt_on_input_while_speaking"])
        self.assertTrue(google_live["music_auto_pause_on_user_speech"])
        self.assertEqual(google_live["echo_tail_suppression_ms"], 400)
        # Tuned up from the 1.0 code default (child-speech capture fix); must stay
        # >= input_speech_tail_ms so the idle safety-net doesn't re-cut a paused child.
        self.assertEqual(google_live["input_flush_delay_sec"], 1.4)
        self.assertFalse(GOOGLE_LIVE_DEFAULTS["raw_audio_barge_in_enabled"])
        self.assertTrue(GOOGLE_LIVE_DEFAULTS["disable_server_side_interruptions"])

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
                    "disable_server_side_interruptions": False,
                    "server_side_vad_enabled": True,
                },
            }
        )

        self.assertFalse(merged["google_live"]["barge_in"])
        self.assertFalse(merged["google_live"]["raw_audio_barge_in_enabled"])
        self.assertFalse(merged["google_live"]["interrupt_on_input_while_speaking"])
        self.assertTrue(merged["google_live"]["disable_server_side_interruptions"])
        self.assertEqual(
            merged["google_live"]["activity_handling"],
            "NO_INTERRUPTION",
        )
        self.assertTrue(merged["google_live"]["server_side_vad_enabled"])
