#!/usr/bin/env python3
"""Validate a redacted physical receipt against one curriculum-v5 candidate."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from course_mode_candidate_manifest import (
    MAX_CANDIDATE_BYTES,
    _secure_hash_relative,
    read_secure_regular,
    strict_json_loads,
    validate_candidate,
)

SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
MAX_RECEIPT_BYTES = 1024 * 1024
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
    path = "main/tbot-server/tests/test_lesson_voice_output_discipline.py"
    root_value = repo.get("path")
    digest, error = _secure_hash_relative(Path(root_value), path) if isinstance(root_value, str) else (None, "path")
    repository_binding = {
        "path": path,
        "repositorySha": repo.get("sha"),
        "binding": "repository",
        "sha256": digest,
    }
    matches = [item for item in repo.get("dirtyExceptions", []) if isinstance(item, dict) and item.get("path") == path]
    if error is not None:
        return None
    return (
        {**matches[0], "repositorySha": repo.get("sha"), "binding": "dirtyException"}
        if len(matches) == 1
        else repository_binding
        if not matches
        else None
    )


def _physical_identity(candidate: dict) -> object:
    binding = candidate.get("tools", {}).get("physicalEvidence")
    repo = candidate.get("repositories", {}).get("adminEsp", {})
    if not isinstance(binding, dict) or set(binding) != {"path", "repositorySha", "sha256", "identity"}:
        return None
    if binding.get("repositorySha") != repo.get("sha"):
        return None
    path = binding.get("path")
    exceptions = repo.get("dirtyExceptions", [])
    matching = [item for item in exceptions if isinstance(item, dict) and item.get("path") == path]
    if not isinstance(path, str) or len(matching) > 1:
        return None
    if matching and matching[0].get("sha256") != binding.get("sha256"):
        return None
    root = repo.get("path")
    digest, error = _secure_hash_relative(Path(root), path) if isinstance(root, str) else (None, "path")
    if error is not None or digest != binding.get("sha256"):
        return None
    try:
        parsed = strict_json_loads(read_secure_regular(Path(root) / path, MAX_RECEIPT_BYTES))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return None
    return parsed if parsed == binding.get("identity") else None


def validate_receipt(document: object, candidate: object) -> list[str]:
    if not isinstance(document, dict) or not isinstance(candidate, dict):
        return ["receipt.schema"]
    candidate_reasons = validate_candidate(candidate)
    reasons = [f"candidate.{reason}" for reason in candidate_reasons]
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
    expected_evidence = _physical_identity(candidate)
    if not isinstance(expected_evidence, dict):
        reasons.append("candidate.physicalEvidence")
        expected_evidence = {}
    if lesson != expected_evidence.get("lesson"):
        reasons.append("receipt.lesson")
    if (
        not isinstance(renderer, dict)
        or set(renderer) != {"rendererId", "contractIdentity", "contractChecksum", "manifestChecksum", "assetChecksums"}
        or renderer.get("rendererId") != curriculum.get("rendererId")
        or renderer.get("rendererId") != "teebot-lesson-renderer.v5"
        or renderer.get("contractIdentity") != curriculum.get("contractIdentity")
        or renderer != expected_evidence.get("renderer")
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
        or replacement != candidate.get("database", {}).get("replacement")
        or replacement != expected_evidence.get("replacement")
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
    if document.get("backendImage") != candidate.get("images", {}).get("backend") or document.get(
        "backendImage"
    ) != expected_evidence.get("backendImage"):
        reasons.append("receipt.image")
    if document.get("firmware") != candidate.get("firmware") or document.get("firmware") != expected_evidence.get(
        "firmware"
    ):
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
        or device != expected_evidence.get("device")
    ):
        reasons.append("receipt.device")
    journey = document.get("journey")
    if (
        not isinstance(journey, dict)
        or set(journey) != {"assignmentId", "lessonSessionId", "deliveryId"}
        or not all(_nonempty(journey.get(field)) for field in journey)
        or journey != candidate.get("database", {}).get("journey")
        or journey != expected_evidence.get("journey")
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
        or database != candidate.get("database", {}).get("terminalReadback")
        or database != expected_evidence.get("database")
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
        candidate = strict_json_loads(read_secure_regular(args.candidate, MAX_CANDIDATE_BYTES))
        receipt = strict_json_loads(read_secure_regular(args.receipt, MAX_RECEIPT_BYTES))
        rerun = (
            strict_json_loads(read_secure_regular(args.rerun_receipt, MAX_RECEIPT_BYTES))
            if args.rerun_receipt
            else None
        )
        reasons = validate_receipt_pair(receipt, rerun, candidate)
        candidate_id = candidate.get("candidateId") if isinstance(candidate, dict) else None
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
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
