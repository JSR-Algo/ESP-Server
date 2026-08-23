#!/usr/bin/env python3
"""Run a bounded, no-device Course Mode physical-TFT configuration preflight."""

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

COMPOSE_PROJECT = "tbot-course-mode-physical-tft"
IMAGE_PREFIX = "local/tbot-backend:course-mode-physical-tft-"
MATERIALIZER_LABEL = "com.tbot.course-mode.materializer-path"
MATERIALIZER_PATH = "/app/dist/lessons/course-mode/course-mode-local-materializer.js"
MATERIALIZER_COMMAND = [
    "dist/lessons/course-mode/course-mode-local-materializer.js",
    "materialize",
]
ARTIFACT_ROOT = Path("/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-task07")
EXPECTED_IDS = {
    "courseId": "70000000-0000-4000-8000-000000000003",
    "lessonId": "70000000-0000-4000-8000-000000000004",
    "deviceId": "70000000-0000-4000-8000-000000000005",
    "assignmentId": "70000000-0000-4000-8000-000000000006",
    "adultOperatorId": "70000000-0000-4000-8000-000000000007",
}
EXPECTED_PRODUCTION_CANDIDATE_TARGET = {
    "firmwareSha": "3d4a1e2a32359278124c61e56fd459fac618506e",
    "applicationSha256": "84c999ece0c90eb6e69a410e335c7791f330e9c0fd39c30dfd4162bb7c4cfc6e",
    "bundleRootSha256": "9ef3729d0faec7b02d867cedb3ab30d110b845b1c0133738c588bba0e0c16be6",
}
EXPECTED_HISTORICAL_INSTALLATION_PROVENANCE = {
    "preservedNvsSha256": "a7a87f72416be20388298cb70cfff306ec78e77f0e8b09231d16113f3d82404e",
}
ACTIVE_LAB_FIRMWARE_SHA = "3df15a712a9e7ed656a1a9f240bd2ac2bf8ba989"
EXPECTED_PROTECTED = {
    "path": "/Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/tbot-server/tests/test_lesson_voice_output_discipline.py",
    "sha256": "08f77b5452301224b17b4b333d2d032fff40c06aa2eaea97fa90932dae7d97e3",
}
REQUIRED_INPUT_FIELDS = {
    "schemaVersion": int,
    "backendWorktree": str,
    "backendSha": str,
    "backendImage": str,
    "composeProject": str,
    "backendBaseUrl": str,
    "espHttpUrl": str,
    "assetOrigin": str,
    "otaUrl": str,
    "websocketUrl": str,
    "endpointAuthority": str,
    "syntheticIds": dict,
    "productionCandidateTarget": dict,
    "historicalInstallationProvenance": dict,
    "sessionNvsBaseline": dict,
    "activeLabApp": dict,
    "protectedTest": dict,
    "outputDirectory": str,
    "sessionStartedAt": str,
}
LOWER_SHA40 = re.compile(r"^[0-9a-f]{40}$")
UTC_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")
FORBIDDEN_OUTPUT = re.compile(
    r"(?i)(bearer|jwt[^\"]*value|secret[^\"]*value|password|private.?key|"
    r"transcript|utterance|raw.?speech|audio.?data|pronunciation.?score|"
    r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2})"
)
VALIDATION_PUBLIC_PEM = "-----BEGIN PUBLIC KEY-----\ntask07-validation-public\n-----END PUBLIC KEY-----"
VALIDATION_PRIVATE_PEM = "-----BEGIN PRIVATE KEY-----\ntask07-validation-private\n-----END PRIVATE KEY-----"


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _approved_lab_url(value: str, schemes: set[str], *, trailing_slash: bool = False) -> str | None:
    parsed = urlparse(value)
    if parsed.scheme not in schemes or parsed.username or parsed.password or not parsed.hostname:
        return None
    if trailing_slash and not value.endswith("/"):
        return None
    lowered = value.lower()
    if any(term in lowered for term in ("prod", "production", "public", "cloud")):
        return None
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        return None
    if (
        address.version != 4
        or not address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    ):
        return None
    return parsed.hostname


def validate_local_lab_endpoints(asset_origin: object, ota_url: object, websocket_url: object) -> list[str]:
    values = (
        ("assetOrigin", asset_origin, {"http"}, True),
        ("otaUrl", ota_url, {"http"}, False),
        ("websocketUrl", websocket_url, {"ws"}, False),
    )
    reasons: list[str] = []
    hosts: set[str] = set()
    for field, value, schemes, trailing_slash in values:
        host = _approved_lab_url(value, schemes, trailing_slash=trailing_slash) if isinstance(value, str) else None
        if host is None:
            reasons.append(field)
        else:
            hosts.add(host)
    if len(hosts) > 1:
        reasons.append("localLabRoute")
    return sorted(set(reasons))


def validate_input(document: object, *, repository_root: Path) -> list[str]:
    if not isinstance(document, dict):
        return ["input.not_object"]
    reasons: list[str] = []
    fields = set(document)
    for field in sorted(set(REQUIRED_INPUT_FIELDS) - fields):
        reasons.append(f"input.missing_field.{field}")
    for field in sorted(fields - set(REQUIRED_INPUT_FIELDS)):
        reasons.append(f"input.extra_field.{field}")
    for field, field_type in REQUIRED_INPUT_FIELDS.items():
        if field in document and type(document[field]) is not field_type:
            reasons.append(f"input.type.{field}")
    if reasons:
        return sorted(set(reasons))

    if document["schemaVersion"] != 1:
        reasons.append("input.schemaVersion")
    backend = Path(document["backendWorktree"])
    if not backend.is_absolute() or not backend.is_dir():
        reasons.append("input.backendWorktree")
    sha = document["backendSha"]
    if not LOWER_SHA40.fullmatch(sha):
        reasons.append("input.backendSha")
    if document["backendImage"] != f"{IMAGE_PREFIX}{sha}":
        reasons.append("input.backendImage")
    if document["composeProject"] != COMPOSE_PROJECT:
        reasons.append("input.composeProject")
    if document["backendBaseUrl"] != "http://127.0.0.1:3000":
        reasons.append("input.backendBaseUrl")
    if document["espHttpUrl"] != "http://host.docker.internal:8003":
        reasons.append("input.espHttpUrl")
    reasons.extend(
        f"input.{reason}"
        for reason in validate_local_lab_endpoints(
            document["assetOrigin"], document["otaUrl"], document["websocketUrl"]
        )
    )
    if document["endpointAuthority"] != "approved-local-task07-lab-route":
        reasons.append("input.endpointAuthority")
    if document["syntheticIds"] != EXPECTED_IDS:
        reasons.append("input.syntheticIds")
    if document["productionCandidateTarget"] != EXPECTED_PRODUCTION_CANDIDATE_TARGET:
        reasons.append("input.productionCandidateTarget")
    if document["historicalInstallationProvenance"] != EXPECTED_HISTORICAL_INSTALLATION_PROVENANCE:
        reasons.append("input.historicalInstallationProvenance")
    session_nvs = document["sessionNvsBaseline"]
    if not isinstance(session_nvs, dict) or set(session_nvs) != {"beforeInstallSha256"}:
        reasons.append("input.sessionNvsBaseline")
    else:
        value = session_nvs.get("beforeInstallSha256")
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
            reasons.append("input.sessionNvsBaseline.beforeInstallSha256")
    active_lab_app = document["activeLabApp"]
    if not isinstance(active_lab_app, dict) or set(active_lab_app) != {
        "firmwareSha", "applicationSha256", "bundleRootSha256"
    }:
        reasons.append("input.activeLabApp")
    else:
        if active_lab_app.get("firmwareSha") != ACTIVE_LAB_FIRMWARE_SHA:
            reasons.append("input.activeLabApp.firmwareSha")
        for field in ("applicationSha256", "bundleRootSha256"):
            value = active_lab_app.get(field)
            if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
                reasons.append(f"input.activeLabApp.{field}")
    if document["protectedTest"] != EXPECTED_PROTECTED:
        reasons.append("input.protectedTest")
    protected_path = Path(EXPECTED_PROTECTED["path"])
    try:
        protected_hash = hashlib.sha256(protected_path.read_bytes()).hexdigest()
    except OSError:
        protected_hash = ""
    if protected_hash != EXPECTED_PROTECTED["sha256"]:
        reasons.append("input.protectedTestHash")
    output = Path(document["outputDirectory"])
    try:
        resolved_output = output.resolve(strict=True)
        resolved_root = ARTIFACT_ROOT.resolve(strict=True)
    except OSError:
        reasons.append("input.outputDirectory")
    else:
        if not output.is_absolute() or not output.is_dir() or not _within(resolved_output, resolved_root):
            reasons.append("input.outputDirectory")
    if not UTC_TIME.fullmatch(document["sessionStartedAt"]):
        reasons.append("input.sessionStartedAt")
    return sorted(set(reasons))


def validate_image_inspect(document: object, expected_image: str, expected_sha: str) -> list[str]:
    if not isinstance(document, list) or len(document) != 1 or not isinstance(document[0], dict):
        return ["image.missing"]
    reasons: list[str] = []
    image = document[0]
    image_id = image.get("Id")
    if not isinstance(image_id, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
        reasons.append("image.id")
    if image.get("RepoTags") != [expected_image]:
        reasons.append("image.reference")
    labels = document[0].get("Config", {}).get("Labels", {})
    if not isinstance(labels, dict) or labels.get(MATERIALIZER_LABEL) != MATERIALIZER_PATH:
        reasons.append("image.materializer_label")
    if not isinstance(labels, dict) or labels.get("org.opencontainers.image.revision") != expected_sha:
        reasons.append("image.revision")
    if not isinstance(labels, dict) or labels.get("com.tbot.course-mode.build-source") != "reviewed-clean-git-worktree":
        reasons.append("image.build_provenance")
    return sorted(set(reasons))


def validate_compose(compose: object, expected: dict[str, object]) -> list[str]:
    if not isinstance(compose, dict):
        return ["compose.not_object"]
    reasons: list[str] = []
    services = compose.get("services")
    if not isinstance(services, dict):
        return ["compose.services"]
    expected_services = {"backend", "course-mode-materialize", "mysql", "postgres", "redis", "web"}
    if set(services) != expected_services:
        reasons.append("compose.services")
    if compose.get("name") != COMPOSE_PROJECT:
        reasons.append("compose.project")
    backend = services.get("backend", {})
    materializer = services.get("course-mode-materialize", {})
    rendered = json.dumps(compose, sort_keys=True).lower()
    if "https://" in rendered or "production" in rendered or "prod." in rendered:
        reasons.append("compose.production_like_host")
    if backend.get("image") != expected["backendImage"]:
        reasons.append("compose.backend.image")
    if materializer.get("image") != expected["backendImage"]:
        reasons.append("compose.materializer.image")
    ports = backend.get("ports")
    if (
        not isinstance(ports, list)
        or len(ports) != 1
        or ports[0].get("host_ip") != "127.0.0.1"
        or ports[0].get("target") != 3000
        or ports[0].get("published") != "3000"
        or ports[0].get("mode", "ingress") != "ingress"
        or ports[0].get("protocol", "tcp") != "tcp"
    ):
        reasons.append("compose.backend.port")
    backend_env = backend.get("environment", {})
    if not isinstance(backend_env, dict) or (
        backend_env.get("ROBOT_ESP_BASE_URL") != expected["espHttpUrl"]
        or backend_env.get("TBOT_ESP_SERVER_URL") != expected["espHttpUrl"]
    ):
        reasons.append("compose.backend.esp_url")
    if isinstance(backend_env, dict) and (
        backend_env.get("LESSON_ASSET_ORIGIN_BASE") != expected["assetOrigin"]
        or backend_env.get("FLATTENED_CINEMATIC_PUBLIC_BASE_URL") != expected["assetOrigin"]
    ):
        reasons.append("compose.backend.asset_origin")
    if not isinstance(backend_env, dict) or backend_env.get("LESSON_ROLLOUT_DEVICE_ALLOWLIST") != "14:c1:9f:d1:ac:20":
        reasons.append("compose.assignment_scope")
    if not isinstance(backend_env, dict) or backend_env.get("JWT_PUBLIC_KEY") != VALIDATION_PUBLIC_PEM:
        reasons.append("compose.jwt_public_key")
    if not isinstance(backend_env, dict) or backend_env.get("JWT_PRIVATE_KEY") != VALIDATION_PRIVATE_PEM:
        reasons.append("compose.jwt_private_key")
    if not isinstance(backend_env, dict) or backend_env.get("TBOT_DEVICE_MINT_SECRET") != "task07-sentinel-mint":
        reasons.append("compose.secret_sentinel.TBOT_DEVICE_MINT_SECRET")
    if materializer.get("command") != MATERIALIZER_COMMAND:
        reasons.append("compose.materializer.command")
    materializer_env = materializer.get("environment", {})
    if not isinstance(materializer_env, dict) or materializer_env.get("COURSE_MODE_DEVICE_MAC") != "14:c1:9f:d1:ac:20":
        reasons.append("compose.assignment_scope")
    if not isinstance(materializer_env, dict) or materializer_env.get("DATABASE_URL") != "postgresql://tbot:tbot@postgres:5432/tbot":
        reasons.append("compose.materializer.database")
    if (
        not isinstance(backend_env, dict)
        or backend_env.get("COURSE_MODE_LOCAL_PHYSICAL_TFT_ENABLED") != "true"
        or not isinstance(materializer_env, dict)
        or materializer_env.get("COURSE_MODE_LOCAL_PHYSICAL_TFT_ENABLED") != "true"
        or materializer_env.get("COURSE_MODE_LOCAL_COMPOSE_ENABLED") != "true"
    ):
        reasons.append("compose.local_gate")
    mounts = materializer.get("volumes")
    expected_source = str(Path(expected["backendWorktree"]) / "src/lessons/fixtures/course-mode")
    if not isinstance(mounts, list) or len(mounts) != 1 or mounts[0].get("source") != expected_source or mounts[0].get("target") != "/course-mode-fixtures" or mounts[0].get("read_only") is not True:
        reasons.append("compose.materializer.fixture_mount")
    for service in services.values():
        if isinstance(service, dict) and service.get("profiles"):
            reasons.append("compose.profiles")
        for mount in service.get("volumes", []) if isinstance(service, dict) else []:
            if isinstance(mount, dict) and mount.get("type") == "bind" and mount.get("read_only") is not True:
                reasons.append("compose.bind_mount")
    network = compose.get("networks", {}).get("lesson-studio-e2e", {})
    volumes = compose.get("volumes", {})
    names = {value.get("name") for value in volumes.values()} if isinstance(volumes, dict) else set()
    if network.get("name") != COMPOSE_PROJECT or names != {
        f"{COMPOSE_PROJECT}-pg-data", f"{COMPOSE_PROJECT}-redis-data", f"{COMPOSE_PROJECT}-mysql-data"
    }:
        reasons.append("compose.resources")
    return sorted(set(reasons))


def _command_env(expected: dict[str, object]) -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "JWT_PUBLIC_KEY": VALIDATION_PUBLIC_PEM,
        "JWT_PRIVATE_KEY": VALIDATION_PRIVATE_PEM,
        "TBOT_DEVICE_MINT_SECRET": "task07-sentinel-mint",
        "LESSON_ASSET_ORIGIN_BASE": "http://127.0.0.1:8102/tvideo-demo",
        "COURSE_MODE_ASSET_ORIGIN_BASE": str(expected["assetOrigin"]),
        "ROBOT_ESP_BASE_URL": str(expected["espHttpUrl"]),
        "TBOT_ESP_SERVER_URL": str(expected["espHttpUrl"]),
        "TBOT_BACKEND_WORKTREE": str(expected["backendWorktree"]),
        "TBOT_LESSON_STUDIO_BACKEND_IMAGE": str(expected["backendImage"]),
        "LESSON_STUDIO_E2E_RESOURCE_PREFIX": COMPOSE_PROJECT,
    }


def _run(command: list[str], *, cwd: Path, env: dict[str, str]) -> tuple[str, str | None]:
    try:
        result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, check=False)
    except OSError:
        return "", "command.unavailable"
    if result.returncode != 0:
        return "", "command.failed"
    return result.stdout, None


def _emit(payload: dict[str, object]) -> None:
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    repository_root = Path(__file__).resolve().parents[3]
    try:
        expected = json.loads(args.input.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        _emit({"valid": False, "reasons": ["input.invalid_json"]})
        return 1
    except (OSError, UnicodeError):
        _emit({"valid": False, "reasons": ["input.unreadable"]})
        return 1
    reasons = validate_input(expected, repository_root=repository_root)
    if reasons:
        _emit({"valid": False, "reasons": reasons})
        return 1
    output_directory = Path(expected["outputDirectory"])
    if args.output.parent.resolve() != output_directory.resolve() or args.output.exists():
        _emit({"valid": False, "reasons": ["output.path"]})
        return 1

    backend = str(expected["backendWorktree"])
    base_compose = repository_root / "docs/docker/docker-compose.lesson-studio-e2e.yml"
    overlay_compose = repository_root / "docs/docker/docker-compose.course-mode-physical-tft.yml"
    commands = [
        ["git", "-C", backend, "rev-parse", "--show-toplevel"],
        ["git", "-C", backend, "rev-parse", "HEAD"],
        ["git", "-C", backend, "status", "--porcelain", "--untracked-files=all"],
        ["docker", "image", "inspect", str(expected["backendImage"])],
        [
            "docker", "compose", "--project-name", COMPOSE_PROJECT,
            "-f", str(base_compose), "-f", str(overlay_compose),
            "config", "--format", "json",
        ],
    ]
    outputs: list[str] = []
    command_env = _command_env(expected)
    for index, command in enumerate(commands):
        stdout, error = _run(command, cwd=repository_root, env=command_env)
        if error:
            reasons.append(f"{error}.{index + 1}")
            break
        outputs.append(stdout)
    if not reasons and Path(outputs[0].strip()).resolve() != Path(backend).resolve():
        reasons.append("git.backend_root")
    if not reasons and outputs[1].strip() != expected["backendSha"]:
        reasons.append("git.backend_sha")
    if not reasons and outputs[2] != "":
        reasons.append("git.backend_dirty")
    image_document: object = None
    compose_document: object = None
    if not reasons:
        try:
            image_document = json.loads(outputs[3])
            compose_document = json.loads(outputs[4])
        except json.JSONDecodeError:
            reasons.append("command.invalid_json")
    if not reasons:
        reasons.extend(validate_image_inspect(
            image_document,
            str(expected["backendImage"]),
            str(expected["backendSha"]),
        ))
        reasons.extend(validate_compose(compose_document, expected))
    if reasons:
        _emit({"valid": False, "reasons": sorted(set(reasons))})
        return 1

    payload = {
        "backendImage": expected["backendImage"],
        "backendSha": expected["backendSha"],
        "productionCandidateTarget": expected["productionCandidateTarget"],
        "historicalInstallationProvenance": expected["historicalInstallationProvenance"],
        "sessionNvsBaseline": expected["sessionNvsBaseline"],
        "activeLabApp": expected["activeLabApp"],
        "composeProject": COMPOSE_PROJECT,
        "deviceSuffix": "AC:20",
        "endpointAuthority": expected["endpointAuthority"],
        "endpoints": {
            "backendBaseUrl": expected["backendBaseUrl"],
            "espHttpUrl": expected["espHttpUrl"],
            "assetOrigin": expected["assetOrigin"],
            "otaUrl": expected["otaUrl"],
            "websocketUrl": expected["websocketUrl"],
            "authority": expected["endpointAuthority"],
        },
        "imageId": image_document[0]["Id"],
        "result": "PASS",
        "secrets": {
            "JWT_KEY_PAIR": "present-redacted",
            "TBOT_DEVICE_MINT_SECRET": "present-redacted",
        },
        "sessionStartedAt": expected["sessionStartedAt"],
        "syntheticIds": expected["syntheticIds"],
        "valid": True,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
    if FORBIDDEN_OUTPUT.search(encoded):
        _emit({"valid": False, "reasons": ["output.redaction"]})
        return 1
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=output_directory, delete=False) as handle:
        handle.write(encoded)
        temporary = Path(handle.name)
    temporary.replace(args.output)
    _emit(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
