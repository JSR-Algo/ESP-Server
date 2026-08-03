#!/usr/bin/env python3
"""Project the canonical backend farm manifest into strict firmware wire frames."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.lesson.flattened_cinematic_contract import (  # noqa: E402
    project_flattened_cinematic_phase,
    validate_flattened_cinematic_manifest,
)

DEFAULT_MANIFEST_CHECKSUM = "bb7d4dcdf6318096c0b9224dc48bcdcb3ff78b325706cdc9c5d39bd4e7da94e4"


def build_firmware_fixture(
    manifest: dict[str, Any],
    *,
    manifest_checksum: str = DEFAULT_MANIFEST_CHECKSUM,
) -> dict[str, Any]:
    validate_flattened_cinematic_manifest(manifest)
    canonical = json.dumps(manifest, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    cache_key = f"{manifest['lessonId']}/v{manifest['lessonVersion']}-{manifest_checksum}"
    local_root = f"sd://tbot/lesson-assets/{cache_key}"
    pack_assets = []
    for phase in manifest["cinematicPhases"]:
        source = phase["asset"]
        path = f"{local_root}/flattenedCinematic.{phase['cueId']}"
        pack_assets.append(
            {
                "key": f"flattenedCinematic.{phase['cueId']}",
                "state": "READY",
                "checksumOk": True,
                "localPath": path,
                "sdPath": path,
                "sha256": source["sha256"],
                "size": source["bytes"],
                "mediaType": source["mediaType"],
                "derivativeId": source["derivativeId"],
                "cueId": phase["cueId"],
                "effect": phase["effect"],
                "stepKey": phase["stepKey"],
                "playbackMode": phase["playbackMode"],
                "compatibilityMetadata": {
                    **source["metadata"],
                    "width": source["width"],
                    "height": source["height"],
                },
            }
        )
    pack = {"ready": True, "localRoot": local_root, "assets": pack_assets}
    frames: list[dict[str, Any]] = []
    sequence = 1
    for phase in manifest["cinematicPhases"]:
        projected = project_flattened_cinematic_phase(phase, pack)
        step_id = phase["cueId"].split("-to-", 1)[0] if phase["effect"] == "word-transition" else phase["stepKey"]
        prepare = {"command": "prepare", **projected, "commandSequenceId": sequence}
        frames.append(
            _envelope(
                manifest,
                "lesson_prepare",
                step_id,
                sequence,
                {
                    "profile": "espTft",
                    "cinematicPhase": prepare,
                },
            )
        )
        sequence += 1
        frames.append(
            _envelope(
                manifest,
                "lesson_cinematic_control",
                step_id,
                sequence,
                {
                    "command": "start",
                    "cueId": phase["cueId"],
                    "commandSequenceId": sequence,
                },
            )
        )
        sequence += 1
    return {
        "schemaVersion": "tvideo-farm-command.v2",
        "softwareOnly": True,
        "hardwareStatus": "PENDING_ATTENDED_HARDWARE",
        "source": {
            "lessonId": manifest["lessonId"],
            "lessonVersion": manifest["lessonVersion"],
            "manifestChecksum": manifest_checksum,
            "canonicalManifestSha256": hashlib.sha256(canonical.encode()).hexdigest(),
            "cueCount": len(manifest["cinematicPhases"]),
        },
        "frames": frames,
    }


def _envelope(
    manifest: dict[str, Any],
    frame_type: str,
    step_id: str,
    sequence: int,
    body: dict[str, Any],
) -> dict[str, Any]:
    return {
        "type": frame_type,
        "protocolVersion": "teebot-lesson-renderer.v4",
        "assignmentId": "fixture-assignment",
        "sessionId": "fixture-session",
        "lessonId": manifest["lessonId"],
        "lessonVersion": manifest["lessonVersion"],
        "stepId": step_id,
        "sequence": sequence,
        "timestamp": 0,
        "body": body,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest-checksum", default=DEFAULT_MANIFEST_CHECKSUM)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.resolve(strict=True).read_text(encoding="utf-8"))
    fixture = build_firmware_fixture(manifest, manifest_checksum=args.manifest_checksum)
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(f"{json.dumps(fixture, ensure_ascii=False, indent=2)}\n", encoding="utf-8")
    canonical = json.dumps(fixture, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    print(hashlib.sha256(canonical.encode()).hexdigest())


if __name__ == "__main__":
    main()
