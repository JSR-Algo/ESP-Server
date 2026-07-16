import hashlib
import importlib.util
import json
import os
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
    return {
        "status": state,
        "cacheKey": KEY if state != "idle" else "",
        "armed": state == "armed",
        "reached": state in ("reached", "consumed"),
        "consumed": state == "consumed",
        "operation": "evict",
        "checkpoint": "after_unlinks",
        "action": "fail",
        "threshold": 1,
        "declaredAssetBytes": 0,
        "pauseSeconds": 0,
        "armSequence": 1 if state != "idle" else 0,
        "reachedSequence": 2 if state in ("reached", "consumed") else 0,
        "consumedSequence": 3 if state == "consumed" else 0,
    }


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
    with pytest.raises(hil.HilValidationError):
        hil.validate_fixture_response({**fixture, "changed": 1}, KEY, SIBLING, "preservation_set", "staged")
    with pytest.raises(hil.HilValidationError):
        hil.validate_inspect_response({**inspect, "entries": [{**inspect["entries"][0], "bytes": False}]}, KEY, SIBLING)


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


def test_cleanup_status_inspect_order_and_sequence_validation():
    hil = load_script("lesson_studio_task14_hil_storage.py")
    events = ["status-before", "inspect-before", "stage", "arm", "trigger", "status-after", "inspect-after", "cleanup"]
    assert hil.validate_event_order(events) == events
    with pytest.raises(hil.HilValidationError):
        hil.validate_event_order(events[:-2] + ["cleanup", "inspect-after"])
    assert hil.validate_sequences(exact_arm(), exact_status()) == {"arm": 1, "reached": 2, "consumed": 3}
    with pytest.raises(hil.HilValidationError):
        hil.validate_sequences(exact_arm(), {**exact_status(), "reachedSequence": 1})


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


def test_release_order_requires_hil_matrix_then_production_reflash_then_soak():
    identity = load_script("lesson_studio_task14_build_identity.py")
    events = ["hil-flash", "hil-matrix-pass", "production-reflash", "production-attest", "production-soak"]
    assert identity.validate_release_order(events) == events
    with pytest.raises(identity.BuildIdentityError):
        identity.validate_release_order(["hil-flash", "production-soak", "production-reflash"])


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
        ("evict-after-unlinks-fail", eviction_result("unlink_failed", 1)),
        ("evict-before-rmdir-fail", eviction_result("rmdir_failed", 1)),
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
            "http://user:password@192.168.0.2/a.png?token=query-secret",
            "--note",
            secret,
        ],
        (secret,),
    )
    for forbidden in ("user", "password", "query-secret", secret):
        assert forbidden not in command


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
