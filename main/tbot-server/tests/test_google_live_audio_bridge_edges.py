import asyncio
import builtins
import json
import time
import unittest
from types import SimpleNamespace

from core.voice.child_safety import SAFE_DEFLECTION_LINE
from core.voice.google_live.audio_bridge import GoogleLiveAudioBridge

SAFE_DEFLECTION_LIVE_TEXT_INSTRUCTION = (
    "Đọc nguyên văn câu sau bằng giọng Google Live đã cấu hình. "
    "Không dịch, không thêm nội dung, không bỏ sót, không rút gọn: "
)


class _Logger:
    def __init__(self, fail_info=False):
        self.fail_info = fail_info
        self.messages = []

    def bind(self, **_kwargs):
        return self

    def info(self, *args, **kwargs):
        if self.fail_info:
            raise RuntimeError("info failed")
        self.messages.append(("info", args, kwargs))

    def warning(self, *args, **kwargs):
        self.messages.append(("warning", args, kwargs))


class _WebSocket:
    def __init__(self):
        self.sent = []

    async def send(self, payload):
        self.sent.append(payload)


class _Client:
    def __init__(self, config=None):
        self.config = config or {}
        self.sent_text = []

    async def send_text(self, text):
        self.sent_text.append(text)


class _Conn:
    def __init__(self, websocket=None, config=None):
        self.websocket = websocket
        self.config = config or {"google_live": {}}
        self.session_id = "session-1"
        self.device_id = "device-1"
        self.sample_rate = 24000
        self.client_abort = False
        self.client_is_speaking = False
        self.google_live_audio_out_started_at = None
        self.google_live_session_started_at = None
        self.google_live_turn_started_at = None
        self.cleared = 0

    def clear_queues(self):
        self.cleared += 1

    def clearSpeakStatus(self):
        self.client_is_speaking = False


class _PausedMusic:
    frame_index = 2

    def __init__(self, raise_pause=False, paused=False):
        self.raise_pause = raise_pause
        self.paused = paused
        self.pause_calls = 0

    def is_paused(self):
        return self.paused

    def pause(self):
        self.pause_calls += 1
        if self.raise_pause:
            raise RuntimeError("pause failed")


class _QueueRaises:
    def qsize(self):
        raise RuntimeError("qsize failed")


class _ResetRaises:
    queue = []

    def reset(self):
        raise RuntimeError("reset failed")


class _StopEventRaises:
    def is_set(self):
        raise RuntimeError("stop failed")


class _TtsQueue:
    def __init__(self):
        self.items = []

    def put(self, item):
        self.items.append(item)


class _TtsStoreRaises:
    def __init__(self):
        self.tts_text_queue = _TtsQueue()

    def store_tts_text(self, *_args):
        raise RuntimeError("store failed")


class _Decoder:
    def decode(self, _audio_bytes, _frame_size):
        return b"\x00\x00" * 20

class _LowPcmDecoder:
    def decode(self, _audio_bytes, _frame_size):
        return (100).to_bytes(2, "little", signed=True) * 20


class _LenRaises:
    def __len__(self):
        raise RuntimeError("len failed")


class _FailingAec:
    sample_rate = 16000
    bypassed = False

    def __init__(self):
        self.references = []

    def process_mic(self, _pcm):
        raise RuntimeError("aec failed")

    def push_reference(self, pcm):
        self.references.append(pcm)


class _BypassedAec(_FailingAec):
    bypassed = True


class _ResponseIds:
    def __init__(self):
        self.current = "response-1"

    def __call__(self):
        return self.current


class _Forwarder:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.events = []

    def enqueue(self, event):
        self.events.append(event)


class _FailingMetricConn(_Conn):
    def note_voice_round_trip(self, _latency_ms):
        raise RuntimeError("note failed")

    def record_voice_metric(self, *_args, **_kwargs):
        raise RuntimeError("metric failed")


class GoogleLiveAudioBridgeEdgeTest(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        bridge = getattr(self, "bridge", None)
        if bridge is not None:
            await bridge.close()

    def make_bridge(self, conn=None, client=None, logger=None, **kwargs):
        self.bridge = GoogleLiveAudioBridge(
            conn or _Conn(),
            client or _Client(),
            logger or _Logger(),
            **kwargs,
        )
        return self.bridge

    async def test_handle_event_defensive_edges(self):
        logger = _Logger()
        conn = _Conn(websocket=_WebSocket())
        bridge = self.make_bridge(conn=conn, logger=logger)

        self.assertFalse(await bridge.handle_event("bad"))
        self.assertFalse(await bridge.handle_event({"type": "transcript", "text": None}))
        self.assertFalse(await bridge.handle_event({"type": "audio_chunk"}))
        self.assertFalse(await bridge.handle_event({"type": "unknown"}))
        self.assertEqual(bridge.input_rms(b""), 0)
        self.assertEqual(await bridge.flush_pending_input_audio(), 0)

        bridge._locally_cancelled_response_ids = {f"old-{idx:02d}" for idx in range(25)}
        self.assertTrue(await bridge.handle_event({"type": "audio_start"}))
        self.assertLessEqual(len(bridge._locally_cancelled_response_ids), 11)

        bridge._moderation_block_active = True
        self.assertTrue(await bridge.handle_event({"type": "audio_start"}))
        bridge._moderation_block_active = False

        bridge._block_model_output_until_user_ack = True
        self.assertTrue(await bridge.handle_event({"type": "audio_start"}))
        bridge._block_model_output_until_user_ack = False

        bridge._suppress_audio_until = time.monotonic() + 1
        self.assertTrue(await bridge.handle_event({"type": "audio_start"}))

    async def test_handler_failures_and_blocked_audio_end_edges(self):
        async def fail_tool(_event):
            raise RuntimeError("tool failed")

        async def fail_cancel(_event):
            raise RuntimeError("cancel failed")

        async def fail_unblocked():
            raise RuntimeError("unblocked failed")

        logger = _Logger()
        bridge = self.make_bridge(
            conn=_Conn(websocket=_WebSocket()),
            logger=logger,
            tool_call_handler=fail_tool,
            tool_call_cancellation_handler=fail_cancel,
            model_output_unblocked_handler=fail_unblocked,
        )

        self.assertTrue(await bridge.handle_event({"type": "tool_call"}))
        bridge_without_handler = GoogleLiveAudioBridge(_Conn(), _Client(), logger)
        try:
            self.assertTrue(await bridge_without_handler.handle_event({"type": "tool_call"}))
        finally:
            await bridge_without_handler.close()
        self.assertTrue(await bridge.handle_event({"type": "tool_call_cancellation"}))

        bridge._moderation_block_active = True
        self.assertTrue(await bridge.handle_event({"type": "audio_end"}))
        self.assertFalse(bridge._moderation_block_active)

        bridge._block_model_output_until_user_ack = True
        bridge._accepted_user_turn_after_block = True
        bridge._waiting_for_interrupted_audio_end = False
        self.assertTrue(await bridge.handle_event({"type": "audio_end"}))
        await bridge._notify_model_output_unblocked()

    async def test_interruption_disabled_and_music_pause_errors(self):
        class _InterruptionsDisabledBridge(GoogleLiveAudioBridge):
            def _server_side_interruptions_disabled(self):
                return True

        logger = _Logger()
        conn = _Conn(websocket=_WebSocket())
        conn._music_session = _PausedMusic(raise_pause=True)
        self.bridge = _InterruptionsDisabledBridge(conn, _Client(), logger)

        self.assertTrue(await self.bridge.handle_event({"type": "interruption"}))
        self.assertFalse(conn.client_abort)
        self.assertTrue(await self.bridge.handle_event({"type": "audio_start"}))
        self.assertEqual(conn._music_session.pause_calls, 1)

    async def test_interruption_disabled_by_config_ignores_live_interrupt_event(self):
        conn = _Conn(
            websocket=_WebSocket(),
            config={"google_live": {"disable_server_side_interruptions": True}},
        )
        bridge = self.make_bridge(conn=conn, client=_Client(conn.config["google_live"]))

        self.assertTrue(await bridge.handle_event({"type": "interruption"}))

        self.assertFalse(conn.client_abort)
        self.assertEqual(conn.websocket.sent, [])
        self.assertIsNone(conn.google_live_audio_out_started_at)

    async def test_interruption_enabled_by_default_honors_live_interrupt_event(self):
        conn = _Conn(websocket=_WebSocket())
        conn.google_live_audio_out_started_at = time.monotonic() - 1.0
        bridge = self.make_bridge(conn=conn, client=_Client({}))

        self.assertTrue(await bridge.handle_event({"type": "interruption"}))

        self.assertTrue(conn.client_abort)
        self.assertEqual(conn.cleared, 1)
        self.assertIsNone(conn.google_live_audio_out_started_at)
        self.assertEqual(len(conn.websocket.sent), 1)
        self.assertIn('"reason": "interrupt"', conn.websocket.sent[0])

    async def test_lesson_transition_stop_does_not_reopen_realtime_listening(self):
        conn = _Conn(websocket=_WebSocket())
        bridge = self.make_bridge(conn=conn)

        await bridge.stop_output_for_lesson()

        payload = json.loads(conn.websocket.sent[-1])
        self.assertEqual(payload["reason"], "interrupt")
        self.assertFalse(payload["continue_listening"])
        self.assertEqual(payload["listen_mode"], "manual")

    async def test_send_helpers_noop_and_emotion_dedup_edges(self):
        bridge = self.make_bridge(conn=_Conn(websocket=None))

        await bridge._send_display_message("hello")
        await bridge._send_llm_message("hello")
        await bridge._send_emotion_message("hello")
        await bridge._send_tts_message("start")
        await bridge._send_tts_stop_now()
        await bridge._send_safe_deflection()
        await bridge._enqueue_safety_block("home address and phone number")
        self.assertEqual(bridge._redact_safety_text("Home Address and phone number"), "[redacted] and [redacted]")

        ws = _WebSocket()
        bridge = self.make_bridge(conn=_Conn(websocket=ws))
        await bridge._send_emotion_message("great job")
        await bridge._send_emotion_message("good work")
        self.assertEqual(len(ws.sent), 1)
        self.assertEqual(json.loads(ws.sent[0])["emotion"], "happy")

    async def test_safe_deflection_uses_google_live_text_only(self):
        conn = _Conn(websocket=_WebSocket())
        conn.tts = SimpleNamespace(tts_text_queue=_TtsQueue())
        client = _Client()
        bridge = self.make_bridge(conn=conn, client=client)

        await bridge._send_safe_deflection()

        self.assertEqual(
            client.sent_text,
            [SAFE_DEFLECTION_LIVE_TEXT_INSTRUCTION + SAFE_DEFLECTION_LINE],
        )
        self.assertEqual(conn.websocket.sent, [])
        self.assertEqual(conn.tts.tts_text_queue.items, [])

    async def test_audio_cpu_and_codec_helper_edges(self):
        conn = _FailingMetricConn(websocket=None)
        bridge = self.make_bridge(conn=conn, client=_Client({"input_sample_rate": "bad", "output_sample_rate": 24000}))

        await bridge.close()
        self.assertEqual(await bridge._run_audio_cpu(lambda value: value + 1, 2), 3)
        self.assertEqual(bridge._decode_input_audio(b""), b"")
        self.assertIsNone(await bridge._send_binary_audio_message(None))
        self.assertEqual(await bridge._flush_output_audio(), 0)
        self.assertEqual(bridge._extract_sample_rate_from_mime("audio/pcm;rate=bad"), 24000)

        conn.google_live_turn_started_at = "bad"
        bridge._record_turn_first_audio_latency()
        self.assertIsNone(conn.google_live_turn_started_at)
        bridge._note_voice_round_trip(123)
        bridge._record_turn_latency_metric(123)
        self.assertEqual(bridge._get_interruption_min_output_age_sec(), 0.0)
        self.assertEqual(bridge._get_transcript_echo_window_sec(), 15.0)
        self.assertEqual(bridge._normalize_transcript_for_echo(None), "")
        self.assertFalse(bridge.looks_like_model_echo("hi"))

        self.assertEqual(GoogleLiveAudioBridge._safe_queue_length(_QueueRaises()), 0)
        self.assertEqual(GoogleLiveAudioBridge._safe_queue_length(_LenRaises()), 0)
        self.assertEqual(GoogleLiveAudioBridge._safe_queue_length([1, 2]), 2)

    async def test_aec_and_config_fallback_edges(self):
        bridge = self.make_bridge(
            conn=_Conn(config={"google_live": {"echo_tail_suppression_ms": "bad", "interrupt_suppress_audio_sec": "bad"}}),
            client=_Client({"input_live_chunk_ms": "bad", "input_sample_rate": "bad"}),
        )
        self.assertEqual(bridge._get_live_input_chunk_bytes(), 640)
        self.assertEqual(bridge._get_interrupt_suppress_audio_sec(), 0.25)
        bridge._mark_echo_tail_suppression("test")
        self.assertGreater(bridge.conn.google_live_echo_suppress_until, 0)
        # Default echo_tail_audible_ms latches audible after stop (anti-monologue).
        self.assertGreater(
            getattr(bridge.conn, "google_live_audible_output_until", 0),
            time.monotonic(),
        )
        bridge.client.config = {
            "echo_tail_suppression_ms": 200,
            "echo_tail_audible_ms": 0,
        }
        bridge.conn.google_live_audible_output_until = 0
        bridge._mark_echo_tail_suppression("no_audible")
        self.assertFalse(
            getattr(bridge.conn, "google_live_audible_output_until", 0) > time.monotonic()
        )

        bridge._aec_processor = _FailingAec()
        self.assertEqual(bridge._apply_aec(b"\x00\x00", 24000), b"\x00\x00")
        self.assertEqual(bridge._apply_aec(b"\x00\x00", 16000), b"")
        idle_bridge = self.make_bridge()
        idle_bridge._aec_processor = _FailingAec()
        self.assertEqual(idle_bridge._apply_aec(b"\x00\x00", 16000), b"\x00\x00")
        bridge.conn.client_is_speaking = True
        bridge.conn.google_live_audio_out_started_at = time.monotonic()
        self.assertEqual(bridge._apply_aec(b"\x00\x00", 16000), b"")
        bridge._push_aec_reference(b"\x00\x00", 16000)
        self.assertEqual(bridge._aec_processor.references, [b"\x00\x00"])

    async def test_response_state_and_logger_defensive_edges(self):
        ids = _ResponseIds()
        logger = _Logger()
        conn = _Conn(websocket=_WebSocket(), config={"google_live": {"model_output_unblock_timeout_sec": "bad"}})
        conn.audio_rate_controller = _ResetRaises()
        conn._music_session = _PausedMusic()
        bridge = self.make_bridge(conn=conn, logger=logger, response_id_getter=ids)

        bridge._block_model_output_until_user_ack = True
        bridge._should_drop_blocked_model_event = lambda _event_type: False
        self.assertTrue(await bridge.handle_event({"type": "audio_start"}))
        self.assertEqual(bridge.current_response_id(), "response-1")
        self.assertTrue(bridge.is_model_output_blocked())

        bridge._block_model_output_until_user_ack = False
        bridge.allow_model_output()
        self.assertFalse(bridge._unblock_model_output())
        self.assertEqual(bridge._get_unblock_timeout_sec(), 1.5)

        await bridge._unblock_after(0)
        failing_log_bridge = GoogleLiveAudioBridge(_Conn(), _Client(), _Logger(fail_info=True))
        try:
            failing_log_bridge._block_model_output_until_user_ack = True
            await failing_log_bridge._unblock_after(0)
            failing_log_bridge._log_stale_model_event_drop("audio", "test")
        finally:
            await failing_log_bridge.close()

        bridge._active_response_id = "response-1"
        bridge._clear_conn_queues()
        self.assertTrue(any(level == "warning" for level, _args, _kwargs in logger.messages))

        ids.current = "response-2"
        self.assertTrue(bridge._is_stale_response_event())
        ids.current = "response-1"
        bridge._locally_cancelled_response_ids.add("response-1")
        self.assertTrue(bridge._is_stale_response_event())

    async def test_unblock_timeout_defaults_malformed_connection_config(self):
        bridge = self.make_bridge(conn=_Conn(config=["bad"]), client=_Client({}))

        self.assertEqual(bridge._get_unblock_timeout_sec(), 1.5)

    async def test_transcript_handler_and_barge_in_failure_edges(self):
        async def fail_user(_text):
            raise RuntimeError("user failed")

        async def fail_barge(_text):
            raise RuntimeError("barge failed")

        conn = _Conn(
            websocket=_WebSocket(),
            config={
                "google_live": {
                    "barge_in_via_transcript": True,
                    "barge_in_transcript_min_chars": "bad",
                    "barge_in_transcript_min_output_age_sec": "bad",
                }
            },
        )
        conn.google_live_audio_out_started_at = time.monotonic() - 10
        bridge = self.make_bridge(
            conn=conn,
            user_transcript_handler=fail_user,
            user_transcript_barge_in_handler=fail_barge,
        )

        self.assertTrue(await bridge.handle_event({"type": "transcript", "source": "user", "text": "hello"}))
        self.assertTrue(any(level == "warning" for level, _args, _kwargs in bridge.logger.messages))

    async def test_music_tts_forwarder_and_import_edges(self):
        bridge = self.make_bridge(conn=_Conn(websocket=None))
        bridge.conn._music_session = SimpleNamespace(stop_event=_StopEventRaises())
        self.assertTrue(bridge._has_music_session())

        conn = _Conn(config={"lesson": {"api_base": "http://backend"}})
        import_fail_bridge = self.make_bridge(conn=conn)
        original_import = builtins.__import__

        def fail_forwarder_import(name, *args, **kwargs):
            if name == "core.lesson.forwarder":
                raise RuntimeError("forwarder import failed")
            return original_import(name, *args, **kwargs)

        try:
            builtins.__import__ = fail_forwarder_import
            self.assertIsNone(await import_fail_bridge._create_connection_safety_forwarder())
        finally:
            builtins.__import__ = original_import

    async def test_audio_decode_resample_and_aec_edges(self):
        bridge = self.make_bridge(client=_Client({"input_sample_rate": 16000, "log_audio_diagnostics": False}))
        bridge._input_decoder = _Decoder()
        self.assertTrue(bridge._decode_input_audio(b"encoded"))
        self.assertTrue(bridge.decode_input_audio(b"encoded"))
        self.assertTrue(await bridge.decode_input_audio_async(b"encoded"))

        self.assertTrue(bridge._is_valid_pcm16(b"\x00\x00"))
        self.assertFalse(bridge._is_valid_pcm16(b"\x00"))
        self.assertEqual(bridge._get_input_frame_size(), 1440)
        bridge._log_input_audio_diagnostics(b"\x00\x00")
        bridge._input_chunk_count = 1
        bridge._log_input_audio_diagnostics(b"\x00\x00")

        resampled = bridge._resample_pcm16(b"\x00\x00" * 40, 24000, 16000)
        self.assertTrue(resampled)
        output_resampled = bridge._resample_pcm16(b"\x00\x00" * 40, 24000, 16000, direction="output")
        self.assertTrue(output_resampled)

        bridge._aec_processor = _BypassedAec()
        self.assertEqual(bridge._apply_aec(b"\x00\x00", 16000), b"\x00\x00")
        bridge._push_aec_reference(b"\x00\x00" * 40, 24000)
        self.assertEqual(bridge._aec_processor.references, [])

    async def test_decode_applies_configured_input_gain_before_live_forwarding(self):
        conn = _Conn()
        conn.sample_rate = 16000
        bridge = self.make_bridge(
            conn=conn,
            client=_Client(
                {
                    "input_sample_rate": 16000,
                    "input_gain": 6.0,
                    "log_audio_diagnostics": False,
                }
            )
        )
        bridge._input_decoder = _LowPcmDecoder()
        bridge._input_decoder_sample_rate = 16000

        decoded = bridge._decode_input_audio(b"encoded")

        self.assertEqual(bridge.input_rms(decoded), 600)

    def test_apply_output_gain_boosts_pcm(self):
        bridge = self.make_bridge(
            client=_Client(
                {
                    "output_gain": 2.0,
                    "log_audio_diagnostics": False,
                }
            )
        )
        # 100 samples of amplitude 1000
        pcm = (1000).to_bytes(2, "little", signed=True) * 40
        boosted = bridge._apply_output_gain(pcm)
        self.assertEqual(bridge.input_rms(boosted), 2000)

    def test_apply_output_gain_default_is_noop(self):
        bridge = self.make_bridge(
            client=_Client({"log_audio_diagnostics": False})
        )
        pcm = (500).to_bytes(2, "little", signed=True) * 20
        self.assertEqual(bridge._apply_output_gain(pcm), pcm)

    async def test_aec_build_import_and_invalid_numeric_edges(self):
        original_import = builtins.__import__

        def fail_aec_import(name, *args, **kwargs):
            if name == "core.voice.aec":
                raise RuntimeError("aec import failed")
            return original_import(name, *args, **kwargs)

        try:
            builtins.__import__ = fail_aec_import
            bridge = GoogleLiveAudioBridge(_Conn(), _Client({"aec_enabled": True}), _Logger())
            self.bridge = bridge
            self.assertIsNone(bridge._aec_processor)
        finally:
            builtins.__import__ = original_import

        bridge = self.make_bridge(
            client=_Client(
                {
                    "aec_enabled": True,
                    "input_sample_rate": 16000,
                    "aec_filter_length_ms": "bad",
                    "aec_frame_ms": "bad",
                }
            )
        )
        self.assertIsNotNone(bridge._aec_processor)

        non_mapping_bridge = self.make_bridge(client=_Client())
        non_mapping_bridge.client.config = "bad"
        self.assertIsNone(non_mapping_bridge._build_aec_processor())

        bridge.conn.config = {"google_live": {"echo_tail_suppression_ms": 0}}
        bridge._mark_echo_tail_suppression("zero")

    async def test_remaining_bridge_constructor_and_state_edges(self):
        logger = _Logger()
        conn = _Conn(websocket=_WebSocket())
        conn._music_session = _PausedMusic()
        bridge = self.make_bridge(conn=conn, logger=logger)

        self.assertTrue(await bridge.handle_event({"type": "audio_start"}))
        self.assertEqual(conn._music_session.pause_calls, 1)

        bridge._block_model_output_until_user_ack = True
        self.assertTrue(await bridge.handle_event({"type": "tool_call_cancellation"}))

        bridge._note_voice_round_trip(1.0)
        bridge._record_turn_latency_metric(1.0)
        bridge.client.config = {"interruption_min_output_age_sec": "bad", "transcript_echo_window_sec": "bad"}
        self.assertEqual(bridge._get_interruption_min_output_age_sec(), 0.0)
        self.assertEqual(bridge._get_transcript_echo_window_sec(), 15.0)
        bridge._record_model_transcript("")

        self.assertIsNotNone(bridge._get_input_decoder())
        self.assertIsNotNone(bridge._get_output_encoder(24000))

        bridge._aec_processor = _FailingAec()
        bridge._push_aec_reference(b"\x00\x00" * 80, 24000)
        self.assertTrue(bridge._aec_processor.references)

    async def test_safety_forwarder_creation_edges(self):
        import config.device_token_client as token_module
        import core.lesson.forwarder as forwarder_module

        logger = _Logger()
        malformed_bridge = self.make_bridge(
            conn=_Conn(config={"lesson": "bad", "server": "bad"}),
            logger=logger,
        )
        self.assertIsNone(await malformed_bridge._create_connection_safety_forwarder())

        conn = _Conn(config={"lesson": {"api_base": "http://backend"}})
        bridge = self.make_bridge(conn=conn, logger=logger)

        original_resolve = token_module.resolve_device_identity
        original_forwarder = forwarder_module.LessonEventForwarder

        mint_calls = []

        async def unexpected_mint(_client, _base_url, _device_id, logger=None):
            mint_calls.append((_base_url, _device_id))
            return "backend-device", "minted-token"

        try:
            token_module.resolve_device_identity = unexpected_mint
            self.assertIsNone(await bridge._create_connection_safety_forwarder())
            self.assertEqual(mint_calls, [])

            conn_with_token = _Conn(
                config={"lesson": {"api_base": "http://backend", "device_token": "static-token"}}
            )
            bridge_with_token = self.make_bridge(conn=conn_with_token, logger=logger)
            forwarder_module.LessonEventForwarder = _Forwarder
            forwarder = await bridge_with_token._create_connection_safety_forwarder()
            self.assertIsInstance(forwarder, _Forwarder)
            self.assertEqual(forwarder.kwargs["device_id"], "device-1")
            self.assertEqual(forwarder.kwargs["token"], "static-token")
            self.assertIs(conn_with_token.safety_event_forwarder, forwarder)
            self.assertEqual(mint_calls, [])
        finally:
            token_module.resolve_device_identity = original_resolve
            forwarder_module.LessonEventForwarder = original_forwarder


if __name__ == "__main__":
    unittest.main()
