"""Immutable parser for pilot and curriculum courseCompanion.v2 contracts."""

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
_ROOT = {
    "schemaVersion",
    "contractVersion",
    "contractChecksum",
    "checksumRules",
    "fixtureId",
    "preset",
    "lesson",
    "targets",
    "evidenceNames",
    "embodiedIntentNames",
    "visualFocus",
    "activities",
    "renderer",
}
_TARGET = {"targetId", "targetWord", "role", "vietnameseMeanings", "activityIds"}
_PILOT = {
    "activityId",
    "targetId",
    "stage",
    "activityType",
    "evidenceName",
    "contextId",
    "embodiedIntent",
    "visualFocusRegion",
    "answerPolicy",
    "listeningTransition",
    "reducedMotionFallback",
}
_CURRICULUM = {
    "activityId",
    "targetIds",
    "stage",
    "activityType",
    "evidenceName",
    "contextId",
    "embodiedIntent",
    "visualFocusRegion",
    "answerPolicy",
    "listeningTransition",
    "reducedMotionFallback",
    "modalities",
    "expectedDurationSec",
    "outcomes",
    "visual",
}
_ANSWER = {
    "targetTextVisible",
    "targetAudioBeforeAssessment",
    "spokenTargetInPrompt",
    "multipleChoiceContainsTarget",
    "minElapsedSinceFullModelMs",
    "minInterveningActivityCount",
}
_ASSESSMENT = {"RECALL", "TRANSFER", "DELAYED_RECALL"}
_ROLES = {"primary", "optional_secondary", "exposure", "review"}
_ACTIONS = {"advance", "retry", "support", "pause", "close", "complete"}
_OUTCOME_NAMES = {"success", "correct", "near", "incorrect", "silence", "vietnamese", "help", "uncertain", "fatigue", "refusal", "story", "continue", "finished", "regulationBreak"}
_MODALITIES = {"speech_en", "speech_vi", "choice", "gesture", "silence", "help"}
_STAGES = {"OPENING", "DISCOVER", "UNDERSTAND", "GUIDED_ACTION", "SUPPORTED_SPEECH", "RECALL", "TRANSFER", "DELAYED_RECALL", "CLOSING"}
_VISUAL = {"strategy", "backgroundAssetKey", "objectAssetKey", "fallback"}
_VISUAL_STRATEGIES = {"embodiedFallback", "publishedTeachingObject"}
_VISUAL_FALLBACKS = {"gesture", "robotActing", "sceneOnly", "visualFree"}
_LISTENING_TRANSITION = ("speech_complete", "gesture_settled", "head_centered", "arms_lowered", "motor_stopped", "assessment_window_open")
FROZEN_CONTRACT_CHECKSUM = "cf12b1a5f71f0a80a8ee22bb2cdc775ada5b803e26d154e5d29c76b14c9fb264"
CANONICAL_V5_CONTRACT_CHECKSUM = "332fb68e340abb94c0178dd83b06ed0939d6e2d63c17d48bcb09dab8cc6bb3be"


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


def _id(value: Any, label: str) -> str:
    if not isinstance(value, str) or _ID_RE.fullmatch(value) is None:
        _fail("UNSAFE_ID", f"{label} is unsafe")
    return value


def _freeze(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(
        {
            k: _freeze(v)
            if isinstance(v, Mapping)
            else tuple(_freeze(x) if isinstance(x, Mapping) else x for x in v)
            if isinstance(v, list)
            else v
            for k, v in value.items()
        }
    )


def _checksum(value: Mapping[str, Any], pilot: bool) -> str:
    def norm(v: Any) -> Any:
        if isinstance(v, str):
            return unicodedata.normalize("NFC", v)
        if isinstance(v, list):
            return [norm(x) for x in v]
        if isinstance(v, Mapping):
            return {k: norm(v[k]) for k in sorted(v)}
        return v

    payload = {k: v for k, v in value.items() if k != "contractChecksum"}
    if pilot and isinstance(payload.get("activities"), list):
        payload["activities"] = [
            {k: v for k, v in a.items() if k != "targetIds"} if isinstance(a, Mapping) else a
            for a in payload["activities"]
        ]
    return hashlib.sha256(
        json.dumps(norm(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


@dataclass(frozen=True)
class CourseActivity:
    activity_id: str
    target_ids: tuple[str, ...]
    stage: str
    activity_type: str
    evidence_name: str
    context_id: str
    embodied_intent: str
    visual_focus_region: str
    answer_policy: Mapping[str, Any]
    listening_transition: tuple[str, ...]
    reduced_motion_fallback: str
    modalities: tuple[str, ...] = ()
    expected_duration_sec: int = 0
    outcomes: Mapping[str, Mapping[str, Any]] = MappingProxyType({})
    visual: Mapping[str, Any] = MappingProxyType({})
    navigation_mode: str = "authoritative_graph"
    evidence_policy: str = "shared_outcome"

    @property
    def target_id(self) -> str:
        return self.target_ids[0]


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
    targets: tuple[CourseWordTarget, ...]
    activities: tuple[CourseActivity, ...]
    evidence_names: tuple[str, ...]
    embodied_intent_names: tuple[str, ...]
    soft_deadline_sec: int = 540
    max_attempts: int = 3
    listen_timeout_sec: int = 6
    is_curriculum: bool = False

    @property
    def primary(self) -> CourseWordTarget:
        return next(t for t in self.targets if t.role == "primary")

    @property
    def secondary(self) -> CourseWordTarget | None:
        return next((t for t in self.targets if t.role == "optional_secondary"), None)

    @classmethod
    def from_mapping(cls, value: Any, *, verify_checksum: bool = True) -> "CourseModeContract":
        if not isinstance(value, Mapping):
            _fail("INVALID_FIELDS", "contract must be an object")
        curriculum = "session" in value
        root = _exact(value, _ROOT | ({"session"} if curriculum else set()), "contract")
        if root["schemaVersion"] != 1 or root["contractVersion"] != "courseCompanion.v2.contract.v1":
            _fail("UNSUPPORTED_CONTRACT", "unsupported semantic contract")
        if _exact(root["preset"], {"presetId", "presetVersion"}, "preset") != {
            "presetId": "courseCompanion",
            "presetVersion": 2,
        }:
            _fail("UNSUPPORTED_PRESET", "courseCompanion.v2 is required")
        lesson = _exact(root["lesson"], {"lessonId", "lessonVersion", "lessonSessionId"}, "lesson")
        session_id = _id(lesson["lessonSessionId"], "lessonSessionId")
        session = {"softDeadlineSec": 540, "maxAttempts": 3, "listenTimeoutSec": 6}
        if curriculum:
            session = dict(_exact(root["session"], set(session), "session"))
            if (
                any(type(session[k]) is not int or session[k] <= 0 for k in session)
                or session["softDeadlineSec"] > 480
                or session["maxAttempts"] > 3
                or session["listenTimeoutSec"] > 6
            ):
                _fail("INVALID_SESSION", "invalid session policy")
            renderer = _exact(root["renderer"], {"rendererId", "visualLayoutContract"}, "renderer")
            if renderer != {
                "rendererId": "teebot-lesson-renderer.v5",
                "visualLayoutContract": "renderer-v5.layered-cinematic-layout.v1",
            }:
                _fail("UNSUPPORTED_RENDERER", "curriculum requires renderer v5 layered cinematic")
        else:
            renderer = _exact(root["renderer"], {"rendererId", "visualLayoutContract"}, "renderer")
            if (renderer["rendererId"], renderer["visualLayoutContract"]) not in {
                ("teebot-lesson-renderer.v4", "renderer-v4.course-mode-layout.v1"),
                ("teebot-lesson-renderer.v5", "renderer-v5.layered-cinematic-layout.v1"),
            }:
                _fail("UNSUPPORTED_RENDERER", "pilot renderer identity is invalid")
        raw_targets = root["targets"]
        if not isinstance(raw_targets, list) or not raw_targets or (not curriculum and len(raw_targets) > 2):
            _fail("TARGET_COUNT", "invalid target count")
        targets = []
        seen_target_ids: set[str] = set()
        for i, item in enumerate(raw_targets):
            t = _exact(item, _TARGET, "target")
            role = t["role"]
            if role not in _ROLES or (not curriculum and role != ("primary" if i == 0 else "optional_secondary")):
                _fail("INVALID_TARGET_ROLE", "target role is invalid")
            meanings, ids = t["vietnameseMeanings"], t["activityIds"]
            if (
                not isinstance(meanings, list)
                or not meanings
                or not all(isinstance(x, str) and x.strip() for x in meanings)
            ):
                _fail("INVALID_TARGET", "Vietnamese meanings are required")
            if not isinstance(ids, list) or (role == "primary" and not ids):
                _fail("INVALID_TARGET", "activityIds are required")
            target_id = _id(t["targetId"], "targetId")
            if target_id in seen_target_ids or not isinstance(t["targetWord"], str) or not t["targetWord"].strip():
                _fail("INVALID_TARGET", "target identity or word is invalid")
            seen_target_ids.add(target_id)
            targets.append(
                CourseWordTarget(
                    target_id,
                    cast(str, t["targetWord"]),
                    cast(str, role),
                    tuple(meanings),
                    tuple(_id(x, "activityId") for x in ids),
                )
            )
        if curriculum and not 2 <= sum(t.role == "primary" for t in targets) <= 4:
            _fail("TARGET_COUNT", "curriculum requires two to four primary targets")
        intents, evidence = root["embodiedIntentNames"], root["evidenceNames"]
        if not isinstance(intents, list) or not isinstance(evidence, list):
            _fail("INVALID_FIELDS", "name registries must be arrays")
        visual_focus = _exact(root["visualFocus"], {"directionSource", "regions", "presentCenterTarget"}, "visualFocus")
        regions = visual_focus["regions"]
        if not isinstance(regions, list) or not regions or not all(isinstance(region, str) for region in regions):
            _fail("INVALID_VISUAL_FOCUS", "visual focus regions are required")
        raw_activities = root["activities"]
        if not isinstance(raw_activities, list) or not raw_activities:
            _fail("INVALID_FIELDS", "activities must be an array")
        if curriculum and not 7 <= len(raw_activities) <= 10:
            _fail("INVALID_ACTIVITY_COUNT", "curriculum requires seven to ten activities")
        activities = []
        seen = set()
        authored = {t.target_id for t in targets}
        total = 0
        for item in raw_activities:
            if curriculum:
                a = _exact(item, _CURRICULUM, "activity", "INVALID_ACTIVITY_FIELDS")
                raw_ids = a["targetIds"]
            else:
                normalized = isinstance(item, Mapping) and "targetIds" in item
                a = _exact(
                    item, _PILOT | ({"targetIds"} if normalized else set()), "activity", "INVALID_ACTIVITY_FIELDS"
                )
                raw_ids = a.get("targetIds", [a["targetId"]])
                if normalized and raw_ids != [a["targetId"]]:
                    _fail("INVALID_ACTIVITY_TARGET", "normalized pilot identity changed")
            aid = _id(a["activityId"], "activityId")
            if aid in seen:
                _fail("DUPLICATE_ACTIVITY", "activity IDs must be unique")
            seen.add(aid)
            if not isinstance(raw_ids, list) or not raw_ids or not set(raw_ids) <= authored:
                _fail("INVALID_ACTIVITY_TARGET", "activity targets are not authored")
            answer = _exact(a["answerPolicy"], _ANSWER, "answerPolicy", "INVALID_ACTIVITY_FIELDS")
            stage = cast(str, a["stage"])
            if curriculum and stage not in _STAGES:
                _fail("INVALID_ACTIVITY_STAGE", "activity stage is not supported")
            if (
                not isinstance(a["activityType"], str)
                or not a["activityType"].strip()
                or len(a["activityType"]) > 128
            ):
                _fail("INVALID_ACTIVITY_TYPE", "activity type must be bounded text")
            if stage in _ASSESSMENT and (
                any(
                    answer[k] is not False
                    for k in (
                        "targetTextVisible",
                        "targetAudioBeforeAssessment",
                        "spokenTargetInPrompt",
                    "multipleChoiceContainsTarget",
                    )
                )
                or type(answer["minElapsedSinceFullModelMs"]) is not int
                or answer["minElapsedSinceFullModelMs"] < 20000
                or type(answer["minInterveningActivityCount"]) is not int
                or answer["minInterveningActivityCount"] < 1
            ):
                _fail("UNSAFE_ASSESSMENT", "assessment leaks the answer")
            modalities = ()
            duration = 0
            outcomes = MappingProxyType({})
            visual = MappingProxyType({})
            navigation_mode = "authoritative_graph" if curriculum else "target_stage"
            evidence_policy = "shared_outcome" if curriculum else "word_mastery"
            if curriculum:
                mods = a["modalities"]
                if not isinstance(mods, list) or not mods or not set(mods) <= _MODALITIES:
                    _fail("INVALID_MODALITY", "invalid modalities")
                modalities = tuple(mods)
                duration = a["expectedDurationSec"]
                if type(duration) is not int or duration <= 0:
                    _fail("INVALID_DURATION", "invalid duration")
                total += duration
                raw_out = a["outcomes"]
                if not isinstance(raw_out, Mapping) or not raw_out:
                    _fail("INVALID_OUTCOME", "outcomes required")
                checked = {}
                for name, out in raw_out.items():
                    if name not in _OUTCOME_NAMES or not isinstance(out, Mapping):
                        _fail("INVALID_OUTCOME", "invalid outcome")
                    expected_fields = {"action", "activityId"} if "activityId" in out else {"action"}
                    _exact(out, expected_fields, "outcome", "INVALID_OUTCOME")
                    action, dest = out.get("action"), out.get("activityId")
                    if action not in _ACTIONS or ((action in {"retry", "support"}) != isinstance(dest, str)):
                        _fail("INVALID_OUTCOME", "invalid outcome action")
                    if isinstance(dest, str):
                        _id(dest, "outcome activityId")
                    checked[name] = _freeze(out)
                outcomes = MappingProxyType(checked)
                authored_visual = _exact(a["visual"], _VISUAL, "visual", "INVALID_VISUAL")
                if not all(
                    authored_visual[key] is None or isinstance(authored_visual[key], str)
                    for key in ("backgroundAssetKey", "objectAssetKey")
                ) or not isinstance(authored_visual["strategy"], str) or not isinstance(authored_visual["fallback"], str):
                    _fail("INVALID_VISUAL", "visual values are invalid")
                if authored_visual["strategy"] not in _VISUAL_STRATEGIES or authored_visual["fallback"] not in _VISUAL_FALLBACKS:
                    _fail("INVALID_VISUAL", "visual strategy or fallback is unsupported")
                if (
                    authored_visual["strategy"] == "publishedTeachingObject"
                    and authored_visual["objectAssetKey"] is None
                ) or (
                    authored_visual["strategy"] == "embodiedFallback"
                    and authored_visual["objectAssetKey"] is not None
                ):
                    _fail("INVALID_VISUAL", "visual strategy does not match object binding")
                visual = _freeze(authored_visual)
            else:
                outcomes = MappingProxyType({
                    "correct": MappingProxyType({"action": "advance"}),
                    "near": MappingProxyType({"action": "advance"}),
                    "vietnamese": MappingProxyType({"action": "advance"}),
                    "incorrect": MappingProxyType({"action": "advance"}),
                    "silence": MappingProxyType({"action": "advance"}),
                    "help": MappingProxyType({"action": "support", "activityId": aid}),
                    "regulationBreak": MappingProxyType({"action": "pause"}),
                })
            if a["embodiedIntent"] not in intents:
                _fail("UNSUPPORTED_INTENT", "embodied intent is not registered")
            if a["evidenceName"] not in evidence:
                _fail("UNSUPPORTED_EVIDENCE", "evidence name is not registered")
            if a["visualFocusRegion"] not in regions:
                _fail("INVALID_VISUAL_FOCUS", "activity focus region is not registered")
            if tuple(a["listeningTransition"]) != _LISTENING_TRANSITION:
                _fail("INVALID_ACTIVITY_FIELDS", "listening transition is invalid")
            if a["reducedMotionFallback"] != "face_and_transient_focus_cue":
                _fail("INVALID_ACTIVITY_FIELDS", "reduced motion fallback is invalid")
            activities.append(
                CourseActivity(
                    aid,
                    tuple(raw_ids),
                    stage,
                    cast(str, a["activityType"]),
                    cast(str, a["evidenceName"]),
                    _id(a["contextId"], "contextId"),
                    cast(str, a["embodiedIntent"]),
                    cast(str, a["visualFocusRegion"]),
                    _freeze(answer),
                    tuple(cast(list[str], a["listeningTransition"])),
                    cast(str, a["reducedMotionFallback"]),
                    modalities,
                    duration,
                    outcomes,
                    visual,
                    navigation_mode,
                    evidence_policy,
                )
            )
        if curriculum and total > session["softDeadlineSec"]:
            _fail("INVALID_DURATION", "activities exceed deadline")
        if curriculum:
            activity_ids = {activity.activity_id for activity in activities}
            by_id = {activity.activity_id: activity for activity in activities}
            index_by_id = {activity.activity_id: index for index, activity in enumerate(activities)}
            for index, activity in enumerate(activities):
                for outcome in activity.outcomes.values():
                    action = outcome["action"]
                    if action in {"retry", "support"} and outcome["activityId"] not in activity_ids:
                        _fail("INVALID_OUTCOME_GRAPH", "outcome destination is not authored")
                    if action == "advance" and index + 1 >= len(activities):
                        _fail("INVALID_OUTCOME_GRAPH", "final activity cannot advance")
            visiting: set[str] = set()
            visited: set[str] = set()

            def visit(activity_id: str) -> None:
                if activity_id in visited:
                    return
                if activity_id in visiting:
                    _fail("INVALID_OUTCOME_GRAPH", "outcome graph contains a cycle")
                visiting.add(activity_id)
                activity = by_id[activity_id]
                for outcome in activity.outcomes.values():
                    if outcome["action"] == "advance":
                        visit(activities[index_by_id[activity_id] + 1].activity_id)
                    elif outcome["action"] in {"retry", "support"}:
                        visit(cast(str, outcome["activityId"]))
                visiting.remove(activity_id)
                visited.add(activity_id)

            visit(activities[0].activity_id)
            if visited != activity_ids:
                _fail("INVALID_OUTCOME_GRAPH", "activity is unreachable")
        required = {
            "DISCOVER",
            "UNDERSTAND",
            "GUIDED_ACTION",
            "SUPPORTED_SPEECH",
            "RECALL",
            "TRANSFER",
            "DELAYED_RECALL",
        }
        if curriculum and any(
            not required <= {a.stage for a in activities if t.target_id in a.target_ids}
            for t in targets
            if t.role == "primary"
        ):
            _fail("MISSING_REQUIRED_ACTIVITY", "primary target lacks required stages")
        if not curriculum and not {"UNDERSTAND", "RECALL", "TRANSFER", "DELAYED_RECALL"} <= {
            a.stage for a in activities if targets[0].target_id in a.target_ids
        }:
            _fail("MISSING_REQUIRED_ACTIVITY", "primary target lacks required assessments")
        for target in targets:
            if tuple(a.activity_id for a in activities if target.target_id in a.target_ids) != target.activity_ids:
                _fail("ACTIVITY_IDENTITY_MISMATCH", "target activity IDs must match authored order")
        if verify_checksum and root["contractChecksum"] != _checksum(root, not curriculum):
            _fail("CHECKSUM_MISMATCH", "canonical checksum does not match")
        if (
            verify_checksum
            and not curriculum
            and root["contractChecksum"] not in {FROZEN_CONTRACT_CHECKSUM, CANONICAL_V5_CONTRACT_CHECKSUM}
        ):
            _fail("UNSUPPORTED_CONTRACT_CHECKSUM", "contract checksum is not an approved pilot identity")
        return cls(
            cast(str, root["contractVersion"]),
            cast(str, root["contractChecksum"]),
            cast(str, root["fixtureId"]),
            session_id,
            tuple(targets),
            tuple(activities),
            tuple(evidence),
            tuple(intents),
            session["softDeadlineSec"],
            session["maxAttempts"],
            session["listenTimeoutSec"],
            curriculum,
        )

    def activity(self, activity_id: str) -> CourseActivity:
        for activity in self.activities:
            if activity.activity_id == activity_id:
                return activity
        raise KeyError(activity_id)
