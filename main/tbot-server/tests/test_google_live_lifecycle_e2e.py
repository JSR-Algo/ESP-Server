import asyncio
import json
from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from core.voice.google_live.client import GoogleLiveClient
from core.voice.google_live.interaction_controller import InteractionState
from core.voice.session_orchestrator import SessionMode
from core.voice.session_provider.google_live import GoogleLiveProvider

HISTORICAL_REGRESSION_NODE_IDS = (
    "tests/test_google_live_provider_edges.py::GoogleLiveProviderEdgeTest::test_model_audio_start_cancels_pending_idle_input_flush",
    "tests/test_connection_voice_provider_routing.py::ConnectionVoiceProviderRoutingTest::test_google_live_ping_routes_to_classic_heartbeat_handler",
    "tests/test_google_live_provider_edges.py::GoogleLiveProviderEdgeTest::test_lesson_start_asr_fallback_live_close_breaks_fragment_chain",
    "tests/test_connection_edges.py::ConnectionEdgeTest::test_lesson_start_handoff_uses_generation_token_and_rejects_stale_release",
    "tests/test_google_live_provider_edges.py::GoogleLiveProviderEdgeTest::test_matching_device_drain_ack_releases_prompt_wait_and_stale_ack_does_not",
    "tests/test_google_live_reconnect.py::ClassifyErrorRoutingTest::test_quota_error_logs_no_retry_and_returns_false",
    "tests/test_google_live_reconnect.py::ClassifyErrorRoutingTest::test_invalid_config_error_logs_no_retry_and_returns_false",
    "tests/test_audio_rate_controller_edges.py::test_controller_constructs_without_a_running_loop_and_rebinds_between_loops",
)

EXPECTED_PROVIDER_TASK_FIELDS = {
    "_receive_task",
    "_input_flush_task",
    "_forced_interrupt_flush_task",
    "_waiting_model_timeout_task",
    "_user_audio_window_task",
    "_lesson_child_transcript_timeout_task",
    "_start_lesson_asr_fallback_task",
    "_func_handler_bootstrap_task",
    "_live_prewarm_task",
    "_wake_greeting_task",
    "_proactive_reconnect_task",
    "_idle_close_task",
    "_session_resumption_persist_tasks",
}


@dataclass
class LifecycleResult:
    receive_loop_max_active: int = 0
    live_session_max_active: int = 0
    replayed_transport_audio: list[bytes] = field(default_factory=list)
    replay_count: int = 0
    replacement_device_audio: list[bytes] = field(default_factory=list)
    response_ids: list[int] = field(default_factory=list)
    text_response_id: int = -1
    session_generations: list[int] = field(default_factory=list)
    stale_state_before: str = ""
    stale_state_after: str = ""
    replacement_transcripts: list[str] = field(default_factory=list)
    final_interaction_state: str = ""
    pending_owned_tasks: tuple[str, ...] = ()
    owned_task_fields: tuple[str, ...] = ()


class _Logger:
    def __init__(self):
        self.messages = []

    def bind(self, **_kwargs):
        return self

    def _record(self, level, *args, **kwargs):
        self.messages.append((level, args, kwargs))

    def debug(self, *args, **kwargs):
        self._record("debug", *args, **kwargs)

    def info(self, *args, **kwargs):
        self._record("info", *args, **kwargs)

    def warning(self, *args, **kwargs):
        self._record("warning", *args, **kwargs)

    def error(self, *args, **kwargs):
        self._record("error", *args, **kwargs)


class _WebSocket:
    def __init__(self):
        self.sent = []

    async def send(self, payload):
        self.sent.append(payload)


class _FunctionHandler:
    def get_functions(self):
        return []


class _Connection:
    def __init__(self):
        hello = {"type": "hello", "audio_params": {"sample_rate": 24000}}
        self.welcome_msg = hello
        self.sample_rate = hello["audio_params"]["sample_rate"]
        self.input_sample_rate = 16000
        self.config = {
            "voice_mode": {"type": "google_live"},
            "google_live": {
                "api_key": "test-key",
                "model": "gemini-live-test",
                "input_sample_rate": self.input_sample_rate,
                "output_sample_rate": self.sample_rate,
                "interrupt_min_capture_ms": 5000,
                "interrupt_speech_tail_ms": 5000,
                "interrupt_max_capture_ms": 5000,
                "model_output_unblock_timeout_sec": 0,
                "session_resumption_enabled": False,
                "reconnect": {
                    "enabled": True,
                    "max_retries": 1,
                    "backoff_ms": 0,
                },
            },
            "prompt": "test system prompt",
            "tts_audio_send_delay": -1,
        }
        self.logger = _Logger()
        self.websocket = _WebSocket()
        self.func_handler = _FunctionHandler()
        self.voice_provider = None
        self.session_id = "lifecycle-session"
        self.device_id = "device-1"
        self.household_id = "household-1"
        self.client_abort = False
        self.client_is_speaking = False
        self.google_live_audio_out_started_at = None
        self.google_live_audible_output_until = 0.0
        self.google_live_echo_suppress_until = 0.0
        self.google_live_session_started_at = None
        self.google_live_turn_started_at = None
        self.google_live_session_resumption_handle = None
        self.sentence_id = "sentence-1"
        self.close_after_chat = False
        self.conn_from_mqtt_gateway = False
        self.last_activity_time = 0.0

    def clear_queues(self):
        return None

    def clearSpeakStatus(self):  # noqa: N802 - production connection API
        self.client_is_speaking = False

    def _set_session_mode(self, mode, *, reason):
        self.session_mode = mode


class _TimeoutRecoveryRuntime:
    state = "RUNNING"
    _step_passive = False
    _step_completed = False

    def __init__(self, conn):
        self.conn = conn
        self.timeout_reasons = []
        self.first_timeout_seen = asyncio.Event()

    def conversation_tool_path_active(self):
        return True

    async def conversation_live_interruption(self, reason):
        self.timeout_reasons.append(reason)
        if len(self.timeout_reasons) == 1:
            self.conn.config["google_live"]["recv_timeout_sec"] = None
            self.first_timeout_seen.set()
        return SimpleNamespace(
            accepted=len(self.timeout_reasons) == 1,
            code="RECONNECT_ONCE",
            window_id="timeout-window",
            reconnect_allowed=True,
            prompt="",
        )


class _BlockingResumptionStore:
    def __init__(self):
        self.save_started = asyncio.Event()
        self.release_save = asyncio.Event()
        self.save_cancelled = asyncio.Event()
        self.saved = []

    async def save(self, device_id, handle):
        self.save_started.set()
        try:
            await self.release_save.wait()
        except asyncio.CancelledError:
            self.save_cancelled.set()
            raise
        self.saved.append((device_id, handle))


class _ImmediateResumptionStore:
    def __init__(self):
        self.saved = []

    async def save(self, device_id, handle):
        self.saved.append((device_id, handle))


class _LifecycleCounters:
    def __init__(self):
        self.receive_loop_active = 0
        self.receive_loop_max_active = 0
        self.live_session_active = 0
        self.live_session_max_active = 0

    def receive_enter(self):
        self.receive_loop_active += 1
        self.receive_loop_max_active = max(
            self.receive_loop_max_active, self.receive_loop_active
        )

    def receive_exit(self):
        self.receive_loop_active -= 1

    def session_enter(self):
        self.live_session_active += 1
        self.live_session_max_active = max(
            self.live_session_max_active, self.live_session_active
        )

    def session_exit(self):
        self.live_session_active -= 1


_TRANSPORT_CLOSE = object()


class _FakeLiveSession:
    def __init__(self, counters):
        self.counters = counters
        self.incoming = asyncio.Queue()
        self.realtime_inputs = []
        self.audio_frames = []
        self.client_content_inputs = []
        self.closed = False

    async def send_realtime_input(self, **kwargs):
        self.realtime_inputs.append(kwargs)
        audio = kwargs.get("audio")
        if isinstance(audio, dict) and isinstance(audio.get("data"), bytes):
            self.audio_frames.append(audio["data"])

    async def send_client_content(self, **kwargs):
        self.client_content_inputs.append(kwargs)

    async def receive(self):
        self.counters.receive_enter()
        try:
            while True:
                if self.closed and self.incoming.empty():
                    return
                item = await self.incoming.get()
                if item is _TRANSPORT_CLOSE:
                    self.closed = True
                    return
                yield item
        finally:
            self.counters.receive_exit()

    async def emit(self, message):
        await self.incoming.put(message)

    async def close_transport(self):
        await self.incoming.put(_TRANSPORT_CLOSE)

    def shutdown(self):
        self.closed = True
        self.incoming.put_nowait(_TRANSPORT_CLOSE)


class _FakeLiveContext:
    def __init__(self, transport, session):
        self.transport = transport
        self.session = session
        self.entered = False

    async def __aenter__(self):
        self.entered = True
        self.transport.counters.session_enter()
        return self.session

    async def __aexit__(self, _exc_type, _exc, _tb):
        if self.entered:
            self.entered = False
            self.transport.counters.session_exit()
        self.session.shutdown()


class _FakeSdkClient:
    def __init__(self, transport):
        self.transport = transport
        self.aio = SimpleNamespace(live=SimpleNamespace(connect=self._connect))

    def _connect(self, **kwargs):
        session = _FakeLiveSession(self.transport.counters)
        self.transport.sessions.append(session)
        self.transport.connect_configs.append(kwargs)
        return _FakeLiveContext(self.transport, session)


class _FakeGenaiModule:
    types = None

    def __init__(self, transport):
        self.transport = transport

    def Client(self, **_kwargs):  # noqa: N802 - google.genai module API
        return _FakeSdkClient(self.transport)


class _FakeTransport:
    def __init__(self):
        self.counters = _LifecycleCounters()
        self.clients = []
        self.sessions = []
        self.connect_configs = []
        self.module = _FakeGenaiModule(self)


class _FrameMarker(bytes):
    def __new__(cls, value):
        return super().__new__(cls, value + (b"\x00" if len(value) % 2 else b""))


class _InProcessGoogleLiveClient(GoogleLiveClient):
    def __init__(self, config, logger, genai_module):
        super().__init__(config, logger)
        self._genai_module = genai_module
        self.observed_events = []

    def _import_genai_module(self):
        return self._genai_module

    async def receive_events(self):
        async for event in super().receive_events():
            self.observed_events.append(event)
            yield event


def _server_message(*, user_text=None, audio=None, turn_complete=False):
    inline_parts = []
    if audio is not None:
        inline_parts.append(
            SimpleNamespace(
                inline_data=SimpleNamespace(
                    data=audio,
                    mime_type=None,
                )
            )
        )
    return SimpleNamespace(
        server_content=SimpleNamespace(
            input_transcription=(
                SimpleNamespace(text=user_text) if user_text is not None else None
            ),
            output_transcription=None,
            interrupted=False,
            turn_complete=turn_complete,
            model_turn=SimpleNamespace(parts=inline_parts),
        )
    )


async def _wait_until(predicate, *, timeout=1.0):
    async def _poll():
        while not predicate():
            await asyncio.sleep(0)

    await asyncio.wait_for(_poll(), timeout=timeout)


def _successful_replay_count(logger):
    return sum(
        1
        for _level, args, _kwargs in logger.messages
        if args and "replayed_interrupt_audio" in str(args[0])
    )


def _pending_provider_tasks(provider):
    pending = []
    for name, value in vars(provider).items():
        if name.endswith("_task"):
            if value is not None and not value.done():
                pending.append(name)
        elif name.endswith("_tasks"):
            pending.extend(
                f"{name}[{index}]"
                for index, task in enumerate(value)
                if not task.done()
            )
    return tuple(pending)


def _provider_task_fields(provider):
    return tuple(
        sorted(
            name
            for name in vars(provider)
            if name.endswith("_task") or name.endswith("_tasks")
        )
    )


def _collect_node_ids(node_ids):
    from _pytest.config import get_config
    from _pytest.main import Session

    config = get_config()
    session = None
    session_started = False
    try:
        config.parse(["-q", "-p", "no:asyncio"])
        config._do_configure()
        session = Session.from_config(config)
        config.hook.pytest_sessionstart(session=session)
        session_started = True
        return tuple(
            item.nodeid for item in session.perform_collect(node_ids, genitems=True)
        )
    finally:
        if session_started:
            config.hook.pytest_sessionfinish(session=session, exitstatus=0)
        config._ensure_unconfigure()


async def _run_lifecycle_journey():
    conn = _Connection()
    transport = _FakeTransport()
    result = LifecycleResult()

    def client_factory(config, logger):
        client = _InProcessGoogleLiveClient(config, logger, transport.module)
        transport.clients.append(client)
        return client

    provider = GoogleLiveProvider(conn, client_factory=client_factory)
    try:
        with patch.object(
            GoogleLiveProvider,
            "_ensure_required_aec_ready",
            autospec=True,
        ):
            await provider.start_session()
            await _wait_until(lambda: len(transport.sessions) == 1)
            first_session = transport.sessions[0]

            result.response_ids.append(provider.current_response_id())
            result.session_generations.append(provider._session_generation)
            assert await provider.handle_text_message(
                json.dumps({"type": "text", "text": "first user turn"})
            )
            result.text_response_id = provider.current_response_id()
            assert first_session.client_content_inputs[-1]["turn_complete"] is True

            await first_session.emit(_server_message(audio=b"old-audio"))
            await _wait_until(
                lambda: [raw for raw in conn.websocket.sent if isinstance(raw, bytes)]
                == [b"old-audio"]
            )

            await provider._begin_user_interrupt("audio_input")
            result.response_ids.append(provider.current_response_id())
            provider._buffer_pending_interrupt_audio(_FrameMarker(b"frame-1"))
            provider._buffer_pending_interrupt_audio(_FrameMarker(b"frame-2"))

            result.stale_state_before = provider._interaction.state.value
            await first_session.emit(_server_message(turn_complete=True))
            await _wait_until(lambda: provider._bridge.current_response_id() is None)
            result.stale_state_after = provider._interaction.state.value

            await first_session.emit(_server_message(audio=b"old-after-interrupt"))
            await asyncio.sleep(0)
            await first_session.close_transport()
            await _wait_until(lambda: len(transport.sessions) == 2)
            second_session = transport.sessions[1]
            result.session_generations.append(provider._session_generation)
            await asyncio.sleep(0)
            assert provider._client is not None, conn.logger.messages[-20:]
            await _wait_until(lambda: _successful_replay_count(conn.logger) == 1)
            await _wait_until(lambda: len(second_session.audio_frames) >= 1)
            result.replayed_transport_audio = list(second_session.audio_frames)
            result.replay_count = _successful_replay_count(conn.logger)

            replacement_start = len(
                [raw for raw in conn.websocket.sent if isinstance(raw, bytes)]
            )
            await second_session.emit(
                _server_message(
                    user_text="replacement user transcript",
                    audio=b"new-audio",
                    turn_complete=True,
                )
            )
            await _wait_until(
                lambda: len(
                    [raw for raw in conn.websocket.sent if isinstance(raw, bytes)]
                )
                > replacement_start
            )
            await _wait_until(
                lambda: provider._interaction.state.value == "LISTENING"
            )
            result.replacement_device_audio = [
                raw for raw in conn.websocket.sent if isinstance(raw, bytes)
            ][replacement_start:]
            result.replacement_transcripts = [
                payload["text"]
                for raw in conn.websocket.sent
                if isinstance(raw, str)
                for payload in [json.loads(raw)]
                if payload.get("type") == "stt"
                and payload.get("text") == "replacement user transcript"
            ]
            result.final_interaction_state = provider._interaction.state.value
    finally:
        await provider.close()

    await _wait_until(lambda: transport.counters.receive_loop_active == 0)
    result.receive_loop_max_active = transport.counters.receive_loop_max_active
    result.live_session_max_active = transport.counters.live_session_max_active
    result.pending_owned_tasks = _pending_provider_tasks(provider)
    result.owned_task_fields = _provider_task_fields(provider)
    assert transport.counters.receive_loop_active == 0
    assert transport.counters.live_session_active == 0
    return result


async def _run_receive_timeout_recovery():
    conn = _Connection()
    conn.session_mode = SessionMode.LESSON
    conn.lesson_runtime = _TimeoutRecoveryRuntime(conn)
    conn.config["google_live"].update(
        {
            "prewarm_live_on_connect": False,
            "recv_timeout_sec": 0.01,
        }
    )
    transport = _FakeTransport()

    def client_factory(config, logger):
        client = _InProcessGoogleLiveClient(config, logger, transport.module)
        transport.clients.append(client)
        return client

    provider = GoogleLiveProvider(conn, client_factory=client_factory)
    try:
        with patch.object(
            GoogleLiveProvider,
            "_ensure_required_aec_ready",
            autospec=True,
        ):
            await provider._open_live_session()
            await asyncio.wait_for(
                conn.lesson_runtime.first_timeout_seen.wait(), timeout=1.0
            )
            await _wait_until(lambda: len(transport.sessions) == 2)
            replacement_session = transport.sessions[1]
            conn.google_live_lesson_prompt_output_allowed = True
            await replacement_session.emit(
                _server_message(
                    user_text="timeout replacement transcript",
                    audio=b"timeout-new-audio",
                    turn_complete=True,
                )
            )
            await _wait_until(
                lambda: b"timeout-new-audio" in conn.websocket.sent
            )
    finally:
        await provider.close()

    await _wait_until(lambda: transport.counters.receive_loop_active == 0)
    return {
        "typed_timeout_events": [
            event
            for event in transport.clients[0].observed_events
            if event == {"type": "receive_timeout"}
        ],
        "timeout_reasons": conn.lesson_runtime.timeout_reasons,
        "session_count": len(transport.sessions),
        "replacement_transcripts": [
            event["text"]
            for event in transport.clients[1].observed_events
            if event.get("type") == "transcript" and event.get("source") == "user"
        ],
        "device_audio": [
            raw for raw in conn.websocket.sent if isinstance(raw, bytes)
        ],
        "pending_owned_tasks": _pending_provider_tasks(provider),
        "owned_task_fields": _provider_task_fields(provider),
    }


def test_historical_regression_node_ids_collect_exactly():
    assert len(HISTORICAL_REGRESSION_NODE_IDS) == 8
    assert len(set(HISTORICAL_REGRESSION_NODE_IDS)) == 8
    assert _collect_node_ids(HISTORICAL_REGRESSION_NODE_IDS) == (
        HISTORICAL_REGRESSION_NODE_IDS
    )


@pytest.mark.asyncio
async def test_close_cancels_owned_session_resumption_persistence():
    conn = _Connection()
    conn.config["google_live"]["session_resumption_enabled"] = True
    conn.live_resumption_store = _BlockingResumptionStore()
    provider = GoogleLiveProvider(conn)

    assert provider._handle_session_resumption_update(
        {"resumable": True, "handle": "persist-handle"}
    )
    await asyncio.wait_for(conn.live_resumption_store.save_started.wait(), timeout=1.0)
    assert _pending_provider_tasks(provider) == (
        "_session_resumption_persist_tasks[0]",
    )

    await provider.close()

    assert conn.live_resumption_store.save_cancelled.is_set()
    assert _pending_provider_tasks(provider) == ()
    assert provider._session_resumption_persist_tasks == set()
    conn.live_resumption_store.release_save.set()
    await asyncio.sleep(0)
    assert conn.live_resumption_store.saved == []


@pytest.mark.asyncio
async def test_completed_session_resumption_persistence_releases_ownership():
    conn = _Connection()
    conn.config["google_live"]["session_resumption_enabled"] = True
    conn.live_resumption_store = _ImmediateResumptionStore()
    provider = GoogleLiveProvider(conn)

    assert provider._handle_session_resumption_update(
        {"resumable": True, "handle": "persisted-handle"}
    )
    await _wait_until(lambda: not provider._session_resumption_persist_tasks)

    assert conn.live_resumption_store.saved == [
        ("device-1", "persisted-handle"),
    ]
    assert _pending_provider_tasks(provider) == ()
    await provider.close()


@pytest.mark.asyncio
async def test_synthetic_stream_end_keeps_stale_generation_out_of_provider_state():
    conn = _Connection()
    transport = _FakeTransport()
    client = _InProcessGoogleLiveClient(
        conn.config["google_live"], conn.logger, transport.module
    )
    client.set_response_generation_getter(lambda: 0)
    await client.connect()
    session = transport.sessions[0]
    await session.emit(_server_message(audio=b"stale-open-audio"))
    await session.close_transport()
    events = client.receive_events().__aiter__()

    assert (await events.__anext__())["type"] == "audio_start"
    assert (await events.__anext__())["type"] == "audio_chunk"

    provider = GoogleLiveProvider(conn)
    provider._response_generation = 1
    provider._cancelled_response_ids.add(0)
    provider._interaction.begin_interrupt(response_id=1, turn_id=1)
    conn.client_abort = True
    provider._post_reply_hold_until = 0.0
    provider._user_audio_allowed_until = 0.0

    synthetic_audio_end = await events.__anext__()
    assert synthetic_audio_end == {"type": "audio_end", "response_generation": 0}
    await provider._handle_live_event(synthetic_audio_end)

    assert provider._interaction.state == InteractionState.INTERRUPTING
    assert conn.client_abort is True
    assert provider._post_reply_hold_until == 0.0
    assert provider._user_audio_allowed_until == 0.0
    await client.close()


@pytest.mark.asyncio
async def test_stale_audio_chunks_do_not_log_at_info_on_the_hot_path():
    conn = _Connection()
    provider = GoogleLiveProvider(conn)
    provider._response_generation = 2
    provider._cancelled_response_ids.add(1)

    for _ in range(2):
        await provider._handle_live_event(
            {
                "type": "audio_chunk",
                "audio": b"old",
                "response_generation": 1,
            }
        )

    stale_logs = [
        level
        for level, args, _kwargs in conn.logger.messages
        if args and "stale_provider_event_ignored" in str(args[0])
    ]
    assert stale_logs == ["debug", "debug"]


@pytest.mark.asyncio
async def test_real_receive_timeout_routes_to_bounded_recovery_and_replacement_output():
    result = await _run_receive_timeout_recovery()

    assert result["typed_timeout_events"] == [{"type": "receive_timeout"}]
    assert result["timeout_reasons"] == ["timeout"]
    assert result["session_count"] == 2
    assert result["replacement_transcripts"] == ["timeout replacement transcript"]
    assert result["device_audio"] == [b"timeout-new-audio"]
    assert result["pending_owned_tasks"] == ()
    assert set(result["owned_task_fields"]) >= EXPECTED_PROVIDER_TASK_FIELDS


@pytest.mark.asyncio
async def test_google_live_full_lifecycle_recovers_without_duplicate_owners_or_audio():
    result = await _run_lifecycle_journey()

    assert result.receive_loop_max_active == 1
    assert result.live_session_max_active == 1
    assert result.replayed_transport_audio == [b"frame-1\x00frame-2\x00"]
    assert result.replayed_transport_audio[0].replace(b"\x00", b"") == (
        b"frame-1frame-2"
    )
    assert result.replay_count == 1
    assert result.replacement_device_audio == [b"new-audio"]
    assert result.text_response_id == 1
    assert result.response_ids == sorted(set(result.response_ids))
    assert result.response_ids == [0, 2]
    assert result.session_generations == sorted(set(result.session_generations))
    assert result.session_generations == [1, 2]
    assert result.stale_state_before == "INTERRUPTING"
    assert result.stale_state_after == result.stale_state_before
    assert result.replacement_transcripts == ["replacement user transcript"]
    # LISTENING is the provider's safe interactive state after model audio_end.
    assert result.final_interaction_state == "LISTENING"
    assert result.pending_owned_tasks == ()
    assert set(result.owned_task_fields) >= EXPECTED_PROVIDER_TASK_FIELDS
