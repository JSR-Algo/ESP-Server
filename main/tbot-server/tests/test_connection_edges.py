import asyncio
import importlib.util
import json
import queue
import threading
import types
import unittest
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import patch

_ROUTING_TESTS_PATH = Path(__file__).with_name("test_connection_voice_provider_routing.py")
_ROUTING_SPEC = importlib.util.spec_from_file_location(
    "test_connection_voice_provider_routing", _ROUTING_TESTS_PATH
)
routing_tests = importlib.util.module_from_spec(_ROUTING_SPEC)
_ROUTING_SPEC.loader.exec_module(routing_tests)

ConnectionHandler = routing_tests.ConnectionHandler
connection_module = routing_tests.connection_module


class _Logger:
    def __init__(self):
        self.messages = []

    def bind(self, **kwargs):
        return self

    def info(self, *args, **kwargs):
        self.messages.append(("info", args, kwargs))

    def debug(self, *args, **kwargs):
        self.messages.append(("debug", args, kwargs))

    def warning(self, *args, **kwargs):
        self.messages.append(("warning", args, kwargs))

    def error(self, *args, **kwargs):
        self.messages.append(("error", args, kwargs))


class _TTS:
    def __init__(self):
        self.tts_text_queue = queue.Queue()
        self.tts_audio_queue = queue.Queue()
        self.stored = []
        self.one_sentence = []
        self.closed = False
        self.opened = False

    async def open_audio_channels(self, conn):
        self.opened = True

    async def close(self):
        self.closed = True

    def store_tts_text(self, sentence_id, text):
        self.stored.append((sentence_id, text))

    def tts_one_sentence(self, conn, content_type, content_detail=None):
        self.one_sentence.append((content_type, content_detail))


class _AsyncClose:
    def __init__(self, *, fail=False):
        self.closed = False
        self.fail = fail

    async def close(self):
        self.closed = True
        if self.fail:
            raise RuntimeError("close failed")


class _AsyncAClose:
    def __init__(self, *, fail=False):
        self.closed = False
        self.fail = fail

    async def aclose(self):
        self.closed = True
        if self.fail:
            raise RuntimeError("aclose failed")


class _WebSocket:
    def __init__(self, *, closed=False, state_name="OPEN", fail=False):
        self.closed = closed
        self.state = types.SimpleNamespace(name=state_name)
        self.close_calls = 0
        self.fail = fail

    async def close(self):
        self.close_calls += 1
        if self.fail:
            raise RuntimeError("ws close failed")


class _SendWebSocket(_WebSocket):
    def __init__(self, *, fail_send=False):
        super().__init__()
        self.sent = []
        self.fail_send = fail_send

    async def send(self, payload):
        if self.fail_send:
            raise RuntimeError("send failed")
        self.sent.append(json.loads(payload))


class _Function:
    def __init__(self, name="", arguments=""):
        self.name = name
        self.arguments = arguments


class _ToolDelta:
    def __init__(self, *, index=None, id=None, name="", arguments=""):
        self.index = index
        self.id = id
        self.function = _Function(name, arguments)


class _Dialogue:
    def __init__(self):
        self.dialogue = []
        self.system_prompt = None

    def put(self, message):
        self.dialogue.append(message)

    def update_system_message(self, prompt):
        self.system_prompt = prompt

    def get_llm_dialogue(self):
        return list(self.dialogue)

    def get_llm_dialogue_with_memory(self, memory, voiceprint):
        return ["dialogue", memory, voiceprint]


class _LLM:
    def __init__(self, responses=None, *, fail=False):
        self.responses = responses or []
        self.fail = fail
        self.calls = []

    def response(self, session_id, dialogue):
        self.calls.append(("response", session_id, dialogue))
        if self.fail:
            raise RuntimeError("llm failed")
        return iter(self.responses)

    def response_with_functions(self, session_id, dialogue, functions=None):
        self.calls.append(("functions", session_id, dialogue, functions))
        if self.fail:
            raise RuntimeError("llm failed")
        return iter(self.responses)


class _FuncHandler:
    def __init__(self, result=None, *, fail=False):
        self.result = result
        self.fail = fail
        self.cleaned = False

    def get_functions(self):
        return [
            {"type": "function", "function": {"name": "tool_a"}},
            {"type": "function", "function": {"name": "handle_exit_intent"}},
        ]

    async def _initialize(self):
        return None

    async def cleanup(self):
        self.cleaned = True
        if self.fail:
            raise RuntimeError("cleanup failed")

    async def handle_llm_function_call(self, conn, tool_call_data):
        if self.fail:
            raise RuntimeError("tool failed")
        return self.result


class _Memory:
    def __init__(self):
        self.init_calls = []
        self.set_llm_calls = []
        self.saved = []

    def init_memory(self, **kwargs):
        self.init_calls.append(kwargs)

    def set_llm(self, llm):
        self.set_llm_calls.append(llm)

    async def save_memory(self, dialogue, session_id):
        self.saved.append((dialogue, session_id))

    async def query_memory(self, query):
        return f"memory:{query}"


def _future_result(value=None, exc=None):
    future = Future()
    if exc is None:
        future.set_result(value)
    else:
        future.set_exception(exc)
    return future


def _build_handler():
    handler = ConnectionHandler(
        config={
            "read_config_from_api": False,
            "tbot": {"audio_params": {"sample_rate": 24000}},
            "exit_commands": [],
            "selected_module": {},
            "prompt": "base prompt",
            "Memory": {"Memory_nomem": {"type": "nomem"}},
            "Intent": {"Intent_nointent": {"type": "nointent"}},
            "LLM": {},
            "tool_call_timeout": 1,
        },
        _vad=None,
        _asr=None,
        _llm=None,
        _memory=None,
        _intent=None,
    )
    handler.logger = _Logger()
    handler.dialogue = _Dialogue()
    handler.tts = _TTS()
    return handler


def _action_response(action, *, result=None, response=None):
    return connection_module.ActionResponse(
        action=action,
        result=result,
        response=response,
    )


class ConnectionEdgeTest(unittest.IsolatedAsyncioTestCase):
    async def test_mqtt_audio_extracts_length_header_and_falls_back_to_tail(self):
        handler = _build_handler()
        first = b"12345678" + (10).to_bytes(4, "big") + (3).to_bytes(4, "big") + b"abczzz"
        self.assertTrue(await handler._process_mqtt_audio_message(first))
        self.assertEqual(handler.asr_audio_queue.get_nowait(), b"abc")

        fallback = b"12345678" + (11).to_bytes(4, "big") + (99).to_bytes(4, "big") + b"tail"
        self.assertTrue(await handler._process_mqtt_audio_message(fallback))
        self.assertEqual(handler.asr_audio_queue.get_nowait(), b"tail")

        self.assertFalse(await handler._process_mqtt_audio_message(b"short"))

    def test_websocket_audio_orders_monotonic_buffers_and_overflow(self):
        handler = _build_handler()
        handler._process_websocket_audio(b"new", 10)
        handler._process_websocket_audio(b"old", 5)
        self.assertEqual(handler.asr_audio_queue.get_nowait(), b"new")
        self.assertEqual(handler.audio_timestamp_buffer, {5: b"old"})

        handler._process_websocket_audio(b"later", 11)
        self.assertEqual(handler.asr_audio_queue.get_nowait(), b"later")
        self.assertEqual(handler.audio_timestamp_buffer, {5: b"old"})

        handler.audio_timestamp_buffer = {i: bytes([i]) for i in range(20)}
        handler._process_websocket_audio(b"overflow", 1)
        self.assertEqual(handler.asr_audio_queue.get_nowait(), b"overflow")

        handler = _build_handler()
        handler.audio_timestamp_buffer = {12: b"buffered"}
        handler.last_processed_timestamp = 10
        handler.max_timestamp_buffer_size = 20
        handler._process_websocket_audio(b"eleven", 11)
        self.assertEqual(handler.asr_audio_queue.get_nowait(), b"eleven")
        self.assertEqual(handler.asr_audio_queue.get_nowait(), b"buffered")

    async def test_session_mode_and_live_idle_paths_persist_and_suspend(self):
        handler = _build_handler()
        saved = []
        handler.device_id = "dev-1"
        handler.google_live_session_resumption_handle = "resume-token"
        handler.live_resumption_store = types.SimpleNamespace(save=lambda dev, token: _future_result())

        async def save(dev, token):
            saved.append((dev, token))

        handler.live_resumption_store.save = save
        provider = _AsyncClose()
        provider._client = None
        handler.voice_provider = provider

        self.assertTrue(await handler.enter_conversation_mode(reason="test"))
        self.assertEqual(handler.session_mode, connection_module.SessionMode.CONVERSATION)
        self.assertIsNotNone(handler.last_live_activity_at)

        handler.voice_provider = types.SimpleNamespace(_close_live_resources=lambda: save("closed", "live"))
        await handler.enter_lesson_mode(reason="lesson")
        self.assertEqual(handler.session_mode, connection_module.SessionMode.LESSON)
        self.assertEqual(saved, [("dev-1", "resume-token")])
        self.assertFalse(await handler.enter_conversation_mode(reason="blocked"))

        await handler.release_lesson_mode(reason="done")
        self.assertEqual(handler.session_mode, connection_module.SessionMode.DORMANT)

        handler.session_mode = connection_module.SessionMode.CONVERSATION
        handler.voice_provider = _AsyncClose()
        handler.last_live_activity_at = 1.0
        handler.config["google_live"] = {"idle_timeout_sec": 5}
        self.assertFalse(await handler.close_live_if_idle(now=5.5))
        self.assertTrue(await handler.close_live_if_idle(now=7.0))
        self.assertEqual(handler.session_mode, connection_module.SessionMode.DORMANT)

        handler.session_mode = connection_module.SessionMode.DORMANT
        self.assertFalse(await handler.close_live_if_idle(now=100))
        handler.session_mode = connection_module.SessionMode.CONVERSATION
        handler.last_live_activity_at = None
        self.assertFalse(await handler.close_live_if_idle(now=100))
        handler.google_live_session_started_at = 95
        self.assertFalse(await handler.close_live_if_idle(now=99))

    async def test_wait_for_voice_provider_ready_covers_cancel_and_done_paths(self):
        handler = _build_handler()
        handler.read_config_from_api = False
        await handler._wait_for_voice_provider_ready()

        handler.read_config_from_api = True
        handler.voice_provider = object()
        await handler._wait_for_voice_provider_ready()

        handler.voice_provider = None
        handler.voice_provider_task = None
        await handler._wait_for_voice_provider_ready()

        done = asyncio.create_task(asyncio.sleep(0))
        await done
        handler.voice_provider_task = done
        await handler._wait_for_voice_provider_ready()

        async def cancelled():
            raise asyncio.CancelledError()

        cancelled_task = asyncio.create_task(cancelled())
        with self.assertRaises(asyncio.CancelledError):
            await cancelled_task
        handler.voice_provider_task = cancelled_task
        await handler._wait_for_voice_provider_ready()

        async def wait_from_same_task():
            handler.voice_provider_task = asyncio.current_task()
            await handler._wait_for_voice_provider_ready()

        await wait_from_same_task()

    async def test_route_message_timeout_bind_and_fallback_paths(self):
        handler = _build_handler()
        discarded = []
        handler._discard_message_with_bind_prompt = lambda: asyncio.sleep(0, result=discarded.append("discard"))
        await handler._route_message(b"early")
        self.assertEqual(discarded, ["discard"])

        handler.bind_completed_event.set()
        handler.need_bind = True
        await handler._route_message("text")
        self.assertEqual(discarded, ["discard", "discard"])

        handler.need_bind = False
        classic = []
        provider = types.SimpleNamespace(handle_text_message=lambda message: asyncio.sleep(0, result=False))
        handler.voice_provider = provider
        original_handle_text = connection_module.handleTextMessage
        try:
            connection_module.handleTextMessage = lambda conn, message: asyncio.sleep(0, result=classic.append(message))
            await handler._route_message("plain")
        finally:
            connection_module.handleTextMessage = original_handle_text
        self.assertEqual(classic, ["plain"])

        handler.voice_provider = None
        handler.vad = None
        handler.asr = None
        await handler._route_message(b"audio")
        self.assertTrue(handler.asr_audio_queue.empty())

        handler.vad = object()
        handler.asr = object()
        handler.conn_from_mqtt_gateway = True
        handler._process_mqtt_audio_message = lambda message: asyncio.sleep(0, result=False)
        await handler._route_message(b"1234567890123456raw")
        self.assertEqual(handler.asr_audio_queue.get_nowait(), b"1234567890123456raw")

    async def test_audio_routing_and_bind_prompt_edges(self):
        handler = _build_handler()
        self.assertFalse(handler._lesson_runtime_accepts_voice_input())
        handler.session_mode = connection_module.SessionMode.LESSON
        handler.lesson_runtime = routing_tests._LessonRuntimeStub(passive=False, completed=False)
        handler.voice_provider = types.SimpleNamespace(handle_audio_bytes=lambda audio: asyncio.sleep(0, result=False))
        self.assertTrue(await handler._route_audio_message(b"lesson"))
        handler.session_mode = connection_module.SessionMode.CONVERSATION
        handler.voice_provider = None
        self.assertTrue(await handler._route_audio_message(b"no-provider"))

        class RaisingLogger(_Logger):
            def bind(self, **kwargs):
                raise RuntimeError("log failed")

        handler.logger = RaisingLogger()
        self.assertEqual(
            handler._set_session_mode(connection_module.SessionMode.DORMANT, reason="log"),
            connection_module.SessionMode.DORMANT,
        )

        handler.tts = _TTS()
        handler.last_bind_prompt_time = 0
        handler.bind_prompt_interval = 1
        prompted = []
        import core.handle.receiveAudioHandle as receive_audio

        original_check = receive_audio.check_bind_device
        original_time = connection_module.time.time
        original_create_task = connection_module.asyncio.create_task
        try:
            receive_audio.check_bind_device = lambda conn: asyncio.sleep(0, result=prompted.append(conn))
            connection_module.time.time = lambda: 10
            connection_module.asyncio.create_task = lambda coro: (coro.close(), prompted.append("scheduled"))[1]
            await handler._discard_message_with_bind_prompt()
        finally:
            receive_audio.check_bind_device = original_check
            connection_module.time.time = original_time
            connection_module.asyncio.create_task = original_create_task
        self.assertEqual(prompted, [handler, "scheduled"])

    async def test_restart_success_and_error_paths_do_not_execute_restart(self):
        handler = _build_handler()
        ws = _SendWebSocket()
        handler.websocket = ws
        started = []

        class FakeThread:
            def __init__(self, target, daemon=False):
                self.target = target
                self.daemon = daemon

            def start(self):
                started.append((self.target, self.daemon))

        original_thread = connection_module.threading.Thread
        try:
            connection_module.threading.Thread = FakeThread
            await handler.handle_restart({})
        finally:
            connection_module.threading.Thread = original_thread
        self.assertEqual(ws.sent[0]["content"], {"action": "restart"})
        self.assertEqual(len(started), 1)

        class FailThenSucceedWebSocket(_SendWebSocket):
            async def send(self, payload):
                if not self.sent:
                    self.sent.append({"failed": True})
                    raise RuntimeError("send failed")
                self.sent.append(json.loads(payload))

        handler.websocket = FailThenSucceedWebSocket()
        await handler.handle_restart({})
        self.assertTrue(any(level == "error" for level, *_ in handler.logger.messages))

    def test_component_initializers_cover_local_and_failure_branches(self):
        handler = _build_handler()
        bind_tts = _TTS()
        handler.need_bind = True
        original_default = connection_module.DefaultTTS
        try:
            connection_module.DefaultTTS = lambda *args, **kwargs: bind_tts
            self.assertIs(handler._initialize_tts(), bind_tts)
        finally:
            connection_module.DefaultTTS = original_default

        local_asr = types.SimpleNamespace(interface_type=connection_module.InterfaceType.LOCAL)
        handler._asr = local_asr
        self.assertIs(handler._initialize_asr(), local_asr)

        handler.config["voiceprint"] = {"enabled": True}
        original_voiceprint = connection_module.VoiceprintProvider
        try:
            connection_module.VoiceprintProvider = lambda config: types.SimpleNamespace(enabled=True)
            handler._initialize_voiceprint()
            self.assertTrue(handler.voiceprint_provider.enabled)
            connection_module.VoiceprintProvider = lambda config: types.SimpleNamespace(enabled=False)
            handler._initialize_voiceprint()
            connection_module.VoiceprintProvider = lambda config: (_ for _ in ()).throw(RuntimeError("voiceprint"))
            handler._initialize_voiceprint()
        finally:
            connection_module.VoiceprintProvider = original_voiceprint

        handler.need_bind = True
        handler.loop = asyncio.get_event_loop()
        handler.tts = _TTS()
        handler.bind_completed_event.clear()
        handler._initialize_components()
        self.assertTrue(handler.bind_completed_event.is_set())

        handler.prompt_manager = types.SimpleNamespace(
            update_context_info=lambda conn, ip: None,
            build_enhanced_prompt=lambda prompt, device_id, client_ip, emoji_enabled=True: "enhanced",
        )
        handler.config["prompt"] = "base"
        handler._init_prompt_enhancement()
        self.assertEqual(handler.prompt, "enhanced")

    async def test_provider_init_background_and_private_config_error_edges(self):
        handler = _build_handler()
        handler.loop = asyncio.get_running_loop()
        handler.read_config_from_api = False
        await handler._initialize_private_config_async()
        self.assertTrue(handler.bind_completed_event.is_set())

        handler = _build_handler()
        handler.read_config_from_api = True
        handler.config["read_config_from_api"] = True
        handler.headers = {"device-id": "dev-1"}
        handler.common_config = dict(handler.config)
        handler.loop = asyncio.get_running_loop()
        private_config = {"selected_module": {}, "voice_mode": {"type": "classic_pipeline"}}
        original_get = connection_module.get_private_config_from_api
        original_init = connection_module.initialize_modules
        try:
            connection_module.get_private_config_from_api = lambda *args, **kwargs: asyncio.sleep(0, result=private_config)
            connection_module.initialize_modules = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("init"))
            await handler._initialize_private_config_async()
        finally:
            connection_module.get_private_config_from_api = original_get
            connection_module.initialize_modules = original_init
        self.assertTrue(handler.bind_completed_event.is_set())

        handler = _build_handler()
        handler._initialize_private_config_async = lambda: asyncio.sleep(0, result=None)
        original_factory = connection_module.create_voice_session_provider
        try:
            connection_module.create_voice_session_provider = lambda conn: (_ for _ in ()).throw(RuntimeError("factory"))
            await handler._initialize_voice_session_async()
        finally:
            connection_module.create_voice_session_provider = original_factory
        self.assertTrue(handler.bind_completed_event.is_set())

        handler = _build_handler()
        handler.executor = types.SimpleNamespace(submit=lambda func: (_ for _ in ()).throw(RuntimeError("submit")))
        await handler._background_initialize()

        handler = _build_handler()
        handler.loop = asyncio.get_running_loop()
        handler.config["selected_module"] = {"LLM": "LLM_Test"}
        handler.llm = None
        original_init = connection_module.initialize_modules
        try:
            connection_module.initialize_modules = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("classic"))
            await handler._ensure_classic_pipeline_modules()
        finally:
            connection_module.initialize_modules = original_init

    async def test_close_websocket_state_variants_and_queue_helpers(self):
        handler = _build_handler()
        handler.tts = None
        handler.clear_queues()

        handler = _build_handler()
        handler.websocket = _WebSocket(closed=True, state_name="OPEN")
        await handler.close()
        self.assertEqual(handler.websocket.close_calls, 1)

        handler = _build_handler()
        ws = _WebSocket(closed=False, state_name="CLOSED")
        await handler.close(ws)
        self.assertEqual(ws.close_calls, 1)

        handler = _build_handler()
        handler.executor = types.SimpleNamespace(shutdown=lambda wait=False: (_ for _ in ()).throw(RuntimeError("shutdown")))
        await handler.close(_WebSocket())

        handler = _build_handler()
        ws = types.SimpleNamespace(state=types.SimpleNamespace(name="OPEN"), close_calls=0)
        async def close_ws():
            ws.close_calls += 1
        ws.close = close_ws
        await handler.close(ws)
        self.assertEqual(ws.close_calls, 1)

        handler = _build_handler()
        self_ws = types.SimpleNamespace(state=types.SimpleNamespace(name="CLOSED"), close_calls=0)
        async def close_self_ws():
            self_ws.close_calls += 1
        self_ws.close = close_self_ws
        handler.websocket = self_ws
        await handler.close()
        self.assertEqual(self_ws.close_calls, 1)

    def test_reset_speak_chat_close_and_static_helper_edges(self):
        handler = _build_handler()
        handler.client_audio_buffer.extend(b"abc")
        handler.client_have_voice = True
        handler.client_voice_stop = True
        handler.client_voice_window.extend([1, 2])
        handler.last_is_voice = True
        handler.vad_last_voice_time = 1.2
        handler.asr_audio.extend([b"a"])
        handler.reset_audio_states()
        self.assertFalse(handler.client_have_voice)
        self.assertEqual(handler.client_audio_buffer, bytearray())
        self.assertEqual(handler.asr_audio, [])

        handler.client_is_speaking = True
        handler.clearSpeakStatus()
        self.assertFalse(handler.client_is_speaking)

        calls = []
        handler.chat = lambda text: calls.append(text)
        handler.chat_and_close("bye")
        self.assertTrue(handler.close_after_chat)
        self.assertEqual(calls, ["bye"])
        handler.chat = lambda text: (_ for _ in ()).throw(RuntimeError("chat"))
        handler.chat_and_close("boom")

        self.assertEqual(ConnectionHandler._extract_direct_answer_response(""), "")
        self.assertEqual(ConnectionHandler._extract_direct_answer_response('{"response": "x"}'), "x")
        self.assertEqual(ConnectionHandler._extract_direct_answer_response('prefix "response":"x"}'), "x")
        self.assertEqual(ConnectionHandler._clean_response_garbage(None), None)
        self.assertFalse(handler._is_hello_message("not-json"))

    async def test_small_remaining_branch_edges(self):
        handler = _build_handler()
        handler.google_live_session_resumption_handle = "token"
        handler.live_resumption_store = object()
        await handler._persist_live_resumption_handle()

        class BadPacket:
            def __getitem__(self, item):
                raise RuntimeError("bad packet")

            def __len__(self):
                return 20

        self.assertFalse(await handler._process_mqtt_audio_message(BadPacket()))

        handler.intent_type = "nointent"
        handler._inject_tool_call_fewshot()
        handler.intent_type = "function_call"
        handler.func_handler = None
        handler._inject_tool_call_fewshot()
        handler.func_handler = types.SimpleNamespace(get_functions=lambda: [])
        handler._inject_tool_call_fewshot()

        handler.tts = _TTS()
        self.assertTrue(await handler.ensure_lesson_tts())

        handler.intent = types.SimpleNamespace(set_llm=lambda llm: setattr(handler, "intent_llm", llm))
        handler.llm = "main-llm"
        handler.loop = asyncio.get_event_loop()
        handler.config.update(
            {
                "selected_module": {"Intent": "Intent_llm"},
                "Intent": {"Intent_llm": {"type": "intent_llm", "llm": "missing"}},
                "LLM": {},
            }
        )
        original_tool_handler = connection_module.UnifiedToolHandler
        original_threadsafe = connection_module.asyncio.run_coroutine_threadsafe
        try:
            connection_module.UnifiedToolHandler = lambda conn: _FuncHandler()
            connection_module.asyncio.run_coroutine_threadsafe = lambda coro, loop: (coro.close(), _future_result())[1]
            handler._initialize_intent()
        finally:
            connection_module.UnifiedToolHandler = original_tool_handler
            connection_module.asyncio.run_coroutine_threadsafe = original_threadsafe
        self.assertEqual(handler.intent_llm, "main-llm")

        handler._handle_function_result(
            [
                (
                    types.SimpleNamespace(action=object(), result=None, response=None),
                    {"id": "u", "name": "unknown", "arguments": "{}"},
                )
            ],
            depth=0,
        )

        handler.config = None
        handler._disable_lesson_runtime()
        handler.config = {}
        handler.logger = types.SimpleNamespace(bind=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("log")))
        handler._disable_lesson_runtime()

        handler.lesson_voice_alarm = types.SimpleNamespace(record_round_trip=lambda value: (_ for _ in ()).throw(RuntimeError("alarm")))
        handler.note_voice_round_trip(1.0)

        handler.logger = types.SimpleNamespace(bind=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("metric")))
        handler.record_voice_metric("latency", 1.0)

        handler.voice_provider = types.SimpleNamespace(_interaction=types.SimpleNamespace(state=None))
        self.assertIsNone(handler._realtime_interaction_state())

        self.assertEqual(ConnectionHandler._extract_direct_answer_response('{"response":"x"}'), "x")

    def test_chat_depth_memory_abort_emoji_and_text_tool_call_paths(self):
        handler = _build_handler()
        handler.loop = asyncio.get_event_loop()
        handler.memory = _Memory()
        handler.intent_type = "function_call"
        handler.func_handler = _FuncHandler()
        handler.features = {"emoji": True}
        handler.sentence_id = "existing-sentence"
        handler.llm = _LLM(["hello"])
        original_threadsafe = connection_module.asyncio.run_coroutine_threadsafe
        try:
            def run_threadsafe(coro, loop):
                coro.close()
                return _future_result("memory:xin chao")

            connection_module.asyncio.run_coroutine_threadsafe = run_threadsafe
            self.assertTrue(handler.chat("xin chao", depth=5))
        finally:
            connection_module.asyncio.run_coroutine_threadsafe = original_threadsafe
        self.assertEqual(handler.sentence_id, "existing-sentence")

        aborting = _build_handler()
        aborting.loop = asyncio.get_event_loop()
        aborting.client_abort = True
        aborting.llm = _LLM(["ignored"])
        aborting.chat("abort")

        text_tool = _build_handler()
        text_tool.loop = asyncio.get_event_loop()
        text_tool.intent_type = "function_call"
        text_tool.func_handler = _FuncHandler(
            result=_action_response(connection_module.Action.RESPONSE, result="tool ok", response="tool ok")
        )
        text_tool.features = {"emoji": False}
        text_tool.llm = _LLM([('<tool_call>{"name":"tool_a","arguments":{"x":1}}', None)])
        reports = []
        original_extract = connection_module.extract_json_from_string
        original_threadsafe = connection_module.asyncio.run_coroutine_threadsafe
        original_enqueue = connection_module.enqueue_tool_report
        try:
            connection_module.extract_json_from_string = lambda value: '{"name":"tool_a","arguments":{"x":1}}'
            connection_module.enqueue_tool_report = lambda *args, **kwargs: reports.append((args, kwargs))
            connection_module.asyncio.run_coroutine_threadsafe = lambda coro, loop: (coro.close(), _future_result(text_tool.func_handler.result))[1]
            text_tool.chat("tool")
        finally:
            connection_module.extract_json_from_string = original_extract
            connection_module.asyncio.run_coroutine_threadsafe = original_threadsafe
            connection_module.enqueue_tool_report = original_enqueue
        self.assertEqual(text_tool.tts.one_sentence[-1][1], "tool ok")

        bad_tool = _build_handler()
        bad_tool.loop = asyncio.get_event_loop()
        bad_tool.intent_type = "function_call"
        bad_tool.func_handler = _FuncHandler()
        bad_tool.features = {"emoji": False}
        bad_tool.llm = _LLM([("<tool_call>not-json", None)])
        original_extract = connection_module.extract_json_from_string
        try:
            connection_module.extract_json_from_string = lambda value: None
            bad_tool.chat("bad")
        finally:
            connection_module.extract_json_from_string = original_extract

        invalid_tool = _build_handler()
        invalid_tool.loop = asyncio.get_event_loop()
        invalid_tool.intent_type = "function_call"
        invalid_tool.func_handler = _FuncHandler()
        invalid_tool.features = {"emoji": False}
        invalid_tool.llm = _LLM([("<tool_call>{bad", None)])
        original_extract = connection_module.extract_json_from_string
        try:
            connection_module.extract_json_from_string = lambda value: "{bad"
            invalid_tool.chat("invalid")
        finally:
            connection_module.extract_json_from_string = original_extract

    async def test_timeout_sleep_error_and_report_worker_error_paths(self):
        handler = _build_handler()
        handler.last_activity_time = 0
        sleeps = []
        original_sleep = connection_module.asyncio.sleep
        try:
            async def stop_sleep(delay):
                sleeps.append(delay)
                handler.stop_event.set()

            connection_module.asyncio.sleep = stop_sleep
            await handler._check_timeout()
        finally:
            connection_module.asyncio.sleep = original_sleep
        self.assertEqual(sleeps, [10])

        handler = _build_handler()
        original_sleep = connection_module.asyncio.sleep
        try:
            async def raise_sleep(delay):
                raise RuntimeError("sleep")

            connection_module.asyncio.sleep = raise_sleep
            await handler._check_timeout()
        finally:
            connection_module.asyncio.sleep = original_sleep

        handler = _build_handler()
        handler.executor = None
        handler.report_queue.put(("tts", "text", b"audio", 1))
        handler.report_queue.put(None)
        handler._report_worker()

        handler = _build_handler()
        handler.executor = types.SimpleNamespace(submit=lambda func, *item: (_ for _ in ()).throw(RuntimeError("submit")))
        handler.report_queue.put(("tts", "text", b"audio", 1))
        handler.report_queue.put(None)
        handler._report_worker()

        original_run = connection_module.asyncio.run
        try:
            connection_module.asyncio.run = lambda coro: (coro.close(), (_ for _ in ()).throw(RuntimeError("report")))[1]
            handler.report_queue.put("pending")
            handler._process_report("tts", "text", b"audio", 1)
        finally:
            connection_module.asyncio.run = original_run

    async def test_lesson_tts_initializes_once_and_returns_false_on_failure(self):
        handler = _build_handler()
        handler.tts = None
        handler.loop = asyncio.get_running_loop()
        created = _TTS()
        handler._initialize_tts = lambda: created

        self.assertTrue(await handler.ensure_lesson_tts())
        self.assertIs(handler.tts, created)
        self.assertTrue(created.opened)
        self.assertTrue(await handler.ensure_lesson_tts())

        failing = _build_handler()
        failing.tts = None
        failing.loop = asyncio.get_running_loop()
        failing._initialize_tts = lambda: (_ for _ in ()).throw(RuntimeError("tts"))
        self.assertFalse(await failing.ensure_lesson_tts())

    async def test_private_config_classic_path_merges_every_module_and_initializes(self):
        handler = _build_handler()
        handler.read_config_from_api = True
        handler.config["read_config_from_api"] = True
        handler.headers = {"device-id": "dev-1", "client-id": "client-1"}
        handler.common_config = dict(handler.config)
        handler.loop = asyncio.get_running_loop()
        handler.config.update(
            {
                "selected_module": {
                    "VAD": "VAD_Default",
                    "ASR": "ASR_Default",
                    "TTS": "TTS_Default",
                    "LLM": "LLM_Default",
                    "Memory": "Memory_nomem",
                    "Intent": "Intent_nointent",
                },
                "TTS": {"TTS_Default": {"type": "default"}},
                "Intent": {"Intent_nointent": {"type": "nointent"}},
                "google_live": {"model": "default-live"},
            }
        )
        private_config = {
            "selected_module": {
                "VAD": "VAD_Private",
                "ASR": "ASR_Private",
                "TTS": "TTS_Private",
                "LLM": "LLM_Private",
                "VLLM": "VLLM_Private",
                "Memory": "Memory_Private",
                "Intent": "Intent_tool",
            },
            "VAD": {"VAD_Private": {"type": "vad"}},
            "ASR": {"ASR_Private": {"type": "asr"}},
            "TTS": {"TTS_Private": {"type": "tts"}},
            "LLM": {"LLM_Private": {"type": "llm"}},
            "VLLM": {"VLLM_Private": {"type": "vllm"}},
            "Memory": {"Memory_Private": {"type": "mem_report_only"}},
            "Intent": {"Intent_tool": {"type": "function_call"}},
            "plugins": {"weather": '{"city": "hanoi"}'},
            "prompt": "private prompt",
            "child_profile": {"name": "Bong"},
            "voiceprint": {"enabled": True},
            "summaryMemory": {"enabled": True},
            "device_max_output_size": "42",
            "chat_history_conf": "1",
            "mcp_endpoint": "http://mcp",
            "context_providers": ["profile"],
            "voice_mode": {"type": "classic_pipeline"},
            "google_live": {"voice_name": "Aoede"},
            "correct_words": {"TBOT": "tee bot"},
        }
        init_calls = []
        modules = {
            "tts": _TTS(),
            "vad": "vad",
            "asr": "asr",
            "llm": "llm",
            "intent": "intent",
            "memory": _Memory(),
        }
        original_get_private_config = connection_module.get_private_config_from_api
        original_vad = connection_module.check_vad_update
        original_asr = connection_module.check_asr_update
        original_init = connection_module.initialize_modules
        try:
            connection_module.get_private_config_from_api = lambda *args, **kwargs: asyncio.sleep(0, result=private_config)
            connection_module.check_vad_update = lambda *args, **kwargs: True
            connection_module.check_asr_update = lambda *args, **kwargs: True

            def initialize_modules(*args):
                init_calls.append(args[-6:])
                return modules

            connection_module.initialize_modules = initialize_modules
            await handler._initialize_private_config_async()
        finally:
            connection_module.get_private_config_from_api = original_get_private_config
            connection_module.check_vad_update = original_vad
            connection_module.check_asr_update = original_asr
            connection_module.initialize_modules = original_init

        self.assertTrue(handler.bind_completed_event.is_set())
        self.assertFalse(handler.need_bind)
        self.assertEqual(init_calls, [(True, True, True, True, True, True)])
        self.assertEqual(handler.config["selected_module"]["VAD"], "VAD_Private")
        self.assertEqual(handler.config["Intent"]["Intent_tool"]["functions"], ["weather"])
        self.assertEqual(handler.config["plugins"], {"weather": {"city": "hanoi"}})
        self.assertEqual(handler.config["TTS"]["TTS_Private"]["correct_words"], {"TBOT": "tee bot"})
        self.assertEqual(handler.config["google_live"]["voice_name"], "Aoede")
        self.assertEqual(handler.max_output_size, 42)
        self.assertEqual(handler.chat_history_conf, 1)
        self.assertIs(handler.memory, modules["memory"])

    async def test_private_config_bind_error_paths_set_bind_state(self):
        async def run_with_error(error):
            handler = _build_handler()
            handler.read_config_from_api = True
            handler.config["read_config_from_api"] = True
            handler.headers = {"device-id": "dev-1"}
            handler.common_config = dict(handler.config)
            handler.loop = asyncio.get_running_loop()
            original_get_private_config = connection_module.get_private_config_from_api
            original_init = connection_module.initialize_modules
            try:
                async def raise_error(*args, **kwargs):
                    raise error

                connection_module.get_private_config_from_api = raise_error
                connection_module.initialize_modules = lambda *args, **kwargs: {}
                await handler._initialize_private_config_async()
            finally:
                connection_module.get_private_config_from_api = original_get_private_config
                connection_module.initialize_modules = original_init
            return handler

        not_found = await run_with_error(connection_module.DeviceNotFoundException())
        self.assertTrue(not_found.need_bind)
        self.assertTrue(not_found.bind_completed_event.is_set())

        bind_error = await run_with_error(connection_module.DeviceBindException("123456"))
        self.assertTrue(bind_error.need_bind)
        self.assertEqual(bind_error.bind_code, "123456")

        generic = await run_with_error(RuntimeError("api down"))
        self.assertTrue(generic.need_bind)

    async def test_private_config_device_not_found_can_fallback_to_base_config(self):
        handler = _build_handler()
        handler.read_config_from_api = True
        handler.config["read_config_from_api"] = True
        handler.config["allow_device_config_fallback"] = True
        handler.config["voice_mode"] = {"type": "google_live"}
        handler.headers = {"device-id": "28:84:85:85:1a:80", "client-id": "client-1"}
        handler.common_config = dict(handler.config)
        handler.loop = asyncio.get_running_loop()

        original_get_private_config = connection_module.get_private_config_from_api
        original_init = connection_module.initialize_modules
        try:
            async def raise_not_found(*args, **kwargs):
                raise connection_module.DeviceNotFoundException("Device not found")

            connection_module.get_private_config_from_api = raise_not_found
            connection_module.initialize_modules = lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("google_live fallback should not initialize classic modules")
            )

            await handler._initialize_private_config_async()
        finally:
            connection_module.get_private_config_from_api = original_get_private_config
            connection_module.initialize_modules = original_init

        self.assertFalse(handler.need_bind)
        self.assertIsNone(handler.bind_code)
        self.assertTrue(handler.bind_completed_event.is_set())
        self.assertTrue(
            any(
                message[0] == "warning"
                and "device config not found; falling back to base config" in str(message[1][0])
                for message in handler.logger.messages
            )
        )

    async def test_save_and_close_runs_title_and_memory_threads_then_closes(self):
        handler = _build_handler()
        handler.session_id = "session-1"
        handler.memory = _Memory()
        handler.dialogue.dialogue = ["hello"]
        close_calls = []
        title_calls = []

        async def fake_title(session_id):
            title_calls.append(session_id)

        async def fake_close(ws):
            close_calls.append(ws)

        real_thread = threading.Thread

        class ImmediateThread:
            def __init__(self, target, daemon=False):
                self.target = target
                self.daemon = daemon

            def start(self):
                thread = real_thread(target=self.target, daemon=self.daemon)
                thread.start()
                thread.join(timeout=1)

        original_thread = connection_module.threading.Thread
        original_title = connection_module.generate_and_save_chat_title
        try:
            connection_module.threading.Thread = ImmediateThread
            connection_module.generate_and_save_chat_title = fake_title
            handler.close = fake_close
            await handler._save_and_close("ws")
        finally:
            connection_module.threading.Thread = original_thread
            connection_module.generate_and_save_chat_title = original_title

        self.assertEqual(title_calls, ["session-1"])
        self.assertEqual(handler.memory.saved, [(["hello"], "session-1")])
        self.assertEqual(close_calls, ["ws"])

    def test_prompt_fewshots_report_threads_memory_and_intent_initializers(self):
        handler = _build_handler()
        handler.intent_type = "function_call"
        handler.func_handler = _FuncHandler()
        handler._inject_tool_call_fewshot()
        self.assertGreaterEqual(len(handler.dialogue.dialogue), 7)

        handler.read_config_from_api = True
        handler.need_bind = False
        handler.chat_history_conf = 1
        handler.report_thread = None
        started = []

        class FakeThread:
            def __init__(self, target, daemon=False):
                self.target = target
                self.daemon = daemon

            def is_alive(self):
                return False

            def start(self):
                started.append(self.target)

        original_thread = connection_module.threading.Thread
        try:
            connection_module.threading.Thread = FakeThread
            handler._init_report_threads()
        finally:
            connection_module.threading.Thread = original_thread
        self.assertEqual(started, [handler._report_worker])

        memory = _Memory()
        handler.memory = memory
        handler.device_id = "dev-1"
        handler.llm = "main-llm"
        handler.read_config_from_api = False
        handler.config.update(
            {
                "selected_module": {"Memory": "Memory_short"},
                "Memory": {"Memory_short": {"type": "mem_local_short", "llm": "LLM_Memory"}},
                "LLM": {"LLM_Memory": {"type": "mock_llm"}},
            }
        )
        import core.utils.llm as llm_utils

        original_create = llm_utils.create_instance
        try:
            llm_utils.create_instance = lambda llm_type, config: (llm_type, config)
            handler._initialize_memory()
        finally:
            llm_utils.create_instance = original_create
        self.assertEqual(memory.set_llm_calls[-1][0], "mock_llm")

        handler.config["Memory"] = {"Memory_short": {"type": "mem_local_short", "llm": "missing"}}
        handler._initialize_memory()
        self.assertEqual(memory.set_llm_calls[-1], "main-llm")

        intent = types.SimpleNamespace(set_llm=lambda llm: setattr(handler, "intent_llm", llm))
        handler.intent = intent
        handler.loop = asyncio.get_event_loop()
        handler.config.update(
            {
                "selected_module": {"Intent": "Intent_llm"},
                "Intent": {"Intent_llm": {"type": "intent_llm", "llm": "LLM_Intent"}},
                "LLM": {"LLM_Intent": {"type": "intent_llm_type"}},
            }
        )
        original_create = llm_utils.create_instance
        original_tool_handler = connection_module.UnifiedToolHandler
        original_threadsafe = connection_module.asyncio.run_coroutine_threadsafe
        try:
            llm_utils.create_instance = lambda llm_type, config: ("intent", llm_type)
            connection_module.UnifiedToolHandler = lambda conn: _FuncHandler()
            connection_module.asyncio.run_coroutine_threadsafe = lambda coro, loop: (coro.close(), _future_result())[1]
            handler._initialize_intent()
        finally:
            llm_utils.create_instance = original_create
            connection_module.UnifiedToolHandler = original_tool_handler
            connection_module.asyncio.run_coroutine_threadsafe = original_threadsafe
        self.assertEqual(handler.intent_llm, ("intent", "intent_llm_type"))
        self.assertTrue(handler.load_function_plugin)

    async def test_close_cleans_resources_and_tolerates_cleanup_failures(self):
        handler = _build_handler()
        handler.vad = types.SimpleNamespace(release_conn_resources=lambda conn: setattr(conn, "vad_released", True))
        handler.audio_buffer = bytearray(b"audio")
        handler.timeout_task = asyncio.create_task(asyncio.sleep(60))
        handler.func_handler = _FuncHandler(fail=True)
        handler.voice_provider = _AsyncClose(fail=True)
        handler.voice_provider_task = asyncio.create_task(asyncio.sleep(60))
        handler.lesson_pull_task = asyncio.create_task(asyncio.sleep(60))
        handler.lesson_runtime = _AsyncClose(fail=True)
        handler.safety_event_forwarder = _AsyncAClose(fail=True)
        handler.audio_rate_controller = types.SimpleNamespace(
            reset=lambda: setattr(handler, "rate_reset", True),
            stop_sending_and_wait=lambda: _future_result(),
        )
        async def stop_sending_and_wait():
            handler.rate_stopped = True
        handler.audio_rate_controller.stop_sending_and_wait = stop_sending_and_wait
        handler.tts.tts_text_queue.put("text")
        handler.tts.tts_audio_queue.put("audio")
        handler.report_queue.put("report")
        handler.asr = _AsyncClose()
        ws = _WebSocket(fail=True)

        await handler.close(ws)

        self.assertTrue(handler.stop_event.is_set())
        self.assertTrue(handler.vad_released)
        self.assertEqual(handler.audio_buffer, bytearray())
        self.assertIsNone(handler.timeout_task)
        self.assertTrue(handler.func_handler.cleaned)
        self.assertTrue(handler.lesson_runtime.closed)
        self.assertTrue(handler.safety_event_forwarder.closed)
        self.assertTrue(handler.rate_reset)
        self.assertTrue(handler.rate_stopped)
        self.assertTrue(handler.tts.closed)
        self.assertTrue(handler.asr.closed)
        self.assertIsNone(handler.executor)

    async def test_check_timeout_uses_bind_first_activity_and_logs_close_errors(self):
        handler = _build_handler()
        handler.need_bind = True
        handler.first_activity_time = 1
        handler.last_activity_time = 999999999999
        handler.timeout_seconds = 1
        close_calls = []

        async def close(ws):
            close_calls.append(ws)
            raise RuntimeError("close failed")

        handler.close = close
        handler.websocket = _WebSocket()
        original_time = connection_module.time.time
        try:
            connection_module.time.time = lambda: 10
            await handler._check_timeout()
        finally:
            connection_module.time.time = original_time

        self.assertEqual(close_calls, [handler.websocket])
        self.assertTrue(handler.stop_event.is_set())

    def test_direct_answer_cleaning_and_tool_call_merge(self):
        self.assertEqual(
            ConnectionHandler._extract_direct_answer_response('{"response": "hello"}'),
            "hello",
        )
        self.assertEqual(
            ConnectionHandler._extract_direct_answer_response('{"response":"hello\\nworld"'),
            "hello\nworld",
        )
        self.assertEqual(ConnectionHandler._extract_direct_answer_response('{"x": 1}'), "")
        self.assertEqual(ConnectionHandler._clean_response_garbage('hello\n"}}\n'), "hello")
        self.assertEqual(ConnectionHandler._clean_response_garbage(""), "")

        handler = _build_handler()
        calls = []
        handler._merge_tool_calls(calls, [_ToolDelta(index=0, id="a", name="tool", arguments="{")])
        handler._merge_tool_calls(calls, [_ToolDelta(index=0, arguments='"x":1}')])
        handler._merge_tool_calls(calls, [_ToolDelta(name="next", arguments="{}")])
        handler._merge_tool_calls(calls, [_ToolDelta(arguments="tail")])
        self.assertEqual(calls[0], {"id": "a", "name": "tool", "arguments": '{"x":1}'})
        self.assertEqual(calls[1], {"id": "", "name": "next", "arguments": "{}tail"})

    def test_function_result_records_all_action_shapes(self):
        handler = _build_handler()
        handler.sentence_id = "sid"
        handler.chat_calls = []
        handler.chat = lambda query, depth=0: handler.chat_calls.append((query, depth))
        tool_results = [
            (
                _action_response(connection_module.Action.RESPONSE, result="same", response="same"),
                {"id": "r1", "name": "reply", "arguments": "{}"},
            ),
            (
                _action_response(connection_module.Action.RECORD, result="recorded", response="shown"),
                {"id": None, "name": "record", "arguments": ""},
            ),
            (
                _action_response(connection_module.Action.REQLLM, result="needs llm"),
                {"id": None, "name": "lookup", "arguments": "{}"},
            ),
        ]

        handler._handle_function_result(tool_results, depth=2, streamed_text="same")

        self.assertEqual(handler.tts.one_sentence, [])
        self.assertIn((None, 3), handler.chat_calls)
        roles = [getattr(message, "role", None) for message in handler.dialogue.dialogue]
        self.assertIn("assistant", roles)
        self.assertIn("tool", roles)

    def test_report_worker_processes_items_and_poison_pill(self):
        handler = _build_handler()
        processed = []
        handler._process_report = lambda *item: processed.append(item)
        handler.report_queue.put(("asr", "text", b"audio", 1.0))
        handler.report_queue.put(None)

        handler._report_worker()

        self.assertEqual(processed, [("asr", "text", b"audio", 1.0)])

    def test_process_report_runs_report_and_marks_done_on_error(self):
        handler = _build_handler()
        calls = []
        original_report = connection_module.report

        async def fake_report(*args):
            calls.append(args)

        try:
            connection_module.report = fake_report
            handler.report_queue.put("pending")
            handler._process_report("tts", "hello", b"audio", 3.0)
        finally:
            connection_module.report = original_report

        self.assertEqual(calls, [(handler, "tts", "hello", b"audio", 3.0)])
        self.assertEqual(handler.report_queue.unfinished_tasks, 0)

    def test_runtime_flags_metrics_and_busy_state(self):
        handler = _build_handler()
        self.assertFalse(handler._lesson_runtime_enabled())
        handler.config["lesson"] = {"runtime_enabled": True}
        self.assertTrue(handler._lesson_runtime_enabled())
        handler._disable_lesson_runtime()
        self.assertFalse(handler.config["lesson"]["runtime_enabled"])

        alarm = types.SimpleNamespace(samples=[])
        alarm.record_round_trip = lambda value: alarm.samples.append(value)
        handler.lesson_voice_alarm = alarm
        handler.note_voice_round_trip(123.4)
        self.assertEqual(alarm.samples, [123.4])
        handler.lesson_voice_alarm = types.SimpleNamespace(record_round_trip=lambda value: (_ for _ in ()).throw(RuntimeError("boom")))
        handler.note_voice_round_trip(5.0)

        handler.record_voice_metric("latency", "12.5", {"phase": "tts"})
        self.assertEqual(handler.voice_metric_samples[-1]["value"], 12.5)
        handler.record_voice_metric("bad", object())

        handler.client_is_speaking = True
        self.assertTrue(handler.is_realtime_busy())
        handler.client_is_speaking = False
        handler.client_have_voice = True
        self.assertTrue(handler.is_realtime_busy())
        handler.client_have_voice = False
        handler.voice_provider = types.SimpleNamespace(_interaction=types.SimpleNamespace(state=types.SimpleNamespace(value="WAITING_MODEL")))
        self.assertTrue(handler.is_realtime_busy())
        handler.voice_provider._interaction.state = types.SimpleNamespace(value="IDLE")
        self.assertFalse(handler.is_realtime_busy())
        handler.voice_provider = types.SimpleNamespace(
            _fallback_provider=types.SimpleNamespace(_interaction=types.SimpleNamespace(state="MUSIC_PLAYING"))
        )
        self.assertTrue(handler.is_realtime_busy())

    async def test_lesson_pull_on_connect_swallows_runtime_import_or_start_failures(self):
        handler = _build_handler()
        await handler._lesson_pull_on_connect()

    async def test_lesson_pull_on_connect_returns_started_runtime_for_start_lesson_task(self):
        handler = _build_handler()
        runtime = object()

        async def _start(conn):
            self.assertIs(conn, handler)
            return runtime

        with patch("core.lesson.runtime.maybe_start_lesson_on_connect", _start):
            self.assertIs(await handler._lesson_pull_on_connect(), runtime)

    def test_google_live_chat_direct_call_does_not_queue_classic_tts(self):
        handler = _build_handler()
        handler.config["voice_mode"] = {"type": "google_live"}

        self.assertIsNone(handler.chat("xin chao"))

        self.assertTrue(handler.tts.tts_text_queue.empty())

    def test_chat_plain_text_llm_streams_tts_and_records_dialogue(self):
        handler = _build_handler()
        handler.loop = asyncio.get_event_loop()
        handler.llm = _LLM(["hello ", "world"])
        handler.intent_type = "nointent"
        handler.features = {"emoji": False}

        self.assertTrue(handler.chat("xin chao"))

        queued = []
        while not handler.tts.tts_text_queue.empty():
            queued.append(handler.tts.tts_text_queue.get_nowait())
        self.assertEqual([item.content_type for item in queued], [connection_module.ContentType.ACTION, connection_module.ContentType.TEXT, connection_module.ContentType.TEXT, connection_module.ContentType.ACTION])
        self.assertEqual(handler.tts.stored[-1][1], "hello world")

    def test_chat_handles_llm_and_stream_errors(self):
        handler = _build_handler()
        handler.loop = asyncio.get_event_loop()
        handler.llm = _LLM(fail=True)
        self.assertIsNone(handler.chat("boom"))

        class BadIterable:
            def __iter__(self):
                raise RuntimeError("stream failed")

        handler.llm = _LLM([BadIterable()])
        handler.llm.response = lambda *args, **kwargs: BadIterable()
        original_error = connection_module.get_system_error_response
        try:
            connection_module.get_system_error_response = lambda config: "system error"
            handler.chat("stream")
        finally:
            connection_module.get_system_error_response = original_error

        details = [getattr(handler.tts.tts_text_queue.get_nowait(), "content_detail", None) for _ in range(handler.tts.tts_text_queue.qsize())]
        self.assertIn("system error", details)

    def test_chat_direct_answer_stream_flushes_remaining_text(self):
        handler = _build_handler()
        handler.loop = asyncio.get_event_loop()
        handler.intent_type = "function_call"
        handler.func_handler = _FuncHandler()
        handler.features = {"emoji": False}
        delta = _ToolDelta(index=0, id="da1", name="direct_answer", arguments='{"response": "Hello child, keep going"}')
        handler.llm = _LLM([(None, [delta])])

        handler.chat("tell me")

        details = []
        while not handler.tts.tts_text_queue.empty():
            details.append(getattr(handler.tts.tts_text_queue.get_nowait(), "content_detail", None))
        streamed = "".join(part or "" for part in details)
        self.assertIn("Hello child", streamed)
        self.assertIn("keep", streamed)
        self.assertEqual(handler.tts.stored[-1][1], "Hello child, keep going")

    def test_chat_real_tool_call_handles_success_and_future_error(self):
        handler = _build_handler()
        handler.loop = asyncio.get_event_loop()
        handler.intent_type = "function_call"
        success = _action_response(connection_module.Action.RESPONSE, result="ok", response="ok")
        handler.func_handler = _FuncHandler(result=success)
        handler.features = {"emoji": False}
        delta = _ToolDelta(index=0, id="t1", name="tool_a", arguments='{"x": 1}')
        handler.llm = _LLM([("preface", [delta])])
        reports = []
        original_threadsafe = connection_module.asyncio.run_coroutine_threadsafe
        original_enqueue = connection_module.enqueue_tool_report
        try:
            connection_module.enqueue_tool_report = lambda *args, **kwargs: reports.append((args, kwargs))
            connection_module.asyncio.run_coroutine_threadsafe = lambda coro, loop: (coro.close(), _future_result(success))[1]
            handler.chat("use tool")
        finally:
            connection_module.asyncio.run_coroutine_threadsafe = original_threadsafe
            connection_module.enqueue_tool_report = original_enqueue

        self.assertEqual(len(reports), 2)
        self.assertEqual(handler.tts.one_sentence[-1][1], "ok")

        failing = _build_handler()
        failing.loop = asyncio.get_event_loop()
        failing.intent_type = "function_call"
        failing.func_handler = _FuncHandler(result=success)
        failing.features = {"emoji": False}
        failing.llm = _LLM([(None, [_ToolDelta(index=0, id="t2", name="tool_a", arguments="{}")])])
        try:
            connection_module.enqueue_tool_report = lambda *args, **kwargs: reports.append((args, kwargs))
            connection_module.asyncio.run_coroutine_threadsafe = lambda coro, loop: (coro.close(), _future_result(exc=TimeoutError("slow")))[1]
            failing.chat("timeout")
        finally:
            connection_module.asyncio.run_coroutine_threadsafe = original_threadsafe
            connection_module.enqueue_tool_report = original_enqueue

        self.assertEqual(failing.tts.one_sentence[-1][1], "Oops, network has problem, try again later!")
