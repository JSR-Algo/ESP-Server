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
    actual = {"candidateId": "course-mode-2026-09-08.41", "createdAt": "2026-09-08T07:00:00Z", "expiresAt": "2026-09-08T09:00:00Z", "course": {"courseId": COURSE_ID, "courseKey": COURSE_KEY}, "repositories": {"adminEsp": repos["admin"], "backend": repos["backend"], "firmware": repos["firmware"]}, "images": {"lessonStudioBackend": {"reference": images["backend"]["reference"], "id": images["backend"]["id"]}, "lessonStudioWeb": {"reference": images["web"]["reference"], "id": images["web"]["id"]}}, "firmware": {"appPath": firmware["app"]["path"], "appOffset": "0x20000", "appBytes": 3637200, "appSha256": APP_SHA, "partitionBytes": 4128768, "evidenceManifestPath": firmware["manifest"]["path"], "evidenceManifestSha256": MANIFEST_SHA}}
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
    input_doc, identity, _, actual = documents(tmp_path, key)
    identity["signer"]["fingerprint"] = hashlib.sha256(public).hexdigest()
    paths = {"input": tmp_path / "input.json", "identity": tmp_path / "identity.json", "signature": tmp_path / "identity.sig", "output": tmp_path / "result.json"}
    paths["input"].write_bytes(canonical(input_doc)); paths["identity"].write_bytes(canonical(identity)); paths["signature"].write_bytes(key.sign(canonical(identity)))
    for path in (Path(input_doc["candidate"]["path"]), paths["input"], paths["identity"], paths["signature"]): path.chmod(0o444)
    monkeypatch.setattr(admission, "utc_now", lambda: NOW)
    monkeypatch.setattr(admission, "collect_serial_inventory", lambda: (["/dev/cu.usbmodem1101"], [], None))
    monkeypatch.setattr(admission.candidate_manifest, "validate_candidate", lambda candidate, now=None: [])
    return input_doc, identity, paths, actual


def run_main(paths):
    return admission.main(["--input", str(paths["input"]), "--output", str(paths["output"]), "--expected-identity", str(paths["identity"]), "--expected-identity-signature", str(paths["signature"])])


def rewrite(path, value):
    path.chmod(0o644); path.write_bytes(canonical(value)); path.chmod(0o444)


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


@pytest.mark.parametrize("payload,reason", [(b'{"schemaVersion":1,"schemaVersion":1}', "input.duplicate_key"), (b'{"schemaVersion":NaN}', "input.invalid_json"), (b'[]', "input.schema"), (b'{' + b' ' * (1024 * 1024 + 1), "input.unreadable")])
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
    monkeypatch.setattr(admission.os, "lstat", lambda path: type("S", (), {"st_mode": stat.S_IFCHR})())
    monkeypatch.setattr(admission.os, "stat", lambda path: type("S", (), {"st_mode": stat.S_IFCHR})())
    monkeypatch.setattr(admission, "TRUSTED_LSOF_EXECUTABLE", Path("/usr/sbin/lsof"))
    monkeypatch.setattr(admission.candidate_manifest, "run_bounded_command", lambda command, **kwargs: calls.append((command, kwargs)) or admission.candidate_manifest.BoundedCommandResult(0, "p321\n", None))
    assert admission.collect_serial_inventory() == (["/dev/cu.usbmodem1101"], [321], None)
    assert calls[0][0] == ["/usr/sbin/lsof", "-nP", "-t", "--", "/dev/cu.usbmodem1101"] and calls[0][1]["max_output_bytes"] == admission.MAX_LSOF_OUTPUT_BYTES


def test_failure_is_deterministic_redacted(valid_files, capsys):
    input_doc, _, paths, _ = valid_files; input_doc["secretValue"] = "PRIVATE KEY AUDIO TRANSCRIPT"; rewrite(paths["input"], input_doc)
    assert run_main(paths) == 1; first = capsys.readouterr().out; assert run_main(paths) == 1; second = capsys.readouterr().out
    assert first == second and all(word not in first for word in ("PRIVATE", "AUDIO", "TRANSCRIPT", "Traceback"))
