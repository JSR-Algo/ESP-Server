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
sys.path.insert(0, str(ROOT / "tests"))
pytest_plugins = ("test_course_mode_physical_tft_receipt_verify",)
NOW = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)


def _anchors(candidate: dict) -> dict:
    expected = candidate["tools"]["physicalEvidence"]["identity"]
    return {
        "repositories": {name: value["sha"] for name, value in candidate["repositories"].items()},
        "images": candidate["images"],
        "firmware": candidate["firmware"],
        "course": candidate["course"],
        "lesson": expected["lesson"],
        "device": expected["device"],
        "journey": candidate["database"]["journey"],
        "database": candidate["database"]["terminalReadback"],
        "receipts": candidate["database"]["replacement"],
    }


def _payload(gate: str) -> dict:
    return {
        "G0": {"validator": "course-mode-candidate.v1", "status": "pass", "reasons": []},
        "G1": {"lanes": [{"name": "full", "exitCode": 0}], "failedLane": None},
        "G2": {"lessonCount": 26, "activityCount": 256, "migration": "PASS", "materialization": "PASS"},
        "G3": {"projects": ["chromium", "webkit"], "authz": "PASS", "result": "PASS"},
        "G4": {"operations": ["materialize", "cutover", "archive", "rollback"], "rollback": "PASS"},
        "G5": {"boundaries": ["admin-http", "postgres", "device-websocket"], "privateAdapterCalls": 0},
        "G6": {"builds": ["firmware-host", "firmware-hil"], "resourceBounded": True, "result": "PASS"},
        "G7": {"ledgerValidator": "course-mode-physical-ledger.v2", "runs": 26, "completionCount": 1, "result": "PASS"},
        "G8": {"signedIdentity": True, "redacted": True, "supplyChain": "PASS"},
        "G9": {"audit": "PASS", "openP0P1": 0, "result": "PASS"},
        "G10": {"rollback": "RESTORED", "protectedPartitionsPreserved": True},
    }[gate]


def _write(
    root: Path,
    candidate: dict,
    gate: str,
    *,
    journey=None,
    verdict="PASS",
    captured="2026-08-30T00:00:00Z",
    historical=False,
    mutate=None,
):
    document = {
        "schemaVersion": 1,
        "candidateId": candidate["candidateId"],
        "gate": gate,
        "journeyId": journey or f"journey-{gate}",
        "verdict": verdict,
        "capturedAt": captured,
        "historical": historical,
        "checksums": {"report": "a" * 64},
        "anchors": _anchors(candidate),
        "payload": _payload(gate),
    }
    if mutate:
        mutate(document)
    path = root / f"{gate}-{document['journeyId']}.json"
    data = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()
    path.write_bytes(data)
    path.with_suffix(path.suffix + ".sha256").write_text(hashlib.sha256(data).hexdigest() + "\n", encoding="ascii")
    return path


def _complete(root: Path, candidate: dict):
    return [_write(root, candidate, f"G{i}") for i in range(11)]


def _rewrite(path: Path, document: dict) -> None:
    data = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()
    path.write_bytes(data)
    path.with_suffix(path.suffix + ".sha256").write_text(hashlib.sha256(data).hexdigest() + "\n")


def test_gate_specific_candidate_bound_evidence_passes(tmp_path: Path, candidate: dict) -> None:
    from course_mode_evidence_audit import audit_evidence

    root = Path(candidate["evidenceRoot"])
    _complete(root, candidate)
    first = audit_evidence(candidate, root, now=NOW)
    assert first == audit_evidence(candidate, root, now=NOW)
    assert first["verdict"] == "PASS" and first["reasons"] == [], first


def test_common_envelope_without_actual_gate_payload_is_rejected(tmp_path: Path, candidate: dict) -> None:
    from course_mode_evidence_audit import audit_evidence

    root = Path(candidate["evidenceRoot"])
    _complete(root, candidate)
    path = next(root.glob("G5-*.json"))
    document = json.loads(path.read_text())
    document["payload"] = {"result": "PASS"}
    _rewrite(path, document)
    assert "evidence.gate.schema.G5" in audit_evidence(candidate, root, now=NOW)["reasons"]


@pytest.mark.parametrize(
    "mutation, reason",
    [
        (
            lambda root, candidate, paths: paths[0].with_suffix(paths[0].suffix + ".sha256").unlink(),
            "evidence.sidecar.missing",
        ),
        (lambda root, candidate, paths: paths[0].write_text(paths[0].read_text() + " "), "evidence.checksum"),
        (
            lambda root, candidate, paths: _write(root, candidate, "G10", journey="journey-G0"),
            "evidence.journey.duplicate",
        ),
        (
            lambda root, candidate, paths: _write(root, candidate, "G0", journey="other", verdict="FAIL"),
            "evidence.gate.cardinality.G0",
        ),
        (
            lambda root, candidate, paths: _write(
                root, candidate, "G7", journey="stale", captured="2026-08-20T00:00:00Z"
            ),
            "evidence.timestamp.stale",
        ),
        (
            lambda root, candidate, paths: _write(
                root, candidate, "G7", journey="naive", captured="2026-08-30T00:00:00"
            ),
            "evidence.timestamp.utc",
        ),
        (
            lambda root, candidate, paths: _write(
                root, candidate, "G7", journey="offset", captured="2026-08-30T07:00:00+07:00"
            ),
            "evidence.timestamp.utc",
        ),
        (
            lambda root, candidate, paths: _write(root, candidate, "G7", journey="historical", historical=True),
            "evidence.historical",
        ),
        (
            lambda root, candidate, paths: _write(
                root, candidate, "G7", journey="private", mutate=lambda d: d.update(transcript="do-not-echo")
            ),
            "evidence.privacy",
        ),
    ],
)
def test_auditor_rejects_invalid_evidence(tmp_path: Path, candidate: dict, mutation, reason: str) -> None:
    from course_mode_evidence_audit import audit_evidence

    root = Path(candidate["evidenceRoot"])
    paths = _complete(root, candidate)
    mutation(root, candidate, paths)
    assert reason in audit_evidence(candidate, root, now=NOW)["reasons"]


def test_auditor_rejects_anchor_drift_and_duplicate_completion(tmp_path: Path, candidate: dict) -> None:
    from course_mode_evidence_audit import audit_evidence

    root = Path(candidate["evidenceRoot"])
    _complete(root, candidate)
    path = next(root.glob("G7-*.json"))
    document = json.loads(path.read_text())
    document["anchors"]["firmware"]["applicationSha256"] = "f" * 64
    document["payload"]["completionCount"] = 2
    _rewrite(path, document)
    reasons = audit_evidence(candidate, root, now=NOW)["reasons"]
    assert "evidence.anchor.G7.firmware" in reasons and "evidence.completion.duplicate" in reasons


def test_cli_rejects_unsafe_output_without_leaking(tmp_path: Path, candidate: dict) -> None:
    root = Path(candidate["evidenceRoot"])
    _complete(root, candidate)
    _write(root, candidate, "G7", journey="secret", mutate=lambda d: d.update(transcript="do-not-echo"))
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(json.dumps(candidate))
    outside = tmp_path.parent / "unsafe-final-report.json"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--candidate",
            str(candidate_path),
            "--evidence-root",
            str(root),
            "--output",
            str(outside),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1 and "output.unsafe" in result.stdout and "do-not-echo" not in result.stdout
