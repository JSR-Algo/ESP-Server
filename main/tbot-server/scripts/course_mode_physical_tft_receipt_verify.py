#!/usr/bin/env python3
"""Validate a redacted physical receipt against one curriculum-v5 candidate."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
FIELDS = {
    "schemaVersion",
    "candidateId",
    "result",
    "capturedAt",
    "course",
    "lesson",
    "replacement",
    "renderer",
    "repositories",
    "backendImage",
    "firmware",
    "protectedSource",
    "device",
    "journey",
    "database",
    "evidence",
}


def _sha(value: object) -> bool:
    return isinstance(value, str) and SHA256.fullmatch(value) is not None


def _nonempty(value: object) -> bool:
    return isinstance(value, str) and bool(value)


def _protected(candidate: dict) -> object:
    repo = candidate.get("repositories", {}).get("adminEsp", {})
    repository_binding = {
        "path": "main/tbot-server/tests/test_lesson_voice_output_discipline.py",
        "repositorySha": repo.get("sha"),
        "binding": "repository",
    }
    matches = [
        item
        for item in repo.get("dirtyExceptions", [])
        if isinstance(item, dict)
        and item.get("path") == "main/tbot-server/tests/test_lesson_voice_output_discipline.py"
    ]
    return (
        {**matches[0], "repositorySha": repo.get("sha"), "binding": "dirtyException"}
        if len(matches) == 1
        else repository_binding
        if not matches
        else None
    )


def validate_receipt(document: object, candidate: object) -> list[str]:
    if not isinstance(document, dict) or not isinstance(candidate, dict):
        return ["receipt.schema"]
    reasons = []
    if set(document) != FIELDS or document.get("schemaVersion") != 1:
        reasons.append("receipt.schema")
    if document.get("candidateId") != candidate.get("candidateId"):
        reasons.append("receipt.candidate")
    if document.get("result") != "PASS":
        reasons.append("receipt.result")
    if document.get("course") != candidate.get("course"):
        reasons.append("receipt.course")
    lesson = document.get("lesson")
    if (
        not isinstance(lesson, dict)
        or set(lesson) != {"lessonId", "lessonKey", "lessonVersion"}
        or lesson.get("lessonKey") == "course-mode-pilot-cat-ball"
        or not all(
            (
                _nonempty(lesson.get("lessonId")),
                _nonempty(lesson.get("lessonKey")),
                type(lesson.get("lessonVersion")) is int,
            )
        )
    ):
        reasons.append("receipt.lesson")
    renderer = document.get("renderer")
    curriculum = candidate.get("curriculum", {})
    if (
        not isinstance(renderer, dict)
        or set(renderer) != {"rendererId", "contractIdentity", "contractChecksum", "manifestChecksum", "assetChecksums"}
        or renderer.get("rendererId") != curriculum.get("rendererId")
        or renderer.get("rendererId") != "teebot-lesson-renderer.v5"
        or renderer.get("contractIdentity") != curriculum.get("contractIdentity")
        or not _sha(renderer.get("contractChecksum"))
        or not _sha(renderer.get("manifestChecksum"))
        or not isinstance(renderer.get("assetChecksums"), list)
        or not renderer["assetChecksums"]
        or not all(_sha(item) for item in renderer["assetChecksums"])
    ):
        reasons.append("receipt.renderer")
    replacement = document.get("replacement")
    if (
        not isinstance(replacement, dict)
        or set(replacement)
        != {"sourceLessonId", "replacementLessonId", "materializationReceiptSha256", "cutoverReceiptSha256"}
        or replacement.get("replacementLessonId") != (lesson or {}).get("lessonId")
        or replacement.get("sourceLessonId") == replacement.get("replacementLessonId")
        or not _sha(replacement.get("materializationReceiptSha256"))
        or not _sha(replacement.get("cutoverReceiptSha256"))
    ):
        reasons.append("receipt.replacement")
    repositories = candidate.get("repositories", {})
    expected_repositories = {name: value.get("sha") for name, value in repositories.items() if isinstance(value, dict)}
    if (
        document.get("repositories") != expected_repositories
        or set(expected_repositories) != {"backend", "adminEsp", "firmware"}
        or not all(SHA40.fullmatch(value or "") for value in expected_repositories.values())
    ):
        reasons.append("receipt.repositories")
    if document.get("backendImage") != candidate.get("images", {}).get("backend"):
        reasons.append("receipt.image")
    if document.get("firmware") != candidate.get("firmware"):
        reasons.append("receipt.firmware")
    if document.get("protectedSource") != _protected(candidate):
        reasons.append("receipt.protected_source")
    device = document.get("device")
    if (
        not isinstance(device, dict)
        or set(device) != {"macSuffix", "appOffset", "partitionTableSha256", "nvsBeforeSha256", "nvsAfterSha256"}
        or device.get("macSuffix") != "AC:20"
        or device.get("appOffset") != "0x20000"
        or not all(_sha(device.get(field)) for field in ("partitionTableSha256", "nvsBeforeSha256", "nvsAfterSha256"))
        or device.get("nvsBeforeSha256") != device.get("nvsAfterSha256")
    ):
        reasons.append("receipt.device")
    journey = document.get("journey")
    if (
        not isinstance(journey, dict)
        or set(journey) != {"assignmentId", "lessonSessionId", "deliveryId"}
        or not all(_nonempty(journey.get(field)) for field in journey)
    ):
        reasons.append("receipt.journey")
    database = document.get("database")
    if (
        not isinstance(database, dict)
        or set(database) != {"terminalState", "completionCount", "progressCount"}
        or database.get("terminalState") != "COMPLETED"
        or database.get("completionCount") != 1
        or type(database.get("progressCount")) is not int
        or database["progressCount"] <= 0
    ):
        reasons.append("receipt.database")
    evidence = document.get("evidence")
    if (
        not isinstance(evidence, list)
        or not evidence
        or any(
            not isinstance(item, dict)
            or set(item) != {"path", "sha256"}
            or not _nonempty(item.get("path"))
            or not _sha(item.get("sha256"))
            for item in evidence
        )
    ):
        reasons.append("receipt.evidence")
    return sorted(set(reasons))


def validate_receipt_pair(first: object, second: object | None, candidate: object) -> list[str]:
    reasons = validate_receipt(first, candidate)
    if second is not None:
        reasons.extend(validate_receipt(second, candidate))
        if first != second:
            reasons.append("receipt.rerun")
    return sorted(set(reasons))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("receipt", type=Path)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--rerun-receipt", type=Path)
    args = parser.parse_args(argv)
    try:
        candidate = json.loads(args.candidate.read_text(encoding="utf-8"))
        receipt = json.loads(args.receipt.read_text(encoding="utf-8"))
        rerun = json.loads(args.rerun_receipt.read_text(encoding="utf-8")) if args.rerun_receipt else None
        reasons = validate_receipt_pair(receipt, rerun, candidate)
        candidate_id = candidate.get("candidateId") if isinstance(candidate, dict) else None
    except (OSError, UnicodeError, json.JSONDecodeError):
        reasons, candidate_id = ["input.invalid"], None
    print(
        json.dumps(
            {"candidateId": candidate_id, "reasons": reasons, "valid": not reasons},
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0 if not reasons else 1


if __name__ == "__main__":
    raise SystemExit(main())
