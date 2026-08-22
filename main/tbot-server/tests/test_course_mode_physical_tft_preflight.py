import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from copy import deepcopy
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
SERVER = ROOT / "main/tbot-server"
SCRIPT = SERVER / "scripts/course_mode_physical_tft_preflight.py"
PROTECTED = SERVER / "tests/test_lesson_voice_output_discipline.py"
ARTIFACT_ROOT = Path("/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-task07")
SHA = "0123456789abcdef0123456789abcdef01234567"
IMAGE = f"local/tbot-backend:course-mode-physical-tft-{SHA}"
LABEL_PATH = "/app/dist/lessons/course-mode/course-mode-local-materializer.js"
EXPECTED_IDS = {
    "courseId": "70000000-0000-4000-8000-000000000003",
    "lessonId": "70000000-0000-4000-8000-000000000004",
    "deviceId": "70000000-0000-4000-8000-000000000005",
    "assignmentId": "70000000-0000-4000-8000-000000000006",
    "adultOperatorId": "70000000-0000-4000-8000-000000000007",
}
PRODUCTION_CANDIDATE_TARGET = {
    "firmwareSha": "3d4a1e2a32359278124c61e56fd459fac618506e",
    "applicationSha256": "84c999ece0c90eb6e69a410e335c7791f330e9c0fd39c30dfd4162bb7c4cfc6e",
    "bundleRootSha256": "9ef3729d0faec7b02d867cedb3ab30d110b845b1c0133738c588bba0e0c16be6",
}
HISTORICAL_INSTALLATION_PROVENANCE = {
    "preservedNvsSha256": "a7a87f72416be20388298cb70cfff306ec78e77f0e8b09231d16113f3d82404e",
}
SESSION_NVS_BASELINE = {"beforeInstallSha256": "0" * 64}
ACTIVE_LAB_APP = {
    "firmwareSha": "3df15a712a9e7ed656a1a9f240bd2ac2bf8ba989",
    "applicationSha256": "c" * 64,
    "bundleRootSha256": "d" * 64,
}


def valid_input(backend: Path, output_directory: Path):
    return {
        "schemaVersion": 1,
        "backendWorktree": str(backend),
        "backendSha": SHA,
        "backendImage": IMAGE,
        "composeProject": "tbot-course-mode-physical-tft",
        "backendBaseUrl": "http://127.0.0.1:3000",
        "espHttpUrl": "http://host.docker.internal:8003",
        "assetOrigin": "http://192.168.100.183:8102/",
        "otaUrl": "http://192.168.100.183:8003/ota",
        "websocketUrl": "ws://192.168.100.183:8003/ws",
        "endpointAuthority": "approved-local-task07-lab-route",
        "syntheticIds": deepcopy(EXPECTED_IDS),
        "productionCandidateTarget": deepcopy(PRODUCTION_CANDIDATE_TARGET),
        "historicalInstallationProvenance": deepcopy(HISTORICAL_INSTALLATION_PROVENANCE),
        "sessionNvsBaseline": deepcopy(SESSION_NVS_BASELINE),
        "activeLabApp": deepcopy(ACTIVE_LAB_APP),
        "protectedTest": {
            "path": str(
                Path("/Users/manhhodinh/Documents/TBOT/robot/esp32-server")
                / "main/tbot-server/tests/test_lesson_voice_output_discipline.py"
            ),
            "sha256": "08f77b5452301224b17b4b333d2d032fff40c06aa2eaea97fa90932dae7d97e3",
        },
        "outputDirectory": str(output_directory),
        "sessionStartedAt": "2026-08-22T00:00:00Z",
    }


def valid_compose(backend: Path):
    fixture_source = str(backend / "src/lessons/fixtures/course-mode")
    services = {
        "backend": {
            "container_name": "tbot-course-mode-physical-tft-backend",
            "image": IMAGE,
            "ports": [{
                "mode": "ingress", "host_ip": "127.0.0.1", "target": 3000,
                "published": "3000", "protocol": "tcp",
            }],
            "extra_hosts": ["host.docker.internal=host-gateway"],
            "environment": {
                "ROBOT_ESP_BASE_URL": "http://host.docker.internal:8003",
                "TBOT_ESP_SERVER_URL": "http://host.docker.internal:8003",
                "LESSON_ASSET_ORIGIN_BASE": "http://192.168.100.183:8102/",
                "FLATTENED_CINEMATIC_PUBLIC_BASE_URL": "http://192.168.100.183:8102/",
                "LESSON_ROLLOUT_DEVICE_ALLOWLIST": "14:c1:9f:d1:ac:20",
                "COURSE_MODE_LOCAL_PHYSICAL_TFT_ENABLED": "true",
                "COURSE_MODE_V2_PUBLISH_ENABLED": "true",
                "LESSON_STUDIO_NEW_ASSIGNMENTS_ENABLED": "true",
                "TBOT_DEVICE_MINT_SECRET": "task07-sentinel-mint",
                "JWT_PUBLIC_KEY": "task07-sentinel-public",
            },
        },
        "course-mode-materialize": {
            "image": IMAGE,
            "command": [
                "dist/lessons/course-mode/course-mode-local-materializer.js",
                "materialize",
            ],
            "depends_on": {"backend": {"condition": "service_healthy"}},
            "environment": {
                "COURSE_MODE_DEVICE_MAC": "14:c1:9f:d1:ac:20",
                "COURSE_MODE_FIXTURE_ROOT": "/course-mode-fixtures",
                "COURSE_MODE_LOCAL_COMPOSE_ENABLED": "true",
                "COURSE_MODE_LOCAL_PHYSICAL_TFT_ENABLED": "true",
                "FLATTENED_CINEMATIC_PUBLIC_BASE_URL": "http://192.168.100.183:8102/",
                "DATABASE_URL": "postgresql://tbot:tbot@postgres:5432/tbot",
            },
            "volumes": [{
                "type": "bind", "source": fixture_source,
                "target": "/course-mode-fixtures", "read_only": True,
            }],
        },
        "postgres": {"container_name": "tbot-course-mode-physical-tft-pg"},
        "redis": {"container_name": "tbot-course-mode-physical-tft-redis"},
        "mysql": {"container_name": "tbot-course-mode-physical-tft-mysql"},
        "web": {"container_name": "tbot-course-mode-physical-tft-web"},
    }
    return {
        "name": "tbot-course-mode-physical-tft",
        "services": services,
        "networks": {"lesson-studio-e2e": {"name": "tbot-course-mode-physical-tft"}},
        "volumes": {
            "lesson-studio-pg-data": {"name": "tbot-course-mode-physical-tft-pg-data"},
            "lesson-studio-redis-data": {"name": "tbot-course-mode-physical-tft-redis-data"},
            "lesson-studio-mysql-data": {"name": "tbot-course-mode-physical-tft-mysql-data"},
        },
    }


@pytest.fixture
def artifact_directory():
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    path = Path(tempfile.mkdtemp(prefix="pytest-tft-", dir=ARTIFACT_ROOT))
    yield path
    shutil.rmtree(path)


def _fake_commands(tmp_path: Path, backend: Path, compose: dict):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "commands.jsonl"
    git = fake_bin / "git"
    git.write_text(
        "#!/usr/bin/env python3\n"
        "import json,sys\n"
        f"open({str(log)!r},'a').write(json.dumps(sys.argv[1:])+'\\n')\n"
        f"args=sys.argv[1:]; root={str(backend)!r}; sha={SHA!r}\n"
        "if args[-2:]==['rev-parse','--show-toplevel']: print(root)\n"
        "elif args[-2:]==['rev-parse','HEAD']: print(sha)\n"
        "elif args[-3:]==['status','--porcelain','--untracked-files=all']: pass\n"
        "else: raise SystemExit(93)\n",
        encoding="utf-8",
    )
    docker = fake_bin / "docker"
    image_inspect = [{
        "Id": "sha256:" + "a" * 64,
        "RepoTags": [IMAGE],
        "Config": {"Labels": {
            "com.tbot.course-mode.materializer-path": LABEL_PATH,
            "org.opencontainers.image.revision": SHA,
            "com.tbot.course-mode.build-source": "reviewed-clean-git-worktree",
        }},
    }]
    docker.write_text(
        "#!/usr/bin/env python3\n"
        "import json,sys\n"
        f"open({str(log)!r},'a').write(json.dumps(sys.argv[1:])+'\\n')\n"
        f"args=sys.argv[1:]; inspect={image_inspect!r}; compose={compose!r}\n"
        "if args[:2]==['image','inspect']: print(json.dumps(inspect))\n"
        "elif args[0]=='compose' and args[-3:]==['config','--format','json']: print(json.dumps(compose))\n"
        "else: raise SystemExit(94)\n",
        encoding="utf-8",
    )
    git.chmod(0o755)
    docker.chmod(0o755)
    return fake_bin, log


def test_valid_preflight_runs_only_five_read_only_commands_and_redacts(tmp_path, artifact_directory):
    backend = tmp_path / "backend"
    backend.mkdir()
    document = valid_input(backend, artifact_directory)
    fake_bin, log = _fake_commands(tmp_path, backend, valid_compose(backend))
    input_path = artifact_directory / "preflight-input.json"
    output_path = artifact_directory / "preflight-result.json"
    input_path.write_text(json.dumps(document), encoding="utf-8")
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["DO_NOT_READ_SECRET"] = "private-secret-value"

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--input", str(input_path), "--output", str(output_path)],
        cwd=ROOT, env=env, capture_output=True, text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["valid"] is True
    assert payload["imageId"] == "sha256:" + "a" * 64
    assert payload["secrets"] == {
        "JWT_PUBLIC_KEY": "present-redacted",
        "TBOT_DEVICE_MINT_SECRET": "present-redacted",
    }
    assert "private-secret-value" not in json.dumps(payload)
    assert payload["productionCandidateTarget"] == PRODUCTION_CANDIDATE_TARGET
    assert payload["historicalInstallationProvenance"] == HISTORICAL_INSTALLATION_PROVENANCE
    assert payload["sessionNvsBaseline"] == SESSION_NVS_BASELINE
    assert payload["activeLabApp"] == ACTIVE_LAB_APP
    calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert calls == [
        ["-C", str(backend), "rev-parse", "--show-toplevel"],
        ["-C", str(backend), "rev-parse", "HEAD"],
        ["-C", str(backend), "status", "--porcelain", "--untracked-files=all"],
        ["image", "inspect", IMAGE],
        [
            "compose", "--project-name", "tbot-course-mode-physical-tft",
            "-f", str(ROOT / "docs/docker/docker-compose.lesson-studio-e2e.yml"),
            "-f", str(ROOT / "docs/docker/docker-compose.course-mode-physical-tft.yml"),
            "config", "--format", "json",
        ],
    ]


def test_input_contract_rejects_missing_extra_override_and_identity_drift(tmp_path, artifact_directory):
    sys.path.insert(0, str(SERVER / "scripts"))
    from course_mode_physical_tft_preflight import validate_input

    backend = tmp_path / "backend"
    backend.mkdir()
    base = valid_input(backend, artifact_directory)
    mutations = [
        (lambda d: d.pop("otaUrl"), "input.missing_field.otaUrl"),
        (lambda d: d.update(command=["docker", "up"]), "input.extra_field.command"),
        (lambda d: d.update(backendSha="f" * 40), "input.backendImage"),
        (lambda d: d.update(backendImage="local/tbot-backend:latest"), "input.backendImage"),
        (lambda d: d.update(composeProject="wrong-project"), "input.composeProject"),
        (lambda d: d.update(espHttpUrl="https://production.example"), "input.espHttpUrl"),
        (lambda d: d.update(assetOrigin="https://prod.example/assets/"), "input.assetOrigin"),
        (lambda d: d.update(otaUrl="http://127.0.0.1:8003/ota"), "input.otaUrl"),
        (lambda d: d.update(websocketUrl="ws://192.168.100.184:8003/ws"), "input.localLabRoute"),
        (lambda d: d["syntheticIds"].update(courseId="not-approved"), "input.syntheticIds"),
        (lambda d: d["productionCandidateTarget"].update(applicationSha256="b" * 64), "input.productionCandidateTarget"),
        (lambda d: d.pop("historicalInstallationProvenance"), "input.missing_field.historicalInstallationProvenance"),
        (lambda d: d["historicalInstallationProvenance"].update(preservedNvsSha256="b" * 64), "input.historicalInstallationProvenance"),
        (lambda d: d["sessionNvsBaseline"].update(beforeInstallSha256="short"), "input.sessionNvsBaseline.beforeInstallSha256"),
        (lambda d: d["sessionNvsBaseline"].update(beforeInstallSha256="A" * 64), "input.sessionNvsBaseline.beforeInstallSha256"),
        (lambda d: d["sessionNvsBaseline"].update(afterInstallSha256="0" * 64), "input.sessionNvsBaseline"),
        (lambda d: d["activeLabApp"].update(firmwareSha="5b6121b7933cda25908cc5bd07f1b494f00728ca"), "input.activeLabApp.firmwareSha"),
        (lambda d: d["activeLabApp"].update(firmwareSha="f" * 40), "input.activeLabApp.firmwareSha"),
        (lambda d: d["activeLabApp"].update(applicationSha256="short"), "input.activeLabApp.applicationSha256"),
        (lambda d: d["activeLabApp"].update(bundleRootSha256="A" * 64), "input.activeLabApp.bundleRootSha256"),
        (lambda d: d["protectedTest"].update(sha256="b" * 64), "input.protectedTest"),
        (lambda d: d.update(outputDirectory=str(tmp_path)), "input.outputDirectory"),
    ]
    for mutate, expected_reason in mutations:
        document = deepcopy(base)
        mutate(document)
        assert expected_reason in validate_input(document, repository_root=ROOT)


def test_output_symlink_escape_and_protected_hash_drift_fail(tmp_path, artifact_directory):
    sys.path.insert(0, str(SERVER / "scripts"))
    from course_mode_physical_tft_preflight import validate_input

    backend = tmp_path / "backend"
    backend.mkdir()
    escaped = artifact_directory / "escaped"
    escaped.symlink_to(tmp_path, target_is_directory=True)
    document = valid_input(backend, escaped)
    assert "input.outputDirectory" in validate_input(document, repository_root=ROOT)

    protected_copy = tmp_path / "protected.py"
    protected_copy.write_text("drift\n", encoding="utf-8")
    document = valid_input(backend, artifact_directory)
    canonical = Path(document["protectedTest"]["path"])
    assert hashlib.sha256(canonical.read_bytes()).hexdigest() == document["protectedTest"]["sha256"]


def test_compose_and_image_validation_fail_closed(tmp_path, artifact_directory):
    sys.path.insert(0, str(SERVER / "scripts"))
    from course_mode_physical_tft_preflight import validate_compose, validate_image_inspect

    backend = tmp_path / "backend"
    backend.mkdir()
    document = valid_input(backend, artifact_directory)
    base = valid_compose(backend)
    mutations = [
        (lambda c: c.update(name="wrong"), "compose.project"),
        (lambda c: c["services"]["backend"].update(image="local/tbot-backend:latest"), "compose.backend.image"),
        (lambda c: c["services"]["backend"].update(ports=[{"host_ip": "0.0.0.0", "target": 3000, "published": "3000"}]), "compose.backend.port"),
        (lambda c: c["services"]["backend"]["environment"].update(ROBOT_ESP_BASE_URL="http://host.docker.internal:9000"), "compose.backend.esp_url"),
        (lambda c: c["services"]["course-mode-materialize"].update(command=["wrong"]), "compose.materializer.command"),
        (lambda c: c["services"]["course-mode-materialize"]["environment"].update(DATABASE_URL="mysql://wrong"), "compose.materializer.database"),
        (lambda c: c["services"]["backend"]["environment"].update(COURSE_MODE_LOCAL_PHYSICAL_TFT_ENABLED="false"), "compose.local_gate"),
        (lambda c: c["services"]["web"].update(environment={"HIDDEN_URL": "https://production.example"}), "compose.production_like_host"),
        (lambda c: c["services"]["course-mode-materialize"]["volumes"][0].update(read_only=False), "compose.materializer.fixture_mount"),
        (lambda c: c["services"].update({"seed-postgres": {}}), "compose.services"),
        (lambda c: c["services"]["backend"]["environment"].update(LESSON_ROLLOUT_DEVICE_ALLOWLIST="wrong"), "compose.assignment_scope"),
        (lambda c: c["volumes"]["lesson-studio-pg-data"].update(name="shared-pg"), "compose.resources"),
    ]
    for mutate, expected_reason in mutations:
        compose = deepcopy(base)
        mutate(compose)
        assert expected_reason in validate_compose(compose, document)

    valid_image = [{
        "Id": "sha256:" + "a" * 64,
        "RepoTags": [IMAGE],
        "Config": {"Labels": {
            "com.tbot.course-mode.materializer-path": LABEL_PATH,
            "org.opencontainers.image.revision": SHA,
            "com.tbot.course-mode.build-source": "reviewed-clean-git-worktree",
        }},
    }]
    assert validate_image_inspect([], IMAGE, SHA) == ["image.missing"]
    assert "image.materializer_label" in validate_image_inspect(
        [{"Id": "sha256:" + "a" * 64, "RepoTags": [IMAGE], "Config": {"Labels": {}}}],
        IMAGE,
        SHA,
    )
    stale = deepcopy(valid_image)
    stale[0]["Config"]["Labels"]["org.opencontainers.image.revision"] = "f" * 40
    assert "image.revision" in validate_image_inspect(stale, IMAGE, SHA)
    retagged = deepcopy(valid_image)
    retagged[0]["RepoTags"] = ["local/tbot-backend:latest"]
    assert "image.reference" in validate_image_inspect(retagged, IMAGE, SHA)
    aliased = deepcopy(valid_image)
    aliased[0]["RepoTags"].append("local/tbot-backend:latest")
    assert "image.reference" in validate_image_inspect(aliased, IMAGE, SHA)
    missing_id = deepcopy(valid_image)
    missing_id[0]["Id"] = "not-an-image-id"
    assert "image.id" in validate_image_inspect(missing_id, IMAGE, SHA)


def test_malformed_input_never_executes_commands(tmp_path, artifact_directory):
    input_path = artifact_directory / "preflight-input.json"
    output_path = artifact_directory / "preflight-result.json"
    input_path.write_text("not-json-private-secret", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--input", str(input_path), "--output", str(output_path)],
        env={"PATH": ""}, capture_output=True, text=True,
    )
    assert result.returncode == 1
    assert json.loads(result.stdout)["reasons"] == ["input.invalid_json"]
    assert "private-secret" not in result.stdout


def test_active_lab_artifact_hashes_are_supplied_immutable_values_not_hard_coded(tmp_path, artifact_directory):
    sys.path.insert(0, str(SERVER / "scripts"))
    from course_mode_physical_tft_preflight import validate_input

    backend = tmp_path / "backend"
    backend.mkdir()
    document = valid_input(backend, artifact_directory)
    document["activeLabApp"]["applicationSha256"] = "e" * 64
    document["activeLabApp"]["bundleRootSha256"] = "f" * 64

    assert validate_input(document, repository_root=ROOT) == []


def test_current_session_nvs_baseline_is_exact_caller_evidence_not_historical_prerequisite(
    tmp_path, artifact_directory
):
    sys.path.insert(0, str(SERVER / "scripts"))
    from course_mode_physical_tft_preflight import validate_input

    backend = tmp_path / "backend"
    backend.mkdir()
    document = valid_input(backend, artifact_directory)
    document["sessionNvsBaseline"]["beforeInstallSha256"] = "6" * 64

    assert document["sessionNvsBaseline"]["beforeInstallSha256"] != (
        document["historicalInstallationProvenance"]["preservedNvsSha256"]
    )
    assert validate_input(document, repository_root=ROOT) == []
