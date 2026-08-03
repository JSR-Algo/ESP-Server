#!/usr/bin/env python3
"""Generate the reproducible Task 4 Farm TRGB contract artifact."""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.lesson.flattened_cinematic_contract import trgb_container_bytes
from scripts.project_tvideo_farm_firmware_fixture import build_firmware_fixture

GENERATOR_VERSION = "tvideo-farm-trgb-task4.v1"
TRGB_MEDIA_TYPE = "application/vnd.tbot.rgb565-indexed"
REPO_ROOT = Path(__file__).resolve().parents[1]
STATIC_PAYLOAD_ROOT = REPO_ROOT / "tests" / "fixtures" / "tvideo_farm_task4_static"
STATIC_ASSETS = (
    ("scene.farm", "scene.farm.sunny.png", "backgroundScene", "poster", True),
    ("object.farm", "object.farm.barn.png", "teachingObject", "primarySubject", True),
    ("object.farm.corn", "object.farm.corn.png", "teachingObject", "supportingSubject", False),
    ("object.farm.cow", "object.farm.cow.png", "teachingObject", "supportingSubject", False),
    ("object.farm.hen", "object.farm.hen.png", "teachingObject", "supportingSubject", False),
    ("robotOverlay.teach", "robotOverlay.teach.png", "robotOverlay", "pose", False),
    ("robotOverlay.listening", "robotOverlay.listening.png", "robotOverlay", "pose", False),
    ("robotOverlay.thinking", "robotOverlay.thinking.png", "robotOverlay", "pose", False),
)


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical_sha256(value: Any) -> str:
    canonical = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return _sha256_bytes(canonical.encode())


def _png_dimensions(payload: bytes) -> tuple[int, int]:
    if len(payload) < 24 or payload[:8] != b"\x89PNG\r\n\x1a\n" or payload[12:16] != b"IHDR":
        raise ValueError("static payload is not a valid PNG")
    return struct.unpack(">II", payload[16:24])


def _static_asset(
    payload_root: Path,
    asset_id: str,
    filename: str,
    layer: str,
    role: str,
    critical: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    payload_path = payload_root / filename
    try:
        payload = payload_path.read_bytes()
    except OSError as exc:
        raise ValueError(f"static payload is unavailable: {filename}") from exc
    width, height = _png_dimensions(payload)
    relative_path = f"tvideo_farm_task4_static/{filename}"
    attestation = {
        "path": f"tests/fixtures/{relative_path}",
        "sha256": _sha256_bytes(payload),
        "bytes": len(payload),
    }
    return {
        "assetId": asset_id,
        "id": asset_id,
        "layer": layer,
        "role": role,
        "mediaType": "image/png",
        "path": relative_path,
        "url": f"https://fixtures.example.test/{relative_path}",
        "sha256": attestation["sha256"],
        "bytes": attestation["bytes"],
        "dimensions": {"width": width, "height": height},
        "critical": critical,
    }, attestation


def _trgb_manifest(source_manifest: Any, payload_root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not isinstance(source_manifest, dict):
        raise ValueError("source fixture must be a JSON object")
    manifest = deepcopy(source_manifest)
    assets = manifest.get("assets")
    phases = manifest.get("cinematicPhases")
    if not isinstance(assets, list) or len(assets) != 3 or not isinstance(phases, list) or len(phases) != 19:
        raise ValueError("source fixture must contain exactly 3 static assets and 19 cinematic cues")
    static_records = [_static_asset(payload_root, *definition) for definition in STATIC_ASSETS]
    manifest["assets"] = [record for record, _attestation in static_records]
    for phase in phases:
        if not isinstance(phase, dict) or not isinstance(phase.get("asset"), dict):
            raise ValueError("source fixture cinematic cue is invalid")
        cue_id = phase.get("cueId")
        timing = phase.get("timing")
        asset = phase["asset"]
        if (
            not isinstance(cue_id, str)
            or not isinstance(timing, dict)
            or type(timing.get("durationMs")) is not int
        ):
            raise ValueError("source fixture cinematic timeline is invalid")
        duration_ms = timing["durationMs"]
        frame_count = duration_ms // 100
        derivative_id = asset.get("derivativeId")
        if not isinstance(derivative_id, str):
            raise ValueError("source fixture derivative identity is invalid")
        asset["path"] = f"lessons/derivatives/{derivative_id}/{cue_id}.trgb"
        asset["url"] = f"https://fixtures.example.test/{asset['path']}"
        asset["mediaType"] = TRGB_MEDIA_TYPE
        asset["width"] = 480
        asset["height"] = 320
        asset["bytes"] = trgb_container_bytes(frame_count)
        asset["metadata"] = {
            "codec": "rgb565le",
            "containerVersion": 1,
            "width": 480,
            "height": 320,
            "storedWidth": 320,
            "storedHeight": 480,
            "orientation": "panelNativeClockwise",
            "fps": 10,
            "durationMs": duration_ms,
            "frameCount": frame_count,
            "frameBytes": 307200,
            "hasAudio": False,
        }
    return manifest, [attestation for _record, attestation in static_records]


def generate_fixture(
    *,
    source: Path,
    output: Path,
    provenance_output: Path,
    payload_root: Path = STATIC_PAYLOAD_ROOT,
) -> dict[str, Any]:
    source = source.resolve(strict=True)
    output = output.resolve()
    provenance_output = provenance_output.resolve()
    source_payload = source.read_bytes()
    source_manifest = json.loads(source_payload)
    manifest, static_payloads = _trgb_manifest(source_manifest, payload_root.resolve())
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(f"{json.dumps(manifest, ensure_ascii=False, indent=2)}\n", encoding="utf-8")

    canonical_sha256 = _canonical_sha256(manifest)
    firmware_fixture = build_firmware_fixture(manifest, manifest_checksum=canonical_sha256)
    provenance = {
        "schemaVersion": 1,
        "source": {
            "file": source.name,
            "fileSha256": _sha256_bytes(source_payload),
            "canonicalSha256": _canonical_sha256(source_manifest),
        },
        "staticPayloads": static_payloads,
        "generator": {
            "version": GENERATOR_VERSION,
            "fileSha256": _sha256_bytes(Path(__file__).resolve().read_bytes()),
        },
        "artifact": {
            "fileSha256": _sha256_bytes(output.read_bytes()),
            "canonicalSha256": canonical_sha256,
            "manifestChecksum": canonical_sha256,
            "cueCount": 19,
            "staticAssetCount": 8,
            "totalAssetCount": 27,
        },
        "firmwareFixture": {
            "canonicalSha256": _canonical_sha256(firmware_fixture),
            "frameCount": len(firmware_fixture["frames"]),
            "cueCount": 19,
        },
    }
    provenance_output.parent.mkdir(parents=True, exist_ok=True)
    provenance_output.write_text(
        f"{json.dumps(provenance, ensure_ascii=False, indent=2)}\n",
        encoding="utf-8",
    )
    return provenance


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--provenance-output", type=Path, required=True)
    args = parser.parse_args()
    generate_fixture(source=args.source, output=args.output, provenance_output=args.provenance_output)


if __name__ == "__main__":
    main()
