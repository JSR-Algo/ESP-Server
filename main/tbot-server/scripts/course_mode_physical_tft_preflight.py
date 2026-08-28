#!/usr/bin/env python3
"""Fail-closed, no-device preflight for the attended W1 physical TFT lane."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlparse

ARTIFACT_ROOT = Path("/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-task07")
COURSE_ID = "a17792f6-8d86-4ad1-a6f3-77663b4d4674"
COURSE_KEY = "english-6month-4-6"
LESSON_KEY = "w01-greetings-politeness"
CONTRACT_VERSION = "courseCompanion.v2.contract.v1"
RENDERER = "teebot-lesson-renderer.v5"
LAYOUT = "renderer-v5.layered-cinematic-layout.v1"
CONTRACT_CHECKSUM = "52303f656b6b21e4a65fc1a7179f7668888a7682c2e86b6d1319f201a548c840"
TASK9_SHA = "3b13883b6e8a8f6495c5670ebdc194392e38de75"
ROBOT_MAC = "14:c1:9f:d1:ac:20"
COMPOSE_PROJECT = "tbot-course-mode-physical-tft"
IMAGE_PREFIX = "local/tbot-backend:course-mode-physical-tft-"
MATERIALIZER_PATH = "/app/dist/lessons/course-mode/course-mode-local-materializer.js"
MATERIALIZER_COMMAND = ["dist/lessons/course-mode/course-mode-local-materializer.js", "materialize"]
VOICE_PATH = "main/tbot-server/tests/test_lesson_voice_output_discipline.py"
VOICE_SHA = "08f77b5452301224b17b4b333d2d032fff40c06aa2eaea97fa90932dae7d97e3"
SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")
TOP_FIELDS = {
    "schemaVersion",
    "sessionDirectory",
    "sessionStartedAt",
    "outputDirectory",
    "localStack",
    "repositories",
    "firmwareRequiredAncestor",
    "backendImage",
    "composeProject",
    "reviewedIdentity",
    "materializationReceipt",
    "assignmentSnapshot",
    "visualPack",
    "flashPlan",
    "nvsPreservation",
}
REF_FIELDS = {"path", "algorithm", "sha256"}
FORBIDDEN_OUTPUT = re.compile(r"(?i)(bearer|password|private.?key|secret.?value|transcript|raw.?speech|audio.?data)")


class DuplicateKeyError(ValueError):
    pass


def _strict_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKeyError(key)
        result[key] = value
    return result


def _load_json_bytes(payload: bytes) -> tuple[object | None, str | None]:
    try:
        return json.loads(payload.decode("utf-8"), object_pairs_hook=_strict_pairs), None
    except DuplicateKeyError:
        return None, "duplicate_key"
    except (json.JSONDecodeError, UnicodeError):
        return None, "invalid_json"


def _record(value: object) -> bool:
    return isinstance(value, dict)


def _real_sha(value: object) -> bool:
    return isinstance(value, str) and SHA256.fullmatch(value) is not None and len(set(value)) > 1


def _valid_uuid(value: object) -> bool:
    if not isinstance(value, str) or UUID.fullmatch(value) is None:
        return False
    compact = value.replace("-", "")
    return len(set(compact)) > 4 and not compact.startswith(("00000000", "11111111", "deadbeef"))


def _normal_mac(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    compact = re.sub(r"[^0-9a-fA-F]", "", value).lower()
    if len(compact) != 12 or re.fullmatch(r"[0-9a-f]{12}", compact) is None:
        return None
    return ":".join(compact[index : index + 2] for index in range(0, 12, 2))


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _safe_file(value: object, session: Path | None) -> tuple[Path | None, str | None]:
    if not isinstance(value, str):
        return None, "type"
    path = Path(value)
    if not path.is_absolute() or session is None:
        return None, "absolute"
    try:
        resolved = path.resolve(strict=True)
        root = session.resolve(strict=True)
    except OSError:
        return None, "missing"
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
        return None, "symlink"
    if not path.is_file() or not _within(resolved, root):
        return None, "boundary"
    return path, None


def _validate_ref(value: object, session: Path | None) -> tuple[Path | None, list[str]]:
    if not _record(value) or set(value) != REF_FIELDS:
        return None, ["schema"]
    path, error = _safe_file(value.get("path"), session)
    reasons = []
    if error:
        reasons.append("path")
    if value.get("algorithm") != "sha256" or not _real_sha(value.get("sha256")):
        reasons.append("hash")
    if path is not None and hashlib.sha256(path.read_bytes()).hexdigest() != value.get("sha256"):
        reasons.append("hash")
    return path, sorted(set(reasons))


def _parse_ref_json(value: object, session: Path | None, prefix: str, reasons: list[str]) -> object | None:
    path, errors = _validate_ref(value, session)
    reasons.extend(f"{prefix}.{error}" for error in errors)
    if path is None or errors:
        return None
    parsed, error = _load_json_bytes(path.read_bytes())
    if error:
        reasons.append(f"{prefix}.{error}")
        return None
    return parsed


def _approved_robot_url(value: object, schemes: set[str], host: object, trailing: bool = False) -> bool:
    if not isinstance(value, str) or not isinstance(host, str) or (trailing and not value.endswith("/")):
        return False
    parsed = urlparse(value)
    if parsed.scheme not in schemes or parsed.username or parsed.password or parsed.hostname != host:
        return False
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        return False
    return (
        address.version == 4
        and address.is_private
        and not address.is_loopback
        and not address.is_link_local
        and not address.is_multicast
        and not address.is_reserved
        and not address.is_unspecified
    )


def validate_local_lab_endpoints(asset_origin: object, ota_url: object, websocket_url: object) -> list[str]:
    values = (
        ("assetOrigin", asset_origin, {"http"}, True),
        ("otaUrl", ota_url, {"http"}, False),
        ("websocketUrl", websocket_url, {"ws"}, False),
    )
    reasons: list[str] = []
    hosts: set[str] = set()
    for field, value, schemes, trailing in values:
        host = urlparse(value).hostname if isinstance(value, str) else None
        if not _approved_robot_url(value, schemes, host, trailing):
            reasons.append(field)
        elif host is not None:
            hosts.add(host)
    if len(hosts) > 1:
        reasons.append("localLabRoute")
    return sorted(set(reasons))


def _validate_local_stack(value: object, reasons: list[str]) -> None:
    fields = {
        "environment",
        "productionMutationAllowed",
        "databaseAuthority",
        "adminMutationAllowed",
        "backendBaseUrl",
        "adminBaseUrl",
        "espHttpUrl",
        "robotLanHost",
        "assetOrigin",
        "otaUrl",
        "websocketUrl",
    }
    if not _record(value) or set(value) != fields:
        reasons.append("local_stack.schema")
        return
    if (
        value.get("environment") != "local-attended-lab"
        or value.get("productionMutationAllowed") is not False
        or value.get("adminMutationAllowed") is not False
        or value.get("databaseAuthority") != "isolated-local-compose"
        or value.get("backendBaseUrl") != "http://127.0.0.1:3000"
        or value.get("adminBaseUrl") != "http://127.0.0.1:3001"
        or value.get("espHttpUrl") != "http://host.docker.internal:8003"
    ):
        reasons.append("local_stack.production_guard")
    host = value.get("robotLanHost")
    if (
        not _approved_robot_url(value.get("assetOrigin"), {"http"}, host, True)
        or not _approved_robot_url(value.get("otaUrl"), {"http"}, host)
        or not _approved_robot_url(value.get("websocketUrl"), {"ws"}, host)
    ):
        reasons.append("local_stack.endpoints")


def _validate_repositories(value: object, task9: object, reasons: list[str]) -> None:
    if not _record(value) or set(value) != {"backend", "esp", "firmware"}:
        reasons.append("repository.schema")
        return
    for name in ("backend", "esp", "firmware"):
        repo = value.get(name)
        prefix = f"repository.{name}"
        if not _record(repo) or set(repo) != {"path", "sha", "dirtyExceptions"}:
            reasons.append(f"{prefix}.schema")
            continue
        path_value = repo.get("path")
        path = Path(path_value) if isinstance(path_value, str) else None
        if path is None or not path.is_absolute() or not path.is_dir() or path.is_symlink():
            reasons.append(f"{prefix}.path")
        if not isinstance(repo.get("sha"), str) or SHA40.fullmatch(repo["sha"]) is None:
            reasons.append(f"{prefix}.sha")
        exceptions = repo.get("dirtyExceptions")
        if not isinstance(exceptions, list):
            reasons.append(f"{prefix}.dirty_exceptions")
            continue
        expected = [{"path": VOICE_PATH, "sha256": VOICE_SHA}] if name == "esp" else []
        if exceptions != expected:
            reasons.append(f"{prefix}.dirty_exceptions")
        if path is not None:
            for item in exceptions:
                if not _record(item) or set(item) != {"path", "sha256"}:
                    reasons.append(f"{prefix}.dirty_exceptions")
                    continue
                candidate = path / item["path"] if isinstance(item.get("path"), str) else None
                if (
                    candidate is None
                    or not candidate.is_file()
                    or hashlib.sha256(candidate.read_bytes()).hexdigest() != item.get("sha256")
                ):
                    reasons.append(f"{prefix}.dirty_exception_hash")
    if task9 != TASK9_SHA:
        reasons.append("repository.firmware.ancestor")


def _validate_materialization(value: object, reasons: list[str]) -> dict[str, object] | None:
    fields = {"schemaVersion", "mode", "result", "course", "source", "replacement", "artifactChecksum"}
    if not _record(value) or set(value) != fields:
        reasons.append("materialization.schema")
        return None
    course = value.get("course")
    source = value.get("source")
    replacement = value.get("replacement")
    if (
        value.get("schemaVersion") != 1
        or value.get("mode") != "materialize"
        or value.get("result") != "pass"
        or not _real_sha(value.get("artifactChecksum"))
    ):
        reasons.append("materialization.identity")
    artifact_projection = (
        {key: value[key] for key in ("course", "source", "replacement")}
        if all(key in value for key in ("course", "source", "replacement"))
        else None
    )
    if (
        artifact_projection is None
        or value.get("artifactChecksum")
        != hashlib.sha256(json.dumps(artifact_projection, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    ):
        reasons.append("materialization.artifact_checksum")
    if course != {"courseId": COURSE_ID, "courseKey": COURSE_KEY}:
        reasons.append("materialization.course")
    source_fields = {"lessonId", "lessonKey", "lessonVersion", "status", "assignable"}
    if (
        not _record(source)
        or set(source) != source_fields
        or not _valid_uuid(source.get("lessonId"))
        or source.get("lessonKey") != LESSON_KEY
        or type(source.get("lessonVersion")) is not int
        or not 1 <= source["lessonVersion"] <= 1000
        or source.get("status") not in {"ARCHIVED", "SUPERSEDED"}
        or source.get("assignable") is not False
    ):
        reasons.append("materialization.source")
    replacement_fields = {
        "lessonId",
        "lessonKey",
        "lessonVersion",
        "status",
        "assignable",
        "mappingState",
        "activeReplacementCount",
        "contractVersion",
        "rendererId",
        "visualLayoutContract",
        "contractChecksum",
        "manifestChecksum",
        "stepsChecksum",
        "visualPackChecksum",
    }
    if (
        not _record(replacement)
        or set(replacement) != replacement_fields
        or not _valid_uuid(replacement.get("lessonId"))
        or replacement.get("lessonId") == (source.get("lessonId") if _record(source) else None)
        or replacement.get("lessonKey") != LESSON_KEY
        or type(replacement.get("lessonVersion")) is not int
        or not 1 <= replacement["lessonVersion"] <= 1000
        or replacement.get("status") != "PUBLISHED"
        or replacement.get("assignable") is not True
        or replacement.get("mappingState") != "ACTIVE"
        or replacement.get("activeReplacementCount") != 1
    ):
        reasons.append("materialization.replacement")
    if _record(replacement) and (
        replacement.get("contractVersion"),
        replacement.get("rendererId"),
        replacement.get("visualLayoutContract"),
        replacement.get("contractChecksum"),
    ) != (CONTRACT_VERSION, RENDERER, LAYOUT, CONTRACT_CHECKSUM):
        reasons.append("materialization.contract")
    if _record(replacement) and any(
        not _real_sha(replacement.get(field)) for field in ("manifestChecksum", "stepsChecksum", "visualPackChecksum")
    ):
        reasons.append("materialization.checksum")
    return value if not any(reason.startswith("materialization.") for reason in reasons) else None


def _validate_reviewed(
    value: object, receipt: dict[str, object] | None, backend_sha: object, reasons: list[str]
) -> None:
    fields = {
        "courseId",
        "courseKey",
        "sourceLessonId",
        "replacementId",
        "lessonKey",
        "replacementVersion",
        "contractVersion",
        "rendererId",
        "visualLayoutContract",
        "contractChecksum",
        "manifestChecksum",
        "stepsChecksum",
        "visualPackChecksum",
        "backendSha",
    }
    if not _record(value) or set(value) != fields:
        reasons.append("identity.schema")
        return
    if (
        not _valid_uuid(value.get("sourceLessonId"))
        or not _valid_uuid(value.get("replacementId"))
        or type(value.get("replacementVersion")) is not int
        or not 1 <= value["replacementVersion"] <= 1000
    ):
        reasons.append("identity.replacement")
    if value.get("backendSha") != backend_sha:
        reasons.append("identity.backend")
    if receipt is not None:
        source = receipt["source"]
        replacement = receipt["replacement"]
        expected = {
            "courseId": COURSE_ID,
            "courseKey": COURSE_KEY,
            "sourceLessonId": source["lessonId"],
            "replacementId": replacement["lessonId"],
            "lessonKey": LESSON_KEY,
            "replacementVersion": replacement["lessonVersion"],
            "contractVersion": replacement["contractVersion"],
            "rendererId": replacement["rendererId"],
            "visualLayoutContract": replacement["visualLayoutContract"],
            "contractChecksum": replacement["contractChecksum"],
            "manifestChecksum": replacement["manifestChecksum"],
            "stepsChecksum": replacement["stepsChecksum"],
            "visualPackChecksum": replacement["visualPackChecksum"],
            "backendSha": backend_sha,
        }
        if value != expected:
            reasons.append("identity.receipt_parity")


def _validate_assignment(value: object, receipt: dict[str, object] | None, reasons: list[str]) -> None:
    fields = {"schemaVersion", "result", "robotMac", "assignments", "snapshotChecksum"}
    if not _record(value) or set(value) != fields:
        reasons.append("assignment.schema")
        return
    assignments = value.get("assignments")
    if not isinstance(assignments, list) or len(assignments) != 1 or not _record(assignments[0]):
        reasons.append("assignment.cardinality")
        return
    item = assignments[0]
    item_fields = {"assignmentId", "state", "lessonId", "lessonVersion", "robotMac"}
    replacement = receipt.get("replacement", {}) if receipt else {}
    if (
        value.get("schemaVersion") != 1
        or value.get("result") != "pass"
        or _normal_mac(value.get("robotMac")) != ROBOT_MAC
        or set(item) != item_fields
        or not _valid_uuid(item.get("assignmentId"))
        or item.get("state") != "ACTIVE"
        or item.get("lessonId") != replacement.get("lessonId")
        or item.get("lessonVersion") != replacement.get("lessonVersion")
        or _normal_mac(item.get("robotMac")) != ROBOT_MAC
    ):
        reasons.append("assignment.identity")
    checksum = hashlib.sha256(json.dumps(assignments, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if value.get("snapshotChecksum") != checksum:
        reasons.append("assignment.checksum")


def _validate_visual_pack(
    value: object, receipt: dict[str, object] | None, session: Path | None, reasons: list[str]
) -> None:
    if not _record(value) or set(value) != {
        "state",
        "publicationState",
        "immutable",
        "lessonId",
        "lessonVersion",
        "checksum",
        "phases",
    }:
        reasons.append("visual_pack.schema")
        return
    replacement = receipt.get("replacement", {}) if receipt else {}
    if (
        value.get("state") != "READY"
        or value.get("publicationState") != "published"
        or value.get("immutable") is not True
        or value.get("lessonId") != replacement.get("lessonId")
        or value.get("lessonVersion") != replacement.get("lessonVersion")
    ):
        reasons.append("visual_pack.identity")
    phases = value.get("phases")
    if not isinstance(phases, list) or not phases:
        reasons.append("visual_pack.phase")
        return
    checksum = hashlib.sha256(
        json.dumps({"phases": phases}, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if value.get("checksum") != checksum or value.get("checksum") != replacement.get("visualPackChecksum"):
        reasons.append("visual_pack.checksum")
    phase_fields = {"phaseId", "activityIds", "templateId", "templateVersion", "playbackMode", "layers"}
    layer_fields = {
        "slot",
        "assetKey",
        "assetVersionId",
        "version",
        "state",
        "publicationState",
        "immutable",
        "storagePath",
        "assetPath",
        "sha256",
        "bytes",
        "mediaType",
        "width",
        "height",
        "compatibilityMetadata",
    }
    seen: set[str] = set()
    for phase in phases:
        if not _record(phase) or set(phase) != phase_fields:
            reasons.append("visual_pack.phase")
            continue
        if (
            not isinstance(phase.get("phaseId"), str)
            or not isinstance(phase.get("activityIds"), list)
            or not phase["activityIds"]
            or any(not isinstance(item, str) or not item for item in phase["activityIds"])
            or phase.get("templateId") != "layeredCinematic"
            or phase.get("templateVersion") != 1
            or phase.get("playbackMode") not in {"once", "hold"}
            or not isinstance(phase.get("layers"), list)
        ):
            reasons.append("visual_pack.phase")
            continue
        layers = phase["layers"]
        backgrounds = [layer for layer in layers if _record(layer) and layer.get("slot") == "backgroundScene"]
        objects = [layer for layer in layers if _record(layer) and layer.get("slot") == "teachingObject"]
        videos = [layer for layer in layers if _record(layer) and layer.get("mediaType") == "video/mp4"]
        robots = [layer for layer in videos if layer.get("slot") == "robotOverlay"]
        if len(backgrounds) != 1 or len(objects) > 1 or len(videos) != 1 or len(robots) != 1:
            reasons.append("visual_pack.robot_video")
        for layer in layers:
            if not _record(layer) or set(layer) != layer_fields:
                reasons.append("visual_pack.layer_schema")
                continue
            if layer.get("slot") not in {"backgroundScene", "teachingObject", "robotOverlay"}:
                reasons.append("visual_pack.layer_slot")
            identifiers = (
                layer.get("assetKey"),
                layer.get("assetVersionId"),
                layer.get("storagePath"),
                layer.get("assetPath"),
            )
            if any(not isinstance(item, str) or not item for item in identifiers) or any(
                item in seen for item in identifiers
            ):
                reasons.append("visual_pack.layer_unique")
            else:
                seen.update(identifiers)
            asset, error = _safe_file(layer.get("assetPath"), session)
            if error:
                reasons.append("visual_pack.asset_path")
            if (
                not _valid_uuid(layer.get("assetVersionId"))
                or type(layer.get("version")) is not int
                or layer["version"] <= 0
                or layer.get("state") != "READY"
                or layer.get("publicationState") != "published"
                or layer.get("immutable") is not True
                or not isinstance(layer.get("storagePath"), str)
                or Path(layer["storagePath"]).is_absolute()
                or ".." in Path(layer["storagePath"]).parts
                or not _real_sha(layer.get("sha256"))
                or type(layer.get("bytes")) is not int
                or layer["bytes"] <= 0
                or type(layer.get("width")) is not int
                or layer["width"] <= 0
                or type(layer.get("height")) is not int
                or layer["height"] <= 0
                or not isinstance(layer.get("mediaType"), str)
                or layer["mediaType"] not in {"image/jpeg", "image/png", "video/mp4"}
            ):
                reasons.append("visual_pack.layer")
            if asset is not None and (
                asset.stat().st_size != layer.get("bytes")
                or hashlib.sha256(asset.read_bytes()).hexdigest() != layer.get("sha256")
            ):
                reasons.append("visual_pack.asset_hash")
            metadata = layer.get("compatibilityMetadata")
            if not _record(metadata):
                reasons.append("visual_pack.compatibility")
                continue
            if layer.get("mediaType") == "video/mp4":
                keys = {
                    "mediaKind",
                    "mediaType",
                    "codec",
                    "fps",
                    "durationMs",
                    "frameCount",
                    "hasAudio",
                    "chromaKey",
                    "rect",
                }
                rect = metadata.get("rect")
                chroma = metadata.get("chromaKey")
                if (
                    set(metadata) != keys
                    or metadata.get("mediaKind") != "video"
                    or metadata.get("mediaType") != "video/mp4"
                    or metadata.get("codec") != "mjpeg"
                    or metadata.get("hasAudio") is not False
                    or type(metadata.get("fps")) is not int
                    or metadata["fps"] <= 0
                    or type(metadata.get("durationMs")) is not int
                    or metadata["durationMs"] <= 0
                    or type(metadata.get("frameCount")) is not int
                    or metadata["frameCount"] <= 0
                    or not _record(rect)
                    or set(rect) != {"x", "y", "width", "height"}
                    or any(type(rect.get(key)) is not int for key in rect)
                    or rect.get("width") != layer.get("width")
                    or rect.get("height") != layer.get("height")
                    or not _record(chroma)
                    or set(chroma) != {"keyColor", "tolerance", "featherPx"}
                    or chroma.get("keyColor") != "#00ff00"
                    or type(chroma.get("tolerance")) is not int
                    or not 0 <= chroma["tolerance"] <= 255
                    or type(chroma.get("featherPx")) is not int
                    or not 0 <= chroma["featherPx"] <= 16
                ):
                    reasons.append("visual_pack.compatibility")
            else:
                keys = {"mediaKind", "mediaType", "fit", "rect"}
                rect = metadata.get("rect")
                if (
                    set(metadata) != keys
                    or metadata.get("mediaKind") != "image"
                    or metadata.get("mediaType") != layer.get("mediaType")
                    or metadata.get("fit") not in {"cover", "contain"}
                    or not _record(rect)
                    or set(rect) != {"x", "y", "width", "height"}
                    or any(type(rect.get(key)) is not int for key in rect)
                    or rect.get("width") != layer.get("width")
                    or rect.get("height") != layer.get("height")
                ):
                    reasons.append("visual_pack.compatibility")


def _hex(value: object) -> int | None:
    if not isinstance(value, str) or re.fullmatch(r"0x[0-9a-f]+", value) is None:
        return None
    return int(value, 16)


def _validate_flash(value: object, session: Path | None, reasons: list[str]) -> None:
    if not _record(value) or set(value) != {"partitionSnapshot", "appPartition", "image", "operations"}:
        reasons.append("flash.schema")
        return
    snapshot = _parse_ref_json(value.get("partitionSnapshot"), session, "flash.partition_snapshot", reasons)
    app_claim = value.get("appPartition")
    image = value.get("image")
    operations = value.get("operations")
    app: dict[str, object] | None = None
    if (
        _record(snapshot)
        and set(snapshot) == {"schemaVersion", "flashSize", "partitions", "snapshotChecksum"}
        and snapshot.get("schemaVersion") == 1
        and isinstance(snapshot.get("partitions"), list)
    ):
        partitions = snapshot["partitions"]
        checksum = hashlib.sha256(json.dumps(partitions, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if snapshot.get("snapshotChecksum") != checksum or _hex(snapshot.get("flashSize")) != 0x1000000:
            reasons.append("flash.partition_snapshot.identity")
        expected_names = [
            "bootloader",
            "partition-table",
            "nvs",
            "ota-data",
            "phy-init",
            "reserved",
            "application",
            "generated-assets",
        ]
        previous_end = 0
        for index, partition in enumerate(partitions):
            if not _record(partition) or set(partition) != {"name", "offset", "size", "end", "protected"}:
                reasons.append("flash.partition_snapshot.schema")
                continue
            offset = _hex(partition.get("offset"))
            size = _hex(partition.get("size"))
            end = _hex(partition.get("end"))
            if (
                index >= len(expected_names)
                or partition.get("name") != expected_names[index]
                or offset is None
                or size is None
                or end is None
                or size <= 0
                or end != offset + size
                or offset != previous_end
            ):
                reasons.append("flash.partition_snapshot.range")
            previous_end = end or previous_end
            if partition.get("name") == "application":
                app = partition
                if partition.get("protected") is not False or offset != 0x20000 or end != 0x800000:
                    reasons.append("flash.partition_snapshot.application")
            elif partition.get("protected") is not True:
                reasons.append("flash.partition_snapshot.protected")
        if len(partitions) != len(expected_names) or previous_end != 0x1000000:
            reasons.append("flash.partition_snapshot.range")
    else:
        reasons.append("flash.partition_snapshot.schema")
    if (
        not _record(app_claim)
        or set(app_claim) != {"offset", "size", "end"}
        or app is None
        or app_claim != {key: app[key] for key in ("offset", "size", "end")}
    ):
        reasons.append("flash.app_partition")
    image_path: Path | None = None
    image_doc = image if _record(image) else {}
    if not _record(image) or set(image) != {"path", "algorithm", "sha256", "size"}:
        reasons.append("flash.image_schema")
    else:
        image_path, ref_errors = _validate_ref({key: image[key] for key in REF_FIELDS}, session)
        if ref_errors or type(image.get("size")) is not int or image["size"] <= 0:
            reasons.append("flash.image")
        app_size = _hex(app.get("size")) if app else None
        if app_size is None or type(image.get("size")) is not int or image["size"] > app_size:
            reasons.append("flash.image_range")
        if image_path is not None and image_path.stat().st_size != image.get("size"):
            reasons.append("flash.file_size")
    if not isinstance(operations, list) or len(operations) != 3:
        reasons.append("flash.operations")
        return
    expected_ops = ("read_flash", "write_flash", "read_flash")
    operation_paths: list[Path | None] = []
    app_offset = _hex(app.get("offset")) if app else None
    app_size = _hex(app.get("size")) if app else None
    for index, operation in enumerate(operations):
        if not _record(operation) or set(operation) != {"operation", "offset", "size", "path", "algorithm", "sha256"}:
            reasons.append("flash.operations")
            operation_paths.append(None)
            continue
        path, ref_errors = _validate_ref({key: operation[key] for key in REF_FIELDS}, session)
        operation_paths.append(path)
        if ref_errors or operation.get("operation") != expected_ops[index] or operation.get("algorithm") != "sha256":
            reasons.append("flash.operations")
        offset = _hex(operation.get("offset"))
        size = operation.get("size")
        if index == 1:
            if (
                offset != app_offset
                or type(size) is not int
                or size != image_doc.get("size")
                or app_size is None
                or offset is None
                or offset + size > app_offset + app_size
            ):
                reasons.append("flash.write_range")
            if operation.get("path") != image_doc.get("path") or operation.get("sha256") != image_doc.get("sha256"):
                reasons.append("flash.operations")
        else:
            if offset != app_offset or type(size) is not int or size != app_size:
                reasons.append("flash.readback_range")
        if path is not None and path.stat().st_size != size:
            reasons.append("flash.file_size")
    if (
        image_path is not None
        and len(operation_paths) == 3
        and operation_paths[2] is not None
        and hashlib.sha256(operation_paths[2].read_bytes()[: image_path.stat().st_size]).hexdigest()
        != image_doc.get("sha256")
    ):
        reasons.append("flash.post_readback")


def _validate_nvs(value: object, session: Path | None, reasons: list[str]) -> None:
    if not _record(value) or set(value) != {"before", "after"}:
        reasons.append("nvs.schema")
        return
    before, before_errors = _validate_ref(value.get("before"), session)
    after, after_errors = _validate_ref(value.get("after"), session)
    if before_errors or after_errors:
        reasons.append("nvs.file")
    if (
        _record(value.get("before"))
        and _record(value.get("after"))
        and (value["before"].get("algorithm") != "sha256" or value["after"].get("algorithm") != "sha256")
    ):
        reasons.append("nvs.algorithm")
    if (
        before is not None
        and after is not None
        and hashlib.sha256(before.read_bytes()).hexdigest() != hashlib.sha256(after.read_bytes()).hexdigest()
    ):
        reasons.append("nvs.equality")


def validate_input(document: object, *, repository_root: Path) -> list[str]:
    del repository_root
    if not _record(document):
        return ["input.not_object"]
    reasons: list[str] = []
    if set(document) != TOP_FIELDS:
        reasons.append("input.schema")
    if (
        document.get("schemaVersion") != 3
        or not isinstance(document.get("sessionStartedAt"), str)
        or UTC.fullmatch(document["sessionStartedAt"]) is None
    ):
        reasons.append("input.identity")
    session: Path | None = None
    session_value = document.get("sessionDirectory")
    if isinstance(session_value, str):
        candidate = Path(session_value)
        try:
            resolved = candidate.resolve(strict=True)
            root = ARTIFACT_ROOT.resolve(strict=True)
            if (
                candidate.is_absolute()
                and resolved.is_dir()
                and not candidate.is_symlink()
                and resolved != root
                and _within(resolved, root)
            ):
                session = resolved
        except OSError:
            pass
    if session is None:
        reasons.append("path.session")
    if not isinstance(document.get("outputDirectory"), str) or document.get("outputDirectory") != session_value:
        reasons.append("path.output")
    _validate_local_stack(document.get("localStack"), reasons)
    _validate_repositories(document.get("repositories"), document.get("firmwareRequiredAncestor"), reasons)
    repos = document.get("repositories") if _record(document.get("repositories")) else {}
    backend = repos.get("backend") if _record(repos.get("backend")) else {}
    backend_sha = backend.get("sha")
    if (
        document.get("backendImage") != f"{IMAGE_PREFIX}{backend_sha}"
        or document.get("composeProject") != COMPOSE_PROJECT
    ):
        reasons.append("compose.identity")
    materialization_doc = _parse_ref_json(
        document.get("materializationReceipt"), session, "materialization.file", reasons
    )
    materialization = _validate_materialization(materialization_doc, reasons)
    _validate_reviewed(document.get("reviewedIdentity"), materialization, backend_sha, reasons)
    assignment_doc = _parse_ref_json(document.get("assignmentSnapshot"), session, "assignment.file", reasons)
    _validate_assignment(assignment_doc, materialization, reasons)
    _validate_visual_pack(document.get("visualPack"), materialization, session, reasons)
    _validate_flash(document.get("flashPlan"), session, reasons)
    _validate_nvs(document.get("nvsPreservation"), session, reasons)
    return sorted(set(reasons))


def validate_image(document: object, expected_image: object, expected_sha: object) -> list[str]:
    if not isinstance(document, list) or len(document) != 1 or not _record(document[0]):
        return ["image.missing"]
    image = document[0]
    config = image.get("Config")
    labels = config.get("Labels") if _record(config) else None
    reasons = []
    if not isinstance(image.get("Id"), str) or re.fullmatch(r"sha256:[0-9a-f]{64}", image["Id"]) is None:
        reasons.append("image.id")
    if not isinstance(expected_image, str) or image.get("RepoTags") != [expected_image]:
        reasons.append("image.reference")
    if not _record(labels) or labels.get("com.tbot.course-mode.materializer-path") != MATERIALIZER_PATH:
        reasons.append("image.materializer")
    if not _record(labels) or labels.get("org.opencontainers.image.revision") != expected_sha:
        reasons.append("image.revision")
    if not _record(labels) or labels.get("com.tbot.course-mode.build-source") != "reviewed-clean-git-worktree":
        reasons.append("image.provenance")
    return sorted(reasons)


def validate_compose(compose: object, expected: object) -> list[str]:
    if (
        not _record(compose)
        or not _record(expected)
        or compose.get("name") != COMPOSE_PROJECT
        or not _record(compose.get("services"))
    ):
        return ["compose.schema"]
    services = compose["services"]
    reasons = []
    if set(services) != {"backend", "course-mode-materialize", "postgres", "redis", "mysql", "web"}:
        reasons.append("compose.services")
    backend = services.get("backend")
    materializer = services.get("course-mode-materialize")
    if not _record(backend):
        reasons.append("compose.backend")
        backend = {}
    if not _record(materializer):
        reasons.append("compose.materializer")
        materializer = {}
    if backend.get("image") != expected.get("backendImage") or materializer.get("image") != expected.get(
        "backendImage"
    ):
        reasons.append("compose.image")
    ports = backend.get("ports")
    if (
        not isinstance(ports, list)
        or len(ports) != 1
        or not _record(ports[0])
        or ports[0].get("host_ip") != "127.0.0.1"
        or ports[0].get("target") != 3000
        or ports[0].get("published") != "3000"
    ):
        reasons.append("compose.port")
    backend_env = backend.get("environment") if _record(backend.get("environment")) else {}
    materializer_env = materializer.get("environment") if _record(materializer.get("environment")) else {}
    stack = expected.get("localStack") if _record(expected.get("localStack")) else {}
    if backend_env.get("ROBOT_ESP_BASE_URL") != stack.get("espHttpUrl") or backend_env.get(
        "LESSON_ASSET_ORIGIN_BASE"
    ) != stack.get("assetOrigin"):
        reasons.append("compose.endpoints")
    if (
        backend_env.get("LESSON_ROLLOUT_DEVICE_ALLOWLIST") != ROBOT_MAC
        or materializer_env.get("COURSE_MODE_DEVICE_MAC") != ROBOT_MAC
    ):
        reasons.append("compose.robot")
    if (
        backend_env.get("COURSE_MODE_LOCAL_PHYSICAL_TFT_ENABLED") != "true"
        or materializer_env.get("COURSE_MODE_LOCAL_PHYSICAL_TFT_ENABLED") != "true"
        or materializer_env.get("COURSE_MODE_LOCAL_COMPOSE_ENABLED") != "true"
    ):
        reasons.append("compose.local_guard")
    if materializer_env.get("DATABASE_URL") != "postgresql://tbot:tbot@postgres:5432/tbot":
        reasons.append("compose.database")
    if materializer.get("command") != MATERIALIZER_COMMAND:
        reasons.append("compose.materializer")
    mounts = materializer.get("volumes")
    backend_repo = expected.get("repositories", {}).get("backend", {}) if _record(expected.get("repositories")) else {}
    source = (
        str(Path(backend_repo.get("path")) / "src/lessons/fixtures/course-mode")
        if isinstance(backend_repo.get("path"), str)
        else None
    )
    if (
        not isinstance(mounts, list)
        or len(mounts) != 1
        or not _record(mounts[0])
        or mounts[0].get("source") != source
        or mounts[0].get("target") != "/course-mode-fixtures"
        or mounts[0].get("read_only") is not True
    ):
        reasons.append("compose.mount")
    if any(term in json.dumps(compose, sort_keys=True).lower() for term in ("https://", "production", "prod.")):
        reasons.append("compose.production")
    return sorted(set(reasons))


def _run(command: list[str], cwd: Path, env: dict[str, str]) -> tuple[str, bool]:
    try:
        result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, check=False)
    except OSError:
        return "", False
    return result.stdout, result.returncode == 0


def _emit(payload: dict[str, object]) -> None:
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[3]
    try:
        raw = args.input.read_bytes()
    except OSError:
        _emit({"reasons": ["input.unreadable"], "valid": False})
        return 1
    expected, error = _load_json_bytes(raw)
    if error:
        _emit({"reasons": [f"input.{error}"], "valid": False})
        return 1
    reasons = validate_input(expected, repository_root=root)
    if reasons:
        _emit({"reasons": reasons, "valid": False})
        return 1
    session = Path(expected["sessionDirectory"]).resolve()
    if args.output.parent.resolve() != session or args.output.exists() or args.output.is_symlink():
        _emit({"reasons": ["output.path"], "valid": False})
        return 1
    outputs: dict[str, str] = {}
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    for name in ("backend", "esp", "firmware"):
        repo = expected["repositories"][name]
        for label, suffix in (
            ("root", ["rev-parse", "--show-toplevel"]),
            ("sha", ["rev-parse", "HEAD"]),
            ("status", ["status", "--porcelain", "--untracked-files=all"]),
        ):
            stdout, ok = _run(["git", "-C", repo["path"], *suffix], root, env)
            if not ok:
                reasons.append(f"command.git.{name}.{label}")
            else:
                outputs[f"{name}.{label}"] = stdout
    firmware = expected["repositories"]["firmware"]
    _, ok = _run(["git", "-C", firmware["path"], "merge-base", "--is-ancestor", TASK9_SHA, firmware["sha"]], root, env)
    if not ok:
        reasons.append("git.ancestor.firmware")
    for name in ("backend", "esp", "firmware"):
        repo = expected["repositories"][name]
        if outputs.get(f"{name}.sha", "").strip() != repo["sha"]:
            reasons.append(f"git.sha.{name}")
        if (
            f"{name}.root" in outputs
            and Path(outputs[f"{name}.root"].strip()).resolve() != Path(repo["path"]).resolve()
        ):
            reasons.append(f"git.root.{name}")
        dirty = sorted(line[3:] for line in outputs.get(f"{name}.status", "").splitlines() if len(line) >= 4)
        if dirty != sorted(item["path"] for item in repo["dirtyExceptions"]):
            reasons.append(f"git.dirty.{name}")
        for item in repo["dirtyExceptions"]:
            path = Path(repo["path"]) / item["path"]
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
                reasons.append(f"git.dirty_hash.{name}")
    image_out, image_ok = _run(["docker", "image", "inspect", expected["backendImage"]], root, env)
    compose_out, compose_ok = _run(
        [
            "docker",
            "compose",
            "--project-name",
            COMPOSE_PROJECT,
            "-f",
            str(root / "docs/docker/docker-compose.lesson-studio-e2e.yml"),
            "-f",
            str(root / "docs/docker/docker-compose.course-mode-physical-tft.yml"),
            "config",
            "--format",
            "json",
        ],
        root,
        env,
    )
    if not image_ok:
        reasons.append("command.image")
    if not compose_ok:
        reasons.append("command.compose")
    if image_ok:
        image_doc, parse_error = _load_json_bytes(image_out.encode())
        if parse_error:
            reasons.append("command.image_json")
        else:
            reasons.extend(
                validate_image(image_doc, expected["backendImage"], expected["repositories"]["backend"]["sha"])
            )
    if compose_ok:
        compose_doc, parse_error = _load_json_bytes(compose_out.encode())
        if parse_error:
            reasons.append("command.compose_json")
        else:
            reasons.extend(validate_compose(compose_doc, expected))
    if reasons:
        _emit({"reasons": sorted(set(reasons)), "valid": False})
        return 1
    reviewed = expected["reviewedIdentity"]
    payload = {
        "valid": True,
        "result": "PASS",
        "inputChecksum": hashlib.sha256(
            json.dumps(expected, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "courseId": COURSE_ID,
        "courseKey": COURSE_KEY,
        "lessonKey": LESSON_KEY,
        "sourceLessonId": reviewed["sourceLessonId"],
        "replacementId": reviewed["replacementId"],
        "replacementVersion": reviewed["replacementVersion"],
        "contractChecksum": reviewed["contractChecksum"],
        "manifestChecksum": reviewed["manifestChecksum"],
        "visualPackChecksum": reviewed["visualPackChecksum"],
        "rendererId": RENDERER,
        "visualLayoutContract": LAYOUT,
        "robotMac": ROBOT_MAC,
        "backendSha": expected["repositories"]["backend"]["sha"],
        "espSha": expected["repositories"]["esp"]["sha"],
        "firmwareSha": expected["repositories"]["firmware"]["sha"],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
    if FORBIDDEN_OUTPUT.search(encoded):
        _emit({"reasons": ["output.redaction"], "valid": False})
        return 1
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=session, delete=False) as handle:
        handle.write(encoded)
        temporary = Path(handle.name)
    temporary.replace(args.output)
    _emit(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
