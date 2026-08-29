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
