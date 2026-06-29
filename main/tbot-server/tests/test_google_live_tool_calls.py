import asyncio
import json
import time
import unittest
from types import SimpleNamespace

from core.voice.google_live.client import GoogleLiveClient
from core.voice.live_admission import AdmissionDecision, LiveAdmissionResult
from core.voice.session_orchestrator import SessionMode
from core.voice.session_provider.google_live import (
    GoogleLiveProvider,
    LESSON_LIVE_TEXT_INSTRUCTION,
)
from plugins_func.register import Action, ActionResponse
# Importing change_volume registers it in all_function_registry so
# always-included live tools can be resolved during these tests.
import plugins_func.functions.change_volume  # noqa: F401
import plugins_func.functions.start_lesson as start_lesson_module
import core.voice.session_provider.google_live as google_live_module


class _DummyLogger:
    def __init__(self):
        self.messages = []

    def bind(self, **kwargs):
        return self

    def info(self, *args, **kwargs):
        self.messages.append(("info", args, kwargs))

    def warning(self, *args, **kwargs):
        self.messages.append(("warning", args, kwargs))

    def error(self, *args, **kwargs):
        self.messages.append(("error", args, kwargs))


_GET_WEATHER_SPEC = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Lấy thời tiết theo thành phố",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "title": "GetWeatherParams",
            "properties": {
                "city": {"type": "string", "title": "City"},
            },
            "required": ["city"],
        },
    },
}


class BuildToolsTest(unittest.TestCase):
    def test_build_tools_returns_none_when_no_functions(self):
        client = GoogleLiveClient({}, _DummyLogger())

        self.assertIsNone(client._build_tools())

    def test_build_tools_converts_openai_specs_into_function_declarations(self):
        client = GoogleLiveClient({"functions": [_GET_WEATHER_SPEC]}, _DummyLogger())

        tools = client._build_tools()

        self.assertEqual(len(tools), 1)
        declarations = tools[0]["function_declarations"]
        self.assertEqual(len(declarations), 1)
        declaration = declarations[0]
        self.assertEqual(declaration["name"], "get_weather")
        self.assertEqual(declaration["description"], "Lấy thời tiết theo thành phố")
        params = declaration["parameters"]
        self.assertEqual(params["type"], "object")
        self.assertEqual(params["properties"], {"city": {"type": "string"}})
        self.assertEqual(params["required"], ["city"])
        self.assertNotIn("additionalProperties", params)
        self.assertNotIn("title", params)
        self.assertNotIn("title", params["properties"]["city"])

    def test_build_tools_skips_malformed_entries(self):
        client = GoogleLiveClient(
            {
                "functions": [
                    {"function": {"description": "no name"}},
                    "not-a-dict",
                    {"function": {"name": "ok", "description": "fine"}},
                ]
            },
            _DummyLogger(),
        )

        tools = client._build_tools()
        declarations = tools[0]["function_declarations"]

        self.assertEqual([d["name"] for d in declarations], ["ok"])

    def test_build_connect_config_attaches_tools_when_configured(self):
        client = GoogleLiveClient(
            {"functions": [_GET_WEATHER_SPEC]},
            _DummyLogger(),
        )

        config = client._build_connect_config()

        self.assertIn("tools", config)
        self.assertEqual(
            config["tools"][0]["function_declarations"][0]["name"],
            "get_weather",
        )


class NormalizeToolCallTest(unittest.TestCase):
    def test_normalize_message_yields_tool_call_event(self):
        client = GoogleLiveClient({}, _DummyLogger())
        message = SimpleNamespace(
            tool_call=SimpleNamespace(
                function_calls=[
                    SimpleNamespace(id="abc-1", name="get_weather", args={"city": "Hà Nội"}),
                ]
            )
        )

        events = client._normalize_message(message)

        self.assertEqual(events, [
            {
                "type": "tool_call",
                "calls": [{"id": "abc-1", "name": "get_weather", "args": {"city": "Hà Nội"}}],
            }
        ])

    def test_normalize_message_yields_tool_call_cancellation(self):
        client = GoogleLiveClient({}, _DummyLogger())
        message = SimpleNamespace(
            tool_call_cancellation=SimpleNamespace(ids=["abc-1", "abc-2"]),
        )

        events = client._normalize_message(message)

        self.assertEqual(
            events,
            [{"type": "tool_call_cancellation", "ids": ["abc-1", "abc-2"]}],
        )

    def test_normalize_message_skips_calls_without_name(self):
        client = GoogleLiveClient({}, _DummyLogger())
        message = SimpleNamespace(
            tool_call=SimpleNamespace(
                function_calls=[SimpleNamespace(id="x", name=None, args={})]
            )
        )

        events = client._normalize_message(message)

        self.assertEqual(events, [])


class SendToolResponseTest(unittest.IsolatedAsyncioTestCase):
    async def test_send_tool_response_invokes_session_send_tool_response(self):
        client = GoogleLiveClient({}, _DummyLogger())

        class _Session:
            def __init__(self):
                self.captured = []

            async def send_tool_response(self, *, function_responses):
                self.captured.append(function_responses)

        session = _Session()
        client._session = session
        client.connected = True

        await client.send_tool_response(
            [
                {"id": "abc-1", "name": "get_weather", "response": {"result": "30°C"}},
            ]
        )

        self.assertEqual(len(session.captured), 1)
        function_responses = session.captured[0]
        self.assertEqual(len(function_responses), 1)
        payload = function_responses[0]
        self.assertEqual(payload["id"], "abc-1")
        self.assertEqual(payload["name"], "get_weather")
        self.assertEqual(payload["response"], {"result": "30°C"})

    async def test_send_tool_response_raises_when_not_connected(self):
        client = GoogleLiveClient({}, _DummyLogger())

        with self.assertRaises(RuntimeError):
            await client.send_tool_response([{"name": "x", "response": {}}])

    async def test_send_tool_response_wraps_non_mapping_payload(self):
        client = GoogleLiveClient({}, _DummyLogger())

        class _Session:
            async def send_tool_response(self, *, function_responses):
                self.received = function_responses

        session = _Session()
        client._session = session
        client.connected = True

        await client.send_tool_response(
            [{"id": "z", "name": "ping", "response": "ok"}]
        )

        self.assertEqual(session.received[0]["response"], {"result": "ok"})


class _ProviderConn:
    def __init__(self, func_handler=None):
        self.config = {
            "voice_mode": {"type": "google_live"},
            "google_live": {"api_key": "key", "model": "gemini-live"},
        }
        self.logger = _DummyLogger()
        self.func_handler = func_handler
        self.client_abort = False
        self.session_id = "s"
        self.websocket = None
        self.client_is_speaking = False
        self.sample_rate = 24000
        self.google_live_audio_out_started_at = None

    def _set_session_mode(self, mode, *, reason=""):
        self.session_mode = mode
        return mode

    def clear_queues(self):
        pass

    def clearSpeakStatus(self):
        self.client_is_speaking = False

class _LessonRuntimeStub:
    def __init__(self, *, passive=False, completed=False):
        self.state = "RUNNING"
        self._step = {"type": "model"}
        self._step_id = "s4"
        self._step_passive = passive
        self._step_completed = completed


class _FakeFuncHandler:
    def __init__(self, action_response, functions=None):
        self._response = action_response
        self.calls = []
        self._functions = functions or []

    def get_functions(self):
        return list(self._functions)

    async def handle_llm_function_call(self, conn, payload):
        self.calls.append(payload)
        return self._response

class _SlowFuncHandler(_FakeFuncHandler):
    def __init__(self, action_response, delay_sec, functions=None):
        super().__init__(action_response, functions=functions)
        self.delay_sec = delay_sec

    async def handle_llm_function_call(self, conn, payload):
        self.calls.append(payload)
        await asyncio.sleep(self.delay_sec)
        return self._response

class _RaisingFuncHandler(_FakeFuncHandler):
    async def handle_llm_function_call(self, conn, payload):
        self.calls.append(payload)
        raise RuntimeError("tool exploded")


class _RecordingClient:
    def __init__(self):
        self.sent_responses = []
        self.connected = True

    async def send_tool_response(self, responses):
        self.sent_responses.append(responses)

class _RecordingWebSocket:
    def __init__(self):
        self.sent = []

    async def send(self, payload):
        self.sent.append(payload)

class _RecordingQueue:
    def __init__(self):
        self.items = []

    def put(self, item):
        self.items.append(item)

class _RecordingTts:
    def __init__(self):
        self.tts_text_queue = _RecordingQueue()
        self.stored_texts = []

    def store_tts_text(self, sentence_id, text):
        self.stored_texts.append((sentence_id, text))

class _RecordingLessonRuntime:
    def __init__(self, handled=True):
        self.handled = handled
        self.responses = []

    async def on_child_response(self, text, *, source="voice_transcript"):
        self.responses.append((text, source))
        return self.handled

class _InteractiveRecordingLessonRuntime(_RecordingLessonRuntime):
    def __init__(self, handled=True):
        super().__init__(handled=handled)
        self.state = "RUNNING"
        self._step = {"type": "model"}
        self._step_id = "s4"
        self._step_passive = False
        self._step_completed = False


class ProviderToolCallTest(unittest.IsolatedAsyncioTestCase):
    def _make_provider(self, action_response):
        handler = _FakeFuncHandler(action_response)
        conn = _ProviderConn(func_handler=handler)
        provider = GoogleLiveProvider(conn)
        provider._client = _RecordingClient()
        return provider, handler

    async def test_tool_call_event_executes_handler_and_sends_response(self):
        provider, handler = self._make_provider(
            ActionResponse(action=Action.REQLLM, response="30°C tại Hà Nội"),
        )

        await provider._handle_tool_call_event(
            {
                "type": "tool_call",
                "calls": [
                    {"id": "call-1", "name": "get_weather", "args": {"city": "Hà Nội"}}
                ],
            }
        )

        self.assertEqual(len(handler.calls), 1)
        self.assertEqual(handler.calls[0]["name"], "get_weather")
        self.assertEqual(handler.calls[0]["arguments"], {"city": "Hà Nội"})

        sent = provider._client.sent_responses
        self.assertEqual(len(sent), 1)
        response = sent[0][0]
        self.assertEqual(response["id"], "call-1")
        self.assertEqual(response["name"], "get_weather")
        self.assertEqual(response["response"]["result"], "30°C tại Hà Nội")
        self.assertEqual(response["response"]["action"], "reqllm")
        completion_logs = [
            args
            for level, args, _kwargs in provider.conn.logger.messages
            if level == "info" and args and "tool_call_completed" in args[0]
        ]
        self.assertEqual(len(completion_logs), 1)
        self.assertEqual(completion_logs[0][1], "get_weather")
        self.assertEqual(completion_logs[0][2], "call-1")
        self.assertGreaterEqual(completion_logs[0][3], 0)
        self.assertTrue(completion_logs[0][4])
        self.assertEqual(completion_logs[0][5], "")

    async def test_tool_call_event_maps_error_action_to_error_payload(self):
        provider, _handler = self._make_provider(
            ActionResponse(action=Action.ERROR, response="rate limit"),
        )

        await provider._handle_tool_call_event(
            {
                "type": "tool_call",
                "calls": [{"id": "call-2", "name": "get_weather", "args": {}}],
            }
        )

        response = provider._client.sent_responses[0][0]
        self.assertEqual(
            response["response"],
            {"ok": False, "errorCode": "TOOL_ERROR", "message": "rate limit"},
        )
        completion_logs = [
            args
            for level, args, _kwargs in provider.conn.logger.messages
            if level == "info" and args and "tool_call_completed" in args[0]
        ]
        self.assertEqual(len(completion_logs), 1)
        self.assertFalse(completion_logs[0][4])
        self.assertEqual(completion_logs[0][5], "TOOL_ERROR")

    async def test_tool_call_event_without_func_handler_returns_error_payload(self):
        conn = _ProviderConn(func_handler=None)
        provider = GoogleLiveProvider(conn)
        provider._client = _RecordingClient()

        await provider._handle_tool_call_event(
            {
                "type": "tool_call",
                "calls": [{"id": "call-3", "name": "get_weather", "args": {}}],
            }
        )

        response = provider._client.sent_responses[0][0]
        self.assertEqual(
            response["response"],
            {
                "ok": False,
                "errorCode": "TOOL_HANDLER_UNAVAILABLE",
                "message": "Tool handler unavailable",
            },
        )

    async def test_tool_failure_returns_structured_error_payload(self):
        conn = _ProviderConn(func_handler=_RaisingFuncHandler(None))
        provider = GoogleLiveProvider(conn)
        provider._client = _RecordingClient()

        await provider._handle_tool_call_event(
            {
                "type": "tool_call",
                "calls": [{"id": "call-err", "name": "get_weather", "args": {}}],
            }
        )

        response = provider._client.sent_responses[0][0]
        self.assertEqual(response["id"], "call-err")
        self.assertEqual(
            response["response"],
            {
                "ok": False,
                "errorCode": "TOOL_EXCEPTION",
                "message": "tool exploded",
            },
        )

    async def test_tool_call_timeout_returns_structured_error_payload(self):
        handler = _SlowFuncHandler(
            ActionResponse(action=Action.REQLLM, response="late"),
            delay_sec=0.05,
        )
        conn = _ProviderConn(func_handler=handler)
        conn.config["google_live"]["tool_timeout_sec"] = 0.01
        provider = GoogleLiveProvider(conn)
        provider._client = _RecordingClient()

        await provider._handle_tool_call_event(
            {
                "type": "tool_call",
                "calls": [{"id": "call-timeout", "name": "get_weather", "args": {}}],
            }
        )

        response = provider._client.sent_responses[0][0]
        self.assertEqual(response["id"], "call-timeout")
        self.assertEqual(response["response"]["ok"], False)
        self.assertEqual(response["response"]["errorCode"], "TOOL_TIMEOUT")

    async def test_tool_call_with_non_mapping_args_is_rejected_before_handler(self):
        provider, handler = self._make_provider(
            ActionResponse(action=Action.REQLLM, response="should not run"),
        )

        await provider._handle_tool_call_event(
            {
                "type": "tool_call",
                "calls": [{"id": "call-badargs", "name": "get_weather", "args": ["bad"]}],
            }
        )

        self.assertEqual(handler.calls, [])
        response = provider._client.sent_responses[0][0]
        self.assertEqual(
            response["response"],
            {
                "ok": False,
                "errorCode": "INVALID_TOOL_ARGS",
                "message": "Tool arguments must be an object",
            },
        )

    async def test_tool_call_cancellation_suppresses_stale_result(self):
        handler = _SlowFuncHandler(
            ActionResponse(action=Action.REQLLM, response="late result"),
            delay_sec=0.03,
        )
        conn = _ProviderConn(func_handler=handler)
        provider = GoogleLiveProvider(conn)
        provider._client = _RecordingClient()

        task = asyncio.create_task(
            provider._handle_tool_call_event(
                {
                    "type": "tool_call",
                    "calls": [
                        {"id": "call-cancel", "name": "get_weather", "args": {"city": "Hà Nội"}}
                    ],
                }
            )
        )
        await asyncio.sleep(0)
        await provider._handle_tool_call_cancellation_event(
            {"type": "tool_call_cancellation", "ids": ["call-cancel"]}
        )
        await task

        self.assertEqual(provider._client.sent_responses, [])
        self.assertNotIn("call-cancel", provider._pending_tool_calls)

    async def test_dangerous_tool_requires_confirmation(self):
        provider, handler = self._make_provider(
            ActionResponse(action=Action.REQLLM, response="rebooted"),
        )
        provider.conn.config["google_live"]["dangerous_tool_names"] = ["reboot_device"]

        await provider._handle_tool_call_event(
            {
                "type": "tool_call",
                "calls": [{"id": "call-danger", "name": "reboot_device", "args": {}}],
            }
        )

        self.assertEqual(handler.calls, [])
        response = provider._client.sent_responses[0][0]
        self.assertEqual(response["response"]["ok"], False)
        self.assertEqual(response["response"]["errorCode"], "CONFIRMATION_REQUIRED")

    async def test_dangerous_tool_ignores_model_supplied_confirmed_flag(self):
        # The model supplies `args` in-band, so it must not be able to
        # self-bypass the dangerous-tool gate by re-issuing the call with
        # {"confirmed": true}.
        provider, handler = self._make_provider(
            ActionResponse(action=Action.REQLLM, response="rebooted"),
        )
        provider.conn.config["google_live"]["dangerous_tool_names"] = ["reboot_device"]

        await provider._handle_tool_call_event(
            {
                "type": "tool_call",
                "calls": [
                    {
                        "id": "call-danger",
                        "name": "reboot_device",
                        "args": {"confirmed": True},
                    }
                ],
            }
        )

        self.assertEqual(handler.calls, [])
        response = provider._client.sent_responses[0][0]
        self.assertEqual(response["response"]["ok"], False)
        self.assertEqual(response["response"]["errorCode"], "CONFIRMATION_REQUIRED")

    async def test_pattern_matched_dangerous_tool_ignores_confirmed_flag(self):
        # Same self-bypass protection for names matched by the danger pattern
        # (not just the explicit dangerous_tool_names list).
        provider, handler = self._make_provider(
            ActionResponse(action=Action.REQLLM, response="deleted"),
        )

        await provider._handle_tool_call_event(
            {
                "type": "tool_call",
                "calls": [
                    {
                        "id": "call-delete",
                        "name": "delete_account",
                        "args": {"confirmed": True},
                    }
                ],
            }
        )

        self.assertEqual(handler.calls, [])
        response = provider._client.sent_responses[0][0]
        self.assertEqual(response["response"]["errorCode"], "CONFIRMATION_REQUIRED")

    async def test_non_dangerous_tool_unaffected_by_gate(self):
        # Ordinary tools must still execute normally and are not gated.
        provider, handler = self._make_provider(
            ActionResponse(action=Action.REQLLM, response="22C"),
        )

        await provider._handle_tool_call_event(
            {
                "type": "tool_call",
                "calls": [
                    {"id": "call-ok", "name": "get_weather", "args": {"city": "x"}}
                ],
            }
        )

        response = provider._client.sent_responses[0][0]
        self.assertNotEqual(
            response["response"].get("errorCode"), "CONFIRMATION_REQUIRED"
        )
        self.assertEqual(len(handler.calls), 1)

    async def test_tool_call_cancellation_clears_pending_set(self):
        provider, _handler = self._make_provider(
            ActionResponse(action=Action.NONE, response=None),
        )
        provider._pending_tool_calls.update({"a", "b"})

        await provider._handle_tool_call_cancellation_event(
            {"type": "tool_call_cancellation", "ids": ["a"]}
        )

        self.assertEqual(provider._pending_tool_calls, {"b"})

    async def test_get_live_config_exposes_child_weather_from_product_toolset(self):
        handler = _FakeFuncHandler(
            ActionResponse(action=Action.NONE, response=None),
            functions=[_GET_WEATHER_SPEC],
        )
        conn = _ProviderConn(func_handler=handler)
        provider = GoogleLiveProvider(conn)

        config = provider._get_live_config_with_functions()

        names = [t["function"]["name"] for t in config["functions"]]
        self.assertIn("get_weather", names)
        self.assertIn("web_search", names)
        self.assertIn("get_news_from_newsnow", names)
        self.assertNotIn("get_news_from_chinanews", names)
        self.assertIn("change_role", names)
        self.assertIn("change_volume", names)

    async def test_live_functions_extra_can_be_overridden_via_google_live_config(self):
        # Ensure plugin module is registered so _build_descriptions_for finds it
        import plugins_func.functions.change_volume  # noqa: F401

        handler = _FakeFuncHandler(
            ActionResponse(action=Action.NONE, response=None),
            functions=[_GET_WEATHER_SPEC],
        )
        conn = _ProviderConn(func_handler=handler)
        conn.config["google_live"]["functions"] = ["change_volume"]
        provider = GoogleLiveProvider(conn)

        config = provider._get_live_config_with_functions()
        names = [t["function"]["name"] for t in config["functions"]]
        self.assertIn("change_volume", names)
        # google_live.functions no longer overrides the reviewed child product
        # toolset, and weather remains available for child-facing Live queries.
        self.assertIn("get_weather", names)
        self.assertIn("web_search", names)
        self.assertIn("get_news_from_newsnow", names)


class AudioBridgeToolCallForwardingTest(unittest.IsolatedAsyncioTestCase):
    async def test_handle_event_forwards_tool_call_to_handler(self):
        from core.voice.google_live.audio_bridge import GoogleLiveAudioBridge

        received = []

        async def _handler(event):
            received.append(event)

        bridge = GoogleLiveAudioBridge(
            conn=SimpleNamespace(
                config={"google_live": {}},
                websocket=None,
                sample_rate=24000,
                google_live_audio_out_started_at=None,
            ),
            client=SimpleNamespace(config={}),
            logger=_DummyLogger(),
            tool_call_handler=_handler,
        )

        event = {"type": "tool_call", "calls": [{"id": "1", "name": "x", "args": {}}]}
        handled = await bridge.handle_event(event)

        self.assertTrue(handled)
        self.assertEqual(received, [event])

    async def test_user_transcript_forwards_to_local_intent_handler_when_idle(self):
        from core.voice.google_live.audio_bridge import GoogleLiveAudioBridge

        received = []
        logger = _DummyLogger()

        async def _handler(text):
            received.append(text)
            return True

        bridge = GoogleLiveAudioBridge(
            conn=SimpleNamespace(
                config={"google_live": {}},
                websocket=None,
                sample_rate=24000,
                google_live_audio_out_started_at=None,
            ),
            client=SimpleNamespace(config={}),
            logger=logger,
            user_transcript_handler=_handler,
        )

        handled = await bridge.handle_event(
            {"type": "transcript", "source": "user", "text": "bắt đầu bài học"}
        )

        self.assertTrue(handled)
        self.assertEqual(received, ["bắt đầu bài học"])
        transcript_logs = [
            args
            for level, args, _kwargs in logger.messages
            if level == "info"
            and args
            and str(args[0]).startswith("Google Live transcript source=")
        ]
        self.assertEqual(len(transcript_logs), 1)
        self.assertIn("text=", transcript_logs[0][0])
        self.assertEqual(transcript_logs[0][3], "bắt đầu bài học")

    async def test_lesson_mode_drops_live_model_output_events(self):
        from core.voice.google_live.audio_bridge import GoogleLiveAudioBridge

        class _WebSocket:
            def __init__(self):
                self.sent = []

            async def send(self, payload):
                self.sent.append(payload)

        websocket = _WebSocket()
        conn = SimpleNamespace(
            config={"google_live": {}},
            websocket=websocket,
            session_id="s",
            session_mode=SessionMode.LESSON,
            sample_rate=24000,
            google_live_audio_out_started_at=None,
        )
        bridge = GoogleLiveAudioBridge(
            conn=conn,
            client=SimpleNamespace(config={}),
            logger=_DummyLogger(),
        )

        for event in (
            {"type": "transcript", "source": "model", "text": "À, con muốn chơi I Spy?"},
            {"type": "audio_start"},
            {"type": "audio", "audio": b"model-audio"},
            {"type": "audio_end"},
            {"type": "tool_call", "calls": [{"id": "1", "name": "play_music", "args": {}}]},
        ):
            with self.subTest(event=event["type"]):
                self.assertTrue(await bridge.handle_event(event))

        self.assertEqual(websocket.sent, [])
        self.assertIsNone(conn.google_live_audio_out_started_at)


class VietnameseMusicControlIntentTest(unittest.IsolatedAsyncioTestCase):
    def _make_provider(self):
        handler = _FakeFuncHandler(ActionResponse(action=Action.NONE, response="ok"))
        conn = _ProviderConn(func_handler=handler)
        conn._music_session = SimpleNamespace(stop_event=SimpleNamespace(is_set=lambda: False))
        provider = GoogleLiveProvider(conn)

        class _Client:
            connected = True

            async def interrupt(self):
                return None

            async def end_audio_stream(self):
                return None

        provider._client = _Client()
        return provider, handler, conn

    async def test_stop_pause_resume_music_commands_dispatch_local_tools(self):
        cases = [
            ("tắt nhạc", "stop_music"),
            ("dừng nhạc", "stop_music"),
            ("tạm dừng nhạc", "pause_music"),
            ("nghe tiếp", "resume_music"),
            ("phát tiếp", "resume_music"),
            ("tiếp tục phát nhạc", "resume_music"),
        ]

        for text, expected_tool in cases:
            provider, handler, _conn = self._make_provider()
            handled = await provider._dispatch_music_control_intent(text)

            self.assertTrue(handled, text)
            self.assertEqual(handler.calls[-1]["name"], expected_tool)

    async def test_named_play_music_uses_spoken_title_and_does_not_invent_song(self):
        provider, handler, _conn = self._make_provider()

        handled = await provider._dispatch_music_control_intent("phát bài Nơi này có anh")

        self.assertTrue(handled)
        payload = handler.calls[-1]
        self.assertEqual(payload["name"], "play_music")
        self.assertEqual(payload["arguments"]["song_name"], "Nơi này có anh")

    async def test_named_play_music_without_title_is_not_dispatched(self):
        provider, handler, _conn = self._make_provider()

        handled = await provider._dispatch_music_control_intent("phát bài")

        self.assertFalse(handled)
        self.assertEqual(handler.calls, [])

class VietnameseLessonStartIntentTest(unittest.IsolatedAsyncioTestCase):
    def _make_provider(self):
        handler = _FakeFuncHandler(ActionResponse(action=Action.NONE, response="ok"))
        conn = _ProviderConn(func_handler=handler)
        conn._lesson_runtime_enabled = lambda: True
        provider = GoogleLiveProvider(conn)

        class _Client:
            connected = True
            sent_texts = []

            async def close(self):
                return None

            async def interrupt(self):
                return None

            async def end_audio_stream(self):
                return None

            async def send_text(self, text):
                self.sent_texts.append(text)

        provider._client = _Client()
        return provider, handler

    async def test_start_lesson_command_dispatches_local_tool(self):
        provider, handler = self._make_provider()

        handled = await provider._dispatch_lesson_start_intent("bắt đầu bài học")

        self.assertTrue(handled)
        self.assertEqual(handler.calls[-1]["name"], "start_lesson")
        self.assertEqual(handler.calls[-1]["arguments"], {})

    async def test_start_lesson_transcript_marker_is_noop_when_tool_not_admitted(self):
        provider, handler = self._make_provider()
        provider.conn._lesson_runtime_enabled = lambda: False
        provider._client.sent_texts = []

        handled = await provider._dispatch_lesson_start_intent("bắt đầu bài học")

        self.assertFalse(handled)
        self.assertEqual(handler.calls, [])
        self.assertEqual(provider._client.sent_texts, [])

    async def test_start_lesson_command_accepts_common_stt_variants(self):
        variants = [
            "BẮT ĐẦU BÀI HỌC!",
            "  bắt   đầu   bài   học  ",
            "bat dau bai hoc",
            "bắt đầu 1 bài học",
            "bắt đầu một bài học",
            "bắt đầu học bài",
            "Quay đầu bài học",
            "TeeBot, bắt đầu bài học nhé",
            "bắt đầu bài học đi",
            "vào bài học của con",
            "vào học bài",
            "vô bài học",
            "vo bai hoc",
            "bắt đầu khoá học",
            "bắt đầu khóa học",
            "vào khóa học",
            "vào khoá học",
            "vao khoa hoc",
            "mở khóa học",
            "mở khoá học",
            "mở khóa học của con",
            "mo khoa hoc",
            "học bài thôi",
            "học bài đi",
            "tiếp tục bài học",
            "tiếp tục khóa học",
            "tiep tuc khoa hoc",
            # Real Google Live ASR heard the user's Vietnamese "bắt đầu bài học"
            # request as this exact English phrase in production.
            "high speed",
            # Real production transcript for "Alô, bắt đầu bài học" on
            # 2026-06-28. This must start the lesson, not fall through to chat.
            "Alô. High Speed",
            "alo high speed",
            "hello high speed",
            # Mac/Google Live can collapse "Hi ESP ... start" into this 14-char
            # transcript, which previously fell through to normal chat.
            "hi speed start",
        ]

        for variant in variants:
            with self.subTest(variant=variant):
                provider, handler = self._make_provider()

                handled = await provider._dispatch_lesson_start_intent(variant)

                self.assertTrue(handled)
                self.assertEqual(handler.calls[-1], {"name": "start_lesson", "arguments": {}})

    async def test_start_lesson_command_enqueues_audible_ack(self):
        provider, handler = self._make_provider()
        provider.conn.websocket = _RecordingWebSocket()
        provider.conn.tts = _RecordingTts()
        provider.conn.sentence_id = None
        provider._client.sent_texts = []

        handled = await provider._dispatch_lesson_start_intent("bắt đầu bài học")

        self.assertTrue(handled)
        self.assertEqual(handler.calls[-1]["name"], "start_lesson")
        self.assertEqual(
            provider._client.sent_texts,
            [LESSON_LIVE_TEXT_INSTRUCTION + "Bắt đầu bài học nhé."],
        )
        self.assertEqual(provider.conn.websocket.sent, [])
        self.assertEqual(provider.conn.tts.stored_texts, [])
        self.assertEqual(len(provider.conn.tts.tts_text_queue.items), 0)

    async def test_start_lesson_ack_prefers_google_live_over_local_tts(self):
        provider, handler = self._make_provider()
        provider.conn.websocket = _RecordingWebSocket()
        provider.conn.tts = _RecordingTts()
        provider.conn.sentence_id = None
        provider._client.sent_texts = []

        handled = await provider._dispatch_lesson_start_intent("bắt đầu bài học")

        self.assertTrue(handled)
        self.assertEqual(handler.calls[-1]["name"], "start_lesson")
        self.assertEqual(
            provider._client.sent_texts,
            [LESSON_LIVE_TEXT_INSTRUCTION + "Bắt đầu bài học nhé."],
        )
        self.assertEqual(provider.conn.websocket.sent, [])
        self.assertEqual(provider.conn.tts.stored_texts, [])
        self.assertEqual(len(provider.conn.tts.tts_text_queue.items), 0)

    async def test_text_message_start_lesson_uses_local_tool_not_chat_forwarding(self):
        provider, handler = self._make_provider()
        async def _active_voice_consent(_conn):
            return True

        provider.conn.voice_consent_client = SimpleNamespace(
            ensure_voice_allowed=_active_voice_consent,
        )
        provider._client.sent_texts = []

        handled = await provider.handle_text_message(
            json.dumps({"type": "text", "text": "bắt đầu bài học"})
        )

        self.assertTrue(handled)
        self.assertEqual(handler.calls[-1], {"name": "start_lesson", "arguments": {}})
        self.assertNotIn("bắt đầu bài học", provider._client.sent_texts)
        self.assertEqual(
            provider._client.sent_texts,
            [LESSON_LIVE_TEXT_INSTRUCTION + "Bắt đầu bài học nhé."],
        )

    async def test_user_transcript_wake_as_i_spy_opens_listening_window_not_chat(self):
        provider, handler = self._make_provider()
        provider.conn.websocket = _RecordingWebSocket()

        handled = await provider._on_user_transcript("hai spy")

        self.assertTrue(handled)
        self.assertGreater(provider._user_audio_allowed_until, time.monotonic())
        self.assertEqual(handler.calls, [])
        sent = [json.loads(payload) for payload in provider.conn.websocket.sent]
        self.assertEqual(sent[0]["type"], "stt")
        self.assertEqual(sent[0]["text"], "hai spy")
        self.assertEqual(sent[1]["type"], "tts")
        self.assertTrue(sent[1]["continue_listening"])

    async def test_lesson_step_prompt_prefers_google_live_voice_over_local_tts(self):
        provider, _handler = self._make_provider()
        provider.conn.websocket = _RecordingWebSocket()
        provider.conn.tts = _RecordingTts()
        provider.conn.sentence_id = None
        provider.conn.config["child_profile"] = {"child_name": "Bong"}

        spoken = await provider.speak_lesson_step_prompt("Welcome to the barn story.")

        self.assertTrue(spoken)
        self.assertEqual(
            provider._client.sent_texts,
            [LESSON_LIVE_TEXT_INSTRUCTION + "Welcome to the barn story."],
        )
        self.assertEqual(provider.conn.websocket.sent, [])
        self.assertEqual(provider.conn.tts.stored_texts, [])
        self.assertEqual(len(provider.conn.tts.tts_text_queue.items), 0)
        self.assertTrue(provider.conn.google_live_lesson_prompt_output_allowed)

    async def test_lesson_step_prompt_falls_back_to_local_tts_when_live_text_unavailable(self):
        from core.providers.tts.dto.dto import ContentType

        provider, _handler = self._make_provider()
        provider._client = None
        provider.conn.websocket = _RecordingWebSocket()
        provider.conn.tts = _RecordingTts()
        provider.conn.sentence_id = None

        spoken = await provider.speak_lesson_step_prompt("Say barn.", continue_listening=True)

        self.assertTrue(spoken)
        self.assertTrue(provider.conn.lesson_continue_listening_after_tts_stop)
        self.assertEqual(provider.conn.tts.stored_texts[-1][1], "Say barn.")
        self.assertEqual(len(provider.conn.tts.tts_text_queue.items), 3)
        self.assertIsNot(provider.conn.tts.tts_text_queue.items[-1].content_type, ContentType.TEXT)

    async def test_lesson_step_prompt_initializes_tts_when_google_live_is_dormant(self):
        from core.providers.tts.dto.dto import ContentType

        provider, _handler = self._make_provider()
        provider.conn.websocket = _RecordingWebSocket()
        provider.conn.tts = None
        provider.conn.sentence_id = None
        calls = []

        async def _ensure_lesson_tts():
            calls.append("ensure")
            provider.conn.tts = _RecordingTts()
            return True

        provider.conn.ensure_lesson_tts = _ensure_lesson_tts

        spoken = await provider.speak_lesson_step_prompt("Say barn.")

        self.assertTrue(spoken)
        self.assertEqual(calls, [])
        self.assertEqual(
            provider._client.sent_texts,
            [LESSON_LIVE_TEXT_INSTRUCTION + "Say barn."],
        )
        self.assertIsNone(provider.conn.tts)

    async def test_lesson_step_prompt_opens_live_when_lesson_mode_is_dormant(self):
        provider, _handler = self._make_provider()
        provider.conn.session_mode = SessionMode.LESSON
        provider.conn.websocket = _RecordingWebSocket()
        provider.conn.tts = _RecordingTts()
        provider._client = None
        opened = []

        async def _active_voice_consent(_conn):
            return True

        provider.conn.voice_consent_client = SimpleNamespace(
            ensure_voice_allowed=_active_voice_consent,
        )

        class _Client:
            connected = True

            def __init__(self):
                self.sent_texts = []

            async def send_text(self, text):
                self.sent_texts.append(text)

        async def _admit_live_open():
            return LiveAdmissionResult(AdmissionDecision.ALLOW_LIVE)

        async def _open_live_session():
            opened.append("open")
            provider._client = _Client()
            provider._bridge = SimpleNamespace(allow_model_output=lambda: None)

        provider._admit_live_open = _admit_live_open
        provider._open_live_session = _open_live_session

        spoken = await provider.speak_lesson_step_prompt("Say barn.")

        self.assertTrue(spoken)
        self.assertEqual(opened, ["open"])
        self.assertEqual(
            provider._client.sent_texts,
            [LESSON_LIVE_TEXT_INSTRUCTION + "Say barn."],
        )
        self.assertEqual(provider.conn.tts.stored_texts, [])

    async def test_lesson_live_text_unblocks_interrupted_model_output_before_send(self):
        provider, _handler = self._make_provider()
        blocked_at_send = []

        class _Client:
            async def send_text(self, _text):
                blocked_at_send.append(bridge.blocked)

        class _Bridge:
            def __init__(self):
                self.blocked = True

            def allow_model_output(self):
                self.blocked = False

        bridge = _Bridge()
        provider._client = _Client()
        provider._bridge = bridge

        sent = await provider.speak_lesson_step_prompt("Con thử nói lại nhé.")

        self.assertTrue(sent)
        self.assertEqual(blocked_at_send, [False])

    async def test_lesson_child_response_window_waits_for_prompt_guard(self):
        provider, _handler = self._make_provider()
        provider.conn.websocket = _RecordingWebSocket()
        provider.conn.tts = _RecordingTts()
        provider.conn.sentence_id = None
        provider.conn.config["google_live"].update(
            {
                "lesson_child_response_open_delay_sec": 2.0,
                "lesson_prompt_tts_chars_per_sec": 10.0,
                "lesson_child_response_max_open_delay_sec": 20.0,
            }
        )
        sleeps = []

        async def _fake_sleep(delay):
            sleeps.append(delay)

        original_sleep = google_live_module.asyncio.sleep
        google_live_module.asyncio.sleep = _fake_sleep
        try:
            self.assertTrue(await provider.speak_lesson_step_prompt("Say barn now."))
            self.assertEqual(provider._user_audio_allowed_until, 0.0)

            self.assertTrue(await provider.open_lesson_child_response_window())
        finally:
            google_live_module.asyncio.sleep = original_sleep

        self.assertEqual(len(sleeps), 1)
        self.assertGreaterEqual(sleeps[0], 3.0)
        self.assertGreater(provider._user_audio_allowed_until, time.monotonic())

    async def test_lesson_child_response_window_has_default_prompt_guard(self):
        provider, _handler = self._make_provider()
        sleeps = []

        async def _fake_sleep(delay):
            sleeps.append(delay)

        original_sleep = google_live_module.asyncio.sleep
        google_live_module.asyncio.sleep = _fake_sleep
        try:
            self.assertTrue(await provider.speak_lesson_step_prompt("Bây giờ con hãy nói theo mình nào: barn!"))
            self.assertTrue(await provider.open_lesson_child_response_window())
        finally:
            google_live_module.asyncio.sleep = original_sleep

        self.assertEqual(len(sleeps), 1)
        self.assertGreater(sleeps[0], 2.0)

    async def test_lesson_child_response_window_uses_lesson_duration_without_session_mode(self):
        provider, _handler = self._make_provider()
        provider.conn.config["google_live"]["lesson_child_response_window_sec"] = 25.0

        await provider.open_lesson_child_response_window()

        remaining = provider._user_audio_allowed_until - time.monotonic()
        self.assertGreater(remaining, 20.0)

    async def test_user_transcript_completes_active_lesson_step_before_chat_intents(self):
        provider, handler = self._make_provider()
        provider.conn.lesson_runtime = _RecordingLessonRuntime(handled=True)
        provider._client.sent_texts = []
        await provider.open_lesson_child_response_window()

        handled = await provider._on_user_transcript("con thấy barn")

        self.assertTrue(handled)
        self.assertEqual(
            provider.conn.lesson_runtime.responses,
            [("con thấy barn", "voice_transcript")],
        )
        self.assertEqual(handler.calls, [])
        self.assertEqual(provider._client.sent_texts, [])

    async def test_lesson_child_response_blocks_live_model_output_for_same_turn(self):
        provider, handler = self._make_provider()
        provider.conn.lesson_runtime = _RecordingLessonRuntime(handled=True)

        class _BlockingBridge:
            def __init__(self):
                self.stop_calls = 0
                self.blocked = False

            async def stop_output(self):
                self.stop_calls += 1
                self.blocked = True

            def is_model_output_blocked(self):
                return self.blocked

        bridge = _BlockingBridge()
        provider._bridge = bridge
        await provider.open_lesson_child_response_window()

        handled = await provider._on_user_transcript("barn")

        self.assertTrue(handled)
        self.assertEqual(provider.conn.lesson_runtime.responses, [("barn", "voice_transcript")])
        self.assertEqual(bridge.stop_calls, 1)
        self.assertTrue(bridge.is_model_output_blocked())
        self.assertEqual(handler.calls, [])

    async def test_lesson_child_response_blocks_model_output_before_runtime_advance(self):
        provider, handler = self._make_provider()

        class _BlockingBridge:
            def __init__(self):
                self.blocked = False

            async def stop_output(self):
                self.blocked = True

            def is_model_output_blocked(self):
                return self.blocked

        bridge = _BlockingBridge()

        class _Runtime:
            def __init__(self):
                self.responses = []

            async def on_child_response(self, text, *, source="voice_transcript"):
                self.responses.append((text, source, bridge.is_model_output_blocked()))
                return True

        provider._bridge = bridge
        provider.conn.lesson_runtime = _Runtime()
        await provider.open_lesson_child_response_window()

        handled = await provider._on_user_transcript("barn")

        self.assertTrue(handled)
        self.assertEqual(provider.conn.lesson_runtime.responses, [("barn", "voice_transcript", True)])
        self.assertEqual(handler.calls, [])

    async def test_lesson_child_response_closes_waiting_model_turn_for_next_step_audio(self):
        provider, handler = self._make_provider()
        provider.conn.lesson_runtime = _RecordingLessonRuntime(handled=True)
        provider._interaction.transition(google_live_module.InteractionState.WAITING_MODEL)

        async def _active_voice_consent(_conn):
            return True

        provider.conn.voice_consent_client = SimpleNamespace(
            ensure_voice_allowed=_active_voice_consent,
        )

        class _AudioBridge:
            def __init__(self):
                self.forwarded = []

            def is_model_output_blocked(self):
                return False

            async def decode_input_audio_async(self, audio):
                return b"pcm:" + audio

            def input_rms(self, _pcm):
                return 1000

            async def forward_decoded_input_audio(self, pcm):
                self.forwarded.append(pcm)

        provider._bridge = _AudioBridge()
        await provider.open_lesson_child_response_window()

        handled = await provider._on_user_transcript("barn")

        self.assertTrue(handled)
        self.assertEqual(provider.conn.lesson_runtime.responses, [("barn", "voice_transcript")])
        self.assertEqual(provider._interaction.state, google_live_module.InteractionState.LISTENING)

        self.assertTrue(await provider.handle_audio_bytes(b"next-step"))
        self.assertEqual(provider._bridge.forwarded, [b"pcm:next-step"])
        self.assertEqual(handler.calls, [])

    async def test_waiting_model_does_not_drop_audio_during_open_lesson_response_window(self):
        provider, handler = self._make_provider()
        provider.conn.lesson_runtime = _InteractiveRecordingLessonRuntime(handled=True)
        provider._interaction.transition(google_live_module.InteractionState.WAITING_MODEL)
        provider._user_stream_response_id = provider._response_generation
        provider._user_stream_started_at = time.monotonic() - 10
        provider._user_stream_last_speech_at = time.monotonic() - 10

        async def _active_voice_consent(_conn):
            return True

        provider.conn.voice_consent_client = SimpleNamespace(
            ensure_voice_allowed=_active_voice_consent,
        )

        class _AudioBridge:
            def __init__(self):
                self.forwarded = []

            def is_model_output_blocked(self):
                return False

            async def decode_input_audio_async(self, audio):
                return b"pcm:" + audio

            def input_rms(self, _pcm):
                return 1000

            async def forward_decoded_input_audio(self, pcm):
                self.forwarded.append(pcm)

        provider._bridge = _AudioBridge()
        await provider.open_lesson_child_response_window()

        self.assertTrue(await provider.handle_audio_bytes(b"barn"))

        self.assertEqual(provider._bridge.forwarded, [b"pcm:barn"])
        self.assertNotEqual(
            provider._interaction.state,
            google_live_module.InteractionState.WAITING_MODEL,
        )
        self.assertEqual(handler.calls, [])

    async def test_text_message_completes_active_lesson_step_before_chat_model(self):
        provider, handler = self._make_provider()
        provider.conn.lesson_runtime = _RecordingLessonRuntime(handled=True)

        async def _active_voice_consent(_conn):
            return True

        provider.conn.voice_consent_client = SimpleNamespace(
            ensure_voice_allowed=_active_voice_consent,
        )

        class _TextClient:
            connected = True

            def __init__(self):
                self.sent_texts = []

            async def send_text(self, text):
                self.sent_texts.append(text)

            async def close(self):
                self.connected = False

        provider._client = _TextClient()
        await provider.open_lesson_child_response_window()

        handled = await provider.handle_text_message('{"type":"text","text":"con thấy barn"}')

        self.assertTrue(handled)
        self.assertEqual(
            provider.conn.lesson_runtime.responses,
            [("con thấy barn", "voice_transcript")],
        )
        self.assertEqual(handler.calls, [])
        self.assertEqual(provider._client.sent_texts, [])

    async def test_user_transcript_outside_lesson_response_window_is_not_child_answer(self):
        provider, handler = self._make_provider()
        provider.conn.lesson_runtime = _RecordingLessonRuntime(handled=True)
        provider._user_audio_allowed_until = time.monotonic() - 1

        handled = await provider._on_user_transcript("barn")

        self.assertFalse(handled)
        self.assertEqual(provider.conn.lesson_runtime.responses, [])
        self.assertEqual(handler.calls, [])

    async def test_user_transcript_barge_in_does_not_interrupt_when_lesson_accepts_response(self):
        provider, handler = self._make_provider()
        provider.conn.lesson_runtime = _RecordingLessonRuntime(handled=True)
        interrupts = []

        async def _begin_user_interrupt(reason):
            interrupts.append(reason)

        provider._begin_user_interrupt = _begin_user_interrupt
        await provider.open_lesson_child_response_window()

        await provider._on_user_transcript_barge_in("barn")

        self.assertEqual(provider.conn.lesson_runtime.responses, [("barn", "voice_transcript")])
        self.assertEqual(interrupts, [])
        self.assertEqual(handler.calls, [])

    async def test_blank_lesson_transcript_is_not_treated_as_child_response_or_command(self):
        provider, handler = self._make_provider()
        provider.conn.lesson_runtime = _RecordingLessonRuntime(handled=False)
        provider._client.sent_texts = []
        await provider.open_lesson_child_response_window()

        handled = await provider._on_user_transcript("   ")

        self.assertFalse(handled)
        self.assertEqual(provider.conn.lesson_runtime.responses, [("   ", "voice_transcript")])
        self.assertEqual(handler.calls, [])
        self.assertEqual(provider._client.sent_texts, [])

    async def test_unhandled_lesson_transcript_can_still_dispatch_start_lesson_command(self):
        provider, handler = self._make_provider()
        provider.conn.lesson_runtime = _RecordingLessonRuntime(handled=False)
        await provider.open_lesson_child_response_window()

        handled = await provider._on_user_transcript("bắt đầu bài học")

        self.assertTrue(handled)
        self.assertEqual(provider.conn.lesson_runtime.responses, [("bắt đầu bài học", "voice_transcript")])
        self.assertEqual(handler.calls[-1], {"name": "start_lesson", "arguments": {}})

    async def test_lesson_interactive_audio_can_open_live_for_transcript(self):
        provider, _handler = self._make_provider()
        provider.conn.session_mode = SessionMode.LESSON
        provider.conn.lesson_runtime = _LessonRuntimeStub(passive=False, completed=False)
        provider._bridge = None
        opened = []

        async def _admit_live_open():
            return LiveAdmissionResult(AdmissionDecision.ALLOW_LIVE)

        async def _open_live_session():
            opened.append("open")
            provider._client = SimpleNamespace(connected=True)
            provider._bridge = object()

        provider._admit_live_open = _admit_live_open
        provider._open_live_session = _open_live_session

        allowed = await provider._ensure_live_open_for_audio()

        self.assertTrue(allowed)
        self.assertEqual(opened, ["open"])

    async def test_lesson_listen_start_uses_child_response_window(self):
        provider, _handler = self._make_provider()
        provider.conn.session_mode = SessionMode.LESSON
        provider.conn.lesson_runtime = _LessonRuntimeStub(passive=False, completed=False)
        provider.conn.config["google_live"]["wake_audio_allow_window_sec"] = 5
        provider.conn.config["google_live"]["lesson_child_response_window_sec"] = 25

        await provider._open_user_audio_window("listen_start")

        remaining = provider._user_audio_allowed_until - time.monotonic()
        self.assertGreater(remaining, 20)

    async def test_lesson_passive_audio_does_not_open_live(self):
        provider, _handler = self._make_provider()
        provider.conn.session_mode = SessionMode.LESSON
        provider.conn.lesson_runtime = _LessonRuntimeStub(passive=True, completed=False)
        opened = []

        async def _open_live_session():
            opened.append("open")

        provider._open_live_session = _open_live_session

        allowed = await provider._ensure_live_open_for_audio()

        self.assertFalse(allowed)
        self.assertEqual(opened, [])

    async def test_teach_me_now_command_is_not_lesson_start(self):
        provider, handler = self._make_provider()

        handled = await provider._dispatch_lesson_start_intent("dạy con học chữ")

        self.assertFalse(handled)
        self.assertEqual(handler.calls, [])


class StartLessonNoAssignmentFeedbackTest(unittest.IsolatedAsyncioTestCase):
    async def test_async_pull_without_assignment_sends_audible_failure(self):
        class _VoiceProvider:
            def __init__(self):
                self.acks = []

            async def _send_lesson_start_ack(self, action_response):
                self.acks.append(action_response.response)
                return True

        class _Conn:
            def __init__(self):
                self.logger = _DummyLogger()
                self.loop = asyncio.get_running_loop()
                self.voice_provider = _VoiceProvider()
                self.lesson_pull_task = None
                self.lesson_start_status = None

            def _lesson_runtime_enabled(self):
                return True

            async def _lesson_pull_on_connect(self):
                self.lesson_start_status = {
                    "code": "NO_CURRENT_ASSIGNMENT",
                    "message": "Robot chưa có bài học nào được giao.",
                }
                return None

        conn = _Conn()

        response = start_lesson_module.start_lesson(conn)
        for _ in range(10):
            if conn.voice_provider.acks:
                break
            await asyncio.sleep(0)

        self.assertEqual(response.action, Action.RECORD)
        self.assertEqual(
            conn.voice_provider.acks,
            ["Robot chưa có bài học nào được giao."],
        )

    async def test_handle_event_handles_tool_call_cancellation(self):
        from core.voice.google_live.audio_bridge import GoogleLiveAudioBridge

        received = []

        async def _handler(event):
            received.append(event)

        bridge = GoogleLiveAudioBridge(
            conn=SimpleNamespace(
                config={"google_live": {}},
                websocket=None,
                sample_rate=24000,
                google_live_audio_out_started_at=None,
            ),
            client=SimpleNamespace(config={}),
            logger=_DummyLogger(),
            tool_call_cancellation_handler=_handler,
        )

        event = {"type": "tool_call_cancellation", "ids": ["a"]}
        handled = await bridge.handle_event(event)

        self.assertTrue(handled)
        self.assertEqual(received, [event])


if __name__ == "__main__":
    unittest.main()
