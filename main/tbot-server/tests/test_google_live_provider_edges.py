import asyncio
import json
import time
import unittest
from collections import deque
from types import SimpleNamespace
from unittest.mock import patch

from plugins_func.register import Action
from core.voice.live_admission import AdmissionDecision, AdmissionReason
from core.voice.session_orchestrator import SessionMode
import core.voice.session_provider.google_live as google_live_module
from core.voice.session_provider.google_live import GoogleLiveProvider


class _Logger:
    def __init__(self):
        self.messages = []

    def bind(self, **_kwargs):
        return self

    def info(self, *args, **kwargs):
        self.messages.append(("info", args, kwargs))

    def warning(self, *args, **kwargs):
        self.messages.append(("warning", args, kwargs))

    def error(self, *args, **kwargs):
        self.messages.append(("error", args, kwargs))


class _WebSocket:
    def __init__(self, fail=False):
        self.fail = fail
        self.sent = []

    async def send(self, payload):
        if self.fail:
            raise RuntimeError("send failed")
        self.sent.append(payload)


class _Consent:
    def __init__(self, allowed=True, fail=False):
        self.allowed = allowed
        self.fail = fail

    async def ensure_voice_allowed(self, _conn):
        if self.fail:
            raise RuntimeError("consent failed token=secret")
        return self.allowed


class _Conn:
    def __init__(self):
        self.config = {
            "voice_mode": {"type": "google_live", "fallback_to_classic_on_error": True},
            "google_live": {
                "api_key": "test-key",
                "model": "gemini-test",
                "aec_enabled": False,
                "reconnect": {"enabled": False},
            },
            "wakeup_words": ["TeeBot"],
            "prompt": "system prompt",
        }
        self.logger = _Logger()
        self.websocket = _WebSocket()
        self.sent = []
        self.session_id = "session-1"
        self.device_id = "device-1"
        self.household_id = "home-1"
        self.client_abort = False
        self.client_is_speaking = False
        self.google_live_audio_out_started_at = None
        self.google_live_session_started_at = None
        self.google_live_turn_started_at = None
        self.google_live_session_resumption_handle = None
        self.voice_consent_client = _Consent(True)
        self.voice_provider = None
        self.clear_queue_calls = 0
        self.clear_speak_calls = 0

    def clear_queues(self):
        self.clear_queue_calls += 1

    def clearSpeakStatus(self):
        self.clear_speak_calls += 1
        self.client_is_speaking = False


class _Fallback:
    def __init__(self):
        self.started = 0
        self.closed = 0
        self.interrupted = 0
        self.text = []
        self.audio = []

    async def start_session(self):
        self.started += 1

    async def close(self):
        self.closed += 1

    async def interrupt(self):
        self.interrupted += 1

    async def handle_text_message(self, message):
        self.text.append(message)
        return "fallback-text"

    async def handle_audio_bytes(self, audio):
        self.audio.append(audio)
        return "fallback-audio"


class _Client:
    def __init__(self, events=None):
        self.connected = True
        self.closed = 0
        self.end_calls = 0
        self.interrupt_calls = 0
        self.text = []
        self.tool_responses = []
        self.events = list(events or [])

    async def connect(self):
        self.connected = True

    async def close(self):
        self.closed += 1
        self.connected = False

    async def end_audio_stream(self):
        self.end_calls += 1

    async def interrupt(self):
        self.interrupt_calls += 1

    async def send_text(self, text):
        self.text.append(text)

    async def send_tool_response(self, responses):
        self.tool_responses.append(responses)

    async def receive_events(self):
        for event in self.events:
            yield event

class _SendTextFailClient(_Client):
    async def send_text(self, text):
        self.text.append(text)
        raise RuntimeError("BidiGenerateContent session not found")


class _Bridge:
    def __init__(self):
        self.events = []
        self.closed = 0
        self.stop_calls = 0
        self.allow_calls = 0
        self.forwarded = []
        self.forwarded_raw = []
        self.flush_calls = 0
        self.blocked = False
        self.rms = 1000

    async def handle_event(self, event):
        self.events.append(event)

    async def close(self):
        self.closed += 1

    async def stop_output(self):
        self.stop_calls += 1

    def allow_model_output(self):
        self.allow_calls += 1

    def current_response_id(self):
        return 0

    def is_model_output_blocked(self):
        return self.blocked

    async def forward_decoded_input_audio(self, pcm):
        self.forwarded.append(pcm)

    async def forward_input_audio(self, audio):
        self.forwarded_raw.append(audio)

    async def flush_pending_input_audio(self):
        self.flush_calls += 1
        return 0

    async def decode_input_audio_async(self, audio):
        return b"pcm:" + audio

    def decode_input_audio(self, audio):
        return b"sync:" + audio

    def input_rms(self, _pcm):
        return self.rms


class _FailingBridge(_Bridge):
    async def close(self):
        raise RuntimeError("bridge close failed")

    async def stop_output(self):
        raise RuntimeError("stop failed")


class _Store:
    def __init__(self, handle="stored-handle"):
        self.handle = handle
        self.saved = []

    async def load(self, _device_id):
        return self.handle

    async def save(self, device_id, handle):
        self.saved.append((device_id, handle))


class _Gate:
    def __init__(self, decision=None):
        self.decision = decision
        self.usage = []
        self.reconnects = []

    def admit(self, *_args):
        return self.decision

    def record_live_usage(self, device_id, household_id, elapsed):
        self.usage.append((device_id, household_id, elapsed))

    def record_reconnect(self, device_id):
        self.reconnects.append(device_id)


class _AsyncGate(_Gate):
    async def admit_async(self, *_args):
        return self.decision

    async def record_live_usage_async(self, device_id, household_id, elapsed):
        self.usage.append((device_id, household_id, elapsed))

    async def record_reconnect_async(self, device_id):
        self.reconnects.append(device_id)


class _FuncHandler:
    def __init__(self, fail=False, result=None):
        self.fail = fail
        self.calls = []
        self.result = result or SimpleNamespace(action=Action.RESPONSE, response="ok", result="ok")

    async def handle_llm_function_call(self, conn, payload):
        self.calls.append((conn, payload))
        if self.fail:
            raise RuntimeError("handler failed token=secret")
        return self.result


class GoogleLiveProviderEdgeTest(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        provider = getattr(self, "provider", None)
        if provider is not None:
            await provider.close()

    def make_provider(self, conn=None, fallback=None):
        conn = conn or _Conn()
        fallback = fallback or _Fallback()
        self.provider = GoogleLiveProvider(
            conn,
            client_factory=lambda *_args: _Client(),
            classic_provider_factory=lambda _conn: fallback,
        )
        return self.provider

    def test_session_resumption_disabled_removes_saved_handle_from_live_config(self):
        conn = _Conn()
        conn.config["google_live"]["session_resumption_enabled"] = False
        conn.google_live_session_resumption_handle = "resume-1"
        provider = self.make_provider(conn)

        cfg = provider._get_live_config_with_functions()

        self.assertFalse(cfg["session_resumption_enabled"])
        self.assertNotIn("session_resumption_handle", cfg)

    async def test_live_open_restores_lesson_mode_when_runtime_is_active(self):
        conn = _Conn()
        conn.session_mode = SessionMode.DORMANT
        conn.lesson_runtime = SimpleNamespace(state="RUNNING")
        mode_changes = []

        def _set_session_mode(mode, reason=None):
            conn.session_mode = mode
            mode_changes.append((mode, reason))

        conn._set_session_mode = _set_session_mode
        original_bridge = google_live_module.GoogleLiveAudioBridge

        class _FakeBridge(_Bridge):
            def __init__(self, *_args, **_kwargs):
                super().__init__()

        google_live_module.GoogleLiveAudioBridge = _FakeBridge
        try:
            provider = self.make_provider(conn)
            provider._ensure_required_aec_ready = lambda: None

            await provider._open_live_session()

            self.assertIn((SessionMode.LESSON, "lesson_runtime_active"), mode_changes)
            self.assertNotIn((SessionMode.CONVERSATION, "live_open"), mode_changes)
        finally:
            google_live_module.GoogleLiveAudioBridge = original_bridge

    async def test_lesson_live_text_send_failure_closes_half_open_live_resources(self):
        conn = _Conn()
        provider = self.make_provider(conn)
        provider._client = _SendTextFailClient()
        provider._bridge = _Bridge()
        provider._receive_task = object()
        closed = []

        async def _close_live_resources():
            closed.append(True)
            provider._client = None
            provider._bridge = None
            provider._receive_task = None

        provider._close_live_resources = _close_live_resources

        sent = await provider._send_live_text_ack(
            "Đây là cái kho. Tiếng Anh là barn.",
            log_label="lesson_step_prompt",
            allow_lesson_output=True,
        )

        self.assertFalse(sent)
        self.assertEqual(closed, [True])
        self.assertFalse(
            getattr(conn, "google_live_lesson_prompt_output_allowed", False)
        )

    def test_augment_prompt_injects_child_name_and_addressing_instruction(self):
        conn = _Conn()
        conn.config["child_profile"] = {"child_name": "  Bong  "}
        provider = self.make_provider(conn)

        augmented = provider._augment_prompt_with_child_name("Base role.")

        self.assertTrue(augmented.startswith("Base role."))
        self.assertIn("<child_profile>", augmented)
        self.assertIn("The child's name is Bong.", augmented)
        self.assertIn("Use the child's name naturally", augmented)

    def test_augment_prompt_accepts_camelcase_child_name_alias(self):
        conn = _Conn()
        conn.config["child_profile"] = {"childName": "Mai"}
        provider = self.make_provider(conn)

        augmented = provider._augment_prompt_with_child_name("Base role.")

        self.assertIn("The child's name is Mai.", augmented)

    def test_augment_prompt_is_noop_without_usable_child_name(self):
        provider = self.make_provider(_Conn())
        # No child_profile configured.
        self.assertEqual(provider._augment_prompt_with_child_name("Base role."), "Base role.")
        # Present but empty / wrong types must not inject a half-built block.
        for bad in ({"child_name": "   "}, {"child_name": None}, {"child_name": 123}, "not-a-dict"):
            provider.conn.config["child_profile"] = bad
            self.assertEqual(
                provider._augment_prompt_with_child_name("Base role."),
                "Base role.",
            )

    def test_live_config_system_prompt_carries_child_name(self):
        conn = _Conn()
        conn.config["child_profile"] = {"child_name": "Bong"}
        provider = self.make_provider(conn)
        provider._resolve_functions_for_live = lambda: None

        config = provider._get_live_config_with_functions()

        self.assertIn("Bong", config["system_prompt"])
        self.assertIn("<child_profile>", config["system_prompt"])
        # Original role text is preserved alongside the injected name.
        self.assertIn("system prompt", config["system_prompt"])

    async def test_lifecycle_consent_fallback_and_prompt_edges(self):
        conn = _Conn()
        provider = self.make_provider(conn)
        provider._client = _Client()
        provider._bridge = _Bridge()
        await provider.start_session()
        self.assertIs(conn.voice_provider, provider)

        fallback = _Fallback()
        conn.voice_consent_client = _Consent(True)
        provider._fallback_provider = fallback
        self.assertEqual(await provider.handle_text_message('{"type":"text","text":"hi"}'), "fallback-text")
        self.assertEqual(await provider.handle_audio_bytes(b"a"), "fallback-audio")
        await provider.interrupt()
        self.assertEqual(fallback.interrupted, 1)

    async def test_start_session_covers_consent_dormant_success_and_fallback_paths(self):
        denied_conn = _Conn()
        denied_conn.voice_consent_client = _Consent(False)
        denied_provider = self.make_provider(denied_conn)

        await denied_provider.start_session()

        self.assertIs(denied_conn.voice_provider, denied_provider)
        self.assertFalse(denied_provider._voice_consent_denied)
        self.assertEqual(denied_conn.sent, [])

        dormant_conn = _Conn()
        dormant_conn.session_mode = SessionMode.DORMANT
        dormant_provider = self.make_provider(dormant_conn)

        await dormant_provider.start_session()

        self.assertIs(dormant_conn.voice_provider, dormant_provider)
        self.assertFalse(dormant_provider._voice_consent_denied)
        self.assertIsNone(dormant_provider._client)

        opened_conn = _Conn()
        opened_provider = self.make_provider(opened_conn)
        opened = []

        async def _open_live_session():
            opened.append(True)
            opened_provider._client = _Client()
            opened_provider._bridge = _Bridge()

        opened_provider._open_live_session = _open_live_session

        await opened_provider.start_session()

        self.assertEqual(opened, [True])
        self.assertIs(opened_conn.voice_provider, opened_provider)
        self.assertFalse(opened_provider._voice_consent_denied)

        failed_conn = _Conn()
        fallback = _Fallback()
        failed_provider = self.make_provider(failed_conn, fallback=fallback)
        closed = []

        async def _open_failure():
            raise RuntimeError("open failed")

        async def _close_live_resources():
            closed.append(True)

        failed_provider._open_live_session = _open_failure
        failed_provider._close_live_resources = _close_live_resources

        await failed_provider.start_session()

        self.assertEqual(closed, [True])
        self.assertIsNone(failed_provider._fallback_provider)
        self.assertEqual(fallback.started, 0)

    async def test_text_and_audio_routing_edges(self):
        conn = _Conn()
        provider = self.make_provider(conn)
        provider._client = _Client()
        provider._bridge = _Bridge()

        self.assertFalse(await provider.handle_text_message("not-json"))
        self.assertTrue(await provider.handle_text_message('{"type":"listen","state":"start"}'))
        self.assertTrue(provider._user_audio_allowed_until > time.monotonic())
        self.assertTrue(await provider.handle_text_message('{"type":"listen","state":"stop"}'))
        self.assertEqual(provider._client.end_calls, 1)

        provider._client = None
        self.assertTrue(await provider.handle_text_message('{"type":"text","text":"hello"}'))
        provider._client = _Client()
        self.assertTrue(await provider.handle_text_message('{"type":"listen","state":"detect","text":"TeeBot"}'))
        self.assertTrue(await provider.handle_text_message('{"type":"listen","state":"detect","text":"   "}'))
        self.assertTrue(await provider.handle_text_message('{"type":"text","text":"dung lai"}'))

        self.assertFalse(provider._is_wake_word_only("not teebot"))
        self.assertFalse(provider._is_local_stop_word(""))

        provider._client = _Client()
        provider._bridge = _Bridge()
        provider._has_active_output = lambda: True
        self.assertTrue(await provider.handle_text_message('{"type":"text","text":"hello child"}'))
        self.assertEqual(provider._client.text[-1], "hello child")
        self.assertEqual(provider._bridge.allow_calls, 1)

        interrupts = []

        async def _window_interrupt(reason):
            interrupts.append(reason)

        provider._begin_user_interrupt = _window_interrupt
        await provider._open_user_audio_window("listen_start")
        self.assertEqual(interrupts, ["listen_start"])

        provider._reconnecting = True
        self.assertTrue(await provider.handle_audio_bytes(b"raw"))
        self.assertEqual(list(provider._pending_reconnect_audio)[-1][1], b"raw")
        provider._reconnecting = False
        provider._bridge = None
        conn.session_mode = SessionMode.CONVERSATION
        self.assertTrue(await provider.handle_audio_bytes(b"raw"))

        provider._bridge = None
        async def _deny_live_open():
            return False

        provider._ensure_live_open_for_audio = _deny_live_open
        self.assertTrue(await provider.handle_audio_bytes(b"needs-live"))

        error_provider = self.make_provider(_Conn())

        async def _stop_error(_text):
            raise RuntimeError("stop failed")

        error_provider._handle_local_stop_word = _stop_error
        self.assertTrue(await error_provider.handle_text_message('{"type":"text","text":"stop"}'))

    async def test_lesson_child_response_window_does_not_open_after_runtime_completes_during_delay(self):
        conn = _Conn()
        conn.session_mode = SessionMode.LESSON
        conn.lesson_runtime = SimpleNamespace(
            state="RUNNING",
            _step_id="s4",
            _step_passive=False,
            _step_completed=False,
            on_child_response=lambda *_args, **_kwargs: True,
        )
        conn.config["google_live"].update(
            {
                "lesson_child_response_open_delay_sec": 0.0,
                "lesson_prompt_tts_chars_per_sec": 1.0,
                "lesson_child_response_max_open_delay_sec": 8.0,
                "lesson_child_response_window_sec": 25.0,
            }
        )
        provider = self.make_provider(conn)
        provider._last_lesson_prompt_len = 4

        original_sleep = google_live_module.asyncio.sleep

        async def _complete_during_delay(_delay):
            conn.lesson_runtime.state = "COMPLETED"
            conn.lesson_runtime._step_completed = True

        google_live_module.asyncio.sleep = _complete_during_delay
        try:
            opened = await provider.open_lesson_child_response_window()
        finally:
            google_live_module.asyncio.sleep = original_sleep

        self.assertFalse(opened)
        self.assertEqual(provider._user_audio_allowed_until, 0.0)

    async def test_lesson_retry_response_window_uses_bounded_fast_reopen_delay(self):
        conn = _Conn()
        conn.session_mode = SessionMode.LESSON
        conn.config["google_live"].update(
            {
                "lesson_child_response_open_delay_sec": 0.0,
                "lesson_prompt_tts_chars_per_sec": 8.0,
                "lesson_child_response_max_open_delay_sec": 8.0,
                "lesson_child_response_fast_reopen_sec": 1.2,
                "lesson_child_response_window_sec": 25.0,
            }
        )
        conn.lesson_runtime = SimpleNamespace(
            state="RUNNING",
            _step_passive=False,
            _step_completed=False,
            _child_response_window_open=True,
            _step_id="s3",
            on_child_response=lambda *_args, **_kwargs: True,
        )
        provider = self.make_provider(conn)
        provider._last_lesson_prompt_len = 96
        provider._lesson_prompt_reopen_fast = True
        slept = []
        original_sleep = google_live_module.asyncio.sleep

        async def _capture_sleep(delay):
            slept.append(delay)

        google_live_module.asyncio.sleep = _capture_sleep
        try:
            opened = await provider.open_lesson_child_response_window()
        finally:
            google_live_module.asyncio.sleep = original_sleep

        self.assertTrue(opened)
        self.assertTrue(slept)
        self.assertLessEqual(max(slept), 1.2)

    async def test_lesson_child_transcript_routes_while_runtime_window_is_open_after_audio_timeout(self):
        conn = _Conn()
        conn.session_mode = SessionMode.LESSON
        handled = []

        async def _on_child_response(text, **kwargs):
            handled.append((text, kwargs))
            return True

        conn.lesson_runtime = SimpleNamespace(
            state="RUNNING",
            _step_id="s4",
            _step_passive=False,
            _step_completed=False,
            _child_response_window_open=True,
            on_child_response=_on_child_response,
        )
        provider = self.make_provider(conn)
        provider._client = _Client()
        provider._bridge = _Bridge()
        provider._user_audio_allowed_until = 0.0

        routed = await provider._route_lesson_child_response("barn barn barn")

        self.assertTrue(routed)
        self.assertEqual(
            handled,
            [("barn barn barn", {"source": "voice_transcript"})],
        )
        self.assertEqual(provider._bridge.stop_calls, 1)

    async def test_lesson_child_audio_logs_aec_live_vad_forward_while_robot_speaks(self):
        conn = _Conn()
        conn.session_mode = SessionMode.LESSON
        conn.client_is_speaking = True
        conn.google_live_audio_out_started_at = time.monotonic() - 1.0
        conn.lesson_runtime = SimpleNamespace(
            state="RUNNING",
            _step_id="s4",
            _step_passive=False,
            _step_completed=False,
            _child_response_window_open=True,
            on_child_response=lambda *_args, **_kwargs: True,
        )
        provider = self.make_provider(conn)
        provider._client = _Client()
        provider._bridge = _Bridge()
        provider._bridge._aec_processor = SimpleNamespace(bypassed=False)
        provider._user_audio_allowed_until = time.monotonic() + 5

        handled = await provider.handle_audio_bytes(b"child")

        self.assertTrue(handled)
        self.assertEqual(provider._bridge.forwarded, [b"pcm:child"])
        self.assertTrue(
            any(
                "aec_live_vad_forward" in str(args)
                for _, args, _ in conn.logger.messages
            )
        )

    async def test_wake_word_detect_opens_listening_without_greeting(self):
        conn = _Conn()
        provider = self.make_provider(conn)
        provider._client = _Client()
        provider._bridge = _Bridge()

        handled = await provider.handle_text_message(
            '{"type":"listen","state":"detect","text":"Hi ESP"}'
        )

        self.assertTrue(handled)
        self.assertGreater(provider._user_audio_allowed_until, time.monotonic())
        sent = [json.loads(payload) for payload in conn.websocket.sent]
        self.assertEqual(sent[0]["type"], "stt")
        self.assertEqual(sent[0]["text"], "Hi ESP")
        self.assertEqual(sent[1]["type"], "tts")
        self.assertEqual(sent[1]["state"], "stop")
        self.assertEqual(provider._client.text, [])

    async def test_high_speed_transcript_is_wake_only_not_lesson_start(self):
        conn = _Conn()
        provider = self.make_provider(conn)
        provider._client = _Client()
        provider._bridge = _Bridge()
        lesson_calls = []

        async def _lesson_start(text):
            lesson_calls.append(text)
            return True

        provider._dispatch_lesson_start_intent = _lesson_start

        handled = await provider._on_user_transcript("high speed")

        self.assertTrue(handled)
        self.assertEqual(lesson_calls, [])
        self.assertGreater(provider._user_audio_allowed_until, time.monotonic())
        sent = [json.loads(payload) for payload in conn.websocket.sent]
        self.assertEqual(sent[0]["type"], "stt")
        self.assertEqual(sent[0]["text"], "high speed")
        self.assertEqual(sent[1]["type"], "tts")
        self.assertEqual(sent[1]["state"], "stop")

    async def test_listen_start_opens_listening_without_greeting(self):
        conn = _Conn()
        conn.config["enable_greeting"] = True
        conn.session_mode = SessionMode.CONVERSATION
        provider = self.make_provider(conn)
        provider._client = _Client()
        provider._bridge = _Bridge()

        handled = await provider.handle_text_message('{"type":"listen","state":"start"}')

        self.assertTrue(handled)
        self.assertGreater(provider._user_audio_allowed_until, time.monotonic())
        self.assertEqual(provider._client.text, [])

    async def test_repeated_listen_start_stays_silent(self):
        conn = _Conn()
        conn.config["enable_greeting"] = True
        conn.session_mode = SessionMode.CONVERSATION
        provider = self.make_provider(conn)
        provider._client = _Client()
        provider._bridge = _Bridge()

        with patch.object(
            google_live_module.time,
            "monotonic",
            side_effect=[100.0, 100.0, 105.0, 105.0, 116.0, 116.0],
        ):
            await provider.handle_text_message('{"type":"listen","state":"start"}')
            await provider.handle_text_message('{"type":"listen","state":"start"}')
            await provider.handle_text_message('{"type":"listen","state":"start"}')

        self.assertEqual(provider._client.text, [])

    async def test_lesson_start_intent_bootstraps_missing_tool_handler_in_dormant_live_mode(self):
        conn = _Conn()
        conn.func_handler = None
        provider = self.make_provider(conn)
        installed_handler = _FuncHandler()
        ensure_calls = []

        async def ensure_func_handler():
            ensure_calls.append(True)
            conn.func_handler = installed_handler

        provider._ensure_func_handler = ensure_func_handler
        original_product_tool_names = google_live_module.product_tool_names
        google_live_module.product_tool_names = lambda _conn: ["start_lesson"]
        try:
            handled = await provider.handle_text_message(
                '{"type":"listen","state":"detect","text":"bắt đầu bài học"}'
            )
        finally:
            google_live_module.product_tool_names = original_product_tool_names

        self.assertTrue(handled)
        self.assertEqual(ensure_calls, [True])
        self.assertEqual(installed_handler.calls[0][1], {"name": "start_lesson", "arguments": {}})

    async def test_lesson_start_intent_releases_realtime_busy_state_after_local_tool_dispatch(self):
        conn = _Conn()
        conn.func_handler = _FuncHandler()
        provider = self.make_provider(conn)
        original_product_tool_names = google_live_module.product_tool_names
        google_live_module.product_tool_names = lambda _conn: ["start_lesson"]
        try:
            handled = await provider._dispatch_lesson_start_intent("bắt đầu bài học")
        finally:
            google_live_module.product_tool_names = original_product_tool_names

        self.assertTrue(handled)
        self.assertEqual(
            provider._interaction.state,
            google_live_module.InteractionState.IDLE,
        )
        self.assertFalse(conn.client_abort)

    async def test_continuous_background_audio_finalizes_clean_turn(self):
        conn = _Conn()
        conn.config["google_live"].update(
            {
                "input_min_capture_ms": 0,
                "input_speech_tail_ms": 1,
                "input_max_capture_ms": 10000,
                "input_speech_rms_threshold": 500,
            }
        )
        provider = self.make_provider(conn)
        provider._client = _Client()
        provider._bridge = _Bridge()
        provider._bridge.rms = 100

        self.assertTrue(await provider.handle_audio_bytes(b"first"))
        await asyncio.sleep(0.01)
        self.assertTrue(await provider.handle_audio_bytes(b"silence"))

        self.assertEqual(provider._client.end_calls, 1)
        self.assertEqual(provider._interaction.state, google_live_module.InteractionState.WAITING_MODEL)

        forwarded_before_waiting_drop = len(provider._bridge.forwarded)
        self.assertTrue(await provider.handle_audio_bytes(b"background"))
        self.assertTrue(await provider.handle_audio_bytes(b"background-again"))
        self.assertEqual(provider._client.end_calls, 1)
        self.assertEqual(len(provider._bridge.forwarded), forwarded_before_waiting_drop)

    def test_conversation_input_timing_is_faster_than_lesson_timing(self):
        conn = _Conn()
        provider = self.make_provider(conn)

        self.assertEqual(provider._get_input_flush_delay(), 0.7)
        self.assertEqual(provider._get_user_speech_tail_sec(), 0.65)

        conn.session_mode = SessionMode.LESSON
        conn.lesson_runtime = SimpleNamespace(state="RUNNING")

        self.assertEqual(provider._get_input_flush_delay(), 1.4)
        self.assertEqual(provider._get_user_speech_tail_sec(), 1.3)

    async def test_model_audio_end_reopens_input_after_waiting_model(self):
        conn = _Conn()
        provider = self.make_provider(conn)
        provider._client = _Client()
        provider._bridge = _Bridge()
        provider._interaction.transition(google_live_module.InteractionState.WAITING_MODEL)

        await provider._handle_live_event({"type": "audio_end"})

        self.assertEqual(provider._interaction.state, google_live_module.InteractionState.LISTENING)
        self.assertTrue(await provider.handle_audio_bytes(b"hi-again"))
        self.assertEqual(provider._bridge.forwarded, [b"pcm:hi-again"])

    async def test_receive_loop_cancels_waiting_timeout_on_model_audio(self):
        class _HoldingClient(_Client):
            def __init__(self):
                super().__init__()
                self.hold = asyncio.Event()

            async def receive_events(self):
                yield {"type": "audio_start"}
                await self.hold.wait()

        conn = _Conn()
        conn.config["google_live"].update(
            {
                "waiting_model_timeout_sec": 0.01,
                "waiting_model_retry_prompt_after_sec": 0,
            }
        )
        provider = self.make_provider(conn)
        provider._client = _HoldingClient()
        provider._bridge = _Bridge()
        provider._session_generation = 1
        provider._interaction.transition(google_live_module.InteractionState.WAITING_MODEL)
        provider._waiting_model_since = time.monotonic() - 1
        provider._schedule_waiting_model_timeout_task()

        task = asyncio.create_task(provider._receive_events_loop(1))
        try:
            for _ in range(20):
                if provider._bridge.events:
                    break
                await asyncio.sleep(0)
            await asyncio.sleep(0.03)
        finally:
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        self.assertEqual(provider._bridge.events, [{"type": "audio_start"}])
        self.assertIsNone(provider._waiting_model_timeout_task)
        self.assertEqual(provider._client.text, [])

    async def test_model_audio_start_cancels_pending_idle_input_flush(self):
        conn = _Conn()
        conn.config["google_live"].update(
            {
                "input_flush_delay_sec": 0.01,
                "waiting_model_timeout_sec": 0.01,
            }
        )
        provider = self.make_provider(conn)
        provider._client = _Client()
        provider._bridge = _Bridge()
        provider._interaction.transition(google_live_module.InteractionState.USER_STREAMING)
        provider._schedule_input_flush()

        await provider._handle_live_event({"type": "audio_start"})
        await asyncio.sleep(0.03)

        self.assertEqual(provider._client.end_calls, 0)
        self.assertNotEqual(
            provider._interaction.state,
            google_live_module.InteractionState.WAITING_MODEL,
        )
        self.assertIsNone(provider._waiting_model_since)
        self.assertIsNone(provider._waiting_model_timeout_task)

    async def test_waiting_model_timeout_reopens_input_when_live_returns_nothing(self):
        conn = _Conn()
        conn.config["google_live"]["waiting_model_timeout_sec"] = 0.01
        provider = self.make_provider(conn)
        provider._client = _Client()
        provider._bridge = _Bridge()
        provider._interaction.transition(google_live_module.InteractionState.WAITING_MODEL)
        provider._waiting_model_since = time.monotonic() - 1

        self.assertTrue(await provider.handle_audio_bytes(b"next-wake"))

        self.assertEqual(provider._interaction.state, google_live_module.InteractionState.USER_STREAMING)
        self.assertEqual(provider._bridge.forwarded, [b"pcm:next-wake"])

    async def test_default_waiting_model_timeout_holds_briefly_for_slow_live_transcript_window(self):
        conn = _Conn()
        provider = self.make_provider(conn)
        provider._client = _Client()
        provider._bridge = _Bridge()
        provider._interaction.transition(google_live_module.InteractionState.WAITING_MODEL)
        provider._waiting_model_since = time.monotonic() - 1.0

        self.assertTrue(await provider.handle_audio_bytes(b"too-soon"))

        self.assertEqual(
            provider._interaction.state,
            google_live_module.InteractionState.WAITING_MODEL,
        )
        self.assertEqual(provider._bridge.forwarded, [])

    async def test_default_waiting_model_timeout_reopens_input_after_two_seconds(self):
        conn = _Conn()
        provider = self.make_provider(conn)
        provider._client = _Client()
        provider._bridge = _Bridge()
        provider._interaction.transition(google_live_module.InteractionState.WAITING_MODEL)
        provider._waiting_model_since = time.monotonic() - 2.1

        self.assertTrue(await provider.handle_audio_bytes(b"not-stuck"))

        self.assertEqual(
            provider._interaction.state,
            google_live_module.InteractionState.USER_STREAMING,
        )
        self.assertEqual(provider._bridge.forwarded, [b"pcm:not-stuck"])

    async def test_waiting_model_timeout_sends_child_retry_prompt_via_live_text_once_per_cooldown(self):
        conn = _Conn()
        conn.config["google_live"].update(
            {
                "waiting_model_timeout_sec": 0.01,
                "waiting_model_retry_prompt_after_sec": 0,
                "waiting_model_retry_prompt_cooldown_sec": 60,
            }
        )
        provider = self.make_provider(conn)
        provider._client = _Client()
        provider._bridge = _Bridge()

        provider._interaction.transition(google_live_module.InteractionState.WAITING_MODEL)
        provider._waiting_model_since = time.monotonic() - 1

        self.assertTrue(await provider.handle_audio_bytes(b"retry-one"))

        provider._interaction.transition(google_live_module.InteractionState.WAITING_MODEL)
        provider._waiting_model_since = time.monotonic() - 1
        self.assertTrue(await provider.handle_audio_bytes(b"retry-two"))

        self.assertEqual(
            provider._client.text,
            [
                google_live_module.LESSON_LIVE_TEXT_INSTRUCTION
                + "Robot chưa nghe rõ, con nói lại nhé."
            ],
        )

    async def test_waiting_model_timeout_fires_without_next_audio_frame(self):
        conn = _Conn()
        conn.config["google_live"].update(
            {
                "waiting_model_timeout_sec": 0.01,
                "waiting_model_retry_prompt_after_sec": 0,
            }
        )
        provider = self.make_provider(conn)
        provider._client = _Client()
        provider._bridge = _Bridge()

        await provider._finalize_user_audio_input("listen_stop")
        await asyncio.sleep(0.05)

        self.assertEqual(provider._interaction.state, google_live_module.InteractionState.LISTENING)
        self.assertIsNone(provider._waiting_model_since)
        self.assertEqual(
            provider._client.text,
            [
                google_live_module.LESSON_LIVE_TEXT_INSTRUCTION
                + "Robot chưa nghe rõ, con nói lại nhé."
            ],
        )

    async def test_idle_flush_finalizes_turn_and_clears_user_stream(self):
        # FIX A: the idle-flush safety-net must be a COMPLETE finalize. Previously it
        # sent end_audio_stream but left a stale _user_stream_started_at, so the next
        # utterance was truncated to ~one frame ("robot can't hear me").
        conn = _Conn()
        provider = self.make_provider(conn)
        provider._client = _Client()
        provider._bridge = _Bridge()
        provider._user_stream_started_at = time.monotonic()
        provider._user_stream_response_id = 5
        provider._user_stream_frames = 12
        provider._input_flush_generation = 7

        await provider._flush_input_after_idle(0, 7)

        self.assertEqual(provider._client.end_calls, 1)
        self.assertEqual(
            provider._interaction.state, google_live_module.InteractionState.WAITING_MODEL
        )
        # Load-bearing: the next turn must NOT inherit this turn's stream bookkeeping.
        self.assertIsNone(provider._user_stream_started_at)
        self.assertIsNone(provider._user_stream_response_id)
        self.assertEqual(provider._user_stream_frames, 0)
        self.assertIsNotNone(provider._waiting_model_since)

    async def test_lesson_child_audio_uses_clean_turn_finalizer(self):
        conn = _Conn()
        conn.config["google_live"].update(
            {
                "input_min_capture_ms": 0,
                "input_speech_tail_ms": 0,
                "input_max_capture_ms": 0,
            }
        )
        conn.lesson_runtime = SimpleNamespace(
            state="RUNNING",
            _step_passive=False,
            _step_completed=False,
            _child_response_window_open=True,
            on_child_response=lambda *_args, **_kwargs: True,
        )
        provider = self.make_provider(conn)
        provider._client = _Client()
        provider._bridge = _Bridge()
        provider._user_audio_allowed_until = time.monotonic() + 5

        handled = await provider.handle_audio_bytes(b"barn")

        self.assertTrue(handled)
        self.assertEqual(provider._bridge.forwarded, [b"pcm:barn"])
        self.assertEqual(provider._client.end_calls, 1)
        self.assertEqual(
            provider._interaction.state,
            google_live_module.InteractionState.WAITING_MODEL,
        )
        self.assertIsNone(provider._user_stream_started_at)

    async def test_finalize_is_guarded_against_re_arm_while_waiting_model(self):
        # FIX B: a redundant finalize (late listen_stop / idle-flush race) arriving
        # while already WAITING_MODEL must not re-send end_audio_stream nor re-stamp
        # _waiting_model_since (which would extend the dead-mic window indefinitely).
        conn = _Conn()
        provider = self.make_provider(conn)
        provider._client = _Client()
        provider._bridge = _Bridge()
        provider._interaction.transition(google_live_module.InteractionState.WAITING_MODEL)
        provider._waiting_model_since = 123.0

        await provider._finalize_user_audio_input("listen_stop")

        self.assertEqual(provider._client.end_calls, 0)
        self.assertEqual(provider._waiting_model_since, 123.0)
        self.assertEqual(
            provider._interaction.state, google_live_module.InteractionState.WAITING_MODEL
        )

    async def test_low_release_timeout_reopens_without_nagging_when_retry_after_is_longer(self):
        # FIX C: a short release timeout re-hears the child fast, but the spoken
        # "con nói lại" nudge stays gated on the longer retry-after grace — so a
        # merely-slow turn does NOT make the robot nag.
        conn = _Conn()
        conn.config["google_live"].update(
            {
                "waiting_model_timeout_sec": 0.01,
                "waiting_model_retry_prompt_after_sec": 10,
            }
        )
        provider = self.make_provider(conn)
        provider._client = _Client()
        provider._bridge = _Bridge()
        provider._interaction.transition(google_live_module.InteractionState.WAITING_MODEL)
        provider._waiting_model_since = time.monotonic() - 1  # elapsed ~1s: >= release, < retry-after

        self.assertTrue(await provider.handle_audio_bytes(b"again"))

        # Re-heard (released + forwarded) ...
        self.assertEqual(
            provider._interaction.state, google_live_module.InteractionState.USER_STREAMING
        )
        self.assertEqual(provider._bridge.forwarded, [b"pcm:again"])
        # ... but NOT nagged.
        self.assertEqual(provider._client.text, [])

    async def test_continuous_loud_audio_finalizes_at_max_capture(self):
        conn = _Conn()
        conn.config["google_live"].update(
            {
                "input_min_capture_ms": 0,
                "input_speech_tail_ms": 10000,
                "input_max_capture_ms": 1,
                "input_speech_rms_threshold": 50,
            }
        )
        provider = self.make_provider(conn)
        provider._client = _Client()
        provider._bridge = _Bridge()
        provider._bridge.rms = 1000

        self.assertTrue(await provider.handle_audio_bytes(b"first"))
        await asyncio.sleep(0.01)
        self.assertTrue(await provider.handle_audio_bytes(b"still-speaking"))

        self.assertEqual(provider._client.end_calls, 1)
        self.assertEqual(provider._interaction.state, google_live_module.InteractionState.WAITING_MODEL)

    async def test_receive_close_admission_usage_and_resumption_edges(self):
        conn = _Conn()
        conn.last_live_activity_at = time.monotonic() - 100
        conn.live_resumption_store = _Store()
        conn.live_admission_gate = _AsyncGate(
            SimpleNamespace(decision=AdmissionDecision.FRIENDLY_BREAK, reason=AdmissionReason.RECONNECT_STORM)
        )
        provider = self.make_provider(conn)
        provider._client = _Client(
            events=[
                {"type": "session_resumption_update", "resumable": True, "handle": "h1"},
                {"type": "session_expiring", "time_left_ms": 1},
                {"type": "custom"},
            ]
        )
        provider._bridge = _Bridge()
        provider._session_generation = 1

        await provider._receive_events_loop(2)
        self.assertEqual(provider._bridge.events, [])
        await provider._receive_events_loop(1)
        self.assertEqual(conn.google_live_session_resumption_handle, "h1")

        self.assertFalse(await provider._ensure_live_open_for_audio())
        self.assertEqual(conn.sent[-1]["status"], "live_unavailable")
        conn.sent = None
        conn.websocket = _WebSocket(fail=True)
        await provider._send_live_unavailable(AdmissionReason.RECONNECT_STORM)

        conn.session_mode = SessionMode.DORMANT
        conn._set_session_mode = lambda mode, reason=None: setattr(conn, "session_mode", mode)
        conn.live_admission_gate = _Gate(
            SimpleNamespace(
                decision=AdmissionDecision.DEGRADE_TTS_ONLY,
                reason=AdmissionReason.DEVICE_DAILY_BUDGET_EXHAUSTED,
            )
        )
        provider._fallback_provider = None
        self.assertFalse(await provider._ensure_live_open_for_audio())
        self.assertEqual(conn.session_mode, SessionMode.CONVERSATION)
        self.assertIsNone(provider._fallback_provider)

        provider._client = _Client()
        provider._bridge = _FailingBridge()
        conn.google_live_session_started_at = time.monotonic() - 1
        conn.google_live_session_resumption_handle = "saved-handle"
        await provider._close_live_resources()
        self.assertTrue(conn.live_resumption_store.saved)

        class _ConcurrentCloseClient(_Client):
            async def close(self):
                self.closed += 1
                raise RuntimeError("anext(): asynchronous generator is already running")

        provider._client = _ConcurrentCloseClient()
        await provider._close_live_resources()
        self.assertIsNone(provider._client)

        conn.live_admission_gate = _Gate(SimpleNamespace(decision=AdmissionDecision.ALLOW_LIVE, reason=AdmissionReason.OK))
        conn.google_live_session_started_at = time.monotonic() - 1
        await provider._record_live_session_usage()
        self.assertTrue(conn.live_admission_gate.usage)

        conn.google_live_session_resumption_handle = None
        self.assertTrue(await provider._restore_session_resumption_handle())
        self.assertFalse(await provider._restore_session_resumption_handle())
        self.assertFalse(provider._handle_session_resumption_update("bad"))
        self.assertFalse(provider._handle_session_resumption_update({"resumable": False}))

    async def test_replay_flush_interrupt_and_config_edges(self):
        conn = _Conn()
        conn.config["google_live"].update(
            {
                "input_flush_delay_sec": "bad",
                "interrupt_forced_flush_delay_sec": "bad",
                "interrupt_min_capture_ms": "bad",
                "interrupt_speech_tail_ms": "bad",
                "interrupt_max_capture_ms": "bad",
                "interrupt_speech_rms_threshold": "bad",
                "reconnect_buffer_ms": "bad",
                "interrupt_replay_buffer_ms": "bad",
                "input_frame_duration_ms": "bad",
                "reconnect": {"enabled": True, "max_retries": "bad", "backoff_ms": "bad", "backoff_multiplier": "bad"},
            }
        )
        provider = self.make_provider(conn)
        provider._client = _Client()
        provider._bridge = _Bridge()
        provider._response_generation = 2

        self.assertEqual(provider._get_reconnect_buffer_capacity(), 33)
        self.assertEqual(provider._get_interrupt_replay_buffer_capacity(), 15)
        self.assertIsNone(provider._get_input_flush_delay())
        self.assertIsNone(provider._get_interrupt_forced_flush_delay())
        self.assertEqual(provider._get_interrupt_min_capture_sec(), 0.36)
        self.assertEqual(provider._get_interrupt_speech_tail_sec(), 0.24)
        self.assertEqual(provider._get_interrupt_max_capture_sec(), 1.2)
        self.assertGreaterEqual(provider._get_interrupt_speech_rms_threshold(), 1)
        self.assertEqual(provider._get_reconnect_config()["max_retries"], 0)
        self.assertEqual(provider._get_reconnect_delay_ms("bad"), 0)
        self.assertEqual(provider._classify_error(RuntimeError("bad api key")), "auth")
        self.assertEqual(provider._classify_error(RuntimeError("quota 429")), "quota")
        self.assertEqual(provider._classify_error(RuntimeError("model config invalid")), "invalid_config")
        self.assertEqual(provider._classify_error(RuntimeError("connection timeout")), "network")

        provider._pending_reconnect_audio = deque([(1, b"old"), (2, b"new"), b"raw", b""])
        await provider._forward_pending_reconnect_audio()
        self.assertEqual(provider._bridge.forwarded, [b"pcm:new", b"pcm:raw"])

        provider._pending_interrupt_audio = deque([b"pcm1", b"", b"pcm2"])
        provider._pending_interrupt_audio_response_id = 2
        await provider._replay_pending_interrupt_audio("test")
        self.assertEqual(provider._bridge.forwarded[-2:], [b"pcm1", b"pcm2"])
        provider._pending_interrupt_audio = deque([b"pcm3"])
        provider._pending_interrupt_audio_response_id = 1
        await provider._replay_pending_interrupt_audio("drift")
        provider._pending_interrupt_audio = deque([b"pcm4"])
        provider._pending_interrupt_audio_response_id = 2
        provider._interrupt_replayed_once = True
        await provider._replay_pending_interrupt_audio("again")

        provider._bridge.blocked = True
        provider._pending_interrupt_audio_response_id = 2
        self.assertTrue(provider._buffer_pending_interrupt_audio_while_blocked(b"held"))
        provider._interrupt_capture_response_id = 2
        self.assertTrue(provider._should_hold_interrupt_audio(b"held2"))
        provider._bridge.blocked = False
        self.assertFalse(provider._should_hold_interrupt_audio(b"free"))

        provider._interrupt_capture_response_id = 2
        provider._interrupt_capture_started_at = time.monotonic() - 2
        provider._interrupt_capture_last_speech_at = time.monotonic() - 2
        self.assertTrue(provider._interrupt_input_can_finalize())
        self.assertGreater(provider._interrupt_capture_elapsed_ms(), 0)

    async def test_intent_ack_tool_and_error_edges(self):
        conn = _Conn()
        conn.func_handler = _FuncHandler()
        provider = self.make_provider(conn)
        provider._client = _Client()
        provider._bridge = _Bridge()
        provider._user_audio_allowed_until = time.monotonic() + 5

        runtime = SimpleNamespace(on_child_response=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("child failed")))
        conn.lesson_runtime = runtime
        self.assertFalse(await provider._dispatch_lesson_child_response("answer"))

        provider._user_audio_allowed_until = time.monotonic() + 5
        conn.lesson_runtime = SimpleNamespace(on_child_response=lambda *_args, **_kwargs: False)
        self.assertFalse(await provider._dispatch_lesson_child_response("answer"))

        barge_interrupts = []

        async def _barge_interrupt(reason):
            barge_interrupts.append(reason)

        provider._begin_user_interrupt = _barge_interrupt
        await provider._on_user_transcript_barge_in("plain chatter")
        self.assertEqual(barge_interrupts, ["transcript_barge_in"])

        self.assertEqual(provider._lesson_start_ack_text(None), "Bắt đầu bài học nhé.")
        self.assertIn("chưa", provider._lesson_start_ack_text(SimpleNamespace(action=Action.ERROR, response="", result="")))
        self.assertIn("chưa", provider._lesson_start_ack_text(SimpleNamespace(action=Action.RESPONSE, response="", result="failed")))
        self.assertFalse(await provider.speak_lesson_step_prompt("   "))
        self.assertTrue(await provider._send_live_text_ack("hello"))
        provider._client.send_text = lambda _text: (_ for _ in ()).throw(RuntimeError("text failed"))
        self.assertFalse(await provider._send_live_text_ack("hello"))

        self.assertEqual(provider._classify_music_control_intent(""), None)
        conn._music_session = SimpleNamespace()
        self.assertEqual(provider._classify_music_control_intent("phát tiếp")["name"], "resume_music")
        self.assertEqual(provider._classify_music_control_intent("tạm dừng nhạc")["name"], "pause_music")
        self.assertEqual(provider._classify_music_control_intent("dừng nhạc")["name"], "stop_music")
        self.assertEqual(provider._classify_music_control_intent("phát bài Baby Shark")["arguments"]["song_name"], "Baby Shark")
        self.assertEqual(provider._music_state_for_tool("bad"), "unknown")
        self.assertTrue(provider._is_wake_word_only("teebot"))
        self.assertTrue(provider._is_local_stop_word("stop"))

        self.assertEqual(await provider._execute_tool_call("", {}), provider._tool_error("MISSING_FUNCTION_NAME", "Missing function name"))
        self.assertEqual((await provider._execute_tool_call("x", []))["errorCode"], "INVALID_TOOL_ARGS")
        provider.conn.func_handler = None
        self.assertEqual((await provider._execute_tool_call("x", {}))["errorCode"], "TOOL_HANDLER_UNAVAILABLE")

    async def test_audio_echo_interrupt_and_music_edges(self):
        conn = _Conn()
        provider = self.make_provider(conn)
        provider._client = _Client()
        provider._bridge = _Bridge()
        config = conn.config["google_live"]

        session = SimpleNamespace(stop_event=SimpleNamespace(is_set=lambda: True))
        conn._music_session = session
        self.assertFalse(provider._has_music_session())
        session.stop_event = SimpleNamespace(is_set=lambda: (_ for _ in ()).throw(RuntimeError("bad")))
        self.assertTrue(provider._has_music_session())
        session.is_paused = lambda: (_ for _ in ()).throw(RuntimeError("bad"))
        self.assertTrue(provider._has_audible_music_session())
        config["music_auto_pause_on_user_speech"] = False
        provider._auto_pause_music_for_interaction()

        config["music_auto_pause_on_user_speech"] = True
        conn._music_session = SimpleNamespace(is_paused=lambda: True, pause=lambda: None)
        provider._auto_pause_music_for_interaction()
        paused = []
        conn._music_session = SimpleNamespace(is_paused=lambda: False, pause=lambda: paused.append(True))
        provider._auto_pause_music_for_interaction()
        self.assertEqual(paused, [True])

        conn._music_session = None
        conn.google_live_echo_suppress_until = time.monotonic() + 1
        self.assertEqual(provider._current_audio_suppression_reason(), "echo_tail")
        self.assertEqual(provider._current_interaction_state_for_audio().value, "MUTED")
        provider._log_audio_decision("drop", "echo", b"pcm")

        config.update({"echo_bypass_interrupt_enabled": True, "robot_output_echo_bypass_rms_threshold": "bad", "robot_output_echo_bypass_min_duration_sec": 0})
        self.assertTrue(provider._should_bypass_echo_gate_for_loud_user(config, 2000))
        self.assertGreater(provider._user_audio_allowed_until, time.monotonic())

        provider._bridge.rms = 5000
        config.update({"robot_output_echo_bypass_rms_threshold": 100, "robot_output_echo_bypass_min_duration_sec": 0})
        provider._echo_bypass_pending_interrupt = False
        self.assertTrue(provider._should_suppress_robot_output_echo(b"pcm"))
        self.assertFalse(provider._echo_bypass_pending_interrupt)

        provider._echo_bypass_pending_interrupt = False
        conn.google_live_echo_suppress_until = 0
        conn._music_session = SimpleNamespace(stop_event=SimpleNamespace(is_set=lambda: False))
        config["echo_bypass_interrupt_enabled"] = False
        self.assertTrue(provider._should_suppress_robot_output_echo(b"pcm"))

        conn.client_is_speaking = True
        conn.google_live_audio_out_started_at = time.monotonic() - 1
        config.update({"barge_in": True, "barge_in_rms_threshold": "bad", "barge_in_min_input_duration_sec": 0})
        self.assertFalse(provider._should_barge_in(b"pcm"))
        provider._bridge.rms = 6000
        self.assertFalse(provider._should_barge_in(b"pcm"))
        config.update({"interrupt_on_input_while_speaking": True, "interrupt_rms_threshold": "bad", "interrupt_min_input_duration_sec": 0, "barge_in": False})
        self.assertFalse(provider._should_interrupt_for_input(b"pcm"))
        provider._get_live_config = lambda: {
            "barge_in": False,
            "interrupt_on_input_while_speaking": True,
            "interrupt_rms_threshold": "bad",
            "interrupt_min_input_duration_sec": 0,
            "interrupt_min_output_age_sec": 0,
        }
        self.assertTrue(provider._should_interrupt_for_input(b"pcm"))
        config["mute_input_after_audio_start_sec"] = "bad"
        self.assertFalse(provider._should_drop_input_post_audio_start())

        await provider._begin_user_interrupt("ambient_audio")
        conn._music_session = None
        provider._cancelled_response_ids = set(range(25))
        await provider._begin_user_interrupt("explicit_interrupt")
        self.assertLessEqual(len(provider._cancelled_response_ids), 11)

        class _EndStreamFailClient(_Client):
            async def end_audio_stream(self):
                raise RuntimeError("already closed token=secret")

        provider._client = _EndStreamFailClient()
        provider._bridge = _Bridge()
        provider._last_interrupt_at = 0
        scheduled = []
        provider._schedule_forced_interrupt_input_flush = lambda reason: scheduled.append(reason)
        await provider._begin_user_interrupt("audio_input")
        self.assertEqual(scheduled, ["audio_input"])

        hard_reconnects = []

        async def _hard_reconnect(reason):
            hard_reconnects.append(reason)

        provider._last_interrupt_at = 0
        provider._receive_task = object()
        provider._should_hard_reconnect_on_interrupt = lambda: True
        provider._hard_reconnect_after_interrupt = _hard_reconnect
        await provider._begin_user_interrupt("audio_input")
        self.assertEqual(hard_reconnects, ["audio_input"])
        provider._receive_task = None

    async def test_tool_inventory_injection_and_intent_guard_edges(self):
        conn = _Conn()
        provider = self.make_provider(conn)

        provider._extra_function_names_for_live = lambda: []
        provider._inject_live_extra_functions_into_intent()

        provider._extra_function_names_for_live = lambda: ["change_volume", "play_music"]
        conn.config = []
        provider._inject_live_extra_functions_into_intent()

        conn.config = {"Intent": {}, "selected_module": {}}
        provider._inject_live_extra_functions_into_intent()
        conn.config = {"Intent": {}, "selected_module": {"Intent": "child"}}
        provider._inject_live_extra_functions_into_intent()
        conn.config = {"Intent": {"child": []}, "selected_module": {"Intent": "child"}}
        provider._inject_live_extra_functions_into_intent()

        class _ToolManager:
            def __init__(self):
                self.calls = 0

            def refresh_tools(self):
                self.calls += 1
                raise RuntimeError("refresh failed")

        tool_manager = _ToolManager()
        conn.func_handler = SimpleNamespace(tool_manager=tool_manager)
        conn.config = {
            "Intent": {"child": {"functions": object()}},
            "selected_module": {"Intent": "child"},
        }
        provider._inject_live_extra_functions_into_intent()
        self.assertEqual(conn.config["Intent"]["child"]["functions"], ["change_volume", "play_music"])
        provider._inject_live_extra_functions_into_intent()
        self.assertEqual(tool_manager.calls, 1)

        conn.func_handler = SimpleNamespace(
            get_functions=lambda: [
                {"function": {"name": "change_volume"}},
                {"function": {}},
                "bad",
            ]
        )
        provider._log_tool_handler_inventory("ready")
        conn.func_handler = SimpleNamespace(get_functions=lambda: (_ for _ in ()).throw(RuntimeError("inventory failed")))
        provider._log_tool_handler_inventory("broken")

        original_product_tool_names = google_live_module.product_tool_names
        google_live_module.product_tool_names = lambda _conn: (_ for _ in ()).throw(RuntimeError("tools failed"))
        try:
            self.assertIsNone(provider._classify_lesson_start_intent("bắt đầu bài học"))
        finally:
            google_live_module.product_tool_names = original_product_tool_names

    async def test_audio_routing_private_branch_edges(self):
        conn = _Conn()
        provider = self.make_provider(conn)
        provider._client = _Client()
        provider._bridge = _Bridge()

        class _SyncBridge:
            def __init__(self):
                self.forwarded = []
                self.allow_calls = 0

            def decode_input_audio(self, audio):
                return b"sync:" + audio

            async def forward_decoded_input_audio(self, pcm):
                self.forwarded.append(pcm)

            def allow_model_output(self):
                self.allow_calls += 1

            def current_response_id(self):
                return 0

            def is_model_output_blocked(self):
                return False

        provider._bridge = _SyncBridge()
        self.assertTrue(await provider.handle_audio_bytes(b"sync"))
        self.assertEqual(provider._bridge.forwarded, [b"sync:sync"])

        provider._bridge = _Bridge()
        bypass_interrupts = []

        async def _begin_bypass(reason):
            bypass_interrupts.append(reason)

        provider._begin_user_interrupt = _begin_bypass
        provider._echo_bypass_pending_interrupt = True
        self.assertTrue(await provider.handle_audio_bytes(b"loud"))
        self.assertEqual(bypass_interrupts, ["loud_input"])

        provider._echo_bypass_pending_interrupt = False
        provider._should_suppress_robot_output_echo = lambda _audio: True
        self.assertTrue(await provider.handle_audio_bytes(b"echo"))

        provider._should_drop_input_post_audio_start = lambda: True
        conn.client_abort = True
        self.assertTrue(await provider.handle_audio_bytes(b"drop"))
        self.assertFalse(conn.client_abort)

        provider._should_drop_input_post_audio_start = lambda: False
        provider._should_suppress_robot_output_echo = lambda _audio: False
        provider._should_hold_interrupt_audio = lambda _audio: True
        self.assertTrue(await provider.handle_audio_bytes(b"hold"))

        interrupts = []

        async def _begin(reason):
            interrupts.append(reason)

        provider._should_hold_interrupt_audio = lambda _audio: False
        provider._should_interrupt_for_input = lambda _audio: True
        provider._begin_user_interrupt = _begin
        self.assertTrue(await provider.handle_audio_bytes(b"interrupt"))
        self.assertEqual(interrupts, ["audio_input"])

        provider._should_interrupt_for_input = lambda _audio: False
        provider._should_drop_input_during_output = lambda: True
        self.assertTrue(await provider.handle_audio_bytes(b"drop-output"))

        class _RawBridge:
            def __init__(self):
                self.raw = []

            async def forward_input_audio(self, audio):
                self.raw.append(audio)

            def current_response_id(self):
                return 0

        raw_bridge = _RawBridge()
        provider._bridge = raw_bridge
        provider._should_drop_input_during_output = lambda: False
        provider._record_interrupt_capture_audio = lambda _audio: None
        provider._buffer_pending_interrupt_audio_while_blocked = lambda _audio: False
        self.assertTrue(await provider.handle_audio_bytes(b"raw"))
        self.assertEqual(raw_bridge.raw, [b"raw"])

        class _ExplodingBridge(_Bridge):
            async def decode_input_audio_async(self, _audio):
                raise RuntimeError("decode failed")

        failures = []

        async def _runtime_failure(exc):
            failures.append(str(exc))

        provider._bridge = _ExplodingBridge()
        provider._handle_runtime_failure = _runtime_failure
        self.assertTrue(await provider.handle_audio_bytes(b"boom"))
        self.assertEqual(failures, ["decode failed"])

    async def test_idle_accounting_and_resumption_edge_branches(self):
        conn = _Conn()
        provider = self.make_provider(conn)
        provider._touch_live_activity()
        self.assertFalse(hasattr(conn, "last_live_activity_at"))

        conn.session_mode = SessionMode.CONVERSATION
        provider._touch_live_activity()
        self.assertGreater(conn.last_live_activity_at, 0)

        conn.live_admission_gate = None
        decision = await provider._admit_live_open()
        self.assertEqual(decision.decision, AdmissionDecision.ALLOW_LIVE)
        self.assertIsNotNone(conn.live_admission_gate)

        conn.sent = None
        conn.websocket = None
        await provider._send_live_unavailable(AdmissionReason.RECONNECT_STORM)

        conn.google_live_session_started_at = time.monotonic() - 1
        conn.google_live_session_resumption_handle = "handle-1"
        conn.live_admission_gate = _AsyncGate(SimpleNamespace(decision=AdmissionDecision.ALLOW_LIVE, reason=AdmissionReason.OK))
        conn.live_resumption_store = _Store()
        await provider._record_live_session_usage()
        self.assertEqual(conn.live_resumption_store.saved, [("device-1", "handle-1")])
        self.assertEqual(len(conn.live_admission_gate.usage), 1)

        conn.google_live_session_started_at = time.monotonic() - 1
        conn.live_admission_gate = _Gate(SimpleNamespace(decision=AdmissionDecision.ALLOW_LIVE, reason=AdmissionReason.OK))
        conn.live_resumption_store = None
        await provider._record_live_session_usage()
        self.assertEqual(len(conn.live_admission_gate.usage), 1)

        self.assertFalse(provider._schedule_idle_close_task())
        provider._client = _Client()
        self.assertFalse(await provider._close_if_idle_once(1))
        conn.last_live_activity_at = time.monotonic() - 2
        conn.google_live_session_started_at = time.monotonic() - 2
        closed = []

        async def _close_resources():
            closed.append(True)
            provider._client = None

        dormant = []

        async def _enter_dormant_mode(reason):
            dormant.append(reason)

        provider._close_live_resources = _close_resources
        conn.enter_dormant_mode = _enter_dormant_mode
        self.assertTrue(await provider._close_if_idle_once(1))
        self.assertEqual(dormant, ["idle_timeout"])

    async def test_interrupt_flush_and_finalize_guard_edges(self):
        conn = _Conn()
        provider = self.make_provider(conn)
        provider._forced_interrupt_flush_generation = 2
        await provider._flush_interrupt_input_after_delay(0, 1, 0, "old")

        await provider._flush_interrupt_input_after_delay(0, 2, 999, "drift")
        provider._closing = True
        await provider._flush_interrupt_input_after_delay(0, 2, 0, "closing")
        provider._closing = False

        scheduled = []
        provider._interrupt_input_can_finalize = lambda: False
        provider._schedule_forced_interrupt_input_flush = lambda reason: scheduled.append(reason)
        await provider._flush_interrupt_input_after_delay(0, 2, 0, "tail")
        self.assertEqual(scheduled, ["tail"])

        provider._interrupt_input_can_finalize = lambda: True
        provider._client = None
        await provider._flush_interrupt_input_after_delay(0, 2, 0, "missing")
        provider._client = _Client()
        provider._client.connected = False
        await provider._flush_interrupt_input_after_delay(0, 2, 0, "disconnected")

        errors = []

        async def _runtime_failure(exc):
            errors.append(str(exc))

        provider._handle_runtime_failure = _runtime_failure
        provider._client = _Client()
        provider._client.end_audio_stream = lambda: (_ for _ in ()).throw(RuntimeError("flush failed"))
        await provider._flush_interrupt_input_after_delay(0, 2, 0, "error")
        self.assertEqual(errors, ["flush failed"])

        provider._input_flush_generation = 3
        await provider._flush_input_after_idle(0, 2)
        provider._closing = True
        await provider._flush_input_after_idle(0, 3)
        provider._closing = False
        provider._client = None
        await provider._flush_input_after_idle(0, 3)
        provider._client = _Client()
        provider._client.connected = False
        await provider._flush_input_after_idle(0, 3)
        provider._client = _Client()
        provider._client.end_audio_stream = lambda: (_ for _ in ()).throw(RuntimeError("idle failed"))
        await provider._flush_input_after_idle(0, 3)
        self.assertEqual(errors[-1], "idle failed")

    async def test_text_lifecycle_runtime_and_config_edge_branches(self):
        conn = _Conn()
        provider = self.make_provider(conn)
        provider._client = _Client()
        provider._bridge = _Bridge()

        async def _true(_text):
            return True

        async def _false(_text):
            return False

        provider._dispatch_lesson_child_response = _true
        self.assertTrue(await provider.handle_text_message('{"type":"text","text":"answer"}'))
        provider._dispatch_lesson_child_response = _false
        provider._dispatch_lesson_start_intent = _true
        self.assertTrue(await provider.handle_text_message('{"type":"text","text":"lesson"}'))
        provider._dispatch_lesson_start_intent = _false
        provider._dispatch_music_control_intent = _true
        self.assertTrue(await provider.handle_text_message('{"type":"text","text":"music"}'))

        errors = []

        async def _runtime_failure(exc):
            errors.append(str(exc))

        async def _send_text_error(_text):
            raise RuntimeError("send text failed")

        provider._dispatch_music_control_intent = _false
        provider._handle_runtime_failure = _runtime_failure
        provider._client.send_text = _send_text_error
        self.assertTrue(await provider.handle_text_message('{"type":"text","text":"hello"}'))
        self.assertEqual(errors, ["send text failed"])

        self.assertFalse(provider._lesson_runtime_accepts_voice_input())
        conn.lesson_runtime = SimpleNamespace(
            state="running",
            _step=object(),
            _step_id="s1",
            _step_passive=False,
            _step_completed=False,
        )
        self.assertTrue(provider._lesson_runtime_accepts_voice_input())

        provider._closing = True
        provider._schedule_proactive_reconnect({"time_left_ms": 1})
        provider._closing = False
        proactive_errors = []

        async def _proactive_failure(_exc):
            proactive_errors.append(True)
            raise RuntimeError("proactive failed")

        provider._handle_runtime_failure = _proactive_failure
        await provider._proactive_reconnect({"time_left_ms": 10})
        self.assertEqual(proactive_errors, [True])

        provider._handle_runtime_failure = _runtime_failure
        provider._fallback_provider = _Fallback()
        await provider._handle_runtime_failure(RuntimeError("ignored"))
        provider._fallback_provider = None

        conn.config["voice_mode"] = {"fallback_to_classic_on_error": False}
        await provider._activate_classic_fallback(RuntimeError("no fallback"))
        self.assertIsNone(provider._fallback_provider)
        conn.config["voice_mode"] = {"fallback_to_classic_on_error": True}
        await provider._activate_classic_fallback(RuntimeError("still no fallback"))
        self.assertIsNone(provider._fallback_provider)

        async def _open_failure():
            raise RuntimeError("open failed")

        provider._open_live_session = _open_failure
        provider._close_live_resources = lambda: _false(None)
        self.assertFalse(await provider._ensure_live_open_for_audio())

    async def test_idle_loop_aec_resumption_and_reconnect_accounting_edges(self):
        conn = _Conn()
        provider = self.make_provider(conn)

        conn.config["google_live"]["idle_timeout_sec"] = "bad"
        self.assertEqual(provider._idle_timeout_sec(), 45.0)
        conn.session_mode = SessionMode.CONVERSATION
        provider._idle_close_task = object()
        provider._schedule_idle_close_task()
        provider._idle_close_task = None
        conn.config["google_live"]["idle_timeout_sec"] = 0
        provider._schedule_idle_close_task()
        self.assertIsNone(provider._idle_close_task)

        provider._client = _Client()

        async def _close_once(_timeout):
            return True

        provider._close_if_idle_once = _close_once
        await provider._idle_close_loop(0)

        async def _close_once_error(_timeout):
            raise RuntimeError("idle loop failed")

        provider._client = _Client()
        provider._close_if_idle_once = _close_once_error
        await provider._idle_close_loop(0)

        conn.config["google_live"].update({"aec_enabled": True})
        provider._bridge = SimpleNamespace(_aec_processor=SimpleNamespace(bypassed=True, reason="disabled"))
        with self.assertRaisesRegex(RuntimeError, "disabled"):
            provider._ensure_required_aec_ready()

        provider._resolve_functions_for_live = lambda: [{"name": "tool"}]
        conn.google_live_session_resumption_handle = "resume-1"
        cfg = provider._get_live_config_with_functions()
        self.assertEqual(cfg["session_resumption_handle"], "resume-1")
        self.assertIn("system_prompt", cfg)

        conn.google_live_session_resumption_handle = None
        conn.live_resumption_store = _Store(handle=None)
        self.assertFalse(await provider._restore_session_resumption_handle())
        conn.device_id = None
        self.assertFalse(await provider._restore_session_resumption_handle())
        conn.device_id = "device-1"

        class _FailAsyncReconnectGate:
            async def record_reconnect_async(self, _device_id):
                raise RuntimeError("async reconnect failed")

        conn.live_admission_gate = _FailAsyncReconnectGate()
        await provider._record_reconnect_attempt()

        class _FailSyncReconnectGate:
            def record_reconnect(self, _device_id):
                raise RuntimeError("sync reconnect failed")

        conn.live_admission_gate = _FailSyncReconnectGate()
        await provider._record_reconnect_attempt()

        class _NoReconnectGate:
            pass

        conn.live_admission_gate = _NoReconnectGate()
        await provider._record_reconnect_attempt()

    async def test_lesson_music_tts_and_audio_gate_edge_branches(self):
        conn = _Conn()
        provider = self.make_provider(conn)
        provider._client = _Client()
        provider._bridge = _Bridge()

        conn.session_mode = SessionMode.LESSON
        conn.lesson_runtime = SimpleNamespace(
            state="running",
            _step=object(),
            _step_id="s1",
            _step_passive=False,
            _step_completed=False,
        )
        conn.config["google_live"]["lesson_child_response_window_sec"] = "bad"
        self.assertEqual(provider._get_user_audio_window_sec("listen_start"), 25.0)

        provider._classify_lesson_start_intent = lambda _text: {"name": "start_lesson", "arguments": {}}
        conn.func_handler = None
        self.assertFalse(await provider._dispatch_lesson_start_intent("start"))
        conn.func_handler = _FuncHandler(fail=True)
        self.assertFalse(await provider._dispatch_lesson_start_intent("start"))

        provider._client = None
        conn.voice_consent_client = _Consent(False)
        self.assertFalse(await provider._send_lesson_start_ack(SimpleNamespace(action=Action.ERROR, response="", result="")))
        self.assertFalse(await provider.speak_lesson_step_prompt("hello"))
        conn.voice_consent_client = _Consent(True)
        provider._client = _Client()
        provider._bridge = _Bridge()

        conn._music_session = SimpleNamespace()
        provider._classify_music_control_intent = lambda _text: {"name": "pause_music", "arguments": {}}
        conn.func_handler = None
        self.assertFalse(await provider._dispatch_music_control_intent("pause"))
        conn.func_handler = _FuncHandler(fail=True)
        self.assertFalse(await provider._dispatch_music_control_intent("pause"))
        provider._classify_music_control_intent = GoogleLiveProvider._classify_music_control_intent.__get__(provider, GoogleLiveProvider)
        self.assertIsNone(provider._classify_music_control_intent("khong ro"))
        self.assertIsNone(provider._extract_strict_music_title("phát bài   "))
        self.assertFalse(provider._is_wake_word_only(""))
        self.assertFalse(provider._is_local_stop_word(""))

        provider._get_live_config = lambda: {"echo_bypass_interrupt_enabled": False}
        self.assertFalse(provider._should_bypass_echo_gate_for_loud_user(provider._get_live_config(), 2000))
        provider._get_live_config = lambda: {
            "echo_bypass_interrupt_enabled": True,
            "robot_output_echo_bypass_rms_threshold": 1000,
            "robot_output_echo_bypass_min_duration_sec": 1.0,
            "input_frame_duration_ms": 10,
        }
        self.assertFalse(provider._should_bypass_echo_gate_for_loud_user(provider._get_live_config(), 2000))

        provider._get_live_config = lambda: {
            "barge_in": True,
            "barge_in_min_output_age_sec": "bad",
            "barge_in_rms_threshold": 1000,
            "barge_in_min_input_duration_sec": 1.0,
            "input_frame_duration_ms": 10,
        }
        conn.client_is_speaking = True
        conn.google_live_audio_out_started_at = time.monotonic()
        self.assertFalse(provider._should_barge_in(b"pcm"))
        conn.google_live_audio_out_started_at = time.monotonic() - 2
        provider._bridge.rms = 500
        self.assertFalse(provider._should_barge_in(b"pcm"))
        provider._bridge.rms = 2000
        self.assertFalse(provider._should_barge_in(b"pcm"))

        provider._get_live_config = lambda: {
            "barge_in": True,
            "interrupt_on_input_while_speaking": True,
        }
        self.assertFalse(provider._should_interrupt_for_input(b"pcm"))
        provider._get_live_config = lambda: {
            "barge_in": False,
            "interrupt_on_input_while_speaking": True,
            "interrupt_min_output_age_sec": "bad",
            "interrupt_rms_threshold": 1000,
            "interrupt_min_input_duration_sec": 1.0,
            "input_frame_duration_ms": 10,
        }
        conn.google_live_audio_out_started_at = time.monotonic()
        self.assertFalse(provider._should_interrupt_for_input(b"pcm"))
        conn.google_live_audio_out_started_at = time.monotonic() - 2
        provider._bridge.rms = 500
        self.assertFalse(provider._should_interrupt_for_input(b"pcm"))
        provider._bridge.rms = 2000
        self.assertFalse(provider._should_interrupt_for_input(b"pcm"))

        provider._get_live_config = lambda: {"drop_input_while_speaking": True, "barge_in": True}
        self.assertFalse(provider._should_drop_input_during_output())
        provider._get_live_config = lambda: {"drop_input_while_speaking": True, "barge_in": False}
        conn.google_live_audio_out_started_at = None
        self.assertFalse(provider._should_drop_input_during_output())
        provider._get_live_config = lambda: {"mute_input_after_audio_start_sec": "bad"}
        self.assertFalse(provider._should_drop_input_post_audio_start())
        provider._get_live_config = lambda: {"mute_input_after_audio_start_sec": 1}
        conn.google_live_audio_out_started_at = None
        self.assertFalse(provider._should_drop_input_post_audio_start())

        conn.client_abort = True
        conn.client_is_speaking = True
        async def _interrupt_stub():
            pass

        provider.interrupt = _interrupt_stub
        await provider._interrupt_for_barge_in()
        self.assertFalse(conn.client_abort)
        self.assertFalse(conn.client_is_speaking)

    async def test_tool_description_event_and_response_payload_edges(self):
        conn = _Conn()
        provider = self.make_provider(conn)

        import importlib
        import plugins_func.register as register_module

        original_registry = register_module.all_function_registry
        original_import_module = importlib.import_module
        try:
            register_module.all_function_registry = {
                "tool_a": SimpleNamespace(
                    description={"function": {"name": "tool_a", "description": "old"}}
                )
            }
            conn.config["plugins"] = {"tool_a": json.dumps({"description": "override"})}
            descriptions = provider._build_descriptions_for(["tool_a", "tool_a", "missing_tool"])
            self.assertEqual(descriptions[0]["function"]["description"], "override")

            def _import_module(name):
                if name == "plugins_func.register":
                    raise RuntimeError("register import failed")
                return original_import_module(name)

            saved_import_module = importlib.import_module
            importlib.import_module = _import_module
            try:
                self.assertIsNone(provider._build_descriptions_for(["tool_a"]))
            finally:
                importlib.import_module = saved_import_module
        finally:
            register_module.all_function_registry = original_registry

        original_product_tool_names = google_live_module.product_tool_names
        google_live_module.product_tool_names = lambda _conn: ["play_music", "tool_a"]
        provider._build_descriptions_for = lambda names: [{"names": names}]
        try:
            self.assertEqual(provider._resolve_functions_for_live(), [{"names": ["tool_a"]}])
        finally:
            google_live_module.product_tool_names = original_product_tool_names

        original_info = conn.logger.info
        conn.logger.info = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("log failed"))
        await provider._handle_tool_call_event({"calls": []})
        conn.logger.info = original_info

        async def _tool_payload(*_args, **_kwargs):
            return {"ok": True}

        provider._execute_tool_call_with_timeout = _tool_payload
        provider._client = None
        await provider._handle_tool_call_event({"calls": [{"id": "c1", "name": "x", "args": {}}]})
        provider._client = _Client()
        provider._client.send_tool_response = lambda _responses: (_ for _ in ()).throw(RuntimeError("tool send failed"))
        failures = []

        async def _runtime_failure(exc):
            failures.append(str(exc))

        provider._handle_runtime_failure = _runtime_failure
        await provider._handle_tool_call_event({"calls": [{"id": "c2", "name": "x", "args": {}}]})
        self.assertEqual(failures, ["tool send failed"])
        self.assertFalse(await provider._handle_tool_call_cancellation_event({"ids": []}))

        self.assertEqual(provider._format_tool_response_payload(None), {"result": ""})
        self.assertEqual(provider._format_tool_response_payload(SimpleNamespace(result="r"))["result"], "r")
        self.assertEqual(
            provider._format_tool_response_payload(SimpleNamespace(action=Action.NOTFOUND, response=""))["errorCode"],
            "TOOL_NOT_FOUND",
        )

    async def test_runtime_reconnect_and_low_level_audio_gate_edges(self):
        conn = _Conn()
        provider = self.make_provider(conn)
        provider._client = _Client()
        provider._bridge = _Bridge()

        conn.voice_consent_client = _Consent(False)
        self.assertTrue(await provider.handle_text_message('{"type":"text","text":"blocked"}'))
        self.assertTrue(await provider.handle_audio_bytes(b"blocked"))
        conn.voice_consent_client = _Consent(True)

        real_runtime_failure = GoogleLiveProvider._handle_runtime_failure.__get__(provider, GoogleLiveProvider)
        provider._fallback_provider = _Fallback()
        await real_runtime_failure(RuntimeError("already fallback"))
        provider._fallback_provider = None

        provider._get_reconnect_config = lambda: {"enabled": True, "max_retries": 1, "backoff_ms": 0, "backoff_multiplier": 1}
        provider._reconnect_attempts = 1
        self.assertFalse(await provider._try_reconnect(RuntimeError("network")))

        provider._get_interrupt_replay_buffer_capacity = lambda: 1
        self.assertEqual(provider._get_interrupt_replay_buffer_capacity(), 1)
        self.assertFalse(provider._buffer_pending_interrupt_audio(b""))

        provider._bridge = None
        self.assertIsNone(provider._input_rms(b"pcm"))
        provider._bridge = SimpleNamespace(input_rms=lambda _audio: (_ for _ in ()).throw(RuntimeError("rms failed")))
        self.assertIsNone(provider._input_rms(b"pcm"))

        provider._get_live_config = lambda: {
            "barge_in": True,
            "barge_in_min_output_age_sec": 0,
            "barge_in_rms_threshold": "bad",
            "barge_in_min_input_duration_sec": 0,
        }
        provider._bridge = _Bridge()
        provider._bridge.rms = 6000
        conn.client_is_speaking = True
        conn.google_live_audio_out_started_at = time.monotonic() - 1
        self.assertTrue(provider._should_barge_in(b"pcm"))
        self.assertTrue(provider._should_interrupt_for_input(b"pcm"))

        provider._bridge.input_rms = lambda _audio: (_ for _ in ()).throw(RuntimeError("rms failed"))
        self.assertFalse(provider._should_barge_in(b"pcm"))

        provider._get_live_config = lambda: {
            "barge_in": False,
            "interrupt_on_input_while_speaking": True,
            "interrupt_min_output_age_sec": 0,
            "interrupt_rms_threshold": 1000,
            "interrupt_min_input_duration_sec": 0,
        }
        provider._bridge.input_rms = lambda _audio: (_ for _ in ()).throw(RuntimeError("rms failed"))
        self.assertFalse(provider._should_interrupt_for_input(b"pcm"))
        conn.google_live_audio_out_started_at = None
        self.assertFalse(provider._should_interrupt_for_input(None))

        provider._get_live_config = lambda: {"drop_input_while_speaking": True, "barge_in": False}
        conn.google_live_audio_out_started_at = time.monotonic()
        self.assertTrue(provider._should_drop_input_during_output())
        provider._get_live_config = lambda: {"mute_input_after_audio_start_sec": 1}
        self.assertTrue(provider._should_drop_input_post_audio_start())

        provider._get_live_config = lambda: {"input_frame_duration_ms": "bad"}
        self.assertEqual(provider._get_input_frame_duration_sec(), 0.06)

    async def test_close_finalize_resumption_and_config_helper_edges(self):
        conn = _Conn()
        provider = self.make_provider(conn)

        provider._idle_close_task = asyncio.create_task(asyncio.sleep(10))
        await provider._close_live_resources()
        self.assertIsNone(provider._idle_close_task)

        class _FailStore:
            async def save(self, _device_id, _handle):
                raise RuntimeError("persist failed")

        conn.live_resumption_store = _FailStore()
        provider._schedule_session_resumption_persist("handle")
        await asyncio.sleep(0)

        import copy

        original_deepcopy = copy.deepcopy
        copy.deepcopy = lambda _value: (_ for _ in ()).throw(RuntimeError("copy failed"))
        try:
            source = {"x": []}
            self.assertIs(provider._clone_description(source), source)
        finally:
            copy.deepcopy = original_deepcopy

        provider._client = None
        await provider._finalize_user_audio_input("missing")
        provider._client = _Client()
        provider._client.connected = False
        await provider._finalize_user_audio_input("disconnected")

        provider._get_live_config = lambda: {
            "input_flush_delay_sec": "",
            "interrupt_forced_flush_delay_sec": -1,
            "interrupt_min_capture_ms": -10,
            "interrupt_speech_tail_ms": "bad",
            "interrupt_max_capture_ms": "bad",
            "robot_output_echo_bypass_rms_threshold": 500,
            "interrupt_speech_rms_threshold": "bad",
            "reconnect": [],
        }
        self.assertIsNone(provider._get_input_flush_delay())
        self.assertIsNone(provider._get_interrupt_forced_flush_delay())
        self.assertEqual(provider._get_interrupt_min_capture_sec(), 0.0)
        self.assertEqual(provider._get_interrupt_speech_tail_sec(), 0.24)
        self.assertEqual(provider._get_interrupt_max_capture_sec(), 1.2)
        self.assertGreaterEqual(provider._get_interrupt_speech_rms_threshold(), 1)
        self.assertEqual(provider._get_reconnect_config()["max_retries"], 0)

        provider._response_generation = 4
        provider._interrupt_capture_response_id = 3
        self.assertIsNone(provider._get_interrupt_finalization_delay())
        self.assertTrue(provider._interrupt_input_can_finalize())
        provider._get_live_config = lambda: {}
        provider._interrupt_capture_response_id = 4
        provider._interrupt_capture_started_at = None
        self.assertIsNotNone(provider._get_interrupt_finalization_delay())
        self.assertTrue(provider._interrupt_input_can_finalize())
        provider._interrupt_capture_started_at = time.monotonic()
        provider._interrupt_capture_last_speech_at = provider._interrupt_capture_started_at
        self.assertFalse(provider._interrupt_input_can_finalize())
        provider._clear_interrupt_capture_turn()
        self.assertIsNone(provider._interrupt_capture_response_id)

        self.assertEqual(provider.current_response_id(), 4)
        provider._cancelled_response_ids.add(4)
        self.assertTrue(provider.is_response_cancelled(4))

    async def test_transcript_echo_tool_and_reconnect_branch_edges(self):
        conn = _Conn()
        provider = self.make_provider(conn)
        provider._client = _Client()
        provider._bridge = _Bridge()

        async def _true(_text):
            return True

        async def _false(_text):
            return False

        provider._dispatch_lesson_child_response = _true
        self.assertIsNone(await provider._on_user_transcript_barge_in("child"))
        self.assertTrue(await provider._on_user_transcript("child"))
        provider._dispatch_lesson_child_response = _false
        provider._dispatch_lesson_start_intent = _true
        self.assertIsNone(await provider._on_user_transcript_barge_in("lesson"))
        self.assertTrue(await provider._on_user_transcript("lesson"))
        provider._dispatch_lesson_start_intent = _false
        provider._dispatch_music_control_intent = _true
        self.assertIsNone(await provider._on_user_transcript_barge_in("music"))
        self.assertTrue(await provider._on_user_transcript("music"))

        provider._get_live_config = lambda: {"suppress_robot_output_echo": False}
        self.assertFalse(provider._should_suppress_robot_output_echo(b"pcm"))
        provider._get_live_config = lambda: {"suppress_robot_output_echo": True}
        conn.google_live_echo_suppress_until = 0
        conn.google_live_audio_out_started_at = None
        conn._music_session = None
        self.assertFalse(provider._should_suppress_robot_output_echo(b"pcm"))
        conn.google_live_audio_out_started_at = time.monotonic()
        provider._bridge.input_rms = lambda _audio: (_ for _ in ()).throw(RuntimeError("rms failed"))
        self.assertTrue(provider._should_suppress_robot_output_echo(b"pcm"))
        self.assertEqual(provider._current_audio_suppression_reason(), "robot_speaking")
        self.assertEqual(provider._current_interaction_state_for_audio().value, "MODEL_SPEAKING")
        conn.google_live_audio_out_started_at = None
        conn._music_session = SimpleNamespace()
        self.assertEqual(provider._current_audio_suppression_reason(), "music_playing")
        self.assertEqual(provider._current_interaction_state_for_audio().value, "MUSIC_PLAYING")
        provider._reconnecting = True
        self.assertEqual(provider._current_interaction_state_for_audio().value, "RECONNECTING")
        provider._reconnecting = False

        provider._get_live_config = lambda: {"tool_timeout_sec": "bad"}
        self.assertEqual(provider._get_tool_timeout_sec(), 10.0)
        provider._get_live_config = lambda: {"tool_timeout_sec": 0}
        self.assertIsNone(provider._get_tool_timeout_sec())
        provider._get_live_config = lambda: {
            "dangerous_tool_names": object(),
            "dangerous_tool_name_pattern": "[",
        }
        self.assertFalse(provider._requires_tool_confirmation("ordinary", {}))

        original_info = conn.logger.info
        conn.logger.info = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("log failed"))
        conn.func_handler = _FuncHandler()
        result = await provider._execute_tool_call("tool", {})
        self.assertFalse(result["ok"])
        conn.logger.info = original_info

        provider._closing = True
        await provider._hard_reconnect_after_interrupt("closing")
        provider._closing = False
        reconnect_calls = []

        async def _close_resources():
            reconnect_calls.append("close")

        async def _open_session():
            reconnect_calls.append("open")

        provider._close_live_resources = _close_resources
        provider._open_live_session = _open_session
        await provider._hard_reconnect_after_interrupt("ok")
        self.assertEqual(reconnect_calls, ["close", "open"])

        async def _open_fail():
            raise RuntimeError("hard reconnect failed")

        failures = []

        async def _runtime_failure(exc):
            failures.append(str(exc))

        provider._open_live_session = _open_fail
        provider._handle_runtime_failure = _runtime_failure
        await provider._hard_reconnect_after_interrupt("fail")
        self.assertEqual(failures, ["hard reconnect failed"])

        provider._bridge = _FailingBridge()
        await provider._stop_live_output_for_transport_change()

        self.assertIsNone(provider._extract_user_text_message('{"type":"text","text":123}'))
        self.assertIsNone(provider._extract_user_text_message("[]"))

    async def test_final_provider_private_defensive_edges(self):
        conn = _Conn()
        provider = self.make_provider(conn)
        provider._client = _Client()
        provider._bridge = _Bridge()

        original_product_tool_names = google_live_module.product_tool_names
        google_live_module.product_tool_names = lambda _conn: (_ for _ in ()).throw(RuntimeError("resolve failed"))
        try:
            self.assertIsNone(provider._resolve_functions_for_live())
        finally:
            google_live_module.product_tool_names = original_product_tool_names

        self.assertEqual(provider._get_interrupt_replay_buffer_capacity(), 15)
        provider._get_live_config = lambda: {"interrupt_replay_buffer_ms": 0, "input_frame_duration_ms": 60}
        self.assertEqual(provider._get_interrupt_replay_buffer_capacity(), 1)
        provider._response_generation = 7
        provider._interrupt_capture_response_id = 7
        provider._record_interrupt_capture_audio(b"")

        provider._pending_interrupt_audio_response_id = 7
        provider._bridge = SimpleNamespace(is_model_output_blocked=lambda: (_ for _ in ()).throw(RuntimeError("blocked failed")))
        self.assertFalse(provider._buffer_pending_interrupt_audio_while_blocked(b"pcm"))
        self.assertFalse(provider._should_hold_interrupt_audio(b"pcm"))

        conn.config["google_live"] = []
        live_config = GoogleLiveProvider._get_live_config(provider)
        self.assertFalse(live_config["barge_in"])
        conn.config["google_live"] = {
            "robot_output_echo_bypass_rms_threshold": "bad",
            "robot_output_echo_bypass_min_duration_sec": "bad",
        }
        live_config = GoogleLiveProvider._get_live_config(provider)
        self.assertEqual(live_config["robot_output_echo_bypass_rms_threshold"], 650)
        self.assertEqual(live_config["robot_output_echo_bypass_min_duration_sec"], 0.06)
        conn.config["google_live"] = {"echo_bypass_interrupt_enabled": True}
        live_config = GoogleLiveProvider._get_live_config(provider)
        self.assertFalse(live_config["echo_bypass_interrupt_enabled"])

        conn.config["google_live"] = {
            "model": "gemini-live-test",
            "voice_name": "Aoede",
            "language_code": "en-US",
            "enable_audio_input": False,
            "enable_audio_output": False,
            "native_voice": False,
            "drop_input_while_speaking": True,
        }
        live_config = GoogleLiveProvider._get_live_config(provider)
        self.assertEqual(live_config["model"], "gemini-3.1-flash-live-preview")
        self.assertEqual(live_config["voice_name"], "Kore")
        self.assertEqual(live_config["language_code"], "vi-VN")
        self.assertTrue(live_config["enable_audio_input"])
        self.assertTrue(live_config["enable_audio_output"])
        self.assertTrue(live_config["native_voice"])
        self.assertFalse(live_config["drop_input_while_speaking"])

        conn.config["google_live"] = {"aec_enabled": False}
        live_config = GoogleLiveProvider._get_live_config(provider)
        self.assertTrue(live_config["aec_enabled"])

        conn.config["google_live"] = {
            "waiting_model_timeout_sec": 12.0,
            "interruption_min_output_age_sec": 2.0,
            "barge_in_transcript_min_output_age_sec": 2.0,
        }
        live_config = GoogleLiveProvider._get_live_config(provider)
        self.assertEqual(live_config["waiting_model_timeout_sec"], 2.0)
        self.assertEqual(live_config["interruption_min_output_age_sec"], 0.0)
        self.assertEqual(live_config["barge_in_transcript_min_output_age_sec"], 0.0)

        provider._get_live_config = lambda: {"wake_audio_allow_window_sec": "bad"}
        self.assertEqual(provider._get_wake_audio_allow_window_sec(), 5.0)
        self.assertIsNone(provider._classify_lesson_start_intent("không bắt đầu bài học"))
        self.assertIsNone(provider._classify_music_control_intent(""))
        self.assertIsNone(provider._extract_strict_music_title(""))

        conn._music_session = SimpleNamespace(pause=lambda: (_ for _ in ()).throw(RuntimeError("pause failed")))
        provider._get_live_config = lambda: {"music_auto_pause_on_user_speech": True}
        provider._auto_pause_music_for_interaction()
        conn._music_session = None
        self.assertEqual(provider._current_audio_suppression_reason(), "unknown")
        self.assertEqual(provider._current_interaction_state_for_audio().value, "USER_STREAMING")
        provider._bridge = SimpleNamespace(input_rms=lambda _audio: (_ for _ in ()).throw(RuntimeError("rms failed")))
        provider._log_audio_decision("forward", "accepted", b"pcm")

        provider._get_live_config = lambda: {
            "echo_bypass_interrupt_enabled": True,
            "robot_output_echo_bypass_rms_threshold": 1000,
        }
        self.assertFalse(provider._should_bypass_echo_gate_for_loud_user(provider._get_live_config(), 10))

        provider._get_live_config = lambda: {"tool_timeout_sec": 0}
        conn.func_handler = _FuncHandler()
        self.assertTrue((await provider._execute_tool_call_with_timeout("tool", {}))["ok"])
        self.assertEqual((await provider._execute_tool_call("tool", None))["result"], "ok")

        provider._get_live_config = lambda: {"interrupt_debounce_sec": "bad"}
        self.assertEqual(provider._get_interrupt_debounce_sec(), 0.2)
        provider._get_live_config = lambda: {}
        conn._music_session = SimpleNamespace()
        await provider._begin_user_interrupt("audio_input")
        conn._music_session = None
        provider._last_interrupt_at = time.monotonic()
        await provider._begin_user_interrupt("audio_input")
        provider._bridge = None
        provider._last_interrupt_at = 0
        await provider._begin_user_interrupt("explicit_interrupt")

        provider._get_live_config = lambda: {"barge_in": True}
        conn.client_is_speaking = False
        self.assertFalse(provider._should_barge_in(b"pcm"))
        conn.client_is_speaking = True
        conn.google_live_audio_out_started_at = None
        self.assertFalse(provider._should_barge_in(None))

    async def test_last_low_risk_provider_coverage_edges(self):
        conn = _Conn()
        provider = self.make_provider(conn)

        import sys
        import types

        module_name = "core.providers.tools.unified_tool_handler"
        saved_module = sys.modules.get(module_name)

        class _UnifiedToolHandler:
            def __init__(self, conn):
                self.conn = conn
                self.tool_manager = SimpleNamespace(refresh_tools=lambda: None)

            async def _initialize(self):
                self.initialized = True

            def get_functions(self):
                return []

        sys.modules[module_name] = types.SimpleNamespace(UnifiedToolHandler=_UnifiedToolHandler)
        try:
            conn.func_handler = None
            await provider._ensure_func_handler()
            self.assertTrue(conn.func_handler.initialized)
        finally:
            if saved_module is None:
                sys.modules.pop(module_name, None)
            else:
                sys.modules[module_name] = saved_module

        original_bridge = google_live_module.GoogleLiveAudioBridge

        class _FakeBridge(_Bridge):
            def __init__(self, *_args, **_kwargs):
                super().__init__()

        google_live_module.GoogleLiveAudioBridge = _FakeBridge
        conn.session_mode = SessionMode.DORMANT
        mode_changes = []
        conn._set_session_mode = lambda mode, reason=None: mode_changes.append((mode, reason))
        try:
            live_provider = self.make_provider(conn)
            live_provider._ensure_required_aec_ready = lambda: None
            await live_provider._open_live_session()
            self.assertEqual(mode_changes[-1], (SessionMode.CONVERSATION, "live_open"))
            await live_provider.close()

            conn.session_mode = SessionMode.LESSON
            mode_changes.clear()
            lesson_provider = self.make_provider(conn)
            lesson_provider._ensure_required_aec_ready = lambda: None
            await lesson_provider._open_live_session()
            self.assertEqual(mode_changes, [])
            self.assertEqual(conn.session_mode, SessionMode.LESSON)
            await lesson_provider.close()
        finally:
            google_live_module.GoogleLiveAudioBridge = original_bridge

        idle_provider = self.make_provider(_Conn())
        idle_provider._client = None
        self.assertTrue(await idle_provider._close_if_idle_once(1))
        idle_provider._client = _Client()
        self.assertFalse(await idle_provider._close_if_idle_once(1))
        idle_provider.conn.google_live_session_started_at = None
        self.assertFalse(await idle_provider._close_if_idle_once(1))
        task = asyncio.create_task(idle_provider._idle_close_loop(10))
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

        import plugins_func.register as register_module
        original_registry = register_module.all_function_registry
        try:
            register_module.all_function_registry = {
                "tool_a": SimpleNamespace(description={"function": {"name": "tool_a"}}),
                "tool_b": SimpleNamespace(description={"function": {"name": "tool_b"}}),
            }
            conn.config["plugins"] = {
                "tool_a": {"description": "mapping override"},
                "tool_b": "not-json",
            }
            descriptions = provider._build_descriptions_for(["tool_a", "tool_b"])
            self.assertEqual(descriptions[0]["function"]["description"], "mapping override")
        finally:
            register_module.all_function_registry = original_registry

        provider._pending_interrupt_audio_response_id = provider._response_generation
        provider._bridge = SimpleNamespace(is_model_output_blocked=lambda: False)
        self.assertFalse(provider._buffer_pending_interrupt_audio_while_blocked(b"pcm"))
        provider._get_live_config = lambda: {"input_flush_delay_sec": 0}
        self.assertIsNone(provider._get_input_flush_delay())
        provider._interrupt_capture_started_at = None
        self.assertEqual(provider._interrupt_capture_elapsed_ms(), 0.0)
        provider._lesson_start_ack_text = lambda _response: ""
        self.assertFalse(await provider._send_lesson_start_ack(None))
        provider._client = _Client()
        self.assertTrue(await provider.speak_lesson_step_prompt("prompt"))
        self.assertIsNone(provider._classify_lesson_start_intent("không bắt đầu bài học"))
        provider.conn._music_session = SimpleNamespace()
        self.assertIsNone(provider._classify_music_control_intent(None))
        provider.conn.config["voice_mode"] = []
        self.assertFalse(provider._should_fallback_to_classic())

        provider._get_live_config = lambda: {"input_frame_duration_ms": 10}
        self.assertFalse(provider._sustained_input_allows_interrupt({"x": "bad"}, "x", 0.03))
        self.assertFalse(provider._sustained_input_allows_interrupt({"x": 0.03}, "x", 0.03))

        class _FailOnceLogger(_Logger):
            def __init__(self):
                super().__init__()
                self.remaining = 1

            def info(self, *args, **kwargs):
                if self.remaining:
                    self.remaining -= 1
                    raise RuntimeError("log failed")
                super().info(*args, **kwargs)

        conn.logger = _FailOnceLogger()
        event_provider = self.make_provider(conn)

        async def _payload(*_args, **_kwargs):
            return {"ok": True}

        event_provider._execute_tool_call_with_timeout = _payload
        event_provider._client = _Client()
        await event_provider._handle_tool_call_event({"calls": [{"id": "c", "name": "tool", "args": {}}]})

        conn.logger = _Logger()
        conn.func_handler = _FuncHandler(result=SimpleNamespace(action=Action.RESPONSE, response="ok", result="ok"))
        self.assertEqual((await provider._execute_tool_call("tool", {}))["result"], "ok")

        provider._client = _Client()
        provider._bridge = _Bridge()
        await provider._flush_input_after_idle(0, provider._input_flush_generation)
        self.assertEqual(provider._bridge.flush_calls, 1)

        original_product_tool_names = google_live_module.product_tool_names
        google_live_module.product_tool_names = lambda _conn: ["start_lesson"]
        try:
            self.assertIsNone(provider._classify_lesson_start_intent("không bắt đầu bài học"))
        finally:
            google_live_module.product_tool_names = original_product_tool_names

        class _FailSecondInfoLogger(_Logger):
            def __init__(self):
                super().__init__()
                self.calls = 0

            def info(self, *args, **kwargs):
                self.calls += 1
                if self.calls == 2:
                    raise RuntimeError("result log failed")
                super().info(*args, **kwargs)

        conn.logger = _FailSecondInfoLogger()
        conn.func_handler = _FuncHandler()
        self.assertEqual((await provider._execute_tool_call("tool", {}))["result"], "ok")


    async def test_lesson_start_intent_accepts_accented_start_but_rejects_negative_aliases(self):
        conn = _Conn()
        provider = self.make_provider(conn)

        original_product_tool_names = google_live_module.product_tool_names
        google_live_module.product_tool_names = lambda _conn: ["start_lesson"]
        try:
            self.assertEqual(
                provider._classify_lesson_start_intent("Bắt đầu bài học nhé"),
                {"name": "start_lesson", "arguments": {}},
            )
            for transcript in (
                "con muốn bắt đầu bài học bây giờ",
                "cho con bắt đầu bài học nhé",
                "bat dau bai hoc",
                "bắt dầu bài học",
                "bắt đầu học bài",
                "học bài thôi",
                "học bài đi",
                "vào bài học",
                "vao bai hoc",
                "vào học bài",
                "vô bài học",
                "vo bai hoc",
                "mở bài học của con",
                "mo bai hoc cua con",
                "bắt đầu khoá học",
                "bắt đầu khóa học",
                "vào khóa học của con",
                "vào khoá học của con",
                "vao khoa hoc cua con",
                "mở khoá học",
                "học tiếp bài",
                "hoc tiep bai",
                "tiếp tục khóa học",
                "tiep tuc khoa hoc",
                "please start the lesson now",
            ):
                with self.subTest(transcript=transcript):
                    self.assertEqual(
                        provider._classify_lesson_start_intent(transcript),
                        {"name": "start_lesson", "arguments": {}},
                    )
            for transcript in (
                "đừng mở bài học",
                "đừng cho con mở bài học",
                "không mở khóa học",
                "không mở khoá học",
                "đừng học bài đi",
                "không vào học bài",
                "không vào khóa học",
                "không vào khoá học",
                "chưa vô bài học",
                "chưa vào khóa học",
                "chưa vào khoá học",
                "khoan vo bai hoc",
                "khoan vao khoa hoc",
                "không muốn bắt đầu bài học",
                "không cần bắt đầu khóa học",
                "không cần bắt đầu khoá học",
                "chưa vào bài học",
                "khoan tiếp tục bài học",
                "khoan tiếp tục khóa học",
                "don't start the lesson",
                "please do not start the lesson",
                "not ready to start lesson",
                "no start lesson yet",
                "never start lesson now",
                "cancel start lesson",
                "cancel open my lesson",
                "khi nào bắt đầu bài học",
                "bao giờ bắt đầu khóa học",
                "what time do we start the lesson",
                "làm sao bắt đầu bài học",
                "tại sao con phải bắt đầu bài học",
                "how do I start the lesson",
                "why start the lesson now",
                "cách bắt đầu bài học",
                "cho con biết cách bắt đầu bài học",
                "how to start the lesson",
                "steps to start the lesson",
                "lát nữa bắt đầu bài học",
                "ngày mai bắt đầu khóa học",
                "start the lesson later",
                "start lesson after breakfast",
                "nhắc con bắt đầu bài học",
                "nhắc con mở khóa học",
                "remind me to start the lesson",
                "tell me to start lesson",
                "bắt đầu bài học không phải bây giờ",
                "mở khóa học không phải bây giờ",
                "start lesson not now",
                "start the lesson no not now",
                "robot nói bắt đầu bài học",
                "cô giáo nói mở khóa học",
                "cô giáo bảo bắt đầu bài học",
                "the phrase start lesson means what",
                "teacher says start the lesson",
                "teacher said start the lesson",
                "teacher told me to start the lesson",
                "please say start the lesson",
                "hãy đọc bắt đầu bài học",
                "lặp lại mở khóa học",
                "repeat start the lesson",
                "say after me start the lesson",
            ):
                with self.subTest(transcript=transcript):
                    self.assertIsNone(provider._classify_lesson_start_intent(transcript))
        finally:
            google_live_module.product_tool_names = original_product_tool_names

if __name__ == "__main__":
    unittest.main()
