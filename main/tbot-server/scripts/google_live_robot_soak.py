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
import base64
import hashlib
import hmac
import inspect
import json
import math
import os
import re
import secrets
import stat
import subprocess
import sys
import tempfile
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import wave
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
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
    _validate_log_reliability_contract,
    _validated_live_connection_transition_chain,
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
    validate_candidate_soak_report,
)
from scripts.voice_mode_websocket_audio_bargein import (  # noqa: E402
    _collect_replacement_response,
    _drain_preflight_terminal,
    _observe_interrupt_stop,
    _opus_packets,
    _opus_packets_from_audio_file,
    _opus_packets_from_pcm,
)
from scripts.voice_mode_websocket_soak import (  # noqa: E402
    _build_headers,
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
_CANDIDATE_LATENCY_METRICS = (
    "firstAudioP50Ms",
    "firstAudioP95Ms",
    "bargeinP95Ms",
    "reconnectRecoveryP95Ms",
)
_OWNED_CLEANUP_TASKS = set()
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
_SENSITIVE_EVIDENCE_VALUE_RE = re.compile(
    r"(?i)(?:\bbearer\s+\S+|\bauthorization\s*[:=]|\b(?:set-)?cookie\s*[:=]|"
    r"\b(?:api[_ -]?key|secret|token|session(?:resumption)?handle|transcript|"
    r"prompt|raw[_ -]?exception|raw[_ -]?audio)\s*[:=]|"
    r"\bAIza[0-9A-Za-z_-]{35}\b|"
    r"\beyJ[0-9A-Za-z_-]{5,}\.[0-9A-Za-z_-]{5,}\.[0-9A-Za-z_-]+\b|"
    r"\bsk-(?:proj-)?[0-9A-Za-z_-]{20,}\b|"
    r"\bAQEA[0-9A-Za-z_-]{32,}\b)"
)
_CANDIDATE_INTENT_VERSION = "google-live-candidate-intent-nfkc-casefold.v1"
_MAX_PROTECTED_INPUT_BYTES = 1024 * 1024
_MAX_PROTECTED_PCM_BYTES = 16 * 1024 * 1024
_MAX_QUIET_OBSERVATION_SEC = 600.0
_QUIET_DURATION_TOLERANCE_MS = 1000
_QUIET_WINDOW_TOLERANCE_SEC = 5.0


@dataclass(slots=True)
class _ProtectedAudioFixture:
    label: str
    pcm: bytearray = field(repr=False)


@dataclass(slots=True)
class _SealedBargeinPlan:
    key: bytearray = field(repr=False)
    initial_mac: bytearray = field(repr=False)
    newest_mac: bytearray = field(repr=False)

    def zeroize(self):
        for private in (self.key, self.initial_mac, self.newest_mac):
            for offset in range(len(private)):
                private[offset] = 0


@dataclass(slots=True)
class _CandidateProtectedInput:
    bargein_initial: _ProtectedAudioFixture
    bargein_newest: _ProtectedAudioFixture
    robot_speaking: _ProtectedAudioFixture
    initial_expected: bytearray | None = field(repr=False)
    newest_expected: bytearray | None = field(repr=False)
    bargein_plans: list[_SealedBargeinPlan] = field(default_factory=list, repr=False)

    def seal_bargein_plans(self, count):
        if self.bargein_plans:
            return
        if self.initial_expected is None or self.newest_expected is None:
            raise ValueError("protected candidate input expectations are unavailable")
        try:
            for _index in range(count):
                key = bytearray(secrets.token_bytes(32))
                self.bargein_plans.append(
                    _SealedBargeinPlan(
                        key=key,
                        initial_mac=bytearray(
                            hmac.new(key, self.initial_expected, hashlib.sha256)
                            .hexdigest()
                            .encode("ascii")
                        ),
                        newest_mac=bytearray(
                            hmac.new(key, self.newest_expected, hashlib.sha256)
                            .hexdigest()
                            .encode("ascii")
                        ),
                    )
                )
        finally:
            for private in (self.initial_expected, self.newest_expected):
                if private is not None:
                    for offset in range(len(private)):
                        private[offset] = 0
            self.initial_expected = None
            self.newest_expected = None

    def consume_bargein_plan(self):
        if not self.bargein_plans:
            raise ValueError("protected candidate input semantic plans are exhausted")
        return self.bargein_plans.pop(0)

    def zeroize(self):
        for private in (
            self.bargein_initial.pcm,
            self.bargein_newest.pcm,
            self.robot_speaking.pcm,
            self.initial_expected,
            self.newest_expected,
        ):
            if private is not None:
                for offset in range(len(private)):
                    private[offset] = 0
        for plan in self.bargein_plans:
            plan.zeroize()
        self.bargein_plans.clear()
        self.initial_expected = None
        self.newest_expected = None


def _open_regular_nofollow(path: Path) -> int:
    absolute = path if path.is_absolute() else Path.cwd() / path
    parts = absolute.parts
    if not parts or any(part in {"", ".", ".."} for part in parts[1:]):
        raise ValueError("protected candidate input fixture is invalid")
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(
        os, "O_NOFOLLOW", 0
    )
    directory_fd = os.open(parts[0], directory_flags)
    try:
        for component in parts[1:-1]:
            next_fd = os.open(component, directory_flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        descriptor = os.open(
            parts[-1],
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory_fd,
        )
    except BaseException:
        os.close(directory_fd)
        raise
    os.close(directory_fd)
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise ValueError("protected candidate input fixture is not regular")
    return descriptor


def _read_protected_wav(path: Path, *, sample_rate: int, label: str):
    descriptor = _open_regular_nofollow(path)
    try:
        identity = (os.fstat(descriptor).st_dev, os.fstat(descriptor).st_ino)
        with os.fdopen(os.dup(descriptor), "rb") as raw:
            with wave.open(raw, "rb") as source:
                if (
                    source.getnchannels() != 1
                    or source.getsampwidth() != 2
                    or source.getframerate() != sample_rate
                    or source.getcomptype() != "NONE"
                    or source.getnframes() <= 0
                ):
                    raise ValueError("protected candidate input WAV shape is unsupported")
                frame_count = source.getnframes()
                bytes_per_frame = source.getnchannels() * source.getsampwidth()
                if (
                    frame_count <= 0
                    or bytes_per_frame <= 0
                    or frame_count > _MAX_PROTECTED_PCM_BYTES // bytes_per_frame
                ):
                    raise ValueError("protected candidate input WAV shape is unsupported")
                expected_bytes = frame_count * bytes_per_frame
                pcm = source.readframes(frame_count)
        if not pcm or len(pcm) != expected_bytes:
            raise ValueError("protected candidate input fixture is empty")
        return _ProtectedAudioFixture(label=label, pcm=bytearray(pcm)), identity
    except (OSError, EOFError, wave.Error) as exc:
        raise ValueError("protected candidate input fixture is invalid") from exc
    finally:
        os.close(descriptor)


def _read_candidate_protected_input(stream, *, output_paths, sample_rate):
    fixtures = []
    try:
        raw = stream.read(_MAX_PROTECTED_INPUT_BYTES + 1)
        if not raw:
            raise ValueError("protected candidate input is missing")
        if len(raw) > _MAX_PROTECTED_INPUT_BYTES:
            raise ValueError("protected candidate input is too large")
        document = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
        if not isinstance(document, dict) or set(document) != {"bargein", "robotSpeaking"}:
            raise ValueError("protected candidate input schema is invalid")
        bargein = document["bargein"]
        speaking = document["robotSpeaking"]
        if (
            not isinstance(bargein, dict)
            or set(bargein)
            != {"initialAudioPath", "initialExpected", "newestAudioPath", "newestExpected"}
            or not isinstance(speaking, dict)
            or set(speaking) != {"triggerAudioPath"}
        ):
            raise ValueError("protected candidate input schema is invalid")
        values = (*bargein.values(), speaking["triggerAudioPath"])
        if any(not isinstance(value, str) or not value for value in values):
            raise ValueError("protected candidate input schema is invalid")
        identities = []
        for label, value in (
            ("bargein/initial", bargein["initialAudioPath"]),
            ("bargein/newest", bargein["newestAudioPath"]),
            ("robot_speaking/trigger", speaking["triggerAudioPath"]),
        ):
            fixture, identity = _read_protected_wav(
                Path(value), sample_rate=sample_rate, label=label
            )
            fixtures.append(fixture)
            identities.append(identity)
        if len(set(identities)) != len(identities):
            raise ValueError("protected candidate input fixture alias detected")
        for output_path in output_paths:
            if Path(output_path).is_symlink():
                raise ValueError("protected candidate input output alias detected")
            try:
                output_stat = os.stat(output_path, follow_symlinks=False)
            except FileNotFoundError:
                continue
            if (output_stat.st_dev, output_stat.st_ino) in identities:
                raise ValueError("protected candidate input output alias detected")
        initial_expected = _normalize_candidate_intent(bargein["initialExpected"])
        newest_expected = _normalize_candidate_intent(bargein["newestExpected"])
        if not initial_expected or not newest_expected:
            raise ValueError("protected candidate input expectation is invalid")
        if hmac.compare_digest(
            initial_expected.encode("utf-8"), newest_expected.encode("utf-8")
        ):
            raise ValueError("protected candidate input expectations must be distinct")
        return _CandidateProtectedInput(
            fixtures[0], fixtures[1], fixtures[2],
            bytearray(initial_expected.encode("utf-8")),
            bytearray(newest_expected.encode("utf-8")),
        )
    except BaseException as exc:
        for fixture in fixtures:
            for offset in range(len(fixture.pcm)):
                fixture.pcm[offset] = 0
        if isinstance(exc, ValueError) and str(exc).startswith(
            "protected candidate input"
        ):
            raise
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        raise ValueError("protected candidate input is invalid") from exc


def _normalize_candidate_intent(value):
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return " ".join(
        "".join(character if character.isalnum() else " " for character in normalized).split()
    )


def _candidate_semantic_counters(
    stage,
    log_evidence,
    *,
    quiet_mode=None,
    requested_duration_sec=None,
    window_duration_sec=None,
):
    semantic = (
        log_evidence.get("candidateSemanticEvidence")
        if isinstance(log_evidence, Mapping)
        else None
    )
    valid = isinstance(semantic, Mapping) and log_evidence.get("status") == "PASS"
    if stage == "bargein":
        valid = valid and semantic == {
            "status": "PASS",
            "kind": "bargein-intent",
            "initialSlotMatched": True,
            "newestSlotMatched": True,
            "orderingValid": True,
            "latestIntentMatched": True,
            "replacementOwnedByNewestGeneration": True,
        }
        if not valid:
            raise RuntimeError("candidate semantic evidence is invalid")
        return {"latestIntentSuccesses": 1, "falseInterrupts": 0}
    if stage == "quiet":
        expected_responses = 0 if quiet_mode == "silence" else 1
        expected_duration_ms = requested_duration_sec * 1000
        observed_duration_ms = semantic.get("durationMs") if valid else None
        valid = (
            valid
            and set(semantic)
            == {
                "status", "kind", "mode", "durationMs", "falseInterrupts",
                "responseStarts", "responseEnds", "replacements", "fallbacks",
            }
            and semantic.get("status") == "PASS"
            and semantic.get("kind") == "quiet"
            and semantic.get("mode") == quiet_mode
            and type(observed_duration_ms) is int
            and expected_duration_ms <= observed_duration_ms
            <= expected_duration_ms + _QUIET_DURATION_TOLERANCE_MS
            and isinstance(window_duration_sec, (int, float))
            and not isinstance(window_duration_sec, bool)
            and math.isfinite(window_duration_sec)
            and window_duration_sec >= requested_duration_sec
            and window_duration_sec
            <= requested_duration_sec + _QUIET_WINDOW_TOLERANCE_SEC
            and semantic.get("falseInterrupts") == 0
            and semantic.get("responseStarts") == expected_responses
            and semantic.get("responseEnds") == expected_responses
            and semantic.get("replacements") == 0
            and semantic.get("fallbacks") == 0
        )
        if not valid:
            raise RuntimeError("candidate semantic evidence is invalid")
        return {"latestIntentSuccesses": 0, "falseInterrupts": 0}
    return {"latestIntentSuccesses": 0, "falseInterrupts": 0}

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


def _atomic_write_json(path: Path, value: Mapping) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def _open_pinned_parent(path: Path):
    absolute = path if path.is_absolute() else Path.cwd() / path
    parts = absolute.parts
    if not parts or any(part in {"", ".", ".."} for part in parts[1:]):
        raise ValueError("candidate evidence output path is invalid")
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(
        os, "O_NOFOLLOW", 0
    )
    directory_fd = os.open(parts[0], directory_flags)
    ancestry = [
        (os.fstat(directory_fd).st_dev, os.fstat(directory_fd).st_ino)
    ]
    try:
        for component in parts[1:-1]:
            try:
                next_fd = os.open(
                    component, directory_flags, dir_fd=directory_fd
                )
            except FileNotFoundError:
                os.mkdir(component, 0o755, dir_fd=directory_fd)
                next_fd = os.open(
                    component, directory_flags, dir_fd=directory_fd
                )
            os.close(directory_fd)
            directory_fd = next_fd
            opened = os.fstat(directory_fd)
            ancestry.append((opened.st_dev, opened.st_ino))
        return absolute, directory_fd, tuple(ancestry)
    except BaseException:
        os.close(directory_fd)
        raise


def _pinned_parent_path_matches(path: Path, ancestry) -> bool:
    parts = path.parts
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(
        os, "O_NOFOLLOW", 0
    )
    directory_fd = os.open(parts[0], directory_flags)
    try:
        opened = os.fstat(directory_fd)
        observed = [(opened.st_dev, opened.st_ino)]
        for component in parts[1:]:
            next_fd = os.open(component, directory_flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
            opened = os.fstat(directory_fd)
            observed.append((opened.st_dev, opened.st_ino))
        return tuple(observed) == tuple(ancestry)
    except OSError:
        return False
    finally:
        os.close(directory_fd)


def _atomic_write_json_exclusive(path: Path, value: Mapping):
    path, directory_fd, ancestry = _open_pinned_parent(Path(path))
    temporary_name = f".{path.name}.{secrets.token_hex(12)}.tmp"
    descriptor = None
    written_stat = None
    published = False
    target_linked = False
    try:
        descriptor = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=directory_fd,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8", closefd=False) as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        written_stat = os.fstat(descriptor)
        os.link(
            temporary_name,
            path.name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
            follow_symlinks=False,
        )
        target_linked = True
        target_fd = os.open(
            path.name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory_fd,
        )
        try:
            target_stat = os.fstat(target_fd)
            if (
                (written_stat.st_dev, written_stat.st_ino)
                != (target_stat.st_dev, target_stat.st_ino)
                or target_stat.st_nlink != 2
            ):
                raise RuntimeError("candidate evidence output identity changed")
            with os.fdopen(target_fd, "r", encoding="utf-8", closefd=False) as target:
                reopened = json.load(target)
        finally:
            os.close(target_fd)
        os.unlink(temporary_name, dir_fd=directory_fd)
        temporary_name = None
        final_stat = os.stat(
            path.name,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
        if (
            (final_stat.st_dev, final_stat.st_ino)
            != (written_stat.st_dev, written_stat.st_ino)
            or final_stat.st_nlink != 1
        ):
            raise RuntimeError("candidate evidence output alias detected")
        if not _pinned_parent_path_matches(path.parent, ancestry):
            raise RuntimeError("candidate evidence parent changed")
        os.fsync(directory_fd)
        published = True
        return reopened
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary_name is not None:
            try:
                os.unlink(temporary_name, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
        if target_linked and not published:
            try:
                current_target_stat = os.stat(
                    path.name,
                    dir_fd=directory_fd,
                    follow_symlinks=False,
                )
                if written_stat is not None and (
                    current_target_stat.st_dev,
                    current_target_stat.st_ino,
                ) == (written_stat.st_dev, written_stat.st_ino):
                    os.unlink(path.name, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
        os.close(directory_fd)


def _publish_candidate_report(path: Path, report: Mapping) -> bool:
    path = Path(path)
    if report.get("status") != "PASS" or path.exists() or path.is_symlink():
        return False
    try:
        _atomic_write_json_exclusive(path, report)
    except FileExistsError:
        return False
    return True


_RESOURCE_SAMPLE_FIELDS = frozenset(
    {"sampleId", "rssBytes", "fdCount", "asyncioTaskCount", "threadCount"}
)
_RESOURCE_METRIC_FIELDS = _RESOURCE_SAMPLE_FIELDS - {"sampleId"}


def _valid_resource_metrics(sample) -> bool:
    return (
        isinstance(sample, Mapping)
        and set(sample) == _RESOURCE_METRIC_FIELDS
        and all(
            type(sample.get(field)) is int and sample[field] >= 0
            for field in _RESOURCE_METRIC_FIELDS
        )
    )


def _valid_resource_samples(samples) -> bool:
    return isinstance(samples, list) and all(
        isinstance(sample, Mapping)
        and set(sample) == _RESOURCE_SAMPLE_FIELDS
        and sample.get("sampleId") == f"candidate-resource-{index}"
        and all(
            type(sample.get(field)) is int and sample[field] >= 0
            for field in _RESOURCE_METRIC_FIELDS
        )
        for index, sample in enumerate(samples, 1)
    )


def _validate_candidate_manifest_structure(manifest, *, identity) -> None:
    if not isinstance(manifest, Mapping) or _forbidden_evidence_fields(manifest):
        raise ValueError("candidate evidence manifest is invalid")
    executions = manifest.get("executions")
    padding = manifest.get("quietPadding")
    cleanup = manifest.get("cleanup")
    samples = manifest.get("resourceSamples")
    expected_names = [
        name
        for name, count in _CANDIDATE_STAGE_COUNTS
        for _index in range(count)
    ]
    expected_fields = {
        "schemaVersion",
        "name",
        "candidateIdentity",
        "durationSec",
        "runtimeElapsedSec",
        "executions",
        "quietPadding",
        "cleanup",
        "resourceSamples",
    }
    if (
        set(manifest) != expected_fields
        or manifest.get("schemaVersion") != SCHEMA_VERSION
        or manifest.get("name") != "candidate_soak_evidence_manifest"
        or manifest.get("candidateIdentity") != identity
        or not isinstance(executions, list)
        or [item.get("name") if isinstance(item, Mapping) else None for item in executions]
        != expected_names
        or not isinstance(padding, list)
        or not isinstance(cleanup, Mapping)
        or cleanup.get("status") != "PASS"
        or cleanup.get("candidateIdentity") != identity
        or not isinstance(samples, list)
        or len(samples) != 1 + len(executions) + len(padding) + 1
        or not _finite_nonnegative(manifest.get("durationSec"))
        or not _finite_nonnegative(manifest.get("runtimeElapsedSec"))
    ):
        raise ValueError("candidate evidence manifest is incomplete")
    if not _valid_resource_samples(samples):
        raise ValueError("candidate evidence resource accounting is invalid")
    execution_fields = {
        "schemaVersion", "name", "status", "candidateIdentity", "evidenceSequence",
        "journeyId", "connectionId", "liveConnectionId", "initialLiveConnectionId",
        "finalLiveConnectionId", "liveConnectionTransitions", "peerIdentityHash",
        "serverIssued", "windowId", "logWindow", "evidenceScope", "successfulTurns",
        "bargeins", "latestIntentSuccesses", "falseInterrupts", "unexpectedFallbacks",
        "latencies", "task5LogEvidence",
    }
    padding_fields = {
        "schemaVersion", "name", "status", "candidateIdentity", "journeyId",
        "connectionId", "serverIssued", "windowId", "logWindow", "evidenceScope",
        "liveConnectionId", "initialLiveConnectionId", "finalLiveConnectionId",
        "liveConnectionTransitions", "peerIdentityHash", "durationSec", "falseInterrupts",
        "unexpectedFallbacks", "resourceVerdict", "task5LogEvidence",
    }
    cleanup_fields = {
        "schemaVersion", "name", "status", "candidateIdentity", "finalScope",
        "serverAnchor", "websocketClosed", "providerFinalizeStatus",
        "providerCloseStatus", "pendingOwnedTasks", "activeSessions",
        "activeReceiveLoops", "logStatus", "resourceEndSampleRequired",
    }
    if any(
        not isinstance(item, Mapping)
        or set(item)
        != execution_fields
        | (
            {"task4TransportEvidence", "task5CorrelatedEvidence"}
            if item.get("name") == "bargein"
            else {"quietMode", "observationDurationSec"}
            if item.get("name") == "quiet"
            else {"lessonManifestSha256"}
            if item.get("name") == "lesson"
            else set()
        )
        for item in executions
    ) or any(
        not isinstance(item, Mapping) or set(item) != padding_fields
        for item in padding
    ) or set(cleanup) != cleanup_fields:
        raise ValueError("candidate evidence manifest schema is invalid")


async def _invoke_candidate_driver(args, **context):
    driver = getattr(args, "candidate_journey_driver", None)
    if not callable(driver):
        driver = _run_candidate_websocket_journey
    value = driver(args, **context)
    return await value if inspect.isawaitable(value) else value


def _candidate_audio_packets(args, fixture=None):
    if isinstance(fixture, _ProtectedAudioFixture):
        return _opus_packets_from_pcm(
            fixture.pcm,
            int(getattr(args, "sample_rate", 24000)),
            int(getattr(args, "frame_duration_ms", 60)),
        )
    audio_file = str(getattr(args, "inject_audio", "") or "")
    sample_rate = int(getattr(args, "sample_rate", 24000))
    frame_duration_ms = int(getattr(args, "frame_duration_ms", 60))
    if audio_file:
        return _opus_packets_from_audio_file(
            audio_file,
            sample_rate,
            frame_duration_ms,
        )
    return _opus_packets(
        sample_rate,
        frame_duration_ms,
        float(getattr(args, "audio_duration_sec", 0.6)),
        int(getattr(args, "audio_rms", 9000)),
    )


async def _run_candidate_audio_bargein(
    args, websocket, *, fixture=None, clock=time.monotonic
):
    packets = (
        _candidate_audio_packets(args)
        if fixture is None
        else _candidate_audio_packets(args, fixture)
    )
    if not packets:
        raise RuntimeError("candidate audio barge-in has no opus packets")
    frame_duration_ms = int(getattr(args, "frame_duration_ms", 60))
    preflight_failure = await _drain_preflight_terminal(
        websocket,
        timeout_sec=args.interrupt_timeout_sec,
    )
    if preflight_failure is not None:
        raise RuntimeError("candidate audio barge-in preflight failed")
    await websocket.send(
        json.dumps({"type": "listen", "state": "start", "mode": "realtime"})
    )
    first_packet_sent = asyncio.Event()
    stop_task = asyncio.create_task(
        _observe_interrupt_stop(
            websocket,
            timeout_sec=args.interrupt_timeout_sec,
            clock=clock,
            first_packet_sent=first_packet_sent,
        )
    )
    try:
        first_packet_sent_at = None
        for packet in packets:
            await websocket.send(packet)
            if first_packet_sent_at is None:
                first_packet_sent_at = clock()
                first_packet_sent.set()
            await asyncio.sleep(frame_duration_ms / 1000)
        stop_result = await stop_task
    finally:
        if not stop_task.done():
            stop_task.cancel()
            await asyncio.gather(stop_task, return_exceptions=True)
    if stop_result.get("failureCode") or stop_result.get("stop") is None:
        raise RuntimeError("candidate audio interruption stop failed")
    replacement = await _collect_replacement_response(
        websocket,
        timeout_sec=args.event_timeout_sec,
        clock=clock,
    )
    if (
        not replacement["replacementResponseStarted"]
        or replacement["replacementBinaryChunks"] < 1
        or not replacement["replacementResponseStopped"]
    ):
        raise RuntimeError("candidate audio replacement response is incomplete")
    return {
        **replacement,
        "interruptStopMarkerObserved": True,
        "bargeinStopMs": round(
            (stop_result["observedAt"] - first_packet_sent_at) * 1000,
            1,
        ),
        "binaryChunks": stop_result["binaryCount"]
        + replacement["replacementBinaryChunks"],
    }


async def _run_candidate_websocket_journey(args, **context):
    """Default synthetic-client transport; it never deploys or controls hardware."""
    operation = context["operation"]
    if operation == "cleanup":
        final_scope = context["final_scope"]
        state = getattr(args, "_candidate_websocket_state", {})
        websocket = state.get("websocket") if isinstance(state, dict) else None
        if websocket is not None:
            await websocket.close()
            state["websocket"] = None
        journey_id = final_scope.get("journeyId")
        collection_url = _evidence_collection_url(args)
        journey_url = f"{collection_url}/{urllib.parse.quote(str(journey_id), safe='')}"
        output_root = Path(getattr(args, "produce_candidate_evidence")).parent
        cleanup_output = output_root / "cleanup" / f"{journey_id}.json"
        cleanup_output.parent.mkdir(parents=True, exist_ok=True)
        log_evidence = await _analyze_candidate_journey(
            args,
            journey_id,
            cleanup_output,
        )
        terminal = await _candidate_control_json(args, "GET", journey_url)
        raw_cleanup_proof = (
            log_evidence.get("cleanupEvidence")
            if isinstance(log_evidence, Mapping)
            else None
        )
        expected_scope = final_scope.get("evidenceScope")
        expected_log_window = final_scope.get("logWindow")
        analyzer_bound = (
            isinstance(log_evidence, Mapping)
            and isinstance(expected_scope, Mapping)
            and isinstance(expected_log_window, Mapping)
            and not _validate_log_reliability_contract(
                log_evidence,
                expected_candidate_identity=_candidate_identity(args),
                expected_log_window=dict(expected_log_window),
                expected_evidence_scope=dict(expected_scope),
            )
            and log_evidence.get("serverIssued") is True
            and log_evidence.get("journeyType") == final_scope.get("journeyType")
            and final_scope.get("journeyType")
            in {"quiet_padding", "conversation_after_lesson"}
            and log_evidence.get("serverConnectionTransitions") == []
            and expected_scope.get("journeyId") == journey_id
            and final_scope.get("connectionId")
            == expected_scope.get("connectionId")
            and final_scope.get("windowId") == expected_log_window.get("windowId")
            and final_scope.get("serverEndUtc") == expected_log_window.get("end")
            and expected_scope.get("journeyType") == final_scope.get("journeyType")
            and expected_scope.get("proofProfile")
            == final_scope.get("proofProfile")
            and final_scope.get("peerIdentityHash")
            == expected_scope.get("peerIdentityHash")
            and final_scope.get("initialLiveConnectionId")
            == expected_scope.get("initialLiveConnectionId")
            and final_scope.get("serverIssued") is True
            and all(
                log_evidence.get(field) == final_scope.get(field)
                for field in (
                    "initialLiveConnectionId",
                    "finalLiveConnectionId",
                    "liveConnectionTransitions",
                )
            )
            and isinstance(terminal, Mapping)
            and terminal.get("journeyId") == journey_id
            and terminal.get("journeyType") == final_scope.get("journeyType")
            and terminal.get("proofProfile") == final_scope.get("proofProfile")
            and terminal.get("status") == "PASS"
        )
        cleanup_proof = raw_cleanup_proof if analyzer_bound else None
        proof_pass = (
            analyzer_bound
            and isinstance(cleanup_proof, Mapping)
            and cleanup_proof.get("status") == "PASS"
            and cleanup_proof.get("pendingOwnedTasks") == 0
            and cleanup_proof.get("activeSessions") == 0
            and cleanup_proof.get("activeReceiveLoops") == 0
            and log_evidence.get("status") == "PASS"
        )
        return {
            "schemaVersion": SCHEMA_VERSION,
            "name": "candidate_cleanup",
            "status": "PASS" if proof_pass else "FAIL",
            "candidateIdentity": _candidate_identity(args),
            "finalScope": final_scope,
            "serverAnchor": {
                "connectionId": final_scope.get("connectionId"),
                "peerIdentityHash": getattr(args, "candidate_peer_identity_hash", None),
            },
            "websocketClosed": True,
            "providerFinalizeStatus": (
                "PASS" if analyzer_bound else "FAIL"
            ),
            "providerCloseStatus": (
                "PASS" if isinstance(cleanup_proof, Mapping) and cleanup_proof.get("status") == "PASS" else "FAIL"
            ),
            "pendingOwnedTasks": cleanup_proof.get("pendingOwnedTasks")
            if isinstance(cleanup_proof, Mapping)
            else None,
            "activeSessions": cleanup_proof.get("activeSessions")
            if isinstance(cleanup_proof, Mapping)
            else None,
            "activeReceiveLoops": cleanup_proof.get("activeReceiveLoops")
            if isinstance(cleanup_proof, Mapping)
            else None,
            "logStatus": "PASS" if analyzer_bound else "FAIL",
            "resourceEndSampleRequired": True,
        }
    name = context["name"]
    index = context.get("index", context["sequence"])
    journey_id = context["journey_id"]
    headers = _build_headers(args)
    started = time.monotonic()
    binary_chunks = 0
    bargein_stop_ms = None
    state = getattr(args, "_candidate_websocket_state", None)
    if not isinstance(state, dict):
        state = {"websocket": None}
        setattr(args, "_candidate_websocket_state", state)
    websocket = state.get("websocket")
    if name == "reconnect" and websocket is not None:
        await websocket.close()
        websocket = None
    if websocket is None:
        websocket = await websockets.connect(
            args.websocket_url,
            additional_headers=headers,
            open_timeout=args.open_timeout_sec,
            max_size=None,
        )
        state["websocket"] = websocket
    try:
        hello = _hello_message()
        hello["evidence_journey_id"] = journey_id
        await websocket.send(json.dumps(hello))
        ack, observed, _messages = await _recv_until(
            websocket,
            lambda payload: payload.get("type") == "hello",
            args.event_timeout_sec,
        )
        binary_chunks += observed
        scope = ack.get("evidenceScope") if isinstance(ack, Mapping) else None
        if not isinstance(scope, Mapping) or scope.get("journeyId") != journey_id:
            raise RuntimeError("candidate hello scope is invalid")
        setattr(args, "candidate_peer_identity_hash", scope.get("peerIdentityHash"))

        first_audio_ms = None
        audio_bargein = None
        monitor_resources = None
        if operation == "monitor":
            duration_sec = float(context["duration_sec"])
            if not math.isfinite(duration_sec) or duration_sec <= 0:
                raise RuntimeError("candidate quiet padding duration is invalid")
            resource_start = sample_process_resources()
            try:
                unexpected = await asyncio.wait_for(
                    websocket.recv(),
                    timeout=duration_sec,
                )
            except asyncio.TimeoutError:
                unexpected = None
            if unexpected is not None:
                raise RuntimeError("candidate quiet padding observed unexpected output")
            monitor_resources = resource_verdict(
                [resource_start, sample_process_resources()]
            )
            if monitor_resources.get("status") != "PASS":
                raise RuntimeError("candidate quiet padding resource budget failed")
        else:
            protected = context.get("protected_input")
            quiet_mode = context.get("quiet_mode")
            initial_fixture = (
                protected.bargein_initial
                if name == "bargein" and isinstance(protected, _CandidateProtectedInput)
                else protected.robot_speaking
                if name == "quiet"
                and quiet_mode == "robot_speaking"
                and isinstance(protected, _CandidateProtectedInput)
                else None
            )
            if name == "quiet" and quiet_mode == "silence":
                duration_sec = float(getattr(args, "idle_duration_sec", 120.0))
                try:
                    unexpected = await asyncio.wait_for(
                        websocket.recv(), timeout=duration_sec
                    )
                except asyncio.TimeoutError:
                    unexpected = None
                if unexpected is not None:
                    raise RuntimeError("candidate quiet silence observed unexpected output")
                first_start = None
            elif initial_fixture is not None:
                await websocket.send(
                    json.dumps({"type": "listen", "state": "start", "mode": "realtime"})
                )
                for packet in _candidate_audio_packets(args, initial_fixture):
                    await websocket.send(packet)
                    await asyncio.sleep(int(getattr(args, "frame_duration_ms", 60)) / 1000)
                first_start, observed, _messages = await _recv_until(
                    websocket,
                    lambda payload: _is_tts_state(payload, "start"),
                    args.event_timeout_sec,
                )
                binary_chunks += observed
            else:
                prompt = args.idle_prompt if name == "quiet" else f"{args.first_prompt} Lần {index}."
                await websocket.send(json.dumps(_detect_message(prompt)))
                first_start, observed, _messages = await _recv_until(
                    websocket,
                    lambda payload: _is_tts_state(payload, "start"),
                    args.event_timeout_sec,
                )
                binary_chunks += observed
            if name == "quiet" and quiet_mode == "silence":
                first_audio_ms = None
            else:
                if first_start is None:
                    raise RuntimeError("candidate tts start timeout")
                first_audio_ms = (time.monotonic() - started) * 1000
            if name == "bargein":
                await asyncio.sleep(args.speak_for_sec)
                audio_bargein = await _run_candidate_audio_bargein(
                    args,
                    websocket,
                    fixture=(protected.bargein_newest if isinstance(protected, _CandidateProtectedInput) else None),
                )
                bargein_stop_ms = audio_bargein["bargeinStopMs"]
                binary_chunks += audio_bargein["binaryChunks"]
            elif not (name == "quiet" and quiet_mode == "silence"):
                stopped, observed, _messages = await _recv_until(
                    websocket,
                    lambda payload: _is_tts_state(payload, "stop"),
                    args.settle_timeout_sec,
                )
                binary_chunks += observed
                if stopped is None:
                    raise RuntimeError("candidate tts stop timeout")
        await websocket.send(
            json.dumps({"type": "evidence_finalize", "evidenceScope": scope})
        )
        finalized, observed, _messages = await _recv_until(
            websocket,
            lambda payload: payload.get("type") == "evidence_finalized",
            args.event_timeout_sec,
        )
        binary_chunks += observed
        if not isinstance(finalized, Mapping) or finalized.get("status") != "PASS":
            raise RuntimeError("candidate evidence finalization failed")
    except BaseException:
        await websocket.close()
        state["websocket"] = None
        raise

    final_live_id = finalized.get("finalLiveConnectionId")
    result = {
        "schemaVersion": SCHEMA_VERSION,
        "name": name,
        "status": "PASS",
        "candidateIdentity": _candidate_identity(args),
        "evidenceSequence": context["sequence"],
        "journeyId": journey_id,
        "connectionId": scope.get("connectionId"),
        "liveConnectionId": scope.get("liveConnectionId"),
        "initialLiveConnectionId": scope.get("initialLiveConnectionId"),
        "finalLiveConnectionId": final_live_id,
        "liveConnectionTransitions": finalized.get("liveConnectionTransitions", []),
        "peerIdentityHash": scope.get("peerIdentityHash"),
        "serverIssued": True,
        "windowId": journey_id,
        "logWindow": {
            "windowId": journey_id,
            "start": scope.get("serverStartUtc"),
            "end": finalized.get("serverEndUtc"),
        },
        "evidenceScope": dict(scope),
        "successfulTurns": 0 if name in {"quiet", "quiet_padding", "lesson"} else 1,
        "bargeins": 1 if name == "bargein" else 0,
        "latestIntentSuccesses": 1 if name == "bargein" else 0,
        "falseInterrupts": 0,
        "unexpectedFallbacks": 0,
        "latencies": {} if operation == "monitor" else {"firstAudioMs": [first_audio_ms]},
        "_scopeFinalized": True,
        "_finalizeResult": dict(finalized),
    }
    if name == "quiet" and context.get("quiet_mode") in {"silence", "robot_speaking"}:
        result["quietMode"] = context["quiet_mode"]
        result["observationDurationSec"] = float(
            getattr(args, "idle_duration_sec", 120.0)
        )
        result["latencies"] = {}
    if operation == "monitor":
        start_utc = _parse_utc_iso(result["logWindow"]["start"])
        end_utc = _parse_utc_iso(result["logWindow"]["end"])
        result.update(
            {
                "durationSec": (end_utc - start_utc).total_seconds(),
                "resourceVerdict": monitor_resources,
            }
        )
    if name == "bargein":
        result["latencies"] = {
            "bargeinStopMs": [bargein_stop_ms],
            "serverOutputGapMs": [0.0],
        }
        result["task4TransportEvidence"] = {
            "schemaVersion": SCHEMA_VERSION,
            "name": "websocket_audio_bargein_transport",
            "status": "SKIPPED",
            "candidateIdentity": _candidate_identity(args),
            "pendingCode": "PENDING_BOUNDED_SERVER_LOG_VERIFICATION",
            "correlationSource": "server_log",
            "correlationStatus": "PENDING_BOUNDED_SERVER_LOG_VERIFICATION",
            "aggregateReleaseEligible": False,
            "interruptStopMarkerObserved": True,
            "replacementResponseStarted": audio_bargein["replacementResponseStarted"],
            "replacementResponseStopped": audio_bargein["replacementResponseStopped"],
            "replacementBinaryChunks": audio_bargein["replacementBinaryChunks"],
            "bargeinStopMs": bargein_stop_ms,
            "maxServerOutputGapMs": audio_bargein["maxServerOutputGapMs"],
            "journeyId": journey_id,
            "evidenceScope": dict(scope),
            "serverConnectionId": scope.get("connectionId"),
            "liveConnectionId": scope.get("liveConnectionId"),
            "peerIdentityHash": scope.get("peerIdentityHash"),
            "initialLiveConnectionId": scope.get("initialLiveConnectionId"),
            "finalLiveConnectionId": final_live_id,
            "liveConnectionTransitions": finalized.get("liveConnectionTransitions", []),
            "logWindow": result["logWindow"],
        }
    elif name in {"reopen", "reconnect"}:
        result["latencies"] = {"reconnectRecoveryMs": [first_audio_ms]}
    elif name == "lesson":
        result["latencies"] = {}
        result["lessonManifestSha256"] = _lesson_manifest_digest(
            args.lesson_manifest
        )
    return result


def _evidence_collection_url(args) -> str:
    base = str(getattr(args, "evidence_control_url", "") or "").rstrip("/")
    device_id = str(
        getattr(args, "device_mac", None)
        or getattr(args, "device_id", None)
        or ""
    )
    if not base or not device_id:
        raise ValueError("evidence control URL and device identity are required")
    encoded_device = urllib.parse.quote(device_id, safe="")
    if base.endswith("/google-live-evidence"):
        return base
    if base.endswith("/internal/devices"):
        return f"{base}/{encoded_device}/google-live-evidence"
    return f"{base}/internal/devices/{encoded_device}/google-live-evidence"


async def _candidate_control_json(args, method, url, payload=None):
    override = getattr(args, "candidate_control_json", None)
    if callable(override):
        value = override(method, url, payload)
        return await value if inspect.isawaitable(value) else value
    secret_name = str(
        getattr(args, "evidence_mint_secret_env", "TBOT_DEVICE_MINT_SECRET")
    )
    secret = os.environ.get(secret_name, "")
    if not secret:
        raise RuntimeError("evidence mint secret is unavailable")

    def request():
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"X-Mint-Secret": secret, "Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        try:
            with urllib.request.urlopen(
                urllib.request.Request(url, data=body, headers=headers, method=method),
                timeout=float(getattr(args, "event_timeout_sec", 30.0)),
            ) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, json.JSONDecodeError) as exc:
            raise RuntimeError("evidence control request failed") from exc

    return await asyncio.to_thread(request)


async def _analyze_candidate_journey(args, journey_id, output_path):
    override = getattr(args, "candidate_log_analyzer", None)
    if callable(override):
        value = override(journey_id=journey_id, output_path=output_path)
        return await value if inspect.isawaitable(value) else value
    command = (
        sys.executable,
        str(SERVER_ROOT / "scripts" / "analyze_google_live_log.py"),
        "--log",
        str(args.server_log),
        "--reliability-window",
        "--journey-id",
        journey_id,
        "--out-json",
        str(output_path),
    )

    def analyze():
        completed = subprocess.run(
            command,
            cwd=SERVER_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError("candidate log analysis failed")
        return _read_json_evidence(output_path, "candidate_log_evidence")

    return await asyncio.to_thread(analyze)


def _validate_candidate_finalization(finalized, *, result, journey_id, stage):
    if not isinstance(finalized, Mapping) or not isinstance(result, Mapping):
        raise RuntimeError("candidate evidence finalization is malformed")
    scope = finalized.get("evidenceScope")
    result_scope = result.get("evidenceScope")
    transitions = finalized.get("liveConnectionTransitions")
    server_start = (
        _parse_utc_iso(scope.get("serverStartUtc"))
        if isinstance(scope, Mapping)
        else None
    )
    server_end = _parse_utc_iso(finalized.get("serverEndUtc"))
    initial_live_id = (
        scope.get("initialLiveConnectionId")
        if isinstance(scope, Mapping)
        else None
    )
    final_live_id = finalized.get("finalLiveConnectionId")
    scope_fields = {
        "journeyId",
        "connectionId",
        "liveConnectionId",
        "initialLiveConnectionId",
        "peerIdentityHash",
        "serverStartUtc",
        "journeyType",
        "proofProfile",
    }
    safe_scope_id = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
    if (
        set(finalized)
        != {
            "type",
            "status",
            "evidenceScope",
            "serverEndUtc",
            "finalLiveConnectionId",
            "liveConnectionTransitions",
        }
        or finalized.get("type") != "evidence_finalized"
        or finalized.get("status") != "PASS"
        or not isinstance(scope, Mapping)
        or set(scope) != scope_fields
        or dict(scope) != result_scope
        or re.fullmatch(r"[A-Za-z0-9._:-]{1,64}", str(scope.get("journeyId", "")))
        is None
        or any(
            safe_scope_id.fullmatch(str(scope.get(field, ""))) is None
            for field in (
                "connectionId",
                "liveConnectionId",
                "initialLiveConnectionId",
            )
        )
        or scope.get("journeyId") != journey_id
        or scope.get("journeyType") != stage
        or scope.get("proofProfile") != "candidate-lifecycle"
        or re.fullmatch(r"sha256:[0-9a-f]{64}", str(scope.get("peerIdentityHash", "")))
        is None
        or scope.get("initialLiveConnectionId") != scope.get("liveConnectionId")
        or safe_scope_id.fullmatch(str(final_live_id or "")) is None
        or server_start is None
        or server_end is None
        or server_end <= server_start
        or _validated_live_connection_transition_chain(
            initial_live_id,
            final_live_id,
            transitions,
        )
        != final_live_id
    ):
        raise RuntimeError("candidate evidence finalization is invalid")
    return dict(finalized)


def build_candidate_journeys(args, *, protected_input=None):
    """Build the exact stateful journey surface consumed by candidate soak."""
    run_id = str(getattr(args, "run_id", "") or "").strip()
    if re.fullmatch(r"[0-9]{8}T[0-9]{6}Z", run_id) is None:
        raise ValueError("run_id must be a UTC basic timestamp")
    if isinstance(protected_input, _CandidateProtectedInput):
        protected_input.seal_bargein_plans(10)
    sequence = 0
    cleanup_called = False

    async def run_lifecycle(
        _args,
        *,
        name,
        index,
        label=None,
        duration_sec=None,
    ):
        nonlocal sequence
        quiet_duration_sec = None
        if name == "quiet":
            try:
                quiet_duration_sec = float(getattr(args, "idle_duration_sec", 120.0))
            except (TypeError, ValueError) as exc:
                raise ValueError("candidate quiet duration is invalid") from exc
            if (
                not math.isfinite(quiet_duration_sec)
                or quiet_duration_sec <= 0
                or quiet_duration_sec > _MAX_QUIET_OBSERVATION_SEC
            ):
                raise ValueError("candidate quiet duration is invalid")
        sequence += 1
        journey_id = f"candidate-soak.{run_id}.{sequence}"
        collection_url = _evidence_collection_url(args)
        journey_url = f"{collection_url}/{urllib.parse.quote(journey_id, safe='')}"
        output_root = Path(getattr(args, "produce_candidate_evidence")).parent
        output_path = output_root / "executions" / f"{sequence:02d}-{name}.json"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        enrolled = False
        try:
            semantic_proof = None
            semantic_key = None
            quiet_mode = None
            if name == "bargein" and isinstance(protected_input, _CandidateProtectedInput):
                sealed_plan = protected_input.consume_bargein_plan()
                semantic_key = sealed_plan.key
                semantic_proof = {
                    "version": _CANDIDATE_INTENT_VERSION,
                    "hmacKeyBase64": base64.b64encode(semantic_key).decode("ascii"),
                    "intentPlan": [
                        {
                            "slot": 1,
                            "role": "initial",
                            "expectedMac": sealed_plan.initial_mac.decode("ascii"),
                        },
                        {
                            "slot": 2,
                            "role": "newest",
                            "expectedMac": sealed_plan.newest_mac.decode("ascii"),
                        },
                    ],
                }
            elif name == "quiet" and isinstance(protected_input, _CandidateProtectedInput):
                quiet_mode = "silence" if index == 1 else "robot_speaking"
                semantic_proof = {
                    "version": "google-live-candidate-quiet.v1",
                    "mode": quiet_mode,
                }
            enrollment_payload = {
                "clientId": args.client_id,
                "journeyId": journey_id,
                "ttlSec": 3600,
                "journeyType": name,
                "proofProfile": "candidate-lifecycle",
            }
            if semantic_proof is not None:
                enrollment_payload["semanticProof"] = semantic_proof
            try:
                await _candidate_control_json(
                    args,
                    "POST",
                    collection_url,
                    enrollment_payload,
                )
            finally:
                if semantic_key is not None:
                    sealed_plan.zeroize()
                    semantic_key = None
                if isinstance(semantic_proof, dict) and "hmacKeyBase64" in semantic_proof:
                    semantic_proof["hmacKeyBase64"] = ""
                    for item in semantic_proof.get("intentPlan", ()):
                        if isinstance(item, dict):
                            item["expectedMac"] = ""
            semantic_proof = None
            enrolled = True
            await _candidate_control_json(
                args,
                "PUT",
                f"{journey_url}/candidate-identity",
                {"candidateIdentity": _candidate_identity(args)},
            )
            result = await _invoke_candidate_driver(
                args,
                operation="monitor" if name == "quiet_padding" else "execute",
                name=name,
                index=index,
                label=label,
                sequence=sequence,
                journey_id=journey_id,
                duration_sec=duration_sec,
                protected_input=protected_input,
                quiet_mode=quiet_mode,
            )
            driver_result = dict(result) if isinstance(result, Mapping) else None
            embedded_finalize = (
                driver_result.pop("_finalizeResult", None)
                if driver_result is not None
                else None
            )
            scope_finalized = bool(
                driver_result is not None
                and driver_result.pop("_scopeFinalized", False) is True
            )
            finalized = (
                embedded_finalize
                if scope_finalized
                else await _candidate_control_json(
                    args, "POST", f"{journey_url}/finalize", {}
                )
            )
            finalized = _validate_candidate_finalization(
                finalized,
                result=driver_result,
                journey_id=journey_id,
                stage=name,
            )
            log_evidence = await _analyze_candidate_journey(
                args, journey_id, output_path
            )
            if not isinstance(result, Mapping):
                raise RuntimeError("candidate journey evidence is malformed")
            analyzer_scope = log_evidence.get("evidenceScope")
            if (
                log_evidence.get("journeyType") != name
                or not isinstance(analyzer_scope, Mapping)
                or analyzer_scope.get("journeyType") != name
                or analyzer_scope.get("proofProfile") != "candidate-lifecycle"
            ):
                raise RuntimeError("candidate log claims are invalid")
            combined = driver_result
            if isinstance(protected_input, _CandidateProtectedInput) and (
                dict(analyzer_scope) != dict(finalized["evidenceScope"])
                or log_evidence.get("candidateIdentity") != _candidate_identity(args)
                or log_evidence.get("logWindow") != combined.get("logWindow")
                or log_evidence.get("serverIssued") is not True
            ):
                raise RuntimeError("candidate semantic evidence scope is invalid")
            combined["task5LogEvidence"] = log_evidence
            if isinstance(protected_input, _CandidateProtectedInput):
                combined.update(
                    _candidate_semantic_counters(
                        name,
                        log_evidence,
                        quiet_mode=quiet_mode,
                        requested_duration_sec=quiet_duration_sec,
                        window_duration_sec=(
                            _parse_utc_iso(combined["logWindow"]["end"])
                            - _parse_utc_iso(combined["logWindow"]["start"])
                        ).total_seconds()
                        if name == "quiet"
                        else None,
                    )
                )
            if name == "quiet" and quiet_mode is not None:
                combined["quietMode"] = quiet_mode
                combined["observationDurationSec"] = quiet_duration_sec
            trusted_latency = log_evidence.get("journeyLatencyEvidence", {})
            if name in {"conversation", "conversation_after_lesson"}:
                combined["latencies"] = {
                    "firstAudioMs": [trusted_latency.get("firstAudioMs")]
                }
            elif name == "bargein":
                transport = combined.get("task4TransportEvidence")
                if isinstance(transport, Mapping):
                    combined["task5CorrelatedEvidence"] = (
                        correlate_websocket_bargein_evidence(
                            transport,
                            log_evidence,
                            expected_candidate_identity=_candidate_identity(args),
                        )
                    )
                combined["latencies"] = {
                    "bargeinStopMs": [transport.get("bargeinStopMs")]
                    if isinstance(transport, Mapping)
                    else [],
                    "serverOutputGapMs": [transport.get("maxServerOutputGapMs")]
                    if isinstance(transport, Mapping)
                    else [],
                }
            elif name in {"reopen", "reconnect"}:
                combined["latencies"] = {
                    "reconnectRecoveryMs": [
                        trusted_latency.get("reconnectRecoveryMs")
                    ]
                }
            return [combined] if name == "quiet_padding" else combined
        except BaseException:
            if semantic_key is not None:
                sealed_plan.zeroize()
            if enrolled:
                try:
                    await asyncio.shield(
                        _candidate_control_json(args, "DELETE", journey_url)
                    )
                except BaseException:
                    pass
            raise

    async def execute(_args, *, name, index, label=None, **_kwargs):
        return await run_lifecycle(
            _args,
            name=name,
            index=index,
            label=label,
        )

    async def monitor(_args, *, duration_sec):
        return await run_lifecycle(
            _args,
            name="quiet_padding",
            index=1,
            duration_sec=duration_sec,
        )

    async def cleanup(_args, *, final_scope):
        nonlocal cleanup_called
        if cleanup_called:
            raise RuntimeError("candidate cleanup called more than once")
        cleanup_called = True
        return await _invoke_candidate_driver(
            args,
            operation="cleanup",
            final_scope=final_scope,
        )

    journeys = dict.fromkeys(
        ("conversation", "bargein", "quiet", "reopen", "reconnect", "lesson"),
        execute,
    )
    journeys["monitor"] = monitor
    journeys["cleanup"] = cleanup
    return journeys


async def produce_candidate_evidence(
    args,
    *,
    journeys=None,
    sample_resources=sample_process_resources,
    clock=time.monotonic,
):
    """Run the candidate workload and publish a closed replay manifest atomically."""
    output = Path(getattr(args, "produce_candidate_evidence"))
    if output.exists() or output.is_symlink():
        raise ValueError("candidate evidence output must not already exist")
    identity = _candidate_identity(args)
    protected_input = None
    if isinstance(journeys, Mapping):
        source = journeys
    else:
        protected_stream = getattr(args, "candidate_protected_stdin", None)
        if protected_stream is None:
            protected_stream = sys.stdin.buffer
        protected_input = _read_candidate_protected_input(
            protected_stream,
            output_paths=tuple(
                path
                for path in (
                    output,
                    getattr(args, "report", None),
                    getattr(args, "server_log", None),
                    getattr(args, "lesson_manifest", None),
                )
                if path is not None
            ),
            sample_rate=int(getattr(args, "sample_rate", 24000)),
        )
        source = build_candidate_journeys(args, protected_input=protected_input)
    executions = []
    padding = []
    cleanup_records = []
    resource_samples = []
    sample_sequence = 0

    def recorded_sample():
        nonlocal sample_sequence
        value = sample_resources()
        if not _valid_resource_metrics(value):
            raise ValueError("candidate evidence resource accounting is invalid")
        sample_sequence += 1
        sample = dict(value)
        sample["sampleId"] = f"candidate-resource-{sample_sequence}"
        resource_samples.append(dict(sample))
        return sample

    async def record_execution(_args, **kwargs):
        callable_name = (
            "conversation"
            if kwargs.get("name") == "conversation_after_lesson"
            else kwargs.get("name")
        )
        value = await source[callable_name](_args, **kwargs)
        if isinstance(value, Mapping):
            executions.append(dict(value))
        return value

    async def record_monitor(_args, **kwargs):
        value = await source["monitor"](_args, **kwargs)
        if isinstance(value, list):
            padding.extend(dict(item) if isinstance(item, Mapping) else item for item in value)
        return value

    async def record_cleanup(_args, **kwargs):
        if cleanup_records:
            raise RuntimeError("candidate cleanup called more than once")
        value = await source["cleanup"](_args, **kwargs)
        cleanup_records.append(dict(value) if isinstance(value, Mapping) else value)
        return value

    recorded = dict.fromkeys(
        ("conversation", "bargein", "quiet", "reopen", "reconnect", "lesson"),
        record_execution,
    )
    recorded["monitor"] = record_monitor
    recorded["cleanup"] = record_cleanup
    try:
        report = await run_candidate_soak(
            args,
            journeys=recorded,
            sample_resources=recorded_sample,
            clock=clock,
        )
    finally:
        if protected_input is not None:
            protected_input.zeroize()
    if report.get("status") != "PASS" or len(cleanup_records) != 1:
        return report
    manifest = {
        "schemaVersion": SCHEMA_VERSION,
        "name": "candidate_soak_evidence_manifest",
        "candidateIdentity": identity,
        "durationSec": report.get("durationSec"),
        "runtimeElapsedSec": report.get("runtimeElapsedSec"),
        "executions": executions,
        "quietPadding": padding,
        "cleanup": cleanup_records[0],
        "resourceSamples": resource_samples,
    }
    _validate_candidate_manifest_structure(manifest, identity=identity)
    reopened = _atomic_write_json_exclusive(output, manifest)
    _validate_candidate_manifest_structure(reopened, identity=identity)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "name": "candidate_soak_evidence_producer",
        "status": "PASS",
        "candidateIdentity": identity,
        "executionCount": len(executions),
        "exit_code": 0,
    }


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
    elif isinstance(value, str) and _SENSITIVE_EVIDENCE_VALUE_RE.search(value):
        hits.append(path or "value")
    return hits


def _finite_nonnegative(value):
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value) and value >= 0


def _finite_positive(value):
    return _finite_nonnegative(value) and value > 0


def _strict_candidate_latency_metrics(value):
    return (
        isinstance(value, Mapping)
        and set(value) == set(_CANDIDATE_LATENCY_METRICS)
        and all(_finite_positive(value.get(metric)) for metric in _CANDIDATE_LATENCY_METRICS)
    )


def _release_owned_cleanup_task(task):
    _OWNED_CLEANUP_TASKS.discard(task)
    if not task.cancelled():
        task.exception()


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


def _validated_execution_server_scope(value, *, identity):
    scope = value.get("evidenceScope")
    log_window = value.get("logWindow")
    transitions = value.get("liveConnectionTransitions")
    if not isinstance(scope, Mapping) or not isinstance(log_window, Mapping):
        return None
    expected_scope = {
        "journeyId": value.get("journeyId"),
        "connectionId": value.get("connectionId"),
        "liveConnectionId": value.get("liveConnectionId"),
        "initialLiveConnectionId": value.get("initialLiveConnectionId"),
        "peerIdentityHash": value.get("peerIdentityHash"),
        "serverStartUtc": log_window.get("start"),
        "journeyType": value.get("name"),
        "proofProfile": "candidate-lifecycle",
    }
    valid = (
        value.get("serverIssued") is True
        and dict(scope) == expected_scope
        and isinstance(expected_scope["journeyId"], str)
        and bool(expected_scope["journeyId"])
        and isinstance(expected_scope["connectionId"], str)
        and bool(expected_scope["connectionId"])
        and isinstance(expected_scope["liveConnectionId"], str)
        and bool(expected_scope["liveConnectionId"])
        and expected_scope["initialLiveConnectionId"]
        == expected_scope["liveConnectionId"]
        and isinstance(expected_scope["peerIdentityHash"], str)
        and re.fullmatch(r"sha256:[0-9a-f]{64}", expected_scope["peerIdentityHash"])
        is not None
        and _validated_live_connection_transition_chain(
            value.get("initialLiveConnectionId"),
            value.get("finalLiveConnectionId"),
            transitions,
        )
        == value.get("finalLiveConnectionId")
    )
    if not valid:
        return None
    log_proof = value.get("task5LogEvidence")
    if _validate_log_reliability_contract(
        log_proof,
        expected_candidate_identity=identity,
        expected_log_window=dict(log_window),
        expected_evidence_scope=dict(scope),
    ) or any(
        log_proof.get(field) != value.get(field)
        for field in (
            "initialLiveConnectionId",
            "finalLiveConnectionId",
            "liveConnectionTransitions",
        )
    ):
        return None
    stage = value.get("name")
    if log_proof.get("journeyType") != stage:
        return None
    if stage in {"bargein", "quiet"}:
        log_start = _parse_utc_iso(value["logWindow"]["start"])
        log_end = _parse_utc_iso(value["logWindow"]["end"])
        try:
            semantic_counters = _candidate_semantic_counters(
                stage,
                log_proof,
                quiet_mode=value.get("quietMode"),
                requested_duration_sec=value.get("observationDurationSec"),
                window_duration_sec=(log_end - log_start).total_seconds()
                if log_start is not None and log_end is not None
                else None,
            )
        except RuntimeError:
            return None
        if any(value.get(field) != expected for field, expected in semantic_counters.items()):
            return None
    flat_latencies = value.get("latencies")
    proof_latencies = log_proof.get("journeyLatencyEvidence")
    expected_flat_latencies = {}
    expected_proof_latencies = {}
    if stage in {"conversation", "conversation_after_lesson"}:
        expected_proof_latencies = dict(proof_latencies) if isinstance(proof_latencies, Mapping) else {}
        expected_flat_latencies = {
            "firstAudioMs": [
                proof_latencies.get("firstAudioMs")
                if isinstance(proof_latencies, Mapping)
                else None
            ]
        }
    elif stage in {"reopen", "reconnect"}:
        expected_proof_latencies = dict(proof_latencies) if isinstance(proof_latencies, Mapping) else {}
        expected_flat_latencies = {
            "reconnectRecoveryMs": [
                proof_latencies.get("reconnectRecoveryMs")
                if isinstance(proof_latencies, Mapping)
                else None
            ]
        }
    elif stage == "bargein":
        transport = value.get("task4TransportEvidence")
        correlated = value.get("task5CorrelatedEvidence")
        expected_flat_latencies = {
            "bargeinStopMs": [
                correlated.get("bargeinStopMs")
                if isinstance(correlated, Mapping)
                else None
            ],
            "serverOutputGapMs": [
                correlated.get("maxServerOutputGapMs")
                if isinstance(correlated, Mapping)
                else None
            ],
        }
        if (
            not isinstance(transport, Mapping)
            or transport.get("bargeinStopMs")
            != expected_flat_latencies["bargeinStopMs"][0]
            or transport.get("maxServerOutputGapMs")
            != expected_flat_latencies["serverOutputGapMs"][0]
        ):
            return None
    if (
        not isinstance(flat_latencies, Mapping)
        or not isinstance(proof_latencies, Mapping)
        or dict(proof_latencies) != expected_proof_latencies
        or set(expected_proof_latencies) != (
            {"firstAudioMs"}
            if stage in {"conversation", "conversation_after_lesson"}
            else {"reconnectRecoveryMs"}
            if stage in {"reopen", "reconnect"}
            else set()
        )
        or dict(flat_latencies) != expected_flat_latencies
        or any(
            not _finite_positive(samples[0])
            for samples in expected_flat_latencies.values()
        )
    ):
        return None
    anchor = {
        "connectionId": scope["connectionId"],
        "peerIdentityHash": scope["peerIdentityHash"],
    }
    if stage != "bargein":
        return anchor
    transport = value.get("task4TransportEvidence")
    correlated = value.get("task5CorrelatedEvidence")
    normalized = correlate_websocket_bargein_evidence(
        transport,
        log_proof,
        expected_candidate_identity=identity,
    )
    if normalized.get("status") != "PASS" or correlated != normalized:
        return None
    return anchor


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
    expected_peer_identity_hash,
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
        "liveConnectionId": value.get("liveConnectionId"),
        "initialLiveConnectionId": value.get("initialLiveConnectionId"),
        "peerIdentityHash": expected_peer_identity_hash,
        "serverStartUtc": log_window.get("start")
        if isinstance(log_window, Mapping)
        else None,
        "journeyType": "quiet_padding",
        "proofProfile": "candidate-lifecycle",
    }
    valid = (
        value.get("schemaVersion") == SCHEMA_VERSION
        and value.get("name") == "quiet_padding"
        and value.get("status") == "PASS"
        and value.get("candidateIdentity") == identity
        and value.get("connectionId") == expected_connection_id
        and value.get("serverIssued") is True
        and value.get("peerIdentityHash") == expected_peer_identity_hash
        and value.get("evidenceScope") == expected_scope
        and value.get("liveConnectionId") == value.get("initialLiveConnectionId")
        and _validated_live_connection_transition_chain(
            value.get("initialLiveConnectionId"),
            value.get("finalLiveConnectionId"),
            value.get("liveConnectionTransitions"),
        )
        == value.get("finalLiveConnectionId")
        and value.get("falseInterrupts") == 0
        and value.get("unexpectedFallbacks") == 0
        and value.get("latencies", {}) == {}
        and isinstance(value.get("resourceVerdict"), Mapping)
        and value["resourceVerdict"].get("status") == "PASS"
        and duration is not None
        and duration > 0
        and _finite_nonnegative(value.get("durationSec"))
        and abs(float(value["durationSec"]) - duration) <= 1.0
        and gap is not None
        and 0 <= gap <= gap_budget_sec
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
    log_proof = value.get("task5LogEvidence")
    if (
        not isinstance(log_proof, Mapping)
        or log_proof.get("journeyType") != "quiet_padding"
        or log_proof.get("maxReceiveLoopsActive") != 1
        or _validate_log_reliability_contract(
            log_proof,
            expected_candidate_identity=identity,
            expected_log_window=dict(log_window),
            expected_evidence_scope=dict(expected_scope),
        )
        or any(
            log_proof.get(field) != value.get(field)
            for field in (
                "initialLiveConnectionId",
                "finalLiveConnectionId",
                "liveConnectionTransitions",
            )
        )
        or log_proof.get("serverConnectionTransitions") != []
    ):
        return None
    normalized = dict(value)
    normalized["serverIssued"] = True
    normalized["logStatus"] = "PASS"
    return normalized, end_utc, utc_window


def _latency_metrics(executions):
    first_audio = []
    bargein = []
    server_output_gap = []
    reconnect = []
    for execution in executions:
        stage = execution.get("name")
        log_proof = execution.get("task5LogEvidence")
        proof_latencies = (
            log_proof.get("journeyLatencyEvidence")
            if isinstance(log_proof, Mapping)
            else None
        )
        if stage == "bargein":
            correlated = execution.get("task5CorrelatedEvidence")
            latencies = {
                "bargeinStopMs": [
                    correlated.get("bargeinStopMs")
                    if isinstance(correlated, Mapping)
                    else None
                ],
                "serverOutputGapMs": [
                    correlated.get("maxServerOutputGapMs")
                    if isinstance(correlated, Mapping)
                    else None
                ],
            }
        elif stage in {"conversation", "conversation_after_lesson"}:
            latencies = {
                "firstAudioMs": [
                    proof_latencies.get("firstAudioMs")
                    if isinstance(proof_latencies, Mapping)
                    else None
                ]
            }
        elif stage in {"reopen", "reconnect"}:
            latencies = {
                "reconnectRecoveryMs": [
                    proof_latencies.get("reconnectRecoveryMs")
                    if isinstance(proof_latencies, Mapping)
                    else None
                ]
            }
        else:
            latencies = {}
        if not isinstance(latencies, Mapping):
            raise ValueError("latencies must be a mapping")
        schema = {
            "conversation": (("firstAudioMs", first_audio),),
            "conversation_after_lesson": (("firstAudioMs", first_audio),),
            "bargein": (
                ("bargeinStopMs", bargein),
                ("serverOutputGapMs", server_output_gap),
            ),
            "reopen": (("reconnectRecoveryMs", reconnect),),
            "reconnect": (("reconnectRecoveryMs", reconnect),),
            "quiet": (),
            "lesson": (),
        }.get(stage)
        if schema is None or set(latencies) != {field for field, _target in schema}:
            raise ValueError("latency fields do not match stage schema")
        for field, target in schema:
            values = latencies[field]
            if (
                not isinstance(values, list)
                or len(values) != 1
                or not _finite_positive(values[0])
            ):
                raise ValueError(f"{field} must contain exactly one positive sample")
            target.extend(values)
    metrics = {
        "firstAudioP50Ms": percentile(first_audio, 50),
        "firstAudioP95Ms": percentile(first_audio, 95),
        "bargeinP95Ms": percentile(bargein, 95),
        "reconnectRecoveryP95Ms": percentile(reconnect, 95),
    }
    expected_counts = {
        "firstAudioMs": 18,
        "bargeinStopMs": 10,
        "serverOutputGapMs": 10,
        "reconnectRecoveryMs": 2,
    }
    if (
        len(first_audio) != expected_counts["firstAudioMs"]
        or len(bargein) != expected_counts["bargeinStopMs"]
        or len(server_output_gap) != expected_counts["serverOutputGapMs"]
        or len(reconnect) != expected_counts["reconnectRecoveryMs"]
    ):
        raise ValueError("latency sample counts do not match candidate workload")
    return metrics, percentile(server_output_gap, 95)


async def _run_candidate_soak_impl(
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
    monitored_duration = 0.0
    gap_budget_sec = float(getattr(args, "evidence_gap_budget_sec", 10.0))
    if not math.isfinite(gap_budget_sec) or not 0 < gap_budget_sec <= 10.0:
        failures.append({"code": "EVIDENCE_GAP_BUDGET_INVALID"})
    maximum_padding_windows = int(getattr(args, "maximum_padding_windows", 60))
    execution_anchors = []
    task5_server_anchor = None
    current_server_connection = None
    immutable_peer_identity_hash = None
    previous_execution_scope = None
    server_connection_transitions = 0
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
            execution_anchor = _validated_execution_server_scope(
                result,
                identity=identity,
            )
            if execution_anchor is None:
                failures.append(
                    {"code": "EXECUTION_SERVER_SCOPE_INVALID", "stage": stage_name}
                )
            else:
                execution_anchors.append(execution_anchor)
                if stage_name == "bargein" and task5_server_anchor is None:
                    task5_server_anchor = execution_anchor
                log_proof = result.get("task5LogEvidence")
                transitions = (
                    log_proof.get("serverConnectionTransitions")
                    if isinstance(log_proof, Mapping)
                    else None
                )
                if current_server_connection is None:
                    current_server_connection = execution_anchor["connectionId"]
                    immutable_peer_identity_hash = execution_anchor[
                        "peerIdentityHash"
                    ]
                if execution_anchor["peerIdentityHash"] != immutable_peer_identity_hash:
                    failures.append(
                        {"code": "EXECUTION_SERVER_ANCHOR_MISMATCH", "stage": stage_name}
                    )
                if stage_name == "reconnect":
                    expected_transition = {
                        "status": "PASS",
                        "source": "server_log",
                        "serverIssued": True,
                        "sequence": 1,
                        "reason": "same_device_reconnect",
                        "fromJourneyId": previous_execution_scope.get("journeyId"),
                        "fromConnectionId": current_server_connection,
                        "toJourneyId": result.get("journeyId"),
                        "toConnectionId": execution_anchor["connectionId"],
                        "peerIdentityHash": immutable_peer_identity_hash,
                    }
                    if (
                        not isinstance(transitions, list)
                        or transitions != [expected_transition]
                        or server_connection_transitions != 0
                    ):
                        failures.append({"code": "SERVER_CONNECTION_TRANSITION_INVALID"})
                    else:
                        server_connection_transitions += 1
                        current_server_connection = expected_transition["toConnectionId"]
                elif (
                    transitions != []
                    or execution_anchor["connectionId"] != current_server_connection
                ):
                    failures.append(
                        {"code": "SERVER_CONNECTION_DRIFT", "stage": stage_name}
                    )
                previous_execution_scope = result.get("evidenceScope")
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
                    gap_sec < 0 or gap_sec > gap_budget_sec
                ):
                    failures.append({"code": "EVIDENCE_UTC_WINDOW_INVALID", "stage": stage_name})
                else:
                    first_window_start = first_window_start or start_utc
                    previous_window_end = end_utc
                    last_window_end = end_utc
                    monitored_duration += (end_utc - start_utc).total_seconds()
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

    if (
        task5_server_anchor is None
        or len(execution_anchors) != len(executions)
        or task5_server_anchor.get("peerIdentityHash")
        != immutable_peer_identity_hash
        or task5_server_anchor.get("connectionId")
        != execution_anchors[0].get("connectionId")
        or server_connection_transitions != 1
    ):
        failures.append({"code": "EXECUTION_SERVER_ANCHOR_MISMATCH"})

    elapsed = clock() - started
    minimum_duration = float(args.minimum_duration_sec)
    padding_evidence = []
    proven_duration = monitored_duration
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
                        expected_peer_identity_hash=immutable_peer_identity_hash,
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
                    monitored_duration += (utc_window[1] - utc_window[0]).total_seconds()
                    samples.append(safe_sample())
                elapsed = clock() - started
        else:
            failures.append({"code": "QUIET_PADDING_INVALID"})
    cleanup = journeys["cleanup"]
    final_execution = executions[-1] if executions else {}
    final_evidence = padding_evidence[-1] if padding_evidence else final_execution
    final_scope = {
        "journeyId": final_evidence.get("journeyId"),
        "connectionId": final_evidence.get("connectionId"),
        "windowId": final_evidence.get("windowId"),
        "serverEndUtc": last_window_end.isoformat()
        if last_window_end is not None
        else None,
        "evidenceScope": final_evidence.get("evidenceScope"),
        "logWindow": final_evidence.get("logWindow"),
        "journeyType": final_evidence.get("name"),
        "proofProfile": "candidate-lifecycle",
        "initialLiveConnectionId": final_evidence.get("initialLiveConnectionId"),
        "finalLiveConnectionId": final_evidence.get("finalLiveConnectionId"),
        "liveConnectionTransitions": final_evidence.get(
            "liveConnectionTransitions"
        ),
        "peerIdentityHash": final_evidence.get("peerIdentityHash"),
        "serverIssued": final_evidence.get("serverIssued"),
    }
    cleanup_evidence = await cleanup(args, final_scope=final_scope)
    samples.append(safe_sample())
    final_sample = samples[-1]
    required_sample_fields = (
        "rssBytes",
        "fdCount",
        "asyncioTaskCount",
        "threadCount",
    )
    final_sample_accounted = bool(final_sample) and all(
        field in final_sample
        and not isinstance(final_sample[field], bool)
        and isinstance(final_sample[field], (int, float))
        and math.isfinite(final_sample[field])
        and final_sample[field] >= 0
        for field in required_sample_fields
    )
    actual_pending_cleanup_tasks = sum(
        1 for task in _OWNED_CLEANUP_TASKS if not task.done()
    )
    cleanup_pass = (
        isinstance(cleanup_evidence, Mapping)
        and not _forbidden_evidence_fields(cleanup_evidence)
        and cleanup_evidence.get("schemaVersion") == SCHEMA_VERSION
        and cleanup_evidence.get("name") == "candidate_cleanup"
        and cleanup_evidence.get("status") == "PASS"
        and cleanup_evidence.get("candidateIdentity") == identity
        and cleanup_evidence.get("finalScope") == final_scope
        and cleanup_evidence.get("serverAnchor")
        == {
            "connectionId": current_server_connection,
            "peerIdentityHash": immutable_peer_identity_hash,
        }
        and cleanup_evidence.get("websocketClosed") is True
        and cleanup_evidence.get("providerFinalizeStatus") == "PASS"
        and cleanup_evidence.get("providerCloseStatus") == "PASS"
        and type(cleanup_evidence.get("pendingOwnedTasks")) is int
        and cleanup_evidence.get("pendingOwnedTasks") == 0
        and type(cleanup_evidence.get("activeSessions")) is int
        and cleanup_evidence.get("activeSessions") == 0
        and type(cleanup_evidence.get("activeReceiveLoops")) is int
        and cleanup_evidence.get("activeReceiveLoops") == 0
        and cleanup_evidence.get("logStatus") == "PASS"
        and cleanup_evidence.get("resourceEndSampleRequired") is True
        and final_sample_accounted
        and actual_pending_cleanup_tasks == 0
    )
    if not cleanup_pass:
        failures.append({"code": "CLEANUP_FAILED"})
    accounting = journeys.get("accounting")
    if callable(accounting):
        try:
            accounting_failures = accounting()
        except Exception:
            accounting_failures = [{"code": "UNEXPECTED_EVIDENCE"}]
        if isinstance(accounting_failures, list):
            failures.extend(
                item
                for item in accounting_failures
                if isinstance(item, Mapping) and isinstance(item.get("code"), str)
            )
        else:
            failures.append({"code": "UNEXPECTED_EVIDENCE"})

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
    proven_duration = monitored_duration
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
    claimed_runtime = getattr(args, "candidate_evidence_runtime_sec", None)
    if replay_mode and claimed_runtime is not None and (
        not _finite_nonnegative(claimed_runtime)
        or float(claimed_runtime) < proven_duration
    ):
        failures.append({"code": "CLAIMED_RUNTIME_MISMATCH"})

    try:
        latency_metrics, server_output_gap_p95_ms = _latency_metrics(executions)
    except ValueError:
        latency_metrics = {
            "firstAudioP50Ms": None,
            "firstAudioP95Ms": None,
            "bargeinP95Ms": None,
            "reconnectRecoveryP95Ms": None,
        }
        server_output_gap_p95_ms = None
    candidate_latency_valid = _strict_candidate_latency_metrics(latency_metrics)
    if not candidate_latency_valid:
        failures.append({"code": "LATENCY_EVIDENCE_MALFORMED"})
    try:
        baseline = _read_json_evidence(args.baseline_report, "baseline_report")
    except ValueError:
        baseline = {}
    baseline_metrics = baseline.get("latencyMetrics")
    baseline_valid = _strict_candidate_latency_metrics(baseline_metrics)
    if not baseline_valid:
        failures.append({"code": "BASELINE_EVIDENCE_INVALID"})
    latency_comparison = compare_latency_baseline(
        latency_metrics if candidate_latency_valid else {},
        baseline_metrics if baseline_valid else {},
    )
    if not latency_comparison["pass"]:
        failures.append({"code": "LATENCY_REGRESSION"})
    hard_latency_pass = (
        _finite_nonnegative(latency_metrics["firstAudioP50Ms"])
        and latency_metrics["firstAudioP50Ms"] <= GOOGLE_LIVE_LIMITS["firstAudioP50Ms"]
        and _finite_nonnegative(latency_metrics["firstAudioP95Ms"])
        and latency_metrics["firstAudioP95Ms"] <= GOOGLE_LIVE_LIMITS["firstAudioP95Ms"]
        and _finite_nonnegative(latency_metrics["bargeinP95Ms"])
        and latency_metrics["bargeinP95Ms"] <= GOOGLE_LIVE_LIMITS["physicalBargeinP95Ms"]
        and _finite_positive(server_output_gap_p95_ms)
        and server_output_gap_p95_ms <= GOOGLE_LIVE_LIMITS["serverOutputGapMaxMs"]
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
        "recordedRuntimeElapsedSec": float(claimed_runtime)
        if replay_mode and _finite_nonnegative(claimed_runtime)
        else None,
        "replayCandidateEvidence": replay_mode,
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
                "candidateIdentity": item.get("candidateIdentity"),
                "connectionId": item.get("connectionId"),
                "windowId": item.get("windowId"),
                "logWindow": item.get("logWindow"),
                "durationSec": item.get("durationSec"),
                "status": item.get("status"),
                "serverIssued": item.get("serverIssued"),
                "peerIdentityHash": item.get("peerIdentityHash"),
                "liveConnectionId": item.get("liveConnectionId"),
                "initialLiveConnectionId": item.get("initialLiveConnectionId"),
                "finalLiveConnectionId": item.get("finalLiveConnectionId"),
                "liveConnectionTransitions": item.get("liveConnectionTransitions"),
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
                "connectionId": item.get("connectionId"),
                "windowId": item.get("windowId"),
                "evidenceScope": item.get("evidenceScope"),
                "initialLiveConnectionId": item.get("initialLiveConnectionId"),
                "finalLiveConnectionId": item.get("finalLiveConnectionId"),
                "liveConnectionTransitions": item.get("liveConnectionTransitions"),
                "serverConnectionTransitions": (
                    item.get("task5LogEvidence") or {}
                ).get("serverConnectionTransitions"),
                "logWindow": item.get("logWindow"),
                "status": item.get("status"),
            }
            for item in executions
        ],
        "totals": totals,
        "latencyMetrics": latency_metrics,
        "serverOutputGapP95Ms": server_output_gap_p95_ms,
        "latencyComparison": latency_comparison,
        "resourceVerdict": resources,
        "cleanupVerdict": {
            "status": "PASS" if cleanup_pass else "FAIL",
            "websocketClosed": isinstance(cleanup_evidence, Mapping)
            and cleanup_evidence.get("websocketClosed") is True,
            "pendingOwnedTasks": actual_pending_cleanup_tasks
            if actual_pending_cleanup_tasks
            else cleanup_evidence.get("pendingOwnedTasks")
            if isinstance(cleanup_evidence, Mapping)
            and type(cleanup_evidence.get("pendingOwnedTasks")) is int
            else None,
            "actualPendingCleanupTasks": actual_pending_cleanup_tasks,
            "activeSessions": cleanup_evidence.get("activeSessions")
            if isinstance(cleanup_evidence, Mapping)
            and type(cleanup_evidence.get("activeSessions")) is int
            else None,
            "activeReceiveLoops": cleanup_evidence.get("activeReceiveLoops")
            if isinstance(cleanup_evidence, Mapping)
            and type(cleanup_evidence.get("activeReceiveLoops")) is int
            else None,
            "providerFinalizeStatus": "PASS"
            if isinstance(cleanup_evidence, Mapping)
            and cleanup_evidence.get("providerFinalizeStatus") == "PASS"
            else "FAIL",
            "providerCloseStatus": "PASS"
            if isinstance(cleanup_evidence, Mapping)
            and cleanup_evidence.get("providerCloseStatus") == "PASS"
            else "FAIL",
            "logStatus": "PASS"
            if isinstance(cleanup_evidence, Mapping)
            and cleanup_evidence.get("logStatus") == "PASS"
            else "FAIL",
            "resourceEndSampleAccounted": final_sample_accounted,
        },
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
    if report["status"] == "PASS":
        contract_failures = validate_candidate_soak_report(
            report, expected_candidate_identity=identity
        )
        if contract_failures:
            report["status"] = "FAIL"
            report["failures"].extend(contract_failures)
            report["exit_code"] = 1
    return redact_mapping(report)


async def run_candidate_soak(
    args,
    *,
    journeys,
    sample_resources=sample_process_resources,
    clock=time.monotonic,
):
    """Run candidate soak with mandatory exactly-once bounded cleanup."""
    cleanup = journeys.get("cleanup") if isinstance(journeys, Mapping) else None
    if not callable(cleanup):
        return {
            "schemaVersion": SCHEMA_VERSION,
            "name": "candidate_soak",
            "status": "FAIL",
            "candidateIdentity": _candidate_identity(args),
            "failures": [{"code": "CLEANUP_CALLABLE_MISSING"}],
            "cleanupVerdict": {"status": "FAIL"},
            "exit_code": 1,
        }
    identity = _candidate_identity(args)
    cleanup_timeout_invalid = False
    try:
        cleanup_timeout = float(getattr(args, "cleanup_timeout_sec", 2.0))
    except (TypeError, ValueError):
        cleanup_timeout = 2.0
        cleanup_timeout_invalid = True
    if not math.isfinite(cleanup_timeout) or cleanup_timeout <= 0:
        cleanup_timeout = 2.0
        cleanup_timeout_invalid = True
    cleanup_called = False
    cleanup_result = None
    cleanup_task = None

    async def guarded_cleanup(_args, *, final_scope):
        nonlocal cleanup_called, cleanup_result, cleanup_task
        if cleanup_called:
            return cleanup_result
        cleanup_called = True

        async def invoke_cleanup():
            try:
                value = cleanup(_args, final_scope=final_scope)
            except Exception as exc:
                return {
                    "schemaVersion": SCHEMA_VERSION,
                    "name": "candidate_cleanup",
                    "status": "FAIL",
                    "candidateIdentity": identity,
                    "finalScope": final_scope,
                    "failureCode": "CLEANUP_EXCEPTION",
                    "errorClass": type(exc).__name__,
                }
            if not inspect.isawaitable(value):
                return {
                    "schemaVersion": SCHEMA_VERSION,
                    "name": "candidate_cleanup",
                    "status": "FAIL",
                    "candidateIdentity": identity,
                    "finalScope": final_scope,
                    "failureCode": "CLEANUP_INVALID_RESULT",
                }
            return await value

        cleanup_task = asyncio.create_task(invoke_cleanup())
        cleanup_task.set_name("google-live-candidate-cleanup")
        _OWNED_CLEANUP_TASKS.add(cleanup_task)
        cleanup_task.add_done_callback(_release_owned_cleanup_task)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + cleanup_timeout
        try:
            done, _pending = await asyncio.wait(
                {cleanup_task}, timeout=max(0.0, deadline - loop.time())
            )
        except asyncio.CancelledError:
            done, _pending = await asyncio.shield(
                asyncio.wait(
                    {cleanup_task}, timeout=max(0.0, deadline - loop.time())
                )
            )
            if not done:
                cleanup_task.cancel()
            raise
        if not done:
            cleanup_task.cancel()
            cleanup_result = {
                "schemaVersion": SCHEMA_VERSION,
                "name": "candidate_cleanup",
                "status": "FAIL",
                "candidateIdentity": identity,
                "finalScope": final_scope,
                "failureCode": "CLEANUP_TIMEOUT",
                "pendingOwnedTasks": 1,
            }
            return cleanup_result
        try:
            cleanup_result = cleanup_task.result()
        except asyncio.CancelledError:
            cleanup_result = {
                "schemaVersion": SCHEMA_VERSION,
                "name": "candidate_cleanup",
                "status": "FAIL",
                "candidateIdentity": identity,
                "finalScope": final_scope,
                "failureCode": "CLEANUP_CANCELLED",
            }
        except Exception as exc:
            cleanup_result = {
                "schemaVersion": SCHEMA_VERSION,
                "name": "candidate_cleanup",
                "status": "FAIL",
                "candidateIdentity": identity,
                "finalScope": final_scope,
                "failureCode": "CLEANUP_EXCEPTION",
                "errorClass": type(exc).__name__,
            }
        return cleanup_result

    guarded_journeys = dict(journeys)
    guarded_journeys["cleanup"] = guarded_cleanup
    try:
        report = await _run_candidate_soak_impl(
            args,
            journeys=guarded_journeys,
            sample_resources=sample_resources,
            clock=clock,
        )
        if cleanup_timeout_invalid:
            report["failures"].append({"code": "CLEANUP_TIMEOUT_INVALID"})
            report["status"] = "FAIL"
            report["exit_code"] = 1
        return report
    finally:
        if not cleanup_called:
            await guarded_cleanup(
                args,
                final_scope={
                    "journeyId": None,
                    "connectionId": None,
                    "windowId": None,
                    "serverEndUtc": None,
                },
            )


async def run_soak(args):
    if getattr(args, "scenario", None) == "tvideo-farm":
        if getattr(args, "dry_run", False):
            return _dry_run_tvideo_farm_report(args)
        return await _run_tvideo_farm_scenario(args)
    mode = getattr(args, "mode", None)
    if mode == "candidate":
        if getattr(args, "produce_candidate_evidence", None):
            return await produce_candidate_evidence(args)
        journeys = getattr(args, "candidate_journeys", None)
        candidate_sampler = sample_process_resources
        if not isinstance(journeys, Mapping):
            manifest = _read_json_evidence(args.journey_evidence, "journey_evidence")
            if _forbidden_evidence_fields(manifest):
                raise ValueError("candidate evidence contains forbidden fields")
            _validate_candidate_manifest_structure(
                manifest,
                identity=_candidate_identity(args),
            )
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
            evidence_runtime = manifest.get("runtimeElapsedSec")
            if evidence_runtime is not None and not _finite_nonnegative(evidence_runtime):
                raise ValueError(
                    "journey evidence runtimeElapsedSec must be finite and non-negative"
                )
            args.candidate_evidence_runtime_sec = evidence_runtime
            args.replay_candidate_evidence = True
            recorded_padding = manifest.get("quietPadding", [])
            if not isinstance(recorded_padding, list):
                raise ValueError("journey evidence quietPadding must be a list")
            recorded_cleanup_evidence = manifest.get("cleanup")
            recorded_samples = manifest.get("resourceSamples")
            if not isinstance(recorded_samples, list):
                raise ValueError("journey evidence resourceSamples must be a list")
            sample_cursor = 0
            seen_sample_ids = set()
            resource_accounting_invalid = False

            def recorded_sample():
                nonlocal resource_accounting_invalid, sample_cursor
                if sample_cursor >= len(recorded_samples):
                    raise ValueError("resource evidence was over-consumed")
                sample = recorded_samples[sample_cursor]
                sample_cursor += 1
                if not isinstance(sample, Mapping):
                    raise ValueError("resource evidence sample must be an object")
                sample_id = sample.get("sampleId")
                if (
                    not isinstance(sample_id, str)
                    or not sample_id
                    or sample_id in seen_sample_ids
                ):
                    resource_accounting_invalid = True
                else:
                    seen_sample_ids.add(sample_id)
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

            cleanup_consumed = 0

            async def recorded_cleanup(_args, *, final_scope):
                nonlocal cleanup_consumed
                cleanup_consumed += 1
                if cursor != len(recorded):
                    raise ValueError("journey evidence was not fully consumed")
                if not isinstance(recorded_cleanup_evidence, Mapping):
                    return recorded_cleanup_evidence
                return dict(recorded_cleanup_evidence)

            journeys["cleanup"] = recorded_cleanup
            padding_consumed = 0

            async def recorded_monitor(_args, *, duration_sec):
                nonlocal padding_consumed
                if padding_consumed:
                    return []
                padding_consumed = len(recorded_padding)
                return [dict(item) if isinstance(item, Mapping) else item for item in recorded_padding]

            journeys["monitor"] = recorded_monitor

            def recorded_accounting():
                accounting_failures = []
                if cursor != len(recorded):
                    accounting_failures.append({"code": "UNEXPECTED_EVIDENCE"})
                if padding_consumed != len(recorded_padding):
                    accounting_failures.append({"code": "UNEXPECTED_EVIDENCE"})
                if cleanup_consumed != 1:
                    accounting_failures.append({"code": "UNEXPECTED_EVIDENCE"})
                if sample_cursor != len(recorded_samples):
                    accounting_failures.append({"code": "RESOURCE_SAMPLE_UNUSED"})
                if resource_accounting_invalid:
                    accounting_failures.append({"code": "RESOURCE_SAMPLE_UNUSED"})
                return accounting_failures

            journeys["accounting"] = recorded_accounting
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
    parser.add_argument("--produce-candidate-evidence", type=Path, default=None)
    parser.add_argument("--evidence-control-url", default=None)
    parser.add_argument(
        "--evidence-mint-secret-env", default="TBOT_DEVICE_MINT_SECRET"
    )
    parser.add_argument("--server-log", type=Path, default=None)
    parser.add_argument("--run-id", default=None)
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
    producing = bool(getattr(args, "produce_candidate_evidence", None))
    replaying = bool(getattr(args, "journey_evidence", None))
    if producing and replaying:
        parser.error(
            "candidate producer and --journey-evidence replay are mutually exclusive"
        )
    required = [
        "candidate_git_sha",
        "candidate_image_digest",
        "firmware_identity",
        "fixture_sha256",
        "baseline_report",
        "real_api_report",
        "transport_report",
        "correlated_transport_report",
        "log_reliability_report",
        "lesson_manifest",
    ]
    if producing:
        required.extend(("evidence_control_url", "server_log", "run_id"))
        if getattr(args, "inject_audio", None) or getattr(args, "inject_text", None):
            parser.error(
                "candidate producer audio and expected text must use protected stdin"
            )
        secret_name = getattr(args, "evidence_mint_secret_env", "")
        if not isinstance(secret_name, str) or not secret_name or not os.environ.get(
            secret_name
        ):
            parser.error("candidate producer mint-secret environment is unavailable")
    else:
        required.extend(("journey_evidence", "report"))
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
        detail = type(exc).__name__ if args.mode == "candidate" else str(exc)
        print(f"SOAK_FAIL {detail}", file=sys.stderr)
        return 1

    if args.produce_candidate_evidence:
        if report.get("status") == "PASS":
            print(f"Wrote candidate evidence: {args.produce_candidate_evidence}")
        return report.get("exit_code", 1)

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        if args.mode == "candidate":
            if not _publish_candidate_report(args.report, report):
                return 1
        else:
            args.report.write_text(json.dumps(report, indent=2, default=str))
        print(f"Wrote soak report: {args.report}")

    if report.get("dry_run"):
        print("DRY_RUN_OK schema validated, no server connection made")
    if "ac_results" in report:
        print(json.dumps(report["ac_results"], indent=2))
    return report.get("exit_code", 0 if report.get("all_ac_pass", False) else 1)


if __name__ == "__main__":
    raise SystemExit(main())
