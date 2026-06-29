"""Tests for PR2 stability fixes: goAway detection, proactive reconnect,
deque replay buffer, non-retriable error classification, recv_timer_reset
observability, and mid-stream blip reconnect routing."""

import asyncio
import unittest
from types import SimpleNamespace

from core.voice.google_live.client import GoogleLiveClient
from core.voice.session_provider.google_live import GoogleLiveProvider


class _Logger:
    def __init__(self):
        self.records = []

    def bind(self, **_):
        return self

    def info(self, *args, **kwargs):  # noqa: D401
        self.records.append(("info", args, kwargs))

    def warning(self, *args, **kwargs):
        self.records.append(("warning", args, kwargs))

    def error(self, *args, **kwargs):
        self.records.append(("error", args, kwargs))

    def debug(self, *args, **kwargs):
        self.records.append(("debug", args, kwargs))

    def has_message(self, fragment, level=None):
        for record_level, args, _kwargs in self.records:
            if level is not None and record_level != level:
                continue
            if not args:
                continue
            try:
                template = str(args[0])
                # loguru-style: args[0] is a {}-placeholder template, args[1:] fill it.
                if "{" in template and len(args) > 1:
                    rendered = template.format(*args[1:])
                else:
                    rendered = template
            except Exception:
                continue
            if fragment in rendered:
                return True
        return False


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
                "reconnect_buffer_ms": 2000,
                "input_frame_duration_ms": 60,
                "reconnect": {
                    "enabled": True,
                    "max_retries": 3,
                    "backoff_ms": 0,
                    "backoff_multiplier": 1,
                },
            },
        }
        self.logger = _Logger()
        self.websocket = None
        self.client_abort = False
        self.sample_rate = 24000
        self.session_id = "s-1"
        self.google_live_audio_out_started_at = None
        self.client_is_speaking = False

        async def _allow_voice(_conn):
            return True

        self.voice_consent_client = SimpleNamespace(
            has_active_ai_voice_consent=lambda _conn: True,
            ensure_voice_allowed=_allow_voice,
        )

    def clear_queues(self):
        pass

    def clearSpeakStatus(self):
        self.client_is_speaking = False


class GoAwayDetectionTest(unittest.TestCase):
    def setUp(self):
        self.client = GoogleLiveClient({}, _Logger())

    def test_go_away_with_iso_duration_string_normalized_to_ms(self):
        message = SimpleNamespace(
            go_away=SimpleNamespace(time_left="12.5s"),
            server_content=None,
        )
        events = self.client._normalize_message(message)
        self.assertIn({"type": "session_expiring", "time_left_ms": 12500}, events)

    def test_go_away_with_struct_seconds_and_nanos(self):
        message = {"go_away": {"time_left": {"seconds": 1, "nanos": 500_000_000}}}
        events = self.client._normalize_message(message)
        self.assertIn({"type": "session_expiring", "time_left_ms": 1500}, events)

    def test_go_away_with_plain_seconds_number(self):
        message = SimpleNamespace(
            go_away=SimpleNamespace(time_left=0.75),
            server_content=None,
        )
        events = self.client._normalize_message(message)
        self.assertIn({"type": "session_expiring", "time_left_ms": 750}, events)

    def test_go_away_without_time_left_field_yields_event_with_none(self):
        message = SimpleNamespace(go_away=SimpleNamespace(time_left=None), server_content=None)
        events = self.client._normalize_message(message)
        self.assertIn({"type": "session_expiring", "time_left_ms": None}, events)

    def test_message_without_go_away_does_not_yield_session_expiring(self):
        message = SimpleNamespace(server_content=None, text=None)
        events = self.client._normalize_message(message)
        self.assertFalse(any(e.get("type") == "session_expiring" for e in events))


class _AlwaysFailClient:
    def __init__(self, *_, **__):
        self.connected = False
        self.connect_calls = 0
        self.close_calls = 0

    async def connect(self):
        self.connect_calls += 1
        raise RuntimeError("API key invalid permission denied")

    async def close(self):
        self.close_calls += 1

    async def send_audio(self, audio_bytes):
        return None

    async def receive_events(self):
        if False:  # pragma: no cover - empty async generator
            yield None


class NonRetriableErrorTest(unittest.IsolatedAsyncioTestCase):
    async def test_auth_error_skips_reconnect_and_falls_back(self):
        conn = _Conn()
        client_holder = {}

        def factory(config, logger):
            client = _AlwaysFailClient(config, logger)
            client_holder["client"] = client
            return client

        classic_started = {"count": 0}

        class _ClassicProvider:
            async def start_session(self):
                classic_started["count"] += 1

            async def close(self):
                pass

            async def handle_text_message(self, _):
                return False

            async def handle_audio_bytes(self, _):
                return False

            async def interrupt(self):
                return None

        provider = GoogleLiveProvider(
            conn,
            client_factory=factory,
            classic_provider_factory=lambda _: _ClassicProvider(),
        )
        await provider.start_session()

        self.assertEqual(client_holder["client"].connect_calls, 1)
        self.assertEqual(classic_started["count"], 1)


class ProactiveReconnectTest(unittest.IsolatedAsyncioTestCase):
    async def test_session_expiring_event_schedules_runtime_failure_path(self):
        conn = _Conn()
        provider = GoogleLiveProvider(
            conn,
            client_factory=lambda *_: _AlwaysFailClient(),
        )

        calls = []

        async def fake_runtime_failure(exc):
            calls.append(str(exc))

        provider._handle_runtime_failure = fake_runtime_failure

        provider._schedule_proactive_reconnect({"time_left_ms": 4321})
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        self.assertEqual(len(calls), 1)
        self.assertIn("session_expiring", calls[0])
        self.assertIn("4321", calls[0])

    async def test_proactive_reconnect_does_not_double_schedule(self):
        conn = _Conn()
        provider = GoogleLiveProvider(
            conn,
            client_factory=lambda *_: _AlwaysFailClient(),
        )
        invocations = []

        async def fake_runtime_failure(exc):
            invocations.append(exc)
            await asyncio.sleep(0)

        provider._handle_runtime_failure = fake_runtime_failure

        provider._schedule_proactive_reconnect({"time_left_ms": 1000})
        provider._schedule_proactive_reconnect({"time_left_ms": 1000})
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        self.assertEqual(len(invocations), 1)


class ReconnectAdmissionAccountingTest(unittest.IsolatedAsyncioTestCase):
    async def test_reconnect_attempt_records_against_admission_gate_before_live_open(self):
        conn = _Conn()
        records = []

        class _Gate:
            def record_reconnect(self, device_id):
                records.append(device_id)

        conn.device_id = "device-1"
        conn.live_admission_gate = _Gate()
        provider = GoogleLiveProvider(conn, client_factory=lambda *_: _AlwaysFailClient())
        opened = {"count": 0}

        async def close_live_resources():
            return None

        async def open_live_session():
            opened["count"] += 1

        async def forward_pending_reconnect_audio():
            return None

        provider._close_live_resources = close_live_resources
        provider._open_live_session = open_live_session
        provider._forward_pending_reconnect_audio = forward_pending_reconnect_audio

        reconnected = await provider._try_reconnect(RuntimeError("stream closed"))

        self.assertTrue(reconnected)
        self.assertEqual(opened["count"], 1)
        self.assertEqual(records, ["device-1"])


class LiveOpenReceiveTaskRaceTest(unittest.IsolatedAsyncioTestCase):
    async def test_open_live_session_cancels_stale_receive_task_before_new_loop(self):
        conn = _Conn()

        class _CloseRecordingClient:
            def __init__(self):
                self.connected = False
                self.closed = False

            async def connect(self):
                self.connected = True

            async def close(self):
                self.closed = True
                self.connected = False

            async def receive_events(self):
                await asyncio.Event().wait()
                if False:
                    yield None

        class _CloseRecordingBridge:
            def __init__(self):
                self.closed = False

            async def close(self):
                self.closed = True

        stale_client = _CloseRecordingClient()
        stale_bridge = _CloseRecordingBridge()
        new_client = _CloseRecordingClient()
        provider = GoogleLiveProvider(
            conn,
            client_factory=lambda *_: new_client,
        )
        stale_cancelled = asyncio.Event()

        async def stale_receive_loop():
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                stale_cancelled.set()
                raise

        stale_task = asyncio.create_task(stale_receive_loop())
        await asyncio.sleep(0)
        provider._receive_task = stale_task
        provider._client = stale_client
        provider._bridge = stale_bridge

        await provider._open_live_session()
        await asyncio.sleep(0)
        new_receive_task = provider._receive_task
        await provider.close()

        self.assertTrue(stale_cancelled.is_set())
        self.assertTrue(stale_task.cancelled())
        self.assertTrue(stale_client.closed)
        self.assertTrue(stale_bridge.closed)
        self.assertIsNot(new_receive_task, stale_task)

    async def test_concurrent_open_live_session_is_serialized(self):
        conn = _Conn()
        first_connect_started = asyncio.Event()
        release_first_connect = asyncio.Event()
        clients = []

        class _BlockingFirstClient:
            def __init__(self, block_connect=False):
                self.block_connect = block_connect
                self.connected = False
                self.closed = False

            async def connect(self):
                if self.block_connect:
                    first_connect_started.set()
                    await release_first_connect.wait()
                self.connected = True

            async def close(self):
                self.closed = True
                self.connected = False

            async def receive_events(self):
                await asyncio.Event().wait()
                if False:
                    yield None

        def factory(*_):
            client = _BlockingFirstClient(block_connect=not clients)
            clients.append(client)
            return client

        provider = GoogleLiveProvider(conn, client_factory=factory)
        first_open = asyncio.create_task(provider._open_live_session())
        await first_connect_started.wait()
        second_open = asyncio.create_task(provider._open_live_session())
        await asyncio.sleep(0)

        self.assertEqual(len(clients), 1)

        release_first_connect.set()
        await asyncio.gather(first_open, second_open)
        await provider.close()

        self.assertEqual(len(clients), 2)
        self.assertTrue(clients[0].closed)


class ReconnectBufferCapacityTest(unittest.TestCase):
    def test_capacity_is_derived_from_budget_and_frame_size(self):
        conn = _Conn()
        provider = GoogleLiveProvider(conn, client_factory=lambda *_: _AlwaysFailClient())
        self.assertEqual(
            provider._pending_reconnect_audio.maxlen, 2000 // 60
        )

    def test_capacity_floors_at_one_for_invalid_config(self):
        conn = _Conn()
        conn.config["google_live"]["reconnect_buffer_ms"] = 0
        provider = GoogleLiveProvider(conn, client_factory=lambda *_: _AlwaysFailClient())
        self.assertEqual(provider._pending_reconnect_audio.maxlen, 1)

class SessionResumptionStateTest(unittest.TestCase):
    def test_resumption_update_is_saved_and_reused_on_next_connect(self):
        conn = _Conn()
        provider = GoogleLiveProvider(conn, client_factory=lambda *_: _AlwaysFailClient())

        provider._handle_session_resumption_update(
            {
                "type": "session_resumption_update",
                "handle": "resume-handle-1",
                "resumable": True,
            }
        )
        live_config = provider._get_live_config_with_functions()

        self.assertEqual(conn.google_live_session_resumption_handle, "resume-handle-1")
        self.assertEqual(live_config["session_resumption_handle"], "resume-handle-1")
        self.assertTrue(live_config["session_resumption_enabled"])

    def test_non_resumable_update_does_not_replace_saved_handle(self):
        conn = _Conn()
        conn.google_live_session_resumption_handle = "old-handle"
        provider = GoogleLiveProvider(conn, client_factory=lambda *_: _AlwaysFailClient())

        provider._handle_session_resumption_update(
            {
                "type": "session_resumption_update",
                "handle": "new-but-not-resumable",
                "resumable": False,
            }
        )

        self.assertEqual(conn.google_live_session_resumption_handle, "old-handle")


class RecvTimerResetLogTest(unittest.TestCase):
    """P2.5 — recv_timer_reset log fires for audio-bearing messages only."""

    def setUp(self):
        self.logger = _Logger()
        self.client = GoogleLiveClient({}, self.logger)

    def test_audio_chunk_message_triggers_recv_timer_reset_log(self):
        message = SimpleNamespace(
            server_content=SimpleNamespace(
                model_turn=SimpleNamespace(
                    parts=[
                        SimpleNamespace(
                            inline_data=SimpleNamespace(
                                data=b"opus-bytes", mime_type="audio/pcm"
                            )
                        )
                    ]
                ),
                interrupted=None,
                input_transcription=None,
                output_transcription=None,
                turn_complete=False,
            )
        )
        self.client._log_recv_timer_reset(message)
        self.assertTrue(
            self.logger.has_message("recv_timer_reset on chunk", level="debug")
        )

    def test_transcript_only_message_does_not_log_recv_timer_reset(self):
        message = SimpleNamespace(
            server_content=SimpleNamespace(
                model_turn=None,
                input_transcription=SimpleNamespace(text="hello"),
                output_transcription=None,
                interrupted=None,
                turn_complete=False,
            )
        )
        self.client._log_recv_timer_reset(message)
        self.assertFalse(self.logger.has_message("recv_timer_reset"))

    def test_sentinel_false_or_none_does_not_log(self):
        self.client._log_recv_timer_reset(None)
        self.client._log_recv_timer_reset(False)
        self.assertFalse(self.logger.has_message("recv_timer_reset"))


class ClassifyErrorRoutingTest(unittest.IsolatedAsyncioTestCase):
    """P2.7 — _try_reconnect emits classify_error log and routes correctly."""

    def _make_provider(self, conn=None):
        conn = conn or _Conn()
        provider = GoogleLiveProvider(
            conn,
            client_factory=lambda *_: _AlwaysFailClient(),
        )
        return conn, provider

    async def test_auth_error_logs_no_retry_and_returns_false(self):
        conn, provider = self._make_provider()
        retried = await provider._try_reconnect(
            RuntimeError("API key invalid permission denied")
        )
        self.assertFalse(retried)
        self.assertTrue(
            conn.logger.has_message("classify_error kind=auth retry=no", level="info")
        )

    async def test_quota_error_logs_no_retry_and_returns_false(self):
        conn, provider = self._make_provider()
        retried = await provider._try_reconnect(RuntimeError("429 quota exceeded"))
        self.assertFalse(retried)
        self.assertTrue(
            conn.logger.has_message("classify_error kind=quota retry=no", level="info")
        )

    async def test_invalid_config_error_logs_no_retry_and_returns_false(self):
        conn, provider = self._make_provider()
        retried = await provider._try_reconnect(RuntimeError("invalid model config"))
        self.assertFalse(retried)
        self.assertTrue(
            conn.logger.has_message(
                "classify_error kind=invalid_config retry=no", level="info"
            )
        )

    async def test_network_error_logs_retry_yes(self):
        conn = _Conn()
        # Force one retry, no actual connect (factory raises RuntimeError "network")
        provider = GoogleLiveProvider(
            conn,
            client_factory=lambda *_: _AlwaysFailNetworkClient(),
        )
        retried = await provider._try_reconnect(
            RuntimeError("connection reset by peer")
        )
        # All retry attempts ultimately fail (factory always raises) → returns False,
        # but classify_error MUST have logged retry=yes at least once.
        self.assertFalse(retried)
        self.assertTrue(
            conn.logger.has_message(
                "classify_error kind=network retry=yes", level="info"
            )
        )


class _AlwaysFailNetworkClient:
    """Mimic a network-class failure during connect for every retry attempt."""

    def __init__(self, *_, **__):
        self.connected = False

    async def connect(self):
        raise RuntimeError("connection refused mid-stream")

    async def close(self):
        return None

    async def send_audio(self, audio_bytes):
        return None

    async def receive_events(self):
        if False:  # pragma: no cover
            yield None


class MidStreamBlipReconnectTest(unittest.IsolatedAsyncioTestCase):
    """P2.8 — websocket mid-stream close exception is classified as network
    and the provider attempts reconnect (not immediate fallback)."""

    async def test_mid_stream_blip_routes_to_retry_path(self):
        conn = _Conn()
        provider = GoogleLiveProvider(
            conn,
            client_factory=lambda *_: _AlwaysFailNetworkClient(),
        )
        # Simulate a websocket close mid-stream: provider should classify as
        # network and call _try_reconnect, which then iterates retries until
        # the budget is exhausted before falling back.
        websocket_blip = RuntimeError("websocket connection closed abnormally")
        retried = await provider._try_reconnect(websocket_blip)
        self.assertFalse(retried)  # all attempts ultimately fail in this mock
        self.assertEqual(provider._reconnect_attempts, 3)  # max_retries from _Conn
        self.assertTrue(
            conn.logger.has_message(
                "classify_error kind=network retry=yes", level="info"
            )
        )

    async def test_mid_stream_audio_replays_from_deque_on_successful_reconnect(self):
        conn = _Conn()

        class _DummyBridge:
            def __init__(self):
                self.forwarded = []

            def decode_input_audio(self, packet):
                return packet  # passthrough

            async def forward_decoded_input_audio(self, decoded):
                self.forwarded.append(decoded)

            async def forward_input_audio(self, raw):
                self.forwarded.append(raw)

            async def close(self):
                pass

        bridge = _DummyBridge()
        provider = GoogleLiveProvider(
            conn,
            client_factory=lambda *_: _AlwaysFailNetworkClient(),
        )
        provider._bridge = bridge

        # Pretend three frames arrived during the blip
        provider._pending_reconnect_audio.append(b"frame-1")
        provider._pending_reconnect_audio.append(b"frame-2")
        provider._pending_reconnect_audio.append(b"frame-3")

        await provider._forward_pending_reconnect_audio()

        self.assertEqual(bridge.forwarded, [b"frame-1", b"frame-2", b"frame-3"])
        self.assertEqual(len(provider._pending_reconnect_audio), 0)
        self.assertTrue(
            conn.logger.has_message(
                "replayed_buffered_audio frames=3 bytes=21", level="info"
            )
        )

    async def test_reconnect_replays_only_current_turn_buffered_audio(self):
        conn = _Conn()

        class _DummyBridge:
            def __init__(self):
                self.forwarded = []

            def decode_input_audio(self, packet):
                return packet

            async def forward_decoded_input_audio(self, decoded):
                self.forwarded.append(decoded)

            async def forward_input_audio(self, raw):
                self.forwarded.append(raw)

        bridge = _DummyBridge()
        provider = GoogleLiveProvider(conn, client_factory=lambda *_: _AlwaysFailNetworkClient())
        provider._bridge = bridge
        provider._response_generation = 7
        provider._pending_reconnect_audio.append((6, b"old-turn"))
        provider._pending_reconnect_audio.append((7, b"current-turn"))

        await provider._forward_pending_reconnect_audio()

        self.assertEqual(bridge.forwarded, [b"current-turn"])
        self.assertTrue(
            conn.logger.has_message(
                "reconnect_replay_skipped reason=stale_turn", level="info"
            )
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
