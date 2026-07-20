from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

_HEADERS = {
    "schemaVersion": "x-tbot-build-schema",
    "hilProfile": "x-tbot-hil-profile",
    "projectName": "x-tbot-project-name",
    "projectVersion": "x-tbot-project-version",
    "idfVersion": "x-tbot-idf-version",
    "secureVersion": "x-tbot-secure-version",
    "elfSha256": "x-tbot-elf-sha256",
    "appSha256": "x-tbot-app-sha256",
    "buildId": "x-tbot-build-id",
}
_SAFE_VALUE = re.compile(r"[A-Za-z0-9._+-]{1,64}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_IDENTITY_FIELDS = tuple(_HEADERS)


@dataclass(frozen=True)
class EspBuildIdentityParse:
    status: str
    identity: dict[str, Any] | None = None
    reason: str | None = None


@dataclass(frozen=True)
class EspBuildIdentityApproval:
    status: str
    identity: dict[str, Any] | None = None
    identity_id: str | None = None
    reason: str | None = None


def _values(headers: Any, name: str) -> list[str]:
    if headers is None:
        return []
    for getter_name in ("get_all", "getall"):
        getter = getattr(headers, getter_name, None)
        if callable(getter):
            try:
                values = getter(name)
            except TypeError:
                values = getter(name, [])
            return [str(value) for value in values]
    items = getattr(headers, "items", None)
    if callable(items):
        return [str(value) for key, value in items() if str(key).lower() == name]
    return []


def parse_esp_build_identity(headers: Any) -> EspBuildIdentityParse:
    collected = {field: _values(headers, name) for field, name in _HEADERS.items()}
    if all(not values for values in collected.values()):
        return EspBuildIdentityParse("missing")
    if any(not values for values in collected.values()):
        return EspBuildIdentityParse("rejected", reason="missing_header")
    if any(len(values) != 1 for values in collected.values()):
        return EspBuildIdentityParse("rejected", reason="duplicate_header")
    raw = {field: values[0] for field, values in collected.items()}
    if raw["schemaVersion"] != "1":
        return EspBuildIdentityParse("rejected", reason="unsupported_schema")
    if any(
        _SAFE_VALUE.fullmatch(raw[field]) is None
        for field in ("hilProfile", "projectName", "projectVersion", "idfVersion")
    ):
        return EspBuildIdentityParse("rejected", reason="malformed_field")
    if _SHA256.fullmatch(raw["elfSha256"]) is None or _SHA256.fullmatch(raw["appSha256"]) is None:
        return EspBuildIdentityParse("rejected", reason="malformed_field")
    if re.fullmatch(r"0|[1-9][0-9]{0,9}", raw["secureVersion"]) is None:
        return EspBuildIdentityParse("rejected", reason="malformed_field")
    expected_build_id = f'tbot-esp-v1:{raw["elfSha256"]}'
    if raw["buildId"] != expected_build_id:
        return EspBuildIdentityParse("rejected", reason="build_id_mismatch")
    identity = {
        "schemaVersion": 1,
        "hilProfile": raw["hilProfile"],
        "projectName": raw["projectName"],
        "projectVersion": raw["projectVersion"],
        "idfVersion": raw["idfVersion"],
        "secureVersion": int(raw["secureVersion"]),
        "elfSha256": raw["elfSha256"],
        "appSha256": raw["appSha256"],
        "buildId": raw["buildId"],
    }
    return EspBuildIdentityParse("valid", identity=identity)


def approved_identities_from_config(config: Any) -> list[Any]:
    if not isinstance(config, dict):
        return []
    lesson = config.get("lesson")
    if not isinstance(lesson, dict):
        return []
    values = lesson.get("esp_build_identity_approved")
    return list(values) if isinstance(values, list) else []


def _canonical_approved_identity(value: Any) -> tuple[tuple[str, type, Any], ...]:
    if type(value) is not dict or set(value) != set(_IDENTITY_FIELDS):
        raise ValueError("approved identity shape is invalid")
    if type(value["schemaVersion"]) is not int or value["schemaVersion"] != 1:
        raise ValueError("approved identity schema is invalid")
    for name in ("hilProfile", "projectName", "projectVersion", "idfVersion"):
        if type(value[name]) is not str or _SAFE_VALUE.fullmatch(value[name]) is None:
            raise ValueError(f"approved identity {name} is invalid")
    if type(value["secureVersion"]) is not int or value["secureVersion"] < 0:
        raise ValueError("approved identity secureVersion is invalid")
    for name in ("elfSha256", "appSha256"):
        if type(value[name]) is not str or _SHA256.fullmatch(value[name]) is None:
            raise ValueError(f"approved identity {name} is invalid")
    if type(value["buildId"]) is not str or value["buildId"] != "tbot-esp-v1:" + value["elfSha256"]:
        raise ValueError("approved identity buildId is invalid")
    return tuple((name, type(value[name]), value[name]) for name in _IDENTITY_FIELDS)


def approved_build_identity_id(identity: dict[str, Any]) -> str:
    record = _canonical_approved_identity(identity)
    normalized = {name: value for name, _value_type, value in record}
    canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "tbot-esp-approved-v1:" + hashlib.sha256(canonical).hexdigest()


def evaluate_esp_build_identity(
    headers: Any, approved_identities: list[Any]
) -> EspBuildIdentityApproval:
    parsed = parse_esp_build_identity(headers)
    if parsed.status == "missing":
        return EspBuildIdentityApproval("missing", reason=parsed.reason)
    if parsed.status != "valid":
        return EspBuildIdentityApproval("invalid", reason=parsed.reason)
    try:
        claimed = _canonical_approved_identity(parsed.identity)
        approved = [_canonical_approved_identity(candidate) for candidate in approved_identities]
    except ValueError:
        return EspBuildIdentityApproval("unapproved", reason="approved_allowlist_invalid")
    if claimed not in approved:
        return EspBuildIdentityApproval("unapproved", reason="identity_not_approved")
    return EspBuildIdentityApproval(
        "approved",
        identity=parsed.identity,
        identity_id=approved_build_identity_id(parsed.identity),
    )


def esp_build_identity_metrics_fields(
    headers: Any, approved_identities: list[dict[str, Any]]
) -> dict[str, Any]:
    evaluated = evaluate_esp_build_identity(headers, approved_identities)
    return {"buildIdentityStatus": evaluated.status}
