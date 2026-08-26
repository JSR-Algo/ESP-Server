from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.lesson.flattened_cinematic_contract import (
    FlattenedCinematicContractError,
    project_flattened_cinematic_phase,
    validate_flattened_cinematic_manifest,
)
from core.lesson.course_mode_compatibility import (
    course_mode_compatibility_for_manifest,
    validate_course_mode_compatibility,
)
from core.lesson.runtime import LessonRuntime, _manifest_asset_cache_inputs
from core.lesson.sd_pack_mcp_payload import (
    FirmwareSyncPackError,
    build_firmware_sync_pack,
)

FIXTURES = Path(__file__).parent / "fixtures" / "course-mode"
CONTRACT_CHECKSUM = "cf12b1a5f71f0a80a8ee22bb2cdc775ada5b803e26d154e5d29c76b14c9fb264"
MANIFEST_CHECKSUM = "205784b3f97cb081ce9c226d8fd83fdd400401e706c000e1b09ba4e7ebdf36ce"
V5_MANIFEST_CHECKSUM = "e8ee7ff1fb67e8dbd0f8c6908b09c4a4f8e0d1cf3ce41bb38142da0fc03519dc"
LAYOUT_CONTRACT = "renderer-v4.course-mode-layout.v1"
MARKER = {
    "schemaVersion": 1,
    "contractChecksum": CONTRACT_CHECKSUM,
    "layoutContract": LAYOUT_CONTRACT,
    "lessonId": "course-mode-pilot-cat-ball",
    "lessonVersion": 1,
    "manifestChecksum": MANIFEST_CHECKSUM,
}
V5_MARKER = {
    "schemaVersion": 1,
    "contractChecksum": CONTRACT_CHECKSUM,
    "layoutContract": "layeredCinematic",
    "lessonId": "course-mode-v5-farm-candidate",
    "lessonVersion": 2,
    "manifestChecksum": V5_MANIFEST_CHECKSUM,
}


def _pilot_manifest() -> dict:
    contract = json.loads(
        (FIXTURES / "course-mode-pilot-cat-ball.json").read_text(encoding="utf-8")
    )
    persistence = json.loads(
        (FIXTURES / "course-mode-pilot-cat-ball.persistence-v1.json").read_text(
            encoding="utf-8"
        )
    )
    phases = []
    for cue in persistence["cues"]:
        derivative = cue["derivative"]
        phases.append(
            {
                "templateId": "flattenedMjpegCinematic",
                "templateVersion": 2,
                "cueId": cue["cueId"],
                "effect": cue["effect"],
                "stepKey": cue["stepKey"],
                "playbackMode": cue["playbackMode"],
                "timing": {"durationMs": derivative["durationMs"]},
                "asset": {
                    "derivativeId": derivative["derivativeId"],
                    "path": f"lessons/{derivative['path']}",
                    "url": f"https://cdn.example/lessons/{derivative['path']}",
                    "sha256": derivative["sha256"],
                    "bytes": derivative["bytes"],
                    "mediaType": "video/mp4",
                    "width": derivative["width"],
                    "height": derivative["height"],
                    "metadata": {
                        "codec": "mjpeg",
                        "fps": derivative["fps"],
                        "durationMs": derivative["durationMs"],
                        "frameCount": derivative["frameCount"],
                        "hasAudio": False,
                    },
                },
            }
        )
    return {
        "manifestVersion": "teebot-lesson-renderer.v4",
        "protocolVersion": "teebot-lesson-renderer.v4",
        "features": {
            "lessonRendererV4": {
                "flattenedMjpegCinematic": True,
                "assetSource": "publishedFlattenedDerivative",
            }
        },
        "courseModeContract": contract,
        "cinematicPhases": phases,
        "assets": [],
    }


def _pilot_pack(manifest: dict) -> dict:
    cache_key = f"course-mode-pilot-cat-ball/v1-{MANIFEST_CHECKSUM}"
    local_root = f"/sdcard/tbot/lesson-assets/{cache_key}"
    assets = []
    for phase in manifest["cinematicPhases"]:
        source = phase["asset"]
        key = f"flattenedCinematic.{phase['cueId']}"
        assets.append(
            {
                "key": key,
                "path": source["path"],
                "url": source["url"],
                "onlineUrl": source["url"],
                "sha256": source["sha256"],
                "size": source["bytes"],
                "mediaType": source["mediaType"],
                "critical": True,
                "layer": "flattenedCinematic",
                "role": phase["cueId"],
                "state": "READY",
                "checksumOk": True,
                "localPath": f"{local_root}/{key}",
                "sdPath": f"{local_root}/{key}",
                "derivativeId": source["derivativeId"],
                "cueId": phase["cueId"],
                "effect": phase["effect"],
                "stepKey": phase["stepKey"],
                "playbackMode": phase["playbackMode"],
                "compatibilityMetadata": {
                    "codec": source["metadata"]["codec"],
                    "width": source["width"],
                    "height": source["height"],
                    "fps": source["metadata"]["fps"],
                    "durationMs": source["metadata"]["durationMs"],
                    "frameCount": source["metadata"]["frameCount"],
                    "hasAudio": source["metadata"]["hasAudio"],
                },
                "courseModeCompatibility": deepcopy(MARKER),
            }
        )
    return {
        "assignmentVersion": 1,
        "lessonId": "course-mode-pilot-cat-ball",
        "lessonVersion": 1,
        "manifestChecksum": MANIFEST_CHECKSUM,
        "cacheKey": cache_key,
        "localRoot": local_root,
        "ready": True,
        "courseModeCompatibility": deepcopy(MARKER),
        "assets": assets,
    }


def _pilot_v5_identity() -> dict:
    return json.loads(
        (FIXTURES / "course-mode-pilot-cat-ball-v2.json").read_text(encoding="utf-8")
    )


def test_reviewed_renderer_v5_identity_returns_exact_marker_without_changing_v1() -> None:
    assert validate_course_mode_compatibility(MARKER) is True
    assert course_mode_compatibility_for_manifest(
        _pilot_manifest(), manifest_checksum=MANIFEST_CHECKSUM
    ) == MARKER

    marker = course_mode_compatibility_for_manifest(
        _pilot_v5_identity(), manifest_checksum=V5_MANIFEST_CHECKSUM
    )

    assert marker == V5_MARKER
    assert validate_course_mode_compatibility(marker) is True


@pytest.mark.parametrize(
    "mutate",
    [
        lambda manifest: manifest.pop("evidenceState"),
        lambda manifest: manifest.update(evidenceState="unreviewed"),
        lambda manifest: manifest["lesson"].update(manifestChecksum=MANIFEST_CHECKSUM),
        lambda manifest: manifest["bundle"].update(checksum=MANIFEST_CHECKSUM),
        lambda manifest: manifest["sharedAssets"][1].update(
            versionId="75000000-0000-4000-8000-000000000021"
        ),
        lambda manifest: manifest["sharedAssets"][0].update(sha256="0" * 64),
        lambda manifest: manifest["sharedAssets"][1].update(bytes=200618),
        lambda manifest: manifest["phaseIdentity"][0]["layers"][1]["metadata"][
            "rect"
        ].update(width=96),
        lambda manifest: manifest["phaseIdentity"][0]["layers"][2]["metadata"][
            "chromaKey"
        ].update(tolerance=21),
        lambda manifest: manifest["cuePhases"][0].update(phaseId="listen"),
        lambda manifest: manifest["manifestIdentityProjection"]["visualRefs"][0].update(
            bytes=43600
        ),
        lambda manifest: manifest["manifestIdentityProjection"]["assets"][1].update(
            sha256="0" * 64
        ),
        lambda manifest: manifest["manifestIdentityProjection"]["steps"][0].update(
            prompt="Altered prompt"
        ),
        lambda manifest: manifest["manifestIdentityProjection"]["steps"][0].pop(
            "robotState"
        ),
        lambda manifest: manifest["manifestIdentityProjection"]["steps"].reverse(),
        lambda manifest: manifest["manifestIdentityProjection"]["visualRefs"][0].update(
            stepKey=None
        ),
        lambda manifest: manifest["manifestIdentityProjection"]["visualRefs"][0].update(
            slot=1
        ),
    ],
)
def test_renderer_v5_identity_rejects_unreviewed_or_mixed_identity(
    mutate,
) -> None:
    manifest = _pilot_v5_identity()
    mutate(manifest)

    assert (
        course_mode_compatibility_for_manifest(
            manifest, manifest_checksum=V5_MANIFEST_CHECKSUM
        )
        is None
    )


def test_generic_or_unreviewed_renderer_v5_manifest_has_no_course_mode_marker() -> None:
    identity = _pilot_v5_identity()
    manifest = deepcopy(identity["manifestIdentityProjection"])

    assert course_mode_compatibility_for_manifest(
        manifest, manifest_checksum=V5_MANIFEST_CHECKSUM
    ) == V5_MARKER

    manifest.pop("courseModeContract")
    assert (
        course_mode_compatibility_for_manifest(
            manifest, manifest_checksum=V5_MANIFEST_CHECKSUM
        )
        is None
    )


def test_exact_frozen_course_mode_mp4_cues_project_with_fail_closed_marker() -> None:
    manifest = _pilot_manifest()

    validate_flattened_cinematic_manifest(
        manifest, manifest_checksum=MANIFEST_CHECKSUM
    )
    projected_assets = _manifest_asset_cache_inputs(
        manifest, manifest_checksum=MANIFEST_CHECKSUM
    )
    assert len(projected_assets) == 8
    assert all(asset["courseModeCompatibility"] == MARKER for asset in projected_assets)

    pack = _pilot_pack(manifest)
    cue = project_flattened_cinematic_phase(
        manifest["cinematicPhases"][0], pack, course_mode_compatibility=MARKER
    )
    assert cue["courseModeCompatibility"] == MARKER
    assert cue["asset"]["courseModeCompatibility"] == MARKER
    assert cue["durationMs"] == 2000
    assert cue["frameCount"] == 20
    assert cue["playbackMode"] == "once"

    runtime = object.__new__(LessonRuntime)
    runtime.assignment_version = 1
    runtime.profile = "espTft"
    runtime.lesson_id = "course-mode-pilot-cat-ball"
    runtime.lesson_version = 1
    runtime.manifest_checksum = MANIFEST_CHECKSUM
    runtime.manifest = manifest
    runtime.asset_cache = SimpleNamespace(preload_timeout_sec=90)
    runtime._lesson_rollout_control_enabled = lambda _name: False
    runtime._use_sd_asset_pack = lambda: False
    runtime._cinematic_enabled = lambda: True
    runtime._cinematic_phase = cue
    prepare = runtime._prepare_body()["cinematicPhase"]
    assert prepare["command"] == "prepare"
    assert prepare["courseModeCompatibility"] == MARKER
    assert prepare["asset"]["courseModeCompatibility"] == MARKER


def test_exact_frozen_course_mode_accepts_only_the_approved_local_http_asset_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = _pilot_manifest()
    for phase in manifest["cinematicPhases"]:
        phase["asset"]["url"] = phase["asset"]["url"].replace(
            "https://cdn.example/", "http://192.168.0.120:8102/"
        )

    with pytest.raises(FlattenedCinematicContractError):
        validate_flattened_cinematic_manifest(
            manifest, manifest_checksum=MANIFEST_CHECKSUM
        )

    monkeypatch.setenv("LESSON_COURSE_MODE_LOCAL_HTTP_ASSET_ENABLED", "true")
    validate_flattened_cinematic_manifest(
        manifest, manifest_checksum=MANIFEST_CHECKSUM
    )

    generic_manifest = deepcopy(manifest)
    generic_manifest.pop("courseModeContract")
    with pytest.raises(FlattenedCinematicContractError):
        validate_flattened_cinematic_manifest(
            generic_manifest, manifest_checksum=MANIFEST_CHECKSUM
        )

    manifest["cinematicPhases"][0]["asset"]["url"] = manifest[
        "cinematicPhases"
    ][0]["asset"]["url"].replace("192.168.0.120", "192.168.0.121")
    with pytest.raises(FlattenedCinematicContractError) as exc_info:
        validate_flattened_cinematic_manifest(
            manifest, manifest_checksum=MANIFEST_CHECKSUM
        )

    assert exc_info.value.code == "CINEMATIC_METADATA_MISMATCH"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda manifest: manifest.pop("courseModeContract"),
        lambda manifest: manifest["courseModeContract"].update(contractChecksum="0" * 64),
        lambda manifest: manifest["courseModeContract"]["renderer"].update(
            visualLayoutContract="renderer-v4.course-mode-layout.v0"
        ),
        lambda manifest: manifest["cinematicPhases"][0]["asset"].update(sha256="0" * 64),
    ],
)
def test_course_mode_marker_is_absent_and_pilot_timing_rejects_on_identity_mismatch(
    mutate,
) -> None:
    manifest = _pilot_manifest()
    mutate(manifest)

    assert all(
        "courseModeCompatibility" not in asset
        for asset in _manifest_asset_cache_inputs(
            manifest, manifest_checksum=MANIFEST_CHECKSUM
        )
    )
    with pytest.raises(FlattenedCinematicContractError):
        validate_flattened_cinematic_manifest(
            manifest, manifest_checksum=MANIFEST_CHECKSUM
        )


def test_generic_renderer_v4_stale_mp4_cue_still_rejects() -> None:
    manifest = _pilot_manifest()
    manifest.pop("courseModeContract")
    manifest["cinematicPhases"] = [manifest["cinematicPhases"][0]]

    with pytest.raises(FlattenedCinematicContractError) as exc_info:
        validate_flattened_cinematic_manifest(
            manifest, manifest_checksum=MANIFEST_CHECKSUM
        )

    assert exc_info.value.code == "CINEMATIC_METADATA_MISMATCH"


def test_generic_cinematic_prepare_does_not_emit_course_mode_marker() -> None:
    runtime = object.__new__(LessonRuntime)
    runtime.assignment_version = 1
    runtime.profile = "espTft"
    runtime.lesson_id = "lesson-a"
    runtime.lesson_version = 1
    runtime.manifest_checksum = "b" * 64
    runtime.manifest = {"assets": []}
    runtime.asset_cache = SimpleNamespace(preload_timeout_sec=90)
    runtime._lesson_rollout_control_enabled = lambda _name: False
    runtime._use_sd_asset_pack = lambda: False
    runtime._cinematic_enabled = lambda: True
    runtime._cinematic_phase = {"templateVersion": 2, "cueId": "barn-listen"}

    prepare = runtime._prepare_body()["cinematicPhase"]

    assert "courseModeCompatibility" not in prepare


def test_firmware_pack_and_prepare_projection_preserve_exact_marker() -> None:
    manifest = _pilot_manifest()
    pack = _pilot_pack(manifest)

    firmware_pack = build_firmware_sync_pack(pack)
    assert firmware_pack["courseModeCompatibility"] == MARKER
    assert all(
        asset["courseModeCompatibility"] == MARKER
        for asset in firmware_pack["assets"]
    )

    compact = LessonRuntime._prepare_asset_pack_payload(pack)
    assert compact["courseModeCompatibility"] == MARKER
    assert all(
        asset["courseModeCompatibility"] == MARKER for asset in compact["assets"]
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda pack: pack.pop("courseModeCompatibility"),
        lambda pack: pack["courseModeCompatibility"].update(manifestChecksum="0" * 64),
        lambda pack: pack["assets"][0].pop("courseModeCompatibility"),
        lambda pack: pack["assets"][0]["courseModeCompatibility"].update(
            contractChecksum="0" * 64
        ),
        lambda pack: pack["assets"].pop(),
    ],
)
def test_firmware_pack_rejects_missing_or_mismatched_course_mode_marker(mutate) -> None:
    pack = _pilot_pack(_pilot_manifest())
    mutate(pack)

    with pytest.raises(FirmwareSyncPackError):
        build_firmware_sync_pack(pack)


def test_cinematic_projection_rejects_missing_course_mode_pack_marker() -> None:
    manifest = _pilot_manifest()
    pack = _pilot_pack(manifest)
    pack.pop("courseModeCompatibility")

    with pytest.raises(FlattenedCinematicContractError) as exc_info:
        project_flattened_cinematic_phase(
            manifest["cinematicPhases"][0],
            pack,
            course_mode_compatibility=MARKER,
        )

    assert exc_info.value.code == "CINEMATIC_IDENTITY_UNSUPPORTED"
