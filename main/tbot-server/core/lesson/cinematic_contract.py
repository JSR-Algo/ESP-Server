"""Pure renderer-v3 cinematic projection from an attested lesson SD pack."""

from __future__ import annotations

import copy
import re
from typing import Any, Dict, List

RENDERER_V3 = "teebot-lesson-renderer.v3"
TEMPLATE_ID = "directMp4Cinematic"
TEMPLATE_VERSION = 1
KNOWN_PHASE_IDS = frozenset(
    {"opening", "greet", "teach", "listen", "thinking", "correct", "retry", "celebrate"}
)
LAYER_SLOTS = (
    ("background", "backgroundScene"),
    ("teachingObject", "teachingObject"),
    ("robotOverlay", "robotOverlay"),
)
_SHA256_RE = re.compile(r"[0-9a-f]{64}")


class CinematicContractError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def _fail(code: str, message: str) -> None:
    raise CinematicContractError(code, message)


def _positive_int(value: Any) -> bool:
    return type(value) is int and value > 0


def _local_sd_path(asset: Dict[str, Any], local_root: str) -> str:
    local_path = asset.get("localPath")
    sd_path = asset.get("sdPath")
    if local_path is not None and sd_path is not None and local_path != sd_path:
        _fail("CINEMATIC_SD_PATH_MISSING", "cinematic SD path aliases do not match")
    value = local_path if isinstance(local_path, str) else sd_path
    if not isinstance(value, str) or not value.strip():
        _fail("CINEMATIC_SD_PATH_MISSING", "cinematic layer has no local SD path")
    value = value.strip()
    normalized_root = local_root.rstrip("/")
    if not normalized_root or not (
        normalized_root.startswith("sd://tbot/lesson-assets")
        or normalized_root.startswith("/sdcard/tbot/lesson-assets")
    ):
        _fail("CINEMATIC_SD_PATH_MISSING", "cinematic pack root is not a lesson SD root")
    if not value.startswith(normalized_root + "/") or value.startswith(("http://", "https://")):
        _fail("CINEMATIC_SD_PATH_MISSING", "cinematic layer is outside the lesson SD pack")
    relative = value[len(normalized_root) + 1 :]
    if any(marker in relative for marker in ("?", "#", "@", "\\")) or "://" in relative:
        _fail("CINEMATIC_SD_PATH_MISSING", "cinematic SD path contains URL or credential syntax")
    return value


def _validate_metadata(metadata: Any, *, duration_ms: int) -> Dict[str, Any]:
    expected_keys = {"codec", "fps", "durationMs", "frameCount", "hasAudio", "rect", "chromaKey"}
    if not isinstance(metadata, dict) or set(metadata) != expected_keys:
        _fail("CINEMATIC_METADATA_MISMATCH", "cinematic layer metadata fields are invalid")
    fps = metadata.get("fps")
    frame_count = metadata.get("frameCount")
    if (
        metadata.get("codec") != "mjpeg"
        or fps not in {10, 15}
        or metadata.get("hasAudio") is not False
        or metadata.get("durationMs") != duration_ms
        or not _positive_int(frame_count)
        or abs(frame_count - (duration_ms * fps / 1000)) > 1
    ):
        _fail("CINEMATIC_METADATA_MISMATCH", "cinematic timing or codec metadata does not match")
    rect = metadata.get("rect")
    if not isinstance(rect, dict) or set(rect) != {"x", "y", "width", "height"}:
        _fail("CINEMATIC_METADATA_MISMATCH", "cinematic rectangle metadata is invalid")
    if not all(type(rect.get(key)) is int for key in rect):
        _fail("CINEMATIC_METADATA_MISMATCH", "cinematic rectangle values must be integers")
    return copy.deepcopy(metadata)


def _firmware_chroma_key(chroma: Any) -> Dict[str, Any] | None:
    if chroma is None:
        return None
    color = chroma.get("color") if isinstance(chroma, dict) else None
    if not isinstance(color, dict):
        _fail("CINEMATIC_METADATA_MISMATCH", "cinematic chroma metadata is invalid")
    channels = tuple(color.get(channel) for channel in ("r", "g", "b"))
    tolerance = chroma.get("tolerance")
    feather = chroma.get("feather")
    if (
        any(type(channel) is not int or not 0 <= channel <= 255 for channel in channels)
        or type(tolerance) is not int
        or not 0 <= tolerance <= 255
        or type(feather) is not int
        or not 0 <= feather <= 255
    ):
        _fail("CINEMATIC_METADATA_MISMATCH", "cinematic chroma metadata is invalid")
    return {
        "keyColor": "#{:02x}{:02x}{:02x}".format(*channels),
        "tolerance": tolerance,
        "featherPx": feather,
    }


def project_cinematic_phase(phase: Any, pack: Any) -> Dict[str, Any]:
    """Validate and project exactly one phase using only verified local SD paths."""
    if not isinstance(pack, dict) or pack.get("ready") is not True:
        _fail("CINEMATIC_PACK_NOT_READY", "verified cinematic SD pack is not ready")
    local_root = pack.get("localRoot")
    assets = pack.get("assets")
    if not isinstance(local_root, str) or not isinstance(assets, list):
        _fail("CINEMATIC_PACK_NOT_READY", "cinematic SD pack metadata is incomplete")
    if not isinstance(phase, dict) or set(phase) != {
        "templateId", "templateVersion", "phaseId", "timing", "layers"
    }:
        _fail("CINEMATIC_METADATA_MISMATCH", "cinematic phase fields are invalid")
    phase_id = phase.get("phaseId")
    if (
        phase.get("templateId") != TEMPLATE_ID
        or phase.get("templateVersion") != TEMPLATE_VERSION
        or phase_id not in KNOWN_PHASE_IDS
    ):
        _fail("CINEMATIC_METADATA_MISMATCH", "cinematic renderer or phase identity is invalid")
    timing = phase.get("timing")
    if not isinstance(timing, dict) or not _positive_int(timing.get("durationMs")):
        _fail("CINEMATIC_METADATA_MISMATCH", "cinematic phase duration is invalid")
    duration_ms = timing["durationMs"]
    layers = phase.get("layers")
    if not isinstance(layers, list) or len(layers) != 3:
        _fail("CINEMATIC_METADATA_MISMATCH", "cinematic phase requires exactly three layers")
    assets_by_key = {
        item.get("key"): item for item in assets if isinstance(item, dict) and isinstance(item.get("key"), str)
    }
    projected_layers: List[Dict[str, Any]] = []
    shared_fps = None
    shared_frame_count = None
    for index, (layer_name, slot) in enumerate(LAYER_SLOTS):
        source = layers[index]
        if not isinstance(source, dict) or source.get("layer") != layer_name or source.get("slot") != slot:
            _fail("CINEMATIC_METADATA_MISMATCH", "cinematic layer order or slot is invalid")
        asset_version_id = source.get("assetVersionId")
        asset_key = source.get("assetKey")
        version = source.get("version")
        if (
            not isinstance(asset_key, str)
            or not asset_key
            or not _positive_int(version)
            or asset_version_id != f"{asset_key}@v{version}"
        ):
            _fail("CINEMATIC_METADATA_MISMATCH", "cinematic asset version identity is invalid")
        asset = assets_by_key.get(asset_version_id)
        if not isinstance(asset, dict):
            _fail("CINEMATIC_SD_PATH_MISSING", "cinematic asset is missing from the SD pack")
        metadata = _validate_metadata(source.get("metadata"), duration_ms=duration_ms)
        if (
            asset.get("state") != "READY"
            or asset.get("checksumOk") is not True
            or asset.get("mediaType") != "video/mp4"
            or asset.get("sharedAssetKey") != asset_key
            or asset.get("sharedAssetVersion") != version
            or asset.get("sha256") != source.get("sha256")
            or asset.get("size") != source.get("bytes")
            or asset.get("compatibilityMetadata") != source.get("metadata")
        ):
            _fail("CINEMATIC_METADATA_MISMATCH", "cinematic SD asset identity does not match the manifest")
        if _SHA256_RE.fullmatch(str(source.get("sha256") or "").lower()) is None or not _positive_int(source.get("bytes")):
            _fail("CINEMATIC_METADATA_MISMATCH", "cinematic asset SHA or byte size is invalid")
        fps = metadata["fps"]
        frame_count = metadata["frameCount"]
        if shared_fps is None:
            shared_fps, shared_frame_count = fps, frame_count
        elif fps != shared_fps or frame_count != shared_frame_count:
            _fail("CINEMATIC_METADATA_MISMATCH", "cinematic layers do not share one clock")
        projected_layers.append(
            {
                "layer": layer_name,
                "slot": slot,
                "assetVersionId": asset_version_id,
                "assetKey": asset_key,
                "version": version,
                "sdPath": _local_sd_path(asset, local_root),
                "sha256": source["sha256"].lower(),
                "bytes": source["bytes"],
                "rect": metadata["rect"],
                "chromaKey": _firmware_chroma_key(metadata["chromaKey"]),
            }
        )
    return {
        "templateId": TEMPLATE_ID,
        "templateVersion": TEMPLATE_VERSION,
        "phaseId": phase_id,
        "durationMs": duration_ms,
        "fps": shared_fps,
        "frameCount": shared_frame_count,
        "layers": projected_layers,
    }
