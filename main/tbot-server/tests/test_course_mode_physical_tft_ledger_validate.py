import hashlib
import sys
from copy import deepcopy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
pytest_plugins = ("test_course_mode_physical_tft_receipt_verify",)


@pytest.fixture
def ledger(tmp_path: Path, candidate: dict, receipt: dict) -> dict:
    path = tmp_path / "capture.png"
    path.write_bytes(b"redacted visual evidence")
    receipt["evidence"] = [{"path": "capture.png", "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}]
    return {
        "schemaVersion": 2,
        "candidateId": candidate["candidateId"],
        "gate": "G8",
        "journeyId": "physical-ac20-1",
        "verdict": "PASS",
        "capturedAt": "2026-08-30T00:00:00Z",
        "receipt": receipt,
        "evidence": receipt["evidence"],
        "notes": ["adult-attended", "redacted"],
    }


def test_ledger_is_candidate_bound_and_hash_valid(tmp_path: Path, candidate: dict, ledger: dict) -> None:
    from course_mode_physical_tft_ledger_validate import validate_ledger

    assert validate_ledger(ledger, candidate=candidate, repository_root=tmp_path)["valid"] is True
    wrong = deepcopy(ledger)
    wrong["candidateId"] = "wrong"
    assert "ledger.candidate" in validate_ledger(wrong, candidate=candidate, repository_root=tmp_path)["reasons"]
    wrong = deepcopy(ledger)
    wrong["evidence"][0]["sha256"] = "f" * 64
    assert "ledger.evidence.hash" in validate_ledger(wrong, candidate=candidate, repository_root=tmp_path)["reasons"]


def test_ledger_rejects_unsafe_private_or_contradictory_evidence(tmp_path: Path, candidate: dict, ledger: dict) -> None:
    from course_mode_physical_tft_ledger_validate import validate_ledger

    for path, reason in [
        ("../escape.png", "ledger.evidence.path"),
        ("child-transcript.json", "ledger.privacy"),
        ("raw-audio.wav", "ledger.privacy"),
    ]:
        changed = deepcopy(ledger)
        changed["evidence"][0]["path"] = path
        assert reason in validate_ledger(changed, candidate=candidate, repository_root=tmp_path)["reasons"]
    changed = deepcopy(ledger)
    changed["receipt"]["result"] = "FAIL"
    changed["receipt"]["database"]["terminalState"] = "FAILED"
    reasons = validate_ledger(changed, candidate=candidate, repository_root=tmp_path)["reasons"]
    assert "ledger.verdict" in reasons and "receipt.result" in reasons


@pytest.mark.parametrize(
    "content", [b"Authorization: Bearer do-not-echo", b"child transcript: private", b"RIFF\x00\x00\x00\x00WAVE"]
)
def test_ledger_scans_bounded_artifact_bytes_for_private_content(
    tmp_path: Path,
    candidate: dict,
    ledger: dict,
    content: bytes,
) -> None:
    from course_mode_physical_tft_ledger_validate import validate_ledger

    path = tmp_path / "capture.png"
    path.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    ledger["evidence"][0]["sha256"] = digest
    ledger["receipt"]["evidence"][0]["sha256"] = digest
    assert "ledger.privacy" in validate_ledger(ledger, candidate=candidate, repository_root=tmp_path)["reasons"]


def test_ledger_rejects_symlink_and_oversized_artifact(tmp_path: Path, candidate: dict, ledger: dict) -> None:
    from course_mode_physical_tft_ledger_validate import MAX_ARTIFACT_BYTES, validate_ledger

    capture = tmp_path / "capture.png"
    capture.unlink()
    target = tmp_path / "target.png"
    target.write_bytes(b"redacted")
    capture.symlink_to(target)
    assert "ledger.evidence.input" in validate_ledger(ledger, candidate=candidate, repository_root=tmp_path)["reasons"]

    capture.unlink()
    capture.write_bytes(b"x" * (MAX_ARTIFACT_BYTES + 1))
    assert "ledger.evidence.input" in validate_ledger(ledger, candidate=candidate, repository_root=tmp_path)["reasons"]
