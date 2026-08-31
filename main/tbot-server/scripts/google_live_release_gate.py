"""Read-only exact-candidate Google Live release evidence aggregation."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.analyze_google_live_log import correlate_websocket_bargein_evidence
from scripts.google_live_reliability import (
    SCHEMA_VERSION,
    forbidden_report_fields,
    validate_candidate_soak_report,
    validate_log_reliability_contract,
    validate_real_api_pass_report,
)
from scripts.physical_smoke_audit import validate_physical_candidate_report

RELEASE_SCHEMA_VERSION = "google-live-release-verdict.v1"
REQUIRED_LAYERS = (
    "deterministic",
    "server_regression",
    "real_api",
    "websocket_e2e",
    "physical",
    "candidate_soak",
)
IDENTITY_FIELDS = (
    "gitSha",
    "imageDigest",
    "firmwareIdentity",
    "configFingerprint",
    "fixtureSha256",
)
SHA256 = re.compile(r"[0-9a-f]{64}")
TAGGED_SHA256 = re.compile(r"sha256:[0-9a-f]{64}")


def _failure(code: str, layer: str | None = None, field: str | None = None) -> dict:
    result = {"code": code}
    if layer is not None:
        result["layer"] = layer
    if field is not None:
        result["field"] = field
    return result


def _identity_failures(identity: Any, expected: Mapping[str, Any], layer: str) -> list[dict]:
    if not isinstance(identity, Mapping) or set(identity) != set(IDENTITY_FIELDS):
        return [_failure("CANDIDATE_IDENTITY_INVALID", layer)]
    return [
        _failure("CANDIDATE_IDENTITY_MISMATCH", layer, field)
        for field in IDENTITY_FIELDS
        if type(identity.get(field)) is not str or identity.get(field) != expected.get(field)
    ]


def _generic_report_valid(report: Any, name: str) -> bool:
    return (
        isinstance(report, Mapping)
        and report.get("schemaVersion") == SCHEMA_VERSION
        and report.get("name") == name
        and report.get("status") == "PASS"
        and type(report.get("status")) is str
        and report.get("failures") == []
        and type(report.get("failures")) is list
    )


def _deterministic_valid(report: Any) -> bool:
    if not _generic_report_valid(report, "deterministic"):
        return False
    verdict = report.get("testVerdict")
    return (
        isinstance(verdict, Mapping)
        and verdict.get("status") == "PASS"
        and type(verdict.get("total")) is int
        and verdict.get("total") > 0
        and verdict.get("failed") == 0
        and type(verdict.get("failed")) is int
        and verdict.get("skipped") == 0
        and type(verdict.get("skipped")) is int
        and verdict.get("failures") == []
    )


def _server_regression_valid(report: Any) -> bool:
    if not isinstance(report, Mapping):
        return False
    return not validate_log_reliability_contract(
        report,
        expected_candidate_identity=report.get("candidateIdentity", {}),
        expected_log_window=report.get("logWindow"),
        expected_evidence_scope=report.get("evidenceScope"),
    )


def _websocket_valid(report: Any) -> bool:
    if not _generic_report_valid(report, "websocket_e2e"):
        return False
    transport = report.get("transportEvidence")
    log_evidence = report.get("logEvidence")
    stored_correlated = report.get("correlatedEvidence")
    if not all(
        isinstance(value, Mapping)
        for value in (transport, log_evidence, stored_correlated)
    ):
        return False
    expected_identity = report.get("candidateIdentity")
    recomputed = correlate_websocket_bargein_evidence(
        transport,
        log_evidence,
        expected_candidate_identity=expected_identity,
    )
    return (
        transport.get("candidateIdentity") == expected_identity
        and log_evidence.get("candidateIdentity") == expected_identity
        and stored_correlated.get("candidateIdentity") == expected_identity
        and recomputed.get("status") == "PASS"
        and recomputed.get("aggregateReleaseEligible") is True
        and stored_correlated == recomputed
    )


def _physical_valid(report: Any, expected_identity: Mapping[str, Any]) -> bool:
    return (
        _generic_report_valid(report, "physical")
        and set(report)
        == {
            "schemaVersion",
            "name",
            "status",
            "candidateIdentity",
            "auditReport",
            "productionProfile",
            "logEvidence",
            "candidateSoakEvidence",
            "failures",
        }
        and not validate_physical_candidate_report(
            report.get("auditReport"),
            expected_candidate_identity=expected_identity,
            reliability_report=report.get("logEvidence"),
            candidate_soak_report=report.get("candidateSoakEvidence"),
            production_profile=report.get("productionProfile"),
        )
    )


def _layer_valid(layer: str, report: Any, expected_identity: Mapping[str, Any]) -> bool:
    validators = {
        "deterministic": _deterministic_valid,
        "server_regression": _server_regression_valid,
        "real_api": lambda value: not validate_real_api_pass_report(
            value, expected_candidate_identity=expected_identity
        ),
        "websocket_e2e": _websocket_valid,
        "physical": lambda value: _physical_valid(value, expected_identity),
        "candidate_soak": lambda value: not validate_candidate_soak_report(
            value, expected_candidate_identity=expected_identity
        ),
    }
    return validators[layer](report) and not forbidden_report_fields(report)


def validate_expected_identity(identity: Mapping[str, Any]) -> None:
    if set(identity) != set(IDENTITY_FIELDS):
        raise ValueError("candidate identity must contain every exact identity field")
    if any(type(identity[field]) is not str or not identity[field].strip() for field in IDENTITY_FIELDS):
        raise ValueError("candidate identity fields must be non-empty strings")
    if TAGGED_SHA256.fullmatch(identity["imageDigest"]) is None:
        raise ValueError("image digest must be lowercase sha256:<64 hex>")
    if TAGGED_SHA256.fullmatch(identity["configFingerprint"]) is None:
        raise ValueError("config fingerprint must be lowercase sha256:<64 hex>")
    if SHA256.fullmatch(identity["fixtureSha256"]) is None:
        raise ValueError("fixture checksum must be 64 lowercase hex characters")


def load_checksum_manifest(
    manifest_path: Path | str, layer_paths: Mapping[str, Path | str]
) -> dict[str, str]:
    """Map trusted GNU sha256sum rows to the exact required report paths."""
    manifest = Path(manifest_path).resolve()
    root = manifest.parent
    expected = {name: Path(path).resolve() for name, path in layer_paths.items()}
    by_path = {path: name for name, path in expected.items()}
    result = {}
    seen_paths = set()
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        if match is None:
            raise ValueError("checksum manifest row is malformed")
        candidate = (root / match.group(2)).resolve()
        if root not in candidate.parents:
            raise ValueError("checksum manifest path escapes its evidence root")
        if candidate not in by_path:
            continue
        if candidate in seen_paths:
            raise ValueError("checksum manifest must name each exact report once")
        seen_paths.add(candidate)
        result[by_path[candidate]] = match.group(1)
    return result


def aggregate_release_evidence(
    expected_identity: Mapping[str, Any],
    layer_paths: Mapping[str, Path | str],
    expected_checksums: Mapping[str, str],
) -> dict[str, Any]:
    """Read and validate all required reports without executing any journey."""
    failures = []
    layers = []
    try:
        validate_expected_identity(expected_identity)
    except ValueError:
        failures.append(_failure("EXPECTED_CANDIDATE_IDENTITY_INVALID"))
    for name in sorted(set(layer_paths) - set(REQUIRED_LAYERS)):
        failures.append(_failure("UNEXPECTED_LAYER", name))

    for layer in REQUIRED_LAYERS:
        path_value = layer_paths.get(layer)
        expected_checksum = expected_checksums.get(layer)
        layer_failures = []
        if type(expected_checksum) is not str or SHA256.fullmatch(expected_checksum) is None:
            layer_failures.append(_failure("CHECKSUM_MISSING", layer))
        if path_value is None:
            layer_failures.append(_failure("LAYER_FILE_MISSING", layer))
            report = None
        else:
            path = Path(path_value)
            try:
                content = path.read_bytes()
            except OSError:
                layer_failures.append(_failure("LAYER_FILE_MISSING", layer))
                content = None
            if content is not None and expected_checksum is not None:
                observed = hashlib.sha256(content).hexdigest()
                if not hmac.compare_digest(observed, str(expected_checksum)):
                    layer_failures.append(_failure("CHECKSUM_MISMATCH", layer))
            try:
                report = json.loads(content) if content is not None else None
            except (json.JSONDecodeError, UnicodeDecodeError, RecursionError):
                layer_failures.append(_failure("LAYER_JSON_INVALID", layer))
                report = None
        layer_failures.extend(
            _identity_failures(
                report.get("candidateIdentity") if isinstance(report, Mapping) else None,
                expected_identity,
                layer,
            )
        )
        if report is None or not _layer_valid(layer, report, expected_identity):
            layer_failures.append(_failure("LAYER_CONTRACT_INVALID", layer))
        failures.extend(layer_failures)
        layers.append(
            {
                "name": layer,
                "status": "PASS" if not layer_failures else "FAIL",
                "checksumVerified": not any(
                    item["code"] in {"CHECKSUM_MISSING", "CHECKSUM_MISMATCH"}
                    for item in layer_failures
                ),
            }
        )
    return {
        "schemaVersion": RELEASE_SCHEMA_VERSION,
        "status": "PASS" if not failures else "FAIL",
        "candidateIdentity": dict(expected_identity),
        "layers": layers,
        "failures": failures,
    }


def _parse_layers(values: list[str]) -> dict[str, Path]:
    result = {}
    for value in values:
        name, separator, path = value.partition("=")
        if not separator or not name or not path or name in result:
            raise ValueError("each --layer must be a unique NAME=PATH")
        result[name] = Path(path)
    return result


def _same_file(left: Path, right: Path) -> bool:
    if left.resolve(strict=False) == right.resolve(strict=False):
        return True
    try:
        return os.path.samefile(left, right)
    except OSError:
        return False


def _output_aliases_evidence(
    output: Path, layer_paths: Mapping[str, Path], checksum_path: Path
) -> bool:
    if output.is_symlink():
        return True
    return any(
        _same_file(output, evidence)
        for evidence in (*layer_paths.values(), checksum_path)
    )


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-git-sha", required=True)
    parser.add_argument("--expected-image-digest", required=True)
    parser.add_argument("--expected-firmware-identity", required=True)
    parser.add_argument("--expected-config-fingerprint", required=True)
    parser.add_argument("--expected-fixture-sha256", required=True)
    parser.add_argument("--layer", action="append", default=[])
    parser.add_argument("--checksums-file", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    identity = {
        "gitSha": args.expected_git_sha,
        "imageDigest": args.expected_image_digest,
        "firmwareIdentity": args.expected_firmware_identity,
        "configFingerprint": args.expected_config_fingerprint,
        "fixtureSha256": args.expected_fixture_sha256,
    }
    try:
        paths = _parse_layers(args.layer)
        if _output_aliases_evidence(args.out, paths, args.checksums_file):
            raise ValueError("output aliases release evidence")
        checksums = load_checksum_manifest(args.checksums_file, paths)
    except (OSError, UnicodeError, ValueError):
        paths = {}
        checksums = {}
    verdict = aggregate_release_evidence(identity, paths, checksums)
    rendered = json.dumps(verdict, indent=2, sort_keys=True) + "\n"
    if paths:
        _atomic_write(args.out, rendered)
    print(rendered, end="")
    return 0 if verdict["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
