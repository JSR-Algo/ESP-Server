import unittest
from unittest.mock import patch

from config.models import AuthConfig, ServerConfig, TBotConfig, VoiceConfig
from config.validator import validate_config_or_die
import config.settings as settings


class ConfigModelsValidatorTest(unittest.TestCase):
    def test_models_apply_defaults_and_allow_extra_config(self):
        config = TBotConfig(extra_section={"enabled": True})

        self.assertIsInstance(config.server, ServerConfig)
        self.assertIsInstance(config.server.auth, AuthConfig)
        self.assertIsInstance(config.voice, VoiceConfig)
        self.assertEqual(config.server.port, 8000)
        self.assertEqual(config.server.auth.token_expiry_seconds, 86400)
        self.assertEqual(config.voice.frame_duration, 60)
        self.assertEqual(config.extra_section, {"enabled": True})

    def test_plugin_secret_validator_rejects_direct_and_nested_placeholders(self):
        self.assertEqual(TBotConfig(plugins={"weather": {"api_key": "ok"}}).plugins, {"weather": {"api_key": "ok"}})
        with self.assertRaisesRegex(ValueError, "Plugin secret api_key"):
            TBotConfig(plugins={"api_key": "__REPLACE_ME__"})
        with self.assertRaisesRegex(ValueError, "Plugin secret weather.api_key"):
            TBotConfig(plugins={"weather": {"api_key": "__REPLACE_ME__"}})

    def test_validate_config_or_die_returns_config_or_exits_with_validation_error(self):
        config = validate_config_or_die({"server": {"port": 9000}})

        self.assertEqual(config.server.port, 9000)
        with self.assertRaisesRegex(SystemExit, "Config validation failed"):
            validate_config_or_die({"plugins": {"api_key": "__REPLACE_ME__"}})


class SettingsConfigFileTest(unittest.TestCase):
    def setUp(self):
        settings.config_file_valid = False

    def tearDown(self):
        settings.config_file_valid = False

    def test_check_config_file_returns_immediately_when_already_valid(self):
        settings.config_file_valid = True


        with patch.object(settings.os.path, "exists", side_effect=AssertionError("should not check")):
            self.assertIsNone(settings.check_config_file())

    def test_check_config_file_raises_when_custom_config_missing(self):
        with patch.object(settings, "get_project_dir", return_value="/project/"), patch.object(
            settings.os.path, "exists", return_value=False
        ):
            with self.assertRaisesRegex(FileNotFoundError, "data/.config.yaml"):
                settings.check_config_file()

    def test_check_config_file_marks_valid_for_local_config(self):
        with patch.object(settings, "get_project_dir", return_value="/project/"), patch.object(
            settings.os.path, "exists", return_value=True
        ), patch.object(settings, "load_config", return_value={"read_config_from_api": False}):
            settings.check_config_file()

        self.assertTrue(settings.config_file_valid)

    def test_check_config_file_accepts_api_config_without_selected_module(self):
        with patch.object(settings, "get_project_dir", return_value="/project/"), patch.object(
            settings.os.path, "exists", return_value=True
        ), patch.object(settings, "load_config", return_value={"read_config_from_api": True}), patch.object(
            settings, "read_config", return_value={}
        ):
            settings.check_config_file()

        self.assertTrue(settings.config_file_valid)

    def test_check_config_file_rejects_mixed_api_and_local_console_config(self):
        with patch.object(settings, "get_project_dir", return_value="/project/"), patch.object(
            settings.os.path, "exists", return_value=True
        ), patch.object(settings, "load_config", return_value={"read_config_from_api": True}), patch.object(
            settings, "read_config", return_value={"selected_module": {"Intent": "function_call"}}
        ):
            with self.assertRaisesRegex(ValueError, "both console config and local config"):
                settings.check_config_file()


if __name__ == "__main__":
    unittest.main()
