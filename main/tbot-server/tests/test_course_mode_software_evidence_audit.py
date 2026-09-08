from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import stat
import subprocess
import sys
import uuid
import zipfile
import zlib
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/course_mode_software_evidence_audit.py"
sys.path.insert(0, str(ROOT / "scripts"))
import course_mode_evidence_privacy as privacy  # noqa: E402
import course_mode_physical_flash_admission as admission  # noqa: E402
import course_mode_software_evidence_audit as auditor  # noqa: E402

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


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    path.chmod(0o444)


def _lanes(names: list[str]) -> list[dict[str, object]]:
    return [{"durationMs": index + 1, "exitCode": 0, "name": name} for index, name in enumerate(names)]


@pytest.fixture
def evidence_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    candidate_path = tmp_path / "candidate.json"
    output = evidence / "06-software-evidence-audit.json"
    candidate_id = "course-mode-2026-09-05.99"
    admin_sha = "a" * 40
    backend_sha = "b" * 40
    firmware_sha = "c" * 40
    candidate = {
        "candidateId": candidate_id,
        "evidenceRoot": str(evidence),
        "repositories": {
            "backend": {"sha": backend_sha},
            "adminEsp": {"sha": admin_sha},
            "firmware": {"sha": firmware_sha},
        },
        "images": {
            "lessonStudioBackend": {
                "reference": f"local/tbot-backend:course-mode-physical-tft-{backend_sha}",
                "id": "sha256:" + "1" * 64,
            },
            "lessonStudioWeb": {
                "reference": f"local/tbot-server-web:course-mode-physical-tft-{admin_sha}",
                "id": "sha256:" + "2" * 64,
            },
        },
        "firmware": {
            "appOffset": "0x20000",
            "appBytes": 3_637_200,
            "appSha256": "3" * 64,
            "evidenceManifestSha256": "4" * 64,
        },
        "course": {
            "courseId": "a17792f6-8d86-4ad1-a6f3-77663b4d4674",
            "courseKey": "english-6month-4-6",
        },
        "curriculum": {"lessonCount": 26, "activityCount": 256},
        "tools": {"pythonTestRuntime": {"version": 1}},
    }
    _write_json(candidate_path, candidate)
    _write_json(
        evidence / "00-candidate-validator.json",
        {"reasons": [], "schemaVersion": 1, "status": "pass", "validator": "course-mode-candidate.v1"},
    )
    attestation_path = evidence / "00-operator-attestation.json"
    _write_json(
        attestation_path,
        {
            "candidateId": candidate_id,
            "effectiveUid": os.getuid(),
            "gateSha": admin_sha,
            "sameUidThreatModel": "malicious-process-excluded",
            "schemaVersion": 1,
            "trustedOperatorAccountConfirmed": True,
            "untrustedAutomationStoppedConfirmed": True,
        },
    )
    attestation_sha = hashlib.sha256(attestation_path.read_bytes()).hexdigest()
    _write_json(
        evidence / "02-runtime-assignment-new-rollback.json",
        {
            "candidateId": candidate_id,
            "failedLane": None,
            "lanes": _lanes(["admin-course-mode-assignment-new", "admin-course-mode-assignment-rollback"]),
            "verdict": "PASS",
        },
    )
    _write_json(
        evidence / "02-runtime-browser-after-assignment.json",
        {
            "candidateId": candidate_id,
            "failedLane": None,
            "lanes": _lanes(
                [
                    "admin-course-mode-playwright-chromium-desktop",
                    "admin-course-mode-playwright-webkit-desktop",
                ]
            ),
            "verdict": "PASS",
        },
    )
    _write_json(
        evidence / "02-runtime-continuity-inspection.json",
        {
            "allMountsCanonical": True,
            "allMountsExist": True,
            "allMountsReadOnly": True,
            "assignmentFlags": {"new": False, "rollback": False},
            "imagesMatchCandidate": True,
            "manualRecreateAfterRollback": False,
            "mountCount": 4,
            "status": "pass",
        },
    )
    for name, lanes in (
        ("03-quick-gate.json", QUICK_LANES),
        ("04-full-gate.json", FULL_LANES),
        ("05-live-db-gate.json", FULL_LANES + ["live-postgres"]),
    ):
        _write_json(
            evidence / name,
            {
                "candidateId": candidate_id,
                "failedLane": None,
                "lanes": _lanes(lanes),
                "operatorAttestationSha256": attestation_sha,
                "verdict": "PASS",
            },
        )
    return candidate_path, evidence, output


def _run(candidate: Path, evidence: Path, output: Path, *preserved: Path) -> subprocess.CompletedProcess[str]:
    arguments = ["--candidate", str(candidate), "--evidence-root", str(evidence)]
    for root in preserved:
        arguments.extend(["--preserved-root", str(root)])
    arguments.extend(["--output", str(output)])
    public_key = os.environ.get("_TBOT_TEST_ADMISSION_PUBLIC_KEY_HEX")
    fingerprint = os.environ.get("_TBOT_TEST_ADMISSION_KEY_FINGERPRINT")
    if public_key is None or fingerprint is None:
        command = [sys.executable, str(SCRIPT), *arguments]
    else:
        bootstrap = (
            "import runpy,sys;"
            "sys.path.insert(0,sys.argv[1]);"
            "import course_mode_physical_flash_admission as admission;"
            "admission.PINNED_APPROVAL_PUBLIC_KEY_RAW=bytes.fromhex(sys.argv[2]);"
            "admission.PINNED_APPROVAL_KEY_FINGERPRINT=sys.argv[3];"
            "script=sys.argv[4];sys.argv=[script,*sys.argv[5:]];"
            "runpy.run_path(script,run_name='__main__')"
        )
        command = [
            sys.executable,
            "-c",
            bootstrap,
            str(SCRIPT.parent),
            public_key,
            fingerprint,
            str(SCRIPT),
            *arguments,
        ]
    return subprocess.run(command, check=False, capture_output=True, text=True)


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _rewrite_json(path: Path, value: object) -> None:
    path.chmod(0o644)
    path.write_bytes(_canonical_json_bytes(value) + b"\n")
    path.chmod(0o444)


def _write_exact_admission_documents(
    *,
    candidate_path: Path,
    evidence: Path,
    private_key: Ed25519PrivateKey,
    signer_fingerprint: str,
) -> dict[str, object]:
    admission_root = evidence / "G7-admission"
    admission_root.mkdir()
    paths = {
        "input": admission_root / "admission-input.json",
        "output": admission_root / "admission-result.json",
        "expectedIdentity": admission_root / "expected-identity.json",
        "expectedIdentitySignature": admission_root / "expected-identity.sig",
    }
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    candidate.update(
        createdAt="2026-09-05T07:00:00Z",
        expiresAt="2026-09-05T09:00:00Z",
    )
    candidate["repositories"] = {
        "adminEsp": {
            "path": "/src/admin",
            "sha": candidate["repositories"]["adminEsp"]["sha"],
            "branch": "main",
            "remoteUrl": "git@example/admin.git",
            "dirtyExceptions": [],
        },
        "backend": {
            "path": "/src/backend",
            "sha": candidate["repositories"]["backend"]["sha"],
            "branch": "main",
            "remoteUrl": "git@example/backend.git",
            "dirtyExceptions": [],
        },
        "firmware": {
            "path": "/src/firmware",
            "sha": admission.FIRMWARE_SHA,
            "branch": "main",
            "remoteUrl": "git@example/firmware.git",
            "dirtyExceptions": [],
        },
    }
    candidate["firmware"].update(
        appPath="/opt/tbot/course-mode/app.bin",
        appOffset=admission.APP_OFFSET,
        appBytes=admission.APP_BYTES,
        appSha256=admission.APP_SHA256,
        partitionBytes=admission.PARTITION_BYTES,
        evidenceManifestPath="/opt/tbot/course-mode/manifest.json",
        evidenceManifestSha256=admission.MANIFEST_SHA256,
    )
    candidate["tools"]["physicalAdmission"] = {name: str(path) for name, path in paths.items()}
    _rewrite_json(candidate_path, candidate)

    repository_binding = {
        "admin": copy.deepcopy(candidate["repositories"]["adminEsp"]),
        "backend": copy.deepcopy(candidate["repositories"]["backend"]),
        "firmware": copy.deepcopy(candidate["repositories"]["firmware"]),
    }
    image_binding = {
        "backend": {
            **candidate["images"]["lessonStudioBackend"],
            "platform": "linux/arm64",
            "provenanceLabels": {
                "org.opencontainers.image.revision": repository_binding["backend"]["sha"],
                "org.opencontainers.image.source": repository_binding["backend"]["remoteUrl"],
            },
        },
        "web": {
            **candidate["images"]["lessonStudioWeb"],
            "platform": "linux/arm64",
            "provenanceLabels": {
                "org.opencontainers.image.revision": repository_binding["admin"]["sha"],
                "org.opencontainers.image.source": repository_binding["admin"]["remoteUrl"],
            },
        },
    }
    candidate_binding = {
        "candidateId": candidate["candidateId"],
        "courseId": candidate["course"]["courseId"],
        "courseKey": candidate["course"]["courseKey"],
        "createdAt": candidate["createdAt"],
        "expiresAt": candidate["expiresAt"],
        "path": str(candidate_path),
        "sha256": hashlib.sha256(candidate_path.read_bytes()).hexdigest(),
        "repositories": repository_binding,
        "images": image_binding,
        "firmware": {
            "board": admission.BOARD,
            "target": admission.TARGET,
            "gitSha": admission.FIRMWARE_SHA,
            "app": {
                "path": candidate["firmware"]["appPath"],
                "sha256": admission.APP_SHA256,
                "bytes": admission.APP_BYTES,
                "offset": admission.APP_OFFSET,
                "partitionBytes": admission.PARTITION_BYTES,
            },
            "manifest": {
                "path": candidate["firmware"]["evidenceManifestPath"],
                "sha256": admission.MANIFEST_SHA256,
            },
        },
    }
    session_id = "cab43f0d-62dc-49c4-9d30-e9630d195a44"
    robot = {
        "mac": admission.ROBOT_MAC,
        "board": admission.BOARD,
        "target": admission.TARGET,
        "serialPath": admission.SERIAL_PATH,
        "exactlyOneRobot": True,
    }
    identity = {
        "schemaVersion": 1,
        "sessionId": session_id,
        "candidate": copy.deepcopy(candidate_binding),
        "partitionTable": copy.deepcopy(admission.EXPECTED_PARTITIONS),
        "robot": copy.deepcopy(robot),
        "signer": {"algorithm": "ed25519", "fingerprint": signer_fingerprint},
    }
    input_document = {
        "schemaVersion": 1,
        "sessionId": session_id,
        "checkedAt": "2026-09-05T08:00:00Z",
        "candidate": copy.deepcopy(candidate_binding),
        "robot": copy.deepcopy(robot),
        "serialLease": {
            "soleLeaseConfirmed": True,
            "competingProcessesStopped": True,
            "devicePath": admission.SERIAL_PATH,
            "discoveredDevices": [admission.SERIAL_PATH],
            "holderPids": [],
            "inventoryMethod": "lstat-glob-lsof-v1",
        },
        "flashPlan": {
            "operation": {
                "operation": "write_flash",
                "offset": admission.APP_OFFSET,
                "imageSha256": admission.APP_SHA256,
                "imageBytes": admission.APP_BYTES,
                "after": "no-reset",
                "eraseChip": False,
                "mergedImage": False,
            },
            "protectedPartitions": [
                copy.deepcopy(partition)
                for partition in admission.EXPECTED_PARTITIONS
                if partition["protected"]
            ],
            "preserveProtectedPartitions": True,
        },
        "safety": {name: True for name in admission.SAFETY_KEYS},
    }
    assert set(input_document) == admission.TOP_KEYS
    assert set(identity) == admission.IDENTITY_KEYS
    checked_at = datetime.fromisoformat(input_document["checkedAt"].replace("Z", "+00:00"))
    assert checked_at.tzinfo == timezone.utc
    assert admission.validate_documents(
        input_document,
        identity,
        candidate,
        checked_at,
        [admission.SERIAL_PATH],
        [],
        None,
    ) == []
    _write_json(paths["input"], input_document)
    _write_json(paths["expectedIdentity"], identity)
    signature = private_key.sign(_canonical_json_bytes(identity))
    assert len(signature) == 64
    paths["expectedIdentitySignature"].write_bytes(signature)
    paths["expectedIdentitySignature"].chmod(0o444)
    return {
        "input": input_document,
        "identity": identity,
        "paths": paths,
        "privateKey": private_key,
    }


def _install_signed_admission(
    candidate_path: Path,
    evidence: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, object]:
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    fingerprint = hashlib.sha256(public).hexdigest()
    monkeypatch.setattr(admission, "PINNED_APPROVAL_PUBLIC_KEY_RAW", public)
    monkeypatch.setattr(admission, "PINNED_APPROVAL_KEY_FINGERPRINT", fingerprint)
    monkeypatch.setenv("_TBOT_TEST_ADMISSION_PUBLIC_KEY_HEX", public.hex())
    monkeypatch.setenv("_TBOT_TEST_ADMISSION_KEY_FINGERPRINT", fingerprint)
    return _write_exact_admission_documents(
        candidate_path=candidate_path,
        evidence=evidence,
        private_key=key,
        signer_fingerprint=fingerprint,
    )


def _rewrite_and_resign_admission(candidate_path: Path, bundle: dict[str, object]) -> None:
    assert candidate_path.is_file()
    paths = bundle["paths"]
    assert isinstance(paths, dict)
    input_document = bundle["input"]
    identity = bundle["identity"]
    private_key = bundle["privateKey"]
    assert isinstance(private_key, Ed25519PrivateKey)
    _rewrite_json(paths["input"], input_document)
    _rewrite_json(paths["expectedIdentity"], identity)
    signature_path = paths["expectedIdentitySignature"]
    signature_path.chmod(0o644)
    signature_path.write_bytes(private_key.sign(_canonical_json_bytes(identity)))
    signature_path.chmod(0o444)


def test_candidate_bound_signed_admission_session_id_is_public_evidence(
    evidence_fixture: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate, evidence, output = evidence_fixture
    _install_signed_admission(candidate, evidence, monkeypatch)

    result = _run(candidate, evidence, output)
    report = json.loads(result.stdout)

    assert result.returncode == 0
    assert report["status"] == "pass"
    assert report["checks"]["secretScan"] is True
    assert "content.secret" not in report["findings"]


def test_public_admission_exemption_requires_exact_candidate_bytes(
    evidence_fixture: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate, evidence, output = evidence_fixture
    _install_signed_admission(candidate, evidence, monkeypatch)
    candidate_document = json.loads(candidate.read_text(encoding="utf-8"))
    candidate_document["tools"]["pythonTestRuntime"]["version"] = 2
    _rewrite_json(candidate, candidate_document)

    result = _run(candidate, evidence, output)
    report = json.loads(result.stdout)

    assert result.returncode == 1
    assert report["checks"]["secretScan"] is False
    assert "content.secret" in report["findings"]


def test_public_admission_exemption_rejects_candidate_replaced_during_audit(
    evidence_fixture: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate, evidence, output = evidence_fixture
    bundle = _install_signed_admission(candidate, evidence, monkeypatch)
    paths = bundle["paths"]
    assert isinstance(paths, dict)
    input_path = paths["input"]
    secure_read = auditor._read_secure_file
    candidate_replaced = False

    def replace_candidate_on_admission_read(path: Path) -> bytes:
        nonlocal candidate_replaced
        if path == input_path and not candidate_replaced:
            candidate_document = json.loads(candidate.read_text(encoding="utf-8"))
            candidate_document["tools"]["pythonTestRuntime"]["token"] = "replacement-secret"
            _rewrite_json(candidate, candidate_document)
            candidate_replaced = True
        return secure_read(path)

    monkeypatch.setattr(auditor, "_read_secure_file", replace_candidate_on_admission_read)

    report = auditor.audit(candidate, evidence, [], output)

    assert candidate_replaced is True
    assert report["status"] == "fail"
    assert report["checks"]["secretScan"] is False
    assert {
        "candidate.metadata_or_json",
        "content.secret",
    }.intersection(report["findings"])


def test_pass_report_rejects_candidate_parent_symlink_alias_before_publication(
    evidence_fixture: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    original_candidate, evidence, output = evidence_fixture
    candidate_dir = original_candidate.parent / "candidate-dir"
    candidate_dir.mkdir()
    candidate = candidate_dir / original_candidate.name
    original_candidate.rename(candidate)
    _install_signed_admission(candidate, evidence, monkeypatch)
    candidate_real = candidate_dir.with_name("candidate-real")
    audit = auditor.audit

    def alias_candidate_parent(*args, **kwargs):
        report = audit(*args, **kwargs)
        candidate_dir.rename(candidate_real)
        candidate_dir.symlink_to(candidate_real, target_is_directory=True)
        return report

    monkeypatch.setattr(auditor, "audit", alias_candidate_parent)

    result = auditor.main(
        [
            "--candidate",
            str(candidate),
            "--evidence-root",
            str(evidence),
            "--output",
            str(output),
        ]
    )
    report = json.loads(capsys.readouterr().out)

    assert result == 1
    assert report["status"] == "fail"
    assert "candidate.metadata_or_json" in report["findings"]
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "fail"


def test_pass_report_rejects_output_parent_symlink_redirection(
    evidence_fixture: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    candidate, evidence, output = evidence_fixture
    evidence_real = evidence.with_name("evidence-real")
    foreign = evidence.with_name("foreign")
    foreign.mkdir()
    audit = auditor.audit

    def redirect_output_parent(*args, **kwargs):
        report = audit(*args, **kwargs)
        evidence.rename(evidence_real)
        evidence.symlink_to(foreign, target_is_directory=True)
        return report

    monkeypatch.setattr(auditor, "audit", redirect_output_parent)

    result = auditor.main(
        [
            "--candidate",
            str(candidate),
            "--evidence-root",
            str(evidence),
            "--output",
            str(output),
        ]
    )
    report = json.loads(capsys.readouterr().out)

    assert result == 1
    assert report["status"] == "fail"
    assert "output.write" in report["findings"]
    assert not (foreign / output.name).exists()


def test_pass_report_rejects_missing_output_parent_snapshot(
    evidence_fixture: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    candidate, evidence, output = evidence_fixture
    monkeypatch.setattr(
        auditor,
        "_directory_snapshot",
        lambda _path: (_ for _ in ()).throw(OSError("transient snapshot failure")),
    )

    result = auditor.main(
        [
            "--candidate",
            str(candidate),
            "--evidence-root",
            str(evidence),
            "--output",
            str(output),
        ]
    )
    report = json.loads(capsys.readouterr().out)

    assert result == 1
    assert report["status"] == "fail"
    assert "output.write" in report["findings"]
    assert not output.exists()


@pytest.mark.parametrize("safe_calls", [1, 2])
def test_candidate_binding_failure_replaces_existing_pass_with_fail_report(
    evidence_fixture: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    safe_calls: int,
) -> None:
    candidate, evidence, output = evidence_fixture
    _write_json(output, {"status": "pass"})
    calls = 0

    def candidate_still_bound(_report) -> bool:
        nonlocal calls
        calls += 1
        return calls <= safe_calls

    monkeypatch.setattr(auditor._AuditReport, "candidate_still_bound", candidate_still_bound)

    result = auditor.main(
        [
            "--candidate",
            str(candidate),
            "--evidence-root",
            str(evidence),
            "--output",
            str(output),
        ]
    )
    report = json.loads(capsys.readouterr().out)

    assert result == 1
    assert report["status"] == "fail"
    assert "candidate.metadata_or_json" in report["findings"]
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "fail"


def test_temp_fsync_failure_replaces_existing_pass_with_fail_report(
    evidence_fixture: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    candidate, evidence, output = evidence_fixture
    _write_json(output, {"status": "pass"})
    fsync = auditor.os.fsync
    failed = False

    def fail_first_regular_fsync(descriptor: int) -> None:
        nonlocal failed
        if stat.S_ISREG(auditor.os.fstat(descriptor).st_mode) and not failed:
            failed = True
            raise OSError("temporary fsync failure")
        fsync(descriptor)

    monkeypatch.setattr(auditor.os, "fsync", fail_first_regular_fsync)

    result = auditor.main(
        [
            "--candidate",
            str(candidate),
            "--evidence-root",
            str(evidence),
            "--output",
            str(output),
        ]
    )
    report = json.loads(capsys.readouterr().out)

    assert result == 1
    assert failed is True
    assert report["status"] == "fail"
    assert "output.write" in report["findings"]
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "fail"


def test_temp_write_failure_replaces_existing_pass_with_fail_report(
    evidence_fixture: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    candidate, evidence, output = evidence_fixture
    _write_json(output, {"status": "pass"})
    write = auditor.os.write
    failed = False

    def fail_first_write(descriptor: int, data) -> int:
        nonlocal failed
        if not failed:
            failed = True
            raise OSError("temporary write failure")
        return write(descriptor, data)

    monkeypatch.setattr(auditor.os, "write", fail_first_write)

    result = auditor.main(
        [
            "--candidate",
            str(candidate),
            "--evidence-root",
            str(evidence),
            "--output",
            str(output),
        ]
    )
    report = json.loads(capsys.readouterr().out)

    assert result == 1
    assert failed is True
    assert report["status"] == "fail"
    assert "output.write" in report["findings"]
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "fail"


def test_output_parent_redirection_invalidates_existing_bound_pass(
    evidence_fixture: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    candidate, evidence, output = evidence_fixture
    _write_json(output, {"status": "pass"})
    evidence_real = evidence.with_name("evidence-real")
    foreign = evidence.with_name("foreign")
    foreign.mkdir()
    audit = auditor.audit

    def redirect_output_parent(*args, **kwargs):
        report = audit(*args, **kwargs)
        evidence.rename(evidence_real)
        evidence.symlink_to(foreign, target_is_directory=True)
        return report

    monkeypatch.setattr(auditor, "audit", redirect_output_parent)

    result = auditor.main(
        [
            "--candidate",
            str(candidate),
            "--evidence-root",
            str(evidence),
            "--output",
            str(output),
        ]
    )
    report = json.loads(capsys.readouterr().out)
    bound_output = evidence_real / output.name

    assert result == 1
    assert report["status"] == "fail"
    assert "output.write" in report["findings"]
    assert json.loads(bound_output.read_text(encoding="utf-8"))["status"] == "fail"
    assert not (foreign / output.name).exists()


def test_failure_report_publication_preserves_output_replacement(
    evidence_fixture: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    candidate, evidence, output = evidence_fixture
    _write_json(output, {"status": "pass"})
    replacement = b"unrelated-output"
    publish_failure = auditor._OutputBinding.publish_failure
    calls = 0

    def candidate_still_bound(_report) -> bool:
        nonlocal calls
        calls += 1
        return calls == 1

    def replace_before_failure_publication(self, report) -> bool:
        output.unlink()
        output.write_bytes(replacement)
        output.chmod(0o444)
        return publish_failure(self, report)

    monkeypatch.setattr(auditor._AuditReport, "candidate_still_bound", candidate_still_bound)
    monkeypatch.setattr(auditor._OutputBinding, "publish_failure", replace_before_failure_publication)

    result = auditor.main(
        [
            "--candidate",
            str(candidate),
            "--evidence-root",
            str(evidence),
            "--output",
            str(output),
        ]
    )
    report = json.loads(capsys.readouterr().out)

    assert result == 1
    assert report["status"] == "fail"
    assert output.read_bytes() == replacement


def test_skipped_unsafe_output_invalidates_captured_existing_pass(
    evidence_fixture: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    candidate, evidence, output = evidence_fixture
    _write_json(output, {"status": "pass"})
    monkeypatch.setattr(auditor, "_output_path_secure", lambda _path: False)

    result = auditor.main(
        [
            "--candidate",
            str(candidate),
            "--evidence-root",
            str(evidence),
            "--output",
            str(output),
        ]
    )
    report = json.loads(capsys.readouterr().out)

    assert result == 1
    assert report["status"] == "fail"
    assert "output.unsafe" in report["findings"]
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "fail"


def test_same_inode_output_metadata_drift_does_not_preserve_stale_pass(
    evidence_fixture: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    candidate, evidence, output = evidence_fixture
    _write_json(output, {"status": "pass"})
    calls = 0

    def drift_then_fail(_report) -> bool:
        nonlocal calls
        calls += 1
        if calls == 2:
            output.chmod(0o644)
            return False
        return calls == 1

    monkeypatch.setattr(auditor._AuditReport, "candidate_still_bound", drift_then_fail)

    result = auditor.main(
        [
            "--candidate",
            str(candidate),
            "--evidence-root",
            str(evidence),
            "--output",
            str(output),
        ]
    )
    report = json.loads(capsys.readouterr().out)

    assert result == 1
    assert report["status"] == "fail"
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "fail"
    assert output.stat().st_mode & 0o777 == 0o444


def test_late_output_binding_failure_does_not_unlink_replacement(
    evidence_fixture: tuple[Path, Path, Path]
) -> None:
    _, _, output = evidence_fixture
    calls = 0
    replacement = b"unrelated-output"

    def replace_after_publish() -> bool:
        nonlocal calls
        calls += 1
        if calls == 3:
            output.unlink()
            output.write_bytes(replacement)
            output.chmod(0o444)
            return False
        return True

    assert auditor._write_output(output, {"status": "pass"}, replace_after_publish) is False
    assert calls == 3
    assert output.read_bytes() == replacement


def test_late_output_binding_failure_never_unlinks_by_path(
    evidence_fixture: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, output = evidence_fixture
    calls = 0
    unlinked: list[str] = []
    unlink = auditor.os.unlink

    def fail_after_publish() -> bool:
        nonlocal calls
        calls += 1
        return calls < 3

    def record_unlink(path, *args, **kwargs) -> None:
        unlinked.append(str(path))
        unlink(path, *args, **kwargs)

    monkeypatch.setattr(auditor.os, "unlink", record_unlink)

    assert auditor._write_output(output, {"status": "pass"}, fail_after_publish) is False
    assert calls == 3
    assert unlinked == []
    assert output.read_bytes() == b""


def test_output_replaced_during_publication_is_rejected_without_unlinking_replacement(
    evidence_fixture: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, output = evidence_fixture
    replacement = b"unrelated-output"
    replace = auditor.os.replace

    def replace_then_swap(*args, **kwargs) -> None:
        replace(*args, **kwargs)
        output.unlink()
        output.write_bytes(replacement)
        output.chmod(0o444)

    monkeypatch.setattr(auditor.os, "replace", replace_then_swap)

    assert auditor._write_output(output, {"status": "pass"}) is False
    assert output.read_bytes() == replacement


def test_final_parent_check_output_swap_is_rejected_without_touching_replacement(
    evidence_fixture: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, output = evidence_fixture
    replacement = b"unrelated-output"
    directory_still_bound = auditor._directory_still_bound
    checks = 0

    def swap_during_final_parent_check(*args, **kwargs) -> bool:
        nonlocal checks
        checks += 1
        result = directory_still_bound(*args, **kwargs)
        if checks == 3:
            output.unlink()
            output.write_bytes(replacement)
            output.chmod(0o444)
        return result

    monkeypatch.setattr(auditor, "_directory_still_bound", swap_during_final_parent_check)

    assert auditor._write_output(output, {"status": "pass"}) is False
    assert checks == 3
    assert output.read_bytes() == replacement


def test_ftruncate_failure_still_invalidates_published_pass(
    evidence_fixture: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, output = evidence_fixture
    calls = 0

    def fail_after_publish() -> bool:
        nonlocal calls
        calls += 1
        return calls < 3

    monkeypatch.setattr(
        auditor.os,
        "ftruncate",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("truncate failure")),
    )

    assert auditor._write_output(output, {"status": "pass"}, fail_after_publish) is False
    assert calls == 3
    with pytest.raises((json.JSONDecodeError, UnicodeDecodeError)):
        json.loads(output.read_text(encoding="utf-8"))


def test_parent_fsync_failure_after_publication_invalidates_published_output(
    evidence_fixture: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, output = evidence_fixture
    fsync = auditor.os.fsync
    failed = False

    def fail_first_directory_fsync(descriptor: int) -> None:
        nonlocal failed
        if stat.S_ISDIR(auditor.os.fstat(descriptor).st_mode) and not failed:
            failed = True
            raise OSError("directory fsync failure")
        fsync(descriptor)

    monkeypatch.setattr(auditor.os, "fsync", fail_first_directory_fsync)

    assert auditor._write_output(output, {"status": "pass"}) is False
    assert failed is True
    assert output.read_bytes() == b""


def test_post_publication_stat_failure_invalidates_published_output(
    evidence_fixture: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, output = evidence_fixture
    os_stat = auditor.os.stat
    failed = False

    def fail_first_output_stat(path, *args, **kwargs):
        nonlocal failed
        if path == output.name and kwargs.get("dir_fd") is not None and not failed:
            failed = True
            raise OSError("post-publication stat failure")
        return os_stat(path, *args, **kwargs)

    monkeypatch.setattr(auditor.os, "stat", fail_first_output_stat)

    assert auditor._write_output(output, {"status": "pass"}) is False
    assert failed is True
    assert output.read_bytes() == b""


def test_public_admission_exemption_rejects_relocated_input_descriptor(
    evidence_fixture: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate, evidence, output = evidence_fixture
    bundle = _install_signed_admission(candidate, evidence, monkeypatch)
    paths = bundle["paths"]
    assert isinstance(paths, dict)
    relocated_input = paths["input"].with_name("relocated-admission-input.json")
    paths["input"].rename(relocated_input)
    candidate_document = json.loads(candidate.read_text(encoding="utf-8"))
    candidate_document["tools"]["physicalAdmission"]["input"] = str(relocated_input)
    _rewrite_json(candidate, candidate_document)

    result = _run(candidate, evidence, output)
    report = json.loads(result.stdout)

    assert result.returncode == 1
    assert report["checks"]["secretScan"] is False
    assert "content.secret" in report["findings"]


def test_public_admission_exemption_rejects_secret_in_signed_candidate_binding(
    evidence_fixture: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate, evidence, output = evidence_fixture
    bundle = _install_signed_admission(candidate, evidence, monkeypatch)
    encoded = base64.b64encode(b"Authorization: Bearer admission-secret-token").decode("ascii")
    split_encoded = "\n".join(encoded[index : index + 8] for index in range(0, len(encoded), 8))
    bundle["input"]["candidate"]["sha256"] = split_encoded
    bundle["identity"]["candidate"]["sha256"] = split_encoded
    _rewrite_and_resign_admission(candidate, bundle)

    result = _run(candidate, evidence, output)
    report = json.loads(result.stdout)

    assert result.returncode == 1
    assert report["checks"]["secretScan"] is False
    assert "content.secret" in report["findings"]


@pytest.mark.parametrize(
    ("semantic_value", "expected_finding"),
    [
        (
            "\n".join(
                encoded[index : index + 8]
                for index in range(0, len(encoded), 8)
            ),
            "content.secret",
        )
        for encoded in [
            base64.b64encode(b"Authorization: Bearer admission-secret-token").decode("ascii")
        ]
    ]
    + [
        (
            "-----BEGIN PRIVATE KEY-----\n"
            "YWRtaXNzaW9uLXByaXZhdGUta2V5\n"
            "-----END PRIVATE KEY-----",
            "content.private_key",
        )
    ],
    ids=["newline-split-base64-bearer", "multiline-private-key"],
)
def test_public_admission_semantic_strings_are_scanned(
    evidence_fixture: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    semantic_value: str,
    expected_finding: str,
) -> None:
    candidate, evidence, output = evidence_fixture
    bundle = _install_signed_admission(candidate, evidence, monkeypatch)
    candidate_document = json.loads(candidate.read_text(encoding="utf-8"))
    candidate_document["repositories"]["adminEsp"]["remoteUrl"] = semantic_value
    _rewrite_json(candidate, candidate_document)
    candidate_sha = hashlib.sha256(candidate.read_bytes()).hexdigest()
    for document in (bundle["input"], bundle["identity"]):
        binding = document["candidate"]
        binding["repositories"]["admin"]["remoteUrl"] = semantic_value
        binding["images"]["web"]["provenanceLabels"][
            "org.opencontainers.image.source"
        ] = semantic_value
        binding["sha256"] = candidate_sha
    checked_at = datetime.fromisoformat(
        bundle["input"]["checkedAt"].replace("Z", "+00:00")
    )
    assert admission.validate_documents(
        bundle["input"],
        bundle["identity"],
        candidate_document,
        checked_at,
        [admission.SERIAL_PATH],
        [],
        None,
    ) == []
    _rewrite_and_resign_admission(candidate, bundle)

    result = _run(candidate, evidence, output)
    report = json.loads(result.stdout)

    assert result.returncode == 1
    assert report["status"] == "fail"
    assert expected_finding in report["findings"]


def test_public_admission_exemption_is_bound_to_scanned_bytes(
    evidence_fixture: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate, evidence, output = evidence_fixture
    bundle = _install_signed_admission(candidate, evidence, monkeypatch)
    paths = bundle["paths"]
    assert isinstance(paths, dict)
    input_path = paths["input"]
    replacement = copy.deepcopy(bundle["input"])
    replacement["sessionId"] = "3fc6471b-4e35-4ce7-8b20-b741c3e8e9e0"
    secure_read = auditor._read_secure_file
    input_reads = 0

    def replace_before_scan(path: Path) -> bytes:
        nonlocal input_reads
        if path == input_path:
            input_reads += 1
            if input_reads == 2:
                _rewrite_json(input_path, replacement)
        return secure_read(path)

    monkeypatch.setattr(auditor, "_read_secure_file", replace_before_scan)

    report = auditor.audit(candidate, evidence, [], output)

    assert input_reads == 2
    assert report["status"] == "fail"
    assert report["checks"]["secretScan"] is False
    assert "content.secret" in report["findings"]


def test_public_admission_exemption_is_bound_to_scanned_signature(
    evidence_fixture: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate, evidence, output = evidence_fixture
    bundle = _install_signed_admission(candidate, evidence, monkeypatch)
    paths = bundle["paths"]
    assert isinstance(paths, dict)
    signature_path = paths["expectedIdentitySignature"]
    secure_read = auditor._read_secure_file
    signature_reads = 0

    def replace_before_scan(path: Path) -> bytes:
        nonlocal signature_reads
        if path == signature_path:
            signature_reads += 1
            if signature_reads == 2:
                signature_path.chmod(0o644)
                signature_path.write_bytes(b"0" * 64)
                signature_path.chmod(0o444)
        return secure_read(path)

    monkeypatch.setattr(auditor, "_read_secure_file", replace_before_scan)

    report = auditor.audit(candidate, evidence, [], output)

    assert signature_reads >= 2
    assert report["status"] == "fail"
    assert report["checks"]["secretScan"] is False
    assert "content.secret" in report["findings"]


def test_public_admission_exemption_rejects_signature_changed_on_evidence_walk(
    evidence_fixture: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate, evidence, output = evidence_fixture
    bundle = _install_signed_admission(candidate, evidence, monkeypatch)
    paths = bundle["paths"]
    assert isinstance(paths, dict)
    input_path = paths["input"]
    identity_path = paths["expectedIdentity"]
    signature_path = paths["expectedIdentitySignature"]
    secure_read = auditor._read_secure_file
    iter_root_entries = auditor._iter_root_entries
    walked_paths: set[Path] = set()
    reading_walk_signature = False
    signature_replaced = False

    def track_evidence_walk(root: Path):
        nonlocal reading_walk_signature
        entries = list(iter_root_entries(root))
        entries.sort(key=lambda entry: entry[0] == signature_path)
        for path, metadata in entries:
            walked_paths.add(path)
            reading_walk_signature = path == signature_path
            yield path, metadata
        reading_walk_signature = False

    def replace_on_evidence_walk(path: Path) -> bytes:
        nonlocal signature_replaced
        if path == signature_path and reading_walk_signature:
            signature_path.chmod(0o644)
            signature_path.write_bytes(b"0" * 64)
            signature_path.chmod(0o444)
            signature_replaced = True
        return secure_read(path)

    monkeypatch.setattr(auditor, "_iter_root_entries", track_evidence_walk)
    monkeypatch.setattr(auditor, "_read_secure_file", replace_on_evidence_walk)

    report = auditor.audit(candidate, evidence, [], output)

    assert input_path in walked_paths
    assert identity_path in walked_paths
    assert signature_replaced is True
    assert report["status"] == "fail"
    assert report["checks"]["secretScan"] is False
    assert "content.secret" in report["findings"]


@pytest.mark.parametrize(
    "removed_fields",
    [
        ("input", "expectedIdentity"),
        ("input", "expectedIdentity", "expectedIdentitySignature"),
    ],
    ids=["session-documents", "complete-bundle"],
)
def test_public_admission_exemption_rejects_bundle_removed_before_evidence_walk(
    evidence_fixture: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    removed_fields: tuple[str, ...],
) -> None:
    candidate, evidence, output = evidence_fixture
    bundle = _install_signed_admission(candidate, evidence, monkeypatch)
    paths = bundle["paths"]
    assert isinstance(paths, dict)
    iter_root_entries = auditor._iter_root_entries
    removed = False

    def remove_before_walk(root: Path):
        nonlocal removed
        if root == evidence and not removed:
            for field in removed_fields:
                paths[field].unlink()
            removed = True
        yield from iter_root_entries(root)

    monkeypatch.setattr(auditor, "_iter_root_entries", remove_before_walk)

    report = auditor.audit(candidate, evidence, [], output)

    assert removed is True
    assert report["status"] == "fail"
    assert report["checks"]["secretScan"] is False
    assert "content.secret" in report["findings"]


def test_declared_public_admission_missing_before_audit_fails_closed(
    evidence_fixture: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate, evidence, output = evidence_fixture
    bundle = _install_signed_admission(candidate, evidence, monkeypatch)
    paths = bundle["paths"]
    assert isinstance(paths, dict)
    for field in ("input", "expectedIdentity", "expectedIdentitySignature"):
        paths[field].unlink()

    report = auditor.audit(candidate, evidence, [], output)

    assert report["status"] == "fail"
    assert report["checks"]["secretScan"] is False
    assert "content.secret" in report["findings"]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda bundle: bundle["input"].update(password="not-public"),
        lambda bundle: bundle["identity"].update(token="not-public"),
        lambda bundle: bundle["input"].__setitem__("sessionId", str(uuid.uuid4())),
        lambda bundle: bundle["identity"].__setitem__("sessionId", "not-a-uuid"),
        lambda bundle: bundle["identity"].update(extraSession="not-public"),
    ],
    ids=[
        "input-real-secret",
        "identity-real-secret",
        "mismatched-session",
        "malformed-session",
        "extra-session-field",
    ],
)
def test_public_admission_exemption_fails_closed(
    evidence_fixture: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    mutation,
) -> None:
    candidate, evidence, output = evidence_fixture
    bundle = _install_signed_admission(candidate, evidence, monkeypatch)
    mutation(bundle)
    _rewrite_and_resign_admission(candidate, bundle)

    result = _run(candidate, evidence, output)
    report = json.loads(result.stdout)

    assert result.returncode == 1
    assert report["checks"]["secretScan"] is False
    assert "content.secret" in report["findings"]


def test_public_admission_exemption_does_not_cover_unrelated_session_id_evidence(
    evidence_fixture: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate, evidence, output = evidence_fixture
    _install_signed_admission(candidate, evidence, monkeypatch)
    _write_json(
        evidence / "unrelated-session.json",
        {"sessionId": "cab43f0d-62dc-49c4-9d30-e9630d195a44"},
    )

    result = _run(candidate, evidence, output)
    report = json.loads(result.stdout)

    assert result.returncode == 1
    assert report["checks"]["secretScan"] is False
    assert "content.secret" in report["findings"]


def test_public_admission_exemption_rejects_filename_only_impersonation(
    evidence_fixture: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate, evidence, output = evidence_fixture
    bundle = _install_signed_admission(candidate, evidence, monkeypatch)
    candidate_document = json.loads(candidate.read_text(encoding="utf-8"))
    candidate_document["tools"].pop("physicalAdmission")
    _rewrite_json(candidate, candidate_document)
    paths = bundle["paths"]
    assert isinstance(paths, dict)
    paths["expectedIdentity"].unlink()
    paths["expectedIdentitySignature"].unlink()

    result = _run(candidate, evidence, output)
    report = json.loads(result.stdout)

    assert result.returncode == 1
    assert report["checks"]["secretScan"] is False
    assert "content.secret" in report["findings"]


def _zip(entries: dict[str, bytes], compression: int = zipfile.ZIP_DEFLATED) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=compression) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def _mark_zip_encrypted(payload: bytes) -> bytes:
    changed = bytearray(payload)
    local = changed.find(b"PK\x03\x04")
    central = changed.find(b"PK\x01\x02")
    assert local >= 0 and central >= 0
    changed[local + 6 : local + 8] = (1).to_bytes(2, "little")
    changed[central + 8 : central + 10] = (1).to_bytes(2, "little")
    return bytes(changed)


def test_cli_accepts_clean_bound_software_evidence_deterministically(
    evidence_fixture: tuple[Path, Path, Path],
) -> None:
    candidate, evidence, output = evidence_fixture

    first = _run(candidate, evidence, output)
    first_bytes = output.read_bytes()
    second = _run(candidate, evidence, output)

    assert first.returncode == second.returncode == 0
    assert output.read_bytes() == first_bytes
    report = json.loads(first.stdout)
    assert report == json.loads(first_bytes)
    assert report["schemaVersion"] == 1
    assert report["candidateId"] == "course-mode-2026-09-05.99"
    assert report["status"] == "pass"
    assert report["findings"] == []
    assert report["checkedFileCount"] == 9
    assert report["checkedArchiveMemberCount"] == 0
    assert report["checks"]["physicalActionsPerformed"] is False
    assert report["checks"]["productionDatabaseUsed"] is False
    assert all(
        value
        for name, value in report["checks"].items()
        if name not in {"physicalActionsPerformed", "productionDatabaseUsed"}
    )
    assert output.stat().st_mode & 0o777 == 0o444


def test_preserved_sanitized_manifest_is_allowed_but_raw_playwright_tree_fails_closed(
    evidence_fixture: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    candidate, evidence, output = evidence_fixture
    preserved = tmp_path / "preserved"
    preserved.mkdir()
    _write_json(
        preserved / "sanitized-tombstone.json",
        {
            "entries": [
                {
                    "action": "deleted",
                    "bytes": 123,
                    "classification": "private-playwright-capture",
                    "path": "results/trace.zip",
                    "reason": "raw capture excluded",
                    "sha256": "d" * 64,
                    "timestamp": "2026-09-05T00:00:00Z",
                }
            ],
            "schemaVersion": 1,
        },
    )

    clean = _run(candidate, evidence, output, preserved)

    assert clean.returncode == 0
    assert json.loads(clean.stdout)["checkedFileCount"] == 10

    raw = preserved / "playwright-report"
    raw.mkdir()
    (raw / "index.html").write_text("<html>raw Playwright report</html>", encoding="utf-8")
    (raw / "index.html").chmod(0o444)

    rejected = _run(candidate, evidence, output, preserved)
    report = json.loads(rejected.stdout)

    assert rejected.returncode == 1
    assert "preserved.raw_playwright" in report["findings"]
    assert report["checks"]["rawPlaywrightAbsent"] is False


@pytest.mark.parametrize("attack", ["symlink", "hardlink", "fifo", "group-writable"])
def test_expected_evidence_rejects_insecure_metadata(
    evidence_fixture: tuple[Path, Path, Path], tmp_path: Path, attack: str
) -> None:
    candidate, evidence, output = evidence_fixture
    target = evidence / "03-quick-gate.json"
    original = target.read_bytes()
    target.chmod(0o644)
    target.unlink()
    replacement = tmp_path / "replacement.json"
    replacement.write_bytes(original)
    replacement.chmod(0o444)
    if attack == "symlink":
        target.symlink_to(replacement)
    elif attack == "hardlink":
        os.link(replacement, target)
    elif attack == "fifo":
        os.mkfifo(target, 0o444)
    else:
        target.write_bytes(original)
        target.chmod(0o464)

    completed = _run(candidate, evidence, output)
    report = json.loads(completed.stdout)

    assert completed.returncode == 1
    assert "evidence.metadata_or_json" in report["findings"]
    assert report["checks"]["secureFileMetadata"] is False


def test_bounded_zip_members_are_scanned_and_counted(
    evidence_fixture: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    candidate, evidence, output = evidence_fixture
    preserved = tmp_path / "preserved"
    preserved.mkdir()
    archive = preserved / "diagnostic.zip"
    archive.write_bytes(_zip({"summary.txt": b"public diagnostic result: PASS\n"}))
    archive.chmod(0o444)

    completed = _run(candidate, evidence, output, preserved)
    report = json.loads(completed.stdout)

    assert completed.returncode == 0
    assert report["checkedArchiveMemberCount"] == 1
    assert report["checks"]["archiveSafety"] is True


@pytest.mark.parametrize(
    ("name", "payload"),
    [
        ("headers.txt", b"Authorization: Bearer do-not-print-this-value\n"),
        ("cookies.json", b'{"Set-Cookie":"private-cookie-value"}'),
        ("session.json", b'{"sessionId":"private-session-value"}'),
        ("password.json", b'{"password":"private-password-value"}'),
        ("secret.json", b'{"secret":"private-secret-value"}'),
    ],
)
def test_zip_secret_values_fail_without_echoing_values(
    evidence_fixture: tuple[Path, Path, Path], tmp_path: Path, name: str, payload: bytes
) -> None:
    candidate, evidence, output = evidence_fixture
    preserved = tmp_path / "preserved"
    preserved.mkdir()
    archive = preserved / "diagnostic.zip"
    archive.write_bytes(_zip({name: payload}))
    archive.chmod(0o444)

    completed = _run(candidate, evidence, output, preserved)
    report = json.loads(completed.stdout)

    assert completed.returncode == 1
    assert "content.secret" in report["findings"]
    assert report["checks"]["secretScan"] is False
    private_value = payload.decode().split("private-", 1)[-1].rstrip('"}\n')
    assert private_value not in completed.stdout
    assert private_value not in output.read_text(encoding="utf-8")


def test_embedded_base64_playwright_payload_is_rejected(
    evidence_fixture: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    candidate, evidence, output = evidence_fixture
    preserved = tmp_path / "preserved"
    preserved.mkdir()
    encoded = base64.b64encode(_zip({"report/index.html": b"Playwright HTML report"})).decode()
    embedded = preserved / "summary.html"
    embedded.write_text(f"<script>window.report='{encoded}'</script>", encoding="utf-8")
    embedded.chmod(0o444)

    completed = _run(candidate, evidence, output, preserved)
    report = json.loads(completed.stdout)

    assert completed.returncode == 1
    assert "content.embedded_playwright" in report["findings"]
    assert report["checks"]["rawPlaywrightAbsent"] is False


@pytest.mark.parametrize(
    ("filename", "payload", "finding"),
    [
        ("encrypted.zip", _mark_zip_encrypted(_zip({"result.txt": b"PASS"})), "archive.encrypted"),
        ("unsupported.zip", _zip({"result.txt": b"PASS"}, zipfile.ZIP_BZIP2), "archive.unsupported"),
        ("escape.zip", _zip({"../outside.txt": b"PASS"}), "archive.invalid"),
        ("nested.zip", _zip({"level-2.zip": _zip({"level-3.zip": _zip({"result.txt": b"PASS"})})}), "archive.nested_limit"),
        ("oversize.zip", _zip({"large.txt": b"x" * (4 * 1024 * 1024 + 1)}), "archive.oversize"),
        ("unsupported.tar", b"not a supported bounded archive", "archive.unsupported"),
    ],
)
def test_archive_hazards_fail_closed(
    evidence_fixture: tuple[Path, Path, Path],
    tmp_path: Path,
    filename: str,
    payload: bytes,
    finding: str | None,
) -> None:
    candidate, evidence, output = evidence_fixture
    preserved = tmp_path / "preserved"
    preserved.mkdir()
    artifact = preserved / filename
    artifact.write_bytes(payload)
    artifact.chmod(0o444)

    completed = _run(candidate, evidence, output, preserved)
    report = json.loads(completed.stdout)

    assert completed.returncode == 1
    assert (finding or "archive.unsupported") in report["findings"]
    assert report["checks"]["archiveSafety"] is False


def test_zip_raw_playwright_member_is_rejected(
    evidence_fixture: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    candidate, evidence, output = evidence_fixture
    preserved = tmp_path / "preserved"
    preserved.mkdir()
    artifact = preserved / "report.zip"
    artifact.write_bytes(_zip({"playwright-report/index.html": b"Playwright HTML report"}))
    artifact.chmod(0o444)

    completed = _run(candidate, evidence, output, preserved)
    report = json.loads(completed.stdout)

    assert completed.returncode == 1
    assert "content.raw_playwright" in report["findings"]
    assert report["checks"]["rawPlaywrightAbsent"] is False


def test_audio_transcript_and_valid_private_key_are_rejected(
    evidence_fixture: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    serialization = pytest.importorskip("cryptography.hazmat.primitives.serialization")
    rsa = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.rsa")
    candidate, evidence, output = evidence_fixture
    preserved = tmp_path / "preserved"
    preserved.mkdir()
    (preserved / "voice.wav").write_bytes(b"RIFF" + b"\0" * 40)
    (preserved / "voice.wav").chmod(0o444)
    (preserved / "child-transcript.txt").write_text("child transcript: hello robot", encoding="utf-8")
    (preserved / "child-transcript.txt").chmod(0o444)
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    (preserved / "identity.pem").write_bytes(pem)
    (preserved / "identity.pem").chmod(0o400)

    completed = _run(candidate, evidence, output, preserved)
    report = json.loads(completed.stdout)

    assert completed.returncode == 1
    assert {"content.audio", "content.transcript", "content.private_key"} <= set(report["findings"])
    assert report["checks"]["privateContentAbsent"] is False
    assert b"PRIVATE KEY" not in completed.stdout.encode()


def test_every_primary_evidence_file_is_scanned_and_output_is_excluded(
    evidence_fixture: tuple[Path, Path, Path]
) -> None:
    candidate, evidence, output = evidence_fixture
    marker = "do-not-echo-primary-secret"
    extra = evidence / "diagnostic.log"
    extra.write_text(f"Authorization: Bearer {marker}\n", encoding="utf-8")
    extra.chmod(0o444)

    completed = _run(candidate, evidence, output)
    first_report = json.loads(completed.stdout)
    repeated = _run(candidate, evidence, output)
    repeated_report = json.loads(repeated.stdout)

    assert completed.returncode == repeated.returncode == 1
    assert first_report == repeated_report
    assert first_report["checkedFileCount"] == 10
    assert "content.secret" in first_report["findings"]
    assert marker not in completed.stdout
    assert marker not in output.read_text(encoding="utf-8")


def test_sanitized_manifest_requires_complete_hash_only_rows_and_no_secret_values(
    evidence_fixture: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    candidate, evidence, output = evidence_fixture
    preserved = tmp_path / "preserved"
    preserved.mkdir()
    marker = "do-not-echo-tombstone-secret"
    _write_json(
        preserved / "bad-tombstone.json",
        {
            "entries": [
                {
                    "action": "deleted",
                    "bytes": 1,
                    "classification": "private",
                    "path": "trace.zip",
                    "reason": f"Authorization: Bearer {marker}",
                    "sha256": "a" * 64,
                    "timestamp": "2026-09-05T00:00:00Z",
                }
            ],
            "schemaVersion": 1,
        },
    )

    completed = _run(candidate, evidence, output, preserved)
    report = json.loads(completed.stdout)

    assert completed.returncode == 1
    assert "content.secret" in report["findings"]
    assert marker not in completed.stdout


@pytest.mark.parametrize(
    ("name", "payload"),
    [
        ("screen.png", b"\x89PNG\r\n\x1a\n" + b"private pixels"),
        ("recording.webm", b"\x1a\x45\xdf\xa3" + b"private video"),
        ("frame.jpg", b"\xff\xd8\xff\xe0" + b"private photo"),
    ],
)
def test_binary_private_media_is_rejected(
    evidence_fixture: tuple[Path, Path, Path], tmp_path: Path, name: str, payload: bytes
) -> None:
    candidate, evidence, output = evidence_fixture
    preserved = tmp_path / "preserved"
    preserved.mkdir()
    artifact = preserved / name
    artifact.write_bytes(payload)
    artifact.chmod(0o444)

    completed = _run(candidate, evidence, output, preserved)
    report = json.loads(completed.stdout)

    assert completed.returncode == 1
    assert "content.binary_media" in report["findings"]
    assert report["checks"]["privateContentAbsent"] is False


def test_release_report_rejects_retained_fields_even_when_lanes_pass(
    evidence_fixture: tuple[Path, Path, Path]
) -> None:
    candidate, evidence, output = evidence_fixture
    report_path = evidence / "04-full-gate.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["retainedPaths"] = ["/tmp/private-runtime"]
    report_path.chmod(0o644)
    _write_json(report_path, report)

    completed = _run(candidate, evidence, output)
    result = json.loads(completed.stdout)

    assert completed.returncode == 1
    assert "evidence.full" in result["findings"]
    assert result["checks"]["fullGate20of20"] is False


def test_missing_preserved_root_and_insecure_candidate_fail_closed(
    evidence_fixture: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    candidate, evidence, output = evidence_fixture
    candidate.chmod(0o466)

    completed = _run(candidate, evidence, output, tmp_path / "missing")
    report = json.loads(completed.stdout)

    assert completed.returncode == 1
    assert {"candidate.metadata_or_json", "preserved.root"} <= set(report["findings"])
    assert report["checks"]["secureFileMetadata"] is False


def test_output_must_be_inside_evidence_root(
    evidence_fixture: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    candidate, evidence, _ = evidence_fixture
    unsafe_output = tmp_path / "outside.json"

    completed = _run(candidate, evidence, unsafe_output)
    report = json.loads(completed.stdout)

    assert completed.returncode == 1
    assert "output.unsafe" in report["findings"]
    assert not unsafe_output.exists()

    wrong_name = evidence / "audit.json"
    completed = _run(candidate, evidence, wrong_name)
    assert completed.returncode == 1
    assert "output.unsafe" in json.loads(completed.stdout)["findings"]
    assert not wrong_name.exists()


def test_cli_does_not_resolve_away_candidate_symlink(
    evidence_fixture: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    candidate, evidence, output = evidence_fixture
    alias = tmp_path / "candidate-link.json"
    alias.symlink_to(candidate)

    completed = _run(alias, evidence, output)
    report = json.loads(completed.stdout)

    assert completed.returncode == 1
    assert "candidate.metadata_or_json" in report["findings"]


def test_invalid_candidate_id_is_not_echoed(
    evidence_fixture: tuple[Path, Path, Path]
) -> None:
    candidate, evidence, output = evidence_fixture
    payload = json.loads(candidate.read_text(encoding="utf-8"))
    marker = "attacker-candidate-id-secret"
    payload["candidateId"] = marker
    candidate.chmod(0o644)
    _write_json(candidate, payload)

    completed = _run(candidate, evidence, output)
    report = json.loads(completed.stdout)

    assert completed.returncode == 1
    assert report["candidateId"] is None
    assert marker not in completed.stdout
    assert marker not in output.read_text(encoding="utf-8")


def test_output_cannot_collide_with_required_input_or_alias_it(
    evidence_fixture: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    candidate, evidence, output = evidence_fixture
    required = evidence / "03-quick-gate.json"
    collision = _run(candidate, evidence, required)
    assert collision.returncode == 1
    assert "output.collision" in json.loads(collision.stdout)["findings"]
    assert required.read_bytes() != output.read_bytes() if output.exists() else True

    alias = tmp_path / "output-alias.json"
    alias.symlink_to(required)
    aliased = _run(candidate, evidence, alias)
    assert aliased.returncode == 1
    assert "output.collision" in json.loads(aliased.stdout)["findings"]

    hardlink = tmp_path / "output-hardlink.json"
    os.link(required, hardlink)
    linked = _run(candidate, evidence, hardlink)
    assert linked.returncode == 1
    assert "output.collision" in json.loads(linked.stdout)["findings"]

    extra = evidence / "extra.json"
    extra.write_bytes(b"{}\n")
    extra.chmod(0o444)
    collision = _run(candidate, evidence, extra)
    assert collision.returncode == 1
    assert "output.collision" in json.loads(collision.stdout)["findings"]


@pytest.mark.parametrize("payload", [b"not a zip", b"MZ-self-extracting-prefix"])
def test_zip_extension_requires_valid_zip_and_prefixed_zip_is_supported(
    evidence_fixture: tuple[Path, Path, Path], tmp_path: Path, payload: bytes
) -> None:
    candidate, evidence, output = evidence_fixture
    preserved = tmp_path / "preserved"
    preserved.mkdir()
    artifact = preserved / "capture.zip"
    if payload.startswith(b"MZ"):
        payload = b"MZ" + _zip({"result.txt": b"PASS"})
    artifact.write_bytes(payload)
    artifact.chmod(0o444)

    completed = _run(candidate, evidence, output, preserved)
    report = json.loads(completed.stdout)

    if payload.startswith(b"MZ"):
        assert completed.returncode == 0
        assert report["checkedArchiveMemberCount"] == 1
    else:
        assert completed.returncode == 1
        assert "archive.invalid" in report["findings"]


def test_tombstone_strings_and_embedded_base64_are_still_scanned(
    evidence_fixture: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    candidate, evidence, output = evidence_fixture
    preserved = tmp_path / "preserved"
    preserved.mkdir()
    marker = "tombstone-secret-value"
    _write_json(
        preserved / "sanitized-tombstone.json",
        {
            "entries": [
                {
                    "action": "deleted",
                    "bytes": 123,
                    "classification": "private-playwright-capture",
                    "path": "results/trace.zip",
                    "reason": f"Authorization: Bearer {marker}",
                    "sha256": "d" * 64,
                    "timestamp": "2026-09-05T00:00:00Z",
                }
            ],
            "schemaVersion": 1,
        },
    )

    completed = _run(candidate, evidence, output, preserved)
    report = json.loads(completed.stdout)

    assert completed.returncode == 1
    assert "content.secret" in report["findings"]
    assert marker not in completed.stdout


def test_tombstone_wrong_field_types_fail_closed_without_crashing(
    evidence_fixture: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    candidate, evidence, output = evidence_fixture
    preserved = tmp_path / "preserved"
    preserved.mkdir()
    _write_json(
        preserved / "sanitized-tombstone.json",
        {
            "entries": [
                {
                    "action": "deleted",
                    "bytes": "123",
                    "classification": "private",
                    "path": "trace.zip",
                    "reason": ["raw capture excluded"],
                    "sha256": "d" * 64,
                    "timestamp": "2026-09-05T00:00:00Z",
                }
            ],
            "schemaVersion": 1,
        },
    )

    completed = _run(candidate, evidence, output, preserved)
    report = json.loads(completed.stdout)

    assert completed.returncode == 1
    assert "preserved.manifest" in report["findings"]


@pytest.mark.parametrize(
    ("name", "decoded", "finding"),
    [
        ("encoded.txt", b"Authorization: Bearer " + b"z" * 120, "content.secret"),
        ("encoded.txt", b"RIFF" + b"z" * 120, "content.audio"),
        ("encoded.txt", b"-----BEGIN PRIVATE KEY-----\nmalformed\n" + b"z" * 120, "content.private_key"),
    ],
)
def test_embedded_base64_payloads_reuse_content_scanner(
    evidence_fixture: tuple[Path, Path, Path],
    tmp_path: Path,
    name: str,
    decoded: bytes,
    finding: str,
) -> None:
    candidate, evidence, output = evidence_fixture
    preserved = tmp_path / "preserved"
    preserved.mkdir()
    artifact = preserved / name
    artifact.write_text(base64.b64encode(decoded).decode(), encoding="ascii")
    artifact.chmod(0o444)

    completed = _run(candidate, evidence, output, preserved)
    report = json.loads(completed.stdout)

    assert completed.returncode == 1
    assert finding in report["findings"]


def test_mime_wrapped_base64_playwright_payload_is_rejected(
    evidence_fixture: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    candidate, evidence, output = evidence_fixture
    preserved = tmp_path / "preserved"
    preserved.mkdir()
    encoded = base64.b64encode(b"Playwright HTML report" + b"x" * 180).decode()
    wrapped = "\n \t".join(encoded[index : index + 48] for index in range(0, len(encoded), 48))
    artifact = preserved / "mime.txt"
    artifact.write_text(wrapped, encoding="ascii")
    artifact.chmod(0o444)

    completed = _run(candidate, evidence, output, preserved)
    report = json.loads(completed.stdout)

    assert completed.returncode == 1
    assert "content.embedded_playwright" in report["findings"]


def test_urlsafe_base64_playwright_payload_is_rejected(
    evidence_fixture: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    candidate, evidence, output = evidence_fixture
    preserved = tmp_path / "preserved"
    preserved.mkdir()
    encoded = base64.urlsafe_b64encode(b"Playwright HTML report" + b"\xfb\xef\xff" * 80).decode()
    assert "-" in encoded or "_" in encoded
    artifact = preserved / "urlsafe.txt"
    artifact.write_text(encoded, encoding="ascii")
    artifact.chmod(0o444)

    completed = _run(candidate, evidence, output, preserved)
    report = json.loads(completed.stdout)

    assert completed.returncode == 1
    assert "content.embedded_playwright" in report["findings"]


@pytest.mark.parametrize(
    "decoded",
    [
        b"Playwright HTML report",
        b"Authorization: Bearer short-secret",
    ],
)
def test_short_embedded_base64_payloads_are_not_skipped(
    evidence_fixture: tuple[Path, Path, Path], tmp_path: Path, decoded: bytes
) -> None:
    candidate, evidence, output = evidence_fixture
    preserved = tmp_path / "preserved"
    preserved.mkdir()
    encoded = base64.urlsafe_b64encode(decoded)
    assert len(encoded) < 128
    artifact = preserved / "short.txt"
    artifact.write_bytes(encoded)
    artifact.chmod(0o444)

    completed = _run(candidate, evidence, output, preserved)
    report = json.loads(completed.stdout)

    assert completed.returncode == 1
    expected = "content.embedded_playwright" if b"Playwright" in decoded else "content.secret"
    assert expected in report["findings"]


def test_repeated_short_base64_blocks_hit_cumulative_budget(
    evidence_fixture: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    candidate, evidence, output = evidence_fixture
    preserved = tmp_path / "preserved"
    preserved.mkdir()
    block = base64.b64encode(b"safe-short-block")
    assert len(block) < 128
    artifact = preserved / "repeated-short.txt"
    artifact.write_bytes(b" ".join([block] * 80))
    artifact.chmod(0o444)

    completed = _run(candidate, evidence, output, preserved)
    report = json.loads(completed.stdout)

    assert completed.returncode == 1
    assert "content.base64_limit" in report["findings"]


def test_double_encoded_standard_base64_playwright_payload_is_rejected(
    evidence_fixture: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    candidate, evidence, output = evidence_fixture
    preserved = tmp_path / "preserved"
    preserved.mkdir()
    encoded = base64.b64encode(base64.b64encode(b"Playwright HTML report" + b"x" * 180)).decode()
    artifact = preserved / "double.txt"
    artifact.write_text(encoded, encoding="ascii")
    artifact.chmod(0o444)

    completed = _run(candidate, evidence, output, preserved)
    report = json.loads(completed.stdout)

    assert completed.returncode == 1
    assert "content.embedded_playwright" in report["findings"]


def test_base64_decode_budget_fails_closed_without_unbounded_recursion(
    evidence_fixture: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    candidate, evidence, output = evidence_fixture
    preserved = tmp_path / "preserved"
    preserved.mkdir()
    encoded = b"x"
    for _ in range(8):
        encoded = base64.b64encode(encoded)
    artifact = preserved / "deep.txt"
    artifact.write_bytes(encoded + b"\n" + b" ".join([base64.b64encode(b"safe-value" + b"x" * 100)] * 80))
    artifact.chmod(0o444)

    completed = _run(candidate, evidence, output, preserved)
    report = json.loads(completed.stdout)

    assert completed.returncode == 1
    assert "content.base64_limit" in report["findings"]


def test_embedded_base64_invalid_zip_is_fail_closed(
    evidence_fixture: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    candidate, evidence, output = evidence_fixture
    preserved = tmp_path / "preserved"
    preserved.mkdir()
    artifact = preserved / "encoded.txt"
    artifact.write_text(base64.b64encode(b"PK\x03\x04opaque-invalid-zip" + b"x" * 128).decode(), encoding="ascii")
    artifact.chmod(0o444)

    completed = _run(candidate, evidence, output, preserved)
    report = json.loads(completed.stdout)

    assert completed.returncode == 1
    assert "archive.invalid" in report["findings"]
    assert "content.embedded_playwright" in report["findings"]


def test_zip_member_zlib_error_is_converted_to_archive_finding() -> None:
    payload = _zip({"report.txt": b"safe"})

    def broken_read(_archive, _info):
        raise zlib.error("corrupt deflate stream")

    original_read = privacy.zipfile.ZipFile.read
    privacy.zipfile.ZipFile.read = broken_read
    try:
        findings, _ = privacy.scan_evidence_payload(payload, "report.zip")
    finally:
        privacy.zipfile.ZipFile.read = original_read

    assert "archive.invalid" in findings


@pytest.mark.parametrize(
    "encoded",
    [
        base64.b64encode(b"token=abc"),
        base64.b64encode(b"Authorization: Bearer x")[:4]
        + b" \n "
        + base64.b64encode(b"Authorization: Bearer x")[4:8]
        + b"\n"
        + base64.b64encode(b"Authorization: Bearer x")[8:],
    ],
)
def test_short_and_whitespace_split_base64_secrets_are_scanned(
    evidence_fixture: tuple[Path, Path, Path], tmp_path: Path, encoded: bytes
) -> None:
    candidate, evidence, output = evidence_fixture
    preserved = tmp_path / "preserved"
    preserved.mkdir()
    artifact = preserved / "short-secret.txt"
    artifact.write_bytes(encoded)
    artifact.chmod(0o444)

    completed = _run(candidate, evidence, output, preserved)
    report = json.loads(completed.stdout)

    assert completed.returncode == 1
    assert "content.secret" in report["findings"]


@pytest.mark.parametrize("split_size", [1, 2, 3, 5, 6, 7])
def test_same_line_whitespace_split_base64_secret_is_scanned(
    evidence_fixture: tuple[Path, Path, Path], tmp_path: Path, split_size: int
) -> None:
    candidate, evidence, output = evidence_fixture
    preserved = tmp_path / "preserved"
    preserved.mkdir()
    encoded = base64.b64encode(b"Authorization: Bearer x").decode()
    split = " ".join(encoded[index : index + split_size] for index in range(0, len(encoded), split_size))
    artifact = preserved / "same-line-secret.txt"
    artifact.write_text(split, encoding="ascii")
    artifact.chmod(0o444)

    completed = _run(candidate, evidence, output, preserved)
    report = json.loads(completed.stdout)

    assert completed.returncode == 1
    assert "content.secret" in report["findings"]


def test_secret_json_key_matching_avoids_counter_and_policy_fields(
    evidence_fixture: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    candidate, evidence, output = evidence_fixture
    preserved = tmp_path / "preserved"
    preserved.mkdir()
    artifact = preserved / "metadata.json"
    artifact.write_bytes(
        b'{"tokenCount":"0","sessionDurationMs":12,"cookiePolicy":"strict"}\n'
    )
    artifact.chmod(0o444)

    completed = _run(candidate, evidence, output, preserved)
    report = json.loads(completed.stdout)

    assert completed.returncode == 0
    assert "content.secret" not in report["findings"]


def test_secret_json_traversal_is_bounded_and_fails_closed(
    evidence_fixture: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    candidate, evidence, output = evidence_fixture
    preserved = tmp_path / "preserved"
    preserved.mkdir()
    value: object = "safe"
    for _ in range(80):
        value = {"nested": value}
    artifact = preserved / "deep.json"
    artifact.write_text(json.dumps(value), encoding="utf-8")
    artifact.chmod(0o444)

    completed = _run(candidate, evidence, output, preserved)
    report = json.loads(completed.stdout)

    assert completed.returncode == 1
    assert "content.secret" in report["findings"]


def test_lane_numeric_fields_reject_bool(
    evidence_fixture: tuple[Path, Path, Path]
) -> None:
    candidate, evidence, output = evidence_fixture
    quick = evidence / "03-quick-gate.json"
    payload = json.loads(quick.read_text(encoding="utf-8"))
    payload["lanes"][0]["durationMs"] = True
    quick.chmod(0o644)
    _write_json(quick, payload)

    completed = _run(candidate, evidence, output)
    report = json.loads(completed.stdout)

    assert completed.returncode == 1
    assert "evidence.quick" in report["findings"]


def test_whole_audit_file_budget_is_cumulative(
    evidence_fixture: tuple[Path, Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate, evidence, output = evidence_fixture
    preserved = tmp_path / "preserved"
    preserved.mkdir()
    for index in range(2):
        artifact = preserved / f"extra-{index}.txt"
        artifact.write_bytes(b"safe")
        artifact.chmod(0o444)
    monkeypatch.setattr(auditor, "MAX_AUDIT_FILES", 10)

    report = auditor.audit(candidate, evidence, [preserved], output)

    assert "evidence.budget" in report["findings"]


def test_whole_audit_byte_budget_is_cumulative(
    evidence_fixture: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate, evidence, output = evidence_fixture
    monkeypatch.setattr(auditor, "MAX_AUDIT_TOTAL_BYTES", 1)

    report = auditor.audit(candidate, evidence, [], output)

    assert "evidence.budget" in report["findings"]


def test_base64_and_archive_budgets_are_cumulative_across_top_level_files(
    evidence_fixture: tuple[Path, Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate, evidence, output = evidence_fixture
    preserved = tmp_path / "preserved"
    preserved.mkdir()
    for index in range(3):
        encoded = base64.b64encode(b"safe-short-block")
        artifact = preserved / f"encoded-{index}.txt"
        artifact.write_bytes(encoded)
        artifact.chmod(0o444)
    monkeypatch.setattr(privacy, "MAX_BASE64_BLOCKS", 2)

    report = auditor.audit(candidate, evidence, [preserved], output)

    assert "content.base64_limit" in report["findings"]

    for index in range(2):
        artifact = preserved / f"archive-{index}.zip"
        artifact.write_bytes(_zip({"one.txt": b"ok", "two.txt": b"ok"}))
        artifact.chmod(0o444)
    monkeypatch.setattr(privacy, "MAX_ARCHIVE_MEMBERS", 2)

    report = auditor.audit(candidate, evidence, [preserved], output)

    assert "archive.oversize" in report["findings"]


def test_intermediate_root_metadata_and_output_symlink_are_rejected(
    evidence_fixture: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    candidate, evidence, output = evidence_fixture
    insecure_parent = tmp_path / "insecure-parent"
    insecure_parent.mkdir(mode=0o700)
    insecure_parent.chmod(0o777)
    preserved = insecure_parent / "preserved"
    preserved.mkdir()
    report = auditor.audit(candidate, evidence, [preserved], output)
    assert "preserved.root" in report["findings"]

    output_parent = tmp_path / "output-parent"
    output_parent.symlink_to(evidence, target_is_directory=True)
    aliased_output = output_parent / "06-software-evidence-audit.json"
    report = auditor.audit(candidate, evidence, [], aliased_output)
    assert "output.unsafe" in report["findings"]


def test_audit_budget_counts_directories_and_does_not_materialize_rglob(
    evidence_fixture: tuple[Path, Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate, evidence, output = evidence_fixture
    preserved = tmp_path / "preserved"
    preserved.mkdir()
    for index in range(20):
        (preserved / f"dir-{index}").mkdir()
    monkeypatch.setattr(auditor, "MAX_AUDIT_FILES", 10)
    monkeypatch.setattr(Path, "rglob", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("rglob")))

    report = auditor.audit(candidate, evidence, [preserved], output)

    assert "evidence.budget" in report["findings"]


def test_streaming_wide_iterator_stops_at_remaining_budget(
    evidence_fixture: tuple[Path, Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate, evidence, output = evidence_fixture
    preserved = tmp_path / "preserved"
    preserved.mkdir()
    for index in range(100):
        artifact = preserved / f"wide-{index}.txt"
        artifact.write_bytes(b"safe")
        artifact.chmod(0o444)
    original_scandir = auditor.os.scandir
    consumed = 0

    class CountingIterator:
        def __init__(self, iterator):
            self.iterator = iterator

        def __next__(self):
            nonlocal consumed
            consumed += 1
            return next(self.iterator)

        def close(self):
            self.iterator.close()

    def wrapped_scandir(path):
        iterator = original_scandir(path)
        return CountingIterator(iterator) if Path(path) == preserved else iterator

    monkeypatch.setattr(auditor.os, "scandir", wrapped_scandir)
    monkeypatch.setattr(auditor, "MAX_AUDIT_FILES", 10)

    report = auditor.audit(candidate, evidence, [preserved], output)

    assert "evidence.budget" in report["findings"]
    assert consumed <= 2


def test_valid_sanitized_manifest_with_failed_action_is_rejected(
    evidence_fixture: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    candidate, evidence, output = evidence_fixture
    preserved = tmp_path / "preserved"
    preserved.mkdir()
    _write_json(
        preserved / "sanitized-tombstone.json",
        {
            "entries": [
                {
                    "action": "sanitization-failed",
                    "bytes": 3,
                    "classification": "private-content",
                    "path": "capture.bin",
                    "reason": "private content removed",
                    "sha256": "d" * 64,
                    "timestamp": "2026-09-05T00:00:00Z",
                }
            ],
            "schemaVersion": 1,
        },
    )

    completed = _run(candidate, evidence, output, preserved)
    report = json.loads(completed.stdout)

    assert completed.returncode == 1
    assert "preserved.sanitization_failed" in report["findings"]


def test_zip_member_after_first_64k_is_scanned(
    evidence_fixture: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    candidate, evidence, output = evidence_fixture
    preserved = tmp_path / "preserved"
    preserved.mkdir()
    artifact = preserved / "report.zip"
    artifact.write_bytes(_zip({"report.html": b"x" * 70_000 + b"Playwright HTML report"}))
    artifact.chmod(0o444)

    completed = _run(candidate, evidence, output, preserved)
    report = json.loads(completed.stdout)

    assert completed.returncode == 1
    assert "content.raw_playwright" in report["findings"]


def test_malformed_pem_private_key_marker_is_rejected(
    evidence_fixture: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    candidate, evidence, output = evidence_fixture
    preserved = tmp_path / "preserved"
    preserved.mkdir()
    key = preserved / "not-a-key.txt"
    key.write_text("-----BEGIN PRIVATE KEY-----\nmalformed\n", encoding="utf-8")
    key.chmod(0o444)

    completed = _run(candidate, evidence, output, preserved)
    report = json.loads(completed.stdout)

    assert completed.returncode == 1
    assert "content.private_key" in report["findings"]


def test_archive_budgets_are_cumulative_across_nested_members(
    evidence_fixture: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    candidate, evidence, output = evidence_fixture
    preserved = tmp_path / "preserved"
    preserved.mkdir()
    inner = _zip({f"member-{index}.txt": b"ok" for index in range(4_100)})
    artifact = preserved / "nested.zip"
    artifact.write_bytes(_zip({"inner.zip": inner}))
    artifact.chmod(0o444)

    completed = _run(candidate, evidence, output, preserved)
    report = json.loads(completed.stdout)

    assert completed.returncode == 1
    assert "archive.oversize" in report["findings"]


def test_symlinked_evidence_or_preserved_root_is_rejected_before_traversal(
    evidence_fixture: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    candidate, evidence, output = evidence_fixture
    evidence_alias = tmp_path / "evidence-alias"
    evidence_alias.symlink_to(evidence, target_is_directory=True)
    candidate_payload = json.loads(candidate.read_text(encoding="utf-8"))
    candidate.chmod(0o644)
    candidate_payload["evidenceRoot"] = str(evidence_alias)
    _write_json(candidate, candidate_payload)

    completed = _run(candidate, evidence_alias, output)
    report = json.loads(completed.stdout)

    assert completed.returncode == 1
    assert "evidence.root" in report["findings"]

    candidate_payload["evidenceRoot"] = str(evidence)
    candidate.chmod(0o644)
    _write_json(candidate, candidate_payload)
    preserved_alias = tmp_path / "preserved-alias"
    preserved_alias.symlink_to(evidence, target_is_directory=True)
    completed = _run(candidate, evidence, output, preserved_alias)
    report = json.loads(completed.stdout)
    assert completed.returncode == 1
    assert "preserved.root" in report["findings"]


def test_nested_evidence_directory_metadata_is_checked(
    evidence_fixture: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    candidate, evidence, output = evidence_fixture
    preserved = tmp_path / "preserved"
    nested = preserved / "nested"
    nested.mkdir(parents=True)
    nested.chmod(0o775)
    marker = nested / "summary.json"
    marker.write_bytes(b"{}\n")
    marker.chmod(0o444)

    completed = _run(candidate, evidence, output, preserved)
    report = json.loads(completed.stdout)

    assert completed.returncode == 1
    assert "preserved.metadata" in report["findings"]
