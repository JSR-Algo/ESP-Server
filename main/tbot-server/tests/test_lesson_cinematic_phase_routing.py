from __future__ import annotations

import asyncio
import json
import os
import sys
from unittest import mock

import pytest

sys.path.insert(0, os.path.dirname(__file__))

import test_lesson_conversation_integration as conversation_fixtures
import test_lesson_runtime as legacy

from core.lesson.runtime import RENDERER_V4, RENDERER_V5, S_RUNNING, LessonRuntime


def _manifest() -> dict:
    manifest = conversation_fixtures._manifest()
    manifest.pop("conversation", None)
    for step in manifest["steps"]:
        step["expectedResponses"] = [step["id"]]
        step["interaction"] = {
            "template": "safeSpeaking",
            "maxAttempts": 3,
            "funPattern": "copyMyMove",
        }
    return manifest


def _runtime() -> LessonRuntime:
    return _runtime_from_manifest(_manifest())


def _v5_runtime() -> LessonRuntime:
    runtime = _runtime()
    runtime.manifest["manifestVersion"] = RENDERER_V5
    runtime.negotiated_version = RENDERER_V5
    runtime.renderer_capabilities = [RENDERER_V5]
    runtime.conn.device_id = "robot-v5"
    runtime.conn.features = {
        "lesson": True,
        "renderer": [RENDERER_V5],
        "lessonRendererV5": {"layeredCinematic": True, "sdAssetPack": True},
    }
    runtime.conn.config["lesson"].update(
        renderer_v4_enabled=False,
        renderer_v5_enabled=True,
        rollout_device_allowlist=["robot-v5"],
    )
    runtime._layered_cinematic_phases = {
        effect: {
            "templateId": "layeredCinematic",
            "templateVersion": 1,
            "phaseId": effect,
            "durationMs": 1000,
            "fps": 10,
            "frameCount": 10,
            "playbackMode": "once",
            "layers": [
                {
                    "slot": "robotOverlay",
                    "sdPath": f"sd://tbot/lesson-assets/test/robot.{effect}%40v1",
                }
            ],
        }
        for effect in ("flyIn", "walk", "teach", "listen", "thinking", "celebrate", "exit")
    }
    runtime._steps[0]["scene"]["robotOverlay"]["asset"] = {
        "src": "https://assets.test/robot.walk@v1"
    }
    runtime.asset_cache.local_pack_url_for_source = lambda source: (
        "sd://tbot/lesson-assets/test/robot.walk%40v1"
        if source == "https://assets.test/robot.walk@v1"
        else None
    )
    runtime._cinematic_phase = runtime._layered_cinematic_phases["flyIn"]
    return runtime


def _runtime_from_manifest(manifest: dict) -> LessonRuntime:
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
            "playful_interactions_enabled": True,
            "rollout_device_allowlist": ["robot-v4"],
            "asset_delivery_mode": "sd_pack",
            "frame_ack_timeout_sec": 60,
        }
    }
    assignment = legacy._build_assignment()
    assignment.update(lessonId="farm-english", lessonVersion=7)
    with mock.patch("core.lesson.runtime.uuid.uuid4", return_value="lesson-session"):
        runtime = LessonRuntime(
            conn,
            assignment=assignment,
            manifest=manifest,
            asset_cache=conversation_fixtures._ConversationAssetCache(manifest),
            forwarder=legacy._FakeForwarder(),
            manifest_checksum=legacy._manifest_checksum(),
        )
    conn.lesson_runtime = runtime
    return runtime


async def _activate(runtime: LessonRuntime, step_index: int) -> None:
    assert await runtime.preload_only()
    runtime.state = S_RUNNING
    runtime._step_index = step_index
    runtime._step = runtime._steps[step_index]
    runtime._step_id = runtime._step["id"]
    runtime._step_seq = 20 + step_index
    runtime._step_acked = True
    runtime._step_visuals_ready = True
    runtime._step_completed = False
    runtime._child_response_window_open = True


def _v5_ack(runtime: LessonRuntime, frame: dict, inbound_sequence: int) -> dict:
    command = frame["body"].get("cinematicPhase", frame["body"])
    event = "frameZeroReady" if command["command"] == "prepare" else "phaseReady"
    return {
        "type": "lesson_ack",
        "protocolVersion": RENDERER_V5,
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
                "event": event,
                "command": command["command"],
                "phaseId": command["phaseId"],
                "commandSequenceId": command["commandSequenceId"],
                "accepted": True,
                event: True,
            },
        },
    }


@pytest.mark.asyncio
async def test_v4_indexes_authored_farm_cues_by_target_word_and_effect() -> None:
    runtime = _runtime()

    assert await runtime.preload_only()
    assert runtime._cinematic_phase["cueId"] == "barn-opening"
    assert runtime._cinematic_cue("greet", step_key="barn")["cueId"] == "barn-greet"
    for word in ("barn", "hay"):
        for effect in (
            "listen",
            "thinking",
            "retry-level-1",
            "retry-level-2",
            "retry-level-3",
            "correct",
            "celebrate",
        ):
            assert runtime._cinematic_cue(effect, step_key=word)["cueId"] == f"{word}-{effect}"


@pytest.mark.asyncio
async def test_v4_selects_opening_for_first_runtime_step_when_phases_are_shuffled() -> None:
    manifest = _manifest()
    manifest["cinematicPhases"] = list(reversed(manifest["cinematicPhases"]))
    runtime = _runtime_from_manifest(manifest)

    assert await runtime.preload_only()
    assert runtime._cinematic_phase["cueId"] == "barn-opening"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "missing_cue",
    ["barn-opening", "barn-greet", "barn-listen", "hay-celebrate"],
)
async def test_v4_preload_rejects_missing_safe_speaking_cue_matrix(
    missing_cue: str,
) -> None:
    manifest = _manifest()
    manifest["cinematicPhases"] = [
        cue for cue in manifest["cinematicPhases"] if cue["cueId"] != missing_cue
    ]
    runtime = _runtime_from_manifest(manifest)

    with pytest.raises(Exception) as exc_info:
        await runtime.preload_only()

    assert getattr(exc_info.value, "code", None) == "CINEMATIC_PHASE_ROUTE_MISSING"
    assert missing_cue in str(exc_info.value)


@pytest.mark.asyncio
async def test_safe_speaking_step_entry_routes_greet_then_listen_and_next_word_listen() -> None:
    runtime = _runtime()
    assert await runtime.preload_only()
    runtime.state = S_RUNNING

    with mock.patch.object(runtime, "_queue_authored_cinematic_sequence") as queue:
        await runtime._emit_step()
        queue.assert_called_once_with(["greet", "listen"])
        queue.reset_mock()
        await runtime._emit_step()
        queue.assert_called_once_with(["listen"])


@pytest.mark.asyncio
async def test_v5_step_entry_uses_typed_cinematic_frames_not_legacy_lesson_step() -> None:
    runtime = _v5_runtime()
    runtime.state = S_RUNNING

    await runtime._emit_step()
    await asyncio.sleep(0)

    frames = [json.loads(payload) for payload in runtime.conn.websocket.sent]
    assert all(frame["type"] != "lesson_step" for frame in frames)
    assert frames[-1]["type"] == "lesson_prepare"
    assert frames[-1]["body"]["cinematicPhase"]["phaseId"] == "walk"


@pytest.mark.asyncio
async def test_v5_authored_effect_prepares_and_starts_exact_phase() -> None:
    runtime = _v5_runtime()
    runtime.state = S_RUNNING
    runtime._step_index = 1
    runtime._step = runtime._steps[1]
    runtime._step_id = runtime._step["id"]
    runtime._step_seq = 21
    runtime._step_acked = True
    runtime._step_visuals_ready = True
    runtime._child_response_window_open = True

    task = asyncio.create_task(runtime._apply_authored_cinematic_effect("thinking"))
    await asyncio.sleep(0)
    prepare = json.loads(runtime.conn.websocket.sent[-1])
    assert prepare["body"]["cinematicPhase"]["phaseId"] == "thinking"

    await runtime.on_lesson_ack(_v5_ack(runtime, prepare, 1))
    start = json.loads(runtime.conn.websocket.sent[-1])
    assert start["type"] == "lesson_start"
    assert start["body"]["cinematicPhase"]["phaseId"] == "thinking"
    await runtime.on_lesson_ack(_v5_ack(runtime, start, 2))

    assert await task is True


@pytest.mark.asyncio
async def test_v5_initial_lesson_start_ack_forwards_first_step_started() -> None:
    runtime = _v5_runtime()
    runtime.state = S_RUNNING

    await runtime._emit("lesson_start", body=runtime._start_body())
    start = json.loads(runtime.conn.websocket.sent[-1])
    assert start["stepId"] is None

    await runtime.on_lesson_ack(_v5_ack(runtime, start, 1))

    step_started = [
        event
        for batch in runtime.forwarder.batches
        for event in batch.get("events", [])
        if event.get("type") == "step_started"
    ]
    assert step_started == [
        {
            "type": "step_started",
            "sequence": 1,
            "stepId": runtime._steps[0]["id"],
            "stepType": runtime._steps[0]["type"],
            "retryCount": 0,
        }
    ]


@pytest.mark.asyncio
async def test_v5_accepted_lesson_start_ack_forwards_step_started_once() -> None:
    runtime = _v5_runtime()
    runtime.state = S_RUNNING
    runtime._step_index = 1
    runtime._step = runtime._steps[1]
    runtime._step_id = runtime._step["id"]
    runtime._step_seq = 21
    runtime._step_acked = True
    runtime._step_visuals_ready = True
    runtime._child_response_window_open = True

    task = asyncio.create_task(runtime._apply_authored_cinematic_effect("thinking"))
    await asyncio.sleep(0)
    prepare = json.loads(runtime.conn.websocket.sent[-1])
    await runtime.on_lesson_ack(_v5_ack(runtime, prepare, 1))
    start = json.loads(runtime.conn.websocket.sent[-1])
    start_ack = _v5_ack(runtime, start, 2)
    start_ack["body"]["telemetry"] = {
        "internalMinimumFreeBytes": 24_576,
        "psramFreeBytes": 1_500_000,
    }

    await runtime.on_lesson_ack(start_ack)
    await runtime.on_lesson_ack(start_ack)

    assert await task is True

    second_task = asyncio.create_task(runtime._apply_authored_cinematic_effect("listen"))
    await asyncio.sleep(0)
    second_prepare = json.loads(runtime.conn.websocket.sent[-1])
    await runtime.on_lesson_ack(_v5_ack(runtime, second_prepare, 3))
    second_start = json.loads(runtime.conn.websocket.sent[-1])
    await runtime.on_lesson_ack(_v5_ack(runtime, second_start, 4))
    assert await second_task is True

    step_started = [
        event
        for batch in runtime.forwarder.batches
        for event in batch.get("events", [])
        if event.get("type") == "step_started"
    ]
    assert step_started == [
        {
            "type": "step_started",
            "sequence": 2,
            "stepId": runtime._step_id,
            "stepType": runtime._step["type"],
            "sramFreeBytes": 24_576,
            "psramFreeBytes": 1_500_000,
            "retryCount": 0,
        }
    ]


@pytest.mark.asyncio
async def test_v5_cinematic_ack_rejects_wrong_envelope_step_id() -> None:
    runtime = _v5_runtime()
    runtime.state = S_RUNNING
    runtime._step_index = 1
    runtime._step = runtime._steps[1]
    runtime._step_id = runtime._step["id"]
    runtime._step_seq = 21
    runtime._step_acked = True
    runtime._step_visuals_ready = True
    runtime._child_response_window_open = True

    task = asyncio.create_task(runtime._apply_authored_cinematic_effect("thinking"))
    await asyncio.sleep(0)
    prepare = json.loads(runtime.conn.websocket.sent[-1])
    await runtime.on_lesson_ack(_v5_ack(runtime, prepare, 1))
    start = json.loads(runtime.conn.websocket.sent[-1])
    wrong_step_ack = _v5_ack(runtime, start, 2)
    wrong_step_ack["stepId"] = "different-step"

    await runtime.on_lesson_ack(wrong_step_ack)

    assert start["sequence"] in runtime._outstanding
    assert not task.done()
    assert all(
        event.get("type") != "step_started"
        for batch in runtime.forwarder.batches
        for event in batch.get("events", [])
    )

    await runtime.on_lesson_ack(_v5_ack(runtime, start, 2))
    assert await task is True


@pytest.mark.asyncio
async def test_v5_terminal_completion_emits_typed_cinematic_stop() -> None:
    runtime = _v5_runtime()
    last_step = len(runtime._steps) - 1
    runtime.state = S_RUNNING
    runtime._step_index = last_step
    runtime._step = runtime._steps[last_step]
    runtime._step_id = runtime._step["id"]
    runtime._step_seq = 20 + last_step
    runtime._step_acked = True
    runtime._cinematic_phase = runtime._layered_cinematic_phases["exit"]
    runtime._step_completed = True

    await runtime._maybe_finish_step()

    stop = json.loads(runtime.conn.websocket.sent[-1])
    assert stop["type"] == "lesson_stop"
    assert stop["body"]["reason"] == "COMPLETED"
    assert stop["body"]["cinematicPhase"] == {
        "command": "stop",
        "phaseId": "exit",
        "commandSequenceId": stop["sequence"],
    }


@pytest.mark.asyncio
async def test_authored_safe_speaking_effect_prepares_and_starts_exact_cue() -> None:
    runtime = _runtime()
    await _activate(runtime, 1)

    task = asyncio.create_task(runtime._apply_authored_cinematic_effect("thinking"))
    await asyncio.sleep(0)
    prepare = json.loads(runtime.conn.websocket.sent[-1])
    assert prepare["body"]["cinematicPhase"]["cueId"] == "hay-thinking"

    await runtime.on_lesson_ack(conversation_fixtures._ack(runtime, prepare, 1))
    start = json.loads(runtime.conn.websocket.sent[-1])
    assert start["body"]["cueId"] == "hay-thinking"
    await runtime.on_lesson_ack(conversation_fixtures._ack(runtime, start, 2))

    assert await task is True


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout_stage", ["prepare", "start"])
async def test_authored_cinematic_ack_timeout_retires_pending_future(
    timeout_stage: str,
) -> None:
    runtime = _runtime()
    runtime.conn.config["lesson"]["frame_ack_max_retries"] = 0
    await _activate(runtime, 0)
    sleep_gates: list[asyncio.Event] = []

    async def controlled_sleep(_seconds: float) -> None:
        gate = asyncio.Event()
        sleep_gates.append(gate)
        await gate.wait()

    runtime._sleep = controlled_sleep
    task = asyncio.create_task(runtime._apply_authored_cinematic_effect("thinking"))
    while len(sleep_gates) < 1:
        await asyncio.sleep(0)
    prepare = json.loads(runtime.conn.websocket.sent[-1])
    if timeout_stage == "start":
        await runtime.on_lesson_ack(conversation_fixtures._ack(runtime, prepare, 1))
        while len(sleep_gates) < 2:
            await asyncio.sleep(0)
        sleep_gates[1].set()
    else:
        sleep_gates[0].set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert await task is False
    assert runtime._authored_cinematic_pending is None
    assert runtime.state != S_RUNNING


@pytest.mark.asyncio
async def test_terminal_cleanup_retires_authored_cinematic_future() -> None:
    runtime = _runtime()
    await _activate(runtime, 0)
    task = asyncio.create_task(runtime._apply_authored_cinematic_effect("thinking"))
    await asyncio.sleep(0)

    runtime.state = "FAILED"
    await runtime._notify_lesson_terminal("test_terminal")

    assert await task is False
    assert runtime._authored_cinematic_pending is None


@pytest.mark.asyncio
async def test_authored_cinematic_start_send_failure_retires_pending_future() -> None:
    runtime = _runtime()
    await _activate(runtime, 0)
    task = asyncio.create_task(runtime._apply_authored_cinematic_effect("thinking"))
    await asyncio.sleep(0)
    prepare = json.loads(runtime.conn.websocket.sent[-1])

    with mock.patch.object(
        runtime, "_send", new=mock.AsyncMock(side_effect=RuntimeError("send failed"))
    ):
        await runtime.on_lesson_ack(conversation_fixtures._ack(runtime, prepare, 1))

    assert await task is False
    assert runtime._authored_cinematic_pending is None
    assert runtime.state != S_RUNNING


@pytest.mark.asyncio
async def test_safe_speaking_routes_three_failures_to_three_authored_retry_levels() -> None:
    runtime = _runtime()
    await _activate(runtime, 0)
    runtime._maybe_finish_step = mock.AsyncMock()

    with mock.patch.object(
        runtime, "_apply_authored_cinematic_effect", new=mock.AsyncMock(return_value=True)
    ) as apply_effect:
        for _ in range(3):
            runtime._child_response_window_open = True
            assert await runtime.on_child_response("cat")

    assert [call.args[0] for call in apply_effect.await_args_list] == [
        "thinking",
        "retry-level-1",
        "thinking",
        "retry-level-2",
        "thinking",
        "retry-level-3",
    ]
    assert runtime._safe_speaking().attempts == 3
    assert runtime._step_completed is True


@pytest.mark.asyncio
async def test_child_response_is_fenced_while_thinking_cue_is_awaited() -> None:
    runtime = _runtime()
    await _activate(runtime, 0)
    timeout_block = asyncio.create_task(asyncio.Event().wait())
    runtime._child_response_timeout_task = timeout_block
    thinking_started = asyncio.Event()
    release_thinking = asyncio.Event()

    async def blocked_effect(effect: str) -> bool:
        assert effect == "thinking"
        thinking_started.set()
        await release_thinking.wait()
        return True

    with mock.patch.object(
        runtime, "_apply_authored_cinematic_effect", side_effect=blocked_effect
    ):
        response_task = asyncio.create_task(runtime.on_child_response("barn"))
        await thinking_started.wait()
        assert runtime._child_response_window_open is False
        assert timeout_block.cancelled()
        runtime._step_index = 1
        runtime._step = runtime._steps[1]
        runtime._step_id = "hay"
        runtime._step_seq = 21
        release_thinking.set()
        assert await response_task is False

    assert runtime._safe_speaking_session is None


@pytest.mark.asyncio
@pytest.mark.parametrize("step_index,word", [(0, "barn"), (1, "hay")])
async def test_safe_speaking_correct_routes_thinking_correct_and_celebrate(
    step_index: int, word: str
) -> None:
    runtime = _runtime()
    await _activate(runtime, step_index)
    runtime._maybe_finish_step = mock.AsyncMock()

    with mock.patch.object(
        runtime, "_apply_authored_cinematic_effect", new=mock.AsyncMock(return_value=True)
    ) as apply_effect:
        assert await runtime.on_child_response(word)

    assert [call.args[0] for call in apply_effect.await_args_list] == [
        "thinking",
        "correct",
        "celebrate",
    ]
