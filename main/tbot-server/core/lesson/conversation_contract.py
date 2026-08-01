"""Immutable input and identity contracts for guided TVideo conversations."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, NoReturn, cast

_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z '\-]{0,79}")
_CUE_EFFECTS = MappingProxyType(
    {
        "teach": "show_teaching_scene",
        "listen": "show_listening_scene",
        "thinking": "show_thinking_scene",
        "correct": "show_correct_reaction",
        "retry_level_1": "show_effort_reaction",
        "retry_level_2": "show_slow_model",
        "retry_level_3": "show_pronunciation_guide",
        "celebrate": "show_celebration",
        "word_transition": "show_word_transition",
    }
)
_BACKEND_CUE_EFFECTS = MappingProxyType(
    {
        "teach": ("teach", "show_teaching_scene", "once"),
        "listen": ("listen", "show_listening_scene", "loop"),
        "thinking": ("thinking", "show_thinking_scene", "loop"),
        "correct": ("correct", "show_correct_reaction", "once"),
        "retry-level-1": ("retry_level_1", "show_effort_reaction", "once"),
        "retry-level-2": ("retry_level_2", "show_slow_model", "once"),
        "retry-level-3": ("retry_level_3", "show_pronunciation_guide", "once"),
        "celebrate": ("celebrate", "show_celebration", "once"),
        "word-transition": ("word_transition", "show_word_transition", "once"),
    }
)
_CONTRACT_FIELDS = frozenset(
    {
        "lesson_session_id",
        "lesson_id",
        "lesson_version",
        "step_key",
        "target_word",
        "meanings_vi",
        "related_concepts",
        "question_seeds",
        "teaching_copy",
        "expected_answer",
        "progress_index",
        "progress_count",
        "pronunciation",
        "cues",
        "max_contextual_turns",
    }
)


class ConversationContractError(ValueError):
    """Raised when an authoritative conversation input is not exact and safe."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def _fail(code: str, message: str) -> NoReturn:
    raise ConversationContractError(code, message)


def _exact_mapping(value: Any, fields: set[str] | frozenset[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        _fail("INVALID_FIELDS", f"{label} fields must match the contract exactly")
    return cast(Mapping[str, Any], value)


def _safe_id(value: Any, label: str) -> str:
    if not isinstance(value, str) or _ID_RE.fullmatch(value) is None:
        _fail("UNSAFE_ID", f"{label} is not a safe identifier")
    return cast(str, value)


def _nonempty_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or any(ord(char) < 32 for char in value):
        _fail("INVALID_CONTENT", f"{label} must be non-empty safe text")
    return cast(str, value).strip()


def _normalize(value: str) -> str:
    return " ".join(value.casefold().split())


def _unique_text_tuple(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or not value:
        _fail("INVALID_CONTENT", f"{label} must be a non-empty list")
    result = tuple(_nonempty_text(item, label) for item in value)
    normalized = tuple(_normalize(item) for item in result)
    if len(set(normalized)) != len(normalized):
        _fail("DUPLICATE_CONTENT", f"{label} contains normalized duplicates")
    return result


@dataclass(frozen=True)
class PronunciationGuidance:
    slow_model: str
    segments: tuple[str, ...] | None
    phonemes: tuple[str, ...] | None
    l1_guidance_vi: str

    @classmethod
    def from_mapping(cls, value: Any) -> PronunciationGuidance:
        if not isinstance(value, Mapping):
            _fail("INVALID_PRONUNCIATION", "pronunciation must be an object")
        segment_fields = {"slow_model", "segments", "l1_guidance_vi"}
        phoneme_fields = {"slow_model", "phonemes", "l1_guidance_vi"}
        fields = set(value)
        if fields == segment_fields:
            segments = _unique_text_tuple(value["segments"], "segments")
            phonemes = None
        elif fields == phoneme_fields:
            segments = None
            phonemes = _unique_text_tuple(value["phonemes"], "phonemes")
        else:
            _fail("INVALID_PRONUNCIATION", "provide exactly segments or phonemes")
        return cls(
            slow_model=_nonempty_text(value["slow_model"], "slow_model"),
            segments=segments,
            phonemes=phonemes,
            l1_guidance_vi=_nonempty_text(value["l1_guidance_vi"], "l1_guidance_vi"),
        )


@dataclass(frozen=True)
class CueSpec:
    role: str
    cue_id: str
    effect: str


@dataclass(frozen=True)
class LessonConversationContract:
    lesson_session_id: str
    lesson_id: str
    lesson_version: int
    step_key: str
    target_word: str
    meanings_vi: tuple[str, ...]
    related_concepts: tuple[str, ...]
    question_seeds: tuple[str, ...]
    teaching_copy: str
    expected_answer: str
    progress_index: int
    progress_count: int
    pronunciation: PronunciationGuidance
    cues: tuple[CueSpec, ...]
    max_contextual_turns: int

    @classmethod
    def from_mapping(cls, value: Any) -> LessonConversationContract:
        source = _exact_mapping(value, _CONTRACT_FIELDS, "conversation")
        lesson_version = source["lesson_version"]
        progress_index = source["progress_index"]
        progress_count = source["progress_count"]
        if type(lesson_version) is not int or lesson_version <= 0:
            _fail("INVALID_PROGRESS", "lesson_version must be positive")
        if (
            type(progress_index) is not int
            or type(progress_count) is not int
            or progress_index <= 0
            or progress_count <= 0
            or progress_index > progress_count
        ):
            _fail("INVALID_PROGRESS", "progress index/count are invalid")
        target = _nonempty_text(source["target_word"], "target_word")
        answer = _nonempty_text(source["expected_answer"], "expected_answer")
        teaching_copy = _nonempty_text(source["teaching_copy"], "teaching_copy")
        if _WORD_RE.fullmatch(target) is None or _normalize(target) != _normalize(answer):
            _fail("TARGET_ANSWER_MISMATCH", "expected answer must be the target English word")
        if _normalize(target) not in _normalize(teaching_copy):
            _fail("TARGET_COPY_MISMATCH", "teaching copy must teach the target word")
        expected_cue_roles = set(_CUE_EFFECTS)
        if progress_index == progress_count:
            expected_cue_roles.remove("word_transition")
        cues_source = _exact_mapping(source["cues"], expected_cue_roles, "cues")
        cues: list[CueSpec] = []
        seen_ids: set[str] = set()
        for role, expected_effect in _CUE_EFFECTS.items():
            if role not in expected_cue_roles:
                continue
            cue = _exact_mapping(cues_source[role], {"cue_id", "effect"}, f"cue {role}")
            cue_id = _safe_id(cue["cue_id"], f"cue {role}")
            if cue_id in seen_ids:
                _fail("DUPLICATE_CUE", "cue identifiers must be unique")
            if cue["effect"] != expected_effect:
                _fail("CUE_EFFECT_MISMATCH", f"cue {role} has an unapproved effect")
            seen_ids.add(cue_id)
            cues.append(CueSpec(role=role, cue_id=cue_id, effect=expected_effect))
        max_contextual_turns = source["max_contextual_turns"]
        if type(max_contextual_turns) is not int or not 0 <= max_contextual_turns <= 2:
            _fail("INVALID_CONTEXT_LIMIT", "max contextual turns must be fixed at two or fewer")
        return cls(
            lesson_session_id=_safe_id(source["lesson_session_id"], "lesson_session_id"),
            lesson_id=_safe_id(source["lesson_id"], "lesson_id"),
            lesson_version=lesson_version,
            step_key=_safe_id(source["step_key"], "step_key"),
            target_word=target,
            meanings_vi=_unique_text_tuple(source["meanings_vi"], "meanings_vi"),
            related_concepts=_unique_text_tuple(source["related_concepts"], "related_concepts"),
            question_seeds=_unique_text_tuple(source["question_seeds"], "question_seeds"),
            teaching_copy=teaching_copy,
            expected_answer=answer,
            progress_index=progress_index,
            progress_count=progress_count,
            pronunciation=PronunciationGuidance.from_mapping(source["pronunciation"]),
            cues=tuple(cues),
            max_contextual_turns=max_contextual_turns,
        )

    @property
    def cue_map(self) -> Mapping[str, CueSpec]:
        return MappingProxyType({cue.role: cue for cue in self.cues})

    @property
    def is_terminal_step(self) -> bool:
        return self.progress_index == self.progress_count


def lesson_conversation_contract_from_backend(
    manifest: Any,
    *,
    lesson_session_id: str,
    step_key: str,
) -> LessonConversationContract:
    """Validate the backend v4 DTO and adapt one authored step to the pure FSM shape."""
    if not isinstance(manifest, Mapping):
        _fail("INVALID_BACKEND_MANIFEST", "lesson manifest must be an object")
    root = cast(Mapping[str, Any], manifest)
    if (
        root.get("manifestVersion") != "teebot-lesson-renderer.v4"
        or root.get("protocolVersion") != "teebot-lesson-renderer.v4"
    ):
        _fail("INVALID_BACKEND_MANIFEST", "conversation requires renderer v4")
    lesson_id = _safe_id(root.get("lessonId"), "lessonId")
    lesson_version = root.get("lessonVersion")
    if type(lesson_version) is not int or lesson_version <= 0:
        _fail("INVALID_BACKEND_MANIFEST", "lessonVersion must be positive")
    conversation = _exact_mapping(
        root.get("conversation"),
        {"presetId", "presetVersion", "maxContextualTurns", "steps"},
        "conversation DTO",
    )
    if conversation["presetId"] != "tvideoJourney" or conversation["presetVersion"] != 1:
        _fail("INVALID_BACKEND_MANIFEST", "unsupported conversation preset")
    if conversation["maxContextualTurns"] != 2:
        _fail("INVALID_BACKEND_MANIFEST", "maxContextualTurns must be exactly two")
    raw_steps = conversation["steps"]
    if not isinstance(raw_steps, list) or len(raw_steps) != 2:
        _fail("INVALID_BACKEND_MANIFEST", "tvideoJourney.v1 requires exactly two authored steps")
    normalized_steps: list[dict[str, Any]] = []
    seen_steps: set[str] = set()
    seen_cues: set[str] = set()
    for index, raw_step in enumerate(raw_steps):
        step = _exact_mapping(
            raw_step,
            {
                "stepKey", "targetWord", "vietnameseMeanings", "relatedConcepts",
                "questionSeeds", "teachingCopy", "expectedAnswer", "progress",
                "pronunciation", "contextTurns", "cues",
            },
            "conversation step DTO",
        )
        current_step_key = _safe_id(step["stepKey"], "stepKey")
        if current_step_key in seen_steps:
            _fail("INVALID_BACKEND_MANIFEST", "conversation step keys must be unique")
        seen_steps.add(current_step_key)
        progress = _exact_mapping(step["progress"], {"index", "count"}, "progress DTO")
        if progress["index"] != index + 1 or progress["count"] != len(raw_steps):
            _fail("INVALID_BACKEND_MANIFEST", "conversation progress does not match step order")
        teaching = _exact_mapping(
            step["teachingCopy"], {"intro", "explanation", "prompt"}, "teachingCopy DTO"
        )
        pronunciation_source = step["pronunciation"]
        if not isinstance(pronunciation_source, Mapping):
            _fail("INVALID_BACKEND_MANIFEST", "pronunciation DTO must be an object")
        unit_key = "approvedSegments" if "approvedSegments" in pronunciation_source else "approvedPhonemes"
        pronunciation = _exact_mapping(
            pronunciation_source,
            {"slowModel", unit_key, "vietnameseL1Guidance"},
            "pronunciation DTO",
        )
        context_turns = _unique_text_tuple(step["contextTurns"], "contextTurns")
        if len(context_turns) > 2:
            _fail("INVALID_BACKEND_MANIFEST", "contextTurns exceeds the bounded contract")
        raw_cues = step["cues"]
        if not isinstance(raw_cues, list):
            _fail("INVALID_BACKEND_MANIFEST", "cues DTO must be a list")
        expected_effect_order = list(_BACKEND_CUE_EFFECTS)
        if index + 1 == len(raw_steps):
            expected_effect_order.remove("word-transition")
        expected_effects = set(expected_effect_order)
        cue_mapping: dict[str, dict[str, str]] = {}
        actual_effects: set[str] = set()
        for raw_cue in raw_cues:
            cue = _exact_mapping(raw_cue, {"cueId", "effect", "playbackMode"}, "cue DTO")
            effect = cue["effect"]
            if effect not in expected_effects or effect in actual_effects:
                _fail("INVALID_BACKEND_MANIFEST", "cue effects do not match the authored step")
            role, internal_effect, playback_mode = _BACKEND_CUE_EFFECTS[effect]
            cue_id = _safe_id(cue["cueId"], "cueId")
            if cue_id in seen_cues or cue["playbackMode"] != playback_mode:
                _fail("INVALID_BACKEND_MANIFEST", "cue identity or playback mode is stale")
            actual_effects.add(effect)
            seen_cues.add(cue_id)
            cue_mapping[role] = {"cue_id": cue_id, "effect": internal_effect}
        if actual_effects != expected_effects or [cue["effect"] for cue in raw_cues] != expected_effect_order:
            _fail("INVALID_BACKEND_MANIFEST", "cue effects do not match the authored step")
        normalized_steps.append(
            {
                "lesson_session_id": lesson_session_id,
                "lesson_id": lesson_id,
                "lesson_version": lesson_version,
                "step_key": current_step_key,
                "target_word": step["targetWord"],
                "meanings_vi": step["vietnameseMeanings"],
                "related_concepts": step["relatedConcepts"],
                "question_seeds": step["questionSeeds"],
                "teaching_copy": " ".join(
                    _nonempty_text(teaching[field], field)
                    for field in ("intro", "explanation", "prompt")
                ),
                "expected_answer": step["expectedAnswer"],
                "progress_index": progress["index"],
                "progress_count": progress["count"],
                "pronunciation": {
                    "slow_model": pronunciation["slowModel"],
                    "segments" if unit_key == "approvedSegments" else "phonemes": pronunciation[unit_key],
                    "l1_guidance_vi": " ".join(
                        _unique_text_tuple(pronunciation["vietnameseL1Guidance"], "vietnameseL1Guidance")
                    ),
                },
                "cues": cue_mapping,
                "max_contextual_turns": conversation["maxContextualTurns"],
            }
        )
    first_step_key = normalized_steps[0]["step_key"]
    expected_cinematics: list[tuple[str, str, str, str]] = [
        (f"{first_step_key}-opening", "opening", first_step_key, "once"),
        (f"{first_step_key}-greet", "greet", first_step_key, "loop"),
    ]
    for index, step in enumerate(normalized_steps):
        for raw_cue in raw_steps[index]["cues"]:
            cue_step_key = (
                normalized_steps[index + 1]["step_key"]
                if raw_cue["effect"] == "word-transition"
                else step["step_key"]
            )
            expected_cinematics.append(
                (raw_cue["cueId"], raw_cue["effect"], cue_step_key, raw_cue["playbackMode"])
            )
    cinematic_phases = root.get("cinematicPhases")
    if not isinstance(cinematic_phases, list) or len(cinematic_phases) != 19:
        _fail("INVALID_BACKEND_MANIFEST", "tvideoJourney.v1 requires exactly 19 cinematic cues")
    actual_cinematics: list[tuple[str, str, str, str]] = []
    for raw_phase in cinematic_phases:
        phase = _exact_mapping(
            raw_phase,
            {
                "templateId", "templateVersion", "cueId", "effect", "stepKey",
                "playbackMode", "timing", "asset",
            },
            "cinematic cue DTO",
        )
        if phase["templateId"] != "flattenedMjpegCinematic" or phase["templateVersion"] != 2:
            _fail("INVALID_BACKEND_MANIFEST", "cinematic cue template identity is invalid")
        actual_cinematics.append(
            (
                _safe_id(phase["cueId"], "cinematic cueId"),
                phase["effect"],
                _safe_id(phase["stepKey"], "cinematic stepKey"),
                phase["playbackMode"],
            )
        )
    if actual_cinematics != expected_cinematics:
        _fail("INVALID_BACKEND_MANIFEST", "cinematic cues do not match conversation authority")
    selected = next((step for step in normalized_steps if step["step_key"] == step_key), None)
    if selected is None:
        _fail("INVALID_BACKEND_MANIFEST", "requested conversation step is absent")
    return LessonConversationContract.from_mapping(selected)


@dataclass(frozen=True)
class LessonToolIdentity:
    lesson_session_id: str
    turn_sequence_id: int
    attempt_id: str
    step_key: str
    cue_id: str | None = None

    def __post_init__(self) -> None:
        _safe_id(self.lesson_session_id, "lesson_session_id")
        _safe_id(self.attempt_id, "attempt_id")
        _safe_id(self.step_key, "step_key")
        if type(self.turn_sequence_id) is not int or self.turn_sequence_id < 1:
            _fail("INVALID_IDENTITY", "turn_sequence_id must be a positive integer")
        if self.cue_id is not None:
            _safe_id(self.cue_id, "cue_id")

    @classmethod
    def from_mapping(cls, value: Any) -> LessonToolIdentity:
        source = _exact_mapping(
            value,
            {"lesson_session_id", "turn_sequence_id", "attempt_id", "step_key", "cue_id"},
            "tool identity",
        )
        return cls(**source)

    def to_mapping(self) -> dict[str, str | int | None]:
        return {
            "lesson_session_id": self.lesson_session_id,
            "turn_sequence_id": self.turn_sequence_id,
            "attempt_id": self.attempt_id,
            "step_key": self.step_key,
            "cue_id": self.cue_id,
        }
