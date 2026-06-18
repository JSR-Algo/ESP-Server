"""Tests for PR4 barge-in correctness: debounce, end_audio_stream guard,
unblock-timer lifecycle, and auto-unblock timeout."""

import asyncio
import json
import time
import unittest
from unittest.mock import patch

from core.voice.google_live.audio_bridge import GoogleLiveAudioBridge
from core.voice.session_provider.google_live import GoogleLiveProvider


class _Logger:
    def __init__(self):
        self.messages = []

    def bind(self, **_):
        return self

    def info(self, *args, **kwargs):
        self.messages.append(("info", args, kwargs))

    def warning(self, *args, **kwargs):
        self.messages.append(("warning", args, kwargs))

    def error(self, *args, **kwargs):
        self.messages.append(("error", args, kwargs))


class _WebSocket:
    def __init__(self):
        self.sent = []

    async def send(self, payload):
        self.sent.append(payload)


class _VoiceConsentClient:
    async def ensure_voice_allowed(self, _conn):
        return True


class _Conn:
    def __init__(self):
        self.config = {
            "voice_mode": {
                "type": "google_live",
                "fallback_to_classic_on_error": True,
            },
            "google_live": {
                "api_key": "test",
                "model": "gemini-live-test",
                "aec_enabled": False,
                "interrupt_debounce_sec": 0.2,
                "model_output_unblock_timeout_sec": 0.05,
            },
            "wakeup_words": ["hi esp"],
        }
        self.logger = _Logger()
        self.websocket = _WebSocket()
        self.sample_rate = 24000
        self.session_id = "s-1"
        self.client_abort = False
        self.client_is_speaking = False
        self.google_live_audio_out_started_at = None
        self.google_live_turn_started_at = None
        self.clear_queue_calls = 0
        self.voice_consent_client = _VoiceConsentClient()

    def clear_queues(self):
        self.clear_queue_calls += 1

    def clearSpeakStatus(self):
        self.client_is_speaking = False

    async def _start_classic_pipeline_session(self):
        pass


class _Client:
    def __init__(self, connected=True):
        self.connected = connected
        self.config = {}
        self.interrupt_calls = 0
        self.end_stream_calls = 0
        self.end_stream_raise = None
        self.connect_calls = 0
        self._post_connect_connected = connected
        self.sent_audio = []
        self.sent_text = []

    async def connect(self):
        self.connect_calls += 1
        self.connected = self._post_connect_connected

    async def interrupt(self):
        self.interrupt_calls += 1

    async def end_audio_stream(self):
        self.end_stream_calls += 1
        if self.end_stream_raise is not None:
            raise self.end_stream_raise

    async def send_audio(self, audio_bytes):
        self.sent_audio.append(audio_bytes)
        return None

    async def send_text(self, text):
        self.sent_text.append(text)
        return None

    async def receive_events(self):
        if False:  # pragma: no cover - empty async generator
            yield None

    async def close(self):
        self.connected = False


class _Controller:
    def __init__(self):
        self.reset_calls = 0

    def reset(self):
        self.reset_calls += 1


class InterruptDebounceTest(unittest.IsolatedAsyncioTestCase):
    async def test_clean_user_turn_records_latency_start_timestamp(self):
        conn = _Conn()
        provider = GoogleLiveProvider(conn, client_factory=lambda *_: _Client())

        provider._mark_clean_user_turn_opened("audio_input")
        first_started_at = conn.google_live_turn_started_at
        provider._mark_clean_user_turn_opened("audio_input")

        self.assertIsNotNone(first_started_at)
        self.assertEqual(conn.google_live_turn_started_at, first_started_at)

    async def test_rapid_audio_input_interrupts_are_debounced(self):
        conn = _Conn()
        client = _Client()
        provider = GoogleLiveProvider(
            conn, client_factory=lambda *_: client
        )
        await provider.start_session()
        await provider._begin_user_interrupt("audio_input")
        first_id = provider.current_response_id()
        await provider._begin_user_interrupt("audio_input")
        second_id = provider.current_response_id()
        await provider.close()

        self.assertEqual(first_id, second_id)
        self.assertEqual(client.interrupt_calls, 1)

    async def test_explicit_user_interrupt_is_not_debounced(self):
        conn = _Conn()
        client = _Client()
        provider = GoogleLiveProvider(
            conn, client_factory=lambda *_: client
        )
        await provider.start_session()
        await provider._begin_user_interrupt("explicit_interrupt")
        first_id = provider.current_response_id()
        await provider._begin_user_interrupt("explicit_interrupt")
        second_id = provider.current_response_id()
        await provider.close()

        self.assertGreater(second_id, first_id)
        self.assertEqual(client.interrupt_calls, 2)

    async def test_audio_input_interrupt_allowed_after_debounce_window(self):
        conn = _Conn()
        conn.config["google_live"]["interrupt_debounce_sec"] = 0.01
        client = _Client()
        provider = GoogleLiveProvider(
            conn, client_factory=lambda *_: client
        )
        await provider.start_session()
        await provider._begin_user_interrupt("audio_input")
        first_id = provider.current_response_id()
        await asyncio.sleep(0.02)
        await provider._begin_user_interrupt("audio_input")
        second_id = provider.current_response_id()
        await provider.close()

        self.assertGreater(second_id, first_id)


class EndAudioStreamGuardTest(unittest.IsolatedAsyncioTestCase):
    async def test_listen_stop_finalizes_audio_stream_without_idle_delay(self):
        conn = _Conn()
        client = _Client(connected=True)
        provider = GoogleLiveProvider(conn, client_factory=lambda *_: client)
        await provider.start_session()

        handled = await provider.handle_text_message(
            '{"type":"listen","state":"stop"}'
        )
        await provider.close()

        self.assertTrue(handled)
        self.assertEqual(client.end_stream_calls, 1)
        self.assertEqual(provider._interaction.state.value, "WAITING_MODEL")
        self.assertTrue(
            any(
                "input_finalized" in str(args[0]) and "listen_stop" in str(args)
                for _, args, _ in conn.logger.messages
            )
        )

    async def test_end_audio_stream_called_after_interrupt_when_connected(self):
        conn = _Conn()
        client = _Client(connected=True)
        provider = GoogleLiveProvider(
            conn, client_factory=lambda *_: client
        )
        await provider.start_session()
        await provider._begin_user_interrupt("audio_input")
        await provider.close()

        self.assertEqual(client.end_stream_calls, 1)

    async def test_end_audio_stream_skipped_when_client_disconnected(self):
        conn = _Conn()
        client = _Client(connected=False)
        provider = GoogleLiveProvider(
            conn, client_factory=lambda *_: client
        )
        await provider.start_session()
        await provider._begin_user_interrupt("audio_input")
        await provider.close()

        self.assertEqual(client.end_stream_calls, 0)

    async def test_end_audio_stream_runtime_error_is_swallowed(self):
        conn = _Conn()
        client = _Client(connected=True)
        client.end_stream_raise = RuntimeError("client not connected")
        provider = GoogleLiveProvider(
            conn, client_factory=lambda *_: client
        )
        await provider.start_session()
        await provider._begin_user_interrupt("audio_input")
        await provider.close()

        skipped_logs = [
            args
            for level, args, _ in conn.logger.messages
            if level == "info"
            and args
            and "end_audio_stream skipped" in str(args[0])
        ]
        self.assertTrue(skipped_logs)


    async def test_interrupt_hard_reconnect_when_enabled(self):
        conn = _Conn()
        conn.config["google_live"]["hard_reconnect_on_interrupt"] = True
        client = _Client(connected=True)
        provider = GoogleLiveProvider(conn, client_factory=lambda *_: client)
        await provider.start_session()

        await provider._begin_user_interrupt("explicit_interrupt")
        await provider.close()

        self.assertEqual(client.connect_calls, 2)
        self.assertTrue(
            any(
                "hard_reconnected_after_interrupt" in str(args[0])
                for _, args, _ in conn.logger.messages
            )
        )

    async def test_interrupt_hard_reconnect_disabled_by_default(self):
        conn = _Conn()
        client = _Client(connected=True)
        provider = GoogleLiveProvider(conn, client_factory=lambda *_: client)
        await provider.start_session()

        await provider._begin_user_interrupt("explicit_interrupt")
        await provider.close()

        self.assertEqual(client.connect_calls, 1)


class AecLiveAdmissionTest(unittest.IsolatedAsyncioTestCase):
    async def test_live_start_refuses_required_aec_when_processor_is_bypassed(self):
        conn = _Conn()
        conn.config["voice_mode"]["fallback_to_classic_on_error"] = False
        conn.config["google_live"]["aec_enabled"] = True
        client = _Client(connected=True)
        provider = GoogleLiveProvider(conn, client_factory=lambda *_: client)

        with patch("core.voice.aec.aec_processor.AEC_AVAILABLE", False):
            with self.assertRaisesRegex(RuntimeError, "AEC required.*bypassed"):
                await provider.start_session()

        self.assertEqual(client.connect_calls, 0)


class LocalStopWordTest(unittest.IsolatedAsyncioTestCase):
    async def test_local_stop_word_interrupts_output_without_forwarding_to_gemini(self):
        conn = _Conn()
        conn.client_is_speaking = True
        conn.google_live_audio_out_started_at = time.monotonic() - 1.0
        client = _Client(connected=True)
        provider = GoogleLiveProvider(conn, client_factory=lambda *_: client)
        await provider.start_session()

        handled = await provider.handle_text_message(
            json.dumps({"type": "listen", "state": "detect", "text": "dừng lại"})
        )
        await provider.close()

        self.assertTrue(handled)
        self.assertEqual(client.sent_text, [])
        self.assertEqual(client.interrupt_calls, 1)
        self.assertEqual(conn.clear_queue_calls, 1)
        self.assertTrue(
            any(
                json.loads(payload).get("state") == "stop"
                and json.loads(payload).get("reason") == "interrupt"
                for payload in conn.websocket.sent
            )
        )

    async def test_local_stop_word_matches_english_stop(self):
        provider = GoogleLiveProvider(_Conn(), client_factory=lambda *_: _Client())

        self.assertTrue(provider._is_local_stop_word("stop"))

    async def test_local_stop_word_clears_output_without_live_or_aec_bridge(self):
        conn = _Conn()
        conn.client_is_speaking = True
        conn.google_live_audio_out_started_at = time.monotonic() - 1.0
        client = _Client(connected=True)
        provider = GoogleLiveProvider(conn, client_factory=lambda *_: client)
        provider._client = client
        provider._bridge = None

        handled = await provider.handle_text_message(
            json.dumps({"type": "listen", "state": "detect", "text": "stop"})
        )

        self.assertTrue(handled)
        self.assertEqual(conn.clear_queue_calls, 1)
        self.assertFalse(conn.client_is_speaking)
        self.assertEqual(client.interrupt_calls, 1)


class UnblockTimerLifecycleTest(unittest.IsolatedAsyncioTestCase):
    def _bridge(self, conn):
        return GoogleLiveAudioBridge(conn, _Client(), _Logger())

    async def test_stop_output_timeout_does_not_unblock_without_user_turn(self):
        conn = _Conn()
        bridge = self._bridge(conn)
        await bridge.stop_output()
        self.assertIsNotNone(bridge._unblock_timer_task)
        await asyncio.sleep(0.08)
        self.assertTrue(bridge._block_model_output_until_user_ack)
        await bridge.close()


class EchoSuppressionPolicyTest(unittest.IsolatedAsyncioTestCase):
    def _bridge(self, conn):
        return GoogleLiveAudioBridge(conn, _Client(), _Logger())

    async def test_echo_tail_suppresses_mic_after_output_stop(self):
        conn = _Conn()
        conn.google_live_echo_suppress_until = time.monotonic() + 0.4
        client = _Client(connected=True)
        provider = GoogleLiveProvider(conn, client_factory=lambda *_: client)
        await provider.start_session()

        self.assertTrue(provider._should_suppress_robot_output_echo(b"\x00\x00" * 10))

        await provider.close()

    async def test_allow_model_output_cancels_pending_unblock_timer(self):
        conn = _Conn()
        conn.config["google_live"]["model_output_unblock_timeout_sec"] = 1.0
        bridge = self._bridge(conn)
        await bridge.stop_output()
        bridge.allow_model_output()
        self.assertIsNone(bridge._unblock_timer_task)
        self.assertFalse(bridge._block_model_output_until_user_ack)
        await bridge.close()

    async def test_close_cancels_unblock_timer(self):
        conn = _Conn()
        conn.config["google_live"]["model_output_unblock_timeout_sec"] = 5.0
        bridge = self._bridge(conn)
        await bridge.stop_output()
        timer = bridge._unblock_timer_task
        self.assertIsNotNone(timer)
        await bridge.close()
        await asyncio.sleep(0)
        self.assertTrue(timer.cancelled() or timer.done())

    async def test_zero_timeout_disables_auto_unblock(self):
        conn = _Conn()
        conn.config["google_live"]["model_output_unblock_timeout_sec"] = 0
        bridge = self._bridge(conn)
        await bridge.stop_output()
        await asyncio.sleep(0.02)
        self.assertTrue(bridge._block_model_output_until_user_ack)
        self.assertIsNone(bridge._unblock_timer_task)
        await bridge.close()

    async def test_stop_output_resets_live_audio_controller_without_classic_tts(self):
        conn = _Conn()
        conn.tts = None
        conn.audio_rate_controller = _Controller()
        bridge = self._bridge(conn)

        await bridge.stop_output()

        self.assertEqual(conn.audio_rate_controller.reset_calls, 1)
        await bridge.close()


class _CapturingBridge(GoogleLiveAudioBridge):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.display_messages = []
        self.binary_messages = []
        self.tts_states = []
        self.tool_events = []

    async def _send_display_message(self, text):
        self.display_messages.append(text)

    async def _send_binary_audio_message(self, audio_bytes, **_kwargs):
        self.binary_messages.append(audio_bytes)

    async def _send_tts_message(self, state):
        self.tts_states.append(state)

    async def _send_tts_stop_now(self):
        self.tts_states.append("stop_now")

    async def _flush_output_audio(self):
        return 0

class _RawInputCapturingBridge(_CapturingBridge):
    def decode_input_audio(self, audio_bytes):
        return audio_bytes

    async def decode_input_audio_async(self, audio_bytes):
        return audio_bytes


class TurnIsolationBarrierTest(unittest.IsolatedAsyncioTestCase):
    async def test_stale_model_events_are_dropped_after_interrupted_audio_end(self):
        conn = _Conn()
        conn.websocket = None
        cancelled = set()
        response_id = 0
        handled_tools = []

        async def handle_tool(event):
            handled_tools.append(event)

        bridge = _CapturingBridge(
            conn,
            _Client(),
            _Logger(),
            response_id_getter=lambda: response_id,
            response_cancelled_checker=lambda rid: rid in cancelled,
            tool_call_handler=handle_tool,
        )

        await bridge.handle_event({"type": "audio_start"})
        await bridge.handle_event(
            {"type": "transcript", "source": "model", "text": "old before"}
        )
        cancelled.add(response_id)
        response_id = 1
        await bridge.stop_output()
        await bridge.handle_event({"type": "audio_end"})
        await bridge.handle_event(
            {"type": "transcript", "source": "model", "text": "old leak"}
        )
        await bridge.handle_event({"type": "audio_chunk", "audio": b"\x00\x00"})
        await bridge.handle_event({"type": "tool_call", "calls": [{"name": "old"}]})
        await bridge.close()

        self.assertEqual(bridge.display_messages, ["old before"])
        self.assertEqual(bridge.binary_messages, [])
        self.assertEqual(handled_tools, [])
        self.assertTrue(
            any(
                "stale_model_event_dropped" in str(args[0])
                for _, args, _ in bridge.logger.messages
            )
        )

    async def test_clean_user_turn_reopens_output_after_interrupted_turn_drains(self):
        conn = _Conn()
        conn.websocket = None
        response_id = 0
        bridge = _CapturingBridge(
            conn,
            _Client(),
            _Logger(),
            response_id_getter=lambda: response_id,
            response_cancelled_checker=lambda _rid: False,
        )

        await bridge.handle_event({"type": "audio_start"})
        response_id = 1
        await bridge.stop_output()
        bridge.allow_model_output()
        await bridge.handle_event({"type": "audio_end"})
        await bridge.handle_event(
            {"type": "transcript", "source": "model", "text": "new answer"}
        )
        await bridge.close()

        self.assertEqual(bridge.display_messages, ["new answer"])
        self.assertFalse(bridge._block_model_output_until_user_ack)

    async def test_clean_user_turn_does_not_reopen_before_interrupted_turn_drains(self):
        conn = _Conn()
        conn.websocket = None
        conn.config["google_live"]["model_output_unblock_timeout_sec"] = 0.01
        response_id = 0
        bridge = _CapturingBridge(
            conn,
            _Client(),
            _Logger(),
            response_id_getter=lambda: response_id,
            response_cancelled_checker=lambda _rid: False,
        )

        await bridge.handle_event({"type": "audio_start"})
        response_id = 1
        await bridge.stop_output()
        bridge.allow_model_output()
        await asyncio.sleep(0.03)
        await bridge.handle_event(
            {"type": "transcript", "source": "model", "text": "old still leaking"}
        )
        await bridge.close()

        self.assertEqual(bridge.display_messages, [])
        self.assertTrue(bridge._block_model_output_until_user_ack)
        self.assertTrue(
            any(
                "model_output_still_blocked_waiting_user_turn" in str(args[0])
                for _, args, _ in bridge.logger.messages
            )
        )


class TranscriptBargeInTest(unittest.IsolatedAsyncioTestCase):
    def _bridge(self, conn, handler=None):
        # websocket=None so _send_display_message short-circuits without
        # touching the cross-test-poisoned sendAudioHandle import path.
        conn.websocket = None
        return GoogleLiveAudioBridge(
            conn,
            _Client(),
            _Logger(),
            user_transcript_barge_in_handler=handler,
        )

    async def test_user_transcript_fires_handler_when_model_speaking(self):
        conn = _Conn()
        conn.config["google_live"]["barge_in_via_transcript"] = True
        conn.config["google_live"]["barge_in_transcript_min_chars"] = 3
        # Backdate so model output is past the min_output_age guard.
        conn.google_live_audio_out_started_at = time.monotonic() - 5
        captured = []

        async def handler(text):
            captured.append(text)

        bridge = self._bridge(conn, handler)
        await bridge.handle_event({"type": "transcript", "source": "user", "text": "stop"})
        await bridge.close()
        self.assertEqual(captured, ["stop"])

    async def test_no_fire_when_feature_flag_off(self):
        conn = _Conn()
        # barge_in_via_transcript NOT set -> defaults False
        conn.google_live_audio_out_started_at = time.monotonic()
        captured = []

        async def handler(text):
            captured.append(text)

        bridge = self._bridge(conn, handler)
        await bridge.handle_event({"type": "transcript", "source": "user", "text": "stop"})
        await bridge.close()
        self.assertEqual(captured, [])

    async def test_no_fire_when_model_not_speaking(self):
        conn = _Conn()
        conn.config["google_live"]["barge_in_via_transcript"] = True
        conn.google_live_audio_out_started_at = None
        captured = []

        async def handler(text):
            captured.append(text)

        bridge = self._bridge(conn, handler)
        await bridge.handle_event({"type": "transcript", "source": "user", "text": "stop please"})
        await bridge.close()
        self.assertEqual(captured, [])

    async def test_missing_output_timestamp_is_treated_as_not_speaking(self):
        conn = _Conn()
        delattr(conn, "google_live_audio_out_started_at")
        conn.config["google_live"]["barge_in_via_transcript"] = True
        captured = []

        async def handler(text):
            captured.append(text)

        bridge = self._bridge(conn, handler)
        await bridge.handle_event({"type": "transcript", "source": "user", "text": "dừng lại"})
        await bridge.close()
        self.assertEqual(captured, [])

    async def test_no_fire_for_short_transcript(self):
        conn = _Conn()
        conn.config["google_live"]["barge_in_via_transcript"] = True
        conn.config["google_live"]["barge_in_transcript_min_chars"] = 5
        conn.google_live_audio_out_started_at = time.monotonic()
        captured = []

        async def handler(text):
            captured.append(text)

        bridge = self._bridge(conn, handler)
        await bridge.handle_event({"type": "transcript", "source": "user", "text": "ok"})
        await bridge.close()
        self.assertEqual(captured, [])

    async def test_no_fire_for_model_source(self):
        conn = _Conn()
        conn.config["google_live"]["barge_in_via_transcript"] = True
        conn.google_live_audio_out_started_at = time.monotonic()
        captured = []

        async def handler(text):
            captured.append(text)

        bridge = self._bridge(conn, handler)
        await bridge.handle_event({"type": "transcript", "source": "model", "text": "robot speaking"})
        await bridge.close()
        self.assertEqual(captured, [])

    async def test_repeated_transcripts_are_debounced_via_provider(self):
        conn = _Conn()
        conn.config["google_live"]["barge_in_via_transcript"] = True
        conn.config["google_live"]["barge_in_transcript_min_chars"] = 3
        conn.config["google_live"]["interrupt_debounce_sec"] = 0.5
        client = _Client(connected=True)
        provider = GoogleLiveProvider(conn, client_factory=lambda *_: client)
        await provider.start_session()
        conn.google_live_audio_out_started_at = time.monotonic()
        await provider._begin_user_interrupt("transcript_barge_in")
        first_id = provider.current_response_id()
        await provider._begin_user_interrupt("transcript_barge_in")
        second_id = provider.current_response_id()
        await provider.close()
        self.assertEqual(first_id, second_id)

    async def test_user_transcript_matching_recent_model_text_is_suppressed_as_echo(self):
        conn = _Conn()
        conn.config["google_live"]["barge_in_via_transcript"] = True
        conn.config["google_live"]["barge_in_transcript_min_chars"] = 3
        conn.google_live_audio_out_started_at = time.monotonic() - 5
        captured = []

        async def handler(text):
            captured.append(text)

        bridge = self._bridge(conn, handler)
        await bridge.handle_event(
            {"type": "transcript", "source": "model", "text": "Xin chào, mình là TBOT"}
        )
        await bridge.handle_event(
            {"type": "transcript", "source": "user", "text": "mình là TBOT"}
        )
        await bridge.close()
        self.assertEqual(captured, [])

    async def test_genuine_user_transcript_still_fires_after_model_speech(self):
        conn = _Conn()
        conn.config["google_live"]["barge_in_via_transcript"] = True
        conn.config["google_live"]["barge_in_transcript_min_chars"] = 3
        conn.google_live_audio_out_started_at = time.monotonic() - 5
        captured = []

        async def handler(text):
            captured.append(text)

        bridge = self._bridge(conn, handler)
        await bridge.handle_event(
            {"type": "transcript", "source": "model", "text": "Xin chào, mình là TBOT"}
        )
        await bridge.handle_event(
            {"type": "transcript", "source": "user", "text": "dừng lại"}
        )
        await bridge.close()
        self.assertEqual(captured, ["dừng lại"])

    async def test_echo_suppression_expires_after_window(self):
        conn = _Conn()
        conn.config["google_live"]["barge_in_via_transcript"] = True
        conn.config["google_live"]["barge_in_transcript_min_chars"] = 3
        conn.config["google_live"]["transcript_echo_window_sec"] = 0.05
        conn.google_live_audio_out_started_at = time.monotonic() - 5
        captured = []

        async def handler(text):
            captured.append(text)

        bridge = self._bridge(conn, handler)
        await bridge.handle_event(
            {"type": "transcript", "source": "model", "text": "mình là TBOT"}
        )
        await asyncio.sleep(0.1)
        await bridge.handle_event(
            {"type": "transcript", "source": "user", "text": "mình là TBOT"}
        )
        await bridge.close()
        self.assertEqual(captured, ["mình là TBOT"])

    async def test_echo_window_zero_disables_guard(self):
        conn = _Conn()
        conn.config["google_live"]["barge_in_via_transcript"] = True
        conn.config["google_live"]["barge_in_transcript_min_chars"] = 3
        conn.config["google_live"]["transcript_echo_window_sec"] = 0
        conn.google_live_audio_out_started_at = time.monotonic() - 5
        captured = []

        async def handler(text):
            captured.append(text)

        bridge = self._bridge(conn, handler)
        await bridge.handle_event(
            {"type": "transcript", "source": "model", "text": "mình là TBOT"}
        )
        await bridge.handle_event(
            {"type": "transcript", "source": "user", "text": "mình là TBOT"}
        )
        await bridge.close()
        self.assertEqual(captured, ["mình là TBOT"])

    async def test_transcript_barge_in_suppressed_during_min_output_age(self):
        conn = _Conn()
        conn.config["google_live"]["barge_in_via_transcript"] = True
        conn.config["google_live"]["barge_in_transcript_min_chars"] = 3
        conn.config["google_live"]["barge_in_transcript_min_output_age_sec"] = 2.0
        # Output started just now → still within the age guard window.
        conn.google_live_audio_out_started_at = time.monotonic()
        captured = []

        async def handler(text):
            captured.append(text)

        bridge = self._bridge(conn, handler)
        await bridge.handle_event(
            {"type": "transcript", "source": "user", "text": "dừng lại"}
        )
        await bridge.close()
        self.assertEqual(captured, [])

    async def test_transcript_barge_in_fires_past_min_output_age(self):
        conn = _Conn()
        conn.config["google_live"]["barge_in_via_transcript"] = True
        conn.config["google_live"]["barge_in_transcript_min_chars"] = 3
        conn.config["google_live"]["barge_in_transcript_min_output_age_sec"] = 0.05
        conn.google_live_audio_out_started_at = time.monotonic() - 1.0
        captured = []

        async def handler(text):
            captured.append(text)

        bridge = self._bridge(conn, handler)
        await bridge.handle_event(
            {"type": "transcript", "source": "user", "text": "dừng lại"}
        )
        await bridge.close()
        self.assertEqual(captured, ["dừng lại"])

class _PassthroughBridge:
    def __init__(self, rms=10000):
        self.forwarded = []
        self.rms = rms
        self._allow_model_output_calls = 0

    def decode_input_audio(self, audio_bytes):
        return audio_bytes

    async def forward_decoded_input_audio(self, pcm_bytes):
        self.forwarded.append(pcm_bytes)

    def input_rms(self, pcm_bytes):
        return self.rms

    def allow_model_output(self):
        self._allow_model_output_calls += 1

class RobotOutputEchoGateTest(unittest.IsolatedAsyncioTestCase):
    async def test_mic_frames_are_suppressed_while_robot_is_speaking(self):
        conn = _Conn()
        conn.websocket = None
        conn.client_is_speaking = True
        conn.google_live_audio_out_started_at = time.monotonic() - 1.0
        provider = GoogleLiveProvider(conn, client_factory=lambda *_: _Client())
        provider._bridge = _PassthroughBridge(rms=300)

        handled = await provider.handle_audio_bytes(b"\x01\x02" * 320)

        self.assertTrue(handled)
        self.assertEqual(provider._bridge.forwarded, [])
        self.assertFalse(conn.client_abort)
        self.assertEqual(provider.current_response_id(), 0)
        self.assertTrue(
            any("echo_suppressed" in str(args[0]) for _, args, _ in conn.logger.messages)
        )

    async def test_wake_audio_window_does_not_bypass_robot_speaking_echo_gate(self):
        conn = _Conn()
        conn.websocket = None
        conn.client_is_speaking = True
        conn.google_live_audio_out_started_at = time.monotonic() - 0.1
        provider = GoogleLiveProvider(conn, client_factory=lambda *_: _Client())
        provider._bridge = _PassthroughBridge(rms=180)
        provider._user_audio_allowed_until = time.monotonic() + 5.0

        handled = await provider.handle_audio_bytes(b"\x01\x02" * 320)

        self.assertTrue(handled)
        self.assertEqual(provider._bridge.forwarded, [])
        self.assertFalse(conn.client_abort)
        self.assertTrue(
            any("suppress_echo" in str(args) for _, args, _ in conn.logger.messages)
        )

    async def test_loud_user_audio_interrupts_while_robot_is_speaking(self):
        conn = _Conn()
        conn.client_is_speaking = True
        conn.google_live_audio_out_started_at = time.monotonic() - 1.0
        conn.config["google_live"]["robot_output_echo_bypass_rms_threshold"] = 2000
        conn.config["google_live"]["robot_output_echo_bypass_min_duration_sec"] = 0.1
        conn.config["google_live"]["echo_bypass_interrupt_enabled"] = True
        client = _Client()
        provider = GoogleLiveProvider(conn, client_factory=lambda *_: client)
        provider._client = client
        provider._bridge = _PassthroughBridge(rms=3000)

        handled = await provider.handle_audio_bytes(b"\x01\x02" * 320)

        self.assertTrue(handled)
        self.assertEqual(provider._bridge.forwarded, [])
        self.assertEqual(client.interrupt_calls, 1)
        self.assertTrue(
            any("echo_bypass" in str(args[0]) for _, args, _ in conn.logger.messages)
        )

    async def test_loud_user_audio_does_not_interrupt_when_bypass_interrupt_disabled(self):
        conn = _Conn()
        conn.client_is_speaking = True
        conn.google_live_audio_out_started_at = time.monotonic() - 1.0
        conn.config["google_live"]["robot_output_echo_bypass_rms_threshold"] = 2000
        conn.config["google_live"]["robot_output_echo_bypass_min_duration_sec"] = 0.1
        conn.config["google_live"]["echo_bypass_interrupt_enabled"] = False
        client = _Client()
        provider = GoogleLiveProvider(conn, client_factory=lambda *_: client)
        provider._client = client
        provider._bridge = _PassthroughBridge(rms=3000)

        handled = await provider.handle_audio_bytes(b"\x01\x02" * 320)

        self.assertTrue(handled)
        # No interrupt fired despite loud audio above bypass threshold.
        self.assertEqual(client.interrupt_calls, 0)
        # Echo gate still suppresses the frame (forwarded list empty).
        self.assertEqual(provider._bridge.forwarded, [])
        self.assertTrue(
            any("echo_suppressed" in str(args[0]) for _, args, _ in conn.logger.messages)
        )

    async def test_moderate_user_audio_interrupts_immediately_while_robot_speaks(self):
        conn = _Conn()
        conn.client_is_speaking = True
        conn.google_live_audio_out_started_at = time.monotonic() - 1.0
        conn.config["google_live"]["robot_output_echo_bypass_rms_threshold"] = 1200
        conn.config["google_live"]["echo_bypass_interrupt_enabled"] = True
        client = _Client()
        provider = GoogleLiveProvider(conn, client_factory=lambda *_: client)
        provider._client = client
        provider._bridge = _PassthroughBridge(rms=1000)

        handled = await provider.handle_audio_bytes(b"\x01\x02" * 320)

        self.assertTrue(handled)
        self.assertEqual(client.interrupt_calls, 1)
        self.assertEqual(provider._bridge.forwarded, [])

    async def test_default_mid_sentence_voice_level_interrupts_robot_output(self):
        conn = _Conn()
        conn.client_is_speaking = True
        conn.google_live_audio_out_started_at = time.monotonic() - 1.0
        conn.config["google_live"]["echo_bypass_interrupt_enabled"] = True
        client = _Client()
        provider = GoogleLiveProvider(conn, client_factory=lambda *_: client)
        provider._client = client
        provider._bridge = _PassthroughBridge(rms=700)

        handled = await provider.handle_audio_bytes(b"\x01\x02" * 320)

        self.assertTrue(handled)
        self.assertEqual(client.interrupt_calls, 1)
        self.assertEqual(provider._bridge.forwarded, [])

    async def test_loud_interrupt_audio_is_replayed_after_old_turn_drains(self):
        conn = _Conn()
        conn.websocket = None
        conn.client_is_speaking = True
        conn.google_live_audio_out_started_at = time.monotonic() - 1.0
        conn.config["google_live"]["robot_output_echo_bypass_rms_threshold"] = 2000
        conn.config["google_live"]["robot_output_echo_bypass_min_duration_sec"] = 0.1
        conn.config["google_live"]["echo_bypass_interrupt_enabled"] = True
        conn.config["google_live"]["interrupt_min_capture_ms"] = 20
        conn.config["google_live"]["interrupt_speech_tail_ms"] = 20
        conn.config["google_live"]["interrupt_max_capture_ms"] = 100
        conn.config["google_live"]["interrupt_forced_flush_delay_sec"] = 0.02
        client = _Client()
        provider = GoogleLiveProvider(conn, client_factory=lambda *_: client)
        provider._client = client
        bridge = _RawInputCapturingBridge(
            conn,
            client,
            _Logger(),
            response_id_getter=provider.current_response_id,
            response_cancelled_checker=provider.is_response_cancelled,
            model_output_unblocked_handler=provider._on_model_output_unblocked,
        )
        provider._bridge = bridge

        await bridge.handle_event({"type": "audio_start"})
        interrupt_frame = b"\xff\x7f" * 320
        await provider.handle_audio_bytes(interrupt_frame)
        sent_before_drain = list(client.sent_audio)
        await bridge.handle_event({"type": "audio_end"})
        for _ in range(30):
            if client.sent_audio:
                break
            await asyncio.sleep(0.01)
        await provider.close()

        self.assertEqual(sent_before_drain, [])
        self.assertEqual(client.sent_audio, [interrupt_frame])
        self.assertTrue(
            any(
                "replayed_interrupt_audio" in str(args[0])
                for _, args, _ in conn.logger.messages
            )
        )
        self.assertEqual(client.end_stream_calls, 2)
        self.assertFalse(
            any(
                "interrupt_input_finalized reason=interrupt_replay" in str(args[0])
                for _, args, _ in conn.logger.messages
            )
        )

    async def test_interrupt_input_is_forced_flushed_even_when_mic_keeps_streaming(self):
        conn = _Conn()
        conn.client_is_speaking = True
        conn.google_live_audio_out_started_at = time.monotonic() - 1.0
        conn.config["google_live"]["echo_bypass_interrupt_enabled"] = True
        conn.config["google_live"]["input_flush_delay_sec"] = 5.0
        conn.config["google_live"]["interrupt_forced_flush_delay_sec"] = 0.02
        conn.config["google_live"]["interrupt_min_capture_ms"] = 20
        conn.config["google_live"]["interrupt_speech_tail_ms"] = 20
        conn.config["google_live"]["interrupt_max_capture_ms"] = 100
        client = _Client()
        provider = GoogleLiveProvider(conn, client_factory=lambda *_: client)
        provider._client = client
        provider._bridge = _PassthroughBridge(rms=3000)

        await provider.handle_audio_bytes(b"\x01\x02" * 320)
        provider._bridge.rms = 100
        for _ in range(3):
            await provider.handle_audio_bytes(b"\x01\x02" * 320)
        await asyncio.sleep(0.04)
        await provider.close()

        self.assertGreaterEqual(client.end_stream_calls, 2)
        self.assertTrue(
            any(
                "interrupt_input_finalized" in str(args[0])
                for _, args, _ in conn.logger.messages
            )
        )

    async def test_interrupt_input_waits_for_speech_tail_before_finalizing(self):
        conn = _Conn()
        conn.client_is_speaking = True
        conn.google_live_audio_out_started_at = time.monotonic() - 1.0
        conn.config["google_live"]["echo_bypass_interrupt_enabled"] = True
        conn.config["google_live"]["interrupt_min_capture_ms"] = 20
        conn.config["google_live"]["interrupt_speech_tail_ms"] = 60
        conn.config["google_live"]["interrupt_max_capture_ms"] = 240
        conn.config["google_live"]["interrupt_forced_flush_delay_sec"] = 0.02
        client = _Client()
        provider = GoogleLiveProvider(conn, client_factory=lambda *_: client)
        provider._client = client
        provider._bridge = _PassthroughBridge(rms=3000)

        await provider.handle_audio_bytes(b"\x01\x02" * 320)
        await asyncio.sleep(0.03)
        await provider.handle_audio_bytes(b"\x01\x02" * 320)
        await asyncio.sleep(0.03)
        self.assertEqual(client.end_stream_calls, 1)
        await asyncio.sleep(0.05)
        await provider.close()

        self.assertGreaterEqual(client.end_stream_calls, 2)
        self.assertTrue(
            any(
                "interrupt_input_finalized" in str(args[0])
                for _, args, _ in conn.logger.messages
            )
        )

    async def test_wake_word_interrupts_and_opens_audio_window_during_music(self):
        conn = _Conn()
        conn._music_session = _MusicSession()
        client = _Client()
        provider = GoogleLiveProvider(conn, client_factory=lambda *_: client)
        provider._client = client
        provider._bridge = _PassthroughBridge()

        handled = await provider.handle_text_message(
            '{"type":"listen","state":"detect","text":"Hi ESP"}'
        )
        await provider.handle_audio_bytes(b"\x01\x02" * 320)

        self.assertTrue(handled)
        self.assertEqual(client.sent_text, [])
        self.assertEqual(client.interrupt_calls, 1)
        self.assertTrue(conn._music_session.is_paused())
        self.assertEqual(provider.current_response_id(), 1)
        self.assertEqual(provider._bridge.forwarded, [b"\x01\x02" * 320])

    async def test_user_transcript_interrupts_music_without_raw_audio_gate(self):
        conn = _Conn()
        conn._music_session = _MusicSession()
        client = _Client()
        provider = GoogleLiveProvider(conn, client_factory=lambda *_: client)
        provider._client = client
        provider._bridge = _PassthroughBridge()

        await provider._on_user_transcript_barge_in("xin nghe tôi nói")

        self.assertEqual(client.interrupt_calls, 1)
        self.assertTrue(conn._music_session.is_paused())
        self.assertEqual(provider.current_response_id(), 1)

    async def test_loud_speech_bypass_can_interrupt_music_only_when_enabled(self):
        conn = _Conn()
        conn._music_session = _MusicSession()
        conn.config["google_live"]["echo_bypass_interrupt_enabled"] = True
        conn.config["google_live"]["robot_output_echo_bypass_rms_threshold"] = 1000
        conn.config["google_live"]["robot_output_echo_bypass_min_duration_sec"] = 0
        client = _Client()
        provider = GoogleLiveProvider(conn, client_factory=lambda *_: client)
        provider._client = client
        provider._bridge = _PassthroughBridge(rms=2000)

        handled = await provider.handle_audio_bytes(b"\x01\x02" * 320)

        self.assertTrue(handled)
        self.assertEqual(client.interrupt_calls, 1)
        self.assertTrue(conn._music_session.is_paused())
        self.assertEqual(provider.current_response_id(), 1)
        self.assertEqual(provider._bridge.forwarded, [])

    async def test_mic_frames_are_suppressed_while_music_session_is_active(self):
        conn = _Conn()
        conn._music_session = object()
        provider = GoogleLiveProvider(conn, client_factory=lambda *_: _Client())
        provider._bridge = _PassthroughBridge(rms=300)

        handled = await provider.handle_audio_bytes(b"\x01\x02" * 320)

        self.assertTrue(handled)
        self.assertEqual(provider._bridge.forwarded, [])
        self.assertEqual(provider.current_response_id(), 0)

class _MusicSession:
    def __init__(self, paused=False):
        self._paused = paused

    def is_paused(self):
        return self._paused

    def pause(self):
        self._paused = True

class MusicControlTranscriptTest(unittest.IsolatedAsyncioTestCase):
    async def test_stop_music_transcript_dispatches_local_tool(self):
        from plugins_func.register import ActionResponse, Action

        class _Handler:
            def __init__(self):
                self.calls = []

            async def handle_llm_function_call(self, _conn, payload):
                self.calls.append(payload)
                return ActionResponse(action=Action.RESPONSE, response="Đã tắt nhạc.")

        conn = _Conn()
        handler = _Handler()
        conn.func_handler = handler
        conn._music_session = _MusicSession()
        client = _Client()
        provider = GoogleLiveProvider(conn, client_factory=lambda *_: client)
        provider._client = client
        provider._bridge = _PassthroughBridge()

        await provider._on_user_transcript_barge_in("Hi ESP tắt nhạc")

        self.assertEqual(handler.calls[0]["name"], "stop_music")
        self.assertEqual(handler.calls[0]["arguments"]["response_success"], "Đã tắt nhạc.")
        self.assertEqual(client.interrupt_calls, 1)

    async def test_resume_music_transcript_dispatches_local_tool(self):
        from plugins_func.register import ActionResponse, Action

        class _Handler:
            def __init__(self):
                self.calls = []

            async def handle_llm_function_call(self, _conn, payload):
                self.calls.append(payload)
                return ActionResponse(action=Action.RESPONSE, response="Phát tiếp nhạc.")

        conn = _Conn()
        handler = _Handler()
        conn.func_handler = handler
        conn._music_session = _MusicSession(paused=True)
        client = _Client()
        provider = GoogleLiveProvider(conn, client_factory=lambda *_: client)
        provider._client = client
        provider._bridge = _PassthroughBridge()

        await provider._on_user_transcript_barge_in("tiếp tục phát nhạc")

        self.assertEqual(handler.calls[0]["name"], "resume_music")
        self.assertEqual(handler.calls[0]["arguments"]["response_success"], "Phát tiếp nhạc.")


class InterruptionOutputAgeGuardTest(unittest.IsolatedAsyncioTestCase):
    def _bridge(self, conn):
        conn.websocket = None
        return GoogleLiveAudioBridge(conn, _Client(), _Logger())

    async def test_interruption_within_min_age_is_suppressed(self):
        conn = _Conn()
        conn.config["google_live"]["disable_server_side_interruptions"] = False
        conn.config["google_live"]["interruption_min_output_age_sec"] = 0.5
        conn.google_live_audio_out_started_at = time.monotonic()  # just started
        bridge = self._bridge(conn)
        result = await bridge.handle_event({"type": "interruption"})
        await bridge.close()
        self.assertTrue(result)
        # output age not cleared because we suppressed
        self.assertIsNotNone(conn.google_live_audio_out_started_at)
        self.assertFalse(conn.client_abort)

    async def test_interruption_after_min_age_is_honoured(self):
        conn = _Conn()
        conn.config["google_live"]["disable_server_side_interruptions"] = False
        conn.config["google_live"]["interruption_min_output_age_sec"] = 0.01
        conn.google_live_audio_out_started_at = time.monotonic() - 1.0  # 1s ago
        bridge = self._bridge(conn)
        result = await bridge.handle_event({"type": "interruption"})
        await bridge.close()
        self.assertTrue(result)
        self.assertIsNone(conn.google_live_audio_out_started_at)
        self.assertTrue(conn.client_abort)

    async def test_explicit_ignore_server_interruptions_cannot_block_live_interruption(self):
        conn = _Conn()
        conn.config["google_live"]["ignore_server_interruptions"] = True
        conn.google_live_audio_out_started_at = time.monotonic() - 5.0  # plenty old
        bridge = self._bridge(conn)
        result = await bridge.handle_event({"type": "interruption"})
        await bridge.close()
        self.assertTrue(result)
        self.assertIsNone(conn.google_live_audio_out_started_at)
        self.assertTrue(conn.client_abort)


class BargeInConfigTuneTest(unittest.TestCase):
    """PR4 P4.5: verify config.yaml + GOOGLE_LIVE_DEFAULTS expose the tuned
    barge-in thresholds and the explicit server_side_vad_enabled flag from
    plan §5. Prevents regression to old 5000 / 0.42 values."""

    def test_config_yaml_barge_in_thresholds_match_pr4_tune(self):
        import os
        import yaml

        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "config.yaml",
        )
        with open(config_path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        google_live = data["google_live"]
        self.assertEqual(google_live["barge_in_rms_threshold"], 4500)
        self.assertEqual(google_live["barge_in_min_input_duration_sec"], 0.30)
        self.assertTrue(google_live["server_side_vad_enabled"])
        self.assertTrue(google_live["suppress_robot_output_echo"])
        self.assertEqual(google_live["wake_audio_allow_window_sec"], 5.0)
        self.assertEqual(google_live["interrupt_forced_flush_delay_sec"], 0.8)
        self.assertEqual(google_live["interrupt_min_capture_ms"], 360)
        self.assertEqual(google_live["interrupt_speech_tail_ms"], 240)
        self.assertEqual(google_live["interrupt_max_capture_ms"], 1200)
        self.assertEqual(google_live["robot_output_echo_bypass_rms_threshold"], 650)
        self.assertEqual(google_live["robot_output_echo_bypass_min_duration_sec"], 0.06)
        self.assertFalse(google_live["hard_reconnect_on_interrupt"])

    def test_google_live_defaults_match_pr4_tune(self):
        from config.config_loader import GOOGLE_LIVE_DEFAULTS

        self.assertEqual(GOOGLE_LIVE_DEFAULTS["barge_in_rms_threshold"], 4500)
        self.assertEqual(GOOGLE_LIVE_DEFAULTS["barge_in_min_input_duration_sec"], 0.30)
        self.assertFalse(GOOGLE_LIVE_DEFAULTS["barge_in"])
        self.assertFalse(GOOGLE_LIVE_DEFAULTS["interrupt_on_input_while_speaking"])
        self.assertTrue(GOOGLE_LIVE_DEFAULTS["server_side_vad_enabled"])
        self.assertTrue(GOOGLE_LIVE_DEFAULTS["suppress_robot_output_echo"])
        self.assertEqual(GOOGLE_LIVE_DEFAULTS["wake_audio_allow_window_sec"], 5.0)
        self.assertEqual(GOOGLE_LIVE_DEFAULTS["interrupt_forced_flush_delay_sec"], 0.8)
        self.assertEqual(GOOGLE_LIVE_DEFAULTS["interrupt_min_capture_ms"], 360)
        self.assertEqual(GOOGLE_LIVE_DEFAULTS["interrupt_speech_tail_ms"], 240)
        self.assertEqual(GOOGLE_LIVE_DEFAULTS["interrupt_max_capture_ms"], 1200)
        self.assertEqual(GOOGLE_LIVE_DEFAULTS["robot_output_echo_bypass_rms_threshold"], 650)
        self.assertEqual(GOOGLE_LIVE_DEFAULTS["robot_output_echo_bypass_min_duration_sec"], 0.06)
        self.assertFalse(GOOGLE_LIVE_DEFAULTS["hard_reconnect_on_interrupt"])

    def test_runtime_config_forces_local_audio_interrupts_off(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        conn = _Conn()
        conn.config["google_live"]["barge_in"] = True
        conn.config["google_live"]["interrupt_on_input_while_speaking"] = True

        try:
            provider = GoogleLiveProvider(conn, client_factory=lambda *_: _Client())
            google_live = provider._get_live_config()
        finally:
            asyncio.set_event_loop(None)
            loop.close()

        self.assertFalse(google_live["barge_in"])
        self.assertFalse(google_live["interrupt_on_input_while_speaking"])
        self.assertEqual(google_live["robot_output_echo_bypass_rms_threshold"], 650)


class InterruptTurnControllerTest(unittest.IsolatedAsyncioTestCase):
    """PR2 tests targeting server-exec delta: per-turn idempotency flags
    _interrupt_replayed_once and _interrupt_forwarded_once."""

    async def test_replay_idempotent_when_called_twice(self):
        """_replay_pending_interrupt_audio called twice in same turn logs once and
        sets _interrupt_replayed_once; second call is a no-op."""
        conn = _Conn()
        conn.websocket = None
        conn.client_is_speaking = True
        conn.google_live_audio_out_started_at = time.monotonic() - 1.0
        conn.config["google_live"]["robot_output_echo_bypass_rms_threshold"] = 2000
        conn.config["google_live"]["robot_output_echo_bypass_min_duration_sec"] = 0.1
        conn.config["google_live"]["echo_bypass_interrupt_enabled"] = True
        conn.config["google_live"]["interrupt_min_capture_ms"] = 20
        conn.config["google_live"]["interrupt_speech_tail_ms"] = 20
        conn.config["google_live"]["interrupt_max_capture_ms"] = 200
        conn.config["google_live"]["interrupt_forced_flush_delay_sec"] = 5.0
        conn.config["google_live"]["model_output_unblock_timeout_sec"] = 5.0

        client = _Client()
        provider = GoogleLiveProvider(conn, client_factory=lambda *_: client)
        provider._client = client
        bridge = _RawInputCapturingBridge(
            conn,
            client,
            _Logger(),
            response_id_getter=provider.current_response_id,
            response_cancelled_checker=provider.is_response_cancelled,
            model_output_unblocked_handler=provider._on_model_output_unblocked,
        )
        provider._bridge = bridge

        # Start output so stop_output latches the block
        await bridge.handle_event({"type": "audio_start"})
        # Loud interrupt frame → buffers into _pending_interrupt_audio
        interrupt_frame = b"\xff\x7f" * 320
        await provider.handle_audio_bytes(interrupt_frame)

        # Drain the old turn so allow_model_output can unblock
        await bridge.handle_event({"type": "audio_end"})
        await asyncio.sleep(0.01)

        # First replay — must succeed
        await provider._replay_pending_interrupt_audio("test_first")
        self.assertTrue(
            provider._interrupt_replayed_once,
            "_interrupt_replayed_once must be True after first replay",
        )
        first_replay_logs = [
            args for _, args, _ in conn.logger.messages
            if "replayed_interrupt_audio" in str(args[0])
        ]
        self.assertEqual(len(first_replay_logs), 1, "Expected exactly one replay log")

        # Second replay — guard must fire, no additional log
        await provider._replay_pending_interrupt_audio("test_second")
        second_replay_logs = [
            args for _, args, _ in conn.logger.messages
            if "replayed_interrupt_audio" in str(args[0])
        ]
        self.assertEqual(
            len(second_replay_logs), 1,
            "Second replay must be suppressed by _interrupt_replayed_once guard",
        )

        await provider.close()

    async def test_replayed_once_resets_on_new_candidate(self):
        """_interrupt_replayed_once is cleared by _start_interrupt_capture_turn so
        a fresh interrupt turn can replay its own audio."""
        conn = _Conn()
        client = _Client()
        provider = GoogleLiveProvider(conn, client_factory=lambda *_: client)
        provider._client = client
        provider._bridge = _PassthroughBridge(rms=100)

        # Manually start a turn and mark it replayed
        provider._start_interrupt_capture_turn("audio_input")
        provider._interrupt_replayed_once = True
        self.assertTrue(provider._interrupt_replayed_once)

        # Start a new candidate — must reset the flag
        provider._response_generation += 1
        provider._start_interrupt_capture_turn("audio_input")

        self.assertFalse(
            provider._interrupt_replayed_once,
            "_interrupt_replayed_once must be False after _start_interrupt_capture_turn",
        )
        self.assertFalse(
            provider._interrupt_forwarded_once,
            "_interrupt_forwarded_once must also be False after reset",
        )
        await provider.close()

    async def test_forwarded_once_marks_after_first_frame(self):
        """handle_audio_bytes sets _interrupt_forwarded_once=True once a mic frame
        is forwarded while an interrupt capture turn is active."""
        conn = _Conn()
        conn.client_is_speaking = True
        conn.google_live_audio_out_started_at = time.monotonic() - 1.0
        conn.config["google_live"]["robot_output_echo_bypass_rms_threshold"] = 2000
        conn.config["google_live"]["echo_bypass_interrupt_enabled"] = True
        client = _Client()
        provider = GoogleLiveProvider(conn, client_factory=lambda *_: client)
        provider._client = client
        provider._bridge = _PassthroughBridge(rms=3000)

        # First frame triggers interrupt and starts capture turn
        await provider.handle_audio_bytes(b"\x01\x02" * 320)

        # At this point an interrupt capture turn is active;
        # send a second frame at lower RMS so it falls through to forwarding path
        provider._bridge.rms = 100
        await provider.handle_audio_bytes(b"\x01\x02" * 320)

        self.assertTrue(
            provider._interrupt_forwarded_once,
            "_interrupt_forwarded_once must be set after a frame is forwarded during capture",
        )
        await provider.close()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
