from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

import scripts.course_mode_operator_attestation as attestation


def _candidate(tmp_path: Path) -> tuple[Path, dict]:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir(mode=0o700)
    payload = {
        "candidateId": "course-mode-2026-08-31.12",
        "repositories": {"adminEsp": {"sha": "a" * 40}},
        "evidenceRoot": str(evidence_root),
    }
    path = tmp_path / "candidate.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path, payload


def _run(candidate: Path, output: Path, *extra: str) -> int:
    return attestation.main([
        "--candidate", str(candidate),
        "--output", str(output),
        *extra,
    ])


@pytest.fixture(autouse=True)
def valid_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(attestation, "validate_candidate", lambda _candidate: [])


def test_writes_exact_candidate_bound_canonical_attestation(tmp_path: Path) -> None:
    candidate_path, candidate = _candidate(tmp_path)
    output = Path(candidate["evidenceRoot"]) / "operator-attestation.json"

    result = _run(
        candidate_path, output,
        "--confirm-trusted-operator-account",
        "--confirm-untrusted-automation-stopped",
    )

    assert result == 0
    raw = output.read_bytes()
    payload = json.loads(raw)
    assert raw == (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
    assert set(payload) == {
        "candidateId", "createdAt", "effectiveUid", "gateSha", "hostName",
        "sameUidThreatModel", "schemaVersion", "trustedOperatorAccountConfirmed",
        "untrustedAutomationStoppedConfirmed",
    }
    assert payload == {
        "candidateId": candidate["candidateId"],
        "createdAt": payload["createdAt"],
        "effectiveUid": os.geteuid(),
        "gateSha": candidate["repositories"]["adminEsp"]["sha"],
        "hostName": socket.gethostname(),
        "sameUidThreatModel": "malicious-process-excluded",
        "schemaVersion": 1,
        "trustedOperatorAccountConfirmed": True,
        "untrustedAutomationStoppedConfirmed": True,
    }
    assert datetime.fromisoformat(payload["createdAt"].replace("Z", "+00:00")).utcoffset().total_seconds() == 0
    assert output.stat().st_mode & 0o777 == 0o444


@pytest.mark.parametrize(
    "flags",
    [(), ("--confirm-trusted-operator-account",),
     ("--confirm-untrusted-automation-stopped",)],
)
def test_refuses_missing_confirmations(tmp_path: Path, flags: tuple[str, ...]) -> None:
    candidate_path, candidate = _candidate(tmp_path)
    output = Path(candidate["evidenceRoot"]) / "operator-attestation.json"

    assert _run(candidate_path, output, *flags) != 0
    assert not output.exists()


def test_refuses_existing_output_without_overwriting(tmp_path: Path) -> None:
    candidate_path, candidate = _candidate(tmp_path)
    output = Path(candidate["evidenceRoot"]) / "operator-attestation.json"
    output.write_bytes(b"keep me")

    assert _run(candidate_path, output, "--confirm-trusted-operator-account",
                "--confirm-untrusted-automation-stopped") != 0
    assert output.read_bytes() == b"keep me"


def test_refuses_output_outside_evidence_root(tmp_path: Path) -> None:
    candidate_path, _candidate_payload = _candidate(tmp_path)
    output = tmp_path / "outside.json"

    assert _run(candidate_path, output, "--confirm-trusted-operator-account",
                "--confirm-untrusted-automation-stopped") != 0
    assert not output.exists()


def test_refuses_candidate_validation_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate_path, candidate = _candidate(tmp_path)
    output = Path(candidate["evidenceRoot"]) / "operator-attestation.json"
    monkeypatch.setattr(attestation, "validate_candidate", lambda _candidate: ["candidate.invalid"])

    assert _run(candidate_path, output, "--confirm-trusted-operator-account",
                "--confirm-untrusted-automation-stopped") != 0
    assert not output.exists()


def test_refuses_symlink_parent(tmp_path: Path) -> None:
    candidate_path, candidate = _candidate(tmp_path)
    real_parent = Path(candidate["evidenceRoot"]) / "real"
    real_parent.mkdir(mode=0o700)
    linked_parent = Path(candidate["evidenceRoot"]) / "linked"
    linked_parent.symlink_to(real_parent, target_is_directory=True)
    output = linked_parent / "operator-attestation.json"

    assert _run(candidate_path, output, "--confirm-trusted-operator-account",
                "--confirm-untrusted-automation-stopped") != 0
    assert not (real_parent / output.name).exists()


@pytest.mark.parametrize("mode", [0o720, 0o707])
def test_refuses_group_or_other_writable_parent(tmp_path: Path, mode: int) -> None:
    candidate_path, candidate = _candidate(tmp_path)
    parent = Path(candidate["evidenceRoot"]) / "unsafe"
    parent.mkdir(mode=0o700)
    parent.chmod(mode)
    output = parent / "operator-attestation.json"

    assert _run(candidate_path, output, "--confirm-trusted-operator-account",
                "--confirm-untrusted-automation-stopped") != 0
    assert not output.exists()


def test_removes_only_new_output_when_candidate_changes_during_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate_path, candidate = _candidate(tmp_path)
    output = Path(candidate["evidenceRoot"]) / "operator-attestation.json"
    original_write = attestation._write_all

    def mutate_candidate(fd: int, raw: bytes) -> None:
        original_write(fd, raw)
        candidate_path.write_bytes(candidate_path.read_bytes() + b" ")

    monkeypatch.setattr(attestation, "_write_all", mutate_candidate)

    assert _run(candidate_path, output, "--confirm-trusted-operator-account",
                "--confirm-untrusted-automation-stopped") != 0
    assert not output.exists()


def test_refuses_parent_replacement_during_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate_path, candidate = _candidate(tmp_path)
    parent = Path(candidate["evidenceRoot"]) / "operator"
    parent.mkdir(mode=0o700)
    output = parent / "operator-attestation.json"
    moved = parent.with_name("operator-moved")
    original_write = attestation._write_all

    def replace_parent(fd: int, raw: bytes) -> None:
        original_write(fd, raw)
        parent.rename(moved)
        parent.mkdir(mode=0o700)

    monkeypatch.setattr(attestation, "_write_all", replace_parent)

    assert _run(candidate_path, output, "--confirm-trusted-operator-account",
                "--confirm-untrusted-automation-stopped") != 0
    assert not output.exists()
    assert not (moved / output.name).exists()


def test_candidate_recheck_occurs_after_parent_fsync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate_path, candidate = _candidate(tmp_path)
    output = Path(candidate["evidenceRoot"]) / "operator-attestation.json"
    original_fsync = attestation.os.fsync
    calls = 0

    def mutate_after_parent_fsync(fd: int) -> None:
        nonlocal calls
        original_fsync(fd)
        calls += 1
        if calls == 2:
            candidate_path.write_bytes(candidate_path.read_bytes() + b" ")

    monkeypatch.setattr(attestation.os, "fsync", mutate_after_parent_fsync)

    assert _run(candidate_path, output, "--confirm-trusted-operator-account",
                "--confirm-untrusted-automation-stopped") != 0
    assert not output.exists()


def test_parent_binding_recheck_occurs_after_parent_fsync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate_path, candidate = _candidate(tmp_path)
    parent = Path(candidate["evidenceRoot"]) / "operator"
    parent.mkdir(mode=0o700)
    output = parent / "operator-attestation.json"
    moved = parent.with_name("operator-moved")
    original_fsync = attestation.os.fsync
    calls = 0

    def replace_after_parent_fsync(fd: int) -> None:
        nonlocal calls
        original_fsync(fd)
        calls += 1
        if calls == 2:
            parent.rename(moved)
            parent.mkdir(mode=0o700)

    monkeypatch.setattr(attestation.os, "fsync", replace_after_parent_fsync)

    assert _run(candidate_path, output, "--confirm-trusted-operator-account",
                "--confirm-untrusted-automation-stopped") != 0
    assert not output.exists()
    assert not (moved / output.name).exists()


def test_help_is_limited_to_public_cli_contract() -> None:
    script = Path(attestation.__file__)
    result = subprocess.run(
        [sys.executable, str(script), "--help"], check=True, capture_output=True, text=True,
    )

    assert set((
        "--candidate", "--output", "--confirm-trusted-operator-account",
        "--confirm-untrusted-automation-stopped",
    )).issubset(result.stdout.split())
    assert "http://" not in result.stdout
    assert "https://" not in result.stdout
    assert "token" not in result.stdout.lower()
