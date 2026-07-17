#!/usr/bin/env python3
"""Attended, MAC-gated Task 14 lesson-storage HIL orchestrator."""

import argparse
import concurrent.futures
import ctypes
import errno
import hashlib
import http.client
import ipaddress
import json
import os
import re
import selectors
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
from lesson_studio_task14_build_identity import (  # noqa: E402
    BuildIdentityError,
    load_build_identity,
)

HIL_STORAGE_SCENARIOS = (
    "evict-before-first-unlink-fail",
    "evict-after-unlinks-fail",
    "evict-before-rmdir-fail",
    "evict-after-unlinks-sd-removal",
    "sync-before-download-write-no-space",
    "sync-after-download-bytes-no-space",
    "sync-before-checksum-corrupt-staging",
    "sync-before-commit-rename-fail",
    "sync-before-commit-rename-power-loss",
)
PARTIAL_EVICTION_SCENARIOS = frozenset({
    "evict-after-unlinks-fail",
    "evict-before-rmdir-fail",
})
MATRIX_REPORT_NAME = "hil-matrix-report.json"
POWER_LOSS_SCENARIO = HIL_STORAGE_SCENARIOS[-1]
HIL_TOOL_NAMES = {
    "arm": "self.lesson_assets.hil.arm_fault",
    "status": "self.lesson_assets.hil.status",
    "stage": "self.lesson_assets.hil.stage_fixture",
    "cleanup": "self.lesson_assets.hil.cleanup_fixture",
    "inspect": "self.lesson_assets.hil.inspect",
}
TRIGGER_TOOLS = {
    "evict": "self.lesson_assets.evict_cache_key",
    "sync": "self.lesson_assets.sync_to_sd",
}
SCENARIO_SPECS = {
    "evict-before-first-unlink-fail": ("evict", "before_first_unlink", "fail", 0, 0, False),
    "evict-after-unlinks-fail": ("evict", "after_unlinks", "fail", 1, 0, False),
    "evict-before-rmdir-fail": ("evict", "before_rmdir", "fail", 0, 0, False),
    "evict-after-unlinks-sd-removal": ("evict", "after_unlinks", "pause", 1, 15, False),
    "sync-before-download-write-no-space": ("sync", "before_download_write", "no_space", 0, 0, False),
    "sync-after-download-bytes-no-space": ("sync", "after_download_bytes", "no_space", 1, 0, False),
    "sync-before-checksum-corrupt-staging": ("sync", "before_checksum_verify", "corrupt_staging", 0, 0, False),
    "sync-before-commit-rename-fail": ("sync", "before_commit_rename", "fail", 0, 0, False),
    "sync-before-commit-rename-power-loss": ("sync", "before_commit_rename", "pause", 0, 30, True),
}
ORDINARY_ARTIFACTS = (
    "command.txt", "serial.log", "server.log", "timeline.log",
    "build-manifest.json", "build-manifest.sha256", "status-before.json",
    "inspect-before.json", "stage-response.json", "arm-response.json",
    "trigger-response.json", "status-after.json", "inspect-after.json",
    "cleanup-response.json", "recovery-response.json", "result.json", "evidence.json",
    "validator-exit-code.txt", "SHA256SUMS",
)
_TRIGGER_INDEX = ORDINARY_ARTIFACTS.index("trigger-response.json")
POWER_LOSS_ARTIFACTS = (
    ORDINARY_ARTIFACTS[:_TRIGGER_INDEX]
    + (
        "checkpoint-reached-utc.txt", "power-removed-utc.txt", "reboot-serial.log",
        "post-reboot-inspect.json",
    )
    + ORDINARY_ARTIFACTS[_TRIGGER_INDEX + 1 :]
)
ARM_FIELDS = frozenset(
    {
        "cacheKey", "status", "operation", "checkpoint", "action", "threshold",
        "declaredAssetBytes", "pauseSeconds", "armSequence",
    }
)
STATUS_FIELDS = frozenset(
    {
        "status", "cacheKey", "armed", "reached", "consumed", "operation",
        "checkpoint", "action", "threshold", "declaredAssetBytes", "pauseSeconds",
        "armSequence", "reachedSequence", "consumedSequence",
    }
)
FIXTURE_FIELDS = frozenset(
    {"cacheKey", "siblingCacheKey", "fixture", "status", "changed"}
)
INSPECT_FIELDS = frozenset(
    {"cacheKey", "siblingCacheKey", "status", "truncated", "entries"}
)
INSPECT_ENTRY_FIELDS = frozenset({"label", "nodeType", "bytes", "sha256"})
EVICT_RESPONSE_FIELDS = frozenset(
    {"cacheKey", "status", "reason", "evicted", "notFound", "fileCount"}
)
PRIMARY_SENTINEL_SHA256 = "e95ab394bdf8569652429018519989d3e94cae168cf91c269c81a2c9bb00d5ec"
SIBLING_SENTINEL_SHA256 = "462cc80e16c12bbee14c7eba5e61da286e79580d6dc5b996bfcf7a43f30a4cf8"
SHA256_RE = re.compile(r"[0-9a-f]{64}")
JWT_RE = re.compile(
    r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{3,}\.[A-Za-z0-9_-]{3,}\.[A-Za-z0-9_-]{3,}(?![A-Za-z0-9_-])"
)
ABSOLUTE_PATH_RE = re.compile(r"/(?:Users|home|private|opt|tmp)/[^\s,;]+")
MAX_SERIAL_CAPTURE_BYTES = 2 * 1024 * 1024
MAX_SERVER_CAPTURE_BYTES = 2 * 1024 * 1024
MAX_CAPTURE_LINES = 50_000
MAX_FAILURE_LOG_BYTES = 256 * 1024
MAX_FAILURE_JSON_BYTES = 256 * 1024
MAX_FAILURE_COMMAND_BYTES = 16 * 1024
FAILURE_ARTIFACTS = (
    "command.txt", "serial.log", "server.log", "timeline.log",
    "build-manifest.json", "failure.json", "last-responses.json", "SHA256SUMS",
)
FAILURE_RESPONSE_KEYS = (
    "statusBefore", "inspectBefore", "stage", "arm", "trigger",
    "statusAfter", "inspectAfter", "recovery", "cleanup",
)
FAILURE_PHASE_CODES = {
    "setup": "HIL_SETUP_FAILED",
    "attestation": "LIVE_CONNECTION_ATTESTATION_FAILED",
    "serial": "SERIAL_MONITOR_FAILED",
    "status": "CONTROLLER_STATUS_INVALID",
    "inspect": "STORAGE_INSPECTION_INVALID",
    "stage": "FIXTURE_STAGE_FAILED",
    "arm": "FAULT_ARM_FAILED",
    "trigger": "SCENARIO_TRIGGER_FAILED",
    "recovery": "PARTIAL_EVICTION_RECOVERY_FAILED",
    "cleanup": "FIXTURE_CLEANUP_REFUSED",
    "validator": "INDEPENDENT_VALIDATION_FAILED",
    "publication": "ATOMIC_PUBLICATION_FAILED",
    "internal": "INTERNAL_ORCHESTRATOR_FAILURE",
}
SCENARIO_EXPECTED_PROGRESS = {
    "evict-before-first-unlink-fail": 0,
    "evict-after-unlinks-fail": 1,
    "evict-before-rmdir-fail": 1,
    "evict-after-unlinks-sd-removal": 1,
    "sync-before-download-write-no-space": 0,
    "sync-after-download-bytes-no-space": 1,
    "sync-before-checksum-corrupt-staging": 0,
    "sync-before-commit-rename-fail": 0,
    "sync-before-commit-rename-power-loss": 0,
}


class HilValidationError(RuntimeError):
    pass


class HilTimeoutError(HilValidationError):
    pass


class HilTransportError(HilValidationError):
    pass


class HilDisconnectError(HilTransportError):
    pass


class HilCaptureLimitError(HilValidationError):
    pass


def require(condition, message):
    if not condition:
        raise HilValidationError(message)


def _exact_fields(value, fields, name):
    require(isinstance(value, dict) and set(value) == fields, f"invalid {name} response fields")
    return value


def _exact_int(value, name, *, minimum=0):
    require(type(value) is int and value >= minimum, f"invalid {name}")
    return value


def _exact_bool(value, name):
    require(type(value) is bool, f"invalid {name}")
    return value


def recovery_not_attempted():
    return {
        "attempted": False,
        "operation": None,
        "reason": None,
        "response": None,
        "inspection": None,
    }


def validate_partial_eviction_retry(value, cache_key):
    value = _exact_fields(value, EVICT_RESPONSE_FIELDS, "recovery eviction")
    _exact_bool(value["evicted"], "recovery evicted")
    _exact_bool(value["notFound"], "recovery notFound")
    _exact_int(value["fileCount"], "recovery fileCount")
    require(
        value == {
            "cacheKey": cache_key,
            "status": "evicted",
            "reason": "evicted",
            "evicted": True,
            "notFound": False,
            "fileCount": 0,
        },
        "partial eviction recovery did not converge",
    )
    return value


def validate_arm_response(
    value,
    cache_key,
    operation,
    checkpoint,
    action,
    *,
    threshold=None,
    declared_asset_bytes=None,
    pause_seconds=None,
):
    value = _exact_fields(value, ARM_FIELDS, "arm")
    require(value["cacheKey"] == cache_key, "arm response cache key mismatch")
    require(value["status"] == "armed", "fault was not armed")
    require(value["operation"] == operation, "arm operation mismatch")
    require(value["checkpoint"] == checkpoint, "arm checkpoint mismatch")
    require(value["action"] == action, "arm action mismatch")
    expected_numbers = {
        "threshold": threshold,
        "declaredAssetBytes": declared_asset_bytes,
        "pauseSeconds": pause_seconds,
    }
    for name, expected in expected_numbers.items():
        actual = _exact_int(value[name], name)
        if expected is not None:
            _exact_int(expected, f"expected {name}")
            require(actual == expected, f"arm {name} mismatch")
    _exact_int(value["armSequence"], "armSequence", minimum=1)
    return value


def validate_status_response(value, *, expected_cache_key=None):
    value = _exact_fields(value, STATUS_FIELDS, "status")
    require(value["status"] in {"idle", "armed", "reached", "consumed"}, "invalid HIL status")
    for name in ("armed", "reached", "consumed"):
        _exact_bool(value[name], name)
    for name in (
        "threshold", "declaredAssetBytes", "pauseSeconds", "armSequence",
        "reachedSequence", "consumedSequence",
    ):
        _exact_int(value[name], name)
    if value["status"] == "idle":
        require(value["cacheKey"] == "", "idle status retains a cache key")
        require(not value["armed"] and not value["reached"] and not value["consumed"], "idle flags invalid")
        require(value["operation"] == "evict", "idle operation default mismatch")
        require(value["checkpoint"] == "before_first_unlink", "idle checkpoint default mismatch")
        require(value["action"] == "fail", "idle action default mismatch")
        for name in (
            "threshold", "declaredAssetBytes", "pauseSeconds", "armSequence",
            "reachedSequence", "consumedSequence",
        ):
            require(value[name] == 0, f"idle status retains {name}")
    elif expected_cache_key is not None:
        require(value["cacheKey"] == expected_cache_key, "status cache key mismatch")
    states = {
        "armed": (True, False, False),
        "reached": (False, True, False),
        "consumed": (False, True, True),
    }
    if value["status"] in states:
        require(
            (value["armed"], value["reached"], value["consumed"]) == states[value["status"]],
            "status flags do not match state",
        )
    return value


def validate_fixture_response(value, cache_key, sibling_cache_key, fixture, expected_status):
    value = _exact_fields(value, FIXTURE_FIELDS, "fixture")
    require(value["cacheKey"] == cache_key, "fixture cache key mismatch")
    require(value["siblingCacheKey"] == sibling_cache_key, "fixture sibling mismatch")
    require(value["fixture"] == fixture, "fixture type mismatch")
    require(value["status"] == expected_status, "fixture operation refused")
    _exact_bool(value["changed"], "fixture changed")
    require(value["changed"] is True, "fixture operation made no change")
    return value


def validate_inspect_response(value, cache_key, sibling_cache_key):
    value = _exact_fields(value, INSPECT_FIELDS, "inspect")
    require(value["cacheKey"] == cache_key, "inspect cache key mismatch")
    require(value["siblingCacheKey"] == sibling_cache_key, "inspect sibling mismatch")
    require(value["status"] == "inspected", "inspection failed")
    _exact_bool(value["truncated"], "inspect truncated")
    require(value["truncated"] is False, "inspection was truncated")
    entries = value["entries"]
    require(isinstance(entries, list), "inspect entries must be a list")
    for entry in entries:
        _exact_fields(entry, INSPECT_ENTRY_FIELDS, "inspect entry")
        require(isinstance(entry["label"], str) and entry["label"], "invalid inspect label")
        require(isinstance(entry["nodeType"], str) and entry["nodeType"], "invalid inspect node type")
        _exact_int(entry["bytes"], "inspect bytes")
        require(
            isinstance(entry["sha256"], str)
            and (entry["sha256"] == "" or SHA256_RE.fullmatch(entry["sha256"])),
            "invalid inspect sha256",
        )
        rendered = json.dumps(entry, sort_keys=True)
        require(not ABSOLUTE_PATH_RE.search(rendered), "inspection contains an absolute path")
    return value


def _entry(label, node_type, bytes_count=0, sha256=""):
    return {"label": label, "nodeType": node_type, "bytes": bytes_count, "sha256": sha256}


def _preservation_entries(cache_key, sibling_cache_key, primary_state, sibling_state):
    expected = []
    for key, state, digest in (
        (cache_key, primary_state, PRIMARY_SENTINEL_SHA256),
        (sibling_cache_key, sibling_state, SIBLING_SENTINEL_SHA256),
    ):
        label = f"lesson-assets/{key}"
        if state == "missing":
            expected.append(_entry(label, "missing"))
        else:
            expected.append(_entry(label, "directory"))
            if state == "full":
                expected.append(
                    _entry(f"{label}/.tbot-hil-sentinel", "regular_file", 33, digest)
                )
    return sorted(expected, key=lambda item: item["label"])


def validate_preservation_inspections(scenario, before, after, *, post_reboot=None):
    require(scenario in HIL_STORAGE_SCENARIOS, "unknown preservation scenario")
    cache_key = before.get("cacheKey") if isinstance(before, dict) else None
    sibling = before.get("siblingCacheKey") if isinstance(before, dict) else None
    validate_inspect_response(before, cache_key, sibling)
    validate_inspect_response(after, cache_key, sibling)
    prefixes = (f"lesson-assets/{cache_key}", f"lesson-assets/{sibling}")

    def split(value):
        target = sorted(
            (item for item in value["entries"] if item["label"].startswith(prefixes)),
            key=lambda item: item["label"],
        )
        protected = sorted(
            (item for item in value["entries"] if not item["label"].startswith(prefixes)),
            key=lambda item: item["label"],
        )
        return target, protected

    before_target, before_protected = split(before)
    after_target, after_protected = split(after)
    require(
        before_target == _preservation_entries(cache_key, sibling, "missing", "missing"),
        "inspection baseline is not clean",
    )
    protected_labels = {item["label"] for item in before_protected}
    require(
        {"lesson-assets/current.json", "lesson-assets/pvg", "lesson-assets/shared"}
        <= protected_labels,
        "protected inspection baseline missing",
    )
    require(before_protected == after_protected, "protected storage fingerprint changed")
    primary_state = {
        "evict-before-first-unlink-fail": "full",
        "evict-after-unlinks-fail": "directory_only",
        "evict-before-rmdir-fail": "directory_only",
        "evict-after-unlinks-sd-removal": "missing",
        "sync-before-download-write-no-space": "full",
        "sync-after-download-bytes-no-space": "full",
        "sync-before-checksum-corrupt-staging": "full",
        "sync-before-commit-rename-fail": "full",
        "sync-before-commit-rename-power-loss": "full",
    }[scenario]
    require(
        after_target == _preservation_entries(cache_key, sibling, primary_state, "full"),
        "scenario inspection state mismatch",
    )
    if scenario == POWER_LOSS_SCENARIO:
        require(post_reboot is not None, "power-loss post-reboot inspection missing")
        validate_inspect_response(post_reboot, cache_key, sibling)
        require(post_reboot == after, "post-reboot inspection mismatch")
    else:
        require(post_reboot is None, "ordinary scenario has post-reboot inspection")
    return {"cacheKey": cache_key, "siblingCacheKey": sibling}


def validate_recovered_preservation_inspection(before, after, recovered):
    cache_key = before.get("cacheKey") if isinstance(before, dict) else None
    sibling = before.get("siblingCacheKey") if isinstance(before, dict) else None
    for value in (before, after, recovered):
        validate_inspect_response(value, cache_key, sibling)
    prefixes = (f"lesson-assets/{cache_key}", f"lesson-assets/{sibling}")

    def split(value):
        target = sorted(
            (item for item in value["entries"] if item["label"].startswith(prefixes)),
            key=lambda item: item["label"],
        )
        protected = sorted(
            (item for item in value["entries"] if not item["label"].startswith(prefixes)),
            key=lambda item: item["label"],
        )
        return target, protected

    _, before_protected = split(before)
    after_target, _ = split(after)
    recovered_target, recovered_protected = split(recovered)
    require(
        after_target == _preservation_entries(cache_key, sibling, "directory_only", "full"),
        "recovery was not preceded by the expected partial eviction state",
    )
    require(
        recovered_target == _preservation_entries(cache_key, sibling, "missing", "full"),
        "recovered preservation state mismatch",
    )
    require(
        recovered_protected == before_protected,
        "recovery changed protected storage fingerprints",
    )
    return recovered


def validate_cleanup_inspection(before, cleaned):
    cache_key = before.get("cacheKey")
    sibling = before.get("siblingCacheKey")
    validate_inspect_response(cleaned, cache_key, sibling)
    prefixes = (f"lesson-assets/{cache_key}", f"lesson-assets/{sibling}")
    cleaned_target = sorted(
        (item for item in cleaned["entries"] if item["label"].startswith(prefixes)),
        key=lambda item: item["label"],
    )
    require(
        cleaned_target == _preservation_entries(cache_key, sibling, "missing", "missing"),
        "fixture cleanup inspection is not clean",
    )
    return cleaned


def parse_internal_mcp_response(value):
    value = _exact_fields(value, frozenset({"data"}), "internal MCP envelope")
    data = _exact_fields(value["data"], frozenset({"called", "result"}), "internal MCP data")
    require(data["called"] is True, "MCP call was not completed")
    result = data["result"]
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except json.JSONDecodeError as exc:
            raise HilValidationError("MCP result is not JSON") from exc
    require(isinstance(result, dict), "MCP result must be an object")
    return result


def redact_text(value, secrets=()):
    rendered = str(value)
    for secret in secrets:
        if secret:
            rendered = rendered.replace(str(secret), "<redacted-secret>")
    rendered = JWT_RE.sub("<redacted-jwt>", rendered)
    rendered = ABSOLUTE_PATH_RE.sub("<redacted-path>", rendered)
    rendered = re.sub(
        r"(?im)\b(?:proxy-)?authorization\s*[:=]\s*[^\r\n]*",
        "Authorization=<redacted>",
        rendered,
    )
    rendered = re.sub(
        r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]+",
        "<redacted-credential>",
        rendered,
    )
    rendered = re.sub(r"(?i)(authorization|x-mint-secret|password|token)\s*[:=]\s*\S+", r"\1=<redacted>", rendered)
    return rendered


def _checkpoint_marker_pattern(operation, checkpoint, cache_key, expected_count):
    return re.compile(
        rf"HIL_STORAGE_CHECKPOINT_REACHED operation={re.escape(operation)} "
        rf"checkpoint={re.escape(checkpoint)} cache_key={re.escape(cache_key)} "
        rf"count={expected_count} reached_sequence=([1-9][0-9]*)(?:\s|$)"
    )


def poll_checkpoint(
    read_chunk,
    *,
    operation,
    checkpoint,
    cache_key,
    expected_count,
    timeout_seconds,
    monotonic=time.monotonic,
    sleep=time.sleep,
):
    require(type(timeout_seconds) in (int, float) and not isinstance(timeout_seconds, bool), "invalid marker timeout")
    require(0 < timeout_seconds <= 90, "invalid marker timeout")
    _exact_int(expected_count, "expected checkpoint count")
    marker = _checkpoint_marker_pattern(operation, checkpoint, cache_key, expected_count)
    deadline = monotonic() + timeout_seconds
    captured = []
    while monotonic() < deadline:
        chunk = read_chunk()
        if chunk:
            captured.append(str(chunk))
            combined = "".join(captured)
            if any(marker.search(line) for line in combined.splitlines()):
                return combined
        sleep(0.02)
    raise HilTimeoutError("checkpoint marker timeout")


def scenario_artifact_names(*, power_loss):
    return POWER_LOSS_ARTIFACTS if power_loss else ORDINARY_ARTIFACTS


def atomic_write_bytes(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    except BaseException:
        with suppress(FileNotFoundError):
            os.unlink(temporary)
        raise


def json_bytes(value):
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def failure_error_code(phase):
    require(phase in FAILURE_PHASE_CODES, "unknown failure phase")
    return FAILURE_PHASE_CODES[phase]


def _reject_existing_symlink_components(path):
    path = Path(path).absolute()
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        if os.path.lexists(current):
            require(not current.is_symlink(), "evidence root contains a symlink")


def validate_evidence_roots(pass_root, failure_root):
    pass_path = Path(pass_root).absolute()
    failure_path = Path(failure_root).absolute()
    _reject_existing_symlink_components(pass_path)
    _reject_existing_symlink_components(failure_path)
    pass_resolved = pass_path.resolve(strict=False)
    failure_resolved = failure_path.resolve(strict=False)
    try:
        common = Path(os.path.commonpath((pass_resolved, failure_resolved)))
    except ValueError:
        common = None
    require(
        common not in {pass_resolved, failure_resolved},
        "PASS and failure evidence roots overlap",
    )
    if pass_resolved.exists() and failure_resolved.exists():
        pass_stat = pass_resolved.stat()
        failure_stat = failure_resolved.stat()
        require(
            (pass_stat.st_dev, pass_stat.st_ino) != (failure_stat.st_dev, failure_stat.st_ino),
            "PASS and failure evidence roots alias the same location",
        )
    return pass_resolved, failure_resolved


def validate_run_roots(arguments):
    pass_root, failure_root = validate_evidence_roots(
        arguments.evidence_dir, arguments.failure_evidence_dir
    )
    arguments.evidence_dir = pass_root
    arguments.failure_evidence_dir = failure_root
    return pass_root, failure_root


def _bounded_redacted_text(value, secrets, max_bytes):
    rendered = redact_text(value or "", secrets)
    lines = rendered.splitlines(keepends=True)[:MAX_CAPTURE_LINES]
    encoded = "".join(lines).encode("utf-8", errors="replace")
    if len(encoded) > max_bytes:
        encoded = encoded[:max_bytes]
        while True:
            try:
                rendered = encoded.decode("utf-8")
                break
            except UnicodeDecodeError as exc:
                encoded = encoded[:exc.start]
    else:
        rendered = encoded.decode("utf-8")
    return (rendered or "<no captured output>\n").encode("utf-8")


def _sanitize_json(value, secrets):
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return redact_text(value, secrets)
    if isinstance(value, list):
        return [_sanitize_json(item, secrets) for item in value]
    if isinstance(value, dict):
        return {
            redact_text(key, secrets): _sanitize_json(item, secrets)
            for key, item in value.items()
            if isinstance(key, str)
        }
    return redact_text(type(value).__name__, secrets)


def _bounded_json_bytes(value, secrets, *, sort_keys=True):
    data = (
        json.dumps(_sanitize_json(value, secrets), indent=2, sort_keys=sort_keys) + "\n"
    ).encode("utf-8")
    require(len(data) <= MAX_FAILURE_JSON_BYTES, "failure JSON exceeds bounded size")
    return data


def _failure_directory_name(scenario, utc_failure):
    stamp = re.sub(r"[^0-9]", "", utc_failure)[:20]
    return f"{stamp}-{scenario}"


def write_failure_evidence(
    failure_root,
    *,
    scenario,
    phase,
    utc_start,
    utc_failure,
    completed_events,
    build_identity,
    last_responses,
    command,
    serial_log,
    server_log,
    timeline_log,
    secrets=(),
    now=None,
):
    require(scenario in HIL_STORAGE_SCENARIOS, "unknown HIL storage scenario")
    error_code = failure_error_code(phase)
    start_value = _strict_utc(utc_start)
    failure_value = _strict_utc(utc_failure)
    require(start_value <= failure_value, "invalid failure timestamps")
    require(
        isinstance(completed_events, list)
        and all(isinstance(event, str) for event in completed_events),
        "invalid completed events",
    )
    require(
        isinstance(last_responses, dict)
        and tuple(last_responses) == FAILURE_RESPONSE_KEYS,
        "invalid last response keys",
    )
    require(
        all(value is None or isinstance(value, dict) for value in last_responses.values()),
        "last response must be a bounded object or null",
    )
    root = Path(failure_root).absolute()
    _reject_existing_symlink_components(root)
    root.mkdir(parents=True, exist_ok=True)
    _reject_existing_symlink_components(root)
    effective_now = now or utc_now
    final = root / _failure_directory_name(scenario, effective_now())
    require(not os.path.lexists(final), "failure evidence directory already exists")
    staging = Path(tempfile.mkdtemp(prefix=f".{final.name}.staging-", dir=root))
    published = False
    try:
        failure = {
            "artifactVersion": 1,
            "completedEvents": completed_events,
            "errorCode": error_code,
            "phase": phase,
            "scenario": scenario,
            "status": "FAIL",
            "utcFailure": utc_failure,
            "utcStart": utc_start,
        }
        payloads = {
            "command.txt": _bounded_redacted_text(command, secrets, MAX_FAILURE_COMMAND_BYTES),
            "serial.log": _bounded_redacted_text(serial_log, secrets, MAX_FAILURE_LOG_BYTES),
            "server.log": _bounded_redacted_text(server_log, secrets, MAX_FAILURE_LOG_BYTES),
            "timeline.log": _bounded_redacted_text(timeline_log, secrets, MAX_FAILURE_LOG_BYTES),
            "build-manifest.json": _bounded_json_bytes(build_identity, secrets),
            "failure.json": _bounded_json_bytes(failure, secrets),
            "last-responses.json": _bounded_json_bytes(
                last_responses, secrets, sort_keys=False
            ),
        }
        assert_artifacts_sanitized(payloads, secrets)
        for name, data in payloads.items():
            atomic_write_bytes(staging / name, data)
        checksums = "".join(
            f"{hashlib.sha256(payloads[name]).hexdigest()}  {name}\n"
            for name in FAILURE_ARTIFACTS if name != "SHA256SUMS"
        ).encode("ascii")
        atomic_write_bytes(staging / "SHA256SUMS", checksums)
        _validate_staging_layout(staging, FAILURE_ARTIFACTS)
        _scenario_checksums(staging, FAILURE_ARTIFACTS)
        for name in FAILURE_ARTIFACTS:
            with (staging / name).open("rb") as artifact:
                os.fsync(artifact.fileno())
        _fsync_directory(staging)
        _rename_directory_noreplace(staging, final)
        published = True
        _fsync_directory(root)
        return final
    except BaseException:
        cleanup = final if published else staging
        with suppress(Exception):
            shutil.rmtree(cleanup)
        with suppress(Exception):
            _fsync_directory(root)
        raise


def quarantine_scenario_failure(primary, *, secondary_log=None, **evidence):
    try:
        write_failure_evidence(**evidence, utc_failure=utc_now())
    except BaseException:
        if secondary_log is not None:
            secondary_log("failure evidence publication also failed")
    raise primary


def finalize_scenario_directory(directory, payloads, *, power_loss):
    directory = Path(directory)
    expected = scenario_artifact_names(power_loss=power_loss)
    expected_payloads = set(expected) - {"SHA256SUMS"}
    require(set(payloads) == expected_payloads, "scenario artifact payload set mismatch")
    if directory.exists():
        require(directory.is_dir() and not directory.is_symlink(), "invalid evidence directory")
        require(not any(directory.iterdir()), "evidence directory must be empty")
    else:
        directory.mkdir(parents=True)
    for name in expected:
        if name == "SHA256SUMS":
            continue
        data = payloads[name]
        require(isinstance(data, bytes) and data, f"invalid artifact payload: {name}")
        atomic_write_bytes(directory / name, data)
    checksum_lines = []
    for name in expected:
        if name == "SHA256SUMS":
            continue
        digest = hashlib.sha256((directory / name).read_bytes()).hexdigest()
        checksum_lines.append(f"{digest}  {name}\n")
    atomic_write_bytes(directory / "SHA256SUMS", "".join(checksum_lines).encode("ascii"))
    require(
        {path.name for path in directory.iterdir()} == set(expected),
        "scenario directory layout mismatch",
    )
    return directory


def _fsync_directory(directory):
    descriptor = os.open(Path(directory), os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _rewrite_scenario_checksums(directory, *, power_loss):
    directory = Path(directory)
    lines = []
    for name in scenario_artifact_names(power_loss=power_loss):
        if name != "SHA256SUMS":
            lines.append(
                f"{hashlib.sha256((directory / name).read_bytes()).hexdigest()}  {name}\n"
            )
    atomic_write_bytes(directory / "SHA256SUMS", "".join(lines).encode("ascii"))


def _validate_staging_layout(directory, expected_names):
    directory = Path(directory)
    entries = {entry.name: entry for entry in os.scandir(directory)}
    require(set(entries) == set(expected_names), "staging scenario directory layout mismatch")
    for name in expected_names:
        mode = entries[name].stat(follow_symlinks=False).st_mode
        require(stat.S_ISREG(mode), f"invalid staging scenario artifact: {name}")


def _rename_directory_noreplace(source, destination):
    source_bytes = os.fsencode(source)
    destination_bytes = os.fsencode(destination)
    library = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        rename = getattr(library, "renamex_np", None)
        arguments = (source_bytes, destination_bytes, 0x00000004)
    elif sys.platform.startswith("linux"):
        rename = getattr(library, "renameat2", None)
        arguments = (-100, source_bytes, -100, destination_bytes, 0x00000001)
    else:
        rename = None
        arguments = ()
    require(rename is not None, "atomic no-replace directory publication is unsupported")
    rename.restype = ctypes.c_int
    if rename(*arguments) == 0:
        return
    error = ctypes.get_errno()
    if error in {errno.EEXIST, errno.ENOTEMPTY}:
        raise HilValidationError("final HIL scenario directory already exists")
    raise HilValidationError(
        f"atomic no-replace directory publication failed: {os.strerror(error)}"
    )


def publish_validated_scenario_directory(
    final_directory,
    payloads,
    *,
    scenario,
    power_loss,
    validator_script,
    secrets=(),
    phase_changed=None,
):
    final_directory = Path(final_directory)
    parent = final_directory.parent
    parent.mkdir(parents=True, exist_ok=True)
    require(
        not os.path.lexists(final_directory),
        "final HIL scenario directory already exists",
    )
    staging = Path(tempfile.mkdtemp(prefix=f".{final_directory.name}.staging-", dir=parent))
    expected = scenario_artifact_names(power_loss=power_loss)
    published = False
    try:
        finalize_scenario_directory(staging, payloads, power_loss=power_loss)
        if phase_changed is not None:
            phase_changed("validator")
        validator = subprocess.run(
            [
                sys.executable, str(validator_script), "--hil-storage-scenario", scenario,
                "--evidence-dir", str(staging), "--output", str(staging / "evidence.json"),
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=30,
        )
        try:
            evidence = json.loads((staging / "evidence.json").read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise HilValidationError("HIL scenario validator produced invalid evidence") from exc
        require(
            validator.returncode == 0
            and isinstance(evidence, dict)
            and evidence.get("scenario") == scenario
            and evidence.get("status") == "PASS"
            and evidence.get("validationErrors") == [],
            "HIL scenario validator failed",
        )
        if phase_changed is not None:
            phase_changed("publication")
        atomic_write_bytes(
            staging / "validator-exit-code.txt",
            f"{validator.returncode}\n".encode("ascii"),
        )
        _rewrite_scenario_checksums(staging, power_loss=power_loss)
        final_artifacts = {
            name: (staging / name).read_bytes()
            for name in expected
            if name != "SHA256SUMS"
        }
        assert_artifacts_sanitized(final_artifacts, secrets)
        _scenario_checksums(staging, expected)
        for name in expected:
            with (staging / name).open("rb") as artifact:
                os.fsync(artifact.fileno())
        _fsync_directory(staging)
        _validate_staging_layout(staging, expected)
        _rename_directory_noreplace(staging, final_directory)
        published = True
        _fsync_directory(parent)
        return final_directory
    except BaseException:
        cleanup = final_directory if published else staging
        with suppress(Exception):
            shutil.rmtree(cleanup)
        with suppress(Exception):
            _fsync_directory(parent)
        raise


def _scenario_checksums(directory, expected_names):
    checksum_path = directory / "SHA256SUMS"
    require(
        not checksum_path.is_symlink() and checksum_path.is_file(),
        "HIL scenario checksum manifest missing",
    )
    rows = checksum_path.read_text(encoding="ascii").splitlines()
    expected_artifacts = tuple(name for name in expected_names if name != "SHA256SUMS")
    require(len(rows) == len(expected_artifacts), "HIL scenario checksum file set mismatch")
    declared = {}
    for row in rows:
        match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9][A-Za-z0-9._-]*)", row)
        require(match is not None, "invalid HIL scenario checksum manifest")
        digest, name = match.groups()
        require(name not in declared, "duplicate HIL scenario checksum entry")
        declared[name] = digest
    require(set(declared) == set(expected_artifacts), "HIL scenario checksum file set mismatch")
    for name in expected_artifacts:
        artifact = directory / name
        require(
            not artifact.is_symlink() and artifact.is_file() and artifact.stat().st_size > 0,
            f"invalid HIL scenario artifact: {name}",
        )
        require(
            hashlib.sha256(artifact.read_bytes()).hexdigest() == declared[name],
            f"HIL scenario artifact checksum mismatch: {name}",
        )
    return declared


def _matrix_scenario_record(evidence_root, scenario, preflight_result):
    scenario_dir = evidence_root / scenario
    require(
        not scenario_dir.is_symlink() and scenario_dir.is_dir(),
        "HIL scenario evidence directory missing",
    )
    expected = scenario_artifact_names(power_loss=scenario == POWER_LOSS_SCENARIO)
    require(
        {path.name for path in scenario_dir.iterdir()} == set(expected),
        "HIL scenario evidence file set mismatch",
    )
    artifacts = _scenario_checksums(scenario_dir, expected)
    try:
        result = json.loads((scenario_dir / "result.json").read_text(encoding="utf-8"))
        evidence = json.loads((scenario_dir / "evidence.json").read_text(encoding="utf-8"))
        build_manifest = json.loads(
            (scenario_dir / "build-manifest.json").read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise HilValidationError("invalid HIL matrix scenario JSON") from exc
    require(
        isinstance(result, dict)
        and result.get("scenario") == scenario
        and result.get("status") == "PASS",
        "HIL matrix scenario result is not PASS",
    )
    require(
        isinstance(evidence, dict)
        and evidence.get("scenario") == scenario
        and evidence.get("status") == "PASS"
        and evidence.get("validationErrors") == [],
        "HIL matrix scenario validator evidence is not PASS",
    )
    require(
        (scenario_dir / "validator-exit-code.txt").read_bytes() == b"0\n",
        "HIL matrix scenario validator did not succeed",
    )
    for field in ("buildIdentity", "deviceId", "deviceUuid", "connectionIdentity"):
        require(
            result.get(field) == preflight_result.get(field),
            f"HIL matrix scenario {field} mismatch",
        )
    require(
        build_manifest == preflight_result.get("buildIdentity"),
        "HIL matrix scenario build manifest mismatch",
    )
    return {
        "scenario": scenario,
        "status": "PASS",
        "evidencePath": f"{scenario}/evidence.json",
        "evidenceSha256": artifacts["evidence.json"],
        "sha256SumsPath": f"{scenario}/SHA256SUMS",
        "sha256SumsSha256": hashlib.sha256(
            (scenario_dir / "SHA256SUMS").read_bytes()
        ).hexdigest(),
        "validatorExitCode": 0,
        "artifacts": artifacts,
    }


def publish_matrix_report(arguments, preflight_result):
    evidence_root = Path(arguments.evidence_dir)
    scenarios = [
        _matrix_scenario_record(evidence_root, scenario, preflight_result)
        for scenario in HIL_STORAGE_SCENARIOS
    ]
    report = {
        "status": "PASS",
        "event": "hil-matrix-pass",
        "buildIdentity": preflight_result["buildIdentity"],
        "deviceId": preflight_result["deviceId"],
        "deviceUuid": preflight_result["deviceUuid"],
        "connectionIdentity": preflight_result["connectionIdentity"],
        "scenarios": scenarios,
    }
    report_path = evidence_root / MATRIX_REPORT_NAME

    def verify_inputs():
        observed = [
            _matrix_scenario_record(evidence_root, scenario, preflight_result)
            for scenario in HIL_STORAGE_SCENARIOS
        ]
        require(observed == scenarios, "HIL matrix inputs changed during publication")

    try:
        verify_inputs()
        atomic_write_bytes(report_path, json_bytes(report))
        verify_inputs()
    except BaseException:
        with suppress(FileNotFoundError):
            report_path.unlink()
        raise
    return report


def validate_power_loss_result(value):
    require(isinstance(value, dict), "invalid power-loss result")
    required = {
        "powerLoss": True,
        "checkpointReached": True,
        "triggerResponseAbsent": True,
        "successMarkerBeforeLoss": False,
        "rebootCaptured": True,
        "postRebootInspected": True,
        "retryStatus": "ready",
        "triggerPendingAtMarker": True,
        "triggerPendingAtCutBoundary": True,
        "disconnectAfterPowerCutBoundary": True,
    }
    for name, expected in required.items():
        require(value.get(name) == expected and type(value.get(name)) is type(expected), f"invalid power-loss field: {name}")
    try:
        ordered = [
            _strict_utc(value[name])
            for name in (
                "utcStart",
                "checkpointReachedUtc",
                "powerCutBoundaryUtc",
                "disconnectObservedUtc",
                "powerRemovalConfirmedUtc",
                "utcEnd",
            )
        ]
    except (AttributeError, KeyError, TypeError, ValueError):
        raise HilValidationError("invalid power-loss boundary timestamps") from None
    start, checkpoint, boundary, disconnected, confirmed, end = ordered
    require(start < checkpoint < boundary < disconnected, "invalid power-loss timestamp order")
    require(disconnected <= confirmed < end, "invalid power-loss timestamp order")
    status = validate_status_response(value.get("postRebootStatus"), expected_cache_key=None)
    require(status["status"] == "idle", "volatile HIL arm survived reboot")
    return value


def _strict_utc(value):
    require(
        isinstance(value, str)
        and re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z", value),
        "invalid UTC timestamp",
    )
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        raise HilValidationError("invalid UTC timestamp") from None
    require(parsed.tzinfo == timezone.utc, "invalid UTC timestamp")
    return parsed


def finalize_power_loss_result(result, power_data):
    result.update(power_data)
    result["armClearedAfterReboot"] = True
    validate_power_loss_result(result)
    return result


def validate_event_order(events):
    ordinary = [
        "status-before", "inspect-before", "stage", "arm", "trigger",
        "status-after", "inspect-after", "cleanup",
    ]
    recovered = ordinary[:-1] + ["recovery-trigger", "recovery-inspect", "cleanup"]
    require(events in (ordinary, recovered), "HIL cleanup/status/inspect order invalid")
    return events


def validate_sequences(arm, status):
    require(status.get("status") == "consumed", "HIL arm was not consumed")
    for name in (
        "cacheKey", "operation", "checkpoint", "action", "threshold",
        "declaredAssetBytes", "pauseSeconds", "armSequence",
    ):
        require(
            status.get(name) == arm.get(name)
            and type(status.get(name)) is type(arm.get(name)),
            f"HIL status does not bind armed field: {name}",
        )
    arm_sequence = _exact_int(arm.get("armSequence"), "arm sequence", minimum=1)
    reached = _exact_int(status.get("reachedSequence"), "reached sequence", minimum=1)
    consumed = _exact_int(status.get("consumedSequence"), "consumed sequence", minimum=1)
    require(arm_sequence < reached < consumed, "HIL evidence sequences are not strictly increasing")
    return {"arm": arm_sequence, "reached": reached, "consumed": consumed}


def _extract_serial_sequence(text, marker, *, operation, checkpoint, cache_key):
    pattern = re.compile(
        rf"{re.escape(marker)} operation={re.escape(operation)} "
        rf"checkpoint={re.escape(checkpoint)} .*cache_key={re.escape(cache_key)} .*"
        rf"(?:reached|consumed)_sequence=([1-9][0-9]*)$",
        re.MULTILINE,
    )
    matches = pattern.findall(text)
    require(len(matches) == 1, f"missing or duplicate serial sequence: {marker}")
    return int(matches[0])


def sequences_from_serial(arm, text, *, operation, checkpoint, cache_key):
    values = {
        "arm": _exact_int(arm.get("armSequence"), "arm sequence", minimum=1),
        "reached": _extract_serial_sequence(
            text, "HIL_STORAGE_CHECKPOINT_REACHED",
            operation=operation, checkpoint=checkpoint, cache_key=cache_key,
        ),
        "consumed": _extract_serial_sequence(
            text, "HIL_STORAGE_FAULT_CONSUMED",
            operation=operation, checkpoint=checkpoint, cache_key=cache_key,
        ),
    }
    require(values["arm"] < values["reached"] < values["consumed"], "serial HIL sequences are not increasing")
    return values


def validate_trigger_response(operation, value, cache_key, *, require_ready=None):
    require(isinstance(value, dict), "trigger response must be an object")
    require(value.get("cacheKey") == cache_key, "trigger response cache key mismatch")
    if operation == "evict":
        _exact_fields(value, EVICT_RESPONSE_FIELDS, "eviction trigger")
        _exact_bool(value["evicted"], "evicted")
        _exact_bool(value["notFound"], "notFound")
        _exact_int(value["fileCount"], "fileCount")
        require(isinstance(value["status"], str) and value["status"], "invalid eviction status")
        require(value["reason"] == value["status"], "eviction reason/status mismatch")
        return value
    base = {
        "cacheKey", "ready", "downloadedCount", "skippedCount", "failedCount",
        "totalBytes", "files",
    }
    fields = set(value)
    require(fields in (base, base | {"manifestChecksum"}), "invalid sync trigger response fields")
    _exact_bool(value["ready"], "sync ready")
    for name in ("downloadedCount", "skippedCount", "failedCount", "totalBytes"):
        _exact_int(value[name], name)
    require(isinstance(value["files"], list) and value["files"], "invalid sync files")
    if value["ready"]:
        require(value.get("manifestChecksum") == cache_key.rsplit("-", 1)[-1], "sync attestation mismatch")
        require(value["failedCount"] == 0, "ready sync contains failures")
    else:
        require("manifestChecksum" not in value, "failed sync must not attest manifest")
    if require_ready is not None:
        require(value["ready"] is require_ready, "unexpected sync readiness")
    for item in value["files"]:
        require(isinstance(item, dict), "invalid sync file result")
        state = item.get("state")
        expected = {
            "DOWNLOADED": {"key", "path", "localPath", "state", "bytes"},
            "SKIPPED": {"key", "path", "localPath", "state"},
            "FAILED": {"key", "path", "localPath", "state", "error"},
        }.get(state)
        require(expected is not None and set(item) == expected, "invalid sync file result fields")
        if "bytes" in item:
            _exact_int(item["bytes"], "sync file bytes", minimum=1)
    return value


def validate_scenario_outcome(scenario, trigger_response, *, cache_key):
    require(scenario in HIL_STORAGE_SCENARIOS, "unknown scenario outcome")
    if scenario == POWER_LOSS_SCENARIO:
        require(trigger_response is None, "power-loss scenario received a trigger response")
        return {
            "checkpointExercised": True,
            "triggerResponseAbsent": True,
            "triggerOutcome": None,
        }
    require(trigger_response is not None, "ordinary scenario lost its trigger response")
    operation = SCENARIO_SPECS[scenario][0]
    response = validate_trigger_response(operation, trigger_response, cache_key)
    if operation == "evict":
        expected = {
            "evict-before-first-unlink-fail": ("unlink_failed", 0, False),
            "evict-after-unlinks-fail": ("partial_evict_recovery_required", 1, False),
            "evict-before-rmdir-fail": ("partial_evict_recovery_required", 1, False),
            "evict-after-unlinks-sd-removal": ("evicted", 1, True),
        }[scenario]
        require(
            (response["status"], response["fileCount"], response["evicted"]) == expected,
            "eviction trigger did not produce the scenario-specific outcome",
        )
        require(response["notFound"] is False, "eviction fault falsely reported not_found")
    else:
        require(response["ready"] is False, "sync fault falsely reported ready")
        require(response["downloadedCount"] == 0, "sync fault falsely reported a download")
        require(response["skippedCount"] == 0, "sync fixture caused the asset to be skipped")
        require(response["failedCount"] == 1, "sync fault did not fail exactly one asset")
        require(response["totalBytes"] == 0, "failed sync falsely committed bytes")
        require(len(response["files"]) == 1 and response["files"][0]["state"] == "FAILED", "sync fault lacks FAILED asset evidence")
        require(response["files"][0].get("error") == "asset transfer failed", "sync fault error is not sanitized/decisive")
        require("manifestChecksum" not in response, "failed sync emitted checksum attestation")
    return {
        "checkpointExercised": True,
        "triggerResponseAbsent": False,
        "triggerOutcome": response,
    }


def validate_lab_url(value, *, require_path):
    require(isinstance(value, str) and value and not any(ord(char) < 0x20 for char in value), "invalid lab URL")
    try:
        parsed = urllib.parse.urlsplit(value)
        host = parsed.hostname
        address = ipaddress.ip_address(host or "")
    except (ValueError, UnicodeError):
        raise HilValidationError("lab URL must use a literal private IP") from None
    require(parsed.scheme in {"http", "https"}, "invalid lab URL scheme")
    require(parsed.username is None and parsed.password is None, "lab URL userinfo is forbidden")
    require(not parsed.query and not parsed.fragment, "lab URL query and fragment are forbidden")
    require(address.is_private or address.is_loopback, "lab URL must use a private or loopback IP")
    require(not require_path or parsed.path not in {"", "/"}, "asset URL path is required")
    return value


def sanitized_command_text(argv, secrets=()):
    sanitized = []
    for token in argv:
        rendered = redact_text(token, secrets)
        if "://" in rendered:
            try:
                parsed = urllib.parse.urlsplit(rendered)
                host = parsed.hostname or "redacted-host"
                port = f":{parsed.port}" if parsed.port is not None else ""
                rendered = urllib.parse.urlunsplit((parsed.scheme, host + port, "", "", ""))
            except (ValueError, UnicodeError):
                rendered = "<redacted-url>"
        sanitized.append(rendered)
    return " ".join(repr(item) for item in sanitized) + "\n"


def enforce_capture_limit(value, *, max_bytes, max_lines, code):
    encoded = value.encode("utf-8", errors="replace")
    if len(encoded) > max_bytes:
        raise HilCaptureLimitError(f"{code}_BYTES")
    if value.count("\n") > max_lines:
        raise HilCaptureLimitError(f"{code}_LINES")
    return value


def assert_artifacts_sanitized(payloads, secrets):
    for name, data in payloads.items():
        require(isinstance(data, bytes), f"invalid artifact for credential scan: {name}")
        text = data.decode("utf-8", errors="replace")
        for secret in secrets:
            require(not secret or secret not in text, f"credential marker in artifact: {name}")
        require(JWT_RE.search(text) is None, f"JWT marker in artifact: {name}")
        require(re.search(r"https?://[^/\s:@]+:[^/\s@]+@", text) is None, f"URL userinfo in artifact: {name}")
        require(re.search(r"[?&](?:token|key|secret|password)=", text, re.I) is None, f"query credential in artifact: {name}")
        require(
            re.search(r"(?i)\b(?:proxy-)?authorization\s*[:=]|\b(?:bearer|basic)\s+", text) is None,
            f"authorization credential in artifact: {name}",
        )
    return payloads


def _normalize_mac(value):
    rendered = str(value or "").strip().lower()
    require(
        re.fullmatch(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}", rendered) is not None,
        "invalid device MAC",
    )
    return rendered


def attest_live_connection(base_url, route_uuid, expected_mac, *, open_url=urllib.request.urlopen):
    expected_mac = _normalize_mac(expected_mac)
    request = urllib.request.Request(
        f"{str(base_url).rstrip('/')}/internal/lesson-runtime/metrics",
        headers={"Accept": "application/json"},
        method="GET",
    )
    try:
        response = open_url(request, timeout=5)
        if hasattr(response, "__enter__"):
            with response as opened:
                payload = json.loads(opened.read().decode("utf-8"))
        else:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, UnicodeError, ValueError, urllib.error.URLError):
        raise HilTransportError("HIL_CONNECTION_LOOKUP_FAILED") from None
    require(isinstance(payload, dict), "invalid live connection metadata")
    devices = payload.get("devices")
    require(isinstance(devices, list), "invalid live connection metadata")
    matches = [
        item for item in devices
        if isinstance(item, dict) and item.get("clientId") == route_uuid
    ]
    require(len(matches) == 1, "route UUID does not resolve to one live connection")
    resolved_mac = _normalize_mac(matches[0].get("deviceId"))
    require(resolved_mac == expected_mac, "live connection MAC does not match attended device")
    return {"deviceId": resolved_mac, "clientId": route_uuid}


class RawMcpTransport:
    def __init__(self, base_url, device_uuid, mint_secret):
        self.base_url = str(base_url).rstrip("/")
        self.device_uuid = str(device_uuid)
        self._mint_secret = str(mint_secret)

    def call(self, tool_name, arguments, timeout_seconds=30):
        body = {
            "toolName": tool_name,
            "allowUnlisted": True,
            "timeoutSeconds": timeout_seconds,
            "args": arguments,
        }
        request = urllib.request.Request(
            f"{self.base_url}/internal/devices/{self.device_uuid}/mcp-call",
            data=json.dumps(body, separators=(",", ":")).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-Mint-Secret": self._mint_secret},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds + 2) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError:
            raise HilTransportError("HIL_HTTP_ERROR") from None
        except urllib.error.URLError as exc:
            if isinstance(
                exc.reason,
                (
                    BrokenPipeError,
                    ConnectionAbortedError,
                    ConnectionRefusedError,
                    ConnectionResetError,
                    http.client.RemoteDisconnected,
                ),
            ):
                raise HilDisconnectError("HIL_TRANSPORT_DISCONNECTED") from None
            raise HilTransportError("HIL_TRANSPORT_FAILED") from None
        except (
            BrokenPipeError,
            ConnectionAbortedError,
            ConnectionRefusedError,
            ConnectionResetError,
            http.client.RemoteDisconnected,
        ):
            raise HilDisconnectError("HIL_TRANSPORT_DISCONNECTED") from None
        except TimeoutError:
            raise HilTransportError("HIL_TRANSPORT_TIMEOUT") from None
        except (OSError, UnicodeError, ValueError):
            raise HilTransportError("HIL_TRANSPORT_FAILED") from None
        return parse_internal_mcp_response(payload)


def await_power_loss_disconnect(
    future,
    ready_prompt,
    remove_power_prompt,
    *,
    timeout_seconds,
    utc_clock=None,
):
    clock = utc_clock or utc_now
    completion_lock = threading.Lock()
    completion_observed = {}
    completion_recorded = threading.Event()

    def record_completion(_future):
        observed = clock()
        with completion_lock:
            completion_observed["utc"] = observed
        completion_recorded.set()

    def observed_completion_utc():
        require(
            completion_recorded.wait(timeout_seconds),
            "disconnect completion timestamp unavailable",
        )
        with completion_lock:
            return completion_observed.get("utc")

    future.add_done_callback(record_completion)
    require(not future.done(), "trigger completed before READY boundary")
    ready_prompt()
    require(not future.done(), "trigger completed during READY prompt")
    boundary_utc = clock()
    if future.done():
        require(
            _strict_utc(observed_completion_utc()) > _strict_utc(boundary_utc),
            "trigger completed before power-cut boundary",
        )
    remove_power_prompt()
    removal_confirmed_utc = clock()
    try:
        result = future.result(timeout=timeout_seconds)
    except HilDisconnectError:
        disconnect_observed_utc = observed_completion_utc()
        boundary_time = _strict_utc(boundary_utc)
        disconnect_time = _strict_utc(disconnect_observed_utc)
        confirmation_time = _strict_utc(removal_confirmed_utc)
        require(
            disconnect_time > boundary_time,
            "disconnect completed before power-cut boundary",
        )
        require(
            disconnect_time <= confirmation_time,
            "disconnect completed after removal confirmation",
        )
        return {
            "triggerPendingAtMarker": True,
            "triggerPendingAtCutBoundary": True,
            "powerCutBoundaryUtc": boundary_utc,
            "disconnectObservedUtc": disconnect_observed_utc,
            "powerRemovalConfirmedUtc": removal_confirmed_utc,
            "disconnectAfterPowerCutBoundary": True,
        }
    except concurrent.futures.TimeoutError:
        raise HilTimeoutError("trigger remained pending after cut boundary") from None
    except Exception:
        raise HilValidationError("non-disconnect failure after cut boundary") from None
    raise HilValidationError(
        f"trigger returned after cut boundary: {type(result).__name__}"
    )


class HilToolClient:
    def __init__(self, transport):
        self.transport = transport

    def status(self, expected_cache_key=None):
        response = self.transport.call(HIL_TOOL_NAMES["status"], {}, 30)
        return validate_status_response(response, expected_cache_key=expected_cache_key)

    def inspect(self, cache_key, sibling_cache_key=""):
        args = {"cacheKey": cache_key, "siblingCacheKey": sibling_cache_key}
        response = self.transport.call(HIL_TOOL_NAMES["inspect"], args, 30)
        return validate_inspect_response(response, cache_key, sibling_cache_key)

    def stage(self, cache_key, fixture, sibling_cache_key=""):
        args = {"cacheKey": cache_key, "fixture": fixture, "siblingCacheKey": sibling_cache_key}
        response = self.transport.call(HIL_TOOL_NAMES["stage"], args, 30)
        return validate_fixture_response(response, cache_key, sibling_cache_key, fixture, "staged")

    def cleanup(self, cache_key, fixture, sibling_cache_key=""):
        args = {"cacheKey": cache_key, "fixture": fixture, "siblingCacheKey": sibling_cache_key}
        response = self.transport.call(HIL_TOOL_NAMES["cleanup"], args, 30)
        return validate_fixture_response(response, cache_key, sibling_cache_key, fixture, "cleaned")

    def arm(self, cache_key, operation, checkpoint, action, *, threshold=0, declared_asset_bytes=0, pause_seconds=0):
        args = {
            "cacheKey": cache_key,
            "operation": operation,
            "checkpoint": checkpoint,
            "action": action,
            "threshold": threshold,
            "declaredAssetBytes": declared_asset_bytes,
            "pauseSeconds": pause_seconds,
        }
        response = self.transport.call(HIL_TOOL_NAMES["arm"], args, 30)
        return validate_arm_response(
            response,
            cache_key,
            operation,
            checkpoint,
            action,
            threshold=threshold,
            declared_asset_bytes=declared_asset_bytes,
            pause_seconds=pause_seconds,
        )


class SerialMonitor:
    """Bounded serial reader that reopens across attended power cycles."""

    def __init__(self, port, baud=115200):
        self.port = port
        self.baud = baud
        self._buffer = []
        self._offset = 0
        self._stop = threading.Event()
        self._thread = None
        self._bytes = 0
        self._lines = 0
        self._overflow = None

    def start(self):
        try:
            import serial  # pylint: disable=import-outside-toplevel
        except ImportError as exc:
            raise HilValidationError("pyserial is required for live HIL capture") from exc

        def reader():
            connection = None
            while not self._stop.is_set():
                try:
                    if connection is None:
                        connection = serial.Serial(self.port, self.baud, timeout=0.1)
                    data = connection.read(4096)
                    if data:
                        text = data.decode("utf-8", errors="replace")
                        self._bytes += len(data)
                        self._lines += text.count("\n")
                        if self._bytes > MAX_SERIAL_CAPTURE_BYTES:
                            self._overflow = "HIL_SERIAL_CAPTURE_LIMIT_BYTES"
                            self._stop.set()
                            continue
                        if self._lines > MAX_CAPTURE_LINES:
                            self._overflow = "HIL_SERIAL_CAPTURE_LIMIT_LINES"
                            self._stop.set()
                            continue
                        self._buffer.append(text)
                except Exception:
                    if connection is not None:
                        with suppress(Exception):
                            connection.close()
                    connection = None
                    time.sleep(0.1)
            if connection is not None:
                connection.close()

        self._thread = threading.Thread(target=reader, name="task14-hil-serial", daemon=True)
        self._thread.start()
        return self

    def read_new(self):
        if self._overflow:
            raise HilCaptureLimitError(self._overflow)
        combined = "".join(self._buffer)
        chunk = combined[self._offset :]
        self._offset = len(combined)
        return chunk

    def snapshot(self):
        if self._overflow:
            raise HilCaptureLimitError(self._overflow)
        return "".join(self._buffer)

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _server_logs(container, since_utc, secrets):
    process = subprocess.Popen(
        ["docker", "logs", "--since", since_utc, container],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    output = _bounded_process_output(
        process,
        timeout_seconds=20,
        max_bytes=MAX_SERVER_CAPTURE_BYTES,
        max_lines=MAX_CAPTURE_LINES,
        code="HIL_SERVER_CAPTURE_LIMIT",
    )
    text = output.decode("utf-8", errors="replace")
    text = enforce_capture_limit(
        text,
        max_bytes=MAX_SERVER_CAPTURE_BYTES,
        max_lines=MAX_CAPTURE_LINES,
        code="HIL_SERVER_CAPTURE_LIMIT",
    )
    return redact_text(text, secrets)


def _terminate_process(process):
    if process.poll() is None:
        with suppress(Exception):
            process.kill()
    with suppress(Exception):
        process.wait(timeout=2)


def _bounded_process_output(
    process,
    *,
    timeout_seconds,
    max_bytes,
    max_lines,
    code,
):
    require(process.stdout is not None, f"{code}_STDOUT")
    require(timeout_seconds > 0 and max_bytes > 0 and max_lines > 0, f"{code}_CONFIG")
    descriptor = process.stdout.fileno()
    selector = selectors.DefaultSelector()
    chunks = []
    total_bytes = 0
    total_lines = 0
    deadline = time.monotonic() + timeout_seconds
    try:
        os.set_blocking(descriptor, False)
        selector.register(descriptor, selectors.EVENT_READ)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise HilTimeoutError(f"{code}_TIMEOUT")
            events = selector.select(timeout=min(0.1, remaining))
            if not events:
                continue
            try:
                chunk = os.read(descriptor, min(65536, max_bytes + 1))
            except BlockingIOError:
                continue
            if not chunk:
                remaining = max(0.001, deadline - time.monotonic())
                try:
                    return_code = process.wait(timeout=remaining)
                except subprocess.TimeoutExpired:
                    raise HilTimeoutError(f"{code}_TIMEOUT") from None
                if return_code != 0:
                    raise HilTransportError(f"{code}_PROCESS_FAILED")
                return b"".join(chunks)
            next_bytes = total_bytes + len(chunk)
            if next_bytes > max_bytes:
                raise HilCaptureLimitError(f"{code}_BYTES")
            next_lines = total_lines + chunk.count(b"\n")
            if next_lines > max_lines:
                raise HilCaptureLimitError(f"{code}_LINES")
            chunks.append(chunk)
            total_bytes = next_bytes
            total_lines = next_lines
    except BaseException:
        _terminate_process(process)
        raise
    finally:
        selector.close()


def _asset_pack(arguments, cache_key):
    return {
        "assetPack": {
            "assignmentVersion": 1,
            "lessonId": "hil-task14",
            "lessonVersion": 1,
            "manifestChecksum": arguments.asset_sha256,
            "cacheKey": cache_key,
            "localRoot": f"/sdcard/tbot/lesson-assets/{cache_key}",
            "ready": True,
            "assets": [
                {
                    "key": "hil-asset.png",
                    "path": "hil-asset.png",
                    "url": arguments.asset_url,
                    "sha256": arguments.asset_sha256,
                    "size": arguments.asset_bytes,
                    "critical": True,
                    "state": "READY",
                    "checksumOk": True,
                    "localPath": f"/sdcard/tbot/lesson-assets/{cache_key}/hil-asset.png",
                }
            ],
        }
    }


def _trigger(client, operation, cache_key, arguments):
    args = {"cacheKey": cache_key} if operation == "evict" else _asset_pack(arguments, cache_key)
    response = client.transport.call(TRIGGER_TOOLS[operation], args, 75)
    return validate_trigger_response(operation, response, cache_key)


def _validator_report(scenario, result):
    script = SCRIPT_DIR / "lesson_studio_task14_fault_driver.py"
    # The final subprocess validation is repeated after artifacts are materialized.
    return {"scenario": scenario, "status": result.get("status"), "validationErrors": []}, script


def run_scenario(arguments, scenario, *, operator_input=input):
    require(scenario in HIL_STORAGE_SCENARIOS, "unknown HIL storage scenario")
    validate_run_roots(arguments)
    operation, checkpoint, action, threshold, pause_seconds, power_loss = SCENARIO_SPECS[scenario]
    started = utc_now()
    events = []
    responses = {key: None for key in FAILURE_RESPONSE_KEYS}
    payloads = {}
    scenario_dir = Path(arguments.evidence_dir) / scenario
    fixture = "preservation_set"
    phase = "setup"
    build_identity = None
    connection_identity = None
    secret = ""
    cache_key = None
    sibling = None
    client = None
    monitor = None
    staged = False
    cleaned = False
    recovery_blocked_cleanup = False
    try:
        build_identity = load_build_identity(arguments.build_manifest, expected_profile="hil")
        phase = "attestation"
        connection_identity = attest_live_connection(
            arguments.esp_base_url, arguments.device_uuid, arguments.device_id
        )
        phase = "setup"
        secret = os.environ.get(arguments.mint_secret_env, "")
        require(bool(secret), f"missing {arguments.mint_secret_env}")
        cache_key = f"hil-task14/v1-{arguments.asset_sha256}"
        sibling = f"hil-task14/v2-{arguments.asset_sha256}"
        transport = RawMcpTransport(arguments.esp_base_url, arguments.device_uuid, secret)
        client = HilToolClient(transport)
        phase = "serial"
        monitor = SerialMonitor(arguments.serial_port).start()
        phase = "status"
        status_before = client.status()
        responses["statusBefore"] = status_before
        require(status_before["status"] == "idle", "HIL controller is not idle before scenario")
        events.append("status-before")
        phase = "inspect"
        inspect_before = client.inspect(cache_key, sibling)
        responses["inspectBefore"] = inspect_before
        events.append("inspect-before")
        phase = "stage"
        stage = client.stage(cache_key, fixture, sibling)
        responses["stage"] = stage
        staged = True
        events.append("stage")
        declared = arguments.asset_bytes if checkpoint == "after_download_bytes" else 0
        phase = "arm"
        arm = client.arm(
            cache_key,
            operation,
            checkpoint,
            action,
            threshold=threshold,
            declared_asset_bytes=declared,
            pause_seconds=pause_seconds,
        )
        responses["arm"] = arm
        events.append("arm")
        trigger = None
        reboot_serial = ""
        checkpoint_utc = power_removal_confirmed_utc = None
        power_data = None
        post_status = post_reboot_inspect = None
        if action == "pause":
            phase = "trigger"
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_trigger, client, operation, cache_key, arguments)
                phase = "serial"
                serial_until_marker = poll_checkpoint(
                    monitor.read_new,
                    operation=operation,
                    checkpoint=checkpoint,
                    cache_key=cache_key,
                    expected_count=SCENARIO_EXPECTED_PROGRESS[scenario],
                    timeout_seconds=min(75, pause_seconds + 20),
                )
                checkpoint_utc = utc_now()
                if power_loss:
                    sequence_log = serial_until_marker
                    sequence_deadline = time.monotonic() + 1
                    while "HIL_STORAGE_FAULT_CONSUMED" not in sequence_log and time.monotonic() < sequence_deadline:
                        sequence_log += monitor.read_new()
                        time.sleep(0.02)
                    def ready_for_power_cut():
                        operator_input(
                            "Reached exact checkpoint. Prepare to remove power, but do not remove it yet. "
                            "Press Enter when ready."
                        )

                    def remove_power_now():
                        operator_input(
                            "READY boundary recorded. Remove robot power now, then press Enter."
                        )

                    disconnect_evidence = await_power_loss_disconnect(
                        future,
                        ready_for_power_cut,
                        remove_power_now,
                        timeout_seconds=10,
                    )
                    trigger = None
                    power_removal_confirmed_utc = disconnect_evidence["powerRemovalConfirmedUtc"]
                    operator_input(
                        "Disconnect observed after the READY boundary. Confirm power is removed, then press Enter."
                    )
                    reboot_start = len(monitor.snapshot())
                    operator_input("Restore robot power, wait for boot, then press Enter.")
                    deadline = time.monotonic() + 45
                    post_status = None
                    phase = "status"
                    while time.monotonic() < deadline:
                        try:
                            post_status = client.status()
                            if post_status["status"] == "idle":
                                break
                        except HilValidationError:
                            pass
                        time.sleep(0.5)
                    require(post_status is not None and post_status["status"] == "idle", "volatile HIL arm did not clear after reboot")
                    reboot_serial = monitor.snapshot()[reboot_start:]
                    phase = "inspect"
                    post_reboot_inspect = client.inspect(cache_key, sibling)
                    phase = "trigger"
                    retry = _trigger(client, operation, cache_key, arguments)
                    validate_trigger_response(operation, retry, cache_key, require_ready=True)
                    power_data = {
                        "powerLoss": True,
                        "checkpointReached": True,
                        "triggerResponseAbsent": True,
                        "successMarkerBeforeLoss": "HIL_STORAGE_CHECKPOINT_CONTINUED" in sequence_log,
                        "rebootCaptured": "TBOT_HIL_STORAGE_FAULTS_ENABLED non-production-image" in reboot_serial,
                        "postRebootStatus": post_status,
                        "postRebootInspected": True,
                        "retryStatus": "ready" if isinstance(retry, dict) else "failed",
                        "retryResponse": retry,
                        "checkpointReachedUtc": checkpoint_utc,
                        **disconnect_evidence,
                    }
                else:
                    operator_input("Remove and reinsert the SD card within the pause window, then press Enter.")
                    phase = "trigger"
                    trigger = future.result(timeout=pause_seconds + 10)
        else:
            serial_until_marker = ""
            phase = "trigger"
            trigger = _trigger(client, operation, cache_key, arguments)
        if trigger is not None:
            responses["trigger"] = trigger
        events.append("trigger")
        phase = "status"
        status_after = post_status if power_loss else client.status(cache_key)
        responses["statusAfter"] = status_after
        events.append("status-after")
        phase = "inspect"
        inspect_after = post_reboot_inspect if power_loss else client.inspect(cache_key, sibling)
        responses["inspectAfter"] = inspect_after
        events.append("inspect-after")
        validate_preservation_inspections(
            scenario,
            inspect_before,
            inspect_after,
            post_reboot=post_reboot_inspect if power_loss else None,
        )
        recovery = recovery_not_attempted()
        if scenario in PARTIAL_EVICTION_SCENARIOS:
            recovery_blocked_cleanup = True
        phase = "trigger"
        outcome = validate_scenario_outcome(scenario, trigger, cache_key=cache_key)
        if scenario in PARTIAL_EVICTION_SCENARIOS:
            phase = "recovery"
            retry_raw = client.transport.call(
                TRIGGER_TOOLS["evict"], {"cacheKey": cache_key}, 75
            )
            retry = validate_partial_eviction_retry(retry_raw, cache_key)
            events.append("recovery-trigger")
            recovered_inspection = client.inspect(cache_key, sibling)
            validate_recovered_preservation_inspection(
                inspect_before, inspect_after, recovered_inspection
            )
            events.append("recovery-inspect")
            recovery = {
                "attempted": True,
                "operation": "evict",
                "reason": "expected_partial_eviction",
                "response": retry,
                "inspection": recovered_inspection,
            }
            responses["recovery"] = recovery
            recovery_blocked_cleanup = False
        if power_loss and operation == "sync":
            evicted_retry = _trigger(client, "evict", cache_key, arguments)
            require(evicted_retry.get("evicted") is True, "retry cache cleanup eviction failed")
        phase = "cleanup"
        cleanup = client.cleanup(cache_key, fixture, sibling)
        responses["cleanup"] = cleanup
        cleaned = True
        events.append("cleanup")
        cleanup_inspect = client.inspect(cache_key, sibling)
        validated_cleanup_inspect = validate_cleanup_inspection(
            inspect_before, cleanup_inspect
        )
        final_status = client.status()
        controller_inactive = final_status["armed"] is False
        require(controller_inactive, "HIL controller remained active after cleanup")
        validate_event_order(events)
        sequences = sequences_from_serial(
            arm, sequence_log,
            operation=operation, checkpoint=checkpoint, cache_key=cache_key,
        ) if power_loss else validate_sequences(arm, status_after)
        ended = utc_now()
        serial_log = redact_text(monitor.snapshot(), (secret,))
        server_log = _server_logs(arguments.server_container, started, (secret,))
        result = {
            "scenario": scenario,
            "status": "PASS",
            "deviceId": connection_identity["deviceId"],
            "deviceUuid": arguments.device_uuid,
            "connectionIdentity": connection_identity,
            "cacheKey": cache_key,
            "utcStart": started,
            "utcEnd": ended,
            "buildIdentity": build_identity,
            "armSequence": sequences["arm"],
            "reachedSequence": sequences["reached"],
            "consumedSequence": sequences["consumed"],
            "events": events,
            "operation": operation,
            "checkpoint": checkpoint,
            "faultAction": action,
            "expectedProgress": SCENARIO_EXPECTED_PROGRESS[scenario],
            "cleanupInspection": validated_cleanup_inspect,
            "finalStatus": final_status,
            "cleanupVerified": validated_cleanup_inspect == cleanup_inspect,
            "controllerInactive": controller_inactive,
            "recovery": recovery,
            **outcome,
        }
        if power_data:
            finalize_power_loss_result(result, power_data)
        phase = "validator"
        evidence, validator_script = _validator_report(scenario, result)
        payloads.update(
            {
                "command.txt": sanitized_command_text(sys.argv, (secret,)).encode("utf-8"),
                "serial.log": (serial_log or "<no serial output>\n").encode("utf-8"),
                "server.log": (server_log or "<no server output>\n").encode("utf-8"),
                "timeline.log": ("\n".join(f"{index + 1} {name}" for index, name in enumerate(events)) + "\n").encode("utf-8"),
                "build-manifest.json": json_bytes(build_identity),
                "build-manifest.sha256": f"{hashlib.sha256(json_bytes(build_identity)).hexdigest()}  build-manifest.json\n".encode("ascii"),
                "status-before.json": json_bytes(status_before),
                "inspect-before.json": json_bytes(inspect_before),
                "stage-response.json": json_bytes(stage),
                "arm-response.json": json_bytes(arm),
                "status-after.json": json_bytes(status_after),
                "inspect-after.json": json_bytes(inspect_after),
                "cleanup-response.json": json_bytes(cleanup),
                "recovery-response.json": json_bytes(recovery),
                "result.json": json_bytes(result),
                "evidence.json": json_bytes(evidence),
                "validator-exit-code.txt": b"0\n",
            }
        )
        if power_loss:
            payloads.update(
                {
                    "checkpoint-reached-utc.txt": f"{checkpoint_utc}\n".encode("ascii"),
                    "power-removed-utc.txt": f"{power_removal_confirmed_utc}\n".encode("ascii"),
                    "reboot-serial.log": (redact_text(reboot_serial, (secret,)) or "<no reboot output>\n").encode("utf-8"),
                    "post-reboot-inspect.json": json_bytes(post_reboot_inspect),
                }
            )
        else:
            payloads["trigger-response.json"] = json_bytes(trigger)
        assert_artifacts_sanitized(payloads, (secret,))
        def set_failure_phase(value):
            nonlocal phase
            phase = value

        phase = "publication"
        publish_validated_scenario_directory(
            scenario_dir,
            payloads,
            scenario=scenario,
            power_loss=power_loss,
            validator_script=validator_script,
            secrets=(secret,),
            phase_changed=set_failure_phase,
        )
        return result
    except BaseException as primary:
        secondary_events = []
        if client is not None and staged and not cleaned and not recovery_blocked_cleanup:
            try:
                responses["cleanup"] = client.cleanup(cache_key, fixture, sibling)
                events.append("cleanup")
            except Exception:
                secondary_events.append("secondary fixture cleanup also failed")
        failure_phase = phase if isinstance(
            primary,
            (HilValidationError, HilTransportError, BuildIdentityError, OSError, ValueError),
        ) else "internal"
        serial_log = monitor.snapshot() if monitor is not None else ""
        server_log = ""
        if build_identity is not None:
            with suppress(Exception):
                server_log = _server_logs(arguments.server_container, started, (secret,))
        timeline_log = "\n".join(
            f"{index + 1} {name}" for index, name in enumerate(events)
        )
        if secondary_events:
            timeline_log += "\n" + "\n".join(secondary_events)
        try:
            write_failure_evidence(
                arguments.failure_evidence_dir,
                scenario=scenario,
                phase=failure_phase,
                utc_start=started,
                utc_failure=utc_now(),
                completed_events=events,
                build_identity=build_identity,
                last_responses=responses,
                command=sanitized_command_text(sys.argv, (secret,)),
                serial_log=serial_log,
                server_log=server_log,
                timeline_log=timeline_log,
                secrets=(secret,),
            )
        except BaseException:
            print(
                "lesson storage HIL: failure evidence publication also failed",
                file=sys.stderr,
            )
        raise
    finally:
        if monitor is not None:
            with suppress(Exception):
                monitor.stop()


def preflight(arguments):
    identity = load_build_identity(arguments.build_manifest, expected_profile="hil")
    secret = os.environ.get(arguments.mint_secret_env, "")
    require(bool(secret), f"missing {arguments.mint_secret_env}")
    device_id = _normalize_mac(arguments.device_id)
    require(re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}", arguments.device_uuid) is not None, "invalid device UUID")
    require(SHA256_RE.fullmatch(arguments.asset_sha256) is not None, "invalid asset SHA-256")
    require(type(arguments.asset_bytes) is int and arguments.asset_bytes > 0, "invalid asset byte count")
    validate_lab_url(arguments.esp_base_url, require_path=False)
    validate_lab_url(arguments.asset_url, require_path=True)
    connection_identity = attest_live_connection(
        arguments.esp_base_url, arguments.device_uuid, device_id
    )
    client = HilToolClient(RawMcpTransport(arguments.esp_base_url, arguments.device_uuid, secret))
    status = client.status()
    require(status["status"] == "idle", "HIL controller is not idle")
    result = {
        "status": "PASS",
        "deviceId": device_id,
        "deviceUuid": arguments.device_uuid,
        "connectionIdentity": connection_identity,
        "buildIdentity": identity,
    }
    print(json.dumps(result, sort_keys=True))
    return result


def _add_live_arguments(parser):
    parser.add_argument("--device-id", required=True)
    parser.add_argument("--device-uuid", required=True)
    parser.add_argument("--serial-port", required=True)
    parser.add_argument("--esp-base-url", required=True)
    parser.add_argument("--asset-url", required=True)
    parser.add_argument("--asset-sha256", required=True)
    parser.add_argument("--asset-bytes", required=True, type=int)
    parser.add_argument("--build-manifest", required=True, type=Path)
    parser.add_argument("--evidence-dir", required=True, type=Path)
    parser.add_argument("--mint-secret-env", default="TBOT_DEVICE_MINT_SECRET")
    parser.add_argument("--server-container", default="tbot-esp32-server")


def build_argument_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    preflight_parser = subparsers.add_parser("preflight")
    _add_live_arguments(preflight_parser)
    scenario_parser = subparsers.add_parser("run-scenario")
    _add_live_arguments(scenario_parser)
    scenario_parser.add_argument("--failure-evidence-dir", required=True, type=Path)
    scenario_parser.add_argument("--scenario", required=True, choices=HIL_STORAGE_SCENARIOS)
    matrix_parser = subparsers.add_parser("run-matrix")
    _add_live_arguments(matrix_parser)
    matrix_parser.add_argument("--failure-evidence-dir", required=True, type=Path)
    return parser


def main():
    arguments = build_argument_parser().parse_args()
    try:
        if arguments.command == "preflight":
            preflight(arguments)
        elif arguments.command == "run-scenario":
            validate_run_roots(arguments)
            run_scenario(arguments, arguments.scenario)
        else:
            validate_run_roots(arguments)
            report_path = Path(arguments.evidence_dir) / MATRIX_REPORT_NAME
            with suppress(FileNotFoundError):
                report_path.unlink()
            preflight_result = None
            for scenario in HIL_STORAGE_SCENARIOS:
                result = run_scenario(arguments, scenario)
                if preflight_result is None:
                    preflight_result = {
                        key: result[key]
                        for key in (
                            "status", "deviceId", "deviceUuid",
                            "connectionIdentity", "buildIdentity",
                        )
                    }
            publish_matrix_report(arguments, preflight_result)
        return 0
    except Exception as exc:
        print(f"lesson storage HIL: FAIL: {redact_text(exc)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
