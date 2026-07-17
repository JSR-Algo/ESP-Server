import argparse
import hashlib
import importlib.util
import json
import os
import pty
import re
import select
import shutil
import signal
import struct
import subprocess
import sys
import time
import zlib
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
HIL_CONNECTION = {
    "deviceId": "28:84:85:85:1a:80",
    "deviceUuid": "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
    "connectionIdentity": {
        "deviceId": "28:84:85:85:1a:80",
        "clientId": "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
    },
}
HIL_RUNTIME = {"cleanupVerified": True, "controllerInactive": True}
PRIMARY_SENTINEL_SHA = "e95ab394bdf8569652429018519989d3e94cae168cf91c269c81a2c9bb00d5ec"
SIBLING_SENTINEL_SHA = "462cc80e16c12bbee14c7eba5e61da286e79580d6dc5b996bfcf7a43f30a4cf8"


def preservation_inspection(cache_key, sibling, primary_state, sibling_state="full"):
    entries = [
        {"label": "lesson-assets/current.json", "nodeType": "missing", "bytes": 0, "sha256": ""},
        {"label": "lesson-assets/pvg", "nodeType": "missing", "bytes": 0, "sha256": ""},
        {"label": "lesson-assets/shared", "nodeType": "missing", "bytes": 0, "sha256": ""},
    ]
    for key, state, digest in (
        (cache_key, primary_state, PRIMARY_SENTINEL_SHA),
        (sibling, sibling_state, SIBLING_SENTINEL_SHA),
    ):
        label = f"lesson-assets/{key}"
        if state == "missing":
            entries.append({"label": label, "nodeType": "missing", "bytes": 0, "sha256": ""})
        else:
            entries.append({"label": label, "nodeType": "directory", "bytes": 0, "sha256": ""})
            if state == "full":
                entries.append({
                    "label": f"{label}/.tbot-hil-sentinel",
                    "nodeType": "regular_file", "bytes": 33, "sha256": digest,
                })
    return {
        "cacheKey": cache_key,
        "siblingCacheKey": sibling,
        "status": "inspected",
        "truncated": False,
        "entries": sorted(entries, key=lambda item: item["label"]),
    }


def load_script(name: str):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.removesuffix(".py"), path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


fault = load_script("lesson_studio_task14_fault_driver.py")
soak = load_script("lesson_studio_task14_soak.py")
audit = load_script("lesson_studio_task14_log_audit.py")


def test_fault_driver_hil_storage_extension_remains_validation_only():
    hil = load_script("lesson_studio_task14_hil_storage.py")
    source = (ROOT / "scripts" / "lesson_studio_task14_fault_driver.py").read_text()

    assert fault.HIL_STORAGE_SCENARIOS == hil.HIL_STORAGE_SCENARIOS
    for forbidden in ("urlopen(", "requests.post(", "serial.Serial(", "tools/call", "arm_fault"):
        assert forbidden not in source


def test_fault_driver_independently_binds_control_artifacts_and_serial_sequences(tmp_path):
    cache_key = f"hil-task14/v1-{'d' * 64}"
    arm = {
        "cacheKey": cache_key,
        "status": "armed",
        "operation": "evict",
        "checkpoint": "after_unlinks",
        "action": "fail",
        "threshold": 1,
        "declaredAssetBytes": 0,
        "pauseSeconds": 0,
        "armSequence": 10,
    }
    status = {
        "status": "consumed",
        "cacheKey": cache_key,
        "armed": False,
        "reached": True,
        "consumed": True,
        "operation": "evict",
        "checkpoint": "after_unlinks",
        "action": "fail",
        "threshold": 1,
        "declaredAssetBytes": 0,
        "pauseSeconds": 0,
        "armSequence": 10,
        "reachedSequence": 11,
        "consumedSequence": 12,
    }
    result = {
        "cacheKey": cache_key,
        "operation": "evict",
        "checkpoint": "after_unlinks",
        "faultAction": "fail",
        "armSequence": 10,
        "reachedSequence": 11,
        "consumedSequence": 12,
        "cleanupVerified": True,
        "controllerInactive": True,
        "cleanupInspection": preservation_inspection(cache_key, f"hil-task14/v2-{'d' * 64}", "missing", "missing"),
        "finalStatus": status,
    }
    (tmp_path / "arm-response.json").write_text(json.dumps(arm))
    (tmp_path / "status-after.json").write_text(json.dumps(status))
    (tmp_path / "inspect-before.json").write_text(json.dumps(
        preservation_inspection(cache_key, f"hil-task14/v2-{'d' * 64}", "missing", "missing")
    ))
    serial = (
        "HIL_STORAGE_CHECKPOINT_REACHED operation=evict checkpoint=after_unlinks "
        f"cache_key={cache_key} count=1 reached_sequence=11\n"
        "HIL_STORAGE_FAULT_CONSUMED operation=evict checkpoint=after_unlinks "
        f"action=fail cache_key={cache_key} consumed_sequence=12\n"
    )
    (tmp_path / "serial.log").write_text(serial)

    assert fault._hil_control_artifact_errors(
        "evict-after-unlinks-fail", tmp_path, result
    ) == []

    tampered = dict(arm, threshold=0)
    (tmp_path / "arm-response.json").write_text(json.dumps(tampered))
    assert fault._hil_control_artifact_errors(
        "evict-after-unlinks-fail", tmp_path, result
    )

    (tmp_path / "arm-response.json").write_text(json.dumps(arm))
    for tampered_serial in (
        serial.replace("count=1", "count=0"),
        serial.replace("action=fail", "action=pause"),
    ):
        (tmp_path / "serial.log").write_text(tampered_serial)
        assert fault._hil_control_artifact_errors(
            "evict-after-unlinks-fail", tmp_path, result
        )

    (tmp_path / "serial.log").write_text(serial)
    (tmp_path / "status-after.json").write_text(json.dumps({**status, "reachedSequence": 12}))
    assert fault._hil_control_artifact_errors(
        "evict-after-unlinks-fail", tmp_path, result
    )

    (tmp_path / "status-after.json").write_text(json.dumps(status))
    (tmp_path / "serial.log").write_text("forged result-only sequence evidence\n")
    assert fault._hil_control_artifact_errors(
        "evict-after-unlinks-fail", tmp_path, result
    )


def test_fault_driver_semantically_validates_all_hil_artifacts_and_credentials(tmp_path):
    cache_key = f"hil-task14/v1-{'d' * 64}"
    sibling = f"hil-task14/v2-{'d' * 64}"
    fixture = {
        "cacheKey": cache_key,
        "siblingCacheKey": sibling,
        "fixture": "preservation_set",
        "status": "staged",
        "changed": True,
    }
    inspect_before = preservation_inspection(cache_key, sibling, "missing", "missing")
    inspect_after = preservation_inspection(cache_key, sibling, "directory_only")
    trigger = {
        "cacheKey": cache_key,
        "status": "partial_evict_recovery_required",
        "evicted": False,
        "notFound": False,
        "fileCount": 1,
        "reason": "partial_evict_recovery_required",
    }
    result = {
        "cacheKey": cache_key,
        "triggerOutcome": trigger,
        "triggerResponseAbsent": False,
    }
    payloads = {
        "stage-response.json": fixture,
        "inspect-before.json": inspect_before,
        "inspect-after.json": inspect_after,
        "trigger-response.json": trigger,
        "cleanup-response.json": {**fixture, "status": "cleaned"},
    }
    for name, payload in payloads.items():
        (tmp_path / name).write_text(json.dumps(payload))

    assert fault._hil_semantic_artifact_errors(
        "evict-after-unlinks-fail", tmp_path, result
    ) == []

    for name, candidate in (
        ("stage-response.json", {**fixture, "changed": False}),
        ("inspect-after.json", {**inspect_after, "entries": []}),
        ("trigger-response.json", {**trigger, "fileCount": 0}),
        ("cleanup-response.json", {**fixture, "status": "staged"}),
    ):
        original = (tmp_path / name).read_text()
        (tmp_path / name).write_text(json.dumps(candidate))
        assert fault._hil_semantic_artifact_errors(
            "evict-after-unlinks-fail", tmp_path, result
        )
        (tmp_path / name).write_text(original)

    credential_forms = (
        "Authorization: Bearer opaque-value",
        "Proxy-Authorization=Basic opaque-value",
        "X-Mint-Secret: opaque-value",
        "token: opaque-value",
        "secret=opaque-value",
        "password: opaque-value",
        "passwd=opaque-value",
        "api_key: opaque-value",
        "api-key=opaque-value",
        "X-API-Key: opaque-value",
        "Cookie: session=opaque-value",
        "Set-Cookie: sid=opaque-value",
        "credential: opaque-value",
        '{"password":"opaque-value"}',
    )
    for credential in credential_forms:
        (tmp_path / "server.log").write_text(credential + "\n")
        assert fault._hil_artifact_credential_errors(tmp_path, ("server.log",))
    (tmp_path / "server.log").write_text(
        '{"sessionId":"lesson-session-1","credentialStatus":"absent"}\n'
    )
    assert fault._hil_artifact_credential_errors(tmp_path, ("server.log",)) == []


def test_fault_driver_power_artifacts_bind_post_reboot_inspection(tmp_path):
    cache_key = f"hil-task14/v1-{'d' * 64}"
    sibling = f"hil-task14/v2-{'d' * 64}"
    before = preservation_inspection(cache_key, sibling, "missing", "missing")
    after = preservation_inspection(cache_key, sibling, "full")
    for name, value in (
        ("inspect-before.json", before),
        ("inspect-after.json", after),
        ("post-reboot-inspect.json", after),
    ):
        (tmp_path / name).write_text(json.dumps(value))
    fixture = {
        "cacheKey": cache_key,
        "siblingCacheKey": sibling,
        "fixture": "preservation_set",
        "status": "staged",
        "changed": True,
    }
    (tmp_path / "stage-response.json").write_text(json.dumps(fixture))
    (tmp_path / "cleanup-response.json").write_text(
        json.dumps({**fixture, "status": "cleaned"})
    )
    result = {
        "cacheKey": cache_key,
        "triggerOutcome": None,
        "triggerResponseAbsent": True,
    }
    assert fault._hil_semantic_artifact_errors(
        fault.HIL_POWER_LOSS_SCENARIO, tmp_path, result
    ) == []
    (tmp_path / "post-reboot-inspect.json").write_text(
        json.dumps({**after, "entries": []})
    )
    assert fault._hil_semantic_artifact_errors(
        fault.HIL_POWER_LOSS_SCENARIO, tmp_path, result
    )


def test_fault_driver_report_persists_raw_cleanup_and_final_status(tmp_path, monkeypatch):
    cleanup = {"cacheKey": "hil-task14/v1-" + "d" * 64, "entries": ["raw"]}
    status = {"status": "consumed", "armed": False}
    result = {"cleanupInspection": cleanup, "finalStatus": status}
    (tmp_path / "result.json").write_text(json.dumps(result))
    monkeypatch.setattr(fault, "validate_hil_storage_result", lambda *_: [])
    monkeypatch.setattr(fault, "_hil_storage_artifact_errors", lambda *_: [])
    report = fault.build_hil_storage_report(
        "evict-after-unlinks-fail", tmp_path
    )
    assert report["cleanupInspection"] == cleanup
    assert report["finalStatus"] == status

def test_fault_driver_validates_hil_sequences_build_identity_and_power_loss():
    build = {
        "sourceCommit": "a" * 40,
        "profile": "hil",
        "configEnabled": True,
        "sdkconfigSha256": "b" * 64,
        "binarySha256": "c" * 64,
        "elfSha256": "d" * 64,
        "mapSha256": "e" * 64,
        "archiveSha256": "f" * 64,
        "binaryBytes": 1,
        "appPartitionFreeBytes": 1,
    }
    result = {
        "scenario": fault.HIL_STORAGE_SCENARIOS[-1],
        "status": "PASS",
        **HIL_CONNECTION,
        **HIL_RUNTIME,
        "buildIdentity": build,
        "cacheKey": f"hil-task14/v1-{'d' * 64}",
        "armSequence": 1,
        "reachedSequence": 2,
        "consumedSequence": 3,
        "events": list(fault.HIL_EVENT_ORDER),
        "operation": "sync",
        "checkpoint": "before_commit_rename",
        "faultAction": "pause",
        "expectedProgress": 0,
        "checkpointExercised": True,
        "triggerResponseAbsent": True,
        "triggerOutcome": None,
        "powerLoss": True,
        "checkpointReached": True,
        "successMarkerBeforeLoss": False,
        "rebootCaptured": True,
        "armClearedAfterReboot": True,
        "postRebootInspected": True,
        "retryStatus": "ready",
        "triggerPendingAtMarker": True,
        "triggerPendingAtCutBoundary": True,
        "utcStart": "2026-07-17T00:00:00Z",
        "checkpointReachedUtc": "2026-07-17T00:00:00.500000Z",
        "powerCutBoundaryUtc": "2026-07-17T00:00:01Z",
        "disconnectObservedUtc": "2026-07-17T00:00:01.500000Z",
        "powerRemovalConfirmedUtc": "2026-07-17T00:00:02Z",
        "utcEnd": "2026-07-17T00:00:03Z",
        "disconnectAfterPowerCutBoundary": True,
    }

    assert fault.validate_hil_storage_result(result["scenario"], result) == []
    assert fault.validate_hil_storage_result(
        result["scenario"], {**result, "reachedSequence": 1}
    )
    assert fault.validate_hil_storage_result(
        result["scenario"], {**result, "triggerResponseAbsent": False}
    )
    assert fault.validate_hil_storage_result(
        result["scenario"],
        {
            **result,
            "connectionIdentity": {
                **result["connectionIdentity"],
                "deviceId": "28:84:85:85:1a:81",
            },
        },
    )
@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("checkpointReachedUtc", None),
        ("checkpointReachedUtc", "not-a-timestamp"),
        ("checkpointReachedUtc", "2026-07-16T23:59:59Z"),
        ("powerCutBoundaryUtc", "2026-07-17T00:00:00.250000Z"),
        ("disconnectObservedUtc", "2026-07-17T00:00:00.750000Z"),
        ("powerRemovalConfirmedUtc", "2026-07-17T00:00:01.250000Z"),
        ("utcEnd", "2026-07-17T00:00:01.750000Z"),
    ),
)
def test_fault_driver_rejects_missing_malformed_or_out_of_order_power_timestamps(
    field, value
):
    result = {
        "scenario": fault.HIL_POWER_LOSS_SCENARIO,
        "status": "PASS",
        **HIL_CONNECTION,
        **HIL_RUNTIME,
        "buildIdentity": {
            "sourceCommit": "a" * 40,
            "profile": "hil",
            "configEnabled": True,
            "sdkconfigSha256": "b" * 64,
            "binarySha256": "c" * 64,
            "elfSha256": "d" * 64,
            "mapSha256": "e" * 64,
            "archiveSha256": "f" * 64,
            "binaryBytes": 1,
            "appPartitionFreeBytes": 1,
        },
        "cacheKey": f"hil-task14/v1-{'d' * 64}",
        "armSequence": 1,
        "reachedSequence": 2,
        "consumedSequence": 3,
        "events": list(fault.HIL_EVENT_ORDER),
        "operation": "sync",
        "checkpoint": "before_commit_rename",
        "faultAction": "pause",
        "expectedProgress": 0,
        "checkpointExercised": True,
        "triggerResponseAbsent": True,
        "triggerOutcome": None,
        "powerLoss": True,
        "checkpointReached": True,
        "successMarkerBeforeLoss": False,
        "rebootCaptured": True,
        "armClearedAfterReboot": True,
        "postRebootInspected": True,
        "retryStatus": "ready",
        "triggerPendingAtMarker": True,
        "triggerPendingAtCutBoundary": True,
        "utcStart": "2026-07-17T00:00:00Z",
        "checkpointReachedUtc": "2026-07-17T00:00:00.500000Z",
        "powerCutBoundaryUtc": "2026-07-17T00:00:01Z",
        "disconnectObservedUtc": "2026-07-17T00:00:01.500000Z",
        "powerRemovalConfirmedUtc": "2026-07-17T00:00:02Z",
        "utcEnd": "2026-07-17T00:00:03Z",
        "disconnectAfterPowerCutBoundary": True,
    }

    assert fault.validate_hil_storage_result(
        result["scenario"], {**result, field: value}
    )


def test_power_loss_timestamp_artifacts_bind_exactly_to_result(tmp_path):
    result = {
        "checkpointReachedUtc": "2026-07-17T00:00:00.500000Z",
        "disconnectObservedUtc": "2026-07-17T00:00:01.500000Z",
        "powerRemovalConfirmedUtc": "2026-07-17T00:00:02Z",
    }
    checkpoint = tmp_path / "checkpoint-reached-utc.txt"
    removed = tmp_path / "power-removed-utc.txt"
    checkpoint.write_text(result["checkpointReachedUtc"] + "\n")
    removed.write_text(result["powerRemovalConfirmedUtc"] + "\n")

    assert fault._power_loss_timestamp_artifact_errors(result, tmp_path) == []

    for path, value in (
        (checkpoint, "2026-07-17T00:00:01Z\n"),
        (removed, "2026-07-17T00:00:03Z\n"),
        (checkpoint, result["checkpointReachedUtc"] + " \n"),
        (removed, result["powerRemovalConfirmedUtc"] + "\n\n"),
    ):
        checkpoint.write_text(result["checkpointReachedUtc"] + "\n")
        removed.write_text(result["powerRemovalConfirmedUtc"] + "\n")
        path.write_text(value)
        assert fault._power_loss_timestamp_artifact_errors(result, tmp_path)

    checkpoint.unlink()
    assert fault._power_loss_timestamp_artifact_errors(result, tmp_path)


def test_power_loss_report_rejects_timestamp_tamper_with_recomputed_checksums(
    tmp_path, monkeypatch
):
    build = {
        "sourceCommit": "a" * 40,
        "profile": "hil",
        "configEnabled": True,
        "sdkconfigSha256": "b" * 64,
        "binarySha256": "c" * 64,
        "elfSha256": "d" * 64,
        "mapSha256": "e" * 64,
        "archiveSha256": "f" * 64,
        "binaryBytes": 1,
        "appPartitionFreeBytes": 1,
    }
    result = {
        "buildIdentity": build,
        "checkpointReachedUtc": "2026-07-17T00:00:00.500000Z",
        "disconnectObservedUtc": "2026-07-17T00:00:01.500000Z",
        "powerRemovalConfirmedUtc": "2026-07-17T00:00:02Z",
    }
    monkeypatch.setattr(fault, "validate_hil_storage_result", lambda *_: [])
    monkeypatch.setattr(fault, "_hil_control_artifact_errors", lambda *_: [])
    monkeypatch.setattr(fault, "_hil_semantic_artifact_errors", lambda *_: [])
    for name in fault.HIL_POWER_REQUIRED:
        if name != "SHA256SUMS":
            (tmp_path / name).write_text("x\n")
    (tmp_path / "result.json").write_text(json.dumps(result))
    (tmp_path / "build-manifest.json").write_text(json.dumps(build))
    build_digest = hashlib.sha256((tmp_path / "build-manifest.json").read_bytes()).hexdigest()
    (tmp_path / "build-manifest.sha256").write_text(
        f"{build_digest}  build-manifest.json\n"
    )
    (tmp_path / "validator-exit-code.txt").write_text("0\n")
    (tmp_path / "serial.log").write_text("HIL_STORAGE_CHECKPOINT_REACHED\n")
    (tmp_path / "checkpoint-reached-utc.txt").write_text(
        result["checkpointReachedUtc"] + "\n"
    )
    (tmp_path / "power-removed-utc.txt").write_text(
        result["powerRemovalConfirmedUtc"] + "\n"
    )

    def rewrite_checksums():
        rows = []
        for name in fault.HIL_POWER_REQUIRED:
            if name != "SHA256SUMS":
                digest = hashlib.sha256((tmp_path / name).read_bytes()).hexdigest()
                rows.append(f"{digest}  {name}\n")
        (tmp_path / "SHA256SUMS").write_text("".join(rows))

    rewrite_checksums()
    assert fault.build_hil_storage_report(fault.HIL_POWER_LOSS_SCENARIO, tmp_path)[
        "status"
    ] == "PASS"

    (tmp_path / "checkpoint-reached-utc.txt").write_text(
        "2026-07-17T00:00:01Z\n"
    )
    rewrite_checksums()
    report = fault.build_hil_storage_report(fault.HIL_POWER_LOSS_SCENARIO, tmp_path)
    assert report["status"] == "NOT_PASS"
    assert any("timestamp artifact mismatch" in error for error in report["validationErrors"])

    (tmp_path / "checkpoint-reached-utc.txt").write_bytes(b"\xff\xfe\n")
    rewrite_checksums()
    report = fault.build_hil_storage_report(fault.HIL_POWER_LOSS_SCENARIO, tmp_path)
    assert report["status"] == "NOT_PASS"
    assert any("timestamp artifact" in error for error in report["validationErrors"])


@pytest.mark.parametrize(
    ("earlier", "later"),
    (
        ("utcStart", "checkpointReachedUtc"),
        ("checkpointReachedUtc", "powerCutBoundaryUtc"),
        ("powerCutBoundaryUtc", "disconnectObservedUtc"),
        ("powerRemovalConfirmedUtc", "utcEnd"),
    ),
)
def test_fault_driver_rejects_equal_adjacent_power_timestamps(earlier, later):
    result = {
        "scenario": fault.HIL_POWER_LOSS_SCENARIO,
        "status": "PASS",
        **HIL_CONNECTION,
        **HIL_RUNTIME,
        "buildIdentity": {
            "sourceCommit": "a" * 40,
            "profile": "hil",
            "configEnabled": True,
            "sdkconfigSha256": "b" * 64,
            "binarySha256": "c" * 64,
            "elfSha256": "d" * 64,
            "mapSha256": "e" * 64,
            "archiveSha256": "f" * 64,
            "binaryBytes": 1,
            "appPartitionFreeBytes": 1,
        },
        "cacheKey": f"hil-task14/v1-{'d' * 64}",
        "armSequence": 1,
        "reachedSequence": 2,
        "consumedSequence": 3,
        "events": list(fault.HIL_EVENT_ORDER),
        "operation": "sync",
        "checkpoint": "before_commit_rename",
        "faultAction": "pause",
        "expectedProgress": 0,
        "checkpointExercised": True,
        "triggerResponseAbsent": True,
        "triggerOutcome": None,
        "powerLoss": True,
        "checkpointReached": True,
        "successMarkerBeforeLoss": False,
        "rebootCaptured": True,
        "armClearedAfterReboot": True,
        "postRebootInspected": True,
        "retryStatus": "ready",
        "triggerPendingAtMarker": True,
        "triggerPendingAtCutBoundary": True,
        "utcStart": "2026-07-17T00:00:00Z",
        "checkpointReachedUtc": "2026-07-17T00:00:00.500000Z",
        "powerCutBoundaryUtc": "2026-07-17T00:00:01Z",
        "disconnectObservedUtc": "2026-07-17T00:00:01.500000Z",
        "powerRemovalConfirmedUtc": "2026-07-17T00:00:02Z",
        "utcEnd": "2026-07-17T00:00:03Z",
        "disconnectAfterPowerCutBoundary": True,
    }
    candidate = {**result, later: result[earlier]}

    assert fault.validate_hil_storage_result(result["scenario"], candidate)


def test_fault_driver_allows_equal_disconnect_and_confirmation_utc():
    result = {
        "scenario": fault.HIL_POWER_LOSS_SCENARIO,
        "status": "PASS",
        **HIL_CONNECTION,
        **HIL_RUNTIME,
        "buildIdentity": {
            "sourceCommit": "a" * 40,
            "profile": "hil",
            "configEnabled": True,
            "sdkconfigSha256": "b" * 64,
            "binarySha256": "c" * 64,
            "elfSha256": "d" * 64,
            "mapSha256": "e" * 64,
            "archiveSha256": "f" * 64,
            "binaryBytes": 1,
            "appPartitionFreeBytes": 1,
        },
        "cacheKey": f"hil-task14/v1-{'d' * 64}",
        "armSequence": 1,
        "reachedSequence": 2,
        "consumedSequence": 3,
        "events": list(fault.HIL_EVENT_ORDER),
        "operation": "sync",
        "checkpoint": "before_commit_rename",
        "faultAction": "pause",
        "expectedProgress": 0,
        "checkpointExercised": True,
        "triggerResponseAbsent": True,
        "triggerOutcome": None,
        "powerLoss": True,
        "checkpointReached": True,
        "successMarkerBeforeLoss": False,
        "rebootCaptured": True,
        "armClearedAfterReboot": True,
        "postRebootInspected": True,
        "retryStatus": "ready",
        "triggerPendingAtMarker": True,
        "triggerPendingAtCutBoundary": True,
        "utcStart": "2026-07-17T00:00:00Z",
        "checkpointReachedUtc": "2026-07-17T00:00:00.500000Z",
        "powerCutBoundaryUtc": "2026-07-17T00:00:01Z",
        "disconnectObservedUtc": "2026-07-17T00:00:02Z",
        "powerRemovalConfirmedUtc": "2026-07-17T00:00:02Z",
        "utcEnd": "2026-07-17T00:00:03Z",
        "disconnectAfterPowerCutBoundary": True,
    }

    assert fault.validate_hil_storage_result(result["scenario"], result) == []


def test_fault_driver_rejects_false_green_hil_trigger_outcomes():
    build = {
        "sourceCommit": "a" * 40,
        "profile": "hil",
        "configEnabled": True,
        "sdkconfigSha256": "b" * 64,
        "binarySha256": "c" * 64,
        "elfSha256": "d" * 64,
        "mapSha256": "e" * 64,
        "archiveSha256": "f" * 64,
        "binaryBytes": 1,
        "appPartitionFreeBytes": 1,
    }
    base = {
        "scenario": "evict-before-first-unlink-fail",
        "status": "PASS",
        **HIL_CONNECTION,
        **HIL_RUNTIME,
        "buildIdentity": build,
        "cacheKey": f"hil-task14/v1-{'d' * 64}",
        "armSequence": 1,
        "reachedSequence": 2,
        "consumedSequence": 3,
        "events": list(fault.HIL_EVENT_ORDER),
        "operation": "evict",
        "checkpoint": "before_first_unlink",
        "faultAction": "fail",
        "checkpointExercised": True,
        "expectedProgress": 0,
        "triggerResponseAbsent": False,
        "triggerOutcome": {
            "cacheKey": f"hil-task14/v1-{'d' * 64}",
            "status": "unlink_failed",
            "evicted": False,
            "notFound": False,
            "fileCount": 0,
            "reason": "unlink_failed",
        },
    }
    assert fault.validate_hil_storage_result(base["scenario"], base) == []
    false_green = {**base, "triggerOutcome": {**base["triggerOutcome"], "status": "evicted", "reason": "evicted", "evicted": True, "fileCount": 1}}
    assert fault.validate_hil_storage_result(base["scenario"], false_green)


def test_fault_driver_self_test_needs_no_live_arguments(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["lesson_studio_task14_fault_driver.py", "--self-test"])

    assert fault.main() == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "PASS"
    assert report["hilPowerTimestampCases"] == 14
    assert report["hilTimestampArtifactCases"] == 7


def test_cold_eviction_validator_has_stable_typed_contract():
    assert fault._cold_eviction_errors.__annotations__ == {
        "result": dict[str, Any],
        "raw_logs": str,
        "return": list[str],
    }


def write_png(path: Path, width=480, height=320, color=(0, 0, 0)):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    rows = b"".join(b"\0" + bytes(color) * width for _ in range(height))
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


def authentic_soak_logs(session_sizes=(40, 40, 20), *, repeated_step_ids=False):
    server_lines = []
    serial_lines = []
    for session_index, step_count in enumerate(session_sizes, start=1):
        assignment = f"assignment-{session_index}"
        session = f"session-{session_index}"
        for step_index in range(1, step_count + 1):
            step_id = f"step-{step_index}" if repeated_step_ids else f"s{session_index}-{step_index}"
            sequence = step_index + 2
            server_lines.append(
                "[LessonRuntime]-INFO-emit lesson_step "
                f"stepId={step_id} stepType=model assignment_id={assignment} "
                f"session_id={session}"
            )
            serial_lines.extend([
                f"I (1) WS: ws text lesson frame type=lesson_step seq={sequence} bytes=100",
                (
                    "I (2) SystemInfo: heap_checkpoint phase=lesson_render.complete "
                    f"internal_free=30000 lifetime_min_internal=25000 "
                    f"phase_min_internal={24576 - (step_index % 3)} "
                    f"largest_internal=20000 psram_free={8_000_000 - step_index * 128}"
                ),
                (
                    "I (3) Lesson: lesson_step rendered "
                    f"stepId={step_id} passive=0 degraded=0 renderElapsedMs=12"
                ),
            ])
    return "\n".join(serial_lines), "\n".join(server_lines)


def authentic_soak_timeline(session_sizes=(40, 40, 20)):
    lines = []
    timestamp = 1
    for session_index, step_count in enumerate(session_sizes, start=1):
        assignment = f"assignment-{session_index}"
        session = f"session-{session_index}"
        lines.append(
            f"{timestamp} server [LessonRuntime]-INFO-emit lesson_prepare stepId= "
            f"assignment_id={assignment} session_id={session}"
        )
        timestamp += 1
        lines.append(
            f"{timestamp} serial I (1) WS: ws text lesson frame type=lesson_prepare seq=1 bytes=100"
        )
        timestamp += 1
        for step_index in range(1, step_count + 1):
            step_id = f"step-{step_index}"
            sequence = step_index + 2
            lines.extend([
                (
                    f"{timestamp} server [LessonRuntime]-INFO-emit lesson_step "
                    f"stepId={step_id} assignment_id={assignment} session_id={session}"
                ),
                (
                    f"{timestamp + 1} serial I (2) WS: ws text lesson frame "
                    f"type=lesson_step seq={sequence} bytes=100"
                ),
                (
                    f"{timestamp + 2} serial I (3) SystemInfo: heap_checkpoint "
                    "phase=lesson_render.complete internal_free=30000 "
                    "lifetime_min_internal=25000 phase_min_internal=24576 "
                    f"largest_internal=20000 psram_free={8_000_000 - step_index * 128}"
                ),
                (
                    f"{timestamp + 3} serial I (4) Lesson: lesson_step rendered "
                    f"stepId={step_id} passive=0 degraded=0 renderElapsedMs=12"
                ),
            ])
            timestamp += 4
    return "\n".join(lines)


def complete_result(tmp_path: Path) -> dict:
    preview = tmp_path / "preview.png"
    hardware = tmp_path / "hardware.png"
    write_png(preview, color=(1, 2, 3))
    write_png(hardware, color=(3, 2, 1))
    capture_script = tmp_path / "lesson_e2e_live_capture.py"
    verifier_script = tmp_path / "lesson_e2e_log_verify.py"
    capture_script.write_text("# deterministic capture fixture\n")
    verifier_script.write_text("# deterministic verifier fixture\n")
    result = {
        "scenario": "cold",
        "status": "PASS",
        "utcStart": "2026-07-12T00:00:00Z",
        "utcEnd": "2026-07-12T00:10:00Z",
        "backendCommit": "a" * 40,
        "espServerCommit": "b" * 40,
        "firmwareCommit": "c" * 40,
        "firmwareVersion": "2.2.37",
        "deviceId": "28:84:85:85:1a:80",
        "assignmentId": "assignment-1",
        "sessionId": "session-1",
        "assignmentVersion": 1,
        "assignmentBackendDeviceId": "14140000-0000-4000-8000-000000000004",
        "assignmentChildId": "14140000-0000-4000-8000-000000000003",
        "assignmentProfile": "espTft",
        "fixtureVersion": "2026-07-11.1",
        "courseId": "production-farm-english-358",
        "lessonId": "pip-farm-3m",
        "lessonVersion": 1,
        "manifestChecksum": "d" * 64,
        "packChecksum": "d" * 64,
        "cacheKey": "pip-farm-3m/v1-" + "d" * 64,
        "captureScriptSha256": hashlib.sha256(capture_script.read_bytes()).hexdigest(),
        "verifierScriptSha256": hashlib.sha256(verifier_script.read_bytes()).hexdigest(),
        "internalSramMin": 32768,
        "psramFirst": 8_000_000,
        "psramLast": 7_999_000,
        "screenshots": [
            {"role": "preview", "path": str(preview)},
            {"role": "hardware", "path": str(hardware)},
        ],
        "operator": "lab-operator",
        "commandExitCode": 0,
        "bytesDownloaded": 100,
        "elapsedMs": 1000,
        "ready": True,
        "checksumVerified": True,
        "evictionRequestedCacheKey": "pip-farm-3m/v1-" + "d" * 64,
        "evictionResult": {
            "cacheKey": "pip-farm-3m/v1-" + "d" * 64,
            "status": "evicted",
            "evicted": True,
            "notFound": False,
            "fileCount": 4,
            "reason": "evicted",
        },
        "evictionCompletedUtc": "2026-07-12T00:01:00Z",
        "coldCaptureStartedUtc": "2026-07-12T00:01:01Z",
        "assignmentCreatedUtc": "2026-07-12T00:01:02Z",
        "logMarkers": ["lesson_cache_evict", "lesson_preload_ready", "checksum_verified"],
    }
    write_cold_artifacts(tmp_path, result)
    return result


def write_cold_artifacts(tmp_path: Path, result: dict):
    response = tmp_path / "eviction-response.json"
    response.write_text(json.dumps({"data": result["evictionResult"]}, sort_keys=True) + "\n")
    response_hash = hashlib.sha256(response.read_bytes()).hexdigest()
    (tmp_path / "eviction-response.sha256").write_text(f"{response_hash}  {response}\n")
    (tmp_path / "utc-start.txt").write_text(result["utcStart"] + "\n")
    (tmp_path / "eviction-completed-utc.txt").write_text(
        result["evictionCompletedUtc"] + "\n"
    )
    (tmp_path / "cold-capture-started-utc.txt").write_text(
        result["coldCaptureStartedUtc"] + "\n"
    )
    assignment = tmp_path / "assignment-create-response.json"
    assignment.write_text(
        json.dumps(
            {
                "data": {
                    "assignment": {
                        "assignmentId": result["assignmentId"],
                        "assignmentVersion": result["assignmentVersion"],
                        "deviceId": result["assignmentBackendDeviceId"],
                        "childId": result["assignmentChildId"],
                        "lessonId": result["lessonId"],
                        "lessonTitle": "Pip Farm 3m",
                        "lessonVersion": result["lessonVersion"],
                        "manifestChecksum": result["manifestChecksum"],
                        "profile": result["assignmentProfile"],
                        "state": "ASSIGNED",
                        "createdAt": result["assignmentCreatedUtc"],
                    }
                }
            },
            sort_keys=True,
        )
        + "\n"
    )
    assignment_hash = hashlib.sha256(assignment.read_bytes()).hexdigest()
    (tmp_path / "assignment-create-response.sha256").write_text(
        f"{assignment_hash}  {assignment}\n"
    )


def helper_script_paths(tmp_path: Path):
    return (
        tmp_path / "lesson_e2e_live_capture.py",
        tmp_path / "lesson_e2e_log_verify.py",
    )


def cold_raw_evidence(result):
    return "\n".join([
        (
            "lesson_cache_evict cache_key={cacheKey} code={status} file_count={fileCount}"
        ).format(**result["evictionResult"]),
        (
            "assignment/current active assignmentId={assignmentId} lessonId={lessonId} "
            "deviceId={deviceId}"
        ).format(**result),
        (
            "lesson_preload_ready cacheKey={cacheKey} assetCount=2 downloadedCount=2 "
            "skippedCount=0 failedCount=0 durationMs={elapsedMs} "
            "assignment_id={assignmentId} session_id={sessionId}"
        ).format(**result),
        (
            "checksum_verified cacheKey={cacheKey} manifestChecksum={manifestChecksum} "
            "assetCount=2 assignment_id={assignmentId} session_id={sessionId}"
        ).format(**result),
    ])


def test_cold_requires_complete_exact_eviction_attestation(tmp_path):
    required = (
        "evictionRequestedCacheKey",
        "evictionResult",
        "evictionCompletedUtc",
        "coldCaptureStartedUtc",
        "assignmentCreatedUtc",
        "assignmentBackendDeviceId",
        "assignmentChildId",
        "assignmentProfile",
    )

    for field in required:
        result = complete_result(tmp_path)
        del result[field]
        errors = fault.validate_result("cold", result, cold_raw_evidence(complete_result(tmp_path)))
        assert f"cold eviction evidence missing: {field}" in errors


def test_cold_accepts_coherent_evicted_and_not_found_results(tmp_path):
    evicted = complete_result(tmp_path)
    assert fault.validate_result("cold", evicted, cold_raw_evidence(evicted)) == []

    not_found = complete_result(tmp_path)
    not_found["evictionResult"] = {
        "cacheKey": not_found["cacheKey"],
        "status": "not_found",
        "evicted": False,
        "notFound": True,
        "fileCount": 0,
        "reason": "not_found",
    }
    write_cold_artifacts(tmp_path, not_found)
    assert fault.validate_result("cold", not_found, cold_raw_evidence(not_found)) == []


@pytest.mark.parametrize(
    "artifact",
    [
        "eviction-response.json",
        "eviction-response.sha256",
        "utc-start.txt",
        "eviction-completed-utc.txt",
        "cold-capture-started-utc.txt",
        "assignment-create-response.json",
        "assignment-create-response.sha256",
    ],
)
def test_cold_requires_each_bound_artifact(tmp_path, artifact):
    result = complete_result(tmp_path)
    (tmp_path / artifact).unlink()

    errors = fault.validate_result(
        "cold", result, cold_raw_evidence(result), base_dir=tmp_path
    )

    assert f"cold evidence artifact missing: {artifact}" in errors


def test_cold_rejects_tampered_eviction_response_and_checksum_artifact(tmp_path):
    result = complete_result(tmp_path)
    response = tmp_path / "eviction-response.json"
    response.write_text('{"data":{"cacheKey":"private-foreign"}}\n')

    errors = fault.validate_result(
        "cold", result, cold_raw_evidence(result), base_dir=tmp_path
    )

    assert "cold eviction response does not exactly match result" in errors
    assert "cold eviction response checksum does not match artifact" in errors


def test_cold_rejects_checksum_bound_to_foreign_same_named_path(tmp_path):
    result = complete_result(tmp_path)
    response = tmp_path / "eviction-response.json"
    response_hash = hashlib.sha256(response.read_bytes()).hexdigest()
    (tmp_path / "eviction-response.sha256").write_text(
        f"{response_hash}  /tmp/foreign/eviction-response.json\n"
    )

    errors = fault.validate_result(
        "cold", result, cold_raw_evidence(result), base_dir=tmp_path
    )

    assert "cold eviction response checksum does not match artifact" in errors


def test_cold_rejects_declared_timestamp_mismatch_with_artifacts(tmp_path):
    result = complete_result(tmp_path)
    (tmp_path / "eviction-completed-utc.txt").write_text("2026-07-12T00:01:00.123456Z\n")

    errors = fault.validate_result(
        "cold", result, cold_raw_evidence(result), base_dir=tmp_path
    )

    assert "cold artifact timestamps do not match result" in errors


@pytest.mark.parametrize(
    ("field", "foreign"),
    [
        ("assignmentId", "stale-assignment-id"),
        ("createdAt", "2026-07-12T00:00:30.000000Z"),
    ],
)
def test_cold_rejects_forged_or_stale_assignment_creation_artifact(
    tmp_path, field, foreign
):
    result = complete_result(tmp_path)
    assignment = tmp_path / "assignment-create-response.json"
    payload = json.loads(assignment.read_text())
    payload["data"]["assignment"][field] = foreign
    assignment.write_text(json.dumps(payload, sort_keys=True) + "\n")
    assignment_hash = hashlib.sha256(assignment.read_bytes()).hexdigest()
    (tmp_path / "assignment-create-response.sha256").write_text(
        f"{assignment_hash}  {assignment}\n"
    )

    errors = fault.validate_result(
        "cold", result, cold_raw_evidence(result), base_dir=tmp_path
    )

    assert "cold assignment creation response does not match result" in errors


@pytest.mark.parametrize(
    ("field", "foreign"),
    [
        ("state", "COMPLETED"),
        ("profile", "mobile"),
        ("lessonId", "foreign-lesson"),
        ("lessonVersion", 2),
        ("manifestChecksum", "e" * 64),
        ("assignmentVersion", 2),
        ("childId", "14140000-0000-4000-8000-000000000099"),
        ("deviceId", "14140000-0000-4000-8000-000000000098"),
    ],
)
def test_cold_rejects_semantically_mismatched_assignment_creation(
    tmp_path, field, foreign
):
    result = complete_result(tmp_path)
    assignment = tmp_path / "assignment-create-response.json"
    payload = json.loads(assignment.read_text())
    payload["data"]["assignment"][field] = foreign
    assignment.write_text(json.dumps(payload, sort_keys=True) + "\n")
    assignment_hash = hashlib.sha256(assignment.read_bytes()).hexdigest()
    (tmp_path / "assignment-create-response.sha256").write_text(
        f"{assignment_hash}  {assignment}\n"
    )

    report = fault.build_evidence_report(
        "cold", result, {}, cold_raw_evidence(result), base_dir=tmp_path
    )

    assert "cold assignment creation response does not match result" in report[
        "validationErrors"
    ]
    assert "assignment-create-response.json" not in report["files"]
    assert "assignment-create-response.sha256" not in report["files"]


def test_cold_rejects_assignment_checksum_bound_to_foreign_path(tmp_path):
    result = complete_result(tmp_path)
    assignment = tmp_path / "assignment-create-response.json"
    assignment_hash = hashlib.sha256(assignment.read_bytes()).hexdigest()
    (tmp_path / "assignment-create-response.sha256").write_text(
        f"{assignment_hash}  /tmp/foreign/assignment-create-response.json\n"
    )

    errors = fault.validate_result(
        "cold", result, cold_raw_evidence(result), base_dir=tmp_path
    )

    assert "cold assignment creation checksum does not match artifact" in errors


@pytest.mark.parametrize(
    "credential_mutation",
    [
        {"metadata": {"authorization": "private"}},
        {"metadata": {"token": "private"}},
        {"metadata": {"accessToken": "private"}},
        {"metadata": {"refreshToken": "private"}},
        {"lessonTitle": "Bearer private-secret"},
        {"lessonTitle": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJwcml2YXRlIn0.signature123"},
    ],
)
def test_cold_rejects_credential_bearing_assignment_artifact(
    tmp_path, credential_mutation
):
    result = complete_result(tmp_path)
    assignment = tmp_path / "assignment-create-response.json"
    payload = json.loads(assignment.read_text())
    payload["data"]["assignment"].update(credential_mutation)
    assignment.write_text(json.dumps(payload, sort_keys=True) + "\n")
    assignment_hash = hashlib.sha256(assignment.read_bytes()).hexdigest()
    (tmp_path / "assignment-create-response.sha256").write_text(
        f"{assignment_hash}  {assignment}\n"
    )

    report = fault.build_evidence_report(
        "cold", result, {}, cold_raw_evidence(result), base_dir=tmp_path
    )

    assert report["status"] == "NOT_PASS"
    assert "cold assignment response contains forbidden credential material" in report[
        "validationErrors"
    ]
    assert "assignment-create-response.json" not in report["files"]
    assert "assignment-create-response.sha256" not in report["files"]


@pytest.mark.parametrize(
    "raw",
    [
        "not-json Authorization: Bearer private-secret\n",
        "not-json eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJwcml2YXRlIn0.signature123\n",
    ],
)
def test_cold_rejects_raw_malformed_credential_assignment_artifact(tmp_path, raw):
    result = complete_result(tmp_path)
    assignment = tmp_path / "assignment-create-response.json"
    assignment.write_text(raw)
    assignment_hash = hashlib.sha256(assignment.read_bytes()).hexdigest()
    (tmp_path / "assignment-create-response.sha256").write_text(
        f"{assignment_hash}  {assignment}\n"
    )

    report = fault.build_evidence_report(
        "cold", result, {}, cold_raw_evidence(result), base_dir=tmp_path
    )

    assert report["status"] == "NOT_PASS"
    assert "cold assignment response contains forbidden credential material" in report[
        "validationErrors"
    ]
    assert "private-secret" not in json.dumps(report)
    assert "eyJhbGci" not in json.dumps(report)
    assert "assignment-create-response.json" not in report["files"]
    assert "assignment-create-response.sha256" not in report["files"]


def test_cold_omits_malformed_assignment_artifacts_even_without_credentials(tmp_path):
    result = complete_result(tmp_path)
    assignment = tmp_path / "assignment-create-response.json"
    assignment.write_text("not-json but no credentials\n")
    assignment_hash = hashlib.sha256(assignment.read_bytes()).hexdigest()
    (tmp_path / "assignment-create-response.sha256").write_text(
        f"{assignment_hash}  {assignment}\n"
    )

    report = fault.build_evidence_report(
        "cold", result, {}, cold_raw_evidence(result), base_dir=tmp_path
    )

    assert report["status"] == "NOT_PASS"
    assert "cold assignment creation response does not match result" in report[
        "validationErrors"
    ]
    assert "assignment-create-response.json" not in report["files"]
    assert "assignment-create-response.sha256" not in report["files"]


def test_cold_evidence_report_hashes_all_cold_artifacts(tmp_path):
    result = complete_result(tmp_path)

    report = fault.build_evidence_report(
        "cold", result, {}, cold_raw_evidence(result), base_dir=tmp_path
    )

    assert {
        "eviction-response.json",
        "eviction-response.sha256",
        "utc-start.txt",
        "eviction-completed-utc.txt",
        "cold-capture-started-utc.txt",
        "assignment-create-response.json",
        "assignment-create-response.sha256",
    } <= set(report["files"])


def test_cold_rejects_requested_result_and_capture_cache_key_mismatch(tmp_path):
    for mutation in ("requested", "result", "capture"):
        result = complete_result(tmp_path)
        foreign = "pip-farm-5m/v2-" + "e" * 64
        if mutation == "requested":
            result["evictionRequestedCacheKey"] = foreign
        elif mutation == "result":
            result["evictionResult"]["cacheKey"] = foreign
        else:
            result["cacheKey"] = foreign
        errors = fault.validate_result("cold", result, cold_raw_evidence(result))
        assert "cold eviction cache keys must match exactly" in errors


@pytest.mark.parametrize(
    "updates",
    [
        {"status": "lesson_runtime_active", "reason": "lesson_runtime_active"},
        {"evicted": False},
        {"notFound": True},
        {"reason": "private-error"},
        {"fileCount": -1},
        {"fileCount": True},
        {"status": "not_found", "evicted": False, "notFound": True, "fileCount": 1, "reason": "not_found"},
    ],
)
def test_cold_rejects_refusal_malformed_or_contradictory_eviction_result(tmp_path, updates):
    result = complete_result(tmp_path)
    result["evictionResult"].update(updates)

    errors = fault.validate_result("cold", result, cold_raw_evidence(result))

    assert "cold eviction result is not coherent" in errors


def test_cold_rejects_coherent_partial_eviction_as_cold_evidence(tmp_path):
    result = complete_result(tmp_path)
    result["evictionResult"] = {
        "cacheKey": result["cacheKey"],
        "status": "partial_evict_recovery_required",
        "evicted": False,
        "notFound": False,
        "fileCount": 2,
        "reason": "partial_evict_recovery_required",
    }

    errors = fault.validate_result("cold", result, cold_raw_evidence(result))

    assert "cold partial eviction requires attended retry or repair" in errors
    assert "cold eviction result is not coherent" in errors


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("evictionCompletedUtc", "2026-07-11T23:59:59Z"),
        ("coldCaptureStartedUtc", "2026-07-12T00:01:00Z"),
        ("assignmentCreatedUtc", "2026-07-12T00:01:01Z"),
        ("assignmentCreatedUtc", "2026-07-12T00:10:00Z"),
        ("evictionCompletedUtc", "2026-07-12T07:01:00+07:00"),
        ("coldCaptureStartedUtc", "not-a-time"),
    ],
)
def test_cold_rejects_timestamps_outside_strict_utc_interval_or_order(tmp_path, field, value):
    result = complete_result(tmp_path)
    result[field] = value

    errors = fault.validate_result("cold", result, cold_raw_evidence(result))

    assert "cold eviction timestamps must be strict UTC and correctly ordered" in errors


def test_cold_rejects_foreign_eviction_log_marker(tmp_path):
    result = complete_result(tmp_path)
    raw_logs = cold_raw_evidence(result).replace(result["cacheKey"], "pip-farm-8m/v9-" + "f" * 64, 1)

    errors = fault.validate_result("cold", result, raw_logs)

    assert "cold eviction log marker does not match result" in errors


def test_cold_rejects_forged_camel_case_eviction_marker(tmp_path):
    result = complete_result(tmp_path)
    raw_logs = cold_raw_evidence(result).replace(
        "cache_key=", "cacheKey=", 1
    ).replace("code=", "status=", 1).replace("file_count=", "fileCount=", 1)

    errors = fault.validate_result("cold", result, raw_logs)

    assert "cold eviction log marker does not match result" in errors


@pytest.mark.parametrize(
    "forged_token",
    [
        "not_lesson_cache_evict",
        "prefixlesson_cache_evict",
        "lesson_cache_evict_suffix",
    ],
)
def test_cold_rejects_embedded_or_prefixed_eviction_marker_token(tmp_path, forged_token):
    result = complete_result(tmp_path)
    raw_logs = cold_raw_evidence(result).replace("lesson_cache_evict", forged_token, 1)

    errors = fault.validate_result("cold", result, raw_logs)

    assert "cold eviction log marker does not match result" in errors


def test_cold_runbook_records_strict_fractional_utc_and_maps_result_fields():
    runbook = (ROOT / "docs" / "lesson-studio-task14-live-matrix.md").read_text()

    assert "datetime.now(timezone.utc)" in runbook
    assert 'timespec="microseconds"' in runbook
    assert "timedelta(microseconds=1)" in runbook
    assert "record_strict_utc" in runbook
    assert "eviction-completed-utc.txt" in runbook
    assert "cold-capture-started-utc.txt" in runbook
    assert "evictionCompletedUtc" in runbook
    assert "coldCaptureStartedUtc" in runbook


def test_cold_runbook_fails_closed_unless_mint_secret_is_exported():
    runbook = (ROOT / "docs" / "lesson-studio-task14-live-matrix.md").read_text()

    assert 'os.environ.get("TBOT_DEVICE_MINT_SECRET")' in runbook
    assert 'X-Mint-Secret: ${TBOT_DEVICE_MINT_SECRET}' in runbook
    assert "must be exported" in runbook


def test_cold_runbook_records_authoritative_assignment_creation_response():
    runbook = (ROOT / "docs" / "lesson-studio-task14-live-matrix.md").read_text()

    assert 'os.environ.get("TBOT_PARENT_JWT")' in runbook
    assert 'Authorization: Bearer ${TBOT_PARENT_JWT}' in runbook
    assert '"$BACKEND_URL/devices/${BACKEND_DEVICE_ID}/assignments"' in runbook
    assert '\\"childId\\":\\"${CHILD_ID}\\"' in runbook
    assert '\\"lessonId\\":\\"${LESSON_ID}\\"' in runbook
    assert 'tee "$EVIDENCE_ROOT/cold/assignment-create-response.json"' in runbook
    assert '.data.assignment.assignmentId' in runbook
    assert '.data.assignment.createdAt' in runbook
    assert '.data.assignment.state == "ASSIGNED"' in runbook
    assert '.data.assignment.profile == "espTft"' in runbook
    assert '.data.assignment.deviceId == $deviceId' in runbook
    assert '.data.assignment.childId == $childId' in runbook
    assert '.data.assignment.lessonId == $lessonId' in runbook
    assert '.data.assignment.lessonVersion == $lessonVersion' in runbook
    assert '.data.assignment.manifestChecksum == $manifestChecksum' in runbook
    assert "assignmentBackendDeviceId" in runbook
    assert "assignmentChildId" in runbook
    assert "assignmentProfile" in runbook
    assert 'assignment-create-response.sha256' in runbook
    cold_started = runbook.index("cold-capture-started-utc.txt")
    assignment_post = runbook.index(
        '"$BACKEND_URL/devices/${BACKEND_DEVICE_ID}/assignments"'
    )
    lesson_wait = runbook.index('wait "$CAPTURE_PID" # explicit wait/stop before validation')
    assert cold_started < assignment_post < lesson_wait


def test_cold_runbook_stops_partial_eviction_before_fresh_assignment():
    runbook = (ROOT / "docs" / "lesson-studio-task14-live-matrix.md").read_text()

    partial = runbook.index("partial_evict_recovery_required")
    retry = runbook.index("retry or repair the exact cache key")
    assignment_post = runbook.index(
        '"$BACKEND_URL/devices/${BACKEND_DEVICE_ID}/assignments"'
    )

    assert partial < retry < assignment_post
    assert "LESSON_CACHE_MAINTENANCE_REQUIRED" in runbook
    assert "Only `evicted` or `not_found`" in runbook


def test_cold_runbook_starts_and_verifies_both_streams_before_eviction():
    runbook = (ROOT / "docs" / "lesson-studio-task14-live-matrix.md").read_text()

    secret_check = runbook.index('os.environ.get("TBOT_DEVICE_MINT_SECRET")')
    start_marker = runbook.index("# BEGIN TASK14_COLD_CAPTURE_START")
    end_marker = runbook.index("# END TASK14_COLD_CAPTURE_START")
    capture_block = runbook[start_marker:end_marker]
    assert "--duration 240" in capture_block
    assert 'python3 - "$@" > "$capture_log" 2>&1 <<\'PY\' &' in capture_block
    assert "CAPTURE_PID=$!" in capture_block
    assert "os.setsid()" in capture_block
    assert "CAPTURE_PGID=$CAPTURE_PID" in capture_block
    assert "scripts/lesson_e2e_live_capture.py" in capture_block
    capture_start = runbook.index(
        'launch_capture_session \\\n  "$EVIDENCE_ROOT/cold/capture-driver.log"',
        start_marker,
    )
    server_ready = runbook.index('test -f "$EVIDENCE_ROOT/cold/capture/esp-server.log"')
    serial_ready = runbook.index('test -f "$EVIDENCE_ROOT/cold/capture/firmware-serial.log"')
    eviction = runbook.index("curl --fail-with-body --silent --show-error")
    assert secret_check < capture_start < server_ready < eviction
    assert capture_start < serial_ready < eviction
    assert runbook.index('kill -0 "$CAPTURE_PID"') < eviction
    assert runbook.index('pgrep -P "$CAPTURE_PID"') < eviction
    assert runbook.index('lsof "$SERIAL_PORT"') < eviction


def _documented_cleanup_block():
    runbook = (ROOT / "docs" / "lesson-studio-task14-live-matrix.md").read_text()
    start = runbook.index("# BEGIN TASK14_CAPTURE_CLEANUP")
    end = runbook.index("# END TASK14_CAPTURE_CLEANUP")
    return runbook[start:end]


def _documented_session_helper_block():
    runbook = (ROOT / "docs" / "lesson-studio-task14-live-matrix.md").read_text()
    start = runbook.index("# BEGIN TASK14_CAPTURE_SESSION_HELPERS")
    end = runbook.index("# END TASK14_CAPTURE_SESSION_HELPERS")
    return runbook[start:end]


def _run_interactive_pty(shell, script, *, ready_path, signal_number, timeout=8):
    pid, fd = pty.fork()
    if pid == 0:
        os.execv(shell, [shell, "-i", "-c", script])
    output = bytearray()
    deadline = time.monotonic() + timeout
    status = None
    signal_sent = False
    try:
        while time.monotonic() < deadline:
            if not signal_sent and ready_path.is_file():
                os.kill(pid, signal_number)
                signal_sent = True
            waited, child_status = os.waitpid(pid, os.WNOHANG)
            if waited == pid:
                status = child_status
                break
            readable, _, _ = select.select([fd], [], [], 0.05)
            if readable:
                try:
                    output.extend(os.read(fd, 65536))
                except OSError:
                    pass
        if status is None:
            os.kill(pid, 9)
            _, status = os.waitpid(pid, 0)
            raise AssertionError(f"interactive shell timed out: {output.decode(errors='replace')}")
    finally:
        os.close(fd)
    return os.waitstatus_to_exitcode(status), output.decode(errors="replace")


@pytest.mark.parametrize("shell", [path for path in (shutil.which("bash"), shutil.which("zsh")) if path])
@pytest.mark.parametrize(("signal_name", "exit_code"), [("INT", 130), ("TERM", 143)])
def test_documented_session_launch_works_with_interactive_job_control(
    tmp_path, shell, signal_name, exit_code
):
    lifecycle = tmp_path / "lifecycle.sh"
    lifecycle.write_text(_documented_cleanup_block() + _documented_session_helper_block())
    capture_script = tmp_path / "capture_like.py"
    child_file = tmp_path / "capture-child.pid"
    parent_file = tmp_path / "capture-parent.pid"
    capture_script.write_text(
        "import signal, subprocess, sys, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "child=subprocess.Popen(['sh','-c','trap \\\"\\\" TERM; echo $$ > \\\"$1\\\"; while :; do sleep 30; done','sh',sys.argv[1]])\n"
        "while True: time.sleep(1)\n"
    )
    script = f'''
source "{lifecycle}"
case $- in *m*) ;; *) exit 91;; esac
trap 'cleanup_capture $?' EXIT
trap 'cleanup_capture 130' INT
trap 'cleanup_capture 143' TERM
launch_capture_session "{tmp_path / 'capture.log'}" "{capture_script}" "{child_file}"
case $- in *m*) ;; *) exit 92;; esac
echo "$CAPTURE_PID" > "{parent_file}"
for _ in $(seq 1 100); do test -s "{child_file}" && break; sleep 0.02; done
test -s "{child_file}"
test "$(ps -o pgid= -p "$CAPTURE_PID" | tr -d ' ')" = "$CAPTURE_PGID"
touch "{tmp_path / 'shell-ready'}"
while :; do sleep 1; done
'''
    unrelated = subprocess.Popen(["sleep", "30"])
    try:
        return_code, output = _run_interactive_pty(
            shell,
            script,
            ready_path=tmp_path / "shell-ready",
            signal_number=getattr(signal, f"SIG{signal_name}"),
        )
        assert return_code == exit_code, output
        assert unrelated.poll() is None
    finally:
        if parent_file.is_file():
            try:
                os.killpg(int(parent_file.read_text()), signal.SIGKILL)
            except (ProcessLookupError, ValueError):
                pass
        unrelated.terminate()
        unrelated.wait(timeout=3)
    for path in (parent_file, child_file):
        pid = int(path.read_text())
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)


def test_documented_capture_block_is_valid_bash():
    runbook = (ROOT / "docs" / "lesson-studio-task14-live-matrix.md").read_text()
    start = runbook.index("# BEGIN TASK14_COLD_CAPTURE_START")
    end = runbook.index("# END TASK14_COLD_CAPTURE_START")

    completed = subprocess.run(
        ["bash", "-n"],
        input=runbook[start:end],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize(("signal_name", "exit_code"), [("INT", 130), ("TERM", 143)])
def test_documented_cleanup_is_bounded_and_reaps_real_process_tree(
    tmp_path, signal_name, exit_code
):
    parent_file = tmp_path / "parent.pid"
    child_file = tmp_path / "child.pid"
    script = _documented_cleanup_block() + _documented_session_helper_block() + r'''
python3 - "$CHILD_FILE" <<'PY' &
import os
import sys

os.setsid()
os.execvp(
    "sh",
    ["sh", "-c", 'trap "" TERM; while :; do sleep 30 & echo $! > "$1"; wait; done', "sh", sys.argv[1]],
)
PY
CAPTURE_PID=$!
CAPTURE_PGID=$CAPTURE_PID
echo "$CAPTURE_PID" > "$PARENT_FILE"
trap 'cleanup_capture $?' EXIT
trap 'cleanup_capture 130' INT
trap 'cleanup_capture 143' TERM
for _ in $(seq 1 50); do
  test -s "$CHILD_FILE" && break
  sleep 0.02
done
test -s "$CHILD_FILE"
kill -"$SIGNAL_NAME" $$
'''
    started = time.monotonic()
    unrelated = subprocess.Popen(["sleep", "30"])
    try:
        completed = subprocess.run(
            ["bash", "-c", script],
            env={
                **os.environ,
                "PARENT_FILE": str(parent_file),
                "CHILD_FILE": str(child_file),
                "SIGNAL_NAME": signal_name,
            },
            check=False,
            timeout=8,
        )
        assert unrelated.poll() is None
    finally:
        unrelated.terminate()
        unrelated.wait(timeout=3)

    assert completed.returncode == exit_code
    assert time.monotonic() - started < 5
    for path in (parent_file, child_file):
        pid = int(path.read_text())
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)


def test_documented_cleanup_targets_only_capture_process_group():
    cleanup = _documented_cleanup_block()

    assert "pgrep -P" not in cleanup
    assert "capture_tracked" not in cleanup
    assert 'kill -TERM -- "-$CAPTURE_PGID"' in cleanup
    assert 'kill -KILL -- "-$CAPTURE_PGID"' in cleanup


def test_cold_runbook_traps_and_explicitly_stops_capture_before_validation():
    runbook = (ROOT / "docs" / "lesson-studio-task14-live-matrix.md").read_text()

    start_marker = runbook.index("# BEGIN TASK14_COLD_CAPTURE_START")
    capture_start = runbook.index(
        'launch_capture_session \\\n  "$EVIDENCE_ROOT/cold/capture-driver.log"',
        start_marker,
    )
    trap = runbook.index("trap 'cleanup_capture $?' EXIT")
    eviction = runbook.index("curl --fail-with-body --silent --show-error")
    explicit_stop = runbook.index('wait "$CAPTURE_PID" # explicit wait/stop before validation')
    validator = runbook.rindex("python3 scripts/lesson_studio_task14_fault_driver.py cold")
    assert trap < capture_start < eviction
    assert eviction < explicit_stop < validator
    assert 'wait "$CAPTURE_PID"' in runbook
    assert "trap 'cleanup_capture 130' INT" in runbook
    assert "trap 'cleanup_capture 143' TERM" in runbook


def test_cold_still_rejects_zero_download_and_missing_checksum_attestation(tmp_path):
    result = complete_result(tmp_path)
    result["bytesDownloaded"] = 0
    result["checksumVerified"] = False

    errors = fault.validate_result("cold", result, cold_raw_evidence(result))

    assert "cold decisive signals are incomplete" in errors


def warm_raw_evidence(result):
    return "\n".join([
        (
            "assignment/current active assignmentId={assignmentId} lessonId={lessonId} "
            "deviceId={deviceId}"
        ).format(**result),
        (
            "asset_cache_hit cacheKey={cacheKey} assetCount=2 downloadedCount=0 "
            "skippedCount=2 failedCount=0 durationMs={elapsedMs} "
            "assignment_id={assignmentId} session_id={sessionId}"
        ).format(**result),
    ])


def checksum_raw_evidence(result):
    return "\n".join([
        (
            "assignment/current active assignmentId={assignmentId} lessonId={lessonId} "
            "deviceId={deviceId}"
        ).format(**result),
        (
            "checksum_mismatch cacheKey={cacheKey} manifestChecksum={manifestChecksum} "
            "mismatchDetected=true partialCleaned=true ready=false "
            "assignment_id={assignmentId} session_id={sessionId}"
        ).format(**result),
        (
            "partial_cleaned cacheKey={cacheKey} manifestChecksum={manifestChecksum} "
            "assignment_id={assignmentId} session_id={sessionId}"
        ).format(**result),
    ])


def test_common_metadata_requires_real_screenshots_and_release_fields(tmp_path):
    result = complete_result(tmp_path)
    assert fault.validate_result("cold", result, cold_raw_evidence(result)) == []
    result["screenshots"] = [{"role": "hardware", "path": str(tmp_path / "missing.png")}]
    errors = fault.validate_result("cold", result, "lesson_preload_ready checksum_verified")
    assert "screenshots must reference non-empty regular files" in errors


def test_all_scenarios_reject_foreign_fixture_course_and_lesson(tmp_path):
    foreign_values = {
        "fixtureVersion": "2026-07-11.2",
        "courseId": "foreign-course",
        "lessonId": "foreign-lesson",
    }
    expected_errors = {
        "fixtureVersion": "fixtureVersion must equal 2026-07-11.1",
        "courseId": "courseId must equal production-farm-english-358",
        "lessonId": "lessonId is not an approved Task 14 fixture lesson",
    }

    for scenario in fault.SCENARIOS:
        for field, foreign_value in foreign_values.items():
            result = complete_result(tmp_path)
            result["scenario"] = scenario
            result[field] = foreign_value
            errors = fault.validate_result(scenario, result, "")
            assert expected_errors[field] in errors, (scenario, field, errors)


def test_helper_script_hashes_are_computed_from_explicit_paths(tmp_path):
    result = complete_result(tmp_path)
    capture_script, verifier_script = helper_script_paths(tmp_path)

    assert fault.validate_result(
        "cold",
        result,
        cold_raw_evidence(result),
        capture_script=capture_script,
        verifier_script=verifier_script,
    ) == []

    result["captureScriptSha256"] = "0" * 64
    errors = fault.validate_result(
        "cold",
        result,
        cold_raw_evidence(result),
        capture_script=capture_script,
        verifier_script=verifier_script,
    )
    assert "captureScriptSha256 does not match --capture-script" in errors

    result = complete_result(tmp_path)
    verifier_script.write_text("# changed after operator recorded the hash\n")
    errors = fault.validate_result(
        "cold",
        result,
        cold_raw_evidence(result),
        capture_script=capture_script,
        verifier_script=verifier_script,
    )
    assert "verifierScriptSha256 does not match --verifier-script" in errors


def test_helper_script_hash_validation_fails_closed_for_missing_paths(tmp_path):
    result = complete_result(tmp_path)
    capture_script, verifier_script = helper_script_paths(tmp_path)
    capture_script.unlink()

    errors = fault.validate_result(
        "cold",
        result,
        cold_raw_evidence(result),
        capture_script=capture_script,
        verifier_script=verifier_script,
    )

    assert "cannot hash --capture-script" in errors


def test_soak_attestation_rejects_foreign_fixture_course_and_lesson(tmp_path):
    complete_result(tmp_path)
    capture_script, verifier_script = helper_script_paths(tmp_path)
    expected = {
        "fixtureVersion": "2026-07-11.1",
        "courseId": "production-farm-english-358",
        "lessonId": "pip-farm-5m",
    }

    metadata, errors = soak.validate_live_attestation(
        **expected,
        capture_script=capture_script,
        verifier_script=verifier_script,
    )
    assert errors == []
    assert metadata["captureScriptSha256"] == hashlib.sha256(capture_script.read_bytes()).hexdigest()
    assert metadata["verifierScriptSha256"] == hashlib.sha256(verifier_script.read_bytes()).hexdigest()

    for field, foreign_value in (
        ("fixtureVersion", "2026-07-11.2"),
        ("courseId", "foreign-course"),
        ("lessonId", "foreign-lesson"),
    ):
        foreign = {**expected, field: foreign_value}
        _metadata, errors = soak.validate_live_attestation(
            **foreign,
            capture_script=capture_script,
            verifier_script=verifier_script,
        )
        assert errors, field


def test_soak_attestation_fails_closed_when_helper_cannot_be_hashed(tmp_path):
    complete_result(tmp_path)
    capture_script, verifier_script = helper_script_paths(tmp_path)
    verifier_script.unlink()

    _metadata, errors = soak.validate_live_attestation(
        fixtureVersion="2026-07-11.1",
        courseId="production-farm-english-358",
        lessonId="pip-farm-8m",
        capture_script=capture_script,
        verifier_script=verifier_script,
    )

    assert "cannot hash --verifier-script" in errors


def test_log_audit_uses_shared_live_attestation_validator(tmp_path):
    complete_result(tmp_path)
    capture_script, verifier_script = helper_script_paths(tmp_path)
    kwargs = {
        "fixtureVersion": "foreign",
        "courseId": "production-farm-english-358",
        "lessonId": "pip-farm-3m",
        "capture_script": capture_script,
        "verifier_script": verifier_script,
    }
    assert audit.validate_live_attestation(**kwargs) == soak.validate_live_attestation(**kwargs)


def test_scenario_markers_must_be_present_in_raw_logs(tmp_path):
    result = complete_result(tmp_path)
    errors = fault.validate_result("cold", result, "unrelated output")
    assert "raw logs missing declared marker: lesson_preload_ready" in errors
    assert "raw logs missing declared marker: checksum_verified" in errors


def test_scenario_requires_decisive_marker_names_not_arbitrary_log_text(tmp_path):
    result = complete_result(tmp_path)
    result["logMarkers"] = ["operator_says_pass"]
    errors = fault.validate_result("cold", result, "operator_says_pass")
    assert "cold requires log marker: lesson_preload_ready" in errors
    assert "cold requires log marker: checksum_verified" in errors


def test_common_metadata_rejects_invalid_time_identity_and_heap_types(tmp_path):
    result = complete_result(tmp_path)
    result.update({"utcEnd": "before-start", "deviceId": "", "internalSramMin": True})
    errors = fault.validate_result("cold", result, "lesson_preload_ready checksum_verified")
    assert "invalid UTC evidence interval" in errors
    assert "invalid deviceId" in errors
    assert "invalid internalSramMin" in errors


def test_malformed_scenario_fields_fail_closed_instead_of_crashing(tmp_path):
    malformed = {
        "cold": {"bytesDownloaded": "100"},
        "warm": {"elapsedMs": []},
        "sd-full": {"freeRatio": "0.04"},
    }
    for scenario, fields in malformed.items():
        result = complete_result(tmp_path)
        result["scenario"] = scenario
        result.update(fields)
        errors = fault.validate_result(scenario, result, "lesson_preload_ready checksum_verified")
        assert f"{scenario} decisive signals are incomplete" in errors


def test_evidence_report_hashes_screenshots(tmp_path):
    result = complete_result(tmp_path)
    evidence = fault.build_evidence_report("cold", result, {}, cold_raw_evidence(result))
    assert evidence["status"] == "PASS"
    assert len(evidence["screenshots"]) == 2
    assert all(len(item["sha256"]) == 64 for item in evidence["screenshots"])


def test_relative_screenshot_paths_resolve_from_evidence_directory(tmp_path):
    screenshot = tmp_path / "hardware.png"
    write_png(screenshot)
    result = complete_result(tmp_path)
    result["screenshots"] = [{"role": "hardware", "path": "hardware.png"}]
    report = fault.build_evidence_report(
        "cold", result, {}, cold_raw_evidence(result), tmp_path
    )
    assert report["status"] == "PASS"
    assert report["screenshots"][0]["path"] == str(screenshot)


def test_preview_parity_requires_distinct_480x320_preview_and_hardware_images(tmp_path):
    result = complete_result(tmp_path)
    result.update({
        "scenario": "preview-parity",
        "logMarkers": ["lesson_step_started", "motion_preset"],
        "previewLayerRects": {"background": [0, 0, 480, 320]},
        "hardwareLayerRects": {"background": [0, 0, 480, 320]},
        "previewWordText": "BARN",
        "hardwareWordText": "BARN",
        "previewPathOutcome": "correct",
        "hardwarePathOutcome": "correct",
        "previewMotionTimeline": ["teach"],
        "hardwareMotionTimeline": ["teach"],
    })
    assert fault.validate_result("preview-parity", result, "lesson_step_started motion_preset", tmp_path) == []
    small = tmp_path / "small.png"
    write_png(small, 320, 240)
    result["screenshots"][1]["path"] = str(small)
    assert "preview-parity screenshots must be exactly 480x320" in fault.validate_result(
        "preview-parity", result, "lesson_step_started motion_preset", tmp_path
    )
    result["screenshots"][1]["path"] = result["screenshots"][0]["path"]
    assert "preview and hardware screenshots must not have identical content" in fault.validate_result(
        "preview-parity", result, "lesson_step_started motion_preset", tmp_path
    )


def test_screenshot_paths_are_confined_regular_bounded_images(tmp_path):
    outside = tmp_path.parent / "outside.png"
    write_png(outside)
    result = complete_result(tmp_path)
    result["screenshots"] = [{"role": "hardware", "path": "../outside.png"}]
    errors = fault.validate_result("cold", result, "lesson_preload_ready checksum_verified", tmp_path)
    assert "screenshot path escapes evidence directory" in errors
    result["screenshots"] = [42]
    assert "malformed screenshot entry" in fault.validate_result(
        "cold", result, "lesson_preload_ready checksum_verified", tmp_path
    )
    fake = tmp_path / "fake.png"
    fake.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 64)
    result["screenshots"] = [{"role": "hardware", "path": "fake.png"}]
    assert "screenshots must be valid PNG or JPEG images" in fault.validate_result(
        "cold", result, "lesson_preload_ready checksum_verified", tmp_path
    )
    huge = tmp_path / "huge.png"
    write_png(huge, 1, 1)
    data = bytearray(huge.read_bytes())
    data[16:24] = struct.pack(">II", 100_000, 100_000)
    data[29:33] = struct.pack(">I", zlib.crc32(bytes(data[12:29])))
    huge.write_bytes(data)
    result["screenshots"] = [{"role": "hardware", "path": "huge.png"}]
    assert "screenshots must be valid PNG or JPEG images" in fault.validate_result(
        "cold", result, "lesson_preload_ready checksum_verified", tmp_path
    )
    link = tmp_path / "linked.png"
    link.symlink_to(outside)
    result["screenshots"] = [{"role": "hardware", "path": "linked.png"}]
    assert "screenshot paths must not be symlinks" in fault.validate_result(
        "cold", result, "lesson_preload_ready checksum_verified", tmp_path
    )
    oversized = tmp_path / "oversized.png"
    write_png(oversized)
    with oversized.open("ab") as handle:
        handle.truncate(fault.MAX_SCREENSHOT_BYTES + 1)
    result["screenshots"] = [{"role": "hardware", "path": "oversized.png"}]
    assert "screenshot exceeds maximum size" in fault.validate_result(
        "cold", result, "lesson_preload_ready checksum_verified", tmp_path
    )
    no_pixels = tmp_path / "no-pixels.png"
    no_pixels.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", 13) + b"IHDR" + struct.pack(">IIBBBBB", 480, 320, 8, 2, 0, 0, 0)
        + struct.pack(">I", zlib.crc32(b"IHDR" + struct.pack(">IIBBBBB", 480, 320, 8, 2, 0, 0, 0)))
        + struct.pack(">I", 0) + b"IEND" + struct.pack(">I", zlib.crc32(b"IEND"))
    )
    result["screenshots"] = [{"role": "hardware", "path": "no-pixels.png"}]
    assert "screenshots must be valid PNG or JPEG images" in fault.validate_result(
        "cold", result, "lesson_preload_ready checksum_verified", tmp_path
    )


def test_malformed_structured_json_never_crashes_progress_audit():
    for payload in ("[]", '"lesson_progress"', "123", "null"):
        assert audit.audit(payload)["status"] == "PASS"


def test_progress_identity_distinguishes_event_type_and_sequence():
    legitimate = "\n".join([
        json.dumps({"type": "lesson_progress_started", "session_id": "s", "step_id": "a", "sequence": 7}),
        json.dumps({"type": "lesson_progress_completed", "session_id": "s", "step_id": "a", "sequence": 7}),
    ])
    assert audit.audit(legitimate)["status"] == "PASS"
    duplicate = legitimate + "\n" + json.dumps(
        {"type": "lesson_progress_completed", "session_id": "s", "step_id": "a", "sequence": 7}
    )
    report = audit.audit(duplicate)
    assert report["status"] == "NOT_PASS"
    assert report["duplicateProgress"][0]["eventType"] == "lesson_progress_completed"
    assert report["duplicateProgress"][0]["identity"] == "sequence:7"


def test_progress_identity_uses_real_envelope_and_text_semantic_event():
    envelopes = [
        {"type": "lesson_progress", "sessionId": "s", "stepId": "a", "body": {"event": "step_started"}},
        {"type": "lesson_progress", "sessionId": "s", "stepId": "a", "body": {"event": "step_completed"}},
    ]
    assert audit.audit("\n".join(map(json.dumps, envelopes)))["status"] == "PASS"
    text_logs = "\n".join([
        "lesson_progress event=step_started session_id=s step_id=a",
        "lesson_progress event=step_completed session_id=s step_id=a",
    ])
    assert audit.audit(text_logs)["status"] == "PASS"
    duplicate = "\n".join([json.dumps(envelopes[1]), json.dumps(envelopes[1])])
    report = audit.audit(duplicate)
    assert report["status"] == "NOT_PASS"
    assert report["duplicateProgress"][0]["eventType"] == "step_completed"


def test_soak_rejects_duplicate_or_out_of_order_transition_identity():
    duplicate = "\n".join(
        f"lesson_step_transition sequence={0 if index == 50 else index} psram_free=8000000 internal_min_free=32768"
        for index in range(100)
    )
    report = soak.analyze(duplicate)
    assert report["status"] == "NOT_PASS"
    assert report["checks"]["transition_sequence_strictly_increasing"] is False


def test_soak_rejects_large_psram_loss_with_small_recovery_blips():
    lines = []
    for index in range(100):
        psram = 8_000_000 - index * 2_048
        if index == 50:
            psram += 4_096
        lines.append(
            f"lesson_step_transition sequence={index} psram_free={psram} "
            "internal_min_free=32768"
        )
    report = soak.analyze("\n".join(lines))
    assert report["status"] == "NOT_PASS"
    assert report["checks"]["no_psram_loss_above_tolerance"] is False


def test_soak_accepts_authentic_three_session_transitions_with_sequence_resets():
    serial_log, server_log = authentic_soak_logs()

    report = soak.analyze_logs(serial_log, server_log, count=99)

    assert report["status"] == "PASS"
    assert report["metrics"]["transitions"] == 100
    assert report["metrics"]["sessions"] == 3
    assert report["metrics"]["internalSramMin"] == 24574


def test_production_soak_requires_timeline_instead_of_zip_binding_sources():
    serial_log, server_log = authentic_soak_logs()
    foreign_server = server_log.replace("assignment-", "foreign-assignment-").replace(
        "session-", "foreign-session-"
    )

    for candidate in (server_log, foreign_server):
        report = soak.analyze_logs(serial_log, candidate)
        assert report["status"] == "NOT_PASS"
        assert report["checks"]["timeline_binding_required"] is False


def test_soak_fails_closed_when_cross_source_transition_binding_is_incomplete():
    serial_log, server_log = authentic_soak_logs()
    server_log = server_log.replace(" assignment_id=assignment-2", "", 1)

    report = soak.analyze_logs(serial_log, server_log)

    assert report["status"] == "NOT_PASS"
    assert report["checks"]["transition_binding_complete"] is False


def test_soak_reads_camel_case_ack_telemetry_heap_samples():
    serial_log, server_log = authentic_soak_logs()
    serial_log = "\n".join(
        line for line in serial_log.splitlines() if "heap_checkpoint" not in line
    )
    telemetry = "\n".join(
        '{"type":"lesson_ack","body":{"telemetry":{"internalMinimumFreeBytes":24576,'
        f'"psramFreeBytes":{8_000_000 - index}}}}}'
        for index in range(100)
    )

    report = soak.analyze_logs(
        serial_log + "\n" + telemetry + "\nlifetime_min_internal=24576",
        server_log,
        count=99,
    )

    assert report["status"] == "PASS"
    assert report["metrics"]["internalSramMin"] == 24576


def test_soak_lifetime_internal_sram_below_gate_blocks_release():
    serial_log, server_log = authentic_soak_logs()
    serial_log = serial_log.replace("lifetime_min_internal=25000", "lifetime_min_internal=1024")

    report = soak.analyze_logs(serial_log, server_log)

    assert report["status"] == "NOT_PASS"
    assert report["checks"]["lifetime_internal_sram_above_gate"] is False
    assert report["metrics"]["activeInternalSramMin"] == 24574
    assert report["metrics"]["lifetimeInternalSramMin"] == 1024


def test_soak_missing_lifetime_internal_sram_fails_closed():
    serial_log, server_log = authentic_soak_logs()
    serial_log = "\n".join(
        line.replace(" lifetime_min_internal=25000", "")
        for line in serial_log.splitlines()
    )

    report = soak.analyze_logs(serial_log, server_log)

    assert report["status"] == "NOT_PASS"
    assert report["checks"]["lifetime_internal_sram_present"] is False


def test_soak_requires_timeline_for_repeated_step_ids_across_sessions():
    serial_log, server_log = authentic_soak_logs(repeated_step_ids=True)

    report = soak.analyze_logs(serial_log, server_log)

    assert report["status"] == "NOT_PASS"
    assert report["checks"]["transition_binding_unambiguous"] is False


def test_soak_timeline_binds_repeated_step_ids_to_their_session_boundaries():
    report = soak.analyze_timeline(authentic_soak_timeline())

    assert report["status"] == "PASS"
    assert report["metrics"]["transitions"] == 100
    assert report["metrics"]["sessions"] == 3


@pytest.mark.parametrize("module", (soak, audit))
def test_production_evidence_cli_requires_transition_minimum_exactly_104(module):
    assert module.minimum_transition_count("104") == 104
    for invalid in ("103", "105"):
        with pytest.raises(argparse.ArgumentTypeError):
            module.minimum_transition_count(invalid)


@pytest.mark.parametrize("module", (soak, audit))
def test_production_evidence_binds_104_transition_requirement_and_build_identity(
    module, tmp_path, monkeypatch, capsys
):
    serial_log, server_log = authentic_soak_logs((35, 35, 34))
    serial_path = tmp_path / "serial.log"
    server_path = tmp_path / "server.log"
    timeline_path = tmp_path / "timeline.log"
    capture_script, verifier_script = helper_script_paths(tmp_path)
    capture_script.write_text("capture\n")
    verifier_script.write_text("verify\n")
    build_manifest = tmp_path / "lesson-storage-hil-build.json"
    release_order_artifact = tmp_path / "release-ledger.json"
    serial_path.write_text(serial_log)
    server_path.write_text(server_log)
    timeline_path.write_text(authentic_soak_timeline((35, 35, 34)))
    build_identity = {
        "sourceCommit": "a" * 40,
        "profile": "production",
        "configEnabled": False,
        "sdkconfigSha256": "b" * 64,
        "binarySha256": "c" * 64,
        "elfSha256": "d" * 64,
        "mapSha256": "e" * 64,
        "archiveSha256": "f" * 64,
        "binaryBytes": 1,
        "appPartitionFreeBytes": 1,
    }
    calls = []
    release_calls = []

    def fake_load(path, *, expected_profile=None):
        calls.append((path, expected_profile))
        return build_identity

    monkeypatch.setattr(module, "load_build_identity", fake_load)
    monkeypatch.setattr(
        module,
        "load_release_ledger",
        lambda path, *, production_identity, required_event: release_calls.append(
            (path, production_identity, required_event)
        ) or {"receipts": [{"event": required_event}]},
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            module.__file__,
            str(serial_path),
            str(server_path),
            "--timeline-log",
            str(timeline_path),
            "--minimum-transitions",
            "104",
            "--build-manifest",
            str(build_manifest),
            "--release-ledger",
            str(release_order_artifact),
            "--fixture-version",
            soak.FIXTURE_VERSION,
            "--course-id",
            soak.COURSE_ID,
            "--lesson-id",
            soak.LESSON_IDS[0],
            "--capture-script",
            str(capture_script),
            "--verifier-script",
            str(verifier_script),
        ],
    )

    assert module.main() == 0
    report = json.loads(capsys.readouterr().out)
    assert report["minimumTransitionsRequired"] == 104
    assert report["metrics"]["minimumTransitionsRequired"] == 104
    assert report["buildIdentity"] == build_identity
    expected_event = "production-attest" if module is soak else "production-soak"
    assert report["releaseLedgerEvidence"]["receipts"][-1]["event"] == expected_event
    assert report["releaseLedgerErrors"] == []
    assert report["metrics"]["transitions"] == 104
    assert calls == [(build_manifest, "production")]
    assert release_calls == [(release_order_artifact, build_identity, expected_event)]


@pytest.mark.parametrize("module", (soak, audit))
def test_production_evidence_cli_requires_release_order_artifact(module, monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            module.__file__,
            "serial.log",
            "server.log",
            "--minimum-transitions",
            "104",
            "--build-manifest",
            "lesson-storage-hil-build.json",
        ],
    )
    with pytest.raises(SystemExit) as exc:
        module.main()
    assert exc.value.code == 2


@pytest.mark.parametrize("module", (soak, audit))
@pytest.mark.parametrize("manifest_kind", ("missing", "invalid-json", "hil"))
def test_production_evidence_cli_replaces_stale_output_on_invalid_build_manifest(
    module, manifest_kind, tmp_path
):
    serial_log, server_log = authentic_soak_logs((35, 35, 34))
    serial_path = tmp_path / "serial.log"
    server_path = tmp_path / "server.log"
    timeline_path = tmp_path / "timeline.log"
    capture_script, verifier_script = helper_script_paths(tmp_path)
    output_path = tmp_path / "report.json"
    serial_path.write_text(serial_log)
    server_path.write_text(server_log)
    timeline_path.write_text(authentic_soak_timeline((35, 35, 34)))
    capture_script.write_text("capture\n")
    verifier_script.write_text("verify\n")
    output_path.write_text("STALE OUTPUT\n")
    if manifest_kind == "missing":
        build_manifest = tmp_path / "missing" / "lesson-storage-hil-build.json"
    elif manifest_kind == "invalid-json":
        build_dir = tmp_path / "invalid"
        build_dir.mkdir()
        build_manifest = build_dir / "lesson-storage-hil-build.json"
        build_manifest.write_text("{invalid json\n")
        (build_dir / "lesson-storage-hil-build.sha256").write_text(
            f"{hashlib.sha256(build_manifest.read_bytes()).hexdigest()}  {build_manifest.name}\n"
        )
    else:
        helper_path = ROOT / "tests" / "test_lesson_studio_task14_hil_storage.py"
        helper_spec = importlib.util.spec_from_file_location(
            f"task14_hil_fixture_{module.__name__}", helper_path
        )
        helper = importlib.util.module_from_spec(helper_spec)
        assert helper_spec.loader is not None
        helper_spec.loader.exec_module(helper)
        build_manifest, _manifest, _paths = helper.task6_manifest(tmp_path / "hil")

    completed = subprocess.run(
        [
            sys.executable,
            module.__file__,
            str(serial_path),
            str(server_path),
            "--timeline-log",
            str(timeline_path),
            "--minimum-transitions",
            "104",
            "--build-manifest",
            str(build_manifest),
            "--release-ledger",
            str(tmp_path / "release-ledger.json"),
            "--fixture-version",
            soak.FIXTURE_VERSION,
            "--course-id",
            soak.COURSE_ID,
            "--lesson-id",
            soak.LESSON_IDS[0],
            "--capture-script",
            str(capture_script),
            "--verifier-script",
            str(verifier_script),
            "--output",
            str(output_path),
        ],
        check=False,
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 1
    assert "Traceback" not in completed.stderr
    assert "build identity" in completed.stderr.lower()
    report = json.loads(output_path.read_text())
    assert report["status"] == "NOT_PASS"
    assert report["minimumTransitionsRequired"] == 104
    assert report["metrics"]["minimumTransitionsRequired"] == 104
    assert report["buildIdentity"] is None
    assert report["buildIdentityErrors"]


def test_log_audit_rejects_firmware_crash_and_heap_corruption_markers():
    for marker in (
        "assert failed: runtime.cpp:42",
        "stack overflow in task lesson",
        "CORRUPT HEAP: bad tail",
        "abort() was called at PC 0x1234",
        "LoadProhibited exception",
    ):
        report = audit.audit(marker)
        assert report["status"] == "NOT_PASS", marker
        assert report["findings"]["firmwareCrash"] == 1


def test_log_audit_detects_duplicate_progress_regardless_of_field_order():
    lines = [
        json.dumps({"type": "lesson_progress", "step_id": "step-a", "session_id": "session-1"}),
        "lesson_progress step id=step-a session id=session-1",
    ]
    report = audit.audit("\n".join(lines))
    assert report["status"] == "NOT_PASS"
    assert report["duplicateProgress"] == [{
        "eventType": "lesson_progress",
        "session": "session-1",
        "step": "step-a",
        "identity": "identity:missing",
    }]


def test_log_audit_requires_both_sources_and_one_hundred_bound_transitions():
    serial_log, server_log = authentic_soak_logs()
    timeline = authentic_soak_timeline()

    assert audit.audit_logs(serial_log, server_log)["status"] == "NOT_PASS"
    assert audit.audit_logs(serial_log, server_log, timeline_text=timeline)["status"] == "PASS"
    assert audit.audit_logs(serial_log, "")["status"] == "NOT_PASS"
    short_serial, short_server = authentic_soak_logs((33, 33, 33))
    short_timeline = authentic_soak_timeline((33, 33, 33))
    assert audit.audit_logs(
        short_serial, short_server, timeline_text=short_timeline
    )["status"] == "NOT_PASS"


def test_log_audit_uses_timeline_session_boundaries_for_repeated_step_ids():
    serial_log, server_log = authentic_soak_logs(repeated_step_ids=True)

    assert audit.audit_logs(serial_log, server_log)["status"] == "NOT_PASS"
    report = audit.audit_logs(
        serial_log,
        server_log,
        timeline_text=authentic_soak_timeline(),
    )

    assert report["status"] == "PASS"
    assert report["metrics"] == {"transitions": 100, "sessions": 3}


def test_log_audit_recognizes_actual_runtime_failure_vocabulary_and_drop_deltas():
    serial_log, server_log = authentic_soak_logs()
    failures = (
        "Failed to allocate download buffer",
        "lesson image: decoded JPEG rejected; skipping",
        "sequence gap: got 7, expected 6",
        "audio_metrics decode_drop=0 encode_drop=0",
        "audio_metrics decode_drop=1 encode_drop=0",
    )

    report = audit.audit_logs(serial_log + "\n" + "\n".join(failures), server_log)

    assert report["status"] == "NOT_PASS"
    assert report["findings"]["allocationFailure"] == 1
    assert report["findings"]["decodeFailure"] == 1
    assert report["findings"]["sequenceDivergence"] == 1
    assert report["findings"]["audioUnderrun"] == 1


def test_fault_driver_rejects_unscoped_operator_marker_text_for_cold(tmp_path):
    result = complete_result(tmp_path)

    errors = fault.validate_result(
        "cold",
        result,
        "lesson_preload_ready checksum_verified operator says downloadedCount=2",
    )

    assert "cold raw evidence is not bound to result identity and cache" in errors


def test_fault_driver_rejects_cross_session_cold_markers(tmp_path):
    result = complete_result(tmp_path)
    raw = cold_raw_evidence(result).replace("session_id=session-1", "session_id=session-other")

    errors = fault.validate_result("cold", result, raw)

    assert "cold raw evidence is not bound to result identity and cache" in errors


def test_fault_driver_binds_warm_and_checksum_decisive_fields_to_raw_logs(tmp_path):
    warm = complete_result(tmp_path)
    warm.update({
        "scenario": "warm",
        "cacheHit": True,
        "bytesDownloaded": 0,
        "logMarkers": ["asset_cache_hit"],
    })
    assert fault.validate_result("warm", warm, warm_raw_evidence(warm), tmp_path) == []
    warm_errors = fault.validate_result(
        "warm",
        warm,
        warm_raw_evidence(warm).replace(warm["cacheKey"], "wrong-cache"),
        tmp_path,
    )
    assert "warm raw evidence is not bound to result identity and cache" in warm_errors

    checksum = complete_result(tmp_path)
    checksum.update({
        "scenario": "checksum",
        "mismatchDetected": True,
        "partialCleaned": True,
        "ready": False,
        "logMarkers": ["checksum_mismatch", "partial_cleaned"],
    })
    assert fault.validate_result("checksum", checksum, checksum_raw_evidence(checksum), tmp_path) == []
    checksum_errors = fault.validate_result(
        "checksum",
        checksum,
        checksum_raw_evidence(checksum).replace("partialCleaned=true", "partialCleaned=false"),
        tmp_path,
    )
    assert "checksum raw evidence is not bound to result identity and cache" in checksum_errors
    cleanup_cache_errors = fault.validate_result(
        "checksum",
        checksum,
        checksum_raw_evidence(checksum).replace(
            "partial_cleaned cacheKey=" + checksum["cacheKey"],
            "partial_cleaned cacheKey=foreign-cache",
        ),
        tmp_path,
    )
    assert "checksum raw evidence is not bound to result identity and cache" in cleanup_cache_errors
    cleanup_checksum_errors = fault.validate_result(
        "checksum",
        checksum,
        checksum_raw_evidence(checksum).replace(
            "partial_cleaned cacheKey={cacheKey} manifestChecksum={manifestChecksum}".format(**checksum),
            "partial_cleaned cacheKey={cacheKey} manifestChecksum={wrong}".format(
                **checksum, wrong="e" * 64
            ),
        ),
        tmp_path,
    )
    assert "checksum raw evidence is not bound to result identity and cache" in cleanup_checksum_errors


def test_task14_docs_include_preview_and_keep_every_live_gate_not_pass():
    probes = (ROOT / "docs" / "lesson-studio-task14-probes.md").read_text()
    matrix = (ROOT / "docs" / "TEST_MATRIX_TASK14.md").read_text()
    live = (ROOT / "docs" / "lesson-studio-task14-live-matrix.md").read_text()
    assert "`preview-parity`" in probes
    assert "preview-parity" in matrix
    assert "common metadata" in probes.lower()
    assert '"role": "preview"' in probes
    assert "480x320" in probes
    assert "10 MiB" in probes
    assert "canonical `partial_cleaned` line must bind" in probes
    assert "`cacheKey`, `manifestChecksum`, `assignment_id`, and `session_id`" in probes
    expected_rows = [
        ("T14-LIVE-01", "preview-parity", "NOT PASS - live run required"),
        ("T14-LIVE-02", "cold", "NOT PASS - live run required"),
        ("T14-LIVE-03", "warm", "NOT PASS - live run required"),
        ("T14-LIVE-04", "offline", "NOT PASS - live run required"),
        ("T14-LIVE-05", "checksum", "NOT PASS - live run required"),
        ("T14-LIVE-06", "interrupted", "NOT PASS - live run required"),
        ("T14-LIVE-07", "power-loss", "NOT PASS - live run required"),
        ("T14-LIVE-08", "missing-optional", "NOT PASS - live run required"),
        ("T14-LIVE-09", "sd-full", "NOT PASS - live run required"),
        ("T14-LIVE-10", "slave-unavailable", "NOT PASS - live run required"),
        ("T14-LIVE-11", "rollback", "NOT PASS - live run required"),
        ("T14-LIVE-12", "soak", "NOT PASS - live run required"),
        ("T14-LIVE-13", "log audit", "NOT PASS - live run required"),
    ]
    actual_rows = []
    for line in live.splitlines():
        match = re.fullmatch(
            r"\| (T14-LIVE-\d{2}) (?:`([^`]+)`|(soak|log audit)) \|.*"
            r"\| ([^|]+) \|",
            line,
        )
        if match:
            actual_rows.append((match.group(1), match.group(2) or match.group(3), match.group(4)))
    assert actual_rows == expected_rows
    assert '"fixtureVersion": "2026-07-11.1"' in live
    assert '"courseId": "production-farm-english-358"' in live
    assert '"captureScriptSha256": "<sha256-of---capture-script>"' in live
    assert '"verifierScriptSha256": "<sha256-of---verifier-script>"' in live
    assert "--capture-script" in live
    assert "--verifier-script" in live
    assert "report.json.fixtureVersion" in live
    assert "audit.json.fixtureVersion" in live
    assert "--capture-only" in live
    assert live.count("--minimum-transitions 104") == 2
    assert live.count('--build-manifest "$PRODUCTION_BUILD_MANIFEST"') == 2
    assert live.count('--release-ledger "$RELEASE_LEDGER"') == 2
    assert "lesson_studio_task14_build_identity.py release" in live
    assert "hil-matrix-pass -> production-reflash -> production-attest -> production-soak" in live
    assert "/Users/manhhodinh/Documents/TBOT/.worktrees/esp32-server-production-lesson-studio-continued" in live
    assert "/Users/manhhodinh/Documents/TBOT/.worktrees/tbot-firmware-production-lesson-studio-continued" in live
