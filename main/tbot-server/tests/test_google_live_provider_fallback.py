import asyncio
import time
import unittest

from core.voice.session_provider.google_live import GoogleLiveProvider


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


class _DummyWebSocket:
    def __init__(self):
        self.sent_messages = []

    async def send(self, payload):
        self.sent_messages.append(payload)


class _VoiceConsentClient:
    async def ensure_voice_allowed(self, _conn):
        return True

class _DummyConn:
    def __init__(self):
        self.config = {
            "voice_mode": {
                "type": "google_live",
                "fallback_to_classic_on_error": True,
            },
            "google_live": {
                "api_key": "test-key",
                "model": "gemini-live-test",
                "aec_enabled": False,
                "aec_required": False,
            },
        }
        self.logger = _DummyLogger()
        self.websocket = _DummyWebSocket()
        self.client_abort = False
        self.classic_start_calls = 0
        self.sample_rate = 24000
        self.session_id = "session-1"
        self.google_live_audio_out_started_at = None
        self.client_is_speaking = False
        self.clear_queue_calls = 0
        self.clear_speak_calls = 0
        self.voice_consent_client = _VoiceConsentClient()

    async def _start_classic_pipeline_session(self):
        self.classic_start_calls += 1

    def clear_queues(self):
        self.clear_queue_calls += 1

    def clearSpeakStatus(self):
        self.clear_speak_calls += 1
        self.client_is_speaking = False


class _FailingClient:
    async def connect(self):
        raise RuntimeError("boom")

    async def send_audio(self, audio_bytes):
        return None

    async def close(self):
        return None

    def receive_events(self):
        return _empty_events()


class _SessionExpiredClient(_FailingClient):
    async def connect(self):
        raise RuntimeError("received 1008 BidiGenerateContent session expired")


class _RecordingClient:
    def __init__(self, events=None):
        self.connected = False
        self.connect_calls = 0
        self.audio_packets = []
        self.closed = False
        self._events = list(events or [])
        self.audio_stream_end_calls = 0
        self.interrupt_calls = 0
        self.text_messages = []

    async def connect(self):
        self.connect_calls += 1
        self.connected = True

    async def send_audio(self, audio_bytes):
        self.audio_packets.append(audio_bytes)

    async def end_audio_stream(self):
        self.audio_stream_end_calls += 1

    async def close(self):
        self.closed = True

    async def receive_events(self):
        for event in self._events:
            yield event

    async def interrupt(self):
        self.interrupt_calls += 1

    async def send_text(self, text):
        self.text_messages.append(text)

class _DisconnectedEndStreamClient(_RecordingClient):
    async def end_audio_stream(self):
        if not self.connected:
            raise RuntimeError("Google Live client not connected")
        await super().end_audio_stream()

class _SendFailingClient(_RecordingClient):
    async def send_audio(self, audio_bytes):
        raise RuntimeError("send failed")

class _PassThroughBridge:
    def __init__(self, client):
        self.client = client

    async def forward_input_audio(self, audio_bytes):
        await self.client.send_audio(audio_bytes)

class _RecordingClassicFallback:
    def __init__(self, events):
        self.events = events
        self.spoken_notices = []

    async def start_session(self):
        self.events.append(("classic_start", None))

    async def speak_child_notice(self, text):
        self.events.append(("classic_speak", text))
        self.spoken_notices.append(text)

class _DecodedBridge:
    def __init__(self, client, pcm_audio, rms):
        self.client = client
        self.pcm_audio = pcm_audio
        self.rms = rms

    def decode_input_audio(self, audio_bytes):
        return self.pcm_audio

    def input_rms(self, pcm_audio):
        return self.rms

    async def forward_decoded_input_audio(self, pcm_audio):
        await self.client.send_audio(pcm_audio)

class _AsyncDecodedBridge(_DecodedBridge):
    def __init__(self, client, pcm_audio, rms):
        super().__init__(client, pcm_audio, rms)
        self.sync_decode_calls = 0
        self.async_decode_calls = 0

    def decode_input_audio(self, audio_bytes):
        self.sync_decode_calls += 1
        return b"sync-pcm"

    async def decode_input_audio_async(self, audio_bytes):
        self.async_decode_calls += 1
        return self.pcm_audio

class _RecordingBridge:
    def __init__(self):
        self.events = []
        self.stop_output_calls = 0
        self.allow_model_output_calls = 0

    async def handle_event(self, event):
        self.events.append(event)

    def current_response_id(self):
        return None

    async def stop_output(self):
        self.stop_output_calls += 1

    def allow_model_output(self):
        self.allow_model_output_calls += 1

class _DelayedReceiveClient(_RecordingClient):
    def __init__(self, events=None):
        super().__init__(events=events)
        self.release_event = asyncio.Event()

    async def receive_events(self):
        await self.release_event.wait()
        for event in self._events:
            yield event

class _PersistentReceiveClient(_DelayedReceiveClient):
    async def receive_events(self):
        await self.release_event.wait()
        for event in self._events:
            yield event
        await asyncio.Event().wait()

class _ReceiveFailingClient(_RecordingClient):
    async def receive_events(self):
        raise RuntimeError("receive failed")
        if False:
            yield None

class _ReceiveEndingClient(_RecordingClient):
    async def receive_events(self):
        if False:
            yield None


class _ReconnectOnceClient(_RecordingClient):
    async def receive_events(self):
        raise RuntimeError("receive failed")
        if False:
            yield None

class _SlowReconnectClient(_RecordingClient):
    async def receive_events(self):
        raise RuntimeError("receive failed")
        if False:
            yield None


class _EventThenFailClient(_RecordingClient):
    def __init__(self, events=None):
        super().__init__(events=events)
        self.release_event = asyncio.Event()
        self._failed = False

    async def receive_events(self):
        await self.release_event.wait()
        for event in self._events:
            yield event
        if not self._failed:
            self._failed = True
            raise RuntimeError("receive failed again")


class _SequencedClientFactory:
    def __init__(self, clients):
        self.clients = list(clients)
        self.calls = 0

    def __call__(self, config, logger):
        client = self.clients[self.calls]
        self.calls += 1
        return client


async def _empty_events():
    if False:
        yield None


class GoogleLiveProviderFallbackTest(unittest.IsolatedAsyncioTestCase):
    async def test_start_session_does_not_fallback_to_classic_provider_on_init_failure(self):
        conn = _DummyConn()
        provider = GoogleLiveProvider(
            conn,
            client_factory=lambda config, logger: _FailingClient(),
        )

        await provider.start_session()

        self.assertEqual(conn.classic_start_calls, 0)
        voice_provider = getattr(conn, "voice_provider", None)
        self.assertNotEqual(
            getattr(voice_provider, "__class__", type(None)).__name__,
            "ClassicPipelineProvider",
        )
        warning_messages = [
            args[0]
            for level, args, _kwargs in conn.logger.messages
            if level == "warning" and args
        ]
        self.assertFalse(
            any("fallback_triggered" in message for message in warning_messages)
        )

    async def test_start_session_retries_without_stale_resumption_before_classic_fallback(self):
        conn = _DummyConn()
        conn.google_live_session_resumption_handle = "stale-resume-handle"
        configs = []
        clients = [_SessionExpiredClient(), _PersistentReceiveClient()]

        def client_factory(config, logger):
            configs.append(dict(config))
            return clients[len(configs) - 1]

        provider = GoogleLiveProvider(conn, client_factory=client_factory)

        await provider.start_session()
        await provider.close()

        self.assertEqual(len(configs), 2)
        self.assertEqual(configs[0]["session_resumption_handle"], "stale-resume-handle")
        self.assertNotIn("session_resumption_handle", configs[1])
        self.assertEqual(conn.classic_start_calls, 0)
        self.assertIs(conn.voice_provider, provider)
        warning_messages = [
            args[0]
            for level, args, _kwargs in conn.logger.messages
            if level == "warning" and args
        ]
        self.assertTrue(
            any("retrying_without_session_resumption" in message for message in warning_messages),
            warning_messages,
        )

    async def test_handle_audio_bytes_forwards_to_live_client_after_start(self):
        conn = _DummyConn()
        client = _PersistentReceiveClient()
        provider = GoogleLiveProvider(
            conn,
            client_factory=lambda config, logger: client,
        )

        await provider.start_session()
        provider._bridge = _PassThroughBridge(client)
        handled = await provider.handle_audio_bytes(b"pcm-frame")
        await provider.close()

        self.assertTrue(handled)
        self.assertTrue(client.connected)
        self.assertEqual(client.audio_packets, [b"pcm-frame"])
        self.assertTrue(client.closed)

    async def test_handle_audio_bytes_prefers_async_decode_path(self):
        conn = _DummyConn()
        client = _PersistentReceiveClient()
        provider = GoogleLiveProvider(
            conn,
            client_factory=lambda config, logger: client,
        )

        await provider.start_session()
        bridge = _AsyncDecodedBridge(client, b"async-pcm", rms=100)
        provider._bridge = bridge
        handled = await provider.handle_audio_bytes(b"opus-frame")
        await provider.close()

        self.assertTrue(handled)
        self.assertEqual(bridge.async_decode_calls, 1)
        self.assertEqual(bridge.sync_decode_calls, 0)
        self.assertEqual(client.audio_packets, [b"async-pcm"])

    async def test_start_session_is_idempotent_for_active_live_client(self):
        conn = _DummyConn()
        client = _PersistentReceiveClient()
        provider = GoogleLiveProvider(
            conn,
            client_factory=lambda config, logger: client,
        )

        await provider.start_session()
        await provider.start_session()
        await provider.close()

        self.assertEqual(client.connect_calls, 1)

    async def test_idle_audio_flush_signals_audio_stream_end(self):
        conn = _DummyConn()
        conn.config["google_live"]["input_flush_delay_sec"] = 0.01
        client = _PersistentReceiveClient()
        provider = GoogleLiveProvider(
            conn,
            client_factory=lambda config, logger: client,
        )

        await provider.start_session()
        provider._bridge = _PassThroughBridge(client)
        await provider.handle_audio_bytes(b"pcm-frame")
        deadline = time.monotonic() + 0.2
        while client.audio_stream_end_calls == 0 and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        await provider.close()

        self.assertEqual(client.audio_packets, [b"pcm-frame"])
        self.assertEqual(client.audio_stream_end_calls, 1)

    async def test_interrupt_cancels_pending_idle_flush(self):
        conn = _DummyConn()
        conn.config["google_live"]["input_flush_delay_sec"] = 0.05
        client = _RecordingClient()
        provider = GoogleLiveProvider(
            conn,
            client_factory=lambda config, logger: client,
        )

        await provider.start_session()
        provider._bridge = _PassThroughBridge(client)
        await provider.handle_audio_bytes(b"pcm-frame")
        await provider.interrupt()
        await asyncio.sleep(0.06)
        await provider.close()

        # Interrupt now forces a turn boundary by calling end_audio_stream once.
        # The pending idle flush timer must still be cancelled so we never get
        # a second flush call from the timer firing after 50ms.
        self.assertEqual(client.audio_stream_end_calls, 1)

    async def test_close_suppresses_pending_idle_flush_runtime_warning(self):
        conn = _DummyConn()
        conn.config["google_live"]["input_flush_delay_sec"] = 0.01
        client = _DisconnectedEndStreamClient()
        provider = GoogleLiveProvider(
            conn,
            client_factory=lambda config, logger: client,
        )

        await provider.start_session()
        provider._bridge = _PassThroughBridge(client)
        await provider.handle_audio_bytes(b"pcm-frame")
        provider._closing = True
        client.connected = False
        await asyncio.sleep(0.02)
        await provider.close()

        warning_messages = [
            args[0]
            for level, args, _kwargs in conn.logger.messages
            if level == "warning" and args
        ]
        self.assertFalse(
            any("runtime failure" in message for message in warning_messages)
        )

    async def test_default_audio_during_live_output_is_suppressed_as_echo(self):
        conn = _DummyConn()
        conn.client_is_speaking = True
        conn.google_live_audio_out_started_at = time.monotonic()
        conn.client_abort = True
        conn.config["google_live"]["barge_in"] = False
        client = _RecordingClient()
        provider = GoogleLiveProvider(
            conn,
            client_factory=lambda config, logger: client,
        )

        await provider.start_session()
        provider._bridge = _PassThroughBridge(client)
        handled = await provider.handle_audio_bytes(b"pcm-frame")
        await provider.close()

        self.assertTrue(handled)
        self.assertEqual(client.interrupt_calls, 0)
        self.assertEqual(client.audio_packets, [])
        self.assertFalse(conn.client_abort)

    async def test_explicit_drop_input_during_output_preserves_old_behavior(self):
        conn = _DummyConn()
        conn.client_is_speaking = True
        conn.google_live_audio_out_started_at = time.monotonic()
        conn.client_abort = True
        conn.config["google_live"]["barge_in"] = False
        conn.config["google_live"]["interrupt_on_input_while_speaking"] = False
        conn.config["google_live"]["drop_input_while_speaking"] = True
        client = _RecordingClient()
        provider = GoogleLiveProvider(
            conn,
            client_factory=lambda config, logger: client,
        )

        await provider.start_session()
        provider._bridge = _PassThroughBridge(client)
        handled = await provider.handle_audio_bytes(b"pcm-frame")
        await provider.close()

        self.assertTrue(handled)
        self.assertEqual(client.interrupt_calls, 0)
        self.assertEqual(client.audio_packets, [])
        self.assertFalse(conn.client_abort)

    async def test_stale_speaking_status_does_not_drop_new_user_audio(self):
        conn = _DummyConn()
        conn.client_is_speaking = True
        conn.google_live_audio_out_started_at = None
        conn.config["google_live"]["barge_in"] = False
        client = _RecordingClient()
        provider = GoogleLiveProvider(
            conn,
            client_factory=lambda config, logger: client,
        )

        await provider.start_session()
        provider._bridge = _PassThroughBridge(client)
        handled = await provider.handle_audio_bytes(b"pcm-frame")
        await provider.close()

        self.assertTrue(handled)
        self.assertEqual(client.audio_packets, [b"pcm-frame"])

    async def test_barge_in_config_allows_continuous_input_audio(self):
        conn = _DummyConn()
        conn.client_is_speaking = True
        conn.config["google_live"]["barge_in"] = True
        client = _RecordingClient()
        provider = GoogleLiveProvider(
            conn,
            client_factory=lambda config, logger: client,
        )

        await provider.start_session()
        provider._bridge = _PassThroughBridge(client)
        await provider.handle_audio_bytes(b"pcm-frame-1")
        await provider.handle_audio_bytes(b"pcm-frame-2")
        await provider.close()

        self.assertEqual(client.interrupt_calls, 0)
        self.assertEqual(client.audio_packets, [b"pcm-frame-1", b"pcm-frame-2"])

    async def test_input_audio_is_forwarded_while_speaking_when_barge_in_enabled(self):
        conn = _DummyConn()
        conn.client_is_speaking = True
        conn.config["google_live"]["barge_in"] = True
        client = _RecordingClient()
        provider = GoogleLiveProvider(
            conn,
            client_factory=lambda config, logger: client,
        )

        await provider.start_session()
        provider._bridge = _PassThroughBridge(client)
        handled = await provider.handle_audio_bytes(b"pcm-frame")
        await provider.close()

        self.assertTrue(handled)
        self.assertEqual(client.audio_packets, [b"pcm-frame"])

    async def test_barge_in_flag_with_stale_speaking_status_forwards_audio(self):
        conn = _DummyConn()
        conn.client_is_speaking = True
        conn.config["google_live"]["barge_in"] = True
        conn.config["google_live"]["barge_in_rms_threshold"] = 600
        conn.config["google_live"]["barge_in_min_input_duration_sec"] = 0.0
        client = _RecordingClient()
        provider = GoogleLiveProvider(
            conn,
            client_factory=lambda config, logger: client,
        )

        await provider.start_session()
        provider._bridge = _DecodedBridge(client, b"decoded-pcm", rms=1200)
        handled = await provider.handle_audio_bytes(b"opus-frame")
        await provider.close()

        self.assertTrue(handled)
        self.assertEqual(client.interrupt_calls, 0)
        self.assertEqual(client.audio_packets, [b"decoded-pcm"])
        self.assertFalse(conn.client_abort)
        self.assertTrue(conn.client_is_speaking)
        self.assertEqual(conn.clear_queue_calls, 0)
        self.assertEqual(conn.clear_speak_calls, 0)

    async def test_single_echo_frame_does_not_barge_in_when_duration_required(self):
        conn = _DummyConn()
        conn.client_is_speaking = True
        conn.google_live_audio_out_started_at = time.monotonic() - 2
        conn.config["google_live"]["barge_in"] = True
        conn.config["google_live"]["barge_in_rms_threshold"] = 600
        conn.config["google_live"]["barge_in_min_input_duration_sec"] = 0.18
        client = _RecordingClient()
        provider = GoogleLiveProvider(
            conn,
            client_factory=lambda config, logger: client,
        )

        await provider.start_session()
        provider._bridge = _DecodedBridge(client, b"echo-pcm", rms=2500)
        handled = await provider.handle_audio_bytes(b"opus-frame")
        await provider.close()

        self.assertTrue(handled)
        self.assertEqual(client.interrupt_calls, 0)
        self.assertEqual(client.audio_packets, [])
        self.assertFalse(conn.client_abort)
        self.assertTrue(conn.client_is_speaking)

    async def test_sustained_loud_input_does_not_barge_in_without_explicit_bypass(self):
        conn = _DummyConn()
        conn.client_is_speaking = True
        conn.google_live_audio_out_started_at = time.monotonic() - 2
        conn.config["google_live"]["barge_in"] = True
        conn.config["google_live"]["barge_in_rms_threshold"] = 600
        conn.config["google_live"]["barge_in_min_input_duration_sec"] = 0.18
        client = _RecordingClient()
        provider = GoogleLiveProvider(
            conn,
            client_factory=lambda config, logger: client,
        )

        await provider.start_session()
        provider._bridge = _DecodedBridge(client, b"user-pcm", rms=2500)
        await provider.handle_audio_bytes(b"opus-frame-1")
        await provider.handle_audio_bytes(b"opus-frame-2")
        handled = await provider.handle_audio_bytes(b"opus-frame-3")
        await provider.close()

        self.assertTrue(handled)
        self.assertEqual(client.interrupt_calls, 0)
        self.assertEqual(client.audio_packets, [])
        self.assertFalse(conn.client_abort)
        self.assertTrue(conn.client_is_speaking)

    async def test_new_audio_interrupts_active_live_response_and_forwards_latest_input(self):
        conn = _DummyConn()
        conn.client_is_speaking = True
        conn.google_live_audio_out_started_at = time.monotonic() - 2
        conn.config["google_live"]["interrupt_on_input_while_speaking"] = True
        conn.config["google_live"]["interrupt_rms_threshold"] = 600
        conn.config["google_live"]["interrupt_min_input_duration_sec"] = 0.0
        client = _RecordingClient()
        provider = GoogleLiveProvider(
            conn,
            client_factory=lambda config, logger: client,
        )

        await provider.start_session()
        provider._bridge = _DecodedBridge(client, b"new-user-pcm", rms=1200)
        handled = await provider.handle_audio_bytes(b"opus-frame")
        await provider.close()

        self.assertTrue(handled)
        self.assertEqual(client.interrupt_calls, 0)
        self.assertEqual(client.audio_packets, [])
        self.assertEqual(conn.clear_queue_calls, 0)
        self.assertFalse(conn.client_abort)
        self.assertTrue(conn.client_is_speaking)

    async def test_interrupt_on_input_ignores_loud_echo_at_output_start(self):
        conn = _DummyConn()
        conn.client_is_speaking = True
        conn.google_live_audio_out_started_at = time.monotonic()
        conn.config["google_live"]["interrupt_on_input_while_speaking"] = True
        conn.config["google_live"]["interrupt_rms_threshold"] = 600
        conn.config["google_live"]["interrupt_min_output_age_sec"] = 1.5
        client = _RecordingClient()
        provider = GoogleLiveProvider(
            conn,
            client_factory=lambda config, logger: client,
        )

        await provider.start_session()
        provider._bridge = _DecodedBridge(client, b"echo-pcm", rms=5000)
        handled = await provider.handle_audio_bytes(b"opus-frame")
        await provider.close()

        self.assertTrue(handled)
        self.assertEqual(client.interrupt_calls, 0)
        self.assertEqual(client.audio_packets, [])

    async def test_repeated_interrupts_advance_response_generation_without_fatal_log(self):
        conn = _DummyConn()
        client = _RecordingClient()
        provider = GoogleLiveProvider(
            conn,
            client_factory=lambda config, logger: client,
        )

        await provider.start_session()
        first_generation = provider.current_response_id()
        await provider.interrupt()
        second_generation = provider.current_response_id()
        await provider.interrupt()
        third_generation = provider.current_response_id()
        await provider.close()

        self.assertLess(first_generation, second_generation)
        self.assertLess(second_generation, third_generation)
        self.assertEqual(client.interrupt_calls, 2)
        self.assertEqual(conn.clear_queue_calls, 2)
        self.assertFalse(
            any(level == "error" for level, _args, _kwargs in conn.logger.messages)
        )

    async def test_text_detect_interrupts_active_response_and_sends_latest_text(self):
        conn = _DummyConn()
        conn.client_is_speaking = True
        conn.google_live_audio_out_started_at = time.monotonic()
        client = _RecordingClient()
        provider = GoogleLiveProvider(
            conn,
            client_factory=lambda config, logger: client,
        )

        await provider.start_session()
        handled = await provider.handle_text_message(
            '{"type":"listen","state":"detect","text":"tiếp tục"}'
        )
        await provider.close()

        self.assertTrue(handled)
        self.assertEqual(client.interrupt_calls, 1)
        self.assertEqual(client.text_messages, ["tiếp tục"])
        self.assertFalse(conn.client_abort)
        self.assertFalse(conn.client_is_speaking)

    async def test_text_detect_unlocks_model_output_after_interrupt(self):
        conn = _DummyConn()
        conn.client_is_speaking = True
        conn.google_live_audio_out_started_at = time.monotonic()
        client = _RecordingClient()
        provider = GoogleLiveProvider(
            conn,
            client_factory=lambda config, logger: client,
        )
        bridge = _RecordingBridge()

        await provider.start_session()
        provider._bridge = bridge
        handled = await provider.handle_text_message(
            '{"type":"listen","state":"detect","text":"tiếp tục"}'
        )
        await provider.close()

        self.assertTrue(handled)
        self.assertEqual(bridge.stop_output_calls, 1)
        self.assertEqual(bridge.allow_model_output_calls, 1)

    async def test_quiet_input_audio_does_not_interrupt_live_output(self):
        conn = _DummyConn()
        conn.client_is_speaking = True
        conn.config["google_live"]["barge_in"] = True
        conn.config["google_live"]["barge_in_rms_threshold"] = 600
        client = _RecordingClient()
        provider = GoogleLiveProvider(
            conn,
            client_factory=lambda config, logger: client,
        )

        await provider.start_session()
        provider._bridge = _DecodedBridge(client, b"decoded-pcm", rms=100)
        handled = await provider.handle_audio_bytes(b"opus-frame")
        await provider.close()

        self.assertTrue(handled)
        self.assertEqual(client.interrupt_calls, 0)
        self.assertEqual(client.audio_packets, [b"decoded-pcm"])

    async def test_barge_in_ignores_echo_at_start_of_assistant_audio(self):
        conn = _DummyConn()
        conn.client_is_speaking = True
        conn.google_live_audio_out_started_at = time.monotonic()
        conn.config["google_live"]["barge_in"] = True
        conn.config["google_live"]["barge_in_rms_threshold"] = 600
        conn.config["google_live"]["barge_in_min_output_age_sec"] = 1.5
        client = _RecordingClient()
        provider = GoogleLiveProvider(
            conn,
            client_factory=lambda config, logger: client,
        )

        await provider.start_session()
        provider._bridge = _DecodedBridge(client, b"decoded-pcm", rms=5000)
        handled = await provider.handle_audio_bytes(b"opus-frame")
        await provider.close()

        self.assertTrue(handled)
        self.assertEqual(client.interrupt_calls, 0)
        self.assertEqual(client.audio_packets, [])

    async def test_audio_during_reconnect_replays_buffered_frames_in_order(self):
        conn = _DummyConn()
        client = _RecordingClient()
        provider = GoogleLiveProvider(
            conn,
            client_factory=lambda config, logger: client,
        )

        await provider.start_session()
        provider._reconnecting = True
        provider._bridge = _PassThroughBridge(client)
        first_handled = await provider.handle_audio_bytes(b"old-pcm-frame")
        second_handled = await provider.handle_audio_bytes(b"latest-pcm-frame")
        provider._reconnecting = False
        await provider._forward_pending_reconnect_audio()
        await provider.close()

        self.assertTrue(first_handled)
        self.assertTrue(second_handled)
        self.assertEqual(
            client.audio_packets, [b"old-pcm-frame", b"latest-pcm-frame"]
        )

    async def test_reconnect_buffer_drops_oldest_when_capacity_exceeded(self):
        conn = _DummyConn()
        conn.config["google_live"]["reconnect_buffer_ms"] = 120
        conn.config["google_live"]["input_frame_duration_ms"] = 60
        client = _RecordingClient()
        provider = GoogleLiveProvider(
            conn,
            client_factory=lambda config, logger: client,
        )

        await provider.start_session()
        provider._reconnecting = True
        provider._bridge = _PassThroughBridge(client)
        await provider.handle_audio_bytes(b"frame-1")
        await provider.handle_audio_bytes(b"frame-2")
        await provider.handle_audio_bytes(b"frame-3")
        provider._reconnecting = False
        await provider._forward_pending_reconnect_audio()
        await provider.close()

        self.assertEqual(client.audio_packets, [b"frame-2", b"frame-3"])

    async def test_receive_loop_forwards_live_events_to_bridge(self):
        conn = _DummyConn()
        client = _DelayedReceiveClient(
            events=[{"type": "audio", "audio": b"live-audio-chunk"}]
        )
        provider = GoogleLiveProvider(
            conn,
            client_factory=lambda config, logger: client,
        )
        bridge = _RecordingBridge()

        await provider.start_session()
        provider._bridge = bridge
        client.release_event.set()
        await asyncio.sleep(0)
        await provider.close()

        self.assertEqual(bridge.events, [{"type": "audio", "audio": b"live-audio-chunk"}])

    async def test_send_failure_after_start_reconnects_before_fallback(self):
        conn = _DummyConn()
        client = _SendFailingClient()
        provider = GoogleLiveProvider(conn, client_factory=lambda config, logger: client)

        await provider.start_session()
        provider._bridge = _PassThroughBridge(client)
        handled = await provider.handle_audio_bytes(b"pcm-frame")
        await provider.close()

        self.assertTrue(handled)
        self.assertEqual(conn.classic_start_calls, 0)
        self.assertIs(conn.voice_provider, provider)

    async def test_receive_failure_after_start_reconnects_before_fallback(self):
        conn = _DummyConn()
        provider = GoogleLiveProvider(
            conn,
            client_factory=lambda config, logger: _ReceiveFailingClient(),
        )

        await provider.start_session()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        await provider.close()

        self.assertEqual(conn.classic_start_calls, 0)
        self.assertIs(conn.voice_provider, provider)

    async def test_receive_failure_reconnects_before_fallback_when_budget_available(self):
        conn = _DummyConn()
        conn.config["google_live"]["reconnect"] = {
            "enabled": True,
            "max_retries": 1,
            "backoff_ms": 0,
        }
        healthy_client = _PersistentReceiveClient(
            events=[{"type": "audio", "audio": b"reconnected-audio"}]
        )
        client_factory = _SequencedClientFactory(
            [_ReconnectOnceClient(), healthy_client]
        )
        provider = GoogleLiveProvider(conn, client_factory=client_factory)
        bridge = _RecordingBridge()

        await provider.start_session()
        await asyncio.sleep(0)
        provider._bridge = bridge
        healthy_client.release_event.set()
        await asyncio.sleep(0)
        await provider.close()

        self.assertEqual(client_factory.calls, 2)
        self.assertEqual(conn.classic_start_calls, 0)
        self.assertEqual(conn.voice_provider, provider)
        self.assertEqual(
            bridge.events,
            [{"type": "audio", "audio": b"reconnected-audio"}],
        )

    async def test_goaway_receive_loop_reconnects_with_session_resumption_handle(self):
        conn = _DummyConn()
        conn.config["google_live"]["reconnect"] = {
            "enabled": True,
            "max_retries": 1,
            "backoff_ms": 0,
        }
        expiring_client = _PersistentReceiveClient(
            events=[
                {
                    "type": "session_resumption_update",
                    "handle": "resume-handle-1",
                    "resumable": True,
                },
                {"type": "session_expiring", "time_left_ms": 5000},
            ]
        )
        healthy_client = _PersistentReceiveClient()
        configs = []

        def client_factory(config, logger):
            configs.append(dict(config))
            return [expiring_client, healthy_client][len(configs) - 1]

        provider = GoogleLiveProvider(conn, client_factory=client_factory)

        await provider.start_session()
        expiring_client.release_event.set()
        deadline = time.monotonic() + 1.0
        while len(configs) < 2 and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        await provider.close()

        self.assertEqual(len(configs), 2)
        self.assertEqual(conn.google_live_session_resumption_handle, "resume-handle-1")
        self.assertEqual(configs[1]["session_resumption_handle"], "resume-handle-1")
        self.assertTrue(configs[1]["session_resumption_enabled"])
        self.assertEqual(conn.classic_start_calls, 0)
        self.assertIs(conn.voice_provider, provider)

    async def test_receive_loop_end_reconnects_before_silence(self):
        conn = _DummyConn()
        conn.config["google_live"]["reconnect"] = {
            "enabled": True,
            "max_retries": 1,
            "backoff_ms": 0,
        }
        healthy_client = _PersistentReceiveClient(
            events=[{"type": "audio", "audio": b"after-loop-end"}]
        )
        client_factory = _SequencedClientFactory(
            [_ReceiveEndingClient(), healthy_client]
        )
        provider = GoogleLiveProvider(conn, client_factory=client_factory)
        bridge = _RecordingBridge()

        await provider.start_session()
        await asyncio.sleep(0)
        provider._bridge = bridge
        healthy_client.release_event.set()
        await asyncio.sleep(0)
        await provider.close()

        self.assertEqual(client_factory.calls, 2)
        self.assertEqual(conn.classic_start_calls, 0)
        self.assertEqual(conn.voice_provider, provider)
        self.assertEqual(bridge.events, [{"type": "audio", "audio": b"after-loop-end"}])

    async def test_reconnect_config_supports_exponential_backoff_multiplier(self):
        conn = _DummyConn()
        conn.config["google_live"]["reconnect"] = {
            "enabled": True,
            "max_retries": 3,
            "backoff_ms": 500,
            "backoff_multiplier": 2,
        }
        provider = GoogleLiveProvider(conn)

        self.assertEqual(provider._get_reconnect_delay_ms(1), 500)
        self.assertEqual(provider._get_reconnect_delay_ms(2), 1000)
        self.assertEqual(provider._get_reconnect_delay_ms(3), 2000)

    async def test_reconnect_retries_until_budget_exhausted_before_fallback(self):
        conn = _DummyConn()
        conn.config["google_live"]["reconnect"] = {
            "enabled": True,
            "max_retries": 3,
            "backoff_ms": 0,
            "backoff_multiplier": 2,
        }
        healthy_client = _PersistentReceiveClient(
            events=[{"type": "audio", "audio": b"after-retry-budget"}]
        )
        client_factory = _SequencedClientFactory(
            [
                _ReconnectOnceClient(),
                _FailingClient(),
                _FailingClient(),
                healthy_client,
            ]
        )
        provider = GoogleLiveProvider(conn, client_factory=client_factory)
        bridge = _RecordingBridge()

        await provider.start_session()
        await asyncio.sleep(0)
        provider._bridge = bridge
        healthy_client.release_event.set()
        await asyncio.sleep(0)
        await provider.close()

        self.assertEqual(client_factory.calls, 4)
        self.assertEqual(conn.classic_start_calls, 0)
        self.assertEqual(conn.voice_provider, provider)
        self.assertEqual(
            bridge.events,
            [{"type": "audio", "audio": b"after-retry-budget"}],
        )

    async def test_reconnect_exhaustion_does_not_fallback_to_classic(self):
        conn = _DummyConn()
        conn.config["google_live"]["reconnect"] = {
            "enabled": True,
            "max_retries": 2,
            "backoff_ms": 0,
        }
        client_factory = _SequencedClientFactory(
            [_ReconnectOnceClient(), _FailingClient(), _FailingClient()]
        )
        provider = GoogleLiveProvider(conn, client_factory=client_factory)

        await provider.start_session()
        await asyncio.sleep(0)

        self.assertEqual(client_factory.calls, 3)
        self.assertEqual(conn.classic_start_calls, 0)
        self.assertIs(conn.voice_provider, provider)

    async def test_runtime_error_classification_keeps_logs_secret_free(self):
        conn = _DummyConn()
        provider = GoogleLiveProvider(conn)

        self.assertEqual(provider._classify_error(RuntimeError("API key is missing")), "auth")
        self.assertEqual(provider._classify_error(RuntimeError("quota exceeded 429")), "quota")
        self.assertEqual(provider._classify_error(RuntimeError("model is missing")), "invalid_config")
        self.assertEqual(provider._classify_error(RuntimeError("Google Live receive loop ended")), "stream_closed")
        self.assertEqual(provider._classify_error(RuntimeError("keepalive ping timeout")), "network")

    async def test_successful_reconnect_resets_budget_for_later_failure(self):
        conn = _DummyConn()
        conn.config["google_live"]["reconnect"] = {
            "enabled": True,
            "max_retries": 1,
            "backoff_ms": 0,
        }
        second_client = _EventThenFailClient(
            events=[{"type": "audio", "audio": b"after-first-reconnect"}]
        )
        third_client = _PersistentReceiveClient(
            events=[{"type": "audio", "audio": b"after-second-reconnect"}]
        )
        client_factory = _SequencedClientFactory(
            [_ReconnectOnceClient(), second_client, third_client]
        )
        provider = GoogleLiveProvider(conn, client_factory=client_factory)
        bridge = _RecordingBridge()

        await provider.start_session()
        await asyncio.sleep(0)
        provider._bridge = bridge
        second_client.release_event.set()
        await asyncio.sleep(0)
        provider._bridge = bridge
        third_client.release_event.set()
        await asyncio.sleep(0)
        await provider.close()

        self.assertEqual(client_factory.calls, 3)
        self.assertEqual(conn.classic_start_calls, 0)
        self.assertEqual(conn.voice_provider, provider)
        self.assertEqual(
            bridge.events,
            [
                {"type": "audio", "audio": b"after-first-reconnect"},
                {"type": "audio", "audio": b"after-second-reconnect"},
            ],
        )

    async def test_runtime_failure_stops_output_before_reconnect(self):
        conn = _DummyConn()
        conn.config["google_live"]["reconnect"] = {
            "enabled": True,
            "max_retries": 1,
            "backoff_ms": 0,
        }
        healthy_client = _PersistentReceiveClient()
        client_factory = _SequencedClientFactory(
            [_ReconnectOnceClient(), healthy_client]
        )
        provider = GoogleLiveProvider(conn, client_factory=client_factory)
        bridge = _RecordingBridge()

        await provider.start_session()
        provider._bridge = bridge
        await asyncio.sleep(0)
        await provider.close()

        self.assertEqual(bridge.stop_output_calls, 1)

    async def test_close_during_reconnect_does_not_open_new_live_session(self):
        conn = _DummyConn()
        conn.config["google_live"]["reconnect"] = {
            "enabled": True,
            "max_retries": 1,
            "backoff_ms": 50,
        }
        client_factory = _SequencedClientFactory(
            [_SlowReconnectClient(), _RecordingClient()]
        )
        provider = GoogleLiveProvider(conn, client_factory=client_factory)

        await provider.start_session()
        await asyncio.sleep(0)
        await provider.close()
        await asyncio.sleep(0.06)

        self.assertEqual(client_factory.calls, 1)
        self.assertEqual(conn.classic_start_calls, 0)
        self.assertEqual(conn.voice_provider, provider)

    async def test_interrupted_flush_task_cannot_end_new_audio_stream(self):
        conn = _DummyConn()
        conn.config["google_live"]["input_flush_delay_sec"] = 0.01
        client = _PersistentReceiveClient()
        provider = GoogleLiveProvider(
            conn,
            client_factory=lambda config, logger: client,
        )

        await provider.start_session()
        provider._bridge = _PassThroughBridge(client)
        await provider.handle_audio_bytes(b"old-pcm")
        provider._input_flush_generation += 1
        await asyncio.sleep(0.02)
        await provider.close()

        self.assertEqual(client.audio_stream_end_calls, 0)

    async def test_runtime_logs_mask_google_api_keys(self):
        conn = _DummyConn()
        provider = GoogleLiveProvider(conn)
        fake_key = "AIza" + "SyDD3tLwoZjDZK9iv3JIDVtoCqF68g4nMao"

        safe_message = provider._safe_error_message(
            RuntimeError(f"bad key {fake_key}")
        )

        self.assertNotIn(fake_key, safe_message)
        self.assertIn("AIza***", safe_message)

    async def test_quota_failure_does_not_send_child_notice_or_swap_provider(self):
        events = []
        conn = _DummyConn()

        original_send = conn.websocket.send

        async def _recording_send(payload):
            events.append(("notice", payload))
            await original_send(payload)

        conn.websocket.send = _recording_send

        provider = GoogleLiveProvider(conn)
        self.assertFalse(
            await provider._activate_classic_fallback(RuntimeError("quota exceeded 429"))
        )

        self.assertEqual(events, [])
        self.assertEqual(conn.classic_start_calls, 0)
        self.assertIsNone(getattr(conn, "voice_provider", None))

    async def test_quota_failure_does_not_start_or_speak_classic_notice(self):
        events = []
        conn = _DummyConn()
        provider = GoogleLiveProvider(conn)
        fallback = _RecordingClassicFallback(events)

        def _recording_factory(_conn):
            events.append(("swap", None))
            return fallback

        provider._classic_provider_factory = _recording_factory

        self.assertFalse(
            await provider._activate_classic_fallback(RuntimeError("quota exceeded 429"))
        )

        self.assertEqual(events, [])
        self.assertEqual(fallback.spoken_notices, [])

    async def test_quota_failure_notice_never_contains_raw_api_key(self):
        # FIX B: the alert payload must never carry the raw exception (which can contain
        # an API key) — it uses a fixed child-friendly message.
        conn = _DummyConn()
        provider = GoogleLiveProvider(conn)
        fake_key = "AIza" + "SyDD3tLwoZjDZK9iv3JIDVtoCqF68g4nMao"

        await provider._send_fallback_notice(
            RuntimeError(f"quota 429 for key {fake_key}")
        )

        self.assertTrue(conn.websocket.sent_messages)
        self.assertFalse(
            any(fake_key in payload for payload in conn.websocket.sent_messages)
        )

    async def test_benign_stream_closed_failure_emits_no_child_notice(self):
        # FIX B: a benign normal close (stream_closed / unknown) must NOT narrate a
        # spurious "robot needs a break" alert to the child.
        conn = _DummyConn()
        provider = GoogleLiveProvider(conn)

        await provider._activate_classic_fallback(
            RuntimeError("Google Live receive loop ended")  # -> stream_closed
        )

        alerts = [
            payload for payload in conn.websocket.sent_messages
            if '"type": "alert"' in payload or '"type":"alert"' in payload
        ]
        self.assertEqual(alerts, [])
        self.assertEqual(conn.classic_start_calls, 0)

    async def test_unknown_failure_emits_no_child_notice(self):
        # FIX B control: the "boom"/unknown init failure path must stay silent (no notice)
        # so we never narrate over a benign close.
        conn = _DummyConn()
        provider = GoogleLiveProvider(conn)

        await provider._send_fallback_notice(RuntimeError("boom"))  # -> unknown

        self.assertEqual(conn.websocket.sent_messages, [])

    async def test_fallback_trigger_log_masks_google_api_keys(self):
        conn = _DummyConn()
        conn.config["voice_mode"]["fallback_to_classic_on_error"] = True
        provider = GoogleLiveProvider(conn)
        fake_key = "AIza" + "SyDD3tLwoZjDZK9iv3JIDVtoCqF68g4nMao"

        await provider._activate_classic_fallback(
            RuntimeError(f"bad key {fake_key}")
        )

        warning_args = [
            args
            for level, args, _kwargs in conn.logger.messages
            if level == "warning"
        ]
        self.assertTrue(warning_args)
        self.assertFalse(
            any(
                fake_key in str(args)
                for args in warning_args
            )
        )
