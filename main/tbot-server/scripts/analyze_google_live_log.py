"""Parse server.log to extract Google Live diagnostic metrics for PR1 baseline.

Reads a tbot-server log file, classifies events by session and speaking state,
and emits a JSON report + a human-readable markdown summary.

Usage:
    python scripts/analyze_google_live_log.py --log tmp/server.log \\
        --out-json tmp/baseline.json --out-md docs/qa/ad-hoc/<date>-baseline.md
"""
from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import sys
import tempfile
from collections.abc import Mapping
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from scripts.google_live_reliability import (
    GOOGLE_LIVE_LIMITS,
    SCHEMA_VERSION,
    redact_mapping,
    reliability_verdict,
)
from scripts.physical_smoke_audit import (
    FATAL_PATTERNS as PHYSICAL_FATAL_PATTERNS,
)
from scripts.physical_smoke_audit import (
    FATAL_REGEX_PATTERNS as PHYSICAL_FATAL_REGEX_PATTERNS,
)

TS_RE = re.compile(r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")

P_INPUT_DIAG = re.compile(
    r"input_audio_diag encoded_bytes=(?P<enc>\d+|unknown) "
    r"decoded_bytes=(?P<dec>\d+) rms=(?P<rms>\d+) "
    r"source_rate=(?P<src>\d+) target_rate=(?P<tgt>\d+)"
)
P_BARGEIN = re.compile(r"barge-in rms=(?P<rms>\d+) threshold=(?P<thr>\d+)")
P_INTERRUPT = re.compile(
    r"user_interrupted reason=(?P<reason>\w+) "
    r"cancelled_response_id=(?P<cancelled>\d+) "
    r"next_response_id=(?P<next>\d+)"
)
P_FIRST_AUDIO = re.compile(r"first_audio_out_latency_ms=(?P<ms>[\d.]+)")
P_AUDIO_END = re.compile(
    r"audio_end reason=(?P<reason>\w+) chunks=(?P<chunks>\d+) bytes=(?P<bytes>\d+)"
)
P_AUDIO_START = re.compile(r"Google Live audio_start$")
P_TRANSCRIPT = re.compile(r"transcript source=(?P<source>user|model) chars=(?P<chars>\d+)")
P_CONNECT_MS = re.compile(r"Google Live session connected in (?P<ms>[\d.]+) ms")
P_RECV_START = re.compile(r"Google Live receive loop started")
P_RECV_STOP = re.compile(r"Google Live receive loop stopped")
P_SESSION_DROP = re.compile(r"Audio send loop exception|received 1000 \(OK\)")
P_FALLBACK = re.compile(r"fallback_triggered reason=(?P<reason>\w+)")
P_FALLBACK_DISABLED = re.compile(r"fallback_disabled reason=(?P<reason>.+)$")
P_RECONNECT = re.compile(r"Google Live reconnect attempt (?P<n>\d+)")
P_SERVER_INT_IGNORED = re.compile(r"Google Live server interruption ignored by config")
P_CONN_OPEN = re.compile(r"core\.connection - (?P<ip>\S+) conn - Headers:")
P_GOAWAY = re.compile(r"goAway|go_away|sent 1011|received 1011|1008", re.I)
P_RECV_TIMEOUT = re.compile(r"Google Live receive timed out")
P_ECHO_SUPPRESSED = re.compile(
    r"Google Live echo_suppressed reason=(?P<reason>\w+) bytes=(?P<bytes>\d+) rms=(?P<rms>\d+|n/a)"
)
P_AEC_LIVE_VAD_FORWARD = re.compile(
    r"Google Live aec_live_vad_forward reason=(?P<reason>\w+) bytes=(?P<bytes>\d+) rms=(?P<rms>\d+|n/a)"
)
P_ECHO_BYPASS = re.compile(
    r"Google Live echo_bypass reason=(?P<reason>\w+) bytes=(?P<bytes>\d+) rms=(?P<rms>\d+|n/a)"
)
P_MUSIC_CONTROL = re.compile(
    r"Google Live music_control_intent tool=(?P<tool>\w+)"
)
P_STALE_MODEL_DROP = re.compile(
    r"Google Live stale_model_event_dropped type=(?P<type>\w+) "
    r"reason=(?P<reason>\w+)"
)
P_MODEL_OUTPUT_STILL_BLOCKED = re.compile(
    r"Google Live model_output_still_blocked_waiting_user_turn"
)
P_CLEAN_USER_TURN = re.compile(
    r"Google Live clean_user_turn_opened reason=(?P<reason>\w+)"
)
P_REPLAYED_INTERRUPT_AUDIO = re.compile(
    r"Google Live replayed_interrupt_audio reason=(?P<reason>\w+) "
    r"frames=(?P<frames>\d+) bytes=(?P<bytes>\d+) response_id=(?P<response_id>\d+)"
)
P_INTERRUPT_INPUT_FINALIZED = re.compile(
    r"Google Live interrupt_input_finalized reason=(?P<reason>\w+) "
    r"elapsed_ms=(?P<elapsed>[\d.]+) response_id=(?P<response_id>\d+) "
    r"frames=(?P<frames>\d+) bytes=(?P<bytes>\d+) peak_rms=(?P<peak_rms>\d+)"
)
P_LESSON_PROMPT_LOCAL_TTS = re.compile(
    r"Google Live lesson_\w+ queued via tts\b"
)
P_LESSON_PROMPT_LIVE_TEXT = re.compile(
    r"Google Live lesson_step_prompt sent via live text\b"
)
P_TTS_STOP_SENT = re.compile(r"tts_state_stop_sent|tts_stop_sent")
P_AUDIO_DECISION = re.compile(r"audio_decision decision=(?P<decision>\S+)")
P_INTERRUPT_STARTED = re.compile(r"interrupt_started reason=(?P<reason>\S+)")
P_OUTPUT_QUEUE_CLEARED = re.compile(r"output_queue_cleared reason=(?P<reason>\S+)")
P_RECONNECT_STARTED = re.compile(r"reconnect_started reason=(?P<reason>\S+)")
P_RECONNECT_SUCCEEDED = re.compile(r"reconnect_succeeded attempt=(?P<attempt>\d+)")
P_RECONNECT_FAILED = re.compile(r"reconnect_failed attempt=(?P<attempt>\d+)")
P_MUSIC_STATE_CHANGED = re.compile(r"music_state_changed state=(?P<state>\S+)")
P_AUDIO_OUTPUT_TRANSPORT_CLOSED = re.compile(
    r"audio_output_transport_closed reason=(?P<reason>\S+)"
)

# New markers — Phase 1.2 / 1.3 (google_live.py + audio_bridge.py)
P_USER_SPEECH_PENDING = re.compile(
    r"user_speech_pending_replay frames=(?P<frames>\d+) bytes=(?P<bytes>\d+)"
)
P_REPLAY_SKIPPED = re.compile(r"replay_skipped reason=(?P<reason>\S+)")
P_INTERRUPT_CAPTURE_FINALIZED = re.compile(
    r"interrupt_capture_finalized frames=(?P<frames>\d+) duration_ms=(?P<duration_ms>\d+)"
)
P_LIVE_TRANSCRIPT_RECV = re.compile(
    r"live_transcript_recv chars=(?P<chars>\d+) source=(?P<source>\S+)"
)
P_TOOL_CALL_DISPATCHED = re.compile(
    r"tool_call_dispatched name=(?P<name>\S+) response_id=(?P<response_id>\d+)"
)
P_MUSIC_AUTO_PAUSED = re.compile(r"music_auto_paused trigger=(?P<trigger>\S+)")
P_MODEL_OUTPUT_CHUNK_DROPPED = re.compile(
    r"model_output_chunk_dropped reason=(?P<reason>\S+) old=(?P<old>\d+) current=(?P<current>\d+)"
)
P_MODEL_OUTPUT_UNBLOCK_TRIGGER = re.compile(
    r"model_output_unblock_trigger source=(?P<source>\S+)"
)

P_RELIABILITY_WINDOW_START = re.compile(
    r"Google Live reliability_window_start window_id=(?P<window_id>[A-Za-z0-9._:-]+) "
    r"(?:journey_id=(?P<journey_id>[A-Za-z0-9._:-]+) )?"
    r"(?:journeys=(?P<journeys>[A-Za-z0-9._:,-]+) )?"
    r"candidate_identity=(?P<candidate_identity>\{.*\})$"
)
P_RELIABILITY_WINDOW_END = re.compile(
    r"Google Live reliability_window_end window_id=(?P<window_id>[A-Za-z0-9._:-]+)$"
)
P_RESPONSE_AUDIO_START = re.compile(
    r"Google Live model_audio_start_hold_input response_id=(?P<response_id>\d+)"
)
P_RESPONSE_AUDIO_END = re.compile(
    r"Google Live model_audio_end_ready_to_listen response_id=(?P<response_id>\d+)"
)
P_RESPONSE_AUDIO_FORWARDED = re.compile(
    r"Google Live model_output_chunk_forwarded response_id=(?P<response_id>\d+)\b"
)
P_EVIDENCE_RESPONSE_START = re.compile(
    r"Google Live evidence_response_started journey_id=(?P<journey_id>\S+) "
    r"connection_id=(?P<connection_id>\S+) live_connection_id=(?P<live_connection_id>\S+) "
    r"response_id=(?P<response_id>\d+)"
)
P_EVIDENCE_RESPONSE_END = re.compile(
    r"Google Live evidence_response_ended journey_id=(?P<journey_id>\S+) "
    r"connection_id=(?P<connection_id>\S+) live_connection_id=(?P<live_connection_id>\S+) "
    r"response_id=(?P<response_id>\d+)"
)
P_EVIDENCE_FORWARDED = re.compile(
    r"Google Live model_output_chunk_forwarded journey_id=(?P<journey_id>\S+) "
    r"connection_id=(?P<connection_id>\S+) live_connection_id=(?P<live_connection_id>\S+) "
    r"response_id=(?P<response_id>\d+)"
)
P_EVIDENCE_INTERRUPT_STARTED = re.compile(
    r"Google Live user_interrupt_started journey_id=(?P<journey_id>\S+) "
    r"connection_id=(?P<connection_id>\S+) live_connection_id=(?P<live_connection_id>\S+) "
    r"reason=(?P<reason>\S+) cancelled_response_id=(?P<cancelled>\d+) "
    r"next_response_id=(?P<next>\d+)"
)
P_EVIDENCE_INTERRUPT_STOPPED = re.compile(
    r"Google Live interrupt_output_stopped journey_id=(?P<journey_id>\S+) "
    r"connection_id=(?P<connection_id>\S+) live_connection_id=(?P<live_connection_id>\S+) "
    r"cancelled_response_id=(?P<cancelled>\d+) next_response_id=(?P<next>\d+)"
)
P_EVIDENCE_USER_INTERRUPTED = re.compile(
    r"Google Live evidence_user_interrupted journey_id=(?P<journey_id>\S+) "
    r"connection_id=(?P<connection_id>\S+) live_connection_id=(?P<live_connection_id>\S+) "
    r"reason=(?P<reason>\S+) cancelled_response_id=(?P<cancelled>\d+) "
    r"next_response_id=(?P<next>\d+)"
)
P_EVIDENCE_CONNECTION_CLOSE = re.compile(
    r"Google Live evidence_connection_close journey_id=(?P<journey_id>\S+) "
    r"connection_id=(?P<connection_id>\S+) live_connection_id=(?P<live_connection_id>\S+) "
    r"pending_tasks=(?P<pending_tasks>\d+)"
)
P_EVIDENCE_STALE_DROP = re.compile(
    r"Google Live evidence_stale_model_drop journey_id=(?P<journey_id>\S+) "
    r"connection_id=(?P<connection_id>\S+) live_connection_id=(?P<live_connection_id>\S+) "
    r"response_id=(?P<response_id>\d+) current_response_id=(?P<current_response_id>\d+)"
)
P_EVIDENCE_INTERRUPT_REPLAYED = re.compile(
    r"Google Live evidence_interrupt_audio_replayed journey_id=(?P<journey_id>\S+) "
    r"connection_id=(?P<connection_id>\S+) live_connection_id=(?P<live_connection_id>\S+) "
    r"response_id=(?P<response_id>\d+)"
)
P_EVIDENCE_INTERRUPT_FINALIZED = re.compile(
    r"Google Live evidence_interrupt_input_finalized journey_id=(?P<journey_id>\S+) "
    r"connection_id=(?P<connection_id>\S+) live_connection_id=(?P<live_connection_id>\S+) "
    r"response_id=(?P<response_id>\d+)"
)
P_CLIENT_DISCONNECTED = re.compile(
    r"Client disconnected\b.*\bclose_code=(?P<close_code>\d+)\b"
)
P_EVIDENCE_RECONNECT_STARTED = re.compile(
    r"Google Live evidence_reconnect_started journey_id=(?P<journey_id>\S+) "
    r"connection_id=(?P<connection_id>\S+) attempt=(?P<attempt>\d+) reason=(?P<reason>\S+)"
)
P_EVIDENCE_REOPEN_READY = re.compile(
    r"Google Live evidence_reopen_ready journey_id=(?P<journey_id>\S+) "
    r"connection_id=(?P<connection_id>\S+) attempt=(?P<attempt>\d+) "
    r"live_connection_id=(?P<live_connection_id>\S+)"
)
P_EVIDENCE_REPLAYED_BUFFERED = re.compile(
    r"Google Live evidence_replayed_buffered_audio journey_id=(?P<journey_id>\S+) "
    r"connection_id=(?P<connection_id>\S+) attempt=(?P<attempt>\d+) "
    r"live_connection_id=(?P<live_connection_id>\S+)"
)
P_EVIDENCE_RECONNECT_OUTCOME = re.compile(
    r"Google Live evidence_reconnect_(?P<outcome>succeeded|failed) "
    r"journey_id=(?P<journey_id>\S+) connection_id=(?P<connection_id>\S+) "
    r"attempt=(?P<attempt>\d+)(?: live_connection_id=(?P<live_connection_id>\S+)| error_class=(?P<error_class>\S+))"
)
_SCOPED_EVIDENCE_PATTERNS = (
    P_EVIDENCE_RESPONSE_START,
    P_EVIDENCE_RESPONSE_END,
    P_EVIDENCE_FORWARDED,
    P_EVIDENCE_INTERRUPT_STARTED,
    P_EVIDENCE_INTERRUPT_STOPPED,
    P_EVIDENCE_USER_INTERRUPTED,
    P_EVIDENCE_CONNECTION_CLOSE,
    P_EVIDENCE_STALE_DROP,
    P_EVIDENCE_INTERRUPT_REPLAYED,
    P_EVIDENCE_INTERRUPT_FINALIZED,
    P_EVIDENCE_RECONNECT_STARTED,
    P_EVIDENCE_REOPEN_READY,
    P_EVIDENCE_REPLAYED_BUFFERED,
    P_EVIDENCE_RECONNECT_OUTCOME,
)
P_STALE_MODEL_DROP_IDS = re.compile(
    r"Google Live stale_model_event_dropped type=(?P<type>\w+) reason=(?P<reason>\w+) "
    r"response_id=(?P<response_id>\d+) current_response_id=(?P<current_response_id>\d+)"
)
P_REPLAYED_BUFFERED_AUDIO = re.compile(
    r"Google Live replayed_buffered_audio frames=(?P<frames>\d+) bytes=(?P<bytes>\d+)"
)
P_WAITING_MODEL_TIMEOUT = re.compile(r"Google Live waiting_model_timeout\b")
P_TIMEOUT_TERMINAL = re.compile(
    r"Google Live waiting_model_timeout released_without_audio\b|"
    r"Google Live silent_session_reopen_suppressed\b|"
    r"Google Live lesson_step_failed\b"
)
P_NON_RETRIABLE_CLASSIFICATION = re.compile(
    r"Google Live classify_error kind=(?P<kind>\S+) retry=no"
)
P_RECONNECT_REASON = re.compile(
    r"reconnect_started reason=(?P<reason>\S+) attempt=(?P<attempt>\d+)"
)
P_SILENT_SESSION_REOPEN = re.compile(r"Google Live silent_session_reopen\b")
P_REOPEN_READY = re.compile(
    r"Google Live reopen_ready reason=(?P<reason>\S+) attempt=(?P<attempt>\d+) "
    r"live_connection_id=(?P<live_connection_id>\S+)"
)
P_HANDOFF_ACQUIRED = re.compile(r"lesson_start_handoff_(?:acquired|coalesced)\b")
P_HANDOFF_RELEASED = re.compile(r"lesson_start_handoff_released\b")
P_PENDING_TASK_CLOSE = re.compile(
    r"Google Live connection_close pending_tasks=(?P<tasks>\S+)"
)
P_LESSON_STEP_START = re.compile(r"Google Live lesson_step_started step_id=(?P<step_id>\S+)")
P_LESSON_STEP_PROGRESS = re.compile(
    r"Google Live lesson_(?:step_progress|conversation_progress) .*?step_id=(?P<step_id>\S+)"
)
P_LESSON_STEP_END = re.compile(r"Google Live lesson_step_ended step_id=(?P<step_id>\S+)")
P_FIRMWARE_LESSON_PING = re.compile(r"firmware_ping .*?lesson_step=(?P<step_id>\S+)")
P_SCOPED_LESSON_STEP_PROGRESS = re.compile(
    r"Google Live lesson_(?:step_progress|conversation_progress) "
    r"journey_id=(?P<journey_id>\S+) connection_id=(?P<connection_id>\S+) "
    r"live_connection_id=(?P<live_connection_id>\S+) step_id=(?P<step_id>\S+)"
)
P_SCOPED_FIRMWARE_LESSON_PING = re.compile(
    r"Google Live firmware_ping journey_id=(?P<journey_id>\S+) "
    r"connection_id=(?P<connection_id>\S+) "
    r"live_connection_id=(?P<live_connection_id>\S+) lesson_step=(?P<step_id>\S+)"
)
P_CLEAN_CONNECTION_CLOSE = re.compile(
    r"Client disconnected\b.*\bclose_code=(?:1000|1001)\b|Google Live clean_close\b"
)

_RELIABILITY_MARKERS = (
    P_RELIABILITY_WINDOW_START,
    P_RELIABILITY_WINDOW_END,
    P_RECV_START,
    P_RECV_STOP,
    P_RESPONSE_AUDIO_START,
    P_RESPONSE_AUDIO_END,
    P_RESPONSE_AUDIO_FORWARDED,
    P_EVIDENCE_RESPONSE_START,
    P_EVIDENCE_RESPONSE_END,
    P_EVIDENCE_FORWARDED,
    P_EVIDENCE_INTERRUPT_STARTED,
    P_EVIDENCE_INTERRUPT_STOPPED,
    P_EVIDENCE_USER_INTERRUPTED,
    P_EVIDENCE_CONNECTION_CLOSE,
    P_EVIDENCE_STALE_DROP,
    P_EVIDENCE_INTERRUPT_REPLAYED,
    P_EVIDENCE_INTERRUPT_FINALIZED,
    P_CLIENT_DISCONNECTED,
    P_EVIDENCE_RECONNECT_STARTED,
    P_EVIDENCE_REOPEN_READY,
    P_EVIDENCE_REPLAYED_BUFFERED,
    P_EVIDENCE_RECONNECT_OUTCOME,
    P_INTERRUPT,
    P_TTS_STOP_SENT,
    P_STALE_MODEL_DROP_IDS,
    P_REPLAYED_INTERRUPT_AUDIO,
    P_INTERRUPT_INPUT_FINALIZED,
    P_REPLAYED_BUFFERED_AUDIO,
    P_WAITING_MODEL_TIMEOUT,
    P_RECV_TIMEOUT,
    P_RECONNECT_STARTED,
    P_SILENT_SESSION_REOPEN,
    P_REOPEN_READY,
    P_RECONNECT_SUCCEEDED,
    P_RECONNECT_FAILED,
    P_FALLBACK,
    P_FALLBACK_DISABLED,
    P_NON_RETRIABLE_CLASSIFICATION,
    P_HANDOFF_ACQUIRED,
    P_HANDOFF_RELEASED,
    P_PENDING_TASK_CLOSE,
    P_LESSON_STEP_START,
    P_LESSON_STEP_PROGRESS,
    P_LESSON_STEP_END,
    P_FIRMWARE_LESSON_PING,
    P_CLEAN_CONNECTION_CLOSE,
)

_SCOPED_MARKER_FAMILIES = (
    (re.compile(r"Google Live evidence_"), _SCOPED_EVIDENCE_PATTERNS),
    (re.compile(r"Google Live user_interrupt_started\b"), (P_EVIDENCE_INTERRUPT_STARTED,)),
    (re.compile(r"Google Live interrupt_output_stopped\b"), (P_EVIDENCE_INTERRUPT_STOPPED,)),
    (
        re.compile(r"Google Live model_output_chunk_forwarded\s+journey"),
        (P_EVIDENCE_FORWARDED,),
    ),
    (
        re.compile(r"Google Live lesson_step_progress\s+journey"),
        (P_SCOPED_LESSON_STEP_PROGRESS,),
    ),
    (
        re.compile(r"Google Live lesson_conversation_progress\s+journey"),
        (P_SCOPED_LESSON_STEP_PROGRESS,),
    ),
    (
        re.compile(r"Google Live firmware_ping\s+journey"),
        (P_SCOPED_FIRMWARE_LESSON_PING,),
    ),
)

_STATEFULLY_ALLOWED_PHYSICAL_MARKERS = frozenset(
    {
        "Client disconnected",
        "Google Live receive timed out",
        "Google Live waiting_model_timeout",
        "Google Live reconnect attempt",
        "reconnect_started",
        "interrupt_started reason=loud_input",
        "Google Live user_interrupted reason=loud_input",
        "audio_decision decision=suppress_echo reason=robot_speaking",
        "audio_decision decision=hold_interrupt_audio reason=blocked_output",
        "Google Live echo_bypass",
        "Google Live echo_suppressed reason=robot_speaking",
    }
)
_FORBIDDEN_LOG_MARKERS = tuple(
    (pattern, re.compile(re.escape(pattern)))
    for pattern in PHYSICAL_FATAL_PATTERNS
    if pattern not in _STATEFULLY_ALLOWED_PHYSICAL_MARKERS
) + tuple(PHYSICAL_FATAL_REGEX_PATTERNS)

# ---------------------------------------------------------------------------
# Latency-span extraction for PR5 §5.1 --check-chain
# The ordered chain per plan §6.6:
#   echo_bypass → user_interrupted → tts_state_stop_sent →
#   replayed_interrupt_audio → interrupt_input_finalized →
#   transcript source=user
# ---------------------------------------------------------------------------
_CHAIN_MARKERS = [
    ("echo_bypass", P_ECHO_BYPASS),
    ("user_interrupted", P_INTERRUPT),
    ("tts_state_stop_sent", P_TTS_STOP_SENT),
    ("replayed_interrupt_audio", P_REPLAYED_INTERRUPT_AUDIO),
    ("interrupt_input_finalized", P_INTERRUPT_INPUT_FINALIZED),
    ("transcript_source_user", P_TRANSCRIPT),
]


@dataclass
class SessionState:
    open_at: Optional[datetime] = None
    close_at: Optional[datetime] = None
    speaking: bool = False  # True after audio_start, False after audio_end
    connect_ms: Optional[float] = None
    first_audio_ms: Optional[float] = None
    audio_chunks_total: int = 0
    audio_bytes_total: int = 0
    bargein_count: int = 0
    interrupt_count: int = 0
    server_interrupt_ignored_count: int = 0
    user_transcript_chars: int = 0
    model_transcript_chars: int = 0
    rms_while_speaking: list[int] = field(default_factory=list)
    rms_while_silent: list[int] = field(default_factory=list)
    bargein_rms_values: list[int] = field(default_factory=list)
    interrupt_reasons: list[str] = field(default_factory=list)
    drop_event: Optional[str] = None
    recv_timeout_count: int = 0
    reconnect_attempts: int = 0
    fallback_reason: Optional[str] = None
    fallback_disabled_reason: Optional[str] = None
    goaway_seen: bool = False
    echo_suppressed_count: int = 0
    echo_bypass_count: int = 0
    aec_live_vad_forward_count: int = 0
    echo_suppressed_rms: list[int] = field(default_factory=list)
    echo_bypass_rms: list[int] = field(default_factory=list)
    aec_live_vad_forward_rms: list[int] = field(default_factory=list)
    music_control_tools: list[str] = field(default_factory=list)
    stale_model_event_dropped_count: int = 0
    stale_model_event_types: list[str] = field(default_factory=list)
    model_output_still_blocked_count: int = 0
    clean_user_turn_opened_count: int = 0
    clean_user_turn_reasons: list[str] = field(default_factory=list)
    replayed_interrupt_audio_count: int = 0
    replayed_interrupt_frames: int = 0
    interrupt_input_finalized_count: int = 0
    interrupt_input_finalized_elapsed_ms: list[float] = field(default_factory=list)
    audio_decision_count: int = 0
    interrupt_started_count: int = 0
    output_queue_cleared_count: int = 0
    reconnect_started_count: int = 0
    reconnect_succeeded_count: int = 0
    reconnect_failed_count: int = 0
    lesson_prompt_local_tts_count: int = 0
    lesson_prompt_live_text_count: int = 0
    music_state_changed_count: int = 0
    audio_output_transport_closed_count: int = 0


def parse_timestamp(line: str) -> Optional[datetime]:
    match = TS_RE.match(line)
    if not match:
        return None
    try:
        return datetime.strptime(match.group("ts"), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def percentile(values: list[float], pct: float) -> Optional[float]:
    if not values:
        return None
    sorted_vals = sorted(values)
    k = max(0, min(len(sorted_vals) - 1, int(round((pct / 100) * (len(sorted_vals) - 1)))))
    return sorted_vals[k]


def summary_stats(values: list[float]) -> dict:
    if not values:
        return {"count": 0}
    return {
        "count": len(values),
        "min": min(values),
        "max": max(values),
        "mean": round(statistics.mean(values), 2),
        "median": round(statistics.median(values), 2),
        "p95": percentile(values, 95),
        "p99": percentile(values, 99),
    }


@dataclass
class LatencySpan:
    """Tracks ms elapsed between two consecutive chain markers (wall-clock seconds from log ts)."""
    from_marker: str = ""
    to_marker: str = ""
    elapsed_sec: Optional[float] = None


def check_chain(log_path: Path) -> dict:
    """Walk the log and find bargein success-chains; report missing markers and latency spans.

    Returns a dict with:
      chains: list of chain observations (one per echo_bypass event anchor)
      missing_marker_count: number of chains with at least one missing marker
      spans: per-span latency stats (median/p95)
    """
    marker_names = [name for name, _ in _CHAIN_MARKERS]
    chains = []
    current_chain: Optional[dict] = None

    with log_path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            ts = parse_timestamp(line)

            # A new chain starts when we see echo_bypass
            if _CHAIN_MARKERS[0][1].search(line):
                if current_chain is not None:
                    current_chain["complete"] = (
                        len(current_chain["found"]) == len(marker_names)
                    )
                    current_chain["missing"] = [
                        n for n in marker_names if n not in current_chain["found"]
                    ]
                    chains.append(current_chain)
                current_chain = {
                    "anchor_ts": ts.isoformat() if ts else None,
                    "found": {"echo_bypass": ts},
                    "timestamps": {"echo_bypass": ts},
                    "complete": False,
                    "missing": [],
                }
                continue

            if current_chain is None:
                continue

            for name, pattern in _CHAIN_MARKERS[1:]:
                if name in current_chain["found"]:
                    continue
                m = pattern.search(line)
                if m:
                    if name == "transcript_source_user":
                        # only count user-source transcripts
                        if hasattr(m, "group") and m.group("source") != "user":
                            continue
                    current_chain["found"][name] = ts
                    current_chain["timestamps"][name] = ts
                    break

    if current_chain is not None:
        current_chain["complete"] = (
            len(current_chain["found"]) == len(marker_names)
        )
        current_chain["missing"] = [
            n for n in marker_names if n not in current_chain["found"]
        ]
        chains.append(current_chain)

    # Compute per-span latency across all complete chains
    span_pairs = list(zip(marker_names[:-1], marker_names[1:]))
    span_samples: dict[str, list[float]] = {f"{a}->{b}": [] for a, b in span_pairs}
    for chain in chains:
        for a, b in span_pairs:
        # Check they are found and have timestamps with .timestamp() available
            ts_a = chain["timestamps"].get(a)
            ts_b = chain["timestamps"].get(b)
            if ts_a is not None and ts_b is not None:
                try:
                    elapsed = (ts_b - ts_a).total_seconds() * 1000
                    if elapsed >= 0:
                        span_samples[f"{a}->{b}"].append(elapsed)
                except (TypeError, AttributeError):
                    pass

    span_stats = {
        span: summary_stats(samples)
        for span, samples in span_samples.items()
    }

    missing_chains = [c for c in chains if c["missing"]]
    chain_records = [
        {
            "anchor_ts": c["anchor_ts"],
            "complete": c["complete"],
            "missing": c["missing"],
            "found_count": len(c["found"]),
        }
        for c in chains
    ]

    return {
        "total_chains": len(chains),
        "complete_chains": len(chains) - len(missing_chains),
        "incomplete_chains": len(missing_chains),
        "missing_marker_count": sum(len(c["missing"]) for c in missing_chains),
        "chain_records": chain_records,
        "span_latency_ms": span_stats,
    }


def analyze(log_path: Path) -> dict:
    sessions: list[SessionState] = []
    current: Optional[SessionState] = None
    total_lines = 0

    with log_path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            total_lines += 1
            ts = parse_timestamp(line)

            if P_RECV_START.search(line):
                if current is not None and current.close_at is None:
                    current.close_at = ts  # treat as soft close
                current = SessionState(open_at=ts)
                sessions.append(current)
                continue

            if current is None:
                # capture conn open as session marker even before recv loop
                if P_CONN_OPEN.search(line):
                    current = SessionState(open_at=ts)
                    sessions.append(current)
                else:
                    continue

            if P_RECV_STOP.search(line):
                current.close_at = ts
                continue

            m = P_CONNECT_MS.search(line)
            if m:
                current.connect_ms = float(m.group("ms"))
                continue

            m = P_FIRST_AUDIO.search(line)
            if m:
                current.first_audio_ms = float(m.group("ms"))
                continue

            if P_AUDIO_START.search(line):
                current.speaking = True
                continue

            m = P_AUDIO_END.search(line)
            if m:
                current.speaking = False
                current.audio_chunks_total += int(m.group("chunks"))
                current.audio_bytes_total += int(m.group("bytes"))
                continue

            m = P_INPUT_DIAG.search(line)
            if m:
                rms = int(m.group("rms"))
                if current.speaking:
                    current.rms_while_speaking.append(rms)
                else:
                    current.rms_while_silent.append(rms)
                continue

            m = P_BARGEIN.search(line)
            if m:
                current.bargein_count += 1
                current.bargein_rms_values.append(int(m.group("rms")))
                continue

            m = P_INTERRUPT.search(line)
            if m:
                current.interrupt_count += 1
                current.interrupt_reasons.append(m.group("reason"))
                continue

            if P_SERVER_INT_IGNORED.search(line):
                current.server_interrupt_ignored_count += 1
                continue

            m = P_TRANSCRIPT.search(line)
            if m:
                chars = int(m.group("chars"))
                if m.group("source") == "user":
                    current.user_transcript_chars += chars
                else:
                    current.model_transcript_chars += chars
                continue

            if P_SESSION_DROP.search(line):
                current.drop_event = line.strip()[-180:]
                continue

            if P_RECV_TIMEOUT.search(line):
                current.recv_timeout_count += 1
                continue

            m = P_RECONNECT.search(line)
            if m:
                current.reconnect_attempts = max(
                    current.reconnect_attempts, int(m.group("n"))
                )
                continue

            m = P_FALLBACK.search(line)
            if m:
                current.fallback_reason = m.group("reason")
                continue

            m = P_FALLBACK_DISABLED.search(line)
            if m:
                current.fallback_disabled_reason = m.group("reason").strip()
                continue

            if P_LESSON_PROMPT_LOCAL_TTS.search(line):
                current.lesson_prompt_local_tts_count += 1
                continue

            if P_LESSON_PROMPT_LIVE_TEXT.search(line):
                current.lesson_prompt_live_text_count += 1
                continue

            if P_GOAWAY.search(line):
                current.goaway_seen = True

            m = P_ECHO_SUPPRESSED.search(line)
            if m:
                current.echo_suppressed_count += 1
                if m.group("rms").isdigit():
                    current.echo_suppressed_rms.append(int(m.group("rms")))
                continue

            m = P_AEC_LIVE_VAD_FORWARD.search(line)
            if m:
                current.aec_live_vad_forward_count += 1
                if m.group("rms").isdigit():
                    current.aec_live_vad_forward_rms.append(int(m.group("rms")))
                continue

            m = P_ECHO_BYPASS.search(line)
            if m:
                current.echo_bypass_count += 1
                if m.group("rms").isdigit():
                    current.echo_bypass_rms.append(int(m.group("rms")))
                continue

            m = P_MUSIC_CONTROL.search(line)
            if m:
                current.music_control_tools.append(m.group("tool"))
                continue

            m = P_STALE_MODEL_DROP.search(line)
            if m:
                current.stale_model_event_dropped_count += 1
                current.stale_model_event_types.append(m.group("type"))
                continue

            if P_MODEL_OUTPUT_STILL_BLOCKED.search(line):
                current.model_output_still_blocked_count += 1
                continue

            m = P_CLEAN_USER_TURN.search(line)
            if m:
                current.clean_user_turn_opened_count += 1
                current.clean_user_turn_reasons.append(m.group("reason"))
                continue

            m = P_REPLAYED_INTERRUPT_AUDIO.search(line)
            if m:
                current.replayed_interrupt_audio_count += 1
                current.replayed_interrupt_frames += int(m.group("frames"))
                continue

            m = P_INTERRUPT_INPUT_FINALIZED.search(line)
            if m:
                current.interrupt_input_finalized_count += 1
                current.interrupt_input_finalized_elapsed_ms.append(
                    float(m.group("elapsed"))
                )
                continue

            if P_AUDIO_DECISION.search(line):
                current.audio_decision_count += 1
                continue

            if P_INTERRUPT_STARTED.search(line):
                current.interrupt_started_count += 1
                continue

            if P_OUTPUT_QUEUE_CLEARED.search(line):
                current.output_queue_cleared_count += 1
                continue

            if P_RECONNECT_STARTED.search(line):
                current.reconnect_started_count += 1
                continue

            if P_RECONNECT_SUCCEEDED.search(line):
                current.reconnect_succeeded_count += 1
                continue

            if P_RECONNECT_FAILED.search(line):
                current.reconnect_failed_count += 1
                continue

            if P_MUSIC_STATE_CHANGED.search(line):
                current.music_state_changed_count += 1
                continue

            if P_AUDIO_OUTPUT_TRANSPORT_CLOSED.search(line):
                current.audio_output_transport_closed_count += 1
                continue

    return build_report(sessions, total_lines, log_path)


def build_report(sessions: list[SessionState], total_lines: int, log_path: Path) -> dict:
    all_rms_speaking = [r for s in sessions for r in s.rms_while_speaking]
    all_rms_silent = [r for s in sessions for r in s.rms_while_silent]
    all_bargein_rms = [r for s in sessions for r in s.bargein_rms_values]
    all_echo_suppressed_rms = [r for s in sessions for r in s.echo_suppressed_rms]
    all_aec_live_vad_forward_rms = [
        r for s in sessions for r in s.aec_live_vad_forward_rms
    ]
    all_echo_bypass_rms = [r for s in sessions for r in s.echo_bypass_rms]
    all_connect_ms = [s.connect_ms for s in sessions if s.connect_ms is not None]
    all_first_audio_ms = [s.first_audio_ms for s in sessions if s.first_audio_ms is not None]
    all_interrupt_finalized_ms = [
        ms for s in sessions for ms in s.interrupt_input_finalized_elapsed_ms
    ]

    reason_counts = defaultdict(int)
    music_tool_counts = defaultdict(int)
    stale_model_event_type_counts = defaultdict(int)
    clean_user_turn_reason_counts = defaultdict(int)
    for s in sessions:
        for r in s.interrupt_reasons:
            reason_counts[r] += 1
        for tool in s.music_control_tools:
            music_tool_counts[tool] += 1
        for event_type in s.stale_model_event_types:
            stale_model_event_type_counts[event_type] += 1
        for reason in s.clean_user_turn_reasons:
            clean_user_turn_reason_counts[reason] += 1

    session_durations = []
    for s in sessions:
        if s.open_at and s.close_at and s.close_at >= s.open_at:
            session_durations.append((s.close_at - s.open_at).total_seconds())

    drop_count = sum(1 for s in sessions if s.drop_event)
    goaway_count = sum(1 for s in sessions if s.goaway_seen)
    fallback_count = sum(1 for s in sessions if s.fallback_reason)
    fallback_disabled_count = sum(1 for s in sessions if s.fallback_disabled_reason)
    reconnect_count = sum(1 for s in sessions if s.reconnect_attempts > 0)
    server_interrupt_ignored_total = sum(
        s.server_interrupt_ignored_count for s in sessions
    )

    report = {
        "source": str(log_path),
        "total_lines": total_lines,
        "session_count": len(sessions),
        "session_duration_sec": summary_stats(session_durations),
        "connect_ms": summary_stats(all_connect_ms),
        "first_audio_out_ms": summary_stats(all_first_audio_ms),
        "rms_while_model_speaking": summary_stats(all_rms_speaking),
        "rms_while_silent_or_user_turn": summary_stats(all_rms_silent),
        "barge_in_rms_at_trigger": summary_stats(all_bargein_rms),
        "totals": {
            "barge_in_fires": sum(s.bargein_count for s in sessions),
            "user_interrupts": sum(s.interrupt_count for s in sessions),
            "server_interrupts_ignored": server_interrupt_ignored_total,
            "audio_chunks": sum(s.audio_chunks_total for s in sessions),
            "audio_bytes": sum(s.audio_bytes_total for s in sessions),
            "recv_timeouts": sum(s.recv_timeout_count for s in sessions),
            "reconnect_attempts_sessions": reconnect_count,
            "fallback_triggered_sessions": fallback_count,
            "fallback_disabled_sessions": fallback_disabled_count,
            "abrupt_drop_sessions": drop_count,
            "goaway_sessions": goaway_count,
            "echo_suppressed": sum(s.echo_suppressed_count for s in sessions),
            "aec_live_vad_forward": sum(
                s.aec_live_vad_forward_count for s in sessions
            ),
            "echo_bypass": sum(s.echo_bypass_count for s in sessions),
            "stale_model_event_dropped": sum(
                s.stale_model_event_dropped_count for s in sessions
            ),
            "model_output_still_blocked_waiting_user_turn": sum(
                s.model_output_still_blocked_count for s in sessions
            ),
            "clean_user_turn_opened": sum(
                s.clean_user_turn_opened_count for s in sessions
            ),
            "replayed_interrupt_audio": sum(
                s.replayed_interrupt_audio_count for s in sessions
            ),
            "replayed_interrupt_frames": sum(
                s.replayed_interrupt_frames for s in sessions
            ),
            "interrupt_input_finalized": sum(
                s.interrupt_input_finalized_count for s in sessions
            ),
            "music_control_intents": sum(len(s.music_control_tools) for s in sessions),
            "audio_decision": sum(s.audio_decision_count for s in sessions),
            "interrupt_started": sum(s.interrupt_started_count for s in sessions),
            "output_queue_cleared": sum(s.output_queue_cleared_count for s in sessions),
            "reconnect_started": sum(s.reconnect_started_count for s in sessions),
            "reconnect_succeeded": sum(s.reconnect_succeeded_count for s in sessions),
            "reconnect_failed": sum(s.reconnect_failed_count for s in sessions),
            "music_state_changed": sum(s.music_state_changed_count for s in sessions),
            "audio_output_transport_closed": sum(
                s.audio_output_transport_closed_count for s in sessions
            ),
            "lesson_prompt_local_tts": sum(
                s.lesson_prompt_local_tts_count for s in sessions
            ),
            "lesson_prompt_live_text": sum(
                s.lesson_prompt_live_text_count for s in sessions
            ),
        },
        "interrupt_reason_distribution": dict(reason_counts),
        "music_control_tool_distribution": dict(music_tool_counts),
        "stale_model_event_type_distribution": dict(stale_model_event_type_counts),
        "clean_user_turn_reason_distribution": dict(clean_user_turn_reason_counts),
        "echo_suppressed_rms": summary_stats(all_echo_suppressed_rms),
        "aec_live_vad_forward_rms": summary_stats(all_aec_live_vad_forward_rms),
        "echo_bypass_rms": summary_stats(all_echo_bypass_rms),
        "interrupt_input_finalized_elapsed_ms": summary_stats(
            all_interrupt_finalized_ms
        ),
        "per_session": [
            {
                "open_at": s.open_at.isoformat() if s.open_at else None,
                "close_at": s.close_at.isoformat() if s.close_at else None,
                "connect_ms": s.connect_ms,
                "first_audio_ms": s.first_audio_ms,
                "audio_chunks": s.audio_chunks_total,
                "audio_bytes": s.audio_bytes_total,
                "rms_speaking_count": len(s.rms_while_speaking),
                "rms_silent_count": len(s.rms_while_silent),
                "rms_speaking_median": (
                    round(statistics.median(s.rms_while_speaking), 1)
                    if s.rms_while_speaking
                    else None
                ),
                "rms_silent_median": (
                    round(statistics.median(s.rms_while_silent), 1)
                    if s.rms_while_silent
                    else None
                ),
                "bargein_fires": s.bargein_count,
                "interrupt_fires": s.interrupt_count,
                "server_interrupt_ignored": s.server_interrupt_ignored_count,
                "drop_event": s.drop_event,
                "goaway_seen": s.goaway_seen,
                "recv_timeouts": s.recv_timeout_count,
                "fallback_reason": s.fallback_reason,
                "fallback_disabled_reason": s.fallback_disabled_reason,
                "reconnect_attempts": s.reconnect_attempts,
                "echo_suppressed": s.echo_suppressed_count,
                "aec_live_vad_forward": s.aec_live_vad_forward_count,
                "echo_bypass": s.echo_bypass_count,
                "stale_model_event_dropped": s.stale_model_event_dropped_count,
                "stale_model_event_types": list(s.stale_model_event_types),
                "model_output_still_blocked_waiting_user_turn": (
                    s.model_output_still_blocked_count
                ),
                "clean_user_turn_opened": s.clean_user_turn_opened_count,
                "clean_user_turn_reasons": list(s.clean_user_turn_reasons),
                "replayed_interrupt_audio": s.replayed_interrupt_audio_count,
                "replayed_interrupt_frames": s.replayed_interrupt_frames,
                "interrupt_input_finalized": s.interrupt_input_finalized_count,
                "interrupt_input_finalized_elapsed_ms": (
                    summary_stats(s.interrupt_input_finalized_elapsed_ms)
                ),
                "music_control_tools": list(s.music_control_tools),
                "audio_decision": s.audio_decision_count,
                "interrupt_started": s.interrupt_started_count,
                "output_queue_cleared": s.output_queue_cleared_count,
                "reconnect_started": s.reconnect_started_count,
                "reconnect_succeeded": s.reconnect_succeeded_count,
                "reconnect_failed": s.reconnect_failed_count,
                "music_state_changed": s.music_state_changed_count,
            }
            for s in sessions
        ],
    }

    # AEC necessity gate: if RMS while model speaking >= 1/4 of RMS during barge-in
    # trigger, echo is loud enough to corrupt detection. Conservative gate.
    speaking_median = report["rms_while_model_speaking"].get("median")
    bargein_median = report["barge_in_rms_at_trigger"].get("median")
    aec_gate = None
    if speaking_median is not None and bargein_median:
        ratio = speaking_median / bargein_median
        aec_gate = {
            "speaking_median_rms": speaking_median,
            "bargein_trigger_median_rms": bargein_median,
            "ratio_speaking_to_bargein": round(ratio, 3),
            "verdict": (
                "AEC_REQUIRED"
                if ratio > 0.25
                else "AEC_OPTIONAL"
            ),
            "rule": (
                "ratio > 0.25 means echo during model output is loud enough to be "
                "mistaken for barge-in or to mask real user speech"
            ),
        }
    report["aec_necessity_gate"] = aec_gate
    return report


def render_markdown(report: dict, log_path: Path) -> str:
    rms_speak = report["rms_while_model_speaking"]
    rms_silent = report["rms_while_silent_or_user_turn"]
    bargein = report["barge_in_rms_at_trigger"]
    echo_suppressed = report["echo_suppressed_rms"]
    aec_live_vad_forward = report["aec_live_vad_forward_rms"]
    echo_bypass = report["echo_bypass_rms"]
    totals = report["totals"]
    gate = report.get("aec_necessity_gate") or {}

    lines = [
        "# Google Live baseline — production log analysis (PR1)",
        "",
        f"**Source log:** `{log_path}`",
        f"**Total log lines parsed:** {report['total_lines']:,}",
        f"**Sessions detected:** {report['session_count']}",
        f"**Generated:** {datetime.now().isoformat(timespec='seconds')}",
        "",
        "## Verification matrix row",
        "",
        "| Task | AC | PASS / FAIL / PARTIAL | Evidence |",
        "|---|---|---|---|",
        (
            "| adhoc-2026-05-19-google-live-baseline | PR1.6 baseline file exists "
            f"| PASS | {len(report['per_session'])} sessions analysed |"
        ),
        (
            "| adhoc-2026-05-19-google-live-baseline | AEC necessity gate computed "
            f"| {'PASS' if gate else 'PARTIAL (no data)'} | "
            f"verdict={gate.get('verdict', 'unknown')} |"
        ),
        "",
        "## Session timing",
        "",
        f"- **connect_ms** — {report['connect_ms']}",
        f"- **first_audio_out_ms** — {report['first_audio_out_ms']}",
        f"- **session_duration_sec** — {report['session_duration_sec']}",
        "",
        "## RMS distributions (the key data for AEC decision)",
        "",
        "| Metric | count | min | median | p95 | max |",
        "|---|---:|---:|---:|---:|---:|",
        (
            f"| RMS while model speaking (echo proxy) "
            f"| {rms_speak.get('count', 0)} | {rms_speak.get('min', '-')} "
            f"| {rms_speak.get('median', '-')} | {rms_speak.get('p95', '-')} "
            f"| {rms_speak.get('max', '-')} |"
        ),
        (
            f"| RMS while silent / user-turn (noise floor + user speech) "
            f"| {rms_silent.get('count', 0)} | {rms_silent.get('min', '-')} "
            f"| {rms_silent.get('median', '-')} | {rms_silent.get('p95', '-')} "
            f"| {rms_silent.get('max', '-')} |"
        ),
        (
            f"| RMS at barge-in trigger "
            f"| {bargein.get('count', 0)} | {bargein.get('min', '-')} "
            f"| {bargein.get('median', '-')} | {bargein.get('p95', '-')} "
            f"| {bargein.get('max', '-')} |"
        ),
        "",
        "## Totals (signs of pain points)",
        "",
        f"- barge_in fires: **{totals['barge_in_fires']}**",
        f"- user_interrupts: **{totals['user_interrupts']}**",
        f"- server interrupts (suppressed by config): **{totals['server_interrupts_ignored']}**",
        f"- audio chunks delivered: {totals['audio_chunks']}",
        f"- audio bytes delivered: {totals['audio_bytes']:,}",
        f"- recv timeouts: **{totals['recv_timeouts']}**",
        f"- reconnect-attempted sessions: **{totals['reconnect_attempts_sessions']}**",
        f"- fallback-triggered sessions: **{totals['fallback_triggered_sessions']}**",
        f"- fallback-disabled sessions: **{totals['fallback_disabled_sessions']}**",
        f"- abrupt drop sessions (websocket 1000 mid-send): **{totals['abrupt_drop_sessions']}**",
        f"- goAway / 1008 / 1011 sessions: **{totals['goaway_sessions']}**",
        f"- echo_suppressed events: **{totals['echo_suppressed']}**",
        f"- aec_live_vad_forward events: **{totals['aec_live_vad_forward']}**",
        f"- echo_bypass events: **{totals['echo_bypass']}**",
        f"- stale model events dropped: **{totals['stale_model_event_dropped']}**",
        f"- model output still blocked waiting user turn: **{totals['model_output_still_blocked_waiting_user_turn']}**",
        f"- clean user turns opened: **{totals['clean_user_turn_opened']}**",
        f"- replayed interrupt audio batches: **{totals['replayed_interrupt_audio']}**",
        f"- replayed interrupt frames: **{totals['replayed_interrupt_frames']}**",
        f"- interrupt input finalized: **{totals['interrupt_input_finalized']}**",
        f"- music_control_intents: **{totals['music_control_intents']}**",
        f"- lesson prompt local TTS markers: **{totals['lesson_prompt_local_tts']}**",
        f"- lesson prompt Live text markers: **{totals['lesson_prompt_live_text']}**",
        "",
        f"- interrupt reason distribution: `{report['interrupt_reason_distribution']}`",
        f"- music control tool distribution: `{report['music_control_tool_distribution']}`",
        f"- stale model event type distribution: `{report['stale_model_event_type_distribution']}`",
        f"- clean user turn reason distribution: `{report['clean_user_turn_reason_distribution']}`",
        f"- echo_suppressed_rms: `{echo_suppressed}`",
        f"- aec_live_vad_forward_rms: `{aec_live_vad_forward}`",
        f"- echo_bypass_rms: `{echo_bypass}`",
        f"- interrupt_input_finalized_elapsed_ms: `{report['interrupt_input_finalized_elapsed_ms']}`",
        "",
        "## AEC necessity gate",
        "",
    ]
    if gate:
        lines.extend(
            [
                f"- median RMS while model speaking: **{gate['speaking_median_rms']}**",
                f"- median RMS at barge-in trigger: **{gate['bargein_trigger_median_rms']}**",
                f"- ratio (speaking / barge-in): **{gate['ratio_speaking_to_bargein']}**",
                f"- rule: {gate['rule']}",
                f"- **VERDICT: {gate['verdict']}**",
            ]
        )
    else:
        lines.append("- Insufficient data (no speaking RMS or no barge-in events)")
    lines.extend(
        [
            "",
            "## Decision implications for plan v2",
            "",
            "- If verdict = AEC_REQUIRED → proceed with PR3 (server-side AEC) before tuning",
            "  barge-in thresholds in PR4.",
            "- If verdict = AEC_OPTIONAL → AEC may be deferred; PR4 threshold tuning alone",
            "  could be enough. Re-confirm by repeating measurement in 3 different rooms.",
            "",
            "## Per-session snapshot",
            "",
            "| open_at | conn_ms | 1st_audio_ms | chunks | bytes | bargein | interrupts | drop | goaway |",
            "|---|---:|---:|---:|---:|---:|---:|---|:---:|",
        ]
    )
    for s in report["per_session"]:
        lines.append(
            "| {open} | {c} | {f} | {ch} | {b} | {bi} | {ii} | {drop} | {go} |".format(
                open=s["open_at"] or "-",
                c=s["connect_ms"] if s["connect_ms"] is not None else "-",
                f=s["first_audio_ms"] if s["first_audio_ms"] is not None else "-",
                ch=s["audio_chunks"],
                b=s["audio_bytes"],
                bi=s["bargein_fires"],
                ii=s["interrupt_fires"],
                drop="yes" if s["drop_event"] else "-",
                go="yes" if s["goaway_seen"] else "-",
            )
        )

    lines.extend(
        [
            "",
            "## Next steps (per plan v2)",
            "",
            "1. If AEC_REQUIRED: green-light PR3 (server-side AEC implementation).",
            "2. Phase 1 also requires fresh capture with controlled scenarios (silence",
            "   only / robot-only / close-mic user) on the physical robot — this log",
            "   analysis is a strong starting point but does not isolate each condition.",
            "3. PR2 (stability) can start in parallel — disconnect evidence is already",
            "   sufficient (see abrupt-drop and recv_timeouts totals above).",
        ]
    )
    return "\n".join(lines) + "\n"


def summarize_pains(log_path: Path) -> dict:
    """Scan log for Phase 1.2/1.3 markers and return a per-pain summary dict."""
    interrupts_initiated = 0
    buffer_appends = 0
    replay_skipped_by_reason: dict[str, int] = defaultdict(int)
    capture_finalized_count = 0
    capture_finalized_zero_frames = 0
    transcripts_received = 0  # live_transcript_recv chars>0
    interrupt_to_tts_stop_ms: list[float] = []
    stale_chunks_dropped = 0
    unblock_triggers: dict[str, int] = defaultdict(int)
    tool_dispatch_count = 0
    tool_dispatch_by_name: dict[str, int] = defaultdict(int)
    music_pause_count = 0
    music_pause_by_trigger: dict[str, int] = defaultdict(int)

    # P2 latency: track last user_interrupted timestamp, then look for tts_state_stop_sent
    _last_interrupt_ts: Optional[datetime] = None

    with log_path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            ts = parse_timestamp(line)

            if P_INTERRUPT.search(line):
                interrupts_initiated += 1
                _last_interrupt_ts = ts
                continue

            if P_TTS_STOP_SENT.search(line):
                if _last_interrupt_ts is not None and ts is not None:
                    elapsed = (ts - _last_interrupt_ts).total_seconds() * 1000
                    if elapsed >= 0:
                        interrupt_to_tts_stop_ms.append(elapsed)
                    _last_interrupt_ts = None
                continue

            m = P_USER_SPEECH_PENDING.search(line)
            if m:
                buffer_appends += 1
                continue

            m = P_REPLAY_SKIPPED.search(line)
            if m:
                replay_skipped_by_reason[m.group("reason")] += 1
                continue

            m = P_INTERRUPT_CAPTURE_FINALIZED.search(line)
            if m:
                capture_finalized_count += 1
                if int(m.group("frames")) == 0:
                    capture_finalized_zero_frames += 1
                continue

            m = P_LIVE_TRANSCRIPT_RECV.search(line)
            if m:
                if int(m.group("chars")) > 0:
                    transcripts_received += 1
                continue

            m = P_MODEL_OUTPUT_CHUNK_DROPPED.search(line)
            if m:
                stale_chunks_dropped += 1
                continue

            m = P_MODEL_OUTPUT_UNBLOCK_TRIGGER.search(line)
            if m:
                unblock_triggers[m.group("source")] += 1
                continue

            m = P_TOOL_CALL_DISPATCHED.search(line)
            if m:
                tool_dispatch_count += 1
                tool_dispatch_by_name[m.group("name")] += 1
                continue

            m = P_MUSIC_AUTO_PAUSED.search(line)
            if m:
                music_pause_count += 1
                music_pause_by_trigger[m.group("trigger")] += 1
                continue

    tts_stop_stats = summary_stats(interrupt_to_tts_stop_ms)
    transcript_loss_rate: Optional[float] = None
    if interrupts_initiated > 0:
        lost = interrupts_initiated - transcripts_received
        transcript_loss_rate = round(max(0, lost) / interrupts_initiated, 4)

    return {
        "P1_user_speech_lost": {
            "interrupts_initiated": interrupts_initiated,
            "buffer_appends": buffer_appends,
            "replay_skipped_by_reason": dict(replay_skipped_by_reason),
            "capture_finalized_count": capture_finalized_count,
            "capture_finalized_with_zero_frames": capture_finalized_zero_frames,
            "transcripts_received": transcripts_received,
            "transcript_loss_rate": transcript_loss_rate,
        },
        "P2_stop_latency": {
            "interrupt_to_tts_stop_sent_ms": tts_stop_stats,
        },
        "P3_response_overlap": {
            "stale_chunks_dropped": stale_chunks_dropped,
            "model_output_unblock_triggers": dict(unblock_triggers),
        },
        "P4_function_calls": {
            "tool_call_dispatched_count": tool_dispatch_count,
            "by_name": dict(tool_dispatch_by_name),
        },
        "P5_music_ducking": {
            "music_auto_pause_count": music_pause_count,
            "by_trigger": dict(music_pause_by_trigger),
        },
    }


def _failure(code: str, line: int, detail: str) -> dict[str, Any]:
    return {"code": code, "line": line, "detail": detail}


def _candidate_identity_valid(identity: Any) -> bool:
    if not isinstance(identity, dict):
        return False
    required = {
        "gitSha",
        "imageDigest",
        "firmwareIdentity",
        "fixtureSha256",
        "configFingerprint",
    }
    if set(identity) != required:
        return False
    if any(not isinstance(identity[key], str) or not identity[key] for key in required):
        return False
    return bool(
        re.fullmatch(r"sha256:[0-9a-fA-F]{64}", identity["imageDigest"])
        and re.fullmatch(r"[0-9a-fA-F]{64}", identity["fixtureSha256"])
        and re.fullmatch(r"sha256:[0-9a-fA-F]{64}", identity["configFingerprint"])
    )


def _is_reliability_line(line: str) -> bool:
    return (
        "Google Live reliability_window_" in line
        or any(pattern.search(line) for pattern in _RELIABILITY_MARKERS)
        or any(pattern.search(line) for _label, pattern in _FORBIDDEN_LOG_MARKERS)
    )


def analyze_reliability_window(log_path: Path) -> dict[str, Any]:
    """Verify one explicitly anchored Google Live evidence window in linear time."""
    failures: list[dict[str, Any]] = []
    fatal_hits: list[str] = []
    start_anchor: dict[str, Any] | None = None
    end_anchor: dict[str, Any] | None = None
    active = False
    previous_ts: datetime | None = None
    receive_loops_active = 0
    max_receive_loops_active = 0
    response_starts: dict[Any, int] = defaultdict(int)
    replay_counts_by_reopen: dict[str, int] = {}
    current_reopen: str | None = None
    current_reopen_ready = False
    pending_timeouts: list[dict[str, int]] = []
    non_retriable_error_line: int | None = None
    handoff_balance = 0
    handoff_lines: list[int] = []
    stale_audio_after_replacement = 0
    interrupt_records: list[dict[str, Any]] = []
    active_lesson_step: dict[str, Any] | None = None
    observed_marker_families: dict[str, set[str]] = defaultdict(set)
    scoped_interrupts: list[dict[str, Any]] = []
    scoped_reconnects: dict[tuple[str, str, int], dict[str, Any]] = {}
    scoped_active_responses: dict[tuple[str, str], int] = {}

    with log_path.open("r", encoding="utf-8", errors="replace") as fh:
        for line_number, raw_line in enumerate(fh, 1):
            line = raw_line.rstrip("\n")
            start_match = P_RELIABILITY_WINDOW_START.search(line)
            end_match = P_RELIABILITY_WINDOW_END.search(line)

            if start_match:
                if start_anchor is not None:
                    failures.append(
                        _failure(
                            "DUPLICATE_WINDOW_START",
                            line_number,
                            "bounded window has more than one start anchor",
                        )
                    )
                    continue
                ts = parse_timestamp(line)
                if ts is None:
                    failures.append(
                        _failure(
                            "MALFORMED_WINDOW_START",
                            line_number,
                            "start anchor timestamp is invalid",
                        )
                    )
                    continue
                try:
                    candidate_identity = json.loads(start_match.group("candidate_identity"))
                except json.JSONDecodeError:
                    candidate_identity = None
                if not _candidate_identity_valid(candidate_identity):
                    failures.append(
                        _failure(
                            "MALFORMED_CANDIDATE_IDENTITY",
                            line_number,
                            "start anchor candidate identity is invalid",
                        )
                    )
                start_anchor = {
                    "windowId": start_match.group("window_id"),
                    "timestamp": ts,
                    "line": line_number,
                    "candidateIdentity": candidate_identity,
                    "journeyId": start_match.group("journey_id"),
                    "claimedJourneys": set(
                        filter(None, (start_match.group("journeys") or "").split(","))
                    ),
                }
                active = True
                previous_ts = ts
                continue

            if end_match:
                if start_anchor is None:
                    failures.append(
                        _failure(
                            "WINDOW_START_MISSING",
                            line_number,
                            "end anchor appeared before a start anchor",
                        )
                    )
                    continue
                if end_anchor is not None:
                    failures.append(
                        _failure(
                            "DUPLICATE_WINDOW_END",
                            line_number,
                            "bounded window has more than one end anchor",
                        )
                    )
                    continue
                ts = parse_timestamp(line)
                if ts is None:
                    failures.append(
                        _failure(
                            "MALFORMED_WINDOW_END",
                            line_number,
                            "end anchor timestamp is invalid",
                        )
                    )
                    continue
                if end_match.group("window_id") != start_anchor["windowId"]:
                    failures.append(
                        _failure(
                            "WINDOW_ID_MISMATCH",
                            line_number,
                            "start and end anchors identify different windows",
                        )
                    )
                if previous_ts is not None and ts < previous_ts:
                    failures.append(
                        _failure(
                            "LOG_TIMESTAMP_REGRESSION",
                            line_number,
                            "end anchor precedes an earlier in-window event",
                        )
                    )
                end_anchor = {
                    "windowId": end_match.group("window_id"),
                    "timestamp": ts,
                    "line": line_number,
                }
                active = False
                continue

            if not active:
                if _is_reliability_line(line):
                    failures.append(
                        _failure(
                            "OUT_OF_WINDOW_RELIABILITY_MARKER",
                            line_number,
                            "reliability marker is outside the explicit anchors",
                        )
                    )
                continue
            ts = parse_timestamp(line)
            if ts is None:
                if _is_reliability_line(line):
                    failures.append(
                        _failure(
                            "MALFORMED_RELIABILITY_LOG_LINE",
                            line_number,
                            "reliability marker has no valid timestamp",
                        )
                    )
                continue
            if previous_ts is not None and ts < previous_ts:
                failures.append(
                    _failure(
                        "LOG_TIMESTAMP_REGRESSION",
                        line_number,
                        "in-window timestamps are not monotonic",
                    )
                )
            previous_ts = ts

            for label, pattern in _FORBIDDEN_LOG_MARKERS:
                if pattern.search(line):
                    if label not in fatal_hits:
                        fatal_hits.append(label)
                    failures.append(
                        _failure("FORBIDDEN_LOG_MARKER", line_number, label)
                    )

            disconnected = P_CLIENT_DISCONNECTED.search(line)
            if disconnected and disconnected.group("close_code") not in {"1000", "1001"}:
                failures.append(
                    _failure(
                        "ABNORMAL_CONNECTION_CLOSE",
                        line_number,
                        f"unexpected close code {disconnected.group('close_code')}",
                    )
                )

            scoped_start = P_EVIDENCE_RESPONSE_START.search(line)
            if scoped_start:
                observed_marker_families[scoped_start.group("journey_id")].add(
                    "response_started"
                )
                response_key = (
                    scoped_start.group("connection_id"),
                    scoped_start.group("live_connection_id"),
                    int(scoped_start.group("response_id")),
                )
                response_starts[response_key] += 1
                response_scope = response_key[:2]
                active_response_id = scoped_active_responses.get(response_scope)
                if active_response_id is not None:
                    failures.append(
                        _failure(
                            "RESPONSE_OVERLAP"
                            if active_response_id != response_key[2]
                            else "DUPLICATE_RESPONSE_ID",
                            line_number,
                            str(response_key),
                        )
                    )
                else:
                    scoped_active_responses[response_scope] = response_key[2]
                for record in scoped_interrupts:
                    if (
                        record["journeyId"] == scoped_start.group("journey_id")
                        and record["connectionId"] == scoped_start.group("connection_id")
                        and record["liveConnectionId"] == scoped_start.group("live_connection_id")
                        and record["replacementResponseId"] == int(scoped_start.group("response_id"))
                    ):
                        record["orderInvalid"] |= record["phase"] != 5
                        record["phase"] = 6
                continue
            scoped_end = P_EVIDENCE_RESPONSE_END.search(line)
            if scoped_end:
                observed_marker_families[scoped_end.group("journey_id")].add(
                    "response_ended"
                )
                response_scope = (
                    scoped_end.group("connection_id"),
                    scoped_end.group("live_connection_id"),
                )
                response_id = int(scoped_end.group("response_id"))
                if scoped_active_responses.get(response_scope) != response_id:
                    failures.append(
                        _failure(
                            "RESPONSE_END_WITHOUT_START",
                            line_number,
                            str((*response_scope, response_id)),
                        )
                    )
                else:
                    scoped_active_responses.pop(response_scope, None)
                for record in scoped_interrupts:
                    if (
                        record["journeyId"] == scoped_end.group("journey_id")
                        and record["connectionId"] == scoped_end.group("connection_id")
                        and record["liveConnectionId"] == scoped_end.group("live_connection_id")
                        and record["replacementResponseId"] == int(scoped_end.group("response_id"))
                    ):
                        record["orderInvalid"] |= record["phase"] != 7
                        record["phase"] = 8
                continue
            scoped_forwarded = P_EVIDENCE_FORWARDED.search(line)
            if scoped_forwarded:
                observed_marker_families[scoped_forwarded.group("journey_id")].add(
                    "forwarded"
                )
                forwarded_response_id = int(scoped_forwarded.group("response_id"))
                response_scope = (
                    scoped_forwarded.group("connection_id"),
                    scoped_forwarded.group("live_connection_id"),
                )
                if scoped_active_responses.get(response_scope) != forwarded_response_id:
                    failures.append(
                        _failure(
                            "RESPONSE_CHUNK_WITHOUT_ACTIVE_RESPONSE",
                            line_number,
                            str((*response_scope, forwarded_response_id)),
                        )
                    )
                for record in scoped_interrupts:
                    if (
                        record["journeyId"] == scoped_forwarded.group("journey_id")
                        and record["connectionId"] == scoped_forwarded.group("connection_id")
                        and record["liveConnectionId"] == scoped_forwarded.group("live_connection_id")
                        and record["phase"] >= 6
                        and forwarded_response_id == record["cancelledResponseId"]
                    ):
                        stale_audio_after_replacement += 1
                        failures.append(
                            _failure(
                                "STALE_AUDIO_AFTER_REPLACEMENT",
                                line_number,
                                "non-replacement response emitted after replacement start",
                            )
                        )
                for record in scoped_interrupts:
                    if (
                        record["journeyId"] == scoped_forwarded.group("journey_id")
                        and record["connectionId"] == scoped_forwarded.group("connection_id")
                        and record["liveConnectionId"] == scoped_forwarded.group("live_connection_id")
                        and record["replacementResponseId"] == forwarded_response_id
                    ):
                        record["orderInvalid"] |= record["phase"] != 6
                        record["phase"] = 7
                continue
            scoped_interrupt_start = P_EVIDENCE_INTERRUPT_STARTED.search(line)
            if scoped_interrupt_start:
                observed_marker_families[
                    scoped_interrupt_start.group("journey_id")
                ].add("interrupt_started")
                scoped_interrupts.append(
                    {
                        "journeyId": scoped_interrupt_start.group("journey_id"),
                        "connectionId": scoped_interrupt_start.group("connection_id"),
                        "liveConnectionId": scoped_interrupt_start.group("live_connection_id"),
                        "cancelledResponseId": int(scoped_interrupt_start.group("cancelled")),
                        "replacementResponseId": int(scoped_interrupt_start.group("next")),
                        "phase": 0,
                        "orderInvalid": False,
                    }
                )
                response_scope = (
                    scoped_interrupt_start.group("connection_id"),
                    scoped_interrupt_start.group("live_connection_id"),
                )
                cancelled_response_id = int(
                    scoped_interrupt_start.group("cancelled")
                )
                active_response_id = scoped_active_responses.get(response_scope)
                if active_response_id == cancelled_response_id:
                    scoped_active_responses.pop(response_scope, None)
                elif active_response_id is None:
                    failures.append(
                        _failure(
                            "INTERRUPT_WITHOUT_ACTIVE_RESPONSE",
                            line_number,
                            str((*response_scope, cancelled_response_id)),
                        )
                    )
                else:
                    failures.append(
                        _failure(
                            "INTERRUPT_RESPONSE_OWNERSHIP_MISMATCH",
                            line_number,
                            str((*response_scope, active_response_id)),
                        )
                    )
                continue
            scoped_interrupt_stop = P_EVIDENCE_INTERRUPT_STOPPED.search(line)
            if scoped_interrupt_stop:
                observed_marker_families[
                    scoped_interrupt_stop.group("journey_id")
                ].add("interrupt_stopped")
                for record in scoped_interrupts:
                    if (
                        record["journeyId"] == scoped_interrupt_stop.group("journey_id")
                        and record["connectionId"] == scoped_interrupt_stop.group("connection_id")
                        and record["liveConnectionId"] == scoped_interrupt_stop.group("live_connection_id")
                        and record["cancelledResponseId"] == int(scoped_interrupt_stop.group("cancelled"))
                        and record["replacementResponseId"] == int(scoped_interrupt_stop.group("next"))
                    ):
                        record["orderInvalid"] |= record["phase"] != 0
                        record["phase"] = 1
                continue
            scoped_interrupted = P_EVIDENCE_USER_INTERRUPTED.search(line)
            if scoped_interrupted:
                observed_marker_families[scoped_interrupted.group("journey_id")].add(
                    "interrupt_finalized"
                )
                for record in scoped_interrupts:
                    if (
                        record["journeyId"] == scoped_interrupted.group("journey_id")
                        and record["connectionId"] == scoped_interrupted.group("connection_id")
                        and record["liveConnectionId"] == scoped_interrupted.group("live_connection_id")
                        and record["cancelledResponseId"] == int(scoped_interrupted.group("cancelled"))
                        and record["replacementResponseId"] == int(scoped_interrupted.group("next"))
                    ):
                        record["orderInvalid"] |= record["phase"] != 1
                        record["phase"] = 2
                continue
            scoped_close = P_EVIDENCE_CONNECTION_CLOSE.search(line)
            if scoped_close:
                observed_marker_families[scoped_close.group("journey_id")].add(
                    "cleanup"
                )
                if int(scoped_close.group("pending_tasks")) != 0:
                    failures.append(
                        _failure(
                            "PENDING_TASK_AT_CLOSE",
                            line_number,
                            "scoped connection closed with provider-owned work pending",
                        )
                    )
                continue
            scoped_reconnect_start = P_EVIDENCE_RECONNECT_STARTED.search(line)
            if scoped_reconnect_start:
                journey_id = scoped_reconnect_start.group("journey_id")
                key = (
                    journey_id,
                    scoped_reconnect_start.group("connection_id"),
                    int(scoped_reconnect_start.group("attempt")),
                )
                if key in scoped_reconnects:
                    failures.append(
                        _failure("DUPLICATE_RECONNECT_ATTEMPT", line_number, str(key))
                    )
                scoped_reconnects[key] = {
                    "ready": False,
                    "replayed": False,
                    "terminalCount": 0,
                }
                observed_marker_families[journey_id].add("reconnect_started")
                continue
            scoped_reopen_ready = P_EVIDENCE_REOPEN_READY.search(line)
            if scoped_reopen_ready:
                journey_id = scoped_reopen_ready.group("journey_id")
                key = (
                    journey_id,
                    scoped_reopen_ready.group("connection_id"),
                    int(scoped_reopen_ready.group("attempt")),
                )
                state = scoped_reconnects.get(key)
                if state is None:
                    failures.append(
                        _failure("REOPEN_READY_WITHOUT_ATTEMPT", line_number, str(key))
                    )
                elif state.get("terminalCount"):
                    failures.append(
                        _failure("RECONNECT_MARKER_AFTER_TERMINAL", line_number, str(key))
                    )
                else:
                    state["ready"] = True
                    state["liveConnectionId"] = scoped_reopen_ready.group(
                        "live_connection_id"
                    )
                observed_marker_families[journey_id].add("reopen_ready")
                continue
            scoped_buffer_replay = P_EVIDENCE_REPLAYED_BUFFERED.search(line)
            if scoped_buffer_replay:
                journey_id = scoped_buffer_replay.group("journey_id")
                key = (
                    journey_id,
                    scoped_buffer_replay.group("connection_id"),
                    int(scoped_buffer_replay.group("attempt")),
                )
                state = scoped_reconnects.get(key)
                if (
                    state is None
                    or not state.get("ready")
                    or state.get("liveConnectionId")
                    != scoped_buffer_replay.group("live_connection_id")
                ):
                    failures.append(
                        _failure(
                            "BUFFER_REPLAY_WITHOUT_SUCCESSFUL_REOPEN",
                            line_number,
                            str(key),
                        )
                    )
                elif state.get("terminalCount"):
                    failures.append(
                        _failure("RECONNECT_MARKER_AFTER_TERMINAL", line_number, str(key))
                    )
                else:
                    state["replayed"] = True
                observed_marker_families[journey_id].add("reconnect_replay")
                continue
            scoped_reconnect_outcome = P_EVIDENCE_RECONNECT_OUTCOME.search(line)
            if scoped_reconnect_outcome:
                journey_id = scoped_reconnect_outcome.group("journey_id")
                key = (
                    journey_id,
                    scoped_reconnect_outcome.group("connection_id"),
                    int(scoped_reconnect_outcome.group("attempt")),
                )
                state = scoped_reconnects.get(key)
                if state is None:
                    failures.append(
                        _failure("RECONNECT_OUTCOME_WITHOUT_ATTEMPT", line_number, str(key))
                    )
                elif scoped_reconnect_outcome.group("outcome") == "succeeded" and (
                    not state.get("ready")
                    or state.get("liveConnectionId")
                    != scoped_reconnect_outcome.group("live_connection_id")
                ):
                    failures.append(
                        _failure("RECONNECT_SUCCESS_WITHOUT_READY", line_number, str(key))
                    )
                if state is not None:
                    state["terminalCount"] += 1
                    if state["terminalCount"] > 1:
                        failures.append(
                            _failure(
                                "DUPLICATE_RECONNECT_OUTCOME",
                                line_number,
                                str(key),
                            )
                        )
                    if (
                        scoped_reconnect_outcome.group("outcome") == "failed"
                        and state.get("replayed")
                    ):
                        failures.append(
                            _failure(
                                "REOPEN_FAILED_AFTER_BUFFER_REPLAY",
                                line_number,
                                str(key),
                            )
                        )
                observed_marker_families[journey_id].add("reconnect_outcome")
                continue
            scoped_stale = P_EVIDENCE_STALE_DROP.search(line)
            if scoped_stale:
                for record in scoped_interrupts:
                    if (
                        record["journeyId"] == scoped_stale.group("journey_id")
                        and record["connectionId"] == scoped_stale.group("connection_id")
                        and record["liveConnectionId"] == scoped_stale.group("live_connection_id")
                        and record["cancelledResponseId"] == int(scoped_stale.group("response_id"))
                        and record["replacementResponseId"] == int(scoped_stale.group("current_response_id"))
                    ):
                        record["orderInvalid"] |= record["phase"] != 2
                        record["phase"] = 3
                continue
            scoped_replay = P_EVIDENCE_INTERRUPT_REPLAYED.search(line)
            if scoped_replay:
                for record in scoped_interrupts:
                    if (
                        record["journeyId"] == scoped_replay.group("journey_id")
                        and record["connectionId"] == scoped_replay.group("connection_id")
                        and record["liveConnectionId"] == scoped_replay.group("live_connection_id")
                        and record["replacementResponseId"] == int(scoped_replay.group("response_id"))
                    ):
                        record["orderInvalid"] |= record["phase"] != 3
                        record["phase"] = 4
                continue
            scoped_finalized = P_EVIDENCE_INTERRUPT_FINALIZED.search(line)
            if scoped_finalized:
                for record in scoped_interrupts:
                    if (
                        record["journeyId"] == scoped_finalized.group("journey_id")
                        and record["connectionId"] == scoped_finalized.group("connection_id")
                        and record["liveConnectionId"] == scoped_finalized.group("live_connection_id")
                        and record["replacementResponseId"] == int(scoped_finalized.group("response_id"))
                    ):
                        record["orderInvalid"] |= record["phase"] != 4
                        record["phase"] = 5
                continue

            if P_RECV_START.search(line):
                receive_loops_active += 1
                max_receive_loops_active = max(
                    max_receive_loops_active, receive_loops_active
                )
                if receive_loops_active > 1:
                    failures.append(
                        _failure(
                            "RECEIVE_LOOP_OVERLAP",
                            line_number,
                            "more than one receive loop is active",
                        )
                    )
                continue
            if P_RECV_STOP.search(line):
                receive_loops_active -= 1
                if receive_loops_active < 0:
                    failures.append(
                        _failure(
                            "RECEIVE_LOOP_STOP_WITHOUT_START",
                            line_number,
                            "receive loop stop has no active owner",
                        )
                    )
                continue

            match = P_INTERRUPT.search(line)
            if match:
                if any(
                    record["cancelledResponseId"] == int(match.group("cancelled"))
                    and record["replacementResponseId"] == int(match.group("next"))
                    for record in scoped_interrupts
                ):
                    continue
                interrupt_records.append(
                    {
                        "line": line_number,
                        "cancelledResponseId": int(match.group("cancelled")),
                        "replacementResponseId": int(match.group("next")),
                        "oldResponseStopped": False,
                        "staleSuppressed": False,
                        "interruptAudioReplayed": False,
                        "interruptInputFinalized": False,
                        "replacementStarted": False,
                        "replacementStopped": False,
                        "phase": 0,
                        "orderInvalid": False,
                    }
                )
                continue

            start = P_RESPONSE_AUDIO_START.search(line)
            if start:
                response_id = int(start.group("response_id"))
                if not start_anchor.get("journeyId"):
                    response_starts[response_id] += 1
                for record in interrupt_records:
                    if response_id == record["replacementResponseId"]:
                        if record["phase"] != 4:
                            record["orderInvalid"] = True
                        record["replacementStarted"] = True
                        record["phase"] = max(record["phase"], 5)
                continue
            end = P_RESPONSE_AUDIO_END.search(line)
            if end:
                response_id = int(end.group("response_id"))
                for record in interrupt_records:
                    if response_id == record["replacementResponseId"]:
                        if record["phase"] != 5:
                            record["orderInvalid"] = True
                        record["replacementStopped"] = True
                        record["phase"] = max(record["phase"], 6)
                continue
            forwarded = P_RESPONSE_AUDIO_FORWARDED.search(line)
            if forwarded:
                response_id = int(forwarded.group("response_id"))
                for record in interrupt_records:
                    if (
                        record["replacementStarted"]
                        and response_id == record["cancelledResponseId"]
                    ):
                        stale_audio_after_replacement += 1
                        failures.append(
                            _failure(
                                "STALE_AUDIO_AFTER_REPLACEMENT",
                                line_number,
                                "cancelled response emitted audio after replacement start",
                            )
                        )
                continue

            if P_TTS_STOP_SENT.search(line) and "reason=interrupt" in line:
                response_match = re.search(r"response_id=(\d+)", line)
                for record in interrupt_records:
                    if response_match is None or int(response_match.group(1)) == record["cancelledResponseId"]:
                        if record["phase"] != 0:
                            record["orderInvalid"] = True
                        record["oldResponseStopped"] = True
                        record["phase"] = max(record["phase"], 1)
                        break
                continue
            stale = P_STALE_MODEL_DROP_IDS.search(line)
            if stale:
                old_id = int(stale.group("response_id"))
                current_id = int(stale.group("current_response_id"))
                for record in interrupt_records:
                    if (
                        old_id == record["cancelledResponseId"]
                        and current_id == record["replacementResponseId"]
                    ):
                        if record["phase"] != 1:
                            record["orderInvalid"] = True
                        record["staleSuppressed"] = True
                        record["phase"] = max(record["phase"], 2)
                continue
            replay = P_REPLAYED_INTERRUPT_AUDIO.search(line)
            if replay:
                response_id = int(replay.group("response_id"))
                for record in interrupt_records:
                    if response_id == record["replacementResponseId"]:
                        if record["phase"] != 2:
                            record["orderInvalid"] = True
                        record["interruptAudioReplayed"] = True
                        record["phase"] = max(record["phase"], 3)
                continue
            finalized = P_INTERRUPT_INPUT_FINALIZED.search(line)
            if finalized:
                response_id = int(finalized.group("response_id"))
                for record in interrupt_records:
                    if response_id == record["replacementResponseId"]:
                        if record["phase"] != 3:
                            record["orderInvalid"] = True
                        record["interruptInputFinalized"] = True
                        record["phase"] = max(record["phase"], 4)
                continue

            reconnect_owner = P_RECONNECT_REASON.search(line)
            if start_anchor.get("journeyId") and (
                reconnect_owner
                or P_REOPEN_READY.search(line)
                or P_RECONNECT_SUCCEEDED.search(line)
                or P_RECONNECT_FAILED.search(line)
                or P_REPLAYED_BUFFERED_AUDIO.search(line)
            ):
                continue
            if reconnect_owner:
                current_reopen = f"attempt-{reconnect_owner.group('attempt')}"
                replay_counts_by_reopen.setdefault(current_reopen, 0)
                current_reopen_ready = False
            reopen_ready = P_REOPEN_READY.search(line)
            if reopen_ready:
                ready_key = f"attempt-{reopen_ready.group('attempt')}"
                if current_reopen is None:
                    current_reopen = ready_key
                    replay_counts_by_reopen.setdefault(current_reopen, 0)
                if current_reopen == ready_key:
                    current_reopen_ready = True
            reconnect_succeeded = P_RECONNECT_SUCCEEDED.search(line)
            if reconnect_succeeded:
                if current_reopen is None:
                    current_reopen = f"attempt-{reconnect_succeeded.group('attempt')}"
                    replay_counts_by_reopen.setdefault(current_reopen, 0)
                current_reopen_ready = True
                pending_timeouts.clear()
                continue
            buffered_replay = P_REPLAYED_BUFFERED_AUDIO.search(line)
            if buffered_replay:
                if current_reopen is None or not current_reopen_ready:
                    failures.append(
                        _failure(
                            "BUFFER_REPLAY_WITHOUT_SUCCESSFUL_REOPEN",
                            line_number,
                            "buffered audio replay has no successful reopen marker",
                        )
                    )
                else:
                    replay_counts_by_reopen[current_reopen] += 1
                    if replay_counts_by_reopen[current_reopen] > 1:
                        failures.append(
                            _failure(
                                "DUPLICATE_BUFFER_REPLAY",
                                line_number,
                                "one reopen replayed buffered audio more than once",
                            )
                        )
                continue

            if P_WAITING_MODEL_TIMEOUT.search(line) or P_RECV_TIMEOUT.search(line):
                pending_timeouts.append({"line": line_number})
                if P_TIMEOUT_TERMINAL.search(line):
                    pending_timeouts.clear()
                continue
            if P_CLEAN_CONNECTION_CLOSE.search(line):
                pending_timeouts.clear()
                non_retriable_error_line = None
                current_reopen = None
                continue
            if (
                P_RECONNECT_FAILED.search(line)
                or P_FALLBACK.search(line)
                or P_FALLBACK_DISABLED.search(line)
                or P_TIMEOUT_TERMINAL.search(line)
            ):
                pending_timeouts.clear()
                if P_RECONNECT_FAILED.search(line):
                    if (
                        current_reopen is not None
                        and replay_counts_by_reopen.get(current_reopen, 0) > 0
                    ):
                        failures.append(
                            _failure(
                                "REOPEN_FAILED_AFTER_BUFFER_REPLAY",
                                line_number,
                                "reopen failed after buffered audio replay began",
                            )
                        )
                    current_reopen = None
                    current_reopen_ready = False

            classified = P_NON_RETRIABLE_CLASSIFICATION.search(line)
            if classified:
                non_retriable_error_line = line_number
                continue
            reconnect = reconnect_owner
            if reconnect and non_retriable_error_line is not None:
                failures.append(
                    _failure(
                        "NON_RETRIABLE_RECONNECT",
                        line_number,
                        "non-retriable error was followed by reconnect",
                    )
                )
                continue

            if P_HANDOFF_ACQUIRED.search(line):
                handoff_balance += 1
                handoff_lines.append(line_number)
                continue
            if P_HANDOFF_RELEASED.search(line):
                handoff_balance = 0
                handoff_lines.clear()
                continue
            pending_close = P_PENDING_TASK_CLOSE.search(line)
            if pending_close:
                tasks = pending_close.group("tasks").strip().lower()
                if tasks not in {"none", "[]", "0"}:
                    failures.append(
                        _failure(
                            "PENDING_TASK_AT_CLOSE",
                            line_number,
                            "connection closed with provider-owned work pending",
                        )
                    )
                continue

            lesson_start = P_LESSON_STEP_START.search(line)
            if lesson_start:
                active_lesson_step = {
                    "stepId": lesson_start.group("step_id"),
                    "pingLine": None,
                    "progressAfterPing": False,
                }
                continue
            lesson_ping = P_FIRMWARE_LESSON_PING.search(line)
            if lesson_ping and active_lesson_step is not None:
                journey_match = re.search(r"journey_id=(\S+)", line)
                if journey_match:
                    observed_marker_families[journey_match.group(1)].add(
                        "lesson_ping"
                    )
                if lesson_ping.group("step_id") == active_lesson_step["stepId"]:
                    active_lesson_step["pingLine"] = line_number
                    active_lesson_step["progressAfterPing"] = False
                continue
            lesson_progress = P_LESSON_STEP_PROGRESS.search(line)
            if lesson_progress and active_lesson_step is not None:
                journey_match = re.search(r"journey_id=(\S+)", line)
                if journey_match:
                    observed_marker_families[journey_match.group(1)].add(
                        "lesson_progress"
                    )
                if (
                    lesson_progress.group("step_id") == active_lesson_step["stepId"]
                    and active_lesson_step["pingLine"] is not None
                    and line_number > active_lesson_step["pingLine"]
                ):
                    active_lesson_step["progressAfterPing"] = True
                continue
            lesson_end = P_LESSON_STEP_END.search(line)
            if lesson_end and active_lesson_step is not None:
                if (
                    lesson_end.group("step_id") == active_lesson_step["stepId"]
                    and active_lesson_step["pingLine"] is not None
                    and not active_lesson_step["progressAfterPing"]
                ):
                    failures.append(
                        _failure(
                            "LESSON_PING_WITHOUT_PROGRESS",
                            active_lesson_step["pingLine"],
                            "firmware pings continued without lesson-step progress",
                        )
                    )
                active_lesson_step = None

    if start_anchor is None:
        failures.append(_failure("WINDOW_START_MISSING", 0, "start anchor is required"))
    if end_anchor is None:
        failures.append(_failure("WINDOW_END_MISSING", 0, "end anchor is required"))
    if receive_loops_active != 0:
        failures.append(
            _failure(
                "RECEIVE_LOOP_IMBALANCE",
                end_anchor["line"] if end_anchor else 0,
                "receive loop starts and stops are not balanced",
            )
        )
    if pending_timeouts:
        for timeout in pending_timeouts:
            failures.append(
                _failure(
                    "UNRECOVERED_TIMEOUT",
                    timeout["line"],
                    "waiting-model timeout has no bounded terminal outcome",
                )
            )
    for response_scope, response_id in scoped_active_responses.items():
        failures.append(
            _failure(
                "RESPONSE_START_WITHOUT_END",
                end_anchor["line"] if end_anchor else 0,
                str((*response_scope, response_id)),
            )
        )
    for reconnect_key, state in scoped_reconnects.items():
        if state.get("terminalCount") != 1:
            failures.append(
                _failure(
                    "RECONNECT_ATTEMPT_UNFINISHED",
                    end_anchor["line"] if end_anchor else 0,
                    str(reconnect_key),
                )
            )
    if handoff_balance:
        for line_number in handoff_lines:
            failures.append(
                _failure(
                    "UNRELEASED_LESSON_HANDOFF",
                    line_number,
                    "lesson handoff was not transferred or released",
                )
            )
    if (
        active_lesson_step is not None
        and active_lesson_step["pingLine"] is not None
        and not active_lesson_step["progressAfterPing"]
    ):
        failures.append(
            _failure(
                "LESSON_PING_WITHOUT_PROGRESS",
                active_lesson_step["pingLine"],
                "firmware pings continued without subsequent lesson-step progress",
            )
        )

    duplicate_response_ids = [
        response_id for response_id, count in response_starts.items() if count > 1
    ]
    for response_id in duplicate_response_ids:
        failures.append(
            _failure(
                "DUPLICATE_RESPONSE_ID",
                0,
                f"response {response_id} started more than once",
            )
        )

    required_correlation_fields = (
        "oldResponseStopped",
        "staleSuppressed",
        "interruptAudioReplayed",
        "interruptInputFinalized",
        "replacementStarted",
        "replacementStopped",
    )
    correlation_failures = []
    for record in interrupt_records:
        if record["orderInvalid"]:
            failures.append(
                _failure(
                    "BARGEIN_CORRELATION_ORDER_INVALID",
                    record["line"],
                    "barge-in evidence markers are not in causal order",
                )
            )
        missing = [field for field in required_correlation_fields if not record[field]]
        if missing:
            correlation_failures.append(
                {
                    "line": record["line"],
                    "missing": missing,
                }
            )
    if correlation_failures:
        for item in correlation_failures:
            failures.append(
                _failure(
                    "BARGEIN_CORRELATION_INCOMPLETE",
                    item["line"],
                    ",".join(item["missing"]),
                )
            )
    valid_correlations = [
        {
            "status": "PASS",
            "cancelledResponseId": record["cancelledResponseId"],
            "replacementResponseId": record["replacementResponseId"],
        }
        for record in interrupt_records
        if not record["orderInvalid"]
        and all(record[field] for field in required_correlation_fields)
    ]
    if scoped_interrupts:
        reported_correlations = [
            {
                "status": "PASS" if not item["orderInvalid"] and item["phase"] == 8 else "FAIL",
                **{key: value for key, value in item.items() if key not in {"phase", "orderInvalid"}},
            }
            for item in scoped_interrupts
        ]
    else:
        reported_correlations = valid_correlations
    for item in reported_correlations:
        if item.get("status") == "FAIL":
            failures.append(
                _failure(
                    "SCOPED_BARGEIN_CORRELATION_INVALID",
                    0,
                    str(item.get("journeyId", "unknown")),
                )
            )
    if scoped_interrupts and len(reported_correlations) == 1 and reported_correlations[0]["status"] == "PASS":
        correlation = {
            "status": "PASS",
            "cancelledResponseId": reported_correlations[0]["cancelledResponseId"],
            "replacementResponseId": reported_correlations[0]["replacementResponseId"],
        }
    elif len(valid_correlations) == 1:
        record = valid_correlations[0]
        correlation = {
            "status": "PASS",
            "cancelledResponseId": record["cancelledResponseId"],
            "replacementResponseId": record["replacementResponseId"],
        }
    elif not interrupt_records and not scoped_interrupts:
        correlation = {"status": "NOT_OBSERVED"}
    else:
        correlation = {
            "status": "FAIL" if correlation_failures else "MULTIPLE",
            "observedInterrupts": len(interrupt_records),
        }

    claimed_journeys = start_anchor.get("claimedJourneys", set()) if start_anchor else set()
    if "bargein" in claimed_journeys:
        required_families = {
            "interrupt_started",
            "interrupt_stopped",
            "interrupt_finalized",
            "response_started",
            "forwarded",
            "response_ended",
            "cleanup",
        }
        journey_id = start_anchor.get("journeyId") if start_anchor else None
        missing_families = sorted(
            required_families - observed_marker_families.get(journey_id, set())
        )
        if missing_families:
            failures.append(
                _failure(
                    "COVERAGE_MISSING",
                    start_anchor["line"] if start_anchor else 0,
                    ",".join(missing_families),
                )
            )
    if "reconnect" in claimed_journeys:
        reconnect_families = {
            "reconnect_started",
            "reopen_ready",
            "reconnect_outcome",
        }
        journey_id = start_anchor.get("journeyId") if start_anchor else None
        missing_families = sorted(
            reconnect_families - observed_marker_families.get(journey_id, set())
        )
        if missing_families:
            failures.append(
                _failure(
                    "COVERAGE_MISSING",
                    start_anchor["line"] if start_anchor else 0,
                    ",".join(missing_families),
                )
            )
    if "lesson" in claimed_journeys:
        lesson_families = {"lesson_ping", "lesson_progress"}
        journey_id = start_anchor.get("journeyId") if start_anchor else None
        missing_families = sorted(
            lesson_families - observed_marker_families.get(journey_id, set())
        )
        if missing_families:
            failures.append(
                _failure(
                    "COVERAGE_MISSING",
                    start_anchor["line"] if start_anchor else 0,
                    ",".join(missing_families),
                )
            )

    candidate_identity = (
        start_anchor.get("candidateIdentity") if start_anchor is not None else None
    )
    log_window = None
    if start_anchor is not None and end_anchor is not None:
        log_window = {
            "windowId": start_anchor["windowId"],
            "start": start_anchor["timestamp"].isoformat(),
            "end": end_anchor["timestamp"].isoformat(),
        }
    report = {
        "schemaVersion": SCHEMA_VERSION,
        "name": "google_live_log_reliability",
        "status": "PASS" if not failures else "FAIL",
        "candidateIdentity": candidate_identity,
        "logWindow": log_window,
        "receiveLoopBalance": receive_loops_active,
        "maxReceiveLoopsActive": max_receive_loops_active,
        "replayCountsByReopen": replay_counts_by_reopen,
        "duplicateResponseIds": duplicate_response_ids,
        "staleAudioAfterReplacement": stale_audio_after_replacement,
        "unrecoveredTimeouts": [item["line"] for item in pending_timeouts],
        "unreleasedLessonHandoffs": handoff_lines,
        "fatalHits": fatal_hits,
        "correlation": correlation,
        "correlations": reported_correlations,
        "failures": failures,
    }
    return redact_mapping(report)


def correlate_websocket_bargein_evidence(
    transport_observation: dict[str, Any],
    log_verdict: dict[str, Any],
    *,
    expected_candidate_identity: dict[str, Any],
) -> dict[str, Any]:
    """Upgrade Task 4's pending transport record only with exact bounded log proof."""
    failures: list[dict[str, Any]] = []
    log_contract_failures = _validate_log_reliability_contract(
        log_verdict,
        expected_candidate_identity=expected_candidate_identity,
        expected_log_window=transport_observation.get("logWindow"),
    )
    failures.extend(log_contract_failures)
    safe_log_verdict = log_verdict if isinstance(log_verdict, Mapping) else {}
    expected_pending = "PENDING_BOUNDED_SERVER_LOG_VERIFICATION"
    required_transport = {
        "schemaVersion": SCHEMA_VERSION,
        "name": "websocket_audio_bargein_transport",
        "status": "SKIPPED",
        "pendingCode": expected_pending,
        "correlationSource": "server_log",
        "correlationStatus": expected_pending,
        "aggregateReleaseEligible": False,
        "interruptStopMarkerObserved": True,
        "replacementResponseStarted": True,
        "replacementResponseStopped": True,
    }
    for contract_field, expected in required_transport.items():
        if transport_observation.get(contract_field) != expected:
            failures.append(
                {"code": "TRANSPORT_CONTRACT_MISMATCH", "field": contract_field}
            )
    replacement_chunks = transport_observation.get("replacementBinaryChunks")
    if (
        isinstance(replacement_chunks, bool)
        or not isinstance(replacement_chunks, int)
        or replacement_chunks < 1
    ):
        failures.append({"code": "TRANSPORT_REPLACEMENT_AUDIO_MISSING"})
        replacement_chunks = 0
    bargein_stop_ms = transport_observation.get("bargeinStopMs")
    if (
        isinstance(bargein_stop_ms, bool)
        or not isinstance(bargein_stop_ms, (int, float))
        or not math.isfinite(bargein_stop_ms)
        or bargein_stop_ms < 0
    ):
        failures.append({"code": "TRANSPORT_LATENCY_INVALID"})
        bargein_stop_ms = None
    elif bargein_stop_ms > GOOGLE_LIVE_LIMITS["physicalBargeinP95Ms"]:
        failures.append({"code": "BARGEIN_STOP_LATENCY_EXCEEDED"})
    max_output_gap_ms = transport_observation.get("maxServerOutputGapMs")
    if (
        isinstance(max_output_gap_ms, bool)
        or not isinstance(max_output_gap_ms, (int, float))
        or not math.isfinite(max_output_gap_ms)
        or max_output_gap_ms < 0
    ):
        failures.append({"code": "TRANSPORT_OUTPUT_GAP_INVALID"})
        max_output_gap_ms = None
    elif max_output_gap_ms > GOOGLE_LIVE_LIMITS["serverOutputGapMaxMs"]:
        failures.append({"code": "SERVER_OUTPUT_GAP_EXCEEDED"})
    if _contains_response_id_key(transport_observation):
        failures.append({"code": "TRANSPORT_RESPONSE_ID_NOT_ALLOWED"})
    if transport_observation.get("candidateIdentity") != expected_candidate_identity:
        failures.append({"code": "CANDIDATE_IDENTITY_MISMATCH", "layer": "transport"})
    if safe_log_verdict.get("candidateIdentity") != expected_candidate_identity:
        failures.append({"code": "CANDIDATE_IDENTITY_MISMATCH", "layer": "server_log"})
    if transport_observation.get("logWindow") != safe_log_verdict.get("logWindow"):
        failures.append({"code": "LOG_WINDOW_MISMATCH"})
    journey_id = transport_observation.get("journeyId")
    if not isinstance(journey_id, str) or not journey_id:
        failures.append({"code": "TRANSPORT_JOURNEY_ID_INVALID"})
        matching_correlations = []
    else:
        correlations = safe_log_verdict.get("correlations", [])
        matching_correlations = (
            [
                item
                for item in correlations
                if isinstance(item, Mapping) and item.get("journeyId") == journey_id
            ]
            if isinstance(correlations, list)
            else []
        )
    if len(matching_correlations) != 1:
        failures.append(
            {
                "code": "SERVER_LOG_JOURNEY_CORRELATION_COUNT",
                "observed": len(matching_correlations),
            }
        )
    elif matching_correlations[0].get("status") != "PASS":
        failures.append({"code": "SERVER_LOG_JOURNEY_CORRELATION_NOT_PASS"})

    transport_layer = {
        "name": "websocket_audio_bargein_transport",
        "status": "PASS" if not failures else "FAIL",
        "candidateIdentity": transport_observation.get("candidateIdentity"),
    }
    log_layer = {
        "name": "google_live_log_reliability",
        "status": "FAIL" if log_contract_failures else safe_log_verdict.get("status"),
        "candidateIdentity": safe_log_verdict.get("candidateIdentity"),
    }
    verdict = reliability_verdict(
        expected_candidate_identity,
        [transport_layer, log_layer],
    )
    failures.extend(verdict["failures"])
    status = "PASS" if not failures else "FAIL"
    correlation = (
        matching_correlations[0]
        if len(matching_correlations) == 1
        and matching_correlations[0].get("status") == "PASS"
        else {}
    )
    report = {
        "schemaVersion": SCHEMA_VERSION,
        "name": "websocket_audio_bargein_correlated",
        "status": status,
        "candidateIdentity": expected_candidate_identity,
        "journeyId": journey_id,
        "logWindow": safe_log_verdict.get("logWindow"),
        "correlationSource": "server_log",
        "correlationStatus": "PASS" if status == "PASS" else "FAIL",
        "aggregateReleaseEligible": status == "PASS",
        "oldResponseStopped": status == "PASS",
        "replacementResponseStarted": bool(
            transport_observation.get("replacementResponseStarted")
        ),
        "replacementResponseStopped": bool(
            transport_observation.get("replacementResponseStopped")
        ),
        "replacementBinaryChunks": replacement_chunks,
        "bargeinStopMs": bargein_stop_ms,
        "maxServerOutputGapMs": max_output_gap_ms,
        "cancelledResponseId": correlation.get("cancelledResponseId"),
        "replacementResponseId": correlation.get("replacementResponseId"),
        "layers": verdict["layers"],
        "failures": failures,
    }
    return redact_mapping(report)


def _validate_log_reliability_contract(
    report: Any,
    *,
    expected_candidate_identity: dict[str, Any],
    expected_log_window: Any,
) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []

    def mismatch(field: str) -> None:
        failures.append({"code": "SERVER_LOG_CONTRACT_MISMATCH", "field": field})

    if not isinstance(report, Mapping):
        mismatch("report")
        return failures

    required_values = {
        "schemaVersion": SCHEMA_VERSION,
        "name": "google_live_log_reliability",
        "status": "PASS",
        "candidateIdentity": expected_candidate_identity,
        "logWindow": expected_log_window,
        "failures": [],
    }
    for contract_field, expected in required_values.items():
        if contract_field not in report or report.get(contract_field) != expected:
            mismatch(contract_field)

    if not _exact_zero_int(report.get("receiveLoopBalance")):
        mismatch("receiveLoopBalance")
    max_receive_loops = report.get("maxReceiveLoopsActive")
    if (
        isinstance(max_receive_loops, bool)
        or not isinstance(max_receive_loops, int)
        or max_receive_loops not in {0, 1}
    ):
        mismatch("maxReceiveLoopsActive")
    if not _exact_zero_int(report.get("staleAudioAfterReplacement")):
        mismatch("staleAudioAfterReplacement")

    replay_counts = report.get("replayCountsByReopen")
    if not isinstance(replay_counts, Mapping) or any(
        not isinstance(key, str)
        or isinstance(value, bool)
        or not isinstance(value, int)
        or value not in {0, 1}
        for key, value in replay_counts.items()
    ):
        mismatch("replayCountsByReopen")

    empty_list_fields = (
        "duplicateResponseIds",
        "unrecoveredTimeouts",
        "unreleasedLessonHandoffs",
        "fatalHits",
    )
    for contract_field in empty_list_fields:
        if report.get(contract_field) != []:
            mismatch(contract_field)
    if not isinstance(report.get("correlations"), list):
        mismatch("correlations")

    correlations = report.get("correlations")
    if isinstance(correlations, list):
        for item in correlations:
            if (
                not isinstance(item, Mapping)
                or item.get("status") != "PASS"
                or not all(
                    isinstance(item.get(contract_field), str)
                    and bool(item.get(contract_field))
                    for contract_field in (
                        "journeyId",
                        "connectionId",
                        "liveConnectionId",
                    )
                )
                or not _nonnegative_int(item.get("cancelledResponseId"))
                or not _nonnegative_int(item.get("replacementResponseId"))
            ):
                mismatch("correlations")
                break
    correlation = report.get("correlation")
    if not isinstance(correlation, Mapping):
        mismatch("correlation")
    elif correlation.get("status") == "PASS":
        if (
            not isinstance(correlations, list)
            or len(correlations) != 1
            or not _nonnegative_int(correlation.get("cancelledResponseId"))
            or not _nonnegative_int(correlation.get("replacementResponseId"))
        ):
            mismatch("correlation")
        else:
            item = correlations[0]
            if isinstance(item, Mapping) and any(
                correlation.get(contract_field) != item.get(contract_field)
                for contract_field in (
                    "cancelledResponseId",
                    "replacementResponseId",
                )
            ):
                mismatch("correlation")
    elif correlation.get("status") == "MULTIPLE":
        observed = correlation.get("observedInterrupts")
        if (
            isinstance(observed, bool)
            or not isinstance(observed, int)
            or not isinstance(correlations, list)
            or len(correlations) < 2
            or observed not in {0, len(correlations)}
        ):
            mismatch("correlation")
    elif correlation.get("status") == "NOT_OBSERVED":
        if correlations != []:
            mismatch("correlation")
    else:
        mismatch("correlation")
    return failures


def _nonnegative_int(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value >= 0


def _exact_zero_int(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value == 0


def _contains_response_id_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = re.sub(r"[^a-zA-Z0-9]", "", str(key)).lower()
            if "responseid" in normalized or _contains_response_id_key(item):
                return True
    elif isinstance(value, list):
        return any(_contains_response_id_key(item) for item in value)
    return False


def _sanitize_reliability_cli_report(report: dict[str, Any]) -> dict[str, Any]:
    """Remove legacy analyzer fields that can contain raw exception text."""
    safe = dict(report)
    safe_sessions = []
    for session in report.get("per_session", []):
        safe_session = dict(session)
        safe_session.pop("drop_event", None)
        safe_session.pop("fallback_reason", None)
        safe_session.pop("fallback_disabled_reason", None)
        safe_sessions.append(safe_session)
    safe["per_session"] = safe_sessions
    return redact_mapping(safe)


def _scoped_marker_validation(line: str) -> tuple[bool, bool]:
    for hint, patterns in _SCOPED_MARKER_FAMILIES:
        if hint.search(line) is None:
            continue
        valid = any(
            (match := pattern.search(line)) is not None and match.end() == len(line)
            for pattern in patterns
        )
        return True, valid
    return False, False


def _correlate_transport_cli(
    *,
    log_path: Path,
    transport_path: Path,
    expected_candidate_path: Path,
) -> dict[str, Any]:
    try:
        transport = json.loads(transport_path.read_text(encoding="utf-8"))
        expected_candidate = json.loads(
            expected_candidate_path.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as exc:
        return redact_mapping(
            {
                "schemaVersion": SCHEMA_VERSION,
                "name": "websocket_audio_bargein_correlated",
                "status": "FAIL",
                "failures": [{"code": "EVIDENCE_JSON_INVALID", "detail": type(exc).__name__}],
            }
        )
    failures = []
    journey_id = transport.get("journeyId")
    log_window = transport.get("logWindow")
    if transport.get("schemaVersion") != SCHEMA_VERSION:
        failures.append({"code": "TRANSPORT_SCHEMA_INVALID"})
    if transport.get("candidateIdentity") != expected_candidate:
        failures.append({"code": "CANDIDATE_IDENTITY_MISMATCH"})
    if not isinstance(journey_id, str) or re.fullmatch(r"[A-Za-z0-9._:-]{1,64}", journey_id) is None:
        failures.append({"code": "TRANSPORT_JOURNEY_ID_INVALID"})
    if not isinstance(log_window, dict):
        failures.append({"code": "TRANSPORT_LOG_WINDOW_INVALID"})
        start = end = None
    else:
        try:
            start = datetime.fromisoformat(log_window["start"])
            end = datetime.fromisoformat(log_window["end"])
            if (
                start.tzinfo is not None
                or end.tzinfo is not None
                or start > end
                or log_window.get("windowId") != journey_id
            ):
                raise ValueError
        except (KeyError, TypeError, ValueError):
            failures.append({"code": "TRANSPORT_LOG_WINDOW_INVALID"})
            start = end = None
    if failures:
        return redact_mapping(
            {
                "schemaVersion": SCHEMA_VERSION,
                "name": "websocket_audio_bargein_correlated",
                "status": "FAIL",
                "journeyId": journey_id,
                "candidateIdentity": expected_candidate,
                "failures": failures,
            }
        )
    if not _candidate_identity_valid(expected_candidate):
        return redact_mapping(
            {
                "schemaVersion": SCHEMA_VERSION,
                "name": "websocket_audio_bargein_correlated",
                "status": "FAIL",
                "journeyId": journey_id,
                "candidateIdentity": expected_candidate,
                "failures": [{"code": "EXPECTED_CANDIDATE_IDENTITY_INVALID"}],
            }
        )

    selected_lines = []
    with log_path.open("r", encoding="utf-8", errors="replace") as fh:
        for raw_line in fh:
            timestamp = parse_timestamp(raw_line)
            window_marker_line = "Google Live reliability_window_" in raw_line
            stripped_line = raw_line.rstrip("\r\n")
            scoped_marker_line, scoped_marker_valid = _scoped_marker_validation(
                stripped_line
            )
            in_window = timestamp is not None and start <= timestamp <= end
            malformed_scoped = (
                in_window and scoped_marker_line and not scoped_marker_valid
            )
            malformed_window = in_window and window_marker_line and not (
                P_RELIABILITY_WINDOW_START.search(stripped_line)
                or P_RELIABILITY_WINDOW_END.search(stripped_line)
            )
            if (
                malformed_scoped
                or malformed_window
                or (
                    timestamp is None
                    and (
                        scoped_marker_line
                        or window_marker_line
                        or _is_reliability_line(raw_line)
                    )
                )
            ):
                return redact_mapping(
                    {
                        "schemaVersion": SCHEMA_VERSION,
                        "name": "websocket_audio_bargein_correlated",
                        "status": "FAIL",
                        "journeyId": journey_id,
                        "candidateIdentity": expected_candidate,
                        "failures": [{"code": "MALFORMED_BOUNDED_LOG_MARKER"}],
                    }
                )
            if timestamp is not None and start <= timestamp <= end:
                selected_lines.append(raw_line.rstrip("\n"))
    identity = json.dumps(expected_candidate, sort_keys=True, separators=(",", ":"))
    bounded_lines = [
        f"{start:%Y-%m-%d %H:%M:%S} Google Live reliability_window_start "
        f"window_id={journey_id} journey_id={journey_id} journeys=bargein "
        f"candidate_identity={identity}",
        *selected_lines,
        f"{end:%Y-%m-%d %H:%M:%S} Google Live reliability_window_end window_id={journey_id}",
    ]
    with tempfile.TemporaryDirectory() as temp_dir:
        bounded_path = Path(temp_dir) / "bounded.log"
        bounded_path.write_text("\n".join(bounded_lines), encoding="utf-8")
        log_verdict = analyze_reliability_window(bounded_path)
    return correlate_websocket_bargein_evidence(
        transport,
        log_verdict,
        expected_candidate_identity=expected_candidate,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", required=True, type=Path, help="Path to server.log")
    parser.add_argument("--out-json", type=Path, help="Optional JSON output path")
    parser.add_argument(
        "--out-md", type=Path, help="Optional markdown evidence file path"
    )
    parser.add_argument(
        "--check-chain",
        action="store_true",
        help=(
            "Detect bargein success-chains (echo_bypass → user_interrupted → "
            "tts_state_stop_sent → replayed_interrupt_audio → "
            "interrupt_input_finalized → transcript source=user) and report "
            "missing markers and per-span latency. Exits non-zero if any chain "
            "is incomplete."
        ),
    )
    parser.add_argument(
        "--pain-summary",
        action="store_true",
        help=(
            "Map Phase 1.2/1.3 markers to the 5 user pains (P1-P5) and emit "
            "a JSON summary: P1 user-speech-lost, P2 stop-latency, "
            "P3 response-overlap, P4 function-calls, P5 music-ducking."
        ),
    )
    parser.add_argument(
        "--check-reliability",
        action="store_true",
        help=(
            "Verify the explicitly anchored Google Live reliability window, "
            "embed the result in JSON output, and exit non-zero on failure."
        ),
    )
    parser.add_argument("--correlate-transport", type=Path)
    parser.add_argument("--expected-candidate-json", type=Path)
    args = parser.parse_args()

    if not args.log.exists():
        raise SystemExit(f"Log file not found: {args.log}")

    if args.correlate_transport is not None:
        if args.expected_candidate_json is None or args.out_json is None:
            raise SystemExit(
                "--correlate-transport requires --expected-candidate-json and --out-json"
            )
        correlated = _correlate_transport_cli(
            log_path=args.log,
            transport_path=args.correlate_transport,
            expected_candidate_path=args.expected_candidate_json,
        )
        args.out_json.parent.mkdir(parents=True, exist_ok=True)
        args.out_json.write_text(
            json.dumps(redact_mapping(correlated), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        if correlated.get("status") != "PASS":
            raise SystemExit(1)
        return

    if getattr(args, "check_chain", False):
        chain_report = check_chain(args.log)
        print(json.dumps(chain_report, indent=2, default=str))
        if chain_report["incomplete_chains"] > 0:
            print(
                f"\nWARNING: {chain_report['incomplete_chains']} incomplete bargein "
                "chain(s) found — missing markers indicate broken latency path.",
                file=sys.stderr,
            )
            raise SystemExit(1)
        return

    if getattr(args, "pain_summary", False):
        pain_report = summarize_pains(args.log)
        print(json.dumps(pain_report, indent=2, default=str))
        return

    report = analyze(args.log)
    reliability = None
    if getattr(args, "check_reliability", False):
        report = _sanitize_reliability_cli_report(report)
        reliability = analyze_reliability_window(args.log)
        report["reliability"] = reliability
    if args.out_json:
        args.out_json.parent.mkdir(parents=True, exist_ok=True)
        args.out_json.write_text(json.dumps(report, indent=2, default=str))
        print(f"Wrote JSON report: {args.out_json}")
    if args.out_md:
        args.out_md.parent.mkdir(parents=True, exist_ok=True)
        args.out_md.write_text(render_markdown(report, args.log))
        print(f"Wrote markdown report: {args.out_md}")
    if not args.out_json and not args.out_md:
        print(json.dumps(report, indent=2, default=str))
    if reliability is not None and reliability["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
