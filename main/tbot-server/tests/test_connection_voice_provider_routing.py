import asyncio
import importlib
import json
import sys
import types
import unittest


def _install_connection_import_stubs():
    if "core.connection" in sys.modules:
        return lambda: None

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

        def update_system_message(self, prompt):
            self.system_prompt = prompt

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

    class DummyToolType:
        SYSTEM_CTL = "SYSTEM_CTL"
        CHANGE_SYS_PROMPT = "CHANGE_SYS_PROMPT"
        WAIT = "WAIT"

    def _dummy_register_function(*args, **kwargs):
        def _decorator(func):
            return func

        return _decorator

    class DummyInterfaceType:
        LOCAL = "LOCAL"

    class DummySentenceType:
        FIRST = "FIRST"
        MIDDLE = "MIDDLE"
        LAST = "LAST"

    class DummyContentType:
        TEXT = "TEXT"
        FILE = "FILE"
        ACTION = "ACTION"

    class DummyTTSMessageDTO:
        def __init__(
            self,
            sentence_id,
            sentence_type,
            content_type,
            content_detail=None,
            content_file=None,
        ):
            self.sentence_id = sentence_id
            self.sentence_type = sentence_type
            self.content_type = content_type
            self.content_detail = content_detail
            self.content_file = content_file

    class DummyAuthenticationError(Exception):
        pass

    class DummyAuthManager:
        def __init__(self, *args, **kwargs):
            pass

        def verify_token(self, *args, **kwargs):
            return True

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
    try:
        real_plugin_register = importlib.import_module("plugins_func.register")
    except Exception:
        real_plugin_register = types.ModuleType("plugins_func.register")
        real_plugin_register.Action = DummyAction
        real_plugin_register.ActionResponse = DummyActionResponse
        real_plugin_register.ToolType = DummyToolType
        real_plugin_register.register_function = _dummy_register_function
    try:
        real_util = importlib.import_module("core.utils.util")
        util_is_fallback = False
    except Exception:
        real_util = types.ModuleType("core.utils.util")
        util_is_fallback = True
    try:
        real_auth = importlib.import_module("core.auth")
        auth_is_fallback = False
    except Exception:
        real_auth = types.ModuleType("core.auth")
        auth_is_fallback = True
    try:
        real_tts_dto = importlib.import_module("core.providers.tts.dto.dto")
        tts_dto_is_fallback = False
    except Exception:
        real_tts_dto = types.ModuleType("core.providers.tts.dto.dto")
        tts_dto_is_fallback = True
    try:
        real_text_utils = importlib.import_module("core.utils.textUtils")
    except Exception:
        real_text_utils = types.ModuleType("core.utils.textUtils")
    # Prefer the REAL config.manage_api_client so we never strip the lesson
    # backend legs (get_current_assignment / get_lesson_manifest /
    # post_lesson_event) out of sys.modules. This stub installer runs at import
    # time and is never torn down, so clobbering the real module here leaks a
    # bare stub into every test that runs afterward in the same process (e.g.
    # test_lesson_republish_on_connect patches those symbols by source and would
    # otherwise fail with AttributeError). The connection test only reads three
    # attrs from this module, and the real one already provides all three.
    try:
        real_manage_api_client = importlib.import_module("config.manage_api_client")
    except Exception:
        real_manage_api_client = types.ModuleType("config.manage_api_client")

    modules = {
        "core.utils.util": real_util,
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
        "plugins_func.register": real_plugin_register,
        "core.auth": real_auth,
        "config.config_loader": real_config_loader,
        "core.providers.tts.dto.dto": real_tts_dto,
        "config.logger": types.ModuleType("config.logger"),
        "config.manage_api_client": real_manage_api_client,
        "core.utils.prompt_manager": types.ModuleType("core.utils.prompt_manager"),
        "core.utils.voiceprint_provider": types.ModuleType(
            "core.utils.voiceprint_provider"
        ),
        "core.utils.textUtils": real_text_utils,
    }

    if util_is_fallback:
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
    # Carry over the REAL module's other symbols (e.g. the `_selected_module` helper that
    # tests/test_modules_initialize_tts imports directly) onto this bare stub, so the
    # sys.modules.update below does not strip names later test modules rely on — leaving
    # the three initialize_* lambdas above as the only overrides. Read from the already
    # imported module in sys.modules (do NOT freshly import: modules_initialize pulls the
    # heavy tts/llm/asr providers this stub exists to avoid).
    _real_mi = sys.modules.get("core.utils.modules_initialize")
    _stub_mi = modules["core.utils.modules_initialize"]
    if _real_mi is not None and _real_mi is not _stub_mi:
        for _name in dir(_real_mi):
            if not hasattr(_stub_mi, _name):
                try:
                    setattr(_stub_mi, _name, getattr(_real_mi, _name))
                except Exception:
                    pass

    modules["core.handle.reportHandle"].report = _noop_async
    modules["core.handle.reportHandle"].enqueue_tool_report = lambda *args, **kwargs: None
    modules["core.providers.tts.default"].DefaultTTS = DummyDefaultTTS
    modules["core.utils.dialogue"].Message = DummyMessage
    modules["core.utils.dialogue"].Dialogue = DummyDialogue
    modules["core.providers.asr.dto.dto"].InterfaceType = DummyInterfaceType
    modules["core.handle.textHandle"].handleTextMessage = _fake_handle_text_message
    modules["core.providers.tools.unified_tool_handler"].UnifiedToolHandler = object
    modules["plugins_func.loadplugins"].auto_import_modules = lambda *args, **kwargs: None
    if auth_is_fallback:
        modules["core.auth"].AuthManager = DummyAuthManager
        modules["core.auth"].AuthenticationError = DummyAuthenticationError
    modules["config.config_loader"].get_private_config_from_api = _noop_async
    if tts_dto_is_fallback:
        modules["core.providers.tts.dto.dto"].ContentType = DummyContentType
        modules["core.providers.tts.dto.dto"].TTSMessageDTO = DummyTTSMessageDTO
        modules["core.providers.tts.dto.dto"].SentenceType = DummySentenceType
    modules["config.logger"].setup_logging = lambda: DummyLogger()
    modules["config.logger"].build_module_string = lambda *args, **kwargs: "stub"
    modules["config.logger"].create_connection_logger = lambda *args, **kwargs: DummyLogger()
    # Only fill these in if the module in sys.modules is a bare fallback stub;
    # when the REAL config.manage_api_client is present it already provides them,
    # and overwriting real symbols with dummies would leak into later tests.
    _mac = modules["config.manage_api_client"]
    if not hasattr(_mac, "DeviceNotFoundException"):
        _mac.DeviceNotFoundException = DummyDeviceNotFoundException
    if not hasattr(_mac, "DeviceBindException"):
        _mac.DeviceBindException = DummyDeviceBindException
    if not hasattr(_mac, "generate_and_save_chat_title"):
        _mac.generate_and_save_chat_title = _noop_async
    modules["core.utils.prompt_manager"].PromptManager = DummyPromptManager
    modules["core.utils.voiceprint_provider"].VoiceprintProvider = (
        DummyVoiceprintProvider
    )

    _missing = object()
    previous_modules = {
        name: sys.modules.get(name, _missing)
        for name in [*modules.keys(), "core.connection"]
    }
    sys.modules.update(modules)

    def _restore_import_stubs():
        for name, previous in previous_modules.items():
            if previous is _missing:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous

    return _restore_import_stubs


_restore_connection_import_stubs = _install_connection_import_stubs()

from core.connection import ConnectionHandler
from core.voice.session_provider.classic_pipeline import ClassicPipelineProvider
import core.connection as connection_module

_restore_connection_import_stubs()


def test_connection_header_log_summary_redacts_authorization_tokens():
    headers = {
        "authorization": "Bearer live-token-value",
        "Authorization": "Bearer upper-token-value",
        "x-api-key": "api-key-value",
        "device-id": "robot-1",
        "client-id": "client-1",
        "user-agent": "probe",
    }

    summary = connection_module._sanitize_headers_for_log(headers)

    assert summary["authorization"] == "<redacted>"
    assert summary["Authorization"] == "<redacted>"
    assert summary["x-api-key"] == "<redacted>"
    assert summary["device-id"] == "robot-1"
    assert summary["client-id"] == "client-1"
    assert "live-token-value" not in repr(summary)


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

class _SendingWebSocket:
    def __init__(self):
        self.sent = []

    async def send(self, payload):
        self.sent.append(payload)

class _RecordingLogger:
    def __init__(self):
        self.records = []

    def bind(self, **kwargs):
        return self

    def info(self, message, *args, **kwargs):
        self.records.append(("info", str(message)))

    def error(self, message, *args, **kwargs):
        self.records.append(("error", str(message)))

    def debug(self, message, *args, **kwargs):
        self.records.append(("debug", str(message)))

    def warning(self, message, *args, **kwargs):
        self.records.append(("warning", str(message)))


class _ClosingWebSocket(_FakeWebSocket):
    async def __anext__(self):
        raise connection_module.websockets.exceptions.ConnectionClosed(None, None)


class _RecordingVoiceProvider:
    def __init__(self):
        self.audio_calls = []
        self.text_calls = []

    async def handle_audio_bytes(self, audio_bytes):
        self.audio_calls.append(audio_bytes)
        return True

    async def handle_text_message(self, message):
        self.text_calls.append(message)
        return False

class _LessonRuntimeStub:
    def __init__(self, *, passive=False, completed=False):
        self.state = "RUNNING"
        self._step = {"type": "model"}
        self._step_id = "s4"
        self._step_passive = passive
        self._step_completed = completed


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

    async def test_voice_provider_audio_refreshes_connection_timeout_activity(self):
        handler = self._build_handler()
        handler.session_mode = connection_module.SessionMode.CONVERSATION
        handler.voice_provider = _RecordingVoiceProvider()
        handler.last_activity_time = 1000.0

        original_time = connection_module.time.time
        original_monotonic = connection_module.time.monotonic
        try:
            connection_module.time.time = lambda: 12.5
            connection_module.time.monotonic = lambda: 99.0

            handled = await handler._route_audio_message(b"opus-frame")
        finally:
            connection_module.time.time = original_time
            connection_module.time.monotonic = original_monotonic

        self.assertTrue(handled)
        self.assertEqual(handler.last_activity_time, 12500.0)
        self.assertEqual(handler.last_live_activity_at, 99.0)

    async def test_handle_connection_schedules_lesson_pull_on_boot_without_blocking_voice_route(self):
        handler = self._build_handler()
        handler.config["lesson"] = {
            "runtime_enabled": True,
            "rollout_device_allowlist": ["robot-01"],
        }
        allow_route_return = asyncio.Event()
        route_called = asyncio.Event()
        lesson_pull_started = asyncio.Event()
        allow_lesson_pull_finish = asyncio.Event()

        async def fake_initialize_private_config_async():
            handler.bind_completed_event.set()

        async def fake_route_message(message):
            route_called.set()
            await allow_route_return.wait()

        async def fake_background_initialize():
            return None

        async def fake_check_timeout():
            return None

        async def fake_lesson_pull_on_connect():
            lesson_pull_started.set()
            await allow_lesson_pull_finish.wait()

        async def fake_save_and_close(ws):
            return None

        handler._initialize_private_config_async = fake_initialize_private_config_async
        handler._background_initialize = fake_background_initialize
        handler._route_message = fake_route_message
        handler._check_timeout = fake_check_timeout
        handler._lesson_pull_on_connect = fake_lesson_pull_on_connect
        handler._save_and_close = fake_save_and_close

        websocket = _FakeWebSocket(['{"type":"listen"}'])
        websocket.request.headers["device-id"] = "robot-01"
        handle_task = asyncio.create_task(handler.handle_connection(websocket))

        await asyncio.wait_for(route_called.wait(), timeout=0.5)
        await asyncio.wait_for(lesson_pull_started.wait(), timeout=0.5)
        self.assertIsNotNone(handler.lesson_pull_task)
        self.assertFalse(handler.lesson_pull_task.done())

        allow_lesson_pull_finish.set()
        allow_route_return.set()
        await asyncio.wait_for(handle_task, timeout=0.5)

    async def test_handle_connection_does_not_schedule_lesson_pull_when_runtime_disabled(self):
        handler = self._build_handler()
        handler.config["lesson"] = {"runtime_enabled": False}
        allow_route_return = asyncio.Event()
        route_called = asyncio.Event()
        lesson_pull_started = asyncio.Event()

        async def fake_initialize_private_config_async():
            handler.bind_completed_event.set()

        async def fake_route_message(message):
            route_called.set()
            await allow_route_return.wait()

        async def fake_background_initialize():
            return None

        async def fake_check_timeout():
            return None

        async def fake_lesson_pull_on_connect():
            lesson_pull_started.set()

        async def fake_save_and_close(ws):
            return None

        handler._initialize_private_config_async = fake_initialize_private_config_async
        handler._background_initialize = fake_background_initialize
        handler._route_message = fake_route_message
        handler._check_timeout = fake_check_timeout
        handler._lesson_pull_on_connect = fake_lesson_pull_on_connect
        handler._save_and_close = fake_save_and_close

        handle_task = asyncio.create_task(
            handler.handle_connection(_FakeWebSocket(['{"type":"listen"}']))
        )

        await asyncio.wait_for(route_called.wait(), timeout=0.5)
        await asyncio.sleep(0)
        self.assertIsNone(handler.lesson_pull_task)
        self.assertFalse(lesson_pull_started.is_set())

        allow_route_return.set()
        await asyncio.wait_for(handle_task, timeout=0.5)

    async def test_handle_connection_logs_device_id_when_client_disconnects(self):
        handler = self._build_handler()
        handler.logger = _RecordingLogger()

        async def fake_initialize_private_config_async():
            handler.bind_completed_event.set()

        async def fake_background_initialize():
            return None

        async def fake_check_timeout():
            return None

        async def fake_save_and_close(ws):
            return None

        handler._initialize_private_config_async = fake_initialize_private_config_async
        handler._background_initialize = fake_background_initialize
        handler._check_timeout = fake_check_timeout
        handler._save_and_close = fake_save_and_close

        ws = _ClosingWebSocket([])
        ws.request.headers["device-id"] = "28:84:85:85:1a:80"
        ws.request.headers["client-id"] = "client-1"

        await handler.handle_connection(ws)

        self.assertTrue(
            any(
                level == "info"
                and "Client disconnected" in message
                and "device_id=28:84:85:85:1a:80" in message
                for level, message in handler.logger.records
            ),
            handler.logger.records,
        )

    async def test_route_message_waits_for_bind_then_forwards_to_provider(self):
        handler = self._build_handler()
        handler.voice_provider = _RecordingVoiceProvider()

        route_task = asyncio.create_task(handler._route_message(b"opus-frame"))
        await asyncio.sleep(0.05)

        self.assertEqual(handler.voice_provider.audio_calls, [])

        handler.bind_completed_event.set()
        await asyncio.wait_for(route_task, timeout=0.5)

        self.assertEqual(handler.voice_provider.audio_calls, [b"opus-frame"])

    async def test_google_live_listen_start_bypasses_pending_bind_gate(self):
        handler = self._build_handler()
        handler.config["voice_mode"] = {"type": "google_live"}
        handler.voice_provider = _RecordingVoiceProvider()

        await asyncio.wait_for(
            handler._route_message('{"type":"listen","state":"start","mode":"auto"}'),
            timeout=1.5,
        )

        self.assertEqual(
            handler.voice_provider.text_calls,
            ['{"type":"listen","state":"start","mode":"auto"}'],
        )

    async def test_google_live_audio_bypasses_pending_bind_gate(self):
        handler = self._build_handler()
        handler.config["voice_mode"] = {"type": "google_live"}
        handler.voice_provider = _RecordingVoiceProvider()

        await asyncio.wait_for(handler._route_message(b"opus-frame"), timeout=1.5)

        self.assertEqual(handler.voice_provider.audio_calls, [b"opus-frame"])

    async def test_lesson_interactive_voice_input_routes_to_provider(self):
        handler = self._build_handler()
        handler.bind_completed_event.set()
        handler.voice_provider = _RecordingVoiceProvider()
        handler.session_mode = connection_module.SessionMode.LESSON
        handler.lesson_runtime = _LessonRuntimeStub(passive=False, completed=False)

        await handler._route_message(b"child-opus-frame")

        self.assertEqual(handler.voice_provider.audio_calls, [b"child-opus-frame"])

    async def test_lesson_passive_voice_input_does_not_route_to_provider(self):
        handler = self._build_handler()
        handler.bind_completed_event.set()
        handler.vad = object()
        handler.asr = object()
        handler.voice_provider = _RecordingVoiceProvider()
        handler.session_mode = connection_module.SessionMode.LESSON
        handler.lesson_runtime = _LessonRuntimeStub(passive=True, completed=False)

        await handler._route_message(b"narration-opus-frame")

        self.assertEqual(handler.voice_provider.audio_calls, [])
        self.assertTrue(handler.asr_audio_queue.empty())

    async def test_active_lesson_runtime_restores_lesson_mode_from_dormant_audio(self):
        handler = self._build_handler()
        handler.bind_completed_event.set()
        handler.voice_provider = _RecordingVoiceProvider()
        handler.session_mode = connection_module.SessionMode.DORMANT
        handler.lesson_runtime = _LessonRuntimeStub(passive=False, completed=False)

        await handler._route_message(b"child-opus-frame")

        self.assertEqual(handler.session_mode, connection_module.SessionMode.LESSON)
        self.assertEqual(handler.voice_provider.audio_calls, [b"child-opus-frame"])

    async def test_active_passive_lesson_runtime_does_not_enter_conversation_from_audio(self):
        handler = self._build_handler()
        handler.bind_completed_event.set()
        handler.voice_provider = _RecordingVoiceProvider()
        handler.session_mode = connection_module.SessionMode.DORMANT
        handler.lesson_runtime = _LessonRuntimeStub(passive=True, completed=False)

        await handler._route_message(b"narration-opus-frame")

        self.assertEqual(handler.session_mode, connection_module.SessionMode.LESSON)
        self.assertEqual(handler.voice_provider.audio_calls, [])

    async def test_finish_lesson_mode_avoids_visible_dormant_hop_when_returning_to_conversation(self):
        handler = self._build_handler()
        handler.config.setdefault("lesson", {})["return_to_conversation"] = True
        handler.config["lesson"]["smooth_finish_to_conversation"] = True
        handler.session_mode = connection_module.SessionMode.LESSON
        handler.audio_channel_owner = connection_module.SessionMode.LESSON
        handler.voice_provider = _ClassicLifecycleVoiceProvider()
        transitions = []

        def _record(mode, *, reason=""):
            normalized = connection_module.normalize_session_mode(mode)
            transitions.append((normalized, reason))
            handler.session_mode = normalized
            handler.audio_channel_owner = normalized
            return normalized

        handler._set_session_mode = _record

        await handler.finish_lesson_mode(reason="lesson_completed")

        self.assertNotIn(
            (connection_module.SessionMode.DORMANT, "lesson_completed"),
            transitions,
        )
        self.assertIn(
            (connection_module.SessionMode.CONVERSATION, "lesson_completed"),
            transitions,
        )
        self.assertTrue(handler.voice_provider.started)

    async def test_finish_lesson_mode_uses_sad_face_for_error_terminal(self):
        handler = self._build_handler()
        handler.session_mode = connection_module.SessionMode.LESSON
        handler.audio_channel_owner = connection_module.SessionMode.LESSON
        handler.voice_provider = _ClassicLifecycleVoiceProvider()
        handler.websocket = _SendingWebSocket()

        await handler.finish_lesson_mode(reason="lesson_error")

        self.assertEqual(handler.session_mode, connection_module.SessionMode.CONVERSATION)
        sent = [json.loads(payload) for payload in handler.websocket.sent]
        self.assertEqual(sent[-1]["type"], "llm")
        self.assertEqual(sent[-1]["emotion"], "sad")

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

    async def test_google_live_without_provider_consumes_text_and_audio(self):
        handler = self._build_handler()
        handler.config["voice_mode"] = {"type": "google_live"}
        handler.bind_completed_event.set()
        handler.voice_provider = None
        handler.vad = object()
        handler.asr = object()
        classic_text_calls = []
        original_handle_text = connection_module.handleTextMessage

        async def fake_classic_text(conn, message):
            classic_text_calls.append(message)

        try:
            connection_module.handleTextMessage = fake_classic_text
            await handler._route_message('{"type":"listen","state":"detect","text":"xin chao"}')
            await handler._route_message(b"raw-opus-frame")
        finally:
            connection_module.handleTextMessage = original_handle_text

        self.assertEqual(classic_text_calls, [])
        self.assertTrue(handler.asr_audio_queue.empty())

    async def test_google_live_ping_routes_to_classic_heartbeat_handler(self):
        handler = self._build_handler()
        handler.config["voice_mode"] = {"type": "google_live"}
        handler.config["enable_websocket_ping"] = True
        handler.bind_completed_event.set()
        handler.websocket = _SendingWebSocket()
        handler.last_activity_time = 1000.0
        provider_text_calls = []

        class Provider(_RecordingVoiceProvider):
            async def handle_text_message(self, message):
                provider_text_calls.append(message)
                return False

        handler.voice_provider = Provider()
        from core.handle.textHandle import handleTextMessage as real_handle_text_message

        original_handle_text = connection_module.handleTextMessage
        original_time = connection_module.time.time
        try:
            connection_module.handleTextMessage = real_handle_text_message
            connection_module.time.time = lambda: 12.5

            await handler._route_message('{"type":"ping"}')
        finally:
            connection_module.handleTextMessage = original_handle_text
            connection_module.time.time = original_time

        self.assertEqual(provider_text_calls, [])
        self.assertEqual(handler.last_activity_time, 12500.0)
        sent = [json.loads(payload) for payload in handler.websocket.sent]
        self.assertEqual(sent[-1]["type"], "pong")

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

    async def test_mcp_message_routes_before_manager_bind_ready(self):
        handler = self._build_handler()
        handler.read_config_from_api = True
        handler.config["read_config_from_api"] = True
        raw_message = '{"type":"mcp","payload":{"jsonrpc":"2.0","id":2,"result":{"tools":[]}}}'
        classic_text_calls = []
        original_handle_text = connection_module.handleTextMessage

        async def fake_classic_text(conn, message):
            classic_text_calls.append(message)

        try:
            connection_module.handleTextMessage = fake_classic_text
            await handler._route_message(raw_message)
        finally:
            connection_module.handleTextMessage = original_handle_text

        self.assertEqual(classic_text_calls, [raw_message])

    async def test_bind_prompt_is_skipped_until_tts_ready(self):
        handler = self._build_handler()
        handler.tts = None
        handler.last_bind_prompt_time = 0

        await handler._discard_message_with_bind_prompt()

        self.assertEqual(handler.last_bind_prompt_time, 0)

    async def test_component_init_continues_when_tts_import_fails(self):
        handler = self._build_handler()
        handler.loop = asyncio.get_running_loop()
        handler.config.update(
            {
                "prompt": "system prompt",
                "voiceprint": {},
                "Memory": {},
                "Intent": {"Intent_nointent": {"type": "nointent"}},
                "selected_module": {"Intent": "Intent_nointent"},
            }
        )
        handler._asr = None
        handler.asr = None
        handler.vad = None
        handler._vad = object()
        handler.memory = None
        handler.intent = None
        open_calls = []
        original_initialize_tts = connection_module.initialize_tts
        original_default_tts = connection_module.DefaultTTS
        original_initialize_asr = connection_module.initialize_asr

        class DummyFallbackTTS:
            async def open_audio_channels(self, conn):
                open_calls.append(conn)

        class DummyASR:
            async def open_audio_channels(self, conn):
                open_calls.append(conn)

        try:
            connection_module.initialize_tts = lambda *args, **kwargs: (_ for _ in ()).throw(
                ImportError("edge_tts missing")
            )
            connection_module.DefaultTTS = lambda *args, **kwargs: DummyFallbackTTS()
            connection_module.initialize_asr = lambda *args, **kwargs: DummyASR()

            handler._initialize_components()
            await asyncio.sleep(0.05)
        finally:
            connection_module.initialize_tts = original_initialize_tts
            connection_module.DefaultTTS = original_default_tts
            connection_module.initialize_asr = original_initialize_asr

        self.assertIsNotNone(handler.tts)
        self.assertIsNotNone(handler.asr)
        self.assertIn("system prompt", handler.prompt)
        self.assertEqual(len(open_calls), 2)

    async def test_classic_fallback_initializes_missing_llm_after_google_live_skip(self):
        handler = self._build_handler()
        handler.loop = asyncio.get_running_loop()
        handler.read_config_from_api = True
        handler.config.update(
            {
                "read_config_from_api": True,
                "voice_mode": {"type": "google_live"},
                "selected_module": {
                    "LLM": "LLM_Test",
                    "Memory": "Memory_nomem",
                    "Intent": "Intent_nointent",
                },
                "LLM": {"LLM_Test": {"type": "test"}},
                "Memory": {"Memory_nomem": {"type": "nomem"}},
                "Intent": {"Intent_nointent": {"type": "nointent"}},
            }
        )
        handler.llm = None
        handler.memory = None
        handler.intent = None
        init_calls = []
        original_initialize_modules = connection_module.initialize_modules

        class DummyMemory:
            def __init__(self):
                self.init_calls = []

            def init_memory(self, **kwargs):
                self.init_calls.append(kwargs)

        dummy_memory = DummyMemory()

        async def fake_background_initialize():
            return None

        def fake_initialize_modules(logger, config, *flags):
            init_calls.append(flags)
            return {"llm": "llm", "memory": dummy_memory, "intent": "intent"}

        try:
            connection_module.initialize_modules = fake_initialize_modules
            handler._background_initialize = fake_background_initialize

            await handler._start_classic_pipeline_session()
        finally:
            connection_module.initialize_modules = original_initialize_modules

        self.assertEqual(init_calls, [(False, False, True, False, True, True)])
        self.assertEqual(handler.llm, "llm")
        self.assertIs(handler.memory, dummy_memory)
        self.assertEqual(handler.intent, "intent")
        self.assertEqual(len(dummy_memory.init_calls), 1)

    async def test_manager_bind_event_waits_until_private_google_live_config_applied(self):
        handler = self._build_handler()
        handler.read_config_from_api = True
        handler.config.update(
            {
                "read_config_from_api": True,
                "voice_mode": {"type": "classic_pipeline"},
                "google_live": {"api_key": "${GOOGLE_API_KEY}"},
                "selected_module": {"VAD": "VAD_Base", "ASR": "ASR_Base"},
            }
        )
        handler.headers = {
            "device-id": "device-1",
            "client-id": "client-1",
        }
        handler.common_config = dict(handler.config)
        handler.loop = asyncio.get_running_loop()
        original_get_private_config = connection_module.get_private_config_from_api
        original_check_vad_update = connection_module.check_vad_update
        original_check_asr_update = connection_module.check_asr_update
        original_merge_configs = connection_module.merge_configs
        bind_states_during_google_live_merge = []

        private_config = {
            "voice_mode": {"type": "google_live"},
            "google_live": {"api_key": "bot-key", "model": "live-model"},
            "selected_module": {"VAD": "VAD_Base", "ASR": "ASR_Base"},
        }

        async def fake_get_private_config(*args, **kwargs):
            return private_config

        def merge_configs_without_early_bind(default_config, custom_config):
            if custom_config is private_config["google_live"]:
                bind_states_during_google_live_merge.append(
                    handler.bind_completed_event.is_set()
                )
            return original_merge_configs(default_config, custom_config)

        try:
            connection_module.get_private_config_from_api = fake_get_private_config
            connection_module.check_vad_update = lambda *args, **kwargs: False
            connection_module.check_asr_update = lambda *args, **kwargs: False
            connection_module.merge_configs = merge_configs_without_early_bind

            await handler._initialize_private_config_async()
        finally:
            connection_module.get_private_config_from_api = original_get_private_config
            connection_module.check_vad_update = original_check_vad_update
            connection_module.check_asr_update = original_check_asr_update
            connection_module.merge_configs = original_merge_configs

        self.assertEqual(bind_states_during_google_live_merge, [False])
        self.assertTrue(handler.bind_completed_event.is_set())
        self.assertFalse(handler.need_bind)
        self.assertEqual(
            handler.config["voice_mode"],
            {"type": "google_live", "fallback_to_classic_on_error": False},
        )
        self.assertEqual(handler.config["google_live"]["api_key"], "bot-key")
        self.assertEqual(handler.config["google_live"]["model"], "live-model")

    async def test_private_config_child_profile_is_available_to_prompt_manager(self):
        handler = self._build_handler()
        handler.read_config_from_api = True
        handler.config.update(
            {
                "read_config_from_api": True,
                "voice_mode": {"type": "classic_pipeline"},
                "google_live": {"api_key": "default-key"},
                "selected_module": {"VAD": "VAD_Base", "ASR": "ASR_Base"},
            }
        )
        handler.headers = {
            "device-id": "device-1",
            "client-id": "client-1",
        }
        handler.common_config = dict(handler.config)
        handler.loop = asyncio.get_running_loop()
        original_get_private_config = connection_module.get_private_config_from_api
        original_check_vad_update = connection_module.check_vad_update
        original_check_asr_update = connection_module.check_asr_update

        private_config = {
            "voice_mode": {"type": "google_live"},
            "google_live": {"api_key": "bot-key", "model": "live-model"},
            "selected_module": {"VAD": "VAD_Base", "ASR": "ASR_Base"},
            "child_profile": {
                "device_id": "device-1",
                "device_alias": "Robot phong ngu",
                "child_name": "Bong",
                "child_age": 6,
            },
        }

        async def fake_get_private_config(*args, **kwargs):
            return private_config

        try:
            connection_module.get_private_config_from_api = fake_get_private_config
            connection_module.check_vad_update = lambda *args, **kwargs: False
            connection_module.check_asr_update = lambda *args, **kwargs: False

            await handler._initialize_private_config_async()
        finally:
            connection_module.get_private_config_from_api = original_get_private_config
            connection_module.check_vad_update = original_check_vad_update
            connection_module.check_asr_update = original_check_asr_update

        self.assertEqual(handler.config["child_profile"], private_config["child_profile"])

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

    async def test_private_config_does_not_route_audio_to_classic_while_google_live_starts(self):
        handler = self._build_handler()
        handler.read_config_from_api = True
        handler.config["read_config_from_api"] = True
        handler.config["voice_mode"] = {"type": "classic_pipeline"}
        handler.bind_completed_event.set()

        classic_provider = _ClassicLifecycleVoiceProvider()
        google_live_provider = _DelayedLifecycleVoiceProvider()
        release_private_config = asyncio.Event()
        original_factory = connection_module.create_voice_session_provider

        async def fake_initialize_private_config_async():
            await release_private_config.wait()
            handler.config["voice_mode"] = {"type": "google_live"}

        def fake_provider_factory(conn):
            return google_live_provider

        try:
            connection_module.create_voice_session_provider = fake_provider_factory
            handler.voice_provider = classic_provider
            handler._initialize_private_config_async = fake_initialize_private_config_async

            init_task = asyncio.create_task(handler._initialize_voice_session_async())
            handler.voice_provider_task = init_task

            route_task = asyncio.create_task(handler._route_message(b"early-opus-frame"))
            await asyncio.sleep(0.05)
            self.assertEqual(classic_provider.audio_calls, [])
            self.assertFalse(route_task.done())

            release_private_config.set()
            await asyncio.wait_for(google_live_provider.start_entered.wait(), timeout=0.5)

            route_during_start_task = asyncio.create_task(
                handler._route_message(b"during-start-opus-frame")
            )
            await asyncio.sleep(0.05)
            self.assertEqual(classic_provider.audio_calls, [])
            self.assertFalse(route_during_start_task.done())

            google_live_provider.release_start.set()
            await asyncio.wait_for(init_task, timeout=0.5)
            await asyncio.wait_for(route_task, timeout=0.5)
            await asyncio.wait_for(route_during_start_task, timeout=0.5)
            await handler._route_message(b"ready-opus-frame")
        finally:
            connection_module.create_voice_session_provider = original_factory

        self.assertTrue(classic_provider.closed)
        self.assertIs(handler.voice_provider, google_live_provider)
        self.assertEqual(
            google_live_provider.audio_calls,
            [
                b"early-opus-frame",
                b"during-start-opus-frame",
                b"ready-opus-frame",
            ],
        )

    async def test_private_config_copies_google_live_voice_config(self):
        handler = self._build_handler()
        handler.read_config_from_api = True
        handler.config["read_config_from_api"] = True
        handler.headers = {"device-id": "device-1", "client-id": "client-1"}
        handler.config["voice_mode"] = {"type": "classic_pipeline"}
        handler.config["google_live"] = {}
        handler.config["lesson"] = {
            "runtime_enabled": False,
            "api_base": "https://base.example/v1",
            "asset_delivery_mode": "internet",
        }
        private_config = {
            "voice_mode": {
                "type": "google_live",
                "fallback_to_classic_on_error": False,
            },
            "google_live": {
                "model": "gemini-live",
                "voice_name": "Aoede",
            },
            "lesson": {
                "runtime_enabled": True,
                "asset_origin_base": "https://assets.example",
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
        self.assertEqual(handler.config["google_live"]["voice_name"], "Kore")
        self.assertIs(handler.config["lesson"]["runtime_enabled"], True)
        self.assertEqual(handler.config["lesson"]["api_base"], "https://base.example/v1")
        self.assertEqual(handler.config["lesson"]["asset_delivery_mode"], "internet")
        self.assertEqual(handler.config["lesson"]["asset_origin_base"], "https://assets.example")

    async def test_private_config_ignores_malformed_lesson_config(self):
        handler = self._build_handler()
        handler.read_config_from_api = True
        handler.config["read_config_from_api"] = True
        handler.headers = {"device-id": "device-1", "client-id": "client-1"}
        handler.config["voice_mode"] = {"type": "classic_pipeline"}
        handler.config["lesson"] = {
            "sample_lesson": True,
            "sample_mode": "interactive",
            "api_base": "https://base.example/v1",
        }
        private_config = {
            "voice_mode": {"type": "google_live"},
            "lesson": "bad",
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

        self.assertEqual(
            handler.config["lesson"],
            {
                "sample_lesson": True,
                "sample_mode": "interactive",
                "api_base": "https://base.example/v1",
            },
        )

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
