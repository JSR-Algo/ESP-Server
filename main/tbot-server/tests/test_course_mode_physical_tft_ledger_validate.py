import hashlib
import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/course_mode_physical_tft_ledger_validate.py"
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
        "journeyId": receipt["journey"]["deliveryId"],
        "verdict": "PASS",
        "capturedAt": "2026-08-29T12:00:00Z",
        "receipt": receipt,
        "evidence": receipt["evidence"],
        "notes": ["adult-attended", "redacted"],
    }


def test_ledger_is_candidate_bound_and_hash_valid(tmp_path: Path, candidate: dict, ledger: dict) -> None:
    from datetime import datetime, timezone

    from course_mode_physical_tft_ledger_validate import validate_ledger

    # The validator refuses a capture older than seven days, so pin the clock to the
    # fixture's own capturedAt rather than let the assertion rot with the calendar.
    now = datetime(2026, 8, 29, 13, tzinfo=timezone.utc)
    assert validate_ledger(ledger, candidate=candidate, repository_root=tmp_path, now=now)["valid"] is True
    wrong = deepcopy(ledger)
    wrong["candidateId"] = "wrong"
    assert "ledger.candidate" in validate_ledger(wrong, candidate=candidate, repository_root=tmp_path, now=now)["reasons"]
    wrong = deepcopy(ledger)
    wrong["evidence"][0]["sha256"] = "f" * 64
    assert "ledger.evidence.hash" in validate_ledger(wrong, candidate=candidate, repository_root=tmp_path, now=now)["reasons"]


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


@pytest.mark.parametrize(
    "content", [b"utterance: private", b"raw speech bytes", b"u\x00t\x00t\x00e\x00r\x00a\x00n\x00c\x00e"]
)
def test_ledger_rejects_utterance_and_raw_speech_content(
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
    candidate["tools"]["physicalEvidence"]["identity"]["evidenceArtifacts"]["capture.png"] = digest
    assert "ledger.privacy" in validate_ledger(ledger, candidate=candidate, repository_root=tmp_path)["reasons"]


def test_ledger_requires_fresh_strict_utc_timestamp(tmp_path: Path, candidate: dict, ledger: dict) -> None:
    from datetime import datetime, timezone

    from course_mode_physical_tft_ledger_validate import validate_ledger

    ledger["capturedAt"] = "2026-08-30T00:00:00"
    reasons = validate_ledger(
        ledger, candidate=candidate, repository_root=tmp_path, now=datetime(2026, 8, 30, 12, tzinfo=timezone.utc)
    )["reasons"]
    assert "ledger.timestamp.utc" in reasons


def test_ledger_binds_journey_and_timestamp_to_receipt(tmp_path: Path, candidate: dict, ledger: dict) -> None:
    from course_mode_physical_tft_ledger_validate import validate_ledger

    ledger["journeyId"] = "other-delivery"
    ledger["capturedAt"] = "2026-08-29T12:00:01Z"
    reasons = validate_ledger(ledger, candidate=candidate, repository_root=tmp_path)["reasons"]
    assert "ledger.journey" in reasons and "ledger.timestamp.binding" in reasons


def test_ledger_detects_headerless_mp3_sync(tmp_path: Path, candidate: dict, ledger: dict) -> None:
    from course_mode_physical_tft_ledger_validate import validate_ledger

    content = b"\xff\xfb\x90\x64" + b"\x00" * 16
    path = tmp_path / "capture.png"
    path.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    ledger["evidence"][0]["sha256"] = digest
    ledger["receipt"]["evidence"][0]["sha256"] = digest
    assert "ledger.privacy" in validate_ledger(ledger, candidate=candidate, repository_root=tmp_path)["reasons"]


@pytest.mark.parametrize(
    "content",
    [
        b"\x00\x00\x00\x18ftypM4A \x00\x00\x00\x00M4A isom",
        b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00isommp42"
        + b"\x00\x00\x00\x14hdlr\x00\x00\x00\x00\x00\x00\x00\x00soun",
        b"ADIF" + b"\x00" * 16,
        b"\x56\xe0" + b"\x00" * 16,
    ],
)
def test_ledger_detects_common_aac_containers(tmp_path: Path, candidate: dict, ledger: dict, content: bytes) -> None:
    from course_mode_physical_tft_ledger_validate import validate_ledger

    path = tmp_path / "capture.png"
    path.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    ledger["evidence"][0]["sha256"] = digest
    ledger["receipt"]["evidence"][0]["sha256"] = digest
    assert "ledger.privacy" in validate_ledger(ledger, candidate=candidate, repository_root=tmp_path)["reasons"]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda ledger, candidate: ledger["receipt"].update(lesson="truthy-not-a-record"),
        lambda ledger, candidate: candidate["repositories"].update(adminEsp="truthy-not-a-record"),
        lambda ledger, candidate: candidate["repositories"]["adminEsp"].update(dirtyExceptions=1),
        lambda ledger, candidate: ledger.update(evidence=[{"path": "capture.png", "sha256": []}]),
        lambda ledger, candidate: ledger.update(receipt=[]),
    ],
)
def test_ledger_nested_fuzz_matrix_returns_sorted_reasons_without_traceback(
    tmp_path: Path, candidate: dict, ledger: dict, mutation
) -> None:
    from course_mode_physical_tft_ledger_validate import validate_ledger

    mutation(ledger, candidate)
    result = validate_ledger(ledger, candidate=candidate, repository_root=tmp_path)
    assert result["reasons"] == sorted(set(result["reasons"])) and result["valid"] is False


def test_malformed_ledger_cli_is_deterministic_bounded_and_no_traceback(
    tmp_path: Path, candidate: dict, ledger: dict
) -> None:
    candidate["repositories"]["adminEsp"] = "truthy-not-a-record"
    candidate_path, ledger_path = tmp_path / "candidate.json", tmp_path / "ledger.json"
    candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
    command = [
        sys.executable,
        str(SCRIPT),
        str(ledger_path),
        "--candidate",
        str(candidate_path),
        "--repository-root",
        str(tmp_path),
    ]
    first = subprocess.run(command, capture_output=True, text=True)
    second = subprocess.run(command, capture_output=True, text=True)
    assert first.returncode == 1 and first.stdout == second.stdout and not first.stderr and not second.stderr
    result = json.loads(first.stdout)
    assert result["valid"] is False and result["reasons"] == sorted(set(result["reasons"]))
    assert len(first.stdout) < 2048
