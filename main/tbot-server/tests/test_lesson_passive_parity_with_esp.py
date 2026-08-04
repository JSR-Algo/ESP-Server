from __future__ import annotations

import copy
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

import test_lesson_runtime as runtime_fixtures
import test_lesson_runtime_branch_gaps as cinematic_fixtures

from core.lesson.flattened_cinematic_contract import project_flattened_cinematic_phase


def _frames(runtime) -> list[dict]:
    return [json.loads(payload) for payload in runtime.conn.websocket.sent]


def test_flattened_template_v1_projection_remains_byte_compatible() -> None:
    phase = cinematic_fixtures._flattened_manifest()["cinematicPhases"][0]
    cache = cinematic_fixtures._FlattenedAssetCache()
    pack = cache.asset_pack_manifest(
        assignment_version=1,
        lesson_id="lesson",
        lesson_version=4,
        manifest_checksum=runtime_fixtures._manifest_checksum(),
    )

    projected = project_flattened_cinematic_phase(phase, pack)
    expected = {
        "templateId": "flattenedMjpegCinematic",
        "templateVersion": 1,
        "phaseId": "opening",
        "durationMs": 9000,
        "fps": 10,
        "frameCount": 90,
        "asset": {
            "derivativeId": "d" * 64,
            "phaseId": "opening",
            "sdPath": f"{pack['localRoot']}/flattenedCinematic.opening",
            "sha256": "a" * 64,
            "bytes": 1234,
            "mediaType": "video/mp4",
            "width": 480,
            "height": 320,
        },
    }

    assert json.dumps(projected, separators=(",", ":")) == json.dumps(
        expected, separators=(",", ":")
    )


def test_flattened_template_v2_uses_cue_identity_without_relabeling_legacy() -> None:
    cue = cinematic_fixtures._flattened_v2_manifest()["cinematicPhases"][0]
    cache = cinematic_fixtures._FlattenedV2AssetCache()
    pack = cache.asset_pack_manifest(
        assignment_version=1,
        lesson_id="lesson",
        lesson_version=4,
        manifest_checksum=runtime_fixtures._manifest_checksum(),
    )

    projected = project_flattened_cinematic_phase(cue, pack)

    assert projected["cueId"] == "barn-opening"
    assert projected["asset"]["cueId"] == "barn-opening"
    assert projected["playbackMode"] == "once"
    assert "phaseId" not in projected
    assert "phaseId" not in projected["asset"]


@pytest.mark.asyncio
async def test_renderer_v3_passive_step_still_auto_advances_on_real_ack() -> None:
    manifest = copy.deepcopy(cinematic_fixtures._cinematic_manifest())
    manifest["steps"][0]["type"] = "greeting"
    manifest["steps"][0]["completionClass"] = "passive"
    runtime = cinematic_fixtures._cinematic_runtime(manifest=manifest)

    await runtime.start()
    prepare = _frames(runtime)[-1]
    await runtime.on_lesson_ack(
        cinematic_fixtures.CinematicRuntimeTest._ack(runtime, prepare, 1)
    )
    start = _frames(runtime)[-1]
    await runtime.on_lesson_ack(
        cinematic_fixtures.CinematicRuntimeTest._ack(runtime, start, 2)
    )
    step = _frames(runtime)[-1]
    assert step["type"] == "lesson_step"
    assert step["protocolVersion"] == "teebot-lesson-renderer.v3"
    assert step["body"]["completionClass"] == "passive"

    ack = runtime_fixtures._ack(step["sequence"], 3, step_id=step["stepId"])
    ack["protocolVersion"] = "teebot-lesson-renderer.v3"
    await runtime.on_lesson_ack(ack)

    assert runtime._steps_completed == 1
    assert _frames(runtime)[-1]["type"] == "lesson_stop"
