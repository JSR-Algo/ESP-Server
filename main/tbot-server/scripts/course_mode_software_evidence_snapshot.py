#!/usr/bin/env python3
"""Content-addressed capture and verification for Course Mode software evidence."""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Mapping, Sequence

SCHEMA_VERSION = 2
VALIDATOR = "course-mode-software-evidence.snapshot.v1"
OUTPUT_NAME = "06-software-evidence-audit.json"
FUTURE_PHYSICAL_OUTPUT_NAME = "07-physical-admission-gate.json"
MAX_ENTRIES = 4096
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_FILE_BYTES = 8 * 1024 * 1024
EXPECTED_CHECKS = {
    "candidateIdentity": True,
    "repositoryIdentity": True,
    "imageIdentityAndProvenance": True,
    "firmwareIdentity": True,
    "curriculumIdentity": True,
    "secureFileMetadata": True,
    "validator": True,
    "attestationBinding": True,
    "runtimeContinuity": True,
    "quickGate4of4": True,
    "fullGate20of20": True,
    "liveDbGate21of21": True,
    "terminalLivePostgres": True,
    "secretScan": True,
    "archiveSafety": True,
    "binaryMediaAbsent": True,
    "privateContentAbsent": True,
    "rawPlaywrightAbsent": True,
    "physicalActionsPerformed": False,
    "productionDatabaseUsed": False,
}

_SCOPES = {"candidate", "evidence", "preserved"}
_SCAN_POLICIES = {
    "candidate-json.v1",
    "evidence-privacy.v1",
    "physical-admission-top-level-session-id.v1",
    "preserved-evidence-privacy.v1",
}
_ADMISSION_KEYS = {"input", "output", "expectedIdentity", "expectedIdentitySignature"}
_ADMISSION_BINDING_KEYS = {
    "candidateSha256",
    "inputSha256",
    "expectedIdentitySha256",
    "signatureSha256",
    "signedCanonicalIdentitySha256",
    "sessionPolicy",
}
_SUBJECT_KEYS = {"scope", "path", "bytes", "sha256", "scanPolicy"}
_SNAPSHOT_KEYS = {"algorithm", "id", "subjects"}
_BASE_REPORT_KEYS = {
    "schemaVersion",
    "validator",
    "candidateId",
    "snapshot",
    "checkedArchiveMemberCount",
    "checkedFileCount",
    "checks",
    "findings",
    "status",
}
_TRUSTED_SYSTEM_SYMLINKS = {Path("/var"): Path("/private/var"), Path("/tmp"): Path("/private/tmp")}


@dataclass(frozen=True)
class CapturedSubject:
    scope: str
    path: str
    data: bytes
    scan_policy: str

    def manifest_entry(self) -> dict[str, object]:
        return {
            "scope": self.scope,
            "path": self.path,
            "bytes": len(self.data),
            "sha256": hashlib.sha256(self.data).hexdigest(),
            "scanPolicy": self.scan_policy,
        }


@dataclass(frozen=True)
class SnapshotCapture:
    subjects: tuple[CapturedSubject, ...]
    findings: tuple[str, ...]
    entry_count: int
    total_bytes: int


@dataclass(frozen=True)
class VerifiedSoftwareAudit:
    audit_sha256: str
    snapshot_id: str
    audit_identity: tuple[int, ...]
    report: dict[str, object]


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _strict_json_loads(data: bytes) -> object:
    def unique_pairs(items: list[tuple[str, object]]) -> dict[str, object]:
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
        object_pairs_hook=unique_pairs,
        parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("non-finite JSON value")),
        parse_float=finite,
    )


def _valid_logical_path(scope: str, value: object) -> bool:
    if not isinstance(value, str) or not value or "\\" in value:
        return False
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return False
    if scope == "candidate":
        return len(path.parts) == 1
    if scope == "preserved":
        return len(path.parts) >= 2 and path.parts[0].isdigit() and str(int(path.parts[0])) == path.parts[0]
    return True


def _validate_manifest_entry(entry: object) -> tuple[str, str]:
    if not isinstance(entry, dict) or set(entry) != _SUBJECT_KEYS:
        raise ValueError("invalid subject shape")
    scope = entry.get("scope")
    path = entry.get("path")
    byte_count = entry.get("bytes")
    digest = entry.get("sha256")
    policy = entry.get("scanPolicy")
    if scope not in _SCOPES or not _valid_logical_path(scope, path):
        raise ValueError("invalid subject path")
    if type(byte_count) is not int or byte_count < 0 or byte_count > MAX_FILE_BYTES:
        raise ValueError("invalid subject byte count")
    if not isinstance(digest, str) or len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise ValueError("invalid subject digest")
    if policy not in _SCAN_POLICIES:
        raise ValueError("invalid subject scan policy")
    if scope == "candidate" and policy != "candidate-json.v1":
        raise ValueError("invalid candidate policy")
    if scope == "preserved" and policy != "preserved-evidence-privacy.v1":
        raise ValueError("invalid preserved policy")
    if scope == "evidence" and policy not in {
        "evidence-privacy.v1",
        "physical-admission-top-level-session-id.v1",
    }:
        raise ValueError("invalid evidence policy")
    return scope, path


def _canonical_manifest_entries(entries: Sequence[dict[str, object]]) -> list[dict[str, object]]:
    validated: list[tuple[tuple[str, str], dict[str, object]]] = []
    keys: set[tuple[str, str]] = set()
    for raw_entry in entries:
        key = _validate_manifest_entry(raw_entry)
        if key in keys:
            raise ValueError("duplicate subject")
        keys.add(key)
        validated.append((key, dict(raw_entry)))
    validated.sort(key=lambda item: item[0])
    return [entry for _key, entry in validated]


def subject_manifest(subjects: Sequence[CapturedSubject]) -> list[dict[str, object]]:
    return _canonical_manifest_entries([subject.manifest_entry() for subject in subjects])


def snapshot_id(subjects: Sequence[dict[str, object]]) -> str:
    canonical = _canonical_manifest_entries(subjects)
    return hashlib.sha256(canonical_json_bytes(canonical)).hexdigest()


def _absolute(path: Path) -> Path:
    return path if path.is_absolute() else Path.cwd() / path


def _lexically_canonical(path: Path) -> bool:
    absolute = _absolute(path)
    return absolute.is_absolute() and not any(part in {".", ".."} for part in absolute.parts[1:])


def _identity(metadata: os.stat_result) -> tuple[int, ...]:
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


def _directory_authority_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_gid,
    )


def _secure_directory_chain(path: Path) -> tuple[tuple[Path, tuple[int, ...]], ...]:
    absolute = _absolute(path)
    if not _lexically_canonical(absolute):
        raise OSError("noncanonical directory")
    checked: list[tuple[Path, tuple[int, ...]]] = []
    current = Path(absolute.anchor)
    for component in absolute.parts[1:]:
        current /= component
        if current.is_symlink():
            replacement = _TRUSTED_SYSTEM_SYMLINKS.get(current)
            if replacement is None or current.resolve(strict=True) != replacement:
                raise OSError("linked directory")
            current = replacement
        metadata = current.lstat()
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid not in {0, os.geteuid()}
            or metadata.st_mode & 0o022
        ):
            raise OSError("insecure directory")
        checked.append((current, _directory_authority_identity(metadata)))
    final = absolute.lstat()
    if final.st_uid != os.geteuid():
        raise OSError("unowned root")
    return tuple(checked)


def _directories_stable(checked: Sequence[tuple[Path, tuple[int, ...]]]) -> bool:
    try:
        return all(_directory_authority_identity(path.lstat()) == identity for path, identity in checked)
    except OSError:
        return False


def _content_directories_stable(checked: Sequence[tuple[Path, tuple[int, ...]]]) -> bool:
    try:
        return all(_identity(path.lstat()) == identity for path, identity in checked)
    except OSError:
        return False


def _read_secure_file(path: Path) -> tuple[bytes, tuple[int, ...]]:
    absolute = _absolute(path)
    checked = _secure_directory_chain(absolute.parent)
    descriptor = os.open(
        absolute,
        os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_uid != os.geteuid()
            or before.st_mode & 0o022
            or before.st_size > MAX_FILE_BYTES
        ):
            raise OSError("insecure file")
        chunks: list[bytes] = []
        remaining = MAX_FILE_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        after = os.fstat(descriptor)
        current = absolute.lstat()
        identity = _identity(before)
        if (
            len(data) > MAX_FILE_BYTES
            or len(data) != before.st_size
            or _identity(after) != identity
            or _identity(current) != identity
            or not _directories_stable(checked)
        ):
            raise OSError("file changed while reading")
        return data, identity
    finally:
        os.close(descriptor)


def capture_candidate(candidate_path: Path) -> tuple[CapturedSubject | None, tuple[str, ...]]:
    path = _absolute(candidate_path)
    try:
        if not _lexically_canonical(path):
            raise OSError("noncanonical candidate")
        data, _identity_value = _read_secure_file(path)
        return CapturedSubject("candidate", path.name, data, "candidate-json.v1"), ()
    except OSError:
        return None, ("candidate.metadata",)


def _canonical_root(
    path: Path,
) -> tuple[Path, tuple[tuple[Path, tuple[int, ...]], ...], tuple[int, ...]]:
    absolute = _absolute(path)
    checked = _secure_directory_chain(absolute)
    if absolute.resolve(strict=True) != absolute:
        raise OSError("noncanonical root")
    return absolute, checked, _identity(absolute.lstat())


def _canonical_member(root: Path, path: Path) -> Path:
    absolute = _absolute(path)
    if not _lexically_canonical(absolute) or not absolute.is_relative_to(root):
        raise ValueError("path outside root")
    return absolute


def capture_snapshot(
    candidate_subject: CapturedSubject,
    evidence_root: Path,
    preserved_roots: Sequence[Path] = (),
    excluded_evidence_paths: Sequence[Path] = (),
    evidence_scan_policies: Mapping[Path, str] | None = None,
) -> SnapshotCapture:
    findings: set[str] = set()
    subjects: list[CapturedSubject] = [candidate_subject]
    entry_count = 1
    total_bytes = len(candidate_subject.data)
    try:
        subject_manifest((candidate_subject,))
    except ValueError:
        findings.add("candidate.metadata")

    try:
        evidence, evidence_ancestors, evidence_identity = _canonical_root(evidence_root)
    except OSError:
        return SnapshotCapture(tuple(subjects), ("evidence.root",), entry_count, total_bytes)

    declared_paths: dict[str, Path] | None = None
    try:
        candidate_document = _strict_json_loads(candidate_subject.data)
        declared_paths = (
            _admission_paths(candidate_document, evidence) if isinstance(candidate_document, dict) else None
        )
    except (UnicodeError, ValueError, RecursionError, json.JSONDecodeError):
        pass

    exclusions = {evidence / OUTPUT_NAME, evidence / FUTURE_PHYSICAL_OUTPUT_NAME}
    try:
        requested_exclusions = tuple(_canonical_member(evidence, path) for path in excluded_evidence_paths)
        declared_output = declared_paths["output"] if declared_paths is not None else None
        if len(requested_exclusions) > 1 or any(path != declared_output for path in requested_exclusions):
            raise ValueError("unsupported exclusion")
        exclusions.update(requested_exclusions)
    except ValueError:
        findings.add("evidence.exclusion")

    policies: dict[Path, str] = {}
    try:
        for path, policy in (evidence_scan_policies or {}).items():
            canonical = _canonical_member(evidence, path)
            if policy != "physical-admission-top-level-session-id.v1":
                raise ValueError("unsupported override")
            policies[canonical] = policy
        expected_policy_paths = (
            {declared_paths["input"], declared_paths["expectedIdentity"]}
            if declared_paths is not None
            else set()
        )
        if policies and set(policies) != expected_policy_paths:
            raise ValueError("incomplete or unrelated override set")
    except ValueError:
        findings.add("evidence.scan_policy")

    observed_policy_paths: set[Path] = set()
    entry_limit_hit = False
    byte_limit_hit = total_bytes > MAX_TOTAL_BYTES
    if byte_limit_hit:
        findings.add("evidence.budget")

    def walk(root: Path, scope: str, prefix: str, default_policy: str) -> None:
        nonlocal byte_limit_hit, entry_count, entry_limit_hit, total_bytes
        if entry_limit_hit or byte_limit_hit:
            return
        stack = [root]
        visited_directories: list[tuple[Path, tuple[int, ...]]] = []
        while stack:
            directory = stack.pop()
            try:
                iterator = os.scandir(directory)
            except OSError:
                findings.add(f"{scope}.root" if scope == "evidence" else "preserved.root")
                continue
            remaining_entries = MAX_ENTRIES - entry_count
            entries = []
            overflow = False
            with contextlib.closing(iterator):
                for entry in iterator:
                    if len(entries) >= remaining_entries:
                        overflow = True
                        break
                    entries.append(entry)
            entry_count += len(entries) + int(overflow)
            entries.sort(key=lambda entry: entry.name, reverse=True)
            if overflow:
                findings.add("evidence.budget")
                entry_limit_hit = True
                return
            for entry in entries:
                path = Path(entry.path)
                try:
                    metadata = entry.stat(follow_symlinks=False)
                except OSError:
                    findings.add(f"{scope}.metadata")
                    continue
                if stat.S_ISDIR(metadata.st_mode):
                    if metadata.st_uid != os.geteuid() or metadata.st_mode & 0o022 or entry.is_symlink():
                        findings.add(f"{scope}.metadata")
                        continue
                    visited_directories.append((path, _identity(metadata)))
                    stack.append(path)
                    continue
                if scope == "evidence" and path in exclusions:
                    if (
                        not stat.S_ISREG(metadata.st_mode)
                        or metadata.st_nlink != 1
                        or metadata.st_uid != os.geteuid()
                        or metadata.st_mode & 0o022
                        or entry.is_symlink()
                    ):
                        findings.add("evidence.metadata")
                    continue
                if not stat.S_ISREG(metadata.st_mode) or entry.is_symlink():
                    findings.add(f"{scope}.metadata")
                    continue
                try:
                    data, _file_identity = _read_secure_file(path)
                except OSError:
                    findings.add(f"{scope}.metadata")
                    continue
                total_bytes += len(data)
                if total_bytes > MAX_TOTAL_BYTES:
                    findings.add("evidence.budget")
                    byte_limit_hit = True
                    return
                relative = path.relative_to(root).as_posix()
                logical_path = f"{prefix}/{relative}" if prefix else relative
                policy = policies.get(path, default_policy) if scope == "evidence" else default_policy
                if path in policies:
                    observed_policy_paths.add(path)
                subjects.append(CapturedSubject(scope, logical_path, data, policy))
        if not _content_directories_stable(visited_directories):
            findings.add(f"{scope}.metadata")

    walk(evidence, "evidence", "", "evidence-privacy.v1")
    if (
        not _directories_stable(evidence_ancestors)
        or not _content_directories_stable(((evidence, evidence_identity),))
    ):
        findings.add("evidence.metadata")

    for index, root in enumerate(preserved_roots):
        try:
            preserved, preserved_ancestors, preserved_identity = _canonical_root(root)
        except OSError:
            findings.add("preserved.root")
            continue
        walk(preserved, "preserved", str(index), "preserved-evidence-privacy.v1")
        if (
            not _directories_stable(preserved_ancestors)
            or not _content_directories_stable(((preserved, preserved_identity),))
        ):
            findings.add("preserved.metadata")

    if observed_policy_paths != set(policies) or set(policies) & exclusions:
        findings.add("evidence.scan_policy")
    try:
        canonical_subjects = tuple(
            sorted(subjects, key=lambda item: (item.scope, item.path))
        )
        subject_manifest(canonical_subjects)
    except ValueError:
        findings.add("evidence.metadata")
        canonical_subjects = tuple(subjects)
    return SnapshotCapture(canonical_subjects, tuple(sorted(findings)), entry_count, total_bytes)


def _admission_paths(candidate: dict[str, object], evidence_root: Path) -> dict[str, Path] | None:
    tools = candidate.get("tools")
    if not isinstance(tools, dict) or "physicalAdmission" not in tools:
        return None
    descriptor = tools["physicalAdmission"]
    if not isinstance(descriptor, dict) or set(descriptor) != _ADMISSION_KEYS:
        raise ValueError("invalid admission descriptor")
    result: dict[str, Path] = {}
    for key, value in descriptor.items():
        if not isinstance(value, str):
            raise ValueError("invalid admission path")
        path = Path(value)
        if not path.is_absolute() or not _lexically_canonical(path) or not path.is_relative_to(evidence_root):
            raise ValueError("invalid admission path")
        result[key] = path
    if len(set(result.values())) != len(result):
        raise ValueError("aliased admission path")
    return result


def _validate_report_subjects(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise ValueError("subjects are not an array")
    canonical = _canonical_manifest_entries(value)
    if canonical != value:
        raise ValueError("subjects are not canonical")
    if len(canonical) > MAX_ENTRIES or sum(entry["bytes"] for entry in canonical) > MAX_TOTAL_BYTES:
        raise ValueError("subject budget exceeded")
    return canonical


def _subject_digest(
    subjects: Sequence[dict[str, object]], scope: str, path: str, policy: str | None = None
) -> str | None:
    matches = [entry for entry in subjects if entry["scope"] == scope and entry["path"] == path]
    if len(matches) != 1 or (policy is not None and matches[0]["scanPolicy"] != policy):
        return None
    digest = matches[0]["sha256"]
    return digest if isinstance(digest, str) else None


def verify_current_software_audit(
    candidate_path: Path,
    evidence_root: Path,
    preserved_roots: Sequence[Path] = (),
) -> tuple[VerifiedSoftwareAudit | None, tuple[str, ...]]:
    candidate_path = _absolute(candidate_path)
    evidence_root = _absolute(evidence_root)
    report_path = evidence_root / OUTPUT_NAME
    try:
        report_bytes, report_identity = _read_secure_file(report_path)
    except FileNotFoundError:
        return None, ("softwareAudit.missing",)
    except OSError:
        return None, ("softwareAudit.metadata",)
    try:
        report = _strict_json_loads(report_bytes)
    except (UnicodeError, ValueError, RecursionError, json.JSONDecodeError):
        return None, ("softwareAudit.json",)
    if not isinstance(report, dict):
        return None, ("softwareAudit.schema",)

    candidate_subject, candidate_findings = capture_candidate(candidate_path)
    if candidate_subject is None or candidate_findings:
        return None, ("softwareAudit.candidate",)
    try:
        candidate = _strict_json_loads(candidate_subject.data)
    except (UnicodeError, ValueError, RecursionError, json.JSONDecodeError):
        return None, ("softwareAudit.candidate",)
    if not isinstance(candidate, dict) or candidate.get("evidenceRoot") != str(evidence_root):
        return None, ("softwareAudit.candidate",)
    try:
        admission_paths = _admission_paths(candidate, evidence_root)
    except ValueError:
        return None, ("softwareAudit.candidate",)

    expected_keys = set(_BASE_REPORT_KEYS)
    if admission_paths is not None:
        expected_keys.add("admissionBinding")
    if (
        set(report) != expected_keys
        or report.get("schemaVersion") != SCHEMA_VERSION
        or report.get("validator") != VALIDATOR
    ):
        return None, ("softwareAudit.schema",)
    if report.get("status") != "pass" or report.get("findings") != []:
        return None, ("softwareAudit.status",)
    if report.get("checks") != EXPECTED_CHECKS:
        return None, ("softwareAudit.checks",)
    if report.get("candidateId") != candidate.get("candidateId"):
        return None, ("softwareAudit.candidate",)
    if type(report.get("checkedArchiveMemberCount")) is not int or report["checkedArchiveMemberCount"] < 0:
        return None, ("softwareAudit.schema",)

    snapshot = report.get("snapshot")
    try:
        if not isinstance(snapshot, dict) or set(snapshot) != _SNAPSHOT_KEYS or snapshot.get("algorithm") != "sha256":
            raise ValueError("invalid snapshot")
        subjects = _validate_report_subjects(snapshot.get("subjects"))
        if type(report.get("checkedFileCount")) is not int or report["checkedFileCount"] != len(subjects):
            raise ValueError("wrong subject count")
        expected_snapshot_id = snapshot_id(subjects)
        if snapshot.get("id") != expected_snapshot_id:
            raise ValueError("wrong snapshot id")
    except (TypeError, ValueError):
        return None, ("softwareAudit.snapshot",)

    policies: dict[Path, str] = {}
    exclusions: tuple[Path, ...] = ()
    if admission_paths is not None:
        policies = {
            admission_paths["input"]: "physical-admission-top-level-session-id.v1",
            admission_paths["expectedIdentity"]: "physical-admission-top-level-session-id.v1",
        }
        exclusions = (admission_paths["output"],)
    current = capture_snapshot(
        candidate_subject,
        evidence_root,
        preserved_roots=preserved_roots,
        excluded_evidence_paths=exclusions,
        evidence_scan_policies=policies,
    )
    if current.findings:
        return None, ("softwareAudit.stale",)
    try:
        current_subjects = subject_manifest(current.subjects)
    except ValueError:
        return None, ("softwareAudit.stale",)
    if current_subjects != subjects:
        return None, ("softwareAudit.stale",)

    if admission_paths is not None:
        binding = report.get("admissionBinding")
        if not isinstance(binding, dict) or set(binding) != _ADMISSION_BINDING_KEYS:
            return None, ("softwareAudit.admissionBinding",)
        relative = {key: path.relative_to(evidence_root).as_posix() for key, path in admission_paths.items()}
        expected_hashes = {
            "candidateSha256": _subject_digest(
                subjects, "candidate", candidate_path.name, "candidate-json.v1"
            ),
            "inputSha256": _subject_digest(
                subjects,
                "evidence",
                relative["input"],
                "physical-admission-top-level-session-id.v1",
            ),
            "expectedIdentitySha256": _subject_digest(
                subjects,
                "evidence",
                relative["expectedIdentity"],
                "physical-admission-top-level-session-id.v1",
            ),
            "signatureSha256": _subject_digest(
                subjects, "evidence", relative["expectedIdentitySignature"], "evidence-privacy.v1"
            ),
        }
        identity_record = next(
            (
                item
                for item in current.subjects
                if item.scope == "evidence" and item.path == relative["expectedIdentity"]
            ),
            None,
        )
        input_record = next(
            (
                item
                for item in current.subjects
                if item.scope == "evidence" and item.path == relative["input"]
            ),
            None,
        )
        try:
            input_document = _strict_json_loads(input_record.data if input_record else b"")
            identity_document = _strict_json_loads(identity_record.data if identity_record else b"")
            signed_identity_hash = hashlib.sha256(canonical_json_bytes(identity_document)).hexdigest()
        except (UnicodeError, ValueError, RecursionError, json.JSONDecodeError):
            return None, ("softwareAudit.admissionBinding",)
        candidate_binding = input_document.get("candidate") if isinstance(input_document, dict) else None
        input_session = input_document.get("sessionId") if isinstance(input_document, dict) else None
        identity_session = identity_document.get("sessionId") if isinstance(identity_document, dict) else None
        try:
            canonical_session = str(uuid.UUID(input_session)) if isinstance(input_session, str) else None
        except ValueError:
            canonical_session = None
        if (
            any(digest is None for digest in expected_hashes.values())
            or not isinstance(candidate_binding, dict)
            or candidate_binding.get("path") != str(candidate_path)
            or candidate_binding.get("sha256") != expected_hashes["candidateSha256"]
            or canonical_session != input_session
            or identity_session != input_session
            or binding.get("sessionPolicy") != "top-level-canonical-uuid.v1"
            or any(binding.get(key) != value for key, value in expected_hashes.items())
            or binding.get("signedCanonicalIdentitySha256") != signed_identity_hash
        ):
            return None, ("softwareAudit.admissionBinding",)

    snapshot_value = snapshot.get("id")
    assert isinstance(snapshot_value, str)
    try:
        if _identity(report_path.lstat()) != report_identity:
            return None, ("softwareAudit.metadata",)
    except OSError:
        return None, ("softwareAudit.metadata",)
    return (
        VerifiedSoftwareAudit(
            audit_sha256=hashlib.sha256(report_bytes).hexdigest(),
            snapshot_id=snapshot_value,
            audit_identity=report_identity,
            report=report,
        ),
        (),
    )
