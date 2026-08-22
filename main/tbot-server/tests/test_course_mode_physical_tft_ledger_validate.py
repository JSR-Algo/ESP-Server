import hashlib
import json
import sys
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SERVER = ROOT / "main/tbot-server"
TEMPLATE = ROOT / "docs/qa/artifacts/2026-08-22-course-mode-task07/physical-tft-ledger-template.json"
sys.path.insert(0, str(SERVER / "scripts"))

CUES = [
    ("cat-discover", "center", "teach/model", False),
    ("cat-meaning", "left", "listen", False),
    ("cat-joint-speech", "center", "teach/repeat", False),
    ("cat-recall", "center", "listen", True),
    ("cat-transfer", "right", "listen", True),
    ("ball-discover", "center", "teach/model", False),
    ("ball-meaning", "right", "listen", False),
    ("cat-delayed", "center", "listen", True),
]
CHECKS = [
    "background", "teachingObject", "robotOverlay", "caption",
    "listeningIndicator", "crop", "overlap", "zOrder", "focusAnchor",
    "flicker", "corruption", "reducedMotion",
]
PRODUCTION_CANDIDATE_TARGET = {
    "firmwareSha": "3d4a1e2a32359278124c61e56fd459fac618506e",
    "applicationSha256": "84c999ece0c90eb6e69a410e335c7791f330e9c0fd39c30dfd4162bb7c4cfc6e",
    "bundleRootSha256": "9ef3729d0faec7b02d867cedb3ab30d110b845b1c0133738c588bba0e0c16be6",
}
HISTORICAL_INSTALLATION_PROVENANCE = {
    "preservedNvsSha256": "a7a87f72416be20388298cb70cfff306ec78e77f0e8b09231d16113f3d82404e",
}
SESSION_NVS_SHA256 = "0" * 64
ACTIVE_LAB_APP = {
    "firmwareSha": "aef1034f859b35efc93215106eb3be89f10f6c66",
    "applicationSha256": "c" * 64,
    "bundleRootSha256": "d" * 64,
}
IDS = {
    "courseId": "70000000-0000-4000-8000-000000000003",
    "lessonId": "70000000-0000-4000-8000-000000000004",
    "deviceId": "70000000-0000-4000-8000-000000000005",
    "assignmentId": "70000000-0000-4000-8000-000000000006",
    "adultOperatorId": "70000000-0000-4000-8000-000000000007",
}
BACKEND_SHA = "0123456789abcdef0123456789abcdef01234567"
BACKEND_IMAGE = f"local/tbot-backend:course-mode-physical-tft-{BACKEND_SHA}"
BACKEND_IMAGE_ID = "sha256:" + "b" * 64
VALID_RECEIPT = {
    "result": "pass",
    "deviceSuffix": "AC:20",
    "lessonKey": "course-mode-pilot-cat-ball",
    "lessonVersion": 1,
    "rendererId": "teebot-lesson-renderer.v4",
    "contractChecksum": "cf12b1a5f71f0a80a8ee22bb2cdc775ada5b803e26d154e5d29c76b14c9fb264",
    "layoutChecksum": "e61b56d1f8219a86c7f3986e7d5c70b91f512286604b5b206ef11e2c989d275c",
    "manifestChecksum": "a" * 64,
    "cueCount": 8,
    "conversationPresent": False,
}


def _artifact(root: Path, session: Path, artifact_id: str, content: bytes, *, direct=False):
    path = session / f"{artifact_id}.bin"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return {
        "id": artifact_id,
        "path": str(path.relative_to(root)),
        "bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "redactionStatus": "REDACTED",
        "directVisual": direct,
    }


def _json_artifact(root: Path, session: Path, artifact_id: str, document: object):
    content = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()
    artifact = _artifact(root, session, artifact_id, content)
    old_path = root / artifact["path"]
    new_path = old_path.with_suffix(".json")
    old_path.rename(new_path)
    artifact["path"] = str(new_path.relative_to(root))
    return artifact


def complete_ledger(repository_root: Path):
    session = repository_root / "task-artifacts/course-mode-task07/tft-20260822T000000Z-ac20"
    endpoints = {
        "backendBaseUrl": "http://127.0.0.1:3000",
        "espHttpUrl": "http://host.docker.internal:8003",
        "assetOrigin": "http://192.168.100.183:8102/",
        "otaUrl": "http://192.168.100.183:8003/ota",
        "websocketUrl": "ws://192.168.100.183:8003/ws",
        "authority": "approved-local-task07-lab-route",
    }
    preflight = {
        "valid": True,
        "result": "PASS",
        "backendSha": BACKEND_SHA,
        "backendImage": BACKEND_IMAGE,
        "imageId": BACKEND_IMAGE_ID,
        "composeProject": "tbot-course-mode-physical-tft",
        "productionCandidateTarget": deepcopy(PRODUCTION_CANDIDATE_TARGET),
        "historicalInstallationProvenance": deepcopy(HISTORICAL_INSTALLATION_PROVENANCE),
        "sessionNvsBaseline": {"beforeInstallSha256": SESSION_NVS_SHA256},
        "activeLabApp": deepcopy(ACTIVE_LAB_APP),
        "deviceSuffix": "AC:20",
        "syntheticIds": deepcopy(IDS),
        "endpoints": deepcopy(endpoints),
        "endpointAuthority": "approved-local-task07-lab-route",
        "sessionStartedAt": "2026-08-22T00:00:00Z",
        "secrets": {
            "JWT_PUBLIC_KEY": "present-redacted",
            "TBOT_DEVICE_MINT_SECRET": "present-redacted",
        },
    }
    artifacts = [
        _json_artifact(repository_root, session, "preflight", preflight),
        _json_artifact(repository_root, session, "receipt-first", VALID_RECEIPT),
        _json_artifact(repository_root, session, "receipt-rerun", VALID_RECEIPT),
    ]
    rows = []
    for index, (cue, focus, visual_mode, assessment) in enumerate(CUES):
        frame = _artifact(repository_root, session, f"frame-{index}", f"frame-{index}".encode(), direct=True)
        artifacts.append(frame)
        rows.append({
            "timestamp": f"2026-08-22T00:00:{10 + index:02d}Z",
            "cueId": cue,
            "activityId": cue,
            "expectedFocus": focus,
            "expectedVisualMode": visual_mode,
            "assessment": assessment,
            "operatorVerdict": "PASS",
            "observerVerdict": "PASS",
            "frameRefs": [frame["id"]],
            "visualChecklist": {check: True for check in CHECKS},
        })
    markers = ["authenticated-ac20-websocket", "app-ready", "lesson_prepare", "lesson_start"]
    for cue, *_ in CUES:
        markers.extend([f"cue-transition:{cue}", f"cue-ack:{cue}"])
    markers.extend(["lesson-complete", "lesson-stop", "quiescent-rest"])
    return {
        "schemaVersion": 1,
        "gate": "course-mode-v2-task07-physical-tft",
        "declaredTftVerdict": "TFT_PASS",
        "task07Verdict": "PHYSICAL_BLOCKED",
        "productionCandidateTarget": deepcopy(PRODUCTION_CANDIDATE_TARGET),
        "historicalInstallationProvenance": deepcopy(HISTORICAL_INSTALLATION_PROVENANCE),
        "sessionNvsPreservation": {
            "phase": "POST_RESTORE",
            "beforeInstallSha256": SESSION_NVS_SHA256,
            "afterInstallSha256": SESSION_NVS_SHA256,
            "afterRestoreSha256": SESSION_NVS_SHA256,
        },
        "activeLabApp": deepcopy(ACTIVE_LAB_APP),
        "backend": {
            "sha": BACKEND_SHA,
            "image": BACKEND_IMAGE,
            "imageId": BACKEND_IMAGE_ID,
        },
        "protectedTest": {
            "path": "/Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/tbot-server/tests/test_lesson_voice_output_discipline.py",
            "sha256": "08f77b5452301224b17b4b333d2d032fff40c06aa2eaea97fa90932dae7d97e3",
        },
        "identity": {
            "deviceSuffix": "AC:20",
            "lessonKey": "course-mode-pilot-cat-ball",
            "lessonVersion": 1,
            "rendererId": "teebot-lesson-renderer.v4",
            "contractChecksum": "cf12b1a5f71f0a80a8ee22bb2cdc775ada5b803e26d154e5d29c76b14c9fb264",
            "layoutChecksum": "e61b56d1f8219a86c7f3986e7d5c70b91f512286604b5b206ef11e2c989d275c",
            "manifestChecksum": "a" * 64,
            "syntheticIds": deepcopy(IDS),
        },
        "endpoints": endpoints,
        "bindings": {
            "preflightArtifactId": "preflight",
            "preflightSha256": artifacts[0]["sha256"],
            "firstReceiptArtifactId": "receipt-first",
            "firstReceiptSha256": artifacts[1]["sha256"],
            "rerunReceiptArtifactId": "receipt-rerun",
            "rerunReceiptSha256": artifacts[2]["sha256"],
        },
        "operators": {
            "operatorName": "Alex Adult",
            "observerName": "David Adult",
            "adultOnly": True,
            "soleLease": True,
            "clearMotionEnvelope": True,
            "immediatePowerIsolation": True,
            "startedAt": "2026-08-22T00:00:00Z",
            "endedAt": "2026-08-22T00:01:00Z",
        },
        "artifacts": artifacts,
        "runtimeMarkers": markers,
        "cues": rows,
        "finalRest": {
            "stableScreen": True,
            "headCentered": True,
            "armsLowered": True,
            "noContinuedMovement": True,
            "noChatter": True,
            "noBinding": True,
            "noVibration": True,
            "noOdor": True,
            "noUnusualHeat": True,
            "stablePower": True,
            "noPrivateData": True,
            "noPrivacyUplink": True,
        },
        "stopConditions": [],
        "stopPhase": "NOT_REQUIRED",
        "unavailableEvidence": [],
        "stopOutcome": {
            "safeState": "NOT_REQUIRED",
            "powerIsolation": "NOT_REQUIRED",
        },
        "privacyOutcome": {
            "privateDataObserved": False,
            "unauthorizedUplinkObserved": False,
        },
        "physicalActions": {
            "stackStart": "OPERATOR_AUTHORIZED_ATTENDED",
            "captureTrigger": "OPERATOR_AUTHORIZED_ATTENDED",
            "cleanup": "NOT_PERFORMED",
        },
        "numericLimits": [{
            "lane": "physical-tft-sub-lane",
            "status": "NOT_MEASURED",
            "authorityReference": "Task 07 master prompt; no numeric limit defined by this tooling",
        }],
        "outstandingBlockers": ["remaining-task07-physical-lanes"],
    }


def test_committed_template_is_structurally_valid_blocked():
    from course_mode_physical_tft_ledger_validate import validate_ledger

    result = validate_ledger(json.loads(TEMPLATE.read_text(encoding="utf-8")), repository_root=ROOT)
    assert result == {
        "valid": True,
        "tftVerdict": "TFT_BLOCKED",
        "task07Verdict": "PHYSICAL_BLOCKED",
        "reasons": [],
    }


def test_complete_attended_fixture_is_tft_pass_but_task07_stays_blocked(tmp_path):
    from course_mode_physical_tft_ledger_validate import validate_ledger

    result = validate_ledger(complete_ledger(tmp_path), repository_root=tmp_path)
    assert result["valid"] is True
    assert result["tftVerdict"] == "TFT_PASS"
    assert result["task07Verdict"] == "PHYSICAL_BLOCKED"
    assert result["reasons"] == []


def test_ledger_rejects_conflated_or_mismatched_firmware_identities(tmp_path):
    from course_mode_physical_tft_ledger_validate import validate_ledger

    active_mismatch = complete_ledger(tmp_path / "active")
    active_mismatch["activeLabApp"]["applicationSha256"] = "e" * 64
    result = validate_ledger(active_mismatch, repository_root=tmp_path / "active")
    assert "bindings.preflight.semantic" in result["reasons"]

    target_mismatch = complete_ledger(tmp_path / "target")
    target_mismatch["productionCandidateTarget"]["firmwareSha"] = ACTIVE_LAB_APP["firmwareSha"]
    result = validate_ledger(target_mismatch, repository_root=tmp_path / "target")
    assert "productionCandidateTarget" in result["reasons"]

    provenance_mismatch = complete_ledger(tmp_path / "provenance")
    provenance_mismatch["historicalInstallationProvenance"]["preservedNvsSha256"] = "f" * 64
    result = validate_ledger(provenance_mismatch, repository_root=tmp_path / "provenance")
    assert "historicalInstallationProvenance" in result["reasons"]


def test_session_nvs_preservation_requires_monotonic_exact_equal_evidence(tmp_path):
    from course_mode_physical_tft_ledger_validate import validate_ledger

    cases = [
        (
            "not-an-object",
            "sessionNvsPreservation",
        ),
        (
            {"beforeInstallSha256": SESSION_NVS_SHA256,
             "afterInstallSha256": None, "afterRestoreSha256": None},
            "sessionNvsPreservation",
        ),
        (
            {"phase": "UNKNOWN", "beforeInstallSha256": SESSION_NVS_SHA256,
             "afterInstallSha256": None, "afterRestoreSha256": None},
            "sessionNvsPreservation.phase",
        ),
        (
            {"phase": "PRE_INSTALL_BASELINE", "beforeInstallSha256": SESSION_NVS_SHA256,
             "afterInstallSha256": SESSION_NVS_SHA256, "afterRestoreSha256": None},
            "sessionNvsPreservation.phase",
        ),
        (
            {"phase": "POST_INSTALL", "beforeInstallSha256": SESSION_NVS_SHA256,
             "afterInstallSha256": "1" * 64, "afterRestoreSha256": None},
            "sessionNvsPreservation.equality",
        ),
        (
            {"phase": "POST_RESTORE", "beforeInstallSha256": SESSION_NVS_SHA256,
             "afterInstallSha256": SESSION_NVS_SHA256, "afterRestoreSha256": None},
            "sessionNvsPreservation.afterRestoreSha256",
        ),
        (
            {"phase": "POST_RESTORE", "beforeInstallSha256": SESSION_NVS_SHA256,
             "afterInstallSha256": SESSION_NVS_SHA256, "afterRestoreSha256": "A" * 64},
            "sessionNvsPreservation.afterRestoreSha256",
        ),
    ]
    for index, (preservation, reason) in enumerate(cases):
        root = tmp_path / str(index)
        document = complete_ledger(root)
        document["sessionNvsPreservation"] = preservation
        result = validate_ledger(document, repository_root=root)
        assert result["valid"] is False
        assert reason in result["reasons"]


def test_session_nvs_before_install_must_match_bound_preflight(tmp_path):
    from course_mode_physical_tft_ledger_validate import validate_ledger

    document = complete_ledger(tmp_path)
    document["sessionNvsPreservation"] = {
        "phase": "POST_RESTORE",
        "beforeInstallSha256": "1" * 64,
        "afterInstallSha256": "1" * 64,
        "afterRestoreSha256": "1" * 64,
    }

    result = validate_ledger(document, repository_root=tmp_path)

    assert result["valid"] is False
    assert "bindings.preflight.semantic" in result["reasons"]


def test_tft_pass_requires_post_restore_nvs_evidence(tmp_path):
    from course_mode_physical_tft_ledger_validate import validate_ledger

    document = complete_ledger(tmp_path)
    document["sessionNvsPreservation"] = {
        "phase": "POST_INSTALL",
        "beforeInstallSha256": SESSION_NVS_SHA256,
        "afterInstallSha256": SESSION_NVS_SHA256,
        "afterRestoreSha256": None,
    }

    result = validate_ledger(document, repository_root=tmp_path)

    assert result["valid"] is False
    assert "sessionNvsPreservation.pass_phase" in result["reasons"]


def test_nvs_evidence_phases_accept_only_the_observations_already_made(tmp_path):
    from course_mode_physical_tft_ledger_validate import validate_ledger

    phases = [
        {
            "phase": "PRE_INSTALL_BASELINE",
            "beforeInstallSha256": SESSION_NVS_SHA256,
            "afterInstallSha256": None,
            "afterRestoreSha256": None,
        },
        {
            "phase": "POST_INSTALL",
            "beforeInstallSha256": SESSION_NVS_SHA256,
            "afterInstallSha256": SESSION_NVS_SHA256,
            "afterRestoreSha256": None,
        },
        {
            "phase": "POST_RESTORE",
            "beforeInstallSha256": SESSION_NVS_SHA256,
            "afterInstallSha256": SESSION_NVS_SHA256,
            "afterRestoreSha256": SESSION_NVS_SHA256,
        },
    ]
    for index, preservation in enumerate(phases):
        root = tmp_path / str(index)
        document = complete_ledger(root)
        document["declaredTftVerdict"] = "TFT_FAIL"
        document["stopConditions"] = ["operator-stop"]
        document["stopPhase"] = "DURING_ATTENDED_CAPTURE"
        document["stopOutcome"] = {
            "safeState": "RESTORED",
            "powerIsolation": "ISOLATED",
        }
        document["sessionNvsPreservation"] = preservation

        assert validate_ledger(document, repository_root=root)["valid"] is True

    root = tmp_path / "not-observed"
    document = complete_ledger(root)
    document["declaredTftVerdict"] = "TFT_FAIL"
    document["stopConditions"] = ["operator-stop"]
    document["stopPhase"] = "DURING_ATTENDED_CAPTURE"
    document["stopOutcome"] = {"safeState": "RESTORED", "powerIsolation": "ISOLATED"}
    document["sessionNvsPreservation"] = {
        "phase": "NOT_OBSERVED",
        "beforeInstallSha256": None,
        "afterInstallSha256": None,
        "afterRestoreSha256": None,
    }

    result = validate_ledger(document, repository_root=root)
    assert "sessionNvsPreservation.preflight_phase" in result["reasons"]


def test_ledger_accepts_new_supplied_active_lab_hashes_when_preflight_matches(tmp_path):
    from course_mode_physical_tft_ledger_validate import validate_ledger

    document = complete_ledger(tmp_path)
    active = deepcopy(ACTIVE_LAB_APP)
    active["applicationSha256"] = "e" * 64
    active["bundleRootSha256"] = "f" * 64
    document["activeLabApp"] = active
    artifact = document["artifacts"][0]
    path = tmp_path / artifact["path"]
    preflight = json.loads(path.read_text(encoding="utf-8"))
    preflight["activeLabApp"] = active
    content = (json.dumps(preflight, sort_keys=True, separators=(",", ":")) + "\n").encode()
    path.write_bytes(content)
    artifact["bytes"] = len(content)
    artifact["sha256"] = hashlib.sha256(content).hexdigest()
    document["bindings"]["preflightSha256"] = artifact["sha256"]

    assert validate_ledger(document, repository_root=tmp_path)["valid"] is True


def test_pass_rejects_hash_valid_but_semantically_arbitrary_preflight_and_receipts(tmp_path):
    from course_mode_physical_tft_ledger_validate import validate_ledger

    cases = [
        (0, {"arbitrary": "preflight"}, "bindings.preflight.semantic"),
        (1, {"arbitrary": "receipt"}, "bindings.firstReceipt.semantic"),
        (2, {"arbitrary": "receipt"}, "bindings.rerunReceipt.semantic"),
    ]
    for index, replacement, reason in cases:
        root = tmp_path / str(index)
        document = complete_ledger(root)
        artifact = document["artifacts"][index]
        path = root / artifact["path"]
        content = (json.dumps(replacement) + "\n").encode()
        path.write_bytes(content)
        artifact["bytes"] = len(content)
        artifact["sha256"] = hashlib.sha256(content).hexdigest()
        prefix = ("preflight", "firstReceipt", "rerunReceipt")[index]
        document["bindings"][f"{prefix}Sha256"] = artifact["sha256"]

        result = validate_ledger(document, repository_root=root)

        assert result["valid"] is False
        assert reason in result["reasons"]


def test_pass_rejects_semantically_different_validator_valid_receipts(tmp_path):
    from course_mode_physical_tft_ledger_validate import validate_ledger

    document = complete_ledger(tmp_path)
    rerun = deepcopy(VALID_RECEIPT)
    rerun["manifestChecksum"] = "b" * 64
    artifact = document["artifacts"][2]
    path = tmp_path / artifact["path"]
    content = (json.dumps(rerun, sort_keys=True) + "\n").encode()
    path.write_bytes(content)
    artifact["bytes"] = len(content)
    artifact["sha256"] = hashlib.sha256(content).hexdigest()
    document["bindings"]["rerunReceiptSha256"] = artifact["sha256"]

    result = validate_ledger(document, repository_root=tmp_path)

    assert result["valid"] is False
    assert "bindings.receipt_pair.semantic" in result["reasons"]


def test_pass_accepts_semantically_equal_receipts_with_different_json_bytes(tmp_path):
    from course_mode_physical_tft_ledger_validate import validate_ledger

    document = complete_ledger(tmp_path)
    artifact = document["artifacts"][2]
    path = tmp_path / artifact["path"]
    content = json.dumps(dict(reversed(list(VALID_RECEIPT.items()))), indent=2).encode()
    path.write_bytes(content)
    artifact["bytes"] = len(content)
    artifact["sha256"] = hashlib.sha256(content).hexdigest()
    document["bindings"]["rerunReceiptSha256"] = artifact["sha256"]

    result = validate_ledger(document, repository_root=tmp_path)

    assert result["valid"] is True


def test_verdict_and_stop_phase_require_truthful_physical_actions(tmp_path):
    from course_mode_physical_tft_ledger_validate import validate_ledger

    passed = complete_ledger(tmp_path / "pass")
    passed["physicalActions"]["stackStart"] = "NOT_PERFORMED"
    result = validate_ledger(passed, repository_root=tmp_path / "pass")
    assert "physicalActions.pass" in result["reasons"]

    early = complete_ledger(tmp_path / "early")
    early["declaredTftVerdict"] = "TFT_FAIL"
    early["artifacts"] = []
    early["backend"] = {"sha": None, "image": None, "imageId": None}
    early["bindings"] = {}
    early["runtimeMarkers"] = []
    early["cues"] = []
    early["finalRest"] = {}
    early["stopConditions"] = ["power-instability"]
    early["stopPhase"] = "PRE_PREFLIGHT"
    early["unavailableEvidence"] = [
        {"evidence": "preflight", "reason": "stop-before-preflight"},
        {"evidence": "firstReceipt", "reason": "stop-before-materialization"},
        {"evidence": "rerunReceipt", "reason": "stop-before-materialization"},
    ]
    early["stopOutcome"] = {"safeState": "RESTORED", "powerIsolation": "ISOLATED"}
    result = validate_ledger(early, repository_root=tmp_path / "early")
    assert "physicalActions.fail_phase" in result["reasons"]


def test_operator_times_must_be_iso8601_utc_and_chronological(tmp_path):
    from course_mode_physical_tft_ledger_validate import validate_ledger

    malformed = complete_ledger(tmp_path / "malformed")
    malformed["operators"]["startedAt"] = "2026-08-22 00:00:00"
    result = validate_ledger(malformed, repository_root=tmp_path / "malformed")
    assert "operators.timestamps" in result["reasons"]

    reversed_times = complete_ledger(tmp_path / "reversed")
    reversed_times["operators"]["endedAt"] = "2026-08-21T23:59:59Z"
    result = validate_ledger(reversed_times, repository_root=tmp_path / "reversed")
    assert "operators.chronology" in result["reasons"]


def test_physical_stop_marker_yields_valid_tft_fail(tmp_path):
    from course_mode_physical_tft_ledger_validate import validate_ledger

    document = complete_ledger(tmp_path)
    document["declaredTftVerdict"] = "TFT_FAIL"
    document["stopConditions"] = ["unexpected-motion"]
    document["stopPhase"] = "DURING_ATTENDED_CAPTURE"
    document["stopOutcome"] = {
        "safeState": "RESTORED",
        "powerIsolation": "ISOLATED",
    }
    result = validate_ledger(document, repository_root=tmp_path)
    assert result["valid"] is True
    assert result["tftVerdict"] == "TFT_FAIL"
    assert result["task07Verdict"] == "PHYSICAL_BLOCKED"


def test_early_physical_stop_accepts_partial_prefix_evidence_and_explicit_outcome(tmp_path):
    from course_mode_physical_tft_ledger_validate import validate_ledger

    document = complete_ledger(tmp_path)
    document["declaredTftVerdict"] = "TFT_FAIL"
    document["runtimeMarkers"] = document["runtimeMarkers"][:6]
    document["cues"] = document["cues"][:1]
    document["cues"][0]["operatorVerdict"] = "FAIL"
    document["cues"][0]["observerVerdict"] = "FAIL"
    document["finalRest"] = {}
    document["stopConditions"] = ["unexpected-motion"]
    document["stopPhase"] = "DURING_ATTENDED_CAPTURE"
    document["stopOutcome"] = {
        "safeState": "RESTORED",
        "powerIsolation": "ISOLATED",
    }

    result = validate_ledger(document, repository_root=tmp_path)

    assert result == {
        "valid": True,
        "tftVerdict": "TFT_FAIL",
        "task07Verdict": "PHYSICAL_BLOCKED",
        "reasons": [],
    }


def test_early_physical_stop_requires_explicit_safe_state_and_power_outcomes(tmp_path):
    from course_mode_physical_tft_ledger_validate import validate_ledger

    document = complete_ledger(tmp_path)
    document["declaredTftVerdict"] = "TFT_FAIL"
    document["runtimeMarkers"] = document["runtimeMarkers"][:4]
    document["cues"] = []
    document["finalRest"] = {}
    document["stopConditions"] = ["power-instability"]
    document["stopPhase"] = "DURING_ATTENDED_CAPTURE"
    document["stopOutcome"] = {}

    result = validate_ledger(document, repository_root=tmp_path)

    assert result["valid"] is False
    assert "stopOutcome" in result["reasons"]


def test_physical_stop_before_first_runtime_marker_is_a_valid_empty_prefix(tmp_path):
    from course_mode_physical_tft_ledger_validate import validate_ledger

    document = complete_ledger(tmp_path)
    document["declaredTftVerdict"] = "TFT_FAIL"
    document["runtimeMarkers"] = []
    document["cues"] = []
    document["finalRest"] = {}
    document["stopConditions"] = ["power-instability"]
    document["stopPhase"] = "DURING_ATTENDED_CAPTURE"
    document["stopOutcome"] = {
        "safeState": "NOT_RESTORED",
        "powerIsolation": "ISOLATED",
    }

    result = validate_ledger(document, repository_root=tmp_path)

    assert result["valid"] is True
    assert result["tftVerdict"] == "TFT_FAIL"


def test_pre_preflight_stop_is_valid_without_preflight_or_receipt_artifacts(tmp_path):
    from course_mode_physical_tft_ledger_validate import validate_ledger

    document = complete_ledger(tmp_path)
    document["declaredTftVerdict"] = "TFT_FAIL"
    document["artifacts"] = []
    document["backend"] = {"sha": None, "image": None, "imageId": None}
    document["bindings"] = {}
    document["runtimeMarkers"] = []
    document["cues"] = []
    document["finalRest"] = {}
    document["stopConditions"] = ["power-instability"]
    document["stopPhase"] = "PRE_PREFLIGHT"
    document["unavailableEvidence"] = [
        {"evidence": "preflight", "reason": "stop-before-preflight"},
        {"evidence": "firstReceipt", "reason": "stop-before-materialization"},
        {"evidence": "rerunReceipt", "reason": "stop-before-materialization"},
    ]
    document["stopOutcome"] = {
        "safeState": "NOT_RESTORED",
        "powerIsolation": "ISOLATED",
    }
    document["physicalActions"] = {
        "stackStart": "NOT_PERFORMED",
        "captureTrigger": "NOT_PERFORMED",
        "cleanup": "NOT_PERFORMED",
    }
    document["sessionNvsPreservation"] = {
        "phase": "NOT_OBSERVED",
        "beforeInstallSha256": None,
        "afterInstallSha256": None,
        "afterRestoreSha256": None,
    }

    result = validate_ledger(document, repository_root=tmp_path)

    assert result == {
        "valid": True,
        "tftVerdict": "TFT_FAIL",
        "task07Verdict": "PHYSICAL_BLOCKED",
        "reasons": [],
    }


def test_fail_phase_requires_only_evidence_available_by_that_phase(tmp_path):
    from course_mode_physical_tft_ledger_validate import validate_ledger

    document = complete_ledger(tmp_path)
    document["declaredTftVerdict"] = "TFT_FAIL"
    document["artifacts"] = document["artifacts"][:1]
    document["bindings"] = {
        "preflightArtifactId": "preflight",
        "preflightSha256": document["artifacts"][0]["sha256"],
    }
    document["runtimeMarkers"] = []
    document["cues"] = []
    document["finalRest"] = {}
    document["stopConditions"] = ["configuration-stop"]
    document["stopPhase"] = "POST_PREFLIGHT"
    document["unavailableEvidence"] = [
        {"evidence": "firstReceipt", "reason": "stop-before-materialization"},
        {"evidence": "rerunReceipt", "reason": "stop-before-materialization"},
    ]
    document["stopOutcome"] = {
        "safeState": "RESTORED",
        "powerIsolation": "ISOLATED",
    }
    document["physicalActions"] = {
        "stackStart": "NOT_PERFORMED",
        "captureTrigger": "NOT_PERFORMED",
        "cleanup": "NOT_PERFORMED",
    }

    assert validate_ledger(document, repository_root=tmp_path)["valid"] is True

    del document["bindings"]["preflightSha256"]
    result = validate_ledger(document, repository_root=tmp_path)
    assert result["valid"] is False
    assert "bindings.preflight" in result["reasons"]


def test_fail_phase_rejects_missing_or_inconsistent_unavailable_evidence(tmp_path):
    from course_mode_physical_tft_ledger_validate import validate_ledger

    document = complete_ledger(tmp_path)
    document["declaredTftVerdict"] = "TFT_FAIL"
    document["runtimeMarkers"] = []
    document["cues"] = []
    document["finalRest"] = {}
    document["stopConditions"] = ["configuration-stop"]
    document["stopPhase"] = "POST_RERUN_RECEIPT"
    document["unavailableEvidence"] = [
        {"evidence": "preflight", "reason": "incorrectly-marked-unavailable"}
    ]
    document["stopOutcome"] = {
        "safeState": "RESTORED",
        "powerIsolation": "ISOLATED",
    }

    result = validate_ledger(document, repository_root=tmp_path)

    assert result["valid"] is False
    assert "unavailableEvidence.phase" in result["reasons"]


def test_ledger_endpoints_must_be_private_credential_free_local_lab_routes(tmp_path):
    from course_mode_physical_tft_ledger_validate import validate_ledger

    mutations = [
        ("assetOrigin", "http://192.168.100.183:8102/assets", "endpoints.assetOrigin"),
        ("assetOrigin", "https://192.168.100.183:8102/", "endpoints.assetOrigin"),
        ("otaUrl", "http://user:pass@192.168.100.183:8003/ota", "endpoints.otaUrl"),
        ("otaUrl", "http://8.8.8.8:8003/ota", "endpoints.otaUrl"),
        ("otaUrl", "http://127.0.0.1:8003/ota", "endpoints.otaUrl"),
        ("websocketUrl", "wss://192.168.100.183:8003/ws", "endpoints.websocketUrl"),
        ("websocketUrl", "ws://production.example/ws", "endpoints.websocketUrl"),
    ]
    for index, (field, value, reason) in enumerate(mutations):
        root = tmp_path / str(index)
        document = complete_ledger(root)
        document["endpoints"][field] = value
        result = validate_ledger(document, repository_root=root)
        assert result["valid"] is False
        assert reason in result["reasons"]


def test_tft_pass_rejects_observed_privacy_stop(tmp_path):
    from course_mode_physical_tft_ledger_validate import validate_ledger

    document = complete_ledger(tmp_path)
    document["privacyOutcome"]["unauthorizedUplinkObserved"] = True

    result = validate_ledger(document, repository_root=tmp_path)

    assert result["valid"] is False
    assert "privacyOutcome.pass" in result["reasons"]


def test_cue_visual_and_two_adult_requirements_fail_closed(tmp_path):
    from course_mode_physical_tft_ledger_validate import validate_ledger

    mutations = [
        (lambda d: d["cues"].pop(), "cues.order"),
        (lambda d: d["cues"].reverse(), "cues.order"),
        (lambda d: d["cues"][0].update(frameRefs=[]), "cues.direct_visual"),
        (lambda d: d["cues"][0].update(observerVerdict="NOT_RECORDED"), "cues.two_adult_verdict"),
        (lambda d: d["cues"][0].update(observerVerdict="FAIL"), "cues.disagreement"),
        (lambda d: d["cues"][0]["visualChecklist"].pop("crop"), "cues.visual_checklist"),
        (lambda d: d["finalRest"].pop("armsLowered"), "finalRest"),
    ]
    for mutate, reason in mutations:
        document = complete_ledger(tmp_path / reason.replace(".", "-"))
        mutate(document)
        result = validate_ledger(document, repository_root=tmp_path / reason.replace(".", "-"))
        assert result["valid"] is False
        assert reason in result["reasons"]


def test_privacy_path_artifact_and_binding_failures_are_rejected(tmp_path):
    from course_mode_physical_tft_ledger_validate import validate_ledger

    cases = []
    private = complete_ledger(tmp_path / "private")
    private["privateTranscript"] = "do-not-retain"
    cases.append((private, tmp_path / "private", "privacy.forbidden_content"))
    token = complete_ledger(tmp_path / "token")
    token["token"] = "do-not-retain-token"
    cases.append((token, tmp_path / "token", "privacy.forbidden_content"))
    extra = complete_ledger(tmp_path / "extra")
    extra["unexpected"] = "benign"
    cases.append((extra, tmp_path / "extra", "schema.extra_field.unexpected"))
    full_mac = complete_ledger(tmp_path / "mac")
    full_mac["identity"]["deviceSuffix"] = "14:c1:9f:d1:ac:20"
    cases.append((full_mac, tmp_path / "mac", "privacy.forbidden_content"))
    absolute = complete_ledger(tmp_path / "absolute")
    absolute["artifacts"][0]["path"] = "/tmp/external"
    cases.append((absolute, tmp_path / "absolute", "artifacts.path"))
    missing = complete_ledger(tmp_path / "missing")
    Path(tmp_path / "missing" / missing["artifacts"][0]["path"]).unlink()
    cases.append((missing, tmp_path / "missing", "artifacts.missing"))
    drift = complete_ledger(tmp_path / "drift")
    drift["artifacts"][0]["bytes"] += 1
    cases.append((drift, tmp_path / "drift", "artifacts.size"))
    binding = complete_ledger(tmp_path / "binding")
    binding["bindings"]["preflightSha256"] = "b" * 64
    cases.append((binding, tmp_path / "binding", "bindings.preflight"))
    artifact_private = complete_ledger(tmp_path / "artifact-private")
    private_path = tmp_path / "artifact-private" / artifact_private["artifacts"][0]["path"]
    private_path.write_bytes(b"token=do-not-retain")
    artifact_private["artifacts"][0]["bytes"] = len(b"token=do-not-retain")
    artifact_private["artifacts"][0]["sha256"] = hashlib.sha256(b"token=do-not-retain").hexdigest()
    artifact_private["bindings"]["preflightSha256"] = artifact_private["artifacts"][0]["sha256"]
    cases.append((artifact_private, tmp_path / "artifact-private", "artifacts.privacy"))

    for document, root, reason in cases:
        result = validate_ledger(document, repository_root=root)
        assert result["valid"] is False
        assert reason in result["reasons"]
        assert "do-not-retain" not in json.dumps(result)


def test_symlink_escape_hash_drift_redaction_and_numeric_claim_fail(tmp_path):
    from course_mode_physical_tft_ledger_validate import validate_ledger

    root = tmp_path / "symlink"
    document = complete_ledger(root)
    artifact = document["artifacts"][0]
    path = root / artifact["path"]
    outside = tmp_path / "outside.bin"
    outside.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(outside)
    assert "artifacts.path" in validate_ledger(document, repository_root=root)["reasons"]

    root = tmp_path / "hash"
    document = complete_ledger(root)
    document["artifacts"][0]["sha256"] = "b" * 64
    assert "artifacts.hash" in validate_ledger(document, repository_root=root)["reasons"]

    root = tmp_path / "redaction"
    document = complete_ledger(root)
    document["artifacts"][0]["redactionStatus"] = "PENDING"
    assert "artifacts.redaction" in validate_ledger(document, repository_root=root)["reasons"]

    root = tmp_path / "limits"
    document = complete_ledger(root)
    document["numericLimits"][0]["status"] = "PASS"
    assert "numericLimits.status" in validate_ledger(document, repository_root=root)["reasons"]
