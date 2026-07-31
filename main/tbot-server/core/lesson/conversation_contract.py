"""Immutable input and identity contracts for guided TVideo conversations."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

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


def _fail(code: str, message: str) -> None:
    raise ConversationContractError(code, message)


def _exact_mapping(value: Any, fields: set[str] | frozenset[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        _fail("INVALID_FIELDS", f"{label} fields must match the contract exactly")
    return value


def _safe_id(value: Any, label: str) -> str:
    if not isinstance(value, str) or _ID_RE.fullmatch(value) is None:
        _fail("UNSAFE_ID", f"{label} is not a safe identifier")
    return value


def _nonempty_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or any(ord(char) < 32 for char in value):
        _fail("INVALID_CONTENT", f"{label} must be non-empty safe text")
    return value.strip()


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
        cues_source = _exact_mapping(source["cues"], set(_CUE_EFFECTS), "cues")
        cues: list[CueSpec] = []
        seen_ids: set[str] = set()
        for role, expected_effect in _CUE_EFFECTS.items():
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
