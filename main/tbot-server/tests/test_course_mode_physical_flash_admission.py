import hashlib
import importlib
import json
import os
import shutil
import stat
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER / "scripts"))
admission = importlib.import_module("course_mode_physical_flash_admission")
software_snapshot = importlib.import_module("course_mode_software_evidence_snapshot")
REAL_VERIFY_SOFTWARE_AUDIT = software_snapshot.verify_current_software_audit
NOW = datetime(2026, 9, 8, 8, 0, 0, tzinfo=timezone.utc)
SESSION_ID = "cab43f0d-62dc-49c4-9d30-e9630d195a44"
COURSE_ID = "a17792f6-8d86-4ad1-a6f3-77663b4d4674"
COURSE_KEY = "english-6month-4-6"
APP_SHA = "8531432b18eef2d656c5afb2574a2b73c2458679d355d6ebcb47828086b0dc95"
MANIFEST_SHA = "39d1538b602829d472d017d78950baf46e8035bab1539e4a99871925732e2809"
FIRMWARE_SHA = "91c86074df5a17d5b684a6c28ea57b727abe3a01"
SAFETY_KEYS = (
    "adultObserverPresent", "motionAreaClearAndSecured", "immediatePowerIsolationReachable",
    "stablePower", "stableLan", "evidenceCaptureReady", "soleUsbSerialLease",
    "preserveBootloader", "preservePartitionTable", "preserveNvs", "preserveOtaData",
    "preservePhyInit", "preserveReserved", "preserveGeneratedAssets",
)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _write_secure(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        path.chmod(0o644)
    path.write_bytes(data)
    path.chmod(0o444)


def _software_audit_report(candidate_path, evidence_root, paths, candidate_id):
    candidate_subject, findings = software_snapshot.capture_candidate(candidate_path)
    assert findings == () and candidate_subject is not None
    capture = software_snapshot.capture_snapshot(
        candidate_subject,
        evidence_root,
        excluded_evidence_paths=(paths["output"],),
        evidence_scan_policies={
            paths["input"]: "physical-admission-top-level-session-id.v1",
            paths["identity"]: "physical-admission-top-level-session-id.v1",
        },
    )
    assert capture.findings == ()
    subjects = software_snapshot.subject_manifest(capture.subjects)
    by_key = {(subject["scope"], subject["path"]): subject for subject in subjects}
    identity = json.loads(paths["identity"].read_bytes())
    relative = {
        key: path.relative_to(evidence_root).as_posix()
        for key, path in paths.items()
        if key in {"input", "identity", "signature"}
    }
    return {
        "schemaVersion": software_snapshot.SCHEMA_VERSION,
        "validator": software_snapshot.VALIDATOR,
        "candidateId": candidate_id,
        "snapshot": {
            "algorithm": "sha256",
            "id": software_snapshot.snapshot_id(subjects),
            "subjects": subjects,
        },
        "checkedArchiveMemberCount": 0,
        "checkedFileCount": len(subjects),
        "checks": dict(software_snapshot.EXPECTED_CHECKS),
        "findings": [],
        "status": "pass",
        "admissionBinding": {
            "candidateSha256": by_key[("candidate", candidate_path.name)]["sha256"],
            "inputSha256": by_key[("evidence", relative["input"])]["sha256"],
            "expectedIdentitySha256": by_key[("evidence", relative["identity"])]["sha256"],
            "signatureSha256": by_key[("evidence", relative["signature"])]["sha256"],
            "signedCanonicalIdentitySha256": hashlib.sha256(
                software_snapshot.canonical_json_bytes(identity)
            ).hexdigest(),
            "sessionPolicy": "top-level-canonical-uuid.v1",
        },
    }


def _install_software_audit(candidate_path, evidence_root, paths, candidate_id):
    report = _software_audit_report(candidate_path, evidence_root, paths, candidate_id)
    audit_path = evidence_root / software_snapshot.OUTPUT_NAME
    _write_secure(audit_path, software_snapshot.canonical_json_bytes(report) + b"\n")
    return audit_path, report


def _use_real_software_audit(monkeypatch):
    monkeypatch.setattr(
        admission.software_snapshot,
        "verify_current_software_audit",
        REAL_VERIFY_SOFTWARE_AUDIT,
    )


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
        "backend": {"reference": "local/tbot-backend:41", "id": "sha256:" + "3" * 64, "platform": "linux/amd64", "provenanceLabels": {"org.opencontainers.image.revision": "2" * 40, "org.opencontainers.image.source": "git@example/backend.git"}},
        "web": {"reference": "local/tbot-server-web:41", "id": "sha256:" + "4" * 64, "platform": "linux/amd64", "provenanceLabels": {"org.opencontainers.image.revision": "1" * 40, "org.opencontainers.image.source": "git@example/admin.git"}},
    }
    firmware = {"board": "LCDWiki ES3C35P", "target": "esp32s3", "gitSha": FIRMWARE_SHA, "app": {"path": "/opt/tbot/course-mode/app.bin", "sha256": APP_SHA, "bytes": 3863248, "offset": "0x20000", "partitionBytes": 4128768}, "manifest": {"path": "/opt/tbot/course-mode/manifest.json", "sha256": MANIFEST_SHA}}
    actual = {"candidateId": "course-mode-2026-09-08.41", "createdAt": "2026-09-08T07:00:00Z", "expiresAt": "2026-09-08T09:00:00Z", "course": {"courseId": COURSE_ID, "courseKey": COURSE_KEY}, "repositories": {"adminEsp": repos["admin"], "backend": repos["backend"], "firmware": repos["firmware"]}, "images": {"lessonStudioBackend": {"reference": images["backend"]["reference"], "id": images["backend"]["id"]}, "lessonStudioWeb": {"reference": images["web"]["reference"], "id": images["web"]["id"]}}, "firmware": {"appPath": firmware["app"]["path"], "appOffset": "0x20000", "appBytes": 3863248, "appSha256": APP_SHA, "partitionBytes": 4128768, "evidenceManifestPath": firmware["manifest"]["path"], "evidenceManifestSha256": MANIFEST_SHA}, "tools": {"physicalAdmission": {name: str(path) for name, path in admission_paths.items()}}, "evidenceRoot": str(tmp_path)}
    candidate_path.write_bytes(canonical(actual))
    candidate = {"candidateId": actual["candidateId"], "courseId": COURSE_ID, "courseKey": COURSE_KEY, "createdAt": actual["createdAt"], "expiresAt": actual["expiresAt"], "path": str(candidate_path), "sha256": hashlib.sha256(candidate_path.read_bytes()).hexdigest(), "repositories": repos, "images": images, "firmware": firmware}
    robot = {"mac": "14:c1:9f:d1:ac:20", "board": "LCDWiki ES3C35P", "target": "esp32s3", "serialPath": "/dev/cu.usbmodem1101", "exactlyOneRobot": True}
    identity = {"schemaVersion": 1, "sessionId": SESSION_ID, "candidate": candidate, "partitionTable": partitions(), "robot": robot, "signer": {"algorithm": "ed25519", "fingerprint": admission.PINNED_APPROVAL_KEY_FINGERPRINT}}
    input_doc = {"schemaVersion": 1, "sessionId": SESSION_ID, "checkedAt": "2026-09-08T08:00:00Z", "candidate": deepcopy(candidate), "robot": deepcopy(robot), "serialLease": {"soleLeaseConfirmed": True, "competingProcessesStopped": True, "devicePath": "/dev/cu.usbmodem1101", "discoveredDevices": ["/dev/cu.usbmodem1101"], "holderPids": [], "inventoryMethod": "lstat-glob-lsof-v1"}, "flashPlan": {"operation": {"operation": "write_flash", "offset": "0x20000", "imageSha256": APP_SHA, "imageBytes": 3863248, "after": "no-reset", "eraseChip": False, "mergedImage": False}, "protectedPartitions": [p for p in partitions() if p["protected"]], "preserveProtectedPartitions": True}, "safety": {name: True for name in SAFETY_KEYS}}
    return input_doc, identity, key.sign(canonical(identity)), actual


@pytest.fixture
def valid_files(tmp_path, monkeypatch):
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    monkeypatch.setattr(admission, "PINNED_APPROVAL_PUBLIC_KEY_RAW", public)
    monkeypatch.setattr(admission, "PINNED_APPROVAL_KEY_FINGERPRINT", hashlib.sha256(public).hexdigest())
    input_doc, identity, _, _ = documents(tmp_path, key)
    identity["signer"]["fingerprint"] = hashlib.sha256(public).hexdigest()
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    paths = {"input": evidence_root / "input.json", "identity": evidence_root / "identity.json", "signature": evidence_root / "identity.sig", "output": evidence_root / "result.json"}
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
    actual["evidenceRoot"] = str(evidence_root)
    firmware = actual["firmware"]
    firmware.update(appBytes=3863248, appSha256=APP_SHA, partitionBytes=4128768, freeBytes=4128768-3863248, evidenceManifestSha256=MANIFEST_SHA)
    repository_binding = {"admin": actual["repositories"]["adminEsp"], "backend": actual["repositories"]["backend"], "firmware": actual["repositories"]["firmware"]}
    image_binding = {
        "backend": {**actual["images"]["lessonStudioBackend"], "platform": "linux/amd64", "provenanceLabels": {"org.opencontainers.image.revision": repository_binding["backend"]["sha"], "org.opencontainers.image.source": repository_binding["backend"]["remoteUrl"]}},
        "web": {**actual["images"]["lessonStudioWeb"], "platform": "linux/amd64", "provenanceLabels": {"org.opencontainers.image.revision": repository_binding["admin"]["sha"], "org.opencontainers.image.source": repository_binding["admin"]["remoteUrl"]}},
    }
    candidate_path = tmp_path / "candidate.json"
    candidate_path.chmod(0o644)
    candidate_path.write_bytes(canonical(actual))
    candidate_path.chmod(0o444)
    binding = {"candidateId": actual["candidateId"], "courseId": COURSE_ID, "courseKey": COURSE_KEY, "createdAt": actual["createdAt"], "expiresAt": actual["expiresAt"], "path": str(candidate_path), "sha256": hashlib.sha256(candidate_path.read_bytes()).hexdigest(), "repositories": repository_binding, "images": image_binding, "firmware": {"board": "LCDWiki ES3C35P", "target": "esp32s3", "gitSha": FIRMWARE_SHA, "app": {"path": firmware["appPath"], "sha256": APP_SHA, "bytes": 3863248, "offset": "0x20000", "partitionBytes": 4128768}, "manifest": {"path": firmware["evidenceManifestPath"], "sha256": MANIFEST_SHA}}}
    input_doc["candidate"] = deepcopy(binding)
    identity["candidate"] = deepcopy(binding)
    real_git = admission.candidate_manifest._git
    firmware_root = Path(actual["repositories"]["firmware"]["path"])
    monkeypatch.setattr(admission.candidate_manifest, "_git", lambda root, *args: FIRMWARE_SHA + "\n" if root == firmware_root and args[-2:] == ("rev-parse", "HEAD") or root == firmware_root and args[-3:] == ("rev-parse", "--verify", "HEAD^{commit}") else real_git(root, *args))
    manifest_content = json.loads(Path(firmware["evidenceManifestPath"]).read_text())
    manifest_content.update(createdAt="2026-09-08T06:00:00Z", sourceCommit=FIRMWARE_SHA)
    manifest_content["app"].update(bytes=3863248, sha256=APP_SHA, offset="0x20000")
    manifest_content["partition"].update(bytes=4128768, freeBytes=4128768-3863248, freePercent=round((4128768-3863248)/4128768*100, 6))
    manifest_bytes = canonical(manifest_content)
    real_descriptor = admission.candidate_manifest.secure_regular_descriptor
    def descriptor(path, limit, **kwargs):
        if path == Path(firmware["evidenceManifestPath"]): return {"sha256": MANIFEST_SHA, "bytes": len(manifest_bytes), "content": manifest_bytes}, None
        if path == Path(firmware["appPath"]): return {"sha256": APP_SHA, "bytes": 3863248}, None
        return real_descriptor(path, limit, **kwargs)
    monkeypatch.setattr(admission.candidate_manifest, "secure_regular_descriptor", descriptor)
    monkeypatch.setattr(admission.candidate_manifest, "_validate_container_tool", lambda name, value, reasons, verify_identity: Path(value["path"]))
    monkeypatch.setattr(admission.candidate_manifest, "_validate_python_test_runtime", lambda *args, **kwargs: None)
    monkeypatch.setattr(admission.candidate_manifest, "_validate_esp_idf", lambda *args, **kwargs: None)
    docker_images = {
        actual["images"]["lessonStudioBackend"]["reference"]: {"Os": "linux", "Architecture": "amd64", "Id": actual["images"]["lessonStudioBackend"]["id"], "Config": {"Labels": image_binding["backend"]["provenanceLabels"]}},
        actual["images"]["lessonStudioWeb"]["reference"]: {"Os": "linux", "Architecture": "amd64", "Id": actual["images"]["lessonStudioWeb"]["id"], "Config": {"Labels": image_binding["web"]["provenanceLabels"]}},
        actual["database"]["engineImage"]: {"Id": actual["database"]["engineImageId"], "Config": {"Labels": {}}},
    }
    monkeypatch.setattr(admission.candidate_manifest, "_docker_image_descriptor", lambda reference, _executable: docker_images.get(reference))
    paths["input"].write_bytes(canonical(input_doc)); paths["identity"].write_bytes(canonical(identity)); paths["signature"].write_bytes(key.sign(canonical(identity)))
    for path in (Path(input_doc["candidate"]["path"]), paths["input"], paths["identity"], paths["signature"]): path.chmod(0o444)
    monkeypatch.setattr(admission, "utc_now", lambda: NOW)
    monkeypatch.setattr(admission, "collect_serial_inventory", lambda: (["/dev/cu.usbmodem1101"], [], None))
    monkeypatch.setattr(
        admission,
        "software_snapshot",
        SimpleNamespace(
            verify_current_software_audit=lambda *_args, **_kwargs: (
                software_snapshot.VerifiedSoftwareAudit("a" * 64, "b" * 64, (1,), {}),
                (),
            )
        ),
        raising=False,
    )
    return input_doc, identity, paths, actual


def run_main(paths):
    return admission.main(["--input", str(paths["input"]), "--output", str(paths["output"]), "--expected-identity", str(paths["identity"]), "--expected-identity-signature", str(paths["signature"])])


def test_accepts_reviewed_m0_firmware_and_staging_platform(tmp_path):
    doc, _, _, _ = documents(tmp_path, Ed25519PrivateKey.generate())
    binding = doc["candidate"]
    source = "91c86074df5a17d5b684a6c28ea57b727abe3a01"
    binding["repositories"]["firmware"]["sha"] = source
    binding["firmware"]["gitSha"] = source
    binding["firmware"]["app"].update(
        sha256="8531432b18eef2d656c5afb2574a2b73c2458679d355d6ebcb47828086b0dc95",
        bytes=3863248,
    )
    binding["firmware"]["manifest"]["sha256"] = "39d1538b602829d472d017d78950baf46e8035bab1539e4a99871925732e2809"
    for image in binding["images"].values():
        image["platform"] = "linux/amd64"
    reasons = set()
    admission._validate_candidate_shape(binding, reasons)
    assert reasons == set()


@pytest.mark.parametrize("image_name", ["lessonStudioBackend", "lessonStudioWeb"])
@pytest.mark.parametrize("platform", [
    {}, {"Os": "linux", "Architecture": "arm64"},
    {"Os": "windows", "Architecture": "amd64"},
    {"Os": "linux", "Architecture": None},
])
def test_observed_image_platform_must_match_staging(valid_files, monkeypatch, capsys, image_name, platform):
    _, _, paths, actual = valid_files
    target = actual["images"][image_name]["reference"]
    original = admission.candidate_manifest._docker_image_descriptor

    def descriptor(reference, executable):
        observed = deepcopy(original(reference, executable))
        if reference == target:
            observed.pop("Os", None)
            observed.pop("Architecture", None)
            observed.update(platform)
        return observed

    monkeypatch.setattr(admission.candidate_manifest, "_docker_image_descriptor", descriptor)
    assert admission._candidate_external_binding(actual, observe_images=True) is None
    assert run_main(paths) == 1
    assert not paths["output"].exists()
    assert "candidate.external.changed" in json.loads(capsys.readouterr().out)["reasons"]


def rewrite(path, value):
    path.chmod(0o644); path.write_bytes(canonical(value)); path.chmod(0o444)


def resign(paths, input_doc, identity):
    rewrite(paths["input"], input_doc)
    rewrite(paths["identity"], identity)
    paths["signature"].chmod(0o644)
    paths["signature"].write_bytes(paths["key"].sign(canonical(identity)))
    paths["signature"].chmod(0o444)


def _enable_real_software_audit(valid_files, monkeypatch):
    input_doc, _, paths, actual = valid_files
    evidence_root = Path(actual["evidenceRoot"])
    other_evidence = evidence_root / "other-evidence.json"
    _write_secure(other_evidence, canonical({"status": "pass"}) + b"\n")
    candidate_path = Path(input_doc["candidate"]["path"])
    audit_path, audit = _install_software_audit(
        candidate_path,
        evidence_root,
        paths,
        actual["candidateId"],
    )
    _use_real_software_audit(monkeypatch)
    return candidate_path, evidence_root, other_evidence, audit_path, audit


def _inventory_must_not_run(monkeypatch):
    def fail_inventory():
        raise AssertionError("serial inventory ran before software audit verification")

    monkeypatch.setattr(admission, "collect_serial_inventory", fail_inventory)


@pytest.mark.parametrize(
    ("mutation", "expected_reason"),
    [
        ("missing", "softwareAudit.missing"),
        ("schema-v1", "softwareAudit.schema"),
        ("malformed", "softwareAudit.json"),
        ("snapshot-id", "softwareAudit.snapshot"),
        ("candidate-id", "softwareAudit.candidate"),
        ("candidate-hash", "softwareAudit.admissionBinding"),
        ("input-hash", "softwareAudit.admissionBinding"),
        ("identity-hash", "softwareAudit.admissionBinding"),
        ("signature-hash", "softwareAudit.admissionBinding"),
    ],
)
def test_software_audit_report_failures_block_before_serial_inventory(
    valid_files, monkeypatch, capsys, mutation, expected_reason,
):
    _, _, _, audit_path, audit = _enable_real_software_audit(valid_files, monkeypatch)
    if mutation == "missing":
        audit_path.unlink()
    elif mutation == "malformed":
        _write_secure(audit_path, b"{")
    else:
        changed = deepcopy(audit)
        if mutation == "schema-v1":
            changed["schemaVersion"] = 1
        elif mutation == "snapshot-id":
            changed["snapshot"]["id"] = "0" * 64
        elif mutation == "candidate-id":
            changed["candidateId"] = "course-mode-wrong"
        else:
            field = {
                "candidate-hash": "candidateSha256",
                "input-hash": "inputSha256",
                "identity-hash": "expectedIdentitySha256",
                "signature-hash": "signatureSha256",
            }[mutation]
            changed["admissionBinding"][field] = "0" * 64
        _write_secure(audit_path, software_snapshot.canonical_json_bytes(changed) + b"\n")
    _inventory_must_not_run(monkeypatch)

    assert run_main(valid_files[2]) == 1
    failure = json.loads(capsys.readouterr().out)
    assert expected_reason in failure["reasons"]
    assert failure["physicalActionsPerformed"] is False
    assert failure["serialOpened"] is False
    assert not valid_files[2]["output"].exists()


@pytest.mark.parametrize(
    "mutation",
    [
        "candidate-change",
        "input-change",
        "identity-change",
        "evidence-change",
        "evidence-add",
        "evidence-remove",
        "evidence-rename",
        "evidence-writable",
        "evidence-hardlink",
        "evidence-symlink",
    ],
)
def test_software_audit_tree_drift_blocks_before_serial_inventory(
    valid_files, monkeypatch, capsys, mutation,
):
    candidate_path, evidence_root, other_evidence, _, _ = _enable_real_software_audit(
        valid_files, monkeypatch
    )
    _, _, paths, actual = valid_files
    if mutation == "candidate-change":
        changed = deepcopy(actual)
        changed["unexpected"] = True
        rewrite(candidate_path, changed)
        digest = hashlib.sha256(candidate_path.read_bytes()).hexdigest()
        input_doc, identity, _, _ = valid_files
        input_doc["candidate"]["sha256"] = digest
        identity["candidate"]["sha256"] = digest
        resign(paths, input_doc, identity)
    elif mutation == "input-change":
        _write_secure(paths["input"], paths["input"].read_bytes() + b" ")
    elif mutation == "identity-change":
        _write_secure(paths["identity"], paths["identity"].read_bytes() + b" ")
    elif mutation == "evidence-change":
        _write_secure(other_evidence, b'{"status":"changed"}\n')
    elif mutation == "evidence-add":
        _write_secure(evidence_root / "added.json", b"{}\n")
    elif mutation == "evidence-remove":
        other_evidence.unlink()
    elif mutation == "evidence-rename":
        other_evidence.rename(evidence_root / "renamed.json")
    elif mutation == "evidence-writable":
        other_evidence.chmod(0o664)
    elif mutation == "evidence-hardlink":
        os.link(other_evidence, evidence_root / "hardlink.json")
    elif mutation == "evidence-symlink":
        (evidence_root / "symlink.json").symlink_to(other_evidence)
    _inventory_must_not_run(monkeypatch)

    assert run_main(paths) == 1
    failure = json.loads(capsys.readouterr().out)
    assert any(reason.startswith("softwareAudit.") for reason in failure["reasons"])
    assert failure["physicalActionsPerformed"] is False
    assert failure["serialOpened"] is False
    assert not paths["output"].exists()


def test_software_audit_identity_is_rechecked_before_result_commit(
    valid_files, monkeypatch, capsys, tmp_path,
):
    _, _, _, audit_path, _ = _enable_real_software_audit(valid_files, monkeypatch)
    original = audit_path.read_bytes()
    calls = 0

    def replace_after_initial_verify(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            audit_path.rename(tmp_path / "old-software-audit.json")
            _write_secure(audit_path, original)
        return REAL_VERIFY_SOFTWARE_AUDIT(*args, **kwargs)

    monkeypatch.setattr(
        admission.software_snapshot,
        "verify_current_software_audit",
        replace_after_initial_verify,
    )

    assert run_main(valid_files[2]) == 1
    assert calls >= 2
    assert not valid_files[2]["output"].exists()
    failure = json.loads(capsys.readouterr().out)
    assert failure["reasons"] == ["softwareAudit.metadata"]
    assert failure["physicalActionsPerformed"] is False
    assert failure["serialOpened"] is False


def test_software_audit_evidence_is_rechecked_before_result_commit(
    valid_files, monkeypatch, capsys,
):
    _, _, other_evidence, _, _ = _enable_real_software_audit(valid_files, monkeypatch)
    calls = 0

    def change_evidence_before_commit_recheck(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            _write_secure(other_evidence, b'{"status":"changed"}\n')
        return REAL_VERIFY_SOFTWARE_AUDIT(*args, **kwargs)

    monkeypatch.setattr(
        admission.software_snapshot,
        "verify_current_software_audit",
        change_evidence_before_commit_recheck,
    )

    assert run_main(valid_files[2]) == 1
    assert calls >= 2
    assert not valid_files[2]["output"].exists()
    failure = json.loads(capsys.readouterr().out)
    assert failure["reasons"] == ["softwareAudit.stale"]
    assert failure["physicalActionsPerformed"] is False
    assert failure["serialOpened"] is False


def test_software_audit_binding_is_published_in_pass_result(valid_files, monkeypatch):
    _, _, _, audit_path, audit = _enable_real_software_audit(valid_files, monkeypatch)

    assert run_main(valid_files[2]) == 0
    result = json.loads(valid_files[2]["output"].read_text())
    assert result["softwareAuditSha256"] == hashlib.sha256(audit_path.read_bytes()).hexdigest()
    assert result["softwareSnapshotId"] == audit["snapshot"]["id"]


def test_valid_signed_admission(valid_files):
    input_doc, identity, paths, _ = valid_files
    assert run_main(paths) == 0
    result = json.loads(paths["output"].read_text())
    assert result["status"] == "pass" and result["reasons"] == []
    assert result["physicalActionsPerformed"] is False and result["serialOpened"] is False
    assert result["candidateId"] == input_doc["candidate"]["candidateId"] and result["sessionId"] == identity["sessionId"]
    metadata = paths["output"].stat()
    assert stat.S_IMODE(metadata.st_mode) == 0o444
    assert metadata.st_nlink == 1 and metadata.st_uid == admission.OPERATOR_UID


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
    monkeypatch.setattr(admission, "_run_lsof", lambda command: calls.append(command) or (0, b"p321\n", b"", None))
    assert admission.collect_serial_inventory() == (["/dev/cu.usbmodem1101"], [321], None)
    assert calls[0] == ["/usr/sbin/lsof", "-nP", "-t", "--", "/dev/cu.usbmodem1101"]


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


@pytest.mark.parametrize("attack", ["failure", "no-effect"])
def test_publish_chmod_failure_removes_exact_created_output(
    valid_files, monkeypatch, capsys, attack,
):
    _, _, paths, _ = valid_files
    real_fchmod = admission.os.fchmod

    def hostile_fchmod(fd, mode):
        if attack == "failure":
            raise OSError("secret chmod failure")
        if attack == "no-effect":
            return None
        return real_fchmod(fd, mode)

    monkeypatch.setattr(admission.os, "fchmod", hostile_fchmod)

    assert run_main(paths) == 1
    assert not paths["output"].exists()
    output = capsys.readouterr().out
    assert json.loads(output)["reasons"] == ["output.path"]
    assert "secret" not in output


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
    (lambda c: c["images"]["web"].__setitem__("platform", "linux/arm64"), "candidate.images.web"),
    (lambda c: c["images"]["backend"]["provenanceLabels"].__setitem__("org.opencontainers.image.source", "wrong"), "candidate.images.backend"),
    (lambda c: c["firmware"].__setitem__("board", "wrong"), "candidate.firmware"),
    (lambda c: c["firmware"].__setitem__("target", "esp32"), "candidate.firmware"),
    (lambda c: c["firmware"]["app"].__setitem__("sha256", "9" * 64), "candidate.firmware.app"),
    (lambda c: c["firmware"]["app"].__setitem__("offset", "0x0"), "candidate.firmware.app"),
    (lambda c: c["repositories"]["firmware"].__setitem__("sha", "b54c6ca33e9beb3747b44feceb7c64fea33fe1d6"), "candidate.repositories.firmware"),
    (lambda c: c["firmware"].__setitem__("gitSha", "b54c6ca33e9beb3747b44feceb7c64fea33fe1d6"), "candidate.firmware"),
    (lambda c: c["firmware"]["app"].__setitem__("sha256", "782020e2f8ac44bd197f57e9e126c196286a8223005de1e425f8290f12b28dff"), "candidate.firmware.app"),
    (lambda c: c["firmware"]["app"].__setitem__("bytes", 3637200), "candidate.firmware.app"),
    (lambda c: c["firmware"]["manifest"].__setitem__("sha256", "23b70849b6b65901b01b38e91279455a2e5e8d13989438e2e0bbfa707602aa65"), "candidate.firmware.manifest"),
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
    monkeypatch.setattr(admission, "_run_lsof", lambda _command: (1, b"", b"", None))
    assert admission.collect_serial_inventory()[2] == "inventory_changed"


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


def _replace_same_uid(path):
    original = path.read_bytes()
    mode = stat.S_IMODE(path.stat().st_mode)
    backup = path.with_name(path.name + ".replaced")
    path.rename(backup)
    path.write_bytes(original)
    path.chmod(mode)


@pytest.mark.parametrize("field", ["appPath", "evidenceManifestPath"])
def test_external_firmware_replacement_before_publish_leaves_no_result(
    valid_files, monkeypatch, capsys, field,
):
    _, _, paths, actual = valid_files
    target = Path(actual["firmware"][field])
    calls = 0

    def inventory():
        nonlocal calls
        calls += 1
        if calls == 2:
            _replace_same_uid(target)
        return [admission.SERIAL_PATH], [], None

    monkeypatch.setattr(admission, "collect_serial_inventory", inventory)
    assert run_main(paths) == 1
    assert not paths["output"].exists()
    assert json.loads(capsys.readouterr().out)["reasons"] == ["candidate.external.changed"]


@pytest.mark.parametrize("field", ["appPath", "evidenceManifestPath"])
def test_external_firmware_replacement_after_output_fsync_removes_result(
    valid_files, monkeypatch, capsys, field,
):
    _, _, paths, actual = valid_files
    target = Path(actual["firmware"][field])
    real_fsync = admission.os.fsync
    replaced = False

    def replace_after_fsync(fd):
        nonlocal replaced
        result = real_fsync(fd)
        if not replaced and stat.S_ISREG(os.fstat(fd).st_mode):
            replaced = True
            _replace_same_uid(target)
        return result

    monkeypatch.setattr(admission.os, "fsync", replace_after_fsync)
    assert run_main(paths) == 1
    assert not paths["output"].exists()
    assert json.loads(capsys.readouterr().out)["reasons"] == ["output.path"]


def test_external_repository_root_replacement_before_publish_leaves_no_result(
    valid_files, monkeypatch, capsys,
):
    _, _, paths, actual = valid_files
    target = Path(actual["repositories"]["backend"]["path"])
    calls = 0

    def inventory():
        nonlocal calls
        calls += 1
        if calls == 2:
            moved = target.with_name(target.name + ".replaced")
            target.rename(moved)
            shutil.copytree(moved, target)
        return [admission.SERIAL_PATH], [], None

    monkeypatch.setattr(admission, "collect_serial_inventory", inventory)
    assert run_main(paths) == 1
    assert not paths["output"].exists()
    assert json.loads(capsys.readouterr().out)["reasons"] == ["candidate.external.changed"]


@pytest.mark.parametrize("field,value", [("Id", "sha256:" + "9" * 64), ("Os", "windows"), ("Architecture", "arm64")])
def test_external_image_observation_change_before_publish_leaves_no_result(
    valid_files, monkeypatch, capsys, field, value,
):
    _, _, paths, actual = valid_files
    target = actual["images"]["lessonStudioWeb"]["reference"]
    original = admission.candidate_manifest._docker_image_descriptor
    inventory_count = 0

    def descriptor(reference, executable):
        observed = original(reference, executable)
        if inventory_count >= 2 and reference == target:
            return {**observed, field: value}
        return observed

    def inventory():
        nonlocal inventory_count
        inventory_count += 1
        return [admission.SERIAL_PATH], [], None

    monkeypatch.setattr(admission.candidate_manifest, "_docker_image_descriptor", descriptor)
    monkeypatch.setattr(admission, "collect_serial_inventory", inventory)
    assert run_main(paths) == 1
    assert not paths["output"].exists()
    assert json.loads(capsys.readouterr().out)["reasons"] == ["candidate.external.changed"]


def test_serial_holder_appearing_during_external_commit_validation_removes_result(
    valid_files, monkeypatch, capsys,
):
    _, _, paths, _ = valid_files
    occupied = False
    original = admission._candidate_external_still_bound

    def validate_external(*args, **kwargs):
        nonlocal occupied
        if paths["output"].exists():
            occupied = True
        return True

    monkeypatch.setattr(admission, "_candidate_external_still_bound", validate_external)
    monkeypatch.setattr(
        admission, "collect_serial_inventory",
        lambda: ([admission.SERIAL_PATH], [321] if occupied else [], None),
    )

    assert run_main(paths) == 1
    assert not paths["output"].exists()
    assert json.loads(capsys.readouterr().out)["reasons"] == ["output.path"]


def test_serial_device_change_after_output_fsync_removes_result(
    valid_files, monkeypatch, capsys,
):
    _, _, paths, _ = valid_files
    changed = False
    real_fsync = admission.os.fsync

    def change_after_fsync(fd):
        nonlocal changed
        result = real_fsync(fd)
        if stat.S_ISREG(os.fstat(fd).st_mode):
            changed = True
        return result

    monkeypatch.setattr(admission.os, "fsync", change_after_fsync)
    monkeypatch.setattr(
        admission, "collect_serial_inventory",
        lambda: ([] if changed else [admission.SERIAL_PATH], [], None),
    )

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


def test_expiry_reached_immediately_before_publish_leaves_no_result(valid_files, monkeypatch, capsys):
    _, _, paths, _ = valid_files
    moments = iter((NOW, datetime(2026, 9, 8, 9, 0, 0, tzinfo=timezone.utc)))
    monkeypatch.setattr(admission, "utc_now", lambda: next(moments))
    assert run_main(paths) == 1
    assert not paths["output"].exists()
    assert "candidate.time" in json.loads(capsys.readouterr().out)["reasons"]


def test_freshness_expires_during_output_fsync_removes_result(valid_files, monkeypatch, capsys):
    _, _, paths, _ = valid_files
    stale = NOW.replace(minute=6)
    moments = iter((NOW, NOW, stale, stale))
    monkeypatch.setattr(admission, "utc_now", lambda: next(moments))
    assert run_main(paths) == 1
    assert not paths["output"].exists()
    assert json.loads(capsys.readouterr().out)["reasons"] == ["output.path"]


def test_second_device_appearing_after_lsof_is_rejected(monkeypatch):
    calls = 0
    def devices(_pattern):
        nonlocal calls
        calls += 1
        return [admission.SERIAL_PATH] if calls == 1 else [admission.SERIAL_PATH, "/dev/cu.usbserial2"]
    def metadata(path):
        inode = 2 if path == admission.SERIAL_PATH else 4
        return type("S", (), {"st_mode": stat.S_IFCHR, "st_dev": 1, "st_ino": inode, "st_rdev": inode + 1})()
    monkeypatch.setattr(admission.glob, "glob", devices)
    monkeypatch.setattr(admission, "_device_lstat", metadata)
    monkeypatch.setattr(admission, "_device_stat", metadata)
    monkeypatch.setattr(admission, "_trusted_lsof", lambda: True)
    monkeypatch.setattr(admission, "_run_lsof", lambda _command: (1, b"", b"", None))
    assert admission.collect_serial_inventory()[2] == "inventory_changed"


def test_inventory_repeated_immediately_before_publish(valid_files, monkeypatch, capsys):
    _, _, paths, _ = valid_files
    calls = 0
    def inventory():
        nonlocal calls
        calls += 1
        return ([admission.SERIAL_PATH], [], None) if calls == 1 else ([admission.SERIAL_PATH, "/dev/cu.usbserial2"], [], None)
    monkeypatch.setattr(admission, "collect_serial_inventory", inventory)
    assert run_main(paths) == 1
    assert not paths["output"].exists()
    assert "serial.inventory" in json.loads(capsys.readouterr().out)["reasons"]


@pytest.mark.parametrize("returncode,stderr,reason", [
    (1, "lsof: WARNING: can't stat() fuse file system", "visibility"),
    (1, "lsof: status error on /dev/cu.usbmodem1101: Permission denied", "visibility"),
    (2, "lsof: illegal status", "exit"),
])
def test_lsof_diagnostics_and_visibility_errors_fail_closed(monkeypatch, returncode, stderr, reason):
    device = type("S", (), {"st_mode": stat.S_IFCHR, "st_dev": 1, "st_ino": 2, "st_rdev": 3})()
    monkeypatch.setattr(admission, "_enumerate_devices", lambda: {admission.SERIAL_PATH: (1, 2, 3, stat.S_IFCHR)})
    monkeypatch.setattr(admission, "_trusted_lsof", lambda: True)
    monkeypatch.setattr(admission, "_run_lsof", lambda _command: (returncode, b"", stderr.encode(), None))
    assert admission.collect_serial_inventory()[2] == reason


@pytest.mark.parametrize("mutation,reason", [
    (lambda d: d["robot"].__setitem__("exactlyOneRobot", 1), "robot.identity"),
    (lambda d: d["flashPlan"]["operation"].__setitem__("eraseChip", 0), "flashPlan.operation"),
    (lambda d: d["flashPlan"]["operation"].__setitem__("mergedImage", 0), "flashPlan.operation"),
    (lambda d: d["flashPlan"]["operation"].__setitem__("imageBytes", True), "flashPlan.operation"),
    (lambda d: d["flashPlan"]["protectedPartitions"][0].__setitem__("protected", 1), "flashPlan.protectedPartitions"),
    (lambda d: d["safety"].__setitem__("stablePower", 1), "safety.stablePower"),
])
def test_bool_int_type_confusion_is_rejected(valid_files, mutation, reason, capsys):
    input_doc, _, paths, _ = valid_files
    mutation(input_doc)
    rewrite(paths["input"], input_doc)
    assert run_main(paths) == 1
    assert reason in json.loads(capsys.readouterr().out)["reasons"]


def test_output_close_failure_unlinks_only_created_result(valid_files, monkeypatch, capsys):
    _, _, paths, _ = valid_files
    real_close = admission.os.close
    output_fd = None
    real_open = admission.os.open
    def track_open(*args, **kwargs):
        nonlocal output_fd
        fd = real_open(*args, **kwargs)
        if args and args[0] == paths["output"].name and kwargs.get("dir_fd") is not None:
            output_fd = fd
        return fd
    def fail_close(fd):
        if fd == output_fd:
            real_close(fd)
            raise OSError("injected close failure")
        return real_close(fd)
    monkeypatch.setattr(admission.os, "open", track_open)
    monkeypatch.setattr(admission.os, "close", fail_close)
    assert run_main(paths) == 1
    assert not paths["output"].exists()
    assert json.loads(capsys.readouterr().out)["reasons"] == ["output.path"]


@pytest.mark.parametrize("program,expected_error,stderr", [
    ("import time;time.sleep(1)", "timeout", b""),
    ("print('x'*100000)", "output", b""),
    ("import sys;sys.stderr.write('permission denied')", None, b"permission denied"),
])
def test_lsof_runner_bounds_both_streams(monkeypatch, program, expected_error, stderr):
    monkeypatch.setattr(admission, "LSOF_TIMEOUT_SECONDS", 0.05)
    returncode, _stdout, observed_stderr, error = admission._run_lsof([sys.executable, "-c", program])
    assert error == expected_error
    if stderr:
        assert returncode == 0 and observed_stderr == stderr


@pytest.mark.parametrize("field,value", [("bytes", 3863248.0), ("partitionBytes", 4128768.0)])
def test_signed_firmware_integer_fields_reject_equal_floats(valid_files, field, value, capsys):
    input_doc, identity, paths, actual = valid_files
    input_doc["candidate"]["firmware"]["app"][field] = value
    identity["candidate"]["firmware"]["app"][field] = value
    actual_field = "appBytes" if field == "bytes" else field
    actual["firmware"][actual_field] = value
    candidate_path = Path(input_doc["candidate"]["path"])
    rewrite(candidate_path, actual)
    digest = hashlib.sha256(candidate_path.read_bytes()).hexdigest()
    input_doc["candidate"]["sha256"] = digest
    identity["candidate"]["sha256"] = digest
    resign(paths, input_doc, identity)
    assert run_main(paths) == 1
    assert "candidate.firmware.app" in json.loads(capsys.readouterr().out)["reasons"]
