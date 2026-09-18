"""T15 run-02: talking/listening/thinking clips are bound to ACTUAL audio boundaries.

The regressions here are the three host-measured drift cases from
`task-artifacts/course-production-ready-2026-09-10/T15/runs/run-02-20260918-audio-sync`:
a non-audio path (capture window, assessment, response-plan dispatch) replaced the
talking clip while the device was still playing that same response's audio. Only the
device's own playout receipts - start, end, cancellation - may move the talking clip.
Clip fixtures are not real-media acceptance and this file proves no device timing.
"""
import asyncio
import json

import pytest

from core.lesson.course_orchestrator import CourseDecision, SessionState
from core.lesson.embodied_intent import EmbodiedIntent
from tests.test_google_live_course_playout import receipt, setup_playout, start
from tests.test_lesson_cinematic_phase_routing import _ack_prepare_and_start, _frames


async def _talking(runtime, conn, provider, bridge):
    """Bring the runtime to a device-confirmed talking clip."""
    token = await start(runtime, conn, bridge)
    assert provider.accept_lesson_audio_drain_ack(receipt(conn, token, "start"))
    await asyncio.sleep(0)
    await _ack_prepare_and_start(runtime, 1)
    assert runtime._cinematic_phase["phaseId"] == "teach"
    assert runtime._course_playout_active
    return token


def _phase_commands(runtime):
    return [
        frame["body"]["cinematicPhase"]["phaseId"]
        for frame in _frames(runtime)
        if isinstance(frame.get("body", {}).get("cinematicPhase"), dict)
        and frame["body"]["cinematicPhase"].get("command") == "prepare"
    ]


def _decision(runtime, visual_state):
    return CourseDecision(
        "d1", True, SessionState.WORD_ACTIVE, "ADVANCE_ACTIVITY", "acknowledge_child",
        None, None, EmbodiedIntent.PRESENT_CENTER, False, None,
        activity_id=runtime._step_id, visual_state=visual_state, replay_entrance=False,
    )


@pytest.mark.asyncio
async def test_capture_window_cannot_show_listening_while_audio_still_plays():
    runtime, conn, provider, bridge = setup_playout()
    token = await _talking(runtime, conn, provider, bridge)
    count = len(_frames(runtime))

    assert await runtime._open_course_assessment_window(runtime._course_assessment_generation)
    for _ in range(10):
        await asyncio.sleep(0)
    assert runtime.course_assessment_window_open
    # The child-response window is open, but the device is still playing this response:
    # the talking clip owns the screen until the audio boundary arrives.
    assert runtime._cinematic_phase["phaseId"] == "teach"
    assert len(_frames(runtime)) == count

    await bridge._send_tts_message("stop")
    assert provider.accept_lesson_audio_drain_ack(receipt(conn, token, "stop", 400))
    await asyncio.sleep(0)
    await _ack_prepare_and_start(runtime, 3)
    assert runtime._cinematic_phase["phaseId"] == "listen"
    await runtime.close()


@pytest.mark.asyncio
async def test_assessment_cannot_show_thinking_while_audio_still_plays_and_is_shown_after():
    runtime, conn, provider, bridge = setup_playout()
    token = await _talking(runtime, conn, provider, bridge)
    count = len(_frames(runtime))

    assert runtime._queue_course_cinematic_phase("thinking") is False
    for _ in range(10):
        await asyncio.sleep(0)
    assert runtime._cinematic_phase["phaseId"] == "teach"
    assert len(_frames(runtime)) == count

    # The deferred intent is not lost: the audio boundary releases thinking, not listen.
    await bridge._send_tts_message("stop")
    assert provider.accept_lesson_audio_drain_ack(receipt(conn, token, "stop", 400))
    await asyncio.sleep(0)
    await _ack_prepare_and_start(runtime, 3)
    assert runtime._cinematic_phase["phaseId"] == "thinking"
    assert _phase_commands(runtime) == ["teach", "thinking"]
    await runtime.close()


@pytest.mark.asyncio
async def test_response_plan_dispatch_cannot_replace_talking_while_audio_still_plays():
    runtime, conn, provider, bridge = setup_playout()
    token = await _talking(runtime, conn, provider, bridge)
    count = len(_frames(runtime))

    await runtime._dispatch_course_embodied_decision(_decision(runtime, "teach"))
    for _ in range(10):
        await asyncio.sleep(0)
    assert runtime._cinematic_phase["phaseId"] == "teach"
    assert len(_frames(runtime)) == count

    await bridge._send_tts_message("stop")
    assert provider.accept_lesson_audio_drain_ack(receipt(conn, token, "stop", 400))
    await asyncio.sleep(0)
    await _ack_prepare_and_start(runtime, 3)
    assert runtime._cinematic_phase["phaseId"] == "listen"
    await runtime.close()


@pytest.mark.asyncio
async def test_correct_decision_during_audio_celebrates_only_after_the_audio_ends():
    runtime, conn, provider, bridge = setup_playout()
    token = await _talking(runtime, conn, provider, bridge)

    await runtime._dispatch_course_embodied_decision(_decision(runtime, "correct"))
    for _ in range(10):
        await asyncio.sleep(0)
    assert runtime._cinematic_phase["phaseId"] == "teach"

    await bridge._send_tts_message("stop")
    assert provider.accept_lesson_audio_drain_ack(receipt(conn, token, "stop", 400))
    await asyncio.sleep(0)
    await _ack_prepare_and_start(runtime, 3)
    assert runtime._cinematic_phase["phaseId"] == "celebrate"
    assert _phase_commands(runtime) == ["teach", "celebrate"]
    await runtime.close()


@pytest.mark.asyncio
async def test_barge_in_drops_the_deferred_phase_and_restores_listening():
    runtime, conn, provider, bridge = setup_playout()
    token = await _talking(runtime, conn, provider, bridge)
    assert runtime._queue_course_cinematic_phase("thinking") is False

    await bridge._send_tts_stop_now()
    assert not runtime._course_playout_active
    await asyncio.sleep(0)
    await _ack_prepare_and_start(runtime, 3)
    # Barge-in retires the obsolete response: its deferred visual must not replay.
    assert runtime._cinematic_phase["phaseId"] == "listen"
    assert _phase_commands(runtime) == ["teach", "listen"]
    assert not provider.accept_lesson_audio_drain_ack(receipt(conn, token, "stop", 400))
    await runtime.close()


@pytest.mark.asyncio
async def test_deferred_phase_does_not_survive_an_activity_change():
    runtime, conn, provider, bridge = setup_playout()
    token = await _talking(runtime, conn, provider, bridge)
    assert runtime._queue_course_cinematic_phase("thinking") is False

    from tests.test_lesson_cinematic_phase_routing import _activate_v5

    _activate_v5(runtime, 1)
    assert not provider.accept_lesson_audio_drain_ack(receipt(conn, token, "stop", 400))
    count = len(_frames(runtime))
    for _ in range(10):
        await asyncio.sleep(0)
    assert len(_frames(runtime)) == count
    await runtime.close()


@pytest.mark.asyncio
async def test_talking_still_starts_and_ends_on_the_device_receipts_only():
    """The suppression must not weaken the audio-bound start/stop it protects."""
    runtime, conn, provider, bridge = setup_playout()
    token = await start(runtime, conn, bridge)
    await asyncio.sleep(0.02)
    assert _frames(runtime) == []                      # no audio yet: no talking clip

    assert provider.accept_lesson_audio_drain_ack(receipt(conn, token, "start"))
    await asyncio.sleep(0)
    await _ack_prepare_and_start(runtime, 1)
    assert runtime._cinematic_phase["phaseId"] == "teach"
    assert runtime._cinematic_phase["playbackMode"] == "loop"

    await asyncio.sleep(0.4)                            # longer than the 300 ms clip
    assert runtime._cinematic_phase["phaseId"] == "teach"

    await bridge._send_tts_message("stop")
    assert provider.accept_lesson_audio_drain_ack(receipt(conn, token, "stop", 500))
    await asyncio.sleep(0)
    await _ack_prepare_and_start(runtime, 3)
    assert runtime._cinematic_phase["phaseId"] == "listen"
    assert json.loads(conn.websocket.sent[-1])["playoutId"] == token
    await runtime.close()
