#!/usr/bin/env python3
"""Validate an attended Course Mode physical-TFT evidence ledger offline."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import re
from pathlib import Path

from course_mode_physical_tft_preflight import validate_local_lab_endpoints
from course_mode_physical_tft_receipt_verify import validate_receipt, validate_receipt_pair


EXPECTED_PRODUCTION_CANDIDATE_TARGET = {
    "firmwareSha": "3d4a1e2a32359278124c61e56fd459fac618506e",
    "applicationSha256": "84c999ece0c90eb6e69a410e335c7791f330e9c0fd39c30dfd4162bb7c4cfc6e",
    "bundleRootSha256": "9ef3729d0faec7b02d867cedb3ab30d110b845b1c0133738c588bba0e0c16be6",
    "preservedNvsSha256": "a7a87f72416be20388298cb70cfff306ec78e77f0e8b09231d16113f3d82404e",
}
ACTIVE_LAB_FIRMWARE_SHA = "aef1034f859b35efc93215106eb3be89f10f6c66"
EXPECTED_PROTECTED = {
    "path": "/Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/tbot-server/tests/test_lesson_voice_output_discipline.py",
    "sha256": "08f77b5452301224b17b4b333d2d032fff40c06aa2eaea97fa90932dae7d97e3",
}
EXPECTED_IDS = {
    "courseId": "70000000-0000-4000-8000-000000000003",
    "lessonId": "70000000-0000-4000-8000-000000000004",
    "deviceId": "70000000-0000-4000-8000-000000000005",
    "assignmentId": "70000000-0000-4000-8000-000000000006",
    "adultOperatorId": "70000000-0000-4000-8000-000000000007",
}
EXPECTED_IDENTITY = {
    "deviceSuffix": "AC:20",
    "lessonKey": "course-mode-pilot-cat-ball",
    "lessonVersion": 1,
    "rendererId": "teebot-lesson-renderer.v4",
    "contractChecksum": "cf12b1a5f71f0a80a8ee22bb2cdc775ada5b803e26d154e5d29c76b14c9fb264",
    "layoutChecksum": "e61b56d1f8219a86c7f3986e7d5c70b91f512286604b5b206ef11e2c989d275c",
}
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
VISUAL_CHECKS = {
    "background", "teachingObject", "robotOverlay", "caption",
    "listeningIndicator", "crop", "overlap", "zOrder", "focusAnchor",
    "flicker", "corruption", "reducedMotion",
}
REST_FIELDS = {
    "stableScreen", "headCentered", "armsLowered", "noContinuedMovement",
    "noChatter", "noBinding", "noVibration", "noOdor", "noUnusualHeat",
    "stablePower", "noPrivateData", "noPrivacyUplink",
}
BLOCKERS = {
    "attended-capture", "direct-visual-evidence", "calibrated-instruments",
    "approved-limits", "estop-and-tp-en", "rollback", "recovery",
    "remaining-task07-physical-lanes",
}
LOWER_SHA256 = re.compile(r"^[0-9a-f]{64}$")
FORBIDDEN_CONTENT = re.compile(
    r"(?i)(\"(?:authorization|jwt|token|secret|password)\"\s*:|authorization\s*[:=]|bearer\s+|"
    r"private.?key|transcript|utterance|raw.?speech|audio.?data|pronunciation.?score|"
    r"child.?name|birth.?date|(?:[0-9a-f]{2}:){5}[0-9a-f]{2})"
)
FORBIDDEN_ARTIFACT = re.compile(
    rb"(?i)(authorization\s*[:=]|bearer\s+|jwt\s*[:=]|token\s*[:=]|secret\s*[:=]|"
    rb"password\s*[:=]|private.?key|transcript|utterance|raw.?speech|audio.?data|"
    rb"pronunciation.?score|(?:[0-9a-f]{2}:){5}[0-9a-f]{2})"
)
EXPECTED_FIELDS = {
    "schemaVersion", "gate", "declaredTftVerdict", "task07Verdict",
    "productionCandidateTarget", "activeLabApp", "backend", "protectedTest",
    "identity", "endpoints", "bindings",
    "operators", "artifacts", "runtimeMarkers", "cues", "finalRest",
    "stopConditions", "stopPhase", "unavailableEvidence", "stopOutcome",
    "privacyOutcome", "physicalActions",
    "numericLimits", "outstandingBlockers",
}
EVIDENCE_PHASES = {
    "PRE_PREFLIGHT": (),
    "POST_PREFLIGHT": ("preflight",),
    "POST_STACK_START": ("preflight",),
    "POST_FIRST_RECEIPT": ("preflight", "firstReceipt"),
    "POST_RERUN_RECEIPT": ("preflight", "firstReceipt", "rerunReceipt"),
    "DURING_ATTENDED_CAPTURE": ("preflight", "firstReceipt", "rerunReceipt"),
}
ALL_BOUND_EVIDENCE = {"preflight", "firstReceipt", "rerunReceipt"}


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _base_reasons(document: dict[str, object]) -> list[str]:
    reasons: list[str] = []
    for field in sorted(set(document) - EXPECTED_FIELDS):
        reasons.append(f"schema.extra_field.{field}")
    for field in sorted(EXPECTED_FIELDS - set(document)):
        reasons.append(f"schema.missing_field.{field}")
    if document.get("schemaVersion") != 1 or document.get("gate") != "course-mode-v2-task07-physical-tft":
        reasons.append("schema.identity")
    if document.get("task07Verdict") != "PHYSICAL_BLOCKED":
        reasons.append("task07Verdict")
    if document.get("productionCandidateTarget") != EXPECTED_PRODUCTION_CANDIDATE_TARGET:
        reasons.append("productionCandidateTarget")
    active_lab_app = document.get("activeLabApp")
    if not isinstance(active_lab_app, dict) or set(active_lab_app) != {
        "firmwareSha", "applicationSha256", "bundleRootSha256"
    }:
        reasons.append("activeLabApp")
    else:
        if active_lab_app.get("firmwareSha") != ACTIVE_LAB_FIRMWARE_SHA:
            reasons.append("activeLabApp.firmwareSha")
        allow_unqualified = (
            document.get("declaredTftVerdict") == "TFT_BLOCKED"
            or document.get("stopPhase") == "PRE_PREFLIGHT"
        )
        hashes = [active_lab_app.get(field) for field in ("applicationSha256", "bundleRootSha256")]
        if allow_unqualified and hashes == [None, None]:
            pass
        else:
            for field, value in zip(("applicationSha256", "bundleRootSha256"), hashes):
                if not isinstance(value, str) or not LOWER_SHA256.fullmatch(value):
                    reasons.append(f"activeLabApp.{field}")
    backend = document.get("backend")
    if not isinstance(backend, dict) or set(backend) != {"sha", "image", "imageId"}:
        reasons.append("backend")
    elif document.get("declaredTftVerdict") == "TFT_BLOCKED" or document.get("stopPhase") == "PRE_PREFLIGHT":
        if any(value is not None for value in backend.values()):
            reasons.append("backend.unavailable")
    elif (
        not isinstance(backend.get("sha"), str)
        or not re.fullmatch(r"[0-9a-f]{40}", backend["sha"])
        or backend.get("image") != f"local/tbot-backend:course-mode-physical-tft-{backend.get('sha')}"
        or not isinstance(backend.get("imageId"), str)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", backend["imageId"])
    ):
        reasons.append("backend")
    if document.get("protectedTest") != EXPECTED_PROTECTED:
        reasons.append("protectedTest")
    identity = document.get("identity")
    if not isinstance(identity, dict):
        reasons.append("identity")
    else:
        for key, value in EXPECTED_IDENTITY.items():
            if identity.get(key) != value:
                reasons.append("identity")
        manifest = identity.get("manifestChecksum")
        if not isinstance(manifest, str) or not LOWER_SHA256.fullmatch(manifest):
            reasons.append("identity")
        if identity.get("syntheticIds") != EXPECTED_IDS:
            reasons.append("identity")
    endpoints = document.get("endpoints")
    if not isinstance(endpoints, dict) or (
        endpoints.get("backendBaseUrl") != "http://127.0.0.1:3000"
        or endpoints.get("espHttpUrl") != "http://host.docker.internal:8003"
        or endpoints.get("authority") != "approved-local-task07-lab-route"
    ):
        reasons.append("endpoints")
    if isinstance(endpoints, dict):
        reasons.extend(
            f"endpoints.{reason}"
            for reason in validate_local_lab_endpoints(
                endpoints.get("assetOrigin"),
                endpoints.get("otaUrl"),
                endpoints.get("websocketUrl"),
            )
        )
    encoded = json.dumps(document, sort_keys=True, separators=(",", ":"))
    if FORBIDDEN_CONTENT.search(encoded):
        reasons.append("privacy.forbidden_content")
    limits = document.get("numericLimits")
    allowed = {"NOT_MEASURED", "MEASURED_PENDING_APPROVED_LIMIT", "EVALUATED_AGAINST_APPROVED_LIMIT"}
    if not isinstance(limits, list) or not limits:
        reasons.append("numericLimits")
    else:
        for row in limits:
            if not isinstance(row, dict) or row.get("status") not in allowed:
                reasons.append("numericLimits.status")
            elif not isinstance(row.get("authorityReference"), str) or not row["authorityReference"]:
                reasons.append("numericLimits.authority")
    return reasons


def _validate_artifacts(
    document: dict[str, object], repository_root: Path
) -> tuple[list[str], dict[str, dict[str, object]], dict[str, bytes]]:
    reasons: list[str] = []
    by_id: dict[str, dict[str, object]] = {}
    contents: dict[str, bytes] = {}
    artifacts = document.get("artifacts")
    if not isinstance(artifacts, list):
        return ["artifacts"], by_id, contents
    workspace_root = (
        repository_root.parents[1]
        if repository_root.name == "esp32-server" and repository_root.parent.name == "robot"
        else repository_root
    )
    artifact_root = (workspace_root / "task-artifacts/course-mode-task07").resolve()
    for artifact in artifacts:
        if not isinstance(artifact, dict) or not isinstance(artifact.get("id"), str):
            reasons.append("artifacts.schema")
            continue
        by_id[artifact["id"]] = artifact
        raw_path = artifact.get("path")
        if not isinstance(raw_path, str) or Path(raw_path).is_absolute():
            reasons.append("artifacts.path")
            continue
        relative = Path(raw_path)
        path = workspace_root / relative if relative.parts[:2] == ("task-artifacts", "course-mode-task07") else artifact_root / relative
        try:
            resolved = path.resolve(strict=True)
        except OSError:
            reasons.append("artifacts.missing")
            continue
        if not _within(resolved, artifact_root):
            reasons.append("artifacts.path")
            continue
        try:
            content = resolved.read_bytes()
        except OSError:
            reasons.append("artifacts.missing")
            continue
        contents[artifact["id"]] = content
        if artifact.get("bytes") != len(content):
            reasons.append("artifacts.size")
        if artifact.get("sha256") != hashlib.sha256(content).hexdigest():
            reasons.append("artifacts.hash")
        if FORBIDDEN_ARTIFACT.search(content):
            reasons.append("artifacts.privacy")
        if artifact.get("redactionStatus") != "REDACTED":
            reasons.append("artifacts.redaction")
    return reasons, by_id, contents


def _validate_bindings(
    document: dict[str, object],
    artifacts: dict[str, dict[str, object]],
    required: tuple[str, ...],
) -> list[str]:
    bindings = document.get("bindings")
    if not isinstance(bindings, dict):
        return ["bindings"]
    reasons: list[str] = []
    expected_keys: set[str] = set()
    for prefix in required:
        expected_keys.update({f"{prefix}ArtifactId", f"{prefix}Sha256"})
        artifact = artifacts.get(bindings.get(f"{prefix}ArtifactId"))
        if artifact is None or bindings.get(f"{prefix}Sha256") != artifact.get("sha256"):
            reasons.append(f"bindings.{prefix}")
    if set(bindings) != expected_keys:
        reasons.append("bindings.phase")
    return reasons


def _parse_bound_json(
    prefix: str,
    bindings: dict[str, object],
    contents: dict[str, bytes],
) -> tuple[object | None, str | None]:
    artifact_id = bindings.get(f"{prefix}ArtifactId")
    content = contents.get(artifact_id) if isinstance(artifact_id, str) else None
    if content is None:
        return None, f"bindings.{prefix}.semantic"
    try:
        return json.loads(content.decode("utf-8")), None
    except (UnicodeError, json.JSONDecodeError):
        return None, f"bindings.{prefix}.semantic"


def _validate_bound_documents(
    document: dict[str, object],
    contents: dict[str, bytes],
    available: tuple[str, ...],
) -> list[str]:
    bindings = document.get("bindings")
    if not isinstance(bindings, dict):
        return []
    reasons: list[str] = []
    parsed: dict[str, object] = {}
    for prefix in available:
        parsed_document, error = _parse_bound_json(prefix, bindings, contents)
        if error:
            reasons.append(error)
        else:
            parsed[prefix] = parsed_document
    if "preflight" in parsed:
        preflight = parsed["preflight"]
        backend = document.get("backend")
        expected_fields = {
            "valid", "result", "backendSha", "backendImage", "imageId",
            "composeProject", "productionCandidateTarget", "activeLabApp",
            "deviceSuffix", "syntheticIds",
            "endpoints", "endpointAuthority", "sessionStartedAt", "secrets",
        }
        expected_preflight = (
            isinstance(preflight, dict)
            and set(preflight) == expected_fields
            and preflight.get("valid") is True
            and preflight.get("result") == "PASS"
            and isinstance(backend, dict)
            and preflight.get("backendSha") == backend.get("sha")
            and preflight.get("backendImage") == backend.get("image")
            and preflight.get("imageId") == backend.get("imageId")
            and preflight.get("composeProject") == "tbot-course-mode-physical-tft"
            and preflight.get("productionCandidateTarget") == document.get("productionCandidateTarget")
            and preflight.get("activeLabApp") == document.get("activeLabApp")
            and preflight.get("deviceSuffix") == document.get("identity", {}).get("deviceSuffix")
            and preflight.get("syntheticIds") == document.get("identity", {}).get("syntheticIds")
            and preflight.get("endpoints") == document.get("endpoints")
            and preflight.get("endpointAuthority") == document.get("endpoints", {}).get("authority")
            and preflight.get("sessionStartedAt") == document.get("operators", {}).get("startedAt")
            and preflight.get("secrets") == {
                "JWT_PUBLIC_KEY": "present-redacted",
                "TBOT_DEVICE_MINT_SECRET": "present-redacted",
            }
        )
        if not expected_preflight:
            reasons.append("bindings.preflight.semantic")
    if "firstReceipt" in parsed and validate_receipt(parsed["firstReceipt"]):
        reasons.append("bindings.firstReceipt.semantic")
    if "rerunReceipt" in parsed and validate_receipt(parsed["rerunReceipt"]):
        reasons.append("bindings.rerunReceipt.semantic")
    if "firstReceipt" in parsed and "rerunReceipt" in parsed and validate_receipt_pair(
        parsed["firstReceipt"], parsed["rerunReceipt"]
    ):
        reasons.append("bindings.receipt_pair.semantic")
    return reasons


def _validate_unavailable_evidence(document: dict[str, object], available: tuple[str, ...]) -> list[str]:
    rows = document.get("unavailableEvidence")
    if not isinstance(rows, list):
        return ["unavailableEvidence"]
    reasons: list[str] = []
    unavailable: set[str] = set()
    for row in rows:
        if (
            not isinstance(row, dict)
            or set(row) != {"evidence", "reason"}
            or row.get("evidence") not in ALL_BOUND_EVIDENCE
            or not isinstance(row.get("reason"), str)
            or not row["reason"].strip()
        ):
            reasons.append("unavailableEvidence.schema")
            continue
        unavailable.add(row["evidence"])
    if unavailable != ALL_BOUND_EVIDENCE - set(available) or len(rows) != len(unavailable):
        reasons.append("unavailableEvidence.phase")
    return reasons


def _expected_markers() -> list[str]:
    markers = ["authenticated-ac20-websocket", "app-ready", "lesson_prepare", "lesson_start"]
    for cue, *_ in CUES:
        markers.extend([f"cue-transition:{cue}", f"cue-ack:{cue}"])
    return markers + ["lesson-complete", "lesson-stop", "quiescent-rest"]


def _utc_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        return None
    return parsed


def _validate_physical_actions(document: dict[str, object], declared: str) -> list[str]:
    actions = document.get("physicalActions")
    if not isinstance(actions, dict) or set(actions) != {"stackStart", "captureTrigger", "cleanup"}:
        return ["physicalActions"]
    if declared == "TFT_PASS":
        expected = {
            "stackStart": "OPERATOR_AUTHORIZED_ATTENDED",
            "captureTrigger": "OPERATOR_AUTHORIZED_ATTENDED",
            "cleanup": "NOT_PERFORMED",
        }
        return [] if actions == expected else ["physicalActions.pass"]
    if declared == "TFT_FAIL":
        phase = document.get("stopPhase")
        stack_started = phase in {
            "POST_STACK_START", "POST_FIRST_RECEIPT", "POST_RERUN_RECEIPT",
            "DURING_ATTENDED_CAPTURE",
        }
        capture_started = phase == "DURING_ATTENDED_CAPTURE"
        expected = {
            "stackStart": "OPERATOR_AUTHORIZED_ATTENDED" if stack_started else "NOT_PERFORMED",
            "captureTrigger": "OPERATOR_AUTHORIZED_ATTENDED" if capture_started else "NOT_PERFORMED",
            "cleanup": "NOT_PERFORMED",
        }
        return [] if actions == expected else ["physicalActions.fail_phase"]
    return []


def _validate_attended(
    document: dict[str, object],
    artifacts: dict[str, dict[str, object]],
    *,
    complete: bool,
) -> list[str]:
    reasons: list[str] = []
    operators = document.get("operators")
    if not isinstance(operators, dict) or not all(
        operators.get(field) is True
        for field in ("adultOnly", "soleLease", "clearMotionEnvelope", "immediatePowerIsolation")
    ) or not all(isinstance(operators.get(field), str) and operators[field] for field in (
        "operatorName", "observerName", "startedAt", "endedAt"
    )):
        reasons.append("operators")
    if isinstance(operators, dict):
        started = _utc_timestamp(operators.get("startedAt"))
        ended = _utc_timestamp(operators.get("endedAt"))
        if started is None or ended is None:
            reasons.append("operators.timestamps")
        elif ended < started:
            reasons.append("operators.chronology")
    markers = document.get("runtimeMarkers")
    expected_markers = _expected_markers()
    if complete:
        if markers != expected_markers:
            reasons.append("runtimeMarkers.order")
    elif not isinstance(markers, list) or markers != expected_markers[: len(markers)]:
        reasons.append("runtimeMarkers.order")
    rows = document.get("cues")
    expected_cue_ids = [cue[0] for cue in CUES]
    actual_cue_ids = [row.get("cueId") for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
    if (
        not isinstance(rows, list)
        or (complete and actual_cue_ids != expected_cue_ids)
        or (not complete and actual_cue_ids != expected_cue_ids[: len(actual_cue_ids)])
    ):
        reasons.append("cues.order")
        return reasons
    for row, expected in zip(rows, CUES):
        cue, focus, visual_mode, assessment = expected
        if (
            row.get("activityId") != cue
            or row.get("expectedFocus") != focus
            or row.get("expectedVisualMode") != visual_mode
            or row.get("assessment") is not assessment
        ):
            reasons.append("cues.contract")
        operator = row.get("operatorVerdict")
        observer = row.get("observerVerdict")
        if operator not in {"PASS", "FAIL"} or observer not in {"PASS", "FAIL"}:
            reasons.append("cues.two_adult_verdict")
        elif operator != observer:
            reasons.append("cues.disagreement")
        checklist = row.get("visualChecklist")
        if not isinstance(checklist, dict) or set(checklist) != VISUAL_CHECKS or not all(value is True for value in checklist.values()):
            reasons.append("cues.visual_checklist")
        frames = row.get("frameRefs")
        if not isinstance(frames, list) or not frames or not all(
            frame in artifacts and artifacts[frame].get("directVisual") is True for frame in frames
        ):
            reasons.append("cues.direct_visual")
    if complete:
        rest = document.get("finalRest")
        if not isinstance(rest, dict) or set(rest) != REST_FIELDS or not all(value is True for value in rest.values()):
            reasons.append("finalRest")
    privacy = document.get("privacyOutcome")
    if not isinstance(privacy, dict) or set(privacy) != {"privateDataObserved", "unauthorizedUplinkObserved"} or not all(
        isinstance(value, bool) for value in privacy.values()
    ):
        reasons.append("privacyOutcome")
    elif complete and any(privacy.values()):
        reasons.append("privacyOutcome.pass")
    return reasons


def validate_ledger(document: object, *, repository_root: Path) -> dict[str, object]:
    if not isinstance(document, dict):
        return {"valid": False, "tftVerdict": "TFT_BLOCKED", "task07Verdict": "PHYSICAL_BLOCKED", "reasons": ["schema.not_object"]}
    reasons = _base_reasons(document)
    declared = document.get("declaredTftVerdict")
    if declared not in {"TFT_BLOCKED", "TFT_FAIL", "TFT_PASS"}:
        reasons.append("declaredTftVerdict")
        declared = "TFT_BLOCKED"
    if declared == "TFT_BLOCKED":
        if document.get("artifacts") != [] or document.get("cues") != [] or document.get("runtimeMarkers") != []:
            reasons.append("blocked.evidence_must_be_empty")
        if document.get("physicalActions") != {
            "stackStart": "NOT_PERFORMED", "captureTrigger": "NOT_PERFORMED", "cleanup": "NOT_PERFORMED"
        }:
            reasons.append("blocked.physicalActions")
        if document.get("stopOutcome") != {
            "safeState": "NOT_PERFORMED", "powerIsolation": "NOT_PERFORMED"
        }:
            reasons.append("blocked.stopOutcome")
        if document.get("privacyOutcome") != {
            "privateDataObserved": None, "unauthorizedUplinkObserved": None
        }:
            reasons.append("blocked.privacyOutcome")
        if document.get("stopPhase") != "NOT_PERFORMED":
            reasons.append("blocked.stopPhase")
        reasons.extend(_validate_unavailable_evidence(document, ()))
        blockers = document.get("outstandingBlockers")
        if not isinstance(blockers, list) or not BLOCKERS.issubset(set(blockers)):
            reasons.append("blocked.outstandingBlockers")
    else:
        artifact_reasons, artifacts, contents = _validate_artifacts(document, repository_root)
        reasons.extend(artifact_reasons)
        if declared == "TFT_PASS":
            available = ("preflight", "firstReceipt", "rerunReceipt")
            if document.get("stopPhase") != "NOT_REQUIRED":
                reasons.append("stopPhase")
        else:
            stop_phase = document.get("stopPhase")
            available = EVIDENCE_PHASES.get(stop_phase, ())
            if stop_phase not in EVIDENCE_PHASES:
                reasons.append("stopPhase")
        reasons.extend(_validate_bindings(document, artifacts, available))
        reasons.extend(_validate_bound_documents(document, contents, available))
        reasons.extend(_validate_unavailable_evidence(document, available))
        reasons.extend(_validate_attended(document, artifacts, complete=declared == "TFT_PASS"))
        reasons.extend(_validate_physical_actions(document, declared))
        stops = document.get("stopConditions")
        if not isinstance(stops, list):
            reasons.append("stopConditions")
        elif stops and declared != "TFT_FAIL":
            reasons.append("stopConditions.verdict")
        elif not stops and declared == "TFT_FAIL":
            reasons.append("stopConditions.required")
        outcome = document.get("stopOutcome")
        if declared == "TFT_FAIL":
            if not isinstance(outcome, dict) or set(outcome) != {"safeState", "powerIsolation"} or outcome.get("safeState") not in {"RESTORED", "NOT_RESTORED"} or outcome.get("powerIsolation") not in {"ISOLATED", "NOT_ISOLATED"}:
                reasons.append("stopOutcome")
        elif outcome != {"safeState": "NOT_REQUIRED", "powerIsolation": "NOT_REQUIRED"}:
            reasons.append("stopOutcome")
    return {
        "valid": not reasons,
        "tftVerdict": declared,
        "task07Verdict": "PHYSICAL_BLOCKED",
        "reasons": sorted(set(reasons)),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("ledger", type=Path)
    parser.add_argument("--repository-root", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        document = json.loads(args.ledger.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        result = {"valid": False, "tftVerdict": "TFT_BLOCKED", "task07Verdict": "PHYSICAL_BLOCKED", "reasons": ["input.invalid_json"]}
    except (OSError, UnicodeError):
        result = {"valid": False, "tftVerdict": "TFT_BLOCKED", "task07Verdict": "PHYSICAL_BLOCKED", "reasons": ["input.unreadable"]}
    else:
        result = validate_ledger(document, repository_root=args.repository_root.resolve())
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
