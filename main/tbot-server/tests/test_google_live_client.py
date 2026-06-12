import asyncio
import os
import unittest
from contextlib import suppress
from types import SimpleNamespace

from core.voice.google_live.client import GoogleLiveClient


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


class GoogleLiveClientTest(unittest.TestCase):
    def test_resolve_api_key_from_environment_placeholder(self):
        os.environ["GOOGLE_API_KEY"] = " env-key\n"
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

    def test_build_connect_config_disables_server_side_interruptions_by_default(self):
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

    def test_build_connect_config_can_keep_sdk_server_interruptions(self):
        client = GoogleLiveClient(
            {
                "enable_audio_output": True,
                "disable_server_side_interruptions": False,
            },
            _DummyLogger(),
        )

        config = client._build_connect_config()

        self.assertNotIn("realtime_input_config", config)

    def test_system_instruction_uses_plain_json_even_when_sdk_types_exist(self):
        class _Types:
            class Part:
                def __init__(self, *, text):
                    self.text = text

            class Content:
                def __init__(self, *, role, parts):
                    self.role = role
                    self.parts = parts

        client = GoogleLiveClient(
            {"system_prompt": "Bạn là TBOT. Trả lời ngắn bằng tiếng Việt."},
            _DummyLogger(),
        )
        client._types = _Types

        config = client._build_connect_config()

        self.assertEqual(
            config["system_instruction"],
            {
                "parts": [
                    {"text": "Bạn là TBOT. Trả lời ngắn bằng tiếng Việt."},
                ],
                "role": "user",
            },
        )
        self.assertIsInstance(config["system_instruction"], dict)
        self.assertIsInstance(config["system_instruction"]["parts"][0], dict)

class GoogleLiveClientAsyncTest(unittest.IsolatedAsyncioTestCase):
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
