from __future__ import annotations

import hashlib
import json
import os
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import course_mode_software_evidence_snapshot as snapshot  # noqa: E402


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.chmod(0o644)
    path.write_bytes(data)
    path.chmod(0o444)


def _write_json(path: Path, value: object) -> None:
    _write(path, snapshot.canonical_json_bytes(value) + b"\n")


def _candidate_document(candidate_path: Path, evidence_root: Path, *, admission: bool) -> dict[str, object]:
    document: dict[str, object] = {
        "candidateId": "course-mode-2026-09-09.44",
        "evidenceRoot": str(evidence_root),
        "tools": {"pythonTestRuntime": {"version": 1}},
    }
    if admission:
        admission_root = evidence_root / "G7-admission"
        document["tools"] = {
            "physicalAdmission": {
                "input": str(admission_root / "admission-input.json"),
                "output": str(admission_root / "admission-result.json"),
                "expectedIdentity": str(admission_root / "expected-identity.json"),
                "expectedIdentitySignature": str(admission_root / "expected-identity.sig"),
            }
        }
    return document


def _create_tree(tmp_path: Path, *, admission: bool = True) -> tuple[Path, Path, dict[str, Path]]:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    candidate_path = tmp_path / "candidate.json"
    candidate = _candidate_document(candidate_path, evidence_root, admission=admission)
    _write_json(candidate_path, candidate)
    paths: dict[str, Path] = {}
    if admission:
        descriptor = candidate["tools"]["physicalAdmission"]  # type: ignore[index]
        paths = {key: Path(value) for key, value in descriptor.items()}
        session_id = str(uuid.uuid4())
        _write_json(
            paths["input"],
            {
                "candidate": {
                    "path": str(candidate_path),
                    "sha256": hashlib.sha256(candidate_path.read_bytes()).hexdigest(),
                },
                "checkedAt": "2026-09-09T00:00:00Z",
                "sessionId": session_id,
            },
        )
        _write_json(paths["expectedIdentity"], {"mac": "14:C1:9F:D1:AC:20", "sessionId": session_id})
        _write(paths["expectedIdentitySignature"], b"s" * 64)
    _write_json(evidence_root / "nested/report.json", {"status": "pass"})
    return candidate_path, evidence_root, paths


def _capture(candidate_path: Path, evidence_root: Path, paths: dict[str, Path], *preserved: Path):
    candidate_subject, findings = snapshot.capture_candidate(candidate_path)
    assert findings == ()
    assert candidate_subject is not None
    policies = {}
    exclusions = ()
    if paths:
        policies = {
            paths["input"]: "physical-admission-top-level-session-id.v1",
            paths["expectedIdentity"]: "physical-admission-top-level-session-id.v1",
        }
        exclusions = (paths["output"],)
    capture = snapshot.capture_snapshot(
        candidate_subject,
        evidence_root,
        preserved_roots=preserved,
        excluded_evidence_paths=exclusions,
        evidence_scan_policies=policies,
    )
    assert capture.findings == ()
    return capture


def _valid_report(candidate_path: Path, evidence_root: Path, paths: dict[str, Path]) -> dict[str, object]:
    capture = _capture(candidate_path, evidence_root, paths)
    subjects = snapshot.subject_manifest(capture.subjects)
    report: dict[str, object] = {
        "schemaVersion": snapshot.SCHEMA_VERSION,
        "validator": snapshot.VALIDATOR,
        "candidateId": "course-mode-2026-09-09.44",
        "snapshot": {
            "algorithm": "sha256",
            "id": snapshot.snapshot_id(subjects),
            "subjects": subjects,
        },
        "checkedArchiveMemberCount": 0,
        "checkedFileCount": len(subjects),
        "checks": dict(snapshot.EXPECTED_CHECKS),
        "findings": [],
        "status": "pass",
    }
    if paths:
        by_key = {(item["scope"], item["path"]): item for item in subjects}
        identity_value = json.loads(paths["expectedIdentity"].read_bytes())
        report["admissionBinding"] = {
            "candidateSha256": by_key[("candidate", candidate_path.name)]["sha256"],
            "inputSha256": by_key[("evidence", paths["input"].relative_to(evidence_root).as_posix())]["sha256"],
            "expectedIdentitySha256": by_key[
                ("evidence", paths["expectedIdentity"].relative_to(evidence_root).as_posix())
            ]["sha256"],
            "signatureSha256": by_key[
                ("evidence", paths["expectedIdentitySignature"].relative_to(evidence_root).as_posix())
            ]["sha256"],
            "signedCanonicalIdentitySha256": hashlib.sha256(
                snapshot.canonical_json_bytes(identity_value)
            ).hexdigest(),
            "sessionPolicy": "top-level-canonical-uuid.v1",
        }
    return report


def _publish_report(evidence_root: Path, report: dict[str, object]) -> Path:
    report_path = evidence_root / snapshot.OUTPUT_NAME
    _write_json(report_path, report)
    return report_path


def test_snapshot_id_is_canonical_and_order_independent() -> None:
    records = (
        snapshot.CapturedSubject("evidence", "z.json", b"z", "evidence-privacy.v1"),
        snapshot.CapturedSubject("candidate", "candidate.json", b"c", "candidate-json.v1"),
    )

    first = snapshot.subject_manifest(records)
    second = snapshot.subject_manifest(tuple(reversed(records)))

    assert first == second
    assert snapshot.snapshot_id(first) == snapshot.snapshot_id(second)
    assert first[0]["scope"] == "candidate"
    assert first[1]["bytes"] == 1
    assert first[1]["sha256"] == hashlib.sha256(b"z").hexdigest()


@pytest.mark.parametrize(
    ("scope", "path", "policy"),
    [
        ("invalid", "x.json", "evidence-privacy.v1"),
        ("evidence", "/absolute.json", "evidence-privacy.v1"),
        ("evidence", "a/../b.json", "evidence-privacy.v1"),
        ("evidence", "x.json", "unknown-policy.v1"),
        ("candidate", "nested/candidate.json", "candidate-json.v1"),
        ("preserved", "summary.json", "preserved-evidence-privacy.v1"),
    ],
)
def test_subject_manifest_rejects_noncanonical_subjects(scope: str, path: str, policy: str) -> None:
    with pytest.raises(ValueError):
        snapshot.subject_manifest((snapshot.CapturedSubject(scope, path, b"x", policy),))


def test_subject_manifest_rejects_duplicate_scope_and_path() -> None:
    subject = snapshot.CapturedSubject("evidence", "x.json", b"x", "evidence-privacy.v1")
    with pytest.raises(ValueError):
        snapshot.subject_manifest((subject, subject))


def test_capture_snapshot_uses_retained_candidate_exclusions_overrides_and_preserved_indexes(
    tmp_path: Path,
) -> None:
    candidate_path, evidence_root, paths = _create_tree(tmp_path)
    preserved_a = tmp_path / "preserved-a"
    preserved_b = tmp_path / "preserved-b"
    _write_json(preserved_a / "runtime/tombstone.json", {"ok": True})
    _write_json(preserved_b / "browser/summary.json", {"ok": True})
    _write_json(evidence_root / snapshot.OUTPUT_NAME, {"old": True})
    _write_json(paths["output"], {"old": True})
    _write_json(evidence_root / snapshot.FUTURE_PHYSICAL_OUTPUT_NAME, {"old": True})

    capture = _capture(candidate_path, evidence_root, paths, preserved_a, preserved_b)

    by_key = {(item.scope, item.path): item for item in capture.subjects}
    assert ("candidate", candidate_path.name) in by_key
    assert ("evidence", "nested/report.json") in by_key
    assert ("preserved", "0/runtime/tombstone.json") in by_key
    assert ("preserved", "1/browser/summary.json") in by_key
    assert ("evidence", snapshot.OUTPUT_NAME) not in by_key
    assert ("evidence", paths["output"].relative_to(evidence_root).as_posix()) not in by_key
    assert ("evidence", snapshot.FUTURE_PHYSICAL_OUTPUT_NAME) not in by_key
    assert by_key[("evidence", paths["input"].relative_to(evidence_root).as_posix())].scan_policy == (
        "physical-admission-top-level-session-id.v1"
    )
    assert by_key[
        ("evidence", paths["expectedIdentitySignature"].relative_to(evidence_root).as_posix())
    ].scan_policy == "evidence-privacy.v1"
    assert capture.entry_count == 14
    assert capture.total_bytes == sum(len(item.data) for item in capture.subjects)


@pytest.mark.parametrize("kind", ["outside", "missing", "excluded"])
def test_capture_snapshot_rejects_unknown_scan_policy_override(tmp_path: Path, kind: str) -> None:
    candidate_path, evidence_root, paths = _create_tree(tmp_path)
    candidate_subject, findings = snapshot.capture_candidate(candidate_path)
    assert findings == () and candidate_subject is not None
    override = {
        "outside": tmp_path / "outside.json",
        "missing": evidence_root / "missing.json",
        "excluded": paths["output"],
    }[kind]

    capture = snapshot.capture_snapshot(
        candidate_subject,
        evidence_root,
        excluded_evidence_paths=(paths["output"],),
        evidence_scan_policies={override: "physical-admission-top-level-session-id.v1"},
    )

    assert "evidence.scan_policy" in capture.findings


def test_capture_snapshot_rejects_session_policy_for_unrelated_existing_evidence(tmp_path: Path) -> None:
    candidate_path, evidence_root, paths = _create_tree(tmp_path)
    candidate_subject, findings = snapshot.capture_candidate(candidate_path)
    assert findings == () and candidate_subject is not None

    capture = snapshot.capture_snapshot(
        candidate_subject,
        evidence_root,
        excluded_evidence_paths=(paths["output"],),
        evidence_scan_policies={
            evidence_root / "nested/report.json": "physical-admission-top-level-session-id.v1"
        },
    )

    assert "evidence.scan_policy" in capture.findings


@pytest.mark.parametrize("descriptor", ["missing", "invalid"])
def test_capture_snapshot_rejects_nonempty_overrides_without_valid_admission_descriptor(
    tmp_path: Path, descriptor: str
) -> None:
    candidate_path, evidence_root, _paths = _create_tree(tmp_path, admission=False)
    if descriptor == "invalid":
        candidate = json.loads(candidate_path.read_bytes())
        candidate["tools"]["physicalAdmission"] = None
        _write_json(candidate_path, candidate)
    candidate_subject, findings = snapshot.capture_candidate(candidate_path)
    assert findings == () and candidate_subject is not None

    capture = snapshot.capture_snapshot(
        candidate_subject,
        evidence_root,
        evidence_scan_policies={
            evidence_root / "nested/report.json": "physical-admission-top-level-session-id.v1"
        },
    )

    assert "evidence.scan_policy" in capture.findings


def test_capture_snapshot_rejects_incomplete_admission_override_set(tmp_path: Path) -> None:
    candidate_path, evidence_root, paths = _create_tree(tmp_path)
    candidate_subject, findings = snapshot.capture_candidate(candidate_path)
    assert findings == () and candidate_subject is not None

    capture = snapshot.capture_snapshot(
        candidate_subject,
        evidence_root,
        excluded_evidence_paths=(paths["output"],),
        evidence_scan_policies={
            paths["input"]: "physical-admission-top-level-session-id.v1"
        },
    )

    assert "evidence.scan_policy" in capture.findings


def test_capture_snapshot_rejects_more_than_one_caller_exclusion(tmp_path: Path) -> None:
    candidate_path, evidence_root, paths = _create_tree(tmp_path)
    candidate_subject, findings = snapshot.capture_candidate(candidate_path)
    assert findings == () and candidate_subject is not None

    capture = snapshot.capture_snapshot(
        candidate_subject,
        evidence_root,
        excluded_evidence_paths=(paths["output"], evidence_root / "nested/report.json"),
    )

    assert "evidence.exclusion" in capture.findings


def test_capture_candidate_rejects_symlink_hardlink_writable_and_non_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate_path, _evidence_root, _paths = _create_tree(tmp_path, admission=False)
    original = candidate_path.read_bytes()
    target = tmp_path / "target.json"
    _write(target, original)
    candidate_path.unlink()
    candidate_path.symlink_to(target)
    assert snapshot.capture_candidate(candidate_path)[0] is None

    candidate_path.unlink()
    os.link(target, candidate_path)
    assert snapshot.capture_candidate(candidate_path)[0] is None

    candidate_path.unlink()
    _write(candidate_path, original)
    candidate_path.chmod(0o666)
    assert snapshot.capture_candidate(candidate_path)[0] is None

    candidate_path.chmod(0o444)
    original_uid = os.geteuid()
    monkeypatch.setattr(snapshot.os, "geteuid", lambda: original_uid + 1)
    assert snapshot.capture_candidate(candidate_path)[0] is None


def test_capture_snapshot_rejects_writable_directory_and_symlink_entry(tmp_path: Path) -> None:
    candidate_path, evidence_root, paths = _create_tree(tmp_path)
    candidate_subject, findings = snapshot.capture_candidate(candidate_path)
    assert findings == () and candidate_subject is not None
    nested = evidence_root / "nested"
    nested.chmod(0o777)
    capture = snapshot.capture_snapshot(candidate_subject, evidence_root)
    assert "evidence.metadata" in capture.findings

    nested.chmod(0o755)
    (evidence_root / "linked.json").symlink_to(nested / "report.json")
    capture = snapshot.capture_snapshot(candidate_subject, evidence_root)
    assert "evidence.metadata" in capture.findings


def test_capture_candidate_rejects_file_over_eight_mib(tmp_path: Path) -> None:
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_bytes(b"x" * (8 * 1024 * 1024 + 1))
    candidate_path.chmod(0o444)
    subject, findings = snapshot.capture_candidate(candidate_path)
    assert subject is None
    assert "candidate.metadata" in findings


def test_capture_snapshot_rejects_more_than_4096_entries(tmp_path: Path) -> None:
    candidate_path, evidence_root, _paths = _create_tree(tmp_path, admission=False)
    for index in range(4095):
        _write(evidence_root / f"entry-{index:04d}", b"")
    candidate_subject, findings = snapshot.capture_candidate(candidate_path)
    assert findings == () and candidate_subject is not None

    capture = snapshot.capture_snapshot(candidate_subject, evidence_root)

    assert "evidence.budget" in capture.findings
    assert capture.entry_count > 4096


def test_capture_snapshot_rejects_more_than_64_mib_total(tmp_path: Path) -> None:
    candidate_path, evidence_root, _paths = _create_tree(tmp_path, admission=False)
    for index in range(9):
        path = evidence_root / f"large-{index}.bin"
        path.write_bytes(b"x" * (8 * 1024 * 1024))
        path.chmod(0o444)
    candidate_subject, findings = snapshot.capture_candidate(candidate_path)
    assert findings == () and candidate_subject is not None

    capture = snapshot.capture_snapshot(candidate_subject, evidence_root)

    assert "evidence.budget" in capture.findings
    assert capture.total_bytes > 64 * 1024 * 1024


@pytest.mark.parametrize("mutation", ["change", "add", "remove", "rename"])
def test_verify_current_snapshot_rejects_any_tree_drift(tmp_path: Path, mutation: str) -> None:
    candidate_path, evidence_root, paths = _create_tree(tmp_path)
    report = _valid_report(candidate_path, evidence_root, paths)
    _publish_report(evidence_root, report)
    target = evidence_root / "nested/report.json"
    if mutation == "change":
        target.chmod(0o644)
        target.write_bytes(b'changed\n')
        target.chmod(0o444)
    elif mutation == "add":
        _write(evidence_root / "added.json", b"added\n")
    elif mutation == "remove":
        target.unlink()
    else:
        target.rename(evidence_root / "nested/renamed.json")

    verified, reasons = snapshot.verify_current_software_audit(candidate_path, evidence_root)

    assert verified is None
    assert "softwareAudit.stale" in reasons


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (lambda report: report.update(schemaVersion=1), "softwareAudit.schema"),
        (lambda report: report.update(validator="wrong"), "softwareAudit.schema"),
        (lambda report: report.update(status="fail"), "softwareAudit.status"),
        (lambda report: report.update(findings=["bad"]), "softwareAudit.status"),
        (lambda report: report["checks"].update(secretScan=False), "softwareAudit.checks"),
        (lambda report: report["snapshot"].update(id="0" * 64), "softwareAudit.snapshot"),
        (lambda report: report.update(candidateId="course-mode-2026-09-09.45"), "softwareAudit.candidate"),
        (lambda report: report["admissionBinding"].update(sessionPolicy="wrong"), "softwareAudit.admissionBinding"),
    ],
)
def test_verify_current_software_audit_rejects_invalid_report(
    tmp_path: Path, mutation, reason: str
) -> None:
    candidate_path, evidence_root, paths = _create_tree(tmp_path)
    report = _valid_report(candidate_path, evidence_root, paths)
    mutation(report)
    _publish_report(evidence_root, report)

    verified, reasons = snapshot.verify_current_software_audit(candidate_path, evidence_root)

    assert verified is None
    assert reason in reasons


def test_verify_current_software_audit_rejects_missing_and_malformed_json(tmp_path: Path) -> None:
    candidate_path, evidence_root, _paths = _create_tree(tmp_path, admission=False)
    assert snapshot.verify_current_software_audit(candidate_path, evidence_root) == (
        None,
        ("softwareAudit.missing",),
    )
    _write(evidence_root / snapshot.OUTPUT_NAME, b'{"schemaVersion":2,"schemaVersion":2}')
    verified, reasons = snapshot.verify_current_software_audit(candidate_path, evidence_root)
    assert verified is None
    assert "softwareAudit.json" in reasons


@pytest.mark.parametrize("field", ["bytes", "sha256", "scanPolicy"])
def test_verify_current_software_audit_rejects_invalid_subject_field(tmp_path: Path, field: str) -> None:
    candidate_path, evidence_root, paths = _create_tree(tmp_path)
    report = _valid_report(candidate_path, evidence_root, paths)
    subject = report["snapshot"]["subjects"][0]
    subject[field] = {"bytes": -1, "sha256": "A" * 64, "scanPolicy": "wrong"}[field]
    _publish_report(evidence_root, report)

    verified, reasons = snapshot.verify_current_software_audit(candidate_path, evidence_root)

    assert verified is None
    assert "softwareAudit.snapshot" in reasons


def test_verify_current_software_audit_rejects_non_integer_bytes_and_duplicate_subjects(
    tmp_path: Path,
) -> None:
    candidate_path, evidence_root, paths = _create_tree(tmp_path)
    report = _valid_report(candidate_path, evidence_root, paths)
    report["snapshot"]["subjects"][0]["bytes"] = 1.0
    _publish_report(evidence_root, report)
    assert "softwareAudit.snapshot" in snapshot.verify_current_software_audit(candidate_path, evidence_root)[1]

    report = _valid_report(candidate_path, evidence_root, paths)
    report["snapshot"]["subjects"].append(dict(report["snapshot"]["subjects"][0]))
    report["checkedFileCount"] += 1
    _publish_report(evidence_root, report)
    assert "softwareAudit.snapshot" in snapshot.verify_current_software_audit(candidate_path, evidence_root)[1]


@pytest.mark.parametrize("field", ["bytes", "sha256", "scanPolicy"])
def test_verify_current_software_audit_rejects_validly_shaped_but_wrong_subject(
    tmp_path: Path, field: str
) -> None:
    candidate_path, evidence_root, paths = _create_tree(tmp_path)
    report = _valid_report(candidate_path, evidence_root, paths)
    subject = report["snapshot"]["subjects"][-1]
    subject[field] = {
        "bytes": subject["bytes"] + 1,
        "sha256": "0" * 64,
        "scanPolicy": "physical-admission-top-level-session-id.v1",
    }[field]
    report["snapshot"]["id"] = snapshot.snapshot_id(report["snapshot"]["subjects"])
    _publish_report(evidence_root, report)

    verified, reasons = snapshot.verify_current_software_audit(candidate_path, evidence_root)

    assert verified is None
    assert "softwareAudit.stale" in reasons


@pytest.mark.parametrize(
    "field",
    [
        "candidateSha256",
        "inputSha256",
        "expectedIdentitySha256",
        "signatureSha256",
        "signedCanonicalIdentitySha256",
    ],
)
def test_verify_current_software_audit_rejects_wrong_admission_binding_hash(
    tmp_path: Path, field: str
) -> None:
    candidate_path, evidence_root, paths = _create_tree(tmp_path)
    report = _valid_report(candidate_path, evidence_root, paths)
    report["admissionBinding"][field] = "0" * 64
    _publish_report(evidence_root, report)

    verified, reasons = snapshot.verify_current_software_audit(candidate_path, evidence_root)

    assert verified is None
    assert "softwareAudit.admissionBinding" in reasons


def test_verify_current_software_audit_requires_exact_admission_binding_keys(tmp_path: Path) -> None:
    candidate_path, evidence_root, paths = _create_tree(tmp_path)
    report = _valid_report(candidate_path, evidence_root, paths)
    report["admissionBinding"]["extra"] = True
    _publish_report(evidence_root, report)
    assert "softwareAudit.admissionBinding" in snapshot.verify_current_software_audit(
        candidate_path, evidence_root
    )[1]


@pytest.mark.parametrize("mutation", ["candidate-path", "candidate-hash", "session-mismatch", "session-noncanonical"])
def test_verify_current_software_audit_rejects_invalid_admission_document_binding(
    tmp_path: Path, mutation: str
) -> None:
    candidate_path, evidence_root, paths = _create_tree(tmp_path)
    admission_input = json.loads(paths["input"].read_bytes())
    identity = json.loads(paths["expectedIdentity"].read_bytes())
    if mutation == "candidate-path":
        admission_input["candidate"]["path"] = str(tmp_path / "other.json")
    elif mutation == "candidate-hash":
        admission_input["candidate"]["sha256"] = "0" * 64
    elif mutation == "session-mismatch":
        identity["sessionId"] = str(uuid.uuid4())
    else:
        admission_input["sessionId"] = admission_input["sessionId"].upper()
    _write_json(paths["input"], admission_input)
    _write_json(paths["expectedIdentity"], identity)
    report = _valid_report(candidate_path, evidence_root, paths)
    _publish_report(evidence_root, report)

    verified, reasons = snapshot.verify_current_software_audit(candidate_path, evidence_root)

    assert verified is None
    assert "softwareAudit.admissionBinding" in reasons


def test_verify_current_software_audit_returns_hash_snapshot_and_stable_audit_identity(
    tmp_path: Path,
) -> None:
    candidate_path, evidence_root, paths = _create_tree(tmp_path)
    report = _valid_report(candidate_path, evidence_root, paths)
    report_path = _publish_report(evidence_root, report)
    expected_hash = hashlib.sha256(report_path.read_bytes()).hexdigest()

    first, reasons = snapshot.verify_current_software_audit(candidate_path, evidence_root)

    assert reasons == ()
    assert first is not None
    assert first.audit_sha256 == expected_hash
    assert first.snapshot_id == report["snapshot"]["id"]
    assert len(first.audit_identity) >= 5
    assert first.report == report

    same_bytes = report_path.read_bytes()
    report_path.unlink()
    _write(report_path, same_bytes)
    second, reasons = snapshot.verify_current_software_audit(candidate_path, evidence_root)
    assert reasons == () and second is not None
    assert second.audit_sha256 == first.audit_sha256
    assert second.audit_identity != first.audit_identity


def test_verify_current_software_audit_accepts_no_admission_candidate(tmp_path: Path) -> None:
    candidate_path, evidence_root, paths = _create_tree(tmp_path, admission=False)
    report = _valid_report(candidate_path, evidence_root, paths)
    _publish_report(evidence_root, report)

    verified, reasons = snapshot.verify_current_software_audit(candidate_path, evidence_root)

    assert reasons == ()
    assert verified is not None


def test_verify_current_software_audit_rejects_declared_but_malformed_admission(tmp_path: Path) -> None:
    candidate_path, evidence_root, paths = _create_tree(tmp_path, admission=False)
    report = _valid_report(candidate_path, evidence_root, paths)
    candidate = json.loads(candidate_path.read_bytes())
    candidate["tools"]["physicalAdmission"] = None
    _write_json(candidate_path, candidate)
    _publish_report(evidence_root, report)

    verified, reasons = snapshot.verify_current_software_audit(candidate_path, evidence_root)

    assert verified is None
    assert "softwareAudit.candidate" in reasons


def test_verifier_has_no_process_network_or_write_side_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate_path, evidence_root, paths = _create_tree(tmp_path)
    report = _valid_report(candidate_path, evidence_root, paths)
    _publish_report(evidence_root, report)
    before = {path: path.stat().st_mtime_ns for path in tmp_path.rglob("*")}

    monkeypatch.setattr(os, "system", lambda *_args, **_kwargs: pytest.fail("process call"))
    verified, reasons = snapshot.verify_current_software_audit(candidate_path, evidence_root)

    assert reasons == () and verified is not None
    assert {path: path.stat().st_mtime_ns for path in tmp_path.rglob("*")} == before
