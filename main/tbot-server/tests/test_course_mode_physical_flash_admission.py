import hashlib
import importlib
import json
import os
import stat
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER / "scripts"))
admission = importlib.import_module("course_mode_physical_flash_admission")
NOW = datetime(2026, 9, 8, 8, 0, 0, tzinfo=timezone.utc)
SESSION_ID = "cab43f0d-62dc-49c4-9d30-e9630d195a44"
COURSE_ID = "a17792f6-8d86-4ad1-a6f3-77663b4d4674"
COURSE_KEY = "english-6month-4-6"
APP_SHA = "782020e2f8ac44bd197f57e9e126c196286a8223005de1e425f8290f12b28dff"
MANIFEST_SHA = "23b70849b6b65901b01b38e91279455a2e5e8d13989438e2e0bbfa707602aa65"
FIRMWARE_SHA = "b54c6ca33e9beb3747b44feceb7c64fea33fe1d6"
SAFETY_KEYS = (
    "adultObserverPresent", "motionAreaClearAndSecured", "immediatePowerIsolationReachable",
    "stablePower", "stableLan", "evidenceCaptureReady", "soleUsbSerialLease",
    "preserveBootloader", "preservePartitionTable", "preserveNvs", "preserveOtaData",
    "preservePhyInit", "preserveReserved", "preserveGeneratedAssets",
)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def partitions():
    return [
        {"name": "bootloader", "offset": "0x0", "size": "0x8000", "end": "0x8000", "protected": True},
        {"name": "partition-table", "offset": "0x8000", "size": "0x1000", "end": "0x9000", "protected": True},
        {"name": "nvs", "offset": "0x9000", "size": "0x4000", "end": "0xd000", "protected": True},
        {"name": "ota-data", "offset": "0xd000", "size": "0x2000", "end": "0xf000", "protected": True},
        {"name": "phy-init", "offset": "0xf000", "size": "0x1000", "end": "0x10000", "protected": True},
        {"name": "reserved", "offset": "0x10000", "size": "0x10000", "end": "0x20000", "protected": True},
        {"name": "application", "offset": "0x20000", "size": "0x7e0000", "end": "0x800000", "protected": False},
        {"name": "generated-assets", "offset": "0x800000", "size": "0x800000", "end": "0x1000000", "protected": True},
    ]


def documents(tmp_path, key):
    candidate_path = tmp_path / "candidate.json"
    admission_paths = {"input": tmp_path / "input.json", "output": tmp_path / "result.json", "expectedIdentity": tmp_path / "identity.json", "expectedIdentitySignature": tmp_path / "identity.sig"}
    repos = {
        "admin": {"path": "/src/admin", "sha": "1" * 40, "branch": "main", "remoteUrl": "git@example/admin.git", "dirtyExceptions": []},
        "backend": {"path": "/src/backend", "sha": "2" * 40, "branch": "main", "remoteUrl": "git@example/backend.git", "dirtyExceptions": []},
        "firmware": {"path": "/src/firmware", "sha": FIRMWARE_SHA, "branch": "main", "remoteUrl": "git@example/firmware.git", "dirtyExceptions": []},
    }
    images = {
        "backend": {"reference": "local/tbot-backend:41", "id": "sha256:" + "3" * 64, "platform": "linux/arm64", "provenanceLabels": {"org.opencontainers.image.revision": "2" * 40, "org.opencontainers.image.source": "git@example/backend.git"}},
        "web": {"reference": "local/tbot-server-web:41", "id": "sha256:" + "4" * 64, "platform": "linux/arm64", "provenanceLabels": {"org.opencontainers.image.revision": "1" * 40, "org.opencontainers.image.source": "git@example/admin.git"}},
    }
    firmware = {"board": "LCDWiki ES3C35P", "target": "esp32s3", "gitSha": FIRMWARE_SHA, "app": {"path": "/opt/tbot/course-mode/app.bin", "sha256": APP_SHA, "bytes": 3637200, "offset": "0x20000", "partitionBytes": 4128768}, "manifest": {"path": "/opt/tbot/course-mode/manifest.json", "sha256": MANIFEST_SHA}}
    actual = {"candidateId": "course-mode-2026-09-08.41", "createdAt": "2026-09-08T07:00:00Z", "expiresAt": "2026-09-08T09:00:00Z", "course": {"courseId": COURSE_ID, "courseKey": COURSE_KEY}, "repositories": {"adminEsp": repos["admin"], "backend": repos["backend"], "firmware": repos["firmware"]}, "images": {"lessonStudioBackend": {"reference": images["backend"]["reference"], "id": images["backend"]["id"]}, "lessonStudioWeb": {"reference": images["web"]["reference"], "id": images["web"]["id"]}}, "firmware": {"appPath": firmware["app"]["path"], "appOffset": "0x20000", "appBytes": 3637200, "appSha256": APP_SHA, "partitionBytes": 4128768, "evidenceManifestPath": firmware["manifest"]["path"], "evidenceManifestSha256": MANIFEST_SHA}, "tools": {"physicalAdmission": {name: str(path) for name, path in admission_paths.items()}}, "evidenceRoot": str(tmp_path)}
    candidate_path.write_bytes(canonical(actual))
    candidate = {"candidateId": actual["candidateId"], "courseId": COURSE_ID, "courseKey": COURSE_KEY, "createdAt": actual["createdAt"], "expiresAt": actual["expiresAt"], "path": str(candidate_path), "sha256": hashlib.sha256(candidate_path.read_bytes()).hexdigest(), "repositories": repos, "images": images, "firmware": firmware}
    robot = {"mac": "14:c1:9f:d1:ac:20", "board": "LCDWiki ES3C35P", "target": "esp32s3", "serialPath": "/dev/cu.usbmodem1101", "exactlyOneRobot": True}
    identity = {"schemaVersion": 1, "sessionId": SESSION_ID, "candidate": candidate, "partitionTable": partitions(), "robot": robot, "signer": {"algorithm": "ed25519", "fingerprint": admission.PINNED_APPROVAL_KEY_FINGERPRINT}}
    input_doc = {"schemaVersion": 1, "sessionId": SESSION_ID, "checkedAt": "2026-09-08T08:00:00Z", "candidate": deepcopy(candidate), "robot": deepcopy(robot), "serialLease": {"soleLeaseConfirmed": True, "competingProcessesStopped": True, "devicePath": "/dev/cu.usbmodem1101", "discoveredDevices": ["/dev/cu.usbmodem1101"], "holderPids": [], "inventoryMethod": "lstat-glob-lsof-v1"}, "flashPlan": {"operation": {"operation": "write_flash", "offset": "0x20000", "imageSha256": APP_SHA, "imageBytes": 3637200, "after": "no-reset", "eraseChip": False, "mergedImage": False}, "protectedPartitions": [p for p in partitions() if p["protected"]], "preserveProtectedPartitions": True}, "safety": {name: True for name in SAFETY_KEYS}}
    return input_doc, identity, key.sign(canonical(identity)), actual


@pytest.fixture
def valid_files(tmp_path, monkeypatch):
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    monkeypatch.setattr(admission, "PINNED_APPROVAL_PUBLIC_KEY_RAW", public)
    monkeypatch.setattr(admission, "PINNED_APPROVAL_KEY_FINGERPRINT", hashlib.sha256(public).hexdigest())
    input_doc, identity, _, _ = documents(tmp_path, key)
    identity["signer"]["fingerprint"] = hashlib.sha256(public).hexdigest()
    paths = {"input": tmp_path / "input.json", "identity": tmp_path / "identity.json", "signature": tmp_path / "identity.sig", "output": tmp_path / "result.json"}
    paths["key"] = key
    sys.path.insert(0, str(SERVER / "tests"))
    candidate_tests = importlib.import_module("test_course_mode_candidate_manifest")
    fixture_root = tmp_path / "complete-candidate"
    fixture_root.mkdir()
    repositories = candidate_tests.repositories.__wrapped__(fixture_root)
    actual = candidate_tests.candidate.__wrapped__(repositories, fixture_root, monkeypatch)
    actual.update(candidateId="course-mode-2026-09-08.41", createdAt="2026-09-08T07:00:00Z", expiresAt="2026-09-08T09:00:00Z")
    actual["course"]["courseId"] = COURSE_ID
    actual["curriculum"]["courseId"] = COURSE_ID
    actual["repositories"]["firmware"]["sha"] = FIRMWARE_SHA
    actual["tools"]["physicalAdmission"] = {"input": str(paths["input"]), "output": str(paths["output"]), "expectedIdentity": str(paths["identity"]), "expectedIdentitySignature": str(paths["signature"])}
    actual["evidenceRoot"] = str(tmp_path)
    firmware = actual["firmware"]
    firmware.update(appBytes=3637200, appSha256=APP_SHA, partitionBytes=4128768, freeBytes=4128768-3637200, evidenceManifestSha256=MANIFEST_SHA)
    repository_binding = {"admin": actual["repositories"]["adminEsp"], "backend": actual["repositories"]["backend"], "firmware": actual["repositories"]["firmware"]}
    image_binding = {
        "backend": {**actual["images"]["lessonStudioBackend"], "platform": "linux/arm64", "provenanceLabels": {"org.opencontainers.image.revision": repository_binding["backend"]["sha"], "org.opencontainers.image.source": repository_binding["backend"]["remoteUrl"]}},
        "web": {**actual["images"]["lessonStudioWeb"], "platform": "linux/arm64", "provenanceLabels": {"org.opencontainers.image.revision": repository_binding["admin"]["sha"], "org.opencontainers.image.source": repository_binding["admin"]["remoteUrl"]}},
    }
    candidate_path = tmp_path / "candidate.json"
    candidate_path.chmod(0o644)
    candidate_path.write_bytes(canonical(actual))
    candidate_path.chmod(0o444)
    binding = {"candidateId": actual["candidateId"], "courseId": COURSE_ID, "courseKey": COURSE_KEY, "createdAt": actual["createdAt"], "expiresAt": actual["expiresAt"], "path": str(candidate_path), "sha256": hashlib.sha256(candidate_path.read_bytes()).hexdigest(), "repositories": repository_binding, "images": image_binding, "firmware": {"board": "LCDWiki ES3C35P", "target": "esp32s3", "gitSha": FIRMWARE_SHA, "app": {"path": firmware["appPath"], "sha256": APP_SHA, "bytes": 3637200, "offset": "0x20000", "partitionBytes": 4128768}, "manifest": {"path": firmware["evidenceManifestPath"], "sha256": MANIFEST_SHA}}}
    input_doc["candidate"] = deepcopy(binding)
    identity["candidate"] = deepcopy(binding)
    real_git = admission.candidate_manifest._git
    firmware_root = Path(actual["repositories"]["firmware"]["path"])
    monkeypatch.setattr(admission.candidate_manifest, "_git", lambda root, *args: FIRMWARE_SHA + "\n" if root == firmware_root and args[-2:] == ("rev-parse", "HEAD") or root == firmware_root and args[-3:] == ("rev-parse", "--verify", "HEAD^{commit}") else real_git(root, *args))
    manifest_content = json.loads(Path(firmware["evidenceManifestPath"]).read_text())
    manifest_content.update(createdAt="2026-09-08T06:00:00Z", sourceCommit=FIRMWARE_SHA)
    manifest_content["app"].update(bytes=3637200, sha256=APP_SHA, offset="0x20000")
    manifest_content["partition"].update(bytes=4128768, freeBytes=4128768-3637200, freePercent=round((4128768-3637200)/4128768*100, 6))
    manifest_bytes = canonical(manifest_content)
    real_descriptor = admission.candidate_manifest.secure_regular_descriptor
    def descriptor(path, limit, **kwargs):
        if path == Path(firmware["evidenceManifestPath"]): return {"sha256": MANIFEST_SHA, "bytes": len(manifest_bytes), "content": manifest_bytes}, None
        if path == Path(firmware["appPath"]): return {"sha256": APP_SHA, "bytes": 3637200}, None
        return real_descriptor(path, limit, **kwargs)
    monkeypatch.setattr(admission.candidate_manifest, "secure_regular_descriptor", descriptor)
    monkeypatch.setattr(admission.candidate_manifest, "_validate_container_tool", lambda name, value, reasons, verify_identity: Path(value["path"]))
    monkeypatch.setattr(admission.candidate_manifest, "_validate_python_test_runtime", lambda *args, **kwargs: None)
    monkeypatch.setattr(admission.candidate_manifest, "_validate_esp_idf", lambda *args, **kwargs: None)
    docker_images = {
        actual["images"]["lessonStudioBackend"]["reference"]: {"Id": actual["images"]["lessonStudioBackend"]["id"], "Config": {"Labels": image_binding["backend"]["provenanceLabels"]}},
        actual["images"]["lessonStudioWeb"]["reference"]: {"Id": actual["images"]["lessonStudioWeb"]["id"], "Config": {"Labels": image_binding["web"]["provenanceLabels"]}},
        actual["database"]["engineImage"]: {"Id": actual["database"]["engineImageId"], "Config": {"Labels": {}}},
    }
    monkeypatch.setattr(admission.candidate_manifest, "_docker_image_descriptor", lambda reference, _executable: docker_images.get(reference))
    paths["input"].write_bytes(canonical(input_doc)); paths["identity"].write_bytes(canonical(identity)); paths["signature"].write_bytes(key.sign(canonical(identity)))
    for path in (Path(input_doc["candidate"]["path"]), paths["input"], paths["identity"], paths["signature"]): path.chmod(0o444)
    monkeypatch.setattr(admission, "utc_now", lambda: NOW)
    monkeypatch.setattr(admission, "collect_serial_inventory", lambda: (["/dev/cu.usbmodem1101"], [], None))
    return input_doc, identity, paths, actual


def run_main(paths):
    return admission.main(["--input", str(paths["input"]), "--output", str(paths["output"]), "--expected-identity", str(paths["identity"]), "--expected-identity-signature", str(paths["signature"])])


def rewrite(path, value):
    path.chmod(0o644); path.write_bytes(canonical(value)); path.chmod(0o444)


def resign(paths, input_doc, identity):
    rewrite(paths["input"], input_doc)
    rewrite(paths["identity"], identity)
    paths["signature"].chmod(0o644)
    paths["signature"].write_bytes(paths["key"].sign(canonical(identity)))
    paths["signature"].chmod(0o444)


def test_valid_signed_admission(valid_files):
    input_doc, identity, paths, _ = valid_files
    assert run_main(paths) == 0
    result = json.loads(paths["output"].read_text())
    assert result["status"] == "pass" and result["reasons"] == []
    assert result["physicalActionsPerformed"] is False and result["serialOpened"] is False
    assert result["candidateId"] == input_doc["candidate"]["candidateId"] and result["sessionId"] == identity["sessionId"]


@pytest.mark.parametrize("mutation,reason", [
    (lambda d: d.update(extra=True), "input.schema"),
    (lambda d: d.__setitem__("schemaVersion", True), "input.schema"),
    (lambda d: d.__setitem__("checkedAt", "2026-09-08T06:00:00Z"), "checkedAt.stale"),
    (lambda d: d["candidate"].__setitem__("candidateId", "wrong"), "candidate.identity"),
    (lambda d: d["candidate"]["repositories"]["backend"].__setitem__("sha", "9" * 40), "candidate.identity"),
    (lambda d: d["candidate"]["images"]["web"].__setitem__("id", "sha256:" + "9" * 64), "candidate.identity"),
    (lambda d: d["candidate"]["firmware"]["app"].__setitem__("bytes", 1), "candidate.identity"),
    (lambda d: d["candidate"]["firmware"]["manifest"].__setitem__("sha256", "8" * 64), "candidate.identity"),
    (lambda d: d["robot"].__setitem__("mac", "00:00:00:00:00:00"), "robot.identity"),
    (lambda d: d["flashPlan"]["operation"].__setitem__("eraseChip", True), "flashPlan.operation"),
    (lambda d: d["flashPlan"]["operation"].__setitem__("mergedImage", True), "flashPlan.operation"),
    (lambda d: d["flashPlan"].update(command="erase_flash"), "flashPlan.keys"),
    (lambda d: d["flashPlan"]["protectedPartitions"].pop(), "flashPlan.protectedPartitions"),
])
def test_rejects_drift_and_unsafe_shapes(valid_files, mutation, reason, capsys):
    input_doc, _, paths, _ = valid_files; mutation(input_doc); rewrite(paths["input"], input_doc)
    assert run_main(paths) == 1 and reason in json.loads(capsys.readouterr().out)["reasons"]
    assert not paths["output"].exists()


@pytest.mark.parametrize("key", SAFETY_KEYS)
def test_each_false_safety_assertion_fails(valid_files, key, capsys):
    input_doc, _, paths, _ = valid_files; input_doc["safety"][key] = False; rewrite(paths["input"], input_doc)
    assert run_main(paths) == 1 and f"safety.{key}" in json.loads(capsys.readouterr().out)["reasons"]


@pytest.mark.parametrize("devices,holders,error,reason", [([], [], None, "serial.inventory"), (["/dev/cu.usbmodem1101", "/dev/cu.usbserial2"], [], None, "serial.inventory"), (["/dev/cu.usbmodem1101"], [321], None, "serial.occupied"), (["/dev/cu.usbmodem1101"], [], "timeout", "serial.lsof.timeout"), (["/dev/cu.usbmodem1101"], [], "exit", "serial.lsof.exit"), (["/dev/cu.usbmodem1101"], [], "output", "serial.lsof.output")])
def test_inventory_failures(valid_files, monkeypatch, capsys, devices, holders, error, reason):
    _, _, paths, _ = valid_files; monkeypatch.setattr(admission, "collect_serial_inventory", lambda: (devices, holders, error))
    assert run_main(paths) == 1 and reason in json.loads(capsys.readouterr().out)["reasons"]


def test_candidate_validation_and_reference_fail_closed(valid_files, monkeypatch, capsys):
    _, _, paths, _ = valid_files; monkeypatch.setattr(admission.candidate_manifest, "validate_candidate", lambda candidate, now=None: ["expiresAt.expired"])
    assert run_main(paths) == 1 and "candidate.expiresAt.expired" in json.loads(capsys.readouterr().out)["reasons"]


def test_signature_mismatch_fails(valid_files, capsys):
    _, _, paths, _ = valid_files; paths["signature"].chmod(0o644); value = bytearray(paths["signature"].read_bytes()); value[0] ^= 1; paths["signature"].write_bytes(value); paths["signature"].chmod(0o444)
    assert run_main(paths) == 1 and json.loads(capsys.readouterr().out)["reasons"] == ["expectedIdentity.signature"]


@pytest.mark.parametrize("payload,reason", [(b'{"schemaVersion":1,"schemaVersion":1}', "input.duplicate_key"), (b'{"schemaVersion":NaN}', "input.invalid_json"), (b'[]', "input.schema"), (b'{' + b' ' * (1024 * 1024 + 1), "input.unreadable")], ids=["duplicate", "nonfinite", "wrong-type", "oversized"])
def test_strict_bounded_json(valid_files, payload, reason, capsys):
    _, _, paths, _ = valid_files; paths["input"].chmod(0o644); paths["input"].write_bytes(payload); paths["input"].chmod(0o444)
    assert run_main(paths) == 1 and reason in json.loads(capsys.readouterr().out)["reasons"]


@pytest.mark.parametrize("kind", ["writable", "hardlink", "symlink"])
def test_rejects_unsafe_input_paths(valid_files, tmp_path, kind, capsys):
    _, _, paths, _ = valid_files; original = paths["input"]
    if kind == "writable": original.chmod(0o644)
    elif kind == "hardlink": paths["input"] = tmp_path / "hard.json"; os.link(original, paths["input"])
    else: paths["input"] = tmp_path / "sym.json"; paths["input"].symlink_to(original)
    assert run_main(paths) == 1 and "input.unreadable" in json.loads(capsys.readouterr().out)["reasons"]


def test_rejects_existing_output(valid_files, capsys):
    _, _, paths, _ = valid_files; paths["output"].write_text("keep")
    assert run_main(paths) == 1 and paths["output"].read_text() == "keep"
    assert "output.path" in json.loads(capsys.readouterr().out)["reasons"]


def test_collect_inventory_is_bounded_and_nonopening(monkeypatch):
    calls = []
    monkeypatch.setattr(admission.glob, "glob", lambda pattern: ["/dev/cu.usbmodem1101"])
    device = type("S", (), {"st_mode": stat.S_IFCHR, "st_dev": 1, "st_ino": 2, "st_rdev": 3})()
    monkeypatch.setattr(admission, "_device_lstat", lambda path: device)
    monkeypatch.setattr(admission, "_device_stat", lambda path: device)
    monkeypatch.setattr(admission, "TRUSTED_LSOF_EXECUTABLE", Path("/usr/sbin/lsof"))
    monkeypatch.setattr(admission, "_trusted_lsof", lambda: True)
    monkeypatch.setattr(admission.candidate_manifest, "run_bounded_command", lambda command, **kwargs: calls.append((command, kwargs)) or admission.candidate_manifest.BoundedCommandResult(0, "p321\n", None))
    assert admission.collect_serial_inventory() == (["/dev/cu.usbmodem1101"], [321], None)
    assert calls[0][0] == ["/usr/sbin/lsof", "-nP", "-t", "--", "/dev/cu.usbmodem1101"] and calls[0][1]["max_output_bytes"] == admission.MAX_LSOF_OUTPUT_BYTES


def test_failure_is_deterministic_redacted(valid_files, capsys):
    input_doc, _, paths, _ = valid_files; input_doc["secretValue"] = "PRIVATE KEY AUDIO TRANSCRIPT"; rewrite(paths["input"], input_doc)
    assert run_main(paths) == 1; first = capsys.readouterr().out; assert run_main(paths) == 1; second = capsys.readouterr().out
    assert first == second and all(word not in first for word in ("PRIVATE", "AUDIO", "TRANSCRIPT", "Traceback"))


def test_candidate_binds_every_cli_path(valid_files, capsys):
    input_doc, _, paths, actual = valid_files
    actual["tools"]["physicalAdmission"]["expectedIdentity"] = str(paths["input"])
    candidate_path = Path(input_doc["candidate"]["path"])
    rewrite(candidate_path, actual)
    input_doc["candidate"]["sha256"] = hashlib.sha256(candidate_path.read_bytes()).hexdigest()
    rewrite(paths["input"], input_doc)
    assert run_main(paths) == 1
    assert json.loads(capsys.readouterr().out)["reasons"] == ["candidate.physicalAdmission"]


@pytest.mark.parametrize("checked,reason", [("2026-09-08T08:01:00Z", "checkedAt.future"), ("2026-09-08T06:59:59Z", "checkedAt.candidateInterval")])
def test_checked_at_cannot_be_future_or_outside_candidate_interval(valid_files, checked, reason, capsys):
    input_doc, _, paths, _ = valid_files
    input_doc["checkedAt"] = checked
    rewrite(paths["input"], input_doc)
    assert run_main(paths) == 1
    assert reason in json.loads(capsys.readouterr().out)["reasons"]


@pytest.mark.parametrize("field,payload,reason", [
    ("identity", b'{"schemaVersion":1,"schemaVersion":1}', "expectedIdentity.duplicate_key"),
    ("identity", b'{"schemaVersion":Infinity}', "expectedIdentity.invalid_json"),
    ("identity", b'{' + b' ' * (1024 * 1024 + 1), "expectedIdentity.unreadable"),
    ("signature", b"x" * 63, "expectedIdentity.signature"),
], ids=["identity-duplicate", "identity-nonfinite", "identity-oversized", "signature-size"])
def test_identity_and_signature_inputs_are_strict_and_bounded(valid_files, field, payload, reason, capsys):
    _, _, paths, _ = valid_files
    paths[field].chmod(0o644); paths[field].write_bytes(payload); paths[field].chmod(0o444)
    assert run_main(paths) == 1
    assert reason in json.loads(capsys.readouterr().out)["reasons"]


@pytest.mark.parametrize("field,reason", [("input", "input.invalid_json"), ("identity", "expectedIdentity.invalid_json")])
def test_overflowing_float_is_rejected(valid_files, field, reason, capsys):
    _, _, paths, _ = valid_files
    paths[field].chmod(0o644); paths[field].write_bytes(b'{"overflow":1e999}'); paths[field].chmod(0o444)
    assert run_main(paths) == 1
    assert reason in json.loads(capsys.readouterr().out)["reasons"]


def test_ed25519_s_plus_l_malleation_is_rejected(valid_files, capsys):
    _, _, paths, _ = valid_files
    signature = paths["signature"].read_bytes()
    order = 2**252 + 27742317777372353535851937790883648493
    malleated = signature[:32] + (int.from_bytes(signature[32:], "little") + order).to_bytes(32, "little")
    paths["signature"].chmod(0o644); paths["signature"].write_bytes(malleated); paths["signature"].chmod(0o444)
    assert run_main(paths) == 1
    assert json.loads(capsys.readouterr().out)["reasons"] == ["expectedIdentity.signature"]


def test_publish_short_write_is_redacted_and_removes_only_partial_file(valid_files, monkeypatch, capsys):
    _, _, paths, _ = valid_files
    real_write = admission.os.write
    monkeypatch.setattr(admission.os, "write", lambda fd, data: 0 if Path(paths["output"]).name else real_write(fd, data))
    assert run_main(paths) == 1
    assert not paths["output"].exists()
    assert json.loads(capsys.readouterr().out)["reasons"] == ["output.path"]


def test_publish_fsync_failure_does_not_remove_replacement(valid_files, monkeypatch, capsys):
    _, _, paths, _ = valid_files
    monkeypatch.setattr(admission.os, "fsync", lambda _fd: (_ for _ in ()).throw(OSError("secret")))
    assert run_main(paths) == 1
    assert not paths["output"].exists()
    assert "secret" not in capsys.readouterr().out


def test_wrong_operator_owner_is_rejected(valid_files, monkeypatch, capsys):
    _, _, paths, _ = valid_files
    real_fstat = admission.os.fstat
    def wrong_owner(fd):
        value = real_fstat(fd)
        if stat.S_ISREG(value.st_mode):
            fields = list(value); fields[4] = 502
            return os.stat_result(fields)
        return value
    monkeypatch.setattr(admission.os, "fstat", wrong_owner)
    assert run_main(paths) == 1
    assert "input.unreadable" in json.loads(capsys.readouterr().out)["reasons"]


def test_untrusted_lsof_is_rejected_without_execution(monkeypatch):
    monkeypatch.setattr(admission.glob, "glob", lambda _pattern: [])
    monkeypatch.setattr(admission, "TRUSTED_LSOF_EXECUTABLE", Path("/tmp/untrusted-lsof"))
    called = []
    monkeypatch.setattr(admission.candidate_manifest, "run_bounded_command", lambda *args, **kwargs: called.append(args))
    assert admission.collect_serial_inventory() == ([], [], "untrusted")
    assert called == []


@pytest.mark.parametrize("mutation,reason", [
    (lambda c: c["images"]["web"].__setitem__("platform", "linux/amd64"), "candidate.images.web"),
    (lambda c: c["images"]["backend"]["provenanceLabels"].__setitem__("org.opencontainers.image.source", "wrong"), "candidate.images.backend"),
    (lambda c: c["firmware"].__setitem__("board", "wrong"), "candidate.firmware"),
    (lambda c: c["firmware"].__setitem__("target", "esp32"), "candidate.firmware"),
    (lambda c: c["firmware"]["app"].__setitem__("sha256", "9" * 64), "candidate.firmware.app"),
    (lambda c: c["firmware"]["app"].__setitem__("offset", "0x0"), "candidate.firmware.app"),
])
def test_resigned_identity_still_rejects_intrinsically_wrong_facts(valid_files, mutation, reason, capsys):
    input_doc, identity, paths, _ = valid_files
    mutation(input_doc["candidate"])
    mutation(identity["candidate"])
    resign(paths, input_doc, identity)
    assert run_main(paths) == 1
    assert reason in json.loads(capsys.readouterr().out)["reasons"]


@pytest.mark.parametrize("field", ["identity", "signature"])
def test_rejects_writable_identity_and_signature(valid_files, field, capsys):
    _, _, paths, _ = valid_files
    paths[field].chmod(0o644)
    assert run_main(paths) == 1
    assert any("expectedIdentity" in reason for reason in json.loads(capsys.readouterr().out)["reasons"])


def test_candidate_path_replacement_before_publish_fails_closed(valid_files, monkeypatch, capsys):
    _, _, paths, _ = valid_files
    real_still_bound = admission._still_bound
    calls = 0
    def changed(record):
        nonlocal calls
        calls += 1
        return False if calls == 4 else real_still_bound(record)
    monkeypatch.setattr(admission, "_still_bound", changed)
    assert run_main(paths) == 1
    assert "input.changed" in json.loads(capsys.readouterr().out)["reasons"]


def test_device_identity_change_across_lsof_is_rejected(monkeypatch):
    before = type("S", (), {"st_mode": stat.S_IFCHR, "st_dev": 1, "st_ino": 2, "st_rdev": 3})()
    after = type("S", (), {"st_mode": stat.S_IFCHR, "st_dev": 1, "st_ino": 9, "st_rdev": 3})()
    calls = 0
    def device(_path):
        nonlocal calls
        calls += 1
        return before if calls <= 2 else after
    monkeypatch.setattr(admission.glob, "glob", lambda _pattern: [admission.SERIAL_PATH])
    monkeypatch.setattr(admission, "_device_lstat", device)
    monkeypatch.setattr(admission, "_device_stat", device)
    monkeypatch.setattr(admission, "_trusted_lsof", lambda: True)
    monkeypatch.setattr(admission.candidate_manifest, "run_bounded_command", lambda *args, **kwargs: admission.candidate_manifest.BoundedCommandResult(1, "", None))
    assert admission.collect_serial_inventory()[2] == "device_changed"


@pytest.mark.parametrize("program,expected", [
    ("import time;time.sleep(1)", "timeout"),
    ("print('x'*100000)", "output"),
])
def test_real_bounded_command_enforces_timeout_and_output(program, expected):
    result = admission.candidate_manifest.run_bounded_command(
        [sys.executable, "-c", program], cwd=Path("/"), timeout_sec=0.05,
        max_output_bytes=128, env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent"},
    )
    assert result.error == expected


def test_real_parent_rename_during_read_is_rejected(tmp_path, monkeypatch):
    parent = tmp_path / "bound"
    parent.mkdir()
    source = parent / "input.json"
    source.write_text("{}")
    source.chmod(0o444)
    moved = tmp_path / "moved"
    real_check = admission.candidate_manifest._trusted_source_directory_still_named
    def replace(path, fd, metadata, ancestry):
        parent.rename(moved)
        parent.mkdir()
        try:
            return real_check(path, fd, metadata, ancestry)
        finally:
            parent.rmdir()
            moved.rename(parent)
    monkeypatch.setattr(admission.candidate_manifest, "_trusted_source_directory_still_named", replace)
    assert admission._secure_read(source, 1024)[1] == "changed"


def test_real_source_replacement_after_output_fsync_removes_result(valid_files, monkeypatch, capsys):
    input_doc, _, paths, _ = valid_files
    candidate_path = Path(input_doc["candidate"]["path"])
    original = candidate_path.read_bytes()
    backup = candidate_path.with_suffix(".old")
    real_fsync = admission.os.fsync
    replaced = False
    def replace_after_fsync(fd):
        nonlocal replaced
        result = real_fsync(fd)
        if not replaced and stat.S_ISREG(os.fstat(fd).st_mode):
            replaced = True
            candidate_path.rename(backup)
            candidate_path.write_bytes(original)
            candidate_path.chmod(0o444)
        return result
    monkeypatch.setattr(admission.os, "fsync", replace_after_fsync)
    assert run_main(paths) == 1
    assert not paths["output"].exists()
    assert json.loads(capsys.readouterr().out)["reasons"] == ["output.path"]


@pytest.mark.parametrize("field,value", [("mac", "00:11:22:33:44:55"), ("board", "wrong"), ("target", "esp32"), ("serialPath", "/dev/cu.wrong")])
def test_resigned_wrong_robot_identity_is_rejected(valid_files, field, value, capsys):
    input_doc, identity, paths, _ = valid_files
    input_doc["robot"][field] = value
    identity["robot"][field] = value
    resign(paths, input_doc, identity)
    assert run_main(paths) == 1
    assert "robot.identity" in json.loads(capsys.readouterr().out)["reasons"]


def test_actual_candidate_expiry_is_reported_by_real_validator(valid_files, capsys):
    input_doc, identity, paths, actual = valid_files
    actual["expiresAt"] = "2026-09-08T07:59:59Z"
    candidate_path = Path(input_doc["candidate"]["path"])
    rewrite(candidate_path, actual)
    digest = hashlib.sha256(candidate_path.read_bytes()).hexdigest()
    input_doc["candidate"]["sha256"] = digest
    identity["candidate"]["sha256"] = digest
    resign(paths, input_doc, identity)
    assert run_main(paths) == 1
    assert "candidate.expiresAt.expired" in json.loads(capsys.readouterr().out)["reasons"]


@pytest.mark.parametrize("kind,reason", [("image-id", "candidate.images.lessonStudioWeb.id"), ("repository-sha", "candidate.repositories.backend.sha"), ("app-size", "candidate.firmware.app")])
def test_resigned_candidate_drift_is_checked_against_external_identity(valid_files, kind, reason, capsys):
    input_doc, identity, paths, actual = valid_files
    if kind == "image-id":
        value = "sha256:" + "9" * 64
        input_doc["candidate"]["images"]["web"]["id"] = value
        identity["candidate"]["images"]["web"]["id"] = value
        actual["images"]["lessonStudioWeb"]["id"] = value
    elif kind == "repository-sha":
        value = "9" * 40
        input_doc["candidate"]["repositories"]["backend"]["sha"] = value
        identity["candidate"]["repositories"]["backend"]["sha"] = value
        input_doc["candidate"]["images"]["backend"]["provenanceLabels"]["org.opencontainers.image.revision"] = value
        identity["candidate"]["images"]["backend"]["provenanceLabels"]["org.opencontainers.image.revision"] = value
        actual["repositories"]["backend"]["sha"] = value
    else:
        input_doc["candidate"]["firmware"]["app"]["bytes"] = 1
        identity["candidate"]["firmware"]["app"]["bytes"] = 1
        actual["firmware"]["appBytes"] = 1
        actual["firmware"]["freeBytes"] = actual["firmware"]["partitionBytes"] - 1
    candidate_path = Path(input_doc["candidate"]["path"])
    rewrite(candidate_path, actual)
    digest = hashlib.sha256(candidate_path.read_bytes()).hexdigest()
    input_doc["candidate"]["sha256"] = digest
    identity["candidate"]["sha256"] = digest
    resign(paths, input_doc, identity)
    assert run_main(paths) == 1
    assert reason in json.loads(capsys.readouterr().out)["reasons"]
