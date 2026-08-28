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
ESP_SHA = "a7c2e64f1eda21e404ede7873d89fe79c5aed210"
FIRMWARE_SHA = "3b13883b6e8a8f6495c5670ebdc194392e38de75"
CONTRACT_SHA = "52303f656b6b21e4a65fc1a7179f7668888a7682c2e86b6d1319f201a548c840"
MANIFEST_SHA = hashlib.sha256(b"canonical-w1-manifest").hexdigest()
STEPS_SHA = hashlib.sha256(b"canonical-w1-steps").hexdigest()
SOURCE_ID = "1453f9e8-a326-4cba-9e8b-d4b186c5cb53"
REPLACEMENT_ID = "2fe871e4-bf3d-43a8-91f4-63e755b4a12c"
ASSIGNMENT_ID = "38b6deaf-8075-42e6-b827-0ca54dcc8f54"
VOICE_PATH = "main/tbot-server/tests/test_lesson_voice_output_discipline.py"
VOICE_SHA = "08f77b5452301224b17b4b333d2d032fff40c06aa2eaea97fa90932dae7d97e3"
APP_OFFSET = 0x20000
APP_PARTITION_SIZE = 0x7E0000


@pytest.fixture
def session_dir():
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    path = Path(tempfile.mkdtemp(prefix="pytest-task12-review-", dir=ARTIFACT_ROOT))
    yield path
    shutil.rmtree(path)


def _write(path: Path, payload: bytes) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return {"path": str(path), "algorithm": "sha256", "sha256": hashlib.sha256(payload).hexdigest()}


def _write_json(path: Path, payload: dict) -> dict:
    encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
    return _write(path, encoded)


def _layer(session: Path, slot: str, media_type: str, index: int) -> dict:
    payload = f"asset-{slot}-{index}".encode()
    ref = _write(session / "assets" / f"{slot}-{index}.bin", payload)
    if media_type == "video/mp4":
        compatibility = {
            "mediaKind": "video",
            "mediaType": media_type,
            "codec": "mjpeg",
            "fps": 10,
            "durationMs": 3000,
            "frameCount": 30,
            "hasAudio": False,
            "chromaKey": {"keyColor": "#00ff00", "tolerance": 20, "featherPx": 1},
            "rect": {"x": 118, "y": 160, "width": 150, "height": 150},
        }
    else:
        width = 480 if slot == "backgroundScene" else 150
        height = 320 if slot == "backgroundScene" else 150
        compatibility = {
            "mediaKind": "image",
            "mediaType": media_type,
            "fit": "cover" if slot == "backgroundScene" else "contain",
            "rect": {"x": 0, "y": 0, "width": width, "height": height},
        }
    return {
        "slot": slot,
        "assetKey": f"w01.{slot}.{index}",
        "assetVersionId": f"4f75e40{index}-2fea-4dc4-9c87-73c3522fa12{index}",
        "version": 1,
        "state": "READY",
        "publicationState": "published",
        "immutable": True,
        "storagePath": f"published/w01/{slot}-{index}",
        "assetPath": ref["path"],
        "sha256": ref["sha256"],
        "bytes": len(payload),
        "mediaType": media_type,
        "width": 480 if slot == "backgroundScene" else 150,
        "height": 320 if slot == "backgroundScene" else 150,
        "compatibilityMetadata": compatibility,
    }


def valid_input(session: Path, tmp_path: Path) -> dict:
    repos = {}
    for name, sha in (("backend", BACKEND_SHA), ("esp", ESP_SHA), ("firmware", FIRMWARE_SHA)):
        path = tmp_path / name
        path.mkdir()
        exceptions = []
        if name == "esp":
            protected = path / VOICE_PATH
            protected.parent.mkdir(parents=True)
            protected.write_bytes((ROOT / VOICE_PATH).read_bytes())
            exceptions = [{"path": VOICE_PATH, "sha256": hashlib.sha256(protected.read_bytes()).hexdigest()}]
        repos[name] = {"path": str(path), "sha": sha, "dirtyExceptions": exceptions}

    phases = [
        {
            "phaseId": "teach",
            "activityIds": ["w01-a1"],
            "templateId": "layeredCinematic",
            "templateVersion": 1,
            "playbackMode": "once",
            "layers": [
                _layer(session, "backgroundScene", "image/jpeg", 1),
                _layer(session, "teachingObject", "image/png", 2),
                _layer(session, "robotOverlay", "video/mp4", 3),
            ],
        },
        {
            "phaseId": "listen",
            "activityIds": ["w01-a2"],
            "templateId": "layeredCinematic",
            "templateVersion": 1,
            "playbackMode": "hold",
            "layers": [
                _layer(session, "backgroundScene", "image/jpeg", 4),
                _layer(session, "robotOverlay", "video/mp4", 5),
            ],
        },
    ]
    pack_projection = {"phases": phases}
    pack_sha = hashlib.sha256(json.dumps(pack_projection, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    materialization = {
        "schemaVersion": 1,
        "mode": "materialize",
        "result": "pass",
        "course": {"courseId": "a17792f6-8d86-4ad1-a6f3-77663b4d4674", "courseKey": "english-6month-4-6"},
        "source": {
            "lessonId": SOURCE_ID,
            "lessonKey": "w01-greetings-politeness",
            "lessonVersion": 1,
            "status": "ARCHIVED",
            "assignable": False,
        },
        "replacement": {
            "lessonId": REPLACEMENT_ID,
            "lessonKey": "w01-greetings-politeness",
            "lessonVersion": 2,
            "status": "PUBLISHED",
            "assignable": True,
            "mappingState": "ACTIVE",
            "activeReplacementCount": 1,
            "contractVersion": "courseCompanion.v2.contract.v1",
            "rendererId": "teebot-lesson-renderer.v5",
            "visualLayoutContract": "renderer-v5.layered-cinematic-layout.v1",
            "contractChecksum": CONTRACT_SHA,
            "manifestChecksum": MANIFEST_SHA,
            "stepsChecksum": STEPS_SHA,
            "visualPackChecksum": pack_sha,
        },
        "artifactChecksum": "pending",
    }
    materialization["artifactChecksum"] = hashlib.sha256(
        json.dumps(
            {key: materialization[key] for key in ("course", "source", "replacement")},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    materialization_ref = _write_json(session / "materialization-receipt.json", materialization)
    assignment_body = [
        {
            "assignmentId": ASSIGNMENT_ID,
            "state": "ACTIVE",
            "lessonId": REPLACEMENT_ID,
            "lessonVersion": 2,
            "robotMac": "14:c1:9f:d1:ac:20",
        }
    ]
    assignment_snapshot = {
        "schemaVersion": 1,
        "result": "pass",
        "robotMac": "14:c1:9f:d1:ac:20",
        "assignments": assignment_body,
        "snapshotChecksum": hashlib.sha256(
            json.dumps(assignment_body, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    }
    assignment_ref = _write_json(session / "assignment-snapshot.json", assignment_snapshot)
    partitions = [
        {"name": "bootloader", "offset": "0x0", "size": "0x8000", "end": "0x8000", "protected": True},
        {"name": "partition-table", "offset": "0x8000", "size": "0x1000", "end": "0x9000", "protected": True},
        {"name": "nvs", "offset": "0x9000", "size": "0x4000", "end": "0xd000", "protected": True},
        {"name": "ota-data", "offset": "0xd000", "size": "0x2000", "end": "0xf000", "protected": True},
        {"name": "phy-init", "offset": "0xf000", "size": "0x1000", "end": "0x10000", "protected": True},
        {"name": "reserved", "offset": "0x10000", "size": "0x10000", "end": "0x20000", "protected": True},
        {"name": "application", "offset": "0x20000", "size": "0x7e0000", "end": "0x800000", "protected": False},
        {"name": "generated-assets", "offset": "0x800000", "size": "0x800000", "end": "0x1000000", "protected": True},
    ]
    partition_ref = _write_json(
        session / "partition-snapshot.json",
        {
            "schemaVersion": 1,
            "flashSize": "0x1000000",
            "partitions": partitions,
            "snapshotChecksum": hashlib.sha256(
                json.dumps(partitions, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
        },
    )
    image = b"candidate-application-image"
    image_ref = _write(session / "candidate-app.bin", image)
    before = b"B" * APP_PARTITION_SIZE
    after = image + b"\xff" * (APP_PARTITION_SIZE - len(image))
    before_ref = _write(session / "app-before.bin", before)
    after_ref = _write(session / "app-after.bin", after)
    nvs_before = _write(session / "nvs-before.bin", b"preserved-nvs")
    nvs_after = _write(session / "nvs-after.bin", b"preserved-nvs")
    reviewed = materialization["replacement"]
    return {
        "schemaVersion": 3,
        "sessionDirectory": str(session),
        "sessionStartedAt": "2026-08-29T00:00:00Z",
        "outputDirectory": str(session),
        "localStack": {
            "environment": "local-attended-lab",
            "productionMutationAllowed": False,
            "databaseAuthority": "isolated-local-compose",
            "adminMutationAllowed": False,
            "backendBaseUrl": "http://127.0.0.1:3000",
            "adminBaseUrl": "http://127.0.0.1:3001",
            "espHttpUrl": "http://host.docker.internal:8003",
            "robotLanHost": "192.168.100.183",
            "assetOrigin": "http://192.168.100.183:8102/",
            "otaUrl": "http://192.168.100.183:8003/ota",
            "websocketUrl": "ws://192.168.100.183:8003/ws",
        },
        "repositories": repos,
        "firmwareRequiredAncestor": FIRMWARE_SHA,
        "backendImage": f"local/tbot-backend:course-mode-physical-tft-{BACKEND_SHA}",
        "composeProject": "tbot-course-mode-physical-tft",
        "reviewedIdentity": {
            "courseId": materialization["course"]["courseId"],
            "courseKey": materialization["course"]["courseKey"],
            "sourceLessonId": SOURCE_ID,
            "replacementId": REPLACEMENT_ID,
            "lessonKey": reviewed["lessonKey"],
            "replacementVersion": reviewed["lessonVersion"],
            "contractVersion": reviewed["contractVersion"],
            "rendererId": reviewed["rendererId"],
            "visualLayoutContract": reviewed["visualLayoutContract"],
            "contractChecksum": reviewed["contractChecksum"],
            "manifestChecksum": reviewed["manifestChecksum"],
            "stepsChecksum": reviewed["stepsChecksum"],
            "visualPackChecksum": pack_sha,
            "backendSha": BACKEND_SHA,
        },
        "materializationReceipt": materialization_ref,
        "assignmentSnapshot": assignment_ref,
        "visualPack": {
            "state": "READY",
            "publicationState": "published",
            "immutable": True,
            "lessonId": REPLACEMENT_ID,
            "lessonVersion": 2,
            "checksum": pack_sha,
            "phases": phases,
        },
        "flashPlan": {
            "partitionSnapshot": partition_ref,
            "appPartition": {"offset": "0x20000", "size": "0x7e0000", "end": "0x800000"},
            "image": {**image_ref, "size": len(image)},
            "operations": [
                {"operation": "read_flash", "offset": "0x20000", "size": APP_PARTITION_SIZE, **before_ref},
                {"operation": "write_flash", "offset": "0x20000", "size": len(image), **image_ref},
                {"operation": "read_flash", "offset": "0x20000", "size": APP_PARTITION_SIZE, **after_ref},
            ],
        },
        "nvsPreservation": {"before": nvs_before, "after": nvs_after},
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
        + f"log={str(log)!r}; repos={repos!r}; required={FIRMWARE_SHA!r}\n"
        + "open(log,'a').write(json.dumps(sys.argv[1:])+'\\n'); a=sys.argv[1:]; repo=repos[a[1]]\n"
        + "if a[-2:]==['rev-parse','--show-toplevel']: print(a[1])\n"
        + "elif a[-2:]==['rev-parse','HEAD']: print(repo['sha'])\n"
        + "elif a[-3:]==['status','--porcelain','--untracked-files=all']:\n  [print(' M '+x['path']) for x in repo['dirtyExceptions']]\n"
        + "elif a[-4:-1]==['merge-base','--is-ancestor',required]: pass\nelse: raise SystemExit(93)\n"
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
        + f"log={str(log)!r}; image={image!r}; compose={_compose(document)!r}\n"
        + "open(log,'a').write(json.dumps(sys.argv[1:])+'\\n'); a=sys.argv[1:]\n"
        + "if a[:2]==['image','inspect']: print(json.dumps(image))\nelif a[0]=='compose' and a[-3:]==['config','--format','json']: print(json.dumps(compose))\nelse: raise SystemExit(94)\n"
    )
    git.chmod(0o755)
    docker.chmod(0o755)
    return fake_bin, log


def _validate(document):
    sys.path.insert(0, str(SERVER / "scripts"))
    from course_mode_physical_tft_preflight import validate_input

    return validate_input(document, repository_root=ROOT)


def test_canonical_receipt_derived_preflight_passes_cli(tmp_path, session_dir):
    document = valid_input(session_dir, tmp_path)
    fake_bin, log = _fake_commands(tmp_path, document)
    source = session_dir / "input.json"
    output = session_dir / "result.json"
    source.write_text(json.dumps(document))
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--input", str(source), "--output", str(output)],
        cwd=ROOT,
        env={**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["replacementId"] == REPLACEMENT_ID and payload["valid"] is True
    assert (
        payload["inputChecksum"]
        == hashlib.sha256(json.dumps(document, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    )
    assert len(log.read_text().splitlines()) == 12


def test_receipts_are_parsed_strictly_and_are_authoritative(tmp_path, session_dir):
    base = valid_input(session_dir, tmp_path)
    drift = deepcopy(base)
    drift["reviewedIdentity"]["replacementId"] = "97b892e1-0f1e-42d5-bbc4-50465042e111"
    assignment = json.loads(Path(base["assignmentSnapshot"]["path"]).read_text())
    assignment["assignments"].append(deepcopy(assignment["assignments"][0]))
    duplicate = deepcopy(base)
    duplicate["assignmentSnapshot"] = _write_json(session_dir / "assignment-many.json", assignment)
    placeholder = deepcopy(base)
    placeholder["reviewedIdentity"]["replacementId"] = "00000000-0000-4000-8000-000000000000"
    cases = [
        (drift, "identity.receipt_parity"),
        (duplicate, "assignment.cardinality"),
        (placeholder, "identity.replacement"),
    ]
    for value, reason in cases:
        assert reason in _validate(value)
    missing = deepcopy(base)
    missing["materializationReceipt"] = _write_json(session_dir / "empty-materialization.json", {})
    assert "materialization.schema" in _validate(missing)
    ambiguous_body = json.loads(Path(base["materializationReceipt"]["path"]).read_text())
    ambiguous_body["replacement"]["activeReplacementCount"] = 2
    ambiguous = deepcopy(base)
    ambiguous["materializationReceipt"] = _write_json(session_dir / "ambiguous-materialization.json", ambiguous_body)
    assert "materialization.replacement" in _validate(ambiguous)


def test_duplicate_json_keys_fail_before_commands(session_dir):
    source = session_dir / "duplicate.json"
    source.write_text('{"schemaVersion":3,"schemaVersion":3}')
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--input", str(source), "--output", str(session_dir / "out.json")],
        env={"PATH": ""},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1 and json.loads(result.stdout)["reasons"] == ["input.duplicate_key"]


def test_nested_receipt_duplicate_keys_are_rejected(tmp_path, session_dir):
    document = valid_input(session_dir, tmp_path)
    payload = b'{"schemaVersion":1,"mode":"materialize","mode":"dry-run"}'
    document["materializationReceipt"] = _write(session_dir / "duplicate-receipt.json", payload)
    assert "materialization.file.duplicate_key" in _validate(document)


def test_flash_partition_and_file_boundaries_fail_closed(tmp_path, session_dir):
    base = valid_input(session_dir, tmp_path)
    cases = [
        (lambda d: d["flashPlan"].update(eraseFlash=True), "flash.schema"),
        (lambda d: d["flashPlan"]["operations"].append({"operation": "erase_flash"}), "flash.operations"),
        (lambda d: d["flashPlan"]["image"].update(size=APP_PARTITION_SIZE + 1), "flash.image_range"),
        (lambda d: d["flashPlan"]["operations"][1].update(offset="0x7ffff0", size=64), "flash.write_range"),
        (lambda d: d["flashPlan"]["operations"][0].update(size=7), "flash.readback_range"),
        (lambda d: d["flashPlan"]["operations"][2].update(size=7), "flash.readback_range"),
        (lambda d: d["flashPlan"]["operations"][0].update(size=APP_PARTITION_SIZE - 1), "flash.file_size"),
    ]
    for mutate, reason in cases:
        value = deepcopy(base)
        mutate(value)
        assert reason in _validate(value)
    target = Path(base["visualPack"]["phases"][0]["layers"][0]["assetPath"])
    link = session_dir / "asset-link.bin"
    link.symlink_to(target)
    linked = deepcopy(base)
    linked["visualPack"]["phases"][0]["layers"][0]["assetPath"] = str(link)
    assert "visual_pack.asset_path" in _validate(linked)


def test_dirty_exceptions_bind_path_and_content_hash(tmp_path, session_dir):
    base = valid_input(session_dir, tmp_path)
    changed = deepcopy(base)
    changed["repositories"]["esp"]["dirtyExceptions"][0]["sha256"] = hashlib.sha256(b"wrong").hexdigest()
    assert "repository.esp.dirty_exception_hash" in _validate(changed)
    renamed = deepcopy(base)
    renamed["repositories"]["esp"]["dirtyExceptions"][0]["path"] = "main/application.cc"
    assert "repository.esp.dirty_exceptions" in _validate(renamed)


def test_layers_require_exact_schema_unique_real_files_and_one_robot_video(tmp_path, session_dir):
    base = valid_input(session_dir, tmp_path)
    cases = [
        (lambda d: d["visualPack"]["phases"][0]["layers"][0].update(extra=True), "visual_pack.layer_schema"),
        (
            lambda d: d["visualPack"]["phases"][0]["layers"][0].update(assetPath="relative.bin"),
            "visual_pack.asset_path",
        ),
        (
            lambda d: d["visualPack"]["phases"][0]["layers"].append(
                deepcopy(d["visualPack"]["phases"][0]["layers"][-1])
            ),
            "visual_pack.layer_unique",
        ),
        (lambda d: d["visualPack"]["phases"][0]["layers"][1].update(mediaType="video/mp4"), "visual_pack.robot_video"),
        (
            lambda d: d["visualPack"]["phases"][0]["layers"].append(
                _layer(session_dir, "effectOverlay", "image/png", 6)
            ),
            "visual_pack.layer_slot",
        ),
    ]
    for mutate, reason in cases:
        value = deepcopy(base)
        mutate(value)
        assert reason in _validate(value)


def test_endpoint_edges_and_nested_wrong_types_never_traceback(tmp_path, session_dir):
    base = valid_input(session_dir, tmp_path)
    cases = [
        (lambda d: d["localStack"].update(assetOrigin="http://0.0.0.0:8102/"), "local_stack.endpoints"),
        (lambda d: d["localStack"].update(assetOrigin="http://224.0.0.1:8102/"), "local_stack.endpoints"),
        (lambda d: d["localStack"].update(assetOrigin="http://240.0.0.1:8102/"), "local_stack.endpoints"),
        (lambda d: d["localStack"].update(assetOrigin="http://[::]:8102/"), "local_stack.endpoints"),
        (lambda d: d.update(sessionDirectory=None), "path.session"),
        (lambda d: d["visualPack"]["phases"][0]["layers"][0].update(mediaType=[]), "visual_pack.layer"),
        (lambda d: d["reviewedIdentity"].update(replacementVersion={}), "identity.replacement"),
    ]
    for mutate, reason in cases:
        value = deepcopy(base)
        mutate(value)
        assert reason in _validate(value)
    assert "input.schema" in _validate({})


def test_wrong_type_cli_inputs_always_emit_stable_json(tmp_path, session_dir):
    base = valid_input(session_dir, tmp_path)
    values = [{}, {**base, "sessionDirectory": None}, {**base, "materializationReceipt": 7}]
    media = deepcopy(base)
    media["visualPack"]["phases"][0]["layers"][0]["mediaType"] = []
    values.append(media)
    for index, value in enumerate(values):
        source = session_dir / f"wrong-{index}.json"
        source.write_text(json.dumps(value))
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--input",
                str(source),
                "--output",
                str(session_dir / f"wrong-{index}-out.json"),
            ],
            env={"PATH": ""},
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        assert json.loads(result.stdout)["valid"] is False
        assert "Traceback" not in result.stderr


def test_compose_non_mapping_and_malformed_secret_input_are_stable(tmp_path, session_dir):
    sys.path.insert(0, str(SERVER / "scripts"))
    from course_mode_physical_tft_preflight import validate_compose

    base = valid_input(session_dir, tmp_path)
    compose = _compose(base)
    compose["services"]["backend"] = []
    assert "compose.backend" in validate_compose(compose, base)
    source = session_dir / "bad.json"
    source.write_text('{"token":"private"')
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--input", str(source), "--output", str(session_dir / "bad-out.json")],
        env={"PATH": ""},
        capture_output=True,
        text=True,
    )
    assert json.loads(result.stdout) == {"reasons": ["input.invalid_json"], "valid": False}
    assert "private" not in result.stdout
