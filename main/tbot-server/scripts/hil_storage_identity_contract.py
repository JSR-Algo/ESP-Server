"""Exact offline model for Task 14 HIL storage-identity schema versioning."""

from __future__ import annotations

import copy
import re

STATUS_V1_FIELDS = frozenset(
    {
        "status",
        "cacheKey",
        "armed",
        "reached",
        "consumed",
        "operation",
        "checkpoint",
        "action",
        "threshold",
        "declaredAssetBytes",
        "pauseSeconds",
        "armSequence",
        "reachedSequence",
        "consumedSequence",
    }
)
INSPECT_V1_FIELDS = frozenset(
    {"cacheKey", "siblingCacheKey", "status", "truncated", "entries"}
)
IDENTITY_AVAILABLE_FIELDS = frozenset(
    {
        "status",
        "kind",
        "cidFingerprint",
        "cid",
        "capacitySectors",
        "sectorSizeBytes",
        "capacityBytes",
        "mountGeneration",
        "volumeSerial",
        "volumeLabel",
    }
)
CID_FIELDS = frozenset(
    {
        "manufacturerId",
        "oemId",
        "productName",
        "revision",
        "serial",
        "manufacturingDate",
    }
)
FAILURE_STATES = frozenset({"unavailable", "invalid", "card_swapped"})
LOWER_HEX_8 = re.compile(r"[0-9a-f]{8}\Z")
CID_FINGERPRINT = re.compile(
    r"[0-9a-f]{2}-[0-9a-f]{4}-[0-9a-f]{10}-[0-9a-f]{2}-[0-9a-f]{8}-[0-9a-f]{3}\Z"
)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _exact_int(value: object, minimum: int, maximum: int, name: str) -> int:
    _require(type(value) is int, f"{name} must be an integer")
    _require(minimum <= value <= maximum, f"{name} out of range")
    return value


def _printable_ascii(value: object, maximum: int, name: str, *, allow_empty: bool) -> str:
    _require(isinstance(value, str), f"{name} must be a string")
    _require(allow_empty or bool(value), f"{name} must not be empty")
    _require(len(value) <= maximum, f"{name} too long")
    _require(all(0x20 <= ord(character) <= 0x7E for character in value), f"{name} invalid")
    return value


def _canonical_cid_fingerprint(cid: dict) -> str:
    product_hex = "".join(f"{ord(character):02x}" for character in cid["productName"])
    return (
        f'{cid["manufacturerId"]:02x}-{cid["oemId"]:04x}-{product_hex}-'
        f'{cid["revision"]:02x}-{cid["serial"]:08x}-{cid["manufacturingDate"]:03x}'
    )


def validate_storage_identity(value: object) -> dict:
    _require(isinstance(value, dict), "storageIdentity must be an object")
    status = value.get("status")
    if status in FAILURE_STATES:
        _require(set(value) == {"status", "kind"}, "failure identity must not be partial")
        _require(value.get("kind") == "sdmmc-fat", "identity kind mismatch")
        return copy.deepcopy(value)

    _require(status == "available", "identity status invalid")
    _require(set(value) == IDENTITY_AVAILABLE_FIELDS, "identity fields mismatch")
    _require(value.get("kind") == "sdmmc-fat", "identity kind mismatch")

    cid = value.get("cid")
    _require(isinstance(cid, dict) and set(cid) == CID_FIELDS, "CID fields mismatch")
    _exact_int(cid["manufacturerId"], 1, 0xFF, "manufacturerId")
    _exact_int(cid["oemId"], 1, 0xFFFF, "oemId")
    product_name = _printable_ascii(
        cid["productName"], 5, "productName", allow_empty=False
    )
    _require(len(product_name) == 5, "productName must contain exactly five bytes")
    _exact_int(cid["revision"], 0, 0xFF, "revision")
    _exact_int(cid["serial"], 1, 0xFFFFFFFF, "serial")
    _exact_int(cid["manufacturingDate"], 1, 0xFFF, "manufacturingDate")

    fingerprint = value.get("cidFingerprint")
    _require(isinstance(fingerprint, str) and CID_FINGERPRINT.fullmatch(fingerprint),
             "CID fingerprint malformed")
    _require(fingerprint == _canonical_cid_fingerprint(cid), "CID fingerprint mismatch")

    sectors = _exact_int(value.get("capacitySectors"), 1, 0x7FFFFFFFFFFFFFFF, "capacitySectors")
    sector_size = _exact_int(value.get("sectorSizeBytes"), 1, 0xFFFFFFFF, "sectorSizeBytes")
    capacity_bytes = _exact_int(value.get("capacityBytes"), 1, 0xFFFFFFFFFFFFFFFF, "capacityBytes")
    _require(sectors * sector_size == capacity_bytes, "capacity arithmetic mismatch")
    _exact_int(value.get("mountGeneration"), 1, 0xFFFFFFFFFFFFFFFF, "mountGeneration")

    volume_serial = value.get("volumeSerial")
    _require(isinstance(volume_serial, str) and LOWER_HEX_8.fullmatch(volume_serial),
             "volume serial malformed")
    _require(volume_serial != "00000000", "volume serial uninitialized")
    _printable_ascii(value.get("volumeLabel"), 11, "volumeLabel", allow_empty=True)
    return copy.deepcopy(value)


def _validate_schema_version(schema_version: object) -> int:
    _require(type(schema_version) is int and schema_version in (1, 2),
             "schema version must be integer 1 or 2")
    return schema_version


def _validate_response(value: object, schema_version: object, legacy_fields: frozenset[str]) -> dict:
    version = _validate_schema_version(schema_version)
    _require(isinstance(value, dict), "response must be an object")
    expected = legacy_fields if version == 1 else legacy_fields | {"schemaVersion", "storageIdentity"}
    _require(set(value) == expected, "response fields mismatch")
    if version == 2:
        _require(value.get("schemaVersion") == 2 and type(value.get("schemaVersion")) is int,
                 "response schema version mismatch")
        validate_storage_identity(value.get("storageIdentity"))
    return copy.deepcopy(value)


def validate_status_response(value: object, *, schema_version: object = 1) -> dict:
    return _validate_response(value, schema_version, STATUS_V1_FIELDS)


def validate_inspect_response(value: object, *, schema_version: object = 1) -> dict:
    return _validate_response(value, schema_version, INSPECT_V1_FIELDS)


def require_same_storage_identity(before: object, after: object) -> bool:
    left = validate_storage_identity(before)
    right = validate_storage_identity(after)
    _require(left == right, "physical SD identity changed")
    _require(left.get("status") == "available", "physical SD identity unavailable")
    return True
