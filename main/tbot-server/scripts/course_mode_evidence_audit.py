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
    r"(?i)(child.?transcript|transcript|utterance|raw.?audio|audio.?data|authorization|bearer|token|secret|password|private.?key)"
)
MAX_FILE = 1024 * 1024
EVIDENCE_FIELDS = {
    "schemaVersion",
    "candidateId",
    "gate",
    "journeyId",
    "verdict",
    "capturedAt",
    "historical",
    "checksums",
    "anchors",
    "payload",
}
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
    return {
        "repositories": {name: value.get("sha") for name, value in candidate.get("repositories", {}).items()},
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
        return (
            set(payload) == {"lanes", "failedLane"}
            and payload["failedLane"] is None
            and isinstance(lanes, list)
            and bool(lanes)
            and all(isinstance(row, dict) and row.get("exitCode") == 0 for row in lanes)
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
    if not isinstance(candidate, dict) or Path(candidate.get("evidenceRoot", "")).resolve() != evidence_root.resolve():
        reasons.add("evidence.root")
    excluded = {path.resolve() for path in (exclude or set())}
    try:
        paths = sorted(path for path in evidence_root.rglob("*.json") if path.resolve() not in excluded)
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
        if not EVIDENCE_FIELDS.issubset(document) or document.get("schemaVersion") != 1:
            reasons.add("evidence.schema")
        documents.append(document)
        journey = document.get("journeyId")
        if not isinstance(journey, str) or not journey:
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
        if (
            not isinstance(checksums, dict)
            or not checksums
            or any(
                not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None
                for value in checksums.values()
            )
        ):
            reasons.add("evidence.checksums")
        if PRIVATE.search(json.dumps(document, sort_keys=True)):
            reasons.add("evidence.privacy")
        anchors = document.get("anchors")
        expected_anchors = _expected_anchors(candidate) if isinstance(candidate, dict) else {}
        if not isinstance(anchors, dict):
            reasons.add(f"evidence.anchor.{gate}")
        else:
            for name in ANCHORS[gate]:
                if anchors.get(name) != expected_anchors.get(name):
                    reasons.add(f"evidence.anchor.{gate}.{name}")
        if not _gate_payload_valid(gate, document.get("payload")):
            reasons.add(f"evidence.gate.schema.{gate}")
        payload = document.get("payload")
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
