from __future__ import annotations

import json
import os
import socket
import stat
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

import scripts.course_mode_operator_attestation as attestation
import scripts.course_mode_release_gate as gate


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


def _is_gate_valid(candidate: dict, output: Path) -> bool:
    return gate._operator_attestation_binding(
        candidate, {gate.OPERATOR_ATTESTATION_ENV: str(output)},
    ) is not None


def _fd_count() -> int:
    return len(os.listdir("/dev/fd"))


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


@pytest.mark.parametrize(
    "option",
    ["--confirm-trusted-operator-acc", "--unrecognized-option"],
)
def test_refuses_abbreviated_or_unknown_options_without_creating_output(
    tmp_path: Path, option: str,
) -> None:
    candidate_path, candidate = _candidate(tmp_path)
    output = Path(candidate["evidenceRoot"]) / "operator-attestation.json"

    with pytest.raises(SystemExit) as raised:
        _run(
            candidate_path, output, option,
            "--confirm-untrusted-automation-stopped",
        )

    assert raised.value.code != 0
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


@pytest.mark.parametrize("failure", ["partial-write", "file-fsync", "parent-fsync", "file-close"])
def test_failed_creation_leaves_no_gate_valid_artifact_or_descriptor_leak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    candidate_path, candidate = _candidate(tmp_path)
    output = Path(candidate["evidenceRoot"]) / "operator-attestation.json"
    original_fsync = attestation.os.fsync
    original_close = attestation.os.close
    triggered = False

    if failure == "partial-write":
        def fail_partial_write(fd: int, raw: bytes) -> None:
            attestation.os.write(fd, raw[:len(raw) // 2])
            raise OSError("injected partial write")

        monkeypatch.setattr(attestation, "_write_all", fail_partial_write)
    elif failure in {"file-fsync", "parent-fsync"}:
        def fail_selected_fsync(fd: int) -> None:
            nonlocal triggered
            metadata = attestation.os.fstat(fd)
            selected = stat.S_ISREG(metadata.st_mode) if failure == "file-fsync" else stat.S_ISDIR(metadata.st_mode)
            if selected and not triggered:
                triggered = True
                raise OSError(f"injected {failure}")
            original_fsync(fd)

        monkeypatch.setattr(attestation.os, "fsync", fail_selected_fsync)
    else:
        def fail_output_close(fd: int) -> None:
            nonlocal triggered
            if output.exists() and not triggered:
                output_metadata = output.stat()
                descriptor_metadata = attestation.os.fstat(fd)
                if (output_metadata.st_dev, output_metadata.st_ino) == (
                    descriptor_metadata.st_dev, descriptor_metadata.st_ino,
                ):
                    triggered = True
                    original_close(fd)
                    raise OSError("injected file close")
            original_close(fd)

        monkeypatch.setattr(attestation.os, "close", fail_output_close)

    before = _fd_count()
    result = _run(
        candidate_path, output,
        "--confirm-trusted-operator-account",
        "--confirm-untrusted-automation-stopped",
    )

    assert result != 0
    assert triggered or failure == "partial-write"
    assert not _is_gate_valid(candidate, output)
    assert _fd_count() == before


def test_unlink_failure_invalidates_owned_artifact_through_open_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate_path, candidate = _candidate(tmp_path)
    output = Path(candidate["evidenceRoot"]) / "operator-attestation.json"
    original_unlink = attestation.os.unlink
    unlink_failed = False

    def fail_owned_unlink(path: str, *, dir_fd: int | None = None) -> None:
        nonlocal unlink_failed
        if path == output.name and dir_fd is not None and not unlink_failed:
            unlink_failed = True
            raise OSError("injected unlink failure")
        original_unlink(path, dir_fd=dir_fd)

    monkeypatch.setattr(attestation, "_candidate_unchanged", lambda *_args: False)
    monkeypatch.setattr(attestation.os, "unlink", fail_owned_unlink)

    before = _fd_count()
    result = _run(
        candidate_path, output,
        "--confirm-trusted-operator-account",
        "--confirm-untrusted-automation-stopped",
    )

    assert result != 0
    assert unlink_failed is True
    assert output.exists()
    assert output.read_bytes() == b""
    assert not _is_gate_valid(candidate, output)
    assert _fd_count() == before


def test_cleanup_preserves_unrelated_replacement_and_invalidates_created_inode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate_path, candidate = _candidate(tmp_path)
    output = Path(candidate["evidenceRoot"]) / "operator-attestation.json"
    displaced = output.with_name("displaced-owned-output.json")

    def replace_then_fail(*_args: object) -> bool:
        output.rename(displaced)
        output.write_bytes(b"unrelated replacement")
        return False

    monkeypatch.setattr(attestation, "_candidate_unchanged", replace_then_fail)

    before = _fd_count()
    result = _run(
        candidate_path, output,
        "--confirm-trusted-operator-account",
        "--confirm-untrusted-automation-stopped",
    )

    assert result != 0
    assert output.read_bytes() == b"unrelated replacement"
    assert displaced.read_bytes() == b""
    assert not _is_gate_valid(candidate, output)
    assert not _is_gate_valid(candidate, displaced)
    assert _fd_count() == before


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
