from __future__ import annotations

import copy
import hashlib
import json
import os
import unicodedata
from pathlib import Path
from typing import Any

import pytest


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "course-mode"
CONTRACT_PATH = FIXTURE_DIR / "course-mode-pilot-cat-ball.json"
LAYOUT_PATH = FIXTURE_DIR / "renderer-v4-visual-layout.json"
V1_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "tvideo_farm_manifest_v2.json"
V1_PROVENANCE_PATH = Path(__file__).parent / "fixtures" / "tvideo_farm_manifest_v2.provenance.json"
V1_FIXTURE_SHA256 = "77f196f20c488aa215fc0051dcdbe490a154f651d8edb060c1b098fba7dc846a"
V1_CANONICAL_SHA256 = "44f1dd88f44acd903c7196b7ad1245e5d2177c18f5dd7de49e137a045bf4d50f"
V1_MANIFEST_CHECKSUM = "bb7d4dcdf6318096c0b9224dc48bcdcb3ff78b325706cdc9c5d39bd4e7da94e4"
CONTRACT_CHECKSUM = "cf12b1a5f71f0a80a8ee22bb2cdc775ada5b803e26d154e5d29c76b14c9fb264"
LAYOUT_CHECKSUM = "e61b56d1f8219a86c7f3986e7d5c70b91f512286604b5b206ef11e2c989d275c"
CONTRACT_FILE_SHA256 = "05e18ae61aee0660c653a9386854552a23f90c8a1f8cfb9e7ff4e15d1d277470"
LAYOUT_FILE_SHA256 = "031e69b82c33da87f5ec63c21cb1e756549b802e6a8bd567a1b76f51e4f77dc5"
CHECKSUM_RULES = {
    "algorithm": "SHA-256",
    "canonicalization": "tbot-json-c14n.v1",
    "encoding": "UTF-8",
    "unicodeNormalization": "NFC",
    "objectKeyOrder": "lexicographic",
    "arrayOrder": "preserved",
    "whitespace": "none",
    "excludedJsonPointers": ["/contractChecksum"],
}

EVIDENCE_NAMES = [
    "NOT_STARTED", "EXPOSED", "UNDERSTOOD", "SUPPORTED_SPEECH",
    "INDEPENDENT_RECALL", "TRANSFERRED", "MASTERED_TODAY", "REVIEW_NEEDED",
]
INTENT_NAMES = [
    "REST_WARM", "GREET_SMALL", "INVITE_CHILD", "PRESENT_CENTER", "PRESENT_LEFT",
    "PRESENT_RIGHT", "LISTEN_STILL", "THINK_CURIOUS", "ACKNOWLEDGE_STORY",
    "MODEL_WORD", "ENCOURAGE_SMALL", "TRY_DIFFERENT_WAY", "CELEBRATE_RECALL",
    "CELEBRATE_MASTERY", "COMFORT_CALM", "PAUSE_CHOICE", "GOODBYE_SMALL",
]
LISTEN_SEQUENCE = [
    "speech_complete", "gesture_settled", "head_centered", "arms_lowered",
    "motor_stopped", "assessment_window_open",
]
ACTIVITY_IDENTITIES = [
    ("cat-discover-center-01", "animals.cat", "DISCOVER", "single_visual_discovery", "EXPOSED", "cat_primary_visual", "PRESENT_CENTER", "focus.center.primary"),
    ("cat-meaning-left-right-01", "animals.cat", "UNDERSTAND", "authored_two_choice_visual", "UNDERSTOOD", "cat_dog_visual_contrast", "PRESENT_LEFT", "focus.left.choice"),
    ("cat-recall-visual-02", "animals.cat", "RECALL", "independent_visual_naming", "INDEPENDENT_RECALL", "cat_primary_visual_recall", "PRESENT_CENTER", "focus.center.primary"),
    ("cat-transfer-scene-01", "animals.cat", "TRANSFER", "second_context_scene_naming", "TRANSFERRED", "cat_second_visual_scene", "PRESENT_RIGHT", "focus.right.choice"),
    ("cat-delayed-recall-01", "animals.cat", "DELAYED_RECALL", "delayed_independent_naming", "MASTERED_TODAY", "cat_delayed_callback", "PRESENT_CENTER", "focus.center.primary"),
    ("ball-discover-center-01", "toys.ball", "DISCOVER", "single_visual_discovery", "EXPOSED", "ball_primary_visual", "PRESENT_CENTER", "focus.center.primary"),
]


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _normalized(value: Any) -> Any:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, list):
        return [_normalized(item) for item in value]
    if isinstance(value, dict):
        return {key: _normalized(value[key]) for key in sorted(value)}
    return value


def _checksum(document: dict[str, Any]) -> str:
    payload = {key: value for key, value in document.items() if key != "contractChecksum"}
    canonical = json.dumps(
        _normalized(payload), ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _keys(value: dict[str, Any], expected: set[str]) -> None:
    assert set(value) == expected


def _reject_raw_servo_fields(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            lowered = key.lower()
            assert "servo" not in lowered
            assert not lowered.endswith(("percent", "degrees", "pwm"))
            _reject_raw_servo_fields(child)
    elif isinstance(value, list):
        for child in value:
            _reject_raw_servo_fields(child)


def validate_contract(document: dict[str, Any], expected_checksum: str = CONTRACT_CHECKSUM) -> None:
    _keys(document, {
        "schemaVersion", "contractVersion", "contractChecksum", "fixtureId", "preset",
        "lesson", "targets", "evidenceNames", "embodiedIntentNames", "visualFocus",
        "activities", "renderer", "checksumRules",
    })
    assert document["schemaVersion"] == 1
    assert document["contractVersion"] == "courseCompanion.v2.contract.v1"
    assert document["checksumRules"] == CHECKSUM_RULES
    assert document["contractChecksum"] == expected_checksum == _checksum(document)
    assert document["fixtureId"] == "course-mode-pilot-cat-ball"
    assert document["preset"] == {"presetId": "courseCompanion", "presetVersion": 2}
    assert document["lesson"] == {
        "lessonId": "course-mode-pilot-cat-ball",
        "lessonVersion": 1,
        "lessonSessionId": "00000000-0000-4000-8000-000000000201",
    }
    assert document["evidenceNames"] == EVIDENCE_NAMES
    assert document["embodiedIntentNames"] == INTENT_NAMES
    assert document["renderer"] == {
        "rendererId": "teebot-lesson-renderer.v4",
        "visualLayoutContract": "renderer-v4.course-mode-layout.v1",
    }
    assert document["visualFocus"] == {
        "directionSource": "authored_visual_focus_region",
        "regions": ["focus.center.primary", "focus.left.choice", "focus.right.choice"],
        "presentCenterTarget": "single_teaching_object",
    }
    assert [
        (target["role"], target["targetId"], target["targetWord"], target["vietnameseMeanings"])
        for target in document["targets"]
    ] == [
        ("primary", "animals.cat", "cat", ["con mèo"]),
        ("optional_secondary", "toys.ball", "ball", ["quả bóng"]),
    ]
    for target in document["targets"]:
        _keys(target, {"targetId", "targetWord", "role", "vietnameseMeanings", "activityIds"})
    assert document["targets"][0]["activityIds"] == [identity[0] for identity in ACTIVITY_IDENTITIES[:5]]
    assert document["targets"][1]["activityIds"] == [ACTIVITY_IDENTITIES[5][0]]
    assert [
        (
            activity["activityId"], activity["targetId"], activity["stage"],
            activity["activityType"], activity["evidenceName"], activity["contextId"],
            activity["embodiedIntent"], activity["visualFocusRegion"],
        )
        for activity in document["activities"]
    ] == ACTIVITY_IDENTITIES
    for activity in document["activities"]:
        _keys(activity, {
            "activityId", "targetId", "stage", "activityType", "evidenceName", "contextId",
            "embodiedIntent", "visualFocusRegion", "answerPolicy", "listeningTransition",
            "reducedMotionFallback",
        })
        assert activity["targetId"] in {"animals.cat", "toys.ball"}
        assert activity["evidenceName"] in EVIDENCE_NAMES
        assert activity["embodiedIntent"] in INTENT_NAMES
        assert activity["visualFocusRegion"] in document["visualFocus"]["regions"]
        assert activity["listeningTransition"] == LISTEN_SEQUENCE
        _keys(activity["answerPolicy"], {
            "targetTextVisible", "targetAudioBeforeAssessment", "spokenTargetInPrompt",
            "multipleChoiceContainsTarget", "minElapsedSinceFullModelMs",
            "minInterveningActivityCount",
        })
        if activity["stage"] in {"RECALL", "TRANSFER", "DELAYED_RECALL"}:
            policy = activity["answerPolicy"]
            assert not policy["targetTextVisible"]
            assert not policy["targetAudioBeforeAssessment"]
            assert not policy["spokenTargetInPrompt"]
            assert not policy["multipleChoiceContainsTarget"]
            assert policy["minElapsedSinceFullModelMs"] >= 20_000
            assert policy["minInterveningActivityCount"] >= 1
    _reject_raw_servo_fields(document)


def _inside(rect: dict[str, int], canvas: dict[str, int]) -> bool:
    return (
        rect["x"] >= 0 and rect["y"] >= 0 and rect["width"] > 0 and rect["height"] > 0
        and rect["x"] + rect["width"] <= canvas["width"]
        and rect["y"] + rect["height"] <= canvas["height"]
    )


def _overlap(a: dict[str, int], b: dict[str, int]) -> int:
    width = max(0, min(a["x"] + a["width"], b["x"] + b["width"]) - max(a["x"], b["x"]))
    height = max(0, min(a["y"] + a["height"], b["y"] + b["height"]) - max(a["y"], b["y"]))
    return width * height


def validate_layout(document: dict[str, Any], expected_checksum: str = LAYOUT_CHECKSUM) -> None:
    _keys(document, {
        "schemaVersion", "contractVersion", "rendererId", "canvas", "layerOrder", "layers",
        "collisionLimits", "captionSafeArea", "focusAnchors", "listeningCue",
        "reducedMotion", "mirroring", "checksumRules", "contractChecksum",
    })
    assert document["schemaVersion"] == 1
    assert document["contractVersion"] == "renderer-v4.course-mode-layout.v1"
    assert document["rendererId"] == "teebot-lesson-renderer.v4"
    assert document["checksumRules"] == CHECKSUM_RULES
    assert document["contractChecksum"] == expected_checksum == _checksum(document)
    assert document["canvas"] == {"width": 480, "height": 320}
    assert document["layerOrder"] == [
        "background", "teachingObject", "robotOverlay", "transientFocusCue",
    ]
    layers = document["layers"]
    assert set(layers) == set(document["layerOrder"])
    for layer in layers.values():
        _keys(layer, {"zIndex", "bounds"})
        _keys(layer["bounds"], {"x", "y", "width", "height"})
        assert _inside(layer["bounds"], document["canvas"])
    assert [layers[name]["zIndex"] for name in document["layerOrder"]] == [0, 1, 2, 3]
    assert layers["teachingObject"]["bounds"] == {"x": 20, "y": 168, "width": 95, "height": 95}
    assert layers["robotOverlay"]["bounds"] == {"x": 118, "y": 160, "width": 150, "height": 150}
    assert document["collisionLimits"] == {
        "robotTeachingObjectMaxOverlapPixels": 0,
        "minimumHorizontalGapPixels": 3,
    }
    _keys(document["captionSafeArea"], {"x", "y", "width", "height"})
    _keys(document["listeningCue"], {"bounds", "minimumTextHeightPixels", "textKey"})
    _keys(document["listeningCue"]["bounds"], {"x", "y", "width", "height"})
    for anchor in document["focusAnchors"].values():
        _keys(anchor, {"x", "y"})
    assert _overlap(layers["teachingObject"]["bounds"], layers["robotOverlay"]["bounds"]) == 0
    teaching = layers["teachingObject"]["bounds"]
    robot = layers["robotOverlay"]["bounds"]
    assert robot["x"] - (teaching["x"] + teaching["width"]) >= document["collisionLimits"]["minimumHorizontalGapPixels"]
    assert _inside(document["captionSafeArea"], document["canvas"])
    assert _inside(document["listeningCue"]["bounds"], document["canvas"])
    assert _overlap(document["listeningCue"]["bounds"], layers["teachingObject"]["bounds"]) == 0
    assert _overlap(document["listeningCue"]["bounds"], layers["robotOverlay"]["bounds"]) == 0
    assert document["listeningCue"]["minimumTextHeightPixels"] >= 24
    assert document["focusAnchors"] == {
        "focus.center.primary": {"x": 67, "y": 215},
        "focus.left.choice": {"x": 67, "y": 215},
        "focus.right.choice": {"x": 366, "y": 215},
    }
    assert document["reducedMotion"] == {
        "fallback": "face_and_transient_focus_cue",
        "preservesLearningMeaning": True,
        "requiresServoMotion": False,
    }
    assert document["mirroring"] == {
        "mode": "authored_focus_regions_only",
        "automaticWholeCompositionMirror": False,
        "inferDirectionFromModelText": False,
    }


def _find_sibling(relative: str) -> Path | None:
    override = os.environ.get("TBOT_WORKSPACE_ROOT")
    starts = [Path(override)] if override else list(Path(__file__).resolve().parents)
    for start in starts:
        candidate = start / relative
        if candidate.exists():
            return candidate
    return None


def test_canonical_course_mode_fixture_is_strict_and_checksum_pinned() -> None:
    assert hashlib.sha256(CONTRACT_PATH.read_bytes()).hexdigest() == CONTRACT_FILE_SHA256
    assert hashlib.sha256(LAYOUT_PATH.read_bytes()).hexdigest() == LAYOUT_FILE_SHA256
    validate_contract(_load(CONTRACT_PATH))


@pytest.mark.parametrize("mutation", [
    lambda value: value.update({"unknownKey": True}),
    lambda value: value["activities"][0].update({"servoValue": 60}),
    lambda value: value["activities"][0].update({"embodiedIntent": "DANCE_RANDOM"}),
    lambda value: value["activities"][2]["answerPolicy"].update({"targetTextVisible": True}),
])
def test_contract_mutations_fail_closed(mutation) -> None:
    value = _load(CONTRACT_PATH)
    mutation(value)
    value["contractChecksum"] = _checksum(value)
    with pytest.raises(AssertionError):
        validate_contract(value, value["contractChecksum"])


@pytest.mark.parametrize("mutation", [
    lambda value: value["activities"][0].update({"targetId": "toys.ball"}),
    lambda value: value["activities"][0].update({"stage": "BOGUS"}),
    lambda value: value["activities"][0].update({"activityType": "bogus"}),
    lambda value: value["activities"].append(copy.deepcopy(value["activities"][0])),
    lambda value: value["targets"][0].update({"activityIds": ["ball-discover-center-01"]}),
])
def test_exact_activity_identity_mutations_fail_closed(mutation) -> None:
    value = _load(CONTRACT_PATH)
    mutation(value)
    value["contractChecksum"] = _checksum(value)
    with pytest.raises(AssertionError):
        validate_contract(value, value["contractChecksum"])


def test_renderer_v4_static_composition_and_fail_closed_mutations() -> None:
    value = _load(LAYOUT_PATH)
    validate_layout(value)
    for mutation in (
        lambda item: item.update({"schemaVersion": 99}),
        lambda item: item["layers"]["teachingObject"]["bounds"].update({"x": 450}),
        lambda item: item.update({"layerOrder": ["background", "robotOverlay", "teachingObject", "transientFocusCue"]}),
        lambda item: item["layers"]["robotOverlay"]["bounds"].update({"x": 100}),
    ):
        drifted = copy.deepcopy(value)
        mutation(drifted)
        drifted["contractChecksum"] = _checksum(drifted)
        with pytest.raises(AssertionError):
            validate_layout(drifted, drifted["contractChecksum"])


def test_all_repository_copies_are_byte_identical_when_available() -> None:
    for relative in (
        "robot/TBOT-Firmware/tests/fixtures/course-mode/course-mode-pilot-cat-ball.json",
        "tbot-backend/src/lessons/fixtures/course-mode/course-mode-pilot-cat-ball.json",
    ):
        sibling = _find_sibling(relative)
        if sibling is not None:
            assert sibling.read_bytes() == CONTRACT_PATH.read_bytes()
    for relative in (
        "robot/TBOT-Firmware/tests/fixtures/course-mode/renderer-v4-visual-layout.json",
        "tbot-backend/src/lessons/fixtures/course-mode/renderer-v4-visual-layout.json",
    ):
        sibling = _find_sibling(relative)
        if sibling is not None:
            assert sibling.read_bytes() == LAYOUT_PATH.read_bytes()


def test_tvideo_v1_conversation_fixture_and_manifest_checksum_are_unchanged() -> None:
    assert hashlib.sha256(V1_FIXTURE_PATH.read_bytes()).hexdigest() == V1_FIXTURE_SHA256
    fixture = _load(V1_FIXTURE_PATH)
    assert fixture["conversation"]["presetId"] == "tvideoJourney"
    assert fixture["conversation"]["presetVersion"] == 1
    canonical = json.dumps(fixture, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    assert hashlib.sha256(canonical).hexdigest() == V1_CANONICAL_SHA256
    assert _load(V1_PROVENANCE_PATH)["manifest"]["manifestChecksum"] == V1_MANIFEST_CHECKSUM
