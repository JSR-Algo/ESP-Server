from __future__ import annotations

import asyncio
import copy
import json
import os
import sys
from unittest import mock

import pytest

sys.path.insert(0, os.path.dirname(__file__))

import test_lesson_runtime as legacy
from core.lesson.conversation_contract import LessonToolIdentity
from core.lesson.errors import LESSON_FRAME_ACK_TIMEOUT, LessonError
from core.lesson.runtime import (
    LessonRuntime,
    MAX_RETIRED_CONVERSATION_ACK_SEQUENCES,
    RENDERER_V4,
    S_COMPLETED,
    S_FAILED,
    S_IDLE,
    S_PAUSED,
    S_RUNNING,
)

from test_lesson_conversation_runtime import _backend_manifest


_DURATIONS = {
    "opening": 9500,
    "greet": 1200,
    "teach": 2600,
    "listen": 1300,
    "thinking": 1300,
    "correct": 600,
    "retry-level-1": 1200,
    "retry-level-2": 1400,
    "retry-level-3": 1600,
    "celebrate": 3000,
    "word-transition": 1100,
}


def _manifest() -> dict:
    authored = _backend_manifest()
    manifest = legacy._build_manifest()
    manifest.update(copy.deepcopy(authored))
    manifest["features"] = {
        "lessonRendererV4": {
            "flattenedMjpegCinematic": True,
            "assetSource": "publishedFlattenedDerivative",
        }
    }
    base_step = legacy._build_manifest()["steps"][0]
    manifest["steps"] = []
    for key in ("barn", "hay"):
        step = copy.deepcopy(base_step)
        step.update(id=key, type="listen", completionClass="interactive")
        manifest["steps"].append(step)
    for phase in manifest["cinematicPhases"]:
        cue_id = phase["cueId"]
        duration_ms = _DURATIONS[phase["effect"]]
        derivative_id = (cue_id.encode().hex() + "0" * 64)[:64]
        phase["timing"] = {"durationMs": duration_ms}
        phase["asset"] = {
            "derivativeId": derivative_id,
            "path": f"lessons/derivatives/{derivative_id}/{cue_id}.mp4",
            "url": f"https://cdn.example.test/lessons/derivatives/{derivative_id}/{cue_id}.mp4",
            "sha256": "a" * 64,
            "bytes": 1234,
            "mediaType": "video/mp4",
            "width": 480,
            "height": 320,
            "metadata": {
                "codec": "mjpeg",
                "fps": 10,
                "durationMs": duration_ms,
                "frameCount": duration_ms // 100,
                "hasAudio": False,
            },
        }
    return manifest


class _ConversationAssetCache(legacy._FakeAssetCache):
    def __init__(self, manifest: dict) -> None:
        super().__init__(ready=True)
        self._manifest = manifest
        self.asset_pack_local_root = "sd://tbot/lesson-assets"
        self.preload_calls = 0

    async def preload(self):
        self.preload_calls += 1
        return await super().preload()

    def asset_pack_manifest(self, **kwargs):
        pack = super().asset_pack_manifest(**kwargs)
        pack["assets"] = []
        for phase in self._manifest["cinematicPhases"]:
            cue_id = phase["cueId"]
            asset = phase["asset"]
            path = f"{pack['localRoot']}/flattenedCinematic.{cue_id}"
            pack["assets"].append(
                {
                    "key": f"flattenedCinematic.{cue_id}",
                    "state": "READY",
                    "checksumOk": True,
                    "localPath": path,
                    "sdPath": path,
                    "sha256": asset["sha256"],
                    "size": asset["bytes"],
                    "mediaType": "video/mp4",
                    "derivativeId": asset["derivativeId"],
                    "cueId": cue_id,
                    "effect": phase["effect"],
                    "stepKey": phase["stepKey"],
                    "playbackMode": phase["playbackMode"],
                    "compatibilityMetadata": {
                        **asset["metadata"],
                        "width": 480,
                        "height": 320,
                    },
                }
            )
        return pack


def _runtime(*, manifest: dict | None = None) -> LessonRuntime:
    selected = manifest or _manifest()
    conn = legacy._FakeConn(
        features={
            "lesson": True,
            "renderer": [RENDERER_V4],
            "lessonRendererV4": {
                "flattenedMjpegCinematic": True,
                "sdAssetPack": True,
            },
        }
    )
    conn.device_id = "robot-v4"
    conn.config = {
        "lesson": {
            "renderer_v4_enabled": True,
            "rollout_device_allowlist": ["robot-v4"],
            "asset_delivery_mode": "sd_pack",
            "frame_ack_timeout_sec": 60,
        }
    }
    assignment = legacy._build_assignment()
    assignment.update(lessonId="farm-english", lessonVersion=4)
    with mock.patch("core.lesson.runtime.uuid.uuid4", return_value="lesson-session"):
        runtime = LessonRuntime(
            conn,
            assignment=assignment,
            manifest=selected,
            asset_cache=_ConversationAssetCache(selected),
            forwarder=legacy._FakeForwarder(),
            manifest_checksum=legacy._manifest_checksum(),
        )
    conn.lesson_runtime = runtime
    return runtime


async def _activate(runtime: LessonRuntime, step_index: int = 0) -> None:
    assert await runtime.preload_only()
    runtime.state = S_RUNNING
    runtime._step_index = step_index
    runtime._step = runtime._steps[step_index]
    runtime._step_id = runtime._step["id"]
    runtime._step_seq = 20 + step_index
    runtime._step_acked = True
    runtime._step_visuals_ready = True
    runtime._step_completed = False
    runtime._bind_conversation_for_current_step()


def _identity(runtime: LessonRuntime, *, cue: bool = False) -> LessonToolIdentity:
    conversation = runtime.conversation
    assert conversation is not None
    return conversation.identity(cue_id=conversation.pending_cue_id if cue else None)


def _frames(runtime: LessonRuntime) -> list[dict]:
    return [json.loads(payload) for payload in runtime.conn.websocket.sent]


def _ack(runtime: LessonRuntime, frame: dict, inbound_sequence: int, *, cue_id: str | None = None) -> dict:
    command = frame["body"]
    return {
        "type": "lesson_ack",
        "protocolVersion": RENDERER_V4,
        "assignmentId": runtime.assignment_id,
        "sessionId": runtime.session_id,
        "lessonId": runtime.lesson_id,
        "lessonVersion": runtime.lesson_version,
        "stepId": frame["stepId"],
        "sequence": inbound_sequence,
        "timestamp": 1,
        "body": {
            "acks": frame["sequence"],
            "cinematicPhase": {
                "event": "phaseReady",
                "command": "start",
                "cueId": cue_id or command["cueId"],
                "commandSequenceId": command["commandSequenceId"],
                "accepted": True,
                "phaseReady": True,
            },
        },
    }


async def _visual_and_ack(
    runtime: LessonRuntime,
    cue_role: str,
    effect: str,
    inbound_sequence: int,
) -> dict:
    decision = await runtime.conversation_visual_reaction(
        _identity(runtime, cue=True), cue_role, effect=effect
    )
    assert decision.accepted
    frame = _frames(runtime)[-1]
    await runtime.on_lesson_ack(_ack(runtime, frame, inbound_sequence))
    return frame


async def _wait_for_count(items: list, count: int) -> None:
    while len(items) < count:
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_conversation_activates_only_for_exact_v4_tvideo_v2_contract() -> None:
    runtime = _runtime()
    await _activate(runtime)
    assert runtime.conversation is not None
    assert runtime.conversation_tool_path_active()
    assert runtime.conversation.identity().lesson_session_id == runtime.session_id
    assert runtime.conversation.identity().step_key == runtime._step_id == "barn"
    context = runtime.conversation_tool_context()
    assert context["identity"] == {
        "lessonSessionId": runtime.session_id,
        "turnSequenceId": 1,
        "attemptId": "lesson-session:barn:1",
        "stepKey": "barn",
        "cueId": "barn-listen",
    }
    assert context["allowedTools"] == ["lesson_visual_reaction"]
    assert context["guidance"]["pronunciation"]["slowModel"]

    for mutate in (
        lambda manifest: manifest["cinematicPhases"][0].update(templateVersion=1),
        lambda manifest: manifest.pop("conversation"),
    ):
        candidate = _manifest()
        mutate(candidate)
        gated = _runtime(manifest=candidate)
        if "conversation" in candidate:
            with pytest.raises(LessonError):
                await gated.preload_only()
        else:
            assert await gated.preload_only()
        gated.state = S_RUNNING
        gated._step_index = 0
        gated._step = gated._steps[0]
        gated._step_id = "barn"
        gated._step_acked = True
        gated._step_visuals_ready = True
        gated._bind_conversation_for_current_step()
        assert gated.conversation is None
        assert gated.conn.websocket.sent == []


@pytest.mark.asyncio
async def test_context_refreshes_to_new_attempt_after_word_transition() -> None:
    runtime = _runtime()
    await _activate(runtime)
    first = runtime.conversation_tool_context()
    runtime._step_index = 1
    runtime._step = runtime._steps[1]
    runtime._step_id = runtime._step["id"]
    runtime._step_seq += 1
    runtime._step_acked = True
    runtime._step_visuals_ready = True
    runtime._step_completed = False
    runtime._bind_conversation_for_current_step()

    second = runtime.conversation_tool_context()

    assert second["identity"]["stepKey"] == "hay"
    assert second["identity"]["attemptId"] != first["identity"]["attemptId"]
    assert second["identity"]["turnSequenceId"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["reordered", "extra", "missing"])
async def test_conversation_activation_rejects_manifest_step_order_drift(mutation: str) -> None:
    manifest = _manifest()
    if mutation == "reordered":
        manifest["steps"].reverse()
    elif mutation == "extra":
        extra = copy.deepcopy(manifest["steps"][-1])
        extra["id"] = "extra"
        manifest["steps"].append(extra)
    else:
        manifest["steps"].pop()
    runtime = _runtime(manifest=manifest)

    with pytest.raises(LessonError) as exc:
        await runtime.preload_only()

    assert exc.value.code == "LESSON_CONVERSATION_CONTRACT_INVALID"
    assert runtime.conversation is None
    assert runtime._conversation_contract_valid is False
    assert runtime.asset_cache.preload_calls == 0
    assert runtime.state == S_IDLE


@pytest.mark.asyncio
async def test_visual_tool_uses_cinematic_sequence_fencing_and_duplicate_is_noop() -> None:
    runtime = _runtime()
    await _activate(runtime)
    before = runtime.forwarder.batches[:]

    identity = _identity(runtime, cue=True)
    decision = await runtime.conversation_visual_reaction(
        identity, "listen", effect="show_listening_scene"
    )
    assert decision.accepted
    command = _frames(runtime)[-1]
    assert command["type"] == "lesson_cinematic_control"
    assert command["body"]["cueId"] == "barn-listen"
    assert command["body"]["playbackMode"] == "loop"

    stale = _ack(runtime, command, 1, cue_id="hay-listen")
    await runtime.on_lesson_ack(stale)
    assert command["sequence"] in runtime._outstanding
    await runtime.on_lesson_ack(_ack(runtime, command, 1))
    assert command["sequence"] not in runtime._outstanding
    assert runtime.forwarder.batches == before

    duplicate = await runtime.conversation_visual_reaction(
        identity,
        "listen",
        effect="show_listening_scene",
    )
    assert not duplicate.accepted
    assert len(_frames(runtime)) == 1


@pytest.mark.asyncio
async def test_invalid_semantic_identity_never_mutates_progress() -> None:
    runtime = _runtime()
    await _activate(runtime)
    await _visual_and_ack(runtime, "listen", "show_listening_scene", 1)
    snapshot = runtime.conversation.snapshot()
    forwarded = runtime.forwarder.batches[:]
    stale = LessonToolIdentity(
        "other-session",
        runtime.conversation.turn_sequence_id,
        runtime.conversation.attempt_id,
        "barn",
    )
    decision = await runtime.conversation_child_response(stale, "target")
    assert decision.code == "CROSS_SESSION"
    assert runtime.conversation.snapshot() == snapshot
    assert runtime._steps_completed == 0
    assert runtime.forwarder.batches == forwarded


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode",
    ["replaced", "candidate", "paused", "stopped", "unacked", "visuals_pending", "closed"],
)
async def test_public_semantic_boundaries_reject_when_runtime_authority_is_lost(mode: str) -> None:
    runtime = _runtime()
    await _activate(runtime)
    await _visual_and_ack(runtime, "listen", "show_listening_scene", 1)
    snapshot = runtime.conversation.snapshot()
    frames = _frames(runtime)
    if mode == "replaced":
        runtime.conn.lesson_runtime = object()
    elif mode == "candidate":
        runtime.conn.lesson_runtime = object()
        runtime.conn.lesson_runtime_candidate = runtime
    elif mode == "paused":
        runtime.state = S_PAUSED
    elif mode == "stopped":
        runtime.state = S_COMPLETED
    elif mode == "unacked":
        runtime._step_acked = False
    elif mode == "visuals_pending":
        runtime._step_visuals_ready = False
    else:
        runtime._closed = True

    decision = await runtime.conversation_child_response(_identity(runtime), "target")

    assert decision.code == "RUNTIME_NOT_AUTHORITATIVE"
    assert runtime.conversation.snapshot() == snapshot
    assert _frames(runtime) == frames
    assert runtime._steps_completed == 0


@pytest.mark.asyncio
async def test_every_public_conversation_boundary_uses_the_same_authority_guard() -> None:
    runtime = _runtime()
    await _activate(runtime)
    runtime.state = S_PAUSED
    snapshot = runtime.conversation.snapshot()
    calls = (
        lambda: runtime.conversation_child_response(_identity(runtime), "target"),
        lambda: runtime.conversation_pronunciation_outcome(_identity(runtime), "correct"),
        lambda: runtime.conversation_context_turn(_identity(runtime)),
        lambda: runtime.conversation_visual_reaction(
            _identity(runtime, cue=True), "listen", effect="show_listening_scene"
        ),
        lambda: runtime.conversation_interrupt(_identity(runtime)),
        lambda: runtime.conversation_continue(
            _identity(runtime, cue=True), effect="show_listening_scene"
        ),
    )

    for call in calls:
        decision = await call()
        assert decision.code == "RUNTIME_NOT_AUTHORITATIVE"
        assert runtime.conversation.snapshot() == snapshot


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["pre_bind", "post_step", "replacement"])
async def test_every_public_conversation_boundary_rejects_when_no_conversation_is_bound(
    mode: str,
) -> None:
    runtime = _runtime()
    if mode != "pre_bind":
        await _activate(runtime)
        runtime.conversation = None
    if mode == "replacement":
        runtime.conn.lesson_runtime = object()
    frames = _frames(runtime)
    outstanding = copy.deepcopy(runtime._outstanding)
    forwarded = runtime.forwarder.batches[:]
    calls = (
        lambda: runtime.conversation_child_response(None, "target"),
        lambda: runtime.conversation_pronunciation_outcome(None, "correct"),
        lambda: runtime.conversation_context_turn(None),
        lambda: runtime.conversation_visual_reaction(
            None, "listen", effect="show_listening_scene"
        ),
        lambda: runtime.conversation_interrupt(None),
        lambda: runtime.conversation_continue(None, effect=None),
    )

    for call in calls:
        decision = await call()
        assert not decision.accepted
        assert decision.code == "CONVERSATION_NOT_ACTIVE"
        assert _frames(runtime) == frames
        assert runtime._outstanding == outstanding
        assert runtime._conversation_pending_visual is None
        assert runtime._frame_ack_timeout_task is None
        assert runtime.forwarder.batches == forwarded


@pytest.mark.asyncio
async def test_visual_send_race_rolls_back_fsm_and_cinematic_authority() -> None:
    runtime = _runtime()
    await _activate(runtime)
    snapshot = runtime.conversation.snapshot()

    async def replace_during_send(_payload: str) -> None:
        runtime.conn.lesson_runtime = object()

    runtime._send = replace_during_send
    decision = await runtime.conversation_visual_reaction(
        _identity(runtime, cue=True), "listen", effect="show_listening_scene"
    )

    assert decision.code == "RUNTIME_NOT_AUTHORITATIVE"
    assert runtime.conversation.snapshot() == snapshot
    assert runtime._conversation_pending_visual is None
    assert runtime._cinematic_pending_command is None
    assert runtime._outstanding == {}


@pytest.mark.asyncio
async def test_visual_send_connection_error_rolls_back_transaction() -> None:
    runtime = _runtime()
    await _activate(runtime)
    snapshot = runtime.conversation.snapshot()

    async def fail_send(_payload: str) -> None:
        raise ConnectionError("firmware disconnected")

    runtime._send = fail_send
    decision = await runtime.conversation_visual_reaction(
        _identity(runtime, cue=True), "listen", effect="show_listening_scene"
    )

    assert decision.code == "VISUAL_EMIT_FAILED"
    assert runtime.conversation.snapshot() == snapshot
    assert runtime._outstanding == {}
    assert runtime._cinematic_pending_command is None
    assert runtime._conversation_pending_visual is None
    assert runtime._frame_ack_timeout_task is None
    assert runtime._frame_ack_timeout_sequence is None


@pytest.mark.asyncio
@pytest.mark.parametrize("interrupt_before_cancel", [False, True])
async def test_visual_send_cancellation_cleans_transaction_without_stale_rollback(
    interrupt_before_cancel: bool,
) -> None:
    runtime = _runtime()
    await _activate(runtime)
    initial_snapshot = runtime.conversation.snapshot()
    send_started = asyncio.Event()
    never_release = asyncio.Event()

    async def blocked_send(_payload: str) -> None:
        send_started.set()
        await never_release.wait()

    runtime._send = blocked_send
    visual_task = asyncio.create_task(
        runtime.conversation_visual_reaction(
            _identity(runtime, cue=True), "listen", effect="show_listening_scene"
        )
    )
    await send_started.wait()
    expected_snapshot = initial_snapshot
    if interrupt_before_cancel:
        assert (await runtime.conversation_interrupt(_identity(runtime))).accepted
        expected_snapshot = runtime.conversation.snapshot()

    visual_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await visual_task

    assert runtime.conversation.snapshot() == expected_snapshot
    assert runtime._outstanding == {}
    assert runtime._cinematic_pending_command is None
    assert runtime._conversation_pending_visual is None
    assert runtime._frame_ack_timeout_task is None
    assert runtime._frame_ack_timeout_sequence is None


@pytest.mark.asyncio
async def test_visual_send_unbound_race_rejects_without_overwriting_stale_conversation() -> None:
    runtime = _runtime()
    await _activate(runtime)
    conversation = runtime.conversation
    send_started = asyncio.Event()
    release_send = asyncio.Event()

    async def blocked_send(_payload: str) -> None:
        send_started.set()
        await release_send.wait()

    runtime._send = blocked_send
    visual_task = asyncio.create_task(
        runtime.conversation_visual_reaction(
            _identity(runtime, cue=True), "listen", effect="show_listening_scene"
        )
    )
    await send_started.wait()
    stale_snapshot = conversation.snapshot()
    runtime.conversation = None
    release_send.set()

    decision = await visual_task

    assert decision.code == "RUNTIME_NOT_AUTHORITATIVE"
    assert conversation.snapshot() == stale_snapshot
    assert runtime._outstanding == {}
    assert runtime._cinematic_pending_command is None
    assert runtime._conversation_pending_visual is None
    assert runtime._frame_ack_timeout_task is None
    assert runtime._frame_ack_timeout_sequence is None
    assert (
        await runtime.conversation_interrupt(None)
    ).code == "CONVERSATION_NOT_ACTIVE"


@pytest.mark.asyncio
async def test_terminal_continue_race_rejects_without_progress_or_fsm_mutation() -> None:
    runtime = _runtime()
    await _activate(runtime, step_index=1)
    runtime._steps_completed = 1
    await _visual_and_ack(runtime, "listen", "show_listening_scene", 1)
    await runtime.conversation_child_response(_identity(runtime), "target")
    await _visual_and_ack(runtime, "thinking", "show_thinking_scene", 2)
    await runtime.conversation_pronunciation_outcome(_identity(runtime), "correct")
    await _visual_and_ack(runtime, "correct", "show_correct_reaction", 3)
    await runtime.conversation_continue(
        _identity(runtime, cue=True), effect="show_correct_reaction"
    )
    await _visual_and_ack(runtime, "celebrate", "show_celebration", 4)
    snapshot = runtime.conversation.snapshot()
    forwarded = runtime.forwarder.batches[:]

    task = asyncio.create_task(
        runtime.conversation_continue(
            _identity(runtime, cue=True), effect="show_celebration"
        )
    )
    await asyncio.sleep(0)
    runtime.conn.lesson_runtime = object()
    decision = await task

    assert decision.code == "RUNTIME_NOT_AUTHORITATIVE"
    assert runtime.conversation.snapshot() == snapshot
    assert runtime._steps_completed == 1
    assert runtime.forwarder.batches == forwarded


@pytest.mark.asyncio
async def test_semantics_wait_for_visual_tool_and_exact_hardware_ack() -> None:
    runtime = _runtime()
    await _activate(runtime)
    initial = runtime.conversation.snapshot()

    blocked = await runtime.conversation_child_response(_identity(runtime), "target")
    assert blocked.code == "VISUAL_ACK_REQUIRED"
    assert runtime.conversation.snapshot() == initial

    listen = await runtime.conversation_visual_reaction(
        _identity(runtime, cue=True), "listen", effect="show_listening_scene"
    )
    assert listen.accepted
    listen_frame = _frames(runtime)[-1]
    assert listen_frame["body"]["cueId"] == "barn-listen"
    assert (await runtime.conversation_child_response(_identity(runtime), "target")).code == "VISUAL_ACK_REQUIRED"

    await runtime.on_lesson_ack(_ack(runtime, listen_frame, 1, cue_id="hay-listen"))
    assert (await runtime.conversation_child_response(_identity(runtime), "target")).code == "VISUAL_ACK_REQUIRED"
    await runtime.on_lesson_ack(_ack(runtime, listen_frame, 1))

    heard = await runtime.conversation_child_response(_identity(runtime), "target")
    assert heard.cue_id == "barn-thinking"
    assert (await runtime.conversation_pronunciation_outcome(_identity(runtime), "correct")).code == "VISUAL_ACK_REQUIRED"

    thinking = await _visual_and_ack(runtime, "thinking", "show_thinking_scene", 2)
    assert thinking["body"]["cueId"] == "barn-thinking"
    correct = await runtime.conversation_pronunciation_outcome(_identity(runtime), "correct")
    assert correct.cue_id == "barn-correct"
    assert (await runtime.conversation_continue(_identity(runtime, cue=True), effect="show_correct_reaction")).code == "VISUAL_ACK_REQUIRED"

    correct_frame = await _visual_and_ack(runtime, "correct", "show_correct_reaction", 3)
    assert correct_frame["body"]["cueId"] == "barn-correct"
    celebrate = await runtime.conversation_continue(
        _identity(runtime, cue=True), effect="show_correct_reaction"
    )
    assert celebrate.cue_id == "barn-celebrate"
    celebrate_frame = await _visual_and_ack(runtime, "celebrate", "show_celebration", 4)
    assert celebrate_frame["body"]["cueId"] == "barn-celebrate"

    continued = await runtime.conversation_continue(
        _identity(runtime, cue=True), effect="show_celebration"
    )
    assert continued.cue_id == "barn-to-hay-word-transition"
    transition_context = runtime.conversation_tool_context()
    assert transition_context["cueId"] == "barn-to-hay-word-transition"
    assert transition_context["allowedTools"] == ["lesson_visual_reaction"]
    assert runtime._step_id == "barn"
    transition = await runtime.conversation_visual_reaction(
        _identity(runtime, cue=True), "word_transition", effect="show_word_transition"
    )
    assert transition.accepted
    transition_frame = _frames(runtime)[-1]
    await runtime.on_lesson_ack(_ack(runtime, transition_frame, 5, cue_id="barn-listen"))
    assert runtime._step_id == "barn"
    await runtime.on_lesson_ack(_ack(runtime, transition_frame, 5))
    assert runtime._step_id == "hay"


@pytest.mark.asyncio
async def test_retry_cue_requires_visual_authorization_and_ack() -> None:
    runtime = _runtime()
    await _activate(runtime)
    await _visual_and_ack(runtime, "listen", "show_listening_scene", 1)
    await runtime.conversation_child_response(_identity(runtime), "target")
    await _visual_and_ack(runtime, "thinking", "show_thinking_scene", 2)

    retry = await runtime.conversation_pronunciation_outcome(_identity(runtime), "retry")
    assert retry.cue_id == "barn-retry-level-1"
    retry_frame = await runtime.conversation_visual_reaction(
        _identity(runtime, cue=True), "retry_level_1", effect="show_effort_reaction"
    )
    assert retry_frame.accepted
    command = _frames(runtime)[-1]
    assert command["body"]["cueId"] == "barn-retry-level-1"
    assert (await runtime.conversation_pronunciation_outcome(_identity(runtime), "retry")).code == "VISUAL_ACK_REQUIRED"
    await runtime.on_lesson_ack(_ack(runtime, command, 3))
    assert (await runtime.conversation_pronunciation_outcome(_identity(runtime), "retry")).accepted


async def _drive_attempted_review(runtime: LessonRuntime) -> int:
    inbound_sequence = 1
    await _visual_and_ack(runtime, "listen", "show_listening_scene", inbound_sequence)
    await runtime.conversation_child_response(_identity(runtime), "target")
    inbound_sequence += 1
    await _visual_and_ack(runtime, "thinking", "show_thinking_scene", inbound_sequence)
    effects = (
        ("retry_level_1", "show_effort_reaction"),
        ("retry_level_2", "show_slow_model"),
        ("retry_level_3", "show_pronunciation_guide"),
    )
    for cue_role, effect in effects:
        retry = await runtime.conversation_pronunciation_outcome(_identity(runtime), "retry")
        assert retry.cue_id is not None
        inbound_sequence += 1
        await _visual_and_ack(runtime, cue_role, effect, inbound_sequence)
    attempted = await runtime.conversation_pronunciation_outcome(_identity(runtime), "retry")
    assert attempted.outcome == "attempted"
    assert attempted.review_needed is True
    return inbound_sequence


@pytest.mark.asyncio
async def test_nonterminal_attempted_review_requires_one_shot_continue_before_transition() -> None:
    runtime = _runtime()
    await _activate(runtime)
    inbound_sequence = await _drive_attempted_review(runtime)
    frames = _frames(runtime)

    premature = await runtime.conversation_visual_reaction(
        _identity(runtime, cue=True), "word_transition", effect="show_word_transition"
    )
    assert premature.code == "CONTINUE_REQUIRED"
    assert _frames(runtime) == frames
    assert runtime._step_id == "barn"

    continued = await runtime.conversation_continue(
        _identity(runtime, cue=True), effect="show_word_transition"
    )
    assert continued.accepted
    assert continued.cue_id == "barn-to-hay-word-transition"
    snapshot = runtime.conversation.snapshot()
    duplicate = await runtime.conversation_continue(
        _identity(runtime, cue=True), effect="show_word_transition"
    )
    assert duplicate.code == "CONTINUE_ALREADY_APPLIED"
    assert runtime.conversation.snapshot() == snapshot
    assert runtime._step_id == "barn"

    visual = await runtime.conversation_visual_reaction(
        _identity(runtime, cue=True), "word_transition", effect="show_word_transition"
    )
    assert visual.accepted
    transition = _frames(runtime)[-1]
    inbound_sequence += 1
    await runtime.on_lesson_ack(_ack(runtime, transition, inbound_sequence))
    assert runtime._step_id == "hay"
    assert runtime._steps_completed == 1


@pytest.mark.asyncio
async def test_terminal_attempted_review_continue_completes_without_transition_cue() -> None:
    runtime = _runtime()
    await _activate(runtime, step_index=1)
    runtime._steps_completed = 1
    await _drive_attempted_review(runtime)
    before = len(_frames(runtime))

    continued = await runtime.conversation_continue(_identity(runtime), effect=None)

    assert continued.accepted
    assert continued.next_intent == "complete_lesson"
    emitted = _frames(runtime)[before:]
    assert emitted[-1]["type"] == "lesson_stop"
    assert all(frame["body"].get("cueId") is None for frame in emitted)
    assert runtime._steps_completed == 2


@pytest.mark.asyncio
async def test_private_safe_mastered_evidence_is_forwarded_exactly_once() -> None:
    runtime = _runtime()
    await _activate(runtime)
    runtime._conversation_started_at = 10.0
    runtime._clock = lambda: 14.321
    await _visual_and_ack(runtime, "listen", "show_listening_scene", 1)
    await runtime.conversation_child_response(_identity(runtime), "target")
    await _visual_and_ack(runtime, "thinking", "show_thinking_scene", 2)
    await runtime.conversation_pronunciation_outcome(_identity(runtime), "correct")
    await _visual_and_ack(runtime, "correct", "show_correct_reaction", 3)
    await runtime.conversation_continue(
        _identity(runtime, cue=True), effect="show_correct_reaction"
    )
    await _visual_and_ack(runtime, "celebrate", "show_celebration", 4)
    await runtime.conversation_continue(
        _identity(runtime, cue=True), effect="show_celebration"
    )
    transition = await runtime.conversation_visual_reaction(
        _identity(runtime, cue=True), "word_transition", effect="show_word_transition"
    )
    assert transition.accepted
    frame = _frames(runtime)[-1]
    await runtime.on_lesson_ack(_ack(runtime, frame, 5))
    await runtime.on_lesson_ack(_ack(runtime, frame, 6))

    completed = [
        event
        for batch in runtime.forwarder.batches
        for event in batch["events"]
        if event.get("type") == "step_completed" and event.get("stepId") == "barn"
    ]
    assert len(completed) == 1
    assert completed[0]["detail"] == {
        "evidence": {
            "outcome": "mastered",
            "attempt_count": 1,
            "final_coaching_level": 0,
            "elapsed_ms": 4321,
            "step_key": "barn",
            "lesson_version": 4,
        }
    }


@pytest.mark.asyncio
async def test_stale_cancelled_or_fallback_paths_record_no_evidence() -> None:
    runtime = _runtime()
    await _activate(runtime)
    await _visual_and_ack(runtime, "listen", "show_listening_scene", 1)
    before = copy.deepcopy(runtime.forwarder.batches)

    stale = LessonToolIdentity("other-session", 2, runtime.conversation.attempt_id, "barn")
    assert (await runtime.conversation_child_response(stale, "target")).accepted is False
    directive = await runtime.conversation_live_interruption("timeout")
    assert directive.accepted
    assert directive.reconnect_allowed
    assert directive.prompt.endswith("barn.")
    assert runtime.conversation.mastered is False
    assert runtime.forwarder.batches == before
    unsupported = await runtime.conversation_live_interruption("not-allowed")
    assert unsupported.accepted is False
    assert unsupported.code == "UNSUPPORTED_LIVE_FALLBACK_REASON"
    assert unsupported.reconnect_allowed is False
    assert unsupported.prompt == ""
    thinking = _frames(runtime)[-1]
    assert thinking["body"]["cueId"] == "barn-thinking"
    assert runtime._conversation_visual_ack is None
    ack_wait = asyncio.create_task(
        runtime.wait_conversation_live_fallback_ack(directive.window_id, timeout_sec=0.2)
    )
    await asyncio.sleep(0)
    await runtime.on_lesson_ack(_ack(runtime, thinking, 2, cue_id="hay-thinking"))
    assert not ack_wait.done()
    await runtime.on_lesson_ack(_ack(runtime, thinking, 2))
    authorization = await ack_wait
    assert isinstance(authorization, str)
    assert runtime.claim_conversation_live_fallback_prompt(
        directive.window_id,
        authorization,
    ) is True
    bounded = await runtime.conversation_live_interruption("timeout")
    assert bounded.accepted
    assert bounded.reconnect_allowed is False
    assert len(_frames(runtime)) == 2
    runtime.conn.lesson_runtime = object()
    stale_window = await runtime.conversation_live_interruption("timeout")
    assert stale_window.accepted is False
    assert stale_window.code == "RUNTIME_NOT_AUTHORITATIVE"
    runtime.conn.lesson_runtime = runtime
    assert runtime._conversation_visual_ack == (
        runtime.conversation.attempt_id,
        "barn-thinking",
    )
    await runtime.conversation_child_response(_identity(runtime), "meaning_vi")
    assert runtime.conversation_live_reconnect_succeeded(directive.window_id) is False
    next_window = await runtime.conversation_live_interruption("transport")
    assert next_window.accepted
    assert next_window.reconnect_allowed
    assert next_window.window_id != directive.window_id


@pytest.mark.asyncio
async def test_fallback_ack_timeout_closes_window_and_late_ack_cannot_revive_prompt() -> None:
    runtime = _runtime()
    await _activate(runtime)
    await _visual_and_ack(runtime, "listen", "show_listening_scene", 1)
    directive = await runtime.conversation_live_interruption("timeout")
    thinking = _frames(runtime)[-1]

    first = await runtime.wait_conversation_live_fallback_ack(
        directive.window_id,
        timeout_sec=0.01,
    )
    await runtime.on_lesson_ack(_ack(runtime, thinking, 2))
    second = await runtime.wait_conversation_live_fallback_ack(
        directive.window_id,
        timeout_sec=0.01,
    )

    assert first is None
    assert second is None
    assert runtime._conversation_fallback_ack_future is None
    assert runtime.claim_conversation_live_fallback_prompt(
        directive.window_id,
        "late-authorization",
    ) is False


@pytest.mark.asyncio
async def test_ack_authorization_revalidates_turn_and_is_one_shot_before_prompt() -> None:
    runtime = _runtime()
    await _activate(runtime)
    await _visual_and_ack(runtime, "listen", "show_listening_scene", 1)
    directive = await runtime.conversation_live_interruption("timeout")
    thinking = _frames(runtime)[-1]
    waiting = asyncio.create_task(
        runtime.wait_conversation_live_fallback_ack(
            directive.window_id,
            timeout_sec=0.2,
        )
    )
    await asyncio.sleep(0)

    await runtime.on_lesson_ack(_ack(runtime, thinking, 2))
    await runtime.conversation_child_response(_identity(runtime), "meaning_vi")
    stale_authorization = await waiting

    assert stale_authorization is None
    assert runtime.claim_conversation_live_fallback_prompt(
        directive.window_id,
        "stale-token",
    ) is False

    fresh = await runtime.conversation_live_interruption("transport")
    fresh_thinking = _frames(runtime)[-1]
    await runtime.on_lesson_ack(_ack(runtime, fresh_thinking, 3))
    authorization = await runtime.wait_conversation_live_fallback_ack(
        fresh.window_id,
        timeout_sec=0.2,
    )
    assert isinstance(authorization, str)
    assert runtime.claim_conversation_live_fallback_prompt(
        fresh.window_id,
        authorization,
    ) is True
    assert runtime.claim_conversation_live_fallback_prompt(
        fresh.window_id,
        authorization,
    ) is False


@pytest.mark.asyncio
async def test_live_fallback_recognizes_prior_normal_interrupt_without_double_consuming() -> None:
    runtime = _runtime()
    await _activate(runtime)
    await _visual_and_ack(runtime, "listen", "show_listening_scene", 1)
    before = runtime.conversation.turn_sequence_id
    assert (await runtime.conversation_interrupt_current()).accepted

    directive = await runtime.conversation_live_interruption("interrupted")

    assert directive.accepted
    assert runtime.conversation.turn_sequence_id == before + 2
    assert _frames(runtime)[-1]["body"]["cueId"] == "barn-thinking"


@pytest.mark.asyncio
async def test_attempted_evidence_is_structured_once_without_child_or_model_content() -> None:
    runtime = _runtime()
    await _activate(runtime, step_index=1)
    runtime._steps_completed = 1
    runtime._conversation_started_at = 20.0
    runtime._clock = lambda: 25.0
    await _drive_attempted_review(runtime)
    assert (await runtime.conversation_continue(_identity(runtime), effect=None)).accepted

    completed = [
        event
        for batch in runtime.forwarder.batches
        for event in batch["events"]
        if event.get("type") == "step_completed" and event.get("stepId") == "hay"
    ]
    assert len(completed) == 1
    assert completed[0]["detail"] == {
        "evidence": {
            "outcome": "attempted",
            "attempt_count": 1,
            "final_coaching_level": 3,
            "elapsed_ms": 5000,
            "step_key": "hay",
            "lesson_version": 4,
        }
    }
    serialized = json.dumps(runtime.forwarder.batches, ensure_ascii=False).lower()
    for forbidden in ("audio_bytes", "transcript", "utterance", "model_prose", "responseclass"):
        assert forbidden not in serialized


@pytest.mark.asyncio
async def test_interrupt_during_blocked_visual_send_retires_old_turn_without_rollback() -> None:
    runtime = _runtime()
    await _activate(runtime)
    send_started = asyncio.Event()
    release_send = asyncio.Event()

    async def blocked_send(_payload: str) -> None:
        send_started.set()
        await release_send.wait()

    runtime._send = blocked_send
    visual_task = asyncio.create_task(
        runtime.conversation_visual_reaction(
            _identity(runtime, cue=True), "listen", effect="show_listening_scene"
        )
    )
    await send_started.wait()
    interrupted = await runtime.conversation_interrupt(_identity(runtime))
    interrupted_snapshot = runtime.conversation.snapshot()
    old_sequence = next(iter(runtime._outstanding))
    release_send.set()
    stale_visual = await visual_task

    assert interrupted.accepted
    assert interrupted.next_intent == "listen_to_child"
    assert stale_visual.code == "RUNTIME_NOT_AUTHORITATIVE"
    assert runtime.conversation.snapshot() == interrupted_snapshot
    assert old_sequence not in runtime._outstanding
    assert runtime._conversation_pending_visual is None
    assert runtime.conversation.pending_cue_id == "barn-listen"

    late = {
        "type": "lesson_ack",
        "protocolVersion": RENDERER_V4,
        "assignmentId": runtime.assignment_id,
        "sessionId": runtime.session_id,
        "lessonId": runtime.lesson_id,
        "lessonVersion": runtime.lesson_version,
        "stepId": "barn",
        "sequence": 1,
        "timestamp": 1,
        "body": {"acks": old_sequence},
    }
    await runtime.on_lesson_ack(late)
    assert runtime.conversation.snapshot() == interrupted_snapshot


@pytest.mark.asyncio
@pytest.mark.parametrize("completion", ["ack", "timeout"])
async def test_stale_visual_cleanup_preserves_new_visual_ack_timeout(
    completion: str,
) -> None:
    runtime = _runtime()
    await _activate(runtime)
    runtime.conn.config["lesson"]["frame_ack_max_retries"] = 0
    old_send_started = asyncio.Event()
    release_old_send = asyncio.Event()
    release_timeout = asyncio.Event()
    sent: list[dict] = []

    async def controlled_sleep(_seconds: float) -> None:
        await release_timeout.wait()

    async def block_first_send(payload: str) -> None:
        sent.append(json.loads(payload))
        if len(sent) == 1:
            old_send_started.set()
            await release_old_send.wait()

    runtime._sleep = controlled_sleep
    runtime._send = block_first_send
    old_visual_task = asyncio.create_task(
        runtime.conversation_visual_reaction(
            _identity(runtime, cue=True), "listen", effect="show_listening_scene"
        )
    )
    await old_send_started.wait()
    assert (await runtime.conversation_interrupt(_identity(runtime))).accepted

    new_visual = await runtime.conversation_visual_reaction(
        _identity(runtime, cue=True), "listen", effect="show_listening_scene"
    )
    assert new_visual.accepted
    old_frame, new_frame = sent
    new_sequence = new_frame["sequence"]
    new_timeout = runtime._frame_ack_timeout_task
    assert runtime._cinematic_pending_command["commandSequenceId"] == new_sequence
    assert runtime._conversation_pending_visual["sequence"] == new_sequence

    release_old_send.set()
    stale_visual = await old_visual_task

    assert stale_visual.code == "RUNTIME_NOT_AUTHORITATIVE"
    assert old_frame["sequence"] not in runtime._outstanding
    assert new_sequence in runtime._outstanding
    assert runtime._cinematic_pending_command["commandSequenceId"] == new_sequence
    assert runtime._conversation_pending_visual["sequence"] == new_sequence
    assert runtime._frame_ack_timeout_task is new_timeout
    assert runtime._frame_ack_timeout_sequence == new_sequence
    assert new_timeout is not None and not new_timeout.done()

    await runtime.on_lesson_ack(_ack(runtime, old_frame, 1))
    assert new_sequence in runtime._outstanding
    assert runtime._frame_ack_timeout_task is new_timeout

    if completion == "ack":
        await runtime.on_lesson_ack(_ack(runtime, new_frame, 2))
        assert new_sequence not in runtime._outstanding
        assert runtime._conversation_pending_visual is None
        assert runtime._frame_ack_timeout_task is None
        assert runtime._frame_ack_timeout_sequence is None
    else:
        release_timeout.set()
        await new_timeout
        assert runtime.state == S_FAILED
        assert runtime.last_error is not None
        assert runtime.last_error.code == LESSON_FRAME_ACK_TIMEOUT


async def _emit_conversation_visual_retry(
    runtime: LessonRuntime,
) -> tuple[dict, dict, list[asyncio.Event]]:
    runtime.conn.config["lesson"]["frame_ack_max_retries"] = 1
    timeout_gates: list[asyncio.Event] = []

    async def controlled_sleep(_seconds: float) -> None:
        gate = asyncio.Event()
        timeout_gates.append(gate)
        await gate.wait()

    runtime._sleep = controlled_sleep
    visual = await runtime.conversation_visual_reaction(
        _identity(runtime, cue=True), "listen", effect="show_listening_scene"
    )
    assert visual.accepted
    await _wait_for_count(timeout_gates, 1)
    original = _frames(runtime)[-1]
    timeout_gates[0].set()
    while len(_frames(runtime)) < 2:
        await asyncio.sleep(0)
    await _wait_for_count(timeout_gates, 2)
    retry = _frames(runtime)[-1]
    assert retry["sequence"] != original["sequence"]
    assert retry["body"]["commandSequenceId"] == original["body"]["commandSequenceId"]
    assert runtime._cinematic_pending_command["ackSequence"] == retry["sequence"]
    return original, retry, timeout_gates


@pytest.mark.asyncio
async def test_interrupt_after_visual_retry_retires_retry_envelope_and_timeout() -> None:
    runtime = _runtime()
    await _activate(runtime)
    original, retry, timeout_gates = await _emit_conversation_visual_retry(runtime)
    retry_timeout = runtime._frame_ack_timeout_task

    interrupted = await runtime.conversation_interrupt(_identity(runtime))

    assert interrupted.accepted
    assert original["sequence"] not in runtime._outstanding
    assert retry["sequence"] not in runtime._outstanding
    assert runtime._cinematic_pending_command is None
    assert runtime._conversation_pending_visual is None
    assert runtime._frame_ack_timeout_task is None
    assert runtime._frame_ack_timeout_sequence is None
    timeout_gates[1].set()
    if retry_timeout is not None:
        await asyncio.gather(retry_timeout, return_exceptions=True)
    assert runtime.state == S_RUNNING
    await runtime.on_lesson_ack(_ack(runtime, retry, 1))
    assert runtime.state == S_RUNNING
    assert runtime._conversation_visual_ack is None


@pytest.mark.asyncio
async def test_runtime_replacement_after_visual_retry_closes_retry_authority() -> None:
    runtime = _runtime()
    await _activate(runtime)
    _original, retry, timeout_gates = await _emit_conversation_visual_retry(runtime)
    retry_timeout = runtime._frame_ack_timeout_task
    replacement = object()
    runtime.conn.lesson_runtime = replacement

    await runtime.close()

    assert runtime.conn.lesson_runtime is replacement
    assert retry["sequence"] not in runtime._outstanding
    assert runtime._cinematic_pending_command is None
    assert runtime._conversation_pending_visual is None
    assert runtime._frame_ack_timeout_task is None
    assert runtime._frame_ack_timeout_sequence is None
    timeout_gates[1].set()
    if retry_timeout is not None:
        await asyncio.gather(retry_timeout, return_exceptions=True)
    assert runtime.state == S_RUNNING
    await runtime.on_lesson_ack(_ack(runtime, retry, 1))
    assert runtime.conn.lesson_runtime is replacement
    assert runtime.state == S_RUNNING


@pytest.mark.asyncio
async def test_exact_late_original_ack_advances_inbound_before_new_visual_ack() -> None:
    runtime = _runtime()
    await _activate(runtime)
    assert (
        await runtime.conversation_visual_reaction(
            _identity(runtime, cue=True), "listen", effect="show_listening_scene"
        )
    ).accepted
    original = _frames(runtime)[-1]
    assert (await runtime.conversation_interrupt(_identity(runtime))).accepted
    assert (
        await runtime.conversation_visual_reaction(
            _identity(runtime, cue=True), "listen", effect="show_listening_scene"
        )
    ).accepted
    current = _frames(runtime)[-1]

    await runtime.on_lesson_ack(_ack(runtime, original, 1))
    await runtime.on_lesson_ack(_ack(runtime, current, 2))

    assert runtime._last_inbound_sequence == 2
    assert current["sequence"] not in runtime._outstanding
    assert runtime._conversation_visual_ack == (
        runtime.conversation.attempt_id,
        "barn-listen",
    )
    assert all(frame["type"] != "lesson_error" for frame in _frames(runtime))


@pytest.mark.asyncio
async def test_exact_late_retry_ack_advances_inbound_before_new_visual_ack() -> None:
    runtime = _runtime()
    await _activate(runtime)
    _original, retry, _timeout_gates = await _emit_conversation_visual_retry(runtime)
    assert (await runtime.conversation_interrupt(_identity(runtime))).accepted
    assert (
        await runtime.conversation_visual_reaction(
            _identity(runtime, cue=True), "listen", effect="show_listening_scene"
        )
    ).accepted
    current = _frames(runtime)[-1]

    await runtime.on_lesson_ack(_ack(runtime, retry, 1))
    await runtime.on_lesson_ack(_ack(runtime, current, 2))

    assert runtime._last_inbound_sequence == 2
    assert current["sequence"] not in runtime._outstanding
    assert runtime._conversation_visual_ack == (
        runtime.conversation.attempt_id,
        "barn-listen",
    )
    assert all(frame["type"] != "lesson_error" for frame in _frames(runtime))


@pytest.mark.asyncio
async def test_forged_retired_ack_does_not_consume_inbound_or_poison_new_visual() -> None:
    runtime = _runtime()
    await _activate(runtime)
    assert (
        await runtime.conversation_visual_reaction(
            _identity(runtime, cue=True), "listen", effect="show_listening_scene"
        )
    ).accepted
    retired = _frames(runtime)[-1]
    assert (await runtime.conversation_interrupt(_identity(runtime))).accepted
    assert (
        await runtime.conversation_visual_reaction(
            _identity(runtime, cue=True), "listen", effect="show_listening_scene"
        )
    ).accepted
    current = _frames(runtime)[-1]

    await runtime.on_lesson_ack(_ack(runtime, retired, 1, cue_id="hay-listen"))
    assert runtime._last_inbound_sequence == 0
    await runtime.on_lesson_ack(_ack(runtime, current, 1))

    assert runtime._last_inbound_sequence == 1
    assert current["sequence"] not in runtime._outstanding
    assert all(frame["type"] != "lesson_error" for frame in _frames(runtime))


@pytest.mark.asyncio
async def test_retired_conversation_ack_tombstones_are_bounded_and_clear_on_close() -> None:
    runtime = _runtime()
    await _activate(runtime)
    for sequence in range(MAX_RETIRED_CONVERSATION_ACK_SEQUENCES + 3):
        runtime._retire_conversation_ack_sequence(
            sequence,
            {
                "type": "lesson_cinematic_control",
                "stepId": "barn",
                "body": {
                    "command": "start",
                    "cueId": "barn-listen",
                    "commandSequenceId": sequence,
                },
            },
        )

    assert (
        len(runtime._retired_conversation_ack_sequences)
        == MAX_RETIRED_CONVERSATION_ACK_SEQUENCES
    )
    assert 0 not in runtime._retired_conversation_ack_sequences
    await runtime.close()
    assert runtime._retired_conversation_ack_sequences == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "retry_error",
    [ConnectionError("retry disconnected"), asyncio.CancelledError()],
    ids=["connection_error", "cancelled"],
)
async def test_current_visual_retry_send_failure_is_consumed_and_fails_closed(
    retry_error: BaseException,
) -> None:
    runtime = _runtime()
    await _activate(runtime)
    runtime.conn.config["lesson"]["frame_ack_max_retries"] = 1
    timeout_gates: list[asyncio.Event] = []
    default_send = runtime._send
    send_count = 0

    async def controlled_sleep(_seconds: float) -> None:
        gate = asyncio.Event()
        timeout_gates.append(gate)
        await gate.wait()

    async def fail_retry_send(payload: str) -> None:
        nonlocal send_count
        send_count += 1
        if send_count == 2:
            raise retry_error
        await default_send(payload)

    runtime._sleep = controlled_sleep
    runtime._send = fail_retry_send
    assert (
        await runtime.conversation_visual_reaction(
            _identity(runtime, cue=True), "listen", effect="show_listening_scene"
        )
    ).accepted
    await _wait_for_count(timeout_gates, 1)
    retry_task = runtime._frame_ack_timeout_task
    timeout_gates[0].set()

    assert retry_task is not None
    await retry_task

    assert runtime.state == S_FAILED
    assert runtime.last_error is not None
    assert runtime.last_error.code == LESSON_FRAME_ACK_TIMEOUT
    assert runtime._conversation_pending_visual is None
    assert runtime._cinematic_pending_command is None
    assert runtime._outstanding == {}
    assert runtime._frame_ack_timeout_task is None
    assert runtime._frame_ack_timeout_sequence is None
    assert runtime._frame_ack_retry_task is None
    assert runtime._frame_ack_retry_command_sequence is None


@pytest.mark.asyncio
async def test_stale_visual_retry_send_cancellation_is_consumed_without_failure() -> None:
    runtime = _runtime()
    await _activate(runtime)
    runtime.conn.config["lesson"]["frame_ack_max_retries"] = 1
    first_timeout = asyncio.Event()
    retry_timeout = asyncio.Event()
    retry_send_started = asyncio.Event()
    never_release = asyncio.Event()
    default_send = runtime._send
    sleep_count = 0
    send_count = 0

    async def controlled_sleep(_seconds: float) -> None:
        nonlocal sleep_count
        sleep_count += 1
        await (first_timeout if sleep_count == 1 else retry_timeout).wait()

    async def block_retry_send(payload: str) -> None:
        nonlocal send_count
        send_count += 1
        if send_count == 2:
            retry_send_started.set()
            await never_release.wait()
            return
        await default_send(payload)

    runtime._sleep = controlled_sleep
    runtime._send = block_retry_send
    assert (
        await runtime.conversation_visual_reaction(
            _identity(runtime, cue=True), "listen", effect="show_listening_scene"
        )
    ).accepted
    retry_task = runtime._frame_ack_timeout_task
    first_timeout.set()
    await retry_send_started.wait()
    assert (await runtime.conversation_interrupt(_identity(runtime))).accepted
    assert retry_task is not None
    retry_task.cancel()

    await retry_task

    assert runtime.state == S_RUNNING
    assert runtime.last_error is None
    assert runtime._conversation_pending_visual is None
    assert runtime._cinematic_pending_command is None
    assert runtime._outstanding == {}
    assert runtime._frame_ack_timeout_task is None
    assert runtime._frame_ack_timeout_sequence is None
    assert runtime._frame_ack_retry_task is None
    assert runtime._frame_ack_retry_command_sequence is None


@pytest.mark.asyncio
async def test_interrupt_retires_old_visual_and_ignores_late_ack() -> None:
    runtime = _runtime()
    await _activate(runtime)
    await _visual_and_ack(runtime, "listen", "show_listening_scene", 1)
    await runtime.conversation_child_response(_identity(runtime), "meaning_vi")
    await runtime.conversation_visual_reaction(
        _identity(runtime, cue=True), "teach", effect="show_teaching_scene"
    )
    old = _frames(runtime)[-1]

    interrupted = await runtime.conversation_interrupt(_identity(runtime))
    assert interrupted.next_intent == "listen_to_child"
    assert old["sequence"] not in runtime._outstanding
    assert runtime.conversation.pending_cue_id == "barn-listen"

    await runtime.on_lesson_ack(_ack(runtime, old, 2))
    assert runtime._conversation_visual_ack is None
    listening = await runtime.conversation_visual_reaction(
        _identity(runtime, cue=True), "listen", effect="show_listening_scene"
    )
    assert listening.accepted


@pytest.mark.asyncio
async def test_nonterminal_continue_advances_only_after_exact_transition_ack_once() -> None:
    runtime = _runtime()
    await _activate(runtime)
    await _visual_and_ack(runtime, "listen", "show_listening_scene", 1)
    await runtime.conversation_child_response(_identity(runtime), "target")
    await _visual_and_ack(runtime, "thinking", "show_thinking_scene", 2)
    await runtime.conversation_pronunciation_outcome(_identity(runtime), "correct")
    await _visual_and_ack(runtime, "correct", "show_correct_reaction", 3)
    await runtime.conversation_continue(
        _identity(runtime, cue=True), effect="show_correct_reaction"
    )
    await _visual_and_ack(runtime, "celebrate", "show_celebration", 4)

    continued = await runtime.conversation_continue(
        _identity(runtime, cue=True), effect="show_celebration"
    )
    assert continued.next_intent == "continue_lesson"
    visual = await runtime.conversation_visual_reaction(
        _identity(runtime, cue=True), "word_transition", effect="show_word_transition"
    )
    assert visual.accepted
    transition = _frames(runtime)[-1]
    assert transition["body"]["cueId"] == "barn-to-hay-word-transition"
    assert runtime._step_id == "barn"
    assert runtime._steps_completed == 0

    await runtime.on_lesson_ack(_ack(runtime, transition, 5, cue_id="barn-listen"))
    assert runtime._step_id == "barn"
    await runtime.on_lesson_ack(_ack(runtime, transition, 5))
    assert runtime._step_id == "hay"
    assert runtime._steps_completed == 1
    assert runtime.conversation is not None
    assert runtime.conversation.identity().step_key == "hay"

    await runtime.on_lesson_ack(_ack(runtime, transition, 6))
    assert runtime._steps_completed == 1


@pytest.mark.asyncio
async def test_terminal_continue_completes_without_fake_transition() -> None:
    runtime = _runtime()
    await _activate(runtime, step_index=1)
    runtime._steps_completed = 1
    await _visual_and_ack(runtime, "listen", "show_listening_scene", 1)
    await runtime.conversation_child_response(_identity(runtime), "target")
    await _visual_and_ack(runtime, "thinking", "show_thinking_scene", 2)
    await runtime.conversation_pronunciation_outcome(_identity(runtime), "correct")
    await _visual_and_ack(runtime, "correct", "show_correct_reaction", 3)
    await runtime.conversation_continue(
        _identity(runtime, cue=True), effect="show_correct_reaction"
    )
    await _visual_and_ack(runtime, "celebrate", "show_celebration", 4)

    before = len(_frames(runtime))
    decision = await runtime.conversation_continue(
        _identity(runtime, cue=True), effect="show_celebration"
    )
    assert decision.next_intent == "complete_lesson"
    emitted = _frames(runtime)[before:]
    assert all(frame["body"].get("cueId") != "word-transition" for frame in emitted)
    assert emitted[-1]["type"] == "lesson_stop"
    assert runtime._steps_completed == 2
