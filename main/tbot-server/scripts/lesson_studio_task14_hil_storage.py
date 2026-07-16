#!/usr/bin/env python3
"""Attended, MAC-gated Task 14 lesson-storage HIL orchestrator."""

import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
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
    "cleanup-response.json", "result.json", "evidence.json",
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
SHA256_RE = re.compile(r"[0-9a-f]{64}")
JWT_RE = re.compile(
    r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{3,}\.[A-Za-z0-9_-]{3,}\.[A-Za-z0-9_-]{3,}(?![A-Za-z0-9_-])"
)
ABSOLUTE_PATH_RE = re.compile(r"/(?:Users|home|private|opt|tmp)/[^\s,;]+")


class HilValidationError(RuntimeError):
    pass


class HilTimeoutError(HilValidationError):
    pass


class HilTransportError(HilValidationError):
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


def validate_arm_response(value, cache_key, operation, checkpoint, action):
    value = _exact_fields(value, ARM_FIELDS, "arm")
    require(value["cacheKey"] == cache_key, "arm response cache key mismatch")
    require(value["status"] == "armed", "fault was not armed")
    require(value["operation"] == operation, "arm operation mismatch")
    require(value["checkpoint"] == checkpoint, "arm checkpoint mismatch")
    require(value["action"] == action, "arm action mismatch")
    for name in ("threshold", "declaredAssetBytes", "pauseSeconds"):
        _exact_int(value[name], name)
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
    rendered = re.sub(r"(?i)(authorization|x-mint-secret|password|token)\s*[:=]\s*\S+", r"\1=<redacted>", rendered)
    return rendered


def poll_checkpoint(read_chunk, *, operation, checkpoint, timeout_seconds, monotonic=time.monotonic, sleep=time.sleep):
    require(type(timeout_seconds) in (int, float) and not isinstance(timeout_seconds, bool), "invalid marker timeout")
    require(0 < timeout_seconds <= 90, "invalid marker timeout")
    marker = f"HIL_STORAGE_CHECKPOINT_REACHED operation={operation} checkpoint={checkpoint}"
    deadline = monotonic() + timeout_seconds
    captured = []
    while monotonic() < deadline:
        chunk = read_chunk()
        if chunk:
            captured.append(str(chunk))
            combined = "".join(captured)
            if any(marker in line for line in combined.splitlines()):
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
    }
    for name, expected in required.items():
        require(value.get(name) == expected and type(value.get(name)) is type(expected), f"invalid power-loss field: {name}")
    status = validate_status_response(value.get("postRebootStatus"), expected_cache_key=None)
    require(status["status"] == "idle", "volatile HIL arm survived reboot")
    return value


def validate_event_order(events):
    expected = [
        "status-before", "inspect-before", "stage", "arm", "trigger",
        "status-after", "inspect-after", "cleanup",
    ]
    require(events == expected, "HIL cleanup/status/inspect order invalid")
    return events


def validate_sequences(arm, status):
    require(status.get("status") == "consumed", "HIL arm was not consumed")
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
        _exact_fields(
            value,
            frozenset({"cacheKey", "status", "evicted", "notFound", "fileCount", "reason"}),
            "eviction trigger",
        )
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
        except (OSError, UnicodeError, ValueError, urllib.error.HTTPError) as exc:
            raise HilTransportError(redact_text(type(exc).__name__, (self._mint_secret,))) from None
        return parse_internal_mcp_response(payload)


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
        return validate_arm_response(response, cache_key, operation, checkpoint, action)


class SerialMonitor:
    """Bounded serial reader that reopens across attended power cycles."""

    def __init__(self, port, baud=115200):
        self.port = port
        self.baud = baud
        self._buffer = []
        self._offset = 0
        self._stop = threading.Event()
        self._thread = None

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
                        self._buffer.append(data.decode("utf-8", errors="replace"))
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
        combined = "".join(self._buffer)
        chunk = combined[self._offset :]
        self._offset = len(combined)
        return chunk

    def snapshot(self):
        return "".join(self._buffer)

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _server_logs(container, since_utc, secrets):
    result = subprocess.run(
        ["docker", "logs", "--since", since_utc, container],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        timeout=20,
    )
    return redact_text(result.stdout, secrets)


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


def _command_text(argv):
    return " ".join(repr(item) for item in argv) + "\n"


def _validator_report(scenario, result):
    script = SCRIPT_DIR / "lesson_studio_task14_fault_driver.py"
    # The final subprocess validation is repeated after artifacts are materialized.
    return {"scenario": scenario, "status": result.get("status"), "validationErrors": []}, script


def run_scenario(arguments, scenario, *, operator_input=input):
    require(scenario in HIL_STORAGE_SCENARIOS, "unknown HIL storage scenario")
    operation, checkpoint, action, threshold, pause_seconds, power_loss = SCENARIO_SPECS[scenario]
    build_identity = load_build_identity(arguments.build_manifest, expected_profile="hil")
    secret = os.environ.get(arguments.mint_secret_env, "")
    require(bool(secret), f"missing {arguments.mint_secret_env}")
    cache_key = f"hil-task14/v1-{arguments.asset_sha256}"
    sibling = f"hil-task14/v2-{arguments.asset_sha256}"
    transport = RawMcpTransport(arguments.esp_base_url, arguments.device_uuid, secret)
    client = HilToolClient(transport)
    monitor = SerialMonitor(arguments.serial_port).start()
    started = utc_now()
    events = []
    payloads = {}
    scenario_dir = Path(arguments.evidence_dir) / scenario
    fixture = "preservation_set"
    staged = False
    cleaned = False
    try:
        status_before = client.status()
        require(status_before["status"] == "idle", "HIL controller is not idle before scenario")
        events.append("status-before")
        inspect_before = client.inspect(cache_key, sibling)
        events.append("inspect-before")
        stage = client.stage(cache_key, fixture, sibling)
        staged = True
        events.append("stage")
        declared = arguments.asset_bytes if checkpoint == "after_download_bytes" else 0
        arm = client.arm(
            cache_key,
            operation,
            checkpoint,
            action,
            threshold=threshold,
            declared_asset_bytes=declared,
            pause_seconds=pause_seconds,
        )
        events.append("arm")
        trigger = None
        reboot_serial = ""
        checkpoint_utc = power_removed_utc = None
        power_data = None
        post_status = post_reboot_inspect = None
        if action == "pause":
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_trigger, client, operation, cache_key, arguments)
                serial_until_marker = poll_checkpoint(
                    monitor.read_new,
                    operation=operation,
                    checkpoint=checkpoint,
                    timeout_seconds=min(75, pause_seconds + 20),
                )
                checkpoint_utc = utc_now()
                if power_loss:
                    sequence_log = serial_until_marker
                    sequence_deadline = time.monotonic() + 1
                    while "HIL_STORAGE_FAULT_CONSUMED" not in sequence_log and time.monotonic() < sequence_deadline:
                        sequence_log += monitor.read_new()
                        time.sleep(0.02)
                    operator_input("Reached exact checkpoint. Remove robot power now, then press Enter.")
                    power_removed_utc = utc_now()
                    try:
                        trigger = future.result(timeout=10)
                    except Exception:
                        trigger = None
                    require(trigger is None, "power loss unexpectedly returned a trigger response")
                    reboot_start = len(monitor.snapshot())
                    operator_input("Restore robot power, wait for boot, then press Enter.")
                    deadline = time.monotonic() + 45
                    post_status = None
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
                    post_reboot_inspect = client.inspect(cache_key, sibling)
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
                    }
                    validate_power_loss_result(power_data)
                else:
                    operator_input("Remove and reinsert the SD card within the pause window, then press Enter.")
                    trigger = future.result(timeout=pause_seconds + 10)
        else:
            serial_until_marker = ""
            trigger = _trigger(client, operation, cache_key, arguments)
        events.append("trigger")
        status_after = post_status if power_loss else client.status(cache_key)
        events.append("status-after")
        inspect_after = post_reboot_inspect if power_loss else client.inspect(cache_key, sibling)
        events.append("inspect-after")
        if power_loss and operation == "sync":
            evicted_retry = _trigger(client, "evict", cache_key, arguments)
            require(evicted_retry.get("evicted") is True, "retry cache cleanup eviction failed")
        cleanup = client.cleanup(cache_key, fixture, sibling)
        cleaned = True
        events.append("cleanup")
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
            "deviceId": arguments.device_id,
            "deviceUuid": arguments.device_uuid,
            "cacheKey": cache_key,
            "utcStart": started,
            "utcEnd": ended,
            "buildIdentity": build_identity,
            "armSequence": sequences["arm"],
            "reachedSequence": sequences["reached"],
            "consumedSequence": sequences["consumed"],
            "events": events,
        }
        if power_data:
            result.update(power_data)
            result["armClearedAfterReboot"] = True
        evidence, validator_script = _validator_report(scenario, result)
        payloads.update(
            {
                "command.txt": _command_text(sys.argv).encode("utf-8"),
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
                "result.json": json_bytes(result),
                "evidence.json": json_bytes(evidence),
                "validator-exit-code.txt": b"0\n",
            }
        )
        if power_loss:
            payloads.update(
                {
                    "checkpoint-reached-utc.txt": f"{checkpoint_utc}\n".encode("ascii"),
                    "power-removed-utc.txt": f"{power_removed_utc}\n".encode("ascii"),
                    "reboot-serial.log": (redact_text(reboot_serial, (secret,)) or "<no reboot output>\n").encode("utf-8"),
                    "post-reboot-inspect.json": json_bytes(post_reboot_inspect),
                }
            )
        else:
            payloads["trigger-response.json"] = json_bytes(trigger)
        secret_bytes = secret.encode("utf-8")
        require(
            all(secret_bytes not in data for data in payloads.values()),
            "mint secret would be persisted in evidence",
        )
        finalize_scenario_directory(scenario_dir, payloads, power_loss=power_loss)
        validator = subprocess.run(
            [
                sys.executable, str(validator_script), "--hil-storage-scenario", scenario,
                "--evidence-dir", str(scenario_dir), "--output", str(scenario_dir / "evidence.json"),
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=30,
        )
        # The validator rewrites evidence, so refresh exit code and the final checksum last.
        atomic_write_bytes(scenario_dir / "validator-exit-code.txt", f"{validator.returncode}\n".encode("ascii"))
        checksum_lines = []
        for name in scenario_artifact_names(power_loss=power_loss):
            if name != "SHA256SUMS":
                checksum_lines.append(f"{hashlib.sha256((scenario_dir / name).read_bytes()).hexdigest()}  {name}\n")
        atomic_write_bytes(scenario_dir / "SHA256SUMS", "".join(checksum_lines).encode("ascii"))
        require(validator.returncode == 0, "HIL scenario validator failed")
        return result
    except BaseException:
        if staged and not cleaned:
            with suppress(Exception):
                client.cleanup(cache_key, fixture, sibling)
        raise
    finally:
        monitor.stop()


def preflight(arguments):
    identity = load_build_identity(arguments.build_manifest, expected_profile="hil")
    secret = os.environ.get(arguments.mint_secret_env, "")
    require(bool(secret), f"missing {arguments.mint_secret_env}")
    require(re.fullmatch(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}", arguments.device_id) is not None, "invalid device MAC")
    require(re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}", arguments.device_uuid) is not None, "invalid device UUID")
    require(SHA256_RE.fullmatch(arguments.asset_sha256) is not None, "invalid asset SHA-256")
    require(type(arguments.asset_bytes) is int and arguments.asset_bytes > 0, "invalid asset byte count")
    client = HilToolClient(RawMcpTransport(arguments.esp_base_url, arguments.device_uuid, secret))
    status = client.status()
    require(status["status"] == "idle", "HIL controller is not idle")
    result = {"status": "PASS", "deviceId": arguments.device_id, "deviceUuid": arguments.device_uuid, "buildIdentity": identity}
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    preflight_parser = subparsers.add_parser("preflight")
    _add_live_arguments(preflight_parser)
    scenario_parser = subparsers.add_parser("run-scenario")
    _add_live_arguments(scenario_parser)
    scenario_parser.add_argument("--scenario", required=True, choices=HIL_STORAGE_SCENARIOS)
    matrix_parser = subparsers.add_parser("run-matrix")
    _add_live_arguments(matrix_parser)
    arguments = parser.parse_args()
    try:
        if arguments.command == "preflight":
            preflight(arguments)
        elif arguments.command == "run-scenario":
            preflight(arguments)
            run_scenario(arguments, arguments.scenario)
        else:
            preflight(arguments)
            for scenario in HIL_STORAGE_SCENARIOS:
                run_scenario(arguments, scenario)
        return 0
    except (HilValidationError, HilTransportError, BuildIdentityError, OSError, ValueError) as exc:
        print(f"lesson storage HIL: FAIL: {redact_text(exc)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
