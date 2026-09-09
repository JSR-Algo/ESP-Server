#!/usr/bin/env python3
"""Audit bounded software-only Course Mode release evidence."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import os
import re
import stat
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from course_mode_evidence_privacy import has_sanitization_failure, is_sanitized_manifest, scan_evidence_payload
import course_mode_software_evidence_snapshot as snapshot

SHA40 = re.compile(r"[0-9a-f]{40}")
SHA256 = re.compile(r"[0-9a-f]{64}")
CANDIDATE_ID = re.compile(r"course-mode-[0-9]{4}-[0-9]{2}-[0-9]{2}\.[1-9][0-9]*")
QUICK_LANES = [
    "backend-course-mode-focused",
    "admin-course-mode-logic",
    "esp-course-mode-focused",
    "firmware-course-mode-focused",
]
FULL_LANES = [
    "backend-lint",
    "backend-typecheck",
    "backend-tests",
    "backend-build",
    "backend-curriculum-verifier",
    "admin-logic",
    "admin-browser",
    "admin-build",
    "admin-course-mode-playwright-chromium-desktop",
    "admin-course-mode-playwright-webkit-desktop",
    "admin-course-mode-playwright-chromium-mobile",
    "admin-course-mode-playwright-webkit-mobile",
    "admin-course-mode-assignment-fixture",
    "admin-course-mode-assignment-new",
    "admin-course-mode-assignment-rollback",
    "esp-course-mode-full",
    "firmware-renderer",
    "firmware-handler",
    "firmware-backward-compatibility",
    "cross-contract-parity",
]
REQUIRED_EVIDENCE = (
    "00-candidate-validator.json",
    "00-operator-attestation.json",
    "02-runtime-assignment-new-rollback.json",
    "02-runtime-browser-after-assignment.json",
    "02-runtime-continuity-inspection.json",
    "03-quick-gate.json",
    "04-full-gate.json",
    "05-live-db-gate.json",
)
RAW_PLAYWRIGHT_DIRS = {"playwright-report", "test-results", "blob-report", "playwright-e2e-original"}
OUTPUT_NAME = "06-software-evidence-audit.json"
TRUSTED_SYSTEM_SYMLINKS = {Path("/var"): Path("/private/var"), Path("/tmp"): Path("/private/tmp")}


def _strict_json_loads(data: bytes) -> object:
    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def finite(value: str) -> float:
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError("non-finite JSON value")
        return parsed

    return json.loads(
        data,
        object_pairs_hook=pairs,
        parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("non-finite JSON value")),
        parse_float=finite,
    )


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _semantic_string_payloads(value: object) -> tuple[bytes, ...]:
    payloads: list[bytes] = []
    pending: list[tuple[object, str, bool]] = [(value, "value", True)]
    while pending:
        current, field, top_level = pending.pop()
        if isinstance(current, dict):
            for key, child in current.items():
                if top_level and key == "sessionId":
                    continue
                pending.append((child, key, False))
        elif isinstance(current, list):
            pending.extend((child, field, False) for child in current)
        elif isinstance(current, str):
            raw = current.encode("utf-8")
            payloads.append(raw)
            if re.sub(r"[^a-z0-9]", "", field.casefold()) in {"session", "sessionid"}:
                payloads.append(f"{field}: ".encode("utf-8") + raw)
    return tuple(payloads)


def _candidate_admission_paths(candidate: object, evidence_root: Path) -> dict[str, Path] | None:
    if not isinstance(candidate, dict):
        return None
    tools = candidate.get("tools")
    descriptor = tools.get("physicalAdmission") if isinstance(tools, dict) else None
    expected_keys = {"input", "output", "expectedIdentity", "expectedIdentitySignature"}
    if not isinstance(descriptor, dict) or set(descriptor) != expected_keys:
        return None
    try:
        canonical_root = evidence_root.resolve(strict=True)
        if canonical_root != evidence_root:
            return None
        paths = {
            key: Path(value) if isinstance(value, str) else Path()
            for key, value in descriptor.items()
        }
        if any(
            not isinstance(descriptor[key], str)
            or not path.is_absolute()
            or not _lexically_canonical(path)
            for key, path in paths.items()
        ):
            return None
        if len(set(paths.values())) != len(expected_keys):
            return None
        for key, path in paths.items():
            if key == "output":
                canonical_parent = path.parent.resolve(strict=True)
                if canonical_parent != path.parent or not path.is_relative_to(canonical_root):
                    return None
                try:
                    metadata = path.lstat()
                except FileNotFoundError:
                    continue
                if (
                    path.resolve(strict=True) != path
                    or not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_nlink != 1
                    or metadata.st_uid != os.geteuid()
                    or metadata.st_mode & 0o022
                ):
                    return None
                continue
            if path.resolve(strict=True) != path or not path.is_relative_to(canonical_root):
                return None
        return paths
    except (OSError, RuntimeError, ValueError):
        return None


@dataclass(frozen=True)
class PublicAdmissionScan:
    input_payloads: tuple[bytes, ...]
    identity_payloads: tuple[bytes, ...]
    binding: dict[str, str]


def _public_admission_scan_payloads(
    candidate: object,
    candidate_path: Path,
    candidate_raw: bytes,
    input_raw: bytes,
    identity_raw: bytes,
    signature: bytes,
) -> PublicAdmissionScan | None:
    try:
        import course_mode_physical_flash_admission as admission

        if len(signature) != 64:
            return None
        input_document = _strict_json_loads(input_raw)
        identity = _strict_json_loads(identity_raw)
        if not isinstance(input_document, dict) or not isinstance(identity, dict):
            return None
        candidate_binding = input_document.get("candidate")
        if (
            not isinstance(candidate_binding, dict)
            or candidate_binding.get("path") != str(candidate_path)
            or candidate_binding.get("sha256") != hashlib.sha256(candidate_raw).hexdigest()
        ):
            return None
        input_session = input_document.get("sessionId")
        if (
            not isinstance(input_session, str)
            or str(uuid.UUID(input_session)) != input_session
            or identity.get("sessionId") != input_session
        ):
            return None
        signature_valid, _fingerprint = admission._verify_signature(
            admission._canonical_bytes(identity), signature
        )
        if not signature_valid:
            return None
        checked_at = admission._parse_utc(input_document.get("checkedAt"))
        if checked_at is None or admission.validate_documents(
            input_document,
            identity,
            candidate,
            checked_at,
            [admission.SERIAL_PATH],
            [],
            None,
        ):
            return None
        return PublicAdmissionScan(
            input_payloads=(
                _canonical_json_bytes({**input_document, "sessionId": "redacted"}),
                *_semantic_string_payloads(input_document),
            ),
            identity_payloads=(
                _canonical_json_bytes({**identity, "sessionId": "redacted"}),
                *_semantic_string_payloads(identity),
            ),
            binding={
                "candidateSha256": hashlib.sha256(candidate_raw).hexdigest(),
                "inputSha256": hashlib.sha256(input_raw).hexdigest(),
                "expectedIdentitySha256": hashlib.sha256(identity_raw).hexdigest(),
                "signatureSha256": hashlib.sha256(signature).hexdigest(),
                "signedCanonicalIdentitySha256": hashlib.sha256(
                    _canonical_json_bytes(identity)
                ).hexdigest(),
                "sessionPolicy": "top-level-canonical-uuid.v1",
            },
        )
    except (
        AttributeError,
        ImportError,
        KeyError,
        OSError,
        RecursionError,
        TypeError,
        UnicodeError,
        ValueError,
        json.JSONDecodeError,
    ):
        return None


def _absolute(path: Path) -> Path:
    return path if path.is_absolute() else Path.cwd() / path


def _lexically_canonical(path: Path) -> bool:
    absolute = _absolute(path)
    return absolute.is_absolute() and not any(part in {".", ".."} for part in absolute.parts[1:])


def _secure_root(path: Path) -> bool:
    try:
        absolute = _absolute(path)
        if not _lexically_canonical(absolute):
            return False
        current = Path(absolute.anchor)
        for component in absolute.parts[1:]:
            current /= component
            if current.is_symlink():
                target = TRUSTED_SYSTEM_SYMLINKS.get(current)
                if target is None or current.resolve() != target:
                    return False
                current = target
            metadata = current.lstat()
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_uid not in {0, os.geteuid()}
                or metadata.st_mode & 0o022
            ):
                return False
        metadata = absolute.lstat()
    except OSError:
        return False
    return (
        stat.S_ISDIR(metadata.st_mode)
        and metadata.st_uid == os.geteuid()
        and not metadata.st_mode & 0o022
    )


def _same_file(left: Path, right: Path) -> bool:
    try:
        return left == right or os.path.samefile(left, right)
    except OSError:
        return left == right


def _output_conflicts(output: Path, protected: list[Path]) -> bool:
    return any(_same_file(output, path) for path in protected)


def _output_path_secure(output: Path) -> bool:
    if not _secure_root(output.parent):
        return False
    try:
        metadata = output.lstat()
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return (
        stat.S_ISREG(metadata.st_mode)
        and metadata.st_nlink == 1
        and metadata.st_uid == os.geteuid()
        and not metadata.st_mode & 0o022
    )


def _lane_report(document: object, candidate_id: object, lanes: list[str], attestation_sha: str | None) -> bool:
    if not isinstance(document, dict):
        return False
    expected = {"candidateId", "failedLane", "lanes", "verdict"}
    if attestation_sha is not None:
        expected.add("operatorAttestationSha256")
    if set(document) != expected:
        return False
    rows = document.get("lanes")
    return (
        document.get("candidateId") == candidate_id
        and document.get("failedLane") is None
        and document.get("verdict") == "PASS"
        and isinstance(rows, list)
        and [row.get("name") for row in rows if isinstance(row, dict)] == lanes
        and len(rows) == len(lanes)
        and all(
            isinstance(row, dict)
            and type(row.get("exitCode")) is int
            and row.get("exitCode") == 0
            and type(row.get("durationMs")) is int
            and row["durationMs"] >= 0
            for row in rows
        )
        and (attestation_sha is None or document.get("operatorAttestationSha256") == attestation_sha)
    )


def _repository_identity(candidate: object) -> bool:
    if not isinstance(candidate, dict):
        return False
    repositories = candidate.get("repositories")
    if not isinstance(repositories, dict) or set(repositories) != {"backend", "adminEsp", "firmware"}:
        return False
    repository_shas = {
        name: value.get("sha") if isinstance(value, dict) else None
        for name, value in repositories.items()
    }
    return not any(
        not isinstance(value, str) or SHA40.fullmatch(value) is None for value in repository_shas.values()
    )


def _image_identity(candidate: object) -> bool:
    if not _repository_identity(candidate) or not isinstance(candidate, dict):
        return False
    repositories = candidate["repositories"]
    shas = {name: descriptor["sha"] for name, descriptor in repositories.items()}
    images = candidate.get("images")
    if not isinstance(images, dict) or set(images) != {"lessonStudioBackend", "lessonStudioWeb"}:
        return False
    expected = {
        "lessonStudioBackend": f"local/tbot-backend:course-mode-physical-tft-{shas['backend']}",
        "lessonStudioWeb": f"local/tbot-server-web:course-mode-physical-tft-{shas['adminEsp']}",
    }
    for image_name in expected:
        descriptor = images.get(image_name)
        if (
            not isinstance(descriptor, dict)
            or descriptor.get("reference") != expected[image_name]
            or not isinstance(descriptor.get("id"), str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", descriptor["id"]) is None
        ):
            return False
    return True


def _firmware_identity(candidate: object) -> bool:
    if not isinstance(candidate, dict):
        return False
    firmware = candidate.get("firmware")
    return (
        isinstance(firmware, dict)
        and firmware.get("appOffset") == "0x20000"
        and isinstance(firmware.get("appBytes"), int)
        and firmware["appBytes"] > 0
        and isinstance(firmware.get("appSha256"), str)
        and SHA256.fullmatch(firmware["appSha256"]) is not None
        and isinstance(firmware.get("evidenceManifestSha256"), str)
        and SHA256.fullmatch(firmware["evidenceManifestSha256"]) is not None
    )


def _curriculum_identity(candidate: object) -> bool:
    if not isinstance(candidate, dict):
        return False
    curriculum = candidate.get("curriculum")
    return (
        candidate.get("course") == {
            "courseId": "a17792f6-8d86-4ad1-a6f3-77663b4d4674",
            "courseKey": "english-6month-4-6",
        }
        and isinstance(curriculum, dict)
        and curriculum.get("lessonCount") == 26
        and curriculum.get("activityCount") == 256
    )


def _candidate_identity(candidate: object, evidence_root: Path) -> bool:
    return (
        isinstance(candidate, dict)
        and isinstance(candidate.get("candidateId"), str)
        and CANDIDATE_ID.fullmatch(candidate["candidateId"]) is not None
        and candidate.get("evidenceRoot") == str(evidence_root)
        and isinstance(candidate.get("tools"), dict)
        and bool(candidate["tools"])
        and _repository_identity(candidate)
        and _image_identity(candidate)
        and _firmware_identity(candidate)
        and _curriculum_identity(candidate)
    )


def audit(candidate_path: Path, evidence_root: Path, preserved_roots: list[Path], output: Path) -> dict[str, object]:
    candidate_path = _absolute(candidate_path)
    evidence_root = _absolute(evidence_root)
    preserved_roots = [_absolute(root) for root in preserved_roots]
    output = _absolute(output)
    findings: set[str] = set()
    candidate_subject, candidate_capture_findings = snapshot.capture_candidate(candidate_path)
    findings.update(
        "candidate.metadata_or_json" if code == "candidate.metadata" else code
        for code in candidate_capture_findings
    )
    retained_candidate = candidate_subject or snapshot.CapturedSubject(
        "candidate", candidate_path.name, b"", "candidate-json.v1"
    )
    candidate_bytes = retained_candidate.data
    try:
        candidate = _strict_json_loads(candidate_bytes)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, RecursionError):
        candidate = None
        findings.add("candidate.metadata_or_json")
    raw_candidate_id = candidate.get("candidateId") if isinstance(candidate, dict) else None
    candidate_id = None
    documents: dict[str, object] = {}
    document_bytes: dict[str, bytes] = {}
    checked_archive_members = 0
    raw_playwright_absent = True
    archive_budget = {"members": 0, "expanded": 0}
    base64_state = {"blocks": 0, "decoded": 0, "limited": False}
    protected_inputs = [candidate_path] + [evidence_root / name for name in REQUIRED_EVIDENCE]
    if output != evidence_root / OUTPUT_NAME or not _lexically_canonical(output):
        findings.add("output.unsafe")
    if not _output_path_secure(output):
        findings.add("output.unsafe")
    if _output_conflicts(output, protected_inputs):
        findings.add("output.collision")

    admission_paths = _candidate_admission_paths(candidate, evidence_root)
    tools = candidate.get("tools") if isinstance(candidate, dict) else None
    public_admission_declared = isinstance(tools, dict) and "physicalAdmission" in tools
    excluded_paths: tuple[Path, ...] = ()
    if admission_paths is not None:
        excluded_paths = (admission_paths["output"],)
    capture = snapshot.capture_snapshot(
        retained_candidate,
        evidence_root,
        preserved_roots=preserved_roots,
        excluded_evidence_paths=excluded_paths,
    )
    for code in capture.findings:
        findings.add(
            {
                "candidate.metadata": "candidate.metadata_or_json",
                "evidence.metadata": "evidence.metadata_or_json",
            }.get(code, code)
        )
    captured = {(subject.scope, subject.path): subject for subject in capture.subjects}
    if any(
        subject.scope == "evidence"
        and _same_file(output, evidence_root / subject.path)
        for subject in capture.subjects
    ):
        findings.add("output.collision")

    candidate_findings, candidate_members = scan_evidence_payload(
        candidate_bytes, candidate_path.name, _budget=archive_budget, _base64_state=base64_state
    )
    findings.update(candidate_findings)
    checked_archive_members += candidate_members
    if not _candidate_identity(candidate, evidence_root):
        findings.add("candidate.identity")
    elif isinstance(raw_candidate_id, str) and CANDIDATE_ID.fullmatch(raw_candidate_id):
        candidate_id = raw_candidate_id
    for name in REQUIRED_EVIDENCE:
        subject = captured.get(("evidence", name))
        data = subject.data if subject is not None else None
        try:
            document = _strict_json_loads(data) if data is not None else None
        except (UnicodeError, json.JSONDecodeError, ValueError, RecursionError):
            document = None
        if document is None or data is None:
            findings.add("evidence.metadata_or_json")
        else:
            document_bytes[name] = data
        documents[name] = document
    validator = documents["00-candidate-validator.json"]
    validator_ok = validator == {
        "reasons": [],
        "schemaVersion": 1,
        "status": "pass",
        "validator": "course-mode-candidate.v1",
    }
    if not validator_ok:
        findings.add("evidence.validator")
    attestation = documents["00-operator-attestation.json"]
    attestation_bytes = document_bytes.get("00-operator-attestation.json", b"")
    attestation_sha = hashlib.sha256(attestation_bytes).hexdigest() if attestation_bytes else None
    admin_sha = None
    if isinstance(candidate, dict) and isinstance(candidate.get("repositories"), dict):
        admin = candidate["repositories"].get("adminEsp")
        admin_sha = admin.get("sha") if isinstance(admin, dict) else None
    attestation_ok = (
        isinstance(attestation, dict)
        and attestation.get("candidateId") == candidate_id
        and attestation.get("gateSha") == admin_sha
        and attestation.get("sameUidThreatModel") == "malicious-process-excluded"
        and attestation.get("trustedOperatorAccountConfirmed") is True
        and attestation.get("untrustedAutomationStoppedConfirmed") is True
    )
    if not attestation_ok:
        findings.add("evidence.attestation")
    runtime_ok = (
        _lane_report(
            documents["02-runtime-assignment-new-rollback.json"],
            candidate_id,
            ["admin-course-mode-assignment-new", "admin-course-mode-assignment-rollback"],
            None,
        )
        and _lane_report(
            documents["02-runtime-browser-after-assignment.json"],
            candidate_id,
            [
                "admin-course-mode-playwright-chromium-desktop",
                "admin-course-mode-playwright-webkit-desktop",
            ],
            None,
        )
    )
    continuity = documents["02-runtime-continuity-inspection.json"]
    continuity_ok = (
        isinstance(continuity, dict)
        and continuity.get("status") == "pass"
        and continuity.get("assignmentFlags") == {"new": False, "rollback": False}
        and continuity.get("mountCount") == 4
        and continuity.get("allMountsCanonical") is True
        and continuity.get("allMountsExist") is True
        and continuity.get("allMountsReadOnly") is True
        and continuity.get("imagesMatchCandidate") is True
        and continuity.get("manualRecreateAfterRollback") is False
    )
    quick_ok = _lane_report(documents["03-quick-gate.json"], candidate_id, QUICK_LANES, attestation_sha)
    full_ok = _lane_report(documents["04-full-gate.json"], candidate_id, FULL_LANES, attestation_sha)
    live_ok = _lane_report(
        documents["05-live-db-gate.json"], candidate_id, FULL_LANES + ["live-postgres"], attestation_sha
    )
    for ok, code in (
        (runtime_ok, "evidence.runtime"),
        (continuity_ok, "evidence.continuity"),
        (quick_ok, "evidence.quick"),
        (full_ok, "evidence.full"),
        (live_ok, "evidence.live_db"),
    ):
        if not ok:
            findings.add(code)
    admission_scan = None
    report_subjects = capture.subjects
    special_payloads: dict[tuple[str, str], tuple[bytes, ...]] = {}
    if admission_paths is not None:
        relative_paths = {
            key: path.relative_to(evidence_root).as_posix()
            for key, path in admission_paths.items()
            if key != "output"
        }
        input_subject = captured.get(("evidence", relative_paths["input"]))
        identity_subject = captured.get(("evidence", relative_paths["expectedIdentity"]))
        signature_subject = captured.get(("evidence", relative_paths["expectedIdentitySignature"]))
        if input_subject is not None and identity_subject is not None and signature_subject is not None:
            admission_scan = _public_admission_scan_payloads(
                candidate,
                candidate_path,
                candidate_bytes,
                input_subject.data,
                identity_subject.data,
                signature_subject.data,
            )
            if admission_scan is not None:
                special_payloads[("evidence", relative_paths["input"])] = admission_scan.input_payloads
                special_payloads[("evidence", relative_paths["expectedIdentity"])] = (
                    admission_scan.identity_payloads
                )
                exempt_keys = {
                    ("evidence", relative_paths["input"]),
                    ("evidence", relative_paths["expectedIdentity"]),
                }
                report_subjects = tuple(
                    snapshot.CapturedSubject(
                        subject.scope,
                        subject.path,
                        subject.data,
                        "physical-admission-top-level-session-id.v1",
                    )
                    if (subject.scope, subject.path) in exempt_keys
                    else subject
                    for subject in capture.subjects
                )
    if public_admission_declared and admission_scan is None:
        findings.add("content.secret")

    for subject in capture.subjects:
        if subject.scope == "candidate":
            continue
        parts = Path(subject.path).parts
        if any(part.lower() in RAW_PLAYWRIGHT_DIRS for part in parts):
            raw_playwright_absent = False
            findings.add(f"{subject.scope}.raw_playwright")
        if subject.scope == "preserved":
            name = Path(subject.path).name
            if ("tombstone" in name.lower() or "summary" in name.lower()) and not is_sanitized_manifest(
                subject.data
            ):
                findings.add("preserved.manifest")
            elif has_sanitization_failure(subject.data):
                findings.add("preserved.sanitization_failed")
        payloads = special_payloads.get((subject.scope, subject.path), (subject.data,))
        for payload in payloads:
            content_findings, member_count = scan_evidence_payload(
                payload,
                Path(subject.path).name,
                _budget=archive_budget,
                _base64_state=base64_state,
            )
            findings.update(content_findings)
            checked_archive_members += member_count
            if content_findings & {"content.embedded_playwright", "content.raw_playwright"}:
                raw_playwright_absent = False
    checks = {
        "candidateIdentity": _candidate_identity(candidate, evidence_root),
        "repositoryIdentity": _repository_identity(candidate),
        "imageIdentityAndProvenance": _image_identity(candidate),
        "firmwareIdentity": _firmware_identity(candidate),
        "curriculumIdentity": _curriculum_identity(candidate),
        "secureFileMetadata": not findings.intersection(
            {
                "candidate.metadata_or_json",
                "evidence.metadata_or_json",
                "evidence.root",
                "preserved.metadata",
                "preserved.root",
            }
        ),
        "validator": validator_ok,
        "attestationBinding": attestation_ok,
        "runtimeContinuity": runtime_ok and continuity_ok,
        "quickGate4of4": quick_ok,
        "fullGate20of20": full_ok,
        "liveDbGate21of21": live_ok,
        "terminalLivePostgres": live_ok,
        "secretScan": "content.secret" not in findings,
        "archiveSafety": not any(code.startswith("archive.") for code in findings),
        "binaryMediaAbsent": "content.binary_media" not in findings,
        "privateContentAbsent": not any(
            code in findings
            for code in ("content.audio", "content.binary_media", "content.transcript", "content.private_key")
        ),
        "rawPlaywrightAbsent": raw_playwright_absent,
        "physicalActionsPerformed": False,
        "productionDatabaseUsed": False,
    }
    subjects = snapshot.subject_manifest(report_subjects)
    report = {
        "schemaVersion": snapshot.SCHEMA_VERSION,
        "validator": snapshot.VALIDATOR,
        "candidateId": candidate_id,
        "snapshot": {
            "algorithm": "sha256",
            "id": snapshot.snapshot_id(subjects),
            "subjects": subjects,
        },
        "checkedArchiveMemberCount": checked_archive_members,
        "checkedFileCount": len(subjects),
        "checks": checks,
        "findings": sorted(findings),
        "status": "pass" if not findings else "fail",
    }
    if admission_scan is not None:
        report["admissionBinding"] = admission_scan.binding
    return report


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        try:
            written = os.write(descriptor, payload[offset:])
        except InterruptedError:
            continue
        if written <= 0:
            raise OSError("short write")
        offset += written


def _write_output(path: Path, report: dict[str, object]) -> bool:
    payload = json.dumps(
        report,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8") + b"\n"
    parent_fd = descriptor = None
    temporary_name = None
    try:
        parent_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary_name = Path(temporary)
        _write_all(descriptor, payload)
        os.fchmod(descriptor, 0o444)
        os.fsync(descriptor)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_uid != os.geteuid()
            or metadata.st_size != len(payload)
        ):
            return False
        os.replace(temporary_name, path)
        temporary_name = None
        os.fsync(parent_fd)
        return True
    except (OSError, TypeError, ValueError):
        return False
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary_name is not None:
            with contextlib.suppress(FileNotFoundError):
                temporary_name.unlink()
        if parent_fd is not None:
            os.close(parent_fd)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--evidence-root", required=True, type=Path)
    parser.add_argument("--preserved-root", action="append", default=[], type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    evidence_root = _absolute(args.evidence_root)
    output = _absolute(args.output)
    candidate_path = _absolute(args.candidate)
    preserved_roots = [_absolute(path) for path in args.preserved_root]
    report = audit(candidate_path, evidence_root, preserved_roots, output)
    if output != evidence_root / OUTPUT_NAME:
        report["findings"] = sorted(set(report["findings"]) | {"output.unsafe"})
        report["status"] = "fail"
    if (
        output == evidence_root / OUTPUT_NAME
        and "output.unsafe" not in report["findings"]
        and "output.collision" not in report["findings"]
        and not _write_output(output, report)
    ):
        report["findings"] = sorted(set(report["findings"]) | {"output.write"})
        report["status"] = "fail"
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
