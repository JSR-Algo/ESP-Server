"""Reconnect evidence joins the active lesson without logging private content."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.voice.session_provider.google_live import GoogleLiveProvider
from tests.test_google_live_provider_edges import _Bridge, _Client, _Conn, _Fallback


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["lesson", "network", "silent", "hard"])
@pytest.mark.parametrize("lesson_active", [True, False])
async def test_reconnect_started_emits_lesson_correlation_without_private_content(
    path, lesson_active
):
    conn = _Conn()
    conn.google_live_evidence_journey_id = "journey-test"
    conn.config["google_live"]["reconnect"] = {
        "enabled": True, "max_retries": 1, "backoff_ms": 0,
        "backoff_multiplier": 1,
    }
    private = "private-content-sentinel"
    conn.child_name = conn.transcript = conn.access_token = private
    if lesson_active:
        conn.lesson_runtime = SimpleNamespace(
            assignment_id="assignment-test", session_id="lesson-session-test",
            child_name=private, transcript=private, access_token=private,
        )
    provider = GoogleLiveProvider(
        conn, client_factory=lambda *_args: _Client(),
        classic_provider_factory=lambda _conn: _Fallback(),
    )
    provider._client = _Client()
    provider._bridge = _Bridge()
    provider._interaction.start_live_connection("live-test-1")
    provider._close_live_resources = AsyncMock()
    provider._record_reconnect_attempt = AsyncMock()
    provider._forward_pending_reconnect_audio = AsyncMock()

    async def open_owner(*_args, **_kwargs):
        provider._interaction.start_live_connection("live-test-2")

    provider._open_live_session = AsyncMock(side_effect=open_owner)
    provider._open_live_session_locked = AsyncMock(side_effect=open_owner)
    try:
        if path == "lesson":
            result = await provider._attempt_lesson_reconnect_once("retry")
        elif path == "network":
            result = await provider._try_reconnect_with_lease(RuntimeError("network"))
        elif path == "silent":
            provider._consecutive_waiting_model_timeouts = provider._SILENT_LIVE_REOPEN_TIMEOUTS
            result = await provider._reopen_silent_live_session_after_timeouts_with_lease(1.0)
        else:
            result = await provider._hard_reconnect_after_interrupt_with_lease("interrupt")
        assert result is True
        records = [
            (args[0].format(*args[1:]), kwargs)
            for level, args, kwargs in conn.logger.messages
            if level == "info" and args and "evidence_reconnect_started" in args[0]
        ]
        assert len(records) == 1
        message, fields = records[0]
        assert "journey_id=journey-test" in message
        assert "connection_id=session-1" in message
        assert "from_live_connection_id=live-test-1" in message
        assert "attempt=1" in message
        assert "session_id=session-1" in message
        if lesson_active:
            assert "assignment_id=assignment-test" in message
        else:
            assert "assignment_id=" not in message
        assert private not in message
        assert fields == {}
    finally:
        await provider.close()


@pytest.mark.asyncio
async def test_playout_receipt_preserves_transport_and_lesson_identities_without_content():
    from tests.test_google_live_course_playout import receipt, setup_playout, start

    runtime, conn, provider, bridge = setup_playout()
    private = "private-playout-content-sentinel"
    conn.child_name = conn.transcript = conn.access_token = private
    try:
        token = await start(runtime, conn, bridge)
        assert provider.accept_lesson_audio_drain_ack(receipt(conn, token, "start", 100))
        await bridge._send_tts_message("stop")
        assert provider.accept_lesson_audio_drain_ack(receipt(conn, token, "stop", 200))
        assert not provider.accept_lesson_audio_drain_ack(receipt(conn, token, "stop", 200))
        records = [
            (args[0].format(*args[1:]), kwargs)
            for level, args, kwargs in conn.logger.messages
            if level == "info" and args and "course_playout_receipt" in args[0]
        ]
        assert len(records) == 2
        for (message, fields), state, at in zip(records, ["start", "stop"], [100, 200]):
            values = dict(field.split("=", 1) for field in message.split()[1:])
            assert values["session_id"] == conn.session_id
            assert values["lesson_session_id"] == runtime.session_id
            assert values["assignment_id"] == runtime.assignment_id
            assert values["playout_id"] == token
            assert values["state"] == state
            assert values["device_playout_ms"] == str(at)
            assert "receipt_monotonic_ms" in values
            assert private not in message
            assert fields == {}
    finally:
        await runtime.close()
