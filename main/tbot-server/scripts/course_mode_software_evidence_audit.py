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
import secrets
import stat
import uuid
from pathlib import Path

from course_mode_evidence_privacy import has_sanitization_failure, is_sanitized_manifest, scan_evidence_payload

MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_AUDIT_FILES = 4096
MAX_AUDIT_TOTAL_BYTES = 64 * 1024 * 1024
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


class _AuditReport(dict[str, object]):
    def __init__(
        self,
        value: dict[str, object],
        candidate_path: Path,
        candidate_snapshot: tuple[bytes, tuple[int, ...], tuple[tuple[int, ...], ...]] | None,
        public_admission_declared: bool,
        output_parent_snapshot: tuple[tuple[int, ...], ...] | None,
    ) -> None:
        super().__init__(value)
        self.candidate_path = candidate_path
        self.candidate_snapshot = candidate_snapshot
        self.public_admission_declared = public_admission_declared
        self.output_parent_snapshot = output_parent_snapshot

    def candidate_still_bound(self) -> bool:
        return self.candidate_snapshot is not None and _secure_file_snapshot_matches(
            self.candidate_path, self.candidate_snapshot
        )


def _directory_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_gid,
    )


def _file_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _open_bound_directory(path: Path) -> tuple[int, tuple[tuple[int, ...], ...]]:
    if not path.is_absolute() or any(part in {".", ".."} for part in path.parts[1:]):
        raise OSError("non-canonical path")
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    current = os.open(path.anchor, flags)
    ancestry = [_directory_identity(os.fstat(current))]
    try:
        for component in path.parts[1:]:
            named = os.stat(component, dir_fd=current, follow_symlinks=False)
            if not stat.S_ISDIR(named.st_mode):
                raise OSError("invalid path ancestry")
            next_descriptor = os.open(component, flags, dir_fd=current)
            try:
                opened = os.fstat(next_descriptor)
                current_named = os.stat(component, dir_fd=current, follow_symlinks=False)
                if _directory_identity(named) != _directory_identity(opened) or _directory_identity(
                    current_named
                ) != _directory_identity(opened):
                    raise OSError("path ancestry changed")
            except Exception:
                os.close(next_descriptor)
                raise
            os.close(current)
            current = next_descriptor
            ancestry.append(_directory_identity(opened))
        return current, tuple(ancestry)
    except Exception:
        os.close(current)
        raise


def _directory_snapshot(path: Path) -> tuple[tuple[int, ...], ...]:
    descriptor, ancestry = _open_bound_directory(path)
    os.close(descriptor)
    return ancestry


def _directory_still_bound(
    path: Path, descriptor: int, ancestry: tuple[tuple[int, ...], ...]
) -> bool:
    verification: int | None = None
    try:
        verification, observed = _open_bound_directory(path)
        return (
            _directory_identity(os.fstat(descriptor)) == ancestry[-1]
            and _directory_identity(os.fstat(verification)) == ancestry[-1]
            and observed == ancestry
        )
    except OSError:
        return False
    finally:
        if verification is not None:
            os.close(verification)


def _read_secure_file_snapshot(
    path: Path,
) -> tuple[bytes, tuple[int, ...], tuple[tuple[int, ...], ...]]:
    parent_descriptor, ancestry = _open_bound_directory(path.parent)
    descriptor: int | None = None
    try:
        descriptor = os.open(
            path.name,
            os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_descriptor,
        )
        before = os.fstat(descriptor)
        named_before = os.stat(path.name, dir_fd=parent_descriptor, follow_symlinks=False)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_uid != os.geteuid()
            or before.st_mode & 0o022
            or before.st_size > MAX_FILE_BYTES
        ):
            raise OSError("insecure evidence metadata")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, MAX_FILE_BYTES + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_FILE_BYTES:
                raise OSError("evidence exceeds bound")
            chunks.append(chunk)
        after = os.fstat(descriptor)
        current = os.stat(path.name, dir_fd=parent_descriptor, follow_symlinks=False)
        identity = (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_nlink,
            before.st_uid,
            before.st_gid,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        observed_after = (
            after.st_dev,
            after.st_ino,
            after.st_mode,
            after.st_nlink,
            after.st_uid,
            after.st_gid,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        observed_current = (
            current.st_dev,
            current.st_ino,
            current.st_mode,
            current.st_nlink,
            current.st_uid,
            current.st_gid,
            current.st_size,
            current.st_mtime_ns,
            current.st_ctime_ns,
        )
        named_identity = (
            named_before.st_dev,
            named_before.st_ino,
            named_before.st_mode,
            named_before.st_nlink,
            named_before.st_uid,
            named_before.st_gid,
            named_before.st_size,
            named_before.st_mtime_ns,
            named_before.st_ctime_ns,
        )
        if identity != named_identity or identity != observed_after:
            raise OSError("evidence changed while reading")
        if identity != observed_current:
            raise OSError("evidence path changed while reading")
        if total != before.st_size:
            raise OSError("evidence size changed while reading")
        if not _directory_still_bound(path.parent, parent_descriptor, ancestry):
            raise OSError("evidence ancestry changed while reading")
        return b"".join(chunks), identity, ancestry
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent_descriptor)


def _read_secure_file(path: Path) -> bytes:
    return _read_secure_file_snapshot(path)[0]


def _secure_file_snapshot_matches(
    path: Path,
    snapshot: tuple[bytes, tuple[int, ...], tuple[tuple[int, ...], ...]],
) -> bool:
    try:
        return _read_secure_file_snapshot(path) == snapshot
    except OSError:
        return False


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


def _public_admission_scan_payloads(
    candidate: object, candidate_path: Path, candidate_raw: bytes, evidence_root: Path
) -> dict[Path, tuple[bytes, bytes, tuple[bytes, ...]]]:
    paths = _candidate_admission_paths(candidate, evidence_root)
    if paths is None:
        return {}
    try:
        import course_mode_physical_flash_admission as admission

        input_raw = _read_secure_file(paths["input"])
        identity_raw = _read_secure_file(paths["expectedIdentity"])
        signature = _read_secure_file(paths["expectedIdentitySignature"])
        if len(signature) != 64:
            return {}
        input_document = _strict_json_loads(input_raw)
        identity = _strict_json_loads(identity_raw)
        if not isinstance(input_document, dict) or not isinstance(identity, dict):
            return {}
        candidate_binding = input_document.get("candidate")
        if (
            not isinstance(candidate_binding, dict)
            or candidate_path.resolve(strict=True) != candidate_path
            or candidate_binding.get("path") != str(candidate_path)
            or candidate_binding.get("sha256") != hashlib.sha256(candidate_raw).hexdigest()
        ):
            return {}
        input_session = input_document.get("sessionId")
        if (
            not isinstance(input_session, str)
            or str(uuid.UUID(input_session)) != input_session
            or identity.get("sessionId") != input_session
        ):
            return {}
        signature_valid, _fingerprint = admission._verify_signature(
            admission._canonical_bytes(identity), signature
        )
        if not signature_valid:
            return {}
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
            return {}
        return {
            paths["input"]: (
                input_raw,
                _canonical_json_bytes({**input_document, "sessionId": "redacted"}),
                _semantic_string_payloads(input_document),
            ),
            paths["expectedIdentity"]: (
                identity_raw,
                _canonical_json_bytes({**identity, "sessionId": "redacted"}),
                _semantic_string_payloads(identity),
            ),
            paths["expectedIdentitySignature"]: (signature, signature, ()),
        }
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
        return {}


def _load_json(path: Path) -> tuple[object | None, bytes | None]:
    try:
        data = _read_secure_file(path)
        return _strict_json_loads(data), data
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, RecursionError):
        return None, None


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


def _iter_root_entries(root: Path):
    """Walk one root without following links or materializing the whole tree."""
    iterators: list[os.ScandirIterator[str]] = [os.scandir(root)]
    try:
        while iterators:
            iterator = iterators[-1]
            try:
                entry = next(iterator)
            except StopIteration:
                iterator.close()
                iterators.pop()
                continue
            path = Path(entry.path)
            try:
                metadata = entry.stat(follow_symlinks=False)
            except OSError:
                yield path, None
                continue
            yield path, metadata
            if stat.S_ISDIR(metadata.st_mode):
                iterators.append(os.scandir(path))
    finally:
        for iterator in iterators:
            iterator.close()


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
    try:
        output_parent_snapshot = _directory_snapshot(output.parent)
    except OSError:
        output_parent_snapshot = None
    findings: set[str] = set()
    if output_parent_snapshot is None:
        findings.add("output.write")
    candidate_snapshot: tuple[bytes, tuple[int, ...], tuple[tuple[int, ...], ...]] | None = None
    try:
        candidate_snapshot = _read_secure_file_snapshot(candidate_path)
        candidate_bytes = candidate_snapshot[0]
        candidate = _strict_json_loads(candidate_bytes)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, RecursionError):
        candidate = None
        candidate_bytes = b""
    raw_candidate_id = candidate.get("candidateId") if isinstance(candidate, dict) else None
    candidate_id = None
    documents: dict[str, object] = {}
    document_bytes: dict[str, bytes] = {}
    checked_files = 1
    checked_archive_members = 0
    raw_playwright_absent = True
    audit_budget = {"entries": 1, "bytes": 0}
    archive_budget = {"members": 0, "expanded": 0}
    base64_state = {"blocks": 0, "decoded": 0, "limited": False}
    protected_inputs = [candidate_path] + [evidence_root / name for name in REQUIRED_EVIDENCE]
    if output != evidence_root / OUTPUT_NAME or not _lexically_canonical(output):
        findings.add("output.unsafe")
    if not _output_path_secure(output):
        findings.add("output.unsafe")
    if _output_conflicts(output, protected_inputs):
        findings.add("output.collision")
    if not _secure_root(evidence_root):
        findings.add("evidence.root")
    if candidate is None:
        findings.add("candidate.metadata_or_json")
    audit_budget["bytes"] = len(candidate_bytes)
    if audit_budget["bytes"] > MAX_AUDIT_TOTAL_BYTES:
        findings.add("evidence.budget")
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
        document, data = _load_json(evidence_root / name)
        if document is None:
            findings.add("evidence.metadata_or_json")
        elif data is not None:
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
    tools = candidate.get("tools") if isinstance(candidate, dict) else None
    public_admission_declared = isinstance(tools, dict) and "physicalAdmission" in tools
    public_admission_payloads = _public_admission_scan_payloads(
        candidate, candidate_path, candidate_bytes, evidence_root
    )
    if public_admission_declared and not public_admission_payloads:
        findings.add("content.secret")
    observed_public_admission_payloads: dict[Path, bytes] = {}
    try:
        with contextlib.closing(_iter_root_entries(evidence_root)) as evidence_paths:
            for path, metadata in evidence_paths:
                if audit_budget["entries"] >= MAX_AUDIT_FILES:
                    findings.add("evidence.budget")
                    break
                audit_budget["entries"] += 1
                if path == output:
                    if path.name != OUTPUT_NAME:
                        findings.add("output.collision")
                    continue
                try:
                    relative_parts = path.relative_to(evidence_root).parts
                except ValueError:
                    findings.add("evidence.metadata_or_json")
                    continue
                if metadata is None:
                    findings.add("evidence.metadata_or_json")
                    continue
                if stat.S_ISDIR(metadata.st_mode):
                    if metadata.st_uid != os.geteuid() or metadata.st_mode & 0o022:
                        findings.add("evidence.metadata_or_json")
                    if any(part.lower() in RAW_PLAYWRIGHT_DIRS for part in relative_parts):
                        raw_playwright_absent = False
                        findings.add("evidence.raw_playwright")
                    continue
                if _same_file(output, path):
                    findings.add("output.collision")
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_nlink != 1
                    or metadata.st_mode & 0o022
                    or metadata.st_size > MAX_FILE_BYTES
                ):
                    findings.add("evidence.metadata_or_json")
                    continue
                if audit_budget["bytes"] + metadata.st_size > MAX_AUDIT_TOTAL_BYTES:
                    findings.add("evidence.budget")
                    break
                audit_budget["bytes"] += metadata.st_size
                checked_files += 1
                try:
                    data = _read_secure_file(path)
                except OSError:
                    findings.add("evidence.metadata_or_json")
                    continue
                public_payload = public_admission_payloads.get(path)
                if public_payload is not None:
                    observed_public_admission_payloads[path] = data
                    continue
                content_findings, member_count = scan_evidence_payload(
                    data, path.name, _budget=archive_budget, _base64_state=base64_state
                )
                findings.update(content_findings)
                checked_archive_members += member_count
                if content_findings & {"content.embedded_playwright", "content.raw_playwright"}:
                    raw_playwright_absent = False
    except OSError:
        findings.add("evidence.root")
    candidate_snapshot_matches = (
        candidate_snapshot is not None
        and _secure_file_snapshot_matches(candidate_path, candidate_snapshot)
    )
    public_snapshot_matches = (
        candidate_snapshot_matches
        and set(observed_public_admission_payloads) == set(public_admission_payloads)
        and all(
            observed_public_admission_payloads[path] == expected[0]
            for path, expected in public_admission_payloads.items()
        )
    )
    if public_admission_payloads and not public_snapshot_matches:
        findings.add("content.secret")
    for path, data in observed_public_admission_payloads.items():
        expected = public_admission_payloads[path]
        scan_payloads = (expected[1], *expected[2]) if public_snapshot_matches else (data,)
        for scan_data in scan_payloads:
            content_findings, member_count = scan_evidence_payload(
                scan_data, path.name, _budget=archive_budget, _base64_state=base64_state
            )
            findings.update(content_findings)
            checked_archive_members += member_count
            if content_findings & {"content.embedded_playwright", "content.raw_playwright"}:
                raw_playwright_absent = False
    for root in preserved_roots:
        if not _secure_root(root):
            findings.add("preserved.root")
            continue
        try:
            with contextlib.closing(_iter_root_entries(root)) as paths:
                for path, metadata in paths:
                    if audit_budget["entries"] >= MAX_AUDIT_FILES:
                        findings.add("evidence.budget")
                        break
                    audit_budget["entries"] += 1
                    try:
                        relative_parts = path.relative_to(root).parts
                    except ValueError:
                        findings.add("preserved.metadata")
                        continue
                    if metadata is None:
                        findings.add("preserved.metadata")
                        continue
                    if stat.S_ISDIR(metadata.st_mode):
                        if metadata.st_uid != os.geteuid() or metadata.st_mode & 0o022:
                            findings.add("preserved.metadata")
                        if any(part.lower() in RAW_PLAYWRIGHT_DIRS for part in relative_parts):
                            raw_playwright_absent = False
                            findings.add("preserved.raw_playwright")
                        continue
                    if _same_file(output, path):
                        findings.add("output.collision")
                    if (
                        not stat.S_ISREG(metadata.st_mode)
                        or metadata.st_nlink != 1
                        or metadata.st_mode & 0o022
                        or metadata.st_size > MAX_FILE_BYTES
                    ):
                        findings.add("preserved.metadata")
                        continue
                    if audit_budget["bytes"] + metadata.st_size > MAX_AUDIT_TOTAL_BYTES:
                        findings.add("evidence.budget")
                        break
                    audit_budget["bytes"] += metadata.st_size
                    checked_files += 1
                    try:
                        data = _read_secure_file(path)
                    except OSError:
                        findings.add("preserved.metadata")
                        continue
                    if ("tombstone" in path.name.lower() or "summary" in path.name.lower()) and not is_sanitized_manifest(data):
                        findings.add("preserved.manifest")
                    elif has_sanitization_failure(data):
                        findings.add("preserved.sanitization_failed")
                    if any(part.lower() in RAW_PLAYWRIGHT_DIRS for part in relative_parts):
                        raw_playwright_absent = False
                        findings.add("preserved.raw_playwright")
                    content_findings, member_count = scan_evidence_payload(
                        data, path.name, _budget=archive_budget, _base64_state=base64_state
                    )
                    findings.update(content_findings)
                    checked_archive_members += member_count
                    if content_findings & {"content.embedded_playwright", "content.raw_playwright"}:
                        raw_playwright_absent = False
        except OSError:
            findings.add("preserved.root")
    if candidate_snapshot is not None and not _secure_file_snapshot_matches(candidate_path, candidate_snapshot):
        findings.add("candidate.metadata_or_json")
        if public_admission_declared:
            findings.add("content.secret")
    checks = {
        "candidateIdentity": _candidate_identity(candidate, evidence_root),
        "repositoryIdentity": _repository_identity(candidate),
        "imageIdentityAndProvenance": _image_identity(candidate),
        "firmwareIdentity": _firmware_identity(candidate),
        "curriculumIdentity": _curriculum_identity(candidate),
        "secureFileMetadata": not findings.intersection(
            {"candidate.metadata_or_json", "evidence.metadata_or_json", "evidence.root", "preserved.metadata", "preserved.root"}
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
    return _AuditReport(
        {
            "candidateId": candidate_id,
            "checkedArchiveMemberCount": checked_archive_members,
            "checkedFileCount": checked_files,
            "checks": checks,
            "findings": sorted(findings),
            "schemaVersion": 1,
            "status": "pass" if not findings else "fail",
        },
        candidate_path,
        candidate_snapshot,
        public_admission_declared,
        output_parent_snapshot,
    )


def _mark_candidate_changed(report: dict[str, object]) -> None:
    findings = set(report.get("findings", []))
    findings.add("candidate.metadata_or_json")
    if isinstance(report, _AuditReport) and report.public_admission_declared:
        findings.add("content.secret")
    report["findings"] = sorted(findings)
    checks = report.get("checks")
    if isinstance(checks, dict):
        checks["secureFileMetadata"] = False
        checks["secretScan"] = "content.secret" not in findings
    report["status"] = "fail"


def _write_all(descriptor: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("short output write")
        view = view[written:]


def _invalidate_published_output(descriptor: int) -> bool:
    try:
        os.ftruncate(descriptor, 0)
        os.fsync(descriptor)
        return True
    except OSError:
        try:
            os.lseek(descriptor, 0, os.SEEK_SET)
            _write_all(descriptor, b"\x00")
            os.fsync(descriptor)
            return True
        except OSError:
            return False


class _OutputBinding:
    def __init__(
        self,
        path: Path,
        parent_descriptor: int,
        parent_snapshot: tuple[tuple[int, ...], ...],
        output_descriptor: int | None,
        output_identity: tuple[int, ...] | None,
    ) -> None:
        self.path = path
        self.parent_descriptor = parent_descriptor
        self.parent_snapshot = parent_snapshot
        self.output_descriptor = output_descriptor
        self.output_identity = output_identity

    def close(self) -> None:
        if self.output_descriptor is not None:
            os.close(self.output_descriptor)
            self.output_descriptor = None
        os.close(self.parent_descriptor)

    def publish_failure(self, report: dict[str, object]) -> bool:
        if self.output_descriptor is None or self.output_identity is None:
            return False
        writable: int | None = None
        original_mode = self.output_identity[2]
        try:
            if _directory_identity(os.fstat(self.parent_descriptor)) != self.parent_snapshot[-1]:
                return False
            bound = os.fstat(self.output_descriptor)
            if (
                _file_identity(bound)[:2] != self.output_identity[:2]
                or not stat.S_ISREG(bound.st_mode)
                or bound.st_nlink != 1
                or bound.st_uid != os.geteuid()
                or bound.st_mode & 0o022
            ):
                return False
            os.fchmod(self.output_descriptor, 0o600)
            writable = os.open(
                self.path.name,
                os.O_WRONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=self.parent_descriptor,
            )
            if _file_identity(os.fstat(writable))[:2] != self.output_identity[:2]:
                return False
            os.lseek(writable, 0, os.SEEK_SET)
            _write_all(writable, b"\x00")
            os.fsync(writable)
            payload = (json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
            os.ftruncate(writable, 0)
            os.lseek(writable, 0, os.SEEK_SET)
            _write_all(writable, payload)
            os.ftruncate(writable, len(payload))
            os.fchmod(writable, 0o444)
            os.fsync(writable)
            os.fsync(self.parent_descriptor)
            current = os.stat(self.path.name, dir_fd=self.parent_descriptor, follow_symlinks=False)
            return _file_identity(current)[:2] == self.output_identity[:2]
        except OSError:
            if writable is not None:
                _invalidate_published_output(writable)
            return False
        finally:
            with contextlib.suppress(OSError):
                os.fchmod(self.output_descriptor, original_mode & 0o7777)
            if writable is not None:
                os.close(writable)


def _capture_output_binding(path: Path) -> _OutputBinding | None:
    parent_descriptor: int | None = None
    output_descriptor: int | None = None
    try:
        parent_descriptor, parent_snapshot = _open_bound_directory(path.parent)
        try:
            named = os.stat(path.name, dir_fd=parent_descriptor, follow_symlinks=False)
        except FileNotFoundError:
            return _OutputBinding(path, parent_descriptor, parent_snapshot, None, None)
        identity = _file_identity(named)
        if (
            not stat.S_ISREG(named.st_mode)
            or named.st_nlink != 1
            or named.st_uid != os.geteuid()
            or named.st_mode & 0o022
        ):
            raise OSError("insecure output metadata")
        output_descriptor = os.open(
            path.name,
            os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_descriptor,
        )
        if _file_identity(os.fstat(output_descriptor)) != identity:
            raise OSError("output changed while binding")
        return _OutputBinding(path, parent_descriptor, parent_snapshot, output_descriptor, identity)
    except OSError:
        if output_descriptor is not None:
            os.close(output_descriptor)
        if parent_descriptor is not None:
            os.close(parent_descriptor)
        return None


def _write_output(
    path: Path,
    report: dict[str, object],
    commit_safe=None,
    parent_snapshot: tuple[tuple[int, ...], ...] | None = None,
) -> bool:
    payload = json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n"
    parent_descriptor: int | None = None
    descriptor: int | None = None
    temporary_name: str | None = None
    published_identity: tuple[int, int] | None = None
    publication_complete = False
    try:
        if commit_safe is not None and not commit_safe():
            return False
        parent_descriptor, observed_parent = _open_bound_directory(path.parent)
        expected_parent = parent_snapshot or observed_parent
        if observed_parent != expected_parent or not _directory_still_bound(
            path.parent, parent_descriptor, expected_parent
        ):
            return False
        for _attempt in range(16):
            temporary_name = f".{path.name}.{secrets.token_hex(8)}"
            try:
                descriptor = os.open(
                    temporary_name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                    dir_fd=parent_descriptor,
                )
                break
            except FileExistsError:
                temporary_name = None
        if descriptor is None or temporary_name is None:
            return False
        data = payload.encode("utf-8")
        _write_all(descriptor, data)
        os.fchmod(descriptor, 0o444)
        os.fsync(descriptor)
        temporary_identity = os.fstat(descriptor)
        published_identity = (temporary_identity.st_dev, temporary_identity.st_ino)
        if (
            not stat.S_ISREG(temporary_identity.st_mode)
            or temporary_identity.st_nlink != 1
            or temporary_identity.st_uid != os.geteuid()
            or temporary_identity.st_size != len(data)
            or not _directory_still_bound(path.parent, parent_descriptor, expected_parent)
            or (commit_safe is not None and not commit_safe())
        ):
            return False
        os.replace(
            temporary_name,
            path.name,
            src_dir_fd=parent_descriptor,
            dst_dir_fd=parent_descriptor,
        )
        temporary_name = None
        publication_complete = True
        os.fsync(parent_descriptor)
        if (
            not _directory_still_bound(path.parent, parent_descriptor, expected_parent)
            or (commit_safe is not None and not commit_safe())
        ):
            _invalidate_published_output(descriptor)
            return False
        published_metadata = os.stat(path.name, dir_fd=parent_descriptor, follow_symlinks=False)
        if (published_metadata.st_dev, published_metadata.st_ino) != published_identity:
            _invalidate_published_output(descriptor)
            return False
        return True
    except OSError:
        if (
            publication_complete
            and parent_descriptor is not None
            and descriptor is not None
            and published_identity is not None
        ):
            _invalidate_published_output(descriptor)
        return False
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if parent_descriptor is not None:
            if temporary_name is not None:
                with contextlib.suppress(OSError):
                    os.unlink(temporary_name, dir_fd=parent_descriptor)
            os.close(parent_descriptor)


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
    output_binding = _capture_output_binding(output)
    try:
        report = audit(candidate_path, evidence_root, preserved_roots, output)
        if isinstance(report, _AuditReport) and not report.candidate_still_bound():
            _mark_candidate_changed(report)
        if output != evidence_root / OUTPUT_NAME:
            report["findings"] = sorted(set(report["findings"]) | {"output.unsafe"})
            report["status"] = "fail"
        output_within_evidence = True
        try:
            output.relative_to(evidence_root)
        except ValueError:
            output_within_evidence = False
            report["findings"] = sorted(set(report["findings"]) | {"output.unsafe"})
            report["status"] = "fail"
        else:
            output_parent_bound = (
                not isinstance(report, _AuditReport) or report.output_parent_snapshot is not None
            )
            output_written = False
            if (
                (not report["findings"] or "output.unsafe" not in report["findings"])
                and "output.collision" not in report["findings"]
            ):
                if output_parent_bound:
                    output_written = _write_output(
                        output,
                        report,
                        report.candidate_still_bound
                        if isinstance(report, _AuditReport) and report["status"] == "pass"
                        else None,
                        report.output_parent_snapshot if isinstance(report, _AuditReport) else None,
                    )
                if not output_written:
                    if isinstance(report, _AuditReport) and not report.candidate_still_bound():
                        _mark_candidate_changed(report)
                    else:
                        report["findings"] = sorted(set(report["findings"]) | {"output.write"})
                        report["status"] = "fail"
            if (
                report["status"] == "fail"
                and not output_written
                and output == evidence_root / OUTPUT_NAME
                and output_within_evidence
                and "output.collision" not in report["findings"]
                and output_binding is not None
            ):
                output_binding.publish_failure(report)
        print(json.dumps(report, sort_keys=True, separators=(",", ":")))
        return 0 if report["status"] == "pass" else 1
    finally:
        if output_binding is not None:
            output_binding.close()


if __name__ == "__main__":
    raise SystemExit(main())
