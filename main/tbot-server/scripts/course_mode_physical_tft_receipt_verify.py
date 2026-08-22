#!/usr/bin/env python3
"""Validate a redacted Course Mode physical-TFT materializer receipt."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


EXPECTED = {
    "result": "pass",
    "deviceSuffix": "AC:20",
    "lessonKey": "course-mode-pilot-cat-ball",
    "lessonVersion": 1,
    "rendererId": "teebot-lesson-renderer.v4",
    "contractChecksum": "cf12b1a5f71f0a80a8ee22bb2cdc775ada5b803e26d154e5d29c76b14c9fb264",
    "layoutChecksum": "e61b56d1f8219a86c7f3986e7d5c70b91f512286604b5b206ef11e2c989d275c",
    "cueCount": 8,
    "conversationPresent": False,
}
EXPECTED_FIELDS = frozenset((*EXPECTED, "manifestChecksum"))
LOWER_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def validate_receipt(document: object) -> list[str]:
    if not isinstance(document, dict):
        return ["schema.not_object"]

    reasons: list[str] = []
    fields = set(document)
    for field in sorted(EXPECTED_FIELDS - fields):
        reasons.append(f"schema.missing_field.{field}")
    if fields - EXPECTED_FIELDS:
        reasons.append("schema.extra_field")

    for field, expected in EXPECTED.items():
        if field not in document:
            continue
        actual = document[field]
        expected_type = type(expected)
        if type(actual) is not expected_type or actual != expected:
            reasons.append(f"identity.{field}")

    manifest = document.get("manifestChecksum")
    if not isinstance(manifest, str) or not LOWER_SHA256.fullmatch(manifest):
        reasons.append("identity.manifestChecksum")

    return sorted(set(reasons))


def validate_receipt_pair(first: object, second: object | None) -> list[str]:
    reasons = validate_receipt(first)
    if second is not None:
        reasons.extend(validate_receipt(second))
        if isinstance(first, dict) and isinstance(second, dict) and first != second:
            reasons.append("rerun.semantic_mismatch")
    return sorted(set(reasons))


def _load(path: Path) -> tuple[object | None, str | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (OSError, UnicodeError):
        return None, "input.unreadable"
    except json.JSONDecodeError:
        return None, "input.invalid_json"


def _emit(payload: dict[str, object]) -> None:
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("receipt", type=Path)
    parser.add_argument("--rerun-receipt", type=Path)
    args = parser.parse_args(argv)

    first, first_error = _load(args.receipt)
    second: object | None = None
    second_error: str | None = None
    if args.rerun_receipt is not None:
        second, second_error = _load(args.rerun_receipt)

    reasons = [reason for reason in (first_error, second_error) if reason]
    if not reasons:
        reasons = validate_receipt_pair(first, second)
    if reasons:
        _emit({"reasons": sorted(set(reasons)), "valid": False})
        return 1

    _emit(
        {
            "cueCount": 8,
            "deviceSuffix": "AC:20",
            "lessonKey": "course-mode-pilot-cat-ball",
            "rendererId": "teebot-lesson-renderer.v4",
            "valid": True,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
