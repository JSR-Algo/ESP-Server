import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DRIVER = ROOT / "scripts/lesson_studio_task14_hil_storage.py"


def load_driver():
    spec = importlib.util.spec_from_file_location("task14_combined_hil_driver", DRIVER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_sd_hil_driver_uses_separate_contract_and_schema_v2_only_for_reads():
    source = DRIVER.read_text(encoding="utf-8")
    assert "from hil_storage_identity_contract import" in source
    assert '"schemaVersion": 2' in source
    assert "schema_version=2" in source
    assert "require_same_storage_identity(" in source
    assert source.count('"schemaVersion": 2') == 2


def test_sd_hil_identity_failure_precedes_generic_cleanup_recovery():
    source = DRIVER.read_text(encoding="utf-8")
    assert "class HilIdentityTerminalError" in source
    assert source.index("except HilIdentityTerminalError") < source.index(
        "if staged and not cleaned"
    )


def test_sd_hil_driver_accepts_separate_esp_attestation_namespace():
    source = DRIVER.read_text(encoding="utf-8")
    for field in (
        "identitySchemaVersion",
        "buildIdentity",
        "buildIdentityId",
        "connectionBindingId",
    ):
        assert field in source
    assert "approved_build_identity_id" in source


def test_sd_hil_mutations_use_the_latest_read_only_attestation_binding():
    source = DRIVER.read_text(encoding="utf-8")
    assert '"expectedBuildIdentityId"' in source
    assert '"expectedConnectionBindingId"' in source
    assert "_update_attestation_binding" in source


def test_sd_and_esp_namespaces_bind_read_only_inspection_to_mutation():
    module = load_driver()
    build_identity = {
        "schemaVersion": 1,
        "hilProfile": "task14-hil-v1",
        "projectName": "xiaozhi",
        "projectVersion": "2.2.75",
        "idfVersion": "v5.4.1",
        "secureVersion": 0,
        "elfSha256": "a" * 64,
        "appSha256": "b" * 64,
        "buildId": "tbot-esp-v1:" + "a" * 64,
    }
    identity_id = module.approved_build_identity_id(build_identity)
    storage_identity = {
        "status": "available",
        "kind": "sdmmc-fat",
        "cidFingerprint": "1b-534d-3030303030-10-4a5f7d3d-17b",
        "cid": {
            "manufacturerId": 0x1B,
            "oemId": 0x534D,
            "productName": "00000",
            "revision": 0x10,
            "serial": 0x4A5F7D3D,
            "manufacturingDate": 0x17B,
        },
        "capacitySectors": 62333952,
        "sectorSizeBytes": 512,
        "capacityBytes": 31914983424,
        "mountGeneration": 1,
        "volumeSerial": "7a31f09c",
        "volumeLabel": "TBOT_HIL",
    }
    common = {
        "schemaVersion": 2,
        "storageIdentity": storage_identity,
        "identitySchemaVersion": 1,
        "buildIdentity": build_identity,
        "buildIdentityId": identity_id,
        "connectionBindingId": "connection-one",
    }
    status = {
        "status": "idle",
        "cacheKey": "",
        "armed": False,
        "reached": False,
        "consumed": False,
        "operation": "evict",
        "checkpoint": "before_first_unlink",
        "action": "fail",
        "threshold": 0,
        "declaredAssetBytes": 0,
        "pauseSeconds": 0,
        "armSequence": 0,
        "reachedSequence": 0,
        "consumedSequence": 0,
        **common,
    }
    inspect = {
        "cacheKey": "cache",
        "siblingCacheKey": "sibling",
        "status": "inspected",
        "truncated": False,
        "entries": [],
        **common,
    }
    fixture = {
        "cacheKey": "cache",
        "siblingCacheKey": "sibling",
        "fixture": "preservation_set",
        "status": "staged",
        "changed": True,
    }

    class Transport:
        calls = []

        def call(self, tool, args, _timeout):
            self.calls.append((tool, args))
            if tool.endswith("status"):
                return status
            if tool.endswith("inspect"):
                return inspect
            return fixture

    transport = Transport()
    local_build = {
        "sourceCommit": "c" * 40,
        "profile": "hil",
        "configEnabled": True,
        "sdkconfigSha256": "d" * 64,
        "binarySha256": "b" * 64,
        "elfSha256": "a" * 64,
        "mapSha256": "e" * 64,
        "archiveSha256": "f" * 64,
        "binaryBytes": 1,
        "appPartitionFreeBytes": 1,
    }
    client = module.HilToolClient(transport, local_build)
    client.status()
    client.inspect("cache", "sibling")
    client.stage("cache", "preservation_set", "sibling")

    mutation_args = transport.calls[-1][1]
    assert mutation_args["expectedBuildIdentityId"] == identity_id
    assert mutation_args["expectedConnectionBindingId"] == "connection-one"


def test_remote_approved_build_must_match_exact_local_manifest_before_mutation():
    module = load_driver()
    remote_build = {
        "schemaVersion": 1,
        "hilProfile": "task14-hil-v1",
        "projectName": "xiaozhi",
        "projectVersion": "2.2.75",
        "idfVersion": "v5.4.1",
        "secureVersion": 0,
        "elfSha256": "a" * 64,
        "appSha256": "b" * 64,
        "buildId": "tbot-esp-v1:" + "a" * 64,
    }
    remote_id = module.approved_build_identity_id(remote_build)
    status = {
        "status": "idle",
        "cacheKey": "",
        "armed": False,
        "reached": False,
        "consumed": False,
        "operation": "evict",
        "checkpoint": "before_first_unlink",
        "action": "fail",
        "threshold": 0,
        "declaredAssetBytes": 0,
        "pauseSeconds": 0,
        "armSequence": 0,
        "reachedSequence": 0,
        "consumedSequence": 0,
        "schemaVersion": 2,
        "storageIdentity": {
            "status": "available",
            "kind": "sdmmc-fat",
            "cidFingerprint": "1b-534d-3030303030-10-4a5f7d3d-17b",
            "cid": {
                "manufacturerId": 0x1B,
                "oemId": 0x534D,
                "productName": "00000",
                "revision": 0x10,
                "serial": 0x4A5F7D3D,
                "manufacturingDate": 0x17B,
            },
            "capacitySectors": 62333952,
            "sectorSizeBytes": 512,
            "capacityBytes": 31914983424,
            "mountGeneration": 1,
            "volumeSerial": "7a31f09c",
            "volumeLabel": "TBOT_HIL",
        },
        "identitySchemaVersion": 1,
        "buildIdentity": remote_build,
        "buildIdentityId": remote_id,
        "connectionBindingId": "connection-one",
    }

    class Transport:
        def __init__(self):
            self.calls = []

        def call(self, tool, args, _timeout):
            self.calls.append((tool, args))
            return status

    local_build = {
        "sourceCommit": "c" * 40,
        "profile": "hil",
        "configEnabled": True,
        "sdkconfigSha256": "d" * 64,
        "binarySha256": "9" * 64,
        "elfSha256": "a" * 64,
        "mapSha256": "e" * 64,
        "archiveSha256": "f" * 64,
        "binaryBytes": 1,
        "appPartitionFreeBytes": 1,
    }
    transport = Transport()
    client = module.HilToolClient(transport, local_build)

    try:
        client.status()
    except module.HilIdentityTerminalError as exc:
        assert "local build manifest" in str(exc)
    else:
        raise AssertionError("approved remote build mismatch must be terminal")

    assert [tool for tool, _args in transport.calls] == [module.HIL_TOOL_NAMES["status"]]


def test_run_scenario_binds_client_to_loaded_local_manifest():
    source = DRIVER.read_text(encoding="utf-8")
    assert "HilToolClient(transport, build_identity)" in source


def test_local_manifest_binding_is_rechecked_on_post_read_and_before_stage():
    module = load_driver()
    remote_build = {
        "schemaVersion": 1,
        "hilProfile": "task14-hil-v1",
        "projectName": "xiaozhi",
        "projectVersion": "2.2.75",
        "idfVersion": "v5.4.1",
        "secureVersion": 0,
        "elfSha256": "a" * 64,
        "appSha256": "b" * 64,
        "buildId": "tbot-esp-v1:" + "a" * 64,
    }
    identity_id = module.approved_build_identity_id(remote_build)
    storage_identity = {
        "status": "available",
        "kind": "sdmmc-fat",
        "cidFingerprint": "1b-534d-3030303030-10-4a5f7d3d-17b",
        "cid": {
            "manufacturerId": 0x1B,
            "oemId": 0x534D,
            "productName": "00000",
            "revision": 0x10,
            "serial": 0x4A5F7D3D,
            "manufacturingDate": 0x17B,
        },
        "capacitySectors": 62333952,
        "sectorSizeBytes": 512,
        "capacityBytes": 31914983424,
        "mountGeneration": 1,
        "volumeSerial": "7a31f09c",
        "volumeLabel": "TBOT_HIL",
    }
    common = {
        "schemaVersion": 2,
        "storageIdentity": storage_identity,
        "identitySchemaVersion": 1,
        "buildIdentity": remote_build,
        "buildIdentityId": identity_id,
        "connectionBindingId": "connection-one",
    }
    status = {
        "status": "idle",
        "cacheKey": "",
        "armed": False,
        "reached": False,
        "consumed": False,
        "operation": "evict",
        "checkpoint": "before_first_unlink",
        "action": "fail",
        "threshold": 0,
        "declaredAssetBytes": 0,
        "pauseSeconds": 0,
        "armSequence": 0,
        "reachedSequence": 0,
        "consumedSequence": 0,
        **common,
    }
    inspect = {
        "cacheKey": "cache",
        "siblingCacheKey": "sibling",
        "status": "inspected",
        "truncated": False,
        "entries": [],
        **common,
    }

    class Transport:
        def __init__(self):
            self.calls = []

        def call(self, tool, args, _timeout):
            self.calls.append((tool, args))
            return status if tool.endswith("status") else inspect

    local_build = {
        "sourceCommit": "c" * 40,
        "profile": "hil",
        "configEnabled": True,
        "sdkconfigSha256": "d" * 64,
        "binarySha256": "b" * 64,
        "elfSha256": "a" * 64,
        "mapSha256": "e" * 64,
        "archiveSha256": "f" * 64,
        "binaryBytes": 1,
        "appPartitionFreeBytes": 1,
    }
    post_transport = Transport()
    post_client = module.HilToolClient(post_transport, local_build)
    post_client.status()
    inspect["buildIdentity"] = {**remote_build, "appSha256": "9" * 64}
    inspect["buildIdentity"]["buildId"] = "tbot-esp-v1:" + "a" * 64
    inspect["buildIdentityId"] = module.approved_build_identity_id(
        inspect["buildIdentity"]
    )
    with pytest.raises(module.HilIdentityTerminalError, match="local build manifest"):
        post_client.inspect("cache", "sibling")

    inspect["buildIdentity"] = remote_build
    inspect["buildIdentityId"] = identity_id
    stage_transport = Transport()
    stage_client = module.HilToolClient(stage_transport, local_build)
    stage_client.status()
    stage_client.inspect("cache", "sibling")
    stage_client.local_build_identity["binarySha256"] = "9" * 64
    with pytest.raises(module.HilIdentityTerminalError, match="local build manifest"):
        stage_client.stage("cache", "preservation_set", "sibling")
    assert [tool for tool, _args in stage_transport.calls] == [
        module.HIL_TOOL_NAMES["status"],
        module.HIL_TOOL_NAMES["inspect"],
    ]
