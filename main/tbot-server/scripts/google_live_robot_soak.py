#!/usr/bin/env python3
"""PR5 soak harness for Google Live voice mode.

Runs N Q&A cycles against a tbot-server websocket endpoint, optionally
injects audio for barge-in, and emits a structured JSON report with
per-cycle latencies and aggregated AC1-AC7 verdicts described in
``.omc/plans/google-live-stability-bargein-v2.md`` Section 8.

Designed to be invoked manually before/after a deploy to gate AC PASS.
Reuses helpers from ``voice_mode_websocket_soak`` and the Opus encoder
from ``voice_mode_websocket_audio_bargein`` so we do not duplicate
plumbing. Log-based AC3 validation requires read access to the
``tmp/server.log`` file the server is writing.

Usage:
    python scripts/google_live_robot_soak.py \\
        --device-id 3c:0f:02:de:c2:e0 \\
        --client-id d16afa54-eb44-4fcb-8cac-cdefdf05f6fc \\
        --cycles 10 --bargein-cycles 5 \\
        --report .omc/research/soak.json

    # CI smoke (no server required):
    python scripts/google_live_robot_soak.py \\
        --cycles 1 --duration-sec 5 --dry-run \\
        --report /tmp/soak-dry.json

    # New --mode variants (plan §6.4 / AC1, AC2, AC4):
    python scripts/google_live_robot_soak.py --mode false_positive \\
        --duration 300 --env quiet --report /tmp/fp.json

    python scripts/google_live_robot_soak.py --mode bargein_latency \\
        --trials 10 --inject-audio data/test_stop_vn.wav --report /tmp/lat.json

    python scripts/google_live_robot_soak.py --mode rapid_interrupt \\
        --trials 10 --report /tmp/rapid.json
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import re
import sys
import time
from collections import Counter
from collections.abc import Mapping
from pathlib import Path

import websockets

SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from core.voice.google_live_credentials import (  # noqa: E402
    GOOGLE_LIVE_CREDENTIAL_ENV_NAMES,
    resolve_google_live_env_api_key,
)
from scripts.analyze_google_live_log import (  # noqa: E402
    _parse_utc_iso,
    correlate_websocket_bargein_evidence,
)
from scripts.google_live_reliability import (  # noqa: E402
    GOOGLE_LIVE_LIMITS,
    SCHEMA_VERSION,
    build_candidate_identity,
    compare_latency_baseline,
    percentile,
    redact_mapping,
    resource_verdict,
    sample_process_resources,
)
from scripts.voice_mode_websocket_audio_bargein import (  # noqa: E402
    _opus_packets_from_audio_file,
)
from scripts.voice_mode_websocket_soak import (  # noqa: E402
    _detect_message,
    _hello_message,
    _is_tts_state,
    _recv_until,
)

__all__ = ["GOOGLE_LIVE_CREDENTIAL_ENV_NAMES"]

_CANDIDATE_STAGE_COUNTS = (
    ("conversation", 17),
    ("bargein", 10),
    ("quiet", 2),
    ("reopen", 1),
    ("reconnect", 1),
    ("lesson", 1),
    ("conversation_after_lesson", 1),
)
_FORBIDDEN_EVIDENCE_KEYS = frozenset(
    {
        "audio",
        "audiochunk",
        "audiobytes",
        "rawaudio",
        "rawaudiobase64",
        "audiobase64",
        "transcript",
        "rawtranscript",
        "prompt",
        "modeltext",
        "rawlog",
        "loglines",
        "authorization",
        "apikey",
        "xgoogapikey",
        "xgoogleapikey",
        "xapikey",
        "token",
        "bearertoken",
        "cookie",
        "setcookie",
        "credential",
        "credentials",
        "secret",
        "exception",
        "sessionresumptionhandle",
    }
)

# ---------------------------------------------------------------------------
# Latency-chain patterns for PR5 modes (also used by analyze_google_live_log)
# ---------------------------------------------------------------------------
LOG_TTS_STOP_SENT_RE = re.compile(r"tts_state_stop_sent|tts_stop_sent")
LOG_REPLAYED_INTERRUPT_RE = re.compile(r"replayed_interrupt_audio")
LOG_INTERRUPT_FINALIZED_RE = re.compile(r"interrupt_input_finalized")
LOG_TRANSCRIPT_SOURCE_USER_RE = re.compile(r"transcript source=user")

DEFAULT_FIRST_PROMPT = "Hãy đếm số tiếng Việt từ một đến năm mươi, chậm rãi và rõ ràng."
DEFAULT_INTERRUPT_PROMPT = "Đổi đề tài. Hãy trả lời ngắn về thời tiết Hà Nội hôm nay."
DEFAULT_IDLE_PROMPT = "Hãy kể một câu chuyện ngắn bằng tiếng Việt trong khoảng hai phút."
TVIDEO_FARM_PROTOCOL_VERSION = "teebot-lesson-renderer.v4"
TVIDEO_FARM_TOOL_AUDIT_TYPE = "google_live_validation_tool_audit"
TVIDEO_FARM_TOOL_AUDIT_FEATURE = "googleLiveValidationToolAuditV1"
TVIDEO_FARM_LESSON_TOOLS = frozenset(
    {
        "lesson_child_response",
        "lesson_pronunciation_outcome",
        "lesson_context_turn",
        "lesson_visual_reaction",
        "lesson_continue",
    }
)
TVIDEO_FARM_EXPECTED_TOOL_PLAN = {
    "lesson_start": ("lesson_visual_reaction",),
    "target_answer": ("lesson_child_response", "lesson_visual_reaction"),
    "meaning_bridge": (
        "lesson_pronunciation_outcome",
        "lesson_visual_reaction",
    ),
    "related_concept": ("lesson_context_turn", "lesson_visual_reaction"),
    "retry_coaching": (
        "lesson_pronunciation_outcome",
        "lesson_visual_reaction",
    ),
    "correction_bargein": ("lesson_continue", "lesson_visual_reaction"),
    "hay_listen": ("lesson_visual_reaction",),
    "hay_thinking": ("lesson_child_response", "lesson_visual_reaction"),
    "hay_correct": (
        "lesson_pronunciation_outcome",
        "lesson_visual_reaction",
    ),
    "hay_celebrate": ("lesson_continue", "lesson_visual_reaction"),
}
TVIDEO_FARM_EXPECTED_PROGRESS = (
    {
        "label": "lesson_start",
        "cue_id": "barn-listen",
        "effect": "listen",
        "step_key": "barn",
    },
    {
        "label": "target_answer",
        "cue_id": "barn-thinking",
        "effect": "thinking",
        "step_key": "barn",
    },
    {
        "label": "meaning_bridge",
        "cue_id": "barn-correct",
        "effect": "correct",
        "step_key": "barn",
    },
    {
        "label": "related_concept",
        "cue_id": "barn-retry-level-1",
        "effect": "retry-level-1",
        "step_key": "barn",
    },
    {
        "label": "retry_coaching",
        "cue_id": "barn-correct",
        "effect": "correct",
        "step_key": "barn",
        "opens_bargein_window": True,
    },
    {
        "label": "correction_bargein",
        "cue_id": "barn-to-hay-word-transition",
        "effect": "word-transition",
        "step_key": "barn",
        "requires_interruption": True,
    },
    {
        "label": "hay_listen",
        "cue_id": "hay-listen",
        "effect": "listen",
        "step_key": "hay",
    },
    {
        "label": "hay_thinking",
        "cue_id": "hay-thinking",
        "effect": "thinking",
        "step_key": "hay",
    },
    {
        "label": "hay_correct",
        "cue_id": "hay-correct",
        "effect": "correct",
        "step_key": "hay",
    },
    {
        "label": "hay_celebrate",
        "cue_id": "hay-celebrate",
        "effect": "celebrate",
        "step_key": "hay",
    },
)
TVIDEO_FARM_AUDIO_FIXTURES = {
    "synthetic": {
        "fixture_set_id": "tvideo-farm-synthetic-speech-v1",
        "path": SERVER_ROOT / "tests" / "fixtures" / "tvideo_farm_audio" / "synthetic_speech_24k_mono.wav",
        "sha256": "654c23b4d1d0fc4b65b9b59141b0f71b7709a3ab4e0b22db69527ddcf97ec237",
        "sample_rate": 24000,
        "format": "wav/pcm_s16le/mono",
        "frame_duration_ms": 60,
    },
    "adult": {
        "fixture_set_id": "tvideo-farm-adult-speech-v1",
        "path": SERVER_ROOT / "tests" / "fixtures" / "tvideo_farm_audio" / "adult_speech_24k_mono.wav",
        "sha256": "dbd55231b25b5de9d7cbe0e54c8b237944b25aedede780afe745802e4d1696c4",
        "sample_rate": 24000,
        "format": "wav/pcm_s16le/mono",
        "frame_duration_ms": 60,
    },
}
TVIDEO_FARM_TURN_AUDIO_FIXTURES = {
    "synthetic": {
        "lesson_start": (
            "tvideo-farm-synthetic-lesson-start-v1",
            "synthetic_lesson_start_24k_mono.wav",
            "432742e9b0aac86caac690b4b821f67a606973459559343e002faecee5112007",
        ),
        "target_answer": (
            "tvideo-farm-synthetic-target-answer-v1",
            "synthetic_target_answer_24k_mono.wav",
            "d5c73802b4b7a8f2d0a67d0a4f28c8916b8c019590d1a428595557cc74a989ab",
        ),
        "meaning_bridge": (
            "tvideo-farm-synthetic-meaning-bridge-v1",
            "synthetic_meaning_bridge_24k_mono.wav",
            "57ef32076172af256267c17d24eb0a00912428f6c25010bda65e4486a42d8f9a",
        ),
        "related_concept": (
            "tvideo-farm-synthetic-related-concept-v1",
            "synthetic_related_concept_24k_mono.wav",
            "7fb7e7ad115138dc08760db17c1ecc5f287d7c7d50493e12fbef82b023f05451",
        ),
        "retry_coaching": (
            "tvideo-farm-synthetic-retry-coaching-v1",
            "synthetic_retry_coaching_24k_mono.wav",
            "d8f258774d056716d68b25693d7800ee32e050f80dea61ff2eff8fa25fc9e24e",
        ),
        "correction_bargein": (
            "tvideo-farm-synthetic-target-correction-v1",
            "synthetic_target_correction_24k_mono.wav",
            "d5c73802b4b7a8f2d0a67d0a4f28c8916b8c019590d1a428595557cc74a989ab",
        ),
        "hay_listen": (
            "tvideo-farm-synthetic-hay-listen-v1",
            "synthetic_hay_listen_24k_mono.wav",
            "24746145784a5260d6b3390e2ed1428e5d45c2a81cc29a1ab03d56e0816b224f",
        ),
        "hay_thinking": (
            "tvideo-farm-synthetic-hay-thinking-v1",
            "synthetic_hay_thinking_24k_mono.wav",
            "ebdae2d6e845f77e001b58c9cbef2726567095d11d6374b2e9832cb8f2417065",
        ),
        "hay_correct": (
            "tvideo-farm-synthetic-hay-correct-v1",
            "synthetic_hay_correct_24k_mono.wav",
            "e1d5534073b16a10297ead81e2e078b848c448e0841828592d1d6d45f5357f5d",
        ),
        "hay_celebrate": (
            "tvideo-farm-synthetic-hay-celebrate-v1",
            "synthetic_hay_celebrate_24k_mono.wav",
            "8b926c0b8a71185cede36f52109608ac9149802925851c71a438666e5d5336fd",
        ),
    },
    "adult": {
        "lesson_start": (
            "tvideo-farm-adult-lesson-start-v1",
            "adult_lesson_start_24k_mono.wav",
            "a817f1ddf56ce1bc11b85d1d6c33fa775b44239d4c9c43ea6e3e6d62163fd232",
        ),
        "target_answer": (
            "tvideo-farm-adult-target-answer-v1",
            "adult_target_answer_24k_mono.wav",
            "27912233181138c7bfece6d754ee4f65a5d9df1b3ec8c6a623dd3054fa9b4aab",
        ),
        "meaning_bridge": (
            "tvideo-farm-adult-meaning-bridge-v1",
            "adult_meaning_bridge_24k_mono.wav",
            "6169cff81fa06bad54ea7681abaad767210868bad137213cd6a162dd102b542e",
        ),
        "related_concept": (
            "tvideo-farm-adult-related-concept-v1",
            "adult_related_concept_24k_mono.wav",
            "a4a609955e33f615869f8e118bc60c3d194c48079fd1287b3b041cdd2f4ed401",
        ),
        "retry_coaching": (
            "tvideo-farm-adult-retry-coaching-v1",
            "adult_retry_coaching_24k_mono.wav",
            "22c14a0763533fab25d5968abebac0380fc2edfc7bd582c743936f70aa95d977",
        ),
        "correction_bargein": (
            "tvideo-farm-adult-target-correction-v1",
            "adult_target_correction_24k_mono.wav",
            "27912233181138c7bfece6d754ee4f65a5d9df1b3ec8c6a623dd3054fa9b4aab",
        ),
        "hay_listen": (
            "tvideo-farm-adult-hay-listen-v1",
            "adult_hay_listen_24k_mono.wav",
            "dd0fd6704855e4562f33493796fd629b224c16061fb778472ad168d53e36963f",
        ),
        "hay_thinking": (
            "tvideo-farm-adult-hay-thinking-v1",
            "adult_hay_thinking_24k_mono.wav",
            "97eb90ef5e55e743a487f9d86ce594b2ed4cee349465c543d9eec6e12937e3f9",
        ),
        "hay_correct": (
            "tvideo-farm-adult-hay-correct-v1",
            "adult_hay_correct_24k_mono.wav",
            "1af59cd826d0d1464d994de93a4d29d4fb55361d750e76b2be0956e80ed4d02a",
        ),
        "hay_celebrate": (
            "tvideo-farm-adult-hay-celebrate-v1",
            "adult_hay_celebrate_24k_mono.wav",
            "f537034a1d0bbc67eb1f90ad14d776b6f9a87531f7fc0b8183a3534a9c04c391",
        ),
    },
}

LOG_INTERRUPT_RE = re.compile(
    r"Google Live user_interrupted reason=(?P<reason>\w+) "
    r"cancelled_response_id=(?P<cancelled>\d+) "
    r"next_response_id=(?P<next>\d+)"
)
LOG_TRANSCRIPT_USER_RE = re.compile(r"Google Live transcript source=user chars=(?P<chars>\d+)")
LOG_AUDIO_START_RE = re.compile(r"Google Live audio_start")
LOG_GOAWAY_RE = re.compile(r"session_expiring|go_away|goAway", re.I)
LOG_RECONNECT_RE = re.compile(r"reconnect attempt (\d+) succeeded")
LOG_FALLBACK_RE = re.compile(r"fallback_triggered")


def _credential_gated_tvideo_farm_report(args):
    if getattr(args, "server_has_google_live_credentials", False):
        return None
    if resolve_google_live_env_api_key():
        return None
    return {
        "scenario": "tvideo-farm",
        "status": "SKIP_GOOGLE_LIVE_CREDENTIALS",
        "audio_source": args.audio_source,
        "duration_sec": args.event_timeout_sec,
        "raw_audio_persisted": False,
        "transcript_persisted": False,
        "exit_code": 0,
    }


def _safe_soak_config(args):
    """Serialize test controls without utterances, audio paths, or model prose."""
    safe_names = (
        "mode",
        "audio_source",
        "duration",
        "trials",
        "env",
        "skip_firmware_timing",
        "bargein_cycles",
        "idle_cycles",
        "event_timeout_sec",
        "speak_for_sec",
        "idle_duration_sec",
        "open_timeout_sec",
        "interrupt_timeout_sec",
        "settle_timeout_sec",
        "bargein_latency_budget_ms",
        "ac1_goaway_budget",
        "dry_run",
    )
    config = {name: getattr(args, name) for name in safe_names if hasattr(args, name)}
    config["inject_audio"] = bool(getattr(args, "inject_audio", None))
    config["inject_text"] = bool(getattr(args, "inject_text", None))
    return config


def _bargein_injection_detect(args):
    if getattr(args, "inject_audio", None):
        return _detect_message("SOAK_AUDIO_INTERRUPT_SENTINEL")
    return _detect_message(str(getattr(args, "interrupt_prompt", "") or ""))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tvideo_farm_fixture_config(audio_source: str) -> dict:
    fixture = TVIDEO_FARM_AUDIO_FIXTURES[audio_source]
    actual = _sha256_file(fixture["path"])
    if actual != fixture["sha256"]:
        raise RuntimeError("tvideo farm audio fixture digest mismatch")
    return fixture


def _tvideo_farm_safe_fixture_report(fixture: dict) -> dict:
    return {
        "source": fixture["fixture_set_id"],
        "sha256": fixture["sha256"],
        "sample_rate": fixture["sample_rate"],
        "format": fixture["format"],
        "frame_duration_ms": fixture["frame_duration_ms"],
    }


def _tvideo_farm_opus_packets(args) -> tuple[list[bytes], dict]:
    fixture = _tvideo_farm_fixture_config(args.audio_source)
    sample_rate = int(getattr(args, "sample_rate", fixture["sample_rate"]))
    frame_duration_ms = int(getattr(args, "frame_duration_ms", fixture["frame_duration_ms"]))
    if sample_rate != fixture["sample_rate"] or frame_duration_ms != fixture["frame_duration_ms"]:
        raise RuntimeError("tvideo farm audio params must match committed fixture metadata")
    packets = _opus_packets_from_audio_file(
        str(fixture["path"]),
        sample_rate,
        frame_duration_ms,
    )
    if not packets:
        raise RuntimeError("tvideo farm audio fixture produced no opus packets")
    return packets, fixture


def _tvideo_farm_turn_fixture_config(audio_source: str, label: str, base_fixture: dict) -> dict:
    fixture_id, filename, sha256 = TVIDEO_FARM_TURN_AUDIO_FIXTURES[audio_source][label]
    path = SERVER_ROOT / "tests" / "fixtures" / "tvideo_farm_audio" / filename
    actual = _sha256_file(path)
    if actual != sha256:
        raise RuntimeError("tvideo farm turn audio fixture digest mismatch")
    return {
        "fixture_id": fixture_id,
        "path": path,
        "sha256": sha256,
        "sample_rate": base_fixture["sample_rate"],
        "format": base_fixture["format"],
        "frame_duration_ms": base_fixture["frame_duration_ms"],
    }


def _tvideo_farm_turn_opus_packets(args, label: str, base_fixture: dict) -> tuple[list[bytes], dict]:
    fixture = _tvideo_farm_turn_fixture_config(args.audio_source, label, base_fixture)
    sample_rate = int(getattr(args, "sample_rate", fixture["sample_rate"]))
    frame_duration_ms = int(getattr(args, "frame_duration_ms", fixture["frame_duration_ms"]))
    if sample_rate != fixture["sample_rate"] or frame_duration_ms != fixture["frame_duration_ms"]:
        raise RuntimeError("tvideo farm audio params must match committed fixture metadata")
    packets = _opus_packets_from_audio_file(str(fixture["path"]), sample_rate, frame_duration_ms)
    if not packets:
        raise RuntimeError("tvideo farm turn audio fixture produced no opus packets")
    return packets, fixture


def _tvideo_farm_cinematic(payload: dict) -> dict | None:
    if payload.get("type") not in {"lesson_prepare", "lesson_cinematic_control"}:
        return None
    body = payload.get("body") if isinstance(payload.get("body"), dict) else {}
    phase = body.get("cinematicPhase") if isinstance(body.get("cinematicPhase"), dict) else body
    if not isinstance(phase, dict):
        return None
    return {
        "frame_type": payload.get("type"),
        "protocol_version": payload.get("protocolVersion"),
        "command": phase.get("command") or body.get("command"),
        "cue_id": phase.get("cueId"),
        "effect": phase.get("effect"),
        "step_key": phase.get("stepKey") or payload.get("stepId"),
        "playback_mode": phase.get("playbackMode") or body.get("playbackMode"),
        "command_sequence_id": phase.get("commandSequenceId") or body.get("commandSequenceId"),
        "envelope_sequence": payload.get("sequence"),
        "assignment_id": payload.get("assignmentId"),
        "session_id": payload.get("sessionId"),
        "lesson_id": payload.get("lessonId"),
        "lesson_version": payload.get("lessonVersion"),
        "payload": payload,
    }


def _expected_tvideo_playback_mode(effect: str) -> str:
    return "loop" if effect in {"listen", "thinking"} else "once"


def _tvideo_farm_duplicate_identity_errors(payload: dict, expected: dict) -> list[str]:
    body = payload.get("body") if isinstance(payload.get("body"), dict) else {}
    phase = body.get("cinematicPhase") if isinstance(body.get("cinematicPhase"), dict) else {}
    errors = []

    if payload.get("protocolVersion") != TVIDEO_FARM_PROTOCOL_VERSION:
        errors.append("wrong_protocol_version")
    if payload.get("stepId") != expected["step_key"]:
        errors.append("wrong_step")

    duplicate_fields = {
        "cueId": expected["cue_id"],
        "effect": expected["effect"],
        "stepKey": expected["step_key"],
        "playbackMode": _expected_tvideo_playback_mode(expected["effect"]),
    }
    for field, expected_value in duplicate_fields.items():
        body_value = body.get(field)
        phase_value = phase.get(field)
        if body_value is None or phase_value is None:
            errors.append("missing_playback_mode" if field == "playbackMode" else "missing_cinematic_duplicate")
            continue
        if body_value != phase_value:
            errors.append("cinematic_duplicate_mismatch")
        if phase_value != expected_value:
            if field == "playbackMode":
                errors.append("wrong_playback_mode")
            elif field == "effect":
                errors.append("wrong_effect")
            elif field == "cueId":
                errors.append("wrong_cue")
            elif field == "stepKey":
                errors.append("wrong_step")

    body_sequence = body.get("commandSequenceId")
    phase_sequence = phase.get("commandSequenceId")
    if body_sequence is None or phase_sequence is None:
        errors.append("missing_command_sequence")
    elif body_sequence != phase_sequence:
        errors.append("cinematic_duplicate_mismatch")

    body_command = body.get("command")
    phase_command = phase.get("command")
    if body_command is None or phase_command is None:
        errors.append("missing_cinematic_duplicate")
    elif body_command != phase_command:
        errors.append("cinematic_duplicate_mismatch")

    return errors


def _expected_tvideo_tool_effect(effect: str) -> str:
    return {
        "listen": "show_listening_scene",
        "thinking": "show_thinking_scene",
        "correct": "show_correct_reaction",
        "retry-level-1": "show_effort_reaction",
        "word-transition": "show_word_transition",
        "celebrate": "show_celebration",
    }[effect]


def _tvideo_farm_tool_audit_errors(
    payload: dict,
    lesson_identity: dict | None,
    expected: dict,
    audit_state: dict,
) -> list[str]:
    if payload.get("type") != TVIDEO_FARM_TOOL_AUDIT_TYPE:
        return []
    errors = []
    if payload.get("feature") != TVIDEO_FARM_TOOL_AUDIT_FEATURE:
        errors.append("wrong_tool_audit_feature")
    if payload.get("protocolVersion") != TVIDEO_FARM_PROTOCOL_VERSION:
        errors.append("wrong_protocol_version")
    if payload.get("accepted") is not True:
        errors.append("tool_audit_rejected")
    if not str(payload.get("code") or ""):
        errors.append("missing_tool_audit_code")
    tool_name = payload.get("toolName")
    if tool_name not in TVIDEO_FARM_LESSON_TOOLS:
        errors.append("wrong_tool_audit_name")
    identity = payload.get("identity") if isinstance(payload.get("identity"), dict) else {}
    refreshed = (
        payload.get("refreshedIdentity")
        if isinstance(payload.get("refreshedIdentity"), dict)
        else {}
    )
    required_identity = {
        "lessonSessionId",
        "turnSequenceId",
        "attemptId",
        "stepKey",
    }
    if not required_identity.issubset(identity) or not required_identity.issubset(
        refreshed
    ):
        errors.append("missing_tool_audit_identity")
    if lesson_identity is not None:
        expected_session = lesson_identity.get("session_id")
        if identity.get("lessonSessionId") != expected_session:
            errors.append("tool_audit_identity_mismatch")
    elif identity.get("lessonSessionId") is None:
        errors.append("tool_audit_identity_mismatch")
    if refreshed.get("lessonSessionId") != identity.get("lessonSessionId"):
        errors.append("tool_audit_identity_mismatch")
    if identity.get("stepKey") != expected["step_key"]:
        errors.append("tool_audit_identity_mismatch")
    if refreshed.get("stepKey") != expected["step_key"]:
        errors.append("tool_audit_identity_mismatch")
    origin_turn = identity.get("turnSequenceId")
    refreshed_turn = refreshed.get("turnSequenceId")
    if (
        not isinstance(origin_turn, int)
        or not isinstance(refreshed_turn, int)
        or refreshed_turn < origin_turn
    ):
        errors.append("tool_audit_identity_mismatch")
    previous = audit_state.get("last_refreshed_identity")
    if isinstance(previous, dict):
        if identity.get("lessonSessionId") != previous.get("lessonSessionId"):
            errors.append("tool_audit_identity_mismatch")
        previous_turn = previous.get("turnSequenceId")
        if (
            not isinstance(previous_turn, int)
            or not isinstance(origin_turn, int)
            or origin_turn < previous_turn
        ):
            errors.append("tool_audit_identity_mismatch")
        previous_step = previous.get("stepKey")
        if identity.get("stepKey") != previous_step and not (
            previous_step == "barn" and identity.get("stepKey") == "hay"
        ):
            errors.append("tool_audit_identity_mismatch")
    cue_id = payload.get("cueId")
    if cue_id is not None and cue_id != expected["cue_id"]:
        errors.append("tool_audit_cue_mismatch")
    effect = payload.get("effect")
    if effect is not None and effect != _expected_tvideo_tool_effect(
        expected["effect"]
    ):
        errors.append("tool_audit_effect_mismatch")
    audit_state["last_refreshed_identity"] = dict(refreshed)
    audit_state.setdefault("turn_tool_names", []).append(tool_name)
    audit_state.setdefault("records", []).append(
        {"toolName": tool_name, "accepted": payload.get("accepted") is True}
    )
    return errors


def _tvideo_farm_ack(frame: dict, inbound_sequence: int) -> dict:
    event = "frameZeroReady" if frame["command"] == "prepare" else "phaseReady"
    return {
        "type": "lesson_ack",
        "protocolVersion": frame["payload"].get("protocolVersion"),
        "assignmentId": frame["assignment_id"],
        "sessionId": frame["session_id"],
        "lessonId": frame["lesson_id"],
        "lessonVersion": frame["lesson_version"],
        "stepId": frame["payload"].get("stepId"),
        "sequence": inbound_sequence,
        "timestamp": 1,
        "body": {
            "acks": frame["envelope_sequence"],
            "rendered": True,
            "degraded": False,
            "cinematicPhase": {
                "event": event,
                "command": frame["command"],
                "cueId": frame["cue_id"],
                "commandSequenceId": frame["command_sequence_id"],
                "accepted": True,
                event: True,
            },
        },
    }


async def _send_tvideo_farm_audio_turn(websocket, packets, frame_duration_ms):
    for packet in packets:
        await websocket.send(packet)
        await asyncio.sleep(frame_duration_ms / 1000)


async def _observe_tvideo_farm_turn(
    websocket,
    expected,
    timeout,
    previous_sequence,
    inbound_ack_sequence,
    lesson_identity,
    previous_output_open,
    output_decoder,
    output_frame_size,
    audit_state,
):
    deadline = time.monotonic() + timeout
    tts_started = False
    tts_stopped = False
    output_binary_chunks = 0
    late_output_chunks = 0
    interruption_stopped = False
    requires_interruption = bool(expected.get("requires_interruption"))
    opens_bargein_window = bool(expected.get("opens_bargein_window"))
    events = []
    errors = []

    if requires_interruption and not previous_output_open:
        errors.append("bargein_without_active_output")

    while time.monotonic() < deadline:
        remaining = max(0.01, deadline - time.monotonic())
        try:
            message = await asyncio.wait_for(websocket.recv(), timeout=remaining)
        except asyncio.TimeoutError:
            break
        if isinstance(message, bytes):
            if requires_interruption and interruption_stopped and not tts_started:
                late_output_chunks += 1
            elif tts_started and not tts_stopped:
                try:
                    decoded = output_decoder.decode(message, output_frame_size)
                except Exception:
                    errors.append("invalid_output_opus")
                else:
                    if not decoded:
                        errors.append("invalid_output_opus")
                    else:
                        output_binary_chunks += 1
                        if opens_bargein_window and len(events) == 2:
                            break
            continue
        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            continue

        if payload.get("type") == TVIDEO_FARM_TOOL_AUDIT_TYPE:
            errors.extend(
                _tvideo_farm_tool_audit_errors(
                    payload,
                    lesson_identity,
                    expected,
                    audit_state,
                )
            )
            continue

        cinematic = _tvideo_farm_cinematic(payload)
        if cinematic is not None:
            errors.extend(_tvideo_farm_duplicate_identity_errors(payload, expected))
            if requires_interruption and not interruption_stopped:
                errors.append("missing_interruption_stop")
            expected_command = "prepare" if not events else "start"
            expected_frame_type = "lesson_prepare" if expected_command == "prepare" else "lesson_cinematic_control"
            if cinematic["command"] != expected_command:
                errors.append("wrong_cinematic_command")
            if cinematic["frame_type"] != expected_frame_type:
                errors.append("wrong_cinematic_frame_type")
            if cinematic["cue_id"] != expected["cue_id"]:
                errors.append("wrong_cue")
            if cinematic["effect"] != expected["effect"]:
                errors.append("wrong_effect")
            if cinematic["step_key"] != expected["step_key"]:
                errors.append("wrong_step")

            command_sequence = cinematic["command_sequence_id"]
            envelope_sequence = cinematic["envelope_sequence"]
            if not isinstance(command_sequence, int):
                errors.append("missing_command_sequence")
            elif command_sequence <= previous_sequence:
                errors.append("non_increasing_command_sequence")
            else:
                previous_sequence = command_sequence
            if command_sequence != envelope_sequence:
                errors.append("command_sequence_envelope_mismatch")

            current_identity = {
                "assignment_id": cinematic["assignment_id"],
                "session_id": cinematic["session_id"],
                "lesson_id": cinematic["lesson_id"],
                "lesson_version": cinematic["lesson_version"],
            }
            if lesson_identity is None:
                if not all(current_identity.values()):
                    errors.append("missing_lesson_identity")
                lesson_identity = current_identity
            elif current_identity != lesson_identity:
                errors.append("lesson_session_mismatch")

            inbound_ack_sequence += 1
            await websocket.send(json.dumps(_tvideo_farm_ack(cinematic, inbound_ack_sequence)))
            events.append(cinematic)
            continue

        if _is_tts_state(payload, "start"):
            if requires_interruption and not interruption_stopped:
                errors.append("missing_interruption_stop")
            if len(events) != 2:
                errors.append("missing_cinematic_event")
            tts_started = True
            continue
        if _is_tts_state(payload, "stop"):
            if requires_interruption and not interruption_stopped and not tts_started:
                if payload.get("reason") != "interrupt":
                    errors.append("wrong_interruption_reason")
                interruption_stopped = True
                continue
            tts_stopped = True
            if len(events) == 2 and tts_started:
                break

    if len(events) != 2:
        errors.append("missing_cinematic_event")
    if not tts_started:
        errors.append("tts_start_timeout")
    if not opens_bargein_window and not tts_stopped:
        errors.append("tts_stop_timeout")
    if output_binary_chunks == 0:
        errors.append("missing_output_audio")
    if requires_interruption and not interruption_stopped:
        errors.append("missing_interruption_stop")
    if late_output_chunks:
        errors.append("late_output_after_interruption")
    observed_tool_names = tuple(audit_state.pop("turn_tool_names", []))
    if observed_tool_names != TVIDEO_FARM_EXPECTED_TOOL_PLAN[expected["label"]]:
        errors.append("tool_audit_sequence_mismatch")

    event = events[-1] if events else None
    if expected["cue_id"] == "hay-listen" and event and event["step_key"] != "hay":
        errors.append("stale_or_missing_step_transition")
    if event and expected["cue_id"].startswith("hay-") and str(event["cue_id"]).startswith("barn-"):
        errors.append("stale_or_missing_step_transition")
    return (
        errors,
        event,
        previous_sequence,
        inbound_ack_sequence,
        lesson_identity,
        {
            "output_binary_chunks": output_binary_chunks,
            "interruption_count": int(interruption_stopped),
            "late_output_chunks": late_output_chunks,
            "output_open": opens_bargein_window and tts_started and not tts_stopped,
        },
    )


class LogTail:
    """Cheap server.log tail: capture file offset at start, read on demand."""

    def __init__(self, log_path: Path | None):
        self.log_path = log_path
        self._start_offset = self._current_offset()

    def _current_offset(self):
        if self.log_path is None or not self.log_path.exists():
            return None
        try:
            return self.log_path.stat().st_size
        except OSError:
            return None

    def reset(self):
        self._start_offset = self._current_offset()

    def read_new(self):
        if self.log_path is None or self._start_offset is None:
            return ""
        try:
            with self.log_path.open("rb") as fh:
                fh.seek(self._start_offset)
                payload = fh.read()
            return payload.decode("utf-8", errors="replace")
        except OSError:
            return ""


async def _wait_first_binary_after(websocket, start_deadline, log_tail):
    binary = 0
    json_messages = []
    while time.monotonic() < start_deadline:
        remaining = max(0.01, start_deadline - time.monotonic())
        try:
            message = await asyncio.wait_for(websocket.recv(), timeout=remaining)
        except asyncio.TimeoutError:
            break
        if isinstance(message, bytes):
            return True, binary, json_messages
        binary += 0
        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            continue
        json_messages.append(payload)
    return False, binary, json_messages


async def _run_bargein_cycle(websocket, cycle_index, args, log_tail):
    record = {
        "index": cycle_index,
        "kind": "bargein",
        "outcome": "FAIL",
        "first_audio_latency_ms": None,
        "bargein_latency_ms": None,
        "user_transcript_received": False,
        "new_response_id": None,
        "cancelled_response_id": None,
        "errors": [],
    }
    log_tail.reset()

    t0 = time.monotonic()
    await websocket.send(json.dumps(_detect_message(f"{args.first_prompt} Lần {cycle_index + 1}.")))
    start_payload, _, _ = await _recv_until(
        websocket,
        lambda payload: _is_tts_state(payload, "start"),
        args.event_timeout_sec,
    )
    if start_payload is None:
        record["errors"].append("first_tts_start_timeout")
        return record
    t1 = time.monotonic()
    record["first_audio_latency_ms"] = round((t1 - t0) * 1000, 1)

    # let the model speak for a while so we have something to interrupt
    await asyncio.sleep(args.speak_for_sec)

    t_int_send = time.monotonic()
    await websocket.send(json.dumps(_detect_message(f"{args.interrupt_prompt} Lần {cycle_index + 1}.")))
    stop_payload, _, _ = await _recv_until(
        websocket,
        lambda payload: _is_tts_state(payload, "stop"),
        args.interrupt_timeout_sec,
    )
    if stop_payload is None:
        record["errors"].append("interrupt_tts_stop_timeout")
        return record
    t_int_stop = time.monotonic()
    record["bargein_latency_ms"] = round((t_int_stop - t_int_send) * 1000, 1)

    second_start, _, _ = await _recv_until(
        websocket,
        lambda payload: _is_tts_state(payload, "start"),
        args.event_timeout_sec,
    )
    if second_start is None:
        record["errors"].append("post_interrupt_tts_start_timeout")
        return record

    final_stop, _, _ = await _recv_until(
        websocket,
        lambda payload: _is_tts_state(payload, "stop"),
        args.settle_timeout_sec,
    )
    if final_stop is None:
        record["errors"].append("final_tts_stop_timeout")
        return record

    log_chunk = log_tail.read_new()
    transcript_match = LOG_TRANSCRIPT_USER_RE.search(log_chunk)
    interrupt_match = LOG_INTERRUPT_RE.search(log_chunk)
    record["user_transcript_received"] = transcript_match is not None
    if interrupt_match:
        record["cancelled_response_id"] = int(interrupt_match.group("cancelled"))
        record["new_response_id"] = int(interrupt_match.group("next"))

    pass_conditions = [
        record["bargein_latency_ms"] is not None and record["bargein_latency_ms"] <= args.bargein_latency_budget_ms,
        record["new_response_id"] is not None
        and record["cancelled_response_id"] is not None
        and record["new_response_id"] > record["cancelled_response_id"],
    ]
    if all(pass_conditions):
        record["outcome"] = "PASS"
    return record


async def _run_idle_cycle(websocket, cycle_index, args, log_tail):
    record = {
        "index": cycle_index,
        "kind": "idle",
        "outcome": "FAIL",
        "false_positive_interrupts": 0,
        "errors": [],
    }
    log_tail.reset()
    await websocket.send(json.dumps(_detect_message(args.idle_prompt)))
    start_payload, _, _ = await _recv_until(
        websocket,
        lambda payload: _is_tts_state(payload, "start"),
        args.event_timeout_sec,
    )
    if start_payload is None:
        record["errors"].append("idle_tts_start_timeout")
        return record

    end_payload, _, _ = await _recv_until(
        websocket,
        lambda payload: _is_tts_state(payload, "stop"),
        args.idle_duration_sec + args.settle_timeout_sec,
    )
    if end_payload is None:
        record["errors"].append("idle_tts_stop_timeout")
    log_chunk = log_tail.read_new()
    false_positive = len(LOG_INTERRUPT_RE.findall(log_chunk))
    record["false_positive_interrupts"] = false_positive
    record["outcome"] = "PASS" if false_positive == 0 else "FAIL"
    return record


def _summarize_acs(cycles, full_log, args):
    bargein_cycles = [c for c in cycles if c["kind"] == "bargein"]
    idle_cycles = [c for c in cycles if c["kind"] == "idle"]
    goaway_count = len(LOG_GOAWAY_RE.findall(full_log))
    reconnect_count = len(LOG_RECONNECT_RE.findall(full_log))
    fallback_count = len(LOG_FALLBACK_RE.findall(full_log))

    bargein_pass = sum(1 for c in bargein_cycles if c["outcome"] == "PASS")
    bargein_latencies = [c["bargein_latency_ms"] for c in bargein_cycles if c["bargein_latency_ms"] is not None]
    p95 = sorted(bargein_latencies)[max(0, int(0.95 * (len(bargein_latencies) - 1)))] if bargein_latencies else None

    idle_pass = sum(1 for c in idle_cycles if c["outcome"] == "PASS")

    return {
        "AC1": {
            "pass": fallback_count == 0 and goaway_count <= args.ac1_goaway_budget,
            "goaway_seen": goaway_count,
            "reconnect_succeeded": reconnect_count,
            "fallback_triggered": fallback_count,
            "budget_goaway": args.ac1_goaway_budget,
        },
        "AC2": {
            "pass": (len(bargein_cycles) > 0 and p95 is not None and p95 <= args.bargein_latency_budget_ms),
            "cycles": len(bargein_cycles),
            "p95_latency_ms": p95,
            "budget_ms": args.bargein_latency_budget_ms,
        },
        "AC3": {
            "pass": (len(bargein_cycles) > 0 and bargein_pass >= max(1, int(0.8 * len(bargein_cycles)))),
            "ratio": f"{bargein_pass}/{len(bargein_cycles)}",
            "rule": ">= 80% bargein cycles must produce new response id with user transcript",
        },
        "AC4": {
            "pass": len(idle_cycles) == 0 or idle_pass == len(idle_cycles),
            "ratio": f"{idle_pass}/{len(idle_cycles)}",
            "rule": "0 user_interrupted log lines during idle cycles",
        },
        "AC5": {
            "pass": fallback_count == 0,
            "fallback_triggered": fallback_count,
            "rule": "no fallback to classic during soak; auth/quota tests run separately",
        },
    }


# ---------------------------------------------------------------------------
# PR5 §6.4: Three new mode runners
# ---------------------------------------------------------------------------


async def _run_false_positive_mode(args):
    """AC1: count user_interrupted events during robot soliloquy (no user present)."""
    headers = {
        "device-id": args.device_mac if args.device_mac else args.device_id,
        "client-id": args.client_id,
    }
    log_tail = LogTail(Path(args.log_path) if args.log_path else None)
    log_tail.reset()
    started_at = time.time()
    duration = getattr(args, "duration", 300)
    env = getattr(args, "env", "unknown")
    false_positives = 0
    tts_stop_timeout = False

    async with websockets.connect(
        args.websocket_url,
        additional_headers=headers,
        open_timeout=args.open_timeout_sec,
        max_size=None,
    ) as websocket:
        await websocket.send(json.dumps(_hello_message()))
        hello_payload, _, _ = await _recv_until(
            websocket,
            lambda payload: payload.get("type") == "hello",
            args.event_timeout_sec,
        )
        if hello_payload is None:
            raise RuntimeError("hello ack timeout")

        await websocket.send(json.dumps(_detect_message(args.idle_prompt)))
        tts_start, _, _ = await _recv_until(
            websocket,
            lambda payload: _is_tts_state(payload, "start"),
            args.event_timeout_sec,
        )
        if tts_start is None:
            tts_stop_timeout = True
        else:
            tts_end, _, _ = await _recv_until(
                websocket,
                lambda payload: _is_tts_state(payload, "stop"),
                duration + args.settle_timeout_sec,
            )
            if tts_end is None:
                tts_stop_timeout = True

        await websocket.close()

    log_chunk = log_tail.read_new()
    false_positives = len(LOG_INTERRUPT_RE.findall(log_chunk))
    # AC1 threshold: ≤ 3 false-positives per 15 min (1 per 5 min)
    ac1_budget = max(1, int(duration / 300))
    ac1_pass = false_positives <= ac1_budget

    report = {
        "mode": "false_positive",
        "environment": env,
        "duration_sec": duration,
        "started_at": started_at,
        "elapsed_sec": round(time.time() - started_at, 1),
        "false_positives": false_positives,
        "ac1_budget": ac1_budget,
        "ac1_pass": ac1_pass,
        "tts_stop_timeout": tts_stop_timeout,
        "exit_code": 0 if ac1_pass else 1,
    }
    return report


async def _run_bargein_latency_mode(args):
    """AC2: inject audio mid-TTS, measure T0-T2 server-side latency chain."""
    headers = {
        "device-id": args.device_mac if args.device_mac else args.device_id,
        "client-id": args.client_id,
    }
    log_tail = LogTail(Path(args.log_path) if args.log_path else None)
    trials = getattr(args, "trials", 10)
    skip_firmware = getattr(args, "skip_firmware_timing", True)
    latencies_ms = []
    started_at = time.time()
    trial_records = []

    async with websockets.connect(
        args.websocket_url,
        additional_headers=headers,
        open_timeout=args.open_timeout_sec,
        max_size=None,
    ) as websocket:
        await websocket.send(json.dumps(_hello_message()))
        hello_payload, _, _ = await _recv_until(
            websocket,
            lambda payload: payload.get("type") == "hello",
            args.event_timeout_sec,
        )
        if hello_payload is None:
            raise RuntimeError("hello ack timeout")

        for i in range(trials):
            log_tail.reset()
            await websocket.send(json.dumps(_detect_message(f"{args.first_prompt} Trial {i + 1}.")))
            tts_start, _, _ = await _recv_until(
                websocket,
                lambda payload: _is_tts_state(payload, "start"),
                args.event_timeout_sec,
            )
            if tts_start is None:
                trial_records.append({"trial": i, "outcome": "FAIL", "error": "tts_start_timeout"})
                continue

            await asyncio.sleep(args.speak_for_sec)

            # T0: emit only a non-sensitive sentinel; binary audio stays hardware-gated.
            t0 = time.monotonic()
            await websocket.send(json.dumps(_bargein_injection_detect(args)))

            tts_stop, _, _ = await _recv_until(
                websocket,
                lambda payload: _is_tts_state(payload, "stop"),
                args.interrupt_timeout_sec,
            )
            t2 = time.monotonic()

            if tts_stop is None:
                trial_records.append({"trial": i, "outcome": "FAIL", "error": "tts_stop_timeout"})
                continue

            latency_ms = (t2 - t0) * 1000
            latencies_ms.append(latency_ms)

            log_chunk = log_tail.read_new()
            has_interrupted = bool(LOG_INTERRUPT_RE.search(log_chunk))
            trial_records.append(
                {
                    "trial": i,
                    "outcome": "PASS",
                    "t0_to_t2_ms": round(latency_ms, 1),
                    "user_interrupted_in_log": has_interrupted,
                    "skip_firmware_timing": skip_firmware,
                }
            )

            # wait for next tts_start before next trial
            await _recv_until(
                websocket,
                lambda payload: _is_tts_state(payload, "stop"),
                args.settle_timeout_sec,
            )

        await websocket.close()

    p50 = None
    p95 = None
    if latencies_ms:
        s = sorted(latencies_ms)
        p50 = s[max(0, int(0.5 * (len(s) - 1)))]
        p95 = s[max(0, int(0.95 * (len(s) - 1)))]

    budget_ms = args.bargein_latency_budget_ms
    ac2_pass = p95 is not None and p95 <= budget_ms

    report = {
        "mode": "bargein_latency",
        "started_at": started_at,
        "elapsed_sec": round(time.time() - started_at, 1),
        "trials": trials,
        "completed_trials": len(latencies_ms),
        "p50_ms": round(p50, 1) if p50 is not None else None,
        "p95_ms": round(p95, 1) if p95 is not None else None,
        "budget_ms": budget_ms,
        "ac2_pass": ac2_pass,
        "trial_records": trial_records,
        "exit_code": 0 if ac2_pass else 1,
    }
    return report


async def _run_rapid_interrupt_mode(args):
    """AC4: inject two utterances 0.2-0.4s apart; assert 1 interrupt per pair."""
    headers = {
        "device-id": args.device_mac if args.device_mac else args.device_id,
        "client-id": args.client_id,
    }
    log_tail = LogTail(Path(args.log_path) if args.log_path else None)
    trials = getattr(args, "trials", 10)
    started_at = time.time()
    single_interrupt_count = 0
    disconnect_count = 0
    trial_records = []
    rapid_gap_sec = 0.3

    async with websockets.connect(
        args.websocket_url,
        additional_headers=headers,
        open_timeout=args.open_timeout_sec,
        max_size=None,
    ) as websocket:
        await websocket.send(json.dumps(_hello_message()))
        hello_payload, _, _ = await _recv_until(
            websocket,
            lambda payload: payload.get("type") == "hello",
            args.event_timeout_sec,
        )
        if hello_payload is None:
            raise RuntimeError("hello ack timeout")

        for i in range(trials):
            log_tail.reset()
            await websocket.send(json.dumps(_detect_message(f"{args.first_prompt} Trial {i + 1}.")))
            tts_start, _, _ = await _recv_until(
                websocket,
                lambda payload: _is_tts_state(payload, "start"),
                args.event_timeout_sec,
            )
            if tts_start is None:
                trial_records.append({"trial": i, "outcome": "FAIL", "error": "tts_start_timeout"})
                continue

            await asyncio.sleep(args.speak_for_sec)

            # First utterance: "stop"
            await websocket.send(json.dumps(_detect_message(args.interrupt_prompt)))
            await asyncio.sleep(rapid_gap_sec)
            # Second utterance: "play music" — 0.2-0.4s later
            await websocket.send(json.dumps(_detect_message("Phát nhạc ngay đi.")))

            tts_stop, _, _ = await _recv_until(
                websocket,
                lambda payload: _is_tts_state(payload, "stop"),
                args.interrupt_timeout_sec + 1.0,
            )

            log_chunk = log_tail.read_new()
            interrupt_matches = LOG_INTERRUPT_RE.findall(log_chunk)
            interrupt_count = len(interrupt_matches)
            is_single_interrupt = interrupt_count == 1
            if is_single_interrupt:
                single_interrupt_count += 1

            trial_records.append(
                {
                    "trial": i,
                    "outcome": "PASS" if is_single_interrupt else "FAIL",
                    "interrupt_count": interrupt_count,
                    "tts_stopped": tts_stop is not None,
                }
            )

            # Allow the response to settle before next trial
            await _recv_until(
                websocket,
                lambda payload: _is_tts_state(payload, "stop"),
                args.settle_timeout_sec,
            )

        await websocket.close()

    ac4_pass = single_interrupt_count == trials and disconnect_count == 0
    report = {
        "mode": "rapid_interrupt",
        "started_at": started_at,
        "elapsed_sec": round(time.time() - started_at, 1),
        "trials": trials,
        "single_interrupt_trials": single_interrupt_count,
        "disconnect_count": disconnect_count,
        "ac4_pass": ac4_pass,
        "trial_records": trial_records,
        "exit_code": 0 if ac4_pass else 1,
    }
    return report


async def _run_tvideo_farm_scenario(args):
    """Exercise the bounded farm conversation without recording speech content."""
    headers = {
        "device-id": args.device_mac if args.device_mac else args.device_id,
        "client-id": args.client_id,
    }
    started_at = time.time()
    records = []
    validation_errors = []
    timeout = max(1.0, float(args.event_timeout_sec))
    fixture = _tvideo_farm_fixture_config(args.audio_source)
    frame_duration_ms = int(getattr(args, "frame_duration_ms", fixture["frame_duration_ms"]))
    binary_chunks_sent = 0
    output_binary_chunks = 0
    interruption_count = 0
    late_output_chunks = 0
    previous_sequence = 0
    inbound_ack_sequence = 0
    lesson_identity = None
    previous_output_open = False
    bargein_audio_sent_while_output_active = False
    observed_step_keys = []
    audit_state = {"records": []}
    import opuslib_next

    output_decoder = opuslib_next.Decoder(fixture["sample_rate"], 1)
    output_frame_size = int(fixture["sample_rate"] * fixture["frame_duration_ms"] / 1000)

    async with websockets.connect(
        args.websocket_url,
        additional_headers=headers,
        open_timeout=args.open_timeout_sec,
        max_size=None,
    ) as websocket:
        hello = _hello_message()
        hello["features"] = {TVIDEO_FARM_TOOL_AUDIT_FEATURE: True}
        await websocket.send(json.dumps(hello))
        hello_payload, _, _ = await _recv_until(
            websocket,
            lambda payload: payload.get("type") == "hello",
            min(timeout, args.event_timeout_sec),
        )
        if hello_payload is None:
            raise RuntimeError("hello ack timeout")

        for expected in TVIDEO_FARM_EXPECTED_PROGRESS:
            turn_started = time.monotonic()
            packets, turn_fixture = _tvideo_farm_turn_opus_packets(args, expected["label"], fixture)
            if expected.get("requires_interruption") and previous_output_open:
                bargein_audio_sent_while_output_active = True
            await _send_tvideo_farm_audio_turn(websocket, packets, frame_duration_ms)
            binary_chunks_sent += len(packets)
            (
                errors,
                event,
                previous_sequence,
                inbound_ack_sequence,
                lesson_identity,
                wire_metrics,
            ) = await _observe_tvideo_farm_turn(
                websocket,
                expected,
                timeout,
                previous_sequence,
                inbound_ack_sequence,
                lesson_identity,
                previous_output_open,
                output_decoder,
                output_frame_size,
                audit_state,
            )
            validation_errors.extend(errors)
            output_binary_chunks += wire_metrics["output_binary_chunks"]
            interruption_count += wire_metrics["interruption_count"]
            late_output_chunks += wire_metrics["late_output_chunks"]
            previous_output_open = wire_metrics["output_open"]
            if event and (not observed_step_keys or observed_step_keys[-1] != event["step_key"]):
                observed_step_keys.append(event["step_key"])
            record = {
                "input_fixture_id": turn_fixture["fixture_id"],
                "input_fixture_sha256": turn_fixture["sha256"],
                "input_opus_packets": len(packets),
            }
            record["latency_ms"] = round((time.monotonic() - turn_started) * 1000, 1)
            records.append(record)

        await websocket.close()

    conversation_identity_changes = max(0, len(observed_step_keys) - 1)
    if observed_step_keys != ["barn", "hay"]:
        validation_errors.append("conversation_identity_transition_mismatch")
    if interruption_count != 1:
        validation_errors.append("interruption_count_mismatch")
    if not bargein_audio_sent_while_output_active:
        validation_errors.append("bargein_not_sent_while_output_active")
    lesson_session_consistent = "lesson_session_mismatch" not in validation_errors
    tool_audit_counts = Counter(
        record["toolName"] for record in audit_state["records"]
    )
    if set(tool_audit_counts) != TVIDEO_FARM_LESSON_TOOLS:
        validation_errors.append("missing_required_tool_audit")
    passed = len(records) == len(TVIDEO_FARM_EXPECTED_PROGRESS) and not validation_errors
    return {
        "scenario": "tvideo-farm",
        "status": "PASS" if passed else "FAIL",
        "audio_source": args.audio_source,
        "fixture_set_id": fixture["fixture_set_id"],
        "fixture": _tvideo_farm_safe_fixture_report(fixture),
        "binary_chunks_sent": binary_chunks_sent,
        "output_binary_chunks": output_binary_chunks,
        "interruption_count": interruption_count,
        "late_output_chunks": late_output_chunks,
        "conversation_identity_changes": conversation_identity_changes,
        "bargein_audio_sent_while_output_active": bargein_audio_sent_while_output_active,
        "lesson_session_consistent": lesson_session_consistent,
        "tool_audit_count": len(audit_state["records"]),
        "tool_audit_counts": dict(sorted(tool_audit_counts.items())),
        "duration_sec": round(time.time() - started_at, 1),
        "turns": records,
        "validation_errors": sorted(set(validation_errors)),
        "raw_audio_persisted": False,
        "transcript_persisted": False,
        "exit_code": 0 if passed else 1,
    }


def _dry_run_tvideo_farm_report(args):
    started_at = time.time()
    fixture = _tvideo_farm_fixture_config(args.audio_source)
    turns = []
    binary_chunks_sent = 0
    for item in TVIDEO_FARM_EXPECTED_PROGRESS:
        packets, turn_fixture = _tvideo_farm_turn_opus_packets(args, item["label"], fixture)
        binary_chunks_sent += len(packets)
        turns.append(
            {
                "input_fixture_id": turn_fixture["fixture_id"],
                "input_fixture_sha256": turn_fixture["sha256"],
                "input_opus_packets": len(packets),
            }
        )
    return {
        "scenario": "tvideo-farm",
        "status": "FAKE_PASS",
        "dry_run": True,
        "audio_source": args.audio_source,
        "fixture_set_id": fixture["fixture_set_id"],
        "fixture": _tvideo_farm_safe_fixture_report(fixture),
        "binary_chunks_sent": binary_chunks_sent,
        "output_binary_chunks": 0,
        "interruption_count": 0,
        "late_output_chunks": 0,
        "conversation_identity_changes": 1,
        "bargein_audio_sent_while_output_active": False,
        "lesson_session_consistent": False,
        "duration_sec": round(time.time() - started_at, 3),
        "turns": turns,
        "validation_errors": [],
        "raw_audio_persisted": False,
        "transcript_persisted": False,
        "exit_code": 0,
    }


def _dry_run_report(args):
    """Return a placeholder report when --dry-run is set (no server needed)."""
    started_at = time.time()
    n = args.bargein_cycles
    cycles = [
        {
            "index": i,
            "kind": "bargein",
            "outcome": "SKIPPED_DRY_RUN",
            "first_audio_latency_ms": None,
            "bargein_latency_ms": None,
            "user_transcript_received": False,
            "new_response_id": None,
            "cancelled_response_id": None,
            "stale_audio_after_interrupt_count": 0,
            "errors": ["dry_run"],
        }
        for i in range(n)
    ]
    ac_results = {
        "AC1": {"pass": None, "details": "dry_run — not evaluated"},
        "AC2": {
            "pass": None,
            "p95_latency_ms": None,
            "budget_ms": args.bargein_latency_budget_ms,
            "details": "dry_run",
        },
        "AC3": {"pass": None, "ratio": f"0/{n}", "rule": "dry_run"},
        "AC4": {"pass": None, "ratio": "0/0", "rule": "dry_run"},
        "AC5": {"pass": None, "fallback_triggered": 0, "rule": "dry_run"},
    }
    return {
        "started_at": started_at,
        "duration_sec": round(time.time() - started_at, 3),
        "dry_run": True,
        "config": _safe_soak_config(args),
        "cycles": cycles,
        "ac_results": ac_results,
        "error_distribution": {"dry_run": n},
        "all_ac_pass": False,
        "exit_code": 0,
    }


def _read_json_evidence(value, field):
    if isinstance(value, Mapping):
        return dict(value)
    try:
        result = json.loads(Path(value).read_text(encoding="utf-8"))
    except (OSError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{field} must be a readable JSON object") from exc
    if not isinstance(result, dict):
        raise ValueError(f"{field} must contain a JSON object")
    return result


def _candidate_identity(args):
    try:
        config = json.loads(args.config_json)
    except (AttributeError, json.JSONDecodeError) as exc:
        raise ValueError("config_json must contain a JSON object") from exc
    if not isinstance(config, dict):
        raise ValueError("config_json must contain a JSON object")
    return build_candidate_identity(
        args.candidate_git_sha,
        args.candidate_image_digest,
        args.firmware_identity,
        config,
        args.fixture_sha256,
    )


def _lesson_manifest_digest(value):
    manifest = _read_json_evidence(value, "lesson_manifest")
    encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _normalized_key(value):
    return re.sub(r"[^a-zA-Z0-9]", "", str(value)).lower()


def _forbidden_evidence_fields(value, path=""):
    hits = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            item_path = f"{path}.{key}" if path else str(key)
            if _normalized_key(key) in _FORBIDDEN_EVIDENCE_KEYS:
                hits.append(item_path)
            else:
                hits.extend(_forbidden_evidence_fields(item, item_path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            hits.extend(_forbidden_evidence_fields(item, f"{path}[{index}]"))
    return hits


def _finite_nonnegative(value):
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value) and value >= 0


def _evidence_reuse_key(*, journey_id, connection_id, log_window):
    if (
        not isinstance(journey_id, str)
        or not journey_id
        or not isinstance(connection_id, str)
        or not connection_id
        or not isinstance(log_window, Mapping)
    ):
        return None
    window_values = tuple(log_window.get(field) for field in ("windowId", "start", "end"))
    if any(not isinstance(value, str) or not value for value in window_values):
        return None
    return (journey_id, connection_id, *window_values)


def _validate_upstream_layer(report, *, name, identity, failures, status="PASS"):
    if not isinstance(report, Mapping):
        failures.append({"code": "UPSTREAM_LAYER_MALFORMED", "layer": name})
        return
    if report.get("schemaVersion") != SCHEMA_VERSION or report.get("name") != name:
        failures.append({"code": "UPSTREAM_LAYER_MALFORMED", "layer": name})
    if report.get("status") != status:
        failures.append({"code": "UPSTREAM_LAYER_NOT_PASSING", "layer": name})
    if report.get("candidateIdentity") != identity:
        failures.append({"code": "CANDIDATE_IDENTITY_MISMATCH", "layer": name})
    if _forbidden_evidence_fields(report):
        failures.append({"code": "FORBIDDEN_EVIDENCE_FIELD", "layer": name})


def _validated_quiet_padding(
    value,
    *,
    identity,
    expected_connection_id,
    previous_end,
    gap_budget_sec,
    seen_journeys,
    seen_windows,
    seen_utc_windows,
):
    if not isinstance(value, Mapping) or _forbidden_evidence_fields(value):
        return None
    log_window = value.get("logWindow")
    start_utc = (
        _parse_utc_iso(log_window.get("start"))
        if isinstance(log_window, Mapping)
        else None
    )
    end_utc = (
        _parse_utc_iso(log_window.get("end"))
        if isinstance(log_window, Mapping)
        else None
    )
    duration = (
        (end_utc - start_utc).total_seconds()
        if start_utc is not None and end_utc is not None
        else None
    )
    gap = (
        (start_utc - previous_end).total_seconds()
        if start_utc is not None and previous_end is not None
        else None
    )
    journey_id = value.get("journeyId")
    window_id = value.get("windowId")
    utc_window = (start_utc, end_utc)
    expected_scope = {
        "journeyId": journey_id,
        "connectionId": expected_connection_id,
        "serverStartUtc": log_window.get("start")
        if isinstance(log_window, Mapping)
        else None,
    }
    valid = (
        value.get("schemaVersion") == SCHEMA_VERSION
        and value.get("name") == "quiet_padding"
        and value.get("status") == "PASS"
        and value.get("candidateIdentity") == identity
        and value.get("connectionId") == expected_connection_id
        and value.get("serverIssued") is True
        and value.get("evidenceScope") == expected_scope
        and value.get("falseInterrupts") == 0
        and value.get("unexpectedFallbacks") == 0
        and isinstance(value.get("resourceVerdict"), Mapping)
        and value["resourceVerdict"].get("status") == "PASS"
        and value.get("logStatus") == "PASS"
        and duration is not None
        and duration > 0
        and _finite_nonnegative(value.get("durationSec"))
        and abs(float(value["durationSec"]) - duration) <= 1.0
        and gap is not None
        and 0 < gap <= gap_budget_sec
        and isinstance(journey_id, str)
        and bool(journey_id)
        and journey_id not in seen_journeys
        and isinstance(window_id, str)
        and bool(window_id)
        and isinstance(log_window, Mapping)
        and log_window.get("windowId") == window_id
        and window_id not in seen_windows
        and utc_window not in seen_utc_windows
    )
    if not valid:
        return None
    return dict(value), end_utc, utc_window


def _latency_metrics(executions):
    first_audio = []
    bargein = []
    reconnect = []
    for execution in executions:
        latencies = execution.get("latencies", {})
        if not isinstance(latencies, Mapping):
            continue
        for field, target in (
            ("firstAudioMs", first_audio),
            ("bargeinMs", bargein),
            ("reconnectRecoveryMs", reconnect),
        ):
            values = latencies.get(field, [])
            if not isinstance(values, list) or any(not _finite_nonnegative(value) for value in values):
                raise ValueError(f"{field} must contain finite non-negative numbers")
            target.extend(values)
    return {
        "firstAudioP50Ms": percentile(first_audio, 50),
        "firstAudioP95Ms": percentile(first_audio, 95),
        "bargeinP95Ms": percentile(bargein, 95),
        "reconnectRecoveryP95Ms": percentile(reconnect, 95),
    }


async def run_candidate_soak(
    args,
    *,
    journeys,
    sample_resources=sample_process_resources,
    clock=time.monotonic,
):
    """Run the fixed candidate workload and aggregate only bounded safe evidence."""
    identity = _candidate_identity(args)
    lesson_manifest_sha256 = _lesson_manifest_digest(args.lesson_manifest)
    failures = []

    def safe_sample():
        try:
            sample = sample_resources()
        except Exception as exc:
            failures.append(
                {"code": "RESOURCE_EVIDENCE_MALFORMED", "errorClass": type(exc).__name__}
            )
            return {}
        if not isinstance(sample, Mapping):
            failures.append({"code": "RESOURCE_EVIDENCE_MALFORMED"})
            return {}
        return dict(sample)

    started = clock()
    samples = [safe_sample()]
    executions = []
    seen_journeys = set()
    seen_windows = set()
    seen_utc_windows = set()
    seen_evidence_keys = set()
    expected_sequence = 1
    first_window_start = None
    previous_window_end = None
    last_window_end = None
    gap_budget_sec = float(getattr(args, "evidence_gap_budget_sec", 10.0))
    if not math.isfinite(gap_budget_sec) or not 0 < gap_budget_sec <= 10.0:
        failures.append({"code": "EVIDENCE_GAP_BUDGET_INVALID"})
    maximum_padding_windows = int(getattr(args, "maximum_padding_windows", 60))

    for stage_name, count in _CANDIDATE_STAGE_COUNTS:
        callable_name = "conversation" if stage_name == "conversation_after_lesson" else stage_name
        journey = journeys.get(callable_name)
        if not callable(journey):
            failures.append({"code": "JOURNEY_CALLABLE_MISSING", "stage": stage_name})
            break
        for index in range(1, count + 1):
            try:
                result = await journey(
                    args,
                    name=stage_name,
                    index=index,
                    label="conversation_after_lesson" if stage_name == "conversation_after_lesson" else None,
                )
            except Exception as exc:
                failures.append(
                    {"code": "JOURNEY_EXECUTION_FAILED", "stage": stage_name, "errorClass": type(exc).__name__}
                )
                break
            samples.append(safe_sample())
            if not isinstance(result, Mapping):
                failures.append({"code": "JOURNEY_EVIDENCE_MALFORMED", "stage": stage_name})
                break
            result = dict(result)
            forbidden = _forbidden_evidence_fields(result)
            if forbidden:
                failures.append({"code": "FORBIDDEN_EVIDENCE_FIELD", "stage": stage_name, "fields": forbidden})
            if result.get("schemaVersion") != SCHEMA_VERSION or result.get("name") != stage_name:
                failures.append({"code": "JOURNEY_EVIDENCE_MALFORMED", "stage": stage_name})
            if result.get("status") != "PASS":
                failures.append({"code": "JOURNEY_NOT_PASSING", "stage": stage_name})
            if result.get("candidateIdentity") != identity:
                failures.append({"code": "CANDIDATE_IDENTITY_MISMATCH", "stage": stage_name})
            if result.get("evidenceSequence") != expected_sequence:
                failures.append({"code": "EVIDENCE_SEQUENCE_INVALID", "stage": stage_name})
            expected_sequence += 1
            journey_id = result.get("journeyId")
            window_id = result.get("windowId")
            if not isinstance(journey_id, str) or not journey_id or journey_id in seen_journeys:
                failures.append({"code": "EVIDENCE_JOURNEY_REUSED", "stage": stage_name})
            else:
                seen_journeys.add(journey_id)
            if not isinstance(window_id, str) or not window_id or window_id in seen_windows:
                failures.append({"code": "EVIDENCE_WINDOW_REUSED", "stage": stage_name})
            else:
                seen_windows.add(window_id)
            connection_id = result.get("connectionId")
            log_window = result.get("logWindow")
            start_utc = (
                _parse_utc_iso(log_window.get("start"))
                if isinstance(log_window, Mapping)
                else None
            )
            end_utc = (
                _parse_utc_iso(log_window.get("end"))
                if isinstance(log_window, Mapping)
                else None
            )
            utc_window = (start_utc, end_utc)
            if (
                not isinstance(log_window, Mapping)
                or log_window.get("windowId") != window_id
                or start_utc is None
                or end_utc is None
                or end_utc <= start_utc
            ):
                failures.append({"code": "EVIDENCE_UTC_WINDOW_INVALID", "stage": stage_name})
            else:
                if utc_window in seen_utc_windows:
                    failures.append({"code": "EVIDENCE_UTC_WINDOW_REUSED", "stage": stage_name})
                else:
                    seen_utc_windows.add(utc_window)
                gap_sec = (
                    (start_utc - previous_window_end).total_seconds()
                    if previous_window_end is not None
                    else 0.0
                )
                if previous_window_end is not None and (
                    gap_sec <= 0 or gap_sec > gap_budget_sec
                ):
                    failures.append({"code": "EVIDENCE_UTC_WINDOW_INVALID", "stage": stage_name})
                else:
                    first_window_start = first_window_start or start_utc
                    previous_window_end = end_utc
                    last_window_end = end_utc
            evidence_key = _evidence_reuse_key(
                journey_id=journey_id,
                connection_id=connection_id,
                log_window=log_window,
            )
            if evidence_key is None:
                failures.append({"code": "EVIDENCE_SCOPE_MALFORMED", "stage": stage_name})
            elif evidence_key in seen_evidence_keys:
                failures.append({"code": "EVIDENCE_SCOPE_REUSED", "stage": stage_name})
            else:
                seen_evidence_keys.add(evidence_key)
            if stage_name == "lesson" and result.get("lessonManifestSha256") != lesson_manifest_sha256:
                failures.append({"code": "LESSON_MANIFEST_MISMATCH"})
            executions.append(result)
        if failures:
            break

    elapsed = clock() - started
    minimum_duration = float(args.minimum_duration_sec)
    padding_evidence = []
    proven_duration = (
        (last_window_end - first_window_start).total_seconds()
        if first_window_start is not None and last_window_end is not None
        else 0.0
    )
    replay_mode = bool(getattr(args, "replay_candidate_evidence", False))
    if (proven_duration < minimum_duration or (elapsed < minimum_duration and not replay_mode)) and not failures:
        monitor = journeys.get("monitor")
        if callable(monitor):
            try:
                observed_padding = await monitor(
                    args,
                    duration_sec=max(
                        0.0,
                        minimum_duration - proven_duration,
                        minimum_duration - elapsed,
                    ),
                )
            except Exception as exc:
                failures.append(
                    {"code": "MONITORED_DURATION_FAILED", "errorClass": type(exc).__name__}
                )
                observed_padding = []
            if (
                not isinstance(observed_padding, list)
                or not observed_padding
                or len(observed_padding) > maximum_padding_windows
            ):
                failures.append({"code": "QUIET_PADDING_INVALID"})
            else:
                expected_connection = executions[-1].get("connectionId")
                for padding in observed_padding:
                    validated = _validated_quiet_padding(
                        padding,
                        identity=identity,
                        expected_connection_id=expected_connection,
                        previous_end=last_window_end,
                        gap_budget_sec=gap_budget_sec,
                        seen_journeys=seen_journeys,
                        seen_windows=seen_windows,
                        seen_utc_windows=seen_utc_windows,
                    )
                    if validated is None:
                        failures.append({"code": "QUIET_PADDING_INVALID"})
                        continue
                    safe_padding, last_window_end, utc_window = validated
                    seen_journeys.add(safe_padding["journeyId"])
                    seen_windows.add(safe_padding["windowId"])
                    seen_utc_windows.add(utc_window)
                    padding_evidence.append(safe_padding)
                    samples.append(safe_sample())
                elapsed = clock() - started
        else:
            failures.append({"code": "QUIET_PADDING_INVALID"})
    cleanup = journeys.get("cleanup")
    if callable(cleanup):
        try:
            await cleanup(args)
        except Exception as exc:
            failures.append({"code": "JOURNEY_CLEANUP_FAILED", "errorClass": type(exc).__name__})
    samples.append(safe_sample())

    upstream = {}
    for field in (
        "real_api_report",
        "transport_report",
        "correlated_transport_report",
        "log_reliability_report",
    ):
        try:
            upstream[field] = _read_json_evidence(getattr(args, field), field)
        except ValueError:
            failures.append({"code": "UPSTREAM_LAYER_MALFORMED", "layer": field})
            upstream[field] = {}
    real_api = upstream["real_api_report"]
    transport = upstream["transport_report"]
    correlated = upstream["correlated_transport_report"]
    log_report = upstream["log_reliability_report"]
    _validate_upstream_layer(real_api, name="real_api", identity=identity, failures=failures)
    _validate_upstream_layer(
        transport,
        name="websocket_audio_bargein_transport",
        identity=identity,
        failures=failures,
        status="SKIPPED",
    )
    if (
        transport.get("pendingCode") != "PENDING_BOUNDED_SERVER_LOG_VERIFICATION"
        or transport.get("aggregateReleaseEligible") is not False
    ):
        failures.append({"code": "RAW_TRANSPORT_CONTRACT_INVALID"})
    _validate_upstream_layer(
        correlated, name="websocket_audio_bargein_correlated", identity=identity, failures=failures
    )
    if correlated.get("aggregateReleaseEligible") is not True or correlated.get("correlationStatus") != "PASS":
        failures.append({"code": "CORRELATED_TRANSPORT_NOT_ELIGIBLE"})
    _validate_upstream_layer(log_report, name="google_live_log_reliability", identity=identity, failures=failures)
    log_contract = {
        "receiveLoopBalance": 0,
        "staleAudioAfterReplacement": 0,
        "unrecoveredTimeouts": [],
        "unreleasedLessonHandoffs": [],
        "fatalHits": [],
        "failures": [],
    }
    if any(log_report.get(key) != expected for key, expected in log_contract.items()) or log_report.get(
        "maxReceiveLoopsActive"
    ) not in {0, 1}:
        failures.append({"code": "LOG_RELIABILITY_CONTRACT_INVALID"})
    normalized_correlation = correlate_websocket_bargein_evidence(
        transport,
        log_report,
        expected_candidate_identity=identity,
    )
    correlated_contract_fields = (
        "schemaVersion",
        "name",
        "status",
        "candidateIdentity",
        "journeyId",
        "evidenceScope",
        "initialLiveConnectionId",
        "finalLiveConnectionId",
        "liveConnectionTransitions",
        "logWindow",
        "correlationSource",
        "correlationStatus",
        "aggregateReleaseEligible",
    )
    if normalized_correlation.get("status") != "PASS" or any(
        correlated.get(field) != normalized_correlation.get(field)
        for field in correlated_contract_fields
    ):
        failures.append({"code": "TASK5_CORRELATED_EVIDENCE_INVALID"})
    upstream_scope = correlated.get("evidenceScope")
    upstream_connection = (
        upstream_scope.get("connectionId") if isinstance(upstream_scope, Mapping) else None
    )
    upstream_window = correlated.get("logWindow")
    upstream_window_id = (
        upstream_window.get("windowId") if isinstance(upstream_window, Mapping) else None
    )
    upstream_utc_window = (
        (
            _parse_utc_iso(upstream_window.get("start")),
            _parse_utc_iso(upstream_window.get("end")),
        )
        if isinstance(upstream_window, Mapping)
        else None
    )
    upstream_key = _evidence_reuse_key(
        journey_id=correlated.get("journeyId"),
        connection_id=upstream_connection,
        log_window=upstream_window,
    )
    if upstream_key is None:
        failures.append({"code": "UPSTREAM_EVIDENCE_SCOPE_MISMATCH"})
    elif (
        correlated.get("journeyId") in seen_journeys
        or upstream_window_id in seen_windows
        or upstream_utc_window in seen_utc_windows
        or upstream_key in seen_evidence_keys
    ):
        failures.append({"code": "UPSTREAM_EVIDENCE_REUSED"})

    total_fields = (
        "successfulTurns",
        "bargeins",
        "latestIntentSuccesses",
        "falseInterrupts",
        "unexpectedFallbacks",
    )
    totals = dict.fromkeys(total_fields, 0)
    for item in executions:
        for key in total_fields:
            value = item.get(key, 0)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                failures.append({"code": "JOURNEY_EVIDENCE_MALFORMED", "field": key})
                continue
            totals[key] += value
        expected_counts = {
            "successfulTurns": 0 if item.get("name") in {"quiet", "lesson"} else 1,
            "bargeins": 1 if item.get("name") == "bargein" else 0,
            "falseInterrupts": 0,
            "unexpectedFallbacks": 0,
        }
        latest_intent = item.get("latestIntentSuccesses", 0)
        if any(
            item.get(key, 0) != value for key, value in expected_counts.items()
        ) or latest_intent not in ({0, 1} if item.get("name") == "bargein" else {0}):
            failures.append(
                {"code": "JOURNEY_MULTIPLICITY_INVALID", "stage": item.get("name")}
            )
    totals["latestIntentSuccessRate"] = (
        round(totals["latestIntentSuccesses"] / totals["bargeins"], 3) if totals["bargeins"] else 0.0
    )
    if totals["successfulTurns"] < int(args.minimum_turns):
        failures.append({"code": "MINIMUM_TURNS_NOT_MET"})
    if totals["bargeins"] < int(args.bargein_cycles):
        failures.append({"code": "MINIMUM_BARGEINS_NOT_MET"})
    if totals["latestIntentSuccessRate"] < GOOGLE_LIVE_LIMITS["minimumLatestIntentSuccessRate"]:
        failures.append({"code": "LATEST_INTENT_RATE_BELOW_BUDGET"})
    if totals["falseInterrupts"]:
        failures.append({"code": "FALSE_INTERRUPT_OBSERVED"})
    if totals["unexpectedFallbacks"]:
        failures.append({"code": "UNEXPECTED_FALLBACK_OBSERVED"})
    proven_duration = (
        (last_window_end - first_window_start).total_seconds()
        if first_window_start is not None and last_window_end is not None
        else 0.0
    )
    claimed_duration = getattr(args, "candidate_evidence_duration_sec", None)
    if proven_duration < minimum_duration:
        failures.append({"code": "PROVEN_DURATION_NOT_MET"})
    if not replay_mode and elapsed < minimum_duration:
        failures.append({"code": "ACTUAL_DURATION_NOT_MET"})
    if claimed_duration is not None and (
        not _finite_nonnegative(claimed_duration)
        or abs(float(claimed_duration) - proven_duration) > 1.0
    ):
        failures.append({"code": "CLAIMED_DURATION_MISMATCH"})

    try:
        latency_metrics = _latency_metrics(executions)
    except ValueError:
        latency_metrics = {
            "firstAudioP50Ms": None,
            "firstAudioP95Ms": None,
            "bargeinP95Ms": None,
            "reconnectRecoveryP95Ms": None,
        }
        failures.append({"code": "LATENCY_EVIDENCE_MALFORMED"})
    try:
        baseline = _read_json_evidence(args.baseline_report, "baseline_report")
    except ValueError:
        baseline = {}
        failures.append({"code": "BASELINE_REPORT_MALFORMED"})
    latency_comparison = compare_latency_baseline(latency_metrics, baseline.get("latencyMetrics", {}))
    if not latency_comparison["pass"]:
        failures.append({"code": "LATENCY_REGRESSION"})
    hard_latency_pass = (
        _finite_nonnegative(latency_metrics["firstAudioP50Ms"])
        and latency_metrics["firstAudioP50Ms"] <= GOOGLE_LIVE_LIMITS["firstAudioP50Ms"]
        and _finite_nonnegative(latency_metrics["firstAudioP95Ms"])
        and latency_metrics["firstAudioP95Ms"] <= GOOGLE_LIVE_LIMITS["firstAudioP95Ms"]
        and _finite_nonnegative(latency_metrics["bargeinP95Ms"])
        and latency_metrics["bargeinP95Ms"] <= GOOGLE_LIVE_LIMITS["physicalBargeinP95Ms"]
    )
    if not hard_latency_pass:
        failures.append({"code": "HARD_LATENCY_BUDGET_FAILED"})
    try:
        resources = resource_verdict(samples)
    except (KeyError, TypeError, ValueError):
        resources = {"status": "FAIL", "failures": [{"code": "RESOURCE_EVIDENCE_MALFORMED"}]}
    if resources["status"] != "PASS":
        failures.append({"code": "RESOURCE_BUDGET_FAILED"})

    stages = [
        {
            "name": name,
            "executions": count,
            "status": "PASS" if sum(1 for item in executions if item.get("name") == name) == count else "FAIL",
        }
        for name, count in _CANDIDATE_STAGE_COUNTS
    ]
    report = {
        "schemaVersion": SCHEMA_VERSION,
        "name": "candidate_soak",
        "status": "PASS" if not failures else "FAIL",
        "candidateIdentity": identity,
        "durationSec": round(proven_duration, 3),
        "runtimeElapsedSec": round(elapsed, 3),
        "evidenceGapBudgetSec": gap_budget_sec,
        "evidenceAnchors": {
            "serverStartUtc": first_window_start.isoformat()
            if first_window_start is not None
            else None,
            "serverEndUtc": last_window_end.isoformat()
            if last_window_end is not None
            else None,
        },
        "quietPadding": [
            {
                "journeyId": item.get("journeyId"),
                "connectionId": item.get("connectionId"),
                "windowId": item.get("windowId"),
                "logWindow": item.get("logWindow"),
                "durationSec": item.get("durationSec"),
                "status": item.get("status"),
                "serverIssued": item.get("serverIssued"),
                "evidenceScope": item.get("evidenceScope"),
                "falseInterrupts": item.get("falseInterrupts"),
                "unexpectedFallbacks": item.get("unexpectedFallbacks"),
                "resourceVerdict": item.get("resourceVerdict"),
                "logStatus": item.get("logStatus"),
            }
            for item in padding_evidence
        ],
        "stages": stages,
        "evidenceExecutions": [
            {
                "sequence": item.get("evidenceSequence"),
                "stage": item.get("name"),
                "journeyId": item.get("journeyId"),
                "windowId": item.get("windowId"),
                "status": item.get("status"),
            }
            for item in executions
        ],
        "totals": totals,
        "latencyMetrics": latency_metrics,
        "latencyComparison": latency_comparison,
        "resourceVerdict": resources,
        "upstreamLayers": [
            {
                "name": layer.get("name"),
                "status": layer.get("status"),
                "candidateIdentity": layer.get("candidateIdentity"),
            }
            for layer in (real_api, transport, correlated, log_report)
        ],
        "failures": failures,
        "rawAudioPersisted": False,
        "transcriptPersisted": False,
        "exit_code": 0 if not failures else 1,
    }
    return redact_mapping(report)


async def run_soak(args):
    if getattr(args, "scenario", None) == "tvideo-farm":
        if getattr(args, "dry_run", False):
            return _dry_run_tvideo_farm_report(args)
        return await _run_tvideo_farm_scenario(args)
    mode = getattr(args, "mode", None)
    if mode == "candidate":
        journeys = getattr(args, "candidate_journeys", None)
        candidate_sampler = sample_process_resources
        if not isinstance(journeys, Mapping):
            manifest = _read_json_evidence(args.journey_evidence, "journey_evidence")
            if _forbidden_evidence_fields(manifest):
                raise ValueError("candidate evidence contains forbidden fields")
            recorded = manifest.get("executions")
            if not isinstance(recorded, list):
                raise ValueError("journey_evidence executions must be a list")
            expected_count = sum(count for _name, count in _CANDIDATE_STAGE_COUNTS)
            if len(recorded) != expected_count:
                raise ValueError("journey evidence must contain exactly 33 executions")
            evidence_duration = manifest.get("durationSec")
            if not _finite_nonnegative(evidence_duration):
                raise ValueError("journey evidence durationSec must be finite and non-negative")
            args.candidate_evidence_duration_sec = evidence_duration
            args.replay_candidate_evidence = True
            recorded_padding = manifest.get("quietPadding", [])
            if not isinstance(recorded_padding, list):
                raise ValueError("journey evidence quietPadding must be a list")
            recorded_samples = manifest.get("resourceSamples")
            expected_samples = expected_count + len(recorded_padding) + 2
            if not isinstance(recorded_samples, list) or len(recorded_samples) != expected_samples:
                raise ValueError(
                    f"journey evidence must contain exactly {expected_samples} resource samples"
                )
            sample_cursor = 0

            def recorded_sample():
                nonlocal sample_cursor
                if sample_cursor >= len(recorded_samples):
                    raise ValueError("resource evidence was over-consumed")
                sample = recorded_samples[sample_cursor]
                sample_cursor += 1
                if not isinstance(sample, Mapping):
                    raise ValueError("resource evidence sample must be an object")
                return dict(sample)

            candidate_sampler = recorded_sample

            cursor = 0

            async def recorded_journey(_args, *, name, **_kwargs):
                nonlocal cursor
                if cursor >= len(recorded):
                    raise ValueError("journey evidence is incomplete")
                evidence = recorded[cursor]
                cursor += 1
                if not isinstance(evidence, Mapping) or evidence.get("name") != name:
                    raise ValueError("journey evidence order does not match candidate sequence")
                return dict(evidence)

            journeys = dict.fromkeys(
                ("conversation", "bargein", "quiet", "reopen", "reconnect", "lesson"),
                recorded_journey,
            )

            async def recorded_cleanup(_args):
                if cursor != len(recorded):
                    raise ValueError("journey evidence was not fully consumed")

            journeys["cleanup"] = recorded_cleanup

            async def recorded_monitor(_args, *, duration_sec):
                return [dict(item) if isinstance(item, Mapping) else item for item in recorded_padding]

            journeys["monitor"] = recorded_monitor
        return await run_candidate_soak(
            args,
            journeys=journeys,
            sample_resources=candidate_sampler,
        )
    if mode == "false_positive":
        return await _run_false_positive_mode(args)
    if mode == "bargein_latency":
        return await _run_bargein_latency_mode(args)
    if mode == "rapid_interrupt":
        return await _run_rapid_interrupt_mode(args)

    if getattr(args, "dry_run", False):
        return _dry_run_report(args)

    headers = {
        "device-id": args.device_mac if args.device_mac else args.device_id,
        "client-id": args.client_id,
    }
    log_tail = LogTail(Path(args.log_path) if args.log_path else None)
    initial_offset = log_tail._start_offset
    started_at = time.time()

    cycles = []
    async with websockets.connect(
        args.websocket_url,
        additional_headers=headers,
        open_timeout=args.open_timeout_sec,
        max_size=None,
    ) as websocket:
        await websocket.send(json.dumps(_hello_message()))
        hello_payload, _, _ = await _recv_until(
            websocket,
            lambda payload: payload.get("type") == "hello",
            args.event_timeout_sec,
        )
        if hello_payload is None:
            raise RuntimeError("hello ack timeout")

        for index in range(args.bargein_cycles):
            cycle = await _run_bargein_cycle(websocket, index, args, log_tail)
            cycles.append(cycle)
            print(
                "BARGEIN_CYCLE outcome={outcome} "
                "first_audio_ms={fa} bargein_latency_ms={bl} "
                "transcript={tx} new_id={ni} cancelled_id={ci}".format(
                    outcome=cycle["outcome"],
                    fa=cycle["first_audio_latency_ms"],
                    bl=cycle["bargein_latency_ms"],
                    tx=cycle["user_transcript_received"],
                    ni=cycle["new_response_id"],
                    ci=cycle["cancelled_response_id"],
                )
            )

        for index in range(args.idle_cycles):
            cycle = await _run_idle_cycle(websocket, index, args, log_tail)
            cycles.append(cycle)
            print(
                "IDLE_CYCLE outcome={outcome} false_positives={fp}".format(
                    outcome=cycle["outcome"],
                    fp=cycle["false_positive_interrupts"],
                )
            )

        await websocket.close()

    full_log = ""
    if log_tail.log_path and initial_offset is not None:
        try:
            with log_tail.log_path.open("rb") as fh:
                fh.seek(initial_offset)
                full_log = fh.read().decode("utf-8", errors="replace")
        except OSError:
            full_log = ""

    ac_results = _summarize_acs(cycles, full_log, args)
    error_distribution = dict(Counter(err for cycle in cycles for err in cycle.get("errors", [])))

    all_pass = all(ac["pass"] for ac in ac_results.values())
    report = {
        "started_at": started_at,
        "duration_sec": round(time.time() - started_at, 1),
        "dry_run": False,
        "config": _safe_soak_config(args),
        "cycles": cycles,
        "ac_results": ac_results,
        "error_distribution": error_distribution,
        "all_ac_pass": all_pass,
        "exit_code": 0 if all_pass else 1,
    }
    return report


def _build_argument_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scenario",
        choices=["tvideo-farm"],
        default=None,
        help="bounded lesson validation scenario; tvideo-farm is credential-gated",
    )
    # Mode selector for PR5 §6.4 modes
    parser.add_argument(
        "--mode",
        choices=["false_positive", "bargein_latency", "rapid_interrupt", "candidate"],
        default=None,
        help=(
            "false_positive: AC1 soliloquy false-positive count; "
            "bargein_latency: AC2 T0-T2 latency measurement; "
            "rapid_interrupt: AC4 double-interrupt debounce check"
        ),
    )
    parser.add_argument(
        "--duration", type=int, default=300, help="duration in seconds for --mode false_positive (default 300)"
    )
    parser.add_argument(
        "--trials", type=int, default=10, help="number of trials for --mode bargein_latency / rapid_interrupt"
    )
    parser.add_argument(
        "--env",
        default="unknown",
        choices=["quiet", "music", "chatter", "unknown"],
        help="acoustic environment label for --mode false_positive",
    )
    parser.add_argument(
        "--skip-firmware-timing",
        action="store_true",
        default=True,
        help="skip T3/T4 firmware UART timing (software-only run; default: True)",
    )
    # Primary URL arg (spec name --ws-url; --websocket-url kept for backward compat)
    parser.add_argument("--ws-url", "--websocket-url", dest="websocket_url", default="ws://localhost:8000/xiaozhi/v1")
    # Device identity — spec uses --device-mac; --device-id kept for backward compat
    parser.add_argument(
        "--device-mac",
        "--device-id",
        dest="device_mac",
        default=None,
        help="Device MAC / device-id header sent in websocket handshake",
    )
    parser.add_argument(
        "--client-id", default="soak-harness-client", help="client-id header (optional for text-mode soak)"
    )
    parser.add_argument(
        "--log-path", default="tmp/server.log", help="path to server.log for AC3/AC4 log-tail validation"
    )
    # Cycle counts — --cycles sets bargein-cycles (spec §8 primary knob)
    parser.add_argument(
        "--cycles",
        "--bargein-cycles",
        dest="bargein_cycles",
        type=int,
        default=10,
        help="number of barge-in Q&A cycles (spec §8 default 10)",
    )
    parser.add_argument("--idle-cycles", type=int, default=1)
    # Per-cycle max wall-clock (spec §8 --duration-sec)
    parser.add_argument(
        "--duration-sec",
        dest="event_timeout_sec",
        type=float,
        default=600,
        help="per-cycle max wall-clock in seconds (spec §8 default 600)",
    )
    # Audio injection (spec §8)
    parser.add_argument(
        "--inject-audio",
        dest="inject_audio",
        default=None,
        help="synthetic or consenting-adult WAV; never use child recordings",
    )
    parser.add_argument(
        "--audio-source",
        choices=["synthetic", "adult"],
        default="synthetic",
        help="privacy provenance for injected audio; child audio is forbidden",
    )
    parser.add_argument(
        "--server-has-google-live-credentials",
        action="store_true",
        help=("run against a server whose manager/private config already supplies Google Live credentials"),
    )
    parser.add_argument(
        "--inject-text",
        dest="inject_text",
        default=None,
        help="text injection for interrupt (default: use --interrupt-prompt)",
    )
    # Prompts
    parser.add_argument("--first-prompt", default=DEFAULT_FIRST_PROMPT)
    parser.add_argument("--interrupt-prompt", default=DEFAULT_INTERRUPT_PROMPT)
    parser.add_argument("--idle-prompt", default=DEFAULT_IDLE_PROMPT)
    # Timing knobs
    parser.add_argument("--speak-for-sec", type=float, default=3.0)
    parser.add_argument("--idle-duration-sec", type=float, default=120.0)
    parser.add_argument("--open-timeout-sec", type=float, default=10.0)
    parser.add_argument("--interrupt-timeout-sec", type=float, default=3.0)
    parser.add_argument("--settle-timeout-sec", type=float, default=30.0)
    parser.add_argument("--bargein-latency-budget-ms", type=float, default=500.0)
    parser.add_argument("--ac1-goaway-budget", type=int, default=0)
    parser.add_argument("--candidate-git-sha", default=None)
    parser.add_argument("--candidate-image-digest", default=None)
    parser.add_argument("--firmware-identity", default=None)
    parser.add_argument("--fixture-sha256", default=None)
    parser.add_argument("--config-json", default="{}")
    parser.add_argument("--baseline-report", type=Path, default=None)
    parser.add_argument("--real-api-report", type=Path, default=None)
    parser.add_argument("--transport-report", type=Path, default=None)
    parser.add_argument("--correlated-transport-report", type=Path, default=None)
    parser.add_argument("--log-reliability-report", type=Path, default=None)
    parser.add_argument("--journey-evidence", type=Path, default=None)
    parser.add_argument("--minimum-turns", type=int, default=30)
    parser.add_argument("--minimum-duration-sec", type=float, default=1800.0)
    parser.add_argument("--evidence-gap-budget-sec", type=float, default=10.0)
    parser.add_argument("--maximum-padding-windows", type=int, default=60)
    parser.add_argument("--lesson-manifest", type=Path, default=None)
    # Output
    parser.add_argument("--report", type=Path, default=None, help="write JSON report to this path (required for CI)")
    # Dry-run: validate args + emit placeholder report, no websocket connect
    parser.add_argument(
        "--dry-run", action="store_true", help="skip websocket connect; emit placeholder report for CI smoke"
    )
    return parser


def _validate_candidate_args(parser, args):
    if args.mode != "candidate":
        return
    required = (
        "candidate_git_sha",
        "candidate_image_digest",
        "firmware_identity",
        "fixture_sha256",
        "baseline_report",
        "real_api_report",
        "transport_report",
        "correlated_transport_report",
        "log_reliability_report",
        "journey_evidence",
        "lesson_manifest",
        "report",
    )
    missing = [field for field in required if not getattr(args, field, None)]
    if missing:
        parser.error("candidate mode requires: " + ", ".join(missing))
    if args.minimum_turns < GOOGLE_LIVE_LIMITS["minimumSoakTurns"]:
        parser.error("candidate mode minimum-turns cannot be below 30")
    if args.minimum_duration_sec < GOOGLE_LIVE_LIMITS["minimumSoakDurationSec"]:
        parser.error("candidate mode minimum-duration-sec cannot be below 1800")
    if args.bargein_cycles != GOOGLE_LIVE_LIMITS["minimumBargeins"]:
        parser.error("candidate mode requires exactly 10 barge-in cycles")
    if not math.isfinite(args.evidence_gap_budget_sec) or not 0 < args.evidence_gap_budget_sec <= 10.0:
        parser.error("candidate mode evidence-gap-budget-sec must be in (0, 10]")
    if args.maximum_padding_windows < 1 or args.maximum_padding_windows > 60:
        parser.error("candidate mode maximum-padding-windows must be in [1, 60]")


def _candidate_failure_report(args, error):
    try:
        identity = _candidate_identity(args)
    except (AttributeError, TypeError, ValueError):
        identity = {}
    return {
        "schemaVersion": SCHEMA_VERSION,
        "name": "candidate_soak",
        "status": "FAIL",
        "candidateIdentity": identity,
        "failures": [
            {
                "code": "CANDIDATE_SOAK_EXECUTION_FAILED",
                "errorClass": type(error).__name__,
            }
        ],
        "rawAudioPersisted": False,
        "transcriptPersisted": False,
        "exit_code": 1,
    }


def main():
    parser = _build_argument_parser()
    args = parser.parse_args()
    _validate_candidate_args(parser, args)

    # --inject-text overrides --interrupt-prompt when provided
    if args.inject_text:
        args.interrupt_prompt = args.inject_text

    # device_id alias for backward compat in _run_bargein_cycle / _run_idle_cycle
    args.device_id = args.device_mac or "unknown"

    if args.scenario == "tvideo-farm" and not args.dry_run:
        skipped = _credential_gated_tvideo_farm_report(args)
        if skipped is not None:
            if args.report:
                args.report.parent.mkdir(parents=True, exist_ok=True)
                args.report.write_text(json.dumps(skipped, indent=2))
            print("SKIP_GOOGLE_LIVE_CREDENTIALS")
            return 0

    try:
        report = asyncio.run(run_soak(args))
    except Exception as exc:
        if args.mode == "candidate" and args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(
                json.dumps(redact_mapping(_candidate_failure_report(args, exc)), indent=2)
                + "\n",
                encoding="utf-8",
            )
        detail = type(exc).__name__ if args.mode == "candidate" else str(exc)
        print(f"SOAK_FAIL {detail}", file=sys.stderr)
        return 1

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, default=str))
        print(f"Wrote soak report: {args.report}")

    if report.get("dry_run"):
        print("DRY_RUN_OK schema validated, no server connection made")
    if "ac_results" in report:
        print(json.dumps(report["ac_results"], indent=2))
    return report.get("exit_code", 0 if report.get("all_ac_pass", False) else 1)


if __name__ == "__main__":
    raise SystemExit(main())
