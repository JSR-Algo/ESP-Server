import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from copy import deepcopy
from pathlib import Path

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

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
NVS_OFFSET = 0x9000
NVS_SIZE = 0x4000
SESSION_ID = "a23be10d-1f38-4c14-b563-bcc821cb809e"
EXPECTED_IDENTITIES = {}
EXPECTED_SIGNATURES = {}
EXPECTED_PUBLIC_KEYS = {}
EXPECTED_FINGERPRINTS = {}


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
        "storagePath": ref["path"],
        "assetPath": ref["path"],
        "sha256": ref["sha256"],
        "bytes": len(payload),
        "mediaType": media_type,
        "width": 480 if slot == "backgroundScene" else 150,
        "height": 320 if slot == "backgroundScene" else 150,
        "compatibilityMetadata": compatibility,
    }


def _create_trusted_tools(tmp_path: Path) -> tuple[Path, Path]:
    tool_dir = tmp_path / "trusted-tools"
    tool_dir.mkdir()
    config = tool_dir / "config.json"
    git = tool_dir / "git"
    docker = tool_dir / "docker"
    git.write_text(
        f"#!{sys.executable}\nimport json,sys\n"
        + f"c=json.load(open({str(config)!r})); open(c['log'],'a').write(json.dumps(sys.argv[1:])+'\\n'); "
        + "a=sys.argv[1:]; root=a[a.index('-C')+1]; repo=c['repos'][root]\n"
        + "if a[-2:]==['rev-parse','--show-toplevel']: print(root)\n"
        + "elif a[-2:]==['rev-parse','HEAD']: print(repo['sha'])\n"
        + "elif a[-3:]==['status','--porcelain','--untracked-files=all']:\n  [print(' M '+x['path']) for x in repo['dirtyExceptions']]\n"
        + "elif a[-4:-1]==['merge-base','--is-ancestor',c['required']]: pass\nelse: raise SystemExit(93)\n"
    )
    docker.write_text(
        f"#!{sys.executable}\nimport json,sys\n"
        + f"c=json.load(open({str(config)!r})); open(c['log'],'a').write(json.dumps(sys.argv[1:])+'\\n'); a=sys.argv[1:]\n"
        + "if a[:2]==['image','inspect']: print(json.dumps(c['image']))\n"
        + "elif a[0]=='compose' and a[-3:]==['config','--format','json']: print(json.dumps(c['compose']))\n"
        + "else: raise SystemExit(94)\n"
    )
    git.chmod(0o755)
    docker.chmod(0o755)
    return git, docker


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
    trusted_git, trusted_docker = _create_trusted_tools(tmp_path)

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
    expected_identity = {
        "schemaVersion": 1,
        "courseId": "a17792f6-8d86-4ad1-a6f3-77663b4d4674",
        "courseKey": "english-6month-4-6",
        "sourceLessonId": SOURCE_ID,
        "sourceLessonVersion": 1,
        "replacementId": REPLACEMENT_ID,
        "replacementVersion": 2,
        "lessonKey": "w01-greetings-politeness",
        "contractVersion": "courseCompanion.v2.contract.v1",
        "rendererId": "teebot-lesson-renderer.v5",
        "visualLayoutContract": "renderer-v5.layered-cinematic-layout.v1",
        "contractChecksum": CONTRACT_SHA,
        "manifestChecksum": MANIFEST_SHA,
        "stepsChecksum": STEPS_SHA,
        "visualPackChecksum": pack_sha,
        "backendGitSha": BACKEND_SHA,
        "backendWorktree": repos["backend"]["path"],
        "espGitSha": ESP_SHA,
        "firmwareGitSha": FIRMWARE_SHA,
        "appSha256": image_ref["sha256"],
        "appSize": len(image),
        "robotMac": "14:c1:9f:d1:ac:20",
        "sessionId": SESSION_ID,
        "materializedPackRoot": str(session / "assets"),
        "partitionTableSha256": hashlib.sha256(
            json.dumps(partitions, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "buildToolVersion": "5.3.2",
        "buildConfigSha256": hashlib.sha256(b"sdkconfig").hexdigest(),
        "materializerVersion": "course-mode-curriculum.v2",
        "backendImageId": "sha256:" + "b" * 64,
        "tools": {
            "git": {"path": str(trusted_git), "sha256": hashlib.sha256(trusted_git.read_bytes()).hexdigest()},
            "docker": {
                "path": str(trusted_docker),
                "sha256": hashlib.sha256(trusted_docker.read_bytes()).hexdigest(),
            },
        },
    }
    expected_ref = _write_json(tmp_path / "expected-physical-identity.json", expected_identity)
    EXPECTED_IDENTITIES[str(session)] = (expected_identity, expected_ref)
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    public_raw = public_key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    fingerprint = hashlib.sha256(public_raw).hexdigest()
    canonical_identity = json.dumps(
        expected_identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()
    signature_path = tmp_path / "expected-physical-identity.sig"
    signature_path.write_bytes(private_key.sign(canonical_identity))

    EXPECTED_SIGNATURES[str(session)] = signature_path
    EXPECTED_PUBLIC_KEYS[str(session)] = public_raw
    EXPECTED_FINGERPRINTS[str(session)] = fingerprint
    materialization.update(
        {
            "backendGitSha": BACKEND_SHA,
            "backendWorktree": repos["backend"]["path"],
            "lifecycleMode": "authorized-local-materialize",
            "materializerVersion": "course-mode-curriculum.v2",
            "timestamp": "2026-08-29T00:00:01Z",
            "sessionId": SESSION_ID,
        }
    )
    materialization["authorization"] = {
        "algorithm": "sha256",
        "expectedIdentitySha256": expected_ref["sha256"],
        "integrityDigest": hashlib.sha256(
            json.dumps(materialization, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    }
    materialization_ref = _write_json(session / "materialization-receipt.json", materialization)
    firmware_receipt = {
        "schemaVersion": 1,
        "result": "pass",
        "firmwareGitSha": FIRMWARE_SHA,
        "buildTool": "esp-idf",
        "buildToolVersion": "5.3.2",
        "buildConfigSha256": hashlib.sha256(b"sdkconfig").hexdigest(),
        "app": {
            "path": image_ref["path"],
            "offset": "0x20000",
            "maxPartitionSize": APP_PARTITION_SIZE,
            "size": len(image),
            "sha256": image_ref["sha256"],
        },
        "expectedIdentitySha256": expected_ref["sha256"],
    }
    firmware_receipt["integrityDigest"] = hashlib.sha256(
        json.dumps(firmware_receipt, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    firmware_ref = _write_json(session / "firmware-build-receipt.json", firmware_receipt)
    before = b"B" * APP_PARTITION_SIZE
    after = image + b"\xff" * (APP_PARTITION_SIZE - len(image))
    before_ref = _write(session / "app-before.bin", before)
    after_ref = _write(session / "app-after.bin", after)
    nvs_before = _write(session / "nvs-before.bin", b"N" * NVS_SIZE)
    nvs_after = _write(session / "nvs-after.bin", b"N" * NVS_SIZE)
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
        "firmwareBuildReceipt": firmware_ref,
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
        "nvsPreservation": {
            "partition": {"offset": "0x9000", "size": "0x4000", "end": "0xd000"},
            "before": nvs_before,
            "after": nvs_after,
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
    config = tmp_path / "trusted-tools" / "config.json"
    config.write_text(
        json.dumps(
            {"log": str(log), "repos": repos, "required": FIRMWARE_SHA, "image": image, "compose": _compose(document)}
        )
    )
    return fake_bin, log


def _validate(document):
    sys.path.insert(0, str(SERVER / "scripts"))
    from course_mode_physical_tft_preflight import validate_input

    repositories = document.get("repositories") if isinstance(document, dict) else None
    backend_value = repositories.get("backend") if isinstance(repositories, dict) else None
    backend = backend_value if isinstance(backend_value, dict) else {}
    match = next(
        (item for item in EXPECTED_IDENTITIES.values() if item[0]["backendWorktree"] == backend.get("path")),
        next(reversed(EXPECTED_IDENTITIES.values())),
    )
    identity, ref = match
    return validate_input(
        document,
        repository_root=ROOT,
        expected_identity=identity,
        expected_identity_sha256=ref["sha256"],
    )


def _validate_with_identity(document, identity, identity_sha):
    sys.path.insert(0, str(SERVER / "scripts"))
    from course_mode_physical_tft_preflight import validate_input

    return validate_input(
        document,
        repository_root=ROOT,
        expected_identity=identity,
        expected_identity_sha256=identity_sha,
    )


def _rewrite_materialization(document, mutate):
    body = json.loads(Path(document["materializationReceipt"]["path"]).read_text())
    mutate(body)
    body["artifactChecksum"] = hashlib.sha256(
        json.dumps(
            {key: body[key] for key in ("course", "source", "replacement")},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    unsigned = {key: value for key, value in body.items() if key != "authorization"}
    body["authorization"]["integrityDigest"] = hashlib.sha256(
        json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    document["materializationReceipt"] = _write_json(
        Path(document["sessionDirectory"]) / "rewritten-materialization.json", body
    )


def test_canonical_receipt_derived_preflight_passes_cli(tmp_path, session_dir, monkeypatch, capsys):
    document = valid_input(session_dir, tmp_path)
    fake_bin, log = _fake_commands(tmp_path, document)
    shadow_marker = tmp_path / "path-shadow-invoked"
    for name in ("git", "docker"):
        shadow = fake_bin / name
        shadow.write_text(f"#!/bin/sh\necho invoked >> {shadow_marker}\nexit 99\n")
        shadow.chmod(0o755)
    source = session_dir / "input.json"
    output = session_dir / "result.json"
    source.write_text(json.dumps(document))
    _, expected_ref = EXPECTED_IDENTITIES[str(session_dir)]
    signature = EXPECTED_SIGNATURES[str(session_dir)]
    sys.path.insert(0, str(SERVER / "scripts"))
    import course_mode_physical_tft_preflight as preflight

    monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ['PATH']}")
    monkeypatch.setattr(preflight, "PINNED_APPROVAL_PUBLIC_KEY_RAW", EXPECTED_PUBLIC_KEYS[str(session_dir)])
    monkeypatch.setattr(preflight, "PINNED_APPROVAL_KEY_FINGERPRINT", EXPECTED_FINGERPRINTS[str(session_dir)])
    result = preflight.main(
        [
            "--input",
            str(source),
            "--output",
            str(output),
            "--expected-identity",
            expected_ref["path"],
            "--expected-identity-signature",
            str(signature),
        ]
    )
    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["replacementId"] == REPLACEMENT_ID and payload["valid"] is True
    assert payload["expectedIdentityApproval"] == "ed25519-signature-verified"
    assert payload["expectedIdentitySignerFingerprint"] != "unprovisioned"
    assert payload["backendImageId"] == "sha256:" + "b" * 64
    identity = EXPECTED_IDENTITIES[str(session_dir)][0]
    assert payload["gitToolSha256"] == identity["tools"]["git"]["sha256"]
    assert payload["dockerToolSha256"] == identity["tools"]["docker"]["sha256"]
    assert (
        payload["inputChecksum"]
        == hashlib.sha256(json.dumps(document, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    )
    assert len(log.read_text().splitlines()) == 12
    assert not shadow_marker.exists()


def test_changed_trusted_tool_bytes_fail_before_subprocess(tmp_path, session_dir, monkeypatch, capsys):
    document = valid_input(session_dir, tmp_path)
    _, log = _fake_commands(tmp_path, document)
    identity, expected_ref = EXPECTED_IDENTITIES[str(session_dir)]
    git_tool = Path(identity["tools"]["git"]["path"])
    git_tool.write_bytes(git_tool.read_bytes() + b"\n# changed\n")
    source = session_dir / "changed-tool-input.json"
    source.write_text(json.dumps(document))
    sys.path.insert(0, str(SERVER / "scripts"))
    import course_mode_physical_tft_preflight as preflight

    monkeypatch.setattr(preflight, "PINNED_APPROVAL_PUBLIC_KEY_RAW", EXPECTED_PUBLIC_KEYS[str(session_dir)])
    monkeypatch.setattr(preflight, "PINNED_APPROVAL_KEY_FINGERPRINT", EXPECTED_FINGERPRINTS[str(session_dir)])
    result = preflight.main(
        [
            "--input",
            str(source),
            "--output",
            str(session_dir / "changed-tool-output.json"),
            "--expected-identity",
            expected_ref["path"],
            "--expected-identity-signature",
            str(EXPECTED_SIGNATURES[str(session_dir)]),
        ]
    )
    assert result == 1
    assert "expected_identity.tools.git" in json.loads(capsys.readouterr().out)["reasons"]
    assert not log.exists()


def test_cli_requires_provisioned_signer_and_rejects_coherent_rewrite(tmp_path, session_dir):
    document = valid_input(session_dir, tmp_path)
    fake_bin, _ = _fake_commands(tmp_path, document)
    source = session_dir / "approval-input.json"
    output = session_dir / "approval-output.json"
    source.write_text(json.dumps(document))
    _, expected_ref = EXPECTED_IDENTITIES[str(session_dir)]
    command = [
        sys.executable,
        str(SCRIPT),
        "--input",
        str(source),
        "--output",
        str(output),
        "--expected-identity",
        expected_ref["path"],
        "--expected-identity-signature",
        str(EXPECTED_SIGNATURES[str(session_dir)]),
    ]
    missing = subprocess.run(command, env={"PATH": str(fake_bin)}, capture_output=True, text=True)
    assert json.loads(missing.stdout)["reasons"] == ["expected_identity.signing_prerequisite"]

    identity = json.loads(Path(expected_ref["path"]).read_text())
    identity["replacementId"] = "97b892e1-0f1e-42d5-bbc4-50465042e111"
    document["reviewedIdentity"]["replacementId"] = identity["replacementId"]
    source.write_text(json.dumps(document))
    _write_json(tmp_path / "changed-identity.json", identity)
    signature = EXPECTED_SIGNATURES[str(session_dir)].read_bytes()
    canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    with pytest.raises(InvalidSignature):
        Ed25519PublicKey.from_public_bytes(EXPECTED_PUBLIC_KEYS[str(session_dir)]).verify(signature, canonical)


def test_retagged_backend_image_id_fails_fixed_external_anchor(tmp_path, session_dir, monkeypatch, capsys):
    document = valid_input(session_dir, tmp_path)
    fake_bin, _ = _fake_commands(tmp_path, document)
    tool_config = tmp_path / "trusted-tools" / "config.json"
    config = json.loads(tool_config.read_text())
    config["image"][0]["Id"] = "sha256:" + "c" * 64
    tool_config.write_text(json.dumps(config))
    source = session_dir / "retagged-input.json"
    source.write_text(json.dumps(document))
    _, expected_ref = EXPECTED_IDENTITIES[str(session_dir)]
    sys.path.insert(0, str(SERVER / "scripts"))
    import course_mode_physical_tft_preflight as preflight

    monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ['PATH']}")
    monkeypatch.setattr(preflight, "PINNED_APPROVAL_PUBLIC_KEY_RAW", EXPECTED_PUBLIC_KEYS[str(session_dir)])
    monkeypatch.setattr(preflight, "PINNED_APPROVAL_KEY_FINGERPRINT", EXPECTED_FINGERPRINTS[str(session_dir)])
    result = preflight.main(
        [
            "--input",
            str(source),
            "--output",
            str(session_dir / "retagged-output.json"),
            "--expected-identity",
            expected_ref["path"],
            "--expected-identity-signature",
            str(EXPECTED_SIGNATURES[str(session_dir)]),
        ]
    )
    assert result == 1
    assert "image.anchor" in json.loads(capsys.readouterr().out)["reasons"]


def test_run_timeout_and_invalid_utf8_are_deterministic(tmp_path):
    sys.path.insert(0, str(SERVER / "scripts"))
    from course_mode_physical_tft_preflight import _run

    python = Path(sys.executable).resolve()
    python_sha = hashlib.sha256(python.read_bytes()).hexdigest()

    _, ok, reason = _run(
        [
            str(python),
            "-c",
            "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c','import time;time.sleep(10)']); time.sleep(10)",
        ],
        tmp_path,
        os.environ.copy(),
        allowed_executable=python,
        expected_executable_sha256=python_sha,
        timeout_seconds=0.05,
    )
    assert (ok, reason) == (False, "timeout")
    _, ok, reason = _run(
        [str(python), "-c", "import os; os.write(1, bytes([255]))"],
        tmp_path,
        os.environ.copy(),
        allowed_executable=python,
        expected_executable_sha256=python_sha,
    )
    assert (ok, reason) == (False, "decode")
    _, ok, reason = _run(
        [str(python), "-c", "import sys; sys.stdout.write('x' * 3000000)"],
        tmp_path,
        os.environ.copy(),
        allowed_executable=python,
        expected_executable_sha256=python_sha,
    )
    assert (ok, reason) == (False, "output_limit")
    started = time.monotonic()
    _, ok, reason = _run(
        [str(python), "-c", "import os; b=b'x'*65536\nwhile True: os.write(1,b)"],
        tmp_path,
        os.environ.copy(),
        allowed_executable=python,
        expected_executable_sha256=python_sha,
        timeout_seconds=2,
    )
    assert (ok, reason) == (False, "output_limit")
    assert time.monotonic() - started < 2


def test_run_cleans_descendants_after_successful_parent_exit(tmp_path):
    sys.path.insert(0, str(SERVER / "scripts"))
    from course_mode_physical_tft_preflight import _run

    python = Path(sys.executable).resolve()
    python_sha = hashlib.sha256(python.read_bytes()).hexdigest()

    marker = tmp_path / "descendant-marker"
    child = f"import time,pathlib;time.sleep(.5);pathlib.Path({str(marker)!r}).write_text('escaped')"
    parent = f"import subprocess,sys;subprocess.Popen([sys.executable,'-c',{child!r}]);print('{{}}')"
    stdout, ok, reason = _run(
        [str(python), "-c", parent],
        tmp_path,
        os.environ.copy(),
        allowed_executable=python,
        expected_executable_sha256=python_sha,
    )
    assert (stdout.strip(), ok, reason) == ("{}", True, None)
    time.sleep(0.7)
    assert not marker.exists()


def test_pinned_signer_fingerprint_is_derived_and_policy_checked(tmp_path, session_dir, monkeypatch):
    valid_input(session_dir, tmp_path)
    sys.path.insert(0, str(SERVER / "scripts"))
    import course_mode_physical_tft_preflight as preflight

    raw = EXPECTED_PUBLIC_KEYS[str(session_dir)]
    signature = EXPECTED_SIGNATURES[str(session_dir)].read_bytes()
    identity = EXPECTED_IDENTITIES[str(session_dir)][0]
    monkeypatch.setattr(preflight, "PINNED_APPROVAL_PUBLIC_KEY_RAW", raw)
    monkeypatch.setattr(preflight, "PINNED_APPROVAL_KEY_FINGERPRINT", "a" * 64)
    valid, derived = preflight._verify_pinned_identity_signature(preflight._canonical_bytes(identity), signature)
    assert valid is False
    assert derived == hashlib.sha256(raw).hexdigest()


def test_cryptography_is_pinned_in_clean_install_manifest():
    requirements = (SERVER / "requirements.txt").read_text()
    assert "cryptography==49.0.0" in requirements.splitlines()
    import cryptography

    assert cryptography.__version__ == "49.0.0"


def test_python_canonical_json_matches_node_unicode_bytes():
    sys.path.insert(0, str(SERVER / "scripts"))
    from course_mode_physical_tft_preflight import _canonical_bytes

    value = {"é": "雪", "a": [1, True, None], "nested": {"β": "café", "z": 2}}
    script = (
        "const v="
        + json.dumps(value, ensure_ascii=False)
        + ";const s=x=>Array.isArray(x)?x.map(s):(x&&typeof x==='object'?"
        + "Object.fromEntries(Object.keys(x).sort().map(k=>[k,s(x[k])])):x);"
        + "process.stdout.write(JSON.stringify(s(v)));"
    )
    node = subprocess.run(["node", "-e", script], capture_output=True, check=True)
    assert node.stdout == _canonical_bytes(value)


def test_parent_swap_to_symlink_fails_secure_read_and_publish(tmp_path, monkeypatch):
    sys.path.insert(0, str(SERVER / "scripts"))
    import course_mode_physical_tft_preflight as preflight

    trusted = tmp_path / "trusted"
    outside = tmp_path / "outside"
    trusted.mkdir()
    outside.mkdir()
    target = trusted / "artifact.bin"
    target.write_bytes(b"trusted")
    (outside / "artifact.bin").write_bytes(b"outside")
    original_check = preflight._path_has_symlink
    swapped = False

    def swap_after_check(path):
        nonlocal swapped
        result = original_check(path)
        if not swapped:
            swapped = True
            trusted.rename(tmp_path / "trusted-real")
            trusted.symlink_to(outside, target_is_directory=True)
        return result

    monkeypatch.setattr(preflight, "_path_has_symlink", swap_after_check)
    assert preflight._read_file_secure(target, collect=True)[2] == "open"
    assert (outside / "artifact.bin").read_bytes() == b"outside"

    publish_root = tmp_path / "publish"
    publish_outside = tmp_path / "publish-outside"
    publish_root.mkdir()
    publish_outside.mkdir()
    real_open_directory = preflight._open_directory_secure

    def swap_before_open(path):
        publish_root.rename(tmp_path / "publish-real")
        publish_root.symlink_to(publish_outside, target_is_directory=True)
        return real_open_directory(path)

    monkeypatch.setattr(preflight, "_path_has_symlink", original_check)
    monkeypatch.setattr(preflight, "_open_directory_secure", swap_before_open)
    assert preflight._publish_output(publish_root / "result.json", b"trusted", publish_root) is False
    assert not (publish_outside / "result.json").exists()


def test_output_publish_never_replaces_racing_destination(tmp_path, monkeypatch):
    sys.path.insert(0, str(SERVER / "scripts"))
    import course_mode_physical_tft_preflight as preflight

    destination = tmp_path / "result.json"
    real_link = os.link

    def race(source, target, **kwargs):
        destination.write_bytes(b"attacker")
        return real_link(source, target, **kwargs)

    monkeypatch.setattr(preflight.os, "link", race)
    assert preflight._publish_output(destination, b"trusted", tmp_path) is False
    assert destination.read_bytes() == b"attacker"


def test_non_finite_json_is_rejected_at_every_boundary(tmp_path, session_dir):
    document = valid_input(session_dir, tmp_path)
    _, expected_ref = EXPECTED_IDENTITIES[str(session_dir)]
    input_path = session_dir / "nan-input.json"
    input_path.write_text('{"schemaVersion":NaN}')
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--input",
            str(input_path),
            "--output",
            str(session_dir / "nan-out.json"),
            "--expected-identity",
            expected_ref["path"],
            "--expected-identity-signature",
            str(session_dir / "unused.sig"),
        ],
        env=os.environ.copy(),
        capture_output=True,
        text=True,
    )
    assert json.loads(result.stdout)["reasons"] == ["input.invalid_json"]

    identity_path = tmp_path / "nan-identity.json"
    identity_path.write_text('{"schemaVersion":NaN}')
    valid_source = session_dir / "valid-for-nan-identity.json"
    valid_source.write_text(json.dumps(document))
    identity_result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--input",
            str(valid_source),
            "--output",
            str(session_dir / "nan-identity-out.json"),
            "--expected-identity",
            str(identity_path),
            "--expected-identity-signature",
            str(session_dir / "unused.sig"),
        ],
        env=os.environ.copy(),
        capture_output=True,
        text=True,
    )
    assert json.loads(identity_result.stdout)["reasons"] == ["expected_identity.invalid_json"]

    receipt = deepcopy(document)
    payload = (
        Path(document["materializationReceipt"]["path"])
        .read_text()
        .replace('"schemaVersion":1', '"x":NaN,"schemaVersion":1')
    )
    receipt["materializationReceipt"] = _write(session_dir / "nan-receipt.json", payload.encode())
    assert "materialization.file.invalid_json" in _validate(receipt)


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


def test_external_identity_defeats_coherent_evidence_rewrite(tmp_path, session_dir):
    base = valid_input(session_dir, tmp_path)
    rewritten_id = "97b892e1-0f1e-42d5-bbc4-50465042e111"
    rewritten = deepcopy(base)
    rewritten["reviewedIdentity"]["replacementId"] = rewritten_id
    rewritten["assignmentSnapshot"] = deepcopy(rewritten["assignmentSnapshot"])
    assignment = json.loads(Path(rewritten["assignmentSnapshot"]["path"]).read_text())
    assignment["assignments"][0]["lessonId"] = rewritten_id
    assignment["snapshotChecksum"] = hashlib.sha256(
        json.dumps(assignment["assignments"], sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    rewritten["assignmentSnapshot"] = _write_json(session_dir / "rewritten-assignment.json", assignment)
    rewritten["visualPack"]["lessonId"] = rewritten_id
    _rewrite_materialization(rewritten, lambda body: body["replacement"].update(lessonId=rewritten_id))
    reasons = _validate(rewritten)
    assert "materialization.anchor" in reasons
    assert "identity.external_anchor" in reasons


def test_external_identity_and_receipts_fail_closed(tmp_path, session_dir):
    base = valid_input(session_dir, tmp_path)
    identity, identity_ref = EXPECTED_IDENTITIES[str(session_dir)]
    bad_mac_values = ["junk14:c1:9f:d1:ac:20", "15:c1:9f:d1:ac:20", "00:00:00:00:00:00"]
    for mac in bad_mac_values:
        changed = deepcopy(identity)
        changed["robotMac"] = mac
        assert "expected_identity.identity" in _validate_with_identity(base, changed, identity_ref["sha256"])

    unsigned = deepcopy(base)
    receipt = json.loads(Path(base["materializationReceipt"]["path"]).read_text())
    receipt["authorization"].pop("integrityDigest")
    unsigned["materializationReceipt"] = _write_json(session_dir / "unsigned-materialization.json", receipt)
    assert "materialization.authorization" in _validate(unsigned)

    firmware_drift = deepcopy(base)
    firmware = json.loads(Path(base["firmwareBuildReceipt"]["path"]).read_text())
    firmware["app"]["sha256"] = hashlib.sha256(b"other-app").hexdigest()
    unsigned_firmware = {key: value for key, value in firmware.items() if key != "integrityDigest"}
    firmware["integrityDigest"] = hashlib.sha256(
        json.dumps(unsigned_firmware, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    firmware_drift["firmwareBuildReceipt"] = _write_json(session_dir / "firmware-drift.json", firmware)
    reasons = _validate(firmware_drift)
    assert "firmware_receipt.anchor" in reasons
    assert "flash.firmware_receipt" in reasons


def test_nvs_visual_identifiers_and_parent_symlinks_fail_closed(tmp_path, session_dir):
    base = valid_input(session_dir, tmp_path)
    short_nvs = deepcopy(base)
    short_nvs["nvsPreservation"]["after"] = _write(session_dir / "short-nvs.bin", b"N" * (NVS_SIZE - 1))
    assert "nvs.size" in _validate(short_nvs)

    duplicate_phase = deepcopy(base)
    duplicate_phase["visualPack"]["phases"][1]["phaseId"] = "teach"
    assert "visual_pack.phase_unique" in _validate(duplicate_phase)
    duplicate_activity = deepcopy(base)
    duplicate_activity["visualPack"]["phases"][1]["activityIds"] = ["w01-a1"]
    assert "visual_pack.phase_unique" in _validate(duplicate_activity)

    linked_parent = session_dir / "linked-assets"
    linked_parent.symlink_to(session_dir / "assets", target_is_directory=True)
    linked = deepcopy(base)
    linked["visualPack"]["phases"][0]["layers"][0]["assetPath"] = str(
        linked_parent / Path(base["visualPack"]["phases"][0]["layers"][0]["assetPath"]).name
    )
    assert "visual_pack.asset_path" in _validate(linked)


def test_partition_table_is_fixed_by_external_identity(tmp_path, session_dir):
    base = valid_input(session_dir, tmp_path)
    snapshot = json.loads(Path(base["flashPlan"]["partitionSnapshot"]["path"]).read_text())
    snapshot["partitions"][2].update(size="0x3fff", end="0xcfff")
    snapshot["partitions"][3].update(offset="0xcfff", end="0xefff")
    snapshot["partitions"][4].update(offset="0xefff", end="0xffff")
    snapshot["partitions"][5].update(offset="0xffff", size="0x10001", end="0x20000")
    snapshot["snapshotChecksum"] = hashlib.sha256(
        json.dumps(snapshot["partitions"], sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    changed = deepcopy(base)
    changed["flashPlan"]["partitionSnapshot"] = _write_json(session_dir / "shifted-partitions.json", snapshot)
    reasons = _validate(changed)
    assert "flash.partition_snapshot.anchor" in reasons
    assert "flash.partition_snapshot.table" in reasons


def test_same_byte_dirty_exception_symlink_is_rejected(tmp_path, session_dir):
    base = valid_input(session_dir, tmp_path)
    esp_root = Path(base["repositories"]["esp"]["path"])
    protected = esp_root / VOICE_PATH
    same_bytes = tmp_path / "same-voice.py"
    same_bytes.write_bytes(protected.read_bytes())
    protected.unlink()
    protected.symlink_to(same_bytes)
    assert "repository.esp.dirty_exception_symlink" in _validate(base)


def test_build_and_materializer_versions_are_externally_anchored(tmp_path, session_dir):
    base = valid_input(session_dir, tmp_path)
    firmware = json.loads(Path(base["firmwareBuildReceipt"]["path"]).read_text())
    firmware["buildToolVersion"] = "9.9.9"
    unsigned = {key: value for key, value in firmware.items() if key != "integrityDigest"}
    firmware["integrityDigest"] = hashlib.sha256(
        json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    changed = deepcopy(base)
    changed["firmwareBuildReceipt"] = _write_json(session_dir / "rewritten-build.json", firmware)
    assert "firmware_receipt.anchor" in _validate(changed)

    materialization = deepcopy(base)
    _rewrite_materialization(
        materialization, lambda body: body.update(materializerVersion="course-mode-curriculum.v999")
    )
    assert "materialization.anchor" in _validate(materialization)


def test_external_identity_duplicate_keys_and_symlink_path_are_rejected(tmp_path, session_dir):
    document = valid_input(session_dir, tmp_path)
    source = session_dir / "input-external.json"
    source.write_text(json.dumps(document))
    duplicate = tmp_path / "duplicate-identity.json"
    duplicate.write_text('{"schemaVersion":1,"schemaVersion":1}')
    duplicate_result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--input",
            str(source),
            "--output",
            str(session_dir / "duplicate-out.json"),
            "--expected-identity",
            str(duplicate),
            "--expected-identity-signature",
            str(session_dir / "unused.sig"),
        ],
        env=os.environ.copy(),
        capture_output=True,
        text=True,
    )
    assert json.loads(duplicate_result.stdout)["reasons"] == ["expected_identity.duplicate_key"]

    _, identity_ref = EXPECTED_IDENTITIES[str(session_dir)]
    identity_link = tmp_path / "identity-link.json"
    identity_link.symlink_to(identity_ref["path"])
    symlink_result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--input",
            str(source),
            "--output",
            str(session_dir / "symlink-out.json"),
            "--expected-identity",
            str(identity_link),
            "--expected-identity-signature",
            str(session_dir / "unused.sig"),
        ],
        env=os.environ.copy(),
        capture_output=True,
        text=True,
    )
    assert json.loads(symlink_result.stdout)["reasons"] == ["expected_identity.path.symlink"]


def test_duplicate_json_keys_fail_before_commands(session_dir):
    source = session_dir / "duplicate.json"
    source.write_text('{"schemaVersion":3,"schemaVersion":3}')
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--input",
            str(source),
            "--output",
            str(session_dir / "out.json"),
            "--expected-identity",
            str(session_dir / "unused.json"),
            "--expected-identity-signature",
            str(session_dir / "unused.sig"),
        ],
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
        (
            lambda d: d["visualPack"]["phases"][0]["layers"].append(
                _layer(session_dir, "robotOverlay", "image/png", 7)
            ),
            "visual_pack.robot_video",
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
        _, expected_ref = EXPECTED_IDENTITIES[str(session_dir)]
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--input",
                str(source),
                "--output",
                str(session_dir / f"wrong-{index}-out.json"),
                "--expected-identity",
                expected_ref["path"],
                "--expected-identity-signature",
                str(EXPECTED_SIGNATURES[str(session_dir)]),
            ],
            env={"PATH": ""},
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        assert json.loads(result.stdout)["valid"] is False
        assert "Traceback" not in result.stderr


def test_recursive_wrong_type_sweep_never_raises(tmp_path, session_dir):
    base = valid_input(session_dir, tmp_path)
    paths = []

    def collect(value, path=()):
        if isinstance(value, dict):
            for key, item in value.items():
                paths.append((*path, key))
                collect(item, (*path, key))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                paths.append((*path, index))
                collect(item, (*path, index))

    def assign(value, path, replacement):
        target = value
        for component in path[:-1]:
            target = target[component]
        target[path[-1]] = replacement

    collect(base)
    for path in paths:
        original = base
        for component in path:
            original = original[component]
        replacements = ({}, [], None, 7, "wrong")
        replacement = next(item for item in replacements if type(item) is not type(original))
        changed = deepcopy(base)
        assign(changed, path, replacement)
        reasons = _validate(changed)
        assert isinstance(reasons, list), path


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
        [
            sys.executable,
            str(SCRIPT),
            "--input",
            str(source),
            "--output",
            str(session_dir / "bad-out.json"),
            "--expected-identity",
            str(session_dir / "unused.json"),
            "--expected-identity-signature",
            str(session_dir / "unused.sig"),
        ],
        env={"PATH": ""},
        capture_output=True,
        text=True,
    )
    assert json.loads(result.stdout) == {"reasons": ["input.invalid_json"], "valid": False}
    assert "private" not in result.stdout
