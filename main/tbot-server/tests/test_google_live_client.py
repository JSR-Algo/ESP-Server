import asyncio
import builtins
import importlib.util
import os
import unittest
from contextlib import suppress
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from config.config_loader import GOOGLE_LIVE_DEFAULTS
from core.voice.google_live.client import GoogleLiveClient
from plugins_func.functions.start_lesson import start_lesson_function_desc

ROOT = Path(__file__).resolve().parents[1]


async def _anext(async_iter):
    return await async_iter.__anext__()


def _extract_fenced_block(markdown, section, language):
    start = markdown.index(section)
    fence = f"```{language}\n"
    block_start = markdown.index(fence, start) + len(fence)
    block_end = markdown.index("\n```", block_start)
    return markdown[block_start:block_end]


def _master_prompt_section_a():
    markdown = (ROOT / "docs" / "lesson-master-prompts.md").read_text(encoding="utf-8")
    return _extract_fenced_block(
        markdown,
        "## A. CONVERSATION-MODE MASTER SYSTEM PROMPT",
        "text",
    )


def _config_prompt_text():
    import yaml

    config = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    return config["prompt"].rstrip("\n")


class _DummyLogger:
    def __init__(self):
        self.messages = []

    def bind(self, **kwargs):
        return self

    def info(self, *args, **kwargs):
        self.messages.append(("info", args, kwargs))
        return None

    def warning(self, *args, **kwargs):
        self.messages.append(("warning", args, kwargs))
        return None

    def error(self, *args, **kwargs):
        self.messages.append(("error", args, kwargs))
        return None

    def debug(self, *args, **kwargs):
        self.messages.append(("debug", args, kwargs))
        return None

class _RaisingInfoLogger(_DummyLogger):
    def info(self, *args, **kwargs):
        raise RuntimeError("log failed")

class _RaisingDebugLogger(_DummyLogger):
    def debug(self, *args, **kwargs):
        raise RuntimeError("debug failed")

class _FakeLiveContext:
    def __init__(self, enter_delay=0, session=None):
        self.enter_delay = enter_delay
        self.entered = False
        self.session = session or object()

    async def __aenter__(self):
        await asyncio.sleep(self.enter_delay)
        self.entered = True
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return None

class _FakeSession:
    def __init__(self, receive_delay=0, messages=None):
        self.receive_delay = receive_delay
        self.messages = list(messages or [])
        self.realtime_inputs = []

    async def receive(self):
        await asyncio.sleep(self.receive_delay)
        messages = self.messages
        self.messages = []
        for message in messages:
            yield message

    async def send_realtime_input(self, **kwargs):
        self.realtime_inputs.append(kwargs)

class _FakeClientContentSession(_FakeSession):
    def __init__(self, receive_delay=0, messages=None):
        super().__init__(receive_delay=receive_delay, messages=messages)
        self.client_content_inputs = []

    async def send_client_content(self, **kwargs):
        self.client_content_inputs.append(kwargs)

class _SequencedReceiveSession:
    def __init__(self, turns):
        self.turns = [list(turn) for turn in turns]
        self.receive_calls = 0

    async def receive(self):
        if self.receive_calls >= len(self.turns):
            await asyncio.Event().wait()
        messages = self.turns[self.receive_calls]
        self.receive_calls += 1
        for message in messages:
            yield message

class _TimeoutThenMessageIterator:
    def __init__(self, timeout_count, message):
        self.timeout_count = timeout_count
        self.message = message
        self.done = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.timeout_count > 0:
            self.timeout_count -= 1
            await asyncio.sleep(0.05)
            return None  # unreachable because wait_for times out first
        if self.done:
            raise StopAsyncIteration
        self.done = True
        return self.message

class _TimeoutThenMessageSession:
    def __init__(self, timeout_count, message):
        self.timeout_count = timeout_count
        self.message = message

    def receive(self):
        return _TimeoutThenMessageIterator(self.timeout_count, self.message)

class _CloseErrorIterator:
    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError as exc:
            raise RuntimeError("receive closed") from exc

class _CloseErrorSession:
    def receive(self):
        return _CloseErrorIterator()

class _FakeSdkClient:
    def __init__(self, context):
        self._context = context
        self.connect_calls = []
        self.aio = SimpleNamespace(live=SimpleNamespace(connect=self._connect))

    def _connect(self, **kwargs):
        self.connect_calls.append(kwargs)
        return self._context

class _FakeGenaiModule:
    def __init__(self, sdk_client):
        self._sdk_client = sdk_client
        self.types = None

    def Client(self, api_key):
        self.api_key = api_key
        return self._sdk_client

class _TypedPart:
    def __init__(self, text=None):
        self.text = text

class _TypedContent:
    def __init__(self, role=None, parts=None):
        self.role = role
        self.parts = parts or []

class _TypedTool:
    def __init__(self, function_declarations=None):
        self.function_declarations = function_declarations or []

class _TypedFunctionDeclaration:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

class _TypedFunctionResponse:
    calls = []

    def __init__(self, **kwargs):
        type(self).calls.append(kwargs)
        if "id" in kwargs and kwargs["id"] == "bad-id":
            raise TypeError("id not supported")
        self.__dict__.update(kwargs)

class _TypedBlob:
    def __init__(self, data, mime_type):
        self.data = data
        self.mime_type = mime_type

class _FakeTypes:
    Content = _TypedContent
    Part = _TypedPart
    Tool = _TypedTool
    FunctionDeclaration = _TypedFunctionDeclaration
    FunctionResponse = _TypedFunctionResponse
    Blob = _TypedBlob

class _RejectingTypes(_FakeTypes):
    class Content:
        def __init__(self, **_kwargs):
            raise RuntimeError("content failed")

class _NoTextSession:
    pass

class _ToolResponseSession(_FakeSession):
    def __init__(self):
        super().__init__()
        self.tool_responses = []

    async def send_tool_response(self, **kwargs):
        self.tool_responses.append(kwargs)

class _StopAfterMessagesIterator:
    def __init__(self, messages):
        self.messages = list(messages)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.messages:
            raise StopAsyncIteration
        return self.messages.pop(0)

class _StopAfterMessagesSession:
    def __init__(self, messages):
        self.messages = list(messages)

    def receive(self):
        messages = self.messages
        self.messages = []
        return _StopAfterMessagesIterator(messages)

class _TestableGoogleLiveClient(GoogleLiveClient):
    def __init__(self, config, logger, genai_module):
        super().__init__(config, logger)
        self._genai_module = genai_module

    def _import_genai_module(self):
        return self._genai_module


class _MessageWithNoisyTextProperty:
    def __init__(self, server_content):
        self.server_content = server_content

    @property
    def text(self):
        raise AssertionError("top-level text property should not be read for server_content messages")

class _ExplodingParts:
    @property
    def parts(self):
        raise RuntimeError("parts failed")


class GoogleLiveClientTest(unittest.TestCase):
    @patch.dict(os.environ, {"GOOGLE_API_KEY": " env-key\n"})
    def test_resolve_api_key_from_environment_placeholder(self):
        # Scope GOOGLE_API_KEY to this test (patch.dict restores it) so the env key does
        # not leak into later tests' load_config and pollute google_live.api_key.
        client = GoogleLiveClient({"api_key": "${GOOGLE_API_KEY}"}, _DummyLogger())

        self.assertEqual(client._resolve_api_key(), "env-key")

    def test_resolve_api_key_strips_control_whitespace(self):
        client = GoogleLiveClient({"api_key": "\n test-\tkey \r"}, _DummyLogger())

        self.assertEqual(client._resolve_api_key(), "test-key")

    def test_normalize_message_maps_transcript_audio_and_turn_completion(self):
        client = GoogleLiveClient(
            {"output_sample_rate": 24000, "send_transcript_events": True},
            _DummyLogger(),
        )
        message = SimpleNamespace(
            server_content=SimpleNamespace(
                input_transcription=SimpleNamespace(text="hello"),
                interrupted=False,
                turn_complete=True,
                model_turn=SimpleNamespace(
                    parts=[
                        SimpleNamespace(
                            inline_data=SimpleNamespace(
                                data=b"pcm-audio",
                                mime_type="audio/pcm;rate=24000",
                            )
                        )
                    ]
                ),
            )
        )

        events = client._normalize_message(message)

        self.assertEqual(
            events[0],
            {"type": "transcript", "text": "hello", "source": "user"},
        )
        self.assertEqual(events[1], {"type": "audio_start"})
        self.assertEqual(events[2]["type"], "audio_chunk")
        self.assertEqual(events[2]["audio"], b"pcm-audio")
        self.assertEqual(events[2]["mime_type"], "audio/pcm;rate=24000")
        self.assertEqual(events[3], {"type": "audio_end"})

    def test_normalize_server_content_message_does_not_read_noisy_top_level_text(self):
        client = GoogleLiveClient(
            {"output_sample_rate": 24000, "send_transcript_events": True},
            _DummyLogger(),
        )
        message = _MessageWithNoisyTextProperty(
            SimpleNamespace(
                interrupted=False,
                turn_complete=True,
                model_turn=SimpleNamespace(
                    parts=[
                        SimpleNamespace(
                            inline_data=SimpleNamespace(
                                data=b"pcm-audio",
                                mime_type="audio/pcm;rate=24000",
                            )
                        )
                    ]
                ),
            )
        )

        events = client._normalize_message(message)

        self.assertEqual(events[0], {"type": "audio_start"})
        self.assertEqual(events[1]["type"], "audio_chunk")
        self.assertEqual(events[2], {"type": "audio_end"})

    def test_build_connect_config_enables_transcriptions_and_optional_voice(self):
        client = GoogleLiveClient(
            {
                "enable_audio_output": True,
                "send_transcript_events": True,
                "native_voice": True,
                "voice_name": "Kore",
                "language_code": "vi-VN",
            },
            _DummyLogger(),
        )

        config = client._build_connect_config()

        self.assertEqual(config["response_modalities"], ["AUDIO"])
        self.assertEqual(config["input_audio_transcription"], {})
        self.assertEqual(config["output_audio_transcription"], {})
        self.assertEqual(
            config["speech_config"],
            {
                "language_code": "vi-VN",
                "voice_config": {
                    "prebuilt_voice_config": {"voice_name": "Kore"},
                },
            },
        )

    def test_build_connect_config_omits_sdk_forbidden_safety_settings(self):
        client = GoogleLiveClient(
            {
                "enable_audio_output": True,
                "send_transcript_events": True,
                "system_prompt": "You are a friendly English tutor.",
            },
            _DummyLogger(),
        )

        config = client._build_connect_config()

        self.assertNotIn("safety_settings", config)
        self.assertIn("<child_safety>", config["system_instruction"]["parts"][0]["text"])

        from google.genai import types

        types.LiveConnectConfig(**config)

    def test_build_connect_config_injects_child_safety_into_system_instruction(self):
        client = GoogleLiveClient(
            {"system_prompt": "You are a friendly English tutor."},
            _DummyLogger(),
        )

        config = client._build_connect_config()
        text = config["system_instruction"]["parts"][0]["text"]

        self.assertIn("<child_safety>", text)
        self.assertIn("Vietnamese child", text)
        self.assertIn("luyen tieng Anh", text)
        self.assertIn("You are a friendly English tutor.", text)

    def test_config_prompt_matches_lesson_master_prompt_without_inline_child_safety(self):
        prompt = _config_prompt_text()

        self.assertEqual(prompt, _master_prompt_section_a())
        self.assertNotIn("<child_safety>", prompt)

    def test_build_connect_config_attaches_prompt_and_admitted_start_lesson_tool(self):
        prompt = "Lesson system prompt."
        client = GoogleLiveClient(
            {"prompt": prompt, "functions": [start_lesson_function_desc]},
            _DummyLogger(),
        )

        config = client._build_connect_config()
        text = config["system_instruction"]["parts"][0]["text"]
        declarations = config["tools"][0]["function_declarations"]

        self.assertIn("<child_safety>", text)
        self.assertIn(prompt, text)
        self.assertTrue(text.endswith(prompt))
        self.assertIn("start_lesson", [declaration["name"] for declaration in declarations])

    def test_build_connect_config_allows_language_without_voice_name(self):
        client = GoogleLiveClient(
            {
                "enable_audio_output": True,
                "send_transcript_events": True,
                "native_voice": True,
                "language_code": "vi-VN",
            },
            _DummyLogger(),
        )

        config = client._build_connect_config()

        self.assertEqual(config["speech_config"], {"language_code": "vi-VN"})

    def test_build_connect_config_uses_text_only_when_audio_output_disabled(self):
        client = GoogleLiveClient(
            {
                "enable_audio_output": False,
                "send_transcript_events": True,
            },
            _DummyLogger(),
        )

        config = client._build_connect_config()

        self.assertEqual(config["response_modalities"], ["TEXT"])
        self.assertEqual(config["input_audio_transcription"], {})
        self.assertEqual(config["output_audio_transcription"], {})

    def test_build_connect_config_enables_output_transcription_for_llm_state_events(self):
        client = GoogleLiveClient(
            {
                "enable_audio_output": True,
                "send_transcript_events": False,
                "send_llm_state_events": True,
            },
            _DummyLogger(),
        )

        config = client._build_connect_config()

        self.assertEqual(config["response_modalities"], ["AUDIO"])
        self.assertNotIn("input_audio_transcription", config)
        self.assertEqual(config["output_audio_transcription"], {})

    def test_build_connect_config_uses_text_when_only_llm_state_events_requested(self):
        client = GoogleLiveClient(
            {
                "enable_audio_output": False,
                "send_transcript_events": False,
                "send_llm_state_events": True,
            },
            _DummyLogger(),
        )

        config = client._build_connect_config()

        self.assertEqual(config["response_modalities"], ["TEXT"])
        self.assertNotIn("input_audio_transcription", config)
        self.assertEqual(config["output_audio_transcription"], {})

    def test_build_connect_config_skips_transcriptions_when_disabled(self):
        client = GoogleLiveClient(
            {
                "enable_audio_output": True,
                "send_transcript_events": False,
                "voice_name": "",
            },
            _DummyLogger(),
        )

        config = client._build_connect_config()

        self.assertEqual(config["response_modalities"], ["AUDIO"])
        self.assertNotIn("input_audio_transcription", config)
        self.assertNotIn("output_audio_transcription", config)
        self.assertNotIn("speech_config", config)

    def test_build_connect_config_uses_start_activity_interrupts_by_default(self):
        client = GoogleLiveClient(
            {
                "enable_audio_output": True,
                "send_transcript_events": True,
            },
            _DummyLogger(),
        )

        config = client._build_connect_config()

        self.assertEqual(
            config["realtime_input_config"],
            {
                "activity_handling": "NO_INTERRUPTION",
                "turn_coverage": "TURN_INCLUDES_ALL_INPUT",
            },
        )

    def test_build_connect_config_with_production_defaults_sets_live_session_policy(self):
        client = GoogleLiveClient(GOOGLE_LIVE_DEFAULTS, _DummyLogger())

        config = client._build_connect_config()

        self.assertEqual(config["response_modalities"], ["AUDIO"])
        self.assertEqual(
            config["realtime_input_config"],
            {
                "activity_handling": "START_OF_ACTIVITY_INTERRUPTS",
                "turn_coverage": "TURN_INCLUDES_ALL_INPUT",
            },
        )
        self.assertNotIn("session_resumption", config)
        self.assertEqual(
            config["context_window_compression"],
            {"trigger_tokens": 24000, "sliding_window": {"target_tokens": 12000}},
        )

    def test_build_connect_config_uses_no_interruption_when_server_interruptions_are_disabled(self):
        client = GoogleLiveClient(
            {
                "enable_audio_output": True,
                "disable_server_side_interruptions": True,
                "activity_handling": "NO_INTERRUPTION",
            },
            _DummyLogger(),
        )

        config = client._build_connect_config()

        self.assertEqual(config["response_modalities"], ["AUDIO"])
        self.assertEqual(
            config["realtime_input_config"].get("activity_handling"),
            "NO_INTERRUPTION",
        )

    def test_build_connect_config_enables_resumption_and_context_compression(self):
        client = GoogleLiveClient(
            {
                "enable_audio_output": True,
                "session_resumption_enabled": True,
                "session_resumption_handle": "resume-handle-1",
                "context_window_compression_enabled": True,
                "context_window_trigger_tokens": 24000,
                "context_window_target_tokens": 12000,
            },
            _DummyLogger(),
        )

        config = client._build_connect_config()

        self.assertEqual(
            config["session_resumption"],
            {"handle": "resume-handle-1"},
        )
        self.assertEqual(
            config["context_window_compression"],
            {"trigger_tokens": 24000, "sliding_window": {"target_tokens": 12000}},
        )

    def test_normalize_message_yields_session_resumption_update(self):
        client = GoogleLiveClient({"child_safety": {"enabled": False}}, _DummyLogger())
        message = SimpleNamespace(
            session_resumption_update=SimpleNamespace(
                resumable=True,
                new_handle="resume-handle-2",
            ),
            server_content=None,
        )

        events = client._normalize_message(message)

        self.assertIn(
            {
                "type": "session_resumption_update",
                "handle": "resume-handle-2",
                "resumable": True,
            },
            events,
        )

    def test_build_connect_config_explicitly_sets_start_activity_interrupts(self):
        client = GoogleLiveClient(
            {
                "enable_audio_output": True,
                "disable_server_side_interruptions": False,
                "activity_handling": "START_OF_ACTIVITY_INTERRUPTS",
            },
            _DummyLogger(),
        )

        config = client._build_connect_config()

        self.assertEqual(
            config["realtime_input_config"]["activity_handling"],
            "START_OF_ACTIVITY_INTERRUPTS",
        )

    def test_sdk_import_fallback_reports_missing_google_genai(self):
        original_import = builtins.__import__

        def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "google" and "genai" in (fromlist or ()):  # module eager import path
                raise ImportError("missing google genai")
            return original_import(name, globals, locals, fromlist, level)

        module_path = ROOT / "core" / "voice" / "google_live" / "client.py"
        spec = importlib.util.spec_from_file_location("tests.google_live_client_no_sdk", module_path)
        module = importlib.util.module_from_spec(spec)
        try:
            builtins.__import__ = fake_import
            spec.loader.exec_module(module)
            self.assertIsNone(module._GENAI_MODULE_EAGER)
            with self.assertRaisesRegex(RuntimeError, "google-genai package"):
                module.GoogleLiveClient({}, _DummyLogger())._import_genai_module()
        finally:
            builtins.__import__ = original_import

    def test_build_config_and_helpers_cover_typed_and_defensive_edges(self):
        logger = _DummyLogger()
        client = GoogleLiveClient(
            {
                "system_prompt": "hello",
                "functions": [
                    {"function": {"name": "ok", "description": "desc", "parameters": {"title": "drop", "properties": {"x": {"default": 1, "type": "string"}}}}},
                    {"function": {"name": "", "parameters": {}}},
                    {"not_function": True},
                    "bad",
                ],
                "disable_server_side_interruptions": True,
                "activity_handling": "NO_INTERRUPTION",
                "start_of_speech_sensitivity": "HIGH",
                "prefix_padding_ms": "bad",
                "silence_duration_ms": 250,
                "session_resumption_enabled": False,
                "context_window_compression_enabled": False,
                "native_voice": False,
            },
            logger,
        )
        client._types = _FakeTypes

        config = client._build_connect_config()

        self.assertNotIn("speech_config", config)
        self.assertNotIn("session_resumption", config)
        self.assertNotIn("context_window_compression", config)
        self.assertIsInstance(config["system_instruction"], _TypedContent)
        self.assertEqual(config["system_instruction"].role, "user")
        self.assertIsInstance(config["tools"][0], _TypedTool)
        declaration = config["tools"][0].function_declarations[0]
        self.assertIsInstance(declaration, _TypedFunctionDeclaration)
        self.assertEqual(declaration.parameters, {"properties": {"x": {"type": "string"}}})
        self.assertEqual(
            config["realtime_input_config"],
            {
                "activity_handling": "NO_INTERRUPTION",
                "turn_coverage": "TURN_INCLUDES_ALL_INPUT",
                "automatic_activity_detection": {
                    "start_of_speech_sensitivity": "HIGH",
                    "silence_duration_ms": 250,
                },
            },
        )
        self.assertEqual(client._summarize_tool_names(config["tools"]), ["ok"])
        self.assertEqual(client._system_instruction_length(config["system_instruction"]), len(config["system_instruction"].parts[0].text))
        self.assertEqual(client._normalize_positive_int("bad"), None)
        self.assertEqual(client._normalize_timeout("bad"), None)
        self.assertEqual(client._normalize_timeout(0), None)

    def test_builder_fallbacks_and_event_normalizers_cover_mapping_edges(self):
        client = GoogleLiveClient({"child_safety": {"enabled": False}}, _DummyLogger())
        client._types = _RejectingTypes
        self.assertIsInstance(client._build_system_instruction(), type(None))
        client.config["system_prompt"] = ""
        self.assertIsNone(client._build_system_instruction())

        client.config["system_prompt"] = "typed fallback"
        instruction = client._build_system_instruction()
        self.assertIsInstance(instruction, dict)
        self.assertEqual(client._system_instruction_length(object()), 0)
        self.assertEqual(client._system_instruction_length({"parts": [object()]}), 0)

        client._types = _FakeTypes
        self.assertIsInstance(client._build_blob(b"ab", "audio/pcm"), _TypedBlob)
        self.assertIsInstance(client._build_text_turn("hello"), _TypedContent)

        with self.assertRaises(TypeError):
            client._build_function_response("bad")
        with self.assertRaises(ValueError):
            client._build_function_response({"response": {}})
        _TypedFunctionResponse.calls = []
        response = client._build_function_response({"id": "bad-id", "name": "tool", "response": None})
        self.assertIsInstance(response, _TypedFunctionResponse)
        self.assertEqual(response.response, {"result": ""})
        self.assertEqual(_TypedFunctionResponse.calls[0]["id"], "bad-id")
        self.assertNotIn("id", _TypedFunctionResponse.calls[-1])

        self.assertEqual(client._message_has_audio_chunk({"server_content": {"model_turn": {"parts": [{"inline_data": None}, {"inline_data": {"data": b"x"}}]}}}), True)
        self.assertEqual(client._message_has_audio_chunk({"server_content": {"model_turn": {"parts": [{"inline_data": {}}]}}}), False)
        events = client._normalize_message(
            {
                "go_away": {"time_left": {"seconds": "2", "nanos": 500_000_000}},
                "session_resumption_update": {"handle": "h1", "resumable": False},
                "tool_call": {"function_calls": [{"id": "1", "name": "do", "args": {"x": 1}}, {"id": "2"}]},
                "tool_call_cancellation": {"ids": ["1", None, 2]},
            }
        )
        self.assertIn({"type": "session_expiring", "time_left_ms": 2500}, events)
        self.assertIn({"type": "session_resumption_update", "handle": "h1", "resumable": False}, events)
        self.assertIn({"type": "tool_call", "calls": [{"id": "1", "name": "do", "args": {"x": 1}}]}, events)
        self.assertIn({"type": "tool_call_cancellation", "ids": ["1", "2"]}, events)
        self.assertIsNone(client._normalize_tool_call({"function_calls": [{"id": "missing-name"}]}))
        self.assertIsNone(client._normalize_tool_call_cancellation({"ids": [None]}))
        self.assertEqual(client._extract_time_left_ms({"time_left": 1.25}), 1250)
        self.assertEqual(client._extract_time_left_ms({"time_left": "3.5s"}), 3500)
        self.assertIsNone(client._extract_time_left_ms({"time_left": "bad"}))
        self.assertIsNone(client._extract_time_left_ms({"nanos": 1}))
        self.assertIsNone(client._extract_time_left_ms({"seconds": "bad"}))
        self.assertEqual(client._normalize_server_content({"interrupted": True}), [{"type": "interruption"}])
        self.assertEqual(client._extract_field({"x": 1}, "x"), 1)

    def test_untyped_and_exception_edges_cover_defensive_branches(self):
        import core.voice.google_live.client as client_module

        logger = _DummyLogger()
        client = GoogleLiveClient(
            {
                "enable_audio_output": False,
                "send_transcript_events": False,
                "send_llm_state_events": False,
                "disable_server_side_interruptions": False,
                "session_resumption_enabled": False,
                "context_window_compression_enabled": False,
                "functions": [{"function": {"name": "plain", "parameters": {}}}],
            },
            logger,
        )

        config = client._build_connect_config()
        self.assertEqual(config["response_modalities"], ["AUDIO"])
        self.assertNotIn("realtime_input_config", config)
        self.assertEqual(config["tools"], [{"function_declarations": [{"name": "plain", "description": ""}]}])
        self.assertEqual(client._build_blob(b"x", "audio/test"), {"data": b"x", "mime_type": "audio/test"})
        self.assertEqual(client._build_text_turn("hello"), {"role": "user", "parts": [{"text": "hello"}]})
        self.assertEqual(
            client._build_function_response({"id": "call-1", "name": "plain", "response": {"ok": True}}),
            {"id": "call-1", "name": "plain", "response": {"ok": True}},
        )
        self.assertEqual(client._system_instruction_length(_ExplodingParts()), -1)
        self.assertIsNone(client._extract_time_left_ms({"time_left": {"seconds": None}}))
        self.assertIsNone(client._extract_time_left_ms({"time_left": {"seconds": "bad", "nanos": object()}}))

        logging_client = GoogleLiveClient(
            {"functions": [{"function": {"name": "logged", "parameters": {}}}]},
            _RaisingInfoLogger(),
        )
        self.assertIn("tools", logging_client._build_connect_config())

        debug_client = GoogleLiveClient({}, _RaisingDebugLogger())
        debug_client._log_recv_timer_reset(
            {"server_content": {"model_turn": {"parts": [{"inline_data": {"data": b"x"}}]}}}
        )

        original_safety = client_module.ensure_child_safety_block
        original_eager = client_module._GENAI_MODULE_EAGER
        original_import = client_module._import_google_genai_with_known_warning_filters
        try:
            client_module.ensure_child_safety_block = lambda _prompt: ""
            self.assertIsNone(GoogleLiveClient({"system_prompt": "prompt"}, logger)._build_system_instruction())

            eager_module = object()
            client_module._GENAI_MODULE_EAGER = eager_module
            self.assertIs(GoogleLiveClient({}, logger)._import_genai_module(), eager_module)

            fallback_module = object()
            client_module._GENAI_MODULE_EAGER = None
            client_module._import_google_genai_with_known_warning_filters = lambda: fallback_module
            self.assertIs(GoogleLiveClient({}, logger)._import_genai_module(), fallback_module)
        finally:
            client_module.ensure_child_safety_block = original_safety
            client_module._GENAI_MODULE_EAGER = original_eager
            client_module._import_google_genai_with_known_warning_filters = original_import

    def test_factory_creates_google_live_client(self):
        from core.voice.google_live.client import GoogleLiveClientFactory

        self.assertIsInstance(GoogleLiveClientFactory.create({}, _DummyLogger()), GoogleLiveClient)

class GoogleLiveClientAsyncTest(unittest.IsolatedAsyncioTestCase):
    async def test_connect_validation_and_close_paths(self):
        logger = _DummyLogger()
        genai_module = _FakeGenaiModule(_FakeSdkClient(_FakeLiveContext()))

        with self.assertRaisesRegex(RuntimeError, "API key"):
            await _TestableGoogleLiveClient({"model": "m"}, logger, genai_module).connect()
        with self.assertRaisesRegex(RuntimeError, "model"):
            await _TestableGoogleLiveClient({"api_key": "k"}, logger, genai_module).connect()

        context = _FakeLiveContext(session=_FakeSession())
        client = _TestableGoogleLiveClient(
            {"api_key": "k", "model": "m"},
            logger,
            _FakeGenaiModule(_FakeSdkClient(context)),
        )
        await client.connect()
        self.assertTrue(client.connected)
        await client.close()
        self.assertFalse(client.connected)
        self.assertIsNone(client._session)

    async def test_send_audio_text_tool_and_interrupt_edges(self):
        logger = _DummyLogger()

        disconnected = GoogleLiveClient({}, logger)
        with self.assertRaisesRegex(RuntimeError, "not connected"):
            await disconnected.send_audio(b"ab")
        with self.assertRaisesRegex(RuntimeError, "not connected"):
            await disconnected.end_audio_stream()
        with self.assertRaisesRegex(RuntimeError, "not connected"):
            await disconnected.send_text("hello")
        with self.assertRaisesRegex(RuntimeError, "not connected"):
            await disconnected.send_tool_response([{"name": "x"}])

        session = _ToolResponseSession()
        context = _FakeLiveContext(session=session)
        client = _TestableGoogleLiveClient(
            {"api_key": "k", "model": "m", "input_sample_rate": 8000},
            logger,
            _FakeGenaiModule(_FakeSdkClient(context)),
        )
        client._types = _FakeTypes
        await client.connect()
        client._types = _FakeTypes

        self.assertIsNone(await client.send_audio(b""))
        await client.send_audio(b"ab")
        self.assertIsInstance(session.realtime_inputs[-1]["audio"], _TypedBlob)
        self.assertEqual(session.realtime_inputs[-1]["audio"].mime_type, "audio/pcm;rate=8000")
        self.assertIsNone(await client.send_text(""))
        await client.send_tool_response([{"id": "ok", "name": "tool", "response": "done"}])
        self.assertEqual(session.tool_responses[-1]["function_responses"][0].response, {"result": "done"})
        await client.interrupt()
        await client.close()
        self.assertIsNone(await client.interrupt())

        no_text = _TestableGoogleLiveClient(
            {"api_key": "k", "model": "m"},
            logger,
            _FakeGenaiModule(_FakeSdkClient(_FakeLiveContext(session=_NoTextSession()))),
        )
        await no_text.connect()
        with self.assertRaisesRegex(RuntimeError, "text input"):
            await no_text.send_text("hello")
        with self.assertRaisesRegex(RuntimeError, "tool responses"):
            await no_text.send_tool_response([{"name": "tool"}])

    async def test_tool_interrupt_and_stream_end_edges(self):
        logger = _DummyLogger()
        session = _FakeClientContentSession()
        client = _TestableGoogleLiveClient(
            {"api_key": "k", "model": "m"},
            logger,
            _FakeGenaiModule(_FakeSdkClient(_FakeLiveContext(session=session))),
        )
        await client.connect()

        self.assertIsNone(await client.send_tool_response([]))
        await client.interrupt()
        self.assertEqual(session.client_content_inputs[-1], {"turns": [], "turn_complete": False})

        audio_message = SimpleNamespace(
            server_content=SimpleNamespace(
                interrupted=False,
                turn_complete=False,
                model_turn=SimpleNamespace(
                    parts=[
                        SimpleNamespace(
                            inline_data=SimpleNamespace(
                                data=b"pcm-audio",
                                mime_type="audio/pcm;rate=24000",
                            )
                        )
                    ]
                ),
            )
        )
        stream_client = _TestableGoogleLiveClient(
            {"api_key": "k", "model": "m"},
            logger,
            _FakeGenaiModule(
                _FakeSdkClient(_FakeLiveContext(session=_StopAfterMessagesSession([audio_message])))
            ),
        )
        await stream_client.connect()

        events = [event async for event in stream_client.receive_events()]

        self.assertEqual([event["type"] for event in events], ["audio_start", "audio_chunk", "audio_end"])

        open_turn_client = _TestableGoogleLiveClient(
            {"api_key": "k", "model": "m"},
            logger,
            _FakeGenaiModule(_FakeSdkClient(_FakeLiveContext(session=_StopAfterMessagesSession([])))),
        )
        await open_turn_client.connect()
        open_turn_client._audio_started = True
        open_turn_client._audio_chunk_count = 1
        open_turn_client._audio_byte_count = 3

        open_turn_events = open_turn_client.receive_events()
        self.assertEqual(await _anext(open_turn_events), {"type": "audio_end"})
        with self.assertRaises(StopAsyncIteration):
            await _anext(open_turn_events)

    async def test_receive_events_disconnected_empty_and_cancel_paths(self):
        logger = _DummyLogger()
        client = GoogleLiveClient({}, logger)
        events = [event async for event in client.receive_events()]
        self.assertEqual(events, [])

        empty_session = _SequencedReceiveSession(turns=[[]])
        empty_client = _TestableGoogleLiveClient(
            {"api_key": "k", "model": "m"},
            logger,
            _FakeGenaiModule(_FakeSdkClient(_FakeLiveContext(session=empty_session))),
        )
        await empty_client.connect()
        self.assertEqual([event async for event in empty_client.receive_events()], [])

        class _NeverIterator:
            def __aiter__(self):
                return self

            async def __anext__(self):
                await asyncio.Event().wait()

        class _NeverSession:
            def receive(self):
                return _NeverIterator()

        cancel_client = _TestableGoogleLiveClient(
            {"api_key": "k", "model": "m", "recv_timeout_sec": 10},
            logger,
            _FakeGenaiModule(_FakeSdkClient(_FakeLiveContext(session=_NeverSession()))),
        )
        await cancel_client.connect()

        async def drain():
            async for _event in cancel_client.receive_events():
                pass

        task = asyncio.create_task(drain())
        await asyncio.sleep(0)
        await cancel_client.close()
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
    async def test_connect_honors_configured_timeout(self):
        logger = _DummyLogger()
        context = _FakeLiveContext(enter_delay=0.05)
        sdk_client = _FakeSdkClient(context)
        genai_module = _FakeGenaiModule(sdk_client)
        client = _TestableGoogleLiveClient(
            {
                "api_key": "test-key",
                "model": "gemini-live-test",
                "connect_timeout_sec": 0.01,
            },
            logger,
            genai_module,
        )

        with self.assertRaises(RuntimeError):
            await client.connect()

        self.assertFalse(client.connected)
        self.assertFalse(context.entered)

    async def test_receive_events_ignores_idle_timeouts_and_keeps_waiting(self):
        logger = _DummyLogger()
        session = _TimeoutThenMessageSession(
            timeout_count=1,
            message=SimpleNamespace(text="hello"),
        )
        context = _FakeLiveContext(session=session)
        sdk_client = _FakeSdkClient(context)
        genai_module = _FakeGenaiModule(sdk_client)
        client = _TestableGoogleLiveClient(
            {
                "api_key": "test-key",
                "model": "gemini-live-test",
                "recv_timeout_sec": 0.01,
            },
            logger,
            genai_module,
        )

        await client.connect()

        events = []
        async for event in client.receive_events():
            events.append(event)
            if events == [{"type": "transcript", "text": "hello", "source": "model"}]:
                break

        self.assertEqual(
            events,
            [{"type": "transcript", "text": "hello", "source": "model"}],
        )
        self.assertTrue(
            any(
                level == "warning" and args and "receive timed out" in args[0]
                for level, args, _kwargs in logger.messages
            )
        )

    async def test_receive_events_flushes_audio_when_stream_ends_without_turn_complete(self):
        logger = _DummyLogger()
        session = _FakeSession(
            messages=[
                SimpleNamespace(
                    server_content=SimpleNamespace(
                        interrupted=False,
                        turn_complete=False,
                        model_turn=SimpleNamespace(
                            parts=[
                                SimpleNamespace(
                                    inline_data=SimpleNamespace(
                                        data=b"pcm-audio",
                                        mime_type="audio/pcm;rate=24000",
                                    )
                                )
                            ]
                        ),
                    )
                )
            ]
        )
        context = _FakeLiveContext(session=session)
        sdk_client = _FakeSdkClient(context)
        genai_module = _FakeGenaiModule(sdk_client)
        client = _TestableGoogleLiveClient(
            {
                "api_key": "test-key",
                "model": "gemini-live-test",
            },
            logger,
            genai_module,
        )

        await client.connect()

        events = []
        async for event in client.receive_events():
            events.append(event)
            if len(events) == 3:
                break

        self.assertEqual(events[0], {"type": "audio_start"})
        self.assertEqual(events[1]["type"], "audio_chunk")
        self.assertEqual(events[2], {"type": "audio_end"})

    async def test_receive_events_keeps_session_open_across_turn_boundaries(self):
        logger = _DummyLogger()
        session = _SequencedReceiveSession(
            turns=[
                [SimpleNamespace(text="first")],
                [SimpleNamespace(text="second")],
            ]
        )
        context = _FakeLiveContext(session=session)
        sdk_client = _FakeSdkClient(context)
        genai_module = _FakeGenaiModule(sdk_client)
        client = _TestableGoogleLiveClient(
            {
                "api_key": "test-key",
                "model": "gemini-live-test",
            },
            logger,
            genai_module,
        )

        await client.connect()

        events = []
        async for event in client.receive_events():
            events.append(event)
            if len(events) == 2:
                break

        self.assertEqual(
            events,
            [
                {"type": "transcript", "text": "first", "source": "model"},
                {"type": "transcript", "text": "second", "source": "model"},
            ],
        )
        self.assertEqual(session.receive_calls, 2)

    async def test_receive_events_retrieves_pending_receive_exception_on_close(self):
        logger = _DummyLogger()
        session = _CloseErrorSession()
        context = _FakeLiveContext(session=session)
        sdk_client = _FakeSdkClient(context)
        genai_module = _FakeGenaiModule(sdk_client)
        client = _TestableGoogleLiveClient(
            {
                "api_key": "test-key",
                "model": "gemini-live-test",
            },
            logger,
            genai_module,
        )
        loop = asyncio.get_running_loop()
        loop_errors = []
        previous_handler = loop.get_exception_handler()
        loop.set_exception_handler(lambda _loop, context: loop_errors.append(context))

        async def drain_events():
            async for _event in client.receive_events():
                pass

        try:
            await client.connect()
            receive_task = asyncio.create_task(drain_events())
            await asyncio.sleep(0)
            await client.close()
            receive_task.cancel()
            with suppress(asyncio.CancelledError):
                await receive_task
            await asyncio.sleep(0)
        finally:
            loop.set_exception_handler(previous_handler)

        self.assertEqual(loop_errors, [])

    async def test_end_audio_stream_sends_audio_stream_end_signal(self):
        logger = _DummyLogger()
        session = _FakeSession()
        context = _FakeLiveContext(session=session)
        sdk_client = _FakeSdkClient(context)
        genai_module = _FakeGenaiModule(sdk_client)
        client = _TestableGoogleLiveClient(
            {
                "api_key": "test-key",
                "model": "gemini-live-test",
            },
            logger,
            genai_module,
        )

        await client.connect()
        await client.end_audio_stream()

        self.assertEqual(session.realtime_inputs, [{"audio_stream_end": True}])

    async def test_send_text_prefers_client_content_turn_completion(self):
        logger = _DummyLogger()
        session = _FakeClientContentSession()
        context = _FakeLiveContext(session=session)
        sdk_client = _FakeSdkClient(context)
        genai_module = _FakeGenaiModule(sdk_client)
        client = _TestableGoogleLiveClient(
            {
                "api_key": "test-key",
                "model": "gemini-live-test",
            },
            logger,
            genai_module,
        )

        await client.connect()
        await client.send_text("tiếp tục")

        self.assertEqual(session.realtime_inputs, [])
        self.assertEqual(
            session.client_content_inputs,
            [
                {
                    "turns": {"role": "user", "parts": [{"text": "tiếp tục"}]},
                    "turn_complete": True,
                }
            ],
        )

    async def test_send_text_falls_back_to_realtime_text_input(self):
        logger = _DummyLogger()
        session = _FakeSession()
        context = _FakeLiveContext(session=session)
        sdk_client = _FakeSdkClient(context)
        genai_module = _FakeGenaiModule(sdk_client)
        client = _TestableGoogleLiveClient(
            {
                "api_key": "test-key",
                "model": "gemini-live-test",
            },
            logger,
            genai_module,
        )

        await client.connect()
        await client.send_text("tiếp tục")

        self.assertEqual(session.realtime_inputs, [{"text": "tiếp tục"}])

    async def test_send_audio_is_noop_when_audio_input_disabled(self):
        logger = _DummyLogger()
        session = _FakeSession()
        context = _FakeLiveContext(session=session)
        sdk_client = _FakeSdkClient(context)
        genai_module = _FakeGenaiModule(sdk_client)
        client = _TestableGoogleLiveClient(
            {
                "api_key": "test-key",
                "model": "gemini-live-test",
                "enable_audio_input": False,
            },
            logger,
            genai_module,
        )

        await client.connect()
        await client.send_audio(b"pcm")
        await client.end_audio_stream()

        self.assertEqual(session.realtime_inputs, [])

    async def test_send_audio_skips_corrupt_odd_width_pcm(self):
        logger = _DummyLogger()
        session = _FakeSession()
        context = _FakeLiveContext(session=session)
        sdk_client = _FakeSdkClient(context)
        genai_module = _FakeGenaiModule(sdk_client)
        client = _TestableGoogleLiveClient(
            {
                "api_key": "test-key",
                "model": "gemini-live-test",
                "enable_audio_input": True,
            },
            logger,
            genai_module,
        )

        await client.connect()
        await client.send_audio(b"\x01")

        self.assertEqual(session.realtime_inputs, [])
        self.assertTrue(
            any(
                level == "warning" and args and "corrupt pcm16 input" in args[0]
                for level, args, _kwargs in logger.messages
            )
        )
