"""Pure renderer-v5 mixed-media projection from an attested lesson SD pack."""

from __future__ import annotations

import copy
import re
from typing import Any, NoReturn, cast

RENDERER_V5 = "teebot-lesson-renderer.v5"
TEMPLATE_ID = "layeredCinematic"
TEMPLATE_VERSION = 1
KNOWN_PHASE_IDS = frozenset(
    {"flyIn", "walk", "teach", "listen", "thinking", "celebrate", "exit"}
)
LAYER_SLOTS = (
    ("background", "backgroundScene"),
    ("teachingObject", "teachingObject"),
    ("robotOverlay", "robotOverlay"),
)
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_RECT_KEYS = {"x", "y", "width", "height"}
_IMAGE_KEYS = {"mediaKind", "mediaType", "width", "height", "rect", "fit"}
_VIDEO_KEYS = {
    "mediaKind", "mediaType", "codec", "hasAudio", "width", "height", "fps",
    "durationMs", "frameCount", "rect", "chromaKey",
}


class LayeredCinematicContractError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def is_layered_cinematic_generation_asset(asset: Any) -> bool:
    metadata = asset.get("compatibilityMetadata") if isinstance(asset, dict) else None
    return isinstance(metadata, dict) and "mediaKind" in metadata


def _fail(code: str, message: str) -> NoReturn:
    raise LayeredCinematicContractError(code, message)


def _positive_int(value: Any) -> bool:
    return type(value) is int and value > 0


def _exact_dict(value: Any, keys: set[str], message: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        _fail("CINEMATIC_METADATA_MISMATCH", message)
    return cast(dict[str, Any], value)


def _rect(value: Any, *, max_width: int, max_height: int) -> dict[str, int]:
    rect = _exact_dict(value, _RECT_KEYS, "layered cinematic rectangle is invalid")
    if not all(type(rect.get(key)) is int for key in _RECT_KEYS):
        _fail("CINEMATIC_METADATA_MISMATCH", "layered cinematic rectangle must use integers")
    x, y, width, height = rect["x"], rect["y"], rect["width"], rect["height"]
    if x < 0 or y < 0 or width <= 0 or height <= 0 or x + width > max_width or y + height > max_height:
        _fail("CINEMATIC_METADATA_MISMATCH", "layered cinematic rectangle is out of bounds")
    return copy.deepcopy(rect)


def _image_metadata(value: Any, *, background: bool) -> dict[str, Any]:
    metadata = _exact_dict(value, _IMAGE_KEYS, "layered cinematic image metadata is invalid")
    expected_media_type = "image/jpeg" if background else "image/png"
    expected_fit = "cover" if background else "contain"
    width, height = metadata.get("width"), metadata.get("height")
    if (
        metadata.get("mediaKind") != "image"
        or metadata.get("mediaType") != expected_media_type
        or metadata.get("fit") != expected_fit
        or not _positive_int(width)
        or not _positive_int(height)
        or width > 480
        or height > 320
        or (background and (width != 480 or height != 320))
    ):
        _fail("CINEMATIC_METADATA_MISMATCH", "layered cinematic image identity is invalid")
    normalized = copy.deepcopy(metadata)
    normalized["rect"] = _rect(metadata.get("rect"), max_width=480, max_height=320)
    return normalized


def _video_metadata(value: Any, *, duration_ms: int) -> dict[str, Any]:
    metadata = _exact_dict(value, _VIDEO_KEYS, "layered cinematic Robot metadata is invalid")
    width, height = metadata.get("width"), metadata.get("height")
    fps, frame_count = metadata.get("fps"), metadata.get("frameCount")
    chroma = metadata.get("chromaKey")
    if (
        metadata.get("mediaKind") != "video"
        or metadata.get("mediaType") != "video/mp4"
        or metadata.get("codec") != "mjpeg"
        or metadata.get("hasAudio") is not False
        or not _positive_int(width)
        or not _positive_int(height)
        or width > 240
        or height > 240
        or fps not in {10, 15}
        or metadata.get("durationMs") != duration_ms
        or not _positive_int(frame_count)
        or abs(frame_count - (duration_ms * fps / 1000)) > 1
        or not isinstance(chroma, dict)
        or set(chroma) != {"keyColor", "tolerance", "featherPx"}
        or chroma.get("keyColor") != "#00ff00"
        or type(chroma.get("tolerance")) is not int
        or not 0 <= chroma["tolerance"] <= 255
        or type(chroma.get("featherPx")) is not int
        or not 0 <= chroma["featherPx"] <= 4
    ):
        _fail("CINEMATIC_METADATA_MISMATCH", "layered cinematic Robot identity is invalid")
    normalized = copy.deepcopy(metadata)
    normalized["rect"] = _rect(metadata.get("rect"), max_width=480, max_height=320)
    return normalized


def _local_sd_path(asset: dict[str, Any], local_root: str) -> str:
    local_path = asset.get("localPath")
    sd_path = asset.get("sdPath")
    if local_path is not None and sd_path is not None and local_path != sd_path:
        _fail("CINEMATIC_SD_PATH_MISSING", "layered cinematic SD path aliases do not match")
    path = local_path if isinstance(local_path, str) else sd_path
    root = local_root.rstrip("/")
    if not (
        isinstance(path, str)
        and root
        and (root.startswith("sd://tbot/lesson-assets/") or root.startswith("/sdcard/tbot/lesson-assets/"))
        and path.startswith(root + "/")
    ):
        _fail("CINEMATIC_SD_PATH_MISSING", "layered cinematic asset is outside the lesson SD pack")
    relative = path[len(root) + 1 :]
    if (
        relative in {"", ".", ".."}
        or ".." in relative.split("/")
        or any(marker in relative for marker in ("?", "#", "@", "\\"))
        or "://" in relative
    ):
        _fail("CINEMATIC_SD_PATH_MISSING", "layered cinematic SD path has unsafe syntax")
    return path


def validate_layered_cinematic_generation_asset(asset: Any) -> dict[str, Any]:
    """Validate one shared renderer-v5 asset before generation materialization."""
    if not isinstance(asset, dict):
        _fail("CINEMATIC_METADATA_MISMATCH", "layered cinematic generation asset is invalid")
    shared_key = asset.get("sharedAssetKey")
    version = asset.get("sharedAssetVersion")
    if (
        not isinstance(shared_key, str)
        or not shared_key
        or not _positive_int(version)
        or asset.get("key") != f"{shared_key}@v{version}"
    ):
        _fail("CINEMATIC_METADATA_MISMATCH", "layered cinematic shared identity is invalid")
    refs = asset.get("visualRefs")
    if not isinstance(refs, list) or not refs:
        _fail("CINEMATIC_METADATA_MISMATCH", "layered cinematic visual refs are invalid")
    slots: set[str] = set()
    for ref in refs:
        if not isinstance(ref, dict) or set(ref) != {"stepKey", "phase", "slot"}:
            _fail("CINEMATIC_METADATA_MISMATCH", "layered cinematic visual ref is invalid")
        if not all(isinstance(ref.get(key), str) and ref[key] for key in ref):
            _fail("CINEMATIC_METADATA_MISMATCH", "layered cinematic visual ref is invalid")
        slots.add(ref["slot"])
    if len(slots) != 1:
        _fail("CINEMATIC_METADATA_MISMATCH", "layered cinematic asset must use one layer slot")
    slot = next(iter(slots))
    media_type = asset.get("mediaType")
    if slot == "backgroundScene" and media_type == "image/jpeg":
        metadata = _image_metadata(asset.get("compatibilityMetadata"), background=True)
    elif slot == "teachingObject" and media_type == "image/png":
        metadata = _image_metadata(asset.get("compatibilityMetadata"), background=False)
    elif slot == "robotOverlay" and media_type == "video/mp4":
        source = asset.get("compatibilityMetadata")
        duration_ms = source.get("durationMs") if isinstance(source, dict) else None
        if not _positive_int(duration_ms):
            _fail("CINEMATIC_METADATA_MISMATCH", "layered cinematic Robot duration is invalid")
        metadata = _video_metadata(source, duration_ms=duration_ms)
    else:
        _fail("CINEMATIC_METADATA_MISMATCH", "layered cinematic slot media type is invalid")
    return {
        "sharedAssetKey": shared_key,
        "sharedAssetVersion": version,
        "compatibilityMetadata": metadata,
        "visualRefs": copy.deepcopy(refs),
    }


def project_layered_cinematic_phase(phase: Any, pack: Any) -> dict[str, Any]:
    """Validate and project one renderer-v5 phase using verified local media only."""
    if not isinstance(pack, dict) or pack.get("ready") is not True:
        _fail("CINEMATIC_PACK_NOT_READY", "verified layered cinematic SD pack is not ready")
    local_root, assets = pack.get("localRoot"), pack.get("assets")
    if not isinstance(local_root, str) or not isinstance(assets, list):
        _fail("CINEMATIC_PACK_NOT_READY", "layered cinematic SD pack metadata is incomplete")
    if not isinstance(phase, dict) or set(phase) != {
        "templateId", "templateVersion", "phaseId", "timing", "playbackMode", "layers"
    }:
        _fail("CINEMATIC_METADATA_MISMATCH", "layered cinematic phase fields are invalid")
    phase_id = phase.get("phaseId")
    if (
        phase.get("templateId") != TEMPLATE_ID
        or phase.get("templateVersion") != TEMPLATE_VERSION
        or phase_id not in KNOWN_PHASE_IDS
        or phase.get("playbackMode") not in {"once", "loop"}
    ):
        _fail("CINEMATIC_METADATA_MISMATCH", "layered cinematic phase identity is invalid")
    timing = _exact_dict(phase.get("timing"), {"durationMs"}, "layered cinematic timing is invalid")
    duration_ms = timing.get("durationMs")
    if not _positive_int(duration_ms):
        _fail("CINEMATIC_METADATA_MISMATCH", "layered cinematic duration is invalid")
    layers = phase.get("layers")
    if not isinstance(layers, list) or len(layers) != 3:
        _fail("CINEMATIC_METADATA_MISMATCH", "layered cinematic phase requires exactly three layers")
    assets_by_key = {
        item.get("key"): item for item in assets
        if isinstance(item, dict) and isinstance(item.get("key"), str)
    }
    projected: list[dict[str, Any]] = []
    robot_fps: int | None = None
    robot_frames: int | None = None
    for index, (layer_name, slot) in enumerate(LAYER_SLOTS):
        source = layers[index]
        if not isinstance(source, dict) or set(source) != {
            "layer", "slot", "assetVersionId", "assetKey", "version", "sha256", "bytes", "metadata"
        } or source.get("layer") != layer_name or source.get("slot") != slot:
            _fail("CINEMATIC_METADATA_MISMATCH", "layered cinematic layer order or slot is invalid")
        asset_key, version = source.get("assetKey"), source.get("version")
        asset_version_id = source.get("assetVersionId")
        if not isinstance(asset_key, str) or not asset_key or not _positive_int(version) or asset_version_id != f"{asset_key}@v{version}":
            _fail("CINEMATIC_METADATA_MISMATCH", "layered cinematic asset version is invalid")
        sha256, byte_count = source.get("sha256"), source.get("bytes")
        if not isinstance(sha256, str) or _SHA256_RE.fullmatch(sha256.lower()) is None or not _positive_int(byte_count):
            _fail("CINEMATIC_METADATA_MISMATCH", "layered cinematic asset checksum is invalid")
        metadata = (
            _image_metadata(source.get("metadata"), background=index == 0)
            if index < 2 else _video_metadata(source.get("metadata"), duration_ms=duration_ms)
        )
        asset = assets_by_key.get(asset_version_id)
        if not isinstance(asset, dict):
            _fail("CINEMATIC_SD_PATH_MISSING", "layered cinematic asset is missing from the SD pack")
        if (
            asset.get("state") != "READY"
            or asset.get("checksumOk") is not True
            or asset.get("mediaType") != metadata["mediaType"]
            or asset.get("sharedAssetKey") != asset_key
            or asset.get("sharedAssetVersion") != version
            or asset.get("sha256") != sha256
            or asset.get("size") != byte_count
            or asset.get("compatibilityMetadata") != source.get("metadata")
        ):
            _fail("CINEMATIC_METADATA_MISMATCH", "layered cinematic SD asset identity does not match")
        item = {
            "layer": layer_name,
            "slot": slot,
            "mediaKind": metadata["mediaKind"],
            "mediaType": metadata["mediaType"],
            "sdPath": _local_sd_path(asset, local_root),
            "sha256": sha256.lower(),
            "bytes": byte_count,
            "width": metadata["width"],
            "height": metadata["height"],
            "rect": metadata["rect"],
        }
        if index < 2:
            item["fit"] = metadata["fit"]
        else:
            item.update(codec="mjpeg", hasAudio=False, chromaKey=metadata["chromaKey"])
            robot_fps, robot_frames = metadata["fps"], metadata["frameCount"]
        projected.append(item)
    return {
        "templateId": TEMPLATE_ID,
        "templateVersion": TEMPLATE_VERSION,
        "phaseId": phase_id,
        "durationMs": duration_ms,
        "fps": robot_fps,
        "frameCount": robot_frames,
        "playbackMode": phase["playbackMode"],
        "layers": projected,
    }
