import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import time
import types
from concurrent.futures import Future
from pathlib import Path

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
        "cleanup-response.json", "result.json", "evidence.json",
        "validator-exit-code.txt", "SHA256SUMS",
    )
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
    evidence_paths = []
    for index, event in enumerate(events):
        evidence = tmp_path / f"{event}.json"
        payload = release_evidence_payload(
            event,
            hil_identity,
            production_identity,
            ledger_path=ledger_path,
            evidence_root=tmp_path,
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
        event_evidence = tmp_path / f"{event}.json"
        event_evidence.write_text(json.dumps(release_evidence_payload(
            event, hil_identity, production_identity, ledger_path=ledger,
            evidence_root=tmp_path,
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
    ledger = tmp_path / "ledger"
    flash = tmp_path / "hil-flash.json"
    flash.write_text(
        json.dumps(release_evidence_payload(
            "hil-flash", hil_identity, production_identity
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

    monkeypatch.setattr(hil, "preflight", lambda _arguments: preflight_result)
    monkeypatch.setattr(
        hil, "run_scenario", lambda _arguments, scenario: calls.append(scenario)
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
        ],
    )

    assert hil.main() == 0
    assert calls == list(hil.HIL_STORAGE_SCENARIOS)
    assert (evidence_root / "hil-matrix-report.json").is_file()


def test_publish_matrix_report_binds_scenario_artifacts_and_attended_identity(tmp_path):
    hil = load_script("lesson_studio_task14_hil_storage.py")
    hil_path, _manifest, _paths = task6_manifest(tmp_path / "hil", profile="hil")
    identity = load_script("lesson_studio_task14_build_identity.py").load_build_identity(
        hil_path, expected_profile="hil"
    )
    report_fixture = _matrix_evidence_payload(tmp_path / "matrix", identity)
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


@pytest.mark.parametrize(
    "mutation",
    (
        "scenario-set", "scenario-extra", "evidence-path", "evidence-hash", "scenario-status",
        "validator", "matrix-build", "matrix-device", "artifact-hash",
        "checksum-manifest",
    ),
)
def test_release_validation_rejects_unbound_hil_matrix_components(tmp_path, mutation):
    identity = load_script("lesson_studio_task14_build_identity.py")
    hil_path, _hil, _ = task6_manifest(tmp_path / "hil", profile="hil", binary=b"hil")
    prod_path, _prod, _ = task6_manifest(
        tmp_path / "prod", profile="production", binary=b"prod"
    )
    pair = identity.validate_build_pair(hil_path, prod_path)
    matrix_root = tmp_path / "matrix"
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


def _matrix_evidence_payload(root, hil_identity):
    identity = load_script("lesson_studio_task14_build_identity.py")
    Path(root).mkdir(parents=True, exist_ok=True)
    device_id = "28:84:85:85:1a:80"
    device_uuid = "fce7bec8-8478-4ab4-817f-7b87c41c1f91"
    connection = {"deviceId": device_id, "clientId": device_uuid}
    scenarios = []
    for scenario in identity.HIL_STORAGE_SCENARIOS:
        directory = Path(root) / scenario
        directory.mkdir()
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
                "buildIdentity": hil_identity,
                "deviceId": device_id,
                "deviceUuid": device_uuid,
                "connectionIdentity": connection,
            }, sort_keys=True) + "\n"
        ).encode()
        payloads["evidence.json"] = (
            json.dumps({
                "scenario": scenario,
                "status": "PASS",
                "validationErrors": [],
            }, sort_keys=True) + "\n"
        ).encode()
        payloads["validator-exit-code.txt"] = b"0\n"
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


def release_evidence_payload(
    event,
    hil_identity,
    production_identity,
    ledger_path=None,
    evidence_root=None,
):
    if event == "hil-flash":
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
