"""Exact renderer-v4 compatibility identity for the frozen Course Mode pilot."""

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

COURSE_MODE_COMPATIBILITY = {
    "schemaVersion": 1,
    "contractChecksum": CONTRACT_CHECKSUM,
    "layoutContract": LAYOUT_CONTRACT,
    "lessonId": LESSON_ID,
    "lessonVersion": LESSON_VERSION,
    "manifestChecksum": MANIFEST_CHECKSUM,
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


def validate_course_mode_compatibility(value: Any) -> bool:
    return isinstance(value, dict) and value == COURSE_MODE_COMPATIBILITY


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


def course_mode_compatibility_for_manifest(
    manifest: Any, *, manifest_checksum: Any
) -> dict[str, Any] | None:
    if not isinstance(manifest, dict) or manifest_checksum != MANIFEST_CHECKSUM:
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
