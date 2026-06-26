import asyncio
import unittest
from unittest.mock import patch

from config.config_loader import get_config_from_api_async, load_config, normalize_voice_config
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

    @patch.dict("os.environ", {}, clear=True)
    @patch("config.config_loader.ensure_directories")
    @patch(
        "config.config_loader.read_config",
        side_effect=[{"lesson": {"runtime_enabled": True}}, {}],
    )
    @patch("core.utils.cache.manager.cache_manager.set")
    @patch("core.utils.cache.manager.cache_manager.get", return_value=None)
    def test_load_config_rejects_enabled_lesson_runtime_without_boot_prereqs(
        self,
        _cache_get,
        _cache_set,
        _read_config,
        _ensure_directories,
    ):
        with self.assertRaisesRegex(
            RuntimeError,
            "lesson.runtime_enabled=true.*TBOT_DEVICE_MINT_SECRET.*LESSON_ASSET_ORIGIN_BASE",
        ):
            load_config()

    @patch.dict(
        "os.environ",
        {
            "LESSON_RUNTIME_ENABLED": "true",
            "TBOT_DEVICE_MINT_SECRET": "mint-secret",
            "LESSON_ASSET_ORIGIN_BASE": "https://cdn.example.com/lesson-assets/",
            "LESSON_ASSET_DELIVERY_MODE": "sd_pack",
            "LESSON_ASSET_PACK_LOCAL_ROOT": "sd://sdcard/tbot/lesson-assets/",
        },
        clear=True,
    )
    @patch("config.config_loader.ensure_directories")
    @patch(
        "config.config_loader.read_config",
        side_effect=[{"lesson": {"runtime_enabled": False}}, {}],
    )
    @patch("core.utils.cache.manager.cache_manager.set")
    @patch("core.utils.cache.manager.cache_manager.get", return_value=None)
    def test_load_config_rejects_sd_pack_without_mount_root(
        self,
        _cache_get,
        _cache_set,
        _read_config,
        _ensure_directories,
    ):
        with self.assertRaisesRegex(
            RuntimeError,
            "sd_pack.*LESSON_ASSET_PACK_MOUNT_ROOT",
        ):
            load_config()

    @patch.dict(
        "os.environ",
        {
            "LESSON_RUNTIME_ENABLED": "true",
            "TBOT_DEVICE_MINT_SECRET": "mint-secret",
            "LESSON_ASSET_ORIGIN_BASE": "https://cdn.example.com/lesson-assets/",
            "LESSON_ASSET_PUBLIC_BASE_URL": "https://ota.example.com/",
            "LESSON_ASSET_DELIVERY_MODE": "sd_pack",
            "LESSON_ASSET_PACK_LOCAL_ROOT": "sd://sdcard/tbot/lesson-assets/",
            "LESSON_ASSET_PACK_MOUNT_ROOT": "/sdcard/tbot/lesson-assets/",
            "LESSON_STEP_TIMEOUT_FLOOR_SEC": "45",
            "LESSON_MAX_ASSET_BYTES": "1048576",
            "LESSON_MAX_TOTAL_ASSET_BYTES": "8388608",
        },
        clear=True,
    )
    @patch("config.config_loader.ensure_directories")
    @patch(
        "config.config_loader.read_config",
        side_effect=[{"lesson": {"runtime_enabled": False}}, {}],
    )
    @patch("core.utils.cache.manager.cache_manager.set")
    @patch("core.utils.cache.manager.cache_manager.get", return_value=None)
    def test_lesson_runtime_env_override_enables_runtime_with_boot_prereqs(
        self,
        _cache_get,
        _cache_set,
        _read_config,
        _ensure_directories,
    ):
        config = load_config()

        self.assertIs(config["lesson"]["runtime_enabled"], True)
        self.assertEqual(
            config["lesson"]["asset_origin_base"],
            "https://cdn.example.com/lesson-assets",
        )
        self.assertEqual(
            config["lesson"]["asset_public_base_url"],
            "https://ota.example.com",
        )
        self.assertEqual(config["lesson"]["asset_delivery_mode"], "sd_pack")
        self.assertEqual(
            config["lesson"]["asset_pack_local_root"],
            "sd://sdcard/tbot/lesson-assets",
        )
        self.assertEqual(
            config["lesson"]["asset_pack_mount_root"],
            "/sdcard/tbot/lesson-assets",
        )
        self.assertEqual(config["lesson"]["step_timeout_floor_sec"], 45.0)
        self.assertEqual(config["lesson"]["max_asset_bytes"], 1048576)
        self.assertEqual(config["lesson"]["max_total_asset_bytes"], 8388608)

    @patch.dict("os.environ", {"TBOT_CLOSE_CONNECTION_NO_VOICE_TIME": "3600"}, clear=True)
    @patch("config.config_loader.ensure_directories")
    @patch(
        "config.config_loader.read_config",
        side_effect=[{"close_connection_no_voice_time": 120}, {}],
    )
    @patch("core.utils.cache.manager.cache_manager.set")
    @patch("core.utils.cache.manager.cache_manager.get", return_value=None)
    def test_connection_idle_timeout_can_be_overridden_by_env(
        self,
        _cache_get,
        _cache_set,
        _read_config,
        _ensure_directories,
    ):
        config = load_config()

        self.assertEqual(config["close_connection_no_voice_time"], 3600)

    def _assert_prod_config_rejected(self, config, env, expected):
        with patch.dict("os.environ", env, clear=True), \
            patch("config.config_loader.ensure_directories"), \
            patch("config.config_loader.read_config", side_effect=[config, {}]), \
            patch("core.utils.cache.manager.cache_manager.set"), \
            patch("core.utils.cache.manager.cache_manager.get", return_value=None), \
            self.assertRaisesRegex(RuntimeError, expected):
            load_config()

    def _safe_prod_env(self):
        return {
            "NODE_ENV": "production",
            "TBOT_REQUIRE_DEVICE_TOKEN": "true",
            "JWT_PUBLIC_KEY": "public-key",
            "TBOT_DEVICE_MINT_SECRET": "mint-secret",
            "LESSON_ASSET_ORIGIN_BASE": "https://cdn.example.com",
        }

    def test_production_posture_rejects_disabled_ws_auth(self):
        for auth_config in ({"enabled": False}, {}):
            with self.subTest(auth_config=auth_config):
                self._assert_prod_config_rejected(
                    {"server": {"auth": auth_config}},
                    self._safe_prod_env(),
                    "server.auth.enabled=true",
                )

    def test_production_posture_rejects_each_missing_or_false_required_env(self):
        cases = [
            ("TBOT_REQUIRE_DEVICE_TOKEN", None, "TBOT_REQUIRE_DEVICE_TOKEN=true"),
            ("TBOT_REQUIRE_DEVICE_TOKEN", "false", "TBOT_REQUIRE_DEVICE_TOKEN=true"),
            ("JWT_PUBLIC_KEY", None, "JWT_PUBLIC_KEY"),
            ("TBOT_DEVICE_MINT_SECRET", None, "TBOT_DEVICE_MINT_SECRET"),
            ("LESSON_ASSET_ORIGIN_BASE", None, "LESSON_ASSET_ORIGIN_BASE"),
        ]
        for name, value, expected in cases:
            env = self._safe_prod_env()
            if value is None:
                del env[name]
            else:
                env[name] = value

            with self.subTest(name=name, value=value):
                self._assert_prod_config_rejected(
                    {"server": {"auth": {"enabled": True}}},
                    env,
                    expected,
                )

    def test_production_posture_rejects_admin_auth_disabled(self):
        env = self._safe_prod_env()
        env["ADMIN_AUTH_DISABLED"] = "true"
        self._assert_prod_config_rejected(
            {"server": {"auth": {"enabled": True}}},
            env,
            "ADMIN_AUTH_DISABLED must not be true",
        )

    def test_production_google_live_rejects_bypassed_aec(self):
        with patch("core.voice.aec.aec_processor.AEC_AVAILABLE", False):
            self._assert_prod_config_rejected(
                {
                    "server": {"auth": {"enabled": True}},
                    "voice_mode": {"type": "google_live"},
                    "google_live": {"aec_enabled": True},
                },
                self._safe_prod_env(),
                "production boot requires active AEC",
            )

    @patch.dict(
        "os.environ",
        {
            "NODE_ENV": "production",
            "TBOT_REQUIRE_DEVICE_TOKEN": "true",
            "JWT_PUBLIC_KEY": "public-key",
            "TBOT_DEVICE_MINT_SECRET": "mint-secret",
            "LESSON_ASSET_ORIGIN_BASE": "https://cdn.example.com",
        },
        clear=True,
    )
    @patch("config.config_loader.ensure_directories")
    @patch(
        "config.config_loader.read_config",
        side_effect=[{"server": {"auth": {"enabled": True}}}, {}],
    )
    @patch("core.utils.cache.manager.cache_manager.set")
    @patch("core.utils.cache.manager.cache_manager.get", return_value=None)
    def test_production_posture_allows_safe_boot(
        self,
        _cache_get,
        _cache_set,
        _read_config,
        _ensure_directories,
    ):
        config = load_config()
        self.assertTrue(config["server"]["auth"]["enabled"])

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
                        "api_url": "https://backend.example.com/v1",
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
        self.assertEqual(
            config["server"]["api_url"],
            "https://backend.example.com/v1",
        )

    @patch.dict(
        "os.environ",
        {
            "TBOT_PUBLIC_WEBSOCKET_URL": "wss://env.example.com/tbot/v1/",
            "TBOT_BACKEND_API_URL": "https://backend-env.example.com/v1/",
        },
    )
    @patch("config.config_loader.init_service")
    @patch("config.config_loader.get_server_config")
    def test_manager_api_config_env_overrides_stale_public_endpoints(
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
                        "websocket": "wss://stale.example.com/tbot/v1/",
                        "api_url": "https://stale-backend.example.com/v1",
                        "vision_explain": "https://public.example.com/mcp/vision/explain",
                        "auth_key": "local-auth",
                    },
                }
            )
        )

        self.assertEqual(
            config["server"]["websocket"],
            "wss://env.example.com/tbot/v1/",
        )
        self.assertEqual(
            config["server"]["api_url"],
            "https://backend-env.example.com/v1",
        )
