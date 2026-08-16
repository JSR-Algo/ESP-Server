"""Build the physical-path asset pack used only for firmware MCP sync."""

from __future__ import annotations

import copy
import re
from typing import Any
from urllib.parse import quote, urlsplit

from core.lesson.flattened_cinematic_contract import (
    TRGB_MEDIA_TYPE,
    FlattenedCinematicContractError,
    validate_flattened_cinematic_cache_asset,
    validate_pathless_flattened_cinematic_cache_asset,
)
from core.lesson.layered_cinematic_contract import (
    LayeredCinematicContractError,
    is_layered_cinematic_generation_asset,
    validate_layered_cinematic_runtime_asset,
)
from core.lesson.cache_key_contract import (
    AssetBasenameRefused,
    encode_asset_basename,
)
from core.lesson.sd_pack_evict import CacheEvictionRefused, validate_cache_key

FIRMWARE_LESSON_ASSET_ROOT = "/sdcard/tbot/lesson-assets"
MAX_SYNC_ASSETS = 64
MAX_FAT_BASENAME_BYTES = 255
MAX_SAFE_SIZE = 2 * 1024 * 1024 * 1024
_LOWER_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_RESERVED_BASENAMES = frozenset(
    {
        ".",
        "..",
        "current.json",
        "pvg.json",
        "activation.json",
        "lesson-pack-activation.json",
    }
)
_RESERVED_SUFFIXES = (".tmp", ".download", ".part", ".backup")
_MP4_METADATA_FIELDS = frozenset({"codec", "fps", "durationMs", "frameCount", "hasAudio", "rect", "chromaKey"})
_RECT_FIELDS = frozenset({"x", "y", "width", "height"})
_CHROMA_FIELDS = frozenset({"color", "tolerance", "feather"})
_COLOR_FIELDS = frozenset({"r", "g", "b"})
_VISUAL_REF_FIELDS = frozenset({"stepKey", "phase", "slot"})
_FLATTENED_METADATA_FIELDS = frozenset(
    {"codec", "width", "height", "fps", "durationMs", "frameCount", "hasAudio"}
)
_FLATTENED_PHASE_IDS = frozenset(
    {"opening", "greet", "teach", "listen", "thinking", "correct", "retry", "celebrate"}
)
_FLATTENED_CUE_EFFECTS = frozenset(
    {
        "opening", "greet", "teach", "listen", "thinking", "correct",
        "retry-level-1", "retry-level-2", "retry-level-3", "celebrate",
        "word-transition",
    }
)
_SAFE_SLUG_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_FLATTENED_CUE_PLAYBACK_MODE = {
    effect: "loop" if effect in {"greet", "listen", "thinking"} else "once"
    for effect in _FLATTENED_CUE_EFFECTS
}
_FLATTENED_CUE_DURATION_MS = {
    "opening": 9500, "greet": 1200, "teach": 2600, "listen": 1300,
    "thinking": 1300, "correct": 600, "retry-level-1": 1200,
    "retry-level-2": 1400, "retry-level-3": 1600, "celebrate": 3000,
    "word-transition": 1100,
}
_FIRMWARE_BASE_ASSET_FIELDS = frozenset({
    "key", "path", "url", "onlineUrl", "sha256", "sourceSha256", "size",
    "mediaType", "critical", "layer", "role", "state", "checksumOk",
    "localPath", "sdPath",
})
_FIRMWARE_RENDERER_V4_IDENTITY_FIELDS = frozenset({
    "cueId", "effect", "stepKey", "playbackMode", "derivativeId", "phaseId",
})
_FIRMWARE_ASSET_FIELDS = (
    _FIRMWARE_BASE_ASSET_FIELDS
    | _FIRMWARE_RENDERER_V4_IDENTITY_FIELDS
    | {"compatibilityMetadata"}
)
_FIRMWARE_ASSET_FIELDS_BY_KIND = {
    "base": _FIRMWARE_BASE_ASSET_FIELDS,
    "renderer_v5_media": _FIRMWARE_BASE_ASSET_FIELDS,
    "renderer_v3_mp4": _FIRMWARE_BASE_ASSET_FIELDS,
    "renderer_v4_mp4": _FIRMWARE_ASSET_FIELDS,
    "renderer_v4_trgb": (
        _FIRMWARE_BASE_ASSET_FIELDS | _FIRMWARE_RENDERER_V4_IDENTITY_FIELDS
    ),
}


class FirmwareSyncPackError(ValueError):
    pass


def _refuse() -> None:
    raise FirmwareSyncPackError("firmware sync pack invalid")


def _canonical_cache_key(value: Any) -> str:
    try:
        return validate_cache_key(value)
    except CacheEvictionRefused:
        _refuse()


def _encoded_basename(asset_key: Any) -> str:
    """Delegates to THE canonical encoder (T5.1).

    This used to carry its own copy of the rules with a 255-byte cap and no FAT
    device-name check — a third policy alongside the materializer's and the
    backend's. Every key reaching here has already passed the materializer, so
    the stricter shared rule cannot reject anything a real pack contains.
    """
    try:
        return encode_asset_basename(asset_key)
    except AssetBasenameRefused:
        _refuse()

def _matching_alias(asset: dict[str, Any], primary: str, legacy: str) -> str:
    primary_present = primary in asset
    legacy_present = legacy in asset
    if primary_present and legacy_present and asset[primary] != asset[legacy]:
        _refuse()
    if primary_present:
        value = asset[primary]
    elif legacy_present:
        value = asset[legacy]
    else:
        _refuse()
    if not isinstance(value, str) or not value:
        _refuse()
    return value

def _validate_source_local_path(value: str, cache_key: str, basename: str) -> None:
    allowed = {
        f"sd://tbot/lesson-assets/{cache_key}/{basename}",
        f"{FIRMWARE_LESSON_ASSET_ROOT}/{cache_key}/{basename}",
    }
    if value not in allowed:
        _refuse()

def _validate_online_url(value: str) -> None:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        _refuse()


def _integer(value: Any, minimum: int, maximum: int = (1 << 53) - 1) -> bool:
    return type(value) is int and minimum <= value <= maximum


def validate_renderer_v3_shared_mp4(asset: Any) -> dict[str, Any]:
    """Validate the narrow shared renderer-v3 MJPEG-in-MP4 transport contract."""
    if not isinstance(asset, dict) or asset.get("mediaType") != "video/mp4":
        _refuse()
    shared_key = asset.get("sharedAssetKey")
    version = asset.get("sharedAssetVersion")
    if not isinstance(shared_key, str) or not shared_key or not _integer(version, 1):
        _refuse()
    if asset.get("key") != f"{shared_key}@v{version}":
        _refuse()
    metadata = asset.get("compatibilityMetadata")
    if not isinstance(metadata, dict) or set(metadata) != _MP4_METADATA_FIELDS:
        _refuse()
    if metadata.get("codec") != "mjpeg" or metadata.get("fps") not in {10, 15} or metadata.get("hasAudio") is not False:
        _refuse()
    duration = metadata.get("durationMs")
    frame_count = metadata.get("frameCount")
    if not _integer(duration, 1) or not _integer(frame_count, 1):
        _refuse()
    if abs(frame_count - (duration * metadata["fps"] / 1000)) > 1:
        _refuse()
    rect = metadata.get("rect")
    if not isinstance(rect, dict) or set(rect) != _RECT_FIELDS:
        _refuse()
    if not all(_integer(rect.get(field), 0 if field in {"x", "y"} else 1) for field in _RECT_FIELDS):
        _refuse()
    refs = asset.get("visualRefs")
    if not isinstance(refs, list) or not refs:
        _refuse()
    slot_roots = set()
    for ref in refs:
        if not isinstance(ref, dict) or set(ref) != _VISUAL_REF_FIELDS:
            _refuse()
        if not all(isinstance(ref.get(field), str) and ref[field] for field in _VISUAL_REF_FIELDS):
            _refuse()
        slot_roots.add(ref["slot"].split(".", 1)[0])
    if len(slot_roots) != 1:
        _refuse()
    slot_root = next(iter(slot_roots))
    chroma = metadata.get("chromaKey")
    if slot_root == "backgroundScene":
        if rect != {"x": 0, "y": 0, "width": 480, "height": 320} or chroma is not None:
            _refuse()
    elif slot_root in {"teachingObject", "robotOverlay"}:
        if rect["width"] > 240 or rect["height"] > 240 or rect["x"] + rect["width"] > 480 or rect["y"] + rect["height"] > 320:
            _refuse()
        if not isinstance(chroma, dict) or set(chroma) != _CHROMA_FIELDS:
            _refuse()
        color = chroma.get("color")
        if not isinstance(color, dict) or set(color) != _COLOR_FIELDS:
            _refuse()
        if not all(_integer(color.get(channel), 0, 255) for channel in _COLOR_FIELDS):
            _refuse()
        if not _integer(chroma.get("tolerance"), 0, 255) or not _integer(chroma.get("feather"), 0, 255):
            _refuse()
    else:
        _refuse()
    online_url = _matching_alias(asset, "onlineUrl", "url")
    parsed = urlsplit(online_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        _refuse()
    return {
        "sharedAssetKey": shared_key,
        "sharedAssetVersion": version,
        "compatibilityMetadata": copy.deepcopy(metadata),
        "visualRefs": copy.deepcopy(refs),
    }


def validate_renderer_v4_flattened_mp4(asset: Any) -> dict[str, Any]:
    """Validate the exact one-file renderer-v4 flattened MJPEG transport."""
    if not isinstance(asset, dict) or asset.get("mediaType") != "video/mp4":
        _refuse()
    if any(
        field in asset
        for field in ("sharedAssetKey", "sharedAssetVersion", "visualRefs")
    ):
        _refuse()
    derivative_id = asset.get("derivativeId")
    if not isinstance(derivative_id, str) or _LOWER_SHA256_RE.fullmatch(derivative_id) is None:
        _refuse()
    has_phase = "phaseId" in asset
    has_cue = any(field in asset for field in ("cueId", "effect", "stepKey", "playbackMode"))
    if has_phase == has_cue:
        _refuse()
    if has_phase:
        phase_id = asset.get("phaseId")
        if (
            not isinstance(phase_id, str)
            or phase_id not in _FLATTENED_PHASE_IDS
            or asset.get("key") != f"flattenedCinematic.{phase_id}"
        ):
            _refuse()
        identity = {"phaseId": phase_id}
    else:
        cue_id = asset.get("cueId")
        step_key = asset.get("stepKey")
        effect = asset.get("effect")
        playback_mode = asset.get("playbackMode")
        if (
            not isinstance(cue_id, str)
            or _SAFE_SLUG_RE.fullmatch(cue_id) is None
            or not isinstance(step_key, str)
            or _SAFE_SLUG_RE.fullmatch(step_key) is None
            or not isinstance(effect, str)
            or effect not in _FLATTENED_CUE_EFFECTS
            or not isinstance(playback_mode, str)
            or playback_mode != _FLATTENED_CUE_PLAYBACK_MODE.get(effect)
            or asset.get("key") != f"flattenedCinematic.{cue_id}"
        ):
            _refuse()
        identity = {
            "cueId": cue_id,
            "effect": effect,
            "stepKey": step_key,
            "playbackMode": playback_mode,
        }
    metadata = asset.get("compatibilityMetadata")
    if not isinstance(metadata, dict) or set(metadata) != _FLATTENED_METADATA_FIELDS:
        _refuse()
    duration_ms = metadata.get("durationMs")
    frame_count = metadata.get("frameCount")
    if (
        metadata.get("codec") != "mjpeg"
        or type(metadata.get("width")) is not int
        or metadata.get("width") != 480
        or type(metadata.get("height")) is not int
        or metadata.get("height") != 320
        or type(metadata.get("fps")) is not int
        or metadata.get("fps") != 10
        or metadata.get("hasAudio") is not False
        or not _integer(duration_ms, 1)
        or not _integer(frame_count, 1)
        or duration_ms != frame_count * 100
    ):
        _refuse()
    if not has_phase and duration_ms != _FLATTENED_CUE_DURATION_MS[effect]:
        _refuse()
    _validate_online_url(_matching_alias(asset, "onlineUrl", "url"))
    return {
        "derivativeId": derivative_id,
        **identity,
        "compatibilityMetadata": copy.deepcopy(metadata),
    }

def _validate_asset_metadata(
    asset: dict[str, Any], cache_key: str, basename: str
) -> tuple[str, str, str | None, str]:
    sha256 = asset.get("sha256")
    if not isinstance(sha256, str) or _LOWER_SHA256_RE.fullmatch(sha256) is None:
        _refuse()
    size = asset.get("size")
    if type(size) is not int or size < 0 or size > MAX_SAFE_SIZE:
        _refuse()
    media_type = asset.get("mediaType")
    if not isinstance(media_type, str) or not media_type.strip() or any(ord(char) < 0x20 for char in media_type):
        _refuse()
    if type(asset.get("critical")) is not bool:
        _refuse()
    local_path = _matching_alias(asset, "sdPath", "localPath")
    local_fallback_url = _matching_alias(asset, "onlineUrl", "url")
    _validate_source_local_path(local_path, cache_key, basename)
    _validate_online_url(local_fallback_url)
    online_url = local_fallback_url
    canonical_path = None
    asset_kind = "base"
    flattened_identity = "derivativeId" in asset or "phaseId" in asset or "cueId" in asset
    if is_layered_cinematic_generation_asset(asset):
        try:
            validate_layered_cinematic_runtime_asset(asset)
        except LayeredCinematicContractError:
            _refuse()
        asset_kind = "renderer_v5_media"
    elif flattened_identity:
        if media_type == TRGB_MEDIA_TYPE:
            asset_kind = "renderer_v4_trgb"
            source_url = asset.get("sourceUrl")
            if source_url is None:
                source_url = local_fallback_url
            if not isinstance(source_url, str) or not source_url:
                _refuse()
            _validate_online_url(source_url)
            try:
                contract_asset = dict(asset)
                contract_asset["url"] = source_url
                contract_asset["onlineUrl"] = source_url
                try:
                    validate_flattened_cinematic_cache_asset(contract_asset)
                except FlattenedCinematicContractError:
                    validated = validate_pathless_flattened_cinematic_cache_asset(contract_asset)
                    canonical_path = validated["path"]
            except FlattenedCinematicContractError:
                _refuse()
            online_url = source_url
        elif media_type.lower().startswith("video/"):
            asset_kind = "renderer_v4_mp4"
            validate_renderer_v4_flattened_mp4(asset)
        else:
            _refuse()
    elif media_type.lower().startswith("video/"):
        asset_kind = "renderer_v3_mp4"
        validate_renderer_v3_shared_mp4(asset)
    return local_path, online_url, canonical_path, asset_kind


def build_firmware_sync_pack(pack: Any) -> dict[str, Any]:
    """Return a deep MCP-only copy; never rewrite the render/prepare pack."""
    if not isinstance(pack, dict):
        _refuse()
    cache_key = _canonical_cache_key(pack.get("cacheKey"))
    manifest_checksum = pack.get("manifestChecksum")
    if (
        not isinstance(manifest_checksum, str)
        or _LOWER_SHA256_RE.fullmatch(manifest_checksum) is None
        or not cache_key.endswith("-" + manifest_checksum)
    ):
        _refuse()
    assets = pack.get("assets")
    if not isinstance(assets, list) or not 1 <= len(assets) <= MAX_SYNC_ASSETS:
        _refuse()
    destinations = set()
    basenames = []
    normalized_assets = []
    for asset in assets:
        if not isinstance(asset, dict):
            _refuse()
        basename = _encoded_basename(asset.get("key"))
        collision_key = basename.lower()
        if collision_key in destinations:
            _refuse()
        destinations.add(collision_key)
        basenames.append(basename)
        normalized_assets.append(_validate_asset_metadata(asset, cache_key, basename))

    result = copy.deepcopy(pack)
    physical_root = f"{FIRMWARE_LESSON_ASSET_ROOT}/{cache_key}"
    result["localRoot"] = physical_root
    for index, basename in enumerate(basenames):
        _source_local_path, online_url, canonical_path, asset_kind = normalized_assets[index]
        physical_path = f"{physical_root}/{basename}"
        sent_asset = result["assets"][index]
        sent_asset.pop("localPath", None)
        sent_asset.pop("url", None)
        sent_asset["sdPath"] = physical_path
        sent_asset["onlineUrl"] = online_url
        if canonical_path is not None:
            sent_asset["path"] = canonical_path
        allowed_fields = _FIRMWARE_ASSET_FIELDS_BY_KIND[asset_kind]
        for field in tuple(sent_asset):
            if field not in allowed_fields:
                sent_asset.pop(field)
    return result
