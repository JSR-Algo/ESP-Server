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
    validate_layered_cinematic_runtime_asset,
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


def test_uuid_phase_refs_project_to_canonical_shared_cache_keys():
    from uuid import uuid4
    phase = _phase()
    assets = []
    for layer in phase['layers']:
        key = layer['assetVersionId']
        layer['assetVersionId'] = str(uuid4())
        assets.append({'id': key, 'url': 'https://cdn.example.com/' + key,
            'path': 'media/' + key})
    manifest = {'manifestVersion': 'teebot-lesson-renderer.v5',
        'assets': assets, 'cinematicPhases': [phase]}
    projected = _manifest_asset_cache_inputs(manifest)
    assert [a['key'] for a in projected] == [a['id'] for a in assets]
    assert [a['url'] for a in projected] == [a['url'] for a in assets]
    assert all(AssetState(a).renderer_v5_media for a in projected)
    assert project_layered_cinematic_phase(phase, _pack())['phaseId'] == 'teach'
    phase['layers'][0]['assetVersionId'] = 'unrelated@v9'
    with pytest.raises(LayeredCinematicContractError):
        _manifest_asset_cache_inputs(manifest)


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


def _canonical_v5_phase_and_pack() -> tuple[dict, dict]:
    manifest = _course_mode_v5_identity()
    phase = deepcopy(manifest["phaseIdentity"][0])
    shared_by_version = {
        asset["versionId"]: asset for asset in manifest["sharedAssets"]
    }
    assets = []
    for layer in phase["layers"]:
        shared = shared_by_version[layer["assetVersionId"]]
        assets.append({
            "key": layer["assetVersionId"],
            "state": "READY",
            "checksumOk": True,
            "localPath": f"{LOCAL_ROOT}/{layer['assetVersionId']}",
            "sdPath": f"{LOCAL_ROOT}/{layer['assetVersionId']}",
            "sha256": layer["sha256"],
            "size": layer["bytes"],
            "mediaType": layer["metadata"]["mediaType"],
            "sharedAssetKey": layer["assetKey"],
            "sharedAssetVersion": layer["version"],
            "compatibilityMetadata": deepcopy(layer["metadata"]),
            "sourceVersionId": shared["versionId"],
        })
    return phase, {"ready": True, "localRoot": LOCAL_ROOT, "assets": assets}


def test_projects_checked_in_canonical_v5_phase_with_uuid_asset_version_ids() -> None:
    phase, pack = _canonical_v5_phase_and_pack()

    projected = project_layered_cinematic_phase(
        phase,
        pack,
        course_mode_activity_ids=set(phase["activityIds"]),
        fallback_activity_ids=set(),
    )

    assert projected["activityIds"] == phase["activityIds"]
    assert [asset["key"] for asset in pack["assets"]] == [
        "75000000-0000-4000-8000-000000000011",
        "75000000-0000-4000-8000-000000000022",
        "75000000-0000-4000-8000-000000000031",
    ]
    assert [layer["sdPath"] for layer in projected["layers"]] == [
        f"{LOCAL_ROOT}/{asset['key']}" for asset in pack["assets"]
    ]


def test_runtime_sd_pack_inputs_join_uuid_layer_references_to_shared_keys() -> None:
    """Layers reference the published UUID; the SD pack input is keyed by the shared identity.

    Previously pinned as keeping the raw UUID as the pack key. That pin was unreachable on the
    real wire: ``manifest.assets`` are keyed ``assetKey@vN`` (so the UUID key never joined the
    asset's path/url) and ``validate_layered_cinematic_runtime_asset`` refuses a key that is not
    ``f"{sharedAssetKey}@v{sharedAssetVersion}"`` (so no entry could ever become renderer-v5 media).
    Shared-key priority with the UUID as fallback alias is the reviewed wire-identity decision
    (1dc0674); S18 run12 observed the refusal on a real cpr-s03-final52 manifest.
    """
    manifest = _course_mode_v5_identity()["manifestIdentityProjection"]

    assets = _manifest_asset_cache_inputs(manifest)

    assert [asset["key"] for asset in assets] == [
        "course-mode.v5.scene.farm@v1",
        "course-mode.v5.object.barn@v1",
        "course-mode.v5.robot.teach@v1",
    ]
    by_id = {asset["id"]: asset for asset in manifest["assets"]}
    for asset in assets:
        assert asset["path"] == by_id[asset["key"]]["path"]
        assert asset["url"] == by_id[asset["key"]].get("url") or asset["url"] is None
        validate_layered_cinematic_runtime_asset(asset)


@pytest.mark.parametrize("asset_version_id", ["not-a-uuid", "75000000-0000-4000-7000-000000000011"])
def test_rejects_malformed_or_unsupported_uuid_asset_version_identity(asset_version_id: str) -> None:
    phase, pack = _canonical_v5_phase_and_pack()
    phase["layers"][0]["assetVersionId"] = asset_version_id

    with pytest.raises(LayeredCinematicContractError) as exc_info:
        project_layered_cinematic_phase(
            phase,
            pack,
            course_mode_activity_ids=set(phase["activityIds"]),
            fallback_activity_ids=set(),
        )

    assert exc_info.value.code == "CINEMATIC_METADATA_MISMATCH"


def test_rejects_uuid_asset_version_id_not_present_in_attested_pack() -> None:
    phase, pack = _canonical_v5_phase_and_pack()
    phase["layers"][0]["assetVersionId"] = "75000000-0000-4000-8000-000000000099"

    with pytest.raises(LayeredCinematicContractError) as exc_info:
        project_layered_cinematic_phase(
            phase,
            pack,
            course_mode_activity_ids=set(phase["activityIds"]),
            fallback_activity_ids=set(),
        )

    assert exc_info.value.code == "CINEMATIC_SD_PATH_MISSING"


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


def _generation_asset(slot: str, phase: str) -> dict:
    layer = _phase()["layers"][2]
    return {
        "key": "robot.teach@v1",
        "sharedAssetKey": "robot.teach",
        "sharedAssetVersion": 1,
        "mediaType": "video/mp4",
        "compatibilityMetadata": deepcopy(layer["metadata"]),
        "visualRefs": [{"stepKey": "a1", "phase": phase, "slot": slot}],
    }


@pytest.mark.parametrize("slot, phase", [
    ("robotOverlay", "opening"),
    ("robotOverlay.flyIn", "flyIn"),
    ("robotOverlay.exit", "exit"),
])
def test_generation_asset_accepts_phase_bound_robot_slots(slot: str, phase: str) -> None:
    from core.lesson.layered_cinematic_contract import validate_layered_cinematic_generation_asset

    validated = validate_layered_cinematic_generation_asset(_generation_asset(slot, phase))
    assert validated["layer"] == "robotOverlay"
    assert validated["visualRefs"] == [{"stepKey": "a1", "phase": phase, "slot": slot}]


@pytest.mark.parametrize("slot, phase", [
    ("robotOverlay.hover", "hover"),
    ("robotOverlay.flyIn", "teach"),
    ("teachingObject.teach", "teach"),
    ("robotOverlay.", "teach"),
])
def test_generation_asset_rejects_unknown_or_mismatched_phase_slots(slot: str, phase: str) -> None:
    from core.lesson.layered_cinematic_contract import validate_layered_cinematic_generation_asset

    with pytest.raises(LayeredCinematicContractError) as error:
        validate_layered_cinematic_generation_asset(_generation_asset(slot, phase))
    assert error.value.code == "CINEMATIC_METADATA_MISMATCH"


def test_runtime_phase_projection_keeps_loop_playback_and_per_phase_rects() -> None:
    phase = _phase()
    phase["phaseId"] = "flyIn"
    phase["playbackMode"] = "loop"
    phase["layers"][2]["metadata"]["rect"] = {"x": 240, "y": 0, "width": 240, "height": 240}
    pack = _pack()
    pack["assets"][2]["compatibilityMetadata"] = deepcopy(phase["layers"][2]["metadata"])
    projected = project_layered_cinematic_phase(phase, pack)
    assert projected["playbackMode"] == "loop"
    assert projected["layers"][2]["rect"] == {"x": 240, "y": 0, "width": 240, "height": 240}
    phase["phaseId"] = "hover"
    with pytest.raises(LayeredCinematicContractError, match="phase identity is invalid"):
        project_layered_cinematic_phase(phase, pack)
    phase["phaseId"] = "walk"
    phase["layers"][2]["metadata"]["rect"] = {"x": 300, "y": 80, "width": 240, "height": 240}
    pack["assets"][2]["compatibilityMetadata"] = deepcopy(phase["layers"][2]["metadata"])
    with pytest.raises(LayeredCinematicContractError, match="out of bounds"):
        project_layered_cinematic_phase(phase, pack)
