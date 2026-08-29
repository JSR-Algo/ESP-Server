#!/usr/bin/env python3
"""Validate one candidate-bound physical evidence ledger offline."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

from course_mode_physical_tft_receipt_verify import validate_receipt

PRIVATE = re.compile(
    r"(?i)(child.?transcript|transcript|raw.?audio|audio\.(wav|mp3)|authorization|bearer|token|secret|password|private.?key)"
)
FIELDS = {"schemaVersion", "candidateId", "gate", "journeyId", "verdict", "capturedAt", "receipt", "evidence", "notes"}


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def validate_ledger(document: object, *, candidate: object, repository_root: Path) -> dict[str, object]:
    reasons = []
    if not isinstance(document, dict) or not isinstance(candidate, dict):
        reasons.append("ledger.schema")
    else:
        if set(document) != FIELDS or document.get("schemaVersion") != 2:
            reasons.append("ledger.schema")
        if document.get("candidateId") != candidate.get("candidateId"):
            reasons.append("ledger.candidate")
        if document.get("gate") != "G8" or not isinstance(document.get("journeyId"), str):
            reasons.append("ledger.identity")
        receipt = document.get("receipt")
        receipt_reasons = validate_receipt(receipt, candidate)
        reasons.extend(receipt_reasons)
        if document.get("verdict") != "PASS" or isinstance(receipt, dict) and receipt.get("result") != "PASS":
            reasons.append("ledger.verdict")
        evidence = document.get("evidence")
        if evidence != (receipt.get("evidence") if isinstance(receipt, dict) else None):
            reasons.append("ledger.evidence.binding")
        if not isinstance(evidence, list):
            reasons.append("ledger.evidence.schema")
        else:
            for item in evidence:
                raw = item.get("path") if isinstance(item, dict) else None
                if (
                    not isinstance(raw, str)
                    or Path(raw).is_absolute()
                    or not _inside(repository_root / raw, repository_root)
                ):
                    reasons.append("ledger.evidence.path")
                    continue
                if PRIVATE.search(raw):
                    reasons.append("ledger.privacy")
                path = repository_root / raw
                try:
                    digest = hashlib.sha256(path.read_bytes()).hexdigest()
                except OSError:
                    reasons.append("ledger.evidence.missing")
                    continue
                if item.get("sha256") != digest:
                    reasons.append("ledger.evidence.hash")
        if PRIVATE.search(json.dumps(document, sort_keys=True)):
            reasons.append("ledger.privacy")
    return {
        "candidateId": candidate.get("candidateId") if isinstance(candidate, dict) else None,
        "reasons": sorted(set(reasons)),
        "valid": not reasons,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("ledger", type=Path)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--repository-root", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = validate_ledger(
            json.loads(args.ledger.read_text()),
            candidate=json.loads(args.candidate.read_text()),
            repository_root=args.repository_root,
        )
    except (OSError, UnicodeError, json.JSONDecodeError):
        result = {"candidateId": None, "reasons": ["input.invalid"], "valid": False}
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
