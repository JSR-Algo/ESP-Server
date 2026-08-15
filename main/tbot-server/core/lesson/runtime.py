"""S8 — single-step lesson interpreter (+ S6 capability gate, restart re-attest).

Drives the slice state machine for ONE espTft ``model`` step, byte-consistent with
the frozen S2 fixture ``fixtures/lesson-protocol.v1.json``:

    prepare(seq1) -> [preload, ESP-synth status] -> start(seq2) -> step s4(seq3) -> stop(seq4)

P0 ack contract (plan §5.3): the ESP correlates each inbound ``lesson_ack`` on
``body.acks == the sequence of the outstanding S->F frame``. The ack's OWN envelope
``sequence`` is the firmware's F->S counter and is NEVER used for correlation; there
is NO ``ackFor`` field anywhere.

Everything here lives in the ``ws_server`` process and is additive — it never
touches the voice path.
"""

from __future__ import annotations

import asyncio
import copy
import json
import re
import math
import time
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional
from urllib.parse import quote

from core.lesson.errors import (
    LessonError,
    ProtocolSequenceError,
    StepTimeout,
    PROTOCOL_VERSION,
    LESSON_VERSION_UNSUPPORTED,
    ASSET_PACK_NOT_READY,
    ASSET_PACK_MATERIALIZE_FAILED,
    LESSON_FRAME_TOO_LARGE,
    LESSON_FRAME_INVALID,
    LESSON_FRAME_ACK_TIMEOUT,
    CINEMATIC_CAPABILITY_UNSUPPORTED,
    lesson_capability_ok,
    device_renderer_capabilities,
)
from core.lesson.cinematic_contract import (
    CinematicContractError,
    RENDERER_V3,
    project_cinematic_phase,
)
from core.lesson.flattened_cinematic_contract import (
    FlattenedCinematicContractError,
    cinematic_identity_key,
    RENDERER_V4,
    project_flattened_cinematic_phase,
    validate_flattened_cinematic_manifest,
)
from core.lesson.layered_cinematic_contract import (
    KNOWN_PHASE_IDS as LAYERED_CINEMATIC_PHASE_IDS,
    LayeredCinematicContractError,
    RENDERER_V5,
    project_layered_cinematic_phase,
)
from core.lesson.conversation_contract import (
    ConversationContractError,
    LessonToolIdentity,
    lesson_conversation_contract_from_backend,
)
from core.lesson.conversation_runtime import (
    ConversationDecision,
    ConversationState,
    LessonConversationRuntime,
    SpeakingEvidence,
    inactive_conversation_decision,
)
from core.providers.tools.device_mcp.mcp_handler import call_mcp_tool
from core.utils.util import get_vision_url
from core.lesson.interaction_templates import FUN_PATTERN_PROMPTS, SafeSpeakingSession, fun_pattern_prompt
from core.lesson.log_context import with_lesson_log_context
from core.lesson.motion_presets import dispatch_motion_preset
from core.lesson.sd_pack_mcp_payload import (
    FirmwareSyncPackError,
    build_firmware_sync_pack,
)
from core.lesson.sd_pack_sync import request_sd_pack_sync, sd_pack_sync_timeout_sec

TAG = "LessonRuntime"
SD_ASSET_SYNC_TOOL = "self_lesson_assets_sync_to_sd"
SAMPLE_SD_ASSET_SYNC_TOOL = "self_lesson_assets_sync_sample_to_sd"


class _SdSyncRealtimeBusyTimeoutError(Exception):
    def __init__(self, timeout_sec: float, state: str) -> None:
        self.timeout_sec = timeout_sec
        self.state = state
        super().__init__(f"realtime busy for {timeout_sec:.3f}s state={state}")

# Keep command frames small. Images/media must travel as URLs or verified SD paths,
# never inline JSON, so 16 KiB is generous for a 3-layer step with prompts/choices.
MAX_LESSON_FRAME_BYTES = 16 * 1024

NO_CURRENT_ASSIGNMENT_MESSAGE = "Robot chưa có bài học nào được giao."
RENDERER_V2 = "teebot-lesson-renderer.v2"
RENDERER_V2_VISUAL_STATES = frozenset(
    {
        "teach", "listen", "thinking", "correct", "nearMiss",
        "incorrect", "retry", "celebrate", "completion",
    }
)
RENDERER_V2_DEFAULT_MOTION_SLOTS = {
    "thinking": "thinking",
    "retry": "incorrect",
}
RENDERER_V2_DEFAULT_MOTION_PRESETS = {
    "thinking": "thinking",
    "retry": "tryAgain",
}
VISUAL_DEGRADED_REASONS = frozenset(
    {
        "missingOverlay",
        "animationStartFailed",
        "phaseTimeout",
        "reducedMotion",
        "unsupportedContract",
        "assetIdentityMismatch",
        "insufficientHeap",
    }
)
VISUAL_REJECTED_REASONS = VISUAL_DEGRADED_REASONS | frozenset({"superseded"})
MAX_RETIRED_VISUAL_ACK_SEQUENCES = 128
MAX_RETIRED_CONVERSATION_ACK_SEQUENCES = 128
CINEMATIC_START_SEND_FAILED = "CINEMATIC_START_SEND_FAILED"
PARENT_RUNTIME_PHASES = frozenset(
    {
        "preparing",
        "entrance",
        "teaching",
        "listening",
        "thinking",
        "feedback",
        "paused",
        "resumed",
        "completed",
        "failed",
        "abandoned",
    }
)
VISUAL_STATE_PARENT_PHASE = {
    "teach": "teaching",
    "listen": "listening",
    "thinking": "thinking",
    "correct": "feedback",
    "nearMiss": "feedback",
    "incorrect": "feedback",
    "retry": "feedback",
    "celebrate": "feedback",
    "completion": "feedback",
}


@dataclass(frozen=True)
class VisualAckResult:
    accepted: bool
    degraded: bool
    degraded_reason: Optional[str]
    sequence: Optional[int]
    visual_generation: int
    timed_out: bool = False


@dataclass(frozen=True)
class ConversationLiveFallbackDirective:
    accepted: bool
    code: str
    reason: str
    window_id: str | None
    reconnect_allowed: bool
    prompt: str


def _set_lesson_start_status(conn: Any, code: str, message: str = "", *, reason: str = "") -> None:
    try:
        status = {"code": code, "message": message}
        if reason:
            status["reason"] = reason
        conn.lesson_start_status = status
    except Exception:
        pass

def _compact_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except Exception:
        return "{}"

def _safe_tvideo_projection(value: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(value, dict):
        return None
    allowed_keys = {"templateId", "templateVersion", "layoutPreset", "geometryVersion", "phases", "revealPhase", "fallbackPolicy", "background", "arrivedPose", "atlas"}
    if set(value) - allowed_keys:
        return None
    if value.get("templateId") != "tvideoFlyWalk" or type(value.get("templateVersion")) is not int or value.get("templateVersion") != 1:
        return None
    if value.get("layoutPreset") not in {"centerRoad", "leftApproach", "rightApproach"} or type(value.get("geometryVersion")) is not int or value.get("geometryVersion") != 1:
        return None
    expected_phases = [
        {"name": "hidden", "durationMs": 100}, {"name": "flyIn", "durationMs": 1200},
        {"name": "landFar", "durationMs": 700}, {"name": "settle", "durationMs": 350},
        {"name": "walkToward", "durationMs": 1800}, {"name": "arriveNear", "durationMs": 250},
        {"name": "greetIdle", "durationMs": 650}, {"name": "revealTeachingContent", "durationMs": 100},
    ]
    if value.get("phases") != expected_phases or value.get("revealPhase") != "revealTeachingContent" or value.get("fallbackPolicy") != "snapToArriveNearAndReveal":
        return None

    def pinned_asset(asset: Any, *, atlas: bool = False) -> Optional[Dict[str, Any]]:
        if not isinstance(asset, dict) or set(asset) != {"versionId", "sha256", "bytes", "mediaType"}:
            return None
        media_type = asset.get("mediaType")
        if not isinstance(asset.get("versionId"), str) or not asset["versionId"].strip():
            return None
        if not isinstance(asset.get("sha256"), str) or re.fullmatch(r"[0-9a-fA-F]{64}", asset["sha256"]) is None:
            return None
        if type(asset.get("bytes")) is not int or asset["bytes"] <= 0 or asset["bytes"] > 4 * 1024 * 1024:
            return None
        if media_type not in ({"image/png"} if atlas else {"image/png", "image/jpeg"}):
            return None
        return {"versionId": asset["versionId"].strip(), "sha256": asset["sha256"].lower(), "bytes": asset["bytes"], "mediaType": media_type}

    background = pinned_asset(value.get("background"))
    arrived_pose = pinned_asset(value.get("arrivedPose"))
    atlas = pinned_asset(value.get("atlas"), atlas=True) if value.get("atlas") is not None else None
    if background is None or arrived_pose is None or (value.get("atlas") is not None and atlas is None):
        return None
    return {
        "templateId": "tvideoFlyWalk", "templateVersion": 1,
        "layoutPreset": value["layoutPreset"], "geometryVersion": 1,
        "phases": expected_phases, "revealPhase": "revealTeachingContent",
        "fallbackPolicy": "snapToArriveNearAndReveal",
        "background": background, "arrivedPose": arrived_pose,
        **({"atlas": atlas} if atlas is not None else {}),
    }

def _lesson_trace_context_from_headers(headers: Any) -> Dict[str, str]:
    if not isinstance(headers, dict):
        return {}
    normalized = {str(key).lower(): value for key, value in headers.items()}
    out: Dict[str, str] = {}
    for key in ("traceparent", "tracestate"):
        value = normalized.get(key)
        if isinstance(value, str) and value.strip():
            out[key] = value.strip()
    return out

def _manifest_story_log_summary(manifest: Dict[str, Any]) -> List[Dict[str, Any]]:
    summary: List[Dict[str, Any]] = []
    for step in manifest.get("steps", []) or []:
        if not isinstance(step, dict):
            continue
        item: Dict[str, Any] = {"id": step.get("id")}
        if step.get("completionClass") is not None:
            item["completionClass"] = step.get("completionClass")
        if step.get("storyBeat") is not None:
            item["storyBeat"] = step.get("storyBeat")
        elif step.get("storyText") is not None:
            item["storyText"] = True
        if len(item) > 1:
            summary.append(item)
    return summary

def _norm_prompt_for_log(prompt: Any) -> str:
    """One-line, quote-safe form of a spoken prompt for the log."""
    text = str(prompt or "").replace('"', "'")
    text = " ".join(text.split())
    return text[:160]


def _manifest_steps_log_summary(manifest: Dict[str, Any]) -> Dict[str, Any]:
    """The served step roster: every step's id, order and completion class.

    Deliberately covers EVERY step, unlike `_manifest_story_log_summary`, which keeps
    only the steps carrying story content. A roster with holes cannot answer "did the
    robot run the lesson it was served", which is the question the completed-step
    checkpoints exist to answer.
    """
    steps: List[Dict[str, Any]] = []
    for order, step in enumerate(manifest.get("steps", []) or [], start=1):
        if not isinstance(step, dict):
            continue
        item: Dict[str, Any] = {"id": step.get("id"), "order": order}
        if step.get("completionClass") is not None:
            item["completionClass"] = step.get("completionClass")
        story_beat = step.get("storyBeat")
        if isinstance(story_beat, dict) and story_beat.get("waitForChild") is not None:
            item["waitForChild"] = bool(story_beat.get("waitForChild"))
        steps.append(item)
    return {"steps": steps}


def _lesson_step_media_log_summary(scene: Dict[str, Any]) -> str:
    """Comma-joined media srcs a lesson_step frame declares (background, teaching object)."""
    urls: List[str] = []
    for layer in ("backgroundScene", "teachingObject", "robotOverlay"):
        node = scene.get(layer)
        if not isinstance(node, dict):
            continue
        for holder in ("video", "poster", "asset"):
            candidate = node.get(holder)
            if isinstance(candidate, dict) and isinstance(candidate.get("src"), str):
                urls.append(candidate["src"])
    # SPACE-separated: the checkpoint contract scans a line for media URLs with a
    # regex, and comma-joining them runs two URLs together into one unmatchable token.
    return " ".join(urls) if urls else "none"


# Runtime states (a slice subset of the assignment state machine).
S_IDLE = "IDLE"
S_PRELOADING = "PRELOADING"
S_READY = "READY"
S_RUNNING = "RUNNING"
S_PAUSED = "PAUSED"
_SD_PACK_BOOT_CLEANED_ROOTS: set[Path] = set()
S_COMPLETED = "COMPLETED"
S_FAILED = "FAILED"

# ── per-step completion semantics (P5 playability fix + L3 P1 author types) ──────
# A step splits into one of two completion classes ON THE WIRE:
#
#   PASSIVE narration — the robot just speaks/animates; the child does NOT tap or
#   answer, so the FIRMWARE NEVER emits a step_completed progress for it. It
#   AUTO-ADVANCES once the firmware acks the lesson_step (render confirmed). A
#   passive step's per-step timer is a display DWELL that, if it fires, is a NORMAL
#   advance — never a FAILED StepTimeout.
#
#   INTERACTIVE — the child answers after render ack opens a listening/response
#   window. The runtime waits for render ack AND child response evidence (voice
#   transcript today, compatible lesson_progress with response detail for older/future
#   clients). STEP_TIMEOUT still fires on ACK ABSENCE.
#
# AUTHORITATIVE CLASSIFIER (L3 P1): the backend manifest step now carries an
# explicit ``completionClass`` ('passive' | 'interactive') per step. The runtime
# trusts THAT — a step is PASSIVE iff completionClass == 'passive', INTERACTIVE iff
# == 'interactive'. This lets authors define NEW step types (e.g. 'songBreak',
# 'warmup', 'recap') that reuse existing render triples without being misclassified
# by a hardcoded type set. NO protocol-version change: completionClass is an additive
# field inside the existing renderer-v1 manifest step.
#
# BACKWARD-COMPAT FALLBACK: a step with NO completionClass (older backend, or a v1
# manifest predating this field) falls back to the v1 BUILTIN set membership below,
# so nothing regresses for the current seed/manifests. An unknown/None stepType under
# that fallback is treated as INTERACTIVE (conservative: keep waiting for
# step_completed rather than silently auto-advancing an unrecognized step).
#
# LOAD-BEARING INVARIANT (pin the dependency): a PASSIVE step auto-advances on its
# ack; an INTERACTIVE step waits for child response evidence after render ack. Do
# NOT rely on draw success as completion; classify from completionClass (falling
# back to PASSIVE_STEP_TYPES) and only finish interactive steps from transcript or
# compatible progress detail carrying response evidence.
#
# v1 BUILTIN fallback set: the 5 passive narration kinds of the original 9-type
# STEP_RENDER_MAP. Documented + retained ONLY as the no-completionClass fallback.
PASSIVE_STEP_TYPES = frozenset(
    {"greeting", "review", "focus", "feedback", "celebrate"}
)

EMPTY_CHILD_RESPONSE_VALUES = frozenset(
    {
        "",
        "null",
        "none",
        "false",
        "0",
        "[]",
        "{}",
        "unknown",
        "unrecognized",
        "noise",
        "[noise]",
        "inaudible",
        "[inaudible]",
        "silence",
        "no_speech",
        "no-speech",
        "...",
        "<unk>",
        "unk",
        "n/a",
        "na",
    }
)

CHILD_RESPONSE_DETAIL_KEYS = (
    "utterance",
    "choiceId",
    "choice",
    "tapTargetHit",
    "recognizedText",
    "transcript",
    "childResponse",
)

CHILD_RESPONSE_FLAG_KEYS = ("accepted", "handled", "recognized")
CHILD_RESPONSE_CONFIDENCE_KEYS = ("confidence", "asrConfidence", "asr_confidence")
STEP_METADATA_KEYS = ("story", "storyText", "storyBeat", "vocab")
EXPECTED_CHILD_RESPONSE_KEYS = (
    "expectedResponses",
    "acceptedResponses",
    "expectedResponse",
    "expectedText",
    "targetWord",
)
IMMEDIATE_SCORING_DETAIL_KEYS = frozenset(
    {
        "score",
        "accuracy",
        "pronunciation",
        "pronunciationscore",
        "pronunciation_score",
        "pronunciationassessment",
        "pronunciation_assessment",
        "phoneme",
        "phonemescore",
        "phoneme_score",
        "phonemeassessment",
        "phoneme_assessment",
        "correction",
        "correctedtext",
        "corrected_text",
        "verdict",
    }
)

CHILD_RESPONSE_INTENT_CORRECT = "correct"
CHILD_RESPONSE_INTENT_WRONG = "wrong"
CHILD_RESPONSE_INTENT_NEAR_MISS = "near_miss"
CHILD_RESPONSE_INTENT_HELP_OR_REPEAT = "help_or_repeat"
CHILD_RESPONSE_INTENT_UNKNOWN_OR_FRUSTRATED = "unknown_or_frustrated"
CHILD_RESPONSE_INTENT_VIETNAMESE_OBJECT = "vietnamese_object"
CHILD_RESPONSE_INTENT_ALREADY_IN_LESSON = "already_in_lesson"

# Vietnamese L1 labels that map to the current teaching object. Safe to speak back
# because they name the concept, not free-form child speech.
_VIETNAMESE_OBJECT_LABELS = ("cái kho", "nhà kho")

def _normalized_detail_key(key: Any) -> str:
    return str(key).replace("-", "_").lower()

def _strip_immediate_scoring_detail(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _strip_immediate_scoring_detail(child)
            for key, child in value.items()
            if _normalized_detail_key(key) not in IMMEDIATE_SCORING_DETAIL_KEYS
        }
    if isinstance(value, list):
        return [_strip_immediate_scoring_detail(child) for child in value]
    return value

def _normalized_child_response_value(value: Any) -> str:
    return str(value or "").strip().lower().strip(".,;:!?")

def _has_observable_child_response_value(value: Any) -> bool:
    if value in (None, "", []):
        return False
    normalized = _normalized_child_response_value(value)
    return normalized not in EMPTY_CHILD_RESPONSE_VALUES and any(char.isalnum() for char in normalized)

def _matching_tokens(value: Any) -> List[str]:
    normalized = unicodedata.normalize("NFKD", str(value or "").lower())
    # The Vietnamese letter đ/Đ (U+0111/U+0110) has no combining-mark decomposition,
    # so NFKD does NOT fold it to 'd'. Without this map an accent-stripped STT
    # transcript ("bat dau bai hoc", "doc lai") misses every marker phrase that the
    # accented form matches. GoogleLiveProvider._normalize_intent_text already folds
    # it for the lesson *trigger*; the in-lesson *answer* classifier must agree.
    normalized = normalized.replace("đ", "d").replace("Đ", "d")
    tokens: List[str] = []
    current: List[str] = []
    for char in normalized:
        if unicodedata.combining(char):
            continue
        if char.isalnum():
            current.append(char)
        elif current:
            tokens.append("".join(current))
            current = []
    if current:
        tokens.append("".join(current))
    return tokens

def _contains_token_sequence(tokens: List[str], expected: List[str]) -> bool:
    if not expected or len(expected) > len(tokens):
        return False
    for index in range(0, len(tokens) - len(expected) + 1):
        if tokens[index:index + len(expected)] == expected:
            return True
    return False

def _contains_any_token_sequence(tokens: List[str], expected_values) -> bool:
    return any(_contains_token_sequence(tokens, _matching_tokens(value)) for value in expected_values)

def _edit_distance_at_most(left: str, right: str, limit: int) -> bool:
    if abs(len(left) - len(right)) > limit:
        return False
    previous = list(range(len(right) + 1))
    for left_index, left_char in enumerate(left, 1):
        current = [left_index]
        row_min = current[0]
        for right_index, right_char in enumerate(right, 1):
            cost = 0 if left_char == right_char else 1
            value = min(
                previous[right_index] + 1,
                current[right_index - 1] + 1,
                previous[right_index - 1] + cost,
            )
            current.append(value)
            row_min = min(row_min, value)
        if row_min > limit:
            return False
        previous = current
    return previous[-1] <= limit

def _pronunciation_token_within_distance(
    token: str, expected: str, max_distance: int
) -> bool:
    """Same first letter + short-word band; used for accept vs near-miss coaching."""
    if not token or not expected or token[0] != expected[0]:
        return False
    if len(expected) < 3 or len(expected) > 8:
        return False
    return _edit_distance_at_most(token, expected, max_distance)

def _near_child_pronunciation_token(token: str, expected: str) -> bool:
    return _pronunciation_token_within_distance(token, expected, 1)

def _near_miss_child_pronunciation_token(token: str, expected: str) -> bool:
    """Close attempt that is not close enough to accept — coach, do not advance."""
    return (
        _pronunciation_token_within_distance(token, expected, 2)
        and not _near_child_pronunciation_token(token, expected)
    )

def _target_vocab_word(expected_responses: List[str], step: Optional[Dict[str, Any]] = None) -> str:
    if expected_responses:
        return str(expected_responses[0]).strip() or "the word"
    if isinstance(step, dict):
        vocab = step.get("vocab")
        if isinstance(vocab, dict):
            word = vocab.get("targetWord") or vocab.get("word") or vocab.get("expectedResponse")
            if word not in (None, ""):
                return str(word).strip()
        scene = step.get("scene") if isinstance(step.get("scene"), dict) else {}
        teaching = scene.get("teachingObject") if isinstance(scene, dict) else {}
        if isinstance(teaching, dict):
            primary = teaching.get("primaryWord")
            if primary not in (None, ""):
                return str(primary).strip()
    return "the word"

def _classify_child_response_intent(
    response: Any,
    expected_responses: List[str],
) -> str:
    if _child_response_matches_expected(response, expected_responses):
        return CHILD_RESPONSE_INTENT_CORRECT
    tokens = _matching_tokens(response)
    if not tokens:
        return CHILD_RESPONSE_INTENT_WRONG
    if _contains_any_token_sequence(
        tokens,
        [
            "bắt đầu bài học",
            "bắt đầu khóa học",
            "mở bài học",
            "mở khóa học",
            "vào bài học",
            "vào khóa học",
            "start lesson",
            "start the lesson",
        ],
    ):
        return CHILD_RESPONSE_INTENT_ALREADY_IN_LESSON
    if _contains_any_token_sequence(
        tokens,
        [
            "nói lại",
            "nhắc lại",
            "lặp lại",
            "đọc lại",
            "chưa nghe",
            "nghe lại",
            "giúp con",
            "giúp",
            "repeat",
            "help",
        ],
    ):
        return CHILD_RESPONSE_INTENT_HELP_OR_REPEAT
    if (
        _contains_any_token_sequence(tokens, _VIETNAMESE_OBJECT_LABELS)
        or (
            "kho" in tokens
            and not _contains_any_token_sequence(tokens, ["khó quá", "kho qua"])
        )
    ):
        return CHILD_RESPONSE_INTENT_VIETNAMESE_OBJECT
    if _contains_any_token_sequence(
        tokens,
        [
            "không biết",
            "con không biết",
            "khó quá",
            "kho qua",
            "không làm được",
            "con không làm được",
            "chịu",
            "con chịu",
        ],
    ):
        return CHILD_RESPONSE_INTENT_UNKNOWN_OR_FRUSTRATED
    if _is_near_miss_child_response(response, expected_responses):
        return CHILD_RESPONSE_INTENT_NEAR_MISS
    return CHILD_RESPONSE_INTENT_WRONG

def _coerce_expected_child_responses(step: Optional[Dict[str, Any]]) -> List[str]:
    if not isinstance(step, dict):
        return []
    values: List[Any] = []
    for key in EXPECTED_CHILD_RESPONSE_KEYS:
        raw = step.get(key)
        if isinstance(raw, list):
            values.extend(raw)
        elif raw not in (None, ""):
            values.append(raw)
    vocab = step.get("vocab")
    if isinstance(vocab, dict):
        raw_word = vocab.get("expectedResponse") or vocab.get("targetWord") or vocab.get("word")
        if raw_word not in (None, ""):
            values.append(raw_word)
    expected: List[str] = []
    seen = set()
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        key = " ".join(_matching_tokens(text))
        if not key or key in seen:
            continue
        seen.add(key)
        expected.append(text)
    return expected

def _child_response_matches_expected(response: Any, expected_responses: List[str]) -> bool:
    if not expected_responses:
        return True
    response_tokens = _matching_tokens(response)
    if not response_tokens:
        return False
    token_aliases = {
        "barn": {"bang", "bon", "bong", "darn", "nong"},
    }
    for expected in expected_responses:
        expected_tokens = _matching_tokens(expected)
        if _contains_token_sequence(response_tokens, expected_tokens):
            return True
        if len(expected_tokens) == 1:
            expected_token = expected_tokens[0]
            aliases = token_aliases.get(expected_token, set())
            if aliases and any(token in aliases for token in response_tokens):
                return True
            if any(
                _near_child_pronunciation_token(token, expected_token)
                for token in response_tokens
            ):
                return True
    return False

def _is_near_miss_child_response(response: Any, expected_responses: List[str]) -> bool:
    if not expected_responses:
        return False
    response_tokens = _matching_tokens(response)
    if not response_tokens:
        return False
    for expected in expected_responses:
        expected_tokens = _matching_tokens(expected)
        if len(expected_tokens) != 1:
            continue
        expected_token = expected_tokens[0]
        if any(
            _near_miss_child_pronunciation_token(token, expected_token)
            for token in response_tokens
        ):
            return True
    return False

def _child_response_coaching_prompt(
    step: Dict[str, Any],
    expected_responses: List[str],
    response: Any,
    intent: str,
) -> str:
    """Short adaptive coaching from child intent; never raw-echo free-form speech."""
    target = _target_vocab_word(expected_responses, step)

    if intent == CHILD_RESPONSE_INTENT_HELP_OR_REPEAT:
        return f"Mình nhắc lại nhé. Từ mới là {target}. Nói theo mình: {target}."

    if intent == CHILD_RESPONSE_INTENT_UNKNOWN_OR_FRUSTRATED:
        return f"Không sao. Nhìn hình, tiếng Anh là {target}. Thử nói: {target}."

    if intent == CHILD_RESPONSE_INTENT_VIETNAMESE_OBJECT:
        return f"Đúng, cái kho! Tiếng Anh là {target}. Con nói: {target}."

    if intent == CHILD_RESPONSE_INTENT_ALREADY_IN_LESSON:
        return f"Mình đang học {target} rồi. Con nói {target} nhé."

    if intent == CHILD_RESPONSE_INTENT_NEAR_MISS:
        return f"Gần đúng lắm! Nói chậm, rõ: {target}."

    return f"Mình nghe rồi. Từ mình học là {target}. Nói chậm: {target}."

def _child_response_success_prompt(
    step: Dict[str, Any],
    expected_responses: Optional[List[str]] = None,
) -> Optional[str]:
    """Authored successPrompt wins for ceremony; else short adaptive cheer."""
    success = step.get("successPrompt")
    if isinstance(success, str) and success.strip():
        return success.strip()
    target = _target_vocab_word(list(expected_responses or []), step)
    if target and target != "the word":
        return f"Đúng rồi! {target}!"
    return "Giỏi lắm!"

def _is_false_child_response_flag_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value is False
    if isinstance(value, (int, float)):
        return math.isfinite(float(value)) and float(value) == 0.0
    normalized = str(value).strip().lower().strip(".,;:!?")
    if normalized == "false":
        return True
    try:
        numeric = float(normalized)
    except (TypeError, ValueError):
        return False
    return math.isfinite(numeric) and numeric == 0.0

def _has_negative_child_response_flag(detail: Dict[str, Any]) -> bool:
    return any(
        _is_false_child_response_flag_value(detail.get(key))
        for key in CHILD_RESPONSE_FLAG_KEYS
        if key in detail
    )

def _has_invalid_child_response_confidence(detail: Dict[str, Any]) -> bool:
    for key in CHILD_RESPONSE_CONFIDENCE_KEYS:
        if key not in detail:
            continue
        try:
            confidence = float(detail.get(key))
        except (TypeError, ValueError):
            return True
        if not math.isfinite(confidence) or confidence <= 0.0:
            return True
    return False


def _spoken_step_prompt(step: Dict[str, Any]) -> Optional[str]:
    story_beat = step.get("storyBeat")
    vocab = step.get("vocab")
    uses_guided_ask = (
        isinstance(story_beat, dict)
        and (
            story_beat.get("waitForChild") is True
            or step.get("completionClass") == "interactive"
            or (isinstance(vocab, dict) and vocab.get("promptKind") == "guided-speaking")
        )
    )
    if uses_guided_ask:
        ask = story_beat.get("ask")
        if isinstance(ask, str) and ask.strip():
            return ask.strip()

    interaction = step.get("interaction")
    if isinstance(interaction, dict) and interaction.get("template") == "safeSpeaking":
        return fun_pattern_prompt(
            interaction.get("funPattern"),
            _target_vocab_word(_coerce_expected_child_responses(step), step),
        )
    if uses_guided_ask:
        return "What do you see?"

    prompt = step.get("prompt")
    if isinstance(prompt, str) and prompt.strip():
        return prompt.strip()
    return None

def _is_passive_step(step: Optional[Dict[str, Any]]) -> bool:
    """True iff the step is a passive narration step (no child interaction, so no
    firmware step_completed) and therefore AUTO-ADVANCES on its ack.

    Classification order:
      1) explicit ``completionClass`` ('passive'|'interactive') — authoritative;
      2) fallback to ``PASSIVE_STEP_TYPES`` membership when completionClass absent.
    """
    if not step:
        return False
    completion_class = step.get("completionClass")
    if completion_class == "passive":
        return True
    if completion_class == "interactive":
        return False
    # Backward-compat: no completionClass -> v1 builtin type-set fallback.
    return step.get("type") in PASSIVE_STEP_TYPES


def _wire_timestamp() -> int:
    """Epoch milliseconds (plan §5.2: wire timestamp is epoch ms, never RFC3339)."""
    return int(time.time() * 1000)


def _coerce_ack_seq(acked: Any) -> Optional[int]:
    """Coerce an inbound ``body.acks`` to the int S->F sequence used as the
    ``_outstanding`` dict key, or ``None`` if it is not a well-formed sequence.

    Tolerates an ``int`` (canonical), a numeric ``str`` ("3"), or a ``bool`` (an int
    subclass — explicitly rejected since True/False are never a real sequence). Any
    unhashable/non-numeric value (list, dict, None, "abc") -> ``None`` so the caller
    treats it as a malformed/stale ack and no-ops, instead of raising TypeError into
    the dict ``.pop()`` (which would otherwise tear down the connection + voice)."""
    if isinstance(acked, bool):
        return None
    if isinstance(acked, int):
        return acked
    if isinstance(acked, str):
        try:
            return int(acked.strip())
        except (ValueError, TypeError):
            return None
    return None


def parse_manifest_checksum(etag: Optional[str]) -> str:
    """ETag is ``"lesson-<version>-<profile>-<checksum>"`` (backend etagFor)."""
    if not etag:
        return ""
    parts = etag.strip().strip('"').split("-")
    return parts[-1] if len(parts) >= 4 else ""

def _positive_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str):
        try:
            parsed = int(value.strip())
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None
    return None


def _bounded_non_negative_number(value: Any, maximum: int = 0xFFFFFFFF) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or value < 0 or value > maximum:
        return None
    return value


def _operations_motion_result(value: Any) -> Optional[str]:
    if value in {"success", "failed", "skipped"}:
        return value
    return None

def _finite_float_or_default(value: Any, default: float) -> float:
    if isinstance(value, bool):
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default

def _int_or_default(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default

def _positive_float_or_default(value: Any, default: float) -> float:
    parsed = _finite_float_or_default(value, default)
    return parsed if parsed > 0 else default

def _positive_int_or_default(value: Any, default: int) -> int:
    parsed = _int_or_default(value, default)
    return parsed if parsed > 0 else default

def _lesson_config(config: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(config, dict):
        return {}
    lesson_cfg = config.get("lesson", {}) or {}
    return lesson_cfg if isinstance(lesson_cfg, dict) else {}

def _server_config(config: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(config, dict):
        return {}
    server_cfg = config.get("server", {}) or {}
    return server_cfg if isinstance(server_cfg, dict) else {}

def _assignment_metadata_errors(assignment: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    for key in ("assignmentId", "lessonId", "profile", "manifestChecksum"):
        if not isinstance(assignment.get(key), str) or not assignment.get(key).strip():
            errors.append(key)
    for key in ("assignmentVersion", "lessonVersion"):
        if _positive_int(assignment.get(key)) is None:
            errors.append(key)
    return errors

def _manifest_identity_errors(assignment: Dict[str, Any], manifest: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    for key in ("lessonId", "profile"):
        value = manifest.get(key)
        if not isinstance(value, str) or not value.strip() or value != assignment.get(key):
            errors.append(key)
    manifest_version = _positive_int(manifest.get("lessonVersion"))
    assignment_version = _positive_int(assignment.get("lessonVersion"))
    if manifest_version is None or assignment_version is None or manifest_version != assignment_version:
        errors.append("lessonVersion")
    return errors

def lesson_asset_public_base_url(config: Dict[str, Any]) -> str:
    lesson_cfg = _lesson_config(config)
    server_cfg = _server_config(config)
    explicit = (
        lesson_cfg.get("asset_public_base_url")
        or lesson_cfg.get("asset_public_base")
        or server_cfg.get("asset_public_base_url")
    )
    if explicit:
        return str(explicit).rstrip("/")
    if not server_cfg:
        return ""
    vision_url = get_vision_url(config)
    if vision_url and "/mcp/vision/explain" in vision_url:
        return vision_url.replace("/mcp/vision/explain", "").rstrip("/")
    return ""


def _renderer_v2_request_enabled(conn: Any, renderer_capabilities: List[str]) -> bool:
    config = getattr(conn, "config", {}) or {}
    lesson_cfg = _lesson_config(config)
    allowlist = lesson_cfg.get("rollout_device_allowlist") or []
    device_id = str(getattr(conn, "device_id", "") or "").strip().lower()
    return (
        lesson_cfg.get("renderer_v2_enabled") is True
        and RENDERER_V2 in renderer_capabilities
        and len(allowlist) == 1
        and device_id == str(allowlist[0]).strip().lower()
    )


def _renderer_v3_request_enabled(conn: Any, renderer_capabilities: List[str]) -> bool:
    config = getattr(conn, "config", {}) or {}
    lesson_cfg = _lesson_config(config)
    features = getattr(conn, "features", None)
    detail = features.get("lessonRendererV3") if isinstance(features, dict) else None
    allowlist = lesson_cfg.get("rollout_device_allowlist") or []
    device_id = str(getattr(conn, "device_id", "") or "").strip().lower()
    return (
        lesson_cfg.get("renderer_v3_enabled") is True
        and RENDERER_V3 in renderer_capabilities
        and isinstance(detail, dict)
        and detail.get("directMp4Cinematic") is True
        and detail.get("sdAssetPack") is True
        and len(allowlist) == 1
        and device_id == str(allowlist[0]).strip().lower()
    )


def _renderer_v4_request_enabled(conn: Any, renderer_capabilities: List[str]) -> bool:
    config = getattr(conn, "config", {}) or {}
    lesson_cfg = _lesson_config(config)
    features = getattr(conn, "features", None)
    detail = features.get("lessonRendererV4") if isinstance(features, dict) else None
    allowlist = lesson_cfg.get("rollout_device_allowlist") or []
    device_id = str(getattr(conn, "device_id", "") or "").strip().lower()
    return (
        lesson_cfg.get("renderer_v4_enabled") is True
        and RENDERER_V4 in renderer_capabilities
        and isinstance(detail, dict)
        and detail.get("flattenedMjpegCinematic") is True
        and detail.get("sdAssetPack") is True
        and len(allowlist) == 1
        and device_id == str(allowlist[0]).strip().lower()
    )


def _renderer_v5_request_enabled(conn: Any, renderer_capabilities: List[str]) -> bool:
    config = getattr(conn, "config", {}) or {}
    lesson_cfg = _lesson_config(config)
    features = getattr(conn, "features", None)
    detail = features.get("lessonRendererV5") if isinstance(features, dict) else None
    allowlist = lesson_cfg.get("rollout_device_allowlist") or []
    device_id = str(getattr(conn, "device_id", "") or "").strip().lower()
    return (
        lesson_cfg.get("renderer_v5_enabled") is True
        and RENDERER_V5 in renderer_capabilities
        and isinstance(detail, dict)
        and detail.get("layeredCinematic") is True
        and detail.get("sdAssetPack") is True
        and len(allowlist) == 1
        and device_id == str(allowlist[0]).strip().lower()
    )


def _requested_renderer_capabilities(
    advertised: List[str],
    *,
    renderer_v2_enabled: bool,
    renderer_v3_enabled: bool,
    renderer_v4_enabled: bool,
    renderer_v5_enabled: bool = False,
) -> List[str]:
    enabled = (
        (RENDERER_V5, renderer_v5_enabled),
        (RENDERER_V4, renderer_v4_enabled),
        (RENDERER_V3, renderer_v3_enabled),
        (RENDERER_V2, renderer_v2_enabled),
    )
    advertised_set = set(advertised)
    requested = [
        renderer
        for renderer, rollout_enabled in enabled
        if rollout_enabled and renderer in advertised_set
    ]
    if PROTOCOL_VERSION in advertised_set:
        requested.append(PROTOCOL_VERSION)
    return requested or [PROTOCOL_VERSION]


def _manifest_asset_cache_inputs(manifest: Dict[str, Any]) -> List[Dict[str, Any]]:
    assets = [
        {
            "key": asset.get("id") or asset.get("assetId"),
            "path": asset.get("path"),
            "url": asset.get("url"),
            "sha256": asset.get("sha256"),
            "size": asset.get("bytes"),
            "critical": asset.get("critical"),
            "layer": asset.get("layer"),
            "role": asset.get("role"),
            "mediaType": asset.get("mediaType") or asset.get("media_type"),
        }
        for asset in manifest.get("assets", [])
        if isinstance(asset, dict)
    ]
    manifest_version = manifest.get("manifestVersion")
    if manifest_version == RENDERER_V5:
        generic_by_key = {asset["key"]: asset for asset in assets if asset.get("key")}
        layered_by_key: dict[str, dict[str, Any]] = {}
        for phase in manifest.get("cinematicPhases", []):
            if not isinstance(phase, dict):
                continue
            for layer in phase.get("layers", []):
                if not isinstance(layer, dict):
                    continue
                metadata = layer.get("metadata")
                if not isinstance(metadata, dict):
                    continue
                key = layer.get("assetVersionId")
                generic = generic_by_key.get(key, {})
                projected = {
                    "key": key,
                    "path": generic.get("path") or layer.get("path"),
                    "url": generic.get("url") or layer.get("url"),
                    "sha256": layer.get("sha256"),
                    "size": layer.get("bytes"),
                    "critical": True,
                    "layer": layer.get("layer"),
                    "role": phase.get("phaseId"),
                    "mediaType": metadata.get("mediaType"),
                    "sharedAssetKey": layer.get("assetKey"),
                    "sharedAssetVersion": layer.get("version"),
                    "compatibilityMetadata": copy.deepcopy(metadata),
                }
                existing = layered_by_key.get(key)
                if existing is not None:
                    comparable = {
                        field: value
                        for field, value in projected.items()
                        if field != "role"
                    }
                    prior = {
                        field: value
                        for field, value in existing.items()
                        if field != "role"
                    }
                    if comparable != prior:
                        raise ValueError(
                            f"renderer v5 asset {key!r} has conflicting phase identities"
                        )
                    continue
                layered_by_key[key] = projected
        # Phase entries are the canonical renderer-v5 metadata source. Keeping
        # generic duplicates creates critical MP4 shadows without attestation.
        return [
            asset for asset in assets if asset.get("key") not in layered_by_key
        ] + list(layered_by_key.values())
    if manifest_version != RENDERER_V4:
        return assets
    for phase in manifest.get("cinematicPhases", []):
        if not isinstance(phase, dict) or not isinstance(phase.get("asset"), dict):
            continue
        source = phase["asset"]
        metadata = source.get("metadata")
        version = phase.get("templateVersion")
        identity_field = "phaseId" if version == 1 else "cueId" if version == 2 else None
        entry_id = phase.get(identity_field) if identity_field else None
        if not isinstance(metadata, dict) or not isinstance(entry_id, str):
            continue
        common = {
            "key": f"flattenedCinematic.{entry_id}",
            "path": source.get("path"),
            "url": source.get("url"),
            "sha256": source.get("sha256"),
            "size": source.get("bytes"),
            "critical": True,
            "layer": "flattenedCinematic",
            "role": entry_id,
            "mediaType": source.get("mediaType"),
            "derivativeId": source.get("derivativeId"),
        }
        compatibility_metadata = (
            {
                "codec": metadata.get("codec"),
                "width": source.get("width"),
                "height": source.get("height"),
                "fps": metadata.get("fps"),
                "durationMs": metadata.get("durationMs"),
                "frameCount": metadata.get("frameCount"),
                "hasAudio": metadata.get("hasAudio"),
            }
            if version == 1
            else copy.deepcopy(metadata)
        )
        compatibility = {"compatibilityMetadata": compatibility_metadata}
        if version == 1:
            assets.append({**common, "phaseId": entry_id, **compatibility})
            continue
        assets.append({
            **common,
            "cueId": entry_id,
            "effect": phase.get("effect"),
            "stepKey": phase.get("stepKey"),
            "playbackMode": phase.get("playbackMode"),
            **compatibility,
        })
    return assets


class LessonRuntime:
    """Per-device lesson session state, held on ``ConnectionHandler.lesson_runtime``.

    Injected deps (``send``, ``clock``, ``sleep``) keep the §10.2 pytest free of a
    real socket / wall clock.
    """

    def __init__(
        self,
        conn: Any,
        *,
        assignment: Dict[str, Any],
        manifest: Dict[str, Any],
        asset_cache: Any,
        forwarder: Any,
        manifest_checksum: str = "",
        send: Optional[Callable[[str], Awaitable[None]]] = None,
        sleep: Optional[Callable[[float], Awaitable[None]]] = None,
        default_step_timeout_sec: float = 12.0,
        min_step_timeout_sec: float = 0.0,
        alarm: Any = None,
        preload_status_reporter: Optional[Callable[[Dict[str, Any]], Awaitable[Any]]] = None,
        graceful_inactivity_finish: bool = False,
    ) -> None:
        self.conn = conn
        # Demo-only: when a child stays silent through an interactive step, model the
        # answer aloud and advance instead of abandoning with a sad face. Real assigned
        # lessons leave this False so the backend still learns the child disengaged.
        self._graceful_inactivity_finish = bool(graceful_inactivity_finish)
        self.logger = getattr(conn, "logger", None)
        self.assignment_id = assignment.get("assignmentId")
        self.assignment_version = int(assignment.get("assignmentVersion", 1))
        self.lesson_id = assignment.get("lessonId")
        self.lesson_version = int(assignment.get("lessonVersion", 1))
        self.profile = assignment.get("profile", "espTft")
        # A lesson run owns its protocol/event identity. It must not inherit either
        # the conversational websocket session or a historical assignment payload,
        # because a reconnect or republish starts a fresh sequence namespace.
        self.session_id = str(uuid.uuid4())
        self._trace_context = _lesson_trace_context_from_headers(getattr(conn, "headers", None))
        # Record the JOIN between the two identities this run carries. The hello ack
        # handed the device the connection session; the line above deliberately mints a
        # separate lesson identity. Both are correct, but without one line naming the
        # pair nothing downstream can tell that two ids describe one run -- an operator
        # reading a capture, or the E2E gate, sees evidence from "two sessions".
        self._log(
            "info",
            "lesson session bound "
            f"sessionId={self.session_id} "
            f"connectionSessionId={getattr(conn, 'session_id', '') or ''}",
        )
        self.manifest = manifest
        self.manifest_checksum = manifest_checksum
        # L3 P3 — the device's advertised renderer-capability SET (forward-modelled
        # string|list from hello.features.renderer; defaults to the v1-only set for
        # every current firmware). A served manifestVersion MUST be in this set or
        # the start() gate rejects it (LESSON_VERSION_UNSUPPORTED).
        self.renderer_capabilities = device_renderer_capabilities(
            getattr(conn, "features", None)
        )
        # The renderer version actually negotiated/served for this session: the
        # manifest's manifestVersion when present, else the v1 PROTOCOL_VERSION
        # fallback. Stamped into every outbound envelope's protocolVersion. Today
        # (v1 manifest, v1 device) this is identical to PROTOCOL_VERSION.
        self.negotiated_version = manifest.get("manifestVersion") or PROTOCOL_VERSION
        self.asset_cache = asset_cache
        self.forwarder = forwarder
        self.preload_status_reporter = preload_status_reporter
        self._send = send or self._default_send
        self._sleep = sleep or asyncio.sleep
        self._default_step_timeout_sec = default_step_timeout_sec
        try:
            min_timeout = float(min_step_timeout_sec or 0.0)
            self._min_step_timeout_sec = (
                max(0.0, min_timeout) if math.isfinite(min_timeout) else 0.0
            )
        except (TypeError, ValueError):
            self._min_step_timeout_sec = 0.0
        # S13 alarm (plan §11.2 / CP-8): brackets the preload window so the voice
        # round-trip p95 is measured "during an active preload". Optional + best-effort
        # — a missing alarm or a raising hook never affects the lesson run.
        self._alarm = alarm

        self._seq = 0  # S->F monotonic counter; first emitted frame is sequence 1.
        self._outstanding: Dict[int, Dict[str, Any]] = {}  # S->F seq -> {type, stepId}
        self._last_inbound_sequence = 0  # F->S gap detector
        self.state = S_IDLE
        self.last_error: Optional[LessonError] = None

        self._preload_task: Optional[asyncio.Task] = None
        self._preload_status_report_tasks: set = set()
        self._frame_ack_timeout_task: Optional[asyncio.Task] = None
        self._frame_ack_timeout_sequence: int | None = None
        self._frame_ack_retry_task: asyncio.Task | None = None
        self._frame_ack_retry_command_sequence: int | None = None
        self._visual_ack_waiters: Dict[int, asyncio.Future] = {}
        self._visual_ack_timeout_tasks: Dict[int, asyncio.Task] = {}
        self._retired_visual_ack_sequences: Dict[int, Dict[str, Any]] = {}
        self._retired_conversation_ack_sequences: dict[int, dict[str, Any]] = {}
        self._visual_generation = 1
        self._current_visual_request: Optional[Dict[str, Any]] = None
        self._visual_transition_task: Optional[asyncio.Task] = None
        self._dispatched_visual_motions: set[tuple[Any, ...]] = set()
        self._step_timeout_task: Optional[asyncio.Task] = None
        self._passive_dwell_task: Optional[asyncio.Task] = None
        self._child_response_timeout_task: Optional[asyncio.Task] = None
        self._child_response_timeout_count = 0
        self._safe_speaking_session: Optional[SafeSpeakingSession] = None
        self._motion_task: Optional[asyncio.Task] = None
        self._motion_generation = 0
        self._semantic_step_sequence = 0
        self._step_seq: Optional[int] = None
        # Resolved timeoutSec of the step currently on the wire; re-armed on resume.
        self._step_timeout_sec: float = float(default_step_timeout_sec)
        self._step_id: Optional[str] = None
        self._step: Optional[Dict[str, Any]] = None  # the in-flight step row
        self._step_passive = False  # cached _is_passive_step(self._step)
        self._step_acked = False
        self._step_visuals_ready = False
        self._step_completed = False
        self._completed_step_ids: set[str] = set()
        self._child_response_window_open = False
        self._closed = False
        # Guards the single durable lesson_failed forward so re-entrant FAILED
        # transitions (e.g. a late lesson_error after an earlier timeout) cannot
        # enqueue a second terminal event for the same run.
        self._failure_forwarded = False
        self._completion_stop_sent = False
        self._completion_visual_pending = False
        self._sd_asset_pack_online_fallback = False
        self._cinematic_phase: Optional[Dict[str, Any]] = None
        self._cinematic_stop_sent = False
        self._cinematic_cancel_sent = False
        self._cinematic_pending_command: Optional[Dict[str, Any]] = None
        self._cinematic_deferred_step_ack: Optional[Dict[str, Any]] = None
        self._conversation_cues: dict[str, dict[str, Any]] = {}
        self._cinematic_cues_by_key: dict[tuple[str, str], dict[str, Any]] = {}
        self._layered_cinematic_phases: dict[str, dict[str, Any]] = {}
        self._layered_cinematic_step_phases: dict[str, dict[str, Any]] = {}
        self._authored_cinematic_pending: dict[str, Any] | None = None
        self._conversation_contract_valid = False
        self._conversation_attempt_serial = 0
        self._conversation_pending_visual: dict[str, Any] | None = None
        self._conversation_visual_ack: tuple[str, str] | None = None
        self._conversation_progress_forwarded: set[str] = set()
        self._conversation_started_at: float | None = None
        self._conversation_fallback_window_id: str | None = None
        self._conversation_fallback_turn_sequence_id: int | None = None
        self._conversation_fallback_ack_future: asyncio.Future[bool] | None = None
        self._conversation_fallback_ack_sequence: int | None = None
        self._conversation_fallback_ack_cue_id: str | None = None
        self._conversation_fallback_ack_attempt_id: str | None = None
        self._conversation_fallback_ack_expired = False
        self._conversation_fallback_prompt_authorization: str | None = None
        self._conversation_fallback_prompt_claimed = False
        self._clock = time.monotonic
        self.conversation: LessonConversationRuntime | None = None
        # Live's own ASR transcript for the child's current speaking turn, held only
        # long enough to corroborate a model-asserted pronunciation "correct" against
        # the contract's known-safe target word/meanings — never logged or forwarded.
        self._conversation_pending_recognized_text: str | None = None

        # P5 multi-step playback: the ordered renderable manifest steps + a cursor.
        # The slice ran ONE step; P5 advances through ALL of them in manifest order,
        # one lesson_step per step, each gated on render ack plus either passive
        # auto-advance or interactive child response evidence.
        self._steps: List[Dict[str, Any]] = self._select_steps()
        self._step_index = -1  # bumped to 0 by the first _emit_step()
        self._steps_completed = 0  # real count for lesson_completed.summary
        self._parent_phase_sequence = -2_000_000
        self._preparing_phase_forwarded = False

    # ── lifecycle ──────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Preload/attest first, then publish the first protocol frame."""
        if await self.preload_only():
            await self.start_protocol(preloaded=True)

    async def preload_only(self) -> bool:
        """Validate and materialize assets without sending ``lesson_prepare``."""
        features = getattr(self.conn, "features", None)
        manifest_version = self.manifest.get("manifestVersion")
        if manifest_version in {RENDERER_V3, RENDERER_V4, RENDERER_V5} and not self._cinematic_enabled():
            self.last_error = LessonError(
                CINEMATIC_CAPABILITY_UNSUPPORTED,
                "device did not advertise the exact cinematic renderer capability",
            )
            raise self.last_error
        if manifest_version not in {RENDERER_V3, RENDERER_V4, RENDERER_V5} and not lesson_capability_ok(
            features, renderer_v2_enabled=self._renderer_v2_rollout_enabled()
        ):
            # D-CAP-FLAG: absence = no support; MUST NOT send lesson_prepare.
            self.last_error = LessonError(
                LESSON_VERSION_UNSUPPORTED, "device did not advertise lesson capability"
            )
            raise self.last_error
        # L3 P3 negotiated-version gate: the served manifestVersion MUST be a version
        # the DEVICE advertised it can render. For every current device this set is
        # exactly {teebot-lesson-renderer.v1}, so a v1 manifest passes and a v2
        # manifest served to a v1-only device is rejected here (LESSON_VERSION_UNSUPPORTED,
        # no crash) — the structural guard that survives a future v2 renderer. Net
        # effect today is identical to the old ``!= PROTOCOL_VERSION`` check.
        if manifest_version not in self.renderer_capabilities or (
            manifest_version == RENDERER_V2 and not self._renderer_v2_rollout_enabled()
        ) or (
            manifest_version == RENDERER_V3 and not self._renderer_v3_enabled()
        ) or (
            manifest_version == RENDERER_V4 and not self._renderer_v4_enabled()
        ) or (
            manifest_version == RENDERER_V5 and not self._renderer_v5_enabled()
        ):
            self.last_error = LessonError(
                LESSON_VERSION_UNSUPPORTED, f"unsupported manifestVersion {manifest_version!r}"
            )
            raise self.last_error
        # Gate passed -> the negotiated version is the served version (validated above).
        self.negotiated_version = manifest_version
        # DEVICE-RENDERER profile gate: a published lesson with a non-espTft profile
        # (piTft/mobile) can be accepted upstream, but espTft-only firmware renders a
        # non-espTft lesson_prepare BLANK. The backend has no device-renderer model, so
        # the gate lives HERE where both the device AND the assignment's profile are
        # known. CONFIG-DRIVEN: a future piTft/mobile firmware just adds its profile to
        # config lesson.supported_profiles (do NOT hardcode espTft). Default ['espTft'].
        # Mirrors the capability/manifestVersion gates above -> caller logs, NO frame on
        # the wire, the lesson is skipped instead of rendering blank.
        config = getattr(self.conn, "config", {}) or {}
        supported = _lesson_config(config).get("supported_profiles") or ["espTft"]
        if self.profile not in supported:
            self.last_error = LessonError(
                LESSON_VERSION_UNSUPPORTED,
                f"profile {self.profile!r} not renderable by this device (supported={supported})",
            )
            raise self.last_error
        # Profile reject (forced full-video espTft backgroundScene) BEFORE prepare.
        self.asset_cache.assert_profile_renderable()
        if not self._steps:
            self.last_error = LessonError("LESSON_STEP_MISSING", "no renderable step in manifest")
            raise self.last_error

        if manifest_version == RENDERER_V4:
            try:
                validate_flattened_cinematic_manifest(self.manifest)
            except FlattenedCinematicContractError as exc:
                self.last_error = LessonError(exc.code, exc.message, retryable=False)
                raise self.last_error from exc
            self._conversation_contract_valid = self._validate_conversation_contracts()
            if self.manifest.get("conversation") is not None and not self._conversation_contract_valid:
                self.last_error = LessonError(
                    "LESSON_CONVERSATION_CONTRACT_INVALID",
                    "conversation steps do not exactly match the lesson runtime order",
                    retryable=False,
                )
                raise self.last_error

        self.state = S_PRELOADING
        if not self._preparing_phase_forwarded:
            self._preparing_phase_forwarded = True
            self._forward_phase("preparing")
        if self._use_sd_asset_pack():
            ready = await self._preload_sd_asset_pack_before_prepare()
            if not ready:
                return False
        if manifest_version in {RENDERER_V3, RENDERER_V4, RENDERER_V5}:
            try:
                if manifest_version == RENDERER_V4:
                    validate_flattened_cinematic_manifest(self.manifest)
                phases = self.manifest.get("cinematicPhases")
                if not isinstance(phases, list) or not phases:
                    raise CinematicContractError(
                        "CINEMATIC_METADATA_MISMATCH", "cinematic manifest has no phases"
                    )
                pack_builder = getattr(self.asset_cache, "asset_pack_manifest", None)
                if not callable(pack_builder):
                    raise CinematicContractError(
                        "CINEMATIC_PACK_NOT_READY", "cinematic SD pack is unavailable"
                    )
                pack = pack_builder(
                    assignment_version=self.assignment_version,
                    lesson_id=self.lesson_id,
                    lesson_version=self.lesson_version,
                    manifest_checksum=self.manifest_checksum,
                )
                if manifest_version == RENDERER_V3:
                    self._cinematic_phase = project_cinematic_phase(phases[0], pack)
                elif manifest_version == RENDERER_V5:
                    projected = [
                        project_layered_cinematic_phase(phase, pack) for phase in phases
                    ]
                    self._layered_cinematic_phases = {
                        phase["phaseId"]: phase for phase in projected
                    }
                    projected_by_asset = {
                        source["layers"][2]["assetVersionId"]: target
                        for source, target in zip(phases, projected)
                    }
                    self._layered_cinematic_step_phases = {}
                    for asset in self.manifest.get("assets", []) or []:
                        if not isinstance(asset, dict):
                            continue
                        asset_id = asset.get("id") or asset.get("assetId") or asset.get("key")
                        target = projected_by_asset.get(asset_id)
                        if target is None:
                            continue
                        for ref in asset.get("visualRefs", []) or []:
                            if (
                                isinstance(ref, dict)
                                and ref.get("slot") == "robotOverlay"
                                and isinstance(ref.get("stepKey"), str)
                            ):
                                self._layered_cinematic_step_phases[ref["stepKey"]] = target
                    self._cinematic_phase = self._layered_cinematic_phases.get(
                        "flyIn", projected[0]
                    )
                else:
                    projected = [
                        project_flattened_cinematic_phase(phase, pack) for phase in phases
                    ]
                    self._cinematic_cues_by_key = {
                        (cue["stepKey"], cue["effect"]): cue
                        for cue in projected
                        if cue.get("templateVersion") == 2
                        and isinstance(cue.get("stepKey"), str)
                        and isinstance(cue.get("effect"), str)
                    }
                    first_step_key = (
                        self._steps[0].get("id")
                        if self._steps and isinstance(self._steps[0], dict)
                        else None
                    )
                    opening = next(
                        (
                            cue
                            for cue in projected
                            if cue.get("effect") == "opening"
                            and cue.get("stepKey") == first_step_key
                        ),
                        next(
                            (cue for cue in projected if cue.get("effect") == "opening"),
                            projected[0],
                        ),
                    )
                    self._cinematic_phase = opening
                    self._conversation_cues = {
                        cue["cueId"]: cue
                        for cue in projected
                        if cue.get("templateVersion") == 2
                        and isinstance(cue.get("cueId"), str)
                    }
                    self._validate_safe_speaking_cinematic_routes()
            except (
                CinematicContractError,
                FlattenedCinematicContractError,
                LayeredCinematicContractError,
            ) as exc:
                self.last_error = LessonError(exc.code, exc.message, retryable=False)
                raise self.last_error
        return True

    async def start_protocol(self, *, preloaded: bool = False) -> None:
        if not preloaded and not await self.preload_only():
            return
        await self._emit("lesson_prepare", body=self._prepare_body())

    def _teardown_disposition(self):
        """T2.5 — classify this teardown in RMA terms (see failure-path-matrix.md).

        ``restock``   the run reached a terminal state and its ledger is closed;
                      nothing downstream needs repair.
        ``refurbish`` the run had not started yet — assignment state is still
                      recoverable by re-pulling on the next connect.
        ``scrap``     the run died mid-flight: this connection's session is gone
                      while the backend assignment is still non-terminal. This is
                      the case that becomes stale state in production, and the
                      only reason it is worth counting.
        """
        from core.lesson.liveness_lease import Disposition

        state = self.state
        if state in (S_COMPLETED, S_FAILED):
            return Disposition.RESTOCK, f"terminal_{str(state).lower()}"
        if state in (S_IDLE, S_PRELOADING, S_READY):
            return Disposition.REFURBISH, f"closed_before_start_{str(state).lower()}"
        return Disposition.SCRAP, f"closed_mid_flight_{str(state).lower()}"

    def _emit_teardown_disposition(self) -> None:
        from core.lesson.liveness_lease import emit_disposition

        try:
            disposition, reason = self._teardown_disposition()
            lease = getattr(self.conn, "liveness_lease", None)
            emit_disposition(
                self.logger,
                disposition=disposition,
                reason=reason,
                device_id=str(getattr(self.conn, "device_id", "") or ""),
                assignment_id=str(self.assignment_id or ""),
                session_id=str(self.session_id or ""),
                session_epoch=getattr(lease, "session_epoch", None),
                extra={"runtimeState": self.state},
            )
        except Exception:  # pragma: no cover - telemetry must never break teardown
            pass

    async def close(self) -> None:
        self._closed = True
        self._emit_teardown_disposition()
        self._clear_conversation_fallback_ack()
        self._cancel_visual_waiters(increment_generation=True, reason="runtimeClosed")
        visual_transition_task = self._visual_transition_task
        self._visual_transition_task = None
        if visual_transition_task is not None and not visual_transition_task.done():
            visual_transition_task.cancel()
        self._retire_conversation_visual()
        self._cancel_frame_ack_retry()
        self._cancel_frame_ack_timeout()
        self._retired_conversation_ack_sequences.clear()
        self._cancel_step_timeout()
        self._cancel_passive_dwell()
        self._cancel_child_response_timeout()
        self._cinematic_deferred_step_ack = None
        if self._preload_task is not None and not self._preload_task.done():
            self._preload_task.cancel()
        for task in list(self._preload_status_report_tasks):
            if not task.done():
                task.cancel()
        self._motion_generation += 1
        motion_task = self._motion_task
        self._motion_task = None
        if motion_task is not None and not motion_task.done():
            motion_task.cancel()
        if self._preload_status_report_tasks:
            await asyncio.gather(*self._preload_status_report_tasks, return_exceptions=True)
            self._preload_status_report_tasks.clear()
        if motion_task is not None:
            await asyncio.gather(motion_task, return_exceptions=True)
        if visual_transition_task is not None:
            await asyncio.gather(visual_transition_task, return_exceptions=True)
        if self.forwarder is not None:
            await self.forwarder.aclose()
        if self.asset_cache is not None:
            await self.asset_cache.aclose()

    def _is_active_runtime(self) -> bool:
        if self._closed:
            return False
        current = getattr(self.conn, "lesson_runtime", None)
        candidate = getattr(self.conn, "lesson_runtime_candidate", None)
        return current is None or current is self or candidate is self

    def _is_pre_activation_fallback_candidate(self) -> bool:
        """True while a replacement is preloading behind a live fallback runtime."""
        current = getattr(self.conn, "lesson_runtime", None)
        candidate = getattr(self.conn, "lesson_runtime_candidate", None)
        return candidate is self and current is not None and current is not self

    def _renderer_v2_rollout_enabled(self) -> bool:
        config = getattr(self.conn, "config", {}) or {}
        lesson_cfg = _lesson_config(config)
        if lesson_cfg.get("renderer_v2_enabled") is not True:
            return False
        allowlist = lesson_cfg.get("rollout_device_allowlist") or []
        if len(allowlist) != 1:
            return False
        device_id = str(getattr(self.conn, "device_id", "") or "").strip().lower()
        return bool(device_id and device_id == str(allowlist[0]).strip().lower())

    def _renderer_v2_enabled(self) -> bool:
        return (
            self._renderer_v2_rollout_enabled()
            and RENDERER_V2 in self.renderer_capabilities
            and self.negotiated_version == RENDERER_V2
        )

    def _renderer_v3_rollout_enabled(self) -> bool:
        config = getattr(self.conn, "config", {}) or {}
        lesson_cfg = _lesson_config(config)
        if lesson_cfg.get("renderer_v3_enabled") is not True:
            return False
        allowlist = lesson_cfg.get("rollout_device_allowlist") or []
        if len(allowlist) != 1:
            return False
        device_id = str(getattr(self.conn, "device_id", "") or "").strip().lower()
        return bool(device_id and device_id == str(allowlist[0]).strip().lower())

    def _renderer_v3_enabled(self) -> bool:
        features = getattr(self.conn, "features", None)
        detail = features.get("lessonRendererV3") if isinstance(features, dict) else None
        return (
            self._renderer_v3_rollout_enabled()
            and self.negotiated_version == RENDERER_V3
            and RENDERER_V3 in self.renderer_capabilities
            and isinstance(detail, dict)
            and detail.get("directMp4Cinematic") is True
            and detail.get("sdAssetPack") is True
        )

    def _renderer_v4_rollout_enabled(self) -> bool:
        config = getattr(self.conn, "config", {}) or {}
        lesson_cfg = _lesson_config(config)
        if lesson_cfg.get("renderer_v4_enabled") is not True:
            return False
        allowlist = lesson_cfg.get("rollout_device_allowlist") or []
        if len(allowlist) != 1:
            return False
        device_id = str(getattr(self.conn, "device_id", "") or "").strip().lower()
        return bool(device_id and device_id == str(allowlist[0]).strip().lower())

    def _renderer_v4_enabled(self) -> bool:
        features = getattr(self.conn, "features", None)
        detail = features.get("lessonRendererV4") if isinstance(features, dict) else None
        return (
            self._renderer_v4_rollout_enabled()
            and self.negotiated_version == RENDERER_V4
            and RENDERER_V4 in self.renderer_capabilities
            and isinstance(detail, dict)
            and detail.get("flattenedMjpegCinematic") is True
            and detail.get("sdAssetPack") is True
        )

    def _renderer_v5_rollout_enabled(self) -> bool:
        config = getattr(self.conn, "config", {}) or {}
        lesson_cfg = _lesson_config(config)
        if lesson_cfg.get("renderer_v5_enabled") is not True:
            return False
        allowlist = lesson_cfg.get("rollout_device_allowlist") or []
        if len(allowlist) != 1:
            return False
        device_id = str(getattr(self.conn, "device_id", "") or "").strip().lower()
        return bool(device_id and device_id == str(allowlist[0]).strip().lower())

    def _renderer_v5_enabled(self) -> bool:
        features = getattr(self.conn, "features", None)
        detail = features.get("lessonRendererV5") if isinstance(features, dict) else None
        return (
            self._renderer_v5_rollout_enabled()
            and self.negotiated_version == RENDERER_V5
            and RENDERER_V5 in self.renderer_capabilities
            and isinstance(detail, dict)
            and detail.get("layeredCinematic") is True
            and detail.get("sdAssetPack") is True
        )

    def _cinematic_enabled(self) -> bool:
        return (
            self._renderer_v3_enabled()
            or self._renderer_v4_enabled()
            or self._renderer_v5_enabled()
        )

    def _validate_conversation_contracts(self) -> bool:
        conversation = self.manifest.get("conversation")
        steps = conversation.get("steps") if isinstance(conversation, dict) else None
        if not isinstance(steps, list) or not steps:
            return False
        manifest_step_keys = [
            step.get("id") for step in self._steps if isinstance(step.get("id"), str)
        ]
        conversation_step_keys = [
            step.get("stepKey") for step in steps if isinstance(step, dict)
        ]
        if (
            len(manifest_step_keys) != len(self._steps)
            or manifest_step_keys != conversation_step_keys
        ):
            return False
        try:
            for step in steps:
                step_key = step.get("stepKey") if isinstance(step, dict) else None
                if not isinstance(step_key, str):
                    return False
                lesson_conversation_contract_from_backend(
                    self.manifest,
                    lesson_session_id=self.session_id,
                    step_key=step_key,
                )
        except ConversationContractError as exc:
            self._log("warning", f"conversation contract disabled code={exc.code}")
            return False
        return True

    def _bind_conversation_for_current_step(self) -> None:
        self.conversation = None
        self._conversation_pending_visual = None
        self._conversation_visual_ack = None
        self._conversation_started_at = None
        self._conversation_fallback_window_id = None
        self._conversation_fallback_turn_sequence_id = None
        self._conversation_pending_recognized_text = None
        self._clear_conversation_fallback_ack()
        if (
            not self._conversation_contract_valid
            or self.negotiated_version != RENDERER_V4
            or not isinstance(self._step_id, str)
        ):
            return
        try:
            contract = lesson_conversation_contract_from_backend(
                self.manifest,
                lesson_session_id=self.session_id,
                step_key=self._step_id,
            )
        except ConversationContractError:
            return

        def next_attempt_id() -> str:
            self._conversation_attempt_serial += 1
            return (
                f"{self.session_id}:{contract.step_key}:"
                f"{self._conversation_attempt_serial}"
            )

        conversation = LessonConversationRuntime(
            contract,
            attempt_id_factory=next_attempt_id,
        )
        conversation.open_attempt()
        self.conversation = conversation
        self._conversation_started_at = self._clock()

    def conversation_tool_context(self) -> dict[str, Any] | None:
        conversation = self.conversation
        if conversation is None or conversation.attempt_id is None:
            return None
        pending_cue = conversation.pending_cue_id
        identity = conversation.identity(cue_id=pending_cue)
        guidance = conversation.guidance
        allowed_tools: list[str] = []
        authoritative = self._conversation_authority_token() is not None
        visual_pending = isinstance(self._conversation_pending_visual, dict)
        visual_acked = self._conversation_visual_ack == (
            conversation.attempt_id,
            pending_cue,
        )
        if authoritative and not visual_pending:
            if conversation.state is ConversationState.COMPLETE:
                if conversation.continue_applied:
                    allowed_tools = ["lesson_visual_reaction"] if pending_cue is not None else []
                else:
                    allowed_tools = ["lesson_continue"]
            elif not visual_acked:
                if conversation.outcome == "attempted" and conversation.review_needed:
                    allowed_tools = ["lesson_continue"]
                elif pending_cue is not None:
                    allowed_tools = ["lesson_visual_reaction"]
            elif conversation.state is ConversationState.LISTENING:
                allowed_tools = (
                    ["lesson_pronunciation_outcome"]
                    if conversation.outcome == "speaking_evidence"
                    else ["lesson_child_response", "lesson_context_turn"]
                )
            elif conversation.state is ConversationState.REACTING:
                allowed_tools = ["lesson_continue"]
            else:
                allowed_tools = ["lesson_child_response", "lesson_context_turn"]
        return {
            "identity": {
                "lessonSessionId": identity.lesson_session_id,
                "turnSequenceId": identity.turn_sequence_id,
                "attemptId": identity.attempt_id,
                "stepKey": identity.step_key,
                "cueId": identity.cue_id,
            },
            "nextIntent": conversation.pending_intent,
            "allowedTools": allowed_tools,
            "cueId": pending_cue,
            "effect": conversation.pending_effect,
            "guidance": {
                "targetWord": guidance.target_word,
                "meaningsVi": list(guidance.meanings_vi),
                "relatedConcepts": list(guidance.related_concepts),
                "teachingCopy": guidance.teaching_copy,
                "expectedAnswer": guidance.expected_answer,
                "pronunciation": {
                    "slowModel": guidance.pronunciation.slow_model,
                    "segments": (
                        list(guidance.pronunciation.segments) if guidance.pronunciation.segments is not None else None
                    ),
                    "phonemes": (
                        list(guidance.pronunciation.phonemes) if guidance.pronunciation.phonemes is not None else None
                    ),
                    "l1GuidanceVi": guidance.pronunciation.l1_guidance_vi,
                },
            },
        }

    def record_conversation_recognized_text(self, text: Any) -> None:
        """Cache Live's own ASR transcript for the child's in-flight speaking turn.

        This is the only place recognized speech is retained, and only transiently:
        it exists solely to corroborate a model-asserted pronunciation "correct"
        against the contract's target word/meanings before the runtime trusts it.
        It is never logged, forwarded, or attached to evidence/progress/telemetry.
        """
        if not self.conversation_tool_path_active():
            return
        if not isinstance(text, str) or not text.strip():
            return
        self._conversation_pending_recognized_text = text

    def _conversation_pronunciation_corroborated(self) -> bool:
        conversation = self.conversation
        if conversation is None:
            return False
        recognized_text = self._conversation_pending_recognized_text
        if not isinstance(recognized_text, str) or not recognized_text.strip():
            return False
        guidance = conversation.guidance
        expected = [guidance.target_word, guidance.expected_answer, *guidance.meanings_vi]
        return _child_response_matches_expected(recognized_text, [value for value in expected if value])

    def conversation_tool_path_active(self) -> bool:
        conversation = self.conversation
        return bool(
            conversation is not None
            and conversation.attempt_id is not None
            and self._conversation_contract_valid
            and self.negotiated_version == RENDERER_V4
            and not self._closed
            and getattr(self.conn, "lesson_runtime", None) is self
            and self.state == S_RUNNING
        )

    async def _publish_conversation_tool_context(self) -> bool:
        context = self.conversation_tool_context()
        publisher = getattr(
            getattr(self.conn, "voice_provider", None),
            "publish_lesson_conversation_context",
            None,
        )
        if context is None or not callable(publisher):
            return False
        try:
            return bool(await publisher(context))
        except Exception as exc:
            self._log(
                "warning",
                f"conversation context publish failed error={type(exc).__name__}",
            )
            return False

    async def conversation_continue_from_tool(self, identity: LessonToolIdentity | None) -> ConversationDecision:
        conversation = self.conversation
        if conversation is None or not isinstance(identity, LessonToolIdentity):
            return inactive_conversation_decision()
        enriched = LessonToolIdentity(
            lesson_session_id=identity.lesson_session_id,
            turn_sequence_id=identity.turn_sequence_id,
            attempt_id=identity.attempt_id,
            step_key=identity.step_key,
            cue_id=conversation.pending_cue_id,
        )
        return await self.conversation_continue(
            enriched,
            effect=conversation.pending_effect,
        )

    async def conversation_interrupt_current(self) -> ConversationDecision:
        conversation = self.conversation
        if conversation is None:
            return inactive_conversation_decision()
        return await self.conversation_interrupt(conversation.identity())

    def _conversation_authority_token(self) -> tuple[Any, ...] | None:
        conversation = self.conversation
        if (
            conversation is None
            or self._closed
            or getattr(self.conn, "lesson_runtime", None) is not self
            or self.state != S_RUNNING
            or not self._step_acked
            or not self._step_visuals_ready
            or self._step_completed
            or self._step_index < 0
            or self._step_index >= len(self._steps)
            or self._steps[self._step_index] is not self._step
            or not isinstance(self._step_id, str)
            or self._step_id != conversation.identity().step_key
            or conversation.attempt_id is None
        ):
            return None
        return (
            id(conversation),
            self.session_id,
            self._step_id,
            self._step_seq,
            conversation.attempt_id,
            conversation.turn_sequence_id,
        )

    def _conversation_token_is_current(self, token: tuple[Any, ...]) -> bool:
        current = self._conversation_authority_token()
        return current == token

    def _conversation_snapshot_owner_matches(
        self,
        conversation: LessonConversationRuntime,
        token: tuple[Any, ...],
    ) -> bool:
        attempt_id = conversation.attempt_id
        return (
            self.conversation is conversation
            and self.session_id == token[1]
            and self._step_id == token[2]
            and self._step_seq == token[3]
            and attempt_id == token[4]
            and conversation.turn_sequence_id == token[5]
        )

    def _conversation_guard(self) -> tuple[LessonConversationRuntime, tuple[Any, ...]] | ConversationDecision:
        conversation = self.conversation
        if conversation is None:
            return inactive_conversation_decision()
        token = self._conversation_authority_token()
        if token is None:
            return conversation.reject("RUNTIME_NOT_AUTHORITATIVE")
        return conversation, token

    def _conversation_semantic_guard(
        self,
    ) -> tuple[LessonConversationRuntime, tuple[Any, ...]] | ConversationDecision:
        guarded = self._conversation_guard()
        if isinstance(guarded, ConversationDecision):
            return guarded
        conversation, token = guarded
        assert isinstance(conversation, LessonConversationRuntime)
        pending_cue = conversation.pending_cue_id
        acknowledged = self._conversation_visual_ack
        if (
            not isinstance(pending_cue, str)
            or acknowledged != (conversation.attempt_id, pending_cue)
        ):
            return conversation.reject("VISUAL_ACK_REQUIRED")
        return conversation, token

    async def conversation_child_response(
        self, identity: LessonToolIdentity | None, response_class: str
    ) -> ConversationDecision:
        guarded = self._conversation_semantic_guard()
        if isinstance(guarded, ConversationDecision):
            return guarded
        conversation, _token = guarded
        decision = conversation.child_response(identity, response_class)
        if decision.accepted:
            self._conversation_visual_ack = None
            self._invalidate_conversation_fallback_after_turn_change()
        return decision

    async def conversation_pronunciation_outcome(
        self, identity: LessonToolIdentity | None, outcome: str
    ) -> ConversationDecision:
        guarded = self._conversation_semantic_guard()
        if isinstance(guarded, ConversationDecision):
            return guarded
        conversation, _token = guarded
        # The model asserts "correct" itself via the tool call; never trust that
        # assertion without independent corroboration against Live's own ASR
        # transcript matched to the contract's target word/meanings. Missing or
        # mismatched evidence must fail gentle (uncertain), never false-accept.
        effective_outcome = outcome
        if outcome == "correct" and not self._conversation_pronunciation_corroborated():
            effective_outcome = "uncertain"
        decision = conversation.pronunciation_outcome(identity, effective_outcome)
        if decision.accepted:
            self._conversation_pending_recognized_text = None
            self._conversation_visual_ack = None
            self._invalidate_conversation_fallback_after_turn_change()
        return decision

    async def conversation_context_turn(
        self, identity: LessonToolIdentity | None
    ) -> ConversationDecision:
        guarded = self._conversation_semantic_guard()
        if isinstance(guarded, ConversationDecision):
            return guarded
        conversation, _token = guarded
        decision = conversation.context_turn(identity)
        if decision.accepted:
            self._conversation_visual_ack = None
            self._invalidate_conversation_fallback_after_turn_change()
        return decision

    async def conversation_visual_reaction(
        self,
        identity: LessonToolIdentity | None,
        cue_role: str,
        *,
        effect: str | None,
    ) -> ConversationDecision:
        guarded = self._conversation_guard()
        if isinstance(guarded, ConversationDecision):
            return guarded
        conversation, _token = guarded
        assert isinstance(conversation, LessonConversationRuntime)
        pending = self._conversation_pending_visual
        if isinstance(pending, dict):
            return conversation.reject("VISUAL_ACK_REQUIRED")
        snapshot = conversation.snapshot()
        decision = conversation.visual_reaction(identity, cue_role, effect=effect)
        token = self._conversation_authority_token()
        if decision.accepted:
            if token is None:
                conversation.restore_authoritative_snapshot(snapshot)
                return conversation.reject("RUNTIME_NOT_AUTHORITATIVE")
            try:
                emitted = await self._emit_conversation_cue(
                    decision,
                    token=token,
                    advances_step=cue_role == "word_transition",
                )
            except asyncio.CancelledError:
                if self._conversation_snapshot_owner_matches(conversation, token):
                    conversation.restore_authoritative_snapshot(snapshot)
                raise
            except Exception:
                if self._conversation_snapshot_owner_matches(conversation, token):
                    conversation.restore_authoritative_snapshot(snapshot)
                return conversation.reject("VISUAL_EMIT_FAILED")
            if not emitted:
                if self._conversation_snapshot_owner_matches(conversation, token):
                    conversation.restore_authoritative_snapshot(snapshot)
                return conversation.reject("RUNTIME_NOT_AUTHORITATIVE")
            self._invalidate_conversation_fallback_after_turn_change()
        return decision

    async def conversation_interrupt(
        self, identity: LessonToolIdentity | None
    ) -> ConversationDecision:
        guarded = self._conversation_guard()
        if isinstance(guarded, ConversationDecision):
            return guarded
        conversation, _token = guarded
        decision = conversation.interrupt(identity)
        if decision.accepted:
            self._retire_conversation_visual()
            self._conversation_visual_ack = None
            self._invalidate_conversation_fallback_after_turn_change()
        return decision

    def _curated_conversation_fallback_prompt(self) -> str:
        conversation = self.conversation
        if conversation is None:
            return ""
        guidance = conversation.guidance
        target = guidance.target_word.strip()
        return f"Mình cùng thử nhé. Say {target}." if target else ""

    def _clear_conversation_fallback_ack(self) -> None:
        future = self._conversation_fallback_ack_future
        self._conversation_fallback_ack_future = None
        self._conversation_fallback_ack_sequence = None
        self._conversation_fallback_ack_cue_id = None
        self._conversation_fallback_ack_attempt_id = None
        self._conversation_fallback_ack_expired = False
        self._conversation_fallback_prompt_authorization = None
        self._conversation_fallback_prompt_claimed = False
        if future is not None and not future.done():
            future.set_result(False)

    def _expire_conversation_fallback_ack(self) -> None:
        future = self._conversation_fallback_ack_future
        self._conversation_fallback_ack_future = None
        self._conversation_fallback_ack_sequence = None
        self._conversation_fallback_ack_cue_id = None
        self._conversation_fallback_ack_attempt_id = None
        self._conversation_fallback_ack_expired = True
        self._conversation_fallback_prompt_authorization = None
        self._conversation_fallback_prompt_claimed = False
        if future is not None and not future.done():
            future.set_result(False)

    def _rollback_conversation_fallback_window(
        self,
        *,
        conversation: LessonConversationRuntime,
        window_id: str,
        turn_sequence_id: int,
    ) -> None:
        if (
            self.conversation is conversation
            and self._conversation_fallback_window_id == window_id
            and self._conversation_fallback_turn_sequence_id == turn_sequence_id
        ):
            self._conversation_fallback_window_id = None
            self._conversation_fallback_turn_sequence_id = None
            self._clear_conversation_fallback_ack()

    def _invalidate_conversation_fallback_after_turn_change(self) -> None:
        conversation = self.conversation
        if (
            conversation is not None
            and self._conversation_fallback_window_id is not None
            and self._conversation_fallback_turn_sequence_id
            != conversation.turn_sequence_id
        ):
            self._expire_conversation_fallback_ack()

    def _conversation_fallback_prompt_authority_current(self, window_id: str) -> bool:
        conversation = self.conversation
        cue_id = self._conversation_fallback_ack_cue_id
        return bool(
            conversation is not None
            and not self._closed
            and window_id == self._conversation_fallback_window_id
            and self._conversation_fallback_turn_sequence_id
            == conversation.turn_sequence_id
            and not self._conversation_fallback_ack_expired
            and isinstance(cue_id, str)
            and self._conversation_fallback_ack_attempt_id == conversation.attempt_id
            and self._conversation_visual_ack == (conversation.attempt_id, cue_id)
            and self._conversation_authority_token() is not None
        )

    async def conversation_live_interruption(
        self,
        reason: str,
    ) -> ConversationLiveFallbackDirective:
        if reason not in {"timeout", "interrupted", "transport"}:
            return ConversationLiveFallbackDirective(
                accepted=False,
                code="UNSUPPORTED_LIVE_FALLBACK_REASON",
                reason=reason,
                window_id=None,
                reconnect_allowed=False,
                prompt="",
            )
        authority = self._conversation_guard()
        if isinstance(authority, ConversationDecision):
            return ConversationLiveFallbackDirective(
                accepted=False,
                code=authority.code,
                reason=reason,
                window_id=None,
                reconnect_allowed=False,
                prompt="",
            )
        existing_window = self._conversation_fallback_window_id
        conversation, _authority_token = authority
        same_window = (
            existing_window is not None
            and self._conversation_fallback_turn_sequence_id
            == conversation.turn_sequence_id
        )
        if same_window:
            return ConversationLiveFallbackDirective(
                accepted=True,
                code="LIVE_FALLBACK_RECONNECT_BOUNDED",
                reason=reason,
                window_id=existing_window,
                reconnect_allowed=False,
                prompt=self._curated_conversation_fallback_prompt(),
            )
        if existing_window is not None:
            self._conversation_fallback_window_id = None
            self._conversation_fallback_turn_sequence_id = None
            self._clear_conversation_fallback_ack()
        snapshot = conversation.snapshot()
        visual_ack = self._conversation_visual_ack
        decision = conversation.live_fallback(conversation.identity(), reason=reason)
        token = self._conversation_authority_token()
        if not decision.accepted or token is None:
            if decision.accepted:
                conversation.restore_authoritative_snapshot(snapshot)
            return ConversationLiveFallbackDirective(
                accepted=False,
                code=decision.code if not decision.accepted else "RUNTIME_NOT_AUTHORITATIVE",
                reason=reason,
                window_id=None,
                reconnect_allowed=False,
                prompt="",
            )
        self._conversation_visual_ack = None
        window_id = f"{conversation.attempt_id}:{conversation.turn_sequence_id}"
        fallback_turn_sequence_id = conversation.turn_sequence_id
        self._conversation_fallback_window_id = window_id
        self._conversation_fallback_turn_sequence_id = conversation.turn_sequence_id
        self._clear_conversation_fallback_ack()
        self._conversation_fallback_ack_future = asyncio.get_running_loop().create_future()
        self._conversation_fallback_ack_sequence = self._seq + 1
        self._conversation_fallback_ack_cue_id = decision.cue_id
        self._conversation_fallback_ack_attempt_id = conversation.attempt_id
        try:
            emitted = await self._emit_conversation_cue(decision, token=token)
        except asyncio.CancelledError:
            self._rollback_conversation_fallback_window(
                conversation=conversation,
                window_id=window_id,
                turn_sequence_id=fallback_turn_sequence_id,
            )
            if self._conversation_snapshot_owner_matches(conversation, token):
                conversation.restore_authoritative_snapshot(snapshot)
                self._conversation_visual_ack = visual_ack
            raise
        except Exception:
            emitted = False
        if not emitted:
            self._rollback_conversation_fallback_window(
                conversation=conversation,
                window_id=window_id,
                turn_sequence_id=fallback_turn_sequence_id,
            )
            if self._conversation_snapshot_owner_matches(conversation, token):
                conversation.restore_authoritative_snapshot(snapshot)
                self._conversation_visual_ack = visual_ack
            return ConversationLiveFallbackDirective(
                accepted=False,
                code="VISUAL_EMIT_FAILED",
                reason=reason,
                window_id=None,
                reconnect_allowed=False,
                prompt="",
            )
        return ConversationLiveFallbackDirective(
            accepted=True,
            code="LIVE_FALLBACK_READY",
            reason=reason,
            window_id=window_id,
            reconnect_allowed=True,
            prompt=self._curated_conversation_fallback_prompt(),
        )

    async def wait_conversation_live_fallback_ack(
        self,
        window_id: str,
        *,
        timeout_sec: float,
    ) -> str | None:
        if (
            window_id != self._conversation_fallback_window_id
            or self._conversation_fallback_turn_sequence_id is None
            or self._conversation_fallback_ack_expired
        ):
            return None
        future = self._conversation_fallback_ack_future
        if future is None:
            return None
        try:
            acknowledged = bool(
                await asyncio.wait_for(
                    asyncio.shield(future),
                    timeout=max(0.01, min(float(timeout_sec), 5.0)),
                )
            )
        except asyncio.TimeoutError:
            self._expire_conversation_fallback_ack()
            return None
        except (TypeError, ValueError):
            self._expire_conversation_fallback_ack()
            return None
        if (
            not acknowledged
            or not self._conversation_fallback_prompt_authority_current(window_id)
            or self._conversation_fallback_prompt_authorization is not None
            or self._conversation_fallback_prompt_claimed
        ):
            return None
        authorization = (
            f"{window_id}:{self._conversation_fallback_ack_sequence}:prompt"
        )
        self._conversation_fallback_prompt_authorization = authorization
        return authorization

    def claim_conversation_live_fallback_prompt(
        self,
        window_id: str,
        authorization: str,
    ) -> bool:
        if (
            self._conversation_fallback_prompt_claimed
            or authorization != self._conversation_fallback_prompt_authorization
            or not self._conversation_fallback_prompt_authority_current(window_id)
        ):
            return False
        self._conversation_fallback_prompt_claimed = True
        return True

    def expire_conversation_live_fallback_prompt(
        self,
        window_id: str,
        authorization: str,
    ) -> bool:
        if (
            window_id != self._conversation_fallback_window_id
            or authorization != self._conversation_fallback_prompt_authorization
        ):
            return False
        self._expire_conversation_fallback_ack()
        return True

    def conversation_live_reconnect_succeeded(self, window_id: str) -> bool:
        conversation = self.conversation
        if (
            conversation is None
            or window_id != self._conversation_fallback_window_id
            or self._conversation_fallback_turn_sequence_id
            != conversation.turn_sequence_id
        ):
            return False
        self._conversation_fallback_window_id = None
        self._conversation_fallback_turn_sequence_id = None
        self._clear_conversation_fallback_ack()
        return True

    async def conversation_continue(
        self,
        identity: LessonToolIdentity | None,
        *,
        effect: str | None,
        next_step_key: str | None = None,
    ) -> ConversationDecision:
        guarded = self._conversation_guard()
        if isinstance(guarded, ConversationDecision):
            return guarded
        conversation, _token = guarded
        assert isinstance(conversation, LessonConversationRuntime)
        attempted_review = (
            conversation.outcome == "attempted" and conversation.review_needed
        )
        if not attempted_review:
            pending_cue = conversation.pending_cue_id
            acknowledged = self._conversation_visual_ack
            if (
                not isinstance(pending_cue, str)
                or acknowledged != (conversation.attempt_id, pending_cue)
            ):
                return conversation.reject("VISUAL_ACK_REQUIRED")
        snapshot = conversation.snapshot()
        visual_ack = self._conversation_visual_ack
        decision = conversation.continue_lesson(
            identity,
            effect=effect,
            next_step_key=next_step_key,
        )
        if not decision.accepted:
            return decision
        self._invalidate_conversation_fallback_after_turn_change()
        self._conversation_visual_ack = None
        if decision.next_intent == "complete_lesson":
            token = self._conversation_authority_token()
            if token is None:
                conversation.restore_authoritative_snapshot(snapshot)
                self._conversation_visual_ack = visual_ack
                return conversation.reject("RUNTIME_NOT_AUTHORITATIVE")
            self._retire_conversation_visual()
            if not await self._complete_conversation_step(token):
                if self._conversation_snapshot_owner_matches(conversation, token):
                    conversation.restore_authoritative_snapshot(snapshot)
                    self._conversation_visual_ack = visual_ack
                return conversation.reject("RUNTIME_NOT_AUTHORITATIVE")
        return decision

    async def _emit_conversation_cue(
        self,
        decision: ConversationDecision,
        *,
        token: tuple[Any, ...],
        advances_step: bool = False,
    ) -> bool:
        cue_id = decision.cue_id
        cue = self._conversation_cues.get(cue_id) if isinstance(cue_id, str) else None
        if (
            not decision.accepted
            or not isinstance(cue, dict)
            or cue.get("stepKey") != self._step_id
            and not advances_step
        ):
            return False
        if not self._conversation_token_is_current(token):
            return False
        self._retire_conversation_visual()
        sequence = await self._emit(
            "lesson_prepare",
            step_id=self._step_id,
            body={
                "profile": self.profile,
                "cinematicPhase": {"command": "prepare", **copy.deepcopy(cue)},
            },
        )
        if not self._conversation_token_is_current(token):
            self._retire_conversation_visual_sequence(sequence)
            return False
        self._conversation_pending_visual = {
            "stage": "prepare",
            "sequence": sequence,
            "cueId": cue_id,
            "stepId": self._step_id,
            "attemptId": self.conversation.attempt_id if self.conversation else None,
            "advancesStep": advances_step,
            "authorityToken": token,
        }
        return True

    def _retire_conversation_visual_sequence(self, sequence: int) -> None:
        ack_sequences = {sequence}
        command = self._cinematic_pending_command
        if isinstance(command, dict) and command.get("commandSequenceId") == sequence:
            ack_sequence = command.get("ackSequence")
            if type(ack_sequence) is int:
                ack_sequences.add(ack_sequence)
            self._cinematic_pending_command = None
        for ack_sequence in ack_sequences:
            frame = self._outstanding.pop(ack_sequence, None)
            if isinstance(frame, dict):
                self._retire_conversation_ack_sequence(ack_sequence, frame)
            self._cancel_frame_ack_timeout(ack_sequence)
        self._cancel_frame_ack_retry(sequence)

    def _retire_conversation_ack_sequence(
        self, sequence: int, frame: dict[str, Any]
    ) -> None:
        command = self._cinematic_frame_command(frame)
        conversation_prepare = bool(
            frame.get("type") == "lesson_prepare"
            and isinstance(command, dict)
            and command.get("command") == "prepare"
            and command.get("templateVersion") == 2
            and isinstance(command.get("cueId"), str)
        )
        if (
            frame.get("type") != "lesson_cinematic_control"
            and not conversation_prepare
        ) or command is None:
            return
        self._retired_conversation_ack_sequences[sequence] = {
            "protocolVersion": self.negotiated_version,
            "assignmentId": self.assignment_id,
            "sessionId": self.session_id,
            "lessonId": self.lesson_id,
            "lessonVersion": self.lesson_version,
            "stepId": frame.get("stepId"),
            "command": {
                key: copy.deepcopy(command.get(key))
                for key in (
                    "command",
                    "phaseId" if "phaseId" in command else "cueId",
                    "commandSequenceId",
                )
            },
        }
        while (
            len(self._retired_conversation_ack_sequences)
            > MAX_RETIRED_CONVERSATION_ACK_SEQUENCES
        ):
            oldest = next(iter(self._retired_conversation_ack_sequences))
            self._retired_conversation_ack_sequences.pop(oldest, None)

    def _retire_conversation_visual(self) -> None:
        pending = self._conversation_pending_visual
        self._conversation_pending_visual = None
        if not isinstance(pending, dict):
            return
        sequence = pending.get("sequence")
        if type(sequence) is int:
            self._retire_conversation_visual_sequence(sequence)

    def _retire_conversation_start_send_transaction(
        self,
        pending: dict[str, Any],
        sequence: int,
    ) -> bool:
        current = self._conversation_pending_visual
        if (
            current is not pending
            or current.get("stage") != "start"
            or current.get("sequence") != sequence
        ):
            return False
        cue_id = current.get("cueId")
        attempt_id = current.get("attemptId")
        self._conversation_pending_visual = None
        self._retire_conversation_visual_sequence(sequence)
        if (
            self._conversation_fallback_ack_sequence == sequence
            and self._conversation_fallback_ack_cue_id == cue_id
            and self._conversation_fallback_ack_attempt_id == attempt_id
        ):
            self._conversation_fallback_window_id = None
            self._conversation_fallback_turn_sequence_id = None
            self._clear_conversation_fallback_ack()
        return True

    async def _fail_conversation_start_send(
        self,
        *,
        cue_id: str,
        step_id: str | None,
        exc: BaseException,
    ) -> None:
        self.last_error = LessonError(
            CINEMATIC_START_SEND_FAILED,
            "failed to send cinematic start after prepare acknowledgement",
            retryable=True,
            context={
                "stepId": step_id,
                "cueId": cue_id,
                "stage": "startSend",
                "errorType": type(exc).__name__,
            },
        )
        self.state = S_FAILED
        self._log(
            "error",
            f"CINEMATIC_START_SEND_FAILED stepId={step_id or ''} "
            f"cueId={cue_id} error={type(exc).__name__}",
        )
        try:
            await self._emit_error(self.last_error)
        except asyncio.CancelledError:
            raise
        except Exception as notify_exc:
            self._log(
                "warning",
                "cinematic start-send error notification failed: "
                f"{type(notify_exc).__name__}",
            )
        try:
            await self._notify_lesson_terminal("cinematic_start_send_failed")
        except asyncio.CancelledError:
            raise
        except Exception as notify_exc:
            self._log(
                "warning",
                "cinematic start-send terminal notification failed: "
                f"{type(notify_exc).__name__}",
            )

    async def _on_conversation_visual_acked(self, frame: dict[str, Any]) -> None:
        pending = self._conversation_pending_visual
        command = self._cinematic_frame_command(frame)
        if not isinstance(pending, dict) or not isinstance(command, dict):
            return
        if (
            pending.get("stage") != "start"
            or command.get("commandSequenceId") != pending.get("sequence")
            or command.get("cueId") != pending.get("cueId")
            or pending.get("stepId") != self._step_id
            or self.conversation is None
            or pending.get("attemptId") != self.conversation.attempt_id
        ):
            return
        self._conversation_pending_visual = None
        self._conversation_visual_ack = (self.conversation.attempt_id, command["cueId"])
        if (
            self._conversation_fallback_window_id is not None
            and not self._conversation_fallback_ack_expired
            and command.get("commandSequenceId")
            == self._conversation_fallback_ack_sequence
            and command.get("cueId") == self._conversation_fallback_ack_cue_id
            and self.conversation.attempt_id
            == self._conversation_fallback_ack_attempt_id
        ):
            future = self._conversation_fallback_ack_future
            if future is not None and not future.done():
                future.set_result(True)
        await self._publish_conversation_tool_context()
        if pending.get("advancesStep") is True:
            token = self._conversation_authority_token()
            if token is not None:
                await self._complete_conversation_step(token)

    async def _on_conversation_visual_prepared(self, frame: dict[str, Any]) -> bool:
        pending = self._conversation_pending_visual
        command = self._cinematic_frame_command(frame)
        if not isinstance(pending, dict) or not isinstance(command, dict):
            return False
        token = pending.get("authorityToken")
        if (
            pending.get("stage") != "prepare"
            or command.get("command") != "prepare"
            or command.get("commandSequenceId") != pending.get("sequence")
            or command.get("cueId") != pending.get("cueId")
            or pending.get("stepId") != self._step_id
            or self.conversation is None
            or pending.get("attemptId") != self.conversation.attempt_id
            or not isinstance(token, tuple)
            or not self._conversation_token_is_current(token)
        ):
            return False
        next_sequence = self._seq + 1
        pending["stage"] = "start"
        pending["sequence"] = next_sequence
        if (
            self._conversation_fallback_ack_sequence == command.get("commandSequenceId")
            and self._conversation_fallback_ack_cue_id == command.get("cueId")
        ):
            self._conversation_fallback_ack_sequence = next_sequence
        try:
            cue = self._conversation_cues.get(command["cueId"])
            if not isinstance(cue, dict):
                raise RuntimeError("conversation cue disappeared before start")
            sequence = await self._emit(
                "lesson_cinematic_control",
                step_id=self._step_id,
                body={
                    "command": "start",
                    "cueId": cue["cueId"],
                },
            )
        except asyncio.CancelledError:
            self._retire_conversation_start_send_transaction(pending, next_sequence)
            raise
        except Exception as exc:
            retired = self._retire_conversation_start_send_transaction(
                pending, next_sequence
            )
            if retired and self._conversation_token_is_current(token):
                await self._fail_conversation_start_send(
                    cue_id=command["cueId"],
                    step_id=pending.get("stepId"),
                    exc=exc,
                )
            return False
        if sequence != next_sequence or not self._conversation_token_is_current(token):
            self._retire_conversation_start_send_transaction(pending, next_sequence)
            return False
        return True

    async def _complete_conversation_step(self, token: tuple[Any, ...]) -> bool:
        await asyncio.sleep(0)
        if not self._conversation_token_is_current(token):
            return False
        if self.conversation is None or not isinstance(self._step_id, str):
            return False
        step_id = self._step_id
        if step_id in self._conversation_progress_forwarded:
            return True
        started_at = self._conversation_started_at
        elapsed_ms = (
            max(0, int(round((self._clock() - started_at) * 1000)))
            if isinstance(started_at, (int, float))
            else 0
        )
        evidence: SpeakingEvidence | None = self.conversation.build_speaking_evidence(
            lesson_version=self.lesson_version,
            elapsed_ms=elapsed_ms,
        )
        if evidence is None:
            return False
        self._conversation_progress_forwarded.add(step_id)
        self._forward(
            {
                "type": "step_completed",
                "sequence": -self._step_seq if isinstance(self._step_seq, int) else None,
                "stepId": step_id,
                "stepType": (self._step or {}).get("type"),
                "result": "success" if self.conversation.mastered else "miss",
                "detail": {"evidence": evidence.to_mapping()},
            }
        )
        self._step_completed = True
        await self._maybe_finish_step()
        return True

    async def replay_pending_terminal_event(self) -> bool:
        replay = getattr(self.forwarder, "replay_pending_terminal_event", None)
        if not callable(replay):
            return False
        return bool(await replay())

    # ── inbound handlers (called by lessonMessageHandler via conn.lesson_runtime) ─

    def _matches_runtime_identity(self, msg_json: Dict[str, Any]) -> bool:
        for key, expected in (("assignmentId", self.assignment_id), ("sessionId", self.session_id)):
            if expected is None:
                continue
            actual = msg_json.get(key)
            if actual != expected:
                self._log("info", f"stale lesson frame ignored: {key} mismatch")
                return False
        return True

    async def _contain_state_machine_fault(self, entry: str, exc: BaseException) -> None:
        """T2.1 fault containment: an unexpected exception raised inside the state
        machine must FAIL THE LESSON TERMINALLY, not wedge it.

        ``lessonMessageHandler`` already swallows handler exceptions so a lesson
        fault can never tear down the WS/voice path — the process survives either
        way. But swallowing alone left the runtime in RUNNING with its step timer
        already cancelled (``_on_frame_acked`` clears it before dispatching), i.e.
        no timer, no terminal event, and a backend assignment stuck in its active
        slot forever. Project the fault as a terminal ``lesson_failed`` instead.
        """
        self._log(
            "error",
            f"lesson runtime fault in {entry}: {type(exc).__name__}: {exc}",
        )
        if self.state in (S_FAILED, S_COMPLETED):
            return
        self.last_error = LessonError(
            "LESSON_RUNTIME_FAULT",
            f"unhandled {type(exc).__name__} in {entry}",
            retryable=False,
        )
        self.state = S_FAILED
        try:
            self._cancel_step_timeout()
            self._cancel_passive_dwell()
            self._cancel_child_response_timeout()
            self._cancel_visual_waiters(increment_generation=True, reason="runtimeFault")
            await self._emit_error(self.last_error)
            await self._notify_lesson_terminal("runtime_fault")
        except Exception as teardown_exc:  # pragma: no cover - teardown is best-effort
            self._log(
                "warning",
                f"lesson runtime fault teardown failed: {type(teardown_exc).__name__}",
            )

    async def on_lesson_ack(self, msg_json: Dict[str, Any]) -> None:
        try:
            await self._on_lesson_ack_impl(msg_json)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - contained into a terminal failure
            await self._contain_state_machine_fault("on_lesson_ack", exc)

    async def on_lesson_progress(self, msg_json: Dict[str, Any]) -> None:
        try:
            await self._on_lesson_progress_impl(msg_json)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - contained into a terminal failure
            await self._contain_state_machine_fault("on_lesson_progress", exc)

    async def on_lesson_error(self, msg_json: Dict[str, Any]) -> None:
        try:
            await self._on_lesson_error_impl(msg_json)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - contained into a terminal failure
            await self._contain_state_machine_fault("on_lesson_error", exc)

    async def _on_lesson_ack_impl(self, msg_json: Dict[str, Any]) -> None:
        if not self._is_active_runtime():
            return
        if await self._resolve_visual_ack(msg_json):
            return
        if self.state in (S_FAILED, S_COMPLETED) or (
            self.state == S_PAUSED and not self._cinematic_enabled()
        ):
            return  # terminal is absorbing — no late frame can resurrect/override it
        body = msg_json.get("body") or {}
        legacy_acked = self._legacy_empty_ack_outstanding_seq(msg_json, body)
        if legacy_acked is None and not self._matches_runtime_identity(msg_json):
            return
        acked = legacy_acked if legacy_acked is not None else body.get("acks")
        # P0: correlate on body.acks, NOT envelope.sequence.
        # DEFENSIVE COERCE: body.acks MUST be the int S->F sequence of the outstanding
        # frame. A malformed firmware/replay frame could send a list (e.g. [3]) or a
        # str — an unhashable/wrong-typed key would raise TypeError on the dict .pop()
        # below, which (pre-isolation) tore down the connection + voice. Coerce to int;
        # anything that is not a hashable int (None, list, dict, non-numeric str) is a
        # malformed ack -> idempotent no-op, identical to a stale/unknown ack.
        acked = _coerce_ack_seq(acked)
        frame = self._outstanding.get(acked) if acked is not None else None
        if frame is None:
            retired = (
                self._retired_conversation_ack_sequences.get(acked)
                if acked is not None
                else None
            )
            if isinstance(retired, dict) and self._retired_conversation_ack_matches(
                msg_json, body, acked, retired
            ):
                accepted = await self._accept_inbound(msg_json.get("sequence"))
                if accepted in {"ok", "duplicate"}:
                    self._retired_conversation_ack_sequences.pop(acked, None)
                return
            if acked is None:
                await self._accept_inbound(msg_json.get("sequence"))
            # Stale / unknown ack -> idempotent no-op (re-ack semantics, plan §5.8).
            return
        pending = self._cinematic_pending_command
        frame_command = self._cinematic_frame_command(frame)
        if (
            isinstance(pending, dict)
            and pending.get("command") in {"pause", "resume", "stop", "cancel"}
            and frame.get("type") == "lesson_step"
        ):
            await self._defer_cinematic_step_ack(
                frame, body, acked, msg_json.get("sequence")
            )
            return
        if self.state == S_PAUSED and (
            frame_command is None or frame_command.get("command") != "resume"
        ):
            return
        if isinstance(pending, dict) and pending.get("command") in {
            "pause", "resume", "stop", "cancel"
        }:
            if (
                frame_command is None
                or frame_command.get("command") != pending.get("command")
                or frame_command.get("commandSequenceId") != pending.get("commandSequenceId")
            ):
                return
        if not self._cinematic_ack_matches(frame, body):
            return
        if (await self._accept_inbound(msg_json.get("sequence"))) != "ok":
            return
        self._outstanding.pop(acked, None)
        command = self._cinematic_frame_command(frame)
        if command is not None:
            self._cinematic_pending_command = None
        self._cancel_frame_ack_timeout(acked)
        self._forward_lesson_step_ack_telemetry(frame, body, msg_json.get("sequence"))
        await self._on_frame_acked(frame, body)

    async def _defer_cinematic_step_ack(
        self,
        frame: Dict[str, Any],
        body: Dict[str, Any],
        acked: int,
        inbound_sequence: Any,
    ) -> None:
        if (await self._accept_inbound(inbound_sequence)) != "ok":
            return
        self._outstanding.pop(acked, None)
        self._cancel_step_timeout()
        if self._cinematic_deferred_step_ack is None:
            self._cinematic_deferred_step_ack = {
                "frame": copy.deepcopy(frame),
                "body": copy.deepcopy(body),
                "inboundSequence": inbound_sequence,
            }

    def _cinematic_frame_command(self, frame: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        body = frame.get("body")
        if not isinstance(body, dict):
            return None
        nested = body.get("cinematicPhase")
        command = nested if isinstance(nested, dict) else body
        if not isinstance(command.get("command"), str):
            return None
        return command

    def _cinematic_identity_payload(self) -> dict[str, str]:
        phase = self._cinematic_phase
        if not isinstance(phase, dict):
            return {}
        if self._renderer_v5_enabled():
            phase_id = phase.get("phaseId")
            return (
                {"phaseId": phase_id}
                if isinstance(phase_id, str) and phase_id in LAYERED_CINEMATIC_PHASE_IDS
                else {}
            )
        identity = cinematic_identity_key(phase)
        return {"phaseId" if "phaseId" in phase else "cueId": identity}

    def _cinematic_ack_matches(
        self, frame: Dict[str, Any], ack_body: Dict[str, Any]
    ) -> bool:
        if not self._cinematic_enabled():
            return True
        command = self._cinematic_frame_command(frame)
        if command is None:
            return True
        if not self._cinematic_ack_payload_matches(command, ack_body):
            return False
        kind = command.get("command")
        try:
            identity_field, identity_key = self._cinematic_command_identity(command)
        except (FlattenedCinematicContractError, LayeredCinematicContractError):
            return False
        pending = self._cinematic_pending_command
        return bool(
            isinstance(pending, dict)
            and pending.get("command") == kind
            and pending.get("commandSequenceId") == command.get("commandSequenceId")
            and pending.get(identity_field) == identity_key
        )

    def _cinematic_ack_payload_matches(
        self, command: dict[str, Any], ack_body: dict[str, Any]
    ) -> bool:
        ack = ack_body.get("cinematicPhase")
        if not isinstance(ack, dict):
            return False
        kind = command.get("command")
        try:
            identity_field, identity_key = self._cinematic_command_identity(command)
        except (FlattenedCinematicContractError, LayeredCinematicContractError):
            return False
        common = {"event", "command", identity_field, "commandSequenceId", "accepted"}
        expected_event = "commandApplied"
        expected_keys = common
        if kind == "prepare":
            expected_event = "frameZeroReady"
            expected_keys = common | {"frameZeroReady"}
        elif kind == "start":
            expected_event = "phaseReady"
            expected_keys = common | {"phaseReady"}
        if (
            set(ack) != expected_keys
            or ack.get("event") != expected_event
            or ack.get("command") != kind
            or ack.get(identity_field) != identity_key
            or ack.get("commandSequenceId") != command.get("commandSequenceId")
            or ack.get("accepted") is not True
        ):
            return False
        if kind == "prepare" and ack.get("frameZeroReady") is not True:
            return False
        if kind == "start" and ack.get("phaseReady") is not True:
            return False
        return True

    def _retired_conversation_ack_matches(
        self,
        msg_json: dict[str, Any],
        body: dict[str, Any],
        acked: int,
        retired: dict[str, Any],
    ) -> bool:
        command = retired.get("command")
        return bool(
            isinstance(command, dict)
            and msg_json.get("type") == "lesson_ack"
            and msg_json.get("protocolVersion") == retired.get("protocolVersion")
            and msg_json.get("assignmentId") == retired.get("assignmentId")
            and msg_json.get("sessionId") == retired.get("sessionId")
            and msg_json.get("stepId") == retired.get("stepId")
            and (
                "lessonId" not in msg_json
                or msg_json.get("lessonId") == retired.get("lessonId")
            )
            and (
                "lessonVersion" not in msg_json
                or msg_json.get("lessonVersion") == retired.get("lessonVersion")
            )
            and type(msg_json.get("sequence")) is int
            and type(body.get("acks")) is int
            and body.get("acks") == acked
            and self._cinematic_ack_payload_matches(command, body)
        )

    def _cinematic_command_identity(self, command: dict[str, Any]) -> tuple[str, str]:
        if self._renderer_v5_enabled():
            if set(key for key in ("phaseId", "cueId") if key in command) != {"phaseId"}:
                raise LayeredCinematicContractError(
                    "CINEMATIC_IDENTITY_UNSUPPORTED",
                    "layered cinematic command requires one phase identity",
                )
            phase_id = command.get("phaseId")
            if not isinstance(phase_id, str) or phase_id not in LAYERED_CINEMATIC_PHASE_IDS:
                raise LayeredCinematicContractError(
                    "CINEMATIC_IDENTITY_UNSUPPORTED",
                    "layered cinematic phase identity is invalid",
                )
            return "phaseId", phase_id
        identity_key = cinematic_identity_key(command)
        return ("phaseId" if "phaseId" in command else "cueId"), identity_key

    async def _resolve_visual_ack(self, msg_json: Dict[str, Any]) -> bool:
        body = msg_json.get("body")
        if not isinstance(body, dict):
            return bool(
                (self._visual_ack_waiters or self._retired_visual_ack_sequences)
                and msg_json.get("protocolVersion") == RENDERER_V2
            )
        raw_acked = body.get("acks")
        acked = raw_acked if type(raw_acked) is int else None
        if acked is None:
            return bool(
                (self._visual_ack_waiters or self._retired_visual_ack_sequences)
                and msg_json.get("protocolVersion") == RENDERER_V2
                and ("accepted" in body or "visualGeneration" in body)
            )
        waiter = self._visual_ack_waiters.get(acked)
        retired = self._retired_visual_ack_sequences.get(acked)
        if waiter is None and retired is None:
            return False
        required_envelope_fields = {
            "type",
            "protocolVersion",
            "assignmentId",
            "sessionId",
            "stepId",
            "sequence",
            "body",
        }
        optional_envelope_fields = {"lessonId", "lessonVersion", "timestamp"}
        envelope_fields = set(msg_json)
        expected_body_fields = {
            "acks",
            "accepted",
            "degraded",
            "degradedReason",
            "visualGeneration",
        }
        if (
            not required_envelope_fields.issubset(envelope_fields)
            or not envelope_fields.issubset(
                required_envelope_fields | optional_envelope_fields
            )
            or set(body) != expected_body_fields
            or msg_json.get("type") != "lesson_ack"
            or msg_json.get("protocolVersion") != RENDERER_V2
            or msg_json.get("assignmentId") != self.assignment_id
            or msg_json.get("sessionId") != self.session_id
            or (
                "lessonId" in msg_json
                and msg_json.get("lessonId") != self.lesson_id
            )
            or (
                "lessonVersion" in msg_json
                and msg_json.get("lessonVersion") != self.lesson_version
            )
            or (
                "timestamp" in msg_json
                and (
                    isinstance(msg_json.get("timestamp"), bool)
                    or not isinstance(msg_json.get("timestamp"), (int, float))
                    or not math.isfinite(float(msg_json.get("timestamp")))
                    or float(msg_json.get("timestamp")) < 0
                )
            )
            or msg_json.get("stepId")
            != (self._step_id if waiter is not None else retired.get("stepId"))
            or type(msg_json.get("sequence")) is not int
        ):
            return True
        expected_generation = (
            getattr(waiter, "visual_generation", None)
            if waiter is not None
            else retired.get("visualGeneration")
        )
        generation = body.get("visualGeneration")
        if type(generation) is not int or generation != expected_generation:
            return True
        accepted = body.get("accepted")
        degraded = body.get("degraded")
        reason = body.get("degradedReason")
        if type(accepted) is not bool or type(degraded) is not bool:
            return True
        valid_degraded_reason = (
            isinstance(reason, str) and reason in VISUAL_DEGRADED_REASONS
        )
        valid_rejected_reason = (
            isinstance(reason, str) and reason in VISUAL_REJECTED_REASONS
        )
        if accepted:
            if degraded != valid_degraded_reason or (
                not degraded and reason is not None
            ):
                return True
        elif degraded or not valid_rejected_reason:
            return True
        if (await self._accept_inbound(msg_json.get("sequence"))) != "ok":
            return True
        if retired is not None:
            self._retired_visual_ack_sequences.pop(acked, None)
            return True
        if not waiter.done():
            waiter.set_result(
                VisualAckResult(
                    accepted=accepted,
                    degraded=degraded,
                    degraded_reason=reason,
                    sequence=acked,
                    visual_generation=generation,
                )
            )
        return True

    def _forward_lesson_step_ack_telemetry(
        self,
        frame: Dict[str, Any],
        ack_body: Dict[str, Any],
        inbound_sequence: Any,
    ) -> None:
        if frame.get("type") != "lesson_step":
            return
        telemetry = ack_body.get("telemetry")
        telemetry = telemetry if isinstance(telemetry, dict) else {}
        event: Dict[str, Any] = {
            "type": "step_started",
            "sequence": inbound_sequence if isinstance(inbound_sequence, int) and not isinstance(inbound_sequence, bool) else None,
            "stepId": frame.get("stepId"),
            "retryCount": max(0, min(_int_or_default(frame.get("retryCount"), 0), 1000)),
        }
        degraded_reason = telemetry.get("degradedReason")
        explicit_render_degraded = telemetry.get("renderDegraded")
        if isinstance(explicit_render_degraded, bool):
            event["renderDegraded"] = explicit_render_degraded
        elif degraded_reason in {
            "backgroundUnavailable",
            "objectUnavailable",
            "overlayUnavailable",
            "optionalLayerMissing",
        }:
            event["renderDegraded"] = True
        elif degraded_reason == "" and ack_body.get("degraded") is False:
            event["renderDegraded"] = False
        sram_free = _bounded_non_negative_number(telemetry.get("internalMinimumFreeBytes"))
        if sram_free is not None:
            event["sramFreeBytes"] = sram_free
        psram_free = _bounded_non_negative_number(telemetry.get("psramFreeBytes"))
        if psram_free is not None:
            event["psramFreeBytes"] = psram_free
        motion_result = _operations_motion_result(telemetry.get("motionDispatch"))
        if motion_result is not None:
            event["motionDispatch"] = motion_result
        self._forward(event)

    def _legacy_empty_ack_outstanding_seq(self, msg_json: Dict[str, Any], body: Dict[str, Any]) -> Optional[int]:
        if msg_json.get("type") != "lesson_ack":
            return None
        if body:
            return None
        expected_fields = (
            ("assignmentId", self.assignment_id),
            ("sessionId", self.session_id),
            ("lessonId", self.lesson_id),
            ("lessonVersion", self.lesson_version),
        )
        for key, expected in expected_fields:
            if expected is not None and msg_json.get(key) != expected:
                return None
        if len(self._outstanding) != 1:
            return None
        seq = next(iter(self._outstanding))
        self._log("info", f"legacy empty lesson_ack correlated seq={seq}")
        return seq

    async def _on_lesson_progress_impl(self, msg_json: Dict[str, Any]) -> None:
        if not self._is_active_runtime():
            return
        if self.state in (S_FAILED, S_PAUSED, S_COMPLETED):
            return  # terminal is absorbing (e.g. no PROTOCOL_SEQUENCE_ERROR after STEP_TIMEOUT)
        if not self._matches_runtime_identity(msg_json):
            return
        if (await self._accept_inbound(msg_json.get("sequence"))) != "ok":
            return
        body = msg_json.get("body") or {}
        event = body.get("event")
        step_id = msg_json.get("stepId")
        if self.conversation is not None and step_id == self._step_id:
            self._log("info", f"firmware progress ignored for conversational step stepId={step_id}")
            return
        if (
            event == "step_completed"
            and isinstance(step_id, str)
            and step_id in self._completed_step_ids
        ):
            self._log(
                "info",
                f"stale completed step_progress ignored stepId={step_id}",
            )
            return
        if (
            event == "step_completed"
            and step_id == self._step_id
            and not self._step_passive
            and self._step_completed
        ):
            self._log(
                "info",
                f"duplicate interactive step_completed ignored stepId={step_id}",
            )
            return
        if (
            event == "step_completed"
            and step_id == self._step_id
            and not self._step_passive
            and not self._interactive_progress_has_response(body)
        ):
            self._log(
                "info",
                f"interactive step_completed ignored until child response stepId={step_id}",
            )
            return
        # Forward the firmware-observed progress (result->outcome rename owned by the
        # forwarder / post_lesson_event). The wire sequence rides through for dedup.
        self._forward(
            {
                "type": event,
                "sequence": msg_json.get("sequence"),
                "stepId": step_id,
                "stepType": body.get("stepType"),
                "result": body.get("result"),
                "detail": _strip_immediate_scoring_detail(body.get("detail")),
            }
        )
        if event == "step_completed":
            # LATCH-CONTAMINATION GUARD: only the step_completed for the CURRENT
            # in-flight step may set the completion latch. Older/future clients may
            # still send lesson_progress, and a stale progress frame for an
            # already-auto-advanced passive step must not latch the next interactive
            # step. The stepId rides the top-level envelope, so a step_completed whose
            # stepId != self._step_id is a STALE/leftover event:
            # it is still forwarded above (log/observability) but MUST NOT latch.
            if step_id == self._step_id:
                self._step_completed = True
                await self._maybe_finish_step()

    async def on_child_response(self, text: Any, *, source: str = "voice_transcript") -> bool:
        if self.conversation is not None:
            return False
        response = str(text or "").strip()
        if not _has_observable_child_response_value(response):
            return False
        if not self._is_active_runtime():
            return False
        if self.state != S_RUNNING or self._step is None or self._step_id is None:
            return False
        if self._step_passive:
            return False
        if not self._step_acked:
            return False
        if self._renderer_v2_enabled() and not self._step_visuals_ready:
            return False
        if self._step_completed:
            return False
        internal_probe = str(source or "") == "internal_dev_endpoint"
        if not self._child_response_window_open and not internal_probe:
            return False
        step_id = self._step_id
        step_seq = self._step_seq
        if not self._child_response_window_still_current(step_id, step_seq):
            return False
        self._cancel_child_response_timeout()
        self._close_child_response_window()
        if self._uses_authored_cinematic_effects():
            if not await self._apply_authored_cinematic_effect("thinking"):
                return False
        elif self._renderer_v2_enabled():
            await self._apply_authored_visual_then_motion("thinking", None)
        if not self._child_response_window_still_current(step_id, step_seq):
            return False

        expected_responses = _coerce_expected_child_responses(self._step)
        response_intent = _classify_child_response_intent(response, expected_responses)
        if self._uses_safe_speaking():
            branch = {
                CHILD_RESPONSE_INTENT_CORRECT: "correct",
                CHILD_RESPONSE_INTENT_NEAR_MISS: "brave_try",
                CHILD_RESPONSE_INTENT_HELP_OR_REPEAT: "help_or_repeat",
                CHILD_RESPONSE_INTENT_VIETNAMESE_OBJECT: "supported",
            }.get(response_intent, "incorrect")
            return await self._handle_safe_speaking_branch(branch)
        if response_intent != CHILD_RESPONSE_INTENT_CORRECT:
            self._cancel_child_response_timeout()
            self._close_child_response_window()
            if self._renderer_v2_enabled():
                await self._apply_authored_visual_then_motion("incorrect", "incorrect")
            retry_prompt = _child_response_coaching_prompt(
                self._step, expected_responses, response, response_intent
            )
            self._log(
                "info",
                (
                    "interactive child response retry "
                    f"stepId={self._step_id} intent={response_intent} expected={expected_responses}"
                ),
            )
            await self._speak_lesson_prompt_text(
                retry_prompt,
                step_id=self._step_id,
                continue_listening=True,
            )
            if self._renderer_v2_enabled():
                await self._apply_authored_visual_then_motion("retry", None)
            await self._open_child_response_window()
            if self._child_response_window_still_current(self._step_id, self._step_seq):
                self._start_child_response_timeout()
            return True

        self._cancel_child_response_timeout()
        detail = {"recognizedText": response, "source": str(source or "voice_transcript")}
        step_type = self._step.get("type")
        self._forward(
            {
                "type": "step_completed",
                # ESP-generated child-response completions do not have a firmware
                # F->S envelope sequence. Use the negative S->F lesson_step seq as
                # a stable per-step dedup key without colliding with real firmware
                # progress sequences or advancing the backend firmware cursor.
                "sequence": -self._step_seq if isinstance(self._step_seq, int) else None,
                "stepId": self._step_id,
                "stepType": step_type,
                # Wire result is the backend outcome enum. The child voice text stays
                # in detail and is stripped at the ESP->backend boundary.
                "result": "success",
                "detail": detail,
            }
        )
        self._log("info", f"interactive child response accepted stepId={self._step_id}")
        # The robot's OWN observation that the step completed, in the shared progress
        # vocabulary -- the runtime is the robot side of the wire, which is exactly why
        # the checkpoint contract rejects `backend` echoes as device-side evidence
        # (F-T53-14). Logged AFTER the acceptance that causes it: the child answering is
        # the event, this is its consequence, and recording them the other way round
        # reads as progress that preceded the answer.
        self._log(
            "info",
            "lesson_progress step_completed "
            f"stepId={self._step_id} result=success stepType={step_type or ''}",
        )
        self._close_child_response_window()
        self._step_completed = True
        if self._renderer_v2_enabled():
            await self._apply_authored_visual_then_motion("correct", "correct")
        success_prompt = _child_response_success_prompt(self._step, expected_responses)
        if success_prompt is not None:
            await self._speak_lesson_prompt_text(
                success_prompt,
                step_id=self._step_id,
                continue_listening=False,
            )
            # Let the cheer / end-of-lesson ceremony finish before advancing or stop.
            await self._wait_lesson_prompt_idle()
        await self._maybe_finish_step()
        return True

    async def on_child_response_failure(self, reason: str = "stt_failure") -> bool:
        """Typed no-transcript hook for voice providers and timeout integrations."""
        if not self._uses_safe_speaking():
            return False
        if self._renderer_v2_enabled() and not self._step_visuals_ready:
            return False
        if not self._child_response_window_open or not self._child_response_window_still_current(
            self._step_id, self._step_seq
        ):
            return False
        branch = "silence" if str(reason or "").lower() in {"silence", "no_speech", "timeout"} else "stt_failure"
        return await self._handle_safe_speaking_branch(branch)

    def _uses_safe_speaking(self) -> bool:
        interaction = (self._step or {}).get("interaction")
        return (
            self._lesson_rollout_control_enabled("playful_interactions_enabled")
            and isinstance(interaction, dict)
            and interaction.get("template") == "safeSpeaking"
        )

    def _lesson_rollout_control_enabled(self, key: str) -> bool:
        config = getattr(self.conn, "config", {})
        lesson_cfg = config.get("lesson", {}) if isinstance(config, dict) else {}
        if not isinstance(lesson_cfg, dict) or lesson_cfg.get(key) is not True:
            return False
        allowlist = lesson_cfg.get("rollout_device_allowlist") or []
        if not allowlist:
            return False
        device_id = str(getattr(self.conn, "device_id", "") or "").strip().lower()
        return device_id in allowlist

    def _safe_speaking(self) -> SafeSpeakingSession:
        if self._safe_speaking_session is None:
            interaction = (self._step or {}).get("interaction") or {}
            expected = _coerce_expected_child_responses(self._step)
            self._safe_speaking_session = SafeSpeakingSession(
                max_attempts=interaction.get("maxAttempts", 3),
                target_word=_target_vocab_word(expected, self._step),
            )
        return self._safe_speaking_session

    def _cinematic_cue(
        self, effect: str, *, step_key: str | None = None
    ) -> dict[str, Any] | None:
        if self._renderer_v5_enabled():
            phase_id = {
                "opening": "flyIn",
                "greet": "walk",
                "retry-level-1": "thinking",
                "retry-level-2": "thinking",
                "retry-level-3": "thinking",
                "correct": "celebrate",
            }.get(effect, effect)
            return self._layered_cinematic_phases.get(phase_id)
        key = step_key if isinstance(step_key, str) and step_key else self._step_id
        if not isinstance(key, str):
            return None
        return self._cinematic_cues_by_key.get((key, effect))

    def _uses_authored_cinematic_effects(self) -> bool:
        return self._renderer_v5_enabled() or (
            self._renderer_v4_enabled() and bool(self._conversation_cues)
        )

    def _layered_cinematic_phase_for_step(
        self, step: dict[str, Any]
    ) -> dict[str, Any] | None:
        step_id = step.get("id")
        if isinstance(step_id, str):
            authored = self._layered_cinematic_step_phases.get(step_id)
            if isinstance(authored, dict):
                return authored
        scene = step.get("scene")
        overlay = scene.get("robotOverlay") if isinstance(scene, dict) else None
        asset = overlay.get("asset") if isinstance(overlay, dict) else None
        source = asset.get("src") if isinstance(asset, dict) else None
        if not isinstance(source, str) or not source:
            atlas = overlay.get("atlas") if isinstance(overlay, dict) else None
            source = atlas.get("image") if isinstance(atlas, dict) else None
        if not isinstance(source, str) or not source:
            return None
        resolver = getattr(self.asset_cache, "local_pack_url_for_source", None)
        resolved = resolver(source) if callable(resolver) else None
        target_path = resolved or source
        for phase in self._layered_cinematic_phases.values():
            for layer in phase.get("layers", []) or []:
                if (
                    isinstance(layer, dict)
                    and layer.get("slot") == "robotOverlay"
                    and layer.get("sdPath") == target_path
                ):
                    return phase
        return None

    def _validate_safe_speaking_cinematic_routes(self) -> None:
        safe_steps = [
            step
            for step in self._steps
            if isinstance(step.get("interaction"), dict)
            and step["interaction"].get("template") == "safeSpeaking"
        ]
        required = {
            "listen",
            "thinking",
            "retry-level-1",
            "retry-level-2",
            "retry-level-3",
            "correct",
            "celebrate",
        }
        missing: list[str] = []
        for index, step in enumerate(safe_steps):
            step_key = step.get("id")
            if not isinstance(step_key, str):
                continue
            effects = required | ({"opening", "greet"} if index == 0 else set())
            missing.extend(
                f"{step_key}-{effect}"
                for effect in sorted(effects)
                if self._cinematic_cue(effect, step_key=step_key) is None
            )
        if missing:
            self.last_error = LessonError(
                "CINEMATIC_PHASE_ROUTE_MISSING",
                f"missing authored cinematic cues: {', '.join(missing)}",
                retryable=False,
            )
            raise self.last_error

    async def _apply_authored_cinematic_effect(self, effect: str) -> bool:
        cue = self._cinematic_cue(effect)
        if not isinstance(cue, dict) or self._authored_cinematic_pending is not None:
            return False
        future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        sequence = self._seq + 1
        self._cinematic_phase = cue
        identity_field = "phaseId" if "phaseId" in cue else "cueId"
        pending = {
            "stage": "prepare",
            "sequence": sequence,
            "identityField": identity_field,
            identity_field: cue[identity_field],
            "stepId": self._step_id,
            "future": future,
        }
        self._authored_cinematic_pending = pending
        try:
            emitted = await self._emit(
                "lesson_prepare",
                step_id=self._step_id,
                body={
                    "profile": self.profile,
                    "cinematicPhase": {"command": "prepare", **copy.deepcopy(cue)},
                },
            )
            if emitted != sequence:
                self._authored_cinematic_pending = None
                return False
            return await future
        except BaseException:
            if self._authored_cinematic_pending is pending:
                self._authored_cinematic_pending = None
            if not future.done():
                future.cancel()
            raise

    def _retire_authored_cinematic_pending(self, *, result: bool = False) -> bool:
        pending = self._authored_cinematic_pending
        self._authored_cinematic_pending = None
        if not isinstance(pending, dict):
            return False
        future = pending.get("future")
        if isinstance(future, asyncio.Future) and not future.done():
            future.set_result(result)
        return True

    def _queue_authored_cinematic_sequence(self, effects: list[str]) -> None:
        previous = self._visual_transition_task
        if previous is not None and not previous.done():
            previous.cancel()
        step_id = self._step_id
        step_seq = self._step_seq

        async def run() -> None:
            for effect in effects:
                if not await self._apply_authored_cinematic_effect(effect):
                    return
            await self._continue_after_step_visuals(step_id, step_seq)

        self._visual_transition_task = asyncio.create_task(run())

    async def _handle_safe_speaking_branch(self, branch: str) -> bool:
        self._cancel_child_response_timeout()
        self._close_child_response_window()
        decision = self._safe_speaking().decide(branch)
        visual_state = {
            "correct": "correct",
            "brave_try": "nearMiss",
            "supported": "correct",
            "modeled": "retry",
        }.get(decision.outcome, "incorrect")
        if self._uses_authored_cinematic_effects():
            if decision.outcome in {"correct", "supported"}:
                await self._apply_authored_cinematic_effect("correct")
            elif decision.outcome == "brave_try":
                await self._apply_authored_cinematic_effect("retry-level-1")
            else:
                level = max(1, min(3, self._safe_speaking().attempts))
                await self._apply_authored_cinematic_effect(f"retry-level-{level}")
        elif self._renderer_v2_enabled():
            await self._apply_authored_visual_then_motion(
                visual_state, decision.motion_slot
            )
        else:
            self._dispatch_step_motion(decision.motion_slot)
        await self._speak_lesson_prompt_text(
            decision.prompt,
            step_id=self._step_id,
            continue_listening=not decision.advance,
        )
        if not decision.advance:
            if self._renderer_v2_enabled():
                await self._apply_authored_visual_then_motion("retry", None)
            await self._open_child_response_window()
            if self._child_response_window_still_current(self._step_id, self._step_seq):
                self._start_child_response_timeout()
            return True

        self._forward_safe_speaking_completion(decision.result, decision.outcome)
        self._forward_story_progress()
        self._step_completed = True
        await self._wait_lesson_prompt_idle()
        if (
            self._uses_authored_cinematic_effects()
            and decision.outcome in {"correct", "supported"}
        ):
            await self._apply_authored_cinematic_effect("celebrate")
        await self._maybe_finish_step()
        return True

    def _forward_safe_speaking_completion(self, result: str, response_class: str) -> None:
        interaction = (self._step or {}).get("interaction") or {}
        pattern = interaction.get("funPattern")
        if pattern not in FUN_PATTERN_PROMPTS:
            pattern = "copyMyMove"
        self._forward(
            {
                "type": "step_completed",
                "sequence": -self._step_seq if isinstance(self._step_seq, int) else None,
                "stepId": self._step_id,
                "stepType": (self._step or {}).get("type"),
                "result": result if result in {"success", "miss", "timeout"} else "miss",
                "detail": {
                    "responseClass": response_class,
                    "interactionTemplate": "safeSpeaking",
                    "attempts": self._safe_speaking().attempts,
                    "funPattern": pattern,
                },
                "totalAttempts": self._safe_speaking().attempts,
            }
        )

    def _forward_story_progress(self) -> None:
        story = (self._step or {}).get("storyBeat")
        if not isinstance(story, dict):
            return
        def bounded(value: Any) -> Optional[str]:
            if not isinstance(value, str):
                return None
            value = value.strip()
            return value[:128] if value else None

        event = {
            "type": "story_progress",
            # Backend dedupe keys include event sequence. Keep storyline beats in a
            # stable negative namespace that cannot collide with step_completed's
            # legacy ``-step_seq`` synthetic sequence.
            "sequence": -(
                1_000_000
                + (self._step_seq if isinstance(self._step_seq, int) else self._step_index + 1)
            ),
            "stepId": self._step_id,
            "petReaction": bounded(story.get("successReaction")),
            "unitGrowth": bounded(story.get("unitGrowth") or story.get("unitProgress")),
            "nextTease": bounded(story.get("nextTease")),
        }
        if any(value is not None for key, value in event.items() if key not in {"type", "stepId"}):
            self._forward(event)

    def _dispatch_step_motion(self, slot: str) -> None:
        motion = (self._step or {}).get("motion")
        if not isinstance(motion, dict):
            return
        preset = motion.get(slot)
        if not isinstance(preset, str):
            return
        if not self._lesson_rollout_control_enabled("motion_presets_enabled"):
            self._log("info", f"lesson_motion_dispatch outcome=disabled preset={preset}")
            return
        self._motion_generation += 1
        generation = self._motion_generation
        step_id = self._step_id
        step_seq = self._step_seq
        previous = self._motion_task
        if previous is not None and not previous.done():
            previous.cancel()

        async def run_serialized_motion() -> None:
            if previous is not None:
                await asyncio.gather(previous, return_exceptions=True)
            if (
                self._closed
                or generation != self._motion_generation
                or step_id != self._step_id
                or step_seq != self._step_seq
            ):
                return
            try:
                dispatched = await dispatch_motion_preset(self.conn, preset)
                if dispatched:
                    self._log("info", f"lesson_motion_dispatch outcome=applied preset={preset}")
                else:
                    self._log("warning", f"lesson_motion_dispatch outcome=failed preset={preset}")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._log(
                    "warning",
                    f"lesson_motion_dispatch outcome=failed preset={preset} error={type(exc).__name__}",
                )

        self._motion_task = asyncio.create_task(run_serialized_motion())

    async def _on_lesson_error_impl(self, msg_json: Dict[str, Any]) -> None:
        if not self._is_active_runtime():
            return
        if not self._matches_runtime_identity(msg_json):
            return
        # lesson_error rides the same F->S sequence stream but is a status report (not
        # acked). Count it so a later ack/progress is not mis-flagged as a gap
        # (symmetric accounting with on_lesson_ack/on_lesson_progress).
        seq = msg_json.get("sequence")
        if isinstance(seq, int) and seq > self._last_inbound_sequence:
            self._last_inbound_sequence = seq
        if self.state in (S_FAILED, S_PAUSED, S_COMPLETED):
            return
        body = msg_json.get("body") or {}
        code = body.get("code")
        self.last_error = LessonError(
            code or "LESSON_ERROR", body.get("message") or "", retryable=bool(body.get("retryable"))
        )
        self._log("warning", f"inbound lesson_error code={code}")
        # T2.1: HONOR THE WIRE `retryable` FLAG. The firmware flags transient
        # conditions (LESSON_ASSET_MUTATION_ACTIVE, LESSON_SESSION_CONFLICT, ...)
        # as retryable: true; killing the run on the first such report throws away
        # a recoverable lesson. Defer to whichever bounded recovery mechanism is
        # ALREADY in flight (frame-ack retry/timeout, step timeout, passive dwell,
        # child-response timeout) — each of those ends in a terminal verdict, so
        # the state machine can never be wedged by deferring here. With NOTHING in
        # flight there is no bound, so a retryable report still fails terminally.
        if self.last_error.retryable and self._bounded_recovery_in_flight():
            self._log(
                "warning",
                f"retryable inbound lesson_error deferred to in-flight recovery code={code}",
            )
            return
        # A firmware-reported error on the active step fails the run (slice scope).
        if self.state in (S_RUNNING, S_PRELOADING):
            self.state = S_FAILED
            self._cancel_visual_waiters(increment_generation=True, reason="lessonError")
            self._cancel_step_timeout()
            self._cancel_child_response_timeout()
            await self._notify_lesson_terminal("lesson_error")

    def _bounded_recovery_in_flight(self) -> bool:
        """True when a timer that ALWAYS ends in a terminal verdict is armed.

        Used by :meth:`on_lesson_error` to decide whether a ``retryable`` inbound
        error can be deferred without risking a run that hangs forever.
        """
        for task in (
            self._frame_ack_timeout_task,
            self._step_timeout_task,
            self._passive_dwell_task,
            self._child_response_timeout_task,
        ):
            if task is not None and not task.done():
                return True
        return False

    # ── state machine ──────────────────────────────────────────────────────────

    async def _on_frame_acked(self, frame: Dict[str, Any], ack_body: Dict[str, Any]) -> None:
        if not self._is_active_runtime():
            return
        ftype = frame.get("type")
        if ftype == "lesson_prepare":
            prepare_command = self._cinematic_frame_command(frame)
            authored_pending = self._authored_cinematic_pending
            authored_identity_field = (
                authored_pending.get("identityField")
                if isinstance(authored_pending, dict)
                else None
            )
            if (
                isinstance(prepare_command, dict)
                and prepare_command.get("command") == "prepare"
                and isinstance(authored_pending, dict)
                and authored_identity_field in {"cueId", "phaseId"}
                and authored_pending.get("stage") == "prepare"
                and authored_pending.get("sequence")
                == prepare_command.get("commandSequenceId")
                and authored_pending.get(authored_identity_field)
                == prepare_command.get(authored_identity_field)
                and authored_pending.get("stepId") == self._step_id
            ):
                authored_pending["stage"] = "start"
                authored_pending["sequence"] = self._seq + 1
                try:
                    await self._emit(
                        "lesson_cinematic_control",
                        step_id=self._step_id,
                        body={
                            "command": "start",
                            authored_identity_field: prepare_command[authored_identity_field],
                        },
                    )
                except asyncio.CancelledError:
                    self._retire_authored_cinematic_pending()
                    raise
                except Exception as exc:
                    self._retire_authored_cinematic_pending()
                    await self._fail_conversation_start_send(
                        cue_id=prepare_command[authored_identity_field],
                        step_id=self._step_id,
                        exc=exc,
                    )
                return
            pending_visual = self._conversation_pending_visual
            if (
                isinstance(prepare_command, dict)
                and prepare_command.get("command") == "prepare"
                and prepare_command.get("templateVersion") == 2
                and isinstance(prepare_command.get("cueId"), str)
                and isinstance(pending_visual, dict)
                and pending_visual.get("stage") == "prepare"
                and pending_visual.get("sequence")
                == prepare_command.get("commandSequenceId")
            ):
                await self._on_conversation_visual_prepared(frame)
                return
            if self._use_sd_asset_pack() and not self._ack_reports_asset_pack_ready(ack_body):
                self.last_error = LessonError(
                    ASSET_PACK_NOT_READY,
                    "device did not report verified SD asset pack before lesson start",
                    retryable=True,
                )
                self.state = S_FAILED
                await self._emit_error(self.last_error)
                await self._notify_lesson_terminal("asset_pack_not_ready")
                return
            if self._use_sd_asset_pack():
                self.state = S_READY
                self._forward({"type": "preload_ready"})
                if isinstance(self.manifest.get("openingEntrance"), dict):
                    self._forward_phase("entrance")
                await self._emit("lesson_start", body=self._start_body())
                return
            # Prepare delivered -> begin the download+verify (D-PRELOAD-OWNER).
            self._preload_task = asyncio.create_task(self._run_preload())
        elif ftype == "lesson_start":
            self.state = S_RUNNING
            self._forward({"type": "lesson_started", "startedAt": _wire_timestamp()})
            await self._emit_step()
        elif ftype == "lesson_cinematic_control":
            command = (frame.get("body") or {}).get("command")
            if command == "start":
                authored_pending = self._authored_cinematic_pending
                frame_command = self._cinematic_frame_command(frame)
                authored_identity_field = (
                    authored_pending.get("identityField")
                    if isinstance(authored_pending, dict)
                    else None
                )
                if (
                    isinstance(authored_pending, dict)
                    and authored_pending.get("stage") == "start"
                    and isinstance(frame_command, dict)
                    and authored_identity_field in {"cueId", "phaseId"}
                    and authored_pending.get("sequence")
                    == frame_command.get("commandSequenceId")
                    and authored_pending.get(authored_identity_field)
                    == frame_command.get(authored_identity_field)
                    and authored_pending.get("stepId") == self._step_id
                ):
                    self._retire_authored_cinematic_pending(result=True)
                else:
                    await self._on_conversation_visual_acked(frame)
            elif command == "pause":
                self.state = S_PAUSED
                self._cancel_visual_waiters(increment_generation=False, reason="paused")
                self._forward_phase("paused")
            elif command == "resume":
                self.state = S_RUNNING
                self._forward_phase("resumed")
                await self._apply_deferred_cinematic_step_ack()
            elif command == "cancel":
                self.state = S_COMPLETED
                self._cancel_visual_waiters(increment_generation=True, reason="cancelled")
                self._cancel_step_timeout()
                self._cancel_child_response_timeout()
                self._clear_cinematic_state()
        elif ftype == "lesson_step":
            # Step delivery is confirmed ONLY by its ack (plan §5.8) -> clear timeout.
            self._cancel_step_timeout()
            self._step_acked = True
            if self._step is None:
                return
            step_id = self._step_id
            step_seq = self._step_seq
            if self._renderer_v2_enabled():
                transitions = [(self._authored_step_visual_state(), "present")]
                if not self._step_passive:
                    transitions.append(("listen", "listen"))
                visual_generation = self._visual_generation + len(transitions)
                assignment_id = self.assignment_id
                session_id = self.session_id

                async def continue_after_visuals() -> None:
                    await self._continue_after_step_visuals(
                        step_id,
                        step_seq,
                        visual_generation=visual_generation,
                        assignment_id=assignment_id,
                        session_id=session_id,
                    )

                self._queue_authored_visual_sequence(
                    transitions, after=continue_after_visuals
                )
                return
            self._dispatch_step_motion("present")
            self._step_visuals_ready = True
            self._forward_phase("teaching")
            await self._continue_after_step_visuals(step_id, step_seq)
        elif ftype == "lesson_stop":
            reason = (frame.get("body") or {}).get("reason")
            if reason != "COMPLETED":
                self.state = S_COMPLETED
                self._cancel_visual_waiters(increment_generation=True, reason="lessonAbandoned")
                if self._cinematic_enabled():
                    self._clear_cinematic_state()
                self._forward_phase("abandoned")
                self._forward(
                    {
                        "type": "lesson_abandoned",
                        "stepId": self._step_id,
                        "stepType": (self._step or {}).get("type"),
                        "reason": str(reason or "stopped").lower(),
                        "abandonedAt": _wire_timestamp(),
                    }
                )
                await self._notify_lesson_terminal("lesson_abandoned")
                return
            self.state = S_COMPLETED
            self._cancel_visual_waiters(increment_generation=True, reason="lessonStopped")
            self._log("info", f"lesson_completed stepsCompleted={self._steps_completed}")
            self._forward_phase("completed")
            self._forward(
                {
                    "type": "lesson_completed",
                    "completedAt": _wire_timestamp(),
                    "summary": {"stepsCompleted": self._steps_completed},
                }
            )
            await self._notify_lesson_terminal("lesson_completed")
            self._start_terminal_readback()

    async def _continue_after_step_visuals(
        self,
        step_id: Optional[str],
        step_seq: Optional[int],
        *,
        visual_generation: Optional[int] = None,
        assignment_id: Any = None,
        session_id: Optional[str] = None,
    ) -> None:
        def continuation_is_current() -> bool:
            if (
                not self._is_active_runtime()
                or self.state != S_RUNNING
                or step_id != self._step_id
                or step_seq != self._step_seq
                or not self._step_acked
                or self._step is None
            ):
                return False
            if visual_generation is None:
                return True
            return self._visual_transition_is_current(
                visual_generation,
                assignment_id=assignment_id,
                session_id=session_id,
                step_id=step_id,
            )

        if not continuation_is_current():
            return
        self._step_visuals_ready = True
        if self.conversation is not None:
            await self._publish_conversation_tool_context()
            return
        prompt_handed_off = await self._speak_step_prompt(self._step)
        if not continuation_is_current():
            return
        if self._step_passive:
            if prompt_handed_off:
                await self._wait_lesson_prompt_idle()
                if (
                    not continuation_is_current()
                    or not self._step_passive
                ):
                    return
            dwell = self._passive_dwell_sec()
            if dwell > 0:
                self._start_passive_dwell(step_seq, step_id, dwell)
                return
            self._complete_passive_step()
        else:
            if not self._renderer_v2_enabled():
                self._dispatch_step_motion("listen")
                self._forward_phase("listening")
            await self._open_child_response_window()
            if self._child_response_window_still_current(step_id, step_seq):
                self._start_child_response_timeout()
        await self._maybe_finish_step()

    async def _notify_lesson_terminal(self, reason: str) -> None:
        if self._is_pre_activation_fallback_candidate():
            self._log(
                "warning",
                f"candidate terminal side effects suppressed before activation reason={reason}",
            )
            return
        # Only real terminal states may leave LESSON mode. Non-terminal callers
        # (historically online-fallback miswired as a notify) must not kick the
        # child out mid-start.
        if self.state not in (S_FAILED, S_COMPLETED, S_PAUSED):
            self._log(
                "warning",
                f"ignoring non-terminal lesson notify reason={reason} state={self.state}",
            )
            return
        if self.state in (S_FAILED, S_COMPLETED):
            self._retire_authored_cinematic_pending()
        if self.state == S_PAUSED:
            self._cancel_visual_waiters(increment_generation=False, reason="paused")
        elif self._visual_ack_waiters:
            self._cancel_visual_waiters(increment_generation=True, reason=reason)
        # Every S_FAILED path routes through here. Forward ONE durable terminal
        # lesson_failed (the forwarder classifies it terminal -> stored + reconnect
        # -replayed) so the backend assignment leaves its single-active slot and
        # persists the failure. lesson_completed/lesson_abandoned forward their own
        # terminal events at their call sites; FAILED had none. The forward happens
        # BEFORE the release hook so a connection without release_lesson_mode still
        # reports the failure.
        if self.state == S_FAILED and not self._failure_forwarded:
            self._failure_forwarded = True
            self._forward_phase("failed")
            self._forward(
                {
                    "type": "lesson_failed",
                    "reason": reason,
                    "code": getattr(self.last_error, "code", None),
                    "stepId": self._step_id,
                    "failedAt": _wire_timestamp(),
                }
            )
        # Terminal states should never strand the lesson layer in LESSON mode or drop
        # the child silently into DORMANT. Prefer the newer finish hook for every
        # terminal so the connection can show a visible face cue and restore Live
        # conversation; fall back to the legacy dormant release hook on older conns.
        finish = getattr(self.conn, "finish_lesson_mode", None)
        if callable(finish):
            try:
                await finish(reason=reason)
                return
            except Exception as exc:  # pragma: no cover - finish is best-effort
                self._log("warning", f"lesson finish mode transition failed: {type(exc).__name__}")
        release = getattr(self.conn, "release_lesson_mode", None)
        if not callable(release):
            return
        try:
            await release(reason=reason)
        except Exception as exc:  # pragma: no cover - orchestrator release is best-effort
            self._log("warning", f"lesson terminal mode release failed: {type(exc).__name__}")

    def _alarm_preload(self, active: bool) -> None:
        """Best-effort bracket of the preload window for the S13 voice-latency alarm.
        Never raises into the lesson run (the alarm is observability, not a gate)."""
        if self._alarm is None:
            return
        try:
            self._alarm.set_preload_active(active)
        except Exception:  # pragma: no cover - alarm is best-effort
            pass

    def _start_preload_status_reports(self, status: Dict[str, Any]) -> None:
        reporter = self.preload_status_reporter
        if not callable(reporter):
            return
        for asset in status.get("assets", []) or []:
            if not isinstance(asset, dict) or asset.get("critical") is not True:
                continue
            state = asset.get("state")
            if state not in ("READY", "FAILED"):
                continue
            asset_id = asset.get("assetId") or asset.get("key") or asset.get("id")
            if not asset_id:
                continue
            report: Dict[str, Any] = {
                "assignmentId": self.assignment_id,
                "assetId": asset_id,
                "state": state,
            }
            if asset.get("checksumOk") is not None:
                report["checksumOk"] = bool(asset.get("checksumOk"))
            task = asyncio.create_task(self._send_preload_status_report(reporter, report))
            self._preload_status_report_tasks.add(task)
            task.add_done_callback(self._preload_status_report_tasks.discard)

    async def _send_preload_status_report(
        self,
        reporter: Callable[[Dict[str, Any]], Awaitable[Any]],
        report: Dict[str, Any],
    ) -> None:
        try:
            await reporter(report)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - telemetry must not gate lesson flow
            self._log("warning", f"preload status report failed: {type(exc).__name__}")

    async def _run_preload(self) -> None:
        # The "active preload window" the S13 alarm measures against is exactly the
        # download phase. Bracket only that await (the ready/start logic below is not
        # downloading); finally guarantees the window closes on every exit, incl.
        # cancellation during teardown.
        self._alarm_preload(True)
        try:
            ready = await self.asset_cache.preload()
        except LessonError as err:
            # ASSET_CHECKSUM_MISMATCH / ASSET_PROFILE_UNAVAILABLE / PRELOAD_TIMEOUT.
            self.last_error = err
            self.state = S_FAILED
            self._log("error", f"preload failed: {err.code}")
            await self._emit_error(err)
            await self._notify_lesson_terminal("preload_failed")
            return
        except asyncio.CancelledError:  # pragma: no cover - teardown
            raise
        except Exception as exc:  # pragma: no cover - unexpected
            self._log("error", f"preload crashed: {type(exc).__name__}")
            self.state = S_FAILED
            await self._notify_lesson_terminal("preload_crashed")
            return
        finally:
            self._alarm_preload(False)

        if not self._is_active_runtime():
            return
        # ESP synthesizes lesson_preload_status from its OWN cache; ready is THE gate.
        status = self.asset_cache.synthesize_preload_status(self.assignment_version)
        self._start_preload_status_reports(status)
        if self._preload_status_report_tasks:
            await asyncio.sleep(0)
        if not ready or not status.get("ready"):
            # A non-mismatch shortfall (e.g. critical network failure) leaves it not
            # ready; lesson_start is gated below and will not fire.
            self._log("info", "preload not ready; lesson_start gated")
            return

        self.state = S_READY
        self._forward({"type": "preload_ready"})
        if isinstance(self.manifest.get("openingEntrance"), dict):
            self._forward_phase("entrance")
        # Start gate satisfied -> now (and only now) emit lesson_start (seq 2).
        await self._emit("lesson_start", body=self._start_body())

    async def _preload_sd_asset_pack_before_prepare(self) -> bool:
        """Materialize verified SD-pack files before asking firmware to attest them."""
        self._alarm_preload(True)
        try:
            ready = await self.asset_cache.preload()
        except LessonError as err:
            self.last_error = err
            self.state = S_FAILED
            self._log("error", f"sd asset pack preload failed: {err.code}")
            await self._emit_error(err)
            await self._notify_lesson_terminal("sd_asset_pack_preload_failed")
            return False
        except asyncio.CancelledError:  # pragma: no cover - teardown
            raise
        except Exception as exc:  # pragma: no cover - unexpected
            self.last_error = LessonError(
                ASSET_PACK_MATERIALIZE_FAILED,
                "ESP could not materialize verified SD asset pack",
                retryable=True,
            )
            self.state = S_FAILED
            self._log("error", f"sd asset pack preload crashed: {type(exc).__name__}")
            await self._emit_error(self.last_error)
            await self._notify_lesson_terminal("sd_asset_pack_preload_crashed")
            return False
        finally:
            self._alarm_preload(False)

        status = self.asset_cache.synthesize_preload_status(self.assignment_version)
        self._start_preload_status_reports(status)
        if self._preload_status_report_tasks:
            await asyncio.sleep(0)
        if not ready or not status.get("ready"):
            self.last_error = LessonError(
                ASSET_PACK_NOT_READY,
                "verified SD asset pack is not ready for this lesson",
                retryable=True,
            )
            self.state = S_FAILED
            self._log("warning", "sd asset pack not ready; refusing online fallback")
            await self._emit_error(self.last_error)
            await self._notify_lesson_terminal("sd_asset_pack_not_ready")
            return False
        if not await self._sync_sd_asset_pack_to_robot():
            if not (
                self.last_error is not None
                and self.last_error.code == "SD_SYNC_REALTIME_BUSY_TIMEOUT"
            ):
                self.last_error = LessonError(
                    ASSET_PACK_NOT_READY,
                    "robot did not attest the exact SD asset pack for this lesson",
                    retryable=True,
                )
            self.state = S_FAILED
            self._log(
                "warning",
                f"robot SD sync not attested code={self.last_error.code}; refusing online fallback",
            )
            await self._emit_error(self.last_error)
            await self._notify_lesson_terminal("sd_asset_pack_sync_failed")
            return False
        return True

    async def _emit_step(self) -> None:
        if not self._is_active_runtime():
            return
        # P5: advance the cursor and emit the NEXT renderable step. The first call
        # (from lesson_start ack) moves -1 -> 0; subsequent calls (from a completed
        # step) move 0 -> 1 -> ... Each emission resets the per-step ack/completed
        # latches so one step's completion can never satisfy the next.
        if self._step_index < 0 and not self._steps:
            # No renderable step in the whole manifest -> fail before any lesson_step.
            self.last_error = LessonError("LESSON_STEP_MISSING", "no model step in manifest")
            self.state = S_FAILED
            self._log("error", "no renderable model step found in manifest")
            return
        if self._renderer_v2_enabled() and self._step is not None:
            transition = self._visual_transition_task
            if transition is not None and not transition.done():
                transition.cancel()
            self._visual_transition_task = None
            self._cancel_visual_waiters(increment_generation=True, reason="stepReplaced")
        self._step_index += 1
        self._cancel_child_response_timeout()
        self._cancel_passive_dwell()
        step = self._steps[self._step_index]
        self._step = step
        self._step_passive = _is_passive_step(step)
        self._step_id = step.get("id")
        self._step_acked = False
        self._step_visuals_ready = False
        self._step_completed = False
        self._child_response_window_open = False
        self._child_response_timeout_count = 0
        self._safe_speaking_session = None
        if self._renderer_v4_enabled():
            self._semantic_step_sequence += 1
            self._step_seq = self._semantic_step_sequence
            self._step_acked = True
            self._step_visuals_ready = True
            self._bind_conversation_for_current_step()
            await self._publish_conversation_tool_context()
            if self.conversation is None and self._uses_safe_speaking():
                self._step_visuals_ready = False
                effects = ["listen"]
                if self._step_index == 0 and self._cinematic_cue("greet") is not None:
                    effects.insert(0, "greet")
                self._queue_authored_cinematic_sequence(effects)
            return
        if self._renderer_v5_enabled():
            self._semantic_step_sequence += 1
            self._step_seq = self._semantic_step_sequence
            self._step_acked = True
            self._step_visuals_ready = True
            self._bind_conversation_for_current_step()
            await self._publish_conversation_tool_context()
            phase = self._layered_cinematic_phase_for_step(step)
            if isinstance(phase, dict):
                phase_id = phase.get("phaseId")
                current_id = (
                    self._cinematic_phase.get("phaseId")
                    if isinstance(self._cinematic_phase, dict)
                    else None
                )
                if isinstance(phase_id, str) and not (
                    self._step_index == 0 and phase_id == current_id
                ):
                    self._step_visuals_ready = False
                    self._queue_authored_cinematic_sequence([phase_id])
                    return
            await self._continue_after_step_visuals(self._step_id, self._step_seq)
            return
        self._bind_conversation_for_current_step()
        await self._publish_conversation_tool_context()
        raw_timeout_sec = step.get("timeoutSec") or self._default_step_timeout_sec
        try:
            timeout_sec = max(float(raw_timeout_sec), self._min_step_timeout_sec)
            if not math.isfinite(timeout_sec):
                raise ValueError
        except (TypeError, ValueError):
            timeout_sec = max(float(self._default_step_timeout_sec), self._min_step_timeout_sec)
        body = self._step_body(step)
        body["timeoutSec"] = timeout_sec
        invalid_reason = self._invalid_lesson_step_scene_reason(body.get("scene"))
        if invalid_reason is not None:
            await self._fail_invalid_step_frame(self._step_id, invalid_reason)
            return
        if self._frame_payload_size("lesson_step", step_id=self._step_id, body=body) > MAX_LESSON_FRAME_BYTES:
            await self._fail_oversized_frame("lesson_step", self._step_id)
            return
        self._step_seq = await self._emit("lesson_step", step_id=self._step_id, body=body)
        if self.state == S_FAILED:
            return
        # Remembered so resume() can re-arm the SAME deadline for a step whose
        # pause outlived its original timer (see resume()).
        self._step_timeout_sec = timeout_sec
        self._start_step_timeout(self._step_seq, self._step_id, timeout_sec)

    async def _speak_step_prompt(self, step: Dict[str, Any]) -> bool:
        prompt = _spoken_step_prompt(step)
        if prompt is None:
            return False
        return await self._speak_lesson_prompt_text(
            prompt,
            step_id=self._step_id,
            continue_listening=not self._step_passive,
        )

    async def _speak_lesson_prompt_text(
        self,
        prompt: str,
        *,
        step_id: Optional[str] = None,
        continue_listening: bool = False,
    ) -> bool:
        if not self._is_active_runtime():
            return False
        provider = getattr(self.conn, "voice_provider", None)
        speaker = getattr(provider, "speak_lesson_step_prompt", None)
        if not callable(speaker):
            return False
        try:
            try:
                handed_off = bool(
                    await speaker(prompt, continue_listening=continue_listening)
                )
            except TypeError as exc:
                if "continue_listening" not in str(exc):
                    raise
                handed_off = bool(await speaker(prompt))
            self._log(
                "info" if handed_off else "warning",
                f"lesson step prompt handoff stepId={step_id or self._step_id or ''} "
                f"handoff={int(handed_off)} "
                # The prompt TEXT, so what the robot actually asked is verifiable: a
                # guiding question ("Can you say barn?") is pedagogically different from
                # a bare command ("Say barn"), and with only a step id nothing
                # downstream could tell which the child received. This is the robot's
                # own scripted lesson content, never child speech.
                f'text="{_norm_prompt_for_log(prompt)}" '
                # The runtime KNOWS whether the device had acked the render when it
                # spoke -- the handoff is gated on it. Nothing downstream could tell:
                # the ack and the prompt share a wire sequence and come from different
                # streams, and the device writes its serial line after sending, so the
                # ack routinely lands after a prompt that in fact followed it. Recording
                # the fact replaces an inference that could not be made.
                f"afterRenderAck={int(bool(self._step_acked))}",
            )
            return handed_off
        except Exception as exc:  # pragma: no cover - voice prompt is best-effort
            self._log(
                "warning",
                f"lesson step prompt voice handoff failed stepId={step_id or self._step_id or ''}: {type(exc).__name__}",
            )
            return False

    async def _wait_lesson_prompt_idle(self) -> None:
        provider = getattr(self.conn, "voice_provider", None)
        waiter = getattr(provider, "wait_lesson_step_prompt_idle", None)
        if not callable(waiter):
            return
        try:
            await waiter()
        except Exception as exc:  # pragma: no cover - prompt idle wait is best-effort
            self._log(
                "warning",
                f"lesson step prompt idle wait failed stepId={self._step_id or ''}: {type(exc).__name__}",
            )

    async def _maybe_finish_step(self) -> None:
        if not self._is_active_runtime():
            return
        if not (self.state == S_RUNNING and self._step_acked and self._step_completed):
            return
        last_step = self._step_index + 1 >= len(self._steps)
        if last_step and (self._completion_stop_sent or self._completion_visual_pending):
            return
        self._cancel_child_response_timeout()
        # A step is done once it is acked AND its step_completed progress arrived
        # (plan §5.1). Count it, then either advance to the next manifest step or,
        # if this was the last one, stop with the real stepsCompleted count.
        if isinstance(self._step_id, str) and self._step_id not in self._completed_step_ids:
            self._steps_completed += 1
            self._completed_step_ids.add(self._step_id)
        elif not isinstance(self._step_id, str):
            self._steps_completed += 1
        if not last_step:
            await self._emit_step()  # next step in manifest order
        elif self._renderer_v2_enabled():
            self._queue_completion_visual_then_stop()
        else:
            self._completion_stop_sent = True
            await self._emit("lesson_stop", body={"reason": "COMPLETED"})

    def _interactive_progress_has_response(self, body: Dict[str, Any]) -> bool:
        detail = body.get("detail")
        if isinstance(detail, dict):
            if _has_negative_child_response_flag(detail) or _has_invalid_child_response_confidence(detail):
                return False
            if any(
                _has_observable_child_response_value(detail.get(key))
                for key in CHILD_RESPONSE_DETAIL_KEYS
            ):
                return True

        result = body.get("result")
        if isinstance(result, str):
            normalized = result.strip().lower()
            # Current ESP firmware emits result="success" with empty detail as a
            # renderer placeholder immediately after drawing the step. That is not
            # evidence that a child answered the prompt.
            return bool(normalized and normalized not in {"success", "ok", "done", "completed", "rendered"})
        return result not in (None, "", [])

    # ── STEP_TIMEOUT (distinct from PROTOCOL_SEQUENCE_ERROR) ─────────────────────

    def _start_step_timeout(self, step_seq: int, step_id: Optional[str], timeout_sec: float) -> None:
        async def _timeout() -> None:
            try:
                await self._sleep(timeout_sec)
            except asyncio.CancelledError:
                return
            if self._step_acked or self.state != S_RUNNING:
                return
            if self._step_passive:
                # Defensive: a passive step normally auto-advances on its ack (which
                # cancels this task). If the timer ever wins the race, an UN-acked
                # passive step is a render stall just like an interactive one — but
                # an ACKED passive step is already handled above. A passive step's
                # timeoutSec is a display DWELL, never a FAILED StepTimeout once
                # acked; the ack-absence path below still applies when truly stalled.
                pass
            # Ack-absence within timeoutSec -> STEP_TIMEOUT (RUNNING->FAILED). This is
            # a runtime stall, NOT an ordering fault — never PROTOCOL_SEQUENCE_ERROR.
            err = StepTimeout(step_id, step_seq)
            self.last_error = err
            self.state = S_FAILED
            self._log("error", f"STEP_TIMEOUT step={step_id} seq={step_seq}")
            await self._emit_error(err)
            await self._notify_lesson_terminal("step_timeout")

        self._step_timeout_task = asyncio.create_task(_timeout())

    def _cancel_step_timeout(self) -> None:
        if self._step_timeout_task is not None and not self._step_timeout_task.done():
            self._step_timeout_task.cancel()
        self._step_timeout_task = None

    def _passive_dwell_sec(self) -> float:
        """Seconds a PASSIVE step keeps its scene on screen before auto-advancing.
        Reads the per-step ``dwellSec`` first (lets the sample/author pace individual
        steps), then the connection-level ``lesson.passive_step_dwell_sec``, else 0
        (advance immediately on ack — unchanged default)."""
        step = self._step or {}
        config = getattr(self.conn, "config", {}) or {}
        lesson_cfg = _lesson_config(config)
        raw = step.get("dwellSec")
        if raw is None:
            raw = lesson_cfg.get("passive_step_dwell_sec")
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return 0.0
        return value if math.isfinite(value) and value > 0 else 0.0

    def _start_passive_dwell(self, step_seq: Optional[int], step_id: Optional[str], dwell_sec: float) -> None:
        self._cancel_passive_dwell()

        async def _dwell() -> None:
            try:
                await self._sleep(dwell_sec)
            except asyncio.CancelledError:
                return
            # Latch guard: only complete THIS still-current, still-running step. A
            # republish/failure/next-step transition makes the dwell a no-op.
            if (
                not self._is_active_runtime()
                or self.state != S_RUNNING
                or self._step_seq != step_seq
                or self._step_id != step_id
                or self._step_completed
            ):
                return
            self._complete_passive_step()
            await self._maybe_finish_step()

        self._passive_dwell_task = asyncio.create_task(_dwell())

    def _complete_passive_step(self) -> bool:
        if not self._step_passive or self._step_completed or self._step is None:
            return False
        step_type = self._step.get("type")
        self._forward(
            {
                "type": "step_completed",
                "sequence": -self._step_seq if isinstance(self._step_seq, int) else None,
                "stepId": self._step_id,
                "stepType": step_type,
                "result": "success",
                "detail": {"source": "passive_runtime"},
            }
        )
        self._log(
            "info",
            "lesson_progress step_completed "
            f"stepId={self._step_id} result=success stepType={step_type or ''} "
            "source=passive_runtime",
        )
        self._step_completed = True
        return True

    def _cancel_passive_dwell(self) -> None:
        current = asyncio.current_task()
        if (
            self._passive_dwell_task is not None
            and self._passive_dwell_task is not current
            and not self._passive_dwell_task.done()
        ):
            self._passive_dwell_task.cancel()
        self._passive_dwell_task = None

    def _frame_ack_timeout_sec(self) -> float:
        config = getattr(self.conn, "config", {}) or {}
        lesson_cfg = _lesson_config(config)
        raw = lesson_cfg.get("frame_ack_timeout_sec", lesson_cfg.get("ack_timeout_sec", 12.0))
        try:
            parsed = float(raw)
        except (TypeError, ValueError):
            parsed = 12.0
        return max(0.0, parsed) if math.isfinite(parsed) else 12.0

    def _frame_ack_max_retries(self) -> int:
        config = getattr(self.conn, "config", {}) or {}
        lesson_cfg = _lesson_config(config)
        raw = lesson_cfg.get(
            "frame_ack_max_retries",
            lesson_cfg.get("lifecycle_frame_ack_max_retries", 1),
        )
        try:
            parsed = int(raw)
        except (TypeError, ValueError, OverflowError):
            parsed = 1
        return max(0, parsed)

    def _start_frame_ack_timeout(self, frame_type: str, seq: int, step_id: Optional[str]) -> None:
        timeout_sec = self._frame_ack_timeout_sec()
        if timeout_sec <= 0:
            return
        self._cancel_frame_ack_timeout()

        async def _timeout() -> None:
            try:
                await self._sleep(timeout_sec)
            except asyncio.CancelledError:
                return
            if self._frame_ack_timeout_sequence != seq:
                return
            self._frame_ack_timeout_task = None
            self._frame_ack_timeout_sequence = None
            if seq not in self._outstanding or self.state in (S_FAILED, S_PAUSED, S_COMPLETED):
                return
            frame = self._outstanding.pop(seq, None) or {}
            self._retire_conversation_ack_sequence(seq, frame)
            retry_count = int(frame.get("retryCount") or 0)
            if retry_count < self._frame_ack_max_retries():
                self._log(
                    "warning",
                    (
                        "FRAME_ACK_TIMEOUT_RETRY "
                        f"type={frame_type} seq={seq} "
                        f"retry={retry_count + 1}/{self._frame_ack_max_retries()} "
                        f"stepId={step_id or ''}"
                    ),
                )
                command = self._cinematic_frame_command(frame)
                pending_visual = self._conversation_pending_visual
                retry_token = self._conversation_authority_token()
                retry_command_sequence = (
                    command.get("commandSequenceId")
                    if isinstance(command, dict)
                    and isinstance(pending_visual, dict)
                    and command.get("commandSequenceId")
                    == pending_visual.get("sequence")
                    else None
                )
                retry_task = asyncio.current_task()
                if type(retry_command_sequence) is int:
                    self._frame_ack_retry_task = retry_task
                    self._frame_ack_retry_command_sequence = retry_command_sequence
                try:
                    await self._emit(
                        frame_type,
                        step_id=step_id,
                        body=copy.deepcopy(frame.get("body") or {}),
                        frame_ack_retry_count=retry_count + 1,
                    )
                except (Exception, asyncio.CancelledError) as exc:
                    retry_is_current = (
                        type(retry_command_sequence) is int
                        and retry_token is not None
                        and self._conversation_token_is_current(retry_token)
                        and isinstance(self._conversation_pending_visual, dict)
                        and self._conversation_pending_visual.get("sequence")
                        == retry_command_sequence
                    )
                    if retry_is_current:
                        self._retire_conversation_visual()
                        await self._fail_frame_ack_retry_send(
                            frame_type, step_id, seq, exc
                        )
                    elif retry_command_sequence is None and self._is_active_runtime():
                        await self._fail_frame_ack_retry_send(
                            frame_type, step_id, seq, exc
                        )
                    return
                finally:
                    if self._frame_ack_retry_task is retry_task:
                        self._frame_ack_retry_task = None
                        self._frame_ack_retry_command_sequence = None
                return
            command = self._cinematic_frame_command(frame)
            authored_pending = self._authored_cinematic_pending
            if (
                isinstance(command, dict)
                and isinstance(authored_pending, dict)
                and command.get("commandSequenceId") == authored_pending.get("sequence")
                and command.get("cueId") == authored_pending.get("cueId")
            ):
                self._retire_authored_cinematic_pending()
            self.last_error = LessonError(
                LESSON_FRAME_ACK_TIMEOUT,
                f"no lesson_ack for {frame_type} within timeout",
                retryable=True,
                context={"frameType": frame_type, "stepId": step_id, "ackedSequence": seq},
            )
            self.state = S_FAILED
            self._log("error", f"FRAME_ACK_TIMEOUT type={frame_type} seq={seq} stepId={step_id or ''}")
            await self._emit_error(self.last_error)
            await self._notify_lesson_terminal("frame_ack_timeout")

        self._frame_ack_timeout_sequence = seq
        self._frame_ack_timeout_task = asyncio.create_task(_timeout())

    async def _fail_frame_ack_retry_send(
        self,
        frame_type: str,
        step_id: str | None,
        sequence: int,
        exc: BaseException,
    ) -> None:
        self.last_error = LessonError(
            LESSON_FRAME_ACK_TIMEOUT,
            f"failed to resend {frame_type} after ACK timeout",
            retryable=True,
            context={
                "frameType": frame_type,
                "stepId": step_id,
                "ackedSequence": sequence,
                "stage": "retrySend",
                "errorType": type(exc).__name__,
            },
        )
        self.state = S_FAILED
        self._log(
            "error",
            f"FRAME_ACK_RETRY_SEND_FAILED type={frame_type} seq={sequence} "
            f"stepId={step_id or ''} error={type(exc).__name__}",
        )
        try:
            await self._emit_error(self.last_error)
        except (Exception, asyncio.CancelledError) as notify_exc:
            self._log(
                "warning",
                "lesson retry-send error notification failed: "
                f"{type(notify_exc).__name__}",
            )
        try:
            await self._notify_lesson_terminal("frame_ack_retry_send_failed")
        except (Exception, asyncio.CancelledError) as notify_exc:
            self._log(
                "warning",
                "lesson retry-send terminal notification failed: "
                f"{type(notify_exc).__name__}",
            )

    def _cancel_frame_ack_timeout(self, sequence: int | None = None) -> None:
        if sequence is not None and self._frame_ack_timeout_sequence != sequence:
            return
        task = self._frame_ack_timeout_task
        self._frame_ack_timeout_task = None
        self._frame_ack_timeout_sequence = None
        if (
            task is not None
            and task is not asyncio.current_task()
            and not task.done()
        ):
            task.cancel()

    def _cancel_frame_ack_retry(self, command_sequence: int | None = None) -> None:
        if (
            command_sequence is not None
            and self._frame_ack_retry_command_sequence != command_sequence
        ):
            return
        task = self._frame_ack_retry_task
        self._frame_ack_retry_task = None
        self._frame_ack_retry_command_sequence = None
        if (
            task is not None
            and task is not asyncio.current_task()
            and not task.done()
        ):
            task.cancel()

    def _start_child_response_timeout(self) -> None:
        self._cancel_child_response_timeout()
        step_id = self._step_id
        timeout_sec = self._child_response_timeout_sec()

        async def _timeout() -> None:
            try:
                await self._sleep(timeout_sec)
            except asyncio.CancelledError:
                return
            if (
                not self._is_active_runtime()
                or self.state != S_RUNNING
                or self._step_id != step_id
                or self._step_passive
                or not self._step_acked
                or self._step_completed
            ):
                return
            await self._handle_child_response_timeout(step_id)

        self._child_response_timeout_task = asyncio.create_task(_timeout())

    def _cancel_child_response_timeout(self) -> None:
        current = asyncio.current_task()
        if (
            self._child_response_timeout_task is not None
            and self._child_response_timeout_task is not current
            and not self._child_response_timeout_task.done()
        ):
            self._child_response_timeout_task.cancel()
        self._child_response_timeout_task = None

    def _child_response_timeout_sec(self) -> float:
        step = self._step or {}
        config = getattr(self.conn, "config", {}) or {}
        lesson_cfg = _lesson_config(config)
        raw = (
            step.get("responseTimeoutSec")
            or step.get("childResponseTimeoutSec")
            or lesson_cfg.get("child_response_timeout_sec")
            or lesson_cfg.get("response_timeout_sec")
            or 12.0
        )
        try:
            parsed = float(raw)
        except (TypeError, ValueError):
            parsed = 12.0
        return parsed if math.isfinite(parsed) and parsed > 0 else 12.0

    def _max_child_response_timeouts(self) -> int:
        step = self._step or {}
        config = getattr(self.conn, "config", {}) or {}
        lesson_cfg = _lesson_config(config)
        raw = step.get("maxNoAnswerAttempts") or lesson_cfg.get("max_no_answer_attempts") or 2
        try:
            parsed = int(raw)
        except (TypeError, ValueError, OverflowError):
            parsed = 2
        return max(1, parsed)

    async def _handle_child_response_timeout(self, step_id: Optional[str]) -> None:
        if not self._is_active_runtime():
            return
        if self._uses_safe_speaking():
            await self.on_child_response_failure("silence")
            return
        self._child_response_timeout_count += 1
        self._close_child_response_window()
        if self._child_response_timeout_count < self._max_child_response_timeouts():
            self._log("info", f"child response inactive; reprompt stepId={step_id}")
            reprompt = self._child_response_timeout_reprompt()
            await self._speak_lesson_prompt_text(
                reprompt,
                step_id=step_id,
                continue_listening=True,
            )
            if not self._is_active_runtime():
                return
            await self._open_child_response_window()
            if self._child_response_window_still_current(self._step_id, self._step_seq):
                self._start_child_response_timeout()
            return

        if self._graceful_inactivity_finish:
            # Demo path: a silent spectator child must not make TeeBot end sad. Model
            # the answer aloud, then advance this step to keep walking toward the happy
            # lesson_completed ending. Reuses on_child_response's success wiring.
            await self._graceful_advance_on_inactivity(step_id)
            return

        self._log("info", f"child response inactive; pausing lesson stepId={step_id}")
        self.state = S_PAUSED
        self._cancel_step_timeout()
        self._cancel_child_response_timeout()
        self._forward(
            {
                "type": "lesson_abandoned",
                "stepId": step_id,
                "stepType": (self._step or {}).get("type"),
                "reason": "child_inactive",
                "abandonedAt": _wire_timestamp(),
            }
        )
        self._forward_phase("paused")
        await self._notify_lesson_terminal("child_inactive")

    async def _graceful_advance_on_inactivity(self, step_id: Optional[str]) -> None:
        """Demo-only inactivity recovery: TeeBot says the target word for the child,
        marks the step complete, and advances so a no-mic showcase still reaches the
        happy ending. Mirrors on_child_response's success tail (step_completed forward +
        success prompt + _maybe_finish_step) without requiring a real transcript."""
        if not self._is_active_runtime() or self.state != S_RUNNING:
            return
        self._cancel_child_response_timeout()
        self._child_response_window_open = False
        expected = _coerce_expected_child_responses(self._step)
        model_word = expected[0] if expected else ""
        model_prompt = (
            f"Để mình nói giúp con nhé: {model_word}." if model_word
            else "Để mình nói giúp con nhé."
        )
        self._log("info", f"child response inactive; demo graceful advance stepId={step_id}")
        await self._speak_lesson_prompt_text(
            model_prompt, step_id=step_id, continue_listening=False
        )
        if not self._is_active_runtime() or self.state != S_RUNNING:
            return
        self._forward(
            {
                "type": "step_completed",
                "sequence": -self._step_seq if isinstance(self._step_seq, int) else None,
                "stepId": step_id,
                "stepType": (self._step or {}).get("type"),
                "result": "modeled",
                "detail": {"reason": "child_inactive_demo_advance", "modeledText": model_word},
            }
        )
        self._step_completed = True
        success_prompt = _child_response_success_prompt(self._step, expected)
        if success_prompt is not None:
            await self._speak_lesson_prompt_text(
                success_prompt, step_id=step_id, continue_listening=False
            )
            await self._wait_lesson_prompt_idle()
        await self._maybe_finish_step()

    def _child_response_timeout_reprompt(self) -> str:
        """Warm, low-pressure nudge when the child stays quiet after a question."""
        expected = _coerce_expected_child_responses(self._step)
        target = expected[0] if expected else ""
        last_step = self._step_index + 1 >= len(self._steps)
        if target and last_step:
            return (
                f"Không sao, con từ từ nhé. "
                f"Nhìn hình, nói chậm tên tiếng Anh: {target}."
            )
        if target:
            return f"Không sao, con từ từ nhé. Nói chậm theo mình: {target}."
        return "Không sao, con từ từ nhé. Thử nói lại khi con sẵn sàng."

    def _child_response_window_still_current(
        self,
        step_id: Optional[str],
        step_seq: Optional[int],
    ) -> bool:
        return (
            self._is_active_runtime()
            and self.state == S_RUNNING
            and self._step_id == step_id
            and self._step_seq == step_seq
            and not self._step_passive
            and self._step_acked
            and not self._step_completed
        )

    async def _open_child_response_window(self) -> bool:
        step_id = self._step_id
        step_seq = self._step_seq
        if not self._child_response_window_still_current(step_id, step_seq):
            return False
        provider = getattr(self.conn, "voice_provider", None)
        opener = getattr(provider, "open_lesson_child_response_window", None)
        if not callable(opener):
            self._child_response_window_open = True
            return True
        try:
            opened = await opener()
            if opened is False or not self._child_response_window_still_current(step_id, step_seq):
                self._child_response_window_open = False
                return False
            self._child_response_window_open = True
            self._log("info", f"child response window opened stepId={self._step_id or ''} listening=1")
            return True
        except Exception as exc:
            self._child_response_window_open = False
            self._log("warning", f"lesson_child_response_window failed: {exc}")
            return False

    def _close_child_response_window(self) -> None:
        if not self._child_response_window_open:
            return
        self._child_response_window_open = False
        provider = getattr(self.conn, "voice_provider", None)
        closer = getattr(provider, "close_lesson_child_response_window", None)
        if not callable(closer):
            return
        try:
            closer()
        except Exception as exc:
            self._log("warning", f"lesson_child_response_window close failed: {exc}")

    # ── inbound sequence guard ──────────────────────────────────────────────────

    async def _accept_inbound(self, seq: Optional[int]) -> str:
        """Returns ``ok`` | ``duplicate`` | ``gap`` for the F->S envelope sequence.

        Gap (seq > last+1) -> emit ``PROTOCOL_SEQUENCE_ERROR`` (retryable) and HOLD.
        Duplicate/stale (seq <= last) -> idempotent no-op. (plan §5.8)
        """
        if seq is None:
            return "ok"
        if seq == self._last_inbound_sequence + 1:
            self._last_inbound_sequence = seq
            return "ok"
        if seq <= self._last_inbound_sequence:
            return "duplicate"
        await self._emit_error(
            ProtocolSequenceError(
                f"sequence gap: got {seq}, expected {self._last_inbound_sequence + 1}",
                context={"expected": self._last_inbound_sequence + 1, "got": seq},
            )
        )
        return "gap"

    # ── frame construction + send ───────────────────────────────────────────────

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    async def send_visual_state(
        self,
        state: str,
        *,
        overlay_key: Optional[str] = None,
        motion_preset: Optional[str] = None,
    ) -> VisualAckResult:
        generation = self._visual_generation
        request = {
            "state": state,
            "overlay_key": overlay_key,
            "motion_preset": motion_preset,
        }
        self._current_visual_request = request
        if not self._renderer_v2_enabled() or self._closed:
            return VisualAckResult(False, False, "unsupportedContract", None, generation)

        result = await self._send_visual_state_attempt(request, generation)
        if result.timed_out and generation == self._visual_generation and not self._closed:
            result = await self._send_visual_state_attempt(request, generation)
        return result

    async def _apply_visual_then_motion(
        self, state: str, overlay_key: Optional[str], preset: Optional[str]
    ) -> bool:
        if state not in RENDERER_V2_VISUAL_STATES or not self._renderer_v2_enabled():
            return False
        self._cancel_visual_waiters(increment_generation=True, reason="visualReplaced")
        generation = self._visual_generation
        assignment_id = self.assignment_id
        session_id = self.session_id
        step_id = self._step_id
        result = await self.send_visual_state(
            state,
            overlay_key=overlay_key,
            motion_preset=preset,
        )
        if not (
            result.accepted
            and result.visual_generation == generation
            and self._visual_transition_is_current(
                generation,
                assignment_id=assignment_id,
                session_id=session_id,
                step_id=step_id,
            )
        ):
            return False
        current = self._visual_transition_is_current(
            generation,
            assignment_id=assignment_id,
            session_id=session_id,
            step_id=step_id,
        )
        if current:
            phase = VISUAL_STATE_PARENT_PHASE.get(state)
            if phase is not None:
                self._forward_phase(phase)
        return current

    async def _apply_authored_visual_then_motion(
        self, state: str, motion_slot: Optional[str]
    ) -> bool:
        motion = (self._step or {}).get("motion")
        resolved_slot = motion_slot or RENDERER_V2_DEFAULT_MOTION_SLOTS.get(state)
        preset = motion.get(resolved_slot) if isinstance(motion, dict) and resolved_slot else None
        if not isinstance(preset, str) or not preset:
            preset = RENDERER_V2_DEFAULT_MOTION_PRESETS.get(state)
            if not preset:
                self._log(
                    "warning",
                    f"lesson_visual_state rejected missing motionPreset state={state} slot={resolved_slot}",
                )
                return False
        overlay_key = self._authored_overlay_key()
        if not overlay_key:
            self._log(
                "warning",
                f"lesson_visual_state rejected missing overlayKey state={state}",
            )
            return False
        return await self._apply_visual_then_motion(
            state,
            overlay_key,
            preset,
        )

    def _authored_overlay_key(self) -> Optional[str]:
        scene = (self._step or {}).get("scene")
        overlay = scene.get("robotOverlay") if isinstance(scene, dict) else None
        asset = overlay.get("asset") if isinstance(overlay, dict) else None
        key = asset.get("key") if isinstance(asset, dict) else None
        return key if isinstance(key, str) and key else None

    def _authored_step_visual_state(self) -> str:
        return {
            "listening": "listen",
            "thinking": "thinking",
            "celebrating": "celebrate",
        }.get((self._step or {}).get("robotState"), "teach")

    def _queue_authored_visual_sequence(
        self,
        transitions: List[tuple[str, Optional[str]]],
        *,
        after: Optional[Callable[[], Awaitable[None]]] = None,
    ) -> None:
        previous = self._visual_transition_task
        if previous is not None and not previous.done():
            previous.cancel()

        async def run() -> None:
            for state, motion_slot in transitions:
                if not await self._apply_authored_visual_then_motion(state, motion_slot):
                    return
            if after is not None:
                await after()

        self._visual_transition_task = asyncio.create_task(run())

    def _queue_completion_visual_then_stop(self) -> None:
        previous = self._visual_transition_task
        if previous is not None and not previous.done():
            previous.cancel()
        expected_generation = self._visual_generation + 1
        assignment_id = self.assignment_id
        session_id = self.session_id
        step_id = self._step_id
        self._completion_visual_pending = True

        async def run() -> None:
            try:
                visual_applied = await self._apply_authored_visual_then_motion(
                    "completion", "completion"
                )
                current_generation = expected_generation
                if (
                    not visual_applied
                    and self._visual_generation == expected_generation - 1
                ):
                    # Authored completion visuals are best-effort. Missing motion or
                    # overlay metadata is rejected before a visual generation starts,
                    # but must not suppress the terminal lesson_stop frame.
                    current_generation = self._visual_generation
                if not self._visual_transition_is_current(
                    current_generation,
                    assignment_id=assignment_id,
                    session_id=session_id,
                    step_id=step_id,
                ):
                    return
                await self._emit("lesson_stop", body={"reason": "COMPLETED"})
                self._completion_stop_sent = True
            finally:
                self._completion_visual_pending = False

        self._visual_transition_task = asyncio.create_task(run())

    def _visual_transition_is_current(
        self,
        generation: int,
        *,
        assignment_id: Any,
        session_id: str,
        step_id: Optional[str],
    ) -> bool:
        return (
            not self._closed
            and generation == self._visual_generation
            and assignment_id == self.assignment_id
            and session_id == self.session_id
            and step_id == self._step_id
            and self._is_active_runtime()
            and self.state == S_RUNNING
        )

    async def _dispatch_motion_once(
        self, preset: str, visual_generation: int, step_id: Optional[str]
    ) -> bool:
        if not self._lesson_rollout_control_enabled("motion_presets_enabled"):
            return False
        key = (
            self.assignment_id,
            self.session_id,
            step_id,
            visual_generation,
            preset,
        )
        if key in self._dispatched_visual_motions:
            return False
        if (
            self._closed
            or visual_generation != self._visual_generation
            or step_id != self._step_id
            or not self._is_active_runtime()
            or self.state != S_RUNNING
        ):
            return False
        self._dispatched_visual_motions.add(key)
        try:
            dispatched = await dispatch_motion_preset(self.conn, preset)
            self._log(
                "info" if dispatched else "warning",
                f"lesson_motion_dispatch outcome={'applied' if dispatched else 'failed'} preset={preset}",
            )
            return dispatched
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._log(
                "warning",
                f"lesson_motion_dispatch outcome=failed preset={preset} error={type(exc).__name__}",
            )
            return False

    async def _send_visual_state_attempt(
        self, request: Dict[str, Any], generation: int
    ) -> VisualAckResult:
        seq = self._next_seq()
        body = {
            "state": request["state"],
            "overlayKey": request.get("overlay_key"),
            "motionPreset": request.get("motion_preset"),
            "visualGeneration": generation,
        }
        frame = self._envelope(
            "lesson_visual_state", step_id=self._step_id, sequence=seq, body=body
        )
        loop = asyncio.get_running_loop()
        waiter = loop.create_future()
        waiter.visual_generation = generation
        waiter.step_id = self._step_id
        self._visual_ack_waiters[seq] = waiter

        async def timeout() -> None:
            try:
                await self._sleep(self._frame_ack_timeout_sec())
            except asyncio.CancelledError:
                return
            if not waiter.done():
                waiter.set_result(
                    VisualAckResult(
                        False,
                        False,
                        "phaseTimeout",
                        seq,
                        generation,
                        timed_out=True,
                    )
                )

        timeout_task = asyncio.create_task(timeout())
        self._visual_ack_timeout_tasks[seq] = timeout_task
        result: Optional[VisualAckResult] = None
        try:
            await self._send(json.dumps(frame, ensure_ascii=False))
            result = await waiter
            return result
        finally:
            self._visual_ack_waiters.pop(seq, None)
            task = self._visual_ack_timeout_tasks.pop(seq, None)
            if task is not None and task is not asyncio.current_task() and not task.done():
                task.cancel()
            if result is not None and result.timed_out:
                self._retire_visual_ack_sequence(seq, generation, self._step_id)

    def _retire_visual_ack_sequence(
        self, sequence: int, generation: int, step_id: Optional[str]
    ) -> None:
        self._retired_visual_ack_sequences[sequence] = {
            "visualGeneration": generation,
            "stepId": step_id,
        }
        while len(self._retired_visual_ack_sequences) > MAX_RETIRED_VISUAL_ACK_SEQUENCES:
            oldest = next(iter(self._retired_visual_ack_sequences))
            self._retired_visual_ack_sequences.pop(oldest, None)

    def _cancel_visual_waiters(self, *, increment_generation: bool, reason: str) -> None:
        if increment_generation:
            self._visual_generation += 1
            self._retired_visual_ack_sequences.clear()
        for seq, waiter in list(self._visual_ack_waiters.items()):
            self._retire_visual_ack_sequence(
                seq,
                getattr(waiter, "visual_generation", self._visual_generation),
                getattr(waiter, "step_id", self._step_id),
            )
            if not waiter.done():
                waiter.set_result(
                    VisualAckResult(
                        False,
                        False,
                        "unsupportedContract",
                        seq,
                        getattr(waiter, "visual_generation", self._visual_generation),
                    )
                )
        for task in list(self._visual_ack_timeout_tasks.values()):
            if not task.done():
                task.cancel()

    async def pause(self) -> None:
        if self.state != S_RUNNING:
            return
        if self._cinematic_enabled() and self._cinematic_phase is not None:
            if self._cinematic_pending_command is None:
                await self._emit(
                    "lesson_cinematic_control",
                    body={"command": "pause", **self._cinematic_identity_payload()},
                )
            return
        self.state = S_PAUSED
        self._cancel_visual_waiters(increment_generation=False, reason="paused")
        self._forward_phase("paused")

    async def resume(self) -> VisualAckResult:
        if self.state != S_PAUSED:
            return VisualAckResult(
                False, False, "unsupportedContract", None, self._visual_generation
            )
        if self._cinematic_enabled() and self._cinematic_phase is not None:
            if self._cinematic_pending_command is None:
                await self._emit(
                    "lesson_cinematic_control",
                    body={
                        "command": "resume",
                        **self._cinematic_identity_payload(),
                        "clockRebaseSequenceId": self._seq + 1,
                    },
                )
            return VisualAckResult(
                False, False, "unsupportedContract", None, self._visual_generation
            )
        self.state = S_RUNNING
        self._forward_phase("resumed")
        # T2.1: a pause that outlives ``timeoutSec`` retires the step timer — the
        # task wakes, sees a non-RUNNING state and returns WITHOUT a verdict. The
        # resumed step would then have no ack deadline at all and the run would
        # wedge in RUNNING forever. Re-arm the same deadline for a step that is
        # still un-acked; an already-acked step is past the ack gate and keeps its
        # own (child-response / dwell) timers.
        if (
            self._step is not None
            and not self._step_acked
            and self._step_timeout_task is None
        ):
            self._start_step_timeout(
                self._step_seq, self._step_id, self._step_timeout_sec
            )
        self._visual_generation += 1
        request = self._current_visual_request
        if request is None:
            return VisualAckResult(
                False, False, "unsupportedContract", None, self._visual_generation
            )
        generation = self._visual_generation
        assignment_id = self.assignment_id
        session_id = self.session_id
        step_id = self._step_id
        result = await self.send_visual_state(**request)
        if (
            self._step_acked
            and not self._step_completed
            and result.accepted
            and result.visual_generation == generation
            and self._visual_transition_is_current(
                generation,
                assignment_id=assignment_id,
                session_id=session_id,
                step_id=step_id,
            )
        ):
            if not self._visual_transition_is_current(
                generation,
                assignment_id=assignment_id,
                session_id=session_id,
                step_id=step_id,
            ):
                return result
            if not self._step_passive and request.get("state") != "listen":
                if not await self._apply_authored_visual_then_motion("listen", "listen"):
                    return result
                generation = self._visual_generation
            await self._continue_after_step_visuals(
                step_id,
                self._step_seq,
                visual_generation=generation,
                assignment_id=assignment_id,
                session_id=session_id,
            )
            return result
        if (
            self._step_acked
            and self._step_completed
            and self._step_index + 1 >= len(self._steps)
            and not self._completion_stop_sent
            and result.visual_generation == generation
            and self._visual_transition_is_current(
                generation,
                assignment_id=assignment_id,
                session_id=session_id,
                step_id=step_id,
            )
        ):
            if self._visual_transition_is_current(
                generation,
                assignment_id=assignment_id,
                session_id=session_id,
                step_id=step_id,
            ):
                await self._emit("lesson_stop", body={"reason": "COMPLETED"})
                self._completion_stop_sent = True
        return result

    async def stop(self) -> None:
        if self._cinematic_enabled():
            if self._cinematic_stop_sent or self._cinematic_pending_command is not None:
                return
        else:
            self._cancel_visual_waiters(increment_generation=True, reason="stopped")
        if self.state not in (S_FAILED, S_COMPLETED):
            # T2.1: the reason MUST stay inside the documented lesson_stop enum
            # (COMPLETED | CANCELLED | FAILED, protocol §4.6). The firmware
            # classifies ANY reason that is not COMPLETED/SUCCEEDED/CANCELLED as a
            # FAILURE (lesson_handler.cc), so the previous "STOPPED" showed the
            # child the sad-face "Bài học bị gián đoạn." UI + error sound for what
            # is a graceful/administrative stop. CANCELLED is the enum member with
            # exactly those semantics, and still projects lesson_abandoned (not
            # lesson_failed) on the ack path below.
            body: Dict[str, Any] = {"reason": "CANCELLED"}
            if self._cinematic_enabled() and self._cinematic_phase is not None:
                body["cinematicPhase"] = {
                    "command": "stop",
                    **self._cinematic_identity_payload(),
                }
                self._cinematic_stop_sent = True
            await self._emit("lesson_stop", body=body)

    async def cancel(self, reason: str = "cancelled") -> None:
        if (
            not self._cinematic_enabled()
            or self._cinematic_cancel_sent
            or self._cinematic_pending_command is not None
        ):
            return
        self._cinematic_cancel_sent = True
        await self._emit(
            "lesson_cinematic_control",
            body={
                "command": "cancel",
                **self._cinematic_identity_payload(),
                "reason": str(reason or "cancelled")[:64],
            },
        )

    def _clear_cinematic_state(self) -> None:
        self._cinematic_pending_command = None
        self._cinematic_deferred_step_ack = None
        self._retire_authored_cinematic_pending()
        self._cinematic_phase = None
        self._layered_cinematic_phases.clear()
        self._layered_cinematic_step_phases.clear()
        for sequence, frame in list(self._outstanding.items()):
            if self._cinematic_frame_command(frame) is not None:
                self._outstanding.pop(sequence, None)

    async def _apply_deferred_cinematic_step_ack(self) -> None:
        deferred = self._cinematic_deferred_step_ack
        self._cinematic_deferred_step_ack = None
        if not isinstance(deferred, dict) or self.state != S_RUNNING:
            return
        frame = deferred.get("frame")
        body = deferred.get("body")
        if not isinstance(frame, dict) or not isinstance(body, dict):
            return
        self._forward_lesson_step_ack_telemetry(
            frame, body, deferred.get("inboundSequence")
        )
        await self._on_frame_acked(frame, body)

    async def on_disconnect(self) -> None:
        self._cancel_visual_waiters(increment_generation=True, reason="disconnected")

    async def on_replaced(self) -> None:
        self._cancel_visual_waiters(increment_generation=True, reason="replaced")

    def _envelope(self, frame_type: str, *, step_id: Optional[str], sequence: int, body: Dict[str, Any]) -> Dict[str, Any]:
        # The frozen §5.2 envelope, key order matching the S2 fixture. The
        # protocolVersion is the NEGOTIATED renderer version (the served
        # manifestVersion, validated by the start() gate to be in the device's
        # capability set), falling back to the v1 PROTOCOL_VERSION default. Today
        # (v1 manifest, v1 device) this stamps v1 — byte-identical to the fixture.
        return {
            "type": frame_type,
            "protocolVersion": self.negotiated_version,
            "assignmentId": self.assignment_id,
            "sessionId": self.session_id,
            "lessonId": self.lesson_id,
            "lessonVersion": self.lesson_version,  # integer on the wire (D-LV)
            "stepId": step_id,
            "sequence": sequence,
            "timestamp": _wire_timestamp(),
            "body": body,
        }

    async def _emit(
        self,
        frame_type: str,
        *,
        step_id: Optional[str] = None,
        body: Optional[Dict[str, Any]] = None,
        frame_ack_retry_count: int = 0,
    ) -> int:
        seq = self._next_seq()
        frame_body = body or {}
        if self._cinematic_enabled():
            cinematic = frame_body.get("cinematicPhase")
            if isinstance(cinematic, dict) and isinstance(cinematic.get("command"), str):
                cinematic.setdefault("commandSequenceId", seq)
                for key in (
                    "command",
                    "cueId",
                    "effect",
                    "stepKey",
                    "playbackMode",
                    "commandSequenceId",
                ):
                    if key in cinematic:
                        frame_body.setdefault(key, cinematic[key])
            if frame_type == "lesson_cinematic_control":
                frame_body.setdefault("commandSequenceId", seq)
                if frame_body.get("command") == "resume":
                    frame_body["clockRebaseSequenceId"] = seq
            command = self._cinematic_frame_command({"body": frame_body})
            if command is not None:
                command_sequence_id = command.get("commandSequenceId", frame_body.get("commandSequenceId"))
                command["commandSequenceId"] = command_sequence_id
                identity_payload = (
                    {"phaseId": command.get("phaseId")}
                    if "phaseId" in command
                    else {"cueId": command.get("cueId")}
                )
                self._cinematic_pending_command = {
                    "command": command.get("command"),
                    **identity_payload,
                    "commandSequenceId": command_sequence_id,
                    # Retries keep commandSequenceId stable but get a new ACK envelope.
                    "ackSequence": seq,
                    "targetState": {
                        "pause": S_PAUSED,
                        "resume": S_RUNNING,
                        "stop": S_COMPLETED,
                        "cancel": S_COMPLETED,
                    }.get(command.get("command")),
                }
        frame = self._envelope(frame_type, step_id=step_id, sequence=seq, body=frame_body)
        if frame_type == "lesson_step":
            scene = frame["body"].get("scene") or {}
            story_beat = frame["body"].get("storyBeat")
            self._log(
                "info",
                # `type=<frame>` is the shared checkpoint contract for "the server sent
                # this frame" (lesson_e2e_log_verify.py `_positive_frame`). Without it
                # the only *_sent evidence in a capture is the DEVICE's `serial RX`
                # line, which is necessarily later — so an ordered verification credits
                # the send to the receive, and every server-side event that happened
                # in between (preload_ready, lesson_started) falls behind the cursor
                # and reports as missing. The `emit lesson_step` prose stays: it is the
                # contract `scripts/physical_smoke_audit.py` matches on.
                "emit lesson_step "
                f"type=lesson_step "
                f"stepId={step_id} "
                # The wire sequence of THIS frame. Without it nothing can pair a
                # lesson_step with the device ack that acknowledges it: the device
                # reports acks=/seq=, the server reported neither, so
                # lesson_ack_sequence_match could only ever see the prepare/start
                # pair and every step ack looked unmatched.
                f"sequence={frame.get('sequence')} "
                f"stepType={frame['body'].get('stepType')} "
                f"backgroundScene={int(bool(scene.get('backgroundScene')))} "
                f"teachingObject={int(bool(scene.get('teachingObject')))} "
                f"robotOverlay={int(bool(scene.get('robotOverlay')))} "
                # The media the frame actually points the renderer at. The device logs
                # a URL when it DRAWS one, but nothing recorded what the server told it
                # to draw -- so a frame that shipped a null/placeholder background was
                # indistinguishable from one the device simply failed to render.
                f"media={_lesson_step_media_log_summary(scene)} "
                f"prompt={int(bool(frame['body'].get('audio')))} "
                f"completionClass={frame['body'].get('completionClass', '')}",
            )
            # storyBeat on its OWN line: the three-layer declaration above is scanned by
            # a check that skips any line carrying JSON (to avoid matching raw frame
            # dumps), so embedding the beat here made the layer declaration invisible
            # and every completed step looked as though it declared no scene at all.
            self._log(
                "info",
                f"lesson_step storyBeat stepId={step_id} "
                f"storyBeat={_compact_json(story_beat) if story_beat is not None else '{}'}",
            )
        elif frame_type in ("lesson_prepare", "lesson_start", "lesson_stop"):
            # The wire sequence is the only ordering signal immune to log-timestamp
            # resolution. The ESP log stamps whole seconds (one capture put 51 lines in
            # a single second), so log position cannot express event order and any
            # ordered verification of a capture is guessing without this (F-T53-15).
            # The device side already reports it as seq=/acks=; this makes the server
            # side correlatable too.
            self._log(
                "info",
                f"emit {frame_type} type={frame_type} "
                f"stepId={step_id or ''} sequence={frame.get('sequence')}",
            )
        payload = json.dumps(frame, ensure_ascii=False)
        if len(payload.encode("utf-8")) > MAX_LESSON_FRAME_BYTES:
            await self._fail_oversized_frame(frame_type, step_id)
            return seq
        # Outstanding S->F frames are correlated by THIS sequence vs inbound body.acks.
        self._outstanding[seq] = {
            "type": frame_type,
            "stepId": step_id,
            "body": copy.deepcopy(frame_body),
            "retryCount": max(0, int(frame_ack_retry_count or 0)),
        }
        if frame_type in {
            "lesson_prepare", "lesson_start", "lesson_stop", "lesson_cinematic_control"
        }:
            self._start_frame_ack_timeout(frame_type, seq, step_id)
        try:
            await self._send(payload)
        except BaseException:
            failed_frame = self._outstanding.pop(seq, None)
            if isinstance(failed_frame, dict):
                self._retire_conversation_ack_sequence(seq, failed_frame)
            pending = self._cinematic_pending_command
            if isinstance(pending, dict) and pending.get("ackSequence") == seq:
                self._cinematic_pending_command = None
            self._cancel_frame_ack_timeout(seq)
            raise
        return seq

    def _frame_payload_size(self, frame_type: str, *, step_id: Optional[str], body: Dict[str, Any]) -> int:
        sequence = self._seq + 1
        frame = self._envelope(frame_type, step_id=step_id, sequence=sequence, body=body)
        return len(json.dumps(frame, ensure_ascii=False).encode("utf-8"))

    def _invalid_lesson_step_scene_reason(self, scene: Any) -> Optional[str]:
        if not isinstance(scene, dict):
            return "scene"
        background = scene.get("backgroundScene")
        teaching = scene.get("teachingObject")
        overlay = scene.get("robotOverlay")
        if not isinstance(background, dict):
            return "backgroundScene"
        if not isinstance(teaching, dict):
            return "teachingObject"
        if not isinstance(overlay, dict):
            return "robotOverlay"

        for reason, node in self._required_lesson_step_asset_nodes(scene):
            if not isinstance(node, dict) or not isinstance(node.get("src"), str) or not node.get("src", "").strip():
                return reason
            if self._use_sd_asset_pack() and not self._is_sd_asset_pack_source(node.get("src")):
                return reason
        return None

    def _is_sd_asset_pack_source(self, source: Any) -> bool:
        if not isinstance(source, str):
            return False
        source = source.strip()
        if not source:
            return False
        config = getattr(self.conn, "config", {}) or {}
        lesson_cfg = _lesson_config(config)
        roots = [lesson_cfg.get("asset_pack_local_root"), getattr(self.asset_cache, "asset_pack_local_root", None)]
        for root in roots:
            if not isinstance(root, str) or not root:
                continue
            normalized = root if root.endswith("://") else root.rstrip("/") + "/"
            if source.startswith(normalized):
                return True
        return False

    async def _fail_invalid_step_frame(self, step_id: Optional[str], reason: str) -> None:
        self.last_error = LessonError(
            LESSON_FRAME_INVALID,
            "lesson_step requires backgroundScene, teachingObject, and robotOverlay image sources",
            retryable=False,
            context={"stepId": step_id, "reason": reason},
        )
        self.state = S_FAILED
        self._cancel_step_timeout()
        self._log("warning", f"lesson frame invalid stepId={step_id} reason={reason}")
        await self._emit_error(self.last_error)
        await self._notify_lesson_terminal("lesson_frame_invalid")

    async def _fail_oversized_frame(self, frame_type: str, step_id: Optional[str]) -> None:
        self.last_error = LessonError(
            LESSON_FRAME_TOO_LARGE,
            f"{frame_type} frame exceeded {MAX_LESSON_FRAME_BYTES} bytes",
            retryable=False,
            context={"frameType": frame_type, "stepId": step_id, "maxBytes": MAX_LESSON_FRAME_BYTES},
        )
        self.state = S_FAILED
        self._cancel_step_timeout()
        self._log("warning", f"lesson frame too large type={frame_type} stepId={step_id}")
        await self._emit_error(self.last_error)
        await self._notify_lesson_terminal("lesson_frame_too_large")

    async def _emit_error(self, err: LessonError) -> None:
        if self._is_pre_activation_fallback_candidate():
            self._log(
                "warning",
                f"candidate lesson_error suppressed before activation code={err.code}",
            )
            return
        seq = self._next_seq()
        frame = self._envelope("lesson_error", step_id=None, sequence=seq, body=err.to_body())
        await self._send(json.dumps(frame, ensure_ascii=False))

    async def _default_send(self, payload: str) -> None:
        # T2.5 stale-socket invariant: once a newer websocket has taken this
        # device, this runtime's ``conn.websocket`` is a ghost. Writing to it
        # either lands on a half-open socket the robot has already abandoned, or
        # — after the device reconnects — races lesson frames from the live
        # session. Drop instead of sending; the superseded connection is being
        # torn down behind us.
        if getattr(self.conn, "superseded_by", None):
            self._log("warning", "lesson frame suppressed: connection superseded")
            return
        ws = getattr(self.conn, "websocket", None)
        if ws is not None:
            await ws.send(payload)

    # ── projections from the backend manifest ───────────────────────────────────

    def _prepare_body(self) -> Dict[str, Any]:
        body = {
            "assignmentVersion": self.assignment_version,
            "profile": self.profile,
            "manifestRef": {
                "lessonId": self.lesson_id,
                "lessonVersion": self.lesson_version,
                "url": f"GET /v1/lessons/{self.lesson_id}/manifest?profile={self.profile}&version={self.lesson_version}",
                "manifestChecksum": self.manifest_checksum,
            },
            "criticalAssets": self._critical_assets_payload(),
            "preloadTimeoutSec": int(self.asset_cache.preload_timeout_sec),
        }
        motion_enabled = self._lesson_rollout_control_enabled("motion_presets_enabled")
        playful_enabled = self._lesson_rollout_control_enabled("playful_interactions_enabled")
        if motion_enabled or playful_enabled:
            body["runtimeControls"] = {
                "motionPresetsEnabled": motion_enabled,
                "playfulInteractionsEnabled": playful_enabled,
            }
        if self._use_sd_asset_pack():
            pack = getattr(self.asset_cache, "asset_pack_manifest", None)
            if callable(pack):
                body["assetPack"] = self._prepare_asset_pack_payload(
                    pack(
                        assignment_version=self.assignment_version,
                        lesson_id=self.lesson_id,
                        lesson_version=self.lesson_version,
                        manifest_checksum=self.manifest_checksum,
                    )
                )
        if self._cinematic_enabled() and self._cinematic_phase is not None:
            body["cinematicPhase"] = {
                "command": "prepare",
                **copy.deepcopy(self._cinematic_phase),
            }
        return body

    def _start_body(self) -> Dict[str, Any]:
        if self._cinematic_enabled() and self._cinematic_phase is not None:
            return {
                "cinematicPhase": {
                    "command": "start",
                    **self._cinematic_identity_payload(),
                }
            }
        if not self._renderer_v2_enabled():
            return {}
        opening = self.manifest.get("openingEntrance")
        body: Dict[str, Any] = {
            "runtimeControls": {
                "openingEntranceEnabled": isinstance(opening, dict),
                "visualStateEventsEnabled": True,
                "physicalMotionOwner": "server",
            }
        }
        if isinstance(opening, dict):
            wire_keys = (
                "preset",
                "policy",
                "layoutPreset",
                "backgroundAssetKey",
                "robotAssetKey",
                "fallback",
            )
            body["openingEntrance"] = {
                key: copy.deepcopy(opening.get(key)) for key in wire_keys
            }
        return body

    @staticmethod
    def _prepare_asset_pack_payload(pack: Dict[str, Any]) -> Dict[str, Any]:
        """Keep only fields consumed by firmware's prepare-time SD attestation."""
        top_level_keys = (
            "assignmentVersion",
            "lessonId",
            "lessonVersion",
            "manifestChecksum",
            "cacheKey",
            "localRoot",
            "ready",
        )
        payload = {key: pack[key] for key in top_level_keys if key in pack}
        asset_keys = ("key", "state", "checksumOk", "size", "mediaType")
        local_root = str(pack.get("localRoot") or "").rstrip("/")
        payload_assets = []
        for asset in pack.get("assets", []):
            if not isinstance(asset, dict):
                continue
            compact = {key: asset[key] for key in asset_keys if key in asset}
            key = asset.get("key")
            local_path = asset.get("localPath")
            derived_path = (
                f"{local_root}/{quote(key, safe='')}"
                if local_root and isinstance(key, str) and key
                else None
            )
            if isinstance(local_path, str) and local_path and local_path != derived_path:
                compact["localPath"] = local_path
            payload_assets.append(compact)
        payload["assets"] = payload_assets
        return payload

    def _critical_assets_payload(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for a in self.manifest.get("assets", []):
            if not a.get("critical"):
                continue
            out.append(
                {
                    "key": a.get("id") or a.get("assetId"),
                    "role": a.get("role"),
                    "layer": a.get("layer"),
                    "mediaType": a.get("mediaType") or a.get("media_type"),
                    "path": a.get("path"),
                    "sha256": a.get("sha256"),
                    "critical": True,
                }
            )
        return out

    def _select_steps(self) -> List[Dict[str, Any]]:
        """P5: the ORDERED list of renderable steps the robot plays, in manifest
        step order. P5 replaces the slice's single-step pick — an authored lesson
        now plays ALL its steps, not just the first ``model`` step.

        Renderable == any step carrying a ``type``. The authored step kinds are the
        9 keys of the backend STEP_RENDER_MAP / render-contract.json ``stepRenderMap``:
        ``greeting``, ``review``, ``focus``, ``model``, ``listen``, ``repeat``,
        ``fillBlank``, ``feedback``, ``celebrate``. Of these, the PASSIVE narration
        kinds (``greeting``/``review``/``focus``/``feedback``/``celebrate``)
        auto-advance on their ack, while the INTERACTIVE kinds (``model``/``listen``/
        ``repeat``/``fillBlank``) wait for child response evidence after render ack —
        see ``PASSIVE_STEP_TYPES`` and ``_on_frame_acked``. Steps without a ``type``
        (pure metadata rows) are skipped. Manifest order is authoritative; we never
        re-sort, so the author's sequence is the playback sequence.

        Back-compat: a manifest with a single ``model`` step yields ``[that step]``,
        byte-identical to the slice. The legacy ``s4`` fallback is retained for a
        manifest that omits ``type`` on its only step.
        """
        steps = self.manifest.get("steps", []) or []
        ordered = [s for s in steps if s.get("type")]
        if ordered:
            return ordered
        # Legacy fallback: a single typeless ``s4`` step (slice manifests).
        for s in steps:
            if s.get("id") == "s4":
                return [s]
        return []

    def _step_body(self, step: Dict[str, Any]) -> Dict[str, Any]:
        # Byte-consistent with the fixture lesson_step.body: the scene IS the frozen
        # 3-layer projection from the manifest step (back->front, no lessonUi).
        scene = self._scene_with_cached_asset_urls(step.get("scene"))
        body = {
            "assignmentVersion": self.assignment_version,
            "stepType": step.get("type"),
            "profile": self.profile,
        }
        prompt = step.get("prompt")
        if prompt is not None:
            body["prompt"] = prompt
        for key in (
            "subject", "helperText", "l1TransferHint", "choices", "teachingWord",
            "interaction", "motion", *STEP_METADATA_KEYS,
        ):
            value = step.get(key)
            if value is not None:
                if key == "motion" and self._renderer_v2_enabled() and isinstance(value, dict):
                    # V2 motion is server-owned; legacy firmware must never see the
                    # only slot it historically executes locally.
                    body[key] = {slot: preset for slot, preset in value.items() if slot != "present"}
                else:
                    body[key] = value
        template_projection = _safe_tvideo_projection(step.get("templateProjection"))
        if template_projection is not None:
            body["templateProjection"] = template_projection
        body["timeoutSec"] = step.get("timeoutSec")
        body["audio"] = step.get("audio")
        body["scene"] = scene
        # Renderer-v1 additive field (NO protocol-version bump): forward the AUTHOR's
        # explicit ``completionClass`` ('passive'|'interactive') so the firmware uses
        # it as the authoritative passive/interactive classifier instead of re-deriving
        # from ``stepType`` (which MISCLASSIFIES author-defined step types -> spurious
        # step_completed / off-by-one). camelCase mirrors ``stepType``. Omitted when the
        # manifest step lacks it, keeping the wire body byte-identical to the frozen
        # fixtures (whose firmware then falls back to the v1 type-set, unchanged).
        completion_class = step.get("completionClass")
        if completion_class is not None:
            body["completionClass"] = completion_class
        return body

    def _scene_with_cached_asset_urls(self, scene: Any) -> Any:
        if scene is None:
            return None
        rewritten = copy.deepcopy(scene)
        self._ensure_robot_overlay_asset_source(rewritten)
        if self._use_sd_asset_pack():
            self._rewrite_required_sd_pack_layer_sources(rewritten)
        else:
            self._rewrite_required_http_layer_sources(rewritten)
        self._rewrite_cached_asset_sources(rewritten)
        return rewritten

    def _rewrite_required_http_layer_sources(self, scene: Any) -> None:
        resolver = getattr(self.asset_cache, "public_url_for_source", None)
        if not callable(resolver):
            return
        for _reason, node in self._required_lesson_step_asset_nodes(scene):
            source = node.get("src") if isinstance(node, dict) else None
            cached = resolver(source) if isinstance(source, str) else None
            if isinstance(node, dict):
                node["src"] = cached or ""

    def _rewrite_required_sd_pack_layer_sources(self, scene: Any) -> None:
        resolver = getattr(self.asset_cache, "local_pack_url_for_source", None)
        if not callable(resolver):
            return
        for _reason, node in self._required_lesson_step_asset_nodes(scene):
            source = node.get("src") if isinstance(node, dict) else None
            cached = resolver(source) if isinstance(source, str) else None
            if isinstance(node, dict):
                node["src"] = cached or ""
        overlay = scene.get("robotOverlay") if isinstance(scene, dict) else None
        atlas = overlay.get("atlas") if isinstance(overlay, dict) else None
        image = atlas.get("image") if isinstance(atlas, dict) else None
        cached_image = resolver(image) if isinstance(image, str) else None
        if isinstance(atlas, dict) and isinstance(image, str):
            atlas["image"] = cached_image or ""

    def _required_lesson_step_asset_nodes(self, scene: Any) -> List[tuple[str, Any]]:
        if not isinstance(scene, dict):
            return []
        background = scene.get("backgroundScene")
        teaching = scene.get("teachingObject")
        overlay = scene.get("robotOverlay")
        return [
            ("backgroundScene.poster.src", background.get("poster") if isinstance(background, dict) else None),
            ("teachingObject.asset.src", teaching.get("asset") if isinstance(teaching, dict) else None),
            ("robotOverlay.asset.src", overlay.get("asset") if isinstance(overlay, dict) else None),
        ]

    def _ensure_robot_overlay_asset_source(self, scene: Any) -> None:
        if not isinstance(scene, dict):
            return
        overlay = scene.get("robotOverlay")
        if not isinstance(overlay, dict):
            return
        asset = overlay.get("asset")
        if isinstance(asset, dict) and isinstance(asset.get("src"), str) and asset.get("src", "").strip():
            return
        atlas = overlay.get("atlas")
        image = atlas.get("image") if isinstance(atlas, dict) else None
        if not isinstance(image, str) or not image.strip():
            return
        next_asset = dict(asset) if isinstance(asset, dict) else {}
        next_asset.setdefault("key", "robotOverlay.asset")
        next_asset["src"] = image
        overlay["asset"] = next_asset

    def _rewrite_cached_asset_sources(self, value: Any) -> None:
        if isinstance(value, dict):
            for key, child in list(value.items()):
                if key == "src" and isinstance(child, str):
                    resolver_name = (
                        "local_pack_url_for_source"
                        if self._use_sd_asset_pack()
                        else "public_url_for_source"
                    )
                    resolver = getattr(self.asset_cache, resolver_name, None)
                    if callable(resolver):
                        cached = resolver(child)
                        if cached:
                            value[key] = cached
                            continue
                self._rewrite_cached_asset_sources(child)
        elif isinstance(value, list):
            for child in value:
                self._rewrite_cached_asset_sources(child)

    def _sd_asset_pack_enabled(self) -> bool:
        config = getattr(self.conn, "config", {}) or {}
        lesson_cfg = _lesson_config(config)
        mode = str(lesson_cfg.get("asset_delivery_mode") or "").strip().lower()
        return mode == "sd_pack" or lesson_cfg.get("sd_asset_pack_enabled") is True

    def _use_sd_asset_pack(self) -> bool:
        return self._sd_asset_pack_enabled() and not self._sd_asset_pack_online_fallback

    async def _sync_sd_asset_pack_to_robot(self) -> bool:
        mcp_client = getattr(self.conn, "mcp_client", None)
        if mcp_client is None:
            features = getattr(self.conn, "features", {}) or {}
            return not bool(features.get("mcp"))
        lesson_cfg = _lesson_config(getattr(self.conn, "config", {}) or {})
        is_ready = getattr(mcp_client, "is_ready", None)
        if callable(is_ready):
            ready_timeout = _finite_float_or_default(
                lesson_cfg.get("sd_sync_ready_timeout_sec", 8.0), 8.0
            )
            ready_poll = max(
                0.001,
                _finite_float_or_default(
                    lesson_cfg.get("sd_sync_ready_poll_sec", 0.05), 0.05
                ),
            )
            deadline = time.monotonic() + max(0.0, ready_timeout)
            while not await is_ready():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                await asyncio.sleep(min(ready_poll, remaining))
        sample_request_builder = getattr(
            self.asset_cache, "firmware_sample_sync_request", None
        )
        sample_result_validator = getattr(
            self.asset_cache, "validate_firmware_sample_sync_result", None
        )
        sample_request = None
        pack = None
        mcp_pack = None
        if callable(sample_request_builder):
            sample_request = sample_request_builder()
            if not isinstance(sample_request, dict) or not callable(sample_result_validator):
                return False
        else:
            pack_builder = getattr(self.asset_cache, "asset_pack_manifest", None)
            if not callable(pack_builder):
                return False
            pack = pack_builder(
                assignment_version=self.assignment_version,
                lesson_id=self.lesson_id,
                lesson_version=self.lesson_version,
                manifest_checksum=self.manifest_checksum,
            )
            if not isinstance(pack, dict) or not pack.get("assets"):
                return False
            try:
                mcp_pack = build_firmware_sync_pack(pack)
            except FirmwareSyncPackError:
                self._log("warning", "robot SD sync pack invalid")
                return False

        async def call_sync_once() -> Any:
            has_tool = getattr(mcp_client, "has_tool", None)
            timeout = sd_pack_sync_timeout_sec(
                getattr(self.conn, "config", {}) or {},
                mcp_pack or pack or {},
            )
            if sample_request is not None:
                if callable(has_tool) and has_tool(SAMPLE_SD_ASSET_SYNC_TOOL):
                    return await call_mcp_tool(
                        self.conn,
                        mcp_client,
                        SAMPLE_SD_ASSET_SYNC_TOOL,
                        sample_request,
                        timeout=timeout,
                    )
                from core.api.device_mcp_admin_handler import _call_raw_mcp_tool

                return await _call_raw_mcp_tool(
                    self.conn,
                    mcp_client,
                    "self.lesson_assets.sync_sample_to_sd",
                    sample_request,
                    timeout=timeout,
                )
            if callable(has_tool) and has_tool(SD_ASSET_SYNC_TOOL):
                return await call_mcp_tool(
                    self.conn,
                    mcp_client,
                    SD_ASSET_SYNC_TOOL,
                    {"assetPack": mcp_pack},
                    timeout=timeout,
                )
            from core.api.device_mcp_admin_handler import _call_raw_mcp_tool

            return await _call_raw_mcp_tool(
                self.conn,
                mcp_client,
                "self.lesson_assets.sync_to_sd",
                {"assetPack": mcp_pack},
                timeout=timeout,
            )

        busy_timeout = max(
            0.05,
            _finite_float_or_default(
                lesson_cfg.get("sd_sync_foreground_busy_timeout_sec", 15.0),
                15.0,
            ),
        )
        busy_poll = max(
            0.001,
            min(
                1.0,
                _finite_float_or_default(
                    lesson_cfg.get("sd_sync_foreground_busy_poll_sec", 0.05),
                    0.05,
                ),
            ),
        )
        admission_reader = getattr(
            self.conn, "lesson_start_sd_sync_admission_token", None
        )
        try:
            start_lesson_admission = (
                admission_reader() if callable(admission_reader) else None
            )
        except Exception:
            start_lesson_admission = None
        if start_lesson_admission is None:
            active_reader = getattr(
                self.conn, "lesson_start_sd_sync_admission_active", None
            )
            try:
                if callable(active_reader) and active_reader():
                    start_lesson_admission = True
            except Exception:
                start_lesson_admission = None

        def realtime_busy_timeout_error() -> _SdSyncRealtimeBusyTimeoutError:
            state_reader = getattr(self.conn, "_realtime_interaction_state", None)
            state = "unknown"
            if callable(state_reader):
                try:
                    state = str(state_reader() or "unknown")
                except Exception:
                    state = "unknown"
            return _SdSyncRealtimeBusyTimeoutError(busy_timeout, state)

        async def call_sync() -> Any:
            foreground_started = asyncio.Event()
            deadline = time.monotonic() + busy_timeout

            async def foreground_operation() -> Any:
                lesson_busy_check = getattr(
                    self.conn, "is_lesson_sd_sync_busy", None
                )
                if callable(lesson_busy_check) and start_lesson_admission is True:
                    def busy_check() -> bool:
                        return lesson_busy_check(start_lesson_dispatch=True)
                elif callable(lesson_busy_check) and start_lesson_admission is not None:
                    def busy_check() -> bool:
                        return lesson_busy_check(
                            start_lesson_admission=start_lesson_admission
                        )
                elif callable(lesson_busy_check):
                    busy_check = lesson_busy_check
                else:
                    busy_check = getattr(self.conn, "is_realtime_busy", None)
                foreground_started.set()
                while callable(busy_check) and busy_check():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise realtime_busy_timeout_error()
                    await asyncio.sleep(min(busy_poll, remaining))
                if time.monotonic() >= deadline:
                    raise realtime_busy_timeout_error()
                return await call_sync_once()

            cache_key = str(
                (mcp_pack or pack or {}).get("cacheKey")
                or (sample_request or {}).get("cacheKey")
                or f"sample:{self.lesson_id}:{self.lesson_version}"
            )
            request_task = asyncio.create_task(
                request_sd_pack_sync(
                    self.conn,
                    cache_key,
                    foreground_operation,
                    foreground=True,
                )
            )
            started_task = asyncio.create_task(foreground_started.wait())
            try:
                done, _pending = await asyncio.wait(
                    {request_task, started_task},
                    timeout=max(0.0, deadline - time.monotonic()),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if request_task in done:
                    return await request_task
                if started_task in done:
                    return await request_task
                request_task.cancel()
                await asyncio.gather(request_task, return_exceptions=True)
                raise realtime_busy_timeout_error()
            except asyncio.CancelledError:
                request_task.cancel()
                await asyncio.gather(request_task, return_exceptions=True)
                raise
            finally:
                started_task.cancel()
                await asyncio.gather(started_task, return_exceptions=True)

        sync_started_at = time.monotonic()
        try:
            result = await call_sync()
        except _SdSyncRealtimeBusyTimeoutError as exc:
            self.last_error = LessonError(
                "SD_SYNC_REALTIME_BUSY_TIMEOUT",
                "realtime voice did not become idle before lesson SD sync",
                retryable=True,
            )
            self._log(
                "warning",
                "robot SD sync realtime busy timeout "
                f"timeoutSec={exc.timeout_sec:.3f} state={exc.state}",
            )
            return False
        except Exception as exc:
            reset = getattr(self.conn, "request_lesson_preload_reset", None)
            if "MCP tools disabled during lesson" not in str(exc) or not callable(reset):
                self._log("warning", f"robot SD sync failed: {type(exc).__name__}")
                return False
            try:
                recovered = await reset(
                    assignment_id=self.assignment_id,
                    lesson_id=self.lesson_id,
                    profile=self.profile,
                )
                if not recovered:
                    return False
                result = await call_sync()
            except _SdSyncRealtimeBusyTimeoutError as retry_exc:
                self.last_error = LessonError(
                    "SD_SYNC_REALTIME_BUSY_TIMEOUT",
                    "realtime voice did not become idle before lesson SD sync",
                    retryable=True,
                )
                self._log(
                    "warning",
                    "robot SD sync realtime busy timeout "
                    f"timeoutSec={retry_exc.timeout_sec:.3f} state={retry_exc.state}",
                )
                return False
            except Exception as retry_exc:
                self._log(
                    "warning",
                    f"robot SD sync recovery failed: {type(retry_exc).__name__}",
                )
                return False
        duration_ms = max(1, int(round((time.monotonic() - sync_started_at) * 1000)))
        if sample_request is not None:
            if not sample_result_validator(result):
                self._log("warning", "robot sample SD sync returned invalid result")
                return False
            self._log("info", f"sample_sd_sync_ready durationMs={duration_ms}")
            return True
        attestation = self._sd_asset_sync_attestation(result, pack)
        if attestation is None:
            diagnostic = self._sd_asset_sync_diagnostic(result, pack)
            self._log(
                "warning",
                "robot SD sync returned invalid attestation "
                f"ready={diagnostic['ready']} "
                f"assetCount={diagnostic['assetCount']} "
                f"downloadedCount={diagnostic['downloadedCount']} "
                f"skippedCount={diagnostic['skippedCount']} "
                f"reusedCount={diagnostic['reusedCount']} "
                f"failedCount={diagnostic['failedCount']} "
                f"criticalFailedCount={diagnostic['criticalFailedCount']} "
                f"cacheKeyMatch={diagnostic['cacheKeyMatch']} "
                f"checksumMatch={diagnostic['checksumMatch']}",
            )
            return False
        marker_fields = (
            f"cacheKey={attestation['cacheKey']} "
            f"assetCount={attestation['assetCount']} "
            f"downloadedCount={attestation['downloadedCount']} "
            f"skippedCount={attestation['skippedCount']} "
            f"reusedCount={attestation['reusedCount']} "
            f"failedCount={attestation['failedCount']} "
            f"durationMs={duration_ms}"
        )
        self._log("info", f"lesson_preload_ready {marker_fields}")
        self._log(
            "info",
            "checksum_verified "
            f"cacheKey={attestation['cacheKey']} "
            f"manifestChecksum={self.manifest_checksum} "
            f"assetCount={attestation['assetCount']}",
        )
        if (
            attestation["downloadedCount"] == 0
            and attestation["skippedCount"] == attestation["assetCount"]
        ):
            self._log("info", f"asset_cache_hit {marker_fields}")
        return True

    def _sd_asset_sync_attestation(
        self, result: Any, requested_pack: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except json.JSONDecodeError:
                return None
        if not isinstance(result, dict):
            return None
        assets = requested_pack.get("assets")
        expected_cache_key = requested_pack.get("cacheKey")
        expected_checksum = requested_pack.get("manifestChecksum")
        if (
            not isinstance(assets, list)
            or not isinstance(expected_cache_key, str)
            or not isinstance(expected_checksum, str)
            or not expected_checksum
            or expected_checksum != self.manifest_checksum
        ):
            return None
        if result.get("ready") is not True or result.get("cacheKey") != expected_cache_key:
            return None
        response_checksums = [
            result[key]
            for key in ("manifestChecksum", "packChecksum")
            if key in result
        ]
        if not response_checksums or any(value != expected_checksum for value in response_checksums):
            return None
        counts = {}
        for key in ("downloadedCount", "skippedCount", "reusedCount", "failedCount"):
            value = result.get(key, 0) if key == "reusedCount" else result.get(key)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                return None
            counts[key] = value
        if counts["failedCount"] != 0 or sum(counts.values()) != len(assets):
            return None
        return {
            "cacheKey": expected_cache_key,
            "assetCount": len(assets),
            **counts,
        }

    def _sd_asset_sync_diagnostic(
        self, result: Any, requested_pack: Dict[str, Any]
    ) -> Dict[str, Any]:
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except json.JSONDecodeError:
                result = {}
        if not isinstance(result, dict):
            result = {}
        assets = requested_pack.get("assets")
        expected_cache_key = requested_pack.get("cacheKey")
        expected_checksum = requested_pack.get("manifestChecksum")

        def count(name: str) -> int:
            value = result.get(name)
            return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else -1

        checksums = [result[key] for key in ("manifestChecksum", "packChecksum") if key in result]
        return {
            "ready": result.get("ready") is True,
            "assetCount": len(assets) if isinstance(assets, list) else -1,
            "downloadedCount": count("downloadedCount"),
            "skippedCount": count("skippedCount"),
            "reusedCount": count("reusedCount") if "reusedCount" in result else 0,
            "failedCount": count("failedCount"),
            "criticalFailedCount": count("criticalFailedCount"),
            "cacheKeyMatch": isinstance(expected_cache_key, str)
            and result.get("cacheKey") == expected_cache_key,
            "checksumMatch": isinstance(expected_checksum, str)
            and bool(checksums)
            and all(value == expected_checksum for value in checksums),
        }

    def _ack_reports_asset_pack_ready(self, ack_body: Dict[str, Any]) -> bool:
        pack = ack_body.get("assetPack")
        if not isinstance(pack, dict) or pack.get("ready") is not True:
            return False
        expected = getattr(self.asset_cache, "cache_key", None)
        actual = pack.get("cacheKey")
        if not isinstance(expected, str) or not expected.strip() or expected != expected.strip():
            return False
        if not isinstance(actual, str) or not actual.strip():
            return False
        return actual == expected

    # ── progress forward (own dispatch path) ────────────────────────────────────

    def _forward(self, event: Dict[str, Any]) -> None:
        if self.forwarder is None:
            return
        clean = {k: v for k, v in event.items() if v is not None}
        self._log_runtime_event(clean)
        batch = {
            "assignmentId": self.assignment_id,
            "lessonId": self.lesson_id,
            "lessonVersion": self.lesson_version,
            "sessionId": self.session_id,
            "events": [clean],
        }
        batch.update(self._trace_context)
        self.forwarder.enqueue(batch)

    def _start_terminal_readback(self) -> None:
        """Re-read the assignment after completing it, and record what the backend says.

        Reporting a completion and OBSERVING it are two different facts, and until now
        the runtime only ever produced the first. The backend row could read COMPLETED
        while nothing on the device could show it, which leaves the loop open exactly
        where it matters -- a completion that never persisted looks identical to one
        that did.

        Fire-and-forget on purpose: the lesson is already complete and the child is
        done, so a slow or failing backend must never delay or fail that. Tests await
        `drain_terminal_readback()` to make it deterministic.
        """
        forwarder = self.forwarder
        base_url = getattr(forwarder, "base_url", None)
        device_id = getattr(forwarder, "device_id", None)
        if not base_url or not device_id:
            return
        try:
            self._terminal_readback_task = asyncio.create_task(
                self._read_back_assignment_state(
                    base_url, device_id, getattr(forwarder, "token", None)
                )
            )
        except RuntimeError:  # pragma: no cover - no running loop (sync teardown)
            self._terminal_readback_task = None

    async def drain_terminal_readback(self) -> None:
        """Await the post-completion read-back, if one was started."""
        task = getattr(self, "_terminal_readback_task", None)
        if task is not None:
            await task

    async def _read_back_assignment_state(self, base_url, device_id, token) -> None:
        from config import manage_api_client as backend_api

        # Wait for our OWN completion to land first. Forwarding is queued, so a
        # read-back fired the instant the runtime reaches COMPLETED overtakes the
        # completion it is trying to observe and faithfully reports the pre-completion
        # state -- which reads as "the backend rejected it".
        drain = getattr(self.forwarder, "drain", None)
        if callable(drain):
            try:
                await drain()
            except Exception:  # a stalled queue must not strand the read-back
                pass

        try:
            import httpx

            async with httpx.AsyncClient(timeout=5.0) as client:
                # Returns the assignment dict itself (or None) -- NOT a (payload, etag)
                # tuple. Unpacking it raises ValueError, which the guard below then
                # swallowed into a bare type name; that is exactly why the message is
                # logged too.
                assignment = await backend_api.get_current_assignment(
                    client, base_url, device_id, token=token, include_terminal=True
                )
        except Exception as exc:  # never let a read-back disturb a finished lesson
            self._log(
                "warning",
                f"assignment/current read-back failed: {type(exc).__name__}: {exc}",
            )
            return
        if not isinstance(assignment, dict):
            # The slot was released, which is what a recorded completion looks like:
            # `assignment/current` only ever reports an ACTIVE assignment, so a
            # completed one is absent by design (it cannot report state=COMPLETED).
            self._log(
                "info",
                "assignment/current read-back completion observed: no active assignment",
            )
            return
        state = str(assignment.get("state", "") or "")
        assignment_id = assignment.get("assignmentId", "")
        if state.upper() in ("COMPLETED", "CANCELLED", "FAILED"):
            # A backend that reports the terminal state directly is the clearest
            # possible confirmation.
            self._log(
                "info",
                "assignment/current read-back completion observed: "
                f"assignmentId={assignment_id} state={state}",
            )
            return
        if assignment_id and assignment_id != self.assignment_id:
            # A DIFFERENT assignment is already active -- ours is no longer the current
            # one, so our completion did land.
            self._log(
                "info",
                "assignment/current read-back completion observed: "
                f"device moved on to assignmentId={assignment_id} state={state}",
            )
            return
        # Our own assignment is STILL ACTIVE after we completed it. The lesson ran to
        # the end on the robot and the backend does not know -- the exact shape of
        # F-T53-17, where a rate-limited terminal batch was discarded and the
        # assignment sat RUNNING with nothing anywhere saying so.
        self._log(
            "warning",
            "assignment/current read-back completion not observed: "
            f"assignmentId={assignment_id} still active state={state}",
        )

    def _log_runtime_event(self, event: Dict[str, Any]) -> None:
        """Record the runtime reaching a state, separately from forwarding it.

        These are two different facts and only the runtime knows the first one:
        "this runtime reached lesson_started" is device-side evidence, whereas
        "the backend accepted a lesson_started event" is a statement about the
        backend. `lesson_e2e_log_verify.py` relies on that distinction -- its
        lesson_started / step_started / preload_ready checkpoints deliberately
        REJECT any line containing "backend", so that a backend echo can never be
        mistaken for the robot actually starting, while its *_posted checkpoints
        require the "backend post ..." wording. Logging only the forward satisfied
        the second family and silently broke the first.

        So the state transition is logged here, by the component that owns it, with
        no "backend" token; LessonEventForwarder logs the POST separately.
        """
        name = event.get("type") or event.get("event")
        if not name:
            return
        parts = [
            f"LessonRuntime event {name}",
            f"assignmentId={self.assignment_id or ''}",
            f"sessionId={self.session_id or ''}",
        ]
        for key in ("stepId", "result", "outcome", "state"):
            value = event.get(key)
            if value is not None:
                parts.append(f"{key}={value}")
        self._log("info", " ".join(parts))

    def _forward_phase(self, phase: str) -> None:
        if (
            phase not in PARENT_RUNTIME_PHASES
            or self._is_pre_activation_fallback_candidate()
        ):
            return
        self._parent_phase_sequence -= 1
        event: Dict[str, Any] = {
            "type": "runtime_phase_changed",
            "sequence": self._parent_phase_sequence,
            "phase": phase,
            "occurredAt": _wire_timestamp(),
        }
        step_id = self._step_id
        if isinstance(step_id, str):
            step_id = step_id.strip()
            if 0 < len(step_id) <= 128:
                event["stepId"] = step_id
        step_type = (self._step or {}).get("type")
        if isinstance(step_type, str):
            step_type = step_type.strip()
            if 0 < len(step_type) <= 64:
                event["stepType"] = step_type
        self._forward(event)

    def _log(self, level: str, message: str) -> None:
        if self.logger is None:
            return
        try:
            getattr(self.logger.bind(tag=TAG), level)(self._with_log_context(message))
        except Exception:
            pass

    def _with_log_context(self, message: str) -> str:
        return with_lesson_log_context(
            message, assignment_id=self.assignment_id, session_id=self.session_id
        )


async def _wait_for_mcp_reconnect_ready(
    conn: Any, lesson_cfg: Dict[str, Any]
) -> tuple[bool, Optional[str]]:
    features = getattr(conn, "features", {}) or {}
    if not isinstance(features, dict) or not bool(features.get("mcp")):
        return True, None

    is_ready = getattr(getattr(conn, "mcp_client", None), "is_ready", None)
    if not callable(is_ready):
        return False, "missing_is_ready"

    timeout_sec = max(
        0.0,
        _finite_float_or_default(
            lesson_cfg.get("mcp_reconnect_ready_timeout_sec", 20.0), 20.0
        ),
    )
    poll_sec = max(
        0.001,
        _finite_float_or_default(
            lesson_cfg.get("mcp_reconnect_ready_poll_sec", 0.05), 0.05
        ),
    )
    deadline = time.monotonic() + timeout_sec
    while True:
        try:
            if await is_ready():
                return True, None
        except Exception as exc:
            return False, type(exc).__name__
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False, "timeout"
        await asyncio.sleep(min(poll_sec, remaining))


async def maybe_start_lesson_on_connect(conn: Any) -> Optional[LessonRuntime]:
    """Serialize concurrent lesson pulls (connect-time pull + spoken start_lesson) so
    they cannot create two runtimes / emit duplicate lesson_prepare (deep-audit). The
    per-connection lock is lazily created; the lazy-init is atomic under asyncio (no
    await between the getattr and the assignment), so two schedulers racing here both
    end up using the same lock, then run the impl serially — the loser re-reads
    conn.lesson_runtime and returns the winner's session instead of duplicating it."""
    from core.providers.tools.product_toolset import lesson_runtime_enabled

    if not lesson_runtime_enabled(conn):
        _set_lesson_start_status(conn, "ROLLOUT_BLOCKED", "Robot chưa được bật bài học.")
        return None
    lock = getattr(conn, "_lesson_pull_lock", None)
    if lock is None:
        lock = asyncio.Lock()
        conn._lesson_pull_lock = lock
    async with lock:
        activity_leases = getattr(conn, "activity_leases", None)
        if activity_leases is not None and activity_leases.has_exclusive_lease():
            _set_lesson_start_status(
                conn,
                "CACHE_EVICTION_RESERVED",
                "Robot đang xác minh bộ nhớ bài học.",
            )
            return None
        return await _maybe_start_lesson_on_connect_impl(conn)


async def _maybe_start_lesson_on_connect_impl(conn: Any) -> Optional[LessonRuntime]:
    """S6 pull-on-connect glue (authoritative hand-off, ADR 0013 §A/§B).

    Gated by the dark-rollout flag. Fetches the device's current assignment + the
    espTft manifest from ``server.api_url`` (the Nest backend), wires the runtime,
    and sends ``lesson_prepare``. Any failure is swallowed (logged) — the lesson
    layer must NEVER break the connection or the voice path.
    """
    config = getattr(conn, "config", {}) or {}
    _set_lesson_start_status(conn, "CHECKING_ASSIGNMENT")
    lesson_cfg = _lesson_config(config)
    server_cfg = _server_config(config)
    base_url = lesson_cfg.get("api_base") or server_cfg.get("api_url")
    if isinstance(base_url, str):
        base_url = base_url.rstrip("/")
    device_id = getattr(conn, "device_id", None)
    logger = getattr(conn, "logger", None)
    _log_context: Dict[str, Any] = {}

    def _log(level: str, message: str) -> None:
        if logger is None:
            return
        assignment_for_log = _log_context.get("assignment")
        contextual_message = with_lesson_log_context(
            message,
            assignment_id=(
                assignment_for_log.get("assignmentId")
                if isinstance(assignment_for_log, dict)
                else None
            ),
            session_id=getattr(conn, "session_id", None),
            device_id=device_id,
        )
        try:
            getattr(logger.bind(tag=TAG), level)(contextual_message)
        except Exception:
            pass

    def _backend_unavailable(phase: str, exc: Exception) -> None:
        _set_lesson_start_status(
            conn,
            "BACKEND_UNAVAILABLE",
            "Robot chưa kết nối được máy chủ bài học. Con thử lại sau nhé.",
        )
        _log("warning", f"lesson backend {phase} unavailable: {type(exc).__name__}")

    if not base_url or not device_id:
        _set_lesson_start_status(conn, "LESSON_CONFIG_MISSING", "Robot chưa kết nối được máy chủ bài học.")
        _log("info", "lesson pull-on-connect skipped: no api_base or device_id")
        return None

    token = lesson_cfg.get("device_token")

    try:
        import httpx
        from config import manage_api_client as backend_api
    except Exception as exc:  # pragma: no cover
        _log("warning", f"lesson pull-on-connect unavailable: {exc}")
        return None

    from core.lesson.errors import lesson_capability_ok as _cap_ok
    from core.lesson.errors import device_renderer_capabilities as _device_caps
    from core.lesson.asset_cache import AssetCache
    from core.lesson.forwarder import LessonEventForwarder

    # Wait briefly for hello/features so the capability gate is meaningful.
    for _ in range(50):
        if getattr(conn, "features", None) is not None:
            break
        await asyncio.sleep(0.1)
    renderer_capabilities = _device_caps(getattr(conn, "features", None))
    renderer_v2_enabled = _renderer_v2_request_enabled(conn, renderer_capabilities)
    renderer_v3_enabled = _renderer_v3_request_enabled(conn, renderer_capabilities)
    renderer_v4_enabled = _renderer_v4_request_enabled(conn, renderer_capabilities)
    renderer_v5_enabled = _renderer_v5_request_enabled(conn, renderer_capabilities)
    if not renderer_v3_enabled and not renderer_v4_enabled and not renderer_v5_enabled and not _cap_ok(
        getattr(conn, "features", None), renderer_v2_enabled=renderer_v2_enabled
    ):
        _set_lesson_start_status(conn, "LESSON_CAPABILITY_MISSING", "Robot chưa sẵn sàng hiển thị bài học.")
        _log("info", "device lacks lesson capability; pull-on-connect no-op")
        return None

    # L3 P3 — the device's advertised renderer-capability set (v1-only for every
    # current firmware). Forwarded to the manifest fetch so the backend serves a
    # manifest this device can render. The runtime re-derives the same set from
    # conn.features for its start() gate; computing it here keeps the fetch honest.
    requested_renderer_capabilities = _requested_renderer_capabilities(
        renderer_capabilities,
        renderer_v2_enabled=renderer_v2_enabled,
        renderer_v3_enabled=renderer_v3_enabled,
        renderer_v4_enabled=renderer_v4_enabled,
        renderer_v5_enabled=renderer_v5_enabled,
    )

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(15.0),
        limits=httpx.Limits(max_keepalive_connections=0),
        follow_redirects=True,
    ) as client:
        try:
            from config.device_token_client import resolve_device_identity

            backend_device_id, minted_token = await resolve_device_identity(
                client, base_url, device_id, logger=logger
            )
        except Exception as exc:  # pragma: no cover - client is fail-soft by contract
            _backend_unavailable("identity", exc)
            return None
        if not backend_device_id or not minted_token:
            _set_lesson_start_status(
                conn,
                "BACKEND_UNAVAILABLE",
                "Robot chưa kết nối được máy chủ bài học. Con thử lại sau nhé.",
            )
            _log("warning", "lesson backend identity unavailable")
            return None
        token = minted_token
        _log(
            "info",
            "lesson backend identity resolved "
            f"apiBase={base_url} "
            f"deviceMac={device_id} "
            f"backendDeviceId={backend_device_id}",
        )
        # Best-effort: address the child by name in plain CONVERSATION. The name
        # the parent sets in the mobile app lives in the backend child profile, NOT
        # the esp manager-api ``ai_device.child_name`` the agent-models config
        # carries (separate keyspace, migration 086). Overlay it onto conn.config so
        # google_live._augment_prompt_with_child_name picks it up — but only when the
        # esp config left the name blank, so an explicit manager-side name still
        # wins. Runs here (not gated on an assignment) because conversation is the
        # primary use; failures are swallowed — this must never break the voice path.
        try:
            child_profile = config.get("child_profile")
            existing_name = ""
            if isinstance(child_profile, dict):
                raw_existing = child_profile.get("child_name")
                existing_name = raw_existing.strip() if isinstance(raw_existing, str) else ""
            if not existing_name:
                backend_child_name = await backend_api.get_device_child_name(
                    client, base_url, backend_device_id, token=token
                )
                if backend_child_name:
                    if not isinstance(child_profile, dict):
                        child_profile = {}
                        config["child_profile"] = child_profile
                    child_profile["child_name"] = backend_child_name
                    _log("info", "conversation child name enriched from backend child profile")
        except Exception as exc:  # pragma: no cover - best-effort enrichment
            _log("warning", f"child-name enrichment skipped: {type(exc).__name__}: {exc}")

        try:
            assignment = await backend_api.get_current_assignment(client, base_url, backend_device_id, token=token)
        except Exception as exc:
            _backend_unavailable("assignment", exc)
            return None
        if assignment:
            _log_context["assignment"] = assignment
        if not assignment:
            _set_lesson_start_status(conn, "NO_CURRENT_ASSIGNMENT", NO_CURRENT_ASSIGNMENT_MESSAGE)
            _log("info", "no current assignment for device; nothing to preload")
            return None
        if assignment.get("state") in ("COMPLETED", "CANCELLED", "FAILED"):
            _set_lesson_start_status(conn, "ASSIGNMENT_TERMINAL", "Bài học này đã kết thúc.")
            _log("info", f"assignment in terminal state {assignment.get('state')}; skipping")
            return None
        metadata_errors = _assignment_metadata_errors(assignment)
        if metadata_errors:
            _set_lesson_start_status(conn, "ASSIGNMENT_INVALID", "Máy chủ gửi bài học thiếu thông tin phiên bản.")
            _log("warning", f"assignment missing required metadata: {','.join(metadata_errors)}")
            return None
        _log(
            "info",
            "assignment/current active "
            f"assignmentId={assignment.get('assignmentId')} "
            f"state={assignment.get('state')} "
            f"lessonId={assignment.get('lessonId')} "
            f"lessonVersion={assignment.get('lessonVersion')} "
            f"assignmentVersion={assignment.get('assignmentVersion')} "
            f"manifestChecksum={assignment.get('manifestChecksum')} "
            f"profile={assignment.get('profile', 'espTft')} "
            f"deviceId={device_id} "
            f"backendDeviceId={backend_device_id} "
            f"childId={assignment.get('childId', '')}",
        )
        assignment_id = assignment.get("assignmentId")
        # _assignment_metadata_errors above already guaranteed assignmentId is a
        # non-empty str (else we returned at metadata_errors); the false edge of this
        # defensive guard is unreachable on this path.
        if isinstance(assignment_id, str) and assignment_id:  # pragma: no cover - assignmentId non-empty str enforced upstream
            existing_runtime = getattr(conn, "lesson_runtime", None)
            existing_forwarder = getattr(existing_runtime, "forwarder", None)
            local_terminal_pending = getattr(
                existing_forwarder, "pending_terminal_batch", None
            )
            if (
                getattr(existing_runtime, "assignment_id", None) == assignment_id
                and local_terminal_pending is not None
            ):
                replay_local = getattr(existing_runtime, "replay_pending_terminal_event", None)
                replayed_local = False
                if callable(replay_local):
                    try:
                        replayed_local = bool(await replay_local())
                    except Exception as exc:
                        _log(
                            "warning",
                            f"local terminal lesson event replay failed: {type(exc).__name__}",
                        )
                if replayed_local:
                    _set_lesson_start_status(
                        conn,
                        "TERMINAL_REPLAYED",
                        "Robot đã đồng bộ kết quả bài học trước đó.",
                    )
                    _log("info", "replayed local pending terminal lesson event; skipping lesson restart")
                else:
                    _set_lesson_start_status(
                        conn,
                        "TERMINAL_REPLAY_PENDING",
                        "Robot đang chờ đồng bộ kết quả bài học trước đó.",
                    )
                    _log("warning", "local pending terminal lesson event blocks lesson restart")
                return existing_runtime
            try:
                from core.lesson.forwarder import (
                    get_terminal_replay_store,
                    replay_stored_terminal_event,
                )

                terminal_store = get_terminal_replay_store()
                pending_terminal = await terminal_store.load(backend_device_id, assignment_id)
                if pending_terminal is None:
                    terminal_replay_state = "none"
                else:
                    replayed_terminal = await replay_stored_terminal_event(
                        device_id=backend_device_id,
                        assignment_id=assignment_id,
                        base_url=base_url,
                        token=token,
                        client=client,
                        logger=logger,
                        terminal_store=terminal_store,
                    )
                    terminal_replay_state = "replayed" if replayed_terminal else "blocked"
            except Exception as exc:
                _log("warning", f"stored terminal lesson event replay failed: {type(exc).__name__}")
                terminal_replay_state = "blocked"
            if terminal_replay_state != "none":
                if terminal_replay_state == "replayed":
                    _set_lesson_start_status(
                        conn,
                        "TERMINAL_REPLAYED",
                        "Robot đã đồng bộ kết quả bài học trước đó.",
                    )
                    _log("info", "replayed pending terminal lesson event; skipping lesson restart")
                else:
                    _set_lesson_start_status(
                        conn,
                        "TERMINAL_REPLAY_PENDING",
                        "Robot đang chờ đồng bộ kết quả bài học trước đó.",
                    )
                    _log("warning", "pending terminal lesson event blocks lesson restart")
                return None
        profile = assignment.get("profile", "espTft")
        try:
            manifest_kwargs = {
                "token": token,
                "renderer_capabilities": requested_renderer_capabilities,
                "lesson_version": assignment.get("lessonVersion"),
            }
            manifest, etag = await backend_api.get_lesson_manifest(
                client,
                base_url,
                assignment.get("lessonId"),
                profile,
                renderer_v2_enabled=renderer_v2_enabled,
                **manifest_kwargs,
            )
        except Exception as exc:
            _backend_unavailable("manifest", exc)
            return None

    if not manifest:
        _set_lesson_start_status(conn, "MANIFEST_EMPTY", "Robot chưa tải được nội dung bài học.")
        _log("warning", "manifest fetch returned empty; aborting lesson start")
        return None

    manifest_identity_errors = _manifest_identity_errors(assignment, manifest)
    existing = getattr(conn, "lesson_runtime", None)
    if manifest_identity_errors:
        _set_lesson_start_status(
            conn,
            "MANIFEST_IDENTITY_MISMATCH",
            "Robot nhận được nội dung bài học không khớp phiên bản được giao.",
        )
        _log(
            "warning",
            "manifest identity mismatch; aborting lesson start: " + ",".join(manifest_identity_errors),
        )
        if existing is not None and getattr(existing, "assignment_id", None) == assignment.get("assignmentId"):
            return existing
        return None

    # ── P5 republish-on-connect (no reconnect required) ─────────────────────────
    # If a runtime is already pinned for THIS device, compare the freshly-pulled
    # assignment's (lessonVersion, assignmentVersion, manifestChecksum) to the live
    # runtime. Unchanged -> keep the existing session (idempotent no-op). Changed ->
    # the author republished; tear down the stale version's cache + runtime and
    # re-pull the new manifest in place. The whole re-pull is GUARDED on
    # is_realtime_busy so it never interrupts an active voice turn — we simply defer
    # until the next connect/poll.
    new_lesson_version = int(assignment.get("lessonVersion", 1))
    new_assignment_version = int(assignment.get("assignmentVersion", 1))
    new_manifest_checksum = parse_manifest_checksum(etag)
    if not new_manifest_checksum:
        _set_lesson_start_status(
            conn,
            "MANIFEST_CHECKSUM_MISSING",
            "Robot chưa xác minh được phiên bản nội dung bài học.",
        )
        _log("warning", "manifest fetch returned missing or malformed checksum; aborting lesson start")
        if existing is not None and getattr(existing, "assignment_id", None) == assignment.get("assignmentId"):
            return existing
        return None
    expected_manifest_checksum = assignment.get("manifestChecksum")
    # _assignment_metadata_errors above already guaranteed manifestChecksum is a
    # non-empty str; the false edge of this defensive guard is unreachable here.
    if isinstance(expected_manifest_checksum, str) and expected_manifest_checksum.strip():  # pragma: no cover - manifestChecksum non-empty str enforced upstream
        expected_manifest_checksum = expected_manifest_checksum.strip()
        if expected_manifest_checksum != new_manifest_checksum:
            _set_lesson_start_status(
                conn,
                "MANIFEST_CHECKSUM_MISMATCH",
                "Robot nhận được nội dung bài học không khớp bản đang được giao.",
            )
            _log(
                "warning",
                "manifest checksum mismatch; aborting lesson start: "
                f"assignment={expected_manifest_checksum[:12]} fetched={new_manifest_checksum[:12]}",
            )
            if existing is not None and getattr(existing, "assignment_id", None) == assignment.get("assignmentId"):
                return existing
            return None
    _log(
        "info",
        "lesson manifest fetched "
        f"assignmentId={assignment.get('assignmentId')} "
        f"lessonId={manifest.get('lessonId') or assignment.get('lessonId')} "
        f"lessonVersion={manifest.get('lessonVersion') or assignment.get('lessonVersion')} "
        f"assignmentVersion={assignment.get('assignmentVersion')} "
        f"manifestChecksum={new_manifest_checksum} "
        f"profile={manifest.get('profile') or profile} "
        f"courseId={manifest.get('courseId', '')} "
        f"deviceId={device_id} "
        f"backendDeviceId={backend_device_id} "
        f"childId={assignment.get('childId', '')} "
        f"stepCount={len(manifest.get('steps', []) or [])} "
        f"assetCount={len(manifest.get('assets', []) or [])} "
        # The step ROSTER, as a `{"steps":[...]}` object. `stepCount` alone says how
        # many steps were served but not which, in what order, or which of them the
        # child has to answer — so nothing downstream can tell "the robot completed
        # every step" from "the robot completed nine of something". The shared
        # checkpoint contract reads exactly this shape (steps[].id +
        # steps[].completionClass), which is also what makes a truncated or reordered
        # manifest detectable rather than merely undercounted.
        f"manifestSteps={_compact_json(_manifest_steps_log_summary(manifest))} "
        f"storyBeat={_compact_json(_manifest_story_log_summary(manifest))}",
    )
    republish_previous = None
    if existing is not None and getattr(existing, "assignment_id", None) == assignment.get("assignmentId"):
        # Re-check after the awaited store/manifest calls above. A terminal
        # forwarder can acquire a local pending batch during that window even
        # when the earlier durable/local checks observed none. This barrier must
        # run before the unchanged/republish split so neither path can replace the
        # runtime while its terminal event still needs durable acknowledgement.
        existing_forwarder = getattr(existing, "forwarder", None)
        local_terminal_pending = getattr(
            existing_forwarder, "pending_terminal_batch", None
        )
        if local_terminal_pending is not None:
            replay = getattr(existing, "replay_pending_terminal_event", None)
            replayed_terminal = False
            if callable(replay):
                try:
                    replayed_terminal = bool(await replay())
                except Exception as exc:
                    _log(
                        "warning",
                        f"terminal lesson event replay failed: {type(exc).__name__}",
                    )
            if replayed_terminal:
                _set_lesson_start_status(
                    conn,
                    "TERMINAL_REPLAYED",
                    "Robot đã đồng bộ kết quả bài học trước đó.",
                )
                _log("info", "replayed terminal lesson event after manifest fetch")
            else:
                _set_lesson_start_status(
                    conn,
                    "TERMINAL_REPLAY_PENDING",
                    "Robot đang chờ đồng bộ kết quả bài học trước đó.",
                )
                _log("warning", "pending terminal lesson event blocks runtime replacement")
            return existing
        unchanged = (
            existing.lesson_version == new_lesson_version
            and existing.assignment_version == new_assignment_version
            and getattr(existing, "manifest_checksum", "") == new_manifest_checksum
        )
        if unchanged:
            if getattr(existing, "state", None) in (S_PAUSED, S_FAILED):
                _log(
                    "info",
                    (
                        "lesson restart requested from "
                        f"{getattr(existing, 'state', None)} state; rebuilding runtime"
                    ),
                )
                try:
                    await existing.close()
                except Exception as exc:  # pragma: no cover - teardown is best-effort
                    _log("warning", f"terminal lesson runtime teardown failed: {type(exc).__name__}")
                conn.lesson_runtime = None
            else:
                _log("info", "lesson republish-on-connect: version unchanged; keeping session")
                return existing
        else:
            busy_check = getattr(conn, "is_realtime_busy", None)
            if callable(busy_check):
                try:
                    if busy_check():
                        _log("info", "lesson republish deferred: realtime voice busy")
                        return existing
                except Exception:  # pragma: no cover - busy_check is best-effort
                    pass
            _log(
                "info",
                "lesson republish-on-connect: version/checksum changed "
                f"v{existing.lesson_version}/a{existing.assignment_version}/m{getattr(existing, 'manifest_checksum', '')[:8]} -> "
                f"v{new_lesson_version}/a{new_assignment_version}/m{new_manifest_checksum[:8]}; preparing candidate",
            )
            # Keep the exact old version/checksum alive until the candidate has
            # completed preload and READY attestation. GC protects this runtime's
            # cache key as previous-known-good after candidate activation.
            republish_previous = existing

    # A different assignment is also a candidate transition: the currently
    # running lesson remains usable until the new assignment reaches READY.
    if republish_previous is None and getattr(conn, "lesson_runtime", None) is existing:
        republish_previous = existing

    async def _cleanup_failed_start(
        reason: str, *, release_connection: bool = True
    ) -> None:
        if republish_previous is not None:
            return
        reset = getattr(conn, "request_lesson_preload_reset", None)
        if callable(reset):
            try:
                reset_ok = await reset(
                    assignment_id=assignment.get("assignmentId"),
                    lesson_id=assignment.get("lessonId"),
                    profile=profile,
                )
                if reset_ok:
                    _log("info", "lesson startup stale-layer reset completed")
                else:
                    _log("warning", "lesson startup stale-layer reset was not acknowledged")
            except Exception as exc:
                _log(
                    "warning",
                    f"lesson startup stale-layer reset failed: {type(exc).__name__}",
                )
        else:
            _log("warning", "lesson startup stale-layer reset unavailable")

        if not release_connection:
            _log("info", "lesson startup connection release already handled")
            return

        release_lesson = getattr(conn, "release_lesson_mode", None)
        if callable(release_lesson):
            try:
                await release_lesson(reason=reason)
                _log("info", "lesson startup connection release completed")
            except Exception as exc:
                _log(
                    "warning",
                    f"lesson startup connection release failed: {type(exc).__name__}",
                )
        else:
            _log("warning", "lesson startup connection release unavailable")

    mcp_ready, mcp_failure_type = await _wait_for_mcp_reconnect_ready(conn, lesson_cfg)
    if not mcp_ready:
        _set_lesson_start_status(
            conn,
            "MCP_DISCOVERY_TIMEOUT",
            "Robot chưa hoàn tất kết nối điều khiển bài học.",
        )
        _log(
            "warning",
            f"lesson MCP reconnect readiness failed: {mcp_failure_type}",
        )
        await _cleanup_failed_start("lesson_start_refused")
        return republish_previous

    gc = _sd_pack_gc_for_connection(conn, lesson_cfg)
    activation = None
    candidate_identity = None
    if gc is not None:
        if not gc.can_preload():
            _set_lesson_start_status(
                conn,
                "SD_PRELOAD_SPACE_LOW",
                "Thẻ nhớ còn dưới ngưỡng an toàn để tải bài học mới.",
            )
            _log("warning", "lesson preload refused: SD free space below preload floor")
            await _cleanup_failed_start("lesson_start_refused")
            return republish_previous
        candidate_cache_key = AssetCache._compose_cache_key(
            str(assignment.get("lessonId") or "lesson"),
            int(assignment.get("lessonVersion", 1)),
            new_manifest_checksum,
        )
        candidate_identity = {
            "cacheKey": candidate_cache_key,
            "lessonVersion": new_lesson_version,
            "manifestChecksum": new_manifest_checksum,
        }
        activation = _sd_pack_activation_for_connection(conn, gc)
        if activation is not None:
            old_cache = getattr(republish_previous, "asset_cache", None)
            old_cache_key = getattr(old_cache, "cache_key", None)
            if isinstance(old_cache_key, str) and old_cache_key:
                activation.set_current_if_empty(
                    {
                        "cacheKey": old_cache_key,
                        "lessonVersion": int(getattr(republish_previous, "lesson_version", 0)),
                        "manifestChecksum": str(
                            getattr(republish_previous, "manifest_checksum", "")
                        ),
                    }
                )
            activation.begin_candidate(candidate_identity)
        result = gc.collect_one(
            active_cache_key=getattr(
                getattr(getattr(conn, "lesson_runtime", None), "asset_cache", None),
                "cache_key",
                None,
            ),
            preloading_cache_key=candidate_cache_key,
            current_cache_key=(
                activation.current_cache_key
                if activation is not None
                else getattr(conn, "lesson_current_cache_key", None)
            ),
            previous_known_good_cache_key=(
                activation.previous_known_good_cache_key
                if activation is not None
                else getattr(conn, "lesson_previous_known_good_cache_key", None)
            ),
        )
        if result.get("deleted"):
            _log("info", f"lesson SD GC deleted cacheKey={result['deleted']}")

    asset_cache = AssetCache(
        assets=_manifest_asset_cache_inputs(manifest),
        profile=profile,
        asset_origin_base=lesson_cfg.get("asset_origin_base"),
        public_base_url=lesson_asset_public_base_url(config),
        asset_pack_local_root=lesson_cfg.get("asset_pack_local_root"),
        asset_pack_mount_root=lesson_cfg.get("asset_pack_mount_root"),
        lesson_key=str(assignment.get("lessonId") or "lesson"),
        lesson_version=int(assignment.get("lessonVersion", 1)),
        manifest_checksum=new_manifest_checksum,
        preload_timeout_sec=_positive_float_or_default(lesson_cfg.get("preload_timeout_sec", 90), 90.0),
        concurrency=_positive_int_or_default(lesson_cfg.get("preload_concurrency", 2), 2),
        max_asset_bytes=_positive_int_or_default(lesson_cfg.get("max_asset_bytes", 8 * 1024 * 1024), 8 * 1024 * 1024),
        max_total_asset_bytes=_positive_int_or_default(lesson_cfg.get("max_total_asset_bytes", 64 * 1024 * 1024), 64 * 1024 * 1024),
        busy_check=getattr(conn, "is_realtime_busy", None),
        logger=logger,
    )
    forwarder = LessonEventForwarder(
        device_id=backend_device_id, base_url=base_url, token=token, logger=logger
    )

    async def _report_preload_status(report: Dict[str, Any]) -> None:
        timeout_sec = _finite_float_or_default(lesson_cfg.get("preload_status_timeout_sec", 5.0), 5.0)
        retry_delay = _finite_float_or_default(lesson_cfg.get("preload_status_retry_delay_sec", 1.0), 1.0)
        max_retries = _int_or_default(lesson_cfg.get("preload_status_max_retries", 2), 2)
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_sec),
            limits=httpx.Limits(max_keepalive_connections=0),
            follow_redirects=True,
        ) as report_client:
            await backend_api.post_preload_status(
                report_client,
                base_url,
                backend_device_id,
                report,
                token=token,
                max_retries=max_retries,
                retry_delay=retry_delay,
            )

    # S13 voice-latency-during-preload auto-disable alarm (plan §11.2 / CP-8). One
    # alarm per connection, reused across runtimes so its sample window survives a
    # re-pull. The disable callback flips the ESP LESSON_RUNTIME_ENABLED flag off.
    alarm = getattr(conn, "lesson_voice_alarm", None)
    if alarm is None:
        try:
            from core.lesson.preload_voice_alarm import PreloadVoiceLatencyAlarm

            alarm = PreloadVoiceLatencyAlarm(
                disable_callback=getattr(conn, "_disable_lesson_runtime", None),
                threshold_ms=lesson_cfg.get("voice_rt_p95_disable_ms"),
                logger=logger,
            )
            conn.lesson_voice_alarm = alarm
        except Exception as exc:  # pragma: no cover - alarm is best-effort
            _log("warning", f"voice-latency alarm unavailable: {type(exc).__name__}")
            alarm = None
    runtime = LessonRuntime(
        conn,
        assignment=assignment,
        manifest=manifest,
        asset_cache=asset_cache,
        forwarder=forwarder,
        manifest_checksum=new_manifest_checksum,
        min_step_timeout_sec=lesson_cfg.get("step_timeout_floor_sec", 0),
        alarm=alarm,
        preload_status_reporter=_report_preload_status,
    )
    # Terminal same-assignment rebuilds may already have closed their runtime. Live
    # current runtimes, including a different assignment, stay open as the fallback
    # until the candidate passes READY attestation below.
    prior = getattr(conn, "lesson_runtime", None)
    if prior is not None and prior is not runtime and prior is not republish_previous:
        try:
            await prior.close()
        except Exception as exc:  # pragma: no cover - teardown is best-effort
            _log("warning", f"prior lesson runtime teardown failed: {type(exc).__name__}")
    conn.lesson_runtime_candidate = runtime
    mode_not_captured = object()

    async def _abort_candidate_for_fallback_terminal(
        phase: str, *, restore_mode: Any = mode_not_captured
    ) -> bool:
        previous_forwarder = getattr(republish_previous, "forwarder", None)
        previous_terminal_pending = getattr(
            previous_forwarder, "pending_terminal_batch", None
        )
        if republish_previous is None or previous_terminal_pending is None:
            return False
        replay_previous = getattr(
            republish_previous, "replay_pending_terminal_event", None
        )
        replayed_previous = False
        if callable(replay_previous):
            try:
                replayed_previous = bool(await replay_previous())
            except Exception as exc:
                _log(
                    "warning",
                    f"{phase} terminal lesson event replay failed: {type(exc).__name__}",
                )
        if getattr(conn, "lesson_runtime_candidate", None) is runtime:
            conn.lesson_runtime_candidate = None
        if activation is not None:
            activation.abort_candidate()
        try:
            await runtime.close()
        except Exception as exc:
            _log(
                "warning",
                f"terminal-barrier candidate teardown failed: {type(exc).__name__}",
            )
        if restore_mode is not mode_not_captured:
            set_session_mode = getattr(conn, "_set_session_mode", None)
            if callable(set_session_mode):
                try:
                    set_session_mode(restore_mode, reason="lesson_candidate_aborted")
                except Exception as exc:
                    _log(
                        "warning",
                        f"lesson candidate mode rollback failed: {type(exc).__name__}",
                    )
            elif hasattr(conn, "session_mode"):
                conn.session_mode = restore_mode
                if hasattr(conn, "audio_channel_owner"):
                    conn.audio_channel_owner = restore_mode
        if replayed_previous:
            _set_lesson_start_status(
                conn,
                "TERMINAL_REPLAYED",
                "Robot đã đồng bộ kết quả bài học trước đó.",
            )
            _log("info", f"replayed fallback terminal lesson event phase={phase}")
        else:
            _set_lesson_start_status(
                conn,
                "TERMINAL_REPLAY_PENDING",
                "Robot đang chờ đồng bộ kết quả bài học trước đó.",
            )
            _log("warning", f"fallback terminal lesson event blocks candidate phase={phase}")
        return True

    try:
        preload_only = getattr(runtime, "preload_only", None)
        start_protocol = getattr(runtime, "start_protocol", None)
        split_start = callable(preload_only) and callable(start_protocol)
        if split_start:
            preloaded = await preload_only()
        else:  # Compatibility for injected/legacy runtime implementations.
            enter_lesson = getattr(conn, "enter_lesson_mode", None)
            if callable(enter_lesson):
                await enter_lesson(reason="lesson_start")
            conn.lesson_runtime = runtime
            conn.lesson_runtime_candidate = None
            await runtime.start()
            preloaded = True
        if not preloaded or runtime.state == S_FAILED:
            _set_lesson_start_status(conn, "START_REFUSED", "Robot chưa hiển thị được bài học.")
            if getattr(conn, "lesson_runtime_candidate", None) is runtime:
                conn.lesson_runtime_candidate = None
            try:
                await runtime.close()
            except Exception as exc:  # pragma: no cover - teardown is best-effort
                _log("warning", f"failed lesson runtime teardown failed: {type(exc).__name__}")
            if activation is not None:
                activation.abort_candidate()
            await _cleanup_failed_start(
                "lesson_start_refused",
                release_connection=runtime.state != S_FAILED,
            )
            return republish_previous
        if split_start and await _abort_candidate_for_fallback_terminal("post_preload"):
            return republish_previous
        if split_start:
            enter_lesson = getattr(conn, "enter_lesson_mode", None)
            previous_session_mode = getattr(conn, "session_mode", mode_not_captured)
            if callable(enter_lesson):
                await enter_lesson(reason="lesson_start")
            if await _abort_candidate_for_fallback_terminal(
                "post_enter_lesson_mode", restore_mode=previous_session_mode
            ):
                return republish_previous
            # No await is allowed between this final terminal check, synchronous
            # asset activation, and the runtime swap.
            if activation is not None and candidate_identity is not None:
                if not activation.verify_for_activation(candidate_identity) or not activation.activate_candidate(
                    candidate_identity
                ):
                    _set_lesson_start_status(conn, "ASSET_PACK_NOT_READY", "Gói bài học chưa xác minh xong.")
                    if getattr(conn, "lesson_runtime_candidate", None) is runtime:
                        conn.lesson_runtime_candidate = None
                    activation.abort_candidate()
                    await runtime.close()
                    await _cleanup_failed_start("lesson_start_refused")
                    return republish_previous
            conn.lesson_runtime = runtime
            conn.lesson_runtime_candidate = None
            await start_protocol(preloaded=True)
        elif activation is not None and candidate_identity is not None:
            # Preserve compatibility-runtime ordering: legacy start() already ran.
            if not activation.verify_for_activation(candidate_identity) or not activation.activate_candidate(
                candidate_identity
            ):
                _set_lesson_start_status(conn, "ASSET_PACK_NOT_READY", "Gói bài học chưa xác minh xong.")
                if getattr(conn, "lesson_runtime_candidate", None) is runtime:
                    conn.lesson_runtime_candidate = None
                activation.abort_candidate()
                await runtime.close()
                await _cleanup_failed_start("lesson_start_refused")
                return republish_previous
        _set_lesson_start_status(conn, "STARTED")
        if republish_previous is not None:
            old_cache = getattr(republish_previous, "asset_cache", None)
            old_cache_key = getattr(old_cache, "cache_key", None)
            if isinstance(old_cache_key, str) and old_cache_key:
                conn.lesson_previous_known_good_cache_key = old_cache_key
            conn.lesson_current_cache_key = getattr(asset_cache, "cache_key", None)
            try:
                await republish_previous.close()
            except Exception as exc:  # pragma: no cover - teardown is best-effort
                _log("warning", f"old lesson runtime teardown failed: {type(exc).__name__}")
    except LessonError as err:
        _set_lesson_start_status(conn, "START_REFUSED", "Robot chưa hiển thị được bài học.")
        _log("warning", f"lesson start refused: {err.code}")
        if getattr(conn, "lesson_runtime_candidate", None) is runtime:
            conn.lesson_runtime_candidate = None
        if getattr(conn, "lesson_runtime", None) is runtime:
            conn.lesson_runtime = republish_previous
        if activation is not None:
            previous = activation.previous_known_good
            if previous is None or not activation.rollback(previous):
                activation.abort_candidate()
        try:
            await runtime.close()
        except Exception as exc:  # pragma: no cover - teardown is best-effort
            _log("warning", f"refused lesson runtime teardown failed: {type(exc).__name__}")
        await _cleanup_failed_start("lesson_start_refused")
        return republish_previous
    except Exception as exc:  # noqa: BLE001 - candidate failure must preserve active runtime
        _set_lesson_start_status(conn, "START_REFUSED", "Robot chưa hiển thị được bài học.")
        _log("warning", f"lesson candidate crashed: {type(exc).__name__}")
        if getattr(conn, "lesson_runtime_candidate", None) is runtime:
            conn.lesson_runtime_candidate = None
        if getattr(conn, "lesson_runtime", None) is runtime:
            conn.lesson_runtime = republish_previous
        if activation is not None:
            previous = activation.previous_known_good
            if previous is None or not activation.rollback(previous):
                activation.abort_candidate()
        try:
            await runtime.close()
        except Exception as close_exc:  # pragma: no cover - teardown is best-effort
            _log("warning", f"crashed lesson runtime teardown failed: {type(close_exc).__name__}")
        await _cleanup_failed_start("lesson_start_failed")
        return republish_previous
    return runtime


def _sd_pack_gc_for_connection(conn: Any, lesson_cfg: Dict[str, Any]) -> Any:
    mount_root = lesson_cfg.get("asset_pack_mount_root")
    if not mount_root:
        return None
    try:
        from core.lesson.sd_pack_gc import SdPackGarbageCollector
        from core.lesson.shared_asset_store import SharedAssetStore

        mounted = Path(str(mount_root)).resolve()
        store = SharedAssetStore(mounted.parent, pack_root=mounted, cleanup_on_init=False)
        runtime = getattr(conn, "lesson_runtime", None)
        render_busy = lambda: getattr(runtime, "state", None) in (S_RUNNING, S_PAUSED)
        gc = SdPackGarbageCollector(
            mounted,
            shared_store=store,
            quota_bytes=_positive_int_or_default(lesson_cfg.get("sd_cache_quota_bytes", 0), 0),
            gc_free_percent=lesson_cfg.get("sd_gc_free_percent", 20),
            preload_min_free_percent=lesson_cfg.get("sd_preload_min_free_percent", 5),
            voice_busy=getattr(conn, "is_realtime_busy", None),
            render_busy=render_busy,
        )
        if mounted not in _SD_PACK_BOOT_CLEANED_ROOTS:
            gc.boot_cleanup()
            _SD_PACK_BOOT_CLEANED_ROOTS.add(mounted)
        return gc
    except (OSError, TypeError) as exc:
        logger = getattr(conn, "logger", None)
        if logger is not None:
            logger.warning(
                with_lesson_log_context(
                    f"lesson SD GC unavailable: {type(exc).__name__}", conn
                )
            )
        return None


def _sd_pack_activation_for_connection(conn: Any, gc: Any) -> Any:
    activation = getattr(conn, "lesson_sd_pack_activation", None)
    if activation is not None:
        return activation
    try:
        from core.lesson.sd_pack_gc import SdPackActivationState

        activation = SdPackActivationState(gc.shared_store)
        conn.lesson_sd_pack_activation = activation
        return activation
    except (OSError, ValueError, TypeError):
        return None


def rollback_sd_pack_assignment(conn: Any, assignment: Dict[str, Any]) -> bool:
    """Re-attest and activate only the exact backend rollback identity."""
    activation = getattr(conn, "lesson_sd_pack_activation", None)
    if activation is None or not isinstance(assignment, dict):
        return False
    lesson_id = assignment.get("lessonId")
    lesson_version = assignment.get("lessonVersion")
    checksum = assignment.get("manifestChecksum")
    if not isinstance(lesson_id, str) or not isinstance(lesson_version, int):
        return False
    if not isinstance(checksum, str) or not checksum:
        return False
    from core.lesson.asset_cache import AssetCache

    identity = {
        "cacheKey": AssetCache._compose_cache_key(lesson_id, lesson_version, checksum),
        "lessonVersion": lesson_version,
        "manifestChecksum": checksum,
    }
    if not activation.rollback(identity):
        return False
    conn.lesson_current_cache_key = activation.current_cache_key
    conn.lesson_previous_known_good_cache_key = activation.previous_known_good_cache_key
    return True
