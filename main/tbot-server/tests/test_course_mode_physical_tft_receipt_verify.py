import hashlib
import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/course_mode_physical_tft_receipt_verify.py"
sys.path.insert(0, str(ROOT / "scripts"))


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


def _repository(root: Path) -> dict:
    return {
        "path": str(root),
        "sha": _git(root, "rev-parse", "HEAD"),
        "branch": _git(root, "branch", "--show-current"),
        "remoteUrl": _git(root, "remote", "get-url", "origin"),
        "dirtyExceptions": [],
    }


def _resign_physical_identity(candidate: dict, monkeypatch) -> None:
    import course_mode_physical_tft_preflight as preflight

    binding = candidate["tools"]["physicalEvidence"]
    repo = candidate["repositories"]["adminEsp"]
    identity_path = Path(repo["path"]) / binding["path"]
    identity_bytes = (json.dumps(binding["identity"], sort_keys=True, separators=(",", ":")) + "\n").encode()
    identity_path.write_bytes(identity_bytes)
    private_key = Ed25519PrivateKey.generate()
    public_raw = private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    fingerprint = hashlib.sha256(public_raw).hexdigest()
    signature_path = Path(repo["path"]) / binding["signaturePath"]
    signature_path.write_bytes(private_key.sign(preflight._canonical_bytes(binding["identity"])))
    binding["sha256"] = hashlib.sha256(identity_bytes).hexdigest()
    binding["signatureSha256"] = hashlib.sha256(signature_path.read_bytes()).hexdigest()
    binding["signerFingerprint"] = fingerprint
    for item in repo["dirtyExceptions"]:
        if item["path"] == binding["path"]:
            item["sha256"] = binding["sha256"]
        elif item["path"] == binding["signaturePath"]:
            item["sha256"] = binding["signatureSha256"]
    monkeypatch.setattr(preflight, "PINNED_APPROVAL_PUBLIC_KEY_RAW", public_raw)
    monkeypatch.setattr(preflight, "PINNED_APPROVAL_KEY_FINGERPRINT", fingerprint)


@pytest.fixture
def candidate(tmp_path: Path, monkeypatch) -> dict:
    import course_mode_physical_tft_preflight as preflight

    roots = {}
    for name in ("backend", "adminEsp", "firmware"):
        root = tmp_path / name
        root.mkdir()
        _git(root, "init", "-b", "candidate")
        _git(root, "config", "user.email", "candidate@example.invalid")
        _git(root, "config", "user.name", "Candidate Test")
        _git(root, "remote", "add", "origin", f"https://example.invalid/{name}.git")
        (root / "tracked.txt").write_text(name, encoding="utf-8")
        roots[name] = root
    curriculum_source = roots["backend"] / "src/lessons/course-mode/curriculum-course-mode.ts"
    curriculum_source.parent.mkdir(parents=True)
    curriculum_source.write_text("export const curriculum = 26;\n", encoding="utf-8")
    voice = roots["adminEsp"] / "main/tbot-server/tests/test_lesson_voice_output_discipline.py"
    voice.parent.mkdir(parents=True)
    voice.write_text("committed voice source\n", encoding="utf-8")
    for root in roots.values():
        _git(root, "add", ".")
        _git(root, "commit", "-m", "fixture")
    repositories = {name: _repository(root) for name, root in roots.items()}
    voice.write_text("current reviewed voice source\n", encoding="utf-8")
    repositories["adminEsp"]["dirtyExceptions"] = [
        {
            "path": str(voice.relative_to(roots["adminEsp"])),
            "sha256": hashlib.sha256(voice.read_bytes()).hexdigest(),
        }
    ]
    lesson = {"lessonId": "lesson-2", "lessonKey": "w01-greetings-politeness", "lessonVersion": 5}
    replacement = {
        "sourceLessonId": "lesson-1",
        "replacementLessonId": "lesson-2",
        "materializationReceiptSha256": "6" * 64,
        "cutoverReceiptSha256": "7" * 64,
    }
    renderer = {
        "rendererId": "teebot-lesson-renderer.v5",
        "contractIdentity": "courseCompanion.v2.contract.v1",
        "contractChecksum": "8" * 64,
        "manifestChecksum": "9" * 64,
        "assetChecksums": ["a" * 64],
    }
    device = {
        "macSuffix": "AC:20",
        "appOffset": "0x20000",
        "partitionTableSha256": "b" * 64,
        "nvsBeforeSha256": "c" * 64,
        "nvsAfterSha256": "c" * 64,
    }
    journey = {"assignmentId": "assignment-1", "lessonSessionId": "session-1", "deliveryId": "delivery-1"}
    database = {"terminalState": "COMPLETED", "completionCount": 1, "progressCount": 9}
    evidence_artifacts = {"capture.png": hashlib.sha256(b"redacted visual evidence").hexdigest()}
    backend_image = {"image": "local/backend:candidate", "imageId": "sha256:" + "4" * 64}
    firmware_identity = {
        "gitSha": repositories["firmware"]["sha"],
        "applicationSha256": "5" * 64,
        "applicationSize": 1234,
    }
    physical_identity = {
        "candidateBinding": {
            "candidateId": "course-mode-2026-08-29.1",
            "createdAt": "2026-08-29T00:00:00Z",
            "expiresAt": "2026-09-05T00:00:00Z",
            "course": {"courseId": "course-1", "courseKey": "english-6month-4-6"},
            "curriculum": {
                "sourceChecksum": hashlib.sha256(curriculum_source.read_bytes()).hexdigest(),
                "rendererId": "teebot-lesson-renderer.v5",
                "contractIdentity": "courseCompanion.v2.contract.v1",
            },
            "repositories": {name: value["sha"] for name, value in repositories.items()},
            "images": {"backend": deepcopy(backend_image)},
            "firmware": deepcopy(firmware_identity),
            "database": {
                "replacement": deepcopy(replacement),
                "journey": deepcopy(journey),
                "terminalReadback": deepcopy(database),
            },
            "protectedSource": {
                "path": str(voice.relative_to(roots["adminEsp"])),
                "repositorySha": repositories["adminEsp"]["sha"],
                "binding": "dirtyException",
                "sha256": repositories["adminEsp"]["dirtyExceptions"][0]["sha256"],
            },
        },
        "lesson": deepcopy(lesson),
        "replacement": deepcopy(replacement),
        "renderer": deepcopy(renderer),
        "device": deepcopy(device),
        "backendImage": deepcopy(backend_image),
        "firmware": deepcopy(firmware_identity),
        "journey": deepcopy(journey),
        "database": deepcopy(database),
        "evidenceArtifacts": deepcopy(evidence_artifacts),
    }
    identity_path = roots["adminEsp"] / "task-artifacts/candidate/expected-physical-identity.json"
    identity_path.parent.mkdir(parents=True)
    identity_bytes = (json.dumps(physical_identity, sort_keys=True, separators=(",", ":")) + "\n").encode()
    identity_path.write_bytes(identity_bytes)
    private_key = Ed25519PrivateKey.generate()
    public_raw = private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    fingerprint = hashlib.sha256(public_raw).hexdigest()
    signature_path = identity_path.with_suffix(".sig")
    signature_path.write_bytes(private_key.sign(preflight._canonical_bytes(physical_identity)))
    monkeypatch.setattr(preflight, "PINNED_APPROVAL_PUBLIC_KEY_RAW", public_raw)
    monkeypatch.setattr(preflight, "PINNED_APPROVAL_KEY_FINGERPRINT", fingerprint)
    repositories["adminEsp"]["dirtyExceptions"].append(
        {
            "path": str(identity_path.relative_to(roots["adminEsp"])),
            "sha256": hashlib.sha256(identity_bytes).hexdigest(),
        }
    )
    repositories["adminEsp"]["dirtyExceptions"].append(
        {
            "path": str(signature_path.relative_to(roots["adminEsp"])),
            "sha256": hashlib.sha256(signature_path.read_bytes()).hexdigest(),
        }
    )
    repositories["adminEsp"]["dirtyExceptions"].sort(key=lambda item: item["path"])
    candidate = {
        "candidateId": "course-mode-2026-08-29.1",
        "createdAt": "2026-08-29T00:00:00Z",
        "expiresAt": "2026-09-05T00:00:00Z",
        "course": {"courseId": "course-1", "courseKey": "english-6month-4-6"},
        "repositories": repositories,
        "images": {"backend": deepcopy(backend_image)},
        "firmware": deepcopy(firmware_identity),
        "database": {
            "replacement": deepcopy(replacement),
            "journey": deepcopy(journey),
            "terminalReadback": deepcopy(database),
        },
        "curriculum": {
            "courseId": "course-1",
            "courseKey": "english-6month-4-6",
            "rendererId": "teebot-lesson-renderer.v5",
            "contractIdentity": "courseCompanion.v2.contract.v1",
            "lessonCount": 26,
            "activityCount": 256,
            "pedagogyCount": 6,
            "responseClassCount": 11,
            "sourceChecksum": hashlib.sha256(curriculum_source.read_bytes()).hexdigest(),
        },
        "tools": {
            "physicalEvidence": {
                "path": str(identity_path.relative_to(roots["adminEsp"])),
                "repositorySha": repositories["adminEsp"]["sha"],
                "sha256": hashlib.sha256(identity_bytes).hexdigest(),
                "identity": physical_identity,
                "signaturePath": str(signature_path.relative_to(roots["adminEsp"])),
                "signatureSha256": hashlib.sha256(signature_path.read_bytes()).hexdigest(),
                "signerFingerprint": fingerprint,
            }
        },
        "evidenceRoot": str(tmp_path / "evidence"),
    }
    Path(candidate["evidenceRoot"]).mkdir()
    return candidate


@pytest.fixture
def receipt(candidate: dict) -> dict:
    voice_exception = candidate["repositories"]["adminEsp"]["dirtyExceptions"][0]
    voice = {
        **voice_exception,
        "repositorySha": candidate["repositories"]["adminEsp"]["sha"],
        "binding": "dirtyException",
    }
    return {
        "schemaVersion": 1,
        "candidateId": candidate["candidateId"],
        "result": "PASS",
        "capturedAt": "2026-08-29T12:00:00Z",
        "course": deepcopy(candidate["course"]),
        "lesson": deepcopy(candidate["tools"]["physicalEvidence"]["identity"]["lesson"]),
        "replacement": deepcopy(candidate["tools"]["physicalEvidence"]["identity"]["replacement"]),
        "renderer": deepcopy(candidate["tools"]["physicalEvidence"]["identity"]["renderer"]),
        "repositories": {name: value["sha"] for name, value in candidate["repositories"].items()},
        "backendImage": deepcopy(candidate["images"]["backend"]),
        "firmware": deepcopy(candidate["firmware"]),
        "protectedSource": voice,
        "device": deepcopy(candidate["tools"]["physicalEvidence"]["identity"]["device"]),
        "journey": deepcopy(candidate["database"]["journey"]),
        "database": deepcopy(candidate["database"]["terminalReadback"]),
        "evidence": [
            {"path": path, "sha256": sha256}
            for path, sha256 in candidate["tools"]["physicalEvidence"]["identity"]["evidenceArtifacts"].items()
        ],
    }


def test_receipt_requires_candidate_curriculum_v5(receipt: dict, candidate: dict) -> None:
    from course_mode_physical_tft_receipt_verify import validate_receipt

    receipt["lesson"]["lessonKey"] = "course-mode-pilot-cat-ball"
    receipt["renderer"]["rendererId"] = "teebot-lesson-renderer.v4"
    assert validate_receipt(receipt, candidate) == ["receipt.lesson", "receipt.renderer"]


def test_receipt_binds_candidate_delivery_and_current_protected_source(receipt: dict, candidate: dict) -> None:
    from course_mode_physical_tft_receipt_verify import validate_receipt

    assert validate_receipt(receipt, candidate) == []
    stale = deepcopy(receipt)
    stale["protectedSource"]["sha256"] = "08f77b5452301224b17b4b333d2d032fff40c06aa2eaea97fa90932dae7d97e3"
    assert validate_receipt(stale, candidate) == ["receipt.protected_source"]
    for field, value, reason in [
        ("candidateId", "wrong", "receipt.candidate"),
        ("repositories", {**receipt["repositories"], "adminEsp": "f" * 40}, "receipt.repositories"),
        ("backendImage", {**receipt["backendImage"], "imageId": "sha256:" + "f" * 64}, "receipt.image"),
        ("firmware", {**receipt["firmware"], "applicationSha256": "f" * 64}, "receipt.firmware"),
        ("journey", {**receipt["journey"], "deliveryId": ""}, "receipt.journey"),
        ("database", {**receipt["database"], "completionCount": 2}, "receipt.database"),
    ]:
        changed = deepcopy(receipt)
        changed[field] = value
        assert reason in validate_receipt(changed, candidate)


def test_protected_source_binding_follows_candidate_dirty_exception(receipt: dict, candidate: dict) -> None:
    from course_mode_physical_tft_receipt_verify import validate_receipt

    candidate["repositories"]["adminEsp"]["dirtyExceptions"][0]["sha256"] = "e" * 64
    receipt["protectedSource"]["sha256"] = "e" * 64
    assert "candidate.repositories.adminEsp.dirtyExceptions.hash" in validate_receipt(receipt, candidate)


def test_protected_source_can_bind_clean_candidate_repository_sha(receipt: dict, candidate: dict, monkeypatch) -> None:
    from course_mode_physical_tft_receipt_verify import validate_receipt

    repo = Path(candidate["repositories"]["adminEsp"]["path"])
    voice = repo / "main/tbot-server/tests/test_lesson_voice_output_discipline.py"
    voice.write_text("committed voice source\n", encoding="utf-8")
    candidate["repositories"]["adminEsp"]["dirtyExceptions"] = [
        item
        for item in candidate["repositories"]["adminEsp"]["dirtyExceptions"]
        if item["path"] != "main/tbot-server/tests/test_lesson_voice_output_discipline.py"
    ]
    receipt["protectedSource"] = {
        "path": "main/tbot-server/tests/test_lesson_voice_output_discipline.py",
        "repositorySha": candidate["repositories"]["adminEsp"]["sha"],
        "binding": "repository",
        "sha256": hashlib.sha256(voice.read_bytes()).hexdigest(),
    }
    candidate["tools"]["physicalEvidence"]["identity"]["candidateBinding"]["protectedSource"] = deepcopy(
        receipt["protectedSource"]
    )
    _resign_physical_identity(candidate, monkeypatch)
    assert validate_receipt(receipt, candidate) == []


def test_receipt_rejects_well_formed_but_unbound_checksums(receipt: dict, candidate: dict) -> None:
    from course_mode_physical_tft_receipt_verify import validate_receipt

    for section, field, reason in [
        ("renderer", "manifestChecksum", "receipt.renderer"),
        ("replacement", "cutoverReceiptSha256", "receipt.replacement"),
        ("device", "partitionTableSha256", "receipt.device"),
    ]:
        changed = deepcopy(receipt)
        changed[section][field] = "f" * 64
        assert reason in validate_receipt(changed, candidate)


def test_forging_candidate_dict_and_receipt_cannot_bypass_signed_physical_identity(
    receipt: dict,
    candidate: dict,
) -> None:
    from course_mode_physical_tft_receipt_verify import validate_receipt

    forged = deepcopy(candidate)
    forged["images"]["backend"]["imageId"] = "sha256:" + "f" * 64
    changed = deepcopy(receipt)
    changed["backendImage"] = deepcopy(forged["images"]["backend"])
    assert "receipt.image" in validate_receipt(changed, forged)

    forged = deepcopy(candidate)
    forged["database"]["terminalReadback"]["completionCount"] = 2
    changed = deepcopy(receipt)
    changed["database"] = deepcopy(forged["database"]["terminalReadback"])
    assert "receipt.database" in validate_receipt(changed, forged)


def test_receipt_evidence_hashes_must_match_signed_expected_artifact_map(receipt: dict, candidate: dict) -> None:
    from course_mode_physical_tft_receipt_verify import validate_receipt

    receipt["evidence"][0]["sha256"] = "f" * 64
    assert "receipt.evidence" in validate_receipt(receipt, candidate)


def test_rewritten_identity_and_updated_dirty_hash_still_requires_pinned_signature(
    receipt: dict, candidate: dict
) -> None:
    from course_mode_physical_tft_receipt_verify import validate_receipt

    binding = candidate["tools"]["physicalEvidence"]
    identity = deepcopy(binding["identity"])
    identity["renderer"]["manifestChecksum"] = "f" * 64
    path = Path(candidate["repositories"]["adminEsp"]["path"]) / binding["path"]
    data = (json.dumps(identity, sort_keys=True, separators=(",", ":")) + "\n").encode()
    path.write_bytes(data)
    binding["identity"] = identity
    binding["sha256"] = hashlib.sha256(data).hexdigest()
    for item in candidate["repositories"]["adminEsp"]["dirtyExceptions"]:
        if item["path"] == binding["path"]:
            item["sha256"] = binding["sha256"]
    receipt["renderer"] = deepcopy(identity["renderer"])
    assert "candidate.physicalEvidence.signature" in validate_receipt(receipt, candidate)


@pytest.mark.parametrize(
    "rewrite",
    [
        lambda candidate: candidate.update(candidateId="course-mode-2026-08-29.2"),
        lambda candidate: candidate["course"].update(courseId="course-2"),
        lambda candidate: candidate["curriculum"].update(sourceChecksum="f" * 64),
        lambda candidate: candidate["repositories"]["backend"].update(sha="f" * 40),
        lambda candidate: candidate.update(createdAt="2026-08-28T00:00:00Z"),
        lambda candidate: candidate["images"]["backend"].update(imageId="sha256:" + "f" * 64),
    ],
)
def test_candidate_anchor_rewrite_without_resigning_is_rejected(receipt: dict, candidate: dict, rewrite) -> None:
    from course_mode_physical_tft_receipt_verify import validate_receipt

    rewrite(candidate)
    receipt["candidateId"] = candidate["candidateId"]
    receipt["course"] = deepcopy(candidate["course"])
    receipt["repositories"] = {name: value["sha"] for name, value in candidate["repositories"].items()}
    assert "candidate.physicalEvidence.signature" in validate_receipt(receipt, candidate)


def test_receipt_rejects_duplicate_artifact_paths_before_projection(receipt: dict, candidate: dict) -> None:
    from course_mode_physical_tft_receipt_verify import validate_receipt

    receipt["evidence"].append(deepcopy(receipt["evidence"][0]))
    assert "receipt.evidence" in validate_receipt(receipt, candidate)


@pytest.mark.parametrize("captured", ["2026-08-30T00:00:00", "2026-08-30T07:00:00+07:00", "2026-08-20T00:00:00Z"])
def test_receipt_requires_fresh_strict_utc_timestamp(receipt: dict, candidate: dict, captured: str) -> None:
    from datetime import datetime, timezone

    from course_mode_physical_tft_receipt_verify import validate_receipt

    receipt["capturedAt"] = captured
    reasons = validate_receipt(receipt, candidate, now=datetime(2026, 8, 30, 12, tzinfo=timezone.utc))
    assert any(reason.startswith("receipt.timestamp") for reason in reasons)


def test_malformed_candidate_types_fail_without_traceback(receipt: dict) -> None:
    from course_mode_physical_tft_receipt_verify import validate_receipt

    reasons = validate_receipt(receipt, {"tools": [], "repositories": [], "database": []})
    assert reasons == sorted(reasons) and reasons


def test_cli_is_deterministic_bounded_and_redacted(tmp_path: Path, receipt: dict, candidate: dict) -> None:
    import course_mode_physical_tft_preflight as preflight

    candidate_path, receipt_path = tmp_path / "candidate.json", tmp_path / "receipt.json"
    candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    bootstrap = (
        "import runpy,sys;"
        "sys.path.insert(0,sys.argv[1]);"
        "import course_mode_physical_tft_preflight as p;"
        "p.PINNED_APPROVAL_PUBLIC_KEY_RAW=bytes.fromhex(sys.argv[2]);"
        "p.PINNED_APPROVAL_KEY_FINGERPRINT=sys.argv[3];"
        "script=sys.argv[4];sys.argv=[script,*sys.argv[5:]];"
        "runpy.run_path(script,run_name='__main__')"
    )
    command = [
        sys.executable,
        "-c",
        bootstrap,
        str(SCRIPT.parent),
        preflight.PINNED_APPROVAL_PUBLIC_KEY_RAW.hex(),
        preflight.PINNED_APPROVAL_KEY_FINGERPRINT,
        str(SCRIPT),
        str(receipt_path),
        "--candidate",
        str(candidate_path),
    ]
    first = subprocess.run(command, capture_output=True, text=True)
    second = subprocess.run(command, capture_output=True, text=True)
    assert first.returncode == 0 and first.stdout == second.stdout
    assert json.loads(first.stdout) == {"candidateId": candidate["candidateId"], "reasons": [], "valid": True}
    assert len(first.stdout) < 1024
    receipt["token"] = "do-not-echo"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    failed = subprocess.run(command, capture_output=True, text=True)
    assert failed.returncode == 1 and "do-not-echo" not in failed.stdout
