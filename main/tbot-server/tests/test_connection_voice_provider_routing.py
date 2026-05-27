import asyncio
import importlib
import sys
import types
import unittest


def _install_connection_import_stubs():
    if "core.connection" in sys.modules:
        return

    class DummyLogger:
        def bind(self, **kwargs):
            return self

        def info(self, *args, **kwargs):
            return None

        def error(self, *args, **kwargs):
            return None

        def debug(self, *args, **kwargs):
            return None

        def warning(self, *args, **kwargs):
            return None

    class DummyDialogue:
        def __init__(self):
            self.dialogue = []

        def put(self, message):
            self.dialogue.append(message)

        def get_llm_dialogue(self):
            return list(self.dialogue)

    class DummyMessage:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class DummyPromptManager:
        def __init__(self, config, logger):
            self.config = config
            self.logger = logger

        def get_quick_prompt(self, prompt):
            return prompt

        def update_context_info(self, conn, client_ip):
            return None

        def build_enhanced_prompt(self, prompt, device_id, client_ip):
            return None

    class DummyDefaultTTS:
        def __init__(self, *args, **kwargs):
            pass

        async def open_audio_channels(self, conn):
            return None

    class DummyVoiceprintProvider:
        def __init__(self, config):
            self.enabled = False

    class DummyAction:
        pass

    class DummyActionResponse:
        pass

    class DummyInterfaceType:
        LOCAL = "LOCAL"

    class DummyAuthenticationError(Exception):
        pass

    class DummyDeviceNotFoundException(Exception):
        pass

    class DummyDeviceBindException(Exception):
        def __init__(self, bind_code=None):
            self.bind_code = bind_code
            super().__init__("bind")

    async def _noop_async(*args, **kwargs):
        return None

    async def _fake_handle_text_message(*args, **kwargs):
        return None

    try:
        real_config_loader = importlib.import_module("config.config_loader")
    except Exception:
        real_config_loader = types.ModuleType("config.config_loader")

    modules = {
        "core.utils.util": types.ModuleType("core.utils.util"),
        "core.utils.modules_initialize": types.ModuleType(
            "core.utils.modules_initialize"
        ),
        "core.handle.reportHandle": types.ModuleType("core.handle.reportHandle"),
        "core.providers.tts.default": types.ModuleType("core.providers.tts.default"),
        "core.utils.dialogue": types.ModuleType("core.utils.dialogue"),
        "core.providers.asr.dto.dto": types.ModuleType("core.providers.asr.dto.dto"),
        "core.handle.textHandle": types.ModuleType("core.handle.textHandle"),
        "core.providers.tools.unified_tool_handler": types.ModuleType(
            "core.providers.tools.unified_tool_handler"
        ),
        "plugins_func.loadplugins": types.ModuleType("plugins_func.loadplugins"),
        "plugins_func.register": types.ModuleType("plugins_func.register"),
        "core.auth": types.ModuleType("core.auth"),
        "config.config_loader": real_config_loader,
        "core.providers.tts.dto.dto": types.ModuleType("core.providers.tts.dto.dto"),
        "config.logger": types.ModuleType("config.logger"),
        "config.manage_api_client": types.ModuleType("config.manage_api_client"),
        "core.utils.prompt_manager": types.ModuleType("core.utils.prompt_manager"),
        "core.utils.voiceprint_provider": types.ModuleType(
            "core.utils.voiceprint_provider"
        ),
        "core.utils.textUtils": types.ModuleType("core.utils.textUtils"),
    }

    modules["core.utils.util"].extract_json_from_string = lambda value: value
    modules["core.utils.util"].check_vad_update = lambda *args, **kwargs: False
    modules["core.utils.util"].check_asr_update = lambda *args, **kwargs: False
    modules["core.utils.util"].filter_sensitive_info = lambda value: value
    modules["core.utils.util"].get_system_error_response = lambda *args, **kwargs: {}

    modules["core.utils.modules_initialize"].initialize_modules = (
        lambda *args, **kwargs: {}
    )
    modules["core.utils.modules_initialize"].initialize_tts = (
        lambda *args, **kwargs: DummyDefaultTTS()
    )
    modules["core.utils.modules_initialize"].initialize_asr = (
        lambda *args, **kwargs: object()
    )

    modules["core.handle.reportHandle"].report = _noop_async
    modules["core.handle.reportHandle"].enqueue_tool_report = lambda *args, **kwargs: None
    modules["core.providers.tts.default"].DefaultTTS = DummyDefaultTTS
    modules["core.utils.dialogue"].Message = DummyMessage
    modules["core.utils.dialogue"].Dialogue = DummyDialogue
    modules["core.providers.asr.dto.dto"].InterfaceType = DummyInterfaceType
    modules["core.handle.textHandle"].handleTextMessage = _fake_handle_text_message
    modules["core.providers.tools.unified_tool_handler"].UnifiedToolHandler = object
    modules["plugins_func.loadplugins"].auto_import_modules = lambda *args, **kwargs: None
    modules["plugins_func.register"].Action = DummyAction
    modules["plugins_func.register"].ActionResponse = DummyActionResponse
    modules["core.auth"].AuthenticationError = DummyAuthenticationError
    modules["config.config_loader"].get_private_config_from_api = _noop_async
    modules["core.providers.tts.dto.dto"].ContentType = object
    modules["core.providers.tts.dto.dto"].TTSMessageDTO = object
    modules["core.providers.tts.dto.dto"].SentenceType = object
    modules["config.logger"].setup_logging = lambda: DummyLogger()
    modules["config.logger"].build_module_string = lambda *args, **kwargs: "stub"
    modules["config.logger"].create_connection_logger = lambda *args, **kwargs: DummyLogger()
    modules["config.manage_api_client"].DeviceNotFoundException = (
        DummyDeviceNotFoundException
    )
    modules["config.manage_api_client"].DeviceBindException = DummyDeviceBindException
    modules["config.manage_api_client"].generate_and_save_chat_title = _noop_async
    modules["core.utils.prompt_manager"].PromptManager = DummyPromptManager
    modules["core.utils.voiceprint_provider"].VoiceprintProvider = (
        DummyVoiceprintProvider
    )

    sys.modules.update(modules)


_install_connection_import_stubs()

from core.connection import ConnectionHandler
from core.voice.session_provider.classic_pipeline import ClassicPipelineProvider
import core.connection as connection_module


class _FakeRequest:
    def __init__(self):
        self.headers = {}
        self.path = "/ws"


class _FakeWebSocket:
    def __init__(self, messages):
        self.request = _FakeRequest()
        self.remote_address = ("127.0.0.1", 12345)
        self._messages = list(messages)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._messages:
            return self._messages.pop(0)
        raise StopAsyncIteration


class _RecordingVoiceProvider:
    def __init__(self):
        self.audio_calls = []

    async def handle_audio_bytes(self, audio_bytes):
        self.audio_calls.append(audio_bytes)
        return True

    async def handle_text_message(self, message):
        return False


class _ClassicLifecycleVoiceProvider:
    def __init__(self):
        self.started = False
        self.closed = False
        self.audio_calls = []

    async def start_session(self):
        self.started = True

    async def close(self):
        self.closed = True

    async def handle_audio_bytes(self, audio_bytes):
        self.audio_calls.append(audio_bytes)
        return True

    async def handle_text_message(self, message):
        return False

    async def interrupt(self):
        return None


class _GoogleLiveLifecycleVoiceProvider(_ClassicLifecycleVoiceProvider):
    pass

class _DelayedLifecycleVoiceProvider(_ClassicLifecycleVoiceProvider):
    def __init__(self):
        super().__init__()
        self.start_entered = asyncio.Event()
        self.release_start = asyncio.Event()

    async def start_session(self):
        self.start_entered.set()
        await self.release_start.wait()
        await super().start_session()


class ConnectionVoiceProviderRoutingTest(unittest.IsolatedAsyncioTestCase):
    def _build_handler(self):
        return ConnectionHandler(
            config={
                "read_config_from_api": False,
                "tbot": {"audio_params": {"sample_rate": 24000}},
                "exit_commands": [],
                "selected_module": {},
            },
            _vad=None,
            _asr=None,
            _llm=None,
            _memory=None,
            _intent=None,
        )

    async def test_handle_connection_starts_classic_provider_before_private_config_finishes(
        self,
    ):
        handler = self._build_handler()
        release_init = asyncio.Event()
        allow_route_return = asyncio.Event()
        route_called = asyncio.Event()
        background_started = asyncio.Event()

        async def fake_initialize_private_config_async():
            await release_init.wait()
            handler.bind_completed_event.set()

        async def fake_route_message(message):
            route_called.set()
            await allow_route_return.wait()

        async def fake_background_initialize():
            background_started.set()

        async def fake_check_timeout():
            return None

        async def fake_save_and_close(ws):
            return None

        handler._initialize_private_config_async = fake_initialize_private_config_async
        handler._background_initialize = fake_background_initialize
        handler._route_message = fake_route_message
        handler._check_timeout = fake_check_timeout
        handler._save_and_close = fake_save_and_close

        handle_task = asyncio.create_task(
            handler.handle_connection(_FakeWebSocket(['{"type":"listen"}']))
        )

        await asyncio.wait_for(route_called.wait(), timeout=0.5)
        await asyncio.wait_for(background_started.wait(), timeout=0.5)

        self.assertIsInstance(handler.voice_provider, ClassicPipelineProvider)
        self.assertIsNotNone(handler.voice_provider_task)
        self.assertFalse(handler.voice_provider_task.done())

        release_init.set()
        allow_route_return.set()

        await asyncio.wait_for(handle_task, timeout=0.5)

    async def test_manager_mode_waits_for_private_config_before_starting_provider(self):
        handler = self._build_handler()
        handler.read_config_from_api = True
        handler.config["read_config_from_api"] = True
        release_init = asyncio.Event()
        allow_route_return = asyncio.Event()
        route_called = asyncio.Event()
        background_started = asyncio.Event()

        async def fake_initialize_private_config_async():
            await release_init.wait()
            handler.bind_completed_event.set()

        async def fake_route_message(message):
            route_called.set()
            await allow_route_return.wait()

        async def fake_background_initialize():
            background_started.set()

        async def fake_check_timeout():
            return None

        async def fake_save_and_close(ws):
            return None

        handler._initialize_private_config_async = fake_initialize_private_config_async
        handler._background_initialize = fake_background_initialize
        handler._route_message = fake_route_message
        handler._check_timeout = fake_check_timeout
        handler._save_and_close = fake_save_and_close

        handle_task = asyncio.create_task(
            handler.handle_connection(_FakeWebSocket(['{"type":"listen"}']))
        )
        await asyncio.wait_for(route_called.wait(), timeout=0.5)
        await asyncio.sleep(0.05)

        self.assertIsNone(handler.voice_provider)
        self.assertFalse(background_started.is_set())
        self.assertIsNotNone(handler.voice_provider_task)
        self.assertFalse(handler.voice_provider_task.done())

        release_init.set()
        allow_route_return.set()

        await asyncio.wait_for(handle_task, timeout=0.5)

    async def test_route_message_waits_for_bind_then_forwards_to_provider(self):
        handler = self._build_handler()
        handler.voice_provider = _RecordingVoiceProvider()

        route_task = asyncio.create_task(handler._route_message(b"opus-frame"))
        await asyncio.sleep(0.05)

        self.assertEqual(handler.voice_provider.audio_calls, [])

        handler.bind_completed_event.set()
        await asyncio.wait_for(route_task, timeout=0.5)

        self.assertEqual(handler.voice_provider.audio_calls, [b"opus-frame"])

    async def test_route_message_waits_for_manager_voice_provider_before_classic_text_path(self):
        handler = self._build_handler()
        handler.read_config_from_api = True
        handler.config["read_config_from_api"] = True
        handler.bind_completed_event.set()
        provider = _RecordingVoiceProvider()
        provider_text_calls = []
        classic_text_calls = []
        release_provider = asyncio.Event()
        original_handle_text = connection_module.handleTextMessage

        async def handle_text_message(message):
            provider_text_calls.append(message)
            return True

        async def fake_classic_text(conn, message):
            classic_text_calls.append(message)

        async def initialize_provider():
            await release_provider.wait()
            provider.handle_text_message = handle_text_message
            handler.voice_provider = provider

        try:
            connection_module.handleTextMessage = fake_classic_text
            handler.voice_provider_task = asyncio.create_task(initialize_provider())
            route_task = asyncio.create_task(
                handler._route_message('{"type":"listen","state":"detect","text":"xin chao"}')
            )
            await asyncio.sleep(0.05)

            self.assertEqual(provider_text_calls, [])
            self.assertEqual(classic_text_calls, [])

            release_provider.set()
            await asyncio.wait_for(route_task, timeout=0.5)
        finally:
            connection_module.handleTextMessage = original_handle_text

        self.assertEqual(provider_text_calls, ['{"type":"listen","state":"detect","text":"xin chao"}'])
        self.assertEqual(classic_text_calls, [])

    async def test_hello_message_routes_before_manager_bind_ready(self):
        handler = self._build_handler()
        handler.read_config_from_api = True
        handler.config["read_config_from_api"] = True
        classic_text_calls = []
        original_handle_text = connection_module.handleTextMessage

        async def fake_classic_text(conn, message):
            classic_text_calls.append(message)

        try:
            connection_module.handleTextMessage = fake_classic_text
            await handler._route_message('{"type":"hello","version":1}')
        finally:
            connection_module.handleTextMessage = original_handle_text

        self.assertEqual(classic_text_calls, ['{"type":"hello","version":1}'])

    async def test_bind_prompt_is_skipped_until_tts_ready(self):
        handler = self._build_handler()
        handler.tts = None
        handler.last_bind_prompt_time = 0

        await handler._discard_message_with_bind_prompt()

        self.assertEqual(handler.last_bind_prompt_time, 0)

    async def test_private_config_can_swap_classic_bootstrap_to_google_live_provider(self):
        handler = self._build_handler()
        handler.config["voice_mode"] = {"type": "classic_pipeline"}
        handler.bind_completed_event.set()

        classic_provider = _ClassicLifecycleVoiceProvider()
        google_live_provider = _GoogleLiveLifecycleVoiceProvider()
        original_factory = connection_module.create_voice_session_provider

        async def fake_initialize_private_config_async():
            handler.config["voice_mode"] = {"type": "google_live"}

        def fake_provider_factory(conn):
            return google_live_provider

        try:
            connection_module.create_voice_session_provider = fake_provider_factory
            handler.voice_provider = classic_provider
            handler._initialize_private_config_async = fake_initialize_private_config_async

            await handler._initialize_voice_session_async()
            await handler._route_message(b"opus-frame")
        finally:
            connection_module.create_voice_session_provider = original_factory

        self.assertTrue(classic_provider.closed)
        self.assertIs(handler.voice_provider, google_live_provider)
        self.assertTrue(google_live_provider.started)
        self.assertEqual(google_live_provider.audio_calls, [b"opus-frame"])

    async def test_private_config_keeps_classic_provider_until_google_live_started(self):
        handler = self._build_handler()
        handler.config["voice_mode"] = {"type": "classic_pipeline"}
        handler.bind_completed_event.set()

        classic_provider = _ClassicLifecycleVoiceProvider()
        google_live_provider = _DelayedLifecycleVoiceProvider()
        original_factory = connection_module.create_voice_session_provider

        async def fake_initialize_private_config_async():
            handler.config["voice_mode"] = {"type": "google_live"}

        def fake_provider_factory(conn):
            return google_live_provider

        try:
            connection_module.create_voice_session_provider = fake_provider_factory
            handler.voice_provider = classic_provider
            handler._initialize_private_config_async = fake_initialize_private_config_async

            init_task = asyncio.create_task(handler._initialize_voice_session_async())
            await asyncio.wait_for(google_live_provider.start_entered.wait(), timeout=0.5)

            await handler._route_message(b"early-opus-frame")
            self.assertIs(handler.voice_provider, classic_provider)
            self.assertEqual(classic_provider.audio_calls, [b"early-opus-frame"])
            self.assertFalse(classic_provider.closed)

            google_live_provider.release_start.set()
            await asyncio.wait_for(init_task, timeout=0.5)
            await handler._route_message(b"ready-opus-frame")
        finally:
            connection_module.create_voice_session_provider = original_factory

        self.assertTrue(classic_provider.closed)
        self.assertIs(handler.voice_provider, google_live_provider)
        self.assertEqual(google_live_provider.audio_calls, [b"ready-opus-frame"])

    async def test_private_config_copies_google_live_voice_config(self):
        handler = self._build_handler()
        handler.read_config_from_api = True
        handler.config["read_config_from_api"] = True
        handler.headers = {"device-id": "device-1", "client-id": "client-1"}
        handler.config["voice_mode"] = {"type": "classic_pipeline"}
        handler.config["google_live"] = {}
        private_config = {
            "voice_mode": {
                "type": "google_live",
                "fallback_to_classic_on_error": False,
            },
            "google_live": {
                "model": "gemini-live",
                "voice_name": "Aoede",
            },
            "selected_module": {},
        }
        original_private_config = connection_module.get_private_config_from_api
        original_initialize_modules = connection_module.initialize_modules

        async def fake_private_config(*args, **kwargs):
            return private_config

        try:
            connection_module.get_private_config_from_api = fake_private_config
            connection_module.initialize_modules = lambda *args, **kwargs: {}

            await handler._initialize_private_config_async()
        finally:
            connection_module.get_private_config_from_api = original_private_config
            connection_module.initialize_modules = original_initialize_modules

        self.assertEqual(handler.config["voice_mode"]["type"], "google_live")
        self.assertFalse(handler.config["voice_mode"]["fallback_to_classic_on_error"])
        self.assertEqual(handler.config["google_live"]["model"], "gemini-live")
        self.assertEqual(handler.config["google_live"]["voice_name"], "Aoede")

    async def test_private_config_init_stops_before_component_init_when_connection_closes(self):
        handler = self._build_handler()
        handler.read_config_from_api = True
        handler.config["read_config_from_api"] = True
        handler.headers = {"device-id": "device-1", "client-id": "client-1"}
        handler.loop = asyncio.get_running_loop()
        private_config = {
            "voice_mode": {"type": "google_live"},
            "selected_module": {},
        }
        initialize_calls = []
        original_private_config = connection_module.get_private_config_from_api
        original_initialize_modules = connection_module.initialize_modules

        async def fake_private_config(*args, **kwargs):
            handler.stop_event.set()
            return private_config

        def fake_initialize_modules(*args, **kwargs):
            initialize_calls.append((args, kwargs))
            return {}

        try:
            connection_module.get_private_config_from_api = fake_private_config
            connection_module.initialize_modules = fake_initialize_modules

            await handler._initialize_private_config_async()
        finally:
            connection_module.get_private_config_from_api = original_private_config
            connection_module.initialize_modules = original_initialize_modules

        self.assertEqual(initialize_calls, [])

    async def test_google_live_private_config_skips_eager_classic_component_init(self):
        handler = self._build_handler()
        handler.read_config_from_api = True
        handler.config["read_config_from_api"] = True
        handler.headers = {"device-id": "device-1", "client-id": "client-1"}
        handler.loop = asyncio.get_running_loop()
        private_config = {
            "voice_mode": {"type": "google_live"},
            "google_live": {"model": "gemini-live"},
            "selected_module": {},
        }
        initialize_calls = []
        original_private_config = connection_module.get_private_config_from_api
        original_initialize_modules = connection_module.initialize_modules

        async def fake_private_config(*args, **kwargs):
            return private_config

        def fake_initialize_modules(*args, **kwargs):
            initialize_calls.append((args, kwargs))
            return {}

        try:
            connection_module.get_private_config_from_api = fake_private_config
            connection_module.initialize_modules = fake_initialize_modules

            await handler._initialize_private_config_async()
        finally:
            connection_module.get_private_config_from_api = original_private_config
            connection_module.initialize_modules = original_initialize_modules

        self.assertEqual(handler.config["voice_mode"]["type"], "google_live")
        self.assertEqual(handler.config["google_live"]["model"], "gemini-live")
        self.assertEqual(initialize_calls, [])
