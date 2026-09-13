"""Actual provider/bridge receipt routing with native lesson protocol fixtures."""
import asyncio
import copy
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from core.handle.textHandler.lessonMessageHandler import TtsAckHandler
from core.voice.google_live.audio_bridge import GoogleLiveAudioBridge
from core.voice.session_provider.google_live import GoogleLiveProvider
from tests.test_google_live_provider_edges import _Conn
from tests.test_lesson_cinematic_phase_routing import (
    _activate_v5, _ack_prepare_and_start, _frames, _phase_bound_runtime,
)


def setup_playout(*, negotiated=True):
    runtime = _phase_bound_runtime()
    _activate_v5(runtime, 0)
    runtime._entrance_completed = True
    conn = _Conn()
    conn.features = {"lessonAudioPlayoutAck": negotiated}
    conn.lesson_runtime = runtime
    conn.sentence_id = "sentence-1"
    provider = GoogleLiveProvider(conn)
    conn.voice_provider = provider
    bridge = GoogleLiveAudioBridge(conn, None, conn.logger,
        response_id_getter=provider.current_response_id,
        response_cancelled_checker=provider.is_response_cancelled)
    provider._bridge = bridge
    bridge._active_response_id = provider.current_response_id()
    return runtime, conn, provider, bridge


def receipt(conn, token, state, at=100):
    return {"type": "tts_ack", "state": state, "playoutId": token,
            "playoutAtMs": at, "session_id": conn.session_id}


async def start(runtime, conn, bridge):
    await bridge._send_tts_message("start")
    message = json.loads(conn.websocket.sent[-1])
    assert not runtime._course_playout_active
    assert not _frames(runtime)
    assert isinstance(message.get("playoutId"), str), "START must correlate actual device output"
    return message["playoutId"]


@pytest.mark.asyncio
@pytest.mark.parametrize("duration", [20, 30000])
async def test_actual_receipt_drives_loop_until_exact_stop_without_clip_timer(duration):
    runtime, conn, provider, bridge = setup_playout()
    token = await start(runtime, conn, bridge)
    assert provider.accept_lesson_audio_drain_ack(receipt(conn, token, "start"))
    await asyncio.sleep(0)
    await _ack_prepare_and_start(runtime, 1)
    assert runtime._cinematic_phase["phaseId"] == "teach"
    assert runtime._cinematic_phase["playbackMode"] == "loop"
    assert not provider.accept_lesson_audio_drain_ack(receipt(conn, token, "start"))
    assert not provider.accept_lesson_audio_drain_ack(receipt(conn, token, "stop", 100 + duration))
    await bridge._send_tts_message("stop")
    assert json.loads(conn.websocket.sent[-1])["playoutId"] == token
    assert provider.accept_lesson_audio_drain_ack(receipt(conn, token, "stop", 100 + duration))
    await asyncio.sleep(0)
    await _ack_prepare_and_start(runtime, 3)
    assert runtime._cinematic_phase["phaseId"] == "listen"
    assert not provider.accept_lesson_audio_drain_ack(receipt(conn, token, "stop", 100 + duration))
    await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("playoutId", "obsolete"), ("session_id", "old"),
    ("playoutAtMs", True), ("playoutAtMs", -1), ("playoutAtMs", 1.5)])
async def test_invalid_receipt_cannot_start_animation(field, value):
    runtime, conn, provider, bridge = setup_playout()
    token = await start(runtime, conn, bridge)
    message = receipt(conn, token, "start")
    message[field] = value
    assert not provider.accept_lesson_audio_drain_ack(message)
    assert not runtime._course_playout_active
    await runtime.close()


@pytest.mark.asyncio
async def test_stop_without_any_output_never_claims_talking_or_playout():
    runtime, conn, provider, bridge = setup_playout()
    token = await start(runtime, conn, bridge)
    await bridge._send_tts_message("stop")
    assert not provider.accept_lesson_audio_drain_ack(receipt(conn, token, "stop"))
    assert not runtime._course_playout_active
    await runtime.close()


@pytest.mark.asyncio
async def test_short_output_stop_supersedes_unacknowledged_teach_prepare():
    runtime, conn, provider, bridge = setup_playout()
    token = await start(runtime, conn, bridge)
    assert provider.accept_lesson_audio_drain_ack(receipt(conn, token, "start"))
    await asyncio.sleep(0)
    assert _frames(runtime)[-1]["body"]["cinematicPhase"]["phaseId"] == "teach"
    await bridge._send_tts_message("stop")
    assert provider.accept_lesson_audio_drain_ack(receipt(conn, token, "stop", 120))
    for _ in range(5):
        await asyncio.sleep(0)
    assert _frames(runtime)[-1]["body"]["cinematicPhase"]["phaseId"] == "listen"
    assert not runtime._course_playout_active
    await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["interrupt", "socket", "runtime", "generation", "close"])
async def test_replaced_or_cancelled_receipt_cannot_reanimate_old_response(change):
    runtime, conn, provider, bridge = setup_playout()
    token = await start(runtime, conn, bridge)
    if change == "interrupt":
        await bridge._send_tts_stop_now()
    elif change == "socket":
        conn.websocket = type(conn.websocket)()
    elif change == "runtime":
        conn.lesson_runtime = object()
    elif change == "generation":
        provider._response_generation += 1
    else:
        await provider._close_live_resources()
    assert not provider.accept_lesson_audio_drain_ack(receipt(conn, token, "start"))
    assert not runtime._course_playout_active
    await runtime.close()


@pytest.mark.asyncio
async def test_legacy_peer_receives_no_new_identity():
    runtime, conn, provider, bridge = setup_playout(negotiated=False)
    await bridge._send_tts_message("start")
    assert "playoutId" not in json.loads(conn.websocket.sent[-1])
    await runtime.close()


@pytest.mark.asyncio
async def test_duplicate_start_and_chunk_gap_preserve_one_identity_and_hidden_scene():
    runtime, conn, provider, bridge = setup_playout()
    runtime._step["scene"]["caption"] = {"visible": False}
    runtime._step["scene"]["objects"] = []
    scene = copy.deepcopy(runtime._step["scene"])
    token = await start(runtime, conn, bridge)
    assert provider.accept_lesson_audio_drain_ack(receipt(conn, token, "start"))
    await asyncio.sleep(0)
    await _ack_prepare_and_start(runtime, 1)
    count = len(_frames(runtime))
    await bridge._send_tts_message("start")
    assert json.loads(conn.websocket.sent[-1])["playoutId"] == token
    await asyncio.sleep(0.02)
    assert runtime._course_playout_active
    assert len(_frames(runtime)) == count
    assert runtime._step["scene"] == scene
    assert all("caption" not in frame.get("body", {}) and "objects" not in frame.get("body", {})
               for frame in _frames(runtime))
    await runtime.close()


@pytest.mark.asyncio
async def test_active_interrupt_retires_teach_without_successful_completion():
    runtime, conn, provider, bridge = setup_playout()
    token = await start(runtime, conn, bridge)
    assert provider.accept_lesson_audio_drain_ack(receipt(conn, token, "start"))
    await asyncio.sleep(0)
    await _ack_prepare_and_start(runtime, 1)
    await bridge._send_tts_stop_now()
    assert not runtime._course_playout_active
    assert not provider.accept_lesson_audio_drain_ack(receipt(conn, token, "stop", 120))
    await asyncio.sleep(0)
    await _ack_prepare_and_start(runtime, 3)
    assert runtime._cinematic_phase["phaseId"] == "listen"
    await runtime.close()


@pytest.mark.asyncio
async def test_same_runtime_disconnect_invalidates_queued_device_start():
    runtime, conn, provider, bridge = setup_playout()
    token = await start(runtime, conn, bridge)
    await runtime.on_disconnect()
    assert not provider.accept_lesson_audio_drain_ack(receipt(conn, token, "start"))
    assert not runtime._course_playout_active
    await runtime.close()


@pytest.mark.asyncio
async def test_transport_failure_retires_identity_before_delayed_receipt():
    runtime, conn, provider, bridge = setup_playout()
    token = await start(runtime, conn, bridge)
    conn.websocket.fail = True
    with pytest.raises(RuntimeError, match="send failed"):
        await bridge._send_tts_message("stop")
    assert not provider.accept_lesson_audio_drain_ack(receipt(conn, token, "start"))
    await runtime.close()


@pytest.mark.asyncio
async def test_canonical_producer_and_device_receipt_envelopes():
    fixture = json.loads((Path(__file__).parent / "fixtures/course-mode/speech-playout.v1.json").read_text())
    runtime, conn, provider, bridge = setup_playout()
    conn.session_id = fixture["start"]["session_id"]
    conn.google_live_lesson_prompt_drain_id = fixture["stop"]["drainId"]
    with patch("core.voice.session_provider.google_live.secrets.token_hex",
               return_value=fixture["start"]["playoutId"][:16]):
        await bridge._send_tts_message("start")
    assert json.loads(conn.websocket.sent[-1]) == fixture["start"]
    await TtsAckHandler().handle(conn, json.loads(json.dumps(fixture["started"])))
    assert runtime._course_playout_active
    await asyncio.sleep(0)
    await _ack_prepare_and_start(runtime, 1)
    await bridge._send_tts_message("stop")
    assert json.loads(conn.websocket.sent[-1]) == fixture["stop"]
    await TtsAckHandler().handle(conn, json.loads(json.dumps(fixture["drained"])))
    assert not runtime._course_playout_active
    await runtime.close()


@pytest.mark.asyncio
async def test_new_response_replaces_playing_identity_and_old_stop_cannot_end_it():
    runtime, conn, provider, bridge = setup_playout()
    token = await start(runtime, conn, bridge)
    assert provider.accept_lesson_audio_drain_ack(receipt(conn, token, "start"))
    await asyncio.sleep(0)
    await _ack_prepare_and_start(runtime, 1)
    provider._response_generation += 1
    bridge._active_response_id = provider.current_response_id()
    await bridge._send_tts_message("start")
    new_token = json.loads(conn.websocket.sent[-1])["playoutId"]
    assert new_token != token
    assert provider.accept_lesson_audio_drain_ack(receipt(conn, new_token, "start", 130))
    assert not provider.accept_lesson_audio_drain_ack(receipt(conn, token, "stop", 150))
    assert runtime._course_playout_active
    await runtime.close()


@pytest.mark.asyncio
async def test_device_stop_cannot_beat_start_timestamp():
    runtime, conn, provider, bridge = setup_playout()
    token = await start(runtime, conn, bridge)
    assert provider.accept_lesson_audio_drain_ack(receipt(conn, token, "start", 100))
    await bridge._send_tts_message("stop")
    assert not provider.accept_lesson_audio_drain_ack(receipt(conn, token, "stop", 99))
    assert runtime._course_playout_active
    await runtime.close()


@pytest.mark.asyncio
async def test_immediate_device_drain_receipt_during_stop_send_is_not_lost():
    runtime, conn, provider, bridge = setup_playout()
    token = await start(runtime, conn, bridge)
    assert provider.accept_lesson_audio_drain_ack(receipt(conn, token, "start"))
    original_send = conn.websocket.send
    accepted = []

    async def send(payload):
        await original_send(payload)
        if json.loads(payload).get("state") == "stop":
            accepted.append(provider.accept_lesson_audio_drain_ack(receipt(conn, token, "stop", 120)))

    conn.websocket.send = send
    await bridge._send_tts_message("stop")
    assert accepted == [True]
    assert not runtime._course_playout_active
    await runtime.close()


@pytest.mark.asyncio
async def test_activity_change_allocates_fresh_identity_before_new_start():
    runtime, conn, provider, bridge = setup_playout()
    token = await start(runtime, conn, bridge)
    _activate_v5(runtime, 1)
    await bridge._send_tts_message("start")
    replacement = json.loads(conn.websocket.sent[-1])["playoutId"]
    assert replacement != token
    assert not provider.accept_lesson_audio_drain_ack(receipt(conn, token, "start"))
    assert provider.accept_lesson_audio_drain_ack(receipt(conn, replacement, "start"))
    await runtime.close()


@pytest.mark.asyncio
async def test_stale_stop_suppression_does_not_authorize_a_drain_receipt():
    runtime, conn, provider, bridge = setup_playout()
    token = await start(runtime, conn, bridge)
    assert provider.accept_lesson_audio_drain_ack(receipt(conn, token, "start"))

    async def change_sentence(_conn):
        conn.sentence_id = "new-sentence"
        conn.audio_flow_control = {"sentence_id": conn.sentence_id}

    with patch("core.handle.sendAudioHandle._wait_for_audio_completion", AsyncMock(side_effect=change_sentence)):
        await bridge._send_tts_message("stop")
    assert not provider.accept_lesson_audio_drain_ack(receipt(conn, token, "stop", 120))
    await runtime.close()


@pytest.mark.asyncio
async def test_live_resource_close_does_not_enqueue_new_visual_content():
    runtime, conn, provider, bridge = setup_playout()
    token = await start(runtime, conn, bridge)
    assert provider.accept_lesson_audio_drain_ack(receipt(conn, token, "start"))
    await asyncio.sleep(0)
    await _ack_prepare_and_start(runtime, 1)
    count = len(_frames(runtime))
    await provider._close_live_resources()
    for _ in range(5):
        await asyncio.sleep(0)
    assert len(_frames(runtime)) == count
    assert not runtime._course_playout_active
    await runtime.close()


@pytest.mark.asyncio
async def test_playout_ids_increase_beyond_history_and_provider_recreation():
    runtime, conn, provider, bridge = setup_playout()
    tokens = []
    for _ in range(45):
        tokens.append(provider.prepare_course_playout(provider.current_response_id()))
        provider.cancel_course_playout()
    replacement = GoogleLiveProvider(conn)
    tokens.append(replacement.prepare_course_playout(replacement.current_response_id()))
    assert all(left < right for left, right in zip(tokens, tokens[1:]))
    assert all(len(token) == 32 and all(c in "0123456789abcdef" for c in token) for token in tokens)
    await runtime.close()


@pytest.mark.asyncio
async def test_playout_counter_exhaustion_cannot_downgrade_to_untagged_audio():
    runtime, conn, provider, bridge = setup_playout()
    conn._course_playout_prefix = "0123456789abcdef"
    conn._course_playout_counter = 2**64 - 1
    with pytest.raises(RuntimeError, match="identity exhausted"):
        await bridge._send_tts_message("start")
    assert not conn.websocket.sent
    await runtime.close()


@pytest.mark.asyncio
async def test_delayed_old_audio_end_cannot_stop_replacement_playout():
    runtime, conn, provider, bridge = setup_playout()
    conn.google_live_lesson_prompt_output_allowed = True
    old_token = await start(runtime, conn, bridge)
    bridge._should_drop_lesson_model_output = lambda *_args: False
    bridge._wait_for_output_deliveries = AsyncMock(return_value=True)
    bridge._flush_model_display = AsyncMock()

    async def replace_during_flush():
        provider._response_generation += 1
        bridge._active_response_id = provider.current_response_id()
        await bridge._send_tts_message("start")
        return 0

    bridge._flush_output_audio = replace_during_flush
    await bridge.handle_event({"type": "audio_end", "response_generation": 0})
    messages = [json.loads(payload) for payload in conn.websocket.sent]
    assert [message["state"] for message in messages] == ["start", "start"]
    assert messages[-1]["playoutId"] != old_token
    assert bridge._active_response_id == provider.current_response_id()
    await runtime.close()


@pytest.mark.asyncio
async def test_replacement_while_transport_stop_waits_cannot_dispatch_old_stop():
    runtime, conn, provider, bridge = setup_playout()
    await start(runtime, conn, bridge)

    async def replace_during_wait(_conn):
        provider._response_generation += 1
        bridge._active_response_id = provider.current_response_id()
        await bridge._send_tts_message("start")

    with patch("core.handle.sendAudioHandle._wait_for_audio_completion", AsyncMock(side_effect=replace_during_wait)):
        await bridge._send_tts_message("stop")
    assert [json.loads(payload)["state"] for payload in conn.websocket.sent] == ["start", "start"]
    assert conn.client_is_speaking
    await runtime.close()


@pytest.mark.asyncio
async def test_provider_interrupt_preserves_retired_playout_identity_in_stop():
    runtime, conn, provider, bridge = setup_playout()
    token = await start(runtime, conn, bridge)
    assert provider.accept_lesson_audio_drain_ack(receipt(conn, token, "start"))
    await provider._begin_user_interrupt("explicit_interrupt")
    stop = json.loads(conn.websocket.sent[-1])
    assert stop["state"] == "stop" and stop["reason"] == "interrupt"
    assert stop.get("playoutId") == token
    assert not provider.accept_lesson_audio_drain_ack(receipt(conn, token, "stop", 120))
    await provider.close()
    await runtime.close()


@pytest.mark.asyncio
async def test_provider_interrupt_cannot_cancel_new_playout_after_lesson_teardown_await():
    runtime, conn, provider, bridge = setup_playout()
    old_token = await start(runtime, conn, bridge)

    async def replace_during_teardown():
        bridge._active_response_id = provider.current_response_id()
        await bridge._send_tts_message("start")

    provider._interrupt_lesson_conversation = replace_during_teardown
    await provider._begin_user_interrupt("explicit_interrupt")
    messages = [json.loads(payload) for payload in conn.websocket.sent]
    assert [message["state"] for message in messages] == ["start", "start"]
    assert provider.current_course_playout_id() == messages[-1]["playoutId"] != old_token
    assert not conn.client_abort
    await provider.close()
    await runtime.close()
