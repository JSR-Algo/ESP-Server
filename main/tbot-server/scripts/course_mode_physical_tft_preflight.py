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
CANONICAL_CONTRACT_CHECKSUM = "52303f656b6b21e4a65fc1a7179f7668888a7682c2e86b6d1319f201a548c840"
FIRMWARE_TASK9_SHA = "3b13883b6e8a8f6495c5670ebdc194392e38de75"
REVIEWED_BACKEND_SHA = "0efd1bed84a3ef160bab2aa5fb4357bf521fde7e"
REVIEWED_ESP_SHA = "ac76c22f9d535ea012e10ae27db2f79a84f74b1b"
ROBOT_MAC = "14:c1:9f:d1:ac:20"
COMPOSE_PROJECT = "tbot-course-mode-physical-tft"
IMAGE_PREFIX = "local/tbot-backend:course-mode-physical-tft-"
MATERIALIZER_PATH = "/app/dist/lessons/course-mode/course-mode-local-materializer.js"
MATERIALIZER_COMMAND = ["dist/lessons/course-mode/course-mode-local-materializer.js", "materialize"]
ESP_DIRTY_ALLOWLIST = {"main/tbot-server/tests/test_lesson_voice_output_discipline.py"}
REQUIRED_FIELDS = {
    "schemaVersion",
    "sessionDirectory",
    "sessionStartedAt",
    "outputDirectory",
    "localStack",
    "repositories",
    "firmwareRequiredAncestor",
    "backendImage",
    "composeProject",
    "course",
    "lesson",
    "assignments",
    "robots",
    "visualPack",
    "materializerReceipt",
    "flashPlan",
    "nvsPreservation",
    "evidencePaths",
}
SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")
FORBIDDEN_OUTPUT = re.compile(r"(?i)(bearer|password|private.?key|secret.?value|transcript|raw.?speech|audio.?data)")


def _record(value: object) -> bool:
    return isinstance(value, dict)


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _normal_mac(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    compact = re.sub(r"[^0-9a-fA-F]", "", value).lower()
    if len(compact) != 12 or not re.fullmatch(r"[0-9a-f]{12}", compact):
        return None
    return ":".join(compact[index : index + 2] for index in range(0, 12, 2))


def _local_url(value: object, schemes: set[str], *, trailing_slash: bool = False) -> str | None:
    if not isinstance(value, str) or (trailing_slash and not value.endswith("/")):
        return None
    parsed = urlparse(value)
    if parsed.scheme not in schemes or parsed.username or parsed.password or not parsed.hostname:
        return None
    if any(term in value.lower() for term in ("prod", "production", "public", "cloud")):
        return None
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        return None
    if address.version != 4 or not address.is_private or address.is_loopback or address.is_link_local:
        return None
    return parsed.hostname


def validate_local_lab_endpoints(asset_origin: object, ota_url: object, websocket_url: object) -> list[str]:
    """Retain the shared Task07 endpoint validator API for ledger verification."""
    values = (
        ("assetOrigin", asset_origin, {"http"}, True),
        ("otaUrl", ota_url, {"http"}, False),
        ("websocketUrl", websocket_url, {"ws"}, False),
    )
    reasons: list[str] = []
    hosts: set[str] = set()
    for field, value, schemes, trailing_slash in values:
        host = _local_url(value, schemes, trailing_slash=trailing_slash)
        if host is None:
            reasons.append(field)
        else:
            hosts.add(host)
    if len(hosts) > 1:
        reasons.append("localLabRoute")
    return sorted(set(reasons))


def _safe_file(value: object, session: Path) -> tuple[Path | None, str | None]:
    if not isinstance(value, str):
        return None, "path.type"
    path = Path(value)
    if not path.is_absolute():
        return None, "path.absolute"
    try:
        resolved = path.resolve(strict=True)
        root = session.resolve(strict=True)
    except OSError:
        return None, "path.missing"
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents if parent != path.anchor):
        return None, "path.symlink"
    if not path.is_file() or not _within(resolved, root):
        return None, "path.boundary"
    return path, None


def _hash_matches(path: Path, expected: object) -> bool:
    return _real_sha256(expected) and hashlib.sha256(path.read_bytes()).hexdigest() == expected


def _real_sha256(value: object) -> bool:
    return isinstance(value, str) and SHA256.fullmatch(value) is not None and len(set(value)) > 1


def _validate_repositories(document: dict[str, object], reasons: list[str]) -> None:
    repositories = document.get("repositories")
    if not _record(repositories) or set(repositories) != {"backend", "esp", "firmware"}:
        reasons.append("repository.schema")
        return
    for name in ("backend", "esp", "firmware"):
        value = repositories.get(name)
        prefix = f"repository.{name}"
        if not _record(value) or set(value) != {"path", "sha", "dirtyAllowlist"}:
            reasons.append(f"{prefix}.schema")
            continue
        path = Path(value.get("path", "")) if isinstance(value.get("path"), str) else Path()
        if not path.is_absolute() or not path.is_dir() or path.is_symlink():
            reasons.append(f"{prefix}.path")
        if not isinstance(value.get("sha"), str) or not SHA40.fullmatch(value["sha"]):
            reasons.append(f"{prefix}.sha")
        allowlist = value.get("dirtyAllowlist")
        expected = sorted(ESP_DIRTY_ALLOWLIST) if name == "esp" else []
        if allowlist != expected:
            reasons.append(f"{prefix}.dirty_allowlist")
    exact_shas = {"backend": REVIEWED_BACKEND_SHA, "esp": REVIEWED_ESP_SHA, "firmware": FIRMWARE_TASK9_SHA}
    for name, expected_sha in exact_shas.items():
        value = repositories.get(name)
        if isinstance(value, dict) and value.get("sha") != expected_sha:
            reasons.append(f"repository.{name}.sha")
    if document.get("firmwareRequiredAncestor") != FIRMWARE_TASK9_SHA:
        reasons.append("repository.firmware.ancestor")


def _validate_lesson(document: dict[str, object], reasons: list[str]) -> None:
    course = document.get("course")
    if not _record(course) or course != {"courseId": COURSE_ID, "courseKey": COURSE_KEY}:
        reasons.append("course.identity")
    lesson = document.get("lesson")
    if not _record(lesson):
        reasons.append("lesson.schema")
        return
    if lesson.get("lessonKey") != LESSON_KEY:
        reasons.append("lesson.identity")
    replacement_id = lesson.get("replacementId")
    if (
        not isinstance(replacement_id, str)
        or not UUID.fullmatch(replacement_id)
        or replacement_id == lesson.get("sourceLessonId")
        or type(lesson.get("replacementVersion")) is not int
        or lesson["replacementVersion"] <= 0
    ):
        reasons.append("lesson.replacement_identity")
    if lesson.get("mappingState") != "ACTIVE":
        reasons.append("lesson.mapping")
    if lesson.get("sourceState") not in {"ARCHIVED", "SUPERSEDED"} or lesson.get("sourceAssignable") is not False:
        reasons.append("lesson.source")
    if lesson.get("activeReplacementCount") != 1:
        reasons.append("lesson.ambiguity")
    if (
        lesson.get("contractVersion"),
        lesson.get("rendererId"),
        lesson.get("visualLayoutContract"),
        lesson.get("contractChecksum"),
    ) != (CONTRACT_VERSION, RENDERER, LAYOUT, CANONICAL_CONTRACT_CHECKSUM):
        reasons.append("lesson.contract")
    for field in ("manifestChecksum", "stepsChecksum"):
        if not _real_sha256(lesson.get(field)):
            reasons.append(f"lesson.{field}")
    backend = document.get("repositories", {}).get("backend", {}) if _record(document.get("repositories")) else {}
    if lesson.get("backendSha") != backend.get("sha"):
        reasons.append("lesson.backend_binding")


def _validate_assignment(document: dict[str, object], reasons: list[str]) -> None:
    lesson = document.get("lesson", {})
    assignments = document.get("assignments")
    robots = document.get("robots")
    if not isinstance(assignments, list) or len(assignments) != 1 or not _record(assignments[0]):
        reasons.append("assignment.cardinality")
    else:
        item = assignments[0]
        if (
            item.get("state") != "ACTIVE"
            or item.get("lessonId") != lesson.get("replacementId")
            or item.get("lessonVersion") != lesson.get("replacementVersion")
            or not isinstance(item.get("assignmentId"), str)
            or not UUID.fullmatch(item["assignmentId"])
            or _normal_mac(item.get("robotMac")) != ROBOT_MAC
        ):
            reasons.append("assignment.identity")
    if not isinstance(robots, list) or len(robots) != 1 or not _record(robots[0]):
        reasons.append("robot.cardinality")
    elif robots[0].get("state") != "ACTIVE" or _normal_mac(robots[0].get("mac")) != ROBOT_MAC:
        reasons.append("robot.identity")


def _validate_visual_pack(document: dict[str, object], reasons: list[str]) -> None:
    pack = document.get("visualPack")
    lesson = document.get("lesson", {})
    if not _record(pack):
        reasons.append("visual_pack.schema")
        return
    if pack.get("state") != "READY" or pack.get("publicationState") != "published" or pack.get("immutable") is not True:
        reasons.append("visual_pack.state")
    identity_fields = (
        "lessonId",
        "lessonVersion",
        "contractChecksum",
        "manifestChecksum",
        "rendererId",
        "visualLayoutContract",
    )
    expected_fields = (
        "replacementId",
        "replacementVersion",
        "contractChecksum",
        "manifestChecksum",
        "rendererId",
        "visualLayoutContract",
    )
    if any(
        pack.get(field) != lesson.get(expected)
        for field, expected in zip(identity_fields, expected_fields, strict=False)
    ):
        reasons.append("visual_pack.identity")
    phases = pack.get("phases")
    if not isinstance(phases, list) or not phases:
        reasons.append("visual_pack.phase_authority")
        return
    phase_checksum = hashlib.sha256(
        json.dumps(phases, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if pack.get("phaseAuthorityChecksum") != phase_checksum:
        reasons.append("visual_pack.phase_checksum")
    phase_ids: set[str] = set()
    activity_ids: set[str] = set()
    for phase in phases:
        if not _record(phase) or set(phase) != {
            "phaseId",
            "activityIds",
            "templateId",
            "templateVersion",
            "playbackMode",
            "layers",
        }:
            reasons.append("visual_pack.phase_authority")
            continue
        activities = phase.get("activityIds")
        if (
            not isinstance(phase.get("phaseId"), str)
            or phase["phaseId"] in phase_ids
            or not isinstance(activities, list)
            or not activities
            or any(not isinstance(item, str) or not item or item in activity_ids for item in activities)
        ):
            reasons.append("visual_pack.phase_authority")
        else:
            phase_ids.add(phase["phaseId"])
            activity_ids.update(activities)
        if (
            phase.get("templateId") != "layeredCinematic"
            or phase.get("templateVersion") != 1
            or phase.get("playbackMode") not in {"once", "hold"}
        ):
            reasons.append("visual_pack.phase_authority")
        layers = phase.get("layers")
        if not isinstance(layers, list):
            reasons.append("visual_pack.layer")
            continue
        backgrounds = [layer for layer in layers if _record(layer) and layer.get("slot") == "backgroundScene"]
        videos = [layer for layer in layers if _record(layer) and layer.get("mediaType") == "video/mp4"]
        robot_videos = [layer for layer in videos if layer.get("slot") == "robotOverlay"]
        objects = [layer for layer in layers if _record(layer) and layer.get("slot") == "teachingObject"]
        if len(backgrounds) != 1:
            reasons.append("visual_pack.background")
        if len(objects) > 1:
            reasons.append("visual_pack.object_count")
        if len(videos) != 1 or len(robot_videos) != 1:
            reasons.append("visual_pack.robot_video_count")
        for layer in layers:
            if not _record(layer):
                reasons.append("visual_pack.layer")
                continue
            path = Path(layer.get("storagePath", "")) if isinstance(layer.get("storagePath"), str) else Path()
            if (
                layer.get("slot") not in {"backgroundScene", "teachingObject", "robotOverlay"}
                or not isinstance(layer.get("assetKey"), str)
                or not layer["assetKey"]
                or not isinstance(layer.get("assetVersionId"), str)
                or not UUID.fullmatch(layer["assetVersionId"])
                or type(layer.get("version")) is not int
                or layer["version"] <= 0
                or layer.get("state") != "READY"
                or layer.get("publicationState") != "published"
                or layer.get("immutable") is not True
                or path.is_absolute()
                or ".." in path.parts
                or not str(path)
                or not _real_sha256(layer.get("sha256"))
                or type(layer.get("bytes")) is not int
                or layer["bytes"] <= 0
                or type(layer.get("width")) is not int
                or layer["width"] <= 0
                or type(layer.get("height")) is not int
                or layer["height"] <= 0
                or layer.get("mediaType") not in {"image/jpeg", "image/png", "video/mp4"}
                or not _record(layer.get("compatibilityMetadata"))
            ):
                reasons.append("visual_pack.layer")
            metadata = layer.get("compatibilityMetadata", {})
            if layer.get("mediaType") == "video/mp4" and (
                metadata.get("mediaKind") != "video"
                or metadata.get("codec") != "mjpeg"
                or metadata.get("hasAudio") is not False
                or type(metadata.get("fps")) is not int
                or metadata["fps"] <= 0
                or type(metadata.get("frameCount")) is not int
                or metadata["frameCount"] <= 0
                or not _record(metadata.get("rect"))
                or not _record(metadata.get("chromaKey"))
            ):
                reasons.append("visual_pack.compatibility")
            if layer.get("mediaType", "").startswith("image/") and (
                metadata.get("mediaKind") != "image"
                or metadata.get("fit") not in {"cover", "contain"}
                or not _record(metadata.get("rect"))
            ):
                reasons.append("visual_pack.compatibility")


def _validate_receipt(document: dict[str, object], session: Path, reasons: list[str]) -> None:
    receipt = document.get("materializerReceipt")
    lesson = document.get("lesson", {})
    course = document.get("course", {})
    assignments = document.get("assignments", [])
    if not _record(receipt):
        reasons.append("receipt.schema")
        return
    path, error = _safe_file(receipt.get("path"), session)
    if error or path is None or not _hash_matches(path, receipt.get("sha256")):
        reasons.append("receipt.file")
    expected = {
        "result": "pass",
        "courseId": course.get("courseId"),
        "courseKey": course.get("courseKey"),
        "lessonKey": lesson.get("lessonKey"),
        "lessonId": lesson.get("replacementId"),
        "lessonVersion": lesson.get("replacementVersion"),
        "assignmentId": assignments[0].get("assignmentId")
        if len(assignments) == 1 and _record(assignments[0])
        else None,
        "robotMac": ROBOT_MAC,
        "contractChecksum": lesson.get("contractChecksum"),
        "manifestChecksum": lesson.get("manifestChecksum"),
        "stepsChecksum": lesson.get("stepsChecksum"),
        "rendererId": RENDERER,
        "visualLayoutContract": LAYOUT,
        "phaseAuthorityChecksum": document.get("visualPack", {}).get("phaseAuthorityChecksum"),
    }
    if any(
        (_normal_mac(receipt.get(field)) if field == "robotMac" else receipt.get(field)) != value
        for field, value in expected.items()
    ):
        reasons.append("receipt.identity")


def _validate_flash(document: dict[str, object], session: Path, reasons: list[str]) -> None:
    plan = document.get("flashPlan")
    if not _record(plan):
        reasons.append("flash.schema")
        return
    if plan.get("appOffset") != "0x20000":
        reasons.append("flash.offset")
    image, error = _safe_file(plan.get("imagePath"), session)
    if (
        error
        or image is None
        or type(plan.get("appSize")) is not int
        or plan["appSize"] <= 0
        or image.stat().st_size != plan["appSize"]
        or not _hash_matches(image, plan.get("imageSha256"))
    ):
        reasons.append("flash.image")
    operations = plan.get("operations")
    expected_kinds = ["read_flash", "write_flash", "read_flash"]
    if not isinstance(operations, list) or len(operations) != 3:
        reasons.append("flash.operations")
        return
    for index, operation in enumerate(operations):
        if (
            not _record(operation)
            or operation.get("operation") != expected_kinds[index]
            or operation.get("offset") != "0x20000"
            or operation.get("size") != plan.get("appSize")
        ):
            reasons.append("flash.operations")
            continue
        path, path_error = _safe_file(operation.get("path"), session)
        if path_error or path is None or not _hash_matches(path, operation.get("sha256")):
            reasons.append("flash.operations")
    if _record(operations[1]) and (
        operations[1].get("path") != plan.get("imagePath") or operations[1].get("sha256") != plan.get("imageSha256")
    ):
        reasons.append("flash.operations")
    if _record(operations[2]) and operations[2].get("sha256") != plan.get("imageSha256"):
        reasons.append("flash.readback")


def _validate_paths_and_nvs(document: dict[str, object], session: Path, reasons: list[str]) -> None:
    paths = document.get("evidencePaths")
    if not _record(paths) or set(paths) != {
        "appImage",
        "appBefore",
        "appAfter",
        "nvsBefore",
        "nvsAfter",
        "materializerReceipt",
    }:
        reasons.append("path.schema")
    else:
        for value in paths.values():
            _, error = _safe_file(value, session)
            if error:
                reasons.append(error if error == "path.symlink" else "path.boundary")
        flash = document.get("flashPlan", {})
        operations = flash.get("operations", []) if _record(flash) else []
        nvs_paths = document.get("nvsPreservation", {})
        receipt = document.get("materializerReceipt", {})
        if len(operations) == 3 and _record(nvs_paths) and _record(receipt):
            expected_paths = {
                "appImage": flash.get("imagePath"),
                "appBefore": operations[0].get("path"),
                "appAfter": operations[2].get("path"),
                "nvsBefore": nvs_paths.get("beforePath"),
                "nvsAfter": nvs_paths.get("afterPath"),
                "materializerReceipt": receipt.get("path"),
            }
            if paths != expected_paths:
                reasons.append("path.binding")
    nvs = document.get("nvsPreservation")
    if not _record(nvs):
        reasons.append("nvs.schema")
        return
    if nvs.get("algorithm") != "sha256":
        reasons.append("nvs.algorithm")
    hashes = (nvs.get("beforeSha256"), nvs.get("afterSha256"))
    if any(not _real_sha256(value) for value in hashes):
        reasons.append("nvs.hash")
    elif hashes[0] != hashes[1]:
        reasons.append("nvs.equality")
    for prefix in ("before", "after"):
        path, error = _safe_file(nvs.get(f"{prefix}Path"), session)
        if error or path is None or not _hash_matches(path, nvs.get(f"{prefix}Sha256")):
            reasons.append("nvs.file")


def _validate_local_stack(document: dict[str, object], reasons: list[str]) -> None:
    stack = document.get("localStack")
    if not _record(stack):
        reasons.append("local_stack.schema")
        return
    if (
        stack.get("environment") != "local-attended-lab"
        or stack.get("productionMutationAllowed") is not False
        or stack.get("adminMutationAllowed") is not False
        or stack.get("databaseAuthority") != "isolated-local-compose"
        or stack.get("backendBaseUrl") != "http://127.0.0.1:3000"
        or stack.get("espHttpUrl") != "http://host.docker.internal:8003"
    ):
        reasons.append("local_stack.production_guard")
    hosts = {
        _local_url(stack.get("assetOrigin"), {"http"}, trailing_slash=True),
        _local_url(stack.get("otaUrl"), {"http"}),
        _local_url(stack.get("websocketUrl"), {"ws"}),
    }
    if None in hosts or len(hosts) != 1:
        reasons.append("local_stack.endpoints")


def validate_input(document: object, *, repository_root: Path) -> list[str]:
    del repository_root
    if not isinstance(document, dict):
        return ["input.not_object"]
    reasons: list[str] = []
    if set(document) != REQUIRED_FIELDS:
        reasons.append("input.schema")
    if (
        document.get("schemaVersion") != 2
        or not isinstance(document.get("sessionStartedAt"), str)
        or not UTC.fullmatch(document["sessionStartedAt"])
    ):
        reasons.append("input.identity")
    session_value = document.get("sessionDirectory")
    output_value = document.get("outputDirectory")
    try:
        session = Path(session_value).resolve(strict=True) if isinstance(session_value, str) else Path()
        artifact_root = ARTIFACT_ROOT.resolve(strict=True)
    except OSError:
        reasons.append("path.session")
        return sorted(set(reasons))
    if (
        not Path(session_value).is_absolute()
        or not session.is_dir()
        or Path(session_value).is_symlink()
        or session == artifact_root
        or not _within(session, artifact_root)
    ):
        reasons.append("path.session")
    if output_value != session_value:
        reasons.append("path.output")
    _validate_local_stack(document, reasons)
    _validate_repositories(document, reasons)
    backend_sha = (
        document.get("repositories", {}).get("backend", {}).get("sha")
        if _record(document.get("repositories"))
        else None
    )
    if (
        document.get("backendImage") != f"{IMAGE_PREFIX}{backend_sha}"
        or document.get("composeProject") != COMPOSE_PROJECT
    ):
        reasons.append("compose.identity")
    _validate_lesson(document, reasons)
    _validate_assignment(document, reasons)
    _validate_visual_pack(document, reasons)
    _validate_receipt(document, session, reasons)
    _validate_flash(document, session, reasons)
    _validate_paths_and_nvs(document, session, reasons)
    return sorted(set(reasons))


def validate_image(document: object, expected_image: str, expected_sha: str) -> list[str]:
    if not isinstance(document, list) or len(document) != 1 or not isinstance(document[0], dict):
        return ["image.missing"]
    image = document[0]
    labels = image.get("Config", {}).get("Labels", {})
    reasons = []
    if not isinstance(image.get("Id"), str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", image["Id"]):
        reasons.append("image.id")
    if image.get("RepoTags") != [expected_image]:
        reasons.append("image.reference")
    if not isinstance(labels, dict) or labels.get("com.tbot.course-mode.materializer-path") != MATERIALIZER_PATH:
        reasons.append("image.materializer")
    if not isinstance(labels, dict) or labels.get("org.opencontainers.image.revision") != expected_sha:
        reasons.append("image.revision")
    if not isinstance(labels, dict) or labels.get("com.tbot.course-mode.build-source") != "reviewed-clean-git-worktree":
        reasons.append("image.provenance")
    return sorted(reasons)


def validate_compose(compose: object, expected: dict[str, object]) -> list[str]:
    if (
        not isinstance(compose, dict)
        or compose.get("name") != COMPOSE_PROJECT
        or not isinstance(compose.get("services"), dict)
    ):
        return ["compose.schema"]
    services = compose["services"]
    reasons = []
    if set(services) != {"backend", "course-mode-materialize", "postgres", "redis", "mysql", "web"}:
        reasons.append("compose.services")
    backend = services.get("backend", {})
    materializer = services.get("course-mode-materialize", {})
    if backend.get("image") != expected["backendImage"] or materializer.get("image") != expected["backendImage"]:
        reasons.append("compose.image")
    ports = backend.get("ports")
    if (
        not isinstance(ports, list)
        or len(ports) != 1
        or ports[0].get("host_ip") != "127.0.0.1"
        or ports[0].get("target") != 3000
        or ports[0].get("published") != "3000"
    ):
        reasons.append("compose.port")
    backend_env = backend.get("environment", {})
    materializer_env = materializer.get("environment", {})
    stack = expected["localStack"]
    if (
        backend_env.get("ROBOT_ESP_BASE_URL") != stack["espHttpUrl"]
        or backend_env.get("LESSON_ASSET_ORIGIN_BASE") != stack["assetOrigin"]
    ):
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
    source = str(Path(expected["repositories"]["backend"]["path"]) / "src/lessons/fixtures/course-mode")
    if (
        not isinstance(mounts, list)
        or len(mounts) != 1
        or mounts[0].get("source") != source
        or mounts[0].get("target") != "/course-mode-fixtures"
        or mounts[0].get("read_only") is not True
    ):
        reasons.append("compose.mount")
    if any(term in json.dumps(compose, sort_keys=True).lower() for term in ("https://", "production", "prod.")):
        reasons.append("compose.production")
    return sorted(set(reasons))


def _run(command: list[str], *, cwd: Path, env: dict[str, str]) -> tuple[str, str | None]:
    try:
        result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, check=False)
    except OSError:
        return "", "command.unavailable"
    return (result.stdout, None) if result.returncode == 0 else ("", "command.failed")


def _emit(payload: dict[str, object]) -> None:
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[3]
    try:
        expected = json.loads(args.input.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        _emit({"reasons": ["input.invalid_json"], "valid": False})
        return 1
    except (OSError, UnicodeError):
        _emit({"reasons": ["input.unreadable"], "valid": False})
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
            stdout, error = _run(["git", "-C", repo["path"], *suffix], cwd=root, env=env)
            if error:
                reasons.append(f"{error}.git.{name}.{label}")
            else:
                outputs[f"{name}.{label}"] = stdout
    firmware = expected["repositories"]["firmware"]
    stdout, error = _run(
        ["git", "-C", firmware["path"], "merge-base", "--is-ancestor", FIRMWARE_TASK9_SHA, firmware["sha"]],
        cwd=root,
        env=env,
    )
    if error:
        reasons.append("git.ancestor.firmware")
    else:
        outputs["firmware.ancestor"] = stdout
    for name in ("backend", "esp", "firmware"):
        repo = expected["repositories"][name]
        if (
            f"{name}.root" in outputs
            and Path(outputs[f"{name}.root"].strip()).resolve() != Path(repo["path"]).resolve()
        ):
            reasons.append(f"git.root.{name}")
        if outputs.get(f"{name}.sha", "").strip() != repo["sha"]:
            reasons.append(f"git.sha.{name}")
        actual_dirty = sorted(line[3:] for line in outputs.get(f"{name}.status", "").splitlines() if len(line) >= 4)
        if actual_dirty != repo["dirtyAllowlist"]:
            reasons.append(f"git.dirty.{name}")

    image_out, image_error = _run(["docker", "image", "inspect", expected["backendImage"]], cwd=root, env=env)
    compose_out, compose_error = _run(
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
        cwd=root,
        env=env,
    )
    if image_error:
        reasons.append(f"{image_error}.image")
    if compose_error:
        reasons.append(f"{compose_error}.compose")
    image_doc: object = None
    compose_doc: object = None
    if not image_error and not compose_error:
        try:
            image_doc = json.loads(image_out)
            compose_doc = json.loads(compose_out)
        except json.JSONDecodeError:
            reasons.append("command.invalid_json")
    if image_doc is not None:
        reasons.extend(validate_image(image_doc, expected["backendImage"], expected["repositories"]["backend"]["sha"]))
    if compose_doc is not None:
        reasons.extend(validate_compose(compose_doc, expected))
    if reasons:
        _emit({"reasons": sorted(set(reasons)), "valid": False})
        return 1

    checked = sorted(
        {
            "app-only-flash-and-readback",
            "canonical-course-and-w1-replacement",
            "canonical-contract-manifest-and-backend-binding",
            "clean-reviewed-repositories-and-firmware-ancestry",
            "exact-single-active-assignment-and-robot",
            "immutable-layered-visual-pack-one-video",
            "local-stack-no-production-mutation",
            "materializer-receipt-parity",
            "nvs-before-after-equality",
            "session-evidence-path-boundary",
        }
    )
    canonical_input = json.dumps(expected, sort_keys=True, separators=(",", ":")).encode("utf-8")
    payload = {
        "backendSha": expected["repositories"]["backend"]["sha"],
        "checkedInvariants": checked,
        "contractChecksum": expected["lesson"]["contractChecksum"],
        "courseId": COURSE_ID,
        "courseKey": COURSE_KEY,
        "espSha": expected["repositories"]["esp"]["sha"],
        "firmwareSha": expected["repositories"]["firmware"]["sha"],
        "inputChecksum": hashlib.sha256(canonical_input).hexdigest(),
        "lessonKey": LESSON_KEY,
        "manifestChecksum": expected["lesson"]["manifestChecksum"],
        "rendererId": RENDERER,
        "replacementId": expected["lesson"]["replacementId"],
        "replacementVersion": expected["lesson"]["replacementVersion"],
        "result": "PASS",
        "robotMac": ROBOT_MAC,
        "valid": True,
        "visualLayoutContract": LAYOUT,
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
