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
import math
import time
import unicodedata
from typing import Any, Awaitable, Callable, Dict, List, Optional

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
    lesson_capability_ok,
    device_renderer_capabilities,
)
from core.utils.util import get_vision_url

TAG = "LessonRuntime"

# Keep command frames small. Images/media must travel as URLs or verified SD paths,
# never inline JSON, so 16 KiB is generous for a 3-layer step with prompts/choices.
MAX_LESSON_FRAME_BYTES = 16 * 1024

NO_CURRENT_ASSIGNMENT_MESSAGE = "Robot chưa có bài học nào được giao."


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

# Runtime states (a slice subset of the assignment state machine).
S_IDLE = "IDLE"
S_PRELOADING = "PRELOADING"
S_READY = "READY"
S_RUNNING = "RUNNING"
S_PAUSED = "PAUSED"
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
CHILD_RESPONSE_INTENT_HELP_OR_REPEAT = "help_or_repeat"
CHILD_RESPONSE_INTENT_UNKNOWN_OR_FRUSTRATED = "unknown_or_frustrated"
CHILD_RESPONSE_INTENT_VIETNAMESE_OBJECT = "vietnamese_object"
CHILD_RESPONSE_INTENT_ALREADY_IN_LESSON = "already_in_lesson"

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

def _contains_any_token_sequence(tokens: List[str], expected_values: List[str]) -> bool:
    return any(_contains_token_sequence(tokens, _matching_tokens(value)) for value in expected_values)

def _child_response_interaction_prompt(step: Dict[str, Any], key: str) -> Optional[str]:
    prompts = step.get("interactionPrompts")
    if not isinstance(prompts, dict):
        return None
    prompt = prompts.get(key)
    if isinstance(prompt, str) and prompt.strip():
        return prompt.strip()
    return None

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
        _contains_any_token_sequence(tokens, ["cái kho", "nhà kho"])
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
        raw_word = vocab.get("expectedResponse") or vocab.get("targetWord")
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
    for expected in expected_responses:
        expected_tokens = _matching_tokens(expected)
        if _contains_token_sequence(response_tokens, expected_tokens):
            return True
    return False

def _child_response_retry_prompt(
    step: Dict[str, Any],
    expected_responses: List[str],
    response: Any = None,
) -> str:
    retry = step.get("retryPrompt")
    if isinstance(retry, str) and retry.strip():
        base = retry.strip()
    elif expected_responses:
        base = f"Con thử nói lại nhé: {expected_responses[0]}."
    else:
        base = "Con thử nói lại nhé."
    return base

def _child_response_coaching_prompt(
    step: Dict[str, Any],
    expected_responses: List[str],
    response: Any,
    intent: str,
) -> str:
    target = expected_responses[0] if expected_responses else "từ này"
    if intent == CHILD_RESPONSE_INTENT_HELP_OR_REPEAT:
        return (
            _child_response_interaction_prompt(step, "helpOrRepeat")
            or f"Mình nhắc lại nhé. Con nhìn hình và nói theo mình: {target}."
        )
    if intent == CHILD_RESPONSE_INTENT_UNKNOWN_OR_FRUSTRATED:
        return (
            _child_response_interaction_prompt(step, "unknownOrFrustrated")
            or f"Không sao. Con nhìn hình cái kho màu đỏ nhé. Tiếng Anh là {target}. Con thử nói: {target}."
        )
    if intent == CHILD_RESPONSE_INTENT_VIETNAMESE_OBJECT:
        return (
            _child_response_interaction_prompt(step, "vietnameseObject")
            or f"Đúng rồi, đó là cái kho. Bây giờ mình nói tên tiếng Anh của cái kho: {target}."
        )
    if intent == CHILD_RESPONSE_INTENT_ALREADY_IN_LESSON:
        return (
            _child_response_interaction_prompt(step, "alreadyInLesson")
            or f"Mình đang học bài này rồi. Con nhìn hình và nói từ {target} nhé."
        )
    return _child_response_retry_prompt(step, expected_responses, response)

def _child_response_success_prompt(step: Dict[str, Any]) -> Optional[str]:
    success = step.get("successPrompt")
    if isinstance(success, str) and success.strip():
        return success.strip()
    return None

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
    lesson_cfg = config.get("lesson", {}) or {}
    server_cfg = config.get("server", {}) or {}
    explicit = (
        lesson_cfg.get("asset_public_base_url")
        or lesson_cfg.get("asset_public_base")
        or server_cfg.get("asset_public_base_url")
    )
    if explicit:
        return str(explicit).rstrip("/")
    if "server" not in config:
        return ""
    vision_url = get_vision_url(config)
    if vision_url and "/mcp/vision/explain" in vision_url:
        return vision_url.replace("/mcp/vision/explain", "").rstrip("/")
    return ""


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
        # sessionId SHOULD equal the WS session_id; a fresh WS connection already
        # mints a fresh session_id, which cleanly resumes the (assignmentId,sessionId)
        # sequence namespace on ESP restart (plan §6.3.5 — "fresh sessionId" option).
        self.session_id = assignment.get("sessionId") or getattr(conn, "session_id", None)
        self._trace_context = _lesson_trace_context_from_headers(getattr(conn, "headers", None))
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
        self._step_timeout_task: Optional[asyncio.Task] = None
        self._passive_dwell_task: Optional[asyncio.Task] = None
        self._child_response_timeout_task: Optional[asyncio.Task] = None
        self._child_response_timeout_count = 0
        self._step_seq: Optional[int] = None
        self._step_id: Optional[str] = None
        self._step: Optional[Dict[str, Any]] = None  # the in-flight step row
        self._step_passive = False  # cached _is_passive_step(self._step)
        self._step_acked = False
        self._step_completed = False
        self._completed_step_ids: set[str] = set()
        self._child_response_window_open = False
        self._closed = False
        # Guards the single durable lesson_failed forward so re-entrant FAILED
        # transitions (e.g. a late lesson_error after an earlier timeout) cannot
        # enqueue a second terminal event for the same run.
        self._failure_forwarded = False
        self._completion_stop_sent = False

        # P5 multi-step playback: the ordered renderable manifest steps + a cursor.
        # The slice ran ONE step; P5 advances through ALL of them in manifest order,
        # one lesson_step per step, each gated on render ack plus either passive
        # auto-advance or interactive child response evidence.
        self._steps: List[Dict[str, Any]] = self._select_steps()
        self._step_index = -1  # bumped to 0 by the first _emit_step()
        self._steps_completed = 0  # real count for lesson_completed.summary

    # ── lifecycle ──────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Validate gates, then send ``lesson_prepare`` (seq 1). Raises a
        ``LessonError`` for any pre-send gate failure (capability / protocol /
        profile) so the caller logs it and NEVER puts a frame on the wire."""
        features = getattr(self.conn, "features", None)
        if not lesson_capability_ok(features):
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
        manifest_version = self.manifest.get("manifestVersion")
        if manifest_version not in self.renderer_capabilities:
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
        supported = (config.get("lesson", {}) or {}).get("supported_profiles") or ["espTft"]
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

        self.state = S_PRELOADING
        if self._sd_asset_pack_enabled():
            ready = await self._preload_sd_asset_pack_before_prepare()
            if not ready:
                return
        await self._emit("lesson_prepare", body=self._prepare_body())

    async def close(self) -> None:
        self._closed = True
        self._cancel_frame_ack_timeout()
        self._cancel_step_timeout()
        self._cancel_passive_dwell()
        self._cancel_child_response_timeout()
        if self._preload_task is not None and not self._preload_task.done():
            self._preload_task.cancel()
        for task in list(self._preload_status_report_tasks):
            if not task.done():
                task.cancel()
        if self._preload_status_report_tasks:
            await asyncio.gather(*self._preload_status_report_tasks, return_exceptions=True)
            self._preload_status_report_tasks.clear()
        if self.forwarder is not None:
            await self.forwarder.aclose()
        if self.asset_cache is not None:
            await self.asset_cache.aclose()

    def _is_active_runtime(self) -> bool:
        if self._closed:
            return False
        current = getattr(self.conn, "lesson_runtime", None)
        return current is None or current is self

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

    async def on_lesson_ack(self, msg_json: Dict[str, Any]) -> None:
        if not self._is_active_runtime():
            return
        if self.state in (S_FAILED, S_PAUSED, S_COMPLETED):
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
            if acked is None:
                await self._accept_inbound(msg_json.get("sequence"))
            # Stale / unknown ack -> idempotent no-op (re-ack semantics, plan §5.8).
            return
        if (await self._accept_inbound(msg_json.get("sequence"))) != "ok":
            return
        self._outstanding.pop(acked, None)
        self._cancel_frame_ack_timeout()
        await self._on_frame_acked(frame, body)

    def _legacy_empty_ack_outstanding_seq(self, msg_json: Dict[str, Any], body: Dict[str, Any]) -> Optional[int]:
        if msg_json.get("type") != "lesson_ack":
            return None
        if body:
            return None
        if any(
            key in msg_json
            for key in ("assignmentId", "sessionId", "lessonId", "lessonVersion", "stepId", "sequence")
        ):
            return None
        if len(self._outstanding) != 1:
            return None
        seq = next(iter(self._outstanding))
        self._log("info", f"legacy empty lesson_ack correlated seq={seq}")
        return seq

    async def on_lesson_progress(self, msg_json: Dict[str, Any]) -> None:
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
        if self._step_completed:
            return False
        internal_probe = str(source or "") == "internal_dev_endpoint"
        if not self._child_response_window_open and not internal_probe:
            return False

        expected_responses = _coerce_expected_child_responses(self._step)
        response_intent = _classify_child_response_intent(response, expected_responses)
        if response_intent != CHILD_RESPONSE_INTENT_CORRECT:
            self._cancel_child_response_timeout()
            self._close_child_response_window()
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
        self._close_child_response_window()
        self._step_completed = True
        success_prompt = _child_response_success_prompt(self._step)
        if success_prompt is not None:
            await self._speak_lesson_prompt_text(
                success_prompt,
                step_id=self._step_id,
                continue_listening=False,
            )
        await self._maybe_finish_step()
        return True

    async def on_lesson_error(self, msg_json: Dict[str, Any]) -> None:
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
        # A firmware-reported error on the active step fails the run (slice scope).
        if self.state in (S_RUNNING, S_PRELOADING):
            self.state = S_FAILED
            self._cancel_step_timeout()
            self._cancel_child_response_timeout()
            await self._notify_lesson_terminal("lesson_error")

    # ── state machine ──────────────────────────────────────────────────────────

    async def _on_frame_acked(self, frame: Dict[str, Any], ack_body: Dict[str, Any]) -> None:
        if not self._is_active_runtime():
            return
        ftype = frame.get("type")
        if ftype == "lesson_prepare":
            if self._sd_asset_pack_enabled() and not self._ack_reports_asset_pack_ready(ack_body):
                self.last_error = LessonError(
                    ASSET_PACK_NOT_READY,
                    "device did not report verified SD asset pack before lesson start",
                    retryable=True,
                )
                self.state = S_FAILED
                await self._emit_error(self.last_error)
                await self._notify_lesson_terminal("asset_pack_not_ready")
                return
            if self._sd_asset_pack_enabled():
                self.state = S_READY
                self._forward({"type": "preload_ready"})
                await self._emit("lesson_start", body={})
                return
            # Prepare delivered -> begin the download+verify (D-PRELOAD-OWNER).
            self._preload_task = asyncio.create_task(self._run_preload())
        elif ftype == "lesson_start":
            self.state = S_RUNNING
            self._forward({"type": "lesson_started", "startedAt": _wire_timestamp()})
            await self._emit_step()
        elif ftype == "lesson_step":
            # Step delivery is confirmed ONLY by its ack (plan §5.8) -> clear timeout.
            self._cancel_step_timeout()
            self._step_acked = True
            # The child-facing narration/question must wait until firmware has
            # acknowledged the rendered lesson_step. This keeps the visual scene
            # (backgroundScene + teachingObject + robotOverlay) on screen before
            # TeeBot starts teaching or listening for the child's reply.
            if self._step is not None:
                await self._speak_step_prompt(self._step)
            if not self._is_active_runtime():
                return
            if self._step_passive:
                # PASSIVE narration (greeting/review/focus/feedback/celebrate): the
                # firmware NEVER sends step_completed, so the ack IS the completion
                # signal — auto-advance. Without this the run would hang forever in
                # S_RUNNING (the per-step timeout is cancelled on ack, so it can no
                # longer fire either). Interactive steps still wait for step_completed.
                #
                # DWELL (step.dwellSec / lesson.passive_step_dwell_sec): keep the rendered
                # scene on screen for N seconds BEFORE auto-advancing, so the child can
                # actually see/hear a passive step instead of it flashing past on its ack.
                # Default 0 -> advance immediately on ack (byte-for-byte the prior behavior;
                # real lessons that set no dwell are unchanged). The dwell runs as a guarded
                # task so it never blocks the inbound receive loop.
                dwell = self._passive_dwell_sec()
                if dwell > 0:
                    self._start_passive_dwell(self._step_seq, self._step_id, dwell)
                    return
                self._step_completed = True
            else:
                await self._open_child_response_window()
                if self._child_response_window_still_current(self._step_id, self._step_seq):
                    self._start_child_response_timeout()
            await self._maybe_finish_step()
        elif ftype == "lesson_stop":
            self.state = S_COMPLETED
            self._log("info", f"lesson_completed stepsCompleted={self._steps_completed}")
            self._forward(
                {
                    "type": "lesson_completed",
                    "completedAt": _wire_timestamp(),
                    "summary": {"stepsCompleted": self._steps_completed},
                }
            )
            await self._notify_lesson_terminal("lesson_completed")

    async def _notify_lesson_terminal(self, reason: str) -> None:
        # Every S_FAILED path routes through here. Forward ONE durable terminal
        # lesson_failed (the forwarder classifies it terminal -> stored + reconnect
        # -replayed) so the backend assignment leaves its single-active slot and
        # persists the failure. lesson_completed/lesson_abandoned forward their own
        # terminal events at their call sites; FAILED had none. The forward happens
        # BEFORE the release hook so a connection without release_lesson_mode still
        # reports the failure.
        if self.state == S_FAILED and not self._failure_forwarded:
            self._failure_forwarded = True
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
        # Start gate satisfied -> now (and only now) emit lesson_start (seq 2).
        await self._emit("lesson_start", body={})

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
                "ESP could not materialize verified SD asset pack before lesson_prepare",
                retryable=True,
            )
            self.state = S_FAILED
            await self._emit_error(self.last_error)
            await self._notify_lesson_terminal("sd_asset_pack_not_ready")
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
        self._step_index += 1
        self._cancel_child_response_timeout()
        self._cancel_passive_dwell()
        step = self._steps[self._step_index]
        self._step = step
        self._step_passive = _is_passive_step(step)
        self._step_id = step.get("id")
        self._step_acked = False
        self._step_completed = False
        self._child_response_window_open = False
        self._child_response_timeout_count = 0
        raw_timeout_sec = step.get("timeoutSec") or self._default_step_timeout_sec
        try:
            timeout_sec = max(float(raw_timeout_sec), self._min_step_timeout_sec)
            if not math.isfinite(timeout_sec):
                raise ValueError
        except (TypeError, ValueError):
            timeout_sec = max(float(self._default_step_timeout_sec), self._min_step_timeout_sec)
        body = self._step_body(step)
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
        self._start_step_timeout(self._step_seq, self._step_id, timeout_sec)

    async def _speak_step_prompt(self, step: Dict[str, Any]) -> None:
        prompt = _spoken_step_prompt(step)
        if prompt is None:
            return
        await self._speak_lesson_prompt_text(
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
    ) -> None:
        if not self._is_active_runtime():
            return
        provider = getattr(self.conn, "voice_provider", None)
        speaker = getattr(provider, "speak_lesson_step_prompt", None)
        if not callable(speaker):
            return
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
                f"lesson step prompt handoff stepId={step_id or self._step_id or ''} handoff={int(handed_off)}",
            )
        except Exception as exc:  # pragma: no cover - voice prompt is best-effort
            self._log(
                "warning",
                f"lesson step prompt voice handoff failed stepId={step_id or self._step_id or ''}: {type(exc).__name__}",
            )

    async def _maybe_finish_step(self) -> None:
        if not self._is_active_runtime():
            return
        if not (self.state == S_RUNNING and self._step_acked and self._step_completed):
            return
        last_step = self._step_index + 1 >= len(self._steps)
        if last_step and self._completion_stop_sent:
            return
        self._cancel_child_response_timeout()
        # A step is done once it is acked AND its step_completed progress arrived
        # (plan §5.1). Count it, then either advance to the next manifest step or,
        # if this was the last one, stop with the real stepsCompleted count.
        self._steps_completed += 1
        if isinstance(self._step_id, str):
            self._completed_step_ids.add(self._step_id)
        if not last_step:
            await self._emit_step()  # next step in manifest order
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
        lesson_cfg = config.get("lesson", {}) or {}
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
            self._step_completed = True
            await self._maybe_finish_step()

        self._passive_dwell_task = asyncio.create_task(_dwell())

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
        lesson_cfg = config.get("lesson", {}) or {}
        raw = lesson_cfg.get("frame_ack_timeout_sec", lesson_cfg.get("ack_timeout_sec", 12.0))
        try:
            parsed = float(raw)
        except (TypeError, ValueError):
            parsed = 12.0
        return max(0.0, parsed) if math.isfinite(parsed) else 12.0

    def _frame_ack_max_retries(self) -> int:
        config = getattr(self.conn, "config", {}) or {}
        lesson_cfg = config.get("lesson", {}) or {}
        raw = lesson_cfg.get(
            "frame_ack_max_retries",
            lesson_cfg.get("lifecycle_frame_ack_max_retries", 1),
        )
        try:
            parsed = int(raw)
        except (TypeError, ValueError):
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
            if seq not in self._outstanding or self.state in (S_FAILED, S_PAUSED, S_COMPLETED):
                return
            frame = self._outstanding.pop(seq, None) or {}
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
                await self._emit(
                    frame_type,
                    step_id=step_id,
                    body=copy.deepcopy(frame.get("body") or {}),
                    frame_ack_retry_count=retry_count + 1,
                )
                return
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

        self._frame_ack_timeout_task = asyncio.create_task(_timeout())

    def _cancel_frame_ack_timeout(self) -> None:
        if self._frame_ack_timeout_task is not None and not self._frame_ack_timeout_task.done():
            self._frame_ack_timeout_task.cancel()
        self._frame_ack_timeout_task = None

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
        lesson_cfg = config.get("lesson", {}) or {}
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
        lesson_cfg = config.get("lesson", {}) or {}
        raw = step.get("maxNoAnswerAttempts") or lesson_cfg.get("max_no_answer_attempts") or 2
        try:
            parsed = int(raw)
        except (TypeError, ValueError):
            parsed = 2
        return max(1, parsed)

    async def _handle_child_response_timeout(self, step_id: Optional[str]) -> None:
        if not self._is_active_runtime():
            return
        self._child_response_timeout_count += 1
        self._close_child_response_window()
        if self._child_response_timeout_count < self._max_child_response_timeouts():
            self._log("info", f"child response inactive; reprompt stepId={step_id}")
            await self._speak_lesson_prompt_text("Con thử nói lại nhé.", step_id=step_id)
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
        success_prompt = _child_response_success_prompt(self._step)
        if success_prompt is not None:
            await self._speak_lesson_prompt_text(
                success_prompt, step_id=step_id, continue_listening=False
            )
        await self._maybe_finish_step()

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
        frame = self._envelope(frame_type, step_id=step_id, sequence=seq, body=frame_body)
        if frame_type == "lesson_step":
            scene = frame["body"].get("scene") or {}
            story_beat = frame["body"].get("storyBeat")
            self._log(
                "info",
                "emit lesson_step "
                f"stepId={step_id} "
                f"stepType={frame['body'].get('stepType')} "
                f"backgroundScene={int(bool(scene.get('backgroundScene')))} "
                f"teachingObject={int(bool(scene.get('teachingObject')))} "
                f"robotOverlay={int(bool(scene.get('robotOverlay')))} "
                f"prompt={int(bool(frame['body'].get('audio')))} "
                f"completionClass={frame['body'].get('completionClass', '')} "
                f"storyBeat={_compact_json(story_beat) if story_beat is not None else '{}'}",
            )
        elif frame_type in ("lesson_prepare", "lesson_start", "lesson_stop"):
            self._log("info", f"emit {frame_type} stepId={step_id or ''}")
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
        if frame_type in {"lesson_prepare", "lesson_start", "lesson_stop"}:
            self._start_frame_ack_timeout(frame_type, seq, step_id)
        await self._send(payload)
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
            if self._sd_asset_pack_enabled() and not self._is_sd_asset_pack_source(node.get("src")):
                return reason
        return None

    def _is_sd_asset_pack_source(self, source: Any) -> bool:
        if not isinstance(source, str):
            return False
        source = source.strip()
        if not source:
            return False
        config = getattr(self.conn, "config", {}) or {}
        lesson_cfg = config.get("lesson", {}) or {}
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
        seq = self._next_seq()
        frame = self._envelope("lesson_error", step_id=None, sequence=seq, body=err.to_body())
        await self._send(json.dumps(frame, ensure_ascii=False))

    async def _default_send(self, payload: str) -> None:
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
        if self._sd_asset_pack_enabled():
            pack = getattr(self.asset_cache, "asset_pack_manifest", None)
            if callable(pack):
                body["assetPack"] = pack(
                    assignment_version=self.assignment_version,
                    lesson_id=self.lesson_id,
                    lesson_version=self.lesson_version,
                    manifest_checksum=self.manifest_checksum,
                )
        return body

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
        for key in ("subject", "helperText", "l1TransferHint", "choices", *STEP_METADATA_KEYS):
            value = step.get(key)
            if value is not None:
                body[key] = value
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
        if self._sd_asset_pack_enabled():
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
                        if self._sd_asset_pack_enabled()
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
        lesson_cfg = config.get("lesson", {}) or {}
        mode = str(lesson_cfg.get("asset_delivery_mode") or "").strip().lower()
        return mode == "sd_pack" or lesson_cfg.get("sd_asset_pack_enabled") is True

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
        batch = {
            "assignmentId": self.assignment_id,
            "lessonId": self.lesson_id,
            "lessonVersion": self.lesson_version,
            "sessionId": self.session_id,
            "events": [clean],
        }
        batch.update(self._trace_context)
        self.forwarder.enqueue(batch)

    def _log(self, level: str, message: str) -> None:
        if self.logger is None:
            return
        try:
            getattr(self.logger.bind(tag=TAG), level)(self._with_log_context(message))
        except Exception:
            pass

    def _with_log_context(self, message: str) -> str:
        fields = []
        if self.assignment_id and "assignment_id=" not in message:
            fields.append(f"assignment_id={self.assignment_id}")
        if self.session_id and "session_id=" not in message:
            fields.append(f"session_id={self.session_id}")
        return f"{message} {' '.join(fields)}" if fields else message


async def maybe_start_lesson_on_connect(conn: Any) -> Optional[LessonRuntime]:
    """Serialize concurrent lesson pulls (connect-time pull + spoken start_lesson) so
    they cannot create two runtimes / emit duplicate lesson_prepare (deep-audit). The
    per-connection lock is lazily created; the lazy-init is atomic under asyncio (no
    await between the getattr and the assignment), so two schedulers racing here both
    end up using the same lock, then run the impl serially — the loser re-reads
    conn.lesson_runtime and returns the winner's session instead of duplicating it."""
    lock = getattr(conn, "_lesson_pull_lock", None)
    if lock is None:
        lock = asyncio.Lock()
        conn._lesson_pull_lock = lock
    async with lock:
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
    lesson_cfg = config.get("lesson", {}) or {}
    server_cfg = config.get("server", {}) or {}
    base_url = lesson_cfg.get("api_base") or server_cfg.get("api_url")
    device_id = getattr(conn, "device_id", None)
    logger = getattr(conn, "logger", None)
    _log_context: Dict[str, Any] = {}

    def _log(level: str, message: str) -> None:
        if logger is None:
            return
        fields = []
        assignment_for_log = _log_context.get("assignment")
        if isinstance(assignment_for_log, dict) and "assignment_id=" not in message:
            assignment_id_for_log = assignment_for_log.get("assignmentId")
            if isinstance(assignment_id_for_log, str) and assignment_id_for_log:
                fields.append(f"assignment_id={assignment_id_for_log}")
        session_id_for_log = getattr(conn, "session_id", None)
        if isinstance(session_id_for_log, str) and session_id_for_log and "session_id=" not in message:
            fields.append(f"session_id={session_id_for_log}")
        contextual_message = f"{message} {' '.join(fields)}" if fields else message
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

    token = lesson_cfg.get("device_token")  # D-RUNTOKEN: optional, ops/backend follow-up.

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
    if not _cap_ok(getattr(conn, "features", None)):
        _set_lesson_start_status(conn, "LESSON_CAPABILITY_MISSING", "Robot chưa sẵn sàng hiển thị bài học.")
        _log("info", "device lacks lesson capability; pull-on-connect no-op")
        return None

    # L3 P3 — the device's advertised renderer-capability set (v1-only for every
    # current firmware). Forwarded to the manifest fetch so the backend serves a
    # manifest this device can render. The runtime re-derives the same set from
    # conn.features for its start() gate; computing it here keeps the fetch honest.
    renderer_capabilities = _device_caps(getattr(conn, "features", None))

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(15.0),
        limits=httpx.Limits(max_keepalive_connections=0),
        follow_redirects=True,
    ) as client:
        # D-RUNTOKEN bridge: resolve the robot's Wi-Fi MAC -> backend device UUID
        # + a short-lived device-scoped JWT so the assignment pull authenticates as
        # the device. A mint failure is surfaced locally and the pull is skipped;
        # a legacy MAC/no-token request only creates a swallowed backend 401.
        backend_device_id = device_id
        try:
            from config.device_token_client import resolve_device_identity

            minted_uuid, minted_token = await resolve_device_identity(
                client, base_url, device_id, logger=logger
            )
            if minted_uuid and minted_token:
                backend_device_id = minted_uuid
                token = minted_token
            else:
                _set_lesson_start_status(
                    conn,
                    "DEVICE_TOKEN_UNAVAILABLE",
                    "Robot chưa xác thực được với máy chủ bài học.",
                    reason="missing_identity",
                )
                _log("warning", "device token required for lesson pull; skipping tokenless request")
                return None
        except Exception as exc:  # pragma: no cover - bridge is best-effort
            _set_lesson_start_status(
                conn,
                "DEVICE_TOKEN_UNAVAILABLE",
                "Robot chưa xác thực được với máy chủ bài học.",
                reason="missing_identity",
            )
            _log("warning", f"device-token mint unavailable: {type(exc).__name__}: {exc}")
            return None
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
            try:
                from core.lesson.forwarder import replay_stored_terminal_event

                replayed_terminal = await replay_stored_terminal_event(
                    device_id=backend_device_id,
                    assignment_id=assignment_id,
                    base_url=base_url,
                    token=token,
                    client=client,
                    logger=logger,
                )
            except Exception as exc:  # pragma: no cover - replay is best-effort
                _log("warning", f"stored terminal lesson event replay failed: {type(exc).__name__}")
                replayed_terminal = False
            if replayed_terminal:
                _log("info", "replayed pending terminal lesson event; skipping lesson restart")
                return None
        profile = assignment.get("profile", "espTft")
        try:
            manifest, etag = await backend_api.get_lesson_manifest(
                client,
                base_url,
                assignment.get("lessonId"),
                profile,
                token=token,
                renderer_capabilities=renderer_capabilities,
                lesson_version=assignment.get("lessonVersion"),
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
        f"deviceId={device_id} "
        f"backendDeviceId={backend_device_id} "
        f"childId={assignment.get('childId', '')} "
        f"stepCount={len(manifest.get('steps', []) or [])} "
        f"assetCount={len(manifest.get('assets', []) or [])} "
        f"storyBeat={_compact_json(_manifest_story_log_summary(manifest))}",
    )
    if existing is not None and getattr(existing, "assignment_id", None) == assignment.get("assignmentId"):
        unchanged = (
            existing.lesson_version == new_lesson_version
            and existing.assignment_version == new_assignment_version
            and getattr(existing, "manifest_checksum", "") == new_manifest_checksum
        )
        if unchanged:
            replay = getattr(existing, "replay_pending_terminal_event", None)
            if getattr(existing, "state", None) in (S_COMPLETED, S_FAILED) and callable(replay):
                try:
                    await replay()
                except Exception as exc:  # pragma: no cover - replay is best-effort
                    _log("warning", f"terminal lesson event replay failed: {type(exc).__name__}")
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
                f"v{new_lesson_version}/a{new_assignment_version}/m{new_manifest_checksum[:8]}; tearing down + re-pulling",
            )
            old_cache = getattr(existing, "asset_cache", None)
            try:
                # Republish EVICTS the old version's bytes (disjoint version-scoped dir),
                # unlike a plain reconnect close() which keeps verified bytes for
                # re-attest. evict() reuses aclose() for client teardown.
                if old_cache is not None and hasattr(old_cache, "evict"):
                    await old_cache.evict()
                await existing.close()  # forwarder + (idempotent) asset client teardown
            except Exception as exc:  # pragma: no cover - teardown is best-effort
                _log("warning", f"old lesson runtime teardown failed: {type(exc).__name__}")
            conn.lesson_runtime = None
            # fall through to a fresh pull of the republished version below.

    asset_cache = AssetCache(
        assets=[
            {
                "key": a.get("id") or a.get("assetId"),
                "path": a.get("path"),
                "url": a.get("url"),
                "sha256": a.get("sha256"),
                "critical": a.get("critical"),
                "layer": a.get("layer"),
                "role": a.get("role"),
                "mediaType": a.get("mediaType") or a.get("media_type"),
            }
            for a in manifest.get("assets", [])
        ],
        profile=profile,
        asset_origin_base=lesson_cfg.get("asset_origin_base"),
        public_base_url=lesson_asset_public_base_url(config),
        asset_pack_local_root=lesson_cfg.get("asset_pack_local_root"),
        asset_pack_mount_root=lesson_cfg.get("asset_pack_mount_root"),
        lesson_key=str(assignment.get("lessonId") or "lesson"),
        lesson_version=int(assignment.get("lessonVersion", 1)),
        manifest_checksum=new_manifest_checksum,
        preload_timeout_sec=float(lesson_cfg.get("preload_timeout_sec", 90)),
        concurrency=int(lesson_cfg.get("preload_concurrency", 2)),
        max_asset_bytes=int(lesson_cfg.get("max_asset_bytes", 8 * 1024 * 1024)),
        max_total_asset_bytes=int(lesson_cfg.get("max_total_asset_bytes", 64 * 1024 * 1024)),
        busy_check=getattr(conn, "is_realtime_busy", None),
        logger=logger,
    )
    forwarder = LessonEventForwarder(
        device_id=backend_device_id, base_url=base_url, token=token, logger=logger
    )

    async def _report_preload_status(report: Dict[str, Any]) -> None:
        timeout_sec = float(lesson_cfg.get("preload_status_timeout_sec", 5.0))
        retry_delay = float(lesson_cfg.get("preload_status_retry_delay_sec", 1.0))
        max_retries = int(lesson_cfg.get("preload_status_max_retries", 2))
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
    # Close any prior runtime for a DIFFERENT assignment before replacing it, so its
    # forwarder worker task + asset httpx client are not leaked (deep-audit #7). The
    # same-assignment republish path above already closed + nulled it (prior is None
    # there); this catches the new-assignment case that fell straight through.
    prior = getattr(conn, "lesson_runtime", None)
    if prior is not None and prior is not runtime:
        try:
            await prior.close()
        except Exception as exc:  # pragma: no cover - teardown is best-effort
            _log("warning", f"prior lesson runtime teardown failed: {type(exc).__name__}")
    conn.lesson_runtime = runtime
    try:
        enter_lesson = getattr(conn, "enter_lesson_mode", None)
        if callable(enter_lesson):
            await enter_lesson(reason="lesson_start")
        await runtime.start()
        if runtime.state == S_FAILED:
            _set_lesson_start_status(conn, "START_REFUSED", "Robot chưa hiển thị được bài học.")
            if getattr(conn, "lesson_runtime", None) is runtime:
                conn.lesson_runtime = None
            try:
                await runtime.close()
            except Exception as exc:  # pragma: no cover - teardown is best-effort
                _log("warning", f"failed lesson runtime teardown failed: {type(exc).__name__}")
            return None
        _set_lesson_start_status(conn, "STARTED")
    except LessonError as err:
        _set_lesson_start_status(conn, "START_REFUSED", "Robot chưa hiển thị được bài học.")
        _log("warning", f"lesson start refused: {err.code}")
        release_lesson = getattr(conn, "release_lesson_mode", None)
        if callable(release_lesson):
            await release_lesson(reason="lesson_start_refused")
        if getattr(conn, "lesson_runtime", None) is runtime:
            conn.lesson_runtime = None
        try:
            await runtime.close()
        except Exception as exc:  # pragma: no cover - teardown is best-effort
            _log("warning", f"refused lesson runtime teardown failed: {type(exc).__name__}")
        return None
    return runtime
