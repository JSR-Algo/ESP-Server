from __future__ import annotations

import copy
import json
import os
import sys
from unittest import mock

import pytest

sys.path.insert(0, os.path.dirname(__file__))

import test_lesson_runtime as legacy
from core.lesson.conversation_contract import LessonToolIdentity
from core.lesson.runtime import LessonRuntime, RENDERER_V4, S_RUNNING

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
        return LessonRuntime(
            conn,
            assignment=assignment,
            manifest=selected,
            asset_cache=_ConversationAssetCache(selected),
            forwarder=legacy._FakeForwarder(),
            manifest_checksum=legacy._manifest_checksum(),
        )


async def _activate(runtime: LessonRuntime, step_index: int = 0) -> None:
    assert await runtime.preload_only()
    runtime.state = S_RUNNING
    runtime._step_index = step_index
    runtime._step = runtime._steps[step_index]
    runtime._step_id = runtime._step["id"]
    runtime._step_seq = 20 + step_index
    runtime._step_acked = True
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


@pytest.mark.asyncio
async def test_conversation_activates_only_for_exact_v4_tvideo_v2_contract() -> None:
    runtime = _runtime()
    await _activate(runtime)
    assert runtime.conversation is not None
    assert runtime.conversation.identity().lesson_session_id == runtime.session_id
    assert runtime.conversation.identity().step_key == runtime._step_id == "barn"

    for mutate in (
        lambda manifest: manifest.update(manifestVersion="teebot-lesson-renderer.v3"),
        lambda manifest: manifest["cinematicPhases"][0].update(templateVersion=1),
        lambda manifest: manifest.pop("conversation"),
    ):
        candidate = _manifest()
        mutate(candidate)
        gated = _runtime(manifest=candidate)
        gated.state = S_RUNNING
        gated._step_index = 0
        gated._step = gated._steps[0]
        gated._step_id = "barn"
        gated._bind_conversation_for_current_step()
        assert gated.conversation is None


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
async def test_interrupt_retires_old_visual_and_ignores_late_ack() -> None:
    runtime = _runtime()
    await _activate(runtime)
    await runtime.conversation_child_response(_identity(runtime), "meaning_vi")
    await runtime.conversation_visual_reaction(
        _identity(runtime, cue=True), "teach", effect="show_teaching_scene"
    )
    old = _frames(runtime)[-1]

    interrupted = await runtime.conversation_interrupt(_identity(runtime))
    assert interrupted.next_intent == "listen_to_child"
    listening = _frames(runtime)[-1]
    assert listening["body"]["cueId"] == "barn-listen"
    assert old["sequence"] not in runtime._outstanding

    await runtime.on_lesson_ack(_ack(runtime, old, 1))
    assert listening["sequence"] in runtime._outstanding


@pytest.mark.asyncio
async def test_nonterminal_continue_advances_only_after_exact_transition_ack_once() -> None:
    runtime = _runtime()
    await _activate(runtime)
    await runtime.conversation_child_response(_identity(runtime), "target")
    await runtime.conversation_pronunciation_outcome(_identity(runtime), "correct")

    continued = await runtime.conversation_continue(
        _identity(runtime, cue=True), effect="show_celebration"
    )
    assert continued.next_intent == "continue_lesson"
    transition = _frames(runtime)[-1]
    assert transition["body"]["cueId"] == "barn-to-hay-word-transition"
    assert runtime._step_id == "barn"
    assert runtime._steps_completed == 0

    await runtime.on_lesson_ack(_ack(runtime, transition, 1, cue_id="barn-listen"))
    assert runtime._step_id == "barn"
    await runtime.on_lesson_ack(_ack(runtime, transition, 1))
    assert runtime._step_id == "hay"
    assert runtime._steps_completed == 1
    assert runtime.conversation is not None
    assert runtime.conversation.identity().step_key == "hay"

    await runtime.on_lesson_ack(_ack(runtime, transition, 2))
    assert runtime._steps_completed == 1


@pytest.mark.asyncio
async def test_terminal_continue_completes_without_fake_transition() -> None:
    runtime = _runtime()
    await _activate(runtime, step_index=1)
    runtime._steps_completed = 1
    await runtime.conversation_child_response(_identity(runtime), "target")
    await runtime.conversation_pronunciation_outcome(_identity(runtime), "correct")
    await runtime.conversation_visual_reaction(
        _identity(runtime, cue=True), "celebrate", effect="show_celebration"
    )
    stale_celebration = _frames(runtime)[-1]

    before = len(_frames(runtime))
    decision = await runtime.conversation_continue(
        _identity(runtime, cue=True), effect="show_celebration"
    )
    assert decision.next_intent == "complete_lesson"
    emitted = _frames(runtime)[before:]
    assert all(frame["body"].get("cueId") != "word-transition" for frame in emitted)
    assert emitted[-1]["type"] == "lesson_stop"
    assert stale_celebration["sequence"] not in runtime._outstanding
    assert runtime._steps_completed == 2
