import copy

import pytest


def available_identity():
    return {
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


def legacy_status():
    return {
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
    }


def test_sd_contract_accepts_exact_v1_and_v2_responses():
    from scripts.hil_storage_identity_contract import validate_status_response

    v1 = legacy_status()
    v2 = {**v1, "schemaVersion": 2, "storageIdentity": available_identity()}
    assert validate_status_response(v1, schema_version=1) == v1
    assert validate_status_response(v2, schema_version=2) == v2


@pytest.mark.parametrize("state", ["unavailable", "invalid", "card_swapped"])
def test_sd_contract_accepts_only_terminal_minimal_failure_envelopes(state):
    from scripts.hil_storage_identity_contract import validate_status_response

    response = {
        **legacy_status(),
        "schemaVersion": 2,
        "storageIdentity": {"status": state, "kind": "sdmmc-fat"},
    }
    assert validate_status_response(response, schema_version=2) == response
    response["storageIdentity"]["volumeSerial"] = "7a31f09c"
    with pytest.raises(ValueError):
        validate_status_response(response, schema_version=2)


@pytest.mark.parametrize(
    "field,bad",
    [
        ("volumeSerial", "7A31F09C"),
        ("capacitySectors", True),
        ("sectorSizeBytes", 512.0),
        ("capacityBytes", 1),
        ("mountGeneration", 0),
    ],
)
def test_sd_contract_rejects_malformed_or_inconsistent_identity(field, bad):
    from scripts.hil_storage_identity_contract import validate_status_response

    identity = available_identity()
    identity[field] = bad
    response = {**legacy_status(), "schemaVersion": 2, "storageIdentity": identity}
    with pytest.raises(ValueError):
        validate_status_response(response, schema_version=2)


def test_sd_contract_detects_a_physical_identity_change():
    from scripts.hil_storage_identity_contract import require_same_storage_identity

    before = available_identity()
    after = copy.deepcopy(before)
    after["cid"]["serial"] += 1
    after["cidFingerprint"] = "1b-534d-3030303030-10-4a5f7d3e-17b"
    with pytest.raises(ValueError, match="physical SD identity changed"):
        require_same_storage_identity(before, after)
