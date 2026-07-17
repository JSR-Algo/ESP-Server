import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
import types
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
KEY = f"hil-task14/v1-{'d' * 64}"
SIBLING = f"hil-task14/v2-{'e' * 64}"


def load_script(name):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.removesuffix(".py"), path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def task6_manifest(tmp_path, profile="hil", source_commit="a" * 40, binary=b"hil"):
    firmware = tmp_path / "firmware"
    build = firmware / f"build-{profile}"
    build.mkdir(parents=True)
    defaults = firmware / "sdkconfig.defaults"
    defaults.write_text("CONFIG_IDF_TARGET_ESP32S3=y\n")
    hil_defaults = firmware / "sdkconfig.defaults.hil-storage"
    hil_defaults.write_text("CONFIG_TBOT_HIL_STORAGE_FAULTS=y\n")
    local_defaults = build / "sdkconfig.defaults.hil-local"
    local_defaults.write_text("CONFIG_OTA_URL=\"https://example.invalid/\"\n")
    artifact_bytes = {
        "bin": binary,
        "elf": b"elf-" + binary,
        "map": b"map-" + binary,
        "mainArchive": b"archive-" + binary,
        "sdkconfig": (
            b"CONFIG_TBOT_HIL_STORAGE_FAULTS=y\n"
            if profile == "hil"
            else b"# CONFIG_TBOT_HIL_STORAGE_FAULTS is not set\n"
        ),
        "partitionBinary": b"partition",
    }
    paths = {}
    for label, content in artifact_bytes.items():
        relative = {
            "bin": "xiaozhi.bin",
            "elf": "xiaozhi.elf",
            "map": "xiaozhi.map",
            "mainArchive": "esp-idf/main/libmain.a",
            "sdkconfig": "sdkconfig",
            "partitionBinary": "partition_table/partition-table.bin",
        }[label]
        path = build / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        paths[label] = path
    description = build / "project_description.json"
    description.write_text(json.dumps({"project_path": str(firmware), "build_dir": str(build)}))
    paths["projectDescription"] = description
    config_defaults = [{"path": "sdkconfig.defaults", "sha256": sha256(defaults)}]
    if profile == "hil":
        config_defaults.extend(
            [
                {"path": "sdkconfig.defaults.hil-storage", "sha256": sha256(hil_defaults)},
                {
                    "path": f"{build.name}/sdkconfig.defaults.hil-local",
                    "sha256": sha256(local_defaults),
                },
            ]
        )
    manifest = {
        "status": "PASS",
        "profile": profile,
        "sourceCommit": source_commit,
        "sourceCommitTimestamp": 1,
        "target": "esp32s3",
        "project": "xiaozhi",
        "buildDirectory": build.name,
        "configDefaults": config_defaults,
        "artifacts": {
            label: {
                "path": str(path.relative_to(build)),
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
            for label, path in paths.items()
        },
        "partition": {
            "partitionTable": "partitions/v2/16m.csv",
            "partitionBytes": 4_000_000,
            "imageBytes": len(binary),
            "freeBytes": 4_000_000 - len(binary),
            "freePercent": 99.0,
        },
        "checks": {
            "hilConfigEnabled": profile == "hil",
            "toolLiterals": "present" if profile == "hil" else "absent",
            "hilSymbols": "present" if profile == "hil" else "absent",
            "bannedApis": "absent",
        },
    }
    manifest_path = build / "lesson-storage-hil-build.json"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True) + "\n")
    checksum_path = build / "lesson-storage-hil-build.sha256"
    checksum_path.write_text(f"{sha256(manifest_path)}  {manifest_path.name}\n")
    return manifest_path, manifest, paths


def exact_arm():
    return {
        "cacheKey": KEY,
        "status": "armed",
        "operation": "evict",
        "checkpoint": "after_unlinks",
        "action": "fail",
        "threshold": 1,
        "declaredAssetBytes": 0,
        "pauseSeconds": 0,
        "armSequence": 1,
    }


def exact_status(state="consumed"):
    idle = state == "idle"
    return {
        "status": state,
        "cacheKey": "" if idle else KEY,
        "armed": state == "armed",
        "reached": state in ("reached", "consumed"),
        "consumed": state == "consumed",
        "operation": "evict",
        "checkpoint": "before_first_unlink" if idle else "after_unlinks",
        "action": "fail",
        "threshold": 0 if idle else 1,
        "declaredAssetBytes": 0,
        "pauseSeconds": 0,
        "armSequence": 0 if idle else 1,
        "reachedSequence": 2 if state in ("reached", "consumed") else 0,
        "consumedSequence": 3 if state == "consumed" else 0,
    }


def exact_recovery_response(**changes):
    response = {
        "cacheKey": KEY,
        "status": "evicted",
        "reason": "evicted",
        "evicted": True,
        "notFound": False,
        "fileCount": 0,
    }
    response.update(changes)
    return response


def preservation_inspection(primary_state, sibling_state="full", *, protected_sha="a" * 64):
    entries = [
        {"label": "lesson-assets/current.json", "nodeType": "regular_file", "bytes": 7, "sha256": protected_sha},
        {"label": "lesson-assets/pvg", "nodeType": "directory", "bytes": 0, "sha256": ""},
        {"label": "lesson-assets/shared", "nodeType": "directory", "bytes": 0, "sha256": ""},
    ]
    entries.extend(hil_preservation_entries(KEY, primary_state, "e95ab394bdf8569652429018519989d3e94cae168cf91c269c81a2c9bb00d5ec"))
    entries.extend(hil_preservation_entries(SIBLING, sibling_state, "462cc80e16c12bbee14c7eba5e61da286e79580d6dc5b996bfcf7a43f30a4cf8"))
    return {
        "cacheKey": KEY,
        "siblingCacheKey": SIBLING,
        "status": "inspected",
        "truncated": False,
        "entries": sorted(entries, key=lambda item: item["label"]),
    }


def hil_preservation_entries(cache_key, state, digest):
    label = f"lesson-assets/{cache_key}"
    if state == "missing":
        return [{"label": label, "nodeType": "missing", "bytes": 0, "sha256": ""}]
    entries = [{"label": label, "nodeType": "directory", "bytes": 0, "sha256": ""}]
    if state == "full":
        entries.append({
            "label": f"{label}/.tbot-hil-sentinel",
            "nodeType": "regular_file",
            "bytes": 33,
            "sha256": digest,
        })
    return entries


def run_scenario_with_fakes(
    monkeypatch, tmp_path, scenario, *, recovery_response=None,
    recovered_inspection=None, injected_response=None, state=None, stage_error=None,
    inspect_before_response=None, bounded_rejection=None,
):
    hil = load_script("lesson_studio_task14_hil_storage.py")
    operation, checkpoint, action, threshold, pause_seconds, power_loss = hil.SCENARIO_SPECS[scenario]
    primary_after = {
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
    before = inspect_before_response or preservation_inspection("missing", "missing")
    after = preservation_inspection(primary_after)
    recovered = recovered_inspection or preservation_inspection("missing")
    cleanup_inspection = preservation_inspection("missing", "missing")
    injected = injected_response if injected_response is not None else (
        eviction_result(
            {
                "evict-before-first-unlink-fail": "unlink_failed",
                "evict-after-unlinks-fail": "partial_evict_recovery_required",
                "evict-before-rmdir-fail": "partial_evict_recovery_required",
                "evict-after-unlinks-sd-removal": "evicted",
            }[scenario],
            0 if scenario == "evict-before-first-unlink-fail" else 1,
            evicted=scenario == "evict-after-unlinks-sd-removal",
        )
        if operation == "evict"
        else failed_sync_result()
    )
    calls = []
    transport_calls = []
    scenario_inspections = iter([before, after, recovered])

    def status_for_arm(state):
        idle = state == "idle"
        return {
            "status": state,
            "cacheKey": "" if idle else KEY,
            "armed": state == "armed",
            "reached": state in ("reached", "consumed"),
            "consumed": state == "consumed",
            "operation": "evict" if idle else operation,
            "checkpoint": "before_first_unlink" if idle else checkpoint,
            "action": "fail" if idle else action,
            "threshold": 0 if idle else threshold,
            "declaredAssetBytes": 0,
            "pauseSeconds": 0 if idle else pause_seconds,
            "armSequence": 0 if idle else 1,
            "reachedSequence": 2 if state in ("reached", "consumed") else 0,
            "consumedSequence": 3 if state == "consumed" else 0,
        }

    class FakeTransport:
        def call(self, tool, args, timeout):
            transport_calls.append((tool, args, timeout))
            if tool == hil.TRIGGER_TOOLS["evict"]:
                evict_count = sum(item[0] == tool for item in transport_calls)
                calls.append("trigger" if operation == "evict" and evict_count == 1 else "recovery-trigger")
                if operation == "evict" and evict_count == 1:
                    return injected
                return recovery_response or exact_recovery_response()
            if tool == hil.TRIGGER_TOOLS["sync"]:
                sync_count = sum(item[0] == tool for item in transport_calls)
                calls.append("trigger" if sync_count == 1 else "retry-trigger")
                if power_loss and sync_count > 1:
                    return {**failed_sync_result(ready=True, state="DOWNLOADED"), "downloadedCount": 1, "failedCount": 0, "totalBytes": 1, "files": [{"key": "hil-asset.png", "path": "hil-asset.png", "localPath": f"/sdcard/tbot/lesson-assets/{KEY}/hil-asset.png", "state": "DOWNLOADED", "bytes": 1}]}
                return injected
            raise AssertionError(tool)

    class FakeClient:
        def __init__(self):
            self.transport = FakeTransport()
            self.status_count = 0
            self.cleanup_count = 0
            self.inspect_count = 0

        def status(self, _expected_cache_key=None):
            self.status_count += 1
            state = "idle" if self.status_count == 1 or (power_loss and self.status_count == 2) or self.status_count >= (3 if power_loss else 3) else "consumed"
            calls.append("status-before" if self.status_count == 1 else "status-after" if state == "consumed" or power_loss and self.status_count == 2 else "final-status")
            return status_for_arm(state)

        def inspect(self, _cache_key, _sibling):
            self.inspect_count += 1
            value = cleanup_inspection if self.cleanup_count else next(scenario_inspections)
            labels = (
                ("inspect-before", "inspect-after", "recovery-inspect", "cleanup-inspect")
                if scenario in hil.PARTIAL_EVICTION_SCENARIOS
                else ("inspect-before", "inspect-after", "cleanup-inspect")
            )
            calls.append(labels[self.inspect_count - 1])
            return hil.validate_inspect_response(value, KEY, SIBLING)

        def stage(self, *_args):
            calls.append("stage")
            if stage_error is not None:
                raise stage_error
            return {"cacheKey": KEY, "siblingCacheKey": SIBLING, "fixture": "preservation_set", "status": "staged", "changed": True}

        def arm(self, *_args, **_kwargs):
            calls.append("arm")
            return {
                "cacheKey": KEY, "status": "armed", "operation": operation,
                "checkpoint": checkpoint, "action": action, "threshold": threshold,
                "declaredAssetBytes": 0, "pauseSeconds": pause_seconds, "armSequence": 1,
            }

        def cleanup(self, *_args):
            self.cleanup_count += 1
            calls.append("cleanup")
            return {"cacheKey": KEY, "siblingCacheKey": SIBLING, "fixture": "preservation_set", "status": "cleaned", "changed": True}

    class FakeMonitor:
        def __init__(self):
            self.snapshot_count = 0

        def start(self):
            return self

        def read_new(self):
            return ""

        def snapshot(self):
            self.snapshot_count += 1
            if power_loss and self.snapshot_count == 1:
                return ""
            return "TBOT_HIL_STORAGE_FAULTS_ENABLED non-production-image\n"

        def stop(self):
            return None

    client = FakeClient()
    published = []
    if state is not None:
        state.update(
            client=client,
            transport_calls=transport_calls,
            calls=calls,
            published=published,
            failure_evidence=[],
        )
    monkeypatch.setenv("TBOT_TEST_SECRET", "test-secret")
    monkeypatch.setattr(hil, "load_build_identity", lambda *_args, **_kwargs: {"status": "PASS"})
    monkeypatch.setattr(hil, "attest_live_connection", lambda *_args: {"deviceId": "28:84:85:85:1a:80"})
    monkeypatch.setattr(hil, "RawMcpTransport", lambda *_args: object())
    monkeypatch.setattr(hil, "HilToolClient", lambda _transport: client)
    monkeypatch.setattr(hil, "SerialMonitor", lambda _port: FakeMonitor())
    monkeypatch.setattr(hil, "poll_checkpoint", lambda *_args, **_kwargs: f"HIL_STORAGE_CHECKPOINT_REACHED operation={operation} checkpoint={checkpoint} cache_key={KEY} count=0 reached_sequence=2\nHIL_STORAGE_FAULT_CONSUMED operation={operation} checkpoint={checkpoint} cache_key={KEY} count=0 consumed_sequence=3\n")
    monkeypatch.setattr(hil, "await_power_loss_disconnect", lambda *_args, **_kwargs: {"triggerPendingAtMarker": True, "triggerPendingAtCutBoundary": True, "powerCutBoundaryUtc": "2026-07-17T00:00:01Z", "disconnectObservedUtc": "2026-07-17T00:00:02Z", "powerRemovalConfirmedUtc": "2026-07-17T00:00:03Z", "disconnectAfterPowerCutBoundary": True})
    monkeypatch.setattr(hil, "utc_now", iter(["2026-07-17T00:00:00Z", "2026-07-17T00:00:00.500000Z", "2026-07-17T00:00:04Z"]).__next__)
    monkeypatch.setattr(hil, "_server_logs", lambda *_args: "")
    monkeypatch.setattr(
        hil,
        "publish_validated_scenario_directory",
        lambda _directory, payloads, **_kwargs: published.append(
            json.loads(payloads["recovery-response.json"])
        ),
    )
    monkeypatch.setattr(hil, "scenario_artifact_names", lambda **_kwargs: ("SHA256SUMS",))
    monkeypatch.setattr(hil, "atomic_write_bytes", lambda *_args: None)
    monkeypatch.setattr(hil.subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(returncode=0))
    if bounded_rejection is not None:
        original_bounded = hil.validate_bounded_failure_response

        def reject_selected(value, secrets=()):
            if bounded_rejection == "recovery" and value.get("attempted") is True:
                raise hil.HilCaptureLimitError("RECOVERY_RESPONSE_BYTES")
            if bounded_rejection == "cleanup" and value.get("status") == "cleaned":
                raise hil.HilCaptureLimitError("CLEANUP_RESPONSE_BYTES")
            return original_bounded(value, secrets)

        monkeypatch.setattr(hil, "validate_bounded_failure_response", reject_selected)
    if state is not None:
        monkeypatch.setattr(
            hil,
            "write_failure_evidence",
            lambda _root, **evidence: state["failure_evidence"].append(evidence),
        )
    pass_root = tmp_path / "pass"
    failure_root = tmp_path / "failures"
    pass_root.mkdir(exist_ok=True)
    failure_root.mkdir(exist_ok=True)
    arguments = SimpleNamespace(
        build_manifest=tmp_path / "manifest.json", esp_base_url="http://127.0.0.1",
        device_uuid="aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee", device_id="28:84:85:85:1a:80",
        mint_secret_env="TBOT_TEST_SECRET", asset_sha256="d" * 64, asset_bytes=1,
        asset_url="http://127.0.0.1/asset", serial_port="fake",
        evidence_dir=pass_root, failure_evidence_dir=failure_root,
        server_container="server",
    )
    result = hil.run_scenario(arguments, scenario, operator_input=lambda _prompt: "")
    return hil, result, client, transport_calls, calls, published, injected


def test_exact_hil_tool_response_schemas_and_foreign_key_rejection():
    hil = load_script("lesson_studio_task14_hil_storage.py")
    arm = exact_arm()
    assert hil.validate_arm_response(arm, KEY, "evict", "after_unlinks", "fail") == arm
    assert hil.validate_status_response(exact_status(), expected_cache_key=KEY)["consumed"] is True
    fixture = {
        "cacheKey": KEY,
        "siblingCacheKey": SIBLING,
        "fixture": "preservation_set",
        "status": "staged",
        "changed": True,
    }
    assert hil.validate_fixture_response(fixture, KEY, SIBLING, "preservation_set", "staged") == fixture
    inspect = {
        "cacheKey": KEY,
        "siblingCacheKey": SIBLING,
        "status": "inspected",
        "truncated": False,
        "entries": [{"label": "leaf", "nodeType": "file", "bytes": 3, "sha256": "f" * 64}],
    }
    assert hil.validate_inspect_response(inspect, KEY, SIBLING) == inspect

    for candidate in ({**arm, "extra": 1}, {key: value for key, value in arm.items() if key != "action"}):
        with pytest.raises(hil.HilValidationError):
            hil.validate_arm_response(candidate, KEY, "evict", "after_unlinks", "fail")
    with pytest.raises(hil.HilValidationError):
        hil.validate_arm_response({**arm, "threshold": True}, KEY, "evict", "after_unlinks", "fail")
    with pytest.raises(hil.HilValidationError):
        hil.validate_arm_response({**arm, "cacheKey": SIBLING}, KEY, "evict", "after_unlinks", "fail")
    for field, value in (
        ("threshold", 0),
        ("declaredAssetBytes", 1),
        ("pauseSeconds", 15),
    ):
        with pytest.raises(hil.HilValidationError):
            hil.validate_arm_response(
                {**arm, field: value},
                KEY,
                "evict",
                "after_unlinks",
                "fail",
                threshold=1,
                declared_asset_bytes=0,
                pause_seconds=0,
            )
    with pytest.raises(hil.HilValidationError):
        hil.validate_fixture_response({**fixture, "changed": 1}, KEY, SIBLING, "preservation_set", "staged")
    with pytest.raises(hil.HilValidationError):
        hil.validate_inspect_response({**inspect, "entries": [{**inspect["entries"][0], "bytes": False}]}, KEY, SIBLING)


@pytest.mark.parametrize(
    "scenario,expected",
    ((scenario, scenario in {
        "evict-after-unlinks-fail",
        "evict-before-rmdir-fail",
    }) for scenario in (
        "evict-before-first-unlink-fail",
        "evict-after-unlinks-fail",
        "evict-before-rmdir-fail",
        "evict-after-unlinks-sd-removal",
        "sync-before-download-write-no-space",
        "sync-after-download-bytes-no-space",
        "sync-before-checksum-corrupt-staging",
        "sync-before-commit-rename-fail",
        "sync-before-commit-rename-power-loss",
    )),
)
def test_recovery_scenario_is_allowed_only_for_partial_eviction(scenario, expected):
    hil = load_script("lesson_studio_task14_hil_storage.py")
    assert hil.PARTIAL_EVICTION_SCENARIOS == frozenset({
        "evict-after-unlinks-fail",
        "evict-before-rmdir-fail",
    })
    assert (scenario in hil.PARTIAL_EVICTION_SCENARIOS) is expected


def test_recovery_contract_accepts_only_exact_converged_eviction_response():
    hil = load_script("lesson_studio_task14_hil_storage.py")
    response = exact_recovery_response()

    assert hil.validate_partial_eviction_retry(response, KEY) == response
    assert hil.recovery_not_attempted() == {
        "attempted": False,
        "operation": None,
        "reason": None,
        "response": None,
        "inspection": None,
    }


@pytest.mark.parametrize(
    "candidate",
    (
        {key: value for key, value in exact_recovery_response().items() if key != "fileCount"},
        exact_recovery_response(extra=None),
        exact_recovery_response(cacheKey=SIBLING),
        exact_recovery_response(fileCount=1),
        exact_recovery_response(notFound=True),
        exact_recovery_response(evicted=False),
        exact_recovery_response(evicted=1),
        exact_recovery_response(notFound=0),
        exact_recovery_response(fileCount=False),
        exact_recovery_response(
            status="partial_evict_recovery_required",
            reason="partial_evict_recovery_required",
        ),
        exact_recovery_response(status="unlink_failed"),
        exact_recovery_response(reason="unlink_failed"),
    ),
)
def test_recovery_contract_rejects_non_exact_or_false_success_response(candidate):
    hil = load_script("lesson_studio_task14_hil_storage.py")
    with pytest.raises(hil.HilValidationError):
        hil.validate_partial_eviction_retry(candidate, KEY)


@pytest.mark.parametrize(
    "scenario",
    ("evict-after-unlinks-fail", "evict-before-rmdir-fail"),
)
def test_run_scenario_recovery_runs_once_after_partial_inspection_and_preserves_trigger(
    monkeypatch, tmp_path, scenario,
):
    hil, result, client, transport_calls, _calls, published, injected = run_scenario_with_fakes(
        monkeypatch, tmp_path, scenario
    )

    evictions = [call for call in transport_calls if call[0] == hil.TRIGGER_TOOLS["evict"]]
    assert evictions == [
        (hil.TRIGGER_TOOLS["evict"], {"cacheKey": KEY}, 75),
        (hil.TRIGGER_TOOLS["evict"], {"cacheKey": KEY}, 75),
    ]
    assert result["events"] == [
        "status-before", "inspect-before", "stage", "arm", "trigger",
        "status-after", "inspect-after", "recovery-trigger",
        "recovery-inspect", "cleanup",
    ]
    assert result["triggerOutcome"] == injected
    assert result["recovery"] == {
        "attempted": True,
        "operation": "evict",
        "reason": "expected_partial_eviction",
        "response": exact_recovery_response(),
        "inspection": preservation_inspection("missing"),
    }
    assert client.cleanup_count == 1
    assert published == [result["recovery"]]


@pytest.mark.parametrize(
    "scenario",
    (
        "evict-before-first-unlink-fail",
        "evict-after-unlinks-sd-removal",
        "sync-before-download-write-no-space",
        "sync-after-download-bytes-no-space",
        "sync-before-checksum-corrupt-staging",
        "sync-before-commit-rename-fail",
        "sync-before-commit-rename-power-loss",
    ),
)
def test_run_scenario_recovery_is_never_attempted_for_non_partial_scenarios(
    monkeypatch, tmp_path, scenario,
):
    _hil, result, client, _transport_calls, _calls, published, injected = run_scenario_with_fakes(
        monkeypatch, tmp_path, scenario
    )

    assert result["recovery"] == {
        "attempted": False,
        "operation": None,
        "reason": None,
        "response": None,
        "inspection": None,
    }
    assert "recovery-trigger" not in result["events"]
    assert "recovery-inspect" not in result["events"]
    assert result["triggerOutcome"] == (None if scenario.endswith("power-loss") else injected)
    assert client.cleanup_count == 1
    assert published == [result["recovery"]]


@pytest.mark.parametrize(
    "recovery_response,recovered_inspection",
    (
        (exact_recovery_response(fileCount=1), None),
        (None, preservation_inspection("missing", protected_sha="b" * 64)),
        (None, preservation_inspection("missing", "missing")),
    ),
)
def test_run_scenario_recovery_failure_prevents_cleanup_and_pass_publication(
    monkeypatch, tmp_path, recovery_response, recovered_inspection,
):
    state = {}
    with pytest.raises(Exception):
        run_scenario_with_fakes(
            monkeypatch,
            tmp_path,
            "evict-after-unlinks-fail",
            recovery_response=recovery_response,
            recovered_inspection=recovered_inspection,
            state=state,
        )

    assert state["client"].cleanup_count == 0
    assert state["published"] == []


def test_failure_evidence_stage_attempt_cleanup_runs_after_remote_mutation_throws(
    monkeypatch, tmp_path,
):
    state = {}
    primary = RuntimeError("remote stage response lost")

    with pytest.raises(RuntimeError) as caught:
        run_scenario_with_fakes(
            monkeypatch,
            tmp_path,
            "evict-before-first-unlink-fail",
            state=state,
            stage_error=primary,
        )

    assert caught.value is primary
    assert state["calls"][:4] == ["status-before", "inspect-before", "stage", "cleanup"]
    assert state["client"].cleanup_count == 1
    assert state["published"] == []


@pytest.mark.parametrize(
    ("scenario", "mutation"),
    (
        ("evict-before-first-unlink-fail", "schema"),
        ("evict-before-first-unlink-fail", "cache-key"),
        ("evict-before-first-unlink-fail", "outcome"),
        ("sync-before-download-write-no-space", "oversized"),
    ),
)
def test_failure_evidence_trigger_capture_requires_valid_bounded_outcome(
    monkeypatch, tmp_path, scenario, mutation,
):
    state = {}
    if mutation == "schema":
        response = {"cacheKey": KEY}
    elif mutation == "cache-key":
        response = {**eviction_result("unlink_failed", 0), "cacheKey": "foreign-cache"}
    elif mutation == "outcome":
        response = eviction_result("evicted", 1, evicted=True)
    else:
        response = failed_sync_result()
        response["files"][0]["path"] = "x" * (300 * 1024)

    with pytest.raises(Exception):
        run_scenario_with_fakes(
            monkeypatch,
            tmp_path,
            scenario,
            injected_response=response,
            state=state,
        )

    assert len(state["failure_evidence"]) == 1
    assert state["failure_evidence"][0]["last_responses"]["trigger"] is None
    assert state["published"] == []


def test_failure_evidence_oversized_valid_inspection_stays_null_and_quarantines(
    monkeypatch, tmp_path,
):
    state = {}
    oversized = preservation_inspection("missing", "missing")
    oversized["entries"].append({
        "label": "protected/" + "x" * (300 * 1024),
        "nodeType": "missing",
        "bytes": 0,
        "sha256": "",
    })

    with pytest.raises(Exception):
        run_scenario_with_fakes(
            monkeypatch,
            tmp_path,
            "evict-before-first-unlink-fail",
            state=state,
            inspect_before_response=oversized,
        )

    evidence = state["failure_evidence"][0]
    assert evidence["phase"] == "inspect"
    assert evidence["last_responses"]["inspectBefore"] is None
    assert len(json.dumps(evidence["last_responses"]).encode()) < 256 * 1024


@pytest.mark.parametrize("response_name", ("recovery", "cleanup"))
def test_failure_evidence_non_trigger_bounded_rejection_keeps_slot_null_and_quarantines(
    monkeypatch, tmp_path, response_name,
):
    state = {}
    scenario = (
        "evict-after-unlinks-fail"
        if response_name == "recovery"
        else "evict-before-first-unlink-fail"
    )

    with pytest.raises(Exception):
        run_scenario_with_fakes(
            monkeypatch,
            tmp_path,
            scenario,
            state=state,
            bounded_rejection=response_name,
        )

    evidence = state["failure_evidence"][0]
    assert evidence["last_responses"][response_name] is None
    assert len(json.dumps(evidence["last_responses"]).encode()) < 256 * 1024


def test_failure_evidence_recorder_rejects_aggregate_context_over_cap():
    hil = load_script("lesson_studio_task14_hil_storage.py")
    responses = {key: None for key in hil.FAILURE_RESPONSE_KEYS}

    def large_inspection(suffix):
        value = {
            "cacheKey": KEY,
            "siblingCacheKey": SIBLING,
            "status": "inspected",
            "truncated": False,
            "entries": [{
                "label": f"protected/{suffix}-" + "x" * (150 * 1024),
                "nodeType": "missing",
                "bytes": 0,
                "sha256": "",
            }],
        }
        return hil.validate_inspect_response(value, KEY, SIBLING)

    inspect_before = large_inspection("before")
    inspect_after = large_inspection("after")
    assert len(hil._bounded_json_bytes(inspect_before, ())) < hil.MAX_FAILURE_JSON_BYTES
    assert len(hil._bounded_json_bytes(inspect_after, ())) < hil.MAX_FAILURE_JSON_BYTES

    hil.record_validated_failure_response(
        responses, "inspectBefore", inspect_before
    )
    with pytest.raises(hil.HilValidationError, match="failure JSON exceeds bounded size"):
        hil.record_validated_failure_response(
            responses, "inspectAfter", inspect_after
        )

    assert tuple(responses) == hil.FAILURE_RESPONSE_KEYS
    assert responses["inspectBefore"] is inspect_before
    assert responses["inspectAfter"] is None
    assert len(hil._bounded_json_bytes(responses, ())) <= hil.MAX_FAILURE_JSON_BYTES


@pytest.mark.parametrize(
    "scenario",
    ("evict-after-unlinks-fail", "evict-before-rmdir-fail"),
)
def test_run_scenario_recovery_rejects_wrong_initial_outcome_before_retry_or_cleanup(
    monkeypatch, tmp_path, scenario,
):
    state = {}
    with pytest.raises(Exception):
        run_scenario_with_fakes(
            monkeypatch,
            tmp_path,
            scenario,
            injected_response=eviction_result("unlink_failed", 0),
            state=state,
        )

    evictions = [
        call for call in state["transport_calls"]
        if call[0].endswith("evict_cache_key")
    ]
    assert evictions == [
        ("self.lesson_assets.evict_cache_key", {"cacheKey": KEY}, 75)
    ]
    assert state["client"].cleanup_count == 0
    assert state["published"] == []


def test_raw_mcp_wrapper_parses_only_exact_internal_response_and_redacts_credentials():
    hil = load_script("lesson_studio_task14_hil_storage.py")
    arm = exact_arm()
    wrapper = {"data": {"called": True, "result": json.dumps(arm)}}
    assert hil.parse_internal_mcp_response(wrapper) == arm
    for invalid in (
        {"data": {"called": True, "result": json.dumps(arm)}, "extra": 1},
        {"data": {"called": 1, "result": json.dumps(arm)}},
        {"data": {"called": False, "result": json.dumps(arm)}},
    ):
        with pytest.raises(hil.HilValidationError):
            hil.parse_internal_mcp_response(invalid)
    secret = "mint-secret-value"
    rendered = hil.redact_text(f"Authorization: Bearer aaa.bbb.ccc {secret} /Users/private/key", (secret,))
    assert secret not in rendered
    assert "aaa.bbb.ccc" not in rendered
    assert "/Users/private/key" not in rendered


def test_redaction_removes_complete_authorization_and_bearer_credentials():
    hil = load_script("lesson_studio_task14_hil_storage.py")
    secret = "opaque-secret-without-jwt-shape"
    rendered = hil.redact_text(
        f"Authorization: Bearer {secret}\n"
        f"authorization=Basic {secret}\n"
        f"proxy-authorization: Bearer {secret}\n"
        f"standalone Bearer {secret}\n"
        f"X-Mint-Secret: {secret}\n"
    )
    assert secret not in rendered
    assert "Bearer" not in rendered
    assert "Basic" not in rendered


def test_checkpoint_polling_is_bounded_and_requires_exact_marker():
    hil = load_script("lesson_studio_task14_hil_storage.py")
    chunks = iter([
        "noise\n",
        "HIL_STORAGE_CHECKPOINT_REACHED operation=evict checkpoint=after_unlinks "
        f"cache_key={KEY} count=1 reached_sequence=2\n",
    ])
    now = iter([0.0, 0.1, 0.2, 0.3])
    text = hil.poll_checkpoint(
        lambda: next(chunks, ""),
        operation="evict",
        checkpoint="after_unlinks",
        cache_key=KEY,
        expected_count=1,
        timeout_seconds=1,
        monotonic=lambda: next(now),
        sleep=lambda _seconds: None,
    )
    assert "HIL_STORAGE_CHECKPOINT_REACHED" in text
    with pytest.raises(hil.HilTimeoutError, match="checkpoint marker timeout"):
        hil.poll_checkpoint(
            lambda: "foreign marker",
            operation="sync",
            checkpoint="before_commit_rename",
            cache_key=KEY,
            expected_count=0,
            timeout_seconds=1,
            monotonic=iter([0.0, 2.0]).__next__,
            sleep=lambda _seconds: None,
        )


def test_ordinary_and_power_loss_layouts_are_exact_and_checksums_written_last(tmp_path, monkeypatch):
    hil = load_script("lesson_studio_task14_hil_storage.py")
    ordinary = hil.scenario_artifact_names(power_loss=False)
    power = hil.scenario_artifact_names(power_loss=True)
    assert ordinary == (
        "command.txt", "serial.log", "server.log", "timeline.log",
        "build-manifest.json", "build-manifest.sha256", "status-before.json",
        "inspect-before.json", "stage-response.json", "arm-response.json",
        "trigger-response.json", "status-after.json", "inspect-after.json",
        "cleanup-response.json", "recovery-response.json", "result.json", "evidence.json",
        "validator-exit-code.txt", "SHA256SUMS",
    )
    assert "recovery-response.json" in power
    assert "trigger-response.json" not in power
    for required in (
        "checkpoint-reached-utc.txt", "power-removed-utc.txt",
        "reboot-serial.log", "post-reboot-inspect.json",
    ):
        assert required in power
    directory = tmp_path / "scenario"
    writes = []
    original = hil.atomic_write_bytes

    def record(path, data):
        writes.append(Path(path).name)
        return original(path, data)

    monkeypatch.setattr(hil, "atomic_write_bytes", record)
    payloads = {name: b"x\n" for name in ordinary if name != "SHA256SUMS"}
    hil.finalize_scenario_directory(directory, payloads, power_loss=False)
    assert writes[-1] == "SHA256SUMS"
    assert tuple(sorted(path.name for path in directory.iterdir())) == tuple(sorted(ordinary))
    assert "mint-secret" not in (directory / "command.txt").read_text()


def test_hil_storage_scenario_publication_uses_hidden_staging_and_requires_validator_pass(
    tmp_path, monkeypatch,
):
    hil = load_script("lesson_studio_task14_hil_storage.py")
    final = tmp_path / "evict-after-unlinks-fail"
    expected = hil.scenario_artifact_names(power_loss=False)
    payloads = {name: b"x\n" for name in expected if name != "SHA256SUMS"}
    payloads["recovery-response.json"] = hil.json_bytes({"attempted": True})
    fsynced = []

    def validator(command, **_kwargs):
        staging = Path(command[command.index("--evidence-dir") + 1])
        output = Path(command[command.index("--output") + 1])
        assert staging.parent == final.parent
        assert staging.name.startswith(f".{final.name}.staging-")
        assert not final.exists()
        output.write_text(json.dumps({
            "scenario": "evict-after-unlinks-fail",
            "status": "PASS",
            "validationErrors": [],
        }) + "\n")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(hil.subprocess, "run", validator)
    monkeypatch.setattr(hil, "_fsync_directory", lambda path: fsynced.append(Path(path)))
    published = hil.publish_validated_scenario_directory(
        final,
        payloads,
        scenario="evict-after-unlinks-fail",
        power_loss=False,
        validator_script=tmp_path / "validator.py",
    )

    assert published == final
    assert {path.name for path in final.iterdir()} == set(expected)
    assert json.loads((final / "evidence.json").read_text())["status"] == "PASS"
    assert (final / "validator-exit-code.txt").read_bytes() == b"0\n"
    assert hil._scenario_checksums(final, expected)
    assert fsynced[-2:] == [next(path for path in fsynced if path.name.startswith(f".{final.name}.staging-")), final.parent]
    assert not list(tmp_path.glob(f".{final.name}.staging-*"))

    failed = tmp_path / "evict-before-rmdir-fail"

    def rejecting_validator(command, **_kwargs):
        output = Path(command[command.index("--output") + 1])
        output.write_text(json.dumps({
            "scenario": "evict-before-rmdir-fail",
            "status": "NOT_PASS",
            "validationErrors": ["rejected"],
        }) + "\n")
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(hil.subprocess, "run", rejecting_validator)
    with pytest.raises(hil.HilValidationError, match="validator failed"):
        hil.publish_validated_scenario_directory(
            failed,
            payloads,
            scenario="evict-before-rmdir-fail",
            power_loss=False,
            validator_script=tmp_path / "validator.py",
        )
    assert not failed.exists()
    assert not list(tmp_path.glob(f".{failed.name}.staging-*"))


def _publication_payloads(hil, *, power_loss):
    return {
        name: b"x\n"
        for name in hil.scenario_artifact_names(power_loss=power_loss)
        if name != "SHA256SUMS"
    }


def _passing_publication_validator(scenario, mutate=None):
    def validator(command, **_kwargs):
        staging = Path(command[command.index("--evidence-dir") + 1])
        if mutate is not None:
            mutate(staging)
        output = Path(command[command.index("--output") + 1])
        output.write_text(json.dumps({
            "scenario": scenario,
            "status": "PASS",
            "validationErrors": [],
        }) + "\n")
        return SimpleNamespace(returncode=0)

    return validator


@pytest.mark.parametrize(
    "failure_point",
    ("finalize", "validator", "checksums", "verify", "staging-fsync", "rename", "parent-fsync"),
)
def test_hil_storage_publication_failure_rolls_back_staging_and_final(
    tmp_path, monkeypatch, failure_point,
):
    hil = load_script("lesson_studio_task14_hil_storage.py")
    final = tmp_path / "evict-after-unlinks-fail"
    payloads = _publication_payloads(hil, power_loss=False)
    marker = RuntimeError(f"failure-at-{failure_point}")
    monkeypatch.setattr(
        hil.subprocess,
        "run",
        _passing_publication_validator("evict-after-unlinks-fail"),
    )

    if failure_point == "finalize":
        monkeypatch.setattr(hil, "finalize_scenario_directory", lambda *_a, **_k: (_ for _ in ()).throw(marker))
    elif failure_point == "validator":
        monkeypatch.setattr(hil.subprocess, "run", lambda *_a, **_k: (_ for _ in ()).throw(marker))
    elif failure_point == "checksums":
        monkeypatch.setattr(hil, "_rewrite_scenario_checksums", lambda *_a, **_k: (_ for _ in ()).throw(marker))
    elif failure_point == "verify":
        monkeypatch.setattr(hil, "_scenario_checksums", lambda *_a, **_k: (_ for _ in ()).throw(marker))
    elif failure_point in {"staging-fsync", "parent-fsync"}:
        calls = 0

        def fail_fsync(_path):
            nonlocal calls
            calls += 1
            target = 1 if failure_point == "staging-fsync" else 2
            if calls == target:
                raise marker

        monkeypatch.setattr(hil, "_fsync_directory", fail_fsync)
    elif failure_point == "rename":
        monkeypatch.setattr(hil, "_rename_directory_noreplace", lambda *_a, **_k: (_ for _ in ()).throw(marker))

    with pytest.raises(RuntimeError, match=f"failure-at-{failure_point}") as captured:
        hil.publish_validated_scenario_directory(
            final,
            payloads,
            scenario="evict-after-unlinks-fail",
            power_loss=False,
            validator_script=tmp_path / "validator.py",
        )

    assert captured.value is marker
    assert not os.path.lexists(final)
    assert not list(tmp_path.glob(f".{final.name}.staging-*"))


@pytest.mark.parametrize("preexisting_root", (False, True))
def test_failure_evidence_publication_failure_removes_only_new_empty_pass_root(
    tmp_path, monkeypatch, preexisting_root,
):
    hil = load_script("lesson_studio_task14_hil_storage.py")
    pass_root = tmp_path / "pass-root"
    if preexisting_root:
        pass_root.mkdir()
    final = pass_root / "evict-after-unlinks-fail"
    marker = RuntimeError("publication failed before final publish")
    monkeypatch.setattr(
        hil,
        "finalize_scenario_directory",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(marker),
    )

    with pytest.raises(RuntimeError) as caught:
        hil.publish_validated_scenario_directory(
            final,
            _publication_payloads(hil, power_loss=False),
            scenario="evict-after-unlinks-fail",
            power_loss=False,
            validator_script=tmp_path / "validator.py",
        )

    assert caught.value is marker
    assert pass_root.exists() is preexisting_root
    if preexisting_root:
        assert not any(pass_root.iterdir())


@pytest.mark.parametrize("preexisting_root", (False, True))
def test_failure_evidence_staging_allocation_failure_cleans_only_new_pass_root(
    tmp_path, monkeypatch, preexisting_root,
):
    hil = load_script("lesson_studio_task14_hil_storage.py")
    pass_root = tmp_path / "pass-root"
    if preexisting_root:
        pass_root.mkdir()
    final = pass_root / "evict-after-unlinks-fail"
    marker = OSError("staging allocation failed")
    monkeypatch.setattr(
        hil.tempfile,
        "mkdtemp",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(marker),
    )

    with pytest.raises(OSError) as caught:
        hil.publish_validated_scenario_directory(
            final,
            _publication_payloads(hil, power_loss=False),
            scenario="evict-after-unlinks-fail",
            power_loss=False,
            validator_script=tmp_path / "validator.py",
        )

    assert caught.value is marker
    assert pass_root.exists() is preexisting_root
    if preexisting_root:
        assert not any(pass_root.iterdir())


def test_failure_evidence_scenario_publication_rejects_post_validation_root_swap(
    tmp_path,
):
    hil = load_script("lesson_studio_task14_hil_storage.py")
    pass_root = tmp_path / "pass"
    failure_root = tmp_path / "failures"
    original_root = tmp_path / "pass-original"
    pass_root.mkdir()
    failure_root.mkdir()
    arguments = SimpleNamespace(
        evidence_dir=pass_root,
        failure_evidence_dir=failure_root,
    )
    hil.validate_run_roots(arguments)
    pass_root.rename(original_root)
    pass_root.symlink_to(failure_root, target_is_directory=True)

    with pytest.raises(hil.HilValidationError, match="changed during publication"):
        hil.publish_validated_scenario_directory(
            pass_root / "evict-after-unlinks-fail",
            _publication_payloads(hil, power_loss=False),
            scenario="evict-after-unlinks-fail",
            power_loss=False,
            validator_script=tmp_path / "validator.py",
            root_identity=arguments._pass_evidence_root_identity,
        )

    assert not any(failure_root.iterdir())
    assert not any(original_root.iterdir())


def test_failure_evidence_bundle_publication_rejects_post_validation_root_swap(
    tmp_path,
):
    hil = load_script("lesson_studio_task14_hil_storage.py")
    pass_root = tmp_path / "pass"
    failure_root = tmp_path / "failures"
    replacement = tmp_path / "replacement"
    original_failure = tmp_path / "failures-original"
    pass_root.mkdir()
    failure_root.mkdir()
    replacement.mkdir()
    arguments = SimpleNamespace(
        evidence_dir=pass_root,
        failure_evidence_dir=failure_root,
    )
    hil.validate_run_roots(arguments)
    failure_root.rename(original_failure)
    failure_root.symlink_to(replacement, target_is_directory=True)

    with pytest.raises(hil.HilValidationError, match="changed during publication"):
        hil.write_failure_evidence(
            failure_root,
            scenario=hil.HIL_STORAGE_SCENARIOS[0],
            phase="setup",
            utc_start="2026-07-17T01:02:03.000000Z",
            utc_failure="2026-07-17T01:02:04.000000Z",
            completed_events=[],
            build_identity=None,
            last_responses={key: None for key in hil.FAILURE_RESPONSE_KEYS},
            command="command\n",
            serial_log="",
            server_log="",
            timeline_log="",
            root_identity=arguments._failure_evidence_root_identity,
        )

    assert not any(replacement.iterdir())
    assert not any(original_failure.iterdir())


@pytest.mark.parametrize("power_loss", (False, True))
@pytest.mark.parametrize("extra_kind", ("file", "directory", "symlink"))
def test_hil_storage_post_validator_layout_rejects_extra_entry(
    tmp_path, monkeypatch, power_loss, extra_kind,
):
    hil = load_script("lesson_studio_task14_hil_storage.py")
    scenario = hil.POWER_LOSS_SCENARIO if power_loss else "evict-after-unlinks-fail"
    final = tmp_path / scenario
    payloads = _publication_payloads(hil, power_loss=power_loss)

    def add_extra(staging):
        extra = staging / "validator-extra"
        if extra_kind == "file":
            extra.write_text("extra\n")
        elif extra_kind == "directory":
            extra.mkdir()
        else:
            extra.symlink_to("missing-target")

    monkeypatch.setattr(
        hil.subprocess,
        "run",
        _passing_publication_validator(scenario, add_extra),
    )
    with pytest.raises(hil.HilValidationError, match="layout"):
        hil.publish_validated_scenario_directory(
            final,
            payloads,
            scenario=scenario,
            power_loss=power_loss,
            validator_script=tmp_path / "validator.py",
        )
    assert not os.path.lexists(final)
    assert not list(tmp_path.glob(f".{final.name}.staging-*"))


def test_hil_storage_noreplace_collision_rejects_broken_symlink_and_directory_race(
    tmp_path, monkeypatch,
):
    hil = load_script("lesson_studio_task14_hil_storage.py")
    payloads = _publication_payloads(hil, power_loss=False)
    monkeypatch.setattr(
        hil.subprocess,
        "run",
        _passing_publication_validator("evict-after-unlinks-fail"),
    )
    broken = tmp_path / "broken-collision"
    broken.symlink_to("missing-target")
    with pytest.raises(hil.HilValidationError, match="already exists"):
        hil.publish_validated_scenario_directory(
            broken,
            payloads,
            scenario="evict-after-unlinks-fail",
            power_loss=False,
            validator_script=tmp_path / "validator.py",
        )
    assert broken.is_symlink()

    raced = tmp_path / "raced-collision"
    original_rename = hil._rename_directory_noreplace

    def create_destination_then_publish(source, destination):
        destination.mkdir()
        return original_rename(source, destination)

    monkeypatch.setattr(hil, "_rename_directory_noreplace", create_destination_then_publish)
    with pytest.raises(hil.HilValidationError, match="already exists"):
        hil.publish_validated_scenario_directory(
            raced,
            payloads,
            scenario="evict-after-unlinks-fail",
            power_loss=False,
            validator_script=tmp_path / "validator.py",
        )
    assert raced.is_dir()
    assert not any(raced.iterdir())
    assert not list(tmp_path.glob(f".{raced.name}.staging-*"))


def test_power_loss_classification_requires_response_absence_reboot_clear_and_retry():
    hil = load_script("lesson_studio_task14_hil_storage.py")
    result = {
        "powerLoss": True,
        "checkpointReached": True,
        "triggerResponseAbsent": True,
        "successMarkerBeforeLoss": False,
        "rebootCaptured": True,
        "postRebootStatus": exact_status("idle"),
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
    assert hil.validate_power_loss_result(result) == result
    for field, invalid in (
        ("triggerResponseAbsent", False),
        ("successMarkerBeforeLoss", True),
        ("rebootCaptured", False),
        ("retryStatus", "failed"),
    ):
        with pytest.raises(hil.HilValidationError):
            hil.validate_power_loss_result({**result, field: invalid})
    stale_idle = {**exact_status("idle"), "operation": "sync"}
    with pytest.raises(hil.HilValidationError):
        hil.validate_power_loss_result({**result, "postRebootStatus": stale_idle})

    for earlier, later in (
        ("utcStart", "checkpointReachedUtc"),
        ("checkpointReachedUtc", "powerCutBoundaryUtc"),
        ("powerCutBoundaryUtc", "disconnectObservedUtc"),
        ("powerRemovalConfirmedUtc", "utcEnd"),
    ):
        with pytest.raises(hil.HilValidationError):
            hil.validate_power_loss_result({**result, later: result[earlier]})


def test_power_loss_result_is_validated_only_after_complete_assembly():
    hil = load_script("lesson_studio_task14_hil_storage.py")
    base = {
        "utcStart": "2026-07-17T00:00:00Z",
        "utcEnd": "2026-07-17T00:00:03Z",
    }
    power_data = {
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
        "checkpointReachedUtc": "2026-07-17T00:00:00.500000Z",
        "powerCutBoundaryUtc": "2026-07-17T00:00:01Z",
        "disconnectObservedUtc": "2026-07-17T00:00:01.500000Z",
        "powerRemovalConfirmedUtc": "2026-07-17T00:00:02Z",
        "postRebootStatus": exact_status("idle"),
    }

    result = hil.finalize_power_loss_result(base, power_data)

    assert result["checkpointReachedUtc"] == power_data["checkpointReachedUtc"]
    assert result["armClearedAfterReboot"] is True

    for field, invalid in (
        ("checkpointReachedUtc", None),
        ("checkpointReachedUtc", "not-a-timestamp"),
        ("checkpointReachedUtc", "2026-07-17T00:00:00.500000+00:00"),
        ("checkpointReachedUtc", "2026-07-16T23:59:59Z"),
        ("powerCutBoundaryUtc", "2026-07-17T00:00:00.250000Z"),
        ("disconnectObservedUtc", "2026-07-17T00:00:00.750000Z"),
        ("powerRemovalConfirmedUtc", "2026-07-17T00:00:01.250000Z"),
        ("utcEnd", "2026-07-17T00:00:01.750000Z"),
    ):
        with pytest.raises(hil.HilValidationError):
            hil.validate_power_loss_result({**result, field: invalid})


def test_cleanup_status_inspect_order_and_sequence_validation():
    hil = load_script("lesson_studio_task14_hil_storage.py")
    events = ["status-before", "inspect-before", "stage", "arm", "trigger", "status-after", "inspect-after", "cleanup"]
    assert hil.validate_event_order(events) == events
    with pytest.raises(hil.HilValidationError):
        hil.validate_event_order(events[:-2] + ["cleanup", "inspect-after"])
    assert hil.validate_sequences(exact_arm(), exact_status()) == {"arm": 1, "reached": 2, "consumed": 3}
    with pytest.raises(hil.HilValidationError):
        hil.validate_sequences(exact_arm(), {**exact_status(), "reachedSequence": 1})
    for field, value in (
        ("cacheKey", SIBLING),
        ("operation", "sync"),
        ("checkpoint", "before_commit_rename"),
        ("action", "pause"),
        ("threshold", 0),
        ("declaredAssetBytes", 1),
        ("pauseSeconds", 15),
        ("armSequence", 2),
    ):
        with pytest.raises(hil.HilValidationError):
            hil.validate_sequences(exact_arm(), {**exact_status(), field: value})


def test_live_connection_attestation_binds_route_uuid_to_resolved_mac():
    hil = load_script("lesson_studio_task14_hil_storage.py")
    expected_mac = "28:84:85:85:1a:80"
    route_uuid = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return json.dumps(
                {
                    "connections": 1,
                    "alarms": 0,
                    "counters": {
                        "forwarder.dropped_events_total": 0,
                        "safety_forwarder.dropped_events_total": 0,
                    },
                    "devices": [{
                        "deviceId": expected_mac,
                        "clientId": route_uuid,
                        "forwarderDroppedEventsTotal": 0,
                        "safetyForwarderDroppedEventsTotal": 0,
                        "alarm": None,
                    }],
                }
            ).encode()

    identity = hil.attest_live_connection(
        "http://127.0.0.1:8003", route_uuid, expected_mac,
        open_url=lambda *_args, **_kwargs: Response(),
    )
    assert identity == {"deviceId": expected_mac, "clientId": route_uuid}

    for claimed_mac, devices in (
        ("28:84:85:85:1a:81", None),
        (expected_mac, []),
        (expected_mac, [
            {"deviceId": expected_mac, "clientId": route_uuid},
            {"deviceId": "28:84:85:85:1a:81", "clientId": route_uuid},
        ]),
    ):
        with pytest.raises(hil.HilValidationError):
            hil.attest_live_connection(
                "http://127.0.0.1:8003", route_uuid, claimed_mac,
                open_url=lambda *_args, devices=devices, **_kwargs: types.SimpleNamespace(
                    read=lambda: json.dumps({
                        "connections": len(devices or [{"deviceId": expected_mac, "clientId": route_uuid}]),
                        "devices": devices if devices is not None else [
                            {"deviceId": expected_mac, "clientId": route_uuid}
                        ],
                    }).encode()
                ),
            )


def test_build_identity_recomputes_task6_artifacts_defaults_and_flat_binding(tmp_path):
    identity = load_script("lesson_studio_task14_build_identity.py")
    manifest_path, _manifest, _paths = task6_manifest(tmp_path)
    binding = identity.load_build_identity(manifest_path, expected_profile="hil")
    assert binding == {
        "sourceCommit": "a" * 40,
        "profile": "hil",
        "configEnabled": True,
        "sdkconfigSha256": sha256(manifest_path.parent / "sdkconfig"),
        "binarySha256": sha256(manifest_path.parent / "xiaozhi.bin"),
        "elfSha256": sha256(manifest_path.parent / "xiaozhi.elf"),
        "mapSha256": sha256(manifest_path.parent / "xiaozhi.map"),
        "archiveSha256": sha256(manifest_path.parent / "esp-idf/main/libmain.a"),
        "binaryBytes": len(b"hil"),
        "appPartitionFreeBytes": 4_000_000 - len(b"hil"),
    }


def test_build_identity_rejects_tamper_bool_traversal_symlink_and_foreign_profile(tmp_path):
    identity = load_script("lesson_studio_task14_build_identity.py")
    manifest_path, manifest, paths = task6_manifest(tmp_path)
    paths["bin"].write_bytes(b"tampered")
    with pytest.raises(identity.BuildIdentityError):
        identity.load_build_identity(manifest_path, expected_profile="hil")

    manifest_path, manifest, _paths = task6_manifest(tmp_path / "bool")
    manifest["artifacts"]["bin"]["bytes"] = True
    manifest_path.write_text(json.dumps(manifest) + "\n")
    (manifest_path.parent / "lesson-storage-hil-build.sha256").write_text(
        f"{sha256(manifest_path)}  {manifest_path.name}\n"
    )
    with pytest.raises(identity.BuildIdentityError):
        identity.load_build_identity(manifest_path, expected_profile="hil")

    manifest_path, manifest, _paths = task6_manifest(tmp_path / "traversal")
    manifest["artifacts"]["bin"]["path"] = "../outside.bin"
    manifest_path.write_text(json.dumps(manifest) + "\n")
    (manifest_path.parent / "lesson-storage-hil-build.sha256").write_text(
        f"{sha256(manifest_path)}  {manifest_path.name}\n"
    )
    with pytest.raises(identity.BuildIdentityError):
        identity.load_build_identity(manifest_path, expected_profile="hil")

    manifest_path, _manifest, paths = task6_manifest(tmp_path / "symlink")
    paths["bin"].unlink()
    os.symlink(manifest_path.parent / "xiaozhi.elf", paths["bin"])
    with pytest.raises(identity.BuildIdentityError):
        identity.load_build_identity(manifest_path, expected_profile="hil")
    with pytest.raises(identity.BuildIdentityError):
        identity.load_build_identity(manifest_path, expected_profile="production")


def test_paired_builds_require_same_source_opposite_profiles_and_different_binaries(tmp_path):
    identity = load_script("lesson_studio_task14_build_identity.py")
    hil_path, _hil, _ = task6_manifest(tmp_path / "hil", profile="hil", binary=b"hil")
    prod_path, _prod, _ = task6_manifest(tmp_path / "prod", profile="production", binary=b"prod")
    pair = identity.validate_build_pair(hil_path, prod_path)
    assert pair["sourceCommit"] == "a" * 40
    assert pair["hil"]["profile"] == "hil"
    assert pair["production"]["profile"] == "production"
    same_path, _same, _ = task6_manifest(tmp_path / "same", profile="production", binary=b"hil")
    with pytest.raises(identity.BuildIdentityError):
        identity.validate_build_pair(hil_path, same_path)
    foreign_path, _foreign, _ = task6_manifest(
        tmp_path / "foreign", profile="production", source_commit="b" * 40, binary=b"prod"
    )
    with pytest.raises(identity.BuildIdentityError):
        identity.validate_build_pair(hil_path, foreign_path)


def test_release_cli_operationally_binds_build_pair_and_order(tmp_path):
    hil_path, _hil, _ = task6_manifest(tmp_path / "hil", profile="hil", binary=b"hil")
    prod_path, _prod, _ = task6_manifest(tmp_path / "prod", profile="production", binary=b"prod")
    script = ROOT / "scripts" / "lesson_studio_task14_build_identity.py"
    identity = load_script("lesson_studio_task14_build_identity.py")
    events = ["hil-flash", "hil-matrix-pass", "production-reflash", "production-attest", "production-soak"]
    ledger_path = tmp_path / "release-ledger"
    hil_identity = identity.load_build_identity(hil_path, expected_profile="hil")
    production_identity = identity.load_build_identity(prod_path, expected_profile="production")
    hil_flash_evidence = _valid_hil_flash_evidence(tmp_path, identity, hil_path)
    evidence_paths = []
    for index, event in enumerate(events):
        evidence = (
            tmp_path / "storage-hil" / "hil-matrix-report.json"
            if event == "hil-matrix-pass"
            else tmp_path / f"{event}.json"
        )
        evidence.parent.mkdir(exist_ok=True)
        payload = release_evidence_payload(
            event,
            hil_identity,
            production_identity,
            ledger_path=ledger_path,
            evidence_root=evidence.parent,
            hil_flash_evidence=hil_flash_evidence,
        )
        evidence.write_text(json.dumps(payload))
        evidence_paths.append(evidence)
        completed = subprocess.run(
            [
                sys.executable, str(script), "release",
                "--ledger", str(ledger_path),
                "--hil-manifest", str(hil_path),
                "--production-manifest", str(prod_path),
                "--event", event,
                "--evidence", str(evidence),
                "--completed-at", f"2026-07-17T00:00:0{index}Z",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
    release_artifact = identity.load_release_ledger(
        ledger_path,
        production_identity=production_identity,
        required_event="production-soak",
    )
    assert [receipt["event"] for receipt in release_artifact["receipts"]] == events
    assert sorted(path.name for path in ledger_path.iterdir()) == [
        "01-hil-flash.json", "02-hil-matrix-pass.json",
        "03-production-reflash.json", "04-production-attest.json",
        "05-production-soak.json", "index.json",
    ]
    first_receipt = ledger_path / "01-hil-flash.json"
    with pytest.raises(identity.BuildIdentityError):
        identity.append_release_receipt(
            ledger_path, hil_path, prod_path,
            event="hil-flash", evidence_path=evidence_paths[0],
            completed_at="2026-07-17T00:00:06Z",
        )
    assert first_receipt.is_file()
    assert identity.load_release_ledger(
        ledger_path,
        production_identity=release_artifact["production"],
        required_event="production-soak",
    ) == release_artifact
    second_receipt = ledger_path / "02-hil-matrix-pass.json"
    tampered = json.loads(second_receipt.read_text())
    tampered["previousReceiptSha256"] = "0" * 64
    second_receipt.chmod(0o644)
    second_receipt.write_text(json.dumps(tampered))
    with pytest.raises(identity.BuildIdentityError):
        identity.load_release_ledger(
            ledger_path,
            production_identity=release_artifact["production"],
            required_event="production-soak",
        )


def test_release_ledger_rejects_skips_and_soak_without_passing_report(tmp_path):
    identity = load_script("lesson_studio_task14_build_identity.py")
    hil_path, _hil, _ = task6_manifest(tmp_path / "hil", profile="hil", binary=b"hil")
    prod_path, _prod, _ = task6_manifest(tmp_path / "prod", profile="production", binary=b"prod")
    ledger = tmp_path / "ledger"
    hil_identity = identity.load_build_identity(hil_path, expected_profile="hil")
    production_identity = identity.load_build_identity(prod_path, expected_profile="production")
    hil_flash_evidence = _valid_hil_flash_evidence(tmp_path, identity, hil_path)
    evidence = tmp_path / "evidence.json"
    evidence.write_text(json.dumps(release_evidence_payload(
        "production-reflash", hil_identity, production_identity
    )))
    with pytest.raises(identity.BuildIdentityError):
        identity.append_release_receipt(
            ledger, hil_path, prod_path,
            event="production-reflash", evidence_path=evidence,
            completed_at="2026-07-17T00:00:00Z",
        )
    for index, event in enumerate(identity.RELEASE_ORDER[:-1]):
        event_evidence = (
            tmp_path / "storage-hil" / "hil-matrix-report.json"
            if event == "hil-matrix-pass"
            else tmp_path / f"{event}.json"
        )
        event_evidence.parent.mkdir(exist_ok=True)
        event_evidence.write_text(json.dumps(release_evidence_payload(
            event, hil_identity, production_identity, ledger_path=ledger,
            evidence_root=event_evidence.parent,
            hil_flash_evidence=hil_flash_evidence,
        )))
        identity.append_release_receipt(
            ledger, hil_path, prod_path,
            event=event, evidence_path=event_evidence,
            completed_at=f"2026-07-17T00:00:0{index}Z",
        )
    failing_soak = tmp_path / "soak.json"
    failing_soak.write_text(json.dumps({
        **release_evidence_payload(
            "production-soak", hil_identity, production_identity,
            ledger_path=ledger,
        ),
        "status": "NOT_PASS",
    }))
    with pytest.raises(identity.BuildIdentityError):
        identity.append_release_receipt(
            ledger, hil_path, prod_path,
            event="production-soak", evidence_path=failing_soak,
            completed_at="2026-07-17T00:00:05Z",
        )


def test_release_ledger_rejects_self_authored_hil_matrix_summary(tmp_path):
    identity = load_script("lesson_studio_task14_build_identity.py")
    hil_path, _hil, _ = task6_manifest(tmp_path / "hil", profile="hil", binary=b"hil")
    prod_path, _prod, _ = task6_manifest(
        tmp_path / "prod", profile="production", binary=b"prod"
    )
    hil_identity = identity.load_build_identity(hil_path, expected_profile="hil")
    production_identity = identity.load_build_identity(
        prod_path, expected_profile="production"
    )
    hil_flash_evidence = _valid_hil_flash_evidence(tmp_path, identity, hil_path)
    ledger = tmp_path / "ledger"
    flash = tmp_path / "hil-flash.json"
    flash.write_text(
        json.dumps(release_evidence_payload(
            "hil-flash", hil_identity, production_identity,
            hil_flash_evidence=hil_flash_evidence,
        ))
    )
    identity.append_release_receipt(
        ledger,
        hil_path,
        prod_path,
        event="hil-flash",
        evidence_path=flash,
        completed_at="2026-07-17T00:00:00Z",
    )
    summary = tmp_path / "self-authored-matrix.json"
    summary.write_text(json.dumps({
        "status": "PASS",
        "event": "hil-matrix-pass",
        "buildIdentity": hil_identity,
        "scenarios": [
            {"scenario": scenario, "status": "PASS"}
            for scenario in identity.HIL_STORAGE_SCENARIOS
        ],
    }))

    with pytest.raises(identity.BuildIdentityError):
        identity.append_release_receipt(
            ledger,
            hil_path,
            prod_path,
            event="hil-matrix-pass",
            evidence_path=summary,
            completed_at="2026-07-17T00:00:01Z",
        )


def test_run_matrix_publishes_aggregate_after_all_scenarios(tmp_path, monkeypatch):
    hil = load_script("lesson_studio_task14_hil_storage.py")
    evidence_root = tmp_path / "matrix"
    failure_root = tmp_path / "failures"
    evidence_root.mkdir()
    failure_root.mkdir()
    manifest = tmp_path / "build.json"
    manifest.write_text("{}")
    calls = []
    preflight_result = {
        "status": "PASS",
        "deviceId": "28:84:85:85:1a:80",
        "deviceUuid": "fce7bec8-8478-4ab4-817f-7b87c41c1f91",
        "connectionIdentity": {
            "deviceId": "28:84:85:85:1a:80",
            "clientId": "fce7bec8-8478-4ab4-817f-7b87c41c1f91",
        },
        "buildIdentity": {"profile": "hil"},
    }

    monkeypatch.setattr(
        hil,
        "preflight",
        lambda _arguments, **_kwargs: (calls.append("preflight"), preflight_result)[1],
    )
    monkeypatch.setattr(
        hil,
        "run_scenario",
        lambda _arguments, scenario: (calls.append(scenario), preflight_result)[1],
    )
    def publish(arguments, observed_preflight):
        assert observed_preflight == preflight_result
        hil.atomic_write_bytes(
            Path(arguments.evidence_dir) / "hil-matrix-report.json",
            b'{"status":"PASS"}\n',
        )

    monkeypatch.setattr(hil, "publish_matrix_report", publish, raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(hil.__file__), "run-matrix",
            "--device-id", "28:84:85:85:1a:80",
            "--device-uuid", "fce7bec8-8478-4ab4-817f-7b87c41c1f91",
            "--serial-port", "/dev/null",
            "--esp-base-url", "http://127.0.0.1:8000",
            "--asset-url", "http://127.0.0.1:8000/asset",
            "--asset-sha256", "d" * 64,
            "--asset-bytes", "1",
            "--build-manifest", str(manifest),
            "--evidence-dir", str(evidence_root),
            "--failure-evidence-dir", str(failure_root),
        ],
    )

    assert hil.main() == 0
    assert calls == ["preflight", *hil.HIL_STORAGE_SCENARIOS]
    assert (evidence_root / "hil-matrix-report.json").is_file()


def _failure_evidence_matrix_arguments(tmp_path):
    pass_root = tmp_path / "pass"
    failure_root = tmp_path / "failures"
    if not os.path.lexists(pass_root):
        pass_root.mkdir()
    if not os.path.lexists(failure_root):
        failure_root.mkdir()
    return SimpleNamespace(
        device_id="28:84:85:85:1a:80",
        device_uuid="fce7bec8-8478-4ab4-817f-7b87c41c1f91",
        serial_port="/dev/null",
        esp_base_url="http://127.0.0.1:8000",
        asset_url="http://127.0.0.1:8000/asset",
        asset_sha256="d" * 64,
        asset_bytes=1,
        build_manifest=tmp_path / "build.json",
        evidence_dir=pass_root,
        failure_evidence_dir=failure_root,
        mint_secret_env="TBOT_DEVICE_MINT_SECRET",
        server_container="unused",
    )


@pytest.mark.parametrize(
    ("failure_point", "expected_phase"),
    (("preflight", "setup"), ("report-unlink", "publication"), ("publish", "publication")),
)
def test_failure_evidence_run_matrix_quarantines_preflight_and_report_failures(
    tmp_path, monkeypatch, failure_point, expected_phase,
):
    hil = load_script("lesson_studio_task14_hil_storage.py")
    arguments = _failure_evidence_matrix_arguments(tmp_path)
    identity = {
        "status": "PASS",
        "deviceId": arguments.device_id,
        "deviceUuid": arguments.device_uuid,
        "connectionIdentity": {
            "deviceId": arguments.device_id,
            "clientId": arguments.device_uuid,
        },
        "buildIdentity": {"profile": "hil"},
    }
    primary = (
        hil.HilValidationError("matrix preflight failed")
        if failure_point == "preflight"
        else OSError(f"matrix {failure_point} failed")
    )
    monkeypatch.setattr(
        hil,
        "preflight",
        lambda _arguments, **_kwargs: (
            (_ for _ in ()).throw(primary) if failure_point == "preflight" else identity
        ),
    )
    monkeypatch.setattr(hil, "run_scenario", lambda *_args: identity)
    monkeypatch.setattr(
        hil,
        "_remove_matrix_report",
        lambda *_args: (
            (_ for _ in ()).throw(primary) if failure_point == "report-unlink" else None
        ),
        raising=False,
    )
    monkeypatch.setattr(
        hil,
        "publish_matrix_report",
        lambda *_args: (
            (_ for _ in ()).throw(primary) if failure_point == "publish" else None
        ),
    )

    with pytest.raises(type(primary)) as caught:
        hil.run_matrix(arguments)

    assert caught.value is primary
    bundles = list(Path(arguments.failure_evidence_dir).iterdir())
    assert len(bundles) == 1
    failure = json.loads((bundles[0] / "failure.json").read_text())
    assert failure["scenario"] == hil.HIL_STORAGE_SCENARIOS[0]
    assert failure["phase"] == expected_phase
    assert failure["errorCode"] == hil.failure_error_code(expected_phase)
    assert not (Path(arguments.evidence_dir) / hil.MATRIX_REPORT_NAME).exists()


def test_failure_evidence_run_matrix_checks_roots_before_hardware(tmp_path, monkeypatch):
    hil = load_script("lesson_studio_task14_hil_storage.py")
    arguments = _failure_evidence_matrix_arguments(tmp_path)
    arguments.failure_evidence_dir = arguments.evidence_dir / "nested"
    arguments.failure_evidence_dir.mkdir()
    calls = []
    monkeypatch.setattr(hil, "preflight", lambda *_args: calls.append("hardware"))

    with pytest.raises(hil.HilValidationError, match="overlap"):
        hil.run_matrix(arguments)

    assert calls == []


def test_publish_matrix_report_binds_scenario_artifacts_and_attended_identity(tmp_path):
    hil = load_script("lesson_studio_task14_hil_storage.py")
    hil_path, _manifest, _paths = task6_manifest(tmp_path / "hil", profile="hil")
    identity = load_script("lesson_studio_task14_build_identity.py").load_build_identity(
        hil_path, expected_profile="hil"
    )
    report_fixture = _matrix_evidence_payload(
        tmp_path / "matrix", identity, storage_layout=True
    )
    preflight_result = {
        key: report_fixture[key]
        for key in ("status", "buildIdentity", "deviceId", "deviceUuid", "connectionIdentity")
    }

    report = hil.publish_matrix_report(
        types.SimpleNamespace(evidence_dir=tmp_path / "matrix"), preflight_result
    )

    report_path = tmp_path / "matrix" / hil.MATRIX_REPORT_NAME
    assert json.loads(report_path.read_text()) == report
    assert report == report_fixture
    assert not list((tmp_path / "matrix").glob(".hil-matrix-report.json.*"))


def test_publish_matrix_report_removes_output_when_inputs_change(tmp_path, monkeypatch):
    hil = load_script("lesson_studio_task14_hil_storage.py")
    calls = 0

    def changing_record(_root, scenario, _preflight):
        nonlocal calls
        calls += 1
        return {"scenario": scenario, "round": (calls - 1) // len(hil.HIL_STORAGE_SCENARIOS)}

    monkeypatch.setattr(hil, "_matrix_scenario_record", changing_record)
    arguments = types.SimpleNamespace(evidence_dir=tmp_path)
    preflight_result = {
        "buildIdentity": {},
        "deviceId": "28:84:85:85:1a:80",
        "deviceUuid": "fce7bec8-8478-4ab4-817f-7b87c41c1f91",
        "connectionIdentity": {
            "deviceId": "28:84:85:85:1a:80",
            "clientId": "fce7bec8-8478-4ab4-817f-7b87c41c1f91",
        },
    }

    with pytest.raises(hil.HilValidationError, match="changed during publication"):
        hil.publish_matrix_report(arguments, preflight_result)

    assert not (tmp_path / hil.MATRIX_REPORT_NAME).exists()


def test_failure_evidence_matrix_publication_rejects_post_validation_root_swap(
    tmp_path,
):
    hil = load_script("lesson_studio_task14_hil_storage.py")
    hil_path, _manifest, _paths = task6_manifest(tmp_path / "hil", profile="hil")
    identity = load_script("lesson_studio_task14_build_identity.py").load_build_identity(
        hil_path, expected_profile="hil"
    )
    matrix_root = tmp_path / "matrix"
    failure_root = tmp_path / "failures"
    original_root = tmp_path / "matrix-original"
    report_fixture = _matrix_evidence_payload(matrix_root, identity, storage_layout=True)
    failure_root.mkdir()
    arguments = SimpleNamespace(
        evidence_dir=matrix_root,
        failure_evidence_dir=failure_root,
    )
    hil.validate_run_roots(arguments)
    matrix_root.rename(original_root)
    matrix_root.symlink_to(failure_root, target_is_directory=True)
    preflight_result = {
        key: report_fixture[key]
        for key in ("status", "buildIdentity", "deviceId", "deviceUuid", "connectionIdentity")
    }

    with pytest.raises(hil.HilValidationError, match="changed during publication"):
        hil.publish_matrix_report(arguments, preflight_result)

    assert not (failure_root / hil.MATRIX_REPORT_NAME).exists()
    assert not (original_root / hil.MATRIX_REPORT_NAME).exists()


def test_failure_evidence_matrix_report_unlink_uses_pinned_root_fd(tmp_path):
    hil = load_script("lesson_studio_task14_hil_storage.py")
    matrix_root = tmp_path / "matrix"
    original_root = tmp_path / "matrix-original"
    replacement_root = tmp_path / "replacement"
    matrix_root.mkdir()
    replacement_root.mkdir()
    original_report = matrix_root / hil.MATRIX_REPORT_NAME
    replacement_report = replacement_root / hil.MATRIX_REPORT_NAME
    original_report.write_bytes(b"original-report\n")
    replacement_report.write_bytes(b"replacement-sentinel\n")
    root_fd = os.open(
        matrix_root,
        os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        matrix_root.rename(original_root)
        matrix_root.symlink_to(replacement_root, target_is_directory=True)
        hil._remove_matrix_report(root_fd)
    finally:
        os.close(root_fd)

    assert not (original_root / hil.MATRIX_REPORT_NAME).exists()
    assert replacement_report.read_bytes() == b"replacement-sentinel\n"


def test_failure_evidence_matrix_publish_swap_never_overwrites_replacement_report(
    tmp_path, monkeypatch,
):
    hil = load_script("lesson_studio_task14_hil_storage.py")
    matrix_root = tmp_path / "matrix"
    failure_root = tmp_path / "failures"
    original_root = tmp_path / "matrix-original"
    matrix_root.mkdir()
    failure_root.mkdir()
    arguments = SimpleNamespace(
        evidence_dir=matrix_root,
        failure_evidence_dir=failure_root,
    )
    hil.validate_run_roots(arguments)
    root_fd = os.open(
        matrix_root,
        os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
    )
    arguments._pass_evidence_root_fd = root_fd
    sentinel = b"replacement-sentinel\n"
    swapped = False

    def swap_before_publish():
        nonlocal swapped
        swapped = True
        matrix_root.rename(original_root)
        matrix_root.mkdir()
        (matrix_root / hil.MATRIX_REPORT_NAME).write_bytes(sentinel)

    monkeypatch.setattr(
        hil, "_matrix_publish_boundary", swap_before_publish, raising=False
    )
    monkeypatch.setattr(
        hil,
        "_matrix_scenario_record",
        lambda _root, scenario, _preflight: {"scenario": scenario},
    )
    preflight = {
        "buildIdentity": {}, "deviceId": "device", "deviceUuid": "uuid",
        "connectionIdentity": {},
    }
    try:
        with pytest.raises(hil.HilValidationError, match="changed during publication"):
            hil.publish_matrix_report(arguments, preflight)
    finally:
        os.close(root_fd)

    assert swapped is True
    assert (matrix_root / hil.MATRIX_REPORT_NAME).read_bytes() == sentinel


def test_failure_evidence_matrix_nested_scenario_cannot_repin_replaced_root(
    tmp_path, monkeypatch,
):
    hil = load_script("lesson_studio_task14_hil_storage.py")
    arguments = _failure_evidence_matrix_arguments(tmp_path)
    original_root = tmp_path / "pass-original"

    def preflight(_arguments, **_kwargs):
        Path(arguments.evidence_dir).rename(original_root)
        Path(arguments.evidence_dir).mkdir()
        return {
            "status": "PASS", "deviceId": "device", "deviceUuid": "uuid",
            "connectionIdentity": {}, "buildIdentity": {},
        }

    monkeypatch.setattr(hil, "preflight", preflight)
    monkeypatch.setattr(
        hil,
        "run_scenario",
        lambda nested_arguments, _scenario: hil.validate_run_roots(nested_arguments),
    )

    with pytest.raises(hil.HilValidationError, match="changed during publication"):
        hil.run_matrix(arguments)

    assert not any(Path(arguments.evidence_dir).iterdir())


@pytest.mark.parametrize(
    "mutation",
    (
        "scenario-set", "scenario-extra", "evidence-path", "evidence-hash", "scenario-status",
        "validator", "matrix-build", "matrix-device", "artifact-hash",
        "checksum-manifest", "recovery-missing", "recovery-hash",
        "recovery-content", "recovery-result", "quarantine-path",
        "recovery-semantics", "recovery-empty-entries",
        "recovery-malformed-entry", "recovery-wrong-sibling",
        "recovery-protected-change", "recovery-events-missing",
        "recovery-events-out-of-order", "cleanup-inspection-forgery",
        "final-status-forgery", "control-artifact-forgery",
        "sequence-forgery", "validator-summary",
    ),
)
def test_release_validation_rejects_unbound_hil_matrix_components(tmp_path, mutation):
    identity = load_script("lesson_studio_task14_build_identity.py")
    hil_path, _hil, _ = task6_manifest(tmp_path / "hil", profile="hil", binary=b"hil")
    prod_path, _prod, _ = task6_manifest(
        tmp_path / "prod", profile="production", binary=b"prod"
    )
    pair = identity.validate_build_pair(hil_path, prod_path)
    matrix_root = tmp_path / "storage-hil"
    report = _matrix_evidence_payload(matrix_root, pair["hil"])
    first = report["scenarios"][0]
    if mutation == "scenario-set":
        report["scenarios"].pop()
    elif mutation == "scenario-extra":
        report["scenarios"].append("ignored")
    elif mutation == "evidence-path":
        first["evidencePath"] = "../evidence.json"
    elif mutation == "evidence-hash":
        first["evidenceSha256"] = "0" * 64
    elif mutation == "scenario-status":
        first["status"] = "NOT_PASS"
    elif mutation == "validator":
        first["validatorExitCode"] = 1
    elif mutation == "matrix-build":
        report["buildIdentity"] = {**pair["hil"], "binarySha256": "0" * 64}
    elif mutation == "matrix-device":
        report["deviceId"] = "00:00:00:00:00:00"
        report["connectionIdentity"]["deviceId"] = report["deviceId"]
    elif mutation == "artifact-hash":
        first["artifacts"]["result.json"] = "0" * 64
    elif mutation == "recovery-missing":
        (matrix_root / first["evidencePath"]).parent.joinpath(
            "recovery-response.json"
        ).unlink()
    elif mutation == "recovery-hash":
        first["artifacts"]["recovery-response.json"] = "0" * 64
    elif mutation == "recovery-content":
        recovery_path = (matrix_root / first["evidencePath"]).parent / "recovery-response.json"
        recovery_path.write_text(json.dumps({"attempted": True}))
    elif mutation == "recovery-result":
        scenario_dir = (matrix_root / first["evidencePath"]).parent
        recovery_path = scenario_dir / "recovery-response.json"
        recovery = json.loads(recovery_path.read_text())
        recovery["attempted"] = not recovery["attempted"]
        recovery_path.write_text(json.dumps(recovery))
        first["artifacts"]["recovery-response.json"] = sha256(recovery_path)
        checksum_path = scenario_dir / "SHA256SUMS"
        checksum_path.write_text("".join(
            f"{first['artifacts'][name]}  {name}\n"
            for name in identity.HIL_ORDINARY_ARTIFACTS
            if name != "SHA256SUMS"
        ))
        first["sha256SumsSha256"] = sha256(checksum_path)
    elif mutation == "quarantine-path":
        first["evidencePath"] = (
            "../storage-hil-failures/evict-before-first-unlink-fail/evidence.json"
        )
    elif mutation == "recovery-semantics":
        record = report["scenarios"][1]
        scenario_dir = (matrix_root / record["evidencePath"]).parent
        recovery_path = scenario_dir / "recovery-response.json"
        recovery = json.loads(recovery_path.read_text())
        recovery["operation"] = "sync"
        recovery_path.write_text(json.dumps(recovery))
        result_path = scenario_dir / "result.json"
        result = json.loads(result_path.read_text())
        result["recovery"] = recovery
        result_path.write_text(json.dumps(result))
        for name in ("recovery-response.json", "result.json"):
            record["artifacts"][name] = sha256(scenario_dir / name)
        checksum_path = scenario_dir / "SHA256SUMS"
        checksum_path.write_text("".join(
            f"{record['artifacts'][name]}  {name}\n"
            for name in identity.HIL_ORDINARY_ARTIFACTS
            if name != "SHA256SUMS"
        ))
        record["sha256SumsSha256"] = sha256(checksum_path)
    elif mutation.startswith("recovery-") and mutation not in {
        "recovery-missing", "recovery-hash", "recovery-content", "recovery-result",
    }:
        record = report["scenarios"][1]
        scenario_dir = (matrix_root / record["evidencePath"]).parent
        recovery_path = scenario_dir / "recovery-response.json"
        recovery = json.loads(recovery_path.read_text())
        result_path = scenario_dir / "result.json"
        result = json.loads(result_path.read_text())
        timeline_path = scenario_dir / "timeline.log"
        if mutation == "recovery-empty-entries":
            recovery["inspection"]["entries"] = []
        elif mutation == "recovery-malformed-entry":
            recovery["inspection"]["entries"][0] = {"label": "broken"}
        elif mutation == "recovery-wrong-sibling":
            recovery["inspection"]["siblingCacheKey"] = KEY
        elif mutation == "recovery-protected-change":
            protected = next(
                item for item in recovery["inspection"]["entries"]
                if item["label"] == "lesson-assets/current.json"
            )
            protected["sha256"] = "f" * 64
        else:
            events = list(result["events"])
            if mutation == "recovery-events-missing":
                events.remove("recovery-inspect")
            else:
                left = events.index("recovery-trigger")
                right = events.index("recovery-inspect")
                events[left], events[right] = events[right], events[left]
            result["events"] = events
            timeline_path.write_text("".join(
                f"{index + 1} {event}\n" for index, event in enumerate(events)
            ))
        result["recovery"] = recovery
        recovery_path.write_text(json.dumps(recovery))
        result_path.write_text(json.dumps(result))
        for name in ("recovery-response.json", "result.json", "timeline.log"):
            record["artifacts"][name] = sha256(scenario_dir / name)
        checksum_path = scenario_dir / "SHA256SUMS"
        checksum_path.write_text("".join(
            f"{record['artifacts'][name]}  {name}\n"
            for name in identity.HIL_ORDINARY_ARTIFACTS
            if name != "SHA256SUMS"
        ))
        record["sha256SumsSha256"] = sha256(checksum_path)
    elif mutation in {
        "cleanup-inspection-forgery", "final-status-forgery",
        "control-artifact-forgery", "sequence-forgery",
    }:
        scenario_dir = (matrix_root / first["evidencePath"]).parent
        result_path = scenario_dir / "result.json"
        result = json.loads(result_path.read_text())
        evidence_path = scenario_dir / "evidence.json"
        evidence = json.loads(evidence_path.read_text())
        changed = {"result.json", "evidence.json"}
        if mutation == "cleanup-inspection-forgery":
            result["cleanupInspection"] = {"status": "forged"}
            evidence["cleanupInspection"] = result["cleanupInspection"]
        elif mutation == "final-status-forgery":
            result["finalStatus"] = {"status": "idle", "armed": True}
            evidence["finalStatus"] = result["finalStatus"]
        elif mutation == "sequence-forgery":
            result["reachedSequence"] = result["armSequence"]
        else:
            arm_path = scenario_dir / "arm-response.json"
            arm_path.write_text(json.dumps({"status": "armed"}))
            changed.add("arm-response.json")
        result_path.write_text(json.dumps(result))
        evidence_path.write_text(json.dumps(evidence))
        for name in changed:
            first["artifacts"][name] = sha256(scenario_dir / name)
        first["evidenceSha256"] = first["artifacts"]["evidence.json"]
        checksum_path = scenario_dir / "SHA256SUMS"
        checksum_path.write_text("".join(
            f"{first['artifacts'][name]}  {name}\n"
            for name in identity.HIL_ORDINARY_ARTIFACTS
            if name != "SHA256SUMS"
        ))
        first["sha256SumsSha256"] = sha256(checksum_path)
    elif mutation == "validator-summary":
        scenario_dir = (matrix_root / first["evidencePath"]).parent
        evidence_path = scenario_dir / "evidence.json"
        evidence_path.write_text(json.dumps({
            "scenario": first["scenario"],
            "status": "PASS",
            "validationErrors": [],
        }))
        first["artifacts"]["evidence.json"] = sha256(evidence_path)
        first["evidenceSha256"] = first["artifacts"]["evidence.json"]
        checksum_path = scenario_dir / "SHA256SUMS"
        checksum_path.write_text("".join(
            f"{first['artifacts'][name]}  {name}\n"
            for name in identity.HIL_ORDINARY_ARTIFACTS
            if name != "SHA256SUMS"
        ))
        first["sha256SumsSha256"] = sha256(checksum_path)
    else:
        checksum = matrix_root / first["sha256SumsPath"]
        checksum.write_text(checksum.read_text() + "invalid\n")
    report_path = matrix_root / "hil-matrix-report.json"
    report_path.write_text(json.dumps(report))

    with pytest.raises(identity.BuildIdentityError):
        identity._validate_hil_matrix(report, pair, report_path)


def test_runbook_releases_generated_hil_matrix_report():
    runbook = (ROOT / "docs" / "lesson-studio-task14-live-matrix.md").read_text()
    matrix_command = runbook.index("lesson_studio_task14_hil_storage.py run-matrix")
    release_command = runbook.index("--event hil-matrix-pass")

    assert matrix_command < release_command
    assert 'test -f "$HIL_MATRIX_REPORT"' in runbook
    assert '--evidence "$HIL_MATRIX_REPORT"' in runbook


def test_runbook_uses_fresh_distinct_hil_roots_and_current_serial_port():
    runbook = (ROOT / "docs" / "lesson-studio-task14-live-matrix.md").read_text()
    matrix = runbook[runbook.index(
        "lesson_studio_task14_hil_storage.py run-matrix"
    ):runbook.index("The attended release order is immutable")]

    assert "export SERIAL_PORT='/dev/cu.usbmodem1101'" in runbook
    assert '--evidence-dir "$EVIDENCE_ROOT/storage-hil"' in matrix
    assert '--failure-evidence-dir "$EVIDENCE_ROOT/storage-hil-failures"' in matrix
    assert '--before default_reset --after hard_reset' in runbook
    assert "Do not erase NVS" in runbook


def _flash_boot_inputs(tmp_path):
    identity = load_script("lesson_studio_task14_build_identity.py")
    manifest, _value, paths = task6_manifest(
        tmp_path, profile="hil", binary=b"h" * 3_651_968
    )
    build = identity.load_build_identity(manifest, expected_profile="hil")
    flash_log = tmp_path / "esptool-flash.log"
    flash_log.write_text(
        "esptool.py --chip esp32s3 --port /dev/cu.usbmodem1101 "
        f"--before default_reset --after hard_reset write_flash 0x20000 {paths['bin']}\n"
        "MAC: 28:84:85:85:1a:80\n"
        "Wrote 3651968 bytes (100%) at 0x00020000\n"
        "Hash of data verified.\n"
    )
    exit_code = tmp_path / "esptool-exit-code.txt"
    exit_code.write_text("0\n")
    preflight = tmp_path / "hil-preflight.json"
    preflight.write_text(json.dumps({
        "status": "PASS",
        "deviceId": "28:84:85:85:1a:80",
        "deviceUuid": "fce7bec8-8478-4ab4-817f-7b87c41c1f91",
        "connectionIdentity": {
            "deviceId": "28:84:85:85:1a:80",
            "clientId": "fce7bec8-8478-4ab4-817f-7b87c41c1f91",
        },
        "buildIdentity": build,
    }))
    serial = tmp_path / "hil-boot-serial.log"
    serial.write_text(
        f"app_init: ELF file SHA256:  {build['elfSha256'][:16]}...\n"
        "TBOT_HIL_STORAGE_FAULTS_ENABLED non-production-image\n"
    )
    return identity, manifest, build, flash_log, exit_code, preflight, serial


def test_flash_and_boot_attestation_cli_bind_running_hil_identity(tmp_path):
    identity, manifest, build, flash_log, exit_code, preflight, serial = (
        _flash_boot_inputs(tmp_path)
    )
    receipt = tmp_path / "hil-flash-receipt.json"
    capture_receipt = tmp_path / "hil-boot-capture.json"
    output = tmp_path / "hil-boot-attestation.json"

    flashed = subprocess.run([
        sys.executable, str(identity.__file__), "flash-attest",
        "--manifest", str(manifest), "--esptool-log", str(flash_log),
        "--exit-code-file", str(exit_code),
        "--device-mac", "28:84:85:85:1a:80",
        "--started-at", "2026-07-17T00:00:00Z",
        "--completed-at", "2026-07-17T00:00:01Z", "--output", str(receipt),
    ], text=True, capture_output=True, check=False)
    assert flashed.returncode == 0, flashed.stderr
    _write_boot_capture_receipt(capture_receipt, serial)

    completed = subprocess.run([
        sys.executable, str(identity.__file__), "boot-attest",
        "--manifest", str(manifest), "--event", "hil-flash",
        "--flash-receipt", str(receipt),
        "--boot-capture-receipt", str(capture_receipt),
        "--connection-attestation", str(preflight),
        "--observed-at", "2026-07-17T00:00:05Z", "--output", str(output),
    ], text=True, capture_output=True, check=False)

    assert completed.returncode == 0, completed.stderr
    value = json.loads(output.read_text())
    assert value["status"] == "PASS"
    assert value["event"] == "hil-flash"
    assert value["buildIdentity"] == build
    assert value["bootIdentity"]["binarySha256"] == build["binarySha256"]
    assert value["flashReceipt"]["deviceId"] == "28:84:85:85:1a:80"
    assert value["bootEvidence"]["elfSha256Prefix"] == build["elfSha256"][:16]


def test_real_idf_elf_sha_line_accepts_whitespace_and_literal_ellipsis(tmp_path):
    identity = load_script("lesson_studio_task14_build_identity.py")
    expected = "4667d1197abcdef0" + "0" * 48
    captured = b"I (109) app_init: ELF file SHA256:  4667d1197abcdef0...\n"
    assert identity.parse_running_elf_sha256(captured, expected) == "4667d1197abcdef0"


@pytest.mark.parametrize(
    "rendered",
    (
        "4667d1197abcdef0",
        "4667d119...",
        "4667d1197abcdef...",
        "4667d1197abcdef00...",
        "4667D1197ABCDEF0...",
    ),
)
def test_real_idf_elf_sha_line_rejects_noncanonical_prefix(rendered):
    identity = load_script("lesson_studio_task14_build_identity.py")
    expected = "4667d1197abcdef0" + "0" * 48
    with pytest.raises(identity.BuildIdentityError):
        identity.parse_running_elf_sha256(
            f"app_init: ELF file SHA256:  {rendered}\n".encode(), expected
        )


def test_boot_capture_opens_port_before_safe_reset_and_orders_markers(tmp_path):
    identity = load_script("lesson_studio_task14_build_identity.py")
    events = []
    chunks = iter([
        b"app_init: ELF file SHA256:  4667d1197abcdef0...\n",
        b"TBOT_HIL_STORAGE_FAULTS_ENABLED non-production-image\n",
    ])

    class FakeSerial:
        def __init__(self, *_args, **_kwargs):
            events.append("open")
            self.dtr = None
            self.rts = None

        def setDTR(self, value):
            events.append(("dtr", value))

        def setRTS(self, value):
            events.append(("rts", value))

        def reset_input_buffer(self):
            events.append("flush")

        def read(self, _size):
            return next(chunks, b"")

        def close(self):
            events.append("close")

    times = iter([
        "2026-07-17T00:00:01Z", "2026-07-17T00:00:02Z",
        "2026-07-17T00:00:03Z", "2026-07-17T00:00:04Z",
    ])
    output = tmp_path / "boot.log"
    receipt = tmp_path / "capture.json"
    value = identity.capture_hil_boot_identity(
        "/dev/cu.usbmodem1101", output, receipt, timeout_seconds=1,
        serial_factory=FakeSerial, utc_now=lambda: next(times),
        monotonic=iter([0.0, 0.1, 0.2]).__next__, sleep=lambda _seconds: None,
    )

    assert events[:5] == [
        "open", ("dtr", False), ("rts", False), "flush", ("rts", True),
    ]
    assert events[-2:] == [("rts", False), "close"]
    assert value["captureStartedUtc"] <= value["resetUtc"] < value["elfMarkerUtc"]
    assert value["resetUtc"] < value["hilMarkerUtc"]
    assert value["timedOut"] is False


def test_boot_capture_timeout_writes_not_pass_receipt(tmp_path):
    identity = load_script("lesson_studio_task14_build_identity.py")

    class SilentSerial:
        def __init__(self, *_args, **_kwargs): pass
        def setDTR(self, _value): pass
        def setRTS(self, _value): pass
        def reset_input_buffer(self): pass
        def read(self, _size): return b""
        def close(self): pass

    output = tmp_path / "timeout.log"
    receipt = tmp_path / "timeout.json"
    with pytest.raises(identity.BuildIdentityError, match="timed out"):
        identity.capture_hil_boot_identity(
            "/dev/cu.usbmodem1101", output, receipt, timeout_seconds=1,
            serial_factory=SilentSerial,
            utc_now=iter(["2026-07-17T00:00:01Z", "2026-07-17T00:00:02Z"]).__next__,
            monotonic=iter([0.0, 0.1, 2.0]).__next__, sleep=lambda _seconds: None,
        )
    assert json.loads(receipt.read_text())["timedOut"] is True


def test_boot_capture_discards_stale_pre_reset_markers(tmp_path):
    identity = load_script("lesson_studio_task14_build_identity.py")
    stale = [
        b"app_init: ELF file SHA256:  4667d1197abcdef0...\n"
        b"TBOT_HIL_STORAGE_FAULTS_ENABLED non-production-image\n"
    ]

    class StaleSerial:
        def __init__(self, *_args, **_kwargs): pass
        def setDTR(self, _value): pass
        def setRTS(self, _value): pass
        def reset_input_buffer(self): stale.clear()
        def read(self, _size): return stale.pop(0) if stale else b""
        def close(self): pass

    with pytest.raises(identity.BuildIdentityError, match="timed out"):
        identity.capture_hil_boot_identity(
            "/dev/cu.usbmodem1101", tmp_path / "boot.log", tmp_path / "receipt.json",
            timeout_seconds=1, serial_factory=StaleSerial,
            utc_now=iter(["2026-07-17T00:00:01Z", "2026-07-17T00:00:02Z"]).__next__,
            monotonic=iter([0.0, 0.1, 2.0]).__next__, sleep=lambda _seconds: None,
        )


def test_boot_capture_flush_failure_aborts_without_attestation(tmp_path):
    identity = load_script("lesson_studio_task14_build_identity.py")

    class FlushFailureSerial:
        def __init__(self, *_args, **_kwargs): pass
        def setDTR(self, _value): pass
        def setRTS(self, _value): pass
        def reset_input_buffer(self): raise OSError("flush failed")
        def close(self): pass

    output = tmp_path / "boot.log"
    receipt = tmp_path / "receipt.json"
    with pytest.raises(OSError, match="flush failed"):
        identity.capture_hil_boot_identity(
            "/dev/cu.usbmodem1101", output, receipt,
            serial_factory=FlushFailureSerial,
        )
    assert not output.exists()
    assert not receipt.exists()


def _write_boot_capture_receipt(path, serial, **changes):
    prefix = re.search(
        r"app_init: ELF file SHA256:\s+([0-9a-f]{16})\.\.\.",
        serial.read_text(),
    ).group(1)
    value = {
        "status": "PASS",
        "event": "hil-boot-capture",
        "serialPort": "/dev/cu.usbmodem1101",
        "serialPath": str(serial.resolve()),
        "serialSha256": sha256(serial),
        "captureStartedUtc": "2026-07-17T00:00:01Z",
        "resetUtc": "2026-07-17T00:00:02Z",
        "elfMarkerUtc": "2026-07-17T00:00:03Z",
        "hilMarkerUtc": "2026-07-17T00:00:04Z",
        "elfSha256Prefix": prefix,
        "timedOut": False,
    }
    value.update(changes)
    path.write_text(json.dumps(value))
    return value


@pytest.mark.parametrize(
    "mutation",
    (
        "mac", "offset", "bytes", "verify", "exit", "old-elf",
        "old-manifest", "stale-time", "capture-order", "capture-timeout",
        "missing-hil", "duplicate-elf", "duplicate-hil", "reversed-markers",
        "erase",
    ),
)
def test_flash_and_boot_attestation_reject_invalid_or_stale_evidence(tmp_path, mutation):
    identity, manifest, build, flash_log, exit_code, preflight, serial = (
        _flash_boot_inputs(tmp_path)
    )
    if mutation == "mac":
        flash_log.write_text(flash_log.read_text().replace("28:84:85:85:1a:80", "00:00:00:00:00:00"))
    elif mutation == "offset":
        flash_log.write_text(flash_log.read_text().replace("0x20000", "0x10000"))
    elif mutation == "bytes":
        flash_log.write_text(flash_log.read_text().replace("3651968 bytes", "1 bytes"))
    elif mutation == "verify":
        flash_log.write_text(flash_log.read_text().replace("Hash of data verified.\n", ""))
    elif mutation == "exit":
        exit_code.write_text("1\n")
    elif mutation == "erase":
        flash_log.write_text("erase_flash\n" + flash_log.read_text())
    receipt = tmp_path / "receipt.json"
    capture_receipt = tmp_path / "capture.json"
    try:
        value = identity.build_flash_attestation(
            manifest, flash_log, exit_code, "28:84:85:85:1a:80",
            "2026-07-17T00:00:00Z", "2026-07-17T00:00:01Z",
        )
        identity.atomic_write_json(receipt, value)
    except identity.BuildIdentityError:
        assert mutation not in {
            "old-elf", "old-manifest", "stale-time", "capture-order",
            "capture-timeout", "missing-hil",
            "duplicate-elf", "duplicate-hil", "reversed-markers",
        }
        return
    if mutation == "old-elf":
        serial.write_text(serial.read_text().replace(build["elfSha256"][:16], "0" * 16))
    if mutation == "old-manifest":
        manifest, _value, _paths = task6_manifest(
            tmp_path / "old", profile="hil", binary=b"old-running-image"
        )
    if mutation == "missing-hil":
        serial.write_text(serial.read_text().replace(
            "TBOT_HIL_STORAGE_FAULTS_ENABLED non-production-image\n", ""
        ))
    elif mutation == "duplicate-elf":
        serial.write_text(serial.read_text().splitlines()[0] + "\n" + serial.read_text())
    elif mutation == "duplicate-hil":
        serial.write_text(
            serial.read_text()
            + "TBOT_HIL_STORAGE_FAULTS_ENABLED non-production-image\n"
        )
    elif mutation == "reversed-markers":
        lines = serial.read_text().splitlines()
        serial.write_text(lines[1] + "\n" + lines[0] + "\n")
    observed = "2026-07-16T23:59:59Z" if mutation == "stale-time" else "2026-07-17T00:00:05Z"
    capture_changes = {}
    if mutation == "capture-order":
        capture_changes["resetUtc"] = "2026-07-17T00:00:04Z"
    elif mutation == "capture-timeout":
        capture_changes.update(status="NOT_PASS", timedOut=True, hilMarkerUtc=None)
    _write_boot_capture_receipt(capture_receipt, serial, **capture_changes)
    with pytest.raises(identity.BuildIdentityError):
        identity.build_boot_attestation(
            manifest, receipt, capture_receipt, preflight, observed, "hil-flash"
        )


def test_runbook_generates_dedicated_hil_boot_attestation_before_release():
    runbook = (ROOT / "docs" / "lesson-studio-task14-live-matrix.md").read_text()
    assert 'export HIL_BOOT_ATTESTATION="$EVIDENCE_ROOT/hil-boot-attestation.json"' in runbook
    assert "lesson_studio_task14_build_identity.py flash-attest" in runbook
    assert 'write_flash 0x20000 $(dirname "$HIL_BUILD_MANIFEST")/xiaozhi.bin' in runbook
    assert "lesson_studio_task14_build_identity.py boot-attest" in runbook
    assert '--flash-receipt "$HIL_FLASH_RECEIPT"' in runbook
    assert "lesson_studio_task14_build_identity.py boot-capture" in runbook
    assert '--boot-capture-receipt "$HIL_BOOT_CAPTURE_RECEIPT"' in runbook
    capture = runbook.index("lesson_studio_task14_build_identity.py boot-capture")
    preflight = runbook.index("lesson_studio_task14_hil_storage.py preflight", capture)
    attest = runbook.index("lesson_studio_task14_build_identity.py boot-attest", preflight)
    assert capture < preflight < attest
    assert '--connection-attestation "$HIL_PREFLIGHT_ATTESTATION"' in runbook
    hil_release = runbook[runbook.index("--event hil-flash"):
                          runbook.index("--event hil-matrix-pass")]
    assert '--evidence "$HIL_BOOT_ATTESTATION"' in hil_release
    assert '--evidence "$HIL_BUILD_MANIFEST"' not in hil_release


def test_hil_matrix_release_requires_canonical_pass_root(tmp_path):
    identity = load_script("lesson_studio_task14_build_identity.py")
    hil_path, _hil, _ = task6_manifest(tmp_path / "hil", profile="hil", binary=b"hil")
    prod_path, _prod, _ = task6_manifest(
        tmp_path / "prod", profile="production", binary=b"prod"
    )
    pair = identity.validate_build_pair(hil_path, prod_path)
    failure_root = tmp_path / "storage-hil-failures"
    report = _matrix_evidence_payload(failure_root, pair["hil"])
    report_path = failure_root / "hil-matrix-report.json"
    report_path.write_text(json.dumps(report))
    with pytest.raises(identity.BuildIdentityError):
        identity._validate_hil_matrix(report, pair, report_path)

    alias = tmp_path / "storage-hil"
    alias.symlink_to(failure_root, target_is_directory=True)
    with pytest.raises(identity.BuildIdentityError):
        identity._validate_hil_matrix(report, pair, alias / "hil-matrix-report.json")


def test_release_hashes_the_same_evidence_bytes_it_validates(tmp_path, monkeypatch):
    identity = load_script("lesson_studio_task14_build_identity.py")
    hil_path, _hil, _ = task6_manifest(tmp_path / "hil", profile="hil", binary=b"hil")
    prod_path, _prod, _ = task6_manifest(
        tmp_path / "prod", profile="production", binary=b"prod"
    )
    pair = identity.validate_build_pair(hil_path, prod_path)
    evidence = tmp_path / "hil-flash.json"
    invalid_bytes = b'{"status":"NOT_PASS"}\n'
    evidence.write_bytes(invalid_bytes)
    valid = release_evidence_payload("hil-flash", pair["hil"], pair["production"])
    original_sha = identity.sha256_file

    def replace_after_hash(path):
        digest = original_sha(path)
        if Path(path) == evidence:
            evidence.write_text(json.dumps(valid))
        return digest

    monkeypatch.setattr(identity, "sha256_file", replace_after_hash)
    ledger = tmp_path / "ledger"
    with pytest.raises(identity.BuildIdentityError):
        identity.append_release_receipt(
            ledger, hil_path, prod_path,
            event="hil-flash", evidence_path=evidence,
            completed_at="2026-07-17T00:00:00Z",
        )
    assert not ledger.exists()


def test_hil_matrix_rejects_scenario_replacement_during_validation(tmp_path, monkeypatch):
    identity = load_script("lesson_studio_task14_build_identity.py")
    hil_path, _hil, _ = task6_manifest(tmp_path / "hil", profile="hil", binary=b"hil")
    prod_path, _prod, _ = task6_manifest(
        tmp_path / "prod", profile="production", binary=b"prod"
    )
    pair = identity.validate_build_pair(hil_path, prod_path)
    root = tmp_path / "storage-hil"
    report = _matrix_evidence_payload(root, pair["hil"])
    report_path = root / "hil-matrix-report.json"
    report_path.write_text(json.dumps(report))
    original_snapshot = identity._snapshot_directory
    calls = 0

    def replace_before_resnapshot(directory, expected):
        nonlocal calls
        calls += 1
        if calls == 2:
            (Path(directory) / "server.log").write_text("replaced during validation\n")
        return original_snapshot(directory, expected)

    monkeypatch.setattr(identity, "_snapshot_directory", replace_before_resnapshot)
    with pytest.raises(identity.BuildIdentityError):
        identity._validate_hil_matrix(report, pair, report_path)


def test_hil_matrix_semantics_never_reopen_original_paths_after_snapshot(
    tmp_path, monkeypatch
):
    identity = load_script("lesson_studio_task14_build_identity.py")
    hil_path, _hil, _ = task6_manifest(tmp_path / "hil", profile="hil", binary=b"hil")
    prod_path, _prod, _ = task6_manifest(
        tmp_path / "prod", profile="production", binary=b"prod"
    )
    pair = identity.validate_build_pair(hil_path, prod_path)
    root = tmp_path / "storage-hil"
    report = _matrix_evidence_payload(root, pair["hil"])
    report_path = root / "hil-matrix-report.json"
    report_path.write_text(json.dumps(report))
    original_load = identity._load_matrix_json

    def reject_original_reopen(path, label):
        if root in Path(path).parents:
            pytest.fail(f"original matrix path reopened after snapshot: {path}")
        return original_load(path, label)

    monkeypatch.setattr(identity, "_load_matrix_json", reject_original_reopen)
    identity._validate_hil_matrix(report, pair, report_path)


def _matrix_evidence_payload(root, hil_identity, *, storage_layout=False):
    identity = load_script("lesson_studio_task14_build_identity.py")
    hil = load_script("lesson_studio_task14_hil_storage.py")
    Path(root).mkdir(parents=True, exist_ok=True)
    device_id = "28:84:85:85:1a:80"
    device_uuid = "fce7bec8-8478-4ab4-817f-7b87c41c1f91"
    connection = {"deviceId": device_id, "clientId": device_uuid}
    scenarios = []
    for scenario in identity.HIL_STORAGE_SCENARIOS:
        directory = Path(root) / scenario
        directory.mkdir()
        operation, checkpoint, action, threshold, pause_seconds, power_loss = (
            hil.SCENARIO_SPECS[scenario]
        )
        expected_progress = hil.SCENARIO_EXPECTED_PROGRESS[scenario]
        arm = {
            "cacheKey": KEY,
            "status": "armed",
            "operation": operation,
            "checkpoint": checkpoint,
            "action": action,
            "threshold": threshold,
            "declaredAssetBytes": 1 if checkpoint == "after_download_bytes" else 0,
            "pauseSeconds": pause_seconds,
            "armSequence": 1,
        }
        status_after = {
            **arm,
            "status": "consumed",
            "armed": False,
            "reached": True,
            "consumed": True,
            "reachedSequence": 2,
            "consumedSequence": 3,
        }
        if power_loss:
            status_after = exact_status("idle")
        status_before = exact_status("idle")
        trigger = _matrix_trigger_outcome(scenario)
        cleanup_inspection = _matrix_preservation_inspection("missing", "missing")
        if storage_layout:
            expected = hil.scenario_artifact_names(
                power_loss=scenario == identity.HIL_STORAGE_SCENARIOS[-1]
            )
        else:
            expected = (
                identity.HIL_POWER_LOSS_ARTIFACTS
                if scenario == identity.HIL_STORAGE_SCENARIOS[-1]
                else identity.HIL_ORDINARY_ARTIFACTS
            )
        payloads = {
            name: b"artifact\n"
            for name in expected
            if name != "SHA256SUMS"
        }
        payloads["build-manifest.json"] = (
            json.dumps(hil_identity, sort_keys=True) + "\n"
        ).encode()
        payloads["build-manifest.sha256"] = (
            hashlib.sha256(payloads["build-manifest.json"]).hexdigest()
            + "  build-manifest.json\n"
        ).encode("ascii")
        payloads["result.json"] = (
            json.dumps({
                "scenario": scenario,
                "status": "PASS",
                "cacheKey": KEY,
                "buildIdentity": hil_identity,
                "deviceId": device_id,
                "deviceUuid": device_uuid,
                "connectionIdentity": connection,
                "recovery": _matrix_recovery_payload(scenario),
                "events": _matrix_recovery_events(scenario),
                "armSequence": 1,
                "reachedSequence": 2,
                "consumedSequence": 3,
                "operation": operation,
                "checkpoint": checkpoint,
                "faultAction": action,
                "expectedProgress": expected_progress,
                "checkpointExercised": True,
                "cleanupVerified": True,
                "controllerInactive": True,
                "triggerOutcome": None if power_loss else trigger,
                "triggerResponseAbsent": power_loss,
                "cleanupInspection": cleanup_inspection,
                "finalStatus": status_after,
                **({
                    "powerLoss": True,
                    "checkpointReached": True,
                    "successMarkerBeforeLoss": False,
                    "rebootCaptured": True,
                    "armClearedAfterReboot": True,
                    "postRebootInspected": True,
                    "retryStatus": "ready",
                    "triggerPendingAtMarker": True,
                    "triggerPendingAtCutBoundary": True,
                    "disconnectAfterPowerCutBoundary": True,
                    "utcStart": "2026-07-17T00:00:00Z",
                    "checkpointReachedUtc": "2026-07-17T00:00:00.500000Z",
                    "powerCutBoundaryUtc": "2026-07-17T00:00:01Z",
                    "disconnectObservedUtc": "2026-07-17T00:00:01.500000Z",
                    "powerRemovalConfirmedUtc": "2026-07-17T00:00:02Z",
                    "utcEnd": "2026-07-17T00:00:03Z",
                } if power_loss else {}),
            }, sort_keys=True) + "\n"
        ).encode()
        payloads["evidence.json"] = (
            json.dumps({
                "scenario": scenario,
                "status": "PASS",
                "capturedAt": "2026-07-17T00:00:00+00:00",
                "validationErrors": [],
                "cleanupInspection": cleanup_inspection,
                "finalStatus": status_after,
            }, sort_keys=True) + "\n"
        ).encode()
        payloads["validator-exit-code.txt"] = b"0\n"
        payloads["recovery-response.json"] = (
            json.dumps(_matrix_recovery_payload(scenario), sort_keys=True) + "\n"
        ).encode()
        payloads["inspect-before.json"] = (
            json.dumps(
                _matrix_preservation_inspection("missing", "missing"), sort_keys=True
            ) + "\n"
        ).encode()
        payloads["inspect-after.json"] = (
            json.dumps(
                _matrix_preservation_inspection(
                    {
                        "evict-before-first-unlink-fail": "full",
                        "evict-after-unlinks-fail": "directory_only",
                        "evict-before-rmdir-fail": "directory_only",
                        "evict-after-unlinks-sd-removal": "missing",
                    }.get(scenario, "full")
                ),
                sort_keys=True,
            ) + "\n"
        ).encode()
        payloads["timeline.log"] = "".join(
            f"{index + 1} {event}\n"
            for index, event in enumerate(_matrix_recovery_events(scenario))
        ).encode()
        payloads["status-before.json"] = (json.dumps(status_before) + "\n").encode()
        payloads["stage-response.json"] = (json.dumps({
            "cacheKey": KEY,
            "siblingCacheKey": KEY.replace("/v1-", "/v2-", 1),
            "fixture": "preservation_set",
            "status": "staged",
            "changed": True,
        }) + "\n").encode()
        payloads["arm-response.json"] = (json.dumps(arm) + "\n").encode()
        payloads["status-after.json"] = (json.dumps(status_after) + "\n").encode()
        payloads["cleanup-response.json"] = (json.dumps({
            "cacheKey": KEY,
            "siblingCacheKey": KEY.replace("/v1-", "/v2-", 1),
            "fixture": "preservation_set",
            "status": "cleaned",
            "changed": True,
        }) + "\n").encode()
        if not power_loss:
            payloads["trigger-response.json"] = (json.dumps(trigger) + "\n").encode()
        else:
            payloads["checkpoint-reached-utc.txt"] = b"2026-07-17T00:00:00.500000Z\n"
            payloads["power-removed-utc.txt"] = b"2026-07-17T00:00:02Z\n"
            payloads["post-reboot-inspect.json"] = payloads["inspect-after.json"]
            payloads["reboot-serial.log"] = payloads["serial.log"]
        payloads["serial.log"] = (
            "TBOT_HIL_STORAGE_FAULTS_ENABLED non-production-image\n"
            f"HIL_STORAGE_CHECKPOINT_REACHED operation={operation} "
            f"checkpoint={checkpoint} cache_key={KEY} count={expected_progress} "
            "reached_sequence=2\n"
            f"HIL_STORAGE_FAULT_CONSUMED operation={operation} checkpoint={checkpoint} "
            f"cache_key={KEY} action={action} consumed_sequence=3\n"
        ).encode()
        for name, data in payloads.items():
            (directory / name).write_bytes(data)
        artifacts = {
            name: hashlib.sha256(data).hexdigest()
            for name, data in payloads.items()
        }
        checksum_bytes = "".join(
            f"{artifacts[name]}  {name}\n"
            for name in expected
            if name != "SHA256SUMS"
        ).encode("ascii")
        (directory / "SHA256SUMS").write_bytes(checksum_bytes)
        scenarios.append({
            "scenario": scenario,
            "status": "PASS",
            "evidencePath": f"{scenario}/evidence.json",
            "evidenceSha256": artifacts["evidence.json"],
            "sha256SumsPath": f"{scenario}/SHA256SUMS",
            "sha256SumsSha256": hashlib.sha256(checksum_bytes).hexdigest(),
            "validatorExitCode": 0,
            "artifacts": artifacts,
        })
    return {
        "status": "PASS",
        "event": "hil-matrix-pass",
        "buildIdentity": hil_identity,
        "deviceId": device_id,
        "deviceUuid": device_uuid,
        "connectionIdentity": connection,
        "scenarios": scenarios,
    }


def _matrix_recovery_payload(scenario):
    attempted = scenario in {
        "evict-after-unlinks-fail", "evict-before-rmdir-fail",
    }
    if not attempted:
        return {
            "attempted": False,
            "operation": None,
            "reason": None,
            "response": None,
            "inspection": None,
        }
    return {
        "attempted": True,
        "operation": "evict",
        "reason": "expected_partial_eviction",
        "response": {
            "cacheKey": KEY,
            "status": "evicted",
            "reason": "evicted",
            "evicted": True,
            "notFound": False,
            "fileCount": 0,
        },
        "inspection": _matrix_preservation_inspection("missing"),
    }


def _matrix_trigger_outcome(scenario):
    if scenario == "evict-before-first-unlink-fail":
        return eviction_result("unlink_failed", 0)
    if scenario in {"evict-after-unlinks-fail", "evict-before-rmdir-fail"}:
        return eviction_result("partial_evict_recovery_required", 1)
    if scenario == "evict-after-unlinks-sd-removal":
        return eviction_result("evicted", 1, evicted=True)
    return failed_sync_result()


def _matrix_recovery_events(scenario):
    events = [
        "status-before", "inspect-before", "stage", "arm", "trigger",
        "status-after", "inspect-after", "cleanup",
    ]
    if scenario in {"evict-after-unlinks-fail", "evict-before-rmdir-fail"}:
        events[-1:-1] = ["recovery-trigger", "recovery-inspect"]
    return events


def _matrix_preservation_inspection(primary_state, sibling_state="full"):
    sibling = KEY.replace("/v1-", "/v2-", 1)
    entries = [
        {
            "label": "lesson-assets/current.json",
            "nodeType": "regular_file",
            "bytes": 7,
            "sha256": "a" * 64,
        },
        {"label": "lesson-assets/pvg", "nodeType": "directory", "bytes": 0, "sha256": ""},
        {"label": "lesson-assets/shared", "nodeType": "directory", "bytes": 0, "sha256": ""},
    ]
    entries.extend(hil_preservation_entries(
        KEY, primary_state,
        "e95ab394bdf8569652429018519989d3e94cae168cf91c269c81a2c9bb00d5ec",
    ))
    entries.extend(hil_preservation_entries(
        sibling, sibling_state,
        "462cc80e16c12bbee14c7eba5e61da286e79580d6dc5b996bfcf7a43f30a4cf8",
    ))
    return {
        "cacheKey": KEY,
        "siblingCacheKey": sibling,
        "status": "inspected",
        "truncated": False,
        "entries": sorted(entries, key=lambda item: item["label"]),
    }


def release_evidence_payload(
    event,
    hil_identity,
    production_identity,
    ledger_path=None,
    evidence_root=None,
    hil_flash_evidence=None,
):
    if event == "hil-flash":
        if hil_flash_evidence is not None:
            return hil_flash_evidence
        return {
            "status": "PASS", "event": event,
            "buildIdentity": hil_identity,
            "bootIdentity": {
                "sourceCommit": hil_identity["sourceCommit"],
                "profile": "hil", "configEnabled": True,
                "binarySha256": hil_identity["binarySha256"],
            },
        }
    if event == "hil-matrix-pass":
        assert evidence_root is not None
        return _matrix_evidence_payload(evidence_root, hil_identity)
    if event == "production-reflash":
        return {
            "status": "PASS", "event": event,
            "buildIdentity": production_identity,
            "bootIdentity": {
                "sourceCommit": production_identity["sourceCommit"],
                "profile": "production", "configEnabled": False,
                "binarySha256": production_identity["binarySha256"],
            },
        }
    if event == "production-attest":
        return {
            "status": "PASS", "event": event,
            "buildIdentity": production_identity,
            "hilToolsAbsent": True,
            "sourceCommit": production_identity["sourceCommit"],
            "binarySha256": production_identity["binarySha256"],
        }
    identity = load_script("lesson_studio_task14_build_identity.py")
    ledger = identity.load_release_ledger(
        ledger_path,
        production_identity=production_identity,
        required_event="production-attest",
    )
    return {
        "status": "PASS", "minimumTransitionsRequired": 104,
        "metrics": {"transitions": 104, "sessions": 3},
        "checks": {"transition_binding_complete": True, "memory_gate": True},
        "buildIdentity": production_identity,
        "releaseLedgerEvidence": ledger,
    }


def _valid_hil_flash_evidence(tmp_path, identity, manifest):
    build = identity.load_build_identity(manifest, expected_profile="hil")
    app = Path(manifest).parent / "xiaozhi.bin"
    flash_log = tmp_path / "release-esptool.log"
    flash_log.write_text(
        "esptool.py --chip esp32s3 --port /dev/cu.usbmodem1101 "
        f"--before default_reset --after hard_reset write_flash 0x20000 {app}\n"
        "MAC: 28:84:85:85:1a:80\n"
        f"Wrote {app.stat().st_size} bytes (100%) at 0x00020000\n"
        "Hash of data verified.\n"
    )
    exit_code = tmp_path / "release-esptool-exit.txt"
    exit_code.write_text("0\n")
    receipt = identity.build_flash_attestation(
        manifest, flash_log, exit_code, "28:84:85:85:1a:80",
        "2026-07-16T23:59:58Z", "2026-07-16T23:59:59Z",
    )
    receipt_path = tmp_path / "release-flash-receipt.json"
    identity.atomic_write_json(receipt_path, receipt)
    serial = tmp_path / "release-boot-serial.log"
    serial.write_text(
        f"app_init: ELF file SHA256:  {build['elfSha256'][:16]}...\n"
        "TBOT_HIL_STORAGE_FAULTS_ENABLED non-production-image\n"
    )
    capture_receipt = tmp_path / "release-boot-capture.json"
    _write_boot_capture_receipt(capture_receipt, serial)
    connection = tmp_path / "release-preflight.json"
    connection.write_text(json.dumps({
        "status": "PASS",
        "deviceId": "28:84:85:85:1a:80",
        "deviceUuid": "fce7bec8-8478-4ab4-817f-7b87c41c1f91",
        "connectionIdentity": {
            "deviceId": "28:84:85:85:1a:80",
            "clientId": "fce7bec8-8478-4ab4-817f-7b87c41c1f91",
        },
        "buildIdentity": build,
    }))
    return identity.build_boot_attestation(
        manifest, receipt_path, capture_receipt, connection,
        "2026-07-17T00:00:05Z", "hil-flash"
    )


def eviction_result(status, file_count, *, evicted=False):
    return {
        "cacheKey": KEY,
        "status": status,
        "evicted": evicted,
        "notFound": False,
        "fileCount": file_count,
        "reason": status,
    }


def failed_sync_result(*, ready=False, skipped=0, state="FAILED"):
    result = {
        "cacheKey": KEY,
        "ready": ready,
        "downloadedCount": 0,
        "skippedCount": skipped,
        "failedCount": 0 if ready else 1,
        "totalBytes": 0,
        "files": [
            {
                "key": "hil-asset.png",
                "path": "hil-asset.png",
                "localPath": f"/sdcard/tbot/lesson-assets/{KEY}/hil-asset.png",
                "state": state,
                **({"error": "asset transfer failed"} if state == "FAILED" else {}),
            }
        ],
    }
    if ready:
        result["manifestChecksum"] = "d" * 64
    return result


@pytest.mark.parametrize(
    "scenario,response",
    (
        ("evict-before-first-unlink-fail", eviction_result("unlink_failed", 0)),
        ("evict-after-unlinks-fail", eviction_result("partial_evict_recovery_required", 1)),
        ("evict-before-rmdir-fail", eviction_result("partial_evict_recovery_required", 1)),
        ("evict-after-unlinks-sd-removal", eviction_result("evicted", 1, evicted=True)),
        ("sync-before-download-write-no-space", failed_sync_result()),
        ("sync-after-download-bytes-no-space", failed_sync_result()),
        ("sync-before-checksum-corrupt-staging", failed_sync_result()),
        ("sync-before-commit-rename-fail", failed_sync_result()),
    ),
)
def test_scenario_outcomes_require_the_exact_injected_failure(scenario, response):
    hil = load_script("lesson_studio_task14_hil_storage.py")
    outcome = hil.validate_scenario_outcome(scenario, response, cache_key=KEY)
    assert outcome["checkpointExercised"] is True
    assert outcome["triggerOutcome"] == response


def test_scenario_outcomes_reject_false_green_success_skip_and_wrong_partial_count():
    hil = load_script("lesson_studio_task14_hil_storage.py")
    cases = (
        ("evict-before-first-unlink-fail", eviction_result("evicted", 1, evicted=True)),
        ("evict-after-unlinks-fail", eviction_result("unlink_failed", 0)),
        ("evict-before-rmdir-fail", eviction_result("unlink_failed", 1)),
        ("sync-before-download-write-no-space", failed_sync_result(ready=True, state="SKIPPED")),
        ("sync-after-download-bytes-no-space", failed_sync_result(skipped=1, state="SKIPPED")),
    )
    for scenario, response in cases:
        with pytest.raises(hil.HilValidationError), pytest.MonkeyPatch.context():
            hil.validate_scenario_outcome(scenario, response, cache_key=KEY)
    with pytest.raises(hil.HilValidationError):
        hil.validate_scenario_outcome(
            "sync-before-commit-rename-power-loss",
            failed_sync_result(),
            cache_key=KEY,
        )
    assert hil.validate_scenario_outcome(
        "sync-before-commit-rename-power-loss", None, cache_key=KEY
    )["triggerResponseAbsent"] is True


def test_checkpoint_marker_binds_cache_count_and_sequence_not_stale_or_foreign():
    hil = load_script("lesson_studio_task14_hil_storage.py")
    exact = (
        "W HIL_STORAGE_CHECKPOINT_REACHED operation=evict checkpoint=after_unlinks "
        f"cache_key={KEY} count=1 reached_sequence=2\n"
    )
    chunks = iter(
        [
            exact.replace(KEY, SIBLING),
            exact.replace("count=1", "count=0"),
            exact,
        ]
    )
    now = iter([0.0, 0.1, 0.2, 0.3, 0.4, 0.5])
    captured = hil.poll_checkpoint(
        lambda: next(chunks, ""),
        operation="evict",
        checkpoint="after_unlinks",
        cache_key=KEY,
        expected_count=1,
        timeout_seconds=1,
        monotonic=lambda: next(now),
        sleep=lambda _seconds: None,
    )
    assert exact in captured


@pytest.mark.parametrize(
    "url",
    (
        "http://user:password@127.0.0.1:8003",
        "http://127.0.0.1:8003/?token=secret",
        "http://127.0.0.1:8003/#fragment",
        "http://8.8.8.8/asset.png",
        "http://127.0.0.1:8003/\nheader",
    ),
)
def test_lab_url_validation_rejects_credentials_query_fragment_public_and_controls(url):
    hil = load_script("lesson_studio_task14_hil_storage.py")
    with pytest.raises(hil.HilValidationError):
        hil.validate_lab_url(url, require_path=False)
    assert hil.validate_lab_url("http://192.168.100.209:8102/demo/asset.png", require_path=True)


def test_command_serialization_redacts_url_credentials_query_and_explicit_secrets():
    hil = load_script("lesson_studio_task14_hil_storage.py")
    secret = "mint-secret-value"
    command = hil.sanitized_command_text(
        [
            "runner.py",
            "--asset-url",
            "http://user:password@192.168.0.2/signed/path-api-key-123/a.png?token=query-secret",
            "--note",
            secret,
        ],
        (secret,),
    )
    for forbidden in ("user", "password", "query-secret", "path-api-key-123", "/signed/", secret):
        assert forbidden not in command


def test_power_loss_response_absence_requires_pending_trigger_and_post_cut_disconnect():
    hil = load_script("lesson_studio_task14_hil_storage.py")
    future = Future()
    moments = iter([
        "2026-07-17T00:00:01Z",
        "2026-07-17T00:00:01.500000Z",
        "2026-07-17T00:00:02Z",
    ])
    events = []

    def clock():
        value = next(moments)
        events.append(("clock", value))
        return value

    def remove_power():
        events.append(("remove", "started"))
        future.set_exception(hil.HilDisconnectError("connection reset"))
        events.append(("remove", "returned"))

    evidence = hil.await_power_loss_disconnect(
        future,
        lambda: None,
        remove_power,
        timeout_seconds=0.1,
        utc_clock=clock,
    )
    assert evidence == {
        "triggerPendingAtMarker": True,
        "triggerPendingAtCutBoundary": True,
        "powerCutBoundaryUtc": "2026-07-17T00:00:01Z",
        "disconnectObservedUtc": "2026-07-17T00:00:01.500000Z",
        "powerRemovalConfirmedUtc": "2026-07-17T00:00:02Z",
        "disconnectAfterPowerCutBoundary": True,
    }
    assert events == [
        ("clock", "2026-07-17T00:00:01Z"),
        ("remove", "started"),
        ("clock", "2026-07-17T00:00:01.500000Z"),
        ("remove", "returned"),
        ("clock", "2026-07-17T00:00:02Z"),
    ]


def test_power_loss_rejects_disconnect_at_or_before_cut_boundary():
    hil = load_script("lesson_studio_task14_hil_storage.py")
    future = Future()
    clock = iter([
        "2026-07-17T00:00:01Z",
        "2026-07-17T00:00:01Z",
        "2026-07-17T00:00:02Z",
    ]).__next__

    with pytest.raises(hil.HilValidationError, match="before power-cut boundary"):
        hil.await_power_loss_disconnect(
            future,
            lambda: None,
            lambda: future.set_exception(hil.HilDisconnectError("early reset")),
            timeout_seconds=0.1,
            utc_clock=clock,
        )


def test_power_loss_rejects_completion_during_boundary_clock_before_remove_prompt():
    hil = load_script("lesson_studio_task14_hil_storage.py")
    future = Future()
    events = []
    calls = 0

    def racing_clock():
        nonlocal calls
        calls += 1
        if calls == 1:
            future.set_exception(hil.HilDisconnectError("pre-boundary reset"))
            return "2026-07-17T00:00:01Z"
        if calls == 2:
            return "2026-07-17T00:00:00.500000Z"
        return "2026-07-17T00:00:02Z"

    with pytest.raises(hil.HilValidationError, match="before power-cut boundary"):
        hil.await_power_loss_disconnect(
            future,
            lambda: None,
            lambda: events.append("remove-prompt"),
            timeout_seconds=0.1,
            utc_clock=racing_clock,
        )

    assert events == []


def test_power_loss_allows_disconnect_and_confirmation_at_same_captured_utc():
    hil = load_script("lesson_studio_task14_hil_storage.py")
    result = {
        "powerLoss": True,
        "checkpointReached": True,
        "triggerResponseAbsent": True,
        "successMarkerBeforeLoss": False,
        "rebootCaptured": True,
        "postRebootStatus": exact_status("idle"),
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

    assert hil.validate_power_loss_result(result) == result


def test_power_loss_rejects_precut_completion_and_semantic_failures():
    hil = load_script("lesson_studio_task14_hil_storage.py")

    completed = Future()
    completed.set_result({"ready": False})
    with pytest.raises(hil.HilValidationError, match="completed before READY boundary"):
        hil.await_power_loss_disconnect(
            completed, lambda: None, lambda: None, timeout_seconds=0.1
        )

    failed = Future()
    failed.set_exception(hil.HilTransportError("HIL_HTTP_ERROR"))
    with pytest.raises(hil.HilValidationError, match="completed before READY boundary"):
        hil.await_power_loss_disconnect(
            failed, lambda: None, lambda: None, timeout_seconds=0.1
        )

    disconnected_during_ready = Future()
    early_events = []

    def ready_too_late():
        disconnected_during_ready.set_exception(hil.HilDisconnectError("early reset"))

    def early_clock():
        early_events.append("completion-timestamped")
        return "2026-07-17T00:00:00.500000Z"

    with pytest.raises(hil.HilValidationError, match="completed during READY prompt"):
        hil.await_power_loss_disconnect(
            disconnected_during_ready,
            ready_too_late,
            lambda: early_events.append("remove-prompt"),
            timeout_seconds=0.1,
            utc_clock=early_clock,
        )
    assert early_events == ["completion-timestamped"]

    semantic_after_cut = Future()

    def remove_then_semantic_failure():
        semantic_after_cut.set_exception(hil.HilValidationError("bad response schema"))

    with pytest.raises(hil.HilValidationError, match="non-disconnect failure after cut boundary"):
        hil.await_power_loss_disconnect(
            semantic_after_cut,
            lambda: None,
            remove_then_semantic_failure,
            timeout_seconds=0.1,
        )

    response_after_cut = Future()
    with pytest.raises(hil.HilValidationError, match="trigger returned after cut boundary"):
        hil.await_power_loss_disconnect(
            response_after_cut,
            lambda: None,
            lambda: response_after_cut.set_result({"ready": False}),
            timeout_seconds=0.1,
        )

    still_pending = Future()
    with pytest.raises(hil.HilTimeoutError, match="pending after cut boundary"):
        hil.await_power_loss_disconnect(
            still_pending,
            lambda: None,
            lambda: None,
            timeout_seconds=0.01,
        )


def test_capture_limits_fail_closed_by_bytes_and_lines():
    hil = load_script("lesson_studio_task14_hil_storage.py")
    assert hil.enforce_capture_limit("a\nb\n", max_bytes=10, max_lines=2, code="TEST") == "a\nb\n"
    with pytest.raises(hil.HilCaptureLimitError, match="TEST_BYTES"):
        hil.enforce_capture_limit("x" * 11, max_bytes=10, max_lines=20, code="TEST")
    with pytest.raises(hil.HilCaptureLimitError, match="TEST_LINES"):
        hil.enforce_capture_limit("a\nb\nc\n", max_bytes=20, max_lines=2, code="TEST")


def test_artifact_credential_scanner_rejects_secret_marker_and_jwt():
    hil = load_script("lesson_studio_task14_hil_storage.py")
    with pytest.raises(hil.HilValidationError):
        hil.assert_artifacts_sanitized({"server.log": b"mint-secret-value"}, ("mint-secret-value",))
    with pytest.raises(hil.HilValidationError):
        hil.assert_artifacts_sanitized({"server.log": b"aaa.bbb.ccc"}, ())


def test_bounded_process_output_kills_running_producer_immediately_at_byte_cap():
    hil = load_script("lesson_studio_task14_hil_storage.py")
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import os,time; os.write(1,b'x'*4096); time.sleep(10)",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    started = time.monotonic()

    with pytest.raises(hil.HilCaptureLimitError, match="TEST_BYTES"):
        hil._bounded_process_output(
            process,
            timeout_seconds=5,
            max_bytes=1024,
            max_lines=100,
            code="TEST",
        )

    assert time.monotonic() - started < 2
    assert process.poll() is not None


def test_failure_evidence_phase_mapping_is_exact_and_closed():
    hil = load_script("lesson_studio_task14_hil_storage.py")
    expected = {
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

    assert hil.FAILURE_PHASE_CODES == expected
    for phase, error_code in expected.items():
        assert hil.failure_error_code(phase) == error_code
    for phase in ("", "network", None):
        with pytest.raises(hil.HilValidationError, match="unknown failure phase"):
            hil.failure_error_code(phase)
    with pytest.raises(TypeError):
        hil.write_failure_evidence(error_code="CALLER_SUPPLIED")


def test_failure_evidence_roots_reject_overlap_symlinks_and_aliases_before_access(
    tmp_path, monkeypatch,
):
    hil = load_script("lesson_studio_task14_hil_storage.py")
    pass_root = tmp_path / "pass"
    failure_root = tmp_path / "failure"
    pass_root.mkdir()
    failure_root.mkdir()

    assert hil.validate_evidence_roots(pass_root, failure_root) == (
        pass_root.resolve(), failure_root.resolve()
    )
    (pass_root / "child").mkdir()
    for candidate in (pass_root, pass_root / "child", tmp_path):
        with pytest.raises(hil.HilValidationError, match="overlap"):
            hil.validate_evidence_roots(pass_root, candidate)

    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(hil.HilValidationError, match="symlink"):
        hil.validate_evidence_roots(pass_root, linked_parent / "quarantine")

    calls = []
    monkeypatch.setattr(hil, "load_build_identity", lambda *_args, **_kwargs: calls.append("build"))
    arguments = SimpleNamespace(evidence_dir=pass_root, failure_evidence_dir=pass_root)
    with pytest.raises(hil.HilValidationError, match="overlap"):
        hil.validate_run_roots(arguments)
    assert calls == []


@pytest.mark.parametrize("file_root", ("pass", "failure"))
@pytest.mark.parametrize("runner", ("scenario", "matrix"))
def test_failure_evidence_root_preflight_rejects_existing_non_directories_before_access(
    tmp_path, monkeypatch, file_root, runner,
):
    hil = load_script("lesson_studio_task14_hil_storage.py")
    pass_root = tmp_path / "pass"
    failure_root = tmp_path / "failure"
    if file_root == "pass":
        pass_root.write_text("not a directory")
        failure_root.mkdir()
    else:
        pass_root.mkdir()
        failure_root.write_text("not a directory")
    arguments = _failure_evidence_matrix_arguments(tmp_path)
    arguments.evidence_dir = pass_root
    arguments.failure_evidence_dir = failure_root
    calls = []
    monkeypatch.setattr(hil, "load_build_identity", lambda *_a, **_k: calls.append("build"))
    monkeypatch.setattr(hil, "attest_live_connection", lambda *_a, **_k: calls.append("attest"))
    monkeypatch.setattr(hil, "preflight", lambda *_a, **_k: calls.append("preflight"))

    with pytest.raises(hil.HilValidationError, match="real directory"):
        if runner == "scenario":
            hil.run_scenario(arguments, hil.HIL_STORAGE_SCENARIOS[0])
        else:
            hil.run_matrix(arguments)

    assert calls == []


@pytest.mark.parametrize("alias_kind", ("missing", "case-variant", "unicode-normalized"))
def test_failure_evidence_root_preflight_requires_existing_distinct_roots_before_access(
    tmp_path, monkeypatch, alias_kind,
):
    hil = load_script("lesson_studio_task14_hil_storage.py")
    if alias_kind == "missing":
        pass_root = tmp_path / "missing-pass"
        failure_root = tmp_path / "missing-failure"
    elif alias_kind == "case-variant":
        pass_root = tmp_path / "storage-hil"
        failure_root = tmp_path / "STORAGE-HIL"
        pass_root.mkdir()
    else:
        pass_root = tmp_path / "évidence-hil"
        failure_root = tmp_path / "e\u0301vidence-hil"
        pass_root.mkdir()
    arguments = _failure_evidence_matrix_arguments(tmp_path)
    arguments.evidence_dir = pass_root
    arguments.failure_evidence_dir = failure_root
    calls = []
    monkeypatch.setattr(hil, "load_build_identity", lambda *_a, **_k: calls.append("build"))
    monkeypatch.setattr(hil, "attest_live_connection", lambda *_a, **_k: calls.append("attest"))
    monkeypatch.setattr(hil, "preflight", lambda *_a, **_k: calls.append("preflight"))

    with pytest.raises(hil.HilValidationError, match="existing|alias|overlap"):
        hil.run_matrix(arguments)

    assert calls == []


@pytest.mark.parametrize("mutation", ("disappear", "replace"))
def test_failure_evidence_root_preflight_rejects_identity_change_during_validation(
    tmp_path, monkeypatch, mutation,
):
    hil = load_script("lesson_studio_task14_hil_storage.py")
    pass_root = tmp_path / "pass"
    failure_root = tmp_path / "failure"
    pass_root.mkdir()
    failure_root.mkdir()
    arguments = _failure_evidence_matrix_arguments(tmp_path)
    arguments.evidence_dir = pass_root
    arguments.failure_evidence_dir = failure_root
    calls = []
    original_resolve = Path.resolve
    mutated = False

    def mutate_during_resolve(path, strict=False):
        nonlocal mutated
        if Path(path) == pass_root and not mutated:
            mutated = True
            pass_root.rmdir()
            if mutation == "replace":
                pass_root.mkdir()
        return original_resolve(path, strict=strict)

    monkeypatch.setattr(Path, "resolve", mutate_during_resolve)
    monkeypatch.setattr(hil, "preflight", lambda *_a, **_k: calls.append("preflight"))

    with pytest.raises((hil.HilValidationError, FileNotFoundError), match="existing|changed|No such"):
        hil.run_matrix(arguments)

    assert calls == []


def test_failure_evidence_argument_is_required_only_for_live_run_commands(monkeypatch):
    hil = load_script("lesson_studio_task14_hil_storage.py")

    parser = hil.build_argument_parser()
    preflight = parser.parse_args([
        "preflight", "--device-id", "28:84:85:85:1a:80",
        "--device-uuid", "fce7bec8-8478-4ab4-817f-7b87c41c1f91",
        "--serial-port", "/dev/null", "--esp-base-url", "http://127.0.0.1:8000",
        "--asset-url", "http://127.0.0.1:8000/asset", "--asset-sha256", "d" * 64,
        "--asset-bytes", "1", "--build-manifest", "/tmp/build.json",
        "--evidence-dir", "/tmp/pass",
    ])
    assert not hasattr(preflight, "failure_evidence_dir")

    common = [
        "--device-id", "28:84:85:85:1a:80",
        "--device-uuid", "fce7bec8-8478-4ab4-817f-7b87c41c1f91",
        "--serial-port", "/dev/null", "--esp-base-url", "http://127.0.0.1:8000",
        "--asset-url", "http://127.0.0.1:8000/asset", "--asset-sha256", "d" * 64,
        "--asset-bytes", "1", "--build-manifest", "/tmp/build.json",
        "--evidence-dir", "/tmp/pass",
    ]
    with pytest.raises(SystemExit):
        parser.parse_args(["run-scenario", *common, "--scenario", hil.HIL_STORAGE_SCENARIOS[0]])
    with pytest.raises(SystemExit):
        parser.parse_args(["run-matrix", *common])


def test_bounded_process_output_enforces_deadline_on_stalled_process():
    hil = load_script("lesson_studio_task14_hil_storage.py")
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(10)"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    started = time.monotonic()

    with pytest.raises(hil.HilTimeoutError, match="TEST_TIMEOUT"):
        hil._bounded_process_output(
            process,
            timeout_seconds=0.05,
            max_bytes=1024,
            max_lines=100,
            code="TEST",
        )

    assert time.monotonic() - started < 2
    assert process.poll() is not None
