"""Pure renderer-v4 flattened cinematic projection from an attested SD pack."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

RENDERER_V4 = "teebot-lesson-renderer.v4"
TEMPLATE_ID = "flattenedMjpegCinematic"
TEMPLATE_VERSION = 1
KNOWN_PHASE_IDS = frozenset(
    {"opening", "greet", "teach", "listen", "thinking", "correct", "retry", "celebrate"}
)
_SHA256_RE = re.compile(r"[0-9a-f]{64}")


class FlattenedCinematicContractError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def _fail(code: str, message: str) -> None:
    raise FlattenedCinematicContractError(code, message)


def _positive_int(value: Any) -> bool:
    return type(value) is int and value > 0


def _exact_dict(value: Any, fields: set[str], message: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        _fail("CINEMATIC_METADATA_MISMATCH", message)
    return value


def _manifest_asset(phase: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    phase_id = phase.get("phaseId")
    if (
        phase.get("templateId") != TEMPLATE_ID
        or phase.get("templateVersion") != TEMPLATE_VERSION
        or phase_id not in KNOWN_PHASE_IDS
    ):
        _fail("CINEMATIC_METADATA_MISMATCH", "flattened cinematic identity is invalid")
    timing = _exact_dict(
        phase.get("timing"), {"durationMs"}, "flattened cinematic timing fields are invalid"
    )
    duration_ms = timing.get("durationMs")
    if not _positive_int(duration_ms) or duration_ms % 100 != 0:
        _fail("CINEMATIC_METADATA_MISMATCH", "flattened cinematic duration is invalid")
    asset = _exact_dict(
        phase.get("asset"),
        {"derivativeId", "path", "url", "sha256", "bytes", "mediaType", "width", "height", "metadata"},
        "flattened cinematic asset fields are invalid",
    )
    derivative_id = asset.get("derivativeId")
    expected_path = f"lessons/derivatives/{derivative_id}/{phase_id}.mp4"
    try:
        parsed_url = urlsplit(asset.get("url"))
    except (TypeError, ValueError):
        parsed_url = None
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
        or parsed_url.path.lstrip("/") != expected_path
        or _SHA256_RE.fullmatch(str(asset.get("sha256") or "")) is None
        or not _positive_int(asset.get("bytes"))
        or asset.get("mediaType") != "video/mp4"
        or asset.get("width") != 480
        or asset.get("height") != 320
    ):
        _fail("CINEMATIC_METADATA_MISMATCH", "flattened cinematic asset identity is invalid")
    metadata = _exact_dict(
        asset.get("metadata"),
        {"codec", "fps", "durationMs", "frameCount", "hasAudio"},
        "flattened cinematic metadata fields are invalid",
    )
    if (
        metadata.get("codec") != "mjpeg"
        or metadata.get("fps") != 10
        or metadata.get("durationMs") != duration_ms
        or metadata.get("frameCount") != duration_ms // 100
        or metadata.get("hasAudio") is not False
    ):
        _fail("CINEMATIC_METADATA_MISMATCH", "flattened cinematic media metadata is invalid")
    return asset, metadata


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
    return path


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
    for phase in phases:
        exact = _exact_dict(
            phase,
            {"templateId", "templateVersion", "phaseId", "timing", "asset"},
            "flattened cinematic phase fields are invalid",
        )
        _manifest_asset(exact)
        if exact["phaseId"] in seen:
            _fail("CINEMATIC_METADATA_MISMATCH", "flattened cinematic phase is duplicated")
        seen.add(exact["phaseId"])


def project_flattened_cinematic_phase(phase: Any, pack: Any) -> dict[str, Any]:
    """Project one exact v4 phase to one verified local SD file identity."""
    if not isinstance(pack, dict) or pack.get("ready") is not True:
        _fail("CINEMATIC_PACK_NOT_READY", "verified flattened cinematic pack is not ready")
    local_root = pack.get("localRoot")
    assets = pack.get("assets")
    if not isinstance(local_root, str) or not isinstance(assets, list):
        _fail("CINEMATIC_PACK_NOT_READY", "flattened cinematic pack attestation is incomplete")
    phase = _exact_dict(
        phase,
        {"templateId", "templateVersion", "phaseId", "timing", "asset"},
        "flattened cinematic phase fields are invalid",
    )
    source, metadata = _manifest_asset(phase)
    phase_id = phase["phaseId"]
    expected_key = f"flattenedCinematic.{phase_id}"
    matches = [item for item in assets if isinstance(item, dict) and item.get("key") == expected_key]
    if len(matches) != 1:
        if any(isinstance(item, dict) and item.get("phaseId") == phase_id for item in assets):
            _fail("CINEMATIC_IDENTITY_UNSUPPORTED", "flattened cinematic pack key is unsupported")
        _fail("CINEMATIC_SD_PATH_MISSING", "flattened cinematic asset is missing from the SD pack")
    packed = matches[0]
    if packed.get("derivativeId") != source["derivativeId"] or packed.get("phaseId") != phase_id:
        _fail("CINEMATIC_IDENTITY_UNSUPPORTED", "flattened cinematic derivative identity does not match")
    expected_metadata = {
        "codec": metadata["codec"],
        "width": source["width"],
        "height": source["height"],
        "fps": metadata["fps"],
        "durationMs": metadata["durationMs"],
        "frameCount": metadata["frameCount"],
        "hasAudio": metadata["hasAudio"],
    }
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
    return {
        "templateId": TEMPLATE_ID,
        "templateVersion": TEMPLATE_VERSION,
        "phaseId": phase_id,
        "durationMs": phase["timing"]["durationMs"],
        "fps": metadata["fps"],
        "frameCount": metadata["frameCount"],
        "asset": {
            "derivativeId": source["derivativeId"],
            "phaseId": phase_id,
            "sdPath": sd_path,
            "sha256": source["sha256"],
            "bytes": source["bytes"],
            "mediaType": source["mediaType"],
            "width": source["width"],
            "height": source["height"],
        },
    }
