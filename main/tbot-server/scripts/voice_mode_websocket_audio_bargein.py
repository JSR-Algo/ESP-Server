#!/usr/bin/env python3
import argparse
import asyncio
import json
import math
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import websockets

SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from core.utils.opus_encoder_utils import OpusEncoderUtils  # noqa: E402
from core.utils.util import audio_to_data_stream  # noqa: E402
from scripts.google_live_reliability import (  # noqa: E402
    GOOGLE_LIVE_LIMITS,
    SCHEMA_VERSION,
    build_candidate_identity,
    redact_mapping,
)
from scripts.voice_mode_websocket_soak import (  # noqa: E402
    _build_headers,
    _hello_message,
    _is_tts_state,
    _recv_until,
)

DEFAULT_TEXT = (
    "Hãy trả lời bằng tiếng Việt trong khoảng hai câu về kiểm thử ngắt ngang "
    "khi robot đang nói."
)
REPLACEMENT_RESPONSE_INCOMPLETE = "REPLACEMENT_RESPONSE_INCOMPLETE"
SAFE_JOURNEY_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")


def _detect_message(text):
    return {"type": "listen", "state": "detect", "text": text}


def _tone_pcm(sample_rate, duration_sec, rms):
    sample_count = int(sample_rate * duration_sec)
    peak = min(30000, max(0, int(rms * math.sqrt(2))))
    timeline = np.arange(sample_count, dtype=np.float64) / sample_rate
    samples = np.sin(2 * math.pi * 440 * timeline) * peak
    return samples.astype(np.int16).tobytes()


def _opus_packets(sample_rate, frame_duration_ms, duration_sec, rms):
    encoder = OpusEncoderUtils(sample_rate, 1, frame_duration_ms)
    packets = []
    encoder.encode_pcm_to_opus_stream(
        _tone_pcm(sample_rate, duration_sec, rms),
        end_of_stream=True,
        callback=packets.append,
    )
    encoder.close()
    return packets


def _opus_packets_from_audio_file(audio_file, sample_rate, frame_duration_ms):
    encoder = OpusEncoderUtils(sample_rate, 1, frame_duration_ms)
    packets = []
    try:
        audio_to_data_stream(
            audio_file,
            is_opus=True,
            callback=packets.append,
            sample_rate=sample_rate,
            opus_encoder=encoder,
        )
    finally:
        encoder.close()
    return packets


async def _collect_replacement_response(websocket, *, timeout_sec, clock=time.monotonic):
    deadline = clock() + timeout_sec
    started = False
    terminal_stopped = False
    binary_times = []
    while True:
        remaining = deadline - clock()
        if remaining <= 0:
            break
        try:
            message = await asyncio.wait_for(
                websocket.recv(),
                timeout=max(0.01, remaining),
            )
        except asyncio.TimeoutError:
            break
        now = clock()
        if isinstance(message, bytes):
            if started:
                binary_times.append(now)
            continue
        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            continue
        if _is_tts_state(payload, "start"):
            started = True
        elif started and _is_tts_state(payload, "stop"):
            terminal_stopped = True
            break

    gaps = [
        (right - left) * 1000
        for left, right in zip(binary_times, binary_times[1:], strict=False)
    ]
    return {
        "replacementResponseStarted": started,
        "replacementResponseStopped": terminal_stopped,
        "replacementBinaryChunks": len(binary_times),
        "maxServerOutputGapMs": round(max(gaps, default=0.0), 1),
    }


async def _observe_interrupt_stop(
    websocket,
    *,
    timeout_sec,
    clock,
    first_packet_sent,
):
    deadline = clock() + timeout_sec
    binary_count = 0
    while True:
        remaining = deadline - clock()
        if remaining <= 0:
            return {"stop": None, "binaryCount": binary_count}
        try:
            message = await asyncio.wait_for(
                websocket.recv(),
                timeout=max(0.01, remaining),
            )
        except asyncio.TimeoutError:
            return {"stop": None, "binaryCount": binary_count}
        observed_at = clock()
        if isinstance(message, bytes):
            binary_count += 1
            continue
        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            continue
        if not _is_tts_state(payload, "stop"):
            continue
        if not first_packet_sent.is_set():
            failure_code = (
                "INTERRUPT_STOP_BEFORE_FIRST_PACKET"
                if payload.get("reason") == "interrupt"
                else "OLD_RESPONSE_COMPLETED_BEFORE_INTERRUPT"
            )
            return {
                "stop": None,
                "binaryCount": binary_count,
                "failureCode": failure_code,
            }
        if payload.get("reason") != "interrupt":
            return {
                "stop": None,
                "binaryCount": binary_count,
                "failureCode": "OLD_RESPONSE_COMPLETED_WITHOUT_INTERRUPT",
            }
        return {
            "stop": payload,
            "binaryCount": binary_count,
            "observedAt": observed_at,
        }


def _queued_receive_count(websocket):
    """Inspect buffered input without a scheduling or timeout race.

    ``queued_message_count`` is the stable harness seam. Production pins
    websockets 14.2, whose public ``recv`` API doesn't expose buffer state, so
    the fallback is isolated here and fails closed if that pinned shape moves.
    """
    explicit_count = getattr(websocket, "queued_message_count", None)
    if explicit_count is not None:
        return int(explicit_count() if callable(explicit_count) else explicit_count)
    recv_messages = getattr(websocket, "recv_messages", None)
    frames = getattr(recv_messages, "frames", None)
    if frames is not None:
        return len(frames)
    legacy_messages = getattr(websocket, "messages", None)
    if isinstance(legacy_messages, list):
        return 0
    if legacy_messages is not None:
        return len(legacy_messages)
    raise RuntimeError("websocket queued receive inspection unavailable")


async def _drain_preflight_terminal(websocket, *, timeout_sec):
    deadline = time.monotonic() + timeout_sec
    while _queued_receive_count(websocket) > 0:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return "PREFLIGHT_RECEIVE_TIMEOUT"
        try:
            message = await asyncio.wait_for(
                websocket.recv(),
                timeout=max(0.01, remaining),
            )
        except asyncio.TimeoutError:
            return "PREFLIGHT_RECEIVE_TIMEOUT"
        if isinstance(message, bytes):
            continue
        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            continue
        if not _is_tts_state(payload, "stop"):
            continue
        return (
            "INTERRUPT_STOP_BEFORE_FIRST_PACKET"
            if payload.get("reason") == "interrupt"
            else "OLD_RESPONSE_COMPLETED_BEFORE_INTERRUPT"
        )
    return None


def _evidence_context(args):
    journey_id = str(getattr(args, "journey_id", "") or "").strip()
    if SAFE_JOURNEY_RE.fullmatch(journey_id) is None:
        raise ValueError("journey_id must be 1-64 safe identifier characters")
    try:
        config = json.loads(getattr(args, "config_json", ""))
    except json.JSONDecodeError as exc:
        raise ValueError("config_json must be valid JSON") from exc
    if not isinstance(config, dict):
        raise ValueError("config_json must contain an object")
    identity = build_candidate_identity(
        getattr(args, "candidate_git_sha", ""),
        getattr(args, "candidate_image_digest", ""),
        getattr(args, "firmware_identity", ""),
        config,
        getattr(args, "fixture_sha256", ""),
    )
    return journey_id, identity


async def run_smoke(
    args,
    *,
    clock=time.monotonic,
    wall_clock=datetime.now,
):
    journey_id, candidate_identity = _evidence_context(args)
    window_start = wall_clock().replace(tzinfo=None).isoformat(timespec="seconds")
    headers = _build_headers(args)
    if getattr(args, "audio_file", ""):
        packets = _opus_packets_from_audio_file(
            args.audio_file,
            args.sample_rate,
            args.frame_duration_ms,
        )
    else:
        packets = _opus_packets(
            args.sample_rate,
            args.frame_duration_ms,
            args.audio_duration_sec,
            args.rms,
        )
    if not packets:
        raise RuntimeError("no opus packets generated")

    summary = {
        "schemaVersion": SCHEMA_VERSION,
        "name": "websocket_audio_bargein_transport",
        "status": "FAIL",
        "opus_packets": len(packets),
        "tts_starts": 0,
        "tts_stops": 0,
        "binary_chunks": 0,
        "oldResponseStopped": False,
        "interruptStopMarkerObserved": False,
        "replacementResponseStarted": False,
        "replacementResponseStopped": False,
        "replacementBinaryChunks": 0,
        "maxServerOutputGapMs": 0.0,
        "bargeinStopMs": None,
        "correlationSource": "server_log",
        "correlationStatus": "PENDING_BOUNDED_SERVER_LOG_VERIFICATION",
        "aggregateReleaseEligible": False,
        "firstInterruptPacketSentAtMonotonicMs": None,
        "journeyId": journey_id,
        "candidateIdentity": candidate_identity,
        "logWindow": {
            "windowId": journey_id,
            "start": window_start,
            "end": None,
        },
    }

    try:
        async with websockets.connect(
            args.websocket_url,
            additional_headers=headers,
            open_timeout=args.open_timeout_sec,
            max_size=None,
        ) as websocket:
            hello = _hello_message()
            hello["audio_params"]["sample_rate"] = args.sample_rate
            hello["audio_params"]["frame_duration"] = args.frame_duration_ms
            hello["evidence_journey_id"] = journey_id
            await websocket.send(json.dumps(hello))
            ack, binary_count, _messages = await _recv_until(
                websocket,
                lambda payload: payload.get("type") == "hello",
                args.event_timeout_sec,
            )
            summary["binary_chunks"] += binary_count
            if ack is None:
                raise RuntimeError("hello ack timeout")

            await websocket.send(json.dumps(_detect_message(args.text)))
            start, binary_count, _messages = await _recv_until(
                websocket,
                lambda payload: _is_tts_state(payload, "start"),
                args.event_timeout_sec,
            )
            summary["binary_chunks"] += binary_count
            if start is None:
                raise RuntimeError("tts start timeout")
            summary["tts_starts"] += 1

            await asyncio.sleep(args.interrupt_delay_sec)
            preflight_failure = await _drain_preflight_terminal(
                websocket,
                timeout_sec=args.interrupt_timeout_sec,
            )
            if preflight_failure is not None:
                summary["failureCode"] = preflight_failure
                return summary
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
                for packet in packets:
                    await websocket.send(packet)
                    if not first_packet_sent.is_set():
                        first_packet_sent_at = clock()
                        summary["firstInterruptPacketSentAtMonotonicMs"] = round(
                            first_packet_sent_at * 1000,
                            3,
                        )
                        first_packet_sent.set()
                    await asyncio.sleep(args.frame_duration_ms / 1000)
                stop_result = await stop_task
            finally:
                if not stop_task.done():
                    stop_task.cancel()
                    await asyncio.gather(stop_task, return_exceptions=True)

            summary["binary_chunks"] += stop_result["binaryCount"]
            if stop_result.get("failureCode"):
                summary["failureCode"] = stop_result["failureCode"]
                return summary
            stop = stop_result["stop"]
            if stop is None:
                raise RuntimeError("audio interrupt tts stop timeout")
            summary["tts_stops"] += 1
            summary["interruptStopMarkerObserved"] = True
            summary["bargeinStopMs"] = round(
                (stop_result["observedAt"] - first_packet_sent_at) * 1000,
                1,
            )

            replacement = await _collect_replacement_response(
                websocket,
                timeout_sec=args.event_timeout_sec,
                clock=clock,
            )
            summary.update(replacement)
            summary["binary_chunks"] += replacement["replacementBinaryChunks"]
            if replacement["replacementResponseStarted"]:
                summary["tts_starts"] += 1
            if replacement["replacementResponseStopped"]:
                summary["tts_stops"] += 1

            if (
                not replacement["replacementResponseStarted"]
                or replacement["replacementBinaryChunks"] < 1
                or not replacement["replacementResponseStopped"]
            ):
                summary["failureCode"] = REPLACEMENT_RESPONSE_INCOMPLETE
                return summary
            if summary["bargeinStopMs"] > GOOGLE_LIVE_LIMITS["physicalBargeinP95Ms"]:
                summary["failureCode"] = "BARGEIN_STOP_LATENCY_EXCEEDED"
                return summary
            if (
                replacement["maxServerOutputGapMs"]
                > GOOGLE_LIVE_LIMITS["serverOutputGapMaxMs"]
            ):
                summary["failureCode"] = "SERVER_OUTPUT_GAP_EXCEEDED"
                return summary

            summary["status"] = "SKIPPED"
            summary["pendingCode"] = "PENDING_BOUNDED_SERVER_LOG_VERIFICATION"
    finally:
        summary["logWindow"]["end"] = (
            wall_clock().replace(tzinfo=None).isoformat(timespec="seconds")
        )
    return summary


def main():
    parser = argparse.ArgumentParser(
        description="Websocket Google Live voice-mode synthetic Opus audio barge-in smoke."
    )
    parser.add_argument("--websocket-url", default="ws://127.0.0.1:8000/tbot/v1/")
    parser.add_argument("--ota-url", default="")
    parser.add_argument("--authorization-token", default="")
    parser.add_argument("--device-id", required=True)
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--text", default=DEFAULT_TEXT)
    parser.add_argument("--sample-rate", type=int, default=24000)
    parser.add_argument("--frame-duration-ms", type=int, default=60)
    parser.add_argument("--audio-duration-sec", type=float, default=0.6)
    parser.add_argument("--audio-file", default="")
    parser.add_argument("--rms", type=int, default=9000)
    parser.add_argument("--interrupt-delay-sec", type=float, default=0.3)
    parser.add_argument("--open-timeout-sec", type=float, default=5)
    parser.add_argument("--event-timeout-sec", type=float, default=20)
    parser.add_argument("--interrupt-timeout-sec", type=float, default=5)
    parser.add_argument("--journey-id", required=True)
    parser.add_argument("--candidate-git-sha", required=True)
    parser.add_argument("--candidate-image-digest", required=True)
    parser.add_argument("--firmware-identity", required=True)
    parser.add_argument("--config-json", required=True)
    parser.add_argument("--fixture-sha256", required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    try:
        summary = asyncio.run(run_smoke(args))
    except Exception as exc:
        print(f"AUDIO_BARGE_IN_FAIL {exc}", file=sys.stderr)
        return 1
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(redact_mapping(summary), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    if summary["status"] == "SKIPPED":
        print(
            "AUDIO_BARGE_IN_PENDING "
            f"pending_code={summary.get('pendingCode', 'UNKNOWN')}",
            file=sys.stderr,
        )
        return 1
    if summary["status"] != "PASS":
        print(
            "AUDIO_BARGE_IN_FAIL "
            f"failure_code={summary.get('failureCode', 'UNKNOWN')}",
            file=sys.stderr,
        )
        return 1
    print("AUDIO_BARGE_IN_FAIL unexpected standalone PASS", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
