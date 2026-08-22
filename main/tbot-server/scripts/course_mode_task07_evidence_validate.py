#!/usr/bin/env python3
"""Validate a Task 07 physical evidence document without touching hardware."""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import math
import re
import sys
from pathlib import Path
from typing import Any


REQUIRED_LANES = (
    "baseline-hardware-health",
    "visual-inspection",
    "embodied-behavior",
    "listening-integrity",
    "thermal-power-comfort",
    "recovery",
    "adult-end-to-end-journeys",
)
VERDICTS = {"PHYSICAL_BLOCKED", "PHYSICAL_FAIL", "PHYSICAL_PASS"}
LANE_VERDICTS = {"BLOCKED", "FAIL", "PASS"}
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
GIT_SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})\Z")
FULL_MAC_RE = re.compile(r"(?i)(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}")
SHELL_PROGRAMS = {"bash", "dash", "fish", "sh", "zsh"}
UNSAFE_TOKENS = {"erase_flash", "erase-flash", "erase_region", "erase-region"}
REQUIRED_PASS_ACTIONS = {"candidate-install", "readback", "rollback"}
APPROVED_DATA_POLICY = "adult-only-redacted-no-real-child-data"
APPROVED_CANDIDATE = {
    "backendSha": "657474ff3b58fba2c3c31f2978d53370ffad8b11",
    "espSha": "7e2628a9b9b4c3c7bbde4b426455700a4e0b7268",
    "firmwareSha": "d47174daebe17b9c1a9d1a1eb506711a57cd3512",
    "reviewedExecutableSha": "7c75ddf26ed2e495829b661c297894c2e5aa7813",
    "task06ManifestSha256": "732af1c9f4ec69f00a4c05076e7dc74f0894ae88ea266b477369db8e62a01c62",
    "firmwareIdentitySha256": "e03942e6b0c9069a98363821209cdc62ea141b52d7b6529632eb156ff2a37938",
}
APPROVED_ROLLBACK_MANIFEST_SHA256 = "3de432f3c0fb7ae29d40d1d50a720e2cd36aeb175d8413a66d1875967e4dc7db"
APPROVED_FLASH_MAP = (
    ("0x0", "bootloader/bootloader.bin"),
    ("0x8000", "partition_table/partition-table.bin"),
    ("0xd000", "ota_data_initial.bin"),
    ("0x20000", "xiaozhi.bin"),
    ("0x800000", "generated_assets.bin"),
)
APPROVED_CANDIDATE_READBACKS = (
    ("0x0", "16256", "readback/bootloader.bin"),
    ("0x8000", "3072", "readback/partition-table.bin"),
    ("0xd000", "8192", "readback/ota_data_initial.bin"),
    ("0x20000", "3611920", "readback/xiaozhi.bin"),
    ("0x800000", "5693495", "readback/generated_assets.bin"),
)
APPROVED_ROLLBACK_READBACKS = (
    ("0x0", "16256", "rollback-readback/bootloader.bin"),
    ("0x8000", "3072", "rollback-readback/partition-table.bin"),
    ("0xd000", "8192", "rollback-readback/ota_data_initial.bin"),
    ("0x20000", "3597792", "rollback-readback/xiaozhi.bin"),
    ("0x800000", "5693495", "rollback-readback/generated_assets.bin"),
)
APPROVED_CANDIDATE_READBACK_HASHES = {
    "readback/bootloader.bin": (16256, "aaa1bfe9535e78e567bc472ee5cbbfca312e50631fa407c87e36329c52d9dac6"),
    "readback/partition-table.bin": (3072, "4811619cacae08ef2e0e71b7220c6033a346ca5da7ca179082408c963ef530b5"),
    "readback/ota_data_initial.bin": (8192, "7d2c7ac4888bfd75cd5f56e8d61f69595121183afc81556c876732fd3782c62f"),
    "readback/xiaozhi.bin": (3611920, "8182dcbb3d23eac255614bf8eafac455053d4f9d5965670257d9071f6ff5e059"),
    "readback/generated_assets.bin": (5693495, "d03b074c39d78601b2a2f6c3438620adc1cf779d634825385e63cafc4528a52b"),
}
APPROVED_PRIVACY_REMEDIATED_CANDIDATE: dict[str, Any] = {
    "identity": {
        **APPROVED_CANDIDATE,
        "firmwareSha": "3d4a1e2a32359278124c61e56fd459fac618506e",
        "firmwareIdentitySha256": "7f802d9482bd1ace45875663fb2711cc61a34232f9a56a8ec7b87fb287a6f574",
    },
    "flashMap": APPROVED_FLASH_MAP,
    "readbacks": (
        ("0x0", "16256", "readback/bootloader.bin"),
        ("0x8000", "3072", "readback/partition-table.bin"),
        ("0xd000", "8192", "readback/ota-data.bin"),
        ("0x20000", "3612672", "readback/xiaozhi.bin"),
        ("0x800000", "5693495", "readback/generated-assets.bin"),
    ),
    "readbackHashes": {
        "readback/bootloader.bin": (16256, "0674a1eb42206a0f1713f7ac8fa41d7fbd09f91c6f691dc2b4a73a4c70b495fa"),
        "readback/partition-table.bin": (3072, "4811619cacae08ef2e0e71b7220c6033a346ca5da7ca179082408c963ef530b5"),
        "readback/ota-data.bin": (8192, "7d2c7ac4888bfd75cd5f56e8d61f69595121183afc81556c876732fd3782c62f"),
        "readback/xiaozhi.bin": (3612672, "84c999ece0c90eb6e69a410e335c7791f330e9c0fd39c30dfd4162bb7c4cfc6e"),
        "readback/generated-assets.bin": (5693495, "d03b074c39d78601b2a2f6c3438620adc1cf779d634825385e63cafc4528a52b"),
    },
}
APPROVED_ROLLBACK_READBACK_HASHES = {
    "rollback-readback/bootloader.bin": (16256, "03eab6ea72837189f6f95e6119b40e099b1282bd3029693f8882953d3c63cc1e"),
    "rollback-readback/partition-table.bin": (3072, "4811619cacae08ef2e0e71b7220c6033a346ca5da7ca179082408c963ef530b5"),
    "rollback-readback/ota_data_initial.bin": (8192, "7d2c7ac4888bfd75cd5f56e8d61f69595121183afc81556c876732fd3782c62f"),
    "rollback-readback/xiaozhi.bin": (3597792, "723ee925eeea12391104ba9631920cdcd57e3b8d9788302835021a1ccc5f238a"),
    "rollback-readback/generated_assets.bin": (5693495, "d03b074c39d78601b2a2f6c3438620adc1cf779d634825385e63cafc4528a52b"),
}
# Numeric limits remain intentionally empty until an authority approves them.
APPROVED_HARDWARE_LIMITS: dict[str, dict[str, Any]] = {}
REQUIRED_LANE_MEASUREMENTS = {
    "baseline-hardware-health": ("supply-voltage", "V"),
    "visual-inspection": ("visual-defect-count", "count"),
    "embodied-behavior": ("servo-settle-time", "ms"),
    "listening-integrity": ("safe-idle-uplink-packets", "count"),
    "thermal-power-comfort": ("maximum-surface-temperature", "degC"),
    "recovery": ("recovery-time", "s"),
    "adult-end-to-end-journeys": ("journey-failure-count", "count"),
}
REQUIRED_ESPTOOL_OPERATION = {
    "candidate-install": "write_flash",
    "readback": "read_flash",
    "rollback": "write_flash",
}
NON_EXECUTING_OPTIONS = {"--help", "-h", "--version", "version"}
OFFSET_RE = re.compile(r"0x[0-9a-fA-F]+\Z")
SIZE_RE = re.compile(r"(?:0x[0-9a-fA-F]+|[0-9]+)\Z")


def _is_dict(value: Any) -> bool:
    return isinstance(value, dict)


def _timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not TIMESTAMP_RE.fullmatch(value):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _sha256(value: Any) -> bool:
    return isinstance(value, str) and bool(SHA256_RE.fullmatch(value))


def _git_sha(value: Any) -> bool:
    return isinstance(value, str) and bool(GIT_SHA_RE.fullmatch(value))


def _unsafe_argv(argv: list[str]) -> str | None:
    lowered = [item.lower() for item in argv]
    if any(Path(item).name in SHELL_PROGRAMS for item in argv) or "-c" in argv:
        return "shell interpreters and -c are forbidden"
    if any(token in UNSAFE_TOKENS for token in lowered):
        return "chip or region erase is forbidden"
    if any(Path(item).name == "merged-binary.bin" for item in argv):
        return "merged-binary.bin flashing is forbidden"
    return None


def _esptool_arguments(argv: list[str]) -> list[str] | None:
    executable = Path(argv[0]).name
    if executable in {"esptool", "esptool.py"}:
        return argv[1:]
    if executable.startswith("python") and len(argv) > 1 and Path(argv[1]).name == "esptool.py":
        return argv[2:]
    return None


def _verified_esptool_action(
    argv: list[str], action_class: str, artifact_manifest_sha256: Any
) -> bool:
    arguments = _esptool_arguments(argv)
    operation = REQUIRED_ESPTOOL_OPERATION.get(action_class)
    if arguments is None or operation not in arguments:
        return False
    if any(item.lower() in NON_EXECUTING_OPTIONS for item in arguments):
        return False

    operation_index = arguments.index(operation)
    operands = arguments[operation_index + 1:]
    if operation == "read_flash":
        expected_regions = _approved_readbacks(artifact_manifest_sha256)
        return tuple(operands) in expected_regions
    flash_map = tuple(
        (operands[index], operands[index + 1])
        for index in range(len(operands) - 1)
        if OFFSET_RE.fullmatch(operands[index]) and not operands[index + 1].startswith("-")
    )
    return flash_map == _approved_flash_map(artifact_manifest_sha256)


def _readback_region(
    argv: list[str], artifact_manifest_sha256: Any
) -> tuple[str, str, str] | None:
    arguments = _esptool_arguments(argv)
    if arguments is None or "read_flash" not in arguments:
        return None
    operands = arguments[arguments.index("read_flash") + 1:]
    region = tuple(operands)
    expected_regions = _approved_readbacks(artifact_manifest_sha256)
    return region if region in expected_regions else None


def _numeric(value: Any) -> bool:
    return type(value) in {int, float} and math.isfinite(value)


def _approved_candidate_bundle() -> dict[str, Any]:
    if _replacement_candidate_bundle_error() is None and APPROVED_PRIVACY_REMEDIATED_CANDIDATE is not None:
        return APPROVED_PRIVACY_REMEDIATED_CANDIDATE
    return {
        "identity": APPROVED_CANDIDATE,
        "flashMap": APPROVED_FLASH_MAP,
        "readbacks": APPROVED_CANDIDATE_READBACKS,
        "readbackHashes": APPROVED_CANDIDATE_READBACK_HASHES,
    }


def _replacement_candidate_bundle_error() -> str | None:
    bundle = APPROVED_PRIVACY_REMEDIATED_CANDIDATE
    if bundle is None:
        return "missing"
    if not _is_dict(bundle):
        return "bundle must be an object"
    identity = bundle.get("identity")
    if not _is_dict(identity) or set(identity) != set(APPROVED_CANDIDATE):
        return "identity must contain the complete approved candidate field set"
    for field in ("backendSha", "espSha", "firmwareSha", "reviewedExecutableSha"):
        if not _git_sha(identity.get(field)):
            return f"identity.{field} must be a full Git SHA"
    for field in ("task06ManifestSha256", "firmwareIdentitySha256"):
        if not _sha256(identity.get(field)):
            return f"identity.{field} must be a SHA-256"

    flash_map = bundle.get("flashMap")
    readbacks = bundle.get("readbacks")
    hashes = bundle.get("readbackHashes")
    required_offsets = {offset for offset, _path in APPROVED_FLASH_MAP}
    if not isinstance(flash_map, (list, tuple)) or not flash_map:
        return "flashMap must be non-empty"
    if not all(
        isinstance(item, (list, tuple))
        and len(item) == 2
        and isinstance(item[0], str)
        and OFFSET_RE.fullmatch(item[0])
        and isinstance(item[1], str)
        and item[1]
        for item in flash_map
    ):
        return "flashMap entries must be offset/file pairs"
    if {item[0] for item in flash_map} != required_offsets:
        return "flashMap must cover every approved partition offset exactly once"
    if len({item[0] for item in flash_map}) != len(flash_map):
        return "flashMap offsets must be unique"

    if not isinstance(readbacks, (list, tuple)) or not readbacks:
        return "readbacks must be non-empty"
    if not all(
        isinstance(item, (list, tuple))
        and len(item) == 3
        and isinstance(item[0], str)
        and OFFSET_RE.fullmatch(item[0])
        and isinstance(item[1], str)
        and SIZE_RE.fullmatch(item[1])
        and int(item[1], 0) > 0
        and isinstance(item[2], str)
        and item[2]
        and not Path(item[2]).is_absolute()
        and ".." not in Path(item[2]).parts
        for item in readbacks
    ):
        return "readback entries must be safe offset/size/output triples"
    if {item[0] for item in readbacks} != required_offsets:
        return "readbacks must cover every approved partition offset exactly once"
    if len({item[0] for item in readbacks}) != len(readbacks):
        return "readback offsets must be unique"
    outputs = {item[2] for item in readbacks}
    if len(outputs) != len(readbacks):
        return "readback outputs must be unique"
    if not _is_dict(hashes) or set(hashes) != outputs:
        return "readbackHashes must exactly cover every readback output"
    readback_sizes = {item[2]: int(item[1], 0) for item in readbacks}
    for output, expected in hashes.items():
        if (
            not isinstance(expected, (list, tuple))
            or len(expected) != 2
            or type(expected[0]) is not int
            or expected[0] < 1
            or expected[0] != readback_sizes[output]
            or not _sha256(expected[1])
        ):
            return f"readbackHashes[{output}] must pin positive bytes and SHA-256"
    return None


def _approved_readbacks(manifest_sha256: Any) -> tuple[tuple[str, str, str], ...]:
    if manifest_sha256 == APPROVED_ROLLBACK_MANIFEST_SHA256:
        return APPROVED_ROLLBACK_READBACKS
    bundle = _approved_candidate_bundle()
    if manifest_sha256 == bundle["identity"].get("firmwareIdentitySha256"):
        return tuple(bundle["readbacks"])
    return ()


def _approved_flash_map(manifest_sha256: Any) -> tuple[tuple[str, str], ...]:
    if manifest_sha256 == APPROVED_ROLLBACK_MANIFEST_SHA256:
        return APPROVED_FLASH_MAP
    bundle = _approved_candidate_bundle()
    if manifest_sha256 == bundle["identity"].get("firmwareIdentitySha256"):
        return tuple(bundle["flashMap"])
    return ()


def validate_document(document: Any, *, evidence_root: Path | None = None) -> dict[str, Any]:
    errors: list[str] = []
    if not _is_dict(document):
        return {"valid": False, "verdict": None, "errors": ["document must be an object"], "deferredBlockers": []}

    verdict = document.get("verdict")
    deferred = document.get("deferredBlockers")
    if verdict not in VERDICTS:
        errors.append("verdict must be PHYSICAL_BLOCKED, PHYSICAL_FAIL, or PHYSICAL_PASS")
    if document.get("schemaVersion") != 1:
        errors.append("schemaVersion must be 1")
    if document.get("gate") != "course-mode-v2-task-07-physical-evidence":
        errors.append("gate must identify Course Mode V2 Task 07 physical evidence")
    if not _timestamp(document.get("generatedAt")):
        errors.append("generatedAt must be an ISO-8601 timestamp with timezone")
    if document.get("dataPolicy") != APPROVED_DATA_POLICY:
        errors.append(f"dataPolicy must be {APPROVED_DATA_POLICY}")
    if document.get("task08Locked") is not True:
        errors.append("Task 08 must remain locked in Task 07 evidence")
    if not isinstance(deferred, list) or not all(isinstance(item, str) and item for item in deferred):
        errors.append("deferredBlockers must be a list of non-empty strings")
        deferred = []

    serialized = json.dumps(document, ensure_ascii=True)
    if FULL_MAC_RE.search(serialized):
        errors.append("evidence must redact full MAC-like device identities")

    candidate = document.get("candidate")
    if not _is_dict(candidate):
        errors.append("candidate must be an object")
        candidate = {}
    for field in ("backendSha", "espSha", "firmwareSha", "reviewedExecutableSha"):
        if not _git_sha(candidate.get(field)):
            errors.append(f"candidate.{field} must be a full lowercase Git SHA")
    for field in ("task06ManifestSha256", "firmwareIdentitySha256"):
        if not _sha256(candidate.get(field)):
            errors.append(f"candidate.{field} must be a lowercase SHA-256")

    rollback = document.get("rollback")
    if not _is_dict(rollback):
        errors.append("rollback must be an object")
        rollback = {}
    if not _sha256(rollback.get("manifestSha256")):
        errors.append("rollback.manifestSha256 must be a lowercase SHA-256")

    safety = document.get("safety")
    if not _is_dict(safety):
        errors.append("safety must be an object")
        safety = {}

    robot = document.get("robot")
    if not _is_dict(robot):
        errors.append("robot must be an object")
        robot = {}

    operators = document.get("operators")
    if not isinstance(operators, list):
        errors.append("operators must be a list")
        operators = []

    environment = document.get("environment")
    if not _is_dict(environment):
        errors.append("environment must be an object")
        environment = {}

    privacy = document.get("privacy")
    if not _is_dict(privacy):
        errors.append("privacy must be an object")
        privacy = {}
    for field in (
        "safeIdleUplinkVerified",
        "openUnauthorizedUplinkFinding",
        "containmentVerified",
    ):
        if type(privacy.get(field)) is not bool:
            errors.append(f"privacy.{field} must be boolean")

    captures = document.get("captures")
    if not isinstance(captures, list):
        errors.append("captures must be a list")
        captures = []
    capture_paths: set[str] = set()
    for capture in captures:
        if not _is_dict(capture):
            errors.append("each capture must be an object")
            continue
        path = capture.get("path")
        if not isinstance(path, str) or not path or Path(path).is_absolute() or ".." in Path(path).parts:
            errors.append("capture path must be a non-empty relative path without traversal")
            continue
        if path in capture_paths:
            errors.append(f"capture path must be unique: {path}")
        capture_paths.add(path)
        if not _sha256(capture.get("sha256")):
            errors.append(f"capture {path} must have a lowercase SHA-256")
        if type(capture.get("bytes")) is not int or capture["bytes"] < 1:
            errors.append(f"capture {path} must have a positive byte size")
        if not isinstance(capture.get("mediaType"), str) or not capture["mediaType"]:
            errors.append(f"capture {path} must have a media type")
        if evidence_root is not None:
            capture_file = evidence_root / path
            root_resolved = evidence_root.resolve()
            path_parts = Path(path).parts
            has_symlink_component = any(
                (evidence_root.joinpath(*path_parts[:index])).is_symlink()
                for index in range(1, len(path_parts) + 1)
            )
            if has_symlink_component:
                errors.append(f"capture file must not be a symlink: {path}")
                continue
            try:
                contained = capture_file.resolve().is_relative_to(root_resolved)
            except OSError:
                contained = False
            if not contained:
                errors.append(f"capture file escapes evidence root: {path}")
            elif not capture_file.is_file():
                errors.append(f"capture file is missing: {path}")
            else:
                payload = capture_file.read_bytes()
                if capture.get("bytes") != len(payload):
                    errors.append(f"capture file byte size does not match: {path}")
                if capture.get("sha256") != hashlib.sha256(payload).hexdigest():
                    errors.append(f"capture file SHA-256 does not match: {path}")

    lanes = document.get("lanes")
    if not isinstance(lanes, list):
        errors.append("lanes must be a list")
        lanes = []
    lane_by_id: dict[str, dict[str, Any]] = {}
    for lane in lanes:
        if not _is_dict(lane) or not isinstance(lane.get("id"), str):
            errors.append("each lane must be an object with an id")
            continue
        lane_id = lane["id"]
        if lane_id in lane_by_id:
            errors.append(f"lane id must be unique: {lane_id}")
        lane_by_id[lane_id] = lane
        if lane.get("verdict") not in LANE_VERDICTS:
            errors.append(f"lane {lane_id} has an invalid verdict")
        references = lane.get("capturePaths")
        if not isinstance(references, list) or not all(isinstance(path, str) for path in references):
            errors.append(f"lane {lane_id} capturePaths must be a list of strings")
            references = []
        for path in references:
            if path not in capture_paths:
                errors.append(f"lane {lane_id} references unpinned capture {path}")
        measurements = lane.get("measurements")
        if not isinstance(measurements, list):
            errors.append(f"lane {lane_id} measurements must be a list")
            measurements = []
        if lane.get("verdict") == "PASS":
            if not _timestamp(lane.get("startedAt")) or not _timestamp(lane.get("endedAt")):
                errors.append(f"lane {lane_id} requires timestamped start and end for PASS")
            elif datetime.fromisoformat(lane["endedAt"].replace("Z", "+00:00")) < datetime.fromisoformat(
                lane["startedAt"].replace("Z", "+00:00")
            ):
                errors.append(f"lane {lane_id} endedAt precedes startedAt")
            if not references:
                errors.append(f"lane {lane_id} requires pinned captures for PASS")
            if not measurements:
                errors.append(f"lane {lane_id} requires measurements for PASS")
            required_measurement = REQUIRED_LANE_MEASUREMENTS.get(lane_id)
            measurement_by_name = {
                measurement.get("name"): measurement
                for measurement in measurements
                if _is_dict(measurement) and isinstance(measurement.get("name"), str)
            }
            if required_measurement is not None and required_measurement[0] not in measurement_by_name:
                errors.append(
                    f"lane {lane_id} lacks required measurement {required_measurement[0]}"
                )
            for measurement in measurements:
                if not _is_dict(measurement) or measurement.get("passed") is not True:
                    errors.append(f"lane {lane_id} has a missing or failed measurement")
                    continue
                authority = measurement.get("authority")
                if not isinstance(authority, str) or not authority or authority == "NEEDS_HUMAN_APPROVAL":
                    errors.append(f"lane {lane_id} measurement lacks approved authority")
            if required_measurement is not None and required_measurement[0] in measurement_by_name:
                measurement = measurement_by_name[required_measurement[0]]
                value = measurement.get("value")
                minimum = measurement.get("minimum")
                maximum = measurement.get("maximum")
                if not all(_numeric(item) for item in (value, minimum, maximum)):
                    errors.append(
                        f"lane {lane_id} measurement {required_measurement[0]} requires numeric value, minimum, and maximum"
                    )
                elif minimum > maximum:
                    errors.append(
                        f"lane {lane_id} measurement {required_measurement[0]} has inverted approved bounds"
                    )
                elif not minimum <= value <= maximum:
                    errors.append(
                        f"lane {lane_id} measurement {required_measurement[0]} is outside approved bounds"
                    )
                if measurement.get("unit") != required_measurement[1]:
                    errors.append(
                        f"lane {lane_id} measurement {required_measurement[0]} must use unit {required_measurement[1]}"
                    )

    missing_lanes = sorted(set(REQUIRED_LANES) - set(lane_by_id))
    extra_lanes = sorted(set(lane_by_id) - set(REQUIRED_LANES))
    if missing_lanes:
        errors.append(f"missing required lanes: {', '.join(missing_lanes)}")
    if extra_lanes:
        errors.append(f"unknown lanes: {', '.join(extra_lanes)}")

    commands = document.get("commands")
    if not isinstance(commands, list):
        errors.append("commands must be a list")
        commands = []
    action_classes: set[str] = set()
    successful_action_classes: set[str] = set()
    verified_action_classes: set[str] = set()
    successful_verified_action_classes: set[str] = set()
    invalid_verified_action_classes: set[str] = set()
    successful_readback_regions: set[tuple[str, str, str, str]] = set()
    for command in commands:
        if not _is_dict(command):
            errors.append("each command record must be an object")
            continue
        if "command" in command:
            errors.append("command evidence must not contain shell command text")
        argv = command.get("argv")
        if not isinstance(argv, list) or not argv or not all(isinstance(item, str) and item for item in argv):
            errors.append("command evidence argv must be a non-empty string array")
        else:
            unsafe = _unsafe_argv(argv)
            if unsafe:
                errors.append(f"unsafe command evidence: {unsafe}")
        action_class = command.get("actionClass")
        if isinstance(action_class, str):
            action_classes.add(action_class)
        if not isinstance(command.get("authorizedBy"), str) or not command["authorizedBy"]:
            errors.append("command evidence requires authorizedBy")
        if not _timestamp(command.get("executedAt")):
            errors.append("command evidence requires executedAt with timezone")
        if type(command.get("exitCode")) is not int:
            errors.append("command evidence requires an integer exitCode")
        elif command["exitCode"] == 0 and isinstance(action_class, str):
            successful_action_classes.add(action_class)
        if (
            isinstance(action_class, str)
            and isinstance(argv, list)
            and argv
            and _verified_esptool_action(
                argv, action_class, command.get("artifactManifestSha256")
            )
        ):
            verified_action_classes.add(action_class)
            if command.get("exitCode") == 0:
                successful_verified_action_classes.add(action_class)
                manifest_sha256 = command.get("artifactManifestSha256")
                region = _readback_region(argv, manifest_sha256)
                if action_class == "readback" and region is not None:
                    successful_readback_regions.add((manifest_sha256, *region))
        elif (
            isinstance(action_class, str)
            and action_class in REQUIRED_PASS_ACTIONS
            and command.get("exitCode") == 0
        ):
            invalid_verified_action_classes.add(action_class)

    verified_action_classes -= invalid_verified_action_classes
    successful_verified_action_classes -= invalid_verified_action_classes

    if verdict == "PHYSICAL_BLOCKED" and not deferred:
        errors.append("PHYSICAL_BLOCKED requires explicit deferred blockers")
    if verdict == "PHYSICAL_PASS":
        replacement_error = _replacement_candidate_bundle_error()
        replacement_approved = replacement_error is None
        approved_bundle = _approved_candidate_bundle()
        approved_candidate = approved_bundle["identity"]
        if APPROVED_PRIVACY_REMEDIATED_CANDIDATE is None:
            errors.append(
                "PHYSICAL_PASS is locked until a privacy-remediated candidate identity is approved"
            )
        elif not replacement_approved:
            errors.append("PHYSICAL_PASS replacement candidate approval bundle is incomplete")
        elif (
            candidate.get("firmwareIdentitySha256")
            != approved_candidate.get("firmwareIdentitySha256")
        ):
            errors.append(
                "PHYSICAL_PASS candidate does not match the approved privacy-remediated identity"
            )
        if evidence_root is None:
            errors.append("PHYSICAL_PASS requires capture files verified from the evidence root")
        if any(lane_by_id.get(lane_id, {}).get("verdict") != "PASS" for lane_id in REQUIRED_LANES):
            errors.append("PHYSICAL_PASS requires every required lane to PASS")
        if candidate.get("installed") is not True:
            errors.append("PHYSICAL_PASS requires the exact candidate to be installed")
        candidate_label = (
            "approved Task 06 candidate"
            if not replacement_approved
            else "approved privacy-remediated candidate"
        )
        for field, approved_value in approved_candidate.items():
            if candidate.get(field) != approved_value:
                errors.append(
                    f"PHYSICAL_PASS candidate.{field} does not match the {candidate_label}"
                )
        if replacement_approved:
            for lane_id, (measurement_name, unit) in REQUIRED_LANE_MEASUREMENTS.items():
                approved_limit = APPROVED_HARDWARE_LIMITS.get(lane_id)
                if approved_limit is None:
                    errors.append(
                        f"PHYSICAL_PASS has no approved hardware limit for {lane_id}/{measurement_name}"
                    )
                    continue
                measurement = next(
                    (
                        item
                        for item in lane_by_id.get(lane_id, {}).get("measurements", [])
                        if _is_dict(item) and item.get("name") == measurement_name
                    ),
                    {},
                )
                for field, expected in {
                    "unit": unit,
                    "minimum": approved_limit.get("minimum"),
                    "maximum": approved_limit.get("maximum"),
                    "authority": approved_limit.get("authority"),
                }.items():
                    if measurement.get(field) != expected:
                        errors.append(
                            f"PHYSICAL_PASS measurement {lane_id}/{measurement_name} {field} does not match the approved hardware limit"
                        )
        if rollback.get("physicallyRehearsedKnownGoodV1") is not True:
            errors.append("PHYSICAL_PASS requires a physically rehearsed known-good V1 rollback")
        if rollback.get("manifestSha256") != APPROVED_ROLLBACK_MANIFEST_SHA256:
            errors.append("PHYSICAL_PASS rollback manifest does not match the approved rollback candidate")
        if safety.get("stopPathVerified") is not True or safety.get("safeRestVerified") is not True:
            errors.append("PHYSICAL_PASS requires verified stop and safe-rest paths")
        if robot.get("formalCurrentBindingVerified") is not True:
            errors.append("PHYSICAL_PASS requires verified current robot binding")
        required_operator_roles = {"adult-operator", "adult-safety-observer"}
        present_operator_roles = {
            operator.get("role")
            for operator in operators
            if _is_dict(operator) and operator.get("present") is True
        }
        if not required_operator_roles.issubset(present_operator_roles):
            errors.append("PHYSICAL_PASS requires both adult operator roles present")
        if (
            environment.get("motionEnvelopeClear") is not True
            or environment.get("immediatePowerIsolationAvailable") is not True
        ):
            errors.append("PHYSICAL_PASS requires a clear motion envelope and immediate power isolation")
        if (
            environment.get("productionAssignmentOff") is not True
            or environment.get("globalV2FlagsOff") is not True
        ):
            errors.append("PHYSICAL_PASS requires production assignments and global V2 flags to remain off")
        if privacy.get("safeIdleUplinkVerified") is not True:
            errors.append("PHYSICAL_PASS requires a verified zero-uplink safe-idle preflight")
        if privacy.get("openUnauthorizedUplinkFinding") is not False:
            errors.append("PHYSICAL_PASS cannot retain an open unauthorized microphone uplink finding")
        if deferred:
            errors.append("PHYSICAL_PASS cannot retain deferred blockers")
        expected_manifests_by_action = {
            "candidate-install": {approved_candidate["firmwareIdentitySha256"]},
            "readback": {
                approved_candidate["firmwareIdentitySha256"],
                APPROVED_ROLLBACK_MANIFEST_SHA256,
            },
            "rollback": {APPROVED_ROLLBACK_MANIFEST_SHA256},
        }
        for command in commands:
            if not _is_dict(command):
                continue
            action_class = command.get("actionClass")
            expected_manifests = expected_manifests_by_action.get(action_class)
            if expected_manifests is not None and command.get("artifactManifestSha256") not in expected_manifests:
                errors.append(f"{action_class} command must pin the approved {'rollback' if action_class == 'rollback' else 'candidate'} manifest")
        missing_actions = sorted(REQUIRED_PASS_ACTIONS - action_classes)
        if missing_actions:
            errors.append(f"PHYSICAL_PASS lacks command evidence: {', '.join(missing_actions)}")
        for action_class in sorted(REQUIRED_PASS_ACTIONS & action_classes):
            if action_class not in successful_action_classes:
                errors.append(
                    f"PHYSICAL_PASS requires successful {action_class} command evidence"
                )
            if action_class not in verified_action_classes:
                errors.append(
                    f"PHYSICAL_PASS requires verified {action_class} esptool operation"
                )
            if action_class not in successful_verified_action_classes:
                errors.append(
                    f"PHYSICAL_PASS requires successful verified {action_class} esptool operation"
                )
        approved_candidate_readbacks = tuple(approved_bundle["readbacks"])
        for offset, size, output in approved_candidate_readbacks:
            expected = (approved_candidate["firmwareIdentitySha256"], offset, size)
            if not any(region[:3] == expected for region in successful_readback_regions):
                errors.append(
                    f"PHYSICAL_PASS requires successful readback for candidate region {offset} size {size}"
                )
            if replacement_approved:
                expected_capture = approved_bundle.get("readbackHashes", {}).get(output)
                capture = next((item for item in captures if _is_dict(item) and item.get("path") == output), None)
                if (
                    expected_capture is None
                    or capture is None
                    or (capture.get("bytes"), capture.get("sha256")) != expected_capture
                ):
                    errors.append(
                        f"PHYSICAL_PASS requires checksum-pinned candidate readback capture {output}"
                    )
        for offset, size, output in APPROVED_ROLLBACK_READBACKS:
            expected = (APPROVED_ROLLBACK_MANIFEST_SHA256, offset, size)
            if not any(region[:3] == expected for region in successful_readback_regions):
                errors.append(
                    f"PHYSICAL_PASS requires successful readback for rollback region {offset} size {size}"
                )
            if replacement_approved:
                expected_capture = APPROVED_ROLLBACK_READBACK_HASHES.get(output)
                capture = next((item for item in captures if _is_dict(item) and item.get("path") == output), None)
                if capture is None or (capture.get("bytes"), capture.get("sha256")) != expected_capture:
                    errors.append(
                        f"PHYSICAL_PASS requires checksum-pinned rollback readback capture {output}"
                    )

    return {"valid": not errors, "verdict": verdict, "errors": errors, "deferredBlockers": deferred}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path)
    arguments = parser.parse_args(argv)
    try:
        document = json.loads(arguments.evidence.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"valid": False, "verdict": None, "errors": [str(exc)], "deferredBlockers": []}, sort_keys=True))
        return 2
    result = validate_document(document, evidence_root=arguments.evidence.parent)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    sys.exit(main())
