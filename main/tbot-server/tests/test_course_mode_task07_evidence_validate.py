import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "course_mode_task07_evidence_validate.py"
TEMPLATE = (
    ROOT.parents[1]
    / "docs/qa/artifacts/2026-08-22-course-mode-task07/physical-evidence-template.json"
)


def load_validator():
    spec = importlib.util.spec_from_file_location("task07_evidence", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def blocked_template():
    return json.loads(TEMPLATE.read_text(encoding="utf-8"))


def passing_evidence():
    evidence = blocked_template()
    evidence["verdict"] = "PHYSICAL_PASS"
    evidence["candidate"]["installed"] = True
    evidence["rollback"]["physicallyRehearsedKnownGoodV1"] = True
    evidence["safety"]["stopPathVerified"] = True
    evidence["safety"]["safeRestVerified"] = True
    evidence["privacy"]["safeIdleUplinkVerified"] = True
    evidence["privacy"]["openUnauthorizedUplinkFinding"] = False
    evidence["robot"]["formalCurrentBindingVerified"] = True
    for operator in evidence["operators"]:
        operator["present"] = True
    evidence["environment"]["motionEnvelopeClear"] = True
    evidence["environment"]["immediatePowerIsolationAvailable"] = True
    evidence["deferredBlockers"] = []
    evidence["captures"] = [
        {
            "path": "captures/lane-1.json",
            "sha256": "a" * 64,
            "bytes": 123,
            "mediaType": "application/json",
        }
    ]
    candidate_map = [
        "0x0", "bootloader/bootloader.bin",
        "0x8000", "partition_table/partition-table.bin",
        "0xd000", "ota_data_initial.bin",
        "0x20000", "xiaozhi.bin",
        "0x800000", "generated_assets.bin",
    ]
    evidence["commands"] = [
        {
            "actionClass": "candidate-install",
            "authorizedBy": "adult-operator",
            "executedAt": "2026-08-22T09:01:00+07:00",
            "exitCode": 0,
            "artifactManifestSha256": evidence["candidate"]["firmwareIdentitySha256"],
            "argv": ["esptool", "write_flash", *candidate_map],
        },
        *[
            {
                "actionClass": "readback",
                "authorizedBy": "adult-operator",
                "executedAt": "2026-08-22T09:02:00+07:00",
                "exitCode": 0,
                "artifactManifestSha256": evidence["candidate"]["firmwareIdentitySha256"],
                "argv": ["esptool", "read_flash", offset, size, output],
            }
            for offset, size, output in (
                ("0x0", "16256", "readback/bootloader.bin"),
                ("0x8000", "3072", "readback/partition-table.bin"),
                ("0xd000", "8192", "readback/ota_data_initial.bin"),
                ("0x20000", "3611920", "readback/xiaozhi.bin"),
                ("0x800000", "5693495", "readback/generated_assets.bin"),
            )
        ],
        {
            "actionClass": "rollback",
            "authorizedBy": "adult-operator",
            "executedAt": "2026-08-22T09:03:00+07:00",
            "exitCode": 0,
            "artifactManifestSha256": evidence["rollback"]["manifestSha256"],
            "argv": ["esptool", "write_flash", *candidate_map],
        },
        *[
            {
                "actionClass": "readback",
                "authorizedBy": "adult-operator",
                "executedAt": "2026-08-22T09:04:00+07:00",
                "exitCode": 0,
                "artifactManifestSha256": evidence["rollback"]["manifestSha256"],
                "argv": ["esptool", "read_flash", offset, size, output],
            }
            for offset, size, output in (
                ("0x0", "16256", "rollback-readback/bootloader.bin"),
                ("0x8000", "3072", "rollback-readback/partition-table.bin"),
                ("0xd000", "8192", "rollback-readback/ota_data_initial.bin"),
                ("0x20000", "3597792", "rollback-readback/xiaozhi.bin"),
                ("0x800000", "5693495", "rollback-readback/generated_assets.bin"),
            )
        ],
    ]
    required_measurements = {
        "baseline-hardware-health": ("supply-voltage", "V"),
        "visual-inspection": ("visual-defect-count", "count"),
        "embodied-behavior": ("servo-settle-time", "ms"),
        "listening-integrity": ("safe-idle-uplink-packets", "count"),
        "thermal-power-comfort": ("maximum-surface-temperature", "degC"),
        "recovery": ("recovery-time", "s"),
        "adult-end-to-end-journeys": ("journey-failure-count", "count"),
    }
    for lane in evidence["lanes"]:
        lane["verdict"] = "PASS"
        lane["startedAt"] = "2026-08-22T09:00:00+07:00"
        lane["endedAt"] = "2026-08-22T09:05:00+07:00"
        lane["capturePaths"] = ["captures/lane-1.json"]
        name, unit = required_measurements[lane["id"]]
        lane["measurements"] = [
            {
                "name": name,
                "value": 0,
                "unit": unit,
                "minimum": 0,
                "maximum": 1,
                "authority": "approved-task07-hardware-limit",
                "passed": True,
            }
        ]
    return evidence


def test_structurally_complete_pass_remains_locked_until_remediated_candidate_is_approved(tmp_path):
    validator = load_validator()
    evidence = passing_evidence()
    capture = tmp_path / "captures/lane-1.json"
    capture.parent.mkdir()
    capture.write_bytes(b'{"result":"pass"}\n')
    evidence["captures"][0]["bytes"] = capture.stat().st_size
    evidence["captures"][0]["sha256"] = hashlib.sha256(capture.read_bytes()).hexdigest()

    result = validator.validate_document(evidence, evidence_root=tmp_path)

    assert result["valid"] is False
    assert result["verdict"] == "PHYSICAL_PASS"
    assert result["errors"] == [
        "PHYSICAL_PASS is locked until a privacy-remediated candidate identity is approved"
    ]
    assert result["deferredBlockers"] == []


def test_evidence_root_verifies_capture_bytes_and_hash(tmp_path):
    validator = load_validator()
    evidence = passing_evidence()
    capture = tmp_path / "captures/lane-1.json"
    capture.parent.mkdir()
    capture.write_bytes(b'{"result":"pass"}\n')
    evidence["captures"][0]["bytes"] = capture.stat().st_size
    evidence["captures"][0]["sha256"] = hashlib.sha256(capture.read_bytes()).hexdigest()

    result = validator.validate_document(evidence, evidence_root=tmp_path)

    assert result["errors"] == [
        "PHYSICAL_PASS is locked until a privacy-remediated candidate identity is approved"
    ]


def test_evidence_root_rejects_missing_or_changed_capture(tmp_path):
    validator = load_validator()
    evidence = passing_evidence()

    result = validator.validate_document(evidence, evidence_root=tmp_path)

    assert result["valid"] is False
    assert "capture file is missing: captures/lane-1.json" in result["errors"]


def test_physical_pass_requires_capture_files_to_be_verified_from_evidence_root():
    validator = load_validator()

    result = validator.validate_document(passing_evidence())

    assert result["valid"] is False
    assert "PHYSICAL_PASS requires capture files verified from the evidence root" in result["errors"]


def test_blocked_template_is_valid_and_preserves_deferred_physical_gates():
    validator = load_validator()

    result = validator.validate_document(blocked_template())

    assert result["valid"] is True
    assert result["verdict"] == "PHYSICAL_BLOCKED"
    assert result["errors"] == []
    assert "approved numeric hardware limits" in result["deferredBlockers"]


def test_evidence_requires_adult_only_redacted_data_policy():
    validator = load_validator()
    evidence = blocked_template()
    evidence["dataPolicy"] = "real-child-audio-retained"

    result = validator.validate_document(evidence)

    assert result["valid"] is False
    assert "dataPolicy must be adult-only-redacted-no-real-child-data" in result["errors"]


def test_evidence_rejects_impossible_calendar_timestamps():
    validator = load_validator()
    evidence = blocked_template()
    evidence["generatedAt"] = "2026-99-99T99:99:99+99:99"

    result = validator.validate_document(evidence)

    assert result["valid"] is False
    assert "generatedAt must be an ISO-8601 timestamp with timezone" in result["errors"]


def test_evidence_root_rejects_capture_symlinks(tmp_path):
    validator = load_validator()
    evidence = blocked_template()
    outside = tmp_path.parent / "outside-task07-capture.bin"
    outside.write_bytes(b"outside")
    capture = tmp_path / "capture.bin"
    capture.symlink_to(outside)
    evidence["captures"] = [
        {
            "path": "capture.bin",
            "sha256": hashlib.sha256(outside.read_bytes()).hexdigest(),
            "bytes": outside.stat().st_size,
            "mediaType": "application/octet-stream",
        }
    ]

    result = validator.validate_document(evidence, evidence_root=tmp_path)

    assert result["valid"] is False
    assert "capture file must not be a symlink: capture.bin" in result["errors"]


def test_physical_pass_requires_complete_lanes_and_rehearsed_rollback():
    validator = load_validator()
    evidence = passing_evidence()
    evidence["lanes"][2]["verdict"] = "BLOCKED"
    evidence["rollback"]["physicallyRehearsedKnownGoodV1"] = False

    result = validator.validate_document(evidence)

    assert result["valid"] is False
    assert "PHYSICAL_PASS requires every required lane to PASS" in result["errors"]
    assert "PHYSICAL_PASS requires a physically rehearsed known-good V1 rollback" in result["errors"]


def test_physical_pass_requires_approved_rollback_manifest_identity():
    validator = load_validator()
    evidence = passing_evidence()
    evidence["rollback"]["manifestSha256"] = "0" * 64

    result = validator.validate_document(evidence)

    assert result["valid"] is False
    assert "PHYSICAL_PASS rollback manifest does not match the approved rollback candidate" in result["errors"]


def test_physical_pass_requires_commands_to_pin_their_artifact_manifests():
    validator = load_validator()
    evidence = passing_evidence()
    evidence["commands"][0]["artifactManifestSha256"] = evidence["rollback"]["manifestSha256"]
    rollback = next(item for item in evidence["commands"] if item["actionClass"] == "rollback")
    rollback["artifactManifestSha256"] = evidence["candidate"]["firmwareIdentitySha256"]

    result = validator.validate_document(evidence)

    assert result["valid"] is False
    assert "candidate-install command must pin the approved candidate manifest" in result["errors"]
    assert "rollback command must pin the approved rollback manifest" in result["errors"]


def test_physical_pass_requires_safe_idle_microphone_proof_and_closed_privacy_findings():
    validator = load_validator()
    evidence = passing_evidence()
    evidence["privacy"]["safeIdleUplinkVerified"] = False
    evidence["privacy"]["openUnauthorizedUplinkFinding"] = True

    result = validator.validate_document(evidence)

    assert result["valid"] is False
    assert "PHYSICAL_PASS requires a verified zero-uplink safe-idle preflight" in result["errors"]
    assert "PHYSICAL_PASS cannot retain an open unauthorized microphone uplink finding" in result["errors"]


def test_physical_pass_requires_current_binding_two_adults_and_safety_environment():
    validator = load_validator()
    evidence = passing_evidence()
    evidence["robot"]["formalCurrentBindingVerified"] = False
    evidence["operators"][0]["present"] = False
    evidence["environment"]["motionEnvelopeClear"] = False
    evidence["environment"]["immediatePowerIsolationAvailable"] = False
    evidence["environment"]["productionAssignmentOff"] = False
    evidence["environment"]["globalV2FlagsOff"] = False

    result = validator.validate_document(evidence)

    assert result["valid"] is False
    assert "PHYSICAL_PASS requires verified current robot binding" in result["errors"]
    assert "PHYSICAL_PASS requires both adult operator roles present" in result["errors"]
    assert "PHYSICAL_PASS requires a clear motion envelope and immediate power isolation" in result["errors"]
    assert "PHYSICAL_PASS requires production assignments and global V2 flags to remain off" in result["errors"]


def test_physical_pass_requires_pinned_capture_files_and_measurements():
    validator = load_validator()
    evidence = passing_evidence()
    evidence["captures"][0]["sha256"] = "not-a-sha"
    evidence["lanes"][0]["measurements"] = []

    result = validator.validate_document(evidence)

    assert result["valid"] is False
    assert "capture captures/lane-1.json must have a lowercase SHA-256" in result["errors"]
    assert "lane baseline-hardware-health requires measurements for PASS" in result["errors"]


def test_physical_pass_rejects_generic_measurements_in_place_of_lane_contracts():
    validator = load_validator()
    evidence = passing_evidence()
    evidence["lanes"][0]["measurements"][0]["name"] = "operator-reviewed-contract"

    result = validator.validate_document(evidence)

    assert result["valid"] is False
    assert "lane baseline-hardware-health lacks required measurement supply-voltage" in result["errors"]


def test_physical_pass_requires_numeric_measurements_with_contract_units():
    validator = load_validator()
    evidence = passing_evidence()
    evidence["lanes"][0]["measurements"][0]["value"] = "nominal"
    evidence["lanes"][0]["measurements"][0]["minimum"] = "low"
    evidence["lanes"][0]["measurements"][0]["unit"] = "volts"

    result = validator.validate_document(evidence)

    assert result["valid"] is False
    assert "lane baseline-hardware-health measurement supply-voltage requires numeric value, minimum, and maximum" in result["errors"]
    assert "lane baseline-hardware-health measurement supply-voltage must use unit V" in result["errors"]


def test_physical_pass_rejects_measurement_outside_its_approved_bounds():
    validator = load_validator()
    evidence = passing_evidence()
    evidence["lanes"][0]["measurements"][0]["value"] = 2

    result = validator.validate_document(evidence)

    assert result["valid"] is False
    assert "lane baseline-hardware-health measurement supply-voltage is outside approved bounds" in result["errors"]


def test_physical_pass_requires_lane_end_at_or_after_start():
    validator = load_validator()
    evidence = passing_evidence()
    evidence["lanes"][0]["startedAt"] = "2026-08-22T09:05:00+07:00"
    evidence["lanes"][0]["endedAt"] = "2026-08-22T09:00:00+07:00"

    result = validator.validate_document(evidence)

    assert result["valid"] is False
    assert "lane baseline-hardware-health endedAt precedes startedAt" in result["errors"]


def test_command_evidence_uses_argv_and_explicit_authorization_not_shell_text():
    validator = load_validator()
    evidence = passing_evidence()
    evidence["commands"][1]["command"] = "esptool --port $PORT read-flash"

    result = validator.validate_document(evidence)

    assert result["valid"] is False
    assert "command evidence must not contain shell command text" in result["errors"]


def test_physical_pass_rejects_failed_required_command_evidence():
    validator = load_validator()
    evidence = passing_evidence()
    evidence["commands"][0]["exitCode"] = 1

    result = validator.validate_document(evidence)

    assert result["valid"] is False
    assert "PHYSICAL_PASS requires successful candidate-install command evidence" in result["errors"]


@pytest.mark.parametrize(
    ("action_class", "argv"),
    [
        ("candidate-install", ["true"]),
        ("readback", ["esptool", "write_flash"]),
        ("rollback", ["env", "sh", "restore.sh"]),
    ],
)
def test_physical_pass_requires_direct_action_specific_esptool_commands(action_class, argv):
    validator = load_validator()
    evidence = passing_evidence()
    command = next(item for item in evidence["commands"] if item["actionClass"] == action_class)
    command["argv"] = argv

    result = validator.validate_document(evidence)

    assert result["valid"] is False
    assert f"PHYSICAL_PASS requires verified {action_class} esptool operation" in result["errors"]


def test_physical_pass_does_not_combine_success_and_operation_across_commands():
    validator = load_validator()
    evidence = passing_evidence()
    evidence["commands"] = [
        {
            "actionClass": "candidate-install",
            "authorizedBy": "adult-operator",
            "executedAt": "2026-08-22T09:01:00+07:00",
            "exitCode": 0,
            "argv": ["true"],
        },
        {
            "actionClass": "candidate-install",
            "authorizedBy": "adult-operator",
            "executedAt": "2026-08-22T09:02:00+07:00",
            "exitCode": 1,
            "artifactManifestSha256": evidence["candidate"]["firmwareIdentitySha256"],
            "argv": next(item["argv"] for item in evidence["commands"] if item["actionClass"] == "candidate-install"),
        },
        *[item for item in evidence["commands"] if item["actionClass"] != "candidate-install"],
    ]

    result = validator.validate_document(evidence)

    assert result["valid"] is False
    assert "PHYSICAL_PASS requires successful verified candidate-install esptool operation" in result["errors"]


def test_failed_attempt_does_not_invalidate_a_later_verified_rerun():
    validator = load_validator()
    evidence = passing_evidence()
    evidence["commands"].insert(
        0,
        {
            "actionClass": "candidate-install",
            "authorizedBy": "adult-operator",
            "executedAt": "2026-08-22T09:00:30+07:00",
            "exitCode": 1,
            "artifactManifestSha256": evidence["candidate"]["firmwareIdentitySha256"],
            "argv": ["esptool", "write_flash", "0x0", "unrelated.bin"],
        },
    )

    result = validator.validate_document(evidence)

    assert "PHYSICAL_PASS requires verified candidate-install esptool operation" not in result["errors"]
    assert "PHYSICAL_PASS requires successful verified candidate-install esptool operation" not in result["errors"]


@pytest.mark.parametrize(
    "field",
    [
        "backendSha",
        "espSha",
        "firmwareSha",
        "reviewedExecutableSha",
        "task06ManifestSha256",
        "firmwareIdentitySha256",
    ],
)
def test_physical_pass_requires_exact_approved_candidate_identity(field):
    validator = load_validator()
    evidence = passing_evidence()
    evidence["candidate"][field] = "0" * len(evidence["candidate"][field])

    result = validator.validate_document(evidence)

    assert result["valid"] is False
    assert f"PHYSICAL_PASS candidate.{field} does not match the approved Task 06 candidate" in result["errors"]


def test_unlocked_pass_requires_the_submitted_remediated_candidate_digest():
    validator = load_validator()
    validator.APPROVED_PRIVACY_REMEDIATED_CANDIDATE = {
        "identity": {
            **validator.APPROVED_CANDIDATE,
            "firmwareIdentitySha256": "f" * 64,
        },
        "flashMap": validator.APPROVED_FLASH_MAP,
        "readbacks": validator.APPROVED_CANDIDATE_READBACKS,
        "readbackHashes": validator.APPROVED_CANDIDATE_READBACK_HASHES,
    }

    result = validator.validate_document(passing_evidence())

    assert result["valid"] is False
    assert "PHYSICAL_PASS candidate does not match the approved privacy-remediated identity" in result["errors"]


def test_unlocked_pass_uses_the_complete_replacement_candidate_mapping():
    validator = load_validator()
    evidence = passing_evidence()
    replacement = {
        **validator.APPROVED_CANDIDATE,
        "espSha": "1" * 40,
        "firmwareSha": "2" * 40,
        "firmwareIdentitySha256": "f" * 64,
    }
    replacement_flash_map = tuple(
        (offset, "xiaozhi-remediated.bin" if offset == "0x20000" else path)
        for offset, path in validator.APPROVED_FLASH_MAP
    )
    replacement_readbacks = tuple(
        (offset, "3612000", "readback/xiaozhi-remediated.bin")
        if offset == "0x20000"
        else (offset, size, output)
        for offset, size, output in validator.APPROVED_CANDIDATE_READBACKS
    )
    evidence["candidate"].update(replacement)
    for command in evidence["commands"]:
        if command["actionClass"] in {"candidate-install", "readback"} and command[
            "artifactManifestSha256"
        ] == validator.APPROVED_CANDIDATE["firmwareIdentitySha256"]:
            command["artifactManifestSha256"] = replacement["firmwareIdentitySha256"]
            if command["actionClass"] == "candidate-install":
                command["argv"] = [
                    "esptool",
                    "write_flash",
                    *[item for pair in replacement_flash_map for item in pair],
                ]
            elif command["argv"][2] == "0x20000":
                command["argv"] = [
                    "esptool",
                    "read_flash",
                    "0x20000",
                    "3612000",
                    "readback/xiaozhi-remediated.bin",
                ]
    validator.APPROVED_PRIVACY_REMEDIATED_CANDIDATE = {
        "identity": replacement,
        "flashMap": replacement_flash_map,
        "readbacks": replacement_readbacks,
        "readbackHashes": {
            output: (int(size, 0), str(index + 1) * 64)
            for index, (_offset, size, output) in enumerate(replacement_readbacks)
        },
    }

    result = validator.validate_document(evidence)

    assert not any("approved Task 06 candidate" in error for error in result["errors"])
    assert "PHYSICAL_PASS candidate does not match the approved privacy-remediated identity" not in result["errors"]
    assert "PHYSICAL_PASS requires verified candidate-install esptool operation" not in result["errors"]
    assert "PHYSICAL_PASS requires successful readback for candidate region 0x20000 size 3612000" not in result["errors"]


def test_incomplete_replacement_candidate_bundle_fails_closed():
    validator = load_validator()
    evidence = passing_evidence()
    evidence["candidate"]["espSha"] = "1" * 40
    validator.APPROVED_PRIVACY_REMEDIATED_CANDIDATE = {
        "identity": {
            "firmwareIdentitySha256": evidence["candidate"]["firmwareIdentitySha256"]
        },
        "flashMap": (),
        "readbacks": (),
        "readbackHashes": {},
    }

    result = validator.validate_document(evidence)

    assert "PHYSICAL_PASS replacement candidate approval bundle is incomplete" in result["errors"]
    assert any("approved Task 06 candidate" in error for error in result["errors"])


def test_replacement_bundle_rejects_readback_hash_byte_mismatch():
    validator = load_validator()
    evidence = passing_evidence()
    hashes = dict(validator.APPROVED_CANDIDATE_READBACK_HASHES)
    output = validator.APPROVED_CANDIDATE_READBACKS[0][2]
    hashes[output] = (1, hashes[output][1])
    validator.APPROVED_PRIVACY_REMEDIATED_CANDIDATE = {
        "identity": validator.APPROVED_CANDIDATE,
        "flashMap": validator.APPROVED_FLASH_MAP,
        "readbacks": validator.APPROVED_CANDIDATE_READBACKS,
        "readbackHashes": hashes,
    }

    result = validator.validate_document(evidence)

    assert "PHYSICAL_PASS replacement candidate approval bundle is incomplete" in result["errors"]


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("flashMap", ((0, "bootloader.bin"),)),
        ("readbacks", (("0x0", 1, "readback/bootloader.bin"),)),
    ],
)
def test_replacement_bundle_rejects_non_string_flash_operands(field, bad_value):
    validator = load_validator()
    evidence = passing_evidence()
    bundle = {
        "identity": validator.APPROVED_CANDIDATE,
        "flashMap": validator.APPROVED_FLASH_MAP,
        "readbacks": validator.APPROVED_CANDIDATE_READBACKS,
        "readbackHashes": validator.APPROVED_CANDIDATE_READBACK_HASHES,
    }
    bundle[field] = bad_value
    validator.APPROVED_PRIVACY_REMEDIATED_CANDIDATE = bundle

    result = validator.validate_document(evidence)

    assert "PHYSICAL_PASS replacement candidate approval bundle is incomplete" in result["errors"]


def test_physical_pass_rejects_measurement_bounds_without_pinned_limit_authority():
    validator = load_validator()
    validator.APPROVED_PRIVACY_REMEDIATED_CANDIDATE = {
        "identity": validator.APPROVED_CANDIDATE,
        "flashMap": validator.APPROVED_FLASH_MAP,
        "readbacks": validator.APPROVED_CANDIDATE_READBACKS,
        "readbackHashes": validator.APPROVED_CANDIDATE_READBACK_HASHES,
    }

    result = validator.validate_document(passing_evidence())

    assert "PHYSICAL_PASS has no approved hardware limit for baseline-hardware-health/supply-voltage" in result["errors"]


def test_physical_pass_requires_checksum_pinned_readback_capture():
    validator = load_validator()
    validator.APPROVED_PRIVACY_REMEDIATED_CANDIDATE = {
        "identity": validator.APPROVED_CANDIDATE,
        "flashMap": validator.APPROVED_FLASH_MAP,
        "readbacks": validator.APPROVED_CANDIDATE_READBACKS,
        "readbackHashes": validator.APPROVED_CANDIDATE_READBACK_HASHES,
    }

    result = validator.validate_document(passing_evidence())

    assert "PHYSICAL_PASS requires checksum-pinned candidate readback capture readback/bootloader.bin" in result["errors"]


@pytest.mark.parametrize("option", ["--help", "-h", "--version"])
def test_physical_pass_rejects_non_executing_esptool_commands(option):
    validator = load_validator()
    evidence = passing_evidence()
    evidence["commands"][0]["argv"].append(option)

    result = validator.validate_document(evidence)

    assert result["valid"] is False
    assert "PHYSICAL_PASS requires verified candidate-install esptool operation" in result["errors"]


def test_physical_pass_accepts_documented_python_launched_esptool_form(tmp_path):
    validator = load_validator()
    evidence = passing_evidence()
    evidence["commands"][0]["argv"] = [
        "/reviewed/idf/python",
        "/reviewed/idf/esptool.py",
        "--chip",
        "esp32s3",
        "write_flash",
        *evidence["commands"][0]["argv"][2:],
    ]
    capture = tmp_path / "captures/lane-1.json"
    capture.parent.mkdir()
    capture.write_bytes(b'{"result":"pass"}\n')
    evidence["captures"][0]["bytes"] = capture.stat().st_size
    evidence["captures"][0]["sha256"] = hashlib.sha256(capture.read_bytes()).hexdigest()

    result = validator.validate_document(evidence, evidence_root=tmp_path)

    assert result["errors"] == [
        "PHYSICAL_PASS is locked until a privacy-remediated candidate identity is approved"
    ]


def test_evidence_redacts_hyphen_separated_full_mac_identity():
    validator = load_validator()
    evidence = blocked_template()
    evidence["robot"]["identitySuffix"] = "AA-BB-CC-DD-EE-FF"

    result = validator.validate_document(evidence)

    assert result["valid"] is False
    assert "evidence must redact full MAC-like device identities" in result["errors"]


def test_physical_pass_rejects_unrelated_flash_and_readback_targets():
    validator = load_validator()
    evidence = passing_evidence()
    evidence["commands"] = [
        {
            **command,
            "argv": ["esptool", "write_flash", "0x0", "unrelated.bin"]
            if command["actionClass"] in {"candidate-install", "rollback"}
            else ["esptool", "read_flash", "0x0", "1", "unrelated.bin"],
        }
        for command in evidence["commands"]
    ]

    result = validator.validate_document(evidence)

    assert result["valid"] is False
    assert "PHYSICAL_PASS requires successful verified candidate-install esptool operation" in result["errors"]
    assert "PHYSICAL_PASS requires successful verified readback esptool operation" in result["errors"]
    assert "PHYSICAL_PASS requires successful verified rollback esptool operation" in result["errors"]


def test_physical_pass_requires_every_candidate_readback_region():
    validator = load_validator()
    evidence = passing_evidence()
    evidence["commands"] = [
        command
        for command in evidence["commands"]
        if command["argv"] != ["esptool", "read_flash", "0x800000", "5693495", "readback/generated_assets.bin"]
    ]

    result = validator.validate_document(evidence)

    assert result["valid"] is False
    assert "PHYSICAL_PASS requires successful readback for candidate region 0x800000 size 5693495" in result["errors"]


def test_physical_pass_requires_every_rollback_readback_region():
    validator = load_validator()
    evidence = passing_evidence()
    evidence["commands"] = [
        command
        for command in evidence["commands"]
        if command["argv"] != [
            "esptool",
            "read_flash",
            "0x20000",
            "3597792",
            "rollback-readback/xiaozhi.bin",
        ]
    ]

    result = validator.validate_document(evidence)

    assert result["valid"] is False
    assert "PHYSICAL_PASS requires successful readback for rollback region 0x20000 size 3597792" in result["errors"]


@pytest.mark.parametrize(
    "argv",
    [
        ["sh", "-c", "esptool write_flash"],
        ["env", "sh", "capture.sh"],
        ["sudo", "bash", "capture.sh"],
        ["esptool", "erase_flash"],
        ["esptool", "write_flash", "0x0", "merged-binary.bin"],
    ],
)
def test_command_evidence_rejects_unsafe_flash_forms(argv):
    validator = load_validator()
    evidence = passing_evidence()
    evidence["commands"][0]["argv"] = argv

    result = validator.validate_document(evidence)

    assert result["valid"] is False
    assert any(error.startswith("unsafe command evidence:") for error in result["errors"])


@pytest.mark.parametrize("field", ["backendSha", "espSha", "firmwareSha"])
def test_candidate_identity_requires_full_git_shas(field):
    validator = load_validator()
    evidence = copy.deepcopy(blocked_template())
    evidence["candidate"][field] = "deadbeef"

    result = validator.validate_document(evidence)

    assert result["valid"] is False
    assert f"candidate.{field} must be a full lowercase Git SHA" in result["errors"]
