"""Exact reviewed compatibility identities for frozen Course Mode lessons."""

from __future__ import annotations

import copy
import os
from typing import Any
from urllib.parse import SplitResult

from core.lesson.course_mode_contract import CourseModeContract, CourseModeContractError

CONTRACT_CHECKSUM = "cf12b1a5f71f0a80a8ee22bb2cdc775ada5b803e26d154e5d29c76b14c9fb264"
LAYOUT_CONTRACT = "renderer-v4.course-mode-layout.v1"
LESSON_ID = "course-mode-pilot-cat-ball"
LESSON_VERSION = 1
MANIFEST_CHECKSUM = "205784b3f97cb081ce9c226d8fd83fdd400401e706c000e1b09ba4e7ebdf36ce"
V5_MANIFEST_CHECKSUM = "e8ee7ff1fb67e8dbd0f8c6908b09c4a4f8e0d1cf3ce41bb38142da0fc03519dc"
V5_LESSON_ID = "course-mode-v5-farm-candidate"
V5_LAYOUT_CONTRACT = "layeredCinematic"

COURSE_MODE_COMPATIBILITY = {
    "schemaVersion": 1,
    "contractChecksum": CONTRACT_CHECKSUM,
    "layoutContract": LAYOUT_CONTRACT,
    "lessonId": LESSON_ID,
    "lessonVersion": LESSON_VERSION,
    "manifestChecksum": MANIFEST_CHECKSUM,
}
COURSE_MODE_V5_COMPATIBILITY = {
    "schemaVersion": 1,
    "contractChecksum": CONTRACT_CHECKSUM,
    "layoutContract": V5_LAYOUT_CONTRACT,
    "lessonId": V5_LESSON_ID,
    "lessonVersion": 2,
    "manifestChecksum": V5_MANIFEST_CHECKSUM,
}
LOCAL_LAB_ASSET_HOST = "192.168.0.120"
LOCAL_LAB_ASSET_PORT = 8102

_CUES = (
    ("cat-discover", "teach", "6c5d8ee1c2695a12dfa8202df5d0820b360aeca5a15662583efb97e812c99f66", "ebeaf4e8159b17da82d615f359272e26e7e81a1f005183335feba5b702f98d72", 134626),
    ("cat-meaning", "listen", "53c890908c8405e7b755f568ce8b3a687c3ff969bc05abb5267071a713d39a6b", "66cfb653fd9439c56d521db33490d91a5644a0e88f56951ab6d2d21b0ad642fe", 134306),
    ("cat-joint-speech", "teach", "b08be0ab8f59cd3ab5be1abc5ea838f6ce06aad1bf245496326af22777d6bb0e", "ebeaf4e8159b17da82d615f359272e26e7e81a1f005183335feba5b702f98d72", 134626),
    ("cat-recall", "listen", "334a28b4b8e84b43ab81f4792d92ce622f69e994b024e7cd3f013559f920c3fc", "66cfb653fd9439c56d521db33490d91a5644a0e88f56951ab6d2d21b0ad642fe", 134306),
    ("cat-transfer", "listen", "c719470f3e38238d2e9f8b9ba55d7d79456e9eac2fe8f7993ea1330d14a1c017", "244fb7c795ea375cef023d5545f4399d79200b41047faee06114c80a3941f9e2", 132426),
    ("ball-discover", "teach", "710b6c0afa7fa762efb75dc7561bde838142aa783c04798dc2caa8772381d379", "061ff71da9ad3e47dc6191016e4c6c228c198525749afaa83b47084c488262d4", 140166),
    ("ball-meaning", "listen", "a2bba81867f1a389e4ef7b80b5d3f08e20168ea78b84502749d61bd830c070df", "3b38014a0855297e2fc47e3aa253aac0c1c5744a7c3c32bbfea1f86d206d9389", 135526),
    ("cat-delayed", "listen", "10de40b2da10be7b591b067afb0603e6adeb626248a907e17ef8235a6dafa746", "6f7e0de371b790c018b3777a41f7914f9d06f1a1cea69d3c41ac9112d416048d", 143106),
)
_V5_CUES = (
    ("cat-discover", "cat-discover-center-01", "teach"),
    ("cat-meaning", "cat-meaning-left-right-01", "listen"),
    ("cat-joint-speech", "cat-discover-center-01", "teach"),
    ("cat-recall", "cat-recall-visual-02", "listen"),
    ("cat-transfer", "cat-transfer-scene-01", "listen"),
    ("ball-discover", "ball-discover-center-01", "teach"),
    ("ball-meaning", "ball-discover-center-01", "listen"),
    ("cat-delayed", "cat-delayed-recall-01", "listen"),
)
_V5_LAYERS = (
    {
        "layer": "background",
        "slot": "backgroundScene",
        "assetId": "75000000-0000-4000-8000-000000000010",
        "assetVersionId": "75000000-0000-4000-8000-000000000011",
        "assetKey": "course-mode.v5.scene.farm",
        "sha256": "d4abb6087dc3122e0a00feb5e6a86b03dc7db550eb59d25e92f54d0fd09e4fc0",
        "bytes": 43599,
        "mediaType": "image/jpeg",
        "width": 480,
        "height": 320,
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
        "assetId": "75000000-0000-4000-8000-000000000020",
        "assetVersionId": "75000000-0000-4000-8000-000000000022",
        "assetKey": "course-mode.v5.object.barn",
        "sha256": "c466239ff8ba202998e3827b6871906d7fbac6232aeaea3a59b7c69bec7d8777",
        "bytes": 15086,
        "mediaType": "image/png",
        "width": 95,
        "height": 95,
        "metadata": {
            "mediaKind": "image",
            "mediaType": "image/png",
            "width": 95,
            "height": 95,
            "rect": {"x": 20, "y": 168, "width": 95, "height": 95},
            "fit": "contain",
        },
    },
    {
        "layer": "robotOverlay",
        "slot": "robotOverlay",
        "assetId": "75000000-0000-4000-8000-000000000030",
        "assetVersionId": "75000000-0000-4000-8000-000000000031",
        "assetKey": "course-mode.v5.robot.teach",
        "sha256": "f2d496b5e750e895f7e086aec827d7b99d0bb322d73ea660a2e84ff484b602c4",
        "bytes": 223033,
        "mediaType": "video/mp4",
        "width": 240,
        "height": 240,
        "metadata": {
            "mediaKind": "video",
            "mediaType": "video/mp4",
            "codec": "mjpeg",
            "hasAudio": False,
            "width": 240,
            "height": 240,
            "fps": 10,
            "durationMs": 3000,
            "frameCount": 30,
            "rect": {"x": 118, "y": 160, "width": 150, "height": 150},
            "chromaKey": {"keyColor": "#00ff00", "tolerance": 20, "featherPx": 1},
        },
    },
)


def validate_course_mode_compatibility(value: Any) -> bool:
    return isinstance(value, dict) and value in (
        COURSE_MODE_COMPATIBILITY,
        COURSE_MODE_V5_COMPATIBILITY,
    )


def course_mode_local_asset_origin_matches(parsed_url: SplitResult | None) -> bool:
    """Allow HTTP only for the exact Task 07 local asset origin."""
    if (
        parsed_url is None
        or os.getenv("LESSON_COURSE_MODE_LOCAL_HTTP_ASSET_ENABLED", "").strip().lower()
        != "true"
    ):
        return False
    try:
        port = parsed_url.port
    except ValueError:
        return False
    return (
        parsed_url.scheme == "http"
        and parsed_url.hostname == LOCAL_LAB_ASSET_HOST
        and port == LOCAL_LAB_ASSET_PORT
        and not parsed_url.username
        and not parsed_url.password
    )


def _cue_identity(cue_id: Any) -> tuple[str, str, str, str, int] | None:
    return next((cue for cue in _CUES if cue[0] == cue_id), None)


def course_mode_compatibility_phase_matches(phase: Any) -> bool:
    if not isinstance(phase, dict) or not isinstance(phase.get("asset"), dict):
        return False
    identity = _cue_identity(phase.get("cueId"))
    if identity is None:
        return False
    cue_id, effect, derivative_id, sha256, size = identity
    asset = phase["asset"]
    metadata = asset.get("metadata")
    return (
        phase.get("templateId") == "flattenedMjpegCinematic"
        and phase.get("templateVersion") == 2
        and phase.get("effect") == effect
        and phase.get("stepKey") == cue_id
        and phase.get("playbackMode") == "once"
        and phase.get("timing") == {"durationMs": 2000}
        and asset.get("derivativeId") == derivative_id
        and asset.get("path")
        == f"lessons/derivatives/{derivative_id}/{cue_id}.mp4"
        and asset.get("sha256") == sha256
        and asset.get("bytes") == size
        and asset.get("mediaType") == "video/mp4"
        and asset.get("width") == 480
        and asset.get("height") == 320
        and metadata
        == {
            "codec": "mjpeg",
            "fps": 10,
            "durationMs": 2000,
            "frameCount": 20,
            "hasAudio": False,
        }
    )


def course_mode_compatibility_asset_matches(asset: Any) -> bool:
    if not isinstance(asset, dict):
        return False
    identity = _cue_identity(asset.get("cueId"))
    if identity is None:
        return False
    cue_id, effect, derivative_id, sha256, size = identity
    return (
        asset.get("key") == f"flattenedCinematic.{cue_id}"
        and asset.get("effect") == effect
        and asset.get("stepKey") == cue_id
        and asset.get("playbackMode") == "once"
        and asset.get("derivativeId") == derivative_id
        and asset.get("sha256") == sha256
        and asset.get("size") == size
        and asset.get("mediaType") == "video/mp4"
        and asset.get("compatibilityMetadata")
        == {
            "codec": "mjpeg",
            "width": 480,
            "height": 320,
            "fps": 10,
            "durationMs": 2000,
            "frameCount": 20,
            "hasAudio": False,
        }
    )


def course_mode_compatibility_assets_match(assets: Any) -> bool:
    if not isinstance(assets, list):
        return False
    marked = [
        asset
        for asset in assets
        if isinstance(asset, dict) and "courseModeCompatibility" in asset
    ]
    return (
        len(marked) == len(_CUES)
        and [asset.get("cueId") for asset in marked] == [cue[0] for cue in _CUES]
        and all(course_mode_compatibility_asset_matches(asset) for asset in marked)
    )


def _v5_layer(layer: dict[str, Any]) -> dict[str, Any]:
    return {
        "assetKey": layer["assetKey"],
        "assetVersionId": layer["assetVersionId"],
        "bytes": layer["bytes"],
        "layer": layer["layer"],
        "metadata": layer["metadata"],
        "sha256": layer["sha256"],
        "slot": layer["slot"],
        "version": 1,
    }


def _v5_phases() -> list[dict[str, Any]]:
    layers = [_v5_layer(layer) for layer in _V5_LAYERS]
    return [
        {
            "layers": copy.deepcopy(layers),
            "phaseId": phase_id,
            "playbackMode": "once",
            "templateId": V5_LAYOUT_CONTRACT,
            "templateVersion": 1,
            "timing": {"durationMs": 3000},
        }
        for phase_id in ("teach", "listen")
    ]


def _v5_visual_refs() -> list[dict[str, Any]]:
    paths = {
        "backgroundScene": "main/manager-web/public/tvideo-demo/assets/t54-layered/background-farm.jpg",
        "teachingObject": "pilot/v2/assets/objects/barn-95x95.png",
        "robotOverlay": "main/manager-web/public/tvideo-demo/assets/t54-layered/robot-teach.mp4",
    }
    return sorted(
        [
            {
                "assetKey": layer["assetKey"],
                "bytes": layer["bytes"],
                "path": paths[layer["slot"]],
                "sha256": layer["sha256"],
                "slot": layer["slot"],
                "stepKey": cue_id,
                "version": 1,
            }
            for cue_id, _, _ in _V5_CUES
            for layer in _V5_LAYERS
        ],
        key=lambda value: (value["stepKey"], value["slot"]),
    )


def _v5_manifest_assets() -> list[dict[str, Any]]:
    roles = {
        "backgroundScene": "poster",
        "teachingObject": "primarySubject",
        "robotOverlay": "pose",
    }
    paths = {
        "backgroundScene": "main/manager-web/public/tvideo-demo/assets/t54-layered/background-farm.jpg",
        "teachingObject": "pilot/v2/assets/objects/barn-95x95.png",
        "robotOverlay": "main/manager-web/public/tvideo-demo/assets/t54-layered/robot-teach.mp4",
    }
    return [
        {
            "bytes": layer["bytes"],
            "critical": True,
            "dimensions": {"height": layer["height"], "width": layer["width"]},
            "id": f'{layer["assetKey"]}@v1',
            "layer": layer["slot"],
            "mediaType": layer["mediaType"],
            "path": paths[layer["slot"]],
            "role": roles[layer["slot"]],
            "sha256": layer["sha256"],
            "version": 1,
        }
        for layer in _V5_LAYERS
    ]


def _v5_projection_matches(manifest: Any) -> bool:
    if not isinstance(manifest, dict):
        return False
    contract = manifest.get("courseModeContract")
    if not isinstance(contract, dict):
        return False
    try:
        CourseModeContract.from_mapping(contract)
    except CourseModeContractError:
        return False
    if (
        manifest.get("manifestVersion") != "teebot-lesson-renderer.v5"
        or manifest.get("protocolVersion") != "teebot-lesson-renderer.v5"
        or manifest.get("lessonId") != V5_LESSON_ID
        or manifest.get("lessonVersion") != 2
        or manifest.get("ageBand") != "18+"
        or manifest.get("profile") != "espTft"
        or manifest.get("features")
        != {"lessonRendererV5": {"assetSource": "publishedVersionedVisualRefs", "layeredCinematic": True}}
        or contract.get("contractChecksum") != CONTRACT_CHECKSUM
        or contract.get("fixtureId") != LESSON_ID
        or manifest.get("assets") != _v5_manifest_assets()
        or manifest.get("cinematicPhases") != _v5_phases()
    ):
        return False
    steps = manifest.get("steps")
    if (
        not isinstance(steps, list)
        or not all(isinstance(step, dict) for step in steps)
        or [step.get("id") for step in steps] != [cue[0] for cue in _V5_CUES]
    ):
        return False
    refs = manifest.get("visualRefs")
    return isinstance(refs, list) and all(isinstance(ref, dict) for ref in refs) and sorted(
        refs, key=lambda value: (value.get("stepKey"), value.get("slot"))
    ) == _v5_visual_refs()


def _v5_envelope_matches(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    bundle = value.get("bundle")
    lesson = value.get("lesson")
    if not isinstance(bundle, dict) or not isinstance(lesson, dict):
        return False
    if (
        value.get("evidenceState") != "reviewed-derivative-candidate"
        or value.get("renderer") != "teebot-lesson-renderer.v5"
        or value.get("template") != V5_LAYOUT_CONTRACT
        or value.get("contractChecksum") != CONTRACT_CHECKSUM
        or value.get("manifestIdentityChecksum") != V5_MANIFEST_CHECKSUM
        or bundle.get("checksum") != V5_MANIFEST_CHECKSUM
        or lesson.get("key") != V5_LESSON_ID
        or lesson.get("version") != 2
        or lesson.get("manifestChecksum") != V5_MANIFEST_CHECKSUM
        or value.get("cuePhases")
        != [
            {"activityId": activity_id, "cueId": cue_id, "phaseId": phase_id}
            for cue_id, activity_id, phase_id in _V5_CUES
        ]
        or value.get("phaseIdentity") != _v5_phases()
        or not _v5_projection_matches(value.get("manifestIdentityProjection"))
    ):
        return False
    shared_assets = value.get("sharedAssets")
    if not isinstance(shared_assets, list) or len(shared_assets) != len(_V5_LAYERS):
        return False
    for asset, expected in zip(shared_assets, _V5_LAYERS):
        if not isinstance(asset, dict) or {
            "assetId": asset.get("assetId"),
            "versionId": asset.get("versionId"),
            "assetKey": asset.get("assetKey"),
            "slot": asset.get("slot"),
            "sha256": asset.get("sha256"),
            "bytes": asset.get("bytes"),
            "mediaType": asset.get("mediaType"),
            "width": asset.get("width"),
            "height": asset.get("height"),
            "compatibilityMetadata": asset.get("compatibilityMetadata"),
        } != {
            "assetId": expected["assetId"],
            "versionId": expected["assetVersionId"],
            "assetKey": expected["assetKey"],
            "slot": expected["slot"],
            "sha256": expected["sha256"],
            "bytes": expected["bytes"],
            "mediaType": expected["mediaType"],
            "width": expected["width"],
            "height": expected["height"],
            "compatibilityMetadata": expected["metadata"],
        }:
            return False
    return True


def course_mode_compatibility_for_manifest(
    manifest: Any, *, manifest_checksum: Any
) -> dict[str, Any] | None:
    if not isinstance(manifest, dict):
        return None
    if manifest_checksum == V5_MANIFEST_CHECKSUM:
        if _v5_envelope_matches(manifest) or _v5_projection_matches(manifest):
            return copy.deepcopy(COURSE_MODE_V5_COMPATIBILITY)
        return None
    if manifest_checksum != MANIFEST_CHECKSUM:
        return None
    contract = manifest.get("courseModeContract")
    if not isinstance(contract, dict):
        return None
    try:
        CourseModeContract.from_mapping(contract)
    except CourseModeContractError:
        return None
    lesson = contract.get("lesson")
    renderer = contract.get("renderer")
    if (
        contract.get("contractChecksum") != CONTRACT_CHECKSUM
        or contract.get("fixtureId") != LESSON_ID
        or not isinstance(lesson, dict)
        or lesson.get("lessonId") != LESSON_ID
        or lesson.get("lessonVersion") != LESSON_VERSION
        or not isinstance(renderer, dict)
        or renderer.get("rendererId") != "teebot-lesson-renderer.v4"
        or renderer.get("visualLayoutContract") != LAYOUT_CONTRACT
    ):
        return None
    phases = manifest.get("cinematicPhases")
    if not isinstance(phases, list) or len(phases) != len(_CUES):
        return None
    for phase, expected in zip(phases, _CUES):
        if phase.get("cueId") != expected[0] or not course_mode_compatibility_phase_matches(phase):
            return None
    return copy.deepcopy(COURSE_MODE_COMPATIBILITY)
