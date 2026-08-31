#!/usr/bin/env python3
import argparse
import hashlib
import json
import math
import re
import sys
import unicodedata
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

_SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(_SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(_SERVER_ROOT))

from scripts.google_live_reliability import (  # noqa: E402
    GOOGLE_LIVE_LIMITS,
    SCHEMA_VERSION,
    forbidden_report_fields,
    percentile,
    validate_candidate_soak_report,
    validate_log_reliability_contract,
)

FATAL_PATTERNS = (
    "Traceback",
    "Logging error",
    "Client disconnected",
    "STEP_TIMEOUT",
    "FRAME_ACK_TIMEOUT",
    "lesson_error",
    "duplicate session",
    "stale audio",
    "self-interrupt",
    "fallback_triggered",
    "fallback_disabled",
    "Send first voice segment:",
    "Send audio message:",
    '"state":"sentence_start"',
    '"state": "sentence_start"',
    "'state': 'sentence_start'",
    "state=sentence_start",
    "audio_decision decision=suppress_echo reason=robot_speaking",
    "audio_decision decision=drop_input reason=output_active",
    "audio_decision decision=hold_interrupt_audio reason=blocked_output",
    "Google Live echo_bypass",
    "Google Live echo_suppressed reason=robot_speaking",
    "Google Live AEC import failed",
    "Google Live AEC process_mic failed while output active",
    "Google Live AEC process_mic failed, dropping AEC for this chunk",
    "Google Live AEC reference resample failed",
    "Google Live AEC push_reference failed",
    "Google Live dropped invalid input audio",
    "Google Live dropped corrupt input opus",
    "Google Live receive timed out",
    "Google Live waiting_model_timeout",
    "Google Live runtime failure type=",
    "Google Live unavailable type=",
    "Google Live lesson_prompt_output_guard_timeout",
    "Google Live lesson_prompt_playback_guard_timeout",
    "Google Live reconnect attempt",
    "reconnect_started",
    "Google Live tool timeout",
    "interrupt_started reason=loud_input",
    "Google Live user_interrupted reason=loud_input",
    "Google Live server interruption ignored by config",
    "Google Live interruption suppressed_for_age",
    "Google Live transcript_barge_in suppressed_for_age",
    "Google Live transcript_barge_in suppressed_as_model_echo",
    "Google Live safe_deflection live text unavailable",
    "Google Live safe_deflection live text failed",
    "Google Live safety_block event dropped",
    "JPEG decode failed",
    "undecodable image; skipping",
    "lesson_step rejected",
    "lesson_step poster fetch failed",
    "lesson_step teaching object fetch failed",
    "lesson_step robot overlay fetch failed",
    "lesson image fetch: status",
    "lesson image fetch: read error",
    "lesson image fetch: empty body",
)

FATAL_REGEX_PATTERNS = (
    ("tts sentence_start", re.compile(r"['\"]?state['\"]?\s*[:=]\s*['\"]?sentence_start['\"]?")),
    ("aec_bypassed", re.compile(r"Google Live AEC initialised .*bypassed=True")),
)

IMMEDIATE_PRONUNCIATION_SCORING_PATTERNS = (
    re.compile(r"(?i)\bpronunciation\b.*\b(score|scoring|grade|grading|evaluate|evaluating|assess|assessing|correct|correction)\b"),
    re.compile(r"(?i)\b(score|scoring|grade|grading|evaluate|evaluating|assess|assessing|correct|correction)\b.*\bpronunciation\b"),
    re.compile(r"(?i)\b(child response|child_response|recognized[_-]?text|transcript)\b.*\b(score|grade|grading|correct\s*[:=]\s*(?:true|false|0|1))\b"),
    re.compile(r"(?i)\b(score|grade|grading|correct\s*[:=]\s*(?:true|false|0|1))\b.*\b(child response|child_response|recognized[_-]?text|transcript)\b"),
    re.compile(r"(?i)\bscore\s*[:=]\s*[^\s,;]+"),
    re.compile(r"(?i)\b(phát âm|phat am)\b.*\b(chưa chuẩn|chua chuan|không chuẩn|khong chuan|sai|lỗi|loi|sửa|sua|đánh giá|danh gia|chấm|cham)\b"),
    re.compile(r"(?i)\b(chưa chuẩn|chua chuan|không chuẩn|khong chuan|sai|lỗi|loi|sửa|sua|đánh giá|danh gia|chấm|cham)\b.*\b(phát âm|phat am)\b"),
    re.compile(r"(?i)\bsai rồi\b"),
)

FIRST_AUDIO_OUT_MARKER_PATTERN = (
    r"(?:Google Live first_audio_out_latency_ms=[\d.]+|"
    r"Google Live turn_latency_ms=[\d.]+ phase=first_audio_out)"
)

_CANDIDATE_STAGE_COUNTS = (
    ("conversation", 17),
    ("bargein", 10),
    ("quiet", 2),
    ("reopen", 1),
    ("reconnect", 1),
    ("lesson", 1),
    ("conversation_after_lesson", 1),
)
_PHYSICAL_AUDIT_SAMPLE_COUNTS = {
    "firstAudio": 10,
    "interruptStop": 10,
    "physicalBargein": 10,
    "serverOutputGap": 10,
}
_INTENTIONAL_OUTPUT_GAP_BOUNDARIES = frozenset(
    {"interrupt", "turn_completion", "backpressure", "transport_recovery"}
)


def _is_target_physical_ws_connection_line(line, device_id, client_id, server_ip=None):
    if "Headers:" not in line:
        return False
    if device_id not in line or client_id not in line:
        return False
    if "127.0.0.1 conn" in line:
        return False
    if server_ip and f"{server_ip} conn" in line:
        return False
    if "Python/" in line or "websockets/" in line:
        return False
    return True

def _has_physical_ws_connection(log_text, device_id, client_id, server_ip=None):
    return any(
        _is_target_physical_ws_connection_line(line, device_id, client_id, server_ip)
        for line in log_text.splitlines()
    )

def _is_target_disconnect_line(line, device_id):
    return "Client disconnected" in line and device_id in line

def _target_physical_ws_log_text(log_text, device_id, client_id, server_ip=None):
    lines = []
    active = False
    for line in log_text.splitlines():
        if "Headers:" in line:
            active = _is_target_physical_ws_connection_line(
                line,
                device_id,
                client_id,
                server_ip,
            )
        if active:
            lines.append(line)
            if _is_target_disconnect_line(line, device_id):
                active = False
    return "\n".join(lines)


def _extract_step_id(line):
    match = re.search(r"\bstepId=([^\s,'\"}]+)", line)
    if match:
        return match.group(1)
    match = re.search(r'"stepId"\s*:\s*"([^"]+)"', line)
    if match:
        return match.group(1)
    return None


def _extract_step_type(line):
    match = re.search(r"\bstepType=([^\s,'\"}]+)", line)
    if match:
        return match.group(1)
    match = re.search(r'"stepType"\s*:\s*"([^"]+)"', line)
    if match:
        return match.group(1)
    return None


def _extract_prompt_text(line):
    match = re.search(r"\btext\s*=\s*'([^']*)'", line)
    if match:
        return match.group(1)
    match = re.search(r'\btext\s*=\s*"([^"]*)"', line)
    if match:
        return match.group(1)
    match = re.search(r'"text"\s*:\s*"([^"]*)"', line)
    if match:
        return match.group(1)
    return ""


def _normalize_match_text(text):
    text = unicodedata.normalize("NFKC", str(text)).casefold()
    text = re.sub(r"[\W_]+", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


def _normalize_transcript_for_echo(text):
    if not text:
        return ""
    return "".join(ch for ch in str(text).lower() if ch.isalnum())

def _user_transcript_texts(lines):
    texts = []
    for line in lines:
        if "Google Live transcript source=user" not in line:
            continue
        text = _extract_prompt_text(line).strip()
        if text:
            texts.append(text)
    return texts


def _model_echo_user_transcript_count(lines):
    model_texts = set()
    count = 0
    for line in lines:
        if "Google Live transcript source=model" in line:
            normalized = _normalize_transcript_for_echo(_extract_prompt_text(line))
            if normalized:
                model_texts.add(normalized)
            continue
        if "Google Live transcript source=user" not in line:
            continue
        normalized = _normalize_transcript_for_echo(_extract_prompt_text(line))
        if any(
            (len(normalized) >= 12 and normalized == model_text)
            or (len(normalized) >= 12 and normalized in model_text)
            for model_text in model_texts
        ):
            count += 1
    return count

def _has_production_live_identity(lines):
    return any(
        "Google Live session identity" in line
        and "model=gemini-3.1-flash-live-preview" in line
        and "voice=Kore" in line
        and "language=vi-VN" in line
        for line in lines
    )

def _live_identity_mismatch_count(lines):
    return sum(
        1
        for line in lines
        if "Google Live session identity" in line
        and not (
            "model=gemini-3.1-flash-live-preview" in line
            and "voice=Kore" in line
            and "language=vi-VN" in line
        )
    )

def _expected_user_transcript_match_count(transcript_texts, expected_transcripts):
    normalized_transcripts = [
        _normalize_match_text(text) for text in transcript_texts
    ]
    matches = 0
    for expected in expected_transcripts:
        normalized_expected = _normalize_match_text(expected)
        if not normalized_expected:
            continue
        if any(
            f" {normalized_expected} " in f" {transcript} "
            for transcript in normalized_transcripts
        ):
            matches += 1
    return matches


_TRANSCRIPT_PROOF_MARKER_RE = re.compile(
    r"Google Live evidence_transcript_match "
    r"journey_id=([A-Za-z0-9._:-]{1,64}) slot=(\d+) "
    r"phase=(interrupt|lesson|post_lesson) chars=(\d+) matched=(true|false)\b"
)


def _transcript_proof_markers(lines, *, journey_id):
    markers = []
    malformed = 0
    for line in lines:
        if "Google Live evidence_transcript_match" not in line:
            continue
        match = _TRANSCRIPT_PROOF_MARKER_RE.search(line)
        if match is None:
            malformed += 1
            continue
        if match.group(1) != journey_id:
            malformed += 1
            continue
        if match.group(5) != "true":
            continue
        markers.append(
            {
                "slot": int(match.group(2)),
                "phase": match.group(3),
                "chars": int(match.group(4)),
                "matched": True,
            }
        )
    return markers, malformed


def _post_lesson_response_chain_count(lines, expected_transcripts):
    expected_transcripts = [
        str(text).strip()
        for text in (expected_transcripts or [])
        if str(text).strip()
    ]
    after_lesson = False
    pending_transcripts = 0
    chains = 0
    for line in lines:
        if re.search(r"lesson_completed|stepsCompleted=\d+", line):
            after_lesson = True
            continue
        if not after_lesson:
            continue
        if "Google Live transcript source=user" in line:
            text = _extract_prompt_text(line).strip()
            if text and (
                not expected_transcripts
                or _expected_user_transcript_match_count([text], expected_transcripts)
            ):
                pending_transcripts += 1
            continue
        if pending_transcripts and re.search(FIRST_AUDIO_OUT_MARKER_PATTERN, line):
            pending_transcripts -= 1
            chains += 1
    return chains

def _user_transcript_texts_after_interruption(lines):
    texts = []
    seen_interruption = False
    for line in lines:
        if re.search(r"Google Live interruption output_age_ms=\d", line):
            seen_interruption = True
            continue
        if not seen_interruption:
            continue
        if "Google Live transcript source=user" not in line:
            continue
        text = _extract_prompt_text(line).strip()
        if text:
            texts.append(text)
    return texts

def _hash_order_match_count(observed_hashes, expected_hashes):
    observed = [str(hash_value).lower() for hash_value in observed_hashes]
    position = 0
    matches = 0
    for expected in expected_hashes:
        expected_hash = str(expected).lower()
        while position < len(observed) and observed[position] != expected_hash:
            position += 1
        if position >= len(observed):
            break
        matches += 1
        position += 1
    return matches

def _unexpected_hash_count(observed_hashes, expected_hashes):
    expected = {str(hash_value).lower() for hash_value in expected_hashes}
    return sum(
        1 for hash_value in observed_hashes
        if str(hash_value).lower() not in expected
    )


def _is_lesson_step_prompt_line(line):
    lowered = line.lower()
    return (
        "lesson_step_prompt queued via tts" in lowered
        or "lesson_step_prompt queued via live text" in lowered
        or "lesson_step_prompt sent via live text" in lowered
    )


def _is_lesson_step_live_text_prompt_line(line):
    lowered = line.lower()
    return "lesson_step_prompt sent via live text" in lowered


def _is_lesson_local_tts_prompt_line(line):
    lowered = line.lower()
    return "lesson_" in lowered and "queued via tts" in lowered


def _lesson_live_text_chars(lines):
    total = 0
    for line in lines:
        if not _is_lesson_step_live_text_prompt_line(line):
            continue
        match = re.search(r"\bchars=(\d+)", line)
        if match:
            total += int(match.group(1))
            continue
        total += len(_extract_prompt_text(line))
    return total


def _text_sha256(text):
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def _lesson_live_text_hashes(lines):
    hashes = []
    for line in lines:
        if not _is_lesson_step_live_text_prompt_line(line):
            continue
        match = re.search(r"\bsha256=([0-9a-fA-F]{64})\b", line)
        if match:
            hashes.append(match.group(1).lower())
    return hashes


def _hash_match_count(observed_hashes, expected_hashes):
    observed = Counter(hash_value.lower() for hash_value in observed_hashes)
    matches = 0
    for expected in expected_hashes:
        hash_value = str(expected).lower()
        if observed[hash_value] <= 0:
            continue
        observed[hash_value] -= 1
        matches += 1
    return matches


def _manifest_spoken_step_prompt(step):
    story_beat = step.get("storyBeat") if isinstance(step, dict) else None
    vocab = step.get("vocab") if isinstance(step, dict) else None
    uses_guided_ask = (
        isinstance(story_beat, dict)
        and (
            story_beat.get("waitForChild") is True
            or step.get("completionClass") == "interactive"
            or (
                isinstance(vocab, dict)
                and vocab.get("promptKind") == "guided-speaking"
            )
        )
    )
    if uses_guided_ask:
        ask = story_beat.get("ask")
        if isinstance(ask, str) and ask.strip():
            return ask.strip()
        return "What do you see?"
    value = step.get("prompt") if isinstance(step, dict) else None
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None

def _format_budget(value):
    try:
        return f"{float(value):g}"
    except (TypeError, ValueError):
        return str(value)


def _number_stats(values):
    converted = []
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            converted.append(number)
    values = converted
    if not values:
        return {"count": 0}
    ordered = sorted(values)
    return {
        "count": len(ordered),
        "min": ordered[0],
        "max": ordered[-1],
        "p50": percentile(ordered, 50),
        "p95": percentile(ordered, 95),
    }


def _first_audio_out_ms_stats(log_text):
    values = re.findall(
        r"Google Live turn_latency_ms=([\d.]+) phase=first_audio_out",
        log_text,
    )
    if not values:
        values = re.findall(r"first_audio_out_latency_ms=([\d.]+)", log_text)
    return _number_stats(values)


def _forbidden_report_fields(value, path=""):
    return forbidden_report_fields(value, path)


def validate_physical_candidate_report(
    report,
    *,
    expected_candidate_identity,
    reliability_report,
    candidate_soak_report,
    production_profile,
):
    """Validate released Task 7 physical budgets and its exact upstream bindings."""
    failures = []

    def mismatch(field):
        failures.append({"code": "PHYSICAL_CONTRACT_MISMATCH", "field": field})

    if not isinstance(report, dict):
        mismatch("report")
        return failures
    exact = {
        "passed": True,
        "candidateIdentity": dict(expected_candidate_identity),
        "missing": [],
        "fatal_hits": [],
        "malformedLatencyMarkers": 0,
        "live_identity_mismatches": 0,
        "model_echo_user_transcripts": 0,
        "receiveLoopBalance": 0,
        "maxReceiveLoopsActive": 1,
        "physical_ws_connected": True,
        "live_identity": True,
        "listen_start_interrupts": 0,
        "expected_user_transcripts": 10,
        "user_transcript_expected_matches": 10,
        "post_interrupt_user_transcript_expected_matches": 10,
    }
    for field, expected in exact.items():
        if report.get(field) != expected or type(report.get(field)) is not type(expected):
            mismatch(field)
    for field, minimum in (("input_audio_diag", 1), ("user_transcripts", 10)):
        if type(report.get(field)) is not int or report.get(field) < minimum:
            mismatch(field)
    exact_count_fields = {
        "audio_interrupts": 10,
        "aec_live_vad_forward": 10,
        "aec_interruption_chains": 10,
        "live_server_interruption": 10,
        "interrupt_tts_stops": 10,
        "interrupt_stop_chains": 10,
        "interrupt_user_chains": 10,
        "interrupt_relisten_chains": 10,
        "post_interrupt_user_transcripts": 10,
        "realtime_tts_stops": 10,
    }
    for field, expected in exact_count_fields.items():
        if type(report.get(field)) is not int or report.get(field) != expected:
            mismatch(field)
    minimum_fields = {
        "live_identity_first_audio_chains": 1,
        "output_relisten_chains": 1,
        "expected_post_lesson_transcripts": 1,
        "post_lesson_response_chains": 1,
        "lesson_prepare": 1,
        "lesson_start": 1,
        "lesson_steps": 1,
        "lesson_step_layers_complete": 1,
        "lesson_prompt_tts": 1,
        "lesson_prompt_after_render": 1,
        "lesson_firmware_rendered": 1,
        "lesson_step_layers_drawn_by_step": 1,
        "lesson_stop": 1,
        "lesson_completed": 1,
    }
    for field, minimum in minimum_fields.items():
        if type(report.get(field)) is not int or report.get(field) < minimum:
            mismatch(field)

    expected_profile = {
        "strictMarkersValidated": True,
        "lessonValidated": True,
        "postLessonValidated": True,
        "receiveLoopBalanceRequired": True,
        "sampleCounts": dict(_PHYSICAL_AUDIT_SAMPLE_COUNTS),
        "budgetsMs": {
            "firstAudioP50": 1200.0,
            "firstAudioP95": 1800.0,
            "interruptStopMax": 250.0,
            "physicalBargeinP95": 500.0,
            "serverOutputGapMax": 250.0,
        },
    }
    if production_profile != expected_profile:
        mismatch("productionProfile")

    metric_specs = (
        ("firstAudioLatencyMs", "p50", 1200.0),
        ("firstAudioLatencyMs", "p95", 1800.0),
        ("interruptStopLatencyMs", "max", 250.0),
        ("physicalBargeinLatencyMs", "p95", 500.0),
        ("serverOutputGapMs", "max", 250.0),
    )
    for field, statistic, limit in metric_specs:
        value = report.get(field)
        if (
            not isinstance(value, dict)
            or type(value.get("count")) is not int
            or value.get("count") != _PHYSICAL_AUDIT_SAMPLE_COUNTS[
                {
                    "firstAudioLatencyMs": "firstAudio",
                    "interruptStopLatencyMs": "interruptStop",
                    "physicalBargeinLatencyMs": "physicalBargein",
                    "serverOutputGapMs": "serverOutputGap",
                }[field]
            ]
            or any(
                isinstance(value.get(name), bool)
                or not isinstance(value.get(name), (int, float))
                or not math.isfinite(value.get(name))
                or value.get(name) < 0
                for name in ("min", "max", "p50", "p95")
            )
            or not value["min"] <= value["p50"] <= value["p95"] <= value["max"]
            or value.get(statistic) > limit
        ):
            mismatch(field)
    mirrored_metrics = {
        "first_audio_out_ms": "firstAudioLatencyMs",
        "interrupt_stop_latency_ms": "interruptStopLatencyMs",
    }
    for legacy, canonical in mirrored_metrics.items():
        if report.get(legacy) != report.get(canonical):
            mismatch(legacy)

    gaps = report.get("serverOutputGapMs")
    if not isinstance(gaps, dict):
        mismatch("serverOutputGapEvidence")
    else:
        allowed_boundaries = _INTENTIONAL_OUTPUT_GAP_BOUNDARIES
        boundaries = gaps.get("excludedByBoundary")
        durations = gaps.get("excludedDurationMsByBoundary")
        if (
            type(gaps.get("observed")) is not int
            or gaps.get("observed") < _PHYSICAL_AUDIT_SAMPLE_COUNTS["serverOutputGap"]
            or type(gaps.get("invalid")) is not int
            or gaps.get("invalid") != 0
            or type(gaps.get("excludedIntentional")) is not int
            or gaps.get("excludedIntentional") < 0
            or not isinstance(boundaries, dict)
            or not set(boundaries) <= allowed_boundaries
            or any(type(value) is not int or value < 0 for value in boundaries.values())
            or not isinstance(durations, dict)
            or set(durations) != set(boundaries)
            or any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
                for value in durations.values()
            )
            or sum(boundaries.values()) != gaps.get("excludedIntentional")
            or isinstance(gaps.get("excludedDurationMs"), bool)
            or not isinstance(gaps.get("excludedDurationMs"), (int, float))
            or not math.isfinite(gaps.get("excludedDurationMs"))
            or gaps.get("excludedDurationMs") < 0
            or abs(sum(durations.values()) - gaps.get("excludedDurationMs")) > 0.001
            or gaps.get("observed")
            != gaps.get("count") + gaps.get("excludedIntentional")
        ):
            mismatch("serverOutputGapEvidence")
        for nested in ("rawGapDurationMs", "unexplainedResidualMs"):
            value = gaps.get(nested)
            if (
                not isinstance(value, dict)
                or type(value.get("count")) is not int
                or any(
                    isinstance(value.get(name), bool)
                    or not isinstance(value.get(name), (int, float))
                    or not math.isfinite(value.get(name))
                    or value.get(name) < 0
                    for name in ("min", "max", "p50", "p95")
                )
                or not value["min"] <= value["p50"] <= value["p95"] <= value["max"]
                or (
                    nested == "rawGapDurationMs"
                    and value.get("count") != gaps.get("observed")
                )
                or (
                    nested == "unexplainedResidualMs"
                    and value.get("count") != gaps.get("count")
                )
                or (nested == "unexplainedResidualMs" and value.get("max") > 250.0)
            ):
                mismatch(nested)

    if not isinstance(reliability_report, dict) or validate_log_reliability_contract(
        reliability_report,
        expected_candidate_identity=expected_candidate_identity,
        expected_log_window=reliability_report.get("logWindow", {}),
        expected_evidence_scope=reliability_report.get("evidenceScope", {}),
    ):
        mismatch("logEvidence")
    if validate_candidate_soak_report(
        candidate_soak_report,
        expected_candidate_identity=expected_candidate_identity,
    ):
        mismatch("candidateSoakEvidence")
    if _forbidden_report_fields(report):
        mismatch("privacy")
    return failures


def _candidate_identity_valid(identity):
    required = {
        "gitSha",
        "imageDigest",
        "firmwareIdentity",
        "fixtureSha256",
        "configFingerprint",
    }
    return (
        isinstance(identity, dict)
        and set(identity) == required
        and all(isinstance(identity[key], str) and identity[key].strip() for key in required)
        and re.fullmatch(r"sha256:[0-9a-fA-F]{64}", identity["imageDigest"])
        is not None
        and re.fullmatch(r"[0-9a-fA-F]{64}", identity["fixtureSha256"])
        is not None
        and re.fullmatch(r"sha256:[0-9a-fA-F]{64}", identity["configFingerprint"])
        is not None
    )


def _finite_number(value, *, minimum=0.0):
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
        and value >= minimum
    )


def _candidate_window_evidence(log_text):
    starts = []
    for match in re.finditer(
        r"Google Live reliability_window_start window_id=(?P<window_id>[A-Za-z0-9._:-]+) "
        r"(?P<fields>.*?)candidate_identity=(?P<identity>\{.*\})$",
        log_text,
        re.MULTILINE,
    ):
        try:
            identity = json.loads(match.group("identity"))
        except json.JSONDecodeError:
            identity = None
        fields = dict(
            re.findall(
                r"\b(journey_id|connection_id|live_connection_id|"
                r"initial_live_connection_id|peer_identity_hash|server_start_utc)="
                r"([^\s]+)",
                match.group("fields"),
            )
        )
        starts.append(
            {
                "windowId": match.group("window_id"),
                "candidateIdentity": identity,
                "journeyId": fields.get("journey_id"),
                "connectionId": fields.get("connection_id"),
                "liveConnectionId": fields.get("live_connection_id"),
                "initialLiveConnectionId": fields.get(
                    "initial_live_connection_id"
                ),
                "peerIdentityHash": fields.get("peer_identity_hash"),
                "serverStartUtc": fields.get("server_start_utc"),
            }
        )
    ends = [
        {"windowId": match.group("window_id"), "serverEndUtc": match.group("end")}
        for match in re.finditer(
            r"Google Live reliability_window_end window_id=(?P<window_id>[A-Za-z0-9._:-]+)"
            r"(?: server_end_utc=(?P<end>\S+))?$",
            log_text,
            re.MULTILINE,
        )
    ]
    return starts, ends


def _output_gap_stats(log_text):
    gaps = {}
    boundaries = []
    invalid = 0
    gap_pattern = re.compile(
        r"Google Live server_output_gap gap_id=(?P<gap_id>[A-Za-z0-9._:-]+) "
        r"start_utc=(?P<start>\S+) end_utc=(?P<end>\S+) "
        r"duration_ms=(?P<duration>[\d.]+)$"
    )
    boundary_pattern = re.compile(
        r"Google Live server_output_gap_boundary "
        r"gap_id=(?P<gap_id>[A-Za-z0-9._:-]+) "
        r"type=(?P<boundary>[A-Za-z_]+) start_utc=(?P<start>\S+) "
        r"end_utc=(?P<end>\S+) duration_ms=(?P<duration>[\d.]+)$"
    )

    def parse_interval(match):
        try:
            start = datetime.fromisoformat(match.group("start"))
            end = datetime.fromisoformat(match.group("end"))
            duration = float(match.group("duration"))
        except (TypeError, ValueError):
            return None
        if (
            start.tzinfo is None
            or end.tzinfo is None
            or start.utcoffset() != timedelta(0)
            or end.utcoffset() != timedelta(0)
            or end < start
            or not math.isfinite(duration)
            or duration < 0
            or abs((end - start).total_seconds() * 1000 - duration) > 0.001
        ):
            return None
        return start, end, duration

    for line in log_text.splitlines():
        if "Google Live server_output_gap " in line:
            match = gap_pattern.search(line)
            interval = parse_interval(match) if match is not None else None
            if match is None or interval is None or match.group("gap_id") in gaps:
                invalid += 1
                continue
            gaps[match.group("gap_id")] = interval
        elif "Google Live server_output_gap_boundary " in line:
            match = boundary_pattern.search(line)
            interval = parse_interval(match) if match is not None else None
            if match is None or interval is None:
                invalid += 1
                continue
            boundaries.append(
                (match.group("gap_id"), match.group("boundary"), *interval)
            )

    by_gap = {}
    for gap_id, boundary_type, start, end, duration in boundaries:
        gap = gaps.get(gap_id)
        if gap is None or boundary_type not in _INTENTIONAL_OUTPUT_GAP_BOUNDARIES:
            invalid += 1
            continue
        gap_start, gap_end, gap_duration = gap
        if (
            duration <= 0
            or start < gap_start
            or end > gap_end
            or duration > gap_duration
        ):
            invalid += 1
            continue
        by_gap.setdefault(gap_id, []).append(
            (start, end, duration, boundary_type)
        )

    residuals = []
    excluded = Counter()
    excluded_durations = Counter()
    for gap_id, (_, _, gap_duration) in gaps.items():
        intervals = sorted(by_gap.get(gap_id, []), key=lambda item: item[0])
        previous_end = None
        explained = 0.0
        valid_intervals = []
        for start, end, duration, boundary_type in intervals:
            if previous_end is not None and start < previous_end:
                invalid += 1
                continue
            previous_end = end
            explained += duration
            valid_intervals.append((duration, boundary_type))
        if explained > gap_duration + 0.001:
            invalid += 1
            explained = 0.0
            valid_intervals = []
        for duration, boundary_type in valid_intervals:
            excluded[boundary_type] += 1
            excluded_durations[boundary_type] += duration
        residuals.append(max(0.0, round(gap_duration - explained, 3)))

    stats = _number_stats(residuals)
    stats["observed"] = len(gaps)
    stats["excludedIntentional"] = sum(excluded.values())
    stats["excludedByBoundary"] = dict(sorted(excluded.items()))
    stats["excludedDurationMs"] = round(sum(excluded_durations.values()), 3)
    stats["excludedDurationMsByBoundary"] = {
        key: round(value, 3) for key, value in sorted(excluded_durations.items())
    }
    stats["invalid"] = invalid
    stats["rawGapDurationMs"] = _number_stats(
        [duration for _, _, duration in gaps.values()]
    )
    stats["unexplainedResidualMs"] = {
        key: value for key, value in stats.items() if key in {"count", "min", "max", "p50", "p95"}
    }
    return stats


def _malformed_latency_marker_count(log_text):
    marker_patterns = (
        (
            lambda line: "phase=first_audio_out" in line
            and "Google Live turn_latency_ms=" in line,
            re.compile(r"Google Live turn_latency_ms=\d+(?:\.\d+)? phase=first_audio_out$"),
        ),
        (
            lambda line: "Google Live first_audio_out_latency_ms=" in line,
            re.compile(r"Google Live first_audio_out_latency_ms=\d+(?:\.\d+)?$"),
        ),
        (
            lambda line: "Google Live interruption_stop_latency_ms=" in line,
            re.compile(r"Google Live interruption_stop_latency_ms=\d+(?:\.\d+)?$"),
        ),
        (
            lambda line: "Google Live physical_bargein_latency_ms=" in line,
            re.compile(r"Google Live physical_bargein_latency_ms=\d+(?:\.\d+)?$"),
        ),
    )
    invalid = 0
    for line in log_text.splitlines():
        for is_marker, valid_pattern in marker_patterns:
            if is_marker(line) and valid_pattern.search(line) is None:
                invalid += 1
    return invalid


def _upstream_candidate_evidence(
    *,
    candidate_identity,
    reliability_report,
    candidate_soak_report,
    starts,
    ends,
):
    failures = []
    if not _candidate_identity_valid(candidate_identity):
        failures.append("candidate_identity_valid")

    if not isinstance(reliability_report, dict):
        failures.append("log_reliability_report_pass")
        reliability_report = {}
    elif (
        reliability_report.get("schemaVersion") != SCHEMA_VERSION
        or reliability_report.get("name") != "google_live_log_reliability"
        or reliability_report.get("status") != "PASS"
        or reliability_report.get("failures") != []
        or reliability_report.get("fatalHits") != []
        or type(reliability_report.get("receiveLoopBalance")) is not int
        or reliability_report.get("receiveLoopBalance") != 0
        or type(reliability_report.get("maxReceiveLoopsActive")) is not int
        or reliability_report.get("maxReceiveLoopsActive") != 1
        or type(reliability_report.get("staleAudioAfterReplacement")) is not int
        or reliability_report.get("staleAudioAfterReplacement") != 0
        or reliability_report.get("duplicateResponseIds") != []
        or reliability_report.get("unrecoveredTimeouts") != []
        or reliability_report.get("unreleasedLessonHandoffs") != []
        or not isinstance(reliability_report.get("replayCountsByReopen"), dict)
        or any(
            type(value) is not int or value not in {0, 1}
            for value in (reliability_report.get("replayCountsByReopen") or {}).values()
        )
        or not isinstance(reliability_report.get("correlations"), list)
        or not reliability_report.get("correlations")
        or any(
            not isinstance(item, dict) or item.get("status") != "PASS"
            for item in reliability_report.get("correlations") or []
        )
    ):
        failures.append("log_reliability_report_pass")
    if _forbidden_report_fields(reliability_report):
        failures.append("log_reliability_report_privacy_safe")

    if validate_candidate_soak_report(
        candidate_soak_report, expected_candidate_identity=candidate_identity
    ):
        failures.append("candidate_soak_report_pass")
    if not isinstance(candidate_soak_report, dict):
        candidate_soak_report = {}
    if _forbidden_report_fields(candidate_soak_report):
        failures.append("candidate_soak_report_privacy_safe")

    upstream_identities = (
        reliability_report.get("candidateIdentity"),
        candidate_soak_report.get("candidateIdentity"),
    )
    if any(identity != candidate_identity for identity in upstream_identities):
        failures.append("candidate_identity_match")

    expected_scope = starts[0] if len(starts) == 1 else {}
    scope = {
        key: expected_scope.get(key)
        for key in (
            "journeyId",
            "connectionId",
            "liveConnectionId",
            "initialLiveConnectionId",
            "peerIdentityHash",
            "serverStartUtc",
        )
    }
    expected_end = ends[0] if len(ends) == 1 else {}
    log_window = {
        "windowId": expected_scope.get("windowId"),
        "start": expected_scope.get("serverStartUtc"),
        "end": expected_end.get("serverEndUtc"),
    }
    shared_failures = validate_log_reliability_contract(
        reliability_report,
        expected_candidate_identity=candidate_identity,
        expected_log_window=log_window,
        expected_evidence_scope=scope,
    )
    if shared_failures:
        failures.append("log_reliability_report_pass")
    valid_scope = (
        len(starts) == 1
        and len(ends) == 1
        and ends[0]["windowId"] == starts[0]["windowId"]
        and starts[0]["candidateIdentity"] == candidate_identity
        and all(scope.values())
        and all(log_window.values())
    )
    if not valid_scope:
        failures.append("candidate_evidence_scope_match")
    return failures

def _ordered_marker_pair_count(lines, first_pattern, second_pattern):
    first = re.compile(first_pattern)
    second = re.compile(second_pattern)
    pending = 0
    pairs = 0
    for line in lines:
        if first.search(line):
            pending += 1
        if pending and second.search(line):
            pending -= 1
            pairs += 1
    return pairs

def _lesson_manifest_expectations(manifest):
    if not isinstance(manifest, dict):
        return {}
    steps = [step for step in manifest.get("steps", []) if isinstance(step, dict)]
    prompts = []
    for step in steps:
        spoken_prompt = _manifest_spoken_step_prompt(step)
        if spoken_prompt:
            prompts.append(spoken_prompt)
        for field in ("retryPrompt", "successPrompt"):
            value = step.get(field)
            if isinstance(value, str) and value.strip():
                prompts.append(value.strip())
    interactive_types = {"model", "listen", "repeat", "fillblank"}
    interactive_steps = 0
    for step in steps:
        completion_class = str(step.get("completionClass") or "").strip().lower()
        step_type = str(step.get("type") or "").strip().lower()
        if completion_class == "interactive" or (
            not completion_class and step_type in interactive_types
        ):
            interactive_steps += 1
    return {
        "expected_lesson_steps": len(steps),
        "expected_interactive_steps": interactive_steps,
        "min_lesson_live_text_chars": sum(len(prompt) for prompt in prompts),
        "lesson_prompt_hashes": [_text_sha256(prompt) for prompt in prompts],
    }


def _is_guided_question(text):
    normalized = text.strip().lower()
    if not normalized or "?" not in normalized:
        return False
    command_starts = (
        "say ",
        "repeat ",
        "read ",
        "tell ",
        "hãy ",
        "hay ",
        "nói ",
        "noi ",
        "lặp lại ",
        "lap lai ",
    )
    if normalized.startswith(command_starts):
        return False
    guided_markers = (
        "can you",
        "could you",
        "what",
        "which",
        "where",
        "how",
        "do you",
        "con có",
        "con co",
        "con thấy",
        "con thay",
        "con nghe",
        "mình cùng",
        "minh cung",
    )
    return any(marker in normalized for marker in guided_markers)

def _is_child_response_window_line(line):
    lowered = line.lower()
    if (
        "child response window opened" in lowered
        or "lesson_child_response_window opened" in lowered
        or "open_lesson_child_response_window" in lowered
    ):
        return True
    return "user_audio_window_open" in lowered and re.search(
        r"\breason\s*=\s*['\"]?lesson_child_response\b", lowered
    )


def _has_observable_child_input(line):
    if _has_contradictory_child_response_state(line):
        return False
    observable_patterns = (
        re.compile(r"(?i)\brecognized[_-]?text\s*[:=]\s*['\"]?([^,'\"}\s;]+)"),
        re.compile(r"(?i)\btranscript\s*[:=]\s*['\"]?([^,'\"}\s;]+)"),
        re.compile(r"(?i)\butterance\s*[:=]\s*['\"]?([^,'\"}\s;]+)"),
        re.compile(r"(?i)\bchild[_-]?response\s*[:=]\s*['\"]?([^,'\"}\s;]+)"),
        re.compile(r"(?i)\bchoice[_-]?id\s*[:=]\s*['\"]?([^,'\"}\s;]+)"),
        re.compile(r"(?i)\btap[_-]?target\s*[:=]\s*['\"]?([^,'\"}\s;]+)"),
    )
    placeholder_values = {
        "unknown",
        "unrecognized",
        "noise",
        "silence",
        "no_speech",
        "none",
        "null",
        "undefined",
    }
    for pattern in observable_patterns:
        match = pattern.search(line)
        if not match:
            continue
        value = match.group(1).strip("'\" .,!?[]<>").lower()
        if value and value not in placeholder_values:
            return True
    return False


def _has_contradictory_child_response_state(line):
    lowered = line.lower()
    false_markers = (
        "accepted=false",
        '"accepted":false',
        "accepted=0",
        '"accepted":0',
        "recognized=false",
        '"recognized":false',
        "recognized=0",
        '"recognized":0',
        "handled=false",
        '"handled":false',
        "handled=0",
        '"handled":0',
    )
    if any(marker in lowered for marker in false_markers):
        return True
    confidence_match = re.search(
        r"(?i)\b(?:confidence|asrConfidence|asr_confidence)\s*[:=]\s*['\"]?([^,'\"}\s;]+)",
        line,
    )
    if not confidence_match:
        return False
    try:
        return float(confidence_match.group(1)) <= 0
    except ValueError:
        return True


def _immediate_pronunciation_scoring_count(lines):
    lesson_context = (
        "lesson",
        "stepid",
        "step_id",
        "prompt",
        "tts",
        "child response",
        "child_response",
        "recognizedtext",
        "recognized_text",
        "transcript",
        "robot",
        "teebot",
    )
    count = 0
    for line in lines:
        lowered = line.lower()
        if not any(token in lowered for token in lesson_context):
            continue
        if any(pattern.search(line) for pattern in IMMEDIATE_PRONUNCIATION_SCORING_PATTERNS):
            count += 1
    return count


def _lesson_prompt_after_render_count(lines):
    current_step_id = None
    emitted_fallback = 0
    rendered = {}
    prompts = {}

    for index, line in enumerate(lines):
        if re.search(r"\bemit lesson_step\b", line):
            emitted_fallback += 1
            current_step_id = _extract_step_id(line) or f"__step_{emitted_fallback}"
        elif re.search(r"\blesson_step rendered\b", line):
            step_id = _extract_step_id(line) or current_step_id
            if step_id:
                rendered.setdefault(step_id, index)
        elif _is_lesson_step_prompt_line(line):
            step_id = _extract_step_id(line) or current_step_id
            if step_id:
                prompts.setdefault(step_id, index)

    return len(
        [
            step_id
            for step_id, prompt_index in prompts.items()
            if step_id in rendered and rendered[step_id] < prompt_index
        ]
    )


def _interactive_child_response_ordered_count(lines):
    current_step_id = None
    emitted_fallback = 0
    windows = {}
    responses = {}

    for index, line in enumerate(lines):
        lowered = line.lower()
        if re.search(r"\bemit lesson_step\b", line):
            emitted_fallback += 1
            current_step_id = _extract_step_id(line) or f"__step_{emitted_fallback}"
        elif _is_child_response_window_line(line):
            step_id = _extract_step_id(line) or current_step_id
            if step_id:
                windows.setdefault(step_id, index)
        elif (
            "interactive child response accepted" in lowered
            or "lesson_child_response accepted" in lowered
        ):
            step_id = _extract_step_id(line) or current_step_id
            if step_id:
                responses.setdefault(step_id, index)

    return len(
        [
            step_id
            for step_id, response_index in responses.items()
            if step_id in windows and windows[step_id] < response_index
        ]
    )


def _interactive_child_response_before_progress_count(lines):
    responses = {}
    progress = {}

    for index, line in enumerate(lines):
        lowered = line.lower()
        if (
            "interactive child response accepted" in lowered
            or "lesson_child_response accepted" in lowered
        ):
            step_id = _extract_step_id(line)
            if step_id:
                responses.setdefault(step_id, index)
        elif (
            "lesson_progress" in lowered
            and "step_completed" in lowered
        ):
            step_id = _extract_step_id(line)
            if step_id:
                progress.setdefault(step_id, index)

    return len(
        [
            step_id
            for step_id, progress_index in progress.items()
            if step_id in responses and responses[step_id] < progress_index
        ]
    )


def _interactive_child_response_after_prompt_count(lines):
    current_step_id = None
    emitted_fallback = 0
    prompts = {}
    responses = {}

    for index, line in enumerate(lines):
        lowered = line.lower()
        if re.search(r"\bemit lesson_step\b", line):
            emitted_fallback += 1
            current_step_id = _extract_step_id(line) or f"__step_{emitted_fallback}"
        elif _is_lesson_step_prompt_line(line):
            step_id = _extract_step_id(line) or current_step_id
            if step_id:
                prompts.setdefault(step_id, index)
        elif (
            "interactive child response accepted" in lowered
            or "lesson_child_response accepted" in lowered
        ):
            step_id = _extract_step_id(line) or current_step_id
            if step_id:
                responses.setdefault(step_id, index)

    return len(
        [
            step_id
            for step_id, response_index in responses.items()
            if step_id in prompts and prompts[step_id] < response_index
        ]
    )


def _interactive_child_response_window_after_prompt_count(lines):
    current_step_id = None
    emitted_fallback = 0
    prompts = {}
    windows = {}

    for index, line in enumerate(lines):
        lowered = line.lower()
        if re.search(r"\bemit lesson_step\b", line):
            emitted_fallback += 1
            current_step_id = _extract_step_id(line) or f"__step_{emitted_fallback}"
        elif _is_lesson_step_prompt_line(line):
            step_id = _extract_step_id(line) or current_step_id
            if step_id:
                prompts.setdefault(step_id, index)
        elif _is_child_response_window_line(line):
            step_id = _extract_step_id(line) or current_step_id
            if step_id:
                windows.setdefault(step_id, index)

    return len(
        [
            step_id
            for step_id, window_index in windows.items()
            if step_id in prompts and prompts[step_id] < window_index
        ]
    )


def _interactive_child_response_observed_count(lines):
    responses = {}

    for index, line in enumerate(lines):
        lowered = line.lower()
        if (
            "interactive child response accepted" in lowered
            or "lesson_child_response accepted" in lowered
        ):
            step_id = _extract_step_id(line)
            if step_id and _has_observable_child_input(line):
                responses.setdefault(step_id, index)

    return len(responses)


def _passive_child_response_activity_count(lines):
    interactive_types = {"model", "listen", "repeat", "fillblank"}
    passive_steps = set()
    activity_count = 0
    current_step_id = None

    for line in lines:
        if re.search(r"\bemit lesson_step\b", line):
            step_id = _extract_step_id(line)
            current_step_id = step_id or current_step_id
            step_type = (_extract_step_type(line) or "").lower()
            if step_id and step_type and step_type not in interactive_types:
                passive_steps.add(step_id)
            continue

        lowered = line.lower()
        if not (
            _is_child_response_window_line(line)
            or "interactive child response accepted" in lowered
            or "lesson_child_response accepted" in lowered
        ):
            continue
        step_id = _extract_step_id(line) or current_step_id
        if step_id in passive_steps:
            activity_count += 1

    return activity_count


def _lesson_step_layers_drawn_by_step_count(lines):
    emitted_step_ids = []
    layers_by_step = {}

    for line in lines:
        if re.search(r"\bemit lesson_step\b", line):
            step_id = _extract_step_id(line)
            if step_id:
                emitted_step_ids.append(step_id)
                layers_by_step.setdefault(step_id, set())
            continue

        step_id = _extract_step_id(line)
        if not step_id:
            continue
        if "lesson_step poster fetched+drawn from URL" in line:
            layers_by_step.setdefault(step_id, set()).add("backgroundScene")
        elif "lesson_step teaching object fetched+drawn from URL" in line:
            layers_by_step.setdefault(step_id, set()).add("teachingObject")
        elif "lesson_step robot overlay fetched+drawn from URL" in line:
            layers_by_step.setdefault(step_id, set()).add("robotOverlay")

    return len(
        [
            step_id
            for step_id in set(emitted_step_ids)
            if {"backgroundScene", "teachingObject", "robotOverlay"}.issubset(
                layers_by_step.get(step_id, set())
            )
        ]
    )


def _interactive_guided_prompt_count(lines):
    current_step_id = None
    emitted_fallback = 0
    prompts = {}
    responses = {}

    for index, line in enumerate(lines):
        lowered = line.lower()
        if re.search(r"\bemit lesson_step\b", line):
            emitted_fallback += 1
            current_step_id = _extract_step_id(line) or f"__step_{emitted_fallback}"
        elif _is_lesson_step_prompt_line(line):
            step_id = _extract_step_id(line) or current_step_id
            if step_id:
                prompts.setdefault(step_id, (index, _extract_prompt_text(line)))
        elif (
            "interactive child response accepted" in lowered
            or "lesson_child_response accepted" in lowered
        ):
            step_id = _extract_step_id(line) or current_step_id
            if step_id:
                responses.setdefault(step_id, index)

    return len(
        [
            step_id
            for step_id, response_index in responses.items()
            if step_id in prompts
            and prompts[step_id][0] < response_index
            and _is_guided_question(prompts[step_id][1])
        ]
    )


def _audit_lesson_flow(
    log_text,
    expected_lesson_steps,
    expected_interactive_steps=0,
    require_lesson_live_text=False,
    min_lesson_live_text_chars=None,
    expected_lesson_live_text_hashes=None,
):
    expected_lesson_live_text_hashes = expected_lesson_live_text_hashes or []
    lines = log_text.splitlines()
    lesson_prepare = len(re.findall(r"\blesson_prepare\b", log_text))
    lesson_start = len(re.findall(r"\blesson_start\b", log_text))
    lesson_stop = len(re.findall(r"\blesson_stop\b", log_text))
    lesson_completed = len(re.findall(r"lesson_completed|stepsCompleted=\d+", log_text))
    lesson_step_lines = [
        line for line in lines if re.search(r"\blesson_step\b", line)
    ]
    emitted_steps = len([line for line in lesson_step_lines if re.search(r"\bemit lesson_step\b", line)])
    rendered_steps = len([line for line in lesson_step_lines if re.search(r"\blesson_step rendered\b", line)])
    lesson_steps = emitted_steps if emitted_steps else rendered_steps
    layer_complete = len(
        [
            line
            for line in lesson_step_lines
            if "backgroundScene=1" in line
            and "teachingObject=1" in line
            and "robotOverlay=1" in line
        ]
    )
    lesson_prompt_tts = sum(1 for line in lines if _is_lesson_step_prompt_line(line))
    lesson_prompt_live_text = sum(
        1 for line in lines if _is_lesson_step_live_text_prompt_line(line)
    )
    lesson_prompt_live_text_chars = _lesson_live_text_chars(lines)
    lesson_prompt_live_text_hashes = _lesson_live_text_hashes(lines)
    lesson_prompt_live_text_hash_matches = _hash_match_count(
        lesson_prompt_live_text_hashes,
        expected_lesson_live_text_hashes,
    )
    lesson_prompt_live_text_hash_order_matches = _hash_order_match_count(
        lesson_prompt_live_text_hashes,
        expected_lesson_live_text_hashes,
    )
    lesson_prompt_live_text_unexpected_hashes = _unexpected_hash_count(
        lesson_prompt_live_text_hashes,
        expected_lesson_live_text_hashes,
    )
    firmware_rendered = len(re.findall(r"lesson_step rendered .*degraded=0", log_text))
    poster_drawn = len(re.findall(r"lesson_step poster fetched\+drawn from URL", log_text))
    object_drawn = len(re.findall(r"lesson_step teaching object fetched\+drawn from URL", log_text))
    overlay_drawn = len(re.findall(r"lesson_step robot overlay fetched\+drawn from URL", log_text))
    layers_drawn_by_step = _lesson_step_layers_drawn_by_step_count(lines)
    prompt_after_render = _lesson_prompt_after_render_count(lines)
    child_response_windows = sum(1 for line in lines if _is_child_response_window_line(line))
    child_responses = len(
        re.findall(
            r"interactive child response accepted|lesson_child_response accepted",
            log_text,
            flags=re.IGNORECASE,
        )
    )
    child_responses_observed = _interactive_child_response_observed_count(lines)
    child_response_ordered = _interactive_child_response_ordered_count(lines)
    child_response_after_prompt = _interactive_child_response_after_prompt_count(lines)
    child_response_window_after_prompt = _interactive_child_response_window_after_prompt_count(lines)
    child_response_before_progress = _interactive_child_response_before_progress_count(lines)
    interactive_guided_prompts = _interactive_guided_prompt_count(lines)
    immediate_pronunciation_scoring = _immediate_pronunciation_scoring_count(lines)
    passive_child_response_activity = _passive_child_response_activity_count(lines)

    missing = []
    if lesson_prepare < 1:
        missing.append("lesson_prepare")
    if lesson_start < 1:
        missing.append("lesson_start")
    if lesson_steps < expected_lesson_steps:
        missing.append(f"lesson_steps>={expected_lesson_steps}")
    if layer_complete < expected_lesson_steps:
        missing.append("lesson_step_layers_complete")
    if lesson_prompt_tts < expected_lesson_steps:
        missing.append(f"lesson_prompt_tts>={expected_lesson_steps}")
    lesson_prompt_local_tts = sum(
        1 for line in lines if _is_lesson_local_tts_prompt_line(line)
    )
    if require_lesson_live_text and lesson_prompt_local_tts:
        missing.append("no_lesson_local_tts")
    if require_lesson_live_text and lesson_prompt_live_text < expected_lesson_steps:
        missing.append(f"lesson_prompt_live_text>={expected_lesson_steps}")
    if (
        min_lesson_live_text_chars is not None
        and lesson_prompt_live_text_chars < int(min_lesson_live_text_chars)
    ):
        missing.append(
            f"lesson_prompt_live_text_chars>={int(min_lesson_live_text_chars)}"
        )
    if (
        expected_lesson_live_text_hashes
        and lesson_prompt_live_text_hash_matches < len(expected_lesson_live_text_hashes)
    ):
        missing.append(
            f"lesson_prompt_live_text_hashes>={len(expected_lesson_live_text_hashes)}"
        )
    if expected_lesson_live_text_hashes and lesson_prompt_live_text_unexpected_hashes:
        missing.append("no_unexpected_lesson_live_text_hashes")
    if (
        expected_lesson_live_text_hashes
        and lesson_prompt_live_text_hash_order_matches < len(expected_lesson_live_text_hashes)
    ):
        missing.append("lesson_prompt_live_text_hash_order")
    if prompt_after_render < expected_lesson_steps:
        missing.append("lesson_prompt_after_render")
    if firmware_rendered < expected_lesson_steps:
        missing.append(f"lesson_firmware_rendered>={expected_lesson_steps}")
    if poster_drawn < expected_lesson_steps:
        missing.append(f"lesson_posters_drawn>={expected_lesson_steps}")
    if object_drawn < expected_lesson_steps:
        missing.append(f"lesson_objects_drawn>={expected_lesson_steps}")
    if overlay_drawn < expected_lesson_steps:
        missing.append(f"lesson_robot_overlays_drawn>={expected_lesson_steps}")
    if layers_drawn_by_step < expected_lesson_steps:
        missing.append("lesson_step_layers_drawn_by_step")
    if expected_interactive_steps > 0 and child_response_windows < expected_interactive_steps:
        missing.append(f"interactive_child_response_windows>={expected_interactive_steps}")
    if expected_interactive_steps > 0 and child_responses < expected_interactive_steps:
        missing.append(f"interactive_child_responses>={expected_interactive_steps}")
    if expected_interactive_steps > 0 and child_responses_observed < expected_interactive_steps:
        missing.append(f"interactive_child_responses_observed>={expected_interactive_steps}")
    if expected_interactive_steps > 0 and child_response_ordered < expected_interactive_steps:
        missing.append(f"interactive_child_response_ordered>={expected_interactive_steps}")
    if expected_interactive_steps > 0 and child_response_after_prompt < expected_interactive_steps:
        missing.append(f"interactive_child_response_after_prompt>={expected_interactive_steps}")
    if expected_interactive_steps > 0 and child_response_window_after_prompt < expected_interactive_steps:
        missing.append(f"interactive_child_response_window_after_prompt>={expected_interactive_steps}")
    if expected_interactive_steps > 0 and interactive_guided_prompts < expected_interactive_steps:
        missing.append(f"interactive_guided_prompts>={expected_interactive_steps}")
    if expected_interactive_steps > 0 and child_response_before_progress < expected_interactive_steps:
        missing.append(f"interactive_child_response_before_progress>={expected_interactive_steps}")
    if immediate_pronunciation_scoring:
        missing.append("no_immediate_pronunciation_scoring")
    if passive_child_response_activity:
        missing.append("no_passive_child_response_activity")
    if lesson_stop < 1:
        missing.append("lesson_stop")
    if lesson_completed < 1:
        missing.append("lesson_completed")

    return {
        "lesson_prepare": lesson_prepare,
        "lesson_start": lesson_start,
        "lesson_steps": lesson_steps,
        "lesson_step_layers_complete": layer_complete,
        "lesson_prompt_tts": lesson_prompt_tts,
        "lesson_prompt_local_tts": lesson_prompt_local_tts,
        "lesson_prompt_live_text": lesson_prompt_live_text,
        "lesson_prompt_live_text_chars": lesson_prompt_live_text_chars,
        "lesson_prompt_live_text_hashes": len(lesson_prompt_live_text_hashes),
        "lesson_prompt_live_text_hash_matches": lesson_prompt_live_text_hash_matches,
        "lesson_prompt_live_text_hash_order_matches": (
            lesson_prompt_live_text_hash_order_matches
        ),
        "lesson_prompt_live_text_unexpected_hashes": (
            lesson_prompt_live_text_unexpected_hashes
        ),
        "lesson_prompt_after_render": prompt_after_render,
        "lesson_firmware_rendered": firmware_rendered,
        "lesson_posters_drawn": poster_drawn,
        "lesson_objects_drawn": object_drawn,
        "lesson_robot_overlays_drawn": overlay_drawn,
        "lesson_step_layers_drawn_by_step": layers_drawn_by_step,
        "interactive_child_response_windows": child_response_windows,
        "interactive_child_responses": child_responses,
        "interactive_child_responses_observed": child_responses_observed,
        "interactive_child_response_ordered": child_response_ordered,
        "interactive_child_response_after_prompt": child_response_after_prompt,
        "interactive_child_response_window_after_prompt": child_response_window_after_prompt,
        "interactive_guided_prompts": interactive_guided_prompts,
        "interactive_child_response_before_progress": child_response_before_progress,
        "immediate_pronunciation_scoring": immediate_pronunciation_scoring,
        "passive_child_response_activity": passive_child_response_activity,
        "lesson_stop": lesson_stop,
        "lesson_completed": lesson_completed,
        "missing": missing,
    }

def audit_log(
    log_text,
    device_id,
    client_id,
    min_interrupts=10,
    min_audio_interrupts=None,
    server_ip=None,
    require_lesson=False,
    expected_lesson_steps=9,
    expected_interactive_steps=0,
    require_aec_live_vad_forward=False,
    min_aec_live_vad_forward=None,
    min_aec_interruption_chains=None,
    require_live_server_interruption=False,
    min_live_server_interruptions=None,
    min_interrupt_tts_stops=None,
    min_interrupt_stop_chains=None,
    min_interrupt_user_chains=None,
    min_interrupt_relisten_chains=None,
    min_post_interrupt_user_transcripts=None,
    min_realtime_tts_stops=None,
    min_output_relisten_chains=None,
    min_output_moderation_blocks=None,
    min_safe_deflection_live_text=None,
    max_aec_live_vad_forward=None,
    max_listen_start_interrupts=None,
    max_first_audio_ms=None,
    max_first_audio_p50_ms=None,
    max_first_audio_p95_ms=None,
    max_interrupt_stop_latency_ms=None,
    max_physical_bargein_p95_ms=None,
    max_server_output_gap_ms=None,
    min_first_audio_samples=None,
    min_interrupt_stop_samples=None,
    min_physical_bargein_samples=None,
    min_server_output_gap_samples=None,
    candidate_identity=None,
    reliability_report=None,
    candidate_soak_report=None,
    require_receive_loop_balance=False,
    require_post_lesson_response=False,
    require_lesson_live_text=False,
    min_lesson_live_text_chars=None,
    lesson_manifest=None,
    expected_user_transcripts=None,
    expected_post_lesson_transcripts=None,
    allowed_fatal_patterns=None,
):
    expected_user_transcripts = [
        str(text).strip()
        for text in (expected_user_transcripts or [])
        if str(text).strip()
    ]
    expected_post_lesson_transcripts = [
        str(text).strip()
        for text in (expected_post_lesson_transcripts or [])
        if str(text).strip()
    ]
    if min_audio_interrupts is None:
        min_audio_interrupts = min_interrupts
    manifest_expectations = _lesson_manifest_expectations(lesson_manifest)
    if manifest_expectations.get("expected_lesson_steps"):
        expected_lesson_steps = manifest_expectations["expected_lesson_steps"]
    if (
        manifest_expectations.get("expected_interactive_steps")
        and expected_interactive_steps <= 0
    ):
        expected_interactive_steps = manifest_expectations["expected_interactive_steps"]
    if (
        min_lesson_live_text_chars is None
        and manifest_expectations.get("min_lesson_live_text_chars")
    ):
        min_lesson_live_text_chars = manifest_expectations[
            "min_lesson_live_text_chars"
        ]

    physical_ws_connected = _has_physical_ws_connection(
        log_text,
        device_id,
        client_id,
        server_ip=server_ip,
    )
    evidence_log_text = _target_physical_ws_log_text(
        log_text,
        device_id,
        client_id,
        server_ip=server_ip,
    )

    audio_interrupts = len(
        re.findall(r"user_interrupted reason=audio_input", evidence_log_text)
    )
    input_audio_diag = len(re.findall(r"input_audio_diag .*rms=", evidence_log_text))
    first_audio_out_ms = _first_audio_out_ms_stats(evidence_log_text)
    aec_live_vad_forward = len(
        re.findall(
            r"Google Live aec_live_vad_forward reason=robot_speaking",
            evidence_log_text,
        )
    )
    live_server_interruption = len(
        re.findall(r"Google Live interruption output_age_ms=\d", evidence_log_text)
    )
    listen_start_interrupts = len(
        re.findall(
            r"(?:Google Live user_interrupted reason=listen_start|"
            r"interrupt_started reason=listen_start)",
            evidence_log_text,
        )
    )
    interrupt_tts_stops = len(
        re.findall(
            r"(?:Google Live )?(?:tts_stop_sent|tts_state_stop_sent)"
            r" reason=interrupt\b",
            evidence_log_text,
        )
    )
    interrupt_stop_latency_ms = _number_stats(
        re.findall(
            r"Google Live interruption_stop_latency_ms=([\d.]+)",
            evidence_log_text,
        )
    )
    physical_bargein_latency_ms = _number_stats(
        re.findall(
            r"Google Live physical_bargein_latency_ms=([\d.]+)",
            evidence_log_text,
        )
    )
    server_output_gap_ms = _output_gap_stats(evidence_log_text)
    malformed_latency_markers = _malformed_latency_marker_count(evidence_log_text)
    candidate_window_starts, candidate_window_ends = _candidate_window_evidence(
        evidence_log_text
    )
    realtime_tts_stops = len(
        re.findall(
            r"(?:Google Live )?tts_stop_sent "
            r"continue_listening=true listen_mode=realtime",
            evidence_log_text,
        )
    )
    output_moderation_blocks = len(
        re.findall(
            r"Google Live output_moderation_blocked source=model_output",
            evidence_log_text,
        )
    )
    safe_deflection_live_text = len(
        re.findall(
            r"Google Live safe_deflection sent via live text chars=\d+",
            evidence_log_text,
        )
    )
    lines = evidence_log_text.splitlines()
    aec_interruption_chains = _ordered_marker_pair_count(
        lines,
        r"Google Live aec_live_vad_forward reason=robot_speaking",
        r"Google Live interruption output_age_ms=\d",
    )
    interrupt_stop_chains = _ordered_marker_pair_count(
        lines,
        r"Google Live interruption output_age_ms=\d",
        r"(?:Google Live )?(?:tts_stop_sent|tts_state_stop_sent) reason=interrupt",
    )
    interrupt_user_chains = _ordered_marker_pair_count(
        lines,
        r"(?:Google Live )?(?:tts_stop_sent|tts_state_stop_sent) reason=interrupt",
        r"Google Live user_interrupted reason=audio_input",
    )
    interrupt_relisten_chains = _ordered_marker_pair_count(
        lines,
        r"Google Live interruption output_age_ms=\d",
        r"(?:Google Live )?(?:tts_stop_sent|tts_state_stop_sent) "
        r"reason=interrupt .*continue_listening=true .*listen_mode=realtime",
    )
    post_interrupt_user_transcripts = _ordered_marker_pair_count(
        lines,
        r"Google Live interruption output_age_ms=\d",
        r"Google Live transcript source=user chars=\d+",
    )
    live_identity = _has_production_live_identity(lines)
    live_identity_first_audio_chains = _ordered_marker_pair_count(
        lines,
        r"Google Live session identity .*model=gemini-3\.1-flash-live-preview .*voice=Kore .*language=vi-VN",
        FIRST_AUDIO_OUT_MARKER_PATTERN,
    )
    output_relisten_chains = _ordered_marker_pair_count(
        lines,
        FIRST_AUDIO_OUT_MARKER_PATTERN,
        r"(?:Google Live )?tts_stop_sent "
        r"continue_listening=true listen_mode=realtime",
    )
    user_transcript_expected_matches = _expected_user_transcript_match_count(
        _user_transcript_texts(lines),
        expected_user_transcripts,
    )
    post_lesson_response_chains = _post_lesson_response_chain_count(
        lines,
        expected_post_lesson_transcripts,
    )
    post_interrupt_user_transcript_expected_matches = _expected_user_transcript_match_count(
        _user_transcript_texts_after_interruption(lines),
        expected_user_transcripts,
    )
    user_transcripts = len(
        re.findall(r"Google Live transcript source=user chars=\d+", evidence_log_text)
    )
    transcript_proof_markers = []
    malformed_transcript_proof_markers = 0
    if candidate_identity is not None:
        evidence_scope = (
            reliability_report.get("evidenceScope", {})
            if isinstance(reliability_report, dict)
            else {}
        )
        transcript_proof_markers, malformed_transcript_proof_markers = (
            _transcript_proof_markers(
                lines,
                journey_id=str(evidence_scope.get("journeyId") or ""),
            )
        )
    allowed_fatal_patterns = set(allowed_fatal_patterns or [])
    fatal_hits = [
        pattern
        for pattern in FATAL_PATTERNS
        if pattern in evidence_log_text and pattern not in allowed_fatal_patterns
    ]
    fatal_hits.extend(
        label
        for label, pattern in FATAL_REGEX_PATTERNS
        if pattern.search(evidence_log_text)
    )
    live_identity_mismatches = _live_identity_mismatch_count(lines)
    if live_identity_mismatches:
        fatal_hits.append("live_identity_mismatch")
    model_echo_user_transcripts = _model_echo_user_transcript_count(lines)
    if model_echo_user_transcripts:
        fatal_hits.append("model_echo_user_transcript")

    missing = []
    if candidate_identity is not None or reliability_report is not None or candidate_soak_report is not None:
        missing.extend(
            _upstream_candidate_evidence(
                candidate_identity=candidate_identity,
                reliability_report=reliability_report,
                candidate_soak_report=candidate_soak_report,
                starts=candidate_window_starts,
                ends=candidate_window_ends,
            )
        )
    if candidate_identity is not None:
        required_transcript_proofs = [
            *({"slot": slot, "phase": "interrupt"} for slot in range(1, 11)),
            {"slot": 11, "phase": "post_lesson"},
        ]
        observed_transcript_proofs = [
            {"slot": marker["slot"], "phase": marker["phase"]}
            for marker in transcript_proof_markers
        ]
        if (
            observed_transcript_proofs != required_transcript_proofs
            or malformed_transcript_proof_markers
        ):
            missing.append("transcript_proof_exact_slots")
    if not physical_ws_connected:
        missing.append("physical_ws_connected")
    if input_audio_diag < 1:
        missing.append("input_audio_diag")
    if user_transcripts < 1:
        missing.append("user_transcript")
    if min_post_interrupt_user_transcripts is not None and not live_identity:
        missing.append("live_identity")
    if (
        min_post_interrupt_user_transcripts is not None
        and live_identity_first_audio_chains < 1
    ):
        missing.append("live_identity_before_first_audio")
    if (
        expected_user_transcripts
        and user_transcript_expected_matches < len(expected_user_transcripts)
    ):
        missing.append(
            f"user_transcript_expected_match>={len(expected_user_transcripts)}"
        )
    min_audio_interrupts = int(min_audio_interrupts)
    if audio_interrupts < min_audio_interrupts:
        missing.append(f"audio_interrupts>={min_audio_interrupts}")
    if max_first_audio_ms is not None:
        if first_audio_out_ms["count"] < 1:
            missing.append("first_audio_out_ms")
        elif first_audio_out_ms["max"] > float(max_first_audio_ms):
            missing.append(f"first_audio_out_ms<={_format_budget(max_first_audio_ms)}")
    if min_first_audio_samples is not None and first_audio_out_ms["count"] != int(
        min_first_audio_samples
    ):
        missing.append(f"first_audio_samples={int(min_first_audio_samples)}")
    if max_first_audio_p50_ms is not None and (
        first_audio_out_ms.get("p50") is None
        or first_audio_out_ms["p50"] > float(max_first_audio_p50_ms)
    ):
        missing.append(
            f"first_audio_p50_ms<={_format_budget(max_first_audio_p50_ms)}"
        )
    if max_first_audio_p95_ms is not None and (
        first_audio_out_ms.get("p95") is None
        or first_audio_out_ms["p95"] > float(max_first_audio_p95_ms)
    ):
        missing.append(
            f"first_audio_p95_ms<={_format_budget(max_first_audio_p95_ms)}"
        )
    if max_interrupt_stop_latency_ms is not None:
        expected_count = int(
            min_interrupt_stop_samples
            if min_interrupt_stop_samples is not None
            else min_interrupt_tts_stops or min_interrupts
        )
        exact_interrupt_samples = min_interrupt_stop_samples is not None
        count_mismatch = (
            interrupt_stop_latency_ms["count"] != expected_count
            if exact_interrupt_samples
            else interrupt_stop_latency_ms["count"] < expected_count
        )
        if count_mismatch:
            operator = "=" if exact_interrupt_samples else ">="
            missing.append(f"interrupt_stop_latency_ms{operator}{expected_count}")
        elif interrupt_stop_latency_ms["max"] > float(max_interrupt_stop_latency_ms):
            missing.append(
                "interrupt_stop_latency_ms"
                f"<={_format_budget(max_interrupt_stop_latency_ms)}"
            )
    if min_physical_bargein_samples is not None and physical_bargein_latency_ms[
        "count"
    ] != int(min_physical_bargein_samples):
        missing.append(
            f"physical_bargein_samples={int(min_physical_bargein_samples)}"
        )
    if max_physical_bargein_p95_ms is not None and (
        physical_bargein_latency_ms.get("p95") is None
        or physical_bargein_latency_ms["p95"] > float(max_physical_bargein_p95_ms)
    ):
        missing.append(
            "physical_bargein_p95_ms"
            f"<={_format_budget(max_physical_bargein_p95_ms)}"
        )
    if min_server_output_gap_samples is not None and (
        server_output_gap_ms["observed"] != int(min_server_output_gap_samples)
        or server_output_gap_ms["count"] < 1
    ):
        missing.append(
            f"server_output_gap_samples={int(min_server_output_gap_samples)}"
        )
    if server_output_gap_ms["invalid"]:
        missing.append("server_output_gap_boundaries_valid")
    if malformed_latency_markers:
        missing.append("latency_evidence_valid")
    if max_server_output_gap_ms is not None and (
        server_output_gap_ms.get("max") is None
        or server_output_gap_ms["max"] > float(max_server_output_gap_ms)
    ):
        missing.append(
            f"server_output_gap_ms<={_format_budget(max_server_output_gap_ms)}"
        )
    receive_loop_balance = (
        reliability_report.get("receiveLoopBalance")
        if isinstance(reliability_report, dict)
        else None
    )
    max_receive_loops_active = (
        reliability_report.get("maxReceiveLoopsActive")
        if isinstance(reliability_report, dict)
        else None
    )
    if require_receive_loop_balance:
        if type(receive_loop_balance) is not int or receive_loop_balance != 0:
            missing.append("receive_loop_balance=0")
        if type(max_receive_loops_active) is not int or max_receive_loops_active != 1:
            missing.append("max_receive_loops_active=1")
    if require_aec_live_vad_forward and aec_live_vad_forward < 1:
        missing.append("aec_live_vad_forward")
    if min_aec_live_vad_forward is not None:
        min_aec_live_vad_forward = int(min_aec_live_vad_forward)
        if aec_live_vad_forward < min_aec_live_vad_forward:
            missing.append(f"aec_live_vad_forward>={min_aec_live_vad_forward}")
    if max_aec_live_vad_forward is not None:
        max_aec_live_vad_forward = int(max_aec_live_vad_forward)
        if aec_live_vad_forward > max_aec_live_vad_forward:
            missing.append(f"aec_live_vad_forward<={max_aec_live_vad_forward}")
    if max_listen_start_interrupts is not None:
        max_listen_start_interrupts = int(max_listen_start_interrupts)
        if listen_start_interrupts > max_listen_start_interrupts:
            missing.append(f"listen_start_interrupts<={max_listen_start_interrupts}")
    if min_aec_interruption_chains is not None:
        min_aec_interruption_chains = int(min_aec_interruption_chains)
        if aec_interruption_chains < min_aec_interruption_chains:
            missing.append(f"aec_interruption_chains>={min_aec_interruption_chains}")
    if require_live_server_interruption and live_server_interruption < 1:
        missing.append("live_server_interruption")
    if min_live_server_interruptions is not None:
        min_live_server_interruptions = int(min_live_server_interruptions)
        if live_server_interruption < min_live_server_interruptions:
            missing.append(
                f"live_server_interruptions>={min_live_server_interruptions}"
            )
    if min_interrupt_tts_stops is not None:
        min_interrupt_tts_stops = int(min_interrupt_tts_stops)
        if interrupt_tts_stops < min_interrupt_tts_stops:
            missing.append(f"interrupt_tts_stops>={min_interrupt_tts_stops}")
    if min_interrupt_stop_chains is not None:
        min_interrupt_stop_chains = int(min_interrupt_stop_chains)
        if interrupt_stop_chains < min_interrupt_stop_chains:
            missing.append(f"interrupt_stop_chains>={min_interrupt_stop_chains}")
    if min_interrupt_user_chains is not None:
        min_interrupt_user_chains = int(min_interrupt_user_chains)
        if interrupt_user_chains < min_interrupt_user_chains:
            missing.append(f"interrupt_user_chains>={min_interrupt_user_chains}")
    if min_interrupt_relisten_chains is not None:
        min_interrupt_relisten_chains = int(min_interrupt_relisten_chains)
        if interrupt_relisten_chains < min_interrupt_relisten_chains:
            missing.append(
                f"interrupt_relisten_chains>={min_interrupt_relisten_chains}"
            )
    if min_post_interrupt_user_transcripts is not None:
        min_post_interrupt_user_transcripts = int(min_post_interrupt_user_transcripts)
        if post_interrupt_user_transcripts < min_post_interrupt_user_transcripts:
            missing.append(
                "post_interrupt_user_transcripts"
                f">={min_post_interrupt_user_transcripts}"
            )
        if (
            expected_user_transcripts
            and post_interrupt_user_transcript_expected_matches
            < len(expected_user_transcripts)
        ):
            missing.append(
                "post_interrupt_user_transcript_expected_match"
                f">={len(expected_user_transcripts)}"
            )
    if min_realtime_tts_stops is not None:
        min_realtime_tts_stops = int(min_realtime_tts_stops)
        if realtime_tts_stops < min_realtime_tts_stops:
            missing.append(f"realtime_tts_stops>={min_realtime_tts_stops}")
    if min_output_relisten_chains is not None:
        min_output_relisten_chains = int(min_output_relisten_chains)
        if output_relisten_chains < min_output_relisten_chains:
            missing.append(f"output_relisten_chains>={min_output_relisten_chains}")
    if min_output_moderation_blocks is not None:
        min_output_moderation_blocks = int(min_output_moderation_blocks)
        if output_moderation_blocks < min_output_moderation_blocks:
            missing.append(
                f"output_moderation_blocks>={min_output_moderation_blocks}"
            )
    if min_safe_deflection_live_text is not None:
        min_safe_deflection_live_text = int(min_safe_deflection_live_text)
        if safe_deflection_live_text < min_safe_deflection_live_text:
            missing.append(
                f"safe_deflection_live_text>={min_safe_deflection_live_text}"
            )
    if fatal_hits:
        missing.append("no_fatal_patterns")
    if (
        require_post_lesson_response or expected_post_lesson_transcripts
    ) and post_lesson_response_chains < 1:
        missing.append("post_lesson_response")

    lesson = None
    if require_lesson:
        lesson = _audit_lesson_flow(
            evidence_log_text,
            expected_lesson_steps,
            expected_interactive_steps,
            require_lesson_live_text=require_lesson_live_text,
            min_lesson_live_text_chars=min_lesson_live_text_chars,
            expected_lesson_live_text_hashes=manifest_expectations.get(
                "lesson_prompt_hashes"
            ),
        )
        missing.extend(lesson["missing"])

    result = {
        "passed": not missing,
        "physical_ws_connected": physical_ws_connected,
        "input_audio_diag": input_audio_diag,
        "first_audio_out_ms": first_audio_out_ms,
        "firstAudioLatencyMs": first_audio_out_ms,
        "aec_live_vad_forward": aec_live_vad_forward,
        "listen_start_interrupts": listen_start_interrupts,
        "aec_interruption_chains": aec_interruption_chains,
        "live_server_interruption": live_server_interruption,
        "interrupt_tts_stops": interrupt_tts_stops,
        "interrupt_stop_latency_ms": interrupt_stop_latency_ms,
        "interruptStopLatencyMs": interrupt_stop_latency_ms,
        "physicalBargeinLatencyMs": physical_bargein_latency_ms,
        "serverOutputGapMs": server_output_gap_ms,
        "malformedLatencyMarkers": malformed_latency_markers,
        "receiveLoopBalance": receive_loop_balance,
        "maxReceiveLoopsActive": max_receive_loops_active,
        "candidateIdentity": candidate_identity,
        "interrupt_stop_chains": interrupt_stop_chains,
        "interrupt_user_chains": interrupt_user_chains,
        "interrupt_relisten_chains": interrupt_relisten_chains,
        "post_interrupt_user_transcripts": post_interrupt_user_transcripts,
        "live_identity": live_identity,
        "live_identity_mismatches": live_identity_mismatches,
        "live_identity_first_audio_chains": live_identity_first_audio_chains,
        "realtime_tts_stops": realtime_tts_stops,
        "output_relisten_chains": output_relisten_chains,
        "output_moderation_blocks": output_moderation_blocks,
        "safe_deflection_live_text": safe_deflection_live_text,
        "user_transcripts": user_transcripts,
        "expected_user_transcripts": len(expected_user_transcripts),
        "user_transcript_expected_matches": user_transcript_expected_matches,
        "expected_post_lesson_transcripts": len(expected_post_lesson_transcripts),
        "post_lesson_response_chains": post_lesson_response_chains,
        "post_interrupt_user_transcript_expected_matches": (
            post_interrupt_user_transcript_expected_matches
        ),
        "model_echo_user_transcripts": model_echo_user_transcripts,
        "transcript_proof_matches": len(transcript_proof_markers),
        "transcript_proof_phases": [
            marker["phase"] for marker in transcript_proof_markers
        ],
        "transcript_proof_slots": [
            marker["slot"] for marker in transcript_proof_markers
        ],
        "malformed_transcript_proof_markers": malformed_transcript_proof_markers,
        "audio_interrupts": audio_interrupts,
        "fatal_hits": fatal_hits,
        "missing": missing,
    }
    if manifest_expectations:
        result["lesson_manifest_steps"] = manifest_expectations.get(
            "expected_lesson_steps", 0
        )
        result["lesson_manifest_interactive_steps"] = manifest_expectations.get(
            "expected_interactive_steps", 0
        )
        result["lesson_manifest_prompt_chars"] = manifest_expectations.get(
            "min_lesson_live_text_chars", 0
        )
        result["lesson_manifest_prompt_hashes"] = len(
            manifest_expectations.get("lesson_prompt_hashes", [])
        )
    if lesson is not None:
        for key, value in lesson.items():
            if key != "missing":
                result[key] = value
    return result


PROOF_METADATA_FLAGS = (
    "proof_backend_device_uuid",
    "proof_board_mac",
    "proof_captured_at",
    "proof_deployed_robot_server_image",
    "proof_hardware_sample_cp7_safety_run",
    "proof_image_reference_type",
    "proof_lesson_flow_completed",
    "proof_llm_judge_checked",
    "proof_output_classifier_checked",
)

PROOF_BOOL_FLAGS = {
    "proof_hardware_sample_cp7_safety_run",
    "proof_lesson_flow_completed",
    "proof_llm_judge_checked",
    "proof_output_classifier_checked",
}

PROOF_KEYS = (
    "backend_device_uuid",
    "board_mac",
    "captured_at",
    "deployed_robot_server_image",
    "hardware_sample_cp7_safety_run",
    "image_reference_type",
    "lesson_flow_completed",
    "llm_judge_checked",
    "output_classifier_checked",
    "redacted",
    "unsafe_output_blocked",
)


def _proof_bool(value):
    if value is None:
        return None
    normalized = str(value).strip().casefold()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise ValueError("expected true or false")


def _proof_text(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text or any(ch in text for ch in "\r\n"):
        raise ValueError("expected a non-empty single-line redacted value")
    return text


def _child_live_moderation_proof_values(args, result):
    return {
        "backend_device_uuid": _proof_text(args.proof_backend_device_uuid),
        "board_mac": _proof_text(args.proof_board_mac),
        "captured_at": _proof_text(args.proof_captured_at),
        "deployed_robot_server_image": _proof_text(
            args.proof_deployed_robot_server_image
        ),
        "hardware_sample_cp7_safety_run": _proof_bool(
            args.proof_hardware_sample_cp7_safety_run
        ),
        "image_reference_type": _proof_text(args.proof_image_reference_type),
        "lesson_flow_completed": _proof_bool(args.proof_lesson_flow_completed),
        "llm_judge_checked": _proof_bool(args.proof_llm_judge_checked),
        "output_classifier_checked": _proof_bool(
            args.proof_output_classifier_checked
        ),
        "redacted": True,
        "unsafe_output_blocked": (
            result.get("output_moderation_blocks", 0) >= 1
            and result.get("safe_deflection_live_text", 0) >= 1
        ),
    }


def _write_child_live_moderation_proof(path, values):
    lines = []
    for key in PROOF_KEYS:
        value = values[key]
        if isinstance(value, bool):
            value = "true" if value else "false"
        lines.append(f"{key}: {value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(
        description="Audit tbot_server logs for physical Google Live voice interrupt smoke evidence."
    )
    parser.add_argument("log_file", type=Path)
    parser.add_argument("--device-id", required=True)
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--min-interrupts", type=int, default=10)
    parser.add_argument("--server-ip")
    parser.add_argument(
        "--expected-user-transcript",
        action="append",
        default=[],
        help=(
            "Expected phrase that must appear in a user transcript; repeatable. "
            "With --production-voice-strict, it must appear after a Live interruption."
        ),
    )
    parser.add_argument(
        "--expected-post-lesson-transcript",
        action="append",
        default=[],
        help=(
            "Expected phrase after lesson_completed that must be followed by "
            "a Live first_audio_out marker; repeatable."
        ),
    )
    parser.add_argument("--require-aec-live-vad-forward", action="store_true")
    parser.add_argument("--require-live-server-interruption", action="store_true")
    parser.add_argument("--max-first-audio-ms", type=float)
    parser.add_argument("--production-google-live-candidate", action="store_true")
    parser.add_argument("--candidate-git-sha")
    parser.add_argument("--candidate-image-digest")
    parser.add_argument("--firmware-identity")
    parser.add_argument("--config-fingerprint")
    parser.add_argument("--fixture-sha256")
    parser.add_argument("--google-live-reliability-report", type=Path)
    parser.add_argument("--candidate-soak-report", type=Path)
    parser.add_argument(
        "--max-first-audio-p50-ms",
        type=float,
        default=GOOGLE_LIVE_LIMITS["firstAudioP50Ms"],
    )
    parser.add_argument(
        "--max-first-audio-p95-ms",
        type=float,
        default=GOOGLE_LIVE_LIMITS["firstAudioP95Ms"],
    )
    parser.add_argument(
        "--max-server-stop-ms",
        type=float,
        default=GOOGLE_LIVE_LIMITS["serverStopMaxMs"],
    )
    parser.add_argument(
        "--max-physical-bargein-p95-ms",
        type=float,
        default=GOOGLE_LIVE_LIMITS["physicalBargeinP95Ms"],
    )
    parser.add_argument(
        "--max-server-output-gap-ms",
        type=float,
        default=GOOGLE_LIVE_LIMITS["serverOutputGapMaxMs"],
    )
    parser.add_argument(
        "--production-voice-strict",
        action="store_true",
        help=(
            "Require production voice gates: fast first audio and AEC-cleaned "
            "Live VAD forwarding while robot audio is active."
        ),
    )
    parser.add_argument(
        "--production-output-safe-strict",
        action="store_true",
        help=(
            "Require output-safe Google Live gates: no listen_start self-interrupt, "
            "no AEC-forward during robot speech, realtime relisten, and a "
            "post-lesson user transcript followed by Live first audio."
        ),
    )
    parser.add_argument(
        "--production-course-strict",
        action="store_true",
        help=(
            "Require production lesson/course gates: lesson flow, Live text "
            "prompt handoff, manifest-derived prompt chars, and prompt hashes."
        ),
    )
    parser.add_argument(
        "--production-child-safety-strict",
        action="store_true",
        help=(
            "Require child-safety evidence: Live output moderation blocked "
            "unsafe model output and sent a safe deflection via Live text."
        ),
    )
    parser.add_argument(
        "--production-strict",
        action="store_true",
        help="Require both production voice and lesson/course gates.",
    )
    parser.add_argument("--require-lesson", action="store_true")
    parser.add_argument("--require-lesson-live-text", action="store_true")
    parser.add_argument("--min-lesson-live-text-chars", type=int)
    parser.add_argument("--lesson-manifest", type=Path)
    parser.add_argument("--expected-lesson-steps", type=int, default=9)
    parser.add_argument("--expected-interactive-steps", type=int, default=0)
    parser.add_argument(
        "--child-live-moderation-proof",
        type=Path,
        help=(
            "Write child_live_moderation proof markdown after a passed "
            "--production-child-safety-strict audit."
        ),
    )
    parser.add_argument("--proof-backend-device-uuid")
    parser.add_argument("--proof-board-mac")
    parser.add_argument("--proof-captured-at")
    parser.add_argument("--proof-deployed-robot-server-image")
    parser.add_argument("--proof-hardware-sample-cp7-safety-run")
    parser.add_argument("--proof-image-reference-type")
    parser.add_argument("--proof-lesson-flow-completed")
    parser.add_argument("--proof-llm-judge-checked")
    parser.add_argument("--proof-output-classifier-checked")
    args = parser.parse_args()

    log_text = args.log_file.read_text(encoding="utf-8", errors="replace")
    candidate_identity = None
    reliability_report = None
    candidate_soak_report = None
    if args.production_google_live_candidate:
        required_candidate_args = (
            "candidate_git_sha",
            "candidate_image_digest",
            "firmware_identity",
            "config_fingerprint",
            "fixture_sha256",
            "google_live_reliability_report",
            "candidate_soak_report",
        )
        missing_candidate_args = [
            "--" + name.replace("_", "-")
            for name in required_candidate_args
            if getattr(args, name) is None
        ]
        if missing_candidate_args:
            parser.error(
                "--production-google-live-candidate requires "
                + ", ".join(missing_candidate_args)
            )
        candidate_identity = {
            "gitSha": str(args.candidate_git_sha).strip(),
            "imageDigest": str(args.candidate_image_digest).strip(),
            "firmwareIdentity": str(args.firmware_identity).strip(),
            "fixtureSha256": str(args.fixture_sha256).strip(),
            "configFingerprint": str(args.config_fingerprint).strip(),
        }
        if not _candidate_identity_valid(candidate_identity):
            parser.error("production candidate identity is malformed")
        try:
            reliability_report = json.loads(
                args.google_live_reliability_report.read_text(encoding="utf-8")
            )
            candidate_soak_report = json.loads(
                args.candidate_soak_report.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError) as exc:
            parser.error(f"candidate evidence report is unreadable: {exc}")
        for budget_name in (
            "max_first_audio_p50_ms",
            "max_first_audio_p95_ms",
            "max_server_stop_ms",
            "max_physical_bargein_p95_ms",
            "max_server_output_gap_ms",
        ):
            value = getattr(args, budget_name)
            if not math.isfinite(value) or value <= 0:
                parser.error(f"--{budget_name.replace('_', '-')} must be positive")
    lesson_manifest = None
    if args.lesson_manifest is not None:
        lesson_manifest = json.loads(
            args.lesson_manifest.read_text(encoding="utf-8", errors="replace")
        )
    production_voice_strict = (
        args.production_voice_strict
        or args.production_strict
        or args.production_google_live_candidate
    )
    production_course_strict = (
        args.production_course_strict
        or args.production_strict
        or args.production_google_live_candidate
    )
    if args.child_live_moderation_proof is not None:
        if not args.production_child_safety_strict:
            parser.error(
                "--child-live-moderation-proof requires "
                "--production-child-safety-strict"
            )
        missing_proof_flags = [
            "--" + flag.replace("_", "-")
            for flag in PROOF_METADATA_FLAGS
            if getattr(args, flag) is None
        ]
        if missing_proof_flags:
            parser.error(
                "--child-live-moderation-proof requires "
                + ", ".join(missing_proof_flags)
            )
        for flag in PROOF_METADATA_FLAGS:
            try:
                if flag in PROOF_BOOL_FLAGS:
                    _proof_bool(getattr(args, flag))
                else:
                    _proof_text(getattr(args, flag))
            except ValueError as exc:
                parser.error(f"--{flag.replace('_', '-')} {exc}")
    if (
        production_voice_strict
        and not args.production_google_live_candidate
        and not args.expected_user_transcript
    ):
        strict_flag = "--production-strict" if args.production_strict else "--production-voice-strict"
        parser.error(f"{strict_flag} requires --expected-user-transcript")
    if production_course_strict and lesson_manifest is None:
        strict_flag = "--production-strict" if args.production_strict else "--production-course-strict"
        parser.error(f"{strict_flag} requires --lesson-manifest")
    require_aec_live_vad_forward = args.require_aec_live_vad_forward
    min_aec_live_vad_forward = None
    min_aec_interruption_chains = None
    require_live_server_interruption = args.require_live_server_interruption
    min_live_server_interruptions = None
    min_interrupt_tts_stops = None
    min_interrupt_stop_chains = None
    min_interrupt_user_chains = None
    min_interrupt_relisten_chains = None
    min_post_interrupt_user_transcripts = None
    min_realtime_tts_stops = None
    min_output_relisten_chains = None
    min_output_moderation_blocks = None
    min_safe_deflection_live_text = None
    max_aec_live_vad_forward = None
    max_listen_start_interrupts = None
    require_post_lesson_response = False
    allowed_fatal_patterns = None
    min_audio_interrupts = None
    max_first_audio_ms = args.max_first_audio_ms
    max_interrupt_stop_latency_ms = None
    if production_voice_strict:
        require_aec_live_vad_forward = True
        min_aec_live_vad_forward = args.min_interrupts
        min_aec_interruption_chains = args.min_interrupts
        require_live_server_interruption = True
        min_live_server_interruptions = args.min_interrupts
        min_interrupt_tts_stops = args.min_interrupts
        min_interrupt_stop_chains = args.min_interrupts
        min_interrupt_relisten_chains = args.min_interrupts
        min_post_interrupt_user_transcripts = 1
        min_realtime_tts_stops = 1
        min_output_relisten_chains = 1
        min_audio_interrupts = 0
        max_interrupt_stop_latency_ms = 250.0
        if max_first_audio_ms is None:
            max_first_audio_ms = 1800.0
    if args.production_google_live_candidate:
        max_first_audio_ms = None
        max_interrupt_stop_latency_ms = args.max_server_stop_ms
    if args.production_output_safe_strict:
        min_audio_interrupts = 0
        min_realtime_tts_stops = 1
        min_output_relisten_chains = 1
        max_aec_live_vad_forward = 0
        max_listen_start_interrupts = 0
        require_post_lesson_response = True
        allowed_fatal_patterns = {
            "audio_decision decision=suppress_echo reason=robot_speaking",
            "Google Live echo_suppressed reason=robot_speaking",
            "Google Live waiting_model_timeout",
        }
        if max_first_audio_ms is None:
            max_first_audio_ms = 8000.0
    if args.production_child_safety_strict:
        min_output_moderation_blocks = 1
        min_safe_deflection_live_text = 1
        if not production_voice_strict:
            min_audio_interrupts = 0
    require_lesson = args.require_lesson
    require_lesson_live_text = args.require_lesson_live_text
    if production_course_strict:
        require_lesson = True
        require_lesson_live_text = True
    result = audit_log(
        log_text,
        device_id=args.device_id,
        client_id=args.client_id,
        min_interrupts=args.min_interrupts,
        min_audio_interrupts=min_audio_interrupts,
        server_ip=args.server_ip,
        require_lesson=require_lesson,
        expected_lesson_steps=args.expected_lesson_steps,
        expected_interactive_steps=args.expected_interactive_steps,
        require_aec_live_vad_forward=require_aec_live_vad_forward,
        min_aec_live_vad_forward=min_aec_live_vad_forward,
        min_aec_interruption_chains=min_aec_interruption_chains,
        require_live_server_interruption=require_live_server_interruption,
        min_live_server_interruptions=min_live_server_interruptions,
        min_interrupt_tts_stops=min_interrupt_tts_stops,
        min_interrupt_stop_chains=min_interrupt_stop_chains,
        min_interrupt_user_chains=min_interrupt_user_chains,
        min_interrupt_relisten_chains=min_interrupt_relisten_chains,
        min_post_interrupt_user_transcripts=min_post_interrupt_user_transcripts,
        min_realtime_tts_stops=min_realtime_tts_stops,
        min_output_relisten_chains=min_output_relisten_chains,
        min_output_moderation_blocks=min_output_moderation_blocks,
        min_safe_deflection_live_text=min_safe_deflection_live_text,
        max_aec_live_vad_forward=max_aec_live_vad_forward,
        max_listen_start_interrupts=max_listen_start_interrupts,
        max_first_audio_ms=max_first_audio_ms,
        max_first_audio_p50_ms=(
            args.max_first_audio_p50_ms
            if args.production_google_live_candidate
            else None
        ),
        max_first_audio_p95_ms=(
            args.max_first_audio_p95_ms
            if args.production_google_live_candidate
            else None
        ),
        max_interrupt_stop_latency_ms=max_interrupt_stop_latency_ms,
        max_physical_bargein_p95_ms=(
            args.max_physical_bargein_p95_ms
            if args.production_google_live_candidate
            else None
        ),
        max_server_output_gap_ms=(
            args.max_server_output_gap_ms
            if args.production_google_live_candidate
            else None
        ),
        min_first_audio_samples=(
            _PHYSICAL_AUDIT_SAMPLE_COUNTS["firstAudio"]
            if args.production_google_live_candidate
            else None
        ),
        min_interrupt_stop_samples=(
            _PHYSICAL_AUDIT_SAMPLE_COUNTS["interruptStop"]
            if args.production_google_live_candidate
            else None
        ),
        min_physical_bargein_samples=(
            _PHYSICAL_AUDIT_SAMPLE_COUNTS["physicalBargein"]
            if args.production_google_live_candidate
            else None
        ),
        min_server_output_gap_samples=(
            _PHYSICAL_AUDIT_SAMPLE_COUNTS["serverOutputGap"]
            if args.production_google_live_candidate
            else None
        ),
        candidate_identity=candidate_identity,
        reliability_report=reliability_report,
        candidate_soak_report=candidate_soak_report,
        require_receive_loop_balance=args.production_google_live_candidate,
        require_post_lesson_response=require_post_lesson_response,
        require_lesson_live_text=require_lesson_live_text,
        min_lesson_live_text_chars=args.min_lesson_live_text_chars,
        lesson_manifest=lesson_manifest,
        expected_user_transcripts=(
            None
            if args.production_google_live_candidate
            else args.expected_user_transcript
        ),
        expected_post_lesson_transcripts=(
            None
            if args.production_google_live_candidate
            else args.expected_post_lesson_transcript
        ),
        allowed_fatal_patterns=allowed_fatal_patterns,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.child_live_moderation_proof is not None and result["passed"]:
        proof_values = _child_live_moderation_proof_values(args, result)
        if not proof_values["unsafe_output_blocked"]:
            parser.error(
                "--child-live-moderation-proof requires strict child safety evidence"
            )
        _write_child_live_moderation_proof(
            args.child_live_moderation_proof,
            proof_values,
        )
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
