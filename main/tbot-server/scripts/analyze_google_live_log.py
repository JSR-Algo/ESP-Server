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
import re
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

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
P_RECONNECT = re.compile(r"Google Live reconnect attempt (?P<n>\d+)")
P_SERVER_INT_IGNORED = re.compile(r"Google Live server interruption ignored by config")
P_CONN_OPEN = re.compile(r"core\.connection - (?P<ip>\S+) conn - Headers:")
P_GOAWAY = re.compile(r"goAway|go_away|sent 1011|received 1011|1008", re.I)
P_RECV_TIMEOUT = re.compile(r"Google Live receive timed out")
P_ECHO_SUPPRESSED = re.compile(
    r"Google Live echo_suppressed reason=(?P<reason>\w+) bytes=(?P<bytes>\d+) rms=(?P<rms>\d+|n/a)"
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
P_TTS_STOP_SENT = re.compile(r"tts_state_stop_sent|tts_stop_sent")

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
    goaway_seen: bool = False
    echo_suppressed_count: int = 0
    echo_bypass_count: int = 0
    echo_suppressed_rms: list[int] = field(default_factory=list)
    echo_bypass_rms: list[int] = field(default_factory=list)
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

            if P_GOAWAY.search(line):
                current.goaway_seen = True

            m = P_ECHO_SUPPRESSED.search(line)
            if m:
                current.echo_suppressed_count += 1
                if m.group("rms").isdigit():
                    current.echo_suppressed_rms.append(int(m.group("rms")))
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

    return build_report(sessions, total_lines, log_path)


def build_report(sessions: list[SessionState], total_lines: int, log_path: Path) -> dict:
    all_rms_speaking = [r for s in sessions for r in s.rms_while_speaking]
    all_rms_silent = [r for s in sessions for r in s.rms_while_silent]
    all_bargein_rms = [r for s in sessions for r in s.bargein_rms_values]
    all_echo_suppressed_rms = [r for s in sessions for r in s.echo_suppressed_rms]
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
            "abrupt_drop_sessions": drop_count,
            "goaway_sessions": goaway_count,
            "echo_suppressed": sum(s.echo_suppressed_count for s in sessions),
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
        },
        "interrupt_reason_distribution": dict(reason_counts),
        "music_control_tool_distribution": dict(music_tool_counts),
        "stale_model_event_type_distribution": dict(stale_model_event_type_counts),
        "clean_user_turn_reason_distribution": dict(clean_user_turn_reason_counts),
        "echo_suppressed_rms": summary_stats(all_echo_suppressed_rms),
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
                "reconnect_attempts": s.reconnect_attempts,
                "echo_suppressed": s.echo_suppressed_count,
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
        f"- abrupt drop sessions (websocket 1000 mid-send): **{totals['abrupt_drop_sessions']}**",
        f"- goAway / 1008 / 1011 sessions: **{totals['goaway_sessions']}**",
        f"- echo_suppressed events: **{totals['echo_suppressed']}**",
        f"- echo_bypass events: **{totals['echo_bypass']}**",
        f"- stale model events dropped: **{totals['stale_model_event_dropped']}**",
        f"- model output still blocked waiting user turn: **{totals['model_output_still_blocked_waiting_user_turn']}**",
        f"- clean user turns opened: **{totals['clean_user_turn_opened']}**",
        f"- replayed interrupt audio batches: **{totals['replayed_interrupt_audio']}**",
        f"- replayed interrupt frames: **{totals['replayed_interrupt_frames']}**",
        f"- interrupt input finalized: **{totals['interrupt_input_finalized']}**",
        f"- music_control_intents: **{totals['music_control_intents']}**",
        "",
        f"- interrupt reason distribution: `{report['interrupt_reason_distribution']}`",
        f"- music control tool distribution: `{report['music_control_tool_distribution']}`",
        f"- stale model event type distribution: `{report['stale_model_event_type_distribution']}`",
        f"- clean user turn reason distribution: `{report['clean_user_turn_reason_distribution']}`",
        f"- echo_suppressed_rms: `{echo_suppressed}`",
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
    args = parser.parse_args()

    if not args.log.exists():
        raise SystemExit(f"Log file not found: {args.log}")

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


if __name__ == "__main__":
    main()
