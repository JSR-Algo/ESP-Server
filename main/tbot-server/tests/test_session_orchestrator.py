import json
import time
import unittest

# Reuse the connection-routing suite's import stubs so this focused unit test does
# not require optional MCP/server integrations just to import ConnectionHandler.
import tests.test_connection_voice_provider_routing as routing  # noqa: F401

from core.connection import ConnectionHandler, SessionMode
from core.voice.session_provider.google_live import GoogleLiveProvider
from core.voice.live_admission import (
    AdmissionDecision,
    AdmissionReason,
    InMemoryLiveAdmissionStore,
    LiveAdmissionGate,
    RedisLiveStateStore,
)


class _Logger:
    def bind(self, **kwargs):
        return self

    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None

    def error(self, *args, **kwargs):
        return None

    def debug(self, *args, **kwargs):
        return None


class _VoiceProvider:
    def __init__(self):
        self.started = 0
        self.closed_live = 0
        self.audio = []

    async def start_session(self):
        self.started += 1

    async def _close_live_resources(self):
        self.closed_live += 1

    async def handle_audio_bytes(self, payload):
        self.audio.append(payload)
        return True

    async def handle_text_message(self, message):
        return False


class _RecordingWS:
    def __init__(self):
        self.sent = []
        self.modes_at_send = []
        self.mode_getter = None

    async def send(self, payload):
        self.sent.append(payload)
        if self.mode_getter is not None:
            self.modes_at_send.append(self.mode_getter())


class _ConsentClient:
    async def ensure_voice_allowed(self, _conn):
        return True


class _ProviderConn:
    def __init__(self, gate=None):
        self.config = {"google_live": {"idle_timeout_sec": 30}}
        self.logger = _Logger()
        self.session_id = "session-1"
        self.device_id = "device-1"
        self.household_id = "house-1"
        self.websocket = None
        self.sent = []
        self.client_abort = False
        self.client_is_speaking = False
        self.sample_rate = 24000
        self.voice_provider = None
        self.func_handler = None
        self.session_mode = SessionMode.DORMANT
        self.audio_channel_owner = SessionMode.DORMANT
        self.last_live_activity_at = None
        self.google_live_session_started_at = None
        self.google_live_audio_out_started_at = None
        self.google_live_turn_started_at = None
        self.google_live_session_resumption_handle = None
        self.voice_consent_client = _ConsentClient()
        self.live_admission_gate = gate or LiveAdmissionGate(
            InMemoryLiveAdmissionStore(), daily_device_minutes=10
        )

    def _set_session_mode(self, mode, *, reason=""):
        self.session_mode = mode
        self.audio_channel_owner = mode

    async def enter_dormant_mode(self, *, reason=""):
        self._set_session_mode(SessionMode.DORMANT, reason=reason)

    def clear_queues(self):
        return None

    def clearSpeakStatus(self):
        self.client_is_speaking = False


class _Bridge:
    def __init__(self):
        self.forwarded = []

    async def decode_input_audio_async(self, audio):
        return audio

    async def forward_decoded_input_audio(self, decoded):
        self.forwarded.append(decoded)

    def input_rms(self, _pcm):
        return 0

    def allow_model_output(self):
        return None

    async def close(self):
        return None


class _FallbackProvider:
    def __init__(self):
        self.started = 0
        self.audio = []

    async def start_session(self):
        self.started += 1

    async def handle_audio_bytes(self, audio):
        self.audio.append(audio)
        return True

    async def handle_text_message(self, _message):
        return False

    async def interrupt(self):
        return None

    async def close(self):
        return None


class _DecisionGate:
    def __init__(self, result):
        self.result = result

    def admit(self, *_args, **_kwargs):
        return self.result

    def record_live_usage(self, *_args, **_kwargs):
        return None

class _FakeAsyncRedis:
    def __init__(self):
        self.values = {}
        self.expirations = {}
        self.zsets = {}

    async def set(self, key, value, ex=None):
        self.values[key] = str(value)
        if ex is not None:
            self.expirations[key] = int(ex)
        return True

    async def get(self, key):
        return self.values.get(key)

    async def incrbyfloat(self, key, amount):
        self.values[key] = str(float(self.values.get(key, 0.0)) + float(amount))
        return float(self.values[key])

    async def expire(self, key, seconds):
        self.expirations[key] = int(seconds)
        return True

    async def zadd(self, key, mapping):
        bucket = self.zsets.setdefault(key, {})
        bucket.update(mapping)
        return len(mapping)

    async def zremrangebyscore(self, key, minimum, maximum):
        bucket = self.zsets.setdefault(key, {})
        low = float("-inf") if minimum == "-inf" else float(minimum)
        high = float("inf") if maximum == "+inf" else float(maximum)
        removed = [member for member, score in bucket.items() if low <= float(score) <= high]
        for member in removed:
            bucket.pop(member, None)
        return len(removed)

    async def zcard(self, key):
        return len(self.zsets.setdefault(key, {}))


def _conn():
    conn = ConnectionHandler(
        {
            "exit_commands": [],
            "close_connection_no_voice_time": 120,
            "live_admission": {"idle_timeout_sec": 30, "daily_live_minutes": 5},
        },
        None,
        None,
        None,
        None,
        None,
    )
    conn.logger = _Logger()
    return conn


class SessionOrchestratorModeTest(unittest.IsolatedAsyncioTestCase):
    async def test_connection_starts_dormant_and_lazily_opens_live_on_audio(self):
        conn = _conn()
        conn.voice_provider = _VoiceProvider()

        self.assertEqual(conn.session_mode, SessionMode.DORMANT)

        handled = await conn._route_audio_message(b"opus-frame")

        self.assertTrue(handled)
        self.assertEqual(conn.session_mode, SessionMode.CONVERSATION)
        self.assertEqual(conn.voice_provider.started, 1)
        self.assertEqual(conn.voice_provider.audio, [b"opus-frame"])

    async def test_idle_timeout_closes_live_and_returns_to_dormant(self):
        conn = _conn()
        conn.voice_provider = _VoiceProvider()
        conn.session_mode = SessionMode.CONVERSATION
        conn.google_live_session_started_at = time.monotonic() - 10
        conn.last_live_activity_at = time.monotonic() - 31

        closed = await conn.close_live_if_idle(now=time.monotonic())

        self.assertTrue(closed)
        self.assertEqual(conn.session_mode, SessionMode.DORMANT)
        self.assertEqual(conn.voice_provider.closed_live, 1)

    async def test_enter_lesson_keeps_live_session_open_and_owns_audio_channel(self):
        conn = _conn()
        conn.voice_provider = _VoiceProvider()
        conn.session_mode = SessionMode.CONVERSATION
        conn.google_live_session_resumption_handle = "resume-1"

        await conn.enter_lesson_mode()

        self.assertEqual(conn.session_mode, SessionMode.LESSON)
        self.assertEqual(conn.voice_provider.closed_live, 0)
        self.assertEqual(conn.live_resumption_store.saved, [(conn.device_id, "resume-1")])

        handled = await conn._route_audio_message(b"lesson-audio")
        self.assertFalse(handled)
        self.assertEqual(conn.voice_provider.audio, [])


    async def test_lesson_terminal_releases_audio_channel_to_dormant(self):
        conn = _conn()
        conn.session_mode = SessionMode.LESSON
        conn.audio_channel_owner = SessionMode.LESSON

        await conn.release_lesson_mode(reason="lesson_completed")

        self.assertEqual(conn.session_mode, SessionMode.DORMANT)
        self.assertEqual(conn.audio_channel_owner, SessionMode.DORMANT)

    async def test_finish_lesson_shows_happy_face_and_returns_to_conversation(self):
        conn = _conn()
        conn.voice_provider = _VoiceProvider()
        conn.websocket = _RecordingWS()
        conn.websocket.mode_getter = lambda: conn.session_mode
        conn.session_mode = SessionMode.LESSON
        conn.audio_channel_owner = SessionMode.LESSON

        await conn.finish_lesson_mode(reason="lesson_completed")

        # A single happy-face emotion frame is pushed to the device over the WS,
        # in the proven get_emotion wire shape the firmware already renders.
        emotions = [json.loads(p) for p in conn.websocket.sent]
        self.assertEqual(len(emotions), 1)
        self.assertEqual(emotions[0]["type"], "llm")
        self.assertEqual(emotions[0]["emotion"], "happy")
        self.assertEqual(emotions[0]["text"], "🙂")
        self.assertEqual(emotions[0]["session_id"], conn.session_id)
        self.assertEqual(conn.websocket.modes_at_send, [SessionMode.CONVERSATION])
        # ...then the robot returns to NORMAL CONVERSATION (Live reopened).
        self.assertEqual(conn.session_mode, SessionMode.CONVERSATION)
        self.assertEqual(conn.voice_provider.started, 1)

    async def test_finish_lesson_can_stay_dormant_when_return_to_conversation_off(self):
        conn = _conn()
        conn.voice_provider = _VoiceProvider()
        conn.websocket = _RecordingWS()
        conn.websocket.mode_getter = lambda: conn.session_mode
        conn.config["lesson"] = {"return_to_conversation": False}
        conn.session_mode = SessionMode.LESSON
        conn.audio_channel_owner = SessionMode.LESSON

        await conn.finish_lesson_mode(reason="lesson_completed")

        # Happy face still shown regardless of the destination mode...
        emotions = [json.loads(p) for p in conn.websocket.sent]
        self.assertEqual([e["emotion"] for e in emotions], ["happy"])
        self.assertEqual(conn.websocket.modes_at_send, [SessionMode.DORMANT])
        # ...but the audio channel idles to dormant (no Live reopen / no extra cost).
        self.assertEqual(conn.session_mode, SessionMode.DORMANT)
        self.assertEqual(conn.voice_provider.started, 0)

    async def test_finish_lesson_noop_when_not_in_lesson_mode(self):
        conn = _conn()
        conn.voice_provider = _VoiceProvider()
        conn.websocket = _RecordingWS()
        conn.session_mode = SessionMode.CONVERSATION

        await conn.finish_lesson_mode(reason="lesson_completed")

        self.assertEqual(conn.websocket.sent, [])
        self.assertEqual(conn.session_mode, SessionMode.CONVERSATION)


class LiveAdmissionGateTest(unittest.TestCase):
    def test_daily_budget_exhaustion_degrades_to_tts_only(self):
        store = InMemoryLiveAdmissionStore()
        gate = LiveAdmissionGate(
            store,
            daily_device_minutes=1,
            daily_household_minutes=10,
            reconnect_window_sec=60,
            reconnect_limit=3,
        )
        store.add_usage("device-1", "house-1", 61)

        decision = gate.admit("device-1", "house-1")

        self.assertEqual(decision, AdmissionDecision.DEGRADE_TTS_ONLY)
        self.assertEqual(decision.reason, AdmissionReason.DEVICE_DAILY_BUDGET_EXHAUSTED)

    def test_household_daily_budget_exhaustion_degrades_to_tts_only(self):
        store = InMemoryLiveAdmissionStore()
        gate = LiveAdmissionGate(
            store,
            daily_device_minutes=10,
            daily_household_minutes=1,
        )
        store.add_usage("other-device", "house-1", 61)

        decision = gate.admit("device-1", "house-1")

        self.assertEqual(decision, AdmissionDecision.DEGRADE_TTS_ONLY)
        self.assertEqual(decision.reason, AdmissionReason.HOUSEHOLD_DAILY_BUDGET_EXHAUSTED)

    def test_reconnect_storm_is_rate_limited_before_live_open(self):
        store = InMemoryLiveAdmissionStore()
        gate = LiveAdmissionGate(store, reconnect_window_sec=60, reconnect_limit=2)

        self.assertEqual(gate.admit("device-1", "house-1"), AdmissionDecision.ALLOW_LIVE)
        store.record_reconnect("device-1", now=100.0)
        store.record_reconnect("device-1", now=101.0)

        decision = gate.admit("device-1", "house-1", now=102.0)

        self.assertEqual(decision, AdmissionDecision.FRIENDLY_BREAK)
        self.assertEqual(decision.reason, AdmissionReason.RECONNECT_STORM)

class RedisLiveStateStoreTest(unittest.IsolatedAsyncioTestCase):
    async def test_resumption_handle_round_trips_by_device_id(self):
        redis = _FakeAsyncRedis()
        store = RedisLiveStateStore(redis, namespace="test", resumption_ttl_sec=60)

        await store.save("device-1", "resume-handle-1")

        self.assertEqual(await store.load("device-1"), "resume-handle-1")
        self.assertEqual(redis.values["tbot:live:test:session:device-1:resumption"], "resume-handle-1")
        self.assertEqual(redis.expirations["tbot:live:test:session:device-1:resumption"], 60)

    async def test_live_budget_is_read_and_written_from_redis(self):
        redis = _FakeAsyncRedis()
        store = RedisLiveStateStore(redis, namespace="test", day_key="2026-06-17")
        gate = LiveAdmissionGate(store, daily_device_minutes=1, daily_household_minutes=10)

        await gate.record_live_usage_async("device-1", "house-1", 61)
        decision = await gate.admit_async("device-1", "house-1")

        self.assertEqual(decision, AdmissionDecision.DEGRADE_TTS_ONLY)
        self.assertEqual(decision.reason, AdmissionReason.DEVICE_DAILY_BUDGET_EXHAUSTED)
        self.assertEqual(redis.values["tbot:live:test:budget:device:2026-06-17:device-1"], "61.0")

    async def test_reconnect_storm_window_uses_redis_sorted_set(self):
        redis = _FakeAsyncRedis()
        store = RedisLiveStateStore(redis, namespace="test")
        gate = LiveAdmissionGate(store, reconnect_window_sec=60, reconnect_limit=2)

        await store.record_reconnect_async("device-1", now=100.0)
        await store.record_reconnect_async("device-1", now=101.0)

        decision = await gate.admit_async("device-1", "house-1", now=102.0)

        self.assertEqual(decision, AdmissionDecision.FRIENDLY_BREAK)
        self.assertEqual(decision.reason, AdmissionReason.RECONNECT_STORM)


class GoogleLiveProviderOrchestratorTest(unittest.IsolatedAsyncioTestCase):
    async def test_reconnect_to_new_replica_loads_redis_resumption_handle(self):
        redis = _FakeAsyncRedis()
        store = RedisLiveStateStore(redis, namespace="test")
        await store.save("device-1", "resume-from-replica-a")
        conn = _ProviderConn()
        conn.live_resumption_store = store
        provider = GoogleLiveProvider(conn)

        await provider._restore_session_resumption_handle()

        self.assertEqual(conn.google_live_session_resumption_handle, "resume-from-replica-a")

    async def test_orchestrated_start_is_dormant_until_first_audio(self):
        conn = _ProviderConn()
        provider = GoogleLiveProvider(conn)
        opened = 0
        bridge = _Bridge()

        async def open_live_session():
            nonlocal opened
            opened += 1
            provider._client = object()
            provider._bridge = bridge
            conn._set_session_mode(SessionMode.CONVERSATION, reason="test_open")

        async def ensure_func_handler():
            return None

        provider._ensure_func_handler = ensure_func_handler
        provider._open_live_session = open_live_session

        await provider.start_session()

        self.assertEqual(opened, 0)
        self.assertEqual(conn.session_mode, SessionMode.DORMANT)

        handled = await provider.handle_audio_bytes(b"opus-frame")

        self.assertTrue(handled)
        self.assertEqual(opened, 1)
        self.assertEqual(conn.session_mode, SessionMode.CONVERSATION)
        self.assertEqual(bridge.forwarded, [b"opus-frame"])

    async def test_over_budget_degrades_to_classic_without_opening_live(self):
        gate = _DecisionGate(
            type("Decision", (), {
                "decision": AdmissionDecision.DEGRADE_TTS_ONLY,
                "reason": AdmissionReason.DEVICE_DAILY_BUDGET_EXHAUSTED,
            })()
        )
        fallback = _FallbackProvider()
        conn = _ProviderConn(gate=gate)
        provider = GoogleLiveProvider(conn, classic_provider_factory=lambda _conn: fallback)
        opened = 0

        async def open_live_session():
            nonlocal opened
            opened += 1

        provider._open_live_session = open_live_session

        await provider.start_session()
        handled = await provider.handle_audio_bytes(b"opus-frame")

        self.assertTrue(handled)
        self.assertEqual(opened, 0)
        self.assertEqual(fallback.started, 1)
        self.assertIs(conn.voice_provider, fallback)

    async def test_reconnect_storm_returns_friendly_break_without_opening_live(self):
        gate = _DecisionGate(
            type("Decision", (), {
                "decision": AdmissionDecision.FRIENDLY_BREAK,
                "reason": AdmissionReason.RECONNECT_STORM,
            })()
        )
        conn = _ProviderConn(gate=gate)
        provider = GoogleLiveProvider(conn)
        opened = 0

        async def open_live_session():
            nonlocal opened
            opened += 1

        provider._open_live_session = open_live_session

        await provider.start_session()
        handled = await provider.handle_audio_bytes(b"opus-frame")

        self.assertTrue(handled)
        self.assertEqual(opened, 0)
        self.assertEqual(conn.sent[-1]["status"], "live_unavailable")
        self.assertEqual(conn.sent[-1]["reason"], "reconnect_storm")
