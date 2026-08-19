import asyncio
import json
import time
import unittest
from collections import deque
from contextvars import ContextVar
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.activity_lease import (
    ActivityLeaseCoordinator,
    ActivityOperation,
    ExclusiveDisposition,
)
from plugins_func.register import Action
from plugins_func.functions import start_lesson as start_lesson_module
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
        self._lesson_asset_last_audio_at = 0.0
        self._lesson_start_handoff_generation = 0
        self._lesson_start_handoff_holder_serial = 0
        self._lesson_start_handoff_holders = set()
        self._lesson_start_handoff_context = ContextVar(
            f"provider_edge_handoff_{id(self):x}", default=None
        )
        self.handoff_releases = []
        self.voice_consent_client = _Consent(True)
        self.voice_provider = None
        self.clear_queue_calls = 0
        self.clear_speak_calls = 0
        try:
            self.activity_leases = ActivityLeaseCoordinator(asyncio.get_running_loop())
        except RuntimeError:
            self.activity_leases = None

    def clear_queues(self):
        self.clear_queue_calls += 1

    def clearSpeakStatus(self):
        self.clear_speak_calls += 1
        self.client_is_speaking = False

    def begin_lesson_start_handoff(self, *, reason):
        if not self._lesson_start_handoff_holders:
            self._lesson_start_handoff_generation += 1
        self._lesson_start_handoff_holder_serial += 1
        lease = (
            self._lesson_start_handoff_generation,
            self._lesson_start_handoff_holder_serial,
        )
        self._lesson_start_handoff_holders.add(lease)
        self._lesson_start_handoff_context.set(lease)
        return lease

    def lesson_start_handoff_token(self):
        lease = self._lesson_start_handoff_context.get()
        return lease if lease in self._lesson_start_handoff_holders else None

    def lesson_start_handoff_active(self):
        return bool(self._lesson_start_handoff_holders)

    async def release_lesson_start_handoff(
        self, token, *, outcome, restore_conversation
    ):
        if token not in self._lesson_start_handoff_holders:
            return False
        self._lesson_start_handoff_holders.remove(token)
        if self._lesson_start_handoff_context.get() == token:
            self._lesson_start_handoff_context.set(None)
        self.handoff_releases.append((token, outcome, restore_conversation))
        return True


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


class _ASR:
    def __init__(self, text):
        self.text = text
        self.calls = []

    async def speech_to_text_wrapper(self, opus_data, session_id, audio_format="opus"):
        self.calls.append((list(opus_data), session_id, audio_format))
        return self.text, None


class _SequenceASR:
    def __init__(self, texts):
        self.texts = iter(texts)
        self.calls = []

    async def speech_to_text_wrapper(self, opus_data, session_id, audio_format="opus"):
        self.calls.append((list(opus_data), session_id, audio_format))
        return next(self.texts), None

class _FailingASR:
    def __init__(self, message):
        self.message = message
        self.calls = []

    async def speech_to_text_wrapper(self, opus_data, session_id, audio_format="opus"):
        self.calls.append((list(opus_data), session_id, audio_format))
        raise RuntimeError(self.message)

class _EmptyAuthFailingASR:
    def __init__(self, message):
        self.last_error = message
        self.calls = []

    async def speech_to_text_wrapper(self, opus_data, session_id, audio_format="opus"):
        self.calls.append((list(opus_data), session_id, audio_format))
        return "", None

class GoogleLiveProviderEdgeTest(unittest.IsolatedAsyncioTestCase):
    async def test_activity_lease_covers_live_open_connect_task(self):
        conn = _Conn()
        connect_entered = asyncio.Event()
        connect_release = asyncio.Event()

        class BlockingClient(_Client):
            async def connect(self):
                connect_entered.set()
                await connect_release.wait()
                await super().connect()

        provider = GoogleLiveProvider(conn, client_factory=lambda *_args: BlockingClient())
        self.provider = provider
        with patch.object(google_live_module, "GoogleLiveAudioBridge", lambda *_a, **_k: _Bridge()):
            task = asyncio.create_task(provider._open_live_session())
            await asyncio.wait_for(connect_entered.wait(), timeout=1)
            self.assertTrue(conn.activity_leases.has_voice_leases())
            self.assertIsNone(
                conn.activity_leases.try_acquire_eviction(
                    ActivityOperation.LESSON_CACHE_EVICT,
                    busy_probe=lambda: False,
                )
            )
            connect_release.set()
            await task

        self.assertFalse(conn.activity_leases.has_voice_leases())

    async def test_activity_lease_covers_prewarm_background_task(self):
        conn = _Conn()
        provider = self.make_provider(conn)
        entered = asyncio.Event()
        release = asyncio.Event()

        async def open_live(*, preserve_live_prewarm=False):
            self.assertTrue(preserve_live_prewarm)
            entered.set()
            await release.wait()
            return True

        provider._ensure_live_open_for_audio = open_live
        provider._schedule_live_prewarm("connect")
        await asyncio.wait_for(entered.wait(), timeout=1)

        self.assertTrue(conn.activity_leases.has_voice_leases())
        release.set()
        await provider._live_prewarm_task
        self.assertFalse(conn.activity_leases.has_voice_leases())

    async def test_activity_lease_same_task_reconnect_nests_real_live_open(self):
        conn = _Conn()
        connect_entered = asyncio.Event()
        connect_release = asyncio.Event()

        class BlockingClient(_Client):
            async def connect(self):
                connect_entered.set()
                await connect_release.wait()
                await super().connect()

        provider = GoogleLiveProvider(conn, client_factory=lambda *_args: BlockingClient())
        self.provider = provider
        provider._get_reconnect_config = lambda: {
            "enabled": True,
            "max_retries": 1,
            "backoff_ms": 0,
            "backoff_multiplier": 1,
        }
        provider._classify_error = lambda _exc: "transport"
        provider._record_reconnect_attempt = AsyncMock()
        provider._close_live_resources = AsyncMock()
        with patch.object(google_live_module, "GoogleLiveAudioBridge", lambda *_a, **_k: _Bridge()):
            reconnect = asyncio.create_task(provider._try_reconnect(RuntimeError("lost")))
            await asyncio.wait_for(connect_entered.wait(), timeout=1)
            snapshot = conn.activity_leases.diagnostic_snapshot()
            self.assertGreaterEqual(snapshot["voiceLeaseCount"], 2)
            self.assertEqual(len(snapshot["voiceOwners"]), 1)
            self.assertIsNone(
                conn.activity_leases.try_acquire_eviction(
                    ActivityOperation.LESSON_CACHE_EVICT,
                    busy_probe=lambda: False,
                )
            )
            connect_release.set()
            self.assertTrue(await reconnect)

        self.assertFalse(conn.activity_leases.has_voice_leases())

    async def test_activity_lease_covers_user_text_and_wake_greeting_send(self):
        conn = _Conn()
        send_entered = asyncio.Event()
        send_release = asyncio.Event()

        class BlockingClient(_Client):
            async def send_text(self, text):
                send_entered.set()
                await send_release.wait()
                await super().send_text(text)

        provider = self.make_provider(conn)
        provider._client = BlockingClient()
        provider._bridge = _Bridge()

        text_task = asyncio.create_task(
            provider.handle_text_message('{"type":"text","text":"hello"}')
        )
        await asyncio.wait_for(send_entered.wait(), timeout=1)
        self.assertTrue(conn.activity_leases.has_voice_leases())
        send_release.set()
        await text_task
        self.assertFalse(conn.activity_leases.has_voice_leases())

        send_entered.clear()
        send_release.clear()
        greeting_task = asyncio.create_task(provider._send_wake_greeting("test"))
        await asyncio.wait_for(send_entered.wait(), timeout=1)
        self.assertTrue(conn.activity_leases.has_voice_leases())
        send_release.set()
        await greeting_task
        self.assertFalse(conn.activity_leases.has_voice_leases())

    async def test_activity_lease_eviction_first_drops_google_open_without_client_creation(self):
        conn = _Conn()
        created = []
        provider = GoogleLiveProvider(
            conn,
            client_factory=lambda *_args: created.append(_Client()) or created[-1],
        )
        self.provider = provider
        provider._admit_live_open = AsyncMock()
        lease = conn.activity_leases.try_acquire_eviction(
            ActivityOperation.LESSON_CACHE_EVICT,
            busy_probe=lambda: False,
        )

        opened = await provider._open_live_for_audio()

        self.assertFalse(opened)
        self.assertEqual(created, [])
        provider._admit_live_open.assert_not_awaited()
        lease.complete_exclusive(ExclusiveDisposition.DEFINITIVE)

    async def test_activity_lease_covers_reconnect_hard_reconnect_and_silent_reopen(self):
        conn = _Conn()
        provider = self.make_provider(conn)
        provider._client = _Client()
        provider._bridge = _Bridge()
        entered = asyncio.Event()
        release = asyncio.Event()

        async def record_reconnect():
            entered.set()
            await release.wait()

        provider._record_reconnect_attempt = record_reconnect
        provider._get_reconnect_config = lambda: {
            "enabled": True,
            "max_retries": 1,
            "backoff_ms": 0,
            "backoff_multiplier": 1,
        }
        provider._classify_error = lambda _exc: "transport"
        provider._close_live_resources = AsyncMock()
        provider._open_live_session = AsyncMock(return_value=True)
        provider._forward_pending_reconnect_audio = AsyncMock()

        reconnect = asyncio.create_task(provider._try_reconnect(RuntimeError("lost")))
        await asyncio.wait_for(entered.wait(), timeout=1)
        self.assertTrue(conn.activity_leases.has_voice_leases())
        release.set()
        await reconnect
        self.assertFalse(conn.activity_leases.has_voice_leases())

        entered.clear()
        release.clear()
        provider._closing = False
        provider._fallback_provider = None
        provider._reconnecting = False
        provider._close_live_resources = record_reconnect
        provider._open_live_session = AsyncMock(return_value=True)
        provider._pending_reconnect_audio.append((0, b"stale-success"))
        hard = asyncio.create_task(provider._hard_reconnect_after_interrupt("test"))
        await asyncio.wait_for(entered.wait(), timeout=1)
        self.assertTrue(conn.activity_leases.has_voice_leases())
        release.set()
        self.assertTrue(await hard)
        self.assertEqual(list(provider._pending_reconnect_audio), [])
        self.assertFalse(conn.activity_leases.has_voice_leases())

        entered.clear()
        release.clear()
        provider._client = _Client()
        provider._consecutive_waiting_model_timeouts = provider._SILENT_LIVE_REOPEN_TIMEOUTS
        provider._last_silent_live_reopen_at = 0.0
        provider._record_reconnect_attempt = record_reconnect
        provider._close_live_resources = AsyncMock()
        provider._open_live_session_locked = AsyncMock()
        provider._forward_pending_reconnect_audio = AsyncMock()
        silent = asyncio.create_task(provider._reopen_silent_live_session_after_timeouts(1.0))
        await asyncio.wait_for(entered.wait(), timeout=1)
        self.assertTrue(conn.activity_leases.has_voice_leases())
        release.set()
        self.assertTrue(await silent)
        self.assertFalse(conn.activity_leases.has_voice_leases())

    async def test_activity_lease_eviction_first_hard_reconnect_discards_replay_buffer(self):
        conn = _Conn()
        provider = self.make_provider(conn)
        provider._pending_reconnect_audio.append((0, b"stale"))
        lease = conn.activity_leases.try_acquire_eviction(
            ActivityOperation.LESSON_CACHE_EVICT,
            busy_probe=lambda: False,
        )

        reconnected = await provider._hard_reconnect_after_interrupt("test")

        self.assertFalse(reconnected)
        self.assertEqual(list(provider._pending_reconnect_audio), [])
        self.assertFalse(provider._reconnecting)
        lease.complete_exclusive(ExclusiveDisposition.DEFINITIVE)

    async def test_activity_lease_runtime_failure_refusal_does_not_mutate_live_transport(self):
        conn = _Conn()
        provider = self.make_provider(conn)
        provider._stop_live_output_for_transport_change = AsyncMock()
        provider._close_live_resources = AsyncMock()
        lease = conn.activity_leases.try_acquire_eviction(
            ActivityOperation.LESSON_CACHE_EVICT,
            busy_probe=lambda: False,
        )

        await provider._handle_runtime_failure(RuntimeError("lost"))

        provider._stop_live_output_for_transport_change.assert_not_awaited()
        provider._close_live_resources.assert_not_awaited()
        lease.complete_exclusive(ExclusiveDisposition.DEFINITIVE)

    async def test_activity_lease_refused_listen_stop_clears_local_google_input_without_finalize(self):
        conn = _Conn()
        provider = self.make_provider(conn)
        provider._user_stream_started_at = time.monotonic()
        provider._user_stream_response_id = 7
        provider._user_stream_last_speech_at = time.monotonic()
        provider._user_stream_frames = 3
        provider._pending_reconnect_audio.append((7, b"reconnect"))
        provider._pending_interrupt_audio.append(b"interrupt")
        provider._start_lesson_asr_fallback_audio.append(b"fallback")
        provider._user_audio_window_task = asyncio.create_task(asyncio.Event().wait())
        provider._finalize_user_audio_input = AsyncMock()

        provider.discard_refused_voice_input()
        await asyncio.sleep(0)

        self.assertIsNone(provider._user_stream_started_at)
        self.assertEqual(provider._user_stream_frames, 0)
        self.assertEqual(list(provider._pending_reconnect_audio), [])
        self.assertEqual(list(provider._pending_interrupt_audio), [])
        self.assertEqual(list(provider._start_lesson_asr_fallback_audio), [])
        self.assertIsNone(provider._user_audio_window_task)
        provider._finalize_user_audio_input.assert_not_awaited()

    async def test_activity_lease_hard_reconnect_failure_discards_replay_buffer(self):
        conn = _Conn()
        provider = self.make_provider(conn)
        provider._pending_reconnect_audio.append((0, b"stale-failure"))
        provider._close_live_resources = AsyncMock()
        provider._open_live_session = AsyncMock(side_effect=RuntimeError("lost"))
        provider._handle_runtime_failure = AsyncMock()

        reconnected = await provider._hard_reconnect_after_interrupt("test")

        self.assertFalse(reconnected)
        self.assertEqual(list(provider._pending_reconnect_audio), [])
        provider._handle_runtime_failure.assert_awaited_once()

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

    async def test_prepare_for_sample_lesson_keeps_active_receive_session_and_resets_turn_state(self):
        conn = _Conn()
        old_client = _Client()
        new_clients = []

        def client_factory(*_args, **_kwargs):
            client = _Client()
            new_clients.append(client)
            return client

        provider = GoogleLiveProvider(conn, client_factory=client_factory)
        self.provider = provider
        provider._client = old_client
        provider._bridge = _Bridge()
        provider._receive_task = asyncio.create_task(asyncio.sleep(60))
        provider._waiting_model_since = time.monotonic() - 10
        conn.google_live_session_resumption_handle = "old-handle"
        conn.google_live_turn_started_at = time.monotonic() - 30
        conn.google_live_audio_out_started_at = time.monotonic() - 20

        with patch.object(google_live_module, "GoogleLiveAudioBridge", lambda *_a, **_k: _Bridge()):
            await provider.prepare_for_sample_lesson()

        self.assertEqual(old_client.closed, 0)
        self.assertEqual(len(new_clients), 0)
        self.assertIs(provider._client, old_client)
        self.assertEqual(conn.google_live_session_resumption_handle, "old-handle")
        self.assertIsNone(conn.google_live_turn_started_at)
        self.assertIsNone(conn.google_live_audio_out_started_at)
        self.assertIsNone(provider._waiting_model_since)

    async def test_prepare_for_sample_lesson_reopens_disconnected_idle_client(self):
        conn = _Conn()
        old_client = _Client()
        old_client.connected = False
        new_clients = []

        def client_factory(*_args, **_kwargs):
            client = _Client()
            new_clients.append(client)
            return client

        provider = GoogleLiveProvider(conn, client_factory=client_factory)
        self.provider = provider
        provider._client = old_client
        provider._bridge = _Bridge()
        provider._receive_task = None

        with patch.object(google_live_module, "GoogleLiveAudioBridge", lambda *_a, **_k: _Bridge()):
            await provider.prepare_for_sample_lesson()

        self.assertEqual(old_client.closed, 1)
        self.assertEqual(len(new_clients), 1)
        self.assertIs(provider._client, new_clients[0])

    async def test_send_live_text_ack_stamps_turn_latency_baseline(self):
        conn = _Conn()
        provider = self.make_provider(conn)
        provider._client = _Client()
        provider._bridge = _Bridge()
        conn.google_live_turn_started_at = None

        sent = await provider._send_live_text_ack(
            "Chào con!",
            log_label="lesson_step_prompt",
            allow_lesson_output=True,
        )

        self.assertTrue(sent)
        self.assertIsNotNone(conn.google_live_turn_started_at)
        self.assertEqual(len(provider._client.text), 1)

    async def test_new_audio_turn_replaces_stale_turn_latency_baseline(self):
        conn = _Conn()
        conn.google_live_turn_started_at = time.monotonic() - 30
        provider = self.make_provider(conn)
        provider._client = _Client()
        provider._bridge = _Bridge()

        started = time.monotonic()
        self.assertTrue(await provider.handle_audio_bytes(b"hello"))

        self.assertGreaterEqual(conn.google_live_turn_started_at, started)
        self.assertEqual(provider._bridge.forwarded, [b"pcm:hello"])

    async def test_listen_start_during_model_output_does_not_cut_response(self):
        conn = _Conn()
        provider = self.make_provider(conn)
        provider._client = _Client()
        provider._bridge = _Bridge()
        conn.google_live_audio_out_started_at = time.monotonic()
        previous_response_id = provider._response_generation

        await provider._open_user_audio_window("listen_start")

        self.assertEqual(provider._response_generation, previous_response_id)
        self.assertEqual(provider._client.interrupt_calls, 0)
        self.assertEqual(provider._bridge.stop_calls, 0)
        self.assertGreater(provider._user_audio_allowed_until, time.monotonic())

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
        provider._has_active_output = lambda: False
        provider._has_music_session = lambda: True
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

    async def test_dormant_audio_live_open_timeout_does_not_wedge_listening(self):
        conn = _Conn()
        conn.session_mode = SessionMode.DORMANT
        conn.config["google_live"]["live_open_timeout_sec"] = 0.01
        provider = self.make_provider(conn)
        closed = []
        opened = []

        async def _ensure_func_handler():
            return None

        async def _open_live_session():
            await asyncio.sleep(10)
            opened.append(True)

        async def _close_live_resources():
            closed.append(True)

        provider._ensure_func_handler = _ensure_func_handler
        provider._open_live_session = _open_live_session
        provider._close_live_resources = _close_live_resources

        handled = await asyncio.wait_for(provider.handle_audio_bytes(b"first-audio"), timeout=0.2)

        self.assertTrue(handled)
        self.assertEqual(opened, [])
        self.assertEqual(closed, [True])
        self.assertIsNone(provider._bridge)
        self.assertTrue(
            any(
                args and args[0] == "Google Live live_open_timeout timeout_sec={}"
                for level, args, _kwargs in conn.logger.messages
                if level == "warning"
            )
        )

    async def test_dormant_audio_slow_tool_bootstrap_does_not_block_live_open(self):
        conn = _Conn()
        conn.session_mode = SessionMode.DORMANT
        conn.config["google_live"]["live_open_timeout_sec"] = 0.2
        provider = self.make_provider(conn)
        bridge = _Bridge()
        bridge.rms = 1200
        opened = []
        bootstrap_started = asyncio.Event()

        async def _slow_func_handler():
            bootstrap_started.set()
            await asyncio.sleep(10)

        async def _open_live_session():
            opened.append(True)
            provider._client = _Client()
            provider._bridge = bridge
            conn.session_mode = SessionMode.CONVERSATION

        provider._ensure_func_handler = _slow_func_handler
        provider._open_live_session = _open_live_session

        handled = await asyncio.wait_for(provider.handle_audio_bytes(b"first-audio"), timeout=0.2)

        self.assertTrue(handled)
        self.assertEqual(opened, [True])
        self.assertEqual(bridge.forwarded, [b"pcm:first-audio"])
        self.assertTrue(bootstrap_started.is_set())

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

    async def test_lesson_prompt_guard_waits_for_slow_live_output(self):
        conn = _Conn()
        conn.google_live_lesson_prompt_output_allowed = True
        provider = self.make_provider(conn)
        elapsed = 0.0
        original_sleep = google_live_module.asyncio.sleep

        async def _slow_live_output(delay):
            nonlocal elapsed
            elapsed += delay
            if elapsed >= 16.0:
                conn.google_live_lesson_prompt_output_allowed = False

        google_live_module.asyncio.sleep = _slow_live_output
        try:
            idle = await provider._wait_for_lesson_prompt_output_idle(
                {"lesson_prompt_output_poll_sec": 1.0}
            )
        finally:
            google_live_module.asyncio.sleep = original_sleep

        self.assertFalse(idle)
        self.assertLessEqual(elapsed, 9.0)

    async def test_lesson_prompt_guard_infers_idle_when_live_omits_audio_end(self):
        conn = _Conn()
        conn.session_mode = SessionMode.LESSON
        conn.google_live_lesson_prompt_output_allowed = True
        provider = self.make_provider(conn)
        now = 1000.0
        original_sleep = google_live_module.asyncio.sleep
        original_monotonic = google_live_module.time.monotonic

        async def _advance_time(delay):
            nonlocal now
            now += delay

        google_live_module.asyncio.sleep = _advance_time
        google_live_module.time.monotonic = lambda: now
        try:
            await provider._handle_live_event(
                {
                    "type": "transcript",
                    "source": "model",
                    "text": "Chào con! Nhìn hình, nghe TeeBot, rồi nói khi mình mời nhé.",
                }
            )
            idle = await provider._wait_for_lesson_prompt_output_idle(
                {
                    "lesson_prompt_output_poll_sec": 0.1,
                    "lesson_prompt_output_guard_timeout_sec": 1.0,
                    "lesson_prompt_inferred_idle_sec": 0.3,
                }
            )
        finally:
            google_live_module.asyncio.sleep = original_sleep
            google_live_module.time.monotonic = original_monotonic

        self.assertTrue(idle)
        self.assertFalse(conn.google_live_lesson_prompt_output_allowed)
        self.assertLess(now, 1001.0)

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
        old_client = provider._client
        old_bridge = provider._bridge

        routed = await provider._route_lesson_child_response("barn barn barn")

        self.assertTrue(routed)
        self.assertEqual(
            handled,
            [("barn barn barn", {"source": "voice_transcript"})],
        )
        self.assertEqual(old_client.interrupt_calls, 1)
        self.assertFalse(conn.client_abort)
        self.assertEqual(old_bridge.stop_calls, 1)

    async def test_lesson_child_transcript_reopens_live_before_runtime_prompt(self):
        conn = _Conn()
        conn.session_mode = SessionMode.LESSON
        events = []

        async def _on_child_response(text, **kwargs):
            events.append(("runtime", text, kwargs))
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

        async def _reconnect(reason, **kwargs):
            events.append(("reconnect", reason, kwargs))

        provider._hard_reconnect_after_interrupt = _reconnect

        routed = await provider._route_lesson_child_response("bond")

        self.assertTrue(routed)
        self.assertEqual(events[0][0], "reconnect")
        self.assertEqual(events[0][1], "lesson_child_response_prompt")
        self.assertEqual(events[0][2], {"restore_session_resumption": False})
        self.assertEqual(events[1], ("runtime", "bond", {"source": "voice_transcript"}))

    async def test_lesson_child_transcript_routes_after_child_audio_before_runtime_window_flag(self):
        conn = _Conn()
        conn.session_mode = SessionMode.LESSON
        handled = []

        async def _on_child_response(text, **kwargs):
            handled.append((text, kwargs, conn.lesson_runtime._child_response_window_open))
            return True

        conn.lesson_runtime = SimpleNamespace(
            state="RUNNING",
            _step_id="s4",
            _step_passive=False,
            _step_completed=False,
            _child_response_window_open=False,
            on_child_response=_on_child_response,
        )
        provider = self.make_provider(conn)
        provider._client = _Client()
        provider._bridge = _Bridge()
        provider._lesson_child_audio_pending_transcript = True
        provider._user_audio_allowed_until = 0.0
        old_bridge = provider._bridge

        routed = await provider._route_lesson_child_response("bỏ")

        self.assertTrue(routed)
        self.assertEqual(
            handled,
            [("bỏ", {"source": "voice_transcript"}, True)],
        )
        self.assertFalse(provider._lesson_child_audio_pending_transcript)
        self.assertEqual(old_bridge.stop_calls, 1)

    async def test_lesson_child_audio_waits_while_robot_speaks(self):
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
        self.assertEqual(provider._bridge.forwarded, [])
        self.assertTrue(
            any(
                "lesson_child_audio_deferred" in str(args)
                for _, args, _ in conn.logger.messages
            )
        )

    async def test_wake_transcript_ignored_when_wake_window_already_open(self):
        conn = _Conn()
        provider = self.make_provider(conn)
        provider._client = _Client()
        provider._bridge = _Bridge()
        provider._wake_audio_window_until = time.monotonic() + 10.0
        interrupts = []

        async def _interrupt(reason):
            interrupts.append(reason)

        provider._begin_user_interrupt = _interrupt
        handled = await provider._on_user_transcript("high speed")
        self.assertTrue(handled)
        self.assertEqual(interrupts, [])
        self.assertTrue(
            any("wake_transcript_ignored_duplicate" in str(args)
                for _, args, _ in conn.logger.messages)
        )

    async def test_wake_word_schedules_live_prewarm_when_live_closed(self):
        conn = _Conn()
        provider = self.make_provider(conn)
        provider._client = None
        provider._bridge = None
        scheduled = []

        def _schedule(reason, delay_sec=0.0):
            scheduled.append((reason, delay_sec))

        provider._schedule_live_prewarm = _schedule
        handled = await provider.handle_text_message(
            '{"type":"listen","state":"detect","text":"Hi ESP"}'
        )
        self.assertTrue(handled)
        self.assertEqual(scheduled, [("wake_word", 0.0)])

    async def test_start_session_schedules_connect_prewarm_when_dormant(self):
        conn = _Conn()
        conn.session_mode = "DORMANT"
        provider = self.make_provider(conn)
        scheduled = []

        def _schedule(reason, delay_sec=0.0):
            scheduled.append((reason, float(delay_sec)))

        provider._schedule_live_prewarm = _schedule
        # Orchestrator present -> dormant init path
        conn.session_mode = type("M", (), {"value": "DORMANT"})()
        # Force has orchestrator
        provider._has_session_orchestrator = lambda: True
        await provider.start_session()
        self.assertEqual(len(scheduled), 1)
        self.assertEqual(scheduled[0][0], "connect")
        self.assertGreaterEqual(scheduled[0][1], 0.0)

    async def test_external_close_still_cancels_in_flight_live_prewarm(self):
        conn = _Conn()
        provider = self.make_provider(conn)
        started = asyncio.Event()

        async def _slow_open(*, preserve_live_prewarm=False):
            self.assertTrue(preserve_live_prewarm)
            started.set()
            await asyncio.sleep(30)

        provider._ensure_live_open_for_audio = _slow_open
        provider._schedule_live_prewarm("test")
        prewarm = provider._live_prewarm_task
        await asyncio.wait_for(started.wait(), timeout=1)

        await provider._close_live_resources()

        self.assertIsNone(provider._live_prewarm_task)
        self.assertTrue(prewarm.cancelled())

    async def test_foreground_budget_degrade_still_cancels_in_flight_prewarm(self):
        conn = _Conn()
        provider = self.make_provider(conn)
        started = asyncio.Event()

        async def _slow_open(*, preserve_live_prewarm=False):
            self.assertTrue(preserve_live_prewarm)
            started.set()
            await asyncio.sleep(30)

        provider._ensure_live_open_for_audio = _slow_open
        provider._schedule_live_prewarm("test")
        prewarm = provider._live_prewarm_task
        await asyncio.wait_for(started.wait(), timeout=1)

        await provider._activate_budget_degrade(
            AdmissionReason.DEVICE_DAILY_BUDGET_EXHAUSTED
        )

        self.assertIsNone(provider._live_prewarm_task)
        self.assertTrue(prewarm.cancelled())

    async def test_live_open_timeout_uses_external_cleanup_semantics(self):
        conn = _Conn()
        conn.config["google_live"]["live_open_timeout_sec"] = 0.01
        provider = self.make_provider(conn)
        cleanup_flags = []

        async def _slow_open(*, preserve_live_prewarm=False):
            await asyncio.sleep(30)

        async def _record_cleanup(*, preserve_live_prewarm=False):
            cleanup_flags.append(preserve_live_prewarm)

        provider._open_live_for_audio = _slow_open
        provider._close_live_resources = _record_cleanup

        self.assertFalse(await provider._ensure_live_open_for_audio())
        self.assertEqual(cleanup_flags, [False])

    async def test_completed_budget_degrade_allows_replacement_prewarm(self):
        conn = _Conn()
        conn.live_admission_gate = _Gate(
            SimpleNamespace(
                decision=AdmissionDecision.DEGRADE_TTS_ONLY,
                reason=AdmissionReason.DEVICE_DAILY_BUDGET_EXHAUSTED,
            )
        )
        provider = self.make_provider(conn)

        provider._schedule_live_prewarm("first")
        first = provider._live_prewarm_task
        self.assertFalse(await asyncio.wait_for(first, timeout=1))

        provider._schedule_live_prewarm("second")
        second = provider._live_prewarm_task
        self.assertIsNot(second, first)
        self.assertFalse(await asyncio.wait_for(second, timeout=1))
        self.assertFalse(second.cancelled())

    async def test_owned_prewarm_cleanup_preserves_only_live_prewarm_task(self):
        conn = _Conn()
        conn.live_admission_gate = _Gate(
            SimpleNamespace(
                decision=AdmissionDecision.DEGRADE_TTS_ONLY,
                reason=AdmissionReason.DEVICE_DAILY_BUDGET_EXHAUSTED,
            )
        )
        provider = self.make_provider(conn)
        greeting_started = asyncio.Event()

        async def _greeting():
            greeting_started.set()
            await asyncio.sleep(30)

        greeting = asyncio.create_task(_greeting())
        provider._wake_greeting_task = greeting
        await asyncio.wait_for(greeting_started.wait(), timeout=1)

        provider._schedule_live_prewarm("test")
        prewarm = provider._live_prewarm_task

        self.assertFalse(await asyncio.wait_for(prewarm, timeout=1))
        self.assertFalse(prewarm.cancelled())
        self.assertTrue(greeting.cancelled())
        self.assertIsNone(provider._wake_greeting_task)

    async def test_wake_word_detect_opens_listening_without_greeting(self):
        conn = _Conn()
        # Unit test: disable spoken wake greeting (production enables it).
        conn.config.setdefault("google_live", {})["wake_greeting_enabled"] = False
        provider = self.make_provider(conn)
        provider._client = _Client()
        provider._bridge = _Bridge()

        handled = await provider.handle_text_message(
            '{"type":"listen","state":"detect","text":"Hi ESP"}'
        )

        self.assertTrue(handled)
        remaining = provider._user_audio_allowed_until - time.monotonic()
        self.assertGreater(remaining, 10.0)
        sent = [json.loads(payload) for payload in conn.websocket.sent]
        self.assertEqual(sent[0]["type"], "stt")
        self.assertEqual(sent[0]["text"], "Hi ESP")
        self.assertEqual(sent[1]["type"], "tts")
        self.assertEqual(sent[1]["state"], "stop")
        self.assertEqual(provider._client.text, [])

    async def test_wake_word_detect_schedules_wake_greeting_when_enabled(self):
        conn = _Conn()
        conn.config.setdefault("google_live", {})["wake_greeting_enabled"] = True
        provider = self.make_provider(conn)
        provider._client = _Client()
        provider._bridge = _Bridge()
        scheduled = []

        def _schedule(reason="wake"):
            scheduled.append(reason)

        provider._schedule_wake_greeting = _schedule
        handled = await provider.handle_text_message(
            '{"type":"listen","state":"detect","text":"Hi ESP"}'
        )
        self.assertTrue(handled)
        self.assertEqual(scheduled, ["wake_detect"])

    async def test_send_wake_greeting_sends_configured_text_when_live_ready(self):
        conn = _Conn()
        conn.config.setdefault("google_live", {})["wake_greeting_enabled"] = True
        conn.config["google_live"]["wake_greeting_text"] = "Xin chào con."
        provider = self.make_provider(conn)
        client = _Client()
        bridge = _Bridge()
        provider._client = client
        provider._bridge = bridge

        ok = await provider._send_wake_greeting("unit_test")
        self.assertTrue(ok)
        self.assertEqual(bridge.allow_calls, 1)
        self.assertEqual(len(client.text), 1)
        self.assertIn("Xin chào con.", client.text[0])
        self.assertGreater(provider._wake_greeting_sent_until, time.monotonic())
        self.assertTrue(
            any("wake_greeting_sent" in str(args) for _, args, _ in conn.logger.messages)
        )

    async def test_close_live_resources_cancels_in_flight_wake_greeting_task(self):
        conn = _Conn()
        provider = self.make_provider(conn)
        provider._client = _Client()
        provider._bridge = _Bridge()
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def _slow_greeting(_reason):
            started.set()
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                cancelled.set()
                raise
            return False

        provider._send_wake_greeting = _slow_greeting
        provider._schedule_wake_greeting("unit_test")
        await asyncio.wait_for(started.wait(), timeout=1.0)
        task = provider._wake_greeting_task
        self.assertIsNotNone(task)
        self.assertFalse(task.done())

        await provider._close_live_resources()
        self.assertIsNone(provider._wake_greeting_task)
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(cancelled.is_set())

    async def test_user_transcript_suppressed_when_bridge_detects_model_echo(self):
        conn = _Conn()
        provider = self.make_provider(conn)
        provider._client = _Client()
        bridge = _Bridge()
        bridge.looks_like_model_echo = lambda _text: True
        provider._bridge = bridge
        lesson_calls = []
        interrupts = []

        async def _lesson(_text):
            lesson_calls.append(_text)
            return True

        async def _interrupt(reason):
            interrupts.append(reason)

        provider._dispatch_lesson_child_response = _lesson
        provider._begin_user_interrupt = _interrupt
        handled = await provider._on_user_transcript("Hôm nay chúng ta học màu đỏ")
        self.assertTrue(handled)
        self.assertEqual(lesson_calls, [])
        self.assertEqual(interrupts, [])
        self.assertTrue(
            any(
                "user_transcript_suppressed_as_model_echo" in str(args)
                for _, args, _ in conn.logger.messages
            )
        )

    async def test_ensure_live_ready_opens_when_client_missing(self):
        conn = _Conn()
        provider = self.make_provider(conn)
        provider._client = None
        provider._bridge = None
        opens = []

        async def _open(*, preserve_live_prewarm=False):
            self.assertFalse(preserve_live_prewarm)
            opens.append(True)
            provider._client = _Client()
            provider._bridge = _Bridge()
            return True

        provider._open_live_for_audio = _open
        ok = await provider.ensure_live_ready(reason="conversation")
        self.assertTrue(ok)
        self.assertEqual(opens, [True])
        self.assertTrue(
            any("ensure_live_ready" in str(args) for _, args, _ in conn.logger.messages)
        )

    async def test_wake_word_detect_opens_next_live_session_without_stale_lesson_resumption(self):
        conn = _Conn()
        conn.google_live_session_resumption_handle = "lesson-barn-handle"
        conn.live_resumption_store = _Store(handle="lesson-barn-handle")
        configs = []

        def client_factory(config, _logger):
            configs.append(dict(config))
            return _Client()

        provider = GoogleLiveProvider(conn, client_factory=client_factory)
        self.provider = provider

        handled = await provider.handle_text_message(
            '{"type":"listen","state":"detect","text":"Hi ESP"}'
        )

        self.assertTrue(handled)
        self.assertIsNone(conn.google_live_session_resumption_handle)

        with patch.object(google_live_module, "GoogleLiveAudioBridge", lambda *_a, **_k: _Bridge()):
            self.assertTrue(await provider._ensure_live_open_for_audio())

        self.assertEqual(len(configs), 1)
        self.assertNotIn("session_resumption_handle", configs[0])

    async def test_listen_start_after_wake_detect_does_not_reset_live_context_again(self):
        conn = _Conn()
        conn.google_live_session_resumption_handle = "fresh-wake-handle"
        conn.live_resumption_store = _Store(handle="fresh-wake-handle")
        provider = self.make_provider(conn)
        provider._client = _Client()
        provider._bridge = _Bridge()

        self.assertTrue(
            await provider.handle_text_message(
                '{"type":"listen","state":"detect","text":"Hi ESP"}'
            )
        )
        fresh_client = _Client()
        conn.google_live_session_resumption_handle = "fresh-wake-handle"
        provider._client = fresh_client
        provider._bridge = _Bridge()

        self.assertTrue(
            await provider.handle_text_message('{"type":"listen","state":"start"}')
        )

        self.assertEqual(conn.google_live_session_resumption_handle, "fresh-wake-handle")
        self.assertIs(provider._client, fresh_client)
        self.assertEqual(fresh_client.closed, 0)

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
        interrupts = []

        async def _interrupt(reason):
            interrupts.append(reason)

        provider._begin_user_interrupt = _interrupt

        handled = await provider._on_user_transcript("high speed")

        self.assertTrue(handled)
        self.assertEqual(lesson_calls, [])
        # No active robot/music output => bare wake must open listening without
        # cancelling a turn (cold-start hang fix).
        self.assertEqual(interrupts, [])
        self.assertGreater(provider._user_audio_allowed_until, time.monotonic())
        sent = [json.loads(payload) for payload in conn.websocket.sent]
        self.assertEqual(sent[0]["type"], "stt")
        self.assertEqual(sent[0]["text"], "high speed")
        self.assertEqual(sent[1]["type"], "tts")
        self.assertEqual(sent[1]["state"], "stop")

    async def test_wake_only_transcript_suppresses_tail_audio_response(self):
        conn = _Conn()
        conn.config["google_live"].update(
            {
                "input_min_capture_ms": 0,
                "input_speech_tail_ms": 0,
                "input_speech_rms_threshold": 500,
            }
        )
        provider = self.make_provider(conn)
        provider._client = _Client()
        provider._bridge = _Bridge()
        provider._bridge.rms = 2200

        self.assertTrue(await provider._on_user_transcript("high speed"))
        end_calls_after_wake = provider._client.end_calls
        self.assertTrue(await provider.handle_audio_bytes(b"tail"))

        self.assertEqual(provider._bridge.forwarded, [])
        self.assertEqual(provider._client.end_calls, end_calls_after_wake)
        self.assertIsNone(provider._waiting_model_since)
        self.assertNotEqual(
            provider._interaction.state,
            google_live_module.InteractionState.WAITING_MODEL,
        )

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

    async def test_listen_start_does_not_reset_open_user_stream(self):
        conn = _Conn()
        conn.session_mode = SessionMode.CONVERSATION
        conn.google_live_audio_out_started_at = time.monotonic()
        provider = self.make_provider(conn)
        provider._client = _Client()
        provider._bridge = _Bridge()
        provider._interaction.transition(google_live_module.InteractionState.USER_STREAMING)
        provider._user_stream_started_at = time.monotonic() - 10
        provider._user_stream_response_id = provider._response_generation
        provider._schedule_input_flush()
        generation = provider._input_flush_generation

        handled = await provider.handle_text_message('{"type":"listen","state":"start"}')

        self.assertTrue(handled)
        self.assertEqual(provider._response_generation, 0)
        self.assertEqual(provider._client.interrupt_calls, 0)
        self.assertEqual(provider._client.end_calls, 0)
        self.assertEqual(provider._input_flush_generation, generation)
        self.assertGreater(provider._user_audio_allowed_until, time.monotonic())

    async def test_listen_start_with_resumption_handle_preserves_open_user_stream(self):
        conn = _Conn()
        conn.session_mode = SessionMode.CONVERSATION
        conn.google_live_session_resumption_handle = "live-handle"
        provider = self.make_provider(conn)
        client = _Client()
        bridge = _Bridge()
        provider._client = client
        provider._bridge = bridge
        provider._interaction.transition(google_live_module.InteractionState.USER_STREAMING)
        provider._user_stream_started_at = time.monotonic() - 0.5
        provider._user_stream_response_id = provider._response_generation
        provider._schedule_input_flush()
        generation = provider._input_flush_generation

        handled = await provider.handle_text_message('{"type":"listen","state":"start"}')

        self.assertTrue(handled)
        self.assertIs(provider._client, client)
        self.assertIs(provider._bridge, bridge)
        self.assertEqual(client.closed, 0)
        self.assertEqual(bridge.closed, 0)
        self.assertEqual(conn.google_live_session_resumption_handle, "live-handle")
        self.assertEqual(provider._interaction.state, google_live_module.InteractionState.USER_STREAMING)
        self.assertEqual(provider._input_flush_generation, generation)
        self.assertGreater(provider._user_audio_allowed_until, time.monotonic())

    async def test_loud_retry_audio_reopens_waiting_model_turn_immediately(self):
        conn = _Conn()
        conn.config["google_live"].update(
            {
                "input_speech_rms_threshold": 500,
                "waiting_model_timeout_sec": 4.0,
            }
        )
        provider = self.make_provider(conn)
        provider._client = _Client()
        provider._bridge = _Bridge()
        provider._bridge.rms = 2200
        provider._interaction.transition(google_live_module.InteractionState.WAITING_MODEL)
        # Must exceed _WAITING_MODEL_RETRY_AUDIO_GRACE_SEC (2.8s) so residual noise
        # does not reopen mid-reply, but intentional loud retry still recovers.
        provider._waiting_model_since = time.monotonic() - 3.2
        provider._schedule_waiting_model_timeout_task()

        handled = await provider.handle_audio_bytes(b"bat-dau-bai-hoc")

        self.assertTrue(handled)
        self.assertEqual(provider._bridge.forwarded, [b"pcm:bat-dau-bai-hoc"])
        self.assertEqual(
            provider._interaction.state,
            google_live_module.InteractionState.USER_STREAMING,
        )
        self.assertIsNone(provider._waiting_model_since)
        self.assertIsNone(provider._waiting_model_timeout_task)

    async def test_live_silent_asr_fallback_dispatches_start_lesson(self):
        conn = _Conn()
        conn.func_handler = _FuncHandler()
        conn.asr = _ASR("bắt đầu bài học")
        conn.config["google_live"].update(
            {
                "input_min_capture_ms": 0,
                "input_speech_tail_ms": 0,
                "input_speech_rms_threshold": 500,
                "lesson_start_asr_fallback_delay_sec": 0,
                "waiting_model_timeout_sec": 4.0,
            }
        )
        provider = self.make_provider(conn)
        provider._client = _Client()
        provider._bridge = _Bridge()
        provider._bridge.rms = 1800
        provider._interaction.transition(google_live_module.InteractionState.WAITING_MODEL)
        provider._waiting_model_since = time.monotonic() - 3.2
        original_product_tool_names = google_live_module.product_tool_names
        google_live_module.product_tool_names = lambda _conn: ["start_lesson"]
        try:
            handled = await provider.handle_audio_bytes(b"start-lesson-opus")
            await asyncio.sleep(0.05)
        finally:
            google_live_module.product_tool_names = original_product_tool_names

        self.assertTrue(handled)
        self.assertEqual(conn.asr.calls, [([b"start-lesson-opus"], "session-1", "opus")])
        self.assertEqual(
            conn.func_handler.calls[-1][1],
            {"name": "start_lesson", "arguments": {}},
        )

    async def test_lesson_start_asr_fallback_keeps_quiet_frames_inside_forwarded_turn(self):
        conn = _Conn()
        conn.config["google_live"]["input_speech_rms_threshold"] = 500
        provider = self.make_provider(conn)
        provider._bridge = _Bridge()
        original_product_tool_names = google_live_module.product_tool_names
        google_live_module.product_tool_names = lambda _conn: ["start_lesson"]
        try:
            provider._bridge.rms = 1800
            provider._record_start_lesson_asr_fallback_audio(b"loud", b"pcm-loud")
            provider._bridge.rms = 120
            provider._record_start_lesson_asr_fallback_audio(b"quiet", b"pcm-quiet")
        finally:
            google_live_module.product_tool_names = original_product_tool_names

        self.assertEqual(
            list(provider._start_lesson_asr_fallback_audio),
            [b"loud", b"quiet"],
        )

    async def test_lesson_start_asr_fallback_detaches_each_turn_before_asr_delay(self):
        conn = _Conn()
        conn.config["google_live"]["lesson_start_asr_fallback_delay_sec"] = 60
        provider = self.make_provider(conn)
        provider._start_lesson_asr_fallback_audio.extend([b"turn-one-a", b"turn-one-b"])
        original_product_tool_names = google_live_module.product_tool_names
        google_live_module.product_tool_names = lambda _conn: ["start_lesson"]
        try:
            provider._schedule_start_lesson_asr_fallback_task()
            self.assertEqual(list(provider._start_lesson_asr_fallback_audio), [])
        finally:
            google_live_module.product_tool_names = original_product_tool_names
            provider._cancel_start_lesson_asr_fallback_task()

    async def test_lesson_start_asr_fallback_combines_only_exact_marker_prefix_fragments(self):
        conn = _Conn()
        conn.func_handler = _FuncHandler()
        conn.asr = _SequenceASR(("bắt", "đầu", "bài", "học"))
        provider = self.make_provider(conn)
        original_product_tool_names = google_live_module.product_tool_names
        google_live_module.product_tool_names = lambda _conn: ["start_lesson"]
        try:
            provider._start_lesson_asr_fallback_generation = 2
            await provider._run_start_lesson_asr_fallback(0, 2, [b"turn-one"])
            self.assertEqual(conn.func_handler.calls, [])
            provider._start_lesson_asr_fallback_generation = 4
            await provider._run_start_lesson_asr_fallback(0, 4, [b"turn-two"])
            self.assertEqual(conn.func_handler.calls, [])
            provider._start_lesson_asr_fallback_generation = 6
            await provider._run_start_lesson_asr_fallback(0, 6, [b"turn-three"])
            self.assertEqual(conn.func_handler.calls, [])
            provider._start_lesson_asr_fallback_generation = 8
            await provider._run_start_lesson_asr_fallback(0, 8, [b"turn-four"])
        finally:
            google_live_module.product_tool_names = original_product_tool_names

        self.assertEqual(
            conn.func_handler.calls[-1][1],
            {"name": "start_lesson", "arguments": {}},
        )

    async def test_lesson_start_asr_fallback_rejects_near_marker_fragment_chain(self):
        conn = _Conn()
        conn.func_handler = _FuncHandler()
        conn.asr = _SequenceASR(("bắt đầu", "bài hát", "bài học"))
        provider = self.make_provider(conn)
        original_product_tool_names = google_live_module.product_tool_names
        google_live_module.product_tool_names = lambda _conn: ["start_lesson"]
        try:
            await provider._run_start_lesson_asr_fallback(0, 0, [b"turn-one"])
            await provider._run_start_lesson_asr_fallback(0, 0, [b"turn-two"])
            await provider._run_start_lesson_asr_fallback(0, 0, [b"turn-three"])
        finally:
            google_live_module.product_tool_names = original_product_tool_names

        self.assertEqual(conn.func_handler.calls, [])

    async def test_lesson_start_asr_fallback_does_not_combine_nonadjacent_turns(self):
        conn = _Conn()
        conn.func_handler = _FuncHandler()
        conn.asr = _SequenceASR(("bắt đầu", "bài học"))
        provider = self.make_provider(conn)
        original_product_tool_names = google_live_module.product_tool_names
        google_live_module.product_tool_names = lambda _conn: ["start_lesson"]
        try:
            provider._start_lesson_asr_fallback_generation = 2
            await provider._run_start_lesson_asr_fallback(0, 2, [b"turn-one"])
            provider._start_lesson_asr_fallback_generation = 5
            await provider._run_start_lesson_asr_fallback(0, 5, [b"turn-after-model-output"])
        finally:
            google_live_module.product_tool_names = original_product_tool_names

        self.assertEqual(conn.func_handler.calls, [])

    async def test_lesson_start_asr_fallback_empty_transcript_breaks_fragment_chain(self):
        conn = _Conn()
        conn.func_handler = _FuncHandler()
        conn.asr = _SequenceASR(("bắt đầu", "", "bài học"))
        provider = self.make_provider(conn)
        original_product_tool_names = google_live_module.product_tool_names
        google_live_module.product_tool_names = lambda _conn: ["start_lesson"]
        try:
            for generation, frame in ((2, b"one"), (4, b"empty"), (6, b"three")):
                provider._start_lesson_asr_fallback_generation = generation
                await provider._run_start_lesson_asr_fallback(0, generation, [frame])
        finally:
            google_live_module.product_tool_names = original_product_tool_names

        self.assertEqual(conn.func_handler.calls, [])

    async def test_lesson_start_asr_fallback_live_close_breaks_fragment_chain(self):
        conn = _Conn()
        conn.func_handler = _FuncHandler()
        conn.asr = _ASR("bài học")
        provider = self.make_provider(conn)
        original_product_tool_names = google_live_module.product_tool_names
        google_live_module.product_tool_names = lambda _conn: ["start_lesson"]
        try:
            self.assertIsNone(
                provider._combine_start_lesson_asr_fragment("bắt đầu", 2)
            )
            await provider._close_live_resources()
            provider._start_lesson_asr_fallback_generation = 4
            await provider._run_start_lesson_asr_fallback(0, 4, [b"after-reopen"])
        finally:
            google_live_module.product_tool_names = original_product_tool_names

        self.assertEqual(conn.func_handler.calls, [])

    def test_lesson_start_asr_fallback_expires_fragment_chain(self):
        provider = self.make_provider(_Conn())

        with patch.object(google_live_module.time, "monotonic", return_value=100.0):
            self.assertIsNone(provider._combine_start_lesson_asr_fragment("bắt đầu", 2))
        with patch.object(google_live_module.time, "monotonic", return_value=109.0):
            self.assertIsNone(provider._combine_start_lesson_asr_fragment("bài học", 4))

        self.assertEqual(provider._start_lesson_asr_fragment, "bai hoc")

    async def test_asr_fallback_auth_failure_disables_retries_for_session(self):
        conn = _Conn()
        conn.asr = _EmptyAuthFailingASR("APIRequest failed: 401")
        conn.config["google_live"].update(
            {
                "input_min_capture_ms": 0,
                "input_speech_tail_ms": 0,
                "input_speech_rms_threshold": 500,
                "lesson_start_asr_fallback_delay_sec": 0,
                "waiting_model_timeout_sec": 4.0,
            }
        )
        provider = self.make_provider(conn)
        provider._client = _Client()
        provider._bridge = _Bridge()
        provider._bridge.rms = 1800
        provider._interaction.transition(google_live_module.InteractionState.WAITING_MODEL)
        provider._waiting_model_since = time.monotonic() - 3.2
        original_product_tool_names = google_live_module.product_tool_names
        google_live_module.product_tool_names = lambda _conn: ["start_lesson"]
        try:
            self.assertTrue(await provider.handle_audio_bytes(b"first"))
            await asyncio.sleep(0.05)
            provider._waiting_model_since = time.monotonic() - 3.2
            self.assertTrue(await provider.handle_audio_bytes(b"second"))
            await asyncio.sleep(0.05)
        finally:
            google_live_module.product_tool_names = original_product_tool_names

        self.assertEqual(conn.asr.calls, [([b"first"], "session-1", "opus")])

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
            side_effect=[100.0] * 10 + [105.0] * 10 + [116.0] * 10,
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
            google_live_module.InteractionState.LISTENING,
        )
        self.assertFalse(conn.client_abort)

    async def test_lesson_start_intent_suppresses_duplicate_while_spoken_start_is_pending(self):
        conn = _Conn()
        conn.func_handler = _FuncHandler()
        pending = asyncio.create_task(asyncio.Event().wait())
        conn.lesson_pull_task = pending
        conn.lesson_pull_task_origin = "spoken_start"
        provider = self.make_provider(conn)
        provider._suppress_start_lesson_tool_call_until = 0.0
        provider.transition_to_lesson_start = AsyncMock(return_value=True)
        original_product_tool_names = google_live_module.product_tool_names
        google_live_module.product_tool_names = lambda _conn: ["start_lesson"]
        try:
            handled = await provider._dispatch_lesson_start_intent("bắt đầu bài học")
        finally:
            google_live_module.product_tool_names = original_product_tool_names
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)

        self.assertTrue(handled)
        provider.transition_to_lesson_start.assert_not_awaited()
        self.assertEqual(conn.func_handler.calls, [])

    async def test_pending_spoken_start_recovers_after_repeat_audio_is_classified(self):
        conn = _Conn()
        conn.func_handler = _FuncHandler()
        pending = asyncio.create_task(asyncio.Event().wait())
        conn.lesson_pull_task = pending
        conn.lesson_pull_task_origin = "spoken_start"
        provider = self.make_provider(conn)
        provider._client = _Client()
        provider._bridge = _Bridge()
        provider._bridge.rms = 6000
        provider._interaction.transition(
            google_live_module.InteractionState.MODEL_SPEAKING
        )
        conn.google_live_audio_out_started_at = time.monotonic() - 1.0
        provider._last_interrupt_at = time.monotonic() - 10.0
        provider._should_drop_input_post_audio_start = lambda: False
        provider._should_suppress_robot_output_echo = lambda _audio: False
        provider._should_hold_interrupt_audio = lambda _audio: False
        provider._is_wake_greeting_protected = lambda: False
        provider._should_interrupt_for_input = lambda _audio: True
        provider.transition_to_lesson_start = AsyncMock(return_value=True)
        generation = provider._response_generation
        original_product_tool_names = google_live_module.product_tool_names
        google_live_module.product_tool_names = lambda _conn: ["start_lesson"]

        try:
            handled = await provider.handle_audio_bytes(b"repeat trigger")
            transcript_handled = await provider._dispatch_lesson_start_intent(
                "bắt đầu bài học"
            )
        finally:
            google_live_module.product_tool_names = original_product_tool_names
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)

        self.assertTrue(handled)
        self.assertTrue(transcript_handled)
        self.assertEqual(provider._response_generation, generation + 1)
        self.assertEqual(provider._bridge.forwarded, [b"pcm:repeat trigger"])
        self.assertEqual(provider._client.interrupt_calls, 1)
        self.assertEqual(
            provider._interaction.state,
            google_live_module.InteractionState.LISTENING,
        )
        self.assertFalse(conn.client_abort)
        provider.transition_to_lesson_start.assert_not_awaited()
        self.assertEqual(conn.func_handler.calls, [])

    async def test_lesson_start_intent_marks_only_local_tool_dispatch_as_sd_sync_admissible(self):
        conn = _Conn()
        observed_depths = []

        class _AdmissionProbeHandler(_FuncHandler):
            async def handle_llm_function_call(self, probe_conn, payload):
                marker = probe_conn._lesson_start_tool_dispatch_context
                observed_depths.append(isinstance(marker.get(), dict))
                return await super().handle_llm_function_call(probe_conn, payload)

        conn.func_handler = _AdmissionProbeHandler()
        provider = self.make_provider(conn)
        original_product_tool_names = google_live_module.product_tool_names
        google_live_module.product_tool_names = lambda _conn: ["start_lesson"]
        try:
            handled = await provider._dispatch_lesson_start_intent("bắt đầu bài học")
        finally:
            google_live_module.product_tool_names = original_product_tool_names

        self.assertTrue(handled)
        self.assertEqual(observed_depths, [True])
        self.assertIsNone(conn._lesson_start_tool_dispatch_context.get())

    async def test_native_start_lesson_tool_marks_local_dispatch_as_sd_sync_admissible(self):
        conn = _Conn()
        observed_depths = []

        class _AdmissionProbeHandler(_FuncHandler):
            async def handle_llm_function_call(self, probe_conn, payload):
                marker = probe_conn._lesson_start_tool_dispatch_context
                observed_depths.append(isinstance(marker.get(), dict))
                return await super().handle_llm_function_call(probe_conn, payload)

        conn.func_handler = _AdmissionProbeHandler()
        provider = self.make_provider(conn)
        provider._client = _Client()

        await provider._handle_tool_call_event(
            {"calls": [{"id": "start-1", "name": "start_lesson", "args": {}}]}
        )

        self.assertEqual(observed_depths, [True])
        self.assertIsNone(conn._lesson_start_tool_dispatch_context.get())

    async def test_start_lesson_admission_is_inherited_only_by_scheduled_lesson_task(self):
        conn = _Conn()
        child_observed = asyncio.get_running_loop().create_future()

        class _SchedulingHandler(_FuncHandler):
            async def handle_llm_function_call(self, probe_conn, payload):
                async def lesson_pull():
                    await asyncio.sleep(0)
                    marker = getattr(
                        probe_conn,
                        "_lesson_start_tool_dispatch_context",
                        None,
                    )
                    child_observed.set_result(
                        bool(marker is not None and isinstance(marker.get(), dict))
                    )

                asyncio.create_task(lesson_pull())
                return await super().handle_llm_function_call(probe_conn, payload)

        conn.func_handler = _SchedulingHandler()
        provider = self.make_provider(conn)
        original_product_tool_names = google_live_module.product_tool_names
        google_live_module.product_tool_names = lambda _conn: ["start_lesson"]
        try:
            self.assertTrue(
                await provider._dispatch_lesson_start_intent("bắt đầu bài học")
            )
        finally:
            google_live_module.product_tool_names = original_product_tool_names

        self.assertTrue(await child_observed)
        marker = conn._lesson_start_tool_dispatch_context
        self.assertIsNone(marker.get())

    async def test_continuous_background_audio_does_not_start_clean_turn(self):
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

        self.assertEqual(provider._client.end_calls, 0)
        self.assertNotEqual(
            provider._interaction.state,
            google_live_module.InteractionState.WAITING_MODEL,
        )
        self.assertIsNone(provider._user_stream_started_at)
        self.assertEqual(provider._bridge.forwarded, [])
        self.assertEqual(conn._lesson_asset_last_audio_at, 0.0)

        forwarded_before_waiting_drop = len(provider._bridge.forwarded)
        self.assertTrue(await provider.handle_audio_bytes(b"background"))
        self.assertTrue(await provider.handle_audio_bytes(b"background-again"))
        self.assertEqual(provider._client.end_calls, 0)
        self.assertEqual(len(provider._bridge.forwarded), forwarded_before_waiting_drop)
        self.assertEqual(conn._lesson_asset_last_audio_at, 0.0)

    async def test_forwarded_user_audio_marks_lesson_asset_activity(self):
        conn = _Conn()
        conn.config["google_live"].update(
            {
                "input_min_capture_ms": 0,
                "input_speech_rms_threshold": 50,
            }
        )
        provider = self.make_provider(conn)
        provider._client = _Client()
        provider._bridge = _Bridge()
        provider._bridge.rms = 1000

        self.assertTrue(await provider.handle_audio_bytes(b"speech"))

        self.assertEqual(provider._bridge.forwarded, [b"pcm:speech"])
        self.assertGreater(conn._lesson_asset_last_audio_at, 0.0)

    def test_conversation_input_timing_is_faster_than_lesson_timing(self):
        conn = _Conn()
        provider = self.make_provider(conn)

        self.assertEqual(provider._get_input_flush_delay(), 0.36)
        self.assertEqual(provider._get_user_speech_tail_sec(), 0.36)
        self.assertEqual(provider._get_user_max_capture_sec(), 5.0)

        conn.session_mode = SessionMode.LESSON
        conn.lesson_runtime = SimpleNamespace(state="RUNNING")

        self.assertEqual(provider._get_input_flush_delay(), 0.75)
        self.assertEqual(provider._get_user_speech_tail_sec(), 0.65)
        self.assertEqual(provider._get_user_max_capture_sec(), 8.0)

        # Say-it child window uses shorter single-word finalization than passive lesson.
        async def _on_child_response(_text, **_kwargs):
            return True

        conn.lesson_runtime = SimpleNamespace(
            state="RUNNING",
            _child_response_window_open=True,
            _step_passive=False,
            _step_acked=True,
            _step_completed=False,
            on_child_response=_on_child_response,
        )
        self.assertEqual(provider._get_input_flush_delay(), 0.32)
        self.assertEqual(provider._get_user_speech_tail_sec(), 0.28)
        self.assertEqual(provider._get_user_max_capture_sec(), 4.0)

    async def test_model_audio_end_returns_to_listening_while_echo_tail_stays_suppressed(self):
        conn = _Conn()
        provider = self.make_provider(conn)
        provider._client = _Client()
        provider._bridge = _Bridge()
        provider._bridge.rms = 2500
        provider._interaction.transition(google_live_module.InteractionState.WAITING_MODEL)

        await provider._handle_live_event({"type": "audio_end"})

        self.assertEqual(provider._interaction.state, google_live_module.InteractionState.LISTENING)
        await provider._open_user_audio_window("test_after_audio_end")
        self.assertTrue(await provider.handle_audio_bytes(b"hi-again"))
        self.assertEqual(provider._bridge.forwarded, [])

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

    async def test_interrupt_does_not_forward_current_audio_when_client_disconnected(self):
        conn = _Conn()
        provider = self.make_provider(conn)
        provider._client = _Client()
        provider._client.connected = False
        provider._bridge = _Bridge()
        provider._should_interrupt_for_input = lambda _decoded: True

        self.assertTrue(await provider.handle_audio_bytes(b"frame"))

        self.assertEqual(provider._bridge.forwarded, [])
        self.assertFalse(
            any(
                level == "warning" and args and "runtime failure" in str(args[0])
                for level, args, _kwargs in conn.logger.messages
            )
        )

    async def test_stale_receive_loop_exits_when_client_was_closed_before_start(self):
        conn = _Conn()
        provider = self.make_provider(conn)
        provider._session_generation = 2
        provider._client = None
        provider._bridge = _Bridge()

        await provider._receive_events_loop(1)

        warnings = [
            args[0]
            for level, args, _kwargs in conn.logger.messages
            if level == "warning" and args
        ]
        self.assertFalse(
            any("Google Live runtime failure" in str(message) for message in warnings)
        )

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

    async def test_model_audio_start_clears_stale_user_stream_before_tail_frame(self):
        conn = _Conn()
        conn.config["google_live"].update(
            {
                "input_max_capture_ms": 1000,
                "input_min_capture_ms": 0,
                "conversation_input_speech_tail_ms": 10,
                "input_flush_delay_sec": 60,
                "waiting_model_timeout_sec": 0.01,
            }
        )
        provider = self.make_provider(conn)
        provider._client = _Client()
        provider._bridge = _Bridge()
        provider._interaction.transition(google_live_module.InteractionState.USER_STREAMING)
        provider._user_stream_started_at = time.monotonic() - 2
        provider._user_stream_last_speech_at = time.monotonic() - 2
        provider._user_stream_response_id = provider._response_generation
        provider._user_stream_frames = 20

        await provider._handle_live_event({"type": "audio_start"})
        await provider.handle_audio_bytes(b"tail")

        self.assertEqual(provider._client.end_calls, 0)
        self.assertEqual(
            provider._interaction.state,
            google_live_module.InteractionState.USER_STREAMING,
        )
        self.assertIsNone(provider._waiting_model_since)

    async def test_wake_listen_window_expires_without_mic_audio(self):
        conn = _Conn()
        conn.config["google_live"]["wake_audio_allow_window_sec"] = 0.01
        provider = self.make_provider(conn)
        provider._client = _Client()
        provider._bridge = _Bridge()

        await provider.handle_text_message(
            '{"type":"listen","state":"detect","text":"Hi ESP"}'
        )
        await asyncio.sleep(0.15)

        self.assertEqual(
            provider._interaction.state,
            google_live_module.InteractionState.IDLE,
        )
        self.assertEqual(provider._client.end_calls, 0)
        sent = [json.loads(payload) for payload in conn.websocket.sent]
        self.assertEqual(sent[-1]["type"], "tts")
        self.assertEqual(sent[-1]["state"], "stop")
        self.assertFalse(sent[-1]["continue_listening"])
        self.assertEqual(sent[-1]["listen_mode"], "manual")
        self.assertFalse(conn.client_abort)
        self.assertTrue(
            any(
                "user_audio_window_expired" in str(args)
                and "wake_word" in str(args)
                for _, args, _ in conn.logger.messages
            )
        )

    async def test_wake_listen_window_expiry_cancels_after_mic_audio(self):
        conn = _Conn()
        conn.config["google_live"]["wake_audio_allow_window_sec"] = 0.01
        provider = self.make_provider(conn)
        provider._client = _Client()
        provider._bridge = _Bridge()

        await provider.handle_text_message(
            '{"type":"listen","state":"detect","text":"Hi ESP"}'
        )
        await provider.handle_audio_bytes(b"voice")
        await asyncio.sleep(0.15)

        sent = [json.loads(payload) for payload in conn.websocket.sent]
        manual_stop = [
            payload
            for payload in sent
            if payload.get("type") == "tts"
            and payload.get("state") == "stop"
            and payload.get("continue_listening") is False
        ]
        self.assertEqual(manual_stop, [])
        self.assertEqual(provider._bridge.forwarded, [b"pcm:voice"])

    async def test_audio_after_expired_wake_window_accepts_speech_in_stale_listening_state(self):
        conn = _Conn()
        conn.session_mode = SessionMode.CONVERSATION
        conn.config["google_live"]["input_speech_rms_threshold"] = 500
        provider = self.make_provider(conn)
        provider._client = _Client()
        provider._bridge = _Bridge()
        provider._bridge.rms = 1200
        provider._interaction.transition(google_live_module.InteractionState.LISTENING)
        provider._user_audio_allowed_until = time.monotonic() - 1

        self.assertTrue(await provider.handle_audio_bytes(b"start lesson"))

        self.assertEqual(provider._bridge.forwarded, [b"pcm:start lesson"])
        self.assertIsNotNone(provider._user_stream_started_at)

    async def test_audio_after_expired_wake_window_drops_low_rms_noise(self):
        conn = _Conn()
        conn.session_mode = SessionMode.CONVERSATION
        conn.config["google_live"]["input_speech_rms_threshold"] = 500
        provider = self.make_provider(conn)
        provider._client = _Client()
        provider._bridge = _Bridge()
        provider._bridge.rms = 200
        provider._interaction.transition(google_live_module.InteractionState.LISTENING)
        provider._user_audio_allowed_until = time.monotonic() - 1

        self.assertTrue(await provider.handle_audio_bytes(b"ambient"))

        self.assertEqual(provider._bridge.forwarded, [])
        self.assertIsNone(provider._user_stream_started_at)

    async def test_expired_wake_window_drop_log_is_throttled(self):
        conn = _Conn()
        conn.session_mode = SessionMode.CONVERSATION
        conn.config["google_live"]["input_speech_rms_threshold"] = 500
        provider = self.make_provider(conn)
        provider._client = _Client()
        provider._bridge = _Bridge()
        provider._bridge.rms = 200
        provider._last_expired_audio_window_drop_log_at = time.monotonic() - 10
        provider._interaction.transition(google_live_module.InteractionState.LISTENING)
        provider._user_audio_allowed_until = time.monotonic() - 1

        await provider.handle_audio_bytes(b"ambient-one")
        await provider.handle_audio_bytes(b"ambient-two")

        logs = [
            args
            for level, args, _kwargs in conn.logger.messages
            if level == "info"
            and args
            and "expired_user_audio_window" in str(args[0])
        ]
        self.assertEqual(len(logs), 1)

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
        provider._waiting_model_since = time.monotonic() - 1.3

        self.assertTrue(await provider.handle_audio_bytes(b"too-soon"))

        self.assertEqual(
            provider._interaction.state,
            google_live_module.InteractionState.WAITING_MODEL,
        )
        self.assertEqual(provider._bridge.forwarded, [])

    async def test_default_waiting_model_timeout_reopens_input_after_short_grace(self):
        conn = _Conn()
        conn.config["google_live"]["waiting_model_timeout_sec"] = 0.01
        provider = self.make_provider(conn)
        provider._client = _Client()
        provider._bridge = _Bridge()
        provider._interaction.transition(google_live_module.InteractionState.WAITING_MODEL)
        provider._waiting_model_since = time.monotonic() - 0.1

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

        self.assertEqual(provider._interaction.state, google_live_module.InteractionState.IDLE)
        self.assertIsNone(provider._waiting_model_since)
        self.assertEqual(provider._client.text, [])
        sent = [json.loads(payload) for payload in conn.websocket.sent]
        self.assertEqual(sent[-1]["state"], "stop")
        self.assertFalse(sent[-1]["continue_listening"])

    async def test_waiting_model_timeout_stops_robot_listening_when_live_returns_no_audio(self):
        conn = _Conn()
        conn.config["google_live"]["waiting_model_timeout_sec"] = 0.01
        provider = self.make_provider(conn)
        provider._client = _Client()
        provider._bridge = _Bridge()

        await provider._finalize_user_audio_input("listen_stop")
        await asyncio.sleep(0.05)

        sent = [json.loads(payload) for payload in conn.websocket.sent]
        self.assertEqual(provider._interaction.state, google_live_module.InteractionState.IDLE)
        self.assertEqual(sent[-1]["type"], "tts")
        self.assertEqual(sent[-1]["state"], "stop")
        self.assertFalse(sent[-1]["continue_listening"])
        self.assertEqual(sent[-1]["listen_mode"], "manual")

    async def test_default_waiting_model_timeout_reopens_without_next_audio_frame(self):
        conn = _Conn()
        provider = self.make_provider(conn)
        provider._client = _Client()
        provider._bridge = _Bridge()

        await provider._finalize_user_audio_input("listen_stop")
        # Default waiting_model_timeout_sec floors to 5.0s in production policy.
        await asyncio.sleep(5.3)

        self.assertEqual(
            provider._interaction.state,
            google_live_module.InteractionState.IDLE,
        )
        self.assertIsNone(provider._waiting_model_since)
        self.assertEqual(provider._client.text, [])

    async def test_repeated_waiting_model_timeouts_reopen_fresh_session_without_resumption(self):
        conn = _Conn()
        conn.config["google_live"]["waiting_model_timeout_sec"] = 0.01
        conn.google_live_session_resumption_handle = "stale-handle"
        old_client = _Client()
        new_clients = []
        configs = []

        def client_factory(config, _logger):
            configs.append(dict(config))
            client = _Client()
            new_clients.append(client)
            return client

        provider = GoogleLiveProvider(conn, client_factory=client_factory)
        self.provider = provider
        provider._client = old_client
        provider._bridge = _Bridge()
        provider._session_generation = 1

        with patch.object(google_live_module, "GoogleLiveAudioBridge", lambda *_a, **_k: _Bridge()):
            for _ in range(google_live_module.GoogleLiveProvider._SILENT_LIVE_REOPEN_TIMEOUTS):
                provider._interaction.transition(google_live_module.InteractionState.WAITING_MODEL)
                provider._waiting_model_since = time.monotonic() - 1
                await provider._release_waiting_model_after_timeout(0)

        self.assertEqual(old_client.closed, 1)
        self.assertEqual(len(new_clients), 1)
        self.assertIs(provider._client, new_clients[0])
        self.assertIsNone(conn.google_live_session_resumption_handle)
        self.assertNotIn("session_resumption_handle", configs[-1])
        self.assertEqual(provider._interaction.state, google_live_module.InteractionState.LISTENING)

    async def test_silent_live_reopen_is_cooldown_limited_for_empty_noise_turns(self):
        class _HoldingClient(_Client):
            def __init__(self):
                super().__init__()
                self.hold = asyncio.Event()

            async def receive_events(self):
                await self.hold.wait()
                if False:
                    yield None

        conn = _Conn()
        conn.config["google_live"]["waiting_model_timeout_sec"] = 0.01
        old_client = _HoldingClient()
        new_clients = []

        def client_factory(_config, _logger):
            client = _HoldingClient()
            new_clients.append(client)
            return client

        provider = GoogleLiveProvider(conn, client_factory=client_factory)
        self.provider = provider
        provider._client = old_client
        provider._bridge = _Bridge()
        provider._session_generation = 1

        with patch.object(google_live_module, "GoogleLiveAudioBridge", lambda *_a, **_k: _Bridge()):
            reopen_n = google_live_module.GoogleLiveProvider._SILENT_LIVE_REOPEN_TIMEOUTS
            for _ in range(reopen_n):
                provider._interaction.transition(google_live_module.InteractionState.WAITING_MODEL)
                provider._waiting_model_since = time.monotonic() - 1
                await provider._release_waiting_model_after_timeout(0)

            self.assertEqual(len(new_clients), 1)
            first_reopened_client = provider._client

            for _ in range(reopen_n):
                provider._interaction.transition(google_live_module.InteractionState.WAITING_MODEL)
                provider._waiting_model_since = time.monotonic() - 1
                await provider._release_waiting_model_after_timeout(0)

        self.assertEqual(len(new_clients), 1)
        self.assertIs(provider._client, first_reopened_client)
        self.assertEqual(provider._interaction.state, google_live_module.InteractionState.IDLE)

    async def test_start_lesson_tool_call_is_deduped_after_local_lesson_start_intent(self):
        conn = _Conn()
        provider = self.make_provider(conn)
        provider._client = _Client()
        provider._suppress_start_lesson_tool_call_until = time.monotonic() + 2
        executed = []

        async def _execute(name, args, **_kwargs):
            executed.append((name, args))
            return {"result": "ran"}

        provider._execute_tool_call_with_timeout = _execute

        await provider._handle_tool_call_event(
            {"calls": [{"id": "c1", "name": "start_lesson", "args": {}}]}
        )

        self.assertEqual(executed, [])
        self.assertEqual(
            provider._client.tool_responses,
            [[{"id": "c1", "name": "start_lesson", "response": {"result": "Lesson already starting."}}]],
        )

    async def test_native_start_lesson_tool_coalesces_pending_spoken_start_and_recovers_state(self):
        conn = _Conn()
        pending = asyncio.create_task(asyncio.Event().wait())
        conn.lesson_pull_task = pending
        conn.lesson_pull_task_origin = "spoken_start"
        provider = self.make_provider(conn)
        provider._client = _Client()
        provider._interaction.transition(
            google_live_module.InteractionState.INTERRUPTING
        )
        conn.client_abort = True
        provider._suppress_start_lesson_tool_call_until = 0.0
        provider._execute_tool_call_with_timeout = AsyncMock(return_value={"result": "ran"})

        try:
            await provider._handle_tool_call_event(
                {"calls": [{"id": "c2", "name": "start_lesson", "args": {}}]}
            )
        finally:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)

        provider._execute_tool_call_with_timeout.assert_not_awaited()
        self.assertEqual(
            provider._interaction.state,
            google_live_module.InteractionState.LISTENING,
        )
        self.assertFalse(conn.client_abort)
        self.assertEqual(
            provider._client.tool_responses,
            [[{"id": "c2", "name": "start_lesson", "response": {"result": "Lesson already starting."}}]],
        )

    async def test_text_turn_arms_waiting_model_timeout_without_next_audio_frame(self):
        conn = _Conn()
        conn.config["google_live"]["waiting_model_timeout_sec"] = 0.01
        provider = self.make_provider(conn)
        provider._client = _Client()
        provider._bridge = _Bridge()
        provider._has_active_output = lambda: True

        self.assertTrue(
            await provider.handle_text_message('{"type":"text","text":"continue"}')
        )
        self.assertEqual(provider._client.text, ["continue"])
        self.assertEqual(
            provider._interaction.state,
            google_live_module.InteractionState.WAITING_MODEL,
        )
        self.assertIsNotNone(provider._waiting_model_since)

        await asyncio.sleep(0.05)

        self.assertEqual(
            provider._interaction.state,
            google_live_module.InteractionState.IDLE,
        )
        self.assertIsNone(provider._waiting_model_since)

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

    async def test_lesson_child_idle_flush_does_not_wait_for_model_audio(self):
        conn = _Conn()
        conn.config["google_live"]["waiting_model_timeout_sec"] = 0.01
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
        provider._bridge.rms = 2200
        provider._user_audio_allowed_until = time.monotonic() + 5
        provider._user_stream_started_at = time.monotonic()
        provider._user_stream_response_id = 5
        provider._user_stream_frames = 12
        provider._input_flush_generation = 9

        await provider._flush_input_after_idle(0, 9)

        self.assertEqual(provider._client.end_calls, 1)
        self.assertEqual(
            provider._interaction.state,
            google_live_module.InteractionState.LISTENING,
        )
        self.assertIsNone(provider._user_stream_started_at)
        self.assertIsNone(provider._waiting_model_since)
        self.assertIsNone(provider._waiting_model_timeout_task)
        self.assertTrue(provider._lesson_child_audio_pending_transcript)

    async def test_default_conversation_audio_turn_finalizes_inside_fast_grace(self):
        conn = _Conn()
        provider = self.make_provider(conn)
        provider._client = _Client()
        provider._bridge = _Bridge()

        started = time.monotonic()
        self.assertTrue(await provider.handle_audio_bytes(b"hello"))
        await asyncio.sleep(0.55)

        self.assertEqual(provider._client.end_calls, 1)
        self.assertLess(time.monotonic() - started, 0.65)
        self.assertEqual(
            provider._interaction.state,
            google_live_module.InteractionState.WAITING_MODEL,
        )
        self.assertIsNone(provider._user_stream_started_at)

    async def test_conversation_start_ignores_low_rms_noise_until_speech(self):
        conn = _Conn()
        conn.config["google_live"]["input_speech_rms_threshold"] = 500
        provider = self.make_provider(conn)
        provider._client = _Client()
        provider._bridge = _Bridge()
        provider._user_audio_allowed_until = time.monotonic() + 5

        provider._bridge.rms = 200
        self.assertTrue(await provider.handle_audio_bytes(b"noise"))

        self.assertEqual(provider._bridge.forwarded, [])
        self.assertIsNone(provider._user_stream_started_at)
        self.assertEqual(provider._client.end_calls, 0)
        self.assertIsNone(provider._waiting_model_since)

        provider._bridge.rms = 2200
        self.assertTrue(await provider.handle_audio_bytes(b"voice"))

        self.assertEqual(provider._bridge.forwarded, [b"pcm:voice"])
        self.assertIsNotNone(provider._user_stream_started_at)

    async def test_lesson_child_audio_uses_clean_turn_finalizer(self):
        conn = _Conn()
        conn.config["google_live"].update(
            {
                "input_min_capture_ms": 0,
                "input_speech_tail_ms": 0,
                "input_max_capture_ms": 0,
                "lesson_child_input_speech_tail_ms": 0,
                "lesson_child_input_max_capture_ms": 0,
                "lesson_child_input_flush_delay_sec": 0.01,
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
        provider._bridge.rms = 2200
        provider._user_audio_allowed_until = time.monotonic() + 5

        self.assertTrue(await provider.handle_audio_bytes(b"barn"))
        self.assertEqual(
            provider._bridge.forwarded,
            [b"pcm:barn"],
        )
        self.assertGreater(conn._lesson_asset_last_audio_at, 0.0)
        self.assertEqual(provider._client.end_calls, 1)
        self.assertEqual(
            provider._interaction.state,
            google_live_module.InteractionState.LISTENING,
        )
        self.assertIsNone(provider._user_stream_started_at)
        self.assertIsNone(provider._waiting_model_since)
        self.assertIsNone(provider._waiting_model_timeout_task)
        self.assertTrue(provider._lesson_child_audio_pending_transcript)

    async def test_lesson_child_ambient_audio_below_threshold_does_not_open_turn(self):
        conn = _Conn()
        conn.config["google_live"].update(
            {
                "input_min_capture_ms": 0,
                "input_speech_tail_ms": 0,
                "input_max_capture_ms": 0,
                "lesson_child_input_speech_tail_ms": 0,
                "lesson_child_input_max_capture_ms": 0,
                "lesson_child_input_flush_delay_sec": 0.01,
                "lesson_child_input_speech_rms_threshold": 2000,
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
        provider._bridge.rms = 1475
        provider._user_audio_allowed_until = time.monotonic() + 5

        self.assertTrue(await provider.handle_audio_bytes(b"ambient"))

        self.assertEqual(provider._bridge.forwarded, [])
        self.assertEqual(provider._client.end_calls, 0)
        self.assertIsNone(provider._user_stream_started_at)
        self.assertFalse(provider._lesson_child_audio_pending_transcript)

        provider._bridge.rms = 2200
        self.assertTrue(await provider.handle_audio_bytes(b"barn"))

        self.assertEqual(
            provider._bridge.forwarded,
            [b"pcm:barn"],
        )
        self.assertEqual(provider._client.end_calls, 1)
        self.assertTrue(provider._lesson_child_audio_pending_transcript)

    async def test_lesson_child_short_speech_frame_opens_turn(self):
        conn = _Conn()
        conn.config["google_live"].update(
            {
                "input_min_capture_ms": 0,
                "input_speech_tail_ms": 0,
                "input_max_capture_ms": 0,
                "lesson_child_input_speech_tail_ms": 0,
                "lesson_child_input_max_capture_ms": 0,
                "lesson_child_input_flush_delay_sec": 0.01,
                "lesson_child_input_speech_rms_threshold": 2000,
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

        provider._bridge.rms = 2071
        self.assertTrue(await provider.handle_audio_bytes(b"bo"))

        self.assertEqual(provider._bridge.forwarded, [b"pcm:bo"])
        self.assertEqual(provider._client.end_calls, 1)
        self.assertIsNone(provider._user_stream_started_at)
        self.assertTrue(provider._lesson_child_audio_pending_transcript)

    async def test_lesson_child_ambient_audio_after_window_expiry_does_not_open_turn(self):
        conn = _Conn()
        conn.config["google_live"].update(
            {
                "input_min_capture_ms": 0,
                "input_speech_tail_ms": 0,
                "input_max_capture_ms": 0,
                "lesson_child_input_speech_tail_ms": 0,
                "lesson_child_input_max_capture_ms": 0,
                "lesson_child_input_flush_delay_sec": 0.01,
                "lesson_child_input_speech_rms_threshold": 2000,
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
        provider._bridge.rms = 1254
        provider._interaction.transition(google_live_module.InteractionState.IDLE)
        provider._user_audio_allowed_until = time.monotonic() - 1

        self.assertTrue(await provider.handle_audio_bytes(b"ambient"))

        self.assertEqual(provider._bridge.forwarded, [])
        self.assertEqual(provider._client.end_calls, 0)
        self.assertIsNone(provider._user_stream_started_at)
        self.assertFalse(provider._lesson_child_audio_pending_transcript)

    async def test_lesson_child_audio_finalization_does_not_wait_for_model_audio(self):
        conn = _Conn()
        conn.config["google_live"].update(
            {
                "input_min_capture_ms": 0,
                "input_speech_tail_ms": 0,
                "input_max_capture_ms": 0,
                "lesson_child_input_speech_tail_ms": 0,
                "lesson_child_input_max_capture_ms": 0,
                "lesson_child_input_flush_delay_sec": 0.01,
                "waiting_model_timeout_sec": 0.01,
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
        provider._bridge.rms = 2200
        provider._user_audio_allowed_until = time.monotonic() + 5

        handled = True
        for frame in (b"barn1", b"barn2", b"barn3", b"barn4", b"barn5"):
            handled = handled and await provider.handle_audio_bytes(frame)

        self.assertTrue(handled)
        self.assertEqual(provider._client.end_calls, 1)
        self.assertEqual(
            provider._interaction.state,
            google_live_module.InteractionState.LISTENING,
        )
        self.assertIsNone(provider._waiting_model_since)
        self.assertIsNone(provider._waiting_model_timeout_task)
        self.assertTrue(provider._lesson_child_audio_pending_transcript)

    async def test_lesson_child_transcript_timeout_allows_retry_audio(self):
        conn = _Conn()
        conn.config["google_live"].update(
            {
                "input_min_capture_ms": 0,
                "input_speech_tail_ms": 0,
                "input_max_capture_ms": 0,
                "lesson_child_input_speech_tail_ms": 0,
                "lesson_child_input_max_capture_ms": 0,
                "lesson_child_input_flush_delay_sec": 0.01,
                "lesson_child_transcript_timeout_sec": 0.01,
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
        provider._bridge.rms = 2200
        provider._user_audio_allowed_until = time.monotonic() + 5

        for frame in (b"first1", b"first2", b"first3", b"first4", b"first5"):
            self.assertTrue(await provider.handle_audio_bytes(frame))
        self.assertTrue(provider._lesson_child_audio_pending_transcript)

        await asyncio.sleep(0.05)
        self.assertFalse(provider._lesson_child_audio_pending_transcript)

        for frame in (b"second1", b"second2", b"second3", b"second4", b"second5"):
            self.assertTrue(await provider.handle_audio_bytes(frame))
        self.assertEqual(
            provider._bridge.forwarded,
            [
                b"pcm:first1",
                b"pcm:second1",
            ],
        )
        self.assertIsNone(provider._waiting_model_since)

    async def test_lesson_child_transcript_timeout_routes_stt_unavailable_to_runtime(self):
        conn = _Conn()
        conn.config["google_live"].update(
            {
                "input_min_capture_ms": 0,
                "input_speech_tail_ms": 0,
                "input_max_capture_ms": 0,
                "lesson_child_input_speech_tail_ms": 0,
                "lesson_child_input_max_capture_ms": 0,
                "lesson_child_input_flush_delay_sec": 0.01,
                "lesson_child_transcript_timeout_sec": 0.01,
            }
        )
        handled = []
        failures = []

        async def _on_child_response(text, **kwargs):
            handled.append((text, kwargs))
            return True

        async def _on_child_response_failure(reason):
            failures.append(reason)
            return True

        conn.lesson_runtime = SimpleNamespace(
            state="RUNNING",
            _step_passive=False,
            _step_completed=False,
            _child_response_window_open=True,
            on_child_response=_on_child_response,
            on_child_response_failure=_on_child_response_failure,
        )
        provider = self.make_provider(conn)
        provider._client = _Client()
        provider._bridge = _Bridge()
        old_bridge = provider._bridge
        provider._bridge.rms = 2200
        provider._user_audio_allowed_until = time.monotonic() + 5

        for frame in (b"first1", b"first2", b"first3", b"first4", b"first5"):
            self.assertTrue(await provider.handle_audio_bytes(frame))
        await asyncio.sleep(0.05)

        self.assertEqual(handled, [])
        self.assertEqual(failures, ["stt_unavailable"])
        self.assertFalse(provider._lesson_child_audio_pending_transcript)
        self.assertEqual(old_bridge.stop_calls, 2)

    async def test_lesson_child_listen_stop_does_not_wait_for_model_audio(self):
        conn = _Conn()
        conn.config["google_live"]["waiting_model_timeout_sec"] = 0.01
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
        provider._interaction.transition(google_live_module.InteractionState.USER_STREAMING)

        await provider._finalize_user_audio_input("listen_stop")

        self.assertEqual(provider._client.end_calls, 1)
        self.assertEqual(
            provider._interaction.state,
            google_live_module.InteractionState.LISTENING,
        )
        self.assertIsNone(provider._waiting_model_since)
        self.assertIsNone(provider._waiting_model_timeout_task)
        self.assertTrue(provider._lesson_child_audio_pending_transcript)

    async def test_lesson_retry_prompt_pending_audio_does_not_fall_through_to_chat_model(self):
        conn = _Conn()
        conn.session_mode = SessionMode.LESSON
        conn.google_live_lesson_prompt_output_allowed = True
        conn.config["google_live"].update(
            {
                "input_min_capture_ms": 0,
                "input_speech_tail_ms": 0,
                "input_max_capture_ms": 0,
                "lesson_child_input_speech_tail_ms": 0,
                "lesson_child_input_max_capture_ms": 0,
                "lesson_child_input_flush_delay_sec": 0.01,
                "waiting_model_timeout_sec": 0.01,
            }
        )
        conn.lesson_runtime = SimpleNamespace(
            state="RUNNING",
            _step_passive=False,
            _step_completed=False,
            _child_response_window_open=False,
            on_child_response=lambda *_args, **_kwargs: True,
        )
        provider = self.make_provider(conn)
        provider._client = _Client()
        provider._bridge = _Bridge()
        provider._user_audio_allowed_until = time.monotonic() + 5

        handled = await provider.handle_audio_bytes(b"retry-echo")

        self.assertTrue(handled)
        self.assertEqual(provider._bridge.forwarded, [])
        self.assertEqual(provider._client.end_calls, 0)
        self.assertIsNone(provider._waiting_model_since)
        self.assertIsNone(provider._waiting_model_timeout_task)
        self.assertNotEqual(
            provider._interaction.state,
            google_live_module.InteractionState.WAITING_MODEL,
        )

    async def test_lesson_listen_stop_outside_child_window_does_not_wait_for_chat_model(self):
        conn = _Conn()
        conn.session_mode = SessionMode.LESSON
        conn.config["google_live"]["waiting_model_timeout_sec"] = 0.01
        conn.lesson_runtime = SimpleNamespace(
            state="RUNNING",
            _step_passive=True,
            _step_completed=False,
        )
        provider = self.make_provider(conn)
        provider._client = _Client()
        provider._bridge = _Bridge()
        provider._interaction.transition(google_live_module.InteractionState.USER_STREAMING)

        await provider._finalize_user_audio_input("listen_stop")

        self.assertEqual(provider._client.end_calls, 1)
        self.assertEqual(
            provider._interaction.state,
            google_live_module.InteractionState.LISTENING,
        )
        self.assertIsNone(provider._waiting_model_since)
        self.assertIsNone(provider._waiting_model_timeout_task)

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

        conn.lesson_runtime = None
        barge_interrupts = []

        async def _barge_interrupt(reason):
            barge_interrupts.append(reason)

        provider._begin_user_interrupt = _barge_interrupt
        await provider._on_user_transcript_barge_in("plain chatter")
        self.assertEqual(barge_interrupts, ["transcript_barge_in"])

        self.assertEqual(provider._lesson_start_ack_text(None), "")
        self.assertIn("chưa", provider._lesson_start_ack_text(SimpleNamespace(action=Action.ERROR, response="", result="")))
        self.assertIn("chưa", provider._lesson_start_ack_text(SimpleNamespace(action=Action.RESPONSE, response="", result="failed")))
        self.assertEqual(
            provider._lesson_start_ack_text(
                SimpleNamespace(action=Action.RECORD, response="", result="lesson start scheduled")
            ),
            "",
        )
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

        conn.google_live_audible_output_until = time.monotonic() + 1
        config["echo_bypass_interrupt_enabled"] = False
        provider._bridge._aec_processor = SimpleNamespace(bypassed=False)
        provider._last_echo_suppressed_log_at["robot_speaking"] = time.monotonic() - 2
        self.assertEqual(provider._current_audio_suppression_reason(), "robot_speaking")
        self.assertEqual(
            provider._current_interaction_state_for_audio().value,
            "MODEL_SPEAKING",
        )
        log_start = len(conn.logger.messages)
        self.assertTrue(provider._should_suppress_robot_output_echo(b"pcm"))
        new_logs = conn.logger.messages[log_start:]
        self.assertFalse(
            any("aec_live_vad_forward" in str(args) for _, args, _ in new_logs)
        )
        self.assertTrue(any("echo_suppressed" in str(args) for _, args, _ in new_logs))
        conn.google_live_audible_output_until = 0

        config.update({"echo_bypass_interrupt_enabled": True, "robot_output_echo_bypass_rms_threshold": "bad", "robot_output_echo_bypass_min_duration_sec": 0})
        self.assertTrue(provider._should_bypass_echo_gate_for_loud_user(config, 2000))
        self.assertGreater(provider._user_audio_allowed_until, time.monotonic())

        provider._bridge.rms = 5000
        config.update({"echo_bypass_interrupt_enabled": False, "robot_output_echo_bypass_rms_threshold": 100, "robot_output_echo_bypass_min_duration_sec": 0})
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
        conn.config = {"Intent": {}, "selected_module": "bad"}
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

    async def test_lesson_transition_interrupts_waiting_model_and_releases_busy_state(self):
        conn = _Conn()
        provider = self.make_provider(conn)
        conn.voice_provider = provider
        conn.is_realtime_busy = lambda: provider._interaction.state not in {
            google_live_module.InteractionState.IDLE,
            google_live_module.InteractionState.LISTENING,
        }
        provider._interaction.transition(google_live_module.InteractionState.WAITING_MODEL)
        provider._begin_user_interrupt = AsyncMock()
        conn.client_abort = True
        conn.client_is_speaking = True

        admitted = await provider.transition_to_lesson_start()

        self.assertTrue(admitted)
        provider._begin_user_interrupt.assert_awaited_once_with("lesson_start_intent")
        self.assertEqual(
            provider._interaction.state,
            google_live_module.InteractionState.LISTENING,
        )
        self.assertFalse(conn.client_abort)
        self.assertFalse(conn.client_is_speaking)
        self.assertTrue(conn.lesson_start_handoff_active())

    async def test_lesson_transition_uses_terminal_voice_stop(self):
        conn = _Conn()
        provider = self.make_provider(conn)
        bridge = _Bridge()
        bridge.stop_output_for_lesson = AsyncMock()
        provider._bridge = bridge
        conn.voice_provider = provider
        conn.is_realtime_busy = lambda: provider._interaction.state not in {
            google_live_module.InteractionState.IDLE,
            google_live_module.InteractionState.LISTENING,
        }

        admitted = await provider.transition_to_lesson_start()

        self.assertTrue(admitted)
        bridge.stop_output_for_lesson.assert_awaited_once_with()
        self.assertEqual(bridge.stop_calls, 0)

    async def test_lesson_transition_settles_stale_model_speaking_after_terminal_stop(self):
        conn = _Conn()
        conn.config["lesson"] = {"live_transition_timeout_sec": 0.1}
        provider = self.make_provider(conn)
        conn.voice_provider = provider
        provider._begin_user_interrupt = AsyncMock()
        provider._has_active_output = lambda: False
        conn.client_is_speaking = False
        conn.client_have_voice = False
        busy_checks = 0

        def busy():
            nonlocal busy_checks
            busy_checks += 1
            if busy_checks == 1:
                # An audio callback can publish the short stop echo-tail as model
                # output after transition_to_lesson_start already selected LISTENING.
                provider._interaction.transition(
                    google_live_module.InteractionState.MODEL_SPEAKING
                )
            return provider._interaction.state not in {
                google_live_module.InteractionState.IDLE,
                google_live_module.InteractionState.LISTENING,
            }

        conn.is_realtime_busy = busy

        admitted = await provider.transition_to_lesson_start()

        self.assertTrue(admitted)
        self.assertGreaterEqual(busy_checks, 2)
        self.assertEqual(
            provider._interaction.state,
            google_live_module.InteractionState.LISTENING,
        )

    async def test_lesson_transition_late_echo_log_does_not_re_latch_busy_state(self):
        conn = _Conn()
        conn.config["lesson"] = {"live_transition_timeout_sec": 0.1}
        provider = self.make_provider(conn)
        conn.voice_provider = provider
        provider._begin_user_interrupt = AsyncMock()
        provider._has_active_output = lambda: False
        conn.client_is_speaking = False
        conn.client_have_voice = False
        conn.is_realtime_busy = lambda: provider._interaction.state not in {
            google_live_module.InteractionState.IDLE,
            google_live_module.InteractionState.LISTENING,
        }

        self.assertTrue(await provider.transition_to_lesson_start())

        # A late echo frame is diagnostic MODEL_SPEAKING evidence only. Once the
        # real output flag clears, logging that frame must not re-latch the
        # authoritative interaction state and block SD maintenance forever.
        provider._has_active_output = lambda: True
        provider._log_audio_decision("suppress_echo", "robot_speaking", b"pcm")
        provider._has_active_output = lambda: False

        self.assertEqual(
            provider._interaction.state,
            google_live_module.InteractionState.LISTENING,
        )
        self.assertFalse(conn.is_realtime_busy())

    async def test_lesson_transition_timeout_remains_retryable(self):
        conn = _Conn()
        conn.config["lesson"] = {"live_transition_timeout_sec": 0.05}
        provider = self.make_provider(conn)
        provider._begin_user_interrupt = AsyncMock()
        conn.is_realtime_busy = lambda: True

        admitted = await provider.transition_to_lesson_start()

        self.assertFalse(admitted)
        provider._begin_user_interrupt.assert_awaited_once_with("lesson_start_intent")
        self.assertFalse(conn.lesson_start_handoff_active())
        self.assertEqual(conn.handoff_releases, [((1, 1), "live_transition_timeout", True)])

    async def test_lesson_transition_exception_releases_handoff(self):
        conn = _Conn()
        provider = self.make_provider(conn)
        provider._begin_user_interrupt = AsyncMock(side_effect=RuntimeError("stop failed"))

        with self.assertRaisesRegex(RuntimeError, "stop failed"):
            await provider.transition_to_lesson_start()

        self.assertFalse(conn.lesson_start_handoff_active())
        self.assertEqual(conn.handoff_releases, [((1, 1), "live_transition_failed", True)])

    async def test_lesson_transition_cancellation_releases_handoff(self):
        conn = _Conn()
        provider = self.make_provider(conn)
        interrupt_started = asyncio.Event()

        async def block_interrupt(_reason):
            interrupt_started.set()
            await asyncio.Event().wait()

        provider._begin_user_interrupt = block_interrupt
        task = asyncio.create_task(provider.transition_to_lesson_start())
        await interrupt_started.wait()
        task.cancel()

        with self.assertRaises(asyncio.CancelledError):
            await task

        self.assertFalse(conn.lesson_start_handoff_active())
        self.assertEqual(conn.handoff_releases, [((1, 1), "cancelled", True)])

    async def test_failed_lesson_start_handoff_restores_connected_live_input(self):
        conn = _Conn()
        provider = self.make_provider(conn)
        provider._client = _Client()
        provider._interaction.transition(google_live_module.InteractionState.WAITING_MODEL)
        provider._open_user_audio_window = AsyncMock()
        conn.client_abort = True

        await provider.restore_after_lesson_start_handoff(outcome="START_REFUSED")

        self.assertEqual(
            provider._interaction.state,
            google_live_module.InteractionState.LISTENING,
        )
        self.assertFalse(conn.client_abort)
        provider._open_user_audio_window.assert_awaited_once_with(
            "lesson_start_failed"
        )
        restored = json.loads(conn.websocket.sent[-1])
        self.assertEqual(restored["type"], "tts")
        self.assertEqual(restored["state"], "stop")
        self.assertTrue(restored["continue_listening"])
        self.assertEqual(restored["listen_mode"], "realtime")

    async def test_spoken_start_keeps_manual_handoff_while_pull_task_is_pending(self):
        conn = _Conn()

        class _SchedulingHandler(_FuncHandler):
            async def handle_llm_function_call(self, probe_conn, payload):
                probe_conn.lesson_pull_task = asyncio.create_task(asyncio.sleep(60))
                probe_conn.lesson_pull_task_origin = "spoken_start"
                probe_conn.lesson_pull_task_handoff_token = (
                    probe_conn.lesson_start_handoff_token()
                )
                return await super().handle_llm_function_call(probe_conn, payload)

        conn.func_handler = _SchedulingHandler()
        provider = self.make_provider(conn)
        provider._client = _Client()
        provider._bridge = _Bridge()
        provider._bridge.stop_output_for_lesson = AsyncMock()
        provider._open_user_audio_window = AsyncMock()
        conn.voice_provider = provider
        conn.is_realtime_busy = lambda: False
        original_product_tool_names = google_live_module.product_tool_names
        google_live_module.product_tool_names = lambda _conn: ["start_lesson"]
        try:
            handled = await provider._dispatch_lesson_start_intent("bắt đầu bài học")
        finally:
            google_live_module.product_tool_names = original_product_tool_names

        self.assertTrue(handled)
        self.assertTrue(conn.lesson_start_handoff_active())
        provider._open_user_audio_window.assert_not_awaited()
        conn.lesson_pull_task.cancel()
        await asyncio.gather(conn.lesson_pull_task, return_exceptions=True)

    async def test_spoken_start_does_not_release_handoff_when_pull_finishes_during_dispatch(self):
        conn = _Conn()

        class _FastSchedulingHandler(_FuncHandler):
            async def handle_llm_function_call(self, probe_conn, payload):
                probe_conn.lesson_pull_task = asyncio.create_task(asyncio.sleep(0))
                probe_conn.lesson_pull_task_origin = "spoken_start"
                probe_conn.lesson_pull_task_handoff_token = (
                    probe_conn.lesson_start_handoff_token()
                )
                await probe_conn.lesson_pull_task
                return await super().handle_llm_function_call(probe_conn, payload)

        conn.func_handler = _FastSchedulingHandler()
        provider = self.make_provider(conn)
        provider._client = _Client()
        provider._bridge = _Bridge()
        provider._bridge.stop_output_for_lesson = AsyncMock()
        conn.voice_provider = provider
        conn.is_realtime_busy = lambda: False
        original_product_tool_names = google_live_module.product_tool_names
        google_live_module.product_tool_names = lambda _conn: ["start_lesson"]
        try:
            handled = await provider._dispatch_lesson_start_intent("bắt đầu bài học")
        finally:
            google_live_module.product_tool_names = original_product_tool_names

        self.assertTrue(handled)
        self.assertEqual(conn.handoff_releases, [])
        self.assertTrue(conn.lesson_start_handoff_active())

    async def test_stale_parent_cleanup_does_not_fall_back_to_another_tasks_lease(self):
        conn = _Conn()
        provider = self.make_provider(conn)
        stale_lease = conn.begin_lesson_start_handoff(reason="spoken_start")
        await conn.release_lesson_start_handoff(
            stale_lease,
            outcome="lesson_started",
            restore_conversation=False,
        )
        concurrent_lease = conn.begin_lesson_start_handoff(reason="protected_nudge")
        provider._lesson_start_handoff_token = concurrent_lease
        conn.lesson_start_handoff_token = lambda: None

        released = await provider._release_lesson_start_handoff(
            outcome="stale_parent_cleanup",
            restore_conversation=True,
        )

        self.assertFalse(released)
        self.assertIn(concurrent_lease, conn._lesson_start_handoff_holders)

    async def test_concurrent_spoken_dispatch_releases_coalesced_holder_without_task_ownership(self):
        conn = _Conn()
        both_dispatching = asyncio.Event()
        dispatch_count = 0

        class _ConcurrentSchedulingHandler(_FuncHandler):
            async def handle_llm_function_call(self, probe_conn, payload):
                nonlocal dispatch_count
                dispatch_count += 1
                if dispatch_count == 2:
                    both_dispatching.set()
                await both_dispatching.wait()
                task = getattr(probe_conn, "lesson_pull_task", None)
                if task is None or task.done():
                    probe_conn.lesson_pull_task = asyncio.create_task(asyncio.sleep(60))
                    probe_conn.lesson_pull_task_origin = "spoken_start"
                    probe_conn.lesson_pull_task_handoff_token = (
                        probe_conn.lesson_start_handoff_token()
                    )
                return await super().handle_llm_function_call(probe_conn, payload)

        conn.func_handler = _ConcurrentSchedulingHandler()
        provider = self.make_provider(conn)
        provider._client = _Client()
        provider._bridge = _Bridge()
        provider._bridge.stop_output_for_lesson = AsyncMock()
        conn.voice_provider = provider
        conn.is_realtime_busy = lambda: False
        original_product_tool_names = google_live_module.product_tool_names
        google_live_module.product_tool_names = lambda _conn: ["start_lesson"]
        try:
            results = await asyncio.gather(
                provider._dispatch_lesson_start_intent("bắt đầu bài học"),
                provider._dispatch_lesson_start_intent("bắt đầu bài học"),
            )
            active_holders = len(conn._lesson_start_handoff_holders)
        finally:
            google_live_module.product_tool_names = original_product_tool_names
            conn.lesson_pull_task.cancel()
            await asyncio.gather(conn.lesson_pull_task, return_exceptions=True)

        self.assertEqual(results, [True, True])
        self.assertEqual(active_holders, 1)

    async def test_sample_fallback_reserve_survives_google_live_post_dispatch_cleanup(self):
        conn = _Conn()
        conn.loop = asyncio.get_running_loop()
        conn.lesson_runtime = None
        conn.lesson_start_status = None
        conn.features = {"lesson": True, "renderer": "teebot-lesson-renderer.v5"}
        conn.config["lesson"] = {
            "runtime_enabled": True,
            "sample_lesson": True,
            "rollout_device_allowlist": [conn.device_id],
        }
        sample_saw_active = []

        async def pull_without_assignment():
            primary_lease = conn.lesson_start_handoff_token()
            conn.lesson_start_status = {
                "code": "NO_CURRENT_ASSIGNMENT",
                "message": "Robot chưa có bài học nào được giao.",
            }
            await conn.release_lesson_start_handoff(
                primary_lease,
                outcome="NO_CURRENT_ASSIGNMENT",
                restore_conversation=True,
            )
            return None

        async def sample_fallback(_conn):
            sample_saw_active.append(conn.lesson_start_handoff_active())
            reserve_lease = conn.lesson_start_handoff_token()
            await conn.release_lesson_start_handoff(
                reserve_lease,
                outcome="lesson_started",
                restore_conversation=False,
            )
            return object()

        conn._lesson_runtime_enabled = lambda: True
        conn._sample_lesson_enabled = lambda: True
        conn._lesson_pull_on_connect = pull_without_assignment

        class _RealStartLessonHandler(_FuncHandler):
            async def handle_llm_function_call(self, probe_conn, payload):
                self.calls.append((probe_conn, payload))
                return start_lesson_module.start_lesson(probe_conn)

        conn.func_handler = _RealStartLessonHandler()
        provider = self.make_provider(conn)
        provider._client = _Client()
        provider._bridge = _Bridge()
        provider._bridge.stop_output_for_lesson = AsyncMock()
        conn.voice_provider = provider
        conn.is_realtime_busy = lambda: False
        original_product_tool_names = google_live_module.product_tool_names
        google_live_module.product_tool_names = lambda _conn: ["start_lesson"]
        try:
            with patch("core.lesson.sample.start_sample_lesson", sample_fallback):
                handled = await provider._dispatch_lesson_start_intent(
                    "bắt đầu bài học"
                )
                for _ in range(20):
                    if sample_saw_active:
                        break
                    await asyncio.sleep(0)
        finally:
            google_live_module.product_tool_names = original_product_tool_names

        self.assertTrue(handled)
        self.assertEqual(sample_saw_active, [True])
        self.assertFalse(conn.lesson_start_handoff_active())

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

    async def test_text_and_model_audio_refresh_idle_activity(self):
        conn = _Conn()
        conn.session_mode = SessionMode.CONVERSATION
        provider = self.make_provider(conn)
        provider._client = _Client()
        provider._bridge = _Bridge()
        async def _enter_dormant_mode(**_kwargs):
            return None

        conn.enter_dormant_mode = _enter_dormant_mode
        conn.last_live_activity_at = time.monotonic() - 10

        self.assertTrue(await provider.handle_text_message('{"type":"text","text":"hi"}'))
        self.assertFalse(await provider._close_if_idle_once(1))

        conn.last_live_activity_at = time.monotonic() - 10
        await provider._handle_live_event({"type": "audio_start"})

        self.assertFalse(await provider._close_if_idle_once(1))

    async def test_model_audio_refreshes_websocket_connection_activity(self):
        conn = _Conn()
        conn.last_activity_time = 1000.0
        provider = self.make_provider(conn)
        original_time = google_live_module.time.time
        try:
            google_live_module.time.time = lambda: 123.456
            await provider._handle_live_event({"type": "audio_start"})
        finally:
            google_live_module.time.time = original_time

        self.assertEqual(conn.last_activity_time, 123456.0)

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

        # Ready client short-circuits open; clear it to exercise the failure path.
        provider._client = None
        provider._bridge = None
        provider._open_live_session = _open_failure
        provider._close_live_resources = (
            lambda *, preserve_live_prewarm=False: _false(None)
        )
        self.assertFalse(await provider._ensure_live_open_for_audio())

    async def test_idle_loop_aec_resumption_and_reconnect_accounting_edges(self):
        conn = _Conn()
        provider = self.make_provider(conn)

        conn.config["google_live"]["idle_timeout_sec"] = "bad"
        # Malformed idle falls back to prewarm-aware default (15 min keeps Live hot).
        self.assertEqual(provider._idle_timeout_sec(), 900.0)
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
        self.assertTrue(await provider._dispatch_lesson_start_intent("start"))
        conn.lesson_runtime = None
        provider._suppress_start_lesson_tool_call_until = 0.0
        conn.func_handler = None
        self.assertFalse(await provider._dispatch_lesson_start_intent("start"))
        conn.func_handler = _FuncHandler(fail=True)
        self.assertFalse(await provider._dispatch_lesson_start_intent("start"))

        provider._client = None
        provider._has_session_orchestrator = lambda: False
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
        # Safety policy caps oversized private values at 6.0s (preferred default is 5.0).
        self.assertEqual(live_config["waiting_model_timeout_sec"], 6.0)
        # Floor 1.0s / cap 2.5s keeps mid-sentence cuts out while allowing real barge-in later.
        self.assertEqual(live_config["interruption_min_output_age_sec"], 2.0)
        self.assertEqual(live_config["barge_in_transcript_min_output_age_sec"], 2.0)

        provider._get_live_config = lambda: {"wake_audio_allow_window_sec": "bad"}
        self.assertEqual(provider._get_wake_audio_allow_window_sec(), 900.0)
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
