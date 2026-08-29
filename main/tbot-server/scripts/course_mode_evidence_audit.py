#!/usr/bin/env python3
"""Aggregate candidate-bound G0-G10 evidence into bounded deterministic JSON."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

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
}


def _time(value: object):
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else None
    except ValueError:
        return None


def audit_evidence(
    candidate: object, evidence_root: Path, *, now: datetime | None = None, exclude: set[Path] | None = None
) -> dict[str, object]:
    reasons, documents, journey_counts = set(), [], {}
    candidate_id = candidate.get("candidateId") if isinstance(candidate, dict) else None
    now = now or datetime.now(timezone.utc)
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
            data = path.read_bytes()
            if len(data) > MAX_FILE:
                raise ValueError
            document = json.loads(data)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            reasons.add("evidence.input")
            continue
        sidecar = path.with_suffix(path.suffix + ".sha256")
        try:
            expected = sidecar.read_text(encoding="ascii").strip()
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
        if document.get("verdict") not in {"PASS", "FAIL", "BLOCKED", "SKIPPED"}:
            reasons.add("evidence.verdict")
        captured = _time(document.get("capturedAt"))
        created = _time(candidate.get("createdAt")) if isinstance(candidate, dict) else None
        if captured is None:
            reasons.add("evidence.timestamp")
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
    if any(count > 1 for count in journey_counts.values()):
        reasons.add("evidence.journey.duplicate")
    for gate in GATES:
        verdicts = {item.get("verdict") for item in documents if item.get("gate") == gate}
        if not verdicts:
            reasons.add(f"evidence.gate.missing.{gate}")
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
        candidate = json.loads(args.candidate.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
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
