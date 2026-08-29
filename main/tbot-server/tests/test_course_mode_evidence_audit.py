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


def _anchors(candidate: dict, gate: str) -> dict:
    expected = candidate["tools"]["physicalEvidence"]["identity"]
    anchors = {
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
    from course_mode_evidence_audit import ANCHORS

    return {name: anchors[name] for name in ANCHORS[gate]}


def _payload(gate: str) -> dict:
    return {
        "G0": {"validator": "course-mode-candidate.v1", "status": "pass", "reasons": []},
        "G1": {
            "lanes": [
                {"name": "backend-full", "exitCode": 0},
                {"name": "admin-full", "exitCode": 0},
                {"name": "esp-full", "exitCode": 0},
                {"name": "firmware-full", "exitCode": 0},
            ],
            "failedLane": None,
        },
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
    journey_id = journey or f"journey-{gate.lower()}"
    support = root / "artifacts" / f"{gate}-{journey_id}.log"
    support.parent.mkdir(parents=True, exist_ok=True)
    support.write_bytes(f"redacted {gate} evidence\n".encode())
    report = {
        "schemaVersion": 1,
        "candidateId": candidate["candidateId"],
        "gate": gate,
        "journeyId": journey_id,
        "capturedAt": captured,
        "anchors": _anchors(candidate, gate),
        "commands": [f"course-mode-{gate.lower()}-verify"],
        "timeline": [{"timestamp": captured, "event": "complete"}],
        "artifacts": [
            {"path": str(support.relative_to(root)), "sha256": hashlib.sha256(support.read_bytes()).hexdigest()}
        ],
        "payload": _payload(gate),
    }
    if mutate:
        mutate(report)
    report_path = root / "reports" / f"{gate}-{journey_id}.report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_data = (json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n").encode()
    report_path.write_bytes(report_data)
    report_path.with_suffix(report_path.suffix + ".sha256").write_text(
        hashlib.sha256(report_data).hexdigest() + "\n", encoding="ascii"
    )
    report_sha = hashlib.sha256(report_data).hexdigest()
    document = {
        "schemaVersion": 1,
        "candidateId": candidate["candidateId"],
        "gate": gate,
        "journeyId": journey_id,
        "verdict": verdict,
        "capturedAt": captured,
        "historical": historical,
        "checksums": {"report": report_sha},
        "artifacts": [{"type": f"{gate}.report", "path": str(report_path.relative_to(root)), "sha256": report_sha}],
    }
    path = root / f"{gate}-{document['journeyId']}.evidence.json"
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


def _report(root: Path, envelope: Path) -> tuple[Path, dict]:
    document = json.loads(envelope.read_text())
    path = root / document["artifacts"][0]["path"]
    return path, json.loads(path.read_text())


def _rewrite_report(root: Path, envelope: Path, report_path: Path, report: dict) -> None:
    _rewrite(report_path, report)
    report_sha = hashlib.sha256(report_path.read_bytes()).hexdigest()
    document = json.loads(envelope.read_text())
    document["checksums"]["report"] = report_sha
    document["artifacts"][0]["sha256"] = report_sha
    _rewrite(envelope, document)


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
    path = next(root.glob("G5-*.evidence.json"))
    report_path, document = _report(root, path)
    document["payload"] = {"result": "PASS"}
    _rewrite_report(root, path, report_path, document)
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
            lambda root, candidate, paths: _write(root, candidate, "G10", journey="journey-g0"),
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
    path = next(root.glob("G7-*.evidence.json"))
    report_path, document = _report(root, path)
    document["anchors"]["firmware"]["applicationSha256"] = "f" * 64
    document["payload"]["completionCount"] = 2
    _rewrite_report(root, path, report_path, document)
    reasons = audit_evidence(candidate, root, now=NOW)["reasons"]
    assert "evidence.anchor.G7.firmware" in reasons and "evidence.completion.duplicate" in reasons


@pytest.mark.parametrize(
    "mutate, reason",
    [
        (lambda report: report.update(commands=["unreviewed-command"]), "evidence.report.command.G5"),
        (lambda report: report.update(timeline=[]), "evidence.report.timeline.G5"),
        (lambda report: report.update(artifacts=[]), "evidence.report.artifacts.G5"),
    ],
)
def test_auditor_validates_actual_report_commands_timelines_and_artifacts(
    tmp_path: Path,
    candidate: dict,
    mutate,
    reason: str,
) -> None:
    from course_mode_evidence_audit import audit_evidence

    root = Path(candidate["evidenceRoot"])
    _complete(root, candidate)
    envelope = next(root.glob("G5-*.evidence.json"))
    report_path, report = _report(root, envelope)
    mutate(report)
    _rewrite_report(root, envelope, report_path, report)
    assert reason in audit_evidence(candidate, root, now=NOW)["reasons"]


def test_auditor_recomputes_referenced_report_hash(tmp_path: Path, candidate: dict) -> None:
    from course_mode_evidence_audit import audit_evidence

    root = Path(candidate["evidenceRoot"])
    _complete(root, candidate)
    envelope = next(root.glob("G5-*.evidence.json"))
    report_path, _ = _report(root, envelope)
    report_path.write_text(report_path.read_text() + " ")
    assert "evidence.artifact.hash.G5" in audit_evidence(candidate, root, now=NOW)["reasons"]


def test_auditor_scans_referenced_support_artifact_content(tmp_path: Path, candidate: dict) -> None:
    from course_mode_evidence_audit import audit_evidence

    root = Path(candidate["evidenceRoot"])
    _complete(root, candidate)
    envelope = next(root.glob("G5-*.evidence.json"))
    report_path, report = _report(root, envelope)
    support = root / report["artifacts"][0]["path"]
    support.write_bytes(b"RIFF\x00\x00\x00\x00WAVE")
    report["artifacts"][0]["sha256"] = hashlib.sha256(support.read_bytes()).hexdigest()
    _rewrite_report(root, envelope, report_path, report)
    assert "evidence.privacy" in audit_evidence(candidate, root, now=NOW)["reasons"]


def test_auditor_detects_headerless_mp3_sync_in_support_artifact(tmp_path: Path, candidate: dict) -> None:
    from course_mode_evidence_audit import audit_evidence

    root = Path(candidate["evidenceRoot"])
    _complete(root, candidate)
    envelope = next(root.glob("G5-*.evidence.json"))
    report_path, report = _report(root, envelope)
    support = root / report["artifacts"][0]["path"]
    support.write_bytes(b"\xff\xfb\x90\x64" + b"\x00" * 16)
    report["artifacts"][0]["sha256"] = hashlib.sha256(support.read_bytes()).hexdigest()
    _rewrite_report(root, envelope, report_path, report)
    assert "evidence.privacy" in audit_evidence(candidate, root, now=NOW)["reasons"]


def test_auditor_rejects_malformed_nested_anchors_without_traceback(tmp_path: Path, candidate: dict) -> None:
    from course_mode_evidence_audit import audit_evidence

    root = Path(candidate["evidenceRoot"])
    _complete(root, candidate)
    envelope = next(root.glob("G5-*.evidence.json"))
    report_path, report = _report(root, envelope)
    report["anchors"] = []
    _rewrite_report(root, envelope, report_path, report)
    result = audit_evidence(candidate, root, now=NOW)
    assert "evidence.anchor.G5" in result["reasons"]


def test_auditor_rejects_other_malformed_nested_values_without_traceback(tmp_path: Path, candidate: dict) -> None:
    from course_mode_evidence_audit import audit_evidence

    root = Path(candidate["evidenceRoot"])
    _complete(root, candidate)
    envelope = next(root.glob("G1-*.evidence.json"))
    report_path, report = _report(root, envelope)
    report["payload"]["lanes"] = [None]
    _rewrite_report(root, envelope, report_path, report)
    envelope = next(root.glob("G2-*.evidence.json"))
    document = json.loads(envelope.read_text())
    document["checksums"] = []
    _rewrite(envelope, document)
    result = audit_evidence(candidate, root, now=NOW)
    assert "evidence.gate.schema.G1" in result["reasons"]
    assert "evidence.checksums" in result["reasons"]


def test_auditor_rejects_malformed_candidate_without_traceback(tmp_path: Path) -> None:
    from course_mode_evidence_audit import audit_evidence

    malformed = {"candidateId": [], "evidenceRoot": [], "repositories": [], "tools": []}
    result = audit_evidence(malformed, tmp_path, now=NOW)
    assert result["verdict"] == "FAIL" and result["reasons"] == sorted(result["reasons"])


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
