"""Exact retained owner envelopes; descriptor bytes are canonicalized by the producer."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any

from core.lesson.cache_key_contract import CacheEvictionRefused, compose_cache_key

CONTRACT = "retained-assignment-pack.v1"
DEVICE_CONTRACT = "retained-assignment-device.v1"
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
HASH = re.compile(r"[0-9a-f]{64}")
SELECTION_FIELDS = {"lessonRowId", "lessonKey", "lessonVersion", "profile",
                    "manifestVersion", "manifestChecksum", "cacheKey", "packDescriptorChecksum"}
OP_FIELDS = {"contractVersion", "action", "operationId", "requestId", "requestRevision",
             "deviceId", "consumerIdentity", "desiredSelectionRevision", "selection"}
RECEIPT_FIELDS = (OP_FIELDS - {"action", "selection"}) | {"cacheKey", "packDescriptorChecksum", "state"}


class RetainedContractError(ValueError):
    code = "INVALID_RETAINED_PACK_CONTRACT"


def _fail():
    raise RetainedContractError("Invalid retained pack contract")


def uuid_value(value: Any) -> str:
    if not isinstance(value, str) or UUID.fullmatch(value) is None:
        _fail()
    return value


def revision(value: Any) -> int:
    if type(value) is not int or not 1 <= value <= 9007199254740991:
        _fail()
    return value


def _object(value: Any, fields: set[str]) -> dict:
    if not isinstance(value, dict) or set(value) != fields:
        _fail()
    return value


def parse_selection(value: Any) -> dict:
    row = _object(value, SELECTION_FIELDS)
    uuid_value(row["lessonRowId"])
    revision(row["lessonVersion"])
    if (row["profile"] != "espTft" or not isinstance(row["manifestVersion"], str)
            or re.fullmatch(r"teebot-lesson-renderer\.v[1-5]", row["manifestVersion"]) is None):
        _fail()
    for key in ["manifestChecksum", "packDescriptorChecksum"]:
        if not isinstance(row[key], str) or HASH.fullmatch(row[key]) is None:
            _fail()
    try:
        expected = compose_cache_key(row["lessonKey"], row["lessonVersion"], row["manifestChecksum"])
    except CacheEvictionRefused:
        _fail()
    if row["cacheKey"] != expected:
        _fail()
    return copy.deepcopy(row)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail()
        result[key] = value
    return result


def descriptor(operation: dict) -> dict:
    raw = operation.get("packCanonicalJson")
    if not isinstance(raw, str):
        _fail()
    try:
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        pack = json.loads(raw, object_pairs_hook=_unique_object, parse_constant=lambda _: _fail())
    except (ValueError, UnicodeError):
        _fail()
    selection = operation["selection"]
    if digest != selection["packDescriptorChecksum"] or not isinstance(pack, dict):
        _fail()
    fields = {"lessonId", "lessonVersion", "profile", "manifestChecksum", "cacheKey", "assets"}
    if "courseModeCompatibility" in pack:
        fields.add("courseModeCompatibility")
    _object(pack, fields)
    if type(pack["lessonVersion"]) is not int:
        _fail()
    for pack_key, selection_key in [("lessonId", "lessonKey"), ("lessonVersion", "lessonVersion"),
                                    ("profile", "profile"), ("manifestChecksum", "manifestChecksum"),
                                    ("cacheKey", "cacheKey")]:
        if pack[pack_key] != selection[selection_key]:
            _fail()
    if not isinstance(pack["assets"], list) or not 1 <= len(pack["assets"]) <= 64:
        _fail()
    return pack


def parse_operation(value: Any) -> dict:
    if not isinstance(value, dict):
        _fail()
    action = value.get("action")
    if not isinstance(action, str) or action not in {"acquire", "bind", "reconcile", "release"}:
        _fail()
    extra = {"packCanonicalJson"} if action == "acquire" else {"assignmentId", "assignmentVersion"} if action == "bind" else set()
    row = _object(value, OP_FIELDS | extra)
    if row["contractVersion"] != CONTRACT:
        _fail()
    for key in ["operationId", "requestId", "deviceId", "consumerIdentity"]:
        uuid_value(row[key])
    for key in ["requestRevision", "desiredSelectionRevision"]:
        revision(row[key])
    parse_selection(row["selection"])
    if action == "acquire":
        descriptor(row)
    if action == "bind":
        uuid_value(row["assignmentId"])
        revision(row["assignmentVersion"])
    return copy.deepcopy(row)


def parse_receipt(value: Any, operation: dict) -> dict:
    row = _object(value, RECEIPT_FIELDS)
    revision(row["requestRevision"])
    revision(row["desiredSelectionRevision"])
    for key in OP_FIELDS - {"action", "selection"}:
        if row[key] != operation[key]:
            _fail()
    for key in ["cacheKey", "packDescriptorChecksum"]:
        if row[key] != operation["selection"][key]:
            _fail()
    allowed = {"acquire": {"materialized"}, "bind": {"bound"},
               "reconcile": {"protected", "release_pending", "released"},
               "release": {"release_pending", "released"}}
    if not isinstance(row["state"], str) or row["state"] not in allowed[operation["action"]]:
        _fail()
    return copy.deepcopy(row)


def receipt_for(operation: dict, state: str) -> dict:
    result = {key: operation[key] for key in OP_FIELDS - {"action", "selection"}}
    result.update({key: operation["selection"][key] for key in ["cacheKey", "packDescriptorChecksum"]})
    result["state"] = state
    return parse_receipt(result, operation)


def parse_device_receipt(value: Any, operation: dict) -> dict:
    op = parse_operation(operation)
    if op['action'] not in {'bind', 'release'}:
        _fail()
    if isinstance(value, str):
        value = json.loads(value, object_pairs_hook=_unique_object, parse_constant=lambda _: _fail())
    extra = {'assignmentId', 'assignmentVersion'} if op['action'] == 'bind' else set()
    row = _object(value, RECEIPT_FIELDS | extra)
    revision(row['requestRevision'])
    revision(row['desiredSelectionRevision'])
    if row['contractVersion'] != DEVICE_CONTRACT:
        _fail()
    for key in OP_FIELDS - {'action', 'selection', 'contractVersion'}:
        if row[key] != op[key]:
            _fail()
    for key in ['cacheKey', 'packDescriptorChecksum']:
        if row[key] != op['selection'][key]:
            _fail()
    if row['state'] != ('bound' if op['action'] == 'bind' else 'released'):
        _fail()
    if extra:
        revision(row['assignmentVersion'])
        if any(row[key] != op[key] for key in extra):
            _fail()
    return copy.deepcopy(row)
