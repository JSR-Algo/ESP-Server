import unittest

import pathlib
import yaml

from config.config_loader import GOOGLE_LIVE_DEFAULTS, merge_configs, normalize_voice_config


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

    def test_config_yaml_uses_safe_live_audio_policy(self):
        config_path = pathlib.Path(__file__).resolve().parents[1] / "config.yaml"
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        google_live = config["google_live"]

        self.assertEqual(google_live["interrupt_policy"], "wake_or_transcript")
        self.assertFalse(google_live["raw_audio_barge_in_enabled"])
        self.assertFalse(google_live["disable_server_side_interruptions"])
        self.assertEqual(google_live["activity_handling"], "START_OF_ACTIVITY_INTERRUPTS")
        self.assertFalse(google_live["barge_in"])
        self.assertFalse(google_live["interrupt_on_input_while_speaking"])
        self.assertTrue(google_live["music_auto_pause_on_user_speech"])
        self.assertEqual(google_live["echo_tail_suppression_ms"], 400)
        self.assertEqual(google_live["input_flush_delay_sec"], 1.0)
        self.assertFalse(GOOGLE_LIVE_DEFAULTS["raw_audio_barge_in_enabled"])

    def test_google_live_missing_section_gets_production_defaults(self):
        merged = normalize_voice_config({"voice_mode": {"type": "google_live"}})

        self.assertEqual(merged["google_live"]["language_code"], "vi-VN")
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
        self.assertFalse(merged["google_live"]["disable_server_side_interruptions"])
        self.assertEqual(
            merged["google_live"]["activity_handling"],
            "START_OF_ACTIVITY_INTERRUPTS",
        )
        self.assertTrue(merged["google_live"]["server_side_vad_enabled"])
