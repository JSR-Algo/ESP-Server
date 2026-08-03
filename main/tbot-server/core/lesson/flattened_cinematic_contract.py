"""Pure renderer-v4 flattened cinematic projection from an attested SD pack."""

from __future__ import annotations

import re
from typing import Any, NoReturn, cast
from urllib.parse import urlsplit

RENDERER_V4 = "teebot-lesson-renderer.v4"
TEMPLATE_ID = "flattenedMjpegCinematic"
TEMPLATE_VERSION_V1 = 1
TEMPLATE_VERSION_V2 = 2
KNOWN_PHASE_IDS = frozenset(
    {"opening", "greet", "teach", "listen", "thinking", "correct", "retry", "celebrate"}
)
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_SLUG_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_V2_EFFECTS = frozenset(
    {
        "opening", "greet", "teach", "listen", "thinking", "correct",
        "retry-level-1", "retry-level-2", "retry-level-3", "celebrate",
        "word-transition",
    }
)
_V2_PLAYBACK_MODE = {
    effect: "loop" if effect in {"greet", "listen", "thinking"} else "once"
    for effect in _V2_EFFECTS
}
_V2_DURATION_MS = {
    "opening": 9500, "greet": 1200, "teach": 2600, "listen": 1300,
    "thinking": 1300, "correct": 600, "retry-level-1": 1200,
    "retry-level-2": 1400, "retry-level-3": 1600, "celebrate": 3000,
    "word-transition": 1100,
}
TRGB_MEDIA_TYPE = "application/vnd.tbot.rgb565-indexed"
TRGB_HEADER_BYTES = 64
TRGB_INDEX_ENTRY_BYTES = 16
TRGB_FRAME_BYTES = 307200
TRGB_DATA_ALIGNMENT = 512
_V2_METADATA_FIELDS = {
    "codec", "containerVersion", "width", "height", "storedWidth", "storedHeight",
    "orientation", "fps", "durationMs", "frameCount", "frameBytes", "hasAudio",
}


class FlattenedCinematicContractError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def _fail(code: str, message: str) -> NoReturn:
    raise FlattenedCinematicContractError(code, message)


def _positive_int(value: Any) -> bool:
    return type(value) is int and value > 0


def trgb_container_bytes(frame_count: int) -> int:
    """Return renderer-v4 TRGB bytes including the aligned frame index."""
    index_end = TRGB_HEADER_BYTES + frame_count * TRGB_INDEX_ENTRY_BYTES
    data_offset = (
        (index_end + TRGB_DATA_ALIGNMENT - 1) // TRGB_DATA_ALIGNMENT
    ) * TRGB_DATA_ALIGNMENT
    return data_offset + frame_count * TRGB_FRAME_BYTES


def _exact_dict(value: Any, fields: set[str], message: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        _fail("CINEMATIC_METADATA_MISMATCH", message)
    return cast(dict[str, Any], value)


def cinematic_identity_key(command: Any) -> str:
    """Return the sole control identity, rejecting ambiguous commands and ACKs."""
    if not isinstance(command, dict):
        _fail("CINEMATIC_IDENTITY_UNSUPPORTED", "cinematic command identity is invalid")
    has_phase = "phaseId" in command
    has_cue = "cueId" in command
    if has_phase == has_cue:
        _fail("CINEMATIC_IDENTITY_UNSUPPORTED", "cinematic command must have exactly one identity")
    identity = command["phaseId"] if has_phase else command["cueId"]
    if has_phase:
        if not isinstance(identity, str) or identity not in KNOWN_PHASE_IDS:
            _fail("CINEMATIC_IDENTITY_UNSUPPORTED", "cinematic phase identity is invalid")
    elif not isinstance(identity, str) or _SLUG_RE.fullmatch(identity) is None:
        _fail("CINEMATIC_IDENTITY_UNSUPPORTED", "cinematic cue identity is invalid")
    return cast(str, identity)


def _entry_identity(entry: dict[str, Any]) -> tuple[int, str]:
    version = entry.get("templateVersion")
    if entry.get("templateId") != TEMPLATE_ID or type(version) is not int:
        _fail("CINEMATIC_METADATA_MISMATCH", "flattened cinematic identity is invalid")
    if version == TEMPLATE_VERSION_V1:
        _exact_dict(
            entry,
            {"templateId", "templateVersion", "phaseId", "timing", "asset"},
            "flattened cinematic phase fields are invalid",
        )
        if entry.get("phaseId") not in KNOWN_PHASE_IDS:
            _fail("CINEMATIC_METADATA_MISMATCH", "flattened cinematic identity is invalid")
        return TEMPLATE_VERSION_V1, entry["phaseId"]
    if version == TEMPLATE_VERSION_V2:
        _exact_dict(
            entry,
            {
                "templateId", "templateVersion", "cueId", "effect", "stepKey",
                "playbackMode", "timing", "asset",
            },
            "flattened cinematic cue fields are invalid",
        )
        effect = entry.get("effect")
        playback_mode = entry.get("playbackMode")
        if (
            not isinstance(entry.get("cueId"), str)
            or _SLUG_RE.fullmatch(entry["cueId"]) is None
            or not isinstance(entry.get("stepKey"), str)
            or _SLUG_RE.fullmatch(entry["stepKey"]) is None
            or not isinstance(effect, str)
            or effect not in _V2_EFFECTS
            or not isinstance(playback_mode, str)
            or playback_mode != _V2_PLAYBACK_MODE[effect]
        ):
            _fail("CINEMATIC_METADATA_MISMATCH", "flattened cinematic cue identity is invalid")
        return TEMPLATE_VERSION_V2, entry["cueId"]
    _fail("CINEMATIC_METADATA_MISMATCH", "flattened cinematic identity is invalid")


def _manifest_asset(entry: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], int, str]:
    version, entry_id = _entry_identity(entry)
    timing = _exact_dict(
        entry.get("timing"), {"durationMs"}, "flattened cinematic timing fields are invalid"
    )
    raw_duration_ms = timing.get("durationMs")
    if not _positive_int(raw_duration_ms):
        _fail("CINEMATIC_METADATA_MISMATCH", "flattened cinematic duration is invalid")
    duration_ms = cast(int, raw_duration_ms)
    if duration_ms % 100 != 0:
        _fail("CINEMATIC_METADATA_MISMATCH", "flattened cinematic duration is invalid")
    if version == TEMPLATE_VERSION_V2 and duration_ms != _V2_DURATION_MS[entry["effect"]]:
        _fail("CINEMATIC_METADATA_MISMATCH", "flattened cinematic cue duration is stale")
    asset = _exact_dict(
        entry.get("asset"),
        {"derivativeId", "path", "url", "sha256", "bytes", "mediaType", "width", "height", "metadata"},
        "flattened cinematic asset fields are invalid",
    )
    derivative_id = asset.get("derivativeId")
    sha256 = asset.get("sha256")
    extension = "mp4" if version == TEMPLATE_VERSION_V1 else "trgb"
    expected_path = f"lessons/derivatives/{derivative_id}/{entry_id}.{extension}"
    asset_url = asset.get("url")
    try:
        parsed_url = urlsplit(asset_url) if isinstance(asset_url, str) else None
    except ValueError:
        parsed_url = None
    url_path = parsed_url.path.lstrip("/") if parsed_url is not None else ""
    url_path_matches = url_path == expected_path or url_path.endswith("/" + expected_path)
    if (
        not isinstance(derivative_id, str)
        or _SHA256_RE.fullmatch(derivative_id) is None
        or asset.get("path") != expected_path
        or parsed_url is None
        or parsed_url.scheme != "https"
        or not parsed_url.netloc
        or parsed_url.username
        or parsed_url.password
        or parsed_url.query
        or parsed_url.fragment
        or not url_path_matches
        or not isinstance(sha256, str)
        or _SHA256_RE.fullmatch(sha256) is None
        or not _positive_int(asset.get("bytes"))
        or asset.get("mediaType")
        != ("video/mp4" if version == TEMPLATE_VERSION_V1 else TRGB_MEDIA_TYPE)
        or type(asset.get("width")) is not int
        or asset.get("width") != 480
        or type(asset.get("height")) is not int
        or asset.get("height") != 320
    ):
        _fail("CINEMATIC_METADATA_MISMATCH", "flattened cinematic asset identity is invalid")
    metadata_fields = (
        {"codec", "fps", "durationMs", "frameCount", "hasAudio"}
        if version == TEMPLATE_VERSION_V1 else _V2_METADATA_FIELDS
    )
    metadata = _exact_dict(
        asset.get("metadata"), metadata_fields, "flattened cinematic metadata fields are invalid"
    )
    common_metadata_invalid = (
        type(metadata.get("fps")) is not int
        or metadata.get("fps") != 10
        or type(metadata.get("durationMs")) is not int
        or metadata.get("durationMs") != duration_ms
        or type(metadata.get("frameCount")) is not int
        or metadata.get("frameCount") != duration_ms // 100
        or metadata.get("hasAudio") is not False
    )
    v1_metadata_invalid = version == TEMPLATE_VERSION_V1 and metadata.get("codec") != "mjpeg"
    v2_metadata_invalid = version == TEMPLATE_VERSION_V2 and (
        metadata.get("codec") != "rgb565le"
        or metadata.get("containerVersion") != 1
        or metadata.get("width") != 480
        or metadata.get("height") != 320
        or metadata.get("storedWidth") != 320
        or metadata.get("storedHeight") != 480
        or metadata.get("orientation") != "panelNativeClockwise"
        or metadata.get("frameBytes") != TRGB_FRAME_BYTES
        or not _positive_int(metadata.get("frameCount"))
        or asset.get("bytes") != trgb_container_bytes(metadata.get("frameCount"))
        or any(
            type(metadata.get(field)) is not int
            for field in (
                "containerVersion", "width", "height", "storedWidth", "storedHeight", "frameBytes"
            )
        )
    )
    if common_metadata_invalid or v1_metadata_invalid or v2_metadata_invalid:
        _fail("CINEMATIC_METADATA_MISMATCH", "flattened cinematic media metadata is invalid")
    return asset, metadata, version, entry_id


def _local_sd_path(asset: dict[str, Any], local_root: str, expected_key: str) -> str:
    local_path = asset.get("localPath")
    sd_path = asset.get("sdPath")
    if local_path is not None and sd_path is not None and local_path != sd_path:
        _fail("CINEMATIC_SD_PATH_MISSING", "flattened cinematic SD aliases disagree")
    path = local_path if isinstance(local_path, str) else sd_path
    root = local_root.rstrip("/")
    if not (
        isinstance(path, str)
        and root
        and (root.startswith("sd://tbot/lesson-assets/") or root.startswith("/sdcard/tbot/lesson-assets/"))
        and path == f"{root}/{expected_key}"
    ):
        _fail("CINEMATIC_SD_PATH_MISSING", "flattened cinematic asset is outside the lesson SD pack")
    relative = path[len(root) + 1 :]
    if (
        relative in {"", ".", ".."}
        or ".." in relative.split("/")
        or any(marker in relative for marker in ("?", "#", "@", "\\"))
        or "://" in relative
    ):
        _fail("CINEMATIC_SD_PATH_MISSING", "flattened cinematic SD path has unsafe syntax")
    return cast(str, path)


def validate_flattened_cinematic_manifest(manifest: Any) -> None:
    """Require the exact renderer-v4 protocol and flattened feature identity."""
    if not isinstance(manifest, dict):
        _fail("CINEMATIC_IDENTITY_UNSUPPORTED", "flattened cinematic manifest is invalid")
    features = manifest.get("features")
    detail = features.get("lessonRendererV4") if isinstance(features, dict) else None
    if (
        manifest.get("manifestVersion") != RENDERER_V4
        or manifest.get("protocolVersion") != RENDERER_V4
        or not isinstance(features, dict)
        or set(features) != {"lessonRendererV4"}
        or not isinstance(detail, dict)
        or set(detail) != {"flattenedMjpegCinematic", "assetSource"}
        or detail.get("flattenedMjpegCinematic") is not True
        or detail.get("assetSource") != "publishedFlattenedDerivative"
    ):
        _fail("CINEMATIC_IDENTITY_UNSUPPORTED", "flattened cinematic renderer identity is invalid")
    phases = manifest.get("cinematicPhases")
    if not isinstance(phases, list) or not phases:
        _fail("CINEMATIC_METADATA_MISMATCH", "flattened cinematic manifest has no phases")
    seen: set[str] = set()
    version: int | None = None
    for phase in phases:
        if not isinstance(phase, dict):
            _fail("CINEMATIC_METADATA_MISMATCH", "flattened cinematic entry is invalid")
        _, _, entry_version, entry_id = _manifest_asset(phase)
        if version is None:
            version = entry_version
        elif version != entry_version:
            _fail("CINEMATIC_METADATA_MISMATCH", "flattened cinematic versions cannot be mixed")
        if entry_id in seen:
            _fail("CINEMATIC_METADATA_MISMATCH", "flattened cinematic identity is duplicated")
        seen.add(entry_id)


def validate_flattened_cinematic_cache_asset(asset: Any) -> None:
    """Validate the exact renderer-v4 v2 TRGB asset after cache projection."""
    if not isinstance(asset, dict):
        _fail("CINEMATIC_METADATA_MISMATCH", "flattened cinematic cache asset is invalid")
    cue_id = asset.get("cueId")
    metadata = asset.get("compatibilityMetadata")
    if not isinstance(cue_id, str) or asset.get("key") != f"flattenedCinematic.{cue_id}":
        _fail("CINEMATIC_METADATA_MISMATCH", "flattened cinematic cache identity is invalid")
    if not isinstance(metadata, dict):
        _fail("CINEMATIC_METADATA_MISMATCH", "flattened cinematic cache metadata is invalid")
    source_url = asset.get("onlineUrl") or asset.get("url")
    try:
        parsed_source_url = urlsplit(source_url) if isinstance(source_url, str) else None
    except ValueError:
        parsed_source_url = None
    normalized_source_url = (
        parsed_source_url._replace(query="", fragment="").geturl()
        if parsed_source_url is not None else None
    )
    projected = {
        "templateId": TEMPLATE_ID,
        "templateVersion": TEMPLATE_VERSION_V2,
        "cueId": cue_id,
        "effect": asset.get("effect"),
        "stepKey": asset.get("stepKey"),
        "playbackMode": asset.get("playbackMode"),
        "timing": {"durationMs": metadata.get("durationMs")},
        "asset": {
            "derivativeId": asset.get("derivativeId"),
            "path": asset.get("path"),
            "url": normalized_source_url,
            "sha256": asset.get("sha256"),
            "bytes": asset.get("size"),
            "mediaType": asset.get("mediaType"),
            "width": metadata.get("width"),
            "height": metadata.get("height"),
            "metadata": metadata,
        },
    }
    _manifest_asset(projected)


def project_flattened_cinematic_phase(phase: Any, pack: Any) -> dict[str, Any]:
    """Project one exact v4 phase to one verified local SD file identity."""
    if not isinstance(pack, dict) or pack.get("ready") is not True:
        _fail("CINEMATIC_PACK_NOT_READY", "verified flattened cinematic pack is not ready")
    local_root = pack.get("localRoot")
    assets = pack.get("assets")
    if not isinstance(local_root, str) or not isinstance(assets, list):
        _fail("CINEMATIC_PACK_NOT_READY", "flattened cinematic pack attestation is incomplete")
    if not isinstance(phase, dict):
        _fail("CINEMATIC_METADATA_MISMATCH", "flattened cinematic entry is invalid")
    source, metadata, version, entry_id = _manifest_asset(phase)
    expected_key = f"flattenedCinematic.{entry_id}"
    matches = [item for item in assets if isinstance(item, dict) and item.get("key") == expected_key]
    if len(matches) != 1:
        identity_field = "phaseId" if version == TEMPLATE_VERSION_V1 else "cueId"
        if any(isinstance(item, dict) and item.get(identity_field) == entry_id for item in assets):
            _fail("CINEMATIC_IDENTITY_UNSUPPORTED", "flattened cinematic pack key is unsupported")
        _fail("CINEMATIC_SD_PATH_MISSING", "flattened cinematic asset is missing from the SD pack")
    packed = matches[0]
    expected_identity = (
        {"phaseId": entry_id}
        if version == TEMPLATE_VERSION_V1
        else {
            "cueId": entry_id,
            "effect": phase["effect"],
            "stepKey": phase["stepKey"],
            "playbackMode": phase["playbackMode"],
        }
    )
    forbidden_identity = (
        {"cueId", "effect", "stepKey", "playbackMode"}
        if version == TEMPLATE_VERSION_V1 else {"phaseId"}
    )
    if (
        packed.get("derivativeId") != source["derivativeId"]
        or any(packed.get(key) != value for key, value in expected_identity.items())
        or any(key in packed for key in forbidden_identity)
    ):
        _fail("CINEMATIC_IDENTITY_UNSUPPORTED", "flattened cinematic derivative identity does not match")
    expected_metadata = (
        {
            "codec": metadata["codec"],
            "width": source["width"],
            "height": source["height"],
            "fps": metadata["fps"],
            "durationMs": metadata["durationMs"],
            "frameCount": metadata["frameCount"],
            "hasAudio": metadata["hasAudio"],
        }
        if version == TEMPLATE_VERSION_V1
        else dict(metadata)
    )
    if (
        packed.get("state") != "READY"
        or packed.get("checksumOk") is not True
        or packed.get("sha256") != source["sha256"]
        or packed.get("size") != source["bytes"]
        or packed.get("mediaType") != source["mediaType"]
        or packed.get("compatibilityMetadata") != expected_metadata
    ):
        _fail("CINEMATIC_METADATA_MISMATCH", "flattened cinematic pack metadata does not match")
    sd_path = _local_sd_path(packed, local_root, expected_key)
    common_asset = {
        "derivativeId": source["derivativeId"],
        "sdPath": sd_path,
        "sha256": source["sha256"],
        "bytes": source["bytes"],
        "mediaType": source["mediaType"],
        "width": source["width"],
        "height": source["height"],
    }
    if version == TEMPLATE_VERSION_V1:
        return {
            "templateId": TEMPLATE_ID,
            "templateVersion": TEMPLATE_VERSION_V1,
            "phaseId": entry_id,
            "durationMs": phase["timing"]["durationMs"],
            "fps": metadata["fps"],
            "frameCount": metadata["frameCount"],
            "asset": {
                "derivativeId": source["derivativeId"],
                "phaseId": entry_id,
                "sdPath": sd_path,
                "sha256": source["sha256"],
                "bytes": source["bytes"],
                "mediaType": source["mediaType"],
                "width": source["width"],
                "height": source["height"],
            },
        }
    return {
        "templateId": TEMPLATE_ID,
        "templateVersion": TEMPLATE_VERSION_V2,
        "cueId": entry_id,
        "effect": phase["effect"],
        "stepKey": phase["stepKey"],
        "playbackMode": phase["playbackMode"],
        "durationMs": phase["timing"]["durationMs"],
        "fps": metadata["fps"],
        "frameCount": metadata["frameCount"],
        "asset": {
            "derivativeId": source["derivativeId"],
            "cueId": entry_id,
            **common_asset,
            "compatibilityMetadata": dict(metadata),
        },
    }
