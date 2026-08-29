import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/course_mode_evidence_audit.py"
sys.path.insert(0, str(ROOT / "scripts"))
NOW = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def candidate(tmp_path: Path) -> dict:
    return {
        "candidateId": "candidate-1",
        "createdAt": "2026-08-29T00:00:00Z",
        "expiresAt": "2026-09-05T00:00:00Z",
        "evidenceRoot": str(tmp_path),
    }


def _write(
    root: Path,
    gate: str,
    *,
    candidate_id="candidate-1",
    journey=None,
    verdict="PASS",
    captured="2026-08-30T00:00:00Z",
    historical=False,
    extra=None,
):
    document = {
        "schemaVersion": 1,
        "candidateId": candidate_id,
        "gate": gate,
        "journeyId": journey or f"journey-{gate}",
        "verdict": verdict,
        "capturedAt": captured,
        "historical": historical,
        "checksums": {"report": "a" * 64},
    }
    if extra:
        document.update(extra)
    path = root / f"{gate}-{document['journeyId']}.json"
    data = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()
    path.write_bytes(data)
    path.with_suffix(path.suffix + ".sha256").write_text(hashlib.sha256(data).hexdigest() + "\n", encoding="ascii")
    return path


def _complete(root: Path, *, captured="2026-08-30T00:00:00Z"):
    return [_write(root, f"G{i}", captured=captured) for i in range(11)]


def test_complete_candidate_bound_evidence_passes_deterministically(tmp_path: Path, candidate: dict) -> None:
    from course_mode_evidence_audit import audit_evidence

    _complete(tmp_path)
    first = audit_evidence(candidate, tmp_path, now=NOW)
    assert first == audit_evidence(candidate, tmp_path, now=NOW)
    assert first == {
        "candidateId": "candidate-1",
        "gates": [f"G{i}" for i in range(11)],
        "reasons": [],
        "schemaVersion": 1,
        "verdict": "PASS",
    }


@pytest.mark.parametrize(
    "mutation, reason",
    [
        (lambda root, paths: paths[0].with_suffix(paths[0].suffix + ".sha256").unlink(), "evidence.sidecar.missing"),
        (lambda root, paths: paths[0].write_text(paths[0].read_text() + " "), "evidence.checksum"),
        (lambda root, paths: _write(root, "G10", journey="journey-G0"), "evidence.journey.duplicate"),
        (
            lambda root, paths: _write(root, "G0", journey="contradiction", verdict="FAIL"),
            "evidence.verdict.contradictory",
        ),
        (lambda root, paths: _write(root, "G9", journey="wrong", candidate_id="other"), "evidence.candidate"),
        (
            lambda root, paths: _write(root, "G9", journey="stale", captured="2026-08-20T00:00:00Z"),
            "evidence.timestamp.stale",
        ),
        (lambda root, paths: _write(root, "G9", journey="historical", historical=True), "evidence.historical"),
        (lambda root, paths: _write(root, "G9", journey="no-checksum", extra={"checksums": {}}), "evidence.checksums"),
        (
            lambda root, paths: _write(root, "G9", journey="child", extra={"childTranscript": "private words"}),
            "evidence.privacy",
        ),
        (lambda root, paths: _write(root, "G9", journey="audio", extra={"rawAudio": "bytes"}), "evidence.privacy"),
    ],
)
def test_auditor_rejects_invalid_evidence(tmp_path: Path, candidate: dict, mutation, reason: str) -> None:
    from course_mode_evidence_audit import audit_evidence

    paths = _complete(tmp_path)
    mutation(tmp_path, paths)
    assert reason in audit_evidence(candidate, tmp_path, now=NOW)["reasons"]


def test_cli_rejects_unsafe_output_without_leaking_private_content(tmp_path: Path, candidate: dict) -> None:
    _complete(tmp_path)
    _write(tmp_path, "G9", journey="secret", extra={"transcript": "do-not-echo"})
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
    outside = tmp_path.parent / "unsafe-final-report.json"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--candidate",
            str(candidate_path),
            "--evidence-root",
            str(tmp_path),
            "--output",
            str(outside),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1 and "output.unsafe" in result.stdout
    assert "do-not-echo" not in result.stdout and len(result.stdout) < 4096


def test_cli_can_repeat_without_treating_its_output_as_evidence(tmp_path: Path, candidate: dict) -> None:
    current = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    _complete(tmp_path, captured=current)
    candidate["createdAt"] = current
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
    output = tmp_path / "final-report.json"
    command = [
        sys.executable,
        str(SCRIPT),
        "--candidate",
        str(candidate_path),
        "--evidence-root",
        str(tmp_path),
        "--output",
        str(output),
    ]
    first = subprocess.run(command, capture_output=True, text=True)
    second = subprocess.run(command, capture_output=True, text=True)
    assert first.returncode == second.returncode == 0
    assert first.stdout == second.stdout
