#!/usr/bin/env python3
"""Validate and flatten immutable Task 6 firmware build manifests."""

import argparse
import hashlib
import importlib.util
import json
import os
import re
import stat
import tempfile
import time
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

SHA256_RE = re.compile(r"[0-9a-f]{64}")
COMMIT_RE = re.compile(r"[0-9a-f]{40}")
PROFILES = ("hil", "production")
ARTIFACT_LABELS = frozenset(
    {"bin", "elf", "map", "mainArchive", "projectDescription", "sdkconfig", "partitionBinary"}
)
TOP_LEVEL_FIELDS = frozenset(
    {
        "status", "profile", "sourceCommit", "sourceCommitTimestamp", "target",
        "project", "buildDirectory", "configDefaults", "artifacts", "partition", "checks",
    }
)
FLAT_FIELDS = frozenset(
    {
        "sourceCommit", "profile", "configEnabled", "sdkconfigSha256",
        "binarySha256", "elfSha256", "mapSha256", "archiveSha256",
        "binaryBytes", "appPartitionFreeBytes",
    }
)
RELEASE_ORDER = (
    "hil-flash", "hil-matrix-pass", "production-reflash",
    "production-attest", "production-soak",
)
HIL_STORAGE_SCENARIOS = (
    "evict-before-first-unlink-fail", "evict-after-unlinks-fail",
    "evict-before-rmdir-fail", "evict-after-unlinks-sd-removal",
    "sync-before-download-write-no-space", "sync-after-download-bytes-no-space",
    "sync-before-checksum-corrupt-staging", "sync-before-commit-rename-fail",
    "sync-before-commit-rename-power-loss",
)
HIL_ORDINARY_ARTIFACTS = (
    "command.txt", "serial.log", "server.log", "timeline.log",
    "build-manifest.json", "build-manifest.sha256", "status-before.json",
    "inspect-before.json", "stage-response.json", "arm-response.json",
    "trigger-response.json", "status-after.json", "inspect-after.json",
    "cleanup-response.json", "recovery-response.json", "result.json", "evidence.json",
    "validator-exit-code.txt", "SHA256SUMS",
)
_HIL_TRIGGER_INDEX = HIL_ORDINARY_ARTIFACTS.index("trigger-response.json")
HIL_POWER_LOSS_ARTIFACTS = (
    HIL_ORDINARY_ARTIFACTS[:_HIL_TRIGGER_INDEX]
    + (
        "checkpoint-reached-utc.txt", "power-removed-utc.txt", "reboot-serial.log",
        "post-reboot-inspect.json",
    )
    + HIL_ORDINARY_ARTIFACTS[_HIL_TRIGGER_INDEX + 1 :]
)
HIL_MATRIX_RECORD_FIELDS = frozenset(
    {
        "scenario", "status", "evidencePath", "evidenceSha256",
        "sha256SumsPath", "sha256SumsSha256", "validatorExitCode", "artifacts",
    }
)
ZERO_SHA256 = "0" * 64
_HIL_EVIDENCE_VALIDATOR = None
ELF_SHA256_PREFIX_LENGTH = 16


class BuildIdentityError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise BuildIdentityError(message)


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_file_snapshot(path, label):
    path = Path(path)
    require(not path.is_symlink(), f"{label} must not be a symlink")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        require(stat.S_ISREG(before.st_mode) and before.st_size > 0, f"invalid {label}")
        chunks = []
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            chunks.append(block)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    current = path.lstat()
    identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    require(
        identity == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        == (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns),
        f"{label} changed during validation",
    )
    data = b"".join(chunks)
    return data, hashlib.sha256(data).hexdigest(), identity


def _snapshot_directory(directory, expected_names):
    return {
        name: _read_file_snapshot(directory / name, f"HIL matrix artifact: {name}")[1:]
        for name in expected_names
    }


def _capture_directory(directory, expected_names):
    return {
        name: _read_file_snapshot(directory / name, f"HIL matrix artifact: {name}")
        for name in expected_names
    }


def _require_unchanged_snapshot(path, identity, label):
    current = Path(path).lstat()
    require(
        not Path(path).is_symlink()
        and identity == (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns),
        f"{label} changed during validation",
    )


def _exact_int(value, name, *, minimum=0):
    require(type(value) is int and value >= minimum, f"invalid {name}")
    return value


def _safe_relative_path(root, value, label):
    require(isinstance(value, str) and value, f"invalid {label} path")
    pure = PurePosixPath(value)
    require(not pure.is_absolute() and ".." not in pure.parts, f"unsafe {label} path")
    candidate = root.joinpath(*pure.parts)
    cursor = root
    for part in pure.parts:
        cursor = cursor / part
        require(not cursor.is_symlink(), f"{label} path must not contain a symlink")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise BuildIdentityError(f"missing {label}") from exc
    resolved_root = root.resolve(strict=True)
    require(resolved == resolved_root or resolved_root in resolved.parents, f"{label} path escapes root")
    require(resolved.is_file() and resolved.stat().st_size > 0, f"invalid {label} artifact")
    return resolved


def _read_manifest(path):
    path = Path(path)
    require(not path.is_symlink(), "manifest must not be a symlink")
    require(path.name == "lesson-storage-hil-build.json", "unexpected manifest filename")
    require(path.is_file() and path.stat().st_size > 0, "manifest missing")
    checksum = path.with_name("lesson-storage-hil-build.sha256")
    require(not checksum.is_symlink() and checksum.is_file(), "manifest checksum missing")
    parts = checksum.read_text(encoding="ascii").strip().split()
    require(
        len(parts) == 2
        and SHA256_RE.fullmatch(parts[0]) is not None
        and parts[1].lstrip("*") == path.name
        and parts[0] == sha256_file(path),
        "manifest checksum mismatch",
    )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BuildIdentityError("invalid manifest JSON") from exc
    require(isinstance(data, dict) and set(data) == TOP_LEVEL_FIELDS, "invalid Task 6 manifest fields")
    return path.resolve(), data


def _validate_artifacts(build_dir, manifest):
    artifacts = manifest.get("artifacts")
    require(isinstance(artifacts, dict) and set(artifacts) == ARTIFACT_LABELS, "invalid artifact set")
    resolved = {}
    for label in sorted(ARTIFACT_LABELS):
        record = artifacts[label]
        require(isinstance(record, dict) and set(record) == {"path", "bytes", "sha256"}, f"invalid {label} record")
        path = _safe_relative_path(build_dir, record.get("path"), label)
        size = _exact_int(record.get("bytes"), f"{label} bytes", minimum=1)
        digest = record.get("sha256")
        require(isinstance(digest, str) and SHA256_RE.fullmatch(digest), f"invalid {label} sha256")
        require(path.stat().st_size == size, f"{label} byte count mismatch")
        require(sha256_file(path) == digest, f"{label} hash mismatch")
        resolved[label] = path
    return resolved


def _firmware_root(build_dir, project_description):
    try:
        description = json.loads(project_description.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BuildIdentityError("invalid project description") from exc
    require(isinstance(description, dict), "invalid project description")
    root_value = description.get("project_path")
    build_value = description.get("build_dir")
    require(isinstance(root_value, str) and isinstance(build_value, str), "project identity missing")
    root = Path(root_value)
    require(root.is_absolute() and not root.is_symlink(), "invalid firmware project path")
    root = root.resolve(strict=True)
    require(root.is_dir() and root == build_dir.parent.resolve(strict=True), "firmware project path mismatch")
    require(Path(build_value).resolve(strict=True) == build_dir.resolve(strict=True), "build directory identity mismatch")
    return root


def _validate_defaults(root, records, profile):
    require(isinstance(records, list) and records, "config defaults missing")
    names = []
    for record in records:
        require(isinstance(record, dict) and set(record) == {"path", "sha256"}, "invalid config default record")
        relative = record.get("path")
        path = _safe_relative_path(root, relative, "config default")
        digest = record.get("sha256")
        require(isinstance(digest, str) and SHA256_RE.fullmatch(digest), "invalid config default sha256")
        require(sha256_file(path) == digest, "config default hash mismatch")
        names.append(path.name)
    has_hil = "sdkconfig.defaults.hil-storage" in names
    require(has_hil == (profile == "hil"), "config default profile mismatch")


def load_build_identity(manifest_path, *, expected_profile=None):
    manifest_path, manifest = _read_manifest(manifest_path)
    build_dir = manifest_path.parent
    require(not build_dir.is_symlink(), "build directory must not be a symlink")
    profile = manifest.get("profile")
    require(profile in PROFILES, "invalid build profile")
    if expected_profile is not None:
        require(expected_profile in PROFILES and profile == expected_profile, "foreign build profile")
    require(manifest.get("status") == "PASS", "Task 6 manifest is not PASS")
    source_commit = manifest.get("sourceCommit")
    require(isinstance(source_commit, str) and COMMIT_RE.fullmatch(source_commit), "invalid source commit")
    _exact_int(manifest.get("sourceCommitTimestamp"), "source commit timestamp", minimum=1)
    require(manifest.get("target") == "esp32s3" and manifest.get("project") == "xiaozhi", "foreign firmware target")
    require(manifest.get("buildDirectory") == build_dir.name, "build directory name mismatch")
    artifacts = _validate_artifacts(build_dir, manifest)
    root = _firmware_root(build_dir, artifacts["projectDescription"])
    _validate_defaults(root, manifest.get("configDefaults"), profile)
    checks = manifest.get("checks")
    require(
        isinstance(checks, dict)
        and set(checks) == {"hilConfigEnabled", "toolLiterals", "hilSymbols", "bannedApis"}
        and type(checks.get("hilConfigEnabled")) is bool,
        "invalid build checks",
    )
    enabled = checks["hilConfigEnabled"]
    require(enabled == (profile == "hil"), "HIL config/profile mismatch")
    require(checks.get("toolLiterals") == ("present" if enabled else "absent"), "tool literal profile mismatch")
    require(checks.get("hilSymbols") == ("present" if enabled else "absent"), "symbol profile mismatch")
    require(checks.get("bannedApis") == "absent", "banned API audit failed")
    partition = manifest.get("partition")
    require(
        isinstance(partition, dict)
        and set(partition) == {"partitionTable", "partitionBytes", "imageBytes", "freeBytes", "freePercent"},
        "invalid partition record",
    )
    image_bytes = _exact_int(partition.get("imageBytes"), "partition image bytes", minimum=1)
    free_bytes = _exact_int(partition.get("freeBytes"), "partition free bytes", minimum=1)
    partition_bytes = _exact_int(partition.get("partitionBytes"), "partition bytes", minimum=1)
    require(image_bytes == artifacts["bin"].stat().st_size, "partition image size mismatch")
    require(image_bytes + free_bytes == partition_bytes, "partition arithmetic mismatch")
    require(type(partition.get("freePercent")) in (int, float), "invalid partition free percent")
    binding = {
        "sourceCommit": source_commit,
        "profile": profile,
        "configEnabled": enabled,
        "sdkconfigSha256": sha256_file(artifacts["sdkconfig"]),
        "binarySha256": sha256_file(artifacts["bin"]),
        "elfSha256": sha256_file(artifacts["elf"]),
        "mapSha256": sha256_file(artifacts["map"]),
        "archiveSha256": sha256_file(artifacts["mainArchive"]),
        "binaryBytes": artifacts["bin"].stat().st_size,
        "appPartitionFreeBytes": free_bytes,
    }
    require(set(binding) == FLAT_FIELDS, "internal build binding error")
    return binding


def validate_build_pair(hil_manifest, production_manifest):
    hil = load_build_identity(hil_manifest, expected_profile="hil")
    production = load_build_identity(production_manifest, expected_profile="production")
    require(hil["sourceCommit"] == production["sourceCommit"], "paired builds use different source commits")
    require(hil["binarySha256"] != production["binarySha256"], "HIL and production binaries must differ")
    return {"sourceCommit": hil["sourceCommit"], "hil": hil, "production": production}


def build_flash_attestation(
    manifest_path, esptool_log_path, exit_code_path, device_mac, started_at, completed_at
):
    identity = load_build_identity(manifest_path, expected_profile="hil")
    manifest_path, manifest = _read_manifest(manifest_path)
    artifacts = _validate_artifacts(manifest_path.parent, manifest)
    app = artifacts["bin"]
    log_path, log_sha256, log_bytes, _log_identity = _release_evidence(esptool_log_path)
    exit_path, exit_sha256, exit_bytes, _exit_identity = _release_evidence(exit_code_path)
    require(exit_bytes == b"0\n", "esptool flash exit code is not zero")
    try:
        log = log_bytes.decode("utf-8")
    except UnicodeError as exc:
        raise BuildIdentityError("invalid esptool flash log") from exc
    require(
        re.fullmatch(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}", device_mac) is not None,
        "invalid flashed device MAC",
    )
    require(
        "erase_flash" not in log and "erase_region" not in log,
        "flash evidence contains prohibited erase command",
    )
    command = re.compile(
        rf"^.*--before default_reset --after hard_reset write_flash "
        rf"0x20000 {re.escape(str(app))}$",
        re.MULTILINE,
    )
    require(len(command.findall(log)) == 1, "invalid esptool application flash command")
    require(log.count(f"MAC: {device_mac}") == 1, "flashed device MAC mismatch")
    wrote = re.findall(
        rf"^Wrote {app.stat().st_size} bytes .* at 0x00020000.*$", log, re.MULTILINE
    )
    require(len(wrote) == 1, "flashed application byte count or offset mismatch")
    require(log.count("Hash of data verified.") == 1, "esptool hash verification missing")
    started = _strict_utc(started_at)
    completed = _strict_utc(completed_at)
    require(started < completed, "flash receipt timestamps are not increasing")
    return {
        "status": "PASS",
        "event": "hil-flash-write",
        "deviceId": device_mac,
        "startedAt": started_at,
        "completedAt": completed_at,
        "manifestPath": str(manifest_path),
        "buildIdentity": identity,
        "application": {
            "path": str(app),
            "offset": "0x00020000",
            "bytes": app.stat().st_size,
            "sha256": sha256_file(app),
        },
        "esptoolLog": {"path": str(log_path), "sha256": log_sha256},
        "exitCode": {"path": str(exit_path), "sha256": exit_sha256, "value": 0},
    }


def _validate_flash_receipt(receipt, identity):
    require(
        isinstance(receipt, dict)
        and set(receipt) == {
            "status", "event", "deviceId", "startedAt", "completedAt",
            "manifestPath", "buildIdentity", "application", "esptoolLog", "exitCode",
        }
        and isinstance(receipt.get("esptoolLog"), dict)
        and set(receipt["esptoolLog"]) == {"path", "sha256"}
        and isinstance(receipt.get("exitCode"), dict)
        and set(receipt["exitCode"]) == {"path", "sha256", "value"}
        and isinstance(receipt.get("application"), dict)
        and set(receipt["application"]) == {"path", "offset", "bytes", "sha256"},
        "invalid HIL flash receipt",
    )
    rebuilt = build_flash_attestation(
        receipt.get("manifestPath"),
        receipt.get("esptoolLog", {}).get("path"),
        receipt.get("exitCode", {}).get("path"),
        receipt.get("deviceId"),
        receipt.get("startedAt"),
        receipt.get("completedAt"),
    )
    require(rebuilt == receipt and receipt.get("buildIdentity") == identity, "HIL flash receipt mismatch")
    return receipt


def build_boot_attestation(
    manifest_path, flash_receipt_path, boot_capture_receipt_path,
    connection_attestation_path, observed_at, event,
):
    expected_profile = "hil"
    require(event == "hil-flash", "invalid boot event")
    identity = load_build_identity(manifest_path, expected_profile=expected_profile)
    _receipt_path, _receipt_digest, receipt_bytes, _receipt_identity = _release_evidence(flash_receipt_path)
    receipt = _load_evidence_json_bytes(receipt_bytes)
    _validate_flash_receipt(receipt, identity)
    _capture_path, _capture_digest, capture_bytes, _capture_identity = _release_evidence(
        boot_capture_receipt_path
    )
    capture = _load_evidence_json_bytes(capture_bytes)
    _validate_boot_capture_receipt(capture, identity, receipt)
    _path, _digest, attestation_bytes, _attestation_identity = _release_evidence(connection_attestation_path)
    attestation = _load_evidence_json_bytes(attestation_bytes)
    require(
        set(attestation) == {
            "status", "deviceId", "deviceUuid", "connectionIdentity", "buildIdentity",
        }
        and attestation.get("status") == "PASS"
        and attestation.get("buildIdentity") == identity,
        "live boot/connection attestation mismatch",
    )
    device_id = attestation.get("deviceId")
    device_uuid = attestation.get("deviceUuid")
    require(
        isinstance(device_id, str)
        and re.fullmatch(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}", device_id)
        and isinstance(device_uuid, str)
        and re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
            device_uuid,
            re.IGNORECASE,
        )
        and attestation.get("connectionIdentity")
        == {"deviceId": device_id, "clientId": device_uuid},
        "invalid live boot connection identity",
    )
    prefix = capture["elfSha256Prefix"]
    observed = _strict_utc(observed_at)
    require(
        _strict_utc(capture["hilMarkerUtc"]) < observed,
        "live preflight does not follow captured boot evidence",
    )
    require(receipt["deviceId"] == device_id, "flash and boot device identity mismatch")
    return {
        "status": "PASS",
        "event": event,
        "buildIdentity": identity,
        "bootIdentity": {
            "sourceCommit": identity["sourceCommit"],
            "profile": identity["profile"],
            "configEnabled": identity["configEnabled"],
            "binarySha256": identity["binarySha256"],
        },
        "flashReceipt": receipt,
        "bootCaptureReceipt": capture,
        "bootEvidence": {
            "observedAt": observed_at,
            "elfSha256Prefix": prefix,
            "deviceId": device_id,
            "deviceUuid": device_uuid,
            "connectionIdentity": attestation["connectionIdentity"],
        },
    }


def _validate_boot_capture_receipt(capture, identity, flash_receipt):
    require(
        isinstance(capture, dict)
        and set(capture) == {
            "status", "event", "serialPort", "serialPath", "serialSha256",
            "captureStartedUtc", "resetUtc", "elfMarkerUtc", "hilMarkerUtc",
            "elfSha256Prefix", "timedOut",
        }
        and capture.get("status") == "PASS"
        and capture.get("event") == "hil-boot-capture"
        and capture.get("timedOut") is False,
        "invalid HIL boot capture receipt",
    )
    serial_path, serial_sha256, serial_bytes, _serial_identity = _release_evidence(
        capture.get("serialPath")
    )
    prefix = parse_running_elf_sha256(serial_bytes, identity["elfSha256"])
    elf_match = re.search(
        rb"app_init: ELF file SHA256:\s+[0-9a-f]{16}\.\.\.", serial_bytes
    )
    hil_marker = b"TBOT_HIL_STORAGE_FAULTS_ENABLED non-production-image"
    require(
        str(serial_path) == capture.get("serialPath")
        and serial_sha256 == capture.get("serialSha256")
        and prefix == capture.get("elfSha256Prefix")
        and serial_bytes.count(hil_marker) == 1
        and elf_match is not None
        and elf_match.start() < serial_bytes.find(hil_marker),
        "HIL boot capture content mismatch",
    )
    capture_started = _strict_utc(capture.get("captureStartedUtc"))
    reset = _strict_utc(capture.get("resetUtc"))
    elf_marker = _strict_utc(capture.get("elfMarkerUtc"))
    hil_marker = _strict_utc(capture.get("hilMarkerUtc"))
    require(
        capture_started <= reset
        and _strict_utc(flash_receipt["completedAt"]) < reset < elf_marker <= hil_marker,
        "HIL boot capture timestamp order invalid",
    )
    return capture


def _validate_flat_identity(value, expected_profile):
    require(isinstance(value, dict) and set(value) == FLAT_FIELDS, "invalid flat build identity")
    require(value.get("profile") == expected_profile, "foreign flat build profile")
    require(value.get("configEnabled") is (expected_profile == "hil"), "flat build config mismatch")
    require(isinstance(value.get("sourceCommit"), str) and COMMIT_RE.fullmatch(value["sourceCommit"]), "invalid flat source commit")
    for name in ("sdkconfigSha256", "binarySha256", "elfSha256", "mapSha256", "archiveSha256"):
        require(isinstance(value.get(name), str) and SHA256_RE.fullmatch(value[name]), f"invalid flat build hash: {name}")
    for name in ("binaryBytes", "appPartitionFreeBytes"):
        _exact_int(value.get(name), f"flat {name}", minimum=1)
    return value


def _strict_utc(value):
    require(
        isinstance(value, str)
        and re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z", value),
        "invalid release receipt UTC",
    )
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        raise BuildIdentityError("invalid release receipt UTC") from None
    require(parsed.tzinfo == timezone.utc, "invalid release receipt UTC")
    return parsed


def _utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _elf_sha256_prefixes(serial_bytes):
    text = serial_bytes.decode("utf-8", errors="replace")
    return re.findall(
        rf"app_init: ELF file SHA256:\s+([0-9a-f]{{{ELF_SHA256_PREFIX_LENGTH}}})\.\.\.",
        text,
    )


def parse_running_elf_sha256(serial_bytes, expected_sha256):
    require(
        isinstance(expected_sha256, str) and SHA256_RE.fullmatch(expected_sha256),
        "invalid expected ELF SHA-256",
    )
    prefixes = _elf_sha256_prefixes(serial_bytes)
    require(len(prefixes) == 1, "running ELF identity marker missing or ambiguous")
    prefix = prefixes[0]
    require(expected_sha256.startswith(prefix), "running ELF identity mismatch")
    return prefix


def capture_hil_boot_identity(
    serial_port, output_path, receipt_path, *, timeout_seconds=60,
    serial_factory=None, utc_now=_utc_now, monotonic=time.monotonic, sleep=time.sleep,
):
    require(type(timeout_seconds) in (int, float) and timeout_seconds > 0, "invalid boot capture timeout")
    if serial_factory is None:
        try:
            import serial  # type: ignore
        except ImportError as exc:
            raise BuildIdentityError("pyserial is required for HIL boot capture") from exc
        serial_factory = serial.Serial
    output_path = Path(output_path)
    receipt_path = Path(receipt_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    require(not output_path.exists() and not receipt_path.exists(), "boot capture output already exists")
    port = serial_factory(serial_port, 115200, timeout=0.25)
    data = bytearray()
    capture_started = None
    elf_marker_utc = None
    hil_marker_utc = None
    reset_utc = None
    try:
        port.setDTR(False)
        port.setRTS(False)
        port.reset_input_buffer()
        capture_started = utc_now()
        port.setRTS(True)
        sleep(0.1)
        port.setRTS(False)
        reset_utc = utc_now()
        sleep(0.2)
        deadline = monotonic() + timeout_seconds
        while monotonic() < deadline:
            chunk = port.read(4096)
            if chunk:
                data.extend(chunk)
                current = bytes(data)
                if elf_marker_utc is None and _elf_sha256_prefixes(current):
                    elf_marker_utc = utc_now()
                if (
                    hil_marker_utc is None
                    and b"TBOT_HIL_STORAGE_FAULTS_ENABLED non-production-image" in current
                ):
                    hil_marker_utc = utc_now()
                if elf_marker_utc is not None and hil_marker_utc is not None:
                    break
    finally:
        with suppress(Exception):
            port.setDTR(False)
        with suppress(Exception):
            port.setRTS(False)
        port.close()
    descriptor = os.open(output_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    with os.fdopen(descriptor, "wb") as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())
    _fsync_directory(output_path.parent)
    serial_bytes = bytes(data)
    prefixes = _elf_sha256_prefixes(serial_bytes)
    hil_marker = b"TBOT_HIL_STORAGE_FAULTS_ENABLED non-production-image"
    elf_match = re.search(
        rb"app_init: ELF file SHA256:\s+[0-9a-f]{16}\.\.\.", serial_bytes
    )
    markers_valid = (
        len(prefixes) == 1
        and serial_bytes.count(hil_marker) == 1
        and elf_match is not None
        and elf_match.start() < serial_bytes.find(hil_marker)
    )
    timed_out = elf_marker_utc is None or hil_marker_utc is None or not markers_valid
    receipt = {
        "status": "NOT_PASS" if timed_out else "PASS",
        "event": "hil-boot-capture",
        "serialPort": serial_port,
        "serialPath": str(output_path.resolve()),
        "serialSha256": sha256_file(output_path),
        "captureStartedUtc": capture_started,
        "resetUtc": reset_utc,
        "elfMarkerUtc": elf_marker_utc,
        "hilMarkerUtc": hil_marker_utc,
        "elfSha256Prefix": (
            prefixes[0] if len(prefixes) == 1 else None
        ),
        "timedOut": timed_out,
    }
    atomic_write_json(receipt_path, receipt)
    _fsync_directory(receipt_path.parent)
    require(not timed_out, "timed out waiting for HIL boot identity markers")
    return receipt


def _release_evidence(path, checksum_path=None):
    path = Path(path)
    data, digest, identity = _read_file_snapshot(path, "release evidence")
    if checksum_path is not None:
        checksum_path = Path(checksum_path)
        require(not checksum_path.is_symlink() and checksum_path.is_file(), "release evidence checksum missing")
        parts = checksum_path.read_text(encoding="ascii").strip().split()
        require(
            len(parts) == 2 and parts[0] == digest
            and parts[1].lstrip("*") == path.name,
            "release evidence checksum mismatch",
        )
    return path.resolve(), digest, data, identity


def _load_evidence_json(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BuildIdentityError("invalid release evidence JSON") from exc
    require(isinstance(value, dict), "release evidence must be an object")
    return value


def _load_evidence_json_bytes(data):
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise BuildIdentityError("invalid release evidence JSON") from exc
    require(isinstance(value, dict), "release evidence must be an object")
    return value


def _validate_boot_evidence(value, event, identity):
    require(value.get("status") == "PASS" and value.get("event") == event, "boot evidence is not PASS")
    require(value.get("buildIdentity") == identity, "boot evidence build mismatch")
    boot = value.get("bootIdentity")
    require(
        isinstance(boot, dict)
        and set(boot) == {"sourceCommit", "profile", "configEnabled", "binarySha256"},
        "invalid boot identity evidence",
    )
    require(
        boot == {
            "sourceCommit": identity["sourceCommit"],
            "profile": identity["profile"],
            "configEnabled": identity["configEnabled"],
            "binarySha256": identity["binarySha256"],
        },
        "boot identity evidence mismatch",
    )
    if event == "hil-flash":
        receipt = _validate_flash_receipt(value.get("flashReceipt"), identity)
        capture = _validate_boot_capture_receipt(
            value.get("bootCaptureReceipt"), identity, receipt
        )
        evidence = value.get("bootEvidence")
        require(
            isinstance(evidence, dict)
            and set(evidence) == {
                "observedAt", "elfSha256Prefix",
                "deviceId", "deviceUuid", "connectionIdentity",
            },
            "invalid running HIL boot evidence",
        )
        prefix = capture["elfSha256Prefix"]
        require(
            evidence.get("elfSha256Prefix") == prefix
            and identity["elfSha256"].startswith(prefix),
            "running HIL identity mismatch",
        )
        require(
            _strict_utc(capture["hilMarkerUtc"]) < _strict_utc(evidence.get("observedAt")),
            "running HIL preflight predates captured boot markers",
        )
        require(
            evidence.get("deviceId") == receipt["deviceId"]
            and evidence.get("connectionIdentity") == {
                "deviceId": evidence.get("deviceId"),
                "clientId": evidence.get("deviceUuid"),
            },
            "running HIL connection identity mismatch",
        )


def _load_matrix_json(path, label):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BuildIdentityError(f"invalid HIL matrix {label} JSON") from exc
    require(isinstance(value, dict), f"invalid HIL matrix {label} JSON")
    return value


def _matrix_checksums(directory, expected_names):
    checksum_path = directory / "SHA256SUMS"
    rows = checksum_path.read_text(encoding="ascii").splitlines()
    expected_artifacts = tuple(name for name in expected_names if name != "SHA256SUMS")
    require(len(rows) == len(expected_artifacts), "HIL matrix checksum file set mismatch")
    declared = {}
    for row in rows:
        match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9][A-Za-z0-9._-]*)", row)
        require(match is not None, "invalid HIL matrix checksum manifest")
        digest, name = match.groups()
        require(name not in declared, "duplicate HIL matrix checksum entry")
        declared[name] = digest
    require(set(declared) == set(expected_artifacts), "HIL matrix checksum file set mismatch")
    for name in expected_artifacts:
        artifact = directory / name
        require(
            not artifact.is_symlink() and artifact.is_file() and artifact.stat().st_size > 0,
            f"invalid HIL matrix artifact: {name}",
        )
        require(
            declared[name] == sha256_file(artifact),
            f"HIL matrix artifact checksum mismatch: {name}",
        )
    return declared


def _hil_evidence_validator():
    global _HIL_EVIDENCE_VALIDATOR
    if _HIL_EVIDENCE_VALIDATOR is None:
        path = Path(__file__).with_name("lesson_studio_task14_fault_driver.py")
        spec = importlib.util.spec_from_file_location(
            "lesson_studio_task14_release_evidence_validator", path
        )
        require(spec is not None and spec.loader is not None, "HIL validator unavailable")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _HIL_EVIDENCE_VALIDATOR = module
    return _HIL_EVIDENCE_VALIDATOR


def _validate_matrix_recovery(scenario, scenario_dir, result):
    errors = _hil_evidence_validator()._hil_recovery_artifact_errors(
        scenario, scenario_dir, result
    )
    require(not errors, f"invalid independent HIL recovery evidence: {'; '.join(errors)}")


def _validate_matrix_validator_evidence(scenario, evidence, result):
    require(
        set(evidence) == {
            "scenario", "status", "capturedAt", "validationErrors",
            "cleanupInspection", "finalStatus",
        },
        "invalid independent HIL validator evidence fields",
    )
    captured_at = evidence.get("capturedAt")
    require(isinstance(captured_at, str), "invalid independent HIL validator UTC")
    try:
        parsed = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
    except ValueError:
        raise BuildIdentityError("invalid independent HIL validator UTC") from None
    require(parsed.tzinfo == timezone.utc, "invalid independent HIL validator UTC")
    require(
        evidence.get("scenario") == scenario
        and evidence.get("status") == "PASS"
        and evidence.get("validationErrors") == []
        and evidence.get("cleanupInspection") == result.get("cleanupInspection")
        and evidence.get("finalStatus") == result.get("finalStatus"),
        "independent HIL validator evidence is not bound to the scenario result",
    )


def _require_canonical_matrix_path(evidence_path):
    evidence_path = Path(evidence_path)
    require(
        evidence_path.name == "hil-matrix-report.json"
        and evidence_path.parent.name == "storage-hil"
        and not evidence_path.parent.is_symlink()
        and evidence_path.parent.resolve(strict=True) == evidence_path.parent.absolute(),
        "HIL matrix report is not under the canonical PASS root",
    )
    require(
        not any(
            "failure" in part.lower() or "quarantine" in part.lower()
            for part in evidence_path.parts
        ),
        "HIL matrix failure/quarantine path is not release evidence",
    )


def _validate_hil_matrix(
    value, pair, evidence_path, report_bytes=None, report_identity=None
):
    evidence_path = Path(evidence_path)
    _require_canonical_matrix_path(evidence_path)
    if report_bytes is None:
        report_bytes, _report_digest, report_identity = _read_file_snapshot(
            evidence_path, "HIL matrix report"
        )
    else:
        require(report_identity is not None, "HIL matrix report snapshot identity missing")
    require(
        _load_evidence_json_bytes(report_bytes) == value,
        "HIL matrix report bytes do not match parsed evidence",
    )
    root = evidence_path.parent
    captured = {}
    captured_directories = {}
    for scenario in HIL_STORAGE_SCENARIOS:
        scenario_dir = root / scenario
        require(
            not scenario_dir.is_symlink()
            and scenario_dir.is_dir()
            and scenario_dir.parent == root,
            "HIL matrix scenario directory is not canonical",
        )
        directory_stat = scenario_dir.lstat()
        captured_directories[scenario] = (
            directory_stat.st_dev, directory_stat.st_ino,
            directory_stat.st_size, directory_stat.st_mtime_ns,
        )
        expected = (
            HIL_POWER_LOSS_ARTIFACTS
            if scenario == HIL_STORAGE_SCENARIOS[-1]
            else HIL_ORDINARY_ARTIFACTS
        )
        require(
            {path.name for path in scenario_dir.iterdir()} == set(expected),
            "HIL matrix scenario file set mismatch",
        )
        captured[scenario] = _capture_directory(scenario_dir, expected)
    with tempfile.TemporaryDirectory(prefix="task14-hil-release-") as temporary:
        snapshot_root = Path(temporary) / "storage-hil"
        snapshot_root.mkdir()
        snapshot_report = snapshot_root / "hil-matrix-report.json"
        snapshot_report.write_bytes(report_bytes)
        for scenario, artifacts in captured.items():
            snapshot_dir = snapshot_root / scenario
            snapshot_dir.mkdir()
            for name, (data, _digest, _identity) in artifacts.items():
                (snapshot_dir / name).write_bytes(data)
        _validate_hil_matrix_snapshot(value, pair, snapshot_report)
    _require_unchanged_snapshot(evidence_path, report_identity, "HIL matrix report")
    for scenario, artifacts in captured.items():
        scenario_dir = root / scenario
        _require_unchanged_snapshot(
            scenario_dir, captured_directories[scenario],
            f"HIL matrix scenario directory: {scenario}",
        )
        require(
            {path.name for path in scenario_dir.iterdir()} == set(artifacts),
            "HIL matrix scenario file set changed during validation",
        )
        for name, (_data, _digest, identity) in artifacts.items():
            _require_unchanged_snapshot(
                root / scenario / name, identity, f"HIL matrix artifact: {name}"
            )


def _validate_hil_matrix_snapshot(value, pair, evidence_path):
    require(
        set(value) == {
            "status", "event", "buildIdentity", "deviceId", "deviceUuid",
            "connectionIdentity", "scenarios",
        },
        "invalid HIL matrix fields",
    )
    require(
        value.get("status") == "PASS" and value.get("event") == "hil-matrix-pass",
        "HIL matrix is not PASS",
    )
    require(value.get("buildIdentity") == pair["hil"], "HIL matrix build mismatch")
    device_id = value.get("deviceId")
    device_uuid = value.get("deviceUuid")
    connection = value.get("connectionIdentity")
    require(
        isinstance(device_id, str)
        and re.fullmatch(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}", device_id),
        "invalid HIL matrix device MAC",
    )
    require(
        isinstance(device_uuid, str)
        and re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
            device_uuid,
            re.IGNORECASE,
        ),
        "invalid HIL matrix device UUID",
    )
    require(
        connection == {"deviceId": device_id, "clientId": device_uuid},
        "HIL matrix connection identity mismatch",
    )
    scenarios = value.get("scenarios")
    require(
        isinstance(scenarios, list)
        and len(scenarios) == len(HIL_STORAGE_SCENARIOS)
        and all(isinstance(item, dict) for item in scenarios)
        and [item.get("scenario") for item in scenarios] == list(HIL_STORAGE_SCENARIOS),
        "HIL matrix scenario evidence incomplete",
    )
    root = Path(evidence_path).parent.resolve(strict=True)
    for index, scenario in enumerate(HIL_STORAGE_SCENARIOS):
        record = scenarios[index]
        require(
            isinstance(record, dict) and set(record) == HIL_MATRIX_RECORD_FIELDS,
            "invalid HIL matrix scenario fields",
        )
        require(record.get("status") == "PASS", "HIL matrix scenario is not PASS")
        require(
            record.get("validatorExitCode") == 0
            and type(record.get("validatorExitCode")) is int,
            "HIL matrix scenario validator did not succeed",
        )
        evidence_relative = f"{scenario}/evidence.json"
        sums_relative = f"{scenario}/SHA256SUMS"
        require(
            record.get("evidencePath") == evidence_relative
            and record.get("sha256SumsPath") == sums_relative,
            "HIL matrix scenario evidence path mismatch",
        )
        scenario_evidence = _safe_relative_path(root, evidence_relative, "HIL matrix evidence")
        checksum_path = _safe_relative_path(root, sums_relative, "HIL matrix checksum")
        scenario_dir = scenario_evidence.parent
        require(
            checksum_path.parent == scenario_dir
            and scenario_dir.parent == root
            and not scenario_dir.is_symlink(),
            "HIL matrix scenario paths diverge",
        )
        expected = (
            HIL_POWER_LOSS_ARTIFACTS
            if scenario == HIL_STORAGE_SCENARIOS[-1]
            else HIL_ORDINARY_ARTIFACTS
        )
        require(
            {path.name for path in scenario_dir.iterdir()} == set(expected),
            "HIL matrix scenario file set mismatch",
        )
        initial_snapshot = _snapshot_directory(scenario_dir, expected)
        declared = _matrix_checksums(scenario_dir, expected)
        require(record.get("artifacts") == declared, "HIL matrix artifact binding mismatch")
        require(
            record.get("evidenceSha256") == declared["evidence.json"]
            == sha256_file(scenario_evidence),
            "HIL matrix evidence hash mismatch",
        )
        require(
            record.get("sha256SumsSha256") == sha256_file(checksum_path),
            "HIL matrix checksum manifest hash mismatch",
        )
        require(
            (scenario_dir / "validator-exit-code.txt").read_bytes() == b"0\n",
            "HIL matrix scenario validator did not succeed",
        )
        scenario_value = _load_matrix_json(scenario_evidence, "evidence")
        result = _load_matrix_json(scenario_dir / "result.json", "result")
        require(
            result.get("scenario") == scenario and result.get("status") == "PASS",
            "HIL matrix scenario result is not PASS",
        )
        require(result.get("buildIdentity") == pair["hil"], "HIL matrix scenario build mismatch")
        require(result.get("deviceId") == device_id, "HIL matrix scenario device mismatch")
        require(result.get("deviceUuid") == device_uuid, "HIL matrix scenario UUID mismatch")
        require(
            result.get("connectionIdentity") == connection,
            "HIL matrix scenario connection mismatch",
        )
        _validate_matrix_validator_evidence(scenario, scenario_value, result)
        _validate_matrix_recovery(scenario, scenario_dir, result)
        validator = _hil_evidence_validator()
        independent_errors = [
            *validator.validate_hil_storage_result(scenario, result),
            *validator._hil_storage_artifact_errors(scenario, scenario_dir, result),
        ]
        require(
            not independent_errors,
            f"invalid independent HIL scenario evidence: {'; '.join(independent_errors)}",
        )
        regenerated = validator.build_hil_storage_report(scenario, scenario_dir)
        require(
            {
                key: scenario_value.get(key)
                for key in (
                    "scenario", "status", "validationErrors",
                    "cleanupInspection", "finalStatus",
                )
            }
            == {
                key: regenerated.get(key)
                for key in (
                    "scenario", "status", "validationErrors",
                    "cleanupInspection", "finalStatus",
                )
            },
            "stored HIL validator evidence differs from independent regeneration",
        )
        require(
            _load_matrix_json(scenario_dir / "build-manifest.json", "build manifest")
            == pair["hil"],
            "HIL matrix scenario build manifest mismatch",
        )
        build_manifest_path = scenario_dir / "build-manifest.json"
        build_checksum_parts = (
            scenario_dir / "build-manifest.sha256"
        ).read_text(encoding="ascii").strip().split()
        require(
            len(build_checksum_parts) == 2
            and build_checksum_parts[0] == sha256_file(build_manifest_path)
            and build_checksum_parts[1].lstrip("*") == build_manifest_path.name,
            "HIL matrix scenario build manifest checksum mismatch",
        )
        require(
            _matrix_checksums(scenario_dir, expected) == declared,
            "HIL matrix scenario artifacts changed during validation",
        )
        require(
            _snapshot_directory(scenario_dir, expected) == initial_snapshot,
            "HIL matrix scenario artifacts changed during validation",
        )


def _validate_event_evidence(
    event, value, pair, prior_ledger, evidence_path,
    evidence_bytes=None, evidence_identity=None,
):
    if event == "hil-flash":
        _validate_boot_evidence(value, event, pair["hil"])
    elif event == "hil-matrix-pass":
        _validate_hil_matrix(
            value, pair, evidence_path,
            report_bytes=evidence_bytes, report_identity=evidence_identity,
        )
    elif event == "production-reflash":
        _validate_boot_evidence(value, event, pair["production"])
    elif event == "production-attest":
        require(value.get("status") == "PASS" and value.get("event") == event, "production attestation is not PASS")
        require(value.get("buildIdentity") == pair["production"], "production attestation build mismatch")
        require(value.get("hilToolsAbsent") is True, "production attestation did not exclude HIL tools")
        require(value.get("sourceCommit") == pair["sourceCommit"], "production attestation source mismatch")
        require(value.get("binarySha256") == pair["production"]["binarySha256"], "production attestation binary mismatch")
    else:
        require(value.get("status") == "PASS", "production soak report is not PASS")
        require(value.get("minimumTransitionsRequired") == 104, "production soak report transition gate missing")
        metrics = value.get("metrics")
        require(
            isinstance(metrics, dict)
            and type(metrics.get("transitions")) is int and metrics["transitions"] >= 104
            and type(metrics.get("sessions")) is int and metrics["sessions"] > 0,
            "production soak metrics incomplete",
        )
        checks = value.get("checks")
        require(isinstance(checks, dict) and checks and all(item is True for item in checks.values()), "production soak checks are not PASS")
        require(value.get("buildIdentity") == pair["production"], "production soak build mismatch")
        require(value.get("releaseLedgerEvidence") == prior_ledger, "production soak report lacks prerequisite ledger")


def _fsync_directory(path):
    descriptor = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _exclusive_json(path, value):
    data = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
    except BaseException:
        with suppress(FileNotFoundError):
            os.unlink(path)
        raise
    _fsync_directory(Path(path).parent)
    return hashlib.sha256(data).hexdigest()


def load_release_ledger(path, *, production_identity, required_event=None):
    path = Path(path)
    require(not path.is_symlink() and path.is_dir(), "release ledger missing")
    index_path = path / "index.json"
    require(not index_path.is_symlink() and index_path.is_file(), "release ledger index missing")
    try:
        index_value = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BuildIdentityError("invalid release ledger JSON") from exc
    require(
        isinstance(index_value, dict)
        and set(index_value) == {"sourceCommit", "hil", "production", "receiptFiles", "headSha256"},
        "invalid release ledger index fields",
    )
    hil = _validate_flat_identity(index_value.get("hil"), "hil")
    production = _validate_flat_identity(index_value.get("production"), "production")
    _validate_flat_identity(production_identity, "production")
    require(production == production_identity, "release artifact production build mismatch")
    require(index_value.get("sourceCommit") == hil["sourceCommit"] == production["sourceCommit"], "release ledger source mismatch")
    require(hil["binarySha256"] != production["binarySha256"], "release ledger binaries must differ")
    receipt_files = index_value.get("receiptFiles")
    require(isinstance(receipt_files, list) and len(receipt_files) <= len(RELEASE_ORDER), "invalid release receipt index")
    expected_files = {"index.json", *receipt_files}
    require({item.name for item in path.iterdir()} == expected_files, "release ledger file set mismatch")
    receipts = []
    previous = None
    previous_hash = ZERO_SHA256
    for index, filename in enumerate(receipt_files):
        expected_filename = f"{index + 1:02d}-{RELEASE_ORDER[index]}.json"
        require(filename == expected_filename, "release receipt filename mismatch")
        receipt_path = path / filename
        require(not receipt_path.is_symlink() and receipt_path.is_file(), "release receipt missing")
        receipt_bytes = receipt_path.read_bytes()
        receipt_hash = hashlib.sha256(receipt_bytes).hexdigest()
        try:
            receipt = json.loads(receipt_bytes.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise BuildIdentityError("invalid release receipt JSON") from exc
        require(
            isinstance(receipt, dict)
            and set(receipt) == {"event", "completedAt", "evidencePath", "evidenceSha256", "previousReceiptSha256"},
            "invalid release receipt fields",
        )
        require(receipt.get("event") == RELEASE_ORDER[index], "release receipt order invalid")
        completed = _strict_utc(receipt.get("completedAt"))
        require(previous is None or previous < completed, "release receipt UTC is not increasing")
        previous = completed
        require(receipt.get("previousReceiptSha256") == previous_hash, "release receipt hash chain invalid")
        evidence_path, digest, _evidence_bytes, _evidence_identity = _release_evidence(receipt.get("evidencePath"))
        require(str(evidence_path) == receipt.get("evidencePath"), "release evidence path is not canonical")
        require(digest == receipt.get("evidenceSha256"), "release evidence hash mismatch")
        receipts.append(receipt)
        previous_hash = receipt_hash
    require(index_value.get("headSha256") == previous_hash, "release ledger head hash mismatch")
    if required_event is not None:
        require(required_event in RELEASE_ORDER, "unknown required release event")
        required_count = RELEASE_ORDER.index(required_event) + 1
        require(len(receipts) == required_count, f"release ledger must end at {required_event}")
    return {**index_value, "receipts": receipts}


def append_release_receipt(
    ledger_path,
    hil_manifest,
    production_manifest,
    *,
    event,
    evidence_path,
    completed_at,
    evidence_checksum_path=None,
):
    pair = validate_build_pair(hil_manifest, production_manifest)
    ledger_path = Path(ledger_path)
    new_ledger = not ledger_path.exists()
    if ledger_path.exists():
        ledger = load_release_ledger(
            ledger_path, production_identity=pair["production"]
        )
        require(ledger["hil"] == pair["hil"], "release ledger HIL build mismatch")
    else:
        require(event == RELEASE_ORDER[0], "release event prerequisite missing")
        ledger = {**pair, "receiptFiles": [], "headSha256": ZERO_SHA256, "receipts": []}
    receipts = ledger["receipts"]
    require(len(receipts) < len(RELEASE_ORDER), "release ledger is complete")
    require(event == RELEASE_ORDER[len(receipts)], "release event prerequisite missing")
    completed = _strict_utc(completed_at)
    if receipts:
        require(_strict_utc(receipts[-1]["completedAt"]) < completed, "release receipt UTC is not increasing")
    evidence, digest, evidence_bytes, evidence_identity = _release_evidence(
        evidence_path, evidence_checksum_path
    )
    evidence_value = _load_evidence_json_bytes(evidence_bytes)
    _validate_event_evidence(
        event, evidence_value, pair, ledger, evidence,
        evidence_bytes=evidence_bytes, evidence_identity=evidence_identity,
    )
    if new_ledger:
        ledger_path.mkdir(parents=True)
        _fsync_directory(ledger_path.parent)
    receipt = {
        "event": event,
        "completedAt": completed_at,
        "evidencePath": str(evidence),
        "evidenceSha256": digest,
        "previousReceiptSha256": ledger["headSha256"],
    }
    filename = f"{len(receipts) + 1:02d}-{event}.json"
    receipt_hash = _exclusive_json(ledger_path / filename, receipt)
    index_value = {
        "sourceCommit": pair["sourceCommit"],
        "hil": pair["hil"],
        "production": pair["production"],
        "receiptFiles": [*ledger["receiptFiles"], filename],
        "headSha256": receipt_hash,
    }
    atomic_write_json(ledger_path / "index.json", index_value)
    _fsync_directory(ledger_path)
    return load_release_ledger(
        ledger_path, production_identity=pair["production"], required_event=event
    )


def atomic_write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(value, output, indent=2, sort_keys=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    except BaseException:
        with suppress(FileNotFoundError):
            os.unlink(temporary)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate")
    validate.add_argument("--manifest", required=True, type=Path)
    validate.add_argument("--profile", required=True, choices=PROFILES)
    validate.add_argument("--output", type=Path)
    pair = subparsers.add_parser("pair")
    pair.add_argument("--hil-manifest", required=True, type=Path)
    pair.add_argument("--production-manifest", required=True, type=Path)
    pair.add_argument("--output", type=Path)
    flash = subparsers.add_parser("flash-attest")
    flash.add_argument("--manifest", required=True, type=Path)
    flash.add_argument("--esptool-log", required=True, type=Path)
    flash.add_argument("--exit-code-file", required=True, type=Path)
    flash.add_argument("--device-mac", required=True)
    flash.add_argument("--started-at", required=True)
    flash.add_argument("--completed-at", required=True)
    flash.add_argument("--output", required=True, type=Path)
    capture = subparsers.add_parser("boot-capture")
    capture.add_argument("--serial-port", required=True)
    capture.add_argument("--serial-log", required=True, type=Path)
    capture.add_argument("--receipt", required=True, type=Path)
    capture.add_argument("--timeout-seconds", type=float, default=60)
    boot = subparsers.add_parser("boot-attest")
    boot.add_argument("--manifest", required=True, type=Path)
    boot.add_argument(
        "--event", required=True, choices=("hil-flash",)
    )
    boot.add_argument("--flash-receipt", required=True, type=Path)
    boot.add_argument("--boot-capture-receipt", required=True, type=Path)
    boot.add_argument("--connection-attestation", required=True, type=Path)
    boot.add_argument("--observed-at", required=True)
    boot.add_argument("--output", required=True, type=Path)
    release = subparsers.add_parser("release")
    release.add_argument("--ledger", required=True, type=Path)
    release.add_argument("--hil-manifest", required=True, type=Path)
    release.add_argument("--production-manifest", required=True, type=Path)
    release.add_argument("--event", required=True, choices=RELEASE_ORDER)
    release.add_argument("--evidence", required=True, type=Path)
    release.add_argument("--evidence-sha256", type=Path)
    release.add_argument("--completed-at", required=True)
    arguments = parser.parse_args()
    try:
        if arguments.command == "validate":
            result = load_build_identity(arguments.manifest, expected_profile=arguments.profile)
        elif arguments.command == "pair":
            result = validate_build_pair(arguments.hil_manifest, arguments.production_manifest)
        elif arguments.command == "flash-attest":
            result = build_flash_attestation(
                arguments.manifest, arguments.esptool_log, arguments.exit_code_file,
                arguments.device_mac, arguments.started_at, arguments.completed_at,
            )
        elif arguments.command == "boot-capture":
            result = capture_hil_boot_identity(
                arguments.serial_port, arguments.serial_log, arguments.receipt,
                timeout_seconds=arguments.timeout_seconds,
            )
        elif arguments.command == "boot-attest":
            result = build_boot_attestation(
                arguments.manifest, arguments.flash_receipt,
                arguments.boot_capture_receipt,
                arguments.connection_attestation, arguments.observed_at, arguments.event,
            )
        else:
            result = append_release_receipt(
                arguments.ledger,
                arguments.hil_manifest,
                arguments.production_manifest,
                event=arguments.event,
                evidence_path=arguments.evidence,
                completed_at=arguments.completed_at,
                evidence_checksum_path=arguments.evidence_sha256,
            )
        if getattr(arguments, "output", None):
            atomic_write_json(arguments.output, result)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (BuildIdentityError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(f"lesson storage build identity: FAIL: {exc}", file=os.sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
