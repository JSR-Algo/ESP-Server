#!/usr/bin/env python3
"""Validate a redacted physical receipt against one curriculum-v5 candidate."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import course_mode_physical_tft_preflight as physical_preflight
from course_mode_candidate_manifest import (
    MAX_CANDIDATE_BYTES,
    _parse_rfc3339_utc,
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
    repositories = candidate.get("repositories")
    repo = repositories.get("adminEsp", {}) if isinstance(repositories, dict) else {}
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
    tools = candidate.get("tools")
    repositories = candidate.get("repositories")
    binding = tools.get("physicalEvidence") if isinstance(tools, dict) else None
    repo = repositories.get("adminEsp", {}) if isinstance(repositories, dict) else {}
    binding_fields = {
        "path",
        "repositorySha",
        "sha256",
        "identity",
        "signaturePath",
        "signatureSha256",
        "signerFingerprint",
    }
    if not isinstance(binding, dict) or set(binding) != binding_fields:
        return None
    if binding.get("repositorySha") != repo.get("sha"):
        return None
    path = binding.get("path")
    signature_path = binding.get("signaturePath")
    exceptions = repo.get("dirtyExceptions", [])
    matching = [item for item in exceptions if isinstance(item, dict) and item.get("path") == path]
    signature_matching = [item for item in exceptions if isinstance(item, dict) and item.get("path") == signature_path]
    if (
        not isinstance(path, str)
        or Path(path).is_absolute()
        or ".." in Path(path).parts
        or not isinstance(signature_path, str)
        or Path(signature_path).is_absolute()
        or ".." in Path(signature_path).parts
        or len(matching) > 1
        or len(signature_matching) > 1
    ):
        return None
    if matching and matching[0].get("sha256") != binding.get("sha256"):
        return None
    if signature_matching and signature_matching[0].get("sha256") != binding.get("signatureSha256"):
        return None
    root = repo.get("path")
    digest, error = _secure_hash_relative(Path(root), path) if isinstance(root, str) else (None, "path")
    if error is not None or digest != binding.get("sha256"):
        return None
    try:
        parsed = strict_json_loads(read_secure_regular(Path(root) / path, MAX_RECEIPT_BYTES))
        signature = read_secure_regular(Path(root) / signature_path, 256)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return None
    if hashlib.sha256(signature).hexdigest() != binding.get("signatureSha256") or len(signature) != 64:
        return None
    valid, fingerprint = physical_preflight._verify_pinned_identity_signature(
        physical_preflight._canonical_bytes(parsed),
        signature,
    )
    if not valid or fingerprint != binding.get("signerFingerprint"):
        return "signature-invalid"
    if parsed != binding.get("identity"):
        return None
    curriculum = candidate.get("curriculum")
    database = candidate.get("database")
    expected_binding = {
        "candidateId": candidate.get("candidateId"),
        "createdAt": candidate.get("createdAt"),
        "expiresAt": candidate.get("expiresAt"),
        "course": candidate.get("course"),
        "curriculum": {
            "sourceChecksum": curriculum.get("sourceChecksum"),
            "rendererId": curriculum.get("rendererId"),
            "contractIdentity": curriculum.get("contractIdentity"),
        }
        if isinstance(curriculum, dict)
        else None,
        "repositories": {name: value.get("sha") for name, value in repositories.items() if isinstance(value, dict)}
        if isinstance(repositories, dict)
        else None,
        "images": candidate.get("images"),
        "firmware": candidate.get("firmware"),
        "database": database,
        "protectedSource": _protected(candidate),
    }
    if parsed.get("candidateBinding") != expected_binding or not isinstance(database, dict):
        return "signature-invalid"
    return parsed


def validate_receipt(document: object, candidate: object, *, now: datetime | None = None) -> list[str]:
    if not isinstance(document, dict) or not isinstance(candidate, dict):
        return ["receipt.schema"]
    try:
        candidate_reasons = validate_candidate(candidate, now=now)
    except (AttributeError, KeyError, TypeError, ValueError):
        candidate_reasons = ["type"]
    reasons = [f"candidate.{reason}" for reason in candidate_reasons]
    if set(document) != FIELDS or document.get("schemaVersion") != 1:
        reasons.append("receipt.schema")
    if document.get("candidateId") != candidate.get("candidateId"):
        reasons.append("receipt.candidate")
    if document.get("result") != "PASS":
        reasons.append("receipt.result")
    captured = _parse_rfc3339_utc(document.get("capturedAt"))
    current = now or datetime.now(timezone.utc)
    created = _parse_rfc3339_utc(candidate.get("createdAt"))
    if captured is None:
        reasons.append("receipt.timestamp.utc")
    elif (created and captured < created) or captured > current or (current - captured).total_seconds() > 7 * 86400:
        reasons.append("receipt.timestamp.stale")
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
    curriculum_value = candidate.get("curriculum")
    curriculum = curriculum_value if isinstance(curriculum_value, dict) else {}
    expected_evidence = _physical_identity(candidate)
    if expected_evidence == "signature-invalid":
        reasons.append("candidate.physicalEvidence.signature")
        expected_evidence = {}
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
        or replacement
        != (candidate.get("database", {}).get("replacement") if isinstance(candidate.get("database"), dict) else None)
        or replacement != expected_evidence.get("replacement")
    ):
        reasons.append("receipt.replacement")
    repositories = candidate.get("repositories")
    repositories = repositories if isinstance(repositories, dict) else {}
    expected_repositories = {name: value.get("sha") for name, value in repositories.items() if isinstance(value, dict)}
    if (
        document.get("repositories") != expected_repositories
        or set(expected_repositories) != {"backend", "adminEsp", "firmware"}
        or not all(SHA40.fullmatch(value or "") for value in expected_repositories.values())
    ):
        reasons.append("receipt.repositories")
    images = candidate.get("images")
    if document.get("backendImage") != (images.get("backend") if isinstance(images, dict) else None) or document.get(
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
        or journey
        != (candidate.get("database", {}).get("journey") if isinstance(candidate.get("database"), dict) else None)
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
        or database
        != (
            candidate.get("database", {}).get("terminalReadback")
            if isinstance(candidate.get("database"), dict)
            else None
        )
        or database != expected_evidence.get("database")
    ):
        reasons.append("receipt.database")
    evidence = document.get("evidence")
    expected_artifacts = expected_evidence.get("evidenceArtifacts")
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
        or len({item.get("path") for item in evidence if isinstance(item, dict)}) != len(evidence)
        or not isinstance(expected_artifacts, dict)
        or {item.get("path"): item.get("sha256") for item in evidence if isinstance(item, dict)} != expected_artifacts
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
