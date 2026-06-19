import asyncio
import json
import unittest
from types import SimpleNamespace

from core.voice.google_live.client import GoogleLiveClient
from core.voice.session_provider.google_live import GoogleLiveProvider
from plugins_func.register import Action, ActionResponse
# Importing change_volume registers it in all_function_registry so
# always-included live tools can be resolved during these tests.
import plugins_func.functions.change_volume  # noqa: F401
import plugins_func.functions.start_lesson as start_lesson_module


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

    def clear_queues(self):
        pass

    def clearSpeakStatus(self):
        self.client_is_speaking = False


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

    async def test_get_live_config_uses_child_product_toolset_not_func_handler_weather(self):
        handler = _FakeFuncHandler(
            ActionResponse(action=Action.NONE, response=None),
            functions=[_GET_WEATHER_SPEC],
        )
        conn = _ProviderConn(func_handler=handler)
        provider = GoogleLiveProvider(conn)

        config = provider._get_live_config_with_functions()

        names = [t["function"]["name"] for t in config["functions"]]
        self.assertNotIn("get_weather", names)
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
        # toolset, and weather remains excluded from child-facing Live tools.
        self.assertNotIn("get_weather", names)


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
            logger=_DummyLogger(),
            user_transcript_handler=_handler,
        )

        handled = await bridge.handle_event(
            {"type": "transcript", "source": "user", "text": "bắt đầu bài học"}
        )

        self.assertTrue(handled)
        self.assertEqual(received, ["bắt đầu bài học"])


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

    async def test_start_lesson_command_enqueues_audible_ack(self):
        provider, handler = self._make_provider()
        provider.conn.websocket = _RecordingWebSocket()
        provider.conn.tts = _RecordingTts()
        provider.conn.sentence_id = None

        handled = await provider._dispatch_lesson_start_intent("bắt đầu bài học")

        self.assertTrue(handled)
        self.assertEqual(handler.calls[-1]["name"], "start_lesson")
        self.assertGreaterEqual(len(provider.conn.websocket.sent), 1)
        tts_message = json.loads(provider.conn.websocket.sent[0])
        self.assertEqual(tts_message["type"], "tts")
        self.assertEqual(tts_message["state"], "sentence_start")
        self.assertEqual(tts_message["text"], "Bắt đầu bài học nhé.")
        self.assertEqual(
            provider.conn.tts.stored_texts[-1][1],
            "Bắt đầu bài học nhé.",
        )
        self.assertEqual(len(provider.conn.tts.tts_text_queue.items), 3)

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
            ["Nói đúng một câu này, không thêm gì: Bắt đầu bài học nhé."],
        )

    async def test_lesson_step_prompt_uses_same_local_tts_path(self):
        provider, _handler = self._make_provider()
        provider.conn.websocket = _RecordingWebSocket()
        provider.conn.tts = _RecordingTts()
        provider.conn.sentence_id = None
        provider.conn.config["child_profile"] = {"child_name": "Bong"}

        spoken = await provider.speak_lesson_step_prompt("Welcome to the barn story.")

        self.assertTrue(spoken)
        self.assertGreaterEqual(len(provider.conn.websocket.sent), 1)
        tts_message = json.loads(provider.conn.websocket.sent[0])
        self.assertEqual(tts_message["type"], "tts")
        self.assertEqual(tts_message["state"], "sentence_start")
        self.assertEqual(tts_message["text"], "Welcome to the barn story.")
        self.assertEqual(tts_message["child_name"], "Bong")
        self.assertEqual(tts_message["childName"], "Bong")
        self.assertEqual(
            provider.conn.tts.stored_texts[-1][1],
            "Welcome to the barn story.",
        )
        self.assertEqual(len(provider.conn.tts.tts_text_queue.items), 3)

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
