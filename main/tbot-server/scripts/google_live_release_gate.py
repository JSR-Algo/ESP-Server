"""Read-only exact-candidate Google Live release evidence aggregation."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import platform
import re
import stat
import sys
from collections.abc import Mapping
from collections.abc import Callable
from dataclasses import dataclass, replace
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
from scripts.google_live_command_runner import (
    RUNTIME_CLOSURE_MANIFEST_GIT_PATH,
    _MAX_CLOSURE_FILE_BYTES,
    _load_runtime_closure_manifest,
    _read_committed_pair_at,
    parse_provenance,
    render_commands_projection,
)
from scripts.google_live_reliability import (
    SCHEMA_VERSION,
    forbidden_report_fields,
    validate_candidate_soak_report,
    validate_log_reliability_contract,
    validate_real_api_pass_report,
)
from scripts.google_live_trusted_git import (
    git_output as _trusted_git_output,
    trusted_git_session,
)
from scripts.physical_smoke_audit import validate_physical_candidate_report

RELEASE_SCHEMA_VERSION = "google-live-release-verdict.v1"
PYTHON_EXECUTABLE_SCHEMA = "google-live-python-executables.v1"
COMMAND_EXECUTION_POLICY = "candidate-git-python-source.v1"
PYTHON_EXECUTABLE_MANIFEST_GIT_PATH = (
    "main/tbot-server/tests/fixtures/google_live_python_executable_manifest.json"
)
REQUIRED_LAYERS = (
    "deterministic",
    "server_regression",
    "real_api",
    "websocket_e2e",
    "physical",
    "candidate_soak",
)
DETERMINISTIC_SUPPORTS = ("deterministic_manifest", "deterministic_junit")
COMMAND_PROVENANCE_SUPPORT = "command_provenance"
COMMAND_PROJECTION_SUPPORT = "command_projection"
TIMELINE_INDEX_SUPPORT = "timeline_index"
RUNTIME_CLOSURE_SUPPORT = "runtime_closure_manifest"
REQUIRED_SUPPORTS = (*DETERMINISTIC_SUPPORTS, RUNTIME_CLOSURE_SUPPORT, COMMAND_PROVENANCE_SUPPORT)
UNIFIED_SUPPORTS = (*REQUIRED_SUPPORTS, COMMAND_PROJECTION_SUPPORT, TIMELINE_INDEX_SUPPORT)
REQUIRED_COMMAND_IDS = (
    "deterministic.produce",
    "real_api.round_trip",
    "websocket.transport",
    "websocket.log_analysis",
    "websocket.correlation",
    "candidate_soak.produce",
    "candidate_soak.replay",
    "physical.capture_and_audit",
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
CANONICAL_DETERMINISTIC_NODE_COUNT = 783
APPROVED_RUNTIME_PLATFORM = "darwin-arm64-cp314"
CANONICAL_DETERMINISTIC_MANIFEST = Path(
    "main/tbot-server/tests/fixtures/google_live_deterministic_nodes.txt"
)


@dataclass(frozen=True)
class BoundDirectory:
    device: int
    inode: int
    size: int
    modified_ns: int
    changed_ns: int
    mode: int
    uid: int
    links: int


@dataclass(frozen=True)
class BoundReleaseInput:
    path: Path
    content: bytes
    device: int
    inode: int
    size: int
    modified_ns: int
    changed_ns: int
    mode: int
    links: int
    sha256: str
    parent_chain: tuple[BoundDirectory, ...]


@dataclass(frozen=True)
class TrustedCommandSpec:
    argv_pattern: tuple[str | None, ...]
    cwd: str
    environment_sources: tuple[str, ...]
    expected_exit_codes: tuple[int, ...]
    input_labels: tuple[str, ...]
    output_labels: tuple[str, ...]
    secret_sources: tuple[str, ...]
    stdin_source: str | None
    timeout_sec: float
    cleanup_grace_sec: float


def _trusted_command_argv_patterns(
    identity: Mapping[str, Any],
) -> dict[str, tuple[str | None, ...]]:
    python = None
    candidate = (
        "--candidate-git-sha",
        str(identity["gitSha"]),
        "--candidate-image-digest",
        str(identity["imageDigest"]),
        "--firmware-identity",
        str(identity["firmwareIdentity"]),
        "--fixture-sha256",
        str(identity["fixtureSha256"]),
    )
    soak_support = (
        "--baseline-report",
        "<evidence:baseline/report.json>",
        "--real-api-report",
        "<evidence:real-api/report.json>",
        "--transport-report",
        "<evidence:websocket-e2e/transport.json>",
        "--correlated-transport-report",
        "<evidence:websocket-e2e/report.json>",
        "--log-reliability-report",
        "<evidence:server-regression/report.json>",
        "--lesson-manifest",
        "<evidence:lesson-manifest.json>",
        "--config-json",
        None,
    )
    return {
        "deterministic.produce": (
            python,
            "scripts/google_live_deterministic_evidence.py",
            "--manifest",
            "<evidence:deterministic/node-manifest.txt>",
            "--junit-out",
            "<evidence:deterministic/pytest.xml>",
            "--report",
            "<evidence:deterministic/report.json>",
            *candidate[:6],
            "--config-fingerprint",
            str(identity["configFingerprint"]),
            *candidate[6:],
        ),
        "real_api.round_trip": (
            python,
            "scripts/google_live_smoke.py",
            "--round-trip",
            "--audio-file",
            "<evidence:fixture.wav>",
            "--report",
            "<evidence:real-api/report.json>",
            *candidate[:6],
            "--config-fingerprint",
            str(identity["configFingerprint"]),
            *candidate[6:],
        ),
        "websocket.transport": (
            python,
            "scripts/voice_mode_websocket_audio_bargein.py",
            "--websocket-url",
            None,
            "--device-id",
            None,
            "--client-id",
            None,
            "--journey-id",
            None,
            *candidate[:6],
            "--config-json",
            None,
            *candidate[6:],
            "--report",
            "<evidence:websocket-e2e/transport.json>",
        ),
        "websocket.log_analysis": (
            python,
            "scripts/analyze_google_live_log.py",
            "--log",
            None,
            "--reliability-window",
            "--journey-id",
            None,
            "--out-json",
            "<evidence:server-regression/report.json>",
        ),
        "websocket.correlation": (
            python,
            "scripts/analyze_google_live_log.py",
            "--log",
            None,
            "--correlate-transport",
            "<evidence:websocket-e2e/transport.json>",
            "--expected-candidate-json",
            None,
            "--out-json",
            "<evidence:websocket-e2e/report.json>",
        ),
        "candidate_soak.produce": (
            python,
            "scripts/google_live_robot_soak.py",
            "--mode",
            "candidate",
            "--produce-candidate-evidence",
            "<evidence:candidate-soak/journey-evidence.json>",
            "--evidence-control-url",
            None,
            "--server-log",
            None,
            "--run-id",
            None,
            *soak_support,
            *candidate,
        ),
        "candidate_soak.replay": (
            python,
            "scripts/google_live_robot_soak.py",
            "--mode",
            "candidate",
            "--journey-evidence",
            "<evidence:candidate-soak/journey-evidence.json>",
            "--report",
            "<evidence:candidate-soak/report.json>",
            *soak_support,
            *candidate,
        ),
        "physical.capture_and_audit": (
            python,
            "scripts/google_live_physical_evidence.py",
            "--candidate-soak-report",
            "<evidence:candidate-soak/report.json>",
            "--server-report",
            "<evidence:server-regression/report.json>",
            "--report",
            "<evidence:physical/report.json>",
            "--operator-confirmed",
            "--transcript-plan-stdin",
            "--base-url",
            None,
            "--device-id",
            None,
            "--client-id",
            None,
            "--server-log",
            None,
            *candidate,
        ),
    }


def _trusted_command_specs(identity: Mapping[str, Any]) -> dict[str, TrustedCommandSpec]:
    outputs = {
        "deterministic.produce": (
            "deterministic/report.json",
            "deterministic/node-manifest.txt",
            "deterministic/pytest.xml",
        ),
        "real_api.round_trip": ("real-api/report.json",),
        "websocket.transport": ("websocket-e2e/transport.json",),
        "websocket.log_analysis": ("server-regression/report.json",),
        "websocket.correlation": ("websocket-e2e/report.json",),
        "candidate_soak.produce": ("candidate-soak/journey-evidence.json",),
        "candidate_soak.replay": ("candidate-soak/report.json",),
        "physical.capture_and_audit": ("physical/report.json",),
    }
    mint_commands = {
        "websocket.transport",
        "candidate_soak.produce",
        "physical.capture_and_audit",
    }
    return {
        command_id: TrustedCommandSpec(
            argv_pattern=_trusted_command_argv_patterns(identity)[command_id],
            cwd=".",
            environment_sources=(),
            expected_exit_codes=(0, 1) if command_id == "websocket.transport" else (0,),
            input_labels={
                "real_api.round_trip": ("fixture.wav",),
                "websocket.log_analysis": ("server.log",),
                "websocket.correlation": (
                    "server.log",
                    "websocket-e2e/transport.json",
                    "server-regression/report.json",
                ),
                "candidate_soak.produce": (
                    "server.log",
                    "baseline/report.json",
                    "real-api/report.json",
                    "websocket-e2e/transport.json",
                    "websocket-e2e/report.json",
                    "server-regression/report.json",
                    "lesson-manifest.json",
                ),
                "candidate_soak.replay": (
                    "candidate-soak/journey-evidence.json",
                    "baseline/report.json",
                    "real-api/report.json",
                    "websocket-e2e/transport.json",
                    "websocket-e2e/report.json",
                    "server-regression/report.json",
                    "lesson-manifest.json",
                ),
                "physical.capture_and_audit": (
                    "candidate-soak/report.json",
                    "server-regression/report.json",
                    "server.log",
                ),
            }.get(command_id, ()),
            output_labels=outputs[command_id],
            secret_sources=("<env:GOOGLE_API_KEY>",)
            if command_id == "real_api.round_trip"
            else (
                ("<env:TBOT_DEVICE_MINT_SECRET>",)
                if command_id in mint_commands
                else ()
            ),
            stdin_source=(
                "<stdin:protected_transcript_plan>"
                if command_id == "physical.capture_and_audit"
                else (
                    "<stdin:protected_candidate_plan>"
                    if command_id == "candidate_soak.produce"
                    else None
                )
            ),
            timeout_sec=300.0,
            cleanup_grace_sec=2.0,
        )
        for command_id in REQUIRED_COMMAND_IDS
    }


def _recorded_command_spec_digest(entry: Mapping[str, Any], runtime_closure_sha256: str | None = None) -> str:
    stable = {
        "argv": entry["argv"],
        "commandId": entry["commandId"],
        "cwd": entry["cwd"],
        "environmentSources": entry["environmentSources"],
        "expectedExitCodes": entry["terminalPolicy"]["expectedExitCodes"],
        "inputs": [item["label"] for item in entry["inputs"]],
        "outputs": [item["label"] for item in entry["outputs"]],
        "secretSources": entry["secretSources"],
        "stdinSource": entry["stdinSource"],
        "timeoutSec": entry["terminalPolicy"]["timeoutSec"],
        "cleanupGraceSec": entry["terminalPolicy"]["cleanupGraceSec"],
        "executionPolicy": COMMAND_EXECUTION_POLICY,
    }
    if runtime_closure_sha256 is not None:
        stable["runtimeClosureSha256"] = runtime_closure_sha256
    canonical = json.dumps(stable, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _runtime_closure_digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _canonical_runtime_closure(manifest: Mapping[str, Any]) -> bytes:
    return (json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode()


def _read_runtime_distribution_file(
    root: Path, relative: str, expected_size: int
) -> tuple[bytes, os.stat_result]:
    parts = relative.split("/")
    descriptors: list[int] = []
    try:
        descriptor = os.open(
            root,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        descriptors.append(descriptor)
        for part in parts[:-1]:
            descriptor = os.open(
                part,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=descriptor,
            )
            descriptors.append(descriptor)
        file_descriptor = os.open(
            parts[-1], os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=descriptor
        )
        descriptors.append(file_descriptor)
        before = os.fstat(file_descriptor)
        if before.st_size != expected_size or before.st_size > _MAX_CLOSURE_FILE_BYTES:
            raise ValueError("runtime closure distribution size changed")
        chunks: list[bytes] = []
        remaining = expected_size + 1
        while remaining:
            chunk = os.read(file_descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        after = os.fstat(file_descriptor)
        if (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_nlink,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_mode,
            after.st_nlink,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise ValueError("runtime closure distribution changed while reading")
        return b"".join(chunks), after
    except OSError as exc:
        raise ValueError("runtime closure distribution is unavailable") from exc
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _validate_runtime_closure_distributions(
    manifest: Mapping[str, Any], expected: tuple[tuple[int, ...], ...] | None = None
) -> tuple[tuple[int, ...], ...]:
    """Recheck mutable dependency files named by the Git-bound closure."""
    identities: list[tuple[int, ...]] = []
    for distribution in manifest.get("distributions", []):
        root = Path(distribution["root"])
        for item in distribution["files"]:
            data, info = _read_runtime_distribution_file(
                root, item["path"], item["size"]
            )
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("runtime closure distribution is not a regular file")
            if info.st_size != item["size"]:
                raise ValueError("runtime closure distribution size changed")
            if hashlib.sha256(data).hexdigest() != item["sha256"]:
                raise ValueError("runtime closure distribution digest changed")
            identities.append(
                (
                    info.st_dev,
                    info.st_ino,
                    info.st_mode,
                    info.st_nlink,
                    info.st_size,
                    info.st_mtime_ns,
                    info.st_ctime_ns,
                )
            )
    snapshot = tuple(identities)
    if expected is not None and snapshot != expected:
        raise ValueError("runtime closure distribution identity changed")
    return snapshot


def parse_trusted_python_executable_manifest(content: bytes) -> dict[str, Any]:
    try:
        value = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("trusted Python executable manifest is invalid") from exc
    canonical = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if (
        canonical != content
        or not isinstance(value, dict)
        or set(value) != {"profiles", "schemaVersion"}
        or value.get("schemaVersion") != PYTHON_EXECUTABLE_SCHEMA
        or not isinstance(value.get("profiles"), list)
        or not value["profiles"]
    ):
        raise ValueError("trusted Python executable manifest is invalid")
    keys = []
    for profile in value["profiles"]:
        if (
            not isinstance(profile, dict)
            or set(profile)
            != {
                "machine",
                "pythonImplementation",
                "pythonMajorMinor",
                "sha256",
                "size",
                "system",
            }
            or any(
                type(profile.get(field)) is not str or not profile[field]
                for field in (
                    "machine",
                    "pythonImplementation",
                    "pythonMajorMinor",
                    "sha256",
                    "system",
                )
            )
            or SHA256.fullmatch(profile["sha256"]) is None
            or re.fullmatch(r"[0-9]+\.[0-9]+", profile["pythonMajorMinor"]) is None
            or type(profile.get("size")) is not int
            or profile["size"] <= 0
            or profile["size"] > 128 * 1024 * 1024
        ):
            raise ValueError("trusted Python executable manifest is invalid")
        keys.append(
            (
                profile["system"],
                profile["machine"],
                profile["pythonImplementation"],
                profile["pythonMajorMinor"],
            )
        )
    if keys != sorted(keys) or len(keys) != len(set(keys)):
        raise ValueError("trusted Python executable manifest is invalid")
    return value


def _approved_python_executable(
    value: Any,
    runtime: Mapping[str, Any],
    executable_trust: Mapping[str, Any],
) -> bool:
    if (
        type(value) is not str
        or not Path(value).is_absolute()
        or not isinstance(runtime, Mapping)
        or not isinstance(executable_trust, Mapping)
    ):
        return False
    expected_version = runtime.get("pythonMajorMinor")
    expected_implementation = runtime.get("pythonImplementation")
    if type(expected_version) is not str or type(expected_implementation) is not str:
        return False
    if Path(value).name not in {"python", "python3", f"python{expected_version}"}:
        return False
    descriptor = None
    try:
        resolved = Path(value).resolve(strict=True)
        descriptor = os.open(
            resolved,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_size <= 0
            or before.st_size > 128 * 1024 * 1024
            or before.st_mode & 0o111 == 0
        ):
            return False
        content = bytearray()
        while len(content) < before.st_size:
            chunk = os.read(descriptor, min(1024 * 1024, before.st_size - len(content)))
            if not chunk:
                return False
            content.extend(chunk)
        after = os.fstat(descriptor)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            return False
        immutable_content = bytes(content)
        magic = immutable_content[:4]
        if magic not in {
            b"\x7fELF",
            b"\xca\xfe\xba\xbe",
            b"\xbe\xba\xfe\xca",
            b"\xce\xfa\xed\xfe",
            b"\xcf\xfa\xed\xfe",
            b"\xfe\xed\xfa\xce",
            b"\xfe\xed\xfa\xcf",
        } and not magic.startswith(b"MZ"):
            return False
        matching = [
            profile
            for profile in executable_trust.get("profiles", [])
            if isinstance(profile, Mapping)
            and profile.get("system") == platform.system().lower()
            and profile.get("machine") == platform.machine().lower()
            and profile.get("pythonImplementation") == expected_implementation
            and profile.get("pythonMajorMinor") == expected_version
        ]
        return len(matching) == 1 and (
            matching[0].get("size") == len(immutable_content)
            and hmac.compare_digest(
                str(matching[0].get("sha256")),
                hashlib.sha256(immutable_content).hexdigest(),
            )
        )
    except OSError:
        return False
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _command_matches_trusted_spec(
    entry: Mapping[str, Any],
    spec: TrustedCommandSpec,
    runtime: Mapping[str, Any],
    *,
    executable_trust: Mapping[str, Any] | None = None,
    executable_approved: bool | None = None,
    runtime_closure_sha256: str | None = None,
) -> bool:
    terminal = entry["terminalPolicy"]
    return (
        len(entry["argv"]) == len(spec.argv_pattern)
        and (
            _approved_python_executable(
                entry["argv"][0], runtime, executable_trust
            )
            if executable_approved is None
            else executable_approved
        )
        and all(
            index == 0 or expected is None or actual == expected
            for index, (actual, expected) in enumerate(
                zip(entry["argv"], spec.argv_pattern, strict=True)
            )
        )
        and entry["cwd"] == spec.cwd
        and entry["environmentSources"] == list(spec.environment_sources)
        and terminal["expectedExitCodes"] == list(spec.expected_exit_codes)
        and [item["label"] for item in entry["inputs"]] == list(spec.input_labels)
        and [item["label"] for item in entry["outputs"]] == list(spec.output_labels)
        and entry["secretSources"] == list(spec.secret_sources)
        and hmac.compare_digest(entry["specSha256"], _recorded_command_spec_digest(entry, runtime_closure_sha256))
        and entry["stdinSource"] == spec.stdin_source
        and terminal["timeoutSec"] == spec.timeout_sec
        and terminal["cleanupGraceSec"] == spec.cleanup_grace_sec
    )


class ReleaseEvidenceChanged(RuntimeError):
    pass


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
    return _trusted_git_output(repo_root, *arguments)


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


def _load_trusted_python_executable_manifest(expected_git_sha: str) -> bytes:
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
        f"{expected_git_sha}:{PYTHON_EXECUTABLE_MANIFEST_GIT_PATH}",
    )
    parse_trusted_python_executable_manifest(content)
    if (
        _git_output(repo_root, "rev-parse", "HEAD").decode().strip() != expected_git_sha
        or _git_output(repo_root, "status", "--porcelain=v1", "-z", "--untracked-files=no")
    ):
        raise ValueError("candidate repository changed during executable validation")
    return content


def _absolute_input_path(path: Path | str) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else Path.cwd() / candidate


def _directory_identity(opened: os.stat_result) -> BoundDirectory:
    return BoundDirectory(
        opened.st_dev,
        opened.st_ino,
        opened.st_size,
        opened.st_mtime_ns,
        opened.st_ctime_ns,
        opened.st_mode,
        opened.st_uid,
        opened.st_nlink,
    )


def _open_release_input_parent(
    path: Path | str,
) -> tuple[Path, int, tuple[BoundDirectory, ...]]:
    absolute = _absolute_input_path(path)
    parts = absolute.parts
    if not parts or any(part in {"", ".", ".."} for part in parts[1:]):
        raise ValueError("release evidence path is invalid")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    directory_fd = os.open(parts[0], flags)
    chain = []
    try:
        chain.append(_directory_identity(os.fstat(directory_fd)))
        for component in parts[1:-1]:
            next_fd = os.open(component, flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
            chain.append(_directory_identity(os.fstat(directory_fd)))
        return absolute, directory_fd, tuple(chain)
    except BaseException:
        os.close(directory_fd)
        raise


def _read_bound_release_input(path: Path | str) -> BoundReleaseInput:
    absolute, parent_fd, parent_chain = _open_release_input_parent(path)
    descriptor = None
    try:
        descriptor = os.open(
            absolute.name,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_fd,
        )
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise RuntimeError("release evidence changed")
        chunks = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        content = b"".join(chunks)
        after = os.fstat(descriptor)
        before_identity = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
            before.st_mode,
            before.st_nlink,
        )
        after_identity = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
            after.st_mode,
            after.st_nlink,
        )
        if (
            _directory_identity(os.fstat(parent_fd)) != parent_chain[-1]
            or before_identity != after_identity
        ):
            raise RuntimeError("release evidence changed")
        return BoundReleaseInput(
            absolute,
            content,
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
            after.st_mode,
            after.st_nlink,
            hashlib.sha256(content).hexdigest(),
            parent_chain,
        )
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent_fd)


def _require_release_input_unchanged(
    bound: BoundReleaseInput,
    *,
    protected_root: Path | None = None,
    allowed_changed_directory: Path | None = None,
    allowed_directory_identity: BoundDirectory | None = None,
) -> None:
    current = _read_bound_release_input(bound.path)
    allowed_index = None
    if allowed_changed_directory is not None:
        allowed = _absolute_input_path(allowed_changed_directory)
        parent_parts = bound.path.parent.parts
        allowed_parts = allowed.parts
        if (
            len(allowed_parts) <= len(parent_parts)
            and parent_parts[: len(allowed_parts)] == allowed_parts
        ):
            allowed_index = len(allowed_parts) - 1
    protected_index = len(
        _absolute_input_path(protected_root or bound.path.parent).parts
    ) - 1
    chain_matches = True
    for index, (observed, expected) in enumerate(
        zip(current.parent_chain, bound.parent_chain, strict=True)
    ):
        if index == allowed_index:
            matches = (
                observed == expected
                if allowed_directory_identity is None
                else observed == allowed_directory_identity
            )
        elif index < protected_index:
            matches = (
                observed.device,
                observed.inode,
                observed.mode,
                observed.uid,
            ) == (
                expected.device,
                expected.inode,
                expected.mode,
                expected.uid,
            )
        else:
            matches = observed == expected
        if not matches:
            chain_matches = False
            break
    if not chain_matches or replace(current, parent_chain=bound.parent_chain) != bound:
        raise ReleaseEvidenceChanged("release evidence changed")


def _require_all_release_inputs_unchanged(
    bindings: Mapping[str, BoundReleaseInput],
    *,
    allowed_changed_directory: Path | None = None,
    allowed_directory_identity: BoundDirectory | None = None,
) -> None:
    protected_root = Path(
        os.path.commonpath([str(bound.path.parent) for bound in bindings.values()])
    )
    try:
        for name, bound in bindings.items():
            try:
                _require_release_input_unchanged(
                    bound,
                    protected_root=protected_root,
                    allowed_changed_directory=allowed_changed_directory,
                    allowed_directory_identity=allowed_directory_identity,
                )
            except (OSError, ValueError, RuntimeError) as exc:
                raise ReleaseEvidenceChanged(
                    f"release evidence changed: {name}"
                ) from exc
    except (OSError, ValueError, RuntimeError) as exc:
        raise ReleaseEvidenceChanged("release evidence changed") from exc


def _bind_committed_provenance_pair(
    bindings: dict[str, BoundReleaseInput], provenance_path: Path
) -> None:
    pointer_path = provenance_path.with_name(f".{provenance_path.name}.pair")
    try:
        pointer_binding = _read_bound_release_input(pointer_path)
    except FileNotFoundError as exc:
        raise ValueError("command provenance pointer is required") from exc
    parent_fd = os.open(
        provenance_path.parent,
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        committed = _read_committed_pair_at(
            parent_fd, provenance_path.name, provenance_path.with_suffix(".txt").name
        )
    finally:
        os.close(parent_fd)
    if committed.pointer is None or committed.pointer_content != pointer_binding.content:
        raise ReleaseEvidenceChanged("release provenance pair changed")
    generation_dir = provenance_path.parent / f".{provenance_path.name}.generations"
    pair_bindings = {
        "command_provenance_pointer": pointer_binding,
        "command_provenance_projection": _read_bound_release_input(
            provenance_path.with_suffix(".txt")
        ),
        "command_provenance_generation_jsonl": _read_bound_release_input(
            generation_dir / f"{committed.pointer.generation}.jsonl"
        ),
        "command_provenance_generation_projection": _read_bound_release_input(
            generation_dir / f"{committed.pointer.generation}.txt"
        ),
    }
    if (
        bindings[COMMAND_PROVENANCE_SUPPORT].content != committed.jsonl
        or pair_bindings["command_provenance_projection"].content
        != committed.projection
        or pair_bindings["command_provenance_generation_jsonl"].content
        != committed.jsonl
        or pair_bindings["command_provenance_generation_projection"].content
        != committed.projection
    ):
        raise ReleaseEvidenceChanged("release provenance pair changed")
    bindings.update(pair_bindings)


def _parse_checksum_manifest_content(
    content: bytes,
    manifest_path: Path | str,
    layer_paths: Mapping[str, Path | str],
) -> dict[str, str]:
    try:
        lines = content.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ValueError("checksum manifest encoding is invalid") from exc
    manifest = _absolute_input_path(manifest_path)
    root = manifest.parent
    expected = {name: _absolute_input_path(path) for name, path in layer_paths.items()}
    by_path = {path: name for name, path in expected.items()}
    result = {}
    seen_paths = set()
    for line in lines:
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
        candidate = root.joinpath(*Path(relative).parts)
        if candidate not in by_path:
            raise ValueError("checksum manifest contains an undeclared artifact")
        if candidate in seen_paths:
            raise ValueError("checksum manifest must name each exact report once")
        seen_paths.add(candidate)
        result[by_path[candidate]] = match.group(1)
    if set(result) != set(expected):
        raise ValueError("checksum manifest is incomplete")
    return result


def load_checksum_manifest(
    manifest_path: Path | str, layer_paths: Mapping[str, Path | str]
) -> dict[str, str]:
    """Map trusted GNU sha256sum rows to the exact required report paths."""
    manifest = _read_bound_release_input(manifest_path)
    return _parse_checksum_manifest_content(manifest.content, manifest.path, layer_paths)


def aggregate_release_evidence(
    expected_identity: Mapping[str, Any],
    layer_paths: Mapping[str, Path | str],
    expected_checksums: Mapping[str, str],
    *,
    input_contents: Mapping[str, bytes] | None = None,
    runtime_closure_distribution_snapshot: tuple[tuple[int, ...], ...] | None = None,
    unified: bool = False,
) -> dict[str, Any]:
    """Read and validate all required reports without executing any journey."""
    failures = []
    layers = []
    loaded_reports = {}
    try:
        validate_expected_identity(expected_identity)
    except ValueError:
        failures.append(_failure("EXPECTED_CANDIDATE_IDENTITY_INVALID"))
    allowed_supports = set(REQUIRED_SUPPORTS) | {
        COMMAND_PROJECTION_SUPPORT,
        TIMELINE_INDEX_SUPPORT,
    }
    for name in sorted(set(layer_paths) - set(REQUIRED_LAYERS) - allowed_supports):
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
                content = (
                    input_contents[layer]
                    if input_contents is not None
                    else path.read_bytes()
                )
            except (KeyError, OSError):
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
    observed_distribution_snapshot = None
    try:
        trusted_manifest_path = _trusted_manifest_path()
        trusted_manifest_content = _load_trusted_deterministic_manifest(
            str(expected_identity.get("gitSha", ""))
        )
        trusted_runtime_content = _load_trusted_pytest_runtime_manifest(
            str(expected_identity.get("gitSha", ""))
        )
        trusted_runtime = parse_pytest_runtime_manifest(trusted_runtime_content)
        trusted_executable_content = _load_trusted_python_executable_manifest(
            str(expected_identity.get("gitSha", ""))
        )
        trusted_executable = parse_trusted_python_executable_manifest(
            trusted_executable_content
        )
        trusted_runtime_closure = _load_runtime_closure_manifest(
            str(expected_identity.get("gitSha", ""))
        )
        observed_distribution_snapshot = _validate_runtime_closure_distributions(
            trusted_runtime_closure, runtime_closure_distribution_snapshot
        )
        if (
            trusted_runtime_closure.get("platform") != APPROVED_RUNTIME_PLATFORM
            or trusted_runtime_closure["runtime"]["pythonMajorMinor"]
            != trusted_runtime["pythonMajorMinor"]
        ):
            raise ValueError("runtime closure does not match deterministic runtime")
        trusted_runtime_closure_content = _canonical_runtime_closure(
            trusted_runtime_closure
        )
        runtime_closure_sha256 = _runtime_closure_digest(
            trusted_runtime_closure_content
        )
    except (OSError, RuntimeError, UnicodeError, ValueError):
        trusted_manifest_path = None
        trusted_manifest_content = None
        trusted_runtime_content = None
        trusted_runtime = None
        trusted_executable_content = None
        trusted_executable = None
        trusted_runtime_closure = None
        trusted_runtime_closure_content = None
        runtime_closure_sha256 = None
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
            content = (
                input_contents[support]
                if input_contents is not None
                else path.read_bytes()
            )
        except (KeyError, OSError):
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
    closure_path_value = layer_paths.get(RUNTIME_CLOSURE_SUPPORT)
    closure_checksum = expected_checksums.get(RUNTIME_CLOSURE_SUPPORT)
    try:
        if (
            closure_path_value is None
            or type(closure_checksum) is not str
            or SHA256.fullmatch(closure_checksum) is None
            or trusted_runtime_closure_content is None
        ):
            raise ValueError
        closure_path = Path(closure_path_value)
        closure_stat = closure_path.lstat()
        if (
            closure_path.is_symlink()
            or not stat.S_ISREG(closure_stat.st_mode)
            or closure_stat.st_nlink != 1
        ):
            raise ValueError
        trusted_closure_path = _trusted_manifest_path().parents[4] / Path(
            RUNTIME_CLOSURE_MANIFEST_GIT_PATH
        )
        if _same_file(closure_path, trusted_closure_path):
            raise ValueError
        closure_content = (
            input_contents[RUNTIME_CLOSURE_SUPPORT]
            if input_contents is not None
            else closure_path.read_bytes()
        )
        if (
            not hmac.compare_digest(
                _runtime_closure_digest(closure_content), closure_checksum
            )
            or not hmac.compare_digest(
                closure_content, trusted_runtime_closure_content
            )
        ):
            raise ValueError
    except (KeyError, OSError, TypeError, ValueError):
        failures.append(_failure("RUNTIME_CLOSURE_INVALID"))
    provenance_path = layer_paths.get(COMMAND_PROVENANCE_SUPPORT)
    provenance_checksum = expected_checksums.get(COMMAND_PROVENANCE_SUPPORT)
    try:
        if (
            provenance_path is None
            or type(provenance_checksum) is not str
            or SHA256.fullmatch(provenance_checksum) is None
        ):
            raise ValueError
        content = (
            input_contents[COMMAND_PROVENANCE_SUPPORT]
            if input_contents is not None
            else Path(provenance_path).read_bytes()
        )
        if not hmac.compare_digest(hashlib.sha256(content).hexdigest(), provenance_checksum):
            raise ValueError
        entries = parse_provenance(content)
        required = [
            entry for entry in entries if not entry["commandId"].startswith("diagnostic.")
        ]
        if [entry["commandId"] for entry in required] != list(REQUIRED_COMMAND_IDS):
            raise ValueError
        if any(entry["candidateIdentity"] != dict(expected_identity) for entry in entries):
            raise ValueError
        if any(
            entry["terminalPolicy"]["classification"] != "expected_exit"
            or entry["terminalPolicy"]["satisfied"] is not True
            or type(entry["exitCode"]) is not int
            or entry["exitCode"] not in entry["terminalPolicy"]["expectedExitCodes"]
            for entry in required
        ):
            raise ValueError
        root = Path(provenance_path).parent
        artifact_by_name = {
            name: {
                "label": Path(path).relative_to(root).as_posix(),
                "sha256": expected_checksums[name],
            }
            for name, path in layer_paths.items()
            if name in (*REQUIRED_LAYERS, *DETERMINISTIC_SUPPORTS)
        }
        by_id = {entry["commandId"]: entry for entry in required}
        trusted_specs = _trusted_command_specs(expected_identity)
        recorded_executables = {entry["argv"][0] for entry in required}
        if len(recorded_executables) != 1:
            raise ValueError
        executable_approved = _approved_python_executable(
            next(iter(recorded_executables)), trusted_runtime, trusted_executable
        )
        if any(
            not _command_matches_trusted_spec(
                by_id[command_id],
                trusted_specs[command_id],
                trusted_runtime,
                executable_approved=executable_approved,
                runtime_closure_sha256=runtime_closure_sha256,
            )
            for command_id in REQUIRED_COMMAND_IDS
        ):
            raise ValueError
        expected_output_bindings = {
            "deterministic.produce": {
                "deterministic",
                "deterministic_manifest",
                "deterministic_junit",
            },
            "real_api.round_trip": {"real_api"},
            "websocket.correlation": {"websocket_e2e"},
            "candidate_soak.replay": {"candidate_soak"},
            "physical.capture_and_audit": {"physical"},
        }
        for command_id, names in expected_output_bindings.items():
            if any(
                artifact_by_name[name] not in by_id[command_id]["outputs"]
                for name in names
            ):
                raise ValueError
        producer_outputs = by_id["candidate_soak.produce"]["outputs"]
        replay_inputs = by_id["candidate_soak.replay"]["inputs"]
        journey_inputs = [
            item
            for item in replay_inputs
            if item["label"] == "candidate-soak/journey-evidence.json"
        ]
        if (
            len(producer_outputs) != 1
            or len(journey_inputs) != 1
            or producer_outputs != journey_inputs
            or producer_outputs[0]["label"] != "candidate-soak/journey-evidence.json"
            or producer_outputs[0] == artifact_by_name["candidate_soak"]
            or artifact_by_name["candidate_soak"] in replay_inputs
        ):
            raise ValueError
        intermediate = _read_bound_release_input(root / producer_outputs[0]["label"])
        if not hmac.compare_digest(intermediate.sha256, producer_outputs[0]["sha256"]):
            raise ValueError
        exact_mint_source = ["<env:TBOT_DEVICE_MINT_SECRET>"]
        mint_commands = {
            "websocket.transport",
            "candidate_soak.produce",
            "physical.capture_and_audit",
        }
        for command_id in REQUIRED_COMMAND_IDS:
            expected_secrets = (
                ["<env:GOOGLE_API_KEY>"]
                if command_id == "real_api.round_trip"
                else (exact_mint_source if command_id in mint_commands else [])
            )
            expected_stdin = (
                "<stdin:protected_transcript_plan>"
                if command_id == "physical.capture_and_audit"
                else (
                    "<stdin:protected_candidate_plan>"
                    if command_id == "candidate_soak.produce"
                    else None
                )
            )
            if (
                by_id[command_id]["secretSources"] != expected_secrets
                or by_id[command_id]["stdinSource"] != expected_stdin
            ):
                raise ValueError
        revalidated_runtime_closure = _load_runtime_closure_manifest(
            str(expected_identity.get("gitSha", ""))
        )
        _validate_runtime_closure_distributions(
            revalidated_runtime_closure, observed_distribution_snapshot
        )
        if not hmac.compare_digest(
            _canonical_runtime_closure(revalidated_runtime_closure),
            trusted_runtime_closure_content,
        ):
            raise ValueError
    except (KeyError, OSError, RuntimeError, TypeError, UnicodeError, ValueError):
        failures.append(_failure("COMMAND_PROVENANCE_INVALID"))
    orchestration_supports_absent = (
        layer_paths.get(COMMAND_PROJECTION_SUPPORT) is None
        and layer_paths.get(TIMELINE_INDEX_SUPPORT) is None
    )
    try:
        if orchestration_supports_absent:
            raise KeyError("optional orchestration supports absent")
        if layer_paths.get(COMMAND_PROJECTION_SUPPORT) is None or layer_paths.get(TIMELINE_INDEX_SUPPORT) is None:
            raise ValueError
        projection_path = Path(layer_paths[COMMAND_PROJECTION_SUPPORT])
        timeline_path = Path(layer_paths[TIMELINE_INDEX_SUPPORT])
        support_paths = [
            Path(layer_paths[name])
            for name in REQUIRED_SUPPORTS
            if layer_paths.get(name) is not None
        ]
        if any(
            _same_file(left, right)
            for index, left in enumerate(support_paths)
            for right in support_paths[index + 1 :]
        ):
            raise ValueError
        projection = (
            input_contents[COMMAND_PROJECTION_SUPPORT]
            if input_contents is not None
            else projection_path.read_bytes()
        )
        timeline = (
            input_contents[TIMELINE_INDEX_SUPPORT]
            if input_contents is not None
            else timeline_path.read_bytes()
        )
        for name, content in (
            (COMMAND_PROJECTION_SUPPORT, projection),
            (TIMELINE_INDEX_SUPPORT, timeline),
        ):
            checksum = expected_checksums.get(name)
            path = Path(layer_paths[name])
            opened = path.lstat()
            if (
                type(checksum) is not str
                or SHA256.fullmatch(checksum) is None
                or path.is_symlink()
                or not stat.S_ISREG(opened.st_mode)
                or opened.st_nlink != 1
                or not hmac.compare_digest(hashlib.sha256(content).hexdigest(), checksum)
            ):
                raise ValueError
        if projection != render_commands_projection(entries):
            raise ValueError
        if _junit_value_is_sensitive(projection.decode("ascii", errors="strict")):
            raise ValueError
        timeline_rows = []
        for raw_line in timeline.decode("ascii", errors="strict").splitlines():
            row = json.loads(raw_line)
            if (
                not isinstance(row, dict)
                or not {"layer", "artifact", "journeyId", "windowId", "startedAtUtc", "endedAtUtc"} <= set(row)
                or not set(row) <= {
                    "layer", "artifact", "journeyId", "windowId", "startedAtUtc", "endedAtUtc"
                }
                or row["layer"] not in REQUIRED_LAYERS
                or row["artifact"]
                != Path(layer_paths[row["layer"]]).relative_to(timeline_path.parent).as_posix()
                or any(not isinstance(row[field], str) or not row[field] for field in ("journeyId", "windowId", "startedAtUtc", "endedAtUtc"))
                or forbidden_report_fields(row)
            ):
                raise ValueError
            server_report = loaded_reports.get("server_regression")
            server_scope = server_report.get("evidenceScope", {}) if isinstance(server_report, Mapping) else {}
            server_window = server_report.get("logWindow", {}) if isinstance(server_report, Mapping) else {}
            if row != {
                "layer": row["layer"],
                "artifact": row["artifact"],
                "journeyId": server_scope.get("journeyId"),
                "windowId": server_window.get("windowId"),
                "startedAtUtc": server_window.get("start"),
                "endedAtUtc": server_window.get("end"),
            }:
                raise ValueError
            timeline_rows.append(row)
        if [row["layer"] for row in timeline_rows] != list(REQUIRED_LAYERS):
            raise ValueError
    except (KeyError, OSError, TypeError, UnicodeError, ValueError, json.JSONDecodeError):
        if unified or not orchestration_supports_absent:
            failures.append(_failure("ORCHESTRATION_SUPPORT_INVALID"))
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
    if any(
        path.is_symlink()
        or (path.exists() and path.is_file() and path.stat().st_nlink != 1)
        for path in paths
    ):
        return True
    return any(
        _same_file(left, right)
        for index, left in enumerate(paths)
        for right in paths[index + 1 :]
    )


def _atomic_write(
    path: Path,
    content: str,
    expected_parent_identity: tuple[int, int] | None = None,
    *,
    pre_publish: Callable[[Path], None] | None = None,
    post_publish: Callable[[], None] | None = None,
) -> None:
    atomic_write_exclusive(
        path,
        content.encode("utf-8"),
        expected_parent_identity=expected_parent_identity,
        pre_publish=pre_publish,
        post_publish=post_publish,
    )


def _snapshot_release_directory(path: Path) -> BoundDirectory:
    descriptor = os.open(
        _absolute_input_path(path),
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        return _directory_identity(os.fstat(descriptor))
    finally:
        os.close(descriptor)


def _produce_release_verdict(
    expected_identity: Mapping[str, Any],
    layer_paths: Mapping[str, Path | str],
    checksum_path: Path | str,
    output_path: Path,
    *,
    after_checksum_read: Callable[[], None] | None = None,
    after_inputs_parsed: Callable[[], None] | None = None,
    pre_publish: Callable[[], None] | None = None,
    post_publish: Callable[[], None] | None = None,
    unified: bool = False,
) -> dict[str, Any]:
    """Publish a verdict only while every validated input retains its identity."""
    expected_paths = set(REQUIRED_LAYERS) | set(
        UNIFIED_SUPPORTS if unified else REQUIRED_SUPPORTS
    )
    if set(layer_paths) != expected_paths:
        raise ValueError("release evidence paths are incomplete")
    evidence_paths = [Path(path) for path in layer_paths.values()]
    if _evidence_paths_alias([*evidence_paths, Path(checksum_path)]):
        raise ValueError("release evidence paths alias")
    if _output_aliases_evidence(output_path, evidence_paths, Path(checksum_path)):
        raise ValueError("output aliases release evidence")

    runtime_closure_distribution_snapshot = None
    try:
        runtime_closure = _load_runtime_closure_manifest(
            str(expected_identity.get("gitSha", ""))
        )
        runtime_closure_distribution_snapshot = _validate_runtime_closure_distributions(
            runtime_closure
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise RuntimeError("release evidence changed") from exc
    bindings = {"checksums": _read_bound_release_input(checksum_path)}
    bindings.update(
        {name: _read_bound_release_input(path) for name, path in layer_paths.items()}
    )
    _bind_committed_provenance_pair(
        bindings, Path(layer_paths[COMMAND_PROVENANCE_SUPPORT])
    )
    provenance_entries = parse_provenance(bindings[COMMAND_PROVENANCE_SUPPORT].content)
    producer_entry = next(
        (
            entry
            for entry in provenance_entries
            if entry["commandId"] == "candidate_soak.produce"
        ),
        None,
    )
    if not isinstance(producer_entry, Mapping) or len(producer_entry["outputs"]) != 1:
        raise ValueError("candidate soak intermediate provenance is invalid")
    intermediate_path = Path(layer_paths[COMMAND_PROVENANCE_SUPPORT]).parent / producer_entry[
        "outputs"
    ][0]["label"]
    if _evidence_paths_alias([*evidence_paths, Path(checksum_path), intermediate_path]):
        raise ValueError("release evidence paths alias")
    bindings["candidate_soak_intermediate"] = _read_bound_release_input(
        intermediate_path
    )
    checksums = _parse_checksum_manifest_content(
        bindings["checksums"].content,
        bindings["checksums"].path,
        layer_paths,
    )
    if after_checksum_read is not None:
        after_checksum_read()
    _require_all_release_inputs_unchanged(bindings)

    contents = {name: bindings[name].content for name in layer_paths}
    verdict = aggregate_release_evidence(
        expected_identity,
        layer_paths,
        checksums,
        input_contents=contents,
        runtime_closure_distribution_snapshot=runtime_closure_distribution_snapshot,
    )
    if after_inputs_parsed is not None:
        after_inputs_parsed()
    if verdict["status"] == "PASS":
        try:
            _validate_runtime_closure_distributions(
                _load_runtime_closure_manifest(str(expected_identity.get("gitSha", ""))),
                runtime_closure_distribution_snapshot,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            raise RuntimeError("release evidence changed") from exc
    _require_all_release_inputs_unchanged(bindings)
    rendered = json.dumps(verdict, indent=2, sort_keys=True) + "\n"
    output_parent_identity = snapshot_output_parent(output_path)

    def validate_pre_publish(_temporary_path: Path) -> None:
        output_parent_identity = _snapshot_release_directory(output_path.parent)
        if pre_publish is not None:
            pre_publish()
        if verdict["status"] == "PASS":
            try:
                _validate_runtime_closure_distributions(
                    _load_runtime_closure_manifest(str(expected_identity.get("gitSha", ""))),
                    runtime_closure_distribution_snapshot,
                )
            except (OSError, RuntimeError, ValueError) as exc:
                raise RuntimeError("release evidence changed") from exc
        _require_all_release_inputs_unchanged(
            bindings,
            allowed_changed_directory=output_path.parent,
            allowed_directory_identity=output_parent_identity,
        )

    def validate_post_publish() -> None:
        output_parent_identity = _snapshot_release_directory(output_path.parent)
        if post_publish is not None:
            post_publish()
        if verdict["status"] == "PASS":
            try:
                _validate_runtime_closure_distributions(
                    _load_runtime_closure_manifest(str(expected_identity.get("gitSha", ""))),
                    runtime_closure_distribution_snapshot,
                )
            except (OSError, RuntimeError, ValueError) as exc:
                raise RuntimeError("release evidence changed") from exc
        _require_all_release_inputs_unchanged(
            bindings,
            allowed_changed_directory=output_path.parent,
            allowed_directory_identity=output_parent_identity,
        )

    _atomic_write(
        output_path,
        rendered,
        output_parent_identity,
        pre_publish=validate_pre_publish,
        post_publish=validate_post_publish,
    )
    return verdict


def produce_release_verdict(
    expected_identity: Mapping[str, Any],
    layer_paths: Mapping[str, Path | str],
    checksum_path: Path | str,
    output_path: Path,
    *,
    after_checksum_read: Callable[[], None] | None = None,
    after_inputs_parsed: Callable[[], None] | None = None,
    pre_publish: Callable[[], None] | None = None,
    post_publish: Callable[[], None] | None = None,
    unified: bool = False,
) -> dict[str, Any]:
    with trusted_git_session():
        return _produce_release_verdict(
            expected_identity,
            layer_paths,
            checksum_path,
            output_path,
            after_checksum_read=after_checksum_read,
            after_inputs_parsed=after_inputs_parsed,
            pre_publish=pre_publish,
            post_publish=post_publish,
            unified=unified,
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
        if set(paths) & set(supports) or set(supports) != set(REQUIRED_SUPPORTS):
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
    if not output_safe or not parse_valid:
        print("release evidence validation failed", file=sys.stderr)
        return 1
    try:
        verdict = produce_release_verdict(
            identity,
            paths,
            args.checksums_file,
            args.out,
        )
    except (OSError, RuntimeError, UnicodeError, ValueError):
        print("release evidence validation failed", file=sys.stderr)
        return 1
    rendered = json.dumps(verdict, indent=2, sort_keys=True) + "\n"
    print(rendered, end="")
    return 0 if verdict["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
