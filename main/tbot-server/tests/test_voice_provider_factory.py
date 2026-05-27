import asyncio
import unittest
from unittest.mock import patch

from config.config_loader import normalize_voice_config
from config.config_loader import get_config_from_api_async
from config.config_loader import load_config
from core.voice.session_provider.factory import create_voice_session_provider


class DummyConn:
    def __init__(self, mode):
        self.config = {"voice_mode": {"type": mode}}


class VoiceProviderFactoryTest(unittest.TestCase):
    def test_returns_classic_pipeline_provider_for_classic_mode(self):
        provider = create_voice_session_provider(DummyConn("classic_pipeline"))
        self.assertEqual(provider.__class__.__name__, "ClassicPipelineProvider")

    def test_returns_google_live_provider_for_google_live_mode(self):
        provider = create_voice_session_provider(DummyConn("google_live"))
        self.assertEqual(provider.__class__.__name__, "GoogleLiveProvider")

    def test_factory_falls_back_to_classic_for_malformed_voice_mode(self):
        conn = DummyConn("classic_pipeline")
        conn.config = {"voice_mode": "google_live"}
        provider = create_voice_session_provider(conn)
        self.assertEqual(provider.__class__.__name__, "ClassicPipelineProvider")

    def test_normalize_voice_config_adds_defaults(self):
        config = normalize_voice_config({})
        self.assertEqual(config["voice_mode"]["type"], "classic_pipeline")
        self.assertTrue(config["voice_mode"]["fallback_to_classic_on_error"])
        self.assertEqual(config["google_live"], {})

    def test_normalize_voice_config_preserves_existing_values(self):
        config = normalize_voice_config(
            {
                "voice_mode": {
                    "type": "google_live",
                    "fallback_to_classic_on_error": False,
                },
                "google_live": {"model": "gemini-live"},
            }
        )
        self.assertEqual(config["voice_mode"]["type"], "google_live")
        self.assertFalse(config["voice_mode"]["fallback_to_classic_on_error"])
        self.assertEqual(config["google_live"]["model"], "gemini-live")

    def test_normalize_voice_config_replaces_none_values_with_default_mappings(self):
        config = normalize_voice_config({"voice_mode": None, "google_live": None})
        self.assertEqual(config["voice_mode"]["type"], "classic_pipeline")
        self.assertTrue(config["voice_mode"]["fallback_to_classic_on_error"])
        self.assertEqual(config["google_live"], {})

    def test_normalize_voice_config_replaces_non_mapping_voice_mode(self):
        config = normalize_voice_config({"voice_mode": "google_live"})
        self.assertEqual(config["voice_mode"]["type"], "classic_pipeline")
        self.assertTrue(config["voice_mode"]["fallback_to_classic_on_error"])
        self.assertEqual(config["google_live"], {})

    @patch("config.config_loader.ensure_directories")
    @patch("config.config_loader.read_config", side_effect=[None, "bad-custom-config"])
    @patch("core.utils.cache.manager.cache_manager.set")
    @patch("core.utils.cache.manager.cache_manager.get", return_value=None)
    def test_load_config_treats_non_mapping_yaml_results_as_empty_dicts(
        self,
        _cache_get,
        _cache_set,
        _read_config,
        _ensure_directories,
    ):
        config = load_config()
        self.assertEqual(config["voice_mode"]["type"], "classic_pipeline")
        self.assertTrue(config["voice_mode"]["fallback_to_classic_on_error"])
        self.assertEqual(config["google_live"], {})

    @patch("config.config_loader.read_config", side_effect=[{}, {"manager-api": {"url": "http://manager"}}])
    @patch("core.utils.cache.manager.cache_manager.get", return_value=None)
    def test_load_config_fails_fast_in_running_event_loop_for_manager_api(
        self,
        _cache_get,
        _read_config,
    ):
        async def call_load_config():
            with self.assertRaisesRegex(RuntimeError, "use load_config_async"):
                load_config()

        asyncio.run(call_load_config())

    @patch("config.config_loader.init_service")
    @patch("config.config_loader.get_server_config")
    def test_manager_api_config_preserves_local_public_websocket_url(
        self,
        get_server_config,
        _init_service,
    ):
        async def server_config():
            return {
                "server": {"auth": {"enabled": False}},
                "selected_module": {},
                "prompt_template": "agent-base-prompt.txt",
            }

        get_server_config.side_effect = server_config
        config = asyncio.run(
            get_config_from_api_async(
                {
                    "manager-api": {
                        "url": "http://manager-api/tbot",
                        "secret": "secret",
                    },
                    "server": {
                        "ip": "0.0.0.0",
                        "port": 8000,
                        "http_port": 8003,
                        "websocket": "wss://public.example.com/tbot/v1/",
                        "vision_explain": "https://public.example.com/mcp/vision/explain",
                        "auth_key": "local-auth",
                    },
                }
            )
        )

        self.assertEqual(
            config["server"]["websocket"],
            "wss://public.example.com/tbot/v1/",
        )
