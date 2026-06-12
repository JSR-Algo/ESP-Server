import asyncio
import unittest
from types import SimpleNamespace

from core.voice.google_live.client import GoogleLiveClient
from core.voice.session_provider.google_live import GoogleLiveProvider
from plugins_func.register import Action, ActionResponse
# Importing change_volume registers it in all_function_registry so
# always-included live tools can be resolved during these tests.
import plugins_func.functions.change_volume  # noqa: F401


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


class _RecordingClient:
    def __init__(self):
        self.sent_responses = []
        self.connected = True

    async def send_tool_response(self, responses):
        self.sent_responses.append(responses)


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
        self.assertEqual(response["response"], {"error": "rate limit"})

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
        self.assertEqual(response["response"], {"error": "Tool handler unavailable"})

    async def test_tool_call_cancellation_clears_pending_set(self):
        provider, _handler = self._make_provider(
            ActionResponse(action=Action.NONE, response=None),
        )
        provider._pending_tool_calls.update({"a", "b"})

        await provider._handle_tool_call_cancellation_event(
            {"type": "tool_call_cancellation", "ids": ["a"]}
        )

        self.assertEqual(provider._pending_tool_calls, {"b"})

    async def test_get_live_config_injects_functions_from_func_handler(self):
        handler = _FakeFuncHandler(
            ActionResponse(action=Action.NONE, response=None),
            functions=[_GET_WEATHER_SPEC],
        )
        conn = _ProviderConn(func_handler=handler)
        provider = GoogleLiveProvider(conn)

        config = provider._get_live_config_with_functions()

        names = [t["function"]["name"] for t in config["functions"]]
        self.assertIn("get_weather", names)
        # change_volume is always-included for live mode so device speaker
        # control works even when the API-supplied tool list omits it.
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
        # Override path replaces (rather than extends) func_handler list.
        self.assertNotIn("get_weather", names)

    async def test_intent_function_call_override_keeps_robot_motion_tools_live(self):
        import plugins_func.functions.raise_left_arm  # noqa: F401
        import plugins_func.functions.robot_arm_actions  # noqa: F401
        import plugins_func.functions.turn_head  # noqa: F401

        conn = _ProviderConn(func_handler=None)
        conn.config["selected_module"] = {"Intent": "function_call"}
        conn.config["Intent"] = {
            "function_call": {
                # Mirrors production: a narrow whitelist existed before the
                # head/percent tools were added.
                "functions": ["raise_left_arm"],
            }
        }
        provider = GoogleLiveProvider(conn)

        config = provider._get_live_config_with_functions()

        names = [t["function"]["name"] for t in config["functions"]]
        self.assertIn("raise_left_arm", names)
        self.assertIn("turn_head_left", names)
        self.assertIn("turn_head_right", names)
        self.assertIn("center_head", names)
        self.assertIn("set_head_angle", names)
        self.assertIn("set_head_percent", names)
        self.assertIn("turn_head_left_then_right_max", names)
        self.assertIn("set_left_arm_percent", names)
        self.assertIn("set_right_arm_percent", names)
        self.assertIn("set_both_arms_percent", names)


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
