from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from core.lesson.course_mode_compatibility import (
    course_mode_compatibility_for_manifest,
)
from core.lesson.asset_cache import AssetCache, AssetState
from core.lesson.layered_cinematic_contract import (
    LayeredCinematicContractError,
    project_layered_cinematic_phase,
)
from core.lesson.runtime import _manifest_asset_cache_inputs


SHA_BACKGROUND = "a" * 64
SHA_OBJECT = "b" * 64
SHA_ROBOT = "c" * 64
LOCAL_ROOT = "/sdcard/tbot/lesson-assets/w02-feelings/v5-checksum"
FIXTURES = Path(__file__).parent / "fixtures" / "course-mode"
COURSE_MODE_V5_CHECKSUM = "22e94ced4b2dae1ced13f3e34de1f72e8a3ce177e1ba3a7c599a4c3d002aea0d"


def _phase() -> dict:
    return {
        "templateId": "layeredCinematic",
        "templateVersion": 1,
        "phaseId": "teach",
        "timing": {"durationMs": 1000},
        "playbackMode": "once",
        "layers": [
            {
                "layer": "background",
                "slot": "backgroundScene",
                "assetVersionId": "background.classroom@v1",
                "assetKey": "background.classroom",
                "version": 1,
                "sha256": SHA_BACKGROUND,
                "bytes": 1000,
                "metadata": {
                    "mediaKind": "image",
                    "mediaType": "image/jpeg",
                    "width": 480,
                    "height": 320,
                    "rect": {"x": 0, "y": 0, "width": 480, "height": 320},
                    "fit": "cover",
                },
            },
            {
                "layer": "teachingObject",
                "slot": "teachingObject",
                "assetVersionId": "object.happy@v1",
                "assetKey": "object.happy",
                "version": 1,
                "sha256": SHA_OBJECT,
                "bytes": 2000,
                "metadata": {
                    "mediaKind": "image",
                    "mediaType": "image/png",
                    "width": 240,
                    "height": 240,
                    "rect": {"x": 130, "y": 72, "width": 200, "height": 200},
                    "fit": "contain",
                },
            },
            {
                "layer": "robotOverlay",
                "slot": "robotOverlay",
                "assetVersionId": "robot.teach@v1",
                "assetKey": "robot.teach",
                "version": 1,
                "sha256": SHA_ROBOT,
                "bytes": 3000,
                "metadata": {
                    "mediaKind": "video",
                    "mediaType": "video/mp4",
                    "codec": "mjpeg",
                    "hasAudio": False,
                    "width": 240,
                    "height": 240,
                    "fps": 10,
                    "durationMs": 1000,
                    "frameCount": 10,
                    "rect": {"x": 160, "y": 64, "width": 220, "height": 220},
                    "chromaKey": {"keyColor": "#00ff00", "tolerance": 20, "featherPx": 1},
                },
            },
        ],
    }


def _pack() -> dict:
    phase = _phase()
    assets = []
    for layer in phase["layers"]:
        asset_id = layer["assetVersionId"]
        filename = asset_id.replace("@", "-")
        assets.append({
            "key": asset_id,
            "state": "READY",
            "checksumOk": True,
            "localPath": f"{LOCAL_ROOT}/{filename}",
            "sdPath": f"{LOCAL_ROOT}/{filename}",
            "sha256": layer["sha256"],
            "size": layer["bytes"],
            "mediaType": layer["metadata"]["mediaType"],
            "sharedAssetKey": layer["assetKey"],
            "sharedAssetVersion": layer["version"],
            "compatibilityMetadata": deepcopy(layer["metadata"]),
        })
    return {"ready": True, "localRoot": LOCAL_ROOT, "assets": assets}


def _course_mode_v5_identity() -> dict:
    return json.loads(
        (FIXTURES / "course-mode-pilot-cat-ball-v2.json").read_text(encoding="utf-8")
    )


def test_course_mode_v5_fixture_preserves_reviewed_layered_identity() -> None:
    manifest = _course_mode_v5_identity()

    assert course_mode_compatibility_for_manifest(
        manifest, manifest_checksum=COURSE_MODE_V5_CHECKSUM
    ) == {
        "schemaVersion": 1,
        "contractChecksum": "332fb68e340abb94c0178dd83b06ed0939d6e2d63c17d48bcb09dab8cc6bb3be",
        "layoutContract": "layeredCinematic",
        "lessonId": "course-mode-v5-farm-candidate",
        "lessonVersion": 2,
        "manifestChecksum": COURSE_MODE_V5_CHECKSUM,
    }
    assert manifest["cuePhases"] == [
        {"activityId": "cat-discover-center-01", "cueId": "cat-discover", "phaseId": "teach"},
        {"activityId": "cat-meaning-left-right-01", "cueId": "cat-meaning", "phaseId": "listen"},
        {"activityId": "cat-discover-center-01", "cueId": "cat-joint-speech", "phaseId": "teach"},
        {"activityId": "cat-recall-visual-02", "cueId": "cat-recall", "phaseId": "listen"},
        {"activityId": "cat-transfer-scene-01", "cueId": "cat-transfer", "phaseId": "listen"},
        {"activityId": "ball-discover-center-01", "cueId": "ball-discover", "phaseId": "teach"},
        {"activityId": "ball-discover-center-01", "cueId": "ball-meaning", "phaseId": "listen"},
        {"activityId": "cat-delayed-recall-01", "cueId": "cat-delayed", "phaseId": "listen"},
    ]
    assert [
        {
            key: asset[key]
            for key in ("versionId", "assetId", "assetKey", "slot", "sha256", "bytes", "mediaType", "width", "height")
        }
        for asset in manifest["sharedAssets"]
    ] == [
        {
            "versionId": "75000000-0000-4000-8000-000000000011",
            "assetId": "75000000-0000-4000-8000-000000000010",
            "assetKey": "course-mode.v5.scene.farm",
            "slot": "backgroundScene",
            "sha256": "d4abb6087dc3122e0a00feb5e6a86b03dc7db550eb59d25e92f54d0fd09e4fc0",
            "bytes": 43599,
            "mediaType": "image/jpeg",
            "width": 480,
            "height": 320,
        },
        {
            "versionId": "75000000-0000-4000-8000-000000000022",
            "assetId": "75000000-0000-4000-8000-000000000020",
            "assetKey": "course-mode.v5.object.barn",
            "slot": "teachingObject",
            "sha256": "c466239ff8ba202998e3827b6871906d7fbac6232aeaea3a59b7c69bec7d8777",
            "bytes": 15086,
            "mediaType": "image/png",
            "width": 95,
            "height": 95,
        },
        {
            "versionId": "75000000-0000-4000-8000-000000000031",
            "assetId": "75000000-0000-4000-8000-000000000030",
            "assetKey": "course-mode.v5.robot.teach",
            "slot": "robotOverlay",
            "sha256": "f2d496b5e750e895f7e086aec827d7b99d0bb322d73ea660a2e84ff484b602c4",
            "bytes": 223033,
            "mediaType": "video/mp4",
            "width": 240,
            "height": 240,
        },
    ]
    assert manifest["phaseIdentity"] == [
        phase
        for phase in manifest["manifestIdentityProjection"]["cinematicPhases"]
    ]


def test_projects_exact_mixed_media_phase_from_attested_pack() -> None:
    assert project_layered_cinematic_phase(_phase(), _pack()) == {
        "templateId": "layeredCinematic",
        "templateVersion": 1,
        "phaseId": "teach",
        "durationMs": 1000,
        "fps": 10,
        "frameCount": 10,
        "playbackMode": "once",
        "layers": [
            {
                "layer": "background",
                "slot": "backgroundScene",
                "mediaKind": "image",
                "mediaType": "image/jpeg",
                "sdPath": f"{LOCAL_ROOT}/background.classroom-v1",
                "sha256": SHA_BACKGROUND,
                "bytes": 1000,
                "width": 480,
                "height": 320,
                "rect": {"x": 0, "y": 0, "width": 480, "height": 320},
                "fit": "cover",
            },
            {
                "layer": "teachingObject",
                "slot": "teachingObject",
                "mediaKind": "image",
                "mediaType": "image/png",
                "sdPath": f"{LOCAL_ROOT}/object.happy-v1",
                "sha256": SHA_OBJECT,
                "bytes": 2000,
                "width": 240,
                "height": 240,
                "rect": {"x": 130, "y": 72, "width": 200, "height": 200},
                "fit": "contain",
            },
            {
                "layer": "robotOverlay",
                "slot": "robotOverlay",
                "mediaKind": "video",
                "mediaType": "video/mp4",
                "sdPath": f"{LOCAL_ROOT}/robot.teach-v1",
                "sha256": SHA_ROBOT,
                "bytes": 3000,
                "width": 240,
                "height": 240,
                "codec": "mjpeg",
                "hasAudio": False,
                "rect": {"x": 160, "y": 64, "width": 220, "height": 220},
                "chromaKey": {"keyColor": "#00ff00", "tolerance": 20, "featherPx": 1},
            },
        ],
    }


def test_course_mode_v5_projects_activity_aware_fallback_without_teaching_object() -> None:
    phase = _phase()
    phase["activityIds"] = ["w19-weather-guided"]
    phase["layers"].pop(1)
    pack = _pack()
    pack["assets"] = [
        asset for asset in pack["assets"]
        if asset["sharedAssetKey"] != "object.happy"
    ]

    projected = project_layered_cinematic_phase(
        phase,
        pack,
        course_mode_activity_ids={"w19-weather-guided"},
        fallback_activity_ids={"w19-weather-guided"},
    )

    assert projected["activityIds"] == ["w19-weather-guided"]
    assert [layer["layer"] for layer in projected["layers"]] == [
        "background", "robotOverlay"
    ]
    assert projected["fps"] == 10


def test_course_mode_v5_rejects_incomplete_or_unapproved_activity_mapping() -> None:
    phase = _phase()
    phase["activityIds"] = ["unknown-activity"]

    with pytest.raises(LayeredCinematicContractError):
        project_layered_cinematic_phase(
            phase,
            _pack(),
            course_mode_activity_ids={"known-activity"},
            fallback_activity_ids=set(),
        )


def test_non_course_renderer_v5_remains_strict_about_activity_ids_and_three_layers() -> None:
    phase = _phase()
    phase["activityIds"] = ["activity-1"]
    with pytest.raises(LayeredCinematicContractError):
        project_layered_cinematic_phase(phase, _pack())

    phase = _phase()
    phase["layers"].pop(1)
    with pytest.raises(LayeredCinematicContractError):
        project_layered_cinematic_phase(phase, _pack())


def test_runtime_manifest_projection_attests_renderer_v5_without_generation_visual_refs() -> None:
    assets = _manifest_asset_cache_inputs({
        "manifestVersion": "teebot-lesson-renderer.v5",
        "assets": [],
        "cinematicPhases": [_phase()],
    })

    assert len(assets) == 3
    robot = next(asset for asset in assets if asset["layer"] == "robotOverlay")
    assert "visualRefs" not in robot
    assert AssetState(robot).renderer_v5_media is True


def test_runtime_manifest_projection_replaces_generic_v5_assets_with_phase_attestations() -> None:
    phases = []
    for index, effect in enumerate(("flyIn", "walk", "teach", "listen", "thinking", "celebrate", "exit")):
        phase = _phase()
        phase["phaseId"] = effect
        robot = phase["layers"][2]
        robot["assetVersionId"] = f"robot.{effect}@v1"
        robot["assetKey"] = f"robot.{effect}"
        robot["sha256"] = f"{index + 1:x}" * 64
        phases.append(phase)
    unique_layers = {
        layer["assetVersionId"]: layer
        for phase in phases
        for layer in phase["layers"]
    }
    generic_assets = [
        {
            "id": layer["assetVersionId"],
            "assetId": layer["assetVersionId"],
            "assetKey": layer["assetKey"],
            "version": layer["version"],
            "path": f"https://assets.test/{layer['assetVersionId']}",
            "url": f"https://assets.test/{layer['assetVersionId']}",
            "sha256": layer["sha256"],
            "bytes": layer["bytes"],
            "critical": True,
            "layer": layer["slot"],
            "role": "pose",
            "mediaType": layer["metadata"]["mediaType"],
        }
        for layer in unique_layers.values()
    ]
    generic_assets.extend(
        {
            "id": f"robotOverlay.{pose}",
            "path": f"https://assets.test/robotOverlay.{pose}",
            "url": f"https://assets.test/robotOverlay.{pose}",
            "sha256": "f" * 64,
            "bytes": 1000,
            "critical": False,
            "layer": "robotOverlay",
            "role": "pose",
            "mediaType": "image/png",
        }
        for pose in ("teach", "listening", "thinking", "celebrate")
    )

    assets = _manifest_asset_cache_inputs({
        "manifestVersion": "teebot-lesson-renderer.v5",
        "assets": generic_assets,
        "cinematicPhases": phases,
    })

    assert [asset["key"] for asset in assets] == [
        "background.classroom@v1",
        "object.happy@v1",
        "robot.flyIn@v1",
        "robot.walk@v1",
        "robot.teach@v1",
        "robot.listen@v1",
        "robot.thinking@v1",
        "robot.celebrate@v1",
        "robot.exit@v1",
    ]
    assert all(AssetState(asset).renderer_v5_media for asset in assets)
    AssetCache(assets=assets, profile="espTft").assert_profile_renderable()


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda phase, pack: phase["layers"].reverse(), "CINEMATIC_METADATA_MISMATCH"),
        (lambda phase, pack: phase["layers"][0]["metadata"].update(mediaType="image/png"), "CINEMATIC_METADATA_MISMATCH"),
        (lambda phase, pack: phase["layers"][1]["metadata"].update(mediaType="image/jpeg"), "CINEMATIC_METADATA_MISMATCH"),
        (lambda phase, pack: phase["layers"][2]["metadata"].update(mediaKind="image"), "CINEMATIC_METADATA_MISMATCH"),
        (lambda phase, pack: phase["layers"][2]["metadata"].update(codec="h264"), "CINEMATIC_METADATA_MISMATCH"),
        (lambda phase, pack: phase["layers"][2]["metadata"].update(hasAudio=True), "CINEMATIC_METADATA_MISMATCH"),
        (lambda phase, pack: phase["layers"][2]["metadata"].update(frameCount=9), "CINEMATIC_METADATA_MISMATCH"),
        (lambda phase, pack: phase["layers"][2]["metadata"].update(chromaKey=None), "CINEMATIC_METADATA_MISMATCH"),
        (lambda phase, pack: phase["layers"][0]["metadata"]["rect"].update(width=481), "CINEMATIC_METADATA_MISMATCH"),
        (lambda phase, pack: phase["layers"][0].update(sha256="bad"), "CINEMATIC_METADATA_MISMATCH"),
        (lambda phase, pack: pack["assets"][0].update(localPath=f"{LOCAL_ROOT}/../escape"), "CINEMATIC_SD_PATH_MISSING"),
    ],
)
def test_rejects_invalid_layered_cinematic_contract(mutate, code: str) -> None:
    phase = _phase()
    pack = _pack()
    mutate(phase, pack)

    with pytest.raises(LayeredCinematicContractError) as exc_info:
        project_layered_cinematic_phase(phase, pack)

    assert exc_info.value.code == code
