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
ARTIFACT_ROOT = Path("/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-task07")
BACKEND_SHA = "0efd1bed84a3ef160bab2aa5fb4357bf521fde7e"
ESP_SHA = "ac76c22f9d535ea012e10ae27db2f79a84f74b1b"
FIRMWARE_SHA = "3b13883b6e8a8f6495c5670ebdc194392e38de75"
CONTRACT_SHA = "52303f656b6b21e4a65fc1a7179f7668888a7682c2e86b6d1319f201a548c840"
MANIFEST_SHA = hashlib.sha256(b"canonical-w1-manifest").hexdigest()
STEPS_SHA = hashlib.sha256(b"canonical-w1-steps").hexdigest()
REPLACEMENT_ID = "20000000-0000-4000-8000-000000000001"
SOURCE_ID = "10000000-0000-4000-8000-000000000001"
ASSIGNMENT_ID = "30000000-0000-4000-8000-000000000001"


@pytest.fixture
def session_dir():
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    path = Path(tempfile.mkdtemp(prefix="pytest-task12-tft-", dir=ARTIFACT_ROOT))
    yield path
    shutil.rmtree(path)


def _evidence(path: Path, name: str, payload: bytes) -> tuple[str, str]:
    target = path / name
    target.write_bytes(payload)
    return str(target), hashlib.sha256(payload).hexdigest()


def valid_input(session: Path, tmp_path: Path) -> dict:
    repositories = {}
    for name, sha, allowlist in (
        ("backend", BACKEND_SHA, []),
        ("esp", ESP_SHA, ["main/tbot-server/tests/test_lesson_voice_output_discipline.py"]),
        ("firmware", FIRMWARE_SHA, []),
    ):
        path = tmp_path / name
        path.mkdir(exist_ok=True)
        repositories[name] = {"path": str(path), "sha": sha, "dirtyAllowlist": allowlist}
    image_path, image_sha = _evidence(session, "candidate-app.bin", b"candidate-app")
    pre_path, pre_sha = _evidence(session, "app-before.bin", b"old-app")
    post_path, post_sha = _evidence(session, "app-after.bin", b"candidate-app")
    nvs_before, nvs_sha = _evidence(session, "nvs-before.bin", b"nvs-state")
    nvs_after, _ = _evidence(session, "nvs-after.bin", b"nvs-state")
    receipt_path, receipt_sha = _evidence(session, "materializer-receipt.json", b"{}")

    def layer(slot, media, suffix):
        metadata = (
            {
                "mediaKind": "video",
                "codec": "mjpeg",
                "fps": 10,
                "durationMs": 3000,
                "frameCount": 30,
                "hasAudio": False,
                "chromaKey": {"keyColor": "#00ff00", "tolerance": 20, "featherPx": 1},
                "rect": {"x": 118, "y": 160, "width": 150, "height": 150},
            }
            if media == "video/mp4"
            else {
                "mediaKind": "image",
                "fit": "cover" if slot == "backgroundScene" else "contain",
                "rect": {"x": 0, "y": 0, "width": 480, "height": 320},
            }
        )
        return {
            "slot": slot,
            "assetKey": f"w01.{slot}.{suffix}",
            "assetVersionId": f"40000000-0000-4000-8000-00000000000{suffix}",
            "version": 1,
            "state": "READY",
            "publicationState": "published",
            "immutable": True,
            "storagePath": f"published/w01/{slot}-{suffix}",
            "sha256": hashlib.sha256(f"layer-{suffix}".encode()).hexdigest(),
            "bytes": 100 + int(suffix),
            "mediaType": media,
            "width": 480 if slot == "backgroundScene" else 150,
            "height": 320 if slot == "backgroundScene" else 150,
            "compatibilityMetadata": metadata,
        }

    phases = [
        {
            "phaseId": "teach",
            "activityIds": ["w01-a1"],
            "templateId": "layeredCinematic",
            "templateVersion": 1,
            "playbackMode": "once",
            "layers": [
                layer("backgroundScene", "image/jpeg", "1"),
                layer("teachingObject", "image/png", "2"),
                layer("robotOverlay", "video/mp4", "3"),
            ],
        },
        {
            "phaseId": "listen",
            "activityIds": ["w01-a2"],
            "templateId": "layeredCinematic",
            "templateVersion": 1,
            "playbackMode": "once",
            "layers": [
                layer("backgroundScene", "image/jpeg", "4"),
                layer("robotOverlay", "video/mp4", "5"),
            ],
        },
    ]
    phase_checksum = hashlib.sha256(json.dumps(phases, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {
        "schemaVersion": 2,
        "sessionDirectory": str(session),
        "sessionStartedAt": "2026-08-29T00:00:00Z",
        "outputDirectory": str(session),
        "localStack": {
            "environment": "local-attended-lab",
            "productionMutationAllowed": False,
            "databaseAuthority": "isolated-local-compose",
            "adminMutationAllowed": False,
            "backendBaseUrl": "http://127.0.0.1:3000",
            "espHttpUrl": "http://host.docker.internal:8003",
            "assetOrigin": "http://192.168.100.183:8102/",
            "otaUrl": "http://192.168.100.183:8003/ota",
            "websocketUrl": "ws://192.168.100.183:8003/ws",
        },
        "repositories": repositories,
        "firmwareRequiredAncestor": FIRMWARE_SHA,
        "backendImage": f"local/tbot-backend:course-mode-physical-tft-{BACKEND_SHA}",
        "composeProject": "tbot-course-mode-physical-tft",
        "course": {"courseId": "a17792f6-8d86-4ad1-a6f3-77663b4d4674", "courseKey": "english-6month-4-6"},
        "lesson": {
            "lessonKey": "w01-greetings-politeness",
            "sourceLessonId": SOURCE_ID,
            "sourceState": "ARCHIVED",
            "sourceAssignable": False,
            "replacementId": REPLACEMENT_ID,
            "replacementVersion": 2,
            "mappingState": "ACTIVE",
            "activeReplacementCount": 1,
            "contractVersion": "courseCompanion.v2.contract.v1",
            "rendererId": "teebot-lesson-renderer.v5",
            "visualLayoutContract": "renderer-v5.layered-cinematic-layout.v1",
            "contractChecksum": CONTRACT_SHA,
            "manifestChecksum": MANIFEST_SHA,
            "stepsChecksum": STEPS_SHA,
            "backendSha": BACKEND_SHA,
        },
        "assignments": [
            {
                "assignmentId": ASSIGNMENT_ID,
                "state": "ACTIVE",
                "lessonId": REPLACEMENT_ID,
                "lessonVersion": 2,
                "robotMac": "14-C1-9F-D1-AC-20",
            }
        ],
        "robots": [{"mac": "14C19FD1AC20", "state": "ACTIVE"}],
        "visualPack": {
            "state": "READY",
            "publicationState": "published",
            "immutable": True,
            "lessonId": REPLACEMENT_ID,
            "lessonVersion": 2,
            "contractChecksum": CONTRACT_SHA,
            "manifestChecksum": MANIFEST_SHA,
            "rendererId": "teebot-lesson-renderer.v5",
            "visualLayoutContract": "renderer-v5.layered-cinematic-layout.v1",
            "phases": phases,
            "phaseAuthorityChecksum": phase_checksum,
        },
        "materializerReceipt": {
            "path": receipt_path,
            "sha256": receipt_sha,
            "result": "pass",
            "courseId": "a17792f6-8d86-4ad1-a6f3-77663b4d4674",
            "courseKey": "english-6month-4-6",
            "lessonKey": "w01-greetings-politeness",
            "lessonId": REPLACEMENT_ID,
            "lessonVersion": 2,
            "assignmentId": ASSIGNMENT_ID,
            "robotMac": "14:c1:9f:d1:ac:20",
            "contractChecksum": CONTRACT_SHA,
            "manifestChecksum": MANIFEST_SHA,
            "stepsChecksum": STEPS_SHA,
            "rendererId": "teebot-lesson-renderer.v5",
            "visualLayoutContract": "renderer-v5.layered-cinematic-layout.v1",
            "phaseAuthorityChecksum": phase_checksum,
        },
        "flashPlan": {
            "appOffset": "0x20000",
            "appSize": len(b"candidate-app"),
            "imagePath": image_path,
            "imageSha256": image_sha,
            "operations": [
                {
                    "operation": "read_flash",
                    "offset": "0x20000",
                    "size": len(b"candidate-app"),
                    "path": pre_path,
                    "sha256": pre_sha,
                },
                {
                    "operation": "write_flash",
                    "offset": "0x20000",
                    "size": len(b"candidate-app"),
                    "path": image_path,
                    "sha256": image_sha,
                },
                {
                    "operation": "read_flash",
                    "offset": "0x20000",
                    "size": len(b"candidate-app"),
                    "path": post_path,
                    "sha256": image_sha,
                },
            ],
        },
        "nvsPreservation": {
            "algorithm": "sha256",
            "beforePath": nvs_before,
            "beforeSha256": nvs_sha,
            "afterPath": nvs_after,
            "afterSha256": nvs_sha,
        },
        "evidencePaths": {
            "appImage": image_path,
            "appBefore": pre_path,
            "appAfter": post_path,
            "nvsBefore": nvs_before,
            "nvsAfter": nvs_after,
            "materializerReceipt": receipt_path,
        },
    }


def _compose(document):
    backend = document["repositories"]["backend"]["path"]
    return {
        "name": document["composeProject"],
        "services": {
            "backend": {
                "image": document["backendImage"],
                "ports": [{"host_ip": "127.0.0.1", "target": 3000, "published": "3000"}],
                "environment": {
                    "ROBOT_ESP_BASE_URL": document["localStack"]["espHttpUrl"],
                    "LESSON_ASSET_ORIGIN_BASE": document["localStack"]["assetOrigin"],
                    "LESSON_ROLLOUT_DEVICE_ALLOWLIST": "14:c1:9f:d1:ac:20",
                    "COURSE_MODE_LOCAL_PHYSICAL_TFT_ENABLED": "true",
                },
            },
            "course-mode-materialize": {
                "image": document["backendImage"],
                "command": ["dist/lessons/course-mode/course-mode-local-materializer.js", "materialize"],
                "environment": {
                    "COURSE_MODE_DEVICE_MAC": "14:c1:9f:d1:ac:20",
                    "COURSE_MODE_LOCAL_COMPOSE_ENABLED": "true",
                    "COURSE_MODE_LOCAL_PHYSICAL_TFT_ENABLED": "true",
                    "DATABASE_URL": "postgresql://tbot:tbot@postgres:5432/tbot",
                },
                "volumes": [
                    {
                        "type": "bind",
                        "source": f"{backend}/src/lessons/fixtures/course-mode",
                        "target": "/course-mode-fixtures",
                        "read_only": True,
                    }
                ],
            },
            "postgres": {},
            "redis": {},
            "mysql": {},
            "web": {},
        },
    }


def _fake_commands(tmp_path, document):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "commands.jsonl"
    repos = {value["path"]: value for value in document["repositories"].values()}
    git = fake_bin / "git"
    git.write_text(
        "#!/usr/bin/env python3\nimport json,sys\n"
        f"log={str(log)!r}; repos={repos!r}; required={FIRMWARE_SHA!r}\n"
        "open(log,'a').write(json.dumps(sys.argv[1:])+'\\n'); a=sys.argv[1:]; repo=repos[a[1]]\n"
        "if a[-2:]==['rev-parse','--show-toplevel']: print(a[1])\n"
        "elif a[-2:]==['rev-parse','HEAD']: print(repo['sha'])\n"
        "elif a[-3:]==['status','--porcelain','--untracked-files=all']:\n"
        "  [print(' M '+p) for p in repo['dirtyAllowlist']]\n"
        "elif a[-4:-1]==['merge-base','--is-ancestor',required]: pass\n"
        "else: raise SystemExit(93)\n"
    )
    image = [
        {
            "Id": "sha256:" + "b" * 64,
            "RepoTags": [document["backendImage"]],
            "Config": {
                "Labels": {
                    "com.tbot.course-mode.materializer-path": "/app/dist/lessons/course-mode/course-mode-local-materializer.js",
                    "org.opencontainers.image.revision": BACKEND_SHA,
                    "com.tbot.course-mode.build-source": "reviewed-clean-git-worktree",
                }
            },
        }
    ]
    docker = fake_bin / "docker"
    docker.write_text(
        "#!/usr/bin/env python3\nimport json,sys\n"
        f"log={str(log)!r}; image={image!r}; compose={_compose(document)!r}\n"
        "open(log,'a').write(json.dumps(sys.argv[1:])+'\\n'); a=sys.argv[1:]\n"
        "if a[:2]==['image','inspect']: print(json.dumps(image))\n"
        "elif a[0]=='compose' and a[-3:]==['config','--format','json']: print(json.dumps(compose))\n"
        "else: raise SystemExit(94)\n"
    )
    git.chmod(0o755)
    docker.chmod(0o755)
    return fake_bin, log


def test_canonical_preflight_is_deterministic_and_read_only(tmp_path, session_dir):
    document = valid_input(session_dir, tmp_path)
    fake_bin, log = _fake_commands(tmp_path, document)
    source = session_dir / "preflight-input.json"
    output = session_dir / "preflight-result.json"
    source.write_text(json.dumps(document))
    env = {**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}", "PRIVATE_SECRET": "must-not-leak"}
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--input", str(source), "--output", str(output)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload == json.loads(output.read_text())
    assert payload["valid"] is True and payload["result"] == "PASS"
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    assert payload["inputChecksum"] == hashlib.sha256(canonical).hexdigest()
    assert payload["checkedInvariants"] == sorted(payload["checkedInvariants"])
    assert payload["robotMac"] == "14:c1:9f:d1:ac:20"
    assert "must-not-leak" not in result.stdout
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert sum(call[0] == "-C" for call in calls) == 10
    assert [call[:2] for call in calls[-2:]] == [["image", "inspect"], ["compose", "--project-name"]]


def test_identity_assignment_receipt_and_repository_mutations_fail_closed(tmp_path, session_dir):
    sys.path.insert(0, str(SERVER / "scripts"))
    from course_mode_physical_tft_preflight import validate_input

    base = valid_input(session_dir, tmp_path)
    cases = [
        (lambda d: d["course"].update(courseId="wrong"), "course.identity"),
        (lambda d: d["lesson"].update(replacementId="placeholder"), "lesson.replacement_identity"),
        (lambda d: d["lesson"].update(mappingState="READY"), "lesson.mapping"),
        (lambda d: d["lesson"].update(sourceAssignable=True), "lesson.source"),
        (lambda d: d["lesson"].update(activeReplacementCount=2), "lesson.ambiguity"),
        (lambda d: d["assignments"].append(deepcopy(d["assignments"][0])), "assignment.cardinality"),
        (lambda d: d["assignments"][0].update(lessonVersion=3), "assignment.identity"),
        (lambda d: d["robots"].append({"mac": "14:c1:9f:d1:ac:21", "state": "ACTIVE"}), "robot.cardinality"),
        (lambda d: d["robots"][0].update(mac="14:c1:9f:d1:ac:21"), "robot.identity"),
        (lambda d: d["materializerReceipt"].update(lessonId=SOURCE_ID), "receipt.identity"),
        (lambda d: d["lesson"].update(contractVersion="courseCompanion.v3.contract.v1"), "lesson.contract"),
        (lambda d: d["lesson"].update(rendererId="teebot-lesson-renderer.v4"), "lesson.contract"),
        (lambda d: d["lesson"].update(backendSha="f" * 40), "lesson.backend_binding"),
        (lambda d: d["repositories"]["firmware"].update(sha="f" * 40), "repository.firmware.sha"),
        (lambda d: d.update(firmwareRequiredAncestor="f" * 40), "repository.firmware.ancestor"),
        (
            lambda d: d["repositories"]["backend"].update(dirtyAllowlist=["src/private.ts"]),
            "repository.backend.dirty_allowlist",
        ),
        (
            lambda d: d["repositories"]["esp"].update(dirtyAllowlist=["main/application.cc"]),
            "repository.esp.dirty_allowlist",
        ),
        (
            lambda d: d["repositories"]["firmware"].update(dirtyAllowlist=["main/application.cc"]),
            "repository.firmware.dirty_allowlist",
        ),
    ]
    for mutate, reason in cases:
        value = deepcopy(base)
        mutate(value)
        assert reason in validate_input(value, repository_root=ROOT)


def test_visual_pack_rejects_layer_checksum_metadata_and_three_video_paths(tmp_path, session_dir):
    sys.path.insert(0, str(SERVER / "scripts"))
    from course_mode_physical_tft_preflight import validate_input

    base = valid_input(session_dir, tmp_path)
    cases = [
        (lambda d: d["visualPack"].update(state="DRAFT"), "visual_pack.state"),
        (lambda d: d["visualPack"].update(manifestChecksum="c" * 64), "visual_pack.identity"),
        (
            lambda d: d["visualPack"].update(phaseAuthorityChecksum=hashlib.sha256(b"wrong").hexdigest()),
            "visual_pack.phase_checksum",
        ),
        (lambda d: d["visualPack"]["phases"][0].update(activityIds=[]), "visual_pack.phase_authority"),
        (lambda d: d["visualPack"]["phases"][0]["layers"].pop(0), "visual_pack.background"),
        (
            lambda d: d["visualPack"]["phases"][0]["layers"].append(
                deepcopy(d["visualPack"]["phases"][0]["layers"][-1])
            ),
            "visual_pack.robot_video_count",
        ),
        (
            lambda d: d["visualPack"]["phases"][0]["layers"][1].update(mediaType="video/mp4"),
            "visual_pack.robot_video_count",
        ),
        (lambda d: d["visualPack"]["phases"][0]["layers"][0].update(sha256="short"), "visual_pack.layer"),
        (lambda d: d["visualPack"]["phases"][0]["layers"][0].update(storagePath="/absolute"), "visual_pack.layer"),
        (lambda d: d["visualPack"]["phases"][0]["layers"][0].update(width=0), "visual_pack.layer"),
        (
            lambda d: d["visualPack"]["phases"][0]["layers"][-1]["compatibilityMetadata"].update(codec="h264"),
            "visual_pack.compatibility",
        ),
    ]
    for mutate, reason in cases:
        value = deepcopy(base)
        mutate(value)
        assert reason in validate_input(value, repository_root=ROOT)


def test_flash_nvs_path_boundary_and_local_guard_mutations_fail(tmp_path, session_dir):
    sys.path.insert(0, str(SERVER / "scripts"))
    from course_mode_physical_tft_preflight import validate_input

    base = valid_input(session_dir, tmp_path)
    outside, outside_sha = _evidence(tmp_path, "outside.bin", b"outside")
    cases = [
        (lambda d: d["flashPlan"].update(appOffset="0x10000"), "flash.offset"),
        (lambda d: d["flashPlan"]["operations"][1].update(offset="0x8000"), "flash.operations"),
        (
            lambda d: d["flashPlan"]["operations"].append(
                {"operation": "write_flash", "offset": "0x9000", "size": 1, "path": outside, "sha256": outside_sha}
            ),
            "flash.operations",
        ),
        (lambda d: d["flashPlan"].update(imageSha256="c" * 64), "flash.image"),
        (lambda d: d["flashPlan"]["operations"][2].update(sha256="d" * 64), "flash.readback"),
        (lambda d: d["nvsPreservation"].update(algorithm="SHA-1"), "nvs.algorithm"),
        (
            lambda d: d["nvsPreservation"].update(afterSha256=hashlib.sha256(b"different-nvs").hexdigest()),
            "nvs.equality",
        ),
        (lambda d: d["nvsPreservation"].update(beforeSha256="0" * 64), "nvs.hash"),
        (lambda d: d["evidencePaths"].update(appBefore=outside), "path.boundary"),
        (lambda d: d.update(outputDirectory=str(tmp_path)), "path.output"),
        (
            lambda d: (d.update(sessionDirectory=str(ARTIFACT_ROOT)), d.update(outputDirectory=str(ARTIFACT_ROOT))),
            "path.session",
        ),
        (lambda d: d["localStack"].update(environment="production"), "local_stack.production_guard"),
        (lambda d: d["localStack"].update(productionMutationAllowed=True), "local_stack.production_guard"),
        (lambda d: d["localStack"].update(assetOrigin="https://prod.example/"), "local_stack.endpoints"),
    ]
    for mutate, reason in cases:
        value = deepcopy(base)
        mutate(value)
        assert reason in validate_input(value, repository_root=ROOT)
    link = session_dir / "linked.bin"
    link.symlink_to(Path(outside))
    value = deepcopy(base)
    value["evidencePaths"]["appBefore"] = str(link)
    assert "path.symlink" in validate_input(value, repository_root=ROOT)


def test_image_compose_and_git_runtime_checks_fail_closed(tmp_path, session_dir):
    sys.path.insert(0, str(SERVER / "scripts"))
    from course_mode_physical_tft_preflight import validate_compose, validate_image

    base = valid_input(session_dir, tmp_path)
    image = [
        {
            "Id": "sha256:" + "b" * 64,
            "RepoTags": [base["backendImage"]],
            "Config": {
                "Labels": {
                    "com.tbot.course-mode.materializer-path": "/app/dist/lessons/course-mode/course-mode-local-materializer.js",
                    "org.opencontainers.image.revision": BACKEND_SHA,
                    "com.tbot.course-mode.build-source": "reviewed-clean-git-worktree",
                }
            },
        }
    ]
    assert validate_image(image, base["backendImage"], BACKEND_SHA) == []
    for mutate, reason in [
        (lambda d: d[0].update(RepoTags=["local/tbot-backend:latest"]), "image.reference"),
        (
            lambda d: d[0]["Config"]["Labels"].update(**{"org.opencontainers.image.revision": "f" * 40}),
            "image.revision",
        ),
        (lambda d: d[0]["Config"]["Labels"].pop("com.tbot.course-mode.materializer-path"), "image.materializer"),
    ]:
        value = deepcopy(image)
        mutate(value)
        assert reason in validate_image(value, base["backendImage"], BACKEND_SHA)
    compose = _compose(base)
    for mutate, reason in [
        (lambda d: d.update(name="production"), "compose.schema"),
        (lambda d: d["services"]["backend"].update(image="latest"), "compose.image"),
        (
            lambda d: d["services"]["backend"].update(
                ports=[{"host_ip": "0.0.0.0", "target": 3000, "published": "3000"}]
            ),
            "compose.port",
        ),
        (
            lambda d: d["services"]["backend"]["environment"].update(
                LESSON_ROLLOUT_DEVICE_ALLOWLIST="14:c1:9f:d1:ac:21"
            ),
            "compose.robot",
        ),
        (lambda d: d["services"]["course-mode-materialize"].update(command=["wrong"]), "compose.materializer"),
        (lambda d: d["services"]["course-mode-materialize"]["volumes"][0].update(read_only=False), "compose.mount"),
        (
            lambda d: d["services"]["web"].update(environment={"URL": "https://production.example"}),
            "compose.production",
        ),
    ]:
        value = deepcopy(compose)
        mutate(value)
        assert reason in validate_compose(value, base)


def test_malformed_input_is_stable_json_and_never_echoes_secrets(session_dir):
    source = session_dir / "input.json"
    source.write_text('{"token":"private-value"')
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--input", str(source), "--output", str(session_dir / "out.json")],
        env={"PATH": ""},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert json.loads(result.stdout) == {"reasons": ["input.invalid_json"], "valid": False}
    assert "private-value" not in result.stdout
