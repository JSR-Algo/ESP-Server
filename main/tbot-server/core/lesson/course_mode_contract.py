"""Exact immutable parser for the frozen courseCompanion.v2 Task 00 contract."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, NoReturn, cast


_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_ROOT_FIELDS = {
    "schemaVersion", "contractVersion", "contractChecksum", "checksumRules", "fixtureId",
    "preset", "lesson", "targets", "evidenceNames", "embodiedIntentNames", "visualFocus",
    "activities", "renderer",
}
_TARGET_FIELDS = {"targetId", "targetWord", "role", "vietnameseMeanings", "activityIds"}
_ACTIVITY_FIELDS = {
    "activityId", "targetId", "stage", "activityType", "evidenceName", "contextId",
    "embodiedIntent", "visualFocusRegion", "answerPolicy", "listeningTransition",
    "reducedMotionFallback",
}
_ANSWER_FIELDS = {
    "targetTextVisible", "targetAudioBeforeAssessment", "spokenTargetInPrompt",
    "multipleChoiceContainsTarget", "minElapsedSinceFullModelMs", "minInterveningActivityCount",
}
_ASSESSMENT_STAGES = {"RECALL", "TRANSFER", "DELAYED_RECALL"}


class CourseModeContractError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


def _fail(code: str, message: str) -> NoReturn:
    raise CourseModeContractError(code, message)


def _exact(value: Any, fields: set[str], label: str, code: str = "INVALID_FIELDS") -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        _fail(code, f"{label} fields must match exactly")
    return cast(Mapping[str, Any], value)


def _safe_id(value: Any, label: str) -> str:
    if not isinstance(value, str) or _ID_RE.fullmatch(value) is None:
        _fail("UNSAFE_ID", f"{label} is unsafe")
    return value


def _canonical_checksum(value: Mapping[str, Any]) -> str:
    def normalize(item: Any) -> Any:
        if isinstance(item, str):
            return unicodedata.normalize("NFC", item)
        if isinstance(item, list):
            return [normalize(child) for child in item]
        if isinstance(item, Mapping):
            return {key: normalize(item[key]) for key in sorted(item)}
        return item

    payload = {key: child for key, child in value.items() if key != "contractChecksum"}
    encoded = json.dumps(normalize(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class CourseActivity:
    activity_id: str
    target_id: str
    stage: str
    activity_type: str
    evidence_name: str
    context_id: str
    embodied_intent: str
    visual_focus_region: str
    answer_policy: Mapping[str, Any]
    listening_transition: tuple[str, ...]
    reduced_motion_fallback: str


@dataclass(frozen=True)
class CourseWordTarget:
    target_id: str
    target_word: str
    role: str
    vietnamese_meanings: tuple[str, ...]
    activity_ids: tuple[str, ...]


@dataclass(frozen=True)
class CourseModeContract:
    contract_version: str
    contract_checksum: str
    fixture_id: str
    lesson_session_id: str
    primary: CourseWordTarget
    secondary: CourseWordTarget | None
    activities: tuple[CourseActivity, ...]
    evidence_names: tuple[str, ...]
    embodied_intent_names: tuple[str, ...]

    @classmethod
    def from_mapping(cls, value: Any, *, verify_checksum: bool = True) -> "CourseModeContract":
        root = _exact(value, _ROOT_FIELDS, "contract")
        if root["schemaVersion"] != 1 or root["contractVersion"] != "courseCompanion.v2.contract.v1":
            _fail("UNSUPPORTED_CONTRACT", "unsupported semantic contract")
        preset = _exact(root["preset"], {"presetId", "presetVersion"}, "preset")
        if preset != {"presetId": "courseCompanion", "presetVersion": 2}:
            _fail("UNSUPPORTED_PRESET", "courseCompanion.v2 is required")
        lesson = _exact(root["lesson"], {"lessonId", "lessonVersion", "lessonSessionId"}, "lesson")
        lesson_session_id = _safe_id(lesson["lessonSessionId"], "lessonSessionId")
        raw_targets = root["targets"]
        if not isinstance(raw_targets, list) or not 1 <= len(raw_targets) <= 2:
            _fail("TARGET_COUNT", "one primary and at most one secondary target are required")
        targets: list[CourseWordTarget] = []
        for index, item in enumerate(raw_targets):
            target = _exact(item, _TARGET_FIELDS, "target")
            role = target["role"]
            expected_role = "primary" if index == 0 else "optional_secondary"
            if role != expected_role:
                _fail("INVALID_TARGET_ROLE", "target roles and ordering are fixed")
            meanings = target["vietnameseMeanings"]
            activity_ids = target["activityIds"]
            if not isinstance(meanings, list) or not meanings or not all(isinstance(x, str) and x.strip() for x in meanings):
                _fail("INVALID_TARGET", "Vietnamese meanings are required")
            if not isinstance(activity_ids, list) or not activity_ids:
                _fail("INVALID_TARGET", "activityIds are required")
            targets.append(CourseWordTarget(
                target_id=_safe_id(target["targetId"], "targetId"),
                target_word=cast(str, target["targetWord"]), role=cast(str, role),
                vietnamese_meanings=tuple(meanings), activity_ids=tuple(_safe_id(x, "activityId") for x in activity_ids),
            ))
        intents = root["embodiedIntentNames"]
        evidence_names = root["evidenceNames"]
        if not isinstance(intents, list) or not isinstance(evidence_names, list):
            _fail("INVALID_FIELDS", "name registries must be arrays")
        raw_activities = root["activities"]
        if not isinstance(raw_activities, list):
            _fail("INVALID_FIELDS", "activities must be an array")
        activities: list[CourseActivity] = []
        seen: set[str] = set()
        target_ids = {target.target_id for target in targets}
        for item in raw_activities:
            activity = _exact(item, _ACTIVITY_FIELDS, "activity", "INVALID_ACTIVITY_FIELDS")
            activity_id = _safe_id(activity["activityId"], "activityId")
            if activity_id in seen:
                _fail("DUPLICATE_ACTIVITY", "activity IDs must be unique")
            seen.add(activity_id)
            if activity["targetId"] not in target_ids:
                _fail("INVALID_ACTIVITY_TARGET", "activity target is not authored")
            if activity["embodiedIntent"] not in intents:
                _fail("UNSUPPORTED_INTENT", "embodied intent is not frozen")
            answer = _exact(activity["answerPolicy"], _ANSWER_FIELDS, "answerPolicy", "INVALID_ACTIVITY_FIELDS")
            stage = cast(str, activity["stage"])
            if stage in _ASSESSMENT_STAGES and (
                any(answer[key] is not False for key in (
                    "targetTextVisible", "targetAudioBeforeAssessment", "spokenTargetInPrompt",
                    "multipleChoiceContainsTarget",
                ))
                or type(answer["minElapsedSinceFullModelMs"]) is not int
                or answer["minElapsedSinceFullModelMs"] < 20_000
                or type(answer["minInterveningActivityCount"]) is not int
                or answer["minInterveningActivityCount"] < 1
            ):
                _fail("UNSAFE_ASSESSMENT", "assessment leaks the answer")
            activities.append(CourseActivity(
                activity_id=activity_id, target_id=cast(str, activity["targetId"]), stage=stage,
                activity_type=cast(str, activity["activityType"]), evidence_name=cast(str, activity["evidenceName"]),
                context_id=_safe_id(activity["contextId"], "contextId"),
                embodied_intent=cast(str, activity["embodiedIntent"]),
                visual_focus_region=cast(str, activity["visualFocusRegion"]),
                answer_policy=MappingProxyType(dict(answer)),
                listening_transition=tuple(cast(list[str], activity["listeningTransition"])),
                reduced_motion_fallback=cast(str, activity["reducedMotionFallback"]),
            ))
        primary_stages = {item.stage for item in activities if item.target_id == targets[0].target_id}
        if not {"UNDERSTAND", "RECALL", "TRANSFER", "DELAYED_RECALL"} <= primary_stages:
            _fail("MISSING_REQUIRED_ACTIVITY", "primary target lacks required assessments")
        for target in targets:
            actual_ids = tuple(item.activity_id for item in activities if item.target_id == target.target_id)
            if actual_ids != target.activity_ids:
                _fail("ACTIVITY_IDENTITY_MISMATCH", "target activity IDs must match authored order")
        if verify_checksum and root["contractChecksum"] != _canonical_checksum(root):
            _fail("CHECKSUM_MISMATCH", "canonical checksum does not match")
        return cls(
            contract_version=cast(str, root["contractVersion"]),
            contract_checksum=cast(str, root["contractChecksum"]), fixture_id=cast(str, root["fixtureId"]),
            lesson_session_id=lesson_session_id, primary=targets[0],
            secondary=targets[1] if len(targets) == 2 else None, activities=tuple(activities),
            evidence_names=tuple(cast(list[str], evidence_names)), embodied_intent_names=tuple(cast(list[str], intents)),
        )

    def activity(self, activity_id: str) -> CourseActivity:
        for activity in self.activities:
            if activity.activity_id == activity_id:
                return activity
        raise KeyError(activity_id)
