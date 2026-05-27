import unittest

from config.config_loader import merge_configs, normalize_voice_config


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
                "google_live": {"model": "gemini-2.5-flash-native-audio-preview-12-2025"},
            },
        )
        merged = normalize_voice_config(merged)

        self.assertEqual(merged["voice_mode"]["type"], "google_live")
        self.assertFalse(merged["voice_mode"]["fallback_to_classic_on_error"])
        self.assertEqual(
            merged["google_live"]["model"],
            "gemini-2.5-flash-native-audio-preview-12-2025",
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
        self.assertEqual(merged["google_live"]["input_flush_delay_sec"], 0.8)
        self.assertEqual(merged["google_live"]["interrupt_rms_threshold"], 5000)
        self.assertEqual(
            merged["google_live"]["interrupt_min_input_duration_sec"],
            0.42,
        )
        self.assertEqual(merged["google_live"]["interrupt_min_output_age_sec"], 0.25)
        self.assertFalse(merged["google_live"]["interrupt_on_input_while_speaking"])
        self.assertFalse(merged["google_live"]["drop_input_while_speaking"])
        self.assertFalse(merged["google_live"]["barge_in"])
        # PR4 P4.5: tuned defaults per baseline data (max echo RMS 8310).
        self.assertEqual(merged["google_live"]["barge_in_rms_threshold"], 4500)
        self.assertEqual(
            merged["google_live"]["barge_in_min_input_duration_sec"],
            0.30,
        )
        self.assertEqual(merged["google_live"]["barge_in_min_output_age_sec"], 0.25)
        self.assertTrue(merged["google_live"]["disable_server_side_interruptions"])
        self.assertFalse(merged["google_live"]["server_side_vad_enabled"])
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

    def test_google_live_missing_section_gets_production_defaults(self):
        merged = normalize_voice_config({"voice_mode": {"type": "google_live"}})

        self.assertEqual(merged["google_live"]["language_code"], "vi-VN")
        self.assertEqual(merged["google_live"]["interrupt_rms_threshold"], 5000)
        # PR4 P4.5: tuned defaults.
        self.assertEqual(merged["google_live"]["barge_in_min_input_duration_sec"], 0.30)
        self.assertEqual(merged["google_live"]["barge_in_rms_threshold"], 4500)
