from __future__ import annotations

from copy import deepcopy

import pytest

from core.lesson.layered_cinematic_contract import (
    LayeredCinematicContractError,
    project_layered_cinematic_phase,
)


SHA_BACKGROUND = "a" * 64
SHA_OBJECT = "b" * 64
SHA_ROBOT = "c" * 64
LOCAL_ROOT = "/sdcard/tbot/lesson-assets/w02-feelings/v5-checksum"


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
