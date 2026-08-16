"""US-006 Slice-01 / LANE-ESP voice non-regression proof.

These tests prove the lesson_* control frames (lesson_ack / lesson_progress /
lesson_error) are NEVER intercepted by the realtime voice path. They must always
fall through ConnectionHandler._route_message to the classic text handler
(handleTextMessage) across:

  * the classic-pipeline provider route,
  * the no-voice-provider route,
  * the Google-Live native route,
  * the Google-Live route with an active classic fallback provider,

and that the realtime busy-guard pauses lesson asset work for active interaction
states while passive LISTENING remains preload-safe, plus that registry dispatch is
wired to TextMessageType.LESSON_*.

Importing ``test_connection_voice_provider_routing`` installs the sys.modules
import stubs (core.handle.textHandle.handleTextMessage -> async no-op, the
config.manage_api_client stub, etc.) and exposes the same ConnectionHandler /
ClassicPipelineProvider / connection_module the routing suite uses.
"""

import asyncio
import unittest
from contextvars import ContextVar
from types import SimpleNamespace

# Importing this module runs _install_connection_import_stubs() at import time,
# which installs the sys.modules stubs and imports core.connection cleanly.
import tests.test_connection_voice_provider_routing as routing  # noqa: F401

from core.connection import ConnectionHandler
import core.connection as connection_module
from core.voice.session_provider.classic_pipeline import ClassicPipelineProvider
from core.voice.session_provider.google_live import GoogleLiveProvider
from core.voice.google_live.interaction_controller import InteractionState

from core.handle.textHandler.lessonMessageHandler import (
    LessonAckHandler,
    LessonProgressHandler,
    LessonErrorHandler,
)
from core.handle.textMessageType import TextMessageType
from core.handle.textMessageProcessor import TextMessageProcessor


# Frozen-shape sample envelopes (one per lesson_* type). None of these are
# listen/text/chat/input, so the voice provider must always return falsy.
LESSON_ACK_JSON = (
    '{"type":"lesson_ack","protocolVersion":"teebot-lesson-renderer.v1",'
    '"assignmentId":"a","sessionId":"s","lessonId":"w01-d01-barn-say-it",'
    '"lessonVersion":3,"stepId":"s4","sequence":3,"timestamp":1,'
    '"body":{"acks":3,"rendered":true,"degraded":false}}'
)
LESSON_PROGRESS_JSON = (
    '{"type":"lesson_progress","protocolVersion":"teebot-lesson-renderer.v1",'
    '"assignmentId":"a","sessionId":"s","lessonId":"w01-d01-barn-say-it",'
    '"lessonVersion":3,"stepId":"s4","sequence":2,"timestamp":1,'
    '"body":{"event":"step_completed"}}'
)
LESSON_ERROR_JSON = (
    '{"type":"lesson_error","protocolVersion":"teebot-lesson-renderer.v1",'
    '"assignmentId":"a","sessionId":"s","lessonId":"w01-d01-barn-say-it",'
    '"lessonVersion":3,"stepId":"s4","sequence":1,"timestamp":1,'
    '"body":{"code":"STEP_TIMEOUT","message":"x"}}'
)
LESSON_JSONS = (LESSON_ACK_JSON, LESSON_PROGRESS_JSON, LESSON_ERROR_JSON)


class _RecordingClient:
    """Minimal GoogleLiveClient stand-in (never connected in these tests)."""

    def __init__(self):
        self.text_calls = []
        self.connected = False

    async def connect(self):
        self.connected = True

    async def send_audio(self, audio_bytes):
        return None

    async def end_audio_stream(self):
        return None

    async def close(self):
        self.connected = False

    async def receive_events(self):
        if False:  # pragma: no cover - async generator with no events
            yield None

    async def interrupt(self):
        return None

    async def send_text(self, text):
        self.text_calls.append(text)
        return None


def _build_handler():
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


class _FakeInteraction:
    def __init__(self, state):
        self.state = state


class _FakeStateProvider:
    """voice_provider exposing only ._interaction.state (no _fallback_provider)."""

    def __init__(self, state):
        self._interaction = _FakeInteraction(state)


class _RecordingRuntime:
    def __init__(self):
        self.calls = []

    async def on_lesson_ack(self, msg_json):
        self.calls.append(("on_lesson_ack", msg_json))

    async def on_lesson_progress(self, msg_json):
        self.calls.append(("on_lesson_progress", msg_json))

    async def on_lesson_error(self, msg_json):
        self.calls.append(("on_lesson_error", msg_json))

class _ClosableRuntime(_RecordingRuntime):
    def __init__(self):
        super().__init__()
        self.closed = False

    async def close(self):
        self.closed = True


class _FakeConnNoRuntime:
    lesson_runtime = None

    def __init__(self):
        # logger.bind(tag=...).debug(...) must not blow up the no-op path.
        self.logger = _FakeLogger()


class _FakeBoundLogger:
    def debug(self, *args, **kwargs):
        return None

    def info(self, *args, **kwargs):
        return None

    def error(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None


class _FakeLogger:
    def bind(self, **kwargs):
        return _FakeBoundLogger()


class _FakeConnWithRuntime:
    def __init__(self, runtime):
        self.lesson_runtime = runtime
        self.logger = _FakeLogger()


class _ProcessorFakeConn:
    def __init__(self):
        self.logger = _FakeLogger()


class _FakeRegistry:
    """Minimal registry mirroring tests/test_text_message_processor_logging.py.

    Maps a lesson message_type.value -> the matching lesson handler instance.
    """

    def __init__(self, handlers):
        self._by_type = {h.message_type.value: h for h in handlers}

    def get_handler(self, message_type):
        return self._by_type.get(message_type)


class LessonVoiceNonRegressionTest(unittest.IsolatedAsyncioTestCase):
    def test_lesson_sd_sync_ignores_only_start_lesson_control_dispatch_busy_state(self):
        handler = _build_handler()
        provider = _FakeStateProvider(InteractionState.INTERRUPTING)
        provider._response_generation = 3
        provider._interaction.response_id = 5
        handler.voice_provider = provider
        handler._lesson_start_tool_dispatch_context = ContextVar(
            "test_lesson_start_dispatch", default=None
        )
        handler._lesson_start_tool_dispatch_context.set(
            {
                "providerId": id(provider),
                "responseGeneration": 3,
                "responseId": 5,
            }
        )

        self.assertTrue(handler.is_realtime_busy())
        self.assertFalse(handler.is_lesson_sd_sync_busy())

        handler.voice_provider._interaction.state = InteractionState.WAITING_MODEL
        self.assertFalse(handler.is_lesson_sd_sync_busy())

        handler.voice_provider._interaction.state = InteractionState.MODEL_SPEAKING
        self.assertTrue(handler.is_lesson_sd_sync_busy())

    def test_lesson_sd_sync_still_blocks_actual_audio_during_start_lesson_dispatch(self):
        handler = _build_handler()
        provider = _FakeStateProvider(InteractionState.INTERRUPTING)
        provider._response_generation = 3
        provider._interaction.response_id = 5
        handler.voice_provider = provider
        handler._lesson_start_tool_dispatch_context = ContextVar(
            "test_lesson_start_audio", default=None
        )
        handler._lesson_start_tool_dispatch_context.set(
            {
                "providerId": id(provider),
                "responseGeneration": 3,
                "responseId": 5,
            }
        )

        for signal, value in (
            ("client_is_speaking", True),
            ("client_have_voice", True),
            ("_lesson_asset_audio_inflight", 1),
        ):
            setattr(handler, signal, value)
            self.assertTrue(handler.is_lesson_sd_sync_busy(), signal)
            self.assertTrue(
                handler.is_lesson_sd_sync_busy(start_lesson_dispatch=True),
                signal,
            )
            setattr(handler, signal, False if isinstance(value, bool) else 0)

        handler.voice_provider._interaction.state = InteractionState.MODEL_SPEAKING
        self.assertTrue(
            handler.is_lesson_sd_sync_busy(start_lesson_dispatch=True)
        )
        handler.voice_provider._interaction.state = InteractionState.INTERRUPTING
        handler._lesson_start_tool_dispatch_context.set(None)
        self.assertTrue(handler.is_lesson_sd_sync_busy())

    def test_lesson_sd_sync_rejects_start_lesson_token_after_response_generation_changes(self):
        handler = _build_handler()
        provider = _FakeStateProvider(InteractionState.WAITING_MODEL)
        provider._response_generation = 7
        provider._interaction.response_id = 11
        handler.voice_provider = provider
        admission = {
            "providerId": id(provider),
            "responseGeneration": 7,
            "responseId": 11,
        }

        self.assertFalse(
            handler.is_lesson_sd_sync_busy(start_lesson_admission=admission)
        )

        provider._interaction.response_id = 12
        self.assertTrue(
            handler.is_lesson_sd_sync_busy(start_lesson_admission=admission)
        )

    def test_lesson_sd_sync_preserves_token_owned_by_pending_spoken_start(self):
        handler = _build_handler()
        provider = _FakeStateProvider(InteractionState.INTERRUPTING)
        provider._response_generation = 8
        provider._interaction.response_id = 12
        handler.voice_provider = provider
        admission = {
            "providerId": id(provider),
            "responseGeneration": 7,
            "responseId": 11,
        }
        pending = SimpleNamespace(done=lambda: False)
        handler.lesson_pull_task = pending
        handler.lesson_pull_task_origin = "spoken_start"
        handler.lesson_pull_task_admission = admission

        self.assertFalse(
            handler.is_lesson_sd_sync_busy(start_lesson_admission=admission)
        )
        handler.client_have_voice = True
        self.assertTrue(
            handler.is_lesson_sd_sync_busy(start_lesson_admission=admission)
        )
        handler.client_have_voice = False
        pending.done = lambda: True
        self.assertTrue(
            handler.is_lesson_sd_sync_busy(start_lesson_admission=admission)
        )

    def test_pending_spoken_start_token_still_blocks_unrelated_model_speech(self):
        handler = _build_handler()
        provider = _FakeStateProvider(InteractionState.MODEL_SPEAKING)
        provider._response_generation = 8
        provider._interaction.response_id = 12
        handler.voice_provider = provider
        admission = {
            "providerId": id(provider),
            "responseGeneration": 7,
            "responseId": 11,
        }
        handler.lesson_pull_task = SimpleNamespace(done=lambda: False)
        handler.lesson_pull_task_origin = "spoken_start"
        handler.lesson_pull_task_admission = admission

        self.assertTrue(
            handler.is_lesson_sd_sync_busy(start_lesson_admission=admission)
        )

    def test_pending_spoken_start_token_rejects_unrelated_model_wait(self):
        handler = _build_handler()
        provider = _FakeStateProvider(InteractionState.WAITING_MODEL)
        provider._response_generation = 8
        provider._interaction.response_id = 12
        handler.voice_provider = provider
        admission = {
            "providerId": id(provider),
            "responseGeneration": 7,
            "responseId": 11,
        }
        handler.lesson_pull_task = SimpleNamespace(done=lambda: False)
        handler.lesson_pull_task_origin = "spoken_start"
        handler.lesson_pull_task_admission = admission

        self.assertTrue(
            handler.is_lesson_sd_sync_busy(start_lesson_admission=admission)
        )

    # ---- Route 0: lesson control frames bypass early bind wait -----------
    async def test_route_message_lesson_control_bypasses_pending_bind_gate(self):
        handler = _build_handler()
        self.assertFalse(handler.bind_completed_event.is_set())

        recorded = []
        discarded = []
        original_handle_text = connection_module.handleTextMessage

        async def record_handle_text(conn, message):
            recorded.append(message)

        async def record_discard():
            discarded.append("discard")

        handler._discard_message_with_bind_prompt = record_discard
        try:
            connection_module.handleTextMessage = record_handle_text
            for lesson_json in LESSON_JSONS:
                await handler._route_message(lesson_json)
        finally:
            connection_module.handleTextMessage = original_handle_text

        self.assertEqual(recorded, list(LESSON_JSONS))
        self.assertEqual(discarded, [])

    # ---- Route 1: classic pipeline provider falls through ----------------
    async def test_route_message_classic_pipeline_falls_through(self):
        handler = _build_handler()
        handler.bind_completed_event.set()
        handler.voice_provider = ClassicPipelineProvider(handler)

        recorded = []
        original_handle_text = connection_module.handleTextMessage

        async def record_handle_text(conn, message):
            recorded.append(message)

        try:
            connection_module.handleTextMessage = record_handle_text
            for lesson_json in LESSON_JSONS:
                await handler._route_message(lesson_json)
        finally:
            connection_module.handleTextMessage = original_handle_text

        self.assertEqual(recorded, list(LESSON_JSONS))

    # ---- Route 2: no voice provider falls through ------------------------
    async def test_route_message_voice_provider_none_falls_through(self):
        handler = _build_handler()
        handler.bind_completed_event.set()
        handler.voice_provider = None

        recorded = []
        original_handle_text = connection_module.handleTextMessage

        async def record_handle_text(conn, message):
            recorded.append(message)

        try:
            connection_module.handleTextMessage = record_handle_text
            for lesson_json in LESSON_JSONS:
                await handler._route_message(lesson_json)
        finally:
            connection_module.handleTextMessage = original_handle_text

        self.assertEqual(recorded, list(LESSON_JSONS))

    # ---- Route 3: Google Live native provider falls through --------------
    async def test_route_message_google_live_native_falls_through(self):
        handler = _build_handler()
        handler.bind_completed_event.set()
        provider = GoogleLiveProvider(
            handler,
            client_factory=lambda config, logger: _RecordingClient(),
        )
        handler.voice_provider = provider

        # Native (no fallback, no session): lesson_* must be falsy.
        for lesson_json in LESSON_JSONS:
            self.assertFalse(await provider.handle_text_message(lesson_json))

        recorded = []
        original_handle_text = connection_module.handleTextMessage

        async def record_handle_text(conn, message):
            recorded.append(message)

        try:
            connection_module.handleTextMessage = record_handle_text
            for lesson_json in LESSON_JSONS:
                await handler._route_message(lesson_json)
        finally:
            connection_module.handleTextMessage = original_handle_text

        self.assertEqual(recorded, list(LESSON_JSONS))

    # ---- Route 4: Google Live with active classic fallback --------------
    async def test_route_message_google_live_with_active_fallback_falls_through(self):
        handler = _build_handler()
        handler.bind_completed_event.set()
        provider = GoogleLiveProvider(
            handler,
            client_factory=lambda config, logger: _RecordingClient(),
        )
        # When a fallback is active, handle_text_message delegates to it; the
        # classic fallback always returns False for lesson_* too.
        provider._fallback_provider = ClassicPipelineProvider(handler)
        handler.voice_provider = provider

        for lesson_json in LESSON_JSONS:
            self.assertFalse(await provider.handle_text_message(lesson_json))

        recorded = []
        original_handle_text = connection_module.handleTextMessage

        async def record_handle_text(conn, message):
            recorded.append(message)

        try:
            connection_module.handleTextMessage = record_handle_text
            for lesson_json in LESSON_JSONS:
                await handler._route_message(lesson_json)
        finally:
            connection_module.handleTextMessage = original_handle_text

        self.assertEqual(recorded, list(LESSON_JSONS))

    # ---- Realtime guard: passive listening leaves preload runnable -------
    async def test_is_realtime_busy_allows_idle_and_passive_listening_states(self):
        handler = _build_handler()
        handler.client_is_speaking = False

        for member in InteractionState:
            handler.voice_provider = _FakeStateProvider(member)
            expected = member not in (InteractionState.IDLE, InteractionState.LISTENING)
            self.assertEqual(
                handler.is_realtime_busy(),
                expected,
                msg=f"state={member.name} expected busy={expected}",
            )

        # No provider + not speaking -> not busy.
        handler.voice_provider = None
        handler.client_is_speaking = False
        self.assertFalse(handler.is_realtime_busy())

        # client_is_speaking wins regardless of provider.
        handler.client_is_speaking = True
        self.assertTrue(handler.is_realtime_busy())

        # client_have_voice (VAD: child speaking now) also pauses, even on the
        # classic pipeline which has no interaction controller. This is the
        # transient signal used instead of the persistent client_listen_mode.
        handler.voice_provider = None
        handler.client_is_speaking = False
        handler.client_have_voice = True
        self.assertTrue(handler.is_realtime_busy())
        handler.client_have_voice = False
        self.assertFalse(handler.is_realtime_busy())

    async def test_persistent_listen_mode_without_voice_flag_does_not_pause_preload(self):
        handler = _build_handler()
        handler.voice_provider = None
        handler.client_is_speaking = False
        handler.client_have_voice = False

        # ``client_listen_mode`` is a persistent mode switch, not a transient
        # child-speaking signal. Preload must not pause for an entire session just
        # because classic/listen mode is configured.
        for listen_mode in ("auto", "manual", "continuous"):
            with self.subTest(listen_mode=listen_mode):
                handler.client_listen_mode = listen_mode
                self.assertFalse(handler.is_realtime_busy())

        # The actual transient voice flag still pauses preload on classic/no-live
        # connections with no interaction controller.
        handler.client_have_voice = True
        self.assertTrue(handler.is_realtime_busy())
        handler.client_have_voice = False
        self.assertFalse(handler.is_realtime_busy())

    # ---- Registry mapping: message_type.value ---------------------------
    async def test_lesson_handler_message_type_values(self):
        self.assertEqual(LessonAckHandler().message_type, TextMessageType.LESSON_ACK)
        self.assertEqual(LessonAckHandler().message_type.value, "lesson_ack")
        self.assertEqual(
            LessonProgressHandler().message_type, TextMessageType.LESSON_PROGRESS
        )
        self.assertEqual(
            LessonProgressHandler().message_type.value, "lesson_progress"
        )
        self.assertEqual(
            LessonErrorHandler().message_type, TextMessageType.LESSON_ERROR
        )
        self.assertEqual(LessonErrorHandler().message_type.value, "lesson_error")

    # ---- Handler dispatch + no-op when runtime missing ------------------
    async def test_lesson_handler_dispatch_and_noop(self):
        # No active runtime -> handle is a safe no-op (no exception).
        noop_conn = _FakeConnNoRuntime()
        await LessonAckHandler().handle(noop_conn, {"type": "lesson_ack"})
        await LessonProgressHandler().handle(noop_conn, {"type": "lesson_progress"})
        await LessonErrorHandler().handle(noop_conn, {"type": "lesson_error"})

        # Active runtime -> dispatch routes to the matching runtime method.
        runtime = _RecordingRuntime()
        conn = _FakeConnWithRuntime(runtime)
        ack_msg = {"type": "lesson_ack"}
        progress_msg = {"type": "lesson_progress"}
        error_msg = {"type": "lesson_error"}
        await LessonAckHandler().handle(conn, ack_msg)
        await LessonProgressHandler().handle(conn, progress_msg)
        await LessonErrorHandler().handle(conn, error_msg)

        self.assertEqual(
            runtime.calls,
            [
                ("on_lesson_ack", ack_msg),
                ("on_lesson_progress", progress_msg),
                ("on_lesson_error", error_msg),
            ],
        )

    async def test_lesson_handler_noop_tolerates_missing_or_broken_logger(self):
        # Missing runtime + no logger is a silent no-op.
        await LessonAckHandler().handle(object(), {"type": "lesson_ack"})

        class _BrokenLogger:
            def bind(self, **kwargs):
                raise RuntimeError("logger unavailable")

        class _Conn:
            lesson_runtime = None
            logger = _BrokenLogger()

        # Even logging failure must not escape into the websocket receive loop.
        await LessonAckHandler().handle(_Conn(), {"type": "lesson_ack"})

    async def test_lesson_handler_ignores_runtime_without_matching_method(self):
        class _RuntimeWithoutAck:
            pass

        await LessonAckHandler().handle(
            _FakeConnWithRuntime(_RuntimeWithoutAck()), {"type": "lesson_ack"}
        )

    async def test_lesson_handler_runtime_exception_is_logged_and_swallowed(self):
        warnings = []

        class _BoundLogger:
            def warning(self, message, **kwargs):
                warnings.append((message, kwargs))

        class _Logger:
            def bind(self, **kwargs):
                return _BoundLogger()

        class _Runtime:
            async def on_lesson_ack(self, msg_json):
                raise RuntimeError("lesson failed")

        conn = _FakeConnWithRuntime(_Runtime())
        conn.logger = _Logger()

        await LessonAckHandler().handle(conn, {"type": "lesson_ack"})

        self.assertEqual(len(warnings), 1)
        self.assertIn("lesson on_lesson_ack handler raised", warnings[0][0])
        self.assertIs(warnings[0][1]["exc_info"], True)

    async def test_lesson_handler_runtime_exception_tolerates_broken_warning_logger(self):
        class _BoundLogger:
            def warning(self, *args, **kwargs):
                raise RuntimeError("warning logger down")

        class _Logger:
            def bind(self, **kwargs):
                return _BoundLogger()

        class _Runtime:
            async def on_lesson_ack(self, msg_json):
                raise RuntimeError("lesson failed")

        conn = _FakeConnWithRuntime(_Runtime())
        conn.logger = _Logger()

        await LessonAckHandler().handle(conn, {"type": "lesson_ack"})

    # ---- S13: _disable_lesson_runtime flips the ESP flag off ------------------
    async def test_disable_lesson_runtime_flips_flag_off(self):
        handler = _build_handler()
        # Enable then auto-disable; the flag is the rollback lever (plan §11.2/§12.1).
        handler.device_id = "robot-rollback-test"
        handler.config["lesson"] = {
            "runtime_enabled": True,
            "rollout_device_allowlist": [handler.device_id],
        }
        self.assertTrue(handler._lesson_runtime_enabled())
        handler._disable_lesson_runtime()
        self.assertFalse(handler._lesson_runtime_enabled())
        self.assertIs(handler.config["lesson"]["runtime_enabled"], False)

    async def test_disable_lesson_runtime_creates_lesson_cfg_when_missing(self):
        handler = _build_handler()
        # No prior lesson block at all -> still safe, ends up explicitly disabled.
        handler.config.pop("lesson", None)
        handler._disable_lesson_runtime()
        self.assertFalse(handler._lesson_runtime_enabled())

    # ---- S13: note_voice_round_trip feeds the alarm, never the voice path ------
    async def test_note_voice_round_trip_feeds_alarm_and_is_safe_when_none(self):
        handler = _build_handler()
        # No alarm wired (dark) -> the hook is a no-op, never raises.
        handler.lesson_voice_alarm = None
        handler.note_voice_round_trip(1234.0)  # must not raise

        # With an alarm wired, the sample is forwarded verbatim.
        recorded = []

        class _Alarm:
            def record_round_trip(self, ms):
                recorded.append(ms)

        handler.lesson_voice_alarm = _Alarm()
        handler.note_voice_round_trip(987.0)
        self.assertEqual(recorded, [987.0])

        # A raising alarm hook is swallowed (voice hot path must never break).
        class _Boom:
            def record_round_trip(self, ms):
                raise RuntimeError("alarm down")

        handler.lesson_voice_alarm = _Boom()
        handler.note_voice_round_trip(500.0)  # must not raise

    # ---- S13: a tripped alarm wired to the real disable callback flips flag ----
    async def test_alarm_autodisable_end_to_end(self):
        from core.lesson.preload_voice_alarm import PreloadVoiceLatencyAlarm

        handler = _build_handler()
        handler.config["lesson"] = {"runtime_enabled": True}
        handler.lesson_pull_task = asyncio.create_task(asyncio.sleep(60))
        handler.lesson_runtime = _ClosableRuntime()
        alarm = PreloadVoiceLatencyAlarm(
            disable_callback=handler._disable_lesson_runtime,
            threshold_ms=1000.0,
            min_samples=3,
        )
        handler.lesson_voice_alarm = alarm
        alarm.set_preload_active(True)
        # Three regressed round-trips DURING the preload window -> auto-disable.
        for _ in range(3):
            handler.note_voice_round_trip(2000.0)
        self.assertTrue(alarm.tripped)
        self.assertFalse(handler._lesson_runtime_enabled())
        await asyncio.sleep(0)
        self.assertTrue(handler.lesson_pull_task.cancelled())
        self.assertIsNone(handler.lesson_runtime)
        self.assertTrue(alarm.tripped)

    # ---- Real TextMessageProcessor routes lesson_ack to the handler -----
    async def test_processor_routes_lesson_ack_to_handler(self):
        runtime = _RecordingRuntime()
        conn = _ProcessorFakeConn()
        conn.lesson_runtime = runtime

        registry = _FakeRegistry(
            [LessonAckHandler(), LessonProgressHandler(), LessonErrorHandler()]
        )
        processor = TextMessageProcessor(registry)
        await processor.process_message(conn, LESSON_ACK_JSON)

        self.assertEqual(len(runtime.calls), 1)
        method, msg_json = runtime.calls[0]
        self.assertEqual(method, "on_lesson_ack")
        self.assertEqual(msg_json["type"], "lesson_ack")
        self.assertEqual(msg_json["body"]["acks"], 3)


if __name__ == "__main__":
    unittest.main()
