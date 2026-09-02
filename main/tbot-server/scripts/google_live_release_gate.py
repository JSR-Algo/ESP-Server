"""Read-only exact-candidate Google Live release evidence aggregation."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.analyze_google_live_log import correlate_websocket_bargein_evidence
from scripts.google_live_deterministic_evidence import (
    APPROVED_TEST_FILES,
    MANIFEST_SCHEMA,
    NODEID_PLUGIN_GIT_PATH,
    PYTEST_RUNTIME_MANIFEST_GIT_PATH,
    PYTEST_RUNTIME_SCHEMA,
    _junit_value_is_sensitive,
    atomic_write_exclusive,
    parse_manifest,
    parse_passing_junit,
    parse_pytest_runtime_manifest,
    snapshot_output_parent,
)
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
DETERMINISTIC_SUPPORTS = ("deterministic_manifest", "deterministic_junit")
IDENTITY_FIELDS = (
    "gitSha",
    "imageDigest",
    "firmwareIdentity",
    "configFingerprint",
    "fixtureSha256",
)
SHA256 = re.compile(r"[0-9a-f]{64}")
TAGGED_SHA256 = re.compile(r"sha256:[0-9a-f]{64}")
CANONICAL_DETERMINISTIC_NODE_COUNT = 783
CANONICAL_DETERMINISTIC_MANIFEST = Path(
    "main/tbot-server/tests/fixtures/google_live_deterministic_nodes.txt"
)


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
    coverage = report.get("coverageProof")
    return (
        isinstance(verdict, Mapping)
        and verdict.get("status") == "PASS"
        and type(verdict.get("total")) is int
        and verdict.get("total") > 0
        and verdict.get("failed") == 0
        and type(verdict.get("failed")) is int
        and verdict.get("skipped") == 0
        and type(verdict.get("skipped")) is int
        and verdict.get("errors") == 0
        and type(verdict.get("errors")) is int
        and verdict.get("failures") == []
        and isinstance(coverage, Mapping)
        and set(coverage)
        == {
            "manifestSchema",
            "manifestSha256",
            "manifestNodeCount",
            "executedNodeCount",
            "junitSha256",
            "nodeidPluginSha256",
            "pytestRuntimeManifestSha256",
            "pytestRuntimeSchema",
        }
        and coverage.get("manifestSchema") == MANIFEST_SCHEMA
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


def _git_output(repo_root: Path, *arguments: str) -> bytes:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=repo_root,
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise RuntimeError("repository verification failed")
    return completed.stdout


def _trusted_manifest_path() -> Path:
    code_root = Path(__file__).resolve().parents[1]
    repo_root = Path(
        _git_output(code_root, "rev-parse", "--show-toplevel").decode().strip()
    ).resolve(strict=True)
    return repo_root / CANONICAL_DETERMINISTIC_MANIFEST


def _validate_canonical_nodes(content: bytes) -> list[str]:
    nodes = parse_manifest(content)
    files = [node.split("::", 1)[0] for node in nodes]
    if (
        len(nodes) != CANONICAL_DETERMINISTIC_NODE_COUNT
        or len(nodes) != len(set(nodes))
        or set(files) != set(APPROVED_TEST_FILES)
        or list(dict.fromkeys(files)) != list(APPROVED_TEST_FILES)
    ):
        raise ValueError("canonical deterministic manifest scope is invalid")
    return nodes


def _load_trusted_deterministic_manifest(expected_git_sha: str) -> bytes:
    fixture_path = _trusted_manifest_path()
    repo_root = Path(
        _git_output(fixture_path.parent, "rev-parse", "--show-toplevel").decode().strip()
    ).resolve(strict=True)
    relative = fixture_path.relative_to(repo_root)
    if _git_output(repo_root, "rev-parse", "HEAD").decode().strip() != expected_git_sha:
        raise ValueError("candidate git SHA does not match repository HEAD")
    if _git_output(
        repo_root, "status", "--porcelain=v1", "-z", "--untracked-files=no"
    ):
        raise ValueError("candidate worktree contains tracked or staged modifications")
    _git_output(repo_root, "ls-files", "--error-unmatch", "--", str(relative))
    ancestors = []
    current = fixture_path.parent
    while current != repo_root:
        ancestors.append(current)
        current = current.parent
    fixture_stat = fixture_path.stat()
    if (
        fixture_path.is_symlink()
        or any(parent.is_symlink() for parent in ancestors)
        or not fixture_path.is_file()
        or fixture_stat.st_nlink != 1
    ):
        raise ValueError("canonical deterministic manifest path is invalid")
    worktree_content = fixture_path.read_bytes()
    after_read_stat = fixture_path.stat()
    if (
        fixture_stat.st_dev,
        fixture_stat.st_ino,
        fixture_stat.st_size,
        fixture_stat.st_mtime_ns,
        fixture_stat.st_nlink,
    ) != (
        after_read_stat.st_dev,
        after_read_stat.st_ino,
        after_read_stat.st_size,
        after_read_stat.st_mtime_ns,
        after_read_stat.st_nlink,
    ):
        raise ValueError("canonical deterministic manifest changed while being read")
    committed_content = _git_output(
        repo_root,
        "show",
        f"{expected_git_sha}:{relative.as_posix()}",
    )
    if worktree_content != committed_content:
        raise ValueError("canonical deterministic manifest differs from candidate Git object")
    _validate_canonical_nodes(committed_content)
    if (
        _git_output(repo_root, "rev-parse", "HEAD").decode().strip() != expected_git_sha
        or _git_output(
            repo_root, "status", "--porcelain=v1", "-z", "--untracked-files=no"
        )
    ):
        raise ValueError("candidate repository changed during manifest validation")
    return committed_content


def _load_trusted_pytest_runtime_manifest(expected_git_sha: str) -> bytes:
    fixture_path = _trusted_manifest_path()
    repo_root = Path(
        _git_output(fixture_path.parent, "rev-parse", "--show-toplevel").decode().strip()
    ).resolve(strict=True)
    if _git_output(repo_root, "rev-parse", "HEAD").decode().strip() != expected_git_sha:
        raise ValueError("candidate git SHA does not match repository HEAD")
    if _git_output(repo_root, "status", "--porcelain=v1", "-z", "--untracked-files=no"):
        raise ValueError("candidate worktree contains tracked or staged modifications")
    content = _git_output(
        repo_root,
        "show",
        f"{expected_git_sha}:{PYTEST_RUNTIME_MANIFEST_GIT_PATH}",
    )
    manifest = parse_pytest_runtime_manifest(content)
    plugin = _git_output(
        repo_root,
        "show",
        f"{expected_git_sha}:{NODEID_PLUGIN_GIT_PATH}",
    )
    if not hmac.compare_digest(hashlib.sha256(plugin).hexdigest(), manifest["plugin"]["sha256"]):
        raise ValueError("trusted pytest runtime plugin is invalid")
    if (
        _git_output(repo_root, "rev-parse", "HEAD").decode().strip() != expected_git_sha
        or _git_output(repo_root, "status", "--porcelain=v1", "-z", "--untracked-files=no")
    ):
        raise ValueError("candidate repository changed during runtime validation")
    return content


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
            raise ValueError("checksum manifest row is malformed")
        if _junit_value_is_sensitive(line):
            raise ValueError("checksum manifest violates the privacy contract")
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        if match is None:
            raise ValueError("checksum manifest row is malformed")
        relative = match.group(2)
        if re.fullmatch(r"(?:[A-Za-z0-9._-]+/)*[A-Za-z0-9._-]+", relative) is None:
            raise ValueError("checksum manifest artifact path is invalid")
        if _junit_value_is_sensitive(relative):
            raise ValueError("checksum manifest violates the privacy contract")
        candidate = (root / relative).resolve()
        if root not in candidate.parents:
            raise ValueError("checksum manifest path escapes its evidence root")
        if candidate not in by_path:
            raise ValueError("checksum manifest contains an undeclared artifact")
        if candidate in seen_paths:
            raise ValueError("checksum manifest must name each exact report once")
        seen_paths.add(candidate)
        result[by_path[candidate]] = match.group(1)
    if set(result) != set(expected):
        raise ValueError("checksum manifest is incomplete")
    return result


def aggregate_release_evidence(
    expected_identity: Mapping[str, Any],
    layer_paths: Mapping[str, Path | str],
    expected_checksums: Mapping[str, str],
) -> dict[str, Any]:
    """Read and validate all required reports without executing any journey."""
    failures = []
    layers = []
    loaded_reports = {}
    try:
        validate_expected_identity(expected_identity)
    except ValueError:
        failures.append(_failure("EXPECTED_CANDIDATE_IDENTITY_INVALID"))
    for name in sorted(set(layer_paths) - set(REQUIRED_LAYERS) - set(DETERMINISTIC_SUPPORTS)):
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
        loaded_reports[layer] = report
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
    physical = loaded_reports.get("physical")
    if isinstance(physical, Mapping) and (
        physical.get("logEvidence") != loaded_reports.get("server_regression")
        or physical.get("candidateSoakEvidence") != loaded_reports.get("candidate_soak")
    ):
        binding_failure = _failure("PHYSICAL_UPSTREAM_BINDING_MISMATCH", "physical")
        failures.append(binding_failure)
        for item in layers:
            if item["name"] == "physical":
                item["status"] = "FAIL"
                break
    deterministic = loaded_reports.get("deterministic")
    coverage = deterministic.get("coverageProof") if isinstance(deterministic, Mapping) else None
    try:
        trusted_manifest_path = _trusted_manifest_path()
        trusted_manifest_content = _load_trusted_deterministic_manifest(
            str(expected_identity.get("gitSha", ""))
        )
        trusted_runtime_content = _load_trusted_pytest_runtime_manifest(
            str(expected_identity.get("gitSha", ""))
        )
        trusted_runtime = parse_pytest_runtime_manifest(trusted_runtime_content)
    except (OSError, RuntimeError, UnicodeError, ValueError):
        trusted_manifest_path = None
        trusted_manifest_content = None
        trusted_runtime_content = None
        trusted_runtime = None
        failures.append(_failure("DETERMINISTIC_TRUSTED_MANIFEST_INVALID", "deterministic"))
    support_contents: dict[str, bytes] = {}
    supplied_support_paths = [
        Path(layer_paths[support])
        for support in DETERMINISTIC_SUPPORTS
        if layer_paths.get(support) is not None
    ]
    support_alias_detected = any(
        _same_file(left, right)
        for index, left in enumerate(supplied_support_paths)
        for right in supplied_support_paths[index + 1 :]
    )
    if support_alias_detected:
        failures.append(_failure("DETERMINISTIC_SUPPORT_ALIAS", "deterministic"))
    for support in DETERMINISTIC_SUPPORTS:
        path_value = layer_paths.get(support)
        checksum = expected_checksums.get(support)
        if path_value is None or type(checksum) is not str or SHA256.fullmatch(checksum) is None:
            failures.append(_failure("DETERMINISTIC_SUPPORT_MISSING", "deterministic", support))
            continue
        path = Path(path_value)
        try:
            if path.is_symlink() or not path.is_file():
                raise OSError
            if (
                support == "deterministic_manifest"
                and trusted_manifest_path is not None
                and _same_file(path, trusted_manifest_path)
            ):
                raise OSError
            content = path.read_bytes()
        except OSError:
            failures.append(_failure("DETERMINISTIC_SUPPORT_MISSING", "deterministic", support))
            continue
        if not hmac.compare_digest(hashlib.sha256(content).hexdigest(), checksum):
            failures.append(_failure("DETERMINISTIC_SUPPORT_CHECKSUM_MISMATCH", "deterministic", support))
        support_contents[support] = content
    try:
        manifest_content = support_contents["deterministic_manifest"]
        junit_content = support_contents["deterministic_junit"]
        if support_alias_detected:
            raise ValueError
        if trusted_manifest_content is None or not hmac.compare_digest(
            manifest_content, trusted_manifest_content
        ):
            raise ValueError
        nodes = parse_manifest(manifest_content)
        if nodes != _validate_canonical_nodes(trusted_manifest_content):
            raise ValueError
        totals = parse_passing_junit(junit_content, nodes)
        expected_coverage = {
            "manifestSchema": MANIFEST_SCHEMA,
            "manifestSha256": hashlib.sha256(manifest_content).hexdigest(),
            "manifestNodeCount": len(nodes),
            "executedNodeCount": len(nodes),
            "junitSha256": hashlib.sha256(junit_content).hexdigest(),
            "nodeidPluginSha256": trusted_runtime["plugin"]["sha256"],
            "pytestRuntimeManifestSha256": hashlib.sha256(
                trusted_runtime_content
            ).hexdigest(),
            "pytestRuntimeSchema": PYTEST_RUNTIME_SCHEMA,
        }
        if coverage != expected_coverage or not isinstance(deterministic, Mapping) or deterministic.get("testVerdict") != {
            "status": "PASS",
            "total": totals["tests"],
            "failed": 0,
            "skipped": 0,
            "errors": 0,
            "failures": [],
        }:
            raise ValueError
        if not hmac.compare_digest(
            trusted_manifest_content,
            _load_trusted_deterministic_manifest(str(expected_identity.get("gitSha", ""))),
        ):
            raise ValueError
    except (KeyError, OSError, RuntimeError, TypeError, UnicodeError, ValueError):
        failures.append(_failure("DETERMINISTIC_SUPPORT_CONTRACT_INVALID", "deterministic"))
    if any(item.get("layer") == "deterministic" for item in failures):
        for item in layers:
            if item["name"] == "deterministic":
                item["status"] = "FAIL"
                break
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


def _layer_path_candidates(values: list[str]) -> list[Path]:
    candidates = []
    for value in values:
        _name, separator, path = value.partition("=")
        if separator and path:
            candidates.append(Path(path))
    return candidates


def _same_file(left: Path, right: Path) -> bool:
    if left.resolve(strict=False) == right.resolve(strict=False):
        return True
    try:
        return os.path.samefile(left, right)
    except OSError:
        return False


def _output_aliases_evidence(
    output: Path, layer_paths: list[Path], checksum_path: Path
) -> bool:
    if output.is_symlink():
        return True
    return any(
        _same_file(output, evidence)
        for evidence in (*layer_paths, checksum_path)
    )


def _evidence_paths_alias(paths: list[Path]) -> bool:
    if any(path.is_symlink() for path in paths):
        return True
    return any(
        _same_file(left, right)
        for index, left in enumerate(paths)
        for right in paths[index + 1 :]
    )


def _atomic_write(
    path: Path, content: str, expected_parent_identity: tuple[int, int] | None = None
) -> None:
    atomic_write_exclusive(
        path,
        content.encode("utf-8"),
        expected_parent_identity=expected_parent_identity,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-git-sha", required=True)
    parser.add_argument("--expected-image-digest", required=True)
    parser.add_argument("--expected-firmware-identity", required=True)
    parser.add_argument("--expected-config-fingerprint", required=True)
    parser.add_argument("--expected-fixture-sha256", required=True)
    parser.add_argument("--layer", action="append", default=[])
    parser.add_argument("--support", action="append", default=[])
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
        supports = _parse_layers(args.support)
        if set(paths) & set(supports) or set(supports) != set(DETERMINISTIC_SUPPORTS):
            raise ValueError("support arguments are malformed")
        paths.update(supports)
        parse_valid = True
    except ValueError:
        paths = {}
        parse_valid = False
    output_safe = not _output_aliases_evidence(
        args.out,
        _layer_path_candidates(args.layer) + _layer_path_candidates(args.support),
        args.checksums_file,
    )
    try:
        output_parent_identity = snapshot_output_parent(args.out) if output_safe else None
    except (OSError, ValueError, RuntimeError):
        output_parent_identity = None
        output_safe = False
    try:
        if not output_safe:
            raise ValueError("output aliases release evidence")
        if not parse_valid:
            raise ValueError("layer arguments are malformed")
        if _evidence_paths_alias([*paths.values(), args.checksums_file]):
            raise ValueError("release evidence paths alias")
        checksums = load_checksum_manifest(args.checksums_file, paths)
    except (OSError, UnicodeError, ValueError):
        paths = {}
        checksums = {}
    verdict = aggregate_release_evidence(identity, paths, checksums)
    rendered = json.dumps(verdict, indent=2, sort_keys=True) + "\n"
    if output_safe:
        try:
            _atomic_write(args.out, rendered, output_parent_identity)
        except (OSError, ValueError, RuntimeError):
            print(rendered, end="")
            return 1
    print(rendered, end="")
    return 0 if verdict["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
