#!/usr/bin/env python3
"""Aggregate candidate-bound G0-G10 evidence into bounded deterministic JSON."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from course_mode_candidate_manifest import (
    MAX_CANDIDATE_BYTES,
    _parse_rfc3339_utc,
    read_secure_regular,
    strict_json_loads,
    validate_candidate,
)
from course_mode_physical_tft_receipt_verify import _physical_identity

GATES = [f"G{i}" for i in range(11)]
PRIVATE = re.compile(
    r"(?i)(child.?transcript|transcript|utterance|raw.?speech|raw.?audio|audio.?data|authorization|bearer|token|secret|password|private.?key)"
)
MAX_FILE = 1024 * 1024
AUDIO_MAGIC = (b"RIFF", b"ID3", b"OggS", b"fLaC")
EVIDENCE_FIELDS = {
    "schemaVersion",
    "candidateId",
    "gate",
    "journeyId",
    "verdict",
    "capturedAt",
    "historical",
    "checksums",
    "artifacts",
}
REPORT_FIELDS = {
    "schemaVersion",
    "candidateId",
    "gate",
    "journeyId",
    "capturedAt",
    "anchors",
    "commands",
    "timeline",
    "artifacts",
    "payload",
}
COMMANDS = {gate: f"course-mode-{gate.lower()}-verify" for gate in GATES}
JOURNEY_ID = re.compile(r"^[a-z0-9][a-z0-9._:-]{2,127}$")
ANCHORS = {
    "G0": {"repositories", "course"},
    "G1": {"repositories", "images", "firmware", "course"},
    "G2": {"repositories", "course", "lesson", "database", "receipts"},
    "G3": {"repositories", "images", "course", "lesson"},
    "G4": {"repositories", "course", "lesson", "database", "receipts"},
    "G5": {"repositories", "images", "firmware", "course", "lesson", "journey", "database"},
    "G6": {"repositories", "firmware", "course", "lesson", "journey", "database"},
    "G7": {"repositories", "images", "firmware", "course", "lesson", "device", "journey", "database", "receipts"},
    "G8": {"repositories", "images", "firmware"},
    "G9": {"repositories", "images", "firmware", "course", "lesson", "journey", "database", "receipts"},
    "G10": {"repositories", "images", "firmware", "course", "lesson", "device", "receipts"},
}


def _expected_anchors(candidate: dict) -> dict[str, object]:
    physical = _physical_identity(candidate)
    if not isinstance(physical, dict):
        physical = {}
    repositories = candidate.get("repositories")
    repositories = repositories if isinstance(repositories, dict) else {}
    return {
        "repositories": {name: value.get("sha") for name, value in repositories.items() if isinstance(value, dict)},
        "images": {"backend": physical.get("backendImage")},
        "firmware": physical.get("firmware"),
        "course": candidate.get("course"),
        "lesson": physical.get("lesson"),
        "device": physical.get("device"),
        "journey": physical.get("journey"),
        "database": physical.get("database"),
        "receipts": physical.get("replacement"),
    }


def _gate_payload_valid(gate: str, payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    if gate == "G0":
        return payload == {"validator": "course-mode-candidate.v1", "status": "pass", "reasons": []}
    if gate == "G1":
        lanes = payload.get("lanes")
        expected_lanes = ["backend-full", "admin-full", "esp-full", "firmware-full"]
        return (
            set(payload) == {"lanes", "failedLane"}
            and payload["failedLane"] is None
            and isinstance(lanes, list)
            and len(lanes) == len(expected_lanes)
            and all(isinstance(row, dict) for row in lanes)
            and [row.get("name") for row in lanes] == expected_lanes
            and all(set(row) == {"name", "exitCode"} and row.get("exitCode") == 0 for row in lanes)
        )
    if gate == "G2":
        return payload == {"lessonCount": 26, "activityCount": 256, "migration": "PASS", "materialization": "PASS"}
    if gate == "G3":
        return payload == {"projects": ["chromium", "webkit"], "authz": "PASS", "result": "PASS"}
    if gate == "G4":
        return payload == {"operations": ["materialize", "cutover", "archive", "rollback"], "rollback": "PASS"}
    if gate == "G5":
        return payload == {"boundaries": ["admin-http", "postgres", "device-websocket"], "privateAdapterCalls": 0}
    if gate == "G6":
        return payload == {"builds": ["firmware-host", "firmware-hil"], "resourceBounded": True, "result": "PASS"}
    if gate == "G7":
        return payload == {
            "ledgerValidator": "course-mode-physical-ledger.v2",
            "runs": 26,
            "completionCount": 1,
            "result": "PASS",
        }
    if gate == "G8":
        return payload == {"signedIdentity": True, "redacted": True, "supplyChain": "PASS"}
    if gate == "G9":
        return payload == {"audit": "PASS", "openP0P1": 0, "result": "PASS"}
    if gate == "G10":
        return payload == {"rollback": "RESTORED", "protectedPartitionsPreserved": True}
    return False


def audit_evidence(
    candidate: object, evidence_root: Path, *, now: datetime | None = None, exclude: set[Path] | None = None
) -> dict[str, object]:
    reasons, documents, journey_counts = set(), [], {}
    candidate_id = candidate.get("candidateId") if isinstance(candidate, dict) else None
    now = now or datetime.now(timezone.utc)
    if isinstance(candidate, dict):
        reasons.update(f"candidate.{reason}" for reason in validate_candidate(candidate, now=now))
    evidence_value = candidate.get("evidenceRoot") if isinstance(candidate, dict) else None
    if not isinstance(evidence_value, str) or Path(evidence_value).resolve() != evidence_root.resolve():
        reasons.add("evidence.root")
    excluded = {path.resolve() for path in (exclude or set())}
    try:
        paths = sorted(path for path in evidence_root.rglob("*.evidence.json") if path.resolve() not in excluded)
    except OSError:
        paths = []
        reasons.add("evidence.root")
    for path in paths:
        try:
            data = read_secure_regular(path, MAX_FILE)
            document = strict_json_loads(data)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            reasons.add("evidence.input")
            continue
        sidecar = path.with_suffix(path.suffix + ".sha256")
        try:
            expected = read_secure_regular(sidecar, 128).decode("ascii").strip()
        except OSError:
            reasons.add("evidence.sidecar.missing")
        else:
            if expected != hashlib.sha256(data).hexdigest():
                reasons.add("evidence.checksum")
        if not isinstance(document, dict):
            reasons.add("evidence.schema")
            continue
        if set(document) != EVIDENCE_FIELDS or document.get("schemaVersion") != 1:
            reasons.add("evidence.schema")
        documents.append(document)
        journey = document.get("journeyId")
        if not isinstance(journey, str) or JOURNEY_ID.fullmatch(journey) is None:
            reasons.add("evidence.journey")
        journey_counts[journey] = journey_counts.get(journey, 0) + 1
        if document.get("candidateId") != candidate_id:
            reasons.add("evidence.candidate")
        if document.get("gate") not in GATES:
            reasons.add("evidence.gate")
            continue
        gate = document["gate"]
        if document.get("verdict") not in {"PASS", "FAIL", "BLOCKED", "SKIPPED"}:
            reasons.add("evidence.verdict")
        captured = _parse_rfc3339_utc(document.get("capturedAt"))
        created = _parse_rfc3339_utc(candidate.get("createdAt")) if isinstance(candidate, dict) else None
        if captured is None:
            reasons.add("evidence.timestamp.utc")
        elif (created and captured < created) or (now - captured).total_seconds() > 7 * 86400 or captured > now:
            reasons.add("evidence.timestamp.stale")
        if document.get("historical") is not False:
            reasons.add("evidence.historical")
        checksums = document.get("checksums")
        checksums_valid = (
            isinstance(checksums, dict)
            and set(checksums) == {"report"}
            and all(
                isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None
                for value in checksums.values()
            )
        )
        if not checksums_valid:
            reasons.add("evidence.checksums")
        safe_checksums = checksums if isinstance(checksums, dict) else {}
        if PRIVATE.search(json.dumps(document, sort_keys=True)):
            reasons.add("evidence.privacy")
        artifacts = document.get("artifacts")
        report = None
        if not isinstance(artifacts, list) or len(artifacts) != 1 or not isinstance(artifacts[0], dict):
            reasons.add(f"evidence.artifact.cardinality.{gate}")
        else:
            artifact = artifacts[0]
            relative = artifact.get("path")
            expected_sha = artifact.get("sha256")
            if (
                set(artifact) != {"type", "path", "sha256"}
                or artifact.get("type") != f"{gate}.report"
                or not isinstance(relative, str)
                or Path(relative).is_absolute()
                or ".." in Path(relative).parts
            ):
                reasons.add(f"evidence.artifact.schema.{gate}")
            else:
                report_path = evidence_root / relative
                try:
                    report_bytes = read_secure_regular(report_path, MAX_FILE)
                    report = strict_json_loads(report_bytes)
                except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
                    reasons.add(f"evidence.artifact.input.{gate}")
                else:
                    actual_sha = hashlib.sha256(report_bytes).hexdigest()
                    if expected_sha != actual_sha or safe_checksums.get("report") != actual_sha:
                        reasons.add(f"evidence.artifact.hash.{gate}")
                    report_sidecar = report_path.with_suffix(report_path.suffix + ".sha256")
                    try:
                        report_sidecar_sha = read_secure_regular(report_sidecar, 128).decode("ascii").strip()
                    except (OSError, UnicodeError):
                        reasons.add(f"evidence.artifact.sidecar.{gate}")
                    else:
                        if report_sidecar_sha != actual_sha:
                            reasons.add(f"evidence.artifact.hash.{gate}")
                    decoded = report_bytes.decode("utf-8", errors="ignore")
                    if PRIVATE.search(decoded) or PRIVATE.search(decoded.replace("\x00", "")):
                        reasons.add("evidence.privacy")
        payload = None
        if not isinstance(report, dict) or set(report) != REPORT_FIELDS:
            reasons.add(f"evidence.report.schema.{gate}")
        else:
            if (
                report.get("schemaVersion") != 1
                or report.get("candidateId") != candidate_id
                or report.get("gate") != gate
                or report.get("journeyId") != journey
                or report.get("capturedAt") != document.get("capturedAt")
            ):
                reasons.add(f"evidence.report.identity.{gate}")
            if report.get("commands") != [COMMANDS[gate]]:
                reasons.add(f"evidence.report.command.{gate}")
            timeline = report.get("timeline")
            if (
                not isinstance(timeline, list)
                or len(timeline) != 1
                or timeline[0] != {"timestamp": document.get("capturedAt"), "event": "complete"}
            ):
                reasons.add(f"evidence.report.timeline.{gate}")
            anchors = report.get("anchors")
            expected_anchors = _expected_anchors(candidate) if isinstance(candidate, dict) else {}
            if not isinstance(anchors, dict) or set(anchors) != ANCHORS[gate]:
                reasons.add(f"evidence.anchor.{gate}")
            else:
                for name in ANCHORS[gate]:
                    if anchors.get(name) != expected_anchors.get(name):
                        reasons.add(f"evidence.anchor.{gate}.{name}")
            support = report.get("artifacts")
            if not isinstance(support, list) or not support:
                reasons.add(f"evidence.report.artifacts.{gate}")
            else:
                for item in support:
                    relative = item.get("path") if isinstance(item, dict) else None
                    if (
                        not isinstance(item, dict)
                        or set(item) != {"path", "sha256"}
                        or not isinstance(relative, str)
                        or Path(relative).is_absolute()
                        or ".." in Path(relative).parts
                    ):
                        reasons.add(f"evidence.report.artifacts.{gate}")
                        continue
                    try:
                        support_bytes = read_secure_regular(evidence_root / relative, MAX_FILE)
                    except OSError:
                        reasons.add(f"evidence.report.artifacts.{gate}")
                        continue
                    if hashlib.sha256(support_bytes).hexdigest() != item.get("sha256"):
                        reasons.add(f"evidence.report.artifacts.{gate}")
                    support_text = support_bytes.decode("utf-8", errors="ignore")
                    if PRIVATE.search(support_text) or PRIVATE.search(support_text.replace("\x00", "")):
                        reasons.add("evidence.privacy")
                    if support_bytes.startswith(AUDIO_MAGIC):
                        reasons.add("evidence.privacy")
                    if len(support_bytes) >= 2 and support_bytes[0] == 0xFF and support_bytes[1] & 0xE0 == 0xE0:
                        reasons.add("evidence.privacy")
            payload = report.get("payload")
        if not _gate_payload_valid(gate, payload):
            reasons.add(f"evidence.gate.schema.{gate}")
        if isinstance(payload, dict) and (
            payload.get("completionCount", 1) != 1 or payload.get("duplicateCompletions", 0) != 0
        ):
            reasons.add("evidence.completion.duplicate")
    if any(count > 1 for count in journey_counts.values()):
        reasons.add("evidence.journey.duplicate")
    for gate in GATES:
        gate_documents = [item for item in documents if item.get("gate") == gate]
        verdicts = {item.get("verdict") for item in gate_documents}
        if not verdicts:
            reasons.add(f"evidence.gate.missing.{gate}")
        if len(gate_documents) != 1:
            reasons.add(f"evidence.gate.cardinality.{gate}")
        if len(verdicts) > 1:
            reasons.add("evidence.verdict.contradictory")
        if verdicts and verdicts != {"PASS"}:
            reasons.add("evidence.verdict")
    return {
        "candidateId": candidate_id,
        "gates": GATES,
        "reasons": sorted(reasons),
        "schemaVersion": 1,
        "verdict": "PASS" if not reasons else "FAIL",
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--evidence-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        candidate = strict_json_loads(read_secure_regular(args.candidate, MAX_CANDIDATE_BYTES))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        candidate = {}
    root = args.evidence_root.resolve()
    output = args.output.resolve()
    result = audit_evidence(candidate, root, exclude={output, args.candidate})
    try:
        output.relative_to(root)
    except ValueError:
        result["reasons"] = sorted(set(result["reasons"] + ["output.unsafe"]))
        result["verdict"] = "FAIL"
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
