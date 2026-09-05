from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import sys
import zipfile
from io import BytesIO
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/course_mode_software_evidence_audit.py"

QUICK_LANES = [
    "backend-course-mode-focused",
    "admin-course-mode-logic",
    "esp-course-mode-focused",
    "firmware-course-mode-focused",
]
FULL_LANES = [
    "backend-lint",
    "backend-typecheck",
    "backend-tests",
    "backend-build",
    "backend-curriculum-verifier",
    "admin-logic",
    "admin-browser",
    "admin-build",
    "admin-course-mode-playwright-chromium-desktop",
    "admin-course-mode-playwright-webkit-desktop",
    "admin-course-mode-playwright-chromium-mobile",
    "admin-course-mode-playwright-webkit-mobile",
    "admin-course-mode-assignment-fixture",
    "admin-course-mode-assignment-new",
    "admin-course-mode-assignment-rollback",
    "esp-course-mode-full",
    "firmware-renderer",
    "firmware-handler",
    "firmware-backward-compatibility",
    "cross-contract-parity",
]


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    path.chmod(0o444)


def _lanes(names: list[str]) -> list[dict[str, object]]:
    return [{"durationMs": index + 1, "exitCode": 0, "name": name} for index, name in enumerate(names)]


@pytest.fixture
def evidence_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    candidate_path = tmp_path / "candidate.json"
    output = evidence / "06-software-evidence-audit.json"
    candidate_id = "course-mode-2026-09-05.99"
    admin_sha = "a" * 40
    backend_sha = "b" * 40
    firmware_sha = "c" * 40
    candidate = {
        "candidateId": candidate_id,
        "evidenceRoot": str(evidence),
        "repositories": {
            "backend": {"sha": backend_sha},
            "adminEsp": {"sha": admin_sha},
            "firmware": {"sha": firmware_sha},
        },
        "images": {
            "lessonStudioBackend": {
                "reference": f"local/tbot-backend:course-mode-physical-tft-{backend_sha}",
                "id": "sha256:" + "1" * 64,
            },
            "lessonStudioWeb": {
                "reference": f"local/tbot-server-web:course-mode-physical-tft-{admin_sha}",
                "id": "sha256:" + "2" * 64,
            },
        },
        "firmware": {
            "appOffset": "0x20000",
            "appBytes": 3_637_200,
            "appSha256": "3" * 64,
            "evidenceManifestSha256": "4" * 64,
        },
        "course": {
            "courseId": "a17792f6-8d86-4ad1-a6f3-77663b4d4674",
            "courseKey": "english-6month-4-6",
        },
        "curriculum": {"lessonCount": 26, "activityCount": 256},
        "tools": {"pythonTestRuntime": {"version": 1}},
    }
    _write_json(candidate_path, candidate)
    _write_json(
        evidence / "00-candidate-validator.json",
        {"reasons": [], "schemaVersion": 1, "status": "pass", "validator": "course-mode-candidate.v1"},
    )
    attestation_path = evidence / "00-operator-attestation.json"
    _write_json(
        attestation_path,
        {
            "candidateId": candidate_id,
            "effectiveUid": os.getuid(),
            "gateSha": admin_sha,
            "sameUidThreatModel": "malicious-process-excluded",
            "schemaVersion": 1,
            "trustedOperatorAccountConfirmed": True,
            "untrustedAutomationStoppedConfirmed": True,
        },
    )
    attestation_sha = hashlib.sha256(attestation_path.read_bytes()).hexdigest()
    _write_json(
        evidence / "02-runtime-assignment-new-rollback.json",
        {
            "candidateId": candidate_id,
            "failedLane": None,
            "lanes": _lanes(["admin-course-mode-assignment-new", "admin-course-mode-assignment-rollback"]),
            "verdict": "PASS",
        },
    )
    _write_json(
        evidence / "02-runtime-browser-after-assignment.json",
        {
            "candidateId": candidate_id,
            "failedLane": None,
            "lanes": _lanes(
                [
                    "admin-course-mode-playwright-chromium-desktop",
                    "admin-course-mode-playwright-webkit-desktop",
                ]
            ),
            "verdict": "PASS",
        },
    )
    _write_json(
        evidence / "02-runtime-continuity-inspection.json",
        {
            "allMountsCanonical": True,
            "allMountsExist": True,
            "allMountsReadOnly": True,
            "assignmentFlags": {"new": False, "rollback": False},
            "imagesMatchCandidate": True,
            "manualRecreateAfterRollback": False,
            "mountCount": 4,
            "status": "pass",
        },
    )
    for name, lanes in (
        ("03-quick-gate.json", QUICK_LANES),
        ("04-full-gate.json", FULL_LANES),
        ("05-live-db-gate.json", FULL_LANES + ["live-postgres"]),
    ):
        _write_json(
            evidence / name,
            {
                "candidateId": candidate_id,
                "failedLane": None,
                "lanes": _lanes(lanes),
                "operatorAttestationSha256": attestation_sha,
                "verdict": "PASS",
            },
        )
    return candidate_path, evidence, output


def _run(candidate: Path, evidence: Path, output: Path, *preserved: Path) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        str(SCRIPT),
        "--candidate",
        str(candidate),
        "--evidence-root",
        str(evidence),
    ]
    for root in preserved:
        command.extend(["--preserved-root", str(root)])
    command.extend(["--output", str(output)])
    return subprocess.run(command, check=False, capture_output=True, text=True)


def _zip(entries: dict[str, bytes], compression: int = zipfile.ZIP_DEFLATED) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=compression) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def _mark_zip_encrypted(payload: bytes) -> bytes:
    changed = bytearray(payload)
    local = changed.find(b"PK\x03\x04")
    central = changed.find(b"PK\x01\x02")
    assert local >= 0 and central >= 0
    changed[local + 6 : local + 8] = (1).to_bytes(2, "little")
    changed[central + 8 : central + 10] = (1).to_bytes(2, "little")
    return bytes(changed)


def test_cli_accepts_clean_bound_software_evidence_deterministically(
    evidence_fixture: tuple[Path, Path, Path],
) -> None:
    candidate, evidence, output = evidence_fixture

    first = _run(candidate, evidence, output)
    first_bytes = output.read_bytes()
    second = _run(candidate, evidence, output)

    assert first.returncode == second.returncode == 0
    assert output.read_bytes() == first_bytes
    report = json.loads(first.stdout)
    assert report == json.loads(first_bytes)
    assert report["schemaVersion"] == 1
    assert report["candidateId"] == "course-mode-2026-09-05.99"
    assert report["status"] == "pass"
    assert report["findings"] == []
    assert report["checkedFileCount"] == 9
    assert report["checkedArchiveMemberCount"] == 0
    assert report["checks"]["physicalActionsPerformed"] is False
    assert report["checks"]["productionDatabaseUsed"] is False
    assert all(
        value
        for name, value in report["checks"].items()
        if name not in {"physicalActionsPerformed", "productionDatabaseUsed"}
    )
    assert output.stat().st_mode & 0o777 == 0o444


def test_preserved_sanitized_manifest_is_allowed_but_raw_playwright_tree_fails_closed(
    evidence_fixture: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    candidate, evidence, output = evidence_fixture
    preserved = tmp_path / "preserved"
    preserved.mkdir()
    _write_json(
        preserved / "sanitized-tombstone.json",
        {
            "entries": [
                {
                    "action": "deleted",
                    "bytes": 123,
                    "classification": "private-playwright-capture",
                    "path": "results/trace.zip",
                    "reason": "raw capture excluded",
                    "sha256": "d" * 64,
                    "timestamp": "2026-09-05T00:00:00Z",
                }
            ],
            "schemaVersion": 1,
        },
    )

    clean = _run(candidate, evidence, output, preserved)

    assert clean.returncode == 0
    assert json.loads(clean.stdout)["checkedFileCount"] == 10

    raw = preserved / "playwright-report"
    raw.mkdir()
    (raw / "index.html").write_text("<html>raw Playwright report</html>", encoding="utf-8")
    (raw / "index.html").chmod(0o444)

    rejected = _run(candidate, evidence, output, preserved)
    report = json.loads(rejected.stdout)

    assert rejected.returncode == 1
    assert "preserved.raw_playwright" in report["findings"]
    assert report["checks"]["rawPlaywrightAbsent"] is False


@pytest.mark.parametrize("attack", ["symlink", "hardlink", "fifo", "group-writable"])
def test_expected_evidence_rejects_insecure_metadata(
    evidence_fixture: tuple[Path, Path, Path], tmp_path: Path, attack: str
) -> None:
    candidate, evidence, output = evidence_fixture
    target = evidence / "03-quick-gate.json"
    original = target.read_bytes()
    target.chmod(0o644)
    target.unlink()
    replacement = tmp_path / "replacement.json"
    replacement.write_bytes(original)
    replacement.chmod(0o444)
    if attack == "symlink":
        target.symlink_to(replacement)
    elif attack == "hardlink":
        os.link(replacement, target)
    elif attack == "fifo":
        os.mkfifo(target, 0o444)
    else:
        target.write_bytes(original)
        target.chmod(0o464)

    completed = _run(candidate, evidence, output)
    report = json.loads(completed.stdout)

    assert completed.returncode == 1
    assert "evidence.metadata_or_json" in report["findings"]
    assert report["checks"]["secureFileMetadata"] is False


def test_bounded_zip_members_are_scanned_and_counted(
    evidence_fixture: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    candidate, evidence, output = evidence_fixture
    preserved = tmp_path / "preserved"
    preserved.mkdir()
    archive = preserved / "diagnostic.zip"
    archive.write_bytes(_zip({"summary.txt": b"public diagnostic result: PASS\n"}))
    archive.chmod(0o444)

    completed = _run(candidate, evidence, output, preserved)
    report = json.loads(completed.stdout)

    assert completed.returncode == 0
    assert report["checkedArchiveMemberCount"] == 1
    assert report["checks"]["archiveSafety"] is True


@pytest.mark.parametrize(
    ("name", "payload"),
    [
        ("headers.txt", b"Authorization: Bearer do-not-print-this-value\n"),
        ("cookies.json", b'{"Set-Cookie":"private-cookie-value"}'),
        ("session.json", b'{"sessionId":"private-session-value"}'),
        ("password.json", b'{"password":"private-password-value"}'),
        ("secret.json", b'{"secret":"private-secret-value"}'),
    ],
)
def test_zip_secret_values_fail_without_echoing_values(
    evidence_fixture: tuple[Path, Path, Path], tmp_path: Path, name: str, payload: bytes
) -> None:
    candidate, evidence, output = evidence_fixture
    preserved = tmp_path / "preserved"
    preserved.mkdir()
    archive = preserved / "diagnostic.zip"
    archive.write_bytes(_zip({name: payload}))
    archive.chmod(0o444)

    completed = _run(candidate, evidence, output, preserved)
    report = json.loads(completed.stdout)

    assert completed.returncode == 1
    assert "content.secret" in report["findings"]
    assert report["checks"]["secretScan"] is False
    private_value = payload.decode().split("private-", 1)[-1].rstrip('"}\n')
    assert private_value not in completed.stdout
    assert private_value not in output.read_text(encoding="utf-8")


def test_embedded_base64_playwright_payload_is_rejected(
    evidence_fixture: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    candidate, evidence, output = evidence_fixture
    preserved = tmp_path / "preserved"
    preserved.mkdir()
    encoded = base64.b64encode(_zip({"report/index.html": b"Playwright HTML report"})).decode()
    embedded = preserved / "summary.html"
    embedded.write_text(f"<script>window.report='{encoded}'</script>", encoding="utf-8")
    embedded.chmod(0o444)

    completed = _run(candidate, evidence, output, preserved)
    report = json.loads(completed.stdout)

    assert completed.returncode == 1
    assert "content.embedded_playwright" in report["findings"]
    assert report["checks"]["rawPlaywrightAbsent"] is False


@pytest.mark.parametrize(
    ("filename", "payload", "finding"),
    [
        ("encrypted.zip", _mark_zip_encrypted(_zip({"result.txt": b"PASS"})), "archive.encrypted"),
        ("unsupported.zip", _zip({"result.txt": b"PASS"}, zipfile.ZIP_BZIP2), "archive.unsupported"),
        ("escape.zip", _zip({"../outside.txt": b"PASS"}), "archive.invalid"),
        ("nested.zip", _zip({"level-2.zip": _zip({"level-3.zip": _zip({"result.txt": b"PASS"})})}), "archive.nested_limit"),
        ("oversize.zip", _zip({"large.txt": b"x" * (4 * 1024 * 1024 + 1)}), "archive.oversize"),
        ("unsupported.tar", b"not a supported bounded archive", "archive.unsupported"),
    ],
)
def test_archive_hazards_fail_closed(
    evidence_fixture: tuple[Path, Path, Path],
    tmp_path: Path,
    filename: str,
    payload: bytes,
    finding: str | None,
) -> None:
    candidate, evidence, output = evidence_fixture
    preserved = tmp_path / "preserved"
    preserved.mkdir()
    artifact = preserved / filename
    artifact.write_bytes(payload)
    artifact.chmod(0o444)

    completed = _run(candidate, evidence, output, preserved)
    report = json.loads(completed.stdout)

    assert completed.returncode == 1
    assert (finding or "archive.unsupported") in report["findings"]
    assert report["checks"]["archiveSafety"] is False


def test_zip_raw_playwright_member_is_rejected(
    evidence_fixture: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    candidate, evidence, output = evidence_fixture
    preserved = tmp_path / "preserved"
    preserved.mkdir()
    artifact = preserved / "report.zip"
    artifact.write_bytes(_zip({"playwright-report/index.html": b"Playwright HTML report"}))
    artifact.chmod(0o444)

    completed = _run(candidate, evidence, output, preserved)
    report = json.loads(completed.stdout)

    assert completed.returncode == 1
    assert "content.raw_playwright" in report["findings"]
    assert report["checks"]["rawPlaywrightAbsent"] is False


def test_audio_transcript_and_valid_private_key_are_rejected(
    evidence_fixture: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    serialization = pytest.importorskip("cryptography.hazmat.primitives.serialization")
    rsa = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.rsa")
    candidate, evidence, output = evidence_fixture
    preserved = tmp_path / "preserved"
    preserved.mkdir()
    (preserved / "voice.wav").write_bytes(b"RIFF" + b"\0" * 40)
    (preserved / "voice.wav").chmod(0o444)
    (preserved / "child-transcript.txt").write_text("child transcript: hello robot", encoding="utf-8")
    (preserved / "child-transcript.txt").chmod(0o444)
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    (preserved / "identity.pem").write_bytes(pem)
    (preserved / "identity.pem").chmod(0o400)

    completed = _run(candidate, evidence, output, preserved)
    report = json.loads(completed.stdout)

    assert completed.returncode == 1
    assert {"content.audio", "content.transcript", "content.private_key"} <= set(report["findings"])
    assert report["checks"]["privateContentAbsent"] is False
    assert b"PRIVATE KEY" not in completed.stdout.encode()


def test_every_primary_evidence_file_is_scanned_and_output_is_excluded(
    evidence_fixture: tuple[Path, Path, Path]
) -> None:
    candidate, evidence, output = evidence_fixture
    marker = "do-not-echo-primary-secret"
    extra = evidence / "diagnostic.log"
    extra.write_text(f"Authorization: Bearer {marker}\n", encoding="utf-8")
    extra.chmod(0o444)

    completed = _run(candidate, evidence, output)
    first_report = json.loads(completed.stdout)
    repeated = _run(candidate, evidence, output)
    repeated_report = json.loads(repeated.stdout)

    assert completed.returncode == repeated.returncode == 1
    assert first_report == repeated_report
    assert first_report["checkedFileCount"] == 10
    assert "content.secret" in first_report["findings"]
    assert marker not in completed.stdout
    assert marker not in output.read_text(encoding="utf-8")


def test_sanitized_manifest_requires_complete_hash_only_rows_and_no_secret_values(
    evidence_fixture: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    candidate, evidence, output = evidence_fixture
    preserved = tmp_path / "preserved"
    preserved.mkdir()
    marker = "do-not-echo-tombstone-secret"
    _write_json(
        preserved / "bad-tombstone.json",
        {
            "entries": [
                {
                    "action": "deleted",
                    "bytes": 1,
                    "classification": "private",
                    "path": "trace.zip",
                    "reason": f"Authorization: Bearer {marker}",
                    "sha256": "a" * 64,
                    "timestamp": "2026-09-05T00:00:00Z",
                }
            ],
            "schemaVersion": 1,
        },
    )

    completed = _run(candidate, evidence, output, preserved)
    report = json.loads(completed.stdout)

    assert completed.returncode == 1
    assert "content.secret" in report["findings"]
    assert marker not in completed.stdout


@pytest.mark.parametrize(
    ("name", "payload"),
    [
        ("screen.png", b"\x89PNG\r\n\x1a\n" + b"private pixels"),
        ("recording.webm", b"\x1a\x45\xdf\xa3" + b"private video"),
        ("frame.jpg", b"\xff\xd8\xff\xe0" + b"private photo"),
    ],
)
def test_binary_private_media_is_rejected(
    evidence_fixture: tuple[Path, Path, Path], tmp_path: Path, name: str, payload: bytes
) -> None:
    candidate, evidence, output = evidence_fixture
    preserved = tmp_path / "preserved"
    preserved.mkdir()
    artifact = preserved / name
    artifact.write_bytes(payload)
    artifact.chmod(0o444)

    completed = _run(candidate, evidence, output, preserved)
    report = json.loads(completed.stdout)

    assert completed.returncode == 1
    assert "content.binary_media" in report["findings"]
    assert report["checks"]["privateContentAbsent"] is False


def test_release_report_rejects_retained_fields_even_when_lanes_pass(
    evidence_fixture: tuple[Path, Path, Path]
) -> None:
    candidate, evidence, output = evidence_fixture
    report_path = evidence / "04-full-gate.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["retainedPaths"] = ["/tmp/private-runtime"]
    report_path.chmod(0o644)
    _write_json(report_path, report)

    completed = _run(candidate, evidence, output)
    result = json.loads(completed.stdout)

    assert completed.returncode == 1
    assert "evidence.full" in result["findings"]
    assert result["checks"]["fullGate20of20"] is False


def test_missing_preserved_root_and_insecure_candidate_fail_closed(
    evidence_fixture: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    candidate, evidence, output = evidence_fixture
    candidate.chmod(0o466)

    completed = _run(candidate, evidence, output, tmp_path / "missing")
    report = json.loads(completed.stdout)

    assert completed.returncode == 1
    assert {"candidate.metadata_or_json", "preserved.root"} <= set(report["findings"])
    assert report["checks"]["secureFileMetadata"] is False


def test_output_must_be_inside_evidence_root(
    evidence_fixture: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    candidate, evidence, _ = evidence_fixture
    unsafe_output = tmp_path / "outside.json"

    completed = _run(candidate, evidence, unsafe_output)
    report = json.loads(completed.stdout)

    assert completed.returncode == 1
    assert "output.unsafe" in report["findings"]
    assert not unsafe_output.exists()


def test_cli_does_not_resolve_away_candidate_symlink(
    evidence_fixture: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    candidate, evidence, output = evidence_fixture
    alias = tmp_path / "candidate-link.json"
    alias.symlink_to(candidate)

    completed = _run(alias, evidence, output)
    report = json.loads(completed.stdout)

    assert completed.returncode == 1
    assert "candidate.metadata_or_json" in report["findings"]


def test_invalid_candidate_id_is_not_echoed(
    evidence_fixture: tuple[Path, Path, Path]
) -> None:
    candidate, evidence, output = evidence_fixture
    payload = json.loads(candidate.read_text(encoding="utf-8"))
    marker = "attacker-candidate-id-secret"
    payload["candidateId"] = marker
    candidate.chmod(0o644)
    _write_json(candidate, payload)

    completed = _run(candidate, evidence, output)
    report = json.loads(completed.stdout)

    assert completed.returncode == 1
    assert report["candidateId"] is None
    assert marker not in completed.stdout
    assert marker not in output.read_text(encoding="utf-8")


def test_output_cannot_collide_with_required_input_or_alias_it(
    evidence_fixture: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    candidate, evidence, output = evidence_fixture
    required = evidence / "03-quick-gate.json"
    collision = _run(candidate, evidence, required)
    assert collision.returncode == 1
    assert "output.collision" in json.loads(collision.stdout)["findings"]
    assert required.read_bytes() != output.read_bytes() if output.exists() else True

    alias = tmp_path / "output-alias.json"
    alias.symlink_to(required)
    aliased = _run(candidate, evidence, alias)
    assert aliased.returncode == 1
    assert "output.collision" in json.loads(aliased.stdout)["findings"]

    hardlink = tmp_path / "output-hardlink.json"
    os.link(required, hardlink)
    linked = _run(candidate, evidence, hardlink)
    assert linked.returncode == 1
    assert "output.collision" in json.loads(linked.stdout)["findings"]

    extra = evidence / "extra.json"
    extra.write_bytes(b"{}\n")
    extra.chmod(0o444)
    collision = _run(candidate, evidence, extra)
    assert collision.returncode == 1
    assert "output.collision" in json.loads(collision.stdout)["findings"]


@pytest.mark.parametrize("payload", [b"not a zip", b"MZ-self-extracting-prefix"])
def test_zip_extension_requires_valid_zip_and_prefixed_zip_is_supported(
    evidence_fixture: tuple[Path, Path, Path], tmp_path: Path, payload: bytes
) -> None:
    candidate, evidence, output = evidence_fixture
    preserved = tmp_path / "preserved"
    preserved.mkdir()
    artifact = preserved / "capture.zip"
    if payload.startswith(b"MZ"):
        payload = b"MZ" + _zip({"result.txt": b"PASS"})
    artifact.write_bytes(payload)
    artifact.chmod(0o444)

    completed = _run(candidate, evidence, output, preserved)
    report = json.loads(completed.stdout)

    if payload.startswith(b"MZ"):
        assert completed.returncode == 0
        assert report["checkedArchiveMemberCount"] == 1
    else:
        assert completed.returncode == 1
        assert "archive.invalid" in report["findings"]


def test_tombstone_strings_and_embedded_base64_are_still_scanned(
    evidence_fixture: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    candidate, evidence, output = evidence_fixture
    preserved = tmp_path / "preserved"
    preserved.mkdir()
    marker = "tombstone-secret-value"
    _write_json(
        preserved / "sanitized-tombstone.json",
        {
            "entries": [
                {
                    "action": "deleted",
                    "bytes": 123,
                    "classification": "private-playwright-capture",
                    "path": "results/trace.zip",
                    "reason": f"Authorization: Bearer {marker}",
                    "sha256": "d" * 64,
                    "timestamp": "2026-09-05T00:00:00Z",
                }
            ],
            "schemaVersion": 1,
        },
    )

    completed = _run(candidate, evidence, output, preserved)
    report = json.loads(completed.stdout)

    assert completed.returncode == 1
    assert "content.secret" in report["findings"]
    assert marker not in completed.stdout


def test_zip_member_after_first_64k_is_scanned(
    evidence_fixture: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    candidate, evidence, output = evidence_fixture
    preserved = tmp_path / "preserved"
    preserved.mkdir()
    artifact = preserved / "report.zip"
    artifact.write_bytes(_zip({"report.html": b"x" * 70_000 + b"Playwright HTML report"}))
    artifact.chmod(0o444)

    completed = _run(candidate, evidence, output, preserved)
    report = json.loads(completed.stdout)

    assert completed.returncode == 1
    assert "content.raw_playwright" in report["findings"]


def test_malformed_pem_private_key_marker_is_rejected(
    evidence_fixture: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    candidate, evidence, output = evidence_fixture
    preserved = tmp_path / "preserved"
    preserved.mkdir()
    key = preserved / "not-a-key.txt"
    key.write_text("-----BEGIN PRIVATE KEY-----\nmalformed\n", encoding="utf-8")
    key.chmod(0o444)

    completed = _run(candidate, evidence, output, preserved)
    report = json.loads(completed.stdout)

    assert completed.returncode == 1
    assert "content.private_key" in report["findings"]


def test_archive_budgets_are_cumulative_across_nested_members(
    evidence_fixture: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    candidate, evidence, output = evidence_fixture
    preserved = tmp_path / "preserved"
    preserved.mkdir()
    inner = _zip({f"member-{index}.txt": b"ok" for index in range(4_100)})
    artifact = preserved / "nested.zip"
    artifact.write_bytes(_zip({"inner.zip": inner}))
    artifact.chmod(0o444)

    completed = _run(candidate, evidence, output, preserved)
    report = json.loads(completed.stdout)

    assert completed.returncode == 1
    assert "archive.oversize" in report["findings"]


def test_symlinked_evidence_or_preserved_root_is_rejected_before_traversal(
    evidence_fixture: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    candidate, evidence, output = evidence_fixture
    evidence_alias = tmp_path / "evidence-alias"
    evidence_alias.symlink_to(evidence, target_is_directory=True)
    candidate_payload = json.loads(candidate.read_text(encoding="utf-8"))
    candidate.chmod(0o644)
    candidate_payload["evidenceRoot"] = str(evidence_alias)
    _write_json(candidate, candidate_payload)

    completed = _run(candidate, evidence_alias, output)
    report = json.loads(completed.stdout)

    assert completed.returncode == 1
    assert "evidence.root" in report["findings"]

    candidate_payload["evidenceRoot"] = str(evidence)
    candidate.chmod(0o644)
    _write_json(candidate, candidate_payload)
    preserved_alias = tmp_path / "preserved-alias"
    preserved_alias.symlink_to(evidence, target_is_directory=True)
    completed = _run(candidate, evidence, output, preserved_alias)
    report = json.loads(completed.stdout)
    assert completed.returncode == 1
    assert "preserved.root" in report["findings"]
